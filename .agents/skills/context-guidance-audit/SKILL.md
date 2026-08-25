---
name: context-guidance-audit
description: Multi-phase, subagent-driven deep audit and remediation of codebase guidance (root agent instructions, docs, module context files, skills/agents references, task routing). Phase 1 subagents write verified findings only; Phase 2 subagents verify and fix guidance. Use when the user asks to audit, refresh, or fix agent context, CLAUDE.md or AGENTS.md drift, stale docs, contradictory rules, or run a context-guidance deep dive.
---

# Context Guidance Audit

Orchestrate a **two-phase, multi-subagent** pass over everything that teaches AI coding agents how to work in the repo. **Phase 1 is read-only** (reports only). **Phase 2 applies fixes** (guidance files only unless the user explicitly includes application code).

Repo layout varies. Discover actual paths first (`Glob **/CLAUDE.md`, `AGENTS.md`, `.cursor/`, `.agents/`, `docs/`) — do not assume the examples below exist in every project.

Complements skills such as **context-engineering** (maturity and architecture) and **update-docs** (doc sync after code changes). This skill is the **operational playbook** for a full guidance health check.

## Non-negotiable rules

1. **Two phases, never merged.** Phase 1 subagents **must not edit** guidance. Phase 2 subagents **must verify** each finding before fixing; skip false positives.
2. **Many narrow subagents, not one generalist.** Split by folder/domain. Run Phase 1 lanes **in parallel**; run Phase 2 **one subagent per Phase 1 report**, in parallel when scopes do not overlap. **Ensure at least one or two lanes cover areas with the most recent codebase changes** so guidance there is checked against live code, not only static folder templates.
3. **Guidance-only by default.** Phase 2 edits agent instruction files, docs, IDE rules, skills metadata, task/index files — not application source unless the user asks.
4. **Verify against the live repo.** Every finding needs evidence: `Glob`/`Grep`/`Read`, manifest scripts (`package.json`, `Makefile`, etc.), routes, installed skill/agent paths.
5. **Concise agent guidance.** Remove ambiguity and stale claims; do not bloat files with copied checklists from external skills.
6. **Ephemeral audit folder.** Reports live in a dated review folder until remediation is accepted; then **delete the whole folder** (user typically approves before Phase 2 and again before cleanup).
7. **Subagent model (Cursor).** Launch **every** Phase 1 and Phase 2 subagent on **Composer 2.5** (`composer-2.5`) unless the user names a different model. If the host offers a **newer Composer generation**, use that latest Composer model instead. Do not omit the model pin on Cursor Task/subagent calls unless the user explicitly asks you to.

## When to use

- User asks to audit context, agent guidance, or documentation for agents.
- Repeated agent mistakes trace to contradictory or stale module docs.
- After large refactors, strategy doc moves, or skill/agent path changes.
- Before standardizing agent discovery across Cursor, Claude Code, or other tooling.

## Review directory

Pick a location your repo already uses for audits (or create one under `docs/reviews/`):

```text
docs/reviews/context-guidance-audit-YYYY-MM-DD/
├── SYNTHESIS.md              # Optional: parent writes after Phase 1 only
├── 01-<scope-slug>.md
├── 02-<scope-slug>.md
└── ...
```

Use zero-padded numbers and stable slug names so Phase 2 maps 1:1 to reports.

---

## Phase 1 — Audit (read-only)

### Orchestrator

