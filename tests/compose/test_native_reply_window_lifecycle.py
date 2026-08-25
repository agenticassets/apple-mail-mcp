"""The native reply must not orphan the compose window it opened.

``newlyOpenedReplyWindowId`` returns ``""`` when Mail's new window cannot be
told apart from the ones already open. Two things used to follow from that, both
bad: the focus guard ran four attempts whose success test compares against that
empty id and so could never pass, and the abort's
``closeNativeReplyWindowSafely`` -- keyed on the same empty id -- returned
immediately without closing anything. The reply window stayed open, the error
blamed focus, and following its "retry with Mail visible" advice opened another
window each time. ``MAX_OPEN_COMPOSE_WINDOWS`` exists because NSWindowServer
OOMs once enough accumulate.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import compose as compose_tools


def _native_reply_script() -> str:
    """Return the generated native-reply compose script."""
    scripts: list[str] = []

    def fake_run(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        if "reply foundMessage" in script:
            return "GUARD_ABORT\nSubject: Re: Test\nDerivedSubject: Re: Test\nDetail: x"
        return "NOT_FOUND"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
        compose_tools.reply_to_email(account="Work", message_id="12345", reply_body="Reply body")

    return next(script for script in scripts if "reply foundMessage" in script)


class NativeReplyWindowLifecycleTests(unittest.TestCase):
    def test_guard_loop_exits_immediately_when_the_window_was_not_adopted(self) -> None:
        """The doomed attempts are skipped, and the reason is recorded, not invented."""
        script = _native_reply_script()

        loop_body = script.split("repeat with guardAttempt from 1 to 4", 1)[1]
        fast_exit = loop_body.index('if replyWindowId is "" then')
        first_attempt = loop_body.index('set guardMail to "(unset)"')
        self.assertLess(
            fast_exit,
            first_attempt,
            "the empty-id check must precede the first guard attempt, not follow it",
        )
        self.assertIn("window adoption found no unique new window", loop_body[fast_exit:first_attempt])
        self.assertIn("exit repeat", loop_body[fast_exit:first_attempt])

    def test_abort_falls_back_to_closing_the_outgoing_message_reference(self) -> None:
        """The one handle that cannot resolve to a window the user opened.

        A title match could close somebody else's compose; the outgoing message
        Mail returned from ``reply`` is unambiguously ours.
        """
        script = _native_reply_script()

        abort_block = script.split("if composeFocusVerified is false then", 1)[1]
        close_call = abort_block.index("my closeNativeReplyWindowSafely(")
        fallback = abort_block.index("close replyMessage saving no")
        self.assertLess(close_call, fallback, "the exact id-and-title close stays the first choice")
        # The fallback only runs when the precise close reported failure.
        self.assertIn("is false then", abort_block[close_call:fallback])
        # And it is guarded, so a Mail build that refuses the verb is no worse
        # than the orphaned-window behavior it replaces.
        self.assertIn("try", abort_block[close_call:fallback])

    def test_unidentified_window_abort_is_its_own_error_not_a_focus_failure(self) -> None:
        """Blaming focus sent callers to a retry that leaks another window."""

        def fake_run(script: str, timeout: int = 120) -> str:
            if "reply foundMessage" in script:
                return "\n".join(
                    [
                        "GUARD_ABORT_WINDOW",
                        "Subject: Re: Test",
                        "DerivedSubject: Re: Test",
                        "Detail: could not identify the reply window Mail just opened",
                    ]
                )
            return "NOT_FOUND"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        payload = json.loads(result)
        self.assertTrue(payload["error"])
        self.assertEqual(payload["code"], "REPLY_WINDOW_NOT_IDENTIFIED")
        self.assertIn("Close other Mail compose windows", payload["remediation"]["preferred"])
        self.assertIn("each retry opens another", payload["remediation"]["alternative"])
        # The sentinel shares the GUARD_ABORT prefix, so its branch has to be
        # reached before the generic focus-failure fallthrough.
        self.assertNotEqual(payload["code"], "REPLY_WINDOW_FOCUS_FAILED")

    def test_plain_guard_abort_still_reports_a_focus_failure(self) -> None:
        """The adopted-window focus failure is unchanged."""

        def fake_run(script: str, timeout: int = 120) -> str:
            if "reply foundMessage" in script:
                return "GUARD_ABORT\nSubject: Re: Test\nDerivedSubject: Re: Test\nDetail: could not focus"
            return "NOT_FOUND"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        self.assertEqual(json.loads(result)["code"], "REPLY_WINDOW_FOCUS_FAILED")


if __name__ == "__main__":
    unittest.main()
