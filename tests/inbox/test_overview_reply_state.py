"""Integration tests for ``get_inbox_overview`` reply-state annotation.

Covers the 2026-07-10 reply-state-annotation rework
(``tasks/active/reply-state-annotation/plan-2026-07-10.md``): RECENT rows
always carry native ``was_replied_to``; ``has_draft``/top-level
``draft_scan`` are governed by ``include_draft_state``. Mocks
``apple_mail_mcp.tools.inbox.run_applescript`` (the same seam
``fetch_drafts_snapshot`` reaches via ``inbox.run_applescript``).
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import inbox as inbox_tools


def _run(coro):
    return asyncio.run(coro)


def _overview_payload(replied_token: str) -> str:
    return "\n".join(
        [
            "HEADER|||Work|||1|||10",
            f"RECENT|||Budget|||alice@example.com|||Fri, May 23, 2026|||false|||{replied_token}|||<budget@example.com>",
        ]
    )


def _fake_runner(overview_raw: str, draft_raw: str | None = None):
    def runner(script: str, timeout: int | None = None) -> str:
        if "draftsMailbox" in script:
            return draft_raw if draft_raw is not None else "COUNT|||0\nTOTAL|||0"
        if "sentMailbox" in script:
            return "SCANNED|||0\nTOTAL|||0"
        return overview_raw

    return runner


class OverviewWasRepliedTests(unittest.TestCase):
    def test_json_recent_rows_carry_was_replied_to(self):
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            side_effect=_fake_runner(_overview_payload("true")),
        ):
            result = _run(
                inbox_tools.get_inbox_overview(account="Work", output_format="json", include_draft_state=False)
            )

        recent = result["accounts"][0]["recent"][0]
        self.assertTrue(recent["was_replied_to"])
        self.assertIsNone(recent["has_draft"])
        self.assertEqual(result["draft_scan"]["status"], "skipped")

    def test_json_sent_header_match_sets_composite_reply_state(self):
        def runner(script: str, timeout: int | None = None) -> str:
            if "sentMailbox" in script:
                return "REPLY|||<budget@example.com>\nSCANNED|||1\nTOTAL|||1"
            return _overview_payload("false")

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=runner):
            result = _run(
                inbox_tools.get_inbox_overview(account="Work", output_format="json", include_draft_state=False)
            )

        recent = result["accounts"][0]["recent"][0]
        self.assertFalse(recent["mail_was_replied_to"])
        self.assertTrue(recent["has_sent_reply"])
        self.assertTrue(recent["reply_state"])
        self.assertEqual(result["sent_reply_scan"]["status"], "ok")

    def test_text_mode_tags_replied_recent_line(self):
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            side_effect=_fake_runner(_overview_payload("true")),
        ):
            result = _run(
                inbox_tools.get_inbox_overview(account="Work", output_format="text", include_draft_state=False)
            )

        self.assertIn("[REPLIED]", result)

    def test_text_mode_no_tag_when_not_replied(self):
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            side_effect=_fake_runner(_overview_payload("false")),
        ):
            result = _run(
                inbox_tools.get_inbox_overview(account="Work", output_format="text", include_draft_state=False)
            )

        self.assertNotIn("[REPLIED]", result)

    def test_text_mode_does_not_scan_sent_headers(self):
        calls: list[str] = []

        def runner(script: str, timeout: int | None = None) -> str:
            calls.append(script)
            return _overview_payload("false")

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=runner):
            _run(inbox_tools.get_inbox_overview(account="Work", output_format="text", include_draft_state=False))

        self.assertEqual(len(calls), 1)
        self.assertNotIn("sentMailbox", calls[0])


class OverviewHasDraftTests(unittest.TestCase):
    def test_json_has_draft_true_for_matching_draft(self):
        draft_raw = "DRAFT|||Re: Budget|||alice@example.com|||2026-07-09T10:00:00|||\nCOUNT|||1\nTOTAL|||1"
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            side_effect=_fake_runner(_overview_payload("false"), draft_raw),
        ):
            result = _run(inbox_tools.get_inbox_overview(account="Work", output_format="json"))

        recent = result["accounts"][0]["recent"][0]
        self.assertTrue(recent["has_draft"])
        self.assertEqual(result["draft_scan"]["status"], "ok")

    def test_text_mode_tags_has_draft(self):
        draft_raw = "DRAFT|||Re: Budget|||alice@example.com|||2026-07-09T10:00:00|||\nCOUNT|||1\nTOTAL|||1"
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            side_effect=_fake_runner(_overview_payload("false"), draft_raw),
        ):
            result = _run(inbox_tools.get_inbox_overview(account="Work", output_format="text"))

        self.assertIn("[HAS DRAFT]", result)

    def test_draft_scan_error_yields_null_has_draft(self):
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            side_effect=_fake_runner(_overview_payload("false"), "ERROR|||boom"),
        ):
            result = _run(inbox_tools.get_inbox_overview(account="Work", output_format="json"))

        recent = result["accounts"][0]["recent"][0]
        self.assertIsNone(recent["has_draft"])
        self.assertEqual(result["draft_scan"]["status"], "error")

    def test_include_draft_state_false_skips_scan_call(self):
        calls: list[str] = []

        def runner(script: str, timeout: int | None = None) -> str:
            calls.append(script)
            if "sentMailbox" in script:
                return "SCANNED|||0\nTOTAL|||0"
            return _overview_payload("false")

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=runner):
            result = _run(
                inbox_tools.get_inbox_overview(account="Work", output_format="json", include_draft_state=False)
            )

        self.assertEqual(len(calls), 2, "only overview and Sent-reply scans should run; no Drafts scan")
        self.assertEqual(result["draft_scan"]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
