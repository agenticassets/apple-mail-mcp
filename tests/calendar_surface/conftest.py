"""Shared fakes for the calendar tool tests: engine-boundary mocking only."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

HOST_TZ = datetime.now().astimezone().tzinfo or timezone.utc


def raw_event(
    uid: str,
    *,
    calendar: str = "Work",
    calendar_id: str | None = None,
    title: str = "Event",
    start: datetime | None = None,
    end: datetime | None = None,
    all_day: bool = False,
    location: str | None = None,
    notes: str | None = None,
    url: str | None = None,
    recurrence: str | None = None,
    status: str | None = None,
    attendees: list[dict] | None = None,
    alarms: list[int] | None = None,
):
    start = start or datetime.now(HOST_TZ) + timedelta(days=1)
    calendar_id = calendar_id or {
        "Work": "UID-WORK",
        "Home": "UID-HOME",
        "MCP Test Calendar": "UID-TEST",
    }.get(calendar, calendar)
    return {
        "event_id": uid,
        "calendar_id": calendar_id,
        "calendar": calendar,
        "title": title,
        "start": start,
        "end": end if end is not None else start + timedelta(hours=1),
        "all_day": all_day,
        "status": status,
        "location": location,
        "url": url,
        "recurrence": recurrence,
        "notes": notes,
        "attendees": attendees or [],
        "alarms_minutes_before": alarms or [],
    }


class FakeReadEngine:
    """Engine-boundary fake honoring the CalendarReadEngine protocol."""

    def __init__(
        self,
        *,
        name: str = "applescript",
        expands_occurrences: bool = False,
        calendars: list[dict] | None = None,
        events: list[dict] | None = None,
        masters: list[dict] | None = None,
        default: str | None = None,
        default_id: str | None = None,
        list_errors: list[str] | None = None,
        row_errors: list[str] | None = None,
    ) -> None:
        self.name = name
        self.expands_occurrences = expands_occurrences
        self.calendars = (
            calendars
            if calendars is not None
            else [
                {
                    "calendar_id": "UID-WORK",
                    "id_kind": "calendar_object_reference",
                    "name": "Work",
                    "writable": True,
                    "description": None,
                },
                {
                    "calendar_id": "UID-HOME",
                    "id_kind": "calendar_object_reference",
                    "name": "Home",
                    "writable": True,
                    "description": None,
                },
                {
                    "calendar_id": "UID-TEST",
                    "id_kind": "calendar_object_reference",
                    "name": "MCP Test Calendar",
                    "writable": False,
                    "description": None,
                },
            ]
        )
        self.events = events or []
        self.masters = masters or []
        self.default = default
        self.default_id = default_id
        self.list_errors = list_errors or []
        self.row_errors = row_errors or []
        self.fetch_calls: list[dict] = []
        self.master_calls: list[dict] = []

    def default_calendar_name(self):
        return self.default

    def default_calendar_id(self):
        return self.default_id

    def list_calendars(self, *, timeout=None):
        return [dict(c) for c in self.calendars], list(self.list_errors)

    def _calendar_name(self, calendar_id: str) -> str:
        """Return the display name for an opaque identifier used by the engine seam."""
        for calendar in self.calendars:
            if calendar["calendar_id"] == calendar_id:
                return str(calendar["name"])
        return calendar_id

    def _calendar_id_for_name(self, calendar_name: str) -> str:
        for calendar in self.calendars:
            if calendar["name"] == calendar_name:
                return str(calendar["calendar_id"])
        return calendar_name

    def fetch_window(
        self,
        window,
        calendar_id,
        *,
        scan_cap,
        include_detail=False,
        event_ids=None,
        timeout=None,
        selector_kind="calendar_object_reference",
    ):
        calendar_name = self._calendar_name(calendar_id)
        self.fetch_calls.append(
            {
                "calendar": calendar_name,
                "calendar_id": calendar_id,
                "selector_kind": selector_kind,
                "scan_cap": scan_cap,
                "include_detail": include_detail,
                "event_ids": list(event_ids) if event_ids else None,
                "window": window,
                "timeout": timeout,
            }
        )
        rows = [
            dict(event)
            for event in self.events
            if event.get("calendar_id", self._calendar_id_for_name(str(event.get("calendar")))) == calendar_id
        ]
        if event_ids is not None:
            wanted = set(event_ids)
            rows = [e for e in rows if e["event_id"] in wanted]
        else:
            rows = [e for e in rows if window.start <= e["start"] <= window.end]
        return rows, list(self.row_errors)

    def fetch_recurring_masters(
        self,
        window,
        calendar_id,
        *,
        include_detail=False,
        timeout=None,
        selector_kind="calendar_object_reference",
    ):
        calendar_name = self._calendar_name(calendar_id)
        self.master_calls.append(
            {
                "calendar": calendar_name,
                "calendar_id": calendar_id,
                "selector_kind": selector_kind,
                "window": window,
                "include_detail": include_detail,
            }
        )
        return [
            dict(master)
            for master in self.masters
            if master.get("calendar_id", self._calendar_id_for_name(str(master.get("calendar")))) == calendar_id
        ], []


class FakeWriteEngine:
    """Records write calls and returns deterministic ids."""

    name = "applescript"

    def __init__(self, *, event_counts: dict[str, int] | None = None) -> None:
        self.created: list[dict] = []
        self.updated: list[dict] = []
        self.deleted: list[dict] = []
        self.calendar_ops: list[dict] = []
        self.event_counts = event_counts or {}

    def create_event(self, **kwargs):
        self.created.append(kwargs)
        return f"NEW-UID-{len(self.created)}"

    def update_event(self, **kwargs):
        self.updated.append(kwargs)
        return kwargs["event_id"]

    def delete_events(
        self,
        *,
        calendar_id,
        event_ids,
        window,
        timeout=None,
        selector_kind="calendar_object_reference",
    ):
        self.deleted.append({"calendar_id": calendar_id, "event_ids": list(event_ids), "window": window})
        return [{"event_id": eid, "title": f"title-{eid}"} for eid in event_ids], []

    def count_events(self, calendar_id, *, timeout=None, selector_kind="calendar_object_reference"):
        return self.event_counts.get(calendar_id, 0)

    def create_calendar(self, *, name, timeout=None):
        self.calendar_ops.append({"op": "create", "name": name})
        return name

    def rename_calendar(self, *, calendar_id, new_name, timeout=None, selector_kind="calendar_object_reference"):
        self.calendar_ops.append({"op": "rename", "calendar_id": calendar_id, "new_name": new_name})
        return new_name

    def delete_calendar(self, *, calendar_id, timeout=None, selector_kind="calendar_object_reference"):
        self.calendar_ops.append({"op": "delete", "calendar_id": calendar_id})
        return calendar_id


@pytest.fixture
def fake_engines(monkeypatch):
    """Patch both engine seams; returns (read_engine, write_engine).

    Also aligns ``list_calendar_names`` with the fake read engine's calendars
    so name resolution and engine data stay consistent inside one test.
    """
    read_engine = FakeReadEngine()
    write_engine = FakeWriteEngine()

    def install(read=None, write=None):
        nonlocal read_engine, write_engine
        read_engine = read or read_engine
        write_engine = write or write_engine
        monkeypatch.setattr("apple_mail_mcp.tools.calendar.get_engine", lambda: read_engine)
        monkeypatch.setattr("apple_mail_mcp.tools.calendar.get_write_engine", lambda: write_engine)
        monkeypatch.setattr(
            "apple_mail_mcp.tools.calendar.list_calendar_names",
            lambda timeout=None: [str(c["name"]) for c in read_engine.calendars],
        )
        return read_engine, write_engine

    return install


def parse_json(result: str) -> dict:
    return json.loads(result)
