"""``get_inbox_overview`` tool plus its per-account script builder and parser.

The text/JSON formatters live in ``overview_formatting.py`` (600 LOC budget).
``run_applescript``, ``validate_account_name``, and ``_list_mail_accounts`` route
through the ``inbox`` facade so the existing test patch seams keep firing."""

import asyncio
from typing import Any

from apple_mail_mcp.bounded_scan import build_bounded_message_scan
from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inbox_mailbox_script,
    inject_preferences,
)
from apple_mail_mcp.core.reply_state import DraftsSnapshot, was_replied_fragment
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import inbox
from apple_mail_mcp.tools.inbox.overview_formatting import (
    _format_overview,
    _format_overview_json,
    _overview_json_error,
)
from apple_mail_mcp.tools.reply_state_wiring import annotate_rows_with_reply_state, build_draft_scan_status

# ``max_recent`` is a plain tool argument with no client-side validation, so a
# caller can hand this tool any integer. That value used to go straight into
# the recent-slice guard, and any value above the mailbox size took the
# ``else`` arm that bound *every* message — on a 25K Exchange inbox that is a
# hang, not an error. Clamp to the same per-account read ceiling the
# rest of the inbox surface uses (``list_inbox_emails`` via
# ``inbox/list_scripts.py``, ``inbox_dashboard``, ``get_statistics``), so no
# argument value can widen the scan.
RECENT_SCAN_CEILING: int = SCAN_BOUNDS["INBOX_HARD_CEILING"]


def _clamp_max_recent(max_recent: int) -> int:
    """Clamp a caller-supplied ``max_recent`` to ``RECENT_SCAN_CEILING``.

    ``max_recent <= 0`` keeps its existing meaning — skip the recent block
    entirely — and is returned unchanged. Flooring it to 1 instead would be a
    different wrong answer: ``messages 1 thru 0`` does not bind an empty list,
    it silently returns the *first* message (verified on all four backends),
    so "read nothing" must never be rewritten into "read something".
    """
    if max_recent <= 0:
        return max_recent
    return min(max_recent, RECENT_SCAN_CEILING)


def _max_recent_clamp_note(requested: int, effective: int) -> str:
    """Agent-facing prose for a clamped ``max_recent`` request."""
    return (
        f"max_recent={requested} exceeds the per-account read ceiling of "
        f"{effective} (SCAN_BOUNDS['INBOX_HARD_CEILING']); at most {effective} "
        "recent message(s) were read per account. The recent list is truncated, "
        "not complete — page with list_inbox_emails for more."
    )


