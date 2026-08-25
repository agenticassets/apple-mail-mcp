"""``batch_create_events`` tool: capped batch creation for personal blocking."""

from typing import Any

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.calendar_core import all_day_echo_instants, validate_alarms
from apple_mail_mcp.constants import CALENDAR_BOUNDS
from apple_mail_mcp.core import AppleScriptTimeout, inject_preferences
from apple_mail_mcp.server import WRITE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import calendar as calendar_tools
from apple_mail_mcp.tools.calendar.events_create import resolve_event_times
from apple_mail_mcp.tools.calendar.helpers import (
    calendar_write_blocked,
    error_json,
    find_conflicts,
    finish,
    output_format_error,
    resolve_create_target,
    timeout_error,
    validate_on_conflict,
)


def _render_batch_text(payload: dict[str, Any]) -> str:
    """Compact human-readable rendering for batch_create_events payloads."""
    calendar = payload.get("calendar")
    if payload.get("dry_run"):
        items = payload.get("would_create") or []
        lines = [f"dry-run: would create {len(items)} event(s) on {calendar}"]
        for item in items:
            conflicts = item.get("conflicts") or []
            flag = f"  ! {len(conflicts)} conflict(s)" if conflicts else ""
            lines.append(f"- {item.get('start')}  {item.get('title')}{flag}")
        return "\n".join(lines)
    created = payload.get("created") or []
    failed = payload.get("failed") or []
    lines = [f"created {len(created)}, failed {len(failed)} on {calendar}"]
    for item in created:
        lines.append(f"+ {item.get('start')}  {item.get('title')}  id: {item.get('event_id')}")
    for item in failed:
        lines.append(f"! [{item.get('index')}] {item.get('title')}: {item.get('error')}")
    return "\n".join(lines)


_ALLOWED_ITEM_KEYS = {
    "title",
    "start",
    "end",
    "duration_minutes",
    "all_day",
    "location",
    "notes",
    "url",
    "alarms_minutes_before",
    "timezone",
}
_FORBIDDEN_ITEM_KEYS = {"attendees", "recurrence", "send_invitations"}


def _validate_item(item: dict[str, Any], index: int, batch_timezone: str | None) -> dict[str, Any]:
    """Validate one batch item; raises ToolError with the item index."""
    forbidden = _FORBIDDEN_ITEM_KEYS & set(item)
    if forbidden:
        raise ToolError(
            code="INVALID_EVENT_WINDOW",
            message=(
                f"events[{index}] contains {sorted(forbidden)}; batch items may not carry attendees or "
                "recurrence. Use create_event for invites and recurring series."
            ),
        )
    unknown = set(item) - _ALLOWED_ITEM_KEYS
    if unknown:
        raise ToolError(
            code="INVALID_EVENT_WINDOW",
            message=f"events[{index}] has unknown keys {sorted(unknown)}; allowed: {sorted(_ALLOWED_ITEM_KEYS)}.",
        )
    title = str(item.get("title") or "").strip()
    if not title:
        raise ToolError(code="INVALID_EVENT_WINDOW", message=f"events[{index}] is missing a title.")
    if not item.get("start"):
        raise ToolError(code="INVALID_EVENT_WINDOW", message=f"events[{index}] is missing a start.")
    all_day = bool(item.get("all_day", False))
    start_dt, end_dt, resolved_tz = resolve_event_times(
        start=str(item["start"]),
        end=str(item["end"]) if item.get("end") is not None else None,
        duration_minutes=int(item["duration_minutes"]) if item.get("duration_minutes") is not None else None,
        all_day=all_day,
        timezone_name=str(item["timezone"]) if item.get("timezone") else batch_timezone,
    )
    alarms_raw = item.get("alarms_minutes_before")
    alarms = validate_alarms(list(alarms_raw)) if alarms_raw else None
    return {
        "index": index,
        "title": title,
        "start": start_dt,
        "end": end_dt,
        "all_day": all_day,
        "location": str(item["location"]) if item.get("location") is not None else None,
        "notes": str(item["notes"]) if item.get("notes") is not None else None,
        "url": str(item["url"]) if item.get("url") is not None else None,
        "alarms_minutes_before": alarms,
        "resolved_timezone": resolved_tz,
    }


