"""create_event tool: times, alarms, rrules, conflicts, attendee gating."""

import json
from datetime import datetime, timedelta, timezone

import apple_mail_mcp.server as server
import pytest
from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.tools.calendar import create_event

from .conftest import HOST_TZ, FakeReadEngine, raw_event


def _run(**kwargs):
    defaults = {
        "title": "Focus block",
        "start": "2026-07-13T09:00:00",
        "duration_minutes": 60,
        "calendar": "Work",
        "timezone": "America/Chicago",
    }
    defaults.update(kwargs)
    return json.loads(create_event(**defaults))


class TestHappyPath:
    def test_creates_and_echoes(self, fake_engines):
        _read, write = fake_engines()
        payload = _run()
        assert payload["created"] is True
        assert payload["event_id"] == "NEW-UID-1"
        assert payload["calendar"] == "Work"
        assert payload["resolved_timezone"] == "America/Chicago"
        assert payload["start"].endswith("-05:00")
        assert payload["start_utc"].endswith("+00:00")
        created = write.created[0]
        assert created["title"] == "Focus block"
        assert (created["end"] - created["start"]) == timedelta(minutes=60)

    def test_end_instead_of_duration(self, fake_engines):
        _read, write = fake_engines()
        _run(end="2026-07-13T10:30:00", duration_minutes=None)
        assert (write.created[0]["end"] - write.created[0]["start"]) == timedelta(minutes=90)

    def test_all_day_defaults_one_day(self, fake_engines):
        _read, write = fake_engines()
        _run(all_day=True, duration_minutes=None)
        created = write.created[0]
        assert created["all_day"] is True
        assert (created["end"] - created["start"]) == timedelta(days=1)
        assert created["start"].hour == 0

    def test_all_day_echo_matches_host_local_midnight(self, fake_engines):
        # Bug 4: all-day events store on the host-local calendar date, so the echo
        # must be host-local midnight of 2026-07-15, not Tokyo midnight (a
        # different absolute instant, the old buggy echo).
        _read, _write = fake_engines()
        payload = _run(start="2026-07-15", all_day=True, duration_minutes=None, timezone="Asia/Tokyo")
        expected_start = datetime(2026, 7, 15, tzinfo=HOST_TZ)
        assert payload["start"] == expected_start.isoformat()
        assert payload["start_utc"] == expected_start.astimezone(timezone.utc).isoformat()
        assert not payload["start"].endswith("+09:00")

    def test_alarms_and_rrule_forwarded(self, fake_engines):
        _read, write = fake_engines()
        payload = _run(alarms_minutes_before=[10, 60], recurrence="freq=weekly;byday=mo")
        assert payload["recurrence_rule"] == "FREQ=WEEKLY;BYDAY=MO"
        assert write.created[0]["alarms_minutes_before"] == [10, 60]
        assert write.created[0]["recurrence"] == "FREQ=WEEKLY;BYDAY=MO"

    def test_default_calendar_env_target(self, fake_engines, monkeypatch):
        _read, write = fake_engines()
        monkeypatch.setattr(server, "DEFAULT_CALENDAR", "Home")
        payload = _run(calendar=None)
        assert payload["calendar"] == "Home"
        assert payload["calendar_id"] == "UID-HOME"
        assert write.created[0]["calendar_id"] == "UID-HOME"

    def test_no_target_and_no_default_refused(self, fake_engines, monkeypatch):
        read = FakeReadEngine(default=None)
        fake_engines(read=read)
        monkeypatch.setattr(server, "DEFAULT_CALENDAR", None)
        payload = _run(calendar=None)
        assert payload["code"] == "CALENDAR_NOT_FOUND"


class TestValidation:
    def test_end_and_duration_refused(self, fake_engines):
        fake_engines()
        payload = _run(end="2026-07-13T10:00:00", duration_minutes=30)
        assert payload["code"] == "INVALID_EVENT_WINDOW"

    def test_neither_end_nor_duration_refused(self, fake_engines):
        fake_engines()
        payload = _run(duration_minutes=None)
        assert payload["code"] == "INVALID_EVENT_WINDOW"

    def test_empty_title_refused(self, fake_engines):
        fake_engines()
        assert create_event(title="  ", start="2026-07-13", duration_minutes=30).startswith("Error:")

    def test_invalid_alarm_refused(self, fake_engines):
        fake_engines()
        payload = _run(alarms_minutes_before=[999_999])
        assert payload["code"] == "INVALID_ALARM"

    def test_invalid_rrule_refused(self, fake_engines):
        fake_engines()
        payload = _run(recurrence="FREQ=HOURLY")
        assert payload["code"] == "INVALID_RECURRENCE_RULE"

    def test_invalid_on_conflict_refused(self, fake_engines):
        fake_engines()
        payload = _run(on_conflict="maybe")
        assert payload["code"] == "EVENT_CONFLICT"

    def test_unknown_calendar_refused(self, fake_engines):
        fake_engines()
        payload = _run(calendar="Nonexistent")
        assert payload["code"] == "CALENDAR_NOT_FOUND"


