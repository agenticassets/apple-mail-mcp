"""``manage_trash`` tool: move-to-trash, delete-permanent, and empty-trash via id-direct or filter-scan paths.

Patched names (``run_applescript``, ``_search_mail_records``, ``validate_account_name``)
are referenced via the ``manage`` facade so existing ``patch('...tools.manage.<name>')``
seams keep working."""

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error, target_selector_deprecated_error
from apple_mail_mcp.bounded_scan import MAX_WHOSE_IDS, build_whose_id_list
from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    build_mailbox_ref,
    escape_applescript,
    inject_preferences,
    normalize_message_ids,
    normalize_search_terms,
)
from apple_mail_mcp.server import DESTRUCTIVE_TOOL_ANNOTATIONS, mcp
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

_TRASH_ACTIONS = ("move_to_trash", "delete_permanent", "empty_trash")

# Per-path `max_deletes` ceilings. A single ceiling was wrong here because the
# three paths cannot reach the same number:
#   * `empty_trash` slices Mail's own `messages` element of the Trash mailbox with
#     no id list involved, so `SCAN_BOUNDS["TRASH_SCAN"]` (100) is genuinely
#     reachable and stays its ceiling.
#   * The id-direct path caps `message_ids` at `MAX_WHOSE_IDS` (50) through
#     `_check_message_ids_cap`, so at most 50 messages can ever be targeted and
#     51-100 is dead range: `if (count of matchingMessages) > 75` can never be
#     true, making any bound above 50 inert rather than honored.
#   * The filter-scan path resolves ids through `_search_message_ids`, and
#     `search/script.py` applies `min(scan_cap, SCAN_BOUNDS["SEARCH_HARD_CEILING"])`
#     (also 50) to its `messages 1 thru scanUpperBound` candidate slice, so it
#     yields at most 50 records before recursing into that same 50-capped path.
# Advertising a flat 1-100 on all three therefore gave the id and filter callers a
# number the tool silently ignores. Tying the non-`empty_trash` ceiling to
# `MAX_WHOSE_IDS` — exactly what `move_email.MAX_MOVES_CEILING` does, and for the
# same reason — also means the clamp and the id-count cap can never drift apart.
MAX_DELETES_CEILING_EMPTY_TRASH = SCAN_BOUNDS["TRASH_SCAN"]
MAX_DELETES_CEILING_BY_IDS = MAX_WHOSE_IDS


def _max_deletes_ceiling(action: str) -> int:
    """Return the largest `max_deletes` the path selected by *action* can honor."""
    return MAX_DELETES_CEILING_EMPTY_TRASH if action == "empty_trash" else MAX_DELETES_CEILING_BY_IDS


def _nonpositive_max_deletes_error(max_deletes: int, ceiling: int) -> str:
    """Structured refusal for a `max_deletes` that cannot be honored at all.

    Refusing rather than clamping up to 1 is the point. `max_deletes` bounds an
    irreversible mutation, and `max(1, ...)` deleted one message under a limit the
    caller set to "none" — the same defect class as the AppleScript index clamp this
    tool guards against, relocated into Python where no AppleScript probe can catch
    it. Measured on the pre-fix code: `max_deletes=0` on `empty_trash` emitted
    `messages 1 thru 1 of trashMailbox` followed by `delete aMessage`.

    An oversized bound has an obvious intent to honor partially ("as many as you
    can"); a non-positive one has none. So the ceiling still clamps and only the
    floor refuses.

    `UNBOUNDED_SCAN_REQUIRED` rather than a new code, and byte-identical in shape to
    `move_email._nonpositive_max_moves_error`: the two destructive tools must answer
    the same condition the same way, and `list_inbox_emails` / `search_emails`
    already answer a non-positive page size with this code.
    """
    return serialize_tool_error(
        ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=(
                f"manage_trash refuses a non-positive max_deletes (got max_deletes={max_deletes}); "
                f"pass max_deletes between 1 and {ceiling}"
            ),
            remediation={
                "preferred": f"Pass max_deletes=5 (valid range 1-{ceiling})",
                "note": (
                    "max_deletes=0 and max_deletes=-1 are not 'no limit'. Both build a slice "
                    "Mail resolves to real messages, so a delete would happen under a bound "
                    "you set to none."
                ),
            },
        )
    )


