# tools/ — validation scripts

Dev-infra guardrails — not MCP tools (`plugin/apple_mail_mcp/tools/` is the server).

| Subfolder | Holds |
|-----------|-------|
| [`gates/`](gates/) | Shell entry-point gates invoked locally by pre-commit, pre-push, and `.claude/hooks` |
| [`validators/`](validators/) | Python validators invoked by the local gates and tests |
| [`manifest_checks/`](manifest_checks/) | Manifest check implementations (imported by `validators/validate_manifests.py`) |
| [`probes/`](probes/) | Research / smoke / patch helpers (not gates, not validators) |
| [`release/`](release/) | Local source-release trust helpers backing the release gates: `source_release.py` (create/verify immutable signed source-release tags), `pre_push.py` (require a current release-gate stamp for sensitive pushes), `marketplace_handoff.py` (print the verified Marketplace handoff) |

Root keeps `expected_test_count.txt` (single source of truth for the collected-test
count), [`marketplace_identity.json`](marketplace_identity.json) (marketplace
identity and promotion boundary), [`marketplace_payload.py`](marketplace_payload.py)
(deterministic payload evidence for a source-owned marketplace promotion; covered by
`tests/infra/test_marketplace_payload.py`), and this index.

## Marketplace identity

`tools/marketplace_identity.json` declares the primary central marketplace
`agentic-assets`, selector `apple-mail@agentic-assets`, source payload
`plugin/`, and destination `plugins/apple-mail`. Promotion must start from an
immutable signed tag in this allowlisted repository. The central marketplace
owns promotion policy, evidence, and attestations; promoted payloads are not
edited directly.

The same file locks this repository's root Claude and Codex marketplace
manifests to their standalone development/public compatibility identity:
`apple-mail-mcp` and `apple-mail@apple-mail-mcp`. Validators for those root
manifests must continue to enforce that identity. They must not be changed to
the central `agentic-assets` identity.

## Marketplace refresh helpers

| Helper | Audience | Contract |
|--------|----------|----------|
| [`gates/refresh-central-marketplace.sh`](gates/refresh-central-marketplace.sh) | Primary Agentic Assets users | Preflight, install, or refresh `apple-mail@agentic-assets` from `Agentic-Assets/Agentic-Assets-Marketplace`; never removes marketplaces, plugins, caches, or user data |
| [`gates/refresh-local-plugins.sh`](gates/refresh-local-plugins.sh) | Maintainers and standalone public-development users | Refresh the compatibility selector `apple-mail@apple-mail-mcp` from this source repository without changing the central marketplace |

Run `bash tools/gates/refresh-central-marketplace.sh --check` for a read-only
central preflight, then omit `--check` to apply. The central helper verifies
Claude Code and Codex registrations and runtime bootstrap. It is not proof of
Cursor marketplace/UI admission.

## Marketplace release handoff

After a source release tag is pushed, run
`bash tools/gates/marketplace-handoff.sh vX.Y.Z`. It verifies the remote signed
tag and prints the bound commit, signed source inventory hash, target, selector, and exact
Marketplace preparation command. It is read-only; admission, evidence, and
attestation remain Marketplace-owned. See
[`docs/marketplace-release-handoff.md`](../docs/marketplace-release-handoff.md).

## sync_skill_references

| Script | Role |
|--------|------|
| `validators/sync_skill_references.py` | Copy canonical `plugin/skills/references/*.md` into each packaged skill's `references/` folder |

Packaged Claude/Codex skills only expose files inside each `plugin/skills/<name>/` directory. Shared refs are edited once under `plugin/skills/references/`, then synced:

```bash
python3 tools/validators/sync_skill_references.py          # write copies
python3 tools/validators/sync_skill_references.py --check  # CI / dev-check parity gate
```

Enforced by `tests/infra/test_packaged_skill_paths.py` (link escape + byte parity).

## validate_tasks_layout

| Script | Role |
|--------|------|
| `validators/validate_tasks_layout.py` | Enforces `tasks/` bucket layout (`active/`, `reference/`, `archive/`); covered by `tests/infra/test_tasks_layout.py` |

Agents must read `tasks/CLAUDE.md` § Agent requirements before creating or moving planning artifacts. The gate fails when:

1. Loose `*.md` or workstream folders appear at `tasks/` root (only `CLAUDE.md`, `INDEX.md`, `todo.md` allowed).
2. `active/`, `reference/`, or `archive/` is missing.
3. `tasks/CLAUDE.md` or `tasks/INDEX.md` drops required layout markers.
4. `tasks/todo.md` links to stale flat paths like `tasks/foo-2026-06-30.md`.

```bash
python3 tools/validators/validate_tasks_layout.py
```

Runs in `bash tools/gates/dev-check.sh` (default and release tiers).

## validate_repo_root

| Script | Role |
|--------|------|
| `validators/validate_repo_root.py` | Enforces a tight allowlist at the repository root; covered by `tests/infra/test_repo_root.py` |

Ephemeral reports, dated handoffs, and scratch artifacts belong under `docs/`, `tasks/active/`, `tasks/reference/`, or `tasks/archive/`, not loose at the repo root. The gate fails when unexpected non-hidden files or directories appear at top level. Allowed root files: navigation manifests (`AGENTS.md`, `CLAUDE.md`, `README.md`, etc.), `pyproject.toml`, `server.json`, `skills-lock.json`, and versioned release artifacts (`apple-mail-mcp-v{semver}.mcpb`, `apple-mail-plugin.zip`, `apple-mail.plugin`). Allowed top-level dirs: `apple-mail-mcpb`, `archive`, `docs`, `plugin`, `tasks`, `tests`, `tools`. Hidden dirs `.agents`, `.claude`, `.claude-plugin`, `.codex`, `.cursor`, `.github`, `.git` are allowed; other dotfiles and dotdirs (`.venv`, `.pytest_cache`, etc.) are ignored.

```bash
python3 tools/validators/validate_repo_root.py
```

Runs in `bash tools/gates/dev-check.sh` (default and release tiers).

## validate_no_committed_identity

| Script | Role |
|--------|------|
| `validators/validate_no_committed_identity.py` | Fails closed on personal/machine identity committed into this public repo; covered by `tests/infra/test_no_committed_identity.py` |

Root [`AGENTS.md`](../AGENTS.md) § This repo is PUBLIC used to describe a manual `git diff --cached | grep`. This makes it a gate. Files come from `git ls-files -z`; three rules run over every tracked text file:

1. **Email address** whose domain is not RFC 2606/6761 reserved (`example.com`, `example.invalid`, …) or a known synthetic placeholder (`company.com`, `bar.com`, `x.com`, …). Real provider, publisher, `.edu`, company, and personal domains are **not** allowlisted — existing ones are grandfathered per file so new ones fail.
2. **Absolute `/Users/<name>/…` path** with a real-looking username segment. A bare `/Users/` mention with no following segment is prose and never fires, which is why the gate can scan its own rulebook without a file-level exemption.
3. **Uppercase account/calendar UUID** (`[0-9A-F]{8}-…-[0-9A-F]{12}`). Uppercase-only on purpose: Apple Mail account directories and EventKit item identifiers are uppercase, while case-insensitive would sweep in every lowercase plugin/bundle/MCPB id.

Skips only `plugin/wheelhouse/` (vendored wheels) and `archive/` (frozen history), plus binary/archive suffixes, `.coverage`, and any blob with a NUL in its first 8 KiB. **`.agents/` is deliberately in scope** — it holds this repo's own first-party skill sources, so excluding it was a 68-file blind spot over files we write; do not re-add it (`tests/infra/test_no_committed_identity.py` asserts a scanned-file floor that fails if you do). Enforcement is a **ratchet**: `KNOWN_IDENTITY_HITS` (repo-relative path → occurrence count) grandfathers what is already published, and any file over its count — or any unlisted file with a hit — fails. Lowering a number is always valid; raising one is not. A separate staleness test forces a redacted hit out of the baseline so the ratchet only tightens. Violation output names file, line, and rule but never echoes the matched value.

```bash
python3 tools/validators/validate_no_committed_identity.py
```

Runs in `bash tools/gates/dev-check.sh` (default and release tiers).

## validate_manifests

| Script | Role |
|--------|------|
| `gates/validate_manifests.sh` | Bash entry; **CI calls this** |
| `validators/validate_manifests.py` | Python orchestrator (`main`); covered by `tests/infra/test_validate_manifests.py` |
| `manifest_checks/` | Check implementations grouped by concern (see below) |