class TestConflicts:
    def _overlapping_engine(self):
        return FakeReadEngine(
            events=[
                raw_event(
                    "UID-BUSY",
                    start=datetime(2026, 7, 13, 9, 30, tzinfo=HOST_TZ),
                    end=datetime(2026, 7, 13, 10, 30, tzinfo=HOST_TZ),
                )
            ]
        )

    def test_warn_creates_and_reports(self, fake_engines):
        _read, write = fake_engines(read=self._overlapping_engine())
        payload = _run(start="2026-07-13T09:00:00", timezone=None)
        assert payload["created"] is True
        assert payload["has_conflicts"] is True
        assert payload["conflicts"][0]["event_id"] == "UID-BUSY"
        assert write.created

    def test_block_refuses_without_writing(self, fake_engines):
        _read, write = fake_engines(read=self._overlapping_engine())
        payload = _run(start="2026-07-13T09:00:00", timezone=None, on_conflict="block")
        assert payload["code"] == "EVENT_CONFLICT"
        assert write.created == []

    def test_allow_skips_check(self, fake_engines):
        read = self._overlapping_engine()
        _read, write = fake_engines(read=read)
        payload = _run(start="2026-07-13T09:00:00", timezone=None, on_conflict="allow")
        assert payload["has_conflicts"] is False
        assert read.fetch_calls == []  # no conflict fetch ran
        assert write.created

    def test_conflict_scan_total_failure_blocks_create(self, fake_engines):
        class _FailingReadEngine(FakeReadEngine):
            def fetch_window(self, *args, **kwargs):
                raise ToolError(code="CALENDAR_NOT_FOUND", message="synthetic stale calendar")

        _read, write = fake_engines(read=_FailingReadEngine())

        payload = _run()

        assert payload["code"] == "CALENDAR_READ_FAILED"
        assert write.created == []

    def test_recurring_conflict_scan_failure_blocks_create(self, fake_engines):
        class _FailingRecurringEngine(FakeReadEngine):
            def fetch_recurring_masters(self, *args, **kwargs):
                raise ToolError(code="CALENDAR_READ_FAILED", message="synthetic recurring read failure")

        _read, write = fake_engines(read=_FailingRecurringEngine())

        payload = _run()

        assert payload["code"] == "CALENDAR_READ_FAILED"
        assert write.created == []


class TestAttendeeGating:
    def test_attendees_without_confirm_refused(self, fake_engines):
        _read, write = fake_engines()
        payload = _run(attendees=["ada@example.com"])
        assert payload["code"] == "INVITE_SEND_REQUIRES_CONFIRM"
        assert write.created == []

    def test_attendees_with_confirm_created_with_disclosure(self, fake_engines):
        _read, write = fake_engines()
        payload = _run(attendees=["Ada@Example.com"], send_invitations=True)
        assert payload["created"] is True
        assert payload["invitation_delivery"] == "platform_dependent"
        assert write.created[0]["attendees"] == ["ada@example.com"]

    def test_attendees_blocked_in_draft_safe(self, fake_engines, monkeypatch):
        _read, write = fake_engines()
        monkeypatch.setattr(server, "DRAFT_SAFE", True)
        payload = _run(attendees=["ada@example.com"], send_invitations=True)
        assert payload["code"] == "INVITE_SEND_BLOCKED"
        assert write.created == []

    def test_too_many_attendees_refused(self, fake_engines):
        fake_engines()
        payload = _run(attendees=[f"user{i}@example.com" for i in range(51)], send_invitations=True)
        assert payload["code"] == "TOO_MANY_ATTENDEES"

    def test_malformed_attendee_refused(self, fake_engines):
        fake_engines()
        payload = _run(attendees=["not-an-email"], send_invitations=True)
        assert payload["code"] == "INVALID_ATTENDEE_EMAIL"


class TestModeGates:
    def test_read_only_backstop(self, fake_engines, monkeypatch):
        _read, write = fake_engines()
        monkeypatch.setattr(server, "READ_ONLY", True)
        payload = _run()
        assert payload["code"] == "CALENDAR_WRITE_BLOCKED"
        assert write.created == []

    def test_draft_safe_allows_plain_create(self, fake_engines, monkeypatch):
        _read, write = fake_engines()
        monkeypatch.setattr(server, "DRAFT_SAFE", True)
        payload = _run()
        assert payload["created"] is True
        assert write.created
