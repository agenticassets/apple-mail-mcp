# Tasks Index

Navigation hub for cross-session planning. **Start at [`todo.md`](todo.md)** for the current state and next action.

**Agents:** placement rules are mandatory — read [`CLAUDE.md`](CLAUDE.md) § Agent requirements before creating or moving files here. Local gates enforce layout via `tools/validators/validate_tasks_layout.py`.

## Layout

| Folder | Role |
|--------|------|
| [`todo.md`](todo.md) | Tiny active pointer (current state, open lanes, roadmap link) |
| [`active/`](active/) | Open workstreams from the last ~30 days |
| [`reference/`](reference/) | Durable specs, goals, baselines, and the roadmap |
| [`archive/`](archive/) | Shipped, superseded, or resolved artifacts (do not edit for current work) |

## Active workstreams

| Folder | Purpose | Status |
|--------|---------|--------|
| [`active/thread-member-completeness-2794/`](active/thread-member-completeness-2794/) | Five root causes behind one silent thread-truncation symptom: thread scan bound, search scan sizing, attachment mailbox scope, `mailbox="All"` anchor, and thread export honesty (AGENTIC-2794 and sub-issues) | Implemented and live-verified on `fix/thread-member-completeness`; awaiting release gate, PR, and Cayman merge approval |
| [`active/calendar-reply-state-2026-08-26/`](active/calendar-reply-state-2026-08-26/) | Stable Calendar object references and exact Sent-thread reply evidence (AGENTIC-2527, AGENTIC-2528) | 3.12.1 branch verified locally and live; awaiting PR and Cayman merge approval |
| [`active/linear-backlog-2026-07-31/`](active/linear-backlog-2026-07-31/) | Bounded Linear backlog fixes: export, mailbox resolution, calendar participant discovery, and compose/draft safety | Shipped in PR #83, merged to `origin/main` at `ed9e1ee`; retained here pending archival housekeeping |
| [`active/native-reply/`](active/native-reply/) | Native-format reply drafts, including attachment preservation, fail-closed verification, foreground requirement, and typing-path timing | Attachment fix verified offline; disposable-fixture live matrix pending. Mail-must-be-frontmost fix shipped and live-proven. **2026-08-25: read [`live-results-2026-08-25.md`](active/native-reply/live-results-2026-08-25.md) first** — live testing completed and fixed three root causes (single-sample Accessibility preflight aborting when Mail is on another Space; typed body truncated because nothing waited for WebKit to drain before save; that wait needing to scale with body length). `TYPING_CHUNK_SIZE` 120 → 300; chunk size is a proxy for backlog depth, not a safety dial. The settle poll now exits on a length delta against a pre-typing baseline, and the tail-match fallback is gated off when the pre-typing editor already contains the tail, so a sign-off repeated in Mail's quoted original cannot satisfy it before a character lands. Shipped in PR #99 (`4a143fc`). Background: [`session-degradation-test-plan-2026-08-25.md`](active/native-reply/session-degradation-test-plan-2026-08-25.md) — it withdraws the "chunk-size cliff between 160 and 200" from [`live-timing-and-frontmost-2026-08-24.md`](active/native-reply/live-timing-and-frontmost-2026-08-24.md) § 4 (those passes verified against previous runs' drafts). It predates the drain fix, so its revert of the shipped constant to 120 is superseded (shipped value is 300); its block on re-measurement until the verifier fix (AGENTIC-2517) lands still stands. Linear: `AGENTIC-2522` (tracking) + 2517/2518/2519/2520/2521 |
| [`active/id-first-search-retirement/`](active/id-first-search-retirement/) | v4 fuzzy-selector retirement, metadata-index spike, `allow_filter_scan` decision | Decision brief awaiting sign-off; follow-up branches not started |
| [`active/v4-performance-consolidation-2026-05-27/`](active/v4-performance-consolidation-2026-05-27/) | v4 perf, FTS, strict-gate | Module split shipped (v3.9.1); perf/FTS stalled since 2026-05-27; confirm resume vs archive |
| [`active/v3.11.6-cursor-adapter/`](active/v3.11.6-cursor-adapter/) | Explicit Cursor plugin-root launcher and host-specific validator | Shipped on `main` in v3.11.6; local 41-tool Cursor Agent acceptance passed; Marketplace/UI distribution evidence remains open |
| [`active/central-marketplace-source-contract/`](active/central-marketplace-source-contract/) | Source payload, identity, signed-tag, and local-gate contract for the shared multi-plugin marketplace | PR #80 merged; shared Marketplace admission and client-registration remediation remain separately authorized external gates |
| [`active/fast-search-index/`](active/fast-search-index/) | Index-backed fast path for metadata search: `Envelope Index` capability envelope, safe-read pattern, phased tool plan (AGENTIC-2345) | Research complete and measured; implementation not started. Phase 0 (AGENTIC-2344) shipped fixed in v3.11.7; its AGENTIC-2356 date-window follow-up is live-verified on a large Exchange mailbox |
| [`active/post-3.11.7-defect-audit/`](active/post-3.11.7-defect-audit/) | Eight ranked post-3.11.7 defects and lower-priority audit items | Merged 2026-08-19 as PR #94 (`f1264c6`) and tagged `v3.11.8`; retained here pending archival housekeeping |
| [`active/public-directory-listings/`](active/public-directory-listings/) | Vendor plugin-directory listings (Claude plugin directory, Claude Desktop extension directory, Cursor Marketplace; Codex blocked by OpenAI policy): tool titles, MCPB 0.3 manifest, Cursor manifests, privacy and security policies, submission packet | Merged to `main` in PR #97; v3.12.0 is **not yet tagged** (tag must point at `4a143fc`, after PR #99). Vendor form sign-in is still Cayman's (`AGENTIC-2492`) |
| [`active/calendar-identity-2470/`](active/calendar-identity-2470/) | Duplicate Calendar.app display-name safety (AGENTIC-2470) | Merged with PR #94 (`f1264c6`) in `v3.11.8`; retained here pending archival housekeeping |

