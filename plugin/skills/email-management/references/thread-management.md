# Thread Management

Apple Mail does not expose true conversation threads to AppleScript. The MCP server reconstructs threads from a known `message_id` by reading dictionary-backed Message-ID, In-Reply-To, and References headers first, then using subject grouping only as a degraded fallback when headers are unavailable.

## Tool

`get_email_thread(message_id="...", account="...", output_format="json")` returns the reconstructed conversation around a known Mail message id with message ids, Internet Message-ID, In-Reply-To, References, mailbox, account metadata, and fallback status. Check `selection_strategy` and `subject_fallback_used` before treating a reconstructed thread as header-confirmed. Check the completeness fields before treating it as the **whole** conversation: it is a separate question from whether the threading was header-confirmed. **Discovery-only:** if no `message_id` yet, pass `subject_keyword` to `search_emails` (or use `list_inbox_emails` for a bounded recent skim), then pass the returned `message_id` to `get_email_thread`.

## Completeness

`get_email_thread` returns two different things that both have to be checked, and they fail independently:

- **Was the threading confirmed?** `selection_strategy` / `subject_fallback_used`. A subject fallback can overmatch on a common subject.
- **Was the whole conversation examined?** Four separate, non-overlapping fields, all of which have to be read — `thread_incomplete` alone is not a completeness test. The per-mailbox candidate scan is bounded independently of `max_messages` (the return limit), so a bounded scan can return a clean, self-consistent subset: `matched == returned`, no `errors`, every other flag false. Nothing was lost in a failed read, so no failure counter moves.

| Field | What it means | Remedy |
|-------|---------------|--------|
| `thread_incomplete: true` | A bound you did **not** choose cut the thread short; the next three rows are its components | Branch on whichever component is set |
| ├ `scan_ceiling_hit: [mailbox, ...]` | A mailbox's candidate slice filled its bound | Raise `scan_messages` (applied bound echoed as `scan_messages_applied`), which is clamped at **400**; past that, narrow `mailboxes` to the folder holding the conversation or report which mailbox was not fully scanned |
| ├ `render_incomplete` / `candidate_scan_incomplete` | Rows counted but not returned, or a candidate read threw | Read `errors` / `error_details`; retry with a larger `timeout` or fewer mailboxes |
| └ an item with `anchor_recovered: true` | The scan missed the anchoring message itself | Treat the result as suspect and re-run wider before acting on it |
| `window_truncated: true` + `date_floor_hit: [mailbox, ...]` | Your own `recent_days` cutoff was the limiting bound. `thread_incomplete` stays **false** | Widen `recent_days`; a larger `scan_messages` will not help |
| `return_limit_reached: true` | The thread hit your own `max_messages` return bound, so more members exist than were returned. `thread_incomplete` stays **false** | Re-run with a larger `max_messages`. `returned == max_messages` is the same boundary whenever the field is absent |

Text mode is not an equivalent view: it prints a `PARTIAL:` line for the `recent_days` date floor that `thread_incomplete` omits, while `attachment_count`, `anchor_recovered`, `matched` / `returned`, and every `*_hit` array are JSON-only, and anchor recovery runs only in the JSON payload builder — text mode never restores a missing anchor and never reports one was missing. Use `output_format="json"` for any completeness check. Per-item `attachment_count` is `null` when Mail could not read the list, which is not `0`; confirm with `list_email_attachments(message_ids=[...], mailboxes=[...])` before reporting an attachment count.

## When To Use

- The user references a conversation that spans multiple replies.
- A single message lacks context and the prior exchange is needed to understand it.
- Before bulk-archiving a long-running discussion, to confirm the full set of related messages.

## Workflows

### Read a conversation in order

```text
results = search_emails(subject_keyword="Q2 planning", mailboxes=["INBOX", "Sent"], limit=5, output_format="json")
get_email_thread(
    account="Work",
    message_id=results["items"][0]["message_id"],
    mailboxes=["INBOX", "Sent"],
    output_format="json",
    include_preview=False,
)
```

The result is already chronological. Read top to bottom for context.

### Archive a resolved thread

1. `search_emails(...)` to identify the target message id, then `get_email_thread(account="Work", message_id="...")` to surface related messages.
2. Check all four completeness fields before collecting ids. Archiving from a truncated thread leaves part of the conversation behind in the inbox, and the move reports full success. Re-run wider on whichever bound fired (a larger `scan_messages` up to its 400 clamp, a wider `recent_days`, a larger `max_messages`); when the ceiling still fires at 400, narrow `mailboxes` to the folder holding the conversation or tell the user which mailbox was not fully scanned.
3. Collect every `message_id` from the thread result (and any stragglers the user confirms).
4. `move_email(dry_run=True, message_ids=[...], to_mailbox="Archive/2026", max_moves=N)`; quote the count; then `move_email(dry_run=False, message_ids=[...], ...)` after confirmation. Do not pass `subject_keyword=` to `move_email` (returns `TARGET_SELECTOR_DEPRECATED`).

### Find the latest message in a long thread

The last entry returned by `get_email_thread()` is the most recent. Prefer replying with `reply_to_email(message_id=...)` when search or list tools already returned the Mail id; pass `message_id`; if no id is known, run search or list first. Default `native_format=True` on replies (see **`email-drafting`** for Accessibility and `REPLY_WINDOW_FOCUS_FAILED`).

**Bulk drafting must use `mode="draft"`, never `mode="open"`.** Every `mode="open"` call leaves a compose window open, and at high counts NSWindowServer runs out of resources. The tools enforce a cap of **5** simultaneous open compose windows and refuse the sixth with `TOO_MANY_OPEN_DRAFTS`. `mode="draft"` saves quietly to Drafts with no window. For human review of a batch, draft them all and then review the Drafts folder (`manage_drafts(action="list")` plus `verify_draft` / `verify_drafts`), or reopen one at a time with `manage_drafts(action="open", draft_id=...)`. Reserve `mode="open"` for a single draft the operator wants to see immediately.

## Cross-Account Threads

`get_email_thread()` honors the same account and mailbox scoping as `search_emails()`. For a thread that spans folders, pass explicit `mailboxes=["INBOX", "Sent", "Archive"]` before considering any whole-account fallback. For a thread that spans personal and work accounts, call the tool once per reviewed account and mailbox list. Whole-account thread scans are slower; use them only when single-account scope is known to be incomplete.

## Limitations

- Header matching depends on Mail exposing useful Message-ID, In-Reply-To, or References values. When those are missing, subject-prefix stripping remains approximate and common subjects can overmatch.
- The candidate scan is bounded per mailbox. `max_messages` bounds what is **returned**, not what is **examined**; `scan_messages` bounds the examination. A conversation whose earlier members sit past a busy mailbox's newest messages needs a larger `scan_messages` (clamped at 400) or a narrower mailbox list, and `thread_incomplete` + `scan_ceiling_hit` are what say so. Hitting the *return* bound is the other axis and reports separately as `return_limit_reached`.
- Use `include_preview=False` for ID collection or archive planning. Turn previews on only when the user needs content context.
