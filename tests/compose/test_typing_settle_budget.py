"""The typed reply waits for the compose editor to DRAIN, on a scaled budget.

Root cause, measured live on Darwin 25.5 (2026-08-25). ``keystroke`` returns when
events are POSTED, not when WebKit has processed them, and the typing loop skips
its inter-chunk delay after the final chunk -- so the script went from the last
``keystroke`` straight to ``save``. Whatever the editor had not drained at save
time was simply absent from the draft. Every observed corruption was pure
truncation with no substitution anywhere in the retained prefix, always cutting
MID-CHUNK, and always ending in a non-breaking space where typing stopped.

The budget has to scale, and a flat one was the second half of the defect. At a
flat 6 s: a 2,400-character body at chunk size 300 drained in time, while a
5,000-character body at the same chunk size saved a clean 3,179-character prefix
and lost the remaining 1,821. The identical run passed once the budget was raised
to 50 s. The tail was LATE, not dropped -- which is the whole reason waiting is
the fix rather than retyping.

The same measurement retired the chunk-size theory. Chunk 600 on a 2,400-character
body failed three of four runs against the flat budget and passed against 50 s,
taking 72.6 s where chunk 300 takes 22.9 s. Bigger chunks post events faster; the
editor drains no faster. See ``constants.TYPING_CHUNK_SIZE``.

Each property below was established by a measurement the code alone does not
explain, so each is pinned separately.
"""

from __future__ import annotations

import unittest

from apple_mail_mcp.tools.compose import constants as compose_constants
from apple_mail_mcp.tools.compose import reply_typing_budget
from apple_mail_mcp.tools.compose.typing_scripts import build_chunked_typing_handler


def _handler() -> str:
    return build_chunked_typing_handler(
        chunk_size=compose_constants.TYPING_CHUNK_SIZE,
        inter_chunk_delay=compose_constants.TYPING_INTER_CHUNK_DELAY,
    )


def _code_only(script: str) -> str:
    return "\n".join(line for line in script.splitlines() if not line.strip().startswith("--"))


def _poll() -> str:
    """The emitted ``waitForTypedBodyToSettle`` handler, comment lines stripped."""
    code = _code_only(_handler())
    return code[code.index("on waitForTypedBodyToSettle(") :]


def _typing_loop() -> str:
    """The emitted ``typeReplyBodyChunks`` handler, comment lines stripped."""
    code = _code_only(_handler())
    return code[code.index("on typeReplyBodyChunks(") : code.index("end typeReplyBodyChunks")]


class SettleBudgetScalingTests(unittest.TestCase):
    def test_an_empty_body_gets_no_budget(self) -> None:
        self.assertEqual(compose_constants.typing_settle_attempts(0), 0)
        self.assertEqual(compose_constants.typing_settle_attempts(-1), 0)

    def test_the_budget_grows_with_the_body(self) -> None:
        """A flat budget is the defect; length is what it must track."""
        short = compose_constants.typing_settle_attempts(2_400)
        long = compose_constants.typing_settle_attempts(5_000)
        self.assertGreater(long, short)

    def test_the_measured_failures_are_inside_the_budget(self) -> None:
        """The two live numbers this constant was fitted to.

        5,000 characters needed under 50 s of drain and got 81 s; 2,400 needed
        under 6 s at the shipped chunk size and gets 42 s. Both margins are
        deliberate -- the wait is only ever paid out on a body that has not
        drained yet, and a fast host exits on the first poll.
        """
        delay = compose_constants.TYPING_SETTLE_DELAY
        self.assertGreater(compose_constants.typing_settle_attempts(5_000) * delay, 50)
        self.assertGreater(compose_constants.typing_settle_attempts(2_400) * delay, 6)

    def test_the_budget_saturates(self) -> None:
        """Unbounded growth would hand a pathological body an unbounded hold on
        the Mail lock. The ceiling is what makes the refusal cap computable."""
        self.assertEqual(
            compose_constants.typing_settle_attempts(10_000_000),
            compose_constants.TYPING_SETTLE_MAX_ATTEMPTS,
        )


