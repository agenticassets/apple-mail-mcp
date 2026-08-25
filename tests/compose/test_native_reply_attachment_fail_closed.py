"""Regressions for native reply quote and attachment preservation."""

import json
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from apple_mail_mcp.tools import compose as compose_tools


def _saved_native_reply_output(*, draft_id: str = "84053") -> str:
    return "\n".join(
        [
            "SAVING REPLY AS DRAFT",
            "",
            "Reply saved as draft!",
            "To: native reply recipients",
            "Subject: Re: Test",
            f"Draft ID: {draft_id}",
            (f"Draft Identity: {draft_id}|||<draft-{draft_id}@example.com>|||<source@example.com>|||rfc"),
            "Quote Needle: On Today, Sender <sender@example.com> wrote:",
            "Quote Anchor: Original source paragraph text",
            "",
        ]
    )


def _saved_native_reply_transaction_output(*, draft_id: str = "84053") -> str:
    """Return a native reply result with an iCloud-safe transaction identity."""
    return "\n".join(
        [
            "SAVING REPLY AS DRAFT",
            "",
            "Reply saved as draft!",
            "To: native reply recipients",
            "Subject: Re: Test",
            f"Draft ID: {draft_id}",
            "Draft Identity: " + "|||".join((draft_id, "", "", "transaction")),
            "Quote Needle: On Today, Sender <sender@example.com> wrote:",
            "Quote Anchor: Original source paragraph text",
            "",
        ]
    )


def _native_reply_script(scripts: list[str]) -> str:
    matches = [script for script in scripts if "reply foundMessage" in script]
    assert len(matches) == 1
    return matches[0]


@pytest.fixture
def home_temp_dir() -> Iterator[Path]:
    """Yield an automatically cleaned directory accepted by attachment safety checks."""
    with tempfile.TemporaryDirectory(prefix="apple-mail-reply-test-", dir=Path.home()) as directory:
        yield Path(directory)


def test_native_reply_fails_when_saved_body_has_no_quoted_original() -> None:
    """A body match alone must not verify a native reply whose quote vanished."""

    def fake_mail(script: str, timeout: int = 120) -> str:
        if "reply foundMessage" in script:
            return _saved_native_reply_output()
        if 'set targetDraftIdText to "84053"' in script:
            # Mail-side verification found the authored body but no quote after it.
            return "QUOTE_MISSING|84053|not_requested|not_requested|0|"
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            output_format="json",
        )

    assert "REPLY_QUOTED_ORIGINAL_MISSING" in result


