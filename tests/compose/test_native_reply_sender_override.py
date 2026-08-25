"""Regressions for the native reply ``from_address`` override.

The native reply path opens Mail's own reply window and then applies two
optional tweaks to it. Both used to live in one bare ``try ... end try`` block
commented "best-effort identity tweaks", so an explicitly requested
``from_address`` that Mail refused was swallowed and the reply was drafted or
sent from a different identity while the tool reported success. No verifier in
the reply path reads the saved draft's ``From``, so nothing downstream caught it.

These tests lock both halves of the fix:

* an explicitly requested override fails closed (script-level guard + the
  structured ``REPLY_SENDER_OVERRIDE_FAILED`` error its sentinel maps to), and
* with no ``from_address`` the quiet path stays quiet: no sender statement, no
  abort branch, and the signature tweak keeps its deliberately swallowed
  ``try`` wrapper so Mail's own default reply signature is still the intended
  outcome rather than a new failure mode.

No test here touches Mail: ``run_applescript`` is mocked and the autouse fixture
below makes any real ``osascript`` invocation fail loudly. The one subprocess
call that is allowed is an offline ``osacompile`` parse check on a generated
script, which never launches Mail.
"""

import json
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from apple_mail_mcp.tools import compose as compose_tools
from apple_mail_mcp.tools.compose import reply_scripts, reply_window_identity_scripts

_OSACOMPILE = shutil.which("osacompile")

_ALIASES = "default@example.com\nsecondary@example.org"
_OVERRIDE_ADDRESS = "secondary@example.org"


@pytest.fixture(autouse=True)
def _no_live_mail(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make any real ``osascript`` call fail loudly instead of driving Mail.app.

    ``osacompile`` is deliberately still allowed: it only parses a temp file.
    """
    real_run = subprocess.run

    def guarded(*args: Any, **kwargs: Any) -> Any:
        argv = args[0] if args else kwargs.get("args")
        if isinstance(argv, (list, tuple)) and argv and "osascript" in str(argv[0]):
            raise AssertionError("test attempted a live osascript call against Mail.app")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded)
    yield


def _sender_override_abort_output() -> str:
    """Return what the emitted ``on error senderErrMsg`` handler now returns."""
    return "\n".join(
        [
            "SENDER_OVERRIDE_FAILED",
            "Subject: Re: Test",
            "DerivedSubject: Re: Test",
            "Detail: Mail got an error: Can't set sender of outgoing message.",
        ]
    )


def _saved_native_reply_output(*, draft_id: str = "84053") -> str:
    return "\n".join(
        [
            "SAVING REPLY AS DRAFT",
            "",
            "Reply saved as draft!",
            "To: native reply recipients",
            "Subject: Re: Test",
            f"Draft ID: {draft_id}",
            f"Draft Identity: {draft_id}|||<draft-{draft_id}@example.com>|||<source@example.com>|||rfc",
            "Quote Needle: On Today, Sender <sender@example.com> wrote:",
            "",
        ]
    )


def _native_reply_script(scripts: list[str]) -> str:
    matches = [script for script in scripts if "reply foundMessage" in script]
    assert len(matches) == 1
    return matches[0]


def _build_native_script(sender_script: str, signature_script: str = "") -> str:
    """Build the native reply script directly, with synthetic fragments."""
    return reply_scripts._build_reply_native_window_applescript(
        header_text="SAVING REPLY AS DRAFT",
        success_text="Reply saved as draft!",
        safe_account="Test Account",
        mailbox_lookup='set sourceMailbox to mailbox "Inbox" of targetAccount',
        lookup_script="set foundMessage to missing value",
        not_found_message="Email not found",
        body_temp_path="/tmp/apple-mail-sender-override-check.txt",
        reply_options="with opening window",
        sender_script=sender_script,
        signature_script=signature_script,
        cc_script="",
        bcc_script="",
        attachment_script="",
        mode="draft",
        cleanup_script='do shell script "rm -f /tmp/apple-mail-sender-override-check.txt"',
        safe_cc="",
        safe_bcc="",
        safe_attachment_info="",
        has_cc=False,
        has_bcc=False,
        has_attachments=False,
    )


def _sender_guard_branch(script: str) -> str:
    """Return only the emitted ``on error senderErrMsg`` handler body."""
    start = script.index("on error senderErrMsg")
    return script[start : script.index("end try", start)]


# ---------------------------------------------------------------------------
# from_address WAS requested: must never be reported as success
# ---------------------------------------------------------------------------


def test_requested_from_address_is_emitted_under_a_fail_closed_error_handler() -> None:
    """A requested override must not sit in a bare ``try`` that swallows refusals."""
    sender_statement = f'set sender of replyMessage to "{_OVERRIDE_ADDRESS}"'
    script = _build_native_script(sender_statement)

    assert sender_statement in script
    assert "on error senderErrMsg" in script
    # The pre-fix shape: the sender statement wrapped in a try with no handler.
    assert f"try\n            {sender_statement}\n        end try" not in script

    branch = _sender_guard_branch(script)
    assert "my closeNativeReplyWindowSafely(replyWindowId, replySubject, derivedReplySubject)" in branch
    assert 'do shell script "rm -f /tmp/apple-mail-sender-override-check.txt"' in branch
    assert f'return "{reply_window_identity_scripts.NATIVE_REPLY_SENDER_OVERRIDE_ABORT}"' in branch
    assert "senderErrMsg" in branch


def test_sender_override_refusal_aborts_before_anything_is_saved() -> None:
    """The refusal must precede ``save replyMessage`` so no Drafts artifact exists."""
    script = _build_native_script('set sender of replyMessage to "alias@example.com"')

    assert script.index('return "SENDER_OVERRIDE_FAILED"') < script.index("save replyMessage")
    assert script.index('return "SENDER_OVERRIDE_FAILED"') < script.index("typeReplyBodyChunks(replyBodyText")


def test_native_reply_reports_a_structured_failure_when_mail_refuses_the_override() -> None:
    """A refused explicit from_address must surface as an error, never a clean result."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        if "return addrText" in script:
            return _ALIASES
        if "reply foundMessage" in script:
            return _sender_override_abort_output()
        return "NOT_FOUND"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            from_address=_OVERRIDE_ADDRESS,
        )

    payload = json.loads(result)
    assert payload["code"] == "REPLY_SENDER_OVERRIDE_FAILED"
    assert "no email was sent" in payload["message"]
    assert "no draft was saved" in payload["message"]
    assert payload["remediation"]["draft_artifact_status"] == "not_found"
    assert payload["remediation"]["suspected_draft_id"] is None
    assert "Can't set sender" in payload["remediation"]["detail"]
    # The sentinel is consumed, not leaked to the caller as opaque text.
    assert "SENDER_OVERRIDE_FAILED" not in payload["message"]


