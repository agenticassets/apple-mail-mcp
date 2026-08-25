"""Every AppleScript handler's ``end`` name must match its ``on`` name.

``osacompile`` does not enforce this, and that is the whole problem. Given::

    on alpha(x)
        return x
    end beta

it exits 0 and silently rewrites the tail to ``end alpha``. So the repo's
compile gate -- ``tests/cross_cutting/test_applescript_builders_compile.py``
and the ``check_applescript_compiles.py`` edit hook -- cannot see a mismatch,
by construction. Only a source-text lint can.

What the mismatch actually costs is reading time. A handler renamed in the
``on`` line but not the ``end`` line leaves a phantom identifier in the file:
``reply_draft_resolver_scripts.py`` carried ``end rfcMessageIdWasPresent`` under
``on identifierWasPresent`` long enough that grepping for the old name still hit
this file and suggested a handler that no longer existed. These fragments are
spliced into each other as text, so "which handlers does this script define" is
a question people answer by grepping, and a stale tail answers it wrong.

The scan is over Python source text rather than built scripts on purpose: the
fragments are assembled from several modules and no single built script
contains all of them, so scanning the sources is the only pass that reaches
every handler.
"""

from __future__ import annotations

import re
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "plugin" / "apple_mail_mcp"

#: ``end <word>`` closers that terminate a block, not a handler.
_BLOCK_CLOSERS = frozenset(
    {
        "considering",
        "if",
        "ignoring",
        "repeat",
        "script",
        "tell",
        "timeout",
        "try",
        "using",  # "end using terms from"
    }
)

_HANDLER_OPEN = re.compile(r"^\s*on\s+([A-Za-z_]\w*)\s*\(", re.MULTILINE)
_BLOCK_END = re.compile(r"^\s*end\s+([A-Za-z_]\w*)", re.MULTILINE)


def _handler_pairs(text: str) -> list[tuple[str, str, int]]:
    """Return ``(on_name, end_name, line_number)`` for each handler in *text*.

    AppleScript handlers cannot nest, so pairing openers with non-block
    ``end`` closers in document order is exact.
    """
    opens = [(m.start(), m.group(1)) for m in _HANDLER_OPEN.finditer(text)]
    ends = [
        (m.start(), m.group(1))
        for m in _BLOCK_END.finditer(text)
        if m.group(1) not in _BLOCK_CLOSERS
    ]
    pairs: list[tuple[str, str, int]] = []
    for offset, on_name in opens:
        closer = next((name for pos, name in ends if pos > offset), None)
        if closer is None:
            continue
        pairs.append((on_name, closer, text.count("\n", 0, offset) + 1))
    return pairs


def test_handler_end_names_match_their_on_names() -> None:
    mismatches: list[str] = []
    scanned = 0
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for on_name, end_name, line in _handler_pairs(text):
            scanned += 1
            if on_name != end_name:
                rel = path.relative_to(_SOURCE_ROOT.parents[1])
                mismatches.append(f"{rel}:{line}: on {on_name}(...) ... end {end_name}")
    assert scanned > 20, f"handler scan found only {scanned} handlers -- the regex stopped matching"
    assert not mismatches, "AppleScript handler end-names do not match their on-names:\n" + "\n".join(
        mismatches
    )
