# Agent Live Testing (Apple Mail MCP)

Use the repo-owned CLI (`.venv/bin/apple-mail`) to verify changes against real Mail.app immediately after edits. This bypasses the slow generated mcporter wrapper and calls the same Python tool functions as the MCP server.

> **This repo is PUBLIC, and everything below reads a real mailbox.** Output from these commands contains real addresses, subjects, account UUIDs, and absolute paths. Never paste it verbatim into a commit, doc, test fixture, PR comment, or Linear issue. Report counts, timings, and redacted samples instead. Root [`AGENTS.md`](../AGENTS.md) § This repo is PUBLIC has the pre-commit diff scan.

> **Verify through `.venv/bin/apple-mail`, not through MCP tools.** The repo CLI is an editable install (`pip install -e .`), so it runs the working tree. The Apple Mail **MCP tools and the generated wrapper run the installed plugin snapshot**, which is a *copy* of `plugin/` taken at install/generation time and is stale the moment you edit a source file. Verifying a working-tree fix through an MCP tool tests the old code and can return a clean, confidently wrong answer. Before trusting any wrapper or MCP result as evidence about your change, either re-sync the install (see **Regenerate wrapper** under § Generated MCP wrapper probes) or reproduce it through the repo CLI.

## Setup

```bash
cd /path/to/apple-mail-mcp
python3 -m venv .venv
.venv/bin/pip install -e . pytest
```

Optional but recommended for faster iteration:

```bash
export DEFAULT_MAIL_ACCOUNT="Your Mail Account Name"
```

When set, `perf-test`, `quick-check`, and `smoke-test` use this account instead of the first configured account.

## Permissions (macOS)

Mail.app must be configured and the terminal (or IDE) running the CLI needs:

- **Automation** — allow control of Mail
- **Mail Data Access** — allow reading mail data

If a command hangs or returns permission errors, open **System Settings → Privacy & Security** and grant access to Terminal, iTerm, or Cursor.

## Safe commands (read-only / dry-run)

### Test profiles

| Profile | Account | Use |
|---------|---------|-----|
| **light** | `ai.openclaw` (~9 mailboxes) | Fast regression after edits |
| **production** | `Cayman - Agentic Assets` (`cayman@agenticassets.ai`) | Realistic large-mailbox perf gate before merge |

`production` is the **default** threshold profile (`DEFAULT_PERF_PROFILE` in
`cli/constants.py`); pass `--profile light` to opt down for a small account.
The profile only selects thresholds — it does not select an account. Set the
account separately.

Set the account once:

```bash
export DEFAULT_MAIL_ACCOUNT="Cayman - Agentic Assets"   # production gate
# export DEFAULT_MAIL_ACCOUNT="ai.openclaw"             # light smoke
```

### Batteries

| Command | What it exercises |
|---------|-------------------|
| `quick-check` | metadata + no-hit search + inbox (~30s target) |
| `perf-test --quick` | same as `quick-check` |
| `perf-test` | full battery: dry-run move/trash, overview, bad-account fast-fail, dashboard metadata |
| `perf-test --include-analysis --allow-heavy-mail-scan` | heavy opt-in battery + needs-response, awaiting-reply, top-senders, statistics |
| `perf-test --profile light` | opt out of the production thresholds for a small account (overview 10s, no-hit search 3s) |
| `smoke-test` | accounts, inbox, no-hit search, invalid-account error, draft-safe send block |

`draft-verify-smoke` is **not** in this table: it writes. See § Opt-in feature checks.

Add `--verbose-sensitive` to `perf-test` / `quick-check` to include account names in perf samples (default output redacts them).

`--include-analysis` is intentionally blocked unless paired with `--allow-heavy-mail-scan`. Those probes are bounded in code, but they still touch enough Mail.app message headers that a large account may fetch remote state. Routine agent testing should use `quick-check`, `smoke-test`, or individual probes with small limits.

### Individual safe probes

