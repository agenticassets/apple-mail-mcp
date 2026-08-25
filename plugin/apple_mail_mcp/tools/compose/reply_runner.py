"""Native-reply abort dispatch, typing-timeout, and stray-artifact-delete helpers.

Leaf module split out of ``reply.py`` (AGENTIC-1214) so the retry loop there
does not have to carry these self-contained pieces and push the module over
the 600 LOC budget. The BODY-verification failure mapping (``body_missing`` /
``body_after_quote`` / ``not_found`` / timeouts) still lives in
``verification.py`` and is dispatched from ``reply.py``; only the pre-save ABORT
sentinels (``REPLY_ACCESSIBILITY_UNAVAILABLE``, ``SENDER_OVERRIDE_FAILED``,
``GUARD_ABORT*``, ``TYPING_INTERRUPTED``) are mapped here, since ``reply.py``
calls the same dispatcher for both the first compose attempt and a retype
attempt.
"""

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.core import escape_applescript, normalize_message_ids
from apple_mail_mcp.tools import compose
from apple_mail_mcp.tools.compose.constants import (
    QUOTE_PROOF_UNAVAILABLE,
    REPLY_ACCESSIBILITY_UNAVAILABLE,
    TYPING_CHUNK_SIZE,
    TYPING_INTER_CHUNK_DELAY,
    TYPING_PER_CHUNK_OVERHEAD_SECONDS,
    TYPING_SETTLE_DELAY,
    typing_settle_attempts,
)
from apple_mail_mcp.tools.compose.reply_draft_resolver_scripts import (
    _native_reply_draft_resolver_handlers_applescript,
)
from apple_mail_mcp.tools.compose.reply_identity import NativeReplyDraftIdentity
from apple_mail_mcp.tools.compose.reply_window_identity_scripts import NATIVE_REPLY_SENDER_OVERRIDE_ABORT
from apple_mail_mcp.tools.compose.saved_draft_checks import _verify_saved_reply_draft
from apple_mail_mcp.tools.compose.verification import _extract_output_field

# Fixed overhead the native compose script spends outside chunk-typing delays:
# claiming the front, the up-to-4 focus-guard attempts, the initial
# `reply ... with opening window` render, the compose-window adoption scan, the
# accessibility walk that resolves the body editor, and the post-type
# save/close settle plus Drafts identity read. Slack is extra cushion for host
# variance. Both exist so a timeout scaled only from chunk-typing time cannot
# come in under budget and let AppleScriptTimeout fire mid-typing, stranding a
# partially typed compose window that a retry could then type into on top of.
#
# A DELIBERATELY CONSERVATIVE CEILING, NOT A CURRENT MEASUREMENT. 34.2s was the
# intercept of a 2026-08-24 fit over four successful live replies (R2 0.98) and
# 35 was set just above it. That fit is now contradicted by this project's own
# later evidence: the chunk-size sweep in ``constants.py`` (2026-08-25) has
# COMPLETE 2,400-character runs finishing in 21.3-28.9s, several below 34.2s,
# which a genuine fixed overhead makes impossible. Refitting that sweep gives
# ~0.48s per chunk on a ~19s intercept; the old 34.2s was almost certainly
# fitted through the undrained-typing regime the settle poll removed, charging
# waiting-for-WebKit time to fixed overhead.
#
# It is NOT lowered to ~19s, deliberately. Over-projection is the safe
# direction, and a value fitted tight to one idle machine is what fails on a
# loaded one. But it must not become a FLOOR either: the guarding test asserts
# conservatism (above the refitted intercept, within a sane multiple of it),
# not fidelity to the superseded 34.2s.
_NATIVE_TYPING_FIXED_OVERHEAD_SECONDS = 35
_NATIVE_TYPING_SLACK_SECONDS = 30
# Bodies whose projected typing time would exceed this are refused with a
# structured error instead of silently under- or wildly over-provisioning the
# AppleScript timeout. The cap applies whether or not the caller passed an
# explicit ``timeout``, which is what keeps the explicit-timeout floor below
# bounded: nothing can ask for a projection larger than this.
#
# WHAT IT ACTUALLY BOUNDS (recomputed 2026-08-25 from the constants in force).
# The two TYPING phases only: chunk posting at TYPING_INTER_CHUNK_DELAY +
# TYPING_PER_CHUNK_OVERHEAD_SECONDS = 1.0s per 300-character chunk, plus the
# editor-drain poll at typing_settle_attempts(n) * TYPING_SETTLE_DELAY. Fixed
# overhead and slack are excluded, so a body at the cap is granted
# 480 + 35 + 30 = 545s. The drain term saturates at 100s
# (TYPING_SETTLE_MAX_ATTEMPTS * TYPING_SETTLE_DELAY) from 6,259 characters up,
# so above that the cap is 100s of poll bound plus 380s of chunk posting: it
# admits exactly 114,000 characters (380 chunks) and refuses 114,001.
#
# The previous wording -- "roughly 38,400 characters, which really does type in
# about six minutes" -- was wrong twice. 38,400 predates the drain term and
# understates the admitted length by 3x, and 480s is not six minutes of typing
# when 100s of it is a poll the script routinely exits early from and the chunk
# term over-projects by design (at the sweep's ~0.48s per chunk, 380 chunks
# post in ~180s). So this is a policy ceiling on how long the native path may
# hold the foreground for one reply, denominated in PROJECTED seconds -- not a
# measured duration and not a feasibility limit.
_NATIVE_TYPING_MAX_PROJECTED_SECONDS = 480


