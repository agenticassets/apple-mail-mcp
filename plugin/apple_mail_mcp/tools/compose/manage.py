"""``manage_drafts`` tool: list/find/open/delete Mail drafts."""

from apple_mail_mcp.applescript_snippets import thread_headers_block
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error, target_selector_deprecated_error
from apple_mail_mcp.core import AppleScriptTimeout, escape_applescript, inject_preferences, normalize_message_ids
from apple_mail_mcp.server import DESTRUCTIVE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import compose
from apple_mail_mcp.tools.compose.cleanup import _expected_recipient_literal
from apple_mail_mcp.tools.compose.constants import DRAFT_LIST_CAP
from apple_mail_mcp.tools.compose.drafts_scripts import (
    _build_manage_drafts_cleanup_script,
    _build_manage_drafts_find_script,
    _build_manage_drafts_list_script,
)
from apple_mail_mcp.tools.compose.helpers import _resolve_account, _validate_from_address
from apple_mail_mcp.tools.compose.payload import (
    _compose_sender_script,
    _standalone_compose_thread_warning,
    _strip_cdata_wrappers,
)


@mcp.tool(annotations=DESTRUCTIVE_TOOL_ANNOTATIONS, title="Manage Drafts")
@inject_preferences
def manage_drafts(
    account: str | None = None,
    action: str = "list",
    subject: str | None = None,
    to: str | None = None,
    body: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    draft_subject: str | None = None,
    draft_id: str | None = None,
    from_address: str | None = None,
    timeout: int | None = None,
    standalone_confirmed: bool = False,
    hide_empty: bool = False,
    dry_run: bool = True,
    max_deletes: int = 20,
    subject_contains: str | None = None,
    limit: int | None = None,
    in_reply_to: str | None = None,
    expected_in_reply_to: str | None = None,
    expected_subject: str | None = None,
    expected_to: str | None = None,
) -> str:
    """
    Manage draft emails - list, create, send, open, delete, or cleanup_empty drafts.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to `DEFAULT_MAIL_ACCOUNT` env var if `account` is omitted.
        action: Action to perform: "list", "find", "create", "send", "open", "delete", or "cleanup_empty". Use "open" to open a draft in a visible compose window for review before sending. Use "cleanup_empty" to remove orphaned blank drafts (preview-only by default). On Exchange and other server accounts, Drafts numeric ids are reassigned on sync and are not stable across calls, including across repeated action="list" calls with no writes; re-run action="list" (or action="find") immediately before send/open/delete rather than caching a draft_id across turns.
        subject: Email subject (required for create)
        to: Recipient email(s) for create (comma-separated)
        body: Email body (required for create)
        cc: Optional CC recipients for create
        bcc: Optional BCC recipients for create
        draft_subject: Deprecated subject keyword selector retained for v3.x schema
            compatibility. Use ``manage_drafts(action="list", subject_contains=...)``
            or ``manage_drafts(action="find", ...)`` to discover ``draft_id``. Passing
            ``draft_subject`` without ``draft_id`` returns ``TARGET_SELECTOR_DEPRECATED``.
        draft_id: Exact numeric Drafts message id for send/open/delete (required for targeting).
            On Exchange and other server accounts this id is reassigned on sync and is not a
            stable handle: re-resolve it with action="list" or action="find" immediately
            before acting, do not cache it across turns. For a durable handle to a reply
            draft, use action="find" with in_reply_to, which matches the source Message-ID
            in the draft's threading headers instead of a store-assigned numeric id.
        from_address: Optional sender address for new drafts (action="create"). Must be one of the account's configured email addresses. When omitted, Mail uses the account's default "Send new messages from" setting.
        timeout: Optional per-AppleScript timeout in seconds. Defaults to the standard 120s. Raise this when working with large mailboxes or slow accounts.
        standalone_confirmed: Required explicit override for action="create" when the subject/body looks like a reply or forward but the caller intentionally wants a new standalone draft.
        hide_empty: For action="list", skip drafts whose subject AND body are both blank (orphaned compose windows). Default False (show everything).
        subject_contains: For action="list", only show drafts whose subject contains this keyword (case-insensitive). This is the fast, reliable way to find a draft you just created — the list scans the newest drafts first and applies the filter in-loop (no date filter is added). Default None (show everything).
        dry_run: For action="cleanup_empty", when True (default) only previews which blank drafts would be removed without deleting. Set False to actually delete. Ignored by other actions. A draft is classified blank only when its subject and body were both read successfully; a draft whose subject or body read fails is reported as skipped and is never deleted, because a failed read is not evidence of emptiness.
        max_deletes: For action="cleanup_empty", maximum number of blank drafts to delete in one call (safety cap). Default 20. Ignored by other actions.
        limit: For action="list" and action="find", maximum newest Drafts messages to show or inspect. Defaults to the repo scan cap.
        in_reply_to: For action="find", source Internet Message-ID to match against Drafts In-Reply-To or References headers. Honored ONLY by action="find". action="create" cannot set In-Reply-To/References (the Mail scripting dictionary exposes no header property on a new outgoing message), so passing in_reply_to with action="create" returns CREATE_CANNOT_THREAD and creates no draft; use reply_to_email(message_id=...) to thread a reply instead.
        expected_in_reply_to: For action="delete", the durable source
            Message-ID expected in the current draft's In-Reply-To or
            References headers. Supplying this guard requires
            ``expected_subject`` and ``expected_to`` too; all three are
            re-read inside the same AppleScript transaction immediately before
            deletion. Use this guarded path for synchronized IMAP/Exchange
            Drafts, whose numeric ids can drift.
        expected_subject: Exact subject expected by the guarded delete path.
        expected_to: Comma-separated recipient set expected by the guarded
            delete path. Comparison is case-insensitive and set-based, and runs
            in both directions: every expected recipient must be present, and no
            unexpected recipient may be. The guard fails closed — if any current
            recipient cannot be read, the delete is refused with
            ``DRAFT_DELETE_RECIPIENTS_UNREADABLE`` rather than counting the
            unreadable one as absent.

    Returns:
        Formatted output based on action. For action="list" each draft now reports
        its message id, To recipients, and a short body snippet so the list is
        directly triageable; verify full threading with `get_email_by_id`.
    """

    if action in {"send", "open", "delete"} and not draft_id and draft_subject:
        return target_selector_deprecated_error(
            "manage_drafts",
            ("draft_subject",),
            preferred="Call manage_drafts(action='list') or manage_drafts(action='find') first, then pass draft_id.",
            discovery="manage_drafts(action='list', subject_contains=...) or manage_drafts(action='find', in_reply_to=...)",
            exact_selector="draft_id",
        )

    delete_identity_supplied = any(value is not None for value in (expected_in_reply_to, expected_subject, expected_to))
    if (
        action == "delete"
        and delete_identity_supplied
        and not all(value and value.strip() for value in (expected_in_reply_to, expected_subject, expected_to))
    ):
        return serialize_tool_error(
            ToolError(
                code="DRAFT_DELETE_IDENTITY_INCOMPLETE",
                message=(
                    "Guarded draft deletion requires expected_in_reply_to, expected_subject, and expected_to together. "
                    "No draft was deleted."
                ),
                remediation={
                    "preferred": "Re-resolve with manage_drafts(action='find', in_reply_to=...) and pass the current subject and recipients.",
                },
            )
        )

    account, account_error = _resolve_account(account, timeout=timeout)
    if account_error:
        return account_error
    assert account is not None  # _resolve_account guarantees non-None when error is None

    body = _strip_cdata_wrappers(body)

    # Escape account for all paths
    safe_account = escape_applescript(account)
    list_limit = DRAFT_LIST_CAP if limit is None else max(1, min(int(limit), DRAFT_LIST_CAP))

    def _draft_action_lookup() -> tuple[str, str, str] | tuple[None, str, None]:
        if draft_id:
            normalized_ids = normalize_message_ids([draft_id])
            if not normalized_ids:
                return None, "Error: 'draft_id' must be a numeric Mail Drafts message id", None
            numeric_id = normalized_ids[0]
            return (
                f"""
                set foundDraft to missing value
                set targetDrafts to every message of draftsMailbox whose id is {numeric_id}
                if (count of targetDrafts) > 0 then
                    set foundDraft to item 1 of targetDrafts
                end if
                """,
                f"draft_id={numeric_id}",
                f"No draft found for draft_id={numeric_id}",
            )
        return (
            None,
            "Error: draft_id is required for this draft action "
            "(discover via manage_drafts(action='list') or action='find')",
            None,
        )

    if action == "list":
        script = _build_manage_drafts_list_script(
            safe_account=safe_account,
            list_limit=list_limit,
            hide_empty=hide_empty,
            subject_contains=subject_contains,
        )

    elif action == "find":
        if not in_reply_to:
            return "Error: 'in_reply_to' is required for manage_drafts(action='find')"
        # The script matches on `contains <value>` after stripping angle
        # brackets and spaces, and AppleScript's `contains ""` is false for
        # every string -- so "<>" or "   " survives the truthiness check above
        # and then matches no draft at all. The caller asked "does a reply
        # draft already exist for this message?" and would read that as a
        # confident no, whose usual next step is composing a duplicate. The
        # sibling delete path already refuses the same degenerate value.
        if not in_reply_to.strip("<> "):
            return (
                "Error: 'in_reply_to' must contain a Message-ID, not just angle brackets or whitespace. "
                "No drafts were searched."
            )
        script = _build_manage_drafts_find_script(
            safe_account=safe_account,
            list_limit=list_limit,
            in_reply_to=in_reply_to,
            subject_contains=subject_contains,
        )

    elif action == "create":
        if not subject or not to or not body:
            return "Error: 'subject', 'to', and 'body' are required for creating drafts"

        if in_reply_to:
            return serialize_tool_error(
                ToolError(
                    code="CREATE_CANNOT_THREAD",
                    message=(
                        'manage_drafts(action="create") builds a standalone new message and cannot set '
                        "In-Reply-To/References headers, so it cannot thread a reply. The in_reply_to "
                        'parameter is only honored by action="find" (draft lookup), never by create. '
                        "No draft was created."
                    ),
                    remediation={
                        "preferred": (
                            "To reply into an existing thread, use reply_to_email(message_id=...) after "
                            "locating the source message with search_emails(...) or list_inbox_emails(...)."
                        ),
                        "find_existing": (
                            "To locate an already-saved reply draft by its source Message-ID, use "
                            'manage_drafts(action="find", in_reply_to=...).'
                        ),
                        "standalone": "If you truly want a new standalone draft, omit in_reply_to.",
                        "in_reply_to": in_reply_to,
                    },
                )
            )

        thread_warning = _standalone_compose_thread_warning(
            subject, body, None, standalone_confirmed, tool_name='manage_drafts(action="create")'
        )
        if thread_warning:
            return thread_warning

        try:
            sender_override, sender_error = _validate_from_address(account, from_address, timeout=timeout)
        except AppleScriptTimeout:
            return (
                "Error: AppleScript timed out while validating sender for account "
                f"{account!r}. Try again or pass a larger `timeout`."
            )
        if sender_error:
            return sender_error

        escaped_subject = escape_applescript(subject)
        escaped_body = escape_applescript(body)
        safe_to = escape_applescript(to)

        sender_script = _compose_sender_script("newDraft", "targetAccount", sender_override)

        # Build TO recipients (split comma-separated)
        to_script = ""
        to_addresses = [addr.strip() for addr in to.split(",")]
        for addr in to_addresses:
            safe_addr = escape_applescript(addr)
            to_script += f'''
                    make new to recipient at end of to recipients with properties {{address:"{safe_addr}"}}
            '''

        # Build CC recipients if provided
        cc_script = ""
        if cc:
            cc_addresses = [addr.strip() for addr in cc.split(",")]
            for addr in cc_addresses:
                safe_addr = escape_applescript(addr)
                cc_script += f'''
                    make new cc recipient at end of cc recipients with properties {{address:"{safe_addr}"}}
                '''

        # Build BCC recipients if provided
        bcc_script = ""
        if bcc:
            bcc_addresses = [addr.strip() for addr in bcc.split(",")]
            for addr in bcc_addresses:
                safe_addr = escape_applescript(addr)
                bcc_script += f'''
                    make new bcc recipient at end of bcc recipients with properties {{address:"{safe_addr}"}}
                '''

        script = f'''
        tell application "Mail"
            set outputText to "CREATING DRAFT" & return & return

            try
                set targetAccount to account "{safe_account}"

                -- Create new outgoing message (draft)
                set newDraft to make new outgoing message with properties {{subject:"{escaped_subject}", content:"{escaped_body}", visible:false}}

                {sender_script}

                -- Add recipients
                tell newDraft
                    {to_script}
                    {cc_script}
                    {bcc_script}
                end tell

                save newDraft
                delay 0.5
                set draftId to ""
                try
                    set draftId to id of newDraft as string
                end try

                set outputText to outputText & "✓ Draft created successfully!" & return & return
                set outputText to outputText & "Subject: {escaped_subject}" & return
                set outputText to outputText & "To: {safe_to}" & return
                if draftId is not "" then set outputText to outputText & "Draft ID: " & draftId & return

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    elif action == "send":
        if compose._server.READ_ONLY:
            return "Error: Sending drafts is disabled in read-only mode."
        if compose._server.DRAFT_SAFE:
            return "Error: Sending drafts is disabled in draft-safe mode."
        lookup_script, _draft_label, not_found_text = _draft_action_lookup()
        if lookup_script is None:
            return _draft_label

        script = f'''
        tell application "Mail"
            set outputText to "SENDING DRAFT" & return & return

            try
                set targetAccount to account "{safe_account}"
                set draftsMailbox to mailbox "Drafts" of targetAccount
                {lookup_script}

                if foundDraft is not missing value then
                    set draftSubject to subject of foundDraft
                    set draftId to id of foundDraft as string

                    -- Send the draft
                    send foundDraft

                    set outputText to outputText & "✓ Draft sent successfully!" & return
                    set outputText to outputText & "Subject: " & draftSubject & return
                    set outputText to outputText & "Draft ID: " & draftId & return

                else
                    set outputText to outputText & "⚠ {not_found_text}" & return
                end if

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    elif action == "open":
        lookup_script, _draft_label, not_found_text = _draft_action_lookup()
        if lookup_script is None:
            return _draft_label

        script = f'''
        tell application "Mail"
            set outputText to "OPENING DRAFT FOR REVIEW" & return & return

            try
                set targetAccount to account "{safe_account}"
                set draftsMailbox to mailbox "Drafts" of targetAccount
                {lookup_script}

                if foundDraft is not missing value then
                    set draftSubject to subject of foundDraft
                    set draftId to id of foundDraft as string

                    -- Open the draft in a visible compose window
                    set draftWindow to open foundDraft
                    activate

                    set outputText to outputText & "✓ Draft opened in Mail for review!" & return
                    set outputText to outputText & "Subject: " & draftSubject & return
                    set outputText to outputText & "Draft ID: " & draftId & return
                    set outputText to outputText & return & "Edit and send when ready." & return

                else
                    set outputText to outputText & "⚠ {not_found_text}" & return
                end if

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    elif action == "delete":
        lookup_script, _draft_label, not_found_text = _draft_action_lookup()
        if lookup_script is None:
            return _draft_label

        guarded_delete_script = ""
        if delete_identity_supplied:
            assert expected_in_reply_to is not None
            assert expected_subject is not None
            assert expected_to is not None
            normalized_expected_in_reply_to = expected_in_reply_to.strip("<> ")
            if not normalized_expected_in_reply_to:
                return serialize_tool_error(
                    ToolError(
                        code="DRAFT_DELETE_IDENTITY_INCOMPLETE",
                        message="Guarded draft deletion requires a non-empty expected In-Reply-To value. No draft was deleted.",
                        remediation={
                            "preferred": "Pass the source Internet Message-ID returned by the resolved reply draft."
                        },
                    )
                )
            expected_to_literal = _expected_recipient_literal(expected_to)
            if expected_to_literal is None:
                return serialize_tool_error(
                    ToolError(
                        code="DRAFT_DELETE_IDENTITY_INCOMPLETE",
                        message="Guarded draft deletion requires at least one expected recipient. No draft was deleted.",
                        remediation={"preferred": "Pass the exact current To recipients from the resolved draft."},
                    )
                )
            safe_expected_in_reply_to = escape_applescript(normalized_expected_in_reply_to)
            safe_expected_subject = escape_applescript(expected_subject)
            headers_script = thread_headers_block(
                message_var="foundDraft",
                in_reply_to_var="currentInReplyTo",
                references_var="currentReferences",
                sanitize_fn=None,
                include_on_error=True,
            )
            guarded_delete_script = f'''
                    -- Exchange and IMAP Drafts can reassign numeric ids during sync.
                    -- Revalidate the durable thread header, subject, and recipient
                    -- set after the id lookup and immediately before deletion.
                    set deleteIdentityMatches to true
                    if (subject of foundDraft as string) is not "{safe_expected_subject}" then set deleteIdentityMatches to false
                    {headers_script}
                    set expectedRfcToken to "<{safe_expected_in_reply_to}>"
                    if currentInReplyTo does not contain expectedRfcToken and currentReferences does not contain expectedRfcToken then set deleteIdentityMatches to false
                    -- Every current To recipient must be READ before the two
                    -- membership checks below mean anything. The reverse check
                    -- ("no actual recipient outside the expected set") is what
                    -- catches a drifted draft carrying an EXTRA recipient, and
                    -- a recipient that cannot be read is exactly what such a
                    -- draft would plausibly carry. Silently skipping it made an
                    -- unreadable recipient indistinguishable from "no extra
                    -- recipient present" — a guard weakening on a destructive
                    -- path. Both arms below therefore fail closed: an
                    -- unreadable recipient aborts the delete instead of
                    -- satisfying the check by absence.
                    set actualToAddresses to {{}}
                    set recipientReadFailure to ""
                    try
                        repeat with aRecipient in (to recipients of foundDraft)
                            try
                                set end of actualToAddresses to (address of aRecipient as string)
                            on error recipientErrMsg
                                set recipientReadFailure to "recipient address unreadable: " & (recipientErrMsg as string)
                            end try
                        end repeat
                    on error recipientListErrMsg
                        set recipientReadFailure to "recipient list unreadable: " & (recipientListErrMsg as string)
                    end try
                    if recipientReadFailure is not "" then return "DRAFT_DELETE_RECIPIENTS_UNREADABLE|||" & draftId & "|||" & recipientReadFailure
                    set expectedToAddresses to {expected_to_literal}
                    repeat with expectedToAddress in expectedToAddresses
                        set expectedRecipientFound to false
                        repeat with actualToAddress in actualToAddresses
                            ignoring case
                                if (actualToAddress as string) is (expectedToAddress as string) then set expectedRecipientFound to true
                            end ignoring
                        end repeat
                        if not expectedRecipientFound then set deleteIdentityMatches to false
                    end repeat
                    repeat with actualToAddress in actualToAddresses
                        set actualRecipientExpected to false
                        repeat with expectedToAddress in expectedToAddresses
                            ignoring case
                                if (actualToAddress as string) is (expectedToAddress as string) then set actualRecipientExpected to true
                            end ignoring
                        end repeat
                        if not actualRecipientExpected then set deleteIdentityMatches to false
                    end repeat
                    if not deleteIdentityMatches then return "DRAFT_DELETE_IDENTITY_DRIFT|||" & draftId
            '''

        script = f'''
        tell application "Mail"
            set outputText to "DELETING DRAFT" & return & return

            try
                set targetAccount to account "{safe_account}"
                set draftsMailbox to mailbox "Drafts" of targetAccount
                {lookup_script}

                if foundDraft is not missing value then
                    set draftSubject to subject of foundDraft
                    set draftId to id of foundDraft as string

                    {guarded_delete_script}

                    -- Delete the draft
                    delete foundDraft

                    set outputText to outputText & "✓ Draft deleted successfully!" & return
                    set outputText to outputText & "Subject: " & draftSubject & return
                    set outputText to outputText & "Draft ID: " & draftId & return

                else
                    set outputText to outputText & "⚠ {not_found_text}" & return
                end if

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    elif action == "cleanup_empty":
        if max_deletes < 1:
            return "Error: 'max_deletes' must be >= 1 for cleanup_empty"
        script = _build_manage_drafts_cleanup_script(
            safe_account=safe_account,
            dry_run=dry_run,
            max_deletes=max_deletes,
        )

    else:
        return f"Error: Invalid action '{action}'. Use: list, find, create, send, open, delete, cleanup_empty"

    try:
        result = (
            compose.run_applescript(script) if timeout is None else compose.run_applescript(script, timeout=timeout)
        )
    except AppleScriptTimeout:
        return (
            f"Error: AppleScript timed out for manage_drafts action {action!r} on "
            f"account {account!r}. Try again or pass a larger `timeout`."
        )
    if action == "delete" and result.strip().startswith("DRAFT_DELETE_RECIPIENTS_UNREADABLE|||"):
        parts = result.strip().split("|||", 2)
        current_id = parts[1]
        detail = parts[2] if len(parts) > 2 else "unknown AppleScript error"
        return serialize_tool_error(
            ToolError(
                code="DRAFT_DELETE_RECIPIENTS_UNREADABLE",
                message=(
                    "At least one current To recipient of the draft could not be read, so the identity guard "
                    "cannot prove the draft carries no unexpected recipient; no draft was deleted."
                ),
                remediation={
                    "draft_id": current_id,
                    "applescript_error": detail,
                    "preferred": "Re-run manage_drafts(action='find', in_reply_to=...) and inspect the draft's current recipients before deleting.",
                    "note": "An unreadable recipient is not evidence of absence: the guarded delete fails closed rather than treating it as 'no extra recipient present'.",
                },
            )
        )
    if action == "delete" and result.strip().startswith("DRAFT_DELETE_IDENTITY_DRIFT|||"):
        current_id = result.strip().split("|||", 1)[1]
        return serialize_tool_error(
            ToolError(
                code="DRAFT_DELETE_IDENTITY_DRIFT",
                message=(
                    "Draft identity no longer matches the durable threading, subject, and recipient guard; no draft was deleted."
                ),
                remediation={
                    "draft_id": current_id,
                    "preferred": "Re-run manage_drafts(action='find', in_reply_to=...) and inspect the current draft before deleting.",
                },
            )
        )
    return result
