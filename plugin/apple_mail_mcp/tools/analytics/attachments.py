"""``list_email_attachments``, its AppleScript builders, and its row parser.

Pure parse/format helpers live in :mod:`attachments_helpers` and are re-exported
here so an existing ``patch('...analytics.attachments.<name>')`` seam still
fires.
"""

import json
from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.applescript_snippets import sanitize_field_handler
from apple_mail_mcp.backend.base import target_selector_deprecated_error
from apple_mail_mcp.bounded_scan import MAX_WHOSE_IDS, build_whose_id_list, iter_id_chunks
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    build_mailbox_ref,
    escape_applescript,
    inject_preferences,
    normalize_message_ids,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import analytics
from apple_mail_mcp.tools.analytics.attachments_helpers import (
    ERROR_MAILBOX_PREFIX,
    SEEN_MESSAGE_PREFIX,
    AttachmentScan,
    format_attachment_text,
    mailbox_error_texts,
    parse_attachment_output,
    unresolved_ids_message,
)

__all__ = [
    "ERROR_MAILBOX_PREFIX",
    "SEEN_MESSAGE_PREFIX",
    "AttachmentScan",
    "format_attachment_text",
    "list_email_attachments",
    "mailbox_error_texts",
    "parse_attachment_output",
    "unresolved_ids_message",
]


def _parse_attachment_listing_rows(text: str) -> list[dict[str, Any]]:
    """Items-only view of :func:`parse_attachment_output` (historical name)."""
    return parse_attachment_output(text).items


def _read_failure_row(escaped_mailbox: str) -> str:
    """AppleScript reporting messages that matched the id predicate but could not be read.

    Same reconciliation as ``search/records.py``'s ``_read_failure_row`` (pattern
    P1): the per-message ``try`` above swallows a failed read, which would
    otherwise leave the id in ``unresolved_message_ids`` as "not in this
    mailbox" when the truth is "found here and unreadable". Counting *scanned*
    rather than *matched* messages keeps the ``max_results`` cap from being
    reported as a failure.
    """
    return f"""
            if mailboxRead < mailboxScanned then
                set end of outputLines to "{ERROR_MAILBOX_PREFIX}{escaped_mailbox}|||read failed for " & ((mailboxScanned - mailboxRead) as string) & " of " & (mailboxScanned as string) & " matched message(s); results are incomplete"
            end if
"""


def _mailbox_scan_block(mailbox: str, message_ids: list[str], max_results: int) -> str:
    """Build the per-mailbox scan, guarded so one bad mailbox cannot abort the rest."""
    escaped_mailbox = escape_applescript(mailbox)
    id_condition = build_whose_id_list(message_ids)
    return f'''
        try
            {build_mailbox_ref(mailbox, var_name="attachMailbox")}
            set mailboxLabel to "{escaped_mailbox}"
            try
                set mailboxLabel to (name of attachMailbox) as string
            end try
            set mailboxField to my sanitize_field(mailboxLabel)
            set targetMessages to every message of attachMailbox whose {id_condition}
            set mailboxScanned to 0
            set mailboxRead to 0

            repeat with aMessage in targetMessages
                if resultCount >= {max_results} then exit repeat
                set mailboxScanned to mailboxScanned + 1

                try
                    set messageId to id of aMessage as string
                    set messageSubject to my sanitize_field(subject of aMessage)
                    set messageSender to my sanitize_field(sender of aMessage)
                    set messageDate to my sanitize_field(date received of aMessage)
                    set end of outputLines to "{SEEN_MESSAGE_PREFIX}" & mailboxField & "|||" & messageId & "|||" & messageSubject & "|||" & messageSender & "|||" & messageDate

                    set msgAttachments to mail attachments of aMessage
                    set attachmentCount to count of msgAttachments

                    repeat with attachmentIndex from 1 to attachmentCount
                        set anAttachment to item attachmentIndex of msgAttachments
                        set attachmentName to name of anAttachment
                        set attachmentSizeText to ""
                        try
                            set attachmentSizeText to (file size of anAttachment as integer) as string
                        end try

                        set end of outputLines to messageId & "|||" & messageSubject & "|||" & messageSender & "|||" & messageDate & "|||" & (attachmentIndex as string) & "|||" & my sanitize_field(attachmentName) & "|||" & attachmentSizeText & "|||" & mailboxField
                    end repeat

                    set resultCount to resultCount + 1
                    set mailboxRead to mailboxRead + 1
                end try
            end repeat
{_read_failure_row(escaped_mailbox)}
        on error errMsg
            set end of outputLines to "{ERROR_MAILBOX_PREFIX}{escaped_mailbox}|||" & errMsg
        end try
'''


