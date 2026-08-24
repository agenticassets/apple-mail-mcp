"""Account enumeration helpers and the two account-listing tools.

``run_applescript`` is routed through the ``inbox`` facade so the
``patch('...tools.inbox.run_applescript')`` seam keeps covering these probes."""

from apple_mail_mcp.core import inject_preferences
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import inbox


def _list_accounts_script() -> str:
    """Tiny AppleScript that returns one Mail account name per line."""
    return """
    tell application "Mail"
        set acctNames to {}
        repeat with anAccount in (every account)
            set end of acctNames to (name of anAccount)
        end repeat
        set AppleScript's text item delimiters to linefeed
        return acctNames as string
    end tell
    """


def _list_mail_accounts(timeout: int | None = 30) -> list[str]:
    """Return the list of Mail account names (cheap; under 1s)."""
    raw = inbox.run_applescript(_list_accounts_script(), timeout=timeout)
    return [line.strip() for line in raw.splitlines() if line.strip()]


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Mail Accounts")
@inject_preferences
def list_accounts(timeout: int | None = 30) -> list[str]:
    """
    List all available Mail accounts.

    Args:
        timeout: Optional AppleScript timeout in seconds (default: 30s).

    Returns:
        List of account names
    """

    script = """
    tell application "Mail"
        set accountNames to {}
        set allAccounts to every account

        repeat with anAccount in allAccounts
            set accountName to name of anAccount
            set end of accountNames to accountName
        end repeat

        set AppleScript's text item delimiters to "|"
        return accountNames as string
    end tell
    """

    result = inbox.run_applescript(script, timeout=timeout)
    return result.split("|") if result else []


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Account Email Addresses")
@inject_preferences
def list_account_addresses(timeout: int | None = 30) -> dict[str, list[str]]:
    """
    List all configured email addresses for each Mail account.

    Useful for mapping a Mail.app account name (e.g. "Gmail", "Work") to the
    actual email address(es) it receives mail at — handy when an integration
    needs to know which inbox a message landed in by address rather than by
    Mail.app's display name.

    Args:
        timeout: Optional AppleScript timeout in seconds (default: 30s).

    Returns:
        Dict mapping account name -> list of email addresses configured on
        that account. Accounts with no addresses configured map to [].
    """

    script = """
    tell application "Mail"
        set outLines to {}
        set allAccounts to every account

        repeat with anAccount in allAccounts
            set acctName to name of anAccount
            try
                set emailAddrs to email addresses of anAccount
            on error
                set emailAddrs to {}
            end try
            if emailAddrs is missing value then
                set emailAddrs to {}
            end if
            set AppleScript's text item delimiters to ","
            set addrStr to emailAddrs as string
            set AppleScript's text item delimiters to ""
            set end of outLines to acctName & "|" & addrStr
        end repeat

        set AppleScript's text item delimiters to linefeed
        set joined to outLines as string
        set AppleScript's text item delimiters to ""
        return joined
    end tell
    """

    result = inbox.run_applescript(script, timeout=timeout)
    out: dict[str, list[str]] = {}
    if not result:
        return out
    for line in result.splitlines():
        if "|" not in line:
            continue
        name, addrs = line.split("|", 1)
        out[name] = [a.strip() for a in addrs.split(",") if a.strip()]
    return out
