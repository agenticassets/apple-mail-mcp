"""``reply_to_email`` tool: native or object-model replies with optional windowless fallback."""

import json
from contextlib import suppress
from pathlib import Path

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error, target_selector_deprecated_error
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    build_mailbox_ref,
    escape_applescript,
    inject_preferences,
)
from apple_mail_mcp.server import DESTRUCTIVE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import compose
from apple_mail_mcp.tools.compose.constants import (
    _MESSAGE_ID_REQUIRED_ERROR,
)
from apple_mail_mcp.tools.compose.helpers import (
    _check_open_compose_window_cap,
    _clean_applescript_error,
    _resolve_account,
    _resolve_signature_name,
    _validate_from_address,
    _validate_signature_name,
)
from apple_mail_mcp.tools.compose.lookup_scripts import (
    _build_found_message_lookup,
)
from apple_mail_mcp.tools.compose.payload import (
    _build_recipient_loops,
    _compose_sender_script,
    _strip_cdata_wrappers,
)
from apple_mail_mcp.tools.compose.reply_identity import native_reply_draft_identity_from_output
from apple_mail_mcp.tools.compose.reply_runner import (
    _delete_reply_artifact,
    _native_reply_abort_response,
    _native_reply_effective_timeout,
    _unrecognized_reply_output_response,
)
from apple_mail_mcp.tools.compose.reply_script_helpers import (
    _reply_command_options,
    _reply_mode_plan,
    _reply_signature_script,
)
from apple_mail_mcp.tools.compose.reply_scripts import (
    _build_reply_native_window_applescript,
    _build_reply_objectmodel_applescript,
)
from apple_mail_mcp.tools.compose.saved_draft_checks import _verify_saved_reply_draft
from apple_mail_mcp.tools.compose.verification import (
    _extract_output_field,
    _format_reply_verification_lines,
    _reply_success_payload,
    _reply_verification_failure_response,
    _ReplyDraftVerification,
)


