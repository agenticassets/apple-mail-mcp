"""Persisted Drafts identity scripts for standalone attachment drafts."""

from apple_mail_mcp.core import escape_applescript
from apple_mail_mcp.core.reply_state import drafts_mailbox_block
from apple_mail_mcp.tools.compose.constants import DRAFT_LIST_CAP

HTML_SUBJECT_MARKER_PREFIX = "__apple_mail_mcp_"


def _standalone_draft_identity_handlers() -> str:
    """Return handlers that identify one newly persisted Drafts message.

    Mail's outgoing-message ``id`` is an in-memory object identifier on some
    providers, including iCloud. A complete pre-compose Drafts snapshot and a
    post-save diff prove the actual Drafts message instead. RFC Message-ID is
    preferred when available. iCloud can leave it blank on unsent drafts, so a
    numeric Drafts-ID diff is the only fallback: it is bounded, requires the
    mailbox count to grow by exactly one, and rejects every ambiguous result.
    """
    return """
using terms from application "Mail"
on isNumericStandaloneDraftId(candidateId)
    if candidateId is "" then return false
    repeat with candidateCharacter in characters of candidateId
        if "0123456789" does not contain (candidateCharacter as string) then return false
    end repeat
    return true
end isNumericStandaloneDraftId

on fullDraftRfcSnapshot(draftsMailbox, draftCap)
    try
        set draftCount to count of messages of draftsMailbox
        if draftCount > draftCap then return missing value
        if draftCount is 0 then return {0, {}, {}}
        set draftMessages to messages 1 thru draftCount of draftsMailbox
        set rfcMessageIds to {}
        set numericDraftIds to {}
        repeat with aDraft in draftMessages
            set candidateDraftId to ""
            try
                set candidateDraftId to (id of aDraft) as string
            end try
            set rfcMessageId to ""
            try
                set rfcMessageId to message id of aDraft as string
            end try
            set end of rfcMessageIds to rfcMessageId
            set end of numericDraftIds to candidateDraftId
        end repeat
        return {draftCount, rfcMessageIds, numericDraftIds}
    on error
        return missing value
    end try
end fullDraftRfcSnapshot

on persistedStandaloneDraftId(draftsMailbox, beforeSnapshot, draftCap)
    try
        if beforeSnapshot is missing value then return {"", ""}
        set beforeCount to item 1 of beforeSnapshot
        set beforeRfcMessageIds to item 2 of beforeSnapshot
        set beforeNumericDraftIds to item 3 of beforeSnapshot
        set afterCount to count of messages of draftsMailbox
        if afterCount is not (beforeCount + 1) or afterCount > draftCap then return {"", ""}
        set afterDrafts to messages 1 thru afterCount of draftsMailbox
        set rfcCandidateIds to {}
        set numericCandidateIds to {}
        repeat with aDraft in afterDrafts
            set candidateDraftId to ""
            try
                set candidateDraftId to (id of aDraft) as string
            end try
            set rfcMessageId to ""
            try
                set rfcMessageId to message id of aDraft as string
            end try
            if rfcMessageId is not "" and beforeRfcMessageIds does not contain rfcMessageId then
                set end of rfcCandidateIds to candidateDraftId
            end if
            if my isNumericStandaloneDraftId(candidateDraftId) and beforeNumericDraftIds does not contain candidateDraftId then
                set end of numericCandidateIds to candidateDraftId
            end if
        end repeat
        if (count of rfcCandidateIds) is 1 then return {(item 1 of rfcCandidateIds as string), "rfc_message_id"}
        if (count of numericCandidateIds) is 1 then return {(item 1 of numericCandidateIds as string), "numeric_snapshot"}
    on error
        return {"", ""}
    end try
    return {"", ""}
end persistedStandaloneDraftId
end using terms from
"""


def standalone_draft_identity_setup_script() -> str:
    """Snapshot the complete bounded Drafts mailbox before compose creation."""
    drafts_resolver = drafts_mailbox_block(var_name="draftsMailbox", account_var="targetAccount")
    return f"""
            set preSaveDraftSnapshot to missing value
            {drafts_resolver}
            try
                if draftsMailbox is not missing value then
                    set preSaveDraftSnapshot to my fullDraftRfcSnapshot(draftsMailbox, {DRAFT_LIST_CAP})
                end if
            end try
"""


