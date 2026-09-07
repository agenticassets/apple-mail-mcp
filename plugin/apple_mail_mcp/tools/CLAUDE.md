# tools/ — MCP tool registrations
All `@mcp.tool` handlers live here; `apple_mail_mcp/__init__.py` imports these seven tool surfaces (the `inbox/`, `search/`, `compose/`, `manage/`, `analytics/`, `smart_inbox/`, and `calendar/` packages) for side-effect registration. **41 tools**; verify: `rg '^@mcp\.tool' plugin/apple_mail_mcp/tools | wc -l` (recursive: every surface is a package).

## Module map

| Module | # | Purpose / tools |
|--------|---|-----------------|
| `inbox/list_emails.py` | 1 | Listing: `list_inbox_emails` (async per-account dispatch; `parsing.py`/`list_scripts.py` leaves) |
| `inbox/unread_counts.py` | 1 | Unread totals: `get_mailbox_unread_counts` (cached-count provenance via `unread_provenance.py`; sentinel key `PROVENANCE_KEY`) |
| `inbox/accounts.py` | 2 | Account enumeration: `list_accounts`, `list_account_addresses` |
| `inbox/mailboxes.py` | 1 | Folder listing: `list_mailboxes` |
| `inbox/overview.py` | 1 | Overview: `get_inbox_overview` (script builder + parser; `overview_formatting.py` holds the pure text/JSON formatters) |
| `search/emails.py` | 1 | Find: `search_emails` (windowing, replied-detection; `scan_cap.py` is a pure leaf holding the scan-bound arithmetic) |
| `search/by_id.py` | 2 | Exact-id fetch: `get_email_by_id`, `get_email_by_ids` |
| `search/thread.py` | 1 | Thread reconstruction: `get_email_thread` (script builder; `thread_helpers.py` holds the subject/header matching and the marker channel, `thread_payload.py` the pure JSON payload assembly, `anchor.py` the bounded anchor-mailbox probe for the `All` selector) |
| `compose/send.py` | 1 | Standalone send/draft: `compose_email` |
| `compose/reply.py` | 1 | Reply (native window default): `reply_to_email` |
| `compose/forward.py` | 1 | Forward draft: `forward_email` |
| `compose/manage.py` | 1 | Draft listing/management: `manage_drafts` |
| `compose/rich_draft.py` | 1 | Rich standalone draft: `create_rich_email_draft` |
| `compose/verify_tools.py` | 2 | Exact-id draft verification: `verify_draft`, `verify_drafts` |
| `manage/move.py` | 1 | Move: `move_email` (id-direct + filter-scan; `_move_email_by_message_ids` helper) |
| `manage/attachments.py` | 1 | Attachment save: `save_email_attachment` (size/disk probes) |
| `manage/status.py` | 1 | Read/flag status: `update_email_status` |
| `manage/trash.py` | 1 | Trash ops: `manage_trash` (move_to_trash/delete_permanent/empty_trash) |
| `manage/mailbox.py` | 1 | Folder creation: `create_mailbox` (nested paths) |
| `manage/sync.py` | 1 | IMAP sync: `synchronize_account` (`helpers.py` is a shared leaf) |
| `analytics/attachments.py` | 1 | Attachment listing: `list_email_attachments` across named mailboxes (`attachments_helpers.py` is a pure parse/merge/render leaf) |
| `analytics/statistics.py` | 1 | Stats: `get_statistics` (account_overview/sender_stats/mailbox_breakdown) |
| `analytics/export.py` | 1 | Export: `export_emails` by exact ids, bounded filters, correspondent/thread scopes, or mailbox pages; supports raw EML and optional size-capped attachment bundles (`export_helpers.py` / `export_formatting.py` / `export_thread_scope.py` leaves) |
| `analytics/full_export.py` | 1 | Disabled refusal shim: `full_inbox_export` (returns `UNBOUNDED_EXPORT_DISABLED`, no AppleScript runs) |
| `analytics/dashboard.py` | 1 | Dashboard: `inbox_dashboard` + recent-email helpers |
| `smart_inbox/awaiting_reply.py` | 1 | Follow-up tracking: `get_awaiting_reply` (sent-vs-inbox Message-ID cross-reference; `helpers.py` shares `_normalize_message_id`) |
| `smart_inbox/needs_response.py` | 1 | Actionable detection: `get_needs_response` (newsletter/automated filtering, replied-detection join) |
| `smart_inbox/top_senders.py` | 1 | Sender analytics: `get_top_senders` (bounded newest-first Counter aggregation, domain grouping) |
| `calendar/calendars_list.py` | 1 | Calendar enumeration: `list_calendars` (writability, defaults, engine diagnostics) |
| `calendar/events_list.py` | 1 | Bounded event listing/search: `list_events` (windows, query, participant-name/address filtering, recurring expansion, paging; `helpers.py` shares the fan-out collector) |
| `calendar/events_get.py` | 1 | Exact-id detail fetch: `get_events_by_id` (notes, alarms, attendees; window-bounded) |
| `calendar/availability.py` | 1 | Free-busy folding: `check_availability` (busy blocks + free slots, 62-day cap) |
| `calendar/events_create.py` | 1 | Event creation: `create_event` (timezone-correct, alarms, allowlisted RRULE, conflict detection) |
| `calendar/events_batch.py` | 1 | Batch creation: `batch_create_events` (25-item cap, all-or-nothing validation, per-item writes) |
| `calendar/events_update.py` | 1 | ID-first PATCH: `update_event` (span rules, attendee-set diffing, dry-run) |
| `calendar/events_delete.py` | 1 | Exact-id bulk delete: `delete_events` (dry-run default, resolve-first, chunked) |
| `calendar/calendars_manage.py` | 1 | Calendar CRUD: `manage_calendars` (create/rename/delete, triple-gated cascade delete) |
| `calendar/rsvp.py` | 1 | Refusal shim: `respond_to_invitation` (returns `CALENDAR_RSVP_UNSUPPORTED`, no engine call) |

