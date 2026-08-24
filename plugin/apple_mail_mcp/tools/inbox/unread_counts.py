"""``get_mailbox_unread_counts`` tool (summary fast path + nested per-mailbox path).

``run_applescript`` and ``validate_account_name`` are routed through the ``inbox``
facade so the existing test patch seams keep firing."""

from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inbox_mailbox_script,
    inject_preferences,
    sanitize_pipe_delimited_field,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import inbox
from apple_mail_mcp.tools.unread_provenance import unread_count_disclosure

#: Namespaced sentinel key carrying the cached-count provenance block. Uses the
#: same dunder convention as the ``__truncated__`` marker below so it cannot
#: collide with a real account or mailbox name.
PROVENANCE_KEY = "__unread_count_provenance__"

#: Per-account list of failure strings, one per read that threw. Same dunder
#: convention as ``__truncated__``: skip it when iterating mailbox names.
ERRORS_KEY = "__errors__"

#: Value written for a mailbox whose ``unread count`` read threw. Copies the
#: ``summary_only`` path, which has always mapped its ``accountName & ":ERROR"``
#: row to ``-1``; a negative count is impossible for a real mailbox, so it can
#: never be confused with the zero-unread mailbox a dropped row used to imitate.
UNREAD_COUNT_UNAVAILABLE = -1

#: Third-field marker for a failed read, mirroring ``summary_only``'s ``ERROR``
#: sentinel. Rows emitted here always carry the Mail error text after the
#: colon; the bare sentinel is accepted on the way back in so a row in the
#: ``summary_only`` spelling is still read as a failure rather than as junk.
_ERROR_VALUE_SENTINEL = "ERROR"
_ERROR_VALUE_PREFIX = f"{_ERROR_VALUE_SENTINEL}:"

#: Row scopes that are not mailboxes: the account block itself, a mailbox whose
#: ``name`` read threw, and a mailbox whose child enumeration threw. They are
#: reported in ``__errors__`` only — writing a ``-1`` count under them would
#: invent a mailbox that does not exist.
_ACCOUNT_SCOPE = "__ACCOUNT__"
_UNNAMED_SCOPE = "__UNNAMED__"
_CHILDREN_SCOPE = "__CHILDREN__"


#: Row expressions for the five failure sites, kept as constants so the
#: ``{_unread_read_error_arm(...)}`` slots inside the script stay one line long.
_ROW_UNNAMED = f'accountName & "|||{_UNNAMED_SCOPE}|||{_ERROR_VALUE_PREFIX}" & errorDetail'
_ROW_MAILBOX = f'accountName & "|||" & mailboxName & "|||{_ERROR_VALUE_PREFIX}" & errorDetail'
_ROW_CHILDREN = f'accountName & "|||" & mailboxName & "/{_CHILDREN_SCOPE}|||{_ERROR_VALUE_PREFIX}" & errorDetail'
_ROW_CHILD = f'accountName & "|||" & mailboxName & "/" & subName & "|||{_ERROR_VALUE_PREFIX}" & errorDetail'
_ROW_ACCOUNT = f'accountName & "|||{_ACCOUNT_SCOPE}|||{_ERROR_VALUE_PREFIX}" & errorDetail'


def _is_sentinel_scope(mailbox_name: str) -> bool:
    """True when the row's leaf path segment is a dunder scope, not a mailbox."""
    leaf = mailbox_name.rsplit("/", 1)[-1]
    return leaf.startswith("__") and leaf.endswith("__")


def _read_row_value(unread_value: str) -> tuple[int, str | None]:
    """Split a row's third field into ``(count, error_detail)``.

    ``error_detail`` is ``None`` for a normal count. An ``ERROR`` /
    ``ERROR:<detail>`` value (or an unparsable one, which can only mean the
    row shape changed under us) reports a failure instead of crashing the
    tool on ``int()``.
    """
    if unread_value == _ERROR_VALUE_SENTINEL or unread_value.startswith(_ERROR_VALUE_PREFIX):
        return UNREAD_COUNT_UNAVAILABLE, unread_value[len(_ERROR_VALUE_PREFIX) :].strip() or "unknown error"
    try:
        return int(unread_value), None
    except ValueError:
        return UNREAD_COUNT_UNAVAILABLE, f"unparsable unread value {unread_value!r}"


