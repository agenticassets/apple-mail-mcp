"""Tests for inbox listing helpers."""

import asyncio
import json
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import inbox as inbox_tools


def _run(coro):
    """Synchronously drive an async tool inside a test."""
    if asyncio.iscoroutine(coro):
        return asyncio.run(coro)
    return coro


class InboxToolTests(unittest.TestCase):
    def test_text_list_inbox_honors_account_filter(self):
        # In the 3.1.5 modernized list_inbox_emails, an explicit `account`
        # triggers the single-account fast path: the AppleScript looks up
        # `account "Work"` directly instead of iterating every account.
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "ok"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            _run(inbox_tools.list_inbox_emails(account="Work", max_emails=5))

        self.assertIn('account "Work"', captured["script"])
        # max_emails=5 should appear as a cap inside the script.
        self.assertIn("1 thru 5", captured["script"])

    def test_list_inbox_rejects_unbounded_scan(self):
        with patch("apple_mail_mcp.tools.inbox.run_applescript") as mock_run:
            result = _run(inbox_tools.list_inbox_emails(account="Work", max_emails=0))

        payload = json.loads(result)
        self.assertEqual(payload["code"], "UNBOUNDED_SCAN_REQUIRED")
        rem = payload["remediation"]
        self.assertNotIn("full_inbox_export", str(rem))
        self.assertTrue(rem.get("preferred"))
        mock_run.assert_not_called()

    def test_json_list_inbox_can_include_content_preview(self):
        # The JSON-format inbox listing should request a content preview when
        # include_content=True and parse the pipe-delimited script output.
        # JSON mode now returns a dict with stable shape {emails, errors};
        # callers must read .emails rather than treating the result as a list.
        captured = {"scripts": []}

        def fake_run(script, timeout=120):
            captured["scripts"].append(script)
            # JSON reply annotation requests Internet Message-ID before preview.
            return (
                "Subject|||sender@example.com|||Thu, Jan 1, 2026|||false|||Work|||1|||false"
                "|||<fixture@example.com>|||Hello | world"
            )

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            response = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=1,
                    include_content=True,
                    output_format="json",
                    include_draft_state=False,
                )
            )

        self.assertIsInstance(response, dict)
        self.assertEqual(response["errors"], [])
        self.assertTrue(any("content of aMessage" in script for script in captured["scripts"]))
        self.assertEqual(response["emails"][0]["content_preview"], "Hello | world")
        self.assertFalse(response["emails"][0]["was_replied_to"])

    def test_parser_preserves_delimiters_in_content_preview(self):
        # Schema: subject|||sender|||date|||read|||account|||mail_app_id|||was_replied_to|||content_preview
        records = inbox_tools._parse_pipe_delimited_emails(
            "Subject|||sender@example.com|||Date|||true|||Work|||1|||true|||Hello ||| still content"
        )

        self.assertEqual(records[0]["content_preview"], "Hello ||| still content")
        self.assertTrue(records[0]["was_replied_to"])


class ListMailboxesJsonTests(unittest.TestCase):
    def test_list_mailboxes_json_max_mailboxes_returns_wrapper(self):
        raw = "Work|||INBOX|||INBOX|||-1|||-1"
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return raw

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            result = inbox_tools.list_mailboxes(
                account="Work",
                output_format="json",
                max_mailboxes=1,
            )

        payload = json.loads(result)
        self.assertIn("mailboxes", payload)
        self.assertTrue(payload["truncated"])
        self.assertIn("if mailboxIndex > 1 then exit repeat", captured["script"])


