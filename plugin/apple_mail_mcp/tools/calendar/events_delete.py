"""``delete_events`` tool: exact-id bulk delete with dry-run-first discipline."""

from typing import Any

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.calendar_core import (
    bounded_calendar_window,
    normalize_event_ids,
    window_payload,
)
from apple_mail_mcp.constants import CALENDAR_BOUNDS
from apple_mail_mcp.core import AppleScriptTimeout, inject_preferences
from apple_mail_mcp.server import DESTRUCTIVE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import calendar as calendar_tools
from apple_mail_mcp.tools.calendar.events_update import _require_span
from apple_mail_mcp.tools.calendar.helpers import (
    calendar_delete_blocked,
    collect_window_events,
    error_json,
    finish,
    output_format_error,
    resolve_read_calendars,
    surviving_recurring_occurrences,
    timeout_error,
    widen_write_window_for_recurring,
)


def _render_delete_text(payload: dict[str, Any]) -> str:
    """Compact human-readable rendering for delete_events payloads."""
    if payload.get("dry_run"):
        targets = payload.get("would_delete") or []
        lines = [f"dry-run: would delete {len(targets)} event(s)"]
        for target in targets:
            lines.append(
                f"- {target.get('start')}  {target.get('title')}  "
                f"id: {target.get('event_id')}  [{target.get('calendar')}]"
            )
        note = payload.get("recurring_note")
        if note:
            lines.append(f"! {note}")
        errors = payload.get("lookup_errors") or []
    else:
        deleted = payload.get("deleted") or []
        lines = [f"deleted {payload.get('deleted_count', len(deleted))} of {payload.get('requested', len(deleted))}"]
        for row in deleted:
            lines.append(f"- {row.get('title')}  id: {row.get('event_id')}")
        errors = payload.get("errors") or []
    for err in errors:
        lines.append(f"! {err}")
    return "\n".join(lines)


