# AGENTS.md

Navigation hub for **apple-mail-mcp**: one Python MCP server (**41 tools**, `fastmcp==3.4.1`) shipped as PyPI package (`mcp-apple-mail`), shared Claude Code, Codex, and Cursor plugin runtime (`plugin/`), Claude Desktop/Cowork `.plugin`, and Claude Desktop `.mcpb` (`apple-mail-mcpb/`). Marketplace entries: `.claude-plugin/marketplace.json` for Claude Code and `.agents/plugins/marketplace.json` for Codex Desktop/CLI. Cursor uses its distinct plugin-local adapter; local Cursor Agent acceptance has passed, while Cursor marketplace/UI admission remains a separate distribution check. The collected-test count is single-sourced in `tools/expected_test_count.txt` (the dev-check/release gate fails on drift and prints the new number); recount with `PYTEST_ADDOPTS='' .venv/bin/pytest --collect-only tests`.

## This repo is PUBLIC. Check what you commit.

`Agentic-Assets/apple-mail-mcp` is a **public** GitHub repo. Commits, branches, PR and issue comments, and release artifacts are world-readable the moment they are pushed, and a force-push does not unpublish them.

The point of this codebase is reading real mailboxes, so real mail data is always one live test away from the working tree. **Never commit:** real email addresses or contact details · real subjects, bodies, snippets, or `Message-ID` headers · account UUIDs, server hostnames, or mailbox URLs · absolute `/Users/<name>/...` paths, session scratch paths, or session IDs · secrets, tokens, or cookies · live-test output pasted verbatim.

Commit **counts, timings, and redacted samples** instead. Fixtures use synthetic addresses (`sender@example.com`). A number measured from a real mailbox is publishable; the message it came from is not.

**This is enforced, not advisory.** [`tools/validators/validate_no_committed_identity.py`](tools/validators/validate_no_committed_identity.py) scans every tracked text file and **exits non-zero** on a new email address at a non-placeholder domain, an absolute `/Users/<name>/...` path, or an uppercase account UUID. It runs in `bash tools/gates/dev-check.sh` (default and release tiers), so it runs on every commit through the pre-commit hook. Run it yourself before committing docs, `tasks/` artifacts, test fixtures, or anything carrying live-test output:

```bash
python3 tools/validators/validate_no_committed_identity.py
```

Hits that are already published are grandfathered per file in that validator's `KNOWN_IDENTITY_HITS` ratchet. Lowering a count is always a valid change; raising one is not — redact instead (synthetic address, elided path, placeholder UUID). Reserved and clearly synthetic placeholder domains never fire, so `sender@example.com` stays legal. Carry this constraint into every subagent prompt that writes files or commits.

## Marketplace identity boundary

[`tools/marketplace_identity.json`](tools/marketplace_identity.json) is the
machine-readable source-repository contract. The primary Agentic Assets
marketplace is `Agentic-Assets/Agentic-Assets-Marketplace`, with marketplace ID
`agentic-assets`, selector `apple-mail@agentic-assets`, and promoted payload
destination `plugins/apple-mail`. This repository owns the editable `plugin/`
source. The marketplace admits only immutable, allowlisted payload snapshots
from signed source tags and owns promotion policy, evidence, and attestations.

The root `.claude-plugin/marketplace.json` and
`.agents/plugins/marketplace.json` remain standalone development/public
compatibility catalogs named `apple-mail-mcp`, with selector
`apple-mail@apple-mail-mcp`. Never rename those catalogs to `agentic-assets`.
Keep client-specific schemas separate, and do not infer marketplace/UI support
from a static manifest or local adapter test.

## Distribution channels (five install surfaces from one source tree)

A single `plugin/` runtime serves Claude Code, Codex, and Cursor plugin installs; `bash tools/gates/build-artifacts.sh` emits the Claude Desktop upload artifacts. Drift between manifests and artifacts has caused real installer failures; `tools/validators/validate_manifests.py` enforces parity and the release gate refuses to ship with any artifact missing or stale.