```bash
.venv/bin/apple-mail accounts --json
.venv/bin/apple-mail addresses --json
.venv/bin/apple-mail mailboxes --account "$DEFAULT_MAIL_ACCOUNT" --json
.venv/bin/apple-mail unread --account "$DEFAULT_MAIL_ACCOUNT" --summary --json
.venv/bin/apple-mail inbox --account "$DEFAULT_MAIL_ACCOUNT" --limit 2 --json
.venv/bin/apple-mail search --account "$DEFAULT_MAIL_ACCOUNT" --query NO_SUCH_SUBJECT_APPLE_MAIL_CLI_SMOKE_20991231 --json
.venv/bin/apple-mail show --account "$DEFAULT_MAIL_ACCOUNT" --id 12345 --no-content --json
.venv/bin/apple-mail overview --account "$DEFAULT_MAIL_ACCOUNT" --format compact --no-mailboxes --no-recent
.venv/bin/apple-mail needs-response --account "$DEFAULT_MAIL_ACCOUNT" --days 2
.venv/bin/apple-mail awaiting-reply --account "$DEFAULT_MAIL_ACCOUNT" --days 7
.venv/bin/apple-mail top-senders --account "$DEFAULT_MAIL_ACCOUNT" --days 30
.venv/bin/apple-mail statistics --account "$DEFAULT_MAIL_ACCOUNT" --scope account_overview --days 2
.venv/bin/apple-mail move-dry-run --account "$DEFAULT_MAIL_ACCOUNT" --to Archive --subject NO_SUCH_SUBJECT_APPLE_MAIL_CLI_SMOKE_20991231
.venv/bin/apple-mail trash-dry-run --account "$DEFAULT_MAIL_ACCOUNT" --subject NO_SUCH_SUBJECT_APPLE_MAIL_CLI_SMOKE_20991231
.venv/bin/apple-mail drafts list --account "$DEFAULT_MAIL_ACCOUNT"
.venv/bin/apple-mail drafts list --account "$DEFAULT_MAIL_ACCOUNT" --hide-empty
.venv/bin/apple-mail drafts cleanup-empty --account "$DEFAULT_MAIL_ACCOUNT"   # dry-run preview; add --execute to delete
.venv/bin/apple-mail search --account "$DEFAULT_MAIL_ACCOUNT" --mailboxes "INBOX,Sent" --query NO_SUCH_SUBJECT_APPLE_MAIL_CLI_SMOKE_20991231 --json
```

### Generated MCP wrapper probes

The generated wrapper (`apple-mail`, currently mcporter) is useful for parity
checks, but its flags are not identical to the repo CLI. Treat the repo CLI as
the canonical fast iteration surface, then spot-check wrapper commands agents
will actually invoke.

**The wrapper does not run your working tree.** It runs the `plugin/` copy
under `$APPLE_MAIL_CLI_HOME` and embeds tool schemas captured when the CLI was
generated. Until you re-run the rsync + regenerate sequence under
**Regenerate wrapper** below, every wrapper result reflects the previously
installed version, not your edits. The same is true of the Apple Mail MCP tools inside an agent host,
which load the installed plugin. Use these commands to check what agents
currently see; use the repo CLI to check what your change does.

```bash
apple-mail --help
apple-mail -o json list-accounts
apple-mail -o json list-mailboxes --account "$DEFAULT_MAIL_ACCOUNT" --include-counts false
apple-mail -o json list-inbox-emails --account "$DEFAULT_MAIL_ACCOUNT" --max-emails 2 --output-format json
apple-mail -o json search-emails --account "$DEFAULT_MAIL_ACCOUNT" --subject-keyword NO_SUCH_SUBJECT_APPLE_MAIL_CLI_SMOKE_20991231 --limit 1 --output-format json
apple-mail -o json get-inbox-overview --raw '{"account":"'"$DEFAULT_MAIL_ACCOUNT"'","output_format":"json","compact":true,"include_mailboxes":false,"include_recent":false,"include_suggestions":false}'
```

#### `--raw` examples for advanced wrapper options

