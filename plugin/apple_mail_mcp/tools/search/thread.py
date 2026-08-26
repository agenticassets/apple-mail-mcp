"""Thread reconstruction tool plus its mailbox-selection script helper.

Subject/header matching helpers and the candidate-scan failure channel live
in ``thread_helpers``; they are re-exported here so the historical
``apple_mail_mcp.tools.search.thread.<name>`` attribute surface still works.

``run_applescript`` and ``validate_account_name`` are routed through the
``search`` package facade so the corresponding test patch seams keep firing.
"""

import json
from datetime import datetime, timedelta
from typing import Any

from apple_mail_mcp.applescript_snippets import iso_datetime_handlers, sanitize_field_handler, thread_headers_block
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.constants import THREAD_PREFIXES
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inject_preferences,
    normalize_message_ids,
)
from apple_mail_mcp.core.reply_state import was_replied_fragment
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import reply_state_wiring as _reply_state
from apple_mail_mcp.tools import search
from apple_mail_mcp.tools.search.by_id import _fetch_email_record_by_id
from apple_mail_mcp.tools.search.records import (
    _build_applescript_date,
    _mailbox_error_texts,
    _non_ceiling_errors,
    _parse_search_records,
    _script_error_message,
)
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
    _thread_error_type,
    candidate_failure_report,
    render_failure_report,
)
from apple_mail_mcp.tools.search.thread_helpers import (
    _thread_strip_prefixes_handler as _thread_strip_prefixes_handler,
)


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
          max_messages: Maximum number of thread messages to return (default: 50)
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

    if message_id:
        normalized_ids = normalize_message_ids([message_id])
        if not normalized_ids:
            return "Error: message_id must be a numeric Apple Mail message id"
        lookup_mailboxes = mailboxes or [mailbox]
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

    date_setup = ""
    if effective_recent_days > 0:
        cutoff = datetime.now() - timedelta(days=effective_recent_days)
        date_setup = _build_applescript_date("cutoffDate", cutoff.strftime("%Y-%m-%d"))

    if effective_recent_days <= 0:
        window_line = "Window: full inbox"
    elif effective_recent_days == 2.0:
        window_line = "Window: last 48h"
    else:
        window_line = f"Window: last {effective_recent_days}d"

    scan_cap = max_messages
    date_check = "if messageDate < cutoffDate then exit repeat" if effective_recent_days > 0 else ""
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
                                set messageCount to count of messages of currentMailbox
                                if messageCount > {scan_cap} then
                                    set scanUpperBound to {scan_cap}
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
        selection_strategy = thread_strategy
        parse_result = result
        script_error: str | None = None
        matched_count: int | None = None
        if result.startswith("THREAD_STRATEGY|||"):
            first_line, _, remaining = result.partition("\n")
            header_fields = first_line.split("|||")
            selection_strategy = header_fields[1].strip() or selection_strategy
            # Field 3 is the FOUND count. Text mode printed it and JSON mode
            # dropped it, so a JSON caller could not detect a truncated thread.
            if len(header_fields) > 2 and header_fields[2].strip().isdigit():
                matched_count = int(header_fields[2].strip())
            parse_result = remaining
        else:
            # The script's own `on error` handler returns "Error: <msg>" as the
            # whole result, which parses to zero rows. Without this check a
            # thread scan that threw is indistinguishable from an empty thread
            # (text mode already returns the raw error string below).
            script_error = _script_error_message(result)
        records, mailbox_errors = _parse_search_records(parse_result)
        sent_snapshots, sent_accounts_requested = _reply_state.new_sent_reply_scan()
        snapshots = _reply_state.annotate_rows_with_reply_state(
            records,
            runner=search.run_applescript,
            timeout=effective_timeout,
            include_draft_state=include_draft_state,
            include_sent_reply_state=True,
            date_field="received_date",
            sent_snapshots=sent_snapshots,
            sent_accounts_requested=sent_accounts_requested,
        )
        draft_scan = _reply_state.build_draft_scan_status(snapshots)
        rendered = len(records)
        if matched_count is None:
            matched_count = rendered
        # A candidate read that threw never entered ``threadMessages``, so it is
        # missing from ``FOUND N`` too: ``matched`` and ``returned`` are short
        # together and reconcile cleanly. This flag is the only thing that says
        # the thread itself may be incomplete.
        candidate_incomplete = any(
            _thread_error_type(item.get("message", "")) == "candidate_scan_error" for item in mailbox_errors
        )
        payload: dict[str, Any] = {
            "items": records,
            "returned": rendered,
            "matched": matched_count,
            "render_incomplete": matched_count > rendered,
            "candidate_scan_incomplete": candidate_incomplete,
            "account": account,
            "mailbox": resolved_mailbox,
            "mailboxes": mailboxes or [resolved_mailbox],
            "subject_keyword": cleaned_keyword,
            "strategy": thread_strategy,
            "selection_strategy": selection_strategy,
            "subject_fallback_used": selection_strategy == "subject_fallback",
            "include_preview": include_preview,
            "recent_days_applied": effective_recent_days,
            "max_messages": max_messages,
            "draft_scan": draft_scan,
            "sent_reply_scan": _reply_state.build_sent_reply_scan_status(sent_snapshots, sent_accounts_requested),
        }
        if script_error is not None:
            payload["error"] = script_error
            payload["errors"] = [script_error]
        # Keyed off the real failures, not the raw list: ``mailbox_errors`` can
        # carry ``SCAN_CEILING`` marker rows, which ``_non_ceiling_errors`` drops
        # because a saturated scan is a bound, not a failure. Keying off the raw
        # list would attach an EMPTY ``errors`` to a ceiling-only payload and
        # suppress the render reconciliation below.
        failures = _non_ceiling_errors(mailbox_errors)
        if failures:
            payload.setdefault("errors", []).extend(_mailbox_error_texts(failures))
            payload["error_details"] = [
                {"mailbox": item["mailbox"], "type": _thread_error_type(item["message"]), "message": item["message"]}
                for item in failures
            ]
        elif matched_count > rendered:
            # More thread messages counted than rows returned, with no
            # attribution from the script (e.g. a row the parser dropped).
            shortfall = f"thread render returned {rendered} of {matched_count} matched message(s); results incomplete"
            payload.setdefault("errors", []).append(shortfall)
            payload["error_details"] = [{"mailbox": resolved_mailbox, "type": "render_mismatch", "message": shortfall}]
        if anchor is not None:
            payload["anchor"] = {
                "message_id": anchor.get("message_id", ""),
                "internet_message_id": anchor.get("internet_message_id", ""),
                "subject": anchor.get("subject", ""),
                "mailbox": anchor.get("mailbox", resolved_mailbox),
                "in_reply_to": anchor.get("in_reply_to", ""),
                "references": anchor.get("references", ""),
            }
        if message_id and not header_tokens:
            payload["warnings"] = [
                "message_id anchor had no thread headers; subject fallback was used",
            ]
        return json.dumps(payload)
    return result
