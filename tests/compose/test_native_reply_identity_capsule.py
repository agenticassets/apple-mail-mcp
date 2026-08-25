"""Round-trip the native reply Drafts identity capsule: emitter -> parser.

WHY THIS MODULE EXISTS
----------------------
``reply_scripts.py`` builds the ``Draft Identity:`` line in AppleScript and
``reply_identity.py`` parses it back in Python. Nothing bound the two, and they
silently drifted: the script emitted four fields ending in ``rfc`` or
``transaction``, while the parser accepted a four-field line only when the
fourth field was literally ``transaction`` AND both id fields were empty. Every
real ``rfc`` capsule parsed to ``None``, and so did every ``transaction``
capsule whose source message had a Message-ID, because the third field was the
top-level ``sourceRfcMessageId`` rather than the item-3 value the resolver had
actually proved.

The failure was invisible because every fixture in the suite hand-wrote a
capsule shape the script could not emit (a legacy three-field line). The tests
passed against fiction while `reply_to_email` lost its exact Drafts id on
essentially every native reply: attachment-bearing replies returned
``IDENTITY_UNAVAILABLE`` for correctly saved drafts, ``exact_id_verified`` was
always false, and the delete-and-retype retry was unreachable.

So this module hand-writes nothing. It reads the emit order out of the
generated script and the tuple-slot assignments out of the resolver, assembles
the line the way AppleScript would, and feeds it to the shipped parser. Reorder
the emit, swap a variable, or change a slot, and this fails.
"""

from __future__ import annotations

import re
import unittest

from apple_mail_mcp.tools.compose.reply_draft_resolver_scripts import (
    _native_reply_draft_resolver_handlers_applescript,
    _native_reply_draft_resolver_script,
)
from apple_mail_mcp.tools.compose.reply_identity import (
    native_reply_draft_identity_from_output,
)

_CAPSULE_PREFIX = "Draft Identity: "

# The two identities persistedReplyDraftIdentity can return, as {item 1 .. item 4}.
# Slot meanings: draft id, draft RFC Message-ID, PROVEN source RFC Message-ID, evidence.
_RFC_TUPLE = ("91061", "<draft-91061@example.com>", "<source@example.com>", "rfc")
_TRANSACTION_TUPLE = ("84053", "", "", "transaction")


def _build_native_reply_script(*, mode: str) -> str:
    from apple_mail_mcp.tools.compose import reply_scripts as m

    return m._build_reply_native_window_applescript(
        header_text="SAVING REPLY AS DRAFT",
        success_text="Reply saved as draft!",
        safe_account="Test Account",
        mailbox_lookup='set sourceMailbox to mailbox "Inbox" of targetAccount',
        lookup_script="set foundMessage to missing value",
        not_found_message="Email not found",
        body_temp_path="/tmp/apple-mail-capsule-body.txt",
        reply_options="with opening window",
        sender_script="",
        signature_script="",
        cc_script="",
        bcc_script="",
        attachment_script="",
        mode=mode,
        cleanup_script="",
        safe_cc="",
        safe_bcc="",
        safe_attachment_info="",
        has_cc=False,
        has_bcc=False,
        has_attachments=False,
    )


def _capsule_emit_variables(script: str) -> list[str]:
    """Return the AppleScript variables the capsule line concatenates, in order.

    The emit looks like::

        ... & "Draft Identity: " & replyDraftId & "|||" & replyDraftRfcMessageId & ...

    so the variables are the ``& name &`` tokens between the ``"|||"`` literals.
    """
    for line in script.splitlines():
        if f'"{_CAPSULE_PREFIX}"' not in line:
            continue
        tail = line.split(f'"{_CAPSULE_PREFIX}"', 1)[1]
        # Stop at the trailing `& return`; everything before it is the capsule.
        tail = tail.rsplit("& return", 1)[0]
        return re.findall(r"&\s*([A-Za-z_][A-Za-z0-9_]*)\s*&", tail + " & ")
    raise AssertionError("native reply script emits no Draft Identity capsule")


def _resolver_slot_by_variable(resolver_script: str) -> dict[str, int]:
    """Map each capsule variable to the ``replyDraftIdentity`` item it is set from."""
    pattern = r"set\s+([A-Za-z_][A-Za-z0-9_]*)\s+to\s+item\s+(\d+)\s+of\s+replyDraftIdentity"
    return {name: int(index) for name, index in re.findall(pattern, resolver_script)}


def _capsule_line_for(tuple_value: tuple[str, str, str, str]) -> str:
    """Assemble the capsule the way the shipped script would, for one identity."""
    emit_variables = _capsule_emit_variables(_build_native_reply_script(mode="draft"))
    slots = _resolver_slot_by_variable(_native_reply_draft_resolver_script())
    missing = [name for name in emit_variables if name not in slots]
    if missing:
        raise AssertionError(
            f"capsule emits {missing}, which the resolver never assigns from replyDraftIdentity. "
            "Emitting a variable the resolver did not prove is how the third field drifted "
            "to the unproven top-level sourceRfcMessageId."
        )
    return _CAPSULE_PREFIX + "|||".join(tuple_value[slots[name] - 1] for name in emit_variables)


