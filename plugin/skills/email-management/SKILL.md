---
name: email-management
description: This skill should be used when the user asks to "help me get to inbox zero", "build a repeatable triage program", "coordinate a multi-week cleanup across mailboxes", "set up sustained email habits", or "I'm drowning in email" and needs a multi-week umbrella program coordinating triage, taxonomy, cleanup, drafting, and analytics, not a single pass. Uses get_inbox_overview, search_emails, move_email, update_email_status, manage_trash, and get_statistics; routes single-purpose work to the narrow sibling skill instead. Do NOT use for tooling-only onboarding (see apple-mail-operator), a single 5–10 minute read-first scan (inbox-triage), a one-shot bulk move/archive/delete campaign (email-archive-cleanup), folder-architecture redesign without execution (mailbox-taxonomy), Mail filter prose only (mail-rules-advisor), or drafting voice capture (email-style-profile before email-drafting).
---

# Email Management

Sustained inbox organization for Apple Mail: repeatable processing habits plus Inbox Zero programs that combine reading, queues, guarded moves/trash, and analytics. Prefer narrow sibling skills (`mailbox-taxonomy`, `email-archive-cleanup`, `mail-rules-advisor`, `email-drafting`, `apple-mail-operator`) when the user intent is clearly one-shot or specialized; use this umbrella when they want coordinated multi-week cleanup or habitual discipline.

## Recent-first processing (required for reply queues)

See [`recent-first-triage.md`](references/recent-first-triage.md). Even in multi-week programs, **daily action** starts from the newest human mail in small batches (3 to 5). Widen `recent_days` or pull older threads only after the current recent window is cleared or the user names a specific older item.

## Large-inbox pre-flight (required when inbox > ~5,000 messages)

See [`large-inbox-rules.md`](references/large-inbox-rules.md) for the canonical pre-flight checklist. On large Exchange profiles, also [`exchange-account-patterns.md`](references/exchange-account-patterns.md).

### `full_inbox_export` is disabled

`full_inbox_export` returns a structured `UNBOUNDED_EXPORT_DISABLED` error and runs no AppleScript. For the rare full-inbox case (annual cleanup, complete audit, compliance archive, or pre-migration snapshot), page bounded `export_emails(scope="entire_mailbox", mailbox=..., max_emails=50, offset=N)` slices instead. For everything else, pass a bounded `recent_days` / `max_emails` and let the structured `UNBOUNDED_SCAN_REQUIRED` error guide a narrower query.

## Before drafting

Umbrella programs may end in replies. Load **`email-drafting`** and follow [`pre-draft-verification.md`](references/pre-draft-verification.md) before any `reply_to_email` call.

Paper/R&R mail with author briefs: after triage, follow [`research-project-tracking.md`](references/research-project-tracking.md) so empirical work lands in the Research issue tracker, not only in mail drafts.

## When To Use This Skill

Use when the request is about reducing inbox volume through **habitual** processing, combining analytics with guarded moves/trash, or coaching an Inbox Zero cadence that may span multiple skill handoffs.

Do NOT use for:

- Composing or replying to a specific message; route to **`email-drafting`** (`reply_to_email(message_id=...)` for thread replies).
- A brief read-first scan; see **`inbox-triage`**.
- Saving attachments; see **`email-attachments`**.
- Pure Mail MCP setup / timeouts; see **`apple-mail-operator`**.
- Designing folder ontology without agreeing execution path; **`mailbox-taxonomy`** (then **`email-archive-cleanup`** once moves ship).

For finding a single specific email, call `search_emails()` directly without invoking this skill.

## Performance Defaults To Know

Internalize these before constructing any tool call. The defaults exist to keep AppleScript queries fast on large Exchange inboxes.

