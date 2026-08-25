# Apple Mail plugin

Apple Mail and Apple Calendar automation for AI coding agents on macOS. The plugin runs a local MCP server that drives Mail.app and Calendar.app through AppleScript, so an agent can search, triage, organize, and draft mail, and read or schedule calendar events, from natural language.

This directory is the shared plugin runtime for Cursor, Claude Code, and Codex. The full project, including the Claude Desktop bundles, lives in the [apple-mail-mcp repository](https://github.com/Agentic-Assets/apple-mail-mcp).

## Requirements

- macOS with Apple Mail configured. Calendar tools use Calendar.app.
- Apple Silicon (arm64) and Python 3.13 for this self-contained plugin payload. Its bundled, hash-locked wheelhouse is platform-specific and never downloads packages at startup.
- Python 3.10+ if you install the Python package from a source checkout (`pip install .`) instead of this payload.
- Mail.app permissions: Automation and Mail Data Access, granted in System Settings > Privacy & Security > Automation. Native reply drafts also need Accessibility permission for the host application.

## Draft-safe by default

Every plugin manifest in this directory launches the server with `--draft-safe`. Mail tools create reviewable drafts and never send. Calendar tools can read, create, and update events, but cannot delete events or send attendee invitations. Pass `--read-only` instead to block every write. See the root README for the full mode table.

## Tools (41)

| Category | Count | Tools |
|----------|-------|-------|
| Reading and search | 9 | `get_inbox_overview`, `list_inbox_emails`, `get_mailbox_unread_counts`, `list_accounts`, `list_account_addresses`, `search_emails`, `get_email_by_id`, `get_email_by_ids`, `get_email_thread` |
| Organization | 6 | `list_mailboxes`, `create_mailbox`, `move_email`, `update_email_status`, `manage_trash`, `synchronize_account` |
| Composition | 7 | `compose_email`, `reply_to_email`, `forward_email`, `manage_drafts`, `verify_draft`, `verify_drafts`, `create_rich_email_draft` |
| Attachments | 2 | `list_email_attachments`, `save_email_attachment` |
| Smart inbox | 3 | `get_awaiting_reply`, `get_needs_response`, `get_top_senders` |
| Analytics and export | 4 | `get_statistics`, `export_emails`, `inbox_dashboard`, `full_inbox_export` (disabled; returns a bounded-alternative error) |
| Apple Calendar | 10 | `list_calendars`, `list_events`, `get_events_by_id`, `check_availability`, `create_event`, `batch_create_events`, `update_event`, `delete_events`, `manage_calendars`, `respond_to_invitation` (documented refusal) |

Reads are bounded: searches and listings scan at most 50 messages per call and page with an offset. Move, flag, and trash actions target exact message ids by default.

## Bundled skills (11)

Workflow skills load with the plugin and route requests to the right tools with safe defaults.

| Skill | Use it for |
|-------|------------|
| `apple-mail-operator` | Mail setup, account and mailbox introspection, safe read and search navigation |
| `inbox-triage` | A short daily scan of what needs a response and what is awaiting a reply |
| `email-management` | Sustained inbox zero programs across mailboxes |
| `mailbox-taxonomy` | Folder strategy and noise diagnosis before organizing |
| `email-archive-cleanup` | Staged archive, bulk move, and trash with dry runs |
| `mail-rules-advisor` | Mail rule and filter proposals (the plugin does not create rules) |
| `email-drafting` | Compose, reply, forward, rich HTML drafts, and draft verification |
| `email-style-profile` | Learn writing voice and signature habits from Sent mail |
| `email-attachments` | List and save attachments safely |
| `calendar-operator` | Bounded calendar reads, event creation and updates, ID-first deletes |
| `meeting-scheduler` | Find a slot, cross-timezone scheduling, invitation limits |

## Install

### Cursor

The manifest is `.cursor-plugin/plugin.json` and the MCP config is `mcp.json`. Once the plugin is listed on the Cursor Marketplace, install it from there. To load this directory directly from a checkout:

```bash
cursor-agent --plugin-dir /path/to/apple-mail-mcp/plugin
```

### Claude Code

```bash
claude plugin marketplace add Agentic-Assets/Agentic-Assets-Marketplace --scope user
claude plugin install apple-mail@agentic-assets --scope user
```

### Codex

```bash
codex plugin marketplace add https://github.com/Agentic-Assets/Agentic-Assets-Marketplace.git
codex plugin add apple-mail@agentic-assets
```

Claude Desktop and Cowork installs use the `.plugin` and `.mcpb` release artifacts described in the root README.

## Configuration

`start_mcp.sh` accepts `--draft-safe` (the default in every plugin manifest here) and `--read-only`. Optional environment variables set the default Mail account, signature, user preferences, and performance limits; the root README's Configuration section lists each one.

## Links

- [Project README](https://github.com/Agentic-Assets/apple-mail-mcp#readme)
- [Privacy](https://github.com/Agentic-Assets/apple-mail-mcp/blob/main/PRIVACY.md)
- [Security](https://github.com/Agentic-Assets/apple-mail-mcp/blob/main/SECURITY.md)
- [Changelog](https://github.com/Agentic-Assets/apple-mail-mcp/blob/main/CHANGELOG.md)
- [License (MIT)](https://github.com/Agentic-Assets/apple-mail-mcp/blob/main/LICENSE)
