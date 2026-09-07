"""Pure, Mail-free helpers for ``get_email_thread``.

Split out of ``thread.py`` the same way ``search/script.py`` sits beside
``search/emails.py``: subject-prefix stripping, Message-ID header tokens, and
the candidate-scan failure channel. Nothing here calls ``run_applescript`` or
opens an AppleScript ``try``, so no package-namespace routing or patch seam is
needed, and the module adds no entry to the bare-``try`` ratchet.

``_thread_mailbox_script`` deliberately stayed behind in ``thread.py``: its
INBOX/Inbox fallback is a pre-existing ``silent``-tier lint entry, and moving
it would relocate that entry into a file the ratchet baseline has never seen,
which reads as a new violation rather than the file move it is.

The candidate-scan channel below is the sibling of the render-loop counters
that live in ``thread.py``. A thread message can be lost in two different
places, and a caller has to be able to tell them apart:

* **candidate collection** — the per-mailbox and per-message ``try`` that runs
  *before* ``FOUND N`` is computed. A message that throws while being matched
  never enters ``threadMessages``, so it is never counted in ``FOUND``. The
  matched-vs-returned reconciliation in ``thread.py`` cannot see it: both
  numbers are consistently wrong together, and the caller gets a short thread
  with ``render_incomplete: false`` and a clean banner.
* **render** — a message that matched, was counted in ``FOUND N``, and then
  threw while its row was built. That one *is* visible as ``matched >
  returned``.

Same in-band channel as ``script.py``'s ``_SCAN_FAILURE_REPORT`` (pattern P1):
one ``ERROR_MAILBOX|||`` row that ``records._parse_search_records`` already
routes into ``mailbox_errors``, plus a ``PARTIAL:`` line for text mode.
"""

import re

from apple_mail_mcp.constants import THREAD_PREFIXES
from apple_mail_mcp.core import escape_applescript

# Single source for the wording that both produces the AppleScript message
# (``candidate_failure_report``) and classifies it back in Python
# (``_thread_error_type``). Candidate-scan rows are typed
# ``candidate_scan_error`` in ``error_details`` so a caller can tell a
# pre-match loss from the render-loop's ``mailbox_error``.
CANDIDATE_SCAN_FAILURE_PREFIX = "candidate scan failed for "


def _thread_strip_prefixes_handler() -> str:
    """AppleScript handler to strip Re:/Fwd:/etc. prefixes from subjects."""
    prefix_checks = ""
    for prefix in THREAD_PREFIXES:
        escaped = escape_applescript(prefix)
        prefix_checks += f'''
                ignoring case
                    if baseSubj starts with "{escaped}" then
                        set baseSubj to text {len(prefix) + 1} thru -1 of baseSubj
                        repeat while baseSubj starts with " "
                            set baseSubj to text 2 thru -1 of baseSubj
                        end repeat
                        set didStrip to true
                    end if
                end ignoring
'''
    return f"""
    on stripThreadPrefixes(subj)
        set baseSubj to subj
        set didStrip to true
        repeat while didStrip
            set didStrip to false
            {prefix_checks}
        end repeat
        return baseSubj
    end stripThreadPrefixes
"""


def thread_loss_report(*, counter_var: str, loss_var: str, message_expr: str, escaped_scope: str) -> str:
    """One "N thread message(s) were lost" report, on both output channels.

    The three loss counters (``threadCandidateFailures``,
    ``threadMailboxFailures``, ``threadRenderFailures``) all report the same
    way, and having to report on *both* channels is what makes the shape worth
    sharing: a report that reached only ``recordRows`` would be invisible to
    text mode, and one that reached only ``outputText`` would be invisible to
    JSON mode. Emitting them from one place is what keeps that pair intact.

    *message_expr* is an AppleScript string expression (not a Python string)
    because every message interpolates its own counters. It is bound to
    *loss_var* once and then read twice, so the two channels can never
    disagree about the wording.

    Assumes ``recordRows`` and ``outputText`` are in scope. *escaped_scope* is
    the already-escaped mailbox scope the thread searched.
    """
    return f"""
            if {counter_var} > 0 then
                set {loss_var} to {message_expr}
                set end of recordRows to "ERROR_MAILBOX|||{escaped_scope}|||" & {loss_var}
                set outputText to outputText & "PARTIAL: " & {loss_var} & return
            end if"""


