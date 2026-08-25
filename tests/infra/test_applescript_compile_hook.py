"""Regression tests for the AppleScript compile-check hook.

The hook (``.claude/hooks/check_applescript_compiles.py``) is the only gate
that reads the AppleScript this server actually emits. It shipped for months
in a state where it exited 0 on the five largest script modules having
compiled *zero* scripts: it loaded each file with ``spec_from_file_location``
under its package-qualified name, the parent package's ``__init__`` then
re-imported symbols from that half-built module, and the resulting
``ImportError`` was swallowed as a stderr warning that
``post_edit_check.sh`` never surfaces (it only forwards stderr on a non-zero
exit). A gate that cannot fail is worth nothing, so these tests hold two
things: that the hook really compiles the emitted scripts, and that every
"could not check" outcome is loud rather than a silent pass.

Every behavioral test runs the hook as a **subprocess**. It monkeypatches
``run_applescript`` across the whole imported package, so running it in-process
would poison the rest of the pytest session.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".claude" / "hooks" / "check_applescript_compiles.py"
PLUGIN_PACKAGE = ROOT / "plugin" / "apple_mail_mcp"

requires_osacompile = pytest.mark.skipif(
    shutil.which("osacompile") is None,
    reason="osacompile is macOS-only; the hook legitimately skips without it",
)


def _load_hook_module():
    """Import the hook for its constants only — never call ``check_module``."""
    name = "_applescript_compile_hook"
    spec = importlib.util.spec_from_file_location(name, HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec because ``@dataclass`` resolves annotations through
    # ``sys.modules[cls.__module__]`` and raises without it.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _run(args: list[str], *, cwd: Path = ROOT, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK), *args],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _run_in(fake_repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(fake_repo / ".claude" / "hooks" / HOOK.name), *args],
        cwd=fake_repo,
        capture_output=True,
        text=True,
        timeout=180,
    )


# --------------------------------------------------------------------------
# The hook against the real tree
# --------------------------------------------------------------------------

# Modules whose emitted AppleScript the hook must actually compile. These are
# exactly the five that silently checked nothing before the import fix, plus
# the module whose 3.3.0 regression the hook exists to catch.
MUST_COMPILE = [
    "tools/search/script.py",
    "tools/search/thread.py",
    "tools/search/by_id.py",
    "tools/inbox/list_scripts.py",
    "tools/analytics/export_helpers.py",
    "tools/smart_inbox/awaiting_reply.py",
]


@requires_osacompile
@pytest.mark.parametrize("rel", MUST_COMPILE)
def test_hook_compiles_scripts_for_module(rel: str) -> None:
    """Each module reports a positive compiled-script count, not a silent pass."""
    result = _run(["--report", str(PLUGIN_PACKAGE / rel)])
    assert result.returncode == 0, result.stderr
    summary = result.stdout.strip()
    assert "compiled " in summary, f"{rel} checked nothing: {summary}"
    compiled = int(summary.split("compiled ", 1)[1].split(" ", 1)[0])
    assert compiled >= 1, summary


@requires_osacompile
def test_whole_package_has_no_unchecked_applescript() -> None:
    """No module may emit AppleScript the hook cannot reach and nobody recorded."""
    sources = sorted(str(p) for p in PLUGIN_PACKAGE.rglob("*.py"))
    result = _run(["--report", *sources])
    assert result.returncode == 0, result.stderr
    assert "STALE LEDGER ENTRY" not in result.stdout, result.stdout


@requires_osacompile
def test_hook_never_executes_applescript(tmp_path: Path) -> None:
    """The executor guard holds: checking a module never shells out to osascript.

    ``_run_trash_script`` is named like a builder but *executes* a script, so a
    hook that called builders without a guard would drive Mail.app from an edit
    hook.
    """
    shim = tmp_path / "bin"
    shim.mkdir()
    log = tmp_path / "osascript.log"
    (shim / "osascript").write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit 1\n', encoding="utf-8")
    (shim / "osascript").chmod(0o755)

    import os

    env = dict(os.environ, PATH=f"{shim}:{os.environ['PATH']}")
    result = subprocess.run(
        [sys.executable, str(HOOK), str(PLUGIN_PACKAGE / "tools" / "manage" / "trash.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert not log.exists(), log.read_text(encoding="utf-8")


def test_no_argument_is_a_loud_failure() -> None:
    """Invoked with nothing to check, the hook must not report a clean check."""
    result = _run([], stdin="")
    assert result.returncode == 2
    assert "no module path given" in result.stderr


@requires_osacompile
def test_hook_accepts_posttooluse_json_on_stdin() -> None:
    """The PostToolUse payload form must reach the same check as an argv path."""
    payload = json.dumps({"tool_input": {"file_path": str(PLUGIN_PACKAGE / "tools" / "search" / "script.py")}})
    result = _run(["--report"], stdin=payload)
    assert result.returncode == 0, result.stderr
    assert "compiled " in result.stdout


# --------------------------------------------------------------------------
# The UNCHECKABLE ledger
# --------------------------------------------------------------------------


def test_ledger_entries_are_real_files_with_reasons() -> None:
    """Every recorded gap names a live file and says why.

    The ledger is empty as of 2026-08 and an empty ledger is the goal state,
    not a broken test: it means no module emits AppleScript this hook cannot
    reach. Emptiness is never a silent pass either — an unreachable module
    that is *not* listed exits non-zero (``test_unreachable_inline_script_is_loud``),
    so the loud-gap machinery is exercised whether or not the ledger has rows.
    """
    hook = _load_hook_module()
    for rel, reason in hook.UNCHECKABLE.items():
        assert (PLUGIN_PACKAGE / rel).exists(), f"ledger entry {rel} no longer exists"
        assert len(reason) > 20, f"ledger entry {rel} needs a real reason, got {reason!r}"


@requires_osacompile
def test_ledger_has_no_stale_entries() -> None:
    """A ledgered module the hook can now fully check must be de-ledgered."""
    hook = _load_hook_module()
    paths = [str(PLUGIN_PACKAGE / rel) for rel in hook.UNCHECKABLE]
    if not paths:
        pytest.skip("the ledger is empty, so no entry can be stale")
    result = _run(["--report", *paths])
    assert "STALE LEDGER ENTRY" not in result.stdout, result.stdout


@requires_osacompile
def test_formerly_ledgered_modules_still_compile_a_script() -> None:
    """The three de-ledgered modules must keep compiling, not silently regress.

    Each of these built AppleScript no gate ever compiled until its tool was
    handed arguments it accepts — ``reply_runner`` a matching
    ``NativeReplyDraftIdentity`` (its script *deletes a draft*),
    ``attachments`` a ``save_path`` inside the home directory, ``export`` the
    scope/format combination that reaches the one script it builds itself.
    Without this test, a sample drifting back out of agreement would return
    them to "no AppleScript to check", which reads exactly like a clean pass.
    """
    modules = [
        "tools/compose/reply_runner.py",
        "tools/manage/attachments.py",
        "tools/analytics/export.py",
    ]
    result = _run(["--report", *(str(PLUGIN_PACKAGE / rel) for rel in modules)])
    assert result.returncode == 0, result.stderr
    for rel in modules:
        line = next(ln for ln in result.stdout.splitlines() if ln.startswith(f"plugin/apple_mail_mcp/{rel}:"))
        assert "compiled " in line, line
        assert "compiled 0 " not in line, line


# --------------------------------------------------------------------------
# The harness can fail: synthetic package, deliberately broken scripts
# --------------------------------------------------------------------------

_GOOD_SCRIPT = '''
def good_script(account: str) -> str:
    return f"""
    tell application "Mail"
        try
            set targetAccount to account "{account}"
            return name of targetAccount
        on error errMsg
            return "Error: " & errMsg
        end try
    end tell
    """
'''

_BROKEN_SCRIPT = '''
def broken_script(account: str) -> str:
    # Missing ``end try`` — exactly the 3.3.0 regression class.
    return f"""
    tell application "Mail"
        try
            set targetAccount to account "{account}"
            return name of targetAccount
        on error errMsg
            return "Error: " & errMsg
    end tell
    """
'''

_UNCALLABLE_BUILDER = '''
class Widget:
    pass


def widget_script(widget: Widget) -> str:
    return f"""
    tell application "Mail"
        return "{widget}"
    end tell
    """
'''

_INLINE_ONLY = '''
def some_tool(widget) -> str:
    script = """
    tell application "Mail"
        return name of every account
    end tell
    """
    return script.strip()
'''

_IMPORT_ERROR = "import definitely_not_a_real_module_name  # noqa: F401\n"


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A minimal repo whose layout satisfies the hook's ``parents[2]`` root."""
    hooks_dir = tmp_path / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)
    shutil.copy2(HOOK, hooks_dir / HOOK.name)

    package = tmp_path / "plugin" / "apple_mail_mcp" / "tools"
    package.mkdir(parents=True)
    (tmp_path / "plugin" / "apple_mail_mcp" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "good.py").write_text(_GOOD_SCRIPT, encoding="utf-8")
    (package / "broken.py").write_text(_BROKEN_SCRIPT, encoding="utf-8")
    (package / "uncallable.py").write_text(_UNCALLABLE_BUILDER, encoding="utf-8")
    (package / "inline_only.py").write_text(_INLINE_ONLY, encoding="utf-8")
    (package / "unimportable.py").write_text(_IMPORT_ERROR, encoding="utf-8")
    return tmp_path


