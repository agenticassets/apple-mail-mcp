"""Pure text/JSON formatters for ``get_inbox_overview`` (no AppleScript I/O).

Split out of ``overview.py`` to keep both modules inside the 600 LOC budget.
The script builder, parser, and the tool itself stay in ``overview.py``; every
name here is re-exported through the ``inbox`` facade, so the existing
``inbox_tools._format_overview_json`` style test seams keep firing.
"""

from typing import Any

from apple_mail_mcp.core.reply_state import reply_state_tags
from apple_mail_mcp.tools.reply_state_wiring import build_draft_scan_status, build_sent_reply_scan_status
from apple_mail_mcp.tools.unread_provenance import (
    unread_count_disclosure,
    unread_count_text_footer,
    unread_count_text_label,
)


def _account_unread_provenance(acct: dict[str, Any], *, include_note: bool = False) -> dict[str, Any]:
    """Cached-count provenance for one parsed account payload."""
    return unread_count_disclosure(
        cached_unread=acct.get("unread"),
        total_messages=acct.get("total"),
        sampled_unread=acct.get("sampled_unread"),
        include_note=include_note,
    )


def _format_overview(
    accounts: list[dict[str, Any]],
    errors: list[str],
    *,
    include_mailboxes: bool = True,
    include_recent: bool = True,
    include_suggestions: bool = True,
    max_recent: int = 10,
    compact: bool = False,
) -> str:
    """Format combined per-account overview payloads into the legacy text shape."""
    lines: list[str] = []
    if not compact:
        lines.append("╔══════════════════════════════════════════╗")
        lines.append("║      EMAIL INBOX OVERVIEW                ║")
        lines.append("╚══════════════════════════════════════════╝")
        lines.append("")
    lines.append("📊 UNREAD EMAILS BY ACCOUNT")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    total_unread = 0
    suspect_details: list[str] = []
    for acct in accounts:
        name = acct.get("account") or "(unknown)"
        if acct.get("error"):
            lines.append(f"  ❌ {name}: Error accessing inbox")
            continue
        unread = acct.get("unread") or 0
        total = acct.get("total") or 0
        total_unread += unread
        provenance = _account_unread_provenance(acct)
        suspect = bool(provenance.get("unread_count_suspect"))
        if suspect:
            suspect_details.append(f"{name}: {provenance['unread_count_suspect_detail']}")
        cached_label = unread_count_text_label(suspect=suspect)
        prefix = "⚠️ " if unread > 0 else "✅"
        if compact:
            lines.append(f"  {prefix} {name}: {unread} unread{cached_label}")
        else:
            lines.append(f"  {prefix} {name}: {unread} unread{cached_label} ({total} total)")

    lines.append("")
    lines.append(f"📈 TOTAL UNREAD: {total_unread} across all accounts{unread_count_text_label()}")
    lines.extend(unread_count_text_footer(suspect_details))

    if include_mailboxes and not compact:
        lines.append("")
        lines.append("")
        lines.append(f"📁 MAILBOX STRUCTURE (unread counts{unread_count_text_label()})")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        for acct in accounts:
            name = acct.get("account") or "(unknown)"
            lines.append(f"\nAccount: {name}")
            for mb_name, mb_unread in acct.get("mailboxes", []):
                if "/" in mb_name:
                    if mb_unread > 0:
                        lines.append(f"     └─ {mb_name.split('/', 1)[1]} ({mb_unread} unread)")
                else:
                    if mb_unread > 0:
                        lines.append(f"  📂 {mb_name} ({mb_unread} unread)")
                    else:
                        lines.append(f"  📂 {mb_name}")
            if acct.get("mailboxes_truncated"):
                lines.append("  ⚠ Mailbox list truncated — account has more mailboxes than the cap allows.")

    if include_recent:
        lines.append("")
        lines.append("")
        label = f"📬 RECENT EMAILS PREVIEW ({max_recent} Most Recent)"
        lines.append(label)
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        recent_combined = []
        for acct in accounts:
            name = acct.get("account") or "(unknown)"
            for r in acct.get("recent", []):
                recent_combined.append((name, r))
        display_count = 0
        for name, r in recent_combined:
            if display_count >= max_recent:
                break
            display_count += 1
            indicator = "✓" if r["is_read"] else "✉"
            tags = reply_state_tags(r.get("was_replied_to"), r.get("has_draft"))
            tag_text = f" {' '.join(tags)}" if tags else ""
            lines.append("")
            lines.append(f"{indicator}{tag_text} {r['subject']}")
            if not compact:
                lines.append(f"   Account: {name}")
            lines.append(f"   From: {r['sender']}")
            lines.append(f"   Date: {r['date']}")

        if display_count == 0:
            lines.append("")
            lines.append("No recent emails found.")

    if include_suggestions and not compact:
        lines.append("")
        lines.append("")
        lines.append("💡 SUGGESTED ACTIONS FOR ASSISTANT")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("Based on this overview, consider suggesting:")
        lines.append("")
        if total_unread > 0:
            lines.append("1. 📧 Review unread emails - Use list_inbox_emails to show recent unread messages")
            lines.append(
                "2. 🔍 Search for action items - Look for keywords like 'urgent', 'action required', 'deadline'"
            )
            lines.append("3. 📤 Move processed emails - Suggest moving read emails to appropriate folders")
        else:
            lines.append("1. ✅ Inbox is clear! No unread emails.")
        lines.append("4. 📋 Organize by topic - Suggest moving emails to project-specific folders")
        lines.append("5. ✉️  Draft replies - Identify emails that need responses")
        lines.append("6. 🗂️  Archive old emails - Move older read emails to archive folders")
        lines.append("7. 🔔 Highlight priority items - Identify emails from important senders or with urgent keywords")
        lines.append("")
        lines.append("═══════════════════════════════════════════════════")
        lines.append("💬 Ask me to drill down into any account or take specific actions!")
        lines.append("═══════════════════════════════════════════════════")

    if errors:
        lines.append("")
        lines.append(f"PARTIAL: {len(errors)} account(s) timed out: {', '.join(errors)}")

    return "\n".join(lines)


