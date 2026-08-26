---
name: "finalize-apple-mail-mcp"
description: "Final codebase review and doc/manifest sync for apple-mail-mcp after feature work. Starts with plugin-validator to fix manifest and doc drift, then pytest, code-simplifier, CLAUDE.md/README/skills/MCPB sync, a required `dev-check.sh release` artifact rebuild, then commit and push, and — once Cayman approves the merge — merging, signing the release tag, and attaching artifacts to the GitHub Release, because a merged-but-untagged version is not a closed loop. Use when finishing a change, before release, when the user says finalize, sync docs, update manifests, ship the branch, merge to main, tag a release, or cut a release."
---

# Finalize apple-mail-mcp

Run this **after implementation is done** and before calling the branch finished. Orchestrate with subagents; do not solo large doc/manifest sweeps.

## When to use

- User finished a feature/fix and wants docs, guides, and manifests aligned
- User says: finalize, ship, sync docs, update CLAUDE.md, validate manifests, pre-release check
- Before opening a PR or tagging a release

## Recommended skills for the change being finalized

Pick by what the diff actually touched; don't run all of them. Each is
either a Skill (run inline) or an Agent (delegate via Task). The dev-mode
hook in `.claude/hooks/dev_mode_reminder.sh` reflects the same map.

| If the diff touched… | Use |
|----------------------|-----|
| AppleScript inside Python f-strings (`tools/*/` packages, `core/` package) | The `.claude/hooks/check_applescript_compiles.py` parse check fires automatically on edit. Live-verify before ship against the **production test account** named in [`tasks/CLAUDE.md`](../../../tasks/CLAUDE.md) § Production test account — see the **live-verification rule** below the table. |
| Perf-sensitive paths (`tools/smart_inbox/`, `tools/analytics/`, large-inbox loops) | `python-performance-optimization` skill |
| Timeout subdivision, retry/backoff, `AppleScriptTimeout` handling | `python-resilience` skill |
| Silent `except` / `on error` skips, `errors[]` surfacing, partial-failure JSON | `python-error-handling` skill |
| New tests, missing test coverage, parser-vs-script gaps | `testing-python` or `python-testing-patterns` skill |
| `asyncio` fan-out, `asyncio.run()`-in-loop bugs | `async-python-patterns` skill |
| Pre-ship review pass | `reviewing-code` + `code-review` skills; `python-anti-patterns` as checklist |
| Confirming a change actually works in the running app | `run` skill, then the live-CLI procedure in [`docs/AGENT_LIVE_TESTING.md`](../../../docs/AGENT_LIVE_TESTING.md) |
| Plugin manifest / marketplace / MCPB drift | `plugin-dev:plugin-validator` agent (REQUIRED; step 1 below) |
| `plugin/skills/*/SKILL.md` wording or triggers | `plugin-dev:skill-reviewer` agent |

### Live-verification rule (an empty result is not a pass)

Verify against an account that is **currently reachable**. Set
`DEFAULT_MAIL_ACCOUNT` to the production test account documented in
[`tasks/CLAUDE.md`](../../../tasks/CLAUDE.md) § Production test account, confirm
it is live, then run a bounded check:

```bash
.venv/bin/apple-mail accounts --json                     # confirm the account is present and reachable
.venv/bin/apple-mail awaiting-reply --account "$DEFAULT_MAIL_ACCOUNT" --days 7 --limit 5
```

**A zero-row result from an unreachable account is NOT a pass.** A retired or
disconnected account (the former TU Exchange mailbox is the known example — that
server access has ended and it is no longer reachable) returns a confident empty
result that looks identical to "the AppleScript ran and found nothing." Never
verify against it: a gate that cannot fail is worse than no gate. Require the
account to appear in `apple-mail accounts` **and** the check to return real rows
before calling AppleScript changes verified.

## Out of scope

- New feature implementation
- Version bump across the seven version files unless user explicitly requests a release
- Force push or amending pushed commits

## Workflow

Copy and track:

