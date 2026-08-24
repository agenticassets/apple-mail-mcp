"""``list_inbox_emails`` tool plus its async per-account dispatch.

``run_applescript``, ``validate_account_name``, ``_list_mail_accounts``, and the
``_list_inbox_emails_text``/``_list_inbox_emails_json`` entry points are routed
through the ``inbox`` facade so the existing test patch seams keep firing."""

import asyncio
import json
from typing import Any, cast

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    account_not_found_json,
    inject_preferences,
)
from apple_mail_mcp.core.reply_state import DraftsSnapshot, fetch_drafts_snapshot
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import inbox
from apple_mail_mcp.tools.inbox.list_scripts import (
    _build_list_inbox_json_script,
    _build_list_inbox_text_script,
)
from apple_mail_mcp.tools.inbox.parsing import (
    _annotate_text_rows_with_reply_state,
    _parse_inbox_error_lines,
    _parse_pipe_delimited_emails,
    _resolve_read_filter,
    _strip_count_marker,
)
from apple_mail_mcp.tools.reply_state_wiring import (
    MAX_DRAFT_SNAPSHOT_ACCOUNTS,
    annotate_rows_with_reply_state,
    build_draft_scan_status,
)


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Inbox Emails")
@inject_preferences
async def list_inbox_emails(
    account: str | None = None,
    all_accounts: bool = False,
    max_emails: int = 50,
    read_status: str | None = None,
    include_read: bool = True,
    include_content: bool = False,
    output_format: str = "text",
    exclude_replied: bool = False,
    flag_replied: bool = False,
    exclude_drafted: bool = False,
    include_draft_state: bool = True,
    timeout: int | None = None,
    limit: int | None = None,
    unread_only: bool | None = None,
) -> str | dict[str, Any]:
    """Defaults to 50 most-recent emails from the default account.

    List all emails from inbox across all accounts or a specific account.

    Full-mailbox scans are disabled; page through with ``max_emails`` and
    ``offset``/repeated bounded calls instead of trying to fetch everything
    at once.

    Smart defaults:
        - When `account` is None and `all_accounts` is False, the tool falls
          back to the ``DEFAULT_MAIL_ACCOUNT`` env-configured account if one
          is set. Pass `all_accounts=True` to opt back into multi-account
          dispatch even when a default is configured.
        - `max_emails` defaults to 50. `max_emails=0` is rejected with
          ``UNBOUNDED_SCAN_REQUIRED``; full-mailbox scans are disabled, so
          bound the call with `max_emails` instead.

    Performance guidance:
        - On multi-account setups with a 10K+ Exchange/Gmail inbox, prefer
          passing an explicit `account` plus a small `max_emails` (e.g. 20).
          Multi-account calls dispatch sequentially, one account at a time,
          so wall time is the sum across accounts, not the slowest one.
        - Read-status filtering binds a bounded newest-first slice and
          applies the predicate in an AppleScript ``repeat`` loop (the
          ``build_bounded_filtered_scan`` helper). This is the only safe
          form on Gmail/IMAP accounts; the historical ``whose read status
          is false`` clause crashed on Gmail because the slice's message
          refs span ``[Gmail]/All Mail``.
        - When one account times out, the call returns partial data for the
          other accounts plus an `errors` field listing the slow account(s).

    Args:
        account: Optional account name to filter (e.g., "Gmail", "Work"). If None, shows all accounts.
        max_emails: Maximum number of emails to return per account.
        read_status: ``"all"`` (default), ``"unread"``, or ``"read"`` — matches
            the same parameter on ``search_emails``. Prefer this over the
            legacy ``include_read`` bool.
        include_read: Deprecated bool form of *read_status*. ``True`` ⇒
            ``read_status="all"``, ``False`` ⇒ ``read_status="unread"``.
            Kept for back-compat; emits a DeprecationWarning when passed
            explicitly. Prefer ``read_status``.
        include_content: Whether to include a content preview for each email (slower, default: False)
        output_format: "text" (default, human-readable) or "json" (structured list of email dicts)
        exclude_replied: When True, filter out rows where Mail's native
            ``was_replied_to`` flag is true. Every row already carries
            ``was_replied_to`` unconditionally (JSON) or a ``[REPLIED]`` tag
            (text); this only controls whether matching rows are dropped.
            No Sent-mailbox scan is performed for this check (v3.11.0+: the
            native per-message property replaces the old Message-ID probe).
        flag_replied: Deprecated; ``was_replied_to`` is now always present
            on every row (no parameter gates it), so this flag no longer
            changes what is fetched. Kept only for backward compatibility:
            when True (and ``exclude_replied=False``), matching rows also
            get a legacy ``already_replied: true`` field alongside
            ``was_replied_to``. Text mode already prefixes replied rows
            with ``[REPLIED] `` regardless of this flag.
        exclude_drafted: When True, filter out rows where ``has_draft`` is
            true (a draft reply already exists). Never excludes a row whose
            ``has_draft`` is ``null`` (unknown/errored scan; see
            ``include_draft_state``). Default False.
        include_draft_state: When True (default), correlate each row
            against a bounded Drafts-mailbox snapshot (one per distinct
            account in the results, capped at 5 accounts) and populate
            ``has_draft`` (JSON: true/false/null; text: ``[HAS DRAFT]``
            tag). Set False to skip the Drafts scan entirely (JSON:
            ``has_draft`` is null on every row and the top-level
            ``draft_scan.status`` is ``"skipped"``; text: no
            ``[HAS DRAFT]`` tags, no extra AppleScript call).
        timeout: Optional per-account AppleScript timeout in seconds (default: 120s).
            Raise this for known-slow accounts (large Exchange inboxes) when
            the default budget is too tight.
        limit: Deprecated alias for `max_emails`. Accepted for backward
            compatibility with agents that misremember the param name; emits
            a warning in the response. Prefer `max_emails`.
        unread_only: Deprecated alias mapping to ``read_status="unread"``.
            Accepted for backward compatibility; emits a warning. Prefer
            ``read_status="unread"``.

    Returns:
        Text mode: formatted list of emails with subject, sender, date, and
        read status (always a ``str``).

        JSON mode (``output_format='json'``): a Python ``dict`` with stable
        shape ``{"emails": [...], "errors": [...], "draft_scan": {...}}``.
        Every email row always carries ``was_replied_to`` (bool) and
        ``has_draft`` (bool or null); ``draft_scan`` summarizes the Drafts
        correlation scan: ``{"status": "ok"|"error"|"skipped", "scanned":
        N, "accounts": [...], "error"?: "..."}``. ``errors`` lists per-account
        probe failures: a bare account name when its AppleScript probe timed
        out, or ``"<account>: <detail>"`` when the inbox script itself failed
        (missing account, unreachable mailbox). It is empty only when every
        probe succeeded, so ``emails == []`` with ``errors == []`` really does
        mean an empty inbox. When deprecated aliases (`limit`, `unread_only`)
        are used a ``warnings`` key is also present.

        **Breaking change (v3.2.x):** the JSON path previously returned a
        JSON-encoded ``str`` (sometimes a raw list, sometimes an object). It
        now always returns a ``dict``. Callers that previously did
        ``json.loads(result)`` should drop the ``json.loads`` call.

        Refusal errors (``UNBOUNDED_SCAN_REQUIRED``) continue to return a
        JSON-encoded ``str`` so text-mode and JSON-mode callers see the same
        shape for that one error path.

        When multi-account dispatch encounters per-account timeouts, the
        text response includes ``PARTIAL: ... timed out`` and the JSON
        response surfaces the slow accounts in ``errors``.
    """

    if output_format not in {"text", "json"}:
        return "Error: Invalid output_format. Use: text, json"

    # Tolerant alias handling: agents frequently misremember the param names
    # as `limit` / `unread_only` / `include_read`. Accept them, map to the
    # canonical `read_status`, and surface a warning so the agent learns
    # the right names.
    import warnings as _warnings_module

    warnings: list[str] = []
    if limit is not None:
        if max_emails != 50:
            return (
                "Error: pass either `max_emails` or `limit`, not both. `limit` is a deprecated alias for `max_emails`."
            )
        max_emails = limit
        warnings.append(
            "WARNING: 'limit' is a deprecated alias for 'max_emails' — please use 'max_emails' going forward."
        )

    # Reconcile read_status / include_read / unread_only into a single
    # 3-state read_filter that the script-builder layer understands.
    explicit_include_read = include_read is not True  # was passed as False
    if unread_only is not None:
        if read_status is not None or explicit_include_read:
            return (
                "Error: pass only one of `read_status`, `include_read`, or "
                "`unread_only`. `unread_only` is a deprecated alias for "
                "`read_status='unread'`."
            )
        read_status = "unread" if bool(unread_only) else "all"
        warnings.append("WARNING: 'unread_only' is a deprecated alias — please use read_status='unread'.")
        _warnings_module.warn(
            "list_inbox_emails: 'unread_only' is deprecated; use read_status='unread'.",
            DeprecationWarning,
            stacklevel=2,
        )
    elif explicit_include_read:
        if read_status is not None:
            return "Error: pass either `read_status` or `include_read`, not both. `include_read` is a deprecated alias."
        read_status = "all" if include_read else "unread"
        warnings.append(
            "WARNING: 'include_read' is a deprecated alias — please use read_status='all' or read_status='unread'."
        )
        _warnings_module.warn(
            "list_inbox_emails: 'include_read' is deprecated; use read_status.",
            DeprecationWarning,
            stacklevel=2,
        )

    try:
        read_filter = _resolve_read_filter(read_status, include_read)
    except ValueError as exc:
        return f"Error: {exc}"

    if max_emails <= 0:
        err = ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=("list_inbox_emails refuses to walk the full inbox; pass max_emails=50 or fewer"),
            remediation={
                "preferred": "Pass max_emails=50 or 200",
                "note": "Full-mailbox scans are disabled; bound this call.",
            },
        )
        return json.dumps(err.to_dict(), indent=2)

    # Smart default: fall back to the configured default account when neither
    # `account` nor `all_accounts` is set. Lazy attribute read so tests can
    # monkeypatch `apple_mail_mcp.server.DEFAULT_MAIL_ACCOUNT` after import.
    if account is None and not all_accounts and _server.DEFAULT_MAIL_ACCOUNT:
        account = _server.DEFAULT_MAIL_ACCOUNT

    if account:
        validation_timeout = 30 if timeout is None else min(timeout, 30)
        account_err = inbox.validate_account_name(account, timeout=validation_timeout)
        if account_err:
            if output_format == "json":
                # ``account_not_found_json`` returns a JSON-encoded string for
                # back-compat with other tools; parse to a dict for JSON-mode
                # callers so they receive the same shape as success paths.
                return cast(dict[str, Any], json.loads(account_not_found_json(account, timeout=validation_timeout)))
            return account_err

    # Native was_replied_to needs no id lookup; internet_message_id is only
    # fetched when has_draft's header-path correlation can use it.
    want_message_id = include_draft_state

    if output_format == "json":
        body = await inbox._list_inbox_emails_json(
            account,
            max_emails,
            read_filter,
            include_content,
            timeout,
            exclude_replied=exclude_replied,
            flag_replied=flag_replied,
            include_message_id=want_message_id,
            include_draft_state=include_draft_state,
            exclude_drafted=exclude_drafted,
        )
        return _attach_warnings_to_json(body, warnings)

    text_body = await inbox._list_inbox_emails_text(
        account,
        max_emails,
        read_filter,
        include_content,
        timeout,
        exclude_replied=exclude_replied,
        include_message_id=want_message_id,
        include_draft_state=include_draft_state,
        exclude_drafted=exclude_drafted,
    )
    if warnings:
        return "\n".join(warnings) + "\n" + text_body
    return text_body


