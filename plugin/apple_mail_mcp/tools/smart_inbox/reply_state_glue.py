"""Row parsing + reply-state classification for ``get_needs_response``.

Split out of ``needs_response.py`` to stay under the 600 LOC module budget
(``docs/CLAUDE-conventions.md`` § Module line budget) after the
reply-state-annotation change added native ``was_replied_to`` + Drafts
correlation to that tool
(``tasks/active/reply-state-annotation/plan-2026-07-10.md``). Owns the
candidate row shape (``_NeedsResponseRow``), the ``MSG|||...`` line parser,
the priority-label formatter, and the classifier that decides, per
candidate, whether it is replied/drafted and whether it stays in the
result or gets excluded and counted. ``needs_response.py`` owns AppleScript
script building, text formatting, and the ``@mcp.tool`` entrypoint; it
imports the symbols here rather than duplicating them.

Its own per-row ``has_draft`` resolution delegates to
``core.reply_state.resolve_has_draft`` (the same fail-open helper
``tools.reply_state_wiring.annotate_rows_with_reply_state`` uses), rather
than duplicating the "only match when the scan is ok" check inline. This
module still owns the rest of the classification pipeline (replied-signal
merge, skip counting, priority labeling) since that logic is specific to
``get_needs_response`` and has no cross-tool equivalent to fold into.
"""

from dataclasses import dataclass
from typing import Any

from apple_mail_mcp.core.replied import SentReplySnapshot
from apple_mail_mcp.core.reply_state import DraftsSnapshot, resolve_has_draft


@dataclass(frozen=True)
class _NeedsResponseRow:
    """Structured per-message candidate emitted by the inbox script."""

    mail_app_id: str
    internet_message_id: str
    subject: str
    sender: str
    date_str: str
    is_flagged: bool
    has_question: bool
    was_replied_to: bool = False

    @property
    def message_id(self) -> str:
        """Backward-compatible alias for older internal tests/helpers."""
        return self.internet_message_id


def _parse_needs_response_inbox_rows(raw: str) -> list[_NeedsResponseRow]:
    """Parse ``MSG|||...`` lines into ``_NeedsResponseRow`` instances.

    Schema: MSG|||mail_app_id|||internet_message_id|||subject|||sender|||date_str|||is_flagged|||has_question|||was_replied
    Booleans are encoded as ``"true"`` / ``"false"``. Malformed rows are
    skipped silently so a single bad message can't poison the result. Rows
    captured before the reply-state-annotation change (7 or 8 fields, no
    trailing ``was_replied`` token) still parse, with ``was_replied_to=False``
    since the native flag was never read for them.
    """
    rows: list[_NeedsResponseRow] = []
    for line in raw.splitlines():
        if not line.startswith("MSG|||"):
            continue
        parts = line.split("|||", 8)
        was_replied_text = "false"
        if len(parts) == 7:
            # Backwards-compatible parser for older tests/log captures that
            # emitted only the Internet Message-ID in the message_id slot.
            _, internet_message_id, subject, sender, date_str, is_flagged, has_question = parts
            mail_app_id = ""
        elif len(parts) == 8:
            _, mail_app_id, internet_message_id, subject, sender, date_str, is_flagged, has_question = parts
        elif len(parts) == 9:
            (
                _,
                mail_app_id,
                internet_message_id,
                subject,
                sender,
                date_str,
                is_flagged,
                has_question,
                was_replied_text,
            ) = parts
        else:
            continue
        rows.append(
            _NeedsResponseRow(
                mail_app_id=mail_app_id,
                internet_message_id=internet_message_id,
                subject=subject,
                sender=sender,
                date_str=date_str,
                is_flagged=is_flagged.strip().lower() == "true",
                has_question=has_question.strip().lower() == "true",
                was_replied_to=was_replied_text.strip().lower() == "true",
            )
        )
    return rows


def _priority_label(*, has_question: bool, is_flagged: bool, replied: bool, has_draft: bool) -> str:
    """Match the AppleScript priority labeling the legacy tool produced, plus reply-state prefixes."""
    if has_question or is_flagged:
        if has_question and is_flagged:
            label = "HIGH (flagged + question)"
        elif is_flagged:
            label = "HIGH (flagged)"
        else:
            label = "MEDIUM (contains question)"
    else:
        label = "NORMAL"
    prefixes = []
    if replied:
        prefixes.append("[ALREADY REPLIED]")
    if has_draft:
        prefixes.append("[HAS DRAFT]")
    if prefixes:
        label = " ".join((*prefixes, label))
    return label


def _classify_needs_response_rows(
    rows: list[_NeedsResponseRow],
    *,
    sent_reply_snapshot: SentReplySnapshot | None,
    include_already_replied: bool,
    include_drafted: bool,
    drafts_snapshot: DraftsSnapshot | None,
    max_results: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    """Split candidates into (high, normal, skipped_replied_count, skipped_drafted_count).

    Each item dict matches the JSON output shape; the text formatter just
    re-renders the same dicts. The high/normal split mirrors the legacy
    AppleScript behavior: ``has_question or is_flagged`` -> high.

    A row is treated as replied when its native ``was_replied_to`` flag is
    set, OR (only when the caller ran the opt-in Sent-header scan, i.e.
    *replied_ids* is non-empty) its Internet Message-ID matches that scan.
    ``has_draft`` comes from *drafts_snapshot* and stays ``None`` (never
    excluded, fail open) whenever the snapshot was not run or errored. A
    row excluded for both reasons at once still increments both skip
    counters, so the counts stay a faithful account of why rows vanished.
    """
    high: list[dict[str, Any]] = []
    normal: list[dict[str, Any]] = []
    skipped_replied = 0
    skipped_drafted = 0

    for row in rows:
        if len(high) + len(normal) >= max_results:
            break

        has_sent_reply = (
            sent_reply_snapshot.matches(row.internet_message_id) if sent_reply_snapshot is not None else None
        )
        reply_state: bool | None
        if row.was_replied_to or has_sent_reply is True:
            reply_state = True
        elif has_sent_reply is False:
            reply_state = False
        else:
            reply_state = None
        replied = reply_state is True

        has_draft = resolve_has_draft(
            drafts_snapshot,
            subject=row.subject,
            sender_email=row.sender,
            internet_message_id=row.internet_message_id or None,
            email_date=row.date_str,
        )

        excluded = False
        if replied and not include_already_replied:
            skipped_replied += 1
            excluded = True
        if has_draft and not include_drafted:
            skipped_drafted += 1
            excluded = True
        if excluded:
            continue

        priority = _priority_label(
            has_question=row.has_question,
            is_flagged=row.is_flagged,
            replied=replied,
            has_draft=bool(has_draft),
        )
        entry = {
            "subject": row.subject,
            "sender": row.sender,
            "date": row.date_str,
            "priority": priority,
            "already_replied": replied,
            "was_replied_to": row.was_replied_to,
            "mail_was_replied_to": row.was_replied_to,
            "has_sent_reply": has_sent_reply,
            "reply_state": reply_state,
            "has_draft": has_draft,
            "message_id": row.mail_app_id,
            "internet_message_id": row.internet_message_id,
        }
        if row.has_question or row.is_flagged:
            high.append(entry)
        else:
            normal.append(entry)

    return high, normal, skipped_replied, skipped_drafted
