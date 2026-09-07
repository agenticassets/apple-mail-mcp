"""Shared constants for Apple Mail MCP tools."""

# Newsletter detection patterns (sender-based)
NEWSLETTER_PLATFORM_PATTERNS = [
    "substack.com",
    "beehiiv.com",
    "mailchimp",
    "sendgrid",
    "convertkit",
    "buttondown",
    "ghost.io",
    "revue.co",
    "mailgun",
]

NEWSLETTER_KEYWORD_PATTERNS = [
    "newsletter",
    "digest",
    "weekly",
    "daily",
    "bulletin",
    "briefing",
    "news@",
    "updates@",
]

# Folders to skip during broad searches.
# Includes localized variants so non-English Mail.app accounts (Exchange,
# Outlook, Gmail in non-English locales) skip system folders correctly.
SKIP_FOLDERS = [
    # English / IMAP standards
    "Trash",
    "Junk",
    "Junk Email",
    "Deleted Items",
    "Sent",
    "Sent Items",
    "Sent Messages",
    "Drafts",
    "Spam",
    "Deleted Messages",
    # French (Exchange/Outlook + Gmail FR)
    "Corbeille",
    "Courrier indésirable",
    "Indésirables",
    "Éléments supprimés",
    "Éléments envoyés",
    "Messages envoyés",
    "Brouillons",
    "Boîte d'envoi",
    # German
    "Papierkorb",
    "Gesendet",
    "Entwürfe",
    "Werbung",
    # Spanish
    "Papelera",
    "Enviados",
    "Borradores",
    "Correo no deseado",
]

# Thread subject prefixes to strip when matching threads
THREAD_PREFIXES = ["Re:", "Fwd:", "FW:", "RE:", "Fw:"]

# Human-friendly time range mappings (name -> days)
TIME_RANGES = {
    "today": 1,
    "yesterday": 2,
    "week": 7,
    "month": 30,
    "all": 0,
}


# ---------------------------------------------------------------------------
# Scan bounds (Phase A of the whose-elimination refactor)
#
# Centralized bounded-slice caps that will replace per-tool magic numbers in
# wave-2 migrations. Kept here so a single edit retunes every tool.
#
# Hard ceilings (2026-07, AGENTIC-988 bounded-export hardening): on a
# ~9,700-message Exchange inbox, cold-cache property reads on a 250-message
# scan slice (search) or 500-message bind (inbox read-status filter) blew
# past wrapper timeouts. `SEARCH_HARD_CEILING` / `INBOX_HARD_CEILING` clamp
# the search and inbox scan paths to 50 messages read per call regardless of
# how the scaled caps below compute. `THREAD_SCAN_HARD_CEILING` is the one
# deliberate exception (see its comment): thread reconstruction is scoped to
# one conversation and must reach members that sit past a mailbox's newest 50.
# ---------------------------------------------------------------------------
SCAN_BOUNDS = {
    # Compose subject/draft fallbacks (drafts mailboxes are usually small).
    "DRAFT_LOOKUP": 75,
    "MESSAGE_LOOKUP": 75,
    # Trash listing and analytics slices.
    "TRASH_SCAN": 100,
    # smart_inbox per-mailbox ceilings.
    "INBOX_SHORT": 25,
    "INBOX_LONG": 75,
    # list_inbox_emails unread/read filter: scans up to max(max_emails*10, floor).
    "INBOX_DEFAULT_CAP": 100,
    "INBOX_MAX_CAP": 50,
    # Hard ceiling applied after INBOX_MAX_CAP in _build_inbox_collection_block.
    "INBOX_HARD_CEILING": 50,
    # search_emails window scaling via bounded_scan.compute_scan_upper_bound().
    "SEARCH_BASE_CAP": 40,
    "SEARCH_WINDOW_CAP": 50,
    "SEARCH_DAYS_SCALE": 3,  # added per recent_days day (was 25 inline)
    "BODY_SEARCH_AUTO_CAP": 25,  # body_text without explicit date_from
    # Hard ceiling applied after scan_cap in _build_search_script.
    "SEARCH_HARD_CEILING": 50,
    # get_email_thread candidate-scan window, via
    # bounded_scan.compute_scan_upper_bound(). Deliberately larger than the
    # search caps above and NOT clamped to SEARCH_HARD_CEILING: thread
    # reconstruction has to reach every member of one conversation, and a
    # conversation's earlier members routinely sit past the newest 50
    # messages of a busy mailbox. Before AGENTIC-2794 the thread scan reused
    # `max_messages` (default 50) as its per-mailbox slice, so a 9-message
    # conversation returned 5 members and reported itself complete. Measured
    # on a live Exchange account: raising the slice from 50 to 150 across
    # three mailboxes took the call from 6.8s to 10.9s and recovered all
    # members; 400 cost no more than 150.
    "THREAD_SCAN_BASE_CAP": 120,
    "THREAD_SCAN_DAYS_SCALE": 15,  # added per recent_days day
    "THREAD_SCAN_WINDOW_CAP": 400,
    # Hard ceiling applied after the scaled cap in get_email_thread.
    "THREAD_SCAN_HARD_CEILING": 400,
    # Mailboxes probed when resolving an anchor id under mailbox="All".
    "THREAD_ANCHOR_MAILBOX_PROBE_CAP": 20,
    # Multi-mailbox search fan-out limits.
    "MAX_MAILBOXES_PER_SEARCH": 20,
    "MAX_MAILBOXES_PER_SEARCH_ALL": 10,
    # Reply-state Drafts snapshot: full-header reads on drafts cost ~72ms
    # each (measured 2026-07-10), mirroring REPLIED_HEADER_READ_CAP in
    # core/replied.py for the analogous Sent-mailbox scan. Distinct from
    # DRAFT_LOOKUP above (compose-side draft/subject fallbacks): tune the
    # reply-state scan here, the compose lookup there.
    "DRAFT_SNAPSHOT_CAP": 50,
    "DRAFT_SNAPSHOT_HEADER_CAP": 10,
}


