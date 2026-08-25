"""Save and put back the user's clipboard around the HTML compose paste.

HTML compose builds its message by putting HTML on the general pasteboard and
pressing Cmd+V, so it necessarily destroys whatever the user had copied. It is
expected to put it back.

The original save read one flavor -- ``stringForType:NSPasteboardTypeString``
-- and skipped the restore entirely when that came back ``missing value``. That
is precisely what a non-text clipboard returns: an image copied out of Preview,
files copied in Finder, RTF-only content from Word. In those cases the user's
copy was replaced by the tool's HTML and never given back, and a pasteboard has
no undo. Rich text survived only as its plain-text flavor.

So the snapshot copies every item and every flavor into detached
``NSPasteboardItem`` objects, and the restore writes them all back. Both halves
live here because the success path and the error handler each restore, in
different modules; when they were spelled separately they were free to drift.
"""

from typing import Final

#: Name of the AppleScript variable holding the detached snapshot.
SAVED_ITEMS_VAR: Final[str] = "savedPasteboardItems"


def pasteboard_snapshot_script(*, pasteboard_var: str = "pb", items_var: str = SAVED_ITEMS_VAR) -> str:
    """Return AppleScript copying the whole pasteboard into detached items.

    Must run before the pasteboard is cleared. The items the pasteboard hands
    out are owned by it and go invalid at ``clearContents()``, so each flavor's
    data is copied into a fresh ``NSPasteboardItem`` we own.

    A flavor whose ``dataForType:`` yields ``missing value`` is skipped rather
    than aborting the snapshot: promised (lazily provided) flavors do that, and
    restoring the flavors we could read beats restoring none of them.

    Snapshotting is best effort by design. If it fails outright the variable is
    left empty and nothing is restored, which is exactly the behavior this
    replaces -- a compose is not worth failing over a clipboard copy.
    """
    return f"""
set {items_var} to {{}}
try
    repeat with anItem in ({pasteboard_var}'s pasteboardItems())
        set copiedItem to current application's NSPasteboardItem's alloc()'s init()
        set copiedAnyFlavor to false
        repeat with aFlavor in (anItem's types())
            set flavorData to (anItem's dataForType:aFlavor)
            if flavorData is not missing value then
                (copiedItem's setData:flavorData forType:aFlavor)
                set copiedAnyFlavor to true
            end if
        end repeat
        if copiedAnyFlavor then set end of {items_var} to copiedItem
    end repeat
on error snapshotError
    log "apple-mail-mcp: clipboard snapshot failed, clipboard will not be restored: " & snapshotError
    set {items_var} to {{}}
end try
"""


def pasteboard_restore_script(*, pasteboard_var: str = "pb", items_var: str = SAVED_ITEMS_VAR) -> str:
    """Return AppleScript putting the snapshot back on the pasteboard.

    Guarded, and the handler cannot itself throw. On the success path this runs
    after the draft is already saved, inside the transaction's outer ``try``, so
    a throw here would route a saved draft into the failure handler and report
    a compose that did not happen. ``log`` writes to stderr, which
    ``run_applescript`` reads only when osascript exits nonzero -- so the detail
    is there for a failing run and silent for a passing one.

    An empty snapshot means the clipboard was empty or unreadable; leaving the
    pasteboard alone is right in both cases.
    """
    return f"""
try
    if (count of {items_var}) > 0 then
        {pasteboard_var}'s clearContents()
        {pasteboard_var}'s writeObjects:{items_var}
    end if
on error restoreError
    log "apple-mail-mcp: clipboard restore failed: " & restoreError
end try
"""