def test_sender_override_refusal_is_structured_on_the_json_contract_too() -> None:
    """``output_format='json'`` must not turn the refusal into a success payload."""

    def fake_mail(script: str, timeout: int = 120) -> str:
        if "return addrText" in script:
            return _ALIASES
        if "reply foundMessage" in script:
            return _sender_override_abort_output()
        return "NOT_FOUND"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            from_address=_OVERRIDE_ADDRESS,
            output_format="json",
        )

    payload = json.loads(result)
    assert payload["code"] == "REPLY_SENDER_OVERRIDE_FAILED"
    assert "verification_status" not in payload
    assert "draft_id" not in payload


# ---------------------------------------------------------------------------
# No from_address: the quiet default path must stay quiet
# ---------------------------------------------------------------------------


def test_default_native_reply_emits_no_sender_statement_and_no_abort_branch() -> None:
    """Without an override there is nothing to guard, so nothing may be emitted.

    This is the load-bearing half of the fix: the sender fragment is empty
    whenever ``from_address`` was omitted (``_validate_from_address`` returns
    ``None``), so the fail-closed handler must not appear at all and cannot
    invent a failure on the default path.
    """
    signature_statement = "set message signature of replyMessage to missing value"
    script = _build_native_script("", signature_script=signature_statement)

    assert "set sender of replyMessage" not in script
    assert "on error senderErrMsg" not in script
    assert "SENDER_OVERRIDE_FAILED" not in script
    # The signature tweak keeps its intentionally swallowed bare-try wrapper:
    # a throw there must not abort, because Mail's own default reply signature
    # is the intended result and the saved-draft verifier still checks a named one.
    assert f"try\n            {signature_statement}\n        end try" in script


def test_default_native_reply_still_verifies_and_succeeds() -> None:
    """A normal no-override reply must produce a clean success with no spurious error."""
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
            include_signature=False,
            output_format="json",
        )

    payload = json.loads(result)
    assert payload["verification_status"] == "found"
    assert payload["draft_id"] == "84053"
    assert "code" not in payload

    script = _native_reply_script(scripts)
    assert "set sender of replyMessage" not in script
    assert "SENDER_OVERRIDE_FAILED" not in script


def test_unrelated_native_reply_output_is_not_treated_as_a_sender_abort() -> None:
    """The abort dispatcher must stay tri-state and pass normal output through."""
    assert (
        compose_tools._native_reply_abort_response(
            _saved_native_reply_output(),
            account="Work",
            reply_body="Reply body",
            timeout=None,
        )
        is None
    )


# ---------------------------------------------------------------------------
# Offline parse check (no Mail involved)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_OSACOMPILE is None, reason="osacompile not available on this host")
def test_sender_override_guard_script_compiles() -> None:
    """The override-bearing native script must still be valid AppleScript."""
    script = _build_native_script(
        f'set sender of replyMessage to "{_OVERRIDE_ADDRESS}"',
        signature_script="set message signature of replyMessage to missing value",
    )

    with tempfile.NamedTemporaryFile("w", suffix=".applescript", delete=False) as handle:
        handle.write(script)
        source_path = handle.name
    out_path = source_path.replace(".applescript", ".scpt")
    try:
        assert _OSACOMPILE is not None
        completed = subprocess.run(
            [_OSACOMPILE, "-o", out_path, source_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, (completed.stderr or completed.stdout).strip()
    finally:
        for path in (source_path, out_path):
            with suppress(OSError):
                Path(path).unlink(missing_ok=True)
