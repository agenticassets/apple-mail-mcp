# Common Email Workflows - Quick Reference

This document provides ready-to-use workflow templates for common email management tasks. Copy and adapt these patterns to your specific needs.

**`move_email` executes on the spot.** Unlike `manage_trash` and `manage_drafts`, its `dry_run` defaults to **`False`**, so any `move_email(...)` snippet below that omits `dry_run` moves mail the moment it runs. Before every bulk move: run the same call with `dry_run=True`, show the user the count and the sample subjects, and move only after they confirm. Archive moves must also pass the Human-Sender Screen in `email-archive-cleanup`'s `SKILL.md` first.

**Action-tool contract:** Mutation examples follow `search-patterns.md`. Use `sender_exact`, `sender_domain`, and `subject_keyword` on **`search_emails`** only. Use `list_inbox_emails` for bounded recent listing (`exclude_replied=True` and `exclude_drafted=True` together when feeding reply candidates, so already-answered and already-drafted rows are both filtered). Pass returned `message_id` / `message_ids` to `reply_to_email`, `move_email`, `manage_trash`, and related action tools. For daily triage, prefer the **`inbox-triage`** skill over keyword-only sweeps below.

## Quick Triage Workflows

### Morning Inbox Check (10 minutes)

Prefer the **`inbox-triage`** skill for the full daily loop. Minimal pattern:

```
# 1. Needs-response queue (read-first triage); defaults already exclude
#    already-replied/already-drafted rows
get_needs_response(account="Work", max_results=20)

# 2. VIP unread (discovery on search_emails only)
vip_matches = search_emails(
    account="Work",
    sender_exact="boss@company.com",
    read_status="unread",
    max_results=10,
    output_format="json",
)
# Repeat for other VIPs; collect message_ids from items[]

# 3. Flag action items (ids from step 1 or 2)
update_email_status(
    account="Work",
    action="flag",
    message_ids=["<message_id-from-needs-response>"],
    mailbox="INBOX",
    max_updates=5,
)

# 4. Preview the cleanup (ids from reviewed search/list; dry_run defaults to
#    True, so nothing is trashed yet)
manage_trash(
    account="Work",
    action="move_to_trash",
    message_ids=["<reviewed-message-id>"],
    mailbox="INBOX",
    max_deletes=10,
)

# 5. Trash for real, after reading the preview back to the user
manage_trash(
    account="Work",
    action="move_to_trash",
    message_ids=["<reviewed-message-id>"],
    mailbox="INBOX",
    max_deletes=10,
    dry_run=False,
)
```

### End of Day Cleanup (5 minutes)

```
# 1. Check unread count
get_mailbox_unread_counts(summary_only=True)

# 2. Quick scan recent
list_inbox_emails(max_emails=20, include_content=False)

# 3. Mark read non-essential
update_email_status(action="mark_read", message_ids=[...], mailbox="INBOX", max_updates=10)

# 4. Preview a small archive candidate set. Before keeping any id, apply the
#    Human-Sender Screen in email-archive-cleanup; leave human or ambiguous
#    correspondents visible.
processed = list_inbox_emails(
    account="Work",
    max_emails=20,
    include_content=False,
    output_format="json",
)
# Keep only ids that passed the Human-Sender Screen after reviewing the preview.
candidate_ids = ["<reviewed-automated-message-id>", ...]
move_email(
    account="Work",
    message_ids=candidate_ids,
    to_mailbox="Archive",
    from_mailbox="INBOX",
    max_moves=len(candidate_ids),
    dry_run=True,
)

# Show the dry-run count and selected subjects to the user. Only after explicit
# confirmation, repeat the same call with dry_run=False.

# 5. Review flagged items for tomorrow
# No tool lists flagged messages: search_emails rows carry no flag field.
# Use get_needs_response (it labels flagged rows HIGH) or read the flagged
# count from get_statistics(scope="account_overview") -- sample-based -- and
# review the flags themselves in Mail.app, then unflag by exact message_ids.
get_needs_response(days_back=7, max_results=10, output_format="json")
```