def test_saved_reply_verifier_emits_quote_missing_when_body_has_no_following_quote() -> None:
    """The generated Mail verifier must distinguish quote loss from body success."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "QUOTE_MISSING|84053|not_requested|not_requested|0|"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools._verify_saved_reply_draft(
            "Work",
            "Re: Test",
            "Reply body",
            draft_id="84053",
            quoted_needle="On Today, Sender <sender@example.com> wrote:",
        )

    assert len(scripts) == 1
    verifier_script = scripts[0]
    assert 'if bodyEndOffset > (count of characters of flatDraft) then return "quote_missing"' in verifier_script
    assert 'if bodyStatus is "quote_missing" then return "QUOTE_MISSING|" & draftId' in verifier_script


def test_saved_reply_verifier_decides_the_body_verdict_before_probing_attachments() -> None:
    """The two body-only verdicts must return before any attachment probe runs.

    ``BODY_AFTER_QUOTE`` and ``BODY_MISSING`` carry the draft id alone, so the
    attachment and signature probes they used to run ahead of the verdict were
    always discarded. Those probes are not cheap: three separate walks of
    ``mail attachments`` (each reading ``file size``) plus a full signature
    normalization of the draft body. On the same-subject fallback scan most of
    the bounded Drafts window is somebody else's draft, and the saved-draft
    verifier polls up to twenty times, so the waste was paid per draft per
    attempt while holding the cross-process Mail lock. Hoisting the probes back
    above the verdict is the regression this guards.
    """
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "QUOTE_MISSING|84053|not_requested|not_requested|0|"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools._verify_saved_reply_draft(
            "Work",
            "Re: Test",
            "Reply body",
            draft_id="84053",
            quoted_needle="On Today, Sender <sender@example.com> wrote:",
        )

    verifier_script = scripts[0]
    body_verdict = verifier_script.index("set bodyStatus to my replyBodyAboveQuoteStatus(draftContent")
    after_quote = verifier_script.index('if bodyStatus is "after_quote" then return "BODY_AFTER_QUOTE|" & draftId')
    body_missing = verifier_script.index(
        'if bodyStatus is not "found" and bodyStatus is not "quote_missing" then return "BODY_MISSING|" & draftId'
    )
    first_probe = verifier_script.index("set draftAttachmentStatus to my attachmentStatus(draftMessage")
    signature_probe = verifier_script.index("set draftSignatureStatus to my signatureStatus(draftContent")
    assert body_verdict < after_quote < body_missing < first_probe < signature_probe


def test_native_reply_uses_source_content_as_its_quote_proof() -> None:
    """A sender-only attribution must not certify a lost native quote."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        if "reply foundMessage" in script:
            return _saved_native_reply_output()
        if 'set targetDraftIdText to "84053"' in script:
            return "FOUND|84053|not_requested|not_requested|0|"
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            output_format="json",
        )

    assert json.loads(result)["verification_status"] == "found"
    native_script = _native_reply_script(scripts)
    assert "set sourceSender to sender of foundMessage as string" in native_script
    assert "set sourceContent to content of foundMessage as string" in native_script
    # The two proof halves travel as separate output fields, because the reader
    # is line-based and a single field with an embedded return delivered only the
    # attribution -- silently dropping the source-content half.
    assert 'set quotedNeedle to sourceSender & " wrote:"' in native_script
    assert 'set outputText to outputText & "Quote Anchor: " & sourceQuoteAnchor' in native_script
    assert 'return "QUOTE_PROOF_UNAVAILABLE"' in native_script
    assert 'set quotedNeedle to "wrote:"' not in native_script
    # ...and the anchor must actually REACH the verifier. It used to be appended
    # to the needle behind an embedded return, which the line-based reader
    # dropped, so the draft was certified by the attribution alone -- the exact
    # sender-only proof this test's name says is not enough.
    verifier_script = next(script for script in scripts if 'set targetDraftIdText to "84053"' in script)
    assert 'set quoteAnchor to "Original source paragraph text"' in verifier_script
    assert "on quoteProofHolds(searchRegion, flatQuote, quoteAnchor)" in verifier_script


def test_native_reply_falls_back_to_a_short_source_paragraph_for_its_anchor() -> None:
    """A short source email must be replyable, not refused for want of an anchor.

    The anchor search prefers a paragraph of 16 characters or more because a
    longer span is more distinctive. It used to *require* one, so "Thanks!",
    "Approved.", and every other one-line email returned
    ``QUOTE_PROOF_UNAVAILABLE`` and could not be replied to at all. The floor is
    now a preference with a first-non-empty-paragraph fallback; the refusal
    survives only for a source whose content is empty or unreadable.
    """
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        if "reply foundMessage" in script:
            return _saved_native_reply_output()
        if 'set targetDraftIdText to "84053"' in script:
            return "FOUND|84053|not_requested|not_requested|0|"
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            output_format="json",
        )

    native_script = _native_reply_script(scripts)
    preference = native_script.index("if candidateQuoteLength >= 16 then")
    fallback = native_script.index('if sourceQuoteFallback is "" and my stripLeadingSpaces(candidateQuoteText)')
    adopt = native_script.index('if sourceQuoteAnchor is "" then set sourceQuoteAnchor to sourceQuoteFallback')
    refusal = native_script.index('return "QUOTE_PROOF_UNAVAILABLE"')
    assert preference < fallback < adopt < refusal
    # 60, not 160: the anchor has to survive Mail re-wrapping the quoted
    # original, and a paragraph's first 60 characters precede any wrap point.
    assert "if candidateQuoteLength > 60 then set candidateQuoteText to text 1 thru 60 of candidateQuoteText" in (
        native_script
    )


