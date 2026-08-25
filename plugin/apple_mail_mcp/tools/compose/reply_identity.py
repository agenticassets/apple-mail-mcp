"""Native reply Drafts identity capsules.

The native AppleScript emits an RFC-backed capsule when Mail has persisted
both message identifiers. iCloud can defer the outgoing Message-ID, so a
second capsule type represents only one bounded, count-plus-one Drafts
transaction. That temporary proof may verify this call's exact numeric row;
it is never sufficient for a later mutation such as delete-and-retype.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class NativeReplyDraftIdentity:
    """Exact Drafts evidence returned by the native reply operation."""

    draft_id: str
    draft_rfc_message_id: str
    source_rfc_message_id: str
    evidence: str = "rfc"

    @property
    def is_rfc_backed(self) -> bool:
        """Return whether this identity can safely authorize a later mutation."""
        return self.evidence == "rfc"


def native_reply_draft_identity_from_output(output: str) -> NativeReplyDraftIdentity | None:
    """Parse a valid native Drafts identity capsule, otherwise return None.

    The capsule is always FOUR pipe-separated fields, and the fourth names the
    evidence class, so that is what selects the validation rule. Reading the
    fourth field as "must literally be ``transaction``" instead rejected every
    ``rfc`` capsule — the strong one — and every ``transaction`` capsule whose
    source message had a Message-ID, which left `reply_to_email` with no exact
    Drafts id on essentially every native reply: attachment-bearing replies
    failed verification with ``IDENTITY_UNAVAILABLE`` despite a correctly saved
    draft, ``exact_id_verified`` was always false, and the delete-and-retype
    retry was unreachable. Keep the emitter in ``reply_scripts.py`` and this
    parser in step; ``test_native_reply_identity_capsule.py`` round-trips them.
    """
    prefix = "Draft Identity: "
    for line in output.splitlines():
        if not line.startswith(prefix):
            continue
        parts = [part.strip() for part in line[len(prefix) :].split("|||")]
        if len(parts) != 4:
            return None
        draft_id, draft_rfc_message_id, source_rfc_message_id, evidence = parts
        if not draft_id.isdigit():
            return None
        if evidence == "rfc":
            # The resolver only reports "rfc" after proving the saved draft's
            # In-Reply-To header carries this exact source id, so both ids must
            # be present and well-formed for the capsule to be self-consistent.
            if not _is_rfc_message_id(draft_rfc_message_id) or not _is_rfc_message_id(source_rfc_message_id):
                return None
            return NativeReplyDraftIdentity(draft_id, draft_rfc_message_id, source_rfc_message_id)
        if evidence == "transaction":
            # Count-plus-one evidence only. Carrying an id here would imply an
            # RFC link that was never checked, so a non-empty id is malformed.
            if draft_rfc_message_id or source_rfc_message_id:
                return None
            return NativeReplyDraftIdentity(draft_id, "", "", evidence="transaction")
        return None
    return None


def _is_rfc_message_id(value: str) -> bool:
    """Return whether ``value`` has the unambiguous angle-bracket RFC-ID form."""
    return len(value) > 2 and value.startswith("<") and value.endswith(">") and " " not in value