- `search_emails` defaults to the last 48 hours on the configured default account and scans at most 50 messages per call regardless of `limit` or window size. Pass `recent_days=7` or `recent_days=30` to widen. `recent_days=0` is refused with `code: UNBOUNDED_SCAN_REQUIRED`; for more than one call's worth, page with `offset` rather than reaching for `full_inbox_export`, which is disabled (`UNBOUNDED_EXPORT_DISABLED`).
- `list_inbox_emails` defaults to the 50 most-recent emails, and 50 is also the hard per-call ceiling regardless of `max_emails`. For **triage and drafting**, pass `max_emails=5` (up to 8) per batch; see [`recent-first-triage.md`](references/recent-first-triage.md). `max_emails=0` is refused with `code: UNBOUNDED_SCAN_REQUIRED`; `full_inbox_export` is disabled, so use a bounded `max_emails` and page with repeated calls for the rare full walk.
- Cross-account scans cost time on large Exchange inboxes. Pass `all_accounts=True` only when truly needed; otherwise let the `DEFAULT_MAIL_ACCOUNT` environment variable keep things scoped.

When in doubt, run a narrow query first and widen only if results are insufficient.

## Decision Tree

| Request signal | Route to |
|----------------|----------|
| "Help me get to inbox zero" / "daily habits" | This skill |
| "How does this MCP work?" / timeouts | `apple-mail-operator` |
| "What came in today / needs reply NOW" | `inbox-triage` |
| "Design folder layout / taxonomy brainstorm" | `mailbox-taxonomy` |
| Staged archival / bulk deletes with dry runs | `email-archive-cleanup` |
| Newsletter noise; propose Mail rules prose | `mail-rules-advisor` |
| Compose / drafts | `email-drafting` (+ `email-style-profile` beforehand) |
| Attachments extraction | `email-attachments` |
| Single lookup | Prefer `apple-mail-operator` cheat sheet vs loading this umbrella |

**Templates are examples, not the skill contract.** Copy-paste workflows under `templates/` and `examples/` must follow `templates/search-patterns.md` (discovery → ids → action). When in doubt, route to the narrow sibling skill above.

## Destructive Operations: Safety Caps

The MCP server enforces conservative defaults. Confirm with the user before raising any cap.

| Operation | Default cap | When to confirm with user |
|-----------|-------------|---------------------------|
| `manage_trash(action="move_to_trash")` | 5 messages | Any time `max_deletes` exceeds 20 |
| `manage_trash(action="delete_permanent")` | 5 messages | Always; this is irreversible |
| `manage_trash(action="empty_trash")` | hard confirm via `confirm_empty=True`, and `dry_run=False` to act | Always |
| `move_email` | 50 messages | Any bulk move (`max_moves` > 10); use explicit `max_moves=1` for single-message filing |
| `update_email_status` | 10 messages | Any bulk update (`max_updates` > 10). This tool has **no `dry_run`**, so show the collected ids and get confirmation before the call |

Pattern: identify candidates with `search_emails()`, preview the count and sample, confirm the user's intent, then run the destructive call with an explicit cap.

Every `manage_trash` action defaults to `dry_run=True` and previews without touching mail, `empty_trash` included. Read the preview back to the user, then repeat the same call with `dry_run=False` to act. A preview that reports what would go is not proof that anything was deleted.

## Core Principles

- Start every workflow with `get_inbox_overview()` to understand current state before acting.
- Prefer batch operations with explicit caps over message-by-message changes.
- Treat the inbox as a processing queue, not as storage; archive or delete once a decision is made.
- Search beats sort for most retrieval needs; keep folder structure shallow (two to three levels max).
- Confirm destructive actions before executing, and prefer reversible operations (move to trash) over permanent ones.
- Respect the configured default account; only widen to all accounts when single-account scope is demonstrably incomplete.
- Cite expected counts to the user before any bulk action so they can intervene if a query has matched more than intended.

## Workflow: Daily Inbox Triage

Goal: process inbox to zero or near-zero in 15 to 30 minutes. For a **5–10 minute scan** only, use the **`inbox-triage`** skill instead.