def standalone_draft_identity_resolver_script() -> str:
    """Resolve one new persisted Drafts ID after save, or emit no ID safely."""
    return f"""
            set savedDraftId to ""
            set savedDraftIdSource to ""
            try
                if preSaveDraftSnapshot is not missing value and draftsMailbox is not missing value then
                    -- iCloud may index the saved Drafts row after ``save`` returns, so
                    -- the resolver still spends the same 1.8s of total patience it
                    -- always did. It just probes BEFORE spending any of it. The old
                    -- shape paid an unconditional ``delay 0.8`` first, which every
                    -- local and Exchange save -- where the row is already there when
                    -- ``save`` returns -- burned for nothing. Probing at 0.0s, 0.8s,
                    -- 1.3s, 1.8s is a strict superset of the previous 0.8s, 1.3s,
                    -- 1.8s: same last probe, same deadline, one extra early chance.
                    set identityBackoff to {{0.8, 0.5, 0.5}}
                    repeat with identityAttempt from 1 to 4
                        set savedDraftIdentity to my persistedStandaloneDraftId(draftsMailbox, preSaveDraftSnapshot, {DRAFT_LIST_CAP})
                        set savedDraftId to item 1 of savedDraftIdentity as string
                        set savedDraftIdSource to item 2 of savedDraftIdentity as string
                        if savedDraftId is not "" then exit repeat
                        if identityAttempt is less than 4 then delay (item identityAttempt of identityBackoff)
                    end repeat
                end if
            on error
                set savedDraftId to ""
            end try
"""


def _bounded_draft_candidate_messages_fragment() -> str:
    """Head+tail bounded Drafts slice copied from ``_build_draft_lookup``."""
    return f"""
                        set totalDrafts to count of messages of draftsMailbox
                        set headEnd to totalDrafts
                        if headEnd > {DRAFT_LIST_CAP} then set headEnd to {DRAFT_LIST_CAP}
                        if totalDrafts is 0 then
                            set candidateMessages to {{}}
                        else
                            set candidateMessages to messages 1 thru headEnd of draftsMailbox
                            if totalDrafts > {DRAFT_LIST_CAP} then
                                set tailStart to totalDrafts - {DRAFT_LIST_CAP} + 1
                                if tailStart > headEnd then
                                    set candidateMessages to candidateMessages & (messages tailStart thru totalDrafts of draftsMailbox)
                                end if
                            end if
                        end if
"""


def _exact_subject_expr_draft_scan_fragment(
    subject_expr: str,
    list_var: str = "markedDrafts",
    *,
    exclude_ids_var: str = "",
) -> str:
    """Head+tail bounded exact-subject scan copied from ``_build_draft_lookup``.

    ``subject_expr`` is an AppleScript expression such as ``"literal"`` or
    ``fwdSubject``. Callers must require ``(count of list_var) is 1`` before
    mutating. Uses ``contents of`` when collecting list-item references so
    later mutations bind the message, not a dangling iterator.

    ``exclude_ids_var`` names an AppleScript list of numeric Drafts ids that
    existed BEFORE this operation saved anything; rows in it are skipped. Subject
    equality alone does not prove we created a row: "Fwd: <subject>" is exactly
    what a previous forward of the same message left in Drafts. While the newly
    saved row is still unindexed, an unfiltered scan sees only the OLD draft,
    binds it, fails the attachment proof against it, and — on the forward error
    path — deletes a message the caller never named. Pass the pre-save id list
    wherever one exists so a pre-existing row can never be the unique match.
    """
    candidates = _bounded_draft_candidate_messages_fragment()
    if exclude_ids_var:
        exclusion_setup = f"""
                        set excludedDraftIds to {exclude_ids_var}"""
        # No leading newline: the caller splices this after a line of its own so
        # ``try`` stays alone on its source line, which is what the bare-``try``
        # lint in tests/core/test_no_bare_applescript_try.py matches on.
        exclusion_guard = """\
                                -- Unguarded on purpose: the enclosing per-candidate
                                -- handler skips a row whose id will not read, and a row
                                -- we cannot identify must never become the unique match.
                                set candidateDraftIdText to (id of candidateDraft) as string
                                set candidateExistedBefore to false
                                repeat with priorDraftId in excludedDraftIds
                                    if (contents of priorDraftId as string) is candidateDraftIdText then
                                        set candidateExistedBefore to true
                                        exit repeat
                                    end if
                                end repeat
                                if candidateExistedBefore is false and (subject of candidateDraft as string) is {subject_expr} then"""
    else:
        exclusion_setup = ""
        exclusion_guard = """\
                                if (subject of candidateDraft as string) is {subject_expr} then"""
    subject_test = exclusion_guard.format(subject_expr=subject_expr)
    return f"""
{candidates}{exclusion_setup}
                        set {list_var} to {{}}
                        repeat with candidateDraft in candidateMessages
                            try
{subject_test}
                                    set end of {list_var} to contents of candidateDraft
                                end if
                            end try
                        end repeat
"""


