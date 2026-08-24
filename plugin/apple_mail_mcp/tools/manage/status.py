"""``update_email_status`` tool: mark read/unread and flag/unflag via id-direct or filter-scan paths.

Patched names (``run_applescript``, ``validate_account_name``) are referenced via the
``manage`` facade so existing ``patch('...tools.manage.<name>')`` seams keep working."""

from apple_mail_mcp import server as _server
from apple_mail_mcp.applescript_snippets import indent_block
from apple_mail_mcp.backend.base import target_selector_deprecated_error
from apple_mail_mcp.bounded_scan import build_whose_id_list
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    build_mailbox_ref,
    escape_applescript,
    inject_preferences,
    normalize_message_ids,
    normalize_search_terms,
)
from apple_mail_mcp.server import IDEMPOTENT_WRITE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import manage
from apple_mail_mcp.tools.manage.helpers import (
    _check_action_cap,
    _check_message_ids_cap,
    _date_from_for_recent_days,
    _date_to_for_older_than,
    _deprecated_target_selectors,
    _filter_scan_disabled_error,
    _search_message_ids,
    _with_filter_scan_warning,
)


def _mutation_report_block(action_label: str, *, indent: str) -> str:
    """Count one mutated message, then describe it under its own ``on error`` arm.

    Both branches of ``update_email_status`` emit this. ``updateCount`` is
    incremented at the mutation site rather than after the three property reads,
    because a message whose status changed but whose subject could not be read is
    still mutated on the server; counting the read made ``TOTAL UPDATED`` a count
    of successful *descriptions*. The reads keep their own arm so a failure there
    lands on ``detailFailureCount`` and never silently subtracts from the
    mutation count.
    """
    return indent_block(
        f"""if mutatedThisMessage then
    set updateCount to updateCount + 1
    try
        set messageSubject to subject of aMessage
        set messageSender to sender of aMessage
        set messageDate to date received of aMessage

        set outputText to outputText & "- {action_label}: " & messageSubject & return
        set outputText to outputText & "   From: " & messageSender & return
        set outputText to outputText & "   Date: " & (messageDate as string) & return & return
    on error
        set detailFailureCount to detailFailureCount + 1
    end try
end if""",
        indent,
    )


def _failure_summary_lines(*, include_selection: bool, indent: str) -> str:
    """Report each failure counter, but only when it is non-zero.

    Guarded so a clean run stays quiet: an unconditional "0 failures" line would
    train callers to skim past the section that matters. ``include_selection`` is
    for the filter branch only, whose candidate loop can lose a message before it
    is ever considered a match; the id branch has no selection step.
    """
    selection_block = ""
    if include_selection:
        selection_block = """if selectionFailureCount > 0 then
    set outputText to outputText & "SELECTION READ FAILURES: " & selectionFailureCount & " candidate(s) could not be read and were never considered; results are incomplete" & return
end if
"""
    return indent_block(
        f"""{selection_block}if updateFailureCount > 0 then
    set outputText to outputText & "UPDATE FAILURES: " & updateFailureCount & " matched message(s) could not be updated" & return
end if
if detailFailureCount > 0 then
    set outputText to outputText & "DETAILS UNAVAILABLE: " & detailFailureCount & " updated message(s) could not be described above; the status change still applied" & return
end if""",
        indent,
    )