class SettleScriptTests(unittest.TestCase):
    def test_the_script_scales_its_own_budget_from_the_body(self) -> None:
        """Computed in AppleScript from ``bodyLength``, not interpolated.

        A retype pass runs the same handler over a different body. Baking a
        single attempt count into the script at build time would give the retype
        the first body's budget.
        """
        code = _code_only(_handler())
        self.assertIn("set settleAttempts to", code)
        self.assertIn("bodyLength", code.split("set settleAttempts to", 1)[1].split("\n", 1)[0])
        self.assertIn("repeat with settleAttempt from 1 to settleAttempts", code)

    def test_the_wait_is_a_poll_not_a_sleep(self) -> None:
        """A flat sleep would tax every fast host to protect the slow case."""
        code = _code_only(_handler())
        self.assertIn('attribute "AXValue"', code)
        self.assertIn("contains bodyTail", code)

    def test_it_waits_on_the_tail_not_the_whole_body(self) -> None:
        """Autocorrect can rewrite a character mid-body. Matching the whole body
        here would fail a draft that is in fact complete, and the case-sensitive
        verification against the SAVED draft is what decides correctness."""
        code = _code_only(_handler())
        self.assertIn("set bodyTail to text (bodyLength - tailLength + 1) thru bodyLength of bodyText", code)

    def test_a_throwing_editor_read_stops_the_poll_instead_of_being_swallowed(self) -> None:
        """An AXValue read that throws keeps throwing -- the element reference is
        stale or the bridge is gone. Retrying it for the whole budget buys
        nothing, so the poll stops rather than looping on it. Regression guard
        for the bare ``try`` this started as.

        Stopping the POLL is not the same as stopping the WAIT: when nothing was
        ever observed the script still spends the budget blind, which
        ``UnobservedDrainStillWaitsTests`` pins separately.
        """
        code = _code_only(_handler())
        self.assertIn("on error settleReadErrMsg", code)
        self.assertIn('if settleReadFailure is not "" then', code)
        self.assertIn('return "read_failed:" & settleReadFailure', code)
        # The failure text reaches the return value, so a transient read failure
        # and a clean drain are no longer indistinguishable. The value is still
        # discarded by the caller -- this is a diagnostic, not a control signal.
        self.assertIn('return "unobserved_read_failed:" & settleReadFailure', code)

    def test_the_settle_runs_before_the_body_is_reported_typed(self) -> None:
        code = _code_only(_handler())
        settle_call = code.index("my waitForTypedBodyToSettle(")
        typed_return = code.index('return "typed"', settle_call)
        self.assertLess(settle_call, typed_return)