def _build_overview_one_account_script(
    account: str,
    *,
    include_mailboxes: bool = True,
    include_recent: bool = True,
    max_recent: int = 10,
    max_mailboxes: int = 100,
) -> str:
    """Build a script that returns one account's unread/total/recent slice.

    Returns a structured payload:
        accountName|||unreadCount|||totalCount
        MAILBOX|||name|||unreadCount
        MAILBOX|||name/subName|||subUnread
        RECENT|||subject|||sender|||date|||read|||wasRepliedToken
        MAILBOX_CAPPED|||accountName|||cap
        ...

    ``wasRepliedToken`` is Mail's native ``was replied to`` property, read
    unconditionally in the same per-message pass (no new AppleScript round
    trip; see ``core.reply_state.was_replied_fragment``).

    A1: caps recent-message enumeration via ``build_bounded_message_scan``,
    which emits ``messages 1 thru N of inboxMailbox`` and never falls back to
    an unbounded spelling. ``max_recent`` is clamped here as well as in
    ``get_inbox_overview`` because this builder, ``_run_overview_one``, and the
    ``inbox`` facade are all reachable without going through the tool.
    A2: caps mailbox enumeration at max_mailboxes (default 100) to prevent
    Exchange deep-folder or Gmail many-labels timeouts.
    """
    escaped_account = escape_applescript(account)
    max_recent = _clamp_max_recent(max_recent)
    recent_block = ""
    if include_recent and max_recent > 0:
        recent_block = f"""
                -- Recent messages: bounded newest-first slice, capped at
                -- {max_recent} (SCAN_BOUNDS["INBOX_HARD_CEILING"]). The helper
                -- slices first and only consults the cached `count of messages`
                -- in its recovery arm; it raises an ERROR_MAILBOX marker rather
                -- than binding a false empty, and never emits a slice whose
                -- upper bound could be 0.
                {build_bounded_message_scan("inboxMailbox", max_recent)}

                repeat with aMessage in candidateMessages
                    try
                        set messageSubject to subject of aMessage
                        set messageSender to sender of aMessage
                        set messageDate to date received of aMessage
                        set messageRead to read status of aMessage
                        {was_replied_fragment()}
                        set end of resultLines to "RECENT|||" & messageSubject & "|||" & messageSender & "|||" & (messageDate as string) & "|||" & messageRead & "|||" & wasRepliedToken
                    end try
                end repeat
        """
    mailbox_block = ""
    if include_mailboxes:
        mailbox_block = f"""
            -- Mailbox structure with unread counts (capped at {max_mailboxes})
            try
                set accountMailboxes to every mailbox of anAccount
                set mailboxIndex to 0
                repeat with aMailbox in accountMailboxes
                    set mailboxIndex to mailboxIndex + 1
                    if mailboxIndex > {max_mailboxes} then
                        set end of resultLines to "MAILBOX_CAPPED|||" & accountName & "|||{max_mailboxes}"
                        exit repeat
                    end if
                    try
                        set mailboxName to name of aMailbox
                        set unreadCount to unread count of aMailbox
                        set end of resultLines to "MAILBOX|||" & mailboxName & "|||" & unreadCount
                        try
                            set subMailboxes to every mailbox of aMailbox
                            repeat with subBox in subMailboxes
                                set subName to name of subBox
                                set subUnread to unread count of subBox
                                set end of resultLines to "SUBMAILBOX|||" & mailboxName & "/" & subName & "|||" & subUnread
                            end repeat
                        end try
                    end try
                end repeat
            end try
        """
    return f"""
    tell application "Mail"
        set resultLines to {{}}
        try
            set anAccount to account "{escaped_account}"
            set accountName to name of anAccount

            try
                {inbox_mailbox_script("inboxMailbox", "anAccount")}
                set unreadCount to unread count of inboxMailbox
                set totalMessages to count of messages of inboxMailbox
                set end of resultLines to "HEADER|||" & accountName & "|||" & unreadCount & "|||" & totalMessages

                {recent_block}
            on error errMsg
                set end of resultLines to "HEADER|||" & accountName & "|||ERROR|||" & errMsg
            end try

            {mailbox_block}
        on error errMsg
            set end of resultLines to "FATAL|||" & errMsg
        end try

        set AppleScript's text item delimiters to linefeed
        return resultLines as string
    end tell
    """


def _run_overview_one(
    account: str,
    timeout: int | None,
    include_mailboxes: bool = True,
    include_recent: bool = True,
    max_recent: int = 10,
    max_mailboxes: int = 100,
) -> str:
    effective_timeout = timeout if timeout is not None else 180
    return inbox.run_applescript(
        _build_overview_one_account_script(
            account,
            include_mailboxes=include_mailboxes,
            include_recent=include_recent,
            max_recent=max_recent,
            max_mailboxes=max_mailboxes,
        ),
        timeout=effective_timeout,
    )


