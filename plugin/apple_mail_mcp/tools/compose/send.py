"""``compose_email`` tool and its ``_send_html_email`` NSPasteboard HTML helper."""

import html
import uuid
from pathlib import Path

from apple_mail_mcp.core import AppleScriptTimeout, escape_applescript, inject_preferences
from apple_mail_mcp.core.reply_state import drafts_mailbox_block
from apple_mail_mcp.server import DESTRUCTIVE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import compose
from apple_mail_mcp.tools.compose.attachment_draft_verification import (
    marker_draft_proof_call,
    marker_draft_verification_handlers,
    verify_standalone_attachment_readiness,
)
from apple_mail_mcp.tools.compose.clipboard_scripts import (
    pasteboard_restore_script,
    pasteboard_snapshot_script,
)
from apple_mail_mcp.tools.compose.helpers import (
    _check_open_compose_window_cap,
    _clean_applescript_error,
    _resolve_account,
    _resolve_signature_name,
    _validate_from_address,
)
from apple_mail_mcp.tools.compose.html_focus_scripts import html_compose_focus_handler
from apple_mail_mcp.tools.compose.html_subject_scripts import (
    html_compose_error_handler_script,
    html_compose_post_paste_script,
    run_html_compose_subject_followup,
)
from apple_mail_mcp.tools.compose.lookup_scripts import _compose_signature_script
from apple_mail_mcp.tools.compose.payload import (
    _build_recipient_loops,
    _compose_sender_script,
    _split_addresses,
    _standalone_compose_thread_warning,
    _strip_cdata_wrappers,
)
from apple_mail_mcp.tools.compose.standalone_draft_identity_scripts import (
    HTML_SUBJECT_MARKER_PREFIX,
    _standalone_draft_identity_handlers,
    standalone_draft_identity_resolver_script,
    standalone_draft_identity_setup_script,
    standalone_marker_draft_finalize_script,
)


