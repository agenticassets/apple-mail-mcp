# Large-account discovery patterns (canonical)

Canonical source for Exchange- and Gmail-scale mailboxes where subject search, thread tools, and heuristic queues are unreliable. After edits, run `python3 tools/validators/sync_skill_references.py` to refresh per-skill copies.

## Discovery: prefer `search_emails` JSON over list-only snapshots

For **actions** (archive, reply, attachment save), always obtain a numeric `message_id` before calling mutation tools.

| Tool | Good for | Weak for |
|------|----------|----------|
| `search_emails(..., output_format="json")` | Stable `message_id` in `"items"`, offset pagination, sender filters | Broad subject-only queries on some Exchange profiles |
| `list_inbox_emails(..., output_format="json")` | Fast newest-first skim of subjects; `message_id` is always present in text and JSON output, so list rows can be used directly for follow-up actions | No inline body content; fetch with `get_email_by_id` for full content |
| `get_inbox_overview(compact)` | Unread totals and subject preview | No ids for bulk actions |

**Reliable discovery loop on large Exchange accounts:**

1. `search_emails(account=..., recent_days=3..7, limit=5, offset=N, output_format="json", sort="date_desc")`
2. If subject search returns empty but overview/list shows the message, retry with `sender="Display Name"` and raise `limit` (e.g. 5 → 10).
3. `get_email_by_id(account=..., message_id=...)` for body and attachment metadata.

## `get_needs_response` is a weak signal

Treat `get_needs_response` as a **hint**, not a work queue. On noisy inboxes it often ranks newsletters, marketing, and noreply digests alongside human mail.

- Start with `days_back=3`, `max_results=5`, and cross-check each candidate with `get_email_by_id`.
- Do **not** draft from this list without thread verification.
- Prefer the newest bounded `search_emails` or `list_inbox_emails` slice before widening `days_back`.

## `get_email_thread` is best-effort

Thread tools can return **incomplete** results on Exchange:

- Replies missing from the thread view while present in Sent.
- `0` hits for a subject you know exists (a course code, a grant name, etc.).
- Subject-keyword threading diverges from header-based threading.
- Earlier members sitting past the newest messages of a busy mailbox, so the candidate scan stops before the conversation does.

**Mitigation:**

1. Anchor on `message_id` when the schema supports it.
2. Call with `output_format="json"` and read **all four** completeness signals; they are separate and non-overlapping, so `thread_incomplete` alone is not a completeness test. `thread_incomplete` is true only when a bound you did *not* choose cut the thread short: a mailbox's scan filled its slice (`scan_ceiling_hit`, remedy: a larger `scan_messages`), rows were counted but not returned, a candidate read threw, or the anchor itself had to be recovered (`anchor_recovered`). Your own `recent_days` cutoff leaves `thread_incomplete` **false** and sets `window_truncated: true` + `date_floor_hit` instead (remedy: a wider `recent_days`); your own `max_messages` return bound also leaves it false and sets `return_limit_reached: true` (remedy: a larger `max_messages`). `matched == returned` with an empty `errors` does **not** settle it: a scan that stops early loses no reads, so no failure counter moves. Text mode prints `PARTIAL:` lines for the ceiling and the date floor but carries none of these fields.
3. Independently search Sent: `search_emails(mailbox="Sent", sender=<user address>, recent_days=14, subject_keyword=...)`.
4. Check Drafts with `get_email_thread(account=..., mailbox="Drafts", ...)` or `manage_drafts(action="list")`.
5. If thread and Sent disagree, trust **Sent date order** over an empty thread view.

## Offset pagination drifts after archives

`search_emails(offset=N)` is a **snapshot**, not a stable cursor. Archiving or moving messages above the window shifts which messages appear at each offset.

After each archive wave:

- Re-pull `offset=0` for the next newest batch, **or**
- Keep an explicit list of `message_id`s collected before mutating.

## Action types: email reply vs portal vs infra

Classify before drafting:

| Pattern | Typical action |
|---------|----------------|
| DocuSign / publisher royalty / tax forms | **Portal only** (complete in vendor UI); usually no email reply |
| Hosting data-deletion / billing warnings | **Operator decision**; keep visible until resolved |
| Blocked CI/deploy notifications | **Engineering follow-up**; do not auto-archive without subject skim |
| Financial statements (retirement, bank) | **Review**; do not bulk-archive with marketing |
| Co-author tasking with PDF brief | **Research tracker** + read attachment (see `research-project-tracking.md`) |

## Subagent fan-out (recommended at scale)

When triaging many messages:

| Parent agent | Subagents (Mail calls serialize) |
|--------------|----------------------|
| `move_email` / `reply_to_email` (one draft at a time) | Classification, thread checks, CRM/context lookup, attachment inventory |
| Archive batches by exact `message_id` | Research whether thread is already answered in Sent |

Subagents should be **read-only** for mail mutations, and Apple Mail tool calls still serialize. Parallelize only non-Mail work (classifying already-fetched content, CRM/context lookup). Any subagent that calls a Mail tool (thread checks, attachment inventory, Sent lookup) queues behind the single-flight lock, so run those one at a time: concurrent Mail calls give no wall-time benefit and risk timeouts. When subagents disagree (e.g. FYI vs needs reply), the parent reads the primary message and Sent/Drafts before acting.

## Verification gaps

If `verify_draft` / `verify_drafts` are not registered in the client:

- Confirm drafts via bounded `manage_drafts(action="list")` or Drafts thread lookup anchored on `message_id` when available.
- Inspect body above quoted original in Mail before send; Drafts preview snippets may show signature blocks first.

## Draft ids drift on sync (do not cache across turns)

On Exchange and other server accounts, numeric Drafts `draft_id`s are reassigned when the mailbox re-syncs, including between two `manage_drafts(action="list")` calls with **zero writes in between** (observed live: `103` -> `91058` -> `91061`). Do not cache a `draft_id` from an earlier turn and act on it later.

- **Before `verify_draft`, `manage_drafts(action="open"|"send"|"delete")`, or `manage_drafts(action="cleanup_empty")`:** re-resolve the id immediately beforehand with a fresh `manage_drafts(action="list")` or `manage_drafts(action="find")` call in the same turn, not a cached id from a prior call.
- **Durable handle for a reply draft:** `manage_drafts(action="find", in_reply_to=<source Message-ID>)` matches the source Internet Message-ID in the draft's threading headers, so it survives an id reassignment; prefer it over remembering a numeric id.
- `reply_to_email`'s own post-save retry (on a `body_missing`/`body_after_quote` verification mismatch) deletes and re-verifies within the same call, so it is not affected by this; the caution above is for ids handed back across separate tool calls or conversation turns.

## Archive hygiene

1. Collect ids from the triage pass; do not pass `subject_keyword` to `move_email` (`TARGET_SELECTOR_DEPRECATED` on v3.x).
2. `move_email(dry_run=True, message_ids=[...], to_mailbox="Archive")`.
3. Execute after quoting subjects/senders to the operator.
4. Do not archive human university mail, co-author threads with open work, financial/security notices, or messages with unreviewed drafts.
