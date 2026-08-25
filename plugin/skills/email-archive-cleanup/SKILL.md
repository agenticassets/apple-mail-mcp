---
name: email-archive-cleanup
description: This skill should be used when the user asks to "archive old mail safely", "move everything from X sender" (screens out human correspondents unless confidently spam), "clear newsletters", "bulk mark read after export", "preview what would move with dry_run=True", or run staged cleanup campaigns. Uses search_emails, move_email(dry_run=True) preview → execute, update_email_status, export_emails, manage_trash (max_deletes cap), synchronize_account, and get_statistics when measuring progress. Do NOT use for taxonomy design sessions without execution (mailbox-taxonomy), proposing Mail filter rules copy (mail-rules-advisor only), drafting mail (email-drafting), or the 5-minute triage skim (inbox-triage).
---

# Archive And Cleanup Campaigns

High-leverage transformations with **explicit human checkpoints**. Optimize for reversible moves (`Archive`, `Trash` soft-delete) unless the operator understands permanent deletes.

## Human-Sender Screen (apply before every archive proposal)

Archiving is for promotional and marketing mail, spam, automated notifications and system alerts, newsletters, receipts, order and shipping confirmations, calendar and system notices, and other bulk or automated mail. It is not for messages from real people.

Never propose archiving a message from a real person the operator corresponds with (a human sender, especially anyone the operator has emailed or replied to, or who addresses the operator by name) unless confident the message is spam. Apply this screen while building the candidate list during evidence-gathering, before any id reaches `move_email(dry_run=True, ...)`; it is a filter on what gets proposed, not a warning attached afterward.

Every signal below uses a field a tool actually returns. Do not rely on headers or filters the tools do not expose (there is no `List-Unsubscribe` field and no direct sent-to filter). `search_emails` and `list_inbox_emails` rows also do **not** carry `in_reply_to` or `references`; those threading headers are emitted empty on discovery rows and only come back from `get_email_by_id` / `get_email_by_ids` / `get_email_thread`.

**Keep visible, do not archive, when the sender shows:**

