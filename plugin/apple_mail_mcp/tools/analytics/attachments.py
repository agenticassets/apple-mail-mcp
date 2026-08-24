"""``list_email_attachments`` tool and its JSON-row parser."""

import json
from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.applescript_snippets import sanitize_field_handler
from apple_mail_mcp.backend.base import target_selector_deprecated_error
from apple_mail_mcp.bounded_scan import MAX_WHOSE_IDS, build_whose_id_list, iter_id_chunks
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inbox_mailbox_script,
    inject_preferences,
    normalize_message_ids,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import analytics


def _parse_attachment_listing_rows(text: str) -> list[dict[str, Any]]:
    """Parse JSON-mode attachment rows emitted by AppleScript."""
    items: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("|||")
        if len(parts) != 7:
            continue
        message_id, subject, sender, received_date, attachment_index, filename, size_text = parts
        try:
            index_value = int(attachment_index)
        except ValueError:
            continue
        try:
            size_bytes: int | None = int(size_text)
        except ValueError:
            size_bytes = None
        items.append(
            {
                "message_id": message_id,
                "subject": subject,
                "sender": sender,
                "received_date": received_date,
                "attachment_index": index_value,
                "filename": filename,
                "size_bytes": size_bytes,
            }
        )
    return items


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Email Attachments")
@inject_preferences
def list_email_attachments(
    account: str | None = None,
    subject_keyword: str = "",
    message_ids: list[str] | None = None,
    max_results: int = 50,
    timeout: int | None = None,
    output_format: str = "text",
) -> str:
    """
    List attachments for exact message ids.

    ``subject_keyword`` is a deprecated selector retained for v3.x schema
    compatibility. Use ``search_emails(..., has_attachments=True)`` to discover
    candidate ids, then pass ``message_ids``. JSON mode returns attachment
    metadata with ``message_id`` and per-message ``attachment_index`` so
    ``save_email_attachment`` can select deterministically.

    Args:
        account: Account name (e.g., "Gmail", "Work", "Personal"). Falls back
            to ``DEFAULT_MAIL_ACCOUNT`` when None.
        subject_keyword: Deprecated schema-compat selector. Returns
            ``TARGET_SELECTOR_DEPRECATED`` when ``message_ids`` is omitted.
        message_ids: List of exact Mail message ids (required for targeting)
        max_results: Maximum number of messages to inspect from the inbox
            (default: 50). The AppleScript only enumerates this many messages.
        timeout: Optional AppleScript timeout in seconds. Defaults to the
            ``run_applescript`` baseline (120s).
        output_format: ``"text"`` (default) or ``"json"``.

    Returns:
        List of attachments with names, sizes, and exact selectors
    """

    if output_format not in {"text", "json"}:
        return "Error: Invalid output_format. Use: text, json"

    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return "Error: 'account' is required (no DEFAULT_MAIL_ACCOUNT configured)"

    if message_ids is None and not subject_keyword:
        return (
            "Error: message_ids is required (discover via search_emails(..., has_attachments=True) "
            "or list_email_attachments, then pass message_ids=[...])"
        )
    if message_ids is None and subject_keyword:
        return target_selector_deprecated_error(
            "list_email_attachments",
            ("subject_keyword",),
            preferred="Call search_emails(..., has_attachments=True) first, then pass message_ids=[...].",
            discovery="search_emails(subject_keyword=..., has_attachments=True, recent_days=..., limit=...)",
            exact_selector="message_ids",
        )

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = analytics.validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return account_err

    # Escape for AppleScript
    escaped_account = escape_applescript(account)
    sanitize_script = sanitize_field_handler()

    normalized_ids = normalize_message_ids(message_ids)
    if not normalized_ids:
        return "Error: 'message_ids' must contain one or more numeric Mail ids"
    if len(normalized_ids) > MAX_WHOSE_IDS:
        chunk_outputs = [
            list_email_attachments(
                account=account,
                message_ids=chunk,
                max_results=max_results,
                timeout=timeout,
                output_format=output_format,
            )
            for chunk in iter_id_chunks(normalized_ids)
        ]
        if output_format == "json":
            items: list[dict[str, Any]] = []
            for chunk_output in chunk_outputs:
                try:
                    payload = json.loads(chunk_output)
                except json.JSONDecodeError:
                    return chunk_output
                if isinstance(payload, dict) and payload.get("code"):
                    return chunk_output
                items.extend(payload.get("items", []))
            return json.dumps(
                {
                    "items": items,
                    "returned": len(items),
                    "message_ids": normalized_ids,
                    "selector": "message_ids",
                    "chunk_size": MAX_WHOSE_IDS,
                },
                indent=2,
            )
        return "\n\n".join(chunk_outputs)
    id_condition = build_whose_id_list(normalized_ids)
    header_label = f"message_ids: {', '.join(normalized_ids)}"
    message_lookup_script = f"set inboxMessages to every message of inboxMailbox whose {id_condition}"

    escaped_header = escape_applescript(header_label)

    if output_format == "json":
        script = f'''
    {sanitize_script}
    tell application "Mail"
        set outputText to ""
        set resultCount to 0

        try
            set targetAccount to account "{escaped_account}"
            {inbox_mailbox_script("inboxMailbox", "targetAccount")}
            {message_lookup_script}

            repeat with aMessage in inboxMessages
                if resultCount >= {max_results} then exit repeat

                try
                    set messageId to id of aMessage as string
                    set messageSubject to subject of aMessage
                    set messageSender to sender of aMessage
                    set messageDate to date received of aMessage
                    set msgAttachments to mail attachments of aMessage
                    set attachmentCount to count of msgAttachments

                    repeat with attachmentIndex from 1 to attachmentCount
                        set anAttachment to item attachmentIndex of msgAttachments
                        set attachmentName to name of anAttachment
                        set attachmentSizeText to ""
                        try
                            set attachmentSizeText to (file size of anAttachment as integer) as string
                        end try

                        set outputText to outputText & messageId & "|||" & my sanitize_field(messageSubject) & "|||" & my sanitize_field(messageSender) & "|||" & my sanitize_field(messageDate) & "|||" & (attachmentIndex as string) & "|||" & my sanitize_field(attachmentName) & "|||" & attachmentSizeText & return
                    end repeat

                    set resultCount to resultCount + 1
                end try
            end repeat

        on error errMsg
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    '''
    else:
        script = f'''
    tell application "Mail"
        set outputText to "ATTACHMENTS FOR: {escaped_header}" & return & return
        set resultCount to 0

        try
            set targetAccount to account "{escaped_account}"
            {inbox_mailbox_script("inboxMailbox", "targetAccount")}
            {message_lookup_script}

            repeat with aMessage in inboxMessages
                if resultCount >= {max_results} then exit repeat

                try
                    set messageSubject to subject of aMessage
                    set messageSender to sender of aMessage
                    set messageDate to date received of aMessage

                    set outputText to outputText & "✉ " & messageSubject & return
                    set outputText to outputText & "   From: " & messageSender & return
                    set outputText to outputText & "   Date: " & (messageDate as string) & return & return

                    -- Get attachments
                    set msgAttachments to mail attachments of aMessage
                    set attachmentCount to count of msgAttachments

                    if attachmentCount > 0 then
                        set outputText to outputText & "   Attachments (" & attachmentCount & "):" & return

                        repeat with anAttachment in msgAttachments
                            set attachmentName to name of anAttachment
                            try
                                set attachmentSize to file size of anAttachment
                                set sizeInKB to (attachmentSize / 1024) as integer
                                set outputText to outputText & "   📎 " & attachmentName & " (" & sizeInKB & " KB)" & return
                            on error
                                set outputText to outputText & "   📎 " & attachmentName & return
                            end try
                        end repeat
                    else
                        set outputText to outputText & "   No attachments" & return
                    end if

                    set outputText to outputText & return
                    set resultCount to resultCount + 1
                end try
            end repeat

            set outputText to outputText & "========================================" & return
            set outputText to outputText & "FOUND: " & resultCount & " matching email(s)" & return
            set outputText to outputText & "========================================" & return

        on error errMsg
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    '''

    try:
        result = analytics.run_applescript(script, timeout=timeout if timeout is not None else 120)
    except AppleScriptTimeout:
        return f"Error: AppleScript timed out while listing attachments for '{account}'"
    if output_format == "json":
        if result.startswith("Error:"):
            return result
        items = _parse_attachment_listing_rows(result)
        return json.dumps(
            {
                "items": items,
                "returned": len(items),
                "message_ids": normalized_ids,
                "selector": "message_ids",
            },
            indent=2,
        )
    return result