## Calendar surface notes

- All Calendar.app I/O flows through `calendar_core/` (engine seam): reads via `calendar.get_engine()` (AppleScript, or EventKit when installed and already granted), writes via `calendar.get_write_engine()` (AppleScript only in 3.10.0). Never emit `every event of` outside `calendar_core/scripts_read.py`/`scripts_write.py`; the lint in `tests/calendar_surface/test_calendar_scripts.py` enforces date-bounded predicates.
- Mode gating is stricter than mail (new plumbing, not the `_send_blocked` port): `--read-only` removes `CALENDAR_WRITE_TOOLS` + `CALENDAR_DESTRUCTIVE_TOOLS`; `--draft-safe` blocks deletes (`CALENDAR_DELETE_BLOCKED`, env unlock `CALENDAR_ALLOW_DESTRUCTIVE=1`) and attendee sends (`INVITE_SEND_BLOCKED`). Internal guards live in `calendar/helpers.py` because the CLI bypasses registry removal.
- Caps live in `constants.CALENDAR_BOUNDS`; every event read requires a `bounded_calendar_window` token. Unscoped reads fan out (capped at 20 calendars + a 240s call budget), which deliberately differs from mail's account-scoping default.
- Calendar ids are UUID-like strings (`calendar_core.validation.normalize_event_ids`), never the numeric Mail id helpers.

## Add a tool

1. Pick module by domain; add `@mcp.tool(annotations=…)` using presets from `../server.py` (matrix: [`tasks/reference/phase-3-annotation-matrix.md`](../../../tasks/reference/phase-3-annotation-matrix.md)).
2. `@inject_preferences` on user-facing tools; user strings → `core.escape_applescript()`; multi-account fan-out → `async` + `asyncio.to_thread`, dispatched sequentially (one account at a time), not via `asyncio.gather`, since all installed plugin hosts for this macOS user queue each `osascript` invocation through the shared cross-process lock in `core/applescript.py`.
3. New file → import in `__init__.py`; update the root release version table files when releasing, plus `apple-mail-mcpb/manifest.json` `tools[]` and advertised tool count.

## Performance (summary)

