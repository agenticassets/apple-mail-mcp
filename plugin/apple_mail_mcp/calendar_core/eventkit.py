"""Optional EventKit read fast path (PyObjC), guarded and never prompting.

This module always imports cleanly without PyObjC installed (pure-Python tests run on
Ubuntu). The EventKit engine activates only when the ``EventKit`` and
``Foundation`` modules import AND ``authorizationStatusForEntityType_``
already reports full access, a synchronous, non-prompting read verified in
the phase-3 platform pass. No code path here ever calls
``requestFullAccessToEvents...``; the consent prompt belongs exclusively to
the human-invoked ``apple-mail calendar-grant`` CLI command.

Inside Claude Desktop and Codex Desktop the host app declares no Calendars
usage string, so EventKit is denied with no prompt (claude-code#63032,
codex#21228); the AppleScript engine is the guaranteed baseline there.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.calendar_core.window import CalendarWindow, require_issued_window

_STATUS_NAMES = {
    0: "not_determined",
    1: "restricted",
    2: "denied",
    4: "write_only",
}

_PARTICIPANT_STATUS = {
    0: "unknown",
    1: "pending",
    2: "accepted",
    3: "declined",
    4: "tentative",
    5: "delegated",
    6: "completed",
    7: "in-process",
}


def load_frameworks() -> tuple[Any, Any] | None:
    """Import (EventKit, Foundation) or return None when unavailable."""
    try:
        import EventKit  # pyobjc-framework-EventKit, optional extra
        import Foundation
    except Exception:
        return None
    return EventKit, Foundation


def eventkit_status() -> tuple[bool, str]:
    """Whether the EventKit read fast path is usable, with a reason label.

    Reads authorization status synchronously; never prompts. Reasons:
    ``full_access``, ``dependency_missing``, ``not_determined``, ``denied``,
    ``restricted``, ``write_only``, or ``status_check_failed: ...``.
    """
    frameworks = load_frameworks()
    if frameworks is None:
        return False, "dependency_missing: pip install 'mcp-apple-mail[eventkit]'"
    eventkit_mod, _ = frameworks
    try:
        entity = getattr(eventkit_mod, "EKEntityTypeEvent", 0)
        status = int(eventkit_mod.EKEventStore.authorizationStatusForEntityType_(entity))
    except Exception as exc:
        return False, f"status_check_failed: {exc}"
    full_access = int(getattr(eventkit_mod, "EKAuthorizationStatusFullAccess", 3))
    legacy_authorized = int(getattr(eventkit_mod, "EKAuthorizationStatusAuthorized", 3))
    if status in (full_access, legacy_authorized):
        return True, "full_access"
    return False, _STATUS_NAMES.get(status, f"status_{status}")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _participant_email(participant: Any) -> str | None:
    try:
        url = participant.URL()
    except Exception:
        return None
    if url is None:
        return None
    text = str(url.absoluteString())
    if text.lower().startswith("mailto:"):
        text = text[len("mailto:") :]
    return text or None


class EventKitCalendarEngine:
    """Read-only EventKit engine; writes stay on AppleScript in 3.10.0."""

    name = "eventkit"
    expands_occurrences = True

    def __init__(self, eventkit_mod: Any, foundation_mod: Any) -> None:
        self._ek = eventkit_mod
        self._foundation = foundation_mod
        self._store: Any = None

    @classmethod
    def create(cls) -> EventKitCalendarEngine:
        frameworks = load_frameworks()
        if frameworks is None:  # pragma: no cover - guarded by callers
            raise RuntimeError("EventKit frameworks are not importable")
        return cls(*frameworks)

    def _event_store(self) -> Any:
        if self._store is None:
            self._store = self._ek.EKEventStore.alloc().init()
        return self._store

    def _nsdate(self, dt: datetime) -> Any:
        return self._foundation.NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())

    def default_calendar_name(self) -> str | None:
        try:
            default = self._event_store().defaultCalendarForNewEvents()
        except Exception:
            return None
        if default is None:
            return None
        return _text(default.title())

    def default_calendar_id(self) -> str | None:
        """Return EventKit's stable default-calendar selector, when available."""
        try:
            default = self._event_store().defaultCalendarForNewEvents()
        except Exception:
            return None
        if default is None:
            return None
        return _text(default.calendarIdentifier())

    def list_calendars(self, *, timeout: int | None = None) -> tuple[list[dict[str, Any]], list[str]]:
        del timeout  # in-process call; the osascript timeout does not apply
        store = self._event_store()
        entity = getattr(self._ek, "EKEntityTypeEvent", 0)
        calendars: list[dict[str, Any]] = []
        errors: list[str] = []
        for cal in store.calendarsForEntityType_(entity) or []:
            try:
                name = _text(cal.title()) or ""
                identifier = _text(cal.calendarIdentifier())
                try:
                    source = cal.source()
                except Exception:
                    source = None
                source_title = None
                source_identifier = None
                if source is not None:
                    try:
                        source_title = _text(source.title())
                    except Exception:
                        source_title = None
                    try:
                        source_identifier = _text(source.sourceIdentifier())
                    except Exception:
                        source_identifier = None
                calendars.append(
                    {
                        "calendar_id": identifier or name,
                        "id_kind": "calendarIdentifier" if identifier else "name",
                        "identifier_diagnostic": (
                            "eventkit_calendar_identifier" if identifier else "exact_name_fallback"
                        ),
                        "name": name,
                        "writable": bool(cal.allowsContentModifications()),
                        "description": None,
                        "source_title": source_title,
                        "source_identifier": source_identifier,
                    }
                )
            except Exception as exc:
                errors.append(f"calendar read failed: {exc}")
        return calendars, errors

    def _map_event(self, event: Any, *, include_detail: bool) -> dict[str, Any]:
        start_ts = float(event.startDate().timeIntervalSince1970())
        end_obj = event.endDate()
        end_ts = float(end_obj.timeIntervalSince1970()) if end_obj is not None else None
        try:
            recurring = bool(event.hasRecurrenceRules())
        except Exception:
            recurring = False
        # AppleScript's ``uid`` (the namespace the write engine matches on for
        # update/delete) equals EventKit's ``calendarItemIdentifier`` on every
        # account type, verified live against Google-CalDAV, iCloud, and local
        # stores. ``calendarItemExternalIdentifier`` is a different value (a
        # ``...@google.com`` id on Google, a hex string on iCloud) and never
        # round-trips through the AppleScript writer, so it must not be the
        # ``event_id``; it is kept as a secondary field for cross-referencing a
        # synced source.
        event_id = None
        external_id = None
        try:
            external_id = _text(event.calendarItemExternalIdentifier())
        except Exception:
            external_id = None
        try:
            event_id = _text(event.calendarItemIdentifier())
        except Exception:
            event_id = None
        if not event_id:
            event_id = _text(event.eventIdentifier()) or ""
        record: dict[str, Any] = {
            "event_id": event_id,
            "external_id": external_id,
            "calendar": _text(event.calendar().title()) if event.calendar() is not None else None,
            "calendar_id": (_text(event.calendar().calendarIdentifier()) if event.calendar() is not None else None),
            "title": _text(event.title()) or "",
            "start": datetime.fromtimestamp(start_ts, tz=timezone.utc),
            "end": datetime.fromtimestamp(end_ts, tz=timezone.utc) if end_ts is not None else None,
            "all_day": bool(event.isAllDay()),
            "status": None,
            "location": _text(event.location()),
            "url": _text(event.URL().absoluteString()) if event.URL() is not None else None,
            "recurrence": None,
            "recurring_flag": recurring,
            "notes": _text(event.notes()),
            "attendees": [],
            "organizer": None,
            "alarms_minutes_before": [],
        }
        if include_detail:
            attendees = []
            for participant in event.attendees() or []:
                try:
                    status = int(participant.participantStatus())
                except Exception:
                    status = 0
                attendees.append(
                    {
                        "name": _text(participant.name()),
                        "email": _participant_email(participant),
                        "participation_status": _PARTICIPANT_STATUS.get(status, str(status)),
                    }
                )
            record["attendees"] = attendees
            try:
                organizer = event.organizer()
            except Exception:
                organizer = None
            if organizer is not None:
                record["organizer"] = {
                    "name": _text(organizer.name()),
                    "email": _participant_email(organizer),
                }
            alarms = []
            for alarm in event.alarms() or []:
                try:
                    offset = float(alarm.relativeOffset())
                except Exception:
                    continue
                alarms.append(abs(int(offset // 60)))
            record["alarms_minutes_before"] = alarms
        return record

    def fetch_window(
        self,
        window: CalendarWindow,
        calendar_id: str,
        *,
        scan_cap: int,
        include_detail: bool = False,
        event_ids: list[str] | None = None,
        timeout: int | None = None,
        selector_kind: str = "calendarIdentifier",
    ) -> tuple[list[dict[str, Any]], list[str]]:
        del timeout
        require_issued_window(window)
        store = self._event_store()
        entity = getattr(self._ek, "EKEntityTypeEvent", 0)
        target = None
        candidates = list(store.calendarsForEntityType_(entity) or [])
        if selector_kind == "name":
            matches = [cal for cal in candidates if _text(cal.title()) == calendar_id]
            if len(matches) > 1:
                raise ToolError(
                    code="AMBIGUOUS_CALENDAR_SELECTOR",
                    message="The exact calendar name no longer identifies one unique calendar.",
                    remediation={"preferred": "Call list_calendars again and use a stable calendar_id."},
                )
            target = matches[0] if matches else None
        else:
            for cal in candidates:
                if _text(cal.calendarIdentifier()) == calendar_id:
                    target = cal
                    break
        if target is None:
            raise ToolError(
                code="CALENDAR_NOT_FOUND",
                message="The resolved calendar no longer exists.",
                remediation={"preferred": "Call list_calendars again and retry with its current calendar_id."},
            )
        predicate = store.predicateForEventsWithStartDate_endDate_calendars_(
            self._nsdate(window.start), self._nsdate(window.end), [target]
        )
        events = list(store.eventsMatchingPredicate_(predicate) or [])
        errors: list[str] = []
        if len(events) > scan_cap:
            events = events[:scan_cap]
            errors.append(f"scan cap reached: only the first {scan_cap} events in the window were read")
        wanted = set(event_ids) if event_ids else None
        records: list[dict[str, Any]] = []
        for event in events:
            try:
                record = self._map_event(event, include_detail=include_detail)
            except Exception as exc:
                errors.append(f"event read failed: {exc}")
                continue
            if wanted is not None and record["event_id"] not in wanted:
                continue
            records.append(record)
        return records, errors

    def fetch_recurring_masters(
        self,
        window: CalendarWindow,
        calendar_id: str,
        *,
        include_detail: bool = False,
        timeout: int | None = None,
        selector_kind: str = "calendarIdentifier",
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """EventKit predicates expand occurrences natively; no master pass."""
        del window, calendar_id, include_detail, timeout, selector_kind
        return [], []


__all__ = [
    "EventKitCalendarEngine",
    "eventkit_status",
    "load_frameworks",
]