def candidate_failure_report(escaped_scope: str) -> str:
    """AppleScript reporting candidate-collection losses, run after the scan loop.

    Splice directly after the mailbox ``repeat`` loop and *before* the
    ``FOUND N`` banner, so the caveat precedes the count it undermines.
    Assumes ``threadCandidateFailures`` / ``threadCandidateScanned`` /
    ``threadMailboxFailures`` (armed by the two ``on error`` arms in
    ``thread.py``) are in scope, plus the variables
    :func:`thread_loss_report` needs.

    Two counters rather than one because the causes differ: a per-message
    throw loses one candidate, a per-mailbox throw loses every message in that
    mailbox, and ``threadCandidateScanned`` is not even a meaningful
    denominator for the second (the slice that would have supplied it is what
    failed).
    """
    per_message = thread_loss_report(
        counter_var="threadCandidateFailures",
        loss_var="threadCandidateLoss",
        message_expr=(
            f'"{CANDIDATE_SCAN_FAILURE_PREFIX}" & (threadCandidateFailures as string) & " of " '
            '& (threadCandidateScanned as string) & " scanned message(s) before thread matching; '
            'those messages were never counted in FOUND, so this thread may be missing messages"'
        ),
        escaped_scope=escaped_scope,
    )
    per_mailbox = thread_loss_report(
        counter_var="threadMailboxFailures",
        loss_var="threadMailboxLoss",
        message_expr=(
            f'"{CANDIDATE_SCAN_FAILURE_PREFIX}" & (threadMailboxFailures as string) '
            '& " mailbox(es) before thread matching; those mailboxes contributed no thread messages"'
        ),
        escaped_scope=escaped_scope,
    )
    return f"{per_message}{per_mailbox}\n    "


def render_failure_report(escaped_scope: str) -> str:
    """AppleScript reporting render-loop losses, run after the display loop.

    The sibling of :func:`candidate_failure_report` on the other side of the
    ``FOUND N`` banner. This loss *is* the gap between ``matched`` and
    ``returned``; a candidate loss is not (see the module docstring). Assumes
    ``threadRenderFailures`` and ``threadMatchedCount`` are in scope.
    """
    return thread_loss_report(
        counter_var="threadRenderFailures",
        loss_var="threadRenderLoss",
        message_expr=(
            '"render failed for " & (threadRenderFailures as string) & " of " '
            '& (threadMatchedCount as string) & " thread message(s); results are incomplete"'
        ),
        escaped_scope=escaped_scope,
    )


def is_candidate_scan_failure(message: str) -> bool:
    """True when an ``ERROR_MAILBOX`` message came from candidate collection."""
    return message.startswith(CANDIDATE_SCAN_FAILURE_PREFIX)


def _thread_error_type(message: str) -> str:
    """Type an ``ERROR_MAILBOX`` row by which loop lost the thread message.

    Candidate-collection losses and render losses arrive on the same in-band
    channel but have different causes and different consequences: a candidate
    loss is invisible in ``matched``/``returned`` (both are short together),
    a render loss is exactly the gap between them.
    """
    return "candidate_scan_error" if is_candidate_scan_failure(message) else "mailbox_error"


_HEADER_MESSAGE_ID_RE = re.compile(r"<([^<>]+)>|([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+)")


def _normalize_thread_header_id(value: str) -> str:
    """Normalize a Message-ID-like token for thread graph comparisons."""
    return value.strip().strip("<>").strip().lower()


def _extract_thread_header_tokens(*values: str | None) -> list[str]:
    """Return normalized header Message-ID tokens from Message-ID/References fields."""
    tokens: set[str] = set()
    for value in values:
        if not value:
            continue
        for bracketed, bare in _HEADER_MESSAGE_ID_RE.findall(value):
            token = _normalize_thread_header_id(bracketed or bare)
            if token:
                tokens.add(token)
    return sorted(tokens)


def _applescript_string_list(values: list[str]) -> str:
    """Render a Python string list as an AppleScript list literal."""
    return "{" + ", ".join(f'"{escape_applescript(value)}"' for value in values) + "}"


# ---------------------------------------------------------------------------
# Thread coverage channel (AGENTIC-2794)
#
# The loss counters above answer "did a read throw". These answer the
# different and, in practice, more damaging question: "did the scan stop
# looking before it ran out of conversation". A thread truncated by its own
# bound loses no reads at all, so every counter above stays zero and the short
# result renders with a clean banner.
#
# These rows travel on their own prefixes rather than as extra ``|||`` fields
# on a record row. ``records._parse_search_records`` splits record rows with
# ``split("|||", 14)``, so a 16th field would land inside the 15th and corrupt
# ``was_replied_to``. ``_parse_search_records`` ignores a 3-field line, so
# these rows must be lifted out in Python *before* it runs -- that is what
# :func:`split_thread_markers` is for.
# ---------------------------------------------------------------------------

#: A mailbox's candidate slice filled its bound, so the scan stopped looking
#: while messages remained behind it. Fields: mailbox, bound.
THREAD_SCAN_CEILING_PREFIX = "THREAD_SCAN_CEILING|||"

#: The candidate loop exited on the ``recent_days`` cutoff, so older members
#: exist outside the requested window. Fields: mailbox, cutoff description.
THREAD_DATE_FLOOR_PREFIX = "THREAD_DATE_FLOOR|||"

#: Per-member attachment count, keyed by numeric Mail id. Fields: id, count,
#: and an optional reason when the count could not be read (count is then -1).
THREAD_ATTACHMENT_COUNT_PREFIX = "THREAD_ATTACHMENTS|||"

_THREAD_MARKER_PREFIXES = (
    THREAD_SCAN_CEILING_PREFIX,
    THREAD_DATE_FLOOR_PREFIX,
    THREAD_ATTACHMENT_COUNT_PREFIX,
)


