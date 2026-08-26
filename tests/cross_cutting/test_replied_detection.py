"""Tests for the Message-ID based replied-detection added in 3.1.8.

Covers three tool surfaces:

- ``smart_inbox.get_needs_response`` — replaces fragile subject-substring
  matching with In-Reply-To / References header parsing.
- ``inbox.list_inbox_emails``: ``exclude_replied``/``flag_replied`` now read
  Mail's native ``was_replied_to`` property (2026-07-10 reply-state-annotation
  rework, see ``core.reply_state.was_replied_fragment``); no more
  Sent-mailbox Message-ID join for this tool (see ``ListInboxRepliedTests``).
- ``search.search_emails``: same native-property rework (see
  ``SearchEmailsRepliedTests``).

These tests follow the existing pattern of mocking ``run_applescript`` and
asserting on the generated script text plus the rendered output.
"""

import asyncio
import json
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import inbox as inbox_tools
from apple_mail_mcp.tools import search as search_tools
from apple_mail_mcp.tools import smart_inbox as smart_inbox_tools


def _run(coro):
    if asyncio.iscoroutine(coro):
        return asyncio.run(coro)
    return coro


class GetNeedsResponseScriptTests(unittest.TestCase):
    """Verify get_needs_response uses Message-ID based replied detection.

    After the 3.2 refactor the inbox loop is a flat ``MSG|||...`` emitter and
    replied-matching happens in Python (O(1) set lookup vs AppleScript O(N×M)).
    These tests pin the new shape: the inbox script must surface
    ``message id of aMessage`` so Python can do the join, and when
    ``check_already_replied=True`` a second script (fetched via the shared
    core helper) must parse In-Reply-To / References from Sent.
    """

    def test_inbox_script_emits_structured_message_id_rows(self):
        captured: dict[str, list[str]] = {"scripts": []}

        def fake_run(script, timeout=120):
            captured["scripts"].append(script)
            return ""

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run):
            smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=1,
                max_results=1,
                check_already_replied=True,
            )

        # Two scripts now: inbox emitter + replied-id probe.
        self.assertEqual(len(captured["scripts"]), 2)
        inbox_script, replied_script = captured["scripts"]
        # Inbox script must surface message-id so Python can do the match.
        self.assertIn("message id of aMessage", inbox_script)
        # Inbox emits structured MSG|||... rows (parsed in Python).
        self.assertIn('"MSG|||"', inbox_script)
        # Inner repliedIds AppleScript loop is gone — matching is in Python.
        self.assertNotIn("repeat with repliedRef in repliedIds", inbox_script)
        # The replied-id probe still does header-based detection.
        self.assertIn("set outputLines to {}", replied_script)
        self.assertIn('"SCANNED|||"', replied_script)
        self.assertIn('"TOTAL|||"', replied_script)
        self.assertIn("In-Reply-To:", replied_script)
        self.assertIn("References:", replied_script)
        # Subject fallback must NOT be present — header-based only.
        self.assertNotIn("sentSubjects", inbox_script)
        self.assertNotIn("sentSubjects", replied_script)
        self.assertNotIn("subject of aSentMessage", inbox_script)
        self.assertNotIn("subject of aSentMessage", replied_script)

    def test_check_already_replied_false_does_not_call_sent_script(self):
        captured: dict[str, list[str]] = {"scripts": []}

        def fake_run(script, timeout=120):
            captured["scripts"].append(script)
            return ""

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run):
            smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=1,
                max_results=5,
                # check_already_replied default = False.
            )

        # Exactly one script: the inbox emitter. No Sent scan.
        self.assertEqual(len(captured["scripts"]), 1)
        self.assertNotIn("sentMailbox", captured["scripts"][0])

    def test_include_already_replied_false_skips_replied_emails(self):
        # Schema: MSG|||message_id|||subject|||sender|||date|||is_flagged|||has_question
        inbox_raw = (
            "MSG|||<keep-1@example.com>|||Project sync|||alice@example.com|||2026-05-20|||false|||false\n"
            "MSG|||<replied-1@example.com>|||Old thread|||bob@example.com|||2026-05-19|||false|||false"
        )
        replied_raw = "<replied-1@example.com>"
        sequence = [inbox_raw, replied_raw]

        def fake_run(script, timeout=120):
            return sequence.pop(0) if sequence else ""

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=1,
                max_results=10,
                include_already_replied=False,
                check_already_replied=True,
            )

        self.assertIsInstance(result, str)
        # The kept email appears; the replied one is filtered out.
        self.assertIn("Project sync", result)
        self.assertNotIn("Old thread", result)
        # Footer references the toggle.
        self.assertIn("include_already_replied=True", result)
        # And the count of skipped emails is surfaced.
        self.assertIn("Filtered 1 already-replied", result)

    def test_include_already_replied_true_keeps_and_annotates(self):
        inbox_raw = (
            "MSG|||<keep-1@example.com>|||Project sync|||alice@example.com|||2026-05-20|||false|||false\n"
            "MSG|||<replied-1@example.com>|||Old thread|||bob@example.com|||2026-05-19|||false|||false"
        )
        replied_raw = "<replied-1@example.com>"
        sequence = [inbox_raw, replied_raw]

        def fake_run(script, timeout=120):
            return sequence.pop(0) if sequence else ""

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=1,
                max_results=10,
                include_already_replied=True,
                check_already_replied=True,
            )

        self.assertIsInstance(result, str)
        # Both kept; replied one annotated with the [ALREADY REPLIED] prefix.
        self.assertIn("Project sync", result)
        self.assertIn("Old thread", result)
        self.assertIn("[ALREADY REPLIED]", result)


