"""``check_availability`` tool: busy blocks and free slots folded in Python."""

import asyncio
from datetime import datetime, timedelta
from typing import Any

from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.calendar_core import (
    bounded_calendar_window,
    shifted_window,
    validate_slot_params,
    window_payload,
)
from apple_mail_mcp.constants import CALENDAR_BOUNDS
from apple_mail_mcp.core import AppleScriptTimeout, inject_preferences
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import calendar as calendar_tools
from apple_mail_mcp.tools.calendar.helpers import (
    collect_window_events,
    error_json,
    finish,
    output_format_error,
    recurring_lookback_disclosure,
    resolve_read_calendars,
    timeout_error,
    tz_for_window,
)


def _merge_busy(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _render_availability_text(payload: dict[str, Any]) -> str:
    lines = [f"Busy blocks: {len(payload['busy'])}  Free slots: {len(payload['free_slots'])}"]
    for block in payload["busy"]:
        lines.append(f"- busy {block['start']} .. {block['end']}  {block.get('title', '')}")
    for slot in payload["free_slots"]:
        lines.append(f"+ free {slot['start']} .. {slot['end']}")
    for err in payload.get("calendar_errors", []):
        lines.append(f"! {err}")
    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Check Availability")
@inject_preferences
async def check_availability(
    start: str,
    end: str,
    timezone: str | None = None,
    calendars: list[str] | None = None,
    slot_minutes: int = 30,
    working_hours_start: str = "09:00",
    working_hours_end: str = "17:00",
    weekdays_only: bool = True,
    max_slots: int = 20,
    ignore_all_day_events: bool = True,
    include_busy_blocks: bool = True,
    output_format: str = "json",
    timeout: int | None = None,
) -> str:
    """
    Answer "am I busy then" and "find me a free slot" over a bounded window.

    No native free-busy API exists on macOS on any engine, so this tool
    fetches the bounded window once (padded one day back so events that
    started earlier but overlap still count) and folds busy intervals and
    free slots in Python. The window is required and capped at 62 days.
    All-day events do not block by default (``ignore_all_day_events``).

    Scoping note (differs from mail's account scoping): with no ``calendars``
    argument this tool fans out across every calendar, capped at 20 plus an
    aggregate wall-clock budget; skipped calendars appear in
    ``calendar_errors``.

    Args:
        start: Required window start, ISO 8601.
        end: Required window end, ISO 8601 (width <= 62 days).
        timezone: IANA zone for interpretation and output.
        calendars: Optional explicit calendar scope.
        slot_minutes: Free-slot length, 5..480 minutes (default 30).
        working_hours_start: Daily slot search start, HH:MM (default 09:00).
        working_hours_end: Daily slot search end, HH:MM (default 17:00).
        weekdays_only: Skip Saturday and Sunday when finding slots.
        max_slots: Maximum free slots returned, 1..50 (default 20).
        ignore_all_day_events: All-day events do not block (default True).
        include_busy_blocks: Include the merged busy list in the response.
        output_format: "json" (default) or "text".
        timeout: Optional AppleScript timeout in seconds per engine call.

    Returns:
        JSON payload with ``busy``, ``free_slots``, ``window``, and ``engine``.
    """
    fmt_error = output_format_error(output_format)
    if fmt_error:
        return fmt_error
    try:
        slot_len, work_start_minute, work_end_minute = validate_slot_params(
            slot_minutes=slot_minutes,
            working_hours_start=working_hours_start,
            working_hours_end=working_hours_end,
            max_slots=max_slots,
        )
        window = bounded_calendar_window(
            start=start,
            end=end,
            timezone_name=timezone,
            max_window_days=int(CALENDAR_BOUNDS["AVAILABILITY_MAX_WINDOW_DAYS"]),
        )
        fetch_window = shifted_window(window, start_delta_days=-float(CALENDAR_BOUNDS["AVAILABILITY_FETCH_PAD_DAYS"]))
        scopes, fan_out_capped = resolve_read_calendars(None, calendars, timeout=timeout)
        engine = calendar_tools.get_engine()
        events, calendar_errors, budget_exhausted = await asyncio.to_thread(
            collect_window_events,
            engine=engine,
            window=fetch_window,
            calendar_ids=[str(scope["calendar_id"]) for scope in scopes],
            calendar_selector_kinds={
                str(scope["calendar_id"]): str(scope.get("id_kind") or "name") for scope in scopes
            },
            expand_recurring=True,
            timeout=timeout,
            fail_on_total_failure=bool(calendars),
        )
    except AppleScriptTimeout:
        return timeout_error("check_availability", timeout)
    except ToolError as exc:
        return error_json(exc)

    tz = tz_for_window(window)
    window_start = window.start
    window_end = window.end
    busy_intervals: list[tuple[datetime, datetime]] = []
    busy_payload: list[dict[str, Any]] = []
    for event in events:
        if ignore_all_day_events and event.get("all_day"):
            continue
        if str(event.get("status") or "").lower() == "cancelled":
            continue
        event_start = datetime.fromisoformat(str(event["start_utc"]))
        end_text = event.get("end_utc")
        event_end = datetime.fromisoformat(str(end_text)) if end_text else event_start
        if event_start >= window_end or event_end <= window_start:
            continue
        clipped = (max(event_start, window_start), min(event_end, window_end))
        busy_intervals.append(clipped)
        busy_payload.append(
            {
                "start": clipped[0].astimezone(tz).isoformat(),
                "end": clipped[1].astimezone(tz).isoformat(),
                "title": event.get("title"),
                "calendar": event.get("calendar"),
                "calendar_id": event.get("calendar_id"),
                "event_id": event.get("event_id"),
            }
        )
    merged = _merge_busy(busy_intervals)

    free_slots: list[dict[str, str]] = []
    day = window_start.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    slot_step = timedelta(minutes=slot_len)
    while day.astimezone(tz).date() <= window_end.astimezone(tz).date() and len(free_slots) < max_slots:
        if not (weekdays_only and day.weekday() >= 5):
            work_start = day + timedelta(minutes=work_start_minute)
            work_end = day + timedelta(minutes=work_end_minute)
            cursor = max(work_start, window_start.astimezone(tz))
            day_end = min(work_end, window_end.astimezone(tz))
            while cursor + slot_step <= day_end and len(free_slots) < max_slots:
                slot_start_utc = cursor.astimezone(window_start.tzinfo)
                slot_end_utc = (cursor + slot_step).astimezone(window_start.tzinfo)
                blocked = any(b_start < slot_end_utc and b_end > slot_start_utc for b_start, b_end in merged)
                if not blocked:
                    free_slots.append({"start": cursor.isoformat(), "end": (cursor + slot_step).isoformat()})
                cursor += slot_step
        day += timedelta(days=1)

    payload: dict[str, Any] = {
        "busy": busy_payload if include_busy_blocks else [],
        "busy_block_count": len(busy_payload),
        "free_slots": free_slots,
        "engine": engine.name,
        "resolved_timezone": window.timezone_name,
        "window": window_payload(window),
        "calendars_scanned": [str(scope["name"]) for scope in scopes],
        "calendar_ids_scanned": [str(scope["calendar_id"]) for scope in scopes],
        "fan_out_capped": fan_out_capped,
        "calendar_errors": calendar_errors,
        "budget_exhausted": budget_exhausted,
        "slot_minutes": slot_len,
        "working_hours": {"start": working_hours_start, "end": working_hours_end},
        "weekdays_only": weekdays_only,
    }
    disclosure = recurring_lookback_disclosure(engine, True)
    if disclosure:
        payload.update(disclosure)
    return finish(payload, output_format, _render_availability_text)