# ---------------------------------------------------------------------------
# Calendar bounds (3.10.0 Apple Calendar surface)
#
# Centralized caps for every calendar read, write, and fan-out path. One edit
# retunes every calendar tool. Values chosen in
# tasks/archive/2026-07/shipped/apple-calendar-tools/final-plan-2026-07-10.md section 4:
# Calendar.app AppleScript `whose` scans cost tracks total store size (not
# window size), so result caps, an inner scan cap, and an aggregate per-call
# wall-clock budget all apply together.
# ---------------------------------------------------------------------------
CALENDAR_BOUNDS = {
    # Hard cap on any event window width in days (a year plus slack).
    "MAX_WINDOW_DAYS": 370,
    # Maximum events returned per call; page with `offset`.
    "EVENT_RETURN_CAP": 200,
    # Inner AppleScript slice cap applied per fetch pass.
    "EVENT_SCAN_CAP": 300,
    # Hard stop for recurring-occurrence expansion per call.
    "OCCURRENCE_SCAN_CEILING": 750,
    # check_availability window cap (slot folding walks the window).
    "AVAILABILITY_MAX_WINDOW_DAYS": 62,
    # Availability fetch extends back this many days to catch events that
    # started before the requested window but overlap it.
    "AVAILABILITY_FETCH_PAD_DAYS": 1,
    # batch_create_events items per call.
    "BATCH_CREATE_CAP": 25,
    # delete_events default max_deletes and its absolute ceiling.
    "BULK_DELETE_DEFAULT_MAX": 20,
    "BULK_DELETE_CEILING": 100,
    # Attendee and alarm caps per event.
    "MAX_ATTENDEES": 50,
    "MAX_ALARMS_PER_EVENT": 5,
    # get_events_by_id input cap; also the delete-path per-osascript chunk.
    "MAX_EVENT_IDS_PER_CALL": 25,
    # Unscoped fan-out cap across calendars.
    "MAX_CALENDARS_PER_QUERY": 20,
    # list_events bounded default window (days ahead).
    "DEFAULT_UPCOMING_DAYS": 7,
    # Default id-lookup window when no absolute window is provided.
    "UID_LOOKUP_BACK_DAYS": 30,
    "UID_LOOKUP_AHEAD_DAYS": 90,
    # Recurring-master second-pass horizon and its own result cap.
    "RECURRING_LOOKBACK_DAYS": 400,
    "RECURRING_MASTER_SCAN_CAP": 200,
    # list_events notes preview truncation.
    "NOTES_PREVIEW_CHARS": 280,
    # Aggregate wall-clock budget per fan-out tool call (seconds).
    "CALL_BUDGET_SECONDS": 240,
}
