"""``move_email`` tool and its id-based mover.

Patched names (``run_applescript``, ``_search_mail_records``, ``validate_account_name``)
are referenced via the ``manage`` facade so existing ``patch('...tools.manage.<name>')``
seams keep working."""

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error, target_selector_deprecated_error
from apple_mail_mcp.bounded_scan import MAX_WHOSE_IDS, build_whose_id_list
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    build_mailbox_ref,
    escape_applescript,
    inject_preferences,
    normalize_message_ids,
    normalize_search_terms,
)
from apple_mail_mcp.server import WRITE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import manage
from apple_mail_mcp.tools.manage.helpers import (
    _check_message_ids_cap,
    _date_from_for_recent_days,
    _date_to_for_older_than,
    _deprecated_target_selectors,
    _filter_scan_disabled_error,
    _format_dry_run_records,
    _search_message_ids,
    _with_filter_scan_warning,
)

# Ceiling for `max_moves`. Deliberately `MAX_WHOSE_IDS` (50) and not a
# `SCAN_BOUNDS` key: no entry in `SCAN_BOUNDS` describes a move, and `TRASH_SCAN`
# is trash-specific. 50 is the cap this tool *already* enforces on its id-direct
# path through `_check_message_ids_cap`, and the filter-scan path can resolve at
# most `SCAN_BOUNDS["SEARCH_HARD_CEILING"]` (also 50) ids before recursing into
# that same capped path. A larger `max_moves` is therefore unreachable on either
# path, so clamping here discards a nonsense value without narrowing any working
# range. Tying the ceiling to `MAX_WHOSE_IDS` also means the clamp and the id-count
# cap can never drift apart.
MAX_MOVES_CEILING = MAX_WHOSE_IDS


def _nonpositive_max_moves_error(max_moves: int) -> str:
    """Structured refusal for a `max_moves` that cannot be honored at all.

    Refusing rather than clamping up to 1 is the deliberate half of this fix.
    `max_moves` bounds a *mutation*, and clamping a non-positive bound to 1 would
    move one message under a limit the caller set to "none" — the same defect class
    as the AppleScript index clamp, relocated into Python and harder to see. An
    oversized bound has an obvious intent to honor partially ("as many as you can");
    a non-positive one has none.

    `UNBOUNDED_SCAN_REQUIRED` rather than a new code: `list_inbox_emails` already
    answers `max_emails <= 0` with it and `search_emails` answers a non-positive
    `limit` the same way, so a non-positive page/batch size is inside the code's
    established meaning.
    """
    return serialize_tool_error(
        ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=(
                f"move_email refuses a non-positive max_moves (got max_moves={max_moves}); "
                f"pass max_moves between 1 and {MAX_MOVES_CEILING}"
            ),
            remediation={
                "preferred": f"Pass max_moves=10 (valid range 1-{MAX_MOVES_CEILING})",
                "note": (
                    "max_moves=0 and max_moves=-1 are not 'no limit'. Both build a slice "
                    "Mail resolves to real messages, so a move would happen under a bound "
                    "you set to none."
                ),
            },
        )
    )


def _max_moves_clamp_note(requested: int, effective: int) -> str:
    """One-line requested-vs-effective disclosure, or empty when nothing was clamped.

    A clamp that silently caps a mutation below the requested number is a partial
    action reported as complete. Every clamped call names the rejected request next
    to the bound actually applied, so `TOTAL: 50 email(s) moved` can never be
    mistaken for the caller's own 10000.
    """
    if requested == effective:
        return ""
    return f"(max_moves={requested} requested, clamped to {effective}; valid range 1-{MAX_MOVES_CEILING})"