```
Finalize progress:
- [ ] 1. plugin-validator: run and fix all reported issues
- [ ] 2. Scope the diff (what changed, why)
- [ ] 3. Code + tests verified
- [ ] 4. code-simplifier: pass over the diff (REQUIRED for any non-trivial change)
- [ ] 5. Docs, CLAUDE.md, skills, manifests synced (remaining drift)
- [ ] 5b. tasks/ hygiene: if planning artifacts moved or a workstream shipped: follow `tasks/CLAUDE.md` § Agent requirements; update `todo.md` + `INDEX.md`; run `python3 tools/validators/validate_tasks_layout.py`
- [ ] 6. skill-reviewer (if plugin/skills touched)
- [ ] 7. Rebuild release artifacts: `bash tools/gates/dev-check.sh release` (rebuilds **all three** artifacts: `apple-mail-plugin.zip` + `apple-mail.plugin` + `apple-mail-mcp-v{VERSION}.mcpb`, runs full validators including byte-parity check, runs mcpb unpack smoke). NEVER skip this step.
- [ ] 8. Final review checklist
- [ ] 9. Commit and push (default: yes, after release tier is green; open a PR if the branch is protected)
- [ ] 10. Merge (needs Cayman's explicit approval), then tag and release if the change carries a version. **A pushed branch is not a finished change** — stamp AFTER the merge, never before.
```

### 1. plugin-validator first (required)

**Delegate immediately** to `plugin-dev:plugin-validator` (Task `subagent_type="plugin-dev:plugin-validator"` — the bare name `plugin-validator` is not a valid agent id). Do not run pytest or doc sweeps before this step completes.

Prompt must include:

- Full validation pass (manifests, tool counts, versions, MCPB parity, plugin structure)
- **Fix every blocker and every fixable warning** in-repo (doc test counts, stale MCPB descriptions, manifest args drift, etc.)
- Re-run `bash tools/gates/validate_manifests.sh` and report PASS/FAIL after fixes

If the validator reports **FAIL** or cannot fix something, stop finalize and surface blockers to the user. Do not proceed to step 2 until plugin-validator ends at **PASS** or the user accepts known exceptions.

### 2. Scope the change

```bash
git status
git log --oneline -5
git diff main...HEAD --stat
```

Identify touched areas: `plugin/apple_mail_mcp/tools/`, `plugin/skills/`, `tests/`, manifests, `README.md`, `docs/`.

### 3. Verify code (delegate to `shell` subagent)

From repo root with `.venv/`:

```bash
.venv/bin/pytest tests/ -q
bash tools/gates/validate_manifests.sh
python3 tools/validators/validate_tasks_layout.py
python3 tools/validators/check_module_line_budget.py   # 600 LOC warn report (regression enforced in pytest + manifests)
.venv/bin/pytest tests/infra/test_module_line_budget.py tests/infra/test_validate_manifests.py tests/infra/test_tasks_layout.py -q
```

Optional when tools or CLI changed:

```bash
bash tools/gates/pre-commit-validate.sh
.venv/bin/apple-mail quick-check --json   # live Mail smoke (~30s)
```

All must pass before updating any remaining doc claims.

### 4. code-simplifier (REQUIRED for any non-trivial change)

Delegate to the **`code-simplifier:code-simplifier`** agent (Task
`subagent_type="code-simplifier:code-simplifier"`). This is non-optional
for any change beyond a one-line bugfix; root `CLAUDE.md` § Agent
orchestration mandates it as part of every "ready to ship" pass.

Scope the agent to the **recently-modified files** in the diff (it
defaults to recent changes; pass explicit paths when the diff is large):

- Behavior must be preserved; pytest after the simplifier pass must
  match the pytest results from step 3.
- The simplifier collapses duplication, drops dead branches, tightens
  names; it does NOT redesign abstractions.
- Especially important after refactors touching many call sites
  (capability-token, structured-error, bounded-scan-style work), any
  file that grew past ~600 LOC, or any helper with >3 near-copies.
- If the simplifier returns edits, re-run pytest before continuing.

Skip only when: the diff is a one-line bugfix, a manifest version bump,
or docs-only edits with zero Python changed.

### 5. Sync documentation (delegate to `generalPurpose` subagent)

Update **only** what the code change still affects after step 1. Do not rewrite unrelated files.

