"""Safe native-reply Drafts-ID resolver AppleScript fragments.

Mail's immediate ``outgoing message`` identifier is not reliably the same as
the persisted identifier in an account's Drafts mailbox. Native replies take a
bounded before/after snapshot. The preferred result is an RFC-linked identity;
when iCloud has not assigned an outgoing RFC Message-ID, exactly one newly
persisted numeric Drafts row is emitted as a transaction-only identity.
"""

from apple_mail_mcp.tools.compose.constants import DRAFT_LIST_CAP


def _native_reply_draft_resolver_handlers_applescript() -> str:
    """Return Mail-aware handlers for a conservative persisted-Drafts lookup."""
    return """
using terms from application "Mail"
-- NOTE: ``standalone_draft_identity_scripts.py`` defines a handler with this
-- same name whose items 2 and 3 are the OTHER way round ({count, rfcMessageIds,
-- numericDraftIds} there; {count, draftIds, draftRfcMessageIds} here). The two
-- fragments are never spliced into one script -- this one goes into the reply
-- paths, that one into compose/forward -- and if they ever were, the second
-- definition would silently shadow the first and hand back a Drafts id where an
-- RFC Message-ID was expected. Keep them in separate scripts, or rename first.
on fullDraftRfcSnapshot(draftsMailbox, draftCap)
    try
        set totalDrafts to count of messages of draftsMailbox
        if totalDrafts > draftCap then return missing value
        -- Three items on every non-``missing value`` branch, matching the
        -- populated return below. A two-item empty case would throw on any
        -- future ``item 3 of`` read, and the caller's surrounding ``try`` would
        -- turn that throw into a silent "no identity resolved".
        if totalDrafts is 0 then return {0, {}, {}}
        set draftMessages to messages 1 thru totalDrafts of draftsMailbox
        set draftIds to {}
        set draftRfcMessageIds to {}
        repeat with aDraft in draftMessages
            set draftId to id of aDraft as string
            if draftId is "" then return missing value
            set rfcMessageId to message id of aDraft as string
            set end of draftIds to draftId
            set end of draftRfcMessageIds to rfcMessageId
        end repeat
        return {totalDrafts, draftIds, draftRfcMessageIds}
    on error
        return missing value
    end try
end fullDraftRfcSnapshot

on sourceRfcMessageIdFor(sourceMessage)
    try
        set sourceMessageId to message id of sourceMessage as string
        if sourceMessageId is not "" then return sourceMessageId
    end try
    return ""
end sourceRfcMessageIdFor

on identifierWasPresent(identifier, priorIdentifiers)
    repeat with priorIdentifier in priorIdentifiers
        if (contents of priorIdentifier as string) is identifier then return true
    end repeat
    return false
end identifierWasPresent

on draftInReplyTo(draftMessage)
    try
        repeat with aHeader in (headers of draftMessage)
            if (name of aHeader as string) is "In-Reply-To" then return {true, content of aHeader as string}
        end repeat
        return {true, ""}
    on error
        return {false, ""}
    end try
end draftInReplyTo

on headerHasExactRfcToken(headerText, expectedRfcMessageId)
    if headerText is "" or expectedRfcMessageId is "" then return false
    set savedDelimiters to AppleScript's text item delimiters
    try
        set AppleScript's text item delimiters to ">"
        set headerParts to text items of headerText
        set AppleScript's text item delimiters to savedDelimiters
        repeat with headerPart in headerParts
            set partText to contents of headerPart as string
            if partText contains "<" then
                set AppleScript's text item delimiters to "<"
                set idParts to text items of partText
                set AppleScript's text item delimiters to savedDelimiters
                if (count of idParts) > 1 then
                    set candidateRfcMessageId to "<" & (item -1 of idParts as string) & ">"
                    if candidateRfcMessageId is expectedRfcMessageId then return true
                end if
            end if
        end repeat
    on error
        set AppleScript's text item delimiters to savedDelimiters
        return false
    end try
    set AppleScript's text item delimiters to savedDelimiters
    return false
end headerHasExactRfcToken

-- Returns ``missing value`` for "no identity could be proved", or a 4-item
-- {draftId, rfcMessageId, provenSourceRfcMessageId, evidence} capsule. The
-- no-answer sentinel is deliberately NOT "": an empty string is a legal value
-- for the id fields inside this handler (an unreadable ``id of aDraft`` reads
-- as ""), so reusing it as the failure signal makes "we found nothing" and "we
-- found a row we could not identify" indistinguishable at the call site.
on persistedReplyDraftIdentity(draftsMailbox, preSaveDraftSnapshot, sourceMessageId, draftCap)
    try
        if preSaveDraftSnapshot is missing value then return missing value
        set preSaveDraftCount to item 1 of preSaveDraftSnapshot
        set preSaveDraftIds to item 2 of preSaveDraftSnapshot
        set postSaveDraftCount to count of messages of draftsMailbox
        if postSaveDraftCount > draftCap then return missing value
        if postSaveDraftCount is not (preSaveDraftCount + 1) then return missing value
        set postSaveDrafts to messages 1 thru postSaveDraftCount of draftsMailbox
        set newDraftIdentities to {}
        repeat with aDraft in postSaveDrafts
            set candidateDraftId to id of aDraft as string
            if candidateDraftId is "" then return missing value
            set candidateRfcMessageId to message id of aDraft as string
            if (my identifierWasPresent(candidateDraftId, preSaveDraftIds)) is false then
                set end of newDraftIdentities to {candidateDraftId, candidateRfcMessageId}
            end if
        end repeat
        if (count of newDraftIdentities) is not 1 then return missing value
        set matchingIdentity to item 1 of newDraftIdentities
        set candidateDraftId to item 1 of matchingIdentity as string
        set candidateRfcMessageId to item 2 of matchingIdentity as string
        if candidateRfcMessageId is "" then return {candidateDraftId, "", "", "transaction"}
        if sourceMessageId is "" then return missing value
        set candidateDraft to first message of draftsMailbox whose id is (candidateDraftId as integer)
        set inReplyToResult to my draftInReplyTo(candidateDraft)
        if item 1 of inReplyToResult is false then return missing value
        if my headerHasExactRfcToken(item 2 of inReplyToResult, sourceMessageId) then
            return {candidateDraftId, candidateRfcMessageId, sourceMessageId, "rfc"}
        end if
    end try
    return missing value
end persistedReplyDraftIdentity
end using terms from
"""


