"""``export_emails`` tool: single-email, message-id, and entire-mailbox exports."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import target_selector_deprecated_error
from apple_mail_mcp.bounded_scan import compute_scan_upper_bound
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inject_preferences,
    normalize_message_ids,
    validate_save_path,
)
from apple_mail_mcp.server import WRITE_TOOL_ANNOTATIONS, mcp

_EXPORT_MAX_EMAILS_CAP = 50
_ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024
_ATTACHMENT_MAX_TOTAL_BYTES = 100 * 1024 * 1024


@mcp.tool(annotations=WRITE_TOOL_ANNOTATIONS, title="Export Emails")
@inject_preferences
def export_emails(
    account: str | None = None,
    scope: str = "entire_mailbox",
    subject_keyword: str | None = None,
    message_id: str | None = None,
    message_ids: list[str] | None = None,
    mailbox: str = "INBOX",
    save_directory: str = "~/Desktop",
    format: str = "txt",
    max_emails: int | None = None,
    offset: int = 0,
    sort: str = "newest_first",
    sender_exact: str | None = None,
    sender_domain: str | None = None,
    email_address: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    recent_days: float = 2.0,
    include_sent: bool = True,
    include_attachments: bool = False,
    timeout: int | None = None,
) -> str:
    """
    Export emails to files for backup or analysis, in small bounded batches.

    ``max_emails`` is hard-capped at 50 across every scope so a single call
    can never trigger inbox-wide Mail.app work. For ``entire_mailbox``
    exports, the AppleScript binds only the requested page window
    (``messages pageStart thru pageEnd``, sized by ``offset``/``max_emails``)
    so the full message list of a large Exchange/Gmail mailbox is never
    materialized. Page with ``offset`` or narrow with filters to export more
    than 50 messages.

    **Exchange / Gmail cold-cache warning:** ``entire_mailbox`` reads
    ``content of aMessage`` for every exported message. On an Exchange account
    that has not recently synced, each body read can take 1-3 seconds. For
    larger metadata-only walks, page with ``offset``/``max_emails`` across
    several bounded calls instead of one large sweep.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Falls back to
            ``DEFAULT_MAIL_ACCOUNT`` when None.
        scope: Export scope: "single_email" (requires ``message_id`` or
            ``message_ids``; ``subject_keyword`` path returns
            ``TARGET_SELECTOR_DEPRECATED``), "filtered", "correspondent",
            "thread", or "entire_mailbox"
        subject_keyword: Deprecated schema-compat selector for single_email scope.
            Returns ``TARGET_SELECTOR_DEPRECATED`` when ``message_id`` is omitted.
        message_id: Exact numeric Apple Mail message id for single_email scope
        message_ids: Optional list of exact Apple Mail message ids to export
        mailbox: Mailbox to export from (default: "INBOX")
        save_directory: Directory to save exports (default: "~/Desktop")
        format: Export format: "txt", "html", or raw RFC 822 "eml"
            (default: "txt"). EML preserves Mail's source headers and MIME
            structure for import into other mail clients.
        max_emails: Maximum number of emails to export. Defaults to 25;
            hard-capped at 50 for every scope (export runs in small bounded
            batches by design). Page with ``offset`` or narrow with filters
            to export more; full-mailbox scans are disabled, so large
            metadata-only walks must page across several bounded calls.
        offset: Pagination offset for bounded filtered or entire_mailbox exports.
        sort: Deterministic mailbox-page ordering. "newest_first" (or the
            compatibility alias "date_desc") orders an entire_mailbox page by
            received date descending, then numeric Mail message id.
        sender_exact: Exact sender-address discovery filter for filtered scope.
        sender_domain: Sender-domain discovery filter for filtered scope.
        email_address: Address to match across sender and recipient fields for
            correspondent scope. Includes received and sent messages by default.
        date_from: Optional YYYY-MM-DD lower date bound.
        date_to: Optional YYYY-MM-DD upper date bound.
        recent_days: Bounded discovery window for filtered and thread exports.
        include_sent: Include Sent in thread exports when true.
        include_attachments: For EML exports, also save each message's
            attachments to ``{index}_{subject}/attachments/`` beside
            ``message.eml``. Each attachment is capped at 25 MiB and the
            each bounded Mail export batch at 100 MiB; larger files are
            skipped and reported.
            Attachment reads can be slow on cold Exchange/Gmail caches; use a
            small page and increase ``timeout`` from its 120-second default if
            Mail needs longer to download files.
        timeout: Optional AppleScript timeout in seconds. Defaults to 120s.

    Returns:
        Confirmation message with export location
    """

    from apple_mail_mcp.tools import analytics
    from apple_mail_mcp.tools.analytics.export_formatting import (
        attachment_bundle_save_block,
        attachment_bundle_setup_block,
        export_content_block,
        mailbox_lookup_block,
        normalize_export_format,
        sanitize_delimiter_block,
    )
    from apple_mail_mcp.tools.analytics.export_helpers import (
        build_correspondent_export_script,
        build_entire_mailbox_export_script,
        message_ids_by_mailbox,
        run_message_id_export,
        run_multi_mailbox_id_export,
        unbounded_export_error,
    )

    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return "Error: 'account' is required (no DEFAULT_MAIL_ACCOUNT configured)"

    try:
        normalized_format = normalize_export_format(format)
    except ValueError as exc:
        return f"Error: {exc}"
    if offset < 0:
        return "Error: offset must be >= 0"
    if max_emails is not None and max_emails <= 0:
        return "Error: max_emails must be > 0"
    if max_emails is not None and max_emails > _EXPORT_MAX_EMAILS_CAP:
        return (
            f"Error: max_emails must be between 1 and {_EXPORT_MAX_EMAILS_CAP}. Export runs in small "
            "bounded batches; page with offset or narrow with filters to export more."
        )
    if sort not in {"newest_first", "date_desc"}:
        return "Error: Invalid sort. Use: newest_first or date_desc"
    if include_attachments and normalized_format != "eml":
        return "Error: include_attachments requires format='eml'"

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = analytics.validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return account_err

    path_err = validate_save_path(save_directory)
    if path_err:
        return path_err

    # Every scope shares the same default and is hard-capped at
    # _EXPORT_MAX_EMAILS_CAP (see validation above), so there is no longer a
    # separate large-export performance-warning path.
    if max_emails is None:
        max_emails = 25

    save_dir = str(Path(save_directory).expanduser().resolve())

    # Escape all user inputs for AppleScript
    safe_account = escape_applescript(account)
    safe_format = escape_applescript(normalized_format)
    safe_save_dir = escape_applescript(save_dir)

    if message_ids is not None:
        if len(message_ids) > _EXPORT_MAX_EMAILS_CAP:
            return f"Error: message_ids is limited to {_EXPORT_MAX_EMAILS_CAP} per call; split into smaller batches."
        return run_message_id_export(
            account=account,
            safe_account=safe_account,
            safe_format=safe_format,
            safe_save_dir=safe_save_dir,
            ids_by_mailbox={mailbox: message_ids},
            include_attachments=include_attachments,
            max_attachment_bytes=_ATTACHMENT_MAX_BYTES,
            max_total_attachment_bytes=_ATTACHMENT_MAX_TOTAL_BYTES,
            timeout=timeout,
            runner=analytics.run_applescript,
        )

    if scope == "single_email":
        if not message_id and not subject_keyword:
            return (
                "Error: message_id is required for single_email scope "
                "(discover via search_emails(...) or list_inbox_emails(...), then pass message_id)"
            )
        if not message_id and subject_keyword:
            return target_selector_deprecated_error(
                "export_emails",
                ("subject_keyword",),
                preferred="Call search_emails(...) first, then pass message_id for scope='single_email'.",
                discovery="search_emails(subject_keyword=..., recent_days=..., limit=...)",
                exact_selector="message_id",
            )

        normalized_ids = normalize_message_ids([message_id])
        if not normalized_ids:
            return "Error: message_id must be a numeric Apple Mail message id"
        target_message_id = normalized_ids[0]
        safe_not_found_label = escape_applescript(f"message_id={target_message_id}")

        script = f'''
        tell application "Mail"
            set outputText to "EXPORTING EMAIL" & return & return

            try
                set targetAccount to account "{safe_account}"
                -- Try to get mailbox
                {mailbox_lookup_block(mailbox)}

                -- Export by exact Mail message id (no subject scan).
                set matchedMessages to (every message of targetMailbox whose id is {target_message_id})
                set foundMessage to missing value
                if (count of matchedMessages) > 0 then
                    set foundMessage to item 1 of matchedMessages
                end if

                if foundMessage is not missing value then
                    set aMessage to foundMessage
                    set messageSubject to subject of foundMessage
                    set messageSender to sender of foundMessage
                    set messageDate to date received of foundMessage
                    set messageContent to content of foundMessage

                    -- Create safe filename
                    set safeSubject to messageSubject
                    {sanitize_delimiter_block("safeSubject")}

                    set exportCount to 1
                    set exportAttachmentBytes to 0
                    set exportIdText to "{target_message_id}"

                    -- Write into a per-scope export subdirectory with an index
                    -- and message-id prefix, exactly like every other scope. A
                    -- bare "<subject>.<format>" in save_directory used to be
                    -- opened with `set eof to 0`, truncating any pre-existing
                    -- user file whose name happened to match the subject.
                    set exportDir to "{safe_save_dir}/single_email_export"
                    do shell script "mkdir -p " & quoted form of exportDir

                    set fileName to (exportCount as string) & "_" & exportIdText & "_" & safeSubject & ".{safe_format}"
                    set filePath to exportDir & "/" & fileName
                    {attachment_bundle_setup_block(include_attachments=include_attachments)}

                    -- Prepare export content. EML uses raw Mail source so
                    -- RFC 822 headers and MIME structure survive export.
                    {export_content_block(safe_format)}

                    -- Write to file
                    set fileRef to open for access POSIX file filePath with write permission
                    set eof of fileRef to 0
                    write exportContent to fileRef as «class utf8»
                    close access fileRef
                    {attachment_bundle_save_block(include_attachments=include_attachments, max_attachment_bytes=_ATTACHMENT_MAX_BYTES, max_total_attachment_bytes=_ATTACHMENT_MAX_TOTAL_BYTES)}

                    set outputText to outputText & "✓ Email exported successfully!" & return & return
                    set outputText to outputText & "Subject: " & messageSubject & return
                    set outputText to outputText & "Saved to: " & filePath & return

                else
                    set outputText to outputText & "⚠ No email found matching: {safe_not_found_label}" & return
                end if

            on error errMsg
                try
                    close access file filePath
                end try
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    elif scope == "filtered":
        if not any((sender_exact, sender_domain, date_from, date_to)):
            return "Error: scope='filtered' requires sender_exact, sender_domain, date_from, or date_to"
        if not date_from and recent_days <= 0:
            return unbounded_export_error(account)

        from apple_mail_mcp.tools.search import _search_mail_records_sync

        try:
            records = _search_mail_records_sync(
                account=account,
                mailbox=mailbox,
                sender_exact=sender_exact,
                sender_domain=sender_domain,
                date_from=date_from,
                date_to=date_to,
                include_content=False,
                content_length=0,
                offset=offset,
                limit=max_emails,
                timeout=timeout,
                recent_days=recent_days,
                date_from_explicit=date_from is not None,
            )
        except AppleScriptTimeout:
            return f"Error: AppleScript timed out while discovering filtered emails for '{account}'"
        except ValueError as exc:
            return f"Error: {exc}"
        if not records:
            return "No emails found for filtered export"

        result = run_message_id_export(
            account=account,
            safe_account=safe_account,
            safe_format=safe_format,
            safe_save_dir=safe_save_dir,
            ids_by_mailbox=message_ids_by_mailbox(records, default_mailbox=mailbox),
            include_attachments=include_attachments,
            max_attachment_bytes=_ATTACHMENT_MAX_BYTES,
            max_total_attachment_bytes=_ATTACHMENT_MAX_TOTAL_BYTES,
            timeout=timeout,
            runner=analytics.run_applescript,
        )
        return "FILTERED EXPORT\n\n" + result

    elif scope == "correspondent":
        if not email_address:
            return "Error: email_address is required for correspondent scope"
        effective_date_from = date_from
        if not effective_date_from:
            if recent_days <= 0:
                return unbounded_export_error(account)
            cutoff = datetime.now() - timedelta(days=recent_days)
            effective_date_from = cutoff.strftime("%Y-%m-%d")

        from apple_mail_mcp.tools.search.records import _build_applescript_date

        try:
            date_setup = _build_applescript_date("fromDate", effective_date_from) + _build_applescript_date(
                "toDate", date_to, end_of_day=True
            )
        except ValueError as exc:
            return f"Error: {exc}"
        date_filter = """
                            if messageDate < fromDate then set shouldExport to false
        """
        if date_to:
            date_filter += """
                            if messageDate > toDate then set shouldExport to false
            """
        script = build_correspondent_export_script(
            safe_account=safe_account,
            safe_email_address=escape_applescript(email_address),
            safe_format=safe_format,
            safe_save_dir=safe_save_dir,
            mailbox=mailbox,
            scan_upper_bound=min(max(max_emails + offset, compute_scan_upper_bound(recent_days)), 250),
            max_emails=max_emails,
            offset=offset,
            include_sent=include_sent,
            date_setup=date_setup,
            date_filter=date_filter,
            include_attachments=include_attachments,
            max_attachment_bytes=_ATTACHMENT_MAX_BYTES,
            max_total_attachment_bytes=_ATTACHMENT_MAX_TOTAL_BYTES,
        )

    elif scope == "thread":
        if not message_id:
            return "Error: message_id is required for thread scope"
        if recent_days <= 0:
            return unbounded_export_error(account)

        import json

        from apple_mail_mcp.tools.search import get_email_thread

        thread_mailboxes: list[str] | None = None
        if include_sent and mailbox.lower() != "all":
            thread_mailboxes = [mailbox]
            if mailbox.lower() != "sent":
                thread_mailboxes.append("Sent")
        thread_result = get_email_thread(
            account=account,
            message_id=message_id,
            mailbox=mailbox,
            mailboxes=thread_mailboxes,
            max_messages=max_emails,
            recent_days=recent_days,
            include_preview=False,
            output_format="json",
            timeout=timeout,
        )
        if thread_result.startswith("Error:"):
            return thread_result
        try:
            thread_payload = json.loads(thread_result)
        except json.JSONDecodeError:
            return "Error: get_email_thread returned invalid JSON during thread export"
        records = thread_payload.get("items", [])
        if offset > 0:
            records = records[offset:]
        if not isinstance(records, list) or not records:
            return "No emails found for thread export"
        thread_records = cast(list[dict[str, object]], records)

        # Gmail-backed accounts report each thread message's container as
        # the virtual "All Mail" mailbox, which Mail.app cannot open
        # directly and which must never be scanned (it is the entire
        # remote store). Look ids up across a small, fixed set of
        # openable, bounded mailboxes instead of trusting the reported
        # mailbox name.
        raw_thread_ids = [str(record.get("message_id", "")).strip() for record in thread_records]
        seen_thread_ids: set[str] = set()
        ordered_thread_ids: list[str] = []
        for mid in normalize_message_ids(raw_thread_ids):
            if mid not in seen_thread_ids:
                seen_thread_ids.add(mid)
                ordered_thread_ids.append(mid)
        if not ordered_thread_ids:
            return "No emails found for thread export"

        candidate_mailboxes = ["INBOX"]
        if include_sent:
            candidate_mailboxes.extend(["Sent Mail", "Sent", "Sent Messages", "Sent Items"])

        result = run_multi_mailbox_id_export(
            account=account,
            safe_account=safe_account,
            candidate_mailboxes=candidate_mailboxes,
            safe_format=safe_format,
            safe_save_dir=safe_save_dir,
            message_ids=ordered_thread_ids,
            include_attachments=include_attachments,
            max_attachment_bytes=_ATTACHMENT_MAX_BYTES,
            max_total_attachment_bytes=_ATTACHMENT_MAX_TOTAL_BYTES,
            timeout=timeout,
            runner=analytics.run_applescript,
        )
        return "THREAD EXPORT\n\n" + result

    elif scope == "entire_mailbox":
        from apple_mail_mcp.tools.search.records import _build_applescript_date

        try:
            date_setup = _build_applescript_date("fromDate", date_from) + _build_applescript_date(
                "toDate", date_to, end_of_day=True
            )
        except ValueError as exc:
            return f"Error: {exc}"
        date_filter = ""
        if date_from:
            date_filter += """
                            if messageDate < fromDate then set shouldExport to false
            """
        if date_to:
            date_filter += """
                            if messageDate > toDate then set shouldExport to false
            """
        script = build_entire_mailbox_export_script(
            safe_account=safe_account,
            mailbox=mailbox,
            safe_format=safe_format,
            safe_save_dir=safe_save_dir,
            max_emails=max_emails,
            offset=offset,
            sort=sort,
            date_setup=date_setup,
            date_filter=date_filter,
            include_attachments=include_attachments,
            max_attachment_bytes=_ATTACHMENT_MAX_BYTES,
            max_total_attachment_bytes=_ATTACHMENT_MAX_TOTAL_BYTES,
        )

    else:
        return f"Error: Invalid scope '{scope}'. Use: single_email, filtered, correspondent, thread, entire_mailbox"

    try:
        result = analytics.run_applescript(script, timeout=timeout if timeout is not None else 120)
    except AppleScriptTimeout:
        return f"Error: AppleScript timed out while exporting emails for '{account}'"
    return result
