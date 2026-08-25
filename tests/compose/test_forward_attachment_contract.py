"""Draft-first attachment contract regressions for ``forward_email``."""

import json
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.tools import compose as compose_tools
from apple_mail_mcp.tools.compose.forward_attachment_scripts import forward_marker_finalize_script


@pytest.fixture
def attachment_file() -> Iterator[Path]:
    """Create an allowed local attachment path for a forward draft."""
    with TemporaryDirectory(prefix="apple-mail-forward-", dir=Path.home()) as directory:
        path = Path(directory) / "report.pdf"
        path.write_bytes(b"attachment bytes")
        yield path


def _saved_forward_output(draft_id: str | None = "84055") -> str:
    lines = [
        "SAVING FORWARD AS DRAFT",
        "",
        "Forward saved as draft.",
        "To: recipient@example.com",
        "Subject: Fwd: Test",
    ]
    if draft_id is not None:
        lines.append(f"Draft ID: {draft_id}")
        lines.append("Draft ID Scope: current Drafts locator; re-resolve after sync")
    return "\n".join(lines) + "\n"


def _verified_attachment_payload(path: Path, *, draft_id: str = "84055", size: int | None = 16) -> str:
    return json.dumps(
        {
            "draft_id": draft_id,
            "found": True,
            "warnings": [],
            "attachments": {
                "expected": [path.name],
                "found": [{"filename": path.name, "size": size}],
                "missing": [],
                "status": "verified",
            },
        }
    )


