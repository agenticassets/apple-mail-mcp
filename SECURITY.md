# Security Policy

## Supported versions

Security fixes go into the latest release only. Check the [latest GitHub Release](https://github.com/Agentic-Assets/apple-mail-mcp/releases/latest) and update if you are behind; older versions are not patched.

## Reporting a vulnerability

Report vulnerabilities privately through [GitHub Security Advisories](https://github.com/Agentic-Assets/apple-mail-mcp/security/advisories/new). Do not open a public issue, pull request, or discussion for a security problem, and do not include real mail content, addresses, or account identifiers in a report (this repository is public, and so are its issues).

A useful report includes the version (`pip show mcp-apple-mail`, the plugin manifest, or the `.mcpb` filename), the host client and install method, the tool and arguments involved with any personal data redacted, and what an attacker could do with the flaw. We will acknowledge the report, work with you on a fix, ship it in a release with a `CHANGELOG.md` entry, and credit you if you want credit.

Scope: the code in this repository, which is the Python server, the `apple-mail` CLI, the plugin launcher (`plugin/start_mcp.sh`), the bundled skills, and the `.plugin` and `.mcpb` build. Bugs in Mail.app, Calendar.app, macOS, the host AI client, or a model provider belong with those vendors, but tell us if this project could work around one.

## Safety model

Apple Mail MCP runs on your Mac and drives Mail.app and Calendar.app through AppleScript. The controls below are what the code enforces; the [privacy policy](PRIVACY.md) covers what data it touches and what leaves the machine.

- **No network surface.** The server opens no sockets and has no HTTP client. It is launched by the host client over standard input and output and exits when that client goes away.
- **Draft-safe by default.** Every plugin manifest (Claude Code, Codex, Cursor, and the Claude Desktop `.mcpb`) starts the server with `--draft-safe`. Compose, reply, and forward tools save drafts and refuse `mode="send"`; `manage_drafts(action="send")` is refused; calendar deletes are refused unless the operator sets `CALENDAR_ALLOW_DESTRUCTIVE=1` in the environment (an agent cannot grant itself that mid-session); attendee invitation sends are refused. Bare `uvx` and `pip` installs must pass the flag themselves.
- **Read-only mode.** `--read-only` removes the three send tools and every calendar write and delete tool from the server, and implies draft-safe.
- **Gated destructive mail operations.** `move_email`, `update_email_status`, and `manage_trash` are not mode-gated, so they are governed by caps and previews instead: 50 moves, 10 status updates, and 5 trash actions per call by default; `manage_trash` defaults to `dry_run=True`, so nothing is deleted or moved to Trash unless a call explicitly overrides it. Action tools take exact `message_ids` and reject subject or sender selectors; date and bulk filter scans require `allow_filter_scan=True`.
- **Write paths are confined.** Exports, saved attachments, and generated `.eml` files may only be written under the user's home folder and never inside `.ssh`, `.gnupg`, `.config`, `.aws`, `.claude`, `Library/LaunchAgents`, `Library/LaunchDaemons`, or `Library/Keychains`. Control characters in paths are rejected.
- **AppleScript input is escaped.** User-supplied strings are escaped before they are placed in scripts, scripts are passed to `osascript` on standard input rather than the command line, and long bodies travel through temporary files that are deleted when the call ends.
- **Serialized, fail-closed Mail automation.** All AppleScript calls take an in-process lock and a per-user cross-process advisory lock so concurrent hosts cannot interleave Mail UI transactions. The lock lives in a private cache directory; if that directory or file is not owned by the user with the expected permissions, the server refuses to automate Mail rather than proceed.
- **Bounded scans.** Search and list tools scan at most 50 messages per call and refuse unbounded windows, which limits what a single prompt can pull from a large mailbox.
- **Offline, hash-locked dependencies.** The plugin payload installs from a bundled wheelhouse with `pip --no-index --require-hashes` and never downloads code at startup.
- **No secrets in the public repo.** `tools/validators/validate_no_committed_identity.py` fails the commit on real email addresses, absolute home paths, and account UUIDs, and runs on every commit through the local gate.

## Verifying a checkout

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" pytest
bash tools/gates/dev-check.sh release
```

The release gate runs lint, strict type checks, the full test suite, manifest parity, and artifact freshness before anything ships.