def _move_email_by_message_ids(
    *,
    account: str,
    from_mailbox: str,
    to_mailbox: str,
    message_ids: list[str],
    max_moves: int,
    dry_run: bool,
    timeout: int,
    dest_ref: str,
    requested_max_moves: int | None = None,
) -> str:
    normalized_ids = normalize_message_ids(message_ids)
    if not normalized_ids:
        return "Error: 'message_ids' must contain one or more numeric Mail ids"
    cap_error = _check_message_ids_cap(normalized_ids, "move_email")
    if cap_error:
        return cap_error

    safe_account = escape_applescript(account)
    safe_from = escape_applescript(from_mailbox)
    safe_to = escape_applescript(to_mailbox)
    id_condition = build_whose_id_list(normalized_ids)
    mode_label = (
        f"DRY RUN - PREVIEW MOVE BY IDS: {safe_from} -> {safe_to}"
        if dry_run
        else f"MOVING EMAILS BY IDS: {safe_from} -> {safe_to}"
    )
    move_action = "" if dry_run else "move aMessage to destMailbox"
    result_prefix = "Would move" if dry_run else "Moved"
    dest_setup = (
        ""
        if dry_run
        else f"""
                set destMailbox to {dest_ref}"""
    )
    clamp_note = _max_moves_clamp_note(
        requested_max_moves if requested_max_moves is not None else max_moves,
        max_moves,
    )
    clamp_footer = (
        f"""
                set outputText to outputText & "   {clamp_note}" & return"""
        if clamp_note
        else ""
    )

    # The `items 1 thru {max_moves}` slice below is safe only because `move_email`
    # clamps the bound to 1..MAX_MOVES_CEILING first. `matchingMessages` is a local
    # AppleScript list of message specifiers, not a Mail `messages` element, so plain
    # list semantics apply and they are unforgiving (probed read-only inside a
    # `tell application "Mail"` block):
    #   * `items 1 thru 0` raises -1728, which this block's `on error` reports as a
    #     mailbox-naming problem rather than as a bad bound.
    #   * `items 1 thru -1` is end-relative and returns the ENTIRE list, and its
    #     `count > -1` guard is always true, so the documented safety limit inverted
    #     into "move everything matched".
    #   * `items 1 thru -2` silently drops the LAST matched id and still prints
    #     "(max_moves limit reached)" — a partial move reported as a capped one.
    # With the bound >= 1 the guard admits the slice only when `count > max_moves`,
    # i.e. `count >= max_moves + 1 >= 2`, so the indices always land inside 1..count.
    # And unlike `count of messages of <mailbox>` (which reads stale-high) this is an
    # exact local list count, so the clamp plus the enclosing `try` covers the site.
    script = f'''
    tell application "Mail"
        with timeout of {timeout} seconds
            set outputText to "{mode_label}" & return & return
            set moveCount to 0

            try
                set targetAccount to account "{safe_account}"
                {build_mailbox_ref(from_mailbox, var_name="sourceMailbox")}
                {dest_setup}

                set matchingMessages to every message of sourceMailbox whose {id_condition}
                if (count of matchingMessages) > {max_moves} then
                    set matchingMessages to items 1 thru {max_moves} of matchingMessages
                end if

                repeat with aMessage in matchingMessages
                    try
                        set messageSubject to subject of aMessage
                        set messageSender to sender of aMessage
                        set messageDate to date received of aMessage

                        {move_action}

                        set outputText to outputText & "{result_prefix}: " & messageSubject & return
                        set outputText to outputText & "   From: " & messageSender & return
                        set outputText to outputText & "   Date: " & (messageDate as string) & return & return

                        set moveCount to moveCount + 1
                    end try
                end repeat

                set outputText to outputText & "========================================" & return
                set outputText to outputText & "REQUESTED IDS: {len(normalized_ids)}" & return
                set outputText to outputText & "TOTAL: " & moveCount & " email(s) {result_prefix.lower()}" & return
                if moveCount >= {max_moves} then
                    set outputText to outputText & "(max_moves limit reached)" & return
                end if{clamp_footer}
                set outputText to outputText & "========================================" & return

            on error errMsg
                return "Error: " & errMsg & return & "Check that account and mailbox names are correct. For nested mailboxes, use '/' separator."
            end try

            return outputText
        end timeout
    end tell
    '''

    try:
        return manage.run_applescript(script, timeout=timeout)
    except AppleScriptTimeout:
        return (
            f"Error: move_email timed out after {timeout}s on account "
            f"'{account}'. Retry with a larger timeout or tighter filters."
        )


