# CLAUDE conventions — deep reference

This file holds the durable engineering rules extracted from the repo root `CLAUDE.md`. Folder-level `CLAUDE.md` files link here instead of duplicating these sections.

**Related:** root [`CLAUDE.md`](../CLAUDE.md) (layout, commands, architecture overview) · [`plugin/docs/CLAUDE.md`](../plugin/docs/CLAUDE.md) (install surface) · [`tests/CLAUDE.md`](../tests/CLAUDE.md) (mock patterns)

---

## Tool-implementation conventions (locked in 3.1.5)

The anti-patterns below caused real production timeouts on a 24K-message Exchange inbox. Every new tool that touches Mail.app must follow these rules. Templates: the tool packages under [`plugin/apple_mail_mcp/tools/`](../plugin/apple_mail_mcp/tools/) — `search/`, `inbox/`, `smart_inbox/`, `manage/`, `analytics/`, `compose/`, `calendar/`. Each is a package of leaf modules re-exported through its `__init__.py`, not a single flat module; see [`plugin/apple_mail_mcp/tools/CLAUDE.md`](../plugin/apple_mail_mcp/tools/CLAUDE.md) for the module map.

### ScanWindow capability token (v3.2.0)

[`bounded_inbox_scan()`](../plugin/apple_mail_mcp/bounded_scan.py) is the **sole legitimate issuer** of `ScanWindow` capability tokens. Tools must never construct `ScanWindow` directly: call `bounded_inbox_scan()` or one of the safe builders (`build_bounded_message_scan`, `build_bounded_filtered_scan`, `build_whose_id_list`). Enforcement is at issue time, inside `bounded_scan.py`: the issuer and the builders raise `ToolError(code="INVALID_SCAN_WINDOW")` on an unbounded or out-of-policy request and `code: UNBOUNDED_SCAN_REQUIRED` when no window is bounded, so a tool cannot smuggle in an unbounded scan by hand-rolling a token. `ScanWindow` in [`backend/base.py`](../plugin/apple_mail_mcp/backend/base.py) carries the `_issued_by` provenance field, and the backend `Protocol` requires any future concrete backend to refuse a token that does not carry the issuer stamp; there is no concrete backend class in the tree today, so do not rely on a second check downstream of `bounded_scan.py`. `full_inbox_export` itself is disabled (`code: UNBOUNDED_EXPORT_DISABLED`, no AppleScript runs); it is no longer a working escape hatch for unbounded scans. Contract suite: `test_bounded_scan_contract`, `test_no_unbounded_whose`, `test_full_inbox_export`.

### ID-first mutations and scan opt-in gates (v3.7.0)

Destructive and bulk mutation tools default to **exact `message_ids`** from a prior `list_inbox_emails` or `search_emails` call. Subject and sender substring target selectors are deprecated on action tools and return `code: TARGET_SELECTOR_DEPRECATED`. Date-only or explicit bulk paths remain off by default and require an explicit escape hatch.

| Gate | Tools | Default | Opt-in kwarg | Structured error when blocked |
|------|-------|---------|--------------|-------------------------------|
| Filter scan | `move_email`, `update_email_status`, `manage_trash` | `message_ids` preferred; date/bulk filter path disabled | `allow_filter_scan=True` | `code: FILTER_SCAN_DISABLED` |
| Body scan | `search_emails` | `body_text` ignored unless opted in | `allow_body_scan=True` | `code: BODY_SCAN_DISABLED` |
| Deprecated target selector | `reply_to_email`, `forward_email`, `move_email`, `update_email_status`, `manage_trash`, `list_email_attachments`, `save_email_attachment`, `export_emails(scope="single_email")`, `manage_drafts(send/open/delete)` | Exact ids required | None | `code: TARGET_SELECTOR_DEPRECATED` |

**`FILTER_SCAN_DISABLED` contract** (`manage/helpers.py` → `_filter_scan_disabled_error`, called from `manage/move.py` and `manage/status.py`):

- Raised when a mutation tool is called with date-only or explicit bulk filter kwargs but **without** `message_ids` and **without** `allow_filter_scan=True`.
- `remediation.preferred`: collect ids via `search_emails` / `list_inbox_emails`, then call the mutation with `message_ids=[...]`.
- `remediation.escape_hatch`: `allow_filter_scan=True` (slow; timeout-prone on 24k+ inboxes; approved bulk/date campaigns only).
- When the escape hatch is used, responses are prefixed with `FILTER_SCAN_WARNING` so agents see the slow-path notice in plain text.

**`TARGET_SELECTOR_DEPRECATED` contract** (`backend/base.py` -> `target_selector_deprecated_error`):

- Raised before AppleScript runs when an action tool is called with `subject_keyword`, `subject_keywords`, `sender`, or `draft_subject` instead of exact ids.
- `remediation.discovery`: the read/search/list tool to call first.
- `remediation.exact_selector`: the id parameter required by the action tool.
- Keep these legacy kwargs in v3.x schemas for compatibility, but do not route them into target lookup.

**`BODY_SCAN_DISABLED` contract** (`search/records.py` → `_body_scan_disabled_error`, called from `search/emails.py`):

- Raised when `search_emails` is called with `body_text` set but `allow_body_scan=False` (the default).
- Body scans are O(N × message-size) on large mailboxes; pair `allow_body_scan=True` with a tight `date_from` / `recent_days` window.
- `remediation.preferred`: narrow with `subject_keyword`, `sender`, `date_from`, or `has_attachments` instead.

**ID path rules** (shared across `move_email`, `update_email_status`, `manage_trash`):

- When `message_ids` is provided, keyword/sender/date filters are **ignored** (fast `build_whose_id_list` path).
- Empty or all-non-numeric `message_ids` → plain-text validation error before AppleScript runs.
- Lists longer than `MAX_WHOSE_IDS` (50) → `code: WHOSE_ID_LIST_TOO_LARGE`; chunk with `bounded_scan.iter_id_chunks`.
- Filter paths still honor `recent_days` defaults and refuse unbounded scans with `UNBOUNDED_SCAN_REQUIRED` when no date window is set.

