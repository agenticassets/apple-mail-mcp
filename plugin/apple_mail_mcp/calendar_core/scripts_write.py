"""AppleScript builders for calendar writes (the only write engine in 3.10.0).

Every builder escapes user text internally via ``escape_applescript``,
interpolates dates only as validated integer components, checks the target
calendar's ``writable`` flag before mutating, and returns single-line
machine-readable results (``CREATED|||uid``, ``UPDATED|||uid``,
``DELETED|||uid|||title`` rows, ``ERROR_READONLY|||name``,
``ERROR_NOT_FOUND|||uid``, ``ERROR_CALENDAR_WRITE|||message``). Event lookups
for update/delete reuse the bounded ``start date`` window plus exact ``uid``
predicates; there is no fuzzy destructive targeting anywhere in this module.
"""

from __future__ import annotations

from datetime import datetime

from apple_mail_mcp.calendar_core.scripts_read import (
    _calendar_name_guard,
    _calendar_target_expression,
    _calendar_target_line,
    applescript_date_block,
)
from apple_mail_mcp.core import escape_applescript


def _quoted(value: str) -> str:
    return f'"{escape_applescript(value)}"'


def build_event_set_lines(
    *,
    target_var: str = "targetEvent",
    title: str | None = None,
    new_start: datetime | None = None,
    new_end: datetime | None = None,
    all_day: bool | None = None,
    location: str | None = None,
    notes: str | None = None,
    url: str | None = None,
    recurrence: str | None = None,
    clear_recurrence: bool = False,
    alarms_minutes_before: list[int] | None = None,
    clear_alarms: bool = False,
    add_attendees: list[str] | None = None,
) -> str:
    """Build PATCH-style ``set`` lines for an event bound to *target_var*.

    ``None`` means leave unchanged. Alarm lists replace the existing alarm
    set; attendees are additive (Calendar.app scripting exposes no attendee
    removal). Dates arrive as aware datetimes and are emitted as integer
    component blocks, never strings. When ``all_day`` is True the new
    start/end dates carry the requested-zone calendar date unchanged (no
    host-local conversion) so an all-day move lands on the requested day
    regardless of the Mac's zone (F1); pass ``all_day=True`` alongside a new
    start/end when moving an all-day event.
    """
    date_all_day = bool(all_day)
    lines: list[str] = []
    if title is not None:
        lines.append(f"set summary of {target_var} to {_quoted(title)}")
    if new_start is not None:
        lines.append(applescript_date_block("ammNewStart", new_start, all_day=date_all_day))
        lines.append(f"set start date of {target_var} to ammNewStart")
    if new_end is not None:
        lines.append(applescript_date_block("ammNewEnd", new_end, all_day=date_all_day))
        lines.append(f"set end date of {target_var} to ammNewEnd")
    if all_day is not None:
        lines.append(f"set allday event of {target_var} to {'true' if all_day else 'false'}")
    if location is not None:
        lines.append(f"set location of {target_var} to {_quoted(location)}")
    if notes is not None:
        lines.append(f"set description of {target_var} to {_quoted(notes)}")
    if url is not None:
        lines.append(f"set url of {target_var} to {_quoted(url)}")
    if clear_recurrence:
        lines.append(
            f'try\n            set recurrence of {target_var} to ""\n        on error\n            set recurrence of {target_var} to missing value\n        end try'
        )
    elif recurrence is not None:
        lines.append(f"set recurrence of {target_var} to {_quoted(recurrence)}")
    if clear_alarms or alarms_minutes_before is not None:
        lines.append(f"try\n            delete every display alarm of {target_var}\n        end try")
        lines.append(f"try\n            delete every sound alarm of {target_var}\n        end try")
    if alarms_minutes_before:
        for minutes in alarms_minutes_before:
            lines.append(
                f"make new display alarm at end of display alarms of {target_var} "
                f"with properties {{trigger interval:-{int(minutes)}}}"
            )
    if add_attendees:
        for address in add_attendees:
            lines.append(
                f"make new attendee at end of attendees of {target_var} with properties {{email:{_quoted(address)}}}"
            )
    return "\n        ".join(lines)


def create_event_script(
    *,
    calendar_id: str,
    title: str,
    start_block: str,
    end_block: str,
    set_lines: str = "",
    timeout_seconds: int = 120,
    selector_kind: str = "calendar_object_reference",
) -> str:
    """Create one event; *start_block*/*end_block* bind windowStart/windowEnd."""
    safe_calendar = escape_applescript(calendar_id)
    target_line = _calendar_target_line(calendar_id, selector_kind)
    return f"""tell application "Calendar"
    with timeout of {int(timeout_seconds)} seconds
        try
            {target_line}
            if writable of targetCal is false then return "ERROR_READONLY|||{safe_calendar}"
            {start_block}
            {end_block}
            set newEvent to make new event at end of events of targetCal with properties {{summary:{_quoted(title)}, start date:windowStart, end date:windowEnd}}
            set targetEvent to newEvent
            {set_lines}
            return "CREATED|||" & (uid of newEvent)
        on error errMsg
            return "ERROR_CALENDAR_WRITE|||" & errMsg
        end try
    end timeout
end tell"""


