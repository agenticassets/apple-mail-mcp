"""Thread-scope export completeness (AGENTIC-2799).

Two defects were reproduced live against a real Exchange account:

* ``mailbox="All"`` hard-errored out of the thread scope.
* A 9-member conversation exported 5 messages and printed ``THREAD
  EXPORT`` with no caveat, because the exporter only ever looked ids up
  in a hardcoded ``INBOX`` + Sent-names list while the thread's members
  lived elsewhere.

These tests mock ``get_email_thread`` and ``run_applescript`` (the
conventions in ``tests/CLAUDE.md`` and ``tests/analytics/test_export.py``)
rather than driving real Mail.app.
"""

import json
from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp.tools import analytics as analytics_tools
from apple_mail_mcp.tools.analytics.export_thread_scope import (
    derive_thread_candidate_mailboxes,
    exported_message_ids,
)

DESKTOP_PATH = str(Path("~/Desktop").expanduser())


class _ScriptCapture:
    """Capture every script passed to run_applescript; return a fixed value."""

    def __init__(self, return_value: str = ""):
        self.scripts: list[str] = []
        self._return_value = return_value

    def __call__(self, script: str, timeout: int | None = 120) -> str:
        self.scripts.append(script)
        return self._return_value

    @property
    def last_script(self) -> str:
        return self.scripts[-1] if self.scripts else ""


def _exported_lines(*message_ids: str) -> str:
    """Build the exporter's own success text for *message_ids*."""
    lines = [f"✓ Exported message_id {mid}: Synthetic subject {mid}" for mid in message_ids]
    lines.append("")
    lines.append(f"Exported: {len(message_ids)}")
    lines.append("Location: /export/thread_export")
    return "\n".join(lines)


def _thread_export(payload: dict[str, object], *, script_result: str, **kwargs):
    """Drive export_emails(scope="thread") with both seams mocked."""
    capture = _ScriptCapture(return_value=script_result)
    call = dict(
        account="Work",
        save_directory=DESKTOP_PATH,
        scope="thread",
        message_id="101",
    )
    call.update(kwargs)
    with (
        patch("apple_mail_mcp.tools.search.get_email_thread", return_value=json.dumps(payload)),
        patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=capture),
    ):
        result = analytics_tools.export_emails(**call)
    return result, capture


# ---------------------------------------------------------------------------
# candidate mailboxes are derived from the records, not hardcoded
# ---------------------------------------------------------------------------


def test_candidates_derived_from_record_mailboxes_plus_fallbacks():
    payload = {
        "items": [
            {"message_id": "101", "mailbox": "Archive"},
            {"message_id": "202", "mailbox": "Projects/Acme"},
            {"message_id": "303", "mailbox": "Archive"},
        ]
    }

    _result, capture = _thread_export(payload, script_result=_exported_lines("101", "202", "303"))

    script = capture.last_script
    assert 'mailbox "Archive" of targetAccount' in script
    assert 'mailbox "Projects/Acme" of targetAccount' in script
    # Historical fallbacks survive so nothing that worked before regresses.
    assert 'mailbox "INBOX" of targetAccount' in script
    assert 'mailbox "Sent Mail" of targetAccount' in script


def test_derived_candidates_preserve_first_seen_order_and_dedupe_case_insensitively():
    records = [
        {"mailbox": "Archive"},
        {"mailbox": "archive"},
        {"mailbox": "Team Mail"},
        {"mailbox": ""},
    ]

    candidates, truncated = derive_thread_candidate_mailboxes(records, include_sent=False)

    assert candidates == ["Archive", "Team Mail", "INBOX"]
    assert truncated is False


def test_all_mail_records_are_never_scanned():
    payload = {
        "items": [
            {"message_id": "101", "mailbox": "[Gmail]/All Mail"},
            {"message_id": "202", "mailbox": "ALL MAIL"},
            {"message_id": "303", "mailbox": "Archive"},
        ]
    }

    _result, capture = _thread_export(payload, script_result=_exported_lines("101", "202", "303"))

    script = capture.last_script
    assert "All Mail" not in script
    assert "ALL MAIL" not in script
    assert 'mailbox "Archive" of targetAccount' in script


def test_derived_sent_mailboxes_are_dropped_when_include_sent_false():
    records = [{"mailbox": "Sent Items"}, {"mailbox": "[Gmail]/Sent Mail"}, {"mailbox": "Archive"}]

    candidates, _truncated = derive_thread_candidate_mailboxes(records, include_sent=False)

    assert candidates == ["Archive", "INBOX"]


def test_candidate_list_is_capped_and_reports_truncation():
    records = [{"mailbox": f"Folder {index}"} for index in range(40)]

    candidates, truncated = derive_thread_candidate_mailboxes(records, include_sent=False, max_mailboxes=5)

    assert len(candidates) == 5
    assert truncated is True


