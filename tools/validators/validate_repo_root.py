#!/usr/bin/env python3
"""Enforce a tight allowlist at the repository root.

Ephemeral reports, dated handoffs, and scratch artifacts belong under
``docs/``, ``tasks/active/``, ``tasks/reference/``, or ``tasks/archive/``,
not loose at the repo root.

Exit 0 when compliant; exit 1 and print violations to stderr otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ALLOWED_ROOT_FILES = frozenset(
    {
        "AGENTS.md",
        "CHANGELOG.md",
        "CLAUDE.md",
        "LICENSE",
        "PRIVACY.md",
        "README.md",
        "SECURITY.md",
        "pyproject.toml",
        "server.json",
        "skills-lock.json",
    }
)

ALLOWED_ROOT_FILE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^apple-mail-mcp-v\d+\.\d+\.\d+\.mcpb$"),
    re.compile(r"^apple-mail-plugin\.zip$"),
    re.compile(r"^apple-mail\.plugin$"),
)

ALLOWED_ROOT_DIRS = frozenset(
    {
        "apple-mail-mcpb",
        "archive",
        "distribution",
        "docs",
        "plugin",
        "provenance",
        "tasks",
        "tests",
        "tools",
    }
)

ALLOWED_HIDDEN_DIRS = frozenset(
    {
        ".agents",
        ".claude",
        ".claude-plugin",
        ".codex",
        ".cursor",
        ".cursor-plugin",
        ".github",
        ".git",
    }
)

SUGGESTED_HOME = (
    "move under docs/, tasks/active/, tasks/reference/, or tasks/archive/"
)


def _is_allowed_root_file(name: str) -> bool:
    if name in ALLOWED_ROOT_FILES:
        return True
    return any(pattern.fullmatch(name) for pattern in ALLOWED_ROOT_FILE_PATTERNS)


def validate_repo_root(root: Path | None = None) -> list[str]:
    """Return validation errors for unexpected repo-root entries."""
    base = root or ROOT
    errors: list[str] = []

    if not base.is_dir():
        return [f"missing repository root: {base}"]

    for child in base.iterdir():
        name = child.name
        if name.startswith("."):
            if child.is_dir() and name in ALLOWED_HIDDEN_DIRS:
                continue
            # Local dev artifacts (.venv, .pytest_cache, .DS_Store, etc.)
            continue

        if child.is_file():
            if not _is_allowed_root_file(name):
                errors.append(
                    f"unexpected file at repo root: {name} ({SUGGESTED_HOME})"
                )
        elif child.is_dir() and name not in ALLOWED_ROOT_DIRS:
            errors.append(
                f"unexpected directory at repo root: {name}/ ({SUGGESTED_HOME})"
            )

    return errors


def main() -> int:
    errors = validate_repo_root()
    if errors:
        print("repo root validation FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print("repo root: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