mcporter embeds tool schemas at generation time; some tools expose only a
subset of parameters as named flags. Use `--raw <json>` to pass the full
parameter set verbatim. These are copy-paste ready — set
`DEFAULT_MAIL_ACCOUNT` first.

```bash
# Full inbox overview with all blocks suppressed → metadata-only JSON dict.
apple-mail -o json get-inbox-overview --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "output_format":"json",
  "compact":true,
  "include_mailboxes":false,
  "include_recent":false,
  "include_suggestions":false
}'

# Account statistics scoped to last 7 days; JSON dict with mailbox_totals.
apple-mail -o json get-statistics --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "scope":"account_overview",
  "days_back":7,
  "output_format":"json"
}'

# Sender-stats scope (requires sender filter).
apple-mail -o json get-statistics --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "scope":"sender_stats",
  "sender":"alerts@example.com",
  "days_back":30,
  "output_format":"json"
}'

# Mailbox breakdown using Mail.app count APIs (no per-message scan).
apple-mail -o json get-statistics --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "scope":"mailbox_breakdown",
  "mailbox":"INBOX",
  "days_back":30,
  "output_format":"json"
}'

# Triage: emails likely needing a response. Defaults already exclude rows
# with was_replied_to=true or has_draft=true, and report skipped_replied_count
# / skipped_drafted_count; check_already_replied adds the legacy Sent-header
# scan as an extra verification layer.
apple-mail -o json get-needs-response --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "days_back":7,
  "max_results":20,
  "output_format":"json"
}'

# Same window, widened to see already-replied and already-drafted rows again.
apple-mail -o json get-needs-response --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "days_back":7,
  "max_results":20,
  "include_already_replied":true,
  "include_drafted":true,
  "output_format":"json"
}'

# Sent messages still awaiting a reply (header-based match).
apple-mail -o json get-awaiting-reply --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "days_back":7,
  "exclude_noreply":true,
  "max_results":20,
  "output_format":"json"
}'

# Top senders grouped by domain.
apple-mail -o json get-top-senders --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "mailbox":"INBOX",
  "days_back":30,
  "top_n":10,
  "group_by_domain":true,
  "output_format":"json"
}'

# Inbox dashboard JSON (UI-free metadata; safe for headless agents).
apple-mail -o json inbox-dashboard --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "include_preview":false,
  "max_total":20,
  "max_per_account":10,
  "output_format":"json"
}'

# full_inbox_export is disabled: expect an immediate structured
# UNBOUNDED_EXPORT_DISABLED refusal (no AppleScript runs; max_emails/batch_size
# are accepted for schema compatibility but ignored). Useful for confirming the
# refusal contract, not for exporting anything. For a real metadata/export
# pass, page with export_emails(scope="entire_mailbox", max_emails<=50, offset=N)
# or list_inbox_emails(max_emails<=50) instead.
apple-mail -o json full-inbox-export --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "mailbox":"INBOX",
  "fields":["subject","sender","date_received","read_status","message_id"],
  "max_emails":500,
  "batch_size":250,
  "output_format":"json"
}'

# Correspondent history export, including received and Sent-side messages.
apple-mail -o json export-emails --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "scope":"correspondent",
  "email_address":"person@example.com",
  "include_sent":true,
  "date_from":"2026-07-01",
  "max_emails":10,
  "format":"txt"
}'
```

Known wrapper checks to keep separate from manifest validation:

- `apple-mail --help` must expose critical read commands, especially `get-email-by-id`.
- Some wrapper commands only expose `--raw <json>` for advanced options.
- Repo CLI flags like `--output-format` may not exist on every wrapper command; use the wrapper help as the source of truth.
- `list_inbox_emails`, `get_statistics`, `get_inbox_overview`, `inbox_dashboard`, `get_needs_response`, `get_awaiting_reply`, and `get_top_senders` all return a Python `dict` for `output_format="json"` (not a JSON string). Through the generated wrapper the dict is rendered as JSON; through the MCP transport it crosses as a structured object.
- `reply_to_email(output_format="json")` is different from the read-only JSON tools: it is returned as a JSON string and is defined only for verified draft/open reply artifacts, not send mode.

