"""AGENTIC-2794: ``list_email_attachments`` searches beyond the inbox and says so.

Before this change the tool hard-coded the account's inbox and reported only
the rows it found. Two consequences were reproduced against a live account:

1. A message id living in ``Sent Items`` returned ``{"items": [], "returned": 0}``
   — a confident empty answer for a message that exists and has an attachment.
2. When several ids were passed and only some resolved, the caller got rows for
   the resolved subset with nothing marking the rest as never found, so a
   subset read as a complete attachment inventory.

These tests mock ``run_applescript`` and capture the generated AppleScript
(mirroring ``tests/analytics/test_export.py``) rather than driving real Mail.app.
"""

import json
from unittest.mock import patch

from apple_mail_mcp.tools import analytics as analytics_tools

SEEN = "SEEN_MESSAGE|||"
MAILBOX_ERROR = "ERROR_MAILBOX|||"


class _ScriptCapture:
    """Capture every script passed to run_applescript; return a routed value."""

    def __init__(self, return_value: str = "", router=None):
        self.scripts: list[str] = []
        self._return_value = return_value
        self._router = router

    def __call__(self, script: str, timeout: int | None = 120) -> str:
        self.scripts.append(script)
        if self._router is not None:
            return self._router(script)
        return self._return_value

    @property
    def last_script(self) -> str:
        return self.scripts[-1] if self.scripts else ""


def _seen_row(mailbox: str, message_id: str, subject: str = "Quarterly report") -> str:
    return f"{SEEN}{mailbox}|||{message_id}|||{subject}|||sender@example.com|||2026-06-29"


def _attachment_row(
    mailbox: str,
    message_id: str,
    index: int = 1,
    filename: str = "report.pdf",
    size: str = "2048",
    subject: str = "Quarterly report",
) -> str:
    return "|||".join([message_id, subject, "sender@example.com", "2026-06-29", str(index), filename, size, mailbox])


def _list(**kwargs):
    """Drive list_email_attachments with a mocked run_applescript."""
    capture = _ScriptCapture(
        return_value=kwargs.pop("_return_value", ""),
        router=kwargs.pop("_router", None),
    )
    defaults = dict(account="Work", message_ids=["777"])
    defaults.update(kwargs)
    with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=capture):
        result = analytics_tools.list_email_attachments(**defaults)
    return result, capture


# ---------------------------------------------------------------------------
# Regression guard: the INBOX default is unchanged
# ---------------------------------------------------------------------------


def test_default_mailbox_still_uses_localized_inbox_lookup():
    result, capture = _list(
        output_format="json",
        _return_value="\n".join([_seen_row("INBOX", "777"), _attachment_row("INBOX", "777")]),
    )

    payload = json.loads(result)
    assert payload["mailboxes_searched"] == ["INBOX"]
    assert payload["returned"] == 1
    assert payload["items"][0]["filename"] == "report.pdf"
    assert payload["items"][0]["size_bytes"] == 2048
    assert payload["items"][0]["mailbox"] == "INBOX"
    assert payload["complete"] is True
    assert "errors" not in payload
    assert "no localized inbox match" in capture.last_script
    assert "id is 777" in capture.last_script


def test_seven_field_rows_without_seen_markers_still_parse():
    """The pre-3.x row shape (no ``mailbox`` field, no SEEN rows) still resolves."""
    result, _capture = _list(
        output_format="json",
        _return_value="777|||Invoice|||sender@example.com|||2026-06-29|||1|||report.pdf|||2048",
    )

    payload = json.loads(result)
    assert payload["returned"] == 1
    assert payload["resolved_message_ids"] == ["777"]
    assert payload["unresolved_message_ids"] == []
    assert payload["complete"] is True


# ---------------------------------------------------------------------------
# Multi-mailbox search
# ---------------------------------------------------------------------------


