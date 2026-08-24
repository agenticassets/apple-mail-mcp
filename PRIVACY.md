# Privacy Policy for Apple Mail MCP

**Effective date:** 2026-08-24
**Publisher:** Agentic Assets LLC
**Applies to:** the `apple-mail` plugin for Claude Code, Codex, and Cursor, the Claude Desktop `.plugin` and `.mcpb` bundles, and the Python package built from the [`Agentic-Assets/apple-mail-mcp`](https://github.com/Agentic-Assets/apple-mail-mcp) repository.

This policy describes what the software does with your data. Every statement below is written against the source code of the release it ships with. If the code and this document ever disagree, the code is what runs, and we treat the disagreement as a bug to fix (see "Changes to this policy").

## Summary

- Apple Mail MCP runs entirely on your Mac as a local process started by your AI client. It reads and writes Mail.app and Calendar.app through AppleScript.
- The server code opens no network connections of its own. It does not contact Agentic Assets or any other server, and it has no account, sign-in, or API key.
- There is no telemetry, no analytics, no advertising, and no sale or sharing of your data. Agentic Assets receives nothing from your installation.
- The AI client you run it in sends tool inputs and outputs to that client's model provider under the provider's own privacy terms. This plugin does not control that.
- Sending email is blocked by default in every plugin install (`--draft-safe`). Drafts are created for you to review and send yourself.

## 1. What the software is

Apple Mail MCP is a Model Context Protocol (MCP) server. Your AI client (for example Claude Desktop, Claude Code, Codex, or Cursor) launches it as a child process on your Mac and talks to it over standard input and output. It exposes tools for reading, searching, organizing, and drafting email and for reading and managing calendar events. The server exits on its own when the client that started it goes away.

## 2. Data the software accesses on your Mac

### Mail.app

Through AppleScript, the tools can read your configured Mail accounts and their email addresses, mailbox names and counts, message headers (sender, recipients, subject, dates, Message-ID), read and flag status, message bodies, attachment names and contents, and existing drafts. Tools can also create drafts, move messages between mailboxes, change read and flag status, create mailboxes, move messages to Trash or delete them (subject to the caps in section 6), ask Mail to synchronize an account, and, only when sending is enabled, send messages.

### Calendar.app

Through AppleScript, the tools can read your calendars and events (titles, times, locations, notes, attendees, and status) and can create and update events. Deleting events and sending attendee invitations are gated (section 6).

If the optional `eventkit` extra is installed (adding `pyobjc-framework-EventKit`) and macOS already reports Full Access to Calendars for the host application, calendar reads can use Apple's EventKit framework instead of AppleScript. The software never triggers the EventKit consent prompt itself; that prompt belongs only to the human-invoked `apple-mail calendar-grant` command.

### What it does not access

- The software does not open Mail's on-disk message store or its Envelope Index database, and it contains no SQLite code. Everything it knows about your mail comes from Mail.app through AppleScript. A future opt-in local metadata cache is described in a contract module in the source, but it is not wired into any tool and writes nothing.
- It does not read your keychain, browser data, contacts, or files outside the paths described in section 4.

### macOS permissions

On first use, macOS asks you to allow the host application to control Mail and Calendar (Automation) and to access Mail data. The software works within those grants. It does not ask for Full Disk Access.

## 3. What leaves your Mac

### The server itself: nothing

The server code contains no HTTP client and opens no sockets. It has no update check, crash reporter, usage counter, or "phone home" of any kind. The only URL handling in the server is building local `message://` links for search results.

### Things that do use the network, none of them run by the server

1. **Your AI client and its model provider.** Everything a tool returns (which can include full message bodies, sender and recipient addresses, subjects, attachment names, calendar details, and any `USER_EMAIL_PREFERENCES` text you configure) goes back to the client that called the tool, and the client sends it to its model provider to produce a response. That transfer is governed by the provider's privacy policy and your agreement with them, not by this policy. Agentic Assets does not receive or see that traffic.
2. **Mail.app and Calendar.app.** They keep talking to your email and calendar providers exactly as they do without this plugin. Some tools ask them to act: `synchronize_account` asks Mail to sync with the server, a send (only when enabled) makes Mail transmit the message, and calendar invitation sends (blocked in draft-safe mode) go through Calendar.app.
3. **The optional inbox dashboard.** `inbox_dashboard` with `output_format="ui"` (its default) returns an HTML page. A host that renders MCP Apps loads the MCP Apps SDK script for that page from `cdn.jsdelivr.net`. That is a script download; the CDN sees the ordinary request metadata for a file fetch, such as your IP address, and no mailbox data is included in the request. Your inbox data is embedded in the page locally, and the page's actions go back through the host, not to any network host. Call `inbox_dashboard(output_format="json")` if you do not want that fetch.

### Installation

The self-contained plugin payload installs its Python dependencies from a bundled, hash-locked wheelhouse with `pip --no-index --require-hashes` and never downloads packages at startup. Installing the Python package from a source checkout (`pip install .`) downloads its dependencies from PyPI at install time, as any Python package install does. The PyPI listing currently named `mcp-apple-mail` is published by the original upstream author, not by Agentic Assets, and is not covered by this policy.

## 4. Files the software writes on your Mac

- **Cross-process lock.** A zero-length file named `mail-ui.lock` in a private cache directory under your home folder (`Library/Caches/apple-mail-mcp/`). It serializes Mail automation across every plugin host you run. It contains no data, the directory is created readable only by you, and the software refuses to run Mail automation if that lock cannot be trusted.
- **Rich HTML draft files.** When you call `create_rich_email_draft` without an `output_path`, the generated `.eml` message is written to `Library/Caches/apple-mail-mcp/rich-drafts/` under your home folder, named after the subject. These files contain the draft's content and are not deleted automatically. You can delete that folder at any time.
- **Temporary body files.** Compose, reply, forward, and draft verification write the message body to a temporary file in the system temporary directory for the duration of one call and delete it when the call finishes, including on failure.
- **Exports and attachments.** `export_emails`, `save_email_attachment`, and `create_rich_email_draft(output_path=...)` write only when you ask and only to the path you give. The path must be inside your home folder and may not be inside `.ssh`, `.gnupg`, `.config`, `.aws`, `.claude`, `Library/LaunchAgents`, `Library/LaunchDaemons`, or `Library/Keychains`; anything else is refused before any file is written.
- **Dependencies.** The plugin launcher creates a Python virtual environment (`venv/`) inside the plugin's own install directory. It holds only the software's dependencies.
- **Log files: none.** The software writes no log files. Diagnostic messages go to the server's standard error stream, which the host application may keep in its own logs under the host's policy.

## 5. Telemetry, analytics, advertising, and sale of data

None. The software has no telemetry or analytics of any kind, shows no advertising, and Agentic Assets does not collect, sell, rent, or share your data because it never receives it. The tools grouped under "Analytics & Export" compute mailbox statistics (top senders, volume by day, unread counts) locally and return them to your client only.

## 6. Sending email and destructive actions

- **Draft-safe mode is the default for every plugin install.** The Claude Code, Codex, and Cursor plugin manifests and the Claude Desktop `.mcpb` bundle all start the server with `--draft-safe`. In this mode `compose_email`, `reply_to_email`, and `forward_email` save drafts (or open them for review) and refuse `mode="send"`; `manage_drafts(action="send")` is refused; calendar event creation and updates still work, but calendar deletes are refused unless the operator sets the `CALENDAR_ALLOW_DESTRUCTIVE=1` environment variable, and attendee invitation sends are refused.
- **Read-only mode** (`--read-only`) removes the send tools (`compose_email`, `reply_to_email`, `forward_email`) and every calendar write and delete tool from the server entirely, and implies draft-safe. Drafts can still be listed, created, and deleted through `manage_drafts`; sending one is blocked.
- **Mail-side moves, status changes, and trash are not mode-gated.** They are governed by per-call caps (50 moves, 10 status updates, 5 trash actions by default) and by `dry_run` previews; `manage_trash` defaults to `dry_run=True`, so nothing is deleted unless a call explicitly overrides it.
- **Bare server installs run with sending enabled unless you pass a flag.** Running the server directly from a source checkout, or through a manual `start_mcp.sh` registration, starts it with no mode flag. Add `--draft-safe` or `--read-only` to the command if you want the same protection the plugin installs give you.

## 7. Configuration values you provide

`DEFAULT_MAIL_ACCOUNT`, `DEFAULT_MAIL_SIGNATURE`, `DEFAULT_CALENDAR`, `CALENDAR_ALLOW_DESTRUCTIVE`, and `USER_EMAIL_PREFERENCES` are read from environment variables that your host application stores in its own configuration files. `USER_EMAIL_PREFERENCES` is appended to the descriptions of preference-aware tools, so its text becomes part of the tool list your client sends to its model provider. Do not put secrets in it.

## 8. Retention

The server keeps nothing between calls except the files listed in section 4. Message data lives in the server's memory only while a tool call is running and is returned to your client. Uninstalling the plugin and deleting `Library/Caches/apple-mail-mcp/` under your home folder removes everything the software created.

## 9. Children

Apple Mail MCP is a general-audience developer and productivity tool. It is not directed at children under 13, and we do not knowingly collect information from them (the software collects no information from anyone).

## 10. Changes to this policy

Changes are recorded in [`CHANGELOG.md`](CHANGELOG.md) and in the repository's git history, so every revision of this document is visible with its date and diff. The effective date at the top of this file is updated with each change.

## 11. Contact

- Questions and bug reports: [GitHub Issues](https://github.com/Agentic-Assets/apple-mail-mcp/issues)
- Security vulnerabilities (private): [GitHub Security Advisories](https://github.com/Agentic-Assets/apple-mail-mcp/security/advisories/new)
- Publisher: [Agentic Assets LLC](https://agenticassets.ai). Company privacy policy: [agenticassets.ai/privacy](https://agenticassets.ai/privacy). Terms: [agenticassets.ai/terms](https://agenticassets.ai/terms).
