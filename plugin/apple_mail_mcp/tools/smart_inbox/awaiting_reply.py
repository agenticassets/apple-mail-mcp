"""``get_awaiting_reply`` tool: sent-vs-inbox Message-ID cross-reference for follow-up tracking.

Patched names (``run_applescript``, ``validate_account_name``) are referenced via the
``smart_inbox`` package facade so existing ``patch('...smart_inbox.run_applescript')``
and the conftest ``validate_account_name`` autouse seam keep firing.
"""

from dataclasses import dataclass
from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    date_cutoff_script,
    escape_applescript,
    inbox_mailbox_script,
    inject_preferences,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import smart_inbox
from apple_mail_mcp.tools.smart_inbox.helpers import _normalize_message_id


def _is_noreply_recipient(address: str) -> bool:
    lower = address.casefold()
    return any(token in lower for token in ("noreply", "no-reply", "do-not-reply", "donotreply"))


def _sent_mailbox_script(var_name: str, account_var: str) -> str:
    return f"""
            set {var_name} to missing value
            try
                set {var_name} to mailbox "Sent Messages" of {account_var}
            on error
                try
                    set {var_name} to mailbox "Sent" of {account_var}
                on error
                    try
                        set {var_name} to mailbox "Sent Items" of {account_var}
                    on error
                        return "Error: Could not find Sent mailbox"
                    end try
                end try
            end try
    """


@dataclass(frozen=True)
class _AwaitingReplySentRow:
    mail_app_id: str
    internet_message_id: str
    subject: str
    recipient_address: str
    sent_at: str


def _parse_awaiting_reply_sent_rows(raw: str) -> list[_AwaitingReplySentRow]:
    rows: list[_AwaitingReplySentRow] = []
    for line in raw.splitlines():
        if not line.startswith("SENT|||"):
            continue
        parts = line.split("|||", 5)
        if len(parts) == 6:
            rows.append(
                _AwaitingReplySentRow(
                    mail_app_id=parts[1],
                    internet_message_id=parts[2],
                    subject=parts[3],
                    recipient_address=parts[4],
                    sent_at=parts[5],
                )
            )
    return rows


def _parse_inbox_replied_ids(raw: str) -> set[str]:
    """Build the set of Message-IDs referenced by inbox In-Reply-To / References headers."""
    replied: set[str] = set()
    for line in raw.splitlines():
        if not line.startswith("INBOXHDR|||"):
            continue
        parts = line.split("|||", 2)
        if len(parts) < 3:
            continue
        value = parts[2].strip()
        if not value:
            continue
        # Extract all <id@host> tokens from the header value.
        for token in value.split():
            token = token.strip()
            if token.startswith("<") and token.endswith(">") and "@" in token:
                replied.add(token)
            elif "@" in token and not token.startswith("<"):
                replied.add("<" + token + ">")
    return replied


def _filter_awaiting_reply(
    *,
    sent_rows: list[_AwaitingReplySentRow],
    replied_to_ids: set[str],
    exclude_noreply: bool,
    max_results: int,
) -> list[_AwaitingReplySentRow]:
    """Apply noreply + already-replied filters; cap to *max_results*."""
    awaiting: list[_AwaitingReplySentRow] = []
    for sent_row in sent_rows:
        if len(awaiting) >= max_results:
            break
        if exclude_noreply and _is_noreply_recipient(sent_row.recipient_address):
            continue
        mid = _normalize_message_id(sent_row.internet_message_id) if sent_row.internet_message_id else ""
        if mid and mid in replied_to_ids:
            continue
        awaiting.append(sent_row)
    return awaiting


def _format_awaiting_reply_results(
    *,
    account: str,
    days_back: int,
    sent_rows: list[_AwaitingReplySentRow],
    replied_to_ids: set[str],
    exclude_noreply: bool,
    max_results: int,
) -> str:
    awaiting = _filter_awaiting_reply(
        sent_rows=sent_rows,
        replied_to_ids=replied_to_ids,
        exclude_noreply=exclude_noreply,
        max_results=max_results,
    )
    lines = [
        "EMAILS AWAITING REPLY",
        f"Account: {account} | Last {days_back} days",
        "========================================",
        "",
    ]
    for index, sent_row in enumerate(awaiting, start=1):
        lines.append(f"{index}. {sent_row.subject}")
        lines.append(f"   To: {sent_row.recipient_address}")
        lines.append(f"   Sent: {sent_row.sent_at}")
        lines.append("")

    lines.append("========================================")
    lines.append(f"Found {len(awaiting)} sent email(s) awaiting reply.")
    return "\n".join(lines)


