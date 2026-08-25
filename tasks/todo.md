# Active Pointer: apple-mail-mcp

**Tasks layout:** Agents MUST follow [`tasks/CLAUDE.md`](CLAUDE.md) § Agent requirements (`active/` · `reference/` · `archive/` only; local gates enforce).

**Current branch:** `chore/public-directory-listings` (base: `origin/main` @ `9ba502e`). It carries **v3.12.0**: everything a vendor plugin-directory reviewer checks before admitting the plugin, plus the copy-ready submission packet. Lane folder: [`active/public-directory-listings/`](active/public-directory-listings/). Not yet merged, not yet tagged.

**Main state:** **v3.11.9 is tagged and pushed** on `9ba502e`, the merge of PR #96 (cross-process Mail lock). v3.11.8 (PR #94, `f1264c6`) closed the post-3.11.7 defect audit. GitHub Release `v3.11.9` carries the `.mcpb`, `.zip`, and `.plugin` artifacts; twelve repo topics and private vulnerability reporting are live.

## What this branch does

Goal: `apple-mail` visible inside each client's own plugin browser (Claude Desktop and Cowork "Browse plugins", Claude Code `/plugin` Discover, Cursor Customize), not only through the GitHub marketplace URL.

- Every tool carries a human-readable `title` beside its `readOnlyHint` / `destructiveHint` / `idempotentHint` annotations.
- `apple-mail-mcpb/manifest.json` moved to MCPB `manifest_version` 0.3 with `compatibility`, `privacy_policies`, `documentation`, and `support`; `_check_mcpb_directory_contract` enforces it.
- Root `.cursor-plugin/marketplace.json`, full `plugin/.cursor-plugin/plugin.json`, `plugin/assets/logo.svg`, `plugin/README.md`, payload classification, and a Cursor marketplace validator.
- `PRIVACY.md`, `SECURITY.md`, README "Privacy Policy" and "Support" sections, release-link install steps.
- [`submission-packet-2026-08-24.md`](active/public-directory-listings/submission-packet-2026-08-24.md): per-channel form answers; [`handoff-2026-08-24.md`](active/public-directory-listings/handoff-2026-08-24.md): verification and the ordered post-merge steps.

**Channel facts (verified 2026-08-24):** Anthropic's open door is the Claude plugin directory form (lands in `anthropics/claude-plugins-community` and claude.com/plugins); `claude-plugins-official` is partner-only. The Claude Desktop extension directory is a separate Google Form that takes the `.mcpb`. Cursor Marketplace is a form with manual review. OpenAI's Plugins Directory accepts only public HTTPS MCP servers, so a local stdio server cannot be listed there yet; the public GitHub marketplace path for Codex keeps working.

## Gate and verification state

`bash tools/gates/source-release-gate.sh` is the proof: fatal `ruff check` / `ruff format --check` / `mypy --strict`, identity scan, module line budget, artifact rebuild plus byte parity, `mcpb` validate, `claude plugin validate --strict`, tasks layout, `validate-tree HEAD`, no tracked or index drift, then the pre-push stamp. Results are recorded in the PR body and the lane handoff, not here.

**Verify through `.venv/bin/apple-mail`, never through MCP tools.** MCP tools execute the *installed* plugin, which returns a clean answer from stale code until the marketplace promotes a payload.

**Next action:** **await Cayman's merge approval**; merging is founder-gated and no approval has been given. After merge, from clean `main`: `bash tools/gates/source-release-gate.sh`, `bash tools/gates/create-release-tag.sh --confirm-create` (the stamp binds HEAD's SHA, so stamp *after* the merge), `git push origin v3.12.0`, GitHub Release with the three artifacts, `bash tools/gates/marketplace-handoff.sh v3.12.0`, then file the three forms from the packet (each needs a signed-in browser session).

**Roadmap:** [`reference/roadmap-2026-07-10.md`](reference/roadmap-2026-07-10.md). Next three builds: port `get_email_source` forward, add junk + colored-flag actions to `update_email_status`, then the typed-`AppleScriptError` error-contract pass.

## Open lanes

**Founder decisions from this lane.** PyPI `mcp-apple-mail` belongs to the original upstream author (latest 3.2.0), so `server.json` and the README badge claim a package Agentic Assets cannot publish; the MCP Registry entry waits on a package-name decision. The Codex directory needs an OpenAI partner lane or local-MCP support. The dashboard template loads the MCP Apps SDK from a public CDN, the one network fetch a reviewer can flag.

**Carried forward from the audit, not fixed here.** **AGENTIC-2421** (promote the current payload; the installed plugin lags `main`), **AGENTIC-2422** (two bounded-in-practice raw enumerations still allowlisted in the `whose` lint), **AGENTIC-2423** (the compile hook cannot reach `reply_runner.py` or `attachments.py`), **AGENTIC-2371** and **AGENTIC-2357** (audit findings). Highest-value remaining by impact: AGENTIC-2372 (`get_awaiting_reply` lists threads that were already answered), AGENTIC-2373 (thread export prints `Exported: 0` on localized/Exchange accounts), AGENTIC-2376 (mutation tools discard partial-work output on error), AGENTIC-2377. AGENTIC-2375 is a founder policy decision on whether file-writing tools should refuse paths outside the home directory. AGENTIC-781's human-operated native-reply and attachment-contract checks stay open; AGENTIC-1093 and AGENTIC-842 remain founder-controlled; AGENTIC-1191 needs a fresh sanitized reproduction before implementation.

