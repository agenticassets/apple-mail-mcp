# tests/ — pytest suite

Mocked unit tests for the Apple Mail MCP server. The collected-test count is single-sourced in [`../tools/expected_test_count.txt`](../tools/expected_test_count.txt) (the dev-check/release gate fails on drift and prints the new number); recount with `PYTEST_ADDOPTS='' .venv/bin/pytest tests/ --collect-only`. Local CI-equivalent gates do not invoke Mail.app: most tests mock AppleScript or exercise pure Python, while macOS syntax checks may invoke local `osacompile` and infrastructure tests may run subprocess contracts in temporary fixtures.

New tests and perf gates: delegate to a **`shell`** or **`generalPurpose`** subagent when available and permitted; parent runs the relevant suite after merge. See root [`CLAUDE.md`](../CLAUDE.md), Agent orchestration section.

**This repo is PUBLIC.** Fixtures use synthetic addresses (`sender@example.com`) and synthetic subjects. Never build a fixture by pasting a real message, header block, or account UUID out of a live run, even when reproducing a real bug: reduce it to the shape that triggers the bug. See root [`AGENTS.md`](../AGENTS.md) § This repo is PUBLIC.

```bash
.venv/bin/pytest tests/
.venv/bin/pytest tests/cli/test_cli.py -q
```

Dev venv: root `.venv/` (editable install). See root [`CLAUDE.md`](../CLAUDE.md).

## conftest.py — validate_account_name

Autouse fixture `_pass_through_known_test_accounts` patches `validate_account_name` in `core` and every tool module. `account='Work'` passes without real Mail; `account='Missing'` returns structured `account_not_found`. Most tool tests depend on this.

## Mock patterns

- **AppleScript capture** — patch `subprocess.run` with `side_effect` reading script from `kwargs["input"]`. Templates: `cross_cutting/test_modernization_3_1_5.py` (`_ScriptCapture`), `search/test_mail_search_tools.py`, `compose/test_compose_tools.py`.
- **Pure helpers** — `core/test_bulk_helpers.py`: `escape_applescript`, filters, mailbox refs (no subprocess mock).
- **Registry / CLI** — `core/test_read_only_registry.py`, `cli/test_cli.py`, `cli/test_cli_perf.py` (perf thresholds, `--include-analysis`, profiles; no live Mail).
- **AppleScript syntax** — `cross_cutting/test_applescript_builders_compile.py` compiles full-script builders with local `osacompile` when available; it skips the compile class on hosts without that executable.
- **Wrapper surface** — `infra/test_wrapper_surface.py`: mocks `check_wrapper_surface.py` help parsing (no generated wrapper required).
- **Infra** — `core/test_orphan_watcher.py` (injectable seams); `infra/test_validate_manifests.py`; `infra/test_tasks_layout.py` (tasks/ bucket layout); `infra/test_module_line_budget.py` (600 LOC budget warn + baseline regression).

## Test files

The live module inventory below is intentionally names-only; use `find tests -type f -name 'test_*.py' | sort` rather than maintaining a total here. `conftest.py` and `fixtures/` stay at `tests/` root.

**Modules by subfolder:**

