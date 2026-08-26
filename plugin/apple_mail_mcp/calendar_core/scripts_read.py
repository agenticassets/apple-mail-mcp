"""AppleScript builders for calendar reads.

These builders are the ONLY sanctioned emitters of ``every event of ... whose``
in this codebase (enforced by the calendar lint in
``tests/calendar/test_calendar_scripts.py``). Every event predicate carries
both ``start date >=`` and ``start date <=`` bounds, results are sliced to an
inner scan cap, dates cross the boundary only as validated integers, and every
user-controlled text field passes ``sanitize_pipe_delimited_field`` before the
``|||`` row join. Recurrence filtering happens in the repeat loop, never in the
``whose`` predicate (final plan, F1).
"""

from __future__ import annotations

from datetime import datetime

from apple_mail_mcp.core import escape_applescript, sanitize_pipe_delimited_field

_FMT_DATE_HANDLER = """
on ammFmtDate(d)
    if d is missing value then return ""
    return ((year of d) as string) & "-" & ((month of d as integer) as string) & "-" & ((day of d) as string) & " " & ((hours of d) as string) & ":" & ((minutes of d) as string) & ":" & ((seconds of d) as string)
end ammFmtDate
"""

_RETURN_ROWS = """
        set AppleScript's text item delimiters to linefeed
        set outputText to outputRows as string
        set AppleScript's text item delimiters to ""
        return outputText
"""


def _calendar_name_guard(calendar_id: str) -> str:
    """Require one live exact-name match without retaining a delete-blocking reference."""
    safe = escape_applescript(calendar_id)
    return f'''set matchingCalendars to every calendar whose name is "{safe}"
            if (count of matchingCalendars) is 0 then error "CALENDAR_NOT_FOUND"
            if (count of matchingCalendars) is greater than 1 then error "AMBIGUOUS_CALENDAR_SELECTOR"'''


def _calendar_target_line(calendar_id: str, selector_kind: str) -> str:
    """Return a safe Calendar.app object selector for a resolved calendar."""
    safe = escape_applescript(calendar_id)
    if selector_kind == "name":
        return _calendar_name_guard(calendar_id) + "\n            set targetCal to item 1 of matchingCalendars"
    return f'set targetCal to calendar id "{safe}"'


def _calendar_target_expression(calendar_id: str, selector_kind: str) -> str:
    """Return the inline selector required by Calendar.app calendar deletion."""
    safe = escape_applescript(calendar_id)
    if selector_kind == "name":
        return f'first calendar whose name is "{safe}"'
    return f'calendar id "{safe}"'


def applescript_date_block(var_name: str, dt: datetime, *, all_day: bool = False) -> str:
    """Emit integer-component AppleScript date construction for an aware datetime.

    For timed events the datetime is converted to the host's local wall clock
    (Calendar.app compares event dates in local time), then interpolated as
    validated integers only; no ``date "..."`` string coercion ever runs.

    For all-day events (``all_day=True``) the date is the whole meaning, so the
    requested-zone calendar date is carried through unchanged and NOT converted
    to host-local: an all-day request for a given date must land on that date
    regardless of the Mac's own zone. Converting a midnight-in-requested-zone
    instant to host-local would roll the day back or forward when the requested
    zone is far east or west of the host (F1).
    """
    base = dt if all_day else dt.astimezone()
    seconds_since_midnight = base.hour * 3600 + base.minute * 60 + base.second
    return (
        f"set {var_name} to current date\n"
        f"        set time of {var_name} to 0\n"
        f"        set day of {var_name} to 1\n"
        f"        set year of {var_name} to {base.year}\n"
        f"        set month of {var_name} to {base.month}\n"
        f"        set day of {var_name} to {base.day}\n"
        f"        set time of {var_name} to {seconds_since_midnight}"
    )


def build_uid_condition(event_ids: list[str]) -> str:
    """Build a parenthesized ``uid is "A" or uid is "B"`` predicate clause.

    Ids must already be shape-validated (``normalize_event_ids``); escaping is
    belt-and-suspenders on top of that validation.
    """
    return "(" + " or ".join(f'uid is "{escape_applescript(uid)}"' for uid in event_ids) + ")"


