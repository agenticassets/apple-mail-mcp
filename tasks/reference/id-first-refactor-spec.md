# ID-first refactor spec

> **Note:** Subject/sender target selectors on action tools now hard-fail with `TARGET_SELECTOR_DEPRECATED` before any scan. `allow_filter_scan=True` applies to date/bulk paths only. Current policy: [`docs/CLAUDE-conventions.md`](../../docs/CLAUDE-conventions.md) and [`../archive/2026-06/shipped/agent-guidance-audit/agent-guidance-audit-2026-06-30.md`](../archive/2026-06/shipped/agent-guidance-audit/agent-guidance-audit-2026-06-30.md).

**Status:** Phase 1 mutation ID paths **shipped in v3.7.0**. Filter-based mutation paths are gated behind `allow_filter_scan=True` (default `False`); see [`docs/CLAUDE-conventions.md`](../../docs/CLAUDE-conventions.md) § ID-first mutations and scan opt-in gates.

## Current state (audit) — updated 2026-06-09

| Tool | Accepts message_ids? | Guardrails (recent_days / cap / filter gate)? |
|---|---|---|
| `update_email_status` | **YES ✓ shipped** | ID path: `build_whose_id_list`, cap 50 ids; filter path: `recent_days=2.0`, requires `allow_filter_scan=True` |
| `move_email` | **YES ✓ shipped** | ID path: fast, ignores filters; filter path: `recent_days=2.0`, `allow_filter_scan=True`, `UNBOUNDED_SCAN_REQUIRED` without date window |
| `manage_trash` (move_to_trash) | **YES ✓ shipped** | ID path: fast; filter path: `allow_filter_scan=True`, `recent_days` on filter branch |
| `manage_trash` (delete_permanent) | **YES ✓ shipped** | ID path: fast; filter path: `allow_filter_scan=True` |
| `get_email_by_id` | YES (singular) ✓ | N/A (single ID lookup) |
| `get_email_thread` | **YES ✓ shipped** (`message_id`) | `recent_days=30` default; subject_keyword fallback capped |
| `search_emails` | N/A (returns ids) | `recent_days=2.0`, output cap; `body_text` requires `allow_body_scan=True` (`BODY_SCAN_DISABLED`) |
| `list_email_attachments` | NO | keyword-only; backlog: add `recent_days` default |
| `get_awaiting_reply` | NO | backlog: `recent_days` default |
| `get_needs_response` | NO | backlog: `recent_days` default |
| `get_top_senders` | NO | backlog: `recent_days` default |
| `get_statistics` | NO | backlog: `recent_days` default |
| `list_inbox_emails` | N/A | list operation, returns `message_id` for downstream mutations |
| `inbox_dashboard` | N/A | list operation, not per-message |

### `allow_filter_scan` gate (v3.7.0)

Applies to **`move_email`**, **`update_email_status`**, and **`manage_trash`** when called **without** `message_ids` and with filter kwargs (`subject_keyword`, `sender`, `apply_to_all`, etc.):

| Caller passes | Result |
|---|---|
| `message_ids=[...]` | Fast ID path; filters ignored; no gate |
| Filters only, `allow_filter_scan=False` (default) | Structured `FILTER_SCAN_DISABLED` with remediation to collect ids first |
| Filters + `allow_filter_scan=True` | Slow filter path; response prefixed with `FILTER_SCAN_WARNING`; still bounded by `recent_days` / `older_than_days` |

Contract tests: `tests/test_phase_2_scan_hardening.py` (mutation gate), `tests/test_mail_search_tools.py` (move/trash integration).

## Template patterns

### `update_email_status` (reference impl)
**File:** `plugin/apple_mail_mcp/tools/manage.py:361–584`

Pattern: When `message_ids` is provided (line 427–490):
1. Call `normalize_message_ids(message_ids)` → List[str] of numeric IDs
2. Build condition via `equals_any_numeric_condition("id", normalized_ids)` (defined in core.py:132–138)
   - Returns: `"(id is 123 or id is 456 or id is 789)"`
3. Use in whose-clause: `every message of targetMailbox whose {id_condition}`
4. **Key**: ID path skips all keyword filters; keyword filters are silently ignored with a note in response

Fall-back to filter-based path when `message_ids is None`.

### `get_email_by_id` (reference impl)
**File:** `plugin/apple_mail_mcp/tools/search.py:845–998`

- Single ID lookup, no scanning
- Line 937: `set targetMessages to every message of targetMailbox whose id is {numeric_id}`
- Uses `build_mailbox_ref(mailbox, var_name="targetMailbox")` for robust mailbox resolution (core.py:270–318)
- Returns structured record via `_parse_search_records()` (search.py:63–95)