1. Create the dated review directory.
2. **Discover** guidance artifacts (root `CLAUDE.md` / `AGENTS.md` / `CODEX.md`, nested `**/CLAUDE.md`, `.cursor/rules/`, `.agents/skills/`, `.claude/agents/`, docs trees).
3. **Identify recently changed areas** (e.g. `git log --since=…`, recent merges, active branch diff, or user-stated focus). Map changed code paths to nearby guidance (`**/CLAUDE.md`, docs, skills) — at least **one or two Phase 1 scopes must include those areas**, even if you otherwise use a standard lane split.
4. Choose **4–8 parallel audit scopes** — disjoint folders/domains, biased toward recent-change coverage where it matters. See [references/example-scope-splits.md](references/example-scope-splits.md) for sample lane patterns (including one real Next.js monolith example).
5. Launch one subagent per scope with:
   - **Model:** Composer 2.5 (`composer-2.5`), or the latest Composer generation available in Cursor if newer.
   - Explicit **read-only** instruction (`Do NOT fix anything`).
   - **Output path** for the report file (subagent **must write** the file — use a subagent type with write access; read-only explorers require the parent to persist output).
   - Report template (below).
   - Instruction: **Severity | Location | Issue | Recommended change** for verified issues only.
6. **Wait for all Phase 1 subagents** before synthesis or Phase 2.
7. Optionally write `SYNTHESIS.md` (top issues, batch fix order). **Do not fix guidance yet** unless the user waives Phase 1 separation.

### Phase 1 report template

```markdown
# <Scope Title> Audit

## Summary
(2–4 sentences)

## Findings
| Severity | Location | Issue | Recommended change |
| High/Medium/Low | file:section | ... | ... |

## Positive observations

## Suggested fix priority order
```

### What Phase 1 subagents check

| Category | Examples (adapt to your stack) |
|----------|--------------------------------|
| **Contradictions** | Root vs module context files; UI layer vs API layer rules |
| **Stale facts** | “Phase N” or migration notes vs shipped code; wrong script names in docs |
| **Broken references** | Missing files, moved paths, wrong line numbers, dead skill names |
| **Ambiguity** | Broad globs (`src/**`) that include files the rule was not meant for |
| **Discovery** | Symlinks, duplicate agent trees (`.cursor/` vs `.claude/`), skill invoke strings |
| **Strategy drift** | Product/docs language vs canonical strategy or north-star docs |
| **Version claims** | Documented framework/library versions vs lockfile or manifest |

---

## Phase 2 — Remediate (verify + fix)

### Gate

**Wait for user permission** after Phase 1 (and optional synthesis) unless they already asked to “audit and fix.”

### Orchestrator

1. Launch **one subagent per Phase 1 report**, same scope boundary — each on **Composer 2.5** (`composer-2.5`) or the latest Composer generation available in Cursor if newer.
2. Each subagent:
   - Reads its audit report.
   - **Re-verifies** each finding.
   - Applies **minimal** edits in scope.
   - Appends `## Remediation log` to the same report (verified / fixed / skipped + paths).
3. **Wait for all Phase 2 subagents** before declaring done.
4. Run repo-appropriate verification when links or paths changed (doc link linter, markdown lint, symlink checks — use whatever the project documents).
5. After user accepts: **delete** the audit folder.

### Phase 2 edit principles

- Prefer **one authoritative home** for a rule; link elsewhere (dedupe parent vs module docs).
- Replace brittle fixed counts with **pointers to canonical helpers** or modules (never `"N agents"`, `"N skills"`, or version snapshots in guidance).
- **Skill/agent IDs — do not swap on weak evidence.** Missing from `.claude/skills/` alone is not enough. Check `.agents/skills/` (the canonical home; `.claude/skills/` is a symlink), plugin marketplaces, the host's installed-plugin cache, and agent frontmatter before changing any skill or agent id. Only replace when **proven** removed (e.g. documented registry deletion, file deleted, upstream rename). When unsure, **skip** and log deferred — do not substitute a local-only skill.
- Add **banners** for superseded docs; link to current strategy or index — do not rewrite historical body text unless asked.
- For modular strategy or policy docs: fix **links and navigation** only unless the user asked to rewrite content.

### Deferred items

Log out-of-scope follow-ups in the remediation log; do not expand scope mid-flight.

---

## Choosing audit scopes

Split so each lane owns a **disjoint** set of guidance files. Typical patterns (names and paths will differ):