def _event_row_block(*, include_detail: bool) -> str:
    detail = ""
    if include_detail:
        detail = f"""
                    repeat with anAttendee in attendees of anEvent
                        try
                            set attName to display name of anAttendee
                            if attName is missing value then set attName to ""
                            set attEmail to email of anAttendee
                            if attEmail is missing value then set attEmail to ""
                            set attStatus to ""
                            try
                                set attStatus to (participation status of anAttendee) as string
                            end try
                            {sanitize_pipe_delimited_field("attName")}
                            {sanitize_pipe_delimited_field("attEmail")}
                            set end of outputRows to "ATT|||" & evUid & "|||" & attName & "|||" & attEmail & "|||" & attStatus
                        end try
                    end repeat
                    repeat with anAlarm in display alarms of anEvent
                        try
                            set end of outputRows to "ALM|||" & evUid & "|||" & ((trigger interval of anAlarm) as string)
                        end try
                    end repeat
                    repeat with anAlarm in sound alarms of anEvent
                        try
                            set end of outputRows to "ALM|||" & evUid & "|||" & ((trigger interval of anAlarm) as string)
                        end try
                    end repeat"""
    return f"""
                try
                    set evUid to uid of anEvent
                    set evTitle to summary of anEvent
                    if evTitle is missing value then set evTitle to ""
                    set evStart to my ammFmtDate(start date of anEvent)
                    set evEnd to my ammFmtDate(end date of anEvent)
                    set evAllday to (allday event of anEvent) as string
                    set evStatus to ""
                    try
                        set evStatus to (status of anEvent) as string
                    end try
                    set evLocation to location of anEvent
                    if evLocation is missing value then set evLocation to ""
                    set evUrl to url of anEvent
                    if evUrl is missing value then set evUrl to ""
                    set evRecurrence to recurrence of anEvent
                    if evRecurrence is missing value then set evRecurrence to ""
                    set evNotes to description of anEvent
                    if evNotes is missing value then set evNotes to ""
                    {sanitize_pipe_delimited_field("evTitle")}
                    {sanitize_pipe_delimited_field("evLocation")}
                    {sanitize_pipe_delimited_field("evUrl")}
                    {sanitize_pipe_delimited_field("evRecurrence")}
                    {sanitize_pipe_delimited_field("evNotes")}
                    set end of outputRows to "EVT|||" & evUid & "|||" & targetCalId & "|||" & targetCalName & "|||" & evTitle & "|||" & evStart & "|||" & evEnd & "|||" & evAllday & "|||" & evStatus & "|||" & evLocation & "|||" & evUrl & "|||" & evRecurrence & "|||" & evNotes{detail}
                on error errMsg
                    set end of outputRows to "ERROR_EVENT|||" & errMsg
                end try"""


def list_calendar_reference_ids_script(*, timeout_seconds: int) -> str:
    """Return Calendar.app's stable object references without reading properties."""
    return f"""tell application "Calendar"
    with timeout of {int(timeout_seconds)} seconds
        get calendars
    end timeout
end tell"""


def _calendar_row_block(*, calendar_id: str | None = None) -> str:
    target = "aCal"
    uid = ""
    if calendar_id is not None:
        safe = escape_applescript(calendar_id)
        target = "targetCal"
        uid = safe
        prefix = f'''        try
            set targetCal to calendar id "{safe}"
'''
        suffix = f"""        on error errMsg
            set end of outputRows to "ERROR_CALENDAR|||{safe}|||" & errMsg
        end try"""
    else:
        prefix = """        repeat with aCal in calendars
            try
"""
        suffix = """            on error errMsg
                set end of outputRows to "ERROR_CALENDAR|||unknown|||" & errMsg
            end try
        end repeat"""
    return f'''{prefix}                set calUid to "{uid}"
                set calName to name of {target}
                set calWritable to (writable of {target}) as string
                set calDesc to ""
                try
                    set calDesc to description of {target}
                    if calDesc is missing value then set calDesc to ""
                end try
                {sanitize_pipe_delimited_field("calName")}
                {sanitize_pipe_delimited_field("calDesc")}
                set end of outputRows to "CAL|||" & calUid & "|||" & calName & "|||" & calWritable & "|||" & calDesc
{suffix}'''


def list_calendars_script(*, timeout_seconds: int, calendar_ids: list[str] | None = None) -> str:
    """List calendars, binding stable object ids when reference discovery worked."""
    if calendar_ids:
        rows = "\n".join(_calendar_row_block(calendar_id=calendar_id) for calendar_id in calendar_ids)
    else:
        rows = _calendar_row_block()
    return f"""tell application "Calendar"
    with timeout of {int(timeout_seconds)} seconds
        set outputRows to {{}}
{rows}
{_RETURN_ROWS}
    end timeout
end tell
{_FMT_DATE_HANDLER}"""


