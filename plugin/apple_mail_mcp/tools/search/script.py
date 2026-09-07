"""AppleScript generators for the ``search_emails`` scan path.

Pure string building only (escape / mailbox refs / scan-bound math); no
``run_applescript`` call lives here, so no package-namespace routing is needed.
"""

from apple_mail_mcp.applescript_snippets import iso_datetime_handlers, sanitize_field_handler
from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.core import build_mailbox_ref, escape_applescript
from apple_mail_mcp.core.reply_state import was_replied_fragment
from apple_mail_mcp.tools.search.records import _build_applescript_date
from apple_mail_mcp.tools.search.scan_cap import compute_search_scan_cap

# Per-message read failures inside the scan loops below used to be swallowed by
# a bare ``try ... end try``, which made "every candidate raised" look exactly
# like "nothing matched". That is what let AGENTIC-2344 ship: the subject fast
# path emitted an unbound ``subject contains "x"`` (legal only inside a
# ``whose`` clause, -1728 ``Can't get subject.`` inside ``repeat with aMessage
# in ...``), so every iteration threw and the tool returned a confident empty
# result with no errors. Count the throws and emit one ``ERROR_MAILBOX``
# diagnostic per mailbox, which ``records._parse_search_records`` already routes
# into the response's ``error_details`` — so a scan that failed on every message
# can never again be reported as an authoritative zero.
#
# ``_SCAN_FAILURE_ARM`` is a dangling ``on error`` clause, not a standalone
# statement: splice it directly before the scan loop's own ``end try``.
_SCAN_FAILURE_INIT = "set scanReadFailures to 0"

_SCAN_FAILURE_ARM = """on error
                                        set scanReadFailures to scanReadFailures + 1"""

_SCAN_FAILURE_REPORT = """
                            if scanReadFailures > 0 then
                                set end of recordLines to "ERROR_MAILBOX|||" & mailboxName & "|||per-message scan failed for " & (scanReadFailures as string) & " of " & (count of candidateMessages) & " scanned message(s); results are incomplete"
                            end if
"""

# The scan trio above covers the *filter* loop: a message that could not be
# read is never considered. This second trio covers the *emit* loop, where a
# message has already matched the filter and only its record line failed to
# build. A bare `try` there drops the row while `collectLimit` — decremented
# after the append — stays put, so the page comes back full-shaped and short by
# one, with nothing to distinguish it from a genuinely smaller result. Same
# in-band marker and same wording as `records._read_failure_row`, which is the
# sanctioned shape for "matched but not emitted" (pattern P1).
_EMIT_FAILURE_INIT = "set emitReadFailures to 0"

_EMIT_FAILURE_ARM = """on error
                                                    set emitReadFailures to emitReadFailures + 1"""

_EMIT_FAILURE_REPORT = """
                                            if emitReadFailures > 0 then
                                                set end of recordLines to "ERROR_MAILBOX|||" & mailboxName & "|||read failed for " & (emitReadFailures as string) & " of " & (count of targetMessages) & " matched message(s); results are incomplete"
                                            end if
"""

# Emitted once per mailbox whose candidate slice filled the hard ceiling, so a
# caller can tell "the filter found nothing more" from "the scan stopped
# looking". Parsed by `records._parse_search_records` into a `scan_ceiling`
# entry, not a mailbox error: a saturated scan is a bound, not a failure.
_SCAN_CEILING_MARKER = "SCAN_CEILING|||"