**Native reply, live-measured 2026-08-24.** [`live-timing-and-frontmost-2026-08-24.md`](active/native-reply/live-timing-and-frontmost-2026-08-24.md) is the current note for this lane. Shipped: Mail is now polled to the front before the reply command and on every guard attempt (`REPLY_MAIL_NOT_FRONTMOST`), live-proven with another app deliberately holding the front. Measured over four successful live replies (1, 1, 21, 39 chunks): **0.70 s per chunk over 34.2 s of fixed overhead**, R2 0.98. The timeout projection models 1.0 s/chunk, so it over-projects by ~43% and the margin stays positive at every admissible length — a 3,060-char reply used 41% of its budget. `_NATIVE_TYPING_FIXED_OVERHEAD_SECONDS` was corrected 20 → 35 to match the measurement (the 30 s slack had been covering the gap), and `NativeReplyTimeoutCalibrationTests` now pins the constants against the live numbers instead of deriving expectations from the constants under test. Open, in priority order: (1) `REPLY_BODY_MISMATCH` is a normalizer ordering bug — `flattenForCompare` strips line breaks before folding paragraph starts, so an autocapitalized paragraph after a non-terminal-punctuation line fails a case-sensitive compare; (2) ~30.8 s of the 34.2 s fixed overhead is un-localized Mail/AX work and is the only real latency target. `TYPING_CHUNK_SIZE` stays at **80**: fixed overhead dominates, so a larger chunk saves nothing on the short replies agents actually send, while `chunk_count` gates `REPLY_BODY_TYPING_BUDGET_EXCEEDED` (250 would triple the accepted-body ceiling) and 250 sits only 1.3-1.9x below the observed keystroke-truncation floor.

**Live verification, needs Cayman.** [`active/native-reply/`](active/native-reply/): native-reply and attachment-contract TO-TEST items that cannot be mocked, plus the destructive-path gate (`manage_trash`, `update_email_status` have never run against real mail under the post-3.11.7 code; the `empty_trash` safety proof is static). Closeout in [`native-reply-attachment-closeout-2026-08-10.md`](active/native-reply/native-reply-attachment-closeout-2026-08-10.md); acceptance matrix in [`native-reply-attachment-forward-queue-2026-08-10.md`](active/native-reply/native-reply-attachment-forward-queue-2026-08-10.md). Disposable fixture only; only RFC-backed reply identity may enter the guarded cleanup test.

**Planning, awaiting sign-off.** [`active/id-first-search-retirement/`](active/id-first-search-retirement/): v4 fuzzy-selector retirement; decision brief ready; also owns the open `allow_filter_scan` product decision for `move_email` / `update_email_status` / `manage_trash`.

**Research complete, implementation not started.** [`active/fast-search-index/`](active/fast-search-index/): index-backed metadata search measured against a live 87K-message `Envelope Index` (AGENTIC-2345); Phase 0 (AGENTIC-2344) shipped in v3.11.7. Read section 10 of the research note before implementing.

**Distribution evidence open.** [`active/central-marketplace-source-contract/`](active/central-marketplace-source-contract/) (shared Marketplace admission is a separately authorized external gate) and [`active/v3.11.6-cursor-adapter/`](active/v3.11.6-cursor-adapter/) (local 41-tool Cursor Agent acceptance passed; Cursor marketplace/UI admission is what this lane's form submission is for).

**Shipped, pending archival housekeeping.** [`active/post-3.11.7-defect-audit/`](active/post-3.11.7-defect-audit/) and [`active/calendar-identity-2470/`](active/calendar-identity-2470/) merged in PR #94 and shipped in v3.11.8; [`active/linear-backlog-2026-07-31/`](active/linear-backlog-2026-07-31/) shipped in PR #83. [`active/v4-performance-consolidation-2026-05-27/`](active/v4-performance-consolidation-2026-05-27/) has not moved since 2026-05-27. All are over the 30-day archive threshold.

**Other open branch (no task folder).** `fix/github-issues-mcp-hardening-20260617` holds an unmerged `get_email_source` tool (raw RFC822/MIME by id). The roadmap flags porting it forward as the top next build; the branch can be dropped once ported.

**Caveats (carried, not blockers):**
- Native reply needs Mail window focus + Accessibility permission (`native_format=False` avoids it; returns `REPLY_WINDOW_FOCUS_FAILED` when focus cannot be acquired).
- Logo not repainted in the reopened draft editor = native Mail behavior (not our bug); SEND-level confirmation still pending.

**Recently shipped** (detail under [`archive/`](archive/)): v3.11.9 cross-process Mail lock, v3.11.8 post-3.11.7 defect audit, v3.11.7 and its three merges, v3.11.0 automatic reply-state annotation, v3.10.0 Apple Calendar surface, v3.9.1 module line-budget splits, v3.8.0 native-format reply drafts.
