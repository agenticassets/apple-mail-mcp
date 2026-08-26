"""Tests for the ``output_format="json"`` surface on smart_inbox tools.

Phase 2c (3.2 robustness) added a structured-dict output to:

- ``smart_inbox.get_needs_response`` — replaces the inline AppleScript
  nested loop with a flat ``MSG|||...`` emitter + Python-side O(1) set
  matching against the replied-IDs returned by ``fetch_replied_ids``.
- ``smart_inbox.get_awaiting_reply`` — the existing two-script pattern
  now also speaks JSON.

These tests pin the dict shape (not the text formatting), error paths,
and the Python replied-matching join.
"""

import unittest
from unittest.mock import patch

from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.tools import smart_inbox as smart_inbox_tools


class GetNeedsResponseTextOutputTests(unittest.TestCase):
    """Text mode after the refactor still produces the same human-readable shape."""

    def test_text_mode_renders_high_and_normal_sections(self):
        inbox_raw = (
            "MSG|||301|||<flagged-1@example.com>|||URGENT review|||boss@example.com|||2026-05-20|||true|||false\n"
            "MSG|||302|||<question-1@example.com>|||Got a minute?|||alice@example.com|||2026-05-20|||false|||true\n"
            "MSG|||303|||<normal-1@example.com>|||FYI status|||bob@example.com|||2026-05-19|||false|||false"
        )

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", return_value=inbox_raw):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=2,
                max_results=10,
            )

        self.assertIsInstance(result, str)
        # Flagged + question entries appear before the normal one.
        flagged_idx = result.index("URGENT review")
        question_idx = result.index("Got a minute?")
        normal_idx = result.index("FYI status")
        self.assertLess(flagged_idx, normal_idx)
        self.assertLess(question_idx, normal_idx)
        # Priority labels match the legacy AppleScript format.
        self.assertIn("HIGH (flagged)", result)
        self.assertIn("MEDIUM (contains question)", result)
        self.assertIn("NORMAL", result)
        # Header is still emitted.
        self.assertIn("EMAILS NEEDING RESPONSE", result)
        self.assertIn("Account: Work | Mailbox: INBOX", result)
        self.assertIn("Found 3 email(s) needing response.", result)


