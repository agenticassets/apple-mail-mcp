"""delete_events tool: dry-run default, resolve-first, span, caps, gating."""

import json
from datetime import datetime, timedelta

import apple_mail_mcp.server as server
from apple_mail_mcp.tools.calendar import delete_events

from .conftest import HOST_TZ, FakeReadEngine, raw_event


def _engine(events=None):
    return FakeReadEngine(
        events=events
        if events is not None
        else [
            raw_event("UID-1", start=datetime.now(HOST_TZ) + timedelta(days=1)),
            raw_event("UID-2", start=datetime.now(HOST_TZ) + timedelta(days=2)),
        ]
    )


def _run(**kwargs):
    defaults = {"event_ids": ["UID-1", "UID-2"], "calendar": "Work"}
    defaults.update(kwargs)
    return json.loads(delete_events(**defaults))


class TestDryRunDefault:
    def test_default_previews_without_deleting(self, fake_engines):
        _read, write = fake_engines(read=_engine())
        payload = _run()
        assert payload["dry_run"] is True
        assert [t["event_id"] for t in payload["would_delete"]] == ["UID-1", "UID-2"]
        assert write.deleted == []

    def test_unresolved_id_aborts_whole_preview(self, fake_engines):
        _read, write = fake_engines(read=_engine())
        payload = _run(event_ids=["UID-1", "GHOST"])
        assert payload["code"] == "EVENT_NOT_FOUND"
        assert payload["remediation"]["missing"] == ["GHOST"]
        assert write.deleted == []

    def test_real_delete_reports_summaries(self, fake_engines):
        _read, write = fake_engines(read=_engine())
        payload = _run(dry_run=False)
        assert payload["deleted_count"] == 2
        assert write.deleted[0]["event_ids"] == ["UID-1", "UID-2"]

    def test_deletes_grouped_per_calendar(self, fake_engines):
        events = [
            raw_event("UID-1", start=datetime.now(HOST_TZ) + timedelta(days=1)),
            raw_event("UID-2", calendar="Home", start=datetime.now(HOST_TZ) + timedelta(days=1)),
        ]
        _read, write = fake_engines(read=_engine(events))
        payload = _run(calendar=None, dry_run=False)
        assert payload["deleted_count"] == 2
        assert {d["calendar_id"] for d in write.deleted} == {"UID-WORK", "UID-HOME"}


class TestSpanAndCaps:
    def test_recurring_requires_span(self, fake_engines):
        events = [raw_event("UID-R", recurrence="FREQ=WEEKLY", start=datetime.now(HOST_TZ) + timedelta(days=1))]
        fake_engines(read=_engine(events))
        payload = _run(event_ids=["UID-R"])
        assert payload["code"] == "RECURRING_SPAN_REQUIRED"

    def test_recurring_with_span_previews_with_note(self, fake_engines):
        events = [raw_event("UID-R", recurrence="FREQ=WEEKLY", start=datetime.now(HOST_TZ) + timedelta(days=1))]
        fake_engines(read=_engine(events))
        payload = _run(event_ids=["UID-R"], span="all_occurrences")
        assert payload["dry_run"] is True
        assert "whole series" in payload["recurring_note"]

    def test_ids_over_max_deletes_refused(self, fake_engines):
        fake_engines(read=_engine())
        payload = _run(event_ids=[f"UID-{i}" for i in range(25)], max_deletes=20)
        assert payload["code"] == "TOO_MANY_DELETES"

    def test_max_deletes_above_ceiling_refused(self, fake_engines):
        fake_engines()
        payload = _run(max_deletes=101)
        assert payload["code"] == "TOO_MANY_DELETES"

    def test_over_100_ids_refused(self, fake_engines):
        fake_engines()
        payload = _run(event_ids=[f"UID-{i}" for i in range(101)], max_deletes=100)
        assert payload["code"] == "TOO_MANY_DELETES"