## Search & Find Workflows

### Find Specific Email Thread

```
# Option 1: Search by subject
search_emails(
    account="Work",
    subject_keyword="Project Alpha",
    include_content=True,
    max_results=5,
    max_content_length=300
)

# Option 2: Get full thread
get_email_thread(
    account="Work",
    message_id="<message_id-from-search>",
    mailboxes=["INBOX", "Sent", "Archive"],
    max_messages=20,
    output_format="json",
    include_preview=False
)

# Option 3: Advanced search
search_emails(
    account="Work",
    mailboxes=["INBOX", "Sent", "Archive"],
    subject_keyword="Project Alpha",
    sender_exact="client@example.com",
    include_content=True,
    max_results=10
)
```

### Find Emails from Specific Person

```
# All emails from sender
search_emails(
    account="Work",
    sender_exact="colleague@company.com",
    mailboxes=["INBOX", "Sent", "Archive"],
    recent_days=30,
    max_results=50
)

# Unread emails from sender
search_emails(
    account="Work",
    sender_exact="colleague@company.com",
    read_status="unread",
    mailbox="INBOX"
)

# Emails with attachments from sender
search_emails(
    account="Work",
    sender_exact="colleague@company.com",
    has_attachments=True,
    mailboxes=["INBOX", "Archive"],
    recent_days=30,
    max_results=20
)
```

### Find Emails by Date Range

```
# Emails from last month (search_emails scans at most 50 messages per call
# regardless of max_results/limit; page with offset for the rest)
search_emails(
    account="Work",
    date_from="2025-01-01",
    date_to="2025-01-31",
    mailboxes=["INBOX", "Sent", "Archive"],
    max_results=50
)

# Recent emails with keyword
search_emails(
    account="Work",
    subject_keyword="invoice",
    date_from="2025-01-15",
    mailboxes=["INBOX", "Archive"],
    max_results=20
)
```

### Find Emails with Attachments

```
# All emails with attachments
search_emails(
    account="Work",
    has_attachments=True,
    mailbox="INBOX",
    max_results=50
)

# Specific sender with attachments
attachment_matches = search_emails(
    account="Work",
    sender_exact="supplier@example.com",
    has_attachments=True,
    mailboxes=["INBOX", "Archive"],
    recent_days=30,
    max_results=20,
    output_format="json",
)

# Review matches, then list attachments by message id
message_ids = [item["message_id"] for item in attachment_matches["items"]]
list_email_attachments(
    account="Work",
    message_ids=message_ids[:5],
)

# Save specific attachment (use the 1-based attachment_index from the list output;
# 0 is refused with "attachment_index must be a positive 1-based integer")
save_email_attachment(
    account="Work",
    message_ids=[message_ids[0]],
    attachment_index=1,
    save_path="~/Desktop/invoice.pdf"
)
```

## Organization Workflows

### Daily Filing Routine

```
# 1. File project emails
project_matches = search_emails(
    account="Work",
    subject_keyword="Project Alpha",
    mailbox="INBOX",
    read_status="all",
    max_results=10,
    output_format="json",
)

project_ids = [item["message_id"] for item in project_matches["items"]]
move_email(
    account="Work",
    message_ids=project_ids,
    to_mailbox="Projects/Alpha",
    from_mailbox="INBOX",
    max_moves=len(project_ids),
)

# 2. File client emails
client_matches = search_emails(
    account="Work",
    sender_exact="client@example.com",
    mailbox="INBOX",
    recent_days=30,
    max_results=10,
    output_format="json",
)

client_ids = [item["message_id"] for item in client_matches["items"]]
move_email(
    account="Work",
    message_ids=client_ids,
    to_mailbox="Clients/ClientName",
    from_mailbox="INBOX",
    max_moves=len(client_ids),
)

# 3. Preview older read leftovers (human-approved date scan; prefer message_ids when possible)
move_email(
    account="Work",
    to_mailbox="Archive",
    from_mailbox="INBOX",
    older_than_days=30,
    only_read=True,
    allow_filter_scan=True,
    max_moves=20,
    dry_run=True
)
```

