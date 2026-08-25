"""The typed reply dismisses macOS autocorrect panels between chunks.

Root cause, proven live on Darwin 25.5 (2026-08-24) by sampling a wedged Mail:
macOS autocorrect / inline predictions react to the synthesized keystrokes and
open an ``NSCorrectionPanel``. Its ``_interceptEvents`` runs a **nested modal
event loop** that pumps UI events but does not dispatch Apple Events. All 2,293
main-thread samples sat in that loop.

The reply path's next statement after typing a chunk is
``chunkFocusBlockedName``, which opens ``tell application "Mail"``. With a panel
up, that call never returns: measured 29 probes over 10 minutes, ``-1712`` on
every one, no spontaneous recovery. ``run_applescript`` then SIGKILLs the
subprocess at its deadline, so the failure arrives with no script state at all
and a compose window left behind.

Reproduction was not exotic -- freshly restarted Mail, one window, 1,200
characters at ``TYPING_CHUNK_SIZE`` 80 (15 chunks), timed out at 120.2 s. Runs
of 1 and 5 chunks passed; 15 and 20 wedged. With the dismissal in place the same
1,200-character reply completed in 30.9 s with ``body_verified:
full_above_quote``.

Three properties carry the fix and each is pinned below, because each one was
established by a measurement that the code alone does not explain.
"""

from __future__ import annotations

import re
import unittest

from apple_mail_mcp.tools.compose.typing_scripts import build_chunked_typing_handler


def _handler(chunk_size: int = 80, inter_chunk_delay: float = 0.35) -> str:
    return build_chunked_typing_handler(chunk_size=chunk_size, inter_chunk_delay=inter_chunk_delay)


def _code_only(script: str) -> str:
    """Drop AppleScript comment lines.

    The handler documents the rejected ``tell process "Mail"`` form in its own
    comment, so a naive substring check would match the prose warning against
    the very thing it is warning about.
    """
    return "\n".join(line for line in script.splitlines() if not line.strip().startswith("--"))


class CorrectionPanelDismissalTests(unittest.TestCase):
    def test_the_dismissal_is_posted_unscoped(self) -> None:
        """Scoping the key event to the process does not release the loop.

        Measured back to back on one wedged process: ``tell process "Mail" to
        key code 53`` left it at ``-1712``; an unscoped ``key code 53`` after
        forcing frontmost had Mail answering in 0.1 s. The nested loop drains the
        *frontmost application's* event queue, which a process-scoped
        accessibility-path event does not enter.

        A refactor that tucks this inside the neighbouring ``tell process
        "Mail"`` block would look tidier and silently restore the hang.
        """
        script = _code_only(_handler())

        body = script[script.index("on dismissTextSuggestionPanel()") : script.index("end dismissTextSuggestionPanel")]

        self.assertIn('tell application "System Events" to key code 53', body)
        self.assertNotIn('tell process "Mail"', body)

    def test_the_dismissal_is_not_wrapped_in_a_bare_try(self) -> None:
        """Swallowing this throw hides the wedge instead of surviving it.

        The tempting reading is that the dismissal is opportunistic -- a no-op
        when no panel is up -- so a failure to post it should not abort a
        half-typed body. An earlier revision wrapped it in a bare ``try`` on
        exactly that reasoning, and it was wrong twice over.

        ``keystroke chunkText`` in the caller's loop is unguarded, so a System
        Events failure is already fatal one line above this. And the swallow
        buys nothing where it counts: with a panel up, the next statement is
        ``chunkFocusBlockedName``'s ``tell application "Mail"``, which blocks
        for 10+ minutes and dies as a context-free SIGKILL. There is no next
        chunk to retry the dismissal on. Silence there turns a diagnosable
        error into the precise failure this handler was written to prevent.

        ``tests/core/test_no_bare_applescript_try.py`` catches the regression
        as a ratchet count; this pins the reason so it is not re-added with a
        non-bare handler that is equally silent.
        """
        script = _handler()
        body = script[script.index("on dismissTextSuggestionPanel()") : script.index("end dismissTextSuggestionPanel")]

        self.assertNotIn("end try", _code_only(body))

    def test_the_dismissal_runs_after_the_settle_delay(self) -> None:
        """Order is the whole point: dismiss *after* the pause, not before it.

        The correction panel opens on a delay after the keystrokes settle
        (``shouldPredictAfterDelayForCandidate``). Dismissing before the pause
        races the panel and lets it open into the gap, which is exactly the
        window the next iteration's ``tell application "Mail"`` walks into.
        """
        script = _handler(inter_chunk_delay=0.35)
        loop = script[script.index("on typeReplyBodyChunks") :]

        keystroke = loop.index("keystroke chunkText")
        settle = loop.index("delay 0.35")
        dismiss = loop.index("my dismissTextSuggestionPanel()")

        self.assertLess(keystroke, settle)
        self.assertLess(settle, dismiss)

    def test_every_chunk_is_followed_by_a_dismissal(self) -> None:
        """Including the last one, whose panel would block the *caller*.

        The final chunk has no next iteration, but the caller resumes with its
        own Mail work (window raise, save, verification). A dismissal that only
        guarded the inter-chunk boundary would leave the most common shape --
        a body that ends on a real word -- still able to wedge after typing
        finished.
        """
        script = _code_only(_handler())
        loop = script[script.index("on typeReplyBodyChunks") : script.index("end typeReplyBodyChunks")]

        # Inside the OUTER repeat body, unconditionally -- not under the
        # `if chunkStart is less than or equal to bodyLength` guard that skips
        # the settle delay on the final pass. `rindex` because the chunk-boundary
        # scan nests two inner repeat loops, so the first `end repeat` closes one
        # of those, not the typing loop.
        repeat_body = loop[loop.index("repeat while chunkStart") : loop.rindex("end repeat")]
        self.assertIn("my dismissTextSuggestionPanel()", repeat_body)

        dismiss_line = next(
            line for line in repeat_body.splitlines() if "my dismissTextSuggestionPanel()" in line
        )
        self.assertNotIn("if ", dismiss_line)

    def test_the_handler_is_defined_once(self) -> None:
        script = _handler()

        self.assertEqual(len(re.findall(r"^on dismissTextSuggestionPanel\(\)", script, re.M)), 1)


if __name__ == "__main__":
    unittest.main()
