"""Regression tests for core.fetch_replied_ids silent-except bug (Bug 2).

Before the fix, the function caught all non-timeout exceptions silently and
returned an empty set without logging anything.  Real failures (OSError,
PermissionError, RuntimeError from a broken Mail.app connection) were masked,
causing get_awaiting_reply to falsely flag every sent message as awaiting
reply.

Fix: keep the graceful degradation (return empty set so callers never crash)
but emit a WARNING-level log message with the exception class + message so
operators can detect broken AppleScript connectivity.
"""

import logging
import unittest
from unittest.mock import patch

import pytest
from apple_mail_mcp.core import SentReplySnapshot, fetch_replied_ids, fetch_sent_reply_snapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_runner_raising(exc: Exception):
    """Return a runner callable that always raises *exc*."""

    def _runner(script, timeout=60):
        raise exc

    return _runner


def _fake_runner_returning(output: str):
    """Return a runner callable that always returns *output*."""

    def _runner(script, timeout=60):
        return output

    return _runner


# ---------------------------------------------------------------------------
# Tests: non-timeout exceptions must log a WARNING and return empty set
# ---------------------------------------------------------------------------


class FetchRepliedIdsNonTimeoutExceptionTests(unittest.TestCase):
    """fetch_replied_ids must log WARNING for every non-AppleScriptTimeout error."""

    def _assert_warning_logged_and_empty_set_returned(self, exc: Exception, caplog):
        """Common assertion: call returns set(), WARNING is emitted."""
        with caplog.at_level(logging.WARNING, logger="apple_mail_mcp.core"):
            result = fetch_replied_ids(
                account="Work",
                runner=_fake_runner_raising(exc),
            )

        self.assertIsInstance(result, set)
        self.assertEqual(result, set(), f"Expected empty set on {type(exc).__name__}")

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        self.assertTrue(
            warning_records,
            f"Expected at least one WARNING log when {type(exc).__name__} is raised, got none",
        )
        # The log message should mention the exception in some way.
        combined_msg = " ".join(r.getMessage() for r in warning_records)
        self.assertTrue(
            str(exc) in combined_msg or type(exc).__name__ in combined_msg,
            f"WARNING message should reference the exception; got: {combined_msg!r}",
        )


@pytest.fixture
def caplog_fixture(caplog):
    return caplog


class FetchRepliedIdsPytestTests:
    """pytest-native tests using caplog fixture — run via pytest not unittest."""


def test_runtime_error_logs_warning_and_returns_empty_set(caplog):
    exc = RuntimeError("AppleScript error: -1728")
    with caplog.at_level(logging.WARNING, logger="apple_mail_mcp.core"):
        result = fetch_replied_ids(account="Work", runner=_fake_runner_raising(exc))

    assert result == set()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "Expected WARNING log for RuntimeError"
    combined = " ".join(r.getMessage() for r in warnings)
    assert "AppleScript error" in combined or "RuntimeError" in combined


def test_os_error_logs_warning_and_returns_empty_set(caplog):
    exc = OSError("Broken pipe")
    with caplog.at_level(logging.WARNING, logger="apple_mail_mcp.core"):
        result = fetch_replied_ids(account="Work", runner=_fake_runner_raising(exc))

    assert result == set()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "Expected WARNING log for OSError"
    combined = " ".join(r.getMessage() for r in warnings)
    assert "Broken pipe" in combined or "OSError" in combined


def test_permission_error_logs_warning_and_returns_empty_set(caplog):
    exc = PermissionError("Access denied to Mail.app")
    with caplog.at_level(logging.WARNING, logger="apple_mail_mcp.core"):
        result = fetch_replied_ids(account="Work", runner=_fake_runner_raising(exc))

    assert result == set()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "Expected WARNING log for PermissionError"
    combined = " ".join(r.getMessage() for r in warnings)
    assert "Access denied" in combined or "PermissionError" in combined


def test_generic_exception_logs_warning_and_returns_empty_set(caplog):
    exc = Exception("something went wrong")
    with caplog.at_level(logging.WARNING, logger="apple_mail_mcp.core"):
        result = fetch_replied_ids(account="Work", runner=_fake_runner_raising(exc))

    assert result == set()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "Expected WARNING log for generic Exception"


