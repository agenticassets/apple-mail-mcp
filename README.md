# Apple Mail MCP Server

<!-- mcp-name: io.github.agentic-assets/apple-mail -->

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Release](https://img.shields.io/github/v/release/Agentic-Assets/apple-mail-mcp)](https://github.com/Agentic-Assets/apple-mail-mcp/releases/latest)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Compatible-green.svg)](https://modelcontextprotocol.io)
[![GitHub stars](https://img.shields.io/github/stars/Agentic-Assets/apple-mail-mcp?style=social)](https://github.com/Agentic-Assets/apple-mail-mcp/stargazers)

## Star History

<a href="https://star-history.com/#Agentic-Assets/apple-mail-mcp&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=Agentic-Assets/apple-mail-mcp&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=Agentic-Assets/apple-mail-mcp&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=Agentic-Assets/apple-mail-mcp&type=Date" />
 </picture>
</a>

An MCP server that gives AI assistants full access to Apple Mail and Apple Calendar -- read, search, compose, organize, and analyze emails, plus bounded calendar reads, conflict-checked event creation, and availability search, via natural language. Built with [FastMCP](https://github.com/jlowin/fastmcp) (`fastmcp>=3.1.0,<4`). **41 tools**, Python **3.10+**.

## Documentation map

| Doc | Purpose |
|-----|---------|
| [`CLAUDE.md`](CLAUDE.md) | Root navigation hub for agents |
| [`PRIVACY.md`](PRIVACY.md) | Privacy policy: what the server accesses, what leaves the Mac, files it writes |
| [`SECURITY.md`](SECURITY.md) | Vulnerability reporting and the safety model |
| [`docs/CLAUDE-conventions.md`](docs/CLAUDE-conventions.md) | Tool performance rules, read-only, skills, plugin-dev |
| [`docs/AGENT_LIVE_TESTING.md`](docs/AGENT_LIVE_TESTING.md) | Live Mail verification via `apple-mail` CLI |
| [`plugin/docs/CLAUDE.md`](plugin/docs/CLAUDE.md) | Plugin wrapper & `start_mcp.sh` |
| [`plugin/apple_mail_mcp/CLAUDE.md`](plugin/apple_mail_mcp/CLAUDE.md) | Package entry, `core.py`, CLI |
| [`plugin/apple_mail_mcp/tools/CLAUDE.md`](plugin/apple_mail_mcp/tools/CLAUDE.md) | MCP tool modules |
| [`plugin/skills/CLAUDE.md`](plugin/skills/CLAUDE.md) | Skill authoring |
| [`tests/CLAUDE.md`](tests/CLAUDE.md) | Test layout & AppleScript mocks |
| [`tools/CLAUDE.md`](tools/CLAUDE.md) | Manifest validation scripts |
| [`tools/marketplace_identity.json`](tools/marketplace_identity.json) | Central marketplace identity, standalone compatibility identity, and promotion ownership |
| [`docs/marketplace-release-handoff.md`](docs/marketplace-release-handoff.md) | Compact signed-tag to central Marketplace update path |
| [`docs/CLAUDE.md`](docs/CLAUDE.md) | Docs folder index + plugin skill map |
| [`tasks/CLAUDE.md`](tasks/CLAUDE.md) | Phase plans & backlog |
| [`apple-mail-mcpb/CLAUDE.md`](apple-mail-mcpb/CLAUDE.md) | Desktop bundle build |
| [`.claude-plugin/CLAUDE.md`](.claude-plugin/CLAUDE.md) | Claude Code marketplace manifest |
| [`.agents/plugins/marketplace.json`](.agents/plugins/marketplace.json) | Codex Desktop/CLI marketplace manifest |

## Linear

Track bugs and feature work in the [Apple Mail MCP](https://linear.app/agenticassets/project/apple-mail-mcp) project on the Agentic Assets team.

| Field | Value |
|-------|-------|
| Team | Agentic Assets (`AGENTIC`) |
| Team ID | `da8832b3-3dde-416f-be01-98c76a5806c7` |
| Project | Apple Mail MCP |
| Project ID | `bce30dc3-76fc-4745-b466-5f258c7f78e7` |

**Local development only.** This repo drives Mail.app through AppleScript on the host Mac. Mocked tests are not enough for tool changes: live verification needs a machine with Apple Mail configured (for example the `mac-mini-apple-mail-mcp` Cursor worker). Do not use cloud agents or remote sandboxes for implementation work here; file Linear issues with the `Agentic-Assets/apple-mail-mcp` repo label.

## Quick Install

**Prerequisites:** macOS with Apple Mail configured. Installing the Python
package from a source checkout of this repository supports Python 3.10+. The
self-contained Claude, Codex, and Cursor plugin payload currently requires Apple
Silicon (macOS arm64) and Python 3.13 because its bundled, hash-locked wheelhouse
is platform-specific and never downloads packages at startup.

> **Note on PyPI.** The PyPI project named `mcp-apple-mail` is published by the
> original upstream author, not by Agentic Assets, and installing it does not
> install this software. Every install path below comes from this repository:
> the plugin marketplaces, the `.mcpb` bundle and `.plugin` upload from a GitHub
> Release, or a source checkout.

### Agentic Assets Marketplace (Recommended)

Agentic Assets users should register the central
[`Agentic-Assets/Agentic-Assets-Marketplace`](https://github.com/Agentic-Assets/Agentic-Assets-Marketplace)
repository. Apple Mail keeps the same primary selector across supported
marketplace clients: `apple-mail@agentic-assets`.

#### Claude Code

One install: MCP server (41 tools) and **eleven** bundled workflow skills under `plugin/skills/` (see table below). Workflow entry points are skills-only; the old `/email-management` slash command was retired to avoid duplicate skill/command exposure.

```bash
claude plugin marketplace add Agentic-Assets/Agentic-Assets-Marketplace --scope user
claude plugin marketplace update agentic-assets
claude plugin install apple-mail@agentic-assets --scope user
```

The GitHub-backed central marketplace lets Claude Code refresh Apple Mail and
other Agentic Assets plugins from one source.

Then restart Claude Code.

#### Codex Desktop / CLI

Codex registers the central GitHub marketplace repository and resolves the
same Apple Mail selector:

```bash
codex plugin marketplace add https://github.com/Agentic-Assets/Agentic-Assets-Marketplace.git
codex plugin add apple-mail@agentic-assets
```

The central marketplace contains a self-contained promoted Apple Mail payload
under `plugins/apple-mail/`. This development repository remains the source of
truth. Releases enter the marketplace only as immutable, allowlisted snapshots
from signed source tags. The marketplace repository owns promotion policy,
evidence, and attestations.

The root `.claude-plugin/marketplace.json` and
`.agents/plugins/marketplace.json` files in this repository deliberately keep
the standalone compatibility identity `apple-mail-mcp` and selector
`apple-mail@apple-mail-mcp`. They support public and maintainer development
workflows and must not be renamed to `agentic-assets`.

MCP-only fallback, still draft-safe:

```bash
codex mcp add apple-mail -- /bin/bash /path/to/apple-mail-mcp/plugin/start_mcp.sh --draft-safe
```

If `mcp__apple-mail__*` tools are absent after plugin install, treat that as an MCP registration failure. Do not create reply drafts with generic AppleScript, Mail UI scripting, shell `osascript`, or standalone compose fallbacks. Fix registration first, or use the MCP-only absolute-path fallback above, restart Codex, and confirm the Apple Mail tools are present before drafting.

How to know it worked: `codex plugin list` showing `installed, enabled` is not enough. The pass condition is that the active Codex session exposes `mcp__apple-mail__*` tools and an MCP `list_tools` handshake includes `reply_to_email`. Maintainers can run `bash tools/gates/validate-codex-plugin.sh` to check that install plus runtime path in a temporary `CODEX_HOME`.

Restart Codex Desktop or start a fresh Codex CLI session after installing.

### Refresh another Mac / second computer

#### Primary Agentic Assets users

Use the central refresh helper for an existing `agentic-assets` registration or
a fresh central install. It preflights both client registrations, refuses a
same-name marketplace from any other source, and never removes marketplaces,
plugins, caches, or user data.

```bash
# Read-only source and payload preflight
bash tools/gates/refresh-central-marketplace.sh --check

# Install or refresh apple-mail@agentic-assets in Claude Code and Codex
bash tools/gates/refresh-central-marketplace.sh
```

Restart Claude Code and Codex after the helper succeeds so both clients reload
the MCP schemas. The helper proves the Claude Code and Codex registrations and
runtime bootstrap only. It does not claim Cursor marketplace/UI admission.

#### Maintainer standalone compatibility

Use the direct-source compatibility helper when another computer has an older
standalone Apple Mail development install or stale `apple-mail-mcp` cache. It
verifies this repository's compatibility identity, not the primary central
marketplace.

On a machine with this repo checked out, the guarded compatibility refresh is:

```bash
bash tools/gates/refresh-local-plugins.sh
```

The script validates the checkout, verifies that the lowercase direct-source
identity `apple-mail-mcp` points to this repository, installs and verifies
`apple-mail@apple-mail-mcp` in both clients, and leaves every other marketplace
and plugin registration untouched. It does not pull Git or delete client caches
directly. If the target identity belongs to another repository, it refuses
without changing any registration. The shared `agentic-assets` identity belongs
to the separate [Agentic Assets Marketplace](https://github.com/Agentic-Assets/Agentic-Assets-Marketplace),
which can catalog Apple Mail and Corbis without a source collision.

1. Get the current code:

```bash
cd ~/Documents/GitHub/agentic-assets/apple-mail-mcp
git switch main && git pull --ff-only
```

2. Refresh both plugin clients with the guarded compatibility helper:

```bash
bash tools/gates/refresh-local-plugins.sh
codex mcp get apple-mail --json
```

The Codex MCP registration should show:

```json
{
  "command": "/bin/bash",
  "args": ["./start_mcp.sh", "--draft-safe"]
}
```

`codex plugin list` showing `installed, enabled` is not enough. For a runtime
smoke, run:

```bash
python3 -m venv .venv
.venv/bin/pip install -e . pytest
.venv/bin/python tools/probes/mcp_tool_smoke.py \
  --command /bin/bash \
  --arg ./start_mcp.sh \
  --arg=--draft-safe \
  --cwd "$PWD/plugin" \
  --expect-count 41 \
  --required-tool reply_to_email \
  --required-tool compose_email \
  --required-tool manage_drafts \
  --required-tool list_accounts \
  --required-tool get_inbox_overview
```

3. Verify the Claude Code registration:

```bash
claude plugin details apple-mail@apple-mail-mcp
```

Prefer `--scope user` for personal machine setup. Project-scope marketplace
entries can write an absolute local path into `.claude/settings.json`, which is
usually not what you want to commit.

`claude plugin details apple-mail@apple-mail-mcp` should report the current version
and `MCP servers (1) apple-mail`. To smoke the installed Claude cache directly,
replace `VERSION` or the path below if the details output shows a different
install path:

```bash
VERSION=3.11.8
.venv/bin/python tools/probes/mcp_tool_smoke.py \
  --command /bin/bash \
  --arg "$HOME/.claude/plugins/cache/apple-mail-mcp/apple-mail/$VERSION/start_mcp.sh" \
  --arg=--draft-safe \
  --cwd "$HOME/.claude/plugins/cache/apple-mail-mcp/apple-mail/$VERSION" \
  --expect-count 41 \
  --required-tool reply_to_email \
  --required-tool compose_email \
  --required-tool manage_drafts \
  --required-tool list_accounts \
  --required-tool get_inbox_overview
```

4. Restart clients:

After either refresh, restart Codex Desktop / start a fresh Codex CLI session and
restart Claude Code so they load the refreshed plugin process.

### Claude Desktop Cowork (plugin marketplace)

Cowork uses Anthropic's **remote marketplace backend** (`remoteMarketplaceClient`), which currently rejects most third-party GitHub marketplaces with a generic **"Failed to add marketplace"** even when the repo is valid. This is a [known Cowork/Desktop bug](https://github.com/anthropics/claude-code/issues/41653), not a problem with this fork's manifest. Claude Code CLI install (above) works; Cowork's GitHub sync often does not.

**Workaround — upload the `.plugin` file directly (recommended for Cowork):**

1. Download `apple-mail.plugin` from the [latest GitHub Release](https://github.com/Agentic-Assets/apple-mail-mcp/releases/latest), or build the artifacts locally with `bash tools/gates/build-artifacts.sh`, which produces `apple-mail.plugin`, `apple-mail-plugin.zip`, and `apple-mail-mcp-v{VERSION}.mcpb` at the repo root.
2. Cowork → **Customize** → **Add plugin** → **Upload plugin**.
3. Select `apple-mail.plugin` and enable **Apple Mail**.

`apple-mail.plugin` and `apple-mail-plugin.zip` are byte-identical — both work, the `.plugin` extension is the canonical Cowork upload format.

```bash
cd /path/to/apple-mail-mcp
bash tools/gates/build-artifacts.sh   # produces apple-mail.plugin, apple-mail-plugin.zip, and .mcpb
```

If you must build the zip by hand, zip from **inside** `plugin/` so `.claude-plugin/plugin.json` sits at the zip root — Cowork rejects uploads where it is nested under `plugin/`:

```bash
cd /path/to/apple-mail-mcp/plugin
zip -rq -X -D ../apple-mail-plugin.zip . \
  -x 'venv/*' '*/__pycache__/*' '*.pyc' '*.DS_Store' 'CLAUDE.md' '*/CLAUDE.md'
```

**Important:** Apple Mail MCP requires **macOS Mail.app** on the host Mac (`start_mcp.sh` → AppleScript). Cowork's Linux VM cannot run Mail directly; the plugin MCP server must execute on your Mac host. If tools fail after upload, use the **Claude Code CLI** install or the **Desktop `.mcpb`** path below instead.

If Cowork's marketplace sync becomes available, use the primary marketplace
`Agentic-Assets/Agentic-Assets-Marketplace` and selector
`apple-mail@agentic-assets`. The direct `apple-mail.plugin` upload remains the
tested compatibility fallback. Do not register this source repository as the
primary Agentic Assets marketplace.

### Other Install Methods

<details>
<summary><strong>Repo CLI + MCP runtime</strong></summary>

This fork includes a maintained `apple-mail` CLI that wraps the same Python
tool code as the MCP server. It is meant for humans, shell scripts, smoke
tests, and agents on another Mac.

```bash
git clone https://github.com/Agentic-Assets/apple-mail-mcp.git
cd apple-mail-mcp
python3 -m venv .venv
.venv/bin/pip install -e .

.venv/bin/apple-mail accounts --json
.venv/bin/apple-mail search --account "Gmail" --query "invoice" --limit 10 --json
.venv/bin/apple-mail show --account "Gmail" --id 12345 --json
.venv/bin/apple-mail draft --account "Gmail" --to person@example.com --subject "Draft" --body "Draft body" --signature-name "TU"
.venv/bin/apple-mail quick-check --account "Gmail" --json
.venv/bin/apple-mail perf-test --account "Gmail" --json
.venv/bin/apple-mail perf-test --include-analysis --allow-heavy-mail-scan --account "Gmail" --json
.venv/bin/apple-mail smoke-test --account "Gmail" --json
```

See [`docs/AGENT_LIVE_TESTING.md`](docs/AGENT_LIVE_TESTING.md) for batteries, permissions, and when to use each command.

Generate draft-safe Claude/OpenClaw MCP config from the same checkout:

```bash
.venv/bin/apple-mail mcp-config --repo "$(pwd)"
```

</details>

<details>
<summary><strong>Python package from source (MCP server only)</strong></summary>

Install the package from a checkout of this repository. There is no
Agentic-Assets-published PyPI release, so `pip install mcp-apple-mail` and
`uvx mcp-apple-mail` resolve to the unrelated upstream project rather than to
this software.

```bash
git clone https://github.com/Agentic-Assets/apple-mail-mcp.git
cd apple-mail-mcp
python3 -m venv .venv
.venv/bin/pip install .

claude mcp add apple-mail -- "$(pwd)/.venv/bin/mcp-apple-mail"
```

Pin to a tagged release instead of the default branch by adding
`git checkout v{VERSION}` after the clone, or install straight from a tag:

```bash
python3 -m venv ~/.venvs/apple-mail
~/.venvs/apple-mail/bin/pip install \
  "git+https://github.com/Agentic-Assets/apple-mail-mcp.git@v{VERSION}"

claude mcp add apple-mail -- ~/.venvs/apple-mail/bin/mcp-apple-mail
```

Or for Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`),
point `command` at the absolute path of the `mcp-apple-mail` executable in that
virtual environment:

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/absolute/path/to/.venv/bin/mcp-apple-mail"
    }
  }
}
```

For Claude Desktop most users should prefer the `.mcpb` bundle or `.plugin`
upload below, which need no Python environment management.

</details>

<details>
<summary><strong>Claude Desktop MCPB / DXT bundle</strong></summary>

For Claude Desktop chat (outside of Cowork mode):

1. Download `apple-mail-mcp-v{VERSION}.mcpb` from the [latest GitHub Release](https://github.com/Agentic-Assets/apple-mail-mcp/releases/latest) or build locally with `bash tools/gates/build-artifacts.sh`.
2. In Claude Desktop, open Settings → **Extensions** (or **Developer → MCP Servers → Install from file** depending on app version), pick **Add Custom Plugin / Install from file**, and select the `.mcpb`.
3. Grant the Automation + Mail Data Access prompts macOS surfaces on first run.
4. Restart Claude Desktop so the extension registers across chat and Cowork sessions. Cowork projects may need to enable the extension explicitly in the project's plugin settings.

The bundle bootstraps a per-install Python venv via `start_mcp.sh` on first run, so `python3` must be on PATH for the Claude Desktop process. For pure Cowork use, prefer the `.plugin` upload above (same MCP server, no Developer mode required).

</details>

<details>
<summary><strong>Manual setup</strong></summary>

```bash
git clone https://github.com/Agentic-Assets/apple-mail-mcp.git
cd apple-mail-mcp/plugin
python3 -m venv venv
venv/bin/pip install -r requirements.txt

claude mcp add apple-mail -- /bin/bash $(pwd)/start_mcp.sh
```

</details>

## Tools (41)

### Reading & Search
| Tool | Description |
|------|-------------|
| `get_inbox_overview` | Dashboard with unread counts, folders, and recent emails |
| `list_inbox_emails` | List emails (defaults to 50 most recent). Multi-account calls dispatch sequentially, one account at a time (all installed plugin hosts queue each `osascript` call); scans at most 50 messages per call |
| `get_mailbox_unread_counts` | Unread counts per mailbox or per-account summary |
| `list_accounts` | List all configured Mail accounts |
| `list_account_addresses` | List sender aliases configured for a Mail account |
| `search_emails` | Unified search: subject, sender, body, dates, attachments. Defaults to last 48h and the default account; scans at most 50 messages per call |
| `get_email_by_id` | Fetch one exact email by the Apple Mail message id returned from search results |
| `get_email_by_ids` | Fetch multiple exact emails by reviewed Apple Mail message ids, chunked internally |
| `get_email_thread` | Conversation thread view across Inbox + Sent; prefer `message_id` from search/list results |

### Organization
| Tool | Description |
|------|-------------|
| `list_mailboxes` | Folder hierarchy with optional message counts |
| `create_mailbox` | Create new mailboxes (supports nested paths) |
| `move_email` | Move by `message_ids` (required for targeting). `allow_filter_scan=True` + `older_than_days` only for date/bulk moves; `subject_keyword`/`sender` return `TARGET_SELECTOR_DEPRECATED`. Default max 50 |
| `update_email_status` | Mark read/unread, flag/unflag by `message_ids` (preferred). `allow_filter_scan=True` + date/bulk filters only; `subject_keyword`/`sender` return `TARGET_SELECTOR_DEPRECATED`. Default max 10 |
| `manage_trash` | Soft delete, permanent delete, empty trash; prefer `message_ids`. `allow_filter_scan=True` + `older_than_days` only; `subject_keyword`/`sender` return `TARGET_SELECTOR_DEPRECATED`. Default max 5 |
| `synchronize_account` | Explicitly confirmed Mail.app sync for an account (can fetch large backlogs) |

### Composition
| Tool | Description |
|------|-------------|
| `compose_email` | Create a new standalone draft by default; refuses reply-like subjects/bodies unless `standalone_confirmed=True`; does not include original thread context. Attachment-bearing calls refuse direct `mode="send"`: use `draft` or `open`, then Mail must immediately verify recipient, subject, authored body, exact attachment multiset, positive sizes, and no warnings at the one transaction-scoped Drafts locator. Any numeric Draft ID is a best-effort locator, not durable identity. |
| `reply_to_email` | Native Mail reply or reply-all draft. Default `native_format=True` is the only supported path: it composes in Mail's reply window (keeps the rich quote bar + logo signature), types `reply_body` above the quote, and only then adds requested attachments before saving. This needs window focus + Accessibility permission, else returns `REPLY_WINDOW_FOCUS_FAILED` (no draft saved). `native_format=False` returns `WINDOWLESS_FALLBACK_DISABLED` unless `allow_windowless_fallback=True` is explicitly passed (deliberate headless/CI only, never set by agents). Draft/open verification requires the authored body above the native quote and every requested attachment. An RFC-backed Drafts identity, linked to the source by `In-Reply-To`, may authorize a later delete-and-retype only after revalidation. If iCloud has not assigned an outgoing RFC `Message-ID`, exactly one new numeric Drafts row can instead prove this operation's verification only; it is never a reusable cleanup handle. A same-subject fallback is suspect and never authorizes deletion. Any attachment-bearing reply refuses `mode="send"` before mutation; save and verify the draft first. Returns the verification status, identity evidence, attachment status, and any current Drafts locator. |
| `forward_email` | Forward by `message_id` (required — discover via `search_emails` or `list_inbox_emails`). `subject_keyword` is schema-compat only and returns `TARGET_SELECTOR_DEPRECATED`. Attach only explicit local paths: source-message attachments are never copied implicitly. Attachment-bearing forwards refuse direct `mode="send"`; they are ready only after same-operation marker-bound proof or immediate strict readback confirms recipients, body, filename/count, and readability. A numeric Drafts locator is optional and volatile, never implied by marker proof. Optional message, CC/BCC; default saves to Drafts. |
| `manage_drafts` | Create, list, send, open, and delete drafts; `action=list` returns Drafts ids; send/open/delete require exact `draft_id` (`draft_subject` is schema-compat only and returns `TARGET_SELECTOR_DEPRECATED`). Guarded deletion is optional; when used, provide `expected_in_reply_to`, `expected_subject`, and `expected_to` together from a freshly resolved draft, and they are re-read immediately before deletion. Standalone `action=create` refuses reply-like drafts unless `standalone_confirmed=True` (`send` blocked in `--read-only` and `--draft-safe`) |
| `verify_draft` | Verify one exact Drafts message id; returns JSON snapshot for recipients, body sentinel, attachments, signature state, quoted-original status, and thread headers. `expected_body_contains` checks above a reliable quoted-original attribution boundary, never a bare authored `wrote:` phrase; with no reliable boundary it checks the whole preview. Pass `resolve_source=True` to map a reply draft back to its source Inbox message via a bounded `internet_message_id` lookup (adds a `source` block; widen `resolve_recent_days` from its 30-day default on a miss) |
| `verify_drafts` | Verify multiple exact Drafts message ids and merge the per-draft JSON snapshots; accepts the same `resolve_source` / `resolve_recent_days` options as `verify_draft` |
| `create_rich_email_draft` | Build a standalone multipart HTML `.eml` with optional explicit local attachments; refuses reply-like drafts unless `standalone_confirmed=True`. `open_in_mail=False` returns the prepared EML only. `open_in_mail=True` preserves that export and delegates to the supported focused HTML `compose_email` transaction, returning its immediate strict readback or `RICH_DRAFT_COMPOSE_FAILED`. |

### Attachments
| Tool | Description |
|------|-------------|
| `list_email_attachments` | List attachments by `message_ids` (required — discover via `search_emails` or `list_inbox_emails`); `subject_keyword` returns `TARGET_SELECTOR_DEPRECATED`. Capped at 50 by default |
| `save_email_attachment` | Save a specific attachment to disk. Requires `message_ids` from prior `search_emails`, `list_inbox_emails`, or `list_email_attachments`; use `list_email_attachments` to pick `attachment_index`. Validates target path |

### Smart Inbox
| Tool | Description |
|------|-------------|
| `get_awaiting_reply` | Sent emails that haven't received a reply (default last 7 days) |
| `get_needs_response` | Unread emails likely needing a response (filters out newsletters/automated); JSON rows include numeric `message_id` for actions and `internet_message_id` for replied-header correlation |
| `get_top_senders` | Most frequent senders by count or domain over a date window |

### Analytics & Export
| Tool | Description |
|------|-------------|
| `get_statistics` | Account overview, sender stats, or mailbox breakdown; short windows fan across 10 mailboxes, longer windows across 20, each capped at 50 messages |
| `export_emails` | Export TXT, HTML, or raw RFC 822 EML by exact `message_ids`, single `message_id`, bounded sender/date filters, correspondent history, threads, or paged mailbox slices. Hard-capped at 50 emails per call (`max_emails` and `message_ids` length); page with `offset` or narrow with filters for more. Each entire-mailbox page is deterministically newest-first (received date, then numeric message id) and reports each exported `message_id` for safe follow-on reads or drafts. For `format="eml"`, `include_attachments=True` writes `{index}_{subject}/message.eml` plus `attachments/`; files over 25 MiB or above the 100 MiB bounded-batch cap are skipped. Attachment downloads can need a larger `timeout` on cold Exchange/Gmail caches. |
| `inbox_dashboard` | Interactive UI dashboard (requires `mcp-ui-server`) |
| `full_inbox_export` | Disabled: returns a structured `UNBOUNDED_EXPORT_DISABLED` error instead of walking the mailbox. Stays registered for compatibility; narrow the window (`recent_days` / `date_from`) or page through bounded calls (`export_emails`, `list_inbox_emails`, `search_emails`) instead. |

### Apple Calendar
| Tool | Description |
|------|-------------|
| `list_calendars` | List every calendar with id, writability, defaults, and engine diagnostics |
| `list_events` | Bounded event listing/search with title/query and participant filters, recurring expansion, and paging (default: next 7 days) |
| `get_events_by_id` | Full detail (notes, alarms, attendees) for exact event ids; always window-bounded |
| `check_availability` | Busy blocks and free slots inside working hours over a bounded window (max 62 days) |
| `create_event` | Timezone-correct event creation with alarms, allowlisted recurrence, and conflict detection on by default |
| `batch_create_events` | Up to 25 one-off events on one calendar; all items validate before any write |
| `update_event` | ID-first PATCH update; recurring targets require `span='all_occurrences'`; attendee sets are diffed |
| `delete_events` | Exact-id bulk delete, dry-run default, capped and chunked; one unresolved id aborts everything |
| `manage_calendars` | Create/rename/delete calendars; delete is triple-gated with a cascade event-count preview |
| `respond_to_invitation` | Documented refusal (`CALENDAR_RSVP_UNSUPPORTED`): no public macOS API can RSVP |

## Configuration

### Read-Only Mode

Pass `--read-only` to disable tools that send email (`compose_email`, `reply_to_email`, `forward_email`) and to remove every calendar write and destructive tool (`create_event`, `update_event`, `batch_create_events`, `manage_calendars`, `delete_events`). Draft management remains available (list, create, delete) but sending a draft via `manage_drafts` is blocked.

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/path/to/venv/bin/python3",
      "args": ["/path/to/apple_mail_mcp.py", "--read-only"]
    }
  }
}
```

### Draft-Safe Mode

Pass `--draft-safe` to keep read, search, draft, and open-for-review workflows available while blocking actual sends. Calendar event creation and updates stay available, but calendar deletes are blocked (`CALENDAR_DELETE_BLOCKED`; operator env unlock `CALENDAR_ALLOW_DESTRUCTIVE=1`) and attendee invitation sends are blocked (`INVITE_SEND_BLOCKED`). This is the recommended mode for shared agent workspaces.

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/path/to/plugin/start_mcp.sh",
      "args": ["--draft-safe"]
    }
  }
}
```

In draft-safe mode:

- `compose_email`, `reply_to_email`, and `forward_email` default to `mode="draft"` (quiet save to Drafts, no leftover compose windows). Native replies assign `reply_body` above the quoted original. An RFC-header-linked identity can support later guarded cleanup after revalidation; an iCloud transaction identity with one new numeric Drafts row verifies only the current call and cannot authorize later cleanup. A bounded newest-Drafts fallback reports a possible artifact only and never authorizes deletion or retyping.
- they apply `DEFAULT_MAIL_SIGNATURE` by default when set; pass `include_signature=False` or CLI `--no-signature` to suppress it. For replies, disabling signatures cannot skip `reply_body` insertion
- use `mode="open"` only when you want each draft saved and left open in Mail for review (bulk reply UIs)
- **ID-first workflow:** discover with `search_emails` or `list_inbox_emails`, collect `message_id`, then `reply_to_email(message_id=..., reply_body=...)` or `forward_email(message_id=...)`. Never pass `subject_keyword` to action tools — it returns `TARGET_SELECTOR_DEPRECATED`.
- reply drafting requires `reply_to_email(message_id=...)`; standalone draft creators (`compose_email`, `create_rich_email_draft`, `manage_drafts(action="create")`) block reply-like `Re:` / `Fwd:` drafts unless `standalone_confirmed=True`
- explicit `mode="send"` calls return an error
- `manage_drafts action="send"` returns an error; when send is enabled outside draft-safe mode, target drafts by exact `draft_id` from `manage_drafts(action="list")`

### Default Mail Account

Set `DEFAULT_MAIL_ACCOUNT` to make most tools default to one account instead of scanning every configured Mail account. This is the single biggest perf win on multi-account setups. Tools still accept an explicit `account` parameter to override, and you can pass `all_accounts=True` to a tool that supports it for explicit cross-account scope.

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/path/to/venv/bin/python3",
      "args": ["/path/to/apple_mail_mcp.py"],
      "env": {
        "DEFAULT_MAIL_ACCOUNT": "Work"
      }
    }
  }
}
```

Use the exact account name as it appears in Apple Mail (e.g. `Gmail`, `Work`, `iCloud`). Leave unset to query all accounts by default.

### User Preferences (Optional)

Set `USER_EMAIL_PREFERENCES` to give the assistant context about your workflow. The string is injected into every preference-aware tool's docstring so the model sees it as part of the tool description.

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/path/to/venv/bin/python3",
      "args": ["/path/to/apple_mail_mcp.py"],
      "env": {
        "DEFAULT_MAIL_ACCOUNT": "Work",
        "USER_EMAIL_PREFERENCES": "Prefer Archive folder over Trash, show max 25 emails, default to last week for triage"
      }
    }
  }
}
```

For `.mcpb` installs, configure both under Claude Desktop → **Developer > MCP Servers > Apple Mail MCP** (the bundle exposes them via `user_config`).

### Default Mail Signature

Set `DEFAULT_MAIL_SIGNATURE` to the exact Apple Mail signature name you want applied to new compose, reply, and forward drafts. Per-call `signature_name` overrides the default; `include_signature=False` disables it for one call. The CLI exposes this as `apple-mail draft --signature-name "TU"` and `--no-signature`.

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/path/to/plugin/start_mcp.sh",
      "args": ["--draft-safe"],
      "env": {
        "DEFAULT_MAIL_ACCOUNT": "Work",
        "DEFAULT_MAIL_SIGNATURE": "TU"
      }
    }
  }
}
```

### Performance Defaults

To stay fast on large mailboxes (24K+ messages), the server applies conservative defaults you can opt out of per-call:

| Default | Tools | Override |
|---------|-------|----------|
| Last 48 hours | `search_emails`, `get_awaiting_reply`, `get_needs_response`, `get_top_senders` | Pass `recent_days=N` (e.g. `7` for a week); routine tools reject unbounded scans |
| 50 emails max | `list_email_attachments` | Pass `max_results` |
| **50-message hard scan ceiling** | `search_emails`, `list_inbox_emails` | None: every call scans at most 50 messages regardless of `limit` / `max_emails` / `recent_days` / window. Page across multiple calls or narrow the window (`recent_days`, `date_from`) to see more |
| Single account | All scoped tools when `DEFAULT_MAIL_ACCOUNT` is set | Pass `account=<name>` or `all_accounts=True` |
| Per-call timeout | All long-running tools | Pass `timeout=<seconds>` |
| **Mail calls serialized** | Every Apple Mail tool | None: all installed plugin hosts for this macOS user queue every `osascript` invocation through one shared cross-process lock. Call one Apple Mail tool at a time and wait for its result; parallel or concurrent Mail tool calls queue behind each other and can time out. |
| Unbounded scans refused | All routine scan/search tools (`recent_days=0` / `max_emails=0`) | Returns structured error `code: UNBOUNDED_SCAN_REQUIRED`; narrow the window (`recent_days` / `date_from`) or page through bounded calls (`export_emails`, `list_inbox_emails`, `search_emails`). `full_inbox_export` is disabled and is not a working fallback |
| **ID-first mutations** | `move_email`, `update_email_status`, `manage_trash` | Pass `message_ids=[...]` from `search_emails`, `list_inbox_emails`, or `get_needs_response(output_format="json")` (fast, preferred). Date/bulk filter paths require `allow_filter_scan=True` or return `code: FILTER_SCAN_DISABLED`. `subject_keyword` and `sender` on action tools always return `TARGET_SELECTOR_DEPRECATED`, even with `allow_filter_scan=True`. |
| **Gated filter scans** | `move_email`, `update_email_status`, `manage_trash` (date/bulk path only) | `allow_filter_scan=True` + `older_than_days` or `apply_to_all` (slow; timeout-prone on 24k+ inboxes). Subject/sender selectors never work on action tools. Filter paths still default to a 48h `recent_days` window. |
| **Body scan gate** | `search_emails` | `body_text` requires `allow_body_scan=True` or returns `code: BODY_SCAN_DISABLED`. Prefer subject/sender/date filters; pair body scans with a tight date window. |

**Recommended mutation flow:** search, list, or `get_needs_response(output_format="json")` → collect numeric `message_id` values → call `move_email`, `update_email_status`, or `manage_trash` with `message_ids`. Use `dry_run=True` with ids for a fast preview without acting.

When a per-account call fails in a multi-account fan-out, you get partial results plus an `errors` field naming the account. JSON responses also include `error_details` when the tool can distinguish a timeout from another Mail/App permission error.

### Safety Limits (destructive ops)

Batch operations cap by default to prevent accidental bulk actions. Override via the per-tool parameter when needed.

| Operation | Default cap | Param |
|-----------|-------------|-------|
| `move_email` | 50 | `max_moves` |
| `update_email_status` | 10 | `max_updates` |
| `manage_trash` | 5 | `max_deletes` |
| `export_emails` | 25 default for every scope, including `entire_mailbox`; hard-capped at 50 (both `max_emails` and `message_ids` list length are rejected above 50) | `max_emails` (up to 50); page with `offset` or narrow with filters for more |

**Dry-run defaults:** `manage_trash` defaults to `dry_run=True` (safe preview — explicit override needed to act, especially for `action="delete_permanent"`). `move_email` and `update_email_status` default to `dry_run=False` (live) because their effects are reversible; pass `dry_run=True` to preview matches first.

## Usage Examples

```
Show me an overview of my inbox
Search for emails about "project update" in my Gmail
Find the recent "Domain name" message, show me its message_id, then draft a reply by id
Search for recent invoice messages, show me the candidate ids, then move the reviewed message_ids to Archive
Show me email statistics for the last 30 days
Draft replies to unread messages with mode=open for review, or create a rich HTML weekly-update draft
```

## CLI

Install from a repo checkout:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Common commands:

```bash
apple-mail accounts --json
apple-mail addresses --json
apple-mail inbox --account "Gmail" --limit 10 --json
apple-mail search --account "Gmail" --query "invoice" --limit 10 --json
apple-mail show --account "Gmail" --id 12345 --json
apple-mail mailboxes --account "Gmail" --json
apple-mail mailboxes --account "Gmail" --counts --json   # slower; explicit counts opt-in
apple-mail draft --account "Gmail" --to person@example.com --subject "Draft" --body "Draft body" --signature-name "TU"
apple-mail mcp-config --repo "$(pwd)"
apple-mail quick-check --account "Gmail" --json
apple-mail perf-test --account "Gmail" --json
apple-mail perf-test --include-analysis --allow-heavy-mail-scan --account "Gmail" --json
apple-mail smoke-test --account "Gmail" --json
```

Live verification guide: [`docs/AGENT_LIVE_TESTING.md`](docs/AGENT_LIVE_TESTING.md).

Use `perf-test --include-analysis --allow-heavy-mail-scan` only when you explicitly want the heavy analysis gate (`needs-response`, `awaiting-reply`, `top-senders`, `statistics`). Routine validation should use `quick-check`, `smoke-test`, or `perf-test` without analysis.

The CLI keeps write operations draft-first. It intentionally does not expose
send/delete shortcuts; use the MCP tools with `--draft-safe` for shared agents.

### Rich HTML Drafts

Use `create_rich_email_draft` when you need a visually formatted email, newsletter, or leadership update.

- It generates an unsent `.eml` file with multipart plain-text + HTML bodies
- It accepts explicit local attachment paths, which are embedded in the EML
- It can write only the `.eml` artifact with `open_in_mail=False`; that artifact is prepared but not Mail-verified or ready to send
- With `open_in_mail=True`, it preserves the EML and creates the Mail draft through the supported focused HTML `compose_email` transaction, never by importing the EML. Mail may briefly use an internal `__apple_mail_mcp_…` window title during paste; the saved draft always gets your real subject restored before save.
- Attachment-bearing drafts are always draft/open and retain `compose_email`'s immediate strict recipient/subject/body/filename/count/readability readback.
- If body focus, subject restore, or verification fails (`COMPOSE_BODY_FOCUS_FAILED`, `HTML_COMPOSE_SUBJECT_RESTORE_FAILED`, or `RICH_DRAFT_COMPOSE_FAILED`), it returns structured errors with the preserved EML path and no ready-to-send claim
- Blank subjects stay `.eml`-only until there is a subject for manual review
- It accepts partial details, so you can start with just an account and subject and fill in the rest later

This is more reliable than injecting raw HTML into AppleScript `content`, which Mail often stores as literal markup.

## Claude Code Skills

Workflow skills ship with the Claude Code and Codex plugin installs and load automatically on install (see [`plugin/skills/CLAUDE.md`](plugin/skills/CLAUDE.md) for routing):

| Skill | Purpose |
|-------|---------|
| [`apple-mail-operator`](plugin/skills/apple-mail-operator/) | MCP + Mail setup, accounts/mailboxes, safe navigation, performance |
| [`inbox-triage`](plugin/skills/inbox-triage/) | 5–10 min read-first scan (needs-response, awaiting-reply) |
| [`email-management`](plugin/skills/email-management/) | Sustained Inbox Zero habits and cross-cutting programs |
| [`mailbox-taxonomy`](plugin/skills/mailbox-taxonomy/) | Folder strategy, noise diagnosis, structural `create_mailbox` |
| [`email-archive-cleanup`](plugin/skills/email-archive-cleanup/) | Staged archive / bulk move / trash with dry runs + exports |
| [`mail-rules-advisor`](plugin/skills/mail-rules-advisor/) | Mail filter / rule proposals (manual apply in Mail.app) |
| [`email-drafting`](plugin/skills/email-drafting/) | Compose, reply, forward, rich drafts (`--draft-safe` aware) |
| [`email-style-profile`](plugin/skills/email-style-profile/) | Learn voice from Sent mail + preferences for drafting |
| [`email-attachments`](plugin/skills/email-attachments/) | List and save attachments with path safety |
| [`calendar-operator`](plugin/skills/calendar-operator/) | Bounded calendar reads, safe event CRUD, ID-first deletes, TCC troubleshooting |
| [`meeting-scheduler`](plugin/skills/meeting-scheduler/) | Find-slot workflow, cross-timezone scheduling, invitation limits + .ics alternative |

For standalone MCP installs, copy the needed skill directories manually (example loop):

```bash
for d in apple-mail-operator inbox-triage email-management mailbox-taxonomy \
         email-archive-cleanup mail-rules-advisor email-drafting \
         email-style-profile email-attachments calendar-operator meeting-scheduler; do
  cp -r "plugin/skills/$d" "$HOME/.claude/skills/$d"
