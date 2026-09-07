"""JSON payload assembly for ``get_email_thread``.

Pure Python: this module parses one thread scan's raw AppleScript output and
turns it into the tool's JSON contract. It contains no AppleScript, which is
why it can live in its own file without a bare-``try`` ratchet allowance --
``thread.py`` was at the 600-line module budget and the payload block was the
one large chunk with no script text in it.

``run_applescript`` and the reply-state helpers are reached through the same
module attributes ``thread.py`` uses, so ``patch("apple_mail_mcp.tools.search.
run_applescript")`` and the ``reply_state_wiring`` seams keep firing.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from apple_mail_mcp.tools import reply_state_wiring as _reply_state
from apple_mail_mcp.tools import search
from apple_mail_mcp.tools.search.records import (
    _mailbox_error_texts,
    _non_ceiling_errors,
    _parse_search_records,
    _script_error_message,
)
from apple_mail_mcp.tools.search.thread_helpers import (
    ThreadMarkers,
    _thread_error_type,
    split_thread_markers,
)

_STRATEGY_PREFIX = "THREAD_STRATEGY|||"


@dataclass
class ThreadRequest:
    """The call parameters the JSON payload has to report back.

    Everything here is already resolved (mailbox names, the cleaned subject,
    the effective bounds), so the payload builder never re-derives a value the
    script was actually run with -- reporting a bound the scan did not use is
    the exact class of dishonesty this whole change is about.
    """

    account: str
    resolved_mailbox: str
    mailboxes: list[str] | None
    cleaned_keyword: str
    thread_strategy: str
    include_preview: bool
    recent_days_applied: float
    max_messages: int
    scan_messages_applied: int
    effective_timeout: int
    include_draft_state: bool
    message_id: str | None = None
    header_tokens: list[str] = field(default_factory=list)
    anchor: dict[str, Any] | None = None
    anchor_mailbox_resolved: bool = False


def _anchor_summary(anchor: dict[str, Any], fallback_mailbox: str) -> dict[str, str]:
    return {
        "message_id": anchor.get("message_id", ""),
        "internet_message_id": anchor.get("internet_message_id", ""),
        "subject": anchor.get("subject", ""),
        "mailbox": anchor.get("mailbox", fallback_mailbox),
        "in_reply_to": anchor.get("in_reply_to", ""),
        "references": anchor.get("references", ""),
    }


def _retain_anchor(records: list[dict[str, Any]], anchor: dict[str, Any] | None) -> bool:
    """Append the anchor row when the scan did not return it. Returns True if added.

    The anchor is the one message the caller already named, and it was fetched
    and read successfully before the scan ran. A thread that omits it is
    self-evidently wrong, and it drops out whenever the anchor's own mailbox
    fell outside ``mailboxes`` or behind the scan bound -- which is precisely
    the situation this tool is being fixed for.
    """
    if anchor is None:
        return False
    anchor_id = str(anchor.get("message_id", "")).strip()
    if not anchor_id:
        return False
    if any(str(record.get("message_id", "")).strip() == anchor_id for record in records):
        return False
    records.append(
        {
            "message_id": anchor_id,
            "internet_message_id": anchor.get("internet_message_id", ""),
            "subject": anchor.get("subject", ""),
            "sender": anchor.get("sender", ""),
            "mailbox": anchor.get("mailbox", ""),
            "account": anchor.get("account", ""),
            "is_read": anchor.get("is_read"),
            "received_date": anchor.get("received_date", ""),
            "content_preview": anchor.get("content_preview", ""),
            "in_reply_to": anchor.get("in_reply_to", ""),
            "references": anchor.get("references", ""),
            "was_replied_to": anchor.get("was_replied_to"),
            "anchor_recovered": True,
        }
    )
    return True


def _apply_attachment_counts(records: list[dict[str, Any]], markers: ThreadMarkers) -> None:
    """Attach each member's attachment count, or None when it was not read.

    ``None`` and ``0`` are different answers -- "we never looked" versus "there
    are none" -- and conflating them is what let a thread export report three
    signature images as the conversation's attachments. A member whose count
    could not be read never reaches ``attachment_counts`` (``split_thread_
    markers`` routes it to ``attachment_errors``), so a missing key is exactly
    the "we never looked" case.
    """
    for record in records:
        record["attachment_count"] = markers.attachment_counts.get(str(record.get("message_id", "")).strip())


def build_thread_payload(raw_result: str, request: ThreadRequest) -> str:
    """Parse one thread scan's raw output into the tool's JSON string."""
    # Coverage markers must come out before _parse_search_records runs: it
    # splits record rows on split("|||", 14), so a marker row left in the
    # stream would be read as a malformed record.
    cleaned, markers = split_thread_markers(raw_result)

    selection_strategy = request.thread_strategy
    parse_result = cleaned
    script_error: str | None = None
    matched_count: int | None = None
    if cleaned.startswith(_STRATEGY_PREFIX):
        first_line, _, remaining = cleaned.partition("\n")
        header_fields = first_line.split("|||")
        selection_strategy = header_fields[1].strip() or selection_strategy
        # Field 3 is the FOUND count. Text mode printed it and JSON mode
        # dropped it, so a JSON caller could not detect a truncated thread.
        if len(header_fields) > 2 and header_fields[2].strip().isdigit():
            matched_count = int(header_fields[2].strip())
        parse_result = remaining
    else:
        # The script's own `on error` handler returns "Error: <msg>" as the
        # whole result, which parses to zero rows. Without this check a thread
        # scan that threw is indistinguishable from an empty thread.
        script_error = _script_error_message(cleaned)

    records, mailbox_errors = _parse_search_records(parse_result)
    _apply_attachment_counts(records, markers)
    anchor_recovered = _retain_anchor(records, request.anchor)

    sent_snapshots, sent_accounts_requested = _reply_state.new_sent_reply_scan()
    snapshots = _reply_state.annotate_rows_with_reply_state(
        records,
        runner=search.run_applescript,
        timeout=request.effective_timeout,
        include_draft_state=request.include_draft_state,
        include_sent_reply_state=True,
        date_field="received_date",
        sent_snapshots=sent_snapshots,
        sent_accounts_requested=sent_accounts_requested,
    )

    rendered = len(records)
    # Taken before the anchor adjustment below: the script stops appending at
    # ``max_messages``, so FOUND N lands exactly on the limit when the return
    # bound truncated the thread. No marker row is emitted for this and
    # ``matched`` and ``returned`` agree, so without this check the truncation
    # is invisible -- and ``export_emails(scope="thread")`` passes its own
    # ``max_emails`` (default 25) straight through as ``max_messages``.
    return_limit_reached = matched_count is not None and matched_count >= request.max_messages
    if matched_count is None:
        matched_count = rendered
    elif anchor_recovered:
        # The anchor was appended after the script counted, so FOUND N would
        # otherwise read one short and trip the render-mismatch reconciliation.
        matched_count += 1

    # A candidate read that threw never entered ``threadMessages``, so it is
    # missing from ``FOUND N`` too: ``matched`` and ``returned`` are short
    # together and reconcile cleanly. This flag is the only thing that says the
    # thread itself may be incomplete.
    candidate_incomplete = any(
        _thread_error_type(item.get("message", "")) == "candidate_scan_error" for item in mailbox_errors
    )
    render_incomplete = matched_count > rendered

    payload: dict[str, Any] = {
        "items": records,
        "returned": rendered,
        "matched": matched_count,
        "render_incomplete": render_incomplete,
        "candidate_scan_incomplete": candidate_incomplete,
        # The one flag a caller should branch on: a bound the caller did NOT
        # choose cut this thread short. Every component below was False on the
        # live 9-member thread that returned 5 and called itself complete.
        # ``anchor_recovered`` counts -- a scan that missed the one message the
        # caller named almost certainly missed its neighbours too.
        #
        # The ``recent_days`` date floor and the ``max_messages`` return bound
        # are deliberately NOT part of this. Both are bounds the caller chose,
        # and the date floor in particular fires whenever a mailbox holds
        # anything older than the window -- nearly always. A flag that is true
        # on every call is one callers learn to ignore. Each gets its own
        # reported bound below, so a caller who needs more than they asked for
        # can still see which of their own limits stopped the scan.
        "thread_incomplete": bool(
            render_incomplete
            or candidate_incomplete
            or markers.scan_ceilings
            or anchor_recovered
            or script_error is not None
        ),
        "window_truncated": bool(markers.date_floors),
        "return_limit_reached": return_limit_reached,
        "scan_ceiling_hit": sorted(markers.scan_ceilings),
        "date_floor_hit": sorted(markers.date_floors),
        "account": request.account,
        "mailbox": request.resolved_mailbox,
        "mailboxes": request.mailboxes or [request.resolved_mailbox],
        "subject_keyword": request.cleaned_keyword,
        "strategy": request.thread_strategy,
        "selection_strategy": selection_strategy,
        "subject_fallback_used": selection_strategy == "subject_fallback",
        "include_preview": request.include_preview,
        "recent_days_applied": request.recent_days_applied,
        "max_messages": request.max_messages,
        "scan_messages_applied": request.scan_messages_applied,
        "draft_scan": _reply_state.build_draft_scan_status(snapshots),
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
    elif render_incomplete:
        # More thread messages counted than rows returned, with no attribution
        # from the script (e.g. a row the parser dropped).
        shortfall = f"thread render returned {rendered} of {matched_count} matched message(s); results incomplete"
        payload.setdefault("errors", []).append(shortfall)
        payload["error_details"] = [
            {"mailbox": request.resolved_mailbox, "type": "render_mismatch", "message": shortfall}
        ]

    if request.anchor is not None:
        payload["anchor"] = _anchor_summary(request.anchor, request.resolved_mailbox)

    warnings = list(markers.warnings())
    if return_limit_reached:
        warnings.append(
            f"The thread filled its max_messages return bound ({request.max_messages}); more members "
            "may exist than were returned. Raise max_messages to see them."
        )
    if anchor_recovered:
        warnings.append(
            "The anchor message was not returned by the thread scan and was added from the "
            "direct fetch; other members in its mailbox were likely missed too."
        )
    if request.anchor_mailbox_resolved:
        warnings.append(
            'mailbox="All": the anchor was fetched from its own mailbox, and the thread '
            "scan still covers every mailbox of the account. Pass an explicit `mailboxes` "
            "list to bound the scan instead."
        )
    if request.message_id and not request.header_tokens:
        warnings.append("message_id anchor had no thread headers; subject fallback was used")
    if warnings:
        payload["warnings"] = warnings

    return json.dumps(payload)
