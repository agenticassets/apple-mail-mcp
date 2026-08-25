"""Focus-guarded chunked System Events keystroke handler for the native reply body.

New leaf module (AGENTIC-1214) so the AppleScript handler text lives outside
``reply_scripts.py``, which is close to the 600 LOC module budget. A single
``keystroke`` of an entire reply body silently drops its tail near 320-480
characters (Bug 1) and can leak shift state into ALL CAPS output (Bug 3).
Typing the body in small chunks, clearing modifier state before and after
every chunk, and re-checking BOTH Mail's own front window and System Events'
process-level focus before every chunk keeps each keystroke call within
Mail's WebKit compose editor throughput and aborts immediately (never
re-stealing focus) the moment another window or application takes system
focus mid-typing, so a chunk can never leak into the wrong place. Clipboard
paste stays banned for the reply body (two prior live reverts; see
``reply_scripts.py`` docstring).

Two properties of the per-chunk guard are load-bearing and easy to lose in a
refactor:

**The editor is resolved once, not per chunk.** Resolution walks ``entire
contents of targetWindow``, which materializes the compose window's whole
Accessibility subtree over Apple Events. Running that before every 80-character
chunk made a 1600-character body pay twenty full subtree walks, inflating both
wall-clock latency and the typing-timeout projection in ``reply_runner``. The
resolved element reference is now carried across the loop and each chunk pays a
single ``AXFocused`` read; a failed read re-resolves once before aborting, so
the abort-on-drift guarantee is unchanged.

**Clicking the editor is only safe before any body text exists.** ``click``
seats the insertion point at the click location, so clicking mid-body would
splice the remaining chunks into the middle of already-typed text and hand back
a scrambled draft that still reports success. The click is therefore confined to
the initial resolution (empty body); every later resolution passes
``allowEditorClick`` false and fails closed instead.
"""


