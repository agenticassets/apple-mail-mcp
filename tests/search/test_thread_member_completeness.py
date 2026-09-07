"""Regression tests: a bounded thread scan must not return a short thread quietly.

AGENTIC-2794 / AGENTIC-2796. ``get_email_thread`` sized its per-mailbox
*candidate slice* from ``max_messages`` -- the *return* bound. A nine-member
conversation split across ``Inbox`` (6) and ``Sent Items`` (3) came back with
five members and reported ``matched=5 returned=5 render_incomplete=false
candidate_scan_incomplete=false errors=null``: not a slow answer or a partial
one, a confident wrong one. Separately, ``mailbox="All"`` -- a documented
argument -- failed the anchor fetch outright with ``Mailbox not found: All``,
because "All" is meaningful to the scan and meaningless to a by-name fetch.

Two distinct properties are pinned here, and the second is the one that makes
the first survivable:

* **Completeness** -- the scan bound is derived from the ``recent_days``
  window, not from the return bound, so an ordinary call reaches the whole
  conversation.
* **Honesty** -- when a bound *does* truncate, the caller is told which
  mailbox, which bound, and which remedy. A short thread is acceptable; a
  short thread wearing a clean banner is not.

The conversation fixture lives in :mod:`tests.search.thread_fixtures`. Its
runner reads ``scanUpperBound`` back out of the generated script, so narrowing
``scan_messages`` narrows the reply for the same reason it would on a real
mailbox -- a fixture that took the bound as an argument would keep passing if
the tool emitted a different one.

All Mail I/O is mocked at ``tools.search.run_applescript`` and the subprocess
layer is poisoned, so nothing here can reach a real mailbox. The one exception
is the offline ``osacompile`` parse check, which compiles a captured script
from a temp file and never talks to Mail.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.tools import search as search_tools
from apple_mail_mcp.tools.search.records import _parse_search_records
from apple_mail_mcp.tools.search.thread_helpers import (
    THREAD_ATTACHMENT_COUNT_PREFIX,
    THREAD_DATE_FLOOR_PREFIX,
    THREAD_SCAN_CEILING_PREFIX,
    split_thread_markers,
    thread_coverage_report,
)
from apple_mail_mcp.tools.search.thread_payload import ThreadRequest, build_thread_payload

from .thread_fixtures import (
    ACCOUNT,
    ALL_MEMBERS,
    FULL_SCAN_BOUND,
    INBOX,
    LEGACY_SCAN_BOUND,
    SENT,
    THREAD_SUBJECT,
    ThreadScanPlan,
    attachment_error_row,
    attachment_row,
    date_floor_row,
    fake_thread_runner,
    member_by_id,
    scan_ceiling_row,
    synthetic_thread_output,
    synthetic_thread_rows,
)

_OSACOMPILE = shutil.which("osacompile")

_BOTH_MAILBOXES = [INBOX, SENT]
_HARD_CEILING = SCAN_BOUNDS["THREAD_SCAN_HARD_CEILING"]
_PROBE_CAP = SCAN_BOUNDS["THREAD_ANCHOR_MAILBOX_PROBE_CAP"]


class _ThreadTestCase(unittest.TestCase):
    """Poison the subprocess layer: no test here may reach a real mailbox."""

    def setUp(self):
        real_run = subprocess.run

        def guarded(*args, **kwargs):
            argv = args[0] if args else kwargs.get("args")
            if isinstance(argv, (list, tuple)) and argv and "osascript" in str(argv[0]):
                raise AssertionError("test attempted a live osascript call")
            return real_run(*args, **kwargs)

        patcher = patch("apple_mail_mcp.core.applescript.subprocess.run", side_effect=guarded)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_thread(self, plan=None, *, captured=None, **kwargs):
        """Call ``get_email_thread`` against the synthetic conversation."""
        params = {
            "account": ACCOUNT,
            "subject_keyword": THREAD_SUBJECT,
            "recent_days": 2.0,
            "output_format": "json",
            "include_draft_state": False,
        }
        params.update(kwargs)
        runner = fake_thread_runner(plan, captured=captured)
        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=runner):
            return search_tools.get_email_thread(**params)

    def thread_json(self, plan=None, **kwargs):
        return json.loads(self.run_thread(plan, **kwargs))

    def thread_script(self, **kwargs):
        """The scan script the tool emitted (not the anchor or reply-state ones)."""
        captured: list[str] = []
        self.run_thread(ThreadScanPlan(), captured=captured, **kwargs)
        return self.scan_script(captured)

    def ids(self, payload):
        return [item["message_id"] for item in payload["items"]]

    def scan_script(self, captured):
        scans = [script for script in captured if "EMAIL THREAD VIEW" in script]
        self.assertEqual(len(scans), 1, "expected exactly one thread scan script")
        return scans[0]


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------


class ThreadCompletenessTests(_ThreadTestCase):
    def test_full_scan_returns_every_member_and_claims_completeness(self):
        payload = self.thread_json(ThreadScanPlan(), mailboxes=_BOTH_MAILBOXES)

        self.assertEqual(payload["returned"], len(ALL_MEMBERS))
        self.assertEqual(payload["matched"], len(ALL_MEMBERS))
        self.assertFalse(payload["thread_incomplete"])
        self.assertFalse(payload["render_incomplete"])
        self.assertFalse(payload["candidate_scan_incomplete"])
        self.assertEqual(payload["scan_ceiling_hit"], [])
        self.assertEqual(payload["date_floor_hit"], [])
        self.assertNotIn("warnings", payload)
        self.assertNotIn("errors", payload)

    def test_a_narrowed_bound_loses_members_and_says_so(self):
        """The exact defect: short result + clean banner is what must not happen."""
        payload = self.thread_json(ThreadScanPlan(), mailboxes=_BOTH_MAILBOXES, scan_messages=4)

        self.assertLess(payload["returned"], len(ALL_MEMBERS))
        self.assertTrue(payload["thread_incomplete"])
        self.assertEqual(payload["scan_ceiling_hit"], sorted(_BOTH_MAILBOXES))
        self.assertEqual(payload["scan_messages_applied"], 4)

        warnings = "\n".join(payload["warnings"])
        self.assertIn("scan ceiling", warnings.lower())
        self.assertIn(f"{INBOX} (4)", warnings)
        self.assertIn(f"{SENT} (4)", warnings)
        self.assertIn("scan_messages", warnings)

    def test_the_legacy_return_bound_reproduces_the_live_short_thread(self):
        """The reported failure, reproduced exactly: nine members, five returned.

        ``scan_messages=LEGACY_SCAN_BOUND`` reinstates the pre-fix slice, since
        the scan bound *was* ``max_messages`` (default 50). Two things have to
        hold: the default call no longer uses that bound, and a call that does
        use it no longer claims to be complete.
        """
        payload = self.thread_json(
            ThreadScanPlan(),
            mailboxes=_BOTH_MAILBOXES,
            scan_messages=LEGACY_SCAN_BOUND,
        )

        self.assertEqual(payload["returned"], 5)
        self.assertEqual(payload["matched"], 5)
        self.assertTrue(payload["thread_incomplete"])
        self.assertEqual(payload["scan_ceiling_hit"], sorted(_BOTH_MAILBOXES))

        default_call = self.thread_json(ThreadScanPlan(), mailboxes=_BOTH_MAILBOXES)
        self.assertGreater(default_call["scan_messages_applied"], LEGACY_SCAN_BOUND)
        self.assertEqual(default_call["returned"], len(ALL_MEMBERS))

    def test_members_from_both_mailboxes_survive(self):
        """A Sent-resident member is a thread member, not a rounding error."""
        payload = self.thread_json(ThreadScanPlan(), mailboxes=_BOTH_MAILBOXES)

        self.assertEqual({item["mailbox"] for item in payload["items"]}, set(_BOTH_MAILBOXES))
        for member in ALL_MEMBERS:
            self.assertIn(member.message_id, self.ids(payload))

    def test_scoping_to_one_mailbox_drops_the_other_mailbox_members(self):
        """Control for the test above: it must be able to fail."""
        payload = self.thread_json(ThreadScanPlan(), mailboxes=[INBOX])

        self.assertEqual({item["mailbox"] for item in payload["items"]}, {INBOX})


class ThreadReturnLimitTests(_ThreadTestCase):
    """``max_messages`` truncation has no marker row, so it needs its own flag.

    The AppleScript stops appending at ``max_messages``, and ``FOUND N`` counts
    the already-capped list. So ``matched == returned == max_messages`` and no
    coverage marker fires: without ``return_limit_reached`` the truncation is
    invisible in exactly the way this whole change exists to prevent. It bites
    hardest through ``export_emails(scope="thread")``, which passes its own
    ``max_emails`` (default 25) straight through as ``max_messages``.

    These drive ``build_thread_payload`` directly rather than the tool: the
    fake runner replays a bounded *scan*, and the return cap is applied inside
    the AppleScript it stands in for, so going through the runner would assert
    against a cap nothing enforced.
    """

    def payload_for(self, *, returned: int, max_messages: int) -> dict:
        raw = synthetic_thread_output(inbox=returned, sent=0, matched=returned)
        request = ThreadRequest(
            account=ACCOUNT,
            resolved_mailbox=INBOX,
            mailboxes=[INBOX],
            cleaned_keyword=THREAD_SUBJECT,
            thread_strategy="subject",
            include_preview=False,
            recent_days_applied=2.0,
            max_messages=max_messages,
            scan_messages_applied=150,
            effective_timeout=120,
            include_draft_state=False,
        )
        return json.loads(build_thread_payload(raw, request))

    def test_filling_max_messages_is_reported(self):
        payload = self.payload_for(returned=4, max_messages=4)
        self.assertEqual(payload["returned"], 4)
        self.assertTrue(payload["return_limit_reached"])
        self.assertTrue(
            any("max_messages" in warning for warning in payload["warnings"]),
            payload["warnings"],
        )

    def test_the_return_bound_is_the_callers_own_choice_so_it_is_not_thread_incomplete(self):
        payload = self.payload_for(returned=4, max_messages=4)
        # Same contract as window_truncated: a bound the caller asked for is
        # reported, but must not claim the tool failed to do what was asked.
        self.assertFalse(payload["thread_incomplete"])
        self.assertEqual(payload["scan_ceiling_hit"], [])

    def test_a_thread_that_fits_does_not_arm_the_flag(self):
        payload = self.payload_for(returned=3, max_messages=10)
        self.assertEqual(payload["returned"], 3)
        self.assertFalse(payload["return_limit_reached"])
        self.assertNotIn("warnings", payload)


# ---------------------------------------------------------------------------
# Scan bound
# ---------------------------------------------------------------------------


class ThreadScanBoundTests(_ThreadTestCase):
    def _bound(self, **kwargs):
        script = self.thread_script(**kwargs)
        marker = "set scanUpperBound to "
        line = next(ln for ln in script.splitlines() if marker in ln and "messageCount" not in ln)
        return int(line.split(marker, 1)[1].strip())

    def test_bound_comes_from_recent_days_not_from_max_messages(self):
        small = self._bound(max_messages=5, recent_days=2.0)
        large = self._bound(max_messages=50, recent_days=2.0)
        self.assertEqual(small, large)

        wider = self._bound(max_messages=5, recent_days=10.0)
        self.assertGreater(wider, small)

    def test_max_messages_is_a_floor_under_the_derived_bound(self):
        """Documented behavior, not a relapse: ``max(max_messages, derived)``.

        The return bound can only *raise* the scan bound, never lower it, so a
        caller asking for 300 members still examines at least 300 messages.
        """
        derived = self._bound(max_messages=5, recent_days=2.0)
        self.assertEqual(self._bound(max_messages=derived + 25, recent_days=2.0), derived + 25)

    def test_scan_messages_overrides_the_derived_bound(self):
        self.assertEqual(self._bound(scan_messages=7, recent_days=2.0), 7)
        payload = self.thread_json(ThreadScanPlan(), scan_messages=7)
        self.assertEqual(payload["scan_messages_applied"], 7)

    def test_scan_messages_is_clamped_to_the_hard_ceiling(self):
        self.assertEqual(self._bound(scan_messages=_HARD_CEILING * 10), _HARD_CEILING)
        payload = self.thread_json(ThreadScanPlan(), scan_messages=_HARD_CEILING * 10)
        self.assertEqual(payload["scan_messages_applied"], _HARD_CEILING)

    def test_derived_bound_is_clamped_to_the_hard_ceiling_too(self):
        self.assertLessEqual(self._bound(recent_days=365.0), _HARD_CEILING)

    def test_non_positive_scan_messages_is_refused(self):
        for value in (0, -1, -400):
            with self.subTest(scan_messages=value):
                result = self.run_thread(ThreadScanPlan(), scan_messages=value)
                self.assertTrue(result.startswith("Error:"), result)
                self.assertIn("scan_messages must be > 0", result)

    def test_candidate_slice_is_bounded_and_never_a_full_enumeration(self):
        script = self.thread_script(mailboxes=_BOTH_MAILBOXES)

        self.assertIn("set candidateMessages to messages 1 thru scanUpperBound of currentMailbox", script)
        self.assertNotIn("every message of currentMailbox", script)
        self.assertNotIn("set candidateMessages to messages of currentMailbox", script)
        # ``messages of currentMailbox`` is legal in exactly one spelling: the
        # cheap ``count of`` property read used to size the slice. Any other
        # occurrence is the unbounded enumeration under a different name.
        for line in script.splitlines():
            if "messages of currentMailbox" in line:
                self.assertIn("count of messages of currentMailbox", line, line.strip())

    def test_zero_bound_slice_is_guarded_before_it_is_emitted(self):
        """``messages 1 thru 0`` silently returns the FIRST message, on every backend.

        So the guard is not cosmetic: an empty mailbox must bind nothing, not
        one arbitrary message that then gets subject-matched into the thread.
        """
        script = self.thread_script()
        guard = script.index("if scanUpperBound > 0 then")
        slice_line = script.index("set candidateMessages to messages 1 thru scanUpperBound of currentMailbox")
        self.assertLess(guard, slice_line)
        self.assertNotIn("messages 1 thru 0 of", script)


# ---------------------------------------------------------------------------
# Date floor
# ---------------------------------------------------------------------------


class ThreadDateFloorTests(_ThreadTestCase):
    def test_date_floor_names_the_mailbox_and_points_at_recent_days(self):
        plan = ThreadScanPlan(date_floor_at={INBOX: 10})
        payload = self.thread_json(plan, mailboxes=_BOTH_MAILBOXES)

        self.assertEqual(payload["date_floor_hit"], [INBOX])
        self.assertTrue(payload["window_truncated"])
        self.assertLess(payload["returned"], len(ALL_MEMBERS))

        warnings = "\n".join(payload["warnings"])
        self.assertIn("recent_days", warnings)
        self.assertIn("Widen recent_days", warnings)
        # The remedy must not be the wrong one: the slice was not the bound.
        self.assertEqual(payload["scan_ceiling_hit"], [])
        self.assertNotIn("Raise scan_messages", warnings)

    def test_date_floor_alone_does_not_raise_thread_incomplete(self):
        """Deliberate, and the reason ``window_truncated`` exists separately.

        The floor fires whenever a mailbox holds anything older than the
        requested window, which is nearly always, so folding it into
        ``thread_incomplete`` would make that flag true on virtually every call
        and train callers to ignore the one signal that matters. The caller the
        floor *does* concern still sees ``window_truncated`` and
        ``date_floor_hit``. This assertion is here so the trade-off is a
        decision on record rather than something a future edit flips by
        accident.
        """
        plan = ThreadScanPlan(date_floor_at={INBOX: 10})
        payload = self.thread_json(plan, mailboxes=_BOTH_MAILBOXES)

        self.assertFalse(payload["thread_incomplete"])
        self.assertEqual(payload["scan_ceiling_hit"], [])
        self.assertTrue(payload["window_truncated"])

    def test_a_scan_ceiling_does_raise_thread_incomplete(self):
        """Mirror image: a bound the caller did not choose is the flagged case."""
        payload = self.thread_json(ThreadScanPlan(), mailboxes=_BOTH_MAILBOXES, scan_messages=4)

        self.assertTrue(payload["thread_incomplete"])
        self.assertFalse(payload["window_truncated"])

    def test_ceiling_is_suppressed_in_a_mailbox_whose_date_floor_fired(self):
        """Deliberate: the window ran out before the slice did."""
        plan = ThreadScanPlan(date_floor_at={INBOX: 3})
        payload = self.thread_json(plan, mailboxes=_BOTH_MAILBOXES, scan_messages=4)

        self.assertEqual(payload["date_floor_hit"], [INBOX])
        self.assertEqual(payload["scan_ceiling_hit"], [SENT])

    def test_coverage_report_prefers_the_floor_over_the_ceiling(self):
        """Producer side of the rule above, read straight off the emitted script."""
        report = thread_coverage_report()
        self.assertLess(
            report.index("if threadDateFloorHit then"),
            report.index("else if threadScanCeilingHit then"),
        )
        script = self.thread_script()
        self.assertIn("if threadDateFloorHit then", script)
        self.assertIn("else if threadScanCeilingHit then", script)


# ---------------------------------------------------------------------------
# Anchor
# ---------------------------------------------------------------------------


class ThreadAnchorTests(_ThreadTestCase):
    def test_mailbox_all_resolves_the_anchor_instead_of_erroring(self):
        anchor = member_by_id("703")
        plan = ThreadScanPlan(probe_mailbox=SENT, anchor=anchor, strategy="header")
        captured: list[str] = []
        raw = self.run_thread(plan, captured=captured, message_id=anchor.message_id, mailbox="All")

        self.assertNotIn("Mailbox not found: All", raw)
        payload = json.loads(raw)
        self.assertEqual(payload["anchor"]["message_id"], anchor.message_id)
        self.assertEqual(payload["anchor"]["mailbox"], SENT)

        fetches = [s for s in captured if "whose id is" in s and "ANCHOR_MAILBOX|||" not in s]
        self.assertEqual(len(fetches), 1, "the anchor should be fetched from the probed mailbox only")
        self.assertIn(f'"{SENT}"', fetches[0])

        warnings = "\n".join(payload["warnings"])
        self.assertIn('mailbox="All"', warnings)
        self.assertIn("anchor was fetched from its own mailbox", warnings)
        # The probe answers where the anchor lives, not where the thread does:
        # resolving it must not silently narrow a whole-account request.
        self.assertEqual(payload["mailbox"], "All")
        self.assertIn("set searchMailboxes to every mailbox of targetAccount", self.scan_script(captured))

    def test_mailbox_all_with_no_probe_hit_names_the_probe_cap(self):
        plan = ThreadScanPlan(probe_mailbox=None, anchor=member_by_id("703"))
        raw = self.run_thread(plan, message_id="703", mailbox="All")

        self.assertTrue(raw.startswith("Error:"), raw)
        self.assertNotIn("Mailbox not found: All", raw)
        self.assertIn(str(_PROBE_CAP), raw)
        self.assertIn("mailboxes", raw)

    def test_anchor_missed_by_the_scan_is_appended_and_flagged(self):
        anchor = member_by_id("703")  # Sent Items, position 11: behind a bound of 4
        plan = ThreadScanPlan(anchor=anchor, strategy="header")
        payload = self.thread_json(
            plan,
            message_id=anchor.message_id,
            mailboxes=_BOTH_MAILBOXES,
            scan_messages=4,
        )

        recovered = [item for item in payload["items"] if item.get("anchor_recovered")]
        self.assertEqual([item["message_id"] for item in recovered], [anchor.message_id])
        self.assertTrue(payload["thread_incomplete"])
        # ``matched`` is incremented alongside the appended row so the
        # render-mismatch reconciliation does not report the anchor twice.
        self.assertEqual(payload["matched"], payload["returned"])
        self.assertFalse(payload["render_incomplete"])
        self.assertNotIn("render_mismatch", json.dumps(payload.get("error_details", [])))

        warnings = "\n".join(payload["warnings"])
        self.assertIn("anchor message was not returned by the thread scan", warnings)

    def test_anchor_returned_by_the_scan_is_not_duplicated(self):
        anchor = member_by_id("601")  # Inbox, position 1: always reached
        plan = ThreadScanPlan(anchor=anchor, strategy="header")
        payload = self.thread_json(plan, message_id=anchor.message_id, mailboxes=_BOTH_MAILBOXES)

        self.assertEqual(self.ids(payload).count(anchor.message_id), 1)
        self.assertEqual(payload["returned"], len(ALL_MEMBERS))
        self.assertEqual(payload["matched"], len(ALL_MEMBERS))
        self.assertFalse(any(item.get("anchor_recovered") for item in payload["items"]))


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


class ThreadAttachmentCountTests(_ThreadTestCase):
    def test_zero_and_unknown_attachment_counts_are_different_answers(self):
        plan = ThreadScanPlan(attachments={"601": 3, "602": 0})
        payload = self.thread_json(plan, mailboxes=_BOTH_MAILBOXES)
        counts = {item["message_id"]: item["attachment_count"] for item in payload["items"]}

        # Every member carries the key, present or null -- an absent key would
        # let a caller read "no attachments" out of a count nobody ever took.
        for member in ALL_MEMBERS:
            self.assertIn(member.message_id, counts)
        for item in payload["items"]:
            self.assertIn("attachment_count", item)

        self.assertEqual(counts["601"], 3)
        self.assertEqual(counts["602"], 0)
        self.assertIsNotNone(counts["602"])  # 0 is a count; None is "never looked"
        self.assertIsNone(counts["603"])

    def test_unreadable_attachment_count_is_null_and_warned_about(self):
        plan = ThreadScanPlan(attachment_errors={"604": "attachment list unavailable"})
        payload = self.thread_json(plan, mailboxes=_BOTH_MAILBOXES)
        counts = {item["message_id"]: item["attachment_count"] for item in payload["items"]}

        self.assertIsNone(counts["604"])
        warnings = "\n".join(payload["warnings"])
        self.assertIn("Attachment counts could not be read", warnings)
        self.assertIn("not the same as zero", warnings)


# ---------------------------------------------------------------------------
# Marker channel
# ---------------------------------------------------------------------------


class ThreadMarkerChannelTests(_ThreadTestCase):
    def _raw_with_markers(self):
        return synthetic_thread_output(
            extra_rows=[scan_ceiling_row(INBOX, 4), date_floor_row(SENT)],
            attachments={"601": 2},
            attachment_errors={"602": "unreadable"},
        )

    def test_markers_never_reach_the_record_parser(self):
        cleaned, markers = split_thread_markers(self._raw_with_markers())

        for prefix in ("THREAD_SCAN_CEILING|||", "THREAD_DATE_FLOOR|||", "THREAD_ATTACHMENTS|||"):
            self.assertNotIn(prefix, cleaned)
        self.assertEqual(markers.scan_ceilings, {INBOX: "4"})
        self.assertEqual(markers.date_floors, {SENT: "recent_days cutoff"})
        self.assertEqual(markers.attachment_counts, {"601": 2})
        self.assertEqual(markers.attachment_errors, {"602": "unreadable"})

    def test_the_parser_would_otherwise_silently_drop_every_marker(self):
        """Why the split has to happen in Python, before parsing.

        ``_parse_search_records`` skips any line with fewer than 8 pipe fields,
        so a marker left in the stream is not mis-parsed into a record -- it
        vanishes, taking the caveat with it. The bound would then be invisible
        and the short thread would look complete, which is the whole bug.
        """
        raw = self._raw_with_markers()
        cleaned, _ = split_thread_markers(raw)

        raw_records, raw_errors = _parse_search_records(raw)
        clean_records, clean_errors = _parse_search_records(cleaned)

        self.assertEqual([r["message_id"] for r in raw_records], [r["message_id"] for r in clean_records])
        self.assertEqual(raw_errors, [])
        self.assertEqual(clean_errors, [])

    def test_malformed_markers_are_dropped_without_raising(self):
        """A broken caveat must never turn a usable thread into an error."""
        malformed = [
            "THREAD_SCAN_CEILING|||",  # no mailbox
            "THREAD_SCAN_CEILING|||   |||9",  # blank mailbox
            "THREAD_DATE_FLOOR|||",  # no mailbox
            "THREAD_ATTACHMENTS|||601|||not-a-number",  # non-numeric count
            "THREAD_ATTACHMENTS|||602",  # no count field at all
            "THREAD_ATTACHMENTS|||",  # nothing at all
        ]
        raw = "\n".join([synthetic_thread_output(), *malformed])
        cleaned, markers = split_thread_markers(raw)

        self.assertEqual(markers.scan_ceilings, {})
        self.assertEqual(markers.date_floors, {})
        self.assertEqual(markers.attachment_counts, {})
        self.assertEqual(markers.attachment_errors, {})
        self.assertFalse(markers.bounded)
        records, _ = _parse_search_records(cleaned)
        self.assertEqual(len(records), len(ALL_MEMBERS))

    def test_a_malformed_marker_does_not_break_the_tool_payload(self):
        plan = ThreadScanPlan(extra_rows=("THREAD_ATTACHMENTS|||601|||not-a-number", "THREAD_SCAN_CEILING|||"))
        payload = self.thread_json(plan, mailboxes=_BOTH_MAILBOXES)

        self.assertEqual(payload["returned"], len(ALL_MEMBERS))
        self.assertFalse(payload["thread_incomplete"])
        self.assertIsNone({item["message_id"]: item["attachment_count"] for item in payload["items"]}["601"])

    def test_fixture_marker_rows_use_the_production_prefixes(self):
        """The fixture must drift with the helper, not away from it."""
        self.assertTrue(scan_ceiling_row(INBOX, 4).startswith(THREAD_SCAN_CEILING_PREFIX))
        self.assertTrue(date_floor_row(SENT).startswith(THREAD_DATE_FLOOR_PREFIX))
        self.assertTrue(attachment_row("601", 2).startswith(THREAD_ATTACHMENT_COUNT_PREFIX))
        self.assertTrue(attachment_error_row("601", "unreadable").startswith(THREAD_ATTACHMENT_COUNT_PREFIX))
        # The error sentinel is a negative count, which is how "unreadable"
        # stays distinguishable from a genuine zero on the wire.
        self.assertIn("|||-1|||", attachment_error_row("601", "unreadable"))

    def test_fixture_rows_round_trip_through_the_record_parser(self):
        """Guard the fixture itself: a wrong field count would fake every pass."""
        records, errors = _parse_search_records(synthetic_thread_rows(attachments={}))

        self.assertEqual(errors, [])
        self.assertEqual(len(records), len(ALL_MEMBERS))
        self.assertEqual(records[0]["mailbox"], INBOX)
        self.assertEqual(records[-1]["mailbox"], SENT)
        self.assertIn("references", records[0])
        self.assertFalse(records[0]["was_replied_to"])


# ---------------------------------------------------------------------------
# The script has to parse
# ---------------------------------------------------------------------------


@unittest.skipUnless(_OSACOMPILE, "osacompile not available (non-macOS CI)")
class ThreadScriptCompilesTests(_ThreadTestCase):
    def test_every_thread_scan_shape_compiles(self):
        shapes = (
            {"recent_days": 7.0},
            {"mailboxes": _BOTH_MAILBOXES},
            {"mailbox": "All"},
            {"include_preview": False},
            {"scan_messages": 37},
            {"scan_messages": FULL_SCAN_BOUND, "mailboxes": _BOTH_MAILBOXES, "include_preview": False},
        )
        for shape in shapes:
            with self.subTest(shape=shape):
                script = self.thread_script(**shape)
                with tempfile.TemporaryDirectory() as tmp:
                    source = Path(tmp) / "thread.applescript"
                    source.write_text(script, encoding="utf-8")
                    proc = subprocess.run(
                        ["osacompile", "-o", "/dev/null", str(source)],
                        capture_output=True,
                        check=False,
                    )
                self.assertEqual(
                    proc.returncode,
                    0,
                    f"osacompile rejected the thread script:\n{proc.stderr.decode('utf-8', 'replace')}",
                )


if __name__ == "__main__":
    unittest.main()
