"""Saved reply/forward draft verification probes used by the reply and forward tools (and by tests directly).

``run_applescript`` is reached through the ``compose`` facade to preserve the existing patch seam."""

from contextlib import suppress
from dataclasses import replace
from pathlib import Path

from apple_mail_mcp.applescript_snippets import sanitize_field_handler, text_offset_handler
from apple_mail_mcp.core import AppleScriptTimeout, escape_applescript
from apple_mail_mcp.tools import compose
from apple_mail_mcp.tools.compose.constants import DRAFT_LIST_CAP
from apple_mail_mcp.tools.compose.reply_draft_resolver_scripts import (
    _native_reply_draft_resolver_handlers_applescript,
)
from apple_mail_mcp.tools.compose.reply_identity import NativeReplyDraftIdentity
from apple_mail_mcp.tools.compose.verification import (
    _first_non_empty_line,
    _format_forward_verification_lines,
    _reply_verification_from_output,
    _ReplyDraftVerification,
)


def _verify_saved_forward_draft(
    account: str,
    *,
    draft_id: str | None,
    to: str,
    subject: str | None,
    lead_message: str | None,
    expected_signature: bool | None,
    timeout: int | None = None,
) -> str:
    """Verify a saved forward draft by exact Drafts id when Mail exposes it."""
    if not draft_id:
        return "Verification Status: unavailable\nWarning: Mail did not expose a saved forward Draft ID\n"

    raw_verification = compose.verify_draft(
        account=account,
        draft_id=draft_id,
        expected_to=to,
        expected_subject=subject,
        expected_body_contains=_first_non_empty_line(lead_message or "") or None,
        expected_signature=expected_signature,
        timeout=timeout,
    )
    return _format_forward_verification_lines(raw_verification, draft_id)