**Wrapper command-surface check** (repo script; skips if no wrapper on PATH):

```bash
python3 tools/validators/check_wrapper_surface.py
```

**Regenerate wrapper** after adding MCP tools (mcporter embeds schemas at generation time):

```bash
APPLE_MAIL_CLI_HOME="${APPLE_MAIL_CLI_HOME:-$HOME/.local/share/apple-mail-cli}"
rsync -a --delete --exclude venv /path/to/apple-mail-mcp/plugin/ "$APPLE_MAIL_CLI_HOME/plugin/"
cd "$APPLE_MAIL_CLI_HOME"
npx mcporter@0.11.3 generate-cli --from ./apple-mail-cli.cjs --bundle apple-mail-cli.cjs
python3 /path/to/apple-mail-mcp/tools/probes/patch_mcporter_wrapper.py ./apple-mail-cli.cjs
./install.sh
python3 /path/to/apple-mail-mcp/tools/validators/check_wrapper_surface.py
```

`patch_mcporter_wrapper.py` is required with mcporter 0.11.3 because the
generated CLI otherwise reserves global `--timeout` for transport timeouts in
milliseconds. The patch renames the request flag to `--request-timeout-ms`, so
tool-level `--timeout` still reaches Apple Mail tools as seconds.

**Repo CLI vs wrapper naming:**

| Repo CLI | Generated wrapper |
|----------|-------------------|
| `show --id` | `get-email-by-id` |
| `inbox` | `list-inbox-emails` |
| `overview` | `get-inbox-overview` |
| `search` | `search-emails` |

## After each change

**Fast loop (~30–60s):**

```bash
.venv/bin/apple-mail quick-check --json
```

**Full performance gate:**

```bash
.venv/bin/apple-mail perf-test --account "$DEFAULT_MAIL_ACCOUNT" --profile production --json
```

**Capture and compare perf baselines:**

Use this before and after hot-path edits. The comparator is pure JSON and does
not touch Mail.app; the only live work is the two explicit `perf-test` captures.

```bash
.venv/bin/apple-mail perf-test --account "$DEFAULT_MAIL_ACCOUNT" --profile production --json > /tmp/apple-mail-baseline.json
.venv/bin/apple-mail perf-test --account "$DEFAULT_MAIL_ACCOUNT" --profile production --json > /tmp/apple-mail-current.json
.venv/bin/python tools/probes/compare_perf_results.py /tmp/apple-mail-baseline.json /tmp/apple-mail-current.json --max-regression-pct 0 --json
```

For routine local iteration, use a small positive budget such as
`--max-regression-pct 5`. For v4 hot-tool work, treat any p95 or live-case
regression as a redesign signal unless the phase plan explicitly records why
the tradeoff is acceptable.

**Honest analysis gate (expect failures until Phase 2 speed work):**

```bash
.venv/bin/apple-mail perf-test --include-analysis --allow-heavy-mail-scan --account "$DEFAULT_MAIL_ACCOUNT" --profile production --json
```

Exit code is non-zero if any threshold is breached.

### Thresholds (full `perf-test`)

| Case | Threshold |
|------|-----------|
| metadata (accounts + addresses + mailboxes) | `2000 + max(0, mailbox_count - 20) × 35` ms |
| no-hit search | < 3s light / < 4.5s production |
| inbox (limit 2) | < 5s |
| dry-run move | < 5s |
| dry-run trash | < 5s |
| overview (compact, metadata-only) | < 10s light / < 15s production |
| bad_account (invalid name fast-fail) | < 2s |
| dashboard_metadata (unread + recent, no preview) | < 5s |

**With `--include-analysis --allow-heavy-mail-scan`:**

| Case | Threshold |
|------|-----------|
| needs-response (days=2) | < 8s |
| awaiting-reply (days=7) | < 5s |
| top-senders (days=30) | < 5s |
| statistics account_overview (days=2) | < 12s |

Output is redacted by default: counts and char lengths only; account names, subjects, senders, and bodies are omitted unless `--verbose-sensitive` is set.