@mcp.tool(annotations=WRITE_TOOL_ANNOTATIONS, title="Batch Create Events")
@inject_preferences
def batch_create_events(
    events: list[dict[str, Any]],
    calendar: str | None = None,
    timezone: str | None = None,
    on_conflict: str = "warn",
    dry_run: bool = False,
    output_format: str = "json",
    timeout: int | None = None,
) -> str:
    """
    Create up to 25 one-off events on one calendar in a single call.

    Every item validates before any write (all-or-nothing validation); the
    write phase then runs per item with partial results (``created`` and
    ``failed``). Items accept title, start, end or duration_minutes, all_day,
    location, notes, url, alarms_minutes_before, and timezone; items may NOT
    carry attendees or recurrence (use create_event for those, which gates
    them properly). ``dry_run=True`` previews all items with conflicts and
    writes nothing. ``on_conflict="block"`` refuses the whole batch when any
    item overlaps an existing event.

    Args:
        events: 1..25 item dicts (see above for keys).
        calendar: One target calendar for the whole batch. Prefer the opaque
            calendar_id from list_calendars; a display name must be exact and
            unique. If omitted, DEFAULT_CALENDAR then the engine default apply.
        timezone: Batch default IANA zone; items may override.
        on_conflict: "warn" (default), "block", or "allow".
        dry_run: Validate and conflict-preview everything, write nothing.
        output_format: "json" (default) or "text".
        timeout: Optional AppleScript timeout in seconds per engine call.

    Returns:
        JSON payload with per-item results.
    """
    fmt_error = output_format_error(output_format)
    if fmt_error:
        return fmt_error
    blocked = calendar_write_blocked("batch_create_events")
    if blocked:
        return blocked
    cap = int(CALENDAR_BOUNDS["BATCH_CREATE_CAP"])
    if not events:
        return "Error: events must contain at least one item."
    if len(events) > cap:
        return serialize_tool_error(
            ToolError(
                code="BATCH_TOO_LARGE",
                message=f"batch_create_events accepts at most {cap} items per call; got {len(events)}.",
                remediation={"preferred": f"Split into batches of {cap} or fewer items."},
            )
        )

    try:
        validate_on_conflict(on_conflict)
        specs = [_validate_item(item, index, timezone) for index, item in enumerate(events)]
        target = resolve_create_target(calendar, timeout=timeout)
        if on_conflict != "allow":
            for spec in specs:
                spec["conflicts"] = find_conflicts(
                    calendar_id=str(target["calendar_id"]),
                    start=spec["start"],
                    end=spec["end"],
                    timezone_name=timezone,
                    timeout=timeout,
                )
        else:
            for spec in specs:
                spec["conflicts"] = []
        conflicted = [spec for spec in specs if spec["conflicts"]]
        if conflicted and on_conflict == "block":
            return serialize_tool_error(
                ToolError(
                    code="EVENT_CONFLICT",
                    message=f"{len(conflicted)} of {len(specs)} items overlap existing events; nothing was created.",
                    remediation={
                        "conflicted_items": [spec["index"] for spec in conflicted],
                        "options": "Retry with on_conflict='warn' or 'allow', or reschedule the conflicted items.",
                    },
                )
            )
    except AppleScriptTimeout:
        return timeout_error("batch_create_events", timeout)
    except ToolError as exc:
        return error_json(exc)

    def _spec_payload(spec: dict[str, Any]) -> dict[str, Any]:
        # All-day items store on the host-local calendar date, so echo host-local
        # midnight to match a later read (bug 4), never the requested-zone instant.
        if spec["all_day"]:
            start_iso, _su, end_iso, _eu = all_day_echo_instants(spec["start"], spec["end"])
        else:
            start_iso = spec["start"].isoformat()
            end_iso = spec["end"].isoformat()
        return {
            "index": spec["index"],
            "calendar": target["name"],
            "calendar_id": target["calendar_id"],
            "title": spec["title"],
            "start": start_iso,
            "end": end_iso,
            "all_day": spec["all_day"],
            "conflicts": spec["conflicts"],
        }

    if dry_run:
        payload = {
            "dry_run": True,
            "calendar": target["name"],
            "calendar_id": target["calendar_id"],
            "would_create": [_spec_payload(spec) for spec in specs],
            "on_conflict": on_conflict,
            "engine": "applescript",
        }
        return finish(payload, output_format, _render_batch_text)

    engine = calendar_tools.get_write_engine()
    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for spec in specs:
        try:
            event_id = engine.create_event(
                calendar_id=str(target["calendar_id"]),
                title=spec["title"],
                start=spec["start"],
                end=spec["end"],
                all_day=spec["all_day"],
                location=spec["location"],
                notes=spec["notes"],
                url=spec["url"],
                alarms_minutes_before=spec["alarms_minutes_before"],
                timeout=timeout,
            )
            row = _spec_payload(spec)
            row["event_id"] = event_id
            created.append(row)
        except AppleScriptTimeout:
            failed.append({"index": spec["index"], "title": spec["title"], "error": "timed out"})
        except ToolError as exc:
            failed.append({"index": spec["index"], "title": spec["title"], "error": f"[{exc.code}] {exc.message}"})
        except Exception as exc:
            failed.append({"index": spec["index"], "title": spec["title"], "error": str(exc)})

    payload = {
        "dry_run": False,
        "calendar": target["name"],
        "calendar_id": target["calendar_id"],
        "created": created,
        "failed": failed,
        "created_count": len(created),
        "failed_count": len(failed),
        "on_conflict": on_conflict,
        "engine": "applescript",
    }
    return finish(payload, output_format, _render_batch_text)
