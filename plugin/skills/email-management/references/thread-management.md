# Thread Management

Apple Mail does not expose true conversation threads to AppleScript. The MCP server reconstructs threads from a known `message_id` by reading dictionary-backed Message-ID, In-Reply-To, and References headers first, then using subject grouping only as a degraded fallback when headers are unavailable.

## Tool

`get_email_thread(message_id="...", account="...", output_format="json")` returns the reconstructed conversation around a known Mail message id with message ids, Internet Message-ID, In-Reply-To, References, mailbox, account metadata, and fallback status. Check `selection_strategy` and `subject_fallback_used` before treating a reconstructed thread as header-confirmed. **Discovery-only:** if no `message_id` yet, pass `subject_keyword` to `search_emails` (or use `list_inbox_emails` for a bounded recent skim), then pass the returned `message_id` to `get_email_thread`.

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
2. Collect every `message_id` from the thread result (and any stragglers the user confirms).
3. `move_email(dry_run=True, message_ids=[...], to_mailbox="Archive/2026", max_moves=N)`; quote the count; then `move_email(dry_run=False, message_ids=[...], ...)` after confirmation. Do not pass `subject_keyword=` to `move_email` (returns `TARGET_SELECTOR_DEPRECATED`).

### Find the latest message in a long thread

The last entry returned by `get_email_thread()` is the most recent. Prefer replying with `reply_to_email(message_id=...)` when search or list tools already returned the Mail id; pass `message_id`; if no id is known, run search or list first. Default `native_format=True` on replies (see **`email-drafting`** for Accessibility and `REPLY_WINDOW_FOCUS_FAILED`).

**Bulk drafting must use `mode="draft"`, never `mode="open"`.** Every `mode="open"` call leaves a compose window open, and at high counts NSWindowServer runs out of resources. The tools enforce a cap of **5** simultaneous open compose windows and refuse the sixth with `TOO_MANY_OPEN_DRAFTS`. `mode="draft"` saves quietly to Drafts with no window. For human review of a batch, draft them all and then review the Drafts folder (`manage_drafts(action="list")` plus `verify_draft` / `verify_drafts`), or reopen one at a time with `manage_drafts(action="open", draft_id=...)`. Reserve `mode="open"` for a single draft the operator wants to see immediately.

## Cross-Account Threads

`get_email_thread()` honors the same account and mailbox scoping as `search_emails()`. For a thread that spans folders, pass explicit `mailboxes=["INBOX", "Sent", "Archive"]` before considering any whole-account fallback. For a thread that spans personal and work accounts, call the tool once per reviewed account and mailbox list. Whole-account thread scans are slower; use them only when single-account scope is known to be incomplete.

## Limitations

- Header matching depends on Mail exposing useful Message-ID, In-Reply-To, or References values. When those are missing, subject-prefix stripping remains approximate and common subjects can overmatch.
- Use `include_preview=False` for ID collection or archive planning. Turn previews on only when the user needs content context.
