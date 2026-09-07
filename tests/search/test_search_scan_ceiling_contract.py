"""`search_emails` must not answer "that is everything" from behind the scan wall.

`script._build_search_script` clamps each mailbox scan to
``SCAN_BOUNDS["SEARCH_HARD_CEILING"]`` (50), while the tool's default ``limit``
is 100 and ``records._build_search_response`` derives ``has_more`` as
``len(records) > limit``. Fifty records against a limit of 100 therefore
produced ``has_more: false`` — an authoritative "there is no more" generated
after examining the newest 50 messages, next to a ``recent_days_applied`` that
asserted a 90-day window had been searched.

The fix does not touch ``has_more``: as a *pagination* bit it was already right,
and forcing it true whenever the ceiling fired would make it true on every page
forever (the mailbox saturates the scan every time), so a caller looping until
it went false would never stop. What was missing was the completeness fact, so
the scan now says when it stopped looking.

Companion defect covered here: the record-emit loop dropped messages that had
*already matched* the filter, because its ``try`` had no ``on error`` arm and
``collectLimit`` decrements after the append. The page came back full-shaped and
one row short with nothing to distinguish it from a genuinely smaller result.
"""

from __future__ import annotations

import json
import subprocess
import unittest

from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.tools.search.records import (
    _build_search_response,
    _mailbox_error_texts,
    _parse_search_records,
)
from apple_mail_mcp.tools.search.script import _build_search_script

CEILING = SCAN_BOUNDS["SEARCH_HARD_CEILING"]

_ROW = (
    "1|||<a@example.com>|||Subject A|||sender@example.com|||INBOX|||Acct"
    "|||false|||2026-08-01T10:00:00|||||||||||||||||false"
)


