"""How long the native typed reply may take, and which bodies are refused.

Leaf module split out of ``reply_runner.py``, which owns the abort-sentinel
dispatch and the stray-artifact delete and had no line budget left. The two
concerns never shared state: everything here is a pure function of
``len(reply_body)`` and the typing constants, computed *before* any AppleScript
runs, while ``reply_runner`` only ever reads what a compose run came back with.

The projection is the Python half of a two-caller contract. The AppleScript in
``typing_scripts.py`` sizes its editor-drain budget from ``bodyLength`` on its
own and never sees the timeout it was granted, so both sides derive from
``constants.typing_settle_attempts``. If they disagree, ``AppleScriptTimeout``
fires mid-drain and strands a compose window with the body typed and unsaved.
"""

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.tools.compose.constants import (
    TYPING_CHUNK_SIZE,
    TYPING_INTER_CHUNK_DELAY,
    TYPING_PER_CHUNK_OVERHEAD_SECONDS,
    TYPING_SETTLE_DELAY,
    typing_settle_attempts,
)

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
