"""Shared reply-state annotation from native, Sent-header, and Draft evidence.

Generic helper consumed by the inbox surface (``list_inbox_emails``,
``get_inbox_overview``), ``analytics/dashboard.py`` (``inbox_dashboard``),
and the ``search/`` surface (``search_emails``, ``get_email_by_id``,
``get_email_by_ids``, ``get_email_thread``, via ``date_field="received_date"``
since parsed search records key their received-date under a different
name), implementing the contract in
``tasks/active/reply-state-annotation/plan-2026-07-10.md``. Wraps
``core.reply_state.fetch_drafts_snapshot`` / ``DraftsSnapshot.matches()`` so
no tool surface re-implements per-account snapshot fan-out or the
``draft_scan`` response shape.

Every row passed in must already carry a native ``was_replied_to`` bool (set
from ``core.reply_state.was_replied_fragment()`` in the caller's own
AppleScript loop); this module only adds ``has_draft``.

Public API:

- ``annotate_rows_with_reply_state(rows, runner=..., timeout=..., ...)``:
  mutates *rows* in place, adding ``has_draft`` (``True``/``False``/``None``)
  to every row. Returns the accumulated ``dict[str, DraftsSnapshot]`` cache
  so callers that process one account at a time (``get_inbox_overview``) can
  share it across calls and build one combined summary at the end.
- ``build_draft_scan_status(snapshots)``: turns that cache into the
  top-level ``draft_scan`` object: ``{"status", "scanned", "accounts", ...}``.
"""

from __future__ import annotations

from typing import Any

from apple_mail_mcp.core.applescript import AppleScriptRunner
from apple_mail_mcp.core.replied import SentReplySnapshot, fetch_sent_reply_snapshot
from apple_mail_mcp.core.reply_state import DraftsSnapshot, fetch_drafts_snapshot, resolve_has_draft

# Multi-account fan-out never fetches more than this many Drafts snapshots
# per call, mirroring the plan's "capped at 5 accounts" contract.
MAX_DRAFT_SNAPSHOT_ACCOUNTS = 5
MAX_SENT_REPLY_SNAPSHOT_ACCOUNTS = 5
SENT_REPLY_SCAN_TIMEOUT = 30


def bounded_sent_reply_timeout(timeout: int | None) -> int:
    """Return the dedicated short timeout used for bounded Sent-header scans."""
    return SENT_REPLY_SCAN_TIMEOUT if timeout is None else min(timeout, SENT_REPLY_SCAN_TIMEOUT)


def new_sent_reply_scan() -> tuple[dict[str, SentReplySnapshot], list[str]]:
    """Return typed snapshot and requested-account accumulators."""
    return {}, []


def build_sent_reply_scan_status(
    snapshots: dict[str, SentReplySnapshot],
    requested_accounts: list[str] | None = None,
) -> dict[str, Any]:
    """Aggregate structured per-account Sent reply snapshots."""
    requested = list(dict.fromkeys(requested_accounts or snapshots))
    skipped_accounts = [account for account in requested if account not in snapshots]
    account_limit_reached = bool(skipped_accounts)
    if not snapshots and not requested:
        return {
            "status": "skipped",
            "scanned": 0,
            "total": 0,
            "truncated": False,
            "accounts": [],
            "errors": [],
            "account_limit_reached": False,
            "skipped_account_count": 0,
            "skipped_accounts": [],
        }

    accounts_detail: list[dict[str, Any]] = []
    errors: list[str] = []
    scanned_total = 0
    total_messages = 0
    any_truncated = False
    any_error = False
    for account, snapshot in snapshots.items():
        accounts_detail.append(
            {
                "account": account,
                "status": snapshot.status,
                "scanned": snapshot.scanned,
                "total": snapshot.total,
                "truncated": snapshot.truncated,
                "errors": list(snapshot.errors),
            }
        )
        scanned_total += snapshot.scanned
        total_messages += snapshot.total if snapshot.total is not None else snapshot.scanned
        any_truncated = any_truncated or snapshot.truncated
        any_error = any_error or snapshot.status != "ok"
        errors.extend(f"{account}: {error}" for error in snapshot.errors)

    return {
        "status": "error" if any_error else "partial" if account_limit_reached else "ok",
        "scanned": scanned_total,
        "total": total_messages,
        "truncated": any_truncated or account_limit_reached,
        "accounts": accounts_detail,
        "errors": errors,
        "account_limit_reached": account_limit_reached,
        "skipped_account_count": len(skipped_accounts),
        "skipped_accounts": skipped_accounts,
    }