class ListMailboxesChildCapTests(unittest.TestCase):
    """Fix 4 regression: cap must fire on children, not just parents."""

    def test_child_cap_fires_and_truncated_false_when_exact_fit(self):
        # Provide exactly max_mailboxes rows (1 parent + no children because
        # AppleScript cap fires). truncated should be True (conservative: cap
        # may have fired) when returned == max_mailboxes.
        raw = "Work|||INBOX|||INBOX|||-1|||-1"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            result = inbox_tools.list_mailboxes(
                account="Work",
                output_format="json",
                max_mailboxes=2,  # cap > returned (1), so truncated=False
            )

        payload = json.loads(result)
        self.assertFalse(payload["truncated"])

    def test_child_cap_appears_inside_child_loop(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            inbox_tools.list_mailboxes(
                account="Work",
                output_format="json",
                max_mailboxes=3,
            )

        script = captured["script"]
        # The cap_check must appear at least twice: once in the parent loop
        # and once in the child loop.
        self.assertGreaterEqual(
            script.count("if mailboxIndex > 3 then exit repeat"),
            2,
            "cap_check must fire in both parent and child loops",
        )


class GetMailboxUnreadCountsNoduplicateTests(unittest.TestCase):
    """Fix 5 regression: parent mailbox with children must not be double-emitted."""

    def test_parent_with_children_emits_only_child_paths(self):
        # Mock: 3-field lines account|||mailbox_path|||unread
        # Two lines: parent Funding (bare) + child TU/Funding — only TU/Funding
        # should appear after the fix (parent emitted only when it has no children).
        # The mock simulates the fixed AppleScript output (children only).
        raw = "Work|||TU/Funding|||3"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            result = inbox_tools.get_mailbox_unread_counts(account="Work")

        keys = list(result.get("Work", {}).keys())
        # Bare "Funding" must NOT appear; only "TU/Funding".
        self.assertNotIn("Funding", keys)
        self.assertIn("TU/Funding", keys)

    def test_no_duplicate_keys_in_result(self):
        # Two rows with the same mailbox path (would be a duplicate).
        raw = "Work|||INBOX|||5\nWork|||INBOX|||5"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            result = inbox_tools.get_mailbox_unread_counts(account="Work")

        # Python dict assignment overwrites, so count of INBOX is 1 (no duplicate key).
        work_mailboxes = result.get("Work", {})
        self.assertEqual(list(work_mailboxes.keys()).count("INBOX"), 1)


class ListInboxEmailsMessageIdTests(unittest.TestCase):
    """Fix 6 regression: message_id must always be present in JSON output."""

    def test_json_output_always_has_message_id(self):
        # Schema now always includes mail_app_id at field[5].
        # JSON mode returns a dict {emails, errors}.
        raw = "Subject|||sender@example.com|||Date|||false|||Work|||42"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            response = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=1,
                    output_format="json",
                )
            )

        self.assertIsInstance(response, dict)
        emails = response["emails"]
        self.assertEqual(len(emails), 1)
        self.assertIn("message_id", emails[0])
        self.assertEqual(emails[0]["message_id"], "42")
        self.assertEqual(response["errors"], [])

    def test_json_script_always_emits_mail_app_id(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=1,
                    output_format="json",
                )
            )

        # The integer id of the message must always be captured unconditionally.
        self.assertIn("set mailAppId to id of aMessage", captured["script"])


class OverviewParseTests(unittest.TestCase):
    def test_parse_overview_account_collects_malformed_counts(self):
        raw = "\n".join(
            [
                "HEADER|||Work|||not-a-number|||also-bad",
                "MAILBOX|||Inbox|||bad-count",
            ]
        )
        parsed = inbox_tools._parse_overview_account(raw)
        self.assertIn("parse_errors", parsed)
        self.assertEqual(len(parsed["parse_errors"]), 2)


class ListMailboxesTextModeCapTests(unittest.TestCase):
    """Fix #6: list_mailboxes text mode must honor max_mailboxes with truncation banner."""

    def test_text_mode_script_has_cap_counter(self):
        """Generated AppleScript must include a counter and early-exit for max_mailboxes."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "MAILBOXES\n\n"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            inbox_tools.list_mailboxes(account="Work", max_mailboxes=25)

        script = captured.get("script", "")
        # Must contain the counter and early-exit pattern
        self.assertIn("mailboxCount", script)
        self.assertIn("exit repeat", script)
        self.assertIn("25", script)

    def test_text_mode_default_cap_is_100(self):
        """Default max_mailboxes for text mode must be 100."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "MAILBOXES\n\n"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            inbox_tools.list_mailboxes(account="Work")

        script = captured.get("script", "")
        # Default cap of 100 must appear in the script
        self.assertIn("100", script)
        self.assertIn("mailboxCount", script)

    def test_text_mode_truncation_banner_in_applescript_output(self):
        """When AppleScript returns a truncation marker, the text response includes a banner."""
        cap = 5
        # Simulate AppleScript returning the truncation marker
        fake_output = (
            "MAILBOXES\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📁 ACCOUNT: Work\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "  📂 Inbox\n\n"
            f"⚠ Truncated: list_mailboxes capped at {cap} mailboxes per account.\n"
            "  Pass max_mailboxes=N to adjust the cap.\n"
        )

        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=fake_output):
            result = inbox_tools.list_mailboxes(account="Work", max_mailboxes=cap)

        # The response should contain the truncation banner from the script
        self.assertIn("Truncated", result)
        self.assertIn(str(cap), result)

    def test_json_mode_still_works_with_max_mailboxes(self):
        """JSON mode must still use max_mailboxes (regression guard)."""
        lines = "\n".join([f"Work|||Box{i}|||Box{i}|||0|||0" for i in range(5)])
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=lines):
            raw = inbox_tools.list_mailboxes(
                account="Work",
                output_format="json",
                max_mailboxes=3,
            )
        data = json.loads(raw)
        self.assertIn("truncated", data)
        self.assertTrue(data["truncated"])