- Default `recent_days=2.0` (48h). Tools refuse unbounded scans (`recent_days=0` / `max_emails=0`) with `code: UNBOUNDED_SCAN_REQUIRED`; `search_emails` answers a non-positive `limit`/`max_results` with the same code, and a negative `offset` with `"Error: offset must be >= 0"` (matching `export_emails` / `list_events`), both before any AppleScript is built. `full_inbox_export` is disabled (`code: UNBOUNDED_EXPORT_DISABLED`, no AppleScript runs) and is not a working fallback; narrow the window (`recent_days` / `date_from`) or page through bounded calls (`export_emails`, `list_inbox_emails`, `search_emails`) instead. Prefer bounded newest-message slices (`messages 1 thru N`) over broad `whose` clauses on large remote mailboxes.
- Pass `timeout` through to `run_applescript`; catch `AppleScriptTimeout` → structured error with account name.
- **ID-first mutations (v3.7.0+):** `move_email`, `update_email_status`, and `manage_trash` prefer `message_ids` from a prior list/search. `subject_keyword` / `sender` on action tools return `TARGET_SELECTOR_DEPRECATED` before any scan (even with `allow_filter_scan=True`). Date/bulk filter paths require `allow_filter_scan=True` or return `FILTER_SCAN_DISABLED`. `search_emails` requires `allow_body_scan=True` when `body_text` is set or returns `BODY_SCAN_DISABLED`.
- **Scan caps (2026-07, AGENTIC-988 hardening):** `SEARCH_HARD_CEILING` and `INBOX_HARD_CEILING` in `constants.py` `SCAN_BOUNDS` clamp `search_emails` and `list_inbox_emails` to at most **50 messages scanned per call**, regardless of `limit` / `max_emails` / `recent_days`; `get_statistics` per-mailbox reads share the same 50-message cap (fanning across 10 or 20 mailboxes instead); `mailbox="All"` fan-out stays capped at 10 accounts. See `docs/CLAUDE-conventions.md` § Centralized scan caps.
- **Mail calls are serialized:** all installed plugin hosts for this macOS user queue every `osascript` invocation through one shared cross-process lock in `core/applescript.py`. Concurrent/parallel Mail tool calls queue behind each other and can time out. Call one Mail tool at a time.
- Mutations: `normalize_message_ids` / `message_ids` for targeted ops. Detail: `docs/CLAUDE-conventions.md`.

## Structured error codes (agent-facing)

Returned as JSON (`serialize_tool_error`) with `code`, `message`, and `remediation` fields. Tests in `tests/cross_cutting/test_phase_2_scan_hardening.py` and `tests/search/test_mail_search_tools.py` lock the contracts.

**Boundary conversion (38 of 41 tools).** `server.py` wraps every registered tool so an *uncaught* `ToolError` becomes this envelope instead of propagating as a transport exception. Calendar tools catch `ToolError` themselves and are unaffected (no double-serialization). Three tools are **excluded** because they declare container return types, and FastMCP validates the returned value against a structured-output schema derived from that annotation — handing them a JSON string yields a pydantic validation error with the real code buried inside `input_value`, which is worse than raising:

| Tool | Return annotation |
|------|-------------------|
| `list_accounts` | `list[str]` |
| `list_account_addresses` | `dict[str, list[str]]` |
| `get_mailbox_unread_counts` | `dict[str, Any]` |

Those three **raise** `ToolError` with an accurate message rather than returning the envelope. `tests/core/test_tool_error_envelope_boundary.py::test_container_return_tools_are_the_only_boundary_exceptions` pins the set so it cannot grow silently. Making the surface uniformly `-> str` would remove the exception, but widening those annotations changes the published `outputSchema`, which is a client-visible contract change.