def sent_reply_scan_warning(scan: dict[str, Any] | None) -> str | None:
    """Describe incomplete Sent evidence for text responses that consumed it."""
    if not scan or scan.get("status") == "skipped":
        return None
    if scan.get("account_limit_reached"):
        skipped = ", ".join(scan.get("skipped_accounts") or []) or "unknown accounts"
        return (
            "Sent reply-state scan reached the account limit and skipped "
            f"{skipped}; unmatched messages from those accounts have unknown reply state."
        )
    if scan.get("status") == "error":
        return "Sent reply-state scan failed or was incomplete; unmatched messages have unknown reply state."
    if scan.get("truncated"):
        return (
            f"Sent reply-state scan examined {scan.get('scanned', 0)} of {scan.get('total', 0)} message(s); "
            "unmatched messages have unknown reply state."
        )
    return None


def build_draft_scan_status(snapshots: dict[str, DraftsSnapshot]) -> dict[str, Any]:
    """Aggregate per-account Drafts snapshots into a top-level ``draft_scan`` object.

    Returns ``{"status": "skipped", "scanned": 0, "total": 0,
    "truncated": False, "accounts": []}`` when *snapshots* is empty (no
    account ever needed a Drafts scan: either ``include_draft_state=False``
    or no row carried a resolvable account). Otherwise ``status`` is
    ``"ok"`` only when every scanned account came back ``"ok"``; any
    ``"error"`` account flips the overall status to ``"error"`` and its
    message is folded into a combined ``"error"`` key. Every envelope
    carries the same ``total`` / ``truncated`` keys as the per-account
    rows, so the ``draft_scan`` shape is uniform across producers.
    """
    if not snapshots:
        return {"status": "skipped", "scanned": 0, "total": 0, "truncated": False, "accounts": []}

    accounts_detail: list[dict[str, Any]] = []
    errors: list[str] = []
    scanned_total = 0
    total_drafts = 0
    any_truncated = False
    all_ok = True
    for account, snapshot in snapshots.items():
        accounts_detail.append(
            {
                "account": account,
                "status": snapshot.status,
                "scanned": snapshot.scanned,
                "total": snapshot.total,
                "truncated": snapshot.truncated,
            }
        )
        scanned_total += snapshot.scanned
        total_drafts += snapshot.total if snapshot.total is not None else snapshot.scanned
        any_truncated = any_truncated or snapshot.truncated
        if snapshot.status != "ok":
            all_ok = False
            if snapshot.error:
                errors.append(f"{account}: {snapshot.error}")

    result: dict[str, Any] = {
        "status": "ok" if all_ok else "error",
        "scanned": scanned_total,
        "total": total_drafts,
        "truncated": any_truncated,
        "accounts": accounts_detail,
    }
    if errors:
        result["error"] = "; ".join(errors)
    return result