class ThreadMarkers:
    """Coverage markers lifted out of one thread scan's raw output.

    ``scan_ceilings`` and ``date_floors`` map mailbox name -> detail string;
    ``attachment_counts`` maps numeric Mail id -> attachment count.
    """

    __slots__ = ("scan_ceilings", "date_floors", "attachment_counts", "attachment_errors")

    def __init__(self) -> None:
        self.scan_ceilings: dict[str, str] = {}
        self.date_floors: dict[str, str] = {}
        self.attachment_counts: dict[str, int] = {}
        self.attachment_errors: dict[str, str] = {}

    @property
    def bounded(self) -> bool:
        """True when the scan stopped early for either bounded reason."""
        return bool(self.scan_ceilings or self.date_floors)

    def warnings(self) -> list[str]:
        """Human-readable caveats, one per distinct bound that fired."""
        notes: list[str] = []
        if self.scan_ceilings:
            named = ", ".join(f"{mb} ({bound})" for mb, bound in sorted(self.scan_ceilings.items()))
            notes.append(
                "Thread scan ceiling reached: the candidate slice filled its bound in "
                f"{len(self.scan_ceilings)} mailbox(es) ({named}), so messages behind it were never "
                "examined for thread membership. This thread may be missing members. Raise "
                "scan_messages, or narrow to the mailbox that holds the conversation."
            )
        if self.date_floors:
            named = ", ".join(sorted(self.date_floors))
            notes.append(
                f"Thread scan stopped at the recent_days cutoff in {len(self.date_floors)} mailbox(es) "
                f"({named}); members older than the window exist and were not enumerated. "
                "Widen recent_days to reach them."
            )
        if self.attachment_errors:
            notes.append(
                f"Attachment counts could not be read for {len(self.attachment_errors)} thread "
                "member(s); their attachment_count is null, which is not the same as zero."
            )
        return notes


def split_thread_markers(output: str) -> tuple[str, ThreadMarkers]:
    """Split coverage-marker rows out of raw thread output.

    Returns the remaining text (safe to hand to ``_parse_search_records``)
    and the parsed markers. Malformed marker rows are dropped rather than
    raised on: a marker is a caveat channel, and losing one must never turn a
    usable thread into an error.
    """
    markers = ThreadMarkers()
    kept: list[str] = []
    for line in output.splitlines():
        prefix = next((p for p in _THREAD_MARKER_PREFIXES if line.startswith(p)), None)
        if prefix is None:
            kept.append(line)
            continue
        first, _, second = line[len(prefix) :].partition("|||")
        key = first.strip()
        if not key:
            continue
        if prefix == THREAD_SCAN_CEILING_PREFIX:
            markers.scan_ceilings[key] = second.strip()
        elif prefix == THREAD_DATE_FLOOR_PREFIX:
            markers.date_floors[key] = second.strip()
        else:
            count_text, _, reason = second.partition("|||")
            try:
                count = int(count_text.strip())
            except ValueError:
                continue
            if count < 0:
                markers.attachment_errors[key] = reason.strip() or "unreadable"
            else:
                markers.attachment_counts[key] = count
    return "\n".join(kept), markers


def thread_coverage_report() -> str:
    """AppleScript reporting one mailbox's coverage bounds, run per mailbox.

    Splice inside the mailbox ``repeat`` loop, after that mailbox's candidate
    loop has finished and while ``currentMailboxName`` still names it.
    Assumes ``recordRows``, ``threadCoverageNotes``, ``threadScanCeilingHit``
    and ``threadDateFloorHit`` are in scope.

    Both channels again (see :func:`candidate_failure_report`): a caveat that
    reached only ``recordRows`` would be invisible in text mode, and one that
    reached only the text would be invisible in JSON mode. The text goes to
    ``threadCoverageNotes`` rather than straight to ``outputText`` because this
    runs *before* the ``FOUND N`` banner is written; the caller flushes the
    notes after the render so the caveats read as caveats on a result rather
    than as a preamble to one.

    The ceiling is suppressed when the date floor fired: reaching the
    ``recent_days`` cutoff means the window ran out before the slice did, so
    the slice was not the limiting bound and saying otherwise would send the
    caller to raise ``scan_messages`` when they need to widen ``recent_days``.
    """
    return f"""
                    if threadDateFloorHit then
                        set end of recordRows to "{THREAD_DATE_FLOOR_PREFIX}" & currentMailboxName & "|||recent_days cutoff"
                        set threadCoverageNotes to threadCoverageNotes & "PARTIAL: scan stopped at the recent_days cutoff in " & currentMailboxName & "; older thread members were not enumerated" & return
                    else if threadScanCeilingHit then
                        set end of recordRows to "{THREAD_SCAN_CEILING_PREFIX}" & currentMailboxName & "|||" & (scanUpperBound as string)
                        set threadCoverageNotes to threadCoverageNotes & "PARTIAL: scan ceiling reached in " & currentMailboxName & " after " & (scanUpperBound as string) & " message(s); this thread may be missing members" & return
                    end if"""
