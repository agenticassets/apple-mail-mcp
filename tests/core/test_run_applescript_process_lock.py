"""Cross-process serialization tests for ``run_applescript``.

These exercise the real advisory lock but never invoke ``osascript``. A spawned
process is intentionally given the same isolated lock directory, so the holder
has an independent process-wide ``flock`` while the test process can assert
that its contender never reaches ``subprocess.run``.
"""

from __future__ import annotations

import multiprocessing
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.core import applescript

_SCRIPT = 'return "ok"'


def test_process_lock_location_ignores_home_environment_override(tmp_path, monkeypatch):
    """Plugin hosts with different HOME values must still share the UID lock."""
    expected_directory = applescript._PROCESS_LOCK_DIRECTORY
    monkeypatch.setenv("HOME", str(tmp_path))
    assert applescript._effective_user_cache_directory() == expected_directory


@pytest.fixture
def isolated_process_lock(tmp_path, monkeypatch):
    """Point one test at a private directory before forking it."""
    lock_directory = tmp_path / "private-process-lock"
    monkeypatch.setattr(applescript, "_PROCESS_LOCK_DIRECTORY", lock_directory)
    yield lock_directory


def _hold_process_mail_lock(
    lock_directory: str,
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    """Hold the real process lock until the parent signals release."""
    applescript._PROCESS_LOCK_DIRECTORY = Path(lock_directory)
    descriptor = applescript._acquire_process_mail_lock(time.monotonic() + 5)
    ready.set()
    try:
        release.wait(timeout=10)
    finally:
        applescript._release_process_mail_lock(descriptor)


@contextmanager
def _held_process_mail_lock(lock_directory: Path):
    """Yield a child holding the shared lock, then always reap that child."""
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(target=_hold_process_mail_lock, args=(str(lock_directory), ready, release))
    holder.start()
    try:
        assert ready.wait(timeout=5), "holder did not acquire the shared process lock"
        yield release, holder
    finally:
        release.set()
        holder.join(timeout=5)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=5)
        assert holder.exitcode == 0, f"holder exited unexpectedly with {holder.exitcode}"


def _completed_process():
    return type("_Completed", (), {"returncode": 0, "stdout": b"ok", "stderr": b""})()


def test_process_lock_contender_times_out_before_starting_osascript(isolated_process_lock):
    """A different MCP process must not begin a competing Mail operation."""
    with (
        _held_process_mail_lock(isolated_process_lock),
        patch.object(applescript, "_LOCK_WAIT_TIMEOUT", 0.1),
        patch.object(
            applescript.subprocess,
            "run",
            side_effect=AssertionError("contender reached osascript despite the process lock"),
        ) as mock_run,
        pytest.raises(applescript.AppleScriptTimeout, match="queued too long"),
    ):
        applescript.run_applescript(_SCRIPT, timeout=1)
    assert not mock_run.called


def test_process_lock_release_allows_the_next_osascript_call(isolated_process_lock):
    """Releasing one process's flock admits the next process immediately."""
    with _held_process_mail_lock(isolated_process_lock) as (release, holder):
        release.set()
        holder.join(timeout=5)
        assert holder.exitcode == 0, "holder failed to release the shared lock"
        with patch.object(applescript.subprocess, "run", return_value=_completed_process()) as mock_run:
            assert applescript.run_applescript(_SCRIPT, timeout=1) == "ok"
    assert mock_run.call_count == 1


def test_symlinked_process_lock_target_is_rejected_before_osascript(tmp_path, monkeypatch):
    """The shared lock must fail closed rather than follow a local symlink."""
    target = tmp_path / "target"
    target.touch()
    lock_directory = tmp_path / "private-process-lock"
    lock_directory.mkdir(mode=0o700)
    unsafe_path = lock_directory / applescript._PROCESS_LOCK_FILENAME
    unsafe_path.symlink_to(target)
    monkeypatch.setattr(applescript, "_PROCESS_LOCK_DIRECTORY", lock_directory)
    try:
        with patch.object(applescript.subprocess, "run") as mock_run, pytest.raises(ToolError) as excinfo:
            applescript.run_applescript(_SCRIPT, timeout=1)
    finally:
        unsafe_path.unlink(missing_ok=True)
    assert excinfo.value.code == "APPLE_SCRIPT_LOCK_UNAVAILABLE"
    assert not mock_run.called


def test_symlinked_process_lock_directory_is_rejected_before_osascript(tmp_path, monkeypatch):
    """The cache directory itself must not be redirected through a symlink."""
    target_directory = tmp_path / "target-directory"
    target_directory.mkdir(mode=0o700)
    unsafe_directory = tmp_path / "unsafe-directory"
    unsafe_directory.symlink_to(target_directory, target_is_directory=True)
    monkeypatch.setattr(applescript, "_PROCESS_LOCK_DIRECTORY", unsafe_directory)
    with patch.object(applescript.subprocess, "run") as mock_run, pytest.raises(ToolError) as excinfo:
        applescript.run_applescript(_SCRIPT, timeout=1)
    assert excinfo.value.code == "APPLE_SCRIPT_LOCK_UNAVAILABLE"
    assert not mock_run.called