def annotate_rows_with_reply_state(
    rows: list[dict[str, Any]],
    *,
    runner: AppleScriptRunner,
    timeout: int,
    include_draft_state: bool = True,
    include_sent_reply_state: bool = False,
    account: str | None = None,
    account_field: str = "account",
    date_field: str = "date",
    max_accounts: int = MAX_DRAFT_SNAPSHOT_ACCOUNTS,
    snapshots: dict[str, DraftsSnapshot] | None = None,
    sent_snapshots: dict[str, SentReplySnapshot] | None = None,
    sent_accounts_requested: list[str] | None = None,
    sent_timeout: int | None = None,
) -> dict[str, DraftsSnapshot]:
    """Annotate rows with raw native state, bounded Sent evidence, and drafts.

    Every row gains a ``has_draft`` key: ``True``/``False`` when a Drafts
    snapshot for its account came back ``"ok"``, ``None`` when the scan was
    skipped (``include_draft_state=False``) or the account's own snapshot
    errored or was never fetched. ``has_draft`` never silently becomes
    ``False`` on a failed or skipped scan.

    When *account* is given, every row is treated as belonging to that one
    account (an explicit override for callers such as ``get_inbox_overview``
    that already process one account's rows at a time). Otherwise each
    row's own ``row[account_field]`` groups it, and at most *max_accounts*
    distinct accounts (lazily, in first-seen row order) get a Drafts
    snapshot fetched; rows whose account did not make the cap get
    ``has_draft=None``.

    *date_field* names the row key holding the candidate's received-date
    string used by the subject+recipient+date correlation rule. Callers
    whose rows come from ``search.records._parse_search_records`` pass
    ``date_field="received_date"``; every other row shape in this codebase
    uses the default ``"date"``.

    Pass a shared *snapshots* dict across repeated calls (e.g. once per
    account in a loop) to reuse already-fetched snapshots and build one
    combined ``draft_scan`` via ``build_draft_scan_status()`` afterwards.
    The cache used (created fresh when *snapshots* is omitted) is always
    returned so the caller can pass it back in on the next call.
    """
    cache: dict[str, DraftsSnapshot] = {} if snapshots is None else snapshots
    sent_cache: dict[str, SentReplySnapshot] = {} if sent_snapshots is None else sent_snapshots

    for row in rows:
        native_replied = bool(row.get("was_replied_to"))
        row["was_replied_to"] = native_replied
        row["mail_was_replied_to"] = native_replied
        row["has_sent_reply"] = None
        row["reply_state"] = True if native_replied else None

    if include_sent_reply_state:
        if account is not None:
            sent_accounts_needed: list[str] = [account] if rows else []
        else:
            sent_accounts_needed = []
            for row in rows:
                if row.get("was_replied_to") or not str(row.get("internet_message_id") or "").strip():
                    continue
                row_account = row.get(account_field)
                if row_account and row_account not in sent_accounts_needed:
                    sent_accounts_needed.append(row_account)

        if account is not None and not any(
            not row.get("was_replied_to") and str(row.get("internet_message_id") or "").strip() for row in rows
        ):
            sent_accounts_needed = []

        if sent_accounts_requested is not None:
            for candidate_account in sent_accounts_needed:
                if candidate_account not in sent_accounts_requested:
                    sent_accounts_requested.append(candidate_account)

        for candidate_account in sent_accounts_needed:
            if candidate_account in sent_cache or len(sent_cache) >= MAX_SENT_REPLY_SNAPSHOT_ACCOUNTS:
                continue
            sent_cache[candidate_account] = fetch_sent_reply_snapshot(
                candidate_account,
                runner=runner,
                timeout=sent_timeout if sent_timeout is not None else bounded_sent_reply_timeout(timeout),
            )

        for row in rows:
            row_account = account if account is not None else row.get(account_field)
            sent_snapshot = sent_cache.get(row_account) if row_account else None
            has_sent_reply = sent_snapshot.matches(row.get("internet_message_id")) if sent_snapshot else None
            row["has_sent_reply"] = has_sent_reply
            if row["mail_was_replied_to"] or has_sent_reply is True:
                row["reply_state"] = True
            elif has_sent_reply is False:
                row["reply_state"] = False

    if not include_draft_state:
        for row in rows:
            row["has_draft"] = None
        return cache

    if account is not None:
        # Nothing to correlate against an empty row list; skip the live
        # Drafts scan entirely rather than paying for an unused snapshot.
        accounts_needed: list[str] = [account] if rows else []
    else:
        accounts_needed = []
        for row in rows:
            row_account = row.get(account_field)
            if row_account and row_account not in accounts_needed:
                accounts_needed.append(row_account)

    for candidate_account in accounts_needed:
        if candidate_account in cache or len(cache) >= max_accounts:
            continue
        cache[candidate_account] = fetch_drafts_snapshot(candidate_account, runner, timeout)

    for row in rows:
        row_account = account if account is not None else row.get(account_field)
        snapshot = cache.get(row_account) if row_account else None
        row["has_draft"] = resolve_has_draft(
            snapshot,
            subject=row.get("subject") or "",
            sender_email=row.get("sender") or "",
            internet_message_id=row.get("internet_message_id"),
            email_date=row.get(date_field),
        )

    return cache
