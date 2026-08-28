"""Row-protocol parsing for the AppleScript calendar engine.

The AppleScript builders emit one line per record, fields joined by ``|||``
after ``sanitize_pipe_delimited_field`` neutralized user text. This module is
the Python half of that belt-and-suspenders defense: it validates field
counts, shape-checks uids, and diverts corrupted rows into ``row_errors``
instead of letting a shifted field ever map onto the wrong event id.

Row shapes:

- ``CAL|||uid|||name|||writable|||description``
- ``EVT|||uid|||calendar_id|||calendar|||title|||start|||end|||allday|||status|||location|||url|||recurrence|||notes``
- ``ATT|||uid|||name|||email|||status`` (detail fetches only)
- ``ALM|||uid|||minutes`` (detail fetches only)
- ``ERROR_EVENT|||message`` / ``ERROR_CALENDAR|||name|||message``

Dates are numeric ``Y-M-D H:M:S`` local wall-clock components (never locale
``as string`` coercion) composed into aware datetimes here.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, tzinfo
from typing import Any

from apple_mail_mcp.calendar_core.validation import EVENT_ID_MAX_LEN
from apple_mail_mcp.constants import CALENDAR_BOUNDS

DELIM = "|||"
CAL_FIELDS = 5
EVT_FIELDS = 13
ATT_FIELDS = 5
ALM_FIELDS = 3

_NUMERIC_DATE = re.compile(r"^(\d{1,4})-(\d{1,2})-(\d{1,2}) (\d{1,2}):(\d{1,2}):(\d{1,2})$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_CALENDAR_REFERENCE_PREFIX = "calendar id "


def _uid_ok(uid: str) -> bool:
    return bool(uid) and len(uid) <= EVENT_ID_MAX_LEN and not _CONTROL_CHARS.search(uid)


def parse_calendar_reference_ids(raw: str) -> tuple[list[str], list[str]]:
    """Parse Calendar.app object references returned by ``get calendars``.

    Calendar.app exposes stable object specifiers as ``calendar id <opaque>``
    even on hosts where reading the documented ``calendarIdentifier`` property
    raises Apple event error -10000.  The returned identifiers are data, never
    interpolated into AppleScript until the normal escaping layer handles them.
    """
    text = raw.strip()
    if not text:
        return [], []
    identifiers: list[str] = []
    errors: list[str] = []
    for item in text.split(", "):
        if not item.startswith(_CALENDAR_REFERENCE_PREFIX):
            errors.append("calendar reference row did not use the expected object-id shape")
            continue
        identifier = item[len(_CALENDAR_REFERENCE_PREFIX) :].strip()
        if not _uid_ok(identifier):
            errors.append("calendar reference row carried an invalid object id")
            continue
        identifiers.append(identifier)
    return identifiers, errors


def parse_numeric_datetime(text: str, tz: tzinfo) -> datetime | None:
    """Parse a ``Y-M-D H:M:S`` numeric-component date in *tz*; None on failure."""
    match = _NUMERIC_DATE.match(text.strip())
    if not match:
        return None
    year, month, day, hours, minutes, seconds = (int(g) for g in match.groups())
    try:
        return datetime(year, month, day, hours, minutes, seconds, tzinfo=tz)
    except ValueError:
        return None


def parse_calendar_rows(raw: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse ``CAL`` rows into calendar dicts plus row-level errors."""
    calendars: list[dict[str, Any]] = []
    errors: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(DELIM)
        if parts[0] == "ERROR_CALENDAR":
            errors.append(DELIM.join(parts[1:]) or "unknown calendar error")
            continue
        if parts[0] != "CAL":
            continue
        if len(parts) != CAL_FIELDS:
            errors.append(f"calendar row has {len(parts)} fields, expected {CAL_FIELDS}")
            continue
        _, uid, name, writable, description = parts
        if not name:
            errors.append("calendar row missing name")
            continue
        calendar_id = uid if _uid_ok(uid) else name
        id_kind = "calendar_object_reference" if calendar_id == uid else "name"
        calendars.append(
            {
                "calendar_id": calendar_id,
                "id_kind": id_kind,
                "identifier_diagnostic": (
                    "stable_calendar_object_reference"
                    if id_kind == "calendar_object_reference"
                    else "exact_name_fallback"
                ),
                "name": name,
                "writable": writable.strip().lower() == "true",
                "description": description or None,
                "source_title": None,
                "source_identifier": None,
            }
        )
    return calendars, errors


