#!/usr/bin/env python3
"""Compile-check every AppleScript a module emits, without executing any of it.

Catches parse-time syntax errors like the 3.3.0
``_build_awaiting_reply_inbox_script`` regression — which used
``header value of header named "X" of msg`` (not valid Mail.app dictionary
syntax) and failed with osascript ``-2740``. Existing unit tests passed
because they only asserted the row-format protocol the Python parser
consumes, not the AppleScript source itself.

Usage:
    python3 .claude/hooks/check_applescript_compiles.py <module_path> [...]
    echo '{"tool_input":{"file_path":"..."}}' | python3 .claude/hooks/...
    python3 .claude/hooks/check_applescript_compiles.py --report <module_path>

Only modules under ``plugin/apple_mail_mcp/`` are checked; anything else
exits 0 (nothing to check, and it would not import with this layout).

Discovery rule (two passes, both with every AppleScript executor stubbed
out, so nothing can reach Mail.app):

    1. Builders — a function named ``*_script`` / ``*_block`` / ``*_handler``
       / ``build*_applescript`` is called with synthesized arguments, and
       every returned string carrying a ``tell application "Mail"`` /
       ``"Calendar"`` block is piped to ``osacompile -o /dev/null``. A string
       with no ``tell`` block is a fragment that only compiles inside an
       enclosing ``tell``, and is recorded as one.
    2. Inline tools — a function that is not a builder but whose *source*
       carries a ``tell`` block builds its script inline, so the tool itself
       is called and the stub compiles whatever script it is handed.

Why this file is paranoid about silence
---------------------------------------
Until 2026-08 this hook exited 0 on the five biggest script modules in the
repo having compiled *zero* scripts: ``_import_module`` loaded each file
with ``spec_from_file_location`` under its package-qualified name, which
pre-registered a half-initialized module in ``sys.modules``; the package
``__init__`` then re-imported names from it and raised
``ImportError: cannot import name ...``. That was swallowed as a warning on
stderr — and ``post_edit_check.sh`` only surfaces stderr when the exit code
is non-zero, so the warning was invisible and exit 0 read as "checked and
clean". A gate that cannot fail is worth nothing, so every "could not
check" outcome below is either a non-zero exit or an entry in the
``UNCHECKABLE`` ledger, which ``tests/infra/test_applescript_compile_hook.py``
holds to the live tree.

Exit codes:
    0  checked (or legitimately nothing to check / ``osacompile`` absent)
    2  a compiled script failed, or a module could not be checked
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
PLUGIN_SRC = REPO / "plugin"
PACKAGE = "apple_mail_mcp"

FULL_SCRIPT_MARKERS = ('tell application "Mail"', 'tell application "Calendar"')

_SAMPLE_DATETIME = datetime(2026, 7, 10, 9, 30)

# ``core.validate_save_path`` rejects any path outside the home directory, and
# ``save_email_attachment`` runs that check *before* it builds its script — so a
# ``/tmp`` sample made the tool unreachable. Build the sample from
# ``Path.home()`` at run time: this repo is public and a literal
# ``/Users/<name>/...`` path is rejected by
# ``tools/validators/validate_no_committed_identity.py``. Nothing is ever
# created here; no script this hook synthesizes is executed.
_SAMPLE_SAVE_DIR = Path.home() / "Library" / "Caches" / "apple-mail-mcp-compile-check"

# Sample values for parameters that script builders take. Curated on purpose:
# many of these are spliced into the script *raw* (AppleScript statements or
# conditions, not quoted literals), so a generically synthesized ``"test"``
# would emit code the builder never really produces and could fail to compile
# for a reason the shipped tool does not have. Add a real, representative value
# here when a new builder introduces a new parameter name.
SAMPLE_KWARGS: dict[str, object] = {
    # Accounts / mailboxes
    "account": "Test Account",
    "escaped_account": "Test Account",
    "account_name": "Test Account",
    "safe_account": "Test Account",
    "account_ref": 'account "Test Account"',
    "account_var": "targetAccount",
    "mailbox": "INBOX",
    "mailboxes": ["INBOX", "Archive"],
    "mailbox_name": "INBOX",
    "safe_mailbox": "INBOX",
    "escaped_mailbox": "INBOX",
    "candidate_mailboxes": ["INBOX", "Archive"],
    # Bounds / caps
    "days_back": 7,
    "recent_days": 2.0,
    "inbox_cap": 10,
    "sent_cap": 20,
    "max_results": 5,
    "scan_cap": 100,
    "include_read": True,
    "var_name": "myVar",
    "replied_var": "repliedIds",
    # Search / filter inputs
    "subject_keyword": "test",
    "subject_terms": ["test"],
    "sender": "sender@example.com",
    "sender_exact": "sender@example.com",
    "sender_domain": "example.com",
    "sender_override": "sender@example.com",
    "safe_email_address": "sender@example.com",
    "body_text": "test",
    "read_status": "all",
    "read_filter": "unread",
    "date_from": "2026-01-01",
    "date_to": "2026-12-31",
    "internet_message_id": "<abc123@example.com>",
    "in_reply_to": "<abc123@example.com>",
    "subject_contains": "test",
    "newsletter_condition": "(false)",
    "sort": "newest_first",
    # Message identity
    "message_id": "12345",
    "message_ids": ["12345", "23456"],
    "numeric_id": "12345",
    "message_var": "aMessage",
    "output_var": "outputText",
    "variable": "newMessage",
    # Compose / draft
    "marker": "__apple_mail_mcp_compile_check__",
    "subject": "Compile check subject",
    "safe_subject": "Compile check subject",
    "final_subject": "Compile check subject",
    "safe_body_sentinel": "COMPILE_CHECK_SENTINEL",
    "expected_to_literal": '{"recipient@example.com"}',
    "html_temp_path": "/tmp/apple-mail-mcp-compile-check.html",
    "mode": "draft",
    "signature_name": "Default",
    "resolved_signature_name": "Default",
    "body_temp_path": "/tmp/apple-mail-mcp-compile-check.txt",
    "header_text": "Compile check header",
    "success_text": "OK",
    "not_found_message": "not found",
    "mailbox_lookup": "",
    "reply_options": "",
    "reply_settle_delay": "0.2",
    "post_action": "",
    "safe_cc": "cc@example.com",
    "safe_bcc": "bcc@example.com",
    "safe_attachment_info": "",
    # Fragment helpers (indentation, recipient/header blocks, scan fragments)
    "block": "",
    "indent": "    ",
    "spaces": 4,
    "recipient_kind": "to",
    "in_reply_to_var": "inReplyTo",
    "references_var": "refsValue",
    "list_var": "markedDrafts",
    "safe_marker": "__apple_mail_mcp_compile_check__",
    "subject_expr": '"Compile check subject"',
    "action_label": "marked",
    "dt": _SAMPLE_DATETIME,
    # Tool entry points reached by the inline-capture pass
    "name": "Compile Check Mailbox",
    "to": "recipient@example.com",
    "body": "Compile check body",
    "body_plain": "Compile check body",
    "body_html": "<p>Compile check body</p>",
    "from_address": "sender@example.com",
    "draft_id": "12345",
    # Fields of NativeReplyDraftIdentity, synthesized by _dataclass_sample. Its
    # ``draft_id`` field reads the same key as the ``draft_id`` parameter above,
    # which is what makes the two agree — ``_delete_reply_artifact`` returns
    # before building its script unless ``normalize_message_ids([draft_id])[0]``
    # equals ``identity.draft_id``, so a numeric, normalization-stable value is
    # load-bearing, not decorative.
    "draft_rfc_message_id": "<draft-12345@example.com>",
    "source_rfc_message_id": "<source-abc123@example.com>",
    "reply_subject": "Compile check subject",
    "reply_body": "Compile check body",
    "from_mailbox": "INBOX",
    "to_mailbox": "Archive",
    "dest_ref": 'mailbox "Archive" of targetAccount',
    "save_path": str(_SAMPLE_SAVE_DIR / "attachment.bin"),
    "attachment_name": "attachment.bin",
    "save_directory": str(_SAMPLE_SAVE_DIR),
    # Export
    "safe_format": "txt",
    "safe_save_dir": "/tmp/apple-mail-mcp-compile-check",
    # Calendar script builders (calendar_core/scripts_read.py, scripts_write.py)
    "calendar_id": "TEST-CALENDAR-ID",
    "calendar_name": "Test Calendar",
    "timeout_seconds": 30,
    "start_block": (
        "set windowStart to current date\n"
        "set time of windowStart to 0\n"
        "set day of windowStart to 1\n"
        "set year of windowStart to 2026\n"
        "set month of windowStart to 7\n"
        "set day of windowStart to 10\n"
        "set time of windowStart to 0"
    ),
    "end_block": (
        "set windowEnd to current date\n"
        "set time of windowEnd to 0\n"
        "set day of windowEnd to 1\n"
        "set year of windowEnd to 2026\n"
        "set month of windowEnd to 7\n"
        "set day of windowEnd to 17\n"
        "set time of windowEnd to 0"
    ),
    "uid_condition": 'uid is "TEST-UID"',
    "event_uid": "TEST-UID",
    "title": "Test Event",
    "new_name": "Renamed Calendar",
    "include_detail": False,
}

# Per-function overrides, for the rare parameter whose name means something
# different here than everywhere else. ``_html_post_paste_mail_block(body=...)``
# splices *AppleScript statements*, not message text, so the global "body"
# sample would emit a script that cannot compile — a failure the shipped code
# does not have.
FUNCTION_KWARG_OVERRIDES: dict[str, dict[str, object]] = {
    "_html_post_paste_mail_block": {"body": ""},
    # attachment_index selects within ONE message, so the tool refuses the
    # shared two-id ``message_ids`` sample with AMBIGUOUS_ATTACHMENT_SELECTOR
    # before it builds anything.
    "save_email_attachment": {"message_ids": ["12345"]},
    # Three coordinated values to reach the one script export_emails builds
    # itself: every other scope delegates to a ``*_script`` builder in
    # ``export_helpers.py`` that is compiled directly there, so "single_email"
    # is the only path with anything left to check here; a non-None
    # ``message_ids`` short-circuits into the id-export path first; and
    # ``include_attachments`` (True by default synthesis) is refused for any
    # format but "eml" -- which is also the variant that splices in the
    # attachment-bundle blocks. "scope" also names a statistics scope
    # elsewhere, which is why it needs a value here at all.
    "export_emails": {"scope": "single_email", "message_ids": None, "format": "eml"},
}

# Parameters carrying a spliced AppleScript fragment rather than a value. An
# empty fragment is always legal at a splice site, so these synthesize to "".
FRAGMENT_PARAM_NAMES = frozenset({"script"})
FRAGMENT_PARAM_SUFFIXES = ("_script", "_block", "_filter", "_setup", "_lines", "_handler")

# Function-name suffixes that mark a *builder* — a pure function returning
# AppleScript source. Everything else holding a ``tell`` block is a tool body
# whose script is built inline and can only be reached by calling the tool.
BUILDER_NAME_SUFFIXES = ("_script", "_handler", "_handlers", "_block", "_fragment")


def _is_builder_name(name: str) -> bool:
    """True for a pure AppleScript-source builder, by naming convention.

    ``*_applescript`` only counts for a ``build``-prefixed name: the suffix
    also ends ``escape_applescript``, which escapes a value and builds nothing.
    """
    if name.endswith(BUILDER_NAME_SUFFIXES):
        return True
    return name.endswith("_applescript") and "build" in name


# Modules whose AppleScript this hook structurally cannot reach: the script is
# built inline inside a tool body (an f-string local, not a callable
# ``*_script`` builder), so there is nothing to call without executing the tool
# itself — which this hook must never do. These are real coverage debt, not
# clean bills of health. Extracting a ``*_script`` builder is one fix; the
# other is a curated ``SAMPLE_KWARGS`` / ``FUNCTION_KWARG_OVERRIDES`` entry
# that gets the tool past its own pre-script validation. Each removal from this
# ledger is a permanent gain, enforced by
# tests/infra/test_applescript_compile_hook.py. NOTHING may be added here
# without also adding the reason, and a module that is *not* listed and cannot
# be checked exits non-zero instead.
#
# Empty is the goal state, and it is currently empty: the last three entries
# (analytics/export.py, compose/reply_runner.py, manage/attachments.py) all
# turned out to be reachable once their tools were handed arguments they
# accept. Do not read emptiness as "the ledger is unused" — an unreachable
# module that is not listed here blocks, which is what keeps this dict honest.
UNCHECKABLE: dict[str, str] = {}


class _ExecutorInvoked(BaseException):
    """Raised by the stub that replaces every AppleScript executor.

    Derived from ``BaseException`` on purpose: tool bodies wrap their
    ``run_applescript`` calls in ``except Exception`` retry/fallback handlers,
    and an ``Exception`` sentinel would be swallowed there and loop.
    """


_CAPTURED_SCRIPTS: list[str] = []


def _executor_stub(*args: Any, **kwargs: Any) -> Any:
    script = kwargs.get("script", args[0] if args else None)
    if isinstance(script, str):
        _CAPTURED_SCRIPTS.append(script)
    raise _ExecutorInvoked()


def _install_execution_guards() -> None:
    """Neutralize every AppleScript seam in the imported package.

    Two jobs. First, safety: a ``*_script`` name is not proof of a pure
    builder — ``_run_trash_script`` *executes* one — so stubbing every executor
    seam before calling anything means a misclassified function can only raise
    ``_ExecutorInvoked``, never drive Mail.app. Second, capture: the stub
    records the script it was handed, which is the only way to compile-check
    the scripts that are built inline inside a tool body instead of in a
    callable ``*_script`` builder.

    Account validation is neutralized too, because it runs AppleScript before
    the tool ever builds its own script and would otherwise abort the capture
    with an "account not found" return.

    This mutates the imported package process-wide. Run it only in a dedicated
    process — tests must invoke this hook as a subprocess, never in-process.
    """
    modules = [m for n, m in list(sys.modules.items()) if n == PACKAGE or n.startswith(PACKAGE + ".")]
    executor_attrs = ("run_applescript", "run_applescript_async")

    # Snapshot the real executors *before* patching: helpers such as
    # ``build_correspondent_export_script``'s caller take ``runner=run_applescript``
    # as a default argument, which binds the real function object at def time
    # and would sail straight past a module-attribute stub — and actually drive
    # Mail.app from an edit hook. Defaults get rewritten below.
    originals = {
        id(getattr(module, attr))
        for module in modules
        for attr in executor_attrs
        if getattr(module, attr, None) is not None
    }
    replacements: dict[str, Any] = {
        "run_applescript": _executor_stub,
        "run_applescript_async": _executor_stub,
        "validate_account_name": lambda *_a, **_k: None,
        "list_mail_account_names": lambda *_a, **_k: [SAMPLE_KWARGS["account"]],
    }
    for module in modules:
        for attr, stub in replacements.items():
            if getattr(module, attr, None) is not None:
                with suppress(Exception):  # pragma: no cover - read-only module attr
                    setattr(module, attr, stub)
    for module in modules:
        for _name, fn in inspect.getmembers(module, inspect.isfunction):
            defaults = fn.__defaults__
            if defaults and any(id(d) in originals for d in defaults):
                fn.__defaults__ = tuple(_executor_stub if id(d) in originals else d for d in defaults)
            kwdefaults = fn.__kwdefaults__
            if kwdefaults and any(id(d) in originals for d in kwdefaults.values()):
                fn.__kwdefaults__ = {k: (_executor_stub if id(v) in originals else v) for k, v in kwdefaults.items()}


def _module_name_for(module_path: Path) -> str:
    rel = module_path.resolve().relative_to(PLUGIN_SRC)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _import_module(module_path: Path) -> Any:
    """Import through the real package path, never as a standalone file.

    ``importlib.util.spec_from_file_location`` + ``sys.modules[name] = mod``
    registers a half-built module under its package-qualified name *before*
    the parent package runs. Every package here re-exports its submodules'
    symbols from ``__init__``, so that partially-initialized entry is what the
    ``__init__`` finds, and the re-export dies with
    ``ImportError: cannot import name '<symbol>'``. ``import_module`` imports
    the parents first, so the package is fully built before the submodule is
    bound.
    """
    if str(PLUGIN_SRC) not in sys.path:
        sys.path.insert(0, str(PLUGIN_SRC))
    return importlib.import_module(_module_name_for(module_path))


def _annotation_names(annotation: Any) -> set[str]:
    """Token set for an annotation, however it is spelled.

    ``str(bool | None)`` is ``"bool | None"``, but ``(bool | None).__name__``
    is ``"Union"`` on 3.14 — reading only the latter is how ``has_attachments``
    silently lost its sample value. Read both spellings.
    """
    text = f"{getattr(annotation, '__name__', '')} {annotation}"
    for char in "|[],":
        text = text.replace(char, " ")
    return {part.strip() for part in text.split() if part.strip()}


def _dataclass_sample(annotation: Any) -> tuple[bool, object]:
    """Return ``(found, instance)`` for a parameter annotated with a repo dataclass.

    A value object such as ``NativeReplyDraftIdentity`` cannot be synthesized
    from its annotation alone, and the function that takes one
    (``_delete_reply_artifact``) returns before it builds a single line of
    AppleScript unless the instance *agrees with its sibling arguments* —
    ``identity.draft_id`` must equal ``normalize_message_ids([draft_id])[0]``.
    Building each field from ``SAMPLE_KWARGS`` by field name is what keeps them
    in agreement: the field and the parameter read the same key. Fields with a
    default are left at it, and a required field with no sample makes the whole
    parameter unsynthesizable, which is reported loudly rather than skipped.

    Restricted to dataclasses defined in this package — a foreign one carries
    invariants this hook knows nothing about.
    """
    if not (isinstance(annotation, type) and dataclasses.is_dataclass(annotation)):
        return False, None
    if not getattr(annotation, "__module__", "").startswith(PACKAGE):
        return False, None
    kwargs: dict[str, object] = {}
    for field_info in dataclasses.fields(annotation):
        if field_info.name in SAMPLE_KWARGS:
            kwargs[field_info.name] = SAMPLE_KWARGS[field_info.name]
        elif field_info.default is dataclasses.MISSING and field_info.default_factory is dataclasses.MISSING:
            return False, None
    try:
        return True, annotation(**kwargs)
    except Exception:
        return False, None


def _synthesize(name: str, param: inspect.Parameter) -> tuple[bool, object]:
    """Return ``(found, value)`` for one parameter."""
    if name in SAMPLE_KWARGS:
        return True, SAMPLE_KWARGS[name]
    if name in FRAGMENT_PARAM_NAMES or name.endswith(FRAGMENT_PARAM_SUFFIXES):
        return True, ""
    found, instance = _dataclass_sample(param.annotation)
    if found:
        return True, instance
    names = _annotation_names(param.annotation)
    if names & {"list", "dict", "set", "tuple", "Callable"}:
        # A container's element semantics are builder-specific; guessing one
        # risks emitting AppleScript the shipped tool never produces. Curate it.
        return False, None
    if "bool" in names:
        return True, True
    if "int" in names:
        return True, 10
    if "float" in names:
        return True, 1.0
    return False, None


def _sample_kwargs_for(fn: Callable[..., Any]) -> tuple[dict[str, object] | None, list[str]]:
    """Synthesize call kwargs. Returns ``(kwargs, missing_parameter_names)``.

    ``kwargs is None`` means the builder cannot be exercised — a loud outcome,
    not a skip: an un-callable builder is an unchecked builder.
    """
    kwargs: dict[str, object] = {}
    missing: list[str] = []
    overrides = FUNCTION_KWARG_OVERRIDES.get(getattr(fn, "__name__", ""), {})
    for name, param in inspect.signature(fn).parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if name in overrides:
            kwargs[name] = overrides[name]
            continue
        found, value = _synthesize(name, param)
        if found:
            kwargs[name] = value
        elif param.default is not inspect.Parameter.empty:
            continue
        else:
            missing.append(name)
    if missing:
        return None, missing
    return kwargs, []


def _kwarg_variants(base: dict[str, object]) -> list[dict[str, object]]:
    """Base call plus one that flips every boolean, to reach both branches."""
    flipped = {k: (not v if isinstance(v, bool) else v) for k, v in base.items()}
    return [base] if flipped == base else [base, flipped]


def _osacompile_check(script: str) -> tuple[bool, str]:
    """Run osacompile in a tempfile. Returns ``(ok, stderr_excerpt)``."""
    with tempfile.NamedTemporaryFile("w", suffix=".applescript", delete=False) as src_f:
        src_f.write(script)
        src_path = src_f.name
    out_path = src_path.replace(".applescript", ".scpt")
    try:
        result = subprocess.run(
            ["osacompile", "-o", out_path, src_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout).strip()
        return True, ""
    finally:
        for p in (src_path, out_path):
            with suppress(OSError):
                Path(p).unlink()


@dataclass
class ModuleReport:
    path: Path
    rel: str
    import_error: str = ""
    compiled: int = 0
    builders_compiled: list[str] = field(default_factory=list)
    captured_from: list[str] = field(default_factory=list)
    fragments: list[str] = field(default_factory=list)
    executors: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    uncallable: list[tuple[str, str]] = field(default_factory=list)
    uncovered_inline: list[str] = field(default_factory=list)
    ledger_reason: str = ""
    osacompile_missing: bool = False

    @property
    def ledger_stale(self) -> bool:
        """Ledgered as unreachable, but the hook now reaches all of it."""
        return bool(self.ledger_reason) and not self.uncovered_inline and not self.import_error

    @property
    def blocked(self) -> bool:
        if self.osacompile_missing:
            return False
        if self.import_error or self.failures or self.uncallable:
            return True
        # AppleScript this hook could not reach, in a module nobody recorded as
        # a known gap: refuse to report a clean check.
        return bool(self.uncovered_inline) and not self.ledger_reason

    def summary(self) -> str:
        if self.import_error:
            return f"{self.rel}: IMPORT FAILED — {self.import_error}"
        if self.osacompile_missing:
            return f"{self.rel}: SKIPPED — osacompile not on PATH"
        if self.failures:
            return f"{self.rel}: {len(self.failures)} script(s) FAILED to compile"
        if self.uncallable:
            names = ", ".join(f"{n} (missing: {m})" for n, m in self.uncallable)
            return f"{self.rel}: builder(s) not callable — {names}"
        if self.compiled:
            sources = [f"{len(self.builders_compiled)} builder(s)"]
            if self.captured_from:
                sources.append(f"{len(self.captured_from)} inline tool(s)")
            extra = f" [{len(self.fragments)} fragment(s)]" if self.fragments else ""
            gap = f" [UNCHECKED: {', '.join(self.uncovered_inline)}]" if self.uncovered_inline else ""
            stale = " [STALE LEDGER ENTRY: drop it from UNCHECKABLE]" if self.ledger_stale else ""
            return f"{self.rel}: compiled {self.compiled} script(s) from {' + '.join(sources)}{extra}{gap}{stale}"
        if self.ledger_stale:
            return f"{self.rel}: STALE LEDGER ENTRY — now fully checked; drop it from UNCHECKABLE"
        if self.uncovered_inline and self.ledger_reason:
            return f"{self.rel}: UNCHECKED (known gap) — {self.ledger_reason}"
        if self.uncovered_inline:
            return f"{self.rel}: UNCHECKED — emits AppleScript this hook cannot reach"
        if self.fragments:
            # "no AppleScript to check" is a lie for a module that emits nothing
            # BUT AppleScript — reply_draft_resolver_scripts.py wraps its handlers
            # in ``using terms from application "Mail"`` rather than a ``tell``
            # block, so discovery classifies every one as a fragment and the old
            # wording read as a clean bill of health. The fragments are compiled
            # where they are spliced (reply_scripts.py's native reply builder);
            # say that instead of implying there was nothing here.
            return (
                f"{self.rel}: {len(self.fragments)} fragment(s), no standalone script"
                " — compiled through their caller"
            )
        return f"{self.rel}: no AppleScript to check"


def _full_scripts_in(value: Any) -> list[str]:
    """Every standalone script in a builder's return value.

    Builders return a bare string, or a tuple whose first element is the
    script (``_build_search_script`` returns ``(script, bool, bool)``, and
    reading only ``isinstance(value, str)`` skipped it entirely). A string with
    no ``tell application`` block is a fragment that only compiles inside an
    enclosing ``tell``, so it is not a compile target.
    """
    parts = value if isinstance(value, (tuple, list)) else (value,)
    return [text for text in parts if isinstance(text, str) and any(marker in text for marker in FULL_SCRIPT_MARKERS)]


def _source_of(fn: Callable[..., Any]) -> str:
    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):  # pragma: no cover - builtins / C funcs
        return ""


def _inline_script_functions(module: Any, module_name: str, handled: set[str]) -> list[tuple[str, Any]]:
    """Functions in this module that embed a full script but are not builders."""
    return [
        (name, fn)
        for name, fn in sorted(inspect.getmembers(module, inspect.isfunction))
        if fn.__module__ == module_name
        and name not in handled
        and any(marker in _source_of(fn) for marker in FULL_SCRIPT_MARKERS)
    ]


def check_module(module_path: Path) -> ModuleReport | None:
    """Check one module. ``None`` means "not ours" (outside plugin/)."""
    try:
        rel_plugin = module_path.resolve().relative_to(PLUGIN_SRC / PACKAGE)
    except ValueError:
        return None
    report = ModuleReport(path=module_path, rel=str(module_path.resolve().relative_to(REPO)))
    report.ledger_reason = UNCHECKABLE.get(rel_plugin.as_posix(), "")
    if not shutil.which("osacompile"):
        report.osacompile_missing = True
        return report

    module_name = _module_name_for(module_path)
    try:
        module = _import_module(module_path)
    except Exception as exc:
        report.import_error = f"{type(exc).__name__}: {exc}"
        return report
    _install_execution_guards()

    seen_scripts: set[int] = set()
    handled: set[str] = set()

    def compile_all(name: str, texts: list[str]) -> int:
        """osacompile every unseen full script in *texts*; return how many."""
        count = 0
        for text in texts:
            key = hash(text)
            if key in seen_scripts:
                continue
            seen_scripts.add(key)
            ok, err = _osacompile_check(text)
            if ok:
                report.compiled += 1
                count += 1
            else:
                report.failures.append((name, err))
        return count

    for name, fn in sorted(inspect.getmembers(module, inspect.isfunction)):
        if fn.__module__ != module_name or not _is_builder_name(name):
            continue
        base, missing = _sample_kwargs_for(fn)
        if base is None:
            report.uncallable.append((name, ", ".join(missing)))
            continue
        emitted_full = False
        executor = False
        for kwargs in _kwarg_variants(base):
            try:
                value = fn(**kwargs)
            except _ExecutorInvoked:
                executor = True
                break
            except Exception:
                # A builder that rejects one synthetic combination is fine as
                # long as another produced a script; ``emitted_full`` decides.
                continue
            full = _full_scripts_in(value)
            emitted_full = emitted_full or bool(full)
            compile_all(name, full)
        # Every builder is accounted for here, compiled or not: a builder that
        # emits only a fragment is not an inline-script tool, and must not be
        # re-reported as one by the pass below.
        handled.add(name)
        if executor:
            report.executors.append(name)
        elif emitted_full:
            report.builders_compiled.append(name)
        else:
            report.fragments.append(name)

    # Second pass: scripts built inline inside a tool body. There is no builder
    # to call, so call the tool itself with the AppleScript executor stubbed and
    # compile whatever it hands the stub. Nothing can reach Mail.app: the stub
    # records the script and raises before any osascript runs.
    for name, fn in _inline_script_functions(module, module_name, handled):
        base, _missing = _sample_kwargs_for(fn)
        if base is None:
            report.uncovered_inline.append(name)
            continue
        del _CAPTURED_SCRIPTS[:]
        returned: Any = None
        try:
            returned = fn(**base)
        except _ExecutorInvoked:
            pass
        except Exception:
            pass
        texts = [s for s in _CAPTURED_SCRIPTS if any(m in s for m in FULL_SCRIPT_MARKERS)]
        texts.extend(_full_scripts_in(returned))
        del _CAPTURED_SCRIPTS[:]
        if texts and compile_all(name, texts):
            report.captured_from.append(name)
        elif not texts:
            report.uncovered_inline.append(name)
    return report


def _paths_from_stdin() -> list[str]:
    if sys.stdin.isatty():
        return []
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return []
    tool_input = payload.get("tool_input") or {}
    candidate = tool_input.get("file_path") or tool_input.get("path") or ""
    return [candidate] if candidate else []


def _failure_report(reports: list[ModuleReport]) -> str:
    lines = ["AppleScript syntax check FAILED:"]
    for report in reports:
        lines.append(f"  ✗ {report.summary()}")
        for name, err in report.failures:
            lines.append(f"      {name}:")
            for ln in err.splitlines():
                lines.append(f"        {ln}")
        for name, missing in report.uncallable:
            lines.append(
                f"      {name}: add a representative value for [{missing}] to "
                "SAMPLE_KWARGS in .claude/hooks/check_applescript_compiles.py"
            )
        if report.import_error:
            lines.append("      the module does not import — the server cannot load it either")
        if not (report.failures or report.uncallable or report.import_error):
            lines.append(
                "      extract the inline script into a `*_script` builder so it can be "
                "compile-checked, or record the gap in UNCHECKABLE with a reason"
            )
    lines += [
        "",
        "This is the same class of bug as 3.3.0's get_awaiting_reply regression",
        "(commit 18362ab → c9e92fb). Fix the builder before relying on tests —",
        "invalid AppleScript can pass unit tests when those tests only assert",
        "the row-format protocol the Python parser consumes.",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    args = list(argv)
    report_mode = "--report" in args
    if report_mode:
        args.remove("--report")
    if not args:
        args = _paths_from_stdin()
    if not args:
        print(
            "check_applescript_compiles.py: no module path given (argv or hook JSON on "
            "stdin). Refusing to report a clean check without checking anything.",
            file=sys.stderr,
        )
        return 2

    reports: list[ModuleReport] = []
    for raw in args:
        path = Path(raw)
        if not path.is_absolute():
            path = (REPO / path).resolve()
        if not path.exists() or path.suffix != ".py":
            continue
        report = check_module(path)
        if report is not None:
            reports.append(report)

    if report_mode:
        for report in reports:
            print(report.summary())
    if not reports:
        return 0
    blocked = [r for r in reports if r.blocked]
    if blocked:
        print(_failure_report(blocked), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
