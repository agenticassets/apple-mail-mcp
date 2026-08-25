# CLAUDE.md — `apple_mail_mcp` package

Source of truth for the MCP server and repo CLI. Packaged on PyPI as **`mcp-apple-mail`** (`pyproject.toml` → `plugin/apple_mail_mcp/` plus `plugin/ui/` for the dashboard).

Tool/CLI work: delegate to subagents when available and permitted; use **`plugin-dev:plugin-validator`** after tool-count or manifest changes when available. If not, document the gap and run local validation. See root [`CLAUDE.md`](../../CLAUDE.md), Agent orchestration section.

## Entry flow

**MCP:** `__main__.py` → orphan watcher → `--read-only` / `--draft-safe` → set `server.READ_ONLY` / `server.DRAFT_SAFE` → import package (registers tools) → remove `SEND_TOOLS` if read-only → `mcp.run()`.

**CLI:** `apple-mail` script → `cli/__init__.py:main` (same tool functions, no MCP transport). Entry points: `mcp-apple-mail` → `__main__:main`; `apple-mail` → `cli:main`.

## Key modules

| Module | Role |
|--------|------|
| `server.py` | Shared `FastMCP`, env config, `ToolAnnotations` presets, `SEND_TOOLS` |
| `core/` | Facade package (`__init__.py` re-exports + `__all__`) over `applescript`/`escaping`/`preferences`/`normalization`/`validation`/`script_fragments`/`replied`: `run_applescript`, `escape_applescript`, validation, `@inject_preferences`, script builders. `core/reply_state.py` is a sibling leaf the facade does **not** re-export — import it directly |
| `cli/` | `apple-mail` subcommands package (facade `__init__.py` + `constants`/`formatting`/`parser`/`perf`/`draft_smoke`/`commands`); search, inbox, draft, smoke-test, quick-check, … |
| `__main__.py` | MCP stdio entry, orphan watcher (python-sdk#526), read-only tool removal |
| `__init__.py` | Side-effect imports of seven `tools/` surfaces; `UI_AVAILABLE` flag |
| `constants.py` | Shared patterns (`SKIP_FOLDERS`, newsletter detection, `TIME_RANGES`, `SCAN_BOUNDS`, `CALENDAR_BOUNDS`) |
| `bounded_scan.py` | `ScanWindow` tokens, `compute_scan_upper_bound`, safe AppleScript builders |
| `backend/base.py` | Backend seam value objects and the structured-error primitives every tool raises: `ScanWindow`, `WriteResult`, `ToolError`, `serialize_tool_error`, `target_selector_deprecated_error`, and the `MailReadBackend`/`MailWriteBackend` Protocols (the Protocols are still dormant; tools call `core.run_applescript` directly) |
| `calendar_core/` | Calendar engine seam: `CalendarWindow` tokens, validation, RRULE expansion, AppleScript builders, optional EventKit read fast path (`get_engine`/`get_write_engine`) |

## `tools/` subfolder

**41 tools** in **7 surfaces** (inbox 6, search 4, compose 7, manage 6, analytics 5, smart_inbox 3, calendar 10). Verify: `rg -c '^@mcp\.tool' plugin/apple_mail_mcp/tools | awk -F: '{sum+=$NF} END {print sum}'` (recursive: `compose/`, `search/`, `inbox/`, `manage/`, `analytics/`, `smart_inbox/`, and `calendar/` are packages). Compose HTML paste splits body focus (`compose/html_focus_scripts.py`) and post-paste subject restore (`compose/html_subject_scripts.py`) out of `compose/send.py`; module map and transaction order live in **`tools/CLAUDE.md`** § Compose package leaves. `inbox/overview.py` keeps the script builder and parser while `inbox/overview_formatting.py` holds its pure text/JSON formatters. Cross-surface leaves at `tools/` level: `draft_verification.py` (pure Drafts-verification payload helpers, e.g. quote/attachment normalization), `reply_state_wiring.py` (replied/draft state), and `unread_provenance.py` (Mail's cached `unread count` is not a measured count — **`tools/CLAUDE.md`** § Cached unread counts). For other tool work read **`tools/CLAUDE.md`** and **`docs/CLAUDE-conventions.md`**; do not duplicate those conventions here.

**Module line budget:** every tool surface (`compose/`, `search/`, `inbox/`, `manage/`, `analytics/`, `smart_inbox/`, `calendar/`) is split into under-budget packages; no tool module exceeds **600 LOC**. CI warns and blocks further growth (`docs/CLAUDE-conventions.md` § Module line budget).

## Shared state (`server.py`)

- `DEFAULT_MAIL_ACCOUNT` — from env; tools read lazily via `server.DEFAULT_MAIL_ACCOUNT`
- `DEFAULT_MAIL_SIGNATURE` — from env; compose/reply/forward apply this Apple Mail signature by default unless `include_signature=False`
- `USER_PREFERENCES` — from `USER_EMAIL_PREFERENCES` env; `@inject_preferences` appends to tool docstrings
- `READ_ONLY` / `DRAFT_SAFE` — set by CLI flags in `__main__.py`
- Annotation presets: `READ_ONLY_TOOL_ANNOTATIONS`, `WRITE_TOOL_ANNOTATIONS`, `IDEMPOTENT_WRITE_TOOL_ANNOTATIONS`, `DESTRUCTIVE_TOOL_ANNOTATIONS`
- `SEND_TOOLS = ("compose_email", "reply_to_email", "forward_email")` — removed in read-only mode
- `CALENDAR_WRITE_TOOLS` + `CALENDAR_DESTRUCTIVE_TOOLS`: removed in read-only mode; deletes also blocked under draft-safe unless `CALENDAR_ALLOW_DESTRUCTIVE=1`
- `DEFAULT_CALENDAR`: from env; calendar create-target default (reads keep capped fan-out)

## AppleScript rule

All Mail.app I/O via `core.run_applescript()`. User strings through `core.escape_applescript()`. Catch `core.AppleScriptTimeout` in tools. No raw `subprocess.run(["osascript", …])`.

## Related & dev

[`tools/CLAUDE.md`](tools/CLAUDE.md) · [`docs/CLAUDE-conventions.md`](../../docs/CLAUDE-conventions.md) · [`plugin/skills/CLAUDE.md`](../skills/CLAUDE.md) (agent workflow skills) · root [`CLAUDE.md`](../../CLAUDE.md)

- `../start_mcp.sh` — plugin launcher; `../../tests/` mocks `subprocess.run`; `../../tools/validators/validate_manifests.py` — manifest parity · [`plugin/skills/CLAUDE.md`](../skills/CLAUDE.md) — which skills reference which tools
- Dependency/package changes must keep `../../pyproject.toml` and `../requirements.txt` aligned; `mcp-ui-server` and `plugin/ui` are required for the dashboard runtime.
- `.venv/bin/pytest tests/` · `.venv/bin/apple-mail quick-check --account "…"` · `.venv/bin/python -m apple_mail_mcp --read-only`
