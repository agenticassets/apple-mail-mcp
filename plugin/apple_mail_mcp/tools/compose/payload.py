"""Pure recipient/address/body/CDATA/rich-draft/attachment payload helpers.

No ``run_applescript`` call lives here; these prepare strings and paths that the
compose tools feed into AppleScript. The compose-specific regexes live here too
because these helpers are their only consumers.
"""

import re
from html import escape as html_escape
from pathlib import Path

from apple_mail_mcp.core import escape_applescript, validate_save_path

_THREADED_SUBJECT_RE = re.compile(r"^\s*((re|fw|fwd)\s*:\s*)+", re.IGNORECASE)
_QUOTED_THREAD_MARKERS_RE = re.compile(r"(?im)(^on .+ wrote:\s*$|^-{2,}\s*original message\s*-{2,}|^from:\s*.+$|^> .+)")
_CDATA_BLOCK_PATTERN = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)


def _split_addresses(value: str | None) -> list[str]:
    """Return trimmed recipient addresses preserving order."""
    if not value:
        return []
    return [addr.strip() for addr in value.split(",") if addr and addr.strip()]


def _build_recipient_loops(
    cc: str | None,
    bcc: str | None,
    *,
    message_var: str | None = None,
    compact: bool = False,
    indent: str = "            ",
    trailing_indent: str | None = None,
) -> tuple[str, str, list[str], list[str]]:
    """Build CC/BCC AppleScript loop fragments and parsed address lists."""
    recipients_cc = _split_addresses(cc)
    recipients_bcc = _split_addresses(bcc)
    of_msg = f" of {message_var}" if message_var else ""
    trail = trailing_indent if trailing_indent is not None else indent

    def _loop(kind: str, addresses: list[str]) -> str:
        if compact:
            script = ""
            for addr in addresses:
                safe_addr = escape_applescript(addr)
                script += (
                    f"make new {kind} recipient at end of {kind} recipients{of_msg} "
                    f'with properties {{address:"{safe_addr}"}}\n'
                )
            return script
        script = ""
        for addr in addresses:
            safe_addr = escape_applescript(addr)
            script += f'''
{indent}make new {kind} recipient at end of {kind} recipients{of_msg} with properties {{address:"{safe_addr}"}}
{trail}'''
        return script

    return (
        _loop("cc", recipients_cc),
        _loop("bcc", recipients_bcc),
        recipients_cc,
        recipients_bcc,
    )


