"""Cross-surface attachment readiness contract for standalone composition."""

from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp.tools import compose as compose_tools
from apple_mail_mcp.tools.compose.attachment_draft_verification import (
    verify_standalone_attachment_readiness,
)
from apple_mail_mcp.tools.compose.constants import DRAFT_LIST_CAP
from apple_mail_mcp.tools.compose.standalone_draft_identity_scripts import (
    _standalone_draft_identity_handlers,
    standalone_draft_identity_resolver_script,
    standalone_marker_draft_finalize_script,
)


def _strict_attachment_readback(draft_id: str, filename: str) -> str:
    return (
        '{"found": true, "draft_id": "'
        + draft_id
        + '", "subject_matches_expected": true, "body_contains_expected": true, '
        + '"checks": {"to_matches_expected": true, "cc_matches_expected": true}, "attachments": '
        + '{"status": "verified", "found": [{"filename": "'
        + filename
        + '", "size": 10}]}, "recipients": {"to": "", "cc": "", "bcc": ""}, "warnings": []}'
    )


def test_compose_attachment_draft_requires_immediate_body_readback(tmp_path: Path):
    """A transaction-scoped locator is insufficient without strict saved-body proof."""
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")

    with (
        patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            return_value="Email saved as draft (HTML)\nDraft ID: 84053\n",
        ),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
        patch(
            "apple_mail_mcp.tools.compose.verify_draft",
            return_value=_strict_attachment_readback("84053", attachment.name),
        ) as verify_draft,
    ):
        result = compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Report",
            body="Please review.",
            attachments=str(attachment),
        )

    assert "Attachment Verification Status: verified" in result
    assert "Attachment Proof Scope: immediate transaction-scoped Drafts readback" in result
    verify_draft.assert_called_once()


def test_compose_attachment_send_is_rejected_before_mail_is_called(tmp_path: Path):
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript") as run_mail,
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
    ):
        result = compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Report",
            body="Please review.",
            attachments=str(attachment),
            mode="send",
        )

    assert "ATTACHMENT_DRAFT_VERIFICATION_REQUIRED" in result
    run_mail.assert_not_called()


def test_compose_attachment_draft_without_transaction_locator_is_not_ready(tmp_path: Path):
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", return_value="✓ Email saved as draft!"),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
    ):
        result = compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Report",
            body="Please review.",
            attachments=str(attachment),
        )

    assert "DRAFT_ATTACHMENT_READBACK_ID_UNAVAILABLE" in result
    assert "not ready" in result.lower()


def test_compose_attachment_draft_accepts_only_a_strict_transaction_scoped_readback(tmp_path: Path):
    """A current numeric locator is usable only for this immediate strict check."""
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")

    with (
        patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            return_value="Email saved as draft (HTML)\nDraft ID: 84053\nDraft ID Source: numeric_snapshot\n",
        ),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
        patch(
            "apple_mail_mcp.tools.compose.verify_draft",
            return_value=_strict_attachment_readback("84053", attachment.name),
        ) as verify_draft,
    ):
        result = compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Report",
            body="Please review.",
            attachments=str(attachment),
        )

    assert "Attachment Verification Status: verified" in result
    verify_draft.assert_called_once()


def test_compose_attachment_draft_reports_transaction_scoped_strict_readback(tmp_path: Path):
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")
    draft_id = "84053"

    with (
        patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            return_value=f"✓ Email saved as draft!\nDraft ID: {draft_id}\n",
        ),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
        patch(
            "apple_mail_mcp.tools.compose.verify_draft",
            return_value=_strict_attachment_readback(draft_id, attachment.name),
        ) as verify_draft,
    ):
        result = compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Report",
            body="Please review.",
            attachments=str(attachment),
        )

    assert "Attachment Verification Status: verified" in result
    assert "Attachment Proof Scope: immediate transaction-scoped Drafts readback" in result
    assert "Draft Locator Stability: best-effort; not identity proof" in result
    verify_draft.assert_called_once()