class ListInboxRepliedTests(unittest.TestCase):
    """list_inbox_emails replied-detection: native ``was_replied_to`` flag
    (see ``core.reply_state.was_replied_fragment``). No second Sent-mailbox
    AppleScript round trip fires anymore for exclude/flag_replied (2026-07-10
    reply-state-annotation rework); a single mocked ``run_applescript``
    response covers the whole call. ``include_draft_state=False`` keeps
    these tests isolated from the (also-added) Drafts-scan call."""

    def test_exclude_replied_requests_no_sent_scan_script(self):
        captured = {"scripts": []}

        def fake_run(script, timeout=120):
            captured["scripts"].append(script)
            return ""

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=3,
                    output_format="json",
                    exclude_replied=True,
                    include_draft_state=False,
                )
            )

        # Exactly one script: the inbox JSON emitter. No Sent-mailbox probe.
        self.assertEqual(len(captured["scripts"]), 1)
        inbox_script = captured["scripts"][0]
        self.assertIn("was replied to of aMessage", inbox_script)
        self.assertNotIn("set repliedIds to {}", inbox_script)

    def test_exclude_replied_filters_matching_emails_json(self):
        # Schema: subject|||sender|||date|||read|||account|||mail_app_id|||was_replied_to
        inbox_raw = (
            "S1|||a@example.com|||Date|||false|||Work|||101|||false\n"
            "S2|||b@example.com|||Date|||false|||Work|||102|||true"
        )

        def fake_run(script, timeout=120):
            return inbox_raw

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            response = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=10,
                    output_format="json",
                    exclude_replied=True,
                    include_draft_state=False,
                )
            )

        # v3.2.x: JSON path returns a dict directly.
        self.assertIsInstance(response, dict)
        subjects = [r["subject"] for r in response["emails"]]
        self.assertEqual(subjects, ["S1"])

    def test_flag_replied_annotates_json_with_already_replied_field(self):
        # Schema: subject|||sender|||date|||read|||account|||mail_app_id|||was_replied_to
        inbox_raw = (
            "S1|||a@example.com|||Date|||false|||Work|||101|||false\n"
            "S2|||b@example.com|||Date|||false|||Work|||102|||true"
        )

        def fake_run(script, timeout=120):
            return inbox_raw

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            response = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=10,
                    output_format="json",
                    exclude_replied=False,
                    flag_replied=True,
                    include_draft_state=False,
                )
            )

        # v3.2.x: JSON path returns a dict directly.
        self.assertIsInstance(response, dict)
        marked = {r["subject"]: r.get("already_replied", False) for r in response["emails"]}
        self.assertEqual(marked, {"S1": False, "S2": True})


