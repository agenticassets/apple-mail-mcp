"""list_events tool: windows, paging, fan-out, query, expansion, budget."""

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.tools.calendar import list_events

from .conftest import HOST_TZ, FakeReadEngine, raw_event


def _run(**kwargs):
    return json.loads(asyncio.run(list_events(**kwargs)))


def _soon(hours=24):
    return datetime.now(HOST_TZ) + timedelta(hours=hours)


class TestHappyPath:
    def test_default_week_window(self, fake_engines):
        read = FakeReadEngine(
            events=[raw_event("UID-1", start=_soon()), raw_event("UID-2", calendar="Home", start=_soon(48))]
        )
        fake_engines(read=read)
        payload = _run()
        assert payload["total_matched"] == 2
        assert payload["events"][0]["event_id"] == "UID-1"
        assert payload["engine"] == "applescript"
        assert payload["calendars_scanned"] == ["Work", "Home", "MCP Test Calendar"]
        assert payload["budget_exhausted"] is False

    def test_calendar_scoping_limits_fetch(self, fake_engines):
        read = FakeReadEngine(events=[raw_event("UID-1", start=_soon())])
        fake_engines(read=read)
        payload = _run(calendar="Work")
        assert payload["calendars_scanned"] == ["Work"]
        assert payload["calendar_ids_scanned"] == ["UID-WORK"]
        assert [c["calendar_id"] for c in read.fetch_calls] == ["UID-WORK"]
        assert [c["calendar"] for c in read.fetch_calls] == ["Work"]

    def test_calendars_list_scoping(self, fake_engines):
        read = FakeReadEngine()
        fake_engines(read=read)
        payload = _run(calendars=["Work", "Home"])
        assert payload["calendars_scanned"] == ["Work", "Home"]

    def test_both_scopes_refused(self, fake_engines):
        fake_engines()
        payload = _run(calendar="Work", calendars=["Home"])
        assert payload["code"] == "AMBIGUOUS_CALENDAR_SELECTOR"

    def test_query_filters_in_python(self, fake_engines):
        read = FakeReadEngine(
            events=[
                raw_event("UID-1", title="Budget review", start=_soon()),
                raw_event("UID-2", title="Standup", start=_soon()),
            ]
        )
        fake_engines(read=read)
        payload = _run(query="budget")
        assert [e["event_id"] for e in payload["events"]] == ["UID-1"]

    def test_participant_query_finds_attendee_without_exposing_participants(self, fake_engines):
        read = FakeReadEngine(
            events=[
                raw_event(
                    "UID-1",
                    title="Program discussion",
                    start=_soon(),
                    attendees=[{"name": "Alex Example", "email": "alex@example.test"}],
                ),
                raw_event("UID-2", title="Program discussion", start=_soon(2)),
            ]
        )
        fake_engines(read=read)
        payload = _run(calendar="Work", participant_query="ALEX@EXAMPLE.TEST")
        assert [event["event_id"] for event in payload["events"]] == ["UID-1"]
        assert "attendees" not in payload["events"][0]
        assert read.fetch_calls[0]["include_detail"] is True

    def test_participant_query_matches_organizer_and_combines_with_text_query(self, fake_engines):
        event = raw_event("UID-1", title="Program discussion", start=_soon())
        event["organizer"] = {"name": "Morgan Organizer", "email": "morgan@example.test"}
        read = FakeReadEngine(events=[event])
        fake_engines(read=read)
        payload = _run(calendar="Work", query="program", participant_query="morgan")
        assert [event["event_id"] for event in payload["events"]] == ["UID-1"]
        assert _run(calendar="Work", query="different", participant_query="morgan")["events"] == []

    def test_participant_query_filters_recurring_master_with_private_detail_fetch(self, fake_engines):
        master = raw_event(
            "UID-R",
            start=datetime.now(HOST_TZ) - timedelta(days=30),
            recurrence="FREQ=DAILY",
            attendees=[{"name": "Alex Example", "email": "alex@example.test"}],
        )
        read = FakeReadEngine(masters=[master])
        fake_engines(read=read)
        payload = _run(calendar="Work", days_ahead=3, participant_query="alex")
        assert payload["events"]
        assert read.master_calls[0]["include_detail"] is True

    def test_include_all_day_false(self, fake_engines):
        read = FakeReadEngine(
            events=[raw_event("UID-1", all_day=True, start=_soon()), raw_event("UID-2", start=_soon())]
        )
        fake_engines(read=read)
        payload = _run(include_all_day=False)
        assert [e["event_id"] for e in payload["events"]] == ["UID-2"]

    def test_text_output_renders(self, fake_engines):
        read = FakeReadEngine(events=[raw_event("UID-1", start=_soon())])
        fake_engines(read=read)
        result = asyncio.run(list_events(output_format="text"))
        assert "UID-1" in result