## Unit tests vs live Mail

Local CI-equivalent gates run the committed-identity scan + mocked pytest + manifest validation + **module line budget** (600 LOC warn, baseline regression fail) + the collected-test-count drift gate:

```bash
python3 tools/validators/validate_no_committed_identity.py   # run this before committing live-test output
bash tools/gates/validate_manifests.sh
python3 tools/validators/check_module_line_budget.py
.venv/bin/pytest tests/ -q -rw
```

Run the identity scan yourself any time you write down a live-test result. It
is also the first step of `dev-check.sh` on the `default` and `release` tiers,
because a leaked address or `/Users/<name>/…` path is the one failure a later
force-push cannot undo.

Detail: [`CLAUDE-conventions.md`](CLAUDE-conventions.md) § Module line budget · § Committed-identity gate.

Required checked-in hooks (identity scan + manifest drift + `tasks/` layout + repo-root hygiene + module budget + pytest + test-count drift; wrapper check when staged MCP tool files change):

```bash
bash tools/gates/install-git-hooks.sh   # every local or cloud checkout
test "$(git config --get core.hooksPath)" = ".githooks"
bash tools/gates/dev-check.sh             # manual equivalent
bash tools/gates/dev-check.sh surface     # always include wrapper check
```

Release packaging gate before commit/PR when `plugin/`, manifests, `pyproject.toml`, `requirements.txt`, zip, or MCPB surfaces changed:

```bash
bash tools/gates/dev-check.sh release
```

Live Mail verification is manual on macOS with Mail.app running.

## Opt-in feature checks (fixture-only)

These drills supplement the routine smoke batteries. Run them only against a
disposable/local fixture account and fixture messages, never client or founder
mail. They may read fixture content and create drafts, but they never send.
Use `--raw` with the generated wrapper (or the equivalent MCP call) because
these options are not necessarily exposed as named wrapper flags.

**These drills exercise the installed plugin, not the working tree.** The
wrapper and MCP paths below prove what an installed agent host will do. If the
point of the drill is to verify a change you just made, re-sync and regenerate
the wrapper first (**Regenerate wrapper** under § Generated MCP wrapper probes)
— otherwise a pass proves only that the *previous* version worked.

Set values only after locating a known disposable fixture. Use a unique test
subject and recipient address so a human can recognize the artifact. Do not use
subject search or a broad cleanup command to remove it.

```bash
export FIXTURE_ACCOUNT="Fixture Mail"
export FIXTURE_MAILBOX="All Mail"          # or an explicit Parent/Child path
export FIXTURE_MESSAGE_ID="12345"           # exact numeric id from search
export FIXTURE_RECIPIENT="fixture@example.test"
export FIXTURE_EXPORT_DIR="/tmp/apple-mail-eml-fixture"
```

### Repo-CLI draft verification (`draft-verify-smoke`)

The one write-path drill that runs the working tree directly. It creates a
single Drafts artifact in the named account, verifies it by exact persisted id,
and then either deletes that exact id or leaves it for inspection. One of
`--cleanup` / `--leave-draft` is required — there is no implicit default — and
the recipient defaults to an `example.invalid` address. It never sends. Run it
against a disposable fixture account only.

```bash
.venv/bin/apple-mail draft-verify-smoke --account "$FIXTURE_ACCOUNT" --cleanup --json
.venv/bin/apple-mail draft-verify-smoke --account "$FIXTURE_ACCOUNT" --leave-draft --json
```

Pass `--from-address` when the fixture account has multiple aliases; the tool
fails closed rather than guessing an identity.

### EML export and attachment limits

Run an exact-id EML export, preferably against a fixture with a small
attachment. Confirm the returned `message_id`, inspect that `message.eml`
preserves the fixture's RFC 822 headers, and confirm the reported attachment
saved/skipped counts. A deliberately over-limit fixture, if available, must be
reported as skipped: each file is limited to 25 MiB and each bounded export
batch to 100 MiB. Treat the local export directory as sensitive fixture data
and remove it under the fixture-account retention procedure.

