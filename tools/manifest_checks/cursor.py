"""Cursor plugin-surface checks for the local draft-safe MCP adapter."""

from __future__ import annotations

from manifest_checks import common
from manifest_checks.common import (
    DIRECT_SOURCE_MARKETPLACE_NAME,
    _check_tool_count_claim,
    _read_json_contract,
)

CURSOR_MANIFEST_LABEL = "plugin/.cursor-plugin/plugin.json"
CURSOR_MCP_LABEL = "plugin/mcp.json"
CURSOR_CATALOG_LABEL = ".cursor-plugin/marketplace.json"
CURSOR_PLUGIN_ID = "apple-mail"
CURSOR_PLUGIN_SOURCE = "./plugin"

# Cursor's published schemas set ``additionalProperties: false`` at every level,
# so any key outside these allowlists makes the whole manifest unparseable to
# Cursor. Mirror them here so drift fails locally instead of at listing time:
# https://github.com/cursor/plugins/blob/main/schemas/plugin.schema.json
# https://github.com/cursor/plugins/blob/main/schemas/marketplace.schema.json
CURSOR_PLUGIN_MANIFEST_KEYS = frozenset(
    {
        "name",
        "displayName",
        "description",
        "version",
        "minClientVersions",
        "author",
        "publisher",
        "homepage",
        "repository",
        "license",
        "logo",
        "keywords",
        "category",
        "tags",
        "commands",
        "agents",
        "skills",
        "rules",
        "hooks",
        "variables",
        "mcpServers",
    }
)
CURSOR_PERSON_KEYS = frozenset({"name", "email"})
CURSOR_CATALOG_KEYS = frozenset({"name", "owner", "metadata", "plugins"})
CURSOR_CATALOG_ENTRY_KEYS = frozenset({"name", "source", "description", "minClientVersions"})


def _check_schema_keys(value: object, allowed: frozenset[str], label: str, errors: list[str]) -> bool:
    """Reject keys Cursor's ``additionalProperties: false`` schemas would refuse.

    Returns False when ``value`` is not an object, so callers can skip the
    per-field checks that would only repeat the same complaint.
    """
    if not isinstance(value, dict):
        errors.append(f"{label}: expected object")
        return False
    unknown = sorted(set(value) - allowed)
    if unknown:
        errors.append(f"{label}: keys not allowed by the Cursor schema: {', '.join(unknown)}")
    return True


def _check_person(value: object, label: str, errors: list[str]) -> None:
    """``author`` / ``owner``: required; schema allows only name+email; this public repo omits email."""
    if value is None:
        errors.append(f"{label}: missing required field")
        return
    if not _check_schema_keys(value, CURSOR_PERSON_KEYS, label, errors):
        return
    if not isinstance(value.get("name"), str) or not value["name"]:
        errors.append(f"{label}.name: required non-empty string")
    if "email" in value:
        errors.append(f"{label}.email: omit contact addresses from public Cursor manifests")


def _check_relative_asset(value: object, label: str, errors: list[str], *, directory: bool = False) -> None:
    """Cursor resolves ``logo``/``skills`` relative to the plugin root; required, no ``..`` or absolute paths."""
    if value is None:
        errors.append(f"{label}: missing required field")
        return
    if not isinstance(value, str) or not value:
        errors.append(f"{label}: expected non-empty relative path string")
        return
    if value.startswith("/") or ".." in value.split("/"):
        errors.append(f"{label}: must be a relative path inside the plugin root, got '{value}'")
        return
    target = common.ROOT / "plugin" / value
    if not (target.is_dir() if directory else target.is_file()):
        kind = "directory" if directory else "file"
        errors.append(f"{label}: {kind} '{value}' not found under plugin/")


def _check_cursor_mcp_launcher_contract(
    server: object,
    label: str,
    errors: list[str],
) -> None:
    """Validate Cursor's plugin-root-aware stdio launcher."""
    if not isinstance(server, dict):
        errors.append(f"{label}: missing mcpServers.apple-mail")
        return
    if server.get("command") != "/bin/bash":
        errors.append(f"{label} mcpServers.apple-mail.command: expected /bin/bash")

    args = server.get("args")
    if not isinstance(args, list):
        errors.append(f"{label} mcpServers.apple-mail.args: expected list")
        return
    expected_launcher = "${CURSOR_PLUGIN_ROOT}/start_mcp.sh"
    if not args or args[0] != expected_launcher:
        errors.append(f"{label} mcpServers.apple-mail.args: first arg must be {expected_launcher}")
    if "--draft-safe" not in args:
        errors.append(f"{label} mcpServers.apple-mail.args: missing --draft-safe")

    if "cwd" in server:
        errors.append(f"{label} mcpServers.apple-mail.cwd: omit cwd for Cursor plugins")

    values = [server.get("command"), *args, server.get("cwd")]
    if any(isinstance(value, str) and "${CLAUDE_PLUGIN_ROOT}" in value for value in values):
        errors.append(f"{label} mcpServers.apple-mail: must not use ${{CLAUDE_PLUGIN_ROOT}} in Cursor launcher fields")


