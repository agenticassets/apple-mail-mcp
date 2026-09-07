"""Pure parse/format/response helpers and shared constants for search.

No AppleScript I/O lives here, so nothing in this module is patched as a test
seam — imports stay direct (core/constants/backend.base only).
"""

import json
from datetime import datetime
from typing import Any
from urllib.parse import quote

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.core import AppleScriptTimeout, escape_applescript
from apple_mail_mcp.core.reply_state import reply_state_tags
from apple_mail_mcp.tools.reply_state_wiring import sent_reply_scan_warning

MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def _build_applescript_date(var_name: str, date_value: str | None, end_of_day: bool = False) -> str:
    """Build AppleScript to create a date from an ISO day string."""
    if not date_value:
        return ""

    try:
        parsed_date = datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid date '{date_value}'. Use YYYY-MM-DD") from exc

    month_name = MONTH_NAMES[parsed_date.month - 1]
    seconds = 86399 if end_of_day else 0
    return f"""
                set {var_name} to current date
                set year of {var_name} to {parsed_date.year}
                set month of {var_name} to {month_name}
                set day of {var_name} to {parsed_date.day}
                set time of {var_name} to {seconds}
    """


_ERROR_MAILBOX_PREFIX = "ERROR_MAILBOX|||"
# Emitted by `script._SCAN_CEILING_MARKER` when a mailbox's candidate slice
# filled the hard scan ceiling. Deliberately not an ERROR_MAILBOX row: nothing
# failed, the scan simply stopped looking, and rendering it as an error would
# put a PARTIAL: line on ordinary healthy searches.
_SCAN_CEILING_PREFIX = "SCAN_CEILING|||"

#: ``type`` tag :func:`_parse_search_records` stamps on a ceiling row, and the
#: single name every consumer tests against. Whether a ceiling row counts as a
#: failure is decided once, in :func:`_non_ceiling_errors`; ``thread.py`` and
#: :func:`_build_search_response` both defer to it rather than re-deciding.
_SCAN_CEILING_ERROR_TYPE = "scan_ceiling"

#: Key carrying the *numeric* bound the marker row reported, kept as a string so
#: the ``list[dict[str, str]]`` shape every consumer already handles is
#: unchanged. The bound is per-mailbox and is not necessarily
#: ``SEARCH_HARD_CEILING``: it is whatever ``scan_cap`` the builder emitted for
#: that call (a body-capped scan stops at 25, a window-sized one anywhere
#: between the page floor and the ceiling). Reporting the constant instead let
#: the warning claim "the newest 50" for a scan that actually stopped at 25.
_SCAN_CEILING_BOUND_KEY = "scan_ceiling"

_SCRIPT_ERROR_PREFIXES = ("ERROR|||", "Error: ")


def _script_error_message(output: str) -> str | None:
    """Return the error text when *output* is a whole-script error sentinel.

    Search AppleScript wraps its body in ``on error`` handlers that return
    ``ERROR|||<msg>`` or ``Error: <msg>`` as the *entire* result. Neither
    string splits into 8 pipe fields, so handing it to
    ``_parse_search_records`` yields ``[]`` — a failed scan rendered as a
    clean empty result. Runners check this first and report the failure
    instead of an empty one (pattern P2, ``core.reply_state_wiring``).
    """
    head = output.split("\n", 1)[0].strip() if output else ""
    for prefix in _SCRIPT_ERROR_PREFIXES:
        if head.startswith(prefix):
            return head[len(prefix) :].strip() or head
    return None


def _read_failure_row(mailbox: str) -> str:
    """AppleScript emitting one ``ERROR_MAILBOX`` row when matched reads were lost.

    Pattern P1 (in-band marker + per-item count, as in ``search.script``). A
    per-message ``try`` that swallows a failed read leaves the message
    matched but unemitted, which a caller cannot tell apart from "that id is
    not in this mailbox". Comparing emitted rows against matched messages
    turns that difference back into a reportable fact;
    ``_parse_search_records`` already routes the row to ``mailbox_errors``.
    Assumes ``recordLines`` (emitted rows) and ``targetMessages`` (matched
    messages) are in scope, and must run before any non-record row is added.
    """
    return f"""
                    set matchedCount to count of targetMessages
                    if (count of recordLines) < matchedCount then
                        set end of recordLines to "{_ERROR_MAILBOX_PREFIX}{escape_applescript(mailbox)}|||read failed for " & ((matchedCount - (count of recordLines)) as string) & " of " & (matchedCount as string) & " matched message(s); results are incomplete"
                    end if
"""


