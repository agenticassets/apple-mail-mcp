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
from apple_mail_mcp.tools.compose.constants import (
    AX_WINDOW_SETTLE_ATTEMPTS,
    AX_WINDOW_SETTLE_DELAY,
    REPLY_ACCESSIBILITY_UNAVAILABLE,
)
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

        preflight = script.index("my accessibilityWindowCountSettled(")
        refusal = script.index(f'return "{REPLY_ACCESSIBILITY_UNAVAILABLE}"')
        reply_command = script.index("reply foundMessage")

        self.assertLess(preflight, reply_command)
        self.assertLess(refusal, reply_command)

    def test_a_zero_has_to_hold_before_it_aborts(self) -> None:
        """One sample inside a Space transition is not evidence of a dead bridge.

        ``ensureMailFrontmost`` returns the moment macOS reports Mail frontmost,
        which happens while a Space switch is still animating. Measured on
        Darwin 25.5 (2026-08-25) with a full-screen app holding the front:
        frontmost was true from the first sample and Accessibility reported 0
        windows for ~0.3 s before reporting 1, with Mail's own dictionary
        reporting 8 the whole time. A single-sample guard aborted a healthy
        reply there and blamed the display.
        """
        script = _native_reply_script()

        self.assertIn("on accessibilityWindowCountSettled(maxAttempts, settleDelay)", script)
        self.assertIn(
            f"my accessibilityWindowCountSettled({AX_WINDOW_SETTLE_ATTEMPTS}, {AX_WINDOW_SETTLE_DELAY})",
            script,
        )
        self.assertGreater(AX_WINDOW_SETTLE_ATTEMPTS, 1)

    def test_settled_probe_returns_the_first_non_zero_count(self) -> None:
        """Polling must stop at the first answer, not run the full budget.

        The healthy path is the overwhelmingly common one and has to stay free:
        the first sample answers and the handler returns immediately, so the
        settle delay is only ever paid on a count that is genuinely zero --
        which was already a hard abort before this poll existed.
        """
        script = _native_reply_script()
        body = script[script.index("on accessibilityWindowCountSettled(") :]
        body = body[: body.index("end accessibilityWindowCountSettled")]

        self.assertIn('if lastCount is not "0" then return lastCount', body)
        self.assertLess(body.index("return lastCount"), body.index("delay settleDelay"))

    def test_detail_reports_mail_own_window_count_too(self) -> None:
        """The two counts together are the whole diagnosis.

        Accessibility reporting zero means nothing on its own. Beside Mail's own
        window count it separates "Mail has no window to reply from" (open a
        viewer) from "Accessibility cannot see the windows Mail has" (a Space, a
        sleeping display, or a lapsed grant) -- three different fixes that the
        Accessibility count alone cannot tell apart.
        """
        script = _native_reply_script()

        self.assertIn("set mailOwnWindowCount to", script)
        self.assertIn("Mail's own scripting dictionary reports", script)

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
        self.assertLess(script.index(guard_line), script.index("my accessibilityWindowCountSettled("))


class AccessibilitySentinelResponseTests(unittest.TestCase):
    def test_sentinel_maps_to_its_own_code(self) -> None:
        result = _reply_with_script_output(_SENTINEL_OUTPUT, output_format="json")

        payload = json.loads(result)
        self.assertTrue(payload["error"])
        self.assertEqual(payload["code"], REPLY_ACCESSIBILITY_UNAVAILABLE)
        self.assertEqual(payload["remediation"]["script_output"], _SENTINEL_OUTPUT)

    def test_remediation_leads_with_the_space_case(self) -> None:
        """The wrong remediation is worse than none: it burns retries on a no-op.

        Three causes produce an Accessibility count of zero, and they are
        indistinguishable from inside the script, so all three are named. The
        order is evidence-driven: measured on Darwin 25.5 (2026-08-25), the
        common one on a working Mac is Mail sitting on a different Space --
        which any full-screen app creates -- while the display is awake and the
        grant is fine. Leading with the display or with System Settings sends a
        user to fix something that was never broken, and the fix that does work
        (leave full-screen) is one no earlier wording mentioned.

        Ordering only. The likelihood ranking is pinned here; the diagnosis is
        pinned by ``test_preferred_does_not_assert_a_cause_it_cannot_prove``,
        which is a separate property -- leading with the most likely cause is
        right, and stating it as fact is not.
        """
        payload = json.loads(_reply_with_script_output(_SENTINEL_OUTPUT, output_format="json"))
        preferred = str(payload["remediation"]["preferred"]).lower()
        remediation = " ".join(str(value) for value in payload["remediation"].values()).lower()

        self.assertIn("space", preferred)
        self.assertIn("display", remediation)
        self.assertIn("accessibility", remediation)
        self.assertNotIn("retry with mail visible", remediation)

    def test_preferred_does_not_assert_a_cause_it_cannot_prove(self) -> None:
        """The reading the text points at cannot separate the three causes.

        ``reply_window_scripts.py`` records the measurement: with the display
        asleep, System Events reported 0 windows for *every* foreground
        application while Mail's own scripting layer reported 13, raising no
        error -- and a lapsed Accessibility grant produces the identical
        reading. Mail's ``count of windows`` is unaffected by display state, so
        the Detail line the remediation tells the user to read cannot tell a
        Space, a locked screen, and a dead grant apart.

        The preferred text therefore has to present the Space as the most
        likely explanation and name the other two, not declare "this is not a
        permissions problem". That wording was a confident wrong diagnosis for
        every locked-screen caller.
        """
        payload = json.loads(_reply_with_script_output(_SENTINEL_OUTPUT, output_format="json"))
        preferred = str(payload["remediation"]["preferred"]).lower()

        self.assertNotIn("not a permissions problem", preferred)
        # Hedged, not asserted.
        self.assertTrue(
            "most likely" in preferred or "likely" in preferred,
            "the Space case must be offered as the likeliest reading, not stated as the cause",
        )
        # All three indistinguishable readings named in the text that leads.
        self.assertIn("space", preferred)
        self.assertIn("display", preferred)
        self.assertIn("accessibility", preferred)
        self.assertTrue(
            "tell them apart" in preferred or "cannot tell" in preferred or "indistinguishable" in preferred,
            "the preferred remediation must say the reading cannot separate the three causes",
        )

    def test_remediation_does_not_prescribe_restarting_mail(self) -> None:
        """Restarting Mail is the folklore remedy this diagnosis replaces.

        A Space-stranded Mail is cured by leaving full-screen; quitting Mail
        only appears to work because a relaunched Mail opens onto the Space the
        user is currently on. Recommending the restart would keep the expensive
        ritual alive for a condition that does not need it.
        """
        payload = json.loads(_reply_with_script_output(_SENTINEL_OUTPUT, output_format="json"))
        remediation = " ".join(str(value) for value in payload["remediation"].values()).lower()

        self.assertNotIn("restart mail", remediation)
        self.assertNotIn("quit mail", remediation)

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