def _invalid_action_error(action: str) -> str:
    """Single spelling of the invalid-action error, shared by both target paths.

    The id-direct path validated `action` and the filter path did not: its branch
    chain fell through to `move_to_trash`, so a typo performed a destructive move
    instead of failing. Both paths now return this string, so the same typo cannot
    be caught on one path and silently acted on by the other.
    """
    return f"Error: Invalid action '{action}'. Use: {', '.join(_TRASH_ACTIONS)}"


def _timed_out_error(account: str, effective_timeout: int) -> str:
    """Single spelling of the timeout message every ``manage_trash`` path returns."""
    return f"Error: manage_trash timed out after {effective_timeout}s on account '{account}'."


def _run_trash_script(script: str, *, account: str, effective_timeout: int) -> str:
    """Run one built script, turning a timeout into the shared error string.

    ``manage.run_applescript`` stays resolved through the facade at call time so
    ``patch('...tools.manage.run_applescript')`` keeps working.
    """
    try:
        return manage.run_applescript(script, timeout=effective_timeout)
    except AppleScriptTimeout:
        return _timed_out_error(account, effective_timeout)


@mcp.tool(annotations=DESTRUCTIVE_TOOL_ANNOTATIONS, title="Manage Trash")
@inject_preferences
def manage_trash(
    account: str | None = None,
    action: str = "move_to_trash",
    message_ids: list[str] | None = None,
    subject_keyword: str | None = None,
    subject_keywords: list[str] | None = None,
    sender: str | None = None,
    mailbox: str = "INBOX",
    max_deletes: int = 5,
    confirm_empty: bool = False,
    apply_to_all: bool = False,
    older_than_days: int | None = None,
    dry_run: bool = True,
    recent_days: float = 2.0,
    allow_filter_scan: bool = False,
    timeout: int | None = None,
) -> str:
    """
    Manage trash operations - delete emails or empty trash.

    Preferred: pass ``message_ids`` from a prior list/search call.
    ``subject_keyword``, ``subject_keywords``, and ``sender`` are deprecated target
    selectors and return ``TARGET_SELECTOR_DEPRECATED`` even when
    ``allow_filter_scan=True``. Date/bulk paths (``older_than_days``,
    ``apply_to_all``) require ``allow_filter_scan=True`` (slow on large mailboxes).
    When dry_run=True (default), previews without acting; fast with message_ids.

    When ``message_ids`` is provided for ``move_to_trash`` or ``delete_permanent``,
    targets exact IDs and ignores keyword/sender filters.

    When ``account`` is None the configured ``DEFAULT_MAIL_ACCOUNT`` is used.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to DEFAULT_MAIL_ACCOUNT.
        action: Action to perform: "move_to_trash", "delete_permanent", "empty_trash"
        message_ids: List of exact Mail message ids (preferred path)
        subject_keyword: Deprecated schema-compat selector. Returns
            ``TARGET_SELECTOR_DEPRECATED`` when ``message_ids`` is omitted.
        subject_keywords: Deprecated schema-compat selector (same as subject_keyword).
        sender: Deprecated schema-compat selector. Returns
            ``TARGET_SELECTOR_DEPRECATED`` when ``message_ids`` is omitted.
        mailbox: Source mailbox (default: "INBOX", not used for empty_trash or delete_permanent)
        max_deletes: Maximum number of emails to delete (safety limit, default: 5).
            A non-positive ``max_deletes`` is refused with ``UNBOUNDED_SCAN_REQUIRED``
            before any AppleScript runs and before the account probe: 0 and -1 are not
            "no limit", they build a slice Mail resolves to real messages, so a
            permanent delete would happen under a bound set to none.
            The upper bound is per path, because the paths cannot reach the same
            number: ``empty_trash`` accepts 1-100, while the ``message_ids`` and
            date/bulk filter paths accept 1-50 (both resolve through the 50-id
            ``message_ids`` cap, so a larger bound was never reachable there). A value
            above the applicable ceiling is clamped, not honored, and ``empty_trash``
            reports the requested and effective bounds in its output.
        confirm_empty: Must be True to execute "empty_trash" action (safety confirmation)
        apply_to_all: Bulk trash without filters (requires allow_filter_scan=True)
        older_than_days: Optional age filter - only affect emails older than N days
            (requires ``allow_filter_scan=True`` when ``message_ids`` is omitted).
            Must be positive; a zero or negative value is treated as absent rather
            than as an empty date window.
        dry_run: If True (default), preview what would be affected without acting
        recent_days: Recent window when using date/bulk filter scan (default: 2.0).
        allow_filter_scan: Opt in to slow date/bulk filter scans only (default: False).
            Does not enable subject/sender selectors.
        timeout: Optional AppleScript timeout in seconds (default: 300s).

    Returns:
        Confirmation message with details of deleted emails
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
    if message_ids is None and action != "empty_trash" and deprecated_selectors:
        return target_selector_deprecated_error(
            "manage_trash",
            deprecated_selectors,
            preferred="Call search_emails(...) or list_inbox_emails(...) first, then pass message_ids=[...].",
            discovery="search_emails(..., recent_days=..., limit=...) or list_inbox_emails(...)",
            exact_selector="message_ids",
        )
    # Validate the action before any Mail round trip, including the account probe,
    # so a typo costs nothing and can never select a destructive branch.
    if action not in _TRASH_ACTIONS:
        return _invalid_action_error(action)

    # Validate the destructive slice bound next, still before any Mail round trip
    # including the account probe, so a nonsense bound costs nothing — the same
    # reasoning the `action` check above applies, and the same position `move_email`
    # validates `max_moves` from. This sits ahead of the branch that picks a target
    # path so all three actions and both target paths answer identically; a guard
    # inside any single branch would leave the others open. It also lands before
    # every downstream reader: both `1 thru {max_deletes}` slice sites, the search
    # `limit`, the dry-run `limit + 1` probe, and the recursive id-direct call at the
    # end of this function.
    #
    # AppleScript slice indices are end-relative and clamp silently, which turns an
    # unvalidated bound into a destructive one (probed read-only on On My Mac,
    # Exchange, Gmail IMAP, and iCloud; identical on all four):
    #   * `messages 1 thru -1` spans the ENTIRE mailbox, so `max_deletes=-1` — a
    #     plausible spelling of "no limit" — passed the `messageCount > -1` guard and
    #     permanently deleted all of Trash.
    #   * `messages 1 thru 0` does not raise on a non-empty mailbox; index 0 clamps
    #     to 1 and exactly ONE message comes back, so `max_deletes=0` deleted one
    #     message when the caller asked for none.
    #   * An out-of-range upper bound raises -1719 `Invalid index.` rather than
    #     clamping, and `count of messages` can read stale-high, so slice sites keep
    #     their surrounding `try` as well as this clamp.
    # Both non-positive cases are refused outright rather than floored to 1 (see
    # `_nonpositive_max_deletes_error`); only the ceiling clamps.
    max_deletes_ceiling = _max_deletes_ceiling(action)
    if max_deletes <= 0:
        return _nonpositive_max_deletes_error(max_deletes, max_deletes_ceiling)
    requested_max_deletes = max_deletes
    max_deletes = min(max_deletes, max_deletes_ceiling)
    max_deletes_clamped = requested_max_deletes != max_deletes

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = manage.validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return account_err

    # Escape all user inputs for AppleScript
    safe_account = escape_applescript(account)
    subject_terms = normalize_search_terms(subject_keyword, subject_keywords)
    effective_timeout = timeout if timeout is not None else 300
    # A non-positive `older_than_days` is "absent", never "an empty window".
    # `_date_to_for_older_than` already returns None for any value <= 0, while
    # `effective_recent_days` below is zeroed whenever `older_than_days is not None`
    # and the UNBOUNDED_SCAN_REQUIRED guard only fires when it `is None`. Such a value
    # therefore reached the search with date_from=None AND date_to=None — no window at
    # all — after discarding the caller's `recent_days`, so a request phrased "purge
    # mail older than N days" targeted the NEWEST messages in Trash, permanently. That
    # is the same defect the `apply_to_all` search routing below closed, reached
    # through the argument's sign instead.
    #
    # Unlike `move_email`, this tool gates its filter path on `apply_to_all` rather
    # than on a falsy `older_than_days`, so `older_than_days=0` was NOT already caught
    # downstream: with `apply_to_all=True` zero reached the same purge. Normalizing
    # both signs here restores the window and the caller's `recent_days` at once.
    # Must precede `effective_recent_days` and the refusal guard, which both read it.
    if older_than_days is not None and older_than_days <= 0:
        older_than_days = None
    effective_recent_days = recent_days if older_than_days is None else 0

    if message_ids is not None:
        if action == "empty_trash":
            return "Error: message_ids cannot be used with empty_trash"

        normalized_ids = normalize_message_ids(message_ids)
        if not normalized_ids:
            return "Error: 'message_ids' must contain one or more numeric Mail ids"
        cap_error = _check_message_ids_cap(normalized_ids, "manage_trash")
        if cap_error:
            return cap_error

        id_condition = build_whose_id_list(normalized_ids)

        if action == "move_to_trash":
            mode_label = "DRY RUN - PREVIEW TRASH BY IDS" if dry_run else "MOVING EMAILS TO TRASH BY IDS"
            move_script = "" if dry_run else "move aMessage to trashMailbox"
            result_verb = "Would trash" if dry_run else "Moved to trash"
            trash_setup = (
                ""
                if dry_run
                else """
                    set trashMailbox to mailbox "Trash" of targetAccount"""
            )
            mailbox_ref = build_mailbox_ref(mailbox, var_name="sourceMailbox")
        elif action == "delete_permanent":
            mode_label = (
                "DRY RUN - PREVIEW PERMANENT DELETE BY IDS" if dry_run else "PERMANENTLY DELETING EMAILS BY IDS"
            )
            move_script = "" if dry_run else "delete aMessage"
            result_verb = "Would permanently delete" if dry_run else "Permanently deleted"
            trash_setup = ""
            mailbox_ref = 'set sourceMailbox to mailbox "Trash" of targetAccount'
        else:
            # Unreachable after the early check above; kept so the branch that binds
            # mode_label / move_script / result_verb stays exhaustive.
            return _invalid_action_error(action)

        script = f'''
        tell application "Mail"
            with timeout of {effective_timeout} seconds
                set outputText to "{mode_label}" & return & return
                set deleteCount to 0

                try
                    set targetAccount to account "{safe_account}"
                    {mailbox_ref}
                    {trash_setup}

                    set matchingMessages to every message of sourceMailbox whose {id_condition}
                    if (count of matchingMessages) > {max_deletes} then
                        set matchingMessages to items 1 thru {max_deletes} of matchingMessages
                    end if

                    repeat with aMessage in matchingMessages
                        try
                            set messageSubject to subject of aMessage
                            set messageSender to sender of aMessage
                            set messageDate to date received of aMessage

                            {move_script}

                            set outputText to outputText & "{result_verb}: " & messageSubject & return
                            set outputText to outputText & "   From: " & messageSender & return
                            set outputText to outputText & "   Date: " & (messageDate as string) & return & return
                            set deleteCount to deleteCount + 1
                        end try
                    end repeat

                    set outputText to outputText & "========================================" & return
                    set outputText to outputText & "REQUESTED IDS: {len(normalized_ids)}" & return
                    set outputText to outputText & "TOTAL: " & deleteCount & " email(s) {result_verb.lower()}" & return
                    set outputText to outputText & "========================================" & return

                on error errMsg
                    return "Error: " & errMsg
                end try

                return outputText
            end timeout
        end tell
        '''

        return _run_trash_script(script, account=account, effective_timeout=effective_timeout)

    # Refuse unbounded scans on destructive filter paths (move_to_trash and
    # delete_permanent). The id-direct path and empty_trash are exempt.
    # older_than_days provides a date_to bound so it is safe even when
    # recent_days=0; only refuse when BOTH are absent.
    if action != "empty_trash" and older_than_days is None and recent_days <= 0:
        tool_error = ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=(
                "manage_trash refuses to scan without a date window; "
                "pass recent_days=7 or older_than_days=30 or message_ids=[...]"
            ),
            remediation={
                "preferred": "Pass recent_days=7 or older_than_days=30 or message_ids=[...]",
                "note": "Full-mailbox scans are disabled; bound this call.",
            },
        )
        return serialize_tool_error(tool_error)

    if action == "empty_trash":
        if not confirm_empty:
            return (
                "Error: empty_trash permanently deletes ALL messages in the trash. Set confirm_empty=True to proceed."
            )
        # dry_run is honored here exactly as on the id-direct path: under a
        # preview no `delete` command is emitted at all, and the output says so.
        # `confirm_empty` remains a separate, additional gate — it is checked
        # above regardless of dry_run.
        mode_label = "DRY RUN - PREVIEW EMPTY TRASH" if dry_run else "EMPTYING TRASH"
        delete_script = "" if dry_run else "delete aMessage"
        result_verb = "Would empty trash for account" if dry_run else "Emptied trash for account"
        count_verb = "Would delete" if dry_run else "Deleted"
        dry_run_footer = (
            """
                    set outputText to outputText & "DRY RUN: nothing was deleted. Pass dry_run=False to actually empty the trash." & return"""
            if dry_run
            else ""
        )
        # Requested-vs-effective disclosure: a clamped bound must never look like the
        # caller's own number. The `(limited by max_deletes=...)` line below reports
        # the effective bound against the true mailbox count, and this line names the
        # rejected request alongside it.
        clamp_footer = (
            f"""
                    set outputText to outputText & "   (max_deletes={requested_max_deletes} requested, clamped to {max_deletes}; valid range 1-{max_deletes_ceiling})" & return"""
            if max_deletes_clamped
            else ""
        )
        script = f'''
        tell application "Mail"
            with timeout of {effective_timeout} seconds
                set outputText to "{mode_label}" & return & return

                try
                    set targetAccount to account "{safe_account}"
                    set trashMailbox to mailbox "Trash" of targetAccount
                    set messageCount to count of messages of trashMailbox
                    set deleteCount to 0

                    if messageCount > {max_deletes} then
                        set trashMessages to messages 1 thru {max_deletes} of trashMailbox
                    else
                        set trashMessages to messages of trashMailbox
                    end if

                    repeat with aMessage in trashMessages
                        {delete_script}
                        set deleteCount to deleteCount + 1
                    end repeat

                    set outputText to outputText & "✓ {result_verb}: {safe_account}" & return
                    set outputText to outputText & "   {count_verb} " & deleteCount & " of " & messageCount & " message(s)" & return
                    if deleteCount < messageCount then
                        set outputText to outputText & "   (limited by max_deletes=" & {max_deletes} & ")" & return
                    end if{clamp_footer}{dry_run_footer}

                on error errMsg
                    return "Error: " & errMsg
                end try

                return outputText
            end timeout
        end tell
        '''
        return _run_trash_script(script, account=account, effective_timeout=effective_timeout)

    if action == "delete_permanent":
        # Safety check: require at least one filter or explicit apply_to_all
        if not subject_terms and not sender and not apply_to_all:
            return (
                "Error: Pass message_ids=[...] (preferred) or apply_to_all=True with "
                "allow_filter_scan=True. subject_keyword and sender are deprecated "
                "(TARGET_SELECTOR_DEPRECATED)."
            )

        if not allow_filter_scan:
            return _filter_scan_disabled_error("manage_trash")

        # delete_permanent always targets Trash, whatever `mailbox` says; the
        # id-direct path it recurses into hardcodes the same mailbox reference.
        # `apply_to_all` here used to hand-roll a script whose target was a bare
        # newest-first `messages 1 thru N of trashMailbox` slice that emitted no date
        # condition at all, so `older_than_days=365` permanently deleted the NEWEST
        # messages in Trash. It now shares the resolve-then-recurse tail below, so the
        # date window always travels with the search.
        scan_mailbox = "Trash"
    else:  # move_to_trash
        # Safety check: require at least one filter or explicit apply_to_all
        has_filter = bool(subject_terms) or bool(sender) or (older_than_days is not None and older_than_days > 0)
        if not has_filter and not apply_to_all:
            return (
                "Error: Pass message_ids=[...] (preferred), older_than_days, or "
                "apply_to_all=True with allow_filter_scan=True. subject_keyword and sender "
                "are deprecated (TARGET_SELECTOR_DEPRECATED)."
            )

        if not allow_filter_scan:
            return _filter_scan_disabled_error("manage_trash")

        if dry_run:
            try:
                records = manage._search_mail_records(
                    account=account,
                    mailbox=mailbox,
                    subject_terms=subject_terms or None,
                    sender=sender,
                    date_from=_date_from_for_recent_days(effective_recent_days),
                    date_to=_date_to_for_older_than(older_than_days),
                    include_content=False,
                    offset=0,
                    limit=max_deletes + 1,
                    timeout=timeout if timeout is not None else 45,
                    recent_days=effective_recent_days,
                )
            except AppleScriptTimeout:
                return _with_filter_scan_warning(
                    f"Error: manage_trash dry-run timed out on account '{account}'. "
                    "Prefer message_ids=[...] or a larger timeout."
                )
            return _with_filter_scan_warning(
                _format_dry_run_records(
                    "DRY RUN - PREVIEW TRASH",
                    records,
                    "Would trash",
                    max_deletes,
                )
            )

        # Every move_to_trash preview returned above, so `dry_run` is False from here
        # on and the shared recursion below can pass it through for both actions.
        scan_mailbox = mailbox

    # No AppleScript condition is assembled here on purpose. `_search_message_ids`
    # takes `subject_terms` / `sender` directly and builds its own bounded,
    # loop-variable-bound predicate. This block used to build
    # `contains_any_condition("subject", ...)` -> `subject contains "x"` and then
    # discard it, testing only its truthiness. That string is the AGENTIC-2344
    # shape: a bare property reference, valid only inside a `whose` clause, which
    # raises -1728 on every message inside a `repeat` loop and gets swallowed by
    # the loop's `try`. Keeping a ready-made version of it one line away from a
    # `delete_permanent` path was a standing invitation to splice it in.
    #
    # Every filter-scan mutation — `delete_permanent` and `move_to_trash` alike,
    # including `apply_to_all` with no subject/sender — resolves ids through the
    # audited bounded search first, then recurses into the id-direct path, which owns
    # `dry_run`. Only that path emits a destructive command.
    search_timeout = timeout if timeout is not None else min(effective_timeout, 120)
    try:
        resolved_ids = _search_message_ids(
            account=account,
            mailbox=scan_mailbox,
            subject_terms=subject_terms or None,
            sender=sender,
            date_from=_date_from_for_recent_days(effective_recent_days),
            date_to=_date_to_for_older_than(older_than_days),
            limit=max_deletes,
            timeout=search_timeout,
            recent_days=effective_recent_days,
        )
    except AppleScriptTimeout:
        return _with_filter_scan_warning(_timed_out_error(account, effective_timeout))
    if not resolved_ids:
        return _with_filter_scan_warning(f"No matching emails found in {scan_mailbox} for account '{account}'.")
    return _with_filter_scan_warning(
        manage_trash(
            account=account,
            action=action,
            message_ids=resolved_ids,
            mailbox=scan_mailbox,
            max_deletes=max_deletes,
            dry_run=dry_run,
            timeout=timeout,
        )
    )