def _non_ceiling_errors(mailbox_errors: list[dict[str, str]]) -> list[dict[str, str]]:
    """Drop scan-ceiling rows, leaving only rows that represent a real failure.

    A saturated scan is a bound, not a failure: the filter was applied to every
    message the scan looked at, and nothing threw. Rendering a ceiling row as an
    error would put a ``PARTIAL:`` line on ordinary healthy searches, so it is
    reported separately (``scan_ceiling_reached`` / a ``warnings`` entry).
    """
    return [item for item in mailbox_errors if item.get("type") != _SCAN_CEILING_ERROR_TYPE]


def _mailbox_error_texts(mailbox_errors: list[dict[str, str]]) -> list[str]:
    """Render parser mailbox errors as flat ``<mailbox>: <message>`` strings."""
    return [f"{item.get('mailbox') or '?'}: {item.get('message', '')}" for item in _non_ceiling_errors(mailbox_errors)]


def _parse_search_records(
    output: str,
) -> "tuple[list[dict[str, Any]], list[dict[str, str]]]":
    """Parse structured search output into (records, mailbox_errors).

    Each *mailbox_errors* entry is a dict with keys ``mailbox`` and ``message``
    for mailboxes that emitted an ``ERROR_MAILBOX|||`` marker line.

    Rows carry a 15th field (index 14, ``wasRepliedToken`` from
    ``core.reply_state.was_replied_fragment``) that becomes
    ``was_replied_to`` (bool, always present in the returned record: Mail's
    native read-only ``was replied to`` property, no parameter gates it). A
    14-field row (no 15th field, e.g. an older-shaped mocked payload) is
    tolerated defensively: ``was_replied_to`` simply defaults to ``False``
    rather than raising.
    """
    if not output:
        return [], []

    records = []
    mailbox_errors: list[dict[str, str]] = []
    for line in output.splitlines():
        if line.startswith(_ERROR_MAILBOX_PREFIX):
            tail = line[len(_ERROR_MAILBOX_PREFIX) :]
            mb, _, msg = tail.partition("|||")
            mailbox_errors.append({"mailbox": mb.strip(), "message": msg.strip()})
            continue
        if line.startswith(_SCAN_CEILING_PREFIX):
            tail = line[len(_SCAN_CEILING_PREFIX) :]
            mb, _, scanned = tail.partition("|||")
            bound = scanned.strip()
            entry = {
                "mailbox": mb.strip(),
                "type": _SCAN_CEILING_ERROR_TYPE,
                "message": (
                    f"scan stopped at the {bound}-message ceiling for this mailbox; "
                    "results are bounded by the scan, not by the filter"
                ),
            }
            # Keep the bound as data, not only as prose: `_build_search_response`
            # reports it verbatim instead of restating a constant.
            if bound.isdigit():
                entry[_SCAN_CEILING_BOUND_KEY] = bound
            mailbox_errors.append(entry)
            continue
        parts = line.split("|||", 14)
        if len(parts) < 8:
            continue

        internet_message_id = parts[1].strip()
        record: dict[str, Any] = {
            "message_id": parts[0].strip(),
            "internet_message_id": internet_message_id,
            "subject": parts[2].strip(),
            "sender": parts[3].strip(),
            "mailbox": parts[4].strip(),
            "account": parts[5].strip(),
            "is_read": parts[6].strip().lower() == "true",
            "received_date": parts[7].strip(),
            "was_replied_to": len(parts) > 14 and parts[14].strip().lower() == "true",
        }
        if internet_message_id:
            # Apple Mail requires: message:// scheme, angle brackets (percent-encoded),
            # and raw @ in the Message-ID. Normalize ID in case angle brackets are
            # present or missing (AppleScript returns both forms).
            msg_id = internet_message_id.strip("<>")
            record["mail_link"] = f"message://%3C{quote(msg_id, safe='@')}%3E"
        # Optional trailing fields, set only when present and non-empty.
        optional_fields = (
            (8, "content_preview"),
            (9, "to"),
            (10, "cc"),
            (11, "in_reply_to"),
            (12, "references"),
            (13, "bcc"),
        )
        for idx, key in optional_fields:
            if len(parts) > idx and parts[idx].strip():
                record[key] = parts[idx].strip()
        records.append(record)

    return records, mailbox_errors


