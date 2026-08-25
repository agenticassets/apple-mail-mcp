"""Native-reply abort remediations report a suspect draft; they never order a delete.

Every abort path (typing interrupted, sender override refused, the three guard
aborts) runs ``_probe_abort_artifact`` to see whether a stray Drafts row was
left behind. That probe calls the verifier with ``draft_id=None``, so
``artifact_identity_verified`` is False by construction -- there is no id to
match against. What it searches on is the reply subject, and a reply subject is
"Re: <thread>", which is also what a reply draft the user wrote earlier in that
same thread carries. The probe cannot tell those apart.

The cleanup string nonetheless read "inspect or delete that exact Drafts
artifact with ... manage_drafts(action='delete', draft_id=...)". Every one of
these errors has already told the caller nothing was saved, so the row it then
offers up for deletion is more likely the user's than the tool's, and a deleted
draft has no undo.

Same defect as the ``REPLY_BODY_MISMATCH`` gate in
``tests/compose/test_reply_mismatch_delete_authorization.py``, on the paths
where identity can never be proven at all.
"""

from __future__ import annotations

import json

import pytest
from apple_mail_mcp.tools.compose import reply_runner
from apple_mail_mcp.tools.compose.reply_window_identity_scripts import (
    NATIVE_REPLY_SENDER_OVERRIDE_ABORT,
)
from apple_mail_mcp.tools.compose.verification import _ReplyDraftVerification

_SENTINELS = [
    "TYPING_INTERRUPTED",
    NATIVE_REPLY_SENDER_OVERRIDE_ABORT,
    "GUARD_ABORT_WINDOW",
    "GUARD_ABORT_SUBJECT",
    "GUARD_ABORT",
]


def _abort_payload(sentinel: str, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Return the parsed error for one abort sentinel with a probe that found a row."""

    def fake_probe(*args: object, **kwargs: object) -> _ReplyDraftVerification:
        # What the real probe returns when a same-subject Drafts row exists.
        # draft_id is None at the call site, so identity is never established.
        return _ReplyDraftVerification(
            ok=False,
            status="body_missing",
            body_missing_artifact_id="116814",
        )

    monkeypatch.setattr(reply_runner, "_verify_saved_reply_draft", fake_probe)
    result = reply_runner._native_reply_abort_response(
        f"{sentinel}|Subject: Re: Quarterly review",
        account="Work",
        reply_body="Thanks, that works for me.",
        timeout=60,
    )
    assert result is not None, f"{sentinel} must be recognized as an abort sentinel"
    return json.loads(result)


@pytest.mark.parametrize("sentinel", _SENTINELS)
def test_the_suspect_id_is_still_reported(sentinel: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Withholding the delete order must not withhold the information."""
    remediation = _abort_payload(sentinel, monkeypatch)["remediation"]
    assert remediation["suspected_draft_id"] == "116814"
    assert remediation["draft_artifact_status"] == "body_missing"


@pytest.mark.parametrize("sentinel", _SENTINELS)
def test_no_abort_path_orders_a_delete(sentinel: str, monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup = _abort_payload(sentinel, monkeypatch)["remediation"]["cleanup"]
    assert "manage_drafts(action='delete'" not in cleanup
    assert "verify_draft(draft_id=...)" in cleanup


@pytest.mark.parametrize("sentinel", _SENTINELS)
def test_every_abort_path_says_why_not(sentinel: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """"Do not delete" without a reason invites an agent to override it."""
    cleanup = _abort_payload(sentinel, monkeypatch)["remediation"]["cleanup"]
    assert "Do not delete it automatically" in cleanup
    assert "found by subject, not by verified reply identity" in cleanup


@pytest.mark.parametrize("sentinel", _SENTINELS)
def test_an_empty_probe_reports_no_suspect(sentinel: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The common case: nothing stray was found, so there is nothing to name."""

    def fake_probe(*args: object, **kwargs: object) -> _ReplyDraftVerification:
        return _ReplyDraftVerification(ok=False, status="not_found")

    monkeypatch.setattr(reply_runner, "_verify_saved_reply_draft", fake_probe)
    result = reply_runner._native_reply_abort_response(
        f"{sentinel}|Subject: Re: Quarterly review",
        account="Work",
        reply_body="Thanks, that works for me.",
        timeout=60,
    )
    assert result is not None
    assert json.loads(result)["remediation"]["suspected_draft_id"] is None


def test_a_non_sentinel_result_is_not_an_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard that lets normal success handling through must stay intact."""
    monkeypatch.setattr(
        reply_runner,
        "_verify_saved_reply_draft",
        lambda *a, **k: pytest.fail("a successful reply must not run the abort probe"),
    )
    assert (
        reply_runner._native_reply_abort_response(
            "Draft ID: 91061\nSubject: Re: Quarterly review",
            account="Work",
            reply_body="Thanks, that works for me.",
            timeout=60,
        )
        is None
    )