done
```

The plugin MCP server starts with **`--draft-safe`** by default for both Claude Code (`plugin/.claude-plugin/plugin.json`) and Codex (`plugin/.mcp.json`).

## Requirements

- macOS with Apple Mail configured
- Python 3.10+ for the MCP server package installed from a source checkout
- Apple Silicon (macOS arm64) and Python 3.13 for the self-contained Claude, Codex, Cursor, and MCPB plugin payload
- `fastmcp>=3.1.0,<4` and `mcp-ui-server==1.0.0` for the MCP Apps dashboard
- Claude Desktop, Codex Desktop/CLI, or any MCP-compatible client
- Mail.app permissions: Automation + Mail Data Access (grant in **System Settings > Privacy & Security > Automation**)

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Mail.app not responding | Ensure Mail.app is running; check Automation permissions in System Settings |
| Slow searches on a large account | Set `DEFAULT_MAIL_ACCOUNT` to the account you actually work in. Pair `account=` with `recent_days=` (default 48h) for tight scopes. Pass `include_content=False` if you don't need bodies |
| One account fails across a fan-out | Returned JSON includes an `errors` array naming the account plus `error_details` when available. The other accounts' results are still returned. Bump `timeout=` for timeout entries; fix Mail permissions or account config for non-timeout entries |
| Mailbox not found | Use exact folder names; nested folders use `/` separator (e.g., `Projects/Alpha`) |
| Permission errors | Grant access in **System Settings > Privacy & Security > Automation** |
| Rich draft shows raw HTML | Use `create_rich_email_draft` instead of pasting HTML into `manage_drafts` or AppleScript `content` |
| Save / Don't Save when closing drafts | Use default `mode="draft"` or `mode="open"` (saves first). Avoid leaving unsaved compose windows from bulk agent runs |

## Privacy Policy

Apple Mail MCP runs entirely on your Mac and talks to Mail.app and Calendar.app through AppleScript. The server code opens no network connections of its own and sends nothing to Agentic Assets: no telemetry, no analytics, no advertising, and no sale of data. The AI client you run it in (Claude, Codex, Cursor, or another MCP client) sends tool inputs and outputs to its own model provider under that provider's terms, which this plugin does not control. The optional `inbox_dashboard` UI page loads one script from a CDN when a host renders it; pass `output_format="json"` to avoid that.

Full policy, including which macOS permissions are used, which files the server writes, and how the send-blocking modes work: [PRIVACY.md](PRIVACY.md). Agentic Assets company policies: [agenticassets.ai/privacy](https://agenticassets.ai/privacy) and [agenticassets.ai/terms](https://agenticassets.ai/terms).

## Support

- Bugs, questions, and install problems: [GitHub Issues](https://github.com/Agentic-Assets/apple-mail-mcp/issues). Include your macOS version, host client, install method, and the tool's structured error output, with addresses and subjects redacted.
- Security vulnerabilities: report privately through [GitHub Security Advisories](https://github.com/Agentic-Assets/apple-mail-mcp/security/advisories/new), not a public issue. See [SECURITY.md](SECURITY.md).
- Publisher: [Agentic Assets](https://agenticassets.ai).

## Project Structure

```
apple-mail-mcp/
├── .agents/
│   └── plugins/
│       └── marketplace.json   # Codex Desktop/CLI marketplace entry
├── .claude-plugin/
│   └── marketplace.json       # Claude Code marketplace manifest
├── plugin/                    # Shared Claude Code, Codex, and Cursor plugin runtime
│   ├── .codex-plugin/
│   │   └── plugin.json        # Codex plugin manifest
│   ├── .cursor-plugin/
│   │   └── plugin.json        # Cursor plugin manifest (local Agent acceptance passed)
│   ├── .claude-plugin/
│   │   └── plugin.json        # Claude Code plugin manifest
│   ├── .mcp.json              # Codex MCP config
│   ├── mcp.json                # Cursor MCP config
│   ├── skills/                # bundled workflow skills (see plugin/skills/CLAUDE.md)
│   ├── apple_mail_mcp/        # Python MCP server package (41 tools)
│   ├── apple_mail_mcp.py      # Entry point
│   ├── start_mcp.sh           # Startup wrapper (auto-creates venv)
│   └── requirements.txt
├── apple-mail-mcpb/           # MCPB build files (Claude Desktop)
├── LICENSE
└── README.md
```

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Run local gates before opening a PR:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" pytest
bash tools/gates/dev-check.sh          # manifests, 600 LOC module budget report, pytest
bash tools/gates/dev-check.sh release  # before plugin/manifest/package changes
```

The **module line budget** warns when `plugin/apple_mail_mcp/` or `tools/` Python files exceed **600 lines**, and CI fails if a tracked large file grows further. See [`docs/CLAUDE-conventions.md`](docs/CLAUDE-conventions.md) § Module line budget.

4. Commit and push
5. Open a Pull Request

## License

MIT -- see [LICENSE](LICENSE).

## Links

- [Releases](https://github.com/Agentic-Assets/apple-mail-mcp/releases)
- [Issues](https://github.com/Agentic-Assets/apple-mail-mcp/issues)
- [Discussions](https://github.com/Agentic-Assets/apple-mail-mcp/discussions)
- [Privacy Policy](PRIVACY.md)
- [Security Policy](SECURITY.md)
- [Agentic Assets](https://agenticassets.ai)
- [FastMCP](https://github.com/jlowin/fastmcp)
- [Model Context Protocol](https://modelcontextprotocol.io)