The individual checks live in the sibling `manifest_checks/` package (`common.py` for the shared `ROOT`/constants/helpers, then `version.py`, `tool_count.py`, `install_contracts.py`, `codex.py`, `cursor.py`, `artifacts.py`, `module_budget.py`); `validators/validate_manifests.py` imports and orchestrates them in `main` and re-exports them so the test suite keeps calling `validate_manifests.<check>`. `validate_manifests.ROOT` forwards to `manifest_checks.common.ROOT`, so monkeypatching it still redirects every check.

Enforces (source of truth: `pyproject.toml` `[project].version` and `[project].name`):

1. **Version sync** — Claude/Codex/Cursor `plugin.json`, Claude marketplace `plugins[0].version`, `server.json` (×2), `apple-mail-mcpb/manifest.json`
2. **Tool count claims**: descriptions must match a recursive scan of `plugin/apple_mail_mcp/tools/` for `^@mcp.tool` (package-nested tools such as `compose/` count toward **41**)
3. **MCPB name parity** — `@mcp.tool` names ↔ `apple-mail-mcpb/manifest.json` `tools[]`
4. **Install contracts** — Claude plugin `mcpServers`, Codex `.mcp.json`, marketplace `source`/skills, MCPB `server` config, `server.json` package metadata, and PyPI package deps/packages must point at the shipped runtime
5. **Payload syntax** — `plugin/start_mcp.sh` and shipped Python files must parse before release
6. **Artifact freshness and exactness** — when `apple-mail-plugin.zip` or `apple-mail-mcp-v{version}.mcpb` exists locally, archive members must match current tracked payload bytes, with no unexpected stale files
7. **Archive structural integrity** — plugin zip and `.mcpb` must contain no duplicate members and no zero-byte directory entries (names ending in `/`); raw `zip -r .` produces entries that installers can reject. Build with `tools/gates/build-artifacts.sh` / `mcpb pack` or `zip -D`.
8. **Release artifact presence** — opt in with `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1` to require **all three** local distributables before shipping: `apple-mail-plugin.zip`, `apple-mail.plugin`, and `apple-mail-mcp-v{version}.mcpb`
9. **Plugin/.plugin byte parity** — `apple-mail.plugin` must exist alongside `apple-mail-plugin.zip` and be byte-identical. The `.plugin` extension is the canonical Cowork "Add plugin → Upload plugin" artifact; drifting bytes break the Cowork upload silently. Always rebuild via `tools/gates/build-artifacts.sh`, which copies the canonical zip to the `.plugin` name.
10. **Marketplace ↔ plugin.json component conflict** — requires `.claude-plugin/marketplace.json plugins[0].strict` to be `true`, and fails if both that entry and `plugin/.claude-plugin/plugin.json` declare any of `commands`, `agents`, `skills`, `hooks`, `mcpServers` while marketplace `strict` is not `true`. Mirrors the Claude Code "conflicting manifests" install error. See [`.claude-plugin/CLAUDE.md`](../.claude-plugin/CLAUDE.md) § "Components live in plugin.json" for the rule and escape hatch.
11. **Codex plugin surface** — `.agents/plugins/marketplace.json` must point at `./plugin`; `plugin/.codex-plugin/plugin.json` must expose `skills: "./skills"` and `mcpServers: "./.mcp.json"`; `plugin/.mcp.json` must launch `/bin/bash ./start_mcp.sh --draft-safe` with `cwd: "."`.
12. **Cursor plugin surface** — `plugin/.cursor-plugin/plugin.json` must point at `./mcp.json`; `plugin/mcp.json` must launch `/bin/bash ${CURSOR_PLUGIN_ROOT}/start_mcp.sh --draft-safe`. Cursor has its own validator and must not inherit Codex's relative-path contract. This is a static adapter check, not live Cursor acceptance proof.
13. **Stale distribution artifacts** — fails if repo root contains `apple-mail-mcp-v*.mcpb` files other than the current `pyproject.toml` version; run `tools/gates/build-artifacts.sh` to prune and rebuild.
14. **Module line budget** — warns on modules over **600 LOC** in `plugin/apple_mail_mcp/` and `tools/` (not `tests/`); **fails** on baseline regression (`tests/fixtures/module_line_budget/baseline.json`, currently empty after v3.9.1). Covered by `tests/infra/test_module_line_budget.py` and `validators/check_module_line_budget.py`.

```bash
bash tools/gates/validate_manifests.sh
APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/gates/validate_manifests.sh
```

Codex runtime registration is checked separately:

```bash
bash tools/gates/validate-codex-plugin.sh
```

