"""``verify_draft`` / ``verify_drafts`` read-only tools for confirming saved draft contents."""

import asyncio
import json
from typing import Any

from apple_mail_mcp.applescript_snippets import (
    recipient_addresses_block,
    sanitize_field_handler,
    text_offset_handler,
    thread_headers_block,
)
from apple_mail_mcp.bounded_scan import MAX_WHOSE_IDS, iter_id_chunks
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inject_preferences,
    normalize_message_ids,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import compose
from apple_mail_mcp.tools.compose.helpers import (
    _resolve_account,
)
from apple_mail_mcp.tools.draft_verification import (
    _build_source_resolution,
    _build_verify_draft_payload,
    _parse_expected_attachments,
    _split_csv_addresses,
)
from apple_mail_mcp.tools.search.emails import search_emails


def _resolve_source_message(
    *,
    account: str,
    in_reply_to: str,
    resolve_recent_days: float,
    timeout: int | None,
) -> dict[str, Any]:
    """Resolve a draft's In-Reply-To header to the source Inbox message.

    Reuses the existing bounded ``search_emails`` path (no new AppleScript):
    a single ``internet_message_id`` lookup capped to ``max_results=1``
    within ``recent_days=resolve_recent_days``. Because ``search_emails`` is
    itself hard-bounded to a small per-call scan, this is best-effort — a
    genuine source message outside that window is honestly reported as
    ``not_found_in_window`` rather than fabricated. Never loops or expands
    the window automatically; callers that need a wider search should retry
    with a larger ``resolve_recent_days`` explicitly.
    """
    if not in_reply_to.strip():
        return _build_source_resolution(in_reply_to, resolve_recent_days, None)

    matched_record: dict[str, Any] | None = None
    try:
        raw = asyncio.run(
            search_emails(
                account=account,
                mailbox="INBOX",
                internet_message_id=in_reply_to,
                recent_days=resolve_recent_days,
                output_format="json",
                max_results=1,
                timeout=timeout,
            )
        )
        parsed: dict[str, Any] = json.loads(raw)
        items = parsed.get("items") or []
        if items:
            matched_record = items[0]
    except Exception:  # noqa: BLE001 - resolve_source is best-effort; never fail verify_draft's primary payload
        matched_record = None

    return _build_source_resolution(in_reply_to, resolve_recent_days, matched_record)


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Verify Draft")
@inject_preferences
def verify_draft(
    account: str | None = None,
    draft_id: str = "",
    expected_to: str | None = None,
    expected_cc: str | None = None,
    expected_subject: str | None = None,
    expected_body_contains: str | None = None,
    expected_attachments: str | list[str] | None = None,
    expected_signature: bool | None = None,
    require_quoted_original: bool | None = None,
    resolve_source: bool = False,
    resolve_recent_days: float = 30.0,
    timeout: int | None = None,
) -> str:
    """
    Verify one saved Apple Mail Drafts message by exact draft id.

    This tool is bounded to a single Drafts message id. It reports recipients,
    subject, body preview, attachment names and sizes, threading headers, quoted
    original detection, and optional expectation checks for agent-safe draft
    readiness decisions.

    Args:
        expected_body_contains: Checked against the body ABOVE the quoted
            original only, when the flattened preview contains a reliable
            Apple Mail attribution (``On <date>, ... wrote:``), an Outlook
            header block, or an Outlook original-message separator. A bare
            ``"wrote:"`` in ordinary prose is not a quote boundary. A needle
            that appears only inside the quoted original below that boundary sets
            ``body_contains_expected=False`` and adds
            ``body_needle_only_in_quote=True`` to the JSON payload, so a
            reply whose new text was truncated cannot false-pass this check
            just because the quoted thread happens to contain the phrase.
            When no quote boundary is present, the full body preview (already
            newline-flattened and capped at 5000 characters) is checked, same
            as before.
        resolve_source: When True and the draft has a non-empty In-Reply-To
            header, resolve that header back to the SOURCE Inbox message's
            numeric id, subject, sender, and received date via one bounded
            ``search_emails(internet_message_id=...)`` call (no new
            AppleScript). Adds a ``source`` key to the JSON payload. Default
            False preserves the exact prior output (no ``source`` key at all).
        resolve_recent_days: Search window (days) used for ``resolve_source``
            resolution. Best-effort only: this does not loop or expand the
            window automatically, so a source older than this window is
            honestly reported as ``source.resolved=false,
            reason="not_found_in_window"`` rather than fabricated. Widen this
            value or fall back to ``manage_drafts(action="find", ...)`` when
            that happens. Ignored when ``resolve_source`` is False.
    """
    account, account_error = _resolve_account(account, timeout=timeout)
    if account_error:
        return account_error
    assert account is not None

    normalized_ids = normalize_message_ids([draft_id])
    if not normalized_ids:
        return "Error: 'draft_id' must be a numeric Mail Drafts message id"
    numeric_id = normalized_ids[0]

    expected_attachment_names = _parse_expected_attachments(expected_attachments)
    expected_to_values = _split_csv_addresses(expected_to)
    expected_cc_values = _split_csv_addresses(expected_cc)
    safe_account = escape_applescript(account)
    effective_timeout = timeout if timeout is not None else 120
    sanitize_script = sanitize_field_handler(include_attachment_row_delimiter=True)
    text_offset_script = text_offset_handler()
    to_recipients_script = recipient_addresses_block(message_var="aDraft", recipient_kind="to", output_var="toRecips")
    cc_recipients_script = recipient_addresses_block(message_var="aDraft", recipient_kind="cc", output_var="ccRecips")
    bcc_recipients_script = recipient_addresses_block(
        message_var="aDraft", recipient_kind="bcc", output_var="bccRecips"
    )
    thread_headers_script = thread_headers_block(
        message_var="aDraft",
        in_reply_to_var="inReplyTo",
        references_var="refsValue",
    )

    script = f'''
    {sanitize_script}

    {text_offset_script}

    tell application "Mail"
        with timeout of {effective_timeout} seconds
            try
                set targetAccount to account "{safe_account}"
                set draftsMailbox to mailbox "Drafts" of targetAccount
                set targetDrafts to every message of draftsMailbox whose id is {numeric_id}
                if (count of targetDrafts) is 0 then return "NOT_FOUND"
                set aDraft to item 1 of targetDrafts

                set draftSubject to my sanitize_field(subject of aDraft)
                set draftBody to ""
                try
                    set draftBody to content of aDraft as string
                end try
                set draftBodyPreview to my sanitize_field(draftBody)
                if length of draftBodyPreview > 5000 then set draftBodyPreview to text 1 thru 5000 of draftBodyPreview

                {to_recipients_script}

                {cc_recipients_script}

                {bcc_recipients_script}

                {thread_headers_script}

                set quotedOriginal to "false"
                if (my textOffset(draftBody, " wrote:")) > 0 then set quotedOriginal to "true"
                if (my textOffset(draftBody, "-----Original Message-----")) > 0 then set quotedOriginal to "true"

                set signatureDetected to "false"
                try
                    set quoteOffset to my textOffset(draftBody, " wrote:")
                    set newBodyText to draftBody
                    if quoteOffset > 1 then set newBodyText to text 1 thru (quoteOffset - 1) of draftBody
                    repeat with sig in signatures
                        set sigText to content of sig as string
                        if sigText is not "" and newBodyText contains sigText then set signatureDetected to "true"
                    end repeat
                end try

                set attachmentRows to ""
                try
                    repeat with anAttachment in mail attachments of aDraft
                        set attachmentName to my sanitize_field(name of anAttachment)
                        set attachmentSize to ""
                        try
                            set attachmentSize to file size of anAttachment as string
                        end try
                        set attachmentRows to attachmentRows & attachmentName & "::" & attachmentSize & ";;"
                    end repeat
                end try

                return "FOUND|||" & draftSubject & "|||" & toRecips & "|||" & ccRecips & "|||" & bccRecips & "|||" & draftBodyPreview & "|||" & inReplyTo & "|||" & refsValue & "|||" & quotedOriginal & "|||" & signatureDetected & "|||" & attachmentRows
            on error errMsg
                return "ERROR|||" & errMsg
            end try
        end timeout
    end tell
    '''

    try:
        raw = compose.run_applescript(script, timeout=effective_timeout).strip()
    except AppleScriptTimeout:
        return json.dumps(
            {
                "draft_id": numeric_id,
                "found": False,
                "error": f"AppleScript timed out while verifying draft_id={numeric_id} on account {account!r}",
            }
        )

    if raw == "NOT_FOUND":
        return json.dumps({"draft_id": numeric_id, "found": False, "warnings": ["draft_not_found"]})
    if raw.startswith("ERROR|||"):
        return json.dumps({"draft_id": numeric_id, "found": False, "error": raw.split("|||", 1)[1]})

    parts = raw.split("|||")
    if len(parts) < 11 or parts[0] != "FOUND":
        return json.dumps({"draft_id": numeric_id, "found": False, "error": "unexpected verifier output"})

    payload = _build_verify_draft_payload(
        numeric_id=numeric_id,
        subject=parts[1],
        to_recips=parts[2],
        cc_recips=parts[3],
        bcc_recips=parts[4],
        body_preview=parts[5],
        in_reply_to=parts[6],
        references=parts[7],
        quoted_text=parts[8],
        signature_text=parts[9],
        attachment_rows=parts[10],
        expected_to_values=expected_to_values,
        expected_cc_values=expected_cc_values,
        expected_subject=expected_subject,
        expected_body_contains=expected_body_contains,
        expected_attachment_names=expected_attachment_names,
        expected_signature=expected_signature,
        require_quoted_original=require_quoted_original,
        source=(
            _resolve_source_message(
                account=account,
                in_reply_to=parts[6],
                resolve_recent_days=resolve_recent_days,
                timeout=timeout,
            )
            if resolve_source
            else None
        ),
    )
    return json.dumps(payload)


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Verify Drafts")
@inject_preferences
def verify_drafts(
    account: str | None = None,
    draft_ids: list[str] | None = None,
    expected_to: str | None = None,
    expected_cc: str | None = None,
    expected_subject: str | None = None,
    expected_body_contains: str | None = None,
    expected_attachments: str | list[str] | None = None,
    expected_signature: bool | None = None,
    require_quoted_original: bool | None = None,
    resolve_source: bool = False,
    resolve_recent_days: float = 30.0,
    timeout: int | None = None,
) -> str:
    """
    Verify multiple saved Apple Mail Drafts messages by exact draft ids.

    This batches calls to the exact Drafts verifier without using subject or
    keyword lookup. The per-draft payload is the same JSON object returned by
    ``verify_draft``, including the optional ``source`` key when
    ``resolve_source=True`` (see ``verify_draft`` for the resolution
    semantics and the honest ``not_found_in_window`` / ``no_in_reply_to_header``
    reasons). Default ``resolve_source=False`` preserves the exact prior
    per-draft payload shape (no ``source`` key at all). Each item's
    ``expected_body_contains`` check reuses ``verify_draft``'s above-quote
    scoping, including the optional ``body_needle_only_in_quote`` field (see
    ``verify_draft`` for the exact semantics).
    """
    account, account_error = _resolve_account(account, timeout=timeout)
    if account_error:
        return account_error
    assert account is not None

    raw_ids = [str(value).strip() for value in (draft_ids or []) if str(value).strip()]
    normalized_ids = normalize_message_ids(raw_ids)
    invalid_ids = [value for value in raw_ids if not value.isdigit()]
    if not normalized_ids:
        return "Error: 'draft_ids' must contain one or more numeric Mail Drafts message ids"

    items: list[dict[str, Any]] = []
    for chunk in iter_id_chunks(normalized_ids):
        for draft_id in chunk:
            raw_result = compose.verify_draft(
                account=account,
                draft_id=draft_id,
                expected_to=expected_to,
                expected_cc=expected_cc,
                expected_subject=expected_subject,
                expected_body_contains=expected_body_contains,
                expected_attachments=expected_attachments,
                expected_signature=expected_signature,
                require_quoted_original=require_quoted_original,
                resolve_source=resolve_source,
                resolve_recent_days=resolve_recent_days,
                timeout=timeout,
            )
            try:
                payload = json.loads(raw_result)
            except json.JSONDecodeError:
                payload = {"draft_id": draft_id, "found": False, "error": raw_result}
            items.append(payload)

    missing_ids = [
        str(item.get("draft_id", ""))
        for item in items
        if item.get("found") is False and "draft_not_found" in (item.get("warnings") or [])
    ]

    return json.dumps(
        {
            "draft_ids": normalized_ids,
            "items": items,
            "returned": len(items),
            "found": sum(1 for item in items if item.get("found") is True),
            "missing_ids": missing_ids,
            "invalid_ids": invalid_ids,
            "account": account,
            "chunk_size": MAX_WHOSE_IDS,
        }
    )