def update_event_script(
    *,
    calendar_id: str,
    uid_condition: str,
    start_block: str,
    end_block: str,
    set_lines: str = "",
    timeout_seconds: int = 120,
    selector_kind: str = "calendar_object_reference",
) -> str:
    """Locate one event by bounded uid predicate and apply PATCH set lines."""
    safe_calendar = escape_applescript(calendar_id)
    target_line = _calendar_target_line(calendar_id, selector_kind)
    return f"""tell application "Calendar"
    with timeout of {int(timeout_seconds)} seconds
        try
            {target_line}
            if writable of targetCal is false then return "ERROR_READONLY|||{safe_calendar}"
            {start_block}
            {end_block}
            set matchingEvents to every event of targetCal whose start date >= windowStart and start date <= windowEnd and {uid_condition}
            if (count of matchingEvents) is 0 then return "ERROR_NOT_FOUND|||no event matched inside the lookup window"
            set targetEvent to item 1 of matchingEvents
            {set_lines}
            return "UPDATED|||" & (uid of targetEvent)
        on error errMsg
            return "ERROR_CALENDAR_WRITE|||" & errMsg
        end try
    end timeout
end tell"""


def delete_events_script(
    *,
    calendar_id: str,
    uid_condition: str,
    start_block: str,
    end_block: str,
    timeout_seconds: int = 120,
    selector_kind: str = "calendar_object_reference",
) -> str:
    """Delete events matched by bounded uid predicate; emits DELETED rows."""
    safe_calendar = escape_applescript(calendar_id)
    target_line = _calendar_target_line(calendar_id, selector_kind)
    return f"""tell application "Calendar"
    with timeout of {int(timeout_seconds)} seconds
        set outputRows to {{}}
        try
            {target_line}
            if writable of targetCal is false then return "ERROR_READONLY|||{safe_calendar}"
            {start_block}
            {end_block}
            set matchingEvents to every event of targetCal whose start date >= windowStart and start date <= windowEnd and {uid_condition}
            repeat with anEvent in matchingEvents
                try
                    set evUid to uid of anEvent
                    set evTitle to summary of anEvent
                    if evTitle is missing value then set evTitle to ""
                    delete anEvent
                    set end of outputRows to "DELETED|||" & evUid & "|||" & evTitle
                on error errMsg
                    set end of outputRows to "ERROR_EVENT|||" & errMsg
                end try
            end repeat
        on error errMsg
            set end of outputRows to "ERROR_EVENT|||" & errMsg
        end try
        set AppleScript's text item delimiters to linefeed
        set outputText to outputRows as string
        set AppleScript's text item delimiters to ""
        return outputText
    end timeout
end tell"""


def create_calendar_script(*, calendar_name: str, timeout_seconds: int = 120) -> str:
    """Create a new calendar by exact name."""
    return f"""tell application "Calendar"
    with timeout of {int(timeout_seconds)} seconds
        try
            set newCal to make new calendar with properties {{name:{_quoted(calendar_name)}}}
            return "CREATED_CAL|||" & (name of newCal)
        on error errMsg
            return "ERROR_CALENDAR_WRITE|||" & errMsg
        end try
    end timeout
end tell"""


def rename_calendar_script(
    *,
    calendar_id: str,
    new_name: str,
    timeout_seconds: int = 120,
    selector_kind: str = "calendar_object_reference",
) -> str:
    """Rename a calendar addressed by its stable Calendar identifier."""
    safe_calendar = escape_applescript(calendar_id)
    target_line = _calendar_target_line(calendar_id, selector_kind)
    return f"""tell application "Calendar"
    with timeout of {int(timeout_seconds)} seconds
        try
            {target_line}
            if writable of targetCal is false then return "ERROR_READONLY|||{safe_calendar}"
            set name of targetCal to {_quoted(new_name)}
            return "RENAMED_CAL|||" & (name of targetCal)
        on error errMsg
            return "ERROR_CALENDAR_WRITE|||" & errMsg
        end try
    end timeout
end tell"""


def delete_calendar_script(
    *, calendar_id: str, timeout_seconds: int = 120, selector_kind: str = "calendar_object_reference"
) -> str:
    """Delete a calendar via the inline whose-specifier form (cascade deletes its events).

    The variable-bound form (``set targetCal to calendar "X"`` then ``delete
    targetCal``) fails live with ``Calendar got an error: AppleEvent handler
    failed`` on current Calendar.app; the inline ``delete (first calendar whose
    name is ...)`` specifier deletes cleanly (verified live, including on a
    non-empty calendar whose events cascade away with it).
    """
    safe_calendar = escape_applescript(calendar_id)
    if selector_kind == "name":
        target_line = _calendar_name_guard(calendar_id)
        target_expression = _calendar_target_expression(calendar_id, selector_kind)
    else:
        target_line = f'''set targetCal to calendar id "{safe_calendar}"
            set targetCalName to name of targetCal
            set matchingCalendars to every calendar whose name is targetCalName
            if (count of matchingCalendars) is 0 then error "CALENDAR_NOT_FOUND"
            if (count of matchingCalendars) is greater than 1 then error "AMBIGUOUS_CALENDAR_SELECTOR"'''
        target_expression = "first calendar whose name is targetCalName"
    return f"""tell application "Calendar"
    with timeout of {int(timeout_seconds)} seconds
        try
            {target_line}
            delete ({target_expression})
            return "DELETED_CAL|||{safe_calendar}"
        on error errMsg
            return "ERROR_CALENDAR_WRITE|||" & errMsg
        end try
    end timeout
end tell"""


__all__ = [
    "build_event_set_lines",
    "create_calendar_script",
    "create_event_script",
    "delete_calendar_script",
    "delete_events_script",
    "rename_calendar_script",
    "update_event_script",
]