### Bulk Folder Organization

```
# 1. Review current structure
list_mailboxes(account="Work", include_counts=True)

# 2. Identify emails to organize
get_statistics(
    account="Work",
    scope="account_overview",
    days_back=30
)

# 3. Batch move by reviewed ids
# Example: Move all emails from a client
client_matches = search_emails(
    account="Work",
    sender_exact="bigclient@example.com",
    mailboxes=["INBOX", "Archive"],
    recent_days=30,
    max_results=50,
    output_format="json",
)

# Then move them (repeat with batches if >10)
client_ids = [item["message_id"] for item in client_matches["items"]]
move_email(
    account="Work",
    message_ids=client_ids[:10],
    to_mailbox="Clients/BigClient",
    from_mailbox="INBOX",
    max_moves=min(len(client_ids), 10),
)
```

### Archive Old Emails

```
# 1. Find old read emails
search_emails(
    account="Work",
    date_from="2020-01-01",
    date_to="2024-12-31",
    read_status="read",
    mailbox="INBOX",
    max_results=50
)

# 2. Review what you found
# (Check if any need to be kept in current folders)

# 3. Export if important, using a reviewed message id
export_emails(
    account="Work",
    scope="single_email",
    message_id="<reviewed-message-id>",
    mailbox="INBOX",
    save_directory="~/Documents/Email-Archives",
    format="txt"
)

# 4. Move reviewed ids to archive
move_email(
    account="Work",
    message_ids=["<reviewed-message-id-1>", "<reviewed-message-id-2>"],
    to_mailbox="Archive/2024",
    from_mailbox="INBOX",
    max_moves=2,
)
```

## Response Workflows

### Quick Reply

```
# 1. Find the email and keep its message_id
search_emails(
    account="Work",
    subject_keyword="Quick Question",
    include_content=True,
    max_results=1,
    max_content_length=300
)

# 2. Check the row before drafting: was_replied_to/has_draft are on every
#    discovery row. Abort if has_draft=true, or was_replied_to=true with no
#    matching draft. Thread check below is a fallback for extra certainty
#    or when has_draft is null; the thread only shows sent replies, so on a
#    null row also run manage_drafts(action="find", ...) per
#    pre-draft-verification.md before drafting.
get_email_thread(
    account="Work",
    message_id="<message_id from search>"
)

# 3. Reply immediately by exact id (default native_format=True; load email-drafting for focus/Accessibility)
reply_to_email(
    account="Work",
    message_id="<message_id from search>",
    reply_body="Yes, that works for me. Thanks!",
    reply_to_all=False
)

# 4. Archive the thread
move_email(
    account="Work",
    message_ids=["<message_id from search>"],
    to_mailbox="Archive",
    from_mailbox="INBOX",
    max_moves=1
)
```

### Deferred Response (Draft)

```
# 1. Review email content and keep its message_id
search_emails(
    account="Work",
    subject_keyword="Complex Request",
    include_content=True,
    max_results=1,
    max_content_length=500
)

# 2. Check the row before drafting: was_replied_to/has_draft are on every
#    discovery row. Abort if has_draft=true, or was_replied_to=true with no
#    matching draft. Thread check below is a fallback for extra certainty
#    or when has_draft is null; the thread only shows sent replies, so on a
#    null row also run manage_drafts(action="find", ...) per
#    pre-draft-verification.md before drafting.
get_email_thread(
    account="Work",
    message_id="<message_id from search>"
)

# 3. Create a reply draft (default native_format=True: rich quote + logo signature; needs Mail focus + Accessibility)
#    On REPLY_WINDOW_FOCUS_FAILED, retry with visible Mail. Do not use native_format=False (gated: WINDOWLESS_FALLBACK_DISABLED).
reply_to_email(
    account="Work",
    message_id="<message_id from search>",
    mode="draft",
    reply_body="Thank you for your email. I'm reviewing your request and will provide a detailed response by [date].\n\n[Draft notes: Need to check with team, review budget, etc.]"
)

# 4. Flag original email
update_email_status(
    account="Work",
    action="flag",
    message_ids=["<message_id from search>"],
    mailbox="INBOX",
    max_updates=1
)
```

