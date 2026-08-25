"""A malformed ``~user`` path must be answered, not raised.

``Path("~typo/report.pdf").expanduser()`` raises ``RuntimeError`` when no such
user exists. ``validate_save_path`` has always guarded that -- it expands inside
its own try and returns an error string, because every one of its callers is a
tool boundary that has to answer rather than throw. But two call sites expanded
the path themselves *before* handing it to that guard, so the throw happened
first and escaped the tool as a transport exception, past the boundary
conversion in ``server.py`` (which only re-shapes ``ToolError``). An agent got a
crash where every other bad path returns a readable string.

Both sites now validate first and expand second.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import compose as compose_tools
from apple_mail_mcp.tools import manage as manage_tools
from apple_mail_mcp.tools.compose.payload import _validate_attachment_paths

_BAD_HOME = "~nosuchuser1234/report.pdf"


class MalformedHomePathTests(unittest.TestCase):
    def test_attachment_validation_returns_an_error_instead_of_raising(self) -> None:
        paths, error = _validate_attachment_paths(_BAD_HOME)
        self.assertEqual(paths, [])
        assert error is not None
        self.assertTrue(error.startswith("Error:"), error)

    def test_compose_email_reports_the_bad_attachment_path(self) -> None:
        def fail_if_called(script: str, timeout: int = 120) -> str:
            raise AssertionError("no AppleScript should run for an unusable attachment path")

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fail_if_called):
            result = compose_tools.compose_email(
                account="Work",
                to="recipient@example.com",
                subject="Subject",
                body="Body",
                attachments=_BAD_HOME,
            )

        self.assertIsInstance(result, str)
        self.assertIn("Error:", result)

    def test_save_email_attachment_reports_the_bad_save_path(self) -> None:
        def fail_if_called(script: str, timeout: int = 120) -> str:
            raise AssertionError("no AppleScript should run for an unusable save path")

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fail_if_called):
            result = manage_tools.save_email_attachment(
                account="Work",
                message_ids=["12345"],
                attachment_index=1,
                save_path=_BAD_HOME,
            )

        self.assertIsInstance(result, str)
        self.assertIn("Error:", result)

    def test_a_normal_tilde_path_still_expands(self) -> None:
        """The guard must not have broken ordinary ``~/...`` handling."""
        paths, error = _validate_attachment_paths("~/definitely-not-a-real-file-9f3a.pdf")
        self.assertEqual(paths, [])
        assert error is not None
        # Reaching the existence check means expansion happened and passed the
        # home-containment guard, rather than being rejected as malformed.
        self.assertIn("does not exist", error)


if __name__ == "__main__":
    unittest.main()