@mcp.tool(annotations=WRITE_TOOL_ANNOTATIONS, title="Move Email")
@inject_preferences
def move_email(
    account: str | None = None,
    to_mailbox: str = "",
    message_ids: list[str] | None = None,
    subject_keyword: str | None = None,
    from_mailbox: str = "INBOX",
    max_moves: int = 50,
    subject_keywords: list[str] | None = None,
    sender: str | None = None,
    older_than_days: int | None = None,
    dry_run: bool = False,
    only_read: bool = False,
    recent_days: float = 2.0,
    allow_filter_scan: bool = False,
    timeout: int | None = None,
) -> str:
    """
      Move email(s) by exact ``message_ids`` (fast) or, rarely, by date/bulk filters.

    Preferred: pass ``message_ids`` from ``list_inbox_emails`` or ``search_emails``.
    ``subject_keyword``, ``subject_keywords``, and ``sender`` are deprecated target
    selectors and return ``TARGET_SELECTOR_DEPRECATED`` even when
    ``allow_filter_scan=True``. Date/bulk paths (``older_than_days``,
    ``apply_to_all``) require ``allow_filter_scan=True`` (slow on large mailboxes).
    Use ``dry_run=True`` with ``message_ids`` for a fast preview without moving.

      When ``message_ids`` is provided, moves exact IDs and ignores keyword/sender
      filters. When ``account`` is None the configured ``DEFAULT_MAIL_ACCOUNT`` is used.

      Args:
          account: Account name (e.g., "Gmail", "Work"). Defaults to DEFAULT_MAIL_ACCOUNT.
          to_mailbox: Destination mailbox name. For nested mailboxes, use "/" separator (e.g., "Projects/Amplify Impact")
          message_ids: List of exact Mail message ids (preferred path)
          subject_keyword: Deprecated schema-compat selector. Returns
              ``TARGET_SELECTOR_DEPRECATED`` when ``message_ids`` is omitted.
          from_mailbox: Source mailbox name (default: "INBOX")
          max_moves: Maximum number of emails to move (default: 50, safety limit).
              Valid range 1-50. A non-positive ``max_moves`` is refused with
              ``UNBOUNDED_SCAN_REQUIRED`` before any AppleScript runs: 0 and -1 are
              not "no limit", they build a slice Mail resolves to real messages. A
              value above 50 is clamped (50 is already the hard cap on
              ``message_ids`` per call, so nothing above it was reachable) and the
              response names both the requested and the effective bound.
          subject_keywords: Deprecated schema-compat selector (same as subject_keyword).
          sender: Deprecated schema-compat selector. Returns
              ``TARGET_SELECTOR_DEPRECATED`` when ``message_ids`` is omitted.
          older_than_days: Optional age filter - only move emails older than N days
              (requires ``allow_filter_scan=True`` when ``message_ids`` is omitted).
              Must be positive; a zero or negative value is treated as absent rather
              than as an empty date window.
          dry_run: If True, preview without acting. Fast with message_ids; slow with filters.
          only_read: If True, only move emails that have been read (default: False)
          recent_days: Recent window when using date/bulk filter scan (default: 2.0).
          allow_filter_scan: Opt in to slow date/bulk filter scans only (default: False).
              Does not enable subject/sender selectors.
          timeout: Optional AppleScript timeout in seconds (default: 300s).

      Returns:
          Confirmation message with details of moved emails
    """
    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return "Error: account is required (and no DEFAULT_MAIL_ACCOUNT configured)."
    if not to_mailbox:
        return "Error: to_mailbox is required."
    deprecated_selectors = _deprecated_target_selectors(
        subject_keyword=subject_keyword,
        subject_keywords=subject_keywords,
        sender=sender,
    )
    if message_ids is None and deprecated_selectors:
        return target_selector_deprecated_error(
            "move_email",
            deprecated_selectors,
            preferred="Call search_emails(...) or list_inbox_emails(...) first, then pass message_ids=[...].",
            discovery="search_emails(..., recent_days=..., limit=...) or list_inbox_emails(...)",
            exact_selector="message_ids",
        )

    # Validate the only bound this tool exposes before any Mail round trip,
    # including the account probe, so a nonsense bound costs nothing — the same
    # reasoning `manage_trash` applies to its `action` check. `max_moves` had no
    # floor, no ceiling and no rejection, and three consumers read it raw:
    #   * the id-direct `items 1 thru {max_moves}` slice (see the template above),
    #   * the filter-scan execute path as `_search_message_ids(limit=max_moves)`,
    #   * the dry-run path as `limit=max_moves + 1`, one value lower.
    # The search dispatch seam now raises `ValueError` for `limit < 1`, which was
    # strictly better than the silent wrong move it replaced but surfaced here as a
    # raw exception (only `AppleScriptTimeout` is caught), and it never covered the
    # id-direct path at all. Both are handled here instead, once, ahead of every
    # branch including the recursive id-direct call at the end of this function.
    if max_moves <= 0:
        return _nonpositive_max_moves_error(max_moves)
    requested_max_moves = max_moves
    max_moves = min(max_moves, MAX_MOVES_CEILING)

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = manage.validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return account_err

    safe_to = escape_applescript(to_mailbox)
    effective_timeout = timeout if timeout is not None else 300

    mailbox_parts = to_mailbox.split("/")
    if len(mailbox_parts) > 1:
        dest_ref = f'mailbox "{escape_applescript(mailbox_parts[-1])}" of '
        for i in range(len(mailbox_parts) - 2, -1, -1):
            dest_ref += f'mailbox "{escape_applescript(mailbox_parts[i])}" of '
        dest_ref += "targetAccount"
    else:
        dest_ref = f'mailbox "{safe_to}" of targetAccount'

    if message_ids is not None:
        return _move_email_by_message_ids(
            account=account,
            from_mailbox=from_mailbox,
            to_mailbox=to_mailbox,
            message_ids=message_ids,
            max_moves=max_moves,
            dry_run=dry_run,
            timeout=effective_timeout,
            dest_ref=dest_ref,
            requested_max_moves=requested_max_moves,
        )

    # A non-positive `older_than_days` is "absent", never "an empty window".
    # `_date_to_for_older_than` already returns None for any value <= 0, while
    # `effective_recent_days` below is zeroed whenever `older_than_days is not None`
    # and the UNBOUNDED_SCAN_REQUIRED guard only fires when it `is None`. A negative
    # value therefore reached the search with date_from=None AND date_to=None — no
    # window at all — after discarding the caller's `recent_days`, so a request
    # phrased "move mail older than N days" targeted the NEWEST messages instead.
    # (`older_than_days=0` was already caught by the falsy check below; only negative
    # values slipped through.) Normalizing here restores both behaviors at once.
    if older_than_days is not None and older_than_days <= 0:
        older_than_days = None

    subject_terms = normalize_search_terms(subject_keyword, subject_keywords)
    if not subject_terms and not sender and not older_than_days:
        return (
            "Error: Pass message_ids=[...] (preferred), or older_than_days with "
            "allow_filter_scan=True for approved date/bulk moves. subject_keyword and "
            "sender are deprecated (TARGET_SELECTOR_DEPRECATED). "
            "This prevents accidentally moving everything."
        )

    if not allow_filter_scan:
        return _filter_scan_disabled_error("move_email")

    effective_recent_days = recent_days if older_than_days is None else 0

    # Refuse unbounded scans on destructive tools. The id-direct path (above)
    # is always safe. older_than_days provides a date_to bound so it is safe
    # even when recent_days=0; only refuse when BOTH are absent.
    if older_than_days is None and recent_days <= 0:
        tool_error = ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=(
                "move_email refuses to scan without a date window; "
                "pass recent_days=7 or older_than_days=30 or message_ids=[...]"
            ),
            remediation={
                "preferred": "Pass recent_days=7 or older_than_days=30 or message_ids=[...]",
                "note": "Full-mailbox scans are disabled; bound this call.",
            },
        )
        return serialize_tool_error(tool_error)

    if dry_run:
        try:
            records = manage._search_mail_records(
                account=account,
                mailbox=from_mailbox,
                subject_terms=subject_terms or None,
                sender=sender,
                read_status="read" if only_read else "all",
                date_from=_date_from_for_recent_days(effective_recent_days),
                date_to=_date_to_for_older_than(older_than_days),
                include_content=False,
                offset=0,
                limit=max_moves + 1,
                timeout=timeout if timeout is not None else 45,
                recent_days=effective_recent_days,
            )
        except AppleScriptTimeout:
            return _with_filter_scan_warning(
                f"Error: move_email dry-run timed out on account '{account}'. "
                "Retry with message_ids=[...] or a larger timeout."
            )
        preview = _format_dry_run_records(
            f"DRY RUN - PREVIEW MOVE: {from_mailbox} -> {to_mailbox}",
            records,
            "Would move",
            max_moves,
        )
        # The preview is what the caller reads before authorizing the real move, so
        # it carries the same requested-vs-effective disclosure the execute path
        # emits from AppleScript. Otherwise a clamped preview would understate the
        # cap and then the execute call would appear to change the rules.
        clamp_note = _max_moves_clamp_note(requested_max_moves, max_moves)
        if clamp_note:
            preview = f"{preview}\n{clamp_note}"
        return _with_filter_scan_warning(preview)

    search_timeout = timeout if timeout is not None else min(effective_timeout, 120)
    try:
        resolved_ids = _search_message_ids(
            account=account,
            mailbox=from_mailbox,
            subject_terms=subject_terms or None,
            sender=sender,
            read_status="read" if only_read else "all",
            date_from=_date_from_for_recent_days(effective_recent_days),
            date_to=_date_to_for_older_than(older_than_days),
            limit=max_moves,
            timeout=search_timeout,
            recent_days=effective_recent_days,
        )
    except AppleScriptTimeout:
        return _with_filter_scan_warning(
            f"Error: move_email timed out on account '{account}'. "
            "Prefer message_ids=[...] or retry with a larger timeout."
        )

    if not resolved_ids:
        return _with_filter_scan_warning(f"No matching emails found in {from_mailbox} for account '{account}'.")

    return _with_filter_scan_warning(
        _move_email_by_message_ids(
            account=account,
            from_mailbox=from_mailbox,
            to_mailbox=to_mailbox,
            message_ids=resolved_ids,
            max_moves=max_moves,
            dry_run=False,
            timeout=effective_timeout,
            dest_ref=dest_ref,
            requested_max_moves=requested_max_moves,
        )
    )