def test_native_reply_never_raises_a_same_subject_window_to_type() -> None:
    """The native guard must activate the exact adopted window, not a title match."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return _saved_native_reply_output()

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
        )

    native_script = _native_reply_script(scripts)
    assert "on raiseNativeReplyWindowSafely(replyWindowId, expectedTitle, derivedTitle)" in native_script
    assert (
        "set replyWindowRaised to my raiseNativeReplyWindowSafely(replyWindowId, replySubject, derivedReplySubject)"
        in native_script
    )
    assert 'perform action "AXRaise" of (first window whose name is replySubject)' not in native_script


def test_saved_reply_verifier_does_not_fallback_after_exact_attachment_failure() -> None:
    """A known Drafts id with a missing attachment must fail closed, never certify an older match."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ATTACHMENT_MISSING|84053|missing|not_requested|0|"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        verification = compose_tools._verify_saved_reply_draft(
            "Work",
            "Re: Test",
            "Reply body",
            draft_id="84053",
            quoted_needle="Sender <sender@example.com> wrote:",
            expected_attachment_count=1,
            expected_attachment_names=["support.pdf"],
        )

    assert verification.status == "attachment_verification_failed"
    verifier_script = scripts[0]
    attachment_failure_return = 'if attachmentFailureResult is not "" then return attachmentFailureResult'
    fallback_scan = "if (requireNativeIdentity is false) and (requireExactAttachmentIdentity is false) then"
    assert attachment_failure_return in verifier_script
    assert verifier_script.index(attachment_failure_return) < verifier_script.index(fallback_scan)


def test_attachment_reply_refuses_a_fallback_match_without_persisted_identity() -> None:
    """An attachment-bearing reply cannot trust a same-subject Drafts fallback."""

    with patch(
        "apple_mail_mcp.tools.compose.run_applescript",
        return_value="FOUND|99999|verified|not_requested|1|support.pdf::9;;",
    ):
        verification = compose_tools._verify_saved_reply_draft(
            "Work",
            "Re: Test",
            "Reply body",
            expected_attachment_count=1,
            expected_attachment_names=["support.pdf"],
        )

    assert verification.ok is False
    assert verification.status == "identity_unavailable"


def test_attachment_reply_accepts_only_a_single_new_iCloud_transaction_draft(home_temp_dir: Path) -> None:
    """A blank iCloud Message-ID is safe only with one bounded newly saved Drafts row."""
    attachment = home_temp_dir / "support.pdf"
    attachment.write_bytes(b"%PDF-test")

    def fake_mail(script: str, timeout: int = 120) -> str:
        if "reply foundMessage" in script:
            return _saved_native_reply_transaction_output()
        if 'set targetDraftIdText to "84053"' in script:
            return "FOUND|84053|verified|not_requested|1|support.pdf::9;;"
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            attachments=str(attachment),
            include_signature=False,
            output_format="json",
        )

    payload = json.loads(result)
    assert payload["verification_status"] == "found"
    assert payload["draft_id"] == "84053"
    assert payload["exact_id_verified"] is True
    assert payload["draft_id_source"] == "transaction_scoped_numeric_identity"


def test_iCloud_transaction_resolver_rejects_ambiguous_or_rfc_mismatched_drafts() -> None:
    """The no-RFC path must not turn an ambiguous or contradictory post-save set into identity."""
    scripts: list[str] = []

    with patch(
        "apple_mail_mcp.tools.compose.run_applescript",
        side_effect=lambda script, timeout=120: scripts.append(script) or "NOT_FOUND",
    ):
        compose_tools._verify_saved_reply_draft(
            "Work",
            "Re: Test",
            "Reply body",
            draft_id="84053",
            quoted_needle="Sender <sender@example.com> wrote:",
            expected_attachment_count=1,
            expected_attachment_names=["support.pdf"],
        )

    resolver_script = scripts[0]
    assert 'if (count of newDraftIdentities) is not 1 then return missing value' in resolver_script
    assert 'if candidateRfcMessageId is "" then return {candidateDraftId, "", "", "transaction"}' in resolver_script
    assert "if my headerHasExactRfcToken(item 2 of inReplyToResult, sourceMessageId) then" in resolver_script
    assert 'return {candidateDraftId, candidateRfcMessageId, sourceMessageId, "rfc"}' in resolver_script


def test_reply_attachment_verifier_rejects_unreadable_attachment_metadata() -> None:
    """A zero or unreadable attachment must not be certified as materialized."""

    verification = compose_tools._reply_verification_from_output(
        "FOUND|84053|verified|not_requested|1|support.pdf::0;;"
    )

    assert verification.ok is False
    assert verification.status == "attachment_verification_failed"
    assert verification.attachment_status == "unreadable"


