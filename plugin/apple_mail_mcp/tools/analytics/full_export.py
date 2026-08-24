"""``full_inbox_export`` tool: disabled, refusal-only shim.

Full-mailbox metadata walks were too heavy on large Exchange/Gmail accounts
and spiked Mail.app CPU. The tool stays registered (read-only, tool count
unchanged) but now refuses immediately with a structured error pointing
callers at bounded alternatives (``export_emails``, ``list_inbox_emails``,
``search_emails``). No AppleScript runs.
"""

import logging
from typing import Any

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.core import inject_preferences
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp

logger = logging.getLogger(__name__)


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Full Inbox Export")
@inject_preferences
async def full_inbox_export(
    account: str | None = None,
    mailbox: str = "INBOX",
    fields: list[str] | str | None = None,
    max_emails: int = 10_000,
    batch_size: int = 500,
    output_format: str = "json",
    timeout: int | None = None,
    ctx: Any | None = None,
) -> str:
    """
    Disabled: full-mailbox metadata walks are too heavy on large Exchange/Gmail
    accounts and can spike Mail.app CPU.

    This tool no longer walks the mailbox and performs no AppleScript. It
    immediately returns a structured ``UNBOUNDED_EXPORT_DISABLED`` refusal
    pointing at bounded alternatives:

    - ``export_emails(scope="entire_mailbox", mailbox=..., max_emails=50,
      offset=N)`` to page the mailbox in small batches.
    - ``list_inbox_emails(max_emails=50)`` for a quick recent listing.
    - ``search_emails(recent_days=7, sender=..., subject_keyword=...)`` for
      filtered discovery.

    Args:
        account: Unused. Retained for signature compatibility.
        mailbox: Unused. Retained for signature compatibility.
        fields: Unused. Retained for signature compatibility.
        max_emails: Unused. Retained for signature compatibility.
        batch_size: Unused. Retained for signature compatibility.
        output_format: Unused. Retained for signature compatibility.
        timeout: Unused. Retained for signature compatibility.
        ctx: Unused. Retained for signature compatibility.

    Returns:
        JSON-encoded structured error with ``code="UNBOUNDED_EXPORT_DISABLED"``
        and a ``remediation`` dict naming the bounded tools to use instead.
    """
    del account, mailbox, fields, max_emails, batch_size, output_format, timeout, ctx

    return serialize_tool_error(
        ToolError(
            code="UNBOUNDED_EXPORT_DISABLED",
            message=(
                "full_inbox_export is disabled: full-mailbox walks are too heavy "
                "on large Exchange/Gmail accounts and can spike Mail.app CPU. "
                "Export in small, bounded batches instead."
            ),
            remediation={
                "preferred": (
                    "export_emails(scope='entire_mailbox', mailbox=..., "
                    "max_emails=50 or less, offset=N): page the mailbox in "
                    "small batches"
                ),
                "list": "list_inbox_emails(max_emails=50 or less) for a quick recent listing",
                "search": ("search_emails(recent_days=7, sender=..., subject_keyword=...) for filtered discovery"),
            },
        )
    )
