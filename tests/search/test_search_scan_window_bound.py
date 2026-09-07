"""A ``date_from`` window must size the scan, and the bound must be reported honestly.

AGENTIC-2794. Two defects, reproduced live against a 24,000-message Exchange
mailbox before this file existed:

1.  ``script._build_search_script`` widened its scan only for ``recent_days``.
    A caller who bounded the window with an explicit ``date_from`` (and
    therefore passed no ``recent_days``) got ``scan_cap = limit + offset + 1``,
    so a *precise* query with a *small* limit received the *smallest possible*
    scan. Measured: one exact Internet Message-ID, ``date_from`` 10 days back,
    ``limit=10`` bound ``messages 1 thru 11`` and returned zero rows; the same
    query at ``limit=120`` bound 50 and found the message.

2.  ``scan_ceiling_applied`` compared the post-clamp ``scan_cap`` against
    ``base_cap``, which is False in exactly that regime — so the in-band
    ``SCAN_CEILING|||`` marker was never spliced and the caller received
    ``returned: 4, has_more: false`` with no warning that only the newest 21 of
    24,000 messages had been examined. The smaller, more precise query produced
    the more confident wrong answer.

Companion fix pinned here: ``records._build_search_response`` hardcoded
``SEARCH_HARD_CEILING`` in the ``scan_ceiling`` field and in the warning text,
so the message could claim "the newest 50" for a scan that actually stopped at
the body-search cap of 25. The marker row already carries the real bound.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from datetime import datetime, timedelta

from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.tools.search.records import _build_search_response, _parse_search_records
from apple_mail_mcp.tools.search.scan_cap import compute_search_scan_cap, window_days_from_date_from
from apple_mail_mcp.tools.search.script import _build_search_script

CEILING = SCAN_BOUNDS["SEARCH_HARD_CEILING"]
BODY_CAP = SCAN_BOUNDS["BODY_SEARCH_AUTO_CAP"]

_SCAN_BOUND_RE = re.compile(r"set scanUpperBound to (\d+)")


def _days_ago(days: int) -> str:
    """A real ``YYYY-MM-DD`` *days* in the past, so the test cannot age out."""
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


_BUILDER_DEFAULTS: dict[str, object] = {
    "account": "Acct",
    "mailbox": "INBOX",
    "subject_terms": None,
    "sender": None,
    "has_attachments": None,
    "read_status": "all",
    "date_from": None,
    "date_to": None,
    "include_content": False,
    "content_length": 300,
    "offset": 0,
    "limit": 100,
    "body_text": None,
    "timeout": 180,
}


def _build(**overrides: object) -> str:
    kwargs = {**_BUILDER_DEFAULTS, **overrides}
    script, _body_capped, _mb_capped = _build_search_script(**kwargs)  # type: ignore[arg-type]
    return script


def _scan_bound(script: str) -> int:
    match = _SCAN_BOUND_RE.search(script)
    assert match is not None, "generated script has no `set scanUpperBound to N` line"
    return int(match.group(1))


class DateFromSizesTheScanTests(unittest.TestCase):
    """Fix 1: the window the caller supplied sizes the slice, whichever name it has."""

    def test_explicit_date_from_widens_a_small_page(self) -> None:
        # The reported repro: limit=10, offset=0 -> base_cap 12. Before the fix
        # the scan was exactly that, so the needle 40 messages back was never
        # looked at.
        script = _build(limit=10, date_from=_days_ago(10), date_from_explicit=True)
        base_cap = 10 + 1 + 0
        assert _scan_bound(script) > base_cap
        assert _scan_bound(script) == CEILING

    def test_the_same_page_size_no_longer_depends_on_the_limit(self) -> None:
        # The defect's signature was that a *larger* page found the message a
        # *smaller* page missed. Both page sizes must now scan the same depth.
        date_from = _days_ago(10)
        small = _scan_bound(_build(limit=10, date_from=date_from, date_from_explicit=True))
        large = _scan_bound(_build(limit=120, date_from=date_from, date_from_explicit=True))
        assert small == large == CEILING

    def test_widened_scan_arms_the_ceiling_marker(self) -> None:
        # Fix 2. Previously absent in exactly this case, so the caller got
        # `has_more: false` with no `scan_ceiling_reached`, no `scan_bounded`
        # and no `warnings`.
        script = _build(limit=10, date_from=_days_ago(10), date_from_explicit=True)
        assert "SCAN_CEILING|||" in script
        # The marker is still gated on runtime saturation, so a mailbox smaller
        # than the slice stays silent.
        assert "if (count of candidateMessages) is greater than or equal to scanUpperBound then" in script

    def test_recent_days_path_is_unchanged(self) -> None:
        # `recent_days` already widened the scan; the shared helper must not
        # have retuned it. compute_scan_upper_bound(2.0) = 40 + 2*3 = 46.
        assert _scan_bound(_build(limit=20, recent_days=2.0, sender="boss@example.com")) == 46

    def test_recent_days_wins_when_both_are_present(self) -> None:
        # `search_emails` derives `date_from` from `recent_days`, so the two
        # spell the same window; consulting `date_from` as well must not widen
        # past what `recent_days` already asked for.
        script = _build(limit=20, recent_days=2.0, date_from=_days_ago(2), sender="boss@example.com")
        assert _scan_bound(script) == 46


class NoWindowStaysNarrowTests(unittest.TestCase):
    """Silence is the contract when nothing was actually cut."""

    def test_small_page_without_a_window_neither_widens_nor_arms(self) -> None:
        # base_cap = 6, no window to widen for, nothing clamped. Saturating a
        # slice this size only means "there is a next page", which `has_more`
        # already says, so a warning here would be crying wolf.
        script = _build(limit=5)
        assert _scan_bound(script) == 6
        assert "SCAN_CEILING|||" not in script

    def test_subject_only_header_search_keeps_its_narrow_cap(self) -> None:
        # Deliberate: narrow header searches are needle lookups, and binding
        # hundreds of recent messages to prove a no-hit subject does not exist
        # can exceed wrapper timeouts on large Exchange accounts. The fast path
        # stays bounded by the caller's requested page even with a wide window.
        script = _build(limit=5, subject_terms=["invoice"], date_from=_days_ago(120), date_from_explicit=True)
        assert _scan_bound(script) == 6

    def test_subject_only_fast_path_is_still_clamped_by_the_hard_ceiling(self) -> None:
        # Staying narrow is not the same as staying unbounded: a huge page on
        # the fast path is still cut to the ceiling, and that cut still arms.
        script = _build(limit=600, subject_terms=["invoice"], date_from=_days_ago(120), date_from_explicit=True)
        assert _scan_bound(script) == CEILING
        assert "SCAN_CEILING|||" in script

    def test_response_without_ceiling_rows_carries_no_ceiling_keys(self) -> None:
        payload = json.loads(
            _build_search_response(
                [{"message_id": "1"}],
                offset=0,
                limit=100,
                sort="date_desc",
                output_format="json",
            )
        )
        for key in ("scan_ceiling_reached", "scan_ceiling", "scan_ceiling_mailboxes", "scan_bounded", "warnings"):
            assert key not in payload


class MalformedDateFromTests(unittest.TestCase):
    """A bad date must not become a traceback from the cap math."""

    def test_unparseable_date_from_degrades_to_no_window(self) -> None:
        for bad in ("not-a-date", "2026-13-45", "", "05/20/2026", "2026-05-20T10:00:00"):
            with self.subTest(date_from=bad):
                assert window_days_from_date_from(bad) == 0.0

    def test_future_date_from_is_not_a_window(self) -> None:
        assert window_days_from_date_from("2099-01-01") == 0.0

    def test_cap_math_falls_back_to_the_page_for_a_bad_date(self) -> None:
        plan = compute_search_scan_cap(
            base_cap=12,
            recent_days=0.0,
            date_from="not-a-date",
            subject_only_header_search=False,
            use_body_search=False,
            date_from_explicit=True,
        )
        assert plan.scan_cap == 12
        assert plan.scan_ceiling_applied is False

    def test_the_date_format_complaint_still_comes_from_the_date_validator(self) -> None:
        # The caller-facing error is unchanged and still names the format; the
        # cap math must not have moved that failure earlier or reworded it.
        with self.assertRaises(ValueError) as caught:
            _build(limit=10, date_from="not-a-date", date_from_explicit=True)
        assert "Use YYYY-MM-DD" in str(caught.exception)


class ReportedBoundTests(unittest.TestCase):
    """Fix 3: the response reports the bound the scan hit, not a constant."""

    @staticmethod
    def _ceiling_row(mailbox: str, bound: int) -> str:
        return f"SCAN_CEILING|||{mailbox}|||{bound}"

    def _payload(self, *rows: str) -> dict[str, object]:
        _records, mailbox_errors = _parse_search_records("\n".join(rows))
        return json.loads(  # type: ignore[no-any-return]
            _build_search_response(
                [],
                offset=0,
                limit=100,
                sort="date_desc",
                output_format="json",
                error_details=mailbox_errors,
            )
        )

    def test_bound_below_the_hard_ceiling_is_reported_verbatim(self) -> None:
        # A body-search scan stops at BODY_SEARCH_AUTO_CAP (25). Reporting 50
        # here told the caller the scan looked twice as far as it did.
        payload = self._payload(self._ceiling_row("INBOX", BODY_CAP))
        assert payload["scan_ceiling"] == BODY_CAP
        assert f"newest {BODY_CAP} message(s)" in " ".join(payload["warnings"])  # type: ignore[index]

    def test_hard_ceiling_bound_still_reports_the_hard_ceiling(self) -> None:
        payload = self._payload(self._ceiling_row("INBOX", CEILING))
        assert payload["scan_ceiling"] == CEILING

    def test_differing_bounds_report_the_maximum_and_name_every_mailbox(self) -> None:
        payload = self._payload(
            self._ceiling_row("INBOX", BODY_CAP),
            self._ceiling_row("Archive", CEILING),
        )
        assert payload["scan_ceiling"] == CEILING
        assert payload["scan_ceiling_mailboxes"] == ["Archive", "INBOX"]

    def test_an_entry_without_a_bound_falls_back_to_the_constant(self) -> None:
        # Defensive: an older-shaped detail dict (no numeric field) must still
        # produce a number rather than crashing the response.
        payload = json.loads(
            _build_search_response(
                [],
                offset=0,
                limit=100,
                sort="date_desc",
                output_format="json",
                error_details=[{"account": "Acct", "mailbox": "INBOX", "type": "scan_ceiling", "message": "bounded"}],
            )
        )
        assert payload["scan_ceiling"] == CEILING

    def test_the_load_bearing_warning_sentences_survive(self) -> None:
        warning = " ".join(self._payload(self._ceiling_row("INBOX", BODY_CAP))["warnings"])  # type: ignore[index]
        assert "scanned window only, not the mailbox" in warning
        assert "paging cannot reach them" in warning

    def test_text_mode_reports_the_same_bound(self) -> None:
        _records, mailbox_errors = _parse_search_records(self._ceiling_row("INBOX", BODY_CAP))
        text = _build_search_response(
            [],
            offset=0,
            limit=100,
            sort="date_desc",
            output_format="text",
            error_details=mailbox_errors,
        )
        assert text.splitlines()[0].startswith("WARNING: Scan ceiling reached")
        assert f"newest {BODY_CAP} message(s)" in text


class GeneratedScriptCompilesTests(unittest.TestCase):
    """Mocked assertions pass happily on AppleScript that will not parse."""

    def test_osacompile_accepts_the_widened_arms(self) -> None:
        cases: dict[str, dict[str, object]] = {
            "date-from-small-page": {"limit": 10, "date_from": _days_ago(10), "date_from_explicit": True},
            "date-from-subject-fast-path": {
                "limit": 5,
                "subject_terms": ["invoice"],
                "date_from": _days_ago(120),
                "date_from_explicit": True,
            },
            "date-from-body-search": {"limit": 10, "date_from": _days_ago(10), "body_text": "needle"},
            "no-window-small-page": {"limit": 5},
        }
        for name, overrides in cases.items():
            with self.subTest(case=name):
                result = subprocess.run(
                    ["osacompile", "-o", "/dev/null"],
                    input=_build(**overrides),
                    text=True,
                    capture_output=True,
                )
                assert result.returncode == 0, result.stderr

    def test_no_arm_can_emit_a_zero_upper_bound(self) -> None:
        # `messages 1 thru 0` silently returns the FIRST message on all four
        # backends, so a zero bound is worse than an error.
        for overrides in ({"limit": 1}, {"limit": 1, "date_from": _days_ago(10)}, {"limit": 5, "body_text": "x"}):
            with self.subTest(overrides=str(overrides)):
                assert _scan_bound(_build(**overrides)) >= 2