def _build(**overrides: object) -> str:
    kwargs: dict[str, object] = {
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
    kwargs.update(overrides)
    script, _body_capped, _mb_capped = _build_search_script(**kwargs)  # type: ignore[arg-type]
    return script


def _ceiling_details(*mailboxes: str) -> list[dict[str, str]]:
    return [
        {
            "account": "Acct",
            "mailbox": mb,
            "type": "scan_ceiling",
            "message": f"scan stopped at the {CEILING}-message ceiling for this mailbox",
        }
        for mb in mailboxes
    ]


class ScanCeilingMarkerEmissionTests(unittest.TestCase):
    """The marker arms only when the clamp actually costs the caller coverage."""

    def test_default_limit_arms_the_marker(self) -> None:
        # limit=100 -> base_cap 101, clamped to 50: the scan cannot reach the
        # page the caller asked for, so has_more can never be honest unaided.
        assert "SCAN_CEILING|||" in _build(limit=100)

    def test_page_that_fits_under_the_ceiling_does_not_arm_it(self) -> None:
        # limit=5 -> base_cap 6, well under the ceiling. Nothing was cut, so a
        # warning here would be crying wolf on the majority of small folders.
        assert "SCAN_CEILING|||" not in _build(limit=5)

    def test_offset_paging_past_the_ceiling_arms_the_marker(self) -> None:
        # offset=30/limit=20 -> base_cap 51, clamps back to 50. This is the
        # paging dead-end: each call re-clamps and reports has_more: false again.
        assert "SCAN_CEILING|||" in _build(offset=30, limit=20)

    def test_subject_fast_path_arms_the_marker(self) -> None:
        assert "SCAN_CEILING|||" in _build(subject_terms=["hello"], date_from="2026-01-01")

    def test_marker_fires_on_saturation_not_on_every_scan(self) -> None:
        # The guard is a runtime count, so a 12-message folder scanned in full
        # stays silent even though the builder armed the marker.
        script = _build(limit=100)
        assert "if (count of candidateMessages) is greater than or equal to scanUpperBound then" in script


class EmitLoopFailureArmTests(unittest.TestCase):
    """A message that matched the filter but failed to render must be counted."""

    def test_emit_loop_has_a_real_error_arm(self) -> None:
        # Independent of the ceiling: a matched-but-unrenderable message is
        # dropped on every page size, not just the clamped ones.
        for limit in (5, 100):
            with self.subTest(limit=limit):
                script = _build(limit=limit)
                assert "set emitReadFailures to 0" in script
                assert "set emitReadFailures to emitReadFailures + 1" in script

    def test_emit_failure_reports_against_the_matched_count(self) -> None:
        # Same wording and same in-band marker as records._read_failure_row,
        # which is the sanctioned shape for "matched but not emitted".
        script = _build(limit=100)
        assert "ERROR_MAILBOX|||" in script
        assert "matched message(s); results are incomplete" in script
        assert "count of targetMessages" in script


class GeneratedScriptStillCompilesTests(unittest.TestCase):
    def test_osacompile_accepts_the_script(self) -> None:
        # Mocked tests can pass while the emitted AppleScript is broken at
        # parse time, so every arm that the new fragments touch is compiled.
        cases = {
            "default": {"limit": 100},
            "small-page": {"limit": 5},
            "offset-page": {"offset": 30, "limit": 20},
            "subject-fast-path": {"subject_terms": ["hello"], "date_from": "2026-01-01"},
            "body-all": {"body_text": "term", "mailbox": "All", "read_status": "unread"},
            "with-content": {"include_content": True},
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


class ScanCeilingParsingTests(unittest.TestCase):
    def test_marker_parses_as_a_bound_not_an_error(self) -> None:
        # The `scan_ceiling` key is new (AGENTIC-2794): the row's numeric bound
        # is kept as data, not only inside the prose, so the response can report
        # the bound the scan actually stopped at instead of restating a
        # constant. Everything else about the entry is unchanged.
        records, mailbox_errors = _parse_search_records("\n".join([_ROW, f"SCAN_CEILING|||INBOX|||{CEILING}"]))
        assert len(records) == 1
        assert mailbox_errors == [
            {
                "mailbox": "INBOX",
                "type": "scan_ceiling",
                "scan_ceiling": str(CEILING),
                "message": (
                    f"scan stopped at the {CEILING}-message ceiling for this mailbox; "
                    "results are bounded by the scan, not by the filter"
                ),
            }
        ]

    def test_real_mailbox_errors_still_parse_alongside_it(self) -> None:
        _records, mailbox_errors = _parse_search_records(
            "\n".join([_ROW, f"SCAN_CEILING|||INBOX|||{CEILING}", "ERROR_MAILBOX|||Archive|||mailbox not found"])
        )
        by_mailbox = {e["mailbox"]: e for e in mailbox_errors}
        assert by_mailbox["Archive"].get("type") is None
        assert by_mailbox["INBOX"]["type"] == "scan_ceiling"

    def test_ceiling_entries_never_render_as_error_text(self) -> None:
        # A saturated scan on a healthy mailbox must not produce a PARTIAL: line.
        _records, mailbox_errors = _parse_search_records(
            "\n".join([_ROW, f"SCAN_CEILING|||INBOX|||{CEILING}", "ERROR_MAILBOX|||Archive|||mailbox not found"])
        )
        assert _mailbox_error_texts(mailbox_errors) == ["Archive: mailbox not found"]


class ScanCeilingResponseContractTests(unittest.TestCase):
    def test_has_more_false_at_the_wall_now_says_which_wall(self) -> None:
        # The exact reported defect: 50 records, limit 100, has_more false.
        records = [{"message_id": str(i)} for i in range(CEILING)]
        payload = json.loads(
            _build_search_response(
                records,
                offset=0,
                limit=100,
                sort="date_desc",
                output_format="json",
                error_details=_ceiling_details("INBOX"),
            )
        )
        assert payload["has_more"] is False
        assert payload["scan_ceiling_reached"] is True
        assert payload["scan_ceiling"] == CEILING
        assert payload["scan_ceiling_mailboxes"] == ["INBOX"]
        warning = " ".join(payload["warnings"])
        assert "scanned window only, not the mailbox" in warning
        assert "paging cannot reach them" in warning

    def test_ceiling_entries_are_kept_out_of_error_details(self) -> None:
        payload = json.loads(
            _build_search_response(
                [],
                offset=0,
                limit=100,
                sort="date_desc",
                output_format="json",
                error_details=[
                    *_ceiling_details("INBOX"),
                    {"account": "Acct", "mailbox": "Archive", "type": "mailbox_error", "message": "not found"},
                ],
            )
        )
        assert payload["error_details"] == [
            {"account": "Acct", "mailbox": "Archive", "type": "mailbox_error", "message": "not found"}
        ]

    def test_ceiling_only_details_leave_error_details_absent(self) -> None:
        payload = json.loads(
            _build_search_response(
                [],
                offset=0,
                limit=100,
                sort="date_desc",
                output_format="json",
                error_details=_ceiling_details("INBOX"),
            )
        )
        assert "error_details" not in payload
        assert payload["scan_ceiling_reached"] is True

    def test_multiple_saturated_mailboxes_are_named_and_deduped(self) -> None:
        payload = json.loads(
            _build_search_response(
                [],
                offset=0,
                limit=100,
                sort="date_desc",
                output_format="json",
                error_details=_ceiling_details("INBOX", "Archive", "INBOX"),
            )
        )
        assert payload["scan_ceiling_mailboxes"] == ["Archive", "INBOX"]

    def test_search_that_never_hit_the_ceiling_is_unchanged(self) -> None:
        # Silence is the contract for the common case: no new keys, no warning.
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

    def test_text_mode_leads_with_the_warning(self) -> None:
        text = _build_search_response(
            [],
            offset=0,
            limit=100,
            sort="date_desc",
            output_format="text",
            error_details=_ceiling_details("INBOX"),
        )
        assert text.splitlines()[0].startswith("WARNING: Scan ceiling reached")

    def test_text_mode_stays_quiet_without_a_ceiling(self) -> None:
        text = _build_search_response(
            [],
            offset=0,
            limit=100,
            sort="date_desc",
            output_format="text",
        )
        assert "Scan ceiling reached" not in text