class GetNeedsResponseJsonTests(unittest.TestCase):
    """Verify the ``output_format='json'`` dict shape."""

    def test_json_mode_returns_dict_with_expected_keys(self):
        inbox_raw = (
            "MSG|||301|||<flagged-1@example.com>|||URGENT review|||boss@example.com|||2026-05-20|||true|||false\n"
            "MSG|||302|||<question-1@example.com>|||Got a minute?|||alice@example.com|||2026-05-20|||false|||true\n"
            "MSG|||303|||<normal-1@example.com>|||FYI status|||bob@example.com|||2026-05-19|||false|||false"
        )
        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", return_value=inbox_raw):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=2,
                max_results=10,
                output_format="json",
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result["account"], "Work")
        self.assertEqual(result["mailbox"], "INBOX")
        self.assertEqual(result["days_back"], 2)
        self.assertEqual(result["max_results"], 10)
        # high_priority captures flagged + question rows; normal_priority the rest.
        high_subjects = [e["subject"] for e in result["high_priority"]]
        normal_subjects = [e["subject"] for e in result["normal_priority"]]
        self.assertEqual(high_subjects, ["URGENT review", "Got a minute?"])
        self.assertEqual(normal_subjects, ["FYI status"])
        # Per-entry shape.
        for entry in (*result["high_priority"], *result["normal_priority"]):
            self.assertIn("subject", entry)
            self.assertIn("sender", entry)
            self.assertIn("date", entry)
            self.assertIn("priority", entry)
            self.assertIn("already_replied", entry)
            self.assertIn("message_id", entry)
            self.assertIn("internet_message_id", entry)
            self.assertFalse(entry["already_replied"])
        self.assertEqual(result["skipped_replied_count"], 0)
        self.assertEqual(result["errors"], [])

    def test_json_mode_returns_numeric_message_id_and_internet_message_id(self):
        inbox_raw = "MSG|||301|||<flagged-1@example.com>|||URGENT review|||boss@example.com|||2026-05-20|||true|||false"
        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", return_value=inbox_raw):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=2,
                max_results=10,
                output_format="json",
            )

        entry = result["high_priority"][0]
        self.assertEqual(entry["message_id"], "301")
        self.assertEqual(entry["internet_message_id"], "<flagged-1@example.com>")

    def test_json_mode_marks_already_replied_when_replied_set_matches(self):
        # Two inbox candidates; the second was already replied to.
        inbox_raw = (
            "MSG|||401|||<keep-1@example.com>|||Project sync|||alice@example.com|||2026-05-20|||false|||false\n"
            "MSG|||402|||<replied-1@example.com>|||Old thread|||bob@example.com|||2026-05-19|||false|||false"
        )
        replied_raw = "REPLY|||<replied-1@example.com>\nSCANNED|||1\nTOTAL|||1"
        sequence = [inbox_raw, replied_raw]

        def fake_run(script, timeout=120):
            return sequence.pop(0) if sequence else ""

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=2,
                max_results=10,
                include_already_replied=True,
                check_already_replied=True,
                output_format="json",
            )

        all_entries = result["normal_priority"] + result["high_priority"]
        marked = {e["subject"]: e["already_replied"] for e in all_entries}
        self.assertEqual(marked, {"Project sync": False, "Old thread": True})
        # The already-replied entry gets the [ALREADY REPLIED] prefix on its priority.
        replied_entry = next(e for e in all_entries if e["subject"] == "Old thread")
        self.assertIn("[ALREADY REPLIED]", replied_entry["priority"])
        self.assertEqual(replied_entry["message_id"], "402")
        self.assertEqual(replied_entry["internet_message_id"], "<replied-1@example.com>")

    def test_json_mode_keeps_numeric_ids_when_sent_matching_uses_internet_ids(self):
        inbox_raw = (
            "MSG|||501|||<high@example.com>|||Flagged item|||lead@example.com|||2026-05-20|||true|||false\n"
            "MSG|||502|||<normal@example.com>|||Routine item|||peer@example.com|||2026-05-19|||false|||false"
        )
        replied_raw = "REPLY|||<normal@example.com>\nSCANNED|||1\nTOTAL|||1"
        sequence = [inbox_raw, replied_raw]

        def fake_run(script, timeout=120):
            return sequence.pop(0) if sequence else ""

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=2,
                max_results=10,
                include_already_replied=True,
                check_already_replied=True,
                output_format="json",
            )

        high_entry = result["high_priority"][0]
        normal_entry = result["normal_priority"][0]
        self.assertEqual(high_entry["message_id"], "501")
        self.assertEqual(high_entry["internet_message_id"], "<high@example.com>")
        self.assertFalse(high_entry["already_replied"])
        self.assertEqual(normal_entry["message_id"], "502")
        self.assertEqual(normal_entry["internet_message_id"], "<normal@example.com>")
        self.assertTrue(normal_entry["already_replied"])

    def test_json_mode_skips_replied_when_include_replied_false(self):
        inbox_raw = (
            "MSG|||401|||<keep-1@example.com>|||Project sync|||alice@example.com|||2026-05-20|||false|||false\n"
            "MSG|||402|||<replied-1@example.com>|||Old thread|||bob@example.com|||2026-05-19|||false|||false"
        )
        replied_raw = "REPLY|||<replied-1@example.com>\nSCANNED|||1\nTOTAL|||1"
        sequence = [inbox_raw, replied_raw]

        def fake_run(script, timeout=120):
            return sequence.pop(0) if sequence else ""

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=2,
                max_results=10,
                include_already_replied=False,
                check_already_replied=True,
                output_format="json",
            )

        all_entries = result["normal_priority"] + result["high_priority"]
        subjects = [e["subject"] for e in all_entries]
        self.assertEqual(subjects, ["Project sync"])
        self.assertEqual(result["skipped_replied_count"], 1)

    def test_json_mode_timeout_returns_error_dict(self):
        def boom(script, timeout=120):
            raise AppleScriptTimeout("simulated")

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=boom):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=1,
                max_results=5,
                output_format="json",
            )

        self.assertIsInstance(result, dict)
        self.assertIn("error", result)
        self.assertIn("timed out", result["error"])
        self.assertEqual(result["high_priority"], [])
        self.assertEqual(result["normal_priority"], [])
        self.assertEqual(result["errors"], [result["error"]])

    def test_invalid_output_format_returns_error_string(self):
        # Validation runs before any AppleScript dispatch, so no patching needed.
        result = smart_inbox_tools.get_needs_response(
            account="Work",
            days_back=1,
            output_format="yaml",
        )
        self.assertIsInstance(result, str)
        self.assertTrue(result.startswith("Error"))
        self.assertIn("invalid output_format", result)

    def test_json_mode_respects_max_results_cap(self):
        # Five candidates, all normal priority. max_results=2 must trim.
        inbox_raw = "\n".join(
            f"MSG|||{300 + i}|||<m{i}@example.com>|||Subj {i}|||u{i}@example.com|||2026-05-{20 - i:02d}|||false|||false"
            for i in range(5)
        )
        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", return_value=inbox_raw):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=2,
                max_results=2,
                output_format="json",
            )

        total = len(result["high_priority"]) + len(result["normal_priority"])
        self.assertEqual(total, 2)


