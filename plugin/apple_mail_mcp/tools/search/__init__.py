"""Search tools: finding and filtering emails.

Linker/facade for the ``search`` package. IO/core/server imports come first so
the patched seams (``run_applescript``, ``validate_account_name``,
``account_not_found_json``, ``asyncio``) resolve as ``apple_mail_mcp.tools.search.<name>``
attributes for the test suite; the submodules are then imported (which
registers the four ``@mcp.tool`` tools exactly once); ``__all__`` re-exports
every moved symbol so mypy --strict no-implicit-reexport is clean and the
historical ``apple_mail_mcp.tools.search.<name>`` attribute surface (cli.py +
tests) is preserved.
"""

import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from apple_mail_mcp import server as _server
from apple_mail_mcp.applescript_snippets import recipient_addresses_block, sanitize_field_handler, thread_headers_block
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.bounded_scan import MAX_WHOSE_IDS, build_whose_id_list, compute_scan_upper_bound, iter_id_chunks
from apple_mail_mcp.constants import SCAN_BOUNDS, THREAD_PREFIXES
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    account_not_found_json,
    build_mailbox_ref,
    escape_applescript,
    inject_preferences,
    list_mail_account_names,
    normalize_message_ids,
    normalize_search_terms,
    run_applescript,
    validate_account_name,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp

# Re-export moved helpers and tools so the historical apple_mail_mcp.tools.search.<name>
# attribute surface (cli.py + tests) is preserved and @mcp.tool registration runs once.
from apple_mail_mcp.tools.search.by_id import (
    _fetch_email_record_by_id,
    _fetch_email_records_by_ids,
    get_email_by_id,
    get_email_by_ids,
)
from apple_mail_mcp.tools.search.dispatch import (
    _list_mail_accounts,
    _search_mail_records,
    _search_mail_records_sync,
    _search_one_account,
)
from apple_mail_mcp.tools.search.emails import search_emails
from apple_mail_mcp.tools.search.records import (
    _ERROR_MAILBOX_PREFIX,
    BODY_TEXT_SEARCH_HINT,
    CONTENT_PREVIEW_SEARCH_HINT,
    MONTH_NAMES,
    SENDER_ONLY_SEARCH_HINT,
    _body_scan_disabled_error,
    _build_applescript_date,
    _build_search_response,
    _format_search_records_text,
    _parse_search_records,
    _search_error_detail,
    _sort_search_records,
)
from apple_mail_mcp.tools.search.script import _build_search_script, _list_accounts_script
from apple_mail_mcp.tools.search.thread import (
    _HEADER_MESSAGE_ID_RE,
    _applescript_string_list,
    _extract_thread_header_tokens,
    _normalize_thread_header_id,
    _thread_mailbox_script,
    _thread_strip_prefixes_handler,
    get_email_thread,
)

__all__ = [
    "Any",
    "AppleScriptTimeout",
    "BODY_TEXT_SEARCH_HINT",
    "CONTENT_PREVIEW_SEARCH_HINT",
    "MAX_WHOSE_IDS",
    "MONTH_NAMES",
    "READ_ONLY_TOOL_ANNOTATIONS",
    "SCAN_BOUNDS",
    "SENDER_ONLY_SEARCH_HINT",
    "THREAD_PREFIXES",
    "ToolError",
    "_ERROR_MAILBOX_PREFIX",
    "_HEADER_MESSAGE_ID_RE",
    "_applescript_string_list",
    "_body_scan_disabled_error",
    "_build_applescript_date",
    "_build_search_response",
    "_build_search_script",
    "_extract_thread_header_tokens",
    "_fetch_email_record_by_id",
    "_fetch_email_records_by_ids",
    "_format_search_records_text",
    "_list_accounts_script",
    "_list_mail_accounts",
    "_normalize_thread_header_id",
    "_parse_search_records",
    "_search_error_detail",
    "_search_mail_records",
    "_search_mail_records_sync",
    "_search_one_account",
    "_server",
    "_sort_search_records",
    "_thread_mailbox_script",
    "_thread_strip_prefixes_handler",
    "account_not_found_json",
    "asyncio",
    "build_mailbox_ref",
    "build_whose_id_list",
    "compute_scan_upper_bound",
    "datetime",
    "escape_applescript",
    "get_email_by_id",
    "get_email_by_ids",
    "get_email_thread",
    "inject_preferences",
    "iter_id_chunks",
    "json",
    "list_mail_account_names",
    "mcp",
    "normalize_message_ids",
    "normalize_search_terms",
    "quote",
    "re",
    "recipient_addresses_block",
    "run_applescript",
    "sanitize_field_handler",
    "search_emails",
    "serialize_tool_error",
    "thread_headers_block",
    "timedelta",
    "validate_account_name",
]