def _native_reply_projected_typing_seconds(body_length: int) -> float:
    """Seconds the native typing pass is projected to spend on ``body_length``.

    Two terms, because typing is two phases with different costs: posting the
    keystroke events (the chunk term) and waiting for the WebKit editor to
    process them (the settle term, LARGER on a long body -- a 5,000-character
    body posts in ~17s and drains for up to 81s). Projecting only the chunk
    term is what let ``AppleScriptTimeout`` sit below the real duration on
    exactly the bodies most likely to need the drain. Uses the same helper the
    AppleScript budget is computed from.
    """
    chunk_count = -(-body_length // TYPING_CHUNK_SIZE) if body_length else 0
    projected_seconds = chunk_count * (TYPING_INTER_CHUNK_DELAY + TYPING_PER_CHUNK_OVERHEAD_SECONDS)
    # ASYMMETRY, stated because it is not self-evidently safe: this term counts
    # only the poll's `delay`, while the chunk term above carries
    # TYPING_PER_CHUNK_OVERHEAD_SECONDS precisely because a delay alone does not
    # capture per-attempt work. Each settle attempt also does an AXValue read
    # that returns the WHOLE editor text, so its cost grows with body length. At
    # ~5 ms per read on the 2,400-character bodies these constants were measured
    # on, the full 400-attempt budget is ~2s against 30s of slack -- immaterial,
    # which is why no per-attempt term is added rather than inventing a constant
    # no measurement supports. At the cap's 114,000 characters it is 400 reads
    # of a >114,000-character editor, and anything near 110 ms per read would
    # consume the whole fixed-overhead-plus-slack cushion.
    #
    # So state the limit plainly: every measurement behind these constants was
    # taken at 2,400-5,000 characters. Above ~6,259 characters, where
    # typing_settle_attempts saturates, NOTHING has been measured. That region
    # is projected rather than evidenced, and rests on the per-read cost
    # staying small.
    projected_seconds += typing_settle_attempts(body_length) * TYPING_SETTLE_DELAY
    return projected_seconds


def _native_reply_effective_timeout(reply_body: str, timeout: int | None) -> tuple[int | None, str | None]:
    """Return ``(effective_timeout, error_json)`` for the native typed-reply script.

    The effective timeout scales with the projected typing duration (chunk
    posting plus editor drain) plus fixed overhead and slack, floored at the
    standard 120s. Bodies whose projected typing time exceeds the documented
    cap are refused with a structured error rather than handed a timeout that
    ``AppleScriptTimeout`` could fire mid-typing.

    An explicit ``timeout`` is FLOORED at that same projected value, not used
    as-is: it can raise the budget, never lower it. That is correctness, not
    convenience. The AppleScript computes its drain budget from ``bodyLength``
    unconditionally and never sees the granted timeout, so an explicit value
    below the projection makes the two callers of ``typing_settle_attempts``
    disagree by construction -- ``AppleScriptTimeout`` fires mid-drain and
    strands a compose window with the body typed and unsaved, which a retry
    then types into a SECOND window. Truncating the drain instead is the worse
    failure: a small ``timeout`` expresses "do not hang forever", and the floor
    still terminates because the refusal cap (which applies to the explicit
    path too) bounds the projection.
    """
    body_length = len(reply_body)
    projected_seconds = _native_reply_projected_typing_seconds(body_length)
    if projected_seconds > _NATIVE_TYPING_MAX_PROJECTED_SECONDS:
        return None, serialize_tool_error(
            ToolError(
                code="REPLY_BODY_TYPING_BUDGET_EXCEEDED",
                message=(
                    f"reply_body is {body_length} characters, which projects to "
                    f"~{int(projected_seconds)}s of focus-guarded chunked typing and editor-drain "
                    f"polling, exceeding the {_NATIVE_TYPING_MAX_PROJECTED_SECONDS}s documented cap "
                    "for the native reply path. No draft was created."
                ),
                remediation={
                    # Deliberately does NOT offer "pass a bigger timeout" any
                    # more. The cap now applies to the explicit-timeout path
                    # too, so that advice would be false; and before the
                    # explicit path was floored it routed callers into the one
                    # branch where AppleScriptTimeout could fire mid-drain and
                    # strand a typed compose window for a retry to duplicate.
                    "preferred": (
                        "Shorten reply_body. The cap is on the native path's projected typing time, "
                        "so an explicit timeout does not lift it -- timeout can only raise the budget "
                        "above the projection, never below it."
                    ),
                    "alternative": (
                        "If the full text must go out, send the long body as an attachment, or split "
                        "it across replies. compose_email and create_rich_email_draft do not type "
                        "through the keyboard and carry no such cap, but they produce a standalone "
                        "message rather than a native in-thread reply."
                    ),
                    "projected_typing_seconds": int(projected_seconds),
                    "cap_seconds": _NATIVE_TYPING_MAX_PROJECTED_SECONDS,
                },
            )
        )
    projected_timeout = max(
        120,
        int(projected_seconds + _NATIVE_TYPING_FIXED_OVERHEAD_SECONDS + _NATIVE_TYPING_SLACK_SECONDS),
    )
    if timeout is not None:
        return max(timeout, projected_timeout), None
    return projected_timeout, None


def _delete_reply_artifact(
    account: str,
    draft_id: str,
    *,
    identity: NativeReplyDraftIdentity,
    timeout: int | None,
) -> bool:
    """Best-effort delete of a stray reply-draft artifact by exact Drafts id.

    Returns True only when Mail confirmed the delete (``DELETED|id``). Returns
    False when the id was non-numeric, not found, the AppleScript errored, or
    the call timed out; the caller treats False as "unconfirmed" and surfaces
    a stale-artifact warning instead of assuming the draft is gone. Exchange
    Drafts ids drift across sync (AGENTIC-1214 observation), so a caller that
    assumed success here could otherwise leave a truncated duplicate behind.
    """
    normalized = normalize_message_ids([draft_id])
    if not normalized or normalized[0] != identity.draft_id:
        return False
    numeric_id = normalized[0]
    safe_account = escape_applescript(account)
    safe_draft_rfc_message_id = escape_applescript(identity.draft_rfc_message_id)
    safe_source_rfc_message_id = escape_applescript(identity.source_rfc_message_id)
    resolver_handlers = _native_reply_draft_resolver_handlers_applescript()
    script = f'''
{resolver_handlers}
tell application "Mail"
    try
        set targetAccount to account "{safe_account}"
        set draftsMailbox to mailbox "Drafts" of targetAccount
        set targetDrafts to every message of draftsMailbox whose id is {numeric_id}
        if (count of targetDrafts) > 0 then
            set targetDraft to item 1 of targetDrafts
            if (message id of targetDraft as string) is not "{safe_draft_rfc_message_id}" then return "NOT_IDENTITY|{numeric_id}"
            set inReplyToResult to my draftInReplyTo(targetDraft)
            if item 1 of inReplyToResult is false then return "NOT_IDENTITY|{numeric_id}"
            if (my headerHasExactRfcToken(item 2 of inReplyToResult, "{safe_source_rfc_message_id}")) is false then return "NOT_IDENTITY|{numeric_id}"
            delete targetDraft
            return "DELETED|{numeric_id}"
        end if
        return "NOT_FOUND|{numeric_id}"
    on error
        return "NOT_FOUND|{numeric_id}"
    end try
end tell
'''
    delete_timeout = 30 if timeout is None else max(15, min(timeout, 60))
    try:
        result = compose.run_applescript(script, timeout=delete_timeout)
    except Exception:  # noqa: BLE001 - best-effort cleanup; caller surfaces a stale-artifact warning
        return False
    return result.strip().startswith("DELETED|")


def _probe_abort_artifact(
    result: str,
    *,
    account: str,
    reply_body: str,
    timeout: int | None,
) -> tuple[str, str | None, str, str]:
    """Return ``(artifact_status, suspected_draft_id, guard_subject, derived_subject)``.

    Runs the same signature-agnostic saved-draft probe used by every native-reply
    abort path (mid-typing interruption or pre-typing guard failure), so all
    three abort error responses below report from one consistent verification
    pass instead of three separately duplicated ones.
    """
    guard_reply_subject = _extract_output_field(result, "Subject") or ""
    derived_reply_subject = _extract_output_field(result, "DerivedSubject") or ""
    probe = _verify_saved_reply_draft(
        account,
        guard_reply_subject or derived_reply_subject,
        reply_body,
        draft_id=None,
        quoted_needle="wrote:",
        signature_requested=None,
        timeout=timeout,
    )
    suspected = probe.matched_artifact_id or probe.body_missing_artifact_id or probe.error_artifact_id
    return probe.status, suspected, guard_reply_subject, derived_reply_subject


#: Inspect-only by construction. ``_probe_abort_artifact`` searches Drafts by
#: reply subject with ``draft_id=None``, so the row it finds can never be
#: identity-verified -- and the subject it searches on is "Re: <thread>", which
#: is exactly what a reply draft the user wrote earlier in this same thread also
#: carries. The probe cannot tell those apart, and every abort path that reports
#: it has already said nothing was saved. This used to end "...or delete that
#: exact Drafts artifact with ... manage_drafts(action='delete', draft_id=...)",
#: which pointed an agent at a coin flip over user-authored text with no undo.
_EXACT_ARTIFACT_CLEANUP = (
    "If suspected_draft_id is present, inspect that Drafts artifact with "
    "verify_draft(draft_id=...). Do not delete it automatically: it was found by "
    "subject, not by verified reply identity, so it may be a draft you did not create."
)


def _abort_artifact_remediation(
    artifact_status: str,
    suspected: str | None,
    result: str,
    *,
    cleanup: str = _EXACT_ARTIFACT_CLEANUP,
) -> dict[str, str | None]:
    """Return the artifact-report tail every native-reply abort remediation ends with.

    Spread last (``{"preferred": ..., **_abort_artifact_remediation(...)}``) so the
    caller-facing key order stays preferred → alternative → artifact report. Each
    abort explains a different failure in its own words but reports the possible
    stray Drafts row identically, so that half is written once.
    """
    return {
        "draft_artifact_status": artifact_status,
        "suspected_draft_id": suspected,
        "cleanup": cleanup,
        "detail": result,
    }


def _native_reply_abort_response(
    result: str,
    *,
    account: str,
    reply_body: str,
    timeout: int | None,
) -> str | None:
    """Return a structured error for a native-reply abort sentinel, or None.

    Handles ``REPLY_ACCESSIBILITY_UNAVAILABLE`` (the Accessibility bridge
    reports no windows for Mail, checked before the reply window is opened),
    ``TYPING_INTERRUPTED`` (focus lost mid-chunk-typing; the partial
    compose window was already discarded by the AppleScript),
    ``SENDER_OVERRIDE_FAILED`` (Mail refused an explicitly requested
    ``from_address`` before anything was saved), and ``GUARD_ABORT`` /
    ``GUARD_ABORT_SUBJECT`` (pre-typing focus failures).
    Returns None when ``result`` is not one of these sentinels so the caller
    proceeds to the normal success/verification handling. Callable for both
    the first compose attempt and a retype attempt so a second-run abort is
    routed through the same branches instead of looping again.
    """
    if result.startswith(REPLY_ACCESSIBILITY_UNAVAILABLE):
        # No artifact probe: this sentinel is emitted *before* the `reply`
        # command, so no compose window was ever opened and there is nothing
        # to look for in Drafts.
        return serialize_tool_error(
            ToolError(
                code=REPLY_ACCESSIBILITY_UNAVAILABLE,
                message=(
                    "System Events can see Mail but cannot see any of Mail's windows, so the reply "
                    "body could never have been typed. The reply was abandoned before Mail's reply "
                    "window was opened: nothing was saved, no email was sent, and no compose window "
                    "was left behind."
                ),
                remediation={
                    # Ordered by likelihood, NOT by certainty. Three conditions
                    # produce a byte-identical reading here -- a different
                    # Space, a sleeping or locked display, and a lapsed
                    # Accessibility grant all report zero windows for every
                    # application with no error raised -- and Mail's own
                    # `count of windows` is unaffected by all three, so the
                    # Detail line the text points at cannot separate them
                    # (measurement: reply_window_scripts.py). The Space case
                    # leads because it is the common one on a working Mac; it
                    # must not be stated as a finding, or a locked-screen
                    # caller is handed a confident wrong diagnosis.
                    "preferred": (
                        "Read the Detail line first: it reports Mail's own window count beside the "
                        "Accessibility one. If Mail has windows and Accessibility sees none, three "
                        "conditions produce that exact reading and nothing in this call can tell them "
                        "apart: Mail parked on another Space, a sleeping or locked display, and an "
                        "Accessibility grant that is no longer in effect. Try the Space first, because "
                        "it is the most likely on a working Mac -- any full-screen app puts you on its "
                        "own Space, and Accessibility enumerates no windows for an application parked "
                        "on another one. Leave full-screen (or move Mail onto the current Space) and "
                        "retry; Mail does not need restarting. If that changes nothing, the display "
                        "and the grant are the other two readings, below."
                    ),
                    "if_mail_reports_no_windows_either": (
                        "Then Mail really has no window to reply from: open a Mail viewer window "
                        "(File > New Viewer Window) and retry."
                    ),
                    "if_display_may_be_asleep": (
                        "A sleeping or locked screen produces an identical reading -- the accessibility "
                        "bridge reports zero windows for every application while raising no error, and "
                        "Mail's own window count is unchanged -- so this cannot be ruled out from the "
                        "Detail line. Waking and unlocking the display fixes it with no other change."
                    ),
                    "alternative": (
                        "If neither the Space nor the display explains it, the Accessibility grant is "
                        "not in effect for the application running this tool: add or re-arm it in "
                        "System Settings > Privacy & Security > Accessibility (an existing entry can "
                        "stop taking effect after an app update; toggling it off and on re-arms it), "
                        "then quit and reopen that application. All three are environment failures, "
                        "not formatting ones -- do not switch off native_format. compose_email and "
                        "create_rich_email_draft need no Accessibility and keep working meanwhile."
                    ),
                    "script_output": result,
                },
            )
        )
    if result.startswith("TYPING_INTERRUPTED"):
        artifact_status, suspected, _guard_subject, _derived_subject = _probe_abort_artifact(
            result, account=account, reply_body=reply_body, timeout=timeout
        )
        return serialize_tool_error(
            ToolError(
                code="REPLY_BODY_TYPING_INTERRUPTED",
                message=(
                    "Native reply lost window focus partway through typing the body, so typing was "
                    "aborted and the partial compose window was discarded (closed without saving). "
                    "No draft with a partial body was left and no email was sent."
                ),
                remediation={
                    "preferred": (
                        "Retry with native_format=True (the default) and Mail visible and not being "
                        "clicked; native replies type into the reply window and need it to hold focus."
                    ),
                    **_abort_artifact_remediation(
                        artifact_status,
                        suspected,
                        result,
                        cleanup=(
                            "If suspected_draft_id is present, the discarded compose may have left a stray "
                            "artifact; inspect it with verify_draft(draft_id=...). Do not delete it "
                            "automatically: it was found by subject, not by verified reply identity, so it "
                            "may be a draft you did not create."
                        ),
                    ),
                },
            )
        )
    if result.startswith(NATIVE_REPLY_SENDER_OVERRIDE_ABORT):
        artifact_status, suspected, _guard_subject, _derived_subject = _probe_abort_artifact(
            result, account=account, reply_body=reply_body, timeout=timeout
        )
        return serialize_tool_error(
            ToolError(
                code="REPLY_SENDER_OVERRIDE_FAILED",
                message=(
                    "Mail refused the requested from_address on the reply window, so the reply was "
                    "abandoned before being saved rather than drafted from a different identity. The "
                    "open reply window was discarded, no draft was saved, and no email was sent."
                ),
                remediation={
                    "preferred": (
                        "Confirm the requested address is enabled for sending on this account (Mail > "
                        "Settings > Accounts), then retry. Omitting from_address lets Mail use the "
                        "account's own default send-from identity."
                    ),
                    "alternative": (
                        "On Exchange or a delegated mailbox, send-as for the requested alias can be "
                        "blocked by server policy; that cannot be worked around from this tool. Do not "
                        "retry without from_address unless sending from the default identity is "
                        "acceptable to the user."
                    ),
                    **_abort_artifact_remediation(artifact_status, suspected, result),
                },
            )
        )
    if not result.startswith("GUARD_ABORT"):
        return None

    artifact_status, suspected_artifact_id, guard_reply_subject, derived_reply_subject = _probe_abort_artifact(
        result, account=account, reply_body=reply_body, timeout=timeout
    )
    if result.startswith("GUARD_ABORT_FRONTMOST"):
        return serialize_tool_error(
            ToolError(
                code="REPLY_MAIL_NOT_FRONTMOST",
                message=(
                    "Mail could not be brought to the front, so the reply body was never typed. "
                    "System Events sends keystrokes to whatever application is frontmost, so typing "
                    "into a background Mail would have entered the text somewhere else entirely. "
                    "Nothing was saved and no email was sent."
                ),
                remediation={
                    "preferred": (
                        "Leave Mail frontmost for the few seconds the reply takes, then retry. The "
                        "tool activates Mail itself and waits for it, so this means another "
                        "application kept taking the front back -- see detail for which one."
                    ),
                    "alternative": (
                        "A locked screen, an active screen saver, or a full-screen Space that "
                        "excludes Mail all prevent activation. This is a foreground-attention "
                        "failure, not a permission or formatting one: do not switch off "
                        "native_format, and do not grant more permissions to work around it."
                    ),
                    **_abort_artifact_remediation(artifact_status, suspected_artifact_id, result),
                },
            )
        )
    if result.startswith("GUARD_ABORT_WINDOW"):
        return serialize_tool_error(
            ToolError(
                code="REPLY_WINDOW_NOT_IDENTIFIED",
                message=(
                    "Native reply opened a compose window, but it could not be told apart from the "
                    "windows already open, so the body was not typed. Nothing was saved and no email "
                    "was sent."
                ),
                remediation={
                    "preferred": (
                        "Close other Mail compose windows for this thread, then retry. The window is "
                        "adopted by being the one new window whose title matches the reply subject; "
                        "another compose open on the same subject makes that ambiguous."
                    ),
                    "alternative": (
                        "Do not switch off native formatting, and do not simply retry with Mail "
                        "visible: visibility is not the blocker here, and each retry opens another "
                        "compose window."
                    ),
                    **_abort_artifact_remediation(artifact_status, suspected_artifact_id, result),
                },
            )
        )
    if result.startswith("GUARD_ABORT_SUBJECT"):
        return serialize_tool_error(
            ToolError(
                code="REPLY_SUBJECT_GUARD_MISMATCH",
                message=(
                    "Native reply opened a compose window, but the window title did not match the "
                    "expected reply subject after Mail subject normalization, so the body was not "
                    "typed and no email was sent."
                ),
                remediation={
                    "preferred": (
                        "Retry once with Mail visible. If this persists, report the Subject / "
                        "DerivedSubject / mailFront values from detail; Mail may have normalized the "
                        "subject differently than expected."
                    ),
                    "alternative": (
                        "Do not switch off native formatting. Inspect or delete any empty compose "
                        "window left open, then retry native_format=True."
                    ),
                    "expected_subject": guard_reply_subject or derived_reply_subject,
                    "derived_subject": derived_reply_subject or None,
                    **_abort_artifact_remediation(artifact_status, suspected_artifact_id, result),
                },
            )
        )
    return serialize_tool_error(
        ToolError(
            code="REPLY_WINDOW_FOCUS_FAILED",
            message=(
                "Native reply could not bring the reply window into focus to type the body, so the "
                "intended reply body was not safely saved and no email was sent."
            ),
            remediation={
                "preferred": (
                    "Retry with Mail visible and not being clicked; native replies type into the "
                    "reply window and need it to hold focus for a moment."
                ),
                "alternative": (
                    "Do not switch off native formatting. Retry with native_format=True (the "
                    "default) once Mail can take focus. If focus still cannot be acquired, stop and "
                    "report the blocker."
                ),
                **_abort_artifact_remediation(artifact_status, suspected_artifact_id, result),
            },
        )
    )


def _unrecognized_reply_output_response(result: str, *, output_format: str) -> str:
    """Shape a non-success compose result for the caller's ``output_format``.

    ``reply_to_email(output_format="json")`` documents a JSON contract, but the
    compose script's non-success exits are plain prose: the not-found message,
    the ``Error: ...`` tail from its ``on error`` handler, and
    ``QUOTE_PROOF_UNAVAILABLE``. Returned verbatim, a genuine failure reaches a
    JSON caller as a ``json.loads`` parse error, so an agent branching on the
    parsed ``code`` never sees the failure it is meant to handle. The abort
    sentinels are already enveloped upstream by
    ``_native_reply_abort_response``; this covers the rest, including any
    sentinel added later. Text callers get the exact string they always got.
    """
    if output_format != "json":
        return result
    if result.startswith(QUOTE_PROOF_UNAVAILABLE):
        return serialize_tool_error(
            ToolError(
                code=QUOTE_PROOF_UNAVAILABLE,
                message=(
                    "The source message has no readable content to anchor the quote proof, so the "
                    "native reply was abandoned before anything was typed. No draft was saved and "
                    "no email was sent."
                ),
                remediation={
                    "preferred": (
                        "Read the source message with get_email_by_id to confirm it has a body. A "
                        "message whose content Mail cannot read has nothing for the verifier to "
                        "prove the quote against."
                    ),
                    "script_output": result,
                },
            )
        )
    return serialize_tool_error(
        ToolError(
            code="REPLY_NOT_COMPLETED",
            message=(
                "The reply compose script did not report success, so there is no draft to describe. "
                "The script's own message is preserved verbatim under remediation.script_output."
            ),
            remediation={
                "preferred": (
                    "Read script_output: it is Mail's own failure text, most often that no message "
                    "matched message_id within recent_days. Widen recent_days or re-resolve the id "
                    "with search_emails, then retry."
                ),
                "script_output": result,
            },
        )
    )