def _sort_search_records(records: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    """Sort records by received date."""
    reverse = sort == "date_desc"
    return sorted(records, key=lambda item: item.get("received_date", ""), reverse=reverse)


def _format_error_details(error_details: list[dict[str, str]], scope_key: str) -> str:
    """Render ``error_details`` as ``<scope> (<type>: <message>)`` pairs.

    *scope_key* names the field that identifies the failure: ``account`` for
    account-level failures (every entry carries one) or ``mailbox`` for
    per-mailbox failures (only ``mailbox_error`` entries carry one).
    """
    return "; ".join(f"{item.get(scope_key, '?')} ({item['type']}: {item['message']})" for item in error_details)


def _format_search_records_text(
    records: list[dict[str, Any]],
    subject_only: bool = False,
    errors: list[str] | None = None,
    error_details: list[dict[str, str]] | None = None,
    recent_days_applied: float | None = None,
) -> str:
    """Format search records as human-readable text."""
    lines = []

    if subject_only:
        lines.append("SUBJECT SEARCH RESULTS")
        lines.append("")
        for item in records:
            lines.append(f"- {item['subject']}")
    else:
        lines.append("SEARCH RESULTS")
        if recent_days_applied is not None:
            if recent_days_applied <= 0:
                lines.append("Window: full inbox")
            elif recent_days_applied == 2.0:
                lines.append("Window: last 48h")
            else:
                lines.append(f"Window: last {recent_days_applied}d")
        lines.append("")
        for item in records:
            indicator = "✓" if item["is_read"] else "✉"
            was_replied = bool(item.get("was_replied_to") or item.get("already_replied"))
            tags = reply_state_tags(was_replied, item.get("has_draft"))
            tag_prefix = "".join(f"{tag} " for tag in tags)
            lines.append(f"{indicator} {tag_prefix}{item['subject']}")
            lines.append(f"   From: {item['sender']}")
            lines.append(f"   Date: {item['received_date']}")
            lines.append(f"   Mailbox: {item['mailbox']}")
            if item.get("mail_link"):
                lines.append(f"   Link: {item['mail_link']}")
            if item.get("content_preview"):
                lines.append(f"   Content: {item['content_preview']}")
            lines.append("")

    lines.append("========================================")
    lines.append(f"FOUND: {len(records)} matching email(s)")
    if errors:
        if error_details:
            lines.append(f"PARTIAL: {len(errors)} account issue(s): {_format_error_details(error_details, 'account')}")
        else:
            lines.append(f"PARTIAL: {len(errors)} account issue(s): {', '.join(errors)}")
    elif error_details:
        # A single-account scan reports per-mailbox failures (including the
        # per-message scan-failure counter from ``script._SCAN_FAILURE_REPORT``)
        # in ``error_details`` with nothing in ``errors``. Surface them here too:
        # text is the default ``output_format``, so without this a scan that
        # threw on every candidate would still render as a clean "FOUND: 0"
        # (AGENTIC-2344).
        lines.append(
            f"PARTIAL: {len(error_details)} mailbox issue(s): {_format_error_details(error_details, 'mailbox')}"
        )
    lines.append("========================================")
    return "\n".join(lines)


SENDER_ONLY_SEARCH_HINT = (
    "sender-only search can be slow on large mailboxes; add subject_keyword, "
    "date_from, has_attachments, or body_text (with allow_body_scan=True) to narrow the scan"
)
CONTENT_PREVIEW_SEARCH_HINT = (
    "include_content=True adds body previews to results and can be slower or expose more message text; "
    "leave it false for discovery, then fetch exact messages by id"
)
BODY_TEXT_SEARCH_HINT = (
    "body_text scans message bodies and can be slow or broad; keep account, date, subject, and limit filters tight"
)


def _body_scan_disabled_error() -> str:
    """Structured error when body_text is set without allow_body_scan opt-in."""
    tool_error = ToolError(
        code="BODY_SCAN_DISABLED",
        message=(
            "search_emails refuses body_text scans without allow_body_scan=True; "
            "body scans are O(N × message-size) on large mailboxes"
        ),
        remediation={
            "preferred": ("Narrow with subject_keyword, sender, date_from, or has_attachments instead"),
            "escape_hatch": "allow_body_scan=True (slow; pair with tight date_from)",
        },
    )
    return serialize_tool_error(tool_error)


def _build_search_response(
    records: list[dict[str, Any]],
    offset: int,
    limit: int,
    sort: str,
    output_format: str,
    subject_only: bool = False,
    errors: list[str] | None = None,
    error_details: list[dict[str, str]] | None = None,
    recent_days_applied: float | None = None,
    searched_from: str | None = None,
    body_search_capped: bool = False,
    mailbox_count_capped: bool = False,
    mailboxes_truncated: bool = False,
    sender_only_hint: bool = False,
    include_content_hint: bool = False,
    body_text_hint: bool = False,
    draft_scan: dict[str, Any] | None = None,
    sent_reply_scan: dict[str, Any] | None = None,
) -> str:
    """Return either JSON or text for search results.

    *draft_scan* (from ``tools.reply_state_wiring.build_draft_scan_status``,
    via ``annotate_rows_with_reply_state``) is surfaced as a top-level
    ``draft_scan`` key only in JSON output
    (``{"status": "ok" | "error" | "skipped", "scanned": N, "accounts": [...],
    "error"?: "..."}``); text output instead relies on the ``[REPLIED]`` /
    ``[HAS DRAFT]`` row prefixes already applied by ``_format_search_records_text``.
    """
    sorted_records = _sort_search_records(records, sort)
    has_more = len(sorted_records) > limit
    items = sorted_records[:limit]
    next_offset = offset + len(items) if has_more else None

    # A saturated scan is a bound, not a failure, so it is split back out of
    # error_details before anything renders it as a PARTIAL: line.
    #
    # `has_more` is deliberately left alone. It answers "is another page
    # reachable", and it already answers that correctly — paging does advance
    # through matches inside the scanned window. Flipping it to True whenever
    # the ceiling fired would make it True on every page forever, since the
    # mailbox saturates the scan every time, and a caller looping until
    # `has_more` goes false would never stop. The lie was never the pagination
    # bit; it was that `has_more: false` reads as "that is everything in the
    # mailbox" when it can only mean "that is everything in the newest N
    # messages". So the fix is to say which one it is.
    ceiling_details = [d for d in error_details or [] if d.get("type") == _SCAN_CEILING_ERROR_TYPE]
    # The bound each mailbox actually stopped at, as reported by its own marker
    # row. `SEARCH_HARD_CEILING` is only the fallback for an entry that carries
    # no bound (an older-shaped payload), never the answer when the scan said
    # otherwise. Mailboxes can disagree (a body-capped scan alongside a
    # window-sized one); report the largest, so the figure is never below any
    # single mailbox's bound and the named mailbox list stays the precise part.
    _reported_bounds = [
        int(d[_SCAN_CEILING_BOUND_KEY]) for d in ceiling_details if d.get(_SCAN_CEILING_BOUND_KEY, "").isdigit()
    ]
    _scan_ceiling = max(_reported_bounds) if _reported_bounds else SCAN_BOUNDS["SEARCH_HARD_CEILING"]
    _ceiling_mailboxes: list[str] = []
    _ceiling_warning = ""
    if ceiling_details:
        error_details = _non_ceiling_errors(error_details or []) or None
        _ceiling_mailboxes = sorted({d.get("mailbox", "") for d in ceiling_details if d.get("mailbox")})
        _named = f": {', '.join(_ceiling_mailboxes)}" if _ceiling_mailboxes else ""
        _ceiling_warning = (
            f"Scan ceiling reached: only the newest {_scan_ceiling} message(s) per mailbox were examined "
            f"({len(ceiling_details)} mailbox(es) hit it{_named}). "
            f"has_more={str(has_more).lower()} describes the scanned window only, not the mailbox — "
            "more matches may exist beyond it, and paging cannot reach them because each call re-clamps "
            "to the same ceiling. Narrow the search with date_from/date_to, a specific mailbox, or a "
            "tighter filter to bring the matches inside the window."
        )

    _max_mb_all = SCAN_BOUNDS["MAX_MAILBOXES_PER_SEARCH_ALL"]
    if output_format == "json":
        payload: dict[str, Any] = {
            "items": items,
            "offset": offset,
            "limit": limit,
            "returned": len(items),
            "has_more": has_more,
            "next_offset": next_offset,
            "sort": sort,
            "recent_days_applied": recent_days_applied if recent_days_applied is not None else 0.0,
            "searched_from": searched_from,
        }
        if ceiling_details:
            payload["scan_ceiling_reached"] = True
            payload["scan_ceiling"] = _scan_ceiling
            payload["scan_ceiling_mailboxes"] = _ceiling_mailboxes
            payload["scan_bounded"] = True
            payload.setdefault("warnings", []).append(_ceiling_warning)
        if body_search_capped:
            payload["body_search_capped"] = True
            _body_cap = SCAN_BOUNDS["BODY_SEARCH_AUTO_CAP"]
            payload["body_search_cap_warning"] = (
                f"body_text scan was capped at {_body_cap} messages because no explicit date_from "
                "was supplied. Pass date_from='YYYY-MM-DD' to search a larger window."
            )
        if mailboxes_truncated:
            payload["mailboxes_truncated"] = True
        if mailbox_count_capped:
            payload.setdefault("warnings", []).append(
                f"mailbox='All' search was capped at {_max_mb_all} mailboxes per account "
                "(SCAN_BOUNDS['MAX_MAILBOXES_PER_SEARCH_ALL']). Accounts with more than "
                f"{_max_mb_all} labels/folders (e.g. Gmail with 200+ labels) may have "
                "incomplete results. Pass mailbox='INBOX' or a specific folder name "
                "for a complete search."
            )
        if sender_only_hint:
            payload.setdefault("warnings", []).append(SENDER_ONLY_SEARCH_HINT)
        if include_content_hint:
            payload.setdefault("warnings", []).append(CONTENT_PREVIEW_SEARCH_HINT)
        if body_text_hint:
            payload.setdefault("warnings", []).append(BODY_TEXT_SEARCH_HINT)
        if errors:
            payload["errors"] = errors
        if error_details:
            payload["error_details"] = error_details
        if draft_scan is not None:
            payload["draft_scan"] = draft_scan
        if sent_reply_scan is not None:
            payload["sent_reply_scan"] = sent_reply_scan
        return json.dumps(payload)

    text_result = _format_search_records_text(
        items,
        subject_only=subject_only,
        errors=errors,
        error_details=error_details,
        recent_days_applied=recent_days_applied,
    )
    if ceiling_details:
        text_result = f"WARNING: {_ceiling_warning}\n" + text_result
    if body_search_capped:
        _body_cap = SCAN_BOUNDS["BODY_SEARCH_AUTO_CAP"]
        warning = (
            f"WARNING: body_text scan capped at {_body_cap} messages (no explicit date_from). "
            "Pass date_from='YYYY-MM-DD' to search a larger window.\n"
        )
        text_result = warning + text_result
    if mailbox_count_capped:
        mb_warning = (
            f"WARNING: mailbox='All' search capped at {_max_mb_all} mailboxes per account. "
            "Accounts with many labels (e.g. Gmail 200+ labels) may have incomplete results.\n"
        )
        text_result = mb_warning + text_result
    if sender_only_hint:
        text_result = f"WARNING: {SENDER_ONLY_SEARCH_HINT}\n" + text_result
    if include_content_hint:
        text_result = f"WARNING: {CONTENT_PREVIEW_SEARCH_HINT}\n" + text_result
    if body_text_hint:
        text_result = f"WARNING: {BODY_TEXT_SEARCH_HINT}\n" + text_result
    sent_warning = sent_reply_scan_warning(sent_reply_scan)
    if sent_warning:
        text_result = f"WARNING: {sent_warning}\n" + text_result
    return text_result


def _search_error_detail(account: str, exc: Exception) -> dict[str, str]:
    if isinstance(exc, AppleScriptTimeout):
        return {"account": account, "type": "timeout", "message": str(exc)}
    return {
        "account": account,
        "type": exc.__class__.__name__,
        "message": str(exc),
    }
