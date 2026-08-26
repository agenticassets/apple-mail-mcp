"""``create_event`` tool: conflict-checked, gated single-event creation."""

from datetime import datetime, timedelta
from typing import Any

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.calendar_core import (
    all_day_echo_instants,
    isoformat_pair,
    parse_iso_datetime,
    resolve_timezone,
    validate_alarms,
    validate_rrule,
)
from apple_mail_mcp.core import AppleScriptTimeout, inject_preferences
from apple_mail_mcp.server import WRITE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import calendar as calendar_tools
from apple_mail_mcp.tools.calendar.helpers import (
    attendee_gate,
    calendar_write_blocked,
    error_json,
    find_conflicts,
    finish,
    output_format_error,
    resolve_create_target,
    timeout_error,
    validate_on_conflict,
)

_MAX_DURATION_MINUTES = 370 * 24 * 60


def resolve_event_times(
    *,
    start: str,
    end: str | None,
    duration_minutes: int | None,
    all_day: bool,
    timezone_name: str | None,
) -> tuple[datetime, datetime, str]:
    """Resolve (start, end, resolved_timezone) for a create/batch item."""
    tz, resolved_name = resolve_timezone(timezone_name)
    start_dt = parse_iso_datetime(start, tz, "start")
    if end is not None and duration_minutes is not None:
        raise ToolError(
            code="INVALID_EVENT_WINDOW",
            message="Pass end or duration_minutes, not both.",
        )
    if all_day:
        start_dt = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        if end is not None:
            end_dt = parse_iso_datetime(end, tz, "end").replace(hour=0, minute=0, second=0, microsecond=0)
        elif duration_minutes is not None:
            end_dt = start_dt + timedelta(minutes=duration_minutes)
        else:
            end_dt = start_dt + timedelta(days=1)
    elif end is not None:
        end_dt = parse_iso_datetime(end, tz, "end")
    elif duration_minutes is not None:
        if not 1 <= duration_minutes <= _MAX_DURATION_MINUTES:
            raise ToolError(
                code="INVALID_EVENT_WINDOW",
                message=f"duration_minutes must be between 1 and {_MAX_DURATION_MINUTES}.",
            )
        end_dt = start_dt + timedelta(minutes=duration_minutes)
    else:
        raise ToolError(
            code="INVALID_EVENT_WINDOW",
            message="Pass end or duration_minutes (all-day events may omit both).",
        )
    if start_dt >= end_dt:
        raise ToolError(code="INVALID_EVENT_WINDOW", message="Event start must be before event end.")
    return start_dt, end_dt, resolved_name


