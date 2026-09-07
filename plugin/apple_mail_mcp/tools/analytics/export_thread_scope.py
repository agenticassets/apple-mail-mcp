"""Pure-Python helpers for ``export_emails(scope="thread")``.

Two defects motivated this module (AGENTIC-2799), both reproduced live:

1. ``candidate_mailboxes`` was a hardcoded ``["INBOX"] + sent names`` list,
   so a thread whose members live anywhere else (Archive, a project
   folder, a second account's Sent container) silently exported fewer
   messages than the thread reported, under an unqualified success
   banner.
2. Nothing reconciled the ids handed to the exporter against the ids the
   exporter actually wrote, and nothing propagated
   ``get_email_thread``'s own incompleteness caveats.

Everything here is string and list manipulation: **no AppleScript**.
Keeping the caveat and mailbox-derivation logic out of ``export.py``
keeps that module inside its line budget and makes this logic unit
testable without mocking Mail.app.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from apple_mail_mcp.constants import SCAN_BOUNDS

#: Gmail's virtual container. Mail.app cannot open it directly and it is
#: the entire remote store, so it must never enter a candidate scan list.
ALL_MAIL_MARKER = "all mail"

#: Fallback sent-container names, in the order the exporter should try them.
#: Order is behaviour: it is the order ``run_multi_mailbox_id_export`` opens
#: them in, and first match wins.
SENT_MAILBOX_NAMES: tuple[str, ...] = ("Sent Mail", "Sent", "Sent Messages", "Sent Items")

_SENT_MAILBOX_LEAVES = frozenset(name.lower() for name in SENT_MAILBOX_NAMES)

#: Line the AppleScript exporter writes for every id it successfully wrote.
EXPORTED_LINE_PREFIX = "✓ Exported message_id "

#: Repo-wide text-mode marker for an incomplete result.
PARTIAL_PREFIX = "PARTIAL: "

THREAD_EXPORT_BANNER = "THREAD EXPORT"


def _leaf_name(name: str) -> str:
    """Return the last path segment of a mailbox name (``[Gmail]/Sent`` -> ``Sent``)."""
    return name.rsplit("/", 1)[-1].strip()


def is_all_mail_name(name: str) -> bool:
    """True for Gmail's virtual all-mail container, in any casing or prefix."""
    return ALL_MAIL_MARKER in name.strip().lower()


def is_sent_mailbox_name(name: str) -> bool:
    """True when *name* denotes a sent container (leaf-matched, case-insensitive)."""
    return _leaf_name(name).lower() in _SENT_MAILBOX_LEAVES


def derive_thread_candidate_mailboxes(
    records: Iterable[Mapping[str, Any]],
    *,
    include_sent: bool,
    requested_mailbox: str | None = None,
    max_mailboxes: int | None = None,
) -> tuple[list[str], bool]:
    """Derive the mailboxes to scan for a thread's ids.

    Real mailbox names come from each thread record's ``mailbox`` field
    (first-seen order, de-duplicated case-insensitively), then the
    requested mailbox, then the historical fallbacks so nothing that
    worked before regresses. ``All Mail`` is always dropped; sent
    containers are dropped when ``include_sent`` is False.

    Returns ``(candidate_mailboxes, truncated)`` where *truncated* says
    the cap dropped at least one otherwise-eligible mailbox.
    """
    cap = max_mailboxes if max_mailboxes is not None else int(SCAN_BOUNDS["MAX_MAILBOXES_PER_SEARCH"])
    ordered: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        name = raw.strip()
        if not name or is_all_mail_name(name):
            return
        if not include_sent and is_sent_mailbox_name(name):
            return
        key = name.lower()
        if key in seen:
            return
        seen.add(key)
        ordered.append(name)

    for record in records:
        add(str(record.get("mailbox") or ""))
    if requested_mailbox and requested_mailbox.strip().lower() != "all":
        add(requested_mailbox)
    add("INBOX")
    if include_sent:
        for name in SENT_MAILBOX_NAMES:
            add(name)

    if cap > 0 and len(ordered) > cap:
        return ordered[:cap], True
    return ordered, False