class TestPagingAndCaps:
    def test_paging_truncation(self, fake_engines):
        events = [raw_event(f"UID-{i:03}", start=_soon(i + 1)) for i in range(10)]
        read = FakeReadEngine(events=events)
        fake_engines(read=read)
        payload = _run(limit=4, offset=4)
        assert [e["event_id"] for e in payload["events"]] == ["UID-004", "UID-005", "UID-006", "UID-007"]
        assert payload["truncated"] is True
        assert payload["next_offset"] == 8

    def test_limit_capped_at_return_cap(self, fake_engines):
        fake_engines()
        payload = _run(limit=10_000)
        assert payload["limit"] == 200

    def test_invalid_offset_and_limit(self, fake_engines):
        fake_engines()
        assert asyncio.run(list_events(offset=-1)).startswith("Error:")
        assert asyncio.run(list_events(limit=0)).startswith("Error:")

    def test_zero_window_refused(self, fake_engines):
        fake_engines()
        payload = _run(days_back=0, days_ahead=0)
        assert payload["code"] == "UNBOUNDED_CALENDAR_SCAN"

    def test_too_wide_window_refused(self, fake_engines):
        fake_engines()
        payload = _run(days_ahead=400)
        assert payload["code"] == "CALENDAR_WINDOW_TOO_WIDE"

    def test_invalid_timezone_refused(self, fake_engines):
        fake_engines()
        payload = _run(timezone="Mars/OlympusMons")
        assert payload["code"] == "INVALID_TIMEZONE"

    def test_fan_out_cap_flag(self, fake_engines):
        calendars = [
            {"calendar_id": f"UID-{i}", "id_kind": "uid", "name": f"Cal {i}", "writable": True, "description": None}
            for i in range(25)
        ]
        read = FakeReadEngine(calendars=calendars)
        fake_engines(read=read)
        payload = _run()
        assert payload["fan_out_capped"] is True
        assert len(payload["calendars_scanned"]) == 20


class TestRecurringExpansion:
    def test_master_expanded_into_occurrences(self, fake_engines):
        master = raw_event(
            "UID-R",
            start=datetime.now(HOST_TZ) - timedelta(days=30),
            recurrence="FREQ=DAILY",
        )
        read = FakeReadEngine(masters=[master])
        fake_engines(read=read)
        payload = _run(calendar="Work", days_ahead=3)
        expanded = [e for e in payload["events"] if e.get("expansion") == "python"]
        assert expanded
        assert all(e["event_id"] == "UID-R" for e in expanded)
        assert all(e["recurring"] for e in expanded)

    def test_unsupported_rule_flagged_not_dropped(self, fake_engines):
        master = raw_event(
            "UID-U",
            start=datetime.now(HOST_TZ) - timedelta(days=30),
            recurrence="FREQ=MONTHLY;BYDAY=2TU",  # outside the expansion grammar
        )
        read = FakeReadEngine(masters=[master])
        fake_engines(read=read)
        payload = _run(calendar="Work", days_ahead=3)
        flagged = [e for e in payload["events"] if e.get("expansion") == "unsupported_rrule"]
        assert [e["event_id"] for e in flagged] == ["UID-U"]

    def test_expand_recurring_false_skips_master_pass(self, fake_engines):
        read = FakeReadEngine(masters=[raw_event("UID-R", recurrence="FREQ=DAILY")])
        fake_engines(read=read)
        _run(calendar="Work", expand_recurring=False)
        assert read.master_calls == []

    def test_eventkit_engine_marks_native(self, fake_engines):
        event = raw_event("EK-1", start=_soon())
        event["recurring_flag"] = True
        event["recurrence"] = None
        read = FakeReadEngine(name="eventkit", expands_occurrences=True, events=[event])
        fake_engines(read=read)
        payload = _run(calendar="Work")
        assert payload["events"][0]["expansion"] == "native"
        assert read.master_calls == []

    def test_applescript_engine_discloses_recurring_horizon(self, fake_engines):
        # F2: the AppleScript recurring pass ran, so the response must disclose
        # the lookback horizon that can silently hide old standing series.
        read = FakeReadEngine(events=[raw_event("UID-1", start=_soon())])
        fake_engines(read=read)
        payload = _run(calendar="Work")
        assert payload["recurring_lookback_days"] == 400
        assert "recurring_coverage_note" in payload

    def test_no_disclosure_when_expand_recurring_false(self, fake_engines):
        read = FakeReadEngine(events=[raw_event("UID-1", start=_soon())])
        fake_engines(read=read)
        payload = _run(calendar="Work", expand_recurring=False)
        assert "recurring_lookback_days" not in payload

    def test_no_disclosure_on_eventkit_engine(self, fake_engines):
        read = FakeReadEngine(name="eventkit", expands_occurrences=True, events=[raw_event("UID-1", start=_soon())])
        fake_engines(read=read)
        payload = _run(calendar="Work")
        assert "recurring_lookback_days" not in payload

    def test_too_dense_expansion_refused(self, fake_engines, monkeypatch):
        monkeypatch.setitem(
            __import__("apple_mail_mcp.constants", fromlist=["CALENDAR_BOUNDS"]).CALENDAR_BOUNDS,
            "OCCURRENCE_SCAN_CEILING",
            2,
        )
        master = raw_event("UID-R", start=datetime.now(HOST_TZ) - timedelta(days=30), recurrence="FREQ=DAILY")
        read = FakeReadEngine(masters=[master])
        fake_engines(read=read)
        payload = _run(calendar="Work", days_ahead=7)
        assert payload["code"] == "CALENDAR_WINDOW_TOO_DENSE"


