"""Integration tests for ``inbox_dashboard`` reply-state annotation.

Covers the 2026-07-10 reply-state-annotation rework
(``tasks/active/reply-state-annotation/plan-2026-07-10.md``): recent-email
rows always carry native ``was_replied_to``; ``has_draft``/top-level
``draft_scan`` are JSON-only and governed by ``include_draft_state``. Mocks
``apple_mail_mcp.tools.analytics.run_applescript`` (the same seam
``fetch_drafts_snapshot`` reaches via ``analytics.run_applescript``).
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import analytics as analytics_tools


def _run(coro):
    return asyncio.run(coro)


def _recent_row(subject: str, was_replied: str, account: str = "Work") -> str:
    return f"{subject}|||alice@example.com|||Date|||false|||{account}|||INBOX|||101|||<id@x.com>|||{was_replied}|||"


def _fake_runner(recent_raw: str, draft_raw: str | None = None):
    def runner(script: str, timeout: int | None = None) -> str:
        if "draftsMailbox" in script:
            return draft_raw if draft_raw is not None else "COUNT|||0\nTOTAL|||0"
        if "sentMailbox" in script:
            return "SCANNED|||0\nTOTAL|||0"
        return recent_raw

    return runner


class DashboardWasRepliedTests(unittest.TestCase):
    def test_json_recent_emails_carry_was_replied_to(self):
        with (
            patch("apple_mail_mcp.tools.inbox.get_mailbox_unread_counts", return_value={"Work": 1}),
            patch(
                "apple_mail_mcp.tools.analytics.run_applescript",
                side_effect=_fake_runner(_recent_row("Budget", "true")),
            ),
        ):
            result = _run(
                analytics_tools.inbox_dashboard(account="Work", output_format="json", include_draft_state=False)
            )

        row = result["recent_emails"][0]
        self.assertTrue(row["was_replied_to"])
        self.assertIsNone(row["has_draft"])
        self.assertEqual(result["draft_scan"]["status"], "skipped")


class DashboardHasDraftTests(unittest.TestCase):
    def test_json_has_draft_true_for_matching_draft(self):
        draft_raw = "DRAFT|||Re: Budget|||alice@example.com|||2026-07-09T10:00:00|||\nCOUNT|||1\nTOTAL|||1"
        with (
            patch("apple_mail_mcp.tools.inbox.get_mailbox_unread_counts", return_value={"Work": 1}),
            patch(
                "apple_mail_mcp.tools.analytics.run_applescript",
                side_effect=_fake_runner(_recent_row("Budget", "false"), draft_raw),
            ),
        ):
            result = _run(analytics_tools.inbox_dashboard(account="Work", output_format="json"))

        row = result["recent_emails"][0]
        self.assertTrue(row["has_draft"])
        self.assertEqual(result["draft_scan"]["status"], "ok")

    def test_draft_scan_error_yields_null_has_draft(self):
        with (
            patch("apple_mail_mcp.tools.inbox.get_mailbox_unread_counts", return_value={"Work": 1}),
            patch(
                "apple_mail_mcp.tools.analytics.run_applescript",
                side_effect=_fake_runner(_recent_row("Budget", "false"), "ERROR|||boom"),
            ),
        ):
            result = _run(analytics_tools.inbox_dashboard(account="Work", output_format="json"))

        row = result["recent_emails"][0]
        self.assertIsNone(row["has_draft"])
        self.assertEqual(result["draft_scan"]["status"], "error")

    def test_include_draft_state_false_skips_scan_call(self):
        calls: list[str] = []

        def runner(script: str, timeout: int | None = None) -> str:
            calls.append(script)
            if "sentMailbox" in script:
                return "SCANNED|||0\nTOTAL|||0"
            return _recent_row("Budget", "false")

        with (
            patch("apple_mail_mcp.tools.inbox.get_mailbox_unread_counts", return_value={"Work": 1}),
            patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=runner),
        ):
            result = _run(
                analytics_tools.inbox_dashboard(account="Work", output_format="json", include_draft_state=False)
            )

        self.assertEqual(len(calls), 2, "only recent and Sent-reply scans should run; no Drafts scan")
        self.assertEqual(result["draft_scan"]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
