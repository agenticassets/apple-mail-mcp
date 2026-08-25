# plugin/ — shared plugin install surface

**Shared Claude Code, Codex, and Cursor install surface** — registers the MCP server, ships skills, bootstraps user-local venv. Tool logic lives in `apple_mail_mcp/`; see root `CLAUDE.md` for server architecture.

## Agent orchestration

Plugin/MCP/skill changes: delegate implementation to subagents when available and permitted; run **`plugin-dev:plugin-validator`** and **`plugin-dev:skill-reviewer`** (and `plugin-dev:mcp-integration` / `plugin-dev:plugin-structure` skills) before merge when those experts are available. If not, document the gap and run the local validation gates. See root [`CLAUDE.md`](../../CLAUDE.md), Agent orchestration section.

## Key files

| File | Role |
|------|------|
| `.claude-plugin/plugin.json` | Plugin manifest: `mcpServers` (includes `--draft-safe` in server args by default), keywords, version |
| `.codex-plugin/plugin.json` | Codex plugin manifest: interface metadata, `skills: "./skills"`, `mcpServers: "./.mcp.json"` |
| `.mcp.json` | Codex MCP config launching `/bin/bash ./start_mcp.sh --draft-safe` with `cwd: "."` |
| `.cursor-plugin/plugin.json` | Cursor plugin manifest pointing to `mcp.json` |
| `mcp.json` | Cursor MCP config launching `/bin/bash ${CURSOR_PLUGIN_ROOT}/start_mcp.sh --draft-safe` |
| `start_mcp.sh` | Self-healing offline venv bootstrap (see below) + `fastmcp` import verify, then exec server |
| `apple_mail_mcp.py` | Thin entry shim → `apple_mail_mcp.__main__.main()` |
| `requirements.lock` + `wheelhouse/` | Hash-locked runtime deps installed into `plugin/venv/` (not root `.venv/`) |

## MCP wiring

```
Claude Code → /bin/bash ${CLAUDE_PLUGIN_ROOT}/start_mcp.sh → plugin/venv/bin/python3 apple_mail_mcp.py
Codex      → cwd=<installed plugin root> /bin/bash ./start_mcp.sh → plugin/venv/bin/python3 apple_mail_mcp.py
Cursor     → /bin/bash ${CURSOR_PLUGIN_ROOT}/start_mcp.sh → plugin/venv/bin/python3 apple_mail_mcp.py
```

`${CLAUDE_PLUGIN_ROOT}` resolves to this `plugin/` directory for Claude Code,
and `${CURSOR_PLUGIN_ROOT}` resolves to the installed plugin root for Cursor.
Codex 0.133.0 does **not** expand either host variable inside argv; keep Codex
`.mcp.json` on the `cwd: "."` + `./start_mcp.sh` contract unless a runtime
smoke proves a new Codex launcher shape.

`plugin.json` passes **`--draft-safe`** to `start_mcp.sh` by default so send tools stay blocked in shared agent workspaces. Override in user MCP config only when intentional.

## Venv self-healing (`start_mcp.sh`)

The plugin venv at `plugin/venv/` is **self-healing** — no manual rebuild when Homebrew or system Python upgrades break the interpreter symlink. The offline release matrix is macOS arm64 with CPython 3.13; a missing compatible interpreter fails closed instead of downloading packages.

| Flag | Behavior |
|------|----------|
| *(default)* | `ensure_venv`: create venv on first run; rebuild from scratch if the interpreter is missing/broken **or** the venv does not match `requirements.lock`; then exec server. A still-unimportable `fastmcp` after that rebuild **fails closed** (exit 1), it is not reinstalled again |
| `--ensure-only`, `--check`, `--doctor` | Build/repair venv and verify imports, then **exit 0** without launching the server (installers / health checks) |

Repair triggers: dangling `venv/bin/python3` (Python removed/upgraded), missing venv, or a `venv/.requirements.lock.sha256` marker that no longer matches `requirements.lock`. Reinstall is offline only — `pip install --no-index --find-links wheelhouse --require-hashes -r requirements.lock` — and a missing lock or empty wheelhouse fails closed rather than downloading. Logs go to stderr (`[Apple Mail MCP] …`) for Claude Desktop / Code logs.

Fresh-install test: run `bash tools/gates/verify-offline-runtime.sh plugin` or unpack a release artifact and run the same command against it.

## Subfolders

- **`apple_mail_mcp/`**: Python package (source of truth for all 41 MCP tools)
- **`skills/`**: Procedural workflows (eleven shipped workflow skills; see `skills/CLAUDE.md`)
- **`ui/`** — Inbox dashboard HTML via `mcp-ui-server` (`dashboard.py`, `templates/`)

## Related distribution shapes

- **`../../tools/marketplace_identity.json`** — Identity boundary: central marketplace `agentic-assets` and selector `apple-mail@agentic-assets`; standalone compatibility marketplace `apple-mail-mcp` and selector `apple-mail@apple-mail-mcp`; immutable allowlisted signed-tag promotion into `plugins/apple-mail`
- **`../../.claude-plugin/marketplace.json`** — Top-level Claude Code standalone development/public compatibility manifest (`name`: `apple-mail-mcp`); `plugins[0].source` → `./plugin` inside this GitHub checkout; do not rename it to the central identity
- **`../../.agents/plugins/marketplace.json`** — Top-level Codex standalone development/public compatibility manifest (`name`: `apple-mail-mcp`); `plugins[0].source` → `./plugin` inside this GitHub checkout; do not rename it to the central identity
- **`../../apple-mail-mcpb/`** — Claude Desktop `.mcpb` bundle build (separate manifest)

Agentic Assets user installs come from
`Agentic-Assets/Agentic-Assets-Marketplace` with selector
`apple-mail@agentic-assets`. That repository owns promotion policy, evidence,
and attestations for the promoted payload. This repository remains the editable
source of truth. Do not edit a promoted marketplace payload to fix source code.

## When to change what

- **Manifest edits** (`plugin.json`, marketplace, mcpb, Codex `.mcp.json`): bump version in all versioned files (see root `CLAUDE.md`); preserve the standalone `apple-mail-mcp` identity, keep `.agents/plugins/marketplace.json` pointed at `./plugin`, and keep `plugin/.mcp.json` draft-safe unless intentionally changing send semantics; run **`plugin-dev:plugin-validator`** before merge when available.
- **Cursor adapter edits** (`.cursor-plugin/plugin.json`, `mcp.json`): retain the separate local launcher contract and do not claim Cursor support until a live client acceptance test passes.
- **Launcher / deps**: edit `start_mcp.sh`, `requirements.txt`, or `pyproject.toml`; keep plugin and PyPI dependencies/packages aligned (`mcp-ui-server`, `plugin/ui`); use `bash tools/gates/verify-offline-runtime.sh plugin` for the standard non-destructive fresh-install check. Delete the scoped gitignored `plugin/venv/` only for an explicit repair test, then run `bash tools/gates/dev-check.sh release`.
- **New MCP tools**: implement under `apple_mail_mcp/tools/` and register in `apple_mail_mcp/__init__.py` — not in this wrapper layer.
- **New user entry points**: add skills under `skills/` only. Do not restore `commands/`; release validation fails if the retired legacy command directory reappears.
- **Venvs**: `plugin/venv/` = user install (gitignored); `../../.venv/` = dev pytest/editable install.