class SettleExitsOnLengthDeltaTests(unittest.TestCase):
    """The tail match alone is unreliable, and the cost of that is measured.

    WebKit rewrites characters as they land -- a trailing space becomes a
    non-breaking space at minimum, and smart punctuation substitution is live --
    so ``editorText contains bodyTail`` misses on bodies that in fact arrived
    intact. The poll then spent its whole scaled budget and succeeded anyway.
    Measured 2026-08-25 across runs that ALL PASSED: chunk 300 on a
    2,400-character body took 65.7 s against 22.9 s for the same configuration
    under the old flat 6 s budget, and 68.2 s at 5,000 characters, while the
    signature runs -- where the match evidently did fire -- came in at 33.5 s and
    48.5 s. So the early exit works, unreliably, and a length delta is the
    condition that survives the substitutions.

    UNVERIFIED against live Mail: these are static assertions on the emitted
    AppleScript.
    """

    def test_the_pre_count_is_taken_before_the_first_keystroke(self) -> None:
        """A count read after typing has nothing left to subtract.

        The editor already holds the quoted original and, when configured, the
        signature, so its ABSOLUTE length says nothing about this body -- the
        quote length is not derivable from ``bodyText``. Only the growth from a
        baseline captured before typing is attributable to the keystrokes.
        """
        loop_body = _typing_loop()
        pre_count = loop_body.index('set preTypingText to (value of attribute "AXValue"')
        first_keystroke = loop_body.index("keystroke chunkText")
        self.assertLess(pre_count, first_keystroke)
        self.assertIn("set preTypingLength to count of characters of preTypingText", loop_body)

    def test_the_pre_count_is_handed_to_the_poll(self) -> None:
        code = _code_only(_handler())
        self.assertIn(
            "my waitForTypedBodyToSettle(replyEditorReference, bodyText, preTypingLength, tailExitUsable)",
            code,
        )
        self.assertIn(
            "on waitForTypedBodyToSettle(editorReference, bodyText, preTypingLength, tailExitUsable)",
            code,
        )

    def test_an_unreadable_pre_count_cannot_satisfy_the_delta(self) -> None:
        """An unknown baseline must disable the delta, never satisfy it.

        A probe that could not be read and then defaulted to 0 (or to any
        guess) would hand the poll a free early exit on a body that has not
        drained -- which is exactly the truncation the wait exists to prevent.
        -1 is the explicit UNKNOWN, and the delta is guarded on it.
        """
        loop_body = _typing_loop()
        self.assertIn("set preTypingLength to -1", loop_body)
        # The sentinel is derived from the failed read, not from a bare default.
        self.assertIn("on error preTypingErrMsg", loop_body)
        self.assertIn("set preTypingLengthFailure to preTypingErrMsg", loop_body)
        self.assertIn(
            'if preTypingLengthFailure is "" and preTypingText is not missing value then',
            loop_body,
        )
        self.assertIn("if preTypingLength is greater than or equal to 0 then", _poll())

    def test_the_delta_exit_is_gated_on_the_length_having_stopped_growing(self) -> None:
        """A single satisfied delta reading is not sufficient, because of the signature.

        ``identity_tweaks_script`` is emitted before ``typeReplyBodyChunks`` is
        called, so the signature Apple Event is SENT before typing -- but that
        does not prove WebKit had rendered the signature into ``AXValue`` before
        the pre-typing count was read. If the signature lands after that read,
        the final length is pre + body + signature, and a bare delta reaches
        ``bodyLength`` while up to a signature's worth of BODY characters are
        still in flight. That is a silent early exit that reintroduces exactly
        the truncation this wait exists to prevent.

        Requiring the length to be UNCHANGED from the previous poll closes it:
        anything still arriving makes consecutive reads differ. Growth stopping
        is the "everything has landed" signal; the delta alone only says enough
        characters exist, which the wrong characters can satisfy.
        """
        poll = _poll()
        self.assertIn(
            "if (currentEditorLength - preTypingLength) is greater than or equal to bodyLength "
            'and currentEditorLength is previousEditorLength then return "settled_delta"',
            poll,
        )
        # -1 is unreachable for a real character count, so the FIRST poll can
        # never satisfy stability no matter what the editor holds.
        self.assertIn("set previousEditorLength to -1", poll)
        # Recorded only after both exits have been evaluated; recording it before
        # would compare a reading against itself and make stability vacuous.
        exit_check = poll.index("and currentEditorLength is previousEditorLength")
        record = poll.index("set previousEditorLength to currentEditorLength")
        self.assertLess(exit_check, record)

    def test_both_the_delta_and_the_tail_are_exit_conditions(self) -> None:
        """Keeping both is load-bearing in both directions.

        A substitution that SHORTENS the text (three periods collapsing to one
        ellipsis) holds the delta below ``bodyLength`` for the whole budget, so
        the tail match is what covers that body; the substitutions that leave
        length alone are what the delta covers.
        """
        poll = _poll()
        self.assertIn(
            "if (currentEditorLength - preTypingLength) is greater than or equal to bodyLength "
            'and currentEditorLength is previousEditorLength then return "settled_delta"',
            poll,
        )
        self.assertIn('if tailExitUsable and editorText contains bodyTail then return "settled_tail"', poll)

    def test_the_tail_exit_is_not_gated_on_stability(self) -> None:
        """The tail match proves the END of the body is present, which is a
        stronger statement than "growth stopped". Gating it on stability would
        make it pay a poll interval it does not owe."""
        poll = _poll()
        tail_exit = poll[poll.index("editorText contains bodyTail") :].split("\n", 1)[0]
        self.assertNotIn("previousEditorLength", tail_exit)
        self.assertNotIn("currentEditorLength", tail_exit)

    def test_the_poll_is_still_non_fatal(self) -> None:
        """The poll only buys the editor time to be worth verifying.

        Its return value is discarded and an exhausted budget returns a plain
        status string, never an error. The case-sensitive verification
        against the SAVED draft is what decides correctness, and a poll that
        failed a complete body here would fail drafts that are in fact fine.
        """
        code = _code_only(_handler())
        # Called as a statement, never bound or branched on.
        self.assertIn("\n    my waitForTypedBodyToSettle(", code)
        self.assertNotIn("set settleResult to my waitForTypedBodyToSettle(", code)
        self.assertNotIn("if (my waitForTypedBodyToSettle(", code)
        poll = _poll()
        # Budget exhaustion falls out of the loop to a plain status, not an error.
        self.assertNotIn("error ", poll.split("end repeat", 1)[1])
        self.assertIn('end repeat\n    return "budget_exhausted"', poll)


