# Apple Mail MCP Live Verification Report (2026-08-01)

> **Historical record:** a 2026-08-01 snapshot of one branch, not a current
> baseline. Its collected-test figure is that day's; the live count is
> single-sourced in [`tools/expected_test_count.txt`](../../tools/expected_test_count.txt).
> For current procedures use [`docs/AGENT_LIVE_TESTING.md`](../AGENT_LIVE_TESTING.md),
> and re-run the gates rather than citing this report as present-day evidence.

**Branch:** `fix/apple-mail-linear-backlog-20260731`
**Scope:** Redacted, evidence-limited verification of the Linear-backlog and final remediation changes.
**Safety:** The live probe was read-only. No email was sent, drafted, moved,
deleted, flagged, marked read or unread, or exported.

## Evidence recorded in this session

| Check | Result | What it established |
|---|---|---|
| `bash tools/gates/dev-check.sh release` | passed | Release validation, including lint, strict type checks, artifact build/parity, manifest validation, module-line budget, and the complete unit suite. |
| Complete unit suite | `1,643` collected tests passed | The changed export, calendar CLI, reply, guarded-draft-delete, compose, and numeric-ID paths have automated coverage alongside the rest of the package. |
| `bash tools/gates/validate-codex-plugin.sh` | passed | The checked-in Codex plugin adapter launched the shared runtime in draft-safe mode and exposed the expected 41-tool contract. |
| Production `perf-test` and `smoke-test` | passed | Redacted live checks on the 100-mailbox production profile passed metadata, no-hit search, inbox, dry-run move/trash, overview, dashboard, invalid-account, and draft-safe-send-block probes. |
| Bounded Calendar no-hit participant probe | passed after CLI timeout support | A fresh `calendar-events --participant-query ... --timeout 5` run returned within the requested bound; a prior pre-fix OS Calendar wait was terminated without changing Calendar data. |

## Interpretation and limits

This report proves the current branch passed its configured local release gate,
the checked-in Codex adapter runtime smoke, redacted production Mail probes,
and a bounded Calendar participant-query no-hit probe. It does not prove
Claude, Codex, Cursor, or marketplace GUI admission; none was performed in
this session. It also does not exercise mutation-capable flows such as fixture
EML export, native reply, guarded draft cleanup, or HTML compose focus.

Run those feature checks only with the disposable fixture-account procedures in
[`docs/AGENT_LIVE_TESTING.md`](../AGENT_LIVE_TESTING.md). Do not substitute
this report, static manifest validation, or a generic quick-check for a client
UI acceptance result or a live mutation proof.
