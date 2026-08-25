"""Execute the reply quote-proof handlers under ``osascript`` and assert verdicts.

Every other test of this logic asserts on the *text* of the generated
AppleScript. Text assertions could not have caught the defect these tests
exist for: the native reply built a two-part quote proof — an attribution
line plus a span of the source body — and the line-based output reader
delivered only the first line to the verifier, so every draft was certified
by the attribution alone. The script text was exactly what it claimed to be;
the value crossing the boundary was not.

``replyBodyAboveQuoteStatus`` and its helpers are pure text manipulation with
no Mail dependency, so the handler prefix of the generated verifier — every
line before its ``tell application "Mail"`` — runs standalone under
``osascript``. That makes the verdict itself testable, not just its spelling.

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

_BODY = "Thanks, that works for me."
_ATTRIBUTION = "Sender wrote:"
_ANCHOR = "Original source paragraph text"


def _applescript_literal(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _handler_prefix() -> str:
    """Return the pure-handler head of the generated saved-reply verifier."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "NOT_FOUND"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools._verify_saved_reply_draft(
            "Work",
            "Re: Test",
            _BODY,
            draft_id="84053",
            quoted_needle=_ATTRIBUTION,
            quote_anchor=_ANCHOR,
        )

    script = scripts[0]
    return script[: script.index('tell application "Mail"')]


def _body_status(draft_content: str, *, body: str = _BODY, needle: str = _ATTRIBUTION, anchor: str = _ANCHOR) -> str:
    source = _handler_prefix() + (
        "\nreturn my replyBodyAboveQuoteStatus("
        f"{_applescript_literal(draft_content)}, "
        f"{_applescript_literal(body)}, "
        f"{_applescript_literal(needle)}, "
        f"{_applescript_literal(anchor)})\n"
    )
    completed = subprocess.run(["osascript", "-"], input=source, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def test_intact_native_quote_verifies() -> None:
    """Body above, attribution and source content below: the ordinary success."""
    assert _body_status(f"{_BODY}\n\n{_ATTRIBUTION}\n{_ANCHOR}") == "found"


def test_attribution_without_source_content_is_not_proof() -> None:
    """A quote header Mail kept over a quote body Mail dropped must not verify.

    This is the regression. The attribution line alone can survive — or be
    typed into a body or signature — with no quoted original under it, and a
    sender-only needle called that ``found``.
    """
    assert _body_status(f"{_BODY}\n\n{_ATTRIBUTION}\n") == "quote_missing"


def test_source_content_above_the_body_is_not_proof() -> None:
    """The anchor has to land after the authored body, not anywhere in the draft.

    The anchor here ends in a period so the authored body still matches: the
    compare neutralizes autocapitalization at sentence starts, so a body whose
    first word sits mid-sentence in the draft reads as ``missing`` rather than
    a quote verdict. Both fail closed, but this case is about the quote.
    """
    anchor = f"{_ANCHOR}."
    assert _body_status(f"{anchor}\n\n{_BODY}\n\n{_ATTRIBUTION}\n", anchor=anchor) == "quote_missing"


def test_rewrapped_source_paragraph_still_verifies() -> None:
    """Mail re-wrapping the quoted original must not read as a lost quote.

    The compare is whitespace-flattened on both sides, which is what lets the
    anchor be a contiguous span of source text rather than a whole line. Were
    this to fail, correct drafts would be reported missing and the retry path
    would delete them, so it is the load-bearing safety case for the anchor.
    """
    assert _body_status(f"{_BODY}\n\n{_ATTRIBUTION}\nOriginal source\nparagraph text") == "found"


def test_missing_anchor_degrades_to_attribution_only_proof() -> None:
    """A source with no usable anchor still verifies on the attribution alone.

    Short sources ("Thanks!", "Approved.") are ordinary mail. Weaker proof for
    them beats refusing to reply to them.
    """
    assert _body_status(f"{_BODY}\n\n{_ATTRIBUTION}\n", anchor="") == "found"


def test_absent_authored_body_still_fails() -> None:
    """The body check is unchanged: an intact quote cannot stand in for it."""
    assert _body_status(f"{_ATTRIBUTION}\n{_ANCHOR}") == "missing"
