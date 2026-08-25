# .claude-plugin/ — marketplace manifest

Top-level **Claude Code standalone compatibility marketplace** registration →
[`plugin/`](../plugin/) via `"source": "./plugin"`. Its public/development
identity is deliberately `apple-mail-mcp`, with selector
`apple-mail@apple-mail-mcp`; never rename it to `agentic-assets`.

Agentic Assets users install from the separate
[`Agentic-Assets/Agentic-Assets-Marketplace`](https://github.com/Agentic-Assets/Agentic-Assets-Marketplace)
catalog with the primary selector `apple-mail@agentic-assets`. The
machine-readable boundary is [`tools/marketplace_identity.json`](../tools/marketplace_identity.json).
This source repository owns editable `plugin/` development. The central
marketplace owns allowlisted signed-tag promotion, evidence, and attestations
for its immutable `plugins/apple-mail` snapshot.

Codex Desktop/CLI uses a separate marketplace file at [`../.agents/plugins/marketplace.json`](../.agents/plugins/marketplace.json) plus [`../plugin/.codex-plugin/plugin.json`](../plugin/.codex-plugin/plugin.json). Keep Claude and Codex plugin identities aligned, but do not add Codex fields to this Claude marketplace manifest.

## Two version fields

| Field | Example | Meaning |
|-------|---------|---------|
| `metadata.version` | `1.0.0` | **This marketplace JSON** — not the plugin. Don't bump on every release. |
| `plugins[0].version` | current plugin release | **Plugin release**: sync with `pyproject.toml`, `plugin.json`, `server.json`, and the MCPB manifest. |

`validate_manifests.sh` checks the marketplace `name` (`apple-mail-mcp`), `plugins[0].strict` is `true`, `plugins[0].version`, tool-count in `plugins[0].description`, `source`, any listed skill paths, plugin name/version parity with `plugin/.claude-plugin/plugin.json`, **and the dual-component-conflict rule below**.

## Components live in plugin.json — not here

Claude Code rejects the install with *"conflicting manifests: both plugin.json and marketplace entry specify components"* when both manifests declare any of `commands`, `agents`, `skills`, `hooks`, `mcpServers` and `strict` is not `true` on the marketplace entry.

This repo keeps **all** component specs (only `mcpServers` today) in `plugin/.claude-plugin/plugin.json`. The marketplace entry stays metadata-only and keeps `strict: true`, matching Claude's strict default and the plugin manifest's component declaration. Skills auto-discover from `plugin/skills/<name>/SKILL.md` — do not re-list them here.

The check lives in `tools/manifest_checks/install_contracts.py::_check_marketplace_contract` (entry point `tools/validators/validate_manifests.py`; regression tests in `tests/infra/test_validate_manifests.py`: `test_marketplace_contract_rejects_dual_component_declarations` and `test_marketplace_contract_allows_dual_components_when_strict_true`). If a future change needs component fields in the marketplace entry, set `"strict": true` in the same edit so installs keep working.

## Not here

- Plugin manifest → `plugin/.claude-plugin/plugin.json`
- Codex marketplace → `.agents/plugins/marketplace.json`
- Codex plugin manifest → `plugin/.codex-plugin/plugin.json`
- Desktop bundle → [`apple-mail-mcpb/`](../apple-mail-mcpb/)

## Install

```bash
# Primary Agentic Assets marketplace (users)
claude plugin marketplace add Agentic-Assets/Agentic-Assets-Marketplace --scope user
claude plugin marketplace update agentic-assets
claude plugin install apple-mail@agentic-assets --scope user

# Standalone compatibility marketplace (public development)
claude plugin marketplace add Agentic-Assets/apple-mail-mcp --scope user
claude plugin marketplace update apple-mail-mcp
claude plugin install apple-mail@apple-mail-mcp --scope user

# From repo checkout (maintainer/offline)
claude plugin marketplace add .
claude plugin install apple-mail@apple-mail-mcp
```

Installs the MCP server (41 tools, **`--draft-safe`** by default) plus **eleven** auto-discovered workflow skills under `plugin/skills/`; see [`plugin/skills/CLAUDE.md`](../plugin/skills/CLAUDE.md).

Codex users should also use the central marketplace identity:

```bash
codex plugin marketplace add https://github.com/Agentic-Assets/Agentic-Assets-Marketplace.git
codex plugin add apple-mail@agentic-assets
```

For standalone compatibility testing, register this repository and use
`apple-mail@apple-mail-mcp`. For a local checkout, use
`codex plugin marketplace add .` before that selector. Keep the central and
standalone registrations distinct so neither source can replace the other.

After edits: `plugin-dev:plugin-validator` when available + `tools/gates/validate_manifests.sh` (+ `plugin-dev:skill-reviewer` when skills change).

## Related

[`plugin/docs/CLAUDE.md`](../plugin/docs/CLAUDE.md) · [`apple-mail-mcpb/CLAUDE.md`](../apple-mail-mcpb/CLAUDE.md) · [`docs/CLAUDE-conventions.md`](../docs/CLAUDE-conventions.md)
