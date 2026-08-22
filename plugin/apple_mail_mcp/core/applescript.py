"""Low-level osascript execution, the runner Protocol, and the timeout exception."""

import errno
import fcntl
import os
import pwd
import stat
import subprocess
import threading
import time
from pathlib import Path
from typing import Protocol

from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.core.escaping import _sanitize_for_json

DEFAULT_TIMEOUT_S = 120
# Upper bound on a single call's deadline. Two measured reasons, not style:
# (1) ``subprocess.run`` raises a bare ``OverflowError`` ("timeout is too
#     large") above 2_147_483 s (INT_MAX ms in poll()). That is neither
#     SubprocessError nor OSError, so the handlers below re-raise it unwrapped
#     and the caller sees an error naming neither AppleScript nor the argument.
# (2) ``_LOCK_WAIT_TIMEOUT`` bounds how long a caller *waits* for the
#     single-flight lock but nothing bounds how long one *holds* it. An
#     unbounded deadline lets one call starve every other Mail call in the
#     process. 3600 s is 12x the largest default any tool passes (300).
MAX_TIMEOUT_S = 3600


class AppleScriptRunner(Protocol):
    """Callable shape for injectable AppleScript runners."""

    def __call__(self, script: str, timeout: int | None = DEFAULT_TIMEOUT_S) -> str: ...


class AppleScriptTimeout(Exception):
    """Raised when an AppleScript invocation exceeds its per-call timeout."""


# Mail.app's AppleScript bridge is effectively single-threaded: concurrent
# osascript invocations (from parallel tool calls or internal fan-out) thrash
# Mail.app instead of running in parallel, causing CPU spin and timeouts.
# This lock makes every subprocess.run(["osascript", ...]) call single-flight
# across the whole process. It is a plain threading.Lock (not RLock, not an
# asyncio primitive) because run_applescript is a synchronous function called
# both from asyncio.to_thread worker threads and from the plain-sync CLI; a
# blocking, thread-safe mutex is what both call paths need.
_MAIL_LOCK = threading.Lock()
_LOCK_WAIT_TIMEOUT = 300

# Different plugin hosts (and different installed versions) run in separate
# processes, so ``_MAIL_LOCK`` alone cannot protect Mail.app from concurrent
# UI automation. This stable per-user cache location deliberately does not
# depend on a plugin install path, version, or a host's ``HOME`` override, and
# its private directory keeps other local users from replacing the lock target.


def _effective_user_cache_directory() -> Path:
    """Return the effective user's canonical macOS cache location."""
    return Path(pwd.getpwuid(os.geteuid()).pw_dir) / "Library" / "Caches" / "apple-mail-mcp"


_PROCESS_LOCK_DIRECTORY = _effective_user_cache_directory()
_PROCESS_LOCK_FILENAME = "mail-ui.lock"
_PROCESS_LOCK_POLL_S = 0.05


def _process_lock_error() -> ToolError:
    """Return the safe failure used when the shared lock cannot be trusted."""
    return ToolError(
        code="APPLE_SCRIPT_LOCK_UNAVAILABLE",
        message="AppleScript execution cannot safely acquire its shared Mail lock.",
        remediation={
            "hint": (
                "Close active plugin hosts, make sure the private Apple Mail cache directory is "
                "accessible and not symlinked, then retry the operation."
            )
        },
    )


def _open_process_mail_lock_directory() -> int:
    """Open the private cache directory only when its target is safe to use."""
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory_flag is None:
        raise _process_lock_error()
    try:
        _PROCESS_LOCK_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            _PROCESS_LOCK_DIRECTORY,
            os.O_RDONLY | directory_flag | nofollow | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise _process_lock_error() from exc

    try:
        directory_stat = os.fstat(descriptor)
        if not stat.S_ISDIR(directory_stat.st_mode) or directory_stat.st_uid != os.geteuid():
            raise _process_lock_error()
        if (directory_stat.st_mode & 0o7777) != 0o700:
            try:
                os.fchmod(descriptor, 0o700)
            except OSError as exc:
                raise _process_lock_error() from exc
            directory_stat = os.fstat(descriptor)
        unsafe_directory = (
            not stat.S_ISDIR(directory_stat.st_mode)
            or directory_stat.st_uid != os.geteuid()
            or (directory_stat.st_mode & 0o7777) != 0o700
        )
        if unsafe_directory:
            raise _process_lock_error()
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_process_mail_lock() -> int:
    """Open the stable interprocess lock only when its target is safe to use.

    The lock contains no data, but following a symlink or accepting another
    user's file would allow an unrelated target to control Mail serialization.
    ``O_NOFOLLOW`` plus a verified directory descriptor make the opens
    race-safe; descriptor checks validate the actual object opened rather than
    a path observed earlier.
    """
    directory_descriptor = _open_process_mail_lock_directory()
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(_PROCESS_LOCK_FILENAME, flags, 0o600, dir_fd=directory_descriptor)
    except OSError as exc:
        raise _process_lock_error() from exc
    finally:
        os.close(directory_descriptor)

    try:
        lock_stat = os.fstat(descriptor)
        unsafe_target = (
            not stat.S_ISREG(lock_stat.st_mode)
            or lock_stat.st_uid != os.geteuid()
            or lock_stat.st_nlink != 1
            or (lock_stat.st_mode & 0o7777) != 0o600
        )
        if unsafe_target:
            raise _process_lock_error()
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _acquire_process_mail_lock(deadline: float) -> int:
    """Acquire the process-wide Mail lock before *deadline*, returning its fd."""
    descriptor = _open_process_mail_lock()
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AppleScriptTimeout(
                        "AppleScript queued too long waiting for Mail.app to become available"
                    ) from None
                time.sleep(min(_PROCESS_LOCK_POLL_S, remaining))
            except OSError as exc:
                if exc.errno != errno.EINTR:
                    raise _process_lock_error() from exc
    except BaseException:
        os.close(descriptor)
        raise