def _safe_eml_name(subject: str | None) -> str:
    """Return a filesystem-safe filename stem for draft exports."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (subject or "rich-email-draft").strip())
    cleaned = cleaned.strip("-._") or "rich-email-draft"
    return cleaned[:80]


def _default_rich_draft_path(subject: str | None) -> Path:
    """Return default output path for generated rich draft EML files."""
    drafts_dir = Path.home() / "Library" / "Caches" / "apple-mail-mcp" / "rich-drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    return drafts_dir / (_safe_eml_name(subject) + ".eml")


def _compose_sender_script(variable: str, account_ref: str, sender_override: str | None) -> str:
    """Return AppleScript that sets the sender for a compose/reply/forward
    outgoing message variable, respecting Mail's account-level defaults.

    With `sender_override` the value is applied unconditionally. Without an
    override, Mail's global composing preference may otherwise win over the
    caller's account choice, so the sender is pinned to the account's only
    alias when the account has a single address, and left untouched for
    multi-alias accounts so the user's Mail preference stays in effect.
    """
    if sender_override:
        safe_sender = escape_applescript(sender_override)
        return f'set sender of {variable} to "{safe_sender}"'
    return (
        f"set emailAddrs to email addresses of {account_ref}\n"
        f"if (count of emailAddrs) is 1 then\n"
        f"    set sender of {variable} to item 1 of emailAddrs\n"
        f"end if"
    )


def _strip_cdata_wrappers(text: str | None) -> str | None:
    """Remove XML CDATA section markers from user-provided body content.

    LLM callers occasionally wrap email bodies in `<![CDATA[...]]>`. HTML
    parsers treat the opening `<![CDATA[` as a bogus comment that ends at
    the first `>` in the actual content, so it's invisible — but the
    trailing `]]>` has no preceding `<` and renders as literal text at the
    end of the message. Strip both forms so callers don't have to know.
    """
    if not text:
        return text
    text = _CDATA_BLOCK_PATTERN.sub(r"\1", text)
    return text.replace("<![CDATA[", "").replace("]]>", "")


def _standalone_compose_thread_warning(
    subject: str,
    body: str | None,
    body_html: str | None,
    standalone_confirmed: bool,
    tool_name: str = "compose_email",
) -> str | None:
    """Return an error when a new compose looks like an accidental reply.

    ``tool_name`` names the calling tool in the returned message so the
    warning matches the tool the agent actually invoked (``compose_email``,
    ``manage_drafts(action="create")``, or ``create_rich_email_draft``)
    instead of always saying "compose_email".
    """
    if standalone_confirmed:
        return None

    signals = []
    if _THREADED_SUBJECT_RE.search(subject or ""):
        signals.append("threaded subject prefix")

    combined_body = "\n".join(part for part in ((body or ""), (body_html or "")) if part)
    if _QUOTED_THREAD_MARKERS_RE.search(combined_body):
        signals.append("quoted-thread markers")

    if not signals:
        return None

    return (
        f"Error: {tool_name} creates a standalone new message and will not "
        "include the original email thread. This draft looks like a reply or "
        f"forward ({', '.join(signals)}). Use reply_to_email(message_id=...) "
        "or forward_email(message_id=...) after locating the source message. "
        "If you intentionally want a brand-new standalone message, pass "
        "standalone_confirmed=True."
    )


def _build_html_from_text(text_body: str | None) -> str:
    """Return a simple HTML wrapper for plain text content."""
    safe_body = html_escape(text_body or "")
    return (
        '<html><body style="font-family: -apple-system, BlinkMacSystemFont, '
        "'Segoe UI', Arial, sans-serif; line-height: 1.45; color: #111111;\">"
        '<pre style="white-space: pre-wrap; font: inherit; margin: 0;">' + safe_body + "</pre></body></html>"
    )


def _prepare_rich_bodies(
    subject: str,
    text_body: str | None,
    html_body: str | None,
) -> tuple[str, str, list[str]]:
    """Return plain-text and HTML bodies, filling sensible placeholders."""
    plain_body = text_body or ""
    rich_body = html_body or ""

    if not plain_body and not rich_body:
        plain_body = "Draft outline\n\n- Add recipients\n- Add the final rich-text content\n- Review before sending"
        rich_body = _build_html_from_text(plain_body)
        return plain_body, rich_body, ["body"]

    if rich_body and not plain_body:
        plain_body = (
            subject.strip() + "\n\n" if subject and subject.strip() else ""
        ) + "This message contains rich HTML content. Open it in Mail for the rendered version."

    if plain_body and not rich_body:
        rich_body = _build_html_from_text(plain_body)

    return plain_body, rich_body, []


def _validate_attachment_paths(attachments: str) -> tuple[list[str], str | None]:
    """Validate and resolve attachment file paths.

    Splits comma-separated paths, expands tildes, resolves symlinks,
    and enforces security constraints (home-dir-only, no sensitive dirs,
    file must exist).

    Returns:
        A tuple of (resolved_paths, error_message).
        If error_message is not None, resolved_paths should be ignored.
    """
    resolved_paths: list[str] = []
    raw_paths = [p.strip() for p in attachments.split(",")]

    for raw_path in raw_paths:
        if not raw_path:
            continue

        # Validate before expanding. `validate_save_path` expands inside its own
        # guard; doing it here first means `~typo/report.pdf` raises RuntimeError
        # out of `expanduser` and leaves this helper as an exception, breaking
        # the (paths, error) contract every other failure here honors.
        path_err = validate_save_path(
            raw_path,
            path_label="Attachment path",
            sensitive_action="attach files from",
        )
        if path_err:
            return [], path_err

        # Expand tilde and resolve symlinks
        resolved_path = Path(raw_path).expanduser().resolve()
        resolved = str(resolved_path)

        # File must exist
        if not resolved_path.is_file():
            return [], f"Error: Attachment file does not exist: {resolved}"

        resolved_paths.append(resolved)

    if not resolved_paths:
        return [], "Error: No valid attachment paths provided."

    return resolved_paths, None