def _exact_marker_draft_scan_fragment(safe_marker: str, list_var: str = "markedDrafts") -> str:
    """Head+tail bounded exact-marker scan; ``safe_marker`` is already escaped."""
    return _exact_subject_expr_draft_scan_fragment(f'"{safe_marker}"', list_var)


def _bind_marked_draft_by_saved_id_fragment(draft_var: str = "markedDraft") -> str:
    """Bind ``draft_var`` to the snapshot id using the same bounded scan."""
    candidates = _bounded_draft_candidate_messages_fragment()
    return f"""
{candidates}
                        repeat with candidateDraft in candidateMessages
                            try
                                if (id of candidateDraft as string) is savedDraftId then
                                    set {draft_var} to contents of candidateDraft
                                    exit repeat
                                end if
                            end try
                        end repeat
"""


def _unique_subject_bind_retry(scan: str, list_var: str, draft_var: str) -> str:
    """Retry a unique exact-subject bind up to four times."""
    return f"""
                    repeat with subjectAttempt from 1 to 4
                        {scan}
                        if (count of {list_var}) is 1 then
                            set {draft_var} to item 1 of {list_var}
                            set savedDraftIdSource to "operation_exact_subject"
                            exit repeat
                        else if (count of {list_var}) is greater than 1 then
                            exit repeat
                        end if
                        if subjectAttempt is less than 4 then delay 0.5
                    end repeat
"""


def _refresh_numeric_saved_draft_id(draft_var: str) -> str:
    """Keep a numeric Drafts id when the bound row still exposes one."""
    return f"""
                try
                    set refreshedDraftId to (id of {draft_var}) as string
                    if my isNumericStandaloneDraftId(refreshedDraftId) then
                        set savedDraftId to refreshedDraftId
                        if savedDraftIdSource is "" then set savedDraftIdSource to "operation_exact_subject"
                    end if
                end try
"""


def standalone_exact_marker_draft_scan(marker: str, *, list_var: str = "markedDrafts") -> str:
    """Public exact-marker scan fragment for HTML compose and attachment finalize."""
    return _exact_marker_draft_scan_fragment(escape_applescript(marker), list_var)


def standalone_exact_subject_expr_draft_scan(
    subject_expr: str,
    *,
    list_var: str = "markedDrafts",
    exclude_ids_var: str = "",
) -> str:
    """Public exact-subject scan; ``subject_expr`` is an AppleScript expression."""
    return _exact_subject_expr_draft_scan_fragment(subject_expr, list_var, exclude_ids_var=exclude_ids_var)


def _delete_if_still_exact_marker(safe_marker: str) -> str:
    """Delete the bound row only when its subject is still the exact marker."""
    return f"""
                                    if (subject of markedDraft as string) is "{safe_marker}" then
                                        delete markedDraft
                                        set markerSweepStatus to "deleted"
                                    else
                                        set markerSweepStatus to "failed"
                                    end if
"""