def _send_html_email(
    account: str,
    to: str,
    subject: str,
    body_plain: str,
    body_html: str,
    cc: str | None = None,
    bcc: str | None = None,
    attachments_script: str = "",
    mode: str = "send",
    sender_override: str | None = None,
    timeout: int | None = None,
    signature_name: str | None = None,
    attachment_paths: list[str] | None = None,
) -> str:
    """Compose HTML through a focused NSPasteboard-backed Mail editor."""
    safe_account = escape_applescript(account)
    temporary_subject_marker = f"{HTML_SUBJECT_MARKER_PREFIX}{uuid.uuid4().hex}__"
    # Build recipient scripts
    to_lines = ""
    for addr in _split_addresses(to):
        to_lines += (
            f'make new to recipient at end of to recipients with properties {{address:"{escape_applescript(addr)}"}}\n'
        )

    cc_lines, bcc_lines, _, _ = _build_recipient_loops(cc, bcc, compact=True)

    sender_script = _compose_sender_script("newMsg", f'account "{safe_account}"', sender_override)
    signature_script = _compose_signature_script("newMsg", signature_name)
    draft_id_capture_script = ""
    draft_id_output_script = ""
    marker_draft_proof_handlers = ""
    identity_handlers = ""
    draft_attachment_paths: list[str] = attachment_paths if attachment_paths and mode in {"draft", "open"} else []
    attachment_draft = bool(draft_attachment_paths)
    if attachment_draft:
        marker_draft_proof_handlers = marker_draft_verification_handlers()
        identity_handlers = _standalone_draft_identity_handlers()
        marker_proof_script = marker_draft_proof_call(
            to_addresses=_split_addresses(to),
            cc_addresses=_split_addresses(cc),
            bcc_addresses=_split_addresses(bcc),
            subject=subject,
            marker=temporary_subject_marker,
            body=body_plain,
            attachment_names=[Path(path).name for path in draft_attachment_paths],
        )
        draft_id_capture_script = standalone_marker_draft_finalize_script(subject, marker_proof_script)
        draft_id_output_script = """ & return & "Draft ID: " & savedDraftId & return & "Draft ID Source: " & savedDraftIdSource & return & "Attachment Transaction Proof: " & attachmentTransactionProof"""

    drafts_setup = standalone_draft_identity_setup_script() if attachment_draft else drafts_mailbox_block()
    post_paste_script = html_compose_post_paste_script(
        mode=mode,
        subject=subject,
        marker=temporary_subject_marker,
        attachment_finalize_script=draft_id_capture_script,
    )
    if mode == "send":
        success_text = "Email sent successfully (HTML)"
    elif mode == "draft":
        success_text = "Email saved as draft (HTML)"
    else:
        success_text = "Email opened in Mail for review (HTML). Edit and send when ready."

    # Write HTML to temp file so the AppleScript can read it without
    # worrying about escaping quotes/special chars in the HTML string.
    with compose.tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".html",
        prefix="mail_html_",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp.write(body_html)
        html_temp_path = tmp.name

    error_handler = html_compose_error_handler_script(
        marker=temporary_subject_marker,
        subject=subject,
        html_temp_path=html_temp_path,
        mode=mode,
    )
    script = f'''
use framework "Foundation"
use framework "AppKit"
use scripting additions
{marker_draft_proof_handlers}
{identity_handlers}

-- Step 1: Read HTML from temp file and place on clipboard
set htmlString to do shell script "cat " & quoted form of "{html_temp_path}"
set pb to current application's NSPasteboard's generalPasteboard()
set temporarySubjectMarker to "{temporary_subject_marker}"

-- Save the whole clipboard for restoration: every item, every flavor. Reading
-- only the text flavor silently discarded a copied image or file.
{pasteboard_snapshot_script()}
pb's clearContents()
set htmlData to (current application's NSString's stringWithString:htmlString)'s dataUsingEncoding:(current application's NSUTF8StringEncoding)
pb's setData:htmlData forType:(current application's NSPasteboardTypeHTML)

-- Step 2: Create compose window (empty body so signature doesn't interfere)
tell application "Mail"
    set targetAccount to account "{safe_account}"
    {drafts_setup}
    set newMsg to make new outgoing message with properties {{subject:"{temporary_subject_marker}", content:"", visible:true}}
    {sender_script}
    {signature_script}
    tell newMsg
        {to_lines}
        {cc_lines}
        {bcc_lines}
        {attachments_script}
    end tell
    -- Bring the correct compose window to the front so the paste lands here.
    try
        set index of (window of newMsg) to 1
    end try
    activate
end tell

-- Step 3: Wait for compose window to render
delay 2.5

-- Step 4: Focus the compose body, then paste. Never Tab into a WebKit
-- body: extra tabs become first-line indent once the caret is already there.
{html_compose_focus_handler()}

try
    tell application "System Events"
        set frontmost of process "Mail" to true
        delay 0.5
        tell process "Mail"
            if not my focusComposeBody(temporarySubjectMarker) then error "COMPOSE_BODY_FOCUS_FAILED"

            -- Paste HTML without Cmd+A so Mail's native signature remains intact.
            keystroke "v" using command down
            delay 0.5

            {post_paste_script}
        end tell
    end tell

    -- Step 5: Clean up temp file
    do shell script "rm -f " & quoted form of "{html_temp_path}"

    -- Step 6: Restore clipboard
    {pasteboard_restore_script()}

    return "{success_text}"{draft_id_output_script}
on error errMsg
{error_handler}
end try
'''

    try:
        output = compose.run_applescript(script, timeout=timeout if timeout is not None else 120)
        # Build confirmation message
        confirm = f"{output}\n\nFrom: {account}\nTo: {to}\nSubject: {subject}"
        if cc:
            confirm += f"\nCC: {cc}"
        if bcc:
            confirm += f"\nBCC: {bcc}"
        if attachment_draft:
            verification = verify_standalone_attachment_readiness(
                output=output,
                account=account,
                to=to,
                cc=cc or "",
                bcc=bcc or "",
                subject=subject,
                body=body_plain,
                attachment_paths=draft_attachment_paths,
                timeout=timeout,
                verify_draft=compose.verify_draft,
            )
            if verification.startswith("Error:"):
                return verification
            confirm += "\n" + verification
        return confirm
    except Exception as exc:
        original_error = (
            "HTML email script timed out" if isinstance(exc, AppleScriptTimeout) else _clean_applescript_error(exc)
        )
        return run_html_compose_subject_followup(
            account=account,
            marker=temporary_subject_marker,
            final_subject=subject,
            original_error=original_error,
            timeout=timeout,
            mode=mode,
            to=to,
        )
    finally:
        temp_path = Path(html_temp_path)
        if temp_path.exists():
            temp_path.unlink()


