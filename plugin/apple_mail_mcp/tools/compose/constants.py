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
TYPING_CHUNK_SIZE: Final[int] = 80
TYPING_INTER_CHUNK_DELAY: Final[float] = 0.35
# Additional per-chunk cost the inter-chunk delay alone does not capture: the
# per-chunk focus re-check (two System Events "tell" blocks, each wrapped in a
# try, roughly 0.3-0.5s together) plus the keystroke call itself. The timeout
# projection in reply_runner.py multiplies this by chunk_count alongside
# TYPING_INTER_CHUNK_DELAY so a long body cannot project under its real typing
# time and get killed by AppleScriptTimeout mid-typing.
TYPING_PER_CHUNK_OVERHEAD_SECONDS: Final[float] = 0.65
