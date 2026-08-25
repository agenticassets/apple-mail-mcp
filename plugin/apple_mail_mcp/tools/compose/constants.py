"""Compose-specific constants and caps shared across the compose helpers.

Leaf module so ``compose.py`` and its pure helper siblings import these without
forming an import cycle. Caps keep deriving from ``constants.SCAN_BOUNDS`` so a
single edit retunes every tool; tests assert the literal ``"items 1 thru 100"`` /
``"messages 1 thru 100"`` slices, so changing a cap value here requires
coordinated updates in ``tests/test_phase_2_scan_hardening.py``.
"""

from typing import Final

from apple_mail_mcp.constants import SCAN_BOUNDS

DRAFT_LIST_CAP = SCAN_BOUNDS["DRAFT_LOOKUP"]
MESSAGE_LOOKUP_CAP = SCAN_BOUNDS["MESSAGE_LOOKUP"]
_MESSAGE_ID_REQUIRED_ERROR = (
    "Error: message_id is required (discover via search_emails(...) or list_inbox_emails(...), then pass message_id)"
)
# Sentinel the native reply script returns when the source message has no
# readable content to cut a quote anchor from, so the two-part quote proof
# (attribution line + a span of the source body) could never be evaluated.
# Shared with the Python side, which turns it into a structured error under
# output_format="json"; the emitting AppleScript and the reader must agree on
# the exact token.
QUOTE_PROOF_UNAVAILABLE: Final[str] = "QUOTE_PROOF_UNAVAILABLE"

# Sentinel the native reply script returns when System Events can see Mail but
# cannot see any of Mail's windows, which is what a non-effective Accessibility
# grant looks like from inside a script. Emitted *before* the `reply` command so
# no compose window is opened and nothing has to be cleaned up. Same
# agree-on-the-token contract as QUOTE_PROOF_UNAVAILABLE above.
REPLY_ACCESSIBILITY_UNAVAILABLE: Final[str] = "REPLY_ACCESSIBILITY_UNAVAILABLE"

# How hard to poll that Accessibility zero before treating it as fatal.
#
# `frontmost of process "Mail"` turns true the instant macOS accepts the
# activation, but when Mail sits on a *different Space* -- which is what happens
# the moment any other app is full-screen, the normal state of a working Mac --
# the Space transition is still animating, and Accessibility enumerates zero
# windows for Mail throughout it. Measured on Darwin 25.5 (2026-08-25) with a
# full-screen app holding the front: frontmost=true from the first sample,
# ax=0 for the first two samples (~0.3 s), then ax=1 steadily. Mail's own
# scripting dictionary reported 8 windows the whole time.
#
# A single sample inside that gap aborts a perfectly healthy reply and blames
# the display or the Accessibility grant, neither of which is wrong. Polling
# costs nothing on the healthy path (the first sample answers) and is only paid
# out when the count is genuinely zero, which was already a hard abort.
AX_WINDOW_SETTLE_ATTEMPTS: Final[int] = 8
AX_WINDOW_SETTLE_DELAY: Final[float] = 0.35