def _release_process_mail_lock(descriptor: int) -> None:
    """Release and close the advisory lock; process exit also releases it."""
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _resolve_timeout(timeout: int | None) -> int | float:
    """Return the per-call deadline, refusing values osascript cannot honour.

    ``None`` means "use the default", not "no deadline". Non-positive values
    are refused rather than clamped: ``subprocess.run`` treats them as already
    expired and kills osascript within ~2 ms, then this module reports
    ``AppleScriptTimeout("AppleScript execution timed out")`` — blaming Mail.app
    for what is really a caller bug. Clamping would swap that misdirection for
    a silently different deadline; refusing names the actual cause. AppleScript
    itself never objects: ``with timeout of -5 seconds`` compiles and runs
    clean, so no osascript-side check can ever catch this.
    """
    if timeout is None:
        return DEFAULT_TIMEOUT_S
    # ``bool`` is a subclass of ``int``, so a bare isinstance check would let
    # ``timeout=True`` through as a 1-second deadline — a near-instant, silent
    # timeout blamed on Mail. Reject it as the non-number it is.
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ToolError(
            code="INVALID_TIMEOUT",
            message=f"timeout must be a number of seconds or None; got {timeout!r}.",
            remediation={"hint": f"Pass an integer in (0, {MAX_TIMEOUT_S}], or None for {DEFAULT_TIMEOUT_S}s."},
        )
    if timeout <= 0:
        raise ToolError(
            code="INVALID_TIMEOUT",
            message=f"timeout must be greater than 0 seconds; got {timeout!r}.",
            remediation={"hint": f"Pass a positive number of seconds, or None for the {DEFAULT_TIMEOUT_S}s default."},
        )
    if timeout > MAX_TIMEOUT_S:
        raise ToolError(
            code="INVALID_TIMEOUT",
            message=f"timeout must be at most {MAX_TIMEOUT_S} seconds; got {timeout!r}.",
            remediation={"hint": "Split the work into bounded calls instead of raising the per-call deadline."},
        )
    return timeout


def run_applescript(script: str, timeout: int | None = DEFAULT_TIMEOUT_S) -> str:
    """Execute AppleScript via stdin pipe for reliable multi-line handling.

    Raises ``AppleScriptTimeout`` (subclass of Exception) on per-call timeout
    so callers can isolate slow-account failures without losing siblings'
    partial results.

    Serializes the actual ``osascript`` invocation behind both the in-process
    ``_MAIL_LOCK`` and a user-private advisory file lock, so one AppleScript
    call reaches Mail.app across all plugin hosts. Callers that wait longer than
    ``_LOCK_WAIT_TIMEOUT`` seconds total for their turn raise
    ``AppleScriptTimeout`` instead of queuing indefinitely.

    Raises ``ToolError(code="INVALID_TIMEOUT")`` for a non-positive or
    out-of-range ``timeout``, before the lock is taken, so a bad argument
    never queues behind live Mail work and never reaches ``osascript``.
    """
    effective_timeout = _resolve_timeout(timeout)
    queue_deadline = time.monotonic() + _LOCK_WAIT_TIMEOUT
    if not _MAIL_LOCK.acquire(timeout=_LOCK_WAIT_TIMEOUT):
        raise AppleScriptTimeout("AppleScript queued too long waiting for Mail.app to become available")
    process_lock_descriptor: int | None = None
    try:
        process_lock_descriptor = _acquire_process_mail_lock(queue_deadline)
        try:
            result = subprocess.run(
                ["osascript", "-"],
                input=script.encode("utf-8"),
                capture_output=True,
                timeout=effective_timeout,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace").strip()
                if stderr:
                    raise Exception(f"AppleScript error: {stderr}")
                raise Exception(f"AppleScript exited with code {result.returncode} (no stderr)")
            output = result.stdout.decode("utf-8", errors="replace").strip()
            return _sanitize_for_json(output)
        except subprocess.TimeoutExpired as exc:
            raise AppleScriptTimeout("AppleScript execution timed out") from exc
        except AppleScriptTimeout:
            raise
        except (subprocess.SubprocessError, OSError) as exc:
            raise Exception(f"AppleScript execution failed: {exc}") from exc
        except Exception:
            raise
    finally:
        if process_lock_descriptor is not None:
            _release_process_mail_lock(process_lock_descriptor)
        _MAIL_LOCK.release()
