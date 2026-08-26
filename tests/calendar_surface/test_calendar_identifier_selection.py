"""Calendar identifiers remain the sole engine selector when display names collide."""

import asyncio
import json
from datetime import datetime, timedelta

from apple_mail_mcp.tools.calendar import create_event, list_calendars, list_events

from .conftest import HOST_TZ, FakeReadEngine, raw_event


def _list_events(**kwargs):
    return json.loads(asyncio.run(list_events(**kwargs)))


def _create_event(**kwargs):
    defaults = {
        "title": "Identifier target",
        "start": "2026-08-24T09:00:00",
        "duration_minutes": 30,
        "timezone": "America/New_York",
        "on_conflict": "allow",
    }
    defaults.update(kwargs)
    return json.loads(create_event(**defaults))


def _duplicate_name_engine() -> FakeReadEngine:
    calendars = [
        {"calendar_id": "CAL-PERSONAL", "id_kind": "calendar_object_reference", "name": "Shared", "writable": True},
        {"calendar_id": "CAL-TEAM", "id_kind": "calendar_object_reference", "name": "Shared", "writable": True},
    ]
    now = datetime.now(HOST_TZ) + timedelta(days=1)
    return FakeReadEngine(
        calendars=calendars,
        events=[
            raw_event("EVENT-PERSONAL", calendar="Shared", start=now) | {"calendar_id": "CAL-PERSONAL"},
            raw_event("EVENT-TEAM", calendar="Shared", start=now) | {"calendar_id": "CAL-TEAM"},
        ],
    )


class TestCalendarIdentifierSelection:
    def test_list_calendars_exposes_distinct_calendar_identifiers(self, fake_engines):
        read = _duplicate_name_engine()
        fake_engines(read=read)

        payload = json.loads(list_calendars())

        assert [calendar["name"] for calendar in payload["calendars"]] == ["Shared", "Shared"]
        assert [calendar["calendar_id"] for calendar in payload["calendars"]] == ["CAL-PERSONAL", "CAL-TEAM"]
        assert all(calendar["id_kind"] == "calendar_object_reference" for calendar in payload["calendars"])

    def test_duplicate_display_name_refuses_read_without_selecting_first_match(self, fake_engines):
        read = _duplicate_name_engine()
        fake_engines(read=read)

        payload = _list_events(calendar="Shared", days_ahead=2)

        assert payload["code"] == "AMBIGUOUS_CALENDAR_SELECTOR"
        assert read.fetch_calls == []
        assert {candidate["calendar_id"] for candidate in payload["remediation"]["candidates"]} == {
            "CAL-PERSONAL",
            "CAL-TEAM",
        }

    def test_duplicate_names_without_stable_ids_fail_closed(self, fake_engines):
        read = FakeReadEngine(
            calendars=[
                {"calendar_id": "Shared", "id_kind": "name", "name": "Shared", "writable": True},
                {"calendar_id": "Shared", "id_kind": "name", "name": "Shared", "writable": True},
            ]
        )
        fake_engines(read=read)

        payload = _list_events(calendar="Shared", days_ahead=2)

        assert payload["code"] == "CALENDAR_IDENTIFIER_UNAVAILABLE"
        assert read.fetch_calls == []

    def test_calendar_identifier_selects_one_duplicate_for_read(self, fake_engines):
        read = _duplicate_name_engine()
        fake_engines(read=read)

        payload = _list_events(calendar="CAL-TEAM", days_ahead=2)

        assert [event["event_id"] for event in payload["events"]] == ["EVENT-TEAM"]
        assert payload["calendars_scanned"] == ["Shared"]
        assert payload["calendar_ids_scanned"] == ["CAL-TEAM"]
        assert read.fetch_calls[0]["calendar_id"] == "CAL-TEAM"

    def test_calendar_identifier_selects_one_duplicate_for_write(self, fake_engines):
        read = _duplicate_name_engine()
        _read, write = fake_engines(read=read)

        payload = _create_event(calendar="CAL-TEAM")

        assert payload["created"] is True
        assert payload["calendar"] == "Shared"
        assert payload["calendar_id"] == "CAL-TEAM"
        assert write.created[0]["calendar_id"] == "CAL-TEAM"

    def test_eventkit_default_uses_opaque_id_when_display_names_collide(self, fake_engines, monkeypatch):
        read = _duplicate_name_engine()
        read.name = "eventkit"
        read.default = "Shared"
        read.default_id = "CAL-TEAM"
        _read, write = fake_engines(read=read)
        monkeypatch.setattr("apple_mail_mcp.server.DEFAULT_CALENDAR", None)

        payload = _create_event(calendar=None)

        assert payload["created"] is True
        assert payload["calendar"] == "Shared"
        assert payload["calendar_id"] == "CAL-TEAM"
        assert write.created[0]["calendar_id"] == "CAL-TEAM"
