"""Integration tests for ``list_inbox_emails`` reply-state annotation.

Covers the 2026-07-10 reply-state-annotation rework
(``tasks/active/reply-state-annotation/plan-2026-07-10.md``): native
``was_replied_to`` (always present, JSON + text), ``has_draft``/
``draft_scan`` (governed by ``include_draft_state``), ``exclude_drafted``,
and the multi-account Drafts-snapshot fan-out cap. Mocks
``apple_mail_mcp.tools.inbox.run_applescript`` (the same seam
``fetch_drafts_snapshot`` reaches via ``inbox.run_applescript``), so no real
Mail.app runs. ``exclude_replied``/``flag_replied`` native-flag coverage
already lives in
``tests/cross_cutting/test_replied_detection.py::ListInboxRepliedTests``.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import inbox as inbox_tools


def _run(coro):
    return asyncio.run(coro)


def _fake_runner(main_raw: str, draft_raw: str | None = None, calls: list[str] | None = None):
    """Route a single account's inbox script vs its Drafts-snapshot script."""

    def runner(script: str, timeout: int | None = None) -> str:
        if calls is not None:
            calls.append(script)
        if "draftsMailbox" in script:
            return draft_raw if draft_raw is not None else "COUNT|||0\nTOTAL|||0"
        if "sentMailbox" in script:
            return "SCANNED|||0\nTOTAL|||0"
        return main_raw

    return runner


class ListInboxWasRepliedAlwaysPresentTests(unittest.TestCase):
    def test_json_rows_always_carry_was_replied_to(self):
        raw = (
            "S1|||a@example.com|||Date|||false|||Work|||101|||true\n"
            "S2|||b@example.com|||Date|||false|||Work|||102|||false"
        )
        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=_fake_runner(raw)):
            response = _run(
                inbox_tools.list_inbox_emails(
                    account="Work", max_emails=10, output_format="json", include_draft_state=False
                )
            )

        was_replied = {e["subject"]: e["was_replied_to"] for e in response["emails"]}
        self.assertEqual(was_replied, {"S1": True, "S2": False})
        # has_draft is still present (null) even with the scan skipped.
        self.assertTrue(all(e["has_draft"] is None for e in response["emails"]))
        self.assertEqual(response["draft_scan"]["status"], "skipped")

    def test_json_sent_header_match_sets_composite_reply_state(self):
        raw = "Budget|||alice@example.com|||Date|||false|||Work|||101|||false|||<budget@example.com>"

        def runner(script: str, timeout: int | None = None) -> str:
            if "sentMailbox" in script:
                return "REPLY|||<budget@example.com>\nSCANNED|||1\nTOTAL|||1"
            return raw

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=runner):
            response = _run(
                inbox_tools.list_inbox_emails(
                    account="Work", max_emails=10, output_format="json", include_draft_state=False
                )
            )

        row = response["emails"][0]
        self.assertFalse(row["mail_was_replied_to"])
        self.assertTrue(row["has_sent_reply"])
        self.assertTrue(row["reply_state"])
        self.assertEqual(response["sent_reply_scan"]["status"], "ok")

    def test_text_mode_tags_replied_rows(self):
        # Matches the real `_build_list_inbox_text_script` per-message
        # render: a `__ROW__` marker line ahead of the ✉/✓ display block.
        raw = (
            "__ROW__|||S1|||a@example.com|||Date|||<id1@x.com>|||true\n"
            "✉ S1\n"
            "   From: a@example.com\n"
            "   Date: Date\n"
            "\n"
            "__ROW__|||S2|||b@example.com|||Date|||<id2@x.com>|||false\n"
            "✉ S2\n"
            "   From: b@example.com\n"
            "   Date: Date\n"
            "\n"
            "__COUNT__|||2\n"
        )
        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=_fake_runner(raw)):
            result = _run(
                inbox_tools.list_inbox_emails(
                    account="Work", max_emails=10, output_format="text", include_draft_state=False
                )
            )

        self.assertIn("[REPLIED]", result)
        lines = result.splitlines()
        s1_line = next(line for line in lines if "S1" in line)
        s2_line = next(line for line in lines if "S2" in line)
        self.assertIn("[REPLIED]", s1_line)
        self.assertNotIn("[REPLIED]", s2_line)


