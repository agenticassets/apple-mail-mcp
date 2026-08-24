"""``get_events_by_id`` tool: full detail for exact event ids, window-bounded."""

import asyncio
from typing import Any

from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.calendar_core import bounded_calendar_window, normalize_event_ids, window_payload
from apple_mail_mcp.constants import CALENDAR_BOUNDS
from apple_mail_mcp.core import AppleScriptTimeout, inject_preferences
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import calendar as calendar_tools
from apple_mail_mcp.tools.calendar.helpers import (
    collect_window_events,
    error_json,
    finish,
    output_format_error,
    render_events_text,
    resolve_read_calendars,
    timeout_error,
)


def _render_get_events_text(payload: dict[str, Any]) -> str:
    """Compact rendering: the event list plus any unresolved ids."""
    lines = [render_events_text(payload)]
    missing = payload.get("missing") or []
    if missing:
        lines.append(f"! missing: {', '.join(str(event_id) for event_id in missing)}")
    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Events by ID")
@inject_preferences
async def get_events_by_id(
    event_ids: list[str],
    calendar: str | None = None,
    start: str | None = None,
    end: str | None = None,
    days_back: float = 30.0,
    days_ahead: float = 90.0,
    timezone: str | None = None,
    output_format: str = "json",
    timeout: int | None = None,
) -> str:
    """
    Fetch full detail for exact event ids from a prior list_events call.

    Detail includes untruncated notes, the alarm list (minutes before start),
    and the read-only attendee list (name, email, participation status). The
    lookup is always window-bounded (default: 30 days back to 90 days ahead)
    because an unbounded uid scan is the single most expensive Calendar.app
    operation; pass ``calendar`` whenever known to avoid the capped
    multi-calendar fan-out. At most 25 ids per call; ids that fail to resolve
    inside the window are listed in ``missing`` instead of failing the call.

    Args:
        event_ids: 1..25 exact event ids.
        calendar: Calendar selector that bounds the scan. Prefer calendar_id
            from list_calendars; a display name must be exact and unique.
        start: Optional absolute lookup window start, ISO 8601 (requires end).
        end: Optional absolute lookup window end.
        days_back: Relative lookup window days back (default 30).
        days_ahead: Relative lookup window days ahead (default 90).
        timezone: IANA zone for output (default host zone).
        output_format: "json" (default) or "text".
        timeout: Optional AppleScript timeout in seconds per engine call.

    Returns:
        JSON payload: ``events`` (full detail) and ``missing`` (unresolved ids).
    """
    fmt_error = output_format_error(output_format)
    if fmt_error:
        return fmt_error
    try:
        ids = normalize_event_ids(event_ids, max_ids=int(CALENDAR_BOUNDS["MAX_EVENT_IDS_PER_CALL"]))
        window = bounded_calendar_window(
            start=start,
            end=end,
            days_back=days_back,
            days_ahead=days_ahead,
            timezone_name=timezone,
        )
        scopes, fan_out_capped = resolve_read_calendars(calendar, None, timeout=timeout)
        engine = calendar_tools.get_engine()
        events, calendar_errors, budget_exhausted = await asyncio.to_thread(
            collect_window_events,
            engine=engine,
            window=window,
            calendar_ids=[str(scope["calendar_id"]) for scope in scopes],
            expand_recurring=False,
            include_detail=True,
            event_ids=ids,
            timeout=timeout,
        )
    except AppleScriptTimeout:
        return timeout_error("get_events_by_id", timeout)
    except ToolError as exc:
        return error_json(exc)

    found = {str(event.get("event_id")) for event in events}
    payload: dict[str, Any] = {
        "events": events,
        "missing": [event_id for event_id in ids if event_id not in found],
        "engine": engine.name,
        "resolved_timezone": window.timezone_name,
        "window": window_payload(window),
        "calendars_scanned": [str(scope["name"]) for scope in scopes],
        "calendar_ids_scanned": [str(scope["calendar_id"]) for scope in scopes],
        "fan_out_capped": fan_out_capped,
        "calendar_errors": calendar_errors,
        "budget_exhausted": budget_exhausted,
    }
    return finish(payload, output_format, _render_get_events_text)
