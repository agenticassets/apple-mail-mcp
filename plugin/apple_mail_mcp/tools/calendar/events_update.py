"""``update_event`` tool: ID-first PATCH with span rules and attendee diffing."""

from datetime import datetime, timedelta
from typing import Any

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.calendar_core import (
    bounded_calendar_window,
    build_event_set_lines,
    parse_iso_datetime,
    resolve_timezone,
    validate_alarms,
    validate_attendee_emails,
    validate_event_id,
    validate_rrule,
)
from apple_mail_mcp.core import AppleScriptTimeout, inject_preferences
from apple_mail_mcp.server import IDEMPOTENT_WRITE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import calendar as calendar_tools
from apple_mail_mcp.tools.calendar.helpers import (
    attendee_gate,
    calendar_write_blocked,
    collect_window_events,
    error_json,
    find_conflicts,
    finish,
    output_format_error,
    resolve_read_calendars,
    timeout_error,
    validate_on_conflict,
    widen_write_window_for_recurring,
)

_ATTENDEE_REMOVAL_NOTE = "Attendee removal is unsupported by Calendar.app scripting; no attendee change was applied."


def _render_update_text(payload: dict[str, Any]) -> str:
    """Compact human-readable rendering for update_event payloads."""
    event_id = payload.get("event_id")
    calendar = payload.get("calendar")
    if payload.get("dry_run"):
        head = f"dry-run: would update {event_id} on {calendar}"
    elif payload.get("updated"):
        head = f"updated {event_id} on {calendar}"
    else:
        head = payload.get("note") or f"no changes for {event_id}"
    lines = [head]
    for field, diff in (payload.get("changes") or {}).items():
        if isinstance(diff, dict) and "to" in diff:
            lines.append(f"  {field}: {diff.get('from')!r} -> {diff.get('to')!r}")
        else:
            lines.append(f"  {field}: {diff}")
    if payload.get("has_conflicts"):
        lines.append(f"! {len(payload.get('conflicts') or [])} conflict(s)")
    if payload.get("attendee_note"):
        lines.append(f"! {payload['attendee_note']}")
    return "\n".join(lines)


SPAN_UNSUPPORTED_REMEDIATION = {
    "supported": "span='all_occurrences' (Calendar.app scripting mutates the whole series)",
    "future_routes": [
        "ASObjC 'use framework \"EventKit\"' through the existing osascript path (save:span:error: supports per-occurrence spans)",
        "a signed, self-disclaiming EventKit helper binary",
    ],
}


def _require_span(recurring: bool, span: str | None) -> None:
    if not recurring:
        return
    if span is None:
        raise ToolError(
            code="RECURRING_SPAN_REQUIRED",
            message=(
                "This event is recurring; pass span='all_occurrences' to confirm the mutation "
                "applies to the whole series."
            ),
            remediation=SPAN_UNSUPPORTED_REMEDIATION,
        )
    if span in ("this_occurrence", "future_occurrences"):
        raise ToolError(
            code="RECURRING_SPAN_UNSUPPORTED",
            message=(
                f"span={span!r} is not supported by the AppleScript engine; Calendar.app scripting "
                "can only mutate the whole series."
            ),
            remediation=SPAN_UNSUPPORTED_REMEDIATION,
        )
    if span != "all_occurrences":
        raise ToolError(
            code="RECURRING_SPAN_REQUIRED",
            message=f"Unknown span {span!r}; use 'all_occurrences'.",
            remediation=SPAN_UNSUPPORTED_REMEDIATION,
        )


