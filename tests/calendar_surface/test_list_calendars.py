"""list_calendars tool: payloads, defaults, engine echo, error paths."""

import json

import apple_mail_mcp.server as server
import pytest
from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.tools.calendar import list_calendars

from .conftest import FakeReadEngine


@pytest.fixture(autouse=True)
def _eventkit_status_stub(monkeypatch):
    monkeypatch.setattr(
        "apple_mail_mcp.tools.calendar.eventkit_status",
        lambda: (False, "dependency_missing: pip install 'mcp-apple-mail[eventkit]'"),
    )


class TestListCalendars:
    def test_json_payload_shape(self, fake_engines):
        fake_engines()
        payload = json.loads(list_calendars())
        assert [c["name"] for c in payload["calendars"]] == ["Work", "Home", "MCP Test Calendar"]
        assert payload["engine"] == "applescript"
        assert payload["eventkit_available"]["available"] is False
        assert payload["calendars"][2]["writable"] is False

    def test_default_calendar_env_wins(self, fake_engines, monkeypatch):
        fake_engines()
        monkeypatch.setattr(server, "DEFAULT_CALENDAR", "Home")
        payload = json.loads(list_calendars())
        assert payload["default_calendar"] == "Home"
        assert [c for c in payload["calendars"] if c["is_default"]][0]["name"] == "Home"

    def test_default_calendar_name_resolves_under_stable_id_engine(self, fake_engines, monkeypatch):
        read = FakeReadEngine(name="eventkit", default="Work", default_id="UID-WORK")
        fake_engines(read=read)
        monkeypatch.setattr(server, "DEFAULT_CALENDAR", "Home")

        payload = json.loads(list_calendars())

        marked = [calendar for calendar in payload["calendars"] if calendar["is_default"]]
        assert [calendar["calendar_id"] for calendar in marked] == ["UID-HOME"]

    def test_default_calendar_stable_id_marks_exact_calendar(self, fake_engines, monkeypatch):
        read = FakeReadEngine(name="eventkit", default="Work", default_id="UID-WORK")
        fake_engines(read=read)
        monkeypatch.setattr(server, "DEFAULT_CALENDAR", "UID-HOME")

        payload = json.loads(list_calendars())

        marked = [calendar for calendar in payload["calendars"] if calendar["is_default"]]
        assert [calendar["calendar_id"] for calendar in marked] == ["UID-HOME"]

    def test_engine_default_used_without_env(self, fake_engines, monkeypatch):
        read = FakeReadEngine(default="Work")
        fake_engines(read=read)
        monkeypatch.setattr(server, "DEFAULT_CALENDAR", None)
        payload = json.loads(list_calendars())
        assert payload["default_calendar"] == "Work"

    def test_text_output(self, fake_engines):
        fake_engines()
        result = list_calendars(output_format="text")
        assert "Work" in result
        assert "read-only" in result

    def test_invalid_output_format(self, fake_engines):
        fake_engines()
        assert list_calendars(output_format="yaml").startswith("Error:")

    def test_engine_errors_surface(self, fake_engines):
        read = FakeReadEngine(list_errors=["calendar row has 3 fields, expected 5"])
        fake_engines(read=read)
        payload = json.loads(list_calendars())
        assert payload["calendar_errors"]

    def test_timeout_names_automation_pane(self, fake_engines, monkeypatch):
        fake_engines()

        def _raise():
            raise AppleScriptTimeout("x")

        monkeypatch.setattr("apple_mail_mcp.tools.calendar.get_engine", _raise)
        result = list_calendars()
        assert "timed out" in result
        assert "Automation" in result
