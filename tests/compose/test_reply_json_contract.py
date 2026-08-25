"""``reply_to_email(output_format="json")`` must always return parseable JSON.

The tool documents a JSON contract for ``mode="draft"`` / ``mode="open"``, but
the compose AppleScript has non-success exits that are plain prose: the
not-found message, the ``Error: ...`` tail from its ``on error`` handler, and
``QUOTE_PROOF_UNAVAILABLE``. Returned verbatim, those reach a JSON caller as a
``json.loads`` parse error rather than as the failure they describe, so an
agent branching on the parsed ``code`` never sees one. Text callers must keep
byte-for-byte what they always received.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp.tools import compose as compose_tools
from apple_mail_mcp.tools.compose import reply as reply_module

_NOT_FOUND = "Error: No email found for message_id=12345"
_QUOTE_PROOF_UNAVAILABLE = "QUOTE_PROOF_UNAVAILABLE\nDetail: source content has no usable quote anchor"


def _reply_with_script_output(output: str, **kwargs: object) -> str:
    """Run ``reply_to_email`` against a compose script that returns ``output``."""

    def fake_run(script: str, timeout: int = 120) -> str:
        if "reply foundMessage" in script:
            return output
        return "NOT_FOUND"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            **kwargs,  # type: ignore[arg-type]
        )
    assert isinstance(result, str)
    return result


class ReplyJsonOutputContractTests(unittest.TestCase):
    def test_not_found_is_a_json_envelope_under_json_output_format(self) -> None:
        result = _reply_with_script_output(_NOT_FOUND, output_format="json")

        payload = json.loads(result)
        self.assertTrue(payload["error"])
        self.assertEqual(payload["code"], "REPLY_NOT_COMPLETED")
        # The script's own words survive: the envelope adds structure, it does
        # not replace the only description of what actually went wrong.
        self.assertEqual(payload["remediation"]["script_output"], _NOT_FOUND)

    def test_not_found_stays_verbatim_under_text_output_format(self) -> None:
        """Text callers are unchanged; this fix is JSON-only by construction."""
        result = _reply_with_script_output(_NOT_FOUND)

        self.assertEqual(result, _NOT_FOUND)

    def test_quote_proof_unavailable_keeps_its_own_code(self) -> None:
        """The sentinel is a distinct failure with distinct remediation.

        Folding it into the generic code would tell an agent to widen
        ``recent_days`` when the real problem is that the source message has no
        readable body at all.
        """
        result = _reply_with_script_output(_QUOTE_PROOF_UNAVAILABLE, output_format="json")

        payload = json.loads(result)
        self.assertEqual(payload["code"], "QUOTE_PROOF_UNAVAILABLE")
        self.assertEqual(payload["remediation"]["script_output"], _QUOTE_PROOF_UNAVAILABLE)

    def test_quote_proof_unavailable_stays_verbatim_under_text_output_format(self) -> None:
        result = _reply_with_script_output(_QUOTE_PROOF_UNAVAILABLE)

        self.assertEqual(result, _QUOTE_PROOF_UNAVAILABLE)

    def test_applescript_error_tail_is_enveloped(self) -> None:
        """The script's ``on error`` tail is prose too, and is multi-line."""
        errored = "Error: Mail got an error: Can't get account.\nPlease check that the account name is correct."
        result = _reply_with_script_output(errored, output_format="json")

        payload = json.loads(result)
        self.assertEqual(payload["code"], "REPLY_NOT_COMPLETED")
        self.assertEqual(payload["remediation"]["script_output"], errored)

    def test_both_passthrough_sites_route_through_the_envelope(self) -> None:
        """The retype attempt has the same exit and must not skip the envelope.

        Only the first compose attempt is reachable from a mocked single-run
        harness; the retry path runs after a delete-and-retype, which needs a
        successful first pass plus a placement failure plus an RFC-backed
        identity. Asserting on the source keeps the second site from silently
        regressing to a bare ``return current_result``.
        """
        source = Path(reply_module.__file__ or "").read_text(encoding="utf-8")
        guard = "if mode_plan.success_text not in current_result:"
        self.assertEqual(source.count(guard), 2)
        for segment in source.split(guard)[1:]:
            next_statement = segment.strip().splitlines()[0]
            self.assertIn("_unrecognized_reply_output_response(current_result", next_statement)
            self.assertIn("output_format=output_format", next_statement)


if __name__ == "__main__":
    unittest.main()