### Reply to All in Thread

```
# 1. View full thread context
get_email_thread(
    account="Work",
    message_id="<message_id from search/list>",
    mailbox="INBOX",
    max_messages=20
)

# 2. Reply to all by exact id so Mail includes the original thread
reply_to_email(
    account="Work",
    message_id="<message_id from search/list>",
    reply_body="Based on the discussion, I agree with the proposal. Let's move forward.",
    reply_to_all=True
)
# Default native_format=True; load email-drafting for Accessibility / REPLY_WINDOW_FOCUS_FAILED
```

### Forward with Context

```
# 1. Find the email
matches = search_emails(
    account="Work",
    subject_keyword="Customer Issue",
    include_content=True,
    max_results=1,
    max_content_length=500,
    output_format="json",
)
message_id = matches["items"][0]["message_id"]

# 2. Forward to colleague
forward_email(
    account="Work",
    message_id=message_id,
    to="colleague@company.com",
    message="Hi [Name],\n\nCan you please help with this customer issue? It seems related to your area.\n\nThanks!",
    mailbox="INBOX"
)

# 3. Update status and move
update_email_status(
    account="Work",
    action="mark_read",
    message_ids=[message_id],
    mailbox="INBOX",
    max_updates=1,
)

move_email(
    account="Work",
    message_ids=[message_id],
    to_mailbox="Waiting For",
    from_mailbox="INBOX",
    max_moves=1,
)
```

## Cleanup Workflows

### Delete Spam and Newsletters

```
# 1. Identify unwanted senders
get_statistics(
    account="Personal",
    scope="account_overview",
    days_back=30
)
# Look for frequent senders you don't read

# 2. Search for their emails (discovery only)
newsletter_matches = search_emails(
    account="Personal",
    sender_exact="newsletter@unwanted.com",
    mailbox="INBOX",
    recent_days=30,
    max_results=50,
    output_format="json",
)
newsletter_ids = [item["message_id"] for item in newsletter_matches["items"]]

# 3. Preview trash (dry_run=True default; reversible)
manage_trash(
    account="Personal",
    action="move_to_trash",
    message_ids=newsletter_ids,
    mailbox="INBOX",
    max_deletes=20,
    dry_run=True
)

# 4. Verify trash (discovery)
search_emails(
    account="Personal",
    sender_exact="newsletter@unwanted.com",
    mailbox="Trash",
    recent_days=30
)

# 5. Execute trash after user confirms, then optionally delete permanent
manage_trash(
    account="Personal",
    action="move_to_trash",
    message_ids=newsletter_ids,
    mailbox="INBOX",
    max_deletes=20,
    dry_run=False
)
```

### Clean Up Old Emails

```
# 1. Find emails older than 90 days
search_emails(
    account="Work",
    date_from="2020-01-01",
    date_to="2024-10-01",
    read_status="read",
    mailbox="INBOX",
    max_results=50
)

# 2. Export important ones first (if needed; max_emails is capped at 50 per
#    call, page with offset for a mailbox with more than 50 messages)
export_emails(
    account="Work",
    scope="entire_mailbox",
    mailbox="INBOX",
    save_directory="~/Documents/Email-Backup",
    format="txt",
    max_emails=50,
)

# 3. Move reviewed ids to archive or delete
move_email(
    account="Work",
    message_ids=["<reviewed-message-id-1>", "<reviewed-message-id-2>"],
    to_mailbox="Archive/2024",
    from_mailbox="INBOX",
    max_moves=2,
)
```

### Empty Trash