def _attach_warnings_to_json(body: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    """Attach a ``warnings`` list to the JSON-mode inbox response dict.

    Returns *body* unchanged when *warnings* is empty so the stable shape
    ``{"emails": [...], "errors": [...]}`` is preserved for the common case.
    Otherwise appends to or sets the ``warnings`` key in place and returns
    *body*.
    """
    if not warnings:
        return body
    existing = body.get("warnings")
    if isinstance(existing, list):
        existing.extend(warnings)
    else:
        body["warnings"] = list(warnings)
    return body


def _run_text_one(
    account: str,
    max_emails: int,
    read_filter: str,
    include_content: bool,
    timeout: int | None,
    include_message_id: bool = False,
) -> str:
    """Synchronously run one account's text inbox script."""
    script = _build_list_inbox_text_script(account, max_emails, read_filter, include_content, include_message_id)
    return inbox.run_applescript(script, timeout=timeout if timeout is not None else 120)


async def _list_inbox_emails_text(
    account: str | None,
    max_emails: int,
    read_filter: str,
    include_content: bool,
    timeout: int | None,
    *,
    exclude_replied: bool = False,
    include_message_id: bool = False,
    include_draft_state: bool = True,
    exclude_drafted: bool = False,
) -> str:
    """Async text-format implementation, dispatching one script per account.

    Every message row carries an unconditional native ``was_replied_to``
    token (rendered as ``[REPLIED]``); ``exclude_replied``/``exclude_drafted``
    drop matching blocks instead of tagging them. ``has_draft`` correlation
    (``[HAS DRAFT]``) fetches at most one bounded Drafts snapshot per
    distinct account (capped at ``MAX_DRAFT_SNAPSHOT_ACCOUNTS``), skipped
    entirely when ``include_draft_state=False``.
    """
    header = "INBOX EMAILS - ALL ACCOUNTS\n\n"
    footer_template = (
        "========================================\nTOTAL EMAILS: {total}\n========================================\n"
    )
    draft_timeout = timeout if timeout is not None else 60
    snapshots: dict[str, DraftsSnapshot] = {}

    async def snapshot_for(acct: str) -> DraftsSnapshot | None:
        if not include_draft_state:
            return None
        if acct not in snapshots and len(snapshots) < MAX_DRAFT_SNAPSHOT_ACCOUNTS:
            snapshots[acct] = await asyncio.to_thread(fetch_drafts_snapshot, acct, inbox.run_applescript, draft_timeout)
        return snapshots.get(acct)

    async def annotate(clean: str, acct: str) -> str:
        # Skip the live Drafts scan entirely when the body has no `__ROW__`
        # markers to annotate (empty account, or a hand-built test fixture
        # with no rows): never pay for a snapshot with nothing to match.
        snap = await snapshot_for(acct) if "__ROW__|||" in clean else None
        return _annotate_text_rows_with_reply_state(
            clean, exclude_replied=exclude_replied, exclude_drafted=exclude_drafted, draft_snapshot=snap
        )

    if account:
        try:
            body = await asyncio.to_thread(
                _run_text_one,
                account,
                max_emails,
                read_filter,
                include_content,
                timeout,
                include_message_id,
            )
        except AppleScriptTimeout:
            return header + footer_template.format(total=0) + f"\nPARTIAL: 1 account(s) timed out: {account}\n"
        clean, count = _strip_count_marker(body)
        clean = await annotate(clean, account)
        return header + clean + "\n" + footer_template.format(total=count)

    # Multi-account: probe account list, then dispatch sequentially (each
    # call still off the event loop via asyncio.to_thread; Mail AppleScript
    # is serialized behind a single-flight lock, so concurrent dispatch
    # would only queue behind itself).
    try:
        accounts = await asyncio.to_thread(inbox._list_mail_accounts, timeout)
    except AppleScriptTimeout:
        return header + footer_template.format(total=0) + "\nPARTIAL: account listing timed out\n"

    if not accounts:
        return header + footer_template.format(total=0)

    async def run_one(acct: str) -> tuple[str, str | AppleScriptTimeout]:
        try:
            return acct, await asyncio.to_thread(
                _run_text_one,
                acct,
                max_emails,
                read_filter,
                include_content,
                timeout,
                include_message_id,
            )
        except AppleScriptTimeout:
            return acct, AppleScriptTimeout(acct)

    results = [await run_one(a) for a in accounts]

    pieces: list[str] = [header]
    total = 0
    errors: list[str] = []
    for acct, outcome in results:
        if isinstance(outcome, AppleScriptTimeout):
            errors.append(acct)
            continue
        clean, count = _strip_count_marker(outcome)
        clean = await annotate(clean, acct)
        if clean:
            pieces.append(clean)
            pieces.append("\n")
        total += count
    pieces.append(footer_template.format(total=total))
    if errors:
        pieces.append(f"\nPARTIAL: {len(errors)} account(s) timed out: {', '.join(errors)}\n")
    return "".join(pieces)


def _run_json_one(
    account: str,
    max_emails: int,
    read_filter: str,
    include_content: bool | int | None = False,
    timeout: int | None = None,
    include_message_id: bool = False,
) -> str:
    """Synchronously run one account's JSON inbox script."""
    # Backward compatibility for older call sites that passed
    # (account, max_emails, read_filter, timeout) before content previews
    # were added to the JSON path.
    if timeout is None and not isinstance(include_content, bool):
        timeout = include_content
        include_content = False
    script = _build_list_inbox_json_script(
        account,
        max_emails,
        read_filter,
        bool(include_content),
        include_message_id=include_message_id,
    )
    return inbox.run_applescript(script, timeout=timeout if timeout is not None else 120)


async def _list_inbox_emails_json(
    account: str | None,
    max_emails: int,
    read_filter: str,
    include_content: bool,
    timeout: int | None,
    *,
    exclude_replied: bool = False,
    flag_replied: bool = False,
    include_message_id: bool = False,
    include_draft_state: bool = True,
    exclude_drafted: bool = False,
) -> dict[str, Any]:
    """Return inbox emails as a structured dict.

    Stable shape: ``{"emails": [...], "errors": [...], "draft_scan": {...}}``
    for both the single-account and multi-account paths. ``errors`` holds a
    bare account name for a timed-out probe and ``"<account>: <detail>"`` for
    an inbox script that reported a top-level failure via the in-band
    ``__APPLE_MAIL_MCP_ERROR__`` marker (``_parse_inbox_error_lines``), so an
    empty ``emails`` list with an empty ``errors`` list is a genuinely empty
    inbox rather than a swallowed failure. Account-listing timeouts surface as
    ``{"emails": [], "errors": ["__account_listing__"], "draft_scan": ...}``.

    Every email always carries ``was_replied_to`` (bool, Mail's native
    property, no extra AppleScript round trip) and ``has_draft``
    (true/false/null, governed by ``include_draft_state``). When
    ``include_content`` is True each record also gains a
    ``content_preview`` field. ``exclude_replied``/``exclude_drafted`` drop
    matching rows; ``flag_replied`` (deprecated) additionally sets a legacy
    ``already_replied: true`` field when ``exclude_replied=False``.
    ``draft_scan`` summarizes the Drafts correlation scan:
    ``{"status": "ok"|"error"|"skipped", "scanned": N, "accounts": [...]}``.

    **Breaking change (v3.2.x):** previously returned a JSON-encoded
    ``str`` (sometimes a raw list, sometimes a dict). Callers that did
    ``json.loads(result)`` should drop the ``json.loads``.
    """
    errors: list[str]
    if account:
        try:
            raw = await asyncio.to_thread(
                _run_json_one,
                account,
                max_emails,
                read_filter,
                include_content,
                timeout,
                include_message_id,
            )
        except AppleScriptTimeout:
            return {"emails": [], "errors": [account], "draft_scan": build_draft_scan_status({})}
        emails = _parse_pipe_delimited_emails(raw, has_message_id=include_message_id)
        # A swallowed AppleScript failure used to reach here as "" → [] with
        # errors == [], which reads as "inbox empty". The script now emits an
        # in-band marker line instead; surface it the way text mode already does.
        errors = _parse_inbox_error_lines(raw)
    else:
        try:
            accounts = await asyncio.to_thread(inbox._list_mail_accounts, timeout)
        except AppleScriptTimeout:
            return {"emails": [], "errors": ["__account_listing__"], "draft_scan": build_draft_scan_status({})}

        if not accounts:
            return {"emails": [], "errors": [], "draft_scan": build_draft_scan_status({})}

        async def run_one(acct: str) -> tuple[str, str | AppleScriptTimeout]:
            try:
                return acct, await asyncio.to_thread(
                    _run_json_one,
                    acct,
                    max_emails,
                    read_filter,
                    include_content,
                    timeout,
                    include_message_id,
                )
            except AppleScriptTimeout:
                return acct, AppleScriptTimeout(acct)

        results = [await run_one(a) for a in accounts]

        emails = []
        errors = []
        for acct, outcome in results:
            if isinstance(outcome, AppleScriptTimeout):
                errors.append(acct)
                continue
            emails.extend(_parse_pipe_delimited_emails(outcome, has_message_id=include_message_id))
            errors.extend(_parse_inbox_error_lines(outcome))

    # Replied-detection: `was_replied_to` is parsed straight off Mail's
    # native property, so no Sent-mailbox AppleScript round trip is needed.
    if exclude_replied:
        emails = [e for e in emails if not e.get("was_replied_to")]
    elif flag_replied:
        for email in emails:
            if email.get("was_replied_to"):
                email["already_replied"] = True

    draft_timeout = timeout if timeout is not None else 60
    snapshots = annotate_rows_with_reply_state(
        emails,
        runner=inbox.run_applescript,
        timeout=draft_timeout,
        include_draft_state=include_draft_state,
    )
    if exclude_drafted:
        emails = [e for e in emails if not e.get("has_draft")]

    return {"emails": emails, "errors": errors, "draft_scan": build_draft_scan_status(snapshots)}
