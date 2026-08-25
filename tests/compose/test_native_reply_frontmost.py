"""The native reply path must take the foreground for itself.

System Events delivers keystrokes to whatever application is frontmost, so a
reply typed while Mail sits in the background does not fail loudly -- it types
the body into whatever window *is* in front. Mail's own ``activate`` is a
request the window server may defer, especially for a background ``osascript``,
which is exactly how this code runs under an MCP host. These tests pin the
poll-and-verify handler and the wiring that calls it, because every failure
mode here is silent or misattributed.
"""

import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import compose as compose_tools
from apple_mail_mcp.tools.compose import reply_runner
from apple_mail_mcp.tools.compose.reply_window_scripts import (
    native_reply_window_handlers_applescript,
)


def _main_reply_script(captured: list[str]) -> str:
    return next(script for script in captured if "on typeReplyBodyChunks(" in script)


def _capture_reply_script(**kwargs: object) -> str:
    captured: list[str] = []

    def fake_run(script, timeout=120):
        captured.append(script)
        if "count of outgoing messages" in script:
            return "0"
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
        compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            **kwargs,
        )
    return _main_reply_script(captured)


class EnsureMailFrontmostHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handlers = native_reply_window_handlers_applescript()

    def test_handler_polls_instead_of_trusting_a_single_activate(self):
        # One `activate` with a fixed sleep is the bug this replaces: the sleep
        # either wastes time or expires before the app switch lands.
        self.assertIn("on ensureMailFrontmost()", self.handlers)
        self.assertIn("repeat with frontAttempt from 1 to 5", self.handlers)
        self.assertIn('tell application "Mail" to activate', self.handlers)
        self.assertIn('if frontmost of process "Mail" then return "frontmost"', self.handlers)

    def test_handler_names_the_app_that_held_the_front(self):
        # Without the blocking app name the caller cannot tell "another app
        # stole focus" from "Accessibility is not granted", which have
        # completely different remedies.
        self.assertIn("set blockingApp to name of first process whose frontmost is true", self.handlers)
        self.assertIn('return "blocked:" & blockingApp', self.handlers)

    def test_accessibility_failure_is_reported_separately_from_being_blocked(self):
        # A missing Accessibility grant makes `frontmost of process` throw. That
        # must not be reported as "some app is in the way".
        self.assertIn('return "unavailable:" & frontErrMsg', self.handlers)
        self.assertIn("on frontmostBlockedApp(frontmostResult)", self.handlers)
        self.assertIn('if frontmostResult starts with "blocked:" then return text 9 thru -1', self.handlers)
        # "blocked:" is 8 characters, so the payload starts at 9. An off-by-one
        # here silently truncates or prefixes the app name in the error text.
        self.assertEqual(len("blocked:") + 1, 9)


class NativeReplyFrontmostWiringTests(unittest.TestCase):
    def test_front_is_claimed_before_the_reply_window_opens(self):
        # Claiming the front only after `reply` means the compose window is
        # rendered and adopted while Mail is still in the background.
        script = _capture_reply_script()
        claim = script.index("my frontmostBlockedApp(my ensureMailFrontmost())")
        opened = script.index("set replyMessage to reply foundMessage")
        self.assertLess(claim, opened)

    def test_front_is_reasserted_on_every_guard_attempt(self):
        # Raising a window *inside* Mail does not make Mail the frontmost
        # application, and anything can take the front back between attempts.
        script = _capture_reply_script()
        self.assertGreaterEqual(script.count("my frontmostBlockedApp(my ensureMailFrontmost())"), 2)
        guard = script.index("repeat with guardAttempt from 1 to 4")
        self.assertIn("my frontmostBlockedApp(my ensureMailFrontmost())", script[guard:])

    def test_blocked_front_is_initialized_before_use(self):
        # An unassigned AppleScript variable raises -2753 at runtime, and this
        # one is read on the abort path, which is where diagnostics matter most.
        script = _capture_reply_script()
        init = script.index('set frontmostBlockedBy to ""')
        self.assertLess(init, script.index("my frontmostBlockedApp("))

    def test_blocked_front_outranks_window_and_focus_diagnoses(self):
        # When Mail was never frontmost, adoption and AX focus were both asked
        # of a background app, so their failures are symptoms. Reporting a
        # symptom sends the caller to "grant Accessibility" for a problem that
        # more permissions cannot fix.
        script = _capture_reply_script()
        frontmost_branch = script.index('set abortCode to "GUARD_ABORT_FRONTMOST"')
        window_branch = script.index('set abortCode to "GUARD_ABORT_WINDOW"')
        self.assertLess(frontmost_branch, window_branch)


class FrontmostAbortMappingTests(unittest.TestCase):
    def test_guard_abort_frontmost_maps_to_its_own_code(self):
        payload = reply_runner._native_reply_abort_response(
            "GUARD_ABORT_FRONTMOST|detail=Mail could not be brought to the front; Finder held it",
            account="Work",
            reply_body="Reply body",
            timeout=None,
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertIn("REPLY_MAIL_NOT_FRONTMOST", payload)

    def test_frontmost_code_is_matched_before_the_generic_guard_abort(self):
        # Both sentinels start with "GUARD_ABORT", so a branch ordered after the
        # generic one would never be reached.
        payload = reply_runner._native_reply_abort_response(
            "GUARD_ABORT_FRONTMOST|detail=blocked",
            account="Work",
            reply_body="Reply body",
            timeout=None,
        )
        assert payload is not None
        self.assertNotIn("REPLY_WINDOW_FOCUS_FAILED", payload)

    def test_remediation_does_not_send_the_caller_after_permissions(self):
        # Live-measured 2026-08-24: with Finder deliberately frontmost the tool
        # reclaimed the front and the reply succeeded. The remaining failure is
        # a foreground-attention problem (locked screen, full-screen Space),
        # which no permission grant and no native_format change can fix.
        payload = reply_runner._native_reply_abort_response(
            "GUARD_ABORT_FRONTMOST|detail=blocked",
            account="Work",
            reply_body="Reply body",
            timeout=None,
        )
        assert payload is not None
        self.assertIn("do not switch off", payload.lower())
        self.assertIn("locked screen", payload.lower())


if __name__ == "__main__":
    unittest.main()
