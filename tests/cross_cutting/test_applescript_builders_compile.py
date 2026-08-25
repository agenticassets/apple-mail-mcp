"""osacompile parse-check for every full-script AppleScript builder.

PURPOSE
-------
Version 3.3.0 shipped a parse-level AppleScript bug in
``_build_awaiting_reply_inbox_script`` that passed CI (Ubuntu, no
``osacompile``) but failed at runtime with osascript error -2740.

This module wraps the same discovery + compilation logic that
``.claude/hooks/check_applescript_compiles.py`` uses in dev-mode hooks,
but surfaces it as proper pytest tests so:

  - On macOS with ``osacompile`` installed: tests FAIL on parse errors,
    catching exactly the 3.3.0 regression class.
  - On Ubuntu / any system without ``osacompile``: tests are SKIPPED via
    ``pytest.mark.skipif`` so CI stays green without spurious skips that
    could hide real failures.

SKIP BEHAVIOUR
--------------
All tests in ``OsacompileAvailableTests`` use the module-level
``pytestmark`` for ``skipif(not _OSACOMPILE_AVAILABLE, ...)``.  The
``OsacompileUnavailableTests`` class runs always and verifies the graceful
skip logic itself.

DISCOVERY RULE (mirrors the hook)
----------------------------------
Any function whose name ends in ``_script`` AND whose return value (when
called with sample kwargs) starts with ``tell application "Mail"`` is
treated as a full-script builder and passed to ``osacompile -o /dev/null``.
Fragment helpers (e.g. ``inbox_mailbox_script``) are skipped because they
are not standalone scripts.
"""

from __future__ import annotations

import contextlib
import inspect
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# osacompile availability check — evaluated once at module import time
# ---------------------------------------------------------------------------

_OSACOMPILE_AVAILABLE = shutil.which("osacompile") is not None

# ---------------------------------------------------------------------------
# Sample kwargs for calling script builders without real account data.
# Mirrors SAMPLE_KWARGS in .claude/hooks/check_applescript_compiles.py.
# ---------------------------------------------------------------------------

_SAMPLE_KWARGS: dict[str, object] = {
    "account": "Test Account",
    "escaped_account": "Test Account",
    "days_back": 7,
    "inbox_cap": 10,
    "sent_cap": 20,
    "max_results": 5,
    "scan_cap": 100,
    "mailbox": "INBOX",
    "mailbox_name": "INBOX",
    "escaped_mailbox": "INBOX",
    "read_filter": "all",
    "var_name": "myVar",
    "account_var": "targetAccount",
    "replied_var": "repliedIds",
    "subject_keyword": "test",
    "sender": "test@example.com",
    "body_text": "test",
    "newsletter_condition": "(false)",
    "body_scan_block": "",
    "date_check": "",
    "max_emails": 10,
    "include_content": False,
    "include_message_id": False,
    # _build_needs_response_inbox_script
    "scan_body": False,
}


def _sample_kwargs_for(fn) -> dict[str, object] | None:
    """Return a kwargs dict for *fn* using _SAMPLE_KWARGS, or None if any
    required parameter lacks a sample value."""
    sig = inspect.signature(fn)
    kwargs: dict[str, object] = {}
    for name, param in sig.parameters.items():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if name in _SAMPLE_KWARGS:
            kwargs[name] = _SAMPLE_KWARGS[name]
        elif param.default is not inspect.Parameter.empty:
            # Has a default; omit from kwargs and let it use the default.
            pass
        else:
            return None  # Required param with no sample value
    return kwargs


def _osacompile_check(script: str) -> tuple[bool, str]:
    """Return ``(ok, stderr)`` for *script* compiled via osacompile.

    Writes the script to a temp file, calls
    ``osacompile -o /dev/null <file>``, and returns True iff the process
    exits 0.  The caller is responsible for skipping when osacompile is
    absent (see module-level ``pytestmark``).
    """
    with tempfile.NamedTemporaryFile(
        "w", suffix=".applescript", delete=False
    ) as src_f:
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
            # osacompile leaves no .scpt behind when it rejects the source, so
            # a missing output file is the normal failure path, not an error.
            with contextlib.suppress(OSError):
                Path(p).unlink()


