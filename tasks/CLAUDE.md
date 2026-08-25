# tasks/ — planning and backlog

Cross-session planning artifacts. In-conversation work uses ephemeral task lists; **this folder survives between sessions**.

**Agents MUST read this file and [`todo.md`](todo.md) before creating or moving anything under `tasks/`.**

**This repo is PUBLIC.** Research notes, handoffs, and measurement writeups land here, and they are world-readable on push. Measured numbers are publishable; the mailboxes behind them are not. Keep out real addresses, subjects, account UUIDs, absolute `/Users/...` paths, and session scratch paths or session IDs. See root [`AGENTS.md`](../AGENTS.md) § This repo is PUBLIC.

## Agent requirements (mandatory)

Every coding agent working in this repo **must** follow the `tasks/` layout. GitHub-hosted Actions are disabled (see [`tests/CLAUDE.md`](../tests/CLAUDE.md) § Local gates vs live Mail), so enforcement is local: the checked-in pre-commit hook runs [`tools/gates/pre-commit-validate.sh`](../tools/gates/pre-commit-validate.sh) → `bash tools/gates/dev-check.sh default`, which runs [`tools/validators/validate_tasks_layout.py`](../tools/validators/validate_tasks_layout.py); [`tests/infra/test_tasks_layout.py`](../tests/infra/test_tasks_layout.py) enforces the same rule in pytest.

### Read order

1. [`todo.md`](todo.md) — see `tasks/todo.md` — current branch, active workstream, next action
2. [`INDEX.md`](INDEX.md) — see `tasks/INDEX.md` — browse `active/`, `reference/`, `archive/`
3. This file — placement rules below

### Layout (only these buckets)

| Path | Put here | Do not |
|------|----------|--------|
| **Root** (`tasks/`) | `CLAUDE.md`, `INDEX.md`, `todo.md` only | No loose `*.md`, no workstream folders at root |
| [`active/`](active/) | Open workstreams from the last ~30 days; one subfolder per lane | Do not archive here; do not leave shipped lanes forever |
| [`reference/`](reference/) | Durable specs/backlogs still cited by code, CHANGELOG, or docs | Do not dump ephemeral issue writeups here |
| [`archive/`](archive/) | Shipped, superseded, or resolved artifacts | **Never edit for current work** |

### When creating planning artifacts

1. **New workstream** — `tasks/active/<short-name>/` with dated files (`handoff-YYYY-MM-DD.md`, `phase-plan.md`, etc.).
2. **One-off issue tracker** — `tasks/active/<lane>/issue-<topic>-YYYY-MM-DD.md` while open; move to `tasks/archive/YYYY-MM/issues/` when resolved.
3. **Durable design spec** cited from code — `tasks/reference/<name>.md` only when it becomes long-lived policy.
4. **Update [`todo.md`](todo.md)** — point at the active handoff under `active/` (not a flat `tasks/foo.md` path).
5. **Update [`INDEX.md`](INDEX.md)** — add or move the row when a lane opens, ships, or archives.

### When shipping or superseding work

1. Move the folder or files to `tasks/archive/YYYY-MM/` (`shipped/` or `issues/` subfolders as appropriate).
2. Add one line to [`archive/README.md`](archive/README.md).
3. Remove the row from **Active** in [`INDEX.md`](INDEX.md) (or mark Reference/Archive).
4. Trim [`todo.md`](todo.md) to the next active pointer.

**Archive rule of thumb:** ship/supersede, or older than ~30 days with no `todo.md` pointer.

### Path references in code and docs

Use full bucket paths: `tasks/active/...`, `tasks/reference/...`, `tasks/archive/...`. Never reintroduce flat `tasks/<dated-file>.md` at the repo root of `tasks/`.

## Layout

| Path | Role |
|------|------|
| [`INDEX.md`](INDEX.md) | Navigation index (active, reference, archive) |
| [`todo.md`](todo.md) | Tiny active pointer |
| [`active/`](active/) | Open workstreams |
| [`reference/`](reference/) | Durable specs and baselines cited by code/docs |
| [`archive/`](archive/) | Shipped or superseded artifacts |

