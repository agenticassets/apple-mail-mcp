---
name: apple-mail-operator
description: This skill should be used when the user asks "how does this Mail MCP work", "which tool should I use", "why is Mail slow", "set up Mail for the assistant", "list my accounts/mailboxes", "find an email quickly", or needs safe read/search navigation in Apple Mail with the MCP. Uses list_accounts, list_mailboxes, get_inbox_overview, list_inbox_emails, search_emails, get_email_by_id, and get_email_thread. Do NOT use for sustained inbox-zero programs (see email-management), a 5–10 minute triage ritual (see inbox-triage), or drafting mail (see email-drafting).
---

# Apple Mail Operator

Operational guide for using the Apple Mail MCP safely and quickly. Focus on bootstrap, selecting the correct tool per intent, avoiding slow cross-account scans, and understanding draft-safe versus send-capable setups.

## Recent-first navigation (required when triaging or drafting)

See [`recent-first-triage.md`](references/recent-first-triage.md). When finding mail to act on, start with the **newest** messages (`list_inbox_emails(max_emails=5)` or tight `search_emails(limit=5, recent_days=2..7)`). Do not open with month-old `date_from` sweeps while fresher inbox mail is unreviewed.

## Large-inbox pre-flight (required when inbox > ~5,000 messages)

See [`large-inbox-rules.md`](references/large-inbox-rules.md) for the canonical pre-flight checklist. On large **Exchange** accounts, also read [`exchange-account-patterns.md`](references/exchange-account-patterns.md).

### `full_inbox_export` is disabled

`full_inbox_export` returns a structured `UNBOUNDED_EXPORT_DISABLED` error and runs no AppleScript; it is never a usable full-inbox walk. For the rare full-inbox case (annual cleanup, full audit, compliance archive), page bounded calls instead: `export_emails(scope="entire_mailbox", mailbox=..., max_emails=50, offset=N)` advancing `offset` by 50 each call, or repeated `list_inbox_emails(max_emails=50)` / `search_emails(recent_days=..., limit=50, offset=N)` slices. For routine discovery, always pass a bounded `recent_days` / `max_emails` instead.

## Before drafting

If triage or search surfaced a `message_id` and the user wants a reply, load **`email-drafting`**. Pre-draft rules (already-replied check, thread verification, native reply): [`pre-draft-verification.md`](references/pre-draft-verification.md).

## When To Use This Skill

| Request signal | Use this skill |
|----------------|----------------|
| "How do I configure this?", "What's DEFAULT_MAIL_ACCOUNT?" | Yes |
| "List accounts / aliases / folders" | Yes |
| "How do I read or find a thread without moving mail?" | Yes |
| Performance or timeout troubleshooting | Yes |
| "Clean my inbox forever" / Inbox Zero program | No → `email-management` |
| "Quick scan what needs reply today" | No → `inbox-triage` |
| Draft or send a message | No → `email-drafting` |

## Bootstrap Checklist

1. Confirm Mail.app is running and macOS Automation + Mail Data Access are granted for the host app (terminal or IDE running the MCP).
2. Prefer setting **`DEFAULT_MAIL_ACCOUNT`** so tools default to one account instead of fanning out across every mailbox.
3. Default plugin installs run **`--draft-safe`**: drafts and open-for-review workflows work; **`mode="send"`** paths error until the server is reconfigured intentionally.
4. Set optional **`USER_EMAIL_PREFERENCES`** for stable tone and workflow hints; those preferences surface on preference-aware tool docstrings plus the **`email-style-profile`** skill.

### Missing MCP Tools Red Line

If `mcp__apple-mail__*` tools are absent from the client tool list, stop and fix MCP registration first. Do not create reply drafts through generic AppleScript, Mail UI scripting, shell `osascript`, or a standalone compose fallback. The only acceptable degraded path is the documented MCP-only absolute-path fallback that launches `plugin/start_mcp.sh --draft-safe`; after adding it, restart the MCP client and confirm the Apple Mail tools are registered before drafting.

## Decision Tree: Read And Navigate

| Goal | Primary tool chain |
|------|-------------------|
| See configured accounts | `list_accounts()` |
| See outbound identities | `list_account_addresses()` — takes no `account`; it returns every account mapped to its addresses, so filter the returned dict by account name yourself |
| Snapshot unread + recent hints | `get_inbox_overview()`; start compact `output_format`, avoid heavy dashboards during debugging |
| Page recent inbox bodies | `search_emails(limit=5..8, output_format="json")` for ids; `list_inbox_emails(max_emails=5..8)` for subject skim only |
| Locate a needle | Narrow `search_emails(limit=5, recent_days=2..7)` → if empty on Exchange, retry `sender="..."` with higher `limit` → `get_email_by_id(account=..., message_id=...)` |
| Conversation context | `get_email_thread(account=..., message_id=..., output_format="json")`, then check all four completeness fields before treating the result as the whole conversation (see **Thread completeness** below) |
| Mailbox map | `list_mailboxes(include_counts=true)` |
| Idle mail fetch | `synchronize_account(account="...", confirm_sync=True)` only after the user accepts that Mail may download a large backlog |

