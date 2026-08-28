"""``get_needs_response`` tool: unread emails that still need a reply from you.

Filters newsletters/automated senders, labels priority, and joins reply-state
signals from ``core.reply_state``
(``tasks/active/reply-state-annotation/plan-2026-07-10.md``): the native
``was replied to`` flag read inline in the per-message loop, and a bounded
Drafts snapshot correlated against each candidate. By default this tool
reports what still needs you: rows already replied to or already drafted
are excluded, and the exclusion is never silent, ``skipped_replied_count``
and ``skipped_drafted_count`` report how many rows were left out. Patched
names (``run_applescript``, ``validate_account_name``) are referenced via
the ``smart_inbox`` package facade so the test seams keep firing;
``fetch_sent_reply_snapshot`` and ``fetch_drafts_snapshot`` are both invoked with
``runner=smart_inbox.run_applescript``.
"""

from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.constants import (
    NEWSLETTER_KEYWORD_PATTERNS,
    NEWSLETTER_PLATFORM_PATTERNS,
    SCAN_BOUNDS,
)
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    date_cutoff_script,
    escape_applescript,
    inject_preferences,
    sanitize_pipe_delimited_field,
)
from apple_mail_mcp.core.replied import SentReplySnapshot, fetch_sent_reply_snapshot
from apple_mail_mcp.core.reply_state import (
    DraftsSnapshot,
    fetch_drafts_snapshot,
    was_replied_fragment,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import smart_inbox
from apple_mail_mcp.tools.reply_state_wiring import sent_reply_scan_warning

# ``_NeedsResponseRow`` (the row dataclass), the ``MSG|||...`` parser, the
# priority-label formatter, and the classifier now live in
# ``reply_state_glue`` (module line budget split, see that module's
# docstring); the ``smart_inbox`` package facade re-exports all of them
# from there so ``smart_inbox_tools._parse_needs_response_inbox_rows`` etc.
# keep resolving.
from apple_mail_mcp.tools.smart_inbox.reply_state_glue import (
    _classify_needs_response_rows,
    _parse_needs_response_inbox_rows,
)


def _newsletter_filter_condition(sender_var: str = "messageSender") -> str:
    """Return AppleScript condition that evaluates to true if email is a newsletter.

    Must be evaluated inside an ``ignoring case`` block — uses raw sender
    text (no longer lowercased) so case-folding is the AppleScript engine's
    job, not a per-message shell-out.
    """
    platform_checks = " or ".join(
        f'{sender_var} contains "{escape_applescript(p)}"' for p in NEWSLETTER_PLATFORM_PATTERNS
    )
    keyword_checks = " or ".join(
        f'{sender_var} contains "{escape_applescript(k)}"' for k in NEWSLETTER_KEYWORD_PATTERNS
    )
    return f"({platform_checks} or {keyword_checks})"


def _format_needs_response_text(
    *,
    account: str,
    mailbox: str,
    days_back: int,
    high: list[dict[str, Any]],
    normal: list[dict[str, Any]],
    skipped_replied: int,
    skipped_drafted: int,
    include_already_replied: bool,
    include_drafted: bool,
    include_draft_state: bool,
    draft_scan: dict[str, Any],
    sent_reply_scan: dict[str, Any],
) -> str:
    """Render the human-readable output identical to the legacy AppleScript, plus reply-state notes."""
    lines = [
        "EMAILS NEEDING RESPONSE",
        f"Account: {account} | Mailbox: {mailbox} | Last {days_back} days",
        "========================================",
        "",
    ]
    result_count = 0
    for entry in (*high, *normal):
        result_count += 1
        lines.append(f"{result_count}. [{entry['priority']}] {entry['subject']}")
        lines.append(f"   From: {entry['sender']}")
        lines.append(f"   Date: {entry['date']}")
        lines.append("")

    lines.append("========================================")
    lines.append(f"Found {result_count} email(s) needing response.")
    if not include_already_replied and skipped_replied > 0:
        lines.append(
            f"Filtered {skipped_replied} already-replied email(s). "
            "Re-run with include_already_replied=True to see them."
        )
    if not include_drafted and skipped_drafted > 0:
        lines.append(f"Filtered {skipped_drafted} drafted email(s). Re-run with include_drafted=True to see them.")
    if not include_draft_state:
        lines.append("Draft-state check disabled (include_draft_state=False); has_draft not evaluated.")
    elif draft_scan.get("status") == "error":
        lines.append(
            f"Draft scan failed ({draft_scan.get('error', 'unknown error')}); "
            "has_draft is unavailable for these results."
        )
    sent_warning = sent_reply_scan_warning(sent_reply_scan)
    if sent_warning:
        lines.append(f"Sent reply-state partial: {sent_warning}")
    # Trailing newline + return-style separator matched the legacy output.
    return "\n".join(lines) + "\n"


def _build_needs_response_inbox_script(
    *,
    escaped_account: str,
    escaped_mailbox: str,
    days_back: int,
    inbox_cap: int,
    max_results: int,
    scan_body: bool,
) -> str:
    """Return AppleScript that emits one ``MSG|||...`` row per candidate email.

    Filters newsletters/automated senders inline (cheaper than fetching every
    sender into Python). Does NOT perform Sent-header replied-detection or
    Drafts correlation, those run as separate, optional passes so this
    per-message loop stays a flat O(N) walk. It does read Mail's native
    ``was replied to`` flag inline (``was_replied_fragment()``, no extra
    AppleScript round trip) and appends it as the row's trailing field.
    """
    newsletter_condition = _newsletter_filter_condition("messageSender")
    date_check = "if messageDate < cutoffDate then exit repeat" if days_back > 0 else ""
    was_replied_script = was_replied_fragment(var="aMessage")
    body_scan_block = (
        """
                            try
                                set msgContent to content of aMessage
                                if length of msgContent > 500 then
                                    set msgContent to text 1 thru 500 of msgContent
                                end if
                                if msgContent contains "?" then set hasQuestion to true
                            end try
"""
        if scan_body
        else ""
    )
    return f'''
    tell application "Mail"
        try
            set targetAccount to account "{escaped_account}"

            try
                set targetMailbox to mailbox "{escaped_mailbox}" of targetAccount
            on error
                if "{escaped_mailbox}" is "INBOX" then
                    set targetMailbox to mailbox "Inbox" of targetAccount
                else
                    error "Mailbox not found: {escaped_mailbox}"
                end if
            end try

            {date_cutoff_script(days_back, "cutoffDate")}

            -- Bounded newest-first slice; no `whose` filter to avoid
            -- materializing deep remote mailboxes.
            set mailboxMessages to {{}}
            set mailboxCount to count of messages of targetMailbox
            if mailboxCount > {inbox_cap} then
                set mailboxUpperBound to {inbox_cap}
            else
                set mailboxUpperBound to mailboxCount
            end if
            if mailboxUpperBound > 0 then
                set mailboxMessages to messages 1 thru mailboxUpperBound of targetMailbox
            end if

            set outputLines to {{}}
            set emittedCount to 0

            repeat with aMessage in mailboxMessages
                if emittedCount >= {max_results} then exit repeat
                try
                    set messageDate to date received of aMessage
                    {date_check}

                    if not (read status of aMessage) then
                        set messageSender to sender of aMessage
                        set messageSubject to subject of aMessage

                        -- Newsletter/automated filter stays in AppleScript:
                        -- shipping every sender back to Python would defeat the
                        -- point of bounding the scan.
                        set isNewsletter to false
                        set isAutomated to false
                        ignoring case
                            set isNewsletter to {newsletter_condition}
                            set isAutomated to (messageSender contains "noreply" or messageSender contains "no-reply" or messageSender contains "donotreply" or messageSender contains "do-not-reply" or messageSender contains "notifications@" or messageSender contains "mailer-daemon" or messageSender contains "postmaster@")
                        end ignoring

                        if not isNewsletter and not isAutomated then
                            set hasQuestion to (messageSubject contains "?")
                            {body_scan_block}

                            set isFlagged to false
                            try
                                set isFlagged to flagged status of aMessage
                            end try

                            {was_replied_script}

                            set mailAppMessageId to id of aMessage as string

                            -- Internet Message-ID may not be available on every
                            -- message; emit "" in that case so Python treats it
                            -- as never-replied.
                            set inboxInternetMessageId to ""
                            try
                                set rawMessageId to message id of aMessage
                                if rawMessageId is not missing value then
                                    set inboxInternetMessageId to rawMessageId as string
                                end if
                            end try

                            set flagText to "false"
                            if isFlagged then set flagText to "true"
                            set questionText to "false"
                            if hasQuestion then set questionText to "true"
                            {sanitize_pipe_delimited_field("messageSubject")}
                            {sanitize_pipe_delimited_field("messageSender")}

                            set end of outputLines to "MSG|||" & mailAppMessageId & "|||" & inboxInternetMessageId & "|||" & messageSubject & "|||" & messageSender & "|||" & (messageDate as string) & "|||" & flagText & "|||" & questionText & "|||" & wasRepliedToken
                            set emittedCount to emittedCount + 1
                        end if
                    end if
                end try
            end repeat

            set AppleScript's text item delimiters to linefeed
            set outputText to outputLines as string
            set AppleScript's text item delimiters to ""
            return outputText
        on error errMsg
            return "Error: " & errMsg
        end try
    end tell
    '''


def _needs_response_error(message: str, *, output_format: str) -> str | dict[str, Any]:
    if output_format == "json":
        return {
            "error": message,
            "errors": [message],
            "high_priority": [],
            "normal_priority": [],
        }
    return f"Error: {message}"


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Needs Response")
@inject_preferences
def get_needs_response(
    account: str | None = None,
    mailbox: str = "INBOX",
    days_back: int = 7,
    max_results: int = 20,
    scan_body: bool = False,
    include_already_replied: bool = False,
    include_drafted: bool = False,
    include_draft_state: bool = True,
    check_already_replied: bool = False,
    timeout: int | None = None,
    output_format: str = "text",
) -> str | dict[str, Any]:
    """Identify unread emails that still need a response from you.

    Filters out newsletters, automated emails, and noreply senders.
    Prioritises direct emails (To: you) with question marks as likely
    needing a reply. This is the "what still needs me" tool: by default it
    excludes emails that are already handled, either because Mail's native
    ``was replied to`` flag is set on them or because a matching draft
    reply already exists in Drafts, so an agent never re-drafts a reply
    that already exists.

    Reply-state signals (see ``core/reply_state.py``):

    - ``was_replied_to``: Mail's native, read-only ``was replied to``
      boolean, read for every candidate in the same per-message loop that
      reads subject/sender/date. This is the primary, always-on signal;
      no parameter gates it.
    - ``has_sent_reply``: optional exact In-Reply-To/References evidence from
      a short bounded Sent scan, enabled only by
      ``check_already_replied=True``. Positive matches remain trustworthy when
      the scan is partial; nonmatches are null unless it completed cleanly.
    - ``has_draft``: a bounded, newest-first Drafts snapshot for *account*
      (fetched once, lazily, only when there is at least one candidate
      row) correlated against each candidate by In-Reply-To/References
      headers or by subject plus recipient plus date. ``true``/``false``
      when the scan ran; ``null`` when the scan was skipped or errored, in
      which case nothing is excluded for draft state (fail open).

    Default exclusion: rows with ``reply_state=true`` or
    ``has_draft=true`` are left out of the results. The exclusion is never
    silent: ``skipped_replied_count`` and ``skipped_drafted_count`` report
    how many rows were left out (JSON keys; a one-line note in text mode).
    Rows that remain always carry ``was_replied_to`` and ``has_draft`` in
    their entry.

    Args:
        account: Account name (e.g., "Gmail", "Work", "Personal").
            Falls back to ``DEFAULT_MAIL_ACCOUNT`` env-configured account when None.
        mailbox: Mailbox to scan (default: "INBOX")
        days_back: How many days back to look (default: 7)
        max_results: Maximum results to return (default: 20)
        scan_body: When True, scan message body for question marks (slower).
            Subject-only detection is usually enough for daily triage (default: False).
        include_already_replied: When False (default), rows where the
            combined replied signal is true are excluded. When True, those
            rows are kept and annotated with a ``[ALREADY REPLIED]`` prefix
            in the priority label.
        include_drafted: When False (default), rows where ``has_draft`` is
            true are excluded. When True, those rows are kept and
            annotated with a ``[HAS DRAFT]`` prefix in the priority label.
        include_draft_state: When True (default), a bounded Drafts snapshot
            is fetched and correlated against candidates so ``has_draft``
            is populated. When False, the snapshot is skipped entirely:
            ``has_draft`` is ``null`` on every row, nothing is excluded for
            draft state, and ``draft_scan.status`` is ``"skipped"``.
        check_already_replied: When True, run one bounded Sent-header scan as
            an additional reply signal. Defaults to False so ordinary calls do
            not trigger remote Sent-header downloads. Failed or truncated
            nonmatches stay unknown and fail open.
        timeout: Optional AppleScript timeout in seconds. Defaults to 120s.
        output_format: ``"text"`` (default, human-readable) or ``"json"``
            (returns a structured dict suitable for programmatic use).

    Returns:
        Ranked list of emails that still need a response. Either a
        formatted text block or a dict ``{"account", "mailbox",
        "days_back", "max_results", "high_priority": [...],
        "normal_priority": [...], "skipped_replied_count",
        "skipped_drafted_count", "draft_scan": {"status", "scanned",
        "total", "truncated", "accounts", "error"?}, "errors"}``
        depending on *output_format*.
        Each entry carries raw ``was_replied_to`` / ``mail_was_replied_to``,
        nullable ``has_sent_reply`` / ``reply_state``, and ``has_draft``.
    """
    if output_format not in {"text", "json"}:
        return _needs_response_error(
            f"invalid output_format: {output_format!r} (expected 'text' or 'json')",
            output_format="text",
        )

    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return _needs_response_error(
            "No account specified and DEFAULT_MAIL_ACCOUNT is not set",
            output_format=output_format,
        )

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = smart_inbox.validate_account_name(account, timeout=validation_timeout)
    if account_err:
        if output_format == "json":
            return {
                "error": account_err,
                "errors": [account_err],
                "high_priority": [],
                "normal_priority": [],
            }
        return account_err

    escaped_account = escape_applescript(account)
    escaped_mailbox = escape_applescript(mailbox)

    # Cap message collection. Tighter caps keep daily triage under agent
    # budgets; ``INBOX_SHORT`` (30) is the centralized ceiling.
    inbox_cap = min(max(max_results * 2, 20), SCAN_BOUNDS["INBOX_SHORT"])
    sent_cap = 20

    # Sent evidence remains explicitly opt-in on this legacy workflow. The
    # primary JSON read surfaces annotate automatically, but this parameter's
    # False default is a compatibility and Exchange-performance contract.
    effective_timeout = timeout if timeout is not None else 120
    inbox_timeout = effective_timeout
    sent_timeout = 30 if timeout is None else min(timeout, 30)
    drafts_timeout = 30 if timeout is None else min(timeout, 30)

    inbox_script = _build_needs_response_inbox_script(
        escaped_account=escaped_account,
        escaped_mailbox=escaped_mailbox,
        days_back=days_back,
        inbox_cap=inbox_cap,
        max_results=max_results,
        scan_body=scan_body,
    )

    try:
        inbox_raw = smart_inbox.run_applescript(inbox_script, timeout=inbox_timeout)
    except AppleScriptTimeout:
        return _needs_response_error(
            (
                f"get_needs_response timed out on account '{account}' after {inbox_timeout}s. "
                "Try increasing timeout or reducing days_back."
            ),
            output_format=output_format,
        )

    if inbox_raw.startswith("Error:"):
        if output_format == "json":
            return {
                "error": inbox_raw,
                "errors": [inbox_raw],
                "high_priority": [],
                "normal_priority": [],
            }
        return inbox_raw

    rows = _parse_needs_response_inbox_rows(inbox_raw)

    sent_reply_snapshot: SentReplySnapshot | None = None
    if check_already_replied:
        sent_reply_snapshot = fetch_sent_reply_snapshot(
            account,
            sent_cap=sent_cap,
            timeout=sent_timeout,
            runner=smart_inbox.run_applescript,
        )

    if sent_reply_snapshot is None:
        sent_reply_scan: dict[str, Any] = {
            "status": "skipped",
            "scanned": 0,
            "total": 0,
            "truncated": False,
            "errors": [],
        }
    else:
        sent_reply_scan = {
            "status": sent_reply_snapshot.status,
            "scanned": sent_reply_snapshot.scanned,
            "total": sent_reply_snapshot.total,
            "truncated": sent_reply_snapshot.truncated,
            "errors": list(sent_reply_snapshot.errors),
        }

    # Drafts snapshot: one bounded scan per call, fetched lazily (only when
    # there is at least one candidate row to correlate against) so an empty
    # inbox slice never pays for a Drafts round trip. Skipped entirely when
    # include_draft_state=False.
    drafts_snapshot: DraftsSnapshot | None = None
    if include_draft_state and rows:
        drafts_snapshot = fetch_drafts_snapshot(
            account,
            runner=smart_inbox.run_applescript,
            timeout=drafts_timeout,
        )

    # ``draft_scan`` carries the same uniform envelope keys as
    # ``reply_state_wiring.build_draft_scan_status`` (status, scanned, total,
    # truncated, accounts): ``total`` falls back to ``scanned`` when the
    # snapshot's mailbox-wide total is unknown, and ``truncated`` mirrors the
    # single snapshot's own fail-open flag. The ``accounts`` value stays a
    # bare account-name list here (this tool scans exactly one account), which
    # is the shape its JSON contract pins.
    draft_scan: dict[str, Any]
    if drafts_snapshot is None:
        draft_scan = {"status": "skipped", "scanned": 0, "total": 0, "truncated": False, "accounts": []}
    else:
        snapshot_total = drafts_snapshot.total if drafts_snapshot.total is not None else drafts_snapshot.scanned
        draft_scan = {
            "status": drafts_snapshot.status,
            "scanned": drafts_snapshot.scanned,
            "total": snapshot_total,
            "truncated": drafts_snapshot.truncated,
            "accounts": [account],
        }
        if drafts_snapshot.status == "error" and drafts_snapshot.error:
            draft_scan["error"] = drafts_snapshot.error

    high, normal, skipped_replied, skipped_drafted = _classify_needs_response_rows(
        rows,
        sent_reply_snapshot=sent_reply_snapshot,
        include_already_replied=include_already_replied,
        include_drafted=include_drafted,
        drafts_snapshot=drafts_snapshot,
        max_results=max_results,
    )

    if output_format == "json":
        return {
            "account": account,
            "mailbox": mailbox,
            "days_back": days_back,
            "max_results": max_results,
            "high_priority": high,
            "normal_priority": normal,
            "skipped_replied_count": skipped_replied,
            "skipped_drafted_count": skipped_drafted,
            "draft_scan": draft_scan,
            "sent_reply_scan": sent_reply_scan,
            "errors": [],
        }

    return _format_needs_response_text(
        account=account,
        mailbox=mailbox,
        days_back=days_back,
        high=high,
        normal=normal,
        skipped_replied=skipped_replied,
        skipped_drafted=skipped_drafted,
        include_already_replied=include_already_replied,
        include_drafted=include_drafted,
        include_draft_state=include_draft_state,
        draft_scan=draft_scan,
        sent_reply_scan=sent_reply_scan,
    )