## Agent orchestration

When executing [`reference/apple-mail-plugin-robustness-goal-2026-05-22.md`](reference/apple-mail-plugin-robustness-goal-2026-05-22.md), [`reference/phase-plan-3.1.7.md`](reference/phase-plan-3.1.7.md), or [`todo.md`](todo.md):

- **Subagents for research and implementation when available and permitted**: delegate coding, tests, docs, and live runs; parallelize independent modules, sequence dependent phases. If the host or task lane forbids subagents, work directly and document that constraint.
- **Plugin-dev experts when available**: `plugin-dev:plugin-validator` and `plugin-dev:skill-reviewer` agents; plus `plugin-dev:mcp-integration`, `plugin-dev:plugin-structure`, and `mcp-builder` skills per phase plan. If unavailable, run local validation and record the gap.

## Active workstreams

The canonical list lives in [`INDEX.md`](INDEX.md) § Active workstreams (single source; do not duplicate it here). The forward roadmap is [`reference/roadmap-2026-07-10.md`](reference/roadmap-2026-07-10.md).

## Reference files

| File | Role |
|------|------|
| [`reference/roadmap-2026-07-10.md`](reference/roadmap-2026-07-10.md) | Forward roadmap (new tools/skills, enhancements, hardening, refusals) |
| [`reference/id-first-refactor-spec.md`](reference/id-first-refactor-spec.md) | Shipped ID-first design (v3.7.0) |
| [`reference/phase-3-annotation-matrix.md`](reference/phase-3-annotation-matrix.md) | Canonical tool-annotation matrix (`server.py` `ToolAnnotations` presets); cited from `plugin/apple_mail_mcp/tools/CLAUDE.md` § Add a tool |
| [`reference/apple-mail-plugin-robustness-goal-2026-05-22.md`](reference/apple-mail-plugin-robustness-goal-2026-05-22.md) | Active robustness goal |
| [`reference/robustness-backlog-2026-05-22.md`](reference/robustness-backlog-2026-05-22.md) | Backlog sidecar |
| [`reference/phase-plan-3.1.7.md`](reference/phase-plan-3.1.7.md) | Historical release sequencing |
| [`reference/live-test-baseline-2026-05-21.md`](reference/live-test-baseline-2026-05-21.md) | Live perf numbers |
| [`reference/mcp-mailbox-timeout-audit-2026-05-22.md`](reference/mcp-mailbox-timeout-audit-2026-05-22.md) | Timeout audit |

## Archive

Superseded plans live under [`archive/`](archive/). **Do not edit archived files for current work.**

## Production test account

Use **`cayman@agenticassets.ai`** for perf gates (194 mailboxes). **`ai.openclaw`** is light regression only.

```bash
export DEFAULT_MAIL_ACCOUNT="cayman@agenticassets.ai"
.venv/bin/apple-mail perf-test --json   # routine core battery
# Heavy analysis only with explicit opt-in:
.venv/bin/apple-mail perf-test --include-analysis --allow-heavy-mail-scan --json
```

## Maintenance

- After `tools/*.py`: `.venv/bin/pytest tests/ -q` (count single-sourced in `tools/expected_test_count.txt`)
- After manifests/package/artifact changes: `bash tools/gates/dev-check.sh release` + `plugin-dev:plugin-validator` when available
- After skills: `plugin-dev:skill-reviewer` when available (+ manifest validator if marketing copy changed)
- **Module line budget:** `python3 tools/validators/check_module_line_budget.py` (also in `dev-check.sh` + CI); refresh baseline only after intentional splits — [`docs/CLAUDE-conventions.md`](../docs/CLAUDE-conventions.md) § Module line budget
- Live workflow: [`docs/AGENT_LIVE_TESTING.md`](../docs/AGENT_LIVE_TESTING.md)
- Engineering rules: [`docs/CLAUDE-conventions.md`](../docs/CLAUDE-conventions.md)

## Related

- Root overview: [`CLAUDE.md`](../CLAUDE.md) → [`tasks/CLAUDE.md`](CLAUDE.md) link in table