| If you changed… | Update |
|-----------------|--------|
| MCP tools (`@mcp.tool`, params, defaults) | `plugin/apple_mail_mcp/tools/CLAUDE.md`, tool docstrings, `README.md` tool table, `docs/CLAUDE-conventions.md`, `apple-mail-mcpb/manifest.json` `tools[].description` |
| Plugin wiring / flags | `plugin/docs/CLAUDE.md`, `plugin/apple_mail_mcp/CLAUDE.md`, `README.md` Configuration |
| Agent workflows | `plugin/skills/*/SKILL.md`, `plugin/skills/CLAUDE.md`, `docs/CLAUDE.md` skill map |
| Planning / task artifacts | `tasks/todo.md`, `tasks/INDEX.md`, `tasks/active/` (see `tasks/CLAUDE.md` § Agent requirements) |
| Test count | `tools/expected_test_count.txt` only (SSOT); after adding/removing tests run `PYTEST_ADDOPTS='' .venv/bin/pytest --collect-only tests` and update that file — dev-check fails on drift and prints the new number. Do not scatter counts in prose docs. |
| Module line budget | After intentional splits: `python3 tools/validators/check_module_line_budget.py --write-baseline tests/fixtures/module_line_budget/baseline.json`; do not refresh merely to allow growth |
| Tool count | The seven version files only on release; always sync **claims**: `find plugin/apple_mail_mcp/tools -name '*.py' | xargs grep -h '^@mcp.tool' | wc -l` (recursive — every tool surface is a package, so a flat `tools/*.py` glob silently returns 0) vs `plugin.json`, marketplace, MCPB `tools[]` |

**Hub files to spot-check** (stale cross-links or wrong counts):

- `AGENTS.md` (root, canonical) **and** `CLAUDE.md` (root) — these two have drifted before around the § Version bump / tool-count paragraph; diff them against each other, do not update only one
- `plugin/docs/CLAUDE.md`, `plugin/apple_mail_mcp/CLAUDE.md`, `plugin/apple_mail_mcp/tools/CLAUDE.md`
- `plugin/skills/CLAUDE.md`, `tests/CLAUDE.md`, `tools/CLAUDE.md`, `docs/CLAUDE.md`
- `.claude-plugin/CLAUDE.md`, `apple-mail-mcpb/CLAUDE.md`, `tasks/CLAUDE.md`

**Manifest rules** (see `tools/CLAUDE.md`):

- Versions — **seven files**: `pyproject.toml`, `plugin/.claude-plugin/plugin.json`, `plugin/.codex-plugin/plugin.json`, `plugin/.cursor-plugin/plugin.json`, `.claude-plugin/marketplace.json` `plugins[0].version`, `server.json` (**both** the top-level `version` **and** `packages[0].version` — `validate_manifests.py` checks each separately), `apple-mail-mcpb/manifest.json`
- The Cursor manifest is enforced like the rest — `tools/validators/validate_manifests.py` checks `plugin/.cursor-plugin/plugin.json` `version`; omitting it fails the gate
- Do **not** bump `metadata.version` in marketplace.json
- MCPB `tools[]` names must match registered tool function names

### 6. skill-reviewer (if plugin skills touched)

If step 5 edited any `plugin/skills/*/SKILL.md`, delegate to `plugin-dev:skill-reviewer` and apply wording fixes.

### 7. Rebuild release artifacts (required; never skip)

**Three artifacts must regenerate together** from current sources before commit. All three ship with the repo, and drift between any of them has caused real installer failures.

| Artifact | Install path | Why drift breaks users |
|----------|--------------|------------------------|
| `apple-mail-plugin.zip` | Claude Code plugin marketplace | Stale bytes → users get an older tool surface than the manifest claims |
| `apple-mail.plugin` | Cowork → Customize → Add plugin → Upload plugin | Missing or diverged from the `.zip` → Cowork upload silently fails or installs stale code |
| `apple-mail-mcp-v{VERSION}.mcpb` | Claude Desktop chat "Add Custom Plugin" | Wrong version filename or directory entries → Desktop installer aborts |

```bash
bash tools/gates/dev-check.sh release
```

That tier runs, in this order:

1. **Identity scan** — `tools/validators/validate_no_committed_identity.py`. Fails on a new real email address, absolute `/Users/<name>/…` path, or uppercase account UUID in any tracked text file.
2. **Lint (fatal)** — `ruff check` + `ruff format --check` + `mypy --strict` over `plugin/apple_mail_mcp/`. These are hard failures on this tier, not warnings; needs `.venv/bin/pip install -e '.[dev]'`.
3. **Module line budget** warn report (the 600 LOC *regression* is hard-gated inside `validate_manifests.py`).
4. **`tools/gates/build-artifacts.sh`** — the rebuild, detailed below.
5. **`validate_tasks_layout.py`** and **`validate_repo_root.py`** (a single stray untracked file at the repo root fails this).
6. **pytest** + the **test-count gate** against `tools/expected_test_count.txt` (fails on drift and prints the new number).
7. **Wrapper-surface check** (`check_wrapper_surface.py`).
8. **`tools/gates/verify-offline-runtime.sh`** against **both** `apple-mail-plugin.zip` and `apple-mail-mcp-v{VERSION}.mcpb`.

Step 4 (`build-artifacts.sh`) is what actually regenerates the three artifacts:

1. Prune stale `apple-mail-mcp-v*.mcpb` at repo root (keeps only the current `pyproject.toml` version).
2. Rebuild `apple-mail-plugin.zip` with the README exclusion list (`venv`, `__pycache__`, `*.pyc`, `.DS_Store`, `CLAUDE.md`, `.env*`, logs, temp/backup files).
3. Copy the zip bytes to `apple-mail.plugin` so the Cowork artifact stays byte-identical to the marketplace zip.
4. Rebuild `apple-mail-mcp-v{VERSION}.mcpb` via `apple-mail-mcpb/build-mcpb.sh` (which prefers official `mcpb pack`).
5. Re-run `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/gates/validate_manifests.sh`; fails if any of the three artifacts is missing, stale older `.mcpb` bundles remain, or the `.plugin` bytes diverge from the `.zip`.
6. Run `mcpb unpack` + `mcpb validate` as an MCPB structural smoke (skipped with a printed notice if neither `mcpb` nor `npx` is available).
7. Unzip `apple-mail-plugin.zip` and run `claude plugin validate --strict` on it (skipped with a printed notice if the `claude` CLI is not on `PATH`). Cowork promotes warnings to errors, so `--strict` is the mode that matches the installer.

Because pytest runs *after* the rebuild on this tier, a green run also proves the artifacts were built from a tree that passes the suite.

If any step fails, fix the underlying issue; do not commit stale artifacts. **Never delete `apple-mail.plugin` or build it manually**; it must come from the build script's byte-copy, not a hand-zip, or the parity check rejects it.

### 8. Final review checklist

- [ ] plugin-validator PASS after fixes
- [ ] code-simplifier pass complete (or explicitly skipped per step 4 exceptions); pytest still green afterward
- [ ] `tools/gates/dev-check.sh release` finished green (artifacts rebuilt, `mcpb unpack` smoke OK, `claude plugin validate --strict` OK when CLI is available)
- [ ] `apple-mail-plugin.zip`, `apple-mail.plugin`, and `apple-mail-mcp-v{VERSION}.mcpb` modified time newer than every changed plugin source
- [ ] `apple-mail.plugin` bytes == `apple-mail-plugin.zip` bytes (validator enforces; manual check: `cmp apple-mail-plugin.zip apple-mail.plugin`)
- [ ] Behavior described in docs matches `tools/compose/` package defaults and other tool packages
- [ ] No stale "open by default" or subject-matching guidance where `message_id` is preferred
- [ ] No skill suggests `compose_email` / `create_rich_email_draft` / `manage_drafts(action="create")` for replies; `standalone_confirmed=True` is documented where standalone-with-Re: is legitimate
- [ ] `email-drafting` and `apple-mail-operator` skills agree with README draft-safe section
- [ ] `tasks/INDEX.md` and `tasks/todo.md` updated if a workstream opened, shipped, or archived
- [ ] `python3 tools/validators/validate_tasks_layout.py` passes (no loose files at `tasks/` root)
- [ ] No secrets or local paths committed
- [ ] Unrelated dirty files left unstaged

### 9. Commit and push (default: yes; close the loop yourself)

Once steps 1-8 are green, **commit and push without waiting to be asked**. The user's standing preference is that finalize closes its own loop. Pause and ask only when there is genuine ambiguity (unrelated WIP in the tree, secrets in staged paths, partial implementation, or a force-push would be required).