@mcp.tool(annotations=DESTRUCTIVE_TOOL_ANNOTATIONS, title="Compose Email")
@inject_preferences
def compose_email(
    account: str | None = None,
    to: str = "",
    subject: str = "",
    body: str = "",
    cc: str | None = None,
    bcc: str | None = None,
    attachments: str | None = None,
    mode: str = "draft",
    body_html: str | None = None,
    from_address: str | None = None,
    timeout: int | None = None,
    include_signature: bool = True,
    signature_name: str | None = None,
    standalone_confirmed: bool = False,
) -> str:
    """
    Compose a new standalone email from a specific account.

    This tool never includes the original email thread. Use ``reply_to_email``
    or ``forward_email`` with ``message_id`` when responding to existing mail.

    Bare ``https://`` URLs on their own line in HTML compose may become Mail
    link-preview cards in the open window; this tool does not create or verify
    those cards.

    Args:
        account: Account name to send from (e.g., "Gmail", "Work", "Personal"). Defaults to `DEFAULT_MAIL_ACCOUNT` env var if `account` is omitted.
        to: Recipient email address(es), comma-separated for multiple
        subject: Email subject line
        body: Email body text (used as plain-text fallback when body_html is provided)
        cc: Optional CC recipients, comma-separated for multiple
        bcc: Optional BCC recipients, comma-separated for multiple
        attachments: Optional file paths to attach, comma-separated for multiple (e.g., "/path/to/file1.png,/path/to/file2.pdf")
        mode: Delivery mode — "draft" (default, save quietly to Drafts), "open" (save first, then leave compose window open for review), or "send" (send immediately)
        body_html: Optional HTML body for rich formatting (bold, headings, links, colors). When provided, the email is sent as HTML. The plain 'body' field is still required as fallback text.
        from_address: Optional sender address to use for this message. Must be one of the account's configured email addresses. When omitted, Mail uses the account's default "Send new messages from" setting.
        timeout: Optional per-AppleScript timeout in seconds. Defaults to the standard 120s. Raise this when working with large mailboxes or slow accounts.
        include_signature: Whether to apply the configured/default Mail signature (default: True).
        signature_name: Optional Mail signature name; falls back to DEFAULT_MAIL_SIGNATURE when omitted.
        standalone_confirmed: Required explicit override when the subject/body looks like a reply or forward but the caller intentionally wants a new standalone message.

    Returns:
        Confirmation message with details of the email
    """

    # Validate mode
    if mode not in ("send", "draft", "open"):
        return f"Error: Invalid mode '{mode}'. Use: send, draft, open"
    blocked = compose._send_blocked(mode)
    if blocked:
        return blocked
    if attachments and mode == "send":
        return (
            "Error: ATTACHMENT_DRAFT_VERIFICATION_REQUIRED\n"
            "Attachment-bearing emails must be saved and exactly verified before human review and sending. "
            "Use mode='draft' or mode='open'."
        )

    if mode == "open":
        cap_err = _check_open_compose_window_cap()
        if cap_err:
            return cap_err

    account, account_error = _resolve_account(account, timeout=timeout)
    if account_error:
        return account_error
    assert account is not None  # _resolve_account guarantees non-None when error is None
    if not to:
        return "Error: 'to' is required"

    body = _strip_cdata_wrappers(body) or ""
    body_html = _strip_cdata_wrappers(body_html)

    thread_warning = _standalone_compose_thread_warning(subject, body, body_html, standalone_confirmed)
    if thread_warning:
        return thread_warning

    # Validate optional sender override
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

    # Validate and resolve attachments early
    attachment_script = ""
    attachment_info = ""
    validated_paths: list[str] = []
    if attachments:
        validated_paths, error = compose._validate_attachment_paths(attachments)
        if error:
            return error
        for path in validated_paths:
            safe_path = escape_applescript(path)
            attachment_script += f'''
                set theFile to POSIX file "{safe_path}"
                make new attachment with properties {{file name:theFile}} at after the last paragraph
                delay 1
            '''
            attachment_info += f"  {path}\n"

    # Attachment-bearing iCloud drafts can lose authored content when Mail's
    # object model assigns ``content`` before saving. Use the compose-window
    # writer for every attachment draft/open message, including plain text.
    # Attachment sends are rejected above, so this never expands send behavior.
    attachment_draft = bool(validated_paths) and mode in {"draft", "open"}
    if body_html or attachment_draft:
        pasted_html = body_html or html.escape(body).replace("\n", "<br>\n")
        return _send_html_email(
            account=account,
            to=to,
            subject=subject,
            body_plain=body,
            body_html=pasted_html,
            cc=cc,
            bcc=bcc,
            attachments_script=attachment_script,
            mode=mode,
            sender_override=sender_override,
            timeout=timeout,
            signature_name=resolved_signature_name,
            attachment_paths=validated_paths,
        )

    # --- Plain-text path: existing AppleScript approach ---
    safe_account = escape_applescript(account)
    escaped_subject = escape_applescript(subject)
    escaped_body = escape_applescript(body)

    # Build TO recipients (split comma-separated addresses)
    to_script = ""
    for addr in _split_addresses(to):
        safe_addr = escape_applescript(addr)
        to_script += f'''
                make new to recipient at end of to recipients with properties {{address:"{safe_addr}"}}
        '''

    cc_script, bcc_script, _, _ = _build_recipient_loops(
        cc,
        bcc,
        indent="                ",
        trailing_indent="            ",
    )

    safe_to = escape_applescript(to)
    safe_cc = escape_applescript(cc) if cc else ""
    safe_bcc = escape_applescript(bcc) if bcc else ""
    safe_attachment_info = escape_applescript(attachment_info) if attachment_info else ""

    sender_script = _compose_sender_script("newMessage", "targetAccount", sender_override)
    signature_script = _compose_signature_script("newMessage", resolved_signature_name)

    # Determine behavior per mode
    if mode == "send":
        header_text = "COMPOSING EMAIL"
        visible = "false"
        send_command = "send newMessage"
        success_text = "✓ Email sent successfully!"
    elif mode == "open":
        header_text = "OPENING EMAIL FOR REVIEW"
        visible = "true"
        send_command = "save newMessage\n            activate"
        success_text = "✓ Email opened in Mail for review. Edit and send when ready."
    else:  # draft
        header_text = "SAVING EMAIL AS DRAFT"
        visible = "false"
        send_command = "save newMessage"
        success_text = "✓ Email saved as draft!"

    draft_id_capture_script = ""
    draft_id_output_script = ""
    if attachment_draft:
        draft_id_capture_script = standalone_draft_identity_resolver_script()
        draft_id_output_script = """
            set outputText to outputText & "Draft ID: " & savedDraftId & return
            set outputText to outputText & "Draft ID Source: " & savedDraftIdSource & return
        """

    script = f'''
    {_standalone_draft_identity_handlers() if attachment_draft else ""}
    tell application "Mail"
        set outputText to "{header_text}" & return & return

        try
            set targetAccount to account "{safe_account}"
            {standalone_draft_identity_setup_script() if attachment_draft else ""}

            -- Start with an empty standalone message. On Exchange, assigning
            -- authored content before Mail applies a native signature can make
            -- the authored portion render as quoted text in synced clients.
            set newMessage to make new outgoing message with properties {{subject:"{escaped_subject}", content:"", visible:{visible}}}

            {sender_script}
            {signature_script}

            -- Mail applies an account's automatic signature asynchronously.
            -- Wait a bounded second for it before prefixing the authored body,
            -- so a late signature cannot overwrite the user's content.
            set signatureContent to ""
            repeat with signatureAttempt from 1 to 5
                try
                    set signatureContent to content of newMessage as string
                end try
                if signatureContent is not "" then exit repeat
                if signatureAttempt is less than 5 then delay 0.2
            end repeat

            -- Prefix authored text so the native signature remains a separate
            -- trailing block rather than reclassifying the body as a quote.
            set content of newMessage to "{escaped_body}" & return & return & signatureContent

            -- Add TO/CC/BCC recipients
            tell newMessage
                {to_script}
                {cc_script}
                {bcc_script}
            end tell

            -- Add attachments
            tell newMessage
                {attachment_script}
            end tell

            -- Send, save as draft, or leave open for review
            {send_command}

            {draft_id_capture_script}

            set outputText to outputText & "{success_text}" & return
            set outputText to outputText & "To: {safe_to}" & return
            set outputText to outputText & "Subject: {escaped_subject}" & return
            {draft_id_output_script}
    '''

    if cc:
        script += f"""
            set outputText to outputText & "CC: {safe_cc}" & return
    """

    if bcc:
        script += f"""
            set outputText to outputText & "BCC: {safe_bcc}" & return
    """

    if attachments:
        script += f'''
            set outputText to outputText & "Attachments:" & return & "{safe_attachment_info}" & return
    '''

    script += """

        on error errMsg
            return "Error: " & errMsg & return & "Please check that the account name and email addresses are correct."
        end try

        return outputText
    end tell
    """

    try:
        result = (
            compose.run_applescript(script) if timeout is None else compose.run_applescript(script, timeout=timeout)
        )
    except AppleScriptTimeout:
        return (
            f"Error: AppleScript timed out while composing email for account "
            f"{account!r}. Try again or pass a larger `timeout`."
        )
    return result
