"""records.py: row-protocol parsing, field-count defense, payload building."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apple_mail_mcp.calendar_core.records import (
    event_payload,
    parse_calendar_reference_ids,
    parse_calendar_rows,
    parse_event_rows,
    parse_numeric_datetime,
)

UTC = timezone.utc


def _evt_row(
    uid="UID-1",
    calendar_id="CAL-WORK",
    cal="Work",
    title="Standup",
    start="2026-7-10 9:0:0",
    end="2026-7-10 9:30:0",
):
    return f"EVT|||{uid}|||{calendar_id}|||{cal}|||{title}|||{start}|||{end}|||false|||confirmed|||Room 1|||https://x.example|||FREQ=DAILY|||some notes"


class TestParseNumericDatetime:
    def test_parses_components(self):
        parsed = parse_numeric_datetime("2026-7-10 14:5:9", UTC)
        assert parsed == datetime(2026, 7, 10, 14, 5, 9, tzinfo=UTC)

    def test_rejects_locale_strings(self):
        assert parse_numeric_datetime("Friday, July 10, 2026", UTC) is None

    def test_rejects_impossible_dates(self):
        assert parse_numeric_datetime("2026-2-30 9:0:0", UTC) is None


class TestParseCalendarRows:
    def test_parses_calendar_identifier_without_name_fallback(self):
        raw = "CAL|||CAL-A|||Work|||true|||desc\nCAL|||CAL-B|||Home|||false|||"
        calendars, errors = parse_calendar_rows(raw)
        assert errors == []
        assert calendars[0]["calendar_id"] == "CAL-A"
        assert calendars[0]["id_kind"] == "calendar_object_reference"
        assert calendars[0]["writable"] is True
        assert calendars[1]["calendar_id"] == "CAL-B"
        assert calendars[1]["id_kind"] == "calendar_object_reference"
        assert calendars[1]["writable"] is False

    def test_parses_stable_calendar_object_references(self):
        identifiers, errors = parse_calendar_reference_ids("calendar id CAL-A, calendar id CAL-B")

        assert identifiers == ["CAL-A", "CAL-B"]
        assert errors == []

    def test_missing_reference_uses_diagnostic_name_fallback(self):
        calendars, errors = parse_calendar_rows("CAL||||||Work|||true|||")

        assert errors == []
        assert calendars[0]["calendar_id"] == "Work"
        assert calendars[0]["id_kind"] == "name"
        assert calendars[0]["identifier_diagnostic"] == "exact_name_fallback"

    def test_error_rows_diverted(self):
        calendars, errors = parse_calendar_rows("ERROR_CALENDAR|||Work|||boom")
        assert calendars == []
        assert "boom" in errors[0]

    def test_wrong_field_count_diverted(self):
        calendars, errors = parse_calendar_rows("CAL|||only|||three")
        assert calendars == []
        assert "fields" in errors[0]


class TestParseEventRows:
    def test_happy_row(self):
        events, errors = parse_event_rows(_evt_row(), UTC)
        assert errors == []
        event = events[0]
        assert event["event_id"] == "UID-1"
        assert event["calendar_id"] == "CAL-WORK"
        assert event["calendar"] == "Work"
        assert event["start"] == datetime(2026, 7, 10, 9, 0, tzinfo=UTC)
        assert event["recurrence"] == "FREQ=DAILY"
        assert event["all_day"] is False

    def test_wrong_field_count_never_yields_event(self):
        # A shifted row (extra field) must be diverted, not mis-mapped.
        events, errors = parse_event_rows(_evt_row() + "|||extra", UTC)
        assert events == []
        assert "fields" in errors[0]

    def test_bad_uid_diverted(self):
        events, errors = parse_event_rows(_evt_row(uid="x" * 600), UTC)
        assert events == []
        assert "uid" in errors[0]

    def test_unparseable_start_diverted(self):
        events, errors = parse_event_rows(_evt_row(start="tomorrow"), UTC)
        assert events == []
        assert "start date" in errors[0]

    def test_error_event_rows_diverted(self):
        events, errors = parse_event_rows("ERROR_EVENT|||scan cap reached: only the first 300", UTC)
        assert events == []
        assert "scan cap" in errors[0]

    def test_attendee_and_alarm_rows_attach(self):
        raw = "\n".join(
            [
                _evt_row(),
                "ATT|||UID-1|||Ada|||ada@example.com|||accepted",
                "ALM|||UID-1|||-15",
                "ALM|||UID-1|||-60",
            ]
        )
        events, errors = parse_event_rows(raw, UTC)
        assert errors == []
        assert events[0]["attendees"][0]["email"] == "ada@example.com"
        assert sorted(events[0]["alarms_minutes_before"]) == [15, 60]

    def test_orphan_detail_rows_diverted(self):
        events, errors = parse_event_rows("ATT|||GHOST|||A|||a@example.com|||pending", UTC)
        assert events == []
        assert "unknown event" in errors[0]


class TestEventPayload:
    def _raw(self, **kwargs):
        base = {
            "event_id": "UID-1",
            "calendar": "Work",
            "title": "Standup",
            "start": datetime(2026, 7, 10, 9, 0, tzinfo=UTC),
            "end": datetime(2026, 7, 10, 9, 30, tzinfo=UTC),
            "all_day": False,
            "status": "confirmed",
            "location": None,
            "url": None,
            "recurrence": None,
            "notes": "n" * 500,
            "attendees": [],
            "alarms_minutes_before": [15],
        }
        base.update(kwargs)
        return base

    def test_dual_zone_output(self):
        payload = event_payload(self._raw(), tz=ZoneInfo("America/Chicago"), engine="applescript")
        assert payload["start"].endswith("-05:00")
        assert payload["start_utc"].endswith("+00:00")

    def test_notes_preview_truncated(self):
        payload = event_payload(self._raw(), tz=UTC, engine="applescript")
        assert len(payload["notes_preview"]) == 280
        assert "notes" not in payload

    def test_detail_includes_full_notes_and_alarms(self):
        payload = event_payload(self._raw(), tz=UTC, engine="applescript", include_detail=True)
        assert len(payload["notes"]) == 500
        assert payload["alarms_minutes_before"] == [15]

    def test_occurrence_shifts_end_by_duration(self):
        occurrence = datetime(2026, 7, 17, 9, 0, tzinfo=UTC)
        payload = event_payload(
            self._raw(recurrence="FREQ=WEEKLY"),
            tz=UTC,
            engine="applescript",
            occurrence_start=occurrence,
            expansion="python",
        )
        assert payload["start"].startswith("2026-07-17T09:00")
        assert payload["end"].startswith("2026-07-17T09:30")
        assert payload["expansion"] == "python"
        assert payload["occurrence_date"] == "2026-07-17"
        assert payload["recurring"] is True

    def test_recurring_flag_marks_recurring_without_rule(self):
        payload = event_payload(self._raw(recurring_flag=True), tz=UTC, engine="eventkit")
        assert payload["recurring"] is True
        assert payload["recurrence_rule"] is None