class TestPartialFailure:
    class _FailingReadEngine(FakeReadEngine):
        def fetch_window(self, window, calendar_id, **kwargs):
            if calendar_id == "UID-WORK":
                raise ToolError(code="CALENDAR_READ_FAILED", message="synthetic target failure")
            return super().fetch_window(window, calendar_id, **kwargs)

    def test_explicit_total_failure_is_structured(self, fake_engines):
        fake_engines(read=self._FailingReadEngine())

        payload = _run(calendar="Work")

        assert payload["code"] == "CALENDAR_READ_FAILED"
        assert "calendar_errors" in payload["remediation"]

    def test_explicit_access_denial_preserves_access_remediation(self, fake_engines):
        class _AccessDeniedEngine(FakeReadEngine):
            def fetch_window(self, window, calendar_id, **kwargs):
                raise ToolError(
                    code="CALENDAR_ACCESS_DENIED",
                    message="synthetic access denial",
                    remediation={"pane": "System Settings > Privacy & Security > Automation"},
                )

        fake_engines(read=_AccessDeniedEngine())

        payload = _run(calendar="Work")

        assert payload["code"] == "CALENDAR_ACCESS_DENIED"
        assert "Automation" in str(payload["remediation"])

    def test_unscoped_failure_keeps_partial_results(self, fake_engines):
        read = self._FailingReadEngine(events=[raw_event("UID-1", calendar="Home", start=_soon())])
        fake_engines(read=read)

        payload = _run()

        assert payload["events"]
        assert any("synthetic target failure" in error for error in payload["calendar_errors"])

    def test_per_calendar_errors_collected(self, fake_engines):
        read = FakeReadEngine(events=[raw_event("UID-1", start=_soon())], row_errors=["scan cap reached"])
        fake_engines(read=read)
        payload = _run(calendar="Work")
        assert any("scan cap" in err for err in payload["calendar_errors"])
        assert payload["events"]  # partial results still returned

    def test_budget_exhaustion_skips_remaining(self, fake_engines, monkeypatch):
        read = FakeReadEngine(events=[raw_event("UID-1", start=_soon())])
        fake_engines(read=read)

        class _InstantBudget:
            def __init__(self, seconds=None):
                pass

            def exhausted(self):
                return True

        monkeypatch.setattr("apple_mail_mcp.tools.calendar.helpers.CallBudget", _InstantBudget)
        payload = _run()
        assert payload["budget_exhausted"] is True
        assert len(payload["calendar_errors"]) == 3  # every calendar skipped
        assert payload["events"] == []