```
# 1. Review what's in trash first
search_emails(
    account="Work",
    mailbox="Trash",
    recent_days=30,
    max_results=20
)

# 2. Export if anything important (max_emails is capped at 50 per call; page
#    with offset for more than 50 messages)
export_emails(
    account="Work",
    scope="entire_mailbox",
    mailbox="Trash",
    save_directory="~/Desktop/Trash-Backup",
    max_emails=50,
)

# 3. Preview the empty (dry_run defaults to True, so nothing is deleted yet)
manage_trash(
    account="Work",
    action="empty_trash",
    confirm_empty=True
)

# 4. Empty trash for real, after reading the preview back to the user
#    (CAREFUL - irreversible)
manage_trash(
    account="Work",
    action="empty_trash",
    confirm_empty=True,
    dry_run=False
)
```

## Draft Management Workflows

### Weekly Draft Review

```
# 1. List all drafts and keep the returned Id for each target draft
manage_drafts(
    account="Work",
    action="list"
)

# 2. Send completed drafts only when the server is intentionally not --draft-safe
#    and the user explicitly confirmed. Default plugin installs block sending.
manage_drafts(
    account="Work",
    action="send",
    draft_id="12345"
)

# 3. Delete outdated drafts. Deleting a draft requires explicit user
#    confirmation of that specific draft first. This call previews only:
#    manage_drafts defaults to dry_run=True. Read the preview back to the
#    user, then repeat the identical call with dry_run=False to delete.
manage_drafts(
    account="Work",
    action="delete",
    draft_id="12346"
)

# 4. Edit others (do in Mail app)
```

### Create Draft for Complex Email

```
# 1. Find the latest message and keep its message_id
search_emails(
    account="Work",
    subject_keyword="Complex Topic",
    mailboxes=["INBOX", "Sent"],
    limit=5
)

# 2. Review the thread by message_id
get_email_thread(
    account="Work",
    message_id="<message_id-from-search>",
    mailboxes=["INBOX", "Sent"],
    output_format="json",
    include_preview=False
)

# 3. Create a reply draft with original thread context
reply_to_email(
    account="Work",
    message_id="<message_id-from-search>",
    cc="team@company.com",
    reply_body="[Draft - Need to expand]\n\n1. Summary of situation\n2. Analysis\n3. Recommendation\n\n[Notes to self: Check data, consult with team]",
    mode="draft"
)
# Default native_format=True; load email-drafting for Accessibility / REPLY_WINDOW_FOCUS_FAILED

# 4. Schedule time to complete
# (Set calendar reminder to finish draft)
```

## Analysis Workflows

### Weekly Email Analytics

```
# 1. Get account overview
get_statistics(
    account="Work",
    scope="account_overview",
    days_back=7
)

# 2. Analyze top senders
# (Use sender names from overview)
get_statistics(
    account="Work",
    scope="sender_stats",
    sender="frequent-sender@example.com",
    days_back=30
)

# 3. Check mailbox distribution
list_mailboxes(
    account="Work",
    include_counts=True
)

# 4. Review unread counts
get_mailbox_unread_counts(summary_only=True)

# 5. Identify actions:
# - Unsubscribe from high-volume, low-value senders
# - Create folders for frequent senders
# - Archive/delete old emails in cluttered folders
```

### Sender Analysis and Action

```
# 1. Get sender statistics
get_statistics(
    account="Work",
    scope="sender_stats",
    sender="automated-reports@company.com",
    days_back=90
)

# 2. If too many emails, decide action:
#    Option A: Create filter (in Mail app)
#    Option B: Move to dedicated folder
#    Option C: Unsubscribe

# 3. Organize existing emails
report_matches = search_emails(
    account="Work",
    sender_exact="automated-reports@company.com",
    mailboxes=["INBOX", "Archive"],
    recent_days=30,
    max_results=50,
    output_format="json",
)

report_ids = [item["message_id"] for item in report_matches["items"]]
move_email(
    account="Work",
    message_ids=report_ids[:20],
    to_mailbox="Automated Reports",
    from_mailbox="INBOX",
    max_moves=min(len(report_ids), 20),
)
```