def fetch_window_events_script(
    *,
    calendar_id: str,
    start_block: str,
    end_block: str,
    scan_cap: int,
    timeout_seconds: int,
    selector_kind: str = "calendar_object_reference",
    uid_condition: str = "",
    include_detail: bool = False,
) -> str:
    """Bounded window fetch: events whose start date falls inside the window.

    *calendar_id* is escaped here (raw user text is safe to pass);
    *start_block*/*end_block* come from ``applescript_date_block`` binding
    ``windowStart``/``windowEnd``. An optional *uid_condition* (from
    ``build_uid_condition``) narrows the same bounded predicate to exact ids.
    """
    safe_calendar_id = escape_applescript(calendar_id)
    target_line = _calendar_target_line(calendar_id, selector_kind)
    uid_clause = f" and {uid_condition}" if uid_condition else ""
    return f"""tell application "Calendar"
    with timeout of {int(timeout_seconds)} seconds
        set outputRows to {{}}
        try
            {target_line}
            set targetCalName to name of targetCal
            set targetCalId to "{safe_calendar_id}"
            {sanitize_pipe_delimited_field("targetCalName")}
            {start_block}
            {end_block}
            set matchingEvents to every event of targetCal whose start date >= windowStart and start date <= windowEnd{uid_clause}
            if (count of matchingEvents) > {int(scan_cap)} then
                set matchingEvents to items 1 thru {int(scan_cap)} of matchingEvents
                set end of outputRows to "ERROR_EVENT|||scan cap reached: only the first {int(scan_cap)} events in the window were read"
            end if
            repeat with anEvent in matchingEvents
{_event_row_block(include_detail=include_detail)}
            end repeat
        on error errMsg
            set end of outputRows to "ERROR_CALENDAR|||" & errMsg
        end try
{_RETURN_ROWS}
    end timeout
end tell
{_FMT_DATE_HANDLER}"""


def fetch_recurring_masters_script(
    *,
    calendar_id: str,
    start_block: str,
    end_block: str,
    scan_cap: int,
    timeout_seconds: int,
    selector_kind: str = "calendar_object_reference",
    include_detail: bool = False,
) -> str:
    """Bounded lookback fetch of recurring masters.

    The window predicate stays date-bounded on both ends; the recurrence
    filter runs inside the repeat loop, never in the ``whose`` predicate.
    """
    safe_calendar_id = escape_applescript(calendar_id)
    target_line = _calendar_target_line(calendar_id, selector_kind)
    return f"""tell application "Calendar"
    with timeout of {int(timeout_seconds)} seconds
        set outputRows to {{}}
        try
            {target_line}
            set targetCalName to name of targetCal
            set targetCalId to "{safe_calendar_id}"
            {sanitize_pipe_delimited_field("targetCalName")}
            {start_block}
            {end_block}
            set matchingEvents to every event of targetCal whose start date >= windowStart and start date <= windowEnd
            if (count of matchingEvents) > {int(scan_cap)} then
                set matchingEvents to items 1 thru {int(scan_cap)} of matchingEvents
                set end of outputRows to "ERROR_EVENT|||scan cap reached: only the first {int(scan_cap)} recurring candidates were read"
            end if
            repeat with anEvent in matchingEvents
                set evRule to recurrence of anEvent
                if evRule is not missing value and evRule is not "" then
{_event_row_block(include_detail=include_detail)}
                end if
            end repeat
        on error errMsg
            set end of outputRows to "ERROR_CALENDAR|||" & errMsg
        end try
{_RETURN_ROWS}
    end timeout
end tell
{_FMT_DATE_HANDLER}"""


def count_events_script(
    *, calendar_id: str, timeout_seconds: int, selector_kind: str = "calendar_object_reference"
) -> str:
    """Count events on one calendar (used by the calendar-delete preview)."""
    safe_calendar_id = escape_applescript(calendar_id)
    target_line = _calendar_target_line(calendar_id, selector_kind)
    return f"""tell application "Calendar"
    with timeout of {int(timeout_seconds)} seconds
        try
            {target_line}
            return "COUNT|||" & ((count of events of targetCal) as string)
        on error errMsg
            return "ERROR_CALENDAR|||{safe_calendar_id}|||" & errMsg
        end try
    end timeout
end tell"""


__all__ = [
    "applescript_date_block",
    "build_uid_condition",
    "count_events_script",
    "fetch_recurring_masters_script",
    "fetch_window_events_script",
    "list_calendars_script",
    "list_calendar_reference_ids_script",
]
