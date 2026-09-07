"""Thread reconstruction tool plus its mailbox-selection script helper.

Subject/header matching helpers and the candidate-scan failure channel live
in ``thread_helpers``; they are re-exported here so the historical
``apple_mail_mcp.tools.search.thread.<name>`` attribute surface still works.

``run_applescript`` and ``validate_account_name`` are routed through the
``search`` package facade so the corresponding test patch seams keep firing.
"""

from datetime import datetime, timedelta
from typing import Any

from apple_mail_mcp.applescript_snippets import iso_datetime_handlers, sanitize_field_handler, thread_headers_block
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.bounded_scan import compute_scan_upper_bound
from apple_mail_mcp.constants import SCAN_BOUNDS, THREAD_PREFIXES
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inject_preferences,
    normalize_message_ids,
)
from apple_mail_mcp.core.reply_state import was_replied_fragment
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import search
from apple_mail_mcp.tools.search.anchor import resolve_message_mailbox
from apple_mail_mcp.tools.search.by_id import _fetch_email_record_by_id
from apple_mail_mcp.tools.search.records import _build_applescript_date
from apple_mail_mcp.tools.search.thread_helpers import (
    _HEADER_MESSAGE_ID_RE as _HEADER_MESSAGE_ID_RE,
)
from apple_mail_mcp.tools.search.thread_helpers import (
    _applescript_string_list as _applescript_string_list,
)
from apple_mail_mcp.tools.search.thread_helpers import (
    _extract_thread_header_tokens as _extract_thread_header_tokens,
)
from apple_mail_mcp.tools.search.thread_helpers import (
    _normalize_thread_header_id as _normalize_thread_header_id,
)
from apple_mail_mcp.tools.search.thread_helpers import (
    _thread_strip_prefixes_handler as _thread_strip_prefixes_handler,
)
from apple_mail_mcp.tools.search.thread_helpers import (
    candidate_failure_report,
    render_failure_report,
    thread_coverage_report,
)
from apple_mail_mcp.tools.search.thread_payload import ThreadRequest, build_thread_payload