- A personalized greeting or one-to-one tone in `content_preview` rather than a bulk template
- A thread the operator already engaged with: `was_replied_to=true` (Mail's native "was replied to" property, always present on every discovery row) or `has_draft=true` (a correlated reply draft exists)
- A human-looking From name and address, not `noreply@`, `no-reply@`, `notifications@`, `marketing@`, or a bulk ESP domain
- No unsubscribe link or marketing/list footer visible in `content_preview`
- Best-effort correspondence history: there is no direct sent-to filter, so `search_emails` cannot ask "did the operator ever email this address." When the stakes are high, search `mailbox="Sent"` for a bounded window and fetch full messages with `get_email_by_id`/`get_email_by_ids` to check the `to` field for the correspondent's address (`search_emails` results always leave `to` empty). Treat missing history as inconclusive, not as evidence the sender is automated.

**Safe to archive when the sender shows:**

- `noreply@`, `no-reply@`, `notifications@`, `marketing@`, or a known bulk ESP domain
- An unsubscribe link or marketing/list footer visible in the message body preview (`content_preview`), a promotional template, or newsletter formatting
- An automated receipt, order or shipping confirmation, or calendar/system notification
- A one-to-one message versus a bulk blast: a generic salutation or template body in `content_preview` with no engagement (`was_replied_to=false` and `has_draft=false`) points toward automated or bulk mail. Treat `has_draft=null` as unknown, not as "no draft"; the bounded Drafts scan was skipped or truncated
- Content that is confidently spam

When it is uncertain whether a sender is a real person or an automated/bulk source, default to NOT archiving and leave the message in the inbox. Bias toward keeping human mail visible; archive only when confident the message is automated, promotional, or spam. If a candidate batch mixes human and automated senders, split it: drop the ambiguous or human-looking ids before quoting a dry-run total, and archive only the confidently automated or promotional subset.

**Explicit override:** when the operator names a specific human sender by address or name and confirms, in that request, that they want that person's mail archived, proceed on that named id or sender at the operator's instruction, outside the automatic screen above. The screen governs automatic and bulk archive proposals, not an explicit, informed instruction about a named sender.

## Large-inbox pre-flight (required when inbox > ~5,000 messages)

See [`large-inbox-rules.md`](references/large-inbox-rules.md) for the canonical pre-flight checklist.

### `full_inbox_export` is disabled

`full_inbox_export` returns a structured `UNBOUNDED_EXPORT_DISABLED` error and is never the evidence step. For annual cleanups, full audits, or pre-migration snapshots, page bounded `export_emails(scope="entire_mailbox", mailbox=..., max_emails=50, offset=N)` slices instead (advance `offset` by 50 each call) before any irreversible `manage_trash(action="delete_permanent")`. For staged campaigns, keep using bounded `search_emails` + `message_ids=[...]`, `export_emails(scope="filtered", ...)`, or `export_emails(scope="correspondent", email_address=..., include_sent=True, ...)` flows.

## ID-first flow (mandatory for bulk moves, status, and trash)

`move_email`, `update_email_status`, and `manage_trash` **require `message_ids` by default**. Passing `subject_keyword=` or `sender=` to action tools returns `code: TARGET_SELECTOR_DEPRECATED`; collect `message_ids` first. Date-only or explicit bulk paths still require `allow_filter_scan=True`.

For any archive move (`to_mailbox` targeting Archive or similar), the id list in step 2 must already have passed the **Human-Sender Screen** (above) before it reaches step 3; do not build a dry-run from unscreened search results.

On a 24k inbox, filter-based mutations re-pay the scan cost for every batch and often time out. Always:

1. **List or search (bounded)**: `search_emails(sender_exact="...", subject_keyword="...", recent_days=30, limit=50)`, `search_emails(sender_domain="...", recent_days=30, limit=50)`, or `list_inbox_emails(...)`; inspect sample subjects.
2. **Collect `message_id`s**: extract ids from the JSON/text result. For archive moves, drop any id whose sender fails the Human-Sender Screen before it leaves this step.
3. **Simulate**: `move_email(dry_run=True, message_ids=[ids], to_mailbox="...", max_moves=50)` (or `manage_trash(dry_run=True, message_ids=[ids], ...)`).
4. **Execute**: `move_email(dry_run=False, message_ids=[ids], ...)` after the operator confirms counts.

Repeat in batches until stop conditions. Re-run `search_emails(offset=0)` after each archive wave because offsets shift on active inboxes (`exchange-account-patterns.md`).

### `allow_filter_scan=True` (rare escape hatch only)

Use only when the user has explicitly approved a bulk campaign and ID collection is impractical (e.g. migrating an entire sender across years). The tool prefixes responses with a slow-scan warning. Do not use `sender=` or `subject_keyword=` on action tools. Use `search_emails(...)` to collect ids, then act by `message_ids`.

## Standard Campaign Shape

### 1. Frame The Objective And Scope

- Confirm target **account**, mailbox, sender/topic/date window.
- Decide whether backlog is exploratory (`recent_days` wide) or targeted with discovery filters (`sender_exact=`, `sender_domain=`, `subject_keyword=`).

### 2. Establish Evidence

Sequence:

1. `search_emails(..., limit≤50)` preview; escalate only after inspecting sample subjects.
2. Optional analytics: `get_statistics(scope="sender_stats", sender="...")` or `scope="mailbox_breakdown"` for volume proof.
3. When deletion risk looms: `export_emails(...)` snapshots before **`manage_trash`**. For preservation or migration evidence, use reviewed ids (or one bounded page) with `format="eml"` to preserve raw RFC 822 source headers and MIME. Use `include_attachments=True` only when required: each attachment is capped at 25 MiB and each batch at 100 MiB, skipped files are reported, and cold Exchange/Gmail caches may need a small page plus a higher `timeout` than the 120-second default.

For archive campaigns, apply the **Human-Sender Screen** (above) to the preview results now: drop any human-looking or ambiguous sender from the candidate list before it becomes an id to move. Always quote expected totals after dry runs.

### 3. Simulate Mutations (`dry_run=True` on `move_email` / `manage_trash`, `message_ids` required)

Mandatory first pass after collecting ids from step 2. For archive moves, `ids` must already be screened: build it only from senders that passed the Human-Sender Screen, not the raw preview.

```
ids = [e["message_id"] for e in preview["items"]]  # search_emails JSON uses "items"; list_inbox_emails uses "emails"
# For archive moves, drop any id whose sender failed the Human-Sender Screen before this call.
move_email(dry_run=True, message_ids=ids, to_mailbox="Archive", max_moves=50)
```

Trash paths: **`manage_trash(action="move_to_trash", dry_run=True, message_ids=ids, ...)`** before committing (`dry_run` defaults to `True` on `manage_trash`, and to `False` on `move_email` — pass it explicitly on moves).

**`update_email_status` has no `dry_run`.** It is the one action tool with no simulate mode, so there is nothing to preview against: the safe path is the id list itself. Show the user the collected ids (with subjects and senders from the search preview that produced them) and get confirmation *before* the call, keep `max_updates` at or below the batch you just showed, and never pass `allow_filter_scan=True` on a status update the user has not seen enumerated. Read/flag changes are reversible (`mark_unread`, `unflag`), which is why the tool ships without a preview; a wrong batch is still a wrong batch, so confirm first.

Quote expected totals from the dry-run response. Discuss raising `max_moves` / `max_deletes`; defaults protect against catastrophe. If you hit `TARGET_SELECTOR_DEPRECATED`, you used a search selector on an action tool. Go back to search/list and collect ids.

### 4. Execute In Batches (`message_ids` only)

| Action | Typical tool |
|--------|---------------|
| File / archive batch | `move_email(dry_run=False, message_ids=ids, to_mailbox="...", max_moves=50)` |
| Mark processed | `update_email_status(action="mark_read"\|"unflag", message_ids=ids, max_updates≤50 after confirmation)` |
| Remove noise | `manage_trash(action="move_to_trash", dry_run=False, message_ids=ids, max_deletes≤50)` after the dry run; escalate `delete_permanent` only post-export + verbal/written affirmation |
| Hydrate caches | `synchronize_account(account="...", confirm_sync=True)` only after explicit user approval; it can fetch large remote backlogs |

Slice `ids` into batches of ≤50. Re-run narrower `search_emails` between batches until stop conditions.

### 5. Regression Check

Finish with **`get_statistics(scope="account_overview")`** or **`get_inbox_overview()`** verifying queues match operator expectations.

## Safety Reference

| Hazard | Mitigation |
|--------|-------------|
| Archiving a real person's mail | Apply the Human-Sender Screen before every archive dry-run; when uncertain, leave the message in the inbox |
| Over-broad keywords | Narrow by `mailbox=`, combine sender + unread flags |
| Mailbox typos | `list_mailboxes` validation before destructive move |
| Accidental unread wipe | Separate pass for unread-only subsets |
| Cross-account fallout | Iterate per account instead of omnibus `all_accounts=True` mutations |

Permanent deletes invoke irreversible **`manage_trash(action="delete_permanent")`**; escalate only with evidence export + checklist.

## Sibling Routing

| If the user pivots... | Skill |
|------------------------|-------|
| Needs smarter folder ontology | **`mailbox-taxonomy`** |
| Wants sieve text for automation | **`mail-rules-advisor`** |
| Suddenly needs reply drafts | **`email-drafting`** |