def test_does_not_raise_on_exception(caplog):
    """fetch_replied_ids must never propagate an exception to the caller."""
    for exc in [RuntimeError("x"), OSError("y"), PermissionError("z"), ValueError("w")]:
        with caplog.at_level(logging.WARNING):
            result = fetch_replied_ids(account="Work", runner=_fake_runner_raising(exc))
        assert isinstance(result, set)


# ---------------------------------------------------------------------------
# Tests: timeout must NOT log a warning (handled separately, silently)
# ---------------------------------------------------------------------------


def test_timeout_returns_empty_set_without_warning(caplog):
    """AppleScriptTimeout should still return empty set but NOT log a warning."""
    from apple_mail_mcp.core import AppleScriptTimeout

    exc = AppleScriptTimeout("timed out")
    with caplog.at_level(logging.WARNING, logger="apple_mail_mcp.core"):
        result = fetch_replied_ids(account="Work", runner=_fake_runner_raising(exc))

    assert result == set()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not warnings, "Timeout should not produce a WARNING log"


# ---------------------------------------------------------------------------
# Tests: happy path must return parsed IDs with NO warning
# ---------------------------------------------------------------------------


def test_happy_path_returns_parsed_ids(caplog):
    output = "<msg-001@example.com>\n<msg-002@example.com>\n"
    with caplog.at_level(logging.WARNING, logger="apple_mail_mcp.core"):
        result = fetch_replied_ids(account="Work", runner=_fake_runner_returning(output))

    assert "<msg-001@example.com>" in result
    assert "<msg-002@example.com>" in result
    assert len(result) == 2

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not warnings, "No WARNING should be logged on success"


def test_happy_path_normalizes_ids_without_angle_brackets(caplog):
    output = "msg-003@example.com\nmsg-004@example.com\n"
    with caplog.at_level(logging.WARNING, logger="apple_mail_mcp.core"):
        result = fetch_replied_ids(account="Work", runner=_fake_runner_returning(output))

    assert "<msg-003@example.com>" in result
    assert "<msg-004@example.com>" in result
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not warnings


def test_happy_path_empty_output_returns_empty_set(caplog):
    with caplog.at_level(logging.WARNING, logger="apple_mail_mcp.core"):
        result = fetch_replied_ids(account="Work", runner=_fake_runner_returning(""))

    assert result == set()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not warnings


def test_sent_reply_snapshot_complete_nonmatch_is_false():
    snapshot = fetch_sent_reply_snapshot(
        "Work",
        runner=_fake_runner_returning("REPLY|||<matched@example.com>\nSCANNED|||2\nTOTAL|||2"),
    )
    assert snapshot.status == "ok"
    assert snapshot.scanned == 2
    assert snapshot.total == 2
    assert snapshot.truncated is False
    assert snapshot.matches("<other@example.com>") is False


def test_sent_reply_snapshot_match_survives_truncation():
    snapshot = fetch_sent_reply_snapshot(
        "Work",
        runner=_fake_runner_returning("REPLY|||<matched@example.com>\nSCANNED|||10\nTOTAL|||25"),
    )
    assert snapshot.truncated is True
    assert snapshot.matches("matched@example.com") is True
    assert snapshot.matches("<other@example.com>") is None


def test_sent_reply_snapshot_error_nonmatch_is_unknown():
    snapshot = fetch_sent_reply_snapshot(
        "Work",
        runner=_fake_runner_returning(
            "REPLY|||<matched@example.com>\nERROR|||one header failed\nSCANNED|||2\nTOTAL|||2"
        ),
    )
    assert snapshot.status == "error"
    assert snapshot.errors == ("one header failed",)
    assert snapshot.matches("<matched@example.com>") is True
    assert snapshot.matches("<other@example.com>") is None


def test_sent_reply_snapshot_missing_id_is_unknown_even_when_complete():
    snapshot = SentReplySnapshot(status="ok", scanned=0, total=0)
    assert snapshot.matches(None) is None