class GetNeedsResponseReplyStateTests(unittest.TestCase):
    """Default exclusion of replied/drafted rows, opt-in restoration, and fail-open behavior.

    Mirrors ``tasks/active/reply-state-annotation/plan-2026-07-10.md``: the
    native ``was_replied_to`` flag (the trailing MSG field) and a Drafts
    snapshot (correlated via ``core.reply_state.DraftsSnapshot.matches``)
    are both excluded by default, with the exclusion always reported via
    ``skipped_replied_count`` / ``skipped_drafted_count``.
    """

    INBOX_RAW = "\n".join(
        [
            "MSG|||601|||<a1@example.com>|||Keep me|||alice@example.com|||2026-07-10T09:00:00|||false|||false|||false",
            "MSG|||602|||<a2@example.com>|||Already replied|||bob@example.com|||2026-07-09T09:00:00|||false|||false|||true",
            "MSG|||603|||<a3@example.com>|||Drafted item|||carol@example.com|||2026-07-08T09:00:00|||false|||false|||false",
            "MSG|||604|||<a4@example.com>|||Both signals|||dave@example.com|||2026-07-07T09:00:00|||false|||false|||true",
        ]
    )
    DRAFTS_RAW = "\n".join(
        [
            "DRAFT|||Re: Drafted item|||carol@example.com|||2026-07-08T10:00:00|||",
            "DRAFT|||Re: Both signals|||dave@example.com|||2026-07-07T12:00:00|||",
            "COUNT|||2",
            "TOTAL|||2",
        ]
    )

    @staticmethod
    def _dispatch_runner(*, inbox=None, sent=None, drafts=None, drafts_calls=None):
        """Return a fake ``run_applescript`` that routes by script marker.

        ``draftsMailbox`` only appears in the Drafts-snapshot script build
        (``core.reply_state.drafts_mailbox_block``); ``sentMailbox`` only
        in the Sent-header scan script (``core.replied.sent_mailbox_resolve_script``);
        anything else is the inbox scan. *drafts_calls*, if given, is a
        list appended to on every Drafts-scan invocation so tests can
        assert call counts (e.g. zero calls when the scan is disabled).
        """

        def _runner(script, timeout=120):
            if "draftsMailbox" in script:
                if drafts_calls is not None:
                    drafts_calls.append(script)
                return drafts if drafts is not None else "COUNT|||0\nTOTAL|||0"
            if "sentMailbox" in script:
                return sent if sent is not None else ""
            return inbox if inbox is not None else ""

        return _runner

    def test_default_excludes_replied_and_drafted_with_correct_skip_counts(self):
        runner = self._dispatch_runner(inbox=self.INBOX_RAW, drafts=self.DRAFTS_RAW)
        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=runner):
            result = smart_inbox_tools.get_needs_response(
                account="Work", days_back=7, max_results=10, output_format="json"
            )

        all_entries = result["high_priority"] + result["normal_priority"]
        subjects = {e["subject"] for e in all_entries}
        self.assertEqual(subjects, {"Keep me"})
        self.assertEqual(result["skipped_replied_count"], 2)
        self.assertEqual(result["skipped_drafted_count"], 2)
        self.assertEqual(result["draft_scan"]["status"], "ok")
        self.assertEqual(result["draft_scan"]["scanned"], 2)
        # Uniform envelope keys: a complete scan (TOTAL == COUNT) reports
        # total and truncated=False alongside status/scanned/accounts.
        self.assertEqual(result["draft_scan"]["total"], 2)
        self.assertFalse(result["draft_scan"]["truncated"])
        self.assertEqual(result["draft_scan"]["accounts"], ["Work"])
        kept = all_entries[0]
        self.assertFalse(kept["was_replied_to"])
        # Complete scan => a nonmatch is a definitive False, not fail-open None.
        self.assertIs(kept["has_draft"], False)

    def test_include_already_replied_restores_replied_rows_but_not_drafted(self):
        runner = self._dispatch_runner(inbox=self.INBOX_RAW, drafts=self.DRAFTS_RAW)
        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=runner):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=7,
                max_results=10,
                include_already_replied=True,
                output_format="json",
            )

        all_entries = result["high_priority"] + result["normal_priority"]
        subjects = {e["subject"] for e in all_entries}
        self.assertEqual(subjects, {"Keep me", "Already replied"})
        self.assertEqual(result["skipped_replied_count"], 0)
        self.assertEqual(result["skipped_drafted_count"], 2)
        replied_entry = next(e for e in all_entries if e["subject"] == "Already replied")
        self.assertTrue(replied_entry["was_replied_to"])
        self.assertTrue(replied_entry["already_replied"])
        self.assertIn("[ALREADY REPLIED]", replied_entry["priority"])

    def test_include_drafted_restores_drafted_rows_but_not_replied(self):
        runner = self._dispatch_runner(inbox=self.INBOX_RAW, drafts=self.DRAFTS_RAW)
        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=runner):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=7,
                max_results=10,
                include_drafted=True,
                output_format="json",
            )

        all_entries = result["high_priority"] + result["normal_priority"]
        subjects = {e["subject"] for e in all_entries}
        self.assertEqual(subjects, {"Keep me", "Drafted item"})
        self.assertEqual(result["skipped_replied_count"], 2)
        self.assertEqual(result["skipped_drafted_count"], 0)
        drafted_entry = next(e for e in all_entries if e["subject"] == "Drafted item")
        self.assertTrue(drafted_entry["has_draft"])
        self.assertIn("[HAS DRAFT]", drafted_entry["priority"])

    def test_include_draft_state_false_skips_snapshot_and_excludes_nothing_for_drafts(self):
        drafts_calls: list = []
        runner = self._dispatch_runner(inbox=self.INBOX_RAW, drafts=self.DRAFTS_RAW, drafts_calls=drafts_calls)
        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=runner):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=7,
                max_results=10,
                include_draft_state=False,
                output_format="json",
            )

        self.assertEqual(drafts_calls, [])
        all_entries = result["high_priority"] + result["normal_priority"]
        subjects = {e["subject"] for e in all_entries}
        # "Drafted item" is no longer excluded: has_draft is null, not True.
        self.assertEqual(subjects, {"Keep me", "Drafted item"})
        self.assertEqual(result["skipped_drafted_count"], 0)
        self.assertEqual(result["skipped_replied_count"], 2)
        self.assertEqual(
            result["draft_scan"],
            {"status": "skipped", "scanned": 0, "total": 0, "truncated": False, "accounts": []},
        )
        for entry in all_entries:
            self.assertIsNone(entry["has_draft"])

    def test_drafts_snapshot_error_fails_open(self):
        runner = self._dispatch_runner(inbox=self.INBOX_RAW, drafts="ERROR|||Could not find Drafts mailbox")
        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=runner):
            result = smart_inbox_tools.get_needs_response(
                account="Work", days_back=7, max_results=10, output_format="json"
            )

        self.assertEqual(result["draft_scan"]["status"], "error")
        self.assertIn("Could not find Drafts mailbox", result["draft_scan"]["error"])
        self.assertEqual(result["skipped_drafted_count"], 0)
        all_entries = result["high_priority"] + result["normal_priority"]
        subjects = {e["subject"] for e in all_entries}
        # Both non-replied rows survive: has_draft is null on scan error,
        # so nothing is excluded for draft state (fail open).
        self.assertEqual(subjects, {"Keep me", "Drafted item"})
        for entry in all_entries:
            self.assertIsNone(entry["has_draft"])

    def test_explicit_sent_header_match_ors_into_replied_state(self):
        inbox_raw = (
            "MSG|||701|||<b1@example.com>|||Untouched|||frank@example.com|||2026-07-10T09:00:00|||false|||false|||false\n"
            "MSG|||702|||<b2@example.com>|||Legacy match|||erin@example.com|||2026-07-09T09:00:00|||false|||false|||false"
        )
        runner = self._dispatch_runner(
            inbox=inbox_raw,
            sent="REPLY|||<b2@example.com>\nSCANNED|||2\nTOTAL|||2",
        )

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=runner):
            default_result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=7,
                max_results=10,
                check_already_replied=True,
                include_draft_state=False,
                output_format="json",
            )
        default_subjects = {e["subject"] for e in default_result["high_priority"] + default_result["normal_priority"]}
        self.assertEqual(default_subjects, {"Untouched"})
        self.assertEqual(default_result["skipped_replied_count"], 1)

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=runner):
            kept_result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=7,
                max_results=10,
                check_already_replied=True,
                include_already_replied=True,
                include_draft_state=False,
                output_format="json",
            )
        all_entries = kept_result["high_priority"] + kept_result["normal_priority"]
        legacy_entry = next(e for e in all_entries if e["subject"] == "Legacy match")
        # Native flag was false; the Sent-header scan is the only reason
        # this row counts as replied, so was_replied_to stays False while
        # already_replied (the combined signal) is True.
        self.assertFalse(legacy_entry["was_replied_to"])
        self.assertTrue(legacy_entry["already_replied"])
        self.assertIn("[ALREADY REPLIED]", legacy_entry["priority"])

    def test_truncated_sent_nonmatch_fails_open(self):
        inbox_raw = (
            "MSG|||703|||<not-in-window@example.com>|||Still open|||sender@example.com|||"
            "2026-07-10T09:00:00|||false|||false|||false"
        )
        runner = self._dispatch_runner(inbox=inbox_raw, sent="SCANNED|||10\nTOTAL|||25")
        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=runner):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                check_already_replied=True,
                include_draft_state=False,
                output_format="json",
            )

        entry = (result["high_priority"] + result["normal_priority"])[0]
        self.assertIsNone(entry["has_sent_reply"])
        self.assertIsNone(entry["reply_state"])
        self.assertEqual(result["skipped_replied_count"], 0)
        self.assertTrue(result["sent_reply_scan"]["truncated"])

    def test_text_mode_reports_truncated_sent_evidence(self):
        inbox_raw = (
            "MSG|||703|||<not-in-window@example.com>|||Still open|||sender@example.com|||"
            "2026-07-10T09:00:00|||false|||false|||false"
        )
        runner = self._dispatch_runner(inbox=inbox_raw, sent="SCANNED|||10\nTOTAL|||25")
        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=runner):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                check_already_replied=True,
                include_draft_state=False,
                output_format="text",
            )

        self.assertIn("Sent reply-state partial", result)
        self.assertIn("examined 10 of 25", result)
        self.assertIn("unknown reply state", result)

    def test_text_mode_reports_skip_notes_for_replied_and_drafted(self):
        runner = self._dispatch_runner(inbox=self.INBOX_RAW, drafts=self.DRAFTS_RAW)
        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=runner):
            result = smart_inbox_tools.get_needs_response(account="Work", days_back=7, max_results=10)

        self.assertIsInstance(result, str)
        self.assertIn("Filtered 2 already-replied email(s)", result)
        self.assertIn("Re-run with include_already_replied=True", result)
        self.assertIn("Filtered 2 drafted email(s)", result)
        self.assertIn("Re-run with include_drafted=True", result)

    def test_text_mode_notes_disabled_draft_state(self):
        runner = self._dispatch_runner(inbox=self.INBOX_RAW)
        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=runner):
            result = smart_inbox_tools.get_needs_response(
                account="Work", days_back=7, max_results=10, include_draft_state=False
            )

        self.assertIn("Draft-state check disabled", result)

    def test_text_mode_notes_draft_scan_error(self):
        runner = self._dispatch_runner(inbox=self.INBOX_RAW, drafts="ERROR|||boom")
        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=runner):
            result = smart_inbox_tools.get_needs_response(account="Work", days_back=7, max_results=10)

        self.assertIn("Draft scan failed", result)

    def test_json_shape_carries_draft_scan_and_skip_keys_with_no_candidates(self):
        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", return_value=""):
            result = smart_inbox_tools.get_needs_response(
                account="Work", days_back=7, max_results=10, output_format="json"
            )

        self.assertEqual(result["high_priority"], [])
        self.assertEqual(result["normal_priority"], [])
        self.assertEqual(result["skipped_replied_count"], 0)
        self.assertEqual(result["skipped_drafted_count"], 0)
        # No candidates at all: the Drafts snapshot is never fetched
        # (lazy fetch), so the scan is reported as skipped rather than
        # "ok" with zero scanned. The skipped envelope still carries the
        # uniform total/truncated keys.
        self.assertEqual(
            result["draft_scan"],
            {"status": "skipped", "scanned": 0, "total": 0, "truncated": False, "accounts": []},
        )