def _build_awaiting_reply_json(
    *,
    account: str,
    days_back: int,
    max_results: int,
    sent_rows: list[_AwaitingReplySentRow],
    replied_to_ids: set[str],
    exclude_noreply: bool,
) -> dict[str, Any]:
    awaiting = _filter_awaiting_reply(
        sent_rows=sent_rows,
        replied_to_ids=replied_to_ids,
        exclude_noreply=exclude_noreply,
        max_results=max_results,
    )
    return {
        "account": account,
        "days_back": days_back,
        "max_results": max_results,
        "awaiting": [
            {
                "subject": row.subject,
                "recipient": row.recipient_address,
                "sent_at": row.sent_at,
                "message_id": row.internet_message_id,
                "mail_app_id": row.mail_app_id,
            }
            for row in awaiting
        ],
        "errors": [],
    }


def _build_awaiting_reply_inbox_script(
    *,
    escaped_account: str,
    inbox_cap: int,
    days_back: int,
) -> str:
    """Return AppleScript that emits In-Reply-To and References headers from inbox messages.

    Iterates ``headers of aMessage`` to read only the two header rows we need
    (no full RFC822 blob). ``header value of header named "..."`` is *not*
    valid Mail.app dictionary syntax and fails to parse with osascript -2740.
    """
    inbox_date_check = "if messageDate < cutoffDate then exit repeat" if days_back > 0 else ""
    return f'''
    tell application "Mail"
        try
            set targetAccount to account "{escaped_account}"
            {inbox_mailbox_script("inboxMailbox", "targetAccount")}
            {date_cutoff_script(days_back, "cutoffDate")}

            set inboxMessages to {{}}
            set inboxCount to count of messages of inboxMailbox
            if inboxCount > {inbox_cap} then
                set inboxUpperBound to {inbox_cap}
            else
                set inboxUpperBound to inboxCount
            end if
            if inboxUpperBound > 0 then
                set inboxMessages to messages 1 thru inboxUpperBound of inboxMailbox
            end if

            set outputLines to {{}}
            repeat with aMessage in inboxMessages
                try
                    set messageDate to date received of aMessage
                    {inbox_date_check}
                    set inReplyTo to ""
                    set refsHeader to ""
                    try
                        repeat with aHeader in (headers of aMessage)
                            set hName to name of aHeader
                            if hName is "In-Reply-To" then
                                set inReplyTo to content of aHeader
                            else if hName is "References" then
                                set refsHeader to content of aHeader
                            end if
                        end repeat
                    end try
                    if inReplyTo is not "" then
                        set end of outputLines to "INBOXHDR|||in-reply-to|||" & inReplyTo
                    end if
                    if refsHeader is not "" then
                        set end of outputLines to "INBOXHDR|||references|||" & refsHeader
                    end if
                end try
            end repeat

            set AppleScript's text item delimiters to linefeed
            return outputLines as string
        on error errMsg
            return "Error: " & errMsg
        end try
    end tell
    '''


def _build_awaiting_reply_sent_script(
    *,
    escaped_account: str,
    sent_cap: int,
    days_back: int,
) -> str:
    """Return AppleScript that emits sent message rows with internet_message_id.

    Shape: SENT|||mail_app_id|||internet_message_id|||subject|||recipient|||date_sent
    """
    sent_date_check = "if messageDate < cutoffDate then exit repeat" if days_back > 0 else ""
    return f'''
    tell application "Mail"
        try
            set targetAccount to account "{escaped_account}"
            {_sent_mailbox_script("sentMailbox", "targetAccount")}
            {date_cutoff_script(days_back, "cutoffDate")}

            set sentMessages to {{}}
            set sentCount to count of messages of sentMailbox
            if sentCount > {sent_cap} then
                set sentUpperBound to {sent_cap}
            else
                set sentUpperBound to sentCount
            end if
            if sentUpperBound > 0 then
                set sentMessages to messages 1 thru sentUpperBound of sentMailbox
            end if

            set outputLines to {{}}
            repeat with aMessage in sentMessages
                try
                    set messageDate to date sent of aMessage
                    {sent_date_check}
                    set mailAppId to ""
                    try
                        set mailAppId to id of aMessage as string
                    end try
                    set internetMsgId to ""
                    try
                        set internetMsgId to message id of aMessage as string
                    end try
                    set messageSubject to ""
                    try
                        set messageSubject to subject of aMessage
                    end try
                    set recipAddr to ""
                    try
                        set messageRecipients to every to recipient of aMessage
                        if (count of messageRecipients) > 0 then
                            set recipAddr to address of item 1 of messageRecipients
                        end if
                    end try
                    set end of outputLines to "SENT|||" & mailAppId & "|||" & internetMsgId & "|||" & messageSubject & "|||" & recipAddr & "|||" & (messageDate as string)
                end try
            end repeat

            set AppleScript's text item delimiters to linefeed
            return outputLines as string
        on error errMsg
            return "Error: " & errMsg
        end try
    end tell
    '''