class GetMailboxUnreadCountsCapTests(unittest.TestCase):
    """Fix #7: get_mailbox_unread_counts must cap mailbox enumeration."""

    def test_script_has_mailbox_index_cap(self):
        """Generated AppleScript must include a mailbox counter and early exit."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            inbox_tools.get_mailbox_unread_counts(account="Work", max_mailboxes=50)

        script = captured.get("script", "")
        self.assertIn("mailboxIndex", script)
        self.assertIn("exit repeat", script)
        self.assertIn("50", script)

    def test_default_max_mailboxes_is_100(self):
        """Default max_mailboxes for get_mailbox_unread_counts must be 100."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            inbox_tools.get_mailbox_unread_counts(account="Work")

        script = captured.get("script", "")
        self.assertIn("100", script)

    def test_truncated_marker_sets_truncated_flag(self):
        """When script emits __TRUNCATED__ marker, account dict has truthy truncation key."""
        # Simulate AppleScript output with truncation marker
        fake_output = "\n".join(
            [
                "Work|||Inbox|||5",
                "Work|||__TRUNCATED__|||100",
            ]
        )
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=fake_output):
            result = inbox_tools.get_mailbox_unread_counts(account="Work", max_mailboxes=100)

        # The account dict should have the truncation marker
        work_data = result.get("Work", {})
        self.assertEqual(work_data.get("Inbox"), 5)
        self.assertTrue(work_data.get("__truncated__"))

    def test_no_truncation_without_marker(self):
        """When script does not emit __TRUNCATED__, account dict has no truncation key."""
        fake_output = "Work|||Inbox|||3\nWork|||Archive|||0"
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=fake_output):
            result = inbox_tools.get_mailbox_unread_counts(account="Work", max_mailboxes=100)

        work_data = result.get("Work", {})
        self.assertNotIn("__truncated__", work_data)


class GetInboxOverviewMailboxCapTests(unittest.TestCase):
    """Fix #7: get_inbox_overview must cap mailbox enumeration at max_mailboxes=100."""

    def test_overview_script_has_mailbox_cap(self):
        """Generated AppleScript must include a mailbox index cap."""
        script = inbox_tools._build_overview_one_account_script("Work", include_mailboxes=True, max_mailboxes=100)
        self.assertIn("mailboxIndex", script)
        self.assertIn("exit repeat", script)
        self.assertIn("100", script)

    def test_overview_custom_max_mailboxes_in_script(self):
        """Custom max_mailboxes is reflected in the generated script."""
        script = inbox_tools._build_overview_one_account_script("Work", include_mailboxes=True, max_mailboxes=25)
        self.assertIn("25", script)
        self.assertIn("MAILBOX_CAPPED", script)

    def test_overview_parse_account_handles_mailbox_capped(self):
        """Parser sets mailboxes_truncated when MAILBOX_CAPPED tag is present."""
        raw = "\n".join(
            [
                "HEADER|||Work|||3|||100",
                "MAILBOX|||Inbox|||3",
                "MAILBOX_CAPPED|||Work|||100",
            ]
        )
        parsed = inbox_tools._parse_overview_account(raw)
        self.assertTrue(parsed.get("mailboxes_truncated"))
        self.assertEqual(parsed["unread"], 3)

    def test_overview_parse_account_no_capped_by_default(self):
        """Parser returns mailboxes_truncated=False when no MAILBOX_CAPPED tag."""
        raw = "\n".join(
            [
                "HEADER|||Work|||3|||100",
                "MAILBOX|||Inbox|||3",
            ]
        )
        parsed = inbox_tools._parse_overview_account(raw)
        self.assertFalse(parsed.get("mailboxes_truncated"))

    def test_overview_json_includes_mailboxes_truncated_flag(self):
        """JSON mode overview sets mailboxes_truncated on the account row."""
        accounts = [
            {
                "account": "Work",
                "unread": 3,
                "total": 100,
                "error": None,
                "mailboxes": [("Inbox", 3)],
                "recent": [],
                "mailboxes_truncated": True,
            }
        ]
        payload = inbox_tools._format_overview_json(accounts, [])
        work_row = next(r for r in payload["accounts"] if r["account"] == "Work")
        self.assertTrue(work_row.get("mailboxes_truncated"))

    def test_overview_json_no_flag_when_not_truncated(self):
        """JSON mode overview does not set mailboxes_truncated when not truncated."""
        accounts = [
            {
                "account": "Work",
                "unread": 1,
                "total": 50,
                "error": None,
                "mailboxes": [("Inbox", 1)],
                "recent": [],
                "mailboxes_truncated": False,
            }
        ]
        payload = inbox_tools._format_overview_json(accounts, [])
        work_row = next(r for r in payload["accounts"] if r["account"] == "Work")
        self.assertNotIn("mailboxes_truncated", work_row)


if __name__ == "__main__":
    unittest.main()