def _build_attachment_listing_script(
    account: str,
    mailboxes: list[str],
    message_ids: list[str],
    max_results: int,
) -> str:
    """Build the whole listing script: one guarded block per requested mailbox."""
    blocks = "\n".join(_mailbox_scan_block(mailbox, message_ids, max_results) for mailbox in mailboxes)
    return f'''
    {sanitize_field_handler()}
    tell application "Mail"
        set outputLines to {{}}
        set resultCount to 0

        try
            set targetAccount to account "{escape_applescript(account)}"
        on error errMsg
            return "Error: " & errMsg
        end try
{blocks}
        set AppleScript's text item delimiters to linefeed
        set outputText to outputLines as string
        set AppleScript's text item delimiters to ""
        return outputText
    end tell
    '''


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Email Attachments")
@inject_preferences
def list_email_attachments(
    account: str | None = None,
    subject_keyword: str = "",
    message_ids: list[str] | None = None,
    max_results: int = 50,
    timeout: int | None = None,
    output_format: str = "text",
    mailbox: str = "INBOX",
    mailboxes: list[str] | None = None,
) -> str:
    """
    List attachments for exact message ids in one or more mailboxes.

    A message id is only found if the message lives in one of the searched
    mailboxes. The default searches the account's inbox only, so an id in
    ``Sent Items`` or an archive folder is reported in
    ``unresolved_message_ids`` (not silently omitted) unless that mailbox is
    named in ``mailbox`` / ``mailboxes``.

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
        max_results: Maximum number of messages to inspect per id chunk
            (default: 50). The AppleScript only enumerates this many messages.
        timeout: Optional AppleScript timeout in seconds. Defaults to the
            ``run_applescript`` baseline (120s).
        output_format: ``"text"`` (default) or ``"json"``.
        mailbox: Single mailbox to search (default: ``"INBOX"``, which resolves
            through the localized-inbox fallback list). Ignored when
            ``mailboxes`` is provided. ``"All"`` is not supported.
        mailboxes: Explicit mailbox list to search; takes precedence over
            ``mailbox``. Every listed mailbox is searched and the rows are
            accumulated, so a mailbox that fails to resolve or throws is
            reported in ``errors`` rather than aborting the call.

    Returns:
        Text mode: one block per resolved message (with its ``Mailbox:``),
        then a ``PARTIAL: ⚠`` line per mailbox failure and one naming the
        count of ids that were not found. JSON mode returns ``items`` (each
        carrying ``mailbox``), ``returned``, ``message_ids``, ``selector``,
        ``chunk_size``, ``account``, ``mailboxes_searched``,
        ``resolved_message_ids``, ``unresolved_message_ids``, and ``complete``
        (True only when nothing is unresolved and no mailbox failed).
        ``errors`` is present only when there is something to report.
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

    single_mailbox = mailbox.strip() or "INBOX"
    if mailboxes is not None:
        mailboxes = [mb.strip() for mb in mailboxes if mb and mb.strip()]
        if not mailboxes:
            return "Error: mailboxes must contain at least one mailbox name"
        if any(mb.lower() == "all" for mb in mailboxes):
            return 'Error: mailboxes does not accept "All"; name each mailbox to search explicitly'
    elif single_mailbox.lower() == "all":
        return 'Error: mailbox does not accept "All"; pass mailboxes=[...] naming each mailbox to search'

    search_mailboxes = mailboxes or [single_mailbox]

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = analytics.validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return account_err

    normalized_ids = normalize_message_ids(message_ids)
    if not normalized_ids:
        return "Error: 'message_ids' must contain one or more numeric Mail ids"

    scan = AttachmentScan()
    for chunk in iter_id_chunks(normalized_ids):
        script = _build_attachment_listing_script(
            account,
            search_mailboxes,
            chunk,
            max_results,
        )
        try:
            result = analytics.run_applescript(script, timeout=timeout if timeout is not None else 120)
        except AppleScriptTimeout:
            return f"Error: AppleScript timed out while listing attachments for '{account}'"
        if result.startswith("Error:"):
            return result
        scan.merge(parse_attachment_output(result, default_mailbox=search_mailboxes[0]))

    resolved_ids = [mid for mid in normalized_ids if mid in scan.seen]
    unresolved_ids = [mid for mid in normalized_ids if mid not in scan.seen]
    error_texts = mailbox_error_texts(scan.mailbox_errors)
    if unresolved_ids:
        error_texts.append(unresolved_ids_message(unresolved_ids, normalized_ids, search_mailboxes))

    if output_format == "json":
        payload: dict[str, Any] = {
            "items": scan.items,
            "returned": len(scan.items),
            "message_ids": normalized_ids,
            "selector": "message_ids",
            "chunk_size": MAX_WHOSE_IDS,
            "account": account,
            "mailboxes_searched": search_mailboxes,
            "resolved_message_ids": resolved_ids,
            "unresolved_message_ids": unresolved_ids,
            "complete": not unresolved_ids and not scan.mailbox_errors,
        }
        if error_texts:
            payload["errors"] = error_texts
        return json.dumps(payload, indent=2)

    return format_attachment_text(
        f"message_ids: {', '.join(normalized_ids)}",
        scan,
        normalized_ids,
        search_mailboxes,
        error_texts,
    )
