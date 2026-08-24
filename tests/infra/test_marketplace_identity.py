"""Regression coverage for the central and standalone marketplace identities."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _json(relative_path: str) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def test_marketplace_identity_declares_central_promotion_boundary() -> None:
    identity = _json("tools/marketplace_identity.json")

    assert identity["schema_version"] == 1
    assert identity["plugin"] == {
        "id": "apple-mail",
        "source_repository": "https://github.com/Agentic-Assets/apple-mail-mcp",
        "source_payload": "plugin",
    }
    assert identity["primary_marketplace"] == {
        "display_name": "Agentic Assets",
        "id": "agentic-assets",
        "repository": "https://github.com/Agentic-Assets/Agentic-Assets-Marketplace",
        "selector": "apple-mail@agentic-assets",
        "payload_destination": "plugins/apple-mail",
    }
    assert identity["promotion"] == {
        "source_ref": "immutable-signed-tag",
        "source_repository_allowlist_required": True,
        "payload_edit_policy": "promote-only",
        "policy_owner": "marketplace",
        "evidence_owner": "marketplace",
        "attestation_owner": "marketplace",
    }


def test_standalone_marketplace_manifests_keep_compatibility_identity() -> None:
    identity = _json("tools/marketplace_identity.json")
    compatibility = identity["standalone_compatibility"]

    assert compatibility == {
        "purpose": "development-public-standalone-compatibility",
        "marketplace_id": "apple-mail-mcp",
        "selector": "apple-mail@apple-mail-mcp",
        "manifests": [
            ".claude-plugin/marketplace.json",
            ".agents/plugins/marketplace.json",
            ".cursor-plugin/marketplace.json",
        ],
        "rename": False,
    }

    for manifest_path in compatibility["manifests"]:
        manifest = _json(manifest_path)
        assert manifest["name"] == compatibility["marketplace_id"]
        assert manifest["plugins"][0]["name"] == identity["plugin"]["id"]


def test_cursor_marketplace_catalog_points_at_shared_plugin_runtime() -> None:
    """The root Cursor catalog is a standalone compatibility catalog over the shared plugin/ payload."""
    identity = _json("tools/marketplace_identity.json")
    catalog = _json(".cursor-plugin/marketplace.json")
    manifest = _json("plugin/.cursor-plugin/plugin.json")

    assert catalog["name"] == identity["standalone_compatibility"]["marketplace_id"]
    assert catalog["owner"] == {"name": identity["primary_marketplace"]["display_name"]}
    assert catalog["plugins"][0]["source"] == "./" + identity["plugin"]["source_payload"]
    assert catalog["plugins"][0]["name"] == manifest["name"] == identity["plugin"]["id"]

    # Public repo: the listing carries no contact address, and every asset it
    # references is committed under plugin/ so Cursor can resolve it by relative path.
    assert "email" not in manifest["author"]
    assert "email" not in catalog["owner"]
    assert (ROOT / "plugin" / manifest["logo"]).is_file()
    assert (ROOT / "plugin" / manifest["skills"]).is_dir()
    assert (ROOT / "plugin" / "README.md").is_file()


def test_user_and_maintainer_docs_preserve_the_identity_boundary() -> None:
    primary_docs = [
        "README.md",
        "plugin/skills/email-management/README.md",
    ]
    maintainer_docs = [
        "AGENTS.md",
        "CLAUDE.md",
        ".claude-plugin/CLAUDE.md",
        "plugin/docs/CLAUDE.md",
        "tools/CLAUDE.md",
        "docs/CLAUDE.md",
        "docs/CLAUDE-conventions.md",
    ]

    for path in primary_docs:
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "Agentic-Assets/Agentic-Assets-Marketplace" in text, path
        assert "apple-mail@agentic-assets" in text, path

    for path in maintainer_docs:
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "tools/marketplace_identity.json" in text, path
        assert "apple-mail@agentic-assets" in text, path
        assert "apple-mail@apple-mail-mcp" in text, path

    for path in ["README.md", "tools/CLAUDE.md"]:
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "refresh-central-marketplace.sh" in text, path
        assert "refresh-local-plugins.sh" in text, path


def test_bundled_skill_docs_do_not_advertise_standalone_selector() -> None:
    legacy_selector = "apple-mail@apple-mail-mcp"
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "plugin" / "skills").rglob("*.md")
        if legacy_selector in path.read_text(encoding="utf-8")
    ]

    assert not offenders, (
        "Bundled skill documentation must use the central marketplace selector; "
        f"found {legacy_selector} in: {', '.join(offenders)}"
    )
