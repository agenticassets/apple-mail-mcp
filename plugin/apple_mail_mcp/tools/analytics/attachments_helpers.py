"""Pure, Mail-free helpers for ``list_email_attachments``.

Split out of ``attachments.py`` the same way ``search/thread_helpers.py`` sits
beside ``search/thread.py``: row parsing, chunk merging, and the text renderer
live here, while the tool and its AppleScript builders stay in
``attachments.py``. Nothing in this module calls ``run_applescript`` or opens an
AppleScript ``try``, so it is not a patch seam and adds no entry to the
bare-``try`` ratchet. ``attachments.py`` re-exports every name below so an
existing ``patch('...analytics.attachments.<name>')`` still fires.

Why the AppleScript needs a per-message "seen" row
--------------------------------------------------
The listing script emits one row per *attachment*, so a message that resolved
and genuinely carries no attachment emits nothing at all — byte-identical to an
id that is not in any searched mailbox. Reporting the second case (a silent,
confident subset) without inventing the first requires an explicit signal, so
each resolved message also emits one ``SEEN_MESSAGE|||`` row carrying its
mailbox and envelope fields. A 7-field payload with no ``SEEN_MESSAGE`` rows
(the pre-3.x row shape) is still accepted: every id that produced an attachment
row is treated as seen.

The two marker prefixes mirror ``tools/search/records.py`` (pattern P1): an
in-band row the AppleScript appends to its own output, which the Python parser
routes to a structured channel instead of the item list.
"""

from dataclasses import dataclass, field
from typing import Any

#: Same literal as ``tools.search.records._ERROR_MAILBOX_PREFIX``. One mailbox
#: that failed to resolve or threw must not abort the other mailboxes, so the
#: AppleScript catches it per mailbox and reports it in band.
ERROR_MAILBOX_PREFIX = "ERROR_MAILBOX|||"

#: ``SEEN_MESSAGE|||<mailbox>|||<id>|||<subject>|||<sender>|||<date>``.
SEEN_MESSAGE_PREFIX = "SEEN_MESSAGE|||"

#: Fields in an attachment row. The 8th (``mailbox``) was added in 3.x; a
#: 7-field row is still parsed so older mocked payloads keep working.
_ATTACHMENT_ROW_FIELDS = 7


@dataclass
class AttachmentScan:
    """One parsed AppleScript payload: items, resolved messages, failures."""

    items: list[dict[str, Any]] = field(default_factory=list)
    seen: dict[str, dict[str, str]] = field(default_factory=dict)
    mailbox_errors: list[dict[str, str]] = field(default_factory=list)

    def merge(self, other: "AttachmentScan") -> None:
        """Fold another chunk's scan into this one (union semantics)."""
        self.items.extend(other.items)
        for message_id, envelope in other.seen.items():
            self.seen.setdefault(message_id, envelope)
        for error in other.mailbox_errors:
            if error not in self.mailbox_errors:
                self.mailbox_errors.append(error)


def parse_attachment_output(text: str, default_mailbox: str = "") -> AttachmentScan:
    """Parse the listing script's rows into items, seen messages, and errors."""
    scan = AttachmentScan()
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith(ERROR_MAILBOX_PREFIX):
            mailbox, _, message = line[len(ERROR_MAILBOX_PREFIX) :].partition("|||")
            error = {"mailbox": mailbox.strip(), "message": message.strip()}
            if error not in scan.mailbox_errors:
                scan.mailbox_errors.append(error)
            continue
        if line.startswith(SEEN_MESSAGE_PREFIX):
            # Pad first, so a short row is read as empty fields rather than
            # needing its own length check before the id can be looked at.
            fields = line[len(SEEN_MESSAGE_PREFIX) :].split("|||")
            fields += [""] * (5 - len(fields))
            message_id = fields[1].strip()
            if not message_id:
                continue
            scan.seen[message_id] = {
                "mailbox": fields[0].strip() or default_mailbox,
                "subject": fields[2].strip(),
                "sender": fields[3].strip(),
                "received_date": fields[4].strip(),
            }
            continue

        parts = line.split("|||")
        if len(parts) not in {_ATTACHMENT_ROW_FIELDS, _ATTACHMENT_ROW_FIELDS + 1}:
            continue
        message_id, subject, sender, received_date, attachment_index, filename, size_text = parts[:7]
        row_mailbox = parts[_ATTACHMENT_ROW_FIELDS].strip() if len(parts) > _ATTACHMENT_ROW_FIELDS else default_mailbox
        try:
            index_value = int(attachment_index)
        except ValueError:
            continue
        try:
            size_bytes: int | None = int(size_text)
        except ValueError:
            size_bytes = None
        scan.items.append(
            {
                "message_id": message_id,
                "subject": subject,
                "sender": sender,
                "received_date": received_date,
                "attachment_index": index_value,
                "filename": filename,
                "size_bytes": size_bytes,
                "mailbox": row_mailbox,
            }
        )
        # A payload with no SEEN_MESSAGE rows must not report every id it did
        # return as unresolved, so an attachment row is itself proof of a read.
        scan.seen.setdefault(
            message_id,
            {
                "mailbox": row_mailbox,
                "subject": subject,
                "sender": sender,
                "received_date": received_date,
            },
        )
    return scan


def unresolved_ids_message(unresolved: list[str], requested: list[str], mailboxes: list[str]) -> str:
    """Render the "N of M ids were not found" entry shared by both modes."""
    return (
        f"{len(unresolved)} of {len(requested)} requested message id(s) were not found in the "
        f"searched mailbox(es): {', '.join(mailboxes)}"
    )


def mailbox_error_texts(mailbox_errors: list[dict[str, str]]) -> list[str]:
    """Render mailbox failures as flat ``<mailbox>: <message>`` strings."""
    return [f"{item.get('mailbox') or '?'}: {item.get('message', '')}" for item in mailbox_errors]


def _format_size(size_bytes: int | None) -> str:
    """Render an attachment size the way the pre-3.x text mode did."""
    return f" ({size_bytes // 1024} KB)" if size_bytes is not None else ""


def format_attachment_text(
    header_label: str,
    scan: AttachmentScan,
    requested_ids: list[str],
    mailboxes: list[str],
    error_texts: list[str],
) -> str:
    """Render the human-readable listing, ending with explicit partial lines."""
    lines = [
        f"ATTACHMENTS FOR: {header_label}",
        f"Mailboxes searched: {', '.join(mailboxes)}",
        "",
    ]
    by_message: dict[str, list[dict[str, Any]]] = {}
    for item in scan.items:
        by_message.setdefault(str(item.get("message_id", "")), []).append(item)

    found = 0
    for message_id in requested_ids:
        envelope = scan.seen.get(message_id)
        if envelope is None:
            continue
        found += 1
        attachments = sorted(by_message.get(message_id, []), key=lambda row: row["attachment_index"])
        lines.append(f"✉ {envelope.get('subject', '')}")
        lines.append(f"   From: {envelope.get('sender', '')}")
        lines.append(f"   Date: {envelope.get('received_date', '')}")
        lines.append(f"   Mailbox: {envelope.get('mailbox', '')}")
        if attachments:
            lines.append(f"   Attachments ({len(attachments)}):")
            lines.extend(f"   📎 {row['filename']}{_format_size(row['size_bytes'])}" for row in attachments)
        else:
            lines.append("   No attachments")
        lines.append("")

    lines.append("========================================")
    lines.append(f"FOUND: {found} matching email(s)")
    lines.append("========================================")
    lines.extend(f"PARTIAL: ⚠ {text}" for text in error_texts)
    return "\n".join(lines)