class TailExitCannotFireAgainstTheQuotedOriginalTests(unittest.TestCase):
    """The tail exit is gated on the pre-typing editor NOT already holding the tail.

    ``editorText contains bodyTail`` is positionless, and the editor is not empty
    when typing starts: it holds Mail's quoted original and, when configured, the
    signature. On a thread the user has replied to before, the quote carries that
    earlier reply's sign-off, so a ``reply_body`` ending in the same sign-off has
    its tail in the editor before a single character is typed -- and the match
    then fires on poll 1, before anything has drained, handing ``save`` an
    undrained editor. Deterministic, so the automatic retype pass reproduces it
    and the call hard-fails.

    UNVERIFIED against live Mail: static assertions on the emitted AppleScript.
    """

    def test_the_answer_is_computed_from_the_pre_typing_read_already_taken(self) -> None:
        """No second AX round trip: the length baseline already read the text."""
        loop_body = _typing_loop()
        # One AXValue read before typing, and both derived answers come off it.
        self.assertEqual(loop_body.count('attribute "AXValue"'), 1)
        self.assertIn("set tailExitUsable to not (preTypingText contains preTypingTail)", loop_body)
        self.assertIn(
            "set preTypingTail to text (bodyLength - preTypingTailLength + 1) thru bodyLength of bodyText",
            loop_body,
        )

    def test_an_unreadable_pre_typing_probe_poisons_the_tail_exit(self) -> None:
        """Unknown must not read as clean, exactly as ``preTypingLength = -1``.

        The default is false and is only raised inside the same guard the length
        baseline uses, so a probe that threw disables both exits rather than
        granting either a free pass.
        """
        loop_body = _typing_loop()
        default = loop_body.index("set tailExitUsable to false")
        guard = loop_body.index('if preTypingLengthFailure is "" and preTypingText is not missing value then')
        raised = loop_body.index("set tailExitUsable to not (preTypingText contains preTypingTail)")
        self.assertLess(default, guard)
        self.assertLess(guard, raised)

    def test_the_poll_gates_the_tail_exit_on_that_answer(self) -> None:
        poll = _poll()
        self.assertIn('if tailExitUsable and editorText contains bodyTail then return "settled_tail"', poll)