def test_forward_attachment_draft_uses_the_persisted_drafts_id_not_the_outgoing_object_id(
    attachment_file: Path,
) -> None:
    """The saved draft ID must survive providers that rewrite object IDs on save."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return _saved_forward_output(draft_id="98397")

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail),
        patch(
            "apple_mail_mcp.tools.compose.verify_draft",
            return_value=_verified_attachment_payload(attachment_file, draft_id="98397"),
        ),
    ):
        result = compose_tools.forward_email(
            account="Work",
            message_id="12345",
            to="recipient@example.com",
            attachments=str(attachment_file),
        )

    assert "Verified Draft ID: 98397" in result
    assert "Draft ID Scope: current Drafts locator; re-resolve after sync" in result
    assert "fullDraftRfcSnapshot" in scripts[0]
    assert "persistedStandaloneDraftId" in scripts[0]
    assert "set forwardDraftId to id of forwardMessage as string" not in scripts[0]


def test_forward_attachment_draft_allows_only_known_inline_signature_assets(attachment_file: Path) -> None:
    """A provider-inserted Outlook inline signature image does not mask attachment loss."""
    payload = json.loads(_verified_attachment_payload(attachment_file))
    payload["attachments"]["found"].append({"filename": "Outlook-signature.png", "size": 512})

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", return_value=_saved_forward_output()),
        patch("apple_mail_mcp.tools.compose.verify_draft", return_value=json.dumps(payload)),
    ):
        result = compose_tools.forward_email(
            account="Work",
            message_id="12345",
            to="recipient@example.com",
            attachments=str(attachment_file),
            include_signature=False,
        )

    assert "Attachment Verification Status: verified" in result


def test_forward_attachment_draft_rejects_unknown_extra_attachment(attachment_file: Path) -> None:
    """An unrecognized extra file must not be excused as a signature artifact."""
    payload = json.loads(_verified_attachment_payload(attachment_file))
    payload["attachments"]["found"].append({"filename": "unexpected.pdf", "size": 512})

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", return_value=_saved_forward_output()),
        patch("apple_mail_mcp.tools.compose.verify_draft", return_value=json.dumps(payload)),
    ):
        result = compose_tools.forward_email(
            account="Work",
            message_id="12345",
            to="recipient@example.com",
            attachments=str(attachment_file),
            include_signature=False,
        )

    assert result.startswith("Error: FORWARD_DRAFT_ATTACHMENT_VERIFICATION_FAILED")


def test_forward_attachment_draft_materializes_and_verifies_exact_saved_draft(attachment_file: Path) -> None:
    """An attachment-bearing forward is ready only after exact Drafts verification."""
    scripts: list[str] = []
    verify_calls: list[dict[str, object]] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return _saved_forward_output()

    def fake_verify(**kwargs: object) -> str:
        verify_calls.append(kwargs)
        return _verified_attachment_payload(attachment_file)

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail),
        patch("apple_mail_mcp.tools.compose.verify_draft", side_effect=fake_verify),
    ):
        result = compose_tools.forward_email(
            account="Work",
            message_id="12345",
            to="recipient@example.com",
            attachments=str(attachment_file),
        )

    assert "Verification Status: found" in result
    assert "Attachment Verification Status: verified" in result
    assert "make new attachment" in scripts[0]
    assert str(attachment_file) in scripts[0]
    assert verify_calls == [
        {
            "account": "Work",
            "draft_id": "84055",
            "expected_to": "recipient@example.com",
            "expected_subject": "Fwd: Test",
            "expected_body_contains": None,
            "expected_attachments": [str(attachment_file.resolve())],
            "expected_signature": None,
            "timeout": None,
        }
    ]


def test_forward_rejects_direct_send_when_attachments_are_requested(attachment_file: Path) -> None:
    """Attachment-bearing forwards must remain in a reviewed draft."""
    with patch("apple_mail_mcp.tools.compose.run_applescript") as run_applescript:
        result = compose_tools.forward_email(
            account="Work",
            message_id="12345",
            to="recipient@example.com",
            attachments=str(attachment_file),
            mode="send",
        )

    run_applescript.assert_not_called()
    assert "attachments" in result.lower()
    assert "mode='draft' or mode='open'" in result


def test_forward_attachment_draft_accepts_marker_proof_when_icloud_hides_numeric_id(attachment_file: Path) -> None:
    """A transaction-scoped proof remains sufficient when iCloud omits a numeric row ID."""
    with (
        patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            return_value=_saved_forward_output(draft_id=None) + "Forward Attachment Proof: verified\n",
        ),
        patch("apple_mail_mcp.tools.compose.verify_draft") as verify_draft,
    ):
        result = compose_tools.forward_email(
            account="Work",
            message_id="12345",
            to="recipient@example.com",
            attachments=str(attachment_file),
        )

    verify_draft.assert_not_called()
    assert "Attachment Verification Status: verified" in result
    assert "same-operation marker-bound persisted Drafts row" in result


def test_forward_attachment_draft_restores_outgoing_subject_before_save(attachment_file: Path) -> None:
    """The iCloud fallback certifies this forward after restoring the live outgoing subject."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return _saved_forward_output() + "Forward Attachment Proof: verified\n"

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail),
        patch("apple_mail_mcp.tools.compose.verify_draft") as verify_draft,
    ):
        result = compose_tools.forward_email(
            account="Work",
            message_id="12345",
            to="recipient@example.com",
            attachments=str(attachment_file),
        )

    verify_draft.assert_not_called()
    assert "Attachment Verification Status: verified" in result
    assert "on markerDraftProof(" in scripts[0]
    assert "on forwardMarkerDraftProof(" in scripts[0]
    assert "forwardMarkerInlineSignatureAsset" in scripts[0]
    restore = scripts[0].index("set subject of forwardMessage to fwdSubject")
    save = scripts[0].index("save forwardMessage")
    assert restore < save
    assert "set subject of markedForwardDraft to fwdSubject" not in scripts[0]
    assert "delete markedForwardDraft" in scripts[0]
    assert "delete forwardMessage" in scripts[0]
    assert "every message of draftsMailbox whose subject" not in scripts[0]
    assert "Forward Attachment Proof:" in scripts[0]
    assert (
        'if (count of leakedForwardMarkerDrafts) is greater than 0 then error "FORWARD_SUBJECT_RESTORE_FAILED"'
        in scripts[0]
    )


