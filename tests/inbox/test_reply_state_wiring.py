"""Tests for ``apple_mail_mcp.tools.reply_state_wiring``.

Covers the generic ``has_draft`` correlation + ``draft_scan`` aggregation
helper shared by ``inbox/list_emails.py``, ``inbox/overview.py``, and
``analytics/dashboard.py`` (see
``tasks/active/reply-state-annotation/plan-2026-07-10.md``). Mocks only the
injectable ``runner`` seam (``core.reply_state.fetch_drafts_snapshot``'s
``AppleScriptRunner`` parameter); no real Mail.app.
"""

from __future__ import annotations

import unittest

from apple_mail_mcp.core.replied import SentReplySnapshot
from apple_mail_mcp.core.reply_state import DraftsSnapshot
from apple_mail_mcp.tools.reply_state_wiring import (
    MAX_DRAFT_SNAPSHOT_ACCOUNTS,
    MAX_SENT_REPLY_SNAPSHOT_ACCOUNTS,
    annotate_rows_with_reply_state,
    build_draft_scan_status,
    build_sent_reply_scan_status,
)


def _drafts_runner(responses: dict[str, str], calls: list[str] | None = None):
    """Return a fake AppleScriptRunner keyed by ``account "<name>"`` substring."""

    def runner(script: str, timeout: int | None = 60) -> str:
        if calls is not None:
            calls.append(script)
        for account, raw in responses.items():
            if f'account "{account}"' in script:
                return raw
        return "COUNT|||0\nTOTAL|||0"

    return runner


class BuildDraftScanStatusTests(unittest.TestCase):
    def test_empty_snapshots_is_skipped(self):
        # The empty-snapshots early return carries the same uniform envelope
        # keys (total, truncated) as the non-empty path, so every draft_scan
        # producer emits an identical key set.
        status = build_draft_scan_status({})
        self.assertEqual(
            status,
            {"status": "skipped", "scanned": 0, "total": 0, "truncated": False, "accounts": []},
        )

    def test_all_ok_snapshots_aggregate_scanned_counts(self):
        snapshots = {
            "Work": DraftsSnapshot(status="ok", scanned=3, account="Work"),
            "Personal": DraftsSnapshot(status="ok", scanned=2, account="Personal"),
        }
        status = build_draft_scan_status(snapshots)
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["scanned"], 5)
        self.assertEqual(len(status["accounts"]), 2)
        self.assertNotIn("error", status)

    def test_one_errored_account_flips_status_to_error(self):
        snapshots = {
            "Work": DraftsSnapshot(status="ok", scanned=3, account="Work"),
            "Slow": DraftsSnapshot(status="error", scanned=0, account="Slow", error="timeout: 60s"),
        }
        status = build_draft_scan_status(snapshots)
        self.assertEqual(status["status"], "error")
        self.assertIn("Slow", status["error"])
        self.assertIn("timeout", status["error"])

    def test_skipped_account_snapshot_does_not_set_error_message(self):
        snapshots = {"Work": DraftsSnapshot(status="skipped", scanned=0, account="Work")}
        status = build_draft_scan_status(snapshots)
        self.assertEqual(status["status"], "error")  # not "ok": not every account is "ok"
        self.assertNotIn("error", status)  # skipped carries no .error message to fold in