class UnobservedDrainStillWaitsTests(unittest.TestCase):
    """A drain that cannot be OBSERVED is waited out blind, not skipped.

    ``resolveReplyBodyEditor`` falls back to an ``AXWebArea`` when it finds no
    ``AXTextArea``, and ``AXValue`` is typically unsupported there, so the first
    read throws. Returning on that throw meant ZERO drain wait -- the pre-fix
    truncation behaviour, silently, on exactly the windows whose Accessibility
    tree is already degraded. The blind wait always pays the full budget where
    the observed path usually exits early; that is the deliberate trade.

    UNVERIFIED against live Mail: static assertions on the emitted AppleScript.
    """

    def test_the_budget_is_computed_before_the_unobservable_exits(self) -> None:
        poll = _poll()
        budget = poll.index("set settleBudgetSeconds to settleAttempts *")
        self.assertLess(budget, poll.index("if editorReference is missing value then"))
        self.assertLess(budget, poll.index("repeat with settleAttempt from 1 to settleAttempts"))

    def test_a_missing_editor_reference_waits_the_budget_out(self) -> None:
        poll = _poll()
        branch = poll[poll.index("if editorReference is missing value then") :]
        branch = branch[: branch.index("end if")]
        self.assertIn("delay settleBudgetSeconds", branch)
        self.assertIn('return "unobserved_no_editor"', branch)

    def test_a_first_read_that_throws_waits_the_budget_out(self) -> None:
        """Only the FIRST read. A later throw follows readings that landed, so
        the drain was observed and partly waited out already."""
        poll = _poll()
        branch = poll[poll.index("if settleAttempt is 1 then") :]
        branch = branch[: branch.index("end if")]
        self.assertIn("delay settleBudgetSeconds", branch)
        self.assertIn('return "unobserved_read_failed:" & settleReadFailure', branch)

    def test_budget_exhaustion_does_not_pay_the_budget_twice(self) -> None:
        """That path already waited; a blind top-up would double every timeout."""
        poll = _poll()
        after_loop = poll.split("end repeat", 1)[1]
        self.assertNotIn("delay", after_loop)


# ---------------------------------------------------------------------------
# Behavioural model of the emitted decision
# ---------------------------------------------------------------------------
#
# The assertions above pin the emitted TEXT, which is what keeps the AppleScript
# from being reformatted out from under its own reasoning. They cannot tell a
# `>` from a `>=`, a swapped operand, or the wrong baseline.
#
# The two functions below re-express the same decision in Python so those can be
# tested. They are a MODEL, not the code under test and not proof the AppleScript
# runs: nothing here executes AppleScript, and the whole path remains UNVERIFIED
# against live Mail. If the emitted logic changes, this model has to be changed
# with it -- deliberately, because that is the moment to ask whether the change
# was intended.


def _caller_derived_inputs(pre_typing_text: str | None, body_text: str) -> tuple[int, bool]:
    """Model of the two values ``typeReplyBodyChunks`` derives before typing.

    ``pre_typing_text`` is ``None`` when the pre-typing ``AXValue`` read threw.
    Returns ``(preTypingLength, tailExitUsable)``; an unreadable probe is the -1
    UNKNOWN and poisons the tail exit rather than granting it.
    """
    if pre_typing_text is None:
        return -1, False
    tail_chars = min(compose_constants.TYPING_SETTLE_TAIL_CHARS, len(body_text))
    body_tail = body_text[len(body_text) - tail_chars :]
    return len(pre_typing_text), body_tail not in pre_typing_text