def standalone_restore_leftover_marker_outgoing_script(marker: str, final_subject: str) -> str:
    """Restore leftover fixture outgoing messages that still have the marker.

    ``outgoing message.subject`` is writable. Saved Drafts ``message.subject``
    is not. Close a leftover fixture window only when restore cannot stick.
    Unrelated outgoing mail is left untouched because the marker is unique.
    """
    safe_marker = escape_applescript(marker)
    safe_subject = escape_applescript(final_subject)
    return f"""
                set leftoverOutgoingStatus to "cleared"
                try
                    repeat with leftoverMsg in outgoing messages
                        try
                            if (subject of leftoverMsg as string) is "{safe_marker}" then
                                set subject of leftoverMsg to "{safe_subject}"
                                set leftoverCheck to subject of leftoverMsg as string
                                if leftoverCheck is not "{safe_subject}" then
                                    try
                                        close (window of leftoverMsg) saving no
                                    end try
                                    set leftoverOutgoingStatus to "failed"
                                else if leftoverOutgoingStatus is not "failed" then
                                    set leftoverOutgoingStatus to "outgoing_ok"
                                end if
                            end if
                        end try
                    end repeat
                on error
                    set leftoverOutgoingStatus to leftoverOutgoingStatus
                end try
"""


def standalone_exact_marker_restore_or_delete_script(
    marker: str,
    final_subject: str,
    *,
    persist_is_failure: bool = False,
) -> str:
    """Restore leftover marker outgoing messages; handle a unique marker Drafts row.

    Saved Gmail Drafts ``message.subject`` is read-only, so this never writes
    ``markedDraft``. Count 0 is ``cleared``. Count 2 or more is ``ambiguous``.
    On the success path, ``persist_is_failure=True``: a unique leftover marker
    row is a leak, so this fails closed without deleting. Error and follow-up
    paths omit that flag and delete a unique leftover marker row.
    """
    safe_marker = escape_applescript(marker)
    outgoing = standalone_restore_leftover_marker_outgoing_script(marker, final_subject)
    scan = _exact_marker_draft_scan_fragment(safe_marker)
    if persist_is_failure:
        unique_action = """
                            set markerSweepStatus to "failed"
"""
    else:
        unique_action = f"""
                            set markedDraft to item 1 of markedDrafts
                            try
                                {_delete_if_still_exact_marker(safe_marker)}
                            on error
                                set markerSweepStatus to "failed"
                            end try
"""
    return f"""
                {outgoing}
                set markerSweepStatus to "cleared"
                try
                    if draftsMailbox is not missing value then
                        {scan}
                        set markerMatchCount to count of markedDrafts
                        if markerMatchCount is greater than 1 then
                            set markerSweepStatus to "ambiguous"
                        else if markerMatchCount is 1 then
                            {unique_action}
                        end if
                    end if
                on error
                    set markerSweepStatus to "failed"
                end try
"""


def standalone_marker_draft_finalize_script(final_subject: str, proof_script: str) -> str:
    """Bind the already-saved real-subject draft, then run attachment proof.

    Caller must ``set subject of newMsg`` and ``save newMsg`` before this
    fragment. After Gmail save, Drafts ``message.subject`` is read-only, so
    this never writes subject. Identity prefers the pre-save snapshot, then
    a unique exact real-subject row under the Drafts cap.
    """
    scan = standalone_exact_marker_draft_scan(final_subject)
    bind_id = _bind_marked_draft_by_saved_id_fragment()
    snapshot = standalone_draft_identity_resolver_script()
    return f"""
            set savedDraftId to ""
            set savedDraftIdSource to ""
            set attachmentTransactionProof to "identity_unavailable"
            set markedDraft to missing value
            try
                {snapshot}
                if savedDraftId is not "" and draftsMailbox is not missing value then
                    {bind_id}
                end if
                if markedDraft is missing value and draftsMailbox is not missing value then
                    {_unique_subject_bind_retry(scan, "markedDrafts", "markedDraft")}
                end if
                if markedDraft is not missing value then
                    {proof_script}
                    if attachmentTransactionProof is not "verified" then error "DRAFT_ATTACHMENT_PROOF_FAILED: " & attachmentTransactionProof
                    {_refresh_numeric_saved_draft_id("markedDraft")}
                end if
            on error errMsg
                set savedDraftId to ""
                set attachmentTransactionProof to "finalization_failed"
                error errMsg
            end try
"""
