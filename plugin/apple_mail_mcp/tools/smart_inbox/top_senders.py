"""``get_top_senders`` tool: bounded newest-first sender/domain frequency analysis.

Aggregates with a Python ``Counter`` over ``ROW|||`` lines and guards unbounded
sweeps with ``UNBOUNDED_SCAN_REQUIRED``. Patched names (``run_applescript``,
``validate_account_name``) are referenced via the ``smart_inbox`` package facade.
"""

from collections import Counter
from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    date_cutoff_script,
    escape_applescript,
    inject_preferences,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import smart_inbox


def _top_senders_error(
    message: str,
    *,
    output_format: str,
    account: str | None = None,
    mailbox: str | None = None,
    days_back: int | None = None,
    top_n: int | None = None,
    group_by_domain: bool | None = None,
    error_code: str = "error",
) -> str | dict[str, Any]:
    if output_format == "json":
        payload: dict[str, Any] = {
            "error": error_code,
            "errors": [message],
            "senders": [],
        }
        if account is not None:
            payload["account"] = account
        if mailbox is not None:
            payload["mailbox"] = mailbox
        if days_back is not None:
            payload["days_back"] = days_back
        if top_n is not None:
            payload["top_n"] = top_n
        if group_by_domain is not None:
            payload["group_by_domain"] = group_by_domain
        return payload
    return f"Error: {message}"


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Top Senders")
@inject_preferences
def get_top_senders(
    account: str | None = None,
    mailbox: str = "INBOX",
    days_back: int = 30,
    top_n: int = 10,
    group_by_domain: bool = False,
    output_format: str = "text",
    timeout: int | None = None,
) -> str | dict[str, Any]:
    """Analyse a mailbox to find the most frequent senders.

    Useful for identifying key contacts, high-volume senders to filter,
    or newsletter sources to unsubscribe from.

    Args:
        account: Account name (e.g., "Gmail", "Work", "Personal").
            Falls back to ``DEFAULT_MAIL_ACCOUNT`` env-configured account when None.
        mailbox: Mailbox to analyse (default: "INBOX")
        days_back: How many days back to look (default: 30). ``0`` (all time)
            is no longer accepted; pass ``days_back=7`` or ``30``. Full-mailbox
            scans are disabled, so narrow the window instead.
        top_n: Number of top senders to return (default: 10)
        group_by_domain: Group results by domain instead of individual sender (default: False)
        output_format: ``"text"`` (default, human-readable) or ``"json"``
            (structured dict with stable keys + ``errors[]``).
        timeout: Optional AppleScript timeout in seconds. Defaults to 120s.

    Returns:
        Ranked list of senders (or domains) with email counts. Text by
        default; a dict with keys ``{account, mailbox, days_back, top_n,
        group_by_domain, senders, total_analysed, mailbox_count,
        unique_senders, scan_cap, errors}`` when ``output_format='json'``.
    """
    if output_format not in {"text", "json"}:
        return _top_senders_error(
            f"invalid output_format: {output_format!r} (expected 'text' or 'json')",
            output_format="text",
        )

    if days_back <= 0:
        err = ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=("get_top_senders refuses to scan without days_back; pass days_back=7 or 30"),
            remediation={
                "preferred": "Pass days_back=7 or 30",
                "note": "Full-mailbox scans are disabled; bound this call.",
            },
        )
        if output_format == "json":
            payload = err.to_dict()
            payload.setdefault("errors", [])
            payload["senders"] = []
            return payload
        return serialize_tool_error(err)

    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return _top_senders_error(
            "No account specified and DEFAULT_MAIL_ACCOUNT is not set",
            output_format=output_format,
            mailbox=mailbox,
            days_back=days_back,
            top_n=top_n,
            group_by_domain=group_by_domain,
            error_code="account_required",
        )

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = smart_inbox.validate_account_name(account, timeout=validation_timeout)
    if account_err:
        if output_format == "json":
            return {
                "error": "account_not_found",
                "errors": [account_err],
                "account": account,
                "mailbox": mailbox,
                "days_back": days_back,
                "top_n": top_n,
                "group_by_domain": group_by_domain,
                "senders": [],
            }
        return account_err

    escaped_account = escape_applescript(account)
    escaped_mailbox = escape_applescript(mailbox)

    date_cutoff = date_cutoff_script(days_back, "cutoffDate")
    date_check = "if messageDate < cutoffDate then exit repeat" if days_back > 0 else ""

    # Cap message scan. Prefer a bounded newest-first slice + Python aggregation
    # over `whose` filters that materialize huge inboxes. ``INBOX_LONG`` (100)
    # is the centralized ceiling for the longest-window read tools.
    scan_cap = min(SCAN_BOUNDS["INBOX_LONG"], max(top_n * 5, 15))
    if days_back > 14:
        scan_cap = min(scan_cap, 25)
    if days_back >= 30:
        scan_cap = min(scan_cap, 15)

    # Build the extraction key: either full sender or domain.
    if group_by_domain:
        # Extract domain from email address
        extract_key = """
                            -- Extract domain from sender address
                            set senderKey to ""
                            set atPos to 0
                            set senderLen to length of messageSender
                            repeat with i from 1 to senderLen
                                if character i of messageSender is "@" then
                                    set atPos to i
                                end if
                            end repeat
                            if atPos > 0 then
                                -- Find the closing > if present
                                set endPos to senderLen
                                repeat with i from atPos to senderLen
                                    if character i of messageSender is ">" then
                                        set endPos to i - 1
                                        exit repeat
                                    end if
                                end repeat
                                set senderKey to text (atPos + 1) thru endPos of messageSender
                            else
                                set senderKey to messageSender
                            end if
"""
        title_label = "TOP SENDER DOMAINS"
    else:
        extract_key = """
                            set senderKey to messageSender
"""
        title_label = "TOP SENDERS"

    # Return one ROW|||sender per message; Python aggregates with Counter.
    script = f'''
    tell application "Mail"
        try
            set targetAccount to account "{escaped_account}"

            -- Get target mailbox
            try
                set targetMailbox to mailbox "{escaped_mailbox}" of targetAccount
            on error
                if "{escaped_mailbox}" is "INBOX" then
                    set targetMailbox to mailbox "Inbox" of targetAccount
                else
                    error "Mailbox not found: {escaped_mailbox}"
                end if
            end try

            {date_cutoff}

            set mailboxMessages to {{}}
            set mailboxCount to count of messages of targetMailbox
            if mailboxCount > {scan_cap} then
                set mailboxUpperBound to {scan_cap}
            else
                set mailboxUpperBound to mailboxCount
            end if
            if mailboxUpperBound > 0 then
                set mailboxMessages to messages 1 thru mailboxUpperBound of targetMailbox
            end if

            set outputLines to {{}}
            set totalAnalysed to 0

            repeat with aMessage in mailboxMessages
                try
                    set messageDate to date received of aMessage
                    {date_check}

                    set messageSender to sender of aMessage
                    set totalAnalysed to totalAnalysed + 1

                    {extract_key}

                    set end of outputLines to "ROW|||" & senderKey
                end try
            end repeat

            set end of outputLines to "TOTAL|||" & (totalAnalysed as string)
            set end of outputLines to "MAILBOX_COUNT|||" & (mailboxCount as string)

            set AppleScript's text item delimiters to linefeed
            set outputText to outputLines as string
            set AppleScript's text item delimiters to ""
            return outputText

        on error errMsg
            return "ERROR|||" & errMsg
        end try
    end tell
    '''

    try:
        raw = smart_inbox.run_applescript(script, timeout=timeout)
    except AppleScriptTimeout:
        wait_s = timeout if timeout is not None else 120
        return _top_senders_error(
            f"get_top_senders timed out on account '{account}' after "
            f"{wait_s}s — try increasing timeout or reducing days_back",
            output_format=output_format,
            account=account,
            mailbox=mailbox,
            days_back=days_back,
            top_n=top_n,
            group_by_domain=group_by_domain,
            error_code="timeout",
        )

    if raw.startswith("ERROR|||"):
        return _top_senders_error(
            raw.split("|||", 1)[1],
            output_format=output_format,
            account=account,
            mailbox=mailbox,
            days_back=days_back,
            top_n=top_n,
            group_by_domain=group_by_domain,
            error_code="applescript_error",
        )

    # Parse ROW lines and aggregate in Python (fast Counter vs AppleScript O(n^2)).
    total_analysed = 0
    mailbox_count = 0
    sender_counts: Counter[str] = Counter()
    for line in raw.splitlines():
        if line.startswith("TOTAL|||"):
            try:
                total_analysed = int(line.split("|||", 1)[1].strip())
            except ValueError:
                total_analysed = 0
        elif line.startswith("MAILBOX_COUNT|||"):
            try:
                mailbox_count = int(line.split("|||", 1)[1].strip())
            except ValueError:
                mailbox_count = 0
        elif line.startswith("ROW|||"):
            key = line.split("|||", 1)[1].strip()
            if key:
                sender_counts[key] += 1

    unique_count = len(sender_counts)
    top_entries = sender_counts.most_common(top_n)

    if output_format == "json":
        sender_records: list[dict[str, Any]] = []
        for key, cnt in top_entries:
            entry: dict[str, Any] = {
                "key": key,
                "count": cnt,
            }
            if total_analysed > 0:
                entry["percent"] = round((cnt / total_analysed) * 100)
            sender_records.append(entry)
        return {
            "account": account,
            "mailbox": mailbox,
            "days_back": days_back,
            "top_n": top_n,
            "group_by_domain": group_by_domain,
            "senders": sender_records,
            "total_analysed": total_analysed,
            "mailbox_count": mailbox_count,
            "unique_senders": unique_count,
            "scan_cap": scan_cap,
            "errors": [],
        }

    lines = [
        title_label,
        f"Account: {account} | Mailbox: {mailbox} | Last {days_back} days",
        "========================================",
        "",
    ]
    for i, (key, cnt) in enumerate(top_entries, start=1):
        if total_analysed > 0:
            pct = round((cnt / total_analysed) * 100)
            pct_text = f" ({pct}%)"
        else:
            pct_text = ""
        lines.append(f"{i}. {key}: {cnt} emails{pct_text}")

    lines.append("")
    lines.append("========================================")
    lines.append(f"Total emails analysed: {total_analysed}")
    if mailbox_count > 0 and scan_cap < mailbox_count:
        lines.append(
            f"Note: analysed {total_analysed} of {mailbox_count} messages (capped at {scan_cap} — "
            "increase days_back (full-mailbox scans are disabled))"
        )
    lines.append(f"Unique senders: {unique_count}")

    return "\n".join(lines) + "\n"
