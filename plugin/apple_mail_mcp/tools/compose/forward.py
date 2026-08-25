"""``forward_email`` tool: draft-first forwarding with explicit attachments."""

import json
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error, target_selector_deprecated_error
from apple_mail_mcp.core import AppleScriptTimeout, escape_applescript, inject_preferences
from apple_mail_mcp.server import DESTRUCTIVE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import compose
from apple_mail_mcp.tools.compose.attachment_draft_verification import (
    marker_draft_verification_handlers,
)
from apple_mail_mcp.tools.compose.constants import _MESSAGE_ID_REQUIRED_ERROR
from apple_mail_mcp.tools.compose.forward_attachment_scripts import (
    forward_marker_draft_proof_call,
    forward_marker_draft_verification_handlers,
    forward_marker_finalize_script,
    forward_restore_outgoing_subject_script,
)
from apple_mail_mcp.tools.compose.helpers import (
    _check_open_compose_window_cap,
    _clean_applescript_error,
    _resolve_account,
    _resolve_signature_name,
    _validate_from_address,
)
from apple_mail_mcp.tools.compose.lookup_scripts import _build_found_message_lookup, _compose_signature_script
from apple_mail_mcp.tools.compose.payload import (
    _build_recipient_loops,
    _compose_sender_script,
    _split_addresses,
    _strip_cdata_wrappers,
    _validate_attachment_paths,
)
from apple_mail_mcp.tools.compose.standalone_draft_identity_scripts import (
    _standalone_draft_identity_handlers,
    standalone_draft_identity_resolver_script,
    standalone_draft_identity_setup_script,
)
from apple_mail_mcp.tools.compose.verification import (
    _extract_output_field,
    _first_non_empty_line,
    _format_forward_verification_lines,
)


def _forward_attachment_verification_error(detail: str) -> str:
    """Return a fail-closed response for an attachment-bearing forward draft."""
    return (
        "Error: FORWARD_DRAFT_ATTACHMENT_VERIFICATION_FAILED\n"
        f"Saved forward draft was not ready: {detail}. No email was sent. "
        "Inspect the exact Drafts message and retry after Mail finishes saving.\n"
    )


# A bare ``try`` around ``content of foundMessage`` used to leave ``origContent``
# as "" whenever the read threw, so the forward carried only the
# "---------- Forwarded message ----------" header block with the quoted
# original silently missing — and still reported "Forward saved as draft."
# Emptiness cannot be the signal, because a genuinely empty original body is a
# legitimate case (subject-only mail, invitations), so the script records
# whether the read itself succeeded and fails closed before any outgoing
# message exists. Same fail-closed shape as the reply path's
# ``QUOTE_PROOF_UNAVAILABLE`` guard in ``compose/reply_scripts.py``.
_FORWARD_QUOTE_UNAVAILABLE_RETURN = (
    'return "Error: FORWARD_QUOTE_UNAVAILABLE" & return & '
    '"Detail: Mail could not read the original message body, so the forward would have contained only the '
    "forwarded-header block with the quoted original missing. No draft was saved and nothing was sent. "
    'Retry after Mail finishes downloading the message."'
)


def _is_known_inline_signature_asset(filename: str) -> bool:
    """Return whether Mail identified a provider-inserted Outlook image asset.

    Mail may retain an account-provided Outlook inline signature image even
    when the caller did not select a signature. This narrow allowance is only
    for that provider-generated naming convention; every caller-selected file
    still has to be present and readable by exact filename and multiplicity.
    """
    return filename.startswith("Outlook-") and filename.lower().endswith(".png")


