"""Tests for structured email search and bulk update helpers."""

import asyncio
import json
import re
import unittest
from unittest.mock import patch

from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.tools import inbox as inbox_tools
from apple_mail_mcp.tools import manage as manage_tools
from apple_mail_mcp.tools import search as search_tools
from apple_mail_mcp.tools.search.script import _build_search_script


def _record_line(
    message_id,
    subject,
    internet_message_id="<abc@example.com>",
    sender="sender@example.com",
    mailbox="INBOX",
    account="Work",
    is_read=False,
    received_date="2026-03-07T10:00:00",
    content_preview="",
):
    return "|||".join(
        [
            str(message_id),
            internet_message_id,
            subject,
            sender,
            mailbox,
            account,
            "true" if is_read else "false",
            received_date,
            content_preview,
        ]
    )


def _thread_record_line(
    message_id,
    subject,
    internet_message_id="<thread@example.com>",
    sender="sender@example.com",
    mailbox="INBOX",
    account="Work",
    is_read=False,
    received_date="2026-03-07T10:00:00",
    content_preview="",
    in_reply_to="",
    references="",
):
    return "|||".join(
        [
            str(message_id),
            internet_message_id,
            subject,
            sender,
            mailbox,
            account,
            "true" if is_read else "false",
            received_date,
            content_preview,
            "",
            "",
            in_reply_to,
            references,
            "",
        ]
    )


def _record_line_ws(
    message_id,
    subject,
    internet_message_id="<abc@example.com>",
    sender="sender@example.com",
    mailbox="INBOX",
    account="Work",
    is_read=False,
    received_date="2026-03-07T10:00:00",
    content_preview="",
    to="",
    cc="",
    in_reply_to="",
    references="",
    bcc="",
    was_replied_to=False,
):
    """Build a full 15-field row (the current shape, including the
    ``was_replied_to`` field added by ``core.reply_state.was_replied_fragment``)."""
    return "|||".join(
        [
            str(message_id),
            internet_message_id,
            subject,
            sender,
            mailbox,
            account,
            "true" if is_read else "false",
            received_date,
            content_preview,
            to,
            cc,
            in_reply_to,
            references,
            bcc,
            "true" if was_replied_to else "false",
        ]
    )


def _run(coro):
    """Convenience: drive an async tool to completion from a sync test."""
    return asyncio.run(coro)


def _empty_sent_reply_snapshot(script: str) -> str | None:
    """Return a complete empty Sent snapshot for tests not exercising it."""
    if "sentMailbox" in script:
        return "SCANNED|||0\nTOTAL|||0"
    return None


def _clear_default_mail_account():
    """Multi-account dispatch tests must not inherit DEFAULT_MAIL_ACCOUNT from env."""
    from apple_mail_mcp import server as _srv

    return patch.object(_srv, "DEFAULT_MAIL_ACCOUNT", None)