def _unread_read_error_arm(row_expression: str, error_var: str) -> str:
    """Build a reporting ``on error`` arm that emits one failure row.

    The nested path used to wrap the per-account block, the per-mailbox read,
    and the child enumeration in bare ``try`` blocks, so an offline account
    contributed zero rows and no marker, and one throwing mailbox took its
    entire child subtree with it. Every ``try`` now carries this arm instead:
    the Mail error text is sanitized (it is untrusted text joined into a
    ``|||`` row) and emitted as a row the Python side turns into an
    ``__errors__`` entry.
    """
    return f"""on error {error_var}
                            set errorDetail to {error_var} as string
                            {sanitize_pipe_delimited_field("errorDetail")}
                            set end of resultList to {row_expression}"""


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Mailbox Unread Counts")
@inject_preferences
def get_mailbox_unread_counts(
    account: str | None = None,
    include_zero: bool = False,
    summary_only: bool = False,
    max_mailboxes: int = 100,
    timeout: int | None = None,
) -> dict[str, Any]:
    """
    Get Mail's **cached** unread counts per mailbox for one or all accounts.

    Every number here is Mail.app's ``unread count`` mailbox property, a cached
    aggregate — **not a measured count**. It drifts low, sometimes hugely:
    measured 2026-08-17 on a 25,012-message Exchange Inbox, Mail reported 3,236
    unread where per-message truth was 10,016 (a 68% under-report). Report these
    as approximate, never as exact, and never subtract them from a message count
    to claim a read count. When an exact number matters, page bounded
    per-message reads with ``list_inbox_emails(read_status="unread")``.

    This tool reads no per-mailbox message count, so it cannot cross-check
    itself; the response carries the provenance block but never a suspect flag.
    ``list_mailboxes(include_counts=True)``, ``get_inbox_overview``, and
    ``get_statistics`` do read both numbers and will flag a cached count that is
    provably wrong.

    When summary_only=True, returns only per-account inbox unread totals
    (replaces the former get_unread_count tool).

    Args:
        account: Optional account name filter
        include_zero: Whether to include mailboxes with zero unread messages
        summary_only: If True, return only per-account inbox unread totals
                      (flat dict of account name -> unread count)
        max_mailboxes: Maximum number of top-level mailboxes to enumerate per
            account (default: 100). When the cap fires, the account's result
            includes a ``truncated: true`` field. On Exchange accounts with
            deep nested folder trees, or Gmail accounts with 200+ labels,
            exceeding this cap can trigger the 120s timeout from sheer
            property-read volume.
        timeout: Optional AppleScript timeout in seconds (default: 120s).

    Returns:
        If summary_only=False: nested dict keyed by account name then mailbox path
        If summary_only=True: flat dict mapping account names to inbox unread counts

        Both shapes also carry a ``__unread_count_provenance__`` key holding
        ``unread_count_source`` (always ``"mail_cached_aggregate"``),
        ``unread_count_measured`` (always ``False``), and
        ``unread_count_note``. Skip that key when iterating account names.

        **Failed reads are reported, never dropped.** A count of ``-1`` means
        Mail threw while reading that mailbox — the same sentinel the
        ``summary_only`` shape already uses per account — so it can never be
        mistaken for a zero-unread mailbox under the default
        ``include_zero=False``. Each affected account also carries an
        ``__errors__`` list of ``"<scope>: <detail>"`` strings, where *scope*
        is the mailbox path, ``account`` (the whole account block threw, e.g.
        offline or mid-resync), ``<parent>/__CHILDREN__`` (child enumeration
        threw, so that subtree is missing), or ``__UNNAMED__`` (the mailbox's
        own ``name`` read threw). ``__errors__`` and ``__truncated__`` are
        dunder sentinels: skip them when iterating mailbox names. An account
        whose result is only ``__errors__`` returned no usable counts at all —
        do not report it as an inbox with nothing unread.
    """
    if account is None and _server.DEFAULT_MAIL_ACCOUNT:
        account = _server.DEFAULT_MAIL_ACCOUNT

    if account:
        account_err = inbox.validate_account_name(account, timeout=30 if timeout is None else min(timeout, 30))
        if account_err:
            return {"error": "account_not_found", "account": account}

    escaped_account = escape_applescript(account) if account else None
    effective_timeout = timeout if timeout is not None else 120

    # Fast path: summary_only returns just per-account inbox unread totals
    if summary_only:
        summary_account_filter = (
            f'''
                if accountName is not "{escaped_account}" then
                    set shouldIncludeAccount to false
                end if
        '''
            if account
            else ""
        )
        script = f"""
        tell application "Mail"
            set resultList to {{}}
            set allAccounts to every account

            repeat with anAccount in allAccounts
                set accountName to name of anAccount
                set shouldIncludeAccount to true
                {summary_account_filter}

                if shouldIncludeAccount then
                    try
                        {inbox_mailbox_script("inboxMailbox", "anAccount")}
                        set unreadCount to unread count of inboxMailbox
                        set end of resultList to accountName & ":" & unreadCount
                    on error
                        set end of resultList to accountName & ":ERROR"
                    end try
                end if
            end repeat

            set AppleScript's text item delimiters to "|"
            return resultList as string
        end tell
        """
        try:
            result = inbox.run_applescript(script, timeout=effective_timeout)
        except AppleScriptTimeout:
            return {
                "error": "timed_out",
                "message": (
                    "AppleScript timed out while fetching inbox unread counts. Try again or pass a larger `timeout`."
                ),
            }
        flat_counts: dict[str, Any] = {}
        for item in result.split("|"):
            if ":" in item:
                acct_name, count_str = item.split(":", 1)
                if count_str != "ERROR":
                    flat_counts[acct_name] = int(count_str)
                else:
                    flat_counts[acct_name] = -1
        flat_counts[PROVENANCE_KEY] = unread_count_disclosure()
        return flat_counts

    account_filter = (
        f'''
            if accountName is not "{escaped_account}" then
                set shouldIncludeAccount to false
            end if
    '''
        if account
        else ""
    )

    script = f"""
    tell application "Mail"
        set resultList to {{}}
        set allAccounts to every account

        repeat with anAccount in allAccounts
            set accountName to name of anAccount
            set shouldIncludeAccount to true
            {account_filter}

            if shouldIncludeAccount then
                try
                    set accountMailboxes to every mailbox of anAccount
                    set mailboxIndex to 0
                    set accountTruncated to false

                    repeat with aMailbox in accountMailboxes
                        set mailboxIndex to mailboxIndex + 1
                        if mailboxIndex > {max_mailboxes} then
                            set accountTruncated to true
                            exit repeat
                        end if
                        -- One try per read, each with its own reporting arm.
                        -- A single shared bare try dropped the mailbox AND its
                        -- whole child subtree on any throw, and under the
                        -- default include_zero=False that was indistinguishable
                        -- from a zero-unread mailbox.
                        set mailboxName to "{_UNNAMED_SCOPE}"
                        set mailboxNamed to true
                        try
                            set mailboxName to name of aMailbox
                        {_unread_read_error_arm(_ROW_UNNAMED, "nameErr")}
                            set mailboxNamed to false
                        end try
                        if mailboxNamed then
                            -- Always emit the parent row with its own unread count
                            -- (bare name as key, NOT prefixed).  Exchange INBOX has
                            -- messages AND children — skipping the parent silently
                            -- drops its own unread count.
                            try
                                set unreadCount to unread count of aMailbox
                                if {str(include_zero).lower()} or unreadCount > 0 then
                                    set end of resultList to accountName & "|||" & mailboxName & "|||" & unreadCount
                                end if
                            {_unread_read_error_arm(_ROW_MAILBOX, "countErr")}
                            end try
                            -- Also emit child mailboxes under parent/child paths so
                            -- each child's own count is visible without double-counting
                            -- the parent (different keys: "Inbox" vs "Inbox/Sub").
                            set subMailboxes to {{}}
                            try
                                set subMailboxes to every mailbox of aMailbox
                            {_unread_read_error_arm(_ROW_CHILDREN, "childErr")}
                            end try
                            repeat with subBox in subMailboxes
                                set subName to "{_UNNAMED_SCOPE}"
                                try
                                    set subName to name of subBox
                                    set subUnread to unread count of subBox
                                    if {str(include_zero).lower()} or subUnread > 0 then
                                        set end of resultList to accountName & "|||" & mailboxName & "/" & subName & "|||" & subUnread
                                    end if
                                {_unread_read_error_arm(_ROW_CHILD, "subErr")}
                                end try
                            end repeat
                        end if
                    end repeat

                    if accountTruncated then
                        set end of resultList to accountName & "|||__TRUNCATED__|||{max_mailboxes}"
                    end if
                {_unread_read_error_arm(_ROW_ACCOUNT, "accountErr")}
                end try
            end if
        end repeat

        if (count of resultList) is 0 then
            return ""
        end if

        set AppleScript's text item delimiters to linefeed
        set outputText to resultList as string
        set AppleScript's text item delimiters to ""
        return outputText
    end tell
    """

    try:
        result = inbox.run_applescript(script, timeout=effective_timeout)
    except AppleScriptTimeout:
        return {
            "error": "timed_out",
            "message": (
                "AppleScript timed out while fetching mailbox unread counts. Try again or pass a larger `timeout`."
            ),
        }
    nested_counts: dict[str, Any] = {}
    truncated_accounts: set[str] = set()
    # An empty `result` (the script's no-rows case) falls through both loops and
    # returns the provenance block alone.
    for line in result.splitlines():
        parts = line.split("|||", 2)
        if len(parts) != 3:
            continue
        account_name, mailbox_name, unread_value = parts
        if mailbox_name == "__TRUNCATED__":
            truncated_accounts.add(account_name)
            continue
        account_record = nested_counts.setdefault(account_name, {})
        count, error = _read_row_value(unread_value)
        if error is not None:
            scope = "account" if mailbox_name == _ACCOUNT_SCOPE else mailbox_name
            account_record.setdefault(ERRORS_KEY, []).append(f"{scope}: {error}")
            # A real mailbox that threw still gets a key, with the -1 sentinel
            # the summary path uses. Without it, `include_zero=False` made a
            # dropped mailbox look exactly like a zero-unread one.
            if not _is_sentinel_scope(mailbox_name):
                account_record[mailbox_name] = UNREAD_COUNT_UNAVAILABLE
            continue
        account_record[mailbox_name] = count

    # Attach truncation marker to offending account records.
    for acct in truncated_accounts:
        if acct not in nested_counts:
            nested_counts[acct] = {}
        nested_counts[acct]["__truncated__"] = True

    nested_counts[PROVENANCE_KEY] = unread_count_disclosure()
    return nested_counts