def test_saved_reply_verifier_emits_unreadable_for_nonpositive_attachment_size() -> None:
    """Mail-side attachment checks must reject a file that has no readable bytes."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ATTACHMENT_UNREADABLE|84053|unreadable|not_requested|1|support.pdf::0;;"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        verification = compose_tools._verify_saved_reply_draft(
            "Work",
            "Re: Test",
            "Reply body",
            draft_id="84053",
            expected_attachment_count=1,
            expected_attachment_names=["support.pdf"],
        )

    assert verification.ok is False
    assert verification.status == "attachment_verification_failed"
    assert verification.attachment_status == "unreadable"
    assert 'if attachmentSize is less than or equal to 0 then return "unreadable"' in scripts[0]
    assert 'if draftAttachmentStatus is "unreadable" then return "ATTACHMENT_UNREADABLE|"' in scripts[0]


@pytest.mark.parametrize("attachment_status", ["missing", "unsupported"])
def test_native_reply_fails_closed_when_requested_attachment_is_not_verified(
    home_temp_dir: Path,
    attachment_status: str,
) -> None:
    """A requested attachment must be required for reply verification success."""
    attachment = home_temp_dir / "support.pdf"
    attachment.write_bytes(b"%PDF-test")

    def fake_mail(script: str, timeout: int = 120) -> str:
        if "reply foundMessage" in script:
            return _saved_native_reply_output()
        if 'set targetDraftIdText to "84053"' in script:
            return f"FOUND|84053|{attachment_status}|not_requested|0|"
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            attachments=str(attachment),
            include_signature=False,
            output_format="json",
        )

    payload = json.loads(result)
    assert payload.get("code") == "REPLY_DRAFT_ATTACHMENT_VERIFICATION_FAILED"
    assert payload["remediation"]["attachment_status"] == attachment_status
    assert payload["remediation"]["draft_id"] == "84053"


def test_native_reply_inserts_attachment_only_after_body_typing(home_temp_dir: Path) -> None:
    """The native quote must settle through typing before Mail adds attachments."""
    attachment = home_temp_dir / "support.pdf"
    attachment.write_bytes(b"%PDF-test")
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        if "reply foundMessage" in script:
            return _saved_native_reply_output()
        if 'set targetDraftIdText to "84053"' in script:
            return "FOUND|84053|verified|not_requested|1|support.pdf::9;;"
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            attachments=str(attachment),
            include_signature=False,
            output_format="json",
        )

    assert json.loads(result)["verification_status"] == "found"
    script = _native_reply_script(scripts)
    body_typed = script.index(
        "set typeChunksResult to my typeReplyBodyChunks(replyBodyText, replySubject, derivedReplySubject, "
        "replyWindowId, item 2 of editorFocusOutcome)"
    )
    attachment_inserted = script.index(
        "make new attachment with properties {file name:theFile} at after the last paragraph of content"
    )
    assert body_typed < attachment_inserted


def test_native_reply_focuses_a_guarded_editor_before_typing() -> None:
    """Native typing must target a verified Mail body editor, not just a title."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
        )

    script = _native_reply_script(scripts)
    assert "on resolveReplyBodyEditor(expectedTitle, derivedTitle, expectedWindowId, allowEditorClick)" in script
    assert 'candidateRole is "AXWebArea"' in script
    assert 'candidateRole is "AXTextArea"' in script
    assert (
        "set editorFocusOutcome to my resolveReplyBodyEditor(replySubject, derivedReplySubject, replyWindowId, true)"
        in script
    )
    # item 1 is the diagnostic status the abort path reports; the guard passes
    # only on "focused", so nothing is typed without a verified body editor.
    assert "set editorFocusResult to item 1 of editorFocusOutcome" in script
    assert script.index("set editorFocusOutcome to my resolveReplyBodyEditor") < script.index(
        "set typeChunksResult to my typeReplyBodyChunks"
    )


def test_native_reply_editor_selector_uses_a_runtime_safe_role_loop() -> None:
    """System Events cannot filter ``entire contents`` by role with ``whose``."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
        )

    script = _native_reply_script(scripts)
    assert 'every UI element of entire contents of targetWindow whose role is "AXWebArea"' not in script
    assert 'every UI element of entire contents of targetWindow whose role is "AXTextArea"' not in script
    assert "set allElements to entire contents of targetWindow" in script
    assert "repeat with candidateElement in allElements" in script
    assert 'set candidateRole to value of attribute "AXRole" of candidateElement as string' in script


def test_native_reply_prefers_text_editor_and_requires_confirmed_focus() -> None:
    """Mail's visible web area is not necessarily the actionable text editor."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
        )

    script = _native_reply_script(scripts)
    assert "set webAreaFallback to missing value" in script
    assert 'if candidateRole is "AXTextArea" then' in script
    assert 'else if candidateRole is "AXWebArea" and webAreaFallback is missing value then' in script
    assert script.index('if candidateRole is "AXTextArea" then') < script.index('else if candidateRole is "AXWebArea"')
    assert "set replyEditor to webAreaFallback" in script
    assert "click replyEditor" in script
    assert 'set focusedUIElement to value of attribute "AXFocusedUIElement" of targetWindow' in script
    assert 'if editorIsFocused or focusedElementMatches then return {"focused", replyEditor}' in script