class SearchEmailsRepliedTests(unittest.TestCase):
    """search_emails replied-detection: native ``was_replied_to`` flag, 15th
    pipe field (see ``core.reply_state.was_replied_fragment``). No second
    Sent-mailbox AppleScript round trip fires anymore for exclude/flag_replied
    (unlike ``list_inbox_emails``/``get_needs_response``, which keep their own
    opt-in Sent-scan paths), so a single mocked ``run_applescript`` response
    covers the whole call. ``include_draft_state=False`` keeps these tests
    isolated from the (also-added) Drafts-scan call."""

    def test_exclude_replied_filters_matching_records(self):
        # 15-field rows: ...|||content_preview|||to|||cc|||in_reply_to|||references|||bcc|||was_replied_to
        search_raw = (
            "1|||<id-1@example.com>|||Sub 1|||a@x.com|||INBOX|||Work|||false|||2026-05-01T00:00:00|||||||||||||||||||||false"
            "\n"
            "2|||<id-2@example.com>|||Sub 2|||b@x.com|||INBOX|||Work|||false|||2026-05-02T00:00:00|||||||||||||||||||||true"
        )

        def fake_run(script, timeout=120):
            return search_raw

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = _run(
                search_tools.search_emails(
                    account="Work",
                    recent_days=2.0,
                    output_format="json",
                    exclude_replied=True,
                    include_draft_state=False,
                )
            )

        payload = json.loads(result)
        subjects = [item["subject"] for item in payload["items"]]
        self.assertEqual(subjects, ["Sub 1"])

    def test_flag_replied_marks_records_already_replied_in_json(self):
        search_raw = (
            "1|||<id-1@example.com>|||Sub 1|||a@x.com|||INBOX|||Work|||false|||2026-05-01T00:00:00|||||||||||||||||||||false"
            "\n"
            "2|||<id-2@example.com>|||Sub 2|||b@x.com|||INBOX|||Work|||false|||2026-05-02T00:00:00|||||||||||||||||||||true"
        )

        def fake_run(script, timeout=120):
            return search_raw

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = _run(
                search_tools.search_emails(
                    account="Work",
                    recent_days=2.0,
                    output_format="json",
                    exclude_replied=False,
                    flag_replied=True,
                    include_draft_state=False,
                )
            )

        payload = json.loads(result)
        was_replied = {item["subject"]: item["was_replied_to"] for item in payload["items"]}
        self.assertEqual(was_replied, {"Sub 1": False, "Sub 2": True})
        # Legacy already_replied field: only set (True) for compat, never
        # added as an explicit False key.
        marked = {item["subject"]: item.get("already_replied", False) for item in payload["items"]}
        self.assertEqual(marked, {"Sub 1": False, "Sub 2": True})

    def test_flag_replied_text_output_includes_replied_marker(self):
        search_raw = (
            "1|||<id-1@example.com>|||Sub 1|||a@x.com|||INBOX|||Work|||false|||2026-05-01T00:00:00|||||||||||||||||||||false"
            "\n"
            "2|||<id-2@example.com>|||Sub 2|||b@x.com|||INBOX|||Work|||false|||2026-05-02T00:00:00|||||||||||||||||||||true"
        )

        def fake_run(script, timeout=120):
            return search_raw

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = _run(
                search_tools.search_emails(
                    account="Work",
                    recent_days=2.0,
                    output_format="text",
                    exclude_replied=False,
                    flag_replied=True,
                    include_draft_state=False,
                )
            )

        # The text formatter prefixes the subject of replied emails, driven
        # by the native was_replied_to flag (flag_replied no longer gates
        # the marker itself; it only adds the legacy JSON field).
        self.assertIn("[REPLIED] Sub 2", result)
        self.assertNotIn("[REPLIED] Sub 1", result)

    def test_native_replied_marker_shown_without_flag_replied(self):
        """was_replied_to drives the [REPLIED] marker unconditionally, no
        parameter gates it, unlike the deprecated flag_replied."""
        search_raw = "1|||<id-1@example.com>|||Sub 1|||a@x.com|||INBOX|||Work|||false|||2026-05-01T00:00:00|||||||||||||||||||||true"

        def fake_run(script, timeout=120):
            return search_raw

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = _run(
                search_tools.search_emails(
                    account="Work",
                    recent_days=2.0,
                    output_format="text",
                    include_draft_state=False,
                )
            )

        self.assertIn("[REPLIED] Sub 1", result)


class CoreRepliedIdsScriptTests(unittest.TestCase):
    """Verify the shared core helper emits well-formed AppleScript."""

    def test_fetch_replied_ids_script_includes_header_parsing(self):
        from apple_mail_mcp.core import fetch_replied_ids_script

        script = fetch_replied_ids_script("Work", sent_cap=50)
        self.assertIn('account "Work"', script)
        self.assertIn("set repliedIds to {}", script)
        self.assertIn("In-Reply-To:", script)
        self.assertIn("References:", script)
        # Cap is templated into the script.
        self.assertIn("sentUpperBound to 50", script)
        # No `whose` clause against the sent mailbox.
        self.assertNotIn("whose", script)

    def test_fetch_replied_ids_normalizes_angle_brackets(self):
        from apple_mail_mcp.core import fetch_replied_ids

        # Three IDs: with brackets, without brackets, empty line — empty
        # should be ignored, the unbracketed should be wrapped.
        raw = "<id-a@example.com>\nid-b@example.com\n\n"

        with patch("apple_mail_mcp.core.run_applescript", return_value=raw):
            ids = fetch_replied_ids("Work", sent_cap=50)

        self.assertEqual(ids, {"<id-a@example.com>", "<id-b@example.com>"})

    def test_replied_ids_script_has_no_subject_of_sent_message(self):
        """Regression: subject fallback path must be completely absent."""
        from apple_mail_mcp.core import replied_ids_script

        script = replied_ids_script()
        self.assertNotIn("subject of aSentMessage", script)
        self.assertNotIn("sentSubjects", script)
        self.assertNotIn("subjectReadCap", script)
        self.assertNotIn("subjectReadCount", script)

    def test_replied_ids_script_no_subject_read_cap_constant(self):
        """Regression: REPLIED_SUBJECT_READ_CAP must not exist as a constant."""
        import apple_mail_mcp.core as core_module

        self.assertFalse(
            hasattr(core_module, "REPLIED_SUBJECT_READ_CAP"),
            "REPLIED_SUBJECT_READ_CAP must be removed; subject fallback is gone",
        )
        # Header cap still exists.
        self.assertTrue(hasattr(core_module, "REPLIED_HEADER_READ_CAP"))
        self.assertIsInstance(core_module.REPLIED_HEADER_READ_CAP, int)
        self.assertGreater(core_module.REPLIED_HEADER_READ_CAP, 0)


if __name__ == "__main__":
    unittest.main()