Stage focused paths; never `git add -A`.

```bash
git add <relevant paths>
git commit -m "$(cat <<'EOF'
<1-2 sentences: why, not what>

EOF
)"
```

**Push** as the closing action of finalize:

```bash
git push -u origin HEAD
```

If `HEAD` is on a protected branch (e.g. `main` with branch-protection rules), switch to a feature branch and open a PR with `gh pr create` instead; same default-to-action principle.

### 10. Merge, tag, and release (required; the loop is not closed at "pushed")

**A pushed branch is not a finished change.** Finalize ends when the work is on
`main` and, if it carries a version, when that version is tagged and released.
Stopping at "PR opened" leaves a merged-but-untagged version behind, which is
how `v3.12.0` sat untagged across four PRs.

**Merging needs Cayman's explicit approval** ("merge to main", "Cayman approved
this merge", or equivalent). Never use GitHub native auto-merge. Ask for it as
the closing action rather than leaving the PR open silently.

Once approved, the order below is not stylistic — each step invalidates the one
before it:

```bash
gh pr merge <N> --squash --repo Agentic-Assets/apple-mail-mcp   # or --merge
git checkout main && git fetch origin && git pull --ff-only origin main
bash tools/gates/source-release-gate.sh          # MUST come after the merge
bash tools/gates/create-release-tag.sh           # preview; no signing
bash tools/gates/create-release-tag.sh --confirm-create
git push origin vX.Y.Z
bash tools/gates/marketplace-handoff.sh vX.Y.Z
```

**Why that order.** The gate's stamp binds HEAD's *commit SHA*, not its tree.
Stamping on the feature branch and then merging produces a merge commit with an
identical tree but a new SHA, so the stamp reads stale and the tag push is
refused. Stamping before the merge is wasted work — budget ~7 minutes for the
run and do it once, afterward.

**Three preconditions that fail late if unmet:**

- **A completely clean checkout, including untracked and *gitignored* files.**
  `validate_repo_root.py` scans the filesystem, so a gitignored file still
  fails it. `uv.lock` is the recurring one: this repo uses pip + `.venv`, but
  any `uv run` whose cwd sits inside the tree walks up, finds the root
  `pyproject.toml`, and writes `uv.lock` there — an external MCP server doing
  this is enough. Park it outside the tree rather than deleting it (shared
  checkout, and it may be another agent's), then re-run the gate.
- **`user.signingkey` must be set *and* its key loaded.**
  `source_release.py create_tag` runs `git -c gpg.format=ssh tag -s`, which
  forces the format but not the key. The key is machine-local and does not
  travel. If it is passphrase-protected and absent from `ssh-agent`, signing
  fails outright — `ssh-add` is the user's to run; never handle the passphrase.
- **HEAD must equal both the fetched and the live `origin/main`.** Run
  `create-release-tag.sh` with no flags first; it previews and prints the exact
  target SHA without signing anything.

Finally, attach `apple-mail-plugin.zip`, `apple-mail.plugin`, and
`apple-mail-mcp-v{VERSION}.mcpb` to the GitHub Release. Only the `.zip` is
tracked in git; the other two are gitignored and exist solely as build output,
so a Release without them ships an incomplete set.

## Release note

If shipping a version bump, bump all **seven** version files together (root `AGENTS.md` / `CLAUDE.md` § Version bump — `pyproject.toml`, the Claude, Codex, and **Cursor** plugin manifests, `.claude-plugin/marketplace.json` `plugins[0].version`, `server.json`, `apple-mail-mcpb/manifest.json`), re-run plugin-validator, then `bash tools/gates/dev-check.sh release` (which rebuilds all three artifacts (`apple-mail-plugin.zip`, `apple-mail.plugin`, and the `.mcpb`) and runs the structural mcpb-unpack smoke plus the byte-parity check between the zip and `.plugin`).

## Additional resources

- Deep conventions: [docs/CLAUDE-conventions.md](../../../docs/CLAUDE-conventions.md)
- Live verification: [docs/AGENT_LIVE_TESTING.md](../../../docs/AGENT_LIVE_TESTING.md)
