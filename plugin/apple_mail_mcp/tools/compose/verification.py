"""Pure parsing and formatting of saved reply/forward draft verifier output.

No I/O lives here; the live verifiers in ``compose.py`` feed their raw
AppleScript output into these helpers.
"""

import json
from dataclasses import dataclass
from typing import Any

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.tools.draft_verification import _normalize_attachment_rows


def _extract_output_field(output: str, field_name: str) -> str | None:
    """Return a `Field: value` line from a tool status string."""
    prefix = f"{field_name}: "
    for line in output.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def _first_non_empty_line(value: str, *, max_chars: int = 500) -> str:
    """Return a bounded content needle for saved-draft verification."""
    for line in value.splitlines():
        candidate = line.strip()
        if candidate:
            return candidate[:max_chars]
    return ""


@dataclass(frozen=True)
class _ReplyDraftVerification:
    ok: bool
    status: str = "not_found"
    body_missing_artifact_id: str | None = None
    error_artifact_id: str | None = None
    matched_artifact_id: str | None = None
    attachment_status: str | None = None
    attachment_count: int | None = None
    attachments_applied: list[dict[str, Any]] | None = None
    signature_status: str | None = None
    artifact_identity_verified: bool = False


def _reply_verification_from_output(output: str) -> _ReplyDraftVerification:
    """Parse the saved-reply verifier AppleScript response."""
    parts = output.strip().split("|", 5)
    status = parts[0] if parts else ""
    artifact_id = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    attachment_status = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
    signature_status = parts[3].strip() if len(parts) > 3 and parts[3].strip() else None
    attachment_count_text = parts[4].strip() if len(parts) > 4 and parts[4].strip() else None
    try:
        attachment_count = int(attachment_count_text) if attachment_count_text is not None else None
    except ValueError:
        attachment_count = None
    attachments_applied = _normalize_attachment_rows(parts[5]) if len(parts) > 5 and parts[5].strip() else None
    if (
        attachment_status == "verified"
        and attachments_applied
        and any(
            not isinstance(attachment.get("size"), int) or attachment["size"] <= 0 for attachment in attachments_applied
        )
    ):
        attachment_status = "unreadable"
    if status == "FOUND":
        if attachment_status in {"missing", "unsupported", "unreadable"}:
            return _ReplyDraftVerification(
                ok=False,
                status="attachment_verification_failed",
                error_artifact_id=artifact_id,
                attachment_status=attachment_status,
                attachment_count=attachment_count,
                attachments_applied=attachments_applied,
                signature_status=signature_status,
            )
        return _ReplyDraftVerification(
            ok=True,
            status="found",
            matched_artifact_id=artifact_id,
            attachment_status=attachment_status,
            attachment_count=attachment_count,
            attachments_applied=attachments_applied,
            signature_status=signature_status,
        )
    if status in {"ATTACHMENT_MISSING", "ATTACHMENT_UNSUPPORTED", "ATTACHMENT_UNREADABLE"}:
        return _ReplyDraftVerification(
            ok=False,
            status="attachment_verification_failed",
            error_artifact_id=artifact_id,
            attachment_status=attachment_status,
            attachment_count=attachment_count,
            attachments_applied=attachments_applied,
            signature_status=signature_status,
        )
    if status == "BODY_MISSING":
        return _ReplyDraftVerification(
            ok=False,
            status="body_missing",
            body_missing_artifact_id=artifact_id,
        )
    if status == "BODY_AFTER_QUOTE":
        return _ReplyDraftVerification(
            ok=False,
            status="body_after_quote",
            body_missing_artifact_id=artifact_id,
        )
    if status == "QUOTE_MISSING":
        return _ReplyDraftVerification(
            ok=False,
            status="quoted_original_missing",
            error_artifact_id=artifact_id,
            attachment_status=attachment_status,
            attachment_count=attachment_count,
            attachments_applied=attachments_applied,
            signature_status=signature_status,
        )
    if status == "IDENTITY_UNAVAILABLE":
        return _ReplyDraftVerification(ok=False, status="identity_unavailable")
    return _ReplyDraftVerification(ok=False, status="not_found")


def _reply_exact_id_verified(verification: _ReplyDraftVerification, draft_id: str | None) -> bool:
    """Return whether verification proved the exact saved Drafts artifact."""
    return bool(verification.ok and draft_id and verification.matched_artifact_id == draft_id)