def _awaiting_reply_error(message: str, *, output_format: str) -> str | dict[str, Any]:
    if output_format == "json":
        return {"error": message, "errors": [message], "awaiting": []}
    return f"Error: {message}"


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Awaiting Reply")
@inject_preferences
def get_awaiting_reply(
    account: str | None = None,
    days_back: int = 7,
    exclude_noreply: bool = True,
    max_results: int = 20,
    timeout: int | None = None,
    output_format: str = "text",
) -> str | dict[str, Any]:
    """Find sent emails that haven't received a reply yet.

    Scans the Sent mailbox for outgoing emails and cross-references with
    the Inbox using Message-ID matching (In-Reply-To / References headers)
    to determine whether a reply has arrived. No subject-substring matching
    is used. Useful for follow-up tracking.

    Args:
        account: Account name (e.g., "Gmail", "Work", "Personal").
            Falls back to ``DEFAULT_MAIL_ACCOUNT`` env-configured account when None.
        days_back: How many days back to check sent emails (default: 7)
        exclude_noreply: Skip emails sent to noreply/no-reply addresses (default: True)
        max_results: Maximum results to return (default: 20)
        timeout: Optional AppleScript timeout in seconds. Defaults to 120s.
        output_format: ``"text"`` (default, human-readable) or ``"json"``
            (returns a structured dict suitable for programmatic use).

    Returns:
        Either a formatted text block or a dict ``{"account", "days_back",
        "max_results", "awaiting": [...], "errors": []}`` depending on
        *output_format*.
    """
    if output_format not in {"text", "json"}:
        return _awaiting_reply_error(
            f"invalid output_format: {output_format!r} (expected 'text' or 'json')",
            output_format="text",
        )

    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return _awaiting_reply_error(
            "No account specified and DEFAULT_MAIL_ACCOUNT is not set",
            output_format=output_format,
        )

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = smart_inbox.validate_account_name(account, timeout=validation_timeout)
    if account_err:
        if output_format == "json":
            # ``validate_account_name`` returns either a serialized JSON
            # ``ToolError`` string or a plain "Error: ..." string. Surface
            # whichever shape it produced under the JSON ``error`` key so
            # callers don't have to dual-decode.
            return {"error": account_err, "errors": [account_err], "awaiting": []}
        return account_err

    escaped_account = escape_applescript(account)

    # ``INBOX_SHORT`` (30) caps the inbox slice; the sent slice stays at 20
    # to match the existing tighter follow-up window.
    sent_cap = min(max(max_results, 5), 20)
    inbox_cap = min(max(max_results * 2, 10), SCAN_BOUNDS["INBOX_SHORT"])
    inbox_script = _build_awaiting_reply_inbox_script(
        escaped_account=escaped_account,
        inbox_cap=inbox_cap,
        days_back=days_back,
    )
    sent_script = _build_awaiting_reply_sent_script(
        escaped_account=escaped_account,
        sent_cap=sent_cap,
        days_back=days_back,
    )
    effective_timeout = timeout if timeout is not None else 120
    # Split budget so the two sequential calls cannot exceed the total wall time.
    # Inbox scan gets 60 % (min 30 s); sent scan gets the remaining 40 % (min 30 s).
    inbox_timeout = max(30, int(effective_timeout * 0.6))
    sent_timeout = max(30, int(effective_timeout * 0.4))

    try:
        inbox_raw = smart_inbox.run_applescript(inbox_script, timeout=inbox_timeout)
    except AppleScriptTimeout:
        return _awaiting_reply_error(
            (
                f"get_awaiting_reply timed out during inbox scan on account '{account}' "
                f"after {inbox_timeout}s — try increasing timeout or reducing days_back"
            ),
            output_format=output_format,
        )
    try:
        sent_raw = smart_inbox.run_applescript(sent_script, timeout=sent_timeout)
    except AppleScriptTimeout:
        return _awaiting_reply_error(
            (
                f"get_awaiting_reply timed out during sent scan on account '{account}' "
                f"after {sent_timeout}s — try increasing timeout or reducing days_back"
            ),
            output_format=output_format,
        )

    for raw in (inbox_raw, sent_raw):
        if raw.startswith("Error:"):
            if output_format == "json":
                return {"error": raw, "errors": [raw], "awaiting": []}
            return raw

    sent_rows = _parse_awaiting_reply_sent_rows(sent_raw)
    replied_to_ids = _parse_inbox_replied_ids(inbox_raw)

    if output_format == "json":
        return _build_awaiting_reply_json(
            account=account,
            days_back=days_back,
            max_results=max_results,
            sent_rows=sent_rows,
            replied_to_ids=replied_to_ids,
            exclude_noreply=exclude_noreply,
        )

    return _format_awaiting_reply_results(
        account=account,
        days_back=days_back,
        sent_rows=sent_rows,
        replied_to_ids=replied_to_ids,
        exclude_noreply=exclude_noreply,
        max_results=max_results,
    )
