"""Subject-core AppleScript handlers for the native reply focus guard.

Leaf module split out of ``reply_scripts.py``, which sits against the 600 LOC
module budget; same rationale as ``typing_scripts.py``. Nothing here touches
I/O -- it returns handler text that the native reply builder splices in ahead of
its ``tell`` blocks.

Mail normalizes compose-window titles (``RE:  Re: Foo`` becomes ``Re: Foo``), so
exact equality against the raw source subject reads as a focus failure on a
window that is in fact the right one. These handlers compare subject *cores*
instead, which is what lets the guard adopt Mail's live front-window title.
"""


def native_reply_subject_helpers_applescript() -> str:
    """AppleScript handlers that collapse leading Re:/Fwd: prefixes for guard compares."""
    return """
on stripLeadingSpaces(rawText)
    set t to rawText as string
    repeat while t starts with " "
        if (length of t) is 1 then return ""
        set t to text 2 thru -1 of t
    end repeat
    return t
end stripLeadingSpaces

on stripReplySubjectPrefixes(rawSubject)
    set t to my stripLeadingSpaces(rawSubject)
    repeat 10 times
        if t is "" then exit repeat
        set prefixLen to 0
        ignoring case
            if t starts with "re:" then
                set prefixLen to 3
            else if t starts with "fwd:" then
                set prefixLen to 4
            else if t starts with "fw:" then
                set prefixLen to 3
            end if
        end ignoring
        if prefixLen is 0 then exit repeat
        if (length of t) is less than or equal to prefixLen then
            set t to ""
            exit repeat
        end if
        set t to my stripLeadingSpaces(text (prefixLen + 1) thru -1 of t)
    end repeat
    return t
end stripReplySubjectPrefixes

on subjectCoresMatch(leftSubject, rightSubject)
    set leftCore to my stripReplySubjectPrefixes(leftSubject)
    set rightCore to my stripReplySubjectPrefixes(rightSubject)
    if leftCore is "" or rightCore is "" then return false
    ignoring case
        return (leftCore is rightCore)
    end ignoring
end subjectCoresMatch

on looksLikeReplyWindowTitle(windowTitle)
    set t to my stripLeadingSpaces(windowTitle)
    if t is "" then return false
    ignoring case
        if t starts with "re:" then return true
        if t starts with "fwd:" then return true
        if t starts with "fw:" then return true
    end ignoring
    return false
end looksLikeReplyWindowTitle

"""
