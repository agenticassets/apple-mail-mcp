"""Composition tools: sending, replying, forwarding, and drafts."""

import json
import os
import subprocess
import tempfile
import time
from contextlib import suppress
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from apple_mail_mcp import server  # public alias used by tests
from apple_mail_mcp import server as _server
from apple_mail_mcp.applescript_snippets import (
    recipient_addresses_block,
    sanitize_field_handler,
    text_offset_handler,
    thread_headers_block,
)
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error, target_selector_deprecated_error
from apple_mail_mcp.bounded_scan import MAX_WHOSE_IDS, iter_id_chunks
from apple_mail_mcp.core import (
    SENSITIVE_DIRS,
    AppleScriptTimeout,
    escape_applescript,
    inject_preferences,
    normalize_message_ids,
    run_applescript,
    validate_account_name,
)
from apple_mail_mcp.server import DESTRUCTIVE_TOOL_ANNOTATIONS, READ_ONLY_TOOL_ANNOTATIONS, WRITE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools.compose.cleanup import delete_draft_if_identity_matches

# Pure helpers split into leaf siblings; re-imported here so every symbol stays
# importable as ``apple_mail_mcp.tools.compose.<name>`` (cli.py + tests rely on
# this attribute surface, and the @mcp.tool tools below call them). compose.py
# has a file-wide F401 ignore for these re-exports.
from apple_mail_mcp.tools.compose.constants import (
    _MESSAGE_ID_REQUIRED_ERROR,
    DRAFT_LIST_CAP,
    MAX_OPEN_COMPOSE_WINDOWS,
    MESSAGE_LOOKUP_CAP,
    TYPING_CHUNK_SIZE,
    TYPING_INTER_CHUNK_DELAY,
)
from apple_mail_mcp.tools.compose.drafts_scripts import (
    _build_manage_drafts_find_script,
    _build_manage_drafts_list_script,
    _build_manage_drafts_subject_filter_script,
    _indent_applescript_block,
)
from apple_mail_mcp.tools.compose.forward import forward_email

# Re-export moved helpers and tools so the historical apple_mail_mcp.tools.compose.<name>
# attribute surface (cli.py + tests) is preserved and @mcp.tool registration runs once.
from apple_mail_mcp.tools.compose.helpers import (
    _account_default_alias_if_single,
    _check_open_compose_window_cap,
    _clean_applescript_error,
    _count_open_outgoing_messages,
    _list_outgoing_message_ids,
    _resolve_account,
    _resolve_signature_name,
    _save_new_compose_window_as_draft,
    _send_blocked,
    _validate_from_address,
    _validate_signature_name,
)
from apple_mail_mcp.tools.compose.lookup_scripts import (
    _applescript_id_list_literal,
    _build_draft_lookup,
    _build_found_message_lookup,
    _compose_signature_script,
)
from apple_mail_mcp.tools.compose.manage import manage_drafts
from apple_mail_mcp.tools.compose.payload import (
    _CDATA_BLOCK_PATTERN,
    _QUOTED_THREAD_MARKERS_RE,
    _THREADED_SUBJECT_RE,
    _build_html_from_text,
    _build_recipient_loops,
    _compose_sender_script,
    _default_rich_draft_path,
    _prepare_rich_bodies,
    _safe_eml_name,
    _split_addresses,
    _standalone_compose_thread_warning,
    _strip_cdata_wrappers,
    _validate_attachment_paths,
)
from apple_mail_mcp.tools.compose.reply import reply_to_email
from apple_mail_mcp.tools.compose.reply_runner import (
    _delete_reply_artifact,
    _native_reply_abort_response,
    _native_reply_effective_timeout,
    _unrecognized_reply_output_response,
)
from apple_mail_mcp.tools.compose.reply_script_helpers import (
    _reply_command_options,
    _reply_extra_output_lines,
    _reply_mode_plan,
    _reply_signature_script,
    _ReplyModePlan,
)
from apple_mail_mcp.tools.compose.reply_scripts import (
    _build_reply_native_window_applescript,
    _build_reply_objectmodel_applescript,
    _native_reply_post_action,
)
from apple_mail_mcp.tools.compose.reply_subject_scripts import native_reply_subject_helpers_applescript
from apple_mail_mcp.tools.compose.rich_draft import create_rich_email_draft
from apple_mail_mcp.tools.compose.saved_draft_checks import _verify_saved_forward_draft, _verify_saved_reply_draft
from apple_mail_mcp.tools.compose.send import _send_html_email, compose_email
from apple_mail_mcp.tools.compose.typing_scripts import build_chunked_typing_handler
from apple_mail_mcp.tools.compose.verification import (
    _extract_output_field,
    _first_non_empty_line,
    _format_forward_verification_lines,
    _format_reply_verification_lines,
    _reply_attachment_details_requested,
    _reply_body_mismatch_error,
    _reply_draft_verification_error,
    _reply_exact_id_verified,
    _reply_success_payload,
    _reply_verification_failure_response,
    _reply_verification_from_output,
    _ReplyDraftVerification,
)
from apple_mail_mcp.tools.compose.verify_tools import verify_draft, verify_drafts
from apple_mail_mcp.tools.draft_verification import (
    _build_verify_draft_payload,
    _parse_expected_attachments,
    _split_csv_addresses,
)

