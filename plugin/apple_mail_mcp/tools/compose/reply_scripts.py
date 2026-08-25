"""Pure reply AppleScript builders (object-model and native-window paths).

These compose f-string scripts only; ``reply_to_email`` in ``compose.py`` runs
them. Keeping them here removes the largest AppleScript blocks from the tool
module without touching any I/O or patched name.
"""

from apple_mail_mcp.tools.compose.constants import (
    AX_WINDOW_SETTLE_ATTEMPTS,
    AX_WINDOW_SETTLE_DELAY,
    QUOTE_PROOF_UNAVAILABLE,
    REPLY_ACCESSIBILITY_UNAVAILABLE,
    TYPING_CHUNK_SIZE,
    TYPING_INTER_CHUNK_DELAY,
)
from apple_mail_mcp.tools.compose.reply_draft_resolver_scripts import (
    _native_reply_draft_resolver_handlers_applescript,
    _native_reply_draft_resolver_script,
    _native_reply_draft_resolver_setup_script,
)
from apple_mail_mcp.tools.compose.reply_script_helpers import _reply_extra_output_lines
from apple_mail_mcp.tools.compose.reply_subject_scripts import native_reply_subject_helpers_applescript
from apple_mail_mcp.tools.compose.reply_window_identity_scripts import native_reply_identity_tweak_script
from apple_mail_mcp.tools.compose.reply_window_scripts import native_reply_window_handlers_applescript
from apple_mail_mcp.tools.compose.typing_scripts import build_chunked_typing_handler


def _build_reply_objectmodel_applescript(
    *,
    header_text: str,
    success_text: str,
    safe_account: str,
    mailbox_lookup: str,
    lookup_script: str,
    not_found_message: str,
    body_temp_path: str,
    reply_options: str,
    reply_settle_delay: str,
    sender_script: str,
    signature_script: str,
    cc_script: str,
    bcc_script: str,
    attachment_script: str,
    post_action: str,
    cleanup_script: str,
    safe_cc: str,
    safe_bcc: str,
    safe_attachment_info: str,
    has_cc: bool,
    has_bcc: bool,
    has_attachments: bool,
) -> str:
    """Build the object-model reply script used when ``native_format=False``.

    This path assigns the reply ``content`` directly (reply_body + a plain-text
    quoted original) without opening a window. It is the headless/bulk-safe path:
    no GUI focus, no Accessibility permission. The trade-off is that Mail's native
    rich quote bar and logo signature are flattened to plain text. The windowed
    ``native_format=True`` path (``_build_reply_native_window_applescript``)
    preserves the native look; this is the quiet fallback.
    """
    extra_output_lines = _reply_extra_output_lines(
        safe_cc=safe_cc,
        safe_bcc=safe_bcc,
        safe_attachment_info=safe_attachment_info,
        has_cc=has_cc,
        has_bcc=has_bcc,
        has_attachments=has_attachments,
    )

    return f'''
tell application "Mail"
    set outputText to "{header_text}" & return & return

    try
        set targetAccount to account "{safe_account}"
        {mailbox_lookup}
        {lookup_script}

        if foundMessage is missing value then
            return "{not_found_message}"
        end if

        set sourceSubject to subject of foundMessage as string
        if sourceSubject starts with "Re:" or sourceSubject starts with "RE:" or sourceSubject starts with "re:" then
            set replySubject to sourceSubject
        else
            set replySubject to "Re: " & sourceSubject
        end if
        set sourceSender to sender of foundMessage as string
        set sourceDate to date received of foundMessage as string
        set sourceContent to content of foundMessage as string
        set replyBodyText to do shell script "cat " & quoted form of "{body_temp_path}"

        -- Native Mail reply: Mail creates an outgoing reply message from the
        -- source message, then this script assigns the intended plain-text body
        -- above the quoted original before the draft is saved.
        set replyMessage to reply foundMessage {reply_options}
        {reply_settle_delay}

        {sender_script}
        {signature_script}

        set quotedOriginalNeedle to ""
        if replyBodyText is not "" then
            set quotedOriginalNeedle to "On " & sourceDate & ", " & sourceSender & " wrote:"
            set quotedOriginalText to quotedOriginalNeedle & return & sourceContent
            set composedReplyContent to replyBodyText & return & return & quotedOriginalText
            set content of replyMessage to (composedReplyContent as rich text)
        end if

        -- Optional extra recipients, on top of Mail's native reply recipients.
        {cc_script}
        {bcc_script}

        -- Add attachments
        {attachment_script}

        {post_action}

        set replyDraftId to ""
        try
            set replyDraftId to id of replyMessage as string
        end try

        set outputText to outputText & "{success_text}" & return
        set outputText to outputText & "To: native reply recipients" & return
        set outputText to outputText & "Subject: " & replySubject & return
        if replyDraftId is not "" then set outputText to outputText & "Draft ID: " & replyDraftId & return
        if quotedOriginalNeedle is not "" then set outputText to outputText & "Quote Needle: " & quotedOriginalNeedle & return
        {extra_output_lines}

        -- Clean up temp file
        {cleanup_script}

        return outputText
    on error errMsg
        try
            {cleanup_script}
        end try
        return "Error: " & errMsg & return & "Please check that the account name is correct and the email exists."
    end try
    end tell
    '''


