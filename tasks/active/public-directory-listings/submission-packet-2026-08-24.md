# Public directory submission packet

**Date:** 2026-08-24
**Goal:** make `apple-mail` discoverable inside each client's own plugin browser (Claude Desktop/Cowork "Browse plugins", Claude Code `/plugin` Discover, Codex Plugins tab, Cursor Customize page), not only through the GitHub marketplace URL.
**Channel facts** were verified live on 2026-08-24 against primary sources; see the handoff for the source list.

Every answer below is copy-ready. Contact fields use URLs only, because this public repo's identity validator refuses committed email addresses. Enter the contact email directly in the form from the publisher's own mailbox.

## Shared listing copy

| Field | Value |
|-------|-------|
| Name | Apple Mail |
| Plugin id / slug | `apple-mail` |
| Publisher | Agentic Assets LLC |
| Short description (≤30 chars) | Draft-safe Apple Mail for AI |
| Tagline (≤55 chars) | Search, triage, draft, and organize Apple Mail locally |
| Description (≤1,024 chars) | Natural language interface for Apple Mail and Apple Calendar on macOS. 41 MCP tools search, read, triage, organize, analyze, export, and draft email, plus bounded calendar reads, conflict-checked event creation, and availability search. Everything runs locally against Mail.app and Calendar.app through AppleScript; the server itself opens no network connections and sends no telemetry. The default launch mode is draft-safe: the assistant creates reviewable drafts and never sends mail on its own. Ships eleven workflow skills (inbox triage, inbox zero, mailbox taxonomy, archive cleanup, Mail rules advisor, drafting, attachments, writing style, calendar operator, meeting scheduler, Mail operator). |
| Category | Productivity |
| Keywords | mcp, mcp-server, apple-mail, apple-calendar, email, macos, applescript, claude, codex, cursor |
| Platform | macOS only (Apple Silicon for the self-contained plugin payload; Python 3.13 via `brew install python@3.13`) |
| Repository | https://github.com/Agentic-Assets/apple-mail-mcp |
| Homepage / documentation | https://github.com/Agentic-Assets/apple-mail-mcp#readme |
| Latest release (artifacts) | https://github.com/Agentic-Assets/apple-mail-mcp/releases/latest |
| Privacy policy | https://github.com/Agentic-Assets/apple-mail-mcp/blob/main/PRIVACY.md (publisher policy: https://agenticassets.ai/privacy) |
| Terms | https://agenticassets.ai/terms |
| Support | https://github.com/Agentic-Assets/apple-mail-mcp/issues |
| Security reports | https://github.com/Agentic-Assets/apple-mail-mcp/security/advisories/new (private vulnerability reporting is enabled) |
| License | MIT |
| Logo | `plugin/assets/logo.svg` in the repo (flat envelope on #2563EB) |

### Example prompts (three required, five supplied)

1. "Summarize my inbox from the last three days and list anything that needs a reply."
2. "Find every email from my department chair this month and show me the thread with the newest one."
3. "Draft a polite reply to the latest message in my inbox declining the meeting and proposing next Tuesday afternoon."
4. "Which senders fill my inbox the most this quarter? Suggest a folder taxonomy and Mail rules."
5. "Check my calendar for open 30-minute slots on Thursday and draft an email offering two of them."

### Reviewer / test instructions

There is no hosted account to hand over: the server reads the reviewer's own Mail.app on their Mac. A reviewer needs a Mac with Apple Mail configured for at least one account with sample mail, Apple Silicon, and Python 3.13 (`brew install python@3.13`). First launch prompts for macOS Automation permission for Mail (and Calendar for calendar tools). Start with the read tools (`list_accounts`, `get_inbox_overview`, `search_emails`); compose tools produce drafts in Mail's Drafts mailbox and never send in the default `--draft-safe` mode. Read-only review is available by launching with `--read-only`. Full CLI-driven verification path: `docs/AGENT_LIVE_TESTING.md`.

### Data-handling statement (for security review fields)

Local only. The server drives Mail.app and Calendar.app through AppleScript on the user's machine; it does not read Mail's on-disk store directly and needs no Full Disk Access. The server process opens no network connections and sends no telemetry, analytics, or advertising data, and no data is sold. The one network fetch a reviewer will see is in the optional inbox dashboard HTML, which loads the MCP Apps SDK script from a public CDN when the host renders it; mailbox data is embedded locally in that page and never posted anywhere. Full policy: PRIVACY.md. Files are written only where the user asks (exports, saved attachments) plus a lock file in a per-user cache directory. Every tool carries MCP annotations (`title`, `readOnlyHint`, `destructiveHint`, `idempotentHint`). Destructive operations (permanent delete, empty trash) are gated behind explicit confirmation and bounded counts; sending is blocked in `--draft-safe` (default) and all writes are blocked in `--read-only`.

## Channel 1: Anthropic Claude plugin directory (open, form)

- Where it lands: https://claude.com/plugins (Cowork and Claude Desktop "Customize → Plugins → Browse plugins") and the `claude-community` marketplace in Claude Code (`anthropics/claude-plugins-community`). This is the only open Anthropic door; `claude-plugins-official` has no application process (partner curation only).
- Form: https://platform.claude.com/plugins/submit (Console org, Developer/Admin/Owner role; free sign-up at platform.claude.com) or https://claude.ai/admin-settings/directory/submissions/plugins/new (claude.ai Team/Enterprise org owner). Status page for the claude.ai path: https://claude.ai/admin-settings/directory/submissions.
- Inputs: public GitHub link (`https://github.com/Agentic-Assets/apple-mail-mcp`), the shared copy above, privacy URL, support URL, example prompts, reviewer instructions. The pipeline runs `claude plugin validate` and an automated safety scan; approved entries are SHA-pinned and re-mirrored automatically from the repo on later pushes.
- Prerequisites in the repo (this branch): tool titles on all 41 tools, `PRIVACY.md`, README Privacy and Support sections, `claude plugin validate plugin/` clean.
- Precedents admitted with the same shape: `local-mcp`, `imessage-mcp`, `mac-notes` (macOS-only local MCP).

## Channel 2: Claude Desktop extension directory (open, Google Form)

- Where it lands: Claude Desktop Settings → Extensions → Browse extensions.
- Form: https://clau.de/desktop-extention-submission (Google Form, any Google account; no status dashboard, escalation contact is the MCP review mailbox named on https://claude.com/docs/connectors/building/submission).
- Inputs: the `.mcpb` download URL from the latest GitHub Release, the shared copy, privacy URL, support URL, platform `darwin`, permissions (macOS Automation for Mail and Calendar), reviewer instructions.
- Prerequisites in the repo (this branch): `apple-mail-mcpb/manifest.json` migrated to `manifest_version` 0.3 with `compatibility.platforms: ["darwin"]` and `privacy_policies`, README "Privacy Policy" section. Missing privacy policy is an immediate rejection per Anthropic's page.

## Channel 3: Cursor Marketplace (open, form, manual review)

- Where it lands: https://cursor.com/marketplace, surfaced in Cursor's Customize page, `/add-plugin`, and `cursor-agent` `/plugin` Marketplace tab.
- Form: https://cursor.com/marketplace/publish (signed-in Cursor account; the owner field is locked to the individual account, so put "Agentic Assets LLC" in the notes and rely on `author` in the manifest). "Submit Application" with the public repo link. Reviews reported at one to eight weeks; indie submitters are sometimes redirected to cursor.directory first.
- Community fallback: https://cursor.directory/plugins/new (Cursor-operated community directory with an automated security scan).
- Notes field text: "Open source (MIT), macOS-only local MCP server for Apple Mail and Apple Calendar, no network egress, draft-safe by default, read-only mode available. Requires Apple Silicon and Python 3.13 (`brew install python@3.13`). Publisher is Agentic Assets LLC (https://agenticassets.ai); the submitting account belongs to its founder. Live-tested with cursor-agent: 41 tools load via `plugin/mcp.json`."
- Prerequisites in the repo (this branch): root `.cursor-plugin/marketplace.json`, full `plugin/.cursor-plugin/plugin.json` (displayName, author, license, homepage, repository, keywords, category, logo, skills), `plugin/README.md`, `plugin/assets/logo.svg`.

## Channel 4: Codex / OpenAI Plugins Directory (blocked by vendor policy)

- The unified ChatGPT and Codex Plugins Directory (https://platform.openai.com/plugins, docs https://developers.openai.com/plugins/deploy/submission) accepts only plugins whose MCP server is a publicly reachable HTTPS streamable-HTTP endpoint with domain verification. OpenAI's own migration guide (https://developers.openai.com/plugins/guides/submit-claude-plugin) says a plugin with only local `stdio` servers should "wait until OpenAI supports local MCP servers", and the portal does not accept `.mcpb`.
- `github.com/openai/plugins` is an export mirror of OpenAI-authored plugins with pull requests disabled; it is not a submission channel.
- Options, in order of value: (1) ask an OpenAI partner contact for the local-execution review lane the guide mentions; (2) keep the public GitHub marketplace path (`codex plugin marketplace add Agentic-Assets/apple-mail-mcp`, then `codex plugin add apple-mail@apple-mail-mcp`), which already works; (3) watch the submission docs for local MCP support; (4) a skills-only listing is possible but not recommended, because the skills reference tools that need the separate local install and the skill scanner flags that.
- Nothing in this repo blocks Codex; `plugin/.mcp.json` already matches the launch contract OpenAI uses for its own local plugins.

## Channel 5: Open MCP Registry (parallel, no Claude-side effect)

- https://registry.modelcontextprotocol.io, namespace `io.github.agentic-assets/apple-mail`. Not read by any Claude surface per Anthropic's docs; useful for other MCP clients.
- Blocker diagnosed on this branch: `server.json` claims PyPI `mcp-apple-mail` (now version-bumped to 3.12.0), but that PyPI project belongs to the original upstream author (latest 3.2.0), so Agentic Assets cannot publish there. The registry entry must instead point at the `.mcpb` GitHub Release asset with its SHA-256 (package type `mcpb`), which requires the release to exist first.
- Publish steps after the release: `mcp-publisher login github --token "$(gh auth token)"` (the `agenticassets` GitHub login must belong to the `Agentic-Assets` org), update `server.json` to the new release asset URL and hash, `mcp-publisher validate`, `mcp-publisher publish`.

## Already live (2026-08-24)

- GitHub Release `v3.11.9` with `apple-mail-mcp-v3.11.9.mcpb`, `apple-mail-plugin.zip`, `apple-mail.plugin`: https://github.com/Agentic-Assets/apple-mail-mcp/releases/tag/v3.11.9
- Repo topics: ai-agents, apple-calendar, apple-mail, applescript, claude-code, claude-plugin, codex-plugin, cursor-plugin, email, macos, mcp, mcp-server
- GitHub private vulnerability reporting enabled