def _build_search_script(
    account: str,
    mailbox: str,
    subject_terms: list[str] | None,
    sender: str | None,
    has_attachments: bool | None,
    read_status: str,
    date_from: str | None,
    date_to: str | None,
    include_content: bool,
    content_length: int,
    offset: int,
    limit: int,
    body_text: str | None,
    sender_exact: str | None = None,
    sender_domain: str | None = None,
    internet_message_id: str | None = None,
    recent_days: float = 0.0,
    timeout: int | None = None,
    date_from_explicit: bool = False,
    mailboxes: list[str] | None = None,
) -> tuple[str, bool, bool]:
    """Build the AppleScript for a single account's search.

    The script caps message collection inside AppleScript via either a
    ``whose`` clause sliced down to ``items 1 thru collectLimit`` or a
    ``messages 1 thru collectLimit`` bound directly, so we never materialize
    the full message list of a large (10K+) mailbox.

    Scan-cap scales with the caller's date window — ``recent_days`` when it was
    passed, otherwise the age of ``date_from`` — so that narrow filters (sender,
    subject_terms) over a wider window actually inspect a meaningful portion of
    it; otherwise a 7-day query with default limit=20 would only inspect the 21
    newest messages and silently miss matches further back. Floor stays at
    ``collect_limit + offset`` and the ceiling caps at ``SEARCH_WINDOW_CAP`` to
    keep Mail bounded on remote IMAP/Exchange mailboxes. The arithmetic and the
    ``scan_ceiling_applied`` gate live in ``scan_cap.compute_search_scan_cap``.

    Performance guidance — body_text:
        When ``body_text`` is set without an explicit ``date_from``, the
        scan_cap is further capped at ``BODY_SEARCH_AUTO_CAP`` to prevent hundreds of cold-cache
        IMAP body fetches (each can take ~1s on large Exchange inboxes).
        When the caller passes an explicit ``date_from`` (``date_from_explicit=True``),
        the cap is left as-is because the caller has explicitly bounded the window.
        A ``body_search_capped`` key is returned in structured responses when the
        cap fires to help callers understand why results may be incomplete.
    """
    escaped_sender = escape_applescript(sender) if sender else None
    escaped_sender_exact = escape_applescript(sender_exact.strip()) if sender_exact and sender_exact.strip() else None
    escaped_sender_domain = (
        escape_applescript(sender_domain.strip().lstrip("@")) if sender_domain and sender_domain.strip() else None
    )
    normalized_internet_message_id = ""
    if internet_message_id and internet_message_id.strip():
        normalized_internet_message_id = internet_message_id.strip()
        if not normalized_internet_message_id.startswith("<"):
            normalized_internet_message_id = "<" + normalized_internet_message_id
        if not normalized_internet_message_id.endswith(">"):
            normalized_internet_message_id = normalized_internet_message_id + ">"
    escaped_internet_message_id = escape_applescript(normalized_internet_message_id)
    use_body_search = body_text is not None

    # The one query shape that drives both decisions below: the narrowed scan_cap
    # and the subject-only fast path. The fast path additionally requires
    # ``not date_to`` because it emits no upper-bound comparison; every other
    # clause is shared, so naming the test once keeps the two in step.
    subject_only_header_search = (
        bool(subject_terms)
        and not sender
        and not sender_exact
        and not sender_domain
        and not internet_message_id
        and not use_body_search
        and has_attachments is None
        and read_status == "all"
    )

    collect_limit = limit + 1  # +1 for has_more probe; offset is decremented separately
    base_cap = collect_limit + offset
    # All scan-bound arithmetic (window widening, body cap, hard ceiling, and
    # the truncation flag) lives in ``scan_cap`` — see that module for why
    # ``date_from`` widens the slice and why the flag compares against the
    # pre-clamp desired size rather than ``base_cap``.
    scan_cap, body_search_capped, scan_ceiling_applied = compute_search_scan_cap(
        base_cap=base_cap,
        recent_days=recent_days,
        date_from=date_from,
        subject_only_header_search=subject_only_header_search,
        use_body_search=use_body_search,
        date_from_explicit=date_from_explicit,
    )

    # Track whether the mailbox-count cap is active (mailbox="All" path).
    # The AppleScript guard caps at MAX_MAILBOXES_PER_SEARCH; we surface this
    # to callers via a warnings field so they know results may be incomplete on
    # accounts with many labels (e.g. Gmail with 200+ labels).
    mailbox_count_capped = mailbox == "All"

    # Candidate binding is bounded in every arm (AGENTIC-2355). The recovery arm
    # cannot be deleted — `messages 1 thru scanUpperBound` raises whenever the
    # mailbox holds fewer messages than the cap, which is ordinary for small
    # folders and for most of a `mailbox="All"` fan-out — and it must not fall back
    # to `messages of currentMailbox`, AppleScript's other spelling of `every
    # message of currentMailbox`. So it re-slices against `count of messages` (a
    # cheap property read, the same guard `bounded_scan.build_bounded_message_scan`
    # uses), and if even that fails it emits an ERROR_MAILBOX marker rather than
    # leaving an empty candidate set that renders as an authoritative `FOUND: 0`.
    # A count of 0 stays silent: a true empty result is not a failure.
    # Full rationale and contract: tests/search/test_search_bounded_candidate_binding.py
    ceiling_marker = (
        f"""
                            if (count of candidateMessages) is greater than or equal to scanUpperBound then
                                set end of recordLines to "{_SCAN_CEILING_MARKER}" & mailboxName & "|||" & (scanUpperBound as string)
                            end if
"""
        if scan_ceiling_applied
        else ""
    )

    bounded_candidate_script = f"""
                            set matchingMessages to {{}}
                            set candidateMessages to {{}}
                            set scanUpperBound to {scan_cap}
                            try
                                set candidateMessages to messages 1 thru scanUpperBound of currentMailbox
                            on error
                                try
                                    set boundedSliceCount to count of messages of currentMailbox
                                    if boundedSliceCount > scanUpperBound then
                                        set boundedSliceCount to scanUpperBound
                                    end if
                                    if boundedSliceCount > 0 then
                                        set candidateMessages to messages 1 thru boundedSliceCount of currentMailbox
                                    end if
                                on error candidateBindError
                                    set end of recordLines to "ERROR_MAILBOX|||" & mailboxName & "|||bounded candidate slice unavailable (" & candidateBindError & "); 0 of " & (scanUpperBound as string) & " requested message(s) scanned, so this mailbox contributed no results"
                                end try
                            end try
                            {ceiling_marker}
    """

    _max_mailboxes_per_search = (
        SCAN_BOUNDS["MAX_MAILBOXES_PER_SEARCH_ALL"] if mailbox == "All" else SCAN_BOUNDS["MAX_MAILBOXES_PER_SEARCH"]
    )
    if mailboxes:
        # Explicit mailbox list: look up each named folder, degrade gracefully
        # if a name doesn't exist (emits ERROR_MAILBOX instead of hard failure).
        # Reuse the shared resolver so Sent display names get the same
        # account-scoped top-level fallback as the singular parameter.
        mailbox_lookups = "\n".join(
            f"""                try
                    {build_mailbox_ref(mb, account_var="targetAccount", var_name=f"searchMailbox{index}")}
                    set end of searchMailboxes to searchMailbox{index}
                on error
                    set end of recordLines to "ERROR_MAILBOX|||{escape_applescript(mb)}|||mailbox not found"
                end try"""
            for index, mb in enumerate(mailboxes)
        )
        mailbox_script = f"""
                set searchMailboxes to {{}}
{mailbox_lookups}
        """
        skip_script = ""
    elif mailbox == "All":
        mailbox_script = f"""
                set searchMailboxes to every mailbox of targetAccount
                if (count of searchMailboxes) > {_max_mailboxes_per_search} then
                    set searchMailboxes to items 1 thru {_max_mailboxes_per_search} of searchMailboxes
                end if
        """
        skip_script = """
                        set skipFolders to {"Trash", "Junk", "Junk Email", "Deleted Items", "Sent", "Sent Items", "Sent Messages", "Drafts", "Spam", "Deleted Messages"}
                        repeat with skipFolder in skipFolders
                            if mailboxName is skipFolder then
                                set shouldSkip to true
                                exit repeat
                            end if
                        end repeat
        """
    else:
        _mailbox_resolve = build_mailbox_ref(mailbox, account_var="targetAccount", var_name="searchMailbox")
        mailbox_script = f"""
                {_mailbox_resolve}
                set searchMailboxes to {{searchMailbox}}
        """
        skip_script = ""

    date_setup = _build_applescript_date("fromDate", date_from)
    date_setup += _build_applescript_date("toDate", date_to, end_of_day=True)

    escaped_account = escape_applescript(account)
    account_setup = f'''
                set searchAccounts to {{account "{escaped_account}"}}
        '''

    # Build per-message filter block. Avoid broad `every message ... whose`
    # filters because Mail.app can materialize remote mailboxes before applying
    # them. We bind a bounded newest-first slice, then filter in that slice.
    #
    # Date lower bounds need a special fast path: Exchange inboxes can have
    # tens of thousands of messages, and even a bounded 300-message slice is
    # too slow if we read subject/sender/body for every no-hit query. Mail
    # returns mailbox messages newest-first, so once a message is older than
    # fromDate the rest of the slice is outside the requested window.
    early_date_break = "if messageDate < fromDate then exit repeat" if date_from and not date_to else ""
    escaped_body = escape_applescript(body_text) if body_text else ""
    per_msg_conditions: list[str] = []
    if subject_terms:
        # Bound to the loop-local ``messageSubject`` variable, never to a bare
        # ``subject`` property reference: every consumer of this string
        # interpolates it into an explicit ``repeat with aMessage in ...`` loop,
        # which supplies no implicit target (AGENTIC-2344).
        subject_checks = " or ".join(f'messageSubject contains "{escape_applescript(t)}"' for t in subject_terms)
        per_msg_conditions.append(f"({subject_checks})")
    if sender:
        per_msg_conditions.append(f'messageSender contains "{escaped_sender}"')
    if escaped_sender_exact:
        per_msg_conditions.append(
            f'(messageSender is "{escaped_sender_exact}" or messageSender contains "<{escaped_sender_exact}>")'
        )
    if escaped_sender_domain:
        per_msg_conditions.append(f'messageSender contains "@{escaped_sender_domain}"')
    if escaped_internet_message_id:
        per_msg_conditions.append(
            f'(internetMessageId is "{escaped_internet_message_id}" or internetMessageId is "{escaped_internet_message_id.strip("<>")}")'
        )
    if read_status == "read":
        per_msg_conditions.append("messageRead is true")
    elif read_status == "unread":
        per_msg_conditions.append("messageRead is false")
    if date_from:
        per_msg_conditions.append("messageDate >= fromDate")
    if date_to:
        per_msg_conditions.append("messageDate <= toDate")
    if has_attachments is True:
        per_msg_conditions.append("(count of mail attachments of aMessage) > 0")
    elif has_attachments is False:
        per_msg_conditions.append("(count of mail attachments of aMessage) = 0")
    if use_body_search:
        per_msg_conditions.append(f'msgContent contains "{escaped_body}"')

    combined_condition = " and ".join(per_msg_conditions)

    if subject_only_header_search and not date_to:
        # Fast no-hit/needle path: filter the already-bounded newest slice
        # with the cheapest possible per-message reads. Avoid `whose` here:
        # AppleScript does not reliably apply it to a list of message objects
        # returned by `messages 1 thru N`. The slice is deliberately tiny for
        # default subject lookups, so a subject-only loop is fast and avoids
        # sender/read-status/message-id/body fetches on large Exchange inboxes.
        #
        # The condition MUST test the loop-local `messageSubject` bound on the
        # line above. A bare `subject contains ...` is only valid inside a
        # `whose` clause; here it is unbound and throws -1728 on every message.
        #
        # AGENTIC-2356: the condition is the whole `combined_condition`, not the
        # subject clause alone. `subject_only_header_search` already excludes every
        # filter needing a sender / read-status / message-id / body read, so the
        # list holds at most the subject clause and `messageDate >= fromDate`.
        # Interpolating only the subject clause threw the caller's date floor away
        # while `search_emails` still reported `searched_from`, so a subject-only
        # search returned matches from outside the window it claimed to search.
        #
        # `date received` is therefore read here too — but only when the caller
        # asked for a floor. `fromDate` is declared only for a truthy `date_from`
        # (`_build_applescript_date` emits nothing otherwise), so an
        # unconditional read would reference an undeclared variable and throw on
        # every message. The guard is `date_from` truthiness, not caller
        # identity: `manage/` and `analytics/` callers used to pass
        # `date_from=None` always, but PR #91 gave `manage_trash` its own date
        # window, so that premise is no longer true and must not be relied on.
        # `early_date_break` is non-empty under exactly the same condition (the
        # guard above guarantees `date_to` is falsy) and pays for the extra read
        # by ending the scan at the first message older than `fromDate`.
        fast_path_date_read = "set messageDate to date received of aMessage" if date_from else ""
        message_collection = f"""
                                {bounded_candidate_script}
                                {_SCAN_FAILURE_INIT}
                            ignoring case
                                repeat with aMessage in candidateMessages
                                    if (count of matchingMessages) >= {scan_cap} then exit repeat
                                    try
                                        {fast_path_date_read}
                                        {early_date_break}
                                        set messageSubject to subject of aMessage
                                        if {combined_condition} then
                                            set end of matchingMessages to aMessage
                                        end if
                                    {_SCAN_FAILURE_ARM}
                                    end try
                                end repeat
                            end ignoring
                            {_SCAN_FAILURE_REPORT}
        """
    elif per_msg_conditions:
        content_read_block = (
            """
                                        set msgContent to ""
                                        try
                                            set msgContent to content of aMessage
                                        end try
        """
            if use_body_search
            else ""
        )
        message_collection = f"""
                                {bounded_candidate_script}
                                {_SCAN_FAILURE_INIT}
                            ignoring case
                                repeat with aMessage in candidateMessages
                                    if (count of matchingMessages) >= {scan_cap} then exit repeat
                                    try
                                        set messageDate to date received of aMessage
                                        {early_date_break}
                                        set messageSubject to subject of aMessage
                                        set messageSender to sender of aMessage
                                        set internetMessageId to ""
                                        try
                                            set internetMessageId to message id of aMessage
                                        end try
                                        set messageRead to read status of aMessage
                                        {content_read_block}
                                        if {combined_condition} then
                                            set end of matchingMessages to aMessage
                                        end if
                                    {_SCAN_FAILURE_ARM}
                                    end try
                                end repeat
                            end ignoring
                            {_SCAN_FAILURE_REPORT}
        """
    else:
        message_collection = f"""
                                {bounded_candidate_script}
                                {_SCAN_FAILURE_INIT}
                            repeat with aMessage in candidateMessages
                                try
                                    set messageDate to date received of aMessage
                                    {early_date_break}
                                    set end of matchingMessages to aMessage
                                {_SCAN_FAILURE_ARM}
                                end try
                            end repeat
                            {_SCAN_FAILURE_REPORT}
        """

    # Template the inner AppleScript timeout from the same value the outer
    # run_applescript wrapper will use, minus 10 s so the AS timeout fires
    # before SIGKILL and Mail.app can clean up gracefully. Floor at 30 s to
    # keep the script meaningful on very tight timeouts.
    inner_timeout = max(30, (timeout if timeout is not None else 180) - 10)

    script = f"""{sanitize_field_handler()}

    {iso_datetime_handlers()}

    tell application "Mail"
        with timeout of {inner_timeout} seconds
            try
                set recordLines to {{}}
                set offsetRemaining to {offset}
                set collectLimit to {collect_limit}
                {date_setup}
                {account_setup}

                repeat with targetAccount in searchAccounts
                    if collectLimit <= 0 then exit repeat
                    set accountName to my sanitize_field(name of targetAccount)
                    {mailbox_script}

                    repeat with currentMailbox in searchMailboxes
                        if collectLimit <= 0 then exit repeat

                        -- NB: do NOT wrap this per-mailbox scan in `with timeout`.
                        -- Materializing `messages 1 thru N` on a 24K+ Exchange
                        -- mailbox routinely exceeds a short timeout; the inner
                        -- candidate-fetch try/catch then swallows the timeout and
                        -- the mailbox silently returns 0 rows. Per-mailbox failures
                        -- are already isolated by the `on error -> ERROR_MAILBOX`
                        -- handler below, and the whole call is bounded by the
                        -- outer call-level timeout budget ({inner_timeout}s).
                        try
                            set mailboxName to my sanitize_field(name of currentMailbox)
                            set shouldSkip to false
                            {skip_script}

                                if not shouldSkip then
                                    {message_collection}
                                    set matchingCount to count of matchingMessages

                                    if offsetRemaining >= matchingCount then
                                        set offsetRemaining to offsetRemaining - matchingCount
                                    else
                                        set startIndex to offsetRemaining + 1
                                        set availableCount to matchingCount - offsetRemaining
                                        if availableCount > collectLimit then
                                            set endIndex to startIndex + collectLimit - 1
                                        else
                                            set endIndex to startIndex + availableCount - 1
                                        end if

                                        if endIndex >= startIndex then
                                            set targetMessages to items startIndex thru endIndex of matchingMessages
                                            {_EMIT_FAILURE_INIT}

                                            repeat with aMessage in targetMessages
                                                try
                                                    set messageId to my sanitize_field(id of aMessage)
                                                    set internetMessageId to ""
                                                    try
                                                        set internetMessageId to my sanitize_field(message id of aMessage)
                                                    end try
                                                    set messageSubject to my sanitize_field(subject of aMessage)
                                                    set messageSender to my sanitize_field(sender of aMessage)
                                                    set messageRead to read status of aMessage
                                                    set messageDate to date received of aMessage
                                                    set receivedAt to my iso_datetime(messageDate)
                                                    set contentPreview to ""
                                                    -- Recipients (to/cc) are intentionally NOT resolved here.
                                                    -- Per-message `to recipients`/`address of` can HANG (not error,
                                                    -- so `on error` cannot catch it) on large remote Exchange/Gmail
                                                    -- mailboxes, blocking the whole bulk scan until timeout. Fetch
                                                    -- recipients per message via get_email_by_id instead (single,
                                                    -- bounded, fast). Emit empty placeholders to keep field alignment.
                                                    set toRecips to ""
                                                    set ccRecips to ""

                                                    if {str(include_content).lower()} then
                                                        try
                                                            set msgContent to content of aMessage
                                                            set AppleScript's text item delimiters to {{return, linefeed, tab}}
                                                            set contentParts to text items of msgContent
                                                            set AppleScript's text item delimiters to " "
                                                            set cleanText to contentParts as string
                                                            set AppleScript's text item delimiters to ""
                                                            if {content_length} > 0 and length of cleanText > {content_length} then
                                                                set contentPreview to my sanitize_field(text 1 thru {content_length} of cleanText & "...")
                                                            else
                                                                set contentPreview to my sanitize_field(cleanText)
                                                            end if
                                                        on error
                                                            set contentPreview to ""
                                                        end try
                                                    end if

                                                    set readValue to "false"
                                                    if messageRead then
                                                        set readValue to "true"
                                                    end if
                                                    {was_replied_fragment(var="aMessage")}

                                                    set recordLine to messageId & "|||" & internetMessageId & "|||" & messageSubject & "|||" & messageSender & "|||" & mailboxName & "|||" & accountName & "|||" & readValue & "|||" & receivedAt & "|||" & contentPreview & "|||" & toRecips & "|||" & ccRecips & "|||" & "" & "|||" & "" & "|||" & "" & "|||" & wasRepliedToken
                                                    set end of recordLines to recordLine
                                                    set collectLimit to collectLimit - 1
                                                    if collectLimit <= 0 then exit repeat
                                                {_EMIT_FAILURE_ARM}
                                                end try
                                            end repeat
                                            {_EMIT_FAILURE_REPORT}
                                        end if

                                        set offsetRemaining to 0
                                    end if
                                end if
                        on error errMsg
                            -- Emit a structured marker so Python can surface it
                            -- in error_details instead of silently discarding it.
                            set end of recordLines to "ERROR_MAILBOX|||" & (name of currentMailbox) & "|||" & errMsg
                        end try
                    end repeat
                end repeat

                if (count of recordLines) is 0 then
                    return ""
                end if

                set AppleScript's text item delimiters to linefeed
                set outputText to recordLines as string
                set AppleScript's text item delimiters to ""
                return outputText
            on error errMsg
                return "ERROR|||" & errMsg
            end try
        end timeout
    end tell
    """

    return script, body_search_capped, mailbox_count_capped


def _list_accounts_script() -> str:
    """Tiny AppleScript that returns one account name per line."""
    return """
    tell application "Mail"
        set acctNames to {}
        repeat with anAccount in (every account)
            set end of acctNames to (name of anAccount)
        end repeat
        set AppleScript's text item delimiters to linefeed
        return acctNames as string
    end tell
    """
