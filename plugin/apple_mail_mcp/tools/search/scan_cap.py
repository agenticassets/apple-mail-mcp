"""Scan-bound arithmetic for ``search_emails``' per-mailbox slice.

Split out of ``script.py`` the same way ``thread_helpers.py`` sits beside
``thread.py``: the module budget is 600 physical lines and ``script.py`` had no
headroom left. Nothing here builds AppleScript or calls ``run_applescript``, so
this module is not a patch seam; ``script`` imports
:func:`compute_search_scan_cap` by name and nothing re-exports from here.

Two facts this module owns, both of which used to be wrong (AGENTIC-2794):

1.  **The scan is sized from the window the caller actually supplied.** Only
    ``recent_days`` used to widen the slice. A caller who passed an explicit
    ``date_from`` (and therefore no ``recent_days``) got
    ``scan_cap = limit + offset + 1``, so a *precise* needle query with a
    *small* limit received the *smallest possible* scan: measured live, one
    exact Message-ID with a 10-day ``date_from`` and ``limit=10`` bound
    ``messages 1 thru 11`` and found nothing, while the identical query with
    ``limit=120`` bound 50 and found the message. ``date_from`` and
    ``recent_days`` describe the same window, so they widen the scan the same
    way, through the same ``bounded_scan.compute_scan_upper_bound`` helper.

2.  **The truncation flag compares against what the scan wanted.** It used to
    compare the post-clamp ``scan_cap`` against ``base_cap``, which is False in
    exactly the regime above — so the ``SCAN_CEILING|||`` marker was never
    spliced, and the caller got ``returned: 4, has_more: false`` with no hint
    that only the newest 21 of 24,000 messages had been examined. The smaller,
    more precise query produced the more confident wrong answer.
"""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple

from apple_mail_mcp.bounded_scan import compute_scan_upper_bound
from apple_mail_mcp.constants import SCAN_BOUNDS


class ScanCapPlan(NamedTuple):
    """Result of :func:`compute_search_scan_cap`.

    *scan_cap* is the number spliced as ``set scanUpperBound to N``.
    *body_search_capped* and *scan_ceiling_applied* are the two flags
    ``_build_search_script`` derives from the same arithmetic.
    """

    scan_cap: int
    body_search_capped: bool
    scan_ceiling_applied: bool


def window_days_from_date_from(date_from: str | None, *, now: datetime | None = None) -> float:
    """Age of *date_from* in days, or ``0.0`` when there is no usable window.

    Absent, unparseable, and future-dated values all return ``0.0`` — i.e. "no
    window to widen for", which is exactly the behaviour that existed before
    ``date_from`` was consulted at all. Parsing defensively is deliberate: the
    caller-facing complaint about a malformed date already exists downstream in
    ``records._build_applescript_date``, which raises
    ``ValueError("Invalid date ... Use YYYY-MM-DD")``. Raising here as well
    would move that error into the cap math, where its message would be wrong
    about which parameter is at fault and where a future refactor could reorder
    it ahead of the caller's real validation.
    """
    if not date_from:
        return 0.0
    try:
        parsed = datetime.strptime(date_from.strip(), "%Y-%m-%d")
    except (AttributeError, TypeError, ValueError):
        return 0.0
    days = ((now or datetime.now()) - parsed).total_seconds() / 86400.0
    return days if days > 0 else 0.0


def compute_search_scan_cap(
    *,
    base_cap: int,
    recent_days: float,
    date_from: str | None,
    subject_only_header_search: bool,
    use_body_search: bool,
    date_from_explicit: bool,
    now: datetime | None = None,
) -> ScanCapPlan:
    """Size one mailbox's newest-first slice and say whether it was cut short.

    *base_cap* is ``limit + 1 + offset`` — exactly the number of messages that
    would have to be inspected for ``has_more`` to be an honest statement about
    the mailbox. It is the floor: a caller paginating past the window helper's
    cap still gets a slice large enough to honor their offset+limit.

    The window cap comes from ``bounded_scan.compute_scan_upper_bound`` and is
    derived from ``recent_days`` when the caller passed one, and otherwise from
    the age of ``date_from``. Both spell the same window, so both widen the scan
    identically; ``recent_days`` wins when both are present because
    ``search_emails`` derives ``date_from`` from it in that case, making them
    the same number twice.

    ``subject_only_header_search`` deliberately skips the widening. Narrow
    header searches are usually "needle" lookups; on large Exchange accounts,
    binding hundreds of recent messages just to prove a no-hit subject does not
    exist can exceed wrapper timeouts. That path stays bounded by the caller's
    requested page.
    """
    window_cap = 0
    if recent_days and recent_days > 0:
        window_cap = compute_scan_upper_bound(recent_days)
    else:
        window_days = window_days_from_date_from(date_from, now=now)
        if window_days > 0:
            window_cap = compute_scan_upper_bound(window_days)

    desired_cap = base_cap if subject_only_header_search else max(base_cap, window_cap)
    scan_cap = desired_cap

    # Body-search cap: reading ``content of aMessage`` for every candidate is
    # O(N × message-size) and triggers cold-cache IMAP fetches on large remote
    # mailboxes. When the caller has not explicitly bounded the window with
    # ``date_from``, cap hard to keep wall time reasonable. With an explicit
    # ``date_from`` the caller has intentionally bounded the scan, so leave it.
    body_search_capped = False
    body_cap = SCAN_BOUNDS["BODY_SEARCH_AUTO_CAP"]
    if use_body_search and not date_from_explicit and scan_cap > body_cap:
        scan_cap = body_cap
        body_search_capped = True

    # Hard ceiling: regardless of how base_cap/window_cap/body-cap computed
    # above, never bind more than SEARCH_HARD_CEILING messages in a single
    # ``messages 1 thru scan_cap`` slice. This is what actually bounds wall time
    # on large cold-cache Exchange accounts.
    scan_cap = min(scan_cap, SCAN_BOUNDS["SEARCH_HARD_CEILING"])

    # Arm the in-band ``SCAN_CEILING|||`` marker when the slice cannot stand for
    # the whole question the caller asked. Two independent ways that happens:
    #
    # * ``scan_cap < desired_cap`` — the hard ceiling or the body cap cut the
    #   scan below the size the math above wanted.
    # * ``desired_cap > base_cap`` — the slice size came from the *date window*
    #   rather than the caller's page arithmetic. A date window has no finite
    #   message count, so a fixed slice can only ever be a heuristic for it: if
    #   that slice saturates, messages inside the requested window went
    #   unexamined. This is the disjunct the 10-day/``limit=10`` repro needs,
    #   because ``compute_scan_upper_bound`` clamps at ``SEARCH_WINDOW_CAP``
    #   (50), which equals ``SEARCH_HARD_CEILING`` — so the clamp comparison
    #   alone can never see that truncation.
    #
    # Arming is not the same as warning. The marker only *fires* from
    # AppleScript when ``count of candidateMessages >= scanUpperBound``, so a
    # mailbox smaller than the slice stays silent no matter how it was armed.
    # A page-bounded scan with no window widening (``desired_cap == base_cap``,
    # nothing clamped) stays unarmed on purpose: saturating there just means
    # "there is a next page", which ``has_more`` already says.
    scan_ceiling_applied = scan_cap < desired_cap or desired_cap > base_cap

    return ScanCapPlan(scan_cap, body_search_capped, scan_ceiling_applied)