| Surface | Install target | Format |
|---------|----------------|--------|
| `apple-mail-plugin.zip` | Claude Code standalone compatibility marketplace (`claude plugin install apple-mail@apple-mail-mcp`) | Plain zip, `.claude-plugin/plugin.json` at zip root; the central marketplace promotes the corresponding payload separately |
| `apple-mail.plugin` | Claude Desktop **Cowork → Customize → Add plugin → Upload plugin** | Byte-identical copy of the `.zip`, `.plugin` extension is what the Cowork UI accepts |
| `apple-mail-mcp-v{VERSION}.mcpb` | Claude Desktop chat extension via "Add Custom Plugin" / "Install from file" | DXT bundle (`mcpb pack`), `manifest.json` at zip root |
| `.agents/plugins/marketplace.json` + `plugin/.codex-plugin/plugin.json` | Codex Desktop/CLI standalone compatibility marketplace (`codex plugin add apple-mail@apple-mail-mcp`) | GitHub marketplace checkout points at shared `./plugin` runtime with `plugin/.mcp.json` |
| `plugin/.cursor-plugin/plugin.json` + `plugin/mcp.json` | Cursor plugin and local MCP adapter | Separate draft-safe Cursor adapter; local Cursor Agent acceptance passed, while marketplace/UI admission remains separate |

If you change distribution, version, or filenames: re-run `bash tools/gates/dev-check.sh release` and verify `tests/infra/test_validate_manifests.py` covers the change. **Never** ship a `.plugin` whose bytes differ from the `.zip` — the validator and local release tests treat that as a hard error.

Full detail: [`docs/CLAUDE-conventions.md`](docs/CLAUDE-conventions.md) § Distribution channels.

## Agent orchestration (required)

When the host exposes this repo's subagent tools, use subagents for both **research and implementation**, not just exploration. Delegate real fixes, tests, docs, and live verification to subagents; the lead agent orchestrates and reviews. If the host, task owner, or safety lane forbids subagents, do the work directly and state that constraint in the handoff.

| When | Subagent |
|------|----------|
| Code changes, tests, docs | `generalPurpose` |
| Codebase search / file discovery | `explore` |
| pytest, live CLI, shell tasks | `shell` |
| Independent workstreams | Run subagents **in parallel** |
| Dependent steps (e.g. perf gates before tool edits) | Run subagents **sequentially** |

Use plugin-dev experts for plugin, MCP, marketplace, and skill work when they are available; invoke before and after substantive changes:

| Expert | Use for |
|--------|---------|
| **`plugin-dev:plugin-validator`** | Manifest drift, tool counts, marketplace readiness |
| **`plugin-dev:skill-reviewer`** | Bundled skill descriptions, trigger overlap, safety language |
| Skills: **`plugin-dev:mcp-integration`**, **`plugin-dev:plugin-structure`**, **`mcp-builder`** | MCP server design, `.mcp.json` / `plugin.json`, tool quality |

Do not solo large plugin or perf workstreams without at least one plugin-dev expert pass unless the current host or task lane makes those experts unavailable; in that case, document the gap and run the repo's local validation gates.

**Run `code-simplifier:code-simplifier` regularly** — after any non-trivial change to tools, backend, helpers, or tests. Especially after refactors that touched many sites (e.g. capability-token / structured-error / bounded-scan work). Behavior must be preserved; the simplifier collapses duplication, drops dead branches, and tightens names. Trigger it as part of every "ready to ship" pass alongside `plugin-validator` and `skill-reviewer`, and any time a file grows past ~600 LOC or a helper sprouts >3 near-copies.

**Module line budget (automated):** the local hooks, `dev-check.sh`, and `validate_manifests.py` warn on modules over **600 LOC** in `plugin/apple_mail_mcp/` and `tools/`, and **fail** if a tracked file grows past its baseline (`tests/fixtures/module_line_budget/baseline.json`). Detail: [`docs/CLAUDE-conventions.md`](docs/CLAUDE-conventions.md) § Module line budget · [`tools/CLAUDE.md`](tools/CLAUDE.md) § `check_module_line_budget.py`.

## When working in…

| Area | Read |
|------|------|
| Plugin wrapper, `start_mcp.sh`, manifests | [`plugin/docs/CLAUDE.md`](plugin/docs/CLAUDE.md) |
| Package entry, `core/`, `server.py`, `cli/` | [`plugin/apple_mail_mcp/CLAUDE.md`](plugin/apple_mail_mcp/CLAUDE.md) |
| Individual MCP tools | [`plugin/apple_mail_mcp/tools/CLAUDE.md`](plugin/apple_mail_mcp/tools/CLAUDE.md) |
| Skills (11 workflow skills) | [`plugin/skills/CLAUDE.md`](plugin/skills/CLAUDE.md) |
| Tests & mocking AppleScript | [`tests/CLAUDE.md`](tests/CLAUDE.md) |
| Manifest validation, pre-commit | [`tools/CLAUDE.md`](tools/CLAUDE.md) |
| Live CLI testing, agent workflows | [`docs/CLAUDE.md`](docs/CLAUDE.md) |
| Deep tool/skill/plugin rules | [`docs/CLAUDE-conventions.md`](docs/CLAUDE-conventions.md) |
| Phase plans & backlog | [`tasks/CLAUDE.md`](tasks/CLAUDE.md) · [`tasks/todo.md`](tasks/todo.md) — **read `tasks/CLAUDE.md` § Agent requirements before adding or moving task files** |
| MCPB bundle build | [`apple-mail-mcpb/CLAUDE.md`](apple-mail-mcpb/CLAUDE.md) |
| Claude Code marketplace manifest | [`.claude-plugin/CLAUDE.md`](.claude-plugin/CLAUDE.md) |
| Codex Desktop/CLI plugin surface | [`.agents/plugins/marketplace.json`](.agents/plugins/marketplace.json) · [`plugin/.codex-plugin/plugin.json`](plugin/.codex-plugin/plugin.json) · [`plugin/.mcp.json`](plugin/.mcp.json) |
| Cursor plugin surface | [`plugin/.cursor-plugin/plugin.json`](plugin/.cursor-plugin/plugin.json) · [`plugin/mcp.json`](plugin/mcp.json) |

