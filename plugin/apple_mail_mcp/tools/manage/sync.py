"""``synchronize_account`` tool: per-account / all-account IMAP synchronize with scaled timeouts.

Patched names (``run_applescript``, ``list_mail_account_names``, ``validate_account_name``)
are referenced via the ``manage`` facade so existing ``patch('...tools.manage.<name>')``
seams keep working."""

from apple_mail_mcp import server as _server
from apple_mail_mcp.core import AppleScriptTimeout, escape_applescript, inject_preferences
from apple_mail_mcp.server import IDEMPOTENT_WRITE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import manage


@mcp.tool(annotations=IDEMPOTENT_WRITE_TOOL_ANNOTATIONS, title="Synchronize Account")
@inject_preferences
def synchronize_account(
    account: str | None = None,
    confirm_sync: bool = False,
    all_accounts: bool = False,
) -> str:
    """
    Force Mail.app to synchronize an account (or every account) with its
    IMAP / Exchange server right now. Equivalent to clicking the
    refresh button next to the account or selecting Mailbox → Synchronize.

    Use after `move_email`, `update_email_status`, or `manage_trash`
    when downstream clients (iPhone, web mail, etc.) need to see the
    change immediately. Mail.app's natural sync cadence is "automatic"
    which can be several minutes — this collapses that to one IMAP push.

    Implementation note:
    --------------------
    Uses the `synchronize with <account>` AppleScript verb (per Mail.sdef:
    "Command to trigger synchronizing of an IMAP account with the server")
    rather than `check for new mail`. The latter is receive-only — it
    pulls new messages but does NOT push pending IMAP commands like
    queued moves / archives / flag changes. With `check for new mail`,
    archives done via `move_email` could sit in Mail.app's local cache
    for several minutes before reaching the IMAP server, leaving iPhone
    Mail (which reads IMAP directly) showing already-archived messages
    still in INBOX. `synchronize with` is the bidirectional verb that
    drains pending IMAP commands AND fetches new mail.

    Mail.app's synchronize is potentially long-running. We wrap each
    invocation in `with timeout of N seconds` so the AppleScript returns
    promptly. When the timeout fires (error -1712) Mail.app keeps the
    sync running in the background — exactly the fire-and-forget
    semantics callers expect.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to
                 DEFAULT_MAIL_ACCOUNT when configured.
        confirm_sync: Required explicit opt-in. Synchronizing can make Mail.app
                 download a large backlog of messages, so agents and test
                 batteries must not trigger it implicitly.
        all_accounts: Required in addition to confirm_sync=True to sync every
                 configured account.

    Returns:
        Confirmation string with the account(s) synced or queued.
    """
    # 8 s is comfortably longer than a healthy IMAP sync but short enough
    # that a stuck account / network blip doesn't block the MCP call. On
    # timeout we report "queued" — the sync continues asynchronously.
    PER_ACCOUNT_TIMEOUT_S = 8

    if account is None and _server.DEFAULT_MAIL_ACCOUNT and not all_accounts:
        account = _server.DEFAULT_MAIL_ACCOUNT

    if account is None or not account.strip():
        if not all_accounts:
            return (
                "Error: account is required (and no DEFAULT_MAIL_ACCOUNT configured). "
                "Set all_accounts=True with confirm_sync=True to sync every account."
            )
        if not confirm_sync:
            return (
                "Error: synchronize_account all-account sync requires confirm_sync=True. "
                "Synchronizing can trigger Mail.app to download a large message backlog; "
                "do not call it from routine tests."
            )
        # Probe account count to compute a scaled outer timeout.
        # Without scaling, a 4-account setup needs ~32s but the outer
        # wrapper fires at 13s — SIGKILL mid-sync causes partial IMAP commits.
        try:
            account_names = manage.list_mail_account_names(timeout=PER_ACCOUNT_TIMEOUT_S)
        except AppleScriptTimeout:
            account_names = []
        account_count = max(len(account_names), 1)
        outer_timeout = PER_ACCOUNT_TIMEOUT_S * account_count + 5

        script = f"""
        tell application "Mail"
            set acctNames to {{}}
            set queuedNames to {{}}
            repeat with a in accounts
                set acctName to name of a
                set end of acctNames to acctName
                try
                    with timeout of {PER_ACCOUNT_TIMEOUT_S} seconds
                        synchronize with a
                    end timeout
                on error errMsg number errNum
                    if errNum is -1712 then
                        set end of queuedNames to acctName
                    end if
                end try
            end repeat
            set AppleScript's text item delimiters to ", "
            if (count of queuedNames) > 0 then
                return "Synchronized all accounts: " & (acctNames as string) & " (queued: " & (queuedNames as string) & ")"
            else
                return "Synchronized all accounts: " & (acctNames as string)
            end if
        end tell
        """
        return manage.run_applescript(script, timeout=outer_timeout)

    account = account.strip()
    account_err = manage.validate_account_name(account, timeout=PER_ACCOUNT_TIMEOUT_S)
    if account_err:
        return account_err
    if not confirm_sync:
        return (
            f"Error: synchronize_account for '{account}' requires confirm_sync=True. "
            "Synchronizing can trigger Mail.app to download a large message backlog."
        )

    acct_escaped = escape_applescript(account)
    script = f'''
    tell application "Mail"
        try
            set targetAccount to first account whose name is "{acct_escaped}"
            try
                with timeout of {PER_ACCOUNT_TIMEOUT_S} seconds
                    synchronize with targetAccount
                end timeout
                return "Synchronized: {acct_escaped}"
            on error errMsg number errNum
                if errNum is -1712 then
                    return "Synchronizing: {acct_escaped} (queued — push in progress)"
                end if
                return "Error: " & errMsg
            end try
        on error errMsg
            return "Error: " & errMsg
        end try
    end tell
    '''
    return manage.run_applescript(script, timeout=PER_ACCOUNT_TIMEOUT_S + 5)