That smoke installs the plugin into a temporary `CODEX_HOME`, reads `codex mcp get apple-mail --json`, launches the registered stdio server, and fails unless MCP `list_tools` includes `reply_to_email`, `compose_email`, `manage_drafts`, `list_accounts`, and `get_inbox_overview`.

Skips Claude marketplace `metadata.version` (1.0.0) and Codex marketplace release versioning because `.agents/plugins/marketplace.json` is install routing metadata — see [`.claude-plugin/CLAUDE.md`](../.claude-plugin/CLAUDE.md).

**Standalone compatibility contract:** marketplace slug `apple-mail-mcp`,
plugin selector `apple-mail@apple-mail-mcp`. The primary `agentic-assets`
identity and `apple-mail@agentic-assets` selector belong to
[`Agentic-Assets/Agentic-Assets-Marketplace`](https://github.com/Agentic-Assets/Agentic-Assets-Marketplace),
not this source repo. Primary refreshes use
[`gates/refresh-central-marketplace.sh`](gates/refresh-central-marketplace.sh).
Canonical commands live in
[`.claude-plugin/CLAUDE.md`](../.claude-plugin/CLAUDE.md) and root
[`README.md`](../README.md). To refresh an existing standalone development
install, run [`gates/refresh-local-plugins.sh`](gates/refresh-local-plugins.sh).
It preflights sources, installs and verifies the lowercase compatibility target,
and never removes or changes the separate shared marketplace; a conflicting
direct-source registration fails closed.

## check_wrapper_surface.py

| Script | Role |
|--------|------|
| `validators/check_wrapper_surface.py` | Generated mcporter wrapper command-surface check; covered by `tests/infra/test_wrapper_surface.py` |

Separate from **`validate_manifests`** — manifest validation checks Python `@mcp.tool` ↔ MCPB `tools[]` parity, but the generated `apple-mail` wrapper on PATH embeds schemas at generation time and can drift when new tools are added.

Verifies critical read commands (`get-email-by-id`, `search-emails`, `get-email-thread`, `list-inbox-emails`, `get-inbox-overview`) appear in `apple-mail --help`. Exit 0 when all present; exit 1 when missing. Skips gracefully (exit 0) if no wrapper on PATH.

```bash
python3 tools/validators/check_wrapper_surface.py
python3 tools/validators/check_wrapper_surface.py --wrapper /path/to/apple-mail
```

Run after regenerating the mcporter bundle or adding read tools agents rely on.

## check_module_line_budget.py

| Script | Role |
|--------|------|
| `validators/check_module_line_budget.py` | 600 LOC budget scanner for `plugin/apple_mail_mcp/` and `tools/`; covered by `tests/infra/test_module_line_budget.py` |

Warn-only CLI (exit 0) listing oversized production modules; **regression** enforced in pytest and `validate_manifests.py` via `tests/fixtures/module_line_budget/baseline.json` (empty `modules` when nothing exceeds 600 LOC).

```bash
python3 tools/validators/check_module_line_budget.py
python3 tools/validators/check_module_line_budget.py --write-baseline tests/fixtures/module_line_budget/baseline.json
```

Runs automatically in `dev-check.sh` (default/release), `validate_manifests.sh` (regression), CI (dedicated step + pytest `-rw`), and pre-commit.

## measure_metadata_hydration.py

| Script | Role |
|--------|------|
| `probes/measure_metadata_hydration.py` | Read-only exact-id timing helper for Phase 4a metadata-index feasibility |

Measures header-read and attachment-count hydration costs for exact Mail message ids. It is not an MCP tool and must not be run casually: it requires `--confirm-read-only-live-mail`, sends nothing, creates no drafts, and prints only aggregate timings/counts. It does not print message contents, headers, senders, subjects, recipient addresses, attachment names, or raw message ids.

```bash
python3 tools/probes/measure_metadata_hydration.py \
  --account "$DEFAULT_MAIL_ACCOUNT" \
  --mailbox INBOX \
  --message-ids "12345,67890" \
  --repeats 3 \
  --confirm-read-only-live-mail
```

Use only with known dummy or approved exact ids. The output is suitable for deciding whether Phase 4b metadata-index hydration is worth implementing, but it is not a runtime cache and does not mutate Mail.

## inspect_envelope_index_schema.py

| Script | Role |
|--------|------|
| `probes/inspect_envelope_index_schema.py` | Schema-only Envelope Index research helper for Phase 4a risk assessment |

Inspects only SQLite schema metadata from Mail's local Envelope Index: table names, column names/types, index names/columns, and a schema fingerprint. It is not an MCP tool, does not read message rows, redacts the file path, and requires `--confirm-read-only-live-mail-index`.

```bash
python3 tools/probes/inspect_envelope_index_schema.py \
  --confirm-read-only-live-mail-index
```

Use this only to assess permission and schema-drift risk before any future direct-index backend work. Do not use it as a runtime query path or include its output in package artifacts.

## dev-check.sh

Tiered local gate (no live Mail except `live` tier). Requires root `.venv/`.

| Tier | Runs |
|------|------|
| `default` | `validate_no_committed_identity.py` **first** (cheapest step, and the only failure that is irreversible once pushed) + `validate_manifests.sh` + `validate_tasks_layout.py` + `validate_repo_root.py` + module line budget report + `pytest` + `run_test_count_check`; adds `check_wrapper_surface.py` when **staged** files touch `plugin/apple_mail_mcp/tools/`, tool registration, or MCPB `manifest.json` |
| `lint` | Fatal package quality gate: `ruff check plugin/apple_mail_mcp/`, `ruff format --check plugin/apple_mail_mcp/`, and `mypy --strict plugin/apple_mail_mcp/` |
| `surface` | default + wrapper check always |
| `manifest` | manifests only |
| `live` | default + `.venv/bin/apple-mail quick-check --json` |
| `release` | `validate_no_committed_identity.py` first (fail before spending a build on a tree that cannot ship), then `lint`, the module-line-budget warn report, then `tools/gates/build-artifacts.sh` (rebuilds `apple-mail-plugin.zip` + `apple-mail.plugin` + `.mcpb`, then runs `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS` validate and `mcpb unpack`/`validate` smoke), `validate_tasks_layout.py` + `validate_repo_root.py`, pytest, `run_test_count_check`, wrapper, and finally `verify-offline-runtime.sh` against **both** built artifacts (the zip and the versioned `.mcpb`). The tiers are not supersets of each other, so a new validator must be added to both `run_default()` and the `release)` arm. **Run before every commit that touches `plugin/`, manifests, `pyproject.toml`, `requirements.txt`, or release artifacts** — finalize-apple-mail-mcp skill enforces this. |
| `all` | default + wrapper check always |

```bash
bash tools/gates/dev-check.sh
bash tools/gates/dev-check.sh surface
bash tools/gates/dev-check.sh release   # always before commit/PR
```

### Collected-test count (single source of truth)

`tools/expected_test_count.txt` holds the one canonical collected-test count. Docs no
longer hardcode the number; `run_test_count_check` (in `default` and `release`)
recomputes the real count with `PYTEST_ADDOPTS='' pytest --collect-only tests` and fails
on drift, printing the new number to drop into that one file. The tool count (41) is
already derived/enforced separately by `validate_manifests`.

## pre-commit hook

Install in every local or cloud coding checkout before its first commit or push:

```bash
bash tools/gates/install-git-hooks.sh
test "$(git config --get core.hooksPath)" = ".githooks"
```

Runs `bash tools/gates/dev-check.sh default` on every commit (committed-identity scan first, then manifests + tasks layout + repo root + module budget + pytest + test-count check; wrapper check when staged tool surface changes). Manual equivalent:

```bash
bash tools/gates/pre-commit-validate.sh
```

## Local CI-equivalent blockers

GitHub-hosted Actions are intentionally disabled. The checked-in pre-commit and
pre-push hooks run the CI-equivalent validation in the coding checkout. The
pre-push hook requires an exact current release-gate stamp for release-sensitive
changes. Live Mail remains manual
([`docs/AGENT_LIVE_TESTING.md`](../docs/AGENT_LIVE_TESTING.md)).

Run after tool add/remove, version bump, mcpb `tools[]` edit, or plugin skill marketing copy in manifests. Supplement with **`plugin-dev:plugin-validator`** when available; add **`plugin-dev:skill-reviewer`** when editing `plugin/skills/*/SKILL.md`.

## Related

[`apple-mail-mcpb/CLAUDE.md`](../apple-mail-mcpb/CLAUDE.md) · [`.claude-plugin/CLAUDE.md`](../.claude-plugin/CLAUDE.md) · [`.agents/plugins/marketplace.json`](../.agents/plugins/marketplace.json) · [`docs/CLAUDE-conventions.md`](../docs/CLAUDE-conventions.md)
