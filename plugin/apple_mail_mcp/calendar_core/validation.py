"""Calendar input validation: name resolution, event ids, alarms, attendees, slots.

Calendar UIDs are UUID-like strings, so none of the numeric Mail id
primitives (``normalize_message_ids`` etc.) apply here. This module owns the
string-shaped id doctrine plus calendar selector resolution: the opaque
``calendar_id`` returned by ``list_calendars`` is preferred; a display name is
accepted only when it is an exact, unique match. Duplicate names and missing
selectors return structured errors with candidates.
"""

from __future__ import annotations

import re
from typing import Any

from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.calendar_core.recurrence import parse_rrule
from apple_mail_mcp.constants import CALENDAR_BOUNDS

EVENT_ID_MAX_LEN = 512

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
# Deliberately simple address shape check: local@domain.tld with no spaces.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_ALARM_MINUTES = 40_320  # four weeks


def validate_event_id(event_id: str) -> str:
    """Shape-validate a single calendar event uid; returns the trimmed id."""
    trimmed = (event_id or "").strip()
    if not trimmed:
        raise ToolError(
            code="INVALID_EVENT_ID",
            message="Event ids must be non-empty strings from a prior list_events/get_events_by_id call.",
        )
    if len(trimmed) > EVENT_ID_MAX_LEN:
        raise ToolError(
            code="INVALID_EVENT_ID",
            message=f"Event id exceeds {EVENT_ID_MAX_LEN} characters; this is not a Calendar uid.",
        )
    if "|||" in trimmed or _CONTROL_CHARS.search(trimmed) or '"' in trimmed or "\\" in trimmed:
        raise ToolError(
            code="INVALID_EVENT_ID",
            message="Event id contains characters that are never present in Calendar uids.",
        )
    return trimmed


def normalize_event_ids(event_ids: list[str], *, max_ids: int, cap_code: str = "INVALID_EVENT_ID") -> list[str]:
    """Validate and de-duplicate a list of event uids, preserving order.

    Raises ``INVALID_EVENT_ID`` for shape failures and *cap_code* (default
    ``INVALID_EVENT_ID``; delete paths pass ``TOO_MANY_DELETES``) when more
    than *max_ids* ids are supplied.
    """
    if not event_ids:
        raise ToolError(
            code="INVALID_EVENT_ID",
            message="Pass at least one event id.",
        )
    seen: dict[str, None] = {}
    for raw in event_ids:
        seen.setdefault(validate_event_id(raw), None)
    ids = list(seen)
    if len(ids) > max_ids:
        raise ToolError(
            code=cap_code,
            message=f"Received {len(ids)} event ids; the cap for this call is {max_ids}.",
            remediation={"preferred": f"Split into batches of {max_ids} or fewer ids."},
        )
    return ids


