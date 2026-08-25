"""Round-trip the clipboard snapshot/restore pair under ``osascript``.

HTML compose owns the clipboard for the length of a paste and is expected to
give it back. The restore it replaces read one flavor,
``stringForType:NSPasteboardTypeString``, and skipped the restore entirely when
that returned ``missing value`` -- which is what a non-text clipboard returns.
An image copied out of Preview, or files copied in Finder, were replaced by the
tool's HTML and never returned, and a pasteboard has no undo.

These tests run against a private pasteboard from
``pasteboardWithUniqueName()``, never the general one, so running the suite
cannot disturb whatever the person at the keyboard has copied. That is what the
builders' ``pasteboard_var`` parameter is for.

Skipped where ``osascript`` is absent, matching
``tests/cross_cutting/test_applescript_builders_compile.py``.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from apple_mail_mcp.tools.compose.clipboard_scripts import (
    pasteboard_restore_script,
    pasteboard_snapshot_script,
)

pytestmark = pytest.mark.skipif(shutil.which("osascript") is None, reason="osascript not available on this host")

_SCRATCH_PB = "scratchPb"


def _roundtrip(*, seed: str, flavor: str) -> str:
    """Seed a private pasteboard, clobber it the way compose does, restore it.

    Returns ``OK:<payload>`` when the seeded flavor came back with its bytes
    intact and the tool's HTML is gone, ``LOST`` when the flavor did not come
    back at all, or ``HTML_LEFTOVER:<payload>`` when the tool's own data
    survived the restore.
    """
    snapshot = pasteboard_snapshot_script(pasteboard_var=_SCRATCH_PB)
    restore = pasteboard_restore_script(pasteboard_var=_SCRATCH_PB)
    source = f"""
use framework "Foundation"
use framework "AppKit"
use scripting additions

set {_SCRATCH_PB} to current application's NSPasteboard's pasteboardWithUniqueName()
set seedData to (current application's NSString's stringWithString:"{seed}")'s dataUsingEncoding:(current application's NSUTF8StringEncoding)
{_SCRATCH_PB}'s clearContents()
{_SCRATCH_PB}'s setData:seedData forType:"{flavor}"

{snapshot}

-- What the HTML compose transaction does to the clipboard.
{_SCRATCH_PB}'s clearContents()
set htmlData to (current application's NSString's stringWithString:"<p>tool html</p>")'s dataUsingEncoding:(current application's NSUTF8StringEncoding)
{_SCRATCH_PB}'s setData:htmlData forType:(current application's NSPasteboardTypeHTML)

{restore}

set verdict to "LOST"
set recovered to ({_SCRATCH_PB}'s dataForType:"{flavor}")
if recovered is not missing value then
    set recoveredText to (current application's NSString's alloc()'s initWithData:recovered encoding:(current application's NSUTF8StringEncoding)) as string
    if ({_SCRATCH_PB}'s dataForType:(current application's NSPasteboardTypeHTML)) is not missing value then
        set verdict to "HTML_LEFTOVER:" & recoveredText
    else
        set verdict to "OK:" & recoveredText
    end if
end if
{_SCRATCH_PB}'s releaseGlobally()
return verdict
"""
    completed = subprocess.run(["osascript", "-"], input=source, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def test_a_non_text_clipboard_survives_the_paste() -> None:
    """The defect: a copied image was destroyed, not restored."""
    assert _roundtrip(seed="synthetic-png-bytes", flavor="public.png") == "OK:synthetic-png-bytes"


def test_file_urls_survive_the_paste() -> None:
    """Files copied in Finder are the other everyday non-text clipboard."""
    assert _roundtrip(seed="file:///synthetic/path", flavor="public.file-url") == "OK:file:///synthetic/path"


def test_plain_text_still_round_trips() -> None:
    """The case the old single-flavor restore did handle stays handled."""
    assert _roundtrip(seed="copied text", flavor="public.utf8-plain-text") == "OK:copied text"


def test_an_empty_snapshot_leaves_the_pasteboard_alone() -> None:
    """Nothing to give back is not a reason to clear what is there."""
    restore = pasteboard_restore_script(pasteboard_var=_SCRATCH_PB)
    source = f"""
use framework "Foundation"
use framework "AppKit"
use scripting additions

set {_SCRATCH_PB} to current application's NSPasteboard's pasteboardWithUniqueName()
set savedPasteboardItems to {{}}
{_SCRATCH_PB}'s clearContents()
set laterData to (current application's NSString's stringWithString:"someone else's copy")'s dataUsingEncoding:(current application's NSUTF8StringEncoding)
{_SCRATCH_PB}'s setData:laterData forType:"public.utf8-plain-text"

{restore}

set survivor to ({_SCRATCH_PB}'s dataForType:"public.utf8-plain-text")
set verdict to "CLEARED"
if survivor is not missing value then
    set verdict to (current application's NSString's alloc()'s initWithData:survivor encoding:(current application's NSUTF8StringEncoding)) as string
end if
{_SCRATCH_PB}'s releaseGlobally()
return verdict
"""
    completed = subprocess.run(["osascript", "-"], input=source, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "someone else's copy"