def _verify_exact_saved_forward_draft(
    account: str,
    *,
    draft_id: str | None,
    to: str,
    subject: str | None,
    lead_message: str | None,
    expected_signature: bool | None,
    attachment_paths: list[str],
    attachment_proof: str,
    timeout: int | None,
) -> str:
    """Verify one exact saved forward, including exact caller-selected attachments."""
    if attachment_paths and attachment_proof == "verified":
        return (
            "Attachment Verification Status: verified\n"
            "Attachment Proof Scope: same-operation marker-bound persisted Drafts row\n"
            "Draft Locator: unavailable after iCloud ID rewrite\n"
            "Draft Locator Stability: not a reusable identity\n"
            "Attachment-bearing forward draft is ready for human review."
        )

    if not draft_id:
        return (
            "Error: FORWARD_DRAFT_ID_UNAVAILABLE\n"
            "Mail saved the forward but did not expose its exact Drafts message ID. "
            "No email was sent; inspect Drafts and retry after Mail finishes saving.\n"
        )

    verification_args: dict[str, Any] = {
        "account": account,
        "draft_id": draft_id,
        "expected_to": to,
        "expected_subject": subject,
        "expected_body_contains": _first_non_empty_line(lead_message or "") or None,
        "expected_signature": expected_signature,
        "timeout": timeout,
    }
    if attachment_paths:
        verification_args["expected_attachments"] = attachment_paths
    try:
        raw_verification = compose.verify_draft(**verification_args)
    except Exception:
        return (
            "Error: FORWARD_DRAFT_VERIFICATION_FAILED\n"
            "Mail could not verify the exact saved forward Drafts message. No email was sent.\n"
        )
    try:
        payload = json.loads(raw_verification)
    except json.JSONDecodeError:
        return (
            "Error: FORWARD_DRAFT_VERIFICATION_FAILED\n"
            "Mail returned an invalid exact-Drafts verification response. No email was sent.\n"
        )

    if payload.get("found") is not True:
        return (
            "Error: FORWARD_DRAFT_VERIFICATION_FAILED\n"
            "Mail did not verify the exact saved forward Drafts message. No email was sent.\n"
        )

    verified_id = str(payload.get("draft_id") or "")
    if verified_id != draft_id:
        return (
            "Error: FORWARD_DRAFT_ID_MISMATCH\n"
            f"Mail verification returned Draft ID {verified_id or 'unavailable'}, not the saved Draft ID {draft_id}. "
            "No email was sent.\n"
        )

    if attachment_paths:
        attachments = payload.get("attachments")
        found_rows = attachments.get("found") if isinstance(attachments, dict) else None
        if not isinstance(found_rows, list):
            return _forward_attachment_verification_error("Mail did not return attachment records")

        expected_names = Counter(Path(path).name for path in attachment_paths)
        selected_rows: list[dict[str, Any]] = []
        extra_rows: list[dict[str, Any]] = []
        remaining_expected = expected_names.copy()
        for row in found_rows:
            if not isinstance(row, dict):
                return _forward_attachment_verification_error("Mail returned an invalid attachment record")
            attachment_row = {str(key): value for key, value in row.items()}
            filename = str(attachment_row.get("filename") or "")
            if remaining_expected[filename] > 0:
                remaining_expected[filename] -= 1
                selected_rows.append(attachment_row)
            else:
                extra_rows.append(attachment_row)

        if any(remaining_expected.values()) or any(
            not _is_known_inline_signature_asset(str(row.get("filename") or "")) for row in extra_rows
        ):
            return _forward_attachment_verification_error(
                "the saved attachment filename/count set did not match the requested files and known inline signature assets"
            )

        unreadable = [
            str(row.get("filename") or "attachment")
            for row in selected_rows
            if not isinstance(row.get("size"), int) or row["size"] <= 0
        ]
        if unreadable:
            return _forward_attachment_verification_error(
                "Mail reported unreadable attachment data for " + ", ".join(unreadable)
            )

    lines = _format_forward_verification_lines(raw_verification, draft_id)
    if attachment_paths:
        lines += "Attachment Verification Status: verified\n"
        lines += f"Attachments Applied Count: {len(attachment_paths)}\n"
    return lines


