"""``inbox_dashboard`` tool plus recent-email helpers (sync + async)."""

import asyncio
from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inbox_mailbox_script,
    inject_preferences,
    sanitize_pipe_delimited_field,
)
from apple_mail_mcp.core.replied import SentReplySnapshot
from apple_mail_mcp.core.reply_state import was_replied_fragment
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import analytics
from apple_mail_mcp.tools.reply_state_wiring import (
    annotate_rows_with_reply_state,
    build_draft_scan_status,
    build_sent_reply_scan_status,
)

# Marker prefix for a diagnostic row smuggled through the text-only
# AppleScript-to-Python channel, matching ``tools/search/records.py``. Reuse that
# convention rather than inventing a second error channel. Read by both the emitter
# (``_build_recent_one_account_script``) and the parser
# (``_split_recent_email_output``) so they cannot drift; deliberately re-declared
# instead of imported from ``tools/search``, which would pull that surface's
# ``@mcp.tool`` registrations into this module's import graph.
_ERROR_MAILBOX_PREFIX = "ERROR_MAILBOX|||"


def _build_recent_one_account_script(
    account: str,
    max_per_account: int,
    include_preview: bool,
) -> str:
    """Build AppleScript that returns recent inbox messages for one account.

    Two hazards are handled here rather than in ``inbox_dashboard``, because
    both ``_get_recent_emails_structured`` helpers and ``cli/perf.py`` reach this
    builder directly:

    *Bound clamping.* An unclamped ``max_per_account`` is a silent wrong answer,
    not an error. Verified live, read-only, across four Mail backends:
    ``messages 1 thru 0`` does **not** raise on a non-empty mailbox — index 0
    clamps to 1 and the range normalizes ascending — so it returns exactly one
    message that downstream renders as a genuine "recent email"; and ``thru -1``
    is end-relative, so a negative bound binds essentially the whole mailbox
    (a hang on a 24K inbox). Only an out-of-range *upper* bound raises (-1719).

    *Failure visibility.* Any throw used to be swallowed by a bare script-level
    ``try``, returning ``""`` → ``[]`` → ``"recent_emails": [], "errors": []``,
    which reads as an authoritative empty inbox (the AGENTIC-2344 /
    AGENTIC-2355 failure class). Mailbox throws and per-message read failures
    now emit an ``ERROR_MAILBOX|||account|||message`` row that the Python layer
    diverts into ``error_details``.
    """
    scan_cap = max(1, min(max_per_account, SCAN_BOUNDS["INBOX_HARD_CEILING"]))
    escaped_account = escape_applescript(account)
    preview_block = ""
    preview_field = '""'
    if include_preview:
        preview_block = """
                        set messagePreview to ""
                        try
                            set msgContent to content of aMessage
                            if length of msgContent > 150 then
                                set messagePreview to text 1 thru 150 of msgContent
                            else
                                set messagePreview to msgContent
                            end if
                            set AppleScript's text item delimiters to {return, linefeed}
                            set contentParts to text items of messagePreview
                            set AppleScript's text item delimiters to " "
                            set messagePreview to contentParts as string
                            set AppleScript's text item delimiters to ""
                        end try
        """
        preview_field = "messagePreview"

    return f'''
    tell application "Mail"
        set resultLines to {{}}
        set scanReadFailures to 0
        set scannedCount to 0
        set mailboxError to ""
        set accountName to "{escaped_account}"
        {sanitize_pipe_delimited_field("accountName")}
        try
            set anAccount to account "{escaped_account}"
            set accountName to name of anAccount
            {sanitize_pipe_delimited_field("accountName")}
            {inbox_mailbox_script("inboxMailbox", "anAccount")}

            -- Bounded newest-first slice. `count of messages` can read
            -- stale-high, so the slice carries its own handler and falls back to
            -- an empty list; it must never fall back to `messages of
            -- inboxMailbox`, which materializes a 24K+ mailbox. On a genuinely
            -- empty mailbox every slice form raises -1719, so a zero count skips
            -- the slice entirely and reports no diagnostic: "no mail" must not
            -- render as "error".
            set inboxMessages to {{}}
            set inboxTotal to count of messages of inboxMailbox
            if inboxTotal > 0 then
                try
                    if inboxTotal > {scan_cap} then
                        set inboxMessages to messages 1 thru {scan_cap} of inboxMailbox
                    else
                        set inboxMessages to messages 1 thru inboxTotal of inboxMailbox
                    end if
                on error sliceErr
                    set mailboxError to sliceErr as string
                end try
            end if
            set scannedCount to count of inboxMessages

            repeat with aMessage in inboxMessages
                try
                    set messageSubject to subject of aMessage
                    set messageSender to sender of aMessage
                    {sanitize_pipe_delimited_field("messageSubject")}
                    {sanitize_pipe_delimited_field("messageSender")}
                    set messageDate to date received of aMessage
                    set messageRead to read status of aMessage
                    set messageAppId to (id of aMessage) as string
                    set messageInternetId to ""
                    try
                        set messageInternetId to message id of aMessage
                    end try
                    {was_replied_fragment()}
                    {preview_block}
                    set end of resultLines to messageSubject & "|||" & messageSender & "|||" & (messageDate as string) & "|||" & messageRead & "|||" & accountName & "|||INBOX|||" & messageAppId & "|||" & messageInternetId & "|||" & wasRepliedToken & "|||" & {preview_field}
                on error
                    -- Count instead of swallowing: a scan that threw on every
                    -- message otherwise returned a confident empty inbox.
                    set scanReadFailures to scanReadFailures + 1
                end try
            end repeat
        on error mailboxErr
            set mailboxError to mailboxErr as string
        end try

        if mailboxError is not "" then
            {sanitize_pipe_delimited_field("mailboxError")}
            set end of resultLines to "{_ERROR_MAILBOX_PREFIX}" & accountName & "|||" & mailboxError
        else if scanReadFailures > 0 then
            set end of resultLines to "{_ERROR_MAILBOX_PREFIX}" & accountName & "|||per-message scan failed for " & (scanReadFailures as string) & " of " & (scannedCount as string) & " scanned message(s); results are incomplete"
        end if

        set AppleScript's text item delimiters to linefeed
        return resultLines as string
    end tell
    '''