@mcp.tool(annotations=IDEMPOTENT_WRITE_TOOL_ANNOTATIONS, title="Update Email Status")
@inject_preferences
def update_email_status(
    account: str | None = None,
    action: str = "mark_read",
    subject_keyword: str | None = None,
    subject_keywords: list[str] | None = None,
    sender: str | None = None,
    mailbox: str = "INBOX",
    max_updates: int = 10,
    apply_to_all: bool = False,
    message_ids: list[str] | None = None,
    older_than_days: int | None = None,
    recent_days: float = 2.0,
    allow_filter_scan: bool = False,
    timeout: int | None = None,
) -> str:
    """
    Update email status - mark as read/unread or flag/unflag emails.

    Preferred: pass ``message_ids`` from a prior list/search call.
    ``subject_keyword``, ``subject_keywords``, and ``sender`` are deprecated target
    selectors and return ``TARGET_SELECTOR_DEPRECATED`` even when
    ``allow_filter_scan=True``. Date/bulk paths (``older_than_days``,
    ``apply_to_all``) require ``allow_filter_scan=True`` (slow on large mailboxes).

    When message_ids is provided, uses exact ID matching (ignores other filters).

    When ``account`` is None the configured ``DEFAULT_MAIL_ACCOUNT`` is used.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to DEFAULT_MAIL_ACCOUNT.
        action: Action to perform: "mark_read", "mark_unread", "flag", "unflag"
        subject_keyword: Deprecated schema-compat selector. Returns
            ``TARGET_SELECTOR_DEPRECATED`` when ``message_ids`` is omitted.
        subject_keywords: Deprecated schema-compat selector (same as subject_keyword).
        sender: Deprecated schema-compat selector. Returns
            ``TARGET_SELECTOR_DEPRECATED`` when ``message_ids`` is omitted.
        mailbox: Mailbox to search in (default: "INBOX")
        max_updates: Maximum number of emails to update (safety limit, default: 10)
        apply_to_all: Bulk update without filters (requires allow_filter_scan=True)
        message_ids: List of exact Mail message ids (preferred path)
        older_than_days: Optional age filter - only update emails older than N days
            (requires ``allow_filter_scan=True`` when ``message_ids`` is omitted)
        recent_days: Recent window when using date/bulk filter scan (default: 2.0).
        allow_filter_scan: Opt in to slow date/bulk filter scans only (default: False).
            Does not enable subject/sender selectors.
        timeout: Optional AppleScript timeout in seconds (default: 300s).

    Returns:
        Confirmation message with details of updated emails
    """
    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return "Error: account is required (and no DEFAULT_MAIL_ACCOUNT configured)."
    deprecated_selectors = _deprecated_target_selectors(
        subject_keyword=subject_keyword,
        subject_keywords=subject_keywords,
        sender=sender,
    )
    if message_ids is None and deprecated_selectors:
        return target_selector_deprecated_error(
            "update_email_status",
            deprecated_selectors,
            preferred="Call search_emails(...) or list_inbox_emails(...) first, then pass message_ids=[...].",
            discovery="search_emails(..., recent_days=..., limit=...) or list_inbox_emails(...)",
            exact_selector="message_ids",
        )

    action_cap_error = _check_action_cap(max_updates, "max_updates", "update_email_status")
    if action_cap_error:
        return action_cap_error

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = manage.validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return account_err

    safe_account = escape_applescript(account)
    effective_timeout = timeout if timeout is not None else 300

    # Build action scripts
    if action == "mark_read":
        bulk_action_script = "set read status of targetMessages to true"
        single_action_script = "set read status of aMessage to true"
        action_label = "Marked as read"
    elif action == "mark_unread":
        bulk_action_script = "set read status of targetMessages to false"
        single_action_script = "set read status of aMessage to false"
        action_label = "Marked as unread"
    elif action == "flag":
        bulk_action_script = "set flagged status of targetMessages to true"
        single_action_script = "set flagged status of aMessage to true"
        action_label = "Flagged"
    elif action == "unflag":
        bulk_action_script = "set flagged status of targetMessages to false"
        single_action_script = "set flagged status of aMessage to false"
        action_label = "Unflagged"
    else:
        return f"Error: Invalid action '{action}'. Use: mark_read, mark_unread, flag, unflag"

    # --- ID-based path (fast, ignores other filters) ---
    if message_ids is not None:
        normalized_ids = normalize_message_ids(message_ids)
        if not normalized_ids:
            return "Error: 'message_ids' must contain one or more numeric Mail ids"
        cap_error = _check_message_ids_cap(normalized_ids, "update_email_status")
        if cap_error:
            return cap_error

        id_condition = build_whose_id_list(normalized_ids)

        script = f'''
        tell application "Mail"
            with timeout of {effective_timeout} seconds
                set outputText to "UPDATING EMAIL STATUS BY IDS: {action_label}" & return & return
                set updateCount to 0
                set updateFailureCount to 0
                set detailFailureCount to 0

                try
                    set targetAccount to account "{safe_account}"
                    {build_mailbox_ref(mailbox, var_name="targetMailbox")}

                    set targetMessages to every message of targetMailbox whose {id_condition}
                    set requestedCount to {len(normalized_ids)}
                    set matchedCount to count of targetMessages

                    if matchedCount > 0 then
                        set bulkSucceeded to false
                        try
                            {bulk_action_script}
                            set bulkSucceeded to true
                        on error errMsg number errNum
                            set outputText to outputText & "BULKERR|errNum=" & errNum & " errMsg=" & errMsg & return
                        end try

                        repeat with aMessage in targetMessages
                            set mutatedThisMessage to bulkSucceeded
                            if not bulkSucceeded then
                                try
                                    {single_action_script}
                                    set mutatedThisMessage to true
                                on error
                                    set updateFailureCount to updateFailureCount + 1
                                end try
                            end if

                            {_mutation_report_block(action_label, indent=" " * 28)}
                        end repeat
                    end if

                    set outputText to outputText & "========================================" & return
                    set outputText to outputText & "REQUESTED IDS: " & requestedCount & return
                    set outputText to outputText & "MATCHED MESSAGES: " & matchedCount & return
                    set outputText to outputText & "TOTAL UPDATED: " & updateCount & " email(s)" & return
                    {_failure_summary_lines(include_selection=False, indent=" " * 20)}
                    set outputText to outputText & "========================================" & return

                on error errMsg
                    return "Error: " & errMsg
                end try

                return outputText
            end timeout
        end tell
        '''

        try:
            return manage.run_applescript(script, timeout=effective_timeout)
        except AppleScriptTimeout:
            return f"Error: update_email_status timed out after {effective_timeout}s on account '{account}'."

    # --- Filter-based path ---
    subject_terms = normalize_search_terms(subject_keyword, subject_keywords)

    # Safety check: require at least one filter or explicit apply_to_all
    has_filter = bool(subject_terms) or bool(sender) or (older_than_days is not None and older_than_days > 0)
    if not has_filter and not apply_to_all:
        return (
            "Error: Pass message_ids=[...] (preferred), older_than_days, or "
            "apply_to_all=True with allow_filter_scan=True. subject_keyword and sender "
            "are deprecated (TARGET_SELECTOR_DEPRECATED)."
        )

    if not allow_filter_scan:
        return _filter_scan_disabled_error("update_email_status")

    effective_recent_days = recent_days if older_than_days is None else 0.0

    if action in {"mark_read", "mark_unread"}:
        search_read = "unread" if action == "mark_read" else "read"
        search_timeout = timeout if timeout is not None else min(effective_timeout, 120)
        try:
            resolved_ids = _search_message_ids(
                account=account,
                mailbox=mailbox,
                subject_terms=subject_terms or None,
                sender=sender,
                read_status=search_read,
                date_from=_date_from_for_recent_days(effective_recent_days),
                date_to=_date_to_for_older_than(older_than_days),
                limit=max_updates,
                timeout=search_timeout,
                recent_days=effective_recent_days,
            )
        except AppleScriptTimeout:
            return _with_filter_scan_warning(
                f"Error: update_email_status timed out after {effective_timeout}s on account '{account}'."
            )
        if not resolved_ids:
            return _with_filter_scan_warning(f"No matching emails found in {mailbox} for account '{account}'.")
        return _with_filter_scan_warning(
            update_email_status(
                account=account,
                action=action,
                mailbox=mailbox,
                message_ids=resolved_ids,
                max_updates=max_updates,
                timeout=timeout,
            )
        )

    scan_cap = min(500, max(max_updates * 10, 50))
    per_msg_checks: list[str] = []
    if action == "flag":
        per_msg_checks.append("flagged status of aMessage is false")
    else:
        per_msg_checks.append("flagged status of aMessage is true")
    if subject_terms:
        subject_checks = " or ".join(f'messageSubject contains "{escape_applescript(term)}"' for term in subject_terms)
        per_msg_checks.append(f"({subject_checks})")
    if sender:
        per_msg_checks.append(f'messageSender contains "{escape_applescript(sender)}"')
    date_setup = ""
    if older_than_days and older_than_days > 0:
        date_setup = f"set cutoffDate to (current date) - ({older_than_days} * days)"
        per_msg_checks.append("messageDate < cutoffDate")

    combined_condition = " and ".join(per_msg_checks)

    script = f'''
    tell application "Mail"
        with timeout of {effective_timeout} seconds
            set outputText to "UPDATING EMAIL STATUS: {action_label}" & return & return
            set updateCount to 0
            set examinedCount to 0
            set selectionFailureCount to 0
            set updateFailureCount to 0
            set detailFailureCount to 0

            try
                set targetAccount to account "{safe_account}"
                {build_mailbox_ref(mailbox, var_name="targetMailbox")}
                {date_setup}

                set matchingMessages to {{}}
                set candidateMessages to {{}}
                set messageCount to count of messages of targetMailbox
                if messageCount > {scan_cap} then
                    set scanUpperBound to {scan_cap}
                else
                    set scanUpperBound to messageCount
                end if
                if scanUpperBound > 0 then
                    set candidateMessages to messages 1 thru scanUpperBound of targetMailbox
                end if

                repeat with aMessage in candidateMessages
                    if (count of matchingMessages) >= {max_updates} then exit repeat
                    set examinedCount to examinedCount + 1
                    try
                        set messageSubject to subject of aMessage
                        set messageSender to sender of aMessage
                        set messageDate to date received of aMessage
                        if {combined_condition} then
                            set end of matchingMessages to aMessage
                        end if
                    on error
                        set selectionFailureCount to selectionFailureCount + 1
                    end try
                end repeat

                set matchedCount to count of matchingMessages

                repeat with aMessage in matchingMessages
                    set mutatedThisMessage to false
                    try
                        {single_action_script}
                        set mutatedThisMessage to true
                    on error
                        set updateFailureCount to updateFailureCount + 1
                    end try

                    {_mutation_report_block(action_label, indent=" " * 20)}
                end repeat

                set outputText to outputText & "========================================" & return
                set outputText to outputText & "CANDIDATES EXAMINED: " & examinedCount & return
                set outputText to outputText & "MATCHED MESSAGES: " & matchedCount & return
                set outputText to outputText & "TOTAL UPDATED: " & updateCount & " email(s)" & return
                {_failure_summary_lines(include_selection=True, indent=" " * 16)}
                set outputText to outputText & "========================================" & return

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end timeout
    end tell
    '''

    try:
        return _with_filter_scan_warning(manage.run_applescript(script, timeout=effective_timeout))
    except AppleScriptTimeout:
        return _with_filter_scan_warning(
            f"Error: update_email_status timed out after {effective_timeout}s on account '{account}'."
        )
