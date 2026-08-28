"""Calendar engine seam: AppleScript guaranteed engine plus lazy selection.

Reads go through a ``CalendarReadEngine`` chosen by ``get_engine()``
(AppleScript everywhere; EventKit only when installed and already granted).
Writes always go through ``get_write_engine()`` which returns the
``AppleScriptCalendarEngine`` in 3.10.0: one write path means one injection
surface and one documented set of span limitations.

Every osascript call flows through ``run_applescript`` (imported here as a
patchable module attribute, mirroring the mail facades) and therefore shares
the process-wide single-flight lock and timeout discipline with the mail
tools.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Protocol

from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.calendar_core import eventkit as _eventkit
from apple_mail_mcp.calendar_core.records import parse_calendar_reference_ids, parse_calendar_rows, parse_event_rows
from apple_mail_mcp.calendar_core.scripts_read import (
    applescript_date_block,
    build_uid_condition,
    count_events_script,
    fetch_recurring_masters_script,
    fetch_window_events_script,
    list_calendar_reference_ids_script,
    list_calendars_script,
)
from apple_mail_mcp.calendar_core.scripts_write import (
    build_event_set_lines,
    create_calendar_script,
    create_event_script,
    delete_calendar_script,
    delete_events_script,
    rename_calendar_script,
    update_event_script,
)
from apple_mail_mcp.calendar_core.window import CalendarWindow, require_issued_window, shifted_window
from apple_mail_mcp.constants import CALENDAR_BOUNDS
from apple_mail_mcp.core import run_applescript  # patch seam: calendar_core.engine.run_applescript

_ENGINE_ENV = "APPLE_MAIL_CALENDAR_ENGINE"


class CalendarReadEngine(Protocol):
    """Read surface both engines implement."""

    name: str
    expands_occurrences: bool

    def default_calendar_name(self) -> str | None: ...

    def default_calendar_id(self) -> str | None: ...

    def list_calendars(self, *, timeout: int | None = None) -> tuple[list[dict[str, Any]], list[str]]: ...

    def fetch_window(
        self,
        window: CalendarWindow,
        calendar_id: str,
        *,
        scan_cap: int,
        include_detail: bool = False,
        event_ids: list[str] | None = None,
        timeout: int | None = None,
        selector_kind: str = "calendar_object_reference",
    ) -> tuple[list[dict[str, Any]], list[str]]: ...

    def fetch_recurring_masters(
        self,
        window: CalendarWindow,
        calendar_id: str,
        *,
        include_detail: bool = False,
        timeout: int | None = None,
        selector_kind: str = "calendar_object_reference",
    ) -> tuple[list[dict[str, Any]], list[str]]: ...


def _effective_timeout(timeout: int | None) -> int:
    return 120 if timeout is None else int(timeout)


def _host_tz() -> Any:
    return datetime.now().astimezone().tzinfo


def _is_access_denied(detail: str) -> bool:
    """True when a Calendar error signals macOS Automation denial (-1743)."""
    return "-1743" in detail or "not authorized" in detail.lower()


def _calendar_access_denied_error(detail: str) -> ToolError:
    return ToolError(
        code="CALENDAR_ACCESS_DENIED",
        message=f"macOS denied Apple Events automation of Calendar.app: {detail}",
        remediation={
            "pane": "System Settings > Privacy & Security > Automation",
            "note": "Enable Calendar under the host app that launches this server, then retry.",
        },
    )


def _calendar_selector_error(detail: str) -> ToolError | None:
    marker = detail.rsplit("|||", 1)[-1].strip()
    if marker == "AMBIGUOUS_CALENDAR_SELECTOR":
        return ToolError(
            code="AMBIGUOUS_CALENDAR_SELECTOR",
            message="The exact calendar name no longer identifies one unique calendar; nothing was changed.",
            remediation={"preferred": "Call list_calendars again and use a stable calendar_id."},
        )
    if marker == "CALENDAR_NOT_FOUND":
        return ToolError(
            code="CALENDAR_NOT_FOUND",
            message="The resolved calendar no longer exists; nothing was changed.",
            remediation={"preferred": "Call list_calendars again and retry with its current calendar_id."},
        )
    return None


def _map_write_error(result: str) -> str:
    """Map single-line script results to ToolErrors; returns the payload."""
    if result.startswith("ERROR_READONLY|||"):
        name = result.split("|||", 1)[1]
        raise ToolError(
            code="CALENDAR_READ_ONLY",
            message=f"Calendar {name!r} is not writable (subscribed or delegated calendars cannot be modified).",
            remediation={"preferred": "Target a writable calendar; list_calendars reports the writable flag."},
        )
    if result.startswith("ERROR_NOT_FOUND|||"):
        raise ToolError(
            code="EVENT_NOT_FOUND",
            message=(
                "No event matched that id inside the lookup window. Widen days_back/days_ahead "
                "or pass the calendar the event lives on."
            ),
        )
    if result.startswith("ERROR_CALENDAR_WRITE|||"):
        detail = result.split("|||", 1)[1]
        if _is_access_denied(detail):
            raise _calendar_access_denied_error(detail)
        selector_error = _calendar_selector_error(detail)
        if selector_error is not None:
            raise selector_error
        raise ToolError(
            code="CALENDAR_WRITE_FAILED",
            message=f"Calendar.app rejected the write: {detail}",
            remediation={
                "note": (
                    "The AppleScript write returned an error from Calendar.app. Retry once; if it "
                    "persists, make the change in Calendar.app directly."
                )
            },
        )
    return result


def _single_line_write(script: str, *, timeout: int, ok_prefix: str, label: str) -> str:
    """Run a one-line-result write script, map errors, and unwrap the OK prefix."""
    result = _map_write_error(run_applescript(script, timeout=timeout).strip())
    if result.startswith(f"{ok_prefix}|||"):
        return result.split("|||", 1)[1]
    raise Exception(f"Unexpected {label} result: {result!r}")


def _raise_calendar_read_failure(raw: str) -> None:
    """Turn a target-level Calendar.app failure into a structured engine error."""
    for line in raw.splitlines():
        if line.startswith("ERROR_CALENDAR|||"):
            detail = line.split("|||", 1)[1]
            if _is_access_denied(detail):
                raise _calendar_access_denied_error(detail)
            selector_error = _calendar_selector_error(detail)
            if selector_error is not None:
                raise selector_error
            raise ToolError(
                code="CALENDAR_READ_FAILED",
                message=f"Calendar.app could not read the resolved calendar: {detail}",
                remediation={
                    "preferred": "Call list_calendars again, then retry with its current calendar_id.",
                    "fallback": "If stable identifiers are unavailable, use an exact unique calendar name.",
                },
            )


class AppleScriptCalendarEngine:
    """Guaranteed engine: Calendar.app AppleScript via ``run_applescript``."""

    name = "applescript"
    expands_occurrences = False

    def default_calendar_name(self) -> str | None:
        return None

    def default_calendar_id(self) -> str | None:
        return None

    # ------------------------------------------------------------------ reads

    def list_calendars(self, *, timeout: int | None = None) -> tuple[list[dict[str, Any]], list[str]]:
        seconds = _effective_timeout(timeout)
        reference_raw = run_applescript(list_calendar_reference_ids_script(timeout_seconds=seconds), timeout=seconds)
        calendar_ids, identifier_errors = parse_calendar_reference_ids(reference_raw)
        raw = run_applescript(
            list_calendars_script(timeout_seconds=seconds, calendar_ids=calendar_ids or None), timeout=seconds
        )
        calendars, errors = parse_calendar_rows(raw)
        if identifier_errors or (not calendar_ids and calendars):
            errors.append(
                "Stable Calendar object references were unavailable; only exact unique-name fallback is safe."
            )
        errors.extend(identifier_errors)
        return calendars, errors

    def fetch_window(
        self,
        window: CalendarWindow,
        calendar_id: str,
        *,
        scan_cap: int,
        include_detail: bool = False,
        event_ids: list[str] | None = None,
        timeout: int | None = None,
        selector_kind: str = "calendar_object_reference",
    ) -> tuple[list[dict[str, Any]], list[str]]:
        require_issued_window(window)
        seconds = _effective_timeout(timeout)
        script = fetch_window_events_script(
            calendar_id=calendar_id,
            start_block=applescript_date_block("windowStart", window.start),
            end_block=applescript_date_block("windowEnd", window.end),
            scan_cap=scan_cap,
            timeout_seconds=seconds,
            uid_condition=build_uid_condition(event_ids) if event_ids else "",
            include_detail=include_detail,
            selector_kind=selector_kind,
        )
        raw = run_applescript(script, timeout=seconds)
        _raise_calendar_read_failure(raw)
        return parse_event_rows(raw, _host_tz())

    def fetch_recurring_masters(
        self,
        window: CalendarWindow,
        calendar_id: str,
        *,
        include_detail: bool = False,
        timeout: int | None = None,
        selector_kind: str = "calendar_object_reference",
    ) -> tuple[list[dict[str, Any]], list[str]]:
        require_issued_window(window)
        seconds = _effective_timeout(timeout)
        width_days = (window.end - window.start).total_seconds() / 86_400.0
        lookback = shifted_window(
            window,
            start_delta_days=-float(CALENDAR_BOUNDS["RECURRING_LOOKBACK_DAYS"]),
            end_delta_days=-width_days,
        )
        script = fetch_recurring_masters_script(
            calendar_id=calendar_id,
            start_block=applescript_date_block("windowStart", lookback.start),
            end_block=applescript_date_block("windowEnd", lookback.end),
            scan_cap=int(CALENDAR_BOUNDS["RECURRING_MASTER_SCAN_CAP"]),
            timeout_seconds=seconds,
            include_detail=include_detail,
            selector_kind=selector_kind,
        )
        raw = run_applescript(script, timeout=seconds)
        _raise_calendar_read_failure(raw)
        return parse_event_rows(raw, _host_tz())

    def count_events(
        self,
        calendar_id: str,
        *,
        timeout: int | None = None,
        selector_kind: str = "calendar_object_reference",
    ) -> int:
        seconds = _effective_timeout(timeout)
        raw = run_applescript(
            count_events_script(calendar_id=calendar_id, timeout_seconds=seconds, selector_kind=selector_kind),
            timeout=seconds,
        ).strip()
        if raw.startswith("COUNT|||"):
            try:
                return int(raw.split("|||", 1)[1])
            except ValueError:
                raise ToolError(
                    code="CALENDAR_READ_FAILED",
                    message="Calendar.app returned a malformed event count; the calendar was not treated as empty.",
                    remediation={"preferred": "Call list_calendars and retry the delete preview."},
                ) from None
        if raw.startswith("ERROR_CALENDAR|||"):
            _map_write_error(f"ERROR_CALENDAR_WRITE|||{raw.split('|||', 1)[1]}")
        _map_write_error(raw if raw.startswith("ERROR_") else f"ERROR_CALENDAR_WRITE|||{raw}")
        return 0  # pragma: no cover - _map_write_error always raises above

    # ----------------------------------------------------------------- writes

    def create_event(
        self,
        *,
        calendar_id: str,
        title: str,
        start: datetime,
        end: datetime,
        all_day: bool = False,
        location: str | None = None,
        notes: str | None = None,
        url: str | None = None,
        recurrence: str | None = None,
        alarms_minutes_before: list[int] | None = None,
        attendees: list[str] | None = None,
        timeout: int | None = None,
        selector_kind: str = "calendar_object_reference",
    ) -> str:
        seconds = _effective_timeout(timeout)
        set_lines = build_event_set_lines(
            target_var="newEvent",
            all_day=True if all_day else None,
            location=location,
            notes=notes,
            url=url,
            recurrence=recurrence,
            alarms_minutes_before=alarms_minutes_before,
            add_attendees=attendees,
        )
        script = create_event_script(
            calendar_id=calendar_id,
            title=title,
            start_block=applescript_date_block("windowStart", start, all_day=all_day),
            end_block=applescript_date_block("windowEnd", end, all_day=all_day),
            set_lines=set_lines,
            timeout_seconds=seconds,
            selector_kind=selector_kind,
        )
        return _single_line_write(script, timeout=seconds, ok_prefix="CREATED", label="create_event")

    def update_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        window: CalendarWindow,
        set_lines: str,
        timeout: int | None = None,
        selector_kind: str = "calendar_object_reference",
    ) -> str:
        require_issued_window(window)
        seconds = _effective_timeout(timeout)
        script = update_event_script(
            calendar_id=calendar_id,
            uid_condition=build_uid_condition([event_id]),
            start_block=applescript_date_block("windowStart", window.start),
            end_block=applescript_date_block("windowEnd", window.end),
            set_lines=set_lines,
            timeout_seconds=seconds,
            selector_kind=selector_kind,
        )
        return _single_line_write(script, timeout=seconds, ok_prefix="UPDATED", label="update_event")

    def delete_events(
        self,
        *,
        calendar_id: str,
        event_ids: list[str],
        window: CalendarWindow,
        timeout: int | None = None,
        selector_kind: str = "calendar_object_reference",
    ) -> tuple[list[dict[str, str]], list[str]]:
        """Delete exact ids; returns (deleted rows, errors)."""
        require_issued_window(window)
        seconds = _effective_timeout(timeout)
        deleted: list[dict[str, str]] = []
        errors: list[str] = []
        chunk_size = int(CALENDAR_BOUNDS["MAX_EVENT_IDS_PER_CALL"])
        for index in range(0, len(event_ids), chunk_size):
            chunk = event_ids[index : index + chunk_size]
            script = delete_events_script(
                calendar_id=calendar_id,
                uid_condition=build_uid_condition(chunk),
                start_block=applescript_date_block("windowStart", window.start),
                end_block=applescript_date_block("windowEnd", window.end),
                timeout_seconds=seconds,
                selector_kind=selector_kind,
            )
            raw = run_applescript(script, timeout=seconds)
            if raw.startswith("ERROR_READONLY|||"):
                _map_write_error(raw.strip())
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("DELETED|||"):
                    parts = line.split("|||")
                    if len(parts) >= 3:
                        deleted.append({"event_id": parts[1], "title": parts[2]})
                elif line.startswith("ERROR_EVENT|||"):
                    message = line.split("|||", 1)[1]
                    # Access-denied is not a soft per-event failure: an
                    # Automation-denied (or mid-call authorization) delete must
                    # surface the structured CALENDAR_ACCESS_DENIED remediation
                    # like create/update do, not report "deleted": [] success
                    # (F3). The delete script's outer on-error emits ERROR_EVENT,
                    # so re-route -1743/not-authorized through _map_write_error.
                    if _is_access_denied(message):
                        _map_write_error(f"ERROR_CALENDAR_WRITE|||{message}")
                    selector_error = _calendar_selector_error(message)
                    if selector_error is not None:
                        raise selector_error
                    errors.append(message)
        return deleted, errors

    def create_calendar(self, *, name: str, timeout: int | None = None) -> str:
        seconds = _effective_timeout(timeout)
        script = create_calendar_script(calendar_name=name, timeout_seconds=seconds)
        return _single_line_write(script, timeout=seconds, ok_prefix="CREATED_CAL", label="create_calendar")

    def rename_calendar(
        self,
        *,
        calendar_id: str,
        new_name: str,
        timeout: int | None = None,
        selector_kind: str = "calendar_object_reference",
    ) -> str:
        seconds = _effective_timeout(timeout)
        script = rename_calendar_script(
            calendar_id=calendar_id,
            new_name=new_name,
            timeout_seconds=seconds,
            selector_kind=selector_kind,
        )
        return _single_line_write(script, timeout=seconds, ok_prefix="RENAMED_CAL", label="rename_calendar")

    def delete_calendar(
        self,
        *,
        calendar_id: str,
        timeout: int | None = None,
        selector_kind: str = "calendar_object_reference",
    ) -> str:
        seconds = _effective_timeout(timeout)
        script = delete_calendar_script(calendar_id=calendar_id, timeout_seconds=seconds, selector_kind=selector_kind)
        return _single_line_write(script, timeout=seconds, ok_prefix="DELETED_CAL", label="delete_calendar")


def get_engine() -> CalendarReadEngine:
    """Select the read engine lazily per call.

    ``APPLE_MAIL_CALENDAR_ENGINE``: ``applescript`` | ``eventkit`` | ``auto``
    (default). Under ``auto`` the EventKit fast path activates only when the
    optional dependency imports and full access is already granted; every
    other state falls back silently to AppleScript. Forcing ``eventkit`` when
    it is unavailable raises ``CALENDAR_ACCESS_DENIED`` with the reason.
    """
    mode = os.environ.get(_ENGINE_ENV, "auto").strip().lower() or "auto"
    if mode == "applescript":
        return AppleScriptCalendarEngine()
    available, reason = _eventkit.eventkit_status()
    if mode == "eventkit":
        if not available:
            raise ToolError(
                code="CALENDAR_ACCESS_DENIED",
                message=f"APPLE_MAIL_CALENDAR_ENGINE=eventkit but the EventKit fast path is unavailable: {reason}",
                remediation={
                    "pane": "System Settings > Privacy & Security > Calendars",
                    "grant": "Run 'apple-mail calendar-grant' from a terminal to request full access once.",
                    "fallback": "Unset APPLE_MAIL_CALENDAR_ENGINE to use the AppleScript engine.",
                },
            )
        return _eventkit.EventKitCalendarEngine.create()
    if available:
        return _eventkit.EventKitCalendarEngine.create()
    return AppleScriptCalendarEngine()


def get_write_engine() -> AppleScriptCalendarEngine:
    """All calendar writes run on AppleScript in 3.10.0 (single write path)."""
    return AppleScriptCalendarEngine()


__all__ = [
    "AppleScriptCalendarEngine",
    "CalendarReadEngine",
    "get_engine",
    "get_write_engine",
    "run_applescript",
]
