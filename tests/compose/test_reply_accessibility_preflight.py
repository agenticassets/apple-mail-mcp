"""The native reply refuses early when System Events cannot see Mail's windows.

Measured on Darwin 25.5 (2026-08-24) with the display asleep: System Events
reported ``frontmost`` true for Mail and **0 windows for every foreground
application on the machine**, raising no error, while Mail's own scripting layer
reported 13 windows at the same moment. Waking the display restored the count
with no permission change, which is what identified the cause -- a
non-effective Accessibility grant produces the identical reading, and nothing
inside the script can tell the two apart. ``name of front window`` throws for
"no windows" and for "cannot see windows" alike, and the guard's ``try`` turns
both into an empty title it accepts as a legitimate answer from a compose
window.

Without the preflight the tool opened a reply window, spent four doomed focus
attempts on it (~82 s measured), left the window behind, and reported
``REPLY_WINDOW_FOCUS_FAILED`` -- whose remediation tells the user to retry with
Mail visible, which cannot fix a permission that is not in effect.

Two properties carry the fix and both are pinned here: the check runs *before*
the ``reply`` command so no window is created, and only a successful count of
exactly ``0`` blocks.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import compose as compose_tools
from apple_mail_mcp.tools.compose.constants import REPLY_ACCESSIBILITY_UNAVAILABLE
from apple_mail_mcp.tools.compose.reply_window_scripts import (
    native_reply_window_handlers_applescript,
)

_SENTINEL_OUTPUT = f"{REPLY_ACCESSIBILITY_UNAVAILABLE}\nDetail: System Events reports 0 windows for Mail"


def _native_reply_script(**kwargs: object) -> str:
    """Return the compose script the native reply path builds for a body."""
    scripts: list[str] = []

    def fake_run(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "NOT_FOUND"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
        compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            **kwargs,  # type: ignore[arg-type]
        )

    matches = [script for script in scripts if "reply foundMessage" in script]
    assert len(matches) == 1
    return matches[0]


def _reply_with_script_output(output: str, **kwargs: object) -> str:
    def fake_run(script: str, timeout: int = 120) -> str:
        if "reply foundMessage" in script:
            return output
        return "NOT_FOUND"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            **kwargs,  # type: ignore[arg-type]
        )
    assert isinstance(result, str)
    return result


class AccessibilityPreflightScriptTests(unittest.TestCase):
    def test_preflight_runs_before_the_reply_command(self) -> None:
        """Order is the entire value: a later check has already leaked a window.

        Mail's ``reply`` command creates the compose window. Discovering the
        bridge is dead after that point means the tool has to clean up a window
        it can no longer see, which is exactly the state that used to leave
        stray "New Message" windows behind.
        """
        script = _native_reply_script()

        preflight = script.index("my accessibilityWindowCount()")
        refusal = script.index(f'return "{REPLY_ACCESSIBILITY_UNAVAILABLE}"')
        reply_command = script.index("reply foundMessage")

        self.assertLess(preflight, reply_command)
        self.assertLess(refusal, reply_command)

    def test_only_an_exact_zero_blocks(self) -> None:
        """A probe that could not answer must not be read as a failed probe.

        ``accessibilityWindowCount`` returns ``unknown:<error>`` when System
        Events threw. Treating that as zero would refuse replies on a healthy
        machine whenever the probe itself hit a transient error, so the guard --
        which still runs -- is left to make that call.
        """
        script = _native_reply_script()

        self.assertIn('if axWindowCount is "0" then', script)
        self.assertNotIn('if axWindowCount is not "unknown"', script)

    def test_probe_carries_the_error_text_when_it_cannot_answer(self) -> None:
        handlers = native_reply_window_handlers_applescript()

        self.assertIn('return "unknown:" & axCountErrMsg', handlers)

    def test_no_preflight_when_there_is_no_body_to_type(self) -> None:
        """An empty body needs no keystrokes, so it needs no Accessibility.

        Refusing it would break the one native-reply shape that works without
        the grant at all.
        """
        script = _native_reply_script()

        guard_line = 'if replyBodyText is not "" then'
        self.assertIn(guard_line, script)
        self.assertLess(script.index(guard_line), script.index("my accessibilityWindowCount()"))


class AccessibilitySentinelResponseTests(unittest.TestCase):
    def test_sentinel_maps_to_its_own_code(self) -> None:
        result = _reply_with_script_output(_SENTINEL_OUTPUT, output_format="json")

        payload = json.loads(result)
        self.assertTrue(payload["error"])
        self.assertEqual(payload["code"], REPLY_ACCESSIBILITY_UNAVAILABLE)
        self.assertEqual(payload["remediation"]["script_output"], _SENTINEL_OUTPUT)

    def test_remediation_names_both_causes_display_first(self) -> None:
        """The wrong remediation is worse than none: it burns retries on a no-op.

        ``REPLY_WINDOW_FOCUS_FAILED`` says to retry with Mail visible, which
        cannot succeed on a display that is not on. The two real causes are a
        sleeping or locked screen and a non-effective Accessibility grant, and
        they are indistinguishable from inside the script -- so both are named,
        with the recoverable one first. Sending a user to System Settings for a
        screen that simply went to sleep is its own kind of wrong.
        """
        payload = json.loads(_reply_with_script_output(_SENTINEL_OUTPUT, output_format="json"))
        preferred = str(payload["remediation"]["preferred"]).lower()
        remediation = " ".join(str(value) for value in payload["remediation"].values()).lower()

        self.assertIn("display", preferred)
        self.assertIn("accessibility", remediation)
        self.assertNotIn("retry with mail visible", remediation)

    def test_sentinel_is_enveloped_for_text_callers_too(self) -> None:
        """Unlike the JSON-only prose exits, this is a pre-open abort.

        Abort sentinels are dispatched before ``output_format`` is consulted, so
        a text caller gets the structured error rather than a bare token it
        would have to recognize on its own.
        """
        payload = json.loads(_reply_with_script_output(_SENTINEL_OUTPUT))

        self.assertEqual(payload["code"], REPLY_ACCESSIBILITY_UNAVAILABLE)

    def test_no_stray_artifact_is_claimed(self) -> None:
        """Nothing was opened, so there is nothing for the caller to inspect.

        The other abort branches probe Drafts for a stray artifact and report a
        ``suspected_draft_id``. Doing that here would point at a draft this call
        provably did not create.
        """
        payload = json.loads(_reply_with_script_output(_SENTINEL_OUTPUT, output_format="json"))

        self.assertNotIn("suspected_draft_id", payload["remediation"])
        self.assertIn("nothing was saved", payload["message"].lower())


if __name__ == "__main__":
    unittest.main()
