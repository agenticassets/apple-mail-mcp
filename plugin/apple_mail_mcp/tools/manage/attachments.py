"""``save_email_attachment`` tool: size/disk probes, ambiguity guards, and the save AppleScript.

Patched names (``run_applescript``, ``validate_account_name``) are referenced via the
``manage`` facade so existing ``patch('...tools.manage.<name>')`` seams keep working."""

import shutil
from pathlib import Path

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error, target_selector_deprecated_error
from apple_mail_mcp.bounded_scan import build_whose_id_list
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inbox_mailbox_script,
    inject_preferences,
    normalize_message_ids,
    validate_save_path,
)
from apple_mail_mcp.server import WRITE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import manage
from apple_mail_mcp.tools.manage.helpers import _check_message_ids_cap


@mcp.tool(annotations=WRITE_TOOL_ANNOTATIONS, title="Save Attachment")
@inject_preferences
def save_email_attachment(
    account: str | None = None,
    subject_keyword: str = "",
    attachment_name: str = "",
    save_path: str = "",
    message_ids: list[str] | None = None,
    attachment_index: int | None = None,
    timeout: int | None = None,
    max_size_bytes: int = 100 * 1024 * 1024,
) -> str:
    """
    Save a specific attachment from an email to disk.

    ``subject_keyword`` is a deprecated selector retained for v3.x schema
    compatibility. Use ``search_emails(..., has_attachments=True)`` or
    ``list_email_attachments`` to discover candidate ids, then pass
    ``message_ids``. Passing ``subject_keyword`` without ``message_ids`` returns
    ``TARGET_SELECTOR_DEPRECATED``.

    When ``message_ids`` is provided, locates the message by exact ID.
    Prefer ``attachment_index`` from ``list_email_attachments(output_format="json")``
    for deterministic selection; ``attachment_name`` remains for compatibility and
    rejects ambiguous duplicate matches.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to DEFAULT_MAIL_ACCOUNT.
        subject_keyword: Deprecated schema-compat selector. Returns
            ``TARGET_SELECTOR_DEPRECATED`` when ``message_ids`` is omitted.
        attachment_name: Name of the attachment to save
        save_path: Full path where to save the attachment
        message_ids: List of exact Mail message ids (required for targeting)
        attachment_index: Optional 1-based attachment index from
            ``list_email_attachments(output_format="json")``. Requires exactly
            one ``message_id``.
        timeout: Optional AppleScript timeout in seconds (default: 120s).
        max_size_bytes: Maximum attachment size in bytes (default: 100 MB). Refuses to
            save attachments larger than this limit. Also checks that the target directory
            has at least ``max_size_bytes + 100 MB`` of free disk space before saving.
            Pass a larger value (e.g. ``max_size_bytes=500*1024*1024``) to raise the cap,
            or save manually via Mail UI for very large attachments.

    Returns:
        Confirmation message with save location
    """
    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return "Error: account is required (and no DEFAULT_MAIL_ACCOUNT configured)."
    if message_ids is None and subject_keyword:
        return target_selector_deprecated_error(
            "save_email_attachment",
            ("subject_keyword",),
            preferred="Call search_emails(..., has_attachments=True) first, then pass message_ids=[...].",
            discovery="search_emails(subject_keyword=..., has_attachments=True, recent_days=..., limit=...)",
            exact_selector="message_ids",
        )

    account_err = manage.validate_account_name(account, timeout=30 if timeout is None else min(timeout, 30))
    if account_err:
        return account_err

    if attachment_index is not None and attachment_index < 1:
        return "Error: attachment_index must be a positive 1-based integer."
    if message_ids is None:
        return (
            "Error: message_ids is required (discover via search_emails(..., has_attachments=True) "
            "or list_email_attachments, then pass message_ids=[...])"
        )
    if not (attachment_name or attachment_index) or not save_path:
        return "Error: attachment_name or attachment_index, and save_path are required."

    normalized_ids = normalize_message_ids(message_ids)
    if not normalized_ids:
        return "Error: 'message_ids' must contain one or more numeric Mail ids"
    cap_error = _check_message_ids_cap(normalized_ids, "save_email_attachment")
    if cap_error:
        return cap_error
    if attachment_index is not None and len(normalized_ids) != 1:
        err = ToolError(
            code="AMBIGUOUS_ATTACHMENT_SELECTOR",
            message="attachment_index requires exactly one message_id because indexes are per message.",
            remediation={
                "preferred": (
                    "Call list_email_attachments(message_ids=[...], output_format='json') and then "
                    "call save_email_attachment(message_ids=[one_id], attachment_index=N, ...)."
                ),
                "exact_selector": "message_ids + attachment_index",
            },
        )
        return serialize_tool_error(err)
    id_condition = build_whose_id_list(normalized_ids)
    message_filter_script = f"set inboxMessages to every message of inboxMailbox whose {id_condition}"
    not_found_detail = f"Message ids: {', '.join(normalized_ids)}"

    # Expand tilde in save_path (POSIX file in AppleScript does not expand ~)
    expanded_path = str(Path(save_path).expanduser())

    # Path validation: use shared helper for home-dir + sensitive-dir checks
    path_err = validate_save_path(
        expanded_path,
        path_label="Save path",
        sensitive_action="save attachments to",
    )
    if path_err:
        return path_err

    save_path_obj = Path(expanded_path).resolve()
    expanded_path = str(save_path_obj)
    save_dir = save_path_obj.parent

    # Escape for AppleScript
    escaped_account = escape_applescript(account)
    escaped_attachment = escape_applescript(attachment_name)
    escaped_path = escape_applescript(expanded_path)
    use_attachment_index = attachment_index is not None
    attachment_index_value = attachment_index if attachment_index is not None else 0
    attachment_selector_label = (
        f"Attachment index: {attachment_index}" if use_attachment_index else f"Attachment name: {escaped_attachment}"
    )

    # --- Attachment size probe ---
    # Run a cheap AppleScript to get the attachment file size before saving.
    # ``file size of anAttachment`` is available on macOS 10.15+. We wrap it in
    # a try block so that if the property is absent on an older OS the probe
    # returns -1 and we skip the cap (safe fail-open rather than blocking saves).
    _probe_message_lookup = f"every message of inboxMailbox whose {id_condition}"
    _escaped_att_probe = escape_applescript(attachment_name)
    _attachment_index_probe = attachment_index_value
    _probe_script = f'''
    tell application "Mail"
        try
            set targetAccount to account "{escaped_account}"
            {inbox_mailbox_script("inboxMailbox", "targetAccount")}
            set probeMessages to {_probe_message_lookup}
            set matchCount to 0
            set firstSize to -1
            repeat with aMessage in probeMessages
                set msgAttachments to mail attachments of aMessage
                set attachmentCount to count of msgAttachments
                repeat with attachmentIndex from 1 to attachmentCount
                    set anAttachment to item attachmentIndex of msgAttachments
                    set attachmentMatches to false
                    if {_attachment_index_probe} > 0 then
                        if attachmentIndex is {_attachment_index_probe} then set attachmentMatches to true
                    else if (name of anAttachment) contains "{_escaped_att_probe}" then
                        set attachmentMatches to true
                    end if
                    if attachmentMatches then
                        set matchCount to matchCount + 1
                        try
                            if firstSize is -1 then set firstSize to file size of anAttachment as integer
                        on error
                            if firstSize is -1 then set firstSize to -1
                        end try
                    end if
                end repeat
            end repeat
            return (matchCount as string) & "|||" & (firstSize as string)
        on error
            return "0|||-1"
        end try
    end tell
    '''
    _probe_timeout = 30 if timeout is None else min(timeout, 30)
    try:
        _probe_raw = manage.run_applescript(_probe_script, timeout=_probe_timeout).strip()
        if "|||" in _probe_raw:
            _match_count_text, _size_text = (_probe_raw.split("|||", 1) + ["-1"])[:2]
            _attachment_match_count = int(_match_count_text) if _match_count_text.isdigit() else 0
            _attachment_size = int(_size_text) if _size_text.lstrip("-").isdigit() else -1
        else:
            _attachment_size = int(_probe_raw) if _probe_raw.lstrip("-").isdigit() else -1
            _attachment_match_count = 1 if _attachment_size >= 0 else 0
    except (AppleScriptTimeout, ValueError, OSError):
        _attachment_size = -1
        _attachment_match_count = 0

    if not use_attachment_index and _attachment_match_count > 1:
        err = ToolError(
            code="AMBIGUOUS_ATTACHMENT_SELECTOR",
            message=(
                f"Attachment name '{attachment_name}' matched {_attachment_match_count} attachments. "
                "Filename substring selection is ambiguous."
            ),
            remediation={
                "preferred": (
                    "Call list_email_attachments(message_ids=[...], output_format='json') and retry with "
                    "attachment_index for the chosen row."
                ),
                "exact_selector": "message_ids + attachment_index",
                "matches": _attachment_match_count,
            },
        )
        return serialize_tool_error(err)

    if _attachment_size >= 0:
        if _attachment_size > max_size_bytes:
            err = ToolError(
                code="ATTACHMENT_TOO_LARGE",
                message=(
                    f"Attachment '{attachment_name or f'index {attachment_index}'}' is {_attachment_size:,} bytes "
                    f"({_attachment_size / (1024 * 1024):.1f} MB), which exceeds the "
                    f"cap of {max_size_bytes:,} bytes ({max_size_bytes / (1024 * 1024):.0f} MB)."
                ),
                remediation={
                    "preferred": (f"Pass max_size_bytes={_attachment_size + 1} to raise the cap for this attachment"),
                    "alternative": "Use Mail UI to save the attachment manually",
                    "actual_size_bytes": _attachment_size,
                    "cap_bytes": max_size_bytes,
                },
            )
            return serialize_tool_error(err)

        # Disk-space guard: require attachment_size + 100 MB buffer free
        _disk_buffer = 100 * 1024 * 1024
        _required_free = _attachment_size + _disk_buffer
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
            _free_bytes = shutil.disk_usage(save_dir).free
        except OSError:
            _free_bytes = None

        if _free_bytes is not None and _free_bytes < _required_free:
            err = ToolError(
                code="ATTACHMENT_TOO_LARGE",
                message=(
                    f"Insufficient disk space in '{save_dir}': "
                    f"{_free_bytes:,} bytes free, need at least "
                    f"{_required_free:,} bytes "
                    f"(attachment {_attachment_size:,} bytes + 100 MB buffer)."
                ),
                remediation={
                    "preferred": "Free up disk space and retry",
                    "alternative": "Use Mail UI to save the attachment manually",
                    "free_bytes": _free_bytes,
                    "required_bytes": _required_free,
                },
            )
            return serialize_tool_error(err)

    # ID lookup is exact; no subject-scan cap needed.
    script = f'''
    tell application "Mail"
        set outputText to ""

        try
            set targetAccount to account "{escaped_account}"
            {inbox_mailbox_script("inboxMailbox", "targetAccount")}
            {message_filter_script}
            set foundAttachment to false

            repeat with aMessage in inboxMessages
                try
                    set messageSubject to subject of aMessage
                    set msgAttachments to mail attachments of aMessage

                    set attachmentCount to count of msgAttachments
                    repeat with attachmentLoopIndex from 1 to attachmentCount
                        set anAttachment to item attachmentLoopIndex of msgAttachments
                        set attachmentFileName to name of anAttachment
                        set attachmentMatches to false
                        if {attachment_index_value} > 0 then
                            if attachmentLoopIndex is {attachment_index_value} then set attachmentMatches to true
                        else if attachmentFileName contains "{escaped_attachment}" then
                            set attachmentMatches to true
                        end if

                        if attachmentMatches then
                            -- Save the attachment
                            save anAttachment in POSIX file "{escaped_path}"

                            set outputText to "✓ Attachment saved successfully!" & return & return
                            set outputText to outputText & "Email: " & messageSubject & return
                            set outputText to outputText & "Attachment: " & attachmentFileName & return
                            set outputText to outputText & "Saved to: {escaped_path}" & return

                            set foundAttachment to true
                            exit repeat
                        end if
                    end repeat

                    if foundAttachment then exit repeat
                end try
            end repeat

            if not foundAttachment then
                set outputText to "⚠ Attachment not found" & return
                set outputText to outputText & "{not_found_detail}" & return
                set outputText to outputText & "{attachment_selector_label}" & return
            end if

        on error errMsg
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    '''

    try:
        result = manage.run_applescript(script, timeout=timeout if timeout is not None else 120)
    except AppleScriptTimeout:
        return (
            f"Error: AppleScript timed out while saving attachment from account "
            f"{account!r}. Try again or pass a larger `timeout`."
        )
    return result