def resolve_calendar_selector(query: str, calendars: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve an exact calendar ID, or an exact name when it is unique.

    Calendar display names are not identifiers: Calendar.app permits duplicate
    names across accounts.  The opaque ``calendar_id`` from ``list_calendars``
    is therefore the preferred selector and is the only value passed below
    this boundary.
    """
    wanted = (query or "").strip()
    if not wanted:
        raise ToolError(code="CALENDAR_NOT_FOUND", message="Calendar selector cannot be empty.")

    id_matches = [calendar for calendar in calendars if str(calendar.get("calendar_id")) == wanted]
    if len(id_matches) == 1:
        return id_matches[0]

    name_matches = [calendar for calendar in calendars if str(calendar.get("name")) == wanted]
    if len(name_matches) == 1:
        return name_matches[0]
    candidates = [
        {"name": str(calendar.get("name")), "calendar_id": str(calendar.get("calendar_id"))}
        for calendar in name_matches
    ]
    if len(name_matches) > 1:
        if all(str(calendar.get("id_kind")) == "name" for calendar in name_matches):
            raise ToolError(
                code="CALENDAR_IDENTIFIER_UNAVAILABLE",
                message=(
                    f"Calendar name {query!r} identifies {len(name_matches)} calendars, but Calendar.app "
                    "did not expose stable object-reference ids for this listing."
                ),
                remediation={
                    "preferred": "Enable the EventKit read engine and call list_calendars again.",
                    "fallback": "Rename one calendar in Calendar.app so the exact display name is unique.",
                },
            )
        raise ToolError(
            code="AMBIGUOUS_CALENDAR_SELECTOR",
            message=f"Calendar name {query!r} identifies {len(name_matches)} calendars.",
            remediation={"candidates": candidates, "preferred": "Pass calendar_id from list_calendars."},
        )
    raise ToolError(
        code="CALENDAR_NOT_FOUND",
        message=f"No calendar matches {query!r}.",
        remediation={
            "candidates": [
                {"name": str(calendar.get("name")), "calendar_id": str(calendar.get("calendar_id"))}
                for calendar in calendars[:5]
            ],
            "preferred": "Call list_calendars first.",
        },
    )


def resolve_calendar_name(query: str, names: list[str]) -> str:
    """Legacy exact-name helper; new tools use ``resolve_calendar_selector``."""
    return str(resolve_calendar_selector(query, [{"calendar_id": name, "name": name} for name in names])["name"])


def validate_rrule(rule: str) -> str:
    """Validate an RRULE against the allowlist grammar; returns canonical text."""
    return parse_rrule(rule).canonical()


def validate_alarms(alarms_minutes_before: list[int]) -> list[int]:
    """Validate the alarm list: count cap and per-alarm minute range."""
    cap = int(CALENDAR_BOUNDS["MAX_ALARMS_PER_EVENT"])
    if len(alarms_minutes_before) > cap:
        raise ToolError(
            code="INVALID_ALARM",
            message=f"At most {cap} alarms per event; got {len(alarms_minutes_before)}.",
        )
    validated: list[int] = []
    for minutes in alarms_minutes_before:
        if not isinstance(minutes, int) or isinstance(minutes, bool) or not 0 <= minutes <= _MAX_ALARM_MINUTES:
            raise ToolError(
                code="INVALID_ALARM",
                message=(
                    f"Alarm offsets must be integers between 0 and {_MAX_ALARM_MINUTES} "
                    f"minutes before the event start; got {minutes!r}."
                ),
            )
        validated.append(minutes)
    return validated


def validate_attendee_emails(attendees: list[str]) -> list[str]:
    """Validate and normalize (lowercase, de-duplicated) attendee addresses."""
    cap = int(CALENDAR_BOUNDS["MAX_ATTENDEES"])
    if len(attendees) > cap:
        raise ToolError(
            code="TOO_MANY_ATTENDEES",
            message=f"At most {cap} attendees per event; got {len(attendees)}.",
        )
    normalized: dict[str, None] = {}
    for raw in attendees:
        address = (raw or "").strip()
        if not _EMAIL_RE.match(address):
            raise ToolError(
                code="INVALID_ATTENDEE_EMAIL",
                message=f"Attendee {raw!r} is not a valid email address.",
            )
        normalized.setdefault(address.lower(), None)
    return list(normalized)


def validate_slot_params(
    *, slot_minutes: int, working_hours_start: str, working_hours_end: str, max_slots: int
) -> tuple[int, int, int]:
    """Validate slot-finding knobs; returns (slot_minutes, start_minute, end_minute)."""
    if not isinstance(slot_minutes, int) or isinstance(slot_minutes, bool) or not 5 <= slot_minutes <= 480:
        raise ToolError(
            code="INVALID_SLOT_PARAMS",
            message=f"slot_minutes must be an integer between 5 and 480; got {slot_minutes!r}.",
        )
    if not isinstance(max_slots, int) or isinstance(max_slots, bool) or not 1 <= max_slots <= 50:
        raise ToolError(
            code="INVALID_SLOT_PARAMS",
            message=f"max_slots must be an integer between 1 and 50; got {max_slots!r}.",
        )
    start_minute = _parse_wall_minutes(working_hours_start, "working_hours_start")
    end_minute = _parse_wall_minutes(working_hours_end, "working_hours_end")
    if start_minute >= end_minute:
        raise ToolError(
            code="INVALID_SLOT_PARAMS",
            message="working_hours_start must be earlier than working_hours_end.",
        )
    return slot_minutes, start_minute, end_minute


def _parse_wall_minutes(value: str, param: str) -> int:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", (value or "").strip())
    if not match:
        raise ToolError(
            code="INVALID_SLOT_PARAMS",
            message=f"{param} must be HH:MM (24-hour); got {value!r}.",
        )
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        raise ToolError(
            code="INVALID_SLOT_PARAMS",
            message=f"{param} must be a valid 24-hour time; got {value!r}.",
        )
    return hours * 60 + minutes


__all__ = [
    "EVENT_ID_MAX_LEN",
    "normalize_event_ids",
    "resolve_calendar_selector",
    "resolve_calendar_name",
    "validate_alarms",
    "validate_attendee_emails",
    "validate_event_id",
    "validate_rrule",
    "validate_slot_params",
]