@mcp.tool(annotations=WRITE_TOOL_ANNOTATIONS, title="Create Event")
@inject_preferences
def create_event(
    title: str,
    start: str,
    end: str | None = None,
    duration_minutes: int | None = None,
    calendar: str | None = None,
    timezone: str | None = None,
    all_day: bool = False,
    location: str | None = None,
    notes: str | None = None,
    url: str | None = None,
    alarms_minutes_before: list[int] | None = None,
    recurrence: str | None = None,
    attendees: list[str] | None = None,
    send_invitations: bool = False,
    on_conflict: str = "warn",
    output_format: str = "json",
    timeout: int | None = None,
) -> str:
    """
    Create one calendar event with conflict detection on by default.

    Times are timezone-correct: pass offset-aware ISO 8601 or a naive
    datetime plus an IANA ``timezone``; the event lands on the correct
    absolute instant regardless of the Mac's zone. Exactly one of ``end`` or
    ``duration_minutes`` is required unless ``all_day=True`` (which defaults
    to one day). ``on_conflict`` runs a bounded overlap check over the new
    event's own span: "warn" (default) creates and reports ``conflicts``,
    "block" refuses with ``EVENT_CONFLICT`` writing nothing, "allow" skips
    the check.

    Attendees are gated as outward-facing: they require explicit
    ``send_invitations=True``, are blocked under --draft-safe/--read-only,
    and the response always reports ``invitation_delivery:
    "platform_dependent"`` because no public macOS API guarantees invitation
    transmission. The reliable alternative is an .ics invitation drafted
    through the gated Mail compose path.

    The target calendar resolves from ``calendar``, then the
    ``DEFAULT_CALENDAR`` environment variable, then the engine default.
    Recurrence accepts the allowlisted RRULE grammar (FREQ, INTERVAL, COUNT,
    UNTIL, BYDAY, BYMONTHDAY, BYMONTH). At most 5 alarms, each 0..40320
    minutes before start.

    Args:
        title: Event title.
        start: Event start, ISO 8601.
        end: Event end, ISO 8601 (or use duration_minutes).
        duration_minutes: Event length in minutes (alternative to end).
        calendar: Target selector. Prefer calendar_id from list_calendars; a
            display name must be exact and unique.
        timezone: IANA zone interpreting naive datetimes.
        all_day: Create an all-day event.
        location: Optional location text.
        notes: Optional notes text.
        url: Optional URL.
        alarms_minutes_before: Alarm offsets in minutes before start.
        recurrence: Optional allowlisted RRULE string.
        attendees: Optional attendee email addresses (gated).
        send_invitations: Must be True whenever attendees are passed.
        on_conflict: "warn" (default), "block", or "allow".
        output_format: "json" (default) or "text".
        timeout: Optional AppleScript timeout in seconds.

    Returns:
        JSON payload with ``created``, ``event_id``, echoes, and conflicts.
    """
    fmt_error = output_format_error(output_format)
    if fmt_error:
        return fmt_error
    blocked = calendar_write_blocked("create_event")
    if blocked:
        return blocked
    if not title or not title.strip():
        return "Error: title cannot be empty."
    validated_attendees, attendee_error = attendee_gate(attendees, send_invitations, "create_event")
    if attendee_error:
        return attendee_error

    try:
        validate_on_conflict(on_conflict)
        start_dt, end_dt, resolved_tz = resolve_event_times(
            start=start,
            end=end,
            duration_minutes=duration_minutes,
            all_day=all_day,
            timezone_name=timezone,
        )
        validated_alarms = validate_alarms(alarms_minutes_before) if alarms_minutes_before else None
        canonical_rule = validate_rrule(recurrence) if recurrence else None
        target = resolve_create_target(calendar, timeout=timeout)
        conflicts: list[dict[str, Any]] = []
        if on_conflict != "allow":
            conflicts = find_conflicts(
                calendar_id=str(target["calendar_id"]),
                selector_kind=str(target.get("id_kind") or "name"),
                start=start_dt,
                end=end_dt,
                timezone_name=timezone,
                timeout=timeout,
            )
        if conflicts and on_conflict == "block":
            return serialize_tool_error(
                ToolError(
                    code="EVENT_CONFLICT",
                    message=f"{len(conflicts)} existing event(s) overlap the requested time; nothing was created.",
                    remediation={
                        "conflicts": conflicts,
                        "options": "Retry with on_conflict='warn' or 'allow', or pick a free slot via check_availability.",
                    },
                )
            )
        engine = calendar_tools.get_write_engine()
        event_id = engine.create_event(
            calendar_id=str(target["calendar_id"]),
            title=title,
            start=start_dt,
            end=end_dt,
            all_day=all_day,
            location=location,
            notes=notes,
            url=url,
            recurrence=canonical_rule,
            alarms_minutes_before=validated_alarms,
            attendees=validated_attendees,
            timeout=timeout,
            selector_kind=str(target.get("id_kind") or "name"),
        )
    except AppleScriptTimeout:
        return timeout_error("create_event", timeout)
    except ToolError as exc:
        return error_json(exc)
    except Exception as exc:
        return f"Error: {exc}"

    tz, _ = resolve_timezone(timezone)
    if all_day:
        start_local, start_utc, end_local, end_utc = all_day_echo_instants(start_dt, end_dt)
    else:
        start_local, start_utc = isoformat_pair(start_dt, tz)
        end_local, end_utc = isoformat_pair(end_dt, tz)
    payload: dict[str, Any] = {
        "created": True,
        "event_id": event_id,
        "calendar": target["name"],
        "calendar_id": target["calendar_id"],
        "title": title,
        "start": start_local,
        "start_utc": start_utc,
        "end": end_local,
        "end_utc": end_utc,
        "all_day": all_day,
        "location": location,
        "notes": notes,
        "url": url,
        "recurrence_rule": canonical_rule,
        "alarms_minutes_before": validated_alarms or [],
        "resolved_timezone": resolved_tz,
        "engine": "applescript",
        "has_conflicts": bool(conflicts),
        "conflicts": conflicts,
        "on_conflict": on_conflict,
    }
    if validated_attendees:
        payload["attendees"] = validated_attendees
        payload["invitation_delivery"] = "platform_dependent"
        payload["invitation_note"] = (
            "macOS offers no public API that guarantees invitation transmission; verify in "
            "Calendar.app, or draft an .ics invitation through the gated Mail compose path."
        )
    return finish(payload, output_format)