def _collect_full_script_builders(module) -> list[tuple[str, callable]]:
    """Return ``[(name, fn)]`` for every function in *module* that:
    1. Has a name ending in ``_script``
    2. Can be called with _SAMPLE_KWARGS (all required params covered)
    3. Returns a string starting with ``tell application "Mail"``

    Fragment helpers (e.g. ``inbox_mailbox_script``) don't pass rule 3.
    """
    builders = []
    for name, fn in inspect.getmembers(module, inspect.isfunction):
        if not name.endswith("_script"):
            continue
        if fn.__module__ != module.__name__:
            continue  # imported helper from another module
        kwargs = _sample_kwargs_for(fn)
        if kwargs is None:
            continue
        try:
            text = fn(**kwargs)
        except Exception:
            continue
        if not isinstance(text, str):
            continue
        if 'tell application "Mail"' not in text.lstrip().split("\n", 1)[0]:
            continue  # fragment, not a full script
        builders.append((name, fn))
    return builders


# ---------------------------------------------------------------------------
# Tests that require osacompile
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _OSACOMPILE_AVAILABLE,
    reason="osacompile not available — AppleScript compile tests skipped on this platform",
)
class OsacompileAvailableTests(unittest.TestCase):
    """Each test compiles a specific script builder via osacompile.

    Failures here mean the script builder produces syntactically invalid
    AppleScript — the same class of bug as the 3.3.0 regression.

    New builders are added as separate test methods so the failure message
    pinpoints the exact function.
    """

    def _assert_compiles(self, module, fn_name: str):
        """Locate *fn_name* in *module*, call it with sample kwargs, and
        assert the resulting AppleScript compiles without errors."""
        fn = getattr(module, fn_name, None)
        self.assertIsNotNone(
            fn, f"{module.__name__}.{fn_name} not found — was it renamed?"
        )
        kwargs = _sample_kwargs_for(fn)
        self.assertIsNotNone(
            kwargs,
            f"{fn_name}: could not build sample kwargs — add param to _SAMPLE_KWARGS",
        )
        try:
            script = fn(**kwargs)
        except Exception as exc:
            self.fail(f"{fn_name}(**sample_kwargs) raised {exc!r}")
        self.assertIsInstance(script, str, f"{fn_name} must return a str")
        ok, err = _osacompile_check(script)
        self.assertTrue(
            ok,
            f"\n\n{fn_name} produced script that osacompile rejected:\n"
            f"{err}\n\n"
            "This is the same class of bug as the 3.3.0 get_awaiting_reply regression.\n"
            "Fix the builder before relying on tests.",
        )

    def _assert_tool_script_compiles(self, patch_target: str, call: Callable[[], object], label: str):
        """Capture the AppleScript a tool sends to *patch_target*, then compile it.

        The sibling of ``_assert_compiles`` for tools that assemble their script
        inline rather than through a discoverable ``_build_*_script`` function,
        so there is no builder to call directly. *call* invokes the tool and
        *label* names it in the failure messages.
        """
        captured: dict[str, str] = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch(patch_target, side_effect=fake_run):
            call()

        script = captured.get("script", "")
        self.assertTrue(script, f"No script was captured from {label}")
        ok, err = _osacompile_check(script)
        self.assertTrue(
            ok,
            f"\n{label} produced script that osacompile rejected:\n{err}\n\n"
            "Fix the inline script builder before releasing.",
        )

    # --- smart_inbox builders ---

    def test_smart_inbox_build_awaiting_reply_inbox_script_compiles(self):
        """Regression: 3.3.0 used 'header value of header named' (error -2740).
        Current form iterates 'headers of aMessage' — this test catches
        any revert to the broken form."""
        from apple_mail_mcp.tools import smart_inbox as m
        self._assert_compiles(m, "_build_awaiting_reply_inbox_script")

    def test_smart_inbox_build_awaiting_reply_sent_script_compiles(self):
        from apple_mail_mcp.tools import smart_inbox as m
        self._assert_compiles(m, "_build_awaiting_reply_sent_script")

    def test_smart_inbox_build_needs_response_inbox_script_compiles(self):
        from apple_mail_mcp.tools import smart_inbox as m
        self._assert_compiles(m, "_build_needs_response_inbox_script")

    # --- compose builders ---

    def test_compose_build_reply_native_window_applescript_compiles(self):
        """Compile the helper-prefixed native reply builder and its chunk typer.

        This builder intentionally does not meet the generic discovery rule: its
        name ends in ``_applescript`` rather than ``_script``, it requires
        structured script fragments, and it starts with top-level handlers
        before the Mail ``tell`` block.  Compile its fully generated script
        explicitly so a syntax error in the native reply flow, including the
        focus-guarded ``typeReplyBodyChunks`` handler, cannot be hidden by
        discovery filtering.
        """
        from apple_mail_mcp.tools.compose import reply_scripts as m

        script = m._build_reply_native_window_applescript(
            header_text="SAVING REPLY AS DRAFT",
            success_text="Reply saved as draft!",
            safe_account="Test Account",
            mailbox_lookup='set sourceMailbox to mailbox "Inbox" of targetAccount',
            lookup_script="set foundMessage to missing value",
            not_found_message="Email not found",
            body_temp_path="/tmp/apple-mail-compile-check-body.txt",
            reply_options="with opening window",
            sender_script="",
            signature_script="",
            cc_script="",
            bcc_script="",
            attachment_script="",
            mode="draft",
            cleanup_script="",
            safe_cc="",
            safe_bcc="",
            safe_attachment_info="",
            has_cc=False,
            has_bcc=False,
            has_attachments=False,
        )

        self.assertIn("on chunkFocusBlockedName(expectedTitle, derivedTitle, expectedWindowId)", script)
        self.assertIn(
            "on typeReplyBodyChunks(bodyText, expectedTitle, derivedTitle, expectedWindowId, preResolvedEditor)",
            script,
        )
        self.assertIn(
            "on resolveReplyBodyEditor(expectedTitle, derivedTitle, expectedWindowId, allowEditorClick)", script
        )
        self.assertIn("key up shift", script)
        ok, err = _osacompile_check(script)
        self.assertTrue(
            ok,
            "\n\nnative reply builder produced script that osacompile rejected:\n"
            f"{err}\n\n"
            "Fix the generated native reply script before relying on tests.",
        )

    def test_reply_builders_compile_across_modes_and_optional_fragments(self):
        """Compile both reply builders for every mode and optional-fragment mix.

        The generic discovery pass above calls each builder once, with a single
        sample ``mode``. That leaves whole branches of the native script — the
        ``mode="send"`` variant, which omits the draft-resolver fragments, and
        the ``mode="open"`` variant, which omits only the window-close fragment
        — never compiled by any gate, even though every native reply the tool
        sends takes one of them.

        The optional fragments (``sender``, ``signature``, ``cc``, ``bcc``,
        ``attachment``) are spliced in as text, so a syntax error inside one
        surfaces whenever that fragment is present. Compiling each fragment in
        isolation, plus all-off and all-on, localizes a failure to a single
        fragment instead of reporting only that "the all-on script broke".

        The fragment text comes from the real helpers ``reply_to_email`` uses,
        not hand-written stand-ins, so this compiles what actually ships.
        """
        from apple_mail_mcp.tools.compose import reply as reply_module
        from apple_mail_mcp.tools.compose import reply_script_helpers as helpers
        from apple_mail_mcp.tools.compose import reply_scripts as m

        cc_script, bcc_script, _, _ = reply_module._build_recipient_loops(
            "cc@example.com",
            "bcc@example.com",
            message_var="replyMessage",
        )
        attachment_script = """
                set theFile to POSIX file "/tmp/apple-mail-compile-check.pdf"
                tell replyMessage
                    make new attachment with properties {file name:theFile} at after the last paragraph of content
                end tell
                delay 1
            """
        optional_fragments = {
            "sender": ('set sender of replyMessage to "sender@example.com"', "sender_script"),
            "signature": (helpers._reply_signature_script("Work", include_signature=True), "signature_script"),
            "cc": (cc_script, "cc_script"),
            "bcc": (bcc_script, "bcc_script"),
            "attachment": (attachment_script, "attachment_script"),
        }
        # all-off, each fragment alone, all-on
        fragment_mixes: list[tuple[str, set[str]]] = [("none", set())]
        fragment_mixes += [(name, {name}) for name in optional_fragments]
        fragment_mixes.append(("all", set(optional_fragments)))

        base = {
            "header_text": "SAVING REPLY AS DRAFT",
            "success_text": "Reply saved as draft!",
            "safe_account": "Test Account",
            "mailbox_lookup": 'set sourceMailbox to mailbox "Inbox" of targetAccount',
            "lookup_script": "set foundMessage to missing value",
            "not_found_message": "Email not found",
            "body_temp_path": "/tmp/apple-mail-compile-check-body.txt",
            "cleanup_script": 'do shell script "rm -f " & quoted form of "/tmp/apple-mail-compile-check-body.txt"',
            "safe_cc": "cc@example.com",
            "safe_bcc": "bcc@example.com",
            "safe_attachment_info": "  /tmp/apple-mail-compile-check.pdf\n",
        }

        checked = 0
        for mode in ("draft", "open", "send"):
            mode_plan = helpers._reply_mode_plan(mode)
            for reply_to_all in (False, True):
                for mix_name, enabled in fragment_mixes:
                    fragments = {
                        param: (text if name in enabled else "")
                        for name, (text, param) in optional_fragments.items()
                    }
                    flags = {
                        "has_cc": "cc" in enabled,
                        "has_bcc": "bcc" in enabled,
                        "has_attachments": "attachment" in enabled,
                    }
                    native_options, _ = helpers._reply_command_options("open", reply_to_all)
                    object_options, settle_delay = helpers._reply_command_options(mode, reply_to_all)
                    variants = {
                        "native": m._build_reply_native_window_applescript(
                            mode=mode,
                            reply_options=native_options,
                            **base,
                            **fragments,
                            **flags,
                        ),
                        "objectmodel": m._build_reply_objectmodel_applescript(
                            reply_options=object_options,
                            reply_settle_delay=settle_delay,
                            post_action=mode_plan.post_action,
                            **base,
                            **fragments,
                            **flags,
                        ),
                    }
                    for path, script in variants.items():
                        with self.subTest(path=path, mode=mode, reply_to_all=reply_to_all, fragments=mix_name):
                            ok, err = _osacompile_check(script)
                            self.assertTrue(
                                ok,
                                f"\n\n{path} reply script rejected by osacompile "
                                f"(mode={mode}, reply_to_all={reply_to_all}, fragments={mix_name}):\n{err}\n",
                            )
                        checked += 1

        # 3 modes x 2 reply_to_all x 7 fragment mixes x 2 builders
        self.assertEqual(checked, 84)

    # --- inbox builders ---

    def test_inbox_build_list_inbox_json_script_compiles(self):
        from apple_mail_mcp.tools import inbox as m
        self._assert_compiles(m, "_build_list_inbox_json_script")

    def test_inbox_build_list_inbox_text_script_compiles(self):
        from apple_mail_mcp.tools import inbox as m
        self._assert_compiles(m, "_build_list_inbox_text_script")

    def test_inbox_build_overview_one_account_script_compiles(self):
        from apple_mail_mcp.tools import inbox as m
        self._assert_compiles(m, "_build_overview_one_account_script")

    # --- analytics builders ---
    # analytics.get_statistics builds its script inline (no named builder
    # function), so we capture via a tool-level round-trip and compile that.

    def test_analytics_get_statistics_overview_script_compiles(self):
        """Capture the script that get_statistics emits for account_overview
        and compile it.  This covers the inline script in analytics.py which
        has no named _build_* wrapper."""
        from apple_mail_mcp.tools import analytics as m

        self._assert_tool_script_compiles(
            "apple_mail_mcp.tools.analytics.run_applescript",
            lambda: m.get_statistics(account="Work", scope="account_overview", days_back=7),
            "get_statistics(account_overview)",
        )

    def test_analytics_get_statistics_sender_stats_script_compiles(self):
        from apple_mail_mcp.tools import analytics as m

        self._assert_tool_script_compiles(
            "apple_mail_mcp.tools.analytics.run_applescript",
            lambda: m.get_statistics(
                account="Work",
                scope="sender_stats",
                sender="test@example.com",
                days_back=7,
            ),
            "get_statistics(sender_stats)",
        )

    def test_analytics_get_statistics_mailbox_breakdown_script_compiles(self):
        """mailbox_breakdown builds its script inline too, and AGENTIC-2346
        added an if/else label branch plus an interpolated note string to it.
        Mocked tests only assert the emitted text, so this is the only check
        that the AppleScript is actually valid."""
        from apple_mail_mcp.tools import analytics as m

        self._assert_tool_script_compiles(
            "apple_mail_mcp.tools.analytics.run_applescript",
            lambda: m.get_statistics(account="Work", scope="mailbox_breakdown", mailbox="INBOX"),
            "get_statistics(mailbox_breakdown)",
        )

    def test_inbox_list_mailboxes_count_script_compiles(self):
        """list_mailboxes builds its script inline. AGENTIC-2346 added an
        if/else cached-count label branch inside the include_counts block,
        which is also string-substituted for child mailboxes."""
        from apple_mail_mcp.tools import inbox as m

        self._assert_tool_script_compiles(
            "apple_mail_mcp.tools.inbox.run_applescript",
            lambda: m.list_mailboxes(account="Work", include_counts=True),
            "list_mailboxes(include_counts=True)",
        )


