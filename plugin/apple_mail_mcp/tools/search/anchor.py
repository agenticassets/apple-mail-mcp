"""Bounded resolution of which mailbox holds a numeric Apple Mail message id.

Exists for one caller: ``get_email_thread(mailbox="All")``. That call used to
hand the literal string ``"All"`` to the anchor fetch, which resolves mailboxes
by name, so Mail raised ``Mailbox not found: All`` and the whole tool returned
a bare error for what is a documented, supported argument. ``"All"`` is
meaningful to the *scan* (it expands to every mailbox of the account) but
meaningless to a by-name fetch, and nothing bridged the two.

The probe is capped at ``SCAN_BOUNDS["THREAD_ANCHOR_MAILBOX_PROBE_CAP"]``
top-level mailboxes and uses ``every message of MB whose id is N`` -- the
numeric-id ``whose`` predicate that ``bounded_scan`` sanctions, not a
full-mailbox enumeration.
"""

from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.core import escape_applescript, normalize_message_ids
from apple_mail_mcp.tools import search

_MAILBOX_PREFIX = "ANCHOR_MAILBOX|||"


def _probe_script(safe_account: str, numeric_id: str, probe_cap: int, timeout: int) -> str:
    return f'''
    tell application "Mail"
        with timeout of {timeout} seconds
            try
                set targetAccount to account "{safe_account}"
                set probeMailboxes to every mailbox of targetAccount
                set probeCount to count of probeMailboxes
                if probeCount > {probe_cap} then
                    set probeCount to {probe_cap}
                end if
                set probeIndex to 1
                set probeFailures to ""
                repeat while probeIndex is less than or equal to probeCount
                    set probeMailbox to item probeIndex of probeMailboxes
                    try
                        set matchedMessages to (every message of probeMailbox whose id is {numeric_id})
                        if (count of matchedMessages) is greater than 0 then
                            return "{_MAILBOX_PREFIX}" & (name of probeMailbox)
                        end if
                    on error probeErr
                        set probeFailures to probeFailures & probeErr & "; "
                    end try
                    set probeIndex to probeIndex + 1
                end repeat
                if probeFailures is not "" then
                    return "Error: anchor mailbox probe failed: " & probeFailures
                end if
                return ""
            on error errMsg
                return "Error: " & errMsg
            end try
        end timeout
    end tell
    '''


def resolve_message_mailbox(account: str, message_id: str, timeout: int | None = None) -> str | None:
    """Return the name of the mailbox holding ``message_id``, or None.

    None means "not resolved" for every reason -- not found, probe error,
    unusable id. The caller's existing not-found path stays the single place
    that reports the failure, so this helper widening its coverage can never
    change which error text a user sees.
    """
    numeric_ids = normalize_message_ids([message_id])
    if not numeric_ids:
        return None
    result = search.run_applescript(
        _probe_script(
            escape_applescript(account),
            numeric_ids[0],
            int(SCAN_BOUNDS["THREAD_ANCHOR_MAILBOX_PROBE_CAP"]),
            timeout if timeout is not None else 120,
        ),
        timeout=timeout,
    )
    stripped = result.strip()
    if not stripped.startswith(_MAILBOX_PREFIX):
        return None
    name = stripped[len(_MAILBOX_PREFIX) :].strip()
    return name or None