## Architecture (prose)

**Plugin wrapper** (`plugin/start_mcp.sh`, `plugin.json`) launches **Python package** (`plugin/apple_mail_mcp/`: `__main__` → import `tools/*` → register on `FastMCP` in `server.py`) which drives **Mail.app** through **`core.run_applescript()`** (stdin osascript, escaped user input, JSON-safe output). Dev venv: repo root `.venv/`; user plugin venv: `plugin/venv/` (install-time only).

## Dev setup

```bash
python3 -m venv .venv && .venv/bin/pip install -e . pytest
.venv/bin/pytest tests/                    # full suite (count tracked in tools/expected_test_count.txt)
python3 tools/validators/check_module_line_budget.py  # 600 LOC warn report (also runs in dev-check + CI)
bash tools/gates/dev-check.sh                    # manifests + module budget + pytest + test-count gate
.venv/bin/apple-mail quick-check --json    # live Mail smoke (~30s)
.venv/bin/python plugin/apple_mail_mcp.py --read-only
```

## Version bump (release together)

- `pyproject.toml` → `[project].version`
- `plugin/.claude-plugin/plugin.json` → `version`
- `plugin/.codex-plugin/plugin.json` → `version`
- `plugin/.cursor-plugin/plugin.json` → `version`
- `.claude-plugin/marketplace.json` → `plugins[0].version` (not `metadata.version`)
- `server.json` → top-level + `packages[0].version`
- `apple-mail-mcpb/manifest.json` → `version`

Sync tool-count claims in manifests with `find plugin/apple_mail_mcp/tools -name '*.py' | xargs grep -h '^@mcp.tool' | wc -l` (recursive: `compose/` is a package). Codex marketplace metadata lives in `.agents/plugins/marketplace.json` and points at `./plugin`; Codex MCP wiring lives in `plugin/.mcp.json`, should keep `--draft-safe`, and should launch via `cwd: "."` + `./start_mcp.sh` unless a fresh `bash tools/gates/validate-codex-plugin.sh` runtime smoke proves a different Codex contract. Before shipping, run `bash tools/gates/dev-check.sh release`; the gate enforces fatal `ruff check`, `ruff format --check`, and `mypy --strict` for `plugin/apple_mail_mcp/`, then exact plugin zip/MCPB payloads, byte parity between `apple-mail-plugin.zip` and `apple-mail.plugin`, package deps/packages, install contracts, source syntax, and artifact freshness. After a signed source tag is pushed, run `bash tools/gates/marketplace-handoff.sh vX.Y.Z`; it verifies the source handoff and prints the one Marketplace preparation command. Do not add new lint/type tools without asking.

## Related folders

`plugin/apple_mail_mcp/` (source of truth) · `plugin/` (shared Claude Code, Codex, and Cursor plugin runtime) · `.claude-plugin/` (Claude Code marketplace) · `.agents/plugins/` (Codex marketplace) · `apple-mail-mcpb/` · `tests/` · `tools/` · `docs/` · `tasks/`

**Repo agent skills:** Add under `.agents/skills/<name>/`; symlink `.claude/skills/<name>` → `../../.agents/skills/<name>` (not `.cursor/skills/`). Commit and push after adding or moving skills. Inventory, and which vendored skills carry local corrections a re-sync would discard: [`.agents/skills/README.md`](.agents/skills/README.md).
**Post-change ship:** Invoke `finalize-apple-mail-mcp` to sync docs, both root hubs (`AGENTS.md` and `CLAUDE.md`), and manifests, then commit and push when the user asks.
