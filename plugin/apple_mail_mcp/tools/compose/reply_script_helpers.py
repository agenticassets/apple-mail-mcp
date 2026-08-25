"""Small pure fragments the reply builders are assembled from.

Split out of ``reply_scripts.py`` when that module crossed the 600 LOC budget.
The two builders there are single large f-string scripts; these are the short
mode/option/signature decisions made *before* one is built, and ``reply.py``
calls four of the five directly rather than through a builder. Keeping them
apart leaves each file with one job.

Pure string and dataclass work only -- no ``run_applescript``, so nothing here
is a patch seam.
"""

from dataclasses import dataclass

from apple_mail_mcp.tools.compose.lookup_scripts import _compose_signature_script


@dataclass(frozen=True)
class _ReplyModePlan:
    header_text: str
    post_action: str
    success_text: str


def _reply_mode_plan(effective_mode: str) -> _ReplyModePlan:
    """Return mode-specific output and Mail action script for replies."""
    if effective_mode == "send":
        return _ReplyModePlan("SENDING REPLY", "send replyMessage", "Reply sent successfully!")
    if effective_mode == "open":
        return _ReplyModePlan(
            "OPENING REPLY FOR REVIEW",
            """
        save replyMessage
        delay 0.8
        activate
        """,
            "Reply opened in Mail for review. Edit and send when ready.",
        )
    return _ReplyModePlan(
        "SAVING REPLY AS DRAFT",
        """
        save replyMessage
        delay 1.0
        """,
        "Reply saved as draft!",
    )


def _reply_command_options(effective_mode: str, reply_to_all: bool) -> tuple[str, str]:
    """Return Mail `reply` command options and any required settle delay."""
    if effective_mode == "open":
        reply_options = "with opening window"
        if reply_to_all:
            reply_options += " and reply to all"
        return reply_options, "delay 0.6"
    if reply_to_all:
        return "with reply to all", ""
    return "", ""


def _reply_signature_script(
    resolved_signature_name: str | None,
    *,
    include_signature: bool,
) -> str:
    """Return reply-specific signature AppleScript."""
    if resolved_signature_name:
        return _compose_signature_script("replyMessage", resolved_signature_name)
    if not include_signature:
        return "set message signature of replyMessage to missing value"
    return ""


def _reply_extra_output_lines(
    *,
    safe_cc: str,
    safe_bcc: str,
    safe_attachment_info: str,
    has_cc: bool,
    has_bcc: bool,
    has_attachments: bool,
) -> str:
    """Build optional status lines appended to native reply output."""
    lines: list[str] = []
    if has_cc:
        lines.append(f'set outputText to outputText & "CC: {safe_cc}" & return')
    if has_bcc:
        lines.append(f'set outputText to outputText & "BCC: {safe_bcc}" & return')
    if has_attachments:
        lines.append(f'set outputText to outputText & "Attachments:" & return & "{safe_attachment_info}" & return')
    return "\n        ".join(lines)