def _settle(
    *,
    body_text: str,
    pre_typing_length: int,
    tail_exit_usable: bool,
    readings: list[str | None],
    editor_reference: bool = True,
) -> tuple[str, int | None]:
    """Model of ``waitForTypedBodyToSettle``. Returns ``(status, attempt)``.

    ``readings`` are successive ``AXValue`` values, ``None`` for a read that
    threw; the last one repeats until the budget runs out, which is what a
    settled editor looks like.
    """
    body_length = len(body_text)
    if body_length == 0:
        return "settled_empty_body", None
    tail_chars = min(compose_constants.TYPING_SETTLE_TAIL_CHARS, body_length)
    body_tail = body_text[body_length - tail_chars :]
    attempts = compose_constants.typing_settle_attempts(body_length)
    if not editor_reference:
        return "unobserved_no_editor", None
    previous_length = -1
    for attempt in range(1, attempts + 1):
        editor_text = readings[min(attempt - 1, len(readings) - 1)]
        if editor_text is None:
            return ("unobserved_read_failed" if attempt == 1 else "read_failed"), attempt
        current_length = len(editor_text)
        # The emitted script nests the -1 UNKNOWN guard around the delta test and
        # the delta around stability; flattened here only because `and` means the
        # same thing. All three conjuncts are load-bearing -- see the mutations
        # each of them is pinned by in this class.
        delta_reached = pre_typing_length >= 0 and current_length - pre_typing_length >= body_length
        if delta_reached and current_length == previous_length:
            return "settled_delta", attempt
        if tail_exit_usable and body_tail in editor_text:
            return "settled_tail", attempt
        previous_length = current_length
    return "budget_exhausted", None


_SIGNOFF = "\n\nBest regards,\nA. Sender\nExample Institute of Applied Things"
_BODY = ("Thanks for the note, here is the answer you asked for. " * 12) + _SIGNOFF
# The quote Mail puts in the editor before typing, on a thread this user has
# already replied to: their own earlier reply, sign-off and all.
_POISONED_QUOTE = "> On a date, someone wrote:\n> earlier text\n> " + _SIGNOFF
_CLEAN_QUOTE = "> On a date, someone wrote:\n> earlier text with no sign-off of ours\n"