1. Get overview: `get_inbox_overview()` to see unread counts, recent messages, and suggested actions.
2. Surface priorities: `get_needs_response(days_back=2, max_results=20, output_format="json")` for likely replies; defaults already exclude rows with `was_replied_to=true` or `has_draft=true` and report `skipped_replied_count` / `skipped_drafted_count`. Use each row's numeric `message_id` for downstream reads, replies, moves, and status updates; keep `internet_message_id` only for replied-header correlation. Optionally use `get_awaiting_reply(days_back=7)` for follow-ups you sent. Use keyword `search_emails` only when the user names a topic.
3. Drill down: after list/search returns a `message_id`, use `get_email_by_id(account="...", message_id=...)` for full content; do not re-search by subject.
4. Decide per message using the four-option rule: respond, defer, file, or delete.
   - For responses, defer to **`email-drafting`** → `reply_to_email(message_id=...)` for thread replies; `compose_email` only for new standalone mail.
   - To defer, flag with `update_email_status(action="flag", message_ids=["..."])`.
   - To file, use `move_email(message_ids=["..."], to_mailbox="...", dry_run=True)` then execute.
   - To delete, preview with `manage_trash(action="move_to_trash", message_ids=["..."])` and an explicit cap, then repeat the same call with `dry_run=False` to act.
5. Mark processed batches read: `update_email_status(action="mark_read", message_ids=[...])`.
6. End the session by re-running `get_inbox_overview()` to confirm the queue is drained.

Tips:

- Process by sender or topic, not strictly chronologically.
- Apply the 2-minute rule: if a reply is short, do it now rather than deferring.
- Do not organize what can be found later by search.

## Workflow: Weekly Email Organization

Goal: keep folder structure healthy and archive aging messages.

1. Review structure: `list_mailboxes(include_counts=True)`.
2. Identify clutter: mailboxes with more than 1,000 messages or with a high unread ratio.
3. Analyze patterns: `get_statistics(scope="account_overview")` plus `get_top_senders()`. For per-folder volume, prefer `list_mailboxes(include_counts=True)`; when calling `get_statistics(scope="mailbox_breakdown")`, pass explicit `mailbox=`; omitting it scopes to the default Inbox in code. Full guidance lives in `references/analytics.md`.
4. Adjust folders: collaborate with **`mailbox-taxonomy`** for naming; create net-new folders with `create_mailbox` after explicit confirmation (rename/delete heavy work still occurs in Mail UI when needed).
5. Bulk-organize by sender or date (ID-first; see **`email-archive-cleanup`**):
   - Prefer `search_emails(sender_exact="person@example.com", recent_days=30)` or `search_emails(sender_domain="example.com", recent_days=30)` when the exact sender/domain is known; use fuzzy `sender="..."` only for discovery when the address is uncertain.
   - `search_emails(sender_exact="...", recent_days=30)` → collect `message_id`s → `move_email(message_ids=[...], to_mailbox="...", dry_run=True)` → execute.
   - Action tools do not target by `sender=`; collect ids with `search_emails(sender_exact="...", ...)` or `search_emails(sender_domain="...", ...)`, then call `move_email(message_ids=[...])`.
6. Archive read mail older than 30 days into `Archive/<year>`.

**Human-Sender Screen applies to every archive move in this workflow.** Before any id from steps 5 or 6 reaches `move_email(dry_run=True, ...)`, apply the Human-Sender Screen in `email-archive-cleanup`'s `SKILL.md` (section "Human-Sender Screen"); drop human-looking or ambiguous senders from the candidate list and archive only the confidently automated or promotional subset. This umbrella skill does not carry its own copy of the screen; all archive execution defers to `email-archive-cleanup` and inherits its screen, so the safety guarantee holds no matter which skill the user entered through.

Detailed safe bulk operations are documented in `references/bulk-cleanup.md`.