- **`analytics/`**: `test_analytics_resource_safety`, `test_correspondent_recipient_reads`, `test_dashboard_id_first`, `test_dashboard_reply_state`, `test_dashboard_scan_diagnostics`, `test_dashboard_ui_diagnostics`, `test_export`, `test_export_failure_reporting`, `test_export_single_email_target`, `test_full_inbox_export`, `test_get_statistics_json`, `test_statistics_read_provenance`
- **`calendar_surface/`**: `test_availability`, `test_batch_create`, `test_calendar_date_block`, `test_calendar_engine`, `test_calendar_enumeration_errors`, `test_calendar_gating`, `test_calendar_identifier_selection`, `test_calendar_records`, `test_calendar_recurrence`, `test_calendar_scripts`, `test_calendar_validation`, `test_calendar_window`, `test_create_event`, `test_delete_events`, `test_eventkit_engine`, `test_get_events_by_id`, `test_list_calendars`, `test_list_events`, `test_manage_calendars`, `test_update_event`
- **`cli/`**: `test_cli`, `test_cli_characterization`, `test_cli_error_contract`, `test_cli_perf`
- **`compose/`**: `test_attachment_draft_contract`, `test_attachment_path_expansion`, `test_clipboard_roundtrip`, `test_compose_none_handling`, `test_compose_security`, `test_compose_tools`, `test_draft_verification_helpers`, `test_forward_attachment_contract`, `test_forward_quote_integrity`, `test_guarded_delete_fails_closed`, `test_html_compose_focus`, `test_html_compose_subject`, `test_manage_drafts_cleanup_slice`, `test_manage_drafts_threading`, `test_native_reply_attachment_fail_closed`, `test_native_reply_frontmost`, `test_native_reply_identity_capsule`, `test_native_reply_sender_override`, `test_native_reply_window_lifecycle`, `test_reply_abort_cleanup_authorization`, `test_reply_accessibility_preflight`, `test_reply_draft_resolver_contract`, `test_reply_json_contract`, `test_reply_mismatch_delete_authorization`, `test_reply_quote_proof_execution`, `test_reply_typing_correction_panel`, `test_typing_settle_budget`, `test_verify_draft_quote_offset`
- **`core/`**: `test_applescript_snippets`, `test_bounded_scan_contract`, `test_bounded_scan_stale_count_guard`, `test_bulk_helpers`, `test_core_fetch_replied_ids`, `test_core_helpers_characterization`, `test_core_validators`, `test_metadata_index_contract`, `test_no_bare_applescript_try`, `test_no_unbounded_whose`, `test_orphan_watcher`, `test_read_only_registry`, `test_reply_state`, `test_run_applescript_process_lock`, `test_run_applescript_timeout_validation`, `test_tool_error_envelope_boundary`
- **`cross_cutting/`**: `test_applescript_builders_compile`, `test_applescript_handler_names`, `test_applescript_script_idioms`, `test_id_first_guidance`, `test_modernization_3_1_5`, `test_phase_2_scan_hardening`, `test_phase_a_fixes`, `test_replied_detection`, `test_scalability_24k`, `test_tier1_hardening_regression`, `test_tier3_hardening`, `test_unread_count_provenance`
- **`inbox/`**: `test_contracts_inbox_tools`, `test_get_inbox_overview_json`, `test_gmail_unread_crash_regression`, `test_inbox_pure_helpers`, `test_inbox_tools`, `test_inbox_typed_kwargs`, `test_list_inbox_error_reporting`, `test_list_inbox_partial_rows`, `test_list_inbox_reply_state`, `test_overview_recent_scan_bound`, `test_overview_reply_state`, `test_reply_state_wiring`, `test_unread_counts_failure_reporting`
- **`infra/`**: `test_applescript_compile_hook`, `test_compare_perf_results`, `test_git_hooks`, `test_inspect_envelope_index_schema`, `test_marketplace_identity`, `test_marketplace_payload`, `test_measure_metadata_hydration`, `test_module_line_budget`, `test_no_committed_identity`, `test_offline_runtime`, `test_packaged_skill_paths`, `test_perf_budget`, `test_refresh_central_marketplace`, `test_repo_root`, `test_server_version_parity`, `test_source_release_trust`, `test_tasks_layout`, `test_validate_manifests`, `test_wrapper_surface`
- **`manage/`**: `test_manage_bulk_action_errors`, `test_manage_create_mailbox`, `test_manage_status_reporting`, `test_manage_trash_safety`, `test_move_email_bounds`
- **`property/` (property-based)**: `test_escape_applescript_properties`, `test_validate_account_name_properties`
- **`search/`**: `test_contracts_search_tools`, `test_dispatch_sync_bridge_bounds`, `test_mail_search_tools`, `test_search_bounded_candidate_binding`, `test_search_error_channels`, `test_search_escaping`, `test_search_partial_render_and_missing_id`, `test_search_scan_ceiling_contract`, `test_search_split_characterization`, `test_thread_candidate_scan_failures`
- **`smart_inbox/`**: `test_contracts_smart_inbox`, `test_smart_inbox_json`, `test_smart_inbox_top_senders_domain`