```bash
apple-mail -o json export-emails --raw '{
  "account":"'"$FIXTURE_ACCOUNT"'",
  "scope":"single_email",
  "message_id":"'"$FIXTURE_MESSAGE_ID"'",
  "mailbox":"'"$FIXTURE_MAILBOX"'",
  "format":"eml",
  "include_attachments":true,
  "save_directory":"'"$FIXTURE_EXPORT_DIR"'"
}'
```

### Archived reply and guarded cleanup

With Mail visible and Accessibility granted to the wrapper host, make a native
draft reply to the fixture using `mailbox="$FIXTURE_MAILBOX"`. This exercises
special-mailbox lookup; for a duplicate nested leaf, use its exact
`Parent/Child` path. Inspect `draft_id_source` in the JSON response before any
cleanup. Only `persisted_header_identity` may enter the guarded-cleanup path,
after a fresh `verify_draft` read. A
`transaction_scoped_numeric_identity` is a successful same-operation iCloud
verification but is **verify-and-inspect only**: do not open, send, delete, or
retype it automatically. A guard mismatch must leave the draft untouched; after
a successful guarded delete, list Drafts and confirm the exact id is absent.
Never replace this sequence with subject-based cleanup.

A native reply is not fast, and slow is not the same as wedged. After the last
typed chunk the script polls the WebKit compose editor until the body has
landed before saving, on a budget that scales with `len(reply_body)` (see
`compose/constants.py` `TYPING_SETTLE_*`). A multi-thousand-character body can
legitimately spend tens of seconds in that drain. The tool's own AppleScript
timeout is projected from the same constants
(`compose/reply_typing_budget.py`), so let it return rather than killing the
run: interrupting mid-drain is what strands a compose window with the body
typed and unsaved.

```bash
apple-mail -o json reply-to-email --raw '{
  "account":"'"$FIXTURE_ACCOUNT"'",
  "message_id":"'"$FIXTURE_MESSAGE_ID"'",
  "mailbox":"'"$FIXTURE_MAILBOX"'",
  "reply_body":"Fixture native reply - do not send.",
  "mode":"draft",
  "native_format":true,
  "output_format":"json"
}'
```

Proceed only when the response reported `draft_id_source` as
`persisted_header_identity` and `verify_draft` returns current values. Set the
four variables below from that one response and make the guarded cleanup call.
Do not reuse values from an older list or a different draft. When the source is
`transaction_scoped_numeric_identity`, retain the fixture draft for manual
inspection and record the result instead.

```bash
export FIXTURE_DRAFT_ID="67890"
export FIXTURE_IN_REPLY_TO="fixture-source@example.test"
export FIXTURE_DRAFT_SUBJECT="Re: Fixture message"
export FIXTURE_DRAFT_TO="fixture@example.test"

apple-mail -o json manage-drafts --raw '{
  "account":"'"$FIXTURE_ACCOUNT"'",
  "action":"delete",
  "draft_id":"'"$FIXTURE_DRAFT_ID"'",
  "expected_in_reply_to":"'"$FIXTURE_IN_REPLY_TO"'",
  "expected_subject":"'"$FIXTURE_DRAFT_SUBJECT"'",
  "expected_to":"'"$FIXTURE_DRAFT_TO"'"
}'
```

### HTML compose subject and focus

Use a standalone fixture recipient and unique human-readable subjects (never
accept `__apple_mail_mcp_…` in a window title or Drafts row). All drills are
draft-only (`mode="draft"` or `mode="open"`); never send.

**Contract to prove manually in Mail.app:**

1. **Real subject, never the marker** — the compose window title and any saved
   Drafts row must show your requested subject, not `__apple_mail_mcp_…`.
   The marker is an internal pre-save window-binding token only.
2. **Restore before save** — after HTML paste, the tool sets the real subject on
   the outgoing message before the first `save` (attachment paths run proof
   after that save).
3. **No leading tab indent** — the first body line must not start with tab
   characters (focus may Tab only while Accessibility reports a header field).
