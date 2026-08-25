"""``REPLY_BODY_MISMATCH`` may only name a delete target it can prove.

The body-mismatch remediation used to publish the suspected artifact as
``draft_id`` and tell the agent to "delete it with
manage_drafts(action='delete', draft_id=...)" -- unconditionally, including
when the id came from the bounded newest-Drafts fallback rather than from the
compose that just ran.

That is the same deletion the retry path upstream refuses in exactly this case
(``test_native_reply_retry_skipped_when_mismatch_artifact_differs_from_draft_id``
asserts "retry must not delete when artifact id differs from compose draft
id"), so one payload carried both the refusal and the instruction. It also
contradicted the shipped skills, which already say a fallback-discovered
same-subject draft is not cleanup authorization -- and an error's own
remediation is what an agent acts on, so the string won.

A wrongly deleted draft is user-authored text with no undo, and body
truncation is the most common native-reply failure, so this is the path most
likely to be walked.

The sibling ``_reply_draft_verification_error`` already gated on
``artifact_identity_verified``. These tests pin that both builders do.
"""

from __future__ import annotations

import json
import unittest

from apple_mail_mcp.tools.compose.verification import (
    _reply_body_mismatch_error,
    _ReplyDraftVerification,
)


def _mismatch(*, artifact_id: str | None, identity_verified: bool) -> dict:
    verification = _ReplyDraftVerification(
        ok=False,
        status="body_missing",
        body_missing_artifact_id=artifact_id,
        artifact_identity_verified=identity_verified,
    )
    return json.loads(
        _reply_body_mismatch_error(
            verification,
            mode_text="saved as a draft",
            reply_body="Thanks, that works for me.",
            retyped=False,
        )
    )


class UnverifiedArtifactTests(unittest.TestCase):
    """Identity unproven: report it, never hand it over as a delete target."""

    def test_no_delete_target_is_published(self) -> None:
        remediation = _mismatch(artifact_id="55555", identity_verified=False)["remediation"]
        self.assertEqual(remediation["suspect_artifact_message_id"], "55555")
        self.assertNotIn("draft_id", remediation)
        self.assertNotIn("artifact_message_id", remediation)

    def test_the_agent_is_told_not_to_delete(self) -> None:
        remediation = _mismatch(artifact_id="55555", identity_verified=False)["remediation"]
        self.assertIn("Do not delete it automatically", remediation["preferred"])
        self.assertNotIn("manage_drafts(action='delete'", remediation["preferred"])
        # The old key carried a bare "Delete the suspected artifact by exact
        # Drafts id" directive of its own; it must not survive here either.
        self.assertNotIn("cleanup", remediation)

    def test_identity_is_reported_so_the_caller_can_see_why(self) -> None:
        remediation = _mismatch(artifact_id="55555", identity_verified=False)["remediation"]
        self.assertIs(remediation["artifact_identity_verified"], False)

    def test_the_message_calls_the_artifact_suspected(self) -> None:
        payload = _mismatch(artifact_id="55555", identity_verified=False)
        self.assertIn("suspected Drafts artifact 55555", payload["message"])

    def test_a_missing_id_is_also_unverified(self) -> None:
        """No id at all cannot authorize a delete either."""
        payload = _mismatch(artifact_id=None, identity_verified=False)
        self.assertNotIn("draft_id", payload["remediation"])
        self.assertIn("(id unavailable)", payload["message"])


class VerifiedArtifactTests(unittest.TestCase):
    """Identity proven: the artifact is ours, so cleanup guidance stays."""

    def test_the_delete_target_is_published(self) -> None:
        remediation = _mismatch(artifact_id="91061", identity_verified=True)["remediation"]
        self.assertEqual(remediation["draft_id"], "91061")
        self.assertEqual(remediation["artifact_message_id"], "91061")
        self.assertNotIn("suspect_artifact_message_id", remediation)

    def test_it_steers_to_the_guarded_delete_form(self) -> None:
        """manage_drafts re-checks identity when all three expected_* are passed.

        A bare ``draft_id`` delete is accepted by that tool, so naming only
        ``draft_id`` here would spend a proof the caller already has.
        """
        remediation = _mismatch(artifact_id="91061", identity_verified=True)["remediation"]
        for field in ("expected_in_reply_to", "expected_subject", "expected_to"):
            self.assertIn(field, remediation["preferred"])
        self.assertIn("together", remediation["cleanup"])

    def test_the_message_calls_the_artifact_saved(self) -> None:
        payload = _mismatch(artifact_id="91061", identity_verified=True)
        self.assertIn("saved Drafts artifact 91061", payload["message"])


class SharedContractTests(unittest.TestCase):
    def test_the_code_is_unchanged_on_both_branches(self) -> None:
        for identity_verified in (True, False):
            with self.subTest(identity_verified=identity_verified):
                payload = _mismatch(artifact_id="91061", identity_verified=identity_verified)
                self.assertEqual(payload["code"], "REPLY_BODY_MISMATCH")
                self.assertEqual(payload["remediation"]["mailbox"], "Drafts")
                self.assertEqual(payload["remediation"]["verification_status"], "body_missing")
                self.assertEqual(
                    payload["remediation"]["expected_body_preview"],
                    "Thanks, that works for me.",
                )


if __name__ == "__main__":
    unittest.main()