def _check_cursor_plugin_contract(
    expected_version: str,
    actual_tool_count: int,
    errors: list[str],
) -> None:
    """Keep Cursor's manifest and local launcher distinct from Codex's adapter."""
    manifest = _read_json_contract(
        common.ROOT / CURSOR_MANIFEST_LABEL,
        CURSOR_MANIFEST_LABEL,
        errors,
    )
    mcp_path = common.ROOT / CURSOR_MCP_LABEL
    if manifest is not None:
        if manifest.get("name") != CURSOR_PLUGIN_ID:
            errors.append(f"{CURSOR_MANIFEST_LABEL} name: got '{manifest.get('name')}', expected '{CURSOR_PLUGIN_ID}'")
        if manifest.get("version") != expected_version:
            errors.append(
                f"{CURSOR_MANIFEST_LABEL} version: got '{manifest.get('version')}', expected '{expected_version}'"
            )
        if manifest.get("mcpServers") != "./mcp.json":
            errors.append(
                f"{CURSOR_MANIFEST_LABEL} mcpServers: got '{manifest.get('mcpServers')}', expected './mcp.json'"
            )
        description = manifest.get("description")
        if description is not None:
            _check_tool_count_claim(description, f"{CURSOR_MANIFEST_LABEL} description", actual_tool_count, errors)
        _check_schema_keys(manifest, CURSOR_PLUGIN_MANIFEST_KEYS, CURSOR_MANIFEST_LABEL, errors)
        _check_person(manifest.get("author"), f"{CURSOR_MANIFEST_LABEL} author", errors)
        _check_relative_asset(manifest.get("logo"), f"{CURSOR_MANIFEST_LABEL} logo", errors)
        # Cursor also accepts an array of skill paths; only the string form names a directory.
        skills = manifest.get("skills")
        if skills is None or isinstance(skills, str):
            _check_relative_asset(skills, f"{CURSOR_MANIFEST_LABEL} skills", errors, directory=True)

    mcp = _read_json_contract(mcp_path, CURSOR_MCP_LABEL, errors)
    if mcp is None:
        return
    servers = mcp.get("mcpServers") or {}
    if not isinstance(servers, dict):
        errors.append(f"{CURSOR_MCP_LABEL} mcpServers: expected object")
        return
    _check_cursor_mcp_launcher_contract(
        servers.get("apple-mail"),
        CURSOR_MCP_LABEL,
        errors,
    )


def _check_cursor_marketplace_catalog(actual_tool_count: int, errors: list[str]) -> None:
    """Root ``.cursor-plugin/marketplace.json`` must keep the standalone identity and point at ``./plugin``."""
    catalog = _read_json_contract(common.ROOT / CURSOR_CATALOG_LABEL, CURSOR_CATALOG_LABEL, errors)
    if catalog is None:
        return
    _check_schema_keys(catalog, CURSOR_CATALOG_KEYS, CURSOR_CATALOG_LABEL, errors)
    if catalog.get("name") != DIRECT_SOURCE_MARKETPLACE_NAME:
        errors.append(
            f"{CURSOR_CATALOG_LABEL} name: got '{catalog.get('name')}', expected '{DIRECT_SOURCE_MARKETPLACE_NAME}'"
        )
    _check_person(catalog.get("owner"), f"{CURSOR_CATALOG_LABEL} owner", errors)

    plugins = catalog.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        errors.append(f"{CURSOR_CATALOG_LABEL}: missing plugins[0]")
        return
    entry = plugins[0]
    label = f"{CURSOR_CATALOG_LABEL} plugins[0]"
    if not _check_schema_keys(entry, CURSOR_CATALOG_ENTRY_KEYS, label, errors):
        return
    if entry.get("name") != CURSOR_PLUGIN_ID:
        errors.append(f"{label} name: got '{entry.get('name')}', expected '{CURSOR_PLUGIN_ID}'")
    if entry.get("source") != CURSOR_PLUGIN_SOURCE:
        errors.append(f"{label} source: got '{entry.get('source')}', expected '{CURSOR_PLUGIN_SOURCE}'")
    description = entry.get("description")
    if description is not None:
        _check_tool_count_claim(description, f"{label} description", actual_tool_count, errors)