def _verify_saved_reply_draft(
    account: str,
    reply_subject: str,
    reply_body: str,
    *,
    draft_id: str | None = None,
    native_draft_identity: NativeReplyDraftIdentity | None = None,
    quoted_needle: str | None = None,
    quote_anchor: str | None = None,
    expected_attachment_count: int | None = None,
    expected_attachment_names: list[str] | None = None,
    signature_requested: bool | None = None,
    expected_signature_name: str | None = None,
    timeout: int | None = None,
) -> _ReplyDraftVerification:
    """Confirm a native reply draft appears in a bounded newest Drafts window.

    Compares the FULL reply body above the quoted original, not just its first
    line (AGENTIC-1214: a first-line-only needle let a truncated or miscased
    tail slip past verification). The body reaches the verifier AppleScript
    through a second temp file — the original compose temp file is already
    gone by the time this runs — and the compare is whitespace-flattened,
    smart-punctuation-folded, sentence-start-case-neutralized, and only THEN
    case-sensitive, so Mail's own Substitutions and autocapitalization do not
    cause a false mismatch while an ALL-CAPS draft (Bug 3) still fails.
    """
    safe_account = escape_applescript(account)
    safe_reply_subject = escape_applescript(reply_subject)
    safe_draft_id = escape_applescript(draft_id or "")
    safe_draft_rfc_message_id = escape_applescript(
        native_draft_identity.draft_rfc_message_id if native_draft_identity else ""
    )
    safe_source_rfc_message_id = escape_applescript(
        native_draft_identity.source_rfc_message_id if native_draft_identity else ""
    )
    require_native_identity = "true" if native_draft_identity else "false"
    require_rfc_identity = "true" if native_draft_identity and native_draft_identity.is_rfc_backed else "false"
    require_exact_attachment_identity = "true" if expected_attachment_count is not None else "false"
    safe_quoted_needle = escape_applescript(_first_non_empty_line(quoted_needle or ""))
    # Second half of the quote proof: a span of the SOURCE body. The attribution
    # line alone can occur in an authored body or signature, so on its own it
    # certifies nothing about the quoted original surviving. Python's strip in
    # ``_first_non_empty_line`` is authoritative here -- a whitespace-only anchor
    # collapses to "" and simply disables the extra check rather than matching
    # everything.
    safe_quote_anchor = escape_applescript(_first_non_empty_line(quote_anchor or ""))
    expected_attachment_names = expected_attachment_names or []
    expected_attachment_names_script = (
        "{" + ", ".join(f'"{escape_applescript(name)}"' for name in expected_attachment_names if name) + "}"
    )
    expected_attachment_count_value = -1 if expected_attachment_count is None else max(0, expected_attachment_count)
    signature_requested_flag = (
        "missing value" if signature_requested is None else ("true" if signature_requested else "false")
    )
    safe_expected_signature_name = escape_applescript(expected_signature_name or "")
    verification_timeout = 60 if timeout is None else max(30, min(timeout, 120))
    sanitize_script = sanitize_field_handler(include_attachment_row_delimiter=True)
    text_offset_script = text_offset_handler()
    resolver_handlers = _native_reply_draft_resolver_handlers_applescript()

    with compose.tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        prefix="mail_reply_verify_",
        delete=False,
        encoding="utf-8",
    ) as verify_body_tmp:
        verify_body_tmp.write(reply_body)
        verify_body_temp_path = verify_body_tmp.name

    script = f'''
    {sanitize_script}

    {text_offset_script}

    {resolver_handlers}

    on foldPair(theText, fromText, toText)
        if fromText is "" then return theText
        set previousDelimiters to AppleScript's text item delimiters
        set AppleScript's text item delimiters to fromText
        set parts to text items of theText
        set AppleScript's text item delimiters to toText
        set joined to parts as string
        set AppleScript's text item delimiters to previousDelimiters
        return joined
    end foldPair

    on lowercaseChar(ch)
        -- ASCII-only case fold (A-Z). Accented capitals are left as-is; that is
        -- an accepted gap, not a correctness bug, since it only widens (never
        -- narrows) the set of sentence-starts left case-sensitive.
        try
            set codeNum to id of ch
        on error
            return ch
        end try
        if codeNum >= 65 and codeNum <= 90 then
            return (character id (codeNum + 32))
        end if
        return ch
    end lowercaseChar

    on foldFirstChar(theString)
        -- ASCII-lowercases the first character of theString and leaves the
        -- rest untouched. Guarded for empty and single-character input so
        -- "text 2 thru -1 of" never runs off the end of a short string.
        set stringLength to count of characters of theString
        if stringLength is 0 then return theString
        if stringLength is 1 then return my lowercaseChar(theString)
        return (my lowercaseChar(character 1 of theString)) & (text 2 thru -1 of theString)
    end foldFirstChar

    on foldSentenceStarts(theText)
        -- Neutralizes macOS "Capitalize words automatically" at sentence starts
        -- (text start and immediately after ".", "!", "?") on BOTH compare sides,
        -- so autocapitalize cannot cause a false mismatch. An ALL-CAPS draft
        -- (Bug 3) still fails: this only folds the first letter of each
        -- sentence, not every letter, so the rest of an ALL-CAPS sentence stays
        -- mismatched against the source's normal case.
        --
        -- O(number of sentence delimiters), not O(characters): the old
        -- per-character loop called a handler and reallocated the result
        -- string on every single character, which is O(n^2) on
        -- AppleScript's copy-on-append strings. Real Exchange drafts carry
        -- long quoted thread histories (tens of KB), so that walk could burn
        -- the whole verifier timeout on one candidate draft, surfacing a real
        -- body mismatch (AGENTIC-1214) as a timeout instead. This version
        -- text-item-delimiter-splits on each of ".", "!", "?" in turn and only
        -- rewrites the first character of the (few) items that follow a
        -- delimiter, so cost tracks sentence count, not text length.
        if theText is "" then return theText
        set resultText to my foldFirstChar(theText)
        set previousDelimiters to AppleScript's text item delimiters
        repeat with delimiterChar in {{".", "!", "?"}}
            set AppleScript's text item delimiters to (contents of delimiterChar)
            set theParts to text items of resultText
            set partCount to count of theParts
            if partCount > 1 then
                repeat with partIndex from 2 to partCount
                    set item partIndex of theParts to my foldFirstChar(item partIndex of theParts)
                end repeat
                set resultText to theParts as string
            end if
        end repeat
        set AppleScript's text item delimiters to previousDelimiters
        return resultText
    end foldSentenceStarts

    on flattenForCompare(theText)
        -- Whitespace-flattens (Mail's compose window soft-wraps long lines into
        -- line breaks that the source text does not have), folds Mail's
        -- Substitutions punctuation (smart quotes/dashes/ellipsis), collapses
        -- hyphen runs so a source "--" matches Mail's single em-dash
        -- substitution, and neutralizes sentence-start capitalization. Case is
        -- preserved everywhere else, so an ALL-CAPS draft still fails the
        -- case-sensitive compare in replyBodyAboveQuoteStatus.
        if theText is "" then return theText
        set t to theText as string
        repeat with stripChar in {{return, linefeed, tab, space, (character id 160)}}
            set t to my foldPair(t, (contents of stripChar), "")
        end repeat
        set t to my foldPair(t, (character id 8216), "'")
        set t to my foldPair(t, (character id 8217), "'")
        set t to my foldPair(t, (character id 8220), "\\"")
        set t to my foldPair(t, (character id 8221), "\\"")
        set t to my foldPair(t, (character id 8211), "-")
        set t to my foldPair(t, (character id 8212), "-")
        set t to my foldPair(t, (character id 8230), "...")
        repeat 20 times
            if t does not contain "--" then exit repeat
            set t to my foldPair(t, "--", "-")
        end repeat
        set t to my foldSentenceStarts(t)
        return t
    end flattenForCompare

    on caseSensitiveOffset(haystackText, needleText)
        -- Self-contained case-sensitive offset finder (its own `considering
        -- case` wraps its own text-item-delimiter split), so callers never
        -- depend on `considering case` propagating into a handler call.
        if needleText is "" then return 0
        set previousDelimiters to AppleScript's text item delimiters
        considering case
            try
                set AppleScript's text item delimiters to needleText
                set splitItems to text items of haystackText
                if (count of splitItems) is 1 then
                    set AppleScript's text item delimiters to previousDelimiters
                    return 0
                end if
                set beforeNeedle to item 1 of splitItems
                set AppleScript's text item delimiters to previousDelimiters
                return ((count of characters of beforeNeedle) + 1)
            on error
                set AppleScript's text item delimiters to previousDelimiters
                return 0
            end try
        end considering
    end caseSensitiveOffset

    using terms from application "Mail"

    on attachmentStatus(draftMessage, expectedAttachmentCount, expectedAttachmentNames)
        if expectedAttachmentCount < 0 then return "not_requested"
        try
            set draftAttachments to mail attachments of draftMessage
            set actualAttachmentCount to count of draftAttachments
            if actualAttachmentCount < expectedAttachmentCount then return "missing"
            repeat with anAttachment in draftAttachments
                try
                    set attachmentSize to file size of anAttachment as integer
                    if attachmentSize is less than or equal to 0 then return "unreadable"
                on error
                    return "unreadable"
                end try
            end repeat
            if (count of expectedAttachmentNames) is 0 then return "verified"

            set draftAttachmentNames to {{}}
            repeat with anAttachment in draftAttachments
                set end of draftAttachmentNames to (name of anAttachment as string)
            end repeat
            repeat with expectedAttachmentName in expectedAttachmentNames
                set matchIndex to 0
                repeat with nameIndex from 1 to count of draftAttachmentNames
                    if item nameIndex of draftAttachmentNames is (expectedAttachmentName as string) then
                        set matchIndex to nameIndex
                        exit repeat
                    end if
                end repeat
                if matchIndex is 0 then return "missing"
                set item matchIndex of draftAttachmentNames to missing value
            end repeat
            return "verified"
        on error
            return "unsupported"
        end try
    end attachmentStatus

    on attachmentCount(draftMessage)
        try
            return count of mail attachments of draftMessage
        on error
            return ""
        end try
    end attachmentCount

    on attachmentRows(draftMessage)
        set attachmentRowsText to ""
        try
            repeat with anAttachment in mail attachments of draftMessage
                set attachmentName to my sanitize_field(name of anAttachment)
                set attachmentSize to ""
                try
                    set attachmentSize to file size of anAttachment as string
                end try
                set attachmentRowsText to attachmentRowsText & attachmentName & "::" & attachmentSize & ";;"
            end repeat
        end try
        return attachmentRowsText
    end attachmentRows

    on signatureStatus(draftContent, fullReplyBody, quotedNeedle, signatureWasRequested, expectedSignatureName)
        if signatureWasRequested is missing value then return "not_requested"
        if signatureWasRequested is false then return "not_requested"
        set newBodyText to draftContent
        if quotedNeedle is not "" then
            set quoteOffset to my textOffset(draftContent, quotedNeedle)
            if quoteOffset > 1 then set newBodyText to text 1 thru (quoteOffset - 1) of draftContent
        end if
        try
            if expectedSignatureName is not "" then
                repeat with sig in signatures
                    if (name of sig as string) is expectedSignatureName then
                        set expectedSigText to content of sig as string
                        if expectedSigText is not "" and newBodyText contains expectedSigText then return "detected"
                        return "missing"
                    end if
                end repeat
                return "missing"
            end if
            repeat with sig in signatures
                set sigText to content of sig as string
                if sigText is not "" and newBodyText contains sigText then return "detected"
            end repeat
        end try
        return "missing"
    end signatureStatus

    on quoteProofHolds(searchRegion, flatQuote, quoteAnchor)
        -- Both halves of the proof must land in the SAME region after the body:
        -- the attribution line proves Mail wrote a quote header, and the source
        -- anchor proves the quoted original itself is still under it. An
        -- attribution with no anchor is the exact failure -- header kept, quote
        -- lost -- that a sender-only needle could not tell from success.
        if my textOffset(searchRegion, flatQuote) is 0 then return false
        if quoteAnchor is "" then return true
        return (my textOffset(searchRegion, my flattenForCompare(quoteAnchor)) > 0)
    end quoteProofHolds

    on replyBodyAboveQuoteStatus(draftContent, fullReplyBody, quotedNeedle, quoteAnchor)
        -- Locates the flattened body FIRST (case-sensitively); only a quote
        -- marker occurrence AFTER that body match counts as the quote
        -- boundary, so a reply body that itself contains "wrote:" (e.g. "As
        -- Keynes wrote: ...") cannot false-fail into after_quote.
        set flatBody to my flattenForCompare(fullReplyBody)
        set flatDraft to my flattenForCompare(draftContent)
        if flatBody is "" then
            if quotedNeedle is "" then return "found"
            set flatQuote to my flattenForCompare(quotedNeedle)
            if my quoteProofHolds(flatDraft, flatQuote, quoteAnchor) then return "found"
            return "quote_missing"
        end if
        set bodyOffset to my caseSensitiveOffset(flatDraft, flatBody)
        if bodyOffset is 0 then return "missing"
        if quotedNeedle is "" then return "found"
        set flatQuote to my flattenForCompare(quotedNeedle)
        set bodyEndOffset to bodyOffset + (count of characters of flatBody)
        if bodyEndOffset > (count of characters of flatDraft) then return "quote_missing"
        set searchRegion to text bodyEndOffset thru -1 of flatDraft
        if my quoteProofHolds(searchRegion, flatQuote, quoteAnchor) then return "found"
        set quoteOffsetAnywhere to my textOffset(flatDraft, flatQuote)
        if quoteOffsetAnywhere > 0 and quoteOffsetAnywhere < bodyOffset then return "after_quote"
        return "quote_missing"
    end replyBodyAboveQuoteStatus

    on verifyReplyDraft(draftMessage, fullReplyBody, quotedNeedle, quoteAnchor, expectedAttachmentCount, expectedAttachmentNames, signatureWasRequested, expectedSignatureName)
        set draftId to id of draftMessage as string
        set draftContent to content of draftMessage as string
        set bodyStatus to my replyBodyAboveQuoteStatus(draftContent, fullReplyBody, quotedNeedle, quoteAnchor)
        -- Decide the body verdict BEFORE probing attachments and signatures. Only
        -- the "found" and "quote_missing" rows below ever emit those four values;
        -- the "after_quote" and "missing" rows return the draft id alone. Computing
        -- them up front meant every draft that is not ours -- which is most of the
        -- bounded Drafts window on the same-subject fallback scan -- paid three
        -- separate walks of ``mail attachments`` (each reading ``file size``) plus a
        -- full signature normalization of the body, and then discarded all of it.
        -- The saved-draft verifier polls up to twenty times, so that waste was paid
        -- per draft per attempt while holding the cross-process Mail lock.
        if bodyStatus is "after_quote" then return "BODY_AFTER_QUOTE|" & draftId
        if bodyStatus is not "found" and bodyStatus is not "quote_missing" then return "BODY_MISSING|" & draftId
        set draftAttachmentStatus to my attachmentStatus(draftMessage, expectedAttachmentCount, expectedAttachmentNames)
        set draftAttachmentCount to my attachmentCount(draftMessage)
        set draftAttachmentRows to my attachmentRows(draftMessage)
        set draftSignatureStatus to my signatureStatus(draftContent, fullReplyBody, quotedNeedle, signatureWasRequested, expectedSignatureName)
        if bodyStatus is "found" then
            if draftAttachmentStatus is "missing" then return "ATTACHMENT_MISSING|" & draftId & "|" & draftAttachmentStatus & "|" & draftSignatureStatus & "|" & draftAttachmentCount & "|" & draftAttachmentRows
            if draftAttachmentStatus is "unsupported" then return "ATTACHMENT_UNSUPPORTED|" & draftId & "|" & draftAttachmentStatus & "|" & draftSignatureStatus & "|" & draftAttachmentCount & "|" & draftAttachmentRows
            if draftAttachmentStatus is "unreadable" then return "ATTACHMENT_UNREADABLE|" & draftId & "|" & draftAttachmentStatus & "|" & draftSignatureStatus & "|" & draftAttachmentCount & "|" & draftAttachmentRows
            return "FOUND|" & draftId & "|" & draftAttachmentStatus & "|" & draftSignatureStatus & "|" & draftAttachmentCount & "|" & draftAttachmentRows
        end if
        if bodyStatus is "quote_missing" then return "QUOTE_MISSING|" & draftId & "|" & draftAttachmentStatus & "|" & draftSignatureStatus & "|" & draftAttachmentCount & "|" & draftAttachmentRows
        return "BODY_MISSING|" & draftId
    end verifyReplyDraft

    on identityMatches(draftMessage, expectedDraftRfcMessageId, expectedSourceRfcMessageId)
        try
            if (message id of draftMessage as string) is not expectedDraftRfcMessageId then return false
            set inReplyToResult to my draftInReplyTo(draftMessage)
            if item 1 of inReplyToResult is false then return false
            return my headerHasExactRfcToken(item 2 of inReplyToResult, expectedSourceRfcMessageId)
        on error
            return false
        end try
    end identityMatches

    end using terms from

    tell application "Mail"
        set targetAccount to account "{safe_account}"
        set targetDraftIdText to "{safe_draft_id}"
        set requireNativeIdentity to {require_native_identity}
        set requireRfcIdentity to {require_rfc_identity}
        set requireExactAttachmentIdentity to {require_exact_attachment_identity}
        set expectedDraftRfcMessageId to "{safe_draft_rfc_message_id}"
        set expectedSourceRfcMessageId to "{safe_source_rfc_message_id}"
        set fullReplyBody to do shell script "cat " & quoted form of "{verify_body_temp_path}"
        set quotedNeedle to "{safe_quoted_needle}"
    set quoteAnchor to "{safe_quote_anchor}"
        set expectedAttachmentCount to {expected_attachment_count_value}
        set expectedAttachmentNames to {expected_attachment_names_script}
        set signatureWasRequested to {signature_requested_flag}
        set expectedSignatureName to "{safe_expected_signature_name}"
        set replyDraftVerified to false
        set bodyMissingDraftId to ""
        set bodyAfterQuoteDraftId to ""
        set quoteMissingResult to ""
        set attachmentFailureResult to ""
        set foundDraftId to ""

        if requireExactAttachmentIdentity and (requireNativeIdentity is false) then return "IDENTITY_UNAVAILABLE"

        repeat with verifyAttempt from 1 to 20
            try
                set draftsMailbox to mailbox "Drafts" of targetAccount
                if targetDraftIdText is not "" then
                    try
                        set targetDraftId to targetDraftIdText as integer
                        set targetDrafts to every message of draftsMailbox whose id is targetDraftId
                        if (count of targetDrafts) > 0 then
                            set exactDraft to item 1 of targetDrafts
                            if requireRfcIdentity then
                                if (my identityMatches(exactDraft, expectedDraftRfcMessageId, expectedSourceRfcMessageId)) is false then
                                    return "IDENTITY_UNAVAILABLE"
                                end if
                            end if
                            set exactResult to my verifyReplyDraft(exactDraft, fullReplyBody, quotedNeedle, quoteAnchor, expectedAttachmentCount, expectedAttachmentNames, signatureWasRequested, expectedSignatureName)
                            if exactResult starts with "ATTACHMENT_" then
                                set attachmentFailureResult to exactResult
                            else
                                return exactResult
                            end if
                        end if
                        if (requireNativeIdentity or requireExactAttachmentIdentity) and attachmentFailureResult is "" then return "IDENTITY_UNAVAILABLE"
                    end try
                end if

                -- An exact Drafts artifact with a known attachment failure
                -- must fail closed. A same-subject fallback can only diagnose
                -- identity-unavailable work; it must never certify an older
                -- matching draft after this compose call's exact artifact
                -- lacked a requested attachment.
                if attachmentFailureResult is not "" then return attachmentFailureResult
                if (requireNativeIdentity or requireExactAttachmentIdentity) and attachmentFailureResult is "" then return "IDENTITY_UNAVAILABLE"

                if (requireNativeIdentity is false) and (requireExactAttachmentIdentity is false) then
                    set totalDrafts to count of messages of draftsMailbox
                    set headEnd to totalDrafts
                    if headEnd > {DRAFT_LIST_CAP} then set headEnd to {DRAFT_LIST_CAP}
                    if headEnd > 0 then
                    set candidateDrafts to messages 1 thru headEnd of draftsMailbox
                    repeat with draftMessage in candidateDrafts
                        try
                            set draftMatched to false
                            set draftSubject to subject of draftMessage as string
                            if "{safe_reply_subject}" is "" or draftSubject is "{safe_reply_subject}" then
                                set draftResult to my verifyReplyDraft(draftMessage, fullReplyBody, quotedNeedle, quoteAnchor, expectedAttachmentCount, expectedAttachmentNames, signatureWasRequested, expectedSignatureName)
                                if draftResult starts with "FOUND|" then
                                    set draftMatched to true
                                    set foundDraftId to draftResult
                                else if draftResult starts with "BODY_AFTER_QUOTE|" then
                                    if bodyAfterQuoteDraftId is "" then set bodyAfterQuoteDraftId to text 18 thru -1 of draftResult
                                else if draftResult starts with "BODY_MISSING|" then
                                    if bodyMissingDraftId is "" then set bodyMissingDraftId to text 14 thru -1 of draftResult
                                else if draftResult starts with "QUOTE_MISSING|" then
                                    if quoteMissingResult is "" then set quoteMissingResult to draftResult
                                else if draftResult starts with "ATTACHMENT_" then
                                    if attachmentFailureResult is "" then set attachmentFailureResult to draftResult
                                end if
                            end if

                            if draftMatched then
                                set replyDraftVerified to true
                                exit repeat
                            end if
                        end try
                    end repeat
                    end if
                end if
            end try
            if replyDraftVerified then exit repeat
            delay 1
        end repeat

        if replyDraftVerified then
            return foundDraftId
        end if
        if attachmentFailureResult is not "" then return attachmentFailureResult
        if bodyAfterQuoteDraftId is not "" then
            return "BODY_AFTER_QUOTE|" & bodyAfterQuoteDraftId
        end if
        if quoteMissingResult is not "" then return quoteMissingResult
        if bodyMissingDraftId is not "" then
            return "BODY_MISSING|" & bodyMissingDraftId
        end if
        return "NOT_FOUND"
    end tell
    '''
    try:
        output = compose.run_applescript(script, timeout=verification_timeout).strip()
    except AppleScriptTimeout:
        return _ReplyDraftVerification(ok=False, status="verification_timeout", error_artifact_id=draft_id)
    except Exception:  # noqa: BLE001 - caller converts verification failure into a safe error
        return _ReplyDraftVerification(ok=False, status="applescript_error", error_artifact_id=draft_id)
    finally:
        with suppress(OSError):
            Path(verify_body_temp_path).unlink(missing_ok=True)
    verification = _reply_verification_from_output(output)
    if expected_attachment_count is not None and verification.ok:
        has_exact_native_identity = bool(
            native_draft_identity
            and draft_id
            and native_draft_identity.draft_id == draft_id
            and verification.matched_artifact_id == draft_id
        )
        if not has_exact_native_identity:
            return _ReplyDraftVerification(ok=False, status="identity_unavailable")
    artifact_id = (
        verification.matched_artifact_id or verification.error_artifact_id or verification.body_missing_artifact_id
    )
    identity_verified = bool(draft_id and artifact_id and artifact_id == draft_id)
    return replace(verification, artifact_identity_verified=identity_verified)