def test_mailboxes_list_searches_every_named_mailbox():
    result, capture = _list(
        message_ids=["777", "888"],
        mailboxes=["INBOX", "Sent Items"],
        output_format="json",
        _return_value="\n".join(
            [
                _seen_row("INBOX", "777"),
                _attachment_row("INBOX", "777"),
                _seen_row("Sent Items", "888", subject="Re: Quarterly report"),
                _attachment_row("Sent Items", "888", filename="signed.pdf", size="4096"),
            ]
        ),
    )

    payload = json.loads(result)
    assert payload["mailboxes_searched"] == ["INBOX", "Sent Items"]
    assert len(capture.scripts) == 1
    assert 'mailbox "Sent Items"' in capture.last_script
    assert "no localized inbox match" in capture.last_script
    by_id = {item["message_id"]: item for item in payload["items"]}
    assert by_id["777"]["mailbox"] == "INBOX"
    assert by_id["888"]["mailbox"] == "Sent Items"
    assert by_id["888"]["filename"] == "signed.pdf"
    assert payload["complete"] is True


def test_id_resolving_only_in_second_mailbox_is_found():
    """The defect: the search must not stop at the first mailbox."""
    result, _capture = _list(
        message_ids=["888"],
        mailboxes=["INBOX", "Sent Items"],
        output_format="json",
        _return_value="\n".join(
            [
                _seen_row("Sent Items", "888"),
                _attachment_row("Sent Items", "888", filename="signed.pdf"),
            ]
        ),
    )

    payload = json.loads(result)
    assert payload["returned"] == 1
    assert payload["resolved_message_ids"] == ["888"]
    assert payload["unresolved_message_ids"] == []
    assert payload["items"][0]["mailbox"] == "Sent Items"
    assert payload["complete"] is True


def test_mailboxes_takes_precedence_over_mailbox():
    _result, capture = _list(
        mailbox="Archive",
        mailboxes=["Sent Items"],
        output_format="json",
    )

    assert 'mailbox "Sent Items"' in capture.last_script
    assert 'mailbox "Archive"' not in capture.last_script


# ---------------------------------------------------------------------------
# Unresolved ids
# ---------------------------------------------------------------------------


def test_unresolved_id_is_reported_and_marks_result_incomplete():
    result, _capture = _list(
        message_ids=["777", "999"],
        output_format="json",
        _return_value="\n".join([_seen_row("INBOX", "777"), _attachment_row("INBOX", "777")]),
    )

    payload = json.loads(result)
    assert payload["resolved_message_ids"] == ["777"]
    assert payload["unresolved_message_ids"] == ["999"]
    assert payload["complete"] is False
    assert payload["errors"] == ["1 of 2 requested message id(s) were not found in the searched mailbox(es): INBOX"]


def test_text_mode_prints_a_partial_line_for_unresolved_ids():
    result, _capture = _list(
        message_ids=["777", "999"],
        _return_value="\n".join([_seen_row("INBOX", "777"), _attachment_row("INBOX", "777")]),
    )

    assert "ATTACHMENTS FOR: message_ids: 777, 999" in result
    assert "Mailboxes searched: INBOX" in result
    assert "PARTIAL: ⚠ 1 of 2 requested message id(s) were not found" in result
    assert "📎 report.pdf (2 KB)" in result


def test_resolved_message_with_zero_attachments_is_not_unresolved():
    result, _capture = _list(
        message_ids=["777"],
        output_format="json",
        _return_value=_seen_row("INBOX", "777"),
    )

    payload = json.loads(result)
    assert payload["returned"] == 0
    assert payload["resolved_message_ids"] == ["777"]
    assert payload["unresolved_message_ids"] == []
    assert payload["complete"] is True
    assert "errors" not in payload


def test_text_mode_renders_a_resolved_message_with_no_attachments():
    result, _capture = _list(message_ids=["777"], _return_value=_seen_row("INBOX", "777"))

    assert "No attachments" in result
    assert "FOUND: 1 matching email(s)" in result
    assert "PARTIAL" not in result


# ---------------------------------------------------------------------------
# Mailbox failures
# ---------------------------------------------------------------------------


def test_failing_mailbox_yields_a_structured_error_without_aborting_the_others():
    result, _capture = _list(
        message_ids=["777"],
        mailboxes=["INBOX", "Nope"],
        output_format="json",
        _return_value="\n".join(
            [
                _seen_row("INBOX", "777"),
                _attachment_row("INBOX", "777"),
                f"{MAILBOX_ERROR}Nope|||Mailbox not found: Nope",
            ]
        ),
    )

    payload = json.loads(result)
    assert payload["returned"] == 1
    assert payload["resolved_message_ids"] == ["777"]
    assert payload["complete"] is False
    assert payload["errors"] == ["Nope: Mailbox not found: Nope"]