Agent workflow: **search/list -> collect numeric `message_id` -> mutate by ids**. `get_needs_response(output_format="json")` also returns numeric Apple Mail `message_id` for downstream reads, replies, moves, and status updates, plus `internet_message_id` for optional Sent-header correlation. JSON rows from the primary read surfaces carry `was_replied_to` and `mail_was_replied_to` (the same raw, read-only Mail property), nullable `has_sent_reply`, nullable composite `reply_state`, and nullable `has_draft`. `get_needs_response` preserves its legacy performance contract: it runs the Sent scan only with `check_already_replied=True`; otherwise `has_sent_reply` and non-native `reply_state` remain null. `reply_state=true` means either Mail's native property or an exact Sent In-Reply-To/References match is true; `false` requires a complete successful Sent nonmatch; `null` is unknown after a skipped, failed, truncated, or account-capped scan or a missing Message-ID. The top-level `sent_reply_scan` reports `status`, `scanned`, `total`, `truncated`, `errors`, and account-cap coverage; it describes MCP evidence only and does not imply Mail's UI reply arrow was changed. The parallel `draft_scan` reports Draft evidence. `has_draft=true` is trustworthy even from a truncated scan; `false` means the scan completed with no match; `null` means unknown. Prefer exact sender/domain/Message-ID filters when available.

### Centralized scan caps (`SCAN_BOUNDS`, v3.7.1)

All bounded AppleScript slices read caps from [`constants.py`](../plugin/apple_mail_mcp/constants.py) `SCAN_BOUNDS`. Edit one dict to retune every tool; `bounded_scan.compute_scan_upper_bound()` uses `SEARCH_BASE_CAP`, `SEARCH_WINDOW_CAP`, and `SEARCH_DAYS_SCALE`.

| Key | Value | Used by |
|-----|-------|---------|
| `SEARCH_BASE_CAP` | 40 | `search_emails` floor via `compute_scan_upper_bound` |
| `SEARCH_WINDOW_CAP` | 50 | `search_emails` ceiling before the hard ceiling below |
| `SEARCH_DAYS_SCALE` | 3 | Per-day scaling in `compute_scan_upper_bound` |
| `BODY_SEARCH_AUTO_CAP` | 25 | `search_emails` body scans without explicit `date_from` |
| `SEARCH_HARD_CEILING` | 50 | Hard ceiling (2026-07, AGENTIC-988): `search_emails` never binds more than this many messages per call, regardless of how the caps above scale |
| `INBOX_DEFAULT_CAP` / `INBOX_MAX_CAP` | 100 / 50 | `list_inbox_emails` unread/read filter slice |
| `INBOX_HARD_CEILING` | 50 | Hard ceiling (2026-07, AGENTIC-988): `list_inbox_emails` never binds more than this many messages per call, regardless of `max_emails`; also the per-mailbox cap for `get_statistics` |
| `INBOX_SHORT` / `INBOX_LONG` | 25 / 75 | `smart_inbox` per-mailbox ceilings |
| `TRASH_SCAN` | 100 | Trash listing branches |
| `DRAFT_LOOKUP` / `MESSAGE_LOOKUP` | 75 | Compose draft/reply lookup tails |
| `MAX_MAILBOXES_PER_SEARCH` | 20 | Multi-mailbox `search_emails` fan-out |
| `MAX_MAILBOXES_PER_SEARCH_ALL` | 10 | `search_emails(mailbox="All")` cap |

`get_statistics`: `days_back <= 7` → 10 mailboxes; else 20 mailboxes. Both branches cap each mailbox read at `INBOX_HARD_CEILING` (50): longer windows fan across more mailboxes rather than reading deeper into each one.

### Performance defaults

- **Recent-window default**: any tool that searches or lists takes `recent_days: float = 2.0` (48h). Tools must refuse unbounded scans (`recent_days=0` / `max_emails=0`) with `code: UNBOUNDED_SCAN_REQUIRED` plus a `remediation.fallback_tool` field. `full_inbox_export` is disabled (`code: UNBOUNDED_EXPORT_DISABLED`, no AppleScript runs) and is not a real fallback; narrow the window (`recent_days` / `date_from`) or page through bounded calls (`export_emails`, `list_inbox_emails`, `search_emails`) instead. Routine tests and skills must pass bounded `recent_days` / `max_emails`.
- **AppleScript-side caps, not Python-side slicing.** Avoid broad `every message of mailbox whose …` scans on remote mailboxes; Mail may materialize/fetch before filtering. Prefer direct newest-first slices (`messages 1 thru N of mailbox`) and filter inside the bounded loop.
- **`ignoring case … end ignoring`** for case-insensitive comparisons. Never call out to `do shell script "echo … | tr '[:upper:]' '[:lower:]'"` per message — the deprecated `LOWERCASE_HANDLER` was removed in 3.1.5 for that exact reason.
- **Push date filters unconditionally** into the `whose` clause when the caller provides `date_from`/`date_to`. Don't gate them on the presence of other filters.

### Forbidden AppleScript patterns (lint-enforced)

The patterns below are catalogued failure modes from real production crashes. **Each is enforced by `tests/core/test_no_unbounded_whose.py` — adding one of them to tool source breaks CI.** Use the named safe alternative.

