"""Tests for compose and rich draft helpers."""

import inspect
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import MagicMock, patch

from apple_mail_mcp import server as _server
from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.tools import compose as compose_tools
from apple_mail_mcp.tools.compose import constants as compose_constants
from apple_mail_mcp.tools.compose import reply_runner
from apple_mail_mcp.tools.compose.reply_identity import NativeReplyDraftIdentity


@contextmanager
def _home_rooted_tmpdir():
    """Yield a scratch directory that ``validate_save_path`` treats as inside home.

    ``create_rich_email_draft(output_path=...)`` is home-restricted (AGENTIC-2361),
    and tests must never write into the operator's real home directory.
    Repointing ``HOME`` at a scratch directory keeps every byte in the temp
    filesystem while still exercising the accepted branch of the guard.
    """
    with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"HOME": tmpdir}):
        yield tmpdir


def _make_subprocess_result(returncode=0, stdout=b"", stderr=b""):
    """Build a MagicMock shaped like subprocess.CompletedProcess."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def _assert_ordered(testcase, text, *snippets):
    """Assert snippets appear in text in the provided order."""
    last_position = -1
    for snippet in snippets:
        position = text.find(snippet)
        testcase.assertGreater(position, last_position)
        last_position = position


def _assert_full_body_verifier_shape(testcase, verifier_script):
    """Assert the saved-reply verifier reads the full body from a temp file.

    AGENTIC-1214: the verifier no longer builds a first-line ``replyBodyNeedle``
    needle; it reads the whole intended body from a second temp file and
    compares it case-sensitively above the quote, whitespace-flattened and
    smart-punctuation-folded.
    """
    testcase.assertIn('set fullReplyBody to do shell script "cat "', verifier_script)
    testcase.assertIn("on flattenForCompare(theText)", verifier_script)
    testcase.assertIn(
        "on replyBodyAboveQuoteStatus(draftContent, fullReplyBody, quotedNeedle, quoteAnchor)", verifier_script
    )
    testcase.assertIn("considering case", verifier_script)
    testcase.assertNotIn("set replyBodyNeedle to", verifier_script)


def _main_reply_script(scripts):
    """Return the generated reply script, skipping helper probes."""
    reply_scripts = [script for script in scripts if "reply foundMessage" in script]
    if len(reply_scripts) != 1:
        raise AssertionError(f"expected one reply script, got {len(reply_scripts)}")
    return reply_scripts[0]


def _assert_native_saved_draft_id_contract(testcase, script, *, quiet_close: bool):
    """Assert native output resolves an exact Drafts identity before quiet close."""
    capture = script.index("set replyDraftIdentity to my persistedReplyDraftIdentity")
    output = script.index('set outputText to outputText & "Draft ID: " & replyDraftId', capture)
    save = script.rfind("save replyMessage", 0, capture)
    testcase.assertGreater(save, -1)
    testcase.assertLess(save, capture)
    if quiet_close:
        close = script.index(
            "my closeNativeReplyWindowSafely(replyWindowId, replySubject, derivedReplySubject)", capture
        )
        testcase.assertLess(close, output)
    else:
        testcase.assertNotIn(
            "my closeNativeReplyWindowSafely(replyWindowId, replySubject, derivedReplySubject)",
            script[capture:output],
        )


def _save_draft_script(scripts):
    """Return the save-as-draft script, skipping the sender/snapshot probes."""
    save_scripts = [script for script in scripts if "save targetMessage" in script]
    if len(save_scripts) != 1:
        raise AssertionError(f"expected one save-draft script, got {len(save_scripts)}")
    return save_scripts[0]


def _saved_reply_draft_output(
    *,
    to="Sender <sender@example.com>",
    subject="Re: Test",
    draft_id=None,
    draft_identity=None,
    quote_needle=None,
):
    if draft_id is not None and draft_identity is None:
        # Four fields, evidence last — the exact shape reply_scripts.py emits.
        # A three-field fixture passed here for months against a parser that
        # rejected every real capsule; see test_native_reply_identity_capsule.py.
        draft_identity = f"{draft_id}|||<draft-{draft_id}@example.com>|||<source@example.com>|||rfc"
    lines = [
        "SAVING REPLY AS DRAFT",
        "",
        "Reply saved as draft!",
        f"To: {to}",
        f"Subject: {subject}",
    ]
    if draft_id is not None:
        lines.append(f"Draft ID: {draft_id}")
    if draft_identity is not None:
        lines.append(f"Draft Identity: {draft_identity}")
    if quote_needle is not None:
        lines.append(f"Quote Needle: {quote_needle}")
    return "\n".join(lines) + "\n"


def _saved_forward_draft_output(*, to="recipient@example.com", subject="Fwd: Test", draft_id=None):
    lines = [
        "SAVING FORWARD AS DRAFT",
        "",
        "Forward saved as draft.",
        f"To: {to}",
        f"Subject: {subject}",
    ]
    if draft_id is not None:
        lines.append(f"Draft ID: {draft_id}")
    return "\n".join(lines) + "\n"


class DefaultMailSignatureSupportTests(unittest.TestCase):
    def test_server_exposes_default_mail_signature_env_setting(self):
        self.assertTrue(hasattr(_server, "DEFAULT_MAIL_SIGNATURE"))

    def test_compose_email_signature_parameters_are_in_tool_signature(self):
        params = inspect.signature(compose_tools.compose_email).parameters

        self.assertIn("include_signature", params)
        self.assertTrue(params["include_signature"].default)
        self.assertIn("signature_name", params)
        self.assertIsNone(params["signature_name"].default)

    def test_default_signature_applies_to_plain_draft_via_mail_signature_property(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "saved"

        with (
            patch.object(compose_tools.server, "DEFAULT_MAIL_SIGNATURE", "TU", create=True),
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        ):
            compose_tools.compose_email(
                account="Work",
                to="self@example.com",
                subject="Test",
                body="Body",
                mode="draft",
            )

        script = captured[0]
        _assert_ordered(
            self,
            script,
            'set message signature of newMessage to signature "TU"',
            'set content of newMessage to "Body" & return & return & signatureContent',
            "save newMessage",
        )
        self.assertIn('content:"", visible:false', script)

    def test_plain_draft_waits_boundedly_for_native_signature_before_prepending_body(self):
        """Mail may materialize an account signature after the message is created."""
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "saved"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            compose_tools.compose_email(
                account="iCloud",
                to="self@example.com",
                subject="Signature timing",
                body="Authored body",
                mode="draft",
            )

        script = captured[0]
        _assert_ordered(
            self,
            script,
            "set newMessage to make new outgoing message",
            "repeat with signatureAttempt from 1 to 5",
            "set signatureContent to content of newMessage as string",
            'set content of newMessage to "Authored body" & return & return & signatureContent',
            "save newMessage",
        )
        self.assertIn("if signatureAttempt is less than 5 then delay 0.2", script)

    def test_include_signature_false_suppresses_default_signature_assignment(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "saved"

        with (
            patch.object(compose_tools.server, "DEFAULT_MAIL_SIGNATURE", "TU", create=True),
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        ):
            compose_tools.compose_email(
                account="Work",
                to="self@example.com",
                subject="Test",
                body="Body",
                include_signature=False,
            )

        self.assertNotIn("message signature of newMessage", captured[0])

    def test_html_compose_applies_signature_without_selecting_all(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "saved"

        with (
            patch.object(compose_tools.server, "DEFAULT_MAIL_SIGNATURE", "TU", create=True),
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        ):
            compose_tools.compose_email(
                account="Work",
                to="team@example.com",
                subject="Hi",
                body="Plain",
                body_html="<p>Hi</p>",
                mode="draft",
            )

        script = captured[0]
        self.assertIn('set message signature of newMsg to signature "TU"', script)
        self.assertNotIn('keystroke "a" using command down', script)

    def test_reply_and_forward_accept_signature_options(self):
        for tool, message_var, kwargs in [
            (
                compose_tools.reply_to_email,
                "replyMessage",
                {"message_id": "12345", "reply_body": "Thanks"},
            ),
            (
                compose_tools.forward_email,
                "forwardMessage",
                {"message_id": "12345", "to": "recipient@example.com"},
            ),
        ]:
            with self.subTest(tool=tool.__name__):
                captured = []

                def fake_run(script, timeout=120, captured=captured):
                    captured.append(script)
                    if "count of outgoing messages" in script:
                        return "0"
                    if "availableSignatures" in script:
                        return ""
                    return "saved"

                with patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    side_effect=fake_run,
                ):
                    tool(
                        account="Work",
                        include_signature=True,
                        signature_name="TU",
                        **kwargs,
                    )

                self.assertTrue(
                    any(f'set message signature of {message_var} to signature "TU"' in script for script in captured),
                    captured,
                )

    def test_default_signature_applies_to_reply_via_mail_signature_property(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "availableSignatures" in script:
                return ""
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|not_requested|detected"
            return "ok"

        with (
            patch.object(compose_tools.server, "DEFAULT_MAIL_SIGNATURE", "TU", create=True),
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                native_format=False,
                allow_windowless_fallback=True,
            )

        script = _main_reply_script(captured)
        _assert_ordered(
            self,
            script,
            'set message signature of replyMessage to signature "TU"',
            "set composedReplyContent to replyBodyText & return & return & quotedOriginalText",
            "save replyMessage",
        )
        verifier_script = next(script for script in captured if "set signatureWasRequested" in script)
        self.assertIn("set signatureWasRequested to true", verifier_script)
        self.assertIn("Signature Verification Status: detected", result)


class ComposeToolTests(unittest.TestCase):
    def test_create_rich_email_draft_delegates_mail_open_to_html_compose_transaction(self):
        """Rich EML export and supported Mail drafting are separate transactions."""
        with _home_rooted_tmpdir() as tmpdir:
            attachment = Path(tmpdir) / "board.pdf"
            attachment.write_bytes(b"attachment contents")
            output_path = Path(tmpdir) / "delegated-rich-draft.eml"

            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    return_value="sender@example.com",
                ),
                patch(
                    "apple_mail_mcp.tools.compose.compose_email",
                    return_value="Email saved as draft (HTML)\nDraft ID: 84053\nAttachment verification: verified",
                ) as mock_compose,
                patch("apple_mail_mcp.tools.compose.subprocess.run") as mock_open,
            ):
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="Board materials",
                    to="team@example.com",
                    text_body="Please review the attached materials.",
                    html_body="<p>Please review the attached materials.</p>",
                    attachments=str(attachment),
                    output_path=str(output_path),
                    open_in_mail=True,
                )

            self.assertTrue(output_path.is_file())
            mock_compose.assert_called_once_with(
                account="Work",
                to="team@example.com",
                subject="Board materials",
                body="Please review the attached materials.",
                cc=None,
                bcc=None,
                attachments=str(attachment),
                mode="draft",
                body_html="<p>Please review the attached materials.</p>",
                from_address=None,
                timeout=None,
                standalone_confirmed=True,
            )
            mock_open.assert_not_called()
            self.assertIn("EML path: " + str(output_path), result)
            self.assertIn("Mail compose: delegated to compose_email", result)
            self.assertIn("Attachment verification: verified", result)

    def test_create_rich_email_draft_fails_closed_when_html_editor_cannot_focus(self):
        """A failed focused HTML editor must not be reported as a ready rich draft."""
        with _home_rooted_tmpdir() as tmpdir:
            output_path = Path(tmpdir) / "focus-failure-rich-draft.eml"

            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    return_value="sender@example.com",
                ),
                patch(
                    "apple_mail_mcp.tools.compose.compose_email",
                    return_value="Error: HTML email send failed: COMPOSE_BODY_FOCUS_FAILED",
                ),
            ):
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="Board materials",
                    to="team@example.com",
                    text_body="Please review.",
                    html_body="<p>Please review.</p>",
                    output_path=str(output_path),
                    open_in_mail=True,
                )

            error = json.loads(result)
            self.assertEqual(error["code"], "RICH_DRAFT_COMPOSE_FAILED")
            self.assertEqual(error["remediation"]["eml_path"], str(output_path))
            self.assertIn("COMPOSE_BODY_FOCUS_FAILED", error["remediation"]["compose_result"])

    def test_create_rich_email_draft_embeds_validated_attachment_in_eml(self):
        """Attachment-bearing EML-only drafts are prepared, never Mail-verified ready."""
        with _home_rooted_tmpdir() as tmpdir:
            attachment = Path(tmpdir) / "board.pdf"
            attachment.write_bytes(b"attachment contents")
            output_path = Path(tmpdir) / "rich-with-attachment.eml"

            with patch(
                "apple_mail_mcp.tools.compose.run_applescript",
                return_value="sender@example.com",
            ):
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="Board materials",
                    to="team@example.com",
                    text_body="Please review the attached materials.",
                    attachments=str(attachment),
                    output_path=str(output_path),
                    open_in_mail=False,
                )

            message = BytesParser(policy=policy.default).parsebytes(output_path.read_bytes())
            attachment_parts = list(message.iter_attachments())
            self.assertEqual(len(attachment_parts), 1)
            self.assertEqual(attachment_parts[0].get_filename(), "board.pdf")
            self.assertEqual(attachment_parts[0].get_payload(decode=True), b"attachment contents")
            self.assertIn("Mail verification: not performed (EML only)", result)
            self.assertNotIn("ready", result.lower())

    def test_create_rich_email_draft_blocks_reply_like_subject_without_confirmation(self):
        with _home_rooted_tmpdir() as tmpdir:
            output_path = Path(tmpdir) / "blocked.eml"

            with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="Re: Complex Request",
                    to="sender@example.com",
                    text_body="Thread-like draft",
                    output_path=str(output_path),
                    open_in_mail=False,
                )

        mock_run.assert_not_called()
        self.assertIn("standalone new message", result)
        self.assertFalse(output_path.exists())

    def test_create_rich_email_draft_allows_reply_like_subject_when_confirmed(self):
        with _home_rooted_tmpdir() as tmpdir:
            output_path = Path(tmpdir) / "confirmed.eml"

            with patch(
                "apple_mail_mcp.tools.compose.run_applescript",
                return_value="sender@example.com",
            ):
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="Re: standalone project name",
                    to="team@example.com",
                    text_body="This is a new standalone draft.",
                    output_path=str(output_path),
                    open_in_mail=False,
                    standalone_confirmed=True,
                )

            self.assertTrue(output_path.exists())
            self.assertIn("Rich draft prepared successfully", result)

    def test_create_rich_email_draft_writes_multipart_eml(self):
        with _home_rooted_tmpdir() as tmpdir:
            output_path = Path(tmpdir) / "weekly-update.eml"

            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    return_value="sender@example.com",
                ),
                patch(
                    "apple_mail_mcp.tools.compose.compose_email",
                    return_value="Email saved as draft (HTML)\nDraft ID: 84053",
                ) as mock_compose,
            ):
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="Weekly Update",
                    to="team@example.com",
                    text_body="Plain fallback",
                    html_body="<html><body><h1>Weekly Update</h1></body></html>",
                    output_path=str(output_path),
                    open_in_mail=True,
                )

            payload = output_path.read_text()
            self.assertIn("multipart/alternative", payload)
            self.assertIn("<h1>Weekly Update</h1>", payload)
            self.assertIn("Subject: Weekly Update", payload)
            self.assertIn("Email saved as draft (HTML)", result)
            mock_compose.assert_called_once()

    def test_create_rich_email_draft_allows_partial_details(self):
        with _home_rooted_tmpdir() as tmpdir:
            output_path = Path(tmpdir) / "partial.eml"

            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    return_value="sender@example.com",
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run"),
            ):
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    output_path=str(output_path),
                    open_in_mail=False,
                )

            payload = output_path.read_text()
            self.assertIn("Draft outline", payload)
            self.assertIn("Missing details: subject, to, body", result)
            self.assertIn("Opened in Mail: no", result)

    def test_create_rich_email_draft_empty_subject_does_not_open_mail_by_default(self):
        with _home_rooted_tmpdir() as tmpdir:
            output_path = Path(tmpdir) / "empty-subject.eml"
            scripts = []

            def fake_run_applescript(script, timeout=120):
                scripts.append(script)
                return "sender@example.com"

            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    side_effect=fake_run_applescript,
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run") as mock_run,
            ):
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="",
                    output_path=str(output_path),
                )

            payload = output_path.read_text()
            self.assertIn("Draft outline", payload)
            self.assertIn("Missing details: subject, to, body", result)
            self.assertIn("Opened in Mail: no", result)
            mock_run.assert_not_called()
            self.assertEqual(len(scripts), 1)
            self.assertNotIn("every outgoing message whose subject is", scripts[0])


class SaveNewComposeWindowAsDraftTests(unittest.TestCase):
    def test_saves_new_compose_window_without_subject_lookup(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "saved"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools._save_new_compose_window_as_draft(close_after_save=True)

        self.assertTrue(result)
        self.assertEqual(len(captured), 1)
        self.assertNotIn("every outgoing message whose subject is", captured[0])
        # Save through Mail's outgoing-message object model via id-diff, not a
        # blind item-1 grab and not System Events.
        self.assertNotIn("item 1 of outgoing messages", captured[0])
        _assert_ordered(
            self,
            captured[0],
            "set priorIds to {",
            "repeat with candidateMessage in outgoing messages",
            "if priorIds does not contain candidateId then",
            "save targetMessage",
            "close (window of targetMessage) saving no",
        )
        self.assertNotIn("System Events", captured[0])
        self.assertNotIn('keystroke "s" using command down', captured[0])
        self.assertNotIn("close window 1 saving no", captured[0])
        self.assertNotIn("close window 1 saving yes", captured[0])

    def test_can_leave_new_compose_window_open_for_review(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "saved"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools._save_new_compose_window_as_draft(close_after_save=False)

        self.assertTrue(result)
        self.assertIn("save targetMessage", captured[0])
        self.assertNotIn("System Events", captured[0])
        self.assertNotIn('keystroke "s" using command down', captured[0])
        self.assertNotIn("close (window of targetMessage) saving no", captured[0])
        self.assertNotIn("close window 1 saving yes", captured[0])

    def test_prior_outgoing_ids_are_excluded_from_save_target(self):
        """A pre-existing compose window (id in the snapshot) is never saved."""
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "saved"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools._save_new_compose_window_as_draft(
                prior_outgoing_ids={"41", "57"},
                close_after_save=False,
            )

        self.assertTrue(result)
        # Both pre-open ids land in the priorIds literal so the diff skips them.
        self.assertIn('"41"', captured[0])
        self.assertIn('"57"', captured[0])
        self.assertIn("if priorIds does not contain candidateId then", captured[0])

    def test_no_new_outgoing_window_returns_false(self):
        """When only pre-existing windows exist, the save reports failure."""

        def fake_run(script, timeout=120):
            return "not-found"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools._save_new_compose_window_as_draft(
                prior_outgoing_ids={"7"},
                retries=2,
                delay_seconds=0,
            )

        self.assertFalse(result)


class StripCdataTests(unittest.TestCase):
    def test_none_passes_through(self):
        self.assertIsNone(compose_tools._strip_cdata_wrappers(None))

    def test_empty_passes_through(self):
        self.assertEqual("", compose_tools._strip_cdata_wrappers(""))

    def test_unwraps_symmetric_block(self):
        self.assertEqual(
            "<p>Hello</p>",
            compose_tools._strip_cdata_wrappers("<![CDATA[<p>Hello</p>]]>"),
        )

    def test_unwraps_multiline_block(self):
        self.assertEqual(
            "\n<p>Hi</p>\n",
            compose_tools._strip_cdata_wrappers("<![CDATA[\n<p>Hi</p>\n]]>"),
        )

    def test_strips_stray_closing_marker(self):
        # This is the symptom users actually see — HTML parsers hide the
        # opening `<![CDATA[`, but the trailing `]]>` renders as text.
        self.assertEqual(
            "<p>Hello</p>",
            compose_tools._strip_cdata_wrappers("<p>Hello</p>]]>"),
        )

    def test_strips_stray_opening_marker(self):
        self.assertEqual(
            "<p>Hello</p>",
            compose_tools._strip_cdata_wrappers("<![CDATA[<p>Hello</p>"),
        )

    def test_leaves_normal_html_untouched(self):
        html = "<html><body><h1>Weekly Update</h1></body></html>"
        self.assertEqual(html, compose_tools._strip_cdata_wrappers(html))


class CreateRichEmailDraftCdataTests(unittest.TestCase):
    def test_cdata_wrapped_html_body_is_stripped_in_eml(self):
        with _home_rooted_tmpdir() as tmpdir:
            output_path = Path(tmpdir) / "cdata.eml"

            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    return_value="sender@example.com",
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run"),
            ):
                compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="CDATA Test",
                    to="team@example.com",
                    text_body="Plain fallback",
                    html_body="<![CDATA[<html><body><h1>Hi</h1></body></html>]]>",
                    output_path=str(output_path),
                    open_in_mail=False,
                )

            payload = output_path.read_text()
            self.assertIn("<h1>Hi</h1>", payload)
            self.assertNotIn("<![CDATA[", payload)
            self.assertNotIn("]]>", payload)


class ValidateFromAddressTests(unittest.TestCase):
    def test_none_skips_lookup(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            override, error = compose_tools._validate_from_address("Work", None)
        self.assertIsNone(override)
        self.assertIsNone(error)
        mock_run.assert_not_called()

    def test_blank_skips_lookup(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            override, error = compose_tools._validate_from_address("Work", "   ")
        self.assertIsNone(override)
        self.assertIsNone(error)
        mock_run.assert_not_called()

    def test_matches_case_insensitively_and_trims(self):
        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            return_value="Default@Example.com\nSecondary@Example.org",
        ):
            override, error = compose_tools._validate_from_address("Work", "  SECONDARY@example.ORG ")
        self.assertEqual(override, "Secondary@Example.org")
        self.assertIsNone(error)

    def test_unknown_alias_returns_error(self):
        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            return_value="default@example.com",
        ):
            override, error = compose_tools._validate_from_address("Work", "other@example.com")
        self.assertIsNone(override)
        self.assertIn("is not configured on account", error)
        self.assertIn("default@example.com", error)

    def test_missing_aliases_returns_error(self):
        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            return_value="",
        ):
            override, error = compose_tools._validate_from_address("Work", "anything@example.com")
        self.assertIsNone(override)
        self.assertIn("Could not read email addresses", error)


class ComposeEmailSenderOverrideTests(unittest.TestCase):
    def test_compose_blocks_reply_like_subject_without_standalone_confirmation(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.compose_email(
                account="Work",
                to="norman@example.com",
                subject="Re: Forwarded notes",
                body="Thanks, I will take a look.",
            )

        mock_run.assert_not_called()
        self.assertIn("compose_email creates a standalone new message", result)
        self.assertIn("Use reply_to_email(message_id=...)", result)

    def test_compose_allows_reply_like_subject_when_standalone_is_confirmed(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Email saved as draft!"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.compose_email(
                account="Work",
                to="norman@example.com",
                subject="Re: standalone project name",
                body="This is not a reply to an existing email.",
                standalone_confirmed=True,
            )

        self.assertEqual(len(captured), 1)
        self.assertIn("SAVING EMAIL AS DRAFT", captured[0])
        self.assertIn("saved as draft", result)

    def test_compose_defaults_to_draft_mode(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Email saved as draft!"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.compose_email(
                account="Work",
                to="self@example.com",
                subject="Test",
                body="Body",
            )

        self.assertIn("SAVING EMAIL AS DRAFT", captured[0])
        self.assertIn("save newMessage", captured[0])
        self.assertNotIn("close window 1 saving yes", captured[0])
        self.assertNotIn("send newMessage", captured[0])

    def test_compose_open_mode_saves_before_leaving_open_for_review(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            # First call is the window-count probe (Fix #12); return 0 open windows.
            if "count of outgoing messages" in script:
                return "0"
            return "✓ Email opened in Mail for review."

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.compose_email(
                account="Work",
                to="self@example.com",
                subject="Test",
                body="Body",
                mode="open",
            )

        # captured[0] is the window-count probe; find the main compose script.
        main_scripts = [s for s in captured if "OPENING EMAIL FOR REVIEW" in s]
        self.assertEqual(len(main_scripts), 1)
        self.assertIn("save newMessage", main_scripts[0])
        self.assertIn("activate", main_scripts[0])
        self.assertIn("review", result)

    def test_draft_safe_blocks_explicit_send(self):
        with patch.object(compose_tools.server, "DRAFT_SAFE", True):
            result = compose_tools.compose_email(
                account="Work",
                to="self@example.com",
                subject="Test",
                body="Body",
                mode="send",
            )

        self.assertIn("draft-safe mode", result)

    def test_default_emits_single_alias_fallback_block(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Email sent successfully!"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.compose_email(
                account="Work",
                to="self@example.com",
                subject="Test",
                body="Body",
                mode="draft",
            )

        self.assertEqual(len(captured), 1)
        script = captured[0]
        self.assertIn("email addresses of targetAccount", script)
        self.assertIn("if (count of emailAddrs) is 1 then", script)
        self.assertIn("set sender of newMessage to item 1 of emailAddrs", script)
        self.assertNotIn('set sender of newMessage to "', script)

    def test_injects_sender_when_from_address_is_valid(self):
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            if len(scripts) == 1:
                return "default@example.com\nsecondary@example.org"
            return "✓ Email sent successfully!"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.compose_email(
                account="Work",
                to="self@example.com",
                subject="Test",
                body="Body",
                mode="draft",
                from_address="secondary@example.org",
            )

        self.assertEqual(len(scripts), 2)
        main_script = scripts[1]
        self.assertIn('set sender of newMessage to "secondary@example.org"', main_script)
        self.assertNotIn("if (count of emailAddrs) is 1 then", main_script)

    def test_rejects_invalid_from_address_without_sending(self):
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            return "default@example.com"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.compose_email(
                account="Work",
                to="self@example.com",
                subject="Test",
                body="Body",
                mode="draft",
                from_address="unknown@example.com",
            )

        self.assertEqual(len(scripts), 1)
        self.assertTrue(result.startswith("Error: 'from_address'"))


class AccountDefaultAliasIfSingleTests(unittest.TestCase):
    def test_returns_sole_alias(self):
        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            return_value="solo@example.com",
        ):
            self.assertEqual(
                compose_tools._account_default_alias_if_single("Solo"),
                "solo@example.com",
            )

    def test_returns_none_when_empty(self):
        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            return_value="",
        ):
            self.assertIsNone(compose_tools._account_default_alias_if_single("Multi"))


class ComposeSenderScriptTests(unittest.TestCase):
    def test_override_sets_sender_directly(self):
        script = compose_tools._compose_sender_script("newMessage", "targetAccount", "chosen@example.com")
        self.assertEqual(script, 'set sender of newMessage to "chosen@example.com"')

    def test_without_override_emits_single_alias_fallback(self):
        script = compose_tools._compose_sender_script("newMessage", "targetAccount", None)
        self.assertIn("email addresses of targetAccount", script)
        self.assertIn("if (count of emailAddrs) is 1 then", script)
        self.assertIn("set sender of newMessage to item 1 of emailAddrs", script)

    def test_override_value_is_escaped(self):
        script = compose_tools._compose_sender_script("newMessage", "targetAccount", 'weird"quote@example.com')
        self.assertIn(r"\"quote@example.com", script)


class CreateRichEmailDraftFromAddressTests(unittest.TestCase):
    def test_omits_from_header_for_multi_alias_account(self):
        with _home_rooted_tmpdir() as tmpdir:
            output_path = Path(tmpdir) / "multi.eml"
            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    return_value="",
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run"),
            ):
                compose_tools.create_rich_email_draft(
                    account="Multi",
                    subject="No From",
                    to="team@example.com",
                    text_body="Body",
                    output_path=str(output_path),
                    open_in_mail=False,
                )

            payload = output_path.read_text()
            header_block = payload.split("\n\n", 1)[0]
            self.assertNotIn("From:", header_block)

    def test_stamps_from_header_for_single_alias_account(self):
        with _home_rooted_tmpdir() as tmpdir:
            output_path = Path(tmpdir) / "single.eml"
            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    return_value="solo@example.com",
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run"),
            ):
                compose_tools.create_rich_email_draft(
                    account="Solo",
                    subject="Single",
                    to="team@example.com",
                    text_body="Body",
                    output_path=str(output_path),
                    open_in_mail=False,
                )

            payload = output_path.read_text()
            self.assertIn("From: solo@example.com", payload)

    def test_stamps_from_header_when_address_is_valid(self):
        with _home_rooted_tmpdir() as tmpdir:
            output_path = Path(tmpdir) / "stamped.eml"
            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    return_value="default@example.com\nsecondary@example.org",
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run"),
            ):
                compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="Stamped",
                    to="team@example.com",
                    text_body="Body",
                    output_path=str(output_path),
                    open_in_mail=False,
                    from_address="secondary@example.org",
                )

            payload = output_path.read_text()
            self.assertIn("From: secondary@example.org", payload)


class ReplyToEmailSenderOverrideTests(unittest.TestCase):
    def test_reply_uses_native_mail_reply_and_preserves_native_quote_by_default(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        script = _main_reply_script(captured)
        # Native default: Mail's own reply window owns the rich quote + signature,
        # and the reply body is TYPED in (never reassigned via `set content`, which
        # flattens the native formatting, and never the clipboard), in small
        # focus-guarded chunks rather than one keystroke of the whole body
        # (AGENTIC-1214: a single keystroke drops its tail near 320-480 chars).
        _assert_ordered(
            self,
            script,
            "set replyBodyText to do shell script",
            "set replyMessage to reply foundMessage with opening window",
            "set replyWindowRaised to my raiseNativeReplyWindowSafely(replyWindowId, replySubject, derivedReplySubject)",
            "my typeReplyBodyChunks(replyBodyText",
            'set quotedNeedle to sourceSender & " wrote:"',
            "save replyMessage",
        )
        # The typeReplyBodyChunks handler definition (hoisted near the top of the
        # script, before the guard loop that calls it) types in chunks and clears
        # modifier state before and after each chunk (the suspected source of
        # Bug 3's leaked shift state).
        self.assertIn("keystroke chunkText", script)
        self.assertIn("key up shift", script)
        # The old one-shot keystroke of the whole body must be gone.
        self.assertNotIn("keystroke replyBodyText", script)
        # Body is typed, so content is never reassigned and no plain-text quote is built.
        self.assertNotIn("set content of replyMessage", script)
        self.assertNotIn("set composedReplyContent", script)
        self.assertNotIn("set quotedOriginalText", script)
        # Empty System Events title is tolerated (AX quirk for compose windows);
        # a different non-empty title aborts before typing. Subject cores are used
        # only to adopt Mail's normalized live title; keystroke still requires exact
        # title equality against the adopted replySubject.
        self.assertIn("on stripReplySubjectPrefixes(rawSubject)", script)
        self.assertIn("on subjectCoresMatch(leftSubject, rightSubject)", script)
        # An empty SE title is a real answer (compose windows report one); a
        # System Events call that never answered at all is not. The old form
        # accepted the "(unset)" sentinel, so a missing Accessibility grant read
        # as agreement from a probe that had not run.
        self.assertIn(
            'set seOk to (guardSEAnswered and (guardSE is replySubject or guardSE is ""))',
            script,
        )
        self.assertNotIn('guardSE is "(unset)"', script)
        self.assertIn("on error systemEventsErrMsg", script)
        self.assertIn("set replySubject to mailWindowTitle", script)
        self.assertIn("set mailOk to (guardMail is replySubject and guardMailWindowId is replyWindowId)", script)
        # Native default never pins the account alias (that drops the logo signature).
        self.assertNotIn("set sender of replyMessage", script)
        self.assertNotIn("make new outgoing message", script)
        self.assertNotIn("NSPasteboard", script)
        self.assertNotIn("set the clipboard", script)
        self.assertNotIn('keystroke "v"', script)

    def test_reply_to_email_accepts_output_format_parameter(self):
        params = inspect.signature(compose_tools.reply_to_email).parameters

        self.assertIn("output_format", params)
        self.assertEqual(params["output_format"].default, "text")

    def test_empty_reply_body_keeps_body_assignment_guarded(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="",
                native_format=False,
                allow_windowless_fallback=True,
            )

        script = _main_reply_script(captured)
        self.assertIn('if replyBodyText is not "" then', script)
        self.assertIn("set content of replyMessage to (composedReplyContent as rich text)", script)

    def test_reply_draft_success_outputs_artifact_id_for_exact_verification(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(to="native reply recipients", draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|not_requested|not_requested"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        self.assertIn("Draft ID: 84053", result)
        self.assertIn("Verification Status: found", result)
        self.assertIn("Verified Draft ID: 84053", result)
        verifier_script = next(script for script in captured if "set targetDraftId to" in script)
        self.assertIn('set targetDraftIdText to "84053"', verifier_script)
        self.assertIn("every message of draftsMailbox whose id is targetDraftId", verifier_script)
        self.assertIn("return exactResult", verifier_script)

    def test_reply_draft_success_json_outputs_contract(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(to="native reply recipients", draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|not_requested|not_requested"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["mode"], "draft")
        self.assertFalse(payload["sent"])
        self.assertEqual(payload["subject"], "Re: Test")
        self.assertEqual(payload["draft_id"], "84053")
        self.assertEqual(payload["captured_draft_id"], "84053")
        self.assertEqual(payload["draft_id_source"], "persisted_header_identity")
        self.assertEqual(payload["verified_draft_id"], "84053")
        self.assertEqual(payload["verification_status"], "found")
        self.assertTrue(payload["exact_id_verified"])
        self.assertTrue(payload["body_present"])
        self.assertEqual(payload["attachment_status"], "not_requested")
        self.assertEqual(payload["signature_status"], "not_requested")
        self.assertEqual(payload["mailbox"], "Drafts")

    def test_reply_draft_success_outputs_attachment_and_signature_verification(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "availableSignatures" in script:
                return ""
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(to="native reply recipients", draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|verified|missing|1|support.pdf::2048;;"
            return "ok"

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "apple_mail_mcp.tools.compose.run_applescript",
                side_effect=fake_run,
            ),
            patch("apple_mail_mcp.tools.compose._validate_attachment_paths") as mock_validate,
        ):
            attachment = Path(tmpdir) / "support.pdf"
            attachment.write_text("pdf")
            mock_validate.return_value = ([str(attachment)], None)
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                attachments=str(attachment),
                include_signature=True,
                signature_name="TU",
            )

        self.assertIn("Attachment Verification Status: verified", result)
        self.assertIn("Attachments Applied Count: 1", result)
        self.assertIn("support.pdf (2048 bytes)", result)
        self.assertIn("Signature Verification Status: missing", result)
        self.assertIn("requested Mail signature was not detected", result)
        verifier_script = next(script for script in captured if "set expectedAttachmentCount to" in script)
        self.assertIn('using terms from application "Mail"', verifier_script)
        self.assertIn("set expectedAttachmentCount to 1", verifier_script)
        self.assertIn('set expectedAttachmentNames to {"support.pdf"}', verifier_script)
        self.assertIn("set signatureWasRequested to true", verifier_script)
        self.assertIn('set expectedSignatureName to "TU"', verifier_script)
        self.assertIn("if (name of sig as string) is expectedSignatureName then", verifier_script)
        self.assertIn("set expectedSigText to content of sig as string", verifier_script)
        self.assertIn("expectedAttachmentName", verifier_script)
        self.assertIn("file size of anAttachment", verifier_script)

    def test_reply_draft_attachment_verification_checks_requested_filenames(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "FOUND|84053|missing|not_requested|1|wrong.pdf::2048;;"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            verification = compose_tools._verify_saved_reply_draft(
                "Work",
                "Re: Test",
                "Reply body",
                draft_id="84053",
                native_draft_identity=NativeReplyDraftIdentity(
                    draft_id="84053",
                    draft_rfc_message_id="<draft-84053@example.com>",
                    source_rfc_message_id="<source@example.com>",
                ),
                expected_attachment_count=1,
                expected_attachment_names=["support.pdf"],
                signature_requested=False,
            )

        self.assertFalse(verification.ok)
        self.assertEqual(verification.status, "attachment_verification_failed")
        self.assertEqual(verification.error_artifact_id, "84053")
        self.assertEqual(verification.attachment_status, "missing")
        script = captured[0]
        self.assertIn('set expectedAttachmentNames to {"support.pdf"}', script)
        self.assertIn("(name of anAttachment as string)", script)
        self.assertIn("set item matchIndex of draftAttachmentNames to missing value", script)

    def test_reply_draft_attachment_verification_uses_multiset_matching(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "FOUND|84053|missing|not_requested|2|support.pdf::2048;;other.pdf::1024;;"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            verification = compose_tools._verify_saved_reply_draft(
                "Work",
                "Re: Test",
                "Reply body",
                draft_id="84053",
                expected_attachment_count=2,
                expected_attachment_names=["support.pdf", "support.pdf"],
                signature_requested=False,
            )

        self.assertFalse(verification.ok)
        self.assertEqual(verification.status, "attachment_verification_failed")
        self.assertEqual(verification.error_artifact_id, "84053")
        self.assertEqual(verification.attachment_status, "missing")
        script = captured[0]
        self.assertIn('set expectedAttachmentNames to {"support.pdf", "support.pdf"}', script)
        self.assertIn("set item matchIndex of draftAttachmentNames to missing value", script)

    def test_reply_draft_attachment_verification_compares_raw_attachment_names(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "FOUND|84053|verified|not_requested|1|foo; ;bar.pdf::2048;;"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            verification = compose_tools._verify_saved_reply_draft(
                "Work",
                "Re: Test",
                "Reply body",
                draft_id="84053",
                native_draft_identity=NativeReplyDraftIdentity(
                    draft_id="84053",
                    draft_rfc_message_id="<draft-84053@example.com>",
                    source_rfc_message_id="<source@example.com>",
                ),
                expected_attachment_count=1,
                expected_attachment_names=["foo;;;bar.pdf"],
                signature_requested=False,
            )

        self.assertTrue(verification.ok)
        self.assertEqual(verification.attachment_status, "verified")
        script = captured[0]
        self.assertIn('set expectedAttachmentNames to {"foo;;;bar.pdf"}', script)
        self.assertIn("(name of anAttachment as string)", script)

    def test_reply_draft_attachment_failure_includes_applied_count(self):
        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(to="native reply recipients", draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|missing|not_requested|0|"
            return "ok"

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "apple_mail_mcp.tools.compose.run_applescript",
                side_effect=fake_run,
            ),
            patch("apple_mail_mcp.tools.compose._validate_attachment_paths") as mock_validate,
        ):
            attachment = Path(tmpdir) / "support.pdf"
            attachment.write_text("pdf")
            mock_validate.return_value = ([str(attachment)], None)
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                attachments=str(attachment),
                include_signature=False,
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["code"], "REPLY_DRAFT_ATTACHMENT_VERIFICATION_FAILED")
        self.assertEqual(payload["remediation"]["draft_id"], "84053")
        self.assertEqual(payload["remediation"]["attachment_status"], "missing")
        self.assertEqual(payload["remediation"]["attachment_count"], 0)
        self.assertEqual(payload["remediation"]["attachments_applied"], [])

    def test_reply_verification_parser_preserves_pipe_in_attachment_filename(self):
        verification = compose_tools._reply_verification_from_output(
            "FOUND|84053|verified|not_requested|1|support|final.pdf::2048;;"
        )

        self.assertTrue(verification.ok)
        self.assertEqual(verification.attachment_status, "verified")
        self.assertEqual(verification.attachment_count, 1)
        self.assertEqual(
            verification.attachments_applied,
            [{"filename": "support|final.pdf", "size": 2048}],
        )

    def test_reply_success_text_hides_attachment_count_when_not_requested(self):
        verification = compose_tools._reply_verification_from_output(
            "FOUND|84053|not_requested|not_requested|1|leftover.pdf::2048;;"
        )

        result = compose_tools._format_reply_verification_lines(verification, "84053")

        self.assertIn("Attachment Verification Status: not_requested", result)
        self.assertNotIn("Attachments Applied Count", result)
        self.assertNotIn("leftover.pdf", result)

    def test_reply_draft_success_json_includes_exact_attachment_and_signature_status(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "availableSignatures" in script:
                return ""
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(to="native reply recipients", draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|verified|missing|1|support.pdf::2048;;"
            return "ok"

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "apple_mail_mcp.tools.compose.run_applescript",
                side_effect=fake_run,
            ),
            patch("apple_mail_mcp.tools.compose._validate_attachment_paths") as mock_validate,
        ):
            attachment = Path(tmpdir) / "support.pdf"
            attachment.write_text("pdf")
            mock_validate.return_value = ([str(attachment)], None)
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                attachments=str(attachment),
                include_signature=True,
                signature_name="TU",
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["draft_id"], "84053")
        self.assertEqual(payload["verified_draft_id"], "84053")
        self.assertEqual(payload["verification_status"], "found")
        self.assertTrue(payload["exact_id_verified"])
        self.assertEqual(payload["attachment_status"], "verified")
        self.assertEqual(payload["attachment_count"], 1)
        self.assertEqual(
            payload["attachments_applied"],
            [{"filename": "support.pdf", "size": 2048}],
        )
        self.assertEqual(payload["signature_status"], "missing")
        self.assertFalse(payload["sent"])

    def test_reply_draft_success_json_promotes_verified_fallback_id(self):
        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(to="native reply recipients", draft_id=None)
            if 'set targetDraftIdText to ""' in script:
                return "FOUND|116814|not_requested|not_requested"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="116800",
                reply_body="Reply body",
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["draft_id"], "116814")
        self.assertIsNone(payload["captured_draft_id"])
        self.assertEqual(payload["draft_id_source"], "verification_fallback")
        self.assertEqual(payload["verified_draft_id"], "116814")
        self.assertEqual(payload["verification_status"], "found")
        self.assertFalse(payload["exact_id_verified"])
        self.assertTrue(payload["body_present"])

    def test_reply_draft_success_text_warns_when_fallback_verified_different_draft(self):
        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(to="native reply recipients", draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84054|not_requested|not_requested"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        self.assertIn("Draft ID: 84053", result)
        self.assertIn("Verified Draft ID: 84054", result)
        self.assertIn("verified by bounded Drafts fallback", result)

    def test_windowless_reply_all_with_attachment_fails_closed_without_persisted_identity(self):
        captured = []
        body_sentinel = "AA-REPLY-ALL-BODY-SENTINEL-84053"

        def fake_run(script, timeout=120):
            captured.append(script)
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(
                    to="native reply recipients",
                    draft_id="84053",
                    quote_needle="On Today, Sender <sender@example.com> wrote:",
                )
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|verified|not_requested"
            return "ok"

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "apple_mail_mcp.tools.compose.run_applescript",
                side_effect=fake_run,
            ),
            patch("apple_mail_mcp.tools.compose._validate_attachment_paths") as mock_validate,
        ):
            attachment = Path(tmpdir) / "support.pdf"
            attachment.write_text("pdf")
            mock_validate.return_value = ([str(attachment)], None)
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body=f"{body_sentinel}\n\nReply body",
                reply_to_all=True,
                attachments=str(attachment),
                include_signature=False,
                native_format=False,
                allow_windowless_fallback=True,
            )

        self.assertIn("did not verify it", result)
        self.assertIn("No email was sent", result)

        reply_script = _main_reply_script(captured)
        self.assertIn("set replyMessage to reply foundMessage with reply to all", reply_script)
        self.assertNotIn("System Events", reply_script)
        self.assertNotIn('keystroke "v"', reply_script)
        self.assertNotIn("set the clipboard to replyBodyText", reply_script)
        self.assertEqual(reply_script.count("set content of replyMessage to"), 1)
        self.assertEqual(reply_script.count("replyBodyText & return & return & quotedOriginalText"), 1)
        _assert_ordered(
            self,
            reply_script,
            "set replyMessage to reply foundMessage with reply to all",
            "set composedReplyContent to replyBodyText & return & return & quotedOriginalText",
            "set content of replyMessage to (composedReplyContent as rich text)",
            "make new attachment with properties {file name:theFile} at after the last paragraph of content",
            "save replyMessage",
        )

        verifier_script = next(script for script in captured if "set targetDraftIdText" in script)
        self.assertIn('set targetDraftIdText to "84053"', verifier_script)
        _assert_full_body_verifier_shape(self, verifier_script)
        self.assertIn("set expectedAttachmentCount to 1", verifier_script)
        self.assertIn("set requireExactAttachmentIdentity to true", verifier_script)

    def test_reply_defaults_to_draft_mode(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        script = _main_reply_script(captured)
        self.assertIn("SAVING REPLY AS DRAFT", script)
        # Native default: Mail opens its own reply window (rich quote + signature)
        # and the body is typed in; draft mode saves quietly and closes the window.
        self.assertIn("set replyMessage to reply foundMessage with opening window", script)
        self.assertGreaterEqual(script.count("save replyMessage"), 1)
        self.assertIn("my closeNativeReplyWindowSafely(replyWindowId, replySubject, derivedReplySubject)", script)
        self.assertNotIn("close (every window whose name is", script)
        self.assertNotIn("close (window of replyMessage)", script)
        self.assertNotIn("close front window", script)
        self.assertIn("set sourceSubject to subject of foundMessage as string", script)
        self.assertNotIn("set replySubject to subject of replyMessage as string", script)
        self.assertIn('set outputText to outputText & "Subject: " & replySubject', script)
        # Typed body: no plain-text quote assembly, no content reassignment. Body
        # is typed in focus-guarded chunks (AGENTIC-1214), not one keystroke.
        self.assertIn("my typeReplyBodyChunks(replyBodyText", script)
        self.assertNotIn("keystroke replyBodyText", script)
        self.assertNotIn("set quotedOriginalText to", script)
        self.assertNotIn("set composedReplyContent", script)
        self.assertNotIn("set content of replyMessage", script)
        self.assertNotIn("content of replyMessage as string", script)
        self.assertNotIn("NSPasteboard", script)
        self.assertNotIn('keystroke "v"', script)
        self.assertNotIn("send replyMessage", script)

    def test_reply_draft_success_runs_bounded_saved_draft_verifier(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "reply foundMessage" in script:
                return _saved_reply_draft_output()
            if "repeat with verifyAttempt from 1 to 20" in script:
                return "FOUND"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        self.assertIn("Reply saved as draft!", result)
        self.assertIn("Verification Status: found", result)
        verifier_script = next(script for script in captured if "repeat with verifyAttempt from 1 to 20" in script)
        self.assertIn("messages 1 thru headEnd of draftsMailbox", verifier_script)
        _assert_full_body_verifier_shape(self, verifier_script)
        self.assertIn('if "Re: Test" is "" or draftSubject is "Re: Test" then', verifier_script)
        self.assertIn(
            "my replyBodyAboveQuoteStatus(draftContent, fullReplyBody, quotedNeedle, quoteAnchor)", verifier_script
        )
        self.assertIn('return "BODY_MISSING|" & bodyMissingDraftId', verifier_script)

    def test_native_reply_scripts_capture_saved_draft_id_only_for_draft_and_open(self):
        """Draft/open expose an exact ID before draft mode closes its window.

        The retry path may delete only the full Drafts identity capsule emitted
        by the compose script. Keep this test at the generator boundary,
        instead of merely fabricating that output from ``run_applescript``, so
        it catches a future removal or reordering of the AppleScript contract.
        """
        for mode in ("draft", "open", "send"):
            with self.subTest(mode=mode):
                captured = []

                def fake_run(script, timeout=120, captured=captured):
                    captured.append(script)
                    return ""

                with (
                    patch.object(compose_tools._server, "READ_ONLY", False),
                    patch.object(compose_tools._server, "DRAFT_SAFE", False),
                    patch(
                        "apple_mail_mcp.tools.compose.run_applescript",
                        side_effect=fake_run,
                    ),
                ):
                    compose_tools.reply_to_email(
                        account="Work",
                        message_id="12345",
                        reply_body="Reply body",
                        mode=mode,
                    )

                script = _main_reply_script(captured)
                if mode == "send":
                    self.assertIn("send replyMessage", script)
                    self.assertNotIn("set replyDraftIdentity to my persistedReplyDraftIdentity", script)
                else:
                    self.assertIn("set sourceRfcMessageId to my sourceRfcMessageIdFor(foundMessage)", script)
                    self.assertIn("set preSaveDraftSnapshot to my fullDraftRfcSnapshot(draftsMailbox, 75)", script)
                    self.assertIn("set candidateDraftId to id of aDraft as string", script)
                    self.assertIn("if my headerHasExactRfcToken(item 2 of inReplyToResult, sourceMessageId)", script)
                    self.assertIn('if (count of newDraftIdentities) is not 1 then return missing value', script)
                    self.assertIn(
                        'if candidateRfcMessageId is "" then return {candidateDraftId, "", "", "transaction"}', script
                    )
                    self.assertIn('if postSaveDraftCount is not (preSaveDraftCount + 1) then return missing value', script)
                    self.assertIn("if totalDrafts > draftCap then return missing value", script)
                    self.assertIn("repeat with identityAttempt from 1 to 3", script)
                    self.assertNotIn("set replyDraftId to id of replyMessage as string", script)
                    _assert_ordered(
                        self,
                        script,
                        "set preSaveDraftSnapshot to my fullDraftRfcSnapshot",
                        "save replyMessage",
                        "set replyDraftIdentity to my persistedReplyDraftIdentity",
                    )
                    _assert_native_saved_draft_id_contract(self, script, quiet_close=mode == "draft")
                    self.assertIn('"Draft Identity: " & replyDraftId & "|||"', script)

    def test_native_identity_capsule_verifier_refuses_subject_fallback_on_drift(self):
        """A native capsule permits only immediate exact-ID identity verification."""
        captured = []
        identity = NativeReplyDraftIdentity(
            draft_id="91061",
            draft_rfc_message_id="<draft-91061@example.com>",
            source_rfc_message_id="<source@example.com>",
        )

        def fake_run(script, timeout=120):
            captured.append(script)
            return "IDENTITY_UNAVAILABLE"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            verification = compose_tools._verify_saved_reply_draft(
                "Work",
                "Re: Test",
                "Reply body",
                draft_id=identity.draft_id,
                native_draft_identity=identity,
            )

        self.assertFalse(verification.ok)
        self.assertEqual(verification.status, "identity_unavailable")
        script = captured[0]
        self.assertIn("set requireNativeIdentity to true", script)
        self.assertIn(
            'if (requireNativeIdentity or requireExactAttachmentIdentity) and attachmentFailureResult is "" then return "IDENTITY_UNAVAILABLE"',
            script,
        )
        self.assertIn('set expectedDraftRfcMessageId to "<draft-91061@example.com>"', script)
        self.assertIn('set expectedSourceRfcMessageId to "<source@example.com>"', script)

    def test_native_identity_capsule_delete_refuses_rfc_or_thread_drift(self):
        """Retry cleanup does not delete a matching numeric ID after identity drift."""
        captured = []
        identity = NativeReplyDraftIdentity(
            draft_id="91061",
            draft_rfc_message_id="<draft-91061@example.com>",
            source_rfc_message_id="<source@example.com>",
        )

        def fake_run(script, timeout=120):
            captured.append(script)
            return "NOT_IDENTITY|91061"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            deleted = reply_runner._delete_reply_artifact(
                "Work",
                "91061",
                identity=identity,
                timeout=None,
            )

        self.assertFalse(deleted)
        script = captured[0]
        self.assertIn('if (message id of targetDraft as string) is not "<draft-91061@example.com>"', script)
        self.assertIn('my headerHasExactRfcToken(item 2 of inReplyToResult, "<source@example.com>")', script)

    def test_reply_draft_verifier_falls_back_when_exact_id_is_not_yet_resolvable(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "FOUND|84054|not_requested|not_requested"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            verification = compose_tools._verify_saved_reply_draft(
                "Work",
                "Re: Test",
                "Reply body",
                draft_id="84053",
            )

        self.assertTrue(verification.ok)
        self.assertEqual(verification.matched_artifact_id, "84054")
        script = captured[0]
        exact_lookup = script.index("set targetDrafts to every message of draftsMailbox whose id is targetDraftId")
        fallback_lookup = script.index("set candidateDrafts to messages 1 thru headEnd of draftsMailbox")
        self.assertLess(exact_lookup, fallback_lookup)
        exact_branch = script[exact_lookup:fallback_lookup]
        self.assertNotIn('return "NOT_FOUND"', exact_branch)

    def test_reply_signature_verification_runs_when_signature_requested_without_resolved_name(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|not_requested|not_requested"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                include_signature=True,
                native_format=False,
                allow_windowless_fallback=True,
            )

        verifier_script = next(script for script in captured if "set signatureWasRequested" in script)
        self.assertIn("set signatureWasRequested to true", verifier_script)

    def test_reply_signature_verification_targets_resolved_signature_name(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "availableSignatures" in script:
                return ""
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|not_requested|detected"
            return "ok"

        with (
            patch.object(compose_tools.server, "DEFAULT_MAIL_SIGNATURE", "TU", create=True),
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                include_signature=True,
            )

        self.assertIn("Signature Verification Status: detected", result)
        verifier_script = next(script for script in captured if "set expectedSignatureName" in script)
        self.assertIn('set expectedSignatureName to "TU"', verifier_script)
        self.assertIn("if (name of sig as string) is expectedSignatureName then", verifier_script)
        self.assertIn("set expectedSigText to content of sig as string", verifier_script)

    def test_reply_attachment_validation_error_removes_body_temp_file(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        temp_path = Path(temp_dir.name) / "mail_reply_body.txt"

        class FakeTempFile:
            name = str(temp_path)

            def __enter__(self):
                self.handle = temp_path.open("w", encoding="utf-8")
                return self.handle

            def __exit__(self, exc_type, exc, tb):
                self.handle.close()

        with (
            patch("apple_mail_mcp.tools.compose.tempfile.NamedTemporaryFile", return_value=FakeTempFile()),
            patch(
                "apple_mail_mcp.tools.compose._validate_attachment_paths",
                return_value=([], "Error: Attachment file does not exist: missing.pdf"),
            ),
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                attachments="missing.pdf",
            )

        self.assertIn("Attachment file does not exist", result)
        self.assertFalse(temp_path.exists())

    def test_reply_draft_success_reports_structured_artifact_error_when_body_missing(self):
        # AGENTIC-1214: a persistent BODY_MISSING on the exact draft id triggers one
        # automatic delete-artifact-and-retype pass (the delete script's generic "ok"
        # response here does not confirm the delete, so it is surfaced as a stale
        # artifact). The second attempt mismatches too, so the final error is
        # REPLY_BODY_MISMATCH, not the un-retried REPLY_DRAFT_BODY_MISSING.
        sentinel = "AA-REPLY-BODY-SENTINEL-84053"

        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "BODY_MISSING|84053"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body=f"{sentinel}\n\nReply body",
            )

        payload = json.loads(result)
        self.assertEqual(payload["code"], "REPLY_BODY_MISMATCH")
        self.assertEqual(payload["remediation"]["artifact_message_id"], "84053")
        self.assertEqual(payload["remediation"]["draft_id"], "84053")
        self.assertEqual(payload["remediation"]["expected_body_preview"], sentinel)
        self.assertTrue(payload["remediation"]["retyped"])
        self.assertEqual(payload["remediation"]["stale_artifact_id"], "84053")
        self.assertIn("No email was sent", payload["message"])
        self.assertIn("automatic retype was attempted once", payload["message"])

    def test_reply_draft_verifier_timeout_preserves_saved_draft_id(self):
        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                raise AppleScriptTimeout("simulated verifier timeout")
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["code"], "REPLY_DRAFT_VERIFICATION_TIMEOUT")
        self.assertEqual(payload["remediation"]["artifact_message_id"], "84053")
        self.assertEqual(payload["remediation"]["draft_id"], "84053")
        self.assertEqual(payload["remediation"]["verification_status"], "verification_timeout")
        self.assertIn("No email was sent", payload["message"])

    def test_reply_draft_verifier_error_preserves_saved_draft_id(self):
        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                raise RuntimeError("simulated verifier failure")
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["code"], "REPLY_DRAFT_VERIFICATION_ERROR")
        self.assertEqual(payload["remediation"]["artifact_message_id"], "84053")
        self.assertEqual(payload["remediation"]["draft_id"], "84053")
        self.assertEqual(payload["remediation"]["verification_status"], "applescript_error")
        self.assertIn("No email was sent", payload["message"])

    def test_reply_to_email_rejects_json_mode_send_before_main_script(self):
        with (
            patch.object(compose_tools._server, "READ_ONLY", False),
            patch.object(compose_tools._server, "DRAFT_SAFE", False),
            patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                mode="send",
                output_format="json",
            )

        mock_run.assert_not_called()
        self.assertIn("output_format='json' is only supported", result)

    def test_reply_to_email_rejects_json_send_alias_before_main_script(self):
        with (
            patch.object(compose_tools._server, "READ_ONLY", False),
            patch.object(compose_tools._server, "DRAFT_SAFE", False),
            patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                send=True,
                output_format="json",
            )

        mock_run.assert_not_called()
        self.assertIn("output_format='json' is only supported", result)

    def test_reply_draft_verifier_rejects_body_after_quoted_original(self):
        def fake_run(script, timeout=120):
            if 'set targetDraftIdText to "84053"' in script:
                return "BODY_AFTER_QUOTE|84053"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            verification = compose_tools._verify_saved_reply_draft(
                "Work",
                "Re: Test",
                "Unique body sentinel",
                draft_id="84053",
                quoted_needle="Original message text",
            )

        self.assertFalse(verification.ok)
        self.assertEqual(verification.body_missing_artifact_id, "84053")
        self.assertEqual(verification.status, "body_after_quote")

    def test_reply_draft_reports_structured_error_when_body_saved_after_quote(self):
        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(
                    draft_id="84053",
                    quote_needle="On Today, Sender <sender@example.com> wrote:",
                )
            if 'set targetDraftIdText to "84053"' in script:
                return "BODY_AFTER_QUOTE|84053"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Unique body sentinel",
            )

        payload = json.loads(result)
        self.assertEqual(payload["code"], "REPLY_DRAFT_BODY_AFTER_QUOTE")
        self.assertEqual(payload["remediation"]["artifact_message_id"], "84053")
        self.assertEqual(payload["remediation"]["verification_status"], "body_after_quote")

    def test_reply_draft_success_reports_error_when_saved_draft_not_verified(self):
        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "NOT_FOUND"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        self.assertIn("did not verify it in the newest Drafts window", result)
        self.assertIn("No email was sent", result)

    def test_reply_open_mode_saves_before_leaving_open_for_review(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            # First call is the window-count probe (Fix #12); return 0 open windows.
            if "count of outgoing messages" in script:
                return "0"
            return "Reply opened in Mail for review."

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                mode="open",
            )

        # captured[0] is the window-count probe; find the main reply script.
        reply_scripts = [s for s in captured if "OPENING REPLY FOR REVIEW" in s]
        self.assertEqual(len(reply_scripts), 1)
        self.assertIn("reply foundMessage with opening window", reply_scripts[0])
        self.assertIn("save replyMessage", reply_scripts[0])
        self.assertIn("review", result)

    def test_reply_open_success_outputs_verification_status(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            if "OPENING REPLY FOR REVIEW" in script:
                return (
                    _saved_reply_draft_output(
                        subject="Re: Test",
                        draft_id="84053",
                        quote_needle="On Today, Sender <sender@example.com> wrote:",
                    )
                    .replace("SAVING REPLY AS DRAFT", "OPENING REPLY FOR REVIEW")
                    .replace(
                        "Reply saved as draft!",
                        "Reply opened in Mail for review. Edit and send when ready.",
                    )
                )
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|not_requested|not_requested"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                mode="open",
            )

        self.assertIn("Draft ID: 84053", result)
        self.assertIn("Verification Status: found", result)
        self.assertIn("Verified Draft ID: 84053", result)

    def test_reply_open_success_json_outputs_open_mode(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            if "OPENING REPLY FOR REVIEW" in script:
                return (
                    _saved_reply_draft_output(
                        subject="Re: Test",
                        draft_id="84053",
                        quote_needle="On Today, Sender <sender@example.com> wrote:",
                    )
                    .replace("SAVING REPLY AS DRAFT", "OPENING REPLY FOR REVIEW")
                    .replace(
                        "Reply saved as draft!",
                        "Reply opened in Mail for review. Edit and send when ready.",
                    )
                )
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|not_requested|not_requested"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                mode="open",
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["mode"], "open")
        self.assertFalse(payload["sent"])
        self.assertEqual(payload["subject"], "Re: Test")
        self.assertEqual(payload["draft_id"], "84053")
        self.assertEqual(payload["verified_draft_id"], "84053")
        self.assertEqual(payload["verification_status"], "found")

    def test_default_emits_single_alias_fallback_for_reply_message(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                send=False,
                native_format=False,
                allow_windowless_fallback=True,
            )

        script = _main_reply_script(captured)
        self.assertIn("if (count of emailAddrs) is 1 then", script)
        self.assertIn("set sender of replyMessage to item 1 of emailAddrs", script)
        self.assertNotIn('set sender of replyMessage to "', script)

    def test_reply_to_all_uses_native_mail_reply_all(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                reply_to_all=True,
            )

        script = _main_reply_script(captured)
        # Native reply-all opens the window with Mail's own reply-to-all recipients.
        self.assertIn(
            "reply foundMessage with opening window and reply to all",
            script,
        )
        self.assertIn("my typeReplyBodyChunks(replyBodyText", script)
        self.assertNotIn("keystroke replyBodyText", script)
        self.assertNotIn("to recipients of foundMessage", script)
        self.assertNotIn("cc recipients of foundMessage", script)
        self.assertNotIn(
            "if rAddr is not senderAddr and rAddr is not in myAddrs",
            script,
        )

    def test_reply_without_all_uses_native_plain_reply(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                reply_to_all=False,
            )

        script = _main_reply_script(captured)
        # Native plain reply (no reply-to-all): window opens without "reply to all".
        self.assertIn("set replyMessage to reply foundMessage with opening window", script)
        self.assertIn("my typeReplyBodyChunks(replyBodyText", script)
        self.assertNotIn("keystroke replyBodyText", script)
        self.assertNotIn("reply to all", script)
        self.assertNotIn("cc recipients of foundMessage", script)
        self.assertNotIn(
            "make new to recipient at end of to recipients of replyMessage",
            script,
        )

    def test_reply_signature_is_applied_before_body_insert(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "availableSignatures" in script:
                return ""
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                signature_name="TU",
                native_format=False,
                allow_windowless_fallback=True,
            )

        script = _main_reply_script(captured)
        _assert_ordered(
            self,
            script,
            "set replyMessage to reply foundMessage",
            'set message signature of replyMessage to signature "TU"',
            "set composedReplyContent to replyBodyText & return & return & quotedOriginalText",
            "set content of replyMessage to (composedReplyContent as rich text)",
        )
        self.assertNotIn("set the clipboard to replyBodyText", script)
        self.assertNotIn("System Events", script)
        self.assertNotIn('keystroke "v"', script)

    def test_include_signature_false_still_inserts_reply_body(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with (
            patch.object(compose_tools.server, "DEFAULT_MAIL_SIGNATURE", "TU", create=True),
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Unique body sentinel 84053",
                include_signature=False,
                native_format=False,
                allow_windowless_fallback=True,
            )

        script = _main_reply_script(captured)
        self.assertIn("set message signature of replyMessage to missing value", script)
        _assert_ordered(
            self,
            script,
            "set message signature of replyMessage to missing value",
            'if replyBodyText is not "" then',
            "set composedReplyContent to replyBodyText & return & return & quotedOriginalText",
            "set content of replyMessage to (composedReplyContent as rich text)",
            "save replyMessage",
        )

    def test_include_signature_false_suppresses_default_signature_and_verifies_one_draft(self):
        captured = []
        body_sentinel = "AA-NO-SIGNATURE-BODY-SENTINEL-81121"

        def fake_run(script, timeout=120):
            captured.append(script)
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(
                    to="native reply recipients",
                    draft_id="81121",
                    quote_needle="On Today, Sender <sender@example.com> wrote:",
                )
            if 'set targetDraftIdText to "81121"' in script:
                return "FOUND|81121|not_requested|not_requested"
            return "ok"

        with (
            patch.object(compose_tools.server, "DEFAULT_MAIL_SIGNATURE", "TU", create=True),
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body=f"{body_sentinel}\n\nReply body",
                include_signature=False,
                native_format=False,
                allow_windowless_fallback=True,
            )

        self.assertIn("Draft ID: 81121", result)
        self.assertIn("Verification Status: found", result)
        self.assertIn("Verified Draft ID: 81121", result)
        self.assertIn("Signature Verification Status: not_requested", result)

        script = _main_reply_script(captured)
        self.assertIn("set replyMessage to reply foundMessage", script)
        self.assertIn("set message signature of replyMessage to missing value", script)
        self.assertNotIn('set message signature of replyMessage to signature "TU"', script)
        self.assertNotIn("with opening window", script)
        self.assertNotIn("close (window of replyMessage)", script)
        self.assertNotIn("close front window", script)
        self.assertEqual(script.count("save replyMessage"), 1)
        _assert_ordered(
            self,
            script,
            "set message signature of replyMessage to missing value",
            "set composedReplyContent to replyBodyText & return & return & quotedOriginalText",
            "set content of replyMessage to (composedReplyContent as rich text)",
            "save replyMessage",
            "set replyDraftId to id of replyMessage as string",
        )

        verifier_script = next(script for script in captured if "set signatureWasRequested" in script)
        self.assertIn('set targetDraftIdText to "81121"', verifier_script)
        _assert_full_body_verifier_shape(self, verifier_script)
        self.assertIn("set signatureWasRequested to false", verifier_script)
        self.assertIn("every message of draftsMailbox whose id is targetDraftId", verifier_script)

    def test_native_default_skips_signature_verification(self):
        # Native default inherits Mail's own logo signature, whose rich text we never
        # set and cannot substring-match. The verifier must be told the signature was
        # NOT requested (missing value) so the native default is not flagged "missing".
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(to="native reply recipients", draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|not_requested|not_requested"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                include_signature=True,
            )

        verifier_script = next(script for script in captured if "set signatureWasRequested" in script)
        self.assertIn("set signatureWasRequested to missing value", verifier_script)
        # The native main script still applies Mail's own default signature property,
        # but verification of it is deliberately skipped.
        main_script = _main_reply_script(captured)
        self.assertIn("set replyMessage to reply foundMessage with opening window", main_script)

    def test_native_reply_guard_abort_returns_focus_failed_error(self):
        # When the native path cannot bring the reply window into focus it returns a
        # GUARD_ABORT sentinel without saving; reply_to_email maps that to a structured
        # REPLY_WINDOW_FOCUS_FAILED error that tells callers to retry native and NOT
        # switch off native formatting.
        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return "\n".join(
                    [
                        "GUARD_ABORT",
                        "Subject: Re: Test",
                        "DerivedSubject: Re: Test",
                        "Detail: could not focus reply window (mailFront=Inbox seFront=Inbox)",
                    ]
                )
            if 'set targetDraftIdText to ""' in script:
                return "NOT_FOUND"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        payload = json.loads(result)
        self.assertTrue(payload["error"])
        self.assertEqual(payload["code"], "REPLY_WINDOW_FOCUS_FAILED")
        self.assertIn("Do not switch off native formatting", payload["remediation"]["alternative"])
        self.assertNotIn("native_format=False", payload["remediation"]["alternative"])
        self.assertEqual(payload["remediation"]["draft_artifact_status"], "not_found")
        self.assertIsNone(payload["remediation"]["suspected_draft_id"])
        self.assertIn("GUARD_ABORT", payload["remediation"]["detail"])

    def test_native_reply_guard_abort_reports_suspected_artifact_id(self):
        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return "\n".join(
                    [
                        "GUARD_ABORT",
                        "Subject: Re: Test",
                        "DerivedSubject: Re: Test",
                        "Detail: could not focus reply window (mailFront=Inbox seFront=Inbox)",
                    ]
                )
            if 'set targetDraftIdText to ""' in script:
                return "BODY_MISSING|116814"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        payload = json.loads(result)
        self.assertTrue(payload["error"])
        self.assertEqual(payload["code"], "REPLY_WINDOW_FOCUS_FAILED")
        self.assertEqual(payload["remediation"]["draft_artifact_status"], "body_missing")
        self.assertEqual(payload["remediation"]["suspected_draft_id"], "116814")
        # Reported for inspection, not offered as a delete target: the probe
        # finds it by reply subject with no draft id to match against, so it can
        # just as easily be a draft the user wrote in this thread earlier. Full
        # contract in tests/compose/test_reply_abort_cleanup_authorization.py.
        self.assertIn("verify_draft(draft_id=...)", payload["remediation"]["cleanup"])
        self.assertNotIn("manage_drafts(action='delete'", payload["remediation"]["cleanup"])

    def test_native_reply_subject_guard_mismatch_returns_distinct_error(self):
        # When Mail opens a reply-looking window whose title still fails the subject
        # core match, map GUARD_ABORT_SUBJECT to REPLY_SUBJECT_GUARD_MISMATCH so
        # callers can distinguish it from true focus loss.
        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return "\n".join(
                    [
                        "GUARD_ABORT_SUBJECT",
                        "Subject: Re: Placeholder: Equire CRE Demo",
                        "DerivedSubject: RE:  Re: Placeholder: Equire CRE Demo",
                        ("Detail: could not focus reply window (mailFront=Re: Other Thread seFront=Re: Other Thread)"),
                    ]
                )
            if 'set targetDraftIdText to ""' in script:
                return "NOT_FOUND"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        payload = json.loads(result)
        self.assertTrue(payload["error"])
        self.assertEqual(payload["code"], "REPLY_SUBJECT_GUARD_MISMATCH")
        self.assertEqual(
            payload["remediation"]["expected_subject"],
            "Re: Placeholder: Equire CRE Demo",
        )
        self.assertEqual(
            payload["remediation"]["derived_subject"],
            "RE:  Re: Placeholder: Equire CRE Demo",
        )
        self.assertIn("GUARD_ABORT_SUBJECT", payload["remediation"]["detail"])
        self.assertIn("Do not switch off native formatting", payload["remediation"]["alternative"])

    def test_native_reply_script_normalizes_double_re_subject_guard(self):
        # AGENTIC-1014: Mail collapses "RE:  Re: Foo" to "Re: Foo" in the compose
        # window title. The native script must adopt the live window title and
        # compare subject cores so the keystroke guard does not false-fail.
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="119318",
                reply_body="Thanks for the invite.",
            )

        script = _main_reply_script(captured)
        self.assertIn("set derivedReplySubject to sourceSubject", script)
        self.assertIn("set replyMessageSubject to subject of replyMessage as string", script)
        self.assertIn("if my subjectCoresMatch(replyMessageSubject, derivedReplySubject) then", script)
        self.assertIn("if my subjectCoresMatch(mailWindowTitle, derivedReplySubject) then", script)
        self.assertIn("set replySubject to mailWindowTitle", script)
        self.assertIn("on stripReplySubjectPrefixes(rawSubject)", script)
        self.assertIn('if t starts with "re:" then', script)
        self.assertIn('if t starts with "fwd:" then', script)
        # Keystroke boundary stays exact-title; core match is adoption-only.
        self.assertIn("set mailOk to (guardMail is replySubject and guardMailWindowId is replyWindowId)", script)
        self.assertNotIn("set mailOk to my replyWindowTitlesMatch", script)
        self.assertIn('set abortCode to "GUARD_ABORT_SUBJECT"', script)
        self.assertIn("on closeNativeReplyWindowSafely(replyWindowId, expectedTitle, derivedTitle)", script)
        self.assertIn("close candidateWindow saving no", script)
        self.assertNotIn("close (every window whose name is", script)
        # Still never reassign content on the native path.
        self.assertNotIn("set content of replyMessage", script)
        # Short body (one chunk): still goes through the chunked typing handler
        # with its modifier-hygiene `key up` clear, the Bug 3 regression anchor.
        self.assertIn("my typeReplyBodyChunks(replyBodyText", script)
        self.assertNotIn("keystroke replyBodyText", script)
        self.assertIn("key up shift", script)

    def test_windowless_fallback_disabled_without_ack(self):
        # native_format=False is gated: without allow_windowless_fallback=True the tool
        # returns WINDOWLESS_FALLBACK_DISABLED before any AppleScript runs, so agents
        # cannot drift into the windowless plain-text fallback path.
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                native_format=False,
            )
        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertTrue(payload["error"])
        self.assertEqual(payload["code"], "WINDOWLESS_FALLBACK_DISABLED")
        self.assertIn("preferred", payload["remediation"])
        self.assertIn("headless_only", payload["remediation"])

    def test_native_reply_verifier_rejoins_soft_wrapped_lines(self):
        # Mail soft-wraps long typed lines, and `content as string` renders the wraps
        # as line breaks (sometimes mid-word). flattenForCompare strips whitespace
        # (return/linefeed/tab/space/nbsp) before the case-sensitive contiguous-
        # substring match, so a wrapped body is still found (AGENTIC-1214).
        #
        # The strip now runs AFTER foldSentenceStarts, not before it. That order is
        # the fix for the paragraph-start half of REPLY_BODY_MISMATCH: the fold needs
        # the line breaks the strip erases, so folding second left every paragraph
        # start case-sensitive. Both steps must survive, in that order.
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "FOUND|84053|not_requested|not_requested"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools._verify_saved_reply_draft(
                "Work",
                "Re: Test",
                "Reply body",
                draft_id="84053",
            )

        verifier_script = captured[0]
        _assert_full_body_verifier_shape(self, verifier_script)
        self.assertIn("on foldPair(theText, fromText, toText)", verifier_script)
        strip_loop = "repeat with stripChar in {return, linefeed, tab, space, (character id 160)}"
        self.assertIn(strip_loop, verifier_script)
        self.assertNotIn("on stripLineBreaks(theText)", verifier_script)

        flatten_body = verifier_script[verifier_script.index("on flattenForCompare(theText)") :]
        flatten_body = flatten_body[: flatten_body.index("end flattenForCompare")]
        self.assertLess(
            flatten_body.index("set t to my foldSentenceStarts(t)"),
            flatten_body.index(strip_loop),
            "flattenForCompare must fold sentence/paragraph starts before it strips the "
            "whitespace that marks them",
        )

    def test_native_reply_verifier_folds_sentence_starts_with_delimiter_split_not_character_walk(self):
        # AGENTIC-1214 perf fix: foldSentenceStarts used to walk the whole
        # draft body character-by-character (a handler call plus a string
        # reallocation per character), which is O(n^2) and could burn the
        # verifier's timeout on long quoted-thread drafts. It now splits on
        # each sentence delimiter via AppleScript's text item delimiters and
        # only rewrites the first character of each following item, so cost
        # tracks sentence count, not text length.
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "FOUND|84053|not_requested|not_requested"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools._verify_saved_reply_draft(
                "Work",
                "Re: Test",
                "Reply body",
                draft_id="84053",
            )

        verifier_script = captured[0]
        self.assertIn("on foldFirstChar(theString)", verifier_script)
        self.assertIn('repeat with delimiterChar in {".", "!", "?", return, linefeed}', verifier_script)
        self.assertNotIn("repeat with i from 1 to n", verifier_script)
        self.assertNotIn("set foldNext to true", verifier_script)

    def test_native_reply_full_body_verifier_is_case_sensitive_and_above_quote(self):
        # AGENTIC-1214: the saved-reply verifier compares the full body above the
        # quote, case-sensitively, from a temp file (not a first-line needle).
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(to="native reply recipients", draft_id="91061")
            if 'set targetDraftIdText to "91061"' in script:
                return "FOUND|91061|not_requested|not_requested"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        verifier_script = next(script for script in captured if 'set targetDraftIdText to "91061"' in script)
        _assert_full_body_verifier_shape(self, verifier_script)

    def test_native_reply_chunks_body_with_typing_bounds(self):
        # AGENTIC-1214 Bug 1/Bug 3: the native path types in focus-guarded chunks
        # sized from TYPING_CHUNK_SIZE/TYPING_INTER_CHUNK_DELAY, not one keystroke.
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        script = _main_reply_script(captured)
        self.assertIn(
            "on typeReplyBodyChunks(bodyText, expectedTitle, derivedTitle, expectedWindowId, preResolvedEditor)",
            script,
        )
        self.assertIn(f"set chunkEnd to chunkStart + {compose_tools.TYPING_CHUNK_SIZE} - 1", script)
        self.assertIn(f"delay {compose_tools.TYPING_INTER_CHUNK_DELAY}", script)
        self.assertIn("key up shift", script)
        self.assertIn("key up option", script)
        self.assertIn("key up control", script)
        self.assertIn("key up command", script)

    def test_native_reply_typing_interrupted_returns_structured_error(self):
        # AGENTIC-1214: focus lost mid-chunk-typing aborts and discards the
        # partially typed compose window; no partial draft is left behind.
        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return "\n".join(
                    [
                        "TYPING_INTERRUPTED",
                        "Subject: Re: Test",
                        "DerivedSubject: Re: Test",
                        "Detail: interrupted:Inbox (mailFront=Re: Test seFront=Re: Test)",
                    ]
                )
            if 'set targetDraftIdText to ""' in script:
                return "NOT_FOUND"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        payload = json.loads(result)
        self.assertTrue(payload["error"])
        self.assertEqual(payload["code"], "REPLY_BODY_TYPING_INTERRUPTED")
        self.assertIn("suspected_draft_id", payload["remediation"])
        self.assertIn("cleanup", payload["remediation"])
        self.assertIn("no email was sent", payload["message"].lower())
        self.assertIn("interrupted:Inbox", payload["remediation"]["detail"])

    def test_native_reply_body_mismatch_retries_then_returns_mismatch(self):
        # AGENTIC-1214: a BODY_MISSING verification with a concrete artifact id
        # triggers one delete-and-retype pass; a second BODY_MISSING (with the
        # drifted Exchange-style id 91062) returns the final REPLY_BODY_MISMATCH.
        compose_calls = {"count": 0}
        delete_scripts = []

        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                compose_calls["count"] += 1
                draft_id = "91061" if compose_calls["count"] == 1 else "91062"
                return _saved_reply_draft_output(
                    to="native reply recipients",
                    draft_id=draft_id,
                    draft_identity=f"{draft_id}|||<draft-{draft_id}@example.com>|||<source@example.com>|||rfc",
                )
            if 'set targetDraftIdText to "91061"' in script:
                return "BODY_MISSING|91061"
            if 'set targetDraftIdText to "91062"' in script:
                return "BODY_MISSING|91062"
            if "delete targetDraft" in script:
                delete_scripts.append(script)
                return "DELETED|91061"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        payload = json.loads(result)
        self.assertEqual(payload["code"], "REPLY_BODY_MISMATCH")
        self.assertEqual(payload["remediation"]["artifact_message_id"], "91062")
        self.assertTrue(payload["remediation"]["retyped"])
        self.assertEqual(compose_calls["count"], 2)
        self.assertEqual(len(delete_scripts), 1)
        self.assertIn("every message of draftsMailbox whose id is 91061", delete_scripts[0])

    def test_native_reply_body_mismatch_retype_succeeds(self):
        # Same delete-and-retype path as above, but the second attempt verifies.
        # Assert the generated native script itself resolves and emits the exact
        # persisted Drafts ID that this mocked Mail result represents. Without that
        # contract, a mocked ``Draft ID`` response could make this retry pass
        # while the real plugin has no safe delete target.
        compose_calls = {"count": 0}

        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                compose_calls["count"] += 1
                _assert_native_saved_draft_id_contract(self, script, quiet_close=True)
                draft_id = "91061" if compose_calls["count"] == 1 else "91062"
                return _saved_reply_draft_output(
                    to="native reply recipients",
                    draft_id=draft_id,
                    draft_identity=f"{draft_id}|||<draft-{draft_id}@example.com>|||<source@example.com>|||rfc",
                )
            if 'set targetDraftIdText to "91061"' in script:
                return "BODY_MISSING|91061"
            if 'set targetDraftIdText to "91062"' in script:
                return "FOUND|91062|not_requested|not_requested"
            if "delete targetDraft" in script:
                return "DELETED|91061"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                output_format="json",
            )

        payload = json.loads(result)
        self.assertNotIn("error", payload)
        self.assertTrue(payload["retyped"])
        self.assertTrue(payload["body_present"])
        self.assertEqual(payload["body_verified"], "full_above_quote")
        self.assertEqual(payload["draft_id"], "91062")
        self.assertEqual(compose_calls["count"], 2)

    def test_native_reply_ambiguous_persisted_drafts_id_omits_id_and_does_not_retry(self):
        # A zero/multiple-candidate persisted-Drafts resolver emits no Draft ID.
        # The bounded verifier can still report a suspected artifact, but without
        # a script-proven exact ID this path must never delete or retype it.
        compose_calls = {"count": 0}

        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                compose_calls["count"] += 1
                self.assertIn('if (count of newDraftIdentities) is not 1 then return missing value', script)
                self.assertIn("if my headerHasExactRfcToken(item 2 of inReplyToResult, sourceMessageId)", script)
                return _saved_reply_draft_output(to="native reply recipients")
            if 'set targetDraftIdText to ""' in script:
                return "BODY_MISSING|91061"
            if "delete targetDraft" in script:
                raise AssertionError("ambiguity must not trigger exact-id deletion")
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["code"], "REPLY_BODY_MISMATCH")
        self.assertFalse(payload["remediation"]["retyped"])
        self.assertEqual(compose_calls["count"], 1)

    def test_native_reply_retry_skipped_when_mismatch_artifact_differs_from_draft_id(self):
        # Native identity capsules now suppress subject fallback entirely, but
        # keep this parser-level defense: a malformed or unexpected verifier
        # response whose artifact id differs from the capsule's Drafts id must
        # never delete or retype any draft.
        compose_calls = {"count": 0}

        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                compose_calls["count"] += 1
                return _saved_reply_draft_output(
                    to="native reply recipients",
                    draft_id="91061",
                    draft_identity="91061|||<draft-91061@example.com>|||<source@example.com>|||rfc",
                )
            if 'set targetDraftIdText to "91061"' in script:
                return "BODY_MISSING|55555"
            if "delete targetDraft" in script:
                raise AssertionError("retry must not delete when artifact id differs from compose draft id")
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        payload = json.loads(result)
        self.assertEqual(payload["code"], "REPLY_BODY_MISMATCH")
        # The id is reported as a suspect, not as a delete target. The retry
        # logic above refuses to delete 55555 because it cannot prove it created
        # it; the remediation must not then hand the same id to the agent as
        # `draft_id` with instructions to delete it.
        self.assertEqual(payload["remediation"]["suspect_artifact_message_id"], "55555")
        self.assertNotIn("draft_id", payload["remediation"])
        self.assertFalse(payload["remediation"]["artifact_identity_verified"])
        self.assertFalse(payload["remediation"]["retyped"])
        self.assertEqual(compose_calls["count"], 1)

    def test_native_reply_converts_tabs_to_spaces_before_temp_file_write(self):
        # AGENTIC-1214 design amendment 7: a literal tab typed via System Events
        # keystroke is a field-navigation key and can move focus out of the
        # compose body field. The native path must convert tabs to spaces in
        # reply_body before the body temp file (read by the AppleScript typing
        # loop) is written.
        captured_temp_writes = []

        class _CapturingTempFile:
            name = "/tmp/mail_reply_tab_test.txt"

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def write(self, data):
                captured_temp_writes.append(data)

        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(to="native reply recipients", draft_id="91061")
            if 'set targetDraftIdText to "91061"' in script:
                return "FOUND|91061|not_requested|not_requested"
            return "ok"

        with (
            patch(
                "apple_mail_mcp.tools.compose.tempfile.NamedTemporaryFile",
                return_value=_CapturingTempFile(),
            ),
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Line one\tLine two",
            )

        # The same NamedTemporaryFile patch also captures the verifier's own
        # comparison temp file (saved_draft_checks.py writes the same already-
        # converted reply_body for its "cat"-and-compare check), so every
        # capture must show the converted, tab-free body.
        self.assertTrue(captured_temp_writes)
        for written in captured_temp_writes:
            self.assertEqual(written, "Line one Line two")
            self.assertNotIn("\t", written)

    def test_invalid_reply_signature_is_rejected_before_native_reply(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "availableSignatures" in script:
                return 'Error: Mail signature "Missing" not found. Available signatures: TU, Agentic Assets'
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                signature_name="Missing",
            )

        self.assertIn('Mail signature "Missing" not found', result)
        self.assertFalse(
            any("reply foundMessage" in script for script in captured),
            captured,
        )

    def test_injects_sender_when_from_address_is_valid(self):
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            if len(scripts) == 1:
                return "default@example.com\nsecondary@example.org"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                from_address="secondary@example.org",
                send=False,
            )

        self.assertEqual(len(scripts), 2)
        script = _main_reply_script(scripts)
        self.assertIn('set sender of replyMessage to "secondary@example.org"', script)
        self.assertNotIn("if (count of emailAddrs) is 1 then", script)

    def test_rejects_invalid_from_address_without_running_main_script(self):
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            return "default@example.com"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                from_address="unknown@example.com",
                send=False,
            )

        self.assertEqual(len(scripts), 1)
        self.assertTrue(result.startswith("Error: 'from_address'"))


class ForwardEmailSenderOverrideTests(unittest.TestCase):
    def test_forward_defaults_to_draft_mode(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Forward saved"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="recipient@example.com",
            )

        self.assertEqual(len(captured), 1)
        script = captured[0]
        self.assertIn("SAVING FORWARD AS DRAFT", script)
        # Object-model forward: race-free `make new outgoing message`, single
        # `save forwardMessage`, NO GUI window, NO clipboard, NO System Events.
        self.assertIn("make new outgoing message", script)
        self.assertEqual(script.count("save forwardMessage"), 1)
        self.assertNotIn("close window 1 saving no", script)
        self.assertNotIn('keystroke "v"', script)
        self.assertNotIn("NSPasteboard", script)
        self.assertNotIn("forward foundMessage with opening window", script)
        self.assertNotIn("send forwardMessage", script)

    def test_forward_open_mode_saves_before_leaving_open_for_review(self):
        captured = []
        call_count = [0]

        def fake_run(script, timeout=120):
            captured.append(script)
            call_count[0] += 1
            # First call is the window-count probe (Fix #12); return 0 open windows.
            if "count of outgoing messages" in script:
                return "0"
            return "Forward opened in Mail for review."

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="recipient@example.com",
                mode="open",
            )

        # captured[0] is the window-count probe; captured[1] is the forward script.
        forward_scripts = [s for s in captured if "OPENING FORWARD FOR REVIEW" in s]
        self.assertEqual(len(forward_scripts), 1)
        self.assertIn("save forwardMessage", forward_scripts[0])
        self.assertIn("review", result)

    def test_forward_draft_success_outputs_draft_id_and_verification(self):
        captured = []
        verify_calls = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return _saved_forward_draft_output(draft_id="84055")

        def fake_verify(**kwargs):
            verify_calls.append(kwargs)
            return json.dumps({"draft_id": kwargs["draft_id"], "found": True, "warnings": []})

        with (
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
            patch("apple_mail_mcp.tools.compose.verify_draft", side_effect=fake_verify),
        ):
            result = compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="recipient@example.com",
                message="Please review\nMore context",
                include_signature=False,
            )

        self.assertIn("Draft ID: 84055", result)
        self.assertIn("Verification Status: found", result)
        self.assertIn("Verified Draft ID: 84055", result)
        self.assertEqual(len(verify_calls), 1)
        self.assertEqual(
            verify_calls[0],
            {
                "account": "Work",
                "draft_id": "84055",
                "expected_to": "recipient@example.com",
                "expected_subject": "Fwd: Test",
                "expected_body_contains": "Please review",
                "expected_signature": False,
                "timeout": None,
            },
        )
        script = captured[0]
        self.assertIn("set savedDraftIdentity to my persistedStandaloneDraftId", script)
        self.assertNotIn("set forwardDraftId to id of forwardMessage as string", script)
        self.assertIn('"Draft ID: " & savedDraftId', script)

    def test_forward_draft_reports_verification_warnings(self):
        def fake_run(script, timeout=120):
            return _saved_forward_draft_output(draft_id="84055")

        def fake_verify(**kwargs):
            return json.dumps({"draft_id": kwargs["draft_id"], "found": True, "warnings": ["signature_unexpected"]})

        with (
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
            patch("apple_mail_mcp.tools.compose.verify_draft", side_effect=fake_verify),
        ):
            result = compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="recipient@example.com",
                include_signature=False,
            )

        self.assertIn("Verification Status: found_with_warnings", result)
        self.assertIn("Verification Warnings: signature_unexpected", result)

    def test_default_emits_single_alias_fallback_for_forward_message(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Forwarded"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="recipient@example.com",
            )

        self.assertEqual(len(captured), 1)
        script = captured[0]
        self.assertIn("if (count of emailAddrs) is 1 then", script)
        self.assertIn("set sender of forwardMessage to item 1 of emailAddrs", script)
        self.assertNotIn('set sender of forwardMessage to "', script)

    def test_injects_sender_when_from_address_is_valid(self):
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            if len(scripts) == 1:
                return "default@example.com\nsecondary@example.org"
            return "✓ Forwarded"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="recipient@example.com",
                from_address="secondary@example.org",
            )

        self.assertEqual(len(scripts), 2)
        main_script = scripts[1]
        self.assertIn(
            'set sender of forwardMessage to "secondary@example.org"',
            main_script,
        )
        self.assertNotIn("if (count of emailAddrs) is 1 then", main_script)

    def test_rejects_invalid_from_address_without_running_main_script(self):
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            return "default@example.com"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="recipient@example.com",
                from_address="unknown@example.com",
            )

        self.assertEqual(len(scripts), 1)
        self.assertTrue(result.startswith("Error: 'from_address'"))


class ManageDraftsCreateSenderOverrideTests(unittest.TestCase):
    def test_manage_drafts_accepts_exact_draft_id_parameter(self):
        params = inspect.signature(compose_tools.manage_drafts).parameters

        self.assertIn("draft_id", params)
        self.assertIsNone(params["draft_id"].default)

    def test_create_draft_blocks_reply_like_subject_without_confirmation(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.manage_drafts(
                account="Work",
                action="create",
                subject="Re: Complex Request",
                to="sender@example.com",
                body="Thread-like draft",
            )

        mock_run.assert_not_called()
        self.assertIn("standalone new message", result)
        self.assertIn("Use reply_to_email(message_id=...)", result)

    def test_create_draft_allows_reply_like_subject_when_confirmed(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Draft created"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.manage_drafts(
                account="Work",
                action="create",
                subject="Re: standalone project name",
                to="team@example.com",
                body="This is a new standalone draft.",
                standalone_confirmed=True,
            )

        self.assertEqual(len(captured), 1)
        self.assertIn("CREATING DRAFT", captured[0])
        self.assertIn("save newDraft", captured[0])
        self.assertIn("set draftId to id of newDraft as string", captured[0])
        self.assertIn('set outputText to outputText & "Draft ID: " & draftId', captured[0])
        self.assertIn("Draft created", result)

    def test_default_emits_single_alias_fallback_for_new_draft(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Draft created"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.manage_drafts(
                account="Work",
                action="create",
                subject="Draft",
                to="recipient@example.com",
                body="Body",
            )

        self.assertEqual(len(captured), 1)
        script = captured[0]
        self.assertIn("if (count of emailAddrs) is 1 then", script)
        self.assertIn("set sender of newDraft to item 1 of emailAddrs", script)
        self.assertIn("save newDraft", script)
        self.assertIn("set draftId to id of newDraft as string", script)
        self.assertNotIn('set sender of newDraft to "', script)

    def test_injects_sender_when_from_address_is_valid(self):
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            if len(scripts) == 1:
                return "default@example.com\nsecondary@example.org"
            return "✓ Draft created"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.manage_drafts(
                account="Work",
                action="create",
                subject="Draft",
                to="recipient@example.com",
                body="Body",
                from_address="secondary@example.org",
            )

        self.assertEqual(len(scripts), 2)
        main_script = scripts[1]
        self.assertIn('set sender of newDraft to "secondary@example.org"', main_script)
        self.assertNotIn("if (count of emailAddrs) is 1 then", main_script)

    def test_rejects_invalid_from_address_without_running_main_script(self):
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            return "default@example.com"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.manage_drafts(
                account="Work",
                action="create",
                subject="Draft",
                to="recipient@example.com",
                body="Body",
                from_address="unknown@example.com",
            )

        self.assertEqual(len(scripts), 1)
        self.assertTrue(result.startswith("Error: 'from_address'"))

    def test_send_draft_prefers_exact_draft_id(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Draft sent successfully!"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.manage_drafts(
                account="Work",
                action="send",
                draft_subject="Duplicate Subject",
                draft_id="84053",
            )

        self.assertIn("Draft sent", result)
        script = captured[0]
        self.assertIn("every message of draftsMailbox whose id is 84053", script)
        self.assertIn('set outputText to outputText & "Draft ID: " & draftId', script)
        self.assertNotIn('contains "Duplicate Subject"', script)

    def test_send_draft_subject_returns_deprecation_before_read_only_send_guard(self):
        with (
            patch.object(compose_tools._server, "READ_ONLY", True),
            patch.object(compose_tools._server, "DRAFT_SAFE", False),
            patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run,
        ):
            result = compose_tools.manage_drafts(
                account="Work",
                action="send",
                draft_subject="Duplicate Subject",
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertEqual(payload["code"], "TARGET_SELECTOR_DEPRECATED")
        self.assertEqual(payload["remediation"]["exact_selector"], "draft_id")

    def test_send_draft_subject_returns_deprecation_before_draft_safe_send_guard(self):
        with (
            patch.object(compose_tools._server, "READ_ONLY", False),
            patch.object(compose_tools._server, "DRAFT_SAFE", True),
            patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run,
        ):
            result = compose_tools.manage_drafts(
                account="Work",
                action="send",
                draft_subject="Duplicate Subject",
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertEqual(payload["code"], "TARGET_SELECTOR_DEPRECATED")
        self.assertEqual(payload["remediation"]["exact_selector"], "draft_id")

    def test_open_and_delete_drafts_can_target_exact_draft_id(self):
        for action, expected_action in [("open", "open foundDraft"), ("delete", "delete foundDraft")]:
            with self.subTest(action=action):
                captured = []

                def fake_run(script, timeout=120, captured=captured):
                    captured.append(script)
                    return "ok"

                with patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    side_effect=fake_run,
                ):
                    compose_tools.manage_drafts(
                        account="Work",
                        action=action,
                        draft_id="84054",
                    )

                script = captured[0]
                self.assertIn("every message of draftsMailbox whose id is 84054", script)
                self.assertIn(expected_action, script)
                self.assertIn('set outputText to outputText & "Draft ID: " & draftId', script)
                self.assertNotIn("contains", script)

    def test_guarded_delete_revalidates_thread_subject_and_recipient_before_delete(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "ok"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            compose_tools.manage_drafts(
                account="Work",
                action="delete",
                draft_id="84054",
                expected_in_reply_to="<source@example.com>",
                expected_subject="Current subject",
                expected_to="recipient@example.com",
            )

        script = captured[0]
        self.assertIn('set expectedToAddresses to {"recipient@example.com"}', script)
        self.assertIn('if (subject of foundDraft as string) is not "Current subject"', script)
        self.assertIn('set expectedRfcToken to "<source@example.com>"', script)
        self.assertIn("currentInReplyTo does not contain expectedRfcToken", script)
        self.assertLess(script.index("if not deleteIdentityMatches"), script.index("delete foundDraft"))

    def test_guarded_delete_uses_bracketed_message_id_token_not_bare_substring(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "ok"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            compose_tools.manage_drafts(
                account="Work",
                action="delete",
                draft_id="84054",
                expected_in_reply_to="source@example.com",
                expected_subject="Current subject",
                expected_to="recipient@example.com",
            )

        script = captured[0]
        self.assertIn('set expectedRfcToken to "<source@example.com>"', script)
        self.assertNotIn('currentInReplyTo does not contain "source@example.com"', script)

    def test_guarded_delete_reads_headers_without_undefined_sanitizer(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "ok"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            compose_tools.manage_drafts(
                account="Work",
                action="delete",
                draft_id="84054",
                expected_in_reply_to="source@example.com",
                expected_subject="Current subject",
                expected_to="recipient@example.com",
            )

        script = captured[0]
        self.assertNotIn("my sanitize_field(", script)
        self.assertIn('set expectedRfcToken to "<source@example.com>"', script)

    def test_guarded_delete_returns_structured_drift_error_without_deleting(self):
        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            return_value="DRAFT_DELETE_IDENTITY_DRIFT|||84055",
        ):
            result = compose_tools.manage_drafts(
                account="Work",
                action="delete",
                draft_id="84054",
                expected_in_reply_to="<source@example.com>",
                expected_subject="Current subject",
                expected_to="recipient@example.com",
            )

        payload = json.loads(result)
        self.assertEqual(payload["code"], "DRAFT_DELETE_IDENTITY_DRIFT")
        self.assertEqual(payload["remediation"]["draft_id"], "84055")

    def test_guarded_delete_rejects_partial_identity_before_applescript(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.manage_drafts(
                account="Work",
                action="delete",
                draft_id="84054",
                expected_in_reply_to="<source@example.com>",
            )

        mock_run.assert_not_called()
        self.assertEqual(json.loads(result)["code"], "DRAFT_DELETE_IDENTITY_INCOMPLETE")

    def test_guarded_delete_rejects_explicitly_empty_identity_before_applescript(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.manage_drafts(
                account="Work",
                action="delete",
                draft_id="84054",
                expected_in_reply_to="",
                expected_subject="",
                expected_to="",
            )

        mock_run.assert_not_called()
        self.assertEqual(json.loads(result)["code"], "DRAFT_DELETE_IDENTITY_INCOMPLETE")

    def test_guarded_delete_rejects_empty_normalized_thread_header_before_applescript(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.manage_drafts(
                account="Work",
                action="delete",
                draft_id="84054",
                expected_in_reply_to="<>",
                expected_subject="Current subject",
                expected_to="recipient@example.com",
            )

        mock_run.assert_not_called()
        self.assertEqual(json.loads(result)["code"], "DRAFT_DELETE_IDENTITY_INCOMPLETE")

    def test_identity_guarded_delete_refuses_drifted_or_mismatched_draft_before_delete(self):
        captured: list[str] = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "IDENTITY_MISMATCH|||91062"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            result = compose_tools.delete_draft_if_identity_matches(
                account="Work",
                draft_id="91061",
                expected_subject="APPLE_MAIL_MCP_DRAFT_VERIFY_SMOKE_1_abcd",
                expected_to="smoke@example.invalid",
                expected_body_sentinel="APPLE_MAIL_MCP_BODY_SENTINEL_abcd",
            )

        payload = json.loads(result)
        self.assertFalse(payload["deleted"])
        self.assertEqual(payload["error"], "smoke_draft_identity_mismatch")
        self.assertEqual(payload["draft_id"], "91062")
        script = captured[0]
        self.assertIn('set expectedSubject to "APPLE_MAIL_MCP_DRAFT_VERIFY_SMOKE_1_abcd"', script)
        self.assertIn('set expectedBodySentinel to "APPLE_MAIL_MCP_BODY_SENTINEL_abcd"', script)
        self.assertIn('set expectedToAddresses to {"smoke@example.invalid"}', script)
        # FIX 1: the raw count-equality gate is gone. manage.py adds one
        # `to recipient` per comma-split address without deduping, so an actual
        # draft can carry more recipients than the deduped expected literal for
        # an identical set; the count gate wrongly orphaned such drafts.
        self.assertNotIn("(count of actualToAddresses) is not (count of expectedToAddresses)", script)
        # Both mutual-containment directions remain (exact set equality, robust
        # to duplicates on either side).
        self.assertIn("repeat with expectedToAddress in expectedToAddresses", script)
        self.assertIn("repeat with actualToAddress in actualToAddresses", script)
        self.assertLess(script.index("if cleanupIdentityMatches then"), script.index("delete foundDraft"))
        self.assertLess(script.index("delete foundDraft"), script.index("set remainingDrafts to every message"))
        self.assertIn("repeat with readbackAttempt from 1 to 3", script)
        self.assertIn("if (count of remainingDrafts) is 0 then", script)
        self.assertLess(
            script.index("if (count of remainingDrafts) is 0 then"),
            script.index('return "DELETED_CONFIRMED|||" & currentDraftId'),
        )
        self.assertLess(
            script.index('return "DELETED_CONFIRMED|||" & currentDraftId'),
            script.index('return "DELETE_UNCONFIRMED|||" & currentDraftId'),
        )
        self.assertNotIn("cleanupReadbackConfirmed", script)
        self.assertNotIn("subject of foundDraft &", script)

    def test_identity_guarded_delete_dedupes_expected_recipients_without_count_gate(self):
        captured: list[str] = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "DELETED_CONFIRMED|||91061"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            result = compose_tools.delete_draft_if_identity_matches(
                account="Work",
                draft_id="91061",
                expected_subject="APPLE_MAIL_MCP_DRAFT_VERIFY_SMOKE_1_abcd",
                expected_to="smoke@example.invalid, SMOKE@example.invalid",
                expected_body_sentinel="APPLE_MAIL_MCP_BODY_SENTINEL_abcd",
            )

        payload = json.loads(result)
        self.assertTrue(payload["deleted"])
        self.assertTrue(payload["confirmed"])
        script = captured[0]
        # Casefolded, order-preserving dedupe collapses the duplicate address to
        # a single-element literal.
        self.assertIn('set expectedToAddresses to {"smoke@example.invalid"}', script)
        self.assertNotIn("(count of actualToAddresses) is not (count of expectedToAddresses)", script)

    def test_identity_guarded_delete_fails_closed_when_exact_id_remains_after_delete(self):
        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            return_value="DELETE_UNCONFIRMED|||91061",
        ):
            result = compose_tools.delete_draft_if_identity_matches(
                account="Work",
                draft_id="91061",
                expected_subject="APPLE_MAIL_MCP_DRAFT_VERIFY_SMOKE_1_abcd",
                expected_to="smoke@example.invalid",
                expected_body_sentinel="APPLE_MAIL_MCP_BODY_SENTINEL_abcd",
            )

        payload = json.loads(result)
        self.assertFalse(payload["deleted"])
        self.assertFalse(payload["confirmed"])
        self.assertTrue(payload["delete_issued"])
        self.assertEqual(payload["draft_id"], "91061")
        self.assertEqual(payload["error"], "smoke_draft_cleanup_unconfirmed")

    def test_expected_recipient_literal_dedupes_case_insensitively(self):
        from apple_mail_mcp.tools.compose import cleanup

        literal = cleanup._expected_recipient_literal("smoke@example.invalid, SMOKE@example.invalid")
        self.assertEqual(literal, '{"smoke@example.invalid"}')
        # Distinct addresses are preserved in order, both casefolded.
        two = cleanup._expected_recipient_literal("Beta@Example.com, alpha@example.com")
        self.assertEqual(two, '{"beta@example.com", "alpha@example.com"}')
        self.assertIsNone(cleanup._expected_recipient_literal("  ,  "))

    def test_identity_guarded_delete_reports_account_resolution_failure_as_json(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.delete_draft_if_identity_matches(
                account="Missing",
                draft_id="91061",
                expected_subject="APPLE_MAIL_MCP_DRAFT_VERIFY_SMOKE_1_abcd",
                expected_to="smoke@example.invalid",
                expected_body_sentinel="APPLE_MAIL_MCP_BODY_SENTINEL_abcd",
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertFalse(payload["deleted"])
        self.assertEqual(payload["error"], "account_resolution_failed")
        self.assertIn("account_not_found", payload["detail"])
        # FIX 3: the smoke path's error detection still flags this consistent
        # JSON shape as a cleanup error and never reports the cleanup confirmed.
        from apple_mail_mcp.cli.draft_smoke import _draft_cleanup_confirmed
        from apple_mail_mcp.cli.formatting import _result_is_error

        self.assertTrue(_result_is_error(result))
        self.assertFalse(_draft_cleanup_confirmed(result))

    def test_identity_guarded_delete_script_builder_is_discovered_and_compiles(self):
        import importlib.util

        from apple_mail_mcp.tools.compose import cleanup

        # Load the shared osacompile discovery/parse harness by path so this
        # assertion uses the suite's own collection mechanism regardless of how
        # pytest names the test packages.
        compile_path = Path(__file__).resolve().parents[1] / "cross_cutting" / "test_applescript_builders_compile.py"
        spec = importlib.util.spec_from_file_location("_amm_delete_builder_compile_probe", compile_path)
        assert spec is not None and spec.loader is not None
        compile_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(compile_mod)

        builders = dict(compile_mod._collect_full_script_builders(cleanup))
        # The extracted builder conforms to the osacompile discovery contract
        # (name ends in `_script`, output starts with `tell application "Mail"`,
        # callable with sample kwargs) so the parse gate covers it.
        self.assertIn("delete_draft_if_identity_matches_script", builders)

        if not compile_mod._OSACOMPILE_AVAILABLE:
            self.skipTest("osacompile not available on this platform")
        script = cleanup.delete_draft_if_identity_matches_script()
        ok, err = compile_mod._osacompile_check(script)
        self.assertTrue(ok, err)

    def test_invalid_draft_id_is_rejected_before_applescript(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.manage_drafts(
                account="Work",
                action="delete",
                draft_id="not-a-number",
            )

        mock_run.assert_not_called()
        self.assertIn("'draft_id' must be a numeric", result)


class ManageDraftsListTests(unittest.TestCase):
    def test_subject_filter_builder_escapes_input_and_keeps_in_loop_filter(self):
        script = compose_tools._build_manage_drafts_subject_filter_script('Q3 "Report"', indent=4)

        self.assertIn("ignoring case", script)
        self.assertIn('does not contain "Q3 \\"Report\\""', script)
        self.assertIn("set skipThisDraft to true", script)
        self.assertNotIn("whose", script)

    def test_subject_filter_builder_omits_filter_when_unset(self):
        self.assertEqual(compose_tools._build_manage_drafts_subject_filter_script(None, indent=4), "")

    def test_list_builder_uses_clamped_limit_and_no_unbounded_enumeration(self):
        script = compose_tools._build_manage_drafts_list_script(
            safe_account="Work",
            list_limit=10,
            hide_empty=True,
            subject_contains="Q3",
        )

        self.assertIn("set hideEmpty to true", script)
        self.assertIn("if headEnd > 10 then set headEnd to 10", script)
        self.assertIn("if totalDrafts is 0 then", script)
        self.assertIn("messages 1 thru headEnd of draftsMailbox", script)
        self.assertIn("if shownCount >= 10 then exit repeat", script)
        self.assertIn('does not contain "Q3"', script)
        self.assertNotIn("every message of draftsMailbox", script)
        self.assertNotIn("current date", script)

    def test_find_builder_uses_bounded_header_scan(self):
        script = compose_tools._build_manage_drafts_find_script(
            safe_account="Work",
            list_limit=12,
            in_reply_to="<source@example.com>",
            subject_contains="Q3",
        )

        self.assertIn("if headEnd > 12 then set headEnd to 12", script)
        self.assertIn("if totalDrafts is 0 then", script)
        self.assertIn("messages 1 thru headEnd of draftsMailbox", script)
        self.assertIn("all headers of aDraft", script)
        self.assertIn('starts with "In-Reply-To:"', script)
        self.assertIn('starts with "References:"', script)
        self.assertIn('contains "source@example.com"', script)
        self.assertNotIn("every message of draftsMailbox", script)

    def test_verify_draft_returns_snapshot_json_with_expectation_warnings(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return (
                "FOUND|||Re: Test|||sender@example.com|||cc@example.com|||"
                "|||"
                "Hi there On Today, Sender wrote: Original|||<source@example.com>|||"
                "<source@example.com> <older@example.com>|||true|||false|||support.pdf::2048;;"
            )

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.verify_draft(
                account="Work",
                draft_id="84053",
                expected_to="sender@example.com",
                expected_cc="cc@example.com",
                expected_subject="Re: Test",
                expected_body_contains="Hi there",
                expected_attachments="support.pdf,missing.docx",
                expected_signature=True,
                require_quoted_original=True,
            )

        payload = json.loads(result)
        self.assertTrue(payload["found"])
        self.assertEqual(payload["draft_id"], "84053")
        self.assertEqual(payload["attachments"]["status"], "missing")
        self.assertEqual(payload["attachments"]["found"][0]["filename"], "support.pdf")
        self.assertEqual(payload["threading"]["in_reply_to"], "<source@example.com>")
        self.assertIn("expected_attachments_missing", payload["warnings"])
        self.assertIn("signature_missing", payload["warnings"])
        script = captured[0]
        self.assertIn('mailbox "Drafts" of targetAccount', script)
        self.assertIn("every message of draftsMailbox whose id is 84053", script)
        self.assertIn("all headers of aDraft", script)
        self.assertIn("mail attachments of aDraft", script)

    def test_verify_draft_rejects_non_numeric_draft_id(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.verify_draft(account="Work", draft_id="abc")

        mock_run.assert_not_called()
        self.assertIn("'draft_id' must be a numeric", result)

    def test_verify_draft_recipient_expectation_requires_exact_address(self):
        def fake_run(script, timeout=120):
            return "FOUND|||Subject|||joann@example.com|||||||||Body|||||||||false|||false|||"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.verify_draft(
                account="Work",
                draft_id="84053",
                expected_to="ann@example.com",
            )

        payload = json.loads(result)
        self.assertFalse(payload["checks"]["to_matches_expected"])
        self.assertIn("to_mismatch", payload["warnings"])

    def test_verify_drafts_preserves_order_and_reports_missing_invalid_ids(self):
        calls: list[dict[str, object]] = []

        def fake_verify(**kwargs):
            calls.append(kwargs)
            draft_id = kwargs["draft_id"]
            found = draft_id != "303"
            return json.dumps(
                {
                    "draft_id": draft_id,
                    "found": found,
                    "warnings": [] if found else ["draft_not_found"],
                    "checks": {"body_contains_expected": kwargs["expected_body_contains"] == "hello"},
                }
            )

        with patch("apple_mail_mcp.tools.compose.verify_draft", side_effect=fake_verify):
            result = compose_tools.verify_drafts(
                account="Work",
                draft_ids=["101", "bad", "202", "101", "303"],
                expected_body_contains="hello",
                expected_signature=True,
            )

        payload = json.loads(result)
        self.assertEqual(payload["draft_ids"], ["101", "202", "303"])
        self.assertEqual(payload["invalid_ids"], ["bad"])
        self.assertEqual(payload["missing_ids"], ["303"])
        self.assertEqual(payload["found"], 2)
        self.assertEqual(payload["chunk_size"], 50)
        self.assertEqual([item["draft_id"] for item in payload["items"]], ["101", "202", "303"])
        self.assertEqual([call["draft_id"] for call in calls], ["101", "202", "303"])
        self.assertTrue(all(call["expected_body_contains"] == "hello" for call in calls))
        self.assertTrue(all(call["expected_signature"] is True for call in calls))

    def test_verify_drafts_rejects_non_numeric_draft_ids_without_calling_verifier(self):
        with patch("apple_mail_mcp.tools.compose.verify_draft") as mock_verify:
            result = compose_tools.verify_drafts(account="Work", draft_ids=["abc", ""])

        mock_verify.assert_not_called()
        self.assertIn("'draft_ids' must contain one or more numeric", result)

    def test_verify_drafts_handles_120_ids(self):
        calls: list[str] = []

        def fake_verify(**kwargs):
            draft_id = kwargs["draft_id"]
            calls.append(draft_id)
            return json.dumps({"draft_id": draft_id, "found": False, "warnings": ["draft_not_found"]})

        ids = [str(i) for i in range(1, 121)]
        with patch("apple_mail_mcp.tools.compose.verify_draft", side_effect=fake_verify):
            result = compose_tools.verify_drafts(account="Work", draft_ids=ids)

        payload = json.loads(result)
        self.assertEqual(payload["draft_ids"], ids)
        self.assertEqual(payload["found"], 0)
        self.assertEqual(payload["missing_ids"], ids)
        self.assertEqual(payload["chunk_size"], 50)
        self.assertEqual(len(calls), 120)
        self.assertEqual(calls[0], "1")
        self.assertEqual(calls[50], "51")
        self.assertEqual(calls[100], "101")

    def test_verify_draft_default_omits_source_key_and_never_calls_search(self):
        def fake_run(script, timeout=120):
            return "FOUND|||Subject|||to@example.com|||||||||Body|||<source@example.com>|||<source@example.com>|||false|||false|||"

        with (
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
            patch("apple_mail_mcp.tools.compose.verify_tools.search_emails") as mock_search,
        ):
            result = compose_tools.verify_draft(account="Work", draft_id="84053")

        payload = json.loads(result)
        self.assertTrue(payload["found"])
        self.assertNotIn("source", payload)
        mock_search.assert_not_called()

    def test_verify_drafts_default_omits_source_key_for_every_item(self):
        def fake_verify(**kwargs):
            return json.dumps({"draft_id": kwargs["draft_id"], "found": True, "warnings": []})

        with patch("apple_mail_mcp.tools.compose.verify_draft", side_effect=fake_verify) as mock_verify:
            result = compose_tools.verify_drafts(account="Work", draft_ids=["101"])

        payload = json.loads(result)
        self.assertNotIn("source", payload["items"][0])
        called_kwargs = mock_verify.call_args.kwargs
        self.assertIs(called_kwargs["resolve_source"], False)
        self.assertEqual(called_kwargs["resolve_recent_days"], 30.0)

    def test_verify_draft_resolve_source_true_attaches_resolved_source(self):
        search_calls = []

        async def fake_search_emails(**kwargs):
            search_calls.append(kwargs)
            return json.dumps(
                {
                    "items": [
                        {
                            "message_id": "9001",
                            "subject": "Original Question",
                            "sender": "Ann <ann@example.com>",
                            "received_date": "2026-07-01 10:00:00",
                            "mailbox": "INBOX",
                        }
                    ],
                    "returned": 1,
                }
            )

        def fake_run(script, timeout=120):
            return (
                "FOUND|||Re: Original Question|||ann@example.com|||||||||"
                "Reply body|||<source123@example.com>|||<source123@example.com>|||false|||false|||"
            )

        with (
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
            patch("apple_mail_mcp.tools.compose.verify_tools.search_emails", new=fake_search_emails),
        ):
            result = compose_tools.verify_draft(
                account="Work",
                draft_id="84053",
                resolve_source=True,
            )

        payload = json.loads(result)
        self.assertEqual(
            payload["source"],
            {
                "resolved": True,
                "message_id": "9001",
                "subject": "Original Question",
                "sender": "Ann <ann@example.com>",
                "mailbox": "INBOX",
                "received_at": "2026-07-01 10:00:00",
                "resolved_within_days": 30.0,
            },
        )
        self.assertEqual(len(search_calls), 1)
        self.assertEqual(search_calls[0]["account"], "Work")
        self.assertEqual(search_calls[0]["mailbox"], "INBOX")
        self.assertEqual(search_calls[0]["internet_message_id"], "<source123@example.com>")
        self.assertEqual(search_calls[0]["recent_days"], 30.0)
        self.assertEqual(search_calls[0]["output_format"], "json")
        self.assertEqual(search_calls[0]["max_results"], 1)

    def test_verify_draft_resolve_source_true_reports_not_found_in_window(self):
        async def fake_search_emails(**kwargs):
            return json.dumps({"items": [], "returned": 0})

        def fake_run(script, timeout=120):
            return (
                "FOUND|||Re: Old Thread|||someone@example.com|||||||||"
                "Reply|||<old-source@example.com>|||<old-source@example.com>|||false|||false|||"
            )

        with (
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
            patch("apple_mail_mcp.tools.compose.verify_tools.search_emails", new=fake_search_emails),
        ):
            result = compose_tools.verify_draft(
                account="Work",
                draft_id="84054",
                resolve_source=True,
                resolve_recent_days=10.0,
            )

        payload = json.loads(result)
        self.assertEqual(
            payload["source"],
            {"resolved": False, "reason": "not_found_in_window", "resolved_within_days": 10.0},
        )

    def test_verify_draft_resolve_source_true_with_no_header_never_calls_search(self):
        def fake_run(script, timeout=120):
            return "FOUND|||Standalone Draft|||someone@example.com|||||||||Body|||||||||false|||false|||"

        with (
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
            patch("apple_mail_mcp.tools.compose.verify_tools.search_emails") as mock_search,
        ):
            result = compose_tools.verify_draft(
                account="Work",
                draft_id="84055",
                resolve_source=True,
            )

        payload = json.loads(result)
        self.assertEqual(payload["source"], {"resolved": False, "reason": "no_in_reply_to_header"})
        mock_search.assert_not_called()

    def test_verify_drafts_resolve_source_true_propagates_to_each_draft(self):
        calls = []

        def fake_verify(**kwargs):
            calls.append(kwargs)
            return json.dumps(
                {
                    "draft_id": kwargs["draft_id"],
                    "found": True,
                    "warnings": [],
                    "source": {"resolved": False, "reason": "no_in_reply_to_header"},
                }
            )

        with patch("apple_mail_mcp.tools.compose.verify_draft", side_effect=fake_verify):
            result = compose_tools.verify_drafts(
                account="Work",
                draft_ids=["101", "202"],
                resolve_source=True,
                resolve_recent_days=5.0,
            )

        payload = json.loads(result)
        self.assertTrue(all(item["source"]["resolved"] is False for item in payload["items"]))
        self.assertTrue(all(call["resolve_source"] is True for call in calls))
        self.assertTrue(all(call["resolve_recent_days"] == 5.0 for call in calls))

    def test_manage_drafts_list_script_reads_date_received_not_date_sent(self):
        script = compose_tools._build_manage_drafts_list_script(
            safe_account="Work",
            list_limit=25,
            hide_empty=False,
            subject_contains=None,
        )

        self.assertIn("date received of aDraft", script)
        self.assertIn('set draftDate to "(unknown)"', script)
        self.assertNotIn("date sent of aDraft", script)
        self.assertNotIn('set draftDate to "(unsent)"', script)

    def test_list_uses_newest_first_slice(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "Found 0 draft(s)"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.manage_drafts(account="Work", action="list")

        self.assertEqual(len(captured), 1)
        script = captured[0]
        # Bounded newest-first slice: real Mail Drafts accounts show newly
        # created native replies near the front. Never scan the whole folder.
        self.assertIn("set totalDrafts to count of messages of draftsMailbox", script)
        self.assertIn("if headEnd > 75 then set headEnd to 75", script)
        self.assertIn("messages 1 thru headEnd of draftsMailbox", script)
        self.assertIn("if shownCount >= 75 then exit repeat", script)
        self.assertNotIn("messages startIdx thru totalDrafts of draftsMailbox", script)
        self.assertNotIn("every message of draftsMailbox", script)

    def test_list_limit_caps_head_window_and_result_count(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "Found 0 draft(s)"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.manage_drafts(account="Work", action="list", limit=10)

        script = captured[0]
        self.assertIn("if headEnd > 10 then set headEnd to 10", script)
        self.assertIn("if shownCount >= 10 then exit repeat", script)

    def test_list_subject_contains_adds_case_insensitive_filter_only(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "Found 0 draft(s)"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.manage_drafts(
                account="Work",
                action="list",
                subject_contains="Q3 Report",
            )

        self.assertEqual(len(captured), 1)
        script = captured[0]
        # In-loop, case-insensitive subject filter.
        self.assertIn("ignoring case", script)
        self.assertIn('does not contain "Q3 Report"', script)
        # No date filter is ever added (would drop null-date new drafts).
        self.assertNotIn("recentCutoffDate", script)
        self.assertNotIn("current date", script)

    def test_list_subject_contains_filters_before_body_and_recipient_reads(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "Found 0 draft(s)"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.manage_drafts(account="Work", action="list", subject_contains="Q3 Report")

        script = captured[0]
        _assert_ordered(
            self,
            script,
            'does not contain "Q3 Report"',
            'set draftBody to ""',
            'set draftTo to ""',
        )

    def test_list_without_subject_contains_omits_filter(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "Found 0 draft(s)"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.manage_drafts(account="Work", action="list")

        self.assertEqual(len(captured), 1)
        self.assertNotIn("ignoring case", captured[0])

    def test_find_by_in_reply_to_uses_bounded_header_scan(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "Found 1 matching draft(s)"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.manage_drafts(
                account="Work",
                action="find",
                in_reply_to="<source@example.com>",
                subject_contains="Q3",
                limit=12,
            )

        self.assertIn("Found 1", result)
        script = captured[0]
        self.assertIn("if headEnd > 12 then set headEnd to 12", script)
        self.assertIn("messages 1 thru headEnd of draftsMailbox", script)
        self.assertNotIn("every message of draftsMailbox", script)
        self.assertIn("all headers of aDraft", script)
        self.assertIn('starts with "In-Reply-To:"', script)
        self.assertIn('starts with "References:"', script)
        self.assertIn('contains "source@example.com"', script)


class ComposeRunApplescriptMigrationTests(unittest.TestCase):
    def test_reply_to_email_resolves_exact_id_from_selected_archive_mailbox(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "ok"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            compose_tools.reply_to_email(
                account="Work",
                message_id="888",
                mailbox="Archive",
                reply_body="Thanks",
            )

        script = captured[0]
        self.assertIn('mailbox "Archive" of targetAccount', script)
        self.assertIn("every message of sourceMailbox whose id is 888", script)
        self.assertNotIn("inboxMailbox", script)

    def test_reply_to_email_forwards_timeout_to_run_applescript(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["timeout"] = timeout
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="888",
                reply_body="Thanks",
                timeout=240,
            )

        self.assertEqual(captured["timeout"], 240)

    def test_send_html_email_uses_run_applescript(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            captured["timeout"] = timeout
            return "Email saved as draft (HTML)"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.compose_email(
                account="Work",
                to="team@example.com",
                subject="Hi",
                body="Plain",
                body_html="<p>Hi</p>",
                mode="draft",
                timeout=90,
            )

        self.assertIn("use framework", captured["script"])
        self.assertEqual(captured["timeout"], 90)
        # FIX #1(c): single persist — save newMsg then close the CORRECT window
        # (window of newMsg, not positional window 1) and no redundant keystroke.
        self.assertIn("save newMsg", captured["script"])
        self.assertIn("close (window of newMsg) saving no", captured["script"])
        self.assertNotIn("close window 1 saving no", captured["script"])
        self.assertIn("set index of (window of newMsg) to 1", captured["script"])
        self.assertIn("on focusComposeBody(theMarker)", captured["script"])
        self.assertNotIn("repeat 7 times", captured["script"])
        self.assertIn("headerRoles contains focusedRole", captured["script"])
        self.assertIn("key code 48", captured["script"])
        self.assertIn('perform action "AXFocus" of composeEditor', captured["script"])
        self.assertIn("click composeEditor", captured["script"])
        self.assertLess(
            captured["script"].index("headerRoles contains focusedRole"),
            captured["script"].index("key code 48"),
        )
        self.assertIn(
            'if not my focusComposeBody(temporarySubjectMarker) then error "COMPOSE_BODY_FOCUS_FAILED"',
            captured["script"],
        )
        self.assertIn("close (window of newMsg) saving no", captured["script"])
        # The clipboard goes back on the success path AND the error path. It is
        # restored by writing back every saved item, not just the text flavor:
        # reading only `stringForType:` returned missing value for a copied
        # image or file and skipped the restore altogether, destroying the copy.
        self.assertIn("pb's pasteboardItems()", captured["script"])
        self.assertGreater(captured["script"].count("pb's writeObjects:savedPasteboardItems"), 1)
        self.assertNotIn('keystroke "s" using command down', captured["script"])
        self.assertNotIn("close window 1 saving yes", captured["script"])
        self.assertIn("Email saved as draft (HTML)", result)
        self.assertLess(
            captured["script"].index('keystroke "v" using command down'),
            captured["script"].index('set subject of newMsg to "Hi"'),
        )
        self.assertLess(
            captured["script"].index('set subject of newMsg to "Hi"'),
            captured["script"].index("save newMsg"),
        )
        self.assertNotIn("set subject of newMsg to temporarySubjectMarker", captured["script"])

    def test_send_html_email_open_mode_saves_before_leaving_open(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "Email opened in Mail for review (HTML). Edit and send when ready."

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools._send_html_email(
                account="Work",
                to="team@example.com",
                subject="Hi",
                body_plain="Plain",
                body_html="<p>Hi</p>",
                mode="open",
            )

        self.assertIn("save newMsg", captured["script"])
        self.assertLess(
            captured["script"].index('set subject of newMsg to "Hi"'),
            captured["script"].index("save newMsg"),
        )
        self.assertNotIn(
            "close (window of newMsg) saving no",
            captured["script"].split("on error errMsg")[0],
        )
        self.assertNotIn('keystroke "s" using command down', captured["script"])
        self.assertNotIn("close window 1 saving yes", captured["script"])
        self.assertIn("review", result)

    def test_send_html_email_send_mode_uses_mail_object_model_send(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "Email sent successfully (HTML)"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools._send_html_email(
                account="Work",
                to="team@example.com",
                subject="Hi",
                body_plain="Plain",
                body_html="<p>Hi</p>",
                mode="send",
            )

        self.assertIn("send newMsg", captured["script"])
        self.assertLess(
            captured["script"].index('set subject of newMsg to "Hi"'),
            captured["script"].index("send newMsg"),
        )
        self.assertNotIn(
            'if restoredOutgoingSubject contains "__apple_mail_mcp_" then error "HTML_COMPOSE_SUBJECT_RESTORE_FAILED"',
            captured["script"],
        )
        self.assertIn(
            'if restoredOutgoingSubject is not "Hi" then error "HTML_COMPOSE_SUBJECT_RESTORE_FAILED"',
            captured["script"],
        )
        self.assertNotIn('keystroke "d" using {command down, shift down}', captured["script"])
        self.assertIn("Email sent successfully (HTML)", result)

    def test_forward_with_message_uses_run_applescript(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Forward saved"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="recipient@example.com",
                message="Please review",
            )

        self.assertEqual(len(captured), 1)
        script = captured[0]
        # Forward with a lead message now uses the race-free object model: the
        # message is read from a temp file and prepended as plain text — no
        # NSPasteboard/use framework clipboard injection.
        self.assertIn("make new outgoing message", script)
        self.assertIn("set fwdLeadText to", script)
        self.assertNotIn("use framework", script)
        self.assertNotIn("NSPasteboard", script)

    def test_split_addresses_dedup_filters_empty_segments(self):
        self.assertEqual(
            compose_tools._split_addresses("a@x.com, , b@y.com"),
            ["a@x.com", "b@y.com"],
        )
        self.assertEqual(compose_tools._split_addresses(""), [])
        self.assertEqual(compose_tools._split_addresses(None), [])

    def test_build_recipient_loops_message_var_and_addresses(self):
        cc_script, bcc_script, cc_addrs, bcc_addrs = compose_tools._build_recipient_loops(
            "a@x.com, b@y.com",
            "c@z.com",
            message_var="replyMessage",
        )
        self.assertEqual(cc_addrs, ["a@x.com", "b@y.com"])
        self.assertEqual(bcc_addrs, ["c@z.com"])
        self.assertIn(
            "make new cc recipient at end of cc recipients of replyMessage",
            cc_script,
        )
        self.assertIn('address:"a@x.com"', cc_script)
        self.assertIn(
            "make new bcc recipient at end of bcc recipients of replyMessage",
            bcc_script,
        )
        self.assertIn('address:"c@z.com"', bcc_script)

    def test_build_recipient_loops_compact_empty(self):
        cc_script, bcc_script, cc_addrs, bcc_addrs = compose_tools._build_recipient_loops(None, "", compact=True)
        self.assertEqual(cc_addrs, [])
        self.assertEqual(bcc_addrs, [])
        self.assertEqual(cc_script, "")
        self.assertEqual(bcc_script, "")
        cc_script, _, _, _ = compose_tools._build_recipient_loops("one@example.com", None, compact=True)
        self.assertEqual(
            cc_script,
            'make new cc recipient at end of cc recipients with properties {address:"one@example.com"}\n',
        )


class NativeReplyEffectiveTimeoutTests(unittest.TestCase):
    """AGENTIC-1214 defect 1: the timeout projection must include per-chunk
    focus-recheck + keystroke overhead, not just the inter-chunk delay."""

    def test_ten_thousand_char_body_projects_beyond_the_floor(self):
        reply_body = "a" * 10_000
        chunk_count = -(-len(reply_body) // compose_constants.TYPING_CHUNK_SIZE)
        expected_projected_seconds = chunk_count * (
            compose_constants.TYPING_INTER_CHUNK_DELAY + compose_constants.TYPING_PER_CHUNK_OVERHEAD_SECONDS
        )
        # Typing has two phases and the projection covers both: posting the
        # keystroke events, then waiting for the WebKit editor to drain them.
        # On a body this long the drain is the larger term.
        expected_projected_seconds += (
            compose_constants.typing_settle_attempts(len(reply_body)) * compose_constants.TYPING_SETTLE_DELAY
        )
        expected_timeout = max(
            120,
            int(
                expected_projected_seconds
                + reply_runner._NATIVE_TYPING_FIXED_OVERHEAD_SECONDS
                + reply_runner._NATIVE_TYPING_SLACK_SECONDS
            ),
        )

        effective_timeout, timeout_error = reply_runner._native_reply_effective_timeout(reply_body, None)

        self.assertIsNone(timeout_error)
        self.assertEqual(effective_timeout, expected_timeout)
        self.assertGreater(effective_timeout, 120)

    def test_body_above_projected_cap_is_still_refused(self):
        # Above the documented cap, the tool must refuse up front rather than
        # hand out a timeout that could still be exceeded mid-typing. Derive a
        # body length that exceeds the cap under the new (larger) per-chunk
        # cost instead of hardcoding a magic character count.
        per_chunk_cost = (
            compose_constants.TYPING_INTER_CHUNK_DELAY + compose_constants.TYPING_PER_CHUNK_OVERHEAD_SECONDS
        )
        chunks_to_exceed_cap = int(reply_runner._NATIVE_TYPING_MAX_PROJECTED_SECONDS // per_chunk_cost) + 2
        reply_body = "a" * (chunks_to_exceed_cap * compose_constants.TYPING_CHUNK_SIZE)

        effective_timeout, timeout_error = reply_runner._native_reply_effective_timeout(reply_body, None)

        self.assertIsNone(effective_timeout)
        self.assertIsNotNone(timeout_error)
        payload = json.loads(timeout_error)
        self.assertEqual(payload["code"], "REPLY_BODY_TYPING_BUDGET_EXCEEDED")
        self.assertGreater(
            payload["remediation"]["projected_typing_seconds"],
            reply_runner._NATIVE_TYPING_MAX_PROJECTED_SECONDS,
        )

    def test_explicit_timeout_below_the_projection_is_floored_at_it(self):
        """An explicit timeout cannot shrink a drain the script will spend anyway.

        ``timeout`` is a public ``reply_to_email`` parameter, so any agent or CLI
        caller can set it. The AppleScript sizes its editor-drain budget from
        ``bodyLength`` on its own and never sees the granted timeout, so a value
        below the projection makes the two consumers of
        ``typing_settle_attempts`` disagree by construction: ``AppleScriptTimeout``
        fires mid-drain, the compose window is left open with the body typed and
        unsaved, and the caller is told to retry -- which types the same body into
        a SECOND window.
        """
        body = "a" * 10_000
        projected, _ = reply_runner._native_reply_effective_timeout(body, None)
        assert projected is not None

        effective_timeout, timeout_error = reply_runner._native_reply_effective_timeout(body, 45)

        self.assertIsNone(timeout_error)
        self.assertEqual(effective_timeout, projected)
        self.assertGreater(effective_timeout, 45)

    def test_explicit_timeout_above_the_projection_still_wins(self):
        """The floor raises a too-small budget; it does not replace the caller's."""
        body = "a" * 10_000
        projected, _ = reply_runner._native_reply_effective_timeout(body, None)
        assert projected is not None
        generous = projected + 600

        effective_timeout, timeout_error = reply_runner._native_reply_effective_timeout(body, generous)

        self.assertIsNone(timeout_error)
        self.assertEqual(effective_timeout, generous)

    def test_explicit_timeout_does_not_lift_the_refusal_cap(self):
        """The floor stays bounded only because the cap applies to this path too.

        Were the cap still skipped whenever ``timeout`` was passed, the floor
        would scale with an unrefused body all the way past ``run_applescript``'s
        own 3600s ``INVALID_TIMEOUT`` ceiling.
        """
        per_chunk_cost = (
            compose_constants.TYPING_INTER_CHUNK_DELAY + compose_constants.TYPING_PER_CHUNK_OVERHEAD_SECONDS
        )
        chunks_to_exceed_cap = int(reply_runner._NATIVE_TYPING_MAX_PROJECTED_SECONDS // per_chunk_cost) + 2
        reply_body = "a" * (chunks_to_exceed_cap * compose_constants.TYPING_CHUNK_SIZE)

        effective_timeout, timeout_error = reply_runner._native_reply_effective_timeout(reply_body, 3600)

        self.assertIsNone(effective_timeout)
        self.assertIsNotNone(timeout_error)
        payload = json.loads(timeout_error)
        self.assertEqual(payload["code"], "REPLY_BODY_TYPING_BUDGET_EXCEEDED")
        # And the refusal must not send the caller back into the timeout
        # parameter, which no longer lifts the cap.
        self.assertIn("shorten reply_body", payload["remediation"]["preferred"].lower())

    def test_every_admitted_body_is_floored_below_the_applescript_timeout_ceiling(self):
        """``run_applescript`` refuses a timeout over 3600s with ``INVALID_TIMEOUT``.

        The floor is derived, not passed in, so a caller cannot see it coming; it
        must never be able to manufacture a value the transport then rejects.
        """
        cap_ceiling = (
            reply_runner._NATIVE_TYPING_MAX_PROJECTED_SECONDS
            + reply_runner._NATIVE_TYPING_FIXED_OVERHEAD_SECONDS
            + reply_runner._NATIVE_TYPING_SLACK_SECONDS
        )
        self.assertLessEqual(cap_ceiling, 3600)


class NativeReplyTimeoutCalibrationTests(unittest.TestCase):
    """The granted timeout must exceed the *measured* typing duration.

    The tests above derive their expected value from the same constants the
    code under test uses, so they stay green however wrong those constants
    are. These compare against a live measurement hardcoded here instead, so a
    mis-calibration fails rather than agreeing with itself.

    Fitted on Darwin 25.5, 2026-08-24 over four **successful** live draft
    replies at 1, 1, 21, and 39 chunks: 0.70s per chunk on a 34.2s fixed
    overhead, R2 0.98.

    Both of those numbers are now known to be inflated, and the class keeps
    them anyway. They were fitted BEFORE the editor-drain poll existed, when
    the script went straight from the last keystroke to ``save`` and every run
    carried an unmeasured amount of undrained-typing time. The later chunk-size
    sweep in ``constants.py`` (2026-08-25) has complete 2,400-character runs
    finishing in 21.3-28.9s end to end -- below the 34.2s "fixed overhead"
    alone, which cannot both be true. Refitting that sweep gives roughly
    0.48s per chunk on a ~19s intercept. Over-projecting is the safe direction
    for a timeout, so the older, larger numbers stay as the yardstick.

    THE YARDSTICK MUST GAIN A TERM WHEN THE PROJECTION DOES. A one-phase model
    (chunks only) stopped describing a run the moment typing became two phases:
    at the refusal cap the drain budget alone is 100s, and a chunks-only
    duration would invent ~245s of margin that no longer exists. So
    ``_measured_duration`` carries the drain explicitly. The claim these tests
    make -- every body the cap admits is one the granted timeout can outlive --
    is only checked if the yardstick models the same phases the script runs.

    Successful runs only, deliberately. A reply that fails verification also
    burns a 20-attempt fallback poll at 1s per attempt that the success path
    never runs -- a 21-chunk success measured 47.6s against an 80.0s 20-chunk
    failure. Fitting a line through a failed run charges that poll to typing
    and triples the apparent per-chunk slope.
    """

    MEASURED_SECONDS_PER_CHUNK = 0.70
    MEASURED_FIXED_OVERHEAD_SECONDS = 34.2
    # The 2026-08-25 sweep's own refit of the same intercept, and the fastest
    # COMPLETE 2,400-character run it recorded. The second number is what
    # refutes the first constant above: a whole run cannot finish faster than
    # the fixed overhead it supposedly pays.
    REFITTED_FIXED_OVERHEAD_SECONDS = 19.1
    SWEEP_FASTEST_COMPLETE_2400_CHAR_RUN_SECONDS = 21.3

    def _measured_duration(self, body_length: int) -> float:
        """Conservative model of a real run: chunk posting + drain + overhead.

        The drain term is the FULL poll budget, not an expected value. The poll
        exits early whenever the tail match or the length-delta fires, so this
        overstates a healthy run -- which is the correct direction for a
        yardstick a timeout has to clear.
        """
        chunk_count = -(-body_length // compose_constants.TYPING_CHUNK_SIZE) if body_length else 0
        drain_seconds = compose_constants.typing_settle_attempts(body_length) * compose_constants.TYPING_SETTLE_DELAY
        return (
            chunk_count * self.MEASURED_SECONDS_PER_CHUNK + drain_seconds + self.MEASURED_FIXED_OVERHEAD_SECONDS
        )

    def test_granted_timeout_exceeds_measured_duration_at_every_admissible_length(self):
        # Walk the range rather than spot-checking: a projection can be ahead
        # of real duration at short and capped lengths and still cross over in
        # the middle, and the middle is where ordinary replies live. The last
        # two entries sit above the ~6,259-character point where the drain
        # budget saturates, which is the region the projection covers by
        # extrapolation rather than measurement.
        for body_length in (0, 1, 57, 500, 1599, 3040, 5000, 8000, 20_000, 38_000, 114_000):
            with self.subTest(body_length=body_length):
                granted, error = reply_runner._native_reply_effective_timeout("a" * body_length, None)
                if error is not None:
                    continue  # refused up front, so there is no timeout to outlive
                assert granted is not None
                self.assertGreater(
                    granted,
                    self._measured_duration(body_length),
                    f"{body_length}-char reply is granted {granted}s for a run measured at "
                    f"~{self._measured_duration(body_length):.0f}s; AppleScriptTimeout would "
                    "fire mid-typing and strand a partially typed compose window",
                )

    def test_fixed_overhead_constant_is_conservative_not_a_frozen_measurement(self):
        """The constant is a ceiling. It must not become a floor under 34.2s.

        This test used to assert ``>= 34.2``, which turned a refuted measurement
        into an enforced minimum: the next person to retune the constant would
        have had to delete a test claiming to encode a measurement in order to
        lower it. The 34.2s fit came from the pre-drain regime, and the sweep in
        ``constants.py`` contradicts it directly -- complete 2,400-character runs
        finished in 21.3s, which is impossible if 34.2s of that is fixed
        overhead.

        What actually has to hold is conservatism: the constant covers the real
        (refitted) overhead with room for host variance, and does not run away
        into a margin so large the timeout stops bounding anything. Over-
        projection is safe, but the timeout is also how long a wedged compose
        window holds the Mail lock before anyone hears about it.
        """
        self.assertLess(
            self.SWEEP_FASTEST_COMPLETE_2400_CHAR_RUN_SECONDS,
            self.MEASURED_FIXED_OVERHEAD_SECONDS,
            "the superseded 34.2s fit is only refuted while a complete run measures faster than it",
        )
        self.assertGreaterEqual(
            reply_runner._NATIVE_TYPING_FIXED_OVERHEAD_SECONDS,
            self.REFITTED_FIXED_OVERHEAD_SECONDS,
        )
        self.assertLessEqual(
            reply_runner._NATIVE_TYPING_FIXED_OVERHEAD_SECONDS,
            self.REFITTED_FIXED_OVERHEAD_SECONDS * 3,
        )

    def test_per_chunk_projection_is_conservative_but_not_absurd(self):
        # Over-projecting is the safe direction; it is not a free one. The
        # timeout is also how long a wedged compose window holds the Mail lock
        # before anyone hears about it, so keep the margin within 3x.
        projected_per_chunk = (
            compose_constants.TYPING_INTER_CHUNK_DELAY + compose_constants.TYPING_PER_CHUNK_OVERHEAD_SECONDS
        )
        self.assertGreater(projected_per_chunk, self.MEASURED_SECONDS_PER_CHUNK)
        self.assertLess(projected_per_chunk, self.MEASURED_SECONDS_PER_CHUNK * 3)

    def test_refusal_cap_admits_no_body_it_cannot_finish(self):
        # The cap and the projection have to agree: every body the cap admits
        # must also be one the granted timeout can outlive.
        #
        # The boundary is found by ASKING the real function rather than
        # re-deriving the formula here. The projection has two terms now (chunk
        # posting plus editor drain) and the drain term saturates, so a boundary
        # solved from the chunk term alone lands on the refused side -- which is
        # exactly how this test failed when the drain term was added. A search
        # cannot go stale the next time the formula gains a term.
        low, high = 1, 1_000_000
        while low < high:
            mid = (low + high + 1) // 2
            _, mid_error = reply_runner._native_reply_effective_timeout("a" * mid, None)
            if mid_error is None:
                low = mid
            else:
                high = mid - 1
        body_length = low

        granted, error = reply_runner._native_reply_effective_timeout("a" * body_length, None)

        self.assertIsNone(error, "body at the cap boundary should be admitted, not refused")
        assert granted is not None
        self.assertGreater(granted, self._measured_duration(body_length))

        # And one character past it is refused, so the boundary is real.
        _, past_error = reply_runner._native_reply_effective_timeout("a" * (body_length + 1), None)
        self.assertIsNotNone(past_error, "one character past the boundary should be refused")


if __name__ == "__main__":
    unittest.main()
