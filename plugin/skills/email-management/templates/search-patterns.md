# ID-First Email Search Patterns

Use search as discovery, not action authorization. Search tools return candidate handles. Action tools operate on exact handles such as `message_id`, `draft_id`, or a future exact attachment selector.

## Core Workflow

1. Run a bounded discovery query.
2. Review the returned subjects, senders, accounts, mailboxes, dates, and ids.
3. Collect exact `message_id` values from the reviewed candidate set.
4. Call action tools with `message_ids=[...]` and a clear cap.
5. Use `dry_run=True` before moves, status updates, trash operations, or large attachment saves.

```python
results = search_emails(
    account="Work",
    mailbox="INBOX",
    subject_keyword="Project Alpha",
    recent_days=7,
    limit=10,
    output_format="json",
)

ids = [item["message_id"] for item in results["items"]]  # search_emails JSON key is "items"
# list_inbox_emails uses results["emails"] instead
# Review subjects, senders, dates, mailboxes, and ids before acting.
move_email(dry_run=True, message_ids=ids, to_mailbox="Projects/Alpha", max_moves=len(ids))
move_email(dry_run=False, message_ids=ids, to_mailbox="Projects/Alpha", max_moves=len(ids))
```

Do not pass subject, sender, or draft-subject selectors to action tools. Those legacy selectors return `TARGET_SELECTOR_DEPRECATED` on action surfaces.

## Discovery Tools

Use `list_inbox_emails` for a fast recent inbox skim. Use `search_emails` for bounded discovery across date, subject, sender, read status, attachments, and explicit mailbox sets. Use `get_email_by_id` when an exact Mail id is already known.

```python
recent = list_inbox_emails(
    account="Work",
    max_emails=20,
    include_read=True,
    include_content=False,
    output_format="json",
)

matches = search_emails(
    account="Work",
    mailbox="INBOX",
    recent_days=7,
    limit=20,
    output_format="json",
)
```

`search_emails` defaults to a recent bounded window. If you need a larger search, widen deliberately with `recent_days=7`, `recent_days=30`, or an explicit `date_from`. `recent_days=0` without `date_from` is refused with `UNBOUNDED_SCAN_REQUIRED`.

## Subject Discovery

Subject keywords are candidate discovery only. They are useful for finding likely ids, then the follow-up action must use ids.

```python
results = search_emails(
    account="Work",
    mailbox="INBOX",
    subject_keyword="board packet",
    recent_days=14,
    limit=10,
    output_format="json",
)

ids = [item["message_id"] for item in results["items"]]
```

Use narrower terms when broad terms return too many candidates. Prefer adding `mailbox`, `recent_days`, `read_status`, `has_attachments`, or sender filters instead of expanding to every folder.

## Sender Discovery

Prefer exact sender or domain filters when known.

```python
from_person = search_emails(
    account="Work",
    mailbox="INBOX",
    sender_exact="person@example.com",
    recent_days=30,
    limit=25,
    output_format="json",
)

from_domain = search_emails(
    account="Work",
    mailboxes=["INBOX", "Archive", "Sent"],
    sender_domain="example.com",
    recent_days=30,
    limit=50,
    output_format="json",
)
```

Use fuzzy `sender="..."` only when the exact address or domain is unknown, and keep it bounded with account, mailbox, date window, and limit.

## Exact Message-ID Discovery

If another tool, a saved note, or a prior thread result gives you an Internet Message-ID, use it as an exact discovery filter. Angle brackets are optional.

```python
by_header = search_emails(
    account="Work",
    mailboxes=["INBOX", "Sent", "Archive"],
    internet_message_id="<reply@example.com>",
    output_format="json",
    limit=5,
)
```

Use the returned numeric `message_id` for follow-up actions.

## Explicit Mailbox Sets

Prefer a short explicit mailbox list over the whole-account search path.

```python
results = search_emails(
    account="Work",
    mailboxes=["INBOX", "Sent", "Archive"],
    subject_keyword="closing checklist",
    recent_days=30,
    limit=25,
    output_format="json",
)
```

Whole-account mailbox search is a capped fallback for cases where the likely folder is unknown. Use it only after narrower mailbox choices fail, and expect warnings about incomplete results on accounts with many labels or folders.

## Date And Status Discovery

```python
unread = search_emails(
    account="Work",
    mailbox="INBOX",
    read_status="unread",
    recent_days=7,
    limit=50,
    output_format="json",
)

range_results = search_emails(
    account="Work",
    mailboxes=["INBOX", "Archive"],
    date_from="2026-01-01",
    date_to="2026-01-31",
    limit=50,
    output_format="json",
)
```