def _native_reply_post_action(mode: str) -> str:
    """Return the post-keystroke Mail action for the windowed native reply path.

    draft: save; the caller resolves a persisted Drafts ID and then closes the
    reply window quietly (one draft remains; the auto-created shell is reused
    by ``save``, so no dedupe is needed). open: save and leave the window up
    for review. send: send (the window closes itself).
    """
    if mode == "send":
        return "send replyMessage\n        delay 0.5"
    if mode == "open":
        return "save replyMessage\n        delay 0.8\n        activate"
    return "save replyMessage\n        delay 1.0"


def _native_reply_draft_window_close_script() -> str:
    """Return the quiet close used only after native Drafts resolution."""
    return """
        my closeNativeReplyWindowSafely(replyWindowId, replySubject, derivedReplySubject)
    """


def _build_reply_native_window_applescript(
    *,
    header_text: str,
    success_text: str,
    safe_account: str,
    mailbox_lookup: str,
    lookup_script: str,
    not_found_message: str,
    body_temp_path: str,
    reply_options: str,
    sender_script: str,
    signature_script: str,
    cc_script: str,
    bcc_script: str,
    attachment_script: str,
    mode: str,
    cleanup_script: str,
    safe_cc: str,
    safe_bcc: str,
    safe_attachment_info: str,
    has_cc: bool,
    has_bcc: bool,
    has_attachments: bool,
) -> str:
    """Build the windowed native reply script used when ``native_format=True``.
    Mail's ``reply ... with opening window`` renders its own rich quoted thread
    (the colored quote bar) and inserts the account's default reply signature
    (with logo). Those exist only in the rendered compose window, never in the
    dictionary ``content``, so this path NEVER reassigns ``content`` (doing so
    flattens them — the prior bug). Instead the reply body is inserted with a
    TYPED System Events keystroke, in small focus-guarded chunks rather than one
    keystroke of the whole body (AGENTIC-1214: a single keystroke of the whole
    body drops its tail near 320-480 chars and can leak shift state into ALL
    CAPS output; see ``typing_scripts.build_chunked_typing_handler``). Never the
    clipboard, which clobbered the pasteboard and leaked bodies into the wrong
    thread in two prior live reverts.

    UI scripting is isolated to the focus guard + keystroke and is unavoidable
    here: the native rich format cannot be expressed through the Mail dictionary.
    After ``reply``, the guard may adopt Mail's live front-window title when its
    subject core matches the derived reply subject (Mail normalizes duplicate
    Re:/Fwd: prefixes). The keystroke itself still requires exact title equality
    against that adopted ``replySubject``. An empty System Events title is
    tolerated (AX quirk); a different non-empty SE title aborts without typing;
    and a System Events call that never answered at all (a missing Accessibility
    grant) aborts as ``GUARD_ABORT`` carrying the underlying error in its detail,
    rather than reading as agreement or escaping as an unstructured error.
    The same exact-title-or-empty check runs again before EVERY chunk (not just
    once before the loop), so a focus loss mid-typing aborts immediately instead
    of leaking chunks into whatever now holds focus; the abort discards the
    partially typed compose window (``close ... saving no``) so no partial draft
    is ever left behind. Requires Accessibility permission for the host process;
    callers that cannot grant it must stop and report the blocker; the
    ``native_format=False`` path is gated behind ``allow_windowless_fallback``.
    """
    extra_output_lines = _reply_extra_output_lines(
        safe_cc=safe_cc,
        safe_bcc=safe_bcc,
        safe_attachment_info=safe_attachment_info,
        has_cc=has_cc,
        has_bcc=has_bcc,
        has_attachments=has_attachments,
    )
    post_action = _native_reply_post_action(mode)
    draft_resolver_handlers = _native_reply_draft_resolver_handlers_applescript()
    draft_resolver_setup_script = _native_reply_draft_resolver_setup_script() if mode != "send" else ""
    draft_resolver_script = _native_reply_draft_resolver_script() if mode != "send" else ""
    draft_window_close_script = _native_reply_draft_window_close_script() if mode == "draft" else ""
    subject_helpers = native_reply_subject_helpers_applescript()
    window_handlers = native_reply_window_handlers_applescript()
    typing_handler = build_chunked_typing_handler(
        chunk_size=TYPING_CHUNK_SIZE,
        inter_chunk_delay=TYPING_INTER_CHUNK_DELAY,
    )
    identity_tweaks_script = native_reply_identity_tweak_script(sender_script, signature_script, cleanup_script)
    return f'''
{subject_helpers}
{window_handlers}
{typing_handler}
{draft_resolver_handlers}
set bodyTempPath to "{body_temp_path}"
set derivedReplySubject to ""
set replySubject to ""
set replyMessage to missing value
set replyWindowId to ""
set preReplyWindowIds to missing value
set replyDraftId to ""
set replyDraftRfcMessageId to ""
set replyDraftSourceRfcMessageId to ""
set replyDraftIdentityEvidence to ""
-- Only the draft/open resolver setup assigns this. mode="send" omits that
-- fragment, and the capsule line that reads it is already gated on a non-empty
-- replyDraftId, but an unassigned variable is one edit away from a runtime
-- -2753 in the success path, so initialize it unconditionally.
set sourceRfcMessageId to ""
set quotedNeedle to ""
set didType to false
set typingInterruptedDetail to ""
set guardMail to "(unset)"
set guardMailWindowId to "(unset)"
set guardSE to "(unset)"
set guardSEAnswered to false
set composeFocusVerified to false
set editorFocusResult to ""
set replyWindowRaised to false
set frontmostBlockedBy to ""

try
    tell application "Mail"
        set targetAccount to account "{safe_account}"
        {mailbox_lookup}
        {lookup_script}

        if foundMessage is missing value then
            {cleanup_script}
            return "{not_found_message}"
        end if

        {draft_resolver_setup_script}
        set sourceSubject to subject of foundMessage as string
        set sourceSender to sender of foundMessage as string
        -- Sender-only attribution can occur in a body/signature; require a source-body anchor.
        -- Prefer a paragraph long enough to be distinctive, but never refuse a reply
        -- just because the source is short: "Thanks!" and "Approved." are ordinary
        -- emails, and a short anchor still proves the quoted source content is there.
        -- The cap is 60 characters, not a full paragraph: the anchor has to survive
        -- Mail re-wrapping the quoted original, and a paragraph's first 60 characters
        -- sit before any wrap point while a 160-character span straddles one.
        set sourceQuoteAnchor to ""
        set sourceQuoteFallback to ""
        try
            set sourceContent to content of foundMessage as string
            repeat with sourceParagraph in paragraphs of sourceContent
                set candidateQuoteText to contents of sourceParagraph as string
                set candidateQuoteLength to count of characters of candidateQuoteText
                if candidateQuoteLength > 60 then set candidateQuoteText to text 1 thru 60 of candidateQuoteText
                if candidateQuoteLength >= 16 then
                    set sourceQuoteAnchor to candidateQuoteText
                    exit repeat
                end if
                if sourceQuoteFallback is "" and my stripLeadingSpaces(candidateQuoteText) is not "" then
                    set sourceQuoteFallback to candidateQuoteText
                end if
            end repeat
        end try
        if sourceQuoteAnchor is "" then set sourceQuoteAnchor to sourceQuoteFallback
        if sourceQuoteAnchor is "" then
            {cleanup_script}
            return "{QUOTE_PROOF_UNAVAILABLE}" & return & "Detail: source content has no usable quote anchor"
        end if
        if sourceSubject starts with "Re:" or sourceSubject starts with "RE:" or sourceSubject starts with "re:" then
            set derivedReplySubject to sourceSubject
        else
            set derivedReplySubject to "Re: " & sourceSubject
        end if
        set replySubject to derivedReplySubject
        set replyBodyText to do shell script "cat " & quoted form of bodyTempPath
        -- Bring Mail to the front *before* the reply command, not just after:
        -- the compose window should open into an already-frontmost app so the
        -- adoption scan and the focus guard below start from a settled front.
        -- Diagnostic only here -- a failure is reported by the guard, which is
        -- where a background Mail actually becomes fatal.
        set frontmostBlockedBy to my frontmostBlockedApp(my ensureMailFrontmost())
        -- Preflight the Accessibility bridge *before* opening a compose window.
        -- Typing needs System Events to see Mail's windows; when it cannot, every
        -- later step is doomed, and finding that out after the `reply` command
        -- costs a leaked compose window plus four focus attempts, then reports it
        -- as a focus problem. Only a successful count of exactly 0 aborts here --
        -- "unknown" means the probe itself failed and is left to the guard.
        --
        -- The zero has to *hold*: ensureMailFrontmost returns as soon as macOS
        -- reports Mail frontmost, which happens before a Space transition has
        -- finished animating, and Accessibility enumerates no windows until it
        -- has. One sample inside that gap aborts a perfectly healthy reply.
        if replyBodyText is not "" then
            set axWindowCount to my accessibilityWindowCountSettled({AX_WINDOW_SETTLE_ATTEMPTS}, {AX_WINDOW_SETTLE_DELAY})
            if axWindowCount is "0" then
                {cleanup_script}
                -- Carry Mail's own window count alongside. The two together
                -- separate "Mail genuinely has no windows" from "Accessibility
                -- cannot see the windows Mail has", which is the difference
                -- between opening a viewer and fixing the environment, and is
                -- invisible from the Accessibility count alone.
                set mailOwnWindowCount to "unknown"
                try
                    set mailOwnWindowCount to (count of windows) as string
                on error mailCountErrMsg
                    -- Same shape as accessibilityWindowCount's "unknown:<error>":
                    -- a probe that cannot answer is itself part of the diagnosis,
                    -- and this string is read by a human deciding what to fix.
                    set mailOwnWindowCount to "unknown (" & mailCountErrMsg & ")"
                end try
                return "{REPLY_ACCESSIBILITY_UNAVAILABLE}" & return & "Detail: System Events reports 0 windows for Mail after " & {AX_WINDOW_SETTLE_ATTEMPTS} & " attempts; Mail's own scripting dictionary reports " & mailOwnWindowCount & " window(s)"
            end if
        end if
        set preReplyWindowIds to my mailWindowIdSnapshot()

        -- Native Mail reply: Mail builds its own rich quoted thread and inserts the
        -- account's default reply signature into the opened window. Content is never
        -- reassigned below, so that native formatting is preserved.
        set replyMessage to reply foundMessage {reply_options}
        delay 1.2
        activate
        delay 0.4
        set replyWindowId to my newlyOpenedReplyWindowId(preReplyWindowIds, derivedReplySubject)

        -- Prefer Mail's own outgoing reply subject when it is the same reply thread.
        -- Mail collapses duplicate Re:/Fwd: prefixes, so the derived source subject
        -- can disagree with the compose title; adopt normalized titles only after a
        -- subject-core match so an unrelated open compose window is never adopted.
        try
            set replyMessageSubject to subject of replyMessage as string
            if replyMessageSubject is not "" then
                if my subjectCoresMatch(replyMessageSubject, derivedReplySubject) then
                    set replySubject to replyMessageSubject
                end if
            end if
        end try
        -- Prefer Mail's live compose-window title when it is the same reply thread.
        try
            set mailWindowTitle to name of front window as string
            if mailWindowTitle is not "" then
                if my subjectCoresMatch(mailWindowTitle, derivedReplySubject) then
                    set replySubject to mailWindowTitle
                end if
            end if
        end try

        -- Identity tweaks on the already-good native window (see
        -- reply_window_identity_scripts: the sender fails closed, the signature does not).
        {identity_tweaks_script}
        {cc_script}
        {bcc_script}
    end tell

    -- Guard the exact Mail window id, then focus its AX editor before typing.
    repeat with guardAttempt from 1 to 4
        -- Window adoption is the guard's precondition, not one of its inputs:
        -- mailOk below compares the front window's id against replyWindowId, and
        -- an empty id matches neither a numeric id nor the "(unset)" no-window
        -- answer. All four attempts are therefore doomed before the first one
        -- runs, so spending ~6s of raise/focus/settle delays on them only delays
        -- an abort that is already certain -- and reports it as a focus problem,
        -- sending the caller back to "retry with Mail visible" when the real
        -- cause is that Mail's new window could not be told apart from the ones
        -- already open.
        if replyWindowId is "" then
            set editorFocusResult to "could not identify the reply window Mail just opened; window adoption found no unique new window matching the reply subject"
            exit repeat
        end if
        set guardMail to "(unset)"
        set guardMailWindowId to "(unset)"
        set guardSE to "(unset)"
        set guardSEAnswered to false
        tell application "Mail"
            set replyWindowRaised to my raiseNativeReplyWindowSafely(replyWindowId, replySubject, derivedReplySubject)
        end tell
        delay 0.3
        -- Re-assert the front on every attempt. Raising the window inside Mail
        -- does not make Mail the frontmost *application*, and anything the user
        -- (or another automation) does between attempts can take the front back.
        set frontmostBlockedBy to my frontmostBlockedApp(my ensureMailFrontmost())
        tell application "System Events"
            tell process "Mail"
                -- Without an Accessibility grant this whole block throws, which
                -- used to escape as a generic "Error:" instead of the documented
                -- REPLY_WINDOW_FOCUS_FAILED. Catch it, name it in the abort
                -- detail, and let the guard fail closed on its own terms.
                try
                    set frontmost to true
                    delay 0.6
                    -- "" is a real answer: compose windows legitimately report an
                    -- empty AX title. "(unset)" means System Events never answered.
                    set guardSE to ""
                    set guardSEAnswered to true
                    try
                        set guardSE to name of front window
                    end try
                on error systemEventsErrMsg
                    set guardSE to "SystemEventsError:" & systemEventsErrMsg
                end try
            end tell
        end tell
        tell application "Mail"
            try
                set guardMail to name of front window
                set guardMailWindowId to id of front window as string
            end try
            -- Late adoption: if focus landed on Mail's normalized reply title,
            -- adopt it before the exact-title keystroke check.
            if guardMail is not "(unset)" and guardMail is not "" then
                if my subjectCoresMatch(guardMail, derivedReplySubject) then
                    set replySubject to guardMail
                end if
            end if
        end tell
        set mailOk to (guardMail is replySubject and guardMailWindowId is replyWindowId)
        set seOk to (guardSEAnswered and (guardSE is replySubject or guardSE is ""))
        if mailOk and seOk then
            -- Resolve the body editor once and hand the reference to the typing
            -- pass. item 1 is the diagnostic status the abort path reports; item 2
            -- is the resolved element, which spares typeReplyBodyChunks a second
            -- ``entire contents`` walk of this compose window.
            set editorFocusOutcome to my resolveReplyBodyEditor(replySubject, derivedReplySubject, replyWindowId, true)
            set editorFocusResult to item 1 of editorFocusOutcome
            if editorFocusResult is "focused" then
                set composeFocusVerified to true
                if replyBodyText is not "" then
                    set typeChunksResult to my typeReplyBodyChunks(replyBodyText, replySubject, derivedReplySubject, replyWindowId, item 2 of editorFocusOutcome)
                    if typeChunksResult is "typed" then
                        set didType to true
                    else
                        set composeFocusVerified to false
                        set typingInterruptedDetail to typeChunksResult
                    end if
                else
                    set didType to true
                end if
                exit repeat
            end if
        end if
        delay 0.5
    end repeat
    if composeFocusVerified is false then
            -- Distinguish a mid-typing focus loss from a pre-typing failure.
            set abortDetailText to "could not focus reply window"
            set abortCode to "GUARD_ABORT"
            if typingInterruptedDetail is not "" then
                set abortCode to "TYPING_INTERRUPTED"
                set abortDetailText to typingInterruptedDetail
            else if frontmostBlockedBy is not "" then
                -- Checked before the window/focus branches: when another app
                -- held the front, window adoption and AX focus were both being
                -- asked of a background Mail, so their failures are symptoms.
                -- Reporting the symptom sends the caller to "grant
                -- Accessibility" when the real fix is "stop stealing the front".
                set abortCode to "GUARD_ABORT_FRONTMOST"
                set abortDetailText to "Mail could not be brought to the front; " & frontmostBlockedBy & " held it"
            else if replyWindowId is "" then
                set abortCode to "GUARD_ABORT_WINDOW"
                set abortDetailText to editorFocusResult
            else if editorFocusResult is not "" then
                set abortDetailText to editorFocusResult
            else if guardMail is not "(unset)" and guardMail is not "" then
                if (my subjectCoresMatch(guardMail, derivedReplySubject)) is false then
                    if my looksLikeReplyWindowTitle(guardMail) then
                        set abortCode to "GUARD_ABORT_SUBJECT"
                    end if
                end if
            end if
            if (my closeNativeReplyWindowSafely(replyWindowId, replySubject, derivedReplySubject)) is false then
                -- The id-and-title close cannot fire without an adopted window,
                -- which is exactly the case that reaches here, so the compose
                -- window Mail just opened would be orphaned and every retry would
                -- leak another one. MAX_OPEN_COMPOSE_WINDOWS exists because
                -- NSWindowServer OOMs once enough accumulate. The outgoing message
                -- Mail returned from `reply` is an unambiguous handle on our own
                -- compose -- unlike a title match, it can never resolve to a window
                -- the user opened -- so closing by it is safe where a title match is
                -- not. Probed on Darwin 25.5: `close <outgoing message> saving no`
                -- closes the window and discards it unsaved. Guarded anyway, so a
                -- Mail build that refuses it leaves us exactly where we already were.
                tell application "Mail"
                    try
                        close replyMessage saving no
                    on error closeErrorText
                        -- Swallowing this would report an abort that cleaned up
                        -- after itself while a compose window is still on screen,
                        -- and the caller's next retry would add another. Say so in
                        -- the detail the abort already returns.
                        set abortDetailText to abortDetailText & " [reply window left open: " & closeErrorText & "]"
                    end try
                end tell
            end if
            {cleanup_script}
            return abortCode & return & "Subject: " & replySubject & return & "DerivedSubject: " & derivedReplySubject & return & "Detail: " & abortDetailText & " (mailFront=" & guardMail & " seFront=" & guardSE & ")"
    end if
    -- Pair the source attribution with actual source content. A bare
    -- ``wrote:`` or even sender-only attribution can occur in an authored
    -- body/signature and would falsely certify a lost native quote. The two
    -- halves travel as SEPARATE output fields: the reader is line-based
    -- (``_extract_output_field``), so a single field holding an embedded
    -- return delivered only its first line -- the attribution -- and silently
    -- dropped the source-content half this pairing exists to supply.
    set quotedNeedle to sourceSender & " wrote:"
    delay 0.4
    tell application "Mail"
        -- Adding attachments before body typing can make Mail rebuild the rich
        -- reply content and discard the quoted original. Attach only after the
        -- typed body has completed successfully.
        {attachment_script}

        {post_action}

        -- Mail's outgoing-message ID is not a Drafts ID on every account.
        -- Resolve only a uniquely new, header-linked persisted Drafts message
        -- from a complete bounded snapshot. Ambiguity, cap truncation, indexing
        -- lag, or an AppleScript failure leaves this empty, which disables the
        -- exact-ID-only delete/retype path safely.
        {draft_resolver_script}

        {draft_window_close_script}

        set outputText to "{header_text}" & return & return
        set outputText to outputText & "{success_text}" & return
        set outputText to outputText & "To: native reply recipients" & return
        set outputText to outputText & "Subject: " & replySubject & return
        if replyDraftId is not "" then set outputText to outputText & "Draft ID: " & replyDraftId & return
        if replyDraftId is not "" and replyDraftIdentityEvidence is not "" then set outputText to outputText & "Draft Identity: " & replyDraftId & "|||" & replyDraftRfcMessageId & "|||" & replyDraftSourceRfcMessageId & "|||" & replyDraftIdentityEvidence & return
        if quotedNeedle is not "" then set outputText to outputText & "Quote Needle: " & quotedNeedle & return
        if sourceQuoteAnchor is not "" then set outputText to outputText & "Quote Anchor: " & sourceQuoteAnchor & return
        {extra_output_lines}

        {cleanup_script}

        return outputText
    end tell
on error errMsg
    try
        my closeNativeReplyWindowSafely(replyWindowId, replySubject, derivedReplySubject)
    end try
    try
        {cleanup_script}
    end try
    return "Error: " & errMsg & return & "Please check that the account name is correct and the email exists."
end try
'''