| Code | When | Remediation hint |
|------|------|------------------|
| `FILTER_SCAN_DISABLED` | `move_email` / `update_email_status` / `manage_trash` called with filters but no `message_ids` and `allow_filter_scan=False` | Collect ids first; or `allow_filter_scan=True` for approved bulk |
| `BODY_SCAN_DISABLED` | `search_emails(body_text=...)` without `allow_body_scan=True` | Narrow with subject/sender/date; or opt in with tight `date_from` |
| `UNBOUNDED_SCAN_REQUIRED` | Routine scan with `recent_days=0` / `max_emails=0`, or a non-positive page size (`search_emails` `limit`/`max_results` <= 0) | Pass a bounded window (`recent_days` / `date_from`) and a positive `limit`, or page through bounded calls (`export_emails`, `list_inbox_emails`, `search_emails`); `full_inbox_export` is disabled and is not a valid remediation |
| `INVALID_SCAN_WINDOW` | Forged or out-of-policy `ScanWindow` token | Call `bounded_inbox_scan()` only |
| `WHOSE_ID_LIST_TOO_LARGE` | `message_ids` longer than `MAX_WHOSE_IDS` (50) | `iter_id_chunks` + one call per batch |
| `UNSAFE_WHOSE_ON_LIST` | `build_bounded_message_scan(..., whose_condition=...)` | Use `build_bounded_filtered_scan` |
| `HTML_COMPOSE_SUBJECT_RESTORE_FAILED` | HTML compose left a visible `__apple_mail_mcp_` marker subject or could not restore/verify the real subject before save | Inspect Drafts and open compose windows for the requested real subject; do not send a marker subject |
| `REPLY_SENDER_OVERRIDE_FAILED` | `reply_to_email(from_address=...)` and Mail refused `set sender of replyMessage` | Nothing was saved and the compose window was closed with `saving no`. Confirm the address is a configured Mail identity (`list_account_addresses`), then retry; omit `from_address` to use the account default |
| `INVALID_TIMEOUT` | Any tool passed `timeout` <= 0 or > 3600 to `run_applescript` | Raised before the Mail lock is acquired, so nothing ran. Pass a positive value in seconds, or omit for the tool default. `0` does not mean "no deadline" — omit the parameter instead |
| `INVALID_ACTION_CAP` | `update_email_status` called with `max_updates` <= 0 | Refused before any Mail I/O. Pass a positive cap, or omit for the default. A non-positive cap is not "act on nothing": the id-resolution helper used to return one message for `limit=0` (AGENTIC-2374). `manage_trash` / `move_email` carry the same guard and refuse a non-positive `max_deletes` / `max_moves` with `UNBOUNDED_SCAN_REQUIRED` |
| `REPLY_WINDOW_NOT_IDENTIFIED` | Native reply opened a compose window that could not be told apart from windows already open (`GUARD_ABORT_WINDOW`); the body was never typed | Nothing saved, nothing sent. Close other compose windows on the same thread and retry — the window is adopted by being the one *new* window matching the reply subject. Do not switch off `native_format`, and do not just "retry with Mail visible": each blind retry opens another window and makes the ambiguity worse |
| `QUOTE_PROOF_UNAVAILABLE` | The source message has no readable content to anchor the quote proof, so the native reply was abandoned before typing | Nothing saved, nothing sent. Read the source with `get_email_by_id` to confirm it has a body; a message Mail cannot read has nothing to prove the quote against |
| `REPLY_NOT_COMPLETED` | Catch-all for a `reply_to_email(output_format="json")` compose run that reported neither success nor a known sentinel | Read `remediation.script_output` — it is Mail's own failure text, most often "no message matched `message_id` within `recent_days`". Widen `recent_days` or re-resolve the id with `search_emails`. This code exists so a JSON caller gets a parseable `code` instead of a `json.loads` error on prose |

## Forbidden AppleScript patterns