class NativeReplyIdentityCapsuleRoundTripTests(unittest.TestCase):
    def test_rfc_identity_round_trips(self):
        identity = native_reply_draft_identity_from_output(_capsule_line_for(_RFC_TUPLE))
        self.assertIsNotNone(
            identity,
            "The parser rejected the capsule the shipped script emits for an RFC-proved "
            "draft. That silently disables exact-id verification, blocks every "
            "attachment-bearing native reply with IDENTITY_UNAVAILABLE, and makes the "
            "delete-and-retype retry unreachable.",
        )
        assert identity is not None
        self.assertEqual(identity.draft_id, _RFC_TUPLE[0])
        self.assertEqual(identity.draft_rfc_message_id, _RFC_TUPLE[1])
        self.assertEqual(identity.source_rfc_message_id, _RFC_TUPLE[2])
        self.assertEqual(identity.evidence, "rfc")
        self.assertTrue(identity.is_rfc_backed)

    def test_transaction_identity_round_trips_but_cannot_authorize_mutation(self):
        identity = native_reply_draft_identity_from_output(_capsule_line_for(_TRANSACTION_TUPLE))
        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(identity.draft_id, _TRANSACTION_TUPLE[0])
        self.assertEqual(identity.evidence, "transaction")
        self.assertFalse(
            identity.is_rfc_backed,
            "Count-plus-one evidence must never authorize delete-and-retype.",
        )

    def test_capsule_carries_the_proved_source_id_not_the_unverified_one(self):
        """The third field must come from the resolver tuple, not the ambient read.

        ``sourceRfcMessageId`` is whatever was read off the source message before
        saving. ``persistedReplyDraftIdentity`` only puts an id in item 3 after
        proving the saved draft's In-Reply-To header carries that exact token.
        Emitting the ambient one made every transaction capsule carry a non-empty
        third field, which is precisely the malformed shape the parser rejects.
        """
        script = _build_native_reply_script(mode="draft")
        emit_variables = _capsule_emit_variables(script)
        self.assertNotIn(
            "sourceRfcMessageId",
            emit_variables,
            "capsule emits the unproven ambient source id; emit item 3 of replyDraftIdentity",
        )
        slots = _resolver_slot_by_variable(_native_reply_draft_resolver_script())
        self.assertEqual(slots[emit_variables[2]], 3)

    def test_resolver_returns_only_the_two_evidence_classes_the_parser_accepts(self):
        """Every evidence literal the handlers can return must parse.

        A new evidence class added in AppleScript without a matching parser
        branch would fall through to ``return None`` — the same silent-drift
        failure this module exists to prevent.
        """
        handlers = _native_reply_draft_resolver_handlers_applescript()
        emitted = set(re.findall(r'return\s+\{[^}]*,\s*"(\w+)"\s*\}', handlers))
        self.assertEqual(emitted, {"rfc", "transaction"})

    def test_every_mode_emits_a_parseable_capsule(self):
        for mode in ("draft", "open", "send"):
            with self.subTest(mode=mode):
                emit_variables = _capsule_emit_variables(_build_native_reply_script(mode=mode))
                self.assertEqual(len(emit_variables), 4)


class NativeReplyIdentityCapsuleRejectionTests(unittest.TestCase):
    """Malformed capsules must still be refused, not coerced into an identity."""

    def test_rejects_rfc_evidence_without_both_message_ids(self):
        line = _CAPSULE_PREFIX + "|||".join(("91061", "<draft@example.com>", "", "rfc"))
        self.assertIsNone(native_reply_draft_identity_from_output(line))

    def test_rejects_transaction_evidence_carrying_a_message_id(self):
        line = _CAPSULE_PREFIX + "|||".join(("91061", "", "<source@example.com>", "transaction"))
        self.assertIsNone(native_reply_draft_identity_from_output(line))

    def test_rejects_unknown_evidence_class(self):
        line = _CAPSULE_PREFIX + "|||".join(("91061", "<a@example.com>", "<b@example.com>", "assumed"))
        self.assertIsNone(native_reply_draft_identity_from_output(line))

    def test_rejects_non_numeric_draft_id(self):
        line = _CAPSULE_PREFIX + "|||".join(("not-an-id", "<a@example.com>", "<b@example.com>", "rfc"))
        self.assertIsNone(native_reply_draft_identity_from_output(line))

    def test_rejects_legacy_three_field_capsule(self):
        """The script cannot emit three fields; accepting them re-opens the drift."""
        line = _CAPSULE_PREFIX + "|||".join(("91061", "<a@example.com>", "<b@example.com>"))
        self.assertIsNone(native_reply_draft_identity_from_output(line))


if __name__ == "__main__":
    unittest.main()