4. **Failed compose cleanup** — on `COMPOSE_BODY_FOCUS_FAILED`,
   `HTML_COMPOSE_SUBJECT_RESTORE_FAILED`, or `DRAFT_ATTACHMENT_PROOF_FAILED`,
   inspect Drafts and open compose windows: do not leave a fixture artifact
   whose subject still contains `__apple_mail_mcp_`. Focus failure deletes the
   empty outgoing fixture; it must not persist a blank draft under the real
   subject. Marker cleanup is not a saved draft. Record the structured error
   instead of retrying through a windowless path.

Mocked ordering is locked in `tests/compose/test_html_compose_subject.py`,
`tests/compose/test_html_compose_focus.py`, and
`tests/compose/test_attachment_draft_contract.py`.

#### (a) HTML draft without attachment — real subject in Drafts

```bash
apple-mail -o json compose-email --raw '{
  "account":"'"$FIXTURE_ACCOUNT"'",
  "to":"'"$FIXTURE_RECIPIENT"'",
  "subject":"Fixture HTML subject - do not send",
  "body":"Fixture HTML body.",
  "body_html":"<p><strong>Fixture HTML body.</strong></p>",
  "mode":"draft"
}'
```

After success: open the saved draft in Mail (or list Drafts in the fixture
account). Confirm the subject is exactly `Fixture HTML subject - do not send`,
the HTML body and signature are present, and the first line has no leading tab
indent. Remove the draft by exact id in the fixture account only.

#### (b) HTML + attachment, saved open for review — real subject in window title

Use a small disposable attachment under `/tmp` or the fixture export dir.

```bash
export FIXTURE_ATTACH="/tmp/apple-mail-fixture-note.pdf"
printf 'fixture' > "$FIXTURE_ATTACH"

apple-mail -o json compose-email --raw '{
  "account":"'"$FIXTURE_ACCOUNT"'",
  "to":"'"$FIXTURE_RECIPIENT"'",
  "subject":"Fixture HTML subject restore attach - do not send",
  "body":"Hi fixture,",
  "body_html":"<p>Hi fixture,</p>",
  "attachments":"'"$FIXTURE_ATTACH"'",
  "mode":"open"
}'
```

Before editing or sending: the **open compose window title** must show
`Fixture HTML subject restore attach - do not send`, not a `__apple_mail_mcp_`
token. Confirm the attachment is present, HTML rendered, and the first body
line is not tab-indented. Close without sending; delete any saved draft in the
fixture account by exact id if Mail persisted one.

#### (c) Plain attachment path (no `body_html`) — same focus and subject rules

Attachment-only standalone drafts still paste through the HTML writer and the
same `focusComposeBody` handler.

```bash
apple-mail -o json compose-email --raw '{
  "account":"'"$FIXTURE_ACCOUNT"'",
  "to":"'"$FIXTURE_RECIPIENT"'",
  "subject":"Fixture attachment subject - do not send",
  "body":"Plain authored body for attachment path.",
  "attachments":"'"$FIXTURE_ATTACH"'",
  "mode":"draft"
}'
```

Confirm the same subject and indent checks as (a). Expect attachment
verification fields in the tool output when readback succeeds.

#### (d) Error path — no marker subject left behind

If (a)–(c) return `COMPOSE_BODY_FOCUS_FAILED` or
`HTML_COMPOSE_SUBJECT_RESTORE_FAILED`, list Drafts in the fixture account and
scan open compose windows. **Pass** only when no row or window still shows a
subject containing `__apple_mail_mcp_`, and when focus failure did not leave a
blank real-subject draft. Follow-up `cleared` / `deleted` / `outgoing_ok` only
means no leftover marker remains; the tool must fail closed and must not
return `Email saved as draft (HTML)`. Locked by
`test_sweep_cleared_is_not_proof_of_a_real_subject_draft` and
`test_outgoing_ok_followup_is_not_a_saved_draft` in
`test_html_compose_subject.py`.

### Calendar participant filter

