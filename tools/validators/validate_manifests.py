#!/usr/bin/env python3
"""Validate version sync, tool counts, mcpb parity, and local artifacts.

The individual checks live in the sibling ``manifest_checks`` package (split
out so every module stays under the 600 LOC budget). This module wires them
together in ``main`` — the entry point ``tools/gates/validate_manifests.sh``
invokes as ``python3 tools/validators/validate_manifests.py`` — and re-exports
them so ``tests/infra/test_validate_manifests.py`` can keep calling
``validate_manifests.<check>`` directly.

``ROOT`` is forwarded to ``manifest_checks.common.ROOT`` (every check reads
``common.ROOT`` at call time) so tests that monkeypatch
``validate_manifests.ROOT`` continue to redirect every check.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

# Run as a script from tools/validators/, so put tools/ on sys.path for the
# sibling manifest_checks package. (Tests import this module with
# tools/validators/ on sys.path and also rely on this bootstrap.)
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from manifest_checks import common
from manifest_checks.artifacts import (
    _check_artifact_freshness,
    _check_no_directory_entries,
    _check_no_stale_distribution_artifacts,
    _check_plugin_file_parity,
    _compare_zip_members,
)
from manifest_checks.codex import _check_codex_plugin_contract
from manifest_checks.common import (
    _check_tool_count_claim,
    _env_truthy,
    _json_field,
)
from manifest_checks.cursor import _check_cursor_marketplace_catalog, _check_cursor_plugin_contract
from manifest_checks.install_contracts import (
    _check_developer_only_skills_not_packaged,
    _check_marketplace_contract,
    _check_mcpb_directory_contract,
    _check_mcpb_runtime_contract,
    _check_plugin_manifest_contract,
    _check_python_package_contract,
    _check_server_json_contract,
    _check_source_syntax,
)
from manifest_checks.module_budget import _check_module_line_budget
from manifest_checks.tool_count import (
    _check_active_doc_tool_count_claims,
    _extract_registered_tool_names,
)
from manifest_checks.version import (
    _check_changelog_release_version,
    _read_project_name,
    _read_project_version,
)

VersionCheck = tuple[Path, str, str]

# Re-exported check surface. Names live in ``manifest_checks.*``; they are
# imported above purely so ``validate_manifests.<name>`` resolves for the test
# suite and ``main`` below. Listing them keeps the re-exports explicit.
# (``ROOT`` is omitted: it is served by the _RootForwardingModule property,
# not a module-level name, so it is reachable as an attribute but not via
# ``import *``.)
__all__ = [
    "main",
    "_check_active_doc_tool_count_claims",
    "_check_artifact_freshness",
    "_check_changelog_release_version",
    "_check_codex_plugin_contract",
    "_check_cursor_marketplace_catalog",
    "_check_cursor_plugin_contract",
    "_check_developer_only_skills_not_packaged",
    "_check_marketplace_contract",
    "_check_mcpb_directory_contract",
    "_check_mcpb_runtime_contract",
    "_check_module_line_budget",
    "_check_no_directory_entries",
    "_check_no_stale_distribution_artifacts",
    "_check_plugin_file_parity",
    "_check_plugin_manifest_contract",
    "_check_python_package_contract",
    "_check_public_versions",
    "_check_server_json_contract",
    "_check_source_syntax",
    "_check_tool_count_claim",
    "_compare_zip_members",
    "_env_truthy",
    "_extract_registered_tool_names",
    "_public_version_checks",
    "_json_field",
    "_read_project_name",
    "_read_project_version",
]


class _RootForwardingModule(types.ModuleType):
    """Keep ``validate_manifests.ROOT`` an alias of ``common.ROOT``.

    Tests monkeypatch ``validate_manifests.ROOT`` to aim the checks at a temp
    repo. Every check now reads ``common.ROOT`` at call time, so forward this
    module's ``ROOT`` attribute get/set onto ``common`` to preserve that.
    """

    @property
    def ROOT(self):  # noqa: N802 - mirrors the historical module constant name
        return common.ROOT

    @ROOT.setter
    def ROOT(self, value):  # noqa: N802
        common.ROOT = value


sys.modules[__name__].__class__ = _RootForwardingModule


def _public_version_checks() -> list[VersionCheck]:
    """Every public plugin, marketplace, package, and bundle version surface.

    PyPI metadata in ``pyproject.toml`` is the source of truth. Everything in
    this list is a published install or discovery surface that must exactly
    match it. Keep this list broad and explicit so Claude, Codex, MCPB, and
    MCP registry releases cannot drift independently.
    """
    return [
        (common.ROOT / "plugin/.claude-plugin/plugin.json", "version", "Claude plugin manifest"),
        (common.ROOT / "plugin/.codex-plugin/plugin.json", "version", "Codex plugin manifest"),
        (common.ROOT / "plugin/.cursor-plugin/plugin.json", "version", "Cursor plugin manifest"),
        (common.ROOT / ".claude-plugin/marketplace.json", "plugins[0].version", "Claude marketplace plugin"),
        (common.ROOT / "server.json", "version", "MCP server metadata"),
        (common.ROOT / "server.json", "packages[0].version", "MCP server package"),
        (common.ROOT / "apple-mail-mcpb/manifest.json", "version", "MCPB manifest"),
    ]


def _check_public_versions(expected_version: str, errors: list[str]) -> None:
    for path, field, label in _public_version_checks():
        actual = _json_field(path, field)
        if actual != expected_version:
            errors.append(f"{label}: got '{actual}', expected '{expected_version}'")


def main() -> None:
    errors: list[str] = []
    expected_version = _read_project_version()
    project_name = _read_project_name()

    _check_public_versions(expected_version, errors)
    _check_changelog_release_version(expected_version, errors)

    code_names = _extract_registered_tool_names()
    actual_count = len(code_names)
    if actual_count == 0:
        errors.append("no @mcp.tool registrations found")

    plugin = json.loads((common.ROOT / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
    _check_plugin_manifest_contract(errors)
    _check_developer_only_skills_not_packaged(errors)
    _check_tool_count_claim(plugin.get("description"), "plugin.json description", actual_count, errors)

    market = json.loads((common.ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    plugins = market.get("plugins") or []
    if not plugins:
        errors.append("marketplace.json: missing plugins[0]")
    else:
        _check_tool_count_claim(
            plugins[0].get("description"),
            "marketplace.json plugins[0].description",
            actual_count,
            errors,
        )
    _check_marketplace_contract(expected_version, errors)
    _check_codex_plugin_contract(expected_version, actual_count, errors)
    _check_cursor_plugin_contract(expected_version, actual_count, errors)
    _check_cursor_marketplace_catalog(actual_count, errors)

    mcpb = json.loads((common.ROOT / "apple-mail-mcpb/manifest.json").read_text(encoding="utf-8"))
    _check_tool_count_claim(mcpb.get("description"), "mcpb manifest description", actual_count, errors)
    _check_mcpb_runtime_contract(mcpb, errors)
    _check_mcpb_directory_contract(mcpb, errors)

    mcpb_names = [tool["name"] for tool in mcpb.get("tools", [])]
    if len(mcpb_names) != actual_count:
        errors.append(f"tool count mismatch: code={actual_count}, mcpb tools[]={len(mcpb_names)}")

    _check_active_doc_tool_count_claims(actual_count, errors)

    code_set = set(code_names)
    mcpb_set = set(mcpb_names)
    only_code = sorted(code_set - mcpb_set)
    only_mcpb = sorted(mcpb_set - code_set)
    if only_code:
        errors.append("registered in code, missing from mcpb: " + ", ".join(only_code))
    if only_mcpb:
        errors.append("present in mcpb tools[], missing from code: " + ", ".join(only_mcpb))

    server = json.loads((common.ROOT / "server.json").read_text(encoding="utf-8"))
    _check_server_json_contract(
        server,
        expected_version=expected_version,
        project_name=project_name,
        errors=errors,
    )
    _check_python_package_contract(errors)
    _check_source_syntax(errors)

    _check_artifact_freshness(
        expected_version,
        errors,
        require_artifacts=_env_truthy("APPLE_MAIL_REQUIRE_DIST_ARTIFACTS"),
    )

    module_budget_warn_count = _check_module_line_budget(errors)

    if errors:
        print("validate_manifests: FAILED", file=sys.stderr)
        for err in errors:
            print(f"  ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    print(
        "validate_manifests: OK "
        f"(version={expected_version}, tools={actual_count}, "
        f"module_budget_warn={module_budget_warn_count})"
    )


if __name__ == "__main__":
    main()