@mcp.tool(annotations=DESTRUCTIVE_TOOL_ANNOTATIONS, title="Delete Events")
@inject_preferences
def delete_events(
    event_ids: list[str],
    calendar: str | None = None,
    start: str | None = None,
    end: str | None = None,
    days_back: float = 30.0,
    days_ahead: float = 90.0,
    span: str | None = None,
    dry_run: bool = True,
    max_deletes: int = int(CALENDAR_BOUNDS["BULK_DELETE_DEFAULT_MAX"]),
    output_format: str = "json",
    timeout: int | None = None,
) -> str:
    """
    Delete events by exact ids only; dry-run preview is the default.

    Never deletes by query. Every id must resolve inside the bounded lookup
    window before anything is deleted: one unresolved id fails the whole call
    with ``EVENT_NOT_FOUND`` (a typo never partially deletes). Recurring ids
    require ``span='all_occurrences'`` as an explicit whole-series confirmation,
    but Calendar.app scripting cannot actually delete a whole recurring series
    (its ``delete`` removes only the targeted occurrence and rule-clearing is
    ignored), so the tool verifies the series after deleting and returns
    ``RECURRING_DELETE_INCOMPLETE`` (with the surviving occurrence dates) when the
    series is not fully gone; delete a whole series in Calendar.app for a reliable
    removal. The write-side lookup for recurring targets is widened back by the
    recurring lookback horizon (400 days) to locate the master; a series whose
    master started earlier returns ``EVENT_NOT_FOUND``, so widen ``days_back`` past
    the series start. Under
    --draft-safe the tool refuses with ``CALENDAR_DELETE_BLOCKED`` unless the
    operator launched with ``CALENDAR_ALLOW_DESTRUCTIVE=1``; under
    --read-only the tool is removed from the registry.

    Caps: at most ``max_deletes`` ids (default 20) with an absolute ceiling
    of 100 per call; ids are chunked 25 per osascript call internally.

    Args:
        event_ids: 1..100 exact event ids from a prior list/get call.
        calendar: Lookup selector that bounds the scan. Prefer calendar_id
            from list_calendars; a display name must be exact and unique.
        start: Optional absolute lookup window start, ISO 8601 (requires end).
        end: Optional absolute lookup window end.
        days_back: Relative lookup window days back (default 30).
        days_ahead: Relative lookup window days ahead (default 90).
        span: Required when any target is recurring; only 'all_occurrences'.
        dry_run: True (default) previews the resolved targets without deleting.
        max_deletes: Per-call delete cap (default 20, ceiling 100).
        output_format: "json" (default) or "text".
        timeout: Optional AppleScript timeout in seconds per engine call.

    Returns:
        JSON payload with the preview or the deleted-event summaries.
    """
    fmt_error = output_format_error(output_format)
    if fmt_error:
        return fmt_error
    blocked = calendar_delete_blocked("delete_events")
    if blocked:
        return blocked
    ceiling = int(CALENDAR_BOUNDS["BULK_DELETE_CEILING"])
    if max_deletes <= 0 or max_deletes > ceiling:
        return serialize_tool_error(
            ToolError(
                code="TOO_MANY_DELETES",
                message=f"max_deletes must be between 1 and {ceiling}; got {max_deletes}.",
            )
        )

    try:
        ids = normalize_event_ids(event_ids, max_ids=ceiling, cap_code="TOO_MANY_DELETES")
        if len(ids) > max_deletes:
            raise ToolError(
                code="TOO_MANY_DELETES",
                message=f"Received {len(ids)} ids but max_deletes={max_deletes}. Raise max_deletes deliberately or split the call.",
            )
        window = bounded_calendar_window(
            start=start,
            end=end,
            days_back=days_back,
            days_ahead=days_ahead,
            timezone_name=None,
        )
        scopes, _fan_out_capped = resolve_read_calendars(calendar, None, timeout=timeout)
        read_engine = calendar_tools.get_engine()
        found, lookup_errors, _exhausted = collect_window_events(
            engine=read_engine,
            window=window,
            calendar_ids=[str(scope["calendar_id"]) for scope in scopes],
            expand_recurring=False,
            event_ids=ids,
            timeout=timeout,
        )
        by_id = {str(event["event_id"]): event for event in found}
        missing = [event_id for event_id in ids if event_id not in by_id]
        if missing:
            raise ToolError(
                code="EVENT_NOT_FOUND",
                message=(
                    f"{len(missing)} of {len(ids)} ids did not resolve inside the lookup window; nothing was deleted."
                ),
                remediation={
                    "missing": missing,
                    "preferred": "Re-run list_events/get_events_by_id, or widen days_back/days_ahead.",
                },
            )
        recurring_targets = [event_id for event_id in ids if by_id[event_id].get("recurring")]
        if recurring_targets:
            _require_span(True, span)

        targets = [
            {
                "event_id": event_id,
                "title": by_id[event_id].get("title"),
                "start": by_id[event_id].get("start"),
                "calendar": by_id[event_id].get("calendar"),
                "calendar_id": by_id[event_id].get("calendar_id"),
                "recurring": bool(by_id[event_id].get("recurring")),
            }
            for event_id in ids
        ]

        if dry_run:
            payload_preview: dict[str, Any] = {
                "dry_run": True,
                "deleted": [],
                "would_delete": targets,
                "recurring_note": (
                    "Recurring delete is best-effort: Calendar.app scripting removes only individual "
                    "occurrences, so the whole series may not be deleted. The tool verifies after "
                    "deleting and returns RECURRING_DELETE_INCOMPLETE (with the surviving occurrence "
                    "dates) if any remain. To reliably remove a whole series, delete it in Calendar.app."
                    if recurring_targets
                    else None
                ),
                "window": window_payload(window),
                "lookup_errors": lookup_errors,
                "next_step": "Re-run with dry_run=False to delete these events.",
            }
            return finish(payload_preview, output_format, _render_delete_text)

        write_engine = calendar_tools.get_write_engine()
        write_window = widen_write_window_for_recurring(window, bool(recurring_targets))
        deleted: list[dict[str, str]] = []
        delete_errors: list[str] = []
        ids_by_calendar: dict[str, list[str]] = {}
        for event_id in ids:
            ids_by_calendar.setdefault(str(by_id[event_id].get("calendar_id")), []).append(event_id)
        for calendar_id, calendar_ids in ids_by_calendar.items():
            chunk_deleted, chunk_errors = write_engine.delete_events(
                calendar_id=calendar_id,
                event_ids=calendar_ids,
                window=write_window,
                timeout=timeout,
            )
            deleted.extend(chunk_deleted)
            for row in chunk_deleted:
                row["calendar_id"] = calendar_id
            delete_errors.extend(f"{calendar_id}: {err}" for err in chunk_errors)
        # Verify-after-delete for recurring targets: Calendar.app scripting removes
        # only the targeted occurrence, so re-query the series and never claim a
        # whole-series delete the write did not achieve.
        recurring_survivors: dict[str, list[str]] = {}
        if recurring_targets:
            recurring_survivors = surviving_recurring_occurrences(
                engine=read_engine,
                window=write_window,
                calendar_ids=[str(scope["calendar_id"]) for scope in scopes],
                recurring_ids=recurring_targets,
                timeout=timeout,
            )
    except AppleScriptTimeout:
        return timeout_error("delete_events", timeout)
    except ToolError as exc:
        return error_json(exc)
    except Exception as exc:
        return f"Error: {exc}"

    if recurring_survivors:
        return serialize_tool_error(
            ToolError(
                code="RECURRING_DELETE_INCOMPLETE",
                message=(
                    "Calendar.app scripting cannot delete a whole recurring series; it removed only "
                    "individual occurrence(s) and the rest survive. The delete was verified after "
                    "running and is incomplete."
                ),
                remediation={
                    "surviving_occurrences": recurring_survivors,
                    "occurrences_removed": deleted,
                    "manual": (
                        "Open Calendar.app, select any occurrence of the series, choose Delete, and pick "
                        "'Delete All' to remove the whole series."
                    ),
                },
            )
        )

    payload: dict[str, Any] = {
        "dry_run": False,
        "requested": len(ids),
        "deleted": deleted,
        "deleted_count": len(deleted),
        "errors": delete_errors + lookup_errors,
        "span": span,
        # Only true when a recurring target existed AND verification found zero
        # surviving occurrences (survivors return RECURRING_DELETE_INCOMPLETE above).
        "recurring_deleted_whole_series": bool(recurring_targets),
        "window": window_payload(window),
        "engine": "applescript",
    }
    return finish(payload, output_format, _render_delete_text)