def parse_event_rows(raw: str, tz: tzinfo) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse ``EVT`` (plus ``ATT``/``ALM`` detail) rows into raw event dicts.

    Raw event dicts carry aware ``start``/``end`` datetimes in host-local
    *tz*, unmodified text fields, ``attendees`` and ``alarms_minutes_before``
    lists when detail rows follow. Rows failing shape checks land in the
    returned errors list, never silently in the results.
    """
    events: list[dict[str, Any]] = []
    by_uid: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(DELIM)
        marker = parts[0]
        if marker == "ERROR_EVENT":
            errors.append(DELIM.join(parts[1:]) or "unknown event error")
            continue
        if marker == "EVT":
            if len(parts) != EVT_FIELDS:
                errors.append(f"event row has {len(parts)} fields, expected {EVT_FIELDS}")
                continue
            (
                _,
                uid,
                calendar_id,
                calendar_name,
                title,
                start_text,
                end_text,
                allday,
                status,
                location,
                url,
                recurrence,
                notes,
            ) = parts
            if not _uid_ok(uid):
                errors.append("event row has a malformed uid; row skipped")
                continue
            start = parse_numeric_datetime(start_text, tz)
            if start is None:
                errors.append(f"event {uid!r} has an unparseable start date; row skipped")
                continue
            end = parse_numeric_datetime(end_text, tz)
            record: dict[str, Any] = {
                "event_id": uid,
                "calendar_id": calendar_id or None,
                "calendar": calendar_name,
                "title": title,
                "start": start,
                "end": end,
                "all_day": allday.strip().lower() == "true",
                "status": status or None,
                "location": location or None,
                "url": url or None,
                "recurrence": recurrence or None,
                "notes": notes or None,
                "attendees": [],
                "alarms_minutes_before": [],
            }
            events.append(record)
            by_uid[uid] = record
            continue
        if marker == "ATT":
            if len(parts) != ATT_FIELDS:
                errors.append(f"attendee row has {len(parts)} fields, expected {ATT_FIELDS}")
                continue
            _, uid, name, email, att_status = parts
            parent = by_uid.get(uid)
            if parent is None:
                errors.append(f"attendee row references unknown event {uid!r}")
                continue
            attendees = parent["attendees"]
            assert isinstance(attendees, list)
            attendees.append({"name": name or None, "email": email or None, "participation_status": att_status or None})
            continue
        if marker == "ALM":
            if len(parts) != ALM_FIELDS:
                errors.append(f"alarm row has {len(parts)} fields, expected {ALM_FIELDS}")
                continue
            _, uid, minutes_text = parts
            parent = by_uid.get(uid)
            if parent is None:
                errors.append(f"alarm row references unknown event {uid!r}")
                continue
            try:
                minutes = abs(int(float(minutes_text)))
            except ValueError:
                errors.append(f"alarm row for {uid!r} has non-numeric minutes")
                continue
            alarms = parent["alarms_minutes_before"]
            assert isinstance(alarms, list)
            alarms.append(minutes)
    return events, errors


def event_payload(
    raw: dict[str, Any],
    *,
    tz: tzinfo,
    engine: str,
    include_detail: bool = False,
    occurrence_start: datetime | None = None,
    expansion: str | None = None,
) -> dict[str, Any]:
    """Build the JSON event shape from a raw engine record.

    *occurrence_start* substitutes an expanded recurring occurrence for the
    master's own start (shifting the end by the master duration).
    """
    start: datetime = raw["start"]
    end: datetime | None = raw.get("end")
    if occurrence_start is not None:
        if end is not None:
            end = occurrence_start + (end - start)
        start = occurrence_start

    notes = raw.get("notes")
    preview_chars = int(CALENDAR_BOUNDS["NOTES_PREVIEW_CHARS"])
    payload: dict[str, Any] = {
        "event_id": raw["event_id"],
        "calendar_id": raw.get("calendar_id"),
        "calendar": raw.get("calendar"),
        "title": raw.get("title") or "",
        "start": start.astimezone(tz).isoformat(),
        "start_utc": start.astimezone(timezone.utc).isoformat(),
        "end": end.astimezone(tz).isoformat() if end is not None else None,
        "end_utc": end.astimezone(timezone.utc).isoformat() if end is not None else None,
        "all_day": bool(raw.get("all_day")),
        "status": raw.get("status"),
        "location": raw.get("location"),
        "url": raw.get("url"),
        "recurring": bool(raw.get("recurrence")) or bool(raw.get("recurring_flag")),
        "recurrence_rule": raw.get("recurrence"),
        "engine": engine,
    }
    external_id = raw.get("external_id")
    if external_id:
        # EventKit-only secondary id (e.g. a Google ``...@google.com`` id); the
        # primary ``event_id`` is the AppleScript-resolvable calendarItemIdentifier.
        payload["external_id"] = external_id
    if expansion is not None:
        payload["expansion"] = expansion
        payload["occurrence_date"] = start.astimezone(tz).date().isoformat()
    if include_detail:
        payload["notes"] = notes
        payload["attendees"] = raw.get("attendees", [])
        payload["alarms_minutes_before"] = sorted(raw.get("alarms_minutes_before", []))
    else:
        payload["notes_preview"] = (notes or "")[:preview_chars] or None
    return payload


__all__ = [
    "ALM_FIELDS",
    "ATT_FIELDS",
    "CAL_FIELDS",
    "DELIM",
    "EVT_FIELDS",
    "event_payload",
    "parse_calendar_rows",
    "parse_event_rows",
    "parse_numeric_datetime",
]
