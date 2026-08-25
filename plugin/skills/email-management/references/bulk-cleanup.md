# Bulk Cleanup Operations

Bulk operations remove or relocate many messages at once. Apple Mail offers no undo for permanent deletes, so this reference exists to keep cleanup safe and reversible.

## ID-first rule (v3.7.0)

Mutations default to **`message_ids=[...]`** from a prior bounded `search_emails` or `list_inbox_emails` call. Filter-based `move_email` / `update_email_status` / `manage_trash` without ids returns **`TARGET_SELECTOR_DEPRECATED`** for subject or sender target selectors; date-only or explicit bulk scans still require **`allow_filter_scan=True`**.

See **`email-archive-cleanup`** for the canonical campaign shape.

**Archive moves inherit the Human-Sender Screen.** Every archive sequence below (any `move_email(..., to_mailbox="Archive...")` step) must apply the Human-Sender Screen documented in `email-archive-cleanup`'s `SKILL.md` before the dry run: drop human-looking or ambiguous senders from the candidate ids and archive only the confidently automated or promotional subset. Trash sequences that target confirmed spam or automated senders are not exempt either; when a candidate could be a real correspondent, apply the same screen before queuing it for `manage_trash`.

## Safety Defaults

The MCP server enforces conservative defaults to prevent runaway destructive operations:

| Tool | Default cap | Override parameter |
|------|-------------|--------------------|
| `manage_trash` (move_to_trash, delete_permanent) | 5 messages | `max_deletes=N` |
| `manage_trash` (empty_trash) | hard confirmation required, previews by default | `confirm_empty=True` plus `dry_run=False` |
| `update_email_status` | 10 messages | `max_updates=N` |
| `move_email` | 50 messages | `max_moves=N` |

Raise these caps only after a confirming search shows the user exactly which messages will be affected.

## Safe Cleanup Sequence

1. **Identify candidates** with `search_emails()` or `list_inbox_emails()`, narrowed by sender, date range, mailbox, or read status.
2. **Collect `message_id`s** from the preview (first ten subjects for human confirmation).
3. **Dry-run by id**: `move_email(message_ids=[...], to_mailbox="...", dry_run=True)` or `manage_trash(message_ids=[...], dry_run=True)`.
4. **Move to Trash first** with `manage_trash(action="move_to_trash", message_ids=[...], dry_run=False)`. Reversible inside Apple Mail.
5. **Verify** by listing Trash or re-searching the source window.
6. **Permanent delete only when certain**: preview with `manage_trash(action="delete_permanent", message_ids=[...])`, then repeat with `dry_run=False` to delete.
7. **Empty Trash is the nuclear option.** Preview first with `manage_trash(action="empty_trash", confirm_empty=True)`, which reports what would go and deletes nothing. Repeat the call with `dry_run=False` only after explicit user confirmation.

## Pre-Cleanup Backup

Before deleting a large mailbox, export the relevant bounded slice: `export_emails(scope="entire_mailbox", mailbox="Archive/2023", max_emails=50, format="html")` (page with `offset` for more than one call's worth; `max_emails` is hard-capped at 50 per call). For a sender or person-specific cleanup, prefer `export_emails(scope="filtered", sender_domain="example.com", recent_days=30, max_emails=25)` or `export_emails(scope="correspondent", email_address="person@example.com", include_sent=True, recent_days=30, max_emails=25)`. The user gets a local copy in case a permanent delete removes something important.

For preservation or migration evidence, use reviewed exact ids (or one bounded page) with `format="eml"`; it preserves Mail's raw RFC 822 source headers and MIME. Add `include_attachments=True` only when the attachment bundle is needed: each file is capped at 25 MiB and each bounded export batch at 100 MiB, with skipped files reported. Attachment reads can be slow on cold Exchange or Gmail caches, so start with a small page and raise `timeout` from its 120-second default only when necessary.

## Common Cleanup Patterns

### Purge old read newsletters

```text
search_emails(sender_exact="newsletter@example.com", read_status="read", recent_days=30)
# collect message_ids from results
manage_trash(action="move_to_trash", message_ids=[...], dry_run=True)
manage_trash(action="move_to_trash", message_ids=[...], dry_run=False)
```

### Archive everything older than 90 days

```text
search_emails(date_from="2025-01-01", date_to="2025-02-20", read_status="read")
# Apply the Human-Sender Screen (email-archive-cleanup/SKILL.md) to the results here:
# drop human-looking or ambiguous senders before the ids below are built.
move_email(message_ids=[...], to_mailbox="Archive/2025", dry_run=True)
move_email(message_ids=[...], to_mailbox="Archive/2025")
```

### Empty a defunct project folder

1. `export_emails(scope="entire_mailbox", mailbox="Projects/OldProject", max_emails=50, offset=0)` for the audit trail (page with `offset` if the mailbox holds more than 50 messages; `max_emails` is capped at 50 per call).
2. `search_emails(mailbox="Projects/OldProject", ...)` → collect ids. (`list_inbox_emails` has no `mailbox` parameter; it only reads Inbox.)
3. Preview with `manage_trash(action="move_to_trash", message_ids=[...], max_deletes=N)` in batches of ≤50, then repeat each call with `dry_run=False` to act. `max_deletes` defaults to **5**, so a batch larger than that needs an explicit cap or most of the ids are silently left behind.
4. Verify, then preview with `manage_trash(action="empty_trash", confirm_empty=True)` and repeat with `dry_run=False` only after explicit user confirmation.

## Confirmation Script

Before any bulk destructive action, restate to the user:

- The exact tool call about to run (with `message_ids` count).
- The expected affected count from the preview search.
- Whether the action is reversible (move_to_trash) or permanent (delete_permanent, empty_trash).

If any of those three are unclear, stop and ask.