@mcp.tool(annotations=IDEMPOTENT_WRITE_TOOL_ANNOTATIONS, title="Update Event")
@inject_preferences
def update_event(
    event_id: str,
    calendar: str | None = None,
    lookup_days_back: float = 30.0,
    lookup_days_ahead: float = 90.0,
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    duration_minutes: int | None = None,
    timezone: str | None = None,
    all_day: bool | None = None,
    location: str | None = None,
    notes: str | None = None,
    url: str | None = None,
    alarms_minutes_before: list[int] | None = None,
    clear_alarms: bool = False,
    recurrence: str | None = None,
    clear_recurrence: bool = False,
    span: str | None = None,
    attendees: list[str] | None = None,
    send_invitations: bool = False,
    on_conflict: str = "warn",
    dry_run: bool = False,
    output_format: str = "json",
    timeout: int | None = None,
) -> str:
    """
    Update one event by exact id with PATCH semantics (None = unchanged).

    ID-first by construction: there is no fuzzy target parameter. The event is
    located with a bounded lookup (default 30 days back to 90 ahead; pass
    ``calendar`` to avoid the capped fan-out), then only the provided fields
    change. Recurring targets require ``span='all_occurrences'``; the
    AppleScript engine mutates whole series only and refuses
    ``this_occurrence``/``future_occurrences`` with
    ``RECURRING_SPAN_UNSUPPORTED``.

    Recurring-target lookup: the write locates a recurring series by the
    master's own start date, which can predate the read lookup window. The
    write-side lookup for recurring targets is automatically widened back by the
    recurring lookback horizon (400 days) to cover the master; a series whose
    master started earlier still returns ``EVENT_NOT_FOUND``, so widen
    ``lookup_days_back`` past the series start (or use the EventKit engine).

    All-day moves: to move an all-day event, pass ``all_day=True`` alongside the
    new ``start``/``end`` so the date lands on the requested calendar day in the
    requested zone regardless of the Mac's own zone.

    Attendee lists are diffed against the stored set: echoing the current
    attendees is a no-op needing no confirmation; adding new addresses is
    gated exactly like create_event (explicit ``send_invitations=True``,
    blocked under draft-safe). Attendee removal is not supported by
    Calendar.app scripting. ``alarms_minutes_before`` replaces the alarm set;
    ``on_conflict`` applies when start or end change. ``dry_run=True``
    previews the resolved target and field diff without writing.

    Args:
        event_id: Exact event id from a prior list/get call.
        calendar: Lookup selector that bounds the scan. Prefer calendar_id
            from list_calendars; a display name must be exact and unique.
        lookup_days_back: Lookup window days back (default 30).
        lookup_days_ahead: Lookup window days ahead (default 90).
        title: New title.
        start: New start, ISO 8601.
        end: New end, ISO 8601 (or duration_minutes).
        duration_minutes: New length in minutes from the effective start.
        timezone: IANA zone interpreting naive datetimes.
        all_day: Change the all-day flag.
        location: New location ("" clears).
        notes: New notes ("" clears).
        url: New URL ("" clears).
        alarms_minutes_before: Replacement alarm offsets.
        clear_alarms: Remove every alarm.
        recurrence: Replacement allowlisted RRULE.
        clear_recurrence: Remove the recurrence rule.
        span: Required for recurring targets; only 'all_occurrences'.
        attendees: Attendee emails; diffed against the stored set.
        send_invitations: Must be True when the attendee set changes.
        on_conflict: "warn" (default), "block", or "allow".
        dry_run: Preview the diff without writing.
        output_format: "json" (default) or "text".
        timeout: Optional AppleScript timeout in seconds.

    Returns:
        JSON payload with ``updated``, ``event_id``, and the applied diff.
    """
    fmt_error = output_format_error(output_format)
    if fmt_error:
        return fmt_error
    blocked = calendar_write_blocked("update_event")
    if blocked:
        return blocked

    try:
        target_id = validate_event_id(event_id)
        validate_on_conflict(on_conflict)
        if recurrence is not None and clear_recurrence:
            raise ToolError(
                code="INVALID_RECURRENCE_RULE",
                message="Pass recurrence or clear_recurrence, not both.",
            )
        if end is not None and duration_minutes is not None:
            raise ToolError(code="INVALID_EVENT_WINDOW", message="Pass end or duration_minutes, not both.")
        window = bounded_calendar_window(
            days_back=lookup_days_back,
            days_ahead=lookup_days_ahead,
            timezone_name=timezone,
        )
        scopes, _fan_out_capped = resolve_read_calendars(calendar, None, timeout=timeout)
        read_engine = calendar_tools.get_engine()
        found, _errors, _exhausted = collect_window_events(
            engine=read_engine,
            window=window,
            calendar_ids=[str(scope["calendar_id"]) for scope in scopes],
            calendar_selector_kinds={
                str(scope["calendar_id"]): str(scope.get("id_kind") or "name") for scope in scopes
            },
            expand_recurring=False,
            include_detail=True,
            event_ids=[target_id],
            timeout=timeout,
        )
        if not found:
            raise ToolError(
                code="EVENT_NOT_FOUND",
                message=(
                    f"Event {target_id!r} was not found inside the lookup window. Widen "
                    "lookup_days_back/lookup_days_ahead or pass the calendar it lives on."
                ),
            )
        current = found[0]
        _require_span(bool(current.get("recurring")), span)

        target_calendar = str(current.get("calendar"))
        target_calendar_id = str(current.get("calendar_id") or "")
        if not target_calendar_id:
            raise ToolError(code="CALENDAR_NOT_FOUND", message="The event did not retain its calendar_id.")
        target_selector_kind = next(
            (
                str(scope.get("id_kind") or "name")
                for scope in scopes
                if str(scope.get("calendar_id")) == target_calendar_id
            ),
            "calendar_object_reference",
        )
        stored_start = datetime.fromisoformat(str(current["start"]))
        stored_end = datetime.fromisoformat(str(current["end"])) if current.get("end") else stored_start

        tz, _resolved = resolve_timezone(timezone)
        new_start = parse_iso_datetime(start, tz, "start") if start is not None else None
        new_end = parse_iso_datetime(end, tz, "end") if end is not None else None
        if duration_minutes is not None:
            if duration_minutes <= 0:
                raise ToolError(code="INVALID_EVENT_WINDOW", message="duration_minutes must be positive.")
            new_end = (new_start or stored_start) + timedelta(minutes=duration_minutes)
        effective_start = new_start or stored_start
        effective_end = new_end or stored_end
        if (new_start is not None or new_end is not None) and effective_start >= effective_end:
            raise ToolError(code="INVALID_EVENT_WINDOW", message="Event start must be before event end.")

        canonical_rule = validate_rrule(recurrence) if recurrence is not None else None
        validated_alarms = validate_alarms(alarms_minutes_before) if alarms_minutes_before is not None else None

        add_attendees: list[str] | None = None
        attendees_changed = False
        attendee_removal_ignored = False
        if attendees is not None:
            requested = set(validate_attendee_emails(attendees))
            stored_attendees = {
                str(entry.get("email")).lower() for entry in current.get("attendees", []) if entry.get("email")
            }
            additions = requested - stored_attendees
            if additions:
                gated, attendee_error = attendee_gate(attendees, send_invitations, "update_event")
                if attendee_error:
                    return attendee_error
                add_attendees = sorted(set(gated or []) - stored_attendees)
                # F8: only a non-empty add is a real attendee change. Calendar.app
                # scripting cannot remove attendees, so a removal-only diff writes
                # no attendee lines and must not claim an attendee change happened.
                attendees_changed = bool(add_attendees)
            elif requested != stored_attendees:
                attendee_removal_ignored = True

        conflicts: list[dict[str, Any]] = []
        if (new_start is not None or new_end is not None) and on_conflict != "allow":
            conflicts = find_conflicts(
                calendar_id=target_calendar_id,
                selector_kind=target_selector_kind,
                start=effective_start,
                end=effective_end,
                timezone_name=timezone,
                timeout=timeout,
                exclude_event_id=target_id,
            )
            if conflicts and on_conflict == "block":
                return serialize_tool_error(
                    ToolError(
                        code="EVENT_CONFLICT",
                        message=f"{len(conflicts)} existing event(s) overlap the new time; nothing was changed.",
                        remediation={"conflicts": conflicts},
                    )
                )

        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = {"from": current.get("title"), "to": title}
        if new_start is not None:
            changes["start"] = {"from": current.get("start"), "to": new_start.isoformat()}
        if new_end is not None:
            changes["end"] = {"from": current.get("end"), "to": new_end.isoformat()}
        if all_day is not None:
            changes["all_day"] = {"from": current.get("all_day"), "to": all_day}
        if location is not None:
            changes["location"] = {"from": current.get("location"), "to": location}
        if notes is not None:
            changes["notes"] = {"from": current.get("notes"), "to": notes}
        if url is not None:
            changes["url"] = {"from": current.get("url"), "to": url}
        if canonical_rule is not None:
            changes["recurrence_rule"] = {"from": current.get("recurrence_rule"), "to": canonical_rule}
        if clear_recurrence:
            changes["recurrence_rule"] = {"from": current.get("recurrence_rule"), "to": None}
        if validated_alarms is not None:
            changes["alarms_minutes_before"] = {
                "from": current.get("alarms_minutes_before"),
                "to": validated_alarms,
            }
        if clear_alarms and validated_alarms is None:
            changes["alarms_minutes_before"] = {"from": current.get("alarms_minutes_before"), "to": []}
        if attendees_changed:
            changes["attendees_added"] = add_attendees

        if not changes:
            payload_unchanged: dict[str, Any] = {
                "updated": False,
                "event_id": target_id,
                "calendar": target_calendar,
                "calendar_id": target_calendar_id,
                "note": (
                    _ATTENDEE_REMOVAL_NOTE
                    if attendee_removal_ignored
                    else "No changes requested (attendee echoes of the stored set are a no-op)."
                ),
            }
            if attendee_removal_ignored:
                payload_unchanged["attendee_note"] = _ATTENDEE_REMOVAL_NOTE
            return finish(payload_unchanged, output_format, _render_update_text)

        if dry_run:
            payload_preview: dict[str, Any] = {
                "dry_run": True,
                "updated": False,
                "event_id": target_id,
                "calendar": target_calendar,
                "calendar_id": target_calendar_id,
                "changes": changes,
                "has_conflicts": bool(conflicts),
                "conflicts": conflicts,
                "span": span,
            }
            if attendee_removal_ignored:
                payload_preview["attendee_note"] = _ATTENDEE_REMOVAL_NOTE
            return finish(payload_preview, output_format, _render_update_text)

        write_engine = calendar_tools.get_write_engine()
        set_lines = build_event_set_lines(
            title=title,
            new_start=new_start,
            new_end=new_end,
            all_day=all_day,
            location=location,
            notes=notes,
            url=url,
            recurrence=canonical_rule,
            clear_recurrence=clear_recurrence,
            alarms_minutes_before=validated_alarms,
            clear_alarms=clear_alarms,
            add_attendees=add_attendees,
        )
        write_window = widen_write_window_for_recurring(window, bool(current.get("recurring")))
        updated_id = write_engine.update_event(
            calendar_id=target_calendar_id,
            event_id=target_id,
            window=write_window,
            set_lines=set_lines,
            timeout=timeout,
            selector_kind=target_selector_kind,
        )
    except AppleScriptTimeout:
        return timeout_error("update_event", timeout)
    except ToolError as exc:
        return error_json(exc)
    except Exception as exc:
        return f"Error: {exc}"

    payload: dict[str, Any] = {
        "updated": True,
        "event_id": updated_id,
        "calendar": target_calendar,
        "calendar_id": target_calendar_id,
        "changes": changes,
        "span": span,
        "has_conflicts": bool(conflicts),
        "conflicts": conflicts,
        "on_conflict": on_conflict,
        "engine": "applescript",
    }
    if attendees_changed:
        payload["invitation_delivery"] = "platform_dependent"
        payload["attendee_note"] = (
            "Attendee additions are attached but delivery is never guaranteed by the platform; "
            "attendee removal is not supported by Calendar.app scripting."
        )
    elif attendee_removal_ignored:
        payload["attendee_note"] = _ATTENDEE_REMOVAL_NOTE
    return finish(payload, output_format, _render_update_text)