## v3.2.0 contract suite (capability-token + unbounded-scan refusal — keep green before any release)

`test_bounded_scan_contract`, `test_no_unbounded_whose`, `test_full_inbox_export`.

## HTML compose contract (`compose_email` + `body_html` / attachments)

Mocked script-capture tests lock the AppleScript order and cleanup semantics for
HTML paste, subject restore, focus, and attachment identity. Live verification:
[`docs/AGENT_LIVE_TESTING.md`](../docs/AGENT_LIVE_TESTING.md) § HTML compose
subject and focus.

| Module | What it locks |
|--------|----------------|
| `test_html_compose_subject.py` | Real subject restored on `newMsg` **after paste, before first save**; never `set subject of newMsg to temporarySubjectMarker` on success or error; send path verifies restored subject before `send newMsg`; attachment finalize binds by exact saved subject (`operation_exact_subject`), never `set subject of markedDraft to`; Python throw/timeout runs `run_html_compose_subject_followup` and **fails closed** (marker absence is not success); `DRAFT_ATTACHMENT_PROOF_FAILED` stays distinct |
| `test_html_compose_focus.py` | `focusComposeBody` binds to marker-named window; Tabs only while Accessibility reports a header field; returns immediately when body already focused; paste precedes subject restore |
| `test_attachment_draft_contract.py` | Attachment drafts paste before save; finalize order is restore outgoing subject → `save newMsg` → bounded Drafts scan → proof (stored subject must equal the real subject); error cleanup deletes the focus-failure fixture without restoring a real subject; no `whose subject` scans |
| `test_compose_tools.py` (`ComposeRunApplescriptMigrationTests`) | HTML draft/open/send paths keep restore-before-save ordering |

Key assertions to preserve when editing `html_subject_scripts.py`, `html_focus_scripts.py`, or `send.py`:

- Order: `focusComposeBody` → paste → `set subject of newMsg to "<real>"` → verify (exact marker token, not a prefix contains) → `save newMsg` / `send newMsg` → attachment proof / marker sweep.
- The marker is a pre-save window-binding token only; saved Drafts subjects are read-only on Gmail — never write the marker back onto a persisted draft.
- Error/follow-up paths use `standalone_exact_marker_restore_or_delete_script`; ambiguous marker matches fail closed. Focus failure deletes the fixture. Success-path leftover marker Drafts fail closed instead of delete-and-succeed.

Run the compose HTML suite in isolation:

```bash
.venv/bin/pytest tests/compose/test_html_compose_subject.py tests/compose/test_html_compose_focus.py tests/compose/test_attachment_draft_contract.py -q
```

## Module line budget

**600 LOC** soft target on `plugin/apple_mail_mcp/` and `tools/` (test modules are not budgeted). Enforced by:

- `tests/infra/test_module_line_budget.py` — warn on oversize production modules; fail on baseline regression
- `tools/validators/check_module_line_budget.py` — standalone report (also invoked by `dev-check.sh` and the local hooks)
- Baseline: `tests/fixtures/module_line_budget/baseline.json` (empty `modules` after v3.9.1 decomposition; regression gate still blocks growth if entries are reintroduced)

```bash
python3 tools/validators/check_module_line_budget.py
python3 tools/validators/check_module_line_budget.py --write-baseline tests/fixtures/module_line_budget/baseline.json
```

Full rules: [`docs/CLAUDE-conventions.md`](../docs/CLAUDE-conventions.md) § Module line budget.

## Local gates vs live Mail

GitHub-hosted Actions are disabled. Install the checked-in hooks with
`bash tools/gates/install-git-hooks.sh`, verify
`git config --get core.hooksPath` returns `.githooks`, and use
[`tools/gates/pre-commit-validate.sh`](../tools/gates/pre-commit-validate.sh)
plus the release-sensitive pre-push gate. Live verification:
[`docs/AGENT_LIVE_TESTING.md`](../docs/AGENT_LIVE_TESTING.md).

## Related

[`docs/CLAUDE-conventions.md`](../docs/CLAUDE-conventions.md) · [`tools/CLAUDE.md`](../tools/CLAUDE.md) · [`plugin/apple_mail_mcp/`](../plugin/apple_mail_mcp/)