def test_plain_attachment_draft_uses_focused_ui_writer_and_discards_only_its_window_on_focus_failure(
    tmp_path: Path,
):
    """iCloud attachment drafts must paste authored text before saving, never assign ``content`` directly."""
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")
    captured_scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        captured_scripts.append(script)
        return "Email saved as draft (HTML)\nDraft ID: 84053\n"

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
        patch(
            "apple_mail_mcp.tools.compose.verify_draft",
            return_value=_strict_attachment_readback("84053", attachment.name),
        ),
    ):
        result = compose_tools.compose_email(
            account="iCloud",
            to="self@example.com",
            subject="Attachment test",
            body="Plain authored body",
            attachments=str(attachment),
            mode="draft",
        )

    assert "Attachment Verification Status: verified" in result
    script = captured_scripts[0]
    assert "on focusComposeBody(theMarker)" in script
    assert 'if not my focusComposeBody(temporarySubjectMarker) then error "COMPOSE_BODY_FOCUS_FAILED"' in script
    assert "headerRoles contains focusedRole" in script
    assert "key code 48" in script
    assert 'perform action "AXFocus" of composeEditor' in script
    assert script.index('if focusedRole is "AXWebArea" or focusedRole is "AXTextArea" then return true') < script.index(
        "key code 48"
    )
    assert 'keystroke "v" using command down' in script
    assert script.index('keystroke "v" using command down') < script.index("save newMsg")
    assert "markerDraftProof(markedDraft" in script
    assert script.index("save newMsg") < script.index("set markedDrafts to")
    assert "set content of newMessage to" not in script
    assert "close (window of newMsg) saving no" in script
    assert "close window 1 saving no" not in script
    assert "set subject of newMsg to temporarySubjectMarker" not in script
    error_handler = script.split("on error errMsg", 1)[1]
    assert 'if errMsg contains "COMPOSE_BODY_FOCUS_FAILED"' in error_handler
    assert error_handler.index('if errMsg contains "COMPOSE_BODY_FOCUS_FAILED"') < error_handler.index("delete newMsg")
    assert 'if errSubject contains "__apple_mail_mcp_"' not in error_handler


def test_html_attachment_draft_resolves_the_persisted_drafts_id_after_saving(tmp_path: Path):
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")
    draft_id = "84053"
    captured_scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        captured_scripts.append(script)
        return f"Email saved as draft (HTML)\nDraft ID: {draft_id}\n"

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
        patch(
            "apple_mail_mcp.tools.compose.verify_draft",
            return_value=_strict_attachment_readback(draft_id, attachment.name),
        ),
    ):
        result = compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Report",
            body="Please review.",
            body_html="<p>Please review.</p>",
            attachments=str(attachment),
        )

    assert "Attachment Verification Status: verified" in result
    script = captured_scripts[0]
    assert "attachmentObjectProof(newMsg" not in script
    assert script.index("save newMsg") < script.index("set markedDrafts to")
    assert script.index('set subject of newMsg to "Report"') < script.index("save newMsg")
    assert "set subject of markedDraft to" not in script


def test_attachment_draft_keeps_the_final_subject_out_of_the_editor_before_paste(tmp_path: Path):
    """The requested subject must not create an identifiable partial draft before focus succeeds."""
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")
    scripts: list[str] = []

    with (
        patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=lambda script, timeout=120: (
                scripts.append(script) or "Email saved as draft (HTML)\nAttachment Transaction Proof: verified\n"
            ),
        ),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
    ):
        compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Final board materials",
            body="Please review.",
            attachments=str(attachment),
        )

    script = scripts[0]
    paste = script.index('keystroke "v" using command down')
    assert 'subject:"Final board materials"' not in script[:paste]
    assert script.index('set subject of newMsg to "Final board materials"') > paste


def test_attachment_draft_sets_the_final_subject_only_after_marker_resolution(tmp_path: Path):
    """The visible subject is applied only after an exact marker-based lookup."""
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")
    scripts: list[str] = []

    with (
        patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=lambda script, timeout=120: (
                scripts.append(script) or "Email saved as draft (HTML)\nAttachment Transaction Proof: verified\n"
            ),
        ),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
    ):
        compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Final board materials",
            body="Please review.",
            attachments=str(attachment),
        )

    script = scripts[0]
    assert script.index('set subject of newMsg to "Final board materials"') < script.index("save newMsg")
    assert script.index("save newMsg") < script.index("set markedDrafts to")
    assert '(subject of candidateDraft as string) is "Final board materials"' in script
    assert "set subject of markedDraft to" not in script


