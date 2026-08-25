---
name: email-attachments
description: This skill should be used when the user asks to "list attachments on messages about X", "save this PDF from email", "which invoices have ZIP files", or needs disk-safe attachment extraction. Uses bounded search_emails (has_attachments filters) to collect message_ids first, then list_email_attachments, save_email_attachment, get_email_by_id for confirmation, and optionally export_emails for bundles. Discovery-only; pass subject_keyword to search_emails when ids are unknown; never pass subject_keyword to list_email_attachments or save_email_attachment. Do NOT use when the real goal is writing responses (email-drafting), diagnosing slow accounts (apple-mail-operator), bulk deleting mail (email-archive-cleanup), or designing folder hierarchies (mailbox-taxonomy).
---

# Email Attachments

Attachment-focused traversal with deliberate **filesystem hygiene**. Never save into sensitive system paths; the MCP blocks known dangerous destinations; still confirm user intention.

## When To Use This Skill

| Signal | Skill |
|--------|-------|
| "Save attachment ..." | Here |
| "What files shipped with invoice thread?" | Here |
| "Reply summarizing attachments" | Start here for inventory → **`email-drafting`** |

## Operational Flow

### 1. Narrow The Message Universe

Prefer known `message_id` from upstream search/list.

Otherwise:

```
search_emails(subject_keyword="...", has_attachments=true, recent_days=7, limit=20)
```

Use the subject search above only as a degraded discovery path after confirming no exact id is available. Review the results and collect `message_id` before listing or saving attachments.

For true full-inbox attachment audits (rare), page bounded `search_emails(has_attachments=True, recent_days=..., limit=50, offset=N)` calls instead of unbounded `recent_days`; `full_inbox_export` is disabled (`UNBOUNDED_EXPORT_DISABLED`) and is not an audit path.
Widen timeframe only after checking performance.

### 2. Inspect Attachments Cheaply

Prefer ids from step 1:

```
list_email_attachments(message_ids=[12345, 12346], max_results=10, output_format="json")
```

If ids are unknown, run bounded discovery first, then call by reviewed ids:

```
list_email_attachments(message_ids=[12345], max_results=10)
```

See [`large-inbox-rules.md`](references/large-inbox-rules.md) for the canonical pre-flight.

`list_email_attachments` and `save_email_attachment` require exact `message_ids`; use bounded `search_emails(..., has_attachments=True)` first when ids are unknown. JSON attachment listing returns each row's `message_id`, `attachment_index`, filename, and size. Treat `message_id + attachment_index` as the exact selector for saving. **`attachment_index` is 1-based**: the first attachment is `1`, and `save_email_attachment(attachment_index=0)` is refused with `Error: attachment_index must be a positive 1-based integer`.

If duplicate or similar filenames exist, choose the row from `list_email_attachments(..., output_format="json")` and save with `attachment_index`.

### 3. Persist With Validation

```
save_email_attachment(message_ids=["12345"], attachment_index=2,
                      save_path="/Users/<user>/Documents/Finance/Quarterly.pdf")
```

Rules:

- Path must reside under **`$HOME`** per server validation.
- Prefer `attachment_index` from JSON listing. `attachment_name` is compatible but ambiguous duplicate matches return a structured error.

### 4. Integrity Pass

Echo saved path, approximate size expectation, optionally open file externally (outside MCP).

When batch exports help, optionally layer **`export_emails(message_ids=[...])`** afterward. Use entire-mailbox exports only for mailbox-level evidence trails.

### 5. Aftercare

Recommend virus scanning posture for unsolicited archives; never auto-enable macros/ZIPs.

When attachments are **paper briefs** (R&R specs, reviewer packets), save under `$HOME`, then attach to the operator's research project issue per [`research-project-tracking.md`](references/research-project-tracking.md).

## Pitfalls Table

| Issue | Guidance |
|-------|----------|
| Ambiguous filenames | Prefer exact match substrings surfaced by `list_email_attachments` |
| Password-protected zips | Note inability to introspect payload |
| Extremely large corp attachments | Mention Mail may choke; consider chunked manual download |

## Related Skills

- **`email-drafting`**: cite attachment paths when emailing summaries.
- **`apple-mail-operator`**: if attachment listing times out due to account scope mishaps.