## Workflow: Achieving Inbox Zero

Goal: drain the inbox by processing every message exactly once.

1. Survey: `get_inbox_overview()` and `get_statistics(scope="account_overview")` to size the problem.
2. Process top-down with the five-D framework on each message:
   - Delete: spam, expired notifications; preview with `manage_trash(action="move_to_trash", message_ids=[...])`, then repeat the same call with `dry_run=False` to act.
   - Delegate: forward; use **`email-drafting`** (`forward_email` tool) after user confirms recipients.
   - Defer: flag and move to a "Follow Up" mailbox.
   - Do: respond now if under two minutes; use **`email-drafting`** → `reply_to_email(message_id=...)` for thread replies; `compose_email` only for new standalone mail. Never auto-send under `--draft-safe`. `compose_email`, `create_rich_email_draft`, and `manage_drafts(action="create")` are standalone-only and refuse `Re:`/`Fwd:` subjects or quoted bodies unless `standalone_confirmed=True`.
   - File: `move_email(message_ids=[...], to_mailbox="...")` for reference material.
3. Keep folders sparing: an "Action Required", "Waiting For", and "Reference" trio handles most cases.
4. Maintain daily; Inbox Zero is a habit, not a one-time event.

Mindset:

- Every message needs a decision.
- Touch each message once when possible.
- The inbox is a queue, not an archive.

## Tool Selection Guidelines

| Goal | Tool | Notes |
|------|------|-------|
| Inbox snapshot | `get_inbox_overview()` | Always the first call |
| Daily 5-min scan | `inbox-triage` skill | Uses needs-response + list, not full cleanup |
| Likely need reply | `get_needs_response(days_back=2, output_format="json")` | Fast subject-only by default; excludes already-replied/already-drafted rows unless `include_already_replied=True` / `include_drafted=True`; JSON `message_id` is the numeric Mail id for actions |
| Follow-ups you sent | `get_awaiting_reply(days_back=7)` | Optional daily check |
| Full dashboard | `inbox_dashboard()` | Heavier, richer view |
| Find a specific email | `search_emails(subject_keyword="...")` | Defaults to last 48 hours |
| Read one message by id | `get_email_by_id(account="...", message_id="...")` | After search/list returns an id |
| Search by sender | `search_emails(sender_exact="person@example.com")` or `search_emails(sender_domain="example.com")` | Use fuzzy `sender="..."` only when the address is uncertain |
| Search email bodies | `search_emails(body_text="...", allow_body_scan=True)` | Slower; requires explicit opt-in |
| Cross-account search | `search_emails(account=None, all_accounts=True)` | Costly on Exchange; use sparingly |
| Recent inbox listing | `list_inbox_emails(max_emails=50, read_status="unread", include_content=False)` | Default cap is 50; `read_status="unread"` is the cheapest pass on a large inbox. Legacy `include_read=False` still works but deprecated. |
| View a conversation | `get_email_thread(account="...", message_id="...")` | **Discovery-only:** if no id yet, run bounded `search_emails` or `list_inbox_emails` first, then pass returned `message_id` |
| Move messages | `move_email(message_ids=[...], max_moves=N)` | ID-first; filter scans need `allow_filter_scan=True` |
| Flag / mark read | `update_email_status(action="...", message_ids=[...])` | ID-first; default cap 10 |
| Move to trash / delete | `manage_trash(action="...", message_ids=[...])` | Previews by default; repeat the same call with `dry_run=False` to act. See `references/bulk-cleanup.md` |
| Analytics | `get_statistics()` and `get_top_senders()` | See `references/analytics.md` |
| Export for backup | `export_emails(message_ids=[...])` or `export_emails(scope="...", mailbox="...")` | Prefer reviewed ids for batches; run before any large delete. Use `format="eml"` when raw source headers and MIME are needed; see `references/bulk-cleanup.md` for attachment and timeout limits. |
| Sync stale account | `synchronize_account(account="...", confirm_sync=True)` | Only after the user explicitly accepts that Mail may fetch a large backlog |