def _reply_attachment_details_requested(verification: _ReplyDraftVerification) -> bool:
    """Return whether attachment details describe a requested attachment check."""
    return bool(verification.attachment_status and verification.attachment_status != "not_requested")


def _format_reply_verification_lines(
    verification: _ReplyDraftVerification,
    fallback_draft_id: str | None,
    *,
    retyped: bool = False,
    stale_artifact_id: str | None = None,
) -> str:
    """Return stable success metadata lines for a verified reply draft."""
    verified_id = verification.matched_artifact_id or fallback_draft_id or ""
    lines = [
        f"Verification Status: {verification.status}",
    ]
    if verified_id:
        lines.append(f"Verified Draft ID: {verified_id}")
    if verification.ok and fallback_draft_id and verified_id and verified_id != fallback_draft_id:
        lines.append(
            "Warning: saved draft was verified by bounded Drafts fallback, not by the exact Draft ID from the compose result"
        )
    if verification.status == "found":
        lines.append("Body Verification: full body matched above quote (case-sensitive)")
    if retyped:
        lines.append("Note: reply body was retyped once after an initial mismatch")
    if stale_artifact_id:
        lines.append(
            f"Warning: original draft {stale_artifact_id} may still exist in Drafts; its deletion "
            "before retyping was not confirmed"
        )
    if verification.attachment_status:
        lines.append(f"Attachment Verification Status: {verification.attachment_status}")
        if _reply_attachment_details_requested(verification) and verification.attachment_count is not None:
            lines.append(f"Attachments Applied Count: {verification.attachment_count}")
        if _reply_attachment_details_requested(verification) and verification.attachments_applied:
            lines.append("Attachments Applied:")
            for attachment in verification.attachments_applied:
                filename = attachment.get("filename") or ""
                size = attachment.get("size")
                size_text = f" ({size} bytes)" if size is not None else ""
                lines.append(f"  {filename}{size_text}")
        if verification.attachment_status in {"missing", "unsupported"}:
            lines.append("Warning: requested attachments could not be verified on the saved draft")
    if verification.signature_status:
        lines.append(f"Signature Verification Status: {verification.signature_status}")
        if verification.signature_status == "missing":
            lines.append("Warning: requested Mail signature was not detected above the quoted original")
    return "\n".join(lines) + "\n"


def _reply_success_payload(
    *,
    mode: str,
    reply_subject: str | None,
    draft_id: str | None,
    verification: _ReplyDraftVerification,
    captured_draft_id_source: str = "mail_returned",
    retyped: bool = False,
    stale_artifact_id: str | None = None,
) -> dict[str, Any]:
    """Return the machine-readable success contract for verified reply drafts."""
    verified_id = verification.matched_artifact_id or draft_id
    response_draft_id = draft_id or verified_id
    if draft_id:
        draft_id_source = captured_draft_id_source
    elif verified_id:
        draft_id_source = "verification_fallback"
    else:
        draft_id_source = "unavailable"
    return {
        "mode": mode,
        "sent": False,
        "subject": reply_subject or "",
        "draft_id": response_draft_id,
        "captured_draft_id": draft_id,
        "draft_id_source": draft_id_source,
        "verified_draft_id": verified_id,
        "verification_status": verification.status,
        "exact_id_verified": _reply_exact_id_verified(verification, draft_id),
        "body_present": verification.status == "found",
        "body_verified": "full_above_quote",
        "retyped": retyped,
        "stale_artifact_id": stale_artifact_id,
        "attachment_status": verification.attachment_status,
        "attachment_count": verification.attachment_count,
        "attachments_applied": verification.attachments_applied or [],
        "signature_status": verification.signature_status,
        "mailbox": "Drafts",
    }


def _format_forward_verification_lines(
    raw_verification: str,
    fallback_draft_id: str,
) -> str:
    """Return stable success metadata lines for a verified forward draft."""
    try:
        payload = json.loads(raw_verification)
    except json.JSONDecodeError:
        return "Verification Status: error\nWarning: saved forward draft verification returned invalid JSON\n"

    warnings = payload.get("warnings") or []
    found = payload.get("found") is True
    if found and warnings:
        status = "found_with_warnings"
    elif found:
        status = "found"
    else:
        status = "not_found"
    verified_id = str(payload.get("draft_id") or fallback_draft_id)
    lines = [f"Verification Status: {status}"]
    if verified_id:
        lines.append(f"Verified Draft ID: {verified_id}")
    if payload.get("error"):
        lines.append(f"Verification Error: {payload['error']}")
    if warnings:
        lines.append("Verification Warnings: " + ", ".join(str(item) for item in warnings))
    return "\n".join(lines) + "\n"