@requires_osacompile
def test_known_good_builder_compiles(fake_repo: Path) -> None:
    result = _run_in(fake_repo, ["--report", "plugin/apple_mail_mcp/tools/good.py"])
    assert result.returncode == 0, result.stderr
    assert "compiled 1 script(s)" in result.stdout


@requires_osacompile
def test_broken_script_is_rejected(fake_repo: Path) -> None:
    """The negative control: a script that does not parse must exit non-zero."""
    result = _run_in(fake_repo, ["plugin/apple_mail_mcp/tools/broken.py"])
    assert result.returncode == 2, result.stdout
    assert "FAILED to compile" in result.stderr
    assert "broken_script" in result.stderr


@requires_osacompile
def test_uncallable_builder_is_loud(fake_repo: Path) -> None:
    """No usable sample kwargs is a blocking outcome, never a silent skip."""
    result = _run_in(fake_repo, ["plugin/apple_mail_mcp/tools/uncallable.py"])
    assert result.returncode == 2, result.stdout
    assert "not callable" in result.stderr
    assert "SAMPLE_KWARGS" in result.stderr


@requires_osacompile
def test_unreachable_inline_script_is_loud(fake_repo: Path) -> None:
    """AppleScript the hook cannot reach, unrecorded, must block."""
    result = _run_in(fake_repo, ["plugin/apple_mail_mcp/tools/inline_only.py"])
    assert result.returncode == 2, result.stdout
    assert "UNCHECKED" in result.stderr
    assert "UNCHECKABLE" in result.stderr


@requires_osacompile
def test_import_failure_is_loud(fake_repo: Path) -> None:
    """The exact silent skip that made this gate vacuous now blocks."""
    result = _run_in(fake_repo, ["plugin/apple_mail_mcp/tools/unimportable.py"])
    assert result.returncode == 2, result.stdout
    assert "IMPORT FAILED" in result.stderr


def test_file_outside_plugin_is_ignored(fake_repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "somewhere_else.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    result = _run_in(fake_repo, ["--report", str(outside)])
    assert result.returncode == 0
    assert result.stdout.strip() == ""
