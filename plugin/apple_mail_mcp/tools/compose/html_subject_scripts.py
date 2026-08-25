"""HTML compose subject restore, leftover-marker sweep, and failure follow-up.

Keeps ``send.py`` under the module line budget. Marker lookup lives in
``standalone_draft_identity_scripts``; this module only interpolates those
fragments into HTML compose success, error, and Python follow-up paths.

The marker exists only as the pre-save window-binding subject for
``focusComposeBody``. After paste, restore ``subject of newMsg`` while it is
still a writable outgoing message, then save once. Never persist the marker
across a Gmail save: saved Drafts ``message.subject`` is read-only.
"""

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.core import AppleScriptTimeout, escape_applescript
from apple_mail_mcp.core.reply_state import drafts_mailbox_block
from apple_mail_mcp.tools import compose
from apple_mail_mcp.tools.compose.clipboard_scripts import pasteboard_restore_script
from apple_mail_mcp.tools.compose.helpers import _clean_applescript_error
from apple_mail_mcp.tools.compose.standalone_draft_identity_scripts import (
    standalone_exact_marker_restore_or_delete_script,
)

_CLOSE_NEW_MSG_WINDOW = """
                try
                    close (window of newMsg) saving no
                end try
"""

_DISCARD_NEW_MSG = f"""
{_CLOSE_NEW_MSG_WINDOW}
                try
                    delete newMsg
                end try
"""

_SWEEP_MUST_BE_CLEAR = """
                if leftoverOutgoingStatus is "failed" then error "HTML_COMPOSE_SUBJECT_RESTORE_FAILED"
                if markerSweepStatus is not "cleared" then error "HTML_COMPOSE_SUBJECT_RESTORE_FAILED"
"""


def _html_post_paste_mail_block(body: str) -> str:
    return f"""
            delay 0.5
            tell application "Mail"
                {body}
            end tell
        """


def _restore_and_verify_outgoing_subject(subject: str, marker: str) -> str:
    escaped_subject = escape_applescript(subject)
    escaped_marker = escape_applescript(marker)
    return f'''
                set subject of newMsg to "{escaped_subject}"
                set restoredOutgoingSubject to subject of newMsg as string
                if restoredOutgoingSubject is "{escaped_marker}" then error "HTML_COMPOSE_SUBJECT_RESTORE_FAILED"
                if restoredOutgoingSubject is not "{escaped_subject}" then error "HTML_COMPOSE_SUBJECT_RESTORE_FAILED"
'''


def html_compose_post_paste_script(
    *,
    mode: str,
    subject: str,
    marker: str,
    attachment_finalize_script: str,
) -> str:
    """Return the post-paste Mail block for HTML draft, open, or send.

    Every path restores the real subject on the outgoing message, verifies it,
    then saves (or sends) once. Attachment proof runs after that save and binds
    by snapshot identity or the real subject, never by a persisted marker.
    """
    restore = _restore_and_verify_outgoing_subject(subject, marker)
    sweep = standalone_exact_marker_restore_or_delete_script(marker, subject, persist_is_failure=True)
    if mode == "send":
        return _html_post_paste_mail_block(f"""
                {restore}
                {sweep}
                {_SWEEP_MUST_BE_CLEAR}
                send newMsg
""")
    close = _CLOSE_NEW_MSG_WINDOW if mode == "draft" else ""
    return _html_post_paste_mail_block(f"""
                {restore}
                save newMsg
                {attachment_finalize_script}
                {sweep}
                {_SWEEP_MUST_BE_CLEAR}
                {close}
""")


def html_compose_error_handler_script(
    *,
    marker: str,
    subject: str,
    html_temp_path: str,
    mode: str = "draft",
) -> str:
    """Discard the fixture on pre-restore failure; never stamp the real subject.

    ``COMPOSE_BODY_FOCUS_FAILED`` (and any still-exact-marker fixture) must
    ``delete newMsg`` after closing without save. Restoring the real subject
    then only closing the window lets Gmail/IMAP persist an empty draft.
    ``mode="open"`` leaves a successfully restored post-save window up.
    Unique leftover marker Drafts rows are deleted; saved Gmail subjects
    cannot be rewritten.
    """
    escaped_marker = escape_applescript(marker)
    sweep = standalone_exact_marker_restore_or_delete_script(marker, subject)
    close_restored_draft = (
        f"""
                else
                    {_CLOSE_NEW_MSG_WINDOW}
"""
        if mode == "draft"
        else ""
    )
    return f"""
    try
        tell application "Mail"
            if errMsg contains "COMPOSE_BODY_FOCUS_FAILED" then
                {_DISCARD_NEW_MSG}
            else
                try
                    set errSubject to subject of newMsg as string
                    if errSubject is "{escaped_marker}" then
                        {_DISCARD_NEW_MSG}
                    {close_restored_draft}
                    end if
                on error
                    {_DISCARD_NEW_MSG}
                end try
            end if
            {sweep}
        end tell
    end try
    try
        do shell script "rm -f " & quoted form of "{html_temp_path}"
    end try
    {pasteboard_restore_script()}
    error errMsg
"""