## Common Scenarios

### "I'm overwhelmed by my inbox"

1. Size the problem: `get_inbox_overview()` and `get_statistics(scope="account_overview")`.
2. Identify the worst senders: `get_top_senders(top_n=10)`.
3. Adopt the Daily Triage workflow above for 15 to 30 minutes per day.
4. Unsubscribe from non-essential senders identified in step 2.
5. Build the minimum folder structure ("Action Required", "Waiting For", "Reference", "Archive").
6. Aim for sustainable progress; do not attempt a one-shot cleanup of a 10,000-message backlog.

### "I can't find an important email"

1. Start with `search_emails(subject_keyword="...")` on the default account and default 48-hour window.
2. Widen the time window: add `recent_days=30`. If the tool returns `code: UNBOUNDED_SCAN_REQUIRED`, follow the `remediation.fallback_tool` field. Usually a wider `recent_days` covers it; for older history, page with `offset` across bounded calls rather than reaching for `full_inbox_export`, which is disabled.
3. Widen the scope: add `all_accounts=True` to search every configured account.
4. Search the body: `search_emails(body_text="...", allow_body_scan=True, recent_days=30)` before asking to run a full scan.
5. Filter by attachment if relevant: `search_emails(has_attachments=True, ...)`.
6. Check Trash explicitly: `search_emails(mailbox="Trash", recent_days=30, ...)`. For a true full Trash walk, page bounded `search_emails(mailbox="Trash", limit=50, offset=N)` calls; `full_inbox_export` is disabled and never the tool for this.

### "I want to organize emails by project"

1. Review current layout: `list_mailboxes(include_counts=True)`.
2. Create project folders in Apple Mail (or via `create_mailbox` if the user confirms).
3. Find project messages: `search_emails(subject_keyword="ProjectName", recent_days=30)`, widening only after review.
4. Bulk move: collect ids from step 3, then `move_email(message_ids=[...], to_mailbox="Projects/ProjectName", dry_run=True)` then execute.
5. Add sender-based moves for team members on the same project.

### "I need to follow up on emails"

1. Flag the message: `update_email_status(action="flag", message_ids=["..."], max_updates=1)`.
2. Optionally move flagged items into a dedicated "Follow Up" mailbox for visibility.
3. Schedule a recurring weekly review of the flagged set with a bounded date window; do not use full scans in recurring workflows.
4. Clear the flag once handled: `update_email_status(action="unflag", message_ids=["..."])`.

### "Too many emails from one sender"

1. Confirm volume: `get_statistics(scope="sender_stats", sender="...")`.
2. Find the messages: `search_emails(sender_exact="...", recent_days=30)` or `search_emails(sender_domain="...", recent_days=30)`. If the user wants every message from this sender across all time, page with `offset` across bounded calls rather than ratcheting `recent_days` indefinitely; `full_inbox_export` is disabled and not an option.
3. If unwanted, run the cleanup sequence from `references/bulk-cleanup.md`.
4. If wanted but noisy, create a dedicated folder and bulk-move with `message_ids` from step 2 (`email-archive-cleanup` workflow).
5. If the sender is a newsletter, surface it via `get_top_senders()` and unsubscribe in Apple Mail.

## Additional Resources

### Reference Files

- `references/analytics.md`: Email analytics, statistics scopes, and using `get_top_senders` for noise diagnosis.
- `references/bulk-cleanup.md`: Safe bulk cleanup operations with confirmation patterns.
- `references/thread-management.md`: Working with reconstructed email threads.

### Examples

The `examples/` directory contains worked walkthroughs:

- `examples/email-triage.md`
- `examples/folder-organization.md`
- `examples/inbox-zero-workflow.md`

### Templates

The `templates/` directory holds reusable query and workflow templates referenced by the examples.
