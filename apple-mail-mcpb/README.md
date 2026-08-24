# Apple Mail MCP bundle

Portable Apple Mail MCP server for Claude Desktop **plus** a mirrored **`skills/`** tree copied from [`plugin/skills`](https://github.com/Agentic-Assets/apple-mail-mcp/tree/main/plugin/skills) for Claude Code workflows.

## What is inside this archive

| Path | Role |
|------|------|
| `apple_mail_mcp/` + `apple_mail_mcp.py` | FastMCP tool implementation (**41 tools**) |
| `start_mcp.sh` | Creates `venv/`, installs only bundled hash-checked wheels, execs Python entry |
| `requirements.lock` + `wheelhouse/` | Offline runtime dependency payload for macOS arm64 CPython 3.13 |
| `ui/` | MCP Apps dashboard helpers for `inbox_dashboard` |
| `skills/` | Bundled Claude Code skills (`SKILL.md` per subdirectory) |

For grouped tool summaries, see the upstream [`README`](https://github.com/Agentic-Assets/apple-mail-mcp#readme).

## Claude Desktop install (.mcpb)

1. Claude Desktop → **Settings → Developer → MCP Servers → Install from file** → choose this `.mcpb`.
2. Approve Automation + Mail Data Access prompts when macOS asks.
3. Populate **Default Mail Account** / **Default Mail Signature** / **Email Preferences** in the MCP inspector when available.

Prefer **`--draft-safe`** for shared/agent hosts; manifests typically enable it by default — override only deliberately.

## Claude Code skills (manual sync)

Mirror the bundle's `skills/` directory into Claude Code (`~/.claude/skills`):

```
mkdir -p ~/.claude/skills
cp -a skills/. ~/.claude/skills/
```

Skills included (each subfolder owns a `SKILL.md`):

- `apple-mail-operator` — MCP + Mail navigation bootstrap
- `inbox-triage` — 5–10 minute read-first scan
- `email-management` — sustained Inbox Zero umbrella
- `mailbox-taxonomy` — folder taxonomy + noise diagnosis
- `email-archive-cleanup` — staged archive / bulk move / trash with dry runs
- `mail-rules-advisor` — Mail rule/filter proposals (**Mail UI apply only** — no MCP rule API)
- `email-drafting` — compose/reply drafts (`--draft-safe` aware)
- `email-style-profile` — derive voice prefs from Sent mail + `USER_EMAIL_PREFERENCES`
- `email-attachments` — list/save attachments with path safeguards
- `calendar-operator` — bounded calendar reads, safe event CRUD, and calendar troubleshooting
- `meeting-scheduler` — find-slot workflows, time-zone coordination, and invitation limits

Also copies `skills/CLAUDE.md` authoring notes — safe to ignore for runtime.

## Privacy Policy

This bundle's privacy policy is published at https://github.com/Agentic-Assets/apple-mail-mcp/blob/main/PRIVACY.md and is declared in `manifest.json` under `privacy_policies`.

## Operational notes

- Keep **`DEFAULT_MAIL_ACCOUNT`** set when multiple accounts fan out slowly.
- Set **`DEFAULT_MAIL_SIGNATURE`** to an exact Mail signature name when drafts should include your standard signature.
- Use narrow `recent_days` / caps before escalating cross-account AppleScript workloads.
- `export_emails`, `save_email_attachment`, compose send paths imply disk or dispatch risk — preview + confirm.

Support & source: https://github.com/Agentic-Assets/apple-mail-mcp