def test_attachment_draft_failure_cleanup_does_not_restamp_the_marker(tmp_path: Path):
    """A focus or paste failure must not re-stamp the marker subject onto the draft."""
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")
    scripts: list[str] = []

    with (
        patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=lambda script, timeout=120: (
                scripts.append(script) or "Email saved as draft (HTML)\nAttachment Transaction Proof: verified\n"
            ),
        ),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
    ):
        compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Final board materials",
            body="Please review.",
            attachments=str(attachment),
        )

    script = scripts[0]
    assert "set temporarySubjectMarker to" in script
    assert "set subject of newMsg to temporarySubjectMarker" not in script
    assert "if name of composeWindow contains temporarySubjectMarker then" not in script
    assert "close (window of newMsg) saving no" in script
    assert "close window 1 saving no" not in script
    assert "(subject of candidateDraft as string) is " in script
    assert "whose subject" not in script
    error_handler = script.split("on error errMsg", 1)[1]
    assert 'if errMsg contains "COMPOSE_BODY_FOCUS_FAILED"' in error_handler
    assert error_handler.index('if errMsg contains "COMPOSE_BODY_FOCUS_FAILED"') < error_handler.index("delete newMsg")
    assert 'if errSubject contains "__apple_mail_mcp_"' not in error_handler


def test_attachment_draft_failure_cleanup_prefers_exact_marker_restore(tmp_path: Path):
    """Error cleanup restores a unique leftover marker row instead of re-stamping it."""
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")
    scripts: list[str] = []

    with (
        patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=lambda script, timeout=120: (
                scripts.append(script) or "Email saved as draft (HTML)\nAttachment Transaction Proof: verified\n"
            ),
        ),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
    ):
        compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Final board materials",
            body="Please review.",
            attachments=str(attachment),
        )

    script = scripts[0]
    assert 'set markerSweepStatus to "cleared"' in script
    assert 'set subject of leftoverMsg to "Final board materials"' in script
    assert "set subject of markedDraft to" not in script
    assert "delete markedDraft" in script
    assert "if markerMatchCount is greater than 1 then" in script
    assert f"if draftCount is greater than {DRAFT_LIST_CAP} then exit repeat" not in script


def test_attachment_draft_focus_uses_bounded_axrole_iteration_not_invalid_ui_selector(tmp_path: Path):
    """System Events cannot filter ``entire contents`` with the invalid role selector."""
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")
    scripts: list[str] = []

    with (
        patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=lambda script, timeout=120: (
                scripts.append(script) or "Email saved as draft (HTML)\nAttachment Transaction Proof: verified\n"
            ),
        ),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
    ):
        compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Final board materials",
            body="Please review.",
            attachments=str(attachment),
        )

    script = scripts[0]
    assert 'every UI element of composeWindow whose role is "AXWebArea"' not in script
    assert "set allElements to entire contents of composeWindow" in script
    assert "repeat with candidateElement in allElements" in script
    assert 'value of attribute "AXFocusedUIElement"' in script
    assert 'value of attribute "AXRole" of focusedElement' in script


def test_attachment_draft_focus_binds_to_its_marker_not_the_front_window(tmp_path: Path):
    """Another Mail window must not steal the paste target during a compose operation."""
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")
    scripts: list[str] = []

    with (
        patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=lambda script, timeout=120: (
                scripts.append(script) or "Email saved as draft (HTML)\nAttachment Transaction Proof: verified\n"
            ),
        ),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
    ):
        compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Final board materials",
            body="Please review.",
            attachments=str(attachment),
        )

    script = scripts[0]
    assert "set composeWindow to front window" not in script
    assert "first window whose name contains theMarker" in script
    assert "focusComposeBody(temporarySubjectMarker)" in script


