"""The ``search_emails`` ``@mcp.tool`` and its windowing/replied-detection logic.

``validate_account_name`` is routed through the ``search`` package facade so the
conftest autouse patch and ``test_phase_a_fixes`` patch keep firing. ``asyncio``
is imported plainly (the shared module honors ``...search.asyncio`` patches), and
``DEFAULT_MAIL_ACCOUNT`` is read lazily off the ``_server`` module so tests can
monkeypatch ``apple_mail_mcp.server.DEFAULT_MAIL_ACCOUNT`` after import.
"""

import asyncio
import json
from datetime import datetime, timedelta

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.core import AppleScriptTimeout, inject_preferences, list_mail_account_names, normalize_search_terms
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import search
from apple_mail_mcp.tools.reply_state_wiring import annotate_rows_with_reply_state, build_draft_scan_status
from apple_mail_mcp.tools.search.dispatch import _search_mail_records
from apple_mail_mcp.tools.search.records import _body_scan_disabled_error, _build_search_response


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Search Emails")
@inject_preferences
async def search_emails(
    account: str | None = None,
    all_accounts: bool = False,
    mailbox: str = "INBOX",
    subject_keyword: str | None = None,
    subject_keywords: list[str] | None = None,
    sender: str | None = None,
    sender_exact: str | None = None,
    sender_domain: str | None = None,
    internet_message_id: str | None = None,
    has_attachments: bool | None = None,
    read_status: str = "all",
    date_from: str | None = None,
    date_to: str | None = None,
    recent_days: float = 2.0,
    include_content: bool = False,
    max_content_length: int = 500,
    body_text: str | None = None,
    allow_body_scan: bool = False,
    max_results: int | None = 20,
    output_format: str = "text",
    offset: int = 0,
    limit: int | None = None,
    sort: str = "date_desc",
    exclude_replied: bool = False,
    flag_replied: bool = False,
    exclude_drafted: bool = False,
    include_draft_state: bool = True,
    timeout: int | None = None,
    mailboxes: list[str] | None = None,
) -> str:
    """Defaults to the last 48 hours and the configured default account. Pass `recent_days=7` for the past week; ``recent_days=0`` without ``date_from`` is rejected since full-mailbox scans are disabled.

    Unified search tool with JSON output, pagination, and real date filtering.

    Consolidates subject search, sender search, exact sender/domain discovery,
    body content search, exact Internet Message-ID lookup, and cross-account
    search into a single tool.

    Smart defaults:
        - When `date_from` is None and `recent_days > 0`, an effective window
          of `now - recent_days` days is applied. Unbounded scans
          (``recent_days=0`` without ``date_from``) are refused with an
          ``UNBOUNDED_SCAN_REQUIRED`` error; full-mailbox scans are disabled,
          so pass a bounded ``recent_days`` or ``date_from``. An explicit
          ``date_from`` always wins.
        - When `account` is None and `all_accounts` is False, the tool falls
          back to the ``DEFAULT_MAIL_ACCOUNT`` env-configured account if one
          is set. Pass `all_accounts=True` to opt back into multi-account
          dispatch even when a default is configured.
        - `recent_days` is applied BEFORE pagination, so `offset` counts
          within the windowed result set.

    Performance guidance (read before omitting filters on large mailboxes):
        - Multi-account search (account=None) on a 10K+ inbox can be slow.
          Prefer passing `account` plus `date_from` together when you know
          which mailbox the messages are in.
        - Setting `body_text` to any non-empty string scans message bodies
          (O(N × message-size)); pair with tight filters (account, date_from,
          subject_keyword) to keep wall time predictable on large mailboxes.
        - When account is None each account runs in parallel; one slow
          account no longer blocks the others, but its name will appear in
          the response's `errors` field (JSON) or partial banner (text).
          JSON also includes `error_details` when the failure reason is known.
        - `was_replied_to` is read off Mail's native property in the same
          per-message pass as subject/sender, so `exclude_replied` and
          `flag_replied` no longer cost a second AppleScript round trip (no
          more Sent-mailbox scan). `include_draft_state=True` (the default)
          does add up to one bounded Drafts-mailbox scan per account
          appearing in the result set (capped at 5 accounts); set
          `include_draft_state=False` to skip it entirely.

    Args:
        account: Account name to search in (e.g., "Gmail", "Work").
            If None, searches ALL accounts in parallel (slower wall time on
            very large inboxes — prefer specifying account + date_from).
        mailbox: Mailbox to search (default: "INBOX", use "All" for all mailboxes, or specific folder name)
        subject_keyword: Optional keyword to search in subject
        subject_keywords: Optional list of subject keywords; matches any keyword
        sender: Optional fuzzy sender email or name to filter by
        sender_exact: Optional exact sender address discovery filter
        sender_domain: Optional exact sender domain discovery filter, with or without "@"
        internet_message_id: Optional exact Internet Message-ID discovery filter.
            Angle brackets are optional.
        has_attachments: Optional filter for emails with attachments (True/False/None)
        read_status: Filter by read status: "all", "read", "unread" (default: "all")
        date_from: Optional start date filter (format: "YYYY-MM-DD")
        date_to: Optional end date filter (format: "YYYY-MM-DD")
        include_content: Whether to include email content preview (slower)
        max_content_length: Maximum content length in characters when include_content=True (default: 500, 0 = unlimited)
        body_text: Optional[str] text to search for in email body content (case-insensitive).
            Setting `body_text` to any non-empty string scans message bodies
            (O(N × message-size)); pair with tight filters (account, date_from,
            subject_keyword) to keep wall time predictable on large mailboxes.
        allow_body_scan: Opt in to body_text scans (default False). When False,
            passing body_text returns a structured ``BODY_SCAN_DISABLED`` error.
        max_results: Backward-compatible alias for limit. Same positive-value
            requirement as `limit`.
        output_format: Output format: "text" or "json" (default: "text")
        offset: Number of matching results to skip before returning data.
            Must be >= 0; a negative offset returns
            ``"Error: offset must be >= 0"`` before any AppleScript runs.
        limit: Maximum number of results to return per page. Must be > 0; a
            zero or negative `limit` (or `max_results`) is refused with a
            structured ``UNBOUNDED_SCAN_REQUIRED`` error before any AppleScript
            runs, rather than returning an empty result set that would be
            indistinguishable from "no matches".
        sort: Result sort order: "date_desc" or "date_asc"
        exclude_replied: When True, filter out emails whose native
            `was_replied_to` flag is true (Mail's own read-only "was replied
            to" property, read in the same per-message pass as
            subject/sender, no extra AppleScript round trip). Default False
            keeps backward-compatible behavior.
        flag_replied: Deprecated; `was_replied_to` is now always present on
            every item (no parameter gates it), so this flag no longer
            changes what is fetched. Kept only for backward compatibility:
            when True (and `exclude_replied=False`), matching items also get
            a legacy `already_replied: true` field alongside `was_replied_to`.
            Text mode already prefixes replied rows with `[REPLIED] `
            regardless of this flag.
        exclude_drafted: When True, filter out emails that already have a
            correlated Drafts reply (see `include_draft_state`). Never
            excludes an email whose draft scan was skipped or errored
            (`has_draft` is null in that case, not treated as drafted).
            Default False.
        include_draft_state: When True (default), fetch one bounded Drafts
            snapshot per account appearing in the result set (lazily, capped
            at 5 accounts) and set `has_draft` (true/false/null) on every
            item. Set False to skip the Drafts scan entirely (zero extra
            AppleScript calls); `has_draft` is then null on every item and
            the response's `draft_scan.status` is "skipped".
        timeout: Optional per-account AppleScript timeout in seconds. Defaults
            to 180s. Raise this for known-slow accounts (e.g. large Exchange
            inboxes) when the default times out.
        mailboxes: Optional explicit list of folder names to search (e.g.
            ["Archive", "Sent"]). When provided and non-empty, overrides
            ``mailbox`` and searches only those named folders for the account.
            Missing folders emit a structured mailbox error and are skipped.

    Returns:
        Formatted list of matching emails or JSON payload with stable message
        metadata. When one or more accounts fail during a multi-account call,
        the response includes account names plus error details so the caller can
        retry timeout accounts or fix non-timeout failures. Every item carries
        `was_replied_to` (bool, always present) and `has_draft`
        (true/false/null, governed by `include_draft_state`); text mode
        prefixes matching rows with `[REPLIED]` / `[HAS DRAFT]`. JSON
        responses also include a top-level `draft_scan` object:
        `{"status": "ok" | "error" | "skipped", "scanned": N, "accounts": [...],
        "error"?: "..."}`.
    """
    if output_format not in {"text", "json"}:
        return "Error: Invalid output_format. Use: text, json"

    if body_text and not allow_body_scan:
        return _body_scan_disabled_error()

    sender_only_hint = bool(
        sender
        and not sender_exact
        and not sender_domain
        and not subject_keyword
        and not subject_keywords
        and not internet_message_id
        and date_from is None
        and not body_text
        and has_attachments is None
    )

    if limit is None:
        limit = max_results if max_results is not None else 100

    # Pagination validation, ahead of the account probe and the script builder,
    # so a malformed page request never reaches Mail.app.
    #
    # `script._build_search_script` derives its scan bound as
    # `base_cap = limit + 1 + offset`, so a non-positive `limit` (or an `offset`
    # negative enough to cancel it) produces `messages 1 thru 0` — a slice a
    # read-only live probe across four Mail backends showed does NOT raise on a
    # non-empty mailbox: AppleScript clamps index 0 up to 1 and hands back one
    # real message. `dispatch._search_mail_records` does guard both cases, but it
    # absorbs them instead of reporting them (`limit <= 0` returns an empty
    # tuple; `offset < 0` raises a `ValueError` this function flattens to a bare
    # string), so a nonsense page size rendered as an authoritative
    # `{"items": [], "returned": 0}` — indistinguishable from an empty mailbox —
    # and `limit=-1` also reported `has_more: true` with `next_offset: 0`
    # (`len([]) > -1`), a pagination loop with no exit. Validating here also
    # stops leaning on that downstream guard for correctness:
    # `dispatch._search_mail_records_sync(account=...)` reaches the same builder
    # while skipping it entirely.
    if offset < 0:
        # Bare string matches the sibling `export_emails` / `list_events` offset
        # guards and preserves the exact text the `ValueError` path already
        # returned. No structured code: none of the documented codes describes a
        # negative offset, and minting one here alone would split the pagination
        # contract across two shapes.
        return "Error: offset must be >= 0"
    if limit <= 0:
        # Reuses `UNBOUNDED_SCAN_REQUIRED` rather than adding a code: the closest
        # sibling, `list_inbox_emails`, already answers `max_emails <= 0` with
        # exactly this code, and `tools/CLAUDE.md` documents its trigger as
        # "`recent_days=0` / `max_emails=0`" — a zero page size is already inside
        # the code's meaning. The remediation names the page-size knob rather
        # than the date window, matching that sibling.
        return serialize_tool_error(
            ToolError(
                code="UNBOUNDED_SCAN_REQUIRED",
                message=(f"search_emails refuses a non-positive limit (got limit={limit}); pass limit=20"),
                remediation={
                    "preferred": "Pass limit=20 (or max_results=20)",
                    "note": "Page with offset to reach older messages; full-mailbox scans are disabled.",
                },
            )
        )

    effective_recent_days = float(recent_days) if recent_days else 0.0
    if date_from is None and effective_recent_days <= 0:
        tool_error = ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=(
                "search_emails refuses to scan without a date window or recent_days; pass recent_days=7 or date_from"
            ),
            remediation={
                "preferred": "Pass recent_days=7 or date_from='YYYY-MM-DD'",
                "note": "Full-mailbox scans are disabled; bound this call.",
            },
        )
        # Always emit the structured JSON envelope (with remediation) for
        # UNBOUNDED_SCAN_REQUIRED, even when output_format != "json".
        # Dropping the remediation would lose the caller's recovery path.
        return serialize_tool_error(tool_error)

    # Smart default: fall back to the configured default account when neither
    # `account` nor `all_accounts` is set. Lazy attribute read so tests can
    # monkeypatch `apple_mail_mcp.server.DEFAULT_MAIL_ACCOUNT` after import.
    if account is None and not all_accounts and _server.DEFAULT_MAIL_ACCOUNT:
        account = _server.DEFAULT_MAIL_ACCOUNT

    if account:
        validation_timeout = 30 if timeout is None else min(timeout, 30)
        account_err = search.validate_account_name(account, timeout=validation_timeout)
        if account_err:
            available_accounts: list[str] = []
            try:
                available_accounts = list_mail_account_names(timeout=validation_timeout)
            except AppleScriptTimeout:
                available_accounts = []
            if output_format == "json":
                return json.dumps(
                    {
                        "results": [],
                        "total": 0,
                        "error": "account_not_found",
                        "account": account,
                        "available_accounts": available_accounts,
                    },
                    indent=2,
                )
            return account_err

    # Smart default: 48h window when no explicit start date was passed.
    searched_from: str | None = None
    _date_from_explicit = False
    if date_from is None and effective_recent_days > 0:
        cutoff = datetime.now() - timedelta(days=effective_recent_days)
        date_from = cutoff.strftime("%Y-%m-%d")
        searched_from = date_from
    elif date_from is not None:
        # Explicit caller override — effective window is 0 for reporting purposes.
        effective_recent_days = 0.0
        searched_from = date_from
        _date_from_explicit = True

    subject_terms = normalize_search_terms(subject_keyword, subject_keywords)

    try:
        records, errors, error_details, body_search_capped = await _search_mail_records(
            account=account,
            mailbox=mailbox,
            subject_terms=subject_terms,
            sender=sender,
            sender_exact=sender_exact,
            sender_domain=sender_domain,
            internet_message_id=internet_message_id,
            has_attachments=has_attachments,
            read_status=read_status,
            date_from=date_from,
            date_to=date_to,
            include_content=include_content,
            content_length=max_content_length,
            offset=offset,
            limit=limit,
            sort=sort,
            body_text=body_text,
            timeout=timeout,
            recent_days=effective_recent_days,
            date_from_explicit=_date_from_explicit,
            mailboxes=mailboxes if mailboxes else None,
        )

        # Replied-detection: `was_replied_to` is parsed straight off Mail's
        # native property (see script._build_search_script), so no second
        # Sent-mailbox AppleScript round trip is needed here anymore.
        if exclude_replied:
            records = [r for r in records if not r.get("was_replied_to")]
        elif flag_replied:
            for rec in records:
                if rec.get("was_replied_to"):
                    rec["already_replied"] = True

        # Draft-state annotation: one bounded Drafts snapshot per account
        # appearing in the (already replied-filtered) result set, run off the
        # event loop since it is itself a synchronous AppleScript round trip.
        draft_timeout = timeout if timeout is not None else 60
        snapshots = await asyncio.to_thread(
            annotate_rows_with_reply_state,
            records,
            runner=search.run_applescript,
            timeout=draft_timeout,
            include_draft_state=include_draft_state,
            date_field="received_date",
        )
        draft_scan = build_draft_scan_status(snapshots)
        if exclude_drafted:
            records = [r for r in records if not r.get("has_draft")]

        _mailbox_all = mailbox == "All"
        return _build_search_response(
            records,
            offset=offset,
            limit=limit,
            sort=sort,
            output_format=output_format,
            subject_only=False,
            errors=errors or None,
            error_details=error_details or None,
            recent_days_applied=effective_recent_days,
            searched_from=searched_from,
            body_search_capped=body_search_capped,
            mailbox_count_capped=_mailbox_all,
            mailboxes_truncated=_mailbox_all,
            sender_only_hint=sender_only_hint,
            include_content_hint=include_content,
            body_text_hint=bool(body_text),
            draft_scan=draft_scan,
        )
    except ValueError as exc:
        return f"Error: {exc}"