**Lint-enforced** by `tests/core/test_no_unbounded_whose.py` — these are the catalogued crash modes. Detail + safe alternatives: [`docs/CLAUDE-conventions.md § Forbidden AppleScript patterns`](../../../docs/CLAUDE-conventions.md#forbidden-applescript-patterns-lint-enforced).

| Don't write | Failure mode | Write instead |
|-------------|--------------|---------------|
| `<sliceVar> whose <pred>` (slice-bound list + `whose`) | Gmail crash: `Can't get {message id N of mailbox "[Gmail]/All Mail" ...} whose ...` | `build_bounded_filtered_scan(...)` from `bounded_scan` |
| `every message of MB whose <non-id-pred>` (unbounded `whose`) | Materializes whole mailbox; hangs on 24K+ inboxes | `build_bounded_message_scan(...)` + in-loop `repeat ... if` |
| `every message of MB` (no `whose`) | Raw enumeration | `messages 1 thru N of MB` |
| `build_bounded_message_scan(..., whose_condition=...)` | Raises `UNSAFE_WHOSE_ON_LIST` at runtime | `build_bounded_filtered_scan(...)` |
| `build_whose_id_list(ids)` with > 50 ids | Mail parser crash/hang; raises `WHOSE_ID_LIST_TOO_LARGE` | `iter_id_chunks(ids)` + loop |
| Bare property in a spliced condition: `subject contains "x"` inside `repeat with aMessage in …` | No implicit target outside `whose`; -1728 `Can't get subject.` on every message, swallowed by the loop's `try` → silent 0 results that look authoritative (AGENTIC-2344) | `set messageSubject to subject of aMessage`, then test `messageSubject contains "x"`. Never reuse a `whose`-shaped condition in a `repeat` loop |
| Pipe-row emit without `sanitize_pipe_delimited_field` on user fields | Subject containing `&#124;&#124;&#124;` corrupts `message_id` → wrong-email delete | `core.sanitize_pipe_delimited_field("messageSubject")` etc. |

When in doubt, copy the pattern from `search/emails.py`'s per-message loop — it has been audited as Gmail-safe and Exchange-bounded.

## Account scoping

`account: Optional[str] = None` → `server.DEFAULT_MAIL_ACCOUNT`; error if unset. Exceptions: `synchronize_account` — a bare `account=None` with no `DEFAULT_MAIL_ACCOUNT` is an **error**, not a fan-out; syncing every account requires `all_accounts=True` **and** `confirm_sync=True` (and `all_accounts=True` also overrides the configured default). `inbox_dashboard` also respects `DEFAULT_MAIL_ACCOUNT` and only fans out across all accounts when no account/default is configured. `all_accounts=True` overrides default scoping.

## JSON `output_format`

Normalized dict JSON: `get_statistics`, `get_inbox_overview`, `list_inbox_emails`, `list_mailboxes`, `get_needs_response`, `get_awaiting_reply`, and `get_top_senders`.

Per-email rows on `list_inbox_emails`, `search_emails`, `get_email_by_id`, `get_email_by_ids`, `get_email_thread`, `get_needs_response`, `inbox_dashboard`, and `get_inbox_overview` recent rows also carry `was_replied_to` / `has_draft`, and each response carries a top-level `draft_scan` status object; docstrings on the individual tools are the source of truth for exact field shapes.

`reply_to_email(output_format="json")` is a compose contract for verified `mode="draft"` / `mode="open"` only. It returns reply artifact metadata including `draft_id`, `verified_draft_id`, `exact_id_verified`, `attachment_status`, `attachment_count`, `attachments_applied`, and verification status fields. Effective `mode="send"` with JSON is rejected before mutation because sent replies do not produce a verifiable Drafts artifact.

## Cached unread counts (AGENTIC-2346)

Mail's `unread count of <mailbox>` is a **cached aggregate**, not a computed one, and it drifts low. Measured 2026-08-17 on a 25,012-message Exchange Inbox: Mail reported **3,236** unread where per-message truth was **10,016** (68% under-report); a 1,549-message folder on the same account was off by 1. `count of messages` is reliable.

Recomputing it was measured, not assumed. `count of (messages of <mb> whose read status is false)` — the one `whose` predicate the bounded-scan lint allows on a mailbox — returned in <1 s on 393 messages, 4 s on 1,549, and **no result at 240 s or 300 s** on 25,012. It is affordable only where the cache is already right and unaffordable exactly where it is wrong, and 4 s per mid-size folder cannot be spent across the 100-mailboxes-per-account fan-out. An exact cheap path means reading Mail's `Envelope Index` (AGENTIC-2345), not AppleScript.

So the four reporting surfaces label the number instead. Single source: [`unread_provenance.py`](unread_provenance.py) (`unread_count_disclosure`, `measured_unread_disclosure`, `unread_count_text_label`, `unread_count_text_footer`). Never emit a bare `unread count` value from a new tool — route it through that module.

| Field | Meaning |
|-------|---------|
| `unread_count_source` | `mail_cached_aggregate` (cached) or `per_message_read_status` (counted in this call's bounded sample) |
| `unread_count_measured` | `false` for the cache, `true` for a per-message count |
| `unread_count_note` | Agent-facing prose; emitted once per payload envelope, not per row |
| `unread_count_suspect` + `_reason` + `_detail` | Set only when the cached value is **provably** wrong |

Two cross-checks are free wherever the tool already read the data, and only those two:

- `cached_unread_exceeds_message_count` — needs `count of messages` (`list_mailboxes(include_counts=True)`, `get_inbox_overview`, `get_statistics`).
- `sampled_unread_exceeds_cached_unread` — needs per-message `read status`; unread in a newest-first slice is a strict lower bound (`get_inbox_overview`'s recent pass).

`get_mailbox_unread_counts` reads neither, so it labels but never flags, and carries the block under the `__unread_count_provenance__` sentinel key (same dunder convention as `__truncated__`). **A clean check is not proof of a correct count** — the measured 3,236-vs-10,016 case trips neither. `get_statistics`'s `read` is `total - unread` and inherits the cache error with the sign flipped, so it carries the same label. `sender_stats` counts per-message `read status` and is genuinely measured.

Contract tests: `tests/cross_cutting/test_unread_count_provenance.py`.

## Scan bounds are not return bounds (AGENTIC-2794)

A **scan bound** is how many messages a tool examines; a **return bound** is how many rows it hands back. Sizing the first from the second is the defect class this section exists to prevent, and it is worse than a timeout: the tool completes, the counts reconcile, and every incompleteness flag reads false. `get_email_thread` bounded its per-mailbox candidate slice by `max_messages`, so a request for at most 50 members also examined only the newest 50 messages of each mailbox. Measured live: a 9-message conversation returned 5 members with `matched=5 returned=5 render_incomplete=false candidate_scan_incomplete=false errors=null`. Nothing was lost in a `try`, so no failure counter moved.

Two rules follow.

**Size the scan from the window, not the page.** `bounded_scan.compute_scan_upper_bound(recent_days, base_cap=..., window_cap=..., days_scale=...)` is the only sanctioned arithmetic; the caps live in `SCAN_BOUNDS`. `search_emails` derives its window from `recent_days` **or**, when only `date_from` was passed, from that date's age (`search/scan_cap.py`) — the two spell the same window, so they widen the slice the same way. `get_email_thread` sizes from `THREAD_SCAN_BASE_CAP` (120) + `THREAD_SCAN_DAYS_SCALE` (15/day), clamped by `THREAD_SCAN_WINDOW_CAP` / `THREAD_SCAN_HARD_CEILING` (400), and takes an explicit `scan_messages` override. That 400 is **not** governed by `SEARCH_HARD_CEILING`/`INBOX_HARD_CEILING` (50): it is the one deliberate exception, justified in the `SCAN_BOUNDS` comment in [`constants.py`](../constants.py), because thread reconstruction is scoped to a single conversation whose earlier members routinely sit past a busy mailbox's newest 50. Do not copy the exception into a general listing or search path.

**A tool that stopped early must say so in-band, in both output modes.** A caveat that reaches only the JSON payload is invisible to a text caller and vice versa, so a marker row carries the fact to JSON *and* the text render carries it too: `get_email_thread`, `list_email_attachments`, and `export_emails(scope="thread")` emit `PARTIAL:` lines, while `search_emails` prefixes a `WARNING:` line in `_build_search_response`. Report the bound the scan actually used, never the constant it was derived from (a body-capped scan stops at `BODY_SEARCH_AUTO_CAP` = 25, not at 50).

| Marker prefix | Emitted by | Python channel |
|---------------|-----------|----------------|
| `ERROR_MAILBOX\|\|\|<mailbox>\|\|\|<msg>` | search, thread, `list_email_attachments` | `errors` / `error_details`; one bad mailbox never aborts the rest |
| `SCAN_CEILING\|\|\|<mailbox>\|\|\|<bound>` | `search_emails` | `scan_ceiling_reached` / `scan_ceiling` / `scan_ceiling_mailboxes` + a `warnings` entry. Split out of `error_details` by `_non_ceiling_errors`: a saturated scan is a bound, not a failure |
| `THREAD_SCAN_CEILING\|\|\|<mailbox>\|\|\|<bound>` | `get_email_thread` | `scan_ceiling_hit[]`; remediation is a larger `scan_messages` |
| `THREAD_DATE_FLOOR\|\|\|<mailbox>\|\|\|<cutoff>` | `get_email_thread` | `date_floor_hit[]`; remediation is a wider `recent_days`. Suppresses the ceiling row for that mailbox — the window ran out before the slice did, so the slice was not the limiting bound |
| `THREAD_ATTACHMENTS\|\|\|<id>\|\|\|<count>[\|\|\|<reason>]` | `get_email_thread` | per-item `attachment_count`; a negative count becomes `null`, which is **not** `0` |
| `SEEN_MESSAGE\|\|\|<mailbox>\|\|\|<id>\|\|\|…` | `list_email_attachments` | proof a message was read; without it a message with zero attachments emits nothing and is indistinguishable from an id that was never found |

**Marker rows must be split out in Python before `_parse_search_records` runs.** That parser splits record rows with `split("|||", 14)`, so a caveat carried as a 16th field folds into `was_replied_to` instead of being parsed. `search/thread_helpers.split_thread_markers()` lifts the three `THREAD_*` rows out first and returns the remaining text; `search/records._parse_search_records` handles `ERROR_MAILBOX` / `SCAN_CEILING` itself because it owns those prefixes. Never widen a record row to carry a caveat.

`get_email_thread` folds every component signal into one boolean, `thread_incomplete`: a render shortfall, a candidate-scan error, the **scan ceiling**, `anchor_recovered`, or a script error. Callers branch on that; the component fields say what to do about it. New tools that can stop early should offer the same single flag rather than making the caller compose four.

**The `recent_days` date floor is deliberately excluded from that flag** and reported separately as `window_truncated` + `date_floor_hit[]`. It fires whenever a mailbox holds anything older than the requested window, which is nearly always, so folding it in made `thread_incomplete` true on every realistic call — trading one useless signal for another. The distinction to preserve when adding a bound: `thread_incomplete` means *a bound the caller did not choose* cut the result short; a bound the caller asked for gets its own field.

Contract tests: `tests/search/test_thread_member_completeness.py` (with the synthetic multi-mailbox thread fixture in `tests/search/thread_fixtures.py`), `tests/search/test_search_scan_window_bound.py`, `tests/search/test_search_scan_ceiling_contract.py`, `tests/analytics/test_export_thread_completeness.py`, `tests/analytics/test_list_email_attachments_mailboxes.py`.

## Agent-facing selection

Workflow skills under [`../../skills/`](../../skills/) document **when** to call each tool (triage vs archive vs compose). After adding/removing tools, update relevant `plugin/skills/*/SKILL.md` frontmatter tool lists and run **`plugin-dev:skill-reviewer`**.

## Compose defaults (`compose/` package)

| Tool | Default | Notes |
|------|---------|-------|
| `compose_email` | `mode="draft"` | New standalone message only; refuses reply-like drafts unless `standalone_confirmed=True`. Bare `https://` URLs in HTML compose may become Mail link-preview cards in the open window; the tool does not create or verify those cards. |
| `reply_to_email` | `mode="draft"` (via `send=False`), `native_format=True` | `native_format=True` is the only supported path: it opens Mail's reply window (rich quote bar + logo signature) and types `reply_body` above the quote, which needs window focus + **Accessibility permission** or returns `REPLY_WINDOW_FOCUS_FAILED` (no draft saved). `native_format=False` returns `WINDOWLESS_FALLBACK_DISABLED` unless `allow_windowless_fallback=True` is explicitly passed (deliberate headless/CI only, never set by agents). Both verify exact Drafts id first with bounded fallback, expose `exact_id_verified` in JSON, and preserve known `draft_id` on verifier timeout/error. `from_address` is fail-closed: a refused `set sender` aborts before `save` and returns `REPLY_SENDER_OVERRIDE_FAILED` with nothing saved |
| `verify_draft` | read-only | Exact Drafts id snapshot for recipients, body, attachments, signatures, quoted original, and thread headers. Optional `resolve_source=True` (`resolve_recent_days=30.0` default) maps the reply's `In-Reply-To` header back to its source Inbox message via one bounded `search_emails(internet_message_id=...)` call, adding a `source` block (`resolved`/`not_found_in_window`/`no_in_reply_to_header`) |
| `verify_drafts` | read-only | Batch exact Drafts id snapshots with per-draft JSON payloads; accepts the same `resolve_source` / `resolve_recent_days` options as `verify_draft` |
| `forward_email` | `mode="draft"` | Same id-first rule as reply |
| `create_rich_email_draft` | saves + closes | Standalone only; same reply-like guard; `review_in_mail=True` for saved-open review |

### Compose package leaves (HTML / subject / attachments)

| Module | Role |
|--------|------|
| `compose/send.py` | `compose_email` + `_send_html_email` (NSPasteboard HTML paste path) |
| `compose/html_focus_scripts.py` | `focusComposeBody` — binds the marker-named compose window; AXFocus/click the editor; Tab only while Accessibility reports a header field, never in a WebKit body |
| `compose/html_subject_scripts.py` | Post-paste subject restore on the writable `outgoing message`, leftover-marker sweep, fail-closed follow-up, `HTML_COMPOSE_SUBJECT_RESTORE_FAILED` |
| `compose/standalone_draft_identity_scripts.py` | Pre-save Drafts snapshot, post-save bind by snapshot id or unique exact real subject, leftover-marker restore/delete (success path fails closed on a persisted marker) |
| `compose/attachment_draft_verification.py` | `markerDraftProof` strict readback for attachment-bearing standalone compose (subject must equal the real subject) |
| `compose/forward_attachment_scripts.py` | Forward restore-before-save, bounded proof/finalize (`__apple_mail_forward_{uuid}__` is pre-save only) |
| `compose/clipboard_scripts.py` | Snapshot/restore of the **whole** general pasteboard around the HTML `Cmd+V` — every item, every flavor, as detached `NSPasteboardItem`s. Single source for both the success path and the error handler, which restore in different modules. Reading only `NSPasteboardTypeString` silently discards an image, Finder file copy, or RTF-only clipboard, and a pasteboard has no undo |
| `compose/reply_script_helpers.py` | Pure mode/option/signature decisions made *before* a reply builder runs (`_ReplyModePlan`, `_reply_mode_plan`, `_reply_command_options`, `_reply_signature_script`, `_reply_extra_output_lines`). `reply.py` calls four of the five directly. No `run_applescript`, so not a patch seam |
| `compose/reply_typing_budget.py` | Pure timeout projection for the native typed reply, decided *before* any AppleScript is built: `_native_reply_projected_typing_seconds` (chunk posting **plus** the editor-drain poll) and `_native_reply_effective_timeout`, which floors an explicit `timeout` at the projection and refuses bodies past the 480 s projected cap with `REPLY_BODY_TYPING_BUDGET_EXCEEDED`. Derives its drain term from `constants.typing_settle_attempts` — the same helper the AppleScript budget uses — because if the two disagree, `AppleScriptTimeout` fires mid-drain and strands a typed compose window. No `run_applescript`, so not a patch seam |

### HTML compose transaction (`_send_html_email`)

Used when `body_html` is set, or when attachment-bearing `mode` is `draft`/`open` (plain `body` is escaped to minimal HTML). Order:

1. Create `outgoing message` with internal marker subject `__apple_mail_mcp_{uuid}__` (window title for `focusComposeBody` only; **must never remain visible**).
2. Focus the body via `html_compose_focus_handler()` — **never Tab into the WebKit body** (extra Tabs become first-line indent).
3. Paste HTML (`Cmd+V`).
4. Restore the caller's real subject on `newMsg` while it is still a writable outgoing message; verify the exact restored subject (match the operation's uuid marker token, not a bare prefix contains).
5. `save` (draft/open) or `send`.
6. Attachment path only: bind the saved row by pre-save snapshot id or a unique exact real subject (`draft_id_source` may be `operation_exact_subject`), run `markerDraftProof` (stored subject must equal the real subject, not the exact marker token), fail closed. **Never `set subject of markedDraft` after save** — Gmail Drafts `message.subject` is read-only once persisted.
7. Sweep leftover marker outgoing windows. After save, a unique leftover marker Drafts row is a leak: fail closed; do not delete-and-succeed. Error and Python follow-up paths still delete a unique leftover marker row. Follow-up never converts marker absence (`cleared` / `deleted` / `outgoing_ok`) into a success banner.

Errors: `COMPOSE_BODY_FOCUS_FAILED`, `HTML_COMPOSE_SUBJECT_RESTORE_FAILED`, `DRAFT_ATTACHMENT_PROOF_FAILED`. AppleScript throw/timeout runs `run_html_compose_subject_followup` cleanup before failing closed. Pre-restore / focus failure deletes the fixture outgoing (`delete newMsg`); it does not restore the real subject onto an empty draft.

Standalone HTML/attachment compose never matches Drafts rows by a **persisted** marker subject. Forward attachment drafts use the same restore-before-save order on the live outgoing message (`forward_attachment_scripts.py`); they never `set subject` of a saved Drafts `message`. Detail: [`docs/CLAUDE-conventions.md`](../../../docs/CLAUDE-conventions.md) § Compose and draft modes.

## Module size

Every tool surface is a split-by-domain package under the **600 LOC** budget. The `compose/`, `search/`, `inbox/`, `manage/`, `analytics/`, `smart_inbox/`, and `calendar/` packages are the worked examples; use their package `__init__.py` facades and focused leaves instead of reviving single-file monoliths. In particular, analytics splits export behavior between `export.py`, `export_helpers.py`, and pure `export_formatting.py`; calendar shares bounded engine and collection behavior through `calendar_core/` and `calendar/helpers.py`. CI warns on every run and **blocks growth** past the baseline in `tests/fixtures/module_line_budget/baseline.json`. See [`docs/CLAUDE-conventions.md`](../../../docs/CLAUDE-conventions.md) § Module line budget.

## Related

`../core/` (bridge package), `../server.py` (mcp + annotations), `../../tests/` (mock `run_applescript`), [`tasks/reference/phase-3-annotation-matrix.md`](../../../tasks/reference/phase-3-annotation-matrix.md).