| Forbidden | Why it fails | Use instead |
|-----------|--------------|-------------|
| `<sliceVar> whose <predicate>` where `<sliceVar>` is `candidateMessages` / `mailboxMessages` / `inboxMessages` / `draftMessages` / etc. — i.e. a variable bound via `messages 1 thru N of MB` followed by a `whose` clause. | AppleScript's `whose` over a list re-resolves the predicate against each ref's underlying physical folder. On Gmail that folder is `[Gmail]/All Mail`; Mail rejects the call with `Can't get {message id N of mailbox "[Gmail]/All Mail" ...} whose ...`. This is the 2026-05-27 Gmail crash. | `bounded_scan.build_bounded_filtered_scan(mailbox_var, scan_cap, target_max, condition_expr)` — emits a bounded slice plus an in-AppleScript `repeat ... if` loop by construction. Predicates of the form `<prop> of aMessage` work safely here. |
| `every message of MB whose <non-id-predicate>` (subject contains, sender contains, date received, read status, …) without a downstream slice. | Mail materializes the entire remote mailbox to evaluate the predicate. Hangs/times out on 24K-message Exchange inboxes and large Gmail folders. | Bind a bounded newest-first slice via `build_bounded_message_scan(mailbox_var, limit)`, then filter per-message in a `repeat with aMessage in candidateMessages` loop. For ID-only lookups use `build_whose_id_list(ids)`. |
| `every message of MB` with no `whose` (raw enumeration). | Same materialization cost as above, with no filter to limit work. | `messages 1 thru N of MB`. |
| `build_bounded_message_scan(..., whose_condition=...)`. | The helper raises `ToolError(code="UNSAFE_WHOSE_ON_LIST")` to prevent the slice-then-whose bug at construction time. | `build_bounded_filtered_scan(...)`. |
| `build_whose_id_list(ids)` with `len(ids) > MAX_WHOSE_IDS` (50). | Mail's AppleScript parser rejects or hangs on `id is X or id is Y or ...` predicates beyond ~200–500 OR-terms (varies by macOS); the helper raises `ToolError(code="WHOSE_ID_LIST_TOO_LARGE")` to prevent the crash. | `iter_id_chunks(ids)` plus a Python loop, one `osascript` call per chunk. |
| A **bare Mail property** in a condition that gets spliced into an explicit loop — e.g. building `subject contains "x"` and interpolating it into `repeat with aMessage in candidateMessages`. | A bare property reference resolves only where an enclosing `whose` clause supplies the implicit target. Inside a `repeat` loop there is no implicit target, so Mail raises **-1728 `Can't get subject.`** on every iteration; the loop's `try` swallows it and the tool returns a confident empty result with no errors. This is the v3.11.6 `search_emails` subject-filter bug (AGENTIC-2344) — worse than a crash, because the wrong answer looks authoritative. `osacompile` accepts the fragment, so compile checks do not catch it. | Bind the property to a loop-local first (`set messageSubject to subject of aMessage`) and test that variable. A condition string that is correct inside a `whose` clause is **not** reusable in a `repeat` loop; build the two forms separately or build only the bound form. Enforced by `tests/search/test_mail_search_tools.py::SubjectFilterUnboundReferenceTests`. |
| Building a pipe-delimited row (`messageSubject & "&#124;&#124;&#124;" & messageSender & ...`) without first running `sanitize_pipe_delimited_field` on each user-controlled field. | A subject legitimately containing the pipe trio shifts every parser field right; the corrupted `message_id` slot can then be passed to `manage_trash(action="delete_permanent")` and **delete the wrong message** — silent data loss. | `core.sanitize_pipe_delimited_field("messageSubject")` (and `"messageSender"`) before the row emit. The Python-side parser additionally validates `message_id.isdigit()` as a belt-and-suspenders backstop. |
| `do shell script "echo X \| tr '[:upper:]' '[:lower:]'"` per message. | Hundreds of subprocess spawns per scan; killed the 3.1.4 search path. | `ignoring case … end ignoring` AppleScript blocks. |
| Tool kwarg `allow_full_scan`. | Retired in v3.2.0 in favor of structured `UNBOUNDED_SCAN_REQUIRED` errors. | Refuse with a structured error; narrow the window (`recent_days` / `date_from`) or page through bounded calls (`export_emails`, `list_inbox_emails`, `search_emails`). Do not point callers at `full_inbox_export`, which is disabled. |

The lint test `tests/core/test_no_unbounded_whose.py` enforces the first four rules via source regex (with an empty `KNOWN_DANGEROUS_WHOSE` allowlist — add to it only with a tracking note and a follow-up PR planned). The builder-output contract `tests/core/test_bounded_scan_contract.py` asserts that the safe helpers emit the in-loop pattern, not the unsafe one. The Gmail-crash regression suite `tests/inbox/test_gmail_unread_crash_regression.py` simulates Mail's rejection to confirm the fix end-to-end.

### Account scoping

- **`DEFAULT_MAIL_ACCOUNT`**: every tool that takes an `account` parameter must (a) default it to `Optional[str] = None`, (b) at the top fall back to `_server.DEFAULT_MAIL_ACCOUNT` if `account is None`, (c) return a structured error if neither is set. Exception: `synchronize_account` requires `confirm_sync=True` and additionally requires `all_accounts=True` for all-account sync.
- **`all_accounts: bool = False`** is the explicit override for tools that need every configured account even when `DEFAULT_MAIL_ACCOUNT` is set.

### Async + per-account isolation

- Tools that fan out across accounts should be `async def` and dispatch each account via `asyncio.to_thread(run_applescript, …)`, one account at a time, in a plain loop (not `asyncio.gather`). `run_applescript` (`core/applescript.py`) serializes every `osascript` invocation behind two layers — the in-process `_MAIL_LOCK` and a user-private advisory file lock that extends single-flight across all installed plugin hosts for this macOS user — so concurrent dispatch only adds thread churn and does not run accounts in parallel. Wall time is the sum across accounts, not the slowest single account.
- Pair with per-account `AppleScriptTimeout` catch; append failing accounts to an `errors: list[str]` field and include structured error details when a tool can distinguish timeout from another Mail/App failure. Partial results > total failure.
- Single-account tools (`compose_email`, `move_email`, `manage_drafts`, `get_top_senders`, etc.) stay sync.
- **Never issue parallel/concurrent Mail tool calls from an agent.** Every installed plugin host for this macOS user queues behind the same single-flight pair; a caller that spends more than `_LOCK_WAIT_TIMEOUT` (300 s) waiting for its turn raises `AppleScriptTimeout` rather than queuing indefinitely. Calling multiple Apple Mail tools at once does not speed anything up and can time out. Call one Mail tool at a time and wait for its result.