def _split_recent_email_output(result: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Split one account's scan output into ``(rows, mailbox_errors)``.

    Row field order: subject|||sender|||date|||read|||account|||mailbox
    |||message_id|||internet_message_id|||was_replied_to|||preview.
    ``was_replied_to`` is Mail's native property, always present (no
    parameter gates it); ``has_draft`` is added later by
    ``reply_state_wiring.annotate_rows_with_reply_state``, not here.

    ``ERROR_MAILBOX|||account|||message`` rows are diverted into
    *mailbox_errors* as ``{"account", "type": "mailbox_error", "message"}`` —
    the shape ``search_emails`` puts in ``error_details`` — so a scan that threw
    is never reported as an empty inbox. They are never parsed as email rows.
    """
    emails: list[dict[str, Any]] = []
    mailbox_errors: list[dict[str, str]] = []
    if not result:
        return emails, mailbox_errors
    for line in result.split("\n"):
        # A marker row is exactly three fields, and every data row emits ten.
        # Check the field count too: field 0 of a data row is the *subject*
        # (unlike search's rows, which start with a numeric id), so a message
        # whose subject is literally "ERROR_MAILBOX" could otherwise inject a
        # fake diagnostic and hide its own row. The emitted marker fields are
        # pipe-sanitized, so a genuine marker never exceeds three fields.
        marker_parts = line.split("|||") if line.startswith(_ERROR_MAILBOX_PREFIX) else []
        if len(marker_parts) == 3:
            mailbox_errors.append(
                {
                    "account": marker_parts[1].strip(),
                    "type": "mailbox_error",
                    "message": marker_parts[2].strip(),
                }
            )
            continue
        if "|||" not in line:
            continue
        parts = line.split("|||", 9)
        if len(parts) >= 5:
            legacy_preview = parts[5].strip() if len(parts) > 5 else ""
            emails.append(
                {
                    "subject": parts[0].strip(),
                    "sender": parts[1].strip(),
                    "date": parts[2].strip(),
                    "is_read": parts[3].strip().lower() == "true",
                    "account": parts[4].strip(),
                    "mailbox": parts[5].strip() if len(parts) > 6 else "INBOX",
                    "message_id": parts[6].strip() if len(parts) > 6 else "",
                    "internet_message_id": parts[7].strip() if len(parts) > 7 else "",
                    "was_replied_to": len(parts) > 8 and parts[8].strip().lower() == "true",
                    "preview": parts[9].strip() if len(parts) > 9 else legacy_preview,
                }
            )
    return emails, mailbox_errors


def _parse_recent_email_lines(result: str) -> list[dict[str, Any]]:
    """Return only the email rows from ``_split_recent_email_output``.

    Kept as the row-only seam for callers and tests that do not consume
    diagnostics; ``ERROR_MAILBOX`` rows are dropped here, never fabricated into
    email rows.
    """
    return _split_recent_email_output(result)[0]


def _scan_timeout_detail(account: str, timeout: int) -> dict[str, str]:
    """Return the ``error_details`` entry for a timed-out per-account scan."""
    return {
        "account": account,
        "type": "timeout",
        "message": f"recent-email scan timed out after {timeout}s; results are incomplete",
    }


def _get_recent_emails_structured(
    account: str | None = None,
    max_total: int = 20,
    max_per_account: int = 10,
    include_preview: bool = False,
    timeout: int | None = None,
    error_details: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """
    Internal helper to get recent emails from all accounts as structured data.
    Runs one AppleScript per account sequentially (use async variant for dashboard).

    *error_details* is an optional sink: pass a list to learn *why* a scan
    returned nothing. Mailbox throws and per-message read failures arrive as
    ``ERROR_MAILBOX`` marker rows, timeouts are recorded directly. Without it an
    empty return is indistinguishable from an empty inbox.
    """
    per_call_timeout = timeout if timeout is not None else 60
    accounts = (
        [account] if account else analytics.list_mail_account_names(timeout=30 if timeout is None else min(timeout, 30))
    )
    emails: list[dict[str, Any]] = []
    for account_name in accounts:
        script = _build_recent_one_account_script(account_name, max_per_account, include_preview)
        try:
            result = analytics.run_applescript(script, timeout=per_call_timeout)
        except AppleScriptTimeout:
            if error_details is not None:
                error_details.append(_scan_timeout_detail(account_name, per_call_timeout))
            continue
        rows, scan_errors = _split_recent_email_output(result)
        emails.extend(rows)
        if error_details is not None:
            error_details.extend(scan_errors)
        if len(emails) >= max_total:
            break
    return emails[:max_total]


async def _get_recent_emails_structured_async(
    account: str | None = None,
    max_total: int = 20,
    max_per_account: int = 10,
    include_preview: bool = False,
    timeout: int | None = None,
    error_details: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Fetch recent emails per account sequentially, off the event loop.

    *error_details* is the same optional diagnostic sink as
    ``_get_recent_emails_structured``.
    """
    per_call_timeout = timeout if timeout is not None else 60

    if account:
        accounts = [account]
    else:
        try:
            accounts = await asyncio.to_thread(analytics.list_mail_account_names, timeout)
        except AppleScriptTimeout:
            if error_details is not None:
                error_details.append(
                    {
                        # Not account-specific: no account was scanned at all.
                        "account": "*",
                        "type": "timeout",
                        "message": "Mail account listing timed out; no account was scanned",
                    }
                )
            return []

    async def run_one(account_name: str) -> list[dict[str, Any]]:
        script = _build_recent_one_account_script(account_name, max_per_account, include_preview)
        try:
            raw = await asyncio.to_thread(analytics.run_applescript, script, per_call_timeout)
        except AppleScriptTimeout:
            if error_details is not None:
                error_details.append(_scan_timeout_detail(account_name, per_call_timeout))
            return []
        rows, scan_errors = _split_recent_email_output(raw)
        if error_details is not None:
            error_details.extend(scan_errors)
        return rows

    combined: list[dict[str, Any]] = []
    for account_name in accounts:
        combined.extend(await run_one(account_name))
    return combined[:max_total]


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Inbox Dashboard")
@inject_preferences
async def inbox_dashboard(
    account: str | None = None,
    include_preview: bool = False,
    max_total: int = 20,
    max_per_account: int = 10,
    output_format: str = "ui",
    timeout: int | None = None,
    include_draft_state: bool = True,
) -> Any:
    """
    Get an interactive dashboard view of your email inbox.

    By default, returns an interactive UI dashboard resource that displays:
    - Unread email counts by account (visual cards with badges)
    - Recent emails for the selected/default account, or all accounts if no
      account/default is configured
    - Quick action buttons for common operations (Mark Read, Archive, Delete)
    - Search functionality to filter emails
    - A warning banner naming each account whose recent-email scan failed; while
      it is showing, an empty list is an incomplete scan, not an empty inbox

    Set ``output_format="json"`` for structured dashboard metadata without
    requiring MCP Apps UI support.

    Args:
        account: Optional account name. Defaults to ``DEFAULT_MAIL_ACCOUNT``
            when configured. Omit both only for an explicit all-account view.
        include_preview: Include body previews for recent emails (slower; default False).
        max_total: Maximum recent emails across all accounts (default: 20).
        max_per_account: Maximum recent emails per account (default: 10).
            Clamped to 1..``SCAN_BOUNDS["INBOX_HARD_CEILING"]``; 0 and negative
            values are floored to 1 rather than scanning nothing or everything.
        output_format: ``ui`` (default) or ``json``.
        timeout: Optional per-call AppleScript timeout in seconds (default: 60).
        include_draft_state: JSON mode only. When True (default), correlate
            each recent email against a bounded per-account Drafts snapshot
            and populate ``has_draft`` (true/false/null); when False, every
            row's ``has_draft`` is null and ``draft_scan.status`` is
            ``"skipped"``, with no extra AppleScript call. Every recent
            email always carries ``was_replied_to`` (Mail's native
            property) regardless of this flag. JSON mode independently adds
            bounded Sent-header evidence as ``has_sent_reply`` and composite
            ``reply_state``; UI mode does not run that extra scan.

    Note: Requires mcp-ui-server package and a compatible MCP client.

    Returns:
        UIResource with uri "ui://apple-mail/inbox-dashboard" containing
        an interactive HTML dashboard, or a structured dict when
        ``output_format="json"``. JSON mode's ``recent_emails`` rows always
        carry raw ``was_replied_to`` / ``mail_was_replied_to``, nullable
        ``has_sent_reply`` / ``reply_state``, and ``has_draft``;
        the dict also carries top-level ``sent_reply_scan`` and ``draft_scan`` objects:
        ``{"status": "ok"|"error"|"skipped", "scanned": N, "accounts": [...]}``.

        An empty ``recent_emails`` is only trustworthy when ``errors`` is also
        empty. ``errors`` lists the account names whose recent-email scan failed
        (``"*"`` when the failure was not account-specific), and a non-empty
        ``error_details`` is then added: ``[{"account", "type":
        "mailbox_error"|"timeout", "message"}]``. Do not report "no recent mail"
        while either is populated — the scan did not complete.

        Per-account ``accounts`` values are Mail's **cached** ``unread count``
        aggregate, not measured counts — see the top-level
        ``unread_count_source`` / ``unread_count_measured`` /
        ``unread_count_note`` fields, and never report them as exact.
    """
    if output_format not in {"ui", "json"}:
        return "Error: Invalid output_format. Use: ui, json"

    from apple_mail_mcp.tools.inbox import get_mailbox_unread_counts
    from apple_mail_mcp.tools.inbox.unread_counts import PROVENANCE_KEY
    from apple_mail_mcp.tools.unread_provenance import unread_count_disclosure

    per_call_timeout = timeout if timeout is not None else 60
    selected_account = account or _server.DEFAULT_MAIL_ACCOUNT

    # Sequenced (not gathered): Mail AppleScript is serialized behind a
    # single-flight lock, so running these two probes concurrently would
    # only make them queue behind each other rather than overlap.
    accounts_data = await asyncio.to_thread(
        get_mailbox_unread_counts,
        account=selected_account,
        summary_only=True,
        timeout=per_call_timeout,
    )
    # Lift the cached-count provenance sentinel out of the account map so it
    # travels as a first-class field and never renders as a phantom account
    # card in the UI (the template iterates Object.keys(accountsData)).
    disclosure = accounts_data.pop(PROVENANCE_KEY, None) or unread_count_disclosure()

    # Diagnostic sink: without it a scan that threw on every message is
    # indistinguishable from an empty inbox (see _build_recent_one_account_script).
    recent_errors: list[dict[str, str]] = []
    recent_emails = await analytics._get_recent_emails_structured_async(
        account=selected_account,
        max_total=max_total,
        max_per_account=max_per_account,
        include_preview=include_preview,
        timeout=per_call_timeout,
        error_details=recent_errors,
    )

    if output_format == "json":
        sent_snapshots: dict[str, SentReplySnapshot] = {}
        sent_accounts_requested: list[str] = []
        snapshots = annotate_rows_with_reply_state(
            recent_emails,
            runner=analytics.run_applescript,
            timeout=per_call_timeout,
            include_draft_state=include_draft_state,
            include_sent_reply_state=True,
            sent_snapshots=sent_snapshots,
            sent_accounts_requested=sent_accounts_requested,
        )
        payload: dict[str, Any] = {
            "account": selected_account,
            "include_preview": include_preview,
            "max_total": max_total,
            "max_per_account": max_per_account,
            "accounts": accounts_data,
            "recent_emails": recent_emails,
            # Affected account names, deduped in encounter order. Stays [] on a
            # genuinely quiet mailbox, so an empty scan with an empty `errors`
            # remains a trustworthy "no recent mail".
            "errors": list(dict.fromkeys(item["account"] for item in recent_errors)),
            "draft_scan": build_draft_scan_status(snapshots),
            "sent_reply_scan": build_sent_reply_scan_status(sent_snapshots, sent_accounts_requested),
            **disclosure,
        }
        if recent_errors:
            payload["error_details"] = recent_errors
        return payload

    from apple_mail_mcp import UI_AVAILABLE

    if not UI_AVAILABLE:
        return "Error: UI module not available. Please install mcp-ui-server package."

    from ui import create_inbox_dashboard_ui

    return create_inbox_dashboard_ui(
        accounts_data=accounts_data,
        recent_emails=recent_emails,
        # Same sink the JSON branch turns into `errors` / `error_details`. Without
        # it the default path renders a failed scan as an empty inbox.
        scan_errors=recent_errors,
        # Same provenance the JSON branch spreads into its payload. Without it the
        # default path renders Mail's cached count as a bare, unqualified badge.
        disclosure=disclosure,
    )