**Always pass `date_from` with `date_to`.** `date_to` alone does not open the window backwards: when `date_from` is omitted, `search_emails` fills it from `recent_days` (default 2.0, so "two days ago"), and a `date_to` in the past then ends the window before it starts. The call succeeds and returns an empty result that looks like "no matches".

Date-only and status-only queries can still produce many candidates. Every `search_emails` call scans at most 50 messages regardless of `limit`; page with `offset` for a wider date range. Review result counts and ids before any mutation.

## Attachment Discovery

Find candidate messages with `has_attachments=True`, then list or save attachments by exact `message_ids`.

```python
candidates = search_emails(
    account="Work",
    mailbox="INBOX",
    has_attachments=True,
    sender_domain="example.com",
    recent_days=30,
    limit=20,
    output_format="json",
)

ids = [item["message_id"] for item in candidates["items"]]
list_email_attachments(message_ids=ids, max_results=20)
```

`save_email_attachment` should be called after the candidate message ids are known. Prefer `attachment_index` from `list_email_attachments(output_format="json")`; exact attachment names remain compatible, but duplicate filename matches require retrying with the exact index.

## Threads And Replies

When a search or list result contains a `message_id`, use that id for the thread and reply flow.

```python
thread = get_email_thread(
    account="Work",
    message_id="12345",
    mailboxes=["INBOX", "Sent"],
    max_messages=20,
    output_format="json",
    include_preview=False,
)

reply_to_email(
    account="Work",
    message_id="12345",
    reply_body="Thanks, I will review and follow up.",
    mode="draft",
)
# Default native_format=True (rich quote + logo signature); load email-drafting for
# Accessibility / REPLY_WINDOW_FOCUS_FAILED recovery: retry with Mail visible. Do not
# use native_format=False (gated: WINDOWLESS_FALLBACK_DISABLED).
```

Do not reply or forward by subject. If no id is known, run `search_emails` or `list_inbox_emails` first.

## Body Search

Body search is slow because it reads message contents. It requires explicit opt-in.

```python
body_matches = search_emails(
    account="Work",
    mailbox="INBOX",
    body_text="specific phrase",
    allow_body_scan=True,
    recent_days=7,
    limit=10,
    output_format="json",
)
```

Pair body search with a tight account, mailbox, date window, and limit. Use it for discovery only.

## Safe Action Patterns

### Move Reviewed Messages

```python
ids = ["101", "202"]
move_email(dry_run=True, message_ids=ids, to_mailbox="Archive/Reviewed", max_moves=len(ids))
move_email(dry_run=False, message_ids=ids, to_mailbox="Archive/Reviewed", max_moves=len(ids))
```

### Mark Reviewed Messages

```python
update_email_status(action="flag", message_ids=["101", "202"], max_updates=2)
update_email_status(action="mark_read", message_ids=["101", "202"], max_updates=2)
```

### Move Reviewed Messages To Trash

```python
manage_trash(action="move_to_trash", dry_run=True, message_ids=["101"], max_deletes=1)
manage_trash(action="move_to_trash", dry_run=False, message_ids=["101"], max_deletes=1)
```

Permanent delete and empty-trash operations need explicit confirmation and, for valuable mailboxes, an export first.

## Slow Or Broad Fallbacks

`full_inbox_export` is disabled (`UNBOUNDED_EXPORT_DISABLED`) and runs no AppleScript; it is not a bypass for bounded search rules. For audited full-mailbox work, page bounded calls instead: repeat `search_emails(..., limit=50, offset=N)` advancing `offset` by 50 each call, or take an export in bounded `export_emails` slices (`max_emails` is capped at 50 per call across every scope).

```python
page = search_emails(
    account="Work",
    mailbox="INBOX",
    recent_days=90,
    limit=50,
    offset=0,
    output_format="json",
)
# advance offset by len(page["items"]) (or 50) for the next page
```

Treat exports and paged scans as evidence-gathering steps. They do not authorize mutations by themselves.

## Anti-Patterns

Do not call action tools with subject, sender, or draft-subject selectors.
Do not make the whole-account mailbox scan the default discovery shape.

Use this instead:

```python
results = search_emails(
    account="Work",
    mailboxes=["INBOX", "Archive"],
    subject_keyword="Project Alpha",
    recent_days=30,
    limit=25,
    output_format="json",
)
ids = [item["message_id"] for item in results["items"]]
move_email(dry_run=True, message_ids=ids, to_mailbox="Archive", max_moves=len(ids))
```