### Thread completeness

A thread result that looks whole can be a truncated slice of the conversation. Check completeness **before** summarizing a thread, counting its messages, deciding a reply is unnecessary, or collecting its ids for an archive or export, and call with `output_format="json"` to do it: the fields below are JSON-only. `matched == returned` with no `errors` does not settle the question: the scan is bounded independently of `max_messages`, and a scan that stopped early loses no reads, so every failure counter stays at zero.

**Four separate signals, none overlapping the others. `thread_incomplete` alone is not a completeness test — read all four.**

| Field | Meaning | Action |
|-------|---------|--------|
| `thread_incomplete: true` | A bound you did **not** choose cut the thread short. The three rows below are its components | Branch on the component that is set |
| ├ `scan_ceiling_hit: [mailbox, ...]` | That mailbox's candidate slice filled its bound, so messages behind it were never examined | Raise `scan_messages` (applied value echoed as `scan_messages_applied`); it is clamped at **400**. When 400 is not enough, narrow `mailboxes` to the one folder holding the conversation, or tell the user which mailbox could not be fully scanned |
| ├ `render_incomplete` / `candidate_scan_incomplete` | Rows were counted but not returned, or a candidate read threw | Read `errors` / `error_details`; retry with a larger `timeout` or a narrower mailbox list |
| └ `anchor_recovered: true` on an item | The scan missed the message you anchored on and it was added back from the direct fetch | Treat the whole thread as suspect: members near the anchor were likely missed too |
| `window_truncated: true` + `date_floor_hit: [mailbox, ...]` | Your own `recent_days` cutoff stopped the walk; older members exist outside the window. `thread_incomplete` stays **false** | Widen `recent_days`. Raising `scan_messages` will not help here |
| `return_limit_reached: true` | The thread hit your own `max_messages` return bound, so more members exist than were returned. `thread_incomplete` stays **false** | Re-run with a larger `max_messages`. Treat `returned == max_messages` as the same boundary whenever the field is absent |

Do not loop "re-run until the flag clears": with `scan_messages` capped at 400, a conversation older or deeper than that never clears, and the terminating move is a narrower `mailboxes` list or an honest report to the user.

Text mode is **not** an equivalent view. It prints a `PARTIAL:` line for the `recent_days` date floor, which `thread_incomplete` deliberately omits, but `attachment_count`, `anchor_recovered`, `matched` / `returned`, and every `*_hit` array reach JSON only; anchor recovery itself runs only in the JSON payload builder, so text mode never restores a missing anchor and never says one was missing.

The `All` mailbox selector is supported and resolves the anchor through a bounded probe, but an explicit `mailboxes=["INBOX", "Sent", "Archive"]` is faster and controls exactly what is scanned, so prefer it. Per-item `attachment_count` is `null` when Mail could not read the list, which is not the same as `0`. Exchange-specific thread caveats and the independent Sent cross-check stay in [`exchange-account-patterns.md`](references/exchange-account-patterns.md).

## Performance Rules

- **Recent-first:** newest received mail first; batches of 3 to 5; see [`recent-first-triage.md`](references/recent-first-triage.md).
- Run **narrow** queries first (`recent_days` small, explicit `account=`, `include_content=false`, tight `limit` ≤ 5 on first pass).
- Reserve `all_accounts=True` / cross-account scans for explicit user requests; large Exchange profiles may time out; partial JSON with `errors` is expected behavior.
- Prefer `search_emails(mailboxes=["INBOX", "Sent", ...])` to scan a few named folders over whole-profile fan-out on large Exchange/Gmail accounts. It returns a structured per-folder error for any missing or slow mailbox instead of failing the call.
- After `list_inbox_emails` or `search_emails` returns `message_id`, always drill with `get_email_by_id` rather than fuzzy re-search. `message_id` is always present in both text and JSON output from `list_inbox_emails`, so use it directly for follow-up actions.
- On Exchange, **subject-only** `search_emails` may return empty while the message is visible in overview; use sender search or offset pagination (`exchange-account-patterns.md`).
- **Never verify drafts with `search_emails`.** Drafts are unsent `outgoing message` objects with a null received date, so the date-filtered search is both slow and silently drops them. For troubleshooting, use `verify_draft(draft_id=...)` or `verify_drafts(draft_ids=[...])` for exact readiness checks, `manage_drafts(action="list", subject_contains="...")` for bounded newest-first discovery, `manage_drafts(action="find", in_reply_to="...")` for bounded header lookup, or `get_email_by_id(account=..., message_id=..., mailbox="Drafts")` for exact raw message fetches. Route draft lifecycle actions through `email-drafting`. `reply_to_email` verifies its own saved artifact before success: RFC-backed native identity can support later cleanup after revalidation, while a blank-iCloud-RFC transaction identity proves only the current call and is never a cleanup handle. A cap limit, delayed indexing, ambiguity, or drift disables automatic cleanup; newest-Drafts fallback is diagnostic only.
- **Draft ids drift on Exchange.** Numeric `draft_id`s from server accounts are reassigned on sync, even between two `manage_drafts(action="list")` calls with no writes in between. Re-resolve with a fresh `list`/`find` call immediately before acting on a `draft_id`; never reuse one cached from an earlier turn. `manage_drafts(action="find", in_reply_to=...)` is the durable handle for a reply draft.

