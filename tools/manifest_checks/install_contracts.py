"""Install-contract checks for the Claude plugin, marketplace, MCPB bundle,
server.json metadata, and the PyPI package, plus shipped-source syntax."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

from manifest_checks import common
from manifest_checks.common import DIRECT_SOURCE_MARKETPLACE_NAME, MARKETPLACE_COMPONENT_FIELDS

# Claude Desktop extension-directory submissions need a current MCPB manifest
# (spec 0.3; the legacy ``dxt_version`` key is deprecated), a ``compatibility``
# block, and an HTTPS ``privacy_policies`` array. Mail.app only exists on macOS.
MCPB_MANIFEST_VERSION = "0.3"
MCPB_PLATFORMS = ["darwin"]


def _check_mcp_launcher_contract(
    server: object,
    label: str,
    first_arg: str,
    errors: list[str],
) -> None:
    if not isinstance(server, dict):
        errors.append(f"{label}: missing mcpServers.apple-mail")
        return
    if server.get("command") != "/bin/bash":
        errors.append(f"{label} mcpServers.apple-mail.command: expected /bin/bash")

    args = server.get("args")
    if not isinstance(args, list):
        errors.append(f"{label} mcpServers.apple-mail.args: expected list")
        return
    if not args or args[0] != first_arg:
        errors.append(f"{label} mcpServers.apple-mail.args: first arg must be {first_arg}")
    if "--draft-safe" not in args:
        errors.append(f"{label} mcpServers.apple-mail.args: missing --draft-safe")


def _check_plugin_manifest_contract(errors: list[str]) -> None:
    """Validate plugin fields that have caused strict install/runtime failures."""
    plugin = json.loads((common.ROOT / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8"))

    # Cowork/Claude strict validation rejected this field for this plugin.
    # Apple Mail ships workflow entry points as skills only.
    if "commands" in plugin:
        errors.append(
            "plugin.json: unsupported strict-validator field 'commands'; ship workflow entry points as skills only"
        )

    commands_dir = common.ROOT / "plugin/commands"
    if commands_dir.exists():
        errors.append("plugin/commands: legacy slash commands are retired; ship skills only")

    servers = plugin.get("mcpServers") or {}
    _check_mcp_launcher_contract(
        servers.get("apple-mail"),
        "plugin.json",
        "${CLAUDE_PLUGIN_ROOT}/start_mcp.sh",
        errors,
    )


def _iter_json_strings(value: object) -> Iterator[str]:
    """Yield every string value inside a JSON-like object."""
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_json_strings(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_json_strings(item)


def _check_developer_only_skills_not_packaged(errors: list[str]) -> None:
    """Prevent repo-local development skills from leaking into packaged plugin surfaces."""
    manifest_paths = (
        common.ROOT / "plugin/.codex-plugin/plugin.json",
        common.ROOT / "plugin/.claude-plugin/plugin.json",
        common.ROOT / ".agents/plugins/marketplace.json",
        common.ROOT / ".claude-plugin/marketplace.json",
    )
    forbidden = (".agents/skills", ".claude/skills")

    for path in manifest_paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        rel = path.relative_to(common.ROOT).as_posix()
        for value in _iter_json_strings(payload):
            if any(marker in value for marker in forbidden):
                label = f"{rel} skills" if "skills" in payload else rel
                errors.append(
                    f"{label}: must not reference repo-local developer skills (.agents/skills or .claude/skills)"
                )
                break

    packaged_skills = common.ROOT / "plugin/skills"
    if packaged_skills.exists():
        resolved = packaged_skills.resolve()
        for dev_dir in (common.ROOT / ".agents/skills", common.ROOT / ".claude/skills"):
            if not dev_dir.exists():
                continue
            try:
                resolved.relative_to(dev_dir.resolve())
            except ValueError:
                continue
            errors.append(
                "plugin/skills: must be packaged workflow skills, not a link into "
                f"{dev_dir.relative_to(common.ROOT).as_posix()}"
            )
            break


def _check_mcpb_runtime_contract(mcpb: dict, errors: list[str]) -> None:
    """Validate the Desktop bundle starts the same safe wrapper as the plugin."""
    server = mcpb.get("server") or {}
    config = server.get("mcp_config") or {}

    if server.get("type") != "python":
        errors.append("mcpb manifest server.type: expected python")

    entry_point = server.get("entry_point")
    if not isinstance(entry_point, str) or not entry_point:
        errors.append("mcpb manifest server.entry_point: expected non-empty string")
    elif not (common.ROOT / "plugin" / entry_point).exists():
        errors.append(f"mcpb manifest server.entry_point: missing plugin/{entry_point}")

    if config.get("command") != "/bin/bash":
        errors.append("mcpb manifest server.mcp_config.command: expected /bin/bash")

    args = config.get("args")
    if not isinstance(args, list):
        errors.append("mcpb manifest server.mcp_config.args: expected list")
    else:
        if not args or args[0] != "${__dirname}/start_mcp.sh":
            errors.append("mcpb manifest server.mcp_config.args: first arg must be ${__dirname}/start_mcp.sh")
        if "--draft-safe" not in args:
            errors.append("mcpb manifest server.mcp_config.args: missing --draft-safe")

    env = config.get("env") or {}
    for key in (
        "USER_EMAIL_PREFERENCES",
        "DEFAULT_MAIL_ACCOUNT",
        "DEFAULT_MAIL_SIGNATURE",
    ):
        if key not in env:
            errors.append(f"mcpb manifest server.mcp_config.env: missing {key}")
            continue
        value = env.get(key)
        if not isinstance(value, str):
            errors.append(f"mcpb manifest server.mcp_config.env.{key}: expected string")
            continue
        for config_key in re.findall(r"\$\{user_config\.([^}]+)\}", value):
            if config_key not in (mcpb.get("user_config") or {}):
                errors.append(f"mcpb manifest server.mcp_config.env.{key}: unknown user_config.{config_key}")


def _check_mcpb_directory_contract(mcpb: dict[str, object], errors: list[str]) -> None:
    """Validate the Desktop bundle metadata the extension directory requires."""
    if "dxt_version" in mcpb:
        errors.append("mcpb manifest dxt_version: legacy key; use manifest_version")
    if mcpb.get("manifest_version") != MCPB_MANIFEST_VERSION:
        errors.append(f"mcpb manifest manifest_version: expected '{MCPB_MANIFEST_VERSION}'")

    policies = mcpb.get("privacy_policies")
    if not isinstance(policies, list) or not policies:
        errors.append("mcpb manifest privacy_policies: expected non-empty list of https URLs")
    else:
        for url in policies:
            if not isinstance(url, str) or not url.startswith("https://"):
                errors.append(f"mcpb manifest privacy_policies: expected https URL, got {url!r}")

    compatibility = mcpb.get("compatibility")
    if not isinstance(compatibility, dict):
        errors.append("mcpb manifest compatibility: expected object")
        return
    if compatibility.get("platforms") != MCPB_PLATFORMS:
        errors.append(f"mcpb manifest compatibility.platforms: expected {MCPB_PLATFORMS} (Mail.app is macOS-only)")
    runtimes = compatibility.get("runtimes") or {}
    if not isinstance(runtimes, dict):
        errors.append("mcpb manifest compatibility.runtimes: expected object")
        return
    runtime = runtimes.get("python")
    if not isinstance(runtime, str) or not runtime:
        errors.append("mcpb manifest compatibility.runtimes.python: expected non-empty version constraint")


def _check_marketplace_contract(expected_version: str, errors: list[str]) -> None:
    """Ensure marketplace source and skill pointers resolve to the plugin."""
    market = json.loads((common.ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    if market.get("name") != DIRECT_SOURCE_MARKETPLACE_NAME:
        errors.append(
            ".claude-plugin/marketplace.json name: got "
            f"'{market.get('name')}', expected '{DIRECT_SOURCE_MARKETPLACE_NAME}'"
        )
    plugins = market.get("plugins") or []
    if not plugins:
        errors.append("marketplace.json: missing plugins[0]")
        return
    plugin_ref = plugins[0]

    if plugin_ref.get("strict") is not True:
        errors.append(
            "marketplace.json plugins[0].strict: expected true "
            "(plugin.json declares components and Claude marketplaces default to strict mode)"
        )

    source = plugin_ref.get("source")
    source_path: Path | None = None
    source_manifest: dict | None = None
    if not isinstance(source, str):
        errors.append("marketplace.json plugins[0].source: expected string")
    elif not source.startswith("./"):
        errors.append(f"marketplace.json plugins[0].source: path must start with ./ (got {source})")
    else:
        source_path = common.ROOT / source[2:]
        manifest_path = source_path / ".claude-plugin/plugin.json"
        if not manifest_path.exists():
            errors.append(f"marketplace.json plugins[0].source: missing {source}/.claude-plugin/plugin.json")
        else:
            source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    expected_name = (source_manifest or {}).get("name", "missing")
    if plugin_ref.get("name") != expected_name:
        errors.append(
            "marketplace.json plugins[0].name: got "
            f"'{plugin_ref.get('name')}', expected plugin.json name '{expected_name}'"
        )
    if plugin_ref.get("version") != expected_version:
        errors.append(
            f"marketplace.json plugins[0].version: got '{plugin_ref.get('version')}', expected '{expected_version}'"
        )

    skills = plugin_ref.get("skills") or []
    for skill_path in skills:
        if not isinstance(skill_path, str):
            errors.append("marketplace.json plugins[0].skills: entries must be strings")
            continue
        if not skill_path.startswith("./"):
            errors.append(f"marketplace.json plugins[0].skills: path must start with ./ (got {skill_path})")
            continue
        skill_file = common.ROOT / skill_path[2:] / "SKILL.md"
        if not skill_file.exists():
            errors.append(f"marketplace.json plugins[0].skills: missing {skill_path}/SKILL.md")

    if source_manifest is not None:
        market_components = [f for f in MARKETPLACE_COMPONENT_FIELDS if plugin_ref.get(f)]
        plugin_components = [f for f in MARKETPLACE_COMPONENT_FIELDS if source_manifest.get(f)]
        if market_components and plugin_components and plugin_ref.get("strict") is not True:
            errors.append(
                "marketplace.json plugins[0]: component fields "
                f"{market_components} conflict with plugin.json "
                f"components {plugin_components}; remove components from one "
                "manifest or set strict: true "
                "(Claude Code rejects the install otherwise)"
            )


def _check_server_json_contract(
    server: dict,
    *,
    expected_version: str,
    project_name: str,
    errors: list[str],
) -> None:
    expected_schema = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
    if server.get("$schema") != expected_schema:
        errors.append(f"server.json $schema: expected {expected_schema}")

    packages = server.get("packages") or []
    if not packages:
        errors.append("server.json: missing packages[0]")
        return

    package = packages[0]
    if package.get("registryType") != "pypi":
        errors.append("server.json packages[0].registryType: expected pypi")
    if package.get("identifier") != project_name:
        errors.append(
            f"server.json packages[0].identifier: got '{package.get('identifier')}', expected '{project_name}'"
        )
    if package.get("version") != expected_version:
        errors.append(f"server.json packages[0].version: got '{package.get('version')}', expected '{expected_version}'")
    transport = package.get("transport") or {}
    if transport.get("type") != "stdio":
        errors.append("server.json packages[0].transport.type: expected stdio")


def _read_pyproject_array(section: str, key: str) -> list[str]:
    text = (common.ROOT / "pyproject.toml").read_text(encoding="utf-8")
    section_match = re.search(
        rf"^\[{re.escape(section)}\]\s*$([\s\S]*?)(?=^\[|\Z)",
        text,
        re.M,
    )
    if not section_match:
        return []
    key_match = re.search(
        rf"^{re.escape(key)}\s*=\s*\[([\s\S]*?)\]",
        section_match.group(1),
        re.M,
    )
    if not key_match:
        return []
    return re.findall(r'["\']([^"\']+)["\']', key_match.group(1))


def _requirement_name(requirement: str) -> str:
    name = re.split(r"[<>=!~;\[]", requirement.strip(), maxsplit=1)[0]
    return name.lower().replace("_", "-")


def _check_python_package_contract(errors: list[str]) -> None:
    """Ensure PyPI package metadata can run the same shipped runtime paths."""
    pyproject_deps = {_requirement_name(dep) for dep in _read_pyproject_array("project", "dependencies")}
    requirements = {
        _requirement_name(line)
        for line in (common.ROOT / "plugin/requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    for dep in sorted(requirements - pyproject_deps):
        errors.append(f"pyproject.toml dependencies: missing runtime dependency {dep} from plugin/requirements.txt")

    packages = set(_read_pyproject_array("tool.hatch.build.targets.wheel", "packages"))
    if "plugin/ui" not in packages:
        errors.append("pyproject.toml wheel packages: missing plugin/ui for inbox_dashboard UI runtime")


def _check_source_syntax(errors: list[str]) -> None:
    """Catch startup syntax failures before a fresh artifact is shipped."""
    start_script = common.ROOT / "plugin/start_mcp.sh"
    if start_script.exists():
        result = subprocess.run(
            ["bash", "-n", str(start_script)],
            cwd=common.ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            suffix = f" {detail[-1]}" if detail else ""
            errors.append(f"plugin/start_mcp.sh: shell syntax error:{suffix}")

    for base in ("plugin/apple_mail_mcp.py", "plugin/apple_mail_mcp", "plugin/ui"):
        path = common.ROOT / base
        paths = [path] if path.is_file() else sorted(path.rglob("*.py")) if path.exists() else []
        for py_file in paths:
            rel = py_file.relative_to(common.ROOT).as_posix()
            try:
                compile(py_file.read_text(encoding="utf-8"), rel, "exec")
            except SyntaxError as exc:
                errors.append(f"{rel}: python syntax error: {exc.msg} at line {exc.lineno}")
