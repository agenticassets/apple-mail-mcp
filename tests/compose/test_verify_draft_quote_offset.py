"""Execute ``verify_draft``'s quote-boundary handlers and assert their answers.

``verify_draft`` reports two things about a draft's quoted original: whether
one is present, and where the draft's own body ends so a signature can be
looked for there. Those two questions used to be answered by different code.
The flag recognized both quote styles Mail and Outlook write; the slice keyed
on Apple's ``" wrote:"`` alone. So on an Exchange-style reply -- whose quote the
flag on the previous line had just confirmed -- the slice found no boundary and
spanned the whole body, quoted original included, and a signature quoted from
an earlier message in the thread was reported as a signature on the draft. In a
draft-first workflow where that report is what a reviewer trusts, the false
"signature present" ships an unsigned email.

Both now read one offset from ``earliestQuoteOffset``, so they cannot disagree.
Both are pure text handlers with no Mail dependency, which means the handler
prefix of the generated verifier -- every line before its ``tell application
"Mail"`` -- runs standalone under ``osascript``. These tests assert the
handlers' actual answers; a text assertion could not have caught the original
defect, because both halves of the script said exactly what they meant.

Skipped where ``osascript`` is absent, matching
``tests/cross_cutting/test_applescript_builders_compile.py``.
"""

from __future__ import annotations

import shutil
import subprocess
from unittest.mock import patch

import pytest
from apple_mail_mcp.tools import compose as compose_tools

pytestmark = pytest.mark.skipif(shutil.which("osascript") is None, reason="osascript not available on this host")

_APPLE_QUOTE = "On Monday, Sender wrote:"
_OUTLOOK_QUOTE = "-----Original Message-----"
_SIGNATURE = "Best,\nSender"
_DRAFT_BODY = "That works for me."


def _applescript_literal(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", '" & return & "') + '"'


def _full_script() -> str:
    """Return the AppleScript ``verify_draft`` generates for one draft id."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "NOT_FOUND"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools.verify_draft(draft_id="84053", account="Work")

    return scripts[0]


def _handler_prefix() -> str:
    """Return the pure-handler head of that script: everything Mail-free."""
    script = _full_script()
    return script[: script.index('tell application "Mail"')]


def _run(expression: str) -> str:
    source = f"{_handler_prefix()}\nreturn {expression}\n"
    completed = subprocess.run(["osascript", "-"], input=source, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _quote_offset(body: str) -> int:
    return int(_run(f"my earliestQuoteOffset({_applescript_literal(body)})"))


def _body_above_quote(body: str) -> str:
    offset = _quote_offset(body)
    return _run(f"my bodyAboveQuote({_applescript_literal(body)}, {offset})")


def test_apple_style_quote_is_located() -> None:
    """The boundary is the ``" wrote:"`` marker, mid-attribution-line.

    So the text above the quote keeps the ``"On Monday, Sender"`` fragment.
    That is the long-standing behavior of this cut, unchanged here: the
    fragment belongs to the attribution line, not to the quoted body, so it
    cannot carry a signature the check would misattribute.
    """
    body = f"{_DRAFT_BODY}\n\n{_APPLE_QUOTE}\nquoted text"
    assert _quote_offset(body) == body.index(" wrote:") + 1


def test_outlook_style_quote_is_located() -> None:
    """The regression: recognized by the flag, invisible to the slice."""
    body = f"{_DRAFT_BODY}\n\n{_OUTLOOK_QUOTE}\nquoted text"
    assert _quote_offset(body) == body.index(_OUTLOOK_QUOTE) + 1


def test_no_quote_header_reports_zero() -> None:
    assert _quote_offset("A message with no quoted original at all.") == 0


def test_the_earliest_of_several_styles_wins() -> None:
    """A thread carrying both styles must break at the first one, not the last."""
    body = f"{_DRAFT_BODY}\n\n{_OUTLOOK_QUOTE}\nolder text\n\n{_APPLE_QUOTE}\noldest text"
    assert _quote_offset(body) == body.index(_OUTLOOK_QUOTE) + 1


def test_outlook_quoted_signature_is_not_read_as_the_drafts_own() -> None:
    """The defect, end to end, at the boundary the signature check reads."""
    body = f"{_DRAFT_BODY}\n\n{_OUTLOOK_QUOTE}\nolder message\n{_SIGNATURE}"
    above = _body_above_quote(body)
    assert _DRAFT_BODY in above
    assert _SIGNATURE not in above


def test_apple_quoted_signature_is_not_read_as_the_drafts_own() -> None:
    body = f"{_DRAFT_BODY}\n\n{_APPLE_QUOTE}\nolder message\n{_SIGNATURE}"
    above = _body_above_quote(body)
    assert _DRAFT_BODY in above
    assert _SIGNATURE not in above


def test_a_draft_that_is_only_a_quote_has_no_body_above_it() -> None:
    """Offset 1: the old ``> 1`` test fell through to the whole body here."""
    body = f"{_OUTLOOK_QUOTE}\nolder message\n{_SIGNATURE}"
    assert _quote_offset(body) == 1
    assert _body_above_quote(body) == ""


def test_unquoted_draft_keeps_its_whole_body() -> None:
    body = f"{_DRAFT_BODY}\n{_SIGNATURE}"
    above = _body_above_quote(body)
    assert _DRAFT_BODY in above
    assert "Best," in above


def test_both_consumers_read_one_shared_offset() -> None:
    """Source-level: the flag and the slice cannot drift apart again."""
    script = _full_script()
    assert "on earliestQuoteOffset(bodyText)" in script
    assert "on bodyAboveQuote(bodyText, quoteOffset)" in script
    assert script.count("my earliestQuoteOffset(draftBody)") == 1, "one scan, reused by both consumers"
    assert "if quoteOffset > 0 then set quotedOriginal" in script
    assert "my bodyAboveQuote(draftBody, quoteOffset)" in script
