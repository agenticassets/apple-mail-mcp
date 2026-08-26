"""Core helpers: AppleScript execution, escaping, parsing, and preference injection.

Linker/facade for the ``apple_mail_mcp.core`` package. The dotted path
``apple_mail_mcp.core`` still resolves (now to this ``__init__``), so every
``from apple_mail_mcp.core import <name>`` importer stays unchanged. Each moved
symbol is re-exported here and listed in ``__all__`` so it remains a valid
``apple_mail_mcp.core.<name>`` attribute (the test patch surface) and mypy
``--strict`` no-implicit-reexport stays clean.

``import os`` is kept at this level so ``apple_mail_mcp.core.os.path.expanduser``
remains patchable by the save-path tests.
"""

import logging
import os

logger = logging.getLogger(__name__)

from apple_mail_mcp.core.applescript import (
    AppleScriptRunner,
    AppleScriptTimeout,
    run_applescript,
)
from apple_mail_mcp.core.escaping import (
    _CONTROL_CHARS_RE,
    _sanitize_for_json,
    escape_applescript,
    sanitize_pipe_delimited_field,
)
from apple_mail_mcp.core.normalization import (
    contains_any_condition,
    equals_any_numeric_condition,
    normalize_message_ids,
    normalize_search_terms,
    parse_email_list,
)
from apple_mail_mcp.core.preferences import P, R, inject_preferences
from apple_mail_mcp.core.replied import (
    REPLIED_HEADER_READ_CAP,
    SentReplySnapshot,
    build_sent_reply_snapshot_script,
    fetch_replied_ids,
    fetch_replied_ids_script,
    fetch_sent_reply_snapshot,
    replied_ids_script,
    sent_mailbox_resolve_script,
)
from apple_mail_mcp.core.script_fragments import (
    INBOX_NAMES,
    build_date_filter,
    build_email_fields_script,
    build_filter_condition,
    build_mailbox_ref,
    content_preview_script,
    date_cutoff_script,
    inbox_mailbox_script,
    skip_folders_condition,
)
from apple_mail_mcp.core.validation import (
    SENSITIVE_DIRS,
    account_not_found_json,
    list_mail_account_names,
    reject_unknown_account,
    validate_account_name,
    validate_save_path,
)

__all__ = [
    "INBOX_NAMES",
    "REPLIED_HEADER_READ_CAP",
    "SentReplySnapshot",
    "SENSITIVE_DIRS",
    "AppleScriptRunner",
    "AppleScriptTimeout",
    "P",
    "R",
    "_CONTROL_CHARS_RE",
    "_sanitize_for_json",
    "account_not_found_json",
    "build_date_filter",
    "build_email_fields_script",
    "build_filter_condition",
    "build_sent_reply_snapshot_script",
    "build_mailbox_ref",
    "contains_any_condition",
    "content_preview_script",
    "date_cutoff_script",
    "equals_any_numeric_condition",
    "escape_applescript",
    "fetch_replied_ids",
    "fetch_replied_ids_script",
    "fetch_sent_reply_snapshot",
    "inbox_mailbox_script",
    "inject_preferences",
    "list_mail_account_names",
    "logger",
    "normalize_message_ids",
    "normalize_search_terms",
    "os",
    "parse_email_list",
    "reject_unknown_account",
    "replied_ids_script",
    "run_applescript",
    "sanitize_pipe_delimited_field",
    "sent_mailbox_resolve_script",
    "skip_folders_condition",
    "validate_account_name",
    "validate_save_path",
]