### `normalize_message_ids` contract
**File:** `plugin/apple_mail_mcp/core.py:118–129`

Input: `Optional[List[Any]]` (strings, ints, mixed)
Output: `List[str]` of unique numeric IDs in order, empty list if None/empty input
Validation: Skips non-digit values, deduplicates, returns strings

### `build_mailbox_ref` contract
**File:** `plugin/apple_mail_mcp/core.py:270–318`

Input: `mailbox: str, account_var: str = "targetAccount", var_name: str = "targetMailbox"`
Output: AppleScript snippet that resolves mailbox to a variable

Behavior:
- Handles nested paths via "/" separator: "Projects/2024" → nested mailbox references
- "INBOX" triggers localized inbox name fallback (INBOX_NAMES list, handles French/German/Japanese)
- Returns AppleScript `try`/`on error` block for robust resolution
- Raises error if mailbox not found

## Spec: move_email — **SHIPPED v3.7.0**

**File:** `plugin/apple_mail_mcp/tools/manage.py` (`move_email`, `_move_email_by_message_ids`)

### New signature
```python
def move_email(
    account: Optional[str] = None,
    to_mailbox: str = "",
    message_ids: Optional[List[str]] = None,  # NEW
    subject_keyword: Optional[str] = None,
    from_mailbox: str = "INBOX",
    max_moves: int = 50,
    subject_keywords: Optional[List[str]] = None,
    sender: Optional[str] = None,
    older_than_days: Optional[int] = None,
    dry_run: bool = False,
    only_read: bool = False,
    timeout: Optional[int] = None,
) -> str:
```

### Behavior matrix

| Scenario | Action |
|---|---|
| `message_ids` provided (not empty) | Use exact ID path; ignore all keyword/sender/date filters |
| `message_ids` provided but empty | Return error "message_ids must contain one or more numeric Mail ids" |
| `message_ids` not provided + keyword/sender/date provided | Use filter-based path (current behavior, add 48h recent_days default) |
| `message_ids` not provided + no filters + `older_than_days` missing | Return error requiring at least one filter |
| dry_run=True + message_ids | Report which IDs would be moved; do NOT enumerate mailbox |

### AppleScript pattern (pseudo)

#### ID-based path (fast, no scanning):
```applescript
set targetMessages to every message of sourceMailbox whose id is in {id1, id2, ...}
if (count of targetMessages) > max_moves then
    set targetMessages to items 1 thru max_moves of targetMessages
end if
repeat with aMessage in targetMessages
    move aMessage to destMailbox
    -- log details
end repeat
```

#### Filter-based path (current, add recent_days):
```applescript
set matchingMessages to every message of sourceMailbox whose (subject condition AND sender condition AND date received < cutoff)
-- cap to max_moves
-- move each
```

### Dry-run handling

- **With message_ids**: Use the ID path but do NOT call `move` action. Query `every message whose id is in {…}` and format output listing which messages would move.
- **Without message_ids**: Use existing `_search_mail_records()` helper which already does dry-run preview.
- **Critical**: Never enumerate full mailbox in dry-run; use ID list or filter to stay fast.

### Helper function
Add to `core.py`:
```python
def build_id_list_condition(field_name: str, ids: List[str]) -> str:
    """Return AppleScript condition for numeric ID list matching.
    
    Returns: "(id is 123 or id is 456 or id is 789)"
    """
    if not ids:
        return "false"
    parts = [f"{field_name} is {id_}" for id_ in ids]
    return "(" + " or ".join(parts) + ")"
```

## Spec: manage_trash — **SHIPPED v3.7.0**

**File:** `plugin/apple_mail_mcp/tools/manage.py` (`manage_trash`, ID branches for `move_to_trash` / `delete_permanent`)

### New signature
```python
def manage_trash(
    account: Optional[str] = None,
    action: str = "move_to_trash",
    message_ids: Optional[List[str]] = None,  # NEW
    subject_keyword: Optional[str] = None,
    subject_keywords: Optional[List[str]] = None,
    sender: Optional[str] = None,
    mailbox: str = "INBOX",
    max_deletes: int = 5,
    confirm_empty: bool = False,
    apply_to_all: bool = False,
    older_than_days: Optional[int] = None,
    dry_run: bool = True,
    timeout: Optional[int] = None,
) -> str:
```

### Behavior matrix

| Action | message_ids provided? | Behavior |
|---|---|---|
| `move_to_trash` | YES | Move exact IDs to trash; ignore keyword/date filters. Respect dry_run. |
| `move_to_trash` | NO | Use keyword-based path; add `recent_days=14` default if no date filter. |
| `delete_permanent` | YES | Permanently delete exact IDs from trash; ignore keyword filters. |
| `delete_permanent` | NO | Current behavior: require filter or `apply_to_all=True`. |
| `empty_trash` | N/A | No message_ids usage. |