def _reply_draft_verification_error(
    verification: _ReplyDraftVerification,
    *,
    mode_text: str,
    reply_body: str,
    retyped: bool = False,
) -> str:
    """Serialize a structured draft-verification failure when an artifact id is known.

    Handles ``body_after_quote``, ``verification_timeout``, and
    ``applescript_error``. ``body_missing`` is the truncated/miscased-body
    defect this branch (AGENTIC-1214) exists to catch and is dispatched to
    ``_reply_body_mismatch_error`` instead; see ``_reply_verification_failure_response``.
    """
    artifact_id = verification.body_missing_artifact_id or verification.error_artifact_id
    if not artifact_id:
        return (
            f"Error: Reply draft was {mode_text}, but Mail did not verify it in the newest Drafts "
            "window. No email was sent. Please check Mail Drafts and retry after Mail finishes saving."
        )

    if verification.status == "body_after_quote":
        code = "REPLY_DRAFT_BODY_AFTER_QUOTE"
        detail = "contains the inserted reply body after the quoted original instead of above it"
    elif verification.status == "quoted_original_missing":
        code = "REPLY_QUOTED_ORIGINAL_MISSING"
        detail = "contains the inserted reply body but no longer contains the quoted original"
    elif verification.status == "attachment_verification_failed":
        code = "REPLY_DRAFT_ATTACHMENT_VERIFICATION_FAILED"
        detail = "does not contain every requested attachment"
    elif verification.status == "verification_timeout":
        code = "REPLY_DRAFT_VERIFICATION_TIMEOUT"
        detail = "could not be verified before the verifier timed out"
    else:
        code = "REPLY_DRAFT_VERIFICATION_ERROR"
        detail = "could not be verified because Mail returned a verifier error"

    failure_needs_verified_identity = verification.status in {
        "quoted_original_missing",
        "attachment_verification_failed",
    }
    identity_verified = verification.artifact_identity_verified
    remediation: dict[str, Any] = {
        "mailbox": "Drafts",
        "verification_status": verification.status,
        "expected_body_needle": _first_non_empty_line(reply_body),
        "retyped": retyped,
    }
    if failure_needs_verified_identity and not identity_verified:
        remediation.update(
            {
                "suspect_artifact_message_id": artifact_id,
                "artifact_identity_verified": False,
                "preferred": (
                    "Inspect the suspected Drafts artifact and retry after Mail finishes saving. "
                    "Do not delete it automatically because persisted reply identity was not verified."
                ),
            }
        )
    else:
        remediation.update(
            {
                "artifact_message_id": artifact_id,
                "draft_id": artifact_id,
                "artifact_identity_verified": identity_verified,
                "preferred": (
                    "Inspect or delete the artifact by exact Drafts message_id, then retry after Mail finishes saving."
                ),
            }
        )
    if verification.status == "attachment_verification_failed":
        remediation.update(
            {
                "attachment_status": verification.attachment_status,
                "attachment_count": verification.attachment_count,
                "attachments_applied": verification.attachments_applied or [],
            }
        )

    artifact_description = (
        f"saved Drafts artifact {artifact_id}"
        if identity_verified or not failure_needs_verified_identity
        else f"suspected Drafts artifact {artifact_id}"
    )
    return serialize_tool_error(
        ToolError(
            code=code,
            message=(
                f"Reply draft was {mode_text}, but {artifact_description} {detail}. No email was sent."
                + (" An automatic retype was attempted once and still did not resolve this." if retyped else "")
            ),
            remediation=remediation,
        )
    )