@mcp.tool(annotations=DESTRUCTIVE_TOOL_ANNOTATIONS, title="Reply to Email")
@inject_preferences
def reply_to_email(
    account: str | None = None,
    subject_keyword: str = "",
    reply_body: str = "",
    reply_to_all: bool = False,
    cc: str | None = None,
    bcc: str | None = None,
    send: bool = False,
    mode: str | None = None,
    attachments: str | None = None,
    body_html: str | None = None,
    from_address: str | None = None,
    message_id: str | None = None,
    mailbox: str = "INBOX",
    recent_days: float = 2.0,
    timeout: int | None = None,
    include_signature: bool = True,
    signature_name: str | None = None,
    output_format: str = "text",
    native_format: bool = True,
    allow_windowless_fallback: bool = False,
) -> str:
    """
    Reply to an email by exact ``message_id`` using Mail's native reply window.
    Native drafting (``native_format=True``, the default) is the only supported
    path; the windowless ``native_format=False`` fallback is gated and returns
    ``WINDOWLESS_FALLBACK_DISABLED`` unless ``allow_windowless_fallback=True`` is
    passed. Agents must never use the fallback.

    The saved draft's full body above the quoted original is verified
    case-sensitively (not just its first line), the native quote must remain
    present, and every requested attachment must be detected before this call
    reports success. On a placement mismatch with a known artifact id, the native
    path automatically deletes that artifact and retypes the identical body
    once before re-verifying; a mismatch that still does not resolve returns
    ``REPLY_BODY_MISMATCH`` naming the suspect Drafts artifact id.

    ``subject_keyword`` is a deprecated selector retained for v3.x schema
    compatibility. Use ``search_emails(...)`` or ``list_inbox_emails(...)`` to
    discover candidate ids, then pass ``message_id``. Passing ``subject_keyword``
    without ``message_id`` returns ``TARGET_SELECTOR_DEPRECATED``.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to `DEFAULT_MAIL_ACCOUNT` env var if `account` is omitted.
        subject_keyword: Deprecated schema-compat selector. Returns
            ``TARGET_SELECTOR_DEPRECATED`` when ``message_id`` is omitted.
        reply_body: The body text of the reply
        reply_to_all: If True, reply to all recipients; if False, reply only to sender (default: False)
        cc: Optional CC recipients, comma-separated for multiple
        bcc: Optional BCC recipients, comma-separated for multiple
        send: If True, send immediately; if False (default), save as draft. Ignored if mode is set.
        mode: Delivery mode — "draft" (default, save quietly to Drafts and close), "open" (save first, then leave compose window open for review), or "send" (send immediately). Overrides send parameter when set.
        attachments: Optional file paths to attach, comma-separated for multiple (e.g., "/path/to/file1.png,/path/to/file2.pdf")
        body_html: Accepted for backwards compatibility but ignored. Replies use Mail's native reply composer and
            insert reply_body as plain text above Mail's native quoted thread.
        from_address: Optional sender address to use for this reply. Must be one of the account's configured email addresses. When omitted, Mail uses the account's default "Send new messages from" setting.
        message_id: Required. Exact numeric Apple Mail message id from
            ``search_emails`` or ``list_inbox_emails``.
        mailbox: Mailbox containing ``message_id`` (default: ``INBOX``). Pass
            a folder returned by ``search_emails`` or ``list_mailboxes``, for
            example ``Archive`` or ``All Mail``. A unique nested folder leaf
            is accepted; when that leaf is ambiguous, pass the
            ``Parent/Child`` path from ``list_mailboxes``. The lookup remains
            exact-id only and does not broaden into a mailbox scan.
        recent_days: Schema-compat parameter for deprecated subject_keyword path
            (default: 2.0 / 48h). Ignored when ``message_id`` is set.
        timeout: Optional per-AppleScript timeout in seconds. When omitted, the native
            path (``native_format=True``) scales the timeout with the projected
            chunked-typing time for ``reply_body``, floored at 120s; a body long
            enough to exceed the documented typing budget is refused up front with
            ``REPLY_BODY_TYPING_BUDGET_EXCEEDED`` instead of risking a mid-typing
            timeout. Alias validation uses up to 30s.
        include_signature: Whether to apply the configured/default Mail signature (default: True).
        signature_name: Optional Mail signature name; falls back to DEFAULT_MAIL_SIGNATURE when omitted.
        output_format: "text" (default) preserves the existing success output.
            "json" returns machine-readable draft/open success metadata after
            saved-draft verification succeeds.
        native_format: When True (default), compose the reply in Mail's native reply
            window so the draft keeps Mail's colored quote bar and the account's
            default reply signature (with logo), inserting reply_body above the
            quote via typed keystrokes sent in small focus-guarded chunks (never one
            keystroke of the whole body). This needs the Mail window to take and
            keep focus and Accessibility permission for the host process; losing
            focus before typing starts returns ``REPLY_WINDOW_FOCUS_FAILED`` or
            ``REPLY_SUBJECT_GUARD_MISMATCH``, and losing it mid-typing returns
            ``REPLY_BODY_TYPING_INTERRUPTED`` with the partially typed compose
            window already discarded. Known limitation: accented or composed
            characters may corrupt during typing (observed live: "Renée" saved
            as "Renae"); the full-body verifier catches this and returns
            ``REPLY_BODY_MISMATCH`` instead of saving silently, so prefer ASCII
            spellings in ``reply_body`` until the typing-fidelity follow-up
            ships. When False, compose the
            reply through the object model with no window (headless/bulk-safe, no
            Accessibility needed); the quote and signature are flattened to plain
            text. ``native_format=False`` is gated: it returns
            ``WINDOWLESS_FALLBACK_DISABLED`` unless ``allow_windowless_fallback=True``
            is also passed, so agents cannot drift into the fallback path.
        allow_windowless_fallback: Explicit ack required to use the windowless
            ``native_format=False`` path. Defaults to False; agents must never set
            it. Exists for deliberate headless/bulk/CI reply runs only.

    Returns:
        Confirmation message with details of the reply sent, saved draft, or opened draft
    """
    if output_format not in {"text", "json"}:
        return "Error: Invalid output_format. Use: text, json"

    if not native_format and not allow_windowless_fallback:
        return serialize_tool_error(
            ToolError(
                code="WINDOWLESS_FALLBACK_DISABLED",
                message=(
                    "The windowless reply path (native_format=False) is disabled by "
                    "default; native reply drafting (native_format=True) is the only "
                    "supported path for normal use because it preserves Mail's rich "
                    "quote bar and logo signature."
                ),
                remediation={
                    "preferred": (
                        "Retry with native_format=True (the default) and Mail visible so "
                        "the reply window can take focus. On REPLY_WINDOW_FOCUS_FAILED no "
                        "draft was saved: retry with Mail visible and not being clicked."
                    ),
                    "headless_only": (
                        "The windowless path is reserved for deliberate headless/bulk/CI "
                        "runs with no GUI focus. If that is actually your situation, pass "
                        "allow_windowless_fallback=True with native_format=False. Agents "
                        "must never set this flag on their own."
                    ),
                },
            )
        )

    if not message_id and not subject_keyword:
        return _MESSAGE_ID_REQUIRED_ERROR
    if not message_id and subject_keyword:
        return target_selector_deprecated_error(
            "reply_to_email",
            ("subject_keyword",),
            preferred="Call search_emails(...) or list_inbox_emails(...) first, then pass message_id.",
            discovery="search_emails(subject_keyword=..., recent_days=..., limit=...)",
            exact_selector="message_id",
        )

    account, account_error = _resolve_account(account, timeout=timeout)
    if account_error:
        return account_error
    assert account is not None  # _resolve_account guarantees non-None when error is None

    lookup_script, lookup_error = _build_found_message_lookup(
        "sourceMailbox",
        message_id=message_id,
        subject_keyword=None,
        recent_days=recent_days,
        messages_var="sourceMessages",
        tool_name="reply_to_email",
    )
    if lookup_error:
        if isinstance(lookup_error, ToolError):
            return serialize_tool_error(lookup_error)
        return lookup_error

    reply_body = _strip_cdata_wrappers(reply_body) or ""
    # body_html is accepted for backwards compatibility only and is ignored:
    # replies use Mail's native reply composer so quoted chains preserve Mail's
    # normal formatting; reply_body is inserted as plain text above that quote.

    if native_format:
        # A literal tab is a field-navigation key under System Events keystroke
        # typing: it can move focus out of the compose body (e.g. into the
        # subject field), corrupting the draft. Convert before anything reads
        # reply_body downstream (the temp file write below, the retry rewrite
        # at the bottom of the verification loop, and verification itself all
        # reuse this same variable), so one conversion covers every use. The
        # saved-draft verifier strips all whitespace, including tabs and
        # spaces, before comparing, so this cannot cause a verification
        # mismatch. The object-model path assigns content directly and never
        # goes through keystroke typing, so it is left untouched.
        reply_body = reply_body.replace("\t", " ")

    try:
        sender_override, sender_error = _validate_from_address(account, from_address, timeout=timeout)
    except AppleScriptTimeout:
        return (
            "Error: AppleScript timed out while validating sender for account "
            f"{account!r}. Try again or pass a larger `timeout`."
        )
    if sender_error:
        return sender_error
    resolved_signature_name = _resolve_signature_name(include_signature, signature_name)
    signature_error = _validate_signature_name(resolved_signature_name, timeout=timeout)
    if signature_error:
        return signature_error

    # Resolve delivery mode before creating temp files so early contract errors
    # leave no local artifacts behind.
    if mode is not None:
        if mode not in ("send", "draft", "open"):
            return f"Error: Invalid mode '{mode}'. Use: send, draft, open"
        effective_mode = mode
    else:
        effective_mode = "send" if send else "draft"

    blocked = compose._send_blocked(effective_mode)
    if blocked:
        return blocked
    if output_format == "json" and effective_mode == "send":
        return "Error: output_format='json' is only supported for mode='draft' or mode='open'."
    if effective_mode == "send" and attachments:
        return serialize_tool_error(
            ToolError(
                code="REPLY_SEND_REQUIRES_VERIFIED_DRAFT",
                message=(
                    "Replies with attachments must be saved and verified before sending. "
                    "No Mail compose or send action was performed."
                ),
                remediation={
                    "preferred_mode": "draft",
                    "preferred": (
                        "Retry with mode='draft', inspect the exact verified draft, then send that "
                        "draft explicitly after approval."
                    ),
                },
            )
        )

    if effective_mode == "open":
        cap_err = _check_open_compose_window_cap()
        if cap_err:
            return cap_err

    # Escape all user inputs for AppleScript
    safe_account = escape_applescript(account)
    mailbox_lookup = build_mailbox_ref(mailbox, account_var="targetAccount", var_name="sourceMailbox")
    not_found_message = f"Error: No email found for message_id={message_id}"

    # Write reply body to a temp file to avoid AppleScript string escaping
    # issues with special characters (em dashes, curly quotes, colons, etc.)
    with compose.tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        prefix="mail_reply_",
        delete=False,
        encoding="utf-8",
    ) as body_tmp:
        body_tmp.write(reply_body)
        body_temp_path = body_tmp.name

    cc_script, bcc_script, _, _ = _build_recipient_loops(cc, bcc, message_var="replyMessage")

    # Build attachment script if provided (object model: attach to replyMessage)
    attachment_script = ""
    attachment_info = ""
    validated_paths: list[str] = []
    if attachments:
        validated_paths, error = compose._validate_attachment_paths(attachments)
        if error:
            with suppress(OSError):
                Path(body_temp_path).unlink(missing_ok=True)
            return error
        for path in validated_paths:
            safe_path = escape_applescript(path)
            attachment_script += f'''
                set theFile to POSIX file "{safe_path}"
                tell replyMessage
                    make new attachment with properties {{file name:theFile}} at after the last paragraph of content
                end tell
                delay 1
            '''
            attachment_info += f"  {path}\n"

    safe_cc = escape_applescript(cc) if cc else ""
    safe_bcc = escape_applescript(bcc) if bcc else ""
    safe_attachment_info = escape_applescript(attachment_info) if attachment_info else ""

    mode_plan = _reply_mode_plan(effective_mode)

    cleanup_script = f'do shell script "rm -f " & quoted form of "{body_temp_path}"'

    signature_script = _reply_signature_script(resolved_signature_name, include_signature=include_signature)

    if native_format:
        # Native reply: let Mail own the reply identity and its default signature
        # (with logo). Only set the sender when the caller explicitly overrode it;
        # never pin the account's single alias here — changing the From on the open
        # reply window makes Mail re-insert a text-only signature and drop the
        # embedded logo image (the saved draft loses the logo).
        native_sender_script = (
            f'set sender of replyMessage to "{escape_applescript(sender_override)}"' if sender_override else ""
        )
        # Always open the reply window so Mail renders its native rich quote +
        # signature; the body is typed in. Reuse the "open" option string only for
        # the "with opening window [and reply to all]" wording, independent of mode.
        native_reply_options, _ = _reply_command_options("open", reply_to_all)
        script = _build_reply_native_window_applescript(
            header_text=mode_plan.header_text,
            success_text=mode_plan.success_text,
            safe_account=safe_account,
            mailbox_lookup=mailbox_lookup,
            lookup_script=lookup_script,
            not_found_message=not_found_message,
            body_temp_path=body_temp_path,
            reply_options=native_reply_options,
            sender_script=native_sender_script,
            signature_script=signature_script,
            cc_script=cc_script,
            bcc_script=bcc_script,
            attachment_script=attachment_script,
            mode=effective_mode,
            cleanup_script=cleanup_script,
            safe_cc=safe_cc,
            safe_bcc=safe_bcc,
            safe_attachment_info=safe_attachment_info,
            has_cc=bool(cc),
            has_bcc=bool(bcc),
            has_attachments=bool(attachments),
        )
    else:
        # Object-model path (no window): pin the single alias so the headless draft
        # still sends from the account's own address, since there is no native reply
        # window to inherit the identity from.
        objectmodel_sender_script = _compose_sender_script("replyMessage", "targetAccount", sender_override)
        reply_options, reply_settle_delay = _reply_command_options(effective_mode, reply_to_all)
        script = _build_reply_objectmodel_applescript(
            header_text=mode_plan.header_text,
            success_text=mode_plan.success_text,
            safe_account=safe_account,
            mailbox_lookup=mailbox_lookup,
            lookup_script=lookup_script,
            not_found_message=not_found_message,
            body_temp_path=body_temp_path,
            reply_options=reply_options,
            reply_settle_delay=reply_settle_delay,
            sender_script=objectmodel_sender_script,
            signature_script=signature_script,
            cc_script=cc_script,
            bcc_script=bcc_script,
            attachment_script=attachment_script,
            post_action=mode_plan.post_action,
            cleanup_script=cleanup_script,
            safe_cc=safe_cc,
            safe_bcc=safe_bcc,
            safe_attachment_info=safe_attachment_info,
            has_cc=bool(cc),
            has_bcc=bool(bcc),
            has_attachments=bool(attachments),
        )

    try:
        if native_format:
            effective_timeout, timeout_error = _native_reply_effective_timeout(reply_body, timeout)
            if timeout_error:
                return timeout_error
        else:
            effective_timeout = timeout

        def _run_reply_script() -> str:
            return (
                compose.run_applescript(script)
                if effective_timeout is None
                else compose.run_applescript(script, timeout=effective_timeout)
            )

        current_result = _run_reply_script()
        abort_response = _native_reply_abort_response(
            current_result, account=account, reply_body=reply_body, timeout=timeout
        )
        if abort_response is not None:
            return abort_response

        if effective_mode not in ("draft", "open"):
            return current_result
        if mode_plan.success_text not in current_result:
            return _unrecognized_reply_output_response(current_result, output_format=output_format)

        mode_text = "opened" if effective_mode == "open" else "created"
        reply_subject = _extract_output_field(current_result, "Subject")
        native_draft_identity = native_reply_draft_identity_from_output(current_result) if native_format else None
        draft_id = (
            native_draft_identity.draft_id
            if native_draft_identity
            else (None if native_format else _extract_output_field(current_result, "Draft ID"))
        )
        quoted_needle = _extract_output_field(current_result, "Quote Needle")
        quote_anchor = _extract_output_field(current_result, "Quote Anchor")
        # The native window inherits Mail's own default reply signature (with logo),
        # whose rich text we never set and cannot reliably substring-match. Only
        # assert a signature when one was explicitly requested by name; otherwise
        # skip the check so the native default signature is not flagged "missing".
        signature_requested_for_verify: bool | None = include_signature
        if native_format and include_signature and not resolved_signature_name:
            signature_requested_for_verify = None

        # Only the native path retries: one automatic delete-artifact-and-retype
        # pass when the FULL-body verifier (AGENTIC-1214) finds a placement
        # failure with a concrete artifact id to delete. The object-model path
        # assigns content directly and never mismatches this way, so it gets a
        # single verification attempt like before.
        retyped = False
        stale_artifact_id: str | None = None
        verification: _ReplyDraftVerification | None = None
        max_attempts = 2 if native_format else 1
        for attempt in range(max_attempts):
            verification = _verify_saved_reply_draft(
                account,
                reply_subject or "",
                reply_body,
                draft_id=draft_id,
                native_draft_identity=native_draft_identity,
                quoted_needle=quoted_needle,
                quote_anchor=quote_anchor,
                expected_attachment_count=len(validated_paths) if validated_paths else None,
                expected_attachment_names=[Path(path).name for path in validated_paths],
                signature_requested=signature_requested_for_verify,
                expected_signature_name=resolved_signature_name,
                timeout=timeout,
            )
            if verification.ok:
                break

            artifact_id = verification.body_missing_artifact_id
            placement_fail = verification.status in ("body_missing", "body_after_quote")
            # Invariant: this tool only ever deletes a Drafts artifact whose id
            # Mail itself returned for this compose call (draft_id, extracted
            # from Mail's own compose output). The verifier's subject-scan
            # fallback records the FIRST mismatching same-subject draft seen
            # across up to 20 eventual-consistency attempts, so under Exchange
            # materialization lag that id can belong to a pre-existing draft
            # the user wrote on the same thread. Requiring artifact_id to equal
            # draft_id keeps the delete-and-retype retry from ever touching a
            # draft Mail did not just create for us.
            can_retry = (
                native_format
                and attempt == 0
                and placement_fail
                and bool(artifact_id)
                and bool(draft_id)
                and native_draft_identity is not None
                and native_draft_identity.is_rfc_backed
                and artifact_id == draft_id
            )
            if not can_retry:
                return _reply_verification_failure_response(
                    verification,
                    mode_text=mode_text,
                    reply_body=reply_body,
                    retyped=retyped,
                    stale_artifact_id=stale_artifact_id,
                )

            assert artifact_id is not None  # can_retry required a truthy artifact_id
            assert native_draft_identity is not None  # can_retry required a persisted identity capsule
            deleted = _delete_reply_artifact(
                account,
                artifact_id,
                identity=native_draft_identity,
                timeout=timeout,
            )
            stale_artifact_id = None if deleted else artifact_id
            retyped = True

            # Rewrite the same body_temp_path (the prior run's `rm -f` already
            # removed it) and re-run the SAME script; nothing about the compose
            # script itself changes between attempts, so no rebuild is needed.
            Path(body_temp_path).write_text(reply_body, encoding="utf-8")
            current_result = _run_reply_script()
            retry_abort_response = _native_reply_abort_response(
                current_result, account=account, reply_body=reply_body, timeout=timeout
            )
            if retry_abort_response is not None:
                return retry_abort_response
            if mode_plan.success_text not in current_result:
                return _unrecognized_reply_output_response(current_result, output_format=output_format)
            reply_subject = _extract_output_field(current_result, "Subject")
            native_draft_identity = native_reply_draft_identity_from_output(current_result)
            draft_id = native_draft_identity.draft_id if native_draft_identity else None
            quoted_needle = _extract_output_field(current_result, "Quote Needle")
            quote_anchor = _extract_output_field(current_result, "Quote Anchor")

        assert verification is not None  # loop always runs at least once
        if output_format == "json":
            return json.dumps(
                _reply_success_payload(
                    mode=effective_mode,
                    reply_subject=reply_subject,
                    draft_id=draft_id,
                    verification=verification,
                    captured_draft_id_source=(
                        (
                            "persisted_header_identity"
                            if native_draft_identity.is_rfc_backed
                            else "transaction_scoped_numeric_identity"
                        )
                        if native_draft_identity
                        else "mail_returned"
                    ),
                    retyped=retyped,
                    stale_artifact_id=stale_artifact_id,
                )
            )
        return current_result + _format_reply_verification_lines(
            verification, draft_id, retyped=retyped, stale_artifact_id=stale_artifact_id
        )
    except AppleScriptTimeout:
        return (
            f"Error: AppleScript timed out while replying on account {account!r}. Try again or pass a larger `timeout`."
        )
    except Exception as e:
        return f"Error: Reply failed: {_clean_applescript_error(e)}"
    finally:
        # Belt-and-suspenders cleanup in case AppleScript didn't run
        with suppress(OSError):
            Path(body_temp_path).unlink(missing_ok=True)