def test_mailbox_failure_is_emitted_in_band_per_mailbox():
    _result, capture = _list(mailboxes=["INBOX", "Nope"], output_format="json")

    assert capture.last_script.count("on error errMsg") >= 2
    assert f'"{MAILBOX_ERROR}Nope|||"' in capture.last_script


def test_text_mode_prints_a_partial_line_for_a_failing_mailbox():
    result, _capture = _list(
        message_ids=["777"],
        mailboxes=["INBOX", "Nope"],
        _return_value="\n".join(
            [
                _seen_row("INBOX", "777"),
                f"{MAILBOX_ERROR}Nope|||Mailbox not found: Nope",
            ]
        ),
    )

    assert "PARTIAL: ⚠ Nope: Mailbox not found: Nope" in result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_empty_mailboxes_list_is_rejected():
    with patch("apple_mail_mcp.tools.analytics.run_applescript") as mock_run:
        result = analytics_tools.list_email_attachments(account="Work", message_ids=["777"], mailboxes=["  "])

    mock_run.assert_not_called()
    assert result == "Error: mailboxes must contain at least one mailbox name"


def test_all_inside_mailboxes_is_rejected():
    with patch("apple_mail_mcp.tools.analytics.run_applescript") as mock_run:
        result = analytics_tools.list_email_attachments(account="Work", message_ids=["777"], mailboxes=["INBOX", "All"])

    mock_run.assert_not_called()
    assert result.startswith('Error: mailboxes does not accept "All"')


def test_mailbox_all_is_rejected():
    with patch("apple_mail_mcp.tools.analytics.run_applescript") as mock_run:
        result = analytics_tools.list_email_attachments(account="Work", message_ids=["777"], mailbox="All")

    mock_run.assert_not_called()
    assert result.startswith('Error: mailbox does not accept "All"')


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def test_chunked_path_merges_the_completeness_fields():
    ids = [str(i) for i in range(1, 52)]

    def router(script: str) -> str:
        if "id is 51" in script:
            return "\n".join(
                [
                    _seen_row("Sent Items", "51"),
                    _attachment_row("Sent Items", "51", filename="second.pdf", size="2048"),
                    f"{MAILBOX_ERROR}Nope|||Mailbox not found: Nope",
                ]
            )
        return "\n".join([_seen_row("INBOX", "1"), _attachment_row("INBOX", "1", filename="first.pdf")])

    result, capture = _list(
        message_ids=ids,
        mailboxes=["INBOX", "Sent Items", "Nope"],
        output_format="json",
        _router=router,
    )

    payload = json.loads(result)
    assert len(capture.scripts) == 2
    assert payload["chunk_size"] == 50
    assert payload["returned"] == 2
    assert payload["message_ids"] == ids
    assert payload["mailboxes_searched"] == ["INBOX", "Sent Items", "Nope"]
    assert payload["resolved_message_ids"] == ["1", "51"]
    assert payload["unresolved_message_ids"] == [i for i in ids if i not in {"1", "51"}]
    assert payload["complete"] is False
    assert "Nope: Mailbox not found: Nope" in payload["errors"]
    assert any("49 of 51 requested message id(s) were not found" in text for text in payload["errors"])
    assert [item["mailbox"] for item in payload["items"]] == ["INBOX", "Sent Items"]


def test_chunked_mailbox_error_is_not_duplicated_across_chunks():
    ids = [str(i) for i in range(1, 52)]
    row = f"{MAILBOX_ERROR}Nope|||Mailbox not found: Nope"

    result, _capture = _list(
        message_ids=ids,
        mailboxes=["Nope"],
        output_format="json",
        _return_value=row,
    )

    payload = json.loads(result)
    assert payload["errors"].count("Nope: Mailbox not found: Nope") == 1


def test_script_reconciles_scanned_against_read_messages():
    """A matched message whose read threw must not silently read as "not found"."""
    _result, capture = _list(mailboxes=["Sent Items"], output_format="json")

    assert "set mailboxScanned to 0" in capture.last_script
    assert "set mailboxRead to mailboxRead + 1" in capture.last_script
    assert "if mailboxRead < mailboxScanned then" in capture.last_script
    assert f'"{MAILBOX_ERROR}Sent Items|||read failed for "' in capture.last_script