def test_attachment_draft_focus_never_tabs_into_the_editor(tmp_path: Path):
    """Tab in a focused WebKit body inserts first-line indent; click/AXFocus instead."""
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")
    scripts: list[str] = []

    with (
        patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=lambda script, timeout=120: (
                scripts.append(script) or "Email saved as draft (HTML)\nAttachment Transaction Proof: verified\n"
            ),
        ),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
    ):
        compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Final board materials",
            body="Please review.",
            attachments=str(attachment),
        )

    script = scripts[0]
    assert "headerRoles contains focusedRole" in script
    assert 'value of attribute "AXFocusedUIElement"' in script
    assert "key code 48" in script
    assert 'perform action "AXFocus" of composeEditor' in script
    assert script.index("headerRoles contains focusedRole") < script.index("key code 48")
    assert script.index("focusComposeBody(temporarySubjectMarker)") < script.index('keystroke "v" using command down')


def test_attachment_draft_resolves_one_locator_after_save(tmp_path: Path):
    """The strict verification locator is resolved only after Mail persists the draft."""
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")
    scripts: list[str] = []
    with (
        patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=lambda script, timeout=120: (
                scripts.append(script) or "Email saved as draft (HTML)\nDraft ID: 84053\n"
            ),
        ),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
    ):
        compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Report",
            body="Please review.",
            attachments=str(attachment),
        )
    script = scripts[0]
    assert script.index("save newMsg") < script.index("set markedDrafts to")


def test_windowless_reply_attachment_send_is_rejected_before_mail_is_called():
    with (
        patch("apple_mail_mcp.tools.compose.reply._resolve_account", return_value=("Work", None)),
        patch("apple_mail_mcp.tools.compose.reply._validate_from_address", return_value=(None, None)),
        patch("apple_mail_mcp.tools.compose.reply._validate_signature_name", return_value=None),
        patch("apple_mail_mcp.tools.compose.run_applescript") as run_mail,
    ):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            attachments="/Users/example/report.pdf",
            native_format=False,
            allow_windowless_fallback=True,
            mode="send",
        )

    assert "REPLY_SEND_REQUIRES_VERIFIED_DRAFT" in result
    run_mail.assert_not_called()


def test_compose_attachment_draft_does_not_expose_a_reusable_icloud_locator(tmp_path: Path):
    """An iCloud numeric Drafts ID can rewrite during finalization and is not returned as identity."""
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")
    captured_scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        captured_scripts.append(script)
        return "✓ Email saved as draft!\nDraft ID: 98392\nAttachment Transaction Proof: verified\n"

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
        patch(
            "apple_mail_mcp.tools.compose.verify_draft",
            return_value=_strict_attachment_readback("98392", attachment.name),
        ),
    ):
        result = compose_tools.compose_email(
            account="iCloud",
            to="self@example.com",
            subject="Attachment test",
            body="Please review.",
            attachments=str(attachment),
        )

    assert "Draft Locator: unavailable after iCloud ID rewrite" in result
    assert "Draft Locator Stability: not a reusable identity" in result
    assert "fullDraftRfcSnapshot" in captured_scripts[0]
    assert "operation_exact_subject" in captured_scripts[0]
    assert "set savedDraftId to id of newMessage as string" not in captured_scripts[0]