| Lane type | Often includes | Look for |
|-----------|----------------|----------|
| **Root + docs index** | Root agent file, `docs/README`, top-level docs policy | Broken links, duplicate indexes, strategy hierarchy |
| **App / routes layer** | Route or page module context files | Handler vs page rules mixed, stale route paths |
| **Library / domain layer** | Service, lib, or component module context files | Stale phase notes, wrong canonical APIs |
| **Tasks + agents + tooling** | Task trackers, agent dirs, IDE rules, slash commands | Symlink breaks, script name drift, discovery conflicts |
| **Maintainer / architecture guides** | Coding guides, ADRs, workflow docs | Pre-implementation docs vs live code |
| **Docs site / published docs** | Static site config, content inventory | Inventory vs on-disk content |
| **Skills inventory** | Skill references across all guidance files | Fictional skill names, plugin vs local skill mismatch |
| **Product / ops / user docs** | User guides, runbooks, API overview | UI labels vs app, wrong URLs, API table gaps |

Adjust lane count (4–8). Merge or split based on repo size. **Do not let a template split skip hot paths:** if recent work touched specific modules, routes, or docs, dedicate at least one lane (or explicit checklist items inside a lane) to cross-check guidance there against the changed code. Example splits: [references/example-scope-splits.md](references/example-scope-splits.md).

---

## Subagent dispatch pattern

**Cursor:** pin **Composer 2.5** (`composer-2.5`) on **every** Phase 1 and Phase 2 subagent unless the user specifies otherwise; if a newer Composer model is available in the host, use the latest Composer generation instead. Non-Cursor hosts: omit explicit model pins unless the user requests them.

**Phase 1 prompt skeleton:**

```text
Audit [SCOPE PATHS] for confusion, contradictions, stale guidance.
Pay extra attention to [RECENT CHANGE PATHS IF IN SCOPE].
Do NOT fix anything. Verify with Glob/Grep/Read against the live repo.
Write report to [REVIEW_DIR]/NN-slug.md
Use the standard findings table. Only verified issues.
```

**Phase 2 prompt skeleton:**

```text
Read [REVIEW_DIR]/NN-slug.md
Verify each finding. Fix verified issues in [SCOPE ONLY] — guidance files only.
Optimize for clear, concise AI-agent guidance. Append ## Remediation log to the report.
```

Use a **write-capable** subagent when reports must be saved on disk; use read-only exploration only if the orchestrator persists output.

---

## Orchestrator checklist

**Phase 1**
- [ ] Guidance artifacts discovered (not assumed)
- [ ] Recent codebase changes mapped to guidance scopes (**≥1–2 lanes** cover those areas)
- [ ] Review directory created
- [ ] 4–8 parallel read-only audits launched with distinct scopes
- [ ] All report files on disk
- [ ] Optional SYNTHESIS.md
- [ ] User permission for Phase 2 (if required)

**Phase 2**
- [ ] One remediation subagent per report
- [ ] Remediation logs appended
- [ ] Targeted verification if links/paths changed
- [ ] User accepted; audit folder deleted

---

## Anti-patterns

- **Single subagent full-repo audit** — misses cross-layer contradictions; overloads context.
- **Fix during Phase 1** — loses verified finding list and user review gate.
- **Assume this repo matches an example layout** — always discover paths first.
- **Paraphrase modular policy/strategy body text** — prefer links and banners.
- **Paste external skill checklists into agent files** — bloat; sync durable repo-specific rules only.
- **Leave audit folders forever** — delete after remediation.
- **Replace plugin skill IDs because `.claude/skills/` lacks a symlink** — check `.agents/skills/`, plugins, and cache first; user-facing agents often list marketplace skills intentionally.
- **Hardcode agent/skill totals** (`"25 Project Agents"`, `"38 skills"`) — point to `.claude/agents/` or skill trees instead.

## Related skills (if present in the repo)

- **context-engineering** — maturity audit, bootstrap, diagnose
- **update-docs** — doc updates driven by code changes
- **docs-maintainer** — docs folder hygiene and index sync
- **code-review-command** — subagent-driven **code** review (different artifact layout)
