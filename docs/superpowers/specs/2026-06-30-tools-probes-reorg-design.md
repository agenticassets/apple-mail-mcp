# Design: reorganize `tools/` with a `tools/probes/` subfolder

> **Historical record (executed).** `tools/probes/` exists and holds these
> helpers. A later reorganization also moved the shell gates into
> `tools/gates/` and the Python validators into `tools/validators/`, so the
> `tools/dev-check.sh`, `tools/validate-codex-plugin.sh`, and
> `tools/check_wrapper_surface.py` paths quoted below are the pre-move ones.
> Use `tools/gates/…` and `tools/validators/…` today; see
> [`tools/CLAUDE.md`](../../../tools/CLAUDE.md). The collected-test count
> quoted in Verification is that day's and is now single-sourced in
> [`tools/expected_test_count.txt`](../../../tools/expected_test_count.txt).

**Date:** 2026-06-30
**Scope:** Conservative reorganization of the `tools/` directory.
**Constraint:** Everything must still work; verified and validated through the full release gate.

## Goal

Move the five non-gate helper scripts out of `tools/` root into a new
`tools/probes/` package, update every reference, and verify through the full
release gate. Gates, validators, `manifest_checks/`, and
`expected_test_count.txt` stay at the `tools/` root where CI, pre-commit,
`.claude/hooks`, and `dev-check.sh` cross-calls expect them.

## Background

`tools/` root currently mixes four kinds of things:

- **Shell gates:** `dev-check.sh`, `validate_manifests.sh`, `build-artifacts.sh`,
  `install-git-hooks.sh`, `pre-commit-validate.sh`, `validate-codex-plugin.sh`,
  `refresh-local-plugins.sh`
- **Python validators:** `validate_manifests.py`, `check_module_line_budget.py`,
  `check_wrapper_surface.py`, `validate_tasks_layout.py`, `sync_skill_references.py`
- **Research / smoke / patch helpers:** `measure_metadata_hydration.py`,
  `inspect_envelope_index_schema.py`, `compare_perf_results.py`,
  `mcp_tool_smoke.py`, `patch_mcporter_wrapper.py`
- **Existing package + data:** `manifest_checks/`, `expected_test_count.txt`

The gates and validators are referenced everywhere (CI, pre-commit,
`.claude/hooks`, `dev-check.sh` cross-calls). The five helpers are imported by
five test files and referenced by a few scripts and living docs. This design
moves only the five helpers, minimizing blast radius while making `tools/` look
more professional with a second themed subfolder alongside `manifest_checks/`.

## What moves

Into `tools/probes/`:

1. `tools/measure_metadata_hydration.py`
2. `tools/inspect_envelope_index_schema.py`
3. `tools/compare_perf_results.py`
4. `tools/mcp_tool_smoke.py`
5. `tools/patch_mcporter_wrapper.py`

New file: `tools/probes/__init__.py` (light docstring only, mirrors
`manifest_checks/__init__.py`). `tools/` itself stays a namespace package (no
`tools/__init__.py`) — unchanged.

## Reference updates (coordinated edits)

### Test imports

- `tests/infra/test_compare_perf_results.py:12` —
  `from tools.compare_perf_results import ...` →
  `from tools.probes.compare_perf_results import ...`
- `tests/infra/test_measure_metadata_hydration.py:12` —
  `from tools import measure_metadata_hydration as measurement` →
  `from tools.probes import measure_metadata_hydration as measurement`
- `tests/infra/test_inspect_envelope_index_schema.py:14` —
  `from tools import inspect_envelope_index_schema as inspector` →
  `from tools.probes import inspect_envelope_index_schema as inspector`
- `tests/infra/test_wrapper_surface.py:17` —
  `_REPO_ROOT / "tools" / "patch_mcporter_wrapper.py"` →
  `_REPO_ROOT / "tools" / "probes" / "patch_mcporter_wrapper.py"`
- `tests/infra/test_validate_manifests.py:703` —
  assert `"tools/mcp_tool_smoke.py"` → `"tools/probes/mcp_tool_smoke.py"`

### Script cross-references

- `tools/validate-codex-plugin.sh:39,56` —
  `tools/mcp_tool_smoke.py` → `tools/probes/mcp_tool_smoke.py`
- `tools/check_wrapper_surface.py:95` —
  help string `python3 /path/to/apple-mail-mcp/tools/patch_mcporter_wrapper.py`
  → `.../tools/probes/patch_mcporter_wrapper.py`

### Living docs

- `tools/CLAUDE.md` — the two research-helper sections' code-block paths and
  table rows (`measure_metadata_hydration.py`, `inspect_envelope_index_schema.py`)
- `README.md:124,156` — `tools/mcp_tool_smoke.py` → `tools/probes/mcp_tool_smoke.py`
- `docs/AGENT_LIVE_TESTING.md:214,219,255` —
  `tools/patch_mcporter_wrapper.py` and `tools/compare_perf_results.py` paths

### Historical tasks docs

Left as-is. Dated progress logs, completion audits, and phase plans record what
existed at the time and are not retroactively edited. Active handoff docs that
contain runnable `tools/<helper>.py` commands are updated so agents do not
follow dead paths.

## Hygiene

Remove stale `tools/__pycache__` entries for the moved modules (regenerated on
the next run).

## Verification

1. `git mv` the five files; create `tools/probes/__init__.py`.
2. Apply all reference edits.
3. `bash tools/dev-check.sh release` — lint (ruff + mypy) + manifest validation
   + module-line budget + full pytest (1016) + test-count gate + artifact
   rebuild + wrapper surface. Catches broken imports, stale `.mcpb`/zip, and
   budget regressions in one shot.
4. Spot-run `bash tools/validate-codex-plugin.sh` if Codex is on PATH (it now
   invokes `tools/probes/mcp_tool_smoke.py`); otherwise rely on the
   `test_validate_manifests.py` content assertion.
5. Confirm `tools/CLAUDE.md` and `README.md` render correct paths.

## Why the budget scanner is safe

`check_module_line_budget.py` uses `root.rglob("*.py")`, so it recurses into
`tools/probes/`. Moved files remain under the scanner. The reported relative
path changes (e.g. `tools/measure_metadata_hydration.py` →
`tools/probes/measure_metadata_hydration.py`), but the baseline
(`tests/fixtures/module_line_budget/baseline.json`) is empty after v3.9.1, so no
regression fires.

## Out of scope

- No gate or validator moves.
- No new lint or type tools.
- No behavior changes.
- No version bump (docs + internal path moves only; the `.mcpb` rebuild is
  content-identical because `tools/` is not bundled into the artifact).