class TestModeGates:
    def test_draft_safe_blocks_delete(self, fake_engines, monkeypatch):
        _read, write = fake_engines(read=_engine())
        monkeypatch.setattr(server, "DRAFT_SAFE", True)
        monkeypatch.setattr(server, "CALENDAR_ALLOW_DESTRUCTIVE", False)
        payload = _run(dry_run=False)
        assert payload["code"] == "CALENDAR_DELETE_BLOCKED"
        assert write.deleted == []

    def test_env_unlock_allows_delete_under_draft_safe(self, fake_engines, monkeypatch):
        _read, write = fake_engines(read=_engine())
        monkeypatch.setattr(server, "DRAFT_SAFE", True)
        monkeypatch.setattr(server, "CALENDAR_ALLOW_DESTRUCTIVE", True)
        payload = _run(dry_run=False)
        assert payload["deleted_count"] == 2

    def test_read_only_backstop_wins_over_unlock(self, fake_engines, monkeypatch):
        fake_engines(read=_engine())
        monkeypatch.setattr(server, "READ_ONLY", True)
        monkeypatch.setattr(server, "CALENDAR_ALLOW_DESTRUCTIVE", True)
        payload = _run(dry_run=False)
        assert payload["code"] == "CALENDAR_WRITE_BLOCKED"


class TestRecurringWriteWindow:
    """F5: recurring deletes widen the write-side lookup past the master start."""

    def test_recurring_delete_widens_write_window(self, fake_engines):
        events = [raw_event("UID-R", recurrence="FREQ=WEEKLY", start=datetime.now(HOST_TZ) + timedelta(days=1))]
        _read, write = fake_engines(read=_engine(events))
        _run(event_ids=["UID-R"], span="all_occurrences", dry_run=False)
        window = write.deleted[0]["window"]
        assert (window.end - window.start).days >= 400

    def test_non_recurring_delete_keeps_narrow_window(self, fake_engines):
        _read, write = fake_engines(read=_engine())
        _run(dry_run=False)
        window = write.deleted[0]["window"]
        assert (window.end - window.start).days <= 121


class _VanishingRecurringEngine(FakeReadEngine):
    """Serves a recurring master on the resolve pass, then reports it gone.

    Models a delete that actually removed the whole series: the verify re-query
    (the second id-scoped ``fetch_window``) finds no surviving occurrences.
    """

    def fetch_window(
        self,
        window,
        calendar_name,
        *,
        scan_cap,
        include_detail=False,
        event_ids=None,
        timeout=None,
        selector_kind="calendar_object_reference",
    ):
        rows, errs = super().fetch_window(
            window,
            calendar_name,
            scan_cap=scan_cap,
            include_detail=include_detail,
            event_ids=event_ids,
            timeout=timeout,
            selector_kind=selector_kind,
        )
        if event_ids:
            if getattr(self, "_served", False):
                return [], errs
            self._served = True
        return rows, errs


class TestRecurringVerify:
    """Bug 2: recurring delete is verified; false whole-series success is impossible."""

    def _recurring_events(self):
        return [raw_event("UID-R", recurrence="FREQ=WEEKLY", start=datetime.now(HOST_TZ) + timedelta(days=1))]

    def test_survivors_report_incomplete_not_success(self, fake_engines):
        # The default fake keeps serving the series, so the verify finds survivors.
        _read, write = fake_engines(read=_engine(self._recurring_events()))
        payload = _run(event_ids=["UID-R"], span="all_occurrences", dry_run=False)
        assert payload["code"] == "RECURRING_DELETE_INCOMPLETE"
        assert "UID-R" in payload["remediation"]["surviving_occurrences"]
        assert write.deleted  # the delete was still attempted

    def test_verified_removal_reports_whole_series(self, fake_engines):
        read = _VanishingRecurringEngine(events=self._recurring_events())
        _read, write = fake_engines(read=read)
        payload = _run(event_ids=["UID-R"], span="all_occurrences", dry_run=False)
        assert payload["recurring_deleted_whole_series"] is True
        assert payload["deleted_count"] >= 1


class TestTextOutput:
    def test_dry_run_text_is_not_json(self, fake_engines):
        _read, _write = fake_engines(read=_engine())
        result = delete_events(event_ids=["UID-1", "UID-2"], calendar="Work", output_format="text")
        assert not result.lstrip().startswith("{")
        assert "would delete" in result
        assert "UID-1" in result
