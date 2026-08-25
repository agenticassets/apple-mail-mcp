# Email Analytics & Insights

Use analytics tools to understand email patterns before reorganizing or cleaning up. Insight first, action second.

## Tools

- `get_statistics(scope="account_overview")`: totals, unread/read counts, flagged count, sample senders, mailbox distribution. `flagged`, `top_senders`, `with_attachments`, and `mailbox_distribution` are sample-based (bounded by `days_back`), not mailbox-wide.
- `get_statistics(scope="sender_stats", sender="name@example.com")`: message count and unread count from a specific sender, plus attachment volume.
- `get_statistics(scope="mailbox_breakdown", mailbox="FolderName")`: per-mailbox totals, unread count, and derived read count.

**Unread is not measured in `account_overview` or `mailbox_breakdown`.** Both report Mail's cached `unread count` aggregate, which drifts low (measured on a 25,012-message Exchange Inbox: 3,236 reported against 10,016 actual, a 68% under-report). The payload labels it `unread_count_source="mail_cached_aggregate"` / `unread_count_measured=false`, and `read` is `total - unread`, so it inherits the same error with the sign flipped (`read_count_measured=false`). Read those labels before quoting either number, and never present them as exact. `count of messages` totals are reliable. Only `sender_stats` counts per-message read status and reports `unread_count_measured=true`.
- `get_top_senders(account="...", top_n=20)`: surface the heaviest senders ranked by volume. Use this to identify newsletter overload, noisy systems, or recurring threads worth bulk-archiving.

## Workflows

### Understand overall load

Run `get_statistics(scope="account_overview", days_back=2)` once per account for a quick load sample. Increase the window only when the user explicitly wants a heavier analysis pass. Look at the sender sample, and read the unread/read counts as a rough directional signal only (they come from the cached aggregate above). A read share that looks well below half usually means inbound volume exceeds processing capacity; fix that with filters and unsubscribes before tweaking folders. Do not build a threshold rule on the exact ratio, and use `sender_stats` when a per-sender unread proportion has to be right.

### Diagnose a noisy sender

Run `get_top_senders()` first to find the worst offenders. For each candidate, call `get_statistics(scope="sender_stats", sender="...")` to confirm volume and unread proportion. High volume plus high unread ratio is a strong signal to unsubscribe (newsletters) or create a dedicated folder (active project threads).

### Identify newsletters

There is no dedicated newsletter detector. Use `get_top_senders()` to rank by volume, then evaluate each sender by name; list addresses, automated systems, and `no-reply@` patterns are almost always newsletters. Unsubscribe in the Mail app or set up a rule.

### Find folders that need cleanup

Start with `list_mailboxes(include_counts=False)` to map structure. If the user approves a slower count pass, request counts for the short candidate set, then call `get_statistics(scope="mailbox_breakdown", mailbox="...")` on the top three by message count. Mailboxes with thousands of messages and a high read ratio are good archive candidates.

For a true full-inbox analytics pass, see [`large-inbox-rules.md`](large-inbox-rules.md); `get_statistics(scope="account_overview", days_back=0)` returns `UNBOUNDED_SCAN_REQUIRED`. `get_statistics` per-mailbox reads are hard-capped at 50 messages for both short and long windows, and `full_inbox_export` is disabled (`UNBOUNDED_EXPORT_DISABLED`); for annual/compliance work, widen `days_back` per mailbox and repeat across mailboxes instead.

## Actionable Signals

| Pattern | Suggested action |
|---------|------------------|
| One sender accounts for more than 10% of inbox | Create a dedicated folder or unsubscribe |
| Many unread messages in Archive | Archive is being used as a triage queue; run bulk cleanup |
| Flagged count growing week over week | Schedule a follow-up review block |
| Mailbox over 5,000 messages | Export and prune (see `bulk-cleanup.md`) |