def _reply_body_mismatch_error(
    verification: _ReplyDraftVerification,
    *,
    mode_text: str,
    reply_body: str,
    retyped: bool,
    stale_artifact_id: str | None = None,
) -> str:
    """Serialize REPLY_BODY_MISMATCH.

    Fires when the saved draft's full body above the quote (whitespace-
    flattened, smart-punctuation-folded, compared case-sensitively) does not
    match the requested ``reply_body`` (AGENTIC-1214 Bug 1 truncation / Bug 3
    ALL CAPS). Fired only for ``status == "body_missing"``; see
    ``_reply_verification_failure_response`` for the dispatch that keeps
    ``not_found`` / ``verification_timeout`` / ``applescript_error`` /
    ``body_after_quote`` on ``_reply_draft_verification_error`` instead.

    Names the artifact as deletable only when
    ``artifact_identity_verified`` -- the same gate
    ``_reply_draft_verification_error`` applies to its own statuses.
    """
    artifact_id = verification.body_missing_artifact_id or verification.matched_artifact_id
    identity_verified = verification.artifact_identity_verified
    remediation: dict[str, Any] = {
        "mailbox": "Drafts",
        "verification_status": verification.status,
        "artifact_identity_verified": identity_verified,
        "retyped": retyped,
        "expected_body_preview": _first_non_empty_line(reply_body),
    }
    if identity_verified:
        remediation.update(
            {
                "artifact_message_id": artifact_id,
                "draft_id": artifact_id,
                "preferred": (
                    "Inspect the draft with verify_draft(draft_id=...). If the body is truncated or in the "
                    "wrong case, delete it with manage_drafts(action='delete', draft_id=..., "
                    "expected_in_reply_to=..., expected_subject=..., expected_to=...) and retry with "
                    "Mail visible and holding focus."
                ),
                "cleanup": (
                    "Delete the artifact by exact Drafts id before retrying so a duplicate draft is not left "
                    "behind. Pass the guarded form's expected_in_reply_to, expected_subject, and expected_to "
                    "together so Mail re-checks the row's identity at delete time."
                ),
            }
        )
    else:
        # The id came from the bounded newest-Drafts fallback, not from the
        # compose that just ran -- either Mail never handed back a draft id, or
        # the verifier resolved a different row than the one it did hand back.
        # This path used to publish it as `draft_id` under "delete it with
        # manage_drafts(action='delete', draft_id=...)", which is the same
        # deletion the retry logic upstream refuses for exactly this reason: it
        # cannot prove it created that row. An agent following the instruction
        # deletes whatever else is sitting in Drafts under a similar subject,
        # and a deleted draft is user-authored text with no undo. The shipped
        # skills already say a fallback-discovered draft is not cleanup
        # authorization; this is the string that was overriding them at the
        # point of use.
        remediation.update(
            {
                "suspect_artifact_message_id": artifact_id,
                "preferred": (
                    "Inspect the suspected Drafts artifact with verify_draft(draft_id=...) and retry after "
                    "Mail finishes saving. Do not delete it automatically because persisted reply identity "
                    "was not verified."
                ),
            }
        )
    if stale_artifact_id:
        remediation["stale_artifact_id"] = stale_artifact_id
        remediation["stale_artifact_warning"] = (
            f"The first attempt's draft {stale_artifact_id} could not be confirmed deleted before "
            "retyping; it may still exist in Drafts as a truncated or miscased duplicate. Inspect and "
            "delete it by exact id."
        )
    return serialize_tool_error(
        ToolError(
            code="REPLY_BODY_MISMATCH",
            message=(
                f"Reply draft was {mode_text}, but the "
                f"{'saved' if identity_verified else 'suspected'} Drafts artifact "
                f"{artifact_id or '(id unavailable)'} does not contain the full reply body above the "
                "quoted original when compared case-sensitively with whitespace and smart-punctuation "
                "normalized. This indicates the typed body was truncated or miscased. No email was sent."
                + (" An automatic retype was attempted once and still did not match." if retyped else "")
            ),
            remediation=remediation,
        )
    )


def _reply_verification_failure_response(
    verification: _ReplyDraftVerification,
    *,
    mode_text: str,
    reply_body: str,
    retyped: bool,
    stale_artifact_id: str | None = None,
) -> str:
    """Map a failed reply-draft verification to its final structured error response.

    ``body_missing`` alone routes to ``REPLY_BODY_MISMATCH``; every other
    status (``body_after_quote``, ``not_found``, ``verification_timeout``,
    ``applescript_error``) keeps ``_reply_draft_verification_error``'s
    pre-existing codes.
    """
    if verification.status == "body_missing":
        return _reply_body_mismatch_error(
            verification,
            mode_text=mode_text,
            reply_body=reply_body,
            retyped=retyped,
            stale_artifact_id=stale_artifact_id,
        )
    return _reply_draft_verification_error(
        verification,
        mode_text=mode_text,
        reply_body=reply_body,
        retyped=retyped,
    )