class GetAwaitingReplyJsonTests(unittest.TestCase):
    def _two_script_runner(self, inbox_raw: str, sent_raw: str):
        def runner(script, timeout=120):
            return inbox_raw if "inboxMailbox" in script else sent_raw

        return runner

    def test_json_mode_returns_dict_with_expected_shape(self):
        inbox_raw = ""  # No replies received yet.
        sent_raw = (
            "SENT|||101|||<proj-alpha@example.com>|||Project Alpha|||bob@example.com|||2026-05-20\n"
            "SENT|||102|||<need-update@example.com>|||Need update|||alice@example.com|||2026-05-19"
        )

        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript",
            side_effect=self._two_script_runner(inbox_raw, sent_raw),
        ):
            result = smart_inbox_tools.get_awaiting_reply(
                account="Work",
                days_back=7,
                max_results=5,
                exclude_noreply=False,
                output_format="json",
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result["account"], "Work")
        self.assertEqual(result["days_back"], 7)
        self.assertEqual(result["max_results"], 5)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["awaiting"]), 2)
        first = result["awaiting"][0]
        self.assertIn("subject", first)
        self.assertIn("recipient", first)
        self.assertIn("sent_at", first)
        self.assertIn("message_id", first)
        self.assertIn("mail_app_id", first)
        self.assertEqual(first["subject"], "Project Alpha")
        self.assertEqual(first["recipient"], "bob@example.com")
        self.assertEqual(first["mail_app_id"], "101")
        self.assertEqual(first["message_id"], "<proj-alpha@example.com>")

    def test_json_mode_filters_replied_and_noreply(self):
        # Sent: one to a real recipient (kept), one to no-reply (dropped),
        # one whose inbox has an In-Reply-To referencing it (dropped).
        inbox_raw = "INBOXHDR|||in-reply-to|||<replied-id@example.com>"
        sent_raw = (
            "SENT|||1|||<keep@example.com>|||Real subject|||friend@example.com|||2026-05-20\n"
            "SENT|||2|||<noreply@example.com>|||Notice|||noreply@vendor.com|||2026-05-20\n"
            "SENT|||3|||<replied-id@example.com>|||Got reply|||alice@example.com|||2026-05-19"
        )

        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript",
            side_effect=self._two_script_runner(inbox_raw, sent_raw),
        ):
            result = smart_inbox_tools.get_awaiting_reply(
                account="Work",
                days_back=7,
                max_results=10,
                exclude_noreply=True,
                output_format="json",
            )

        subjects = [row["subject"] for row in result["awaiting"]]
        self.assertEqual(subjects, ["Real subject"])

    def test_json_mode_includes_noreply_when_exclude_noreply_false(self):
        inbox_raw = ""
        sent_raw = (
            "SENT|||1|||<keep@example.com>|||Real subject|||friend@example.com|||2026-05-20\n"
            "SENT|||2|||<noreply@example.com>|||Notice|||noreply@vendor.com|||2026-05-20"
        )

        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript",
            side_effect=self._two_script_runner(inbox_raw, sent_raw),
        ):
            result = smart_inbox_tools.get_awaiting_reply(
                account="Work",
                days_back=7,
                max_results=10,
                exclude_noreply=False,
                output_format="json",
            )

        subjects = [row["subject"] for row in result["awaiting"]]
        self.assertEqual(subjects, ["Real subject", "Notice"])

    def test_json_mode_timeout_returns_error_dict(self):
        def boom(script, timeout=120):
            raise AppleScriptTimeout("simulated")

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=boom):
            result = smart_inbox_tools.get_awaiting_reply(
                account="Work",
                days_back=7,
                max_results=5,
                output_format="json",
            )

        self.assertIsInstance(result, dict)
        self.assertIn("error", result)
        self.assertIn("timed out", result["error"])
        self.assertEqual(result["awaiting"], [])

    def test_invalid_output_format_returns_error_string(self):
        result = smart_inbox_tools.get_awaiting_reply(account="Work", days_back=7, output_format="yaml")
        self.assertIsInstance(result, str)
        self.assertTrue(result.startswith("Error"))
        self.assertIn("invalid output_format", result)