def _overview_suggestions(total_unread: int) -> list[str]:
    """Action suggestions mirrored from the text-mode overview footer."""
    if total_unread > 0:
        return [
            "Review unread emails - Use list_inbox_emails to show recent unread messages",
            "Search for action items - Look for keywords like 'urgent', 'action required', 'deadline'",
            "Move processed emails - Suggest moving read emails to appropriate folders",
            "Organize by topic - Suggest moving emails to project-specific folders",
            "Draft replies - Identify emails that need responses",
            "Archive old emails - Move older read emails to archive folders",
            "Highlight priority items - Identify emails from important senders or with urgent keywords",
        ]
    return [
        "Inbox is clear! No unread emails.",
        "Organize by topic - Suggest moving emails to project-specific folders",
        "Draft replies - Identify emails that need responses",
        "Archive old emails - Move older read emails to archive folders",
        "Highlight priority items - Identify emails from important senders or with urgent keywords",
    ]


def _overview_json_error(
    error: str,
    *,
    account: str | None = None,
    include_mailboxes: bool = True,
    include_recent: bool = True,
    include_suggestions: bool = True,
    max_recent: int = 10,
    message: str | None = None,
    errors: list[str] | None = None,
    draft_scan: dict[str, Any] | None = None,
    sent_reply_scan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error": error,
        "output_format": "json",
        "include_mailboxes": include_mailboxes,
        "include_recent": include_recent,
        "include_suggestions": include_suggestions,
        "max_recent": max_recent,
        "total_unread": 0,
        "accounts": [],
        "suggestions": [],
        "errors": errors or [],
        "draft_scan": draft_scan if draft_scan is not None else build_draft_scan_status({}),
        "sent_reply_scan": sent_reply_scan if sent_reply_scan is not None else build_sent_reply_scan_status({}),
        **unread_count_disclosure(),
    }
    if account is not None:
        payload["account"] = account
    if message is not None:
        payload["message"] = message
    return payload


def _format_overview_json(
    accounts: list[dict[str, Any]],
    errors: list[str],
    *,
    account: str | None = None,
    include_mailboxes: bool = True,
    include_recent: bool = True,
    include_suggestions: bool = True,
    max_recent: int = 10,
    draft_scan: dict[str, Any] | None = None,
    sent_reply_scan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return structured overview payload for JSON mode.

    ``recent`` items already carry ``was_replied_to``/``has_draft`` when the
    caller pre-annotated them via ``annotate_rows_with_reply_state``; this
    only threads the aggregate *draft_scan* onto the top-level payload.
    """
    total_unread = 0
    account_rows: list[dict[str, Any]] = []
    first_suspect: dict[str, Any] = {}
    for acct in accounts:
        row: dict[str, Any] = {"account": acct.get("account")}
        if acct.get("error"):
            row["error"] = acct["error"]
        else:
            row["unread"] = acct.get("unread") or 0
            row["total"] = acct.get("total") or 0
            total_unread += row["unread"]
            provenance = _account_unread_provenance(acct)
            row.update(provenance)
            if provenance.get("unread_count_suspect") and not first_suspect:
                first_suspect = provenance
            if include_mailboxes:
                row["mailboxes"] = [{"path": name, "unread": unread} for name, unread in acct.get("mailboxes", [])]
                if acct.get("mailboxes_truncated"):
                    row["mailboxes_truncated"] = True
            if include_recent:
                row["recent"] = acct.get("recent", [])[:max_recent]
        account_rows.append(row)

    # Envelope provenance carries the prose note once; if any account's cached
    # count was caught out, promote that verdict so a caller reading only the
    # envelope still sees it. Merging the whole block adds only the suspect
    # fields — its source/measured values are the cached ones already here.
    envelope_disclosure = unread_count_disclosure()
    envelope_disclosure.update(first_suspect)

    payload: dict[str, Any] = {
        "output_format": "json",
        "include_mailboxes": include_mailboxes,
        "include_recent": include_recent,
        "include_suggestions": include_suggestions,
        "max_recent": max_recent,
        "total_unread": total_unread,
        "accounts": account_rows,
        "suggestions": _overview_suggestions(total_unread) if include_suggestions else [],
        "errors": errors,
        "draft_scan": draft_scan if draft_scan is not None else build_draft_scan_status({}),
        "sent_reply_scan": sent_reply_scan if sent_reply_scan is not None else build_sent_reply_scan_status({}),
        **envelope_disclosure,
    }
    if account is not None:
        payload["account"] = account
    return payload
