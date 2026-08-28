"""engine.py: selection matrix, AppleScript engine parsing, write error mapping."""

from datetime import datetime, timezone

import apple_mail_mcp.calendar_core.engine as engine_mod
import pytest
from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.calendar_core.engine import AppleScriptCalendarEngine, get_engine, get_write_engine
from apple_mail_mcp.calendar_core.window import bounded_calendar_window

UTC = timezone.utc


def _window():
    return bounded_calendar_window(start="2026-07-10", end="2026-07-17", timezone_name="UTC")


class _Capture:
    def __init__(self, response=""):
        self.scripts: list[str] = []
        self.timeouts: list[int | None] = []
        self.response = response

    def __call__(self, script, timeout=120):
        self.scripts.append(script)
        self.timeouts.append(timeout)
        if isinstance(self.response, list):
            return self.response.pop(0)
        return self.response


class TestEngineSelection:
    def test_default_auto_without_eventkit_is_applescript(self, monkeypatch):
        monkeypatch.delenv("APPLE_MAIL_CALENDAR_ENGINE", raising=False)
        monkeypatch.setattr(
            "apple_mail_mcp.calendar_core.eventkit.eventkit_status",
            lambda: (False, "dependency_missing: pip install"),
        )
        assert get_engine().name == "applescript"

    def test_forced_applescript(self, monkeypatch):
        monkeypatch.setenv("APPLE_MAIL_CALENDAR_ENGINE", "applescript")
        assert get_engine().name == "applescript"

    def test_auto_with_full_access_selects_eventkit(self, monkeypatch):
        monkeypatch.delenv("APPLE_MAIL_CALENDAR_ENGINE", raising=False)
        monkeypatch.setattr("apple_mail_mcp.calendar_core.eventkit.eventkit_status", lambda: (True, "full_access"))
        sentinel = object()
        monkeypatch.setattr(
            "apple_mail_mcp.calendar_core.eventkit.EventKitCalendarEngine.create",
            classmethod(lambda cls: sentinel),
        )
        assert get_engine() is sentinel

    @pytest.mark.parametrize("reason", ["not_determined", "denied", "write_only"])
    def test_auto_falls_back_silently(self, monkeypatch, reason):
        monkeypatch.delenv("APPLE_MAIL_CALENDAR_ENGINE", raising=False)
        monkeypatch.setattr("apple_mail_mcp.calendar_core.eventkit.eventkit_status", lambda: (False, reason))
        assert get_engine().name == "applescript"

    def test_forced_eventkit_unavailable_raises_access_denied(self, monkeypatch):
        monkeypatch.setenv("APPLE_MAIL_CALENDAR_ENGINE", "eventkit")
        monkeypatch.setattr("apple_mail_mcp.calendar_core.eventkit.eventkit_status", lambda: (False, "denied"))
        with pytest.raises(ToolError) as exc:
            get_engine()
        assert exc.value.code == "CALENDAR_ACCESS_DENIED"
        assert "calendar-grant" in str(exc.value.remediation)

    def test_write_engine_is_always_applescript(self, monkeypatch):
        monkeypatch.setenv("APPLE_MAIL_CALENDAR_ENGINE", "eventkit")
        assert isinstance(get_write_engine(), AppleScriptCalendarEngine)