## Batch Operation Workflows

### Flag Multiple Emails for Review

```
# 1. Search for pattern
review_matches = search_emails(
    account="Work",
    subject_keyword="Q4 Review",
    mailboxes=["INBOX", "Archive"],
    recent_days=30,
    max_results=20,
    output_format="json",
)

# 2. Batch flag
review_ids = [item["message_id"] for item in review_matches["items"]]
update_email_status(
    account="Work",
    action="flag",
    message_ids=review_ids[:10],
    mailbox="INBOX",
    max_updates=min(len(review_ids), 10),
)
```

### Mark Multiple Emails as Read

```
# 1. Identify emails to mark read (discovery)
notification_matches = search_emails(
    account="Work",
    sender_domain="notifications.example.com",
    read_status="unread",
    mailbox="INBOX",
    max_results=20,
    output_format="json",
)
notification_ids = [item["message_id"] for item in notification_matches["items"]]

# 2. Batch mark as read by exact ids
update_email_status(
    account="Work",
    action="mark_read",
    message_ids=notification_ids,
    mailbox="INBOX",
    max_updates=len(notification_ids),
)
```

### Bulk Move by Sender

```
# 1. Find all emails from sender (discovery)
team_matches = search_emails(
    account="Work",
    sender_exact="project-team@company.com",
    mailbox="INBOX",
    recent_days=30,
    max_results=50,
    output_format="json",
)
team_ids = [item["message_id"] for item in team_matches["items"]]

# 2. Preview move in batches (max_moves=10 is safe)
move_email(
    account="Work",
    message_ids=team_ids[:10],
    to_mailbox="Projects/Team Project",
    from_mailbox="INBOX",
    max_moves=min(len(team_ids), 10),
    dry_run=True
)

# 3. Execute after user confirms; repeat with next batch if more than 10
move_email(
    account="Work",
    message_ids=team_ids[:10],
    to_mailbox="Projects/Team Project",
    from_mailbox="INBOX",
    max_moves=min(len(team_ids), 10),
    dry_run=False
)
```

## Backup and Export Workflows

### Export Important Mailbox

```
# 1. Check mailbox contents
search_emails(
    account="Work",
    mailbox="Important Project",
    recent_days=30,
    max_results=20
)

# 2. Export a bounded mailbox page (max_emails is capped at 50 per call;
#    page with offset for a mailbox with more than 50 messages)
export_emails(
    account="Work",
    scope="entire_mailbox",
    mailbox="Important Project",
    save_directory="~/Documents/Email-Backups/Important-Project",
    max_emails=50,
    format="txt"
)

# 3. Verify export
# (Check ~/Documents/Email-Backups/Important-Project directory)
```

### Export Single Important Email

```
# 1. Find the email
contract_matches = search_emails(
    account="Work",
    subject_keyword="Contract Agreement",
    include_content=True,
    max_results=1,
    max_content_length=0,  # Full content
    output_format="json",
)
message_id = contract_matches["items"][0]["message_id"]

# 2. Export with attachments
list_email_attachments(
    account="Work",
    message_ids=[message_id],
)

save_email_attachment(
    account="Work",
    message_ids=[message_id],
    attachment_index=1,  # 1-based, from the list output above
    save_path="~/Documents/Contracts/contract.pdf"
)

# 3. Export email text
export_emails(
    account="Work",
    scope="single_email",
    message_id=message_id,
    save_directory="~/Documents/Contracts",
    format="html"
)
```

### Export Correspondent History

```
export_emails(
    account="Work",
    scope="correspondent",
    email_address="person@example.com",
    include_sent=True,
    recent_days=30,
    max_emails=25,
    save_directory="~/Documents/Email-Backups/Correspondents/person-example",
    format="txt"
)
```

## Weekly Maintenance Workflow