def _thread_mailbox_script(mailbox: str, mailboxes: list[str] | None) -> str:
    """Build bounded mailbox selection setup for get_email_thread."""
    if mailboxes:
        mailbox_lines = [
            """
            set searchMailboxes to {}
            set useAllMailboxes to false
            """
        ]
        for mb in mailboxes:
            escaped_mb = escape_applescript(mb)
            if mb.lower() == "inbox":
                mailbox_lines.append(
                    """
            try
                set resolvedMailbox to mailbox "INBOX" of targetAccount
            on error
                set resolvedMailbox to mailbox "Inbox" of targetAccount
            end try
            set end of searchMailboxes to resolvedMailbox
                    """
                )
            else:
                mailbox_lines.append(
                    f"""
            set end of searchMailboxes to mailbox "{escaped_mb}" of targetAccount
                    """
                )
        return "\n".join(mailbox_lines)

    escaped_mailbox = escape_applescript(mailbox)
    return f'''
        try
            set searchMailbox to mailbox "{escaped_mailbox}" of targetAccount
        on error
            if "{escaped_mailbox}" is "INBOX" then
                set searchMailbox to mailbox "Inbox" of targetAccount
            else if "{escaped_mailbox}" is "All" then
                set searchMailboxes to every mailbox of targetAccount
                set useAllMailboxes to true
            else
                error "Mailbox not found: {escaped_mailbox}"
            end if
        end try

        if "{escaped_mailbox}" is not "All" then
            set searchMailboxes to {{searchMailbox}}
            set useAllMailboxes to false
        end if
    '''


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Email Thread")
@inject_preferences
def get_email_thread(
    account: str,
    subject_keyword: str | None = None,
    message_id: str | None = None,
    mailbox: str = "INBOX",
    mailboxes: list[str] | None = None,
    max_messages: int = 50,
    scan_messages: int | None = None,
    recent_days: float = 2.0,
    include_preview: bool = True,
    output_format: str = "text",
    timeout: int | None = None,
    include_draft_state: bool = True,
) -> str:
    """
      Get an email conversation thread - all messages with the same or similar subject.

      Defaults to the last 48 hours. Unbounded thread scans
      (``recent_days=0``) are refused; full-mailbox scans are disabled, so
      pass a bounded ``recent_days`` window instead. Subject matching is
      case-insensitive.

    Preferred: pass ``message_id`` from ``search_emails`` or ``list_inbox_emails``
    to fetch the anchor message by id and match related messages by
    Internet Message-ID, In-Reply-To, and References headers before falling
    back to subject matching.

      Args:
          account: Account name (e.g., "Gmail", "Work")
          subject_keyword: Keyword to identify the thread (e.g., "Re: Project Update").
              Optional when ``message_id`` is provided.
          message_id: Optional numeric Apple Mail message id. When set, fetches the
              anchor message first and derives the thread subject from it.
          mailbox: Mailbox to search in (default: "INBOX", use "All" for all mailboxes).
              Ignored when ``mailboxes`` is provided.
          mailboxes: Explicit mailbox list to search. Prefer this over ``mailbox="All"``.
          max_messages: Maximum number of thread *matched* messages to return
              (default: 50). This is the return bound, not the scan bound.
          scan_messages: Override for how many messages are examined per
              mailbox while looking for members. Defaults to a window-scaled
              bound (``SCAN_BOUNDS["THREAD_SCAN_*"]``), clamped to
              ``THREAD_SCAN_HARD_CEILING``. Raise it when ``thread_incomplete``
              is true with a ``scan_ceiling_hit`` entry.
          recent_days: Only scan messages received within this many days (default: 2.0
              = 48h). ``recent_days=0`` is rejected with ``UNBOUNDED_SCAN_REQUIRED``.
          include_preview: Include content previews in output. Set false to avoid
              reading message bodies during thread discovery.
          output_format: Output format: "text" or "json" (default: "text").
          timeout: Optional AppleScript timeout in seconds (default: 120).
          include_draft_state: When True (default) and ``output_format="json"``,
              fetch one bounded Drafts snapshot per account appearing in the
              thread (lazily, capped at 5 accounts) and set `has_draft` on
              every item (true/false when scanned, null when the scan was
              skipped or errored). Set False to skip the Drafts scan
              entirely. Text output does not carry `has_draft` (thread text
              is rendered inside AppleScript, not from parsed rows); it still
              shows the native `[REPLIED]` marker, which needs no Drafts scan.
              JSON mode independently adds one short bounded Sent-header scan
              per relevant account for composite reply state.

      Returns:
          Formatted thread view (text mode prefixes replied messages with
          `[REPLIED]`), or JSON with items, ids, headers, anchor, strategy,
          and reply/draft scan diagnostics. Every JSON item carries raw
          `was_replied_to` / `mail_was_replied_to`, nullable `has_sent_reply`
          and `reply_state`, plus `has_draft` (true/false/null, governed by
          `include_draft_state`). `draft_scan` is `{"status": "ok" | "error" |
          "skipped", "scanned": N, "accounts": [...], "error"?: "..."}`.
          A scan that failed inside AppleScript adds `error` and `errors` to
          the JSON payload instead of reporting an empty thread; per-mailbox
          failures add `errors` plus `error_details`. Neither key appears on
          a genuinely empty thread. JSON also carries `matched` (text mode's
          `FOUND N`) beside `returned` (rows rendered) and `render_incomplete`
          (`matched > returned`); a render that threw is attributed as a
          `render failed for N of M` entry in `errors`/`error_details` (text
          mode prints `PARTIAL:`), an unattributed shortfall as
          `render_mismatch`. A candidate read that threw *before* matching is
          reported separately as `candidate_scan_incomplete` plus a
          `candidate scan failed for ...` entry in `errors`/`error_details`
          (`type: "candidate_scan_error"`; text mode prints its own `PARTIAL:`
          line). It is invisible to `matched`/`returned`, which are short
          together, so the thread may be missing messages entirely.

          Completeness is reported as FOUR independent checks; read all of
          them, because `thread_incomplete` alone is not a completeness test.
          `thread_incomplete` covers only bounds you did NOT choose: a short
          render, a candidate scan that threw, a mailbox whose slice filled
          (`scan_ceiling_hit` — raise `scan_messages`, capped at 400), a
          recovered anchor, or a script error. Your own bounds are reported
          apart from it and leave it false: `window_truncated` /
          `date_floor_hit` (the `recent_days` cutoff stopped the scan — widen
          `recent_days`) and `return_limit_reached` (the thread filled
          `max_messages` — raise it). `scan_messages_applied` reports the
          slice used, and each item carries `attachment_count` (null when
          unreadable, which is not 0). An anchor the scan missed is appended
          with `anchor_recovered: true` plus a warning, not dropped. These
          fields are JSON-only; use `output_format="json"` to check
          completeness.
    """
    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = search.validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return account_err

    if not message_id and not subject_keyword:
        return "Error: Provide either message_id or subject_keyword"

    if output_format not in {"text", "json"}:
        return "Error: Invalid output_format. Use: text, json"

    if max_messages <= 0:
        return "Error: max_messages must be > 0"

    if scan_messages is not None and scan_messages <= 0:
        return "Error: scan_messages must be > 0"

    if mailboxes is not None:
        mailboxes = [mb.strip() for mb in mailboxes if mb and mb.strip()]
        if not mailboxes:
            return "Error: mailboxes must contain at least one mailbox name"
        if any(mb.lower() == "all" for mb in mailboxes):
            return 'Error: mailboxes does not accept "All"; use mailbox="All" only as a degraded fallback'

    effective_recent_days = float(recent_days) if recent_days else 0.0
    if effective_recent_days <= 0:
        tool_error = ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=("get_email_thread refuses to scan without a date window; pass recent_days=7 or smaller"),
            remediation={
                "preferred": "Pass recent_days=7",
                "note": "Full-mailbox scans are disabled; bound this call.",
            },
        )
        return serialize_tool_error(tool_error)
    effective_timeout = timeout if timeout is not None else 120

    resolved_mailbox = mailbox
    resolved_subject = subject_keyword or ""
    anchor: dict[str, Any] | None = None
    anchor_mailbox_resolved = False

    if message_id:
        normalized_ids = normalize_message_ids([message_id])
        if not normalized_ids:
            return "Error: message_id must be a numeric Apple Mail message id"
        lookup_mailboxes = mailboxes or [mailbox]
        if not mailboxes and mailbox.lower() == "all":
            # "All" expands to every mailbox for the *scan* but is meaningless
            # to a by-name fetch, so this used to hand Mail the literal string
            # and return `Mailbox not found: All` for a supported argument.
            try:
                probed_mailbox = resolve_message_mailbox(account, message_id, timeout=effective_timeout)
            except AppleScriptTimeout:
                return (
                    f"Error: AppleScript timed out while resolving the mailbox for "
                    f"message_id={normalized_ids[0]} on account {account!r}. Pass an explicit "
                    '`mailboxes` list instead of mailbox="All", or a larger `timeout`.'
                )
            if not probed_mailbox:
                return (
                    f"Error: No email found for message_id={normalized_ids[0]} in the first "
                    f"{SCAN_BOUNDS['THREAD_ANCHOR_MAILBOX_PROBE_CAP']} mailbox(es) of account "
                    f"{account!r}. Pass an explicit `mailboxes` list naming the mailbox."
                )
            lookup_mailboxes = [probed_mailbox]
            anchor_mailbox_resolved = True
        for lookup_mailbox in lookup_mailboxes:
            try:
                anchor = _fetch_email_record_by_id(
                    account=account,
                    message_id=message_id,
                    mailbox=lookup_mailbox,
                    include_content=False,
                    max_content_length=0,
                    timeout=effective_timeout,
                )
            except AppleScriptTimeout:
                return (
                    f"Error: AppleScript timed out while fetching message_id={normalized_ids[0]} "
                    f"on account {account!r}. Try again or pass a larger `timeout`."
                )
            except ValueError as exc:
                return f"Error: {exc}"
            if anchor is not None:
                break
        if anchor is None:
            searched = ", ".join(lookup_mailboxes)
            return f"Error: No email found for message_id={normalized_ids[0]} in {searched}"
        resolved_subject = anchor.get("subject", "") or resolved_subject
        if not anchor_mailbox_resolved:
            # The probe answers where the *anchor* lives, not where the thread
            # lives, so letting it set the scope under mailbox="All" would
            # silently narrow a whole-account request to one mailbox.
            resolved_mailbox = anchor.get("mailbox") or mailbox

    escaped_account = escape_applescript(account)

    cleaned_keyword = resolved_subject
    for prefix in THREAD_PREFIXES:
        cleaned_keyword = cleaned_keyword.replace(prefix, "").strip()
    if not cleaned_keyword:
        cleaned_keyword = resolved_subject
    escaped_keyword = escape_applescript(cleaned_keyword)
    header_tokens = _extract_thread_header_tokens(
        anchor.get("internet_message_id") if anchor else None,
        anchor.get("in_reply_to") if anchor else None,
        anchor.get("references") if anchor else None,
    )
    header_matching_enabled = bool(message_id and header_tokens)
    thread_strategy = "header_first" if header_matching_enabled else "subject"
    header_tokens_literal = _applescript_string_list(header_tokens)

    # ``effective_recent_days`` is > 0 from here on: a non-positive window
    # already returned UNBOUNDED_SCAN_REQUIRED above, so there is no unbounded
    # variant of the cutoff date, the window banner, or the date floor below.
    cutoff = datetime.now() - timedelta(days=effective_recent_days)
    date_setup = _build_applescript_date("cutoffDate", cutoff.strftime("%Y-%m-%d"))
    window_line = "Window: last 48h" if effective_recent_days == 2.0 else f"Window: last {effective_recent_days}d"

    # The scan bound is not the return bound. This was `max_messages`, so a
    # live 9-member thread returned 5 and called itself complete. Size the
    # slice from the date window instead (AGENTIC-2794).
    if scan_messages is not None:
        desired_scan = scan_messages
    else:
        desired_scan = max(
            max_messages,
            compute_scan_upper_bound(
                effective_recent_days,
                base_cap=SCAN_BOUNDS["THREAD_SCAN_BASE_CAP"],
                window_cap=SCAN_BOUNDS["THREAD_SCAN_WINDOW_CAP"],
                days_scale=SCAN_BOUNDS["THREAD_SCAN_DAYS_SCALE"],
            ),
        )
    scan_cap = min(desired_scan, SCAN_BOUNDS["THREAD_SCAN_HARD_CEILING"])
    date_check = """if messageDate < cutoffDate then
                                    set threadDateFloorHit to true
                                    exit repeat
                                end if"""
    sanitize_script = sanitize_field_handler()
    thread_headers_script = thread_headers_block(
        message_var="aMessage",
        in_reply_to_var="inReplyTo",
        references_var="refsValue",
        include_on_error=True,
    )
    was_replied_fragment_script = was_replied_fragment(var="aMessage")
    candidate_collection = f"""
                                set candidateMessages to {{}}
                                set threadScanCeilingHit to false
                                set threadDateFloorHit to false
                                set currentMailboxName to my sanitize_field(name of currentMailbox)
                                set messageCount to count of messages of currentMailbox
                                if messageCount > {scan_cap} then
                                    set scanUpperBound to {scan_cap}
                                    set threadScanCeilingHit to true
                                else
                                    set scanUpperBound to messageCount
                                end if
                                if scanUpperBound > 0 then
                                    set candidateMessages to messages 1 thru scanUpperBound of currentMailbox
                                end if
    """
    mailbox_script = _thread_mailbox_script(resolved_mailbox, mailboxes)
    escaped_render_scope = escape_applescript(", ".join(mailboxes) if mailboxes else resolved_mailbox)
    preview_collect_block = ""
    preview_text_block = ""
    if include_preview:
        preview_collect_block = """
                        -- Get content preview
                        try
                            set msgContent to content of aMessage
                            set AppleScript's text item delimiters to {return, linefeed}
                            set contentParts to text items of msgContent
                            set AppleScript's text item delimiters to " "
                            set cleanText to contentParts as string
                            set AppleScript's text item delimiters to ""

                            if length of cleanText > 150 then
                                set contentPreview to my sanitize_field(text 1 thru 150 of cleanText & "...")
                            else
                                set contentPreview to my sanitize_field(cleanText)
                            end if
                        end try
        """
        preview_text_block = """
                    if contentPreview is not "" then
                        set outputText to outputText & "   Preview: " & contentPreview & return
                    end if
        """

    script = f'''
    {sanitize_script}
    {_thread_strip_prefixes_handler()}

    {iso_datetime_handlers()}

    tell application "Mail"
        set outputText to "EMAIL THREAD VIEW" & return & return
        set outputText to outputText & "Thread topic: {escaped_keyword}" & return
        set outputText to outputText & "Account: {escaped_account}" & return
        set outputText to outputText & "{window_line}" & return & return
        set recordRows to {{}}
        set headerThreadMessages to {{}}
        set subjectFallbackMessages to {{}}
        set threadMessages to {{}}
        set threadHeaderTokens to {header_tokens_literal}
        set selectedStrategy to "subject"
        set threadMatchedCount to 0
        set threadRenderFailures to 0
        set threadCandidateScanned to 0
        set threadCandidateFailures to 0
        set threadMailboxFailures to 0
        set threadCoverageNotes to ""
        {date_setup}

        try
            set targetAccount to account "{escaped_account}"
            {mailbox_script}

            -- Collect matching messages from mailboxes with date filter + cap
            repeat with currentMailbox in searchMailboxes
                if (count of headerThreadMessages) >= {max_messages} then exit repeat
                if (not {str(header_matching_enabled).lower()}) and (count of subjectFallbackMessages) >= {max_messages} then exit repeat

                try
                    {candidate_collection}
                    set threadCandidateScanned to threadCandidateScanned + (count of candidateMessages)

                    ignoring case
                        repeat with aMessage in candidateMessages
                            if (count of headerThreadMessages) >= {max_messages} then exit repeat
                            if (not {str(header_matching_enabled).lower()}) and (count of subjectFallbackMessages) >= {max_messages} then exit repeat

                            try
                                set messageSubject to subject of aMessage
                                set messageDate to date received of aMessage
                                {date_check}
                                set cleanSubject to my stripThreadPrefixes(messageSubject)
                                set subjectMatched to false
                                if cleanSubject contains "{escaped_keyword}" or messageSubject contains "{escaped_keyword}" then
                                    set subjectMatched to true
                                end if

                                set headerMatched to false
                                if {str(header_matching_enabled).lower()} then
                                    set internetMessageIdForMatch to ""
                                    try
                                        set internetMessageIdForMatch to message id of aMessage
                                    end try
                                    {thread_headers_script}
                                    set candidateHeaderText to internetMessageIdForMatch & " " & inReplyTo & " " & refsValue
                                    ignoring case
                                        repeat with threadToken in threadHeaderTokens
                                            if candidateHeaderText contains (threadToken as string) then
                                                set headerMatched to true
                                                exit repeat
                                            end if
                                        end repeat
                                    end ignoring
                                end if

                                if headerMatched then
                                    set end of headerThreadMessages to aMessage
                                else if subjectMatched and (count of subjectFallbackMessages) < {max_messages} then
                                    set end of subjectFallbackMessages to aMessage
                                end if
                            on error
                                set threadCandidateFailures to threadCandidateFailures + 1
                            end try
                        end repeat
                    end ignoring
{thread_coverage_report()}
                on error
                    set threadMailboxFailures to threadMailboxFailures + 1
                end try
            end repeat
{candidate_failure_report(escaped_render_scope)}

            if {str(header_matching_enabled).lower()} and (count of headerThreadMessages) > 0 then
                set threadMessages to headerThreadMessages
                set selectedStrategy to "header"
            else if {str(header_matching_enabled).lower()} then
                set selectedStrategy to "subject_fallback"
                set threadMessages to subjectFallbackMessages
            else
                set threadMessages to subjectFallbackMessages
            end if

            -- Display thread messages
            set threadMatchedCount to count of threadMessages
            set outputText to outputText & "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" & return
            set outputText to outputText & "FOUND " & threadMatchedCount & " MESSAGE(S) IN THREAD" & return
            set outputText to outputText & "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" & return & return

            repeat with aMessage in threadMessages
                try
                    set messageSubject to subject of aMessage
                    set messageSender to sender of aMessage
                    set messageDate to date received of aMessage
                    set messageRead to read status of aMessage
                    set messageId to my sanitize_field(id of aMessage)
                    set internetMessageId to ""
                    try
                        set internetMessageId to my sanitize_field(message id of aMessage)
                    end try
                    try
                        set end of recordRows to "THREAD_ATTACHMENTS|||" & messageId & "|||" & ((count of mail attachments of aMessage) as string)
                    on error attachErr
                        -- The count reaches Python as null, a different answer
                        -- from 0, and the reason rides along so an unreadable
                        -- attachment list is diagnosable rather than merely
                        -- absent. Dropping the whole member here would be a
                        -- worse failure than not knowing its count.
                        set end of recordRows to "THREAD_ATTACHMENTS|||" & messageId & "|||-1|||" & my sanitize_field(attachErr)
                    end try
                    set mailboxName to my sanitize_field(name of mailbox of aMessage)
                    set accountName to my sanitize_field(name of account of mailbox of aMessage)
                    set receivedAt to my iso_datetime(messageDate)
                    {thread_headers_script}
                    set contentPreview to ""
                    {preview_collect_block}

                    if messageRead then
                        set readIndicator to "✓"
                        set readValue to "true"
                    else
                        set readIndicator to "✉"
                        set readValue to "false"
                    end if
                    {was_replied_fragment_script}
                    set repliedMarker to ""
                    if wasRepliedToken is "true" then set repliedMarker to "[REPLIED] "

                    set end of recordRows to messageId & "|||" & internetMessageId & "|||" & my sanitize_field(messageSubject) & "|||" & my sanitize_field(messageSender) & "|||" & mailboxName & "|||" & accountName & "|||" & readValue & "|||" & receivedAt & "|||" & contentPreview & "|||||||||" & inReplyTo & "|||" & refsValue & "|||" & "" & "|||" & wasRepliedToken

                    set outputText to outputText & readIndicator & " " & repliedMarker & messageSubject & return
                    set outputText to outputText & "   From: " & messageSender & return
                    set outputText to outputText & "   Date: " & (messageDate as string) & return
                    {preview_text_block}

                    set outputText to outputText & return
                on error
                    set threadRenderFailures to threadRenderFailures + 1
                end try
            end repeat{render_failure_report(escaped_render_scope)}
            set outputText to outputText & threadCoverageNotes

        on error errMsg
            return "Error: " & errMsg
        end try

        if "{output_format}" is "json" then
            set AppleScript's text item delimiters to return
            set outputRows to recordRows as string
            set AppleScript's text item delimiters to ""
            return "THREAD_STRATEGY|||" & selectedStrategy & "|||" & (threadMatchedCount as string) & return & outputRows
        end if

        return outputText
    end tell
    '''

    try:
        result = search.run_applescript(script, timeout=effective_timeout)
    except AppleScriptTimeout:
        return (
            f"Error: get_email_thread timed out on account '{account}' after "
            f"{effective_timeout}s. Retry with a larger timeout or tighter filters."
        )
    if output_format == "json":
        return build_thread_payload(
            result,
            ThreadRequest(
                account=account,
                resolved_mailbox=resolved_mailbox,
                mailboxes=mailboxes,
                cleaned_keyword=cleaned_keyword,
                thread_strategy=thread_strategy,
                include_preview=include_preview,
                recent_days_applied=effective_recent_days,
                max_messages=max_messages,
                scan_messages_applied=scan_cap,
                effective_timeout=effective_timeout,
                include_draft_state=include_draft_state,
                message_id=message_id,
                header_tokens=header_tokens,
                anchor=anchor,
                anchor_mailbox_resolved=anchor_mailbox_resolved,
            ),
        )
    return result