def exported_message_ids(result_text: str, requested_ids: Sequence[str]) -> list[str]:
    """Ids the exporter reported as written, intersected with *requested_ids*.

    The exporter emits one ``✓ Exported message_id <id>: <subject>`` line
    per written message and one ``⚠ No email found for message_id <id>``
    line per id that resolved in no candidate mailbox, so the written set
    is recoverable from its own text. Intersecting with the requested ids
    keeps a subject that happens to contain a newline plus the marker
    from inflating the count.
    """
    wanted = set(requested_ids)
    found: list[str] = []
    seen: set[str] = set()
    for line in result_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(EXPORTED_LINE_PREFIX):
            continue
        candidate = stripped[len(EXPORTED_LINE_PREFIX) :].split(":", 1)[0].strip()
        if candidate in wanted and candidate not in seen:
            seen.add(candidate)
            found.append(candidate)
    return found


def thread_payload_caveats(payload: Mapping[str, Any]) -> list[str]:
    """Human-readable caveat lines carried by a ``get_email_thread`` payload.

    Reads every field defensively: the thread tool is free to omit any of
    them, and an older installed build omits the newer ones entirely.
    """
    caveats: list[str] = []
    raw_warnings = payload.get("warnings")
    if isinstance(raw_warnings, list):
        for warning in raw_warnings:
            text = str(warning).strip()
            if text:
                caveats.append(text)

    raw_errors = payload.get("errors")
    if isinstance(raw_errors, list):
        for error in raw_errors:
            text = str(error).strip()
            if text and text not in caveats:
                caveats.append(text)

    matched = payload.get("matched")
    returned = payload.get("returned")
    if isinstance(matched, int) and isinstance(returned, int) and matched > returned:
        text = f"get_email_thread matched {matched} message(s) but returned {returned}"
        if text not in caveats:
            caveats.append(text)

    if payload.get("candidate_scan_incomplete"):
        caveats.append(
            "get_email_thread reported an incomplete candidate scan; the thread itself may be missing members"
        )

    if payload.get("thread_incomplete") and not caveats:
        # ``thread_incomplete`` without any attribution still has to be said
        # out loud, or the export prints an unqualified success banner for a
        # thread the scan already knows it may have truncated.
        caveats.append("get_email_thread reported the thread scan may have missed members")

    return caveats


def format_thread_export(
    *,
    result_text: str,
    requested_ids: Sequence[str],
    candidate_mailboxes: Sequence[str],
    mailboxes_truncated: bool,
    thread_payload: Mapping[str, Any],
) -> str:
    """Render the thread-export result, qualified by every known caveat.

    The ``THREAD EXPORT`` banner stays first for compatibility. A thread
    export that may be missing members never prints it unqualified: a
    ``PARTIAL:`` block follows it whenever fewer ids were written than
    requested, the mailbox list was capped, or ``get_email_thread``
    reported its own incompleteness.
    """
    caveats: list[str] = []

    exported = exported_message_ids(result_text, requested_ids)
    if len(exported) < len(requested_ids):
        searched = ", ".join(candidate_mailboxes) if candidate_mailboxes else "none"
        caveats.append(
            f"exported {len(exported)} of {len(requested_ids)} thread message(s). Searched mailboxes: {searched}."
        )
    if mailboxes_truncated:
        caveats.append(
            f"mailbox search list was capped at {len(candidate_mailboxes)}; "
            "some mailboxes holding this thread were not searched."
        )
    caveats.extend(thread_payload_caveats(thread_payload))

    if not caveats:
        return f"{THREAD_EXPORT_BANNER}\n\n" + result_text
    block = "\n".join(f"{PARTIAL_PREFIX}{line}" for line in caveats)
    return f"{THREAD_EXPORT_BANNER}\n\n{block}\n\n" + result_text