# How long to wait for the WebKit compose editor to catch up after the LAST
# chunk before reporting the body typed.
#
# `keystroke` returns when the events are posted, not when WebKit has processed
# them, and the loop's inter-chunk delay is deliberately skipped after the final
# chunk -- so the script went straight from the last `keystroke` to `save`. Under
# load the editor runs a backlog behind the event queue, and whatever had not
# been drained at save time was simply absent from the draft.
#
# Measured on Darwin 25.5 (2026-08-25) on 2,400-character bodies: chunk size 120
# saved a clean 2,184-character prefix (last 216 chars missing) and chunk size
# 160 saved a clean 1,967-character prefix (last 433 chars missing). Both were
# pure truncation with no substitution anywhere in the retained text, and both
# lost close to the last two chunks -- the backlog, not corruption. This is also
# why larger chunk sizes looked more dangerous: the same backlog depth in chunks
# is more characters lost.
#
# The wait is a poll on the editor's own text, not a flat sleep: it ends as soon
# as the editor's own text says the body has landed, so a fast machine pays one
# AX read.
#
# With ONE exception, which is deliberate. When the drain cannot be observed at
# all -- no editor reference, or the very first `AXValue` read throws, which is
# what an `AXWebArea` fallback editor normally does -- the script waits the same
# computed budget BLIND instead of returning immediately. Returning immediately
# was a silent zero-length wait, i.e. the pre-fix truncation behaviour, on
# exactly the windows whose Accessibility tree is already degraded. The blind
# path always pays the full budget where the observed path usually exits early;
# that cost is accepted because the alternative is a truncated reply. Budget
# exhaustion does not blind-wait -- that path already waited.
# The budget SCALES WITH BODY LENGTH, and a flat one was the defect. Measured on
# Darwin 25.5 (2026-08-25): a 2,400-character body at chunk size 300 drained
# inside 6 s, while the same chunk size on a 5,000-character body did not -- it
# saved a clean 3,179-character prefix and lost the remaining 1,821, and the
# identical run passed once the budget was raised to 50 s. The tail was LATE,
# not dropped. A flat budget therefore fails as a function of how much text is
# in flight, which is why longer bodies looked like a separate defect.
#
# The same measurement retired the chunk-size theory. Chunk 600 on a
# 2,400-character body failed three times out of four against the 6 s budget and
# PASSED against 50 s -- but took 72.6 s wall clock, against 22.9 s for chunk
# 300. Larger chunks post keystroke events faster; the WebKit editor does not
# process them any faster, so the only thing a bigger chunk buys is a deeper
# backlog to wait out. The apparent speed of large chunks in every earlier sweep
# was the script saving before the editor had caught up.
TYPING_SETTLE_DELAY: Final[float] = 0.25
TYPING_SETTLE_BASE_ATTEMPTS: Final[int] = 24
# 60 attempts per 1,000 characters = 15 s of drain budget per 1,000 characters.
# The 5,000-character case needed under 50 s, and this grants it 81 s.
TYPING_SETTLE_ATTEMPTS_PER_1K_CHARS: Final[int] = 60
# 100 s. A ceiling, not a target: past this the body is long enough that a
# failure is more useful than a longer wait, and the caller's own timeout is
# projected from the same numbers so it cannot fire mid-drain.
TYPING_SETTLE_MAX_ATTEMPTS: Final[int] = 400
# WHY THE TAIL MATCH IS NOT THE ONLY EXIT. The poll used to end only on
# `editorText contains bodyTail`, and that match often does not fire even on a
# body that arrived intact -- WebKit rewrites characters in the editor (a
# trailing space becomes a non-breaking space at minimum, and smart punctuation
# substitution is live), so a literal compare against the typed tail misses. The
# poll then ran to the end of its budget and succeeded anyway, because by then
# everything had drained: a scaled sleep wearing the shape of an early-exit poll.
#
# Cost, measured 2026-08-25 across runs that all PASSED: chunk 300 on a
# 2,400-character body took 65.7 s where the same configuration took 22.9 s under
# the old 6 s budget, and 68.2 s at 5,000 characters. The signature runs -- where
# the match evidently did fire -- came in at 33.5 s (2,400) and 48.5 s (5,000).
# So the money is real and the early exit does work, unreliably.
#
# `typing_scripts.py` therefore also exits on a LENGTH DELTA THAT HAS STOPPED
# GROWING: it reads the editor's character count before the first keystroke and
# stops once the count has grown by the body's length AND is unchanged from the
# immediately preceding poll. The pre-count is required because the editor
# already holds the quoted original and, when configured, the signature, and
# neither length is derivable from the body -- only the delta is attributable to
# typing. An unreadable pre-count is carried as -1 UNKNOWN and disables the delta
# rather than satisfying it; the same failed read also disables the tail exit,
# because the text it would have to be judged against is exactly what could not
# be read.
#
# The stability half is not belt-and-braces. The signature is applied before
# typing, but that only proves the Apple Event was SENT first, not that WebKit
# had rendered the signature into AXValue before the pre-count was read. A
# signature that lands after the pre-count makes the final length pre + body +
# signature, so a bare delta reaches bodyLength while up to a signature's worth
# of BODY characters are still missing -- the same truncation, silently. Growth
# stopping is what says everything has landed; the delta alone only says enough
# characters exist.
#
# Both conditions ship. The tail match is deliberately NOT gated on stability:
# it already proves the END of the body is present, and it covers a substitution
# that SHORTENS the text below the delta ("..." to a single ellipsis).
#
# It IS gated on the tail not having been in the editor ALREADY. `contains` is
# positionless and the editor is not empty when typing starts -- it holds Mail's
# quoted original and, when configured, the signature -- so on a thread the user
# has replied to before, the quote carries that earlier reply's sign-off, and a
# reply_body ending in the same sign-off has its tail present before a single
# character is typed. The match then fires on poll 1, before anything has
# drained. `typing_scripts.py` therefore answers "was the tail already there?"
# from the SAME pre-typing read the length baseline uses, and skips the tail exit
# when the answer is yes or unknown; such a body falls back to
# delta-plus-stability, and failing that to the full budget, which still passes.
#
# Neither exit is authoritative -- the case-sensitive verification against the
# SAVED draft still decides correctness.
#
# UNVERIFIED against live Mail: the delta exit is static-tested only, including
# the late-signature race the stability requirement exists to close. The scaled
# budget above is what makes the path CORRECT and is measured; the delta is a
# speed fix layered on top of it, and with stability required it costs at most
# one extra poll interval and can otherwise only fail toward the old behaviour of
# spending the full budget.
#
# Characters of the body's tail to look for when deciding the editor has caught
# up. Short enough that one autocorrected character does not hide an otherwise
# complete body.
#
# Length is NOT what keeps the match honest, and the note that used to claim it
# was ("long enough not to match earlier text by accident") accounted only for
# earlier TYPED text. The text the tail is actually compared against includes the
# quoted original and the signature, which were in the editor before typing
# began; a sign-off block is routinely longer than any tail value worth using, so
# no length defeats it. The positional problem is solved by disabling the tail
# exit when the pre-typing editor already contained the tail, not here.
TYPING_SETTLE_TAIL_CHARS: Final[int] = 40