def _parse_overview_account(raw: str) -> dict[str, Any]:
    """Parse one account's overview payload."""
    result: dict[str, Any] = {
        "account": None,
        "unread": None,
        "total": None,
        "error": None,
        "mailboxes": [],  # list of (name, unread_count) tuples
        "recent": [],  # list of dicts
        "mailboxes_truncated": False,
        "sampled_unread": None,  # unread seen in the recent slice (lower bound)
    }
    parse_errors: list[str] = []
    if not raw:
        return result
    for line in raw.splitlines():
        if "|||" not in line:
            continue
        parts = line.split("|||")
        tag = parts[0]
        if tag == "HEADER" and len(parts) >= 4:
            result["account"] = parts[1]
            if parts[2] == "ERROR":
                # ``ERROR_MAILBOX|||<mailbox>|||<detail>`` markers raised by
                # ``build_bounded_message_scan`` reach here already containing
                # the delimiter, so rejoin the tail instead of truncating the
                # diagnostic to its first field.
                result["error"] = "|||".join(parts[3:]) if len(parts) > 3 else "unknown error"
            else:
                try:
                    result["unread"] = int(parts[2])
                    result["total"] = int(parts[3])
                except ValueError:
                    parse_errors.append(f"Invalid HEADER counts for {parts[1]!r}: {parts[2]!r}, {parts[3]!r}")
        elif tag in ("MAILBOX", "SUBMAILBOX") and len(parts) >= 3:
            try:
                result["mailboxes"].append((parts[1], int(parts[2])))
            except ValueError:
                parse_errors.append(f"Invalid {tag} unread count for {parts[1]!r}: {parts[2]!r}")
        elif tag == "MAILBOX_CAPPED" and len(parts) >= 2:
            result["mailboxes_truncated"] = True
        elif tag == "RECENT" and len(parts) >= 5:
            result["recent"].append(
                {
                    "subject": parts[1],
                    "sender": parts[2],
                    "date": parts[3],
                    "is_read": parts[4].strip().lower() == "true",
                    "was_replied_to": len(parts) > 5 and parts[5].strip().lower() == "true",
                }
            )
        elif tag == "FATAL" and len(parts) >= 2:
            result["error"] = parts[1]
    # Unread messages counted in the newest-first recent slice are a strict
    # lower bound on the inbox's true unread total. When that lower bound
    # exceeds Mail's cached `unread count`, the cached value is provably too
    # low. Free: the recent pass already read every row's `read status`.
    if result["recent"]:
        result["sampled_unread"] = sum(1 for row in result["recent"] if not row["is_read"])
    if parse_errors:
        result["parse_errors"] = parse_errors
    return result


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Inbox Overview")
@inject_preferences
async def get_inbox_overview(
    account: str | None = None,
    output_format: str = "text",
    include_mailboxes: bool = True,
    include_recent: bool = True,
    include_suggestions: bool = True,
    max_recent: int = 10,
    max_mailboxes: int = 100,
    timeout: int | None = None,
    include_draft_state: bool = True,
) -> str | dict[str, Any]:
    """
    Get a comprehensive overview of your email inbox status across all accounts.

    Each account is queried sequentially, one AppleScript call at a time
    (Mail.app AppleScript is serialized behind a single-flight lock), so a
    single slow account (e.g. a large Exchange inbox) does not corrupt the
    rest of the overview. It appears as an entry in a `PARTIAL` line and the
    rest of the data is returned anyway.

    **Every unread number here is Mail.app's cached ``unread count`` aggregate,
    not a measured count.** It drifts low: measured 2026-08-17 on a
    25,012-message Exchange Inbox, Mail reported 3,236 unread where per-message
    truth was 10,016 (a 68% under-report). ``total`` (``count of messages``) is
    reliable; do not subtract ``unread`` from it to claim a read count. Report
    unread as approximate, and use
    ``list_inbox_emails(read_status="unread")`` when an exact number matters.

    This tool cross-checks the cached value two ways for free, because it
    already reads both the mailbox message count and the recent slice's
    per-message read status, and sets ``unread_count_suspect`` when either
    check fails:

    * ``cached_unread_exceeds_message_count`` — impossible on its face.
    * ``sampled_unread_exceeds_cached_unread`` — unread messages in the newest
      slice are a strict lower bound, so exceeding the cached total proves it
      too low.

    A clean check is not proof of a correct count; the measured 3,236-vs-10,016
    case trips neither.

    Args:
        account: Optional account name to scope the overview to one account.
        output_format: ``text`` (default), ``compact`` (shorter text), or ``json``.
        include_mailboxes: Include mailbox structure with unread counts (default: True).
        include_recent: Include recent-email preview section (default: True).
        include_suggestions: Include assistant action suggestions (default: True).
        max_recent: Maximum recent emails to show across all accounts
            (default: 10). Hard-clamped to
            ``SCAN_BOUNDS["INBOX_HARD_CEILING"]`` (50): the recent pass reads
            five properties per message, so an unclamped value presents as a
            hang rather than an error on a 25K-message Exchange inbox. ``0``
            (or any non-positive value) keeps its meaning of "skip the recent
            block" and is not floored to 1. When the clamp fires, JSON mode
            gains ``max_recent_clamped=True``, ``max_recent_requested``, and
            ``max_recent_clamp_note``, and text mode appends a
            ``RECENT PREVIEW TRUNCATED`` warning, so a capped list is never
            returned as if it were complete.
        max_mailboxes: Maximum top-level mailboxes to enumerate per account
            (default: 100). When the cap fires, the affected account's data will
            show ``mailboxes_truncated=True`` in JSON mode and a warning in the
            errors field. On Exchange accounts with deep nested folders or Gmail
            with many labels, uncapped mailbox enumeration can exceed the 120s
            timeout from sheer property-read volume.
        timeout: Optional per-account AppleScript timeout in seconds
            (default: 180s).
        include_draft_state: When True (default), correlate each recent row
            against a bounded per-account Drafts snapshot and populate
            ``has_draft`` (JSON: true/false/null; text: ``[HAS DRAFT]``).
            ``was_replied_to`` is always present regardless (native
            property, no extra call). False skips the Drafts scan: JSON's
            ``draft_scan.status`` becomes ``"skipped"``, ``has_draft`` null.

    Returns:
        Comprehensive overview including unread counts, optional mailbox
        structure, recent preview, and optional AI suggestions. JSON mode
        returns a structured dict whose recent-email rows always carry
        ``was_replied_to`` (bool) and ``has_draft`` (bool or null), plus a
        top-level ``draft_scan`` object: ``{"status": "ok"|"error"|
        "skipped", "scanned": N, "accounts": [...]}``. Text mode tags
        matching recent lines with ``[REPLIED]``/``[HAS DRAFT]``.

        JSON mode also carries ``unread_count_source``
        (``"mail_cached_aggregate"``), ``unread_count_measured`` (``False``),
        and ``unread_count_note`` at the envelope, repeats the first two on each
        account row, and adds ``unread_count_suspect`` /
        ``unread_count_suspect_reason`` / ``unread_count_suspect_detail``
        wherever a cached count is provably wrong. Text mode marks each unread
        number ``[Mail cached, unverified]`` or ``[Mail cached, SUSPECT]`` and
        prints the same note.
    """
    if output_format not in {"text", "compact", "json"}:
        return "Error: Invalid output_format. Use: text, compact, json"

    # Clamp before anything else so every downstream consumer — script builder,
    # formatters, and the early-return error payloads — sees one effective
    # value, and the response reports the cap instead of silently shrinking the
    # caller's request.
    requested_max_recent = max_recent
    max_recent = _clamp_max_recent(max_recent)
    clamp_note = (
        _max_recent_clamp_note(requested_max_recent, max_recent) if max_recent != requested_max_recent else None
    )

    def finish(payload: str | dict[str, Any]) -> str | dict[str, Any]:
        """Attach the ``max_recent`` clamp disclosure to a finished response."""
        if clamp_note is None:
            return payload
        if isinstance(payload, dict):
            payload["max_recent_requested"] = requested_max_recent
            payload["max_recent_clamped"] = True
            payload["max_recent_clamp_note"] = clamp_note
            return payload
        return f"{payload}\n\n\u26a0 RECENT PREVIEW TRUNCATED — {clamp_note}"

    if account:
        validation_timeout = 30 if timeout is None else min(timeout, 30)
        account_err = inbox.validate_account_name(account, timeout=validation_timeout)
        if account_err:
            if output_format == "json":
                return finish(
                    _overview_json_error(
                        "account_not_found",
                        account=account,
                        include_mailboxes=include_mailboxes,
                        include_recent=include_recent,
                        include_suggestions=include_suggestions,
                        max_recent=max_recent,
                    )
                )
            return account_err
        accounts_to_query = [account]
    else:
        try:
            accounts_to_query = await asyncio.to_thread(inbox._list_mail_accounts, timeout)
        except AppleScriptTimeout:
            if output_format == "json":
                return finish(
                    _overview_json_error(
                        "account_listing_timeout",
                        account=account,
                        include_mailboxes=include_mailboxes,
                        include_recent=include_recent,
                        include_suggestions=include_suggestions,
                        max_recent=max_recent,
                        message="Error: Mail account listing timed out",
                        errors=["__account_listing__"],
                    )
                )
            return "Error: Mail account listing timed out"

    if not accounts_to_query:
        if output_format == "json":
            return finish(
                _format_overview_json(
                    [],
                    [],
                    account=account,
                    include_mailboxes=include_mailboxes,
                    include_recent=include_recent,
                    include_suggestions=include_suggestions,
                    max_recent=max_recent,
                )
            )
        return finish(_format_overview([], [], compact=output_format == "compact"))

    async def run_one(acct: str) -> tuple[str, str | AppleScriptTimeout]:
        try:
            return acct, await asyncio.to_thread(
                _run_overview_one,
                acct,
                timeout,
                include_mailboxes,
                include_recent,
                max_recent,
                max_mailboxes,
            )
        except AppleScriptTimeout:
            return acct, AppleScriptTimeout(acct)

    results = [await run_one(a) for a in accounts_to_query]

    parsed: list[dict[str, Any]] = []
    errors: list[str] = []
    for acct, outcome in results:
        if isinstance(outcome, AppleScriptTimeout):
            errors.append(acct)
            continue
        parsed_acct = _parse_overview_account(outcome)
        if parsed_acct.get("parse_errors"):
            errors.extend(parsed_acct["parse_errors"])
        parsed.append(parsed_acct)

    # has_draft correlation runs one account at a time (each `parsed` entry
    # already scopes its own "recent" rows to one account), sharing a single
    # snapshot cache so a repeated account across calls is never re-scanned.
    draft_timeout = timeout if timeout is not None else 60
    snapshots: dict[str, DraftsSnapshot] = {}
    for parsed_acct_row in parsed:
        if parsed_acct_row.get("error"):
            continue
        snapshots = annotate_rows_with_reply_state(
            parsed_acct_row.get("recent", []),
            runner=inbox.run_applescript,
            timeout=draft_timeout,
            include_draft_state=include_draft_state,
            account=parsed_acct_row.get("account"),
            snapshots=snapshots,
        )
    draft_scan = build_draft_scan_status(snapshots)

    if output_format == "json":
        return finish(
            _format_overview_json(
                parsed,
                errors,
                account=account,
                include_mailboxes=include_mailboxes,
                include_recent=include_recent,
                include_suggestions=include_suggestions,
                max_recent=max_recent,
                draft_scan=draft_scan,
            )
        )

    return finish(
        _format_overview(
            parsed,
            errors,
            include_mailboxes=include_mailboxes,
            include_recent=include_recent,
            include_suggestions=include_suggestions,
            max_recent=max_recent,
            compact=output_format == "compact",
        )
    )