def test_truncated_candidate_list_is_reported_as_partial():
    payload = {"items": [{"message_id": str(100 + index), "mailbox": f"Folder {index}"} for index in range(40)]}

    result, _capture = _thread_export(payload, script_result=_exported_lines("100"))

    assert "PARTIAL: mailbox search list was capped at 20" in result


# ---------------------------------------------------------------------------
# requested vs exported reconciliation
# ---------------------------------------------------------------------------


def test_exported_message_ids_ignores_ids_that_were_not_requested():
    text = _exported_lines("101", "999")

    assert exported_message_ids(text, ["101", "202"]) == ["101"]


def test_shortfall_is_reported_with_both_counts_and_searched_mailboxes():
    payload = {
        "items": [
            {"message_id": "101", "mailbox": "Archive"},
            {"message_id": "202", "mailbox": "Archive"},
            {"message_id": "303", "mailbox": "Archive"},
        ]
    }

    result, _capture = _thread_export(payload, script_result=_exported_lines("101"))

    assert result.startswith("THREAD EXPORT")
    assert "PARTIAL: exported 1 of 3 thread message(s)." in result
    assert "Searched mailboxes: Archive, INBOX, Sent Mail, Sent, Sent Messages, Sent Items." in result


def test_no_partial_line_when_every_member_exported_and_thread_is_clean():
    payload = {
        "items": [
            {"message_id": "101", "mailbox": "Archive"},
            {"message_id": "202", "mailbox": "Archive"},
        ],
        "matched": 2,
        "returned": 2,
        "render_incomplete": False,
        "candidate_scan_incomplete": False,
        "thread_incomplete": False,
    }

    result, _capture = _thread_export(payload, script_result=_exported_lines("101", "202"))

    assert result.startswith("THREAD EXPORT")
    assert "PARTIAL:" not in result


# ---------------------------------------------------------------------------
# get_email_thread's own caveats propagate
# ---------------------------------------------------------------------------


def test_thread_incomplete_warnings_are_surfaced():
    payload = {
        "items": [{"message_id": "101", "mailbox": "Archive"}],
        "matched": 1,
        "returned": 1,
        "thread_incomplete": True,
        "warnings": [
            "thread scan hit the per-mailbox ceiling; older members may be missing",
            "date floor reached before the thread root",
        ],
    }

    result, _capture = _thread_export(payload, script_result=_exported_lines("101"))

    assert "PARTIAL: thread scan hit the per-mailbox ceiling; older members may be missing" in result
    assert "PARTIAL: date floor reached before the thread root" in result


def test_thread_incomplete_without_warnings_still_qualifies_the_banner():
    payload = {
        "items": [{"message_id": "101", "mailbox": "Archive"}],
        "matched": 1,
        "returned": 1,
        "thread_incomplete": True,
    }

    result, _capture = _thread_export(payload, script_result=_exported_lines("101"))

    assert "PARTIAL: get_email_thread reported the thread scan may have missed members" in result


def test_render_shortfall_and_errors_are_surfaced():
    payload = {
        "items": [{"message_id": "101", "mailbox": "Archive"}],
        "matched": 4,
        "returned": 1,
        "candidate_scan_incomplete": True,
        "errors": ["candidate scan failed for one mailbox"],
    }

    result, _capture = _thread_export(payload, script_result=_exported_lines("101"))

    assert "PARTIAL: candidate scan failed for one mailbox" in result
    assert "PARTIAL: get_email_thread matched 4 message(s) but returned 1" in result
    assert "PARTIAL: get_email_thread reported an incomplete candidate scan" in result


# ---------------------------------------------------------------------------
# Defect A — mailbox="All" must not blow up the export
# ---------------------------------------------------------------------------


def test_mailbox_all_does_not_raise_and_is_never_opened_as_a_mailbox():
    payload = {"items": [{"message_id": "101", "mailbox": "Inbox"}]}

    result, capture = _thread_export(payload, script_result=_exported_lines("101"), mailbox="All")

    assert result.startswith("THREAD EXPORT")
    assert "Mailbox not found" not in result
    assert 'mailbox "All" of targetAccount' not in capture.last_script


def test_mailbox_all_thread_error_is_returned_verbatim():
    capture = _ScriptCapture(return_value="")
    with (
        patch("apple_mail_mcp.tools.search.get_email_thread", return_value="Error: Mailbox not found: All"),
        patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=capture),
    ):
        result = analytics_tools.export_emails(
            account="Work",
            save_directory=DESKTOP_PATH,
            scope="thread",
            message_id="101",
            mailbox="All",
        )

    assert result == "Error: Mailbox not found: All"
    assert capture.scripts == []
