"""Phase 2 scan-path hardening: compose caps, timeouts."""

import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.tools import analytics as analytics_tools
from apple_mail_mcp.tools import compose as compose_tools
from apple_mail_mcp.tools import inbox as inbox_tools
from apple_mail_mcp.tools import manage as manage_tools
from apple_mail_mcp.tools import search as search_tools
from apple_mail_mcp.tools import smart_inbox as smart_inbox_tools


def _make_subprocess_result(returncode=0, stdout=b"ok", stderr=b""):
    from unittest.mock import MagicMock

    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def _main_reply_script(scripts):
    return next(script for script in scripts if "reply foundMessage" in script)


class ComposeScanCapTests(unittest.TestCase):
    def test_manage_drafts_list_caps_draft_enumeration(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "Found 0 draft(s)"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            compose_tools.manage_drafts(account="Work", action="list")

        self.assertEqual(len(captured), 1)
        # Bounded cap: list reads the newest visible Drafts window and never
        # materializes the whole mailbox.
        self.assertIn("messages 1 thru headEnd of draftsMailbox", captured[0])
        self.assertIn("if headEnd > 75 then set headEnd to 75", captured[0])
        self.assertNotIn("every message of draftsMailbox", captured[0])

    def test_reply_to_email_subject_lookup_returns_deprecation_error(self):
        import json

        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                subject_keyword="Invoice",
                reply_body="Thanks",
            )

        self.assertEqual(captured, [])
        payload = json.loads(result)
        self.assertEqual(payload["code"], "TARGET_SELECTOR_DEPRECATED")
        self.assertEqual(payload["remediation"]["exact_selector"], "message_id")

    def test_reply_to_email_message_id_skips_subject_scan(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Thanks",
            )

        script = _main_reply_script(captured)
        self.assertIn("whose id is 12345", script)
        self.assertNotIn("messages 1 thru 75 of inboxMailbox", script)

    def test_forward_email_subject_lookup_returns_deprecation_error(self):
        import json

        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "ok"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            result = compose_tools.forward_email(
                account="Work",
                subject_keyword="Invoice",
                to="other@example.com",
            )

        self.assertEqual(captured, [])
        payload = json.loads(result)
        self.assertEqual(payload["code"], "TARGET_SELECTOR_DEPRECATED")
        self.assertEqual(payload["remediation"]["exact_selector"], "message_id")

    def test_forward_email_forwards_timeout_to_run_applescript(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["timeout"] = timeout
            return "ok"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            compose_tools.forward_email(
                account="Work",
                message_id="888",
                to="other@example.com",
                timeout=240,
            )

        self.assertEqual(captured["timeout"], 240)


class MessageIdsTests(unittest.TestCase):
    def test_move_email_with_message_ids_uses_exact_id_condition(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "moved"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            result = manage_tools.move_email(
                account="Work",
                to_mailbox="Archive",
                message_ids=["101", "202"],
                dry_run=True,
            )

        self.assertEqual(result, "moved")
        self.assertIn("id is 101", captured["script"])
        self.assertIn("id is 202", captured["script"])
        self.assertIn("DRY RUN - PREVIEW MOVE BY IDS", captured["script"])
        self.assertNotIn("move aMessage to destMailbox", captured["script"])

    def test_manage_trash_with_message_ids_uses_exact_id_condition(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "trashed"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            result = manage_tools.manage_trash(
                account="Work",
                action="move_to_trash",
                message_ids=["555"],
                dry_run=False,
            )

        self.assertEqual(result, "trashed")
        self.assertIn("id is 555", captured["script"])
        self.assertIn("MOVING EMAILS TO TRASH BY IDS", captured["script"])
        self.assertIn("move aMessage to trashMailbox", captured["script"])

    def test_manage_trash_permanent_delete_with_message_ids_dry_run_skips_delete(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "preview"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            result = manage_tools.manage_trash(
                account="Work",
                action="delete_permanent",
                message_ids=["555"],
            )

        self.assertEqual(result, "preview")
        self.assertIn("id is 555", captured["script"])
        self.assertIn("DRY RUN - PREVIEW PERMANENT DELETE BY IDS", captured["script"])
        self.assertIn("Would permanently delete", captured["script"])
        self.assertNotIn("delete aMessage", captured["script"])

    def test_save_email_attachment_with_message_ids_uses_exact_id_condition(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "saved"

        home = __import__("os").path.expanduser("~")
        save_path = f"{home}/Downloads/test-file.bin"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            manage_tools.save_email_attachment(
                account="Work",
                attachment_name="file.bin",
                save_path=save_path,
                message_ids=["777"],
            )

        self.assertIn("id is 777", captured["script"])
        self.assertNotIn("subject contains", captured["script"])


class TimeoutForwardingTests(unittest.TestCase):
    def test_get_email_by_id_forwards_timeout(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["timeout"] = timeout
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            search_tools.get_email_by_id(
                account="Work",
                message_id="99",
                timeout=180,
            )

        self.assertEqual(captured["timeout"], 180)

    def test_get_email_by_id_handles_timeout(self):
        with patch(
            "apple_mail_mcp.tools.search.run_applescript",
            side_effect=AppleScriptTimeout("slow"),
        ):
            result = search_tools.get_email_by_id(
                account="Work",
                message_id="99",
            )

        self.assertIn("timed out", result.lower())

    def test_save_email_attachment_forwards_timeout(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["run_timeout"] = timeout
            return "saved"

        home = __import__("os").path.expanduser("~")
        save_path = f"{home}/Downloads/test-file.bin"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            manage_tools.save_email_attachment(
                account="Work",
                attachment_name="file.bin",
                save_path=save_path,
                message_ids=["777"],
                timeout=90,
            )

        self.assertEqual(captured["run_timeout"], 90)

    def test_save_email_attachment_handles_timeout(self):
        home = __import__("os").path.expanduser("~")
        save_path = f"{home}/Downloads/test-file.bin"

        with patch(
            "apple_mail_mcp.tools.manage.run_applescript",
            side_effect=AppleScriptTimeout("slow"),
        ):
            result = manage_tools.save_email_attachment(
                account="Work",
                attachment_name="file.bin",
                save_path=save_path,
                message_ids=["777"],
            )

        self.assertIn("timed out", result.lower())

    def test_get_mailbox_unread_counts_forwards_timeout(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["timeout"] = timeout
            return "Work:3"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            inbox_tools.get_mailbox_unread_counts(summary_only=True, timeout=60)

        self.assertEqual(captured["timeout"], 60)

    def test_get_mailbox_unread_counts_summary_only_scopes_to_account(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "Work:3"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            result = inbox_tools.get_mailbox_unread_counts(
                account="Work",
                summary_only=True,
            )

        # The per-account counts, minus the cached-count provenance sentinel
        # (AGENTIC-2346: every reported unread number is labelled).
        self.assertEqual(
            {k: v for k, v in result.items() if not k.startswith("__")},
            {"Work": 3},
        )
        self.assertEqual(
            result["__unread_count_provenance__"]["unread_count_source"],
            "mail_cached_aggregate",
        )
        self.assertIn('if accountName is not "Work" then', captured["script"])
        self.assertIn("set shouldIncludeAccount to false", captured["script"])
        self.assertIn("if shouldIncludeAccount then", captured["script"])

    def test_get_mailbox_unread_counts_handles_timeout(self):
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            side_effect=AppleScriptTimeout("slow"),
        ):
            result = inbox_tools.get_mailbox_unread_counts(summary_only=True)

        self.assertEqual(result.get("error"), "timed_out")


class DashboardAccountScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_inbox_dashboard_passes_selected_account_to_unread_and_recent(self):
        captured = {}

        def fake_unread(**kwargs):
            captured["unread"] = kwargs
            return {"Work": 4}

        async def fake_recent(**kwargs):
            captured["recent"] = kwargs
            return []

        def fake_ui(**kwargs):
            captured["ui"] = kwargs
            return {"ok": True}

        fake_ui_module = SimpleNamespace(create_inbox_dashboard_ui=fake_ui)

        with (
            patch("apple_mail_mcp.UI_AVAILABLE", True),
            patch.dict(sys.modules, {"ui": fake_ui_module}),
            patch(
                "apple_mail_mcp.tools.inbox.get_mailbox_unread_counts",
                side_effect=fake_unread,
            ),
            patch(
                "apple_mail_mcp.tools.analytics._get_recent_emails_structured_async",
                side_effect=fake_recent,
            ),
        ):
            result = await analytics_tools.inbox_dashboard(
                account="Work",
                max_total=5,
                max_per_account=3,
                timeout=45,
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(captured["unread"]["account"], "Work")
        self.assertTrue(captured["unread"]["summary_only"])
        self.assertEqual(captured["unread"]["timeout"], 45)
        self.assertEqual(captured["recent"]["account"], "Work")
        self.assertEqual(captured["recent"]["max_total"], 5)
        self.assertEqual(captured["recent"]["max_per_account"], 3)
        self.assertEqual(captured["recent"]["timeout"], 45)

    async def test_inbox_dashboard_json_does_not_require_ui_package(self):
        async def fake_recent(**kwargs):
            return [{"subject": "Hello"}]

        with (
            patch("apple_mail_mcp.UI_AVAILABLE", False),
            patch(
                "apple_mail_mcp.tools.inbox.get_mailbox_unread_counts",
                return_value={"Work": 4},
            ),
            patch(
                "apple_mail_mcp.tools.analytics._get_recent_emails_structured_async",
                side_effect=fake_recent,
            ),
        ):
            result = await analytics_tools.inbox_dashboard(
                account="Work",
                max_total=5,
                max_per_account=3,
                output_format="json",
            )

        self.assertEqual(result["account"], "Work")
        self.assertEqual(result["accounts"], {"Work": 4})
        # The synthetic row has no account or Message-ID, so both bounded
        # correlation passes are skipped and their evidence remains unknown.
        self.assertEqual(
            result["recent_emails"],
            [
                {
                    "subject": "Hello",
                    "was_replied_to": False,
                    "mail_was_replied_to": False,
                    "has_sent_reply": None,
                    "reply_state": None,
                    "has_draft": None,
                }
            ],
        )
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["draft_scan"]["status"], "skipped")


class HeavyScanGuardTests(unittest.TestCase):
    def test_overview_flags_skip_mailbox_and_recent_applescript_loops(self):
        script = inbox_tools._build_overview_one_account_script(
            "Work",
            include_mailboxes=False,
            include_recent=False,
        )

        self.assertNotIn("set accountMailboxes to every mailbox", script)
        self.assertNotIn("set recentMessages to", script)
        self.assertNotIn("RECENT|||", script)
        self.assertNotIn("MAILBOX|||", script)
        self.assertIn("HEADER|||", script)

    def test_needs_response_uses_bounded_slice_not_unbounded_whose(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "ok"

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run):
            smart_inbox_tools.get_needs_response(account="Work", days_back=7)

        self.assertIn("set mailboxUpperBound to 25", captured["script"])
        self.assertIn("messages 1 thru mailboxUpperBound of targetMailbox", captured["script"])
        self.assertNotIn("every message of targetMailbox whose", captured["script"])
        self.assertNotIn("set mailboxMessages to messages of targetMailbox", captured["script"])

    def test_needs_response_emits_numeric_and_internet_message_ids_safely(self):
        script = smart_inbox_tools._build_needs_response_inbox_script(
            escaped_account="Work",
            escaped_mailbox="INBOX",
            days_back=7,
            inbox_cap=25,
            max_results=10,
            scan_body=False,
        )

        self.assertIn("set mailAppMessageId to id of aMessage as string", script)
        self.assertIn("set rawMessageId to message id of aMessage", script)
        self.assertIn(
            '"MSG|||" & mailAppMessageId & "|||" & inboxInternetMessageId',
            script,
        )
        self.assertIn('set AppleScript\'s text item delimiters to "|||"', script)
        self.assertIn("set messageSubject to _amm_parts as string", script)
        self.assertIn("set messageSender to _amm_parts as string", script)

    def test_awaiting_reply_uses_bounded_slices_not_unbounded_whose(self):
        captured: dict[str, list[str]] = {"scripts": []}

        def fake_run(script, timeout=120):
            captured["scripts"].append(script)
            return "ok"

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run):
            smart_inbox_tools.get_awaiting_reply(account="Work", days_back=7)

        self.assertEqual(len(captured["scripts"]), 2)
        inbox_script, sent_script = captured["scripts"]
        self.assertIn("set inboxUpperBound to 25", inbox_script)
        self.assertIn("messages 1 thru inboxUpperBound of inboxMailbox", inbox_script)
        self.assertIn("set sentUpperBound to 20", sent_script)
        self.assertIn("messages 1 thru sentUpperBound of sentMailbox", sent_script)
        self.assertNotIn("every message of inboxMailbox whose", inbox_script)
        self.assertNotIn("every message of sentMailbox whose", sent_script)

    def test_statistics_uses_bounded_slices_not_unbounded_date_whose(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "ok"

        with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=fake_run):
            analytics_tools.get_statistics(
                account="Work",
                scope="account_overview",
                days_back=2,
            )

        self.assertIn("set mailboxUpperBound to 50", captured["script"])
        self.assertIn("1 thru 10", captured["script"])
        self.assertIn("messages 1 thru mailboxUpperBound of aMailbox", captured["script"])
        self.assertNotIn("every message of aMailbox whose date received", captured["script"])
        self.assertNotIn("set mailboxMessages to messages of aMailbox", captured["script"])

    def test_save_email_attachment_subject_lookup_returns_deprecation_error(self):
        import json

        home = __import__("os").path.expanduser("~")
        save_path = f"{home}/Downloads/test-file.bin"

        with patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run:
            result = manage_tools.save_email_attachment(
                account="Work",
                subject_keyword="Invoice",
                attachment_name="file.bin",
                save_path=save_path,
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertEqual(payload["code"], "TARGET_SELECTOR_DEPRECATED")
        self.assertEqual(payload["remediation"]["exact_selector"], "message_ids")

    def test_export_single_email_subject_lookup_returns_deprecation_error(self):
        import json

        home = __import__("os").path.expanduser("~")

        with patch("apple_mail_mcp.tools.analytics.run_applescript") as mock_run:
            result = analytics_tools.export_emails(
                account="Work",
                scope="single_email",
                subject_keyword="Invoice",
                save_directory=f"{home}/Downloads",
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertEqual(payload["code"], "TARGET_SELECTOR_DEPRECATED")
        self.assertEqual(payload["remediation"]["exact_selector"], "message_id")


class FixAUnreadCountsExchangeParentTests(unittest.TestCase):
    """Fix A: parent mailbox with unread + children must all appear."""

    def _make_output(self):
        # Simulate Exchange INBOX with 50 own unreads + 3 children with 10 each
        lines = [
            "Exchange|||Inbox|||50",
            "Exchange|||Inbox/Subfolder1|||10",
            "Exchange|||Inbox/Subfolder2|||10",
            "Exchange|||Inbox/Subfolder3|||10",
        ]
        return "\n".join(lines)

    def test_parent_and_children_all_emitted(self):
        """Parent row must appear alongside child rows — 4 distinct keys."""
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=self._make_output(),
        ):
            result = inbox_tools.get_mailbox_unread_counts(
                account="Exchange",
                include_zero=False,
            )

        exchange = result.get("Exchange", {})
        self.assertEqual(len(exchange), 4, f"Expected 4 keys, got {list(exchange.keys())}")
        self.assertEqual(exchange["Inbox"], 50)
        self.assertEqual(exchange["Inbox/Subfolder1"], 10)
        self.assertEqual(exchange["Inbox/Subfolder2"], 10)
        self.assertEqual(exchange["Inbox/Subfolder3"], 10)

    def test_script_emits_parent_row_before_children(self):
        """Generated AppleScript must emit the parent row without the leaf-only guard."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            inbox_tools.get_mailbox_unread_counts(account="Exchange")

        script = captured["script"]
        # The old guard skipped parents; new code should not have it
        self.assertNotIn("(count of subMailboxes) is 0", script)
        # Parent row is emitted before iterating subMailboxes
        self.assertIn("set unreadCount to unread count of aMailbox", script)


class FixBListInboxIdFallbackTests(unittest.IsolatedAsyncioTestCase):
    """Fix B: list_inbox_emails row must survive when id of aMessage fails."""

    def test_script_wraps_id_read_in_try(self):
        """Generated AppleScript must have inner try around id of aMessage."""
        from apple_mail_mcp.tools.inbox import _build_list_inbox_json_script

        script = _build_list_inbox_json_script(
            account="Work",
            max_emails=10,
            read_filter="all",
        )
        self.assertIn("set mailAppId to", script)
        # The id read must be wrapped in its own try block with a fallback
        self.assertIn('set mailAppId to ""', script)
        self.assertIn("try\n                        set mailAppId to id of aMessage", script)

    async def test_row_with_empty_id_is_filtered(self):
        """Parser must drop rows where mail_app_id is empty (Fix 19).

        Empty ids cannot be used for targeted operations and likely result
        from transient sync failures. Rows with a non-empty id are kept.
        """
        # Row with empty id (6th field)
        raw_empty_id = "Hello|||sender@example.com|||Monday, January 1, 2024 at 12:00:00 PM|||false|||Work|||"
        # Row with a valid numeric id
        raw_valid_id = "Hello2|||sender@example.com|||Monday, January 1, 2024 at 12:00:00 PM|||false|||Work|||42"

        def fake_run(script, timeout=120):
            return raw_empty_id + "\n" + raw_valid_id

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            data = await inbox_tools.list_inbox_emails(account="Work", max_emails=5, output_format="json")

        # v3.2.x: JSON path returns a dict with stable shape directly.
        self.assertIsInstance(data, dict)
        emails = data["emails"]
        # Only the row with a non-empty id should survive
        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0]["message_id"], "42")


class FixCAccountOverviewInboxCaseFallbackTests(unittest.TestCase):
    """Fix C: account_overview must try 'Inbox' and 'inbox' when 'INBOX' fails."""

    def test_script_contains_inbox_and_INBOX_lookups(self):
        """Generated AppleScript must attempt both 'INBOX' and 'Inbox' name forms."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=fake_run):
            analytics_tools.get_statistics(
                account="Work",
                scope="account_overview",
                days_back=2,
            )

        script = captured["script"]
        self.assertIn('mailbox "INBOX"', script)
        self.assertIn('mailbox "Inbox"', script)
        # fallback loop uses ignoring case
        self.assertIn("ignoring case", script)


class FixDRepliedIdsSubjectCapTests(unittest.TestCase):
    """Fix D (updated): subject-fallback path is entirely removed from replied_ids_script.

    The original capped the subject path; it is now fully gone. These tests
    assert that the subject path is absent and that only the header-based cap
    constant remains.
    """

    def test_script_contains_header_cap_but_no_subject_cap(self):
        from apple_mail_mcp.core import REPLIED_HEADER_READ_CAP, replied_ids_script

        script = replied_ids_script()
        self.assertIn(f"set headerReadCap to {REPLIED_HEADER_READ_CAP}", script)
        # Subject cap must be completely gone.
        self.assertNotIn("subjectReadCap", script)
        self.assertNotIn("subjectReadCount", script)

    def test_script_has_no_subject_of_sent_message(self):
        from apple_mail_mcp.core import replied_ids_script

        script = replied_ids_script()
        self.assertNotIn("subject of aSentMessage", script)
        self.assertNotIn("sentSubjects", script)

    def test_only_header_cap_constant_exists(self):
        import apple_mail_mcp.core as core_module

        self.assertIsInstance(core_module.REPLIED_HEADER_READ_CAP, int)
        self.assertGreater(core_module.REPLIED_HEADER_READ_CAP, 0)
        self.assertFalse(
            hasattr(core_module, "REPLIED_SUBJECT_READ_CAP"),
            "REPLIED_SUBJECT_READ_CAP must not exist; subject fallback is gone",
        )


class FixEAwaitingReplyTimeoutSubdivisionTests(unittest.TestCase):
    """Fix E: get_awaiting_reply must split timeout between inbox/sent calls."""

    def test_two_different_timeouts_passed(self):
        timeouts = []

        def fake_run(script, timeout=120):
            timeouts.append(timeout)
            return ""

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run):
            smart_inbox_tools.get_awaiting_reply(account="Work", timeout=120)

        self.assertEqual(len(timeouts), 2)
        inbox_t, sent_t = timeouts
        self.assertNotEqual(inbox_t, sent_t, "Timeouts must differ (60/40 split)")
        self.assertLessEqual(inbox_t + sent_t, 120, "Sum must not exceed configured budget")

    def test_timeouts_sum_does_not_exceed_budget(self):
        timeouts = []

        def fake_run(script, timeout=120):
            timeouts.append(timeout)
            return ""

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run):
            smart_inbox_tools.get_awaiting_reply(account="Work", timeout=200)

        self.assertEqual(len(timeouts), 2)
        self.assertLessEqual(sum(timeouts), 200)

    def test_inbox_timeout_named_in_error_on_inbox_fail(self):
        call_count = [0]

        def fake_run(script, timeout=120):
            call_count[0] += 1
            if call_count[0] == 1:
                raise AppleScriptTimeout("inbox slow")
            return ""

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run):
            result = smart_inbox_tools.get_awaiting_reply(account="Work", timeout=120)

        self.assertIn("inbox scan", result.lower())


class FixFListMailboxesCapFencepostTests(unittest.TestCase):
    """Fix F: list_mailboxes must return exactly max_mailboxes rows even when
    AppleScript emits N+1 due to the fence-post in the AppleScript cap_check."""

    def test_python_truncates_over_emission(self):
        """When AppleScript returns N+1 rows and cap=N, result has exactly N."""
        cap = 3
        # Simulate N+1 rows returned by AppleScript
        lines = "\n".join(f"Work|||Box{i}|||Box{i}|||0|||0" for i in range(cap + 1))

        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=lines,
        ):
            raw = inbox_tools.list_mailboxes(
                account="Work",
                output_format="json",
                max_mailboxes=cap,
            )

        import json as _json

        data = _json.loads(raw)
        self.assertEqual(len(data["mailboxes"]), cap)
        self.assertTrue(data["truncated"])
        self.assertEqual(data["returned"], cap)

    def test_exact_cap_rows_still_truncated_flag(self):
        """When AppleScript returns exactly max_mailboxes rows, truncated=True."""
        cap = 2
        lines = "\n".join(f"Work|||Box{i}|||Box{i}|||0|||0" for i in range(cap))

        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=lines,
        ):
            raw = inbox_tools.list_mailboxes(
                account="Work",
                output_format="json",
                max_mailboxes=cap,
            )

        import json as _json

        data = _json.loads(raw)
        self.assertEqual(len(data["mailboxes"]), cap)
        self.assertTrue(data["truncated"])


class FilterScanGateTests(unittest.TestCase):
    """Mutation tools require message_ids unless allow_filter_scan=True."""

    def test_move_email_filter_without_opt_in_returns_target_selector_deprecated(self):
        import json

        with patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run:
            result = manage_tools.move_email(
                account="Work",
                sender="newsletter@example.com",
                to_mailbox="Archive",
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertEqual(payload["code"], "TARGET_SELECTOR_DEPRECATED")
        self.assertIn("message_ids", payload["remediation"]["preferred"])

    def test_move_email_date_filter_with_opt_in_invokes_search(self):
        with (
            patch(
                "apple_mail_mcp.tools.manage._search_mail_records",
                return_value=[],
            ) as mock_search,
            patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run,
        ):
            result = manage_tools.move_email(
                account="Work",
                older_than_days=30,
                to_mailbox="Archive",
                dry_run=True,
                allow_filter_scan=True,
            )

        mock_search.assert_called_once()
        mock_run.assert_not_called()
        self.assertIn("WARNING: filter scan enabled", result)

    def test_move_email_message_ids_ignores_filter_gate(self):
        with patch(
            "apple_mail_mcp.tools.manage.run_applescript",
            return_value="MOVING EMAILS BY IDS",
        ) as mock_run:
            result = manage_tools.move_email(
                account="Work",
                to_mailbox="Archive",
                message_ids=["99"],
                dry_run=True,
            )

        mock_run.assert_called_once()
        self.assertNotIn("FILTER_SCAN_DISABLED", result)

    def test_update_email_status_filter_without_opt_in_returns_target_selector_deprecated(self):
        import json

        with patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run:
            result = manage_tools.update_email_status(
                account="Work",
                action="mark_read",
                subject_keyword="Newsletter",
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertEqual(payload["code"], "TARGET_SELECTOR_DEPRECATED")

    def test_manage_trash_filter_without_opt_in_returns_target_selector_deprecated(self):
        import json

        with patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run:
            result = manage_tools.manage_trash(
                account="Work",
                action="move_to_trash",
                sender="spam@example.com",
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertEqual(payload["code"], "TARGET_SELECTOR_DEPRECATED")


class MoveEmailUnboundedScanGuardTests(unittest.TestCase):
    """Fix #3: move_email must refuse recent_days=0 without older_than_days."""

    def test_move_email_recent_days_zero_returns_unbounded_scan_error(self):
        """recent_days=0, no older_than_days, no message_ids -> UNBOUNDED_SCAN_REQUIRED."""
        import json

        with patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run:
            result = manage_tools.move_email(
                account="Work",
                sender="newsletter@example.com",
                to_mailbox="Archive",
                recent_days=0,
                allow_filter_scan=True,
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertTrue(payload.get("error"))
        self.assertEqual(payload["code"], "TARGET_SELECTOR_DEPRECATED")
        self.assertEqual(payload["remediation"]["exact_selector"], "message_ids")

    def test_move_email_recent_days_zero_with_older_than_days_is_allowed(self):
        """older_than_days overrides the recent_days window — should proceed."""
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return ""

        with (
            patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run),
            patch(
                "apple_mail_mcp.tools.manage._search_mail_records",
                return_value=[],
            ),
        ):
            result = manage_tools.move_email(
                account="Work",
                to_mailbox="Archive",
                recent_days=0,
                older_than_days=30,
                allow_filter_scan=True,
            )

        # Should NOT return a structured error
        import json

        try:
            payload = json.loads(result)
            self.assertNotEqual(payload.get("code"), "UNBOUNDED_SCAN_REQUIRED")
        except (json.JSONDecodeError, TypeError):
            pass  # Plain text response is fine — no structured error

    def test_move_email_message_ids_skips_unbounded_guard(self):
        """message_ids path is always safe — no UNBOUNDED_SCAN_REQUIRED."""
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "MOVING EMAILS BY IDS: INBOX -> Archive\n\nTOTAL: 0 email(s) moved"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            result = manage_tools.move_email(
                account="Work",
                to_mailbox="Archive",
                message_ids=["42"],
                recent_days=0,
            )

        import json

        try:
            payload = json.loads(result)
            self.assertNotEqual(payload.get("code"), "UNBOUNDED_SCAN_REQUIRED")
        except (json.JSONDecodeError, TypeError):
            pass  # Plain text result is fine


class ManageTrashUnboundedScanGuardTests(unittest.TestCase):
    """Fix #3: manage_trash must refuse recent_days=0 without older_than_days."""

    def test_manage_trash_move_to_trash_recent_days_zero_returns_error(self):
        """move_to_trash with recent_days=0, no older_than_days -> UNBOUNDED_SCAN_REQUIRED."""
        import json

        with patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run:
            result = manage_tools.manage_trash(
                account="Work",
                action="move_to_trash",
                apply_to_all=True,
                recent_days=0,
                allow_filter_scan=True,
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertTrue(payload.get("error"))
        self.assertEqual(payload["code"], "UNBOUNDED_SCAN_REQUIRED")
        self.assertIn("recent_days=7", payload["remediation"]["preferred"])
        self.assertNotIn("full_inbox_export", str(payload["remediation"]))

    def test_manage_trash_delete_permanent_recent_days_zero_returns_error(self):
        """delete_permanent with recent_days=0, no older_than_days -> UNBOUNDED_SCAN_REQUIRED."""
        import json

        with patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run:
            result = manage_tools.manage_trash(
                account="Work",
                action="delete_permanent",
                apply_to_all=True,
                recent_days=0,
                allow_filter_scan=True,
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertTrue(payload.get("error"))
        self.assertEqual(payload["code"], "UNBOUNDED_SCAN_REQUIRED")

    def test_manage_trash_empty_trash_exempt_from_unbounded_guard(self):
        """empty_trash does not use recent_days scan — guard must not fire."""
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "EMPTYING TRASH\n\n✓ Emptied trash for account: Work"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            result = manage_tools.manage_trash(
                account="Work",
                action="empty_trash",
                confirm_empty=True,
                recent_days=0,
            )

        import json

        try:
            payload = json.loads(result)
            self.assertNotEqual(payload.get("code"), "UNBOUNDED_SCAN_REQUIRED")
        except (json.JSONDecodeError, TypeError):
            pass  # Plain text result is fine

    def test_manage_trash_message_ids_skips_unbounded_guard(self):
        """message_ids path is always safe — no UNBOUNDED_SCAN_REQUIRED."""
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "MOVING EMAILS TO TRASH BY IDS\n\nTOTAL: 0 email(s) moved to trash"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            result = manage_tools.manage_trash(
                account="Work",
                action="move_to_trash",
                message_ids=["42"],
                recent_days=0,
            )

        import json

        try:
            payload = json.loads(result)
            self.assertNotEqual(payload.get("code"), "UNBOUNDED_SCAN_REQUIRED")
        except (json.JSONDecodeError, TypeError):
            pass  # Plain text result is fine

    def test_manage_trash_older_than_days_allows_zero_recent_days(self):
        """older_than_days overrides recent_days window — should proceed."""
        with patch(
            "apple_mail_mcp.tools.manage._search_mail_records",
            return_value=[],
        ):
            result = manage_tools.manage_trash(
                account="Work",
                action="move_to_trash",
                recent_days=0,
                older_than_days=30,
                dry_run=True,
                allow_filter_scan=True,
            )

        import json

        try:
            payload = json.loads(result)
            self.assertNotEqual(payload.get("code"), "UNBOUNDED_SCAN_REQUIRED")
        except (json.JSONDecodeError, TypeError):
            pass  # Plain text dry-run result is fine


class AnalyticsMessageIdPathTests(unittest.TestCase):
    def test_list_email_attachments_message_ids_uses_exact_id_condition(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "ATTACHMENTS FOR: message_ids: 777"

        with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=fake_run):
            result = analytics_tools.list_email_attachments(
                account="Work",
                message_ids=["777"],
            )

        self.assertIn("id is 777", captured["script"])
        self.assertNotIn("subject contains", captured["script"])
        self.assertIn("ATTACHMENTS FOR", result)

    def test_list_email_attachments_subject_lookup_returns_deprecation_error(self):
        with patch("apple_mail_mcp.tools.analytics.run_applescript") as mock_run:
            result = analytics_tools.list_email_attachments(
                account="Work",
                subject_keyword="Invoice",
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertEqual(payload["code"], "TARGET_SELECTOR_DEPRECATED")
        self.assertEqual(payload["remediation"]["exact_selector"], "message_ids")

    def test_list_email_attachments_json_returns_exact_attachment_selectors(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "\n".join(
                [
                    "777|||Invoice|||sender@example.com|||2026-06-29|||1|||report.pdf|||2048",
                    "777|||Invoice|||sender@example.com|||2026-06-29|||2|||data.csv|||",
                ]
            )

        with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=fake_run):
            result = analytics_tools.list_email_attachments(
                account="Work",
                message_ids=["777"],
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["returned"], 2)
        self.assertEqual(payload["selector"], "message_ids")
        self.assertEqual(payload["items"][0]["message_id"], "777")
        self.assertEqual(payload["items"][0]["attachment_index"], 1)
        self.assertEqual(payload["items"][0]["filename"], "report.pdf")
        self.assertEqual(payload["items"][0]["size_bytes"], 2048)
        self.assertIsNone(payload["items"][1]["size_bytes"])
        self.assertIn("id of aMessage as string", captured["script"])
        self.assertIn("attachmentIndex from 1 to attachmentCount", captured["script"])
        result_count_init_pos = captured["script"].find("set resultCount to 0")
        result_count_check_pos = captured["script"].find("if resultCount >=")
        self.assertGreaterEqual(result_count_init_pos, 0)
        self.assertGreater(result_count_check_pos, result_count_init_pos)

    def test_list_email_attachments_chunks_51_message_ids_json(self):
        captured: list[str] = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "id is 51" in script:
                return "51|||Second chunk|||sender@example.com|||2026-06-29|||1|||second.pdf|||2048"
            return "1|||First chunk|||sender@example.com|||2026-06-29|||1|||first.pdf|||1024"

        ids = [str(i) for i in range(1, 52)]
        with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=fake_run):
            result = analytics_tools.list_email_attachments(
                account="Work",
                message_ids=ids,
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["message_ids"], ids)
        self.assertEqual(payload["chunk_size"], 50)
        self.assertEqual(payload["returned"], 2)
        self.assertEqual([item["message_id"] for item in payload["items"]], ["1", "51"])
        self.assertEqual(len(captured), 2)
        self.assertIn("id is 1", captured[0])
        self.assertIn("id is 50", captured[0])
        self.assertNotIn("id is 51", captured[0])
        self.assertIn("id is 51", captured[1])

    def test_list_email_attachments_chunks_120_message_ids_json(self):
        captured: list[str] = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return ""

        ids = [str(i) for i in range(1, 121)]
        with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=fake_run):
            result = analytics_tools.list_email_attachments(
                account="Work",
                message_ids=ids,
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["message_ids"], ids)
        self.assertEqual(payload["returned"], 0)
        self.assertEqual(len(captured), 3)
        self.assertIn("id is 1", captured[0])
        self.assertIn("id is 51", captured[1])
        self.assertIn("id is 101", captured[2])

    def test_list_email_attachments_rejects_invalid_output_format(self):
        result = analytics_tools.list_email_attachments(
            account="Work",
            message_ids=["777"],
            output_format="xml",
        )

        self.assertIn("Invalid output_format", result)

    def test_save_email_attachment_with_attachment_index_uses_exact_index(self):
        captured: list[str] = []
        home = __import__("os").path.expanduser("~")
        save_path = f"{home}/Downloads/test-file.bin"

        def fake_run(script, timeout=120):
            captured.append(script)
            if "file size of anAttachment" in script:
                return "1|||-1"
            return "saved by index"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            result = manage_tools.save_email_attachment(
                account="Work",
                message_ids=["777"],
                attachment_index=2,
                save_path=save_path,
            )

        self.assertEqual(result, "saved by index")
        self.assertIn("attachmentLoopIndex is 2", captured[-1])
        self.assertIn("id is 777", captured[-1])
        self.assertNotIn("subject contains", captured[-1])

    def test_save_email_attachment_index_requires_single_message_id(self):
        home = __import__("os").path.expanduser("~")
        save_path = f"{home}/Downloads/test-file.bin"

        with patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run:
            result = manage_tools.save_email_attachment(
                account="Work",
                message_ids=["777", "778"],
                attachment_index=1,
                save_path=save_path,
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertEqual(payload["code"], "AMBIGUOUS_ATTACHMENT_SELECTOR")
        self.assertEqual(payload["remediation"]["exact_selector"], "message_ids + attachment_index")

    def test_save_email_attachment_duplicate_name_requires_attachment_index(self):
        home = __import__("os").path.expanduser("~")
        save_path = f"{home}/Downloads/test-file.bin"

        def fake_run(script, timeout=120):
            if "file size of anAttachment" in script:
                return "2|||1024"
            return "saved"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            result = manage_tools.save_email_attachment(
                account="Work",
                message_ids=["777"],
                attachment_name="report",
                save_path=save_path,
            )

        payload = json.loads(result)
        self.assertEqual(payload["code"], "AMBIGUOUS_ATTACHMENT_SELECTOR")
        self.assertEqual(payload["remediation"]["matches"], 2)

    def test_export_emails_single_email_message_id_skips_subject_search(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "EXPORTING EMAIL"

        with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=fake_run):
            result = analytics_tools.export_emails(
                account="Work",
                scope="single_email",
                message_id="888",
                save_directory="~/Desktop",
            )

        self.assertIn("whose id is 888", captured["script"])
        self.assertIn("EXPORTING EMAIL", result)

    def test_export_emails_message_ids_use_exact_batch_path_with_default_scope(self):
        captured: list[str] = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "EXPORTING MESSAGES BY ID\n\nExported: 2"

        with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=fake_run):
            result = analytics_tools.export_emails(
                account="Work",
                message_ids=["101", "bad", "202", "101"],
                save_directory="~/Desktop",
            )

        self.assertEqual(len(captured), 1)
        self.assertIn("set requestedIds to {101, 202}", captured[0])
        self.assertIn("whose id is requestedId", captured[0])
        self.assertIn("message_id_export", captured[0])
        self.assertNotIn("subject_keyword", captured[0])
        self.assertNotIn("messages 1 thru", captured[0])
        self.assertIn("EXPORTING MESSAGES BY ID", result)
        self.assertIn("Ignored invalid message_ids: bad", result)

    def test_export_emails_message_ids_over_50_rejected_without_applescript(self):
        """v3.9.3 bounded batches: >50 explicit ids refuse up front, no Mail work."""
        ids = [str(i) for i in range(1, 121)]
        with patch("apple_mail_mcp.tools.analytics.run_applescript") as mock_run:
            result = analytics_tools.export_emails(
                account="Work",
                message_ids=ids,
                save_directory="~/Desktop",
            )

        mock_run.assert_not_called()
        self.assertIn("message_ids is limited to 50", result)

    def test_export_emails_message_ids_50_runs_single_bounded_call(self):
        """The cap boundary (50 ids) runs as one bounded AppleScript call."""
        captured: list[str] = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "EXPORTING MESSAGES BY ID\n\nExported: 0"

        ids = [str(i) for i in range(1, 51)]
        with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=fake_run):
            analytics_tools.export_emails(
                account="Work",
                message_ids=ids,
                save_directory="~/Desktop",
            )

        self.assertEqual(len(captured), 1)
        self.assertIn("set requestedIds to {1, 2", captured[0])
        self.assertIn("50}", captured[0])

    def test_export_emails_message_ids_rejects_invalid_only_without_applescript(self):
        with patch("apple_mail_mcp.tools.analytics.run_applescript") as mock_run:
            result = analytics_tools.export_emails(
                account="Work",
                message_ids=["bad"],
                save_directory="~/Desktop",
            )

        mock_run.assert_not_called()
        self.assertIn("'message_ids' must contain one or more numeric", result)


if __name__ == "__main__":
    unittest.main()