def test_forward_marker_lookup_failure_enters_exact_object_cleanup() -> None:
    """An unavailable marker must discard this forward instead of leaving a marker draft."""
    script = forward_marker_finalize_script("__forward_marker__", 'set forwardAttachmentProof to "verified"')

    proof_error = 'if forwardAttachmentProof is not "verified" then error "FORWARD_ATTACHMENT_PROOF_FAILED: " & forwardAttachmentProof'
    assert script.count(proof_error) == 2
    assert script.rindex(proof_error) < script.index("on error errMsg")
    assert "delete markedForwardDraft" in script
    assert "delete forwardMessage" in script


def test_subject_bind_excludes_rows_that_existed_before_the_save() -> None:
    """A draft that predates this forward can never become the unique subject match.

    ``Fwd: <subject>`` is exactly what a *previous* forward of the same message
    left in Drafts. While the row this call just saved is still unindexed
    (iCloud/Exchange lag), an unfiltered exact-subject scan sees only the OLD
    draft and binds it — then fails the attachment proof against a message the
    caller never named.
    """
    script = forward_marker_finalize_script("__forward_marker__", 'set forwardAttachmentProof to "verified"')

    seed = "set preSaveForwardDraftIds to item 3 of preSaveDraftSnapshot"
    assert seed in script
    assert "set excludedDraftIds to preSaveForwardDraftIds" in script
    # The subject test itself must be gated, not merely accompanied by the list.
    assert (
        "if candidateExistedBefore is false and (subject of candidateDraft as string) is fwdSubject then" in script
    )
    assert script.index(seed) < script.index("set excludedDraftIds to preSaveForwardDraftIds")


def test_error_path_releases_a_subject_bound_row_instead_of_deleting_it() -> None:
    """Subject equality is a locator, not proof of authorship — so never delete on it.

    ``operation_exact_subject`` means the row merely had the right subject. The
    cleanup handler used to ``delete markedForwardDraft`` unconditionally, which
    on a misbind destroys a draft the user wrote on the same thread while
    reporting only ``FORWARD_ATTACHMENT_PROOF_FAILED``.
    """
    script = forward_marker_finalize_script("__forward_marker__", 'set forwardAttachmentProof to "verified"')

    release = 'if savedDraftIdSource is "operation_exact_subject" then set markedForwardDraft to missing value'
    assert release in script
    handler = script.index("on error errMsg")
    assert handler < script.index(release) < script.index("delete markedForwardDraft", handler)