class AnnotateRowsWithReplyStateTests(unittest.TestCase):
    def test_composite_reply_state_truth_table(self):
        rows = [
            {"account": "Work", "internet_message_id": "<native@example.com>", "was_replied_to": True},
            {"account": "Work", "internet_message_id": "<header@example.com>", "was_replied_to": False},
            {"account": "Work", "internet_message_id": "<untouched@example.com>", "was_replied_to": False},
        ]
        sent_cache = {
            "Work": SentReplySnapshot(
                status="ok",
                replied_ids=frozenset({"<header@example.com>"}),
                scanned=3,
                total=3,
            )
        }
        annotate_rows_with_reply_state(
            rows,
            runner=_drafts_runner({}),
            timeout=30,
            include_draft_state=False,
            include_sent_reply_state=True,
            sent_snapshots=sent_cache,
        )
        self.assertEqual([row["mail_was_replied_to"] for row in rows], [True, False, False])
        self.assertEqual([row["has_sent_reply"] for row in rows], [False, True, False])
        self.assertEqual([row["reply_state"] for row in rows], [True, True, False])
        self.assertEqual([row["was_replied_to"] for row in rows], [True, False, False])

    def test_truncated_sent_nonmatch_is_unknown(self):
        rows = [{"account": "Work", "internet_message_id": "<unseen@example.com>", "was_replied_to": False}]
        sent_cache = {"Work": SentReplySnapshot(status="ok", scanned=10, total=20, truncated=True)}
        annotate_rows_with_reply_state(
            rows,
            runner=_drafts_runner({}),
            timeout=30,
            include_draft_state=False,
            include_sent_reply_state=True,
            sent_snapshots=sent_cache,
        )
        self.assertIsNone(rows[0]["has_sent_reply"])
        self.assertIsNone(rows[0]["reply_state"])

    def test_sent_reply_scan_status_includes_errors(self):
        status = build_sent_reply_scan_status(
            {"Work": SentReplySnapshot(status="error", scanned=1, total=2, truncated=True, errors=("failed",))}
        )
        self.assertEqual(status["status"], "error")
        self.assertEqual(status["scanned"], 1)
        self.assertEqual(status["total"], 2)
        self.assertTrue(status["truncated"])
        self.assertEqual(status["errors"], ["Work: failed"])

    def test_sent_reply_account_cap_reports_partial_coverage(self):
        calls: list[str] = []
        accounts = [f"Acct{i}" for i in range(6)]
        rows = [
            {
                "account": account,
                "internet_message_id": f"<{account.lower()}@example.com>",
                "was_replied_to": False,
            }
            for account in accounts
        ]
        sent_cache: dict[str, SentReplySnapshot] = {}
        requested: list[str] = []
        annotate_rows_with_reply_state(
            rows,
            runner=_drafts_runner({account: "SCANNED|||0\nTOTAL|||0" for account in accounts}, calls),
            timeout=60,
            include_draft_state=False,
            include_sent_reply_state=True,
            sent_snapshots=sent_cache,
            sent_accounts_requested=requested,
        )

        status = build_sent_reply_scan_status(sent_cache, requested)
        self.assertEqual(len(sent_cache), MAX_SENT_REPLY_SNAPSHOT_ACCOUNTS)
        self.assertEqual(status["status"], "partial")
        self.assertTrue(status["truncated"])
        self.assertTrue(status["account_limit_reached"])
        self.assertEqual(status["skipped_account_count"], 1)
        self.assertEqual(status["skipped_accounts"], ["Acct5"])
        self.assertIsNone(rows[-1]["has_sent_reply"])
        self.assertEqual(len(calls), MAX_SENT_REPLY_SNAPSHOT_ACCOUNTS)

    def test_sent_scan_skips_rows_without_usable_ids_or_with_native_reply(self):
        calls: list[str] = []
        rows = [
            {"account": "Work", "internet_message_id": "", "was_replied_to": False},
            {"account": "Work", "internet_message_id": "<native@example.com>", "was_replied_to": True},
        ]
        annotate_rows_with_reply_state(
            rows,
            runner=_drafts_runner({}, calls),
            timeout=60,
            include_draft_state=False,
            include_sent_reply_state=True,
        )
        self.assertEqual(calls, [])
        self.assertIsNone(rows[0]["reply_state"])
        self.assertTrue(rows[1]["reply_state"])

    def test_include_draft_state_false_sets_null_and_never_calls_runner(self):
        calls: list[str] = []
        rows = [{"account": "Work", "subject": "Hi", "sender": "a@b.com", "date": None}]
        cache = annotate_rows_with_reply_state(
            rows, runner=_drafts_runner({}, calls), timeout=30, include_draft_state=False
        )
        self.assertIsNone(rows[0]["has_draft"])
        self.assertEqual(cache, {})
        self.assertEqual(calls, [])

    def test_empty_rows_with_account_override_never_calls_runner(self):
        calls: list[str] = []
        cache = annotate_rows_with_reply_state(
            [], runner=_drafts_runner({}, calls), timeout=30, include_draft_state=True, account="Work"
        )
        self.assertEqual(cache, {})
        self.assertEqual(calls, [])

    def test_matching_draft_sets_has_draft_true(self):
        raw = "DRAFT|||Re: Budget|||alice@example.com|||2026-07-09T10:00:00|||\nCOUNT|||1\nTOTAL|||1"
        rows = [{"account": "Work", "subject": "Budget", "sender": "alice@example.com", "date": None}]
        annotate_rows_with_reply_state(rows, runner=_drafts_runner({"Work": raw}), timeout=30, include_draft_state=True)
        self.assertTrue(rows[0]["has_draft"])

    def test_non_matching_draft_sets_has_draft_false(self):
        # Complete scan (TOTAL == COUNT) => a nonmatch is a definitive False,
        # not a fail-open None.
        raw = "DRAFT|||Something else|||bob@example.com|||2026-07-09T10:00:00|||\nCOUNT|||1\nTOTAL|||1"
        rows = [{"account": "Work", "subject": "Budget", "sender": "alice@example.com", "date": None}]
        annotate_rows_with_reply_state(rows, runner=_drafts_runner({"Work": raw}), timeout=30, include_draft_state=True)
        self.assertIs(rows[0]["has_draft"], False)

    def test_errored_scan_sets_has_draft_null_not_false(self):
        rows = [{"account": "Work", "subject": "Budget", "sender": "alice@example.com", "date": None}]
        annotate_rows_with_reply_state(
            rows,
            runner=_drafts_runner({"Work": "ERROR|||Could not find Drafts mailbox"}),
            timeout=30,
            include_draft_state=True,
        )
        self.assertIsNone(rows[0]["has_draft"])

    def test_account_override_ignores_row_account_field(self):
        raw = "DRAFT|||Budget|||alice@example.com|||2026-07-09T10:00:00|||\nCOUNT|||1\nTOTAL|||1"
        rows = [{"subject": "Budget", "sender": "alice@example.com", "date": None}]  # no "account" key
        annotate_rows_with_reply_state(
            rows, runner=_drafts_runner({"Work": raw}), timeout=30, include_draft_state=True, account="Work"
        )
        self.assertTrue(rows[0]["has_draft"])

    def test_multi_account_fan_out_capped_at_five(self):
        calls: list[str] = []
        accounts = [f"Acct{i}" for i in range(6)]
        rows = [{"account": a, "subject": "x", "sender": "y@z.com", "date": None} for a in accounts]
        responses = {a: "COUNT|||0\nTOTAL|||0" for a in accounts}
        cache = annotate_rows_with_reply_state(
            rows, runner=_drafts_runner(responses, calls), timeout=30, include_draft_state=True
        )
        self.assertEqual(len(cache), MAX_DRAFT_SNAPSHOT_ACCOUNTS)
        # The 6th account never made the cap; its row must be null, not False.
        capped_accounts = set(cache.keys())
        uncapped = [r for r in rows if r["account"] not in capped_accounts]
        self.assertEqual(len(uncapped), 1)
        self.assertIsNone(uncapped[0]["has_draft"])
        for row in rows:
            if row["account"] in capped_accounts:
                self.assertIsNotNone(row["has_draft"])

    def test_shared_snapshot_cache_reused_across_calls(self):
        calls: list[str] = []
        runner = _drafts_runner({"Work": "COUNT|||0\nTOTAL|||0"}, calls)
        cache: dict[str, DraftsSnapshot] = {}
        annotate_rows_with_reply_state(
            [{"account": "Work", "subject": "a", "sender": "b@c.com", "date": None}],
            runner=runner,
            timeout=30,
            include_draft_state=True,
            account="Work",
            snapshots=cache,
        )
        annotate_rows_with_reply_state(
            [{"account": "Work", "subject": "d", "sender": "e@f.com", "date": None}],
            runner=runner,
            timeout=30,
            include_draft_state=True,
            account="Work",
            snapshots=cache,
        )
        # Only one Drafts snapshot AppleScript call across both invocations.
        self.assertEqual(len(calls), 1)

    def test_null_internet_message_id_falls_back_to_subject_sender(self):
        raw = "DRAFT|||Budget|||alice@example.com|||2026-07-09T10:00:00|||\nCOUNT|||1\nTOTAL|||1"
        rows = [
            {
                "account": "Work",
                "subject": "Budget",
                "sender": "alice@example.com",
                "date": None,
                "internet_message_id": None,
            }
        ]
        annotate_rows_with_reply_state(rows, runner=_drafts_runner({"Work": raw}), timeout=30, include_draft_state=True)
        self.assertTrue(rows[0]["has_draft"])


if __name__ == "__main__":
    unittest.main()