## Reference

| File | Purpose |
|------|---------|
| [`reference/roadmap-2026-07-10.md`](reference/roadmap-2026-07-10.md) | Forward roadmap: new tools, skills, enhancements, hardening backlog, documented macOS refusals |
| [`reference/id-first-refactor-spec.md`](reference/id-first-refactor-spec.md) | Shipped ID-first mutations + `allow_filter_scan` gate (v3.7.0) |
| [`reference/phase-3-annotation-matrix.md`](reference/phase-3-annotation-matrix.md) | Canonical tool-annotation matrix (`server.py` `ToolAnnotations` presets) |
| [`reference/apple-mail-plugin-robustness-goal-2026-05-22.md`](reference/apple-mail-plugin-robustness-goal-2026-05-22.md) | Whole-plugin robustness goal and completion contract |
| [`reference/robustness-backlog-2026-05-22.md`](reference/robustness-backlog-2026-05-22.md) | Robustness backlog sidecar (Phases 1-4 shipped; Deferred items carried in the roadmap) |
| [`reference/phase-plan-3.1.7.md`](reference/phase-plan-3.1.7.md) | Historical release sequencing after 3.1.6; verify against current source |
| [`reference/live-test-baseline-2026-05-21.md`](reference/live-test-baseline-2026-05-21.md) | Live perf baseline (production vs light account) |
| [`reference/mcp-mailbox-timeout-audit-2026-05-22.md`](reference/mcp-mailbox-timeout-audit-2026-05-22.md) | Timeout audit reference |

## Archive

See [`archive/README.md`](archive/README.md).

| Bucket | Contents |
|--------|----------|
| [`archive/2026-05-21/`](archive/2026-05-21/) | Shipped 3.1.6 audit and planning artifacts |
| [`archive/2026-05/`](archive/2026-05/) | May workstreams (whose-elimination, robustness audits, scalability hardening) |
| [`archive/2026-06/shipped/`](archive/2026-06/shipped/) | Shipped June workstreams (Codex plugin setup, MCP registration incident, doc cleanup, agent-guidance audit, draft-verification simplification) |
| [`archive/2026-06/issues/`](archive/2026-06/issues/) | Resolved June issue trackers and investigation notes |
| [`archive/2026-07/shipped/`](archive/2026-07/shipped/) | Apple Calendar surface (v3.10.0), manifest-release-hardening (parked), marketplace offline release candidate (v3.11.3), and Cursor marketplace source candidate (v3.11.4) |
| [`archive/2026-07/shipped/agentic-1277-compose-draft-verification/`](archive/2026-07/shipped/agentic-1277-compose-draft-verification/) | Compose-smoke identity verification and bounded reply-state performance hardening, integrated for v3.11.5 |
| [`archive/2026-07/shipped/branch-review-v3.11.3/`](archive/2026-07/shipped/branch-review-v3.11.3/) | v3.11.3 branch review and completed fix plan; deferred product decisions remain recorded in its archived forward queue |
| [`archive/2026-07/shipped/v3.11.5-consolidated-release/`](archive/2026-07/shipped/v3.11.5-consolidated-release/) | v3.11.5 consolidation, merged and tagged; successor distribution evidence is tracked in the active v3.11.6 lane |
| [`archive/2026-07/shipped/reply-state-annotation/`](archive/2026-07/shipped/reply-state-annotation/) | Automatic reply-state annotation, shipped in v3.11.0 (PR #73) |
| [`archive/2026-07/shipped/agentic-1214-reply-fixes/`](archive/2026-07/shipped/agentic-1214-reply-fixes/) | Native-reply hardening, shipped in v3.11.2 (PR #75) |
| [`archive/2026-08/shipped/identity-gate-and-search-date-window/`](archive/2026-08/shipped/identity-gate-and-search-date-window/) | Enforced public-repo identity gate (AGENTIC-2358), `search_emails` subject-fast-path date-window fix (AGENTIC-2356), bounded-scan fallback conversion (AGENTIC-2355) — merged 2026-08-19 as PR #90 (`dc56b3c`) |
| [`archive/2026-08/shipped/trash-safety-and-zero-bounds/`](archive/2026-08/shipped/trash-safety-and-zero-bounds/) | Eleven silent-failure defects: trash `dry_run`/`max_deletes`/date-window, draft-cleanup deletes, `move_email` bounds, dashboard silent-zero, search bound validation (AGENTIC-2359) — merged 2026-08-19 as PR #91 (`ab04304`); destructive paths still not live-verified |
| [`archive/2026-08/shipped/silent-error-channels/`](archive/2026-08/shipped/silent-error-channels/) | Bare-AppleScript-`try` defect class, two file-clobber paths, timeout validation, and the package-wide lint ratchet (AGENTIC-2363 family, 2361, 2369, 2374) — merged 2026-08-19 as PR #92 (`b30d9c4`) |