class SearchToolTests(unittest.TestCase):
    def test_search_emails_pagination_consistency(self):
        captured = {}

        def fake_run(script, timeout=120):
            sent_result = _empty_sent_reply_snapshot(script)
            if sent_result is not None:
                return sent_result
            captured["script"] = script
            return "\n".join(
                [
                    _record_line(
                        100,
                        "Ticket 100",
                        received_date="2026-03-07T12:00:00",
                    ),
                    _record_line(
                        101,
                        "Ticket 101",
                        received_date="2026-03-07T11:00:00",
                    ),
                    _record_line(
                        102,
                        "Ticket 102",
                        received_date="2026-03-07T10:00:00",
                    ),
                ]
            )

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                _run(
                    search_tools.search_emails(
                        account="Work",
                        output_format="json",
                        offset=1,
                        limit=2,
                        max_results=None,
                        include_draft_state=False,
                    )
                )
            )

        self.assertEqual(response["offset"], 1)
        self.assertEqual(response["returned"], 2)
        self.assertTrue(response["has_more"])
        self.assertEqual(response["next_offset"], 3)
        self.assertEqual(
            response["items"][0]["mail_link"],
            "message://%3Cabc@example.com%3E",
        )
        self.assertIn("set offsetRemaining to 1", captured["script"])
        self.assertIn("set collectLimit to 3", captured["script"])

    def test_search_emails_unread_only_filter(self):
        """Test that read_status='unread' adds the correct whose clause."""
        captured = {}

        def fake_run(script, timeout=120):
            sent_result = _empty_sent_reply_snapshot(script)
            if sent_result is not None:
                return sent_result
            captured["script"] = script
            return _record_line(201, "Unread Ticket", is_read=False)

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                _run(
                    search_tools.search_emails(
                        account="Work",
                        subject_keyword="Ticket",
                        read_status="unread",
                        output_format="json",
                        limit=1,
                        include_draft_state=False,
                    )
                )
            )

        self.assertEqual(len(response["items"]), 1)
        self.assertFalse(response["items"][0]["is_read"])
        self.assertIn("messageRead is false", captured["script"])

    def test_search_emails_builds_real_date_filters(self):
        captured = {}

        def fake_run(script, timeout=120):
            sent_result = _empty_sent_reply_snapshot(script)
            if sent_result is not None:
                return sent_result
            captured["script"] = script
            return _record_line(
                301,
                "Dated Ticket",
                received_date="2026-03-05T09:00:00",
            )

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                _run(
                    search_tools.search_emails(
                        account="Work",
                        subject_keyword="Ticket",
                        date_from="2026-03-01",
                        date_to="2026-03-07",
                        output_format="json",
                        limit=1,
                        max_results=None,
                        include_draft_state=False,
                    )
                )
            )

        self.assertEqual(response["items"][0]["message_id"], "301")
        self.assertIn("set year of fromDate to 2026", captured["script"])
        self.assertIn("set month of fromDate to March", captured["script"])
        self.assertIn("messageDate >= fromDate", captured["script"])
        self.assertIn("messageDate <= toDate", captured["script"])

    def test_large_mailbox_search_uses_applescript_cap(self):
        """A1: when subject/sender filters are supplied, the script must bind
        a bounded newest-message slice and filter inside it so a 24K-message
        Exchange mailbox doesn't materialize every match."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                _run(
                    search_tools.search_emails(
                        account="Work",
                        subject_keywords=["INC-1", "INC-2"],
                        include_content=False,
                        output_format="json",
                        limit=50,
                        max_results=None,
                    )
                )
            )

        self.assertEqual(response["items"], [])
        # Narrow subject-only searches keep the scan to the requested page
        # size so no-hit lookups on large Exchange inboxes stay fast. Needle
        # fast path would use base_cap = limit+1+offset = 51, but the
        # SEARCH_HARD_CEILING (50) clamp fires and wins (AGENTIC-988).
        self.assertIn("set scanUpperBound to 50", captured["script"])
        self.assertIn("messages 1 thru scanUpperBound of currentMailbox", captured["script"])
        # The old, unfiltered enumeration must not appear.
        self.assertNotIn(
            "set matchingMessages to every message of currentMailbox\n",
            captured["script"],
        )
        self.assertNotIn("every message of currentMailbox whose", captured["script"])

    def test_no_filter_caps_via_messages_1_thru_n(self):
        """A1: with no filter conditions, the script should bind
        `messages 1 thru N` directly instead of `every message`.

        Phase A: ``recent_days=0`` is no longer accepted (the
        ``allow_full_scan`` escape hatch was retired), so this exercises the
        explicit-``date_from`` branch — ``search_emails`` zeros
        ``effective_recent_days`` when the caller passes ``date_from``.

        AGENTIC-2794 changed the expected bound. This used to assert
        ``scanUpperBound to 11`` (``base_cap = limit + 1 + offset``), which was
        the defect, not the contract: a caller who bounds the window with
        ``date_from`` instead of ``recent_days`` was handed the *page* as the
        scan, so a needle query with a small limit searched fewer messages than
        the same query with a large one. The window now sizes the slice
        whichever parameter spells it, so a months-old ``date_from`` scales to
        ``SEARCH_WINDOW_CAP`` (50). The binding shape this test exists to pin —
        ``messages 1 thru scanUpperBound``, never ``every message`` — is
        unchanged.
        """
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            _run(
                search_tools.search_emails(
                    account="Work",
                    output_format="json",
                    limit=10,
                    max_results=None,
                    date_from="2026-05-01",
                )
            )

        self.assertIn("set scanUpperBound to 50", captured["script"])
        self.assertIn("messages 1 thru scanUpperBound of currentMailbox", captured["script"])
        # `every message` should not appear as the binding source (the helper
        # functions don't reference it either in this branch).
        self.assertNotIn("set matchingMessages to every message", captured["script"])

    def test_date_only_filter_uses_whose_clause(self):
        """A2: a date-only call still filters inside the bounded slice so it
        doesn't fall back to a full-scan branch."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            _run(
                search_tools.search_emails(
                    account="Work",
                    date_from="2026-05-01",
                    output_format="json",
                    limit=10,
                    max_results=None,
                )
            )

        self.assertIn("messages 1 thru scanUpperBound of currentMailbox", captured["script"])
        self.assertIn("messageDate >= fromDate", captured["script"])
        self.assertNotIn("every message of currentMailbox whose", captured["script"])

    def test_search_emails_returns_mail_link_from_internet_message_id(self):
        def fake_run(script, timeout=120):
            return _record_line(
                401,
                "Linked Ticket",
                internet_message_id="<QwcH6OP9REaEX0pi8aR6-g@geopod-ismtpd-60>",
            )

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                _run(
                    search_tools.search_emails(
                        account="Work",
                        subject_keyword="Linked",
                        output_format="json",
                        limit=1,
                        max_results=None,
                    )
                )
            )

        self.assertEqual(
            response["items"][0]["internet_message_id"],
            "<QwcH6OP9REaEX0pi8aR6-g@geopod-ismtpd-60>",
        )
        self.assertEqual(
            response["items"][0]["mail_link"],
            "message://%3CQwcH6OP9REaEX0pi8aR6-g@geopod-ismtpd-60%3E",
        )

    def test_search_emails_mail_link_normalizes_missing_angle_brackets(self):
        """AppleScript sometimes returns the Message-ID without angle brackets;
        the mail_link should still include them (percent-encoded)."""

        def fake_run(script, timeout=120):
            return _record_line(
                402,
                "Unbracketed Ticket",
                internet_message_id="abc@example.com",
            )

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                _run(
                    search_tools.search_emails(
                        account="Work",
                        subject_keyword="Unbracketed",
                        output_format="json",
                        limit=1,
                        max_results=None,
                    )
                )
            )

        self.assertEqual(
            response["items"][0]["internet_message_id"],
            "abc@example.com",
        )
        self.assertEqual(
            response["items"][0]["mail_link"],
            "message://%3Cabc@example.com%3E",
        )

    def test_search_emails_account_none_dispatches_per_account(self):
        """A4b: when account is None, the tool first lists accounts (one
        AppleScript call), then runs one AppleScript per account in
        parallel via asyncio.to_thread. Each per-account script targets
        a single account via `{account "..."}`."""
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            # First call is the account list probe — return two account names.
            if "set acctNames to" in script:
                return "Work\nPersonal"
            # Subsequent per-account calls return no records.
            return ""

        with _clear_default_mail_account(), patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            _run(
                search_tools.search_emails(
                    account=None,
                    subject_keyword="Test",
                    output_format="json",
                    limit=5,
                )
            )

        # 1 list-accounts call + 2 per-account search calls
        self.assertEqual(len(scripts), 3)
        per_account_scripts = scripts[1:]
        self.assertTrue(any('set searchAccounts to {account "Work"}' in s for s in per_account_scripts))
        self.assertTrue(any('set searchAccounts to {account "Personal"}' in s for s in per_account_scripts))

    def test_search_emails_body_text_uses_ignoring_case_not_lowercase_handler(self):
        """A4c: body-search must no longer rely on the per-message shell-out
        lowercase handler. Instead it wraps comparisons in `ignoring case`."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            _run(
                search_tools.search_emails(
                    account="Work",
                    body_text="invoice",
                    allow_body_scan=True,
                    output_format="json",
                    limit=5,
                )
            )

        self.assertNotIn("on lowercase(", captured["script"])
        self.assertNotIn("my lowercase(", captured["script"])
        self.assertIn("ignoring case", captured["script"])
        self.assertIn('msgContent contains "invoice"', captured["script"])

    def test_get_email_by_id_returns_exact_message_json(self):
        captured = {}

        def fake_run(script, timeout=120):
            sent_result = _empty_sent_reply_snapshot(script)
            if sent_result is not None:
                return sent_result
            captured["script"] = script
            return _record_line(
                12345,
                "Exact Ticket",
                content_preview="Full body preview",
            )

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                search_tools.get_email_by_id(
                    account="Work",
                    message_id="12345",
                    output_format="json",
                    include_draft_state=False,
                )
            )

        self.assertEqual(response["item"]["message_id"], "12345")
        self.assertEqual(response["item"]["subject"], "Exact Ticket")
        self.assertEqual(response["item"]["content_preview"], "Full body preview")
        self.assertEqual(response["item"]["content"], "Full body preview")
        self.assertTrue(response["item"]["content_available"])
        self.assertFalse(response["item"]["content_truncated"])
        self.assertEqual(response["item"]["content_status"], "available")
        self.assertIn("whose id is 12345", captured["script"])

    def test_get_email_by_id_json_marks_truncated_content(self):
        def fake_run(script, timeout=120):
            return _record_line(
                12345,
                "Exact Ticket",
                content_preview="12345...",
            )

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                search_tools.get_email_by_id(
                    account="Work",
                    message_id="12345",
                    include_content=True,
                    max_content_length=5,
                    output_format="json",
                )
            )

        self.assertEqual(response["item"]["content"], "12345...")
        self.assertEqual(response["item"]["content_preview"], "12345...")
        self.assertTrue(response["item"]["content_available"])
        self.assertTrue(response["item"]["content_truncated"])
        self.assertEqual(response["item"]["content_status"], "truncated")

    def test_get_email_by_id_rejects_non_numeric_ids(self):
        result = search_tools.get_email_by_id(
            account="Work",
            message_id="abc",
            output_format="json",
        )

        self.assertIn("message_id must be a numeric", result)

    def test_get_email_by_ids_json_preserves_requested_order_and_missing_ids(self):
        captured: list[str] = []

        def fake_run(script, timeout=120):
            captured.append(script)
            sent_result = _empty_sent_reply_snapshot(script)
            if sent_result is not None:
                return sent_result
            return "\n".join(
                [
                    _record_line(202, "Second", sender="sender2@example.com"),
                    _record_line(101, "First", sender="sender1@example.com"),
                ]
            )

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_by_ids(
                account="Work",
                message_ids=["101", "bad", "202", "101", "303"],
                output_format="json",
                include_draft_state=False,
            )

        payload = json.loads(result)
        self.assertEqual(payload["requested_ids"], ["101", "202", "303"])
        self.assertEqual(payload["invalid_ids"], ["bad"])
        self.assertEqual(payload["missing_ids"], ["303"])
        self.assertEqual([item["message_id"] for item in payload["items"]], ["101", "202"])
        self.assertFalse(payload["include_content"])
        self.assertEqual(payload["chunk_size"], 50)
        self.assertEqual(len(captured), 2)
        self.assertIn("whose id is 101 or id is 202 or id is 303", captured[0])
        self.assertNotIn("set msgContent to content of aMessage", captured[0])

    def test_get_email_by_ids_keeps_50_ids_in_one_chunk(self):
        captured: list[str] = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return ""

        ids = [str(i) for i in range(1, 51)]
        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_by_ids(account="Work", message_ids=ids, output_format="json")

        payload = json.loads(result)
        self.assertEqual(payload["missing_ids"], ids)
        self.assertEqual(len(captured), 1)
        self.assertIn("whose id is 1 or id is 2", captured[0])
        self.assertIn("id is 50", captured[0])

    def test_get_email_by_ids_chunks_51_ids(self):
        captured: list[str] = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return ""

        ids = [str(i) for i in range(1, 52)]
        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_by_ids(account="Work", message_ids=ids, output_format="json")

        payload = json.loads(result)
        self.assertEqual(payload["requested_ids"], ids)
        self.assertEqual(payload["missing_ids"], ids)
        self.assertEqual(len(captured), 2)
        self.assertIn("whose id is 1 or id is 2", captured[0])
        self.assertIn("whose id is 51", captured[1])

    def test_get_email_by_ids_chunks_120_ids(self):
        captured: list[str] = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return ""

        ids = [str(i) for i in range(1, 121)]
        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_by_ids(account="Work", message_ids=ids, output_format="json")

        payload = json.loads(result)
        self.assertEqual(payload["returned"], 0)
        self.assertEqual(len(captured), 3)
        self.assertIn("whose id is 1", captured[0])
        self.assertIn("whose id is 51", captured[1])
        self.assertIn("whose id is 101", captured[2])

    def test_get_email_by_ids_include_content_reads_content_and_sets_quote_flag(self):
        def fake_run(script, timeout=120):
            self.assertIn("set msgContent to content of aMessage", script)
            return _record_line(101, "Quoted", content_preview="On Monday, someone wrote: hello")

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_by_ids(
                account="Work",
                message_ids=["101"],
                include_content=True,
                output_format="json",
                include_draft_state=False,
            )

        payload = json.loads(result)
        self.assertTrue(payload["items"][0]["has_quoted_original"])

    def test_search_emails_timeout_param_is_forwarded(self):
        """A3: an explicit `timeout=N` kwarg must reach run_applescript so the
        caller can extend (or shorten) the per-account budget."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["timeout"] = timeout
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            _run(
                search_tools.search_emails(
                    account="Work",
                    subject_keyword="Test",
                    output_format="json",
                    limit=5,
                    timeout=300,
                )
            )

        self.assertEqual(captured["timeout"], 300)

    def test_search_emails_per_account_timeout_yields_errors_field(self):
        """A4: when one account's AppleScript times out, the call must still
        return data from the other accounts plus an `errors` list naming the
        slow account(s)."""

        def fake_run(script, timeout=120):
            if "set acctNames to" in script:
                return "Work\nTU"
            if 'account "TU"' in script:
                raise AppleScriptTimeout("TU timed out")
            # Work returns one record.
            return _record_line(700, "Work email", account="Work")

        with _clear_default_mail_account(), patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                _run(
                    search_tools.search_emails(
                        account=None,
                        subject_keyword="Anything",
                        output_format="json",
                        limit=5,
                    )
                )
            )

        self.assertIn("errors", response)
        self.assertEqual(response["errors"], ["TU"])
        self.assertEqual(
            response["error_details"],
            [{"account": "TU", "type": "timeout", "message": "TU"}],
        )
        self.assertEqual(len(response["items"]), 1)
        self.assertEqual(response["items"][0]["account"], "Work")

    def test_search_emails_per_account_error_includes_error_details(self):
        def fake_run(script, timeout=120):
            if "set acctNames to" in script:
                return "Work\nBroken"
            if 'account "Broken"' in script:
                raise RuntimeError("Mail permission denied")
            return _record_line(701, "Work email", account="Work")

        with _clear_default_mail_account(), patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                _run(
                    search_tools.search_emails(
                        account=None,
                        subject_keyword="Anything",
                        output_format="json",
                        limit=5,
                    )
                )
            )

        self.assertEqual(response["errors"], ["Broken"])
        self.assertEqual(
            response["error_details"],
            [
                {
                    "account": "Broken",
                    "type": "RuntimeError",
                    "message": "Mail permission denied",
                }
            ],
        )
        self.assertEqual(len(response["items"]), 1)

    def test_search_emails_single_account_skips_account_listing(self):
        """A4b: when an explicit account is passed, the tool must NOT run the
        account-listing probe — single-account calls should incur zero gather
        overhead."""
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            _run(
                search_tools.search_emails(
                    account="Work",
                    subject_keyword="Test",
                    output_format="json",
                    limit=5,
                )
            )

        self.assertEqual(len(scripts), 1)
        self.assertNotIn("set acctNames to", scripts[0])

    def test_search_emails_default_recent_days_applies_48h_window(self):
        """A0a: with no date args, a 48h window is auto-applied — the script
        must contain a populated `fromDate` and a `date received >= fromDate`
        clause, and the JSON response must echo `recent_days_applied=2.0`."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                _run(
                    search_tools.search_emails(
                        account="Work",
                        output_format="json",
                        limit=5,
                    )
                )
            )

        self.assertIn("set year of fromDate to", captured["script"])
        self.assertIn("messageDate >= fromDate", captured["script"])
        self.assertEqual(response["recent_days_applied"], 2.0)
        self.assertIsNotNone(response["searched_from"])

    def test_search_emails_explicit_date_from_disables_window(self):
        """A0a: an explicit ``date_from`` disables the recent_days auto-window —
        ``recent_days_applied`` should report ``0.0`` and ``searched_from``
        should echo the caller-supplied date."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                _run(
                    search_tools.search_emails(
                        account="Work",
                        output_format="json",
                        limit=5,
                        date_from="2026-05-01",
                    )
                )
            )

        # An explicit date_from still emits the fromDate machinery, but the
        # response should report effective_recent_days=0 and echo the date.
        self.assertIn("set year of fromDate to", captured["script"])
        self.assertEqual(response["recent_days_applied"], 0.0)
        self.assertEqual(response["searched_from"], "2026-05-01")

    def test_search_emails_rejects_unbounded_scan(self):
        """Phase A: ``recent_days=0`` without ``date_from`` returns a
        structured ``UNBOUNDED_SCAN_REQUIRED`` error with a bounded
        ``preferred`` fix and no dead-end pointer at the disabled
        ``full_inbox_export`` tool (no more ``allow_full_scan`` opt-in)."""
        with patch("apple_mail_mcp.tools.search.run_applescript") as mock_run:
            result = _run(
                search_tools.search_emails(
                    account="Work",
                    output_format="json",
                    limit=5,
                    recent_days=0,
                )
            )

        payload = json.loads(result)
        self.assertTrue(payload["error"])
        self.assertEqual(payload["code"], "UNBOUNDED_SCAN_REQUIRED")
        self.assertIn("recent_days", payload["message"])
        self.assertNotIn("full_inbox_export", str(payload["remediation"]))
        self.assertTrue(payload["remediation"].get("preferred"))
        mock_run.assert_not_called()

    def test_search_emails_explicit_date_from_overrides_default_window(self):
        """A0a: an explicit `date_from` overrides the 48h default — the script
        must encode the caller-supplied date, not today−2."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                _run(
                    search_tools.search_emails(
                        account="Work",
                        date_from="2026-01-01",
                        output_format="json",
                        limit=5,
                    )
                )
            )

        self.assertIn("set year of fromDate to 2026", captured["script"])
        self.assertIn("set month of fromDate to January", captured["script"])
        self.assertIn("set day of fromDate to 1", captured["script"])
        self.assertEqual(response["searched_from"], "2026-01-01")

    def test_search_emails_default_account_respected_when_env_set(self):
        """A0b: when DEFAULT_MAIL_ACCOUNT is set and the caller passes neither
        `account` nor `all_accounts=True`, the generated script must target
        that default account."""
        captured = {}

        def fake_run(script, timeout=120):
            captured.setdefault("scripts", []).append(script)
            return ""

        from apple_mail_mcp import server as _srv

        with (
            patch.object(_srv, "DEFAULT_MAIL_ACCOUNT", "Work"),
            patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run),
        ):
            _run(
                search_tools.search_emails(
                    subject_keyword="Test",
                    output_format="json",
                    limit=5,
                )
            )

        # Single-account fast path: only one AppleScript call, targeting "Work".
        self.assertEqual(len(captured["scripts"]), 1)
        self.assertIn('set searchAccounts to {account "Work"}', captured["scripts"][0])
        self.assertNotIn("set acctNames to", captured["scripts"][0])

    def test_search_emails_all_accounts_overrides_default_account(self):
        """A0b: `all_accounts=True` must bypass the DEFAULT_MAIL_ACCOUNT
        fallback and trigger multi-account dispatch."""
        captured = {}

        def fake_run(script, timeout=120):
            captured.setdefault("scripts", []).append(script)
            if "set acctNames to" in script:
                return "Work\nPersonal"
            return ""

        from apple_mail_mcp import server as _srv

        with (
            patch.object(_srv, "DEFAULT_MAIL_ACCOUNT", "Work"),
            patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run),
        ):
            _run(
                search_tools.search_emails(
                    all_accounts=True,
                    subject_keyword="Test",
                    output_format="json",
                    limit=5,
                )
            )

        # 1 account-listing probe + 2 per-account dispatches.
        self.assertEqual(len(captured["scripts"]), 3)
        per_account = captured["scripts"][1:]
        self.assertTrue(any('set searchAccounts to {account "Work"}' in s for s in per_account))
        self.assertTrue(any('set searchAccounts to {account "Personal"}' in s for s in per_account))

    def test_search_emails_parallel_dispatch_uses_to_thread(self):
        """A4b: per-account searches must be dispatched via asyncio.to_thread
        (one call per account) rather than serially inside one big script."""
        from unittest.mock import MagicMock

        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            if "set acctNames to" in script:
                return "A\nB\nC"
            return ""

        with (
            _clear_default_mail_account(),
            patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run),
            patch(
                "apple_mail_mcp.tools.search.asyncio.to_thread",
                wraps=asyncio.to_thread,
            ) as to_thread_spy,
        ):
            _run(
                search_tools.search_emails(
                    account=None,
                    subject_keyword="X",
                    output_format="json",
                    limit=5,
                )
            )

        # 1 list-accounts dispatch + 3 per-account dispatches
        self.assertGreaterEqual(to_thread_spy.call_count, 4)


class ListInboxEmailsTests(unittest.TestCase):
    def test_list_inbox_emails_caps_messages_in_applescript(self):
        """A1: text-format list_inbox_emails must bind `messages 1 thru N`
        rather than `every message` so large inboxes don't fully enumerate."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            _run(inbox_tools.list_inbox_emails(account="Work", max_emails=10))

        self.assertIn("messages 1 thru 10 of inboxMailbox", captured["script"])
        self.assertNotIn("every message of inboxMailbox", captured["script"])

    def test_list_inbox_emails_unread_only_uses_in_loop_filter(self):
        """A1: include_read=False must use an in-loop `if read status` check
        instead of the dangerous `whose read status is false` over a bound slice.
        The safe pattern binds `messages 1 thru N` first, then filters in-loop."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with (
            self.assertWarns(DeprecationWarning),
            patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run),
        ):
            _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=10,
                    include_read=False,
                    output_format="json",
                )
            )

        script = captured["script"]
        # Safe pattern: bounded slice first, then in-loop if filter.
        self.assertIn("messages 1 thru", script)
        self.assertIn("if read status of aMessage is false", script)
        # The dangerous `whose` over a bound slice must NOT appear.
        self.assertNotIn("candidateMessages whose read status is false", script)

    def test_list_inbox_emails_timeout_is_forwarded(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["timeout"] = timeout
            return ""

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            _run(inbox_tools.list_inbox_emails(account="Work", max_emails=5, timeout=240))

        self.assertEqual(captured["timeout"], 240)

    def test_list_inbox_emails_default_max_emails_is_50(self):
        """A0a: list_inbox_emails defaults max_emails to 50, which must be
        baked into the AppleScript via `messages 1 thru 50`."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            _run(inbox_tools.list_inbox_emails(account="Work"))

        self.assertIn("messages 1 thru 50 of inboxMailbox", captured["script"])

    def test_list_inbox_emails_partial_results_on_account_timeout(self):
        """A4: when one account's AppleScript times out, list_inbox_emails
        (JSON path) must still return other accounts' data + an `errors`
        list."""

        def fake_run(script, timeout=120):
            if "set acctNames to" in script:
                return "Work\nTU"
            if 'account "TU"' in script:
                raise AppleScriptTimeout("TU timed out")
            # Schema: subject|||sender|||date|||read|||account|||mail_app_id
            return "Hello|||sender@example.com|||today|||false|||Work|||1"

        with _clear_default_mail_account(), patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            payload = _run(inbox_tools.list_inbox_emails(output_format="json", max_emails=5))

        # v3.2.x: JSON path returns a dict directly (no json.loads needed).
        self.assertIsInstance(payload, dict)
        self.assertIn("emails", payload)
        self.assertIn("errors", payload)
        self.assertEqual(payload["errors"], ["TU"])
        self.assertEqual(len(payload["emails"]), 1)
        self.assertEqual(payload["emails"][0]["account"], "Work")