## Operator Safety Patterns

| Need | Guidance |
|------|----------|
| No accidental sends | Keep `--draft-safe`; require explicit user confirmation before any send attempt |
| Quiet bulk drafts | Default `mode="draft"` on compose tools; do not leave unsaved compose windows |
| Review each draft in Mail | Use `mode="open"` (saves first, then leaves window open); for rich `.eml`, keep `open_in_mail=True` and set `review_in_mail=True` |
| Reply to a known message | Use `reply_to_email(message_id=...)`. `compose_email`, `create_rich_email_draft`, and `manage_drafts(action="create")` are standalone-only and **error out** on `Re:`/`Fwd:` subjects or quoted-thread bodies unless `standalone_confirmed=True` is explicitly passed. Use that override only for a confirmed new message that happens to start with `Re:`. |
| Read-only auditing | Mention `--read-only` server flag; removes send-facing compose registrations |
| Destructive moves/deletes | Defer to `email-archive-cleanup` or `email-management`; never bury trash/delete actions inside troubleshooting |

### When to reach for `inbox_dashboard`

`inbox_dashboard()` returns a snapshot of per-account unread counts plus recent emails in a single call. It defaults to `output_format="ui"`, which returns an interactive HTML resource for MCP Apps hosts; pass `output_format="json"` whenever you need to read the data yourself.

The JSON payload has `account`, `accounts` (per-account unread counts), `recent_emails` (each row carrying `was_replied_to` and `has_draft`), `errors`, `error_details` when non-empty, `draft_scan`, the `unread_count_source` / `unread_count_measured` / `unread_count_note` provenance fields, and the echoed `include_preview` / `max_total` / `max_per_account` bounds. **There is no pinned list and no suggestions list** — for suggested actions use `get_inbox_overview(include_suggestions=True)`, and for an actionable queue use `get_needs_response`. Read `errors` before reporting: an empty `recent_emails` alongside a non-empty `errors` is an incomplete scan, not an empty inbox, and `accounts` values are Mail's **cached** unread aggregate rather than measured counts.

Prefer it over chained `get_inbox_overview` + `list_inbox_emails` + `get_needs_response` calls when:

- `get_inbox_overview` is timing out or returning partial JSON with `errors` on a large inbox.
- The user asks for a visual or one-glance summary ("show me what my inbox looks like", "give me the dashboard").
- Consolidated unread counts and recent mail are enough, without three separate AppleScript round-trips.

It is heavier than a compact `get_inbox_overview` on small inboxes, so keep `get_inbox_overview(output_format="compact", ...)` as the daily-loop default. Escalate to `inbox_dashboard()` as the rescue path.

## Reply drafting handoff

This skill covers read/search navigation, not compose. For a reply request after discovery returns a `message_id`, load **`email-drafting`** and call `reply_to_email(message_id=..., reply_body=..., mode="draft")`.

- **Default path:** `native_format=True` (Mail's native reply window, colored quote bar, logo signature). Requires Mail focus and Accessibility permission for the host process; the body types in small focus-guarded chunks, never one keystroke of the whole body.
- **On `REPLY_WINDOW_FOCUS_FAILED`, `REPLY_SUBJECT_GUARD_MISMATCH`, or `REPLY_BODY_TYPING_INTERRUPTED`:** no draft with a partial body was left and no email was sent. Retry with Mail visible and unfocused elsewhere. Do not switch off native formatting (the windowless `native_format=False` path is gated: `WINDOWLESS_FALLBACK_DISABLED` unless `allow_windowless_fallback=True`, which agents must never set). If focus still cannot be acquired, stop and report the blocker.
- **On `REPLY_BODY_MISMATCH`:** inspect the named artifact id with `verify_draft` before cleanup. Delete only an RFC-backed identity revalidated as the draft under review; an iCloud transaction-only id and a fallback-discovered same-subject draft are not later-cleanup authorization.
- **Never** substitute `compose_email`, `create_rich_email_draft`, or `manage_drafts(action="create")` for in-thread replies. Collect ids via `search_emails` / `list_inbox_emails`; never pass `subject_keyword` to `reply_to_email`.

Full pre-draft verification, standalone-draft guards, and post-draft checks live in **`email-drafting`**.

## Additional Resources

- Root README → Configuration section for **`DEFAULT_MAIL_ACCOUNT`**, **`USER_EMAIL_PREFERENCES`**, **`--draft-safe`**, **`--read-only`**.
- Sibling **`inbox-triage`** for scripted daily queues.
- Sibling **`email-management`** when the objective is habitual processing and bulk transformation, not tooling orientation.