def html_compose_subject_followup_script(
    account: str = "Test Account",
    marker: str = "__apple_mail_mcp_compile_check__",
    final_subject: str = "Compile check subject",
) -> str:
    """Standalone leftover-marker cleanup after HTML compose throw/timeout.

    Restores leftover outgoing messages (writable), then deletes a unique
    leftover marker Drafts row. Defaults keep this builder osacompile-
    discoverable (name ends in ``_script`` and the body starts with
    ``tell application "Mail"``).
    """
    safe_account = escape_applescript(account)
    drafts_resolver = drafts_mailbox_block(var_name="draftsMailbox", account_var="targetAccount")
    sweep = standalone_exact_marker_restore_or_delete_script(marker, final_subject)
    return f'''
    tell application "Mail"
        try
            set targetAccount to account "{safe_account}"
            {drafts_resolver}
            {sweep}
            if leftoverOutgoingStatus is "failed" then return "failed"
            if leftoverOutgoingStatus is "outgoing_ok" then return "outgoing_ok"
            return markerSweepStatus
        on error errMsg
            return "failed"
        end try
    end tell
    '''


def html_subject_restore_failed(*, original_error: str, cleanup_result: str) -> str:
    """Return the structured HTML compose failure envelope. Never a success banner."""
    return serialize_tool_error(
        ToolError(
            code="HTML_COMPOSE_SUBJECT_RESTORE_FAILED",
            message=(
                f"HTML compose did not finish cleanly ({original_error}). Marker-subject cleanup: {cleanup_result}."
            ),
            remediation={
                "preferred": (
                    "Inspect Drafts and open compose windows for the requested real subject. "
                    "Do not send a leftover __apple_mail_mcp_ marker subject."
                ),
                "note": (
                    "The marker is only a pre-save window-binding token. After paste, "
                    "the outgoing message subject is restored before save. Leftover "
                    "marker outgoing windows are restored; a unique leftover marker "
                    "Drafts row is deleted because saved Gmail subjects are read-only."
                ),
            },
        )
    )


def _attachment_proof_failed(*, original_error: str, cleanup_result: str) -> str:
    return serialize_tool_error(
        ToolError(
            code="DRAFT_ATTACHMENT_PROOF_FAILED",
            message=(
                f"Attachment proof failed after restoring the visible subject ({original_error}). "
                f"Marker-subject cleanup: {cleanup_result}."
            ),
            remediation={
                "preferred": (
                    "The compose window should already show the real subject. "
                    "Review the draft in Mail; do not send until attachments verify."
                ),
            },
        )
    )


def run_html_compose_subject_followup(
    *,
    account: str,
    marker: str,
    final_subject: str,
    original_error: str,
    timeout: int | None,
    mode: str = "draft",
    to: str = "",
) -> str:
    """Clean leftover markers, then fail closed. Marker absence is not success.

    A follow-up ``cleared`` / ``deleted`` / ``outgoing_ok`` status only means
    no leftover *marker* remains. It does not prove a real-subject draft from
    this operation exists. Never convert a compose exception into a success
    banner. ``mode`` and ``to`` remain on the public call contract used by
    ``_send_html_email``.
    """
    _ = (mode, to)
    script = html_compose_subject_followup_script(
        account=account,
        marker=marker,
        final_subject=final_subject,
    )
    cleanup_result = "followup_not_run"
    try:
        cleanup_result = (
            compose.run_applescript(script, timeout=timeout if timeout is not None else 30).strip() or "empty"
        )
    except AppleScriptTimeout:
        cleanup_result = "followup_timeout"
    except Exception as exc:
        cleaned = _clean_applescript_error(exc)
        cleanup_result = f"followup_error:{cleaned}" if cleaned else "followup_error"

    if "DRAFT_ATTACHMENT_PROOF_FAILED" in original_error:
        return _attachment_proof_failed(original_error=original_error, cleanup_result=cleanup_result)

    return html_subject_restore_failed(original_error=original_error, cleanup_result=cleanup_result)