class SettleDecisionModelTests(unittest.TestCase):
    """Behaviour of the modelled decision, including the defect it now excludes.

    A MODEL of the emitted AppleScript (see the note above it), not evidence the
    AppleScript runs. Everything here is static; the path is still UNVERIFIED
    against live Mail.
    """

    def test_the_quote_can_carry_the_bodys_own_tail(self) -> None:
        """The premise, asserted rather than assumed: this is an ordinary reply."""
        pre_length, tail_usable = _caller_derived_inputs(_POISONED_QUOTE, _BODY)
        self.assertEqual(pre_length, len(_POISONED_QUOTE))
        self.assertFalse(tail_usable)
        self.assertTrue(_caller_derived_inputs(_CLEAN_QUOTE, _BODY)[1])

    def test_a_tail_already_in_the_quote_does_not_end_an_undrained_wait(self) -> None:
        """DEFECT: this exited on poll 1 with 100 of ~700 characters typed."""
        pre_length, tail_usable = _caller_derived_inputs(_POISONED_QUOTE, _BODY)
        undrained = _POISONED_QUOTE + _BODY[:100]
        self.assertIn(_BODY[-compose_constants.TYPING_SETTLE_TAIL_CHARS :], undrained)
        status, _ = _settle(
            body_text=_BODY,
            pre_typing_length=pre_length,
            tail_exit_usable=tail_usable,
            readings=[undrained],
        )
        self.assertEqual(status, "budget_exhausted")

    def test_the_same_reading_exits_at_once_when_the_quote_is_clean(self) -> None:
        """The contrast that makes the gate the operative difference."""
        pre_length, tail_usable = _caller_derived_inputs(_CLEAN_QUOTE, _BODY)
        status, attempt = _settle(
            body_text=_BODY,
            pre_typing_length=pre_length,
            tail_exit_usable=tail_usable,
            readings=[_CLEAN_QUOTE + _BODY],
        )
        self.assertEqual((status, attempt), ("settled_tail", 1))

    def test_a_poisoned_tail_falls_back_to_delta_plus_stability(self) -> None:
        """Slower, not broken: the body still settles, one poll behind."""
        pre_length, tail_usable = _caller_derived_inputs(_POISONED_QUOTE, _BODY)
        status, attempt = _settle(
            body_text=_BODY,
            pre_typing_length=pre_length,
            tail_exit_usable=tail_usable,
            readings=[
                _POISONED_QUOTE + _BODY[:100],
                _POISONED_QUOTE + _BODY[:400],
                _POISONED_QUOTE + _BODY,
            ],
        )
        self.assertEqual((status, attempt), ("settled_delta", 4))

    def test_one_satisfied_delta_reading_is_not_enough(self) -> None:
        """Stability needs two consecutive readings; the first can never exit."""
        # Same length as the body, different tail: a substitution that leaves the
        # count alone is exactly what the delta exists to cover.
        substituted = _CLEAN_QUOTE + _BODY[:-1] + "X"
        pre_length, tail_usable = _caller_derived_inputs(_CLEAN_QUOTE, _BODY)
        status, attempt = _settle(
            body_text=_BODY,
            pre_typing_length=pre_length,
            tail_exit_usable=tail_usable,
            readings=[substituted],
        )
        self.assertEqual((status, attempt), ("settled_delta", 2))

    def test_a_still_growing_editor_keeps_the_wait_open(self) -> None:
        """Two readings that satisfy the delta but differ prove nothing landed."""
        pre_length, tail_usable = _caller_derived_inputs(_CLEAN_QUOTE, _BODY)
        status, attempt = _settle(
            body_text=_BODY,
            pre_typing_length=pre_length,
            tail_exit_usable=tail_usable,
            readings=[
                _CLEAN_QUOTE + _BODY[:-1] + "X",
                _CLEAN_QUOTE + _BODY[:-1] + "XY",
                _CLEAN_QUOTE + _BODY[:-1] + "XYZ",
            ],
        )
        # Only once the readings stop differing (the last one repeating) does it exit.
        self.assertEqual((status, attempt), ("settled_delta", 4))

    def test_the_unknown_baseline_disables_the_delta(self) -> None:
        """-1 must never read as a satisfied delta, however long the editor is."""
        status, _ = _settle(
            body_text=_BODY,
            pre_typing_length=-1,
            tail_exit_usable=False,
            readings=[_CLEAN_QUOTE + _BODY + "plenty more text than the body"],
        )
        self.assertEqual(status, "budget_exhausted")

    def test_an_unreadable_probe_grants_no_early_exit_at_all(self) -> None:
        """Both exits are derived from the read that failed, so both are off."""
        pre_length, tail_usable = _caller_derived_inputs(None, _BODY)
        self.assertEqual((pre_length, tail_usable), (-1, False))
        status, _ = _settle(
            body_text=_BODY,
            pre_typing_length=pre_length,
            tail_exit_usable=tail_usable,
            readings=[_CLEAN_QUOTE + _BODY],
        )
        self.assertEqual(status, "budget_exhausted")

    def test_a_shortening_substitution_still_exits_on_the_tail(self) -> None:
        """Three periods collapsing to one ellipsis holds the delta below
        ``bodyLength`` for the whole budget; the intact tail is what covers it."""
        # The substitution has to sit EARLY: a tail rewritten by autocorrect is
        # the case the delta covers, and the point here is the reverse pair.
        body = "First point... " + ("and a good deal more text in between. " * 4) + "Closing thought, unrewritten."
        shortened = _CLEAN_QUOTE + body.replace("...", "\u2026", 1)
        self.assertNotIn("...", body[-compose_constants.TYPING_SETTLE_TAIL_CHARS :])
        pre_length, tail_usable = _caller_derived_inputs(_CLEAN_QUOTE, body)
        self.assertTrue(tail_usable)
        self.assertLess(len(shortened) - pre_length, len(body))
        status, attempt = _settle(
            body_text=body,
            pre_typing_length=pre_length,
            tail_exit_usable=tail_usable,
            readings=[shortened],
        )
        self.assertEqual((status, attempt), ("settled_tail", 1))

    def test_a_first_read_that_throws_is_distinguishable_from_a_later_one(self) -> None:
        """The first is an unobserved drain (waited blind); a later one is not."""
        pre_length, tail_usable = _caller_derived_inputs(_CLEAN_QUOTE, _BODY)
        first, _ = _settle(
            body_text=_BODY,
            pre_typing_length=pre_length,
            tail_exit_usable=tail_usable,
            readings=[None],
        )
        later, attempt = _settle(
            body_text=_BODY,
            pre_typing_length=pre_length,
            tail_exit_usable=tail_usable,
            readings=[_CLEAN_QUOTE + _BODY[:100], None],
        )
        self.assertEqual(first, "unobserved_read_failed")
        self.assertEqual((later, attempt), ("read_failed", 2))

    def test_a_missing_editor_reference_is_its_own_status(self) -> None:
        status, _ = _settle(
            body_text=_BODY,
            pre_typing_length=0,
            tail_exit_usable=True,
            readings=[_BODY],
            editor_reference=False,
        )
        self.assertEqual(status, "unobserved_no_editor")

    def test_an_empty_body_never_polls(self) -> None:
        status, attempt = _settle(
            body_text="",
            pre_typing_length=0,
            tail_exit_usable=True,
            readings=[""],
        )
        self.assertEqual((status, attempt), ("settled_empty_body", None))