class TestAppleScriptEngineReads:
    def test_list_calendars_parses_rows(self, monkeypatch):
        capture = _Capture(
            [
                "calendar id UID-1",
                "CAL|||UID-1|||Work|||true|||\nERROR_CALENDAR|||x|||boom",
            ]
        )
        monkeypatch.setattr(engine_mod, "run_applescript", capture)
        calendars, errors = AppleScriptCalendarEngine().list_calendars(timeout=30)
        assert calendars[0]["name"] == "Work"
        assert "boom" in errors[0]
        assert capture.timeouts == [30, 30]

    def test_fetch_window_builds_bounded_script(self, monkeypatch):
        capture = _Capture("")
        monkeypatch.setattr(engine_mod, "run_applescript", capture)
        AppleScriptCalendarEngine().fetch_window(_window(), "Work", scan_cap=300, timeout=25)
        script = capture.scripts[0]
        assert "start date >= windowStart and start date <= windowEnd" in script
        assert "with timeout of 25 seconds" in script

    def test_fetch_window_refuses_forged_token(self, monkeypatch):
        from apple_mail_mcp.calendar_core.window import CalendarWindow

        monkeypatch.setattr(engine_mod, "run_applescript", _Capture(""))
        forged = CalendarWindow(
            start=datetime(2026, 7, 10, tzinfo=UTC), end=datetime(2026, 7, 17, tzinfo=UTC), timezone_name="UTC"
        )
        with pytest.raises(ToolError):
            AppleScriptCalendarEngine().fetch_window(forged, "Work", scan_cap=300)

    def test_fetch_recurring_masters_uses_lookback(self, monkeypatch):
        capture = _Capture("")
        monkeypatch.setattr(engine_mod, "run_applescript", capture)
        AppleScriptCalendarEngine().fetch_recurring_masters(_window(), "Work", timeout=20)
        assert "evRule is not missing value" in capture.scripts[0]

    def test_count_events(self, monkeypatch):
        monkeypatch.setattr(engine_mod, "run_applescript", _Capture("COUNT|||42"))
        assert AppleScriptCalendarEngine().count_events("Work") == 42

    def test_malformed_count_fails_closed_instead_of_reporting_empty(self, monkeypatch):
        monkeypatch.setattr(engine_mod, "run_applescript", _Capture("COUNT|||not-a-number"))

        with pytest.raises(ToolError) as exc:
            AppleScriptCalendarEngine().count_events("Work")

        assert exc.value.code == "CALENDAR_READ_FAILED"

    def test_read_access_denied_maps_to_access_denied(self, monkeypatch):
        monkeypatch.setattr(
            engine_mod,
            "run_applescript",
            _Capture("ERROR_CALENDAR|||error -1743 not authorized to send Apple events"),
        )

        with pytest.raises(ToolError) as exc:
            AppleScriptCalendarEngine().fetch_window(_window(), "CAL-WORK", scan_cap=300)

        assert exc.value.code == "CALENDAR_ACCESS_DENIED"
        assert "Automation" in str(exc.value.remediation)

    @pytest.mark.parametrize("marker", ["CALENDAR_NOT_FOUND", "AMBIGUOUS_CALENDAR_SELECTOR"])
    def test_read_name_guard_markers_keep_structured_codes(self, monkeypatch, marker):
        monkeypatch.setattr(engine_mod, "run_applescript", _Capture(f"ERROR_CALENDAR|||{marker}"))

        with pytest.raises(ToolError) as exc:
            AppleScriptCalendarEngine().fetch_window(_window(), "Work", scan_cap=300, selector_kind="name")

        assert exc.value.code == marker


