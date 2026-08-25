"""Transaction-scoped AppleScript for attachment-bearing forward drafts."""

from pathlib import Path

from apple_mail_mcp.core import escape_applescript
from apple_mail_mcp.tools.compose.attachment_draft_verification import quoted_applescript_list
from apple_mail_mcp.tools.compose.standalone_draft_identity_scripts import (
    _bind_marked_draft_by_saved_id_fragment,
    _refresh_numeric_saved_draft_id,
    _unique_subject_bind_retry,
    standalone_exact_marker_draft_scan,
    standalone_exact_subject_expr_draft_scan,
)


def forward_marker_draft_verification_handlers() -> str:
    """Verify a marked forward while allowing only Mail's Outlook PNG asset."""
    return """
using terms from application "Mail"
on forwardMarkerInlineSignatureAsset(attachmentName)
    ignoring case
        return attachmentName starts with "Outlook-" and attachmentName ends with ".png"
    end ignoring
end forwardMarkerInlineSignatureAsset

on forwardMarkerDraftProof(draftMessage, expectedTo, expectedCc, expectedBcc, expectedSubject, expectedMarker, expectedBody, expectedAttachmentNames)
    try
        set storedSubject to subject of draftMessage as string
        if storedSubject is expectedMarker then return "subject_mismatch"
        if storedSubject is not expectedSubject then return "subject_mismatch"
        if my markerRecipientSetMatches(to recipients of draftMessage, expectedTo) is false then return "recipient_mismatch"
        if my markerRecipientSetMatches(cc recipients of draftMessage, expectedCc) is false then return "cc_recipient_mismatch"
        if my markerRecipientSetMatches(bcc recipients of draftMessage, expectedBcc) is false then return "bcc_recipient_mismatch"
        if (content of draftMessage as string) does not contain expectedBody then return "body_mismatch"
        set savedAttachments to mail attachments of draftMessage
        set remainingAttachmentNames to {}
        repeat with savedAttachment in savedAttachments
            set attachmentSize to file size of savedAttachment as integer
            if attachmentSize is less than or equal to 0 then return "attachment_unreadable"
            set end of remainingAttachmentNames to (name of savedAttachment as string)
        end repeat
        repeat with expectedAttachmentName in expectedAttachmentNames
            set matchIndex to 0
            repeat with attachmentIndex from 1 to count of remainingAttachmentNames
                if (item attachmentIndex of remainingAttachmentNames as string) is (expectedAttachmentName as string) then
                    set matchIndex to attachmentIndex
                    exit repeat
                end if
            end repeat
            if matchIndex is 0 then return "attachment_mismatch"
            set item matchIndex of remainingAttachmentNames to missing value
        end repeat
        repeat with remainingAttachmentName in remainingAttachmentNames
            if remainingAttachmentName is not missing value then
                if my forwardMarkerInlineSignatureAsset(remainingAttachmentName as string) is false then return "attachment_mismatch"
            end if
        end repeat
        return "verified"
    on error
        return "unavailable"
    end try
end forwardMarkerDraftProof
end using terms from
"""


def forward_restore_outgoing_subject_script() -> str:
    """Restore ``fwdSubject`` on the live outgoing message before the first save."""
    return """
        set subject of forwardMessage to fwdSubject
        set restoredForwardSubject to subject of forwardMessage as string
        if restoredForwardSubject is not fwdSubject then error "FORWARD_SUBJECT_RESTORE_FAILED"
"""


def forward_marker_draft_proof_call(
    *,
    to_addresses: list[str],
    cc_addresses: list[str],
    bcc_addresses: list[str],
    marker: str,
    body: str,
    attachment_paths: list[str],
) -> str:
    """Build the strict proof call for the saved real-subject forward Drafts row."""
    attachment_names = quoted_applescript_list(Path(path).name for path in attachment_paths)
    return (
        "set forwardAttachmentProof to my forwardMarkerDraftProof(markedForwardDraft, "
        f"{{{quoted_applescript_list(to_addresses)}}}, {{{quoted_applescript_list(cc_addresses)}}}, "
        f"{{{quoted_applescript_list(bcc_addresses)}}}, fwdSubject, "
        f'"{escape_applescript(marker)}", "{escape_applescript(body)}", {{{attachment_names}}})'
    )


def forward_marker_finalize_script(marker: str, proof_script: str) -> str:
    """Bind the already-saved real-subject forward, then run attachment proof.

    Caller must restore ``subject of forwardMessage`` to ``fwdSubject`` and
    save before this fragment. Never writes saved ``message.subject``. A unique
    leftover marker Drafts row is a leak: fail closed. Identity prefers the
    pre-save snapshot, then a unique exact ``fwdSubject`` row.
    """
    marker_scan = standalone_exact_marker_draft_scan(marker, list_var="leakedForwardMarkerDrafts")
    subject_scan = standalone_exact_subject_expr_draft_scan(
        "fwdSubject",
        list_var="markedForwardDrafts",
        exclude_ids_var="preSaveForwardDraftIds",
    )
    bind_id = _bind_marked_draft_by_saved_id_fragment("markedForwardDraft")
    return f"""
        set forwardAttachmentProof to "identity_unavailable"
        set markedForwardDraft to missing value
        -- Drafts ids that existed before this forward saved anything. "Fwd: <subject>"
        -- is exactly what a previous forward of the same message leaves behind, so
        -- the subject bind must never be able to select one of these rows.
        -- ``fullDraftRfcSnapshot`` returns either ``missing value`` or a 3-item
        -- list, so the guard is the whole check; no ``try`` needed.
        set preSaveForwardDraftIds to {{}}
        if preSaveDraftSnapshot is not missing value then set preSaveForwardDraftIds to item 3 of preSaveDraftSnapshot
        try
            if draftsMailbox is not missing value then
                {marker_scan}
                if (count of leakedForwardMarkerDrafts) is greater than 0 then error "FORWARD_SUBJECT_RESTORE_FAILED"
                if savedDraftId is not "" then
                    {bind_id}
                end if
                if markedForwardDraft is missing value then
                    {_unique_subject_bind_retry(subject_scan, "markedForwardDrafts", "markedForwardDraft")}
                end if
            end if
            if markedForwardDraft is not missing value then
                {proof_script}
                if forwardAttachmentProof is not "verified" then error "FORWARD_ATTACHMENT_PROOF_FAILED: " & forwardAttachmentProof
                {_refresh_numeric_saved_draft_id("markedForwardDraft")}
            end if
            if forwardAttachmentProof is not "verified" then error "FORWARD_ATTACHMENT_PROOF_FAILED: " & forwardAttachmentProof
        on error errMsg
            -- Only delete a row whose identity this operation actually proved.
            -- "operation_exact_subject" means the row merely had the right
            -- subject; that is a locator, not evidence we created it, so a
            -- delete there can destroy a draft the user wrote on the same
            -- thread. Release the binding first so the fallback cannot act on
            -- it either, and leave the row alone.
            if savedDraftIdSource is "operation_exact_subject" then set markedForwardDraft to missing value
            try
                delete markedForwardDraft
            on error
                try
                    delete forwardMessage
                end try
            end try
            set savedDraftId to ""
            set forwardAttachmentProof to "finalization_failed"
            error errMsg
        end try
"""
