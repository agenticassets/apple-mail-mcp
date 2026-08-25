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

# Maximum number of Mail compose windows that may be open simultaneously when
# mode="open" is used. Each call in mode="open" leaves a window open; at high
# counts NSWindowServer OOMs. Agents doing bulk drafting must use mode="draft".
MAX_OPEN_COMPOSE_WINDOWS = 5

# System Events keystroke throughput bounds for the native reply typed path
# (AGENTIC-1214). A single keystroke of the whole reply_body drops its tail
# near 320-480 chars (Bug 1) and can leak shift-state into ALL CAPS (Bug 3).
# Typing in small chunks with a settle delay keeps up with Mail's WebKit
# compose editor, and clearing modifier state between chunks resets any
# leaked shift state. TYPING_CHUNK_SIZE is well below the observed truncation
# floor; both values are empirically tunable against Mail on the host (the
# live-verification agent may retune them). Typed ``Final`` constants (not a
# mixed-type dict) so mypy --strict keeps ``chunk_size`` an int in the
# generated AppleScript.
# ---------------------------------------------------------------------------
# IF NATIVE REPLIES START FAILING `REPLY_BODY_MISMATCH`: SET THIS TO 120.
#
# 120 is the pre-tested fallback, verified 4 of 4 at ~33.9s. It is one step
# further from the corruption cliff than the current value and costs ~2.4s.
# Nothing else needs to change with it -- no other constant is coupled to this
# one for correctness.
# ---------------------------------------------------------------------------
#
# Raised 80 -> 160 on 2026-08-25 against a measured sweep, not a guess. Holding
# body length at 2,400 characters and varying only this value:
#
#     size   chunks   runs   result                        elapsed
#      80      30       2    verified 2/2                  39.0s
#     120      20       4    verified 4/4                  33.4-34.3s
#     160      15       3    verified 3/3                  31.5s  <- current
#     200      12       2    REPLY_BODY_MISMATCH 0/2       69.6-71.3s
#     250      10       2    REPLY_BODY_MISMATCH 0/2       68.6-70.7s
#
# That is a **cliff between 160 and 200**, not a gradient, and the failures were
# SLOWER because a mismatch burns the retype path. So 200+ is worse on both speed
# and safety; it is not a trade.
#
# 160 was chosen deliberately over the more conservative 120: it was the fastest
# verified size and the most consistent measured (31.5s on all three runs). The
# accepted risk is that it sits directly adjacent to the first failing size, so it
# has no *tested* margin -- the cliff could move down on a loaded machine. That
# risk is bounded because the failure is LOUD (`REPLY_BODY_MISMATCH`, caught by
# the full-body verifier), never a quietly wrong email sent to a recipient. The
# cost of being wrong here is one ~70s failed run and a retry at 120, which is
# why the fallback is called out at the top rather than buried.
#
# Re-measure only with a body of >= 2,400 characters. At 1,200 characters EVERY
# size passed, including 250 -- a short body does not discriminate, and a sweep
# run on a two-paragraph reply will wrongly clear 250. Also confirm Mail answers
# in under a second before each run; a run started against a wedged Mail measures
# the wedge, not the chunk size.
#
# The mechanism ties this to typing_scripts.py: the per-chunk Escape *rejects* an
# autocorrect suggestion, so a larger chunk types more text between rejections and
# lets a substitution land first. This is not a throughput dial. It is how often
# the body gets protected.
TYPING_CHUNK_SIZE: Final[int] = 160
TYPING_INTER_CHUNK_DELAY: Final[float] = 0.35
# Additional per-chunk cost the inter-chunk delay alone does not capture: the
# per-chunk focus re-check (two System Events "tell" blocks, each wrapped in a
# try) plus the keystroke call itself. The timeout projection in
# reply_runner.py multiplies this by chunk_count alongside
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