class TestWriteErrorMapping:
    def test_readonly_maps_to_calendar_read_only(self, monkeypatch):
        monkeypatch.setattr(engine_mod, "run_applescript", _Capture("ERROR_READONLY|||Subscribed"))
        with pytest.raises(ToolError) as exc:
            AppleScriptCalendarEngine().create_event(
                calendar_id="CAL-SUBSCRIBED",
                title="T",
                start=datetime(2026, 7, 10, 9, tzinfo=UTC),
                end=datetime(2026, 7, 10, 10, tzinfo=UTC),
            )
        assert exc.value.code == "CALENDAR_READ_ONLY"

    def test_not_found_maps_to_event_not_found(self, monkeypatch):
        monkeypatch.setattr(engine_mod, "run_applescript", _Capture("ERROR_NOT_FOUND|||nothing matched"))
        with pytest.raises(ToolError) as exc:
            AppleScriptCalendarEngine().update_event(
                calendar_id="CAL-WORK", event_id="UID-1", window=_window(), set_lines=""
            )
        assert exc.value.code == "EVENT_NOT_FOUND"

    def test_minus_1743_maps_to_access_denied(self, monkeypatch):
        monkeypatch.setattr(engine_mod, "run_applescript", _Capture("ERROR_CALENDAR_WRITE|||error -1743 not permitted"))
        with pytest.raises(ToolError) as exc:
            AppleScriptCalendarEngine().create_calendar(name="X")
        assert exc.value.code == "CALENDAR_ACCESS_DENIED"
        assert "Automation" in str(exc.value.remediation)

    def test_generic_write_error_maps_to_structured_error(self, monkeypatch):
        monkeypatch.setattr(engine_mod, "run_applescript", _Capture("ERROR_CALENDAR_WRITE|||boom"))
        with pytest.raises(ToolError) as exc:
            AppleScriptCalendarEngine().rename_calendar(calendar_id="CAL-A", new_name="B")
        assert exc.value.code == "CALENDAR_WRITE_FAILED"
        assert "boom" in exc.value.message

    @pytest.mark.parametrize("marker", ["CALENDAR_NOT_FOUND", "AMBIGUOUS_CALENDAR_SELECTOR"])
    def test_write_name_guard_markers_keep_structured_codes(self, monkeypatch, marker):
        monkeypatch.setattr(engine_mod, "run_applescript", _Capture(f"ERROR_CALENDAR_WRITE|||{marker}"))

        with pytest.raises(ToolError) as exc:
            AppleScriptCalendarEngine().rename_calendar(calendar_id="Work", new_name="Renamed", selector_kind="name")

        assert exc.value.code == marker

    def test_count_name_guard_marker_is_not_zero_events(self, monkeypatch):
        monkeypatch.setattr(
            engine_mod,
            "run_applescript",
            _Capture("ERROR_CALENDAR|||Work|||AMBIGUOUS_CALENDAR_SELECTOR"),
        )

        with pytest.raises(ToolError) as exc:
            AppleScriptCalendarEngine().count_events("Work", selector_kind="name")

        assert exc.value.code == "AMBIGUOUS_CALENDAR_SELECTOR"

    def test_create_event_returns_uid(self, monkeypatch):
        monkeypatch.setattr(engine_mod, "run_applescript", _Capture("CREATED|||NEW-UID"))
        uid = AppleScriptCalendarEngine().create_event(
            calendar_id="CAL-WORK",
            title="T",
            start=datetime(2026, 7, 10, 9, tzinfo=UTC),
            end=datetime(2026, 7, 10, 10, tzinfo=UTC),
        )
        assert uid == "NEW-UID"

    def test_delete_events_chunks_over_25_ids(self, monkeypatch):
        capture = _Capture("DELETED|||UID-0|||t")
        monkeypatch.setattr(engine_mod, "run_applescript", capture)
        ids = [f"UID-{i}" for i in range(30)]
        deleted, errors = AppleScriptCalendarEngine().delete_events(
            calendar_id="CAL-WORK", event_ids=ids, window=_window()
        )
        assert len(capture.scripts) == 2  # 25 + 5
        assert errors == []
        assert deleted  # rows parsed from each chunk

    def test_delete_access_denied_raises_not_soft_error(self, monkeypatch):
        # F3: a -1743 / not-authorized delete must surface CALENDAR_ACCESS_DENIED,
        # not land in a soft errors list with a "successful" empty delete.
        monkeypatch.setattr(engine_mod, "run_applescript", _Capture("ERROR_EVENT|||error -1743 not authorized"))
        with pytest.raises(ToolError) as exc:
            AppleScriptCalendarEngine().delete_events(calendar_id="CAL-WORK", event_ids=["UID-1"], window=_window())
        assert exc.value.code == "CALENDAR_ACCESS_DENIED"
        assert "Automation" in str(exc.value.remediation)

    def test_delete_soft_event_error_stays_soft(self, monkeypatch):
        # A non-authorization per-event error is still collected, not raised.
        monkeypatch.setattr(engine_mod, "run_applescript", _Capture("ERROR_EVENT|||event vanished"))
        deleted, errors = AppleScriptCalendarEngine().delete_events(
            calendar_id="CAL-WORK", event_ids=["UID-1"], window=_window()
        )
        assert deleted == []
        assert errors == ["event vanished"]

    def test_delete_name_guard_marker_is_not_a_soft_error(self, monkeypatch):
        monkeypatch.setattr(
            engine_mod,
            "run_applescript",
            _Capture("ERROR_EVENT|||AMBIGUOUS_CALENDAR_SELECTOR"),
        )

        with pytest.raises(ToolError) as exc:
            AppleScriptCalendarEngine().delete_events(
                calendar_id="Work",
                event_ids=["UID-1"],
                window=_window(),
                selector_kind="name",
            )

        assert exc.value.code == "AMBIGUOUS_CALENDAR_SELECTOR"