# ---------------------------------------------------------------------------
# Tests that always run — verify graceful-skip and fragment detection
# ---------------------------------------------------------------------------


class OsacompileUnavailableTests(unittest.TestCase):
    """These tests always run regardless of osacompile availability.

    They verify that:
    - ``_osacompile_check`` handles the unavailable-osacompile case cleanly
      (the skipif marker on OsacompileAvailableTests means it never calls
      _osacompile_check when the tool is absent, but this test is extra
      documentation).
    - ``_collect_full_script_builders`` correctly distinguishes full scripts
      from fragment helpers by the 'tell application "Mail"' sentinel.
    """

    def test_osacompile_check_returns_tuple_on_success_or_error(self):
        """_osacompile_check must always return (bool, str) — never raise —
        so callers can safely inspect the result."""
        if not _OSACOMPILE_AVAILABLE:
            # If not available the function would error when called because it
            # doesn't guard internally; the caller (OsacompileAvailableTests)
            # is skipped. Just assert the sentinel is correct.
            self.assertFalse(_OSACOMPILE_AVAILABLE)
            return
        ok, err = _osacompile_check('tell application "Mail"\n  return "ok"\nend tell')
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(err, str)

    def test_fragment_helpers_excluded_from_builder_collection(self):
        """Fragment helpers like ``inbox_mailbox_script`` do not start with
        ``tell application "Mail"`` so they must be excluded from the compile
        check — otherwise the test would compile an incomplete snippet and
        fail spuriously."""
        from apple_mail_mcp.tools.smart_inbox import awaiting_reply, needs_response

        awaiting_names = {name for name, _ in _collect_full_script_builders(awaiting_reply)}
        needs_names = {name for name, _ in _collect_full_script_builders(needs_response)}
        # Fragment helpers must be absent (``_newsletter_filter_condition``
        # returns a bare condition, not a full ``tell application "Mail"`` script).
        self.assertNotIn("_newsletter_filter_condition", needs_names)
        # Full builders must be present in their respective submodules.
        self.assertIn("_build_awaiting_reply_inbox_script", awaiting_names)
        self.assertIn("_build_awaiting_reply_sent_script", awaiting_names)
        self.assertIn("_build_needs_response_inbox_script", needs_names)

    def test_inbox_full_script_builders_detected(self):
        from apple_mail_mcp.tools.inbox import list_scripts as m
        builders = _collect_full_script_builders(m)
        names = {name for name, _ in builders}
        self.assertIn("_build_list_inbox_json_script", names)
        self.assertIn("_build_list_inbox_text_script", names)

    def test_osacompile_flag_is_bool(self):
        self.assertIsInstance(_OSACOMPILE_AVAILABLE, bool)


if __name__ == "__main__":
    unittest.main()