def test_standalone_draft_identity_falls_back_only_to_one_new_numeric_drafts_id_after_settlement():
    """iCloud drafts without RFC Message-IDs still require an exact, bounded identity."""
    handlers = _standalone_draft_identity_handlers()
    resolver = standalone_draft_identity_resolver_script()

    assert "set numericDraftIds to {}" in handlers
    assert "set numericCandidateIds to {}" in handlers
    assert 'set rfcMessageId to ""' in handlers
    assert "try\n                set candidateDraftId to (id of aDraft) as string\n            end try" in handlers
    assert "try\n                set rfcMessageId to message id of aDraft as string\n            end try" in handlers
    assert 'set candidateDraftId to ""' in handlers
    assert (
        "if my isNumericStandaloneDraftId(candidateDraftId) and beforeNumericDraftIds does not contain candidateDraftId then"
        in handlers
    )
    assert 'if afterCount is not (beforeCount + 1) or afterCount > draftCap then return {"", ""}' in handlers
    assert "if (count of numericCandidateIds) is 1 then" in handlers
    assert 'return {(item 1 of numericCandidateIds as string), "numeric_snapshot"}' in handlers
    # Settlement patience is spent lazily: probe first, then wait only while the
    # row is still missing. The 0.8/0.5/0.5 backoff keeps the same 1.8s deadline
    # and the same final probe as the old unconditional ``delay 0.8`` lead-in, so
    # an iCloud row that needs settling still gets every chance it had before --
    # but a local or Exchange save, where the row is already indexed when ``save``
    # returns, now pays nothing. Reintroducing a bare delay ahead of the loop is
    # the regression this guards.
    assert "set identityBackoff to {0.8, 0.5, 0.5}" in resolver
    assert "repeat with identityAttempt from 1 to 4" in resolver
    assert "if identityAttempt is less than 4 then delay (item identityAttempt of identityBackoff)" in resolver
    first_probe = resolver.index("set savedDraftIdentity to my persistedStandaloneDraftId")
    lead_in = [
        line.strip()
        for line in resolver[:first_probe].splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    assert not any(line.startswith("delay") for line in lead_in), (
        f"no settlement delay may run before the first probe, found: {lead_in}"
    )
    assert "set savedDraftIdSource to item 2 of savedDraftIdentity" in resolver


def test_attachment_marker_proof_binds_to_cc_and_bcc_recipients(tmp_path: Path):
    """Same-save proof must reject a draft whose Cc or Bcc was lost by Mail."""
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")
    scripts: list[str] = []

    with (
        patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=lambda script, timeout=120: (
                scripts.append(script) or "Email saved as draft (HTML)\nAttachment Transaction Proof: verified\n"
            ),
        ),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
    ):
        compose_tools.compose_email(
            account="Work",
            to="to@example.com",
            cc="cc@example.com",
            bcc="bcc@example.com",
            subject="Recipient proof",
            body="Please review.",
            attachments=str(attachment),
        )

    script = scripts[0]
    assert "cc recipients of draftMessage" in script
    assert "bcc recipients of draftMessage" in script
    assert '"cc@example.com"' in script
    assert '"bcc@example.com"' in script
    assert 'if storedSubject is expectedMarker then return "subject_mismatch"' in script
    assert 'if storedSubject is not expectedSubject then return "subject_mismatch"' in script


def test_marker_finalization_is_bounded_and_fails_closed_after_proof():
    """Proof runs after save; subject writes are not legal on the persisted Gmail draft."""
    script = standalone_marker_draft_finalize_script("Final subject", 'set attachmentTransactionProof to "verified"')

    assert 'set attachmentTransactionProof to "identity_unavailable"' in script
    assert "every message of draftsMailbox whose subject" not in script
    assert f"if draftCount is greater than {DRAFT_LIST_CAP} then exit repeat" not in script
    assert f"if headEnd > {DRAFT_LIST_CAP} then set headEnd to {DRAFT_LIST_CAP}" in script
    assert "set candidateMessages to messages 1 thru headEnd of draftsMailbox" in script
    assert "set end of markedDrafts to contents of candidateDraft" in script
    assert '(subject of candidateDraft as string) is "Final subject"' in script
    assert "set subject of markedDraft to" not in script
    assert "set subject of newMsg to" not in script
    assert "set refreshedDraftId to (id of markedDraft) as string" in script
    assert 'error "DRAFT_ATTACHMENT_PROOF_FAILED:' in script
    assert 'set attachmentTransactionProof to "finalization_failed"' in script
    assert "error errMsg" in script


def test_attachment_readback_rejects_lost_cc_or_bcc_recipients(tmp_path: Path):
    """The immediate-readback compatibility path also needs exact recipient sets."""
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")
    output = "Email saved as draft (HTML)\nDraft ID: 84053\n"
    readback = (
        '{"found": true, "draft_id": "84053", "subject_matches_expected": true, '
        '"body_contains_expected": true, "checks": {"to_matches_expected": true, '
        '"cc_matches_expected": true}, "recipients": {"to": "to@example.com", '
        '"cc": "wrong@example.com", "bcc": "wrong-bcc@example.com"}, "attachments": '
        '{"status": "verified", "found": [{"filename": "report.pdf", "size": 10}]}, '
        '"warnings": []}'
    )

    result = verify_standalone_attachment_readiness(
        output=output,
        account="Work",
        to="to@example.com",
        cc="cc@example.com",
        bcc="bcc@example.com",
        subject="Report",
        body="Please review.",
        attachment_paths=[str(attachment)],
        timeout=None,
        verify_draft=lambda **_: readback,
    )

    assert "DRAFT_ATTACHMENT_READBACK_FAILED" in result
