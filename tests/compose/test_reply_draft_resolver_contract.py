"""The native-reply Drafts resolver's return shapes, held to one convention each.

Two footguns this locks, both of the "AppleScript is untyped and the enclosing
``try`` eats the evidence" family:

1. ``fullDraftRfcSnapshot`` returns a fixed-arity list on every branch that is
   not ``missing value``. It used to return a 2-item ``{0, {}}`` for an empty
   Drafts mailbox and a 3-item list otherwise. Nothing read item 3 yet, so
   nothing broke -- but the first ``item 3 of`` added would have thrown only on
   the empty-mailbox path, and the caller's surrounding ``try`` converts that
   throw into "no identity resolved", which is a wrong answer that looks like a
   clean one.

2. ``persistedReplyDraftIdentity`` signals "nothing proved" with ``missing
   value``, never ``""``. Inside that same handler ``""`` is a legal *value*
   for a draft id (an unreadable ``id of aDraft`` reads as ""), so using it as
   the failure sentinel makes "found nothing" and "found a row I could not
   identify" the same signal at the call site.

The sibling handler of the same name in ``standalone_draft_identity_scripts``
already used the 3-item form; the divergence test below pins that they agree on
arity while their item *order* deliberately differs (documented in the reply
module, since splicing both into one script would silently shadow one).
"""

from __future__ import annotations

import re

from apple_mail_mcp.tools.compose.reply_draft_resolver_scripts import (
    _native_reply_draft_resolver_handlers_applescript,
    _native_reply_draft_resolver_script,
)
from apple_mail_mcp.tools.compose.standalone_draft_identity_scripts import (
    _standalone_draft_identity_handlers,
)

_RETURN_LIST = re.compile(r"return \{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}")


def _handler_body(script: str, name: str) -> str:
    start = script.index(f"on {name}(")
    return script[start : script.index(f"end {name}", start)]


def _returned_list_arities(body: str) -> list[int]:
    """Return the item count of each ``return {...}`` literal in *body*."""
    arities = []
    for match in _RETURN_LIST.finditer(body):
        inner = match.group(1)
        # Mask nested braces so a nested list counts as one item.
        flat = re.sub(r"\{[^{}]*\}", "X", inner)
        arities.append(len([part for part in flat.split(",") if part.strip()]))
    return arities


def test_reply_snapshot_returns_the_same_arity_on_every_branch() -> None:
    body = _handler_body(_native_reply_draft_resolver_handlers_applescript(), "fullDraftRfcSnapshot")
    arities = set(_returned_list_arities(body))
    assert arities == {3}, f"fullDraftRfcSnapshot returns lists of differing arity: {sorted(arities)}"


def test_standalone_snapshot_agrees_on_arity() -> None:
    """The same-named sibling must not drift apart from the reply one."""
    body = _handler_body(_standalone_draft_identity_handlers(), "fullDraftRfcSnapshot")
    assert set(_returned_list_arities(body)) == {3}


def test_identity_handler_uses_missing_value_not_empty_string() -> None:
    body = _handler_body(
        _native_reply_draft_resolver_handlers_applescript(), "persistedReplyDraftIdentity"
    )
    assert 'return ""' not in body, (
        'persistedReplyDraftIdentity signals failure with "", which is also a legal draft-id '
        "value inside this handler; return missing value instead"
    )
    assert "return missing value" in body


def test_the_caller_tests_for_that_same_sentinel() -> None:
    """A sentinel change that misses the call site is worse than no change."""
    resolver = _native_reply_draft_resolver_script()
    assert "if replyDraftIdentity is not missing value then" in resolver
    assert 'if replyDraftIdentity is not "" then' not in resolver


def test_the_success_capsule_is_still_four_items() -> None:
    """The four capsule fields the resolver unpacks by index must all be emitted."""
    body = _handler_body(
        _native_reply_draft_resolver_handlers_applescript(), "persistedReplyDraftIdentity"
    )
    assert set(_returned_list_arities(body)) == {4}