@pytest.mark.skipif(shutil.which("osacompile") is None, reason="osacompile is not available")
def test_forward_attachment_marker_fallback_applescript_compiles(attachment_file: Path) -> None:
    """The iCloud-specific marker transaction remains executable AppleScript."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return _saved_forward_output() + "Forward Attachment Proof: verified\n"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools.forward_email(
            account="Work",
            message_id="12345",
            to="recipient@example.com",
            attachments=str(attachment_file),
        )

    with TemporaryDirectory(prefix="apple-mail-forward-compile-") as directory:
        source = Path(directory) / "forward-marker.applescript"
        compiled = Path(directory) / "forward-marker.scpt"
        source.write_text(scripts[0], encoding="utf-8")
        result = subprocess.run(
            ["osacompile", "-o", str(compiled), str(source)],
            capture_output=True,
            text=True,
            check=False,
        )

    assert result.returncode == 0, result.stderr or result.stdout


def test_forward_attachment_draft_rejects_missing_id_without_marker_proof(attachment_file: Path) -> None:
    """A saved forward cannot be certified through a similar existing Drafts row."""
    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", return_value=_saved_forward_output(draft_id=None)),
        patch("apple_mail_mcp.tools.compose.verify_draft") as verify_draft,
    ):
        result = compose_tools.forward_email(
            account="Work",
            message_id="12345",
            to="recipient@example.com",
            attachments=str(attachment_file),
        )

    verify_draft.assert_not_called()
    assert result.startswith("Error: FORWARD_DRAFT_ID_UNAVAILABLE")


def test_forward_attachment_draft_rejects_unreadable_saved_attachment(attachment_file: Path) -> None:
    """Filename-only Mail attachment records cannot certify a ready forward."""
    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", return_value=_saved_forward_output()),
        patch(
            "apple_mail_mcp.tools.compose.verify_draft",
            return_value=_verified_attachment_payload(attachment_file, size=None),
        ),
    ):
        result = compose_tools.forward_email(
            account="Work",
            message_id="12345",
            to="recipient@example.com",
            attachments=str(attachment_file),
        )

    assert result.startswith("Error: FORWARD_DRAFT_ATTACHMENT_VERIFICATION_FAILED")
    assert "unreadable" in result


def test_forward_attachment_draft_rejects_zero_byte_saved_attachment(attachment_file: Path) -> None:
    """A zero-size Mail record is not evidence that the selected file materialized."""
    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", return_value=_saved_forward_output()),
        patch(
            "apple_mail_mcp.tools.compose.verify_draft",
            return_value=_verified_attachment_payload(attachment_file, size=0),
        ),
    ):
        result = compose_tools.forward_email(
            account="Work",
            message_id="12345",
            to="recipient@example.com",
            attachments=str(attachment_file),
        )

    assert result.startswith("Error: FORWARD_DRAFT_ATTACHMENT_VERIFICATION_FAILED")
    assert "unreadable" in result


def test_forward_attachment_draft_rejects_verification_of_different_draft(attachment_file: Path) -> None:
    """Verification must describe the exact ID captured from the saved forward."""
    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", return_value=_saved_forward_output()),
        patch(
            "apple_mail_mcp.tools.compose.verify_draft",
            return_value=_verified_attachment_payload(attachment_file, draft_id="84054"),
        ),
    ):
        result = compose_tools.forward_email(
            account="Work",
            message_id="12345",
            to="recipient@example.com",
            attachments=str(attachment_file),
        )

    assert result.startswith("Error: FORWARD_DRAFT_ID_MISMATCH")


@pytest.mark.parametrize("verification_error", [RuntimeError("verifier unavailable"), AppleScriptTimeout("timed out")])
def test_forward_verification_exception_is_fail_closed_without_lead_message(
    verification_error: Exception,
) -> None:
    """Verification errors cannot escape after Mail has saved a forward draft."""
    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", return_value=_saved_forward_output()),
        patch("apple_mail_mcp.tools.compose.verify_draft", side_effect=verification_error),
    ):
        result = compose_tools.forward_email(
            account="Work",
            message_id="12345",
            to="recipient@example.com",
        )

    assert result.startswith("Error: FORWARD_DRAFT_VERIFICATION_FAILED")
    assert "No email was sent" in result


def test_forward_default_signature_expectation_is_unknown_when_no_name_resolves() -> None:
    """Mail's automatic default signature is neither required nor rejected."""
    verify_calls: list[dict[str, object]] = []

    def fake_verify(**kwargs: object) -> str:
        verify_calls.append(kwargs)
        return json.dumps({"draft_id": "84055", "found": True, "warnings": []})

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", return_value=_saved_forward_output()),
        patch("apple_mail_mcp.tools.compose.forward._resolve_signature_name", return_value=None),
        patch("apple_mail_mcp.tools.compose.verify_draft", side_effect=fake_verify),
    ):
        result = compose_tools.forward_email(
            account="Work",
            message_id="12345",
            to="recipient@example.com",
            include_signature=True,
        )

    assert "Verification Status: found" in result
    assert verify_calls[0]["expected_signature"] is None


def test_forward_verification_parser_exception_is_fail_closed_without_lead_message() -> None:
    """Unexpected verifier payload types cannot escape as a tool exception."""
    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", return_value=_saved_forward_output()),
        patch("apple_mail_mcp.tools.compose.verify_draft", return_value=None),
    ):
        result = compose_tools.forward_email(
            account="Work",
            message_id="12345",
            to="recipient@example.com",
        )

    assert result.startswith("Error: FORWARD_DRAFT_VERIFICATION_FAILED")
    assert "No email was sent" in result