class NeedsResponseRowParsingTests(unittest.TestCase):
    """Pin the ``MSG|||...`` parser against malformed and pipe-bearing input."""

    def test_parser_skips_malformed_lines(self):
        raw = (
            "MSG|||<a@example.com>|||Good|||alice@example.com|||2026-05-20|||false|||false\n"
            "MSG|||not-enough-fields\n"
            "MSG|||<b@example.com>|||Another|||bob@example.com|||2026-05-19|||true|||true"
        )
        rows = smart_inbox_tools._parse_needs_response_inbox_rows(raw)
        subjects = [r.subject for r in rows]
        self.assertEqual(subjects, ["Good", "Another"])
        self.assertTrue(rows[1].is_flagged)
        self.assertTrue(rows[1].has_question)

    def test_parser_supports_numeric_and_internet_message_ids(self):
        raw = "MSG|||301|||<a@example.com>|||Good|||alice@example.com|||2026-05-20|||false|||false"

        rows = smart_inbox_tools._parse_needs_response_inbox_rows(raw)

        self.assertEqual(rows[0].mail_app_id, "301")
        self.assertEqual(rows[0].internet_message_id, "<a@example.com>")

    def test_parser_keeps_legacy_internet_id_rows_as_non_actionable(self):
        raw = "MSG|||<a@example.com>|||Good|||alice@example.com|||2026-05-20|||false|||false"

        rows = smart_inbox_tools._parse_needs_response_inbox_rows(raw)

        self.assertEqual(rows[0].mail_app_id, "")
        self.assertEqual(rows[0].internet_message_id, "<a@example.com>")

    def test_parser_ignores_non_msg_prefixed_lines(self):
        raw = "ERROR|||not for us\nMSG|||<a@example.com>|||S|||u@example.com|||2026|||false|||false\nrandom noise"
        rows = smart_inbox_tools._parse_needs_response_inbox_rows(raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].subject, "S")

    def test_parser_reads_native_was_replied_field(self):
        raw = "MSG|||301|||<a@example.com>|||Good|||alice@example.com|||2026-05-20|||false|||false|||true"

        rows = smart_inbox_tools._parse_needs_response_inbox_rows(raw)

        self.assertTrue(rows[0].was_replied_to)

    def test_parser_defaults_was_replied_false_for_legacy_eight_field_rows(self):
        raw = "MSG|||301|||<a@example.com>|||Good|||alice@example.com|||2026-05-20|||false|||false"

        rows = smart_inbox_tools._parse_needs_response_inbox_rows(raw)

        self.assertFalse(rows[0].was_replied_to)

    def test_parser_defaults_was_replied_false_for_legacy_seven_field_rows(self):
        raw = "MSG|||<a@example.com>|||Good|||alice@example.com|||2026-05-20|||false|||false"

        rows = smart_inbox_tools._parse_needs_response_inbox_rows(raw)

        self.assertFalse(rows[0].was_replied_to)


