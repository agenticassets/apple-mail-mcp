"""``create_rich_email_draft`` tool: build a standalone rich (HTML) draft via the Mail object model."""

from email.message import EmailMessage
from mimetypes import guess_type
from pathlib import Path

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.core import AppleScriptTimeout, inject_preferences, validate_save_path
from apple_mail_mcp.server import WRITE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import compose
from apple_mail_mcp.tools.compose.helpers import (
    _account_default_alias_if_single,
    _resolve_account,
    _validate_from_address,
)
from apple_mail_mcp.tools.compose.payload import (
    _default_rich_draft_path,
    _prepare_rich_bodies,
    _split_addresses,
    _standalone_compose_thread_warning,
    _strip_cdata_wrappers,
)


@mcp.tool(annotations=WRITE_TOOL_ANNOTATIONS, title="Create Rich Draft")
@inject_preferences
def create_rich_email_draft(
    account: str | None = None,
    subject: str = "",
    to: str | None = None,
    text_body: str | None = None,
    html_body: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    attachments: str | None = None,
    output_path: str | None = None,
    open_in_mail: bool = True,
    save_as_draft: bool = True,
    review_in_mail: bool = False,
    from_address: str | None = None,
    timeout: int | None = None,
    standalone_confirmed: bool = False,
) -> str:
    """
    Create a rich-text EML export and, when requested, a supported HTML Mail draft.

    This is the preferred path for HTML or richly formatted emails because Mail reliably renders `.eml`
    content, while setting raw HTML through AppleScript often stores the literal markup instead.

    Bare ``https://`` URLs on their own line in HTML compose may become Mail
    link-preview cards in the open window; this tool does not create or verify
    those cards.

    Args:
        account: Account name to use for the sender identity (e.g., "Work", "Oracle"). Defaults to `DEFAULT_MAIL_ACCOUNT` env var if `account` is omitted.
        subject: Subject line for the draft (optional; defaults to empty)
        to: Optional recipient email address(es), comma-separated for multiple
        text_body: Optional plain-text body. If omitted but html_body is provided, a fallback plain body is generated.
        html_body: Optional HTML body. If omitted but text_body is provided, a basic HTML wrapper is generated.
        cc: Optional CC recipients, comma-separated for multiple
        bcc: Optional BCC recipients, comma-separated for multiple
        attachments: Optional file paths to attach, comma-separated for multiple
        output_path: Optional path for the generated `.eml` file
        open_in_mail: If True and the subject is nonblank, create the Mail draft through the focused HTML compose transaction, never by importing the `.eml`. Default: True. Blank-subject drafts are written as `.eml` only by default to avoid opening incomplete drafts. Pass False to only create the `.eml` file.
        save_as_draft: Retained for compatibility; the supported Mail transaction saves a draft before returning.
        review_in_mail: If True, use the supported saved-open HTML compose mode for review. Otherwise save the draft quietly.
        from_address: Optional sender address to stamp into the `.eml` `From:` header. Must be one of the account's configured email addresses. When omitted, Mail fills the account's default "Send new messages from" address on open.
        timeout: Optional per-AppleScript timeout in seconds for sender alias lookup. Defaults to the standard 120s.
        standalone_confirmed: Required explicit override when the subject/body looks like a reply or forward but the caller intentionally wants a new standalone draft.

    Returns:
        Prepared EML details, plus the supported Mail compose result when requested
    """
    account, account_error = _resolve_account(account, timeout=timeout)
    if account_error:
        return account_error
    assert account is not None  # _resolve_account guarantees non-None when error is None
    if not account.strip():
        return "Error: 'account' is required"

    text_body = _strip_cdata_wrappers(text_body)
    html_body = _strip_cdata_wrappers(html_body)

    thread_warning = _standalone_compose_thread_warning(subject, text_body, html_body, standalone_confirmed)
    if thread_warning:
        return thread_warning

    try:
        sender_override, sender_error = _validate_from_address(account, from_address, timeout=timeout)
        if sender_error:
            return sender_error

        sender_address = sender_override or _account_default_alias_if_single(account, timeout=timeout)
    except AppleScriptTimeout:
        return (
            "Error: AppleScript timed out while resolving sender for account "
            f"{account!r}. Try again or pass a larger `timeout`."
        )

    recipients_to = _split_addresses(to)
    recipients_cc = _split_addresses(cc)
    recipients_bcc = _split_addresses(bcc)
    plain_body, rich_body, body_missing = _prepare_rich_bodies(subject, text_body, html_body)

    validated_attachments: list[str] = []
    if attachments:
        validated_attachments, attachment_error = compose._validate_attachment_paths(attachments)
        if attachment_error:
            return attachment_error

    missing_details = []
    if not subject or not subject.strip():
        missing_details.append("subject")
    if not recipients_to:
        missing_details.append("to")
    missing_details.extend(body_missing)

    message = EmailMessage()
    if subject:
        message["Subject"] = subject
    if sender_address:
        message["From"] = sender_address
    if recipients_to:
        message["To"] = ", ".join(recipients_to)
    if recipients_cc:
        message["Cc"] = ", ".join(recipients_cc)
    if recipients_bcc:
        message["Bcc"] = ", ".join(recipients_bcc)
    message["X-Unsent"] = "1"
    message.set_content(plain_body)
    message.add_alternative(rich_body, subtype="html")
    for attachment_path in validated_attachments:
        mime_type, _ = guess_type(attachment_path)
        maintype, subtype = (mime_type or "application/octet-stream").split("/", 1)
        message.add_attachment(
            Path(attachment_path).read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=Path(attachment_path).name,
        )

    if output_path:
        # One shared guard for every caller-supplied write target: home
        # containment first, then the sensitive-directory denylist. This used to
        # be an inlined SENSITIVE_DIRS-only loop, which silently accepted any
        # absolute path outside the home directory (/etc, /Library/LaunchDaemons,
        # a pre-existing file anywhere on the volume) because every
        # SENSITIVE_DIRS entry is home-relative and cannot match such a path.
        path_error = validate_save_path(
            output_path,
            path_label="output_path",
            sensitive_action="write a draft .eml to",
        )
        if path_error:
            return path_error
        draft_path = Path(output_path).expanduser()
    else:
        draft_path = _default_rich_draft_path(subject)

    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_bytes(bytes(message))

    can_open_in_mail = bool(subject and subject.strip())
    mail_open_skipped = open_in_mail and not can_open_in_mail
    if open_in_mail and can_open_in_mail:
        mode = "open" if review_in_mail else "draft"
        compose_result = compose.compose_email(
            account=account,
            to=to or "",
            subject=subject,
            body=plain_body,
            cc=cc,
            bcc=bcc,
            attachments=attachments,
            mode=mode,
            body_html=rich_body,
            from_address=from_address,
            timeout=timeout,
            standalone_confirmed=True,
        )
        expected_success = "Email opened in Mail for review (HTML)" if mode == "open" else "Email saved as draft (HTML)"
        if expected_success not in compose_result:
            attachment_names = [Path(path).name for path in validated_attachments]
            failure_kind = "editor focus or save" if "FOCUS_FAILED" in compose_result else "draft verification"
            return serialize_tool_error(
                ToolError(
                    code="RICH_DRAFT_COMPOSE_FAILED",
                    message=(
                        "The rich EML was preserved, but the supported HTML Mail compose transaction did not "
                        f"complete ({failure_kind}). It is not ready to send. No email was sent."
                    ),
                    remediation={
                        "account": account,
                        "eml_path": str(draft_path),
                        "mode": mode,
                        "expected_attachments": attachment_names,
                        "compose_result": compose_result,
                        "next_step": (
                            "Keep Mail visible and unobstructed, then retry. Do not send or trust an unverified "
                            "draft; inspect a returned exact Drafts ID first."
                        ),
                    },
                )
            )
        return "\n".join(
            [
                "RICH EMAIL DRAFT",
                "",
                "Rich EML export prepared.",
                "EML path: " + str(draft_path),
                "Mail compose: delegated to compose_email",
                "",
                compose_result,
            ]
        )

    output_lines = ["RICH EMAIL DRAFT", "", "✓ Rich draft prepared successfully!", ""]
    output_lines.append("Account: " + account)
    output_lines.append("Subject: " + (subject if subject else "[empty]"))
    output_lines.append("EML path: " + str(draft_path))
    output_lines.append("Opened in Mail: no")
    output_lines.append("Mail verification: not performed (EML only)")
    if sender_address:
        output_lines.append("From: " + sender_address)
    if recipients_to:
        output_lines.append("To: " + ", ".join(recipients_to))
    if recipients_cc:
        output_lines.append("CC: " + ", ".join(recipients_cc))
    if recipients_bcc:
        output_lines.append("BCC: " + ", ".join(recipients_bcc))
    output_lines.append("Missing details: " + (", ".join(missing_details) if missing_details else "none"))
    output_lines.append(
        "Note: Prefer this `.eml` workflow for HTML email drafts; Mail renders it more reliably than raw HTML injected via AppleScript content."
    )
    if mail_open_skipped:
        output_lines.append(
            "Note: Blank-subject rich drafts are written as `.eml` only by default to avoid opening incomplete drafts."
        )
    return "\n".join(output_lines)
