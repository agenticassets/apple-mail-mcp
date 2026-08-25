"""AppleScript handlers for identifying and closing one native reply window."""


def native_reply_window_handlers_applescript() -> str:
    """Return id-based native-reply window handlers.

    A reply title is diagnostic only.  The caller snapshots Mail window ids before
    creating the reply, adopts exactly one new same-thread window afterward, and
    never closes a window unless that exact id still has an expected title.
    """
    return """
on mailWindowIdSnapshot()
    tell application "Mail"
        try
            set windowIds to {}
            repeat with aWindow in every window
                set end of windowIds to id of aWindow as string
            end repeat
            return windowIds
        on error
            return missing value
        end try
    end tell
end mailWindowIdSnapshot

on windowIdWasPresent(candidateId, priorWindowIds)
    repeat with priorId in priorWindowIds
        if (contents of priorId as string) is candidateId then return true
    end repeat
    return false
end windowIdWasPresent

on newlyOpenedReplyWindowId(priorWindowIds, derivedTitle)
    if priorWindowIds is missing value then return ""
    tell application "Mail"
        try
            set matchingIds to {}
            repeat with candidateWindow in every window
                set candidateId to id of candidateWindow as string
                if (my windowIdWasPresent(candidateId, priorWindowIds)) is false then
                    set candidateTitle to name of candidateWindow as string
                    if my subjectCoresMatch(candidateTitle, derivedTitle) then set end of matchingIds to candidateId
                end if
            end repeat
            if (count of matchingIds) is 1 then return item 1 of matchingIds as string
        end try
    end tell
    return ""
end newlyOpenedReplyWindowId

on ensureMailFrontmost()
    -- Mail must own the front before System Events drives its compose window:
    -- keystrokes go to whatever app is frontmost, so typing into a background
    -- Mail lands in someone else's window with no error raised. `activate` is
    -- a request, not a guarantee -- macOS routinely defers it when the caller
    -- is a background osascript, which is exactly how this runs under an MCP
    -- host -- so poll until the process actually reports frontmost, and name
    -- whoever held the front when it never does. Returns "frontmost",
    -- "blocked:<app>", or "unavailable:<error>" (no Accessibility grant).
    set blockingApp to "(unknown)"
    set activateNote to ""
    repeat with frontAttempt from 1 to 5
        try
            tell application "Mail" to activate
        on error activateErrMsg
            -- Not fatal on its own: the frontmost poll below is the real test,
            -- and Mail can already be front when `activate` refuses. Carry the
            -- reason so a caller that never reaches the front is not left
            -- guessing whether activation was even attempted.
            set activateNote to " (activate error: " & activateErrMsg & ")"
        end try
        delay 0.25
        try
            tell application "System Events"
                if frontmost of process "Mail" then return "frontmost"
                try
                    set blockingApp to name of first process whose frontmost is true
                on error blockingErrMsg
                    set blockingApp to "(front app unreadable: " & blockingErrMsg & ")"
                end try
            end tell
        on error frontErrMsg
            return "unavailable:" & frontErrMsg
        end try
    end repeat
    return "blocked:" & blockingApp & activateNote
end ensureMailFrontmost

on accessibilityWindowCount()
    -- How many windows System Events can actually see for Mail.
    --
    -- This is the difference between "Mail has no windows" and "this client
    -- cannot see Mail's windows", and the two are indistinguishable everywhere
    -- else in the guard: `name of front window` throws for both, and the
    -- caller's `try` turns that into an empty title, which the focus guard
    -- accepts as a legitimate answer from a compose window.
    --
    -- Measured on Darwin 25.5 (2026-08-24) with the display asleep: System
    -- Events reported frontmost=true for Mail and 0 windows for *every*
    -- foreground application on the machine, raising no error, while Mail's own
    -- scripting layer reported 13 windows at the same moment. The same reading
    -- appears when the Accessibility grant is not in effect, and neither is
    -- distinguishable from the other here. Confirmed by the recovery: waking the
    -- display restored the count with no permission change. Without this probe
    -- the reply path opens a compose window, spends four doomed focus attempts
    -- on it (~82 s measured), and reports a focus failure whose remediation
    -- ("retry with Mail visible") cannot work on a display that is not on.
    --
    -- Returns a count as a string, or "unknown:<error>" when the probe itself
    -- failed -- never block on an unknown, only on a successful count of 0. The
    -- error text rides along because a probe that cannot answer is itself a
    -- diagnosis of what is wrong with the bridge.
    try
        tell application "System Events"
            tell process "Mail"
                return (count of windows) as string
            end tell
        end tell
    on error axCountErrMsg
        return "unknown:" & axCountErrMsg
    end try
end accessibilityWindowCount

on frontmostBlockedApp(frontmostResult)
    -- "" when Mail reached the front, and also when the answer was
    -- "unavailable:" -- a missing Accessibility grant is already reported on
    -- its own terms by the focus guard, and reporting it twice under two
    -- different names sends the caller chasing the wrong remedy.
    if frontmostResult starts with "blocked:" then return text 9 thru -1 of frontmostResult
    return ""
end frontmostBlockedApp

on raiseNativeReplyWindowSafely(replyWindowId, expectedTitle, derivedTitle)
    -- Never raise a title-matched window here.  A user can have multiple
    -- replies with the same subject open, so only the window adopted from the
    -- pre/post reply snapshot is eligible to become frontmost.
    if replyWindowId is "" then return false
    tell application "Mail"
        try
            set candidateWindow to first window whose id is replyWindowId
            set candidateTitle to name of candidateWindow as string
            if candidateTitle is expectedTitle or candidateTitle is derivedTitle then
                set index of candidateWindow to 1
                activate
                return true
            end if
        end try
    end tell
    return false
end raiseNativeReplyWindowSafely

on closeNativeReplyWindowSafely(replyWindowId, expectedTitle, derivedTitle)
    if replyWindowId is "" then return false
    tell application "Mail"
        try
            set candidateWindow to first window whose id is replyWindowId
            set candidateTitle to name of candidateWindow as string
            if candidateTitle is expectedTitle or candidateTitle is derivedTitle then
                close candidateWindow saving no
                return true
            end if
        end try
    end tell
    return false
end closeNativeReplyWindowSafely
"""