class ListInboxHasDraftTests(unittest.TestCase):
    def test_json_has_draft_true_for_matching_draft(self):
        main_raw = "Budget|||alice@example.com|||Date|||false|||Work|||101|||false|||<budget@example.com>"
        draft_raw = "DRAFT|||Re: Budget|||alice@example.com|||2026-07-09T10:00:00|||\nCOUNT|||1\nTOTAL|||1"
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            side_effect=_fake_runner(main_raw, draft_raw),
        ):
            response = _run(inbox_tools.list_inbox_emails(account="Work", max_emails=10, output_format="json"))

        self.assertTrue(response["emails"][0]["has_draft"])
        self.assertEqual(response["draft_scan"]["status"], "ok")

    def test_text_mode_tags_has_draft(self):
        main_raw = (
            "__ROW__|||Budget|||alice@example.com|||Date|||<id@x.com>|||false\n"
            "✉ Budget\n"
            "   From: alice@example.com\n"
            "   Date: Date\n"
            "\n"
            "__COUNT__|||1\n"
        )
        draft_raw = "DRAFT|||Re: Budget|||alice@example.com|||2026-07-09T10:00:00|||\nCOUNT|||1\nTOTAL|||1"
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            side_effect=_fake_runner(main_raw, draft_raw),
        ):
            result = _run(inbox_tools.list_inbox_emails(account="Work", max_emails=10, output_format="text"))

        self.assertIn("[HAS DRAFT]", result)

    def test_exclude_drafted_removes_matching_row_json(self):
        main_raw = (
            "Budget|||alice@example.com|||Date|||false|||Work|||101|||false\n"
            "Other|||bob@example.com|||Date|||false|||Work|||102|||false"
        )
        draft_raw = "DRAFT|||Re: Budget|||alice@example.com|||2026-07-09T10:00:00|||\nCOUNT|||1\nTOTAL|||1"
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            side_effect=_fake_runner(main_raw, draft_raw),
        ):
            response = _run(
                inbox_tools.list_inbox_emails(account="Work", max_emails=10, output_format="json", exclude_drafted=True)
            )

        subjects = [e["subject"] for e in response["emails"]]
        self.assertEqual(subjects, ["Other"])

    def test_include_draft_state_false_skips_scan_entirely(self):
        main_raw = "Budget|||alice@example.com|||Date|||false|||Work|||101|||false|||<budget@example.com>"
        calls: list[str] = []
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            side_effect=_fake_runner(main_raw, calls=calls),
        ):
            response = _run(
                inbox_tools.list_inbox_emails(
                    account="Work", max_emails=10, output_format="json", include_draft_state=False
                )
            )

        self.assertEqual(len(calls), 2, "only inbox and Sent-reply scans should run; no Drafts scan")
        self.assertIsNone(response["emails"][0]["has_draft"])
        self.assertEqual(response["draft_scan"]["status"], "skipped")
        # exclude_drafted never excludes on a null has_draft.
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            side_effect=_fake_runner(main_raw),
        ):
            response2 = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=10,
                    output_format="json",
                    include_draft_state=False,
                    exclude_drafted=True,
                )
            )
        self.assertEqual(len(response2["emails"]), 1)

    def test_draft_scan_error_sets_null_and_excludes_nothing(self):
        main_raw = (
            "Budget|||alice@example.com|||Date|||false|||Work|||101|||false\n"
            "Other|||bob@example.com|||Date|||false|||Work|||102|||false"
        )
        draft_raw = "ERROR|||Could not find Drafts mailbox"
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            side_effect=_fake_runner(main_raw, draft_raw),
        ):
            response = _run(
                inbox_tools.list_inbox_emails(account="Work", max_emails=10, output_format="json", exclude_drafted=True)
            )

        self.assertEqual(len(response["emails"]), 2)
        self.assertTrue(all(e["has_draft"] is None for e in response["emails"]))
        self.assertEqual(response["draft_scan"]["status"], "error")


class ListInboxMultiAccountDraftCapTests(unittest.TestCase):
    def test_lazy_snapshot_fetch_capped_at_five_accounts(self):
        accounts = [f"Acct{i}" for i in range(6)]
        draft_calls: list[str] = []

        def fake_run(script: str, timeout: int | None = None) -> str:
            if "draftsMailbox" in script:
                draft_calls.append(script)
                return "COUNT|||0\nTOTAL|||0"
            for acct in accounts:
                if f'account "{acct}"' in script:
                    return f"Subj|||sender@x.com|||Date|||false|||{acct}|||1|||false"
            return ""

        with (
            patch("apple_mail_mcp.tools.inbox._list_mail_accounts", return_value=accounts),
            patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run),
        ):
            response = _run(inbox_tools.list_inbox_emails(account=None, max_emails=10, output_format="json"))

        # Exactly 5 distinct accounts scanned, never one per email.
        scanned_accounts = {c for c in draft_calls if c}
        self.assertLessEqual(len(scanned_accounts), 5)
        self.assertEqual(len(draft_calls), 5)

        has_draft_by_account = {e["account"]: e["has_draft"] for e in response["emails"]}
        null_accounts = [acct for acct, val in has_draft_by_account.items() if val is None]
        self.assertEqual(len(null_accounts), 1, "exactly one account must fall outside the cap")


if __name__ == "__main__":
    unittest.main()