class ManageToolTests(unittest.TestCase):
    def test_move_email_dry_run_uses_search_helper(self):
        with (
            patch(
                "apple_mail_mcp.tools.manage._search_mail_records",
                return_value=[
                    {
                        "subject": "Ticket",
                        "sender": "sender@example.com",
                        "received_date": "2026-03-07T10:00:00",
                    }
                ],
            ) as mock_search,
            patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run,
        ):
            result = manage_tools.move_email(
                account="Work",
                to_mailbox="Archive",
                older_than_days=30,
                dry_run=True,
                max_moves=1,
                allow_filter_scan=True,
            )

        mock_search.assert_called_once()
        mock_run.assert_not_called()
        self.assertIn("WARNING: filter scan enabled", result)
        self.assertIn("DRY RUN - PREVIEW MOVE", result)
        self.assertIn("Would move: Ticket", result)

    def test_manage_trash_dry_run_uses_search_helper(self):
        with (
            patch(
                "apple_mail_mcp.tools.manage._search_mail_records",
                return_value=[],
            ) as mock_search,
            patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run,
        ):
            result = manage_tools.manage_trash(
                account="Work",
                action="move_to_trash",
                older_than_days=30,
                dry_run=True,
                max_deletes=1,
                allow_filter_scan=True,
            )

        mock_search.assert_called_once()
        mock_run.assert_not_called()
        self.assertIn("WARNING: filter scan enabled", result)
        self.assertIn("DRY RUN - PREVIEW TRASH", result)
        self.assertIn("TOTAL: 0", result)

    def test_manage_trash_permanent_delete_dry_run_preserves_preview(self):
        with patch(
            "apple_mail_mcp.tools.manage.run_applescript",
            return_value="preview",
        ) as mock_run:
            result = manage_tools.manage_trash(
                account="Work",
                action="delete_permanent",
                message_ids=["101"],
            )

        self.assertIn("preview", result)
        script = mock_run.call_args.args[0]
        self.assertIn("DRY RUN - PREVIEW PERMANENT DELETE BY IDS", script)
        self.assertIn("Would permanently delete", script)
        self.assertNotIn("delete aMessage", script)

    def test_manage_trash_permanent_delete_apply_to_all_dry_run_does_not_delete(self):
        """apply_to_all resolves ids through the bounded search, then previews by id.

        The purge used to be a hand-rolled script over a bare newest-first
        ``messages 1 thru N of trashMailbox`` slice, which applied no date window at
        all. It now goes through ``_search_message_ids`` (which carries
        ``date_from`` / ``date_to``) and recurses into the id-direct path, so the
        dry-run label is the BY IDS variant and the preview must still not delete.
        """
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "preview"

        with (
            patch(
                "apple_mail_mcp.tools.manage._search_mail_records",
                return_value=[{"message_id": "101", "subject": "Old", "sender": "sender@example.com"}],
            ) as mock_search,
            patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run),
        ):
            result = manage_tools.manage_trash(
                account="Work",
                action="delete_permanent",
                apply_to_all=True,
                allow_filter_scan=True,
            )

        self.assertEqual(mock_search.call_args.kwargs["mailbox"], "Trash")
        self.assertIn("preview", result)
        self.assertIn("DRY RUN - PREVIEW PERMANENT DELETE", captured["script"])
        self.assertIn("Would permanently delete", captured["script"])
        self.assertIn("id is 101", captured["script"])
        self.assertNotIn("messages 1 thru 5 of trashMailbox", captured["script"])
        self.assertNotIn("delete aMessage", captured["script"])

    def test_update_email_status_with_message_ids_uses_exact_id_condition(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "updated"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            result = manage_tools.update_email_status(
                account="Work",
                mailbox="INBOX",
                message_ids=["101", "202"],
                action="mark_read",
            )

        self.assertEqual(result, "updated")
        self.assertIn("id is 101", captured["script"])
        self.assertIn("id is 202", captured["script"])
        self.assertIn("set read status of targetMessages to true", captured["script"])


class GetEmailThreadTests(unittest.TestCase):
    """Phase 2 scan-path hardening for get_email_thread."""

    def test_get_email_thread_default_emits_whose_date_filter_and_cap(self):
        """The candidate slice is sized from the date window, not ``max_messages``.

        This test used to assert ``scanUpperBound == max_messages``, which was
        the AGENTIC-2794 defect written down as a contract: bounding the *scan*
        by the *return* limit meant a conversation whose earlier members sat
        past the newest ``max_messages`` messages came back short and reported
        itself complete. ``recent_days=2`` now scales to
        ``THREAD_SCAN_BASE_CAP + 2 * THREAD_SCAN_DAYS_SCALE``. The bounded-slice
        properties this test exists to protect are unchanged: still
        ``messages 1 thru scanUpperBound``, never ``every message``.
        """
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            captured["timeout"] = timeout
            return "EMAIL THREAD VIEW"

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_thread(
                account="Work",
                subject_keyword="Project Update",
                max_messages=25,
            )

        script = captured["script"]
        self.assertIn("EMAIL THREAD VIEW", result)
        self.assertIn("set cutoffDate to current date", script)
        self.assertIn("messageDate < cutoffDate", script)
        from apple_mail_mcp.constants import SCAN_BOUNDS

        expected_scan = min(
            SCAN_BOUNDS["THREAD_SCAN_BASE_CAP"] + int(2.0 * SCAN_BOUNDS["THREAD_SCAN_DAYS_SCALE"]),
            SCAN_BOUNDS["THREAD_SCAN_HARD_CEILING"],
        )
        self.assertIn(f"set scanUpperBound to {expected_scan}", script)
        self.assertNotIn("set scanUpperBound to 25", script)
        self.assertIn("messages 1 thru scanUpperBound of currentMailbox", script)
        self.assertNotIn("every message of currentMailbox", script)
        self.assertIn("ignoring case", script)
        self.assertIn("Window: last 48h", script)
        self.assertEqual(captured["timeout"], 120)

    def test_get_email_thread_recent_days_zero_rejected(self):
        """Phase A: ``recent_days=0`` is refused. The structured error carries
        a bounded ``preferred`` fix instead of an opt-in flag, and must not
        point callers at the disabled ``full_inbox_export`` tool."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "ok"

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_thread(
                account="Work",
                subject_keyword="Budget",
                max_messages=10,
                recent_days=0,
            )

        # get_email_thread is `-> str`; the unbounded-scan envelope is
        # JSON-encoded so callers always get the structured remediation.
        self.assertIsInstance(result, str)
        import json as _json

        parsed = _json.loads(result)
        self.assertIsInstance(parsed, dict)
        self.assertEqual(parsed.get("code"), "UNBOUNDED_SCAN_REQUIRED")
        self.assertTrue(parsed.get("error"))
        remediation = parsed.get("remediation", {})
        self.assertNotIn("full_inbox_export", str(remediation))
        self.assertTrue(remediation.get("preferred"))
        self.assertNotIn("script", captured)

    def test_get_email_thread_no_bare_every_message_enumeration(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "ok"

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            search_tools.get_email_thread(
                account="Work",
                subject_keyword="Standup",
                max_messages=5,
            )

        script = captured["script"]
        self.assertNotIn("set mailboxMessages to every message of currentMailbox", script)
        self.assertNotIn("repeat with aMessage in mailboxMessages", script)

    def test_get_email_thread_passes_custom_timeout(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["timeout"] = timeout
            return "ok"

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            search_tools.get_email_thread(
                account="Work",
                subject_keyword="Invoice",
                timeout=300,
            )

        self.assertEqual(captured["timeout"], 300)

    def test_get_email_thread_handles_timeout(self):
        def fake_run(script, timeout=120):
            raise AppleScriptTimeout("simulated")

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_thread(
                account="Work",
                subject_keyword="Invoice",
                timeout=90,
            )

        self.assertIn("timed out", result.lower())
        self.assertIn("Work", result)
        self.assertIn("90", result)

    def test_get_email_thread_rejects_invalid_max_messages(self):
        result = search_tools.get_email_thread(
            account="Work",
            subject_keyword="Invoice",
            max_messages=0,
        )
        self.assertIn("max_messages must be > 0", result)

    def test_get_email_thread_rejects_invalid_output_format(self):
        result = search_tools.get_email_thread(
            account="Work",
            subject_keyword="Invoice",
            output_format="xml",
        )
        self.assertIn("Invalid output_format", result)

    def test_get_email_thread_json_returns_ids_headers_and_anchor(self):
        anchor_line = _thread_record_line(
            12345,
            "Re: Budget Review",
            internet_message_id="<reply@example.com>",
            sender="alice@example.com",
            in_reply_to="<root@example.com>",
            references="<root@example.com> <prior@example.com>",
        )
        thread_rows = "\n".join(
            [
                _thread_record_line(
                    12345,
                    "Re: Budget Review",
                    internet_message_id="<reply@example.com>",
                    sender="alice@example.com",
                    in_reply_to="<root@example.com>",
                    references="<root@example.com> <prior@example.com>",
                ),
                _thread_record_line(
                    12000,
                    "Renamed: Budget decision",
                    internet_message_id="<root@example.com>",
                    sender="finance@example.com",
                    references="<prior@example.com>",
                ),
            ]
        )
        captured: list[str] = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "whose id is 12345" in script:
                return anchor_line
            return "THREAD_STRATEGY|||header\n" + thread_rows

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_thread(
                account="Work",
                message_id="12345",
                mailboxes=["INBOX", "Sent"],
                max_messages=10,
                recent_days=7,
                include_preview=False,
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["strategy"], "header_first")
        self.assertEqual(payload["selection_strategy"], "header")
        self.assertFalse(payload["subject_fallback_used"])
        self.assertFalse(payload["include_preview"])
        self.assertEqual(payload["mailboxes"], ["INBOX", "Sent"])
        self.assertEqual(payload["anchor"]["message_id"], "12345")
        self.assertEqual(payload["anchor"]["in_reply_to"], "<root@example.com>")
        self.assertEqual(payload["returned"], 2)
        self.assertEqual(payload["items"][0]["message_id"], "12345")
        self.assertEqual(payload["items"][0]["references"], "<root@example.com> <prior@example.com>")
        self.assertNotIn("content_preview", payload["items"][0])

        thread_script = captured[1]
        self.assertIn(
            'set threadHeaderTokens to {"prior@example.com", "reply@example.com", "root@example.com"}', thread_script
        )
        self.assertIn('mailbox "Sent" of targetAccount', thread_script)
        self.assertNotIn("set msgContent to content of aMessage", thread_script)

    def test_get_email_thread_header_first_uses_subject_fallback_only_when_no_header_matches(self):
        anchor_line = _thread_record_line(
            12345,
            "Re: Quarterly Update",
            internet_message_id="<reply@example.com>",
            in_reply_to="<root@example.com>",
            references="<root@example.com>",
        )
        captured: list[str] = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "whose id is 12345" in script:
                return anchor_line
            return "THREAD_STRATEGY|||subject_fallback\n"

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_thread(
                account="Work",
                message_id="12345",
                max_messages=10,
                recent_days=7,
                output_format="json",
            )

        thread_script = captured[1]
        self.assertIn("set headerThreadMessages to {}", thread_script)
        self.assertIn("set subjectFallbackMessages to {}", thread_script)
        self.assertIn("set end of headerThreadMessages to aMessage", thread_script)
        self.assertIn("set end of subjectFallbackMessages to aMessage", thread_script)
        self.assertIn("if true and (count of headerThreadMessages) > 0 then", thread_script)
        self.assertIn("set threadMessages to headerThreadMessages", thread_script)
        self.assertIn("set threadMessages to subjectFallbackMessages", thread_script)

        payload = json.loads(result)
        self.assertEqual(payload["selection_strategy"], "subject_fallback")
        self.assertTrue(payload["subject_fallback_used"])

    def test_get_email_thread_message_id_without_headers_reports_subject_strategy(self):
        anchor_line = _thread_record_line(
            12345,
            "Re: Budget Review",
            internet_message_id="",
        )

        def fake_run(script, timeout=120):
            if "whose id is 12345" in script:
                return anchor_line
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_thread(
                account="Work",
                message_id="12345",
                max_messages=10,
                recent_days=7,
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["strategy"], "subject")
        self.assertIn("warnings", payload)
        self.assertTrue(
            any("subject fallback" in warning for warning in payload["warnings"]),
            payload["warnings"],
        )

    def test_get_email_thread_message_id_tries_explicit_mailboxes_for_anchor(self):
        anchor_line = _thread_record_line(
            12345,
            "Re: Budget Review",
            mailbox="Sent",
            internet_message_id="<reply@example.com>",
            in_reply_to="<root@example.com>",
        )
        captured: list[str] = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if 'set targetMailbox to mailbox "INBOX"' in script:
                return ""
            if 'set targetMailbox to mailbox "Sent"' in script:
                return anchor_line
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_thread(
                account="Work",
                message_id="12345",
                mailboxes=["INBOX", "Sent"],
                max_messages=10,
                recent_days=7,
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["anchor"]["mailbox"], "Sent")
        # Two anchor probes (INBOX misses, Sent hits) + the thread scan, then
        # the draft and sent-reply scans. Those last two are new: the anchor is
        # now retained when the scan does not return it, so the row set is no
        # longer empty and the reply-state annotation actually has work to do.
        self.assertEqual(len(captured), 5)
        self.assertIn('mailbox "Sent" of targetAccount', captured[2])
        self.assertTrue(payload["items"], "the anchor must survive an empty scan")
        self.assertTrue(payload["items"][0]["anchor_recovered"])
        self.assertTrue(payload["thread_incomplete"])


class MailboxAllCapTests(unittest.TestCase):
    """Fix #5: search_emails(mailbox='All') must cap at MAX_MAILBOXES_PER_SEARCH_ALL."""

    def _run(self, coro):
        import asyncio

        return asyncio.run(coro)

    def test_mailbox_all_script_contains_cap_guard(self):
        """Generated AppleScript must truncate searchMailboxes when > 10."""
        from apple_mail_mcp.constants import SCAN_BOUNDS

        cap = SCAN_BOUNDS["MAX_MAILBOXES_PER_SEARCH_ALL"]

        captured = {}

        def fake_run(script, timeout=180):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            self._run(
                search_tools.search_emails(
                    account="Work",
                    mailbox="All",
                    sender="test@example.com",
                    recent_days=7,
                )
            )

        script = captured.get("script", "")
        self.assertIn(f"items 1 thru {cap} of searchMailboxes", script)
        self.assertIn(f"(count of searchMailboxes) > {cap}", script)

    def test_mailbox_all_json_response_contains_warning(self):
        """JSON response for mailbox='All' must include a mailbox cap warning."""

        def fake_run(script, timeout=180):
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            raw = self._run(
                search_tools.search_emails(
                    account="Work",
                    mailbox="All",
                    sender="test@example.com",
                    recent_days=7,
                    output_format="json",
                )
            )

        payload = json.loads(raw)
        self.assertIn("warnings", payload)
        self.assertTrue(len(payload["warnings"]) > 0)
        self.assertIn("mailbox='All'", payload["warnings"][0])
        self.assertTrue(payload.get("mailboxes_truncated"))

    def test_mailbox_inbox_script_has_no_cap_guard(self):
        """For mailbox='INBOX' the cap guard must NOT appear in the script."""
        from apple_mail_mcp.constants import SCAN_BOUNDS

        cap = SCAN_BOUNDS["MAX_MAILBOXES_PER_SEARCH"]

        captured = {}

        def fake_run(script, timeout=180):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            self._run(
                search_tools.search_emails(
                    account="Work",
                    mailbox="INBOX",
                    sender="test@example.com",
                    recent_days=7,
                )
            )

        script = captured.get("script", "")
        self.assertNotIn(f"items 1 thru {cap} of searchMailboxes", script)

    def test_mailbox_inbox_json_response_has_no_mailbox_warning(self):
        """JSON response for mailbox='INBOX' must NOT include a mailbox cap warning."""

        def fake_run(script, timeout=180):
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            raw = self._run(
                search_tools.search_emails(
                    account="Work",
                    mailbox="INBOX",
                    sender="test@example.com",
                    recent_days=7,
                    output_format="json",
                )
            )

        payload = json.loads(raw)
        warnings = payload.get("warnings", [])
        # No mailbox cap warnings for INBOX
        for w in warnings:
            self.assertNotIn("mailbox='All'", w)


class NewFieldsTests(unittest.TestCase):
    """Tests for FIX #2 (to/cc on search_emails), FIX #3 (get_email_by_id
    threading/recipient fields), and FIX #5 (mailboxes param + All timeout)."""

    def _run(self, coro):  # type: ignore[override]
        return asyncio.run(coro)

    # ------------------------------------------------------------------
    # FIX #2: bulk search does NOT resolve recipients (Exchange-safe).
    # Per-message `to recipients`/`address of` can HANG on large remote
    # mailboxes (uncatchable by `on error`) and stall the whole scan, so
    # recipients are fetched per-message via get_email_by_id instead. The
    # shared parser still surfaces to/cc when a line carries them.
    # ------------------------------------------------------------------

    def test_search_emails_does_not_resolve_recipients_in_bulk(self):
        """The bulk scan script must NOT call `to recipients`/`cc recipients`
        or `address of` — those can hang on large Exchange/Gmail mailboxes."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return _record_line(999, "Hello Subject")

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            self._run(
                search_tools.search_emails(
                    account="Work",
                    include_content=True,
                    output_format="json",
                    limit=5,
                    include_draft_state=False,
                )
            )

        self.assertNotIn("to recipients of aMessage", captured["script"])
        self.assertNotIn("cc recipients of aMessage", captured["script"])
        self.assertNotIn("address of aRecip", captured["script"])

    def test_search_parser_surfaces_to_cc_when_line_carries_them(self):
        """The shared record parser still exposes to/cc (fields 9,10) when a
        line provides them — this is the path get_email_by_id relies on."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            parts = [
                "999",  # 0 message_id
                "<msg@example.com>",  # 1 internet_message_id
                "Hello Subject",  # 2 subject
                "sender@example.com",  # 3 sender
                "INBOX",  # 4 mailbox
                "Work",  # 5 account
                "false",  # 6 read
                "2026-06-01T10:00:00",  # 7 received_date
                "Body preview",  # 8 content_preview
                "alice@example.com, bob@example.com",  # 9 to
                "carol@example.com",  # 10 cc
                "",  # 11 in_reply_to
                "",  # 12 references
                "",  # 13 bcc
            ]
            return "|||".join(parts)

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                self._run(
                    search_tools.search_emails(
                        account="Work",
                        include_content=True,
                        output_format="json",
                        limit=5,
                    )
                )
            )

        item = response["items"][0]
        self.assertEqual(item["to"], "alice@example.com, bob@example.com")
        self.assertEqual(item["cc"], "carol@example.com")

    # ------------------------------------------------------------------
    # FIX #3: get_email_by_id threading/recipient fields
    # ------------------------------------------------------------------

    def _record_line_14(
        self,
        message_id: int,
        subject: str,
        content_preview: str = "",
        to: str = "",
        cc: str = "",
        in_reply_to: str = "",
        references: str = "",
        bcc: str = "",
    ) -> str:
        parts = [
            str(message_id),
            "<abc@example.com>",
            subject,
            "sender@example.com",
            "INBOX",
            "Work",
            "false",
            "2026-06-01T10:00:00",
            content_preview,
            to,
            cc,
            in_reply_to,
            references,
            bcc,
        ]
        return "|||".join(parts)

    def test_get_email_by_id_parses_threading_fields(self):
        """A mocked 14-field line must parse into to/cc/in_reply_to/references/bcc."""
        line = self._record_line_14(
            12345,
            "Re: Budget",
            content_preview="Thanks for the update",
            to="alice@example.com",
            cc="bob@example.com",
            in_reply_to=" <orig@example.com>",
            references=" <orig@example.com>",
            bcc="",
        )

        def fake_run(script, timeout=120):
            return line

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                search_tools.get_email_by_id(
                    account="Work",
                    message_id="12345",
                    output_format="json",
                )
            )

        item = response["item"]
        self.assertEqual(item["to"], "alice@example.com")
        self.assertEqual(item["cc"], "bob@example.com")
        self.assertEqual(item["in_reply_to"], "<orig@example.com>")
        self.assertEqual(item["references"], "<orig@example.com>")
        self.assertNotIn("bcc", item)  # bcc was empty

    def test_get_email_by_id_script_contains_all_headers(self):
        """The generated AppleScript must read `all headers of aMessage`
        to extract In-Reply-To and References."""
        captured = {}

        def fake_run(script, timeout=120):
            sent_result = _empty_sent_reply_snapshot(script)
            if sent_result is not None:
                return sent_result
            captured["script"] = script
            return self._record_line_14(12345, "Test")

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            search_tools.get_email_by_id(
                account="Work",
                message_id="12345",
                output_format="json",
                include_draft_state=False,
            )

        self.assertIn("all headers of aMessage", captured["script"])
        self.assertIn('starts with "In-Reply-To:"', captured["script"])
        self.assertIn('starts with "References:"', captured["script"])

    def test_get_email_by_id_has_quoted_original_true(self):
        """has_quoted_original must be True when content contains 'On X wrote:'."""
        # Note: content_preview must not contain raw newlines here because the
        # mock line is split by splitlines() in _parse_search_records. Use a
        # space-collapsed preview that still triggers the regex.
        line = self._record_line_14(
            12345,
            "Re: Something",
            content_preview="My reply. On Mon Jan 1 2026 Alice wrote: original text",
        )

        def fake_run(script, timeout=120):
            return line

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                search_tools.get_email_by_id(
                    account="Work",
                    message_id="12345",
                    include_content=True,
                    output_format="json",
                )
            )

        self.assertTrue(response["item"]["has_quoted_original"])

    def test_get_email_by_id_has_quoted_original_false(self):
        """has_quoted_original must be False for plain new messages."""
        line = self._record_line_14(
            12346,
            "New Topic",
            content_preview="Just a plain message with no quotes.",
        )

        def fake_run(script, timeout=120):
            return line

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                search_tools.get_email_by_id(
                    account="Work",
                    message_id="12346",
                    include_content=True,
                    output_format="json",
                )
            )

        self.assertFalse(response["item"]["has_quoted_original"])

    # ------------------------------------------------------------------
    # FIX #5a: mailboxes=[...] builds targeted script
    # ------------------------------------------------------------------

    def test_search_emails_mailboxes_param_targets_named_folders(self):
        """mailboxes=['Archive','Sent'] must produce a script that looks up
        those specific folders and does not use the `All` search path."""
        captured = {}

        def fake_run(script, timeout=120):
            sent_result = _empty_sent_reply_snapshot(script)
            if sent_result is not None:
                return sent_result
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            self._run(
                search_tools.search_emails(
                    account="Work",
                    mailboxes=["Archive", "Sent"],
                    output_format="json",
                    limit=5,
                    date_from="2026-01-01",
                )
            )

        script = captured["script"]
        self.assertIn('mailbox "Archive" of targetAccount', script)
        self.assertIn('mailbox "Sent" of targetAccount', script)
        self.assertNotIn("set searchMailboxes to every mailbox of targetAccount", script)

    def test_search_emails_sent_mail_uses_account_scoped_fallback_for_singular_param(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            self._run(
                search_tools.search_emails(
                    account="Work",
                    mailbox="Sent Mail",
                    output_format="json",
                    limit=5,
                    date_from="2026-01-01",
                )
            )

        script = captured["script"]
        self.assertIn('mailbox "Sent Mail" of targetAccount', script)
        self.assertIn("every mailbox of targetAccount", script)

    def test_search_emails_sent_mail_uses_account_scoped_fallback_for_mailboxes_param(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            self._run(
                search_tools.search_emails(
                    account="Work",
                    mailboxes=["Sent Mail"],
                    output_format="json",
                    limit=5,
                    date_from="2026-01-01",
                )
            )

        script = captured["script"]
        self.assertIn('mailbox "Sent Mail" of targetAccount', script)
        self.assertIn("every mailbox of targetAccount", script)

    # ------------------------------------------------------------------
    # FIX #5b: All path isolates per-mailbox failures (partial results).
    # The per-mailbox scan is deliberately NOT wrapped in a short
    # `with timeout` — that fires on large Exchange mailboxes and the inner
    # candidate-fetch try/catch swallows it into a silent 0-row result.
    # Isolation comes from `on error -> ERROR_MAILBOX` instead.
    # ------------------------------------------------------------------

    def test_search_emails_all_path_isolates_per_mailbox_errors(self):
        """mailbox='All' must isolate a failing folder via ERROR_MAILBOX and
        must NOT wrap each scan in a short per-mailbox `with timeout`."""
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            self._run(
                search_tools.search_emails(
                    account="Work",
                    mailbox="All",
                    output_format="json",
                    limit=5,
                    date_from="2026-01-01",
                )
            )

        script = captured["script"]
        # Per-folder isolation handler is present.
        self.assertIn("ERROR_MAILBOX", script)
        self.assertIn("on error errMsg", script)
        # No short per-mailbox timeout (only the single outer call budget remains).
        self.assertEqual(script.count("with timeout of"), 1)

    def test_search_emails_all_path_error_mailbox_yields_partial_results(self):
        """An ERROR_MAILBOX marker in the output from the All path must
        produce partial records plus a structured mailbox error in the JSON."""
        good_line = _record_line(501, "Good Email", account="Work")
        error_line = "ERROR_MAILBOX|||SlowFolder|||timed out"

        def fake_run(script, timeout=120):
            return good_line + "\n" + error_line

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                self._run(
                    search_tools.search_emails(
                        account="Work",
                        mailbox="All",
                        output_format="json",
                        limit=10,
                        date_from="2026-01-01",
                    )
                )
            )

        # Good record must be present
        self.assertEqual(len(response["items"]), 1)
        self.assertEqual(response["items"][0]["subject"], "Good Email")
        # Error detail must surface the mailbox error
        self.assertIn("error_details", response)
        error_mailboxes = [e["mailbox"] for e in response["error_details"]]
        self.assertIn("SlowFolder", error_mailboxes)


class SearchGuardrailTests(unittest.TestCase):
    """Phase 3 search_emails guardrails: body scan gate, sender hint."""

    def _run(self, coro):
        import asyncio

        return asyncio.run(coro)

    def test_body_text_without_allow_body_scan_returns_structured_error(self):
        result = self._run(
            search_tools.search_emails(
                account="Work",
                body_text="quarterly report",
                recent_days=7,
            )
        )
        parsed = json.loads(result)
        self.assertEqual(parsed.get("code"), "BODY_SCAN_DISABLED")
        self.assertIn("allow_body_scan", parsed.get("remediation", {}).get("escape_hatch", ""))

    def test_body_text_with_allow_body_scan_proceeds(self):
        captured = {}

        def fake_run(script, timeout=180):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = self._run(
                search_tools.search_emails(
                    account="Work",
                    body_text="quarterly report",
                    allow_body_scan=True,
                    recent_days=7,
                )
            )

        self.assertNotIn("BODY_SCAN_DISABLED", result)
        self.assertIn("msgContent contains", captured["script"])
        self.assertIn("WARNING:", result)
        self.assertIn("body_text scans", result)

    def test_body_text_with_explicit_date_emits_json_warning(self):
        def fake_run(script, timeout=180):
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            raw = self._run(
                search_tools.search_emails(
                    account="Work",
                    body_text="quarterly report",
                    allow_body_scan=True,
                    date_from="2026-06-01",
                    recent_days=0,
                    output_format="json",
                )
            )

        payload = json.loads(raw)
        self.assertNotIn("body_search_capped", payload)
        warnings = payload.get("warnings", [])
        self.assertTrue(any("body_text scans" in w for w in warnings))

    def test_sender_only_search_emits_json_warning(self):
        def fake_run(script, timeout=180):
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            raw = self._run(
                search_tools.search_emails(
                    account="Work",
                    sender="news@example.com",
                    recent_days=7,
                    output_format="json",
                )
            )

        payload = json.loads(raw)
        warnings = payload.get("warnings", [])
        self.assertTrue(any("sender-only" in w.lower() for w in warnings))

    def test_sender_only_search_emits_text_warning(self):
        def fake_run(script, timeout=180):
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = self._run(
                search_tools.search_emails(
                    account="Work",
                    sender="news@example.com",
                    recent_days=7,
                    output_format="text",
                )
            )

        self.assertIn("WARNING:", result)
        self.assertIn("sender-only", result.lower())

    def test_include_content_search_emits_json_warning(self):
        def fake_run(script, timeout=180):
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            raw = self._run(
                search_tools.search_emails(
                    account="Work",
                    subject_keyword="review",
                    include_content=True,
                    recent_days=7,
                    output_format="json",
                )
            )

        payload = json.loads(raw)
        warnings = payload.get("warnings", [])
        self.assertTrue(any("include_content=true" in w.lower() for w in warnings))

    def test_include_content_search_emits_text_warning(self):
        def fake_run(script, timeout=180):
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = self._run(
                search_tools.search_emails(
                    account="Work",
                    subject_keyword="review",
                    include_content=True,
                    recent_days=7,
                    output_format="text",
                )
            )

        self.assertIn("WARNING:", result)
        self.assertIn("include_content=True", result)

    def test_sender_exact_builds_exact_address_condition(self):
        captured = {}

        def fake_run(script, timeout=180):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            self._run(
                search_tools.search_emails(
                    account="Work",
                    sender_exact="person@example.com",
                    recent_days=7,
                    output_format="json",
                )
            )

        script = captured["script"]
        self.assertIn('messageSender is "person@example.com"', script)
        self.assertIn('messageSender contains "<person@example.com>"', script)

    def test_sender_domain_builds_domain_condition_without_sender_only_warning(self):
        captured = {}

        def fake_run(script, timeout=180):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            raw = self._run(
                search_tools.search_emails(
                    account="Work",
                    sender_domain="@example.com",
                    recent_days=7,
                    output_format="json",
                )
            )

        payload = json.loads(raw)
        self.assertNotIn("warnings", payload)
        self.assertIn('messageSender contains "@example.com"', captured["script"])

    def test_internet_message_id_builds_exact_header_condition(self):
        captured = {}

        def fake_run(script, timeout=180):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            raw = self._run(
                search_tools.search_emails(
                    account="Work",
                    internet_message_id="reply@example.com",
                    sender="news@example.com",
                    recent_days=7,
                    output_format="json",
                )
            )

        payload = json.loads(raw)
        self.assertNotIn("warnings", payload)
        script = captured["script"]
        self.assertIn("set internetMessageId to message id of aMessage", script)
        self.assertIn('internetMessageId is "<reply@example.com>"', script)
        self.assertIn('internetMessageId is "reply@example.com"', script)


class GetEmailThreadMessageIdTests(unittest.TestCase):
    """Phase 2: get_email_thread(message_id=...) derives subject from anchor."""

    def test_get_email_thread_message_id_fetches_anchor_then_scans(self):
        anchor_line = (
            "12345|||msg@example.com|||Re: Budget Review|||alice@example.com|||"
            "INBOX|||Work|||false|||2026-01-15T10:00:00||||||"
        )
        captured: list[str] = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "whose id is 12345" in script:
                return anchor_line
            return "EMAIL THREAD VIEW"

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_thread(
                account="Work",
                message_id="12345",
                max_messages=10,
                recent_days=7,
            )

        self.assertIn("EMAIL THREAD VIEW", result)
        self.assertEqual(len(captured), 2)
        thread_script = captured[1]
        self.assertIn("Budget Review", thread_script)

    def test_get_email_thread_requires_subject_or_message_id(self):
        result = search_tools.get_email_thread(account="Work", recent_days=7)
        self.assertIn("message_id or subject_keyword", result)


class ReplyStateAnnotationTests(unittest.TestCase):
    """15-field row parsing + has_draft/draft_scan wiring across
    search_emails, get_email_by_id, get_email_by_ids, and get_email_thread
    (JSON mode). See tasks/active/reply-state-annotation/plan-2026-07-10.md.
    Pure-function coverage of the shared annotation/capping/fail-open logic
    itself (search now routes through the same helper as list_inbox_emails /
    inbox_dashboard, keyed by ``received_date`` via ``date_field``) lives in
    tests/inbox/test_reply_state_wiring.py; these tests only prove each
    search tool wires it up correctly end to end."""

    def _run(self, coro):
        return asyncio.run(coro)

    @staticmethod
    def _route_drafts(drafts_raw, other_raw, sent_raw="SCANNED|||0\nTOTAL|||0"):
        """Return (fake_run, calls) routing drafts-snapshot scripts to
        *drafts_raw* (detected via the ``"DRAFT|||"`` literal every
        ``build_drafts_snapshot_script`` output contains) and everything
        else to *other_raw* (str or one-arg callable)."""
        calls: list[str] = []

        def fake_run(script, timeout=None):
            calls.append(script)
            if '"DRAFT|||"' in script:
                return drafts_raw
            if "sentMailbox" in script:
                return sent_raw
            return other_raw(script) if callable(other_raw) else other_raw

        return fake_run, calls

    # ------------------------------------------------------------------
    # Parser: 15-field was_replied_to + 14-field row tolerance
    # ------------------------------------------------------------------

    def test_parse_search_records_reads_was_replied_to_true(self):
        from apple_mail_mcp.tools.search.records import _parse_search_records

        records, _errors = _parse_search_records(_record_line_ws(1, "Subj", was_replied_to=True))
        self.assertTrue(records[0]["was_replied_to"])

    def test_parse_search_records_reads_was_replied_to_false(self):
        from apple_mail_mcp.tools.search.records import _parse_search_records

        records, _errors = _parse_search_records(_record_line_ws(1, "Subj", was_replied_to=False))
        self.assertFalse(records[0]["was_replied_to"])

    def test_parse_search_records_tolerates_14_field_row(self):
        """An older-shaped row (pre-15th-field, e.g. a stale mock) must not
        crash the parser; was_replied_to simply defaults to False."""
        from apple_mail_mcp.tools.search.records import _parse_search_records

        records, _errors = _parse_search_records(_thread_record_line(1, "Legacy row"))
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]["was_replied_to"])

    def test_parse_search_records_tolerates_short_9_field_row(self):
        from apple_mail_mcp.tools.search.records import _parse_search_records

        records, _errors = _parse_search_records(_record_line(1, "Short row"))
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]["was_replied_to"])

    # ------------------------------------------------------------------
    # search_emails: exclude_drafted / include_draft_state / fail-open
    # ------------------------------------------------------------------

    def test_search_emails_exclude_drafted_filters_matching_records(self):
        search_raw = "\n".join(
            [
                _record_line_ws(
                    1,
                    "Budget Update",
                    internet_message_id="<a@example.com>",
                    sender="alice@example.com",
                    received_date="2026-05-01T09:00:00",
                ),
                _record_line_ws(
                    2,
                    "Other Topic",
                    internet_message_id="<b@example.com>",
                    sender="bob@example.com",
                    received_date="2026-05-01T09:00:00",
                ),
            ]
        )
        drafts_raw = "DRAFT|||Re: Budget Update|||alice@example.com|||2026-05-01T10:00:00|||\nCOUNT|||1\nTOTAL|||1"
        fake_run, calls = self._route_drafts(drafts_raw, search_raw)

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = self._run(
                search_tools.search_emails(
                    account="Work",
                    recent_days=2.0,
                    output_format="json",
                    exclude_drafted=True,
                )
            )

        payload = json.loads(result)
        self.assertEqual([item["subject"] for item in payload["items"]], ["Other Topic"])
        self.assertEqual(payload["draft_scan"]["status"], "ok")
        self.assertEqual(
            payload["draft_scan"]["accounts"],
            [{"account": "Work", "status": "ok", "scanned": 1, "total": 1, "truncated": False}],
        )
        # One search, one Drafts snapshot, and one Sent-reply snapshot.
        self.assertEqual(len(calls), 3)

    def test_search_emails_include_draft_state_false_skips_drafts_scan(self):
        search_raw = _record_line_ws(1, "Something", sender="alice@example.com")
        fake_run, calls = self._route_drafts("COUNT|||0", search_raw)

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = self._run(
                search_tools.search_emails(
                    account="Work",
                    recent_days=2.0,
                    output_format="json",
                    include_draft_state=False,
                )
            )

        payload = json.loads(result)
        self.assertEqual(
            payload["draft_scan"],
            {"status": "skipped", "scanned": 0, "total": 0, "truncated": False, "accounts": []},
        )
        self.assertIsNone(payload["items"][0]["has_draft"])
        # Search + Sent-reply snapshot; no Drafts-snapshot call.
        self.assertEqual(len(calls), 2)

    def test_search_emails_json_sent_match_sets_composite_reply_state(self):
        search_raw = _record_line_ws(
            1,
            "Budget Update",
            internet_message_id="<budget@example.com>",
            was_replied_to=False,
        )
        fake_run, _calls = self._route_drafts(
            "COUNT|||0\nTOTAL|||0",
            search_raw,
            sent_raw="REPLY|||<budget@example.com>\nSCANNED|||1\nTOTAL|||1",
        )

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = self._run(
                search_tools.search_emails(
                    account="Work", recent_days=2.0, output_format="json", include_draft_state=False
                )
            )

        payload = json.loads(result)
        item = payload["items"][0]
        self.assertFalse(item["mail_was_replied_to"])
        self.assertTrue(item["has_sent_reply"])
        self.assertTrue(item["reply_state"])

    def test_search_emails_text_skips_sent_scan_unless_reply_option_consumes_it(self):
        search_raw = _record_line_ws(1, "Budget Update", internet_message_id="<budget@example.com>")
        fake_run, calls = self._route_drafts("COUNT|||0\nTOTAL|||0", search_raw)

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            self._run(
                search_tools.search_emails(
                    account="Work", recent_days=2.0, output_format="text", include_draft_state=False
                )
            )

        self.assertEqual(len(calls), 1)
        self.assertNotIn("sentMailbox", calls[0])

    def test_search_emails_text_reports_truncated_sent_evidence_when_filter_uses_it(self):
        search_raw = _record_line_ws(1, "Budget Update", internet_message_id="<budget@example.com>")
        fake_run, _calls = self._route_drafts(
            "COUNT|||0\nTOTAL|||0",
            search_raw,
            sent_raw="SCANNED|||10\nTOTAL|||25",
        )

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = self._run(
                search_tools.search_emails(
                    account="Work",
                    recent_days=2.0,
                    output_format="text",
                    exclude_replied=True,
                    include_draft_state=False,
                )
            )

        self.assertIn("WARNING: Sent reply-state scan examined 10 of 25", result)
        self.assertIn("unknown reply state", result)

    def test_search_emails_draft_scan_error_fails_open(self):
        search_raw = _record_line_ws(1, "Something", sender="alice@example.com")

        def fake_run(script, timeout=None):
            if '"DRAFT|||"' in script:
                raise RuntimeError("AppleScript error: -1728")
            return search_raw

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = self._run(
                search_tools.search_emails(
                    account="Work",
                    recent_days=2.0,
                    output_format="json",
                    exclude_drafted=True,
                )
            )

        payload = json.loads(result)
        # Fail-open: a scan error never excludes anything on has_draft.
        self.assertEqual(len(payload["items"]), 1)
        self.assertIsNone(payload["items"][0]["has_draft"])
        self.assertEqual(payload["draft_scan"]["status"], "error")

    def test_search_emails_text_mode_shows_has_draft_marker(self):
        search_raw = _record_line_ws(
            1,
            "Budget Update",
            internet_message_id="<a@example.com>",
            sender="alice@example.com",
            received_date="2026-05-01T09:00:00",
        )
        drafts_raw = "DRAFT|||Re: Budget Update|||alice@example.com|||2026-05-01T10:00:00|||\nCOUNT|||1\nTOTAL|||1"
        fake_run, _calls = self._route_drafts(drafts_raw, search_raw)

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = self._run(
                search_tools.search_emails(
                    account="Work",
                    recent_days=2.0,
                    output_format="text",
                )
            )

        self.assertIn("[HAS DRAFT] Budget Update", result)

    # ------------------------------------------------------------------
    # get_email_by_id / get_email_by_ids annotation
    # ------------------------------------------------------------------

    def test_get_email_by_id_annotates_has_draft_true(self):
        record = _record_line_ws(
            12345,
            "Budget Update",
            internet_message_id="<a@example.com>",
            sender="alice@example.com",
            received_date="2026-05-01T09:00:00",
        )
        drafts_raw = "DRAFT|||Re: Budget Update|||alice@example.com|||2026-05-01T10:00:00|||\nCOUNT|||1\nTOTAL|||1"
        fake_run, calls = self._route_drafts(drafts_raw, record)

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_by_id(
                account="Work",
                message_id="12345",
                output_format="json",
            )

        payload = json.loads(result)
        self.assertTrue(payload["item"]["has_draft"])
        self.assertEqual(payload["draft_scan"]["status"], "ok")
        self.assertEqual(len(calls), 3)

    def test_get_email_by_id_include_draft_state_false_skips_scan(self):
        record = _record_line_ws(12345, "Something")
        fake_run, calls = self._route_drafts("COUNT|||0", record)

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_by_id(
                account="Work",
                message_id="12345",
                output_format="json",
                include_draft_state=False,
            )

        payload = json.loads(result)
        self.assertIsNone(payload["item"]["has_draft"])
        self.assertEqual(payload["draft_scan"]["status"], "skipped")
        self.assertEqual(len(calls), 2)

    def test_get_email_by_id_json_sent_match_sets_composite_reply_state(self):
        record = _record_line_ws(
            12345,
            "Budget Update",
            internet_message_id="<budget@example.com>",
            was_replied_to=False,
        )
        fake_run, _calls = self._route_drafts(
            "COUNT|||0\nTOTAL|||0",
            record,
            sent_raw="REPLY|||<budget@example.com>\nSCANNED|||1\nTOTAL|||1",
        )

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_by_id(
                account="Work",
                message_id="12345",
                output_format="json",
                include_draft_state=False,
            )

        payload = json.loads(result)
        item = payload["item"]
        self.assertTrue(item["has_sent_reply"])
        self.assertTrue(item["reply_state"])

    def test_get_email_by_id_text_skips_sent_scan(self):
        record = _record_line_ws(12345, "Budget Update", internet_message_id="<budget@example.com>")
        fake_run, calls = self._route_drafts("COUNT|||0\nTOTAL|||0", record)

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            search_tools.get_email_by_id(
                account="Work",
                message_id="12345",
                output_format="text",
                include_draft_state=False,
            )

        self.assertEqual(len(calls), 1)
        self.assertNotIn("sentMailbox", calls[0])

    def test_get_email_by_ids_annotates_has_draft_across_items(self):
        records_raw = "\n".join(
            [
                _record_line_ws(
                    101,
                    "Budget Update",
                    internet_message_id="<a@example.com>",
                    sender="alice@example.com",
                    received_date="2026-05-01T09:00:00",
                ),
                _record_line_ws(
                    202,
                    "Other Topic",
                    internet_message_id="<b@example.com>",
                    sender="bob@example.com",
                    received_date="2026-05-01T09:00:00",
                ),
            ]
        )
        drafts_raw = "DRAFT|||Re: Budget Update|||alice@example.com|||2026-05-01T10:00:00|||\nCOUNT|||1\nTOTAL|||1"
        fake_run, calls = self._route_drafts(drafts_raw, records_raw)

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_by_ids(
                account="Work",
                message_ids=["101", "202"],
                output_format="json",
            )

        payload = json.loads(result)
        has_draft_by_subject = {item["subject"]: item["has_draft"] for item in payload["items"]}
        self.assertEqual(has_draft_by_subject, {"Budget Update": True, "Other Topic": False})
        self.assertEqual(payload["draft_scan"]["status"], "ok")
        # One by-ids chunk, one Drafts snapshot, and one Sent-reply snapshot.
        self.assertEqual(len(calls), 3)

    # ------------------------------------------------------------------
    # get_email_thread: JSON-mode annotation, text-mode native marker only
    # ------------------------------------------------------------------

    def test_get_email_thread_json_annotates_has_draft(self):
        thread_line = _thread_record_line(
            401,
            "Re: Budget Review",
            internet_message_id="<reply@example.com>",
            sender="alice@example.com",
            received_date="2026-05-01T09:00:00",
        )
        drafts_raw = "DRAFT|||Budget Review|||alice@example.com|||2026-05-01T10:00:00|||\nCOUNT|||1\nTOTAL|||1"
        fake_run, calls = self._route_drafts(drafts_raw, "THREAD_STRATEGY|||subject\n" + thread_line)

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_thread(
                account="Work",
                subject_keyword="Budget Review",
                max_messages=10,
                recent_days=7,
                output_format="json",
            )

        payload = json.loads(result)
        self.assertTrue(payload["items"][0]["has_draft"])
        self.assertEqual(payload["draft_scan"]["status"], "ok")
        # One thread listing, one Drafts snapshot, and one Sent-reply snapshot.
        self.assertEqual(len(calls), 3)

    def test_get_email_thread_text_mode_never_calls_drafts_scan(self):
        """Thread text output is rendered entirely inside AppleScript (no
        parsed-record pass-through), so has_draft/draft_scan are JSON-only
        for this tool; text mode must never fire the extra Drafts-scan call."""
        calls: list[str] = []

        def fake_run(script, timeout=None):
            calls.append(script)
            return "EMAIL THREAD VIEW"

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            result = search_tools.get_email_thread(
                account="Work",
                subject_keyword="Budget Review",
                max_messages=10,
                recent_days=7,
                output_format="text",
            )

        self.assertIn("EMAIL THREAD VIEW", result)
        self.assertEqual(len(calls), 1)
        self.assertNotIn('"DRAFT|||"', calls[0])

    def test_get_email_thread_text_mode_shows_native_replied_marker(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "REPLIED_LINE"

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            search_tools.get_email_thread(
                account="Work",
                subject_keyword="Budget Review",
                max_messages=10,
                recent_days=7,
            )

        # The display loop reads the native was-replied property and only
        # needs a wasRepliedToken check, no extra AppleScript round trip.
        self.assertIn("was replied to of aMessage", captured["script"])
        self.assertIn('if wasRepliedToken is "true" then set repliedMarker to "[REPLIED] "', captured["script"])


class SubjectFilterUnboundReferenceTests(unittest.TestCase):
    """AGENTIC-2344: the subject fast path must filter on a bound loop variable.

    v3.11.6 shipped ``if subject contains "x" then`` inside
    ``repeat with aMessage in candidateMessages``. Inside a ``whose`` clause a
    bare property reference is legal because ``whose`` supplies the implicit
    target; inside an explicit ``repeat`` loop there is no implicit target, so
    Mail raises -1728 ``Can't get subject.`` on every iteration. The loop's
    ``try`` swallowed it, so subject search returned 0 results on every account
    with ``has_more: false`` and no errors.

    Unit tests mock ``subprocess.run`` and never execute the AppleScript, and
    ``osacompile`` accepts the fragment because it is syntactically valid, so
    text-level invariants on the emitted script are the only automatable guard.
    """

    # Mail properties that are only resolvable via `of <var>` or inside a
    # `whose` clause. A bare one on the left of a comparison is unbound.
    _BARE_PROPERTY = re.compile(
        r"(?<![A-Za-z0-9_])"
        r"(subject|sender|content|read status|date received|date sent|message id|was replied to)"
        r"\s+(contains|is|starts with|ends with|does not contain)\b"
    )

    # One mailbox diagnostic, exactly as `script._SCAN_FAILURE_REPORT` emits it
    # when every candidate read threw.
    _SCAN_FAILURE_LINE = (
        "ERROR_MAILBOX|||Inbox|||per-message scan failed for 50 of 50 scanned message(s); results are incomplete"
    )

    @staticmethod
    def _fast_path_script(subject_terms):
        """Build the subject-only fast-path script (no sender/body/date_to/etc.)."""
        script, _body_capped, _mb_capped = _build_search_script(
            account="Work",
            mailbox="Inbox",
            subject_terms=subject_terms,
            sender=None,
            has_attachments=None,
            read_status="all",
            date_from="2026-08-01",
            date_to=None,
            include_content=False,
            content_length=0,
            offset=0,
            limit=20,
            body_text=None,
            recent_days=2.0,
        )
        return script

    @staticmethod
    def _candidate_loop(script):
        """Return just the bounded candidate `repeat` loop body."""
        start = script.index("repeat with aMessage in candidateMessages")
        end = script.index("end repeat", start)
        return script[start:end]

    @staticmethod
    def _search_subject(applescript_result, **kwargs):
        """Run the broken call shape — `search_emails(subject_keyword=...)` with no
        other filter — against a stubbed AppleScript result.

        Returns ``(tool output, the script that was sent to Mail)``.
        """
        captured = {}

        def fake_run(script, timeout=120):
            sent_result = _empty_sent_reply_snapshot(script)
            if sent_result is not None:
                return sent_result
            captured["script"] = script
            return applescript_result

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            output = _run(
                search_tools.search_emails(
                    account="Work",
                    subject_keyword="Invoice",
                    limit=20,
                    include_draft_state=False,
                    **kwargs,
                )
            )
        return output, captured["script"]

    def test_fast_path_filters_on_bound_variable(self):
        """The condition must name `messageSubject`, the variable the line above binds."""
        script = self._fast_path_script(["Invoice"])
        loop = self._candidate_loop(script)

        self.assertIn("set messageSubject to subject of aMessage", loop)
        self.assertIn('if (messageSubject contains "Invoice") and messageDate >= fromDate then', loop)

    def test_fast_path_condition_operands_are_all_locally_bound(self):
        """Regression guard: every operand in the fast-path condition must be a
        variable the loop itself assigned with `set <name> to ...`.

        Before the AGENTIC-2344 fix the subject operand was the bare property
        `subject`, which no `set` statement binds, so this assertion fails on the
        shipped v3.11.6 script and passes on the fixed one. AGENTIC-2356 added the
        caller's date floor to the same condition, so the date clause is split off
        explicitly and `messageDate` is held to the identical binding rule — a bare
        `date received >= fromDate` is the same -1728 crash in a different operand.
        """
        script = self._fast_path_script(["Invoice", "Receipt"])
        loop = self._candidate_loop(script)

        condition_match = re.search(r"^\s*if (.+?) then$", loop, re.MULTILINE)
        if condition_match is None:
            self.fail(f"no `if … then` condition found in the fast-path loop:\n{loop}")
        condition = condition_match.group(1)
        bound_vars = set(re.findall(r"\bset ([A-Za-z_][A-Za-z0-9_]*) to ", loop))

        # `_fast_path_script` passes a `date_from`, so the emitted condition is
        # `(<subject or-chain>) and messageDate >= fromDate`. Peel the trailing
        # date clause off rather than loosening the operand check.
        subject_clause, _sep, date_clause = condition.rpartition(" and ")
        self.assertEqual(
            date_clause,
            "messageDate >= fromDate",
            f"expected the fast-path condition to end in the date floor, got {condition!r}",
        )
        self.assertIn(
            "messageDate",
            bound_vars,
            "`messageDate` must be bound by a `set messageDate to ...` line inside the same "
            "loop; a bare `date received >= fromDate` has no implicit target inside "
            f"`repeat with ... in ...` and throws -1728 on every message. Loop:\n{loop}",
        )

        operands = [clause.split(" contains ")[0].strip() for clause in subject_clause.strip("()").split(" or ")]
        self.assertEqual(len(operands), 2, f"expected one clause per subject term, got {condition!r}")
        for operand in operands:
            self.assertIn(
                operand,
                bound_vars,
                f"condition operand {operand!r} is not bound by any `set` in the loop; "
                f"a bare Mail property throws -1728 inside `repeat with ... in ...`. Loop:\n{loop}",
            )

    def test_fast_path_applies_the_requested_date_floor(self):
        """AGENTIC-2356: the fast path interpolated `subject_checks` alone, so the
        `messageDate >= fromDate` entry that `per_msg_conditions` already carried
        was silently dropped — a subject-only search scanned the newest <=50
        messages regardless of age while `search_emails` still reported
        `searched_from`. The date read must be bound inside the loop and the
        comparison must reach the emitted condition."""
        loop = self._candidate_loop(self._fast_path_script(["Invoice"]))

        self.assertIn("set messageDate to date received of aMessage", loop)
        self.assertIn("messageDate >= fromDate", loop)

    def test_fast_path_keeps_the_early_date_break(self):
        """The early break is what makes the extra date read affordable: Mail hands
        back mailbox messages newest-first, so the first message older than
        `fromDate` ends the useful part of the slice."""
        loop = self._candidate_loop(self._fast_path_script(["Invoice"]))

        self.assertIn("if messageDate < fromDate then exit repeat", loop)
        # The break must precede the subject read, or it saves nothing.
        self.assertLess(
            loop.index("if messageDate < fromDate then exit repeat"),
            loop.index("set messageSubject to subject of aMessage"),
        )

    def test_fast_path_still_avoids_sender_read_status_and_content_reads(self):
        """The date fix must not turn the fast path into the general loop: reading
        sender / read status / content per candidate is what it exists to avoid on
        large Exchange inboxes."""
        loop = self._candidate_loop(self._fast_path_script(["Invoice"]))

        self.assertNotIn("sender of aMessage", loop)
        self.assertNotIn("read status of aMessage", loop)
        self.assertNotIn("content of aMessage", loop)

    def test_fast_path_without_date_from_never_references_from_date(self):
        """`_build_search_script` is also called from `manage/` and `analytics/`
        with `date_from=None`. The emitted script only declares `fromDate` when a
        date was passed (`records._build_applescript_date` returns "" for a falsy
        date), so both the date read and the comparison must stay conditional —
        referencing an undeclared `fromDate` throws on every message."""
        script, _body_capped, _mb_capped = _build_search_script(
            account="Work",
            mailbox="Inbox",
            subject_terms=["Invoice"],
            sender=None,
            has_attachments=None,
            read_status="all",
            date_from=None,
            date_to=None,
            include_content=False,
            content_length=0,
            offset=0,
            limit=20,
            body_text=None,
            recent_days=0,
        )

        loop = self._candidate_loop(script)
        self.assertNotIn("fromDate", script)
        self.assertNotIn("set messageDate to date received of aMessage", loop)
        # The subject filter itself still has to be there and still bound.
        self.assertIn('if (messageSubject contains "Invoice") then', loop)

    def test_no_bare_mail_property_comparison_outside_whose(self):
        """No emitted line may compare a bare Mail property unless it is a `whose` clause."""
        for terms in (["Invoice"], ["Invoice", "Receipt"]):
            script = self._fast_path_script(terms)
            for lineno, line in enumerate(script.splitlines(), start=1):
                if "whose" in line:
                    continue
                match = self._BARE_PROPERTY.search(line)
                if match is not None:
                    self.fail(
                        f"line {lineno} compares bare property {match.group(1)!r} outside a "
                        f"`whose` clause (unbound at runtime, -1728): {line.strip()!r}"
                    )

    def test_subject_only_search_returns_matches(self):
        """End-to-end regression for the exact call shape that returned 0 results:
        subject term only — no sender, sender_exact, sender_domain,
        internet_message_id, attachment filter, read-status filter, body search,
        or date_to."""
        output, script = self._search_subject(_record_line_ws(901, "Invoice 901 for August"), output_format="json")
        response = json.loads(output)

        self.assertEqual(response["returned"], 1)
        self.assertEqual(response["items"][0]["subject"], "Invoice 901 for August")
        # The fast path is the one that was broken; confirm we took it and that
        # its condition is bound to the loop variable. The default 48h window
        # supplies a `date_from`, so the auto-applied floor rides along
        # (AGENTIC-2356).
        self.assertIn("set messageSubject to subject of aMessage", script)
        self.assertIn('(messageSubject contains "Invoice") and messageDate >= fromDate', script)

    def test_scan_loops_count_per_message_failures(self):
        """Amplifier guard: a swallowed per-message throw must be counted, and a
        non-zero count must emit an ERROR_MAILBOX diagnostic rather than letting
        an all-failed scan look like a legitimate empty result."""
        script = self._fast_path_script(["Invoice"])

        self.assertIn("set scanReadFailures to 0", script)
        self.assertIn("set scanReadFailures to scanReadFailures + 1", script)
        self.assertIn("if scanReadFailures > 0 then", script)
        self.assertIn('"ERROR_MAILBOX|||" & mailboxName & "|||per-message scan failed for "', script)
        # The counting arm replaces the bare `try ... end try` swallow.
        loop = self._candidate_loop(script)
        self.assertIn("on error", loop)

    def test_scan_failure_count_surfaces_in_json_error_details(self):
        """The counter reaches the caller through the existing error channel."""
        output, _script = self._search_subject(self._SCAN_FAILURE_LINE, output_format="json")
        response = json.loads(output)

        self.assertEqual(response["returned"], 0)
        self.assertIn("error_details", response)
        detail = response["error_details"][0]
        self.assertEqual(detail["mailbox"], "Inbox")
        self.assertEqual(detail["type"], "mailbox_error")
        self.assertIn("per-message scan failed for 50 of 50", detail["message"])

    def test_scan_failure_count_surfaces_in_text_output(self):
        """Text is the default output_format, so an all-failed scan must not
        render as a clean `FOUND: 0` there either."""
        result, _script = self._search_subject(self._SCAN_FAILURE_LINE)

        self.assertIn("FOUND: 0 matching email(s)", result)
        self.assertIn("PARTIAL: 1 mailbox issue(s)", result)
        self.assertIn("per-message scan failed for 50 of 50", result)


if __name__ == "__main__":
    unittest.main()
