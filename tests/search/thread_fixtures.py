"""A synthetic multi-mailbox conversation, in the wire format the scan emits.

Built for AGENTIC-2794/2796. The live defect was a 9-member conversation split
across two mailboxes that came back with 5 members and
``thread_incomplete: false`` -- the *scan* bound was ``max_messages``, the
*return* bound, so the tool stopped looking before it ran out of conversation
and then said nothing about it.

Reproducing that needs a fixture with three properties a flat list of rows does
not have:

1.  **Members in more than one mailbox.** Six in ``Inbox``, three in
    ``Sent Items``. A bound applies per mailbox, so a single-mailbox fixture
    cannot show a Sent-resident member being dropped.
2.  **Members interleaved with non-members.** A mailbox is a stream of
    messages, only some of which belong to the thread, and the scan bound
    counts *messages*, not members. Members therefore sit at scan positions
    (:data:`INBOX_STREAM_LENGTH` / :data:`SENT_STREAM_LENGTH` bound the
    streams), and a bound of 4 reaches only the members sitting in the
    newest 4 messages.
3.  **A bound the fixture actually obeys.** :func:`fake_thread_runner` reads
    ``scanUpperBound`` back out of the *generated script* rather than taking it
    as an argument, so narrowing ``scan_messages`` narrows the reply for the
    same reason it would on a real mailbox. A test that passed the bound to the
    fixture directly would pass even if the tool emitted a different one.

Everything here is synthetic (``sender@example.com``, ``Q3 planning``,
``<root@example.com>``): this repository is public and no fixture may carry a
real address, subject, or Message-ID.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

INBOX = "Inbox"
SENT = "Sent Items"
ACCOUNT = "Work"
THREAD_SUBJECT = "Q3 planning"

STRATEGY_PREFIX = "THREAD_STRATEGY|||"
SCAN_CEILING_PREFIX = "THREAD_SCAN_CEILING|||"
DATE_FLOOR_PREFIX = "THREAD_DATE_FLOOR|||"
ATTACHMENTS_PREFIX = "THREAD_ATTACHMENTS|||"

#: The text ``thread_coverage_report()`` emits as a date-floor detail.
DATE_FLOOR_DETAIL = "recent_days cutoff"


@dataclass(frozen=True)
class ThreadMember:
    """One synthetic conversation member and where it sits in its mailbox.

    *position* is the message's 1-based index in its mailbox's newest-first
    stream -- i.e. the smallest ``messages 1 thru N`` slice that reaches it.
    """

    message_id: str
    mailbox: str
    position: int
    subject: str = THREAD_SUBJECT
    sender: str = "sender@example.com"
    received_date: str = "2026-03-07T10:00:00"
    internet_message_id: str = ""
    in_reply_to: str = ""
    references: str = ""
    is_read: bool = False
    was_replied_to: bool = False

    @property
    def imid(self) -> str:
        return self.internet_message_id or f"<m{self.message_id}@example.com>"


def _member(message_id: str, mailbox: str, position: int, **kwargs: object) -> ThreadMember:
    return ThreadMember(message_id=message_id, mailbox=mailbox, position=position, **kwargs)  # type: ignore[arg-type]


#: Both mailboxes are longer than the *old* scan bound (``max_messages``,
#: default 50) and shorter than the window-derived bound a 2-day call now
#: produces (120 + 2 x 15 = 150). That gap is the regression: under the old
#: bound the newest 50 messages of each mailbox hold only three Inbox members
#: and two Sent members -- the live 9-returns-5 case, reproduced exactly -- and
#: under the new one every member is reachable with no ceiling to report.
INBOX_STREAM_LENGTH = 140
SENT_STREAM_LENGTH = 90

#: The pre-fix per-mailbox slice, kept as a name so the regression test reads
#: as "the old bound" rather than as a bare 50.
LEGACY_SCAN_BOUND = 50

INBOX_MEMBERS: tuple[ThreadMember, ...] = (
    _member("601", INBOX, 1, subject="Re: Q3 planning", references="<root@example.com>"),
    _member("602", INBOX, 3, subject="Re: Q3 planning", references="<root@example.com>"),
    _member("603", INBOX, 18, subject="Re: Q3 planning", references="<root@example.com>"),
    _member("604", INBOX, 60, subject="Re: Q3 planning", references="<root@example.com>"),
    _member("605", INBOX, 95, subject="Re: Q3 planning", references="<root@example.com>"),
    _member(
        "606",
        INBOX,
        130,
        subject=THREAD_SUBJECT,
        internet_message_id="<root@example.com>",
    ),
)

#: Three members in ``Sent Items`` -- the mailbox the live bug dropped almost
#: entirely, because a reply you sent sits behind everything you sent since.
SENT_MEMBERS: tuple[ThreadMember, ...] = (
    _member("701", SENT, 2, subject="Re: Q3 planning", sender="me@example.com", references="<root@example.com>"),
    _member("702", SENT, 40, subject="Re: Q3 planning", sender="me@example.com", references="<root@example.com>"),
    _member("703", SENT, 75, subject="Re: Q3 planning", sender="me@example.com", references="<root@example.com>"),
)

ALL_MEMBERS: tuple[ThreadMember, ...] = INBOX_MEMBERS + SENT_MEMBERS

STREAM_LENGTHS = {INBOX: INBOX_STREAM_LENGTH, SENT: SENT_STREAM_LENGTH}

#: The smallest bound that reaches every member of the whole conversation.
FULL_SCAN_BOUND = max(STREAM_LENGTHS.values())


def members_for(mailbox: str) -> tuple[ThreadMember, ...]:
    return tuple(m for m in ALL_MEMBERS if m.mailbox == mailbox)


def member_by_id(message_id: str) -> ThreadMember:
    for member in ALL_MEMBERS:
        if member.message_id == message_id:
            return member
    raise KeyError(message_id)


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------


def record_row(member: ThreadMember, *, content_preview: str = "") -> str:
    """One thread record row, field-for-field as ``thread.py`` emits it.

    Fifteen ``|||`` fields, because ``records._parse_search_records`` splits
    with ``split("|||", 14)``: id, Message-ID, subject, sender, mailbox,
    account, read, date, preview, to, cc, in-reply-to, references, bcc,
    was-replied.
    """
    return "|||".join(
        [
            member.message_id,
            member.imid,
            member.subject,
            member.sender,
            member.mailbox,
            ACCOUNT,
            "true" if member.is_read else "false",
            member.received_date,
            content_preview,
            "",
            "",
            member.in_reply_to,
            member.references,
            "",
            "true" if member.was_replied_to else "false",
        ]
    )


def strategy_header(strategy: str = "subject", matched: int = 0) -> str:
    """The ``THREAD_STRATEGY`` first line, carrying the ``FOUND N`` count."""
    return f"{STRATEGY_PREFIX}{strategy}|||{matched}"


def scan_ceiling_row(mailbox: str, bound: int) -> str:
    return f"{SCAN_CEILING_PREFIX}{mailbox}|||{bound}"


def date_floor_row(mailbox: str, detail: str = DATE_FLOOR_DETAIL) -> str:
    return f"{DATE_FLOOR_PREFIX}{mailbox}|||{detail}"


def attachment_row(message_id: str, count: int) -> str:
    return f"{ATTACHMENTS_PREFIX}{message_id}|||{count}"


def attachment_error_row(message_id: str, reason: str) -> str:
    """The ``-1`` sentinel row: the count was not read, which is not zero."""
    return f"{ATTACHMENTS_PREFIX}{message_id}|||-1|||{reason}"


def synthetic_thread_rows(
    *,
    inbox: int = len(INBOX_MEMBERS),
    sent: int = len(SENT_MEMBERS),
    attachments: dict[str, int] | None = None,
    attachment_errors: dict[str, str] | None = None,
) -> str:
    """Record rows for the newest *inbox* / *sent* members, plus attachment markers.

    Marker rows are interleaved with record rows exactly as the script emits
    them (the attachment marker for a member is appended just before that
    member's record row), so a consumer that fails to lift markers out before
    parsing is caught by this fixture rather than by a tidier one.
    """
    attachments = attachments or {}
    attachment_errors = attachment_errors or {}
    lines: list[str] = []
    for member in INBOX_MEMBERS[:inbox] + SENT_MEMBERS[:sent]:
        if member.message_id in attachment_errors:
            lines.append(attachment_error_row(member.message_id, attachment_errors[member.message_id]))
        elif member.message_id in attachments:
            lines.append(attachment_row(member.message_id, attachments[member.message_id]))
        lines.append(record_row(member))
    return "\n".join(lines)


def synthetic_thread_output(
    *,
    inbox: int = len(INBOX_MEMBERS),
    sent: int = len(SENT_MEMBERS),
    strategy: str = "subject",
    matched: int | None = None,
    extra_rows: list[str] | None = None,
    **kwargs: object,
) -> str:
    """A complete JSON-mode scan reply: strategy header + rows + extra markers."""
    rows = synthetic_thread_rows(inbox=inbox, sent=sent, **kwargs)  # type: ignore[arg-type]
    counted = (inbox + sent) if matched is None else matched
    parts = [strategy_header(strategy, counted)]
    if rows:
        parts.append(rows)
    parts.extend(extra_rows or [])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# A runner that actually obeys the bound the tool emitted
# ---------------------------------------------------------------------------

_SCAN_BOUND_RE = re.compile(r"if messageCount > (\d+) then")
_ANCHOR_PROBE_MARKER = "ANCHOR_MAILBOX|||"
_THREAD_SCRIPT_MARKER = "EMAIL THREAD VIEW"


def script_scan_bound(script: str) -> int:
    """The per-mailbox slice bound the generated thread script carries."""
    match = _SCAN_BOUND_RE.search(script)
    if match is None:  # pragma: no cover - defensive; a missing bound is a bug
        raise AssertionError("thread script carried no scanUpperBound guard")
    return int(match.group(1))


def _anchor_script_targets(script: str, mailbox: str) -> bool:
    """True when an anchor fetch script resolves *mailbox*.

    ``build_mailbox_ref`` spells INBOX as a localized-name loop and every other
    mailbox as a quoted literal, so the two cases are recognised differently.
    """
    if mailbox.casefold() == "inbox":
        return "__mailboxLookupName" in script
    return f'"{mailbox}"' in script


def scan_script_targets_mailbox(script: str, mailbox: str) -> bool:
    """True when the *thread scan* script would visit *mailbox*.

    The fake mailbox stream is only served when the tool actually put that
    mailbox in ``searchMailboxes``; otherwise a test asserting "the Sent member
    came back" would pass on a tool that never looked in Sent.
    ``mailbox="All"`` expands to every mailbox of the account.
    """
    if "set searchMailboxes to every mailbox of targetAccount" in script:
        return True
    if mailbox.casefold() == "inbox":
        return '"INBOX"' in script or '"Inbox"' in script
    return f'"{mailbox}"' in script


@dataclass
class ThreadScanPlan:
    """What the fake Mail should do for one thread call."""

    mailboxes: tuple[str, ...] = (INBOX, SENT)
    strategy: str = "subject"
    #: mailbox -> the scan position at which the ``recent_days`` cutoff fired.
    #: Members at or past that position are never reached, and the mailbox
    #: reports a date floor instead of a ceiling (see ``thread_coverage_report``).
    date_floor_at: dict[str, int] = field(default_factory=dict)
    attachments: dict[str, int] = field(default_factory=dict)
    attachment_errors: dict[str, str] = field(default_factory=dict)
    #: Reply for the ``mailbox="All"`` anchor mailbox probe: a mailbox name, or
    #: None for "the probe found nothing".
    probe_mailbox: str | None = None
    #: The member the anchor fetch resolves to, when the call passes a
    #: ``message_id``.
    anchor: ThreadMember | None = None
    #: Raw extra rows appended to the thread reply (error markers, etc.).
    extra_rows: tuple[str, ...] = ()

    def reached(self, mailbox: str, bound: int) -> list[ThreadMember]:
        """Members of *mailbox* the scan reaches under *bound*."""
        stop = min(bound, STREAM_LENGTHS.get(mailbox, bound))
        floor = self.date_floor_at.get(mailbox)
        if floor is not None:
            stop = min(stop, floor - 1)
        return [m for m in members_for(mailbox) if m.position <= stop]

    def coverage_rows(self, mailbox: str, bound: int) -> list[str]:
        """Replicate ``thread_coverage_report()``: floor wins over ceiling."""
        if mailbox in self.date_floor_at:
            return [date_floor_row(mailbox)]
        stream = STREAM_LENGTHS.get(mailbox, bound)
        if stream > bound:
            return [scan_ceiling_row(mailbox, bound)]
        return []


def fake_thread_runner(plan: ThreadScanPlan | None = None, *, captured: list[str] | None = None):
    """A ``run_applescript`` stand-in that plays a bounded multi-mailbox scan.

    Dispatches on the script's own text: the anchor mailbox probe, the anchor
    by-id fetch, and the thread scan are three different calls through one
    seam. Anything else (the Sent/Drafts reply-state scans) answers empty,
    which those helpers already treat as "no evidence".
    """
    plan = plan or ThreadScanPlan()

    def run(script: str, timeout: int | None = 120) -> str:
        if captured is not None:
            captured.append(script)
        if _ANCHOR_PROBE_MARKER in script:
            return f"{_ANCHOR_PROBE_MARKER}{plan.probe_mailbox}" if plan.probe_mailbox else ""
        if plan.anchor is not None and "whose id is" in script and _THREAD_SCRIPT_MARKER not in script:
            if _anchor_script_targets(script, plan.anchor.mailbox):
                return record_row(plan.anchor)
            return ""
        if _THREAD_SCRIPT_MARKER not in script:
            return ""

        bound = script_scan_bound(script)
        lines: list[str] = []
        matched = 0
        for mailbox in plan.mailboxes:
            if not scan_script_targets_mailbox(script, mailbox):
                continue
            for member in plan.reached(mailbox, bound):
                matched += 1
                if member.message_id in plan.attachment_errors:
                    lines.append(attachment_error_row(member.message_id, plan.attachment_errors[member.message_id]))
                elif member.message_id in plan.attachments:
                    lines.append(attachment_row(member.message_id, plan.attachments[member.message_id]))
                lines.append(record_row(member))
            lines.extend(plan.coverage_rows(mailbox, bound))
        lines.extend(plan.extra_rows)
        return "\n".join([strategy_header(plan.strategy, matched), *lines])

    return run