```
# Monday Morning (30 min)

# 1. Review weekend emails
get_inbox_overview()

# 2. Triage urgent items
search_emails(subject_keyword="urgent", read_status="unread")
search_emails(subject_keyword="action required", read_status="unread")

# 3. Process inbox to zero
# (Use inbox zero workflow)

# 4. Review weekly tasks
# No tool lists flagged messages: search_emails rows carry no flag field.
# Use get_needs_response (it labels flagged rows HIGH) or read the flagged
# count from get_statistics(scope="account_overview") -- sample-based -- and
# review the flags themselves in Mail.app, then unflag by exact message_ids.
get_needs_response(days_back=7, max_results=10, output_format="json")

# 5. Set up for the week
manage_drafts(action="list")
```

```
# Friday Afternoon (30 min)

# 1. Complete pending replies
manage_drafts(action="list")
# Review each draft with the user. Sending needs a server that is intentionally
# not --draft-safe plus explicit approval of that draft; deleting needs explicit
# confirmation of that draft and a dry_run=False repeat of the preview call.

# 2. Clean up flagged items
# No tool lists flagged messages: search_emails rows carry no flag field.
# Use get_needs_response (it labels flagged rows HIGH) or read the flagged
# count from get_statistics(scope="account_overview") -- sample-based -- and
# review the flags themselves in Mail.app, then unflag by exact message_ids.
get_needs_response(days_back=7, max_results=10, output_format="json")
update_email_status(
    account="Work",
    action="unflag",
    message_ids=["<reviewed-message-id>"],
    mailbox="INBOX",
    max_updates=10,
)

# 3. Archive week's emails (collect ids from list/search first)
week_ids = ["<message_id-1>", "<message_id-2>"]  # from list_inbox_emails or search_emails
move_email(
    account="Work",
    message_ids=week_ids,
    to_mailbox="Archive",
    from_mailbox="INBOX",
    max_moves=len(week_ids),
)

# 4. Review statistics
get_statistics(scope="account_overview", days_back=7)

# 5. Plan for next week
# Note patterns, senders to filter, folders to create
```

## Tips for Using These Workflows

1. **Copy and adapt**: These are templates - adjust parameters for your needs
2. **Chain commands**: Run multiple commands in sequence for complex workflows
3. **Use max limits**: Always respect max_moves, max_deletes safety limits
4. **Review before deleting**: Always search first, then delete
5. **Export before cleanup**: Backup important emails before bulk operations
6. **Start small**: Test with small max values (5-10) before increasing

## Quick Reference: Most Common Commands

```
# Daily essentials (discovery → ids → action)
get_needs_response(account="Work")  # excludes already-replied/already-drafted by default
list_inbox_emails(max_emails=20, output_format="json")
search_emails(sender_exact="...", mailboxes=["INBOX"], max_results=20, output_format="json")
get_email_thread(account="Work", message_id="<message_id from search>")
reply_to_email(message_id="<message_id from search>", reply_body="...")  # load email-drafting for native reply
move_email(message_ids=["<id>"], to_mailbox="Archive", from_mailbox="INBOX", max_moves=1)

# Weekly maintenance
list_mailboxes(include_counts=True)
manage_drafts(action="list")
get_statistics(scope="account_overview", days_back=7)
update_email_status(action="flag", message_ids=["<id>"], mailbox="INBOX", max_updates=1)

# Cleanup operations
manage_trash(action="move_to_trash", message_ids=["<id>"], mailbox="INBOX", max_deletes=10)  # previews
manage_trash(action="move_to_trash", message_ids=["<id>"], mailbox="INBOX", max_deletes=10, dry_run=False)  # acts
export_emails(scope="entire_mailbox", mailbox="...", max_emails=50, offset=N, ...)  # 50 is the per-call cap; page with offset
export_emails(scope="correspondent", email_address="person@example.com", include_sent=True, recent_days=30, max_emails=25)
```

---

**Remember**: These workflows are starting points. Adapt them to your specific email patterns and work style.