__all__ = [
    "Any",
    "AppleScriptTimeout",
    "DESTRUCTIVE_TOOL_ANNOTATIONS",
    "DRAFT_LIST_CAP",
    "EmailMessage",
    "MAX_OPEN_COMPOSE_WINDOWS",
    "MAX_WHOSE_IDS",
    "MESSAGE_LOOKUP_CAP",
    "Path",
    "READ_ONLY_TOOL_ANNOTATIONS",
    "SENSITIVE_DIRS",
    "ToolError",
    "TYPING_CHUNK_SIZE",
    "TYPING_INTER_CHUNK_DELAY",
    "WRITE_TOOL_ANNOTATIONS",
    "_CDATA_BLOCK_PATTERN",
    "_MESSAGE_ID_REQUIRED_ERROR",
    "_QUOTED_THREAD_MARKERS_RE",
    "_ReplyDraftVerification",
    "_ReplyModePlan",
    "_THREADED_SUBJECT_RE",
    "_account_default_alias_if_single",
    "_applescript_id_list_literal",
    "_build_draft_lookup",
    "_build_found_message_lookup",
    "_build_html_from_text",
    "_build_manage_drafts_find_script",
    "_build_manage_drafts_list_script",
    "_build_manage_drafts_subject_filter_script",
    "_build_recipient_loops",
    "_build_reply_native_window_applescript",
    "_build_reply_objectmodel_applescript",
    "_build_verify_draft_payload",
    "_check_open_compose_window_cap",
    "_clean_applescript_error",
    "_compose_sender_script",
    "_compose_signature_script",
    "_count_open_outgoing_messages",
    "_default_rich_draft_path",
    "_delete_reply_artifact",
    "_extract_output_field",
    "_first_non_empty_line",
    "_format_forward_verification_lines",
    "_format_reply_verification_lines",
    "_indent_applescript_block",
    "_list_outgoing_message_ids",
    "_native_reply_abort_response",
    "_native_reply_effective_timeout",
    "_native_reply_post_action",
    "native_reply_subject_helpers_applescript",
    "_parse_expected_attachments",
    "_prepare_rich_bodies",
    "_reply_attachment_details_requested",
    "_reply_body_mismatch_error",
    "_reply_command_options",
    "_reply_draft_verification_error",
    "_reply_exact_id_verified",
    "_reply_extra_output_lines",
    "_reply_mode_plan",
    "_reply_signature_script",
    "_reply_success_payload",
    "_reply_verification_failure_response",
    "_reply_verification_from_output",
    "_resolve_account",
    "_resolve_signature_name",
    "_safe_eml_name",
    "_save_new_compose_window_as_draft",
    "_send_blocked",
    "_send_html_email",
    "_server",
    "_split_addresses",
    "_split_csv_addresses",
    "_standalone_compose_thread_warning",
    "_strip_cdata_wrappers",
    "_unrecognized_reply_output_response",
    "_validate_attachment_paths",
    "_validate_from_address",
    "_validate_signature_name",
    "_verify_saved_forward_draft",
    "_verify_saved_reply_draft",
    "build_chunked_typing_handler",
    "compose_email",
    "create_rich_email_draft",
    "delete_draft_if_identity_matches",
    "escape_applescript",
    "forward_email",
    "inject_preferences",
    "iter_id_chunks",
    "json",
    "manage_drafts",
    "mcp",
    "normalize_message_ids",
    "os",
    "recipient_addresses_block",
    "reply_to_email",
    "run_applescript",
    "sanitize_field_handler",
    "serialize_tool_error",
    "server",
    "subprocess",
    "suppress",
    "target_selector_deprecated_error",
    "tempfile",
    "text_offset_handler",
    "thread_headers_block",
    "time",
    "validate_account_name",
    "verify_draft",
    "verify_drafts",
]