def build_chunked_typing_handler(*, chunk_size: int, inter_chunk_delay: float) -> str:
    """Return the AppleScript handler that types a reply body in focus-guarded chunks.

    Only the two numeric bounds are interpolated below; nothing user-derived
    reaches this text, so no AppleScript escaping is required. The returned
    handlers run at the top-level script scope (alongside the subject-helper
    handlers) and open their own ``Mail`` / ``System Events`` tells.
    """
    return f"""
on resolveReplyBodyEditor(expectedTitle, derivedTitle, expectedWindowId, allowEditorClick)
    -- Returns {{status, editorReference}}. status is "focused" when the reply
    -- window's body editor holds focus, otherwise a diagnostic name and
    -- ``missing value``. Title matching alone is not enough: users can have
    -- multiple compose windows for the same thread. First require Mail's actual
    -- front-window id, then focus a body editor in the matching System Events
    -- window before a keystroke can be sent.
    if expectedWindowId is "" then return {{"window_identity_missing", missing value}}
    set mailFrontName to "(unset)"
    set mailFrontId to "(unset)"
    tell application "Mail"
        try
            set mailFrontName to name of front window as string
            set mailFrontId to id of front window as string
        end try
    end tell
    if mailFrontId is not expectedWindowId then return {{"window_identity_mismatch", missing value}}
    if mailFrontName is not expectedTitle and mailFrontName is not derivedTitle then return {{"window_title_mismatch", missing value}}
    tell application "System Events"
        tell process "Mail"
            try
                if frontmost is false then return {{"mail_not_frontmost", missing value}}
                set targetWindow to front window
                set systemEventsTitle to name of targetWindow as string
                if systemEventsTitle is not expectedTitle and systemEventsTitle is not derivedTitle and systemEventsTitle is not "" then return {{"system_events_title_mismatch", missing value}}
                set replyEditor to missing value
                set webAreaFallback to missing value
                set allElements to entire contents of targetWindow
                repeat with candidateElement in allElements
                    try
                        set candidateRole to value of attribute "AXRole" of candidateElement as string
                        if candidateRole is "AXTextArea" then
                            set replyEditor to contents of candidateElement
                            exit repeat
                        else if candidateRole is "AXWebArea" and webAreaFallback is missing value then
                            set webAreaFallback to contents of candidateElement
                        end if
                    end try
                end repeat
                if replyEditor is missing value then set replyEditor to webAreaFallback
                if replyEditor is missing value then return {{"reply_editor_missing", missing value}}
                set editorIsFocused to false
                try
                    perform action "AXFocus" of replyEditor
                    set editorIsFocused to (value of attribute "AXFocused" of replyEditor) is true
                end try
                -- click seats the caret where it lands, so it is only safe while the
                -- body is still empty. Mid-typing callers pass allowEditorClick false
                -- and fail closed rather than splice later chunks into the middle of
                -- the text already typed.
                if editorIsFocused is not true and allowEditorClick then
                    click replyEditor
                end if
                delay 0.1
                set editorIsFocused to false
                set focusedElementMatches to false
                try
                    set editorIsFocused to value of attribute "AXFocused" of replyEditor
                end try
                try
                    set focusedUIElement to value of attribute "AXFocusedUIElement" of targetWindow
                    set focusedElementMatches to focusedUIElement is replyEditor
                end try
                if editorIsFocused or focusedElementMatches then return {{"focused", replyEditor}}
                return {{"reply_editor_not_focused", missing value}}
            on error axErrMsg
                -- Carry Mail's own words. Every other branch here returns a
                -- status that names what was wrong; this one used to return a
                -- bare label, so the single most common native-reply failure
                -- arrived in the field with nothing to diagnose it by. The
                -- `entire contents` walk above is the usual thrower (it times
                -- out on a busy compose window), and that is indistinguishable
                -- from a missing Accessibility grant without this text.
                return {{"reply_editor_focus_error:" & axErrMsg, missing value}}
            end try
        end tell
    end tell
end resolveReplyBodyEditor

on dismissTextSuggestionPanel()
    -- macOS autocorrect / inline predictions react to the synthesized keystrokes
    -- and open an NSCorrectionPanel. Its ``_interceptEvents`` runs a NESTED MODAL
    -- event loop that pumps UI events but does not dispatch Apple Events, so the
    -- next ``tell application "Mail"`` -- the one opening chunkFocusBlockedName --
    -- blocks until something dismisses the panel. Measured live: no recovery in
    -- 10 minutes, -1712 on all 29 probes, main thread 100% inside
    -- ``-[NSCorrectionPanel _interceptEvents]``. The reply then dies as a
    -- context-free subprocess SIGKILL and leaves its compose window behind.
    --
    -- Escape REJECTS the suggestion, which is also the correct answer for the
    -- other failure this same subsystem causes: autocorrect silently rewriting
    -- the typed body (REPLY_BODY_MISMATCH).
    --
    -- Posted UNSCOPED, deliberately. A ``tell process "Mail"`` key event did not
    -- release the loop in live testing; an unscoped one did, because the nested
    -- loop drains the frontmost application's event queue. Callers reach here
    -- only while the per-chunk guard has just proven Mail is frontmost.
    --
    -- Verified inert when no panel is up: on a Mail compose window with no
    -- suggestion showing, Escape left the window count unchanged and the
    -- outgoing message reachable.
    --
    -- Deliberately UNGUARDED, and the earlier revision that wrapped this in a
    -- bare ``try`` was wrong. Two reasons:
    --
    -- 1. ``keystroke chunkText`` in the caller's loop is itself unguarded, so a
    --    System Events failure is already fatal one line above this. The same
    --    failure has one cause, and it should not report differently depending
    --    on which of the two calls hit it.
    -- 2. Swallowing is worst exactly when it matters. If no panel is up, a
    --    thrown Escape cost nothing. If a panel IS up, the very next statement
    --    is chunkFocusBlockedName's ``tell application "Mail"`` -- the call that
    --    blocks for 10+ minutes and dies as a context-free SIGKILL. So a
    --    swallowed throw does not buy a retry on the next chunk; there is no
    --    next chunk. It converts a diagnosable failure into the exact wedge
    --    this handler exists to prevent, with no trace of why.
    tell application "System Events" to key code 53
end dismissTextSuggestionPanel

on replyEditorFocusHolds(editorReference)
    -- One AXFocused read on an already-resolved element, instead of another
    -- ``entire contents`` walk of the whole compose window. Window identity is
    -- proven separately by chunkFocusBlockedName before this runs.
    if editorReference is missing value then return false
    set stillFocused to false
    tell application "System Events"
        tell process "Mail"
            try
                if frontmost is false then return false
                set stillFocused to (value of attribute "AXFocused" of editorReference) is true
            end try
        end tell
    end tell
    return stillFocused
end replyEditorFocusHolds

on chunkFocusBlockedName(expectedTitle, derivedTitle, expectedWindowId)
    -- Returns "" when Mail's own front window AND System Events' process-level
    -- focus both still point at the expected reply window. Returns a diagnostic
    -- name otherwise so the caller can abort with the actual front window/app
    -- name instead of typing into whatever now holds focus.
    set mailFrontName to "(unset)"
    set mailFrontId to "(unset)"
    tell application "Mail"
        try
            set mailFrontName to name of front window
            set mailFrontId to id of front window as string
        end try
    end tell
    if expectedWindowId is "" or mailFrontId is not expectedWindowId then
        return "MailWindowId:" & mailFrontId
    end if
    if mailFrontName is not expectedTitle and mailFrontName is not derivedTitle then
        return mailFrontName
    end if
    set seOk to false
    set seFrontName to "(unset)"
    tell application "System Events"
        tell process "Mail"
            try
                if frontmost is true then
                    set seFrontName to ""
                    try
                        set seFrontName to name of front window
                    end try
                    if seFrontName is expectedTitle or seFrontName is derivedTitle or seFrontName is "" then
                        set seOk to true
                    end if
                end if
            end try
        end tell
    end tell
    if seOk then return ""
    return "SystemEvents:" & seFrontName
end chunkFocusBlockedName

on typeReplyBodyChunks(bodyText, expectedTitle, derivedTitle, expectedWindowId, preResolvedEditor)
    set bodyLength to count of characters of bodyText
    if bodyLength is 0 then return "typed"
    -- Clear any lingering modifier state before the first chunk. A truncated or
    -- interrupted prior keystroke pass is the suspected source of Bug 3's leaked
    -- shift state; releasing modifiers here resets it for this typing pass.
    tell application "System Events"
        key up shift
        key up option
        key up control
        key up command
    end tell
    -- The caller's pre-typing guard already resolved the editor, and resolution
    -- is the single most expensive step on this path: it materializes the whole
    -- compose-window Accessibility subtree over Apple Events. Re-resolving here
    -- paid for that walk a second time, microseconds after the first, to learn
    -- exactly what the guard had just learned. Adopt the guard's reference
    -- instead. Nothing is verified less: the per-chunk guard below re-proves
    -- window identity AND editor focus before the FIRST keystroke, so no chunk
    -- has ever been typed on the strength of this resolution alone. Callers with
    -- no reference in hand still resolve for themselves.
    set replyEditorReference to preResolvedEditor
    if replyEditorReference is missing value then
        set resolvedEditor to my resolveReplyBodyEditor(expectedTitle, derivedTitle, expectedWindowId, true)
        if item 1 of resolvedEditor is not "focused" then return "interrupted:" & (item 1 of resolvedEditor)
        set replyEditorReference to item 2 of resolvedEditor
    end if
    set chunkStart to 1
    repeat while chunkStart is less than or equal to bodyLength
        set chunkEnd to chunkStart + {chunk_size} - 1
        if chunkEnd > bodyLength then set chunkEnd to bodyLength
        if chunkEnd < bodyLength then
            -- Prefer a newline boundary so line structure stays intact; fall
            -- back to a space boundary so words are not split across the
            -- inter-chunk pause; fall back to the hard size boundary when
            -- neither is found in the chunk window.
            set scanIndex to chunkEnd
            set boundaryFound to false
            repeat while scanIndex > chunkStart
                set scanChar to character scanIndex of bodyText
                if scanChar is return or scanChar is linefeed then
                    set chunkEnd to scanIndex
                    set boundaryFound to true
                    exit repeat
                end if
                set scanIndex to scanIndex - 1
            end repeat
            if boundaryFound is false then
                set scanIndex to chunkEnd
                repeat while scanIndex > chunkStart
                    if character scanIndex of bodyText is space then
                        set chunkEnd to scanIndex
                        exit repeat
                    end if
                    set scanIndex to scanIndex - 1
                end repeat
            end if
        end if
        set chunkText to text chunkStart thru chunkEnd of bodyText
        -- Re-verify focus before EACH chunk (not just once before the loop).
        -- A drift mid-typing means the user or another app took focus; abort
        -- so no partial body is ever left typed into a stray window.
        set blockedName to my chunkFocusBlockedName(expectedTitle, derivedTitle, expectedWindowId)
        if blockedName is not "" then
            return "interrupted:" & blockedName
        end if
        if (my replyEditorFocusHolds(replyEditorReference)) is false then
            -- Focus left the editor without leaving the window (a header field, or
            -- a stale element reference). Re-resolve WITHOUT clicking: the caret
            -- must never be re-seated once body text exists.
            set resolvedEditor to my resolveReplyBodyEditor(expectedTitle, derivedTitle, expectedWindowId, false)
            if item 1 of resolvedEditor is not "focused" then return "interrupted:" & (item 1 of resolvedEditor)
            set replyEditorReference to item 2 of resolvedEditor
        end if
        tell application "System Events"
            tell process "Mail"
                key up shift
                keystroke chunkText
                key up shift
            end tell
        end tell
        set chunkStart to chunkEnd + 1
        if chunkStart is less than or equal to bodyLength then delay {inter_chunk_delay}
        -- After the pause, not before it: the correction panel opens on a delay
        -- after the keystrokes settle, so dismissing first would race it. This
        -- placement is what keeps every ``tell application "Mail"`` in the next
        -- iteration's guard -- and in the caller after the loop -- from landing
        -- while a modal panel owns the event loop.
        my dismissTextSuggestionPanel()
    end repeat
    return "typed"
end typeReplyBodyChunks
"""