class GetTopSendersJsonTests(unittest.TestCase):
    """JSON output_format on ``get_top_senders``."""

    _SENDER_RAW = (
        "ROW|||alice@example.com\n"
        "ROW|||alice@example.com\n"
        "ROW|||alice@example.com\n"
        "ROW|||bob@example.com\n"
        "ROW|||bob@example.com\n"
        "ROW|||carol@example.com\n"
        "TOTAL|||6\n"
        "MAILBOX_COUNT|||1200"
    )

    def test_invalid_output_format_returns_text_error(self):
        result = smart_inbox_tools.get_top_senders(account="Work", days_back=7, output_format="xml")
        self.assertIsInstance(result, str)
        self.assertIn("Error: invalid output_format", result)

    def test_json_mode_returns_dict_with_stable_keys(self):
        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript",
            return_value=self._SENDER_RAW,
        ):
            result = smart_inbox_tools.get_top_senders(
                account="Work",
                days_back=7,
                top_n=5,
                output_format="json",
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result["account"], "Work")
        self.assertEqual(result["mailbox"], "INBOX")
        self.assertEqual(result["days_back"], 7)
        self.assertEqual(result["top_n"], 5)
        self.assertFalse(result["group_by_domain"])
        self.assertEqual(result["total_analysed"], 6)
        self.assertEqual(result["mailbox_count"], 1200)
        self.assertEqual(result["unique_senders"], 3)
        self.assertEqual(result["errors"], [])
        keys = {entry["key"]: entry for entry in result["senders"]}
        self.assertEqual(keys["alice@example.com"]["count"], 3)
        self.assertEqual(keys["alice@example.com"]["percent"], 50)
        self.assertEqual(keys["bob@example.com"]["count"], 2)
        self.assertEqual(keys["carol@example.com"]["count"], 1)

    def test_json_mode_top_n_caps_results(self):
        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript",
            return_value=self._SENDER_RAW,
        ):
            result = smart_inbox_tools.get_top_senders(
                account="Work",
                days_back=7,
                top_n=2,
                output_format="json",
            )

        self.assertEqual(len(result["senders"]), 2)
        self.assertEqual(result["senders"][0]["key"], "alice@example.com")
        self.assertEqual(result["senders"][1]["key"], "bob@example.com")

    def test_json_mode_timeout_returns_error_dict(self):
        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript",
            side_effect=AppleScriptTimeout(120),
        ):
            result = smart_inbox_tools.get_top_senders(
                account="Work",
                days_back=7,
                output_format="json",
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result["error"], "timeout")
        self.assertEqual(result["senders"], [])
        self.assertTrue(result["errors"])
        self.assertIn("timed out", result["errors"][0])

    def test_json_mode_unbounded_scan_returns_dict(self):
        result = smart_inbox_tools.get_top_senders(
            account="Work",
            days_back=0,
            output_format="json",
        )
        self.assertIsInstance(result, dict)
        self.assertEqual(result["code"], "UNBOUNDED_SCAN_REQUIRED")
        self.assertEqual(result["senders"], [])
        self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main()