### Timeout exposure

- Every modernized tool takes `timeout: Optional[int] = None` and threads it into `run_applescript(..., timeout=timeout)`. Wrap in `try/except core.AppleScriptTimeout` and return a structured error naming the account and elapsed budget.

### Escaping

- User-supplied strings reaching AppleScript **always** go through `core.escape_applescript()`. Missing it is script-injection and syntax-corruption regardless of string source.

### What NOT to do

- Don't add `subprocess.run(["osascript", …])` calls that bypass `run_applescript()`. Compose paths were migrated in 3.1.6; don't add new bypasses.
- Don't write `except: pass` or `except Exception: pass` — collect errors into a list the caller can see.
- Don't materialize a full mailbox into a Python list before filtering. `every message of …` without a `whose` cap is the bug.

### Orphan watcher

`__main__._start_orphan_watcher` works around [python-sdk#526](https://github.com/modelcontextprotocol/python-sdk/issues/526): when the MCP client exits without closing stdin, the server keeps polling Mail.app and silently relaunches Mail after the user quits it. The watcher captures the initial PPID and self-terminates with `os._exit(0)` when reparented. `get_ppid` and `exit_fn` are injectable for `tests/core/test_orphan_watcher.py` — keep those seams.

### Read-only enforcement

`--read-only` removes send tools from the registry; it does **not** branch inside tool implementations. `manage_drafts` stays registered but blocks the "send" action internally. New email-sending capabilities: extend `SEND_TOOLS` in `plugin/apple_mail_mcp/server.py`.

### Rich HTML drafts

`create_rich_email_draft` generates a multipart `.eml` on disk rather than injecting HTML into AppleScript's `content` property (Mail stores literal markup). It accepts explicit local attachment paths. `open_in_mail=False` creates a prepared EML only: it is not Mail-verified or ready to send. `open_in_mail=True` keeps that EML export and delegates to the supported focused HTML `compose_email` transaction, never `open -a Mail <file.eml>`. Attachment-bearing rich drafts reuse `compose_email`'s immediate strict recipient, subject, body, filename-multiset, count, and readable-size readback at one current Drafts locator. Editor or verification failure returns `RICH_DRAFT_COMPOSE_FAILED` with the EML path and never a ready claim.

### HTML compose subject and focus (`compose/send.py`, `html_focus_scripts.py`, `html_subject_scripts.py`)

`compose_email` uses `_send_html_email` when `body_html` is set or when attachment-bearing `mode` is `draft`/`open`. The flow is clipboard-backed HTML paste, not AppleScript `content` injection.

**Internal marker subject (`__apple_mail_mcp_{uuid}__`).** Mail names the compose window after the current subject, so the tool opens with a unique internal marker only as a pre-save bind token for `focusComposeBody`. Agents must never treat a visible `__apple_mail_mcp_` subject as acceptable output, and must not send mail that still carries that marker.

**Restore before first save.** After paste, the tool sets the caller's real subject on the writable `outgoing message`, verifies it, then saves once. Gmail and similar providers make saved Drafts `message.subject` read-only, so the implementation never restores subject by writing `set subject of markedDraft` post-save.

**Focus without body Tabs.** `html_focus_scripts.py` binds to the marker-named window, AXFocus/clicks the WebKit editor, and Tabs only while Accessibility reports a header field. Tab in an already-focused body inserts first-line indent; the handler returns `COMPOSE_BODY_FOCUS_FAILED` instead of Tab-looping into the body.

**Failure cleanup.** `COMPOSE_BODY_FOCUS_FAILED` (and any still-exact-marker fixture) deletes the outgoing message (`close … saving no` + `delete newMsg`); it does not restore the real subject onto an unsaved empty compose. Other error and Python follow-up paths restore leftover writable outgoing marker windows or delete a unique leftover marker Drafts row. After a successful save, a leftover marker Drafts row is a leak: fail closed, do not delete-and-succeed. Follow-up `cleared` / `deleted` / `outgoing_ok` only means no leftover marker remains; never convert that into `Email saved as draft (HTML)`. Unclean exits return `HTML_COMPOSE_SUBJECT_RESTORE_FAILED`.

### Compose and draft modes

`compose_email`, `reply_to_email`, and `forward_email` share a `mode` parameter:

| Mode | Behavior | When agents should use it |
|------|----------|---------------------------|
| `draft` (default) | Save to Drafts quietly; do not leave fresh compose windows open. By default `reply_to_email` runs the native path (`native_format=True`): Mail's `reply ... with opening window` renders its own colored quote bar and default logo signature, and `reply_body` is typed in above the quote via System Events keystrokes in small focus-guarded chunks (never one keystroke of the whole body, and never the clipboard); a single keystroke of the whole body was the AGENTIC-1214 truncation/ALL-CAPS bug. Chunking alone does not prevent truncation: `keystroke` returns when events are posted, not when WebKit has processed them, so after the last chunk the script polls the compose editor's own text until the body has landed before it saves. That drain budget scales with body length (`TYPING_SETTLE_*` in `compose/constants.py`), and the caller-facing AppleScript timeout is projected from the same constants in `compose/reply_typing_budget.py` so it cannot fire mid-drain. Chunk size is a proxy for backlog depth, not a safety knob — do not "fix" a truncated tail by shrinking `TYPING_CHUNK_SIZE`. `native_format=True` is the only supported path for normal agent use. `native_format=False` is gated: it returns `WINDOWLESS_FALLBACK_DISABLED` unless the caller explicitly passes `allow_windowless_fallback=True`, and that windowless object-model path (plain-text quote, no Accessibility) is reserved for deliberate headless/CI runs only, never set by agents. An RFC-backed reply identity, with `Message-ID` and source-linked `In-Reply-To`, can authorize later guarded cleanup after revalidation. If iCloud has no outgoing RFC ID, exactly one newly persisted numeric Drafts row is transaction-scoped proof for this verification only, never cleanup. Cap limits, indexing delay, ambiguity, or identity drift fail closed; newest-Drafts fallback is diagnostic only. Reply JSON exposes both `exact_id_verified` and `draft_id_source`. | Native drafting and background agent work under `--draft-safe` |
| `open` | Save first, then leave the compose window open for human review | User wants each draft to pop up in Mail (e.g. review 10 replies in sequence) |
| `send` | Send immediately. `reply_to_email(output_format="json", mode="send")` is rejected before mutation. Any attachment-bearing `compose_email`, `reply_to_email`, or `forward_email` call rejects direct send before mutation: draft/open, verification, and human review come first. Standalone HTML/attachment compose restores the real subject before save, then proves the saved row by snapshot identity or exact real subject with no reusable locator. Forward attachment drafts restore `fwdSubject` on the live outgoing message before the first save, then prove the saved row by snapshot identity or unique exact real subject; re-resolve a fresh exact id only when a later lifecycle action is authorized. | Explicit user authorization only; blocked when `DRAFT_SAFE` or `READ_ONLY` |

**Reply/forward targeting:** pass `message_id` from `search_emails`, `list_inbox_emails`, or `get_email_by_id`. `subject_keyword` is schema-compatible only and returns `TARGET_SELECTOR_DEPRECATED`; run discovery first. `reply_to_email` defaults to the native reply window (`native_format=True`), the only supported path for normal agent use, so drafts keep Mail's rich quote bar and logo signature; this needs the Mail window to take focus and **Accessibility permission for the host process**. Requested attachments are added after typing and before save. Native attachment sends return `REPLY_SEND_REQUIRES_VERIFIED_DRAFT` before Mail mutation. `from_address` is applied fail-closed: if Mail refuses `set sender of replyMessage`, the reply aborts before `save`, the compose window is closed with `saving no`, the temp artifact is removed, and the tool returns `REPLY_SENDER_OVERRIDE_FAILED` rather than saving a draft from the account default identity. A sender that is silently not applied rather than refused is not detectable without a `From` readback and is not currently caught. The saved body must match above the source-attributed quote and requested attachments must be readable. An RFC-backed Drafts identity is revalidated before the one possible delete-and-retype retry. If iCloud has not assigned an outgoing RFC ID, one bounded new numeric Drafts row can prove only that operation's verification; its `draft_id_source="transaction_scoped_numeric_identity"` must never authorize later delete, send, open, or retype. A same-subject fallback is diagnostic only. `body_html` is ignored on replies for compatibility. Do not use standalone draft creators (`compose_email`, `create_rich_email_draft`, or `manage_drafts(action="create")`) to answer existing mail: they create standalone messages with no quoted original thread. These paths refuse reply-like `Re:` / `Fwd:` subjects or quoted-thread bodies unless the caller explicitly passes `standalone_confirmed=True`. `manage_drafts(action="create", in_reply_to=...)` additionally refuses up front with `CREATE_CANNOT_THREAD`: create has no header property to set In-Reply-To/References, so `in_reply_to` is honored only by `action="find"`.

**Thread discovery:** pass `message_id` and explicit `mailboxes=[...]` to `get_email_thread` whenever a prior list/search result exposed the id. The message-id path reads Mail's dictionary-backed Message-ID, In-Reply-To, and References headers first, then uses subject fallback only when headers are unavailable or no header-linked messages are found. Use `output_format="json"` to collect exact message ids and header metadata, and `include_preview=False` when the workflow only needs handles. Check `selection_strategy` and `subject_fallback_used` before treating a reconstructed thread as header-confirmed.

**Rich `.eml` drafts:** Mail's `open -a Mail <file.eml>` import is not a reliable way to create a scriptable outgoing compose object, so `create_rich_email_draft` never uses it for drafting. The tool writes the EML export, then for nonblank `open_in_mail=True` requests delegates to the focused HTML `compose_email` transaction. It preserves the EML separately from that transaction's immediate strict readback; any numeric Drafts ID is a best-effort locator. Blank subjects stay EML-only until a nonblank subject exists.

**Universal attachment contract:** attachment-bearing `compose_email`, `reply_to_email`, and `forward_email` refuse direct send. Standalone `compose_email` (HTML or attachment draft/open) order: paste → restore real subject on the outgoing message → save → bind the saved row by pre-save snapshot or unique exact real subject → `markerDraftProof` → optional numeric locator (`draft_id_source` may be `operation_exact_subject`). The internal `__apple_mail_mcp_{uuid}__` window marker must never persist as the visible subject. Reply verification uses its own bounded contract. Forward attachment drafts restore the real subject on the live outgoing message before save, then prove the saved row by snapshot identity or unique exact real subject (`operation_exact_subject`); that proof has no reusable locator after iCloud ID rewrite. Reply and forward verification never certify a same-subject fallback after an attachment failure. `forward_email` attaches only explicit caller-selected paths and never copies a source message's attachments implicitly. `create_rich_email_draft` embeds explicit paths in its EML and, when its supported Mail path is requested, delegates those paths to `compose_email` in draft/open mode. The EML alone is prepared content, not a Mail-verified draft; compose or verification failures return `RICH_DRAFT_COMPOSE_FAILED`.

**Draft lifecycle targeting:** `manage_drafts(action="list")` returns each draft's id. For `send`, `open`, or `delete`, pass `draft_id`; `draft_subject` is schema-compatible only and returns `TARGET_SELECTOR_DEPRECATED`. Guarded deletion is optional, but when used it requires `expected_in_reply_to`, `expected_subject`, and `expected_to` together from a freshly resolved draft; the tool re-reads all three immediately before deletion.

**Attachment targeting:** pass `message_ids` to `list_email_attachments`; `subject_keyword` is schema-compatible only and returns `TARGET_SELECTOR_DEPRECATED`. Use `output_format="json"` to get per-row `message_id`, `attachment_index`, filename, and size. Prefer `save_email_attachment(message_ids=[one_id], attachment_index=N, ...)` for exact saves. `attachment_name` remains compatible, but duplicate filename matches return `AMBIGUOUS_ATTACHMENT_SELECTOR` and instruct callers to retry with `attachment_index`.

**Agent guidance:** `plugin/skills/email-drafting/` documents the quiet-default vs saved-open review split, HTML compose subject restore (`__apple_mail_mcp_` is pre-save window binding only), native reply (`native_format=True`), Accessibility requirements, and `REPLY_WINDOW_FOCUS_FAILED` / `HTML_COMPOSE_SUBJECT_RESTORE_FAILED` recovery. `apple-mail-operator` covers bootstrap and navigation and hands off reply drafting to `email-drafting`. Sync `apple-mail-mcpb/manifest.json` tool descriptions when compose behavior changes.

---

## Versioning

Version is duplicated across **seven** files — bump all together when releasing. `pyproject.toml` is the source of truth; the other six are published install or discovery surfaces, and [`_public_version_checks()`](../tools/validators/validate_manifests.py) in `validate_manifests.py` fails the gate on any drift from it. Top-level Claude marketplace `metadata.version` (1.0.0) describes the marketplace manifest itself; don't touch it. The Codex marketplace at `.agents/plugins/marketplace.json` does not carry a release version; it points at `./plugin`. See [`.claude-plugin/CLAUDE.md`](../.claude-plugin/CLAUDE.md).

| File | Field |
|------|-------|
| `pyproject.toml` | `[project].version` (source of truth) |
| `plugin/.claude-plugin/plugin.json` | `version` |
| `plugin/.codex-plugin/plugin.json` | `version` |
| `plugin/.cursor-plugin/plugin.json` | `version` |
| `.claude-plugin/marketplace.json` | `plugins[0].version` |
| `server.json` | `version` and `packages[0].version` |
| `apple-mail-mcpb/manifest.json` | `version` |

Tool-count claims drift. Description fields in Claude/Codex `plugin.json`, marketplace manifests, and `apple-mail-mcpb/manifest.json` must match a recursive count of `^@mcp.tool` in `plugin/apple_mail_mcp/tools/` (including package subfolders such as `compose/`). The mcpb manifest also embeds the full `tools[]` array — both count and names must match code. Run [`tools/validators/validate_manifests.py`](../tools/validators/validate_manifests.py) or `plugin-dev:plugin-validator` after add/remove; run `bash tools/gates/dev-check.sh release` before shipping manifest, package, or artifact changes.

---

## Distribution channels — five install surfaces, one source

The repo ships from **one source tree** to **five install surfaces**. Claude Desktop artifacts rebuild in one shot via [`tools/gates/build-artifacts.sh`](../tools/gates/build-artifacts.sh); Claude Code, Codex, and Cursor plugin installs share the checked-in `plugin/` runtime but retain distinct adapters. The validator and local tests enforce static parity. Local Cursor Agent acceptance has passed; Cursor marketplace/UI admission remains a separate distribution check.

The marketplace identity boundary is machine-readable in
[`tools/marketplace_identity.json`](../tools/marketplace_identity.json). Agentic
Assets users install the promoted Apple Mail payload from
`Agentic-Assets/Agentic-Assets-Marketplace` with selector
`apple-mail@agentic-assets`. Promotion copies only `plugin/` from an immutable,
allowlisted signed tag into `plugins/apple-mail`; the marketplace repository
owns promotion policy, evidence, and attestations. Fixes are made here and
promoted again, never edited in the promoted payload.

This repository's root Claude and Codex catalogs remain standalone
development/public compatibility surfaces named `apple-mail-mcp`, with
selector `apple-mail@apple-mail-mcp`. Do not rename them to `agentic-assets`.
The Cursor adapter below has local acceptance only; it is not proof of Cursor
marketplace/UI admission.

| Artifact | Target | How users install |
|----------|--------|-------------------|
| `apple-mail-plugin.zip` | Claude Code standalone compatibility marketplace | `claude plugin marketplace add Agentic-Assets/apple-mail-mcp --scope user`, `claude plugin marketplace update apple-mail-mcp`, then `claude plugin install apple-mail@apple-mail-mcp --scope user` (uses `.claude-plugin/marketplace.json`) |
| `apple-mail.plugin` | Claude Desktop **Cowork** | Customize → Add plugin → **Upload plugin**. The Cowork UI accepts the `.plugin` extension; without it the upload silently fails. |
| `apple-mail-mcp-v{VERSION}.mcpb` | Claude Desktop **chat extension** | "Add Custom Plugin" / "Install from file" (MCPB bundle, `manifest_version` 0.3 with `compatibility` and `privacy_policies`, built with `mcpb pack`) |
| `.agents/plugins/marketplace.json` + `plugin/.codex-plugin/plugin.json` | Codex Desktop/CLI standalone compatibility marketplace | `codex plugin marketplace add https://github.com/Agentic-Assets/apple-mail-mcp.git` then `codex plugin add apple-mail@apple-mail-mcp`; local checkouts are maintainer/offline only |
| `plugin/.cursor-plugin/plugin.json` + `plugin/mcp.json` | Cursor plugin adapter | Cursor resolves `/bin/bash ${CURSOR_PLUGIN_ROOT}/start_mcp.sh --draft-safe`; local Cursor Agent acceptance passed, while marketplace/UI admission remains separate |

**`.zip` and `.plugin` must be byte-identical** — `tools/gates/build-artifacts.sh` copies the canonical zip to the `.plugin` name so they cannot drift. `tools/validators/validate_manifests.py::_check_plugin_file_parity` rejects any divergence and `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1` promotes a missing `.plugin` to a hard error. Regression coverage: `tests/infra/test_validate_manifests.py::test_plugin_file_parity_*`.

**Never** ship a release where any required artifact or manifest is missing or stale. Real installer failures we have hit and now guard against:

- MCPB built with raw `zip -r .` emitting zero-byte directory entries → Claude Desktop installer aborts with `ENOENT`. Build with `mcpb pack` or `zip -X -D`. Guard: `_check_no_directory_entries`.
- MCPB manifest on the legacy `dxt_version` key, or missing `privacy_policies` / `compatibility.platforms` → Claude Desktop extension-directory submission is rejected. Guard: `_check_mcpb_directory_contract`; detail in `apple-mail-mcpb/CLAUDE.md`.
- Plugin zip wrapping files under `plugin/` prefix → Cowork rejects with "No manifest found". Build from inside `plugin/`. Guard: `test_plugin_zip_has_manifest_at_root_not_nested`.
- `.plugin` extension missing → Cowork "Upload plugin" rejects the `.zip` silently. Guard: `_check_plugin_file_parity`.

---

## Marketplace vs plugin.json — component ownership

Claude Code rejects the install with *"conflicting manifests: both plugin.json and marketplace entry specify components"* when both `.claude-plugin/marketplace.json plugins[0]` and `plugin/.claude-plugin/plugin.json` declare any of `commands`, `agents`, `skills`, `hooks`, `mcpServers` while `strict` is not `true` on the marketplace entry.

Rule for this repo: **all component declarations live in `plugin/.claude-plugin/plugin.json`** (today: only `mcpServers`). The marketplace entry is metadata-only and uses `strict: true`, matching Claude's strict default and the plugin manifest's component declaration. Skills auto-discover from `plugin/skills/<name>/SKILL.md` — do not re-list them in marketplace.json.

The guard lives in `tools/validators/validate_manifests.py::_check_marketplace_contract`; regression tests `test_marketplace_contract_rejects_dual_component_declarations` / `..._allows_dual_components_when_strict_true` lock it in. Also see [`.claude-plugin/CLAUDE.md`](../.claude-plugin/CLAUDE.md) § "Components live in plugin.json".

---

## Plugin-dev agents

This repo **is** a Claude Code plugin. For plugin shell, MCP wiring, skills, agents, commands, hooks, or manifests, defer to `plugin-dev:*` agents when the host exposes them; they override memory about plugin authoring. If those experts are unavailable, say so in the handoff and run the local validation gates listed below:

| Agent / skill | When |
|---------------|------|
| **`plugin-dev:plugin-validator`** | After any change to `plugin.json`, `marketplace.json`, `.mcp.json`, command/skill/agent frontmatter, or directory layout. Blocking before merge when available; otherwise run `bash tools/gates/dev-check.sh release`. |
| **`plugin-dev:skill-reviewer`** | After creating or editing any skill under `plugin/skills/`. Focus on `description` / frontmatter — that drives triggering. |
| **`plugin-dev:agent-creator`** | Adding a new agent. Don't hand-author frontmatter from memory. |
| **`plugin-dev:*` skills** | Invoke the matching skill *before* designing (`mcp-integration`, `skill-development`, `command-development`, etc.). |

Server-side AppleScript/FastMCP work is plain Python — use general agents, not plugin-dev.

---

## Skill authoring convention

Every skill under `plugin/skills/` follows the same shape so siblings trigger crisply without competing:

- **Directory name == frontmatter `name`.** `email-management/` ↔ `name: email-management`. No `-expert` suffix.
- **`description`**: third-person, scenario-rich, ends with "Do NOT use for X (see \<sibling\>)". Include 4–6 quoted trigger phrases and name 3–5 central MCP tools.
- **Body**: imperative/infinitive ("Start with `get_inbox_overview()`"). Addresses the executing model, not a human reader.
- **`SKILL.md`**: 1,500–2,000 words. Detail → `references/`, code → `examples/`, scripts → `scripts/`. Link in "Additional Resources".
- **Packaged skill paths:** Agents only see files inside each `plugin/skills/<name>/` directory. Do not link to `../references/` or other paths outside the skill folder. Canonical shared refs live in `plugin/skills/references/`; run `python3 tools/validators/sync_skill_references.py` after edits to refresh per-skill `references/` copies. `tests/infra/test_packaged_skill_paths.py` enforces both rules.
- **Top of body**: (1) purpose, (2) when-to-use / when-NOT-to-use, (3) performance defaults, (4) sibling decision tree, (5) red-flag table for destructive ops.
- **No persona openers** ("You are an expert…").
- **Verify** with `plugin-dev:skill-reviewer` before merge when available. If unavailable, run manifest/release validation and note the missing expert pass. Template: `plugin/skills/email-management/SKILL.md`.

### Skills only — no new slash commands

Entry points ship as skills only. Do not restore `plugin/commands/`; the old `/email-management` slash command was retired because hosts can surface commands beside skills and confuse routing. Release validation fails if the legacy commands directory reappears.

| Skill directory | Primary intent |
|-----------------|----------------|
| `apple-mail-operator` | MCP bootstrap, navigation, troubleshooting |
| `inbox-triage` | Fast read-first daily scan |
| `email-management` | Umbrella Inbox Zero / sustained habits |
| `mailbox-taxonomy` | Folder design + noise diagnosis |
| `email-archive-cleanup` | Staged moves, exports, capped trash |
| `mail-rules-advisor` | Filter/rule prose only (no MCP rule API) |
| `email-drafting` | Compose / reply / forward / rich drafts |
| `email-style-profile` | Voice contract before drafting |
| `email-attachments` | List + save attachments |
| `calendar-operator` | Bounded calendar reads + safe event CRUD |
| `meeting-scheduler` | Find-slot scheduling + invitation limits |

**Routing cheat sheet:** [`plugin/skills/CLAUDE.md`](../plugin/skills/CLAUDE.md). **Narrow skills** may stay shorter than the umbrella template if they include triggers, sibling matrix, performance notes, and destructive red lines. **Umbrella template:** `plugin/skills/email-management/SKILL.md` (also has `references/`, `examples/`, `templates/`).

After adding or editing any skill: run **`plugin-dev:skill-reviewer`** when available. After manifest, package, artifact, or skill-count marketing copy changes: run **`plugin-dev:plugin-validator`** when available plus `bash tools/gates/dev-check.sh release`.

---

## Committed-identity gate (public repo)

Root [`AGENTS.md`](../AGENTS.md) § This repo is PUBLIC states the rule; [`tools/validators/validate_no_committed_identity.py`](../tools/validators/validate_no_committed_identity.py) enforces it. It scans every tracked text file from `git ls-files` and **exits 1** on an email address at a non-placeholder domain, an absolute `/Users/<name>/...` path, or an uppercase account UUID. It runs **first** in `dev-check.sh` `default` and `release`: it is the cheapest step, and it is the only gate here whose miss cannot be undone, because a push to a public remote is not retractable by a later force-push.

Two design points that matter when you touch it:

- **Allowlist vs ratchet is not a judgment call.** A domain joins `SYNTHETIC_TEST_DOMAINS` only when an address there is *inherently* not identity, so no allowlist rot is possible (`bar.com`, `vendor.com`, the single-letter `Message-ID` stubs). Every domain where an address *is* identity — the company domain, any founder's personal domain, any `.edu`, every real vendor or mail provider — goes in the per-file `KNOWN_IDENTITY_HITS` ratchet instead, so existing published text stays legal while the next occurrence fails.
- **The ratchet only tightens.** Counts are keyed by path, never by line number. Lowering one is always valid; raising one is not — redact instead. A separate staleness test in [`tests/infra/test_no_committed_identity.py`](../tests/infra/test_no_committed_identity.py) fails when an entry claims more hits than the tree actually contains, so a redaction that is not accompanied by a baseline decrement is caught rather than silently banked.

Violation output names the file, line, and rule but never echoes the matched value: a gate that prints the leak into terminal scrollback and CI logs has made a second copy of it. Detail: [`tools/CLAUDE.md`](../tools/CLAUDE.md) § `validate_no_committed_identity`.

---

## Module line budget (600 LOC)

Keep production modules focused and splittable. The repo enforces a **600 physical-line** soft target on `plugin/apple_mail_mcp/` and `tools/` (test modules under `tests/` are not budgeted; aligned with agent guidance and the `python-project-structure` skill's 300–500 line split heuristic).

### Automated gates

| Layer | Behavior |
|-------|----------|
| **`tools/validators/check_module_line_budget.py`** | Warn-only CLI; lists modules over budget |
| **`tests/infra/test_module_line_budget.py`** | Pytest warning on oversize production modules; **hard fail** on baseline regression |
| **`validate_manifests.py`** | Same regression check during manifest validation; prints WARN lines |
| **`dev-check.sh`** | Prints budget report before pytest (default, release, live, surface, all) |
| **Local pre-push** | GitHub-hosted Actions are **disabled by repo policy** (`.github/workflows-disabled/`), so there is no hosted CI step. The checked-in `.githooks/pre-push` gate plus `tools/gates/source-release-gate.sh` are the CI-equivalent blockers |
| **Pre-commit** | Via `dev-check.sh default` |

### Baseline fixture

[`tests/fixtures/module_line_budget/baseline.json`](../tests/fixtures/module_line_budget/baseline.json) maps tracked production module paths to their last-known line counts. After v3.9.1 decomposition the `modules` object is **empty** (no file exceeds 600 LOC). CI still **fails** when a tracked file grows past its baseline count, so any future oversize module must either be split first or refresh the baseline intentionally after a measured shrink:

```bash
python3 tools/validators/check_module_line_budget.py --write-baseline tests/fixtures/module_line_budget/baseline.json
```

Do not refresh the baseline merely to silence growth from new features; split helpers into focused modules first (`plugin/apple_mail_mcp/tools/CLAUDE.md` module map). Run **`code-simplifier:code-simplifier`** after splits.

### Status

No production module in `plugin/apple_mail_mcp/` or `tools/` exceeds 600 LOC today. The warn report and regression gate still run on every CI pass to prevent sprawl. Historical decomposition context: [`tasks/active/v4-performance-consolidation-2026-05-27/learnings-and-parking-lot.md`](../tasks/active/v4-performance-consolidation-2026-05-27/learnings-and-parking-lot.md).

Detail: [`tools/CLAUDE.md`](../tools/CLAUDE.md) § `check_module_line_budget.py` · [`tests/CLAUDE.md`](../tests/CLAUDE.md) § Module line budget.

---

## Platform constraints

- **macOS only.** Tests mock `subprocess.run` — see `tests/cross_cutting/test_modernization_3_1_5.py` and `tests/search/test_mail_search_tools.py` (patch with `side_effect` capturing script via `kwargs["input"]`).
- **Python runtime split.** The PyPI/server package supports Python 3.10+ per `pyproject.toml`. The self-contained Claude, Codex, Cursor, and MCPB payload currently requires Apple Silicon (macOS arm64) with Python 3.13 because `start_mcp.sh` installs only its bundled platform-specific wheelhouse. The MCPB embedded README must stay in sync.
- **Permissions**: Mail.app must be configured; Automation + Mail Data Access granted to the terminal/IDE. Surface clear errors; don't retry blindly.
- **Async**: `asyncio.to_thread` for `run_applescript` in worker threads. Don't make `run_applescript` itself async.