def _native_reply_draft_resolver_setup_script() -> str:
    """Return the bounded pre-save snapshot and source RFC Message-ID lookup."""
    return f"""
        set sourceRfcMessageId to ""
        set preSaveDraftSnapshot to missing value
        try
            set sourceRfcMessageId to my sourceRfcMessageIdFor(foundMessage)
            set draftsMailbox to mailbox "Drafts" of targetAccount
            set preSaveDraftSnapshot to my fullDraftRfcSnapshot(draftsMailbox, {DRAFT_LIST_CAP})
        end try
    """


def _native_reply_draft_resolver_script() -> str:
    """Return a no-ID-on-ambiguity persisted-Drafts resolver after save."""
    return f"""
        set replyDraftId to ""
        set replyDraftRfcMessageId to ""
        set replyDraftSourceRfcMessageId to ""
        set replyDraftIdentityEvidence to ""
        try
            if preSaveDraftSnapshot is not missing value then
                repeat with identityAttempt from 1 to 3
                    set replyDraftIdentity to my persistedReplyDraftIdentity(draftsMailbox, preSaveDraftSnapshot, sourceRfcMessageId, {DRAFT_LIST_CAP})
                    if replyDraftIdentity is not missing value then
                        set replyDraftId to item 1 of replyDraftIdentity as string
                        set replyDraftRfcMessageId to item 2 of replyDraftIdentity as string
                        -- Item 3, NOT the top-level sourceRfcMessageId. They differ:
                        -- the top-level value is whatever was read off the source
                        -- message, while item 3 is "" unless persistedReplyDraftIdentity
                        -- PROVED this draft's In-Reply-To header carries that exact id.
                        -- Emitting the unproven one made every transaction-evidence
                        -- capsule carry a non-empty third field and fail to parse.
                        set replyDraftSourceRfcMessageId to item 3 of replyDraftIdentity as string
                        set replyDraftIdentityEvidence to item 4 of replyDraftIdentity as string
                        exit repeat
                    end if
                    if identityAttempt is less than 3 then delay 0.5
                end repeat
            end if
        on error
            set replyDraftId to ""
            set replyDraftRfcMessageId to ""
            set replyDraftSourceRfcMessageId to ""
            set replyDraftIdentityEvidence to ""
        end try
    """