def test_native_reply_never_clicks_the_editor_once_body_text_exists() -> None:
    """``click`` seats the caret, so a mid-body click would splice later chunks into typed text.

    The click is confined to the pre-typing resolution (empty body). Every
    resolution attempted between chunks passes ``allowEditorClick`` false and
    fails closed instead of re-seating the insertion point.
    """
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
        )

    script = _native_reply_script(scripts)
    assert "if editorIsFocused is not true and allowEditorClick then" in script
    # The only click site is guarded by that flag.
    assert script.count("click replyEditor") == 1
    # Pre-typing resolution may click; the mid-typing re-resolve may not.
    assert (
        "set resolvedEditor to my resolveReplyBodyEditor(expectedTitle, derivedTitle, expectedWindowId, true)" in script
    )
    assert (
        "set resolvedEditor to my resolveReplyBodyEditor(expectedTitle, derivedTitle, expectedWindowId, false)"
        in script
    )


def test_native_reply_resolves_the_editor_once_not_per_chunk() -> None:
    """``entire contents`` is a full AX subtree walk; paying it per 80-char chunk is the bug.

    A 1600-character body used to trigger twenty full walks of the compose
    window's Accessibility tree, inflating latency and the typing-timeout
    projection. Each chunk now pays one ``AXFocused`` read against a cached
    element reference, and only re-resolves when that read fails.

    The success path now walks the subtree exactly once for the whole reply: the
    pre-typing guard resolves, and ``typeReplyBodyChunks`` adopts that reference
    instead of resolving a second time microseconds later. The safety property is
    unchanged because the per-chunk guard re-proves window identity and editor
    focus before the FIRST keystroke, so no chunk is ever typed on the strength
    of the guard's resolution alone.
    """
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
        )

    script = _native_reply_script(scripts)
    assert "on replyEditorFocusHolds(editorReference)" in script
    assert "set allElements to entire contents of targetWindow" in script
    # The walk lives in exactly one handler, and the per-chunk guard is not it.
    assert script.count("set allElements to entire contents of targetWindow") == 1
    assert "if (my replyEditorFocusHolds(replyEditorReference)) is false then" in script
    # The chunk loop must not call the resolving handler unconditionally.
    typing_loop = script[script.index("on typeReplyBodyChunks(") :]
    assert typing_loop.index("if (my replyEditorFocusHolds(replyEditorReference)) is false then") < typing_loop.index(
        "keystroke chunkText"
    )
    # The typing pass adopts the guard's already-resolved editor and only walks
    # the subtree itself when handed nothing. Dropping this guard silently
    # reintroduces a second full walk on every native reply.
    assert "set replyEditorReference to preResolvedEditor" in typing_loop
    adopt = typing_loop.index("set replyEditorReference to preResolvedEditor")
    fallback = typing_loop.index("if replyEditorReference is missing value then")
    assert adopt < fallback < typing_loop.index("set resolvedEditor to my resolveReplyBodyEditor")
    # Identity and focus are both re-proved before any keystroke, which is why
    # dropping the duplicate resolve costs no verification.
    assert typing_loop.index("set blockedName to my chunkFocusBlockedName(") < typing_loop.index("keystroke chunkText")


def test_native_reply_aborts_before_attachment_when_editor_focus_is_not_verified(home_temp_dir: Path) -> None:
    """An attachment must not be created if the native reply body editor cannot be focused."""
    attachment = home_temp_dir / "support.pdf"
    attachment.write_bytes(b"%PDF-test")
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="",
            attachments=str(attachment),
            include_signature=False,
        )

    script = _native_reply_script(scripts)
    assert "set composeFocusVerified to false" in script
    assert "if composeFocusVerified is false then" in script
    assert script.index("if composeFocusVerified is false then") < script.index(
        "make new attachment with properties {file name:theFile}"
    )