On a disposable calendar with a known attendee, issue a one-day bounded
`list_events` request with both `query` and `participant_query`. Confirm only
events matching both filters remain, that `engine` explains whether EventKit
was used, and that attendee details are absent from the list response. Organizer
matches are expected only on the EventKit path.

Repo CLI (runs the working tree; `--timeout` bounds each Calendar engine call):

```bash
.venv/bin/apple-mail calendar-events \
  --calendar "Fixture Calendar" \
  --days 1 \
  --query fixture \
  --participant-query "fixture@example.test" \
  --limit 10 --timeout 5 --json
```

Generated wrapper (runs the installed plugin):

```bash
apple-mail -o json list-events --raw '{
  "calendar":"Fixture Calendar",
  "days_ahead":1,
  "query":"fixture",
  "participant_query":"fixture@example.test",
  "limit":10,
  "output_format":"json"
}'
```

Calendar reads need EventKit consent. `.venv/bin/apple-mail calendar-grant` is
the human-run helper that surfaces the macOS prompt; it is interactive and not
part of any automated battery.

## MCP config for agents

### MCP env vars

The Claude plugin starts the server via `mcpServers.apple-mail` → `${CLAUDE_PLUGIN_ROOT}/start_mcp.sh` (see `plugin/.claude-plugin/plugin.json`). Optional environment variables:

| Variable | Purpose |
|----------|---------|
| `DEFAULT_MAIL_ACCOUNT` | Exact Mail account name (e.g. `Work`, `Gmail`). When set, most tools default to this account instead of fanning out across every account — largest perf win on multi-account mailboxes. |
| `DEFAULT_MAIL_SIGNATURE` | Exact Apple Mail signature name to apply by default to compose, reply, and forward drafts (e.g. `TU`). |
| `USER_EMAIL_PREFERENCES` | Free-text workflow hints injected into preference-aware tool docstrings (e.g. "Prefer Archive over Trash, cap lists at 25"). |

Example `env` block for a manual MCP config (also emitted by `apple-mail mcp-config` if you add `env` yourself):

```json
"env": {
  "DEFAULT_MAIL_ACCOUNT": "Work",
  "DEFAULT_MAIL_SIGNATURE": "TU",
  "USER_EMAIL_PREFERENCES": "Prefer Archive over Trash; default triage window 7 days"
}
```

Full setup examples: [README — Default Mail Account & User Preferences](../README.md#default-mail-account).

Generate draft-safe MCP wiring from the repo checkout:

```bash
.venv/bin/apple-mail mcp-config --repo "$(pwd)"
```

This adds `--draft-safe` so send tools stay blocked during agent testing.

## Plugin workflow skills (agent UX)

The Claude Code plugin bundles **eleven** workflow skills under `plugin/skills/`. They complement live CLI testing: skills guide **tool selection and safety**; this doc guides **verification**.

| Agent task | Start with skill | Live CLI probes (examples) |
|------------|------------------|----------------------------|
| Daily “what needs reply?” | `inbox-triage` | `needs-response`, `awaiting-reply`, `overview --format compact` |
| Folder mess / taxonomy | `mailbox-taxonomy` | `mailboxes --json`, `top-senders`, `statistics --scope account_overview` |
| Bulk archive / cleanup | `email-archive-cleanup` | `move-dry-run`, `trash-dry-run`, `search` previews before writes |
| Draft / reply | `email-drafting` | `draft` (quiet default), `draft --open` (saved-open review); reply/forward should use `message_id` when known; send blocked in draft-safe |
| MCP misbehaving / slow | `apple-mail-operator` | `quick-check`, `accounts`, narrow `search` with `recent_days` |

Full skill map: [`plugin/skills/CLAUDE.md`](../plugin/skills/CLAUDE.md). User install copy: [`README`](../README.md) § Claude Code Skills.

When editing skills, run **`plugin-dev:skill-reviewer`**. When editing manifests, package/dependency files, release artifacts, or bundled skill marketing copy, run **`bash tools/gates/dev-check.sh release`** and **`plugin-dev:plugin-validator`**. Use **`bash tools/gates/validate_manifests.sh`** for quick inner-loop checks.