# Maximum number of Mail compose windows that may be open simultaneously when
# mode="open" is used. Each call in mode="open" leaves a window open; at high
# counts NSWindowServer OOMs. Agents doing bulk drafting must use mode="draft".
MAX_OPEN_COMPOSE_WINDOWS = 5

# System Events keystroke throughput bounds for the native reply typed path
# (AGENTIC-1214). A single keystroke of the whole reply_body drops its tail and
# can leak shift-state into ALL CAPS (Bug 3), so the body is typed in chunks with
# modifier state cleared between them.
#
# WHAT THIS CONSTANT ACTUALLY TRADES (re-measured 2026-08-25, Darwin 25.5):
# it is NOT a safety dial, and every earlier note here that framed it as one was
# reading a broken instrument. `keystroke` returns when events are POSTED, not
# when WebKit has processed them. Until the settle poll above existed, the script
# went from the last keystroke straight to `save`, so a larger chunk did not
# corrupt anything -- it simply left a deeper undrained backlog at save time, and
# more characters went missing. Chunk size was a proxy for backlog depth.
#
# With the drain waited out properly, the trade inverts: bigger chunks post
# faster but the editor drains no faster, so the wait grows to match and total
# wall clock gets WORSE. Measured on 2,400-character bodies, all verified against
# a nonce-carrying draft this run created:
#
#     size   chunks   result                            elapsed
#      120     20     pass x1                            28.9s
#      160     15     pass x1                            25.9s
#      200     12     pass x1                            25.3s
#      250     10     pass x1                            24.1s
#      300      8     pass x4                            22.5-23.4s   <- current
#      400      6     pass x1                            22.2s
#      500      5     pass x1                            21.3s
#      600      4     FAIL x3 (6s drain budget)          63.8-68.9s
#      600      4     pass x1 (50s drain budget)         72.6s
#
# 600 is the cliff and it is a real one, but it costs speed, not just safety:
# even when it passes it is three times slower than 300, because the whole
# advantage of posting 600 characters at once is spent waiting for the editor.
# 400 and 500 are marginally faster than 300 and both passed cleanly, but each
# has a single observation against 300's four, and the gain over 300 is ~1s on a
# ~23s operation. 300 is shipped because it is the fastest value whose evidence
# is deep enough to trust, not because 500 is known to be unsafe.
#
# Re-measure only with a body of >= 2,400 characters and a UNIQUE NONCE in the
# body. At 1,200 characters every size passed, including ones that fail at 2,400.
# The nonce is not optional: this host's Drafts mailbox holds far more messages
# than DRAFT_LIST_CAP, so the identity resolver bails and the verifier falls back
# to subject + body-contains -- and a sweep typing a byte-identical body into
# replies to the same source message will happily verify each run against the
# draft the PREVIOUS run left behind. That artifact is what produced the earlier
# "cliff between 160 and 200" claim, which is withdrawn.
TYPING_CHUNK_SIZE: Final[int] = 300
TYPING_INTER_CHUNK_DELAY: Final[float] = 0.35
# Additional per-chunk cost the inter-chunk delay alone does not capture: the
# per-chunk focus re-check (two System Events "tell" blocks, each wrapped in a
# try) plus the keystroke call itself. The timeout projection in
# reply_typing_budget.py multiplies this by chunk_count alongside
# TYPING_INTER_CHUNK_DELAY so a long body cannot project under its real typing
# time and get killed by AppleScriptTimeout mid-typing.
#
# Re-measured 2026-08-24 (Darwin 25.5) after the autocorrect-panel fix in
# typing_scripts.py, on a controlled sweep that held body length at 1,200
# characters and varied only chunk size: 15 chunks 30.6s, 10 chunks 28.1s,
# 6 chunks 26.6s, 5 chunks 25.3s. That is a marginal cost of **0.53s per chunk**
# on a ~23s fixed floor, and it already includes the added per-chunk Escape.
# So 0.35 + 0.65 = 1.0s over-projects by about 89%.
#
# Over-projecting is the safe direction -- it only makes the typing timeout
# generous -- so the value stands. It is deliberately NOT retuned down to the
# measured slope: this constant exists to stop a long body from being SIGKILLed
# mid-typing, and a projection fitted tight to one idle machine would do exactly
# that on a loaded one.
#
# The superseded figure here was 0.70s per chunk. It was fitted on a machine
# that was intermittently wedged by the correction-panel bug, which inflated
# every run it was solved through.
#
# Measure this only from runs that SUCCEEDED. A reply that fails verification
# also burns a 20-attempt fallback poll with a 1s delay per attempt, which the
# success path never runs; a 21-chunk success took 47.6s where a 20-chunk
# failure took 80.0s. Solving a line through a failed run attributes that poll
# to typing and inflates the per-chunk slope threefold.
TYPING_PER_CHUNK_OVERHEAD_SECONDS: Final[float] = 0.65


def typing_settle_attempts(body_length: int) -> int:
    """Poll attempts to allow the compose editor to drain ``body_length`` chars.

    Shared deliberately by two callers that must agree: the AppleScript builder
    in ``typing_scripts.py``, which spends the budget, and
    ``reply_typing_budget._native_reply_effective_timeout``, which must project a
    timeout large enough to contain it. If those two ever disagree,
    ``AppleScriptTimeout`` fires mid-drain and strands a partially typed compose
    window -- the exact failure the projection exists to prevent.
    """
    if body_length <= 0:
        return 0
    scaled = TYPING_SETTLE_BASE_ATTEMPTS + round(body_length * TYPING_SETTLE_ATTEMPTS_PER_1K_CHARS / 1000)
    return min(scaled, TYPING_SETTLE_MAX_ATTEMPTS)