def test_native_reply_cleanup_targets_only_the_opened_window() -> None:
    """A failed reply must never close another user draft sharing its subject."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
        )

    script = _native_reply_script(scripts)
    assert "set preReplyWindowIds to my mailWindowIdSnapshot()" in script
    assert "set replyWindowId to my newlyOpenedReplyWindowId(preReplyWindowIds, derivedReplySubject)" in script
    assert "set replyWindowId to id of front window as string" not in script
    assert "on closeNativeReplyWindowSafely(replyWindowId, expectedTitle, derivedTitle)" in script
    assert "close candidateWindow saving no" in script
    assert "close (every window whose name is" not in script


def test_native_reply_with_attachment_rejects_direct_send_before_mail_mutation(home_temp_dir: Path) -> None:
    """Attachments must go through save-and-verify before any send is allowed."""
    attachment = home_temp_dir / "support.pdf"
    attachment.write_bytes(b"%PDF-test")
    with (
        patch("apple_mail_mcp.tools.compose.reply._resolve_account", return_value=("Work", None)),
        patch("apple_mail_mcp.tools.compose.reply._validate_from_address", return_value=(None, None)),
        patch("apple_mail_mcp.tools.compose.reply._validate_signature_name", return_value=None),
        patch.object(compose_tools._server, "READ_ONLY", False),
        patch.object(compose_tools._server, "DRAFT_SAFE", False),
        patch("apple_mail_mcp.tools.compose.run_applescript") as mock_mail,
    ):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            attachments=str(attachment),
            mode="send",
            output_format="text",
        )

    mock_mail.assert_not_called()
    payload = json.loads(result)
    assert payload["code"] == "REPLY_SEND_REQUIRES_VERIFIED_DRAFT"
    assert payload["remediation"]["preferred_mode"] == "draft"


def test_saved_reply_verifier_retries_transient_attachment_miss() -> None:
    """A just-saved Exchange attachment may materialize after the draft body."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ATTACHMENT_MISSING|84053|missing|not_requested|0|"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        verification = compose_tools._verify_saved_reply_draft(
            "Work",
            "Re: Test",
            "Reply body",
            draft_id="84053",
            quoted_needle="wrote:",
            expected_attachment_count=1,
            expected_attachment_names=["support.pdf"],
        )

    assert verification.ok is False
    assert verification.status == "attachment_verification_failed"
    verifier_script = scripts[0]
    assert (
        'if exactResult starts with "ATTACHMENT_" then\n'
        "                                set attachmentFailureResult to exactResult\n"
        "                            else\n"
        "                                return exactResult"
    ) in verifier_script


def test_native_attachment_only_reply_still_requires_quoted_original(home_temp_dir: Path) -> None:
    """An empty authored body must not exempt a native attachment reply from quote verification."""
    attachment = home_temp_dir / "support.pdf"
    attachment.write_bytes(b"%PDF-test")
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        if "reply foundMessage" in script:
            return _saved_native_reply_output()
        if 'set targetDraftIdText to "84053"' in script:
            return "QUOTE_MISSING|84053|verified|not_requested|1|support.pdf::9;;"
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="",
            attachments=str(attachment),
            include_signature=False,
            output_format="json",
        )

    assert json.loads(result)["code"] == "REPLY_QUOTED_ORIGINAL_MISSING"
    script = _native_reply_script(scripts)
    assert 'set quotedNeedle to sourceSender & " wrote:"' in script
    verifier_script = next(script for script in scripts if 'set targetDraftIdText to "84053"' in script)
    assert 'if flatBody is "" then' in verifier_script
    assert 'if my quoteProofHolds(flatDraft, flatQuote, quoteAnchor) then return "found"' in verifier_script
    assert 'return "quote_missing"' in verifier_script


def test_fallback_quote_failure_marks_artifact_as_suspect() -> None:
    """A same-subject fallback match is diagnostic and must not authorize deletion."""

    def fake_mail(script: str, timeout: int = 120) -> str:
        if "reply foundMessage" in script:
            return "\n".join(
                [
                    "SAVING REPLY AS DRAFT",
                    "Reply saved as draft!",
                    "To: native reply recipients",
                    "Subject: Re: Test",
                    "Quote Needle: wrote:",
                ]
            )
        if 'set targetDraftIdText to ""' in script:
            return "QUOTE_MISSING|99999|not_requested|not_requested|0|"
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            output_format="json",
        )

    payload = json.loads(result)
    remediation = payload["remediation"]
    assert payload["code"] == "REPLY_QUOTED_ORIGINAL_MISSING"
    assert remediation["artifact_identity_verified"] is False
    assert remediation["suspect_artifact_message_id"] == "99999"
    assert "draft_id" not in remediation
    assert "do not delete" in remediation["preferred"].lower()