### AppleScript pattern (pseudo)

#### move_to_trash with message_ids (dry_run=False):
```applescript
set targetMessages to every message of sourceMailbox whose id is in {id1, id2, ...}
repeat with aMessage in targetMessages
    move aMessage to trashMailbox
end repeat
```

#### delete_permanent with message_ids:
```applescript
set targetMessages to every message of trashMailbox whose id is in {id1, id2, ...}
repeat with aMessage in targetMessages
    delete aMessage
end repeat
```

## Spec: get_email_thread — **SHIPPED v3.7.0**

**File:** `plugin/apple_mail_mcp/tools/search.py` (`get_email_thread`)

### New signature
```python
def get_email_thread(
    account: str,
    message_id: Optional[str] = None,  # NEW: single message ID to start thread
    subject_keyword: Optional[str] = None,  # fallback if no message_id
    mailbox: str = "INBOX",
    max_messages: int = 50,
    recent_days: int = 30,  # NEW: cap date range to prevent full mailbox scan
    timeout: Optional[int] = None,  # NEW
) -> str:
```

### Lookup strategy

**Path 1: message_id provided (fast, recommended)**
1. Fetch email by ID using `get_email_by_id` logic (core.py ID matching)
2. Extract the base subject (strip "Re:", "Fwd:", etc.)
3. Search for all messages in the past `recent_days` (default 30d) with matching base subject
4. Return sorted thread

**Path 2: subject_keyword fallback (existing behavior, capped)**
1. When `message_id` is None, use subject_keyword to find base message
2. Apply `recent_days=30` window (default, configurable) to avoid full mailbox scan
3. Find all matches in window with normalized subject
4. Return sorted thread

### Fallback behavior

- If `message_id` not provided AND `subject_keyword` is empty: return error "Provide either message_id or subject_keyword"
- If no matches found in `recent_days` window: report "No thread found in the past N days; try increasing recent_days or providing message_id"
- Always use `recent_days` cap; do NOT remove or make unbounded

### Open question
- Mail.app does not expose a native thread/conversation accessor in AppleScript. Confirm we continue using subject matching (with base subject normalization) rather than attempting message-id header matching.

## Guardrails to add

### Priority: apply before first refactor iteration

| Tool | Issue | Fix |
|---|---|---|
| `list_email_attachments` (analytics.py:21) | keyword-only, no recent_days default | Add `recent_days=7` param, apply via date filter |
| `get_top_senders` (smart_inbox.py:524) | no date filter | Add `recent_days=30` default, apply in whose-clause |
| `get_awaiting_reply` (smart_inbox.py:73) | no date filter | Add `recent_days=30` default, apply in whose-clause |
| `get_needs_response` (smart_inbox.py:293) | no date filter | Add `recent_days=30` default, apply in whose-clause |
| `get_statistics` (analytics.py:154) | no date filter | Add `recent_days=90` default for broader stats, but cap to single account if unspecified |
| `export_emails` (analytics.py:492) | keyword-only, `recent_days` hardcoded to 90 in helper | Document hardcoded window in docstring, consider making param |

### Lower priority: backlog for Phase 2

- Add `message_ids` path to `list_email_attachments` (niche use case)
- Parallelize multi-account searches in all tools (already done in `search_emails`; apply pattern to others)

## Open questions

1. **Thread detection via Message-ID header**  
   Does Mail.app expose `message-id` header accessor in AppleScript? If so, should `get_email_thread` use strict RFC 5322 Message-ID matching instead of subject-based grouping? (Recommend: subject-based for now; revisit only if strict RFC requirement emerges.)

2. **Bulk operation cap on ID lists**  
   Should `move_email` and `manage_trash` accept large ID lists (e.g., 1000 IDs)? Current `max_moves`/`max_deletes` default to 50. Recommend: keep defaults, document that very large lists may timeout. Implementer should test with 200+ IDs to confirm AppleScript whose-clause performance.

3. **Async vs sync for ID-based operations**  
   Current `update_email_status` with IDs is synchronous. Should ID paths in `move_email` / `manage_trash` also be sync, or is there value in making them async for very large ID lists? Recommend: keep sync for simplicity and predictability.

4. **Error reporting on partial failures**  
   If AppleScript fails to move message ID 123 but succeeds on 124 and 125, should we report partial success or bail? Current code reports summary counts. Recommend: keep summary counts; add optional `strict_mode` flag if exact failure per-ID is needed.

---

**Next step (backlog):** Apply `recent_days` defaults to analytics/smart_inbox tools in the guardrails table; optional `message_ids` on `list_email_attachments`. Live perf gate on 24K-message mailbox after release.