@mcp.tool(annotations=DESTRUCTIVE_TOOL_ANNOTATIONS, title="Forward Email")
@inject_preferences
def forward_email(
    account: str | None = None,
    subject_keyword: str = "",
    to: str = "",
    message: str | None = None,
    mailbox: str = "INBOX",
    cc: str | None = None,
    bcc: str | None = None,
    from_address: str | None = None,
    mode: str = "draft",
    message_id: str | None = None,
    recent_days: float = 2.0,
    timeout: int | None = None,
    include_signature: bool = True,
    signature_name: str | None = None,
    attachments: str | None = None,
) -> str:
    """
    Forward an email to one or more recipients by exact ``message_id``.

    ``subject_keyword`` is a deprecated selector retained for v3.x schema
    compatibility. Use ``search_emails(...)`` or ``list_inbox_emails(...)`` to
    discover candidate ids, then pass ``message_id``. Passing ``subject_keyword``
    without ``message_id`` returns ``TARGET_SELECTOR_DEPRECATED``.

    A bare ``https://`` URL in ``message`` may become a Mail link-preview card
    in the open window; this tool does not create or verify those cards.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to `DEFAULT_MAIL_ACCOUNT` env var if `account` is omitted.
        subject_keyword: Deprecated schema-compat selector. Returns
            ``TARGET_SELECTOR_DEPRECATED`` when ``message_id`` is omitted.
        to: Recipient email address(es), comma-separated for multiple
        message: Optional message to add before forwarded content
        mailbox: Mailbox to search in (default: "INBOX")
        cc: Optional CC recipients, comma-separated for multiple
        bcc: Optional BCC recipients, comma-separated for multiple
        from_address: Optional sender address to use when forwarding. Must be one of the account's configured email addresses. When omitted, Mail uses the account's default "Send new messages from" setting.
        mode: Delivery mode — "draft" (default, save quietly to Drafts), "open" (save first, then leave compose window open for review), or "send" (send immediately)
        message_id: Required. Exact numeric Apple Mail message id from
            ``search_emails`` or ``list_inbox_emails``.
        recent_days: Schema-compat parameter for deprecated subject_keyword path
            (default: 2.0 / 48h). Ignored when ``message_id`` is set.
        timeout: Optional per-AppleScript timeout in seconds. Defaults to the standard 120s. Raise this when working with large mailboxes or slow accounts.
        include_signature: Whether to apply the configured/default Mail signature (default: True).
        signature_name: Optional Mail signature name; falls back to DEFAULT_MAIL_SIGNATURE when omitted.
        attachments: Optional comma-separated local file paths. These are added
            only to an exact verified draft or open review window.

    Returns:
        Confirmation with the current numeric Drafts locator for saved drafts.
        This locator can drift after server sync; re-resolve it immediately
        before any later draft mutation.
    """

    if not message_id and not subject_keyword:
        return _MESSAGE_ID_REQUIRED_ERROR
    if not to:
        return "Error: 'to' is required"
    if not message_id and subject_keyword:
        return target_selector_deprecated_error(
            "forward_email",
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
        "targetMailbox",
        message_id=message_id,
        subject_keyword=None,
        recent_days=recent_days,
        tool_name="forward_email",
    )
    if lookup_error:
        if isinstance(lookup_error, ToolError):
            return serialize_tool_error(lookup_error)
        return lookup_error

    message = _strip_cdata_wrappers(message)

    # Validate mode
    if mode not in ("send", "draft", "open"):
        return f"Error: Invalid mode '{mode}'. Use: send, draft, open"
    if attachments and mode == "send":
        return "Error: Attachments require mode='draft' or mode='open' so Mail can verify the saved draft."
    blocked = compose._send_blocked(mode)
    if blocked:
        return blocked

    if mode == "open":
        cap_err = _check_open_compose_window_cap()
        if cap_err:
            return cap_err

    attachment_paths: list[str] = []
    if attachments:
        attachment_paths, attachment_error = _validate_attachment_paths(attachments)
        if attachment_error:
            return attachment_error

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

    # Escape all user inputs for AppleScript
    safe_account = escape_applescript(account)
    safe_to = escape_applescript(to)
    safe_mailbox = escape_applescript(mailbox)
    not_found_message = f"Error: No email found for message_id={message_id}"

    sender_script = _compose_sender_script("forwardMessage", "targetAccount", sender_override)
    signature_script = _compose_signature_script("forwardMessage", resolved_signature_name)

    cc_script, bcc_script, _, _ = _build_recipient_loops(cc, bcc, message_var="forwardMessage")

    safe_cc = escape_applescript(cc) if cc else ""
    safe_bcc = escape_applescript(bcc) if bcc else ""

    attachment_script = ""
    for attachment_path in attachment_paths:
        safe_attachment_path = escape_applescript(attachment_path)
        attachment_script += f'''
        set theFile to POSIX file "{safe_attachment_path}"
        make new attachment with properties {{file name:theFile}} at after the last paragraph
        delay 1
        '''

    # Build TO recipients (split comma-separated)
    to_script = ""
    for addr in _split_addresses(to):
        safe_addr = escape_applescript(addr)
        to_script += f'''
                make new to recipient at end of to recipients of forwardMessage with properties {{address:"{safe_addr}"}}
        '''

    # Optional leading message is composed as plain text via the object model
    # (no clipboard, no System Events keystroke). Write it to a temp file so
    # special characters survive without AppleScript escaping headaches.
    fwd_msg_temp_path = None
    fwd_read_script = 'set fwdLeadText to ""'
    fwd_cleanup_script = ""
    if message:
        with compose.tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            prefix="mail_fwd_",
            delete=False,
            encoding="utf-8",
        ) as fwd_msg_tmp:
            fwd_msg_tmp.write(message)
            fwd_msg_temp_path = fwd_msg_tmp.name
        fwd_read_script = (
            f'set fwdLeadText to (do shell script "cat " & quoted form of "{fwd_msg_temp_path}") & return & return'
        )
        fwd_cleanup_script = f'do shell script "rm -f " & quoted form of "{fwd_msg_temp_path}"'

    forward_marker_handlers = ""
    forward_marker_finalization_script = ""
    forward_attachment_proof_output = ""
    restore_forward_subject_script = ""
    initial_forward_subject_script = "set initialForwardSubject to fwdSubject"
    if attachment_paths and mode in {"draft", "open"}:
        forward_subject_marker = f"__apple_mail_forward_{uuid.uuid4().hex}__"
        forward_marker_handlers = marker_draft_verification_handlers()
        forward_marker_handlers += forward_marker_draft_verification_handlers()
        marker_proof_script = forward_marker_draft_proof_call(
            to_addresses=_split_addresses(to),
            cc_addresses=_split_addresses(cc),
            bcc_addresses=_split_addresses(bcc),
            marker=forward_subject_marker,
            body=message or "",
            attachment_paths=attachment_paths,
        )
        forward_marker_finalization_script = forward_marker_finalize_script(forward_subject_marker, marker_proof_script)
        restore_forward_subject_script = forward_restore_outgoing_subject_script()
        initial_forward_subject_script = f'set initialForwardSubject to "{escape_applescript(forward_subject_marker)}"'
        forward_attachment_proof_output = (
            'set outputText to outputText & "Forward Attachment Proof: " & forwardAttachmentProof & return'
        )

    visible_lower = "true" if mode == "open" else "false"
    if mode == "send":
        header_text = "FORWARDING EMAIL"
        post_forward_action = "send forwardMessage"
        success_text = "Email forwarded successfully."
    elif mode == "open":
        header_text = "OPENING FORWARD FOR REVIEW"
        post_forward_action = "save forwardMessage\n            activate"
        success_text = "Forward opened in Mail for review. Edit and send when ready."
    else:
        header_text = "SAVING FORWARD AS DRAFT"
        post_forward_action = "save forwardMessage"
        success_text = "Forward saved as draft."

    draft_id_setup_script = ""
    draft_id_capture_script = ""
    draft_id_output_script = ""
    if mode in {"draft", "open"}:
        draft_id_setup_script = standalone_draft_identity_setup_script()
        draft_id_capture_script = standalone_draft_identity_resolver_script()
        draft_id_output_script = """
        if savedDraftId is not "" then
            set outputText to outputText & "Draft ID: " & savedDraftId & return
            set outputText to outputText & "Draft ID Source: " & savedDraftIdSource & return
            set outputText to outputText & "Draft ID Scope: current Drafts locator; re-resolve after sync" & return
        end if
        """

    script = f'''
{forward_marker_handlers}
{_standalone_draft_identity_handlers() if mode in {"draft", "open"} else ""}
tell application "Mail"
    set outputText to "{header_text}" & return & return

    try
        set targetAccount to account "{safe_account}"
        {draft_id_setup_script}
        -- Try to get mailbox
        try
            set targetMailbox to mailbox "{safe_mailbox}" of targetAccount
        on error
            if "{safe_mailbox}" is "INBOX" then
                set targetMailbox to mailbox "Inbox" of targetAccount
            else
                error "Mailbox not found: {safe_mailbox}"
            end if
        end try

        {lookup_script}

        if foundMessage is missing value then
            return "{not_found_message}"
        end if

        set origSubject to subject of foundMessage
        set origSender to sender of foundMessage
        set origDate to ""
        try
            set origDate to (date received of foundMessage) as string
        end try
        -- Tri-state read: "" alone cannot distinguish a failed body read from a
        -- genuinely empty original, so track the read itself and fail closed
        -- only on failure (see _FORWARD_QUOTE_UNAVAILABLE_RETURN).
        set origContent to ""
        set origContentRead to false
        try
            set origContent to content of foundMessage
            set origContentRead to true
        end try
        if not origContentRead then
            {_FORWARD_QUOTE_UNAVAILABLE_RETURN}
        end if
        if (count of characters of origContent) > 4000 then
            set origContent to (text 1 thru 4000 of origContent) & return & "[... forwarded original truncated ...]"
        end if

        {fwd_read_script}

        -- Build forwarded body: optional lead message + forwarded header + quoted original
        set fwdHeader to "---------- Forwarded message ----------" & return
        set fwdHeader to fwdHeader & "From: " & origSender & return
        set fwdHeader to fwdHeader & "Subject: " & origSubject & return
        set fwdHeader to fwdHeader & "Date: " & origDate & return & return
        set fullBody to fwdLeadText & fwdHeader & origContent

        set fwdSubject to origSubject
        if fwdSubject does not start with "Fwd:" then set fwdSubject to "Fwd: " & fwdSubject
        {initial_forward_subject_script}

        -- Object-model draft: NO window, NO clipboard, NO System Events
        set forwardMessage to make new outgoing message with properties {{visible:{visible_lower}, subject:initialForwardSubject, content:fullBody}}

        {sender_script}
        {signature_script}

        -- Add recipients
        {to_script}

        -- Add CC/BCC recipients
        {cc_script}
        {bcc_script}

        -- Only explicit caller-selected paths are attached. A source
        -- message's attachments are never copied implicitly on forward.
        tell forwardMessage
            {attachment_script}
        end tell

        {restore_forward_subject_script}
        {post_forward_action}

        {draft_id_capture_script}
        {forward_marker_finalization_script}

        -- Clean up temp file
        {fwd_cleanup_script}

        set outputText to outputText & "{success_text}" & return
        set outputText to outputText & "To: {safe_to}" & return
        set outputText to outputText & "Subject: " & fwdSubject & return
        {draft_id_output_script}
        {forward_attachment_proof_output}
    '''

    if cc:
        script += f"""
        set outputText to outputText & "CC: {safe_cc}" & return
    """

    if bcc:
        script += f"""
        set outputText to outputText & "BCC: {safe_bcc}" & return
    """

    script += f"""
        return outputText
    on error errMsg
        try
            {fwd_cleanup_script}
        end try
        return "Error: " & errMsg
    end try
    end tell
    """

    try:
        result = (
            compose.run_applescript(script) if timeout is None else compose.run_applescript(script, timeout=timeout)
        )
        if mode in ("draft", "open") and success_text in result:
            draft_id = _extract_output_field(result, "Draft ID")
            attachment_proof = _extract_output_field(result, "Forward Attachment Proof") or ""
            forward_subject = _extract_output_field(result, "Subject")
            expected_signature = (
                False if not include_signature else (True if resolved_signature_name is not None else None)
            )
            try:
                verification = _verify_exact_saved_forward_draft(
                    account,
                    draft_id=draft_id,
                    to=to,
                    subject=forward_subject,
                    lead_message=message,
                    expected_signature=expected_signature,
                    attachment_paths=attachment_paths,
                    attachment_proof=attachment_proof,
                    timeout=timeout,
                )
            except Exception:
                return (
                    "Error: FORWARD_DRAFT_VERIFICATION_FAILED\n"
                    "Mail could not verify the exact saved forward Drafts message. No email was sent.\n"
                )
            if verification.startswith("Error:"):
                return verification
            result += verification
        return result
    except AppleScriptTimeout:
        return (
            f"Error: AppleScript timed out while forwarding email for account "
            f"{account!r}. Try again or pass a larger `timeout`."
        )
    except Exception as e:
        # This used to re-raise whenever `message` was falsy. `message` is the
        # optional lead-in text the caller writes above the forwarded mail, not
        # a marker of how far the forward got -- so the plain
        # `forward_email(message_id=..., to=...)` call, the common one, crashed
        # out as a transport exception while adding a lead-in got a readable
        # error. `compose_email` and `reply_to_email` both return here
        # unconditionally; this is the outlier, and nothing depended on it.
        return f"Error: Forward failed: {_clean_applescript_error(e)}"
    finally:
        if fwd_msg_temp_path:
            fwd_msg_path = Path(fwd_msg_temp_path)
            if fwd_msg_path.exists():
                fwd_msg_path.unlink()