class SettleIsInsideTheTimeoutTests(unittest.TestCase):
    """The budget and the timeout are projected from the SAME helper.

    If they disagree, ``AppleScriptTimeout`` fires mid-drain and strands a
    partially typed compose window -- which a retry then types on top of. That
    is strictly worse than the truncation the drain wait exists to fix.
    """

    def test_the_projection_contains_the_drain(self) -> None:
        body = "a" * 5_000
        granted, error = reply_typing_budget._native_reply_effective_timeout(body, None)
        self.assertIsNone(error)
        assert granted is not None
        drain_seconds = compose_constants.typing_settle_attempts(len(body)) * compose_constants.TYPING_SETTLE_DELAY
        self.assertGreater(granted, drain_seconds)

    def test_a_longer_body_is_granted_more_time(self) -> None:
        short, _ = reply_typing_budget._native_reply_effective_timeout("a" * 2_400, None)
        long, _ = reply_typing_budget._native_reply_effective_timeout("a" * 20_000, None)
        assert short is not None and long is not None
        self.assertGreater(long, short)

    def test_an_explicit_caller_timeout_can_only_raise_the_budget(self) -> None:
        """A caller timeout BELOW the projection is floored at it, not honoured.

        The AppleScript computes its drain budget from ``bodyLength`` and knows
        nothing about the timeout it was granted, so a smaller explicit value
        makes the two users of ``typing_settle_attempts`` disagree by
        construction and fires ``AppleScriptTimeout`` mid-drain -- stranding a
        compose window with the body typed and unsaved. See
        ``reply_typing_budget._native_reply_effective_timeout``; the same contract is
        pinned from the caller's side in
        ``test_compose_tools.py::test_explicit_timeout_below_the_projection_is_floored_at_it``.
        """
        projected, _ = reply_typing_budget._native_reply_effective_timeout("a" * 5_000, None)
        assert projected is not None
        floored, error = reply_typing_budget._native_reply_effective_timeout("a" * 5_000, 42)
        self.assertIsNone(error)
        self.assertEqual(floored, projected)
        generous, error = reply_typing_budget._native_reply_effective_timeout("a" * 5_000, projected + 300)
        self.assertIsNone(error)
        self.assertEqual(generous, projected + 300)


if __name__ == "__main__":
    unittest.main()
