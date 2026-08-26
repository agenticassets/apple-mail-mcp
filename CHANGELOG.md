# Changelog

All notable changes to **apple-mail-mcp** (PyPI: `mcp-apple-mail`) are documented
here. The plugin/MCPB/marketplace versions track this file.

## Unreleased

## 3.12.1 - 2026-08-26

- **Calendar.app reads use stable object references on hosts where
  `calendarIdentifier` fails.** Calendar listing now recovers opaque IDs from
  Calendar object references, passes those selectors through bounded reads and
  writes, and uses exact-name fallback only when the live name is unique.
  Duplicate identifier-less names fail closed, explicit read failures are
  structured, and EventKit listings expose source metadata when available.

- **Reply-state JSON now distinguishes Mail metadata from verified Sent
  threading.** Primary read/triage rows retain `was_replied_to` for
  compatibility and duplicate that raw, read-only Mail property as
  `mail_was_replied_to`. They also expose nullable `has_sent_reply` and a
  nullable composite `reply_state`. Exact In-Reply-To/References matches are
  true even when the bounded Sent scan is partial; a nonmatch is false only
  when the scan is complete, otherwise null. `sent_reply_scan` reports
  status/scanned/total/truncated/errors. This improves MCP triage and does not
  set Mail's answered flag or promise that Mail's reply arrow will appear.

## 3.12.0 - 2026-08-25

Two lanes. The first is the directory listing work: metadata, documentation,
and validators that a vendor plugin-directory reviewer (Claude plugin
directory, Claude Desktop extension directory, Cursor Marketplace) checks
before admitting a plugin. The second is an adversarial review of the drafting
surface, weighted toward the native reply path, which did change tool
behavior — see **Drafting hardening** below.

### Directory listings

- **Every tool now carries a human-readable `title`.** Host plugin browsers and
  directory reviewers show `title` beside the tool name; the 41 tools had only
  their snake_case names. Titles sit on the `@mcp.tool` decorator next to the
  existing `readOnlyHint` / `destructiveHint` / `idempotentHint` annotations,
  and the registry test fails if any tool lacks one.
- **The Claude Desktop bundle manifest moved from the deprecated
  `dxt_version: "0.1"` to MCPB `manifest_version: "0.3"`,** adding
  `compatibility.platforms: ["darwin"]`, `compatibility.runtimes.python:
  ">=3.13,<3.14"`, `privacy_policies`, `documentation`, and `support`. The
  desktop extension directory rejects a submission missing any of these.
  `_check_mcpb_directory_contract` in `tools/manifest_checks/install_contracts.py`
  enforces them on every commit.
- **Cursor Marketplace manifests.** A root `.cursor-plugin/marketplace.json`
  (the file Cursor reads for a repository submission) plus a full
  `plugin/.cursor-plugin/plugin.json` with `displayName`, `author`, `license`,
  `homepage`, `repository`, `keywords`, `category`, `logo`, and `skills`. New
  `plugin/assets/logo.svg` and `plugin/README.md` ship inside the plugin
  payload; `distribution/marketplace-payload.json` classifies them and
  `tools/manifest_checks/cursor.py` validates the marketplace file.
- **`PRIVACY.md` and `SECURITY.md`** at the repository root, with README
  "Privacy Policy" and "Support" sections. Every directory form asks for a
  privacy-policy URL and a support channel; the MCPB manifest and the bundle
  README point at these files. Cowork and MCPB install steps in the README now
  link the latest GitHub Release instead of a local build.

### Native reply: the truncation defect, and three the review found on top of it (2026-08-25)

- **An Accessibility count of zero no longer aborts on a single sample.** The native-reply preflight
  read Mail's window count once and refused on zero, with remediation naming a sleeping display or a
  lapsed Accessibility grant. Measured on Darwin 25.5: Accessibility reports **0 windows for Mail
  whenever Mail sits on a different Space** — which any full-screen app creates — while Mail's own
  scripting dictionary reports 8 at the same moment, and `frontmost` reads true for ~0.3 s before the
  Space transition finishes and the count catches up. A healthy reply aborted at 0.79 s inside that
  gap. The count is now polled until a zero holds, the abort detail carries **Mail's own window count
  beside the Accessibility one** (which is what separates "Mail has no window" from "Accessibility
  cannot see Mail's windows"), and the remediation leads with the Space case instead of sending the
  user to System Settings. It also no longer suggests restarting Mail, which never was the fix.
- **The typed reply body is no longer truncated at save.** Every observed failure was a clean correct
  prefix followed by Mail's quote, with no substitution anywhere — so autocorrect was not the cause.
  Every cut also landed mid-chunk, not on a chunk boundary. `keystroke` returns when events are
  posted rather than processed, and the typing loop skips its inter-chunk delay after the final
  chunk, so the script went from the last keystroke straight to `save` and whatever WebKit had not
  drained never reached the draft: 216 characters lost at chunk size 120, 433 at 160, on
  2,400-character bodies. The loop now polls the editor's own `AXValue` before reporting success —
  never fatal, since the case-sensitive verification against the saved draft still decides
  correctness.
- **That wait now scales with the body, and a flat one was the other half of the defect.** A flat 6 s
  budget only moved the boundary: a 2,400-character body at chunk size 300 drained in time, while a
  5,000-character body at the same chunk size saved a clean 3,179-character prefix and lost the
  remaining 1,821. The identical run passed at a 50 s budget, so **the tail was late, not dropped** —
  which is why waiting is the fix rather than retyping. The budget is computed from the body length
  in AppleScript (so a retype pass re-scales) and mirrored in Python by
  `constants.typing_settle_attempts()`, which `_native_reply_effective_timeout` now adds to its
  projected timeout. That projection moved out of `reply_runner.py` into its own leaf module,
  `plugin/apple_mail_mcp/tools/compose/reply_typing_budget.py`, so the two halves of the agreement
  sit next to each other. Those two must agree: if the timeout were still projected from chunk-typing time
  alone, `AppleScriptTimeout` could fire mid-drain and strand a partially typed compose window, which
  is worse than the truncation being fixed. With the scaled budget, 5,000-character bodies and
  signature-enabled bodies both pass, and chunk 200 — designated the positive control that "must
  fail" — does not fail.
- **`TYPING_CHUNK_SIZE` raised 120 → 300, and the constant's documented meaning is corrected.** Chunk
  size was never a safety dial; it was a proxy for how much undrained text was still in flight at
  save time, which is why every earlier sweep read it as causal. Once the drain is actually waited
  for, the trade inverts: chunk 600 on a 2,400-character body failed three runs of four against the
  small budget and *passed* against a large one, taking 72.6 s where chunk 300 takes 22.9 s. Bigger
  chunks post events faster; the editor drains no faster, so the only thing they buy is a longer
  wait. 400 and 500 also passed cleanly and are marginally faster, but each has a single observation
  against 300's four. 300 ships because it is the fastest value whose evidence is deep enough to
  trust, not because 500 is known to be unsafe. The earlier "cliff between 160 and 200" claim is
  withdrawn; it was an artifact of a verifier matching each run against the previous run's draft.
- **The drain wait could report success against the quoted original.** Its exit condition was
  `editorText contains bodyTail` — and `contains` is positionless, while the compose editor is not
  empty when typing starts: it already holds Mail's quoted original and the signature. So a reply on
  a thread the user had replied to before, ending in the same sign-off that appears in the quote,
  matched on the first poll and returned "drained" before a single character had landed. That is the
  truncation the wait exists to prevent, it is deterministic, and the automatic retype reproduced it
  and hard-failed. The tail is now cut from the body **before** typing and compared against the
  pre-typing editor; if it is already present, that exit is disabled for the run and the wait falls
  back to the length delta. An unreadable pre-typing probe disables it too — an unknown must never
  buy a free early exit.
- **The drain wait no longer scales down to nothing when the editor cannot be read.** When
  Accessibility resolves an `AXWebArea` instead of an `AXTextArea`, `AXValue` reads throw, which
  disabled the length baseline *and* ended the poll on its first attempt — a zero-length wait, i.e.
  exactly the pre-fix behaviour, on precisely the windows whose accessibility tree is already
  degraded. When the drain cannot be observed at all, the wait is now spent blind instead of skipped.
  The degraded path therefore always pays the full budget where the observed path usually exits
  early; that is deliberate, because the alternative is silent truncation.
- **An explicit `timeout` is now floored at the projection instead of overriding it.** The drain
  budget is computed inside the AppleScript from the body length and cannot see what the caller
  granted, so `reply_to_email(..., timeout=60)` on a 2,400-character body — ample before this
  release, when that body took ~23 s — now fires `AppleScriptTimeout` mid-drain and strands a typed,
  unsaved compose window. The old remediation then told the caller to retry, which types the body
  into a *second* window. An explicit value can now only raise the budget, never cut it below what
  the script will actually spend, and the refusal cap bounds the result. The
  `REPLY_BODY_TYPING_BUDGET_EXCEEDED` remediation no longer recommends passing a timeout, which had
  become advice routing callers into the unguarded path.
- **The Accessibility remediation stops asserting a diagnosis the reading cannot support.** It had
  begun telling users that Mail-has-windows-but-Accessibility-sees-none is "not a permissions
  problem". A sleeping or locked display produces a byte-identical reading, and so does a lapsed
  grant. The Space case still leads, because it is the most common, but as the most likely of three
  indistinguishable readings rather than a conclusion.
- **The refusal cap admits 114,000 characters, not the 38,400 its comment claimed,** and roughly 100
  s of the 480 s ceiling is now a poll bound rather than typing time. The comment is corrected, and
  the region it does not cover is stated rather than implied: every measurement behind these
  constants was taken at 2,400-5,000 characters, and the drain budget saturates at 6,259.

### Drafting hardening

Adversarial review of the compose surface, weighted toward
`reply_to_email(native_format=True)`. The recurring shape is the one v3.11.8
chased: a check that could not fail, a locator mistaken for evidence, or a
failure reported as prose to a caller parsing JSON.

- **macOS autocorrect could wedge Mail for as long as the machine stayed
  untouched.** A native reply of any real length made Mail stop answering
  *every* AppleScript call — not just the reply — while the process stayed
  alive, kept syncing IMAP, and kept answering Accessibility requests normally.
  Sampling a wedged Mail put all 2,293 main-thread samples inside
  `-[NSCorrectionPanel _interceptEvents]`: macOS autocorrect / inline
  predictions react to the synthesized keystrokes and open a correction panel,
  whose **nested modal event loop pumps UI events but does not dispatch Apple
  Events**. The reply's next statement is a `tell application "Mail"`, which
  then never returns; `run_applescript` SIGKILLs the subprocess at its deadline,
  so the failure arrived with no script state and a compose window left behind.
  Measured: a 1,200-character reply timed out at 120.2 s and Mail refused all 29
  probes over the following 10 minutes. The typing loop now dismisses the panel
  after every chunk, which also *rejects* the suggestion — the same substitution
  that corrupts typed bodies. The same reply now completes in 30.9 s, verified.
  Because a correction panel is an `NSPanel` and not an `AXSheet`, it is
  invisible to Accessibility: a clean AX window list is **not** evidence that no
  modal is up.
- **`TYPING_CHUNK_SIZE` raised 80 → 120**, making a 2,400-character native reply
  about 13% faster (39.0 s → ~33.9 s) with no change in behavior. 120 is the
  largest value backed by uniquely-attributed live runs (4 of 4).
  *Superseded within this same release:* the 2026-08-25 live work above raised
  it again to 300 and established that this constant trades speed, not safety.
  The reasoning recorded here — that a larger chunk is more dangerous — does not
  hold once the compose editor's drain is actually waited for.
- **A larger raise, to 160, was tried and reverted — the measurement was
  invalid.** All four of 160's apparent passes verified against a draft the
  *preceding* run had created; every result row carried `exact_id_verified:
  false`. The tell was that all four clocked 31.5 s to the tenth of a second
  across twelve minutes, where 80 and 120 spread ~0.9 s. On its first honest
  test — once the stale matching drafts had aged out of the lookup window — 160
  failed. **The previously published claim of "a cliff between 160 and 200" is
  withdrawn**; the real boundary is above 120 and has not been located.
- **Why the verifier could be fooled**, which is a live defect and not only a
  testing story: when a Drafts mailbox holds more messages than
  `DRAFT_LIST_CAP`, the identity resolver bails and verification falls back to
  matching on subject plus body-contains over the newest drafts. It does not
  require the matched draft to be the one the call just created. Any repeated
  test that types an identical body into a reply to the same message will
  therefore verify against its own predecessors. Sweeps must now carry a unique
  nonce per run and must treat `exact_id_verified: false` as indeterminate
  rather than as a pass.
- **What still stands from the sweep.** At 2,400 characters, sizes 200 and 250
  failed `REPLY_BODY_MISMATCH` on 4 of 4 runs; those failures are trustworthy
  because the contamination channel can only manufacture passes, never
  failures. They were also *slower* (69-71 s against 33-39 s), because a
  mismatch burns the retype path. At 1,200 characters every size passed,
  including 250: a short body does not discriminate, and re-testing this needs
  at least 2,400 characters.
  *Superseded within this same release:* the failures happened, but the
  mechanism recorded here was wrong, and so was the conclusion that a larger
  chunk is less safe. Nothing was substituted — every failure was a clean
  prefix, and chunk size was only measuring how much undrained text sat in the
  editor at save time. Once the drain is waited for, chunk 200 (the sweep's
  designated "must corrupt" positive control) passes, and the shipped value is
  300. Full audit, the defect list it uncovered, and the design that would
  settle the boundary:
  `tasks/active/native-reply/session-degradation-test-plan-2026-08-25.md`.

- **The native reply identity capsule parser rejected every valid capsule.**
  The capsule is four pipe-separated fields whose fourth names the evidence
  class; the parser instead required that fourth field to be literally
  `transaction`, which threw out every `rfc` capsule (the strong one) and every
  `transaction` capsule from a source message that had a Message-ID. The
  practical effect was that `reply_to_email` came back with no exact Drafts id
  on essentially every native reply: `exact_id_verified` was always false,
  attachment-bearing replies failed with `IDENTITY_UNAVAILABLE` despite a
  correctly saved draft, and the delete-and-retype retry was unreachable. The
  emitter and parser are now round-tripped by
  `tests/compose/test_native_reply_identity_capsule.py`.
- **The quote proof was one-sided.** Verification looked for Mail's attribution
  line and nothing else, so it could not distinguish "the quoted original
  survived" from "Mail kept the header and lost the quote" — and an attribution
  line can occur in an authored body or a signature. Both halves must now land
  in the same region after the reply body: the attribution line, plus a span of
  the source message.
- **Two error paths named a Drafts artifact as a delete target without
  proving it was ours.** `REPLY_BODY_MISMATCH` published the suspected artifact
  as `draft_id` under an instruction to delete it, ignoring the
  `artifact_identity_verified` flag its sibling error builder already gates on.
  All five native-reply abort paths (`TYPING_INTERRUPTED`, sender-override
  refusal, and the three guard aborts) shared a cleanup string offering the
  same delete, on paths where identity can never be established: the probe
  searches by reply subject with no draft id to match against, and a reply
  subject is `Re: <thread>`, which a reply draft the user wrote earlier in that
  same thread also carries. Every one of those errors has already reported that
  nothing was saved. Both now report the id for inspection only, say why, and
  the verified branch steers to the guarded
  `manage_drafts(action='delete', ..., expected_in_reply_to=..., expected_subject=..., expected_to=...)`
  form so Mail re-checks identity at delete time. `pre-draft-verification.md`
  was telling agents to delete unconditionally and is updated to match.
- **Forward's subject bind could select a previous forward's draft.**
  `Fwd: <subject>` is exactly what an earlier forward of the same message
  leaves in Drafts. The bind now excludes ids present before this call saved
  anything, and the cleanup path refuses to delete a row bound only by subject
  (`operation_exact_subject` is a locator, not evidence).
- **HTML compose destroyed a non-text clipboard.** The snapshot read only
  `NSPasteboardTypeString` and skipped the restore entirely when that came back
  `missing value` — which is what a copied image, a Finder file copy, or
  RTF-only content returns. The user's clipboard was replaced by the tool's
  HTML and never given back, and a pasteboard has no undo. Every item and every
  flavor is now snapshotted as detached `NSPasteboardItem`s and written back,
  from one module shared by the success path and the error handler.
- **`manage_drafts(action='find', in_reply_to='<>')` reported a confident "no
  draft".** The value passed the truthiness check, then matched nothing because
  AppleScript's `contains ""` is false for every string. The caller asked
  whether a reply draft already existed and would read the empty result as no,
  whose usual next step is composing a duplicate. It is now refused up front,
  matching the sibling delete path.
- **`forward_email` raised a transport exception on the common call.** The
  handler re-raised whenever `message` was falsy, but `message` is the optional
  lead-in text above the forwarded mail, not a marker of progress — so the
  plain `forward_email(message_id=..., to=...)` crashed while adding a lead-in
  returned a readable error. `compose_email` and `reply_to_email` both return
  unconditionally.
- **`~typo/report.pdf` escaped the attachment validator as an exception.**
  Path expansion ran before validation, so `expanduser` raised `RuntimeError`
  out of a helper whose contract is `(paths, error)`. Validation now runs
  first, on the raw path.
- **Non-success reply exits reached JSON callers as prose.**
  `reply_to_email(output_format="json")` documents a JSON contract, but the
  compose script's failure exits are plain text, so a genuine failure arrived
  at an agent as a `json.loads` error and the parsed `code` it was meant to
  branch on never existed. Three codes now cover them: `REPLY_NOT_COMPLETED`
  (catch-all, with Mail's own text preserved under
  `remediation.script_output`), `QUOTE_PROOF_UNAVAILABLE` (source message has
  no readable content to anchor the proof), and `REPLY_WINDOW_NOT_IDENTIFIED`
  (the compose window could not be told apart from windows already open, so
  nothing was typed).

Performance, with behavior held fixed:

- **The reply body editor is resolved once per reply, not once per chunk.**
  Resolution walks `entire contents of targetWindow`, materializing the compose
  window's whole Accessibility subtree over Apple Events; at 80 characters per
  chunk a 1,600-character body paid twenty of those walks. The reference is now
  carried across the loop and each chunk pays a single `AXFocused` read, with a
  re-resolve before aborting. Nothing is verified less: the per-chunk guard
  still re-proves window identity and editor focus before the first keystroke.
- **The body verdict is decided before attachments and signatures are probed.**
  Only two of the four verdict rows emit those values, so every draft that is
  not ours — most of the bounded Drafts window on the same-subject fallback
  scan — paid three walks of `mail attachments` (each reading `file size`) plus
  a full signature normalization, then discarded all of it. The saved-draft
  verifier polls up to twenty times, so that was paid per draft per attempt
  while holding the cross-process Mail lock.
- **The Drafts resolver probes before spending its patience.** It kept the same
  1.8s total budget but paid an unconditional `delay 0.8` first, which every
  reply paid whether or not iCloud had already indexed the row.

Also: clicking the reply editor is now confined to the initial resolution while
the body is still empty (`click` seats the caret where it lands, so clicking
mid-body would splice later chunks into the middle of the text already typed
and still report success); `verify_draft` detects the quote boundary through a
shared earliest-quote-offset handler instead of two hardcoded needles; the
reply Drafts resolver returns a fixed arity on every branch and signals "no
identity" with `missing value` rather than `""`, which is also a legal draft-id
value inside that handler; `reply_scripts.py` split its pure mode/option
helpers into `reply_script_helpers.py` to stay under the module line budget;
and a new source lint (`tests/cross_cutting/test_applescript_handler_names.py`)
catches AppleScript handlers whose `end` name does not match their `on` name,
which `osacompile` accepts and silently rewrites, so no compile gate can see it.

### Closeout now requires the tag, not just the push (2026-08-25)

`v3.12.0` sat merged but untagged across four PRs because every context file
treated "pushed" as the end of a change. The `finalize-apple-mail-mcp` skill
gains a required step 10 covering merge, signed tag, and GitHub Release, and
both root hubs carry a short form of it.

It records the three things that fail late: the release gate's stamp binds
HEAD's *commit SHA* rather than its tree, so a merge commit invalidates a stamp
taken on the feature branch and the gate must be re-run **after** the merge;
signing needs `user.signingkey` set *and* the key loaded in `ssh-agent`, which
is machine-local and does not travel; and preflight demands a checkout clean of
untracked **and gitignored** files, since `validate_repo_root.py` scans the
filesystem — a stray root `uv.lock`, written by any `uv run` whose working
directory sits inside this tree, fails it while `git status` stays silent.

### Documentation and skills accuracy sweep (2026-08-25)

No runtime behavior changed. Every `CLAUDE.md` hub, guide, and skill in the
tree was re-read against source, and the claims that no longer held were
corrected rather than left to mislead the next agent.

- **Shipped skills described parameters and defaults that do not exist.**
  `calendar-operator` documented a `calendar` argument on `check_availability`
  (the tool takes `calendars`); `email-management` targeted a non-Inbox
  mailbox through `list_inbox_emails`, which has no `mailbox` parameter; six
  sites reviewed flag state through `search_emails`, whose rows carry no flag
  field. Bulk-action guidance now matches the real defaults — `move_email`
  previews only when asked (`dry_run=False`), `update_email_status` has no
  `dry_run` at all, and `manage_trash` silently caps at `max_deletes=5`.
- **The native reply length cap was documented nowhere an agent would look.**
  `email-drafting` now states it: `reply_to_email` refuses with
  `REPLY_BODY_TYPING_BUDGET_EXCEEDED` before touching Mail when projected
  typing time exceeds the ceiling, nothing is created when it fires, and an
  explicit `timeout` cannot lift it.
- **Draft deletion guidance was missing its confirmation requirement.** The
  `REPLY_BODY_MISMATCH` recovery told an agent to delete a draft outright.
  It now requires explicit user confirmation of that specific draft, even when
  the same call created it.
- **A read-ratio heuristic was built on a number that is not measured.**
  `unread count` is a cached aggregate that drifts low; the analytics guidance
  now carries the provenance and is downgraded to a directional signal.
- **Repo-maintenance skills carried guidance for a different codebase** —
  `uv`/`uv.lock`, `pytest -n auto`, `pytest-asyncio`, and an in-memory FastMCP
  client transport, none of which exist here. They now describe this repo's
  actual venv, gates, and `unittest.IsolatedAsyncioTestCase` async pattern.
  New [`.agents/skills/README.md`](.agents/skills/README.md) records which
  vendored skills carry local corrections that a re-sync from upstream would
  silently discard.
- **Corrected structural claims in the hubs:** the `fastmcp` pin, a `core.py`
  module that is now a `core/` package, `AppleScriptBackend._check_window`
  (which does not exist — the bound is enforced at issue time in
  `bounded_scan.py`), the two-layer in-process plus cross-process Mail lock,
  and the release version table, which covers seven files rather than six.
- **The AppleScript compile gate's ledger of unreachable modules is now
  empty.** All three remaining entries turned out to be reachable once the
  hook could hand each tool arguments it accepts: a dataclass sample for
  `reply_runner` (whose script *deletes a draft*, and which no gate had ever
  compiled), a home-directory `save_path` and single-id selector for
  `attachments`, and the one scope/format combination that reaches the script
  `export_emails` builds itself. The ledger stays load-bearing — an
  unreachable module that is *not* listed still blocks — and the gate was
  proved able to fail by injecting a syntax error into `reply_runner`, which
  exited 2 naming the function. Because a passing count cannot tell one
  compiled script from another, the arguments that select the `export_emails`
  path are pinned by their own test rather than inferred from
  `compiled 1 script(s)`.
- A merge artifact that had spliced a second file preamble and a duplicate
  `3.11.3` heading into the middle of this changelog is removed, and six
  broken relative links under `tasks/reference/` are repaired.

## 3.11.9 - 2026-08-22

- **Separate plugin hosts could automate Mail at the same time.** The existing
  single-flight lock applied only inside one Python process, so concurrent
  Codex, Claude, or Cursor plugin servers could interleave AppleScript calls
  with a native reply's focus-guarded typing transaction. Every `osascript`
  invocation now also acquires a version-independent advisory lock in a
  private per-user cache directory. The lock releases when a process exits,
  including after a crash, and fails closed if its directory or file cannot be
  safely trusted.

## 3.11.8 - 2026-08-19

Every entry below is one shape: a failure that reported success. The v3.11.7
release closed that class in the three branches it merged; this release closes
the ones the post-merge audit found still standing, plus four the audit's own
tooling was too blind to see.

- **`search_emails` answered "that is everything" from behind the scan wall.**
  The scan clamps to 50 messages per mailbox while the default `limit` is 100,
  and `has_more` is derived as `len(records) > limit` — so 50 records against a
  limit of 100 returned `has_more: false`, an authoritative "there is no more"
  produced after examining the newest 50 messages, beside a `recent_days_applied`
  asserting a 90-day window had been searched. Paging could not escape it:
  `offset=30, limit=20` re-clamped to 50 and reported `has_more: false` again.
  `has_more` itself is unchanged, deliberately — as a pagination bit it was
  already correct, and forcing it true whenever the ceiling fired would make it
  true on every page forever, since the mailbox saturates the scan every time, so
  a caller looping until it went false would never stop. What was missing was the
  completeness fact. A saturated scan now reports `scan_ceiling_reached`, the
  ceiling, and which mailboxes hit it, with a warning naming the remedy; text
  mode leads with it. The signal fires on runtime saturation rather than on the
  static clamp, so a small folder read in full stays silent. Verified live on two
  accounts across four page shapes.

- **`export_emails(scope="correspondent")` omitted messages under a success
  banner.** Five bare AppleScript `try` blocks each wrapped an entire `repeat`
  over a recipient list and fell through to `return false`, so a throw excluded
  the message, never incremented the matched count, and never reached the
  export-failure arm. Because the `try` wrapped the whole loop, one unresolvable
  recipient hid every later recipient in that list, and `address of aRecipient`
  is `missing value` for unresolved, distribution-list, and X.500 entries, where
  `missing value contains "…"` raises −1700. Measured against the shipped
  handler: a target recipient behind a `missing value` returned `false`. This is
  the call `plugin/skills/email-archive-cleanup/SKILL.md` prescribes as the
  evidence snapshot taken *before* an irreversible
  `manage_trash(action="delete_permanent")`, so a silent under-export meant mail
  was permanently deleted that was never written to disk. Each recipient read now
  carries its own guard, `missing value` has its own branch, and a message whose
  match could not be decided is counted and reported rather than dropped.

- **`get_mailbox_unread_counts` could drop a mailbox, a subtree, or a whole
  account.** Bare tries around the per-account block, the per-mailbox read, and
  the child enumeration meant an offline or mid-resyncing account contributed
  zero rows and no marker, and a per-mailbox throw took out that mailbox *and its
  entire child subtree*. With the default `include_zero=False`, a dropped mailbox
  was indistinguishable from a zero-unread one. The file's own `summary_only`
  path already did this correctly twelve lines up; the nested path now follows it.

- **`list_inbox_emails` rendered a short list as complete.** The per-message
  `try` stayed bare after the script-level swallow was fixed, so rows dropped
  silently — while text mode was already emitting `__COUNT__|||sentCount` beside
  a header built from `messageCount` and *never comparing the two*. The data
  needed to detect the drop was on the wire and being discarded. Sharpest under
  `read_status="unread"`, which `unread_counts.py` designates as ground truth
  when the cached count is suspect. A second invisible drop in the same file is
  also closed: a bare `id of aMessage` probe fed rows that the parser silently
  discards for a non-numeric id.

- **`get_email_thread` printed `FOUND N` and rendered fewer than N**, and JSON
  mode returned rows with no count at all, so a JSON caller got a truncated
  thread with zero signal. Both loops are now armed: the render loop reports rows
  lost against the matched count, and the candidate-collection loop reports reads
  lost *before* `FOUND N` is computed — a distinct defect, because those losses
  leave `matched` and `returned` consistently wrong together and invisible to the
  render reconciliation. The two causes are distinguishable by error type,
  payload key, and wording. The failure mode was a conversation summarized from
  an incomplete thread.

- **`search_emails` dropped messages that had already matched the filter.** A
  bare `try` wrapped the record-emit loop and `collectLimit` decremented after the
  append, so a throw skipped the row and the page came back full-shaped and short
  by one, with no `PARTIAL:` line.

- **`build_bounded_message_scan` bound an empty result on a stale-low count.**
  The unguarded `messages 1 thru _mbCount of MB` fallback silently produced `{}`
  when `count of messages` read low or zero on a non-empty mailbox, where the old
  code enumerated the real contents — and Mail is documented in this repo to
  report cached counts low. The builder is now slice-first: the full bounded
  window is requested before the count is ever consulted, so a stale-low count
  cannot narrow a scan that would otherwise succeed, and a count contradicted by
  a probe past its end raises instead of binding empty. The anti-hang property is
  preserved — no unbounded enumeration on any path — and `messages 1 thru 0`,
  which returns the *first* message rather than none, is never emitted.

- **`manage_drafts(action="delete")` could satisfy its own safety check by
  failing to read.** An inner bare `try` silently dropped one recipient, blinding
  the reverse "no unexpected actual recipient" test — the check that exists to
  catch a drifted draft carrying an extra recipient, which is exactly what an
  unresolvable extra recipient would be. The path now fails closed with
  `DRAFT_DELETE_RECIPIENTS_UNREADABLE` before `delete foundDraft`.

- **`serverInfo.version` advertised the `mcp` library's version.** FastMCP
  accepts no version and the low-level SDK falls back to
  `importlib.metadata.version("mcp")`, so every client saw `1.29.0` as this
  plugin's version — and the package had no runtime version source of truth at
  all. A client could not tell an installed 3.11.6 from a repo 3.11.7 through the
  handshake, which is the drift hazard that forces live verification onto the
  CLI. The handshake now reports the plugin's own version, resolved from
  distribution metadata with a checkout fallback that is accepted only when the
  `pyproject.toml` it reads names this project.

- **The CLI exited 0 on every structured error**, so a shell caller could not
  detect failure from `$?`, and `search --json` had three different error
  envelopes across success, account errors, and mailbox errors. There is now one
  envelope with a machine-readable code, matching the existing `ToolError` shape
  rather than a third convention, and structured errors exit non-zero. Gate-facing
  subcommands (`quick-check`, `smoke-test`, `perf-test`) keep their own exit
  contracts, and partial results still exit 0.

- **`get_statistics` returned a derived `read` / `read_percent` with no
  provenance on the field itself**, while the same payload's note said not to
  derive a read count from Mail's cached unread value — the value this repo has
  measured at 3,236 against a true 10,016. The number is still emitted; it now
  carries its own source, measured flag, and note, both at the envelope and
  inline beside the value, because reading `statistics["read"]` directly is the
  access pattern that made it dangerous. The genuinely measured `sender_stats`
  scope is untouched.

- **`get_inbox_overview` could hang on a caller-supplied argument.**
  `max_recent` had no clamp anywhere in the package, so a large value made the
  bounded guard false and enumerated an entire mailbox — 25,012 messages on a
  live Exchange inbox, then five property reads each. That is the hang the
  bounded-scan contract exists to prevent, reachable directly from a tool
  parameter.

- **Two of the gates meant to catch this class were themselves vacuous.** The
  unbounded-`whose` lint was a line-by-line regex over raw file text, with four
  evasions demonstrated end to end against its real test methods — including one
  a `ruff format` reflow triggers by accident — and three of its five rules were
  rooted below the package, leaving `core/`, `bounded_scan.py`, and
  `calendar_core/` unscanned. It had already gone vacuous once, silently, through
  the whole v3.11.6 subject-filter bug. It is rebuilt on the AST foundation its
  bare-`try` sibling already proved, reconstructing each script from f-string and
  concatenation parts so layout cannot hide a violation, and deriving slice
  variable names from the source instead of a hardcoded list. Each evasion now
  has a test that fails without the fix, and six mutations of the rebuilt lint
  each fail loudly.

  The AppleScript compile hook was the second. It exited 0 having compiled
  nothing on five of the modules it was pointed at, because its loader
  registered a half-built module under its package-qualified name *before*
  executing it; every package here is a re-export facade, so the parent's
  `from .script import ...` found the empty pre-registered entry and raised
  `ImportError`, which the hook reported as a skip. The failure was
  self-inflicted by the loader, not a real import cycle. Two further silent
  skips sat behind it: union annotations read as `"Union"` on Python 3.14, so
  `bool | None` parameters were never synthesized, and the full-script check
  inspected only the first line of the emitted text while real scripts open
  with handler definitions. Coverage went from 0 to 82 scripts across 36
  modules. The hook now blocks on a compile failure, an import failure, and an
  unreachable script in a module not on an in-source three-entry ledger of
  known gaps — the ledger exists because a warning on exit 0 is invisible to
  the wrapper, and it can only shrink, since a ledgered module that becomes
  checkable is flagged stale. Proven not to drive Mail: with a logging
  `osascript` shim on PATH, a full-package run produced zero invocations.
  Negative control: deleting one `end try` from the module of the original
  3.3.0 regression makes it exit 2 and name the handler.

## 3.11.7 - 2026-08-19

- **The tracked plugin zip was not reproducible, so a release that changed
  nothing still failed its own gate.** `zip` stamps every entry with its
  source file's mtime and walks the tree in readdir order, so one commit built
  in two checkouts produced two different archives: a `git checkout` writes
  fresh mtimes, and mtimes are exactly what the archive records. Rebuilding
  `apple-mail-plugin.zip` in a fresh clone therefore drifted the tracked
  artifact with zero content difference — 40 entry timestamps moved and not a
  single CRC did — and `source-release-gate.sh`'s zero-drift check refused to
  stamp the release until someone committed a no-op 15 MB binary. The build
  now stages the payload, normalises every mtime to the 1980-01-01 zip epoch,
  and hands `zip` an `LC_ALL=C`-sorted entry list, so the bytes depend on
  content alone; touching every `.py` file, the launcher, and the manifest to
  three different times now rebuilds the identical archive. Entry names, CRCs,
  sizes, and permissions are unchanged from the previous build, including the
  `0755` on `start_mcp.sh` that an install needs to launch at all. Symlinks
  are enumerated rather than skipped so a newly added one fails the build
  loudly instead of silently vanishing from the payload.

- **A native-format reply could be saved from an identity the caller never
  asked for.** When `from_address` was supplied, `set sender of replyMessage`
  was wrapped in a bare `try` described in-source as a "best-effort identity
  tweak". If Mail refused the override, the error was swallowed and the reply
  was saved from the account's default identity, reported as success. The
  refusal now aborts before `save replyMessage`, closes the compose window with
  `saving no`, removes the temp artifact, and returns a structured
  `REPLY_SENDER_OVERRIDE_FAILED` rather than a draft the caller must notice is
  wrong. The default path (no `from_address`) emits no sender statement and no
  abort branch, and its AppleScript is unchanged apart from two comment lines.
  A sender that is silently *not applied* rather than refused still passes
  undetected; catching that needs a `From` readback and is not in this change.
- **The bare-`try` defect class is now lint-enforced.**
  `run_applescript` raises on a nonzero `osascript` exit, so a script with *no*
  `try` fails loudly and is correct; a `try` with no `on error` arm is precisely
  what turns a loud failure into an exit-0 wrong answer. Every fix in this
  release is one instance of that shape. `tests/core/test_no_bare_applescript_try.py`
  now scans the whole package (not just `tools/` — the shared emitters in
  `core/`, `bounded_scan.py`, and `calendar_core/` account for about a quarter
  of all sites, including the fragment builder that raises the deliberate
  `error "No inbox mailbox found…"` this rule exists to protect) and ratchets
  two rules: `bare` (no arm, or an empty arm) and `silent` (an arm with no
  observable signal). Baseline: 236 sites across 50 modules, 178 bare and 58
  silent, keyed by repo-relative path because `helpers.py` exists in four
  packages. A staleness test fails when a count comes in *under* baseline, so a
  fix must remove its entry rather than leave headroom for the next one.
  Detection is `ast`-based and reconstructs f-strings: the package has 331 try
  blocks, but a naive `ast.Constant` walk finds only 39, so 88% of this
  codebase's AppleScript is written in f-strings and would have been invisible
  to a text-level check. That is the same way the `whose`-clause lint went
  vacuous through v3.11.6. The sanctioned P1 error channel emits a *dangling*
  `on error` arm spliced in at the call site; the detector resolves those
  builders so the lint cannot fail contributors for adopting the very pattern
  its remediation message recommends.
- **A non-positive action cap acted on one message instead of none.**
  `_search_message_ids` appended a resolved id *before* testing its bound, so
  `limit=0` returned one id — the Python-side twin of the `messages 1 thru 0`
  footgun, which also yields one message rather than zero. No lower-bound guard
  existed on `max_deletes`, `max_moves`, or `max_updates`, so
  `manage_trash(max_deletes=0, allow_filter_scan=True)` resolved a message and
  deleted it. The shared helper now returns `[]` for a non-positive limit
  before running any search, and checks the bound before each append, so all
  four call sites are covered. `update_email_status` additionally refuses a
  non-positive `max_updates` at the boundary with `INVALID_ACTION_CAP`;
  `manage_trash` and `move_email` adopt the same guard, which is the whole of
  AGENTIC-2374 and ships here rather than waiting on it.
  Source-verified; no destructive call was executed to confirm the reach.
- **`move_email` accepted a `max_moves` and an `older_than_days` it could not
  honor.** A non-positive `max_moves` is now refused with
  `UNBOUNDED_SCAN_REQUIRED` before any Mail I/O rather than clamped up to 1 —
  clamping a mutation bound the caller set to "none" is the same defect class
  as the AppleScript index clamp, just relocated into Python where it is
  harder to see. An oversized bound has an obvious partial intent to honor, so
  it clamps to the 50-message ceiling and names both numbers in the preview and
  the result (`max_moves=10000 requested, clamped to 50; valid range 1-50`),
  which keeps `TOTAL: 50 email(s) moved` from reading as the caller's own
  10000. Separately, a *negative* `older_than_days` used to null both window
  bounds and discard the caller's `recent_days` along with them, so a request
  phrased "move mail older than N days" moved the newest messages instead; it
  now normalises to no age filter and leaves the rest of the window intact.
  (`older_than_days=0` was already caught by an existing falsy check.)
- **A `cleanup_empty` run whose Drafts window could not be read still reported
  a clean summary.** The slice that fetches the draft head sat in a bare `try`,
  so a failed read left the candidate list empty and the run reported nothing to
  clean — indistinguishable from a Drafts mailbox that genuinely had no empty
  drafts. The failure is now named in the report (`drafts window unavailable
  (…); 0 of N draft(s) examined`) and the summary says outright that no drafts
  were examined, so the result cannot be read as confirmation that Drafts is
  clean.
- **`update_email_status` reported a count that was not the mutation count.**
  On the id path the mutation ran in one loop and `updateCount` was incremented
  in a *separate* loop, as the last statement after three property reads inside
  a bare `try` — so the number reported as "updated" was the number of messages
  Mail could describe. On the filter-scan path the mutation was the first
  statement inside the bare `try` and every read after it could throw. Either
  way a read failure left mail flagged on the server and reported
  `TOTAL UPDATED: 0`. Counting now happens at the mutation site, gated on an
  explicit per-message success flag, before any property read; display is gated
  on the same flag, so a message that failed to mutate is no longer printed
  under a "Marked as read:" heading. Messages that were updated but could not
  be described are reported separately as `DETAILS UNAVAILABLE`. The
  filter-scan branch gains `CANDIDATES EXAMINED` and `MATCHED MESSAGES`
  denominators (the id branch had `REQUESTED IDS` but no matched count), so
  `TOTAL UPDATED: 0` is no longer ambiguous between "nothing matched" and
  "everything matched and every read threw". The candidate-selection loop's own
  bare `try`, which silently dropped a message from consideration entirely, now
  counts selection failures. All failure lines are guarded, so a clean run
  emits none of them.
- **`forward_email` could send mail with the quoted original silently
  missing.** `set origContent to content of foundMessage` sat inside a bare
  `try` initialised to `""`, so a failed body read produced a forward
  consisting of the lead text and header block with nothing beneath it, and
  reported "Forward saved as draft." `mode="send"` had the same hole and
  actually sent the gutted message. The read is now tracked by an explicit
  success flag rather than by testing the content for emptiness, which would
  have rejected every legitimately empty original (subject-only mail, meeting
  invitations).
- **`list_inbox_emails` in JSON mode returned a payload byte-identical to a
  genuinely empty inbox when the scan failed.** Text mode reported the failure
  all along; the JSON builder's bare `try` left the result map empty, which
  parsed to zero rows against a hardcoded `errors: []`. Both modes now report
  the same thing.
- **`export_emails` reported a success count of messages it never wrote.**
  All four export builders incremented the reported counter *before* the
  `write` call, inside a loop whose error arm was a bare `on error` with no
  variable, no counter, and no output, so a failed write still printed
  `✓ Mailbox exported successfully!` and counted the message. Exports now
  report the count that reached disk, plus a `Failed:` count, per-message
  `Error exporting message_id <id>: <err>` lines, and a `PARTIAL:` summary;
  the mailbox banner degrades to `⚠ … with errors`. For the offset-paged
  `correspondent` scope the failure was worse than a wrong number: the failed
  message consumed an offset slot, so the next page stepped over it and it was
  never exported. That scope now halts at the first message that consumed a
  slot and produced no file, making the reported count an exact resume offset.
  The halt is gated on a per-iteration flag so a failed *read* before the
  offset gate reports and keeps scanning instead of wedging the scope
  permanently.
- **Failed calendar enumeration rendered as an empty calendar.**
  `list_calendar_names` discarded the error list its engine already returned,
  so an enumeration failure became zero names, then zero events, and
  `calendar_errors: []`. It reached five read tools plus `resolve_create_target`
  and `manage_calendars`; with an explicit `calendar=` it produced a misleading
  `CALENDAR_NOT_FOUND` when the truth was that enumeration broke. Callers now
  get `CALENDAR_ENUMERATION_FAILED`, which says which of the two it was. The
  guard requires the engine to have *reported* an error, so a host that
  genuinely has no calendars still returns zero with no error.
- **`get_email_thread` JSON mode fed the script's own error string into the row
  parser** and returned `{"items": [], "returned": 0}`. Text mode was correct.
  **`get_email_by_ids` conflated "this id is not in the mailbox" with "the read
  threw"**, reporting both as `missing_ids`; a failed read is now reported
  separately. The row parser itself is deliberately unchanged: reclassifying
  short lines as errors would fire on any content preview beginning `"Error: "`.
- **`create_rich_email_draft` could overwrite any file outside the home
  directory.** It carried its own inlined denylist instead of the shared
  `validate_save_path`, and every `SENSITIVE_DIRS` entry is home-relative and
  only ever joined onto `Path.home()` — so the check could not match any
  absolute path. It accepted `/etc/hosts` and `/Library/LaunchDaemons/…`, the
  latter being a path the denylist names but only ever resolved under `~`. The
  inlined check is deleted and the tool now uses the shared helper.
  **Behaviour change:** `output_path` outside the home directory is now
  refused, matching `export_emails` and `save_email_attachment`, which have
  behaved this way since v3.9.1. Whether home-only is the right policy for all
  three is AGENTIC-2375.
- **`export_emails(export_scope="single_email")` wrote a bare subject-named
  file directly into the caller's directory** and truncated whatever was
  already there. All four sibling scopes write into a per-scope subdirectory
  with an index prefix; this one did not. It now writes to
  `single_email_export/{index}_{message_id}_{subject}.{fmt}`.
- **`validate_save_path` raised out of the tool boundary** for an
  unresolvable path such as `~nosuchuser/x`, and both existing callers invoked
  it unguarded. It now returns an error string. Strictly more permissive;
  no accept/reject decision changed.
- **`timeout` was never range-checked at the only place that runs
  AppleScript.** `run_applescript` substituted a default for `None` and passed
  every other value straight through. Measured: AppleScript accepts any numeric
  `with timeout` without complaint — negative, zero, fractional, and absurd all
  compile and run clean — while `subprocess.run` treats a non-positive timeout
  as already expired and kills osascript in about 2 ms. That `TimeoutExpired`
  was then re-raised as `AppleScriptTimeout`, so a caller-side argument bug
  arrived looking like a slow mailbox. Above 2,147,483 s `subprocess` raises a
  bare `OverflowError` that no handler wrapped. Non-positive and out-of-range
  timeouts are now refused with `INVALID_TIMEOUT` before the Mail lock is
  acquired, matching the `ToolError` precedent in `bounded_scan.py`. Valid
  values pass through unmodified; `3600` is the new ceiling, 12× the largest
  default any tool passes. `timeout=True` no longer becomes a 1-second
  deadline (`bool` is a subclass of `int`).
- **Uncaught `ToolError` now reaches agents as the documented envelope rather
  than a transport exception.** Only the calendar surface had `except ToolError`
  arms, so the new `INVALID_TIMEOUT` raise escaped every mail tool unserialized
  — the right message in the wrong shape, where the previous behaviour had been
  the wrong message ("timed out after 0 seconds", blaming Mail for a caller
  bug) in the right shape. `server.py` now converts at the single `@mcp.tool`
  registration seam. Three tools are excluded: `list_accounts`,
  `list_account_addresses`, and `get_mailbox_unread_counts` declare container
  return types, and FastMCP validates the returned value against a
  structured-output schema derived from that annotation, so a JSON string there
  produces a pydantic error with the real code buried inside `input_value`.
  Those three raise with an accurate message instead; a test pins the set so it
  cannot grow silently. The published tool registry — all 41 tools' `name`,
  `description`, `inputSchema`, `outputSchema`, and `annotations` — is
  byte-identical before and after (verified by hashing the serialized
  `list_tools()` payload), and the 8 `async def` tools stay coroutine
  functions. Exceptions that are not `ToolError` still propagate: converting
  arbitrary failures into tidy JSON is the pattern this release exists to
  remove.
- **`manage_trash(action="empty_trash")` deleted mail under the default
  `dry_run=True`.** The branch read `confirm_empty` and never read `dry_run`, so
  `confirm_empty=True` alone permanently deleted. It now derives its mode from
  `dry_run` the way the id-direct branch already did: `dry_run=True` emits no
  `delete` command, labels the output `DRY RUN - PREVIEW EMPTY TRASH`, and states
  that `dry_run=False` is required to act. `confirm_empty` is unchanged and still
  checked independently. **Behavior change:** a caller passing `confirm_empty=True`
  and relying on the default now gets a preview instead of a deletion. The bundled
  `email-management` skill documented exactly that call, so its
  `SKILL.md`, `references/bulk-cleanup.md`, and `templates/common-workflows.md`
  now pass `dry_run=False` on the step that actually empties.
- **`manage_trash(action="delete_permanent")` accepted a date window and applied
  none of it, deleting the newest messages when asked for the oldest.** The
  `apply_to_all` path hand-rolled a script whose selection was a bare newest-first
  `messages 1 thru max_deletes of trashMailbox`, with no date condition emitted;
  `older_than_days=365` and the default `recent_days=2.0` produced byte-identical
  scripts. The `UNBOUNDED_SCAN_REQUIRED` guard that demanded a window was
  satisfied by the *default* `recent_days`, so it enforced nothing downstream.
  That script is deleted. The path now routes through the same bounded
  `_search_message_ids(mailbox="Trash", date_from=…, date_to=…)` call its sibling
  branches already used, then recurses into the id-direct path, which honors
  `dry_run` correctly. `delete_permanent` can no longer permanently delete a
  message outside the caller's requested window. Known limitation, in the safe
  direction: with `older_than_days` set, the scan cap is `max_deletes + 1` clamped
  to 50, so the call examines only the newest 6 to 50 messages in Trash and
  resolves nothing when those are all recent. It under-deletes and says so.
- **`max_deletes=-1` permanently deleted the entire Trash.** With no floor and no
  ceiling on the parameter documented as a "safety limit", `messageCount > -1`
  passed and the emitted slice became `messages 1 thru -1`, which spans the whole
  mailbox because `thru -1` is end-relative. `max_deletes=0` emitted
  `messages 1 thru 0`, which does not raise on a non-empty mailbox and instead
  returns exactly one message, so a request for zero deletions deleted one. Both
  were verified against Mail on four backends. `max_deletes` is now clamped once,
  before every branch, to `max(1, min(max_deletes, SCAN_BOUNDS["TRASH_SCAN"]))`
  — adopting the `TRASH_SCAN = 100` constant that already existed and was
  referenced nowhere. A clamped call names both numbers rather than silently
  echoing the corrected one.
- **A non-positive `max_deletes` is now refused rather than clamped up to 1.** An
  earlier pass in this release floored the value with `max(1, …)`, which meant
  `manage_trash(action="delete_permanent", max_deletes=0, dry_run=False)`
  permanently deleted **one** message for a caller who had asked for none.
  Captured from the pre-fix build: `if messageCount > 1 then` /
  `set trashMessages to messages 1 thru 1 of trashMailbox` / `delete aMessage`.
  That is the `messages 1 thru 0` defect this release fixes, re-created in Python
  where no AppleScript probe would catch it. `manage_trash` now returns the same
  `UNBOUNDED_SCAN_REQUIRED` refusal `move_email` and `manage_drafts` already
  returned for the equivalent bound, on every action including `empty_trash`, and
  before any account probe or AppleScript. Oversized values still clamp.
- **`manage_trash`'s advertised `max_deletes` range was fiction on two of its
  three paths.** The clamp used `SCAN_BOUNDS["TRASH_SCAN"]` (100), but 51 to 100
  is unreachable by ids — `_check_message_ids_cap` caps at `MAX_WHOSE_IDS` (50) —
  and unreachable by filter, where `SEARCH_HARD_CEILING` (50) bounds the resolved
  records. The ceiling is now selected per path: `TRASH_SCAN` for `empty_trash`,
  which genuinely slices Mail's own `messages` element, and `MAX_WHOSE_IDS` for
  the id and filter paths. This is the same coupling `move_email` chose so the
  clamp and the id cap cannot drift apart.
- **A non-positive `older_than_days` walked straight around the date fix above,
  on both trash actions.** The refusal guard tests `older_than_days is None`,
  which is false for `-1` and `0`; `effective_recent_days` is then zeroed because
  the value is not `None`, silently discarding the caller's `recent_days`; and
  `_date_to_for_older_than` returns `None` for anything `<= 0`. Net result:
  `date_from=None` and `date_to=None`, no window at all, so a request phrased
  "purge mail older than N days" permanently deleted the **newest** messages —
  the exact defect this release fixes for positive values. Measured before the
  fix: `older_than_days` of `-1`, `-365`, and `0` all produced an unwindowed scan
  with the caller's `recent_days=2.0` reduced to `0`. `move_to_trash` shared the
  hole on both its live and its dry-run path, so its preview described the wrong
  messages too. A non-positive `older_than_days` now normalizes to `None` before
  the guard runs, which restores the caller's `recent_days` window and lets
  `recent_days=0` correctly return `UNBOUNDED_SCAN_REQUIRED`. Positive values are
  byte-identical to before. Note `older_than_days=0` was **not** already safe
  here: unlike `move_email`, these paths gate on `apply_to_all` rather than on a
  falsy `older_than_days`.
- **An invalid `action` silently performed a trash-move.** The non-`message_ids`
  path fell through `empty_trash` and `delete_permanent` into `move_to_trash`, so
  a typo'd action moved mail while the `message_ids` path caught the same typo
  with `Error: Invalid action`. Both paths now validate against the same action
  set and return the same message, before any AppleScript runs.
- **`inbox_dashboard`'s default UI mode was serving a dead page.** The template's
  "fallback if data not injected" block declares `var accountsData` inside an
  `if`, and `var` hoists to script scope, so the renderer's injected
  `const accountsData` made the whole inline `<script>` a **parse-time**
  SyntaxError. Nothing in it ran: no data render, no search, no actions. The page
  served its static shell with empty containers and a literal `0 emails`, which
  reads as an empty inbox. Confirmed with `node --check` against the committed
  template and renderer. The injection now emits `var` to match the fallback, and
  three `node --check` tests plus a declaration-collision test lock it. This is
  why the diagnostics work above needed a UI path as well as a JSON one: on the
  default output format, the code meant to display an error could not execute.
- **A subject or mailbox name containing `</script>` could break out of the
  dashboard's inline script.** `json.dumps` output was injected raw, and
  `json.dumps` does not escape `</`, so user-controlled text closed the script
  element and the remainder rendered as live markup. All three injected payloads
  now pass through an `_embed_json()` helper that escapes `</` and the U+2028 /
  U+2029 line terminators JS honors and JSON does not. DOM-boundary escaping was
  already correct via the template's `escapeHtml()`; the hole was one layer up, at
  the script-element boundary.
- **The unread-count provenance stopped at the JSON payload.** The release note
  below lists `inbox_dashboard` among the surfaces that report where an unread
  number came from, and that was true only on `output_format="json"` — a format
  no default caller passes. The UI branch popped the provenance sentinel out of
  the account map (so it would not render as a phantom account card) and then
  called the renderer without it, so the page a person actually looks at showed
  a count measured 68% low on a real 25K-message Exchange Inbox as a bare badge
  with nothing to question. `create_inbox_dashboard_ui` now takes the
  `disclosure` dict and the template renders its lede sentence under the Accounts
  heading, with the full note on hover. It speaks only when
  `unread_count_measured` is explicitly `false`: an absent disclosure means
  unknown provenance, and manufacturing a disclaimer for an unknown is its own
  wrong answer. Styled as a note rather than a warning, because a cached count is
  the normal case and alarming on the normal case teaches people to ignore it.

- **`inbox_dashboard` scan failures now reach the default UI output too.**
  `create_inbox_dashboard_ui` takes an optional `scan_errors` list, defaulting to
  empty so every existing caller is unchanged. A failed scan reveals a warning
  banner, labels the count `0 emails (incomplete)`, and replaces the `Inbox Zero`
  empty state with `Scan Incomplete`; a genuinely quiet mailbox still renders the
  clean empty state with no warning.
- **The search dispatch layer had a sync bridge that skipped every bound check
  the async path performed.** `_search_mail_records_sync` called
  `_search_one_account` directly, bypassing the `offset`, `limit`, `sort`, and
  `read_status` validation four lines above it. Because `base_cap = limit + 1 +
  offset`, a caller could drive the emitted slice to any value: `limit=0` bound
  `messages 1 thru 1` and returned one real message for a page sized zero, and
  `offset=-102, limit=100` bound `messages 1 thru -1`, a full-mailbox
  materialization of the kind this package bans everywhere else. Both checks now
  live in one `_validate_dispatch_args` seam that both paths call, which makes
  `base_cap >= 2` an invariant of every script the package builds. The async
  path's `limit <= 0` arm no longer returns a silent empty either: that shape is
  itself the silent-zero defect, and it was already unreachable from its only
  tool caller once `search_emails` began refusing non-positive bounds upstream.
  The stale docstring claiming `list_email_attachments` as a caller is corrected;
  the third caller is `export_emails`, which already handled the raise.
- **Eleven bundled skill examples told agents to trash mail with a call that only
  previewed it.** `manage_trash` defaults to `dry_run=True`, so a `dry_run`-less
  example produces a preview; an agent following it reports the mail trashed when
  nothing happened. Sites across `email-management/SKILL.md`,
  `references/bulk-cleanup.md`, `templates/common-workflows.md`,
  `examples/inbox-zero-workflow.md`, `examples/email-triage.md`, and
  `email-archive-cleanup/SKILL.md` now show the two-step shape: preview with the
  default, then repeat the same call with `dry_run=False`. Workflows that had no
  action step gained one rather than having their preview relabelled. The 45
  `move_email` examples were audited and deliberately left unchanged: `move_email`
  defaults to `dry_run=False` and genuinely acts, so labelling those as previews
  would have documented a mutation as a no-op.
- **`inbox_dashboard` rendered every failure as an authoritative empty inbox.**
  Its script-level `try` had no `on error` arm, so any throw returned `""`, which
  parsed to `[]` and surfaced as `"recent_emails": [], "errors": []` — with
  `errors` a hardcoded literal, its only occurrence in the file. An agent reading
  that concludes there is no mail and reports no action needed. Measured live
  against a non-existent account: previously 0 rows and **0 errors** in 115 ms;
  now 0 rows and one `mailbox_error`. The outer `try` gained an `on error` arm,
  the per-message loop counts swallowed read failures, and both emit the same
  `ERROR_MAILBOX` marker rows `search_emails` already used, surfaced as `errors`
  plus a new `error_details` list in JSON mode. A genuinely empty inbox still
  reports empty with no error.
- **`inbox_dashboard` emitted unsanitized subjects and senders into a
  `|||`-delimited row.** A subject containing the delimiter shifted every
  downstream field, which is the documented wrong-`message_id` footgun. Both
  fields now pass through `sanitize_pipe_delimited_field`, and the marker-row
  parser requires an exact 3-field shape so a message whose subject is literally
  `ERROR_MAILBOX` cannot forge a diagnostic and suppress its own row.
- **`inbox_dashboard`'s `max_per_account` had no floor and no ceiling.**
  `max_per_account=0` emitted `messages 1 thru 0`, which returns one message
  rather than raising, so a request for zero recent emails fabricated one row;
  `-5` bound essentially the whole inbox. Now clamped in the script builder to
  `max(1, min(max_per_account, SCAN_BOUNDS["INBOX_HARD_CEILING"]))`, which covers
  all three callers rather than the tool function alone. The raw
  `messages of inboxMailbox` fallback arm is gone, replaced by a count-clamped
  slice with an explicit empty-mailbox arm and its own error handler for a
  stale-high count; its lint allowlist entry drops to zero.
- **`manage_drafts(action="cleanup_empty")` failed on most real Drafts folders.**
  It emitted a fixed, unguarded `messages 1 thru 75 of draftsMailbox` with no
  clamp to the live count. An out-of-range upper bound raises -1719
  `Invalid index.` rather than clamping, so any Drafts mailbox holding fewer than
  75 messages errored out. Measured live across six accounts at draft counts of
  11, 262, 1014, 45, 3, and 5: **four of the six raised -1719**; only the two
  above 75 worked. The slice now clamps to `count of messages` with an explicit
  empty-mailbox arm, following the pattern its sibling builders already used, and
  keeps a surrounding `try` because Mail's `count of messages` can itself read
  stale-high — a clamp alone is necessary but not sufficient.
- **`cleanup_empty` permanently deleted non-empty drafts when a body read
  failed.** The `content` read sat in a bare `try`, so a throw left `draftBody`
  empty and a draft with a blank subject was then classified as empty and
  deleted. Classification is now fail-closed: it binds a read-success sentinel
  and a draft is deletable only when its emptiness was positively established.
  Drafts skipped because their body could not be read, and deletes that failed,
  are counted and reported instead of vanishing from the tally. Covers the throw
  path only; if the read *hangs* rather than raising, no `on error` arm helps and
  only the call timeout bounds it. The `cleanup_empty` script builder moved from
  `compose/manage.py` to `compose/drafts_scripts.py` alongside its siblings,
  which took `manage.py` from 596 to 522 lines; the move was verified
  byte-identical in emission before the behavior changes were applied.
- **`search_emails(limit=-1)` told the caller to keep paginating forever.** A
  non-positive `limit` returned an empty page with `has_more: true` and
  `next_offset: 0`, because `has_more` was computed as `len(items) > limit` and
  `0 > -1` holds. An agent paginating on `has_more` re-issues the identical call
  indefinitely. A zero `limit` returned a clean empty result indistinguishable
  from "no matches," and both spent a live `validate_account_name` AppleScript
  round trip before returning. `search_emails` was the only one of its four
  siblings with no bound validation of its own. A non-positive
  `limit`/`max_results` now returns `UNBOUNDED_SCAN_REQUIRED` — the same code
  `list_inbox_emails` already uses for `max_emails <= 0`, whose documented
  trigger already covered a zero page size — and a negative `offset` returns
  `Error: offset must be >= 0`, matching `export_emails` and `list_events`
  verbatim. Both refuse before any AppleScript is built, confirmed live at
  sub-second latency.
- **`search_emails` subject filtering returned 0 results on every account.**
  The subject-only fast path built its filter as a bare `subject contains "…"`
  and spliced it into `repeat with aMessage in candidateMessages`. A bare
  property reference only resolves where an enclosing `whose` clause supplies
  the implicit target; inside an explicit `repeat` loop it is unbound, so Mail
  raised -1728 `Can't get subject.` on every message. The loop's `try`
  swallowed the error, so the tool reported an empty result set with
  `has_more: false` and no errors. The fast path now tests `messageSubject`,
  the loop-local the preceding line already binds.
- **The same subject-only fast path ignored `date_from` and `recent_days`.**
  Binding the filter to `messageSubject` made subject matching work, but the
  subject-only shape then tested subject and nothing else: it never read
  `date received`, so a caller asking for "subject contains X in the last 7 days"
  got matches from any date, silently outside the window they asked for. The fast
  path now reads the date into a loop-local, ANDs the floor into the per-message
  condition, and keeps the descending-order `exit repeat` so the scan still stops
  at the first message older than the window. Sender, read-status, and content
  reads stay out of the fast path, which is the entire reason it is fast.
- **Bounded mailbox scans no longer fall back to enumerating the whole mailbox.**
  `build_bounded_message_scan` and `search_emails`'s `bounded_candidate_script`
  each had an arm that bound `messages of <mailbox>` when a bounded slice was not
  taken. Every arm now slices. The search recovery path re-reads
  `count of messages`, clamps to the smaller of that count and the scan cap, and
  emits a structured `ERROR_MAILBOX` diagnostic only if the clamped slice also
  fails, so a mailbox smaller than the cap still returns its results instead of
  reporting a spurious error. The extra count read is paid only on the arm that
  previously enumerated, never on the fast path. Message sets are unchanged in
  every case.
- **The bounded-scan lint now covers the whole package, not just `tools/`.**
  `tests/core/test_no_unbounded_whose.py` scanned
  `plugin/apple_mail_mcp/tools/` only, so `bounded_scan.py`,
  `core/script_fragments.py`, and the two `calendar_core/` script modules were
  unlinted: the bounded-scan builder itself sat outside the bounded-scan lint.
  Now rooted at `plugin/apple_mail_mcp/` (115 modules) with a coverage assertion
  that fails if the scanned set ever collapses again, and the ratchet re-keyed
  package-relative so same-named modules in different packages cannot share one
  allowlist entry.
- **New gate: committed-identity scan for this public repo.**
  `tools/validators/validate_no_committed_identity.py` fails closed on an email
  address at a non-placeholder domain, an absolute `/Users/<name>/...` path, or an
  uppercase account UUID in any tracked text file. Root `AGENTS.md` documented the
  equivalent scan as a manual habit; it is now the first step of
  `tools/gates/dev-check.sh` `default` and `release`, and therefore of the
  pre-commit hook. It runs first because it is the cheapest step and the only one
  whose miss cannot be undone in the working tree. Enforcement is a path-keyed
  ratchet, so already-published hits do not block work while no new one can land,
  and violation output names the file, line, and rule without ever echoing the
  matched value.
- **A scan that throws on every message no longer looks like a legitimate
  empty result.** All three `search_emails` scan loops now count swallowed
  per-message failures and emit one `ERROR_MAILBOX` diagnostic per mailbox
  when the count is non-zero, surfaced as a `mailbox_error` in
  `error_details`. Text output (the default `output_format`) reports mailbox
  issues even when no account-level error occurred, so a fully failed scan
  can no longer render as a clean `FOUND: 0`.
- **Unread counts are labelled with their provenance instead of being presented
  as measured.** Mail's `unread count` property is a cached aggregate, not a
  computed one, and on a 25,012-message Exchange Inbox it reported 3,236 against
  a per-message truth of 10,016 — 68% low. Computing the true count is not a
  usable fix: `count of (messages of <mb> whose read status is false)` returned
  no result at 300s on that mailbox while `count of messages` answered in ~1s, so
  the measurement is affordable only where the cache is already right. All four
  surfaces that read the property (`get_mailbox_unread_counts`, `list_mailboxes`,
  `get_inbox_overview`, `get_statistics`) plus the two consumers that inherit it
  (`inbox_dashboard`, the CLI) now emit `unread_count_source`,
  `unread_count_measured`, and `unread_count_note`; text modes tag each number
  `[Mail cached, unverified]`. `get_statistics` `sender_stats` genuinely measures
  per-message `read status` and is labelled `per_message_read_status` /
  `measured=true`, so the two provenances are distinguishable rather than both
  silent. Additive contract change; no numeric field changed type.
- **`get_statistics` over-reported `read` by the same margin as the unread
  under-count**, because it derives `read = total - unread` from the cached
  value.
- **The bounded-scan AppleScript lint was silently disarmed and is now
  re-armed.** `tests/core/test_no_unbounded_whose.py` iterated
  `tools/` with a non-recursive glob, so once every tool surface became a package
  it scanned 2 modules instead of 79 and all four forbidden-pattern checks passed
  vacuously — including through the subject-filter bug above. It now scans
  recursively, keys findings by package-relative path (`helpers.py` exists in
  four packages), and a coverage test fails if the scanned set ever collapses
  again. A new rule bans `messages of <mailbox>`, the previously unlinted twin of
  `every message of`; six existing sites are grandfathered behind a
  path-to-count ratchet with a staleness test, tracked in AGENTIC-2355.
- **`manage_trash` no longer builds an unused bare-property AppleScript
  condition** one line from its `delete_permanent` path. The value was computed
  and discarded (only its truthiness was read), but it was the same shape that
  caused the subject-filter bug. With that call gone,
  `core.normalization.contains_any_condition` has no production caller, and a
  new lint rule refuses any future call that passes a bare Mail property
  (`subject`, `sender`, `content`, …) rather than a loop-bound variable. The rule
  scans the whole package, not just `tools/`, because the helper lives in
  `core/`.

## 3.11.6 - 2026-07-15

- **HTML compose no longer leaves the internal marker as the visible subject.**
  The temporary `__apple_mail_mcp_…` token is only for binding the compose
  window during focus and paste. After paste, the real subject is set on the
  outgoing message and verified before the first save or send. Error and
  Python follow-up paths restore or delete leftover marker rows instead of
  re-stamping the marker; a failed compose must not leave a draft or open
  window titled with `__apple_mail_mcp_`.
- **Attachment HTML finalize binds by the saved real subject, not a marker
  rewrite on the persisted Drafts row.** Proof runs after save; Gmail saved
  draft subjects stay read-only. Proof fails if the stored subject is still
  the operation marker or is not the requested real subject.
- **HTML compose follow-up no longer converts marker cleanup into success.**
  `cleared` / `deleted` / `outgoing_ok` only mean no leftover marker remains;
  they do not prove a real-subject draft exists.
- **HTML compose focus failure deletes the fixture outgoing message** instead
  of restoring the real subject and closing without delete, which let Gmail
  persist an empty draft.
- **Success-path leftover marker Drafts fail closed** instead of
  delete-and-succeed if IMAP persisted the marker after outgoing readback.
- **Forward attachment drafts restore `fwdSubject` on the live outgoing
  message before the first save** and never write saved `message.subject`.
- **HTML compose uses the documented 120s AppleScript timeout** unless the
  caller passes `timeout`. Restore verification matches the exact marker
  token, not a bare `__apple_mail_mcp_` prefix contains.
- **HTML compose no longer Tabs into Mail's body editor.** After signature
  insertion the caret is already in the WebKit compose field, so the old
  unguarded `focusComposeBody` Tab loop inserted first-line indent (often
  four tabs) before the HTML paste. Focus now binds to the marker-named
  compose window, AXFocus/clicks the editor, and Tabs only while
  Accessibility reports a header field. It never Tabs when the body
  already has focus, and never Tabs when the focused role cannot be read.
- Fix the Cursor adapter to resolve `start_mcp.sh` from
  `${CURSOR_PLUGIN_ROOT}` while preserving the draft-safe launch flag.
- Validate Cursor and Codex launcher contracts independently so a relative
  Codex path cannot mask an invalid Cursor installation path.
- Native replies now add attachments only after the authored body finishes
  typing, preventing the object-model mutation from disrupting Mail's native
  quote cursor.
- Saved reply verification now fails closed with
  `REPLY_QUOTED_ORIGINAL_MISSING` or
  `REPLY_DRAFT_ATTACHMENT_VERIFICATION_FAILED` when the native quote or a
  requested attachment is absent. Persisted identity matches name the exact
  retained artifact; same-subject fallback ids are marked as suspect and
  never authorize deletion.
- Native attachment replies now require draft-first verification instead of
  direct send. Verification also retries transient attachment materialization
  and requires the quote for attachment-only replies.

## 3.11.5 - 2026-07-15

- Integrate the offline runtime and Cursor adapter with exact-recipient draft
  verification, bounded reply-state reporting, and provider-specific Sent
  mailbox resolution.

## 3.11.4 - 2026-07-11

- Add separate Cursor plugin and local MCP adapters to the offline release payload.
- Keep the Cursor launcher draft-safe and version-synchronized with the Claude and Codex adapters.

## 3.11.3 - 2026-07-11

- Add a hash-locked offline wheelhouse for the macOS arm64 CPython 3.13 plugin release channel.
- Make the plugin launcher fail closed instead of downloading runtime dependencies.

### Fixed

- **Compose draft smoke verification now requires the exact persisted To
  recipient set and uses an identity-guarded cleanup transaction.** A recipient
  mismatch or Exchange Drafts ID drift now retains the artifact instead of
  risking deletion of another draft.
- **Reply-state Drafts scans stay bounded at 50 without claiming false
  negatives.** When a capped scan omits older drafts, matching rows remain
  `true` and nonmatches return `null` (`unknown`). The performance check now
  scales its mailbox metadata threshold from the mailbox response envelope.
- **Identity-guarded cleanup no longer refuses to delete a verified smoke
  draft whose recipient list contains duplicates.** The delete transaction
  now proves exact recipient-set equality by mutual containment instead of a
  count comparison that mismatched against the deduplicated expected list.
- **A Drafts snapshot with an unreadable mailbox-wide total now fails open.**
  A missing `TOTAL` marker is treated as a truncated scan, so nonmatches
  report `null` (`unknown`) rather than a definitive `false`.
- **Every `draft_scan` producer now emits the same envelope.** `total` and
  `truncated` appear on `get_needs_response`, the inbox skipped/error paths,
  and the empty-scan early return, matching the annotated list/search
  responses; skill references and conventions docs describe the three-state
  `has_draft` semantics including truncation.
- **The identity-guarded delete AppleScript is covered by the osacompile
  parse gate.** The script moved into a discoverable `_script()` builder, and
  an account-resolution failure now returns the helper's structured JSON
  error shape instead of a raw string.
- **Recipient normalization is unified on one casefolding helper** shared by
  draft verification, the smoke CLI's exact-set check, and the cleanup
  identity literal, so Unicode addresses compare identically at every stage.

## 3.11.2 - 2026-07-11

### Fixed

- **Native reply verification now uses a persisted, header-linked Drafts
  identity.** `reply_to_email` never treats Mail's transient outgoing-message
  id as a Drafts id. After saving, it takes a complete bounded Drafts snapshot,
  requires exactly one new persisted message, and requires that message's RFC
  `Message-ID` and `In-Reply-To` link it to the source. Only then does it emit
  `Draft ID` plus the internal identity capsule, verify that exact artifact, or
  permit an automatic delete-and-retype. The verifier and deletion path both
  revalidate the capsule. Cap limits, indexing delay, ambiguity, or identity
  drift fail closed: fallback may report an artifact, but never authorizes
  deletion or retyping.
- **`verify_draft(expected_body_contains=...)` no longer mistakes ordinary
  authored `wrote:` prose for quoted text.** Quote scoping now recognizes an
  Apple Mail `On <date>, ... wrote:` attribution, Outlook's structured header
  block, or the Outlook original-message separator. If none is present, the
  expectation checks the whole body preview.
- **Native reply AppleScript is now explicitly compiled in the test suite.**
  This covers the helper-prefixed native builder and its focus-guarded chunked
  typer, which generic builder discovery does not select.

## 3.11.1 - 2026-07-10

AGENTIC-1214 reply drafting correctness: chunked native typing, full-body
draft verification, and honest threading contracts.

### Fixed

- **AGENTIC-1214: `reply_to_email` native reply body no longer truncates or
  types in ALL CAPS.** The native reply path (`native_format=True`) previously
  inserted the entire `reply_body` with one System Events `keystroke` call,
  which silently dropped the tail of long bodies around 320-480 characters
  and could leak leftover shift state into ALL-CAPS output on short bodies.
  It now types the body in small, focus-guarded chunks (`TYPING_CHUNK_SIZE`,
  `TYPING_INTER_CHUNK_DELAY`), releasing keyboard modifiers before and after
  every chunk and re-checking both Mail's own front window and System
  Events' process-level focus before each chunk, aborting immediately
  (never re-stealing focus) on a mismatch instead of typing into whatever
  now holds focus. A mid-typing abort discards the partially typed compose
  window and returns the new `REPLY_BODY_TYPING_INTERRUPTED` structured
  error, distinct from the pre-typing `REPLY_WINDOW_FOCUS_FAILED` /
  `REPLY_SUBJECT_GUARD_MISMATCH` abort codes. The native-path AppleScript
  timeout now scales with the projected chunk-typing time (floored at
  120s); a body long enough to exceed the documented typing budget is
  refused up front with `REPLY_BODY_TYPING_BUDGET_EXCEEDED` instead of
  risking a mid-typing timeout.
- **AGENTIC-1214: post-save reply verification now checks the full body, not
  just its first line.** The saved-draft verifier previously matched only
  the first non-empty line of `reply_body`, so a truncated or miscased tail
  could still pass. It now compares the FULL body against the saved draft
  above the quoted original: whitespace-flattened, smart-punctuation-folded,
  sentence-start case neutralized (so Mail's own autocapitalization cannot
  cause a false mismatch), located first under `considering case` so a body
  that itself contains "wrote:" cannot false-fail into "after quote", then
  compared case-sensitively so an ALL-CAPS draft still fails. On a
  `body_missing` mismatch with a concrete artifact id, `reply_to_email`
  automatically deletes the artifact and retypes the identical body once
  before re-verifying; a mismatch that persists (or an unconfirmed delete)
  returns the new `REPLY_BODY_MISMATCH` structured error naming the suspect
  Drafts artifact id, with `retyped` and `stale_artifact_id` remediation
  fields. The success payload and text output gained `body_verified`,
  `retyped`, and `stale_artifact_id`. This verification (and its automatic
  retype) only runs for `mode="draft"` / `mode="open"`; a `mode="send"`
  native reply still gets the chunked-typing fix above but has no saved
  Drafts artifact left to verify afterward, so draft-then-verify-then-send
  stays the safe sequence when typed-body correctness matters.
- **AGENTIC-1214: `manage_drafts(action="create")` no longer silently drops
  `in_reply_to`.** Passing `in_reply_to` to `action="create"` now returns a
  structured `CREATE_CANNOT_THREAD` error before any AppleScript runs and
  before the standalone reply-like guard, since the Mail scripting
  dictionary exposes no header property on a new outgoing message and
  `create` can never set In-Reply-To/References. `in_reply_to` remains
  honored only by `action="find"`. The remediation points at
  `reply_to_email(message_id=...)` to thread a reply, or
  `manage_drafts(action="find", in_reply_to=...)` to locate an
  already-saved reply draft.
- **`manage_drafts(action="create")`'s standalone reply-like guard now names
  the tool that was actually called.** `_standalone_compose_thread_warning`
  previously always said "compose_email" in its error message even when
  `manage_drafts(action="create")` triggered it; it now names the calling
  tool.
- **Draft-id instability on Exchange is now documented.** `manage_drafts`'s
  `action` and `draft_id` docstrings now note that server-account Drafts
  numeric ids are reassigned on sync (observed drifting between two
  `action="list"` calls with zero writes in between) and are not a stable
  handle across turns; `action="find"` with `in_reply_to` is the durable
  handle for a reply draft.
- **Native typing timeout projection now models per-chunk overhead.** The
  scaled AppleScript timeout accounts for the per-chunk focus re-check and
  keystroke cost (`TYPING_PER_CHUNK_OVERHEAD_SECONDS`), not just the
  inter-chunk delay, so long reply bodies no longer risk `AppleScriptTimeout`
  killing osascript mid-typing and stranding a partially typed compose window.
- **The automatic retype never deletes a draft that is not provably ours.**
  The delete-and-retype retry now requires the verifier's mismatch artifact id
  to equal the draft id Mail itself returned for this compose call; under
  Exchange eventual-consistency lag the subject-scan fallback could otherwise
  name a pre-existing same-subject draft the user wrote, and the retry would
  have deleted it. When ids differ, the tool returns `REPLY_BODY_MISMATCH`
  naming the suspect id and deletes nothing.
- **Tab characters in `reply_body` are converted to spaces on the native typed
  path.** A typed tab is a field-navigation key that can move focus out of the
  compose body mid-draft; the conversion happens before the body temp file is
  written and is compare-neutral in verification (which flattens all
  whitespace on both sides).
- **The reply-draft verifier's sentence-start case fold now scales with
  sentence count instead of body length.** The previous per-character
  AppleScript walk was quadratic over the full draft content, so replies on
  long quoted threads could exhaust the verification timeout and mask real
  body mismatches as `verification_timeout`.
- **AGENTIC-1192 item 2: `verify_draft` / `verify_drafts` no longer false-pass
  `expected_body_contains` on quoted text.** The needle is now scoped to the
  reply body above the first quote boundary; when it appears only inside the
  quoted original the payload carries `body_needle_only_in_quote: true` and an
  `expected_body_only_in_quote` warning instead of a pass.

### Known limitations (found in the 2026-07-10 live verification, fail closed)

- **Accented and composed characters can corrupt during native typing.**
  Observed live: "Renée" saved as "Renae" (System Events keystroke layer or
  Mail autocorrect; smart quotes, em dashes, and ellipsis typed correctly).
  The full-body verifier catches this and returns `REPLY_BODY_MISMATCH`
  naming the artifact instead of silently saving a corrupted draft. Until the
  typing-fidelity follow-up ships, prefer ASCII spellings in `reply_body` on
  the native path.
- **The automatic retype engages only when Mail exposes the compose draft id
  and the verifier resolves the same id.** On Exchange, post-save id capture
  can fail or drift, in which case the tool skips the delete-and-retype (it
  never deletes a draft it cannot prove it created) and returns
  `REPLY_BODY_MISMATCH` with the suspect id for manual cleanup.
- **A focus steal landing mid-keystroke can corrupt the typed body without
  tripping `REPLY_BODY_TYPING_INTERRUPTED`** (the per-chunk guard checks
  before each chunk, not during one). Verification still fails closed with
  `REPLY_BODY_MISMATCH`; the caller deletes the named artifact and retries.
- **`verify_draft`'s `body_preview` is capped at 5000 characters** (a
  pre-existing cap, unchanged here), so `expected_body_contains` needles for
  replies longer than that must target the body prefix, not the tail.
  `reply_to_email`'s internal full-body verifier has no such cap.

## 3.11.0 - 2026-07-10

Automatic reply-state annotation: every primary read and triage tool now reports
whether an email was already answered or already has a reply draft, with no
opt-in flag required, so agents never double-draft a reply that exists.

### Added

- **`was_replied_to` on every discovery row** (always present, no parameter
  gates it): `list_inbox_emails`, `search_emails`, `get_email_by_id`,
  `get_email_by_ids`, `get_email_thread`, `get_needs_response`,
  `inbox_dashboard`, and `get_inbox_overview` recent rows now read Mail's
  native `was replied to` flag inside the existing per-message AppleScript
  property pass (measured ~15ms/message marginal, no extra round trip).
- **`has_draft` on the same rows** (`true` / `false` / `null`; `null` means
  the draft scan was skipped or errored, never silently false): one bounded
  Drafts-mailbox snapshot per account per call (`DRAFT_LOOKUP=75` cap,
  ~2s flat) correlates drafts to candidates by In-Reply-To/References header
  match (headers read for the newest `DRAFT_SNAPSHOT_HEADER_CAP=10` drafts)
  or by normalized-subject equality plus draft-recipient equals sender plus
  draft date not before the email. Governed by a per-tool
  `include_draft_state: bool = True` escape hatch; annotation is automatic
  by default. JSON responses carry a top-level `draft_scan` status object;
  text modes append `[REPLIED]` / `[HAS DRAFT]` markers.
- **`core/reply_state.py`**: shared snapshot builder, correlation rule, and a
  localized Drafts-mailbox name resolver ("Drafts", "Brouillons",
  "Entwürfe", "Borradores"), the fallback treatment Inbox and Sent
  mailboxes already had.
- **`exclude_drafted: bool = False`** on `list_inbox_emails` and
  `search_emails` alongside the existing `exclude_replied`.
- **`include_drafted: bool = False`** on `get_needs_response`.

### Changed

- **`get_needs_response` now excludes already-handled mail by default**: rows
  with `was_replied_to=true` or `has_draft=true` are skipped and reported via
  visible `skipped_replied_count` / `skipped_drafted_count` fields.
  `include_already_replied=True` / `include_drafted=True` restore them,
  annotated. On a draft-scan error nothing is excluded for draft state
  (fail-open) and `draft_scan.status` reports `"error"`. The legacy
  `check_already_replied` Sent-header scan remains as an opt-in extra
  verification layer.
- **`exclude_replied` on `list_inbox_emails` / `search_emails`** now filters
  on the native flag instead of a Sent-mailbox header scan (faster, no scan
  cap interaction). `flag_replied` is deprecated but still accepted; the
  canonical field is `was_replied_to`.
- **Skills and references** (`pre-draft-verification`, `recent-first-triage`,
  triage/drafting/management skills) now teach the row-level
  `was_replied_to` / `has_draft` check as the primary pre-draft duplicate
  guard, with the thread check as fallback.
- `get_awaiting_reply` is intentionally unchanged: it tracks the opposite
  direction (did they reply to me), for which no native Mail property exists.

## 3.10.1 - 2026-07-10

### Changed

- **`email-archive-cleanup` skill: Human-Sender Screen.** The archiving skill now
  applies a conservative human-sender filter at the dry-run/preview stage before
  any message becomes an archive candidate. It never archives mail from a real
  person the user corresponds with unless it is confidently spam; archiving is
  reserved for promotional and marketing mail, newsletters, automated updates and
  notifications, receipts, order/shipping/calendar/system notices, and obvious
  spam. When the sender's nature is uncertain, the safe default is to leave the
  message visible in the inbox rather than archive it.

## 3.10.0 - 2026-07-10

Apple Calendar tool surface: 10 new MCP tools (41 total), 2 new workflow skills
(11 total), and a hybrid calendar engine, all behind the same safety doctrine as
the mail surface.

### Added

- **10 Apple Calendar tools**: `list_calendars`, `list_events`, `get_events_by_id`,
  `check_availability`, `create_event`, `batch_create_events`, `update_event`,
  `delete_events`, `manage_calendars`, and the `respond_to_invitation`
  documented-refusal shim (no public macOS API can RSVP).
- **Hybrid calendar engine**: Calendar.app AppleScript via the shared
  `run_applescript` lock is the guaranteed engine on every install surface; an
  optional EventKit read fast path (`pip install 'mcp-apple-mail[eventkit]'`)
  activates only when Calendars full access is already granted and never
  triggers the consent prompt from a tool call.
- **Bounded-read contract for calendars**: every event read requires a capped
  window (370-day width cap, 200-event return cap with paging, 750-occurrence
  recurring expansion ceiling, 20-calendar fan-out cap, and an aggregate
  240-second per-call budget with partial results). Central caps live in
  `constants.CALENDAR_BOUNDS`.
- **New mode gating for calendars** (stricter than the mail tools by design):
  `--read-only` removes every calendar write tool; `--draft-safe` additionally
  blocks calendar deletes (`CALENDAR_DELETE_BLOCKED`, env unlock
  `CALENDAR_ALLOW_DESTRUCTIVE=1`) and attendee invitation sends
  (`INVITE_SEND_BLOCKED`). Mail tool gating is unchanged; the server
  instructions now document the domain split.
- **Safety doctrine**: ID-first mutations with no fuzzy destructive selectors,
  dry-run-default deletes that abort on any unresolved id, a triple-gated
  calendar delete (preview, confirm, force), recurring mutations requiring
  `span='all_occurrences'`, allowlisted RRULE grammar, and attendee writes
  gated behind explicit `send_invitations=True` with
  `invitation_delivery: "platform_dependent"` disclosure.
- **Timezone correctness**: IANA `timezone` parameters everywhere, dual
  zone-local plus UTC output, and integer-component AppleScript date
  interpolation (no locale string coercion).
- **2 new workflow skills**: `calendar-operator` (bounded reads, ID-first
  mutations, TCC troubleshooting) and `meeting-scheduler` (find-slot workflow,
  cross-timezone discipline, the .ics-via-Mail invitation alternative), plus
  the shared `calendar-safety-limits.md` reference.
- **CLI**: `apple-mail calendars`, `apple-mail calendar-events`, and
  `apple-mail calendar-grant` (the only code path allowed to request EventKit
  access; human-run, terminal only, permission-specific exit codes).

### Fixed

- **Cross-engine event ids now round-trip** (default `auto` mode): the EventKit
  read engine reports `calendarItemIdentifier` (the value Calendar.app AppleScript
  exposes as `uid` on every account type, verified live against Google-CalDAV,
  iCloud, and local stores) as `event_id`, not `calendarItemExternalIdentifier`
  (a `...@google.com` / hex id that never resolved through the AppleScript writer).
  A create id now round-trips to `get_events_by_id` / `update_event` /
  `delete_events` under the shipped default; the external id is preserved as a
  secondary `external_id` payload field.
- **Recurring delete never reports an unverified whole-series success**:
  Calendar.app scripting cannot delete a whole recurring series (its `delete`
  removes only the targeted occurrence and rule-clearing is silently ignored,
  proven live). `delete_events` now re-queries the series after deleting and returns
  the structured `RECURRING_DELETE_INCOMPLETE` (with the surviving occurrence dates
  and a Calendar.app remediation) when occurrences survive, instead of the previous
  false `recurring_deleted_whole_series: true`. `update_event` still mutates the
  whole series (that path works and is verified live).
- **`manage_calendars(action="delete")` works**: the delete script now uses the
  inline `delete (first calendar whose name is ...)` specifier, which deletes
  cleanly (including non-empty calendars whose events cascade away); the previous
  variable-bound `delete targetCal` form failed live with `AppleEvent handler
  failed`. Generic Calendar.app write failures now surface the structured
  `CALENDAR_WRITE_FAILED` error instead of raw `Error:` text.
- **All-day create echo instant**: `create_event` / `batch_create_events` all-day
  responses now echo the host-local calendar-date midnight actually stored (so the
  echo matches a later `get_events_by_id`), instead of the requested-zone midnight
  instant, which for a far-east or far-west zone described a moment hours away from
  the stored event.
- **All-day timezone date shift**: all-day events now land on the requested
  calendar date in the requested zone instead of the host-local conversion of
  midnight-in-zone. Previously an all-day request in a zone far east or west of
  the Mac could roll the date back or forward one day (`create_event`,
  `batch_create_events`, and `update_event` all-day paths).
- **Delete access-denied is no longer soft**: an Automation-denied
  (`-1743`/not authorized) `delete_events` now raises the structured
  `CALENDAR_ACCESS_DENIED` remediation like create/update, instead of reporting
  a "successful" empty delete.
- **Recurring write lookup**: `update_event` and `delete_events` widen the
  write-side uid lookup for recurring targets back by the 400-day recurring
  lookback horizon (still date-bounded), so a standing series whose master
  started before the read window no longer spuriously returns `EVENT_NOT_FOUND`.
- **Attendee-removal honesty**: `update_event` no longer reports
  `attendees_changed`/`invitation_delivery` for a removal-only or empty
  attendee diff (Calendar.app scripting cannot remove attendees); it returns an
  explicit "attendee removal is unsupported" note instead.

### Changed

- **Recurring coverage disclosure**: `list_events` and `check_availability`
  now surface `recurring_lookback_days` and a `recurring_coverage_note` when the
  AppleScript recurring-master pass runs, so callers know standing series older
  than the 400-day horizon may be missing (the EventKit engine expands
  natively and carries no such note).
- **Honest `output_format="text"`**: `get_events_by_id`, `update_event`,
  `delete_events`, `batch_create_events`, and `manage_calendars` now emit
  compact text summaries in text mode instead of pretty-printed JSON, matching
  `list_events`/`list_calendars`/`check_availability`.
- **Docstrings**: `list_events` states the 280-char `notes_preview` query match
  limit and the recurring lookback horizon; `update_event`/`delete_events`
  document the recurring-target lookup requirement and all-day moves.

### Notes

- Invitation delivery and RSVP are platform gaps, not omissions: no public
  macOS API guarantees invitation transmission, and EventKit participant
  status is read-only. Both are documented in the tools and skills.
- `DEFAULT_CALENDAR` (env) sets the create target; unscoped reads fan out
  across calendars (capped), which deliberately differs from mail's
  account-scoping default and is documented in each fan-out tool.
## 3.9.4 - 2026-07-10

Bundled-skill guidance accuracy pass. A parallel review of all nine workflow
skills against the live tool signatures found copy-paste examples that would
fail or churn on first use; this release corrects them. No tool-surface or
behavior change (still 31 tools).

### Fixed

- **`get_top_senders(limit=...)` examples corrected to `top_n=...`.** The tool
  has no `limit` parameter, so the documented call raised a `TypeError`. Fixed
  in the mail-rules-advisor, email-management, and mailbox-taxonomy skills.
- **`get_email_thread` / `get_email_by_id` / `get_email_by_ids` examples now
  pass the required `account`.** Unlike `search_emails` / `list_inbox_emails`,
  these three tools have no `DEFAULT_MAIL_ACCOUNT` fallback, so examples that
  omitted `account` failed with a missing-argument error. Fixed across the
  drafting, style-profile, management, triage skills, the shared
  `exchange-account-patterns` reference, and the CLI `show` example.
- **`list_inbox_emails` result now read via the `emails` key, not `items`.** A
  cleanup template read `result["items"]` off a `list_inbox_emails` result
  (its shape is `{"emails": [...]}`), which raised a `KeyError`.
- **`get_inbox_overview` example scoped with an explicit `account`.** That tool
  does not honor `DEFAULT_MAIL_ACCOUNT` and otherwise fans out across every
  configured account, so the triage skill now passes `account` and notes the
  behavior.
- **Dropped the stale "list rows may lack `message_id`" caveat.** List output
  always includes `message_id`, so the guidance to re-resolve via an extra
  `search_emails` round-trip was removing a bounded fast path.
- **`get_statistics` docstring corrected** to the current per-mailbox cap of 50
  messages (was "10 mailboxes by 75; longer windows 20 by 250").
- Minor skill fixes: `skills/CLAUDE.md` reference-sync table completed, an
  inconsistent `recent_days` ladder example, an inflated `max_moves` example,
  and a mis-attributed `max_moves` cap on `manage_trash`.

## 3.9.3 - 2026-07-09

Safe-by-design bounded mail access. Every scan, search, and export is now hard
capped so a single tool call can never trigger a Mail.app cold-cache read storm
or 98% CPU spin on a large Exchange or Gmail inbox (tool count unchanged at 31).

### Changed

- **Hard scan ceilings on `search_emails` and `list_inbox_emails`.** A single
  call now scans at most 50 messages regardless of `limit`, `recent_days`, or
  window size. The underlying `SCAN_BOUNDS` were lowered across the board
  (base, window, per-day scaling, and body-search auto caps) so large inboxes
  no longer force thousands of uncached message reads per call.
- **`get_statistics` per-mailbox reads are hard capped** at the same 50 message
  ceiling for both short and long windows. Longer windows fan across more
  mailboxes instead of reading deeper into any one of them.
- **`export_emails` is bounded and cannot exceed 50 emails per call.** Requests
  above the cap (via `max_emails` or an over-long `message_ids` list) are
  rejected before Mail.app runs. `entire_mailbox` exports a paged slice
  (default 25) rather than listing the whole mailbox.
- **`full_inbox_export` is disabled.** It now returns a structured
  `UNBOUNDED_EXPORT_DISABLED` error that redirects to the bounded
  `export_emails`, `list_inbox_emails`, and `search_emails` tools. The tool
  stays registered so existing configs keep loading.
- **AppleScript calls are serialized through one process-wide lock.** Parallel
  (concurrent) Mail tool calls now queue instead of contending for Mail.app,
  and internal fan-out that previously issued parallel Mail queries runs
  sequentially. Server instructions and the bundled large-inbox skill rules
  advise agents to call one Mail tool at a time.
- **Missed-replies workflow on `verify_draft` and `verify_drafts`.** A new
  opt-in `resolve_source` (with `resolve_recent_days`) maps a reply draft back
  to its source inbox message through a bounded `internet_message_id` lookup and
  returns a `source` block. Defaults preserve the prior output shape. New
  bounded "missed-replies queue" guidance in the email-drafting skill.

### Fixed

- **Thread and correspondent exports no longer open the virtual "All Mail"
  container** that Gmail accounts expose (it cannot be opened and caused export
  failures). They now scan real mailboxes (`INBOX` plus Sent variants).
- **`single_email` export creates its destination directory** before writing.
- **Draft age now reads `date received`** (always populated) instead of
  `date sent` (unset for never-sent drafts, which previously showed as unset).
- **`get_statistics` window cap inversion** where a longer window could apply a
  smaller per-mailbox cap than a shorter one.

## 3.9.2 - 2026-07-09

### Changed

- **`export_emails` bounded scopes** now support sender/date filtered export,
  correspondent history export that includes Sent by default, thread export by
  `message_id`, and paged `entire_mailbox` slices. Unsupported formats such as
  `pdf` now fail before Mail.app runs instead of reporting a zero-file success.
- **Claude Code and Codex marketplace registry** — Marketplace key renamed from
  `apple-mail-mcp` to `Agentic-Assets`; plugin selector is now
  `apple-mail@Agentic-Assets`. User installs register the GitHub-backed
  marketplace (`Agentic-Assets/apple-mail-mcp` or the `.git` URL for Codex)
  instead of a local checkout path. Validators, refresh scripts, and install
  docs updated; legacy uninstall commands remain in README for migration.

## 3.9.1 - 2026-06-30

Internal module-line-budget cleanup. No behavior change, no tool-surface change
(still 31 tools); all checks and live behavior preserved.

### Changed

- **Oversized modules split into packages to satisfy the 600 LOC budget.** The
  plugin runtime modules (`cli.py`, `core.py`, and the `tools/` handlers
  `analytics`, `compose`, `inbox`, `manage`, `search`, `smart_inbox`) and the
  dev-infra validator `tools/validate_manifests.py` are now packages of
  cohesive submodules. `validate_manifests.py` stays the entry point invoked by
  `tools/validate_manifests.sh`; its checks moved to `tools/manifest_checks/`
  and are re-exported so the test suite and CI call sites are unchanged. The
  module-line-budget baseline (`tests/fixtures/module_line_budget/baseline.json`)
  is now empty.
- **Test suite reorganized into per-area subfolders** (`tests/<area>/`) with the
  collected-test count tracked in `tools/expected_test_count.txt`.

## 3.9.0 - 2026-06-30

Native-only reply drafting enforced. The windowless `native_format=False` path
is now gated so agents can no longer drift into the plain-text fallback that
drops Mail's colored quote bar and logo signature.

### Added

- **`allow_windowless_fallback` parameter on `reply_to_email`** (default
  `False`). Passing `native_format=False` without
  `allow_windowless_fallback=True` now returns the structured error
  `WINDOWLESS_FALLBACK_DISABLED` before any AppleScript runs. The windowless
  object-model path remains available for deliberate headless/bulk/CI runs
  where no GUI focus or Accessibility permission is available; agents must
  never set `allow_windowless_fallback=True` on their own.

### Changed

- **`REPLY_WINDOW_FOCUS_FAILED` remediation no longer offers the fallback.**
  The `alternative` field now tells callers to retry with
  `native_format=True` (the default) once Mail can take focus, or to stop and
  report the blocker. It no longer mentions `native_format=False`, so the tool
  itself no longer steers agents toward the plain-text path.
- **Skill and docs guidance rewritten to native-only.** `email-drafting`,
  `apple-mail-operator`, `inbox-triage`, `email-management` templates, the
  shared `pre-draft-verification` and `agent-id-first-workflow` references,
  `README.md`, `tools/CLAUDE.md`, `skills/CLAUDE.md`, and
  `docs/CLAUDE-conventions.md` now state that native drafting is the only
  supported reply method and that the windowless path is gated. The
  `email-drafting` skill leads with a binding "Native drafting only" rule.

## 3.8.0 - 2026-06-30

Native-format reply drafts. `reply_to_email` now defaults to Mail's native reply
window so saved drafts keep the colored quote bar and the account's default logo
signature, with a windowless fallback preserved for headless and bulk use.

### Added

- **`native_format` parameter on `reply_to_email`** (default `True`). The native
  path opens Mail's `reply ... with opening window`, which renders Mail's own rich
  quoted thread and default reply signature, then types `reply_body` above the quote
  with a System Events keystroke (never the clipboard). Set `native_format=False`
  for the windowless object-model path (plain-text quote, no signature logo, no
  Accessibility permission required) for headless, bulk, or CI use.
- **`REPLY_WINDOW_FOCUS_FAILED` structured error.** When the native path cannot
  bring the reply window into focus, it aborts without saving and returns a
  structured error that points callers at `native_format=False`.
- **Module line budget gate.** `tools/check_module_line_budget.py` and
  `tests/test_module_line_budget.py` warn on modules over **600 LOC** in
  `plugin/apple_mail_mcp/` and `tools/`, and fail CI on baseline regression
  (`tests/fixtures/module_line_budget/baseline.json`). Runs in `dev-check.sh`,
  `validate_manifests.py`, pre-commit, and GitHub CI. Documented in
  `docs/CLAUDE-conventions.md` § Module line budget.

### Changed

- **Reply verification is line-break-insensitive.** The saved-draft verifier now
  strips CR/LF before matching, so a soft-wrapped first line no longer trips a false
  `BODY_MISSING`. The native default also skips signature substring matching (Mail's
  own logo signature cannot be reliably substring-matched) and never pins the
  account alias on the native window (pinning had dropped the embedded logo).
- **Attachment verification matches names as a multiset.** Reply-draft verification
  and `verify_draft` / `verify_drafts` now require each expected attachment name to be
  present with its full multiplicity (duplicate filenames are consumed one for one)
  and compare raw Mail attachment names, so a draft missing one of two identically
  named files is reported as `missing` rather than passing.
- **Agent guidance ID-first alignment.** Skills, `common-workflows.md`, README tool
  table, `apple-mail-mcpb/manifest.json`, and compose/manage/analytics docstrings now
  tell the same story: `message_id` / `message_ids` required on action tools;
  `subject_keyword`, `sender`, and `draft_subject` are schema-compat only
  (`TARGET_SELECTOR_DEPRECATED`). New canonical references:
  `plugin/skills/references/agent-id-first-workflow.md` and
  `pre-draft-verification.md` (per-skill copies via `tools/sync_skill_references.py`,
  enforced by `tests/test_packaged_skill_paths.py`). Extended
  `tests/test_id_first_guidance.py` for README, manifest, and template traps. Stale
  banners on historical task docs (`scalability-24k-hardening`, `id-first-refactor-spec`,
  `LIVE_FIELD_REPORT`).

### Notes

- The native path needs the host process to hold macOS Accessibility permission
  (System Events keystroke); `native_format=False` avoids it.
- 981 collected tests; tool count unchanged (31).

## 3.6.1 — 2026-06-07

Codex plugin install-smoke regression recovery and test-count verification.

### Fixed

- **Codex plugin install surface** — recovered `plugin/.codex-plugin/plugin.json` versioning and marketplace routing after Codex setup work (2026-06-07).
- **Test count verification** — confirmed 798 tests + 30 subtests via `pytest --collect-only -q` in CI; updated root guidance.

### Changed

- **Documentation alignment** — manifest validator, release gate, and CI now all source from canonical `pyproject.toml` version (3.6.1).
## 3.7.1 — 2026-06-09

Tighter centralized ``SCAN_BOUNDS`` for large-mailbox performance. Tool count
unchanged (28).

### Changed (scan caps)

Search window ceiling **250** (was 500), search base **100** (was 200), inbox
unread scan max **500** (was 1000), ``mailbox="All"`` fan-out unchanged at
**10** folders, explicit multi-mailbox search cap **20** (was 50).
``compute_scan_upper_bound()`` reads defaults from ``SCAN_BOUNDS`` (scale **25**
days/message, was 50). ``get_statistics`` short windows: 10 mailboxes × **75**
messages; longer windows: 20 × **250**.

## 3.7.0 — 2026-06-09

ID-first mutation hardening for large mailboxes (24k+). Tool count unchanged (28).

### Changed (performance / agent safety)

- **`move_email`**, **`update_email_status`**, **`manage_trash`**: filter-based scans
  (subject/sender/date) now require **`allow_filter_scan=True`**. Default path is
  **`message_ids=[...]`** from a prior `list_inbox_emails` or `search_emails` call.
  Filter escape hatch responses include an explicit slow-scan warning.
- **`search_emails`**: **`body_text`** requires **`allow_body_scan=True`** or returns
  structured **`BODY_SCAN_DISABLED`**.
- **`mailbox="All"`** searches cap at **10** folders (was 50); JSON sets
  `mailboxes_truncated` when capped.
- Sender-only searches emit pairing hints (co-filter with subject or tight
  `recent_days`).

### Added

- Structured error **`FILTER_SCAN_DISABLED`** with remediation pointing to ID-first
  workflow and `allow_filter_scan` escape hatch.
- **`get_email_thread(message_id=...)`** for thread drill-down without subject
  re-search.
- **`list_email_attachments(message_ids=[...])`** and **`export_emails`**
  `single_email` **`message_id`** param.

### Fixed

- Mutation filter paths now pass **`recent_days`** into the search helper so scan
  caps use `compute_scan_upper_bound()` instead of bare `limit+1`.

## 3.6.0 — 2026-06-05

Compose-path race elimination + reliable draft lookup, from a second live draft-QA
session on the 24K Exchange account. The 3.5.0 `saving no` change was insufficient:
the reply/forward path was driving Mail's GUI, which is inherently racy. Tool count
unchanged (28); one additive optional param (`manage_drafts(subject_contains=...)`).

### Fixed (compose GUI races — data-loss/corruption class)

- **Reply/forward no longer leak a draft's body into the wrong thread, duplicate,
  or save empty.** `reply_to_email` and `forward_email` previously opened a Mail
  compose window (which auto-saves an empty draft *shell* → the duplicate), pasted
  the body from the **system clipboard** via `keystroke "v"` into whatever window
  had focus (→ body landing in an unrelated thread; or pasting nothing → empty
  draft), and closed with the **positional** `close window 1` (→ wrong window).
  Both tools now build the draft entirely through Mail's **object model**
  (`make new outgoing message` + `make new to recipient`), exactly like
  `compose_email`: **no window, no clipboard, no System Events, one `save`.** This
  removes the entire race class.
- **`reply_to_all=True` now includes every original party.** Instead of trusting
  Mail's reply-to-all (which silently dropped recipients), the reply now sets
  recipients **deterministically**: the original sender as To, and every other
  To/Cc party as Cc, excluding the sender and the account's own addresses.
- **Newly-created drafts are found reliably.** `manage_drafts(action="list")` and
  the draft lookup behind `send`/`open`/`delete` now read the **newest** drafts
  (a `messages startIdx thru totalDrafts` tail, newest-first) instead of the 100
  *oldest* (`messages 1 thru 100`), so a just-created draft is never missed when a
  mailbox holds >100 drafts. No date filter is used — fresh `outgoing message`
  drafts have a null `date received`, which previously made date-filtered draft
  searches silently drop them.
- **`compose_email` HTML path hardened** — the rich-HTML compose (which still needs
  the clipboard) now targets `window of newMsg` (and brings it to front before the
  paste) instead of the positional `window 1`.

### Added

- **`manage_drafts(subject_contains=...)`** — optional case-insensitive, in-loop
  subject filter for `action="list"`, giving a fast, bounded "find the draft I just
  created" lookup over the small Drafts mailbox. Prefer this (or `get_email_by_id`)
  over `search_emails` for draft verification — `search_emails` runs a date-filtered
  scan that is slow on large accounts and drops null-date drafts.

### Changed (behavior trade-off)

- **Replies/forwards are now reliable plain-text "Re:"/"Fwd:" drafts.** Because the
  clipboard was the only thing inserting rich HTML, eliminating it means reply and
  forward bodies are plain text with a `> `-quoted copy of the original (bounded to
  4000 chars) and a `Re:`/`Fwd:` subject. The draft is **correctly addressed and
  always contains your text** — the priority after the cross-thread corruption.
  `body_html` is still accepted on `reply_to_email` for backward compatibility but
  is ignored. Trade-off: replies no longer carry native `In-Reply-To`/`References`
  headers (they thread visually via subject + quote). `create_rich_email_draft` and
  `compose_email` still produce rich HTML for genuinely standalone messages.

## 3.5.0 — 2026-06-05

Live field-report hardening (draft QA workflow on a 24K Exchange account) plus
the previously-unreleased mcporter wrapper + large-mailbox work. Tool count is
unchanged (28); changes are additive params/actions/fields, all backward
compatible.

### Fixed (draft QA field report)

- **Reply / forward / rich drafts no longer create duplicate drafts.** The
  draft paths persisted twice — an explicit `save <message>` *then*
  `close window 1 saving yes` — which committed a second, byte-identical copy
  to Drafts (observed as same-second duplicate pairs). Every draft path now
  persists exactly once (`save` then `close window 1 saving no`; the rich-draft
  helper keeps its single `Cmd+S` and closes with `saving no`). Verified live:
  one reply call yields exactly one threaded draft.
- **`search_emails` no longer hangs on Exchange when a per-mailbox scan is
  slow.** A per-mailbox `with timeout` wrapper (added during the unreleased
  work) fired on the 24K-message Exchange INBOX, and the inner candidate-fetch
  `try` swallowed the timeout into a silent **0-row** result. The wrapper is
  removed; per-folder failures are still isolated by the existing
  `on error → ERROR_MAILBOX` handler, and the whole call is bounded by the
  single outer timeout budget.
- **`get_email_by_id` header parsing tolerates value-less headers.** A bare
  `In-Reply-To:` / `References:` line (no value) would make the `text N thru -1`
  slice raise and the surrounding `on error` wipe *both* fields — discarding a
  sibling header that had already parsed cleanly. Length guards now skip empty
  header values so threading metadata survives.

### Added (draft QA field report)

- **`get_email_by_id` now returns threading + recipient metadata** so an agent
  can confirm a draft is a correctly-addressed reply without opening Mail:
  `to`, `cc`, `bcc`, `in_reply_to`, `references` (parsed from `all headers`),
  and a computed `has_quoted_original` flag. Single-message, bounded, fast.
- **`search_emails(mailboxes=[...])`** — new optional parameter to search an
  explicit list of folders (e.g. `["Archive", "Sent"]`) instead of one mailbox
  or paying for `mailbox="All"`. Missing folders degrade to a structured
  per-mailbox error rather than failing the call. Recommended over `"All"` on
  large Exchange/Gmail accounts.
- **`manage_drafts(action="list")` is now triageable** — each draft reports its
  `Id`, `To` recipients, and a short body snippet; new `hide_empty=True` skips
  orphaned blank drafts.
- **`manage_drafts(action="cleanup_empty")`** — removes orphaned blank drafts
  (blank subject **and** empty body). Preview-only by default (`dry_run=True`)
  with a `max_deletes` safety cap, matching the repo's destructive-op
  conventions.
- **CLI parity for the new draft/search surfaces.** `apple-mail search` gains
  `--mailboxes a,b,c` (comma-separated targeted-folder search); `apple-mail
  drafts list` gains `--hide-empty`; and a new `apple-mail drafts cleanup-empty`
  subcommand previews orphaned blanks by default and only deletes with
  `--execute` (`--limit` caps the batch). The repo CLI is the live-test harness,
  so these mirror the MCP params 1:1.

### Changed (draft QA field report)

- **Bulk `search_emails` no longer resolves per-message recipients.** Resolving
  `to recipients`/`address of` inside the bulk scan can *hang* (uncatchable by
  `on error`) on large remote mailboxes. Recipients are now fetched per message
  via `get_email_by_id` (and shown in `manage_drafts` list over the small local
  Drafts mailbox). The record layout reserves the fields, so they still surface
  wherever a tool populates them.

### Fixed (Gmail crash)

- **`list_inbox_emails(include_read=False)` no longer crashes on Gmail / Google
  Workspace accounts.** The historical AppleScript `(candidateMessages whose
  read status is false)` evaluated `whose` against a list of message
  references; on Gmail those refs point at `[Gmail]/All Mail`, which
  Mail.app rejects with `Can't get {message id N of mailbox
  "[Gmail]/All Mail" ...} whose read status = false`. Replaced with an
  in-loop `if read status of aMessage is false` filter (the same pattern
  `search_emails` already uses safely). Works on every account type
  including 24K+ Exchange inboxes.
- **`reply_to_email` / `forward_email` subject lookup hardened the same
  way.** The historical `whose subject contains "X" and date received >=
  cutoff` over a bound slice carried the same Gmail risk; the predicate is
  now evaluated in an AppleScript `repeat` loop with an early-exit on the
  date cutoff (slices are newest-first).
- **`bounded_scan.build_bounded_message_scan(..., whose_condition=...)`
  now raises `UNSAFE_WHOSE_ON_LIST`.** The footgun is gone — any future
  caller that needs to filter a bounded slice must use the new
  `build_bounded_filtered_scan(...)` helper, which emits the safe in-loop
  pattern by construction.

### Added

- **`list_inbox_emails(read_status=...)`** — new public parameter with the
  same vocabulary as `search_emails`: `"all"` (default), `"unread"`,
  `"read"`. The legacy `include_read: bool` / `unread_only: bool` kwargs
  continue to work but emit a `DeprecationWarning`.
- **`bounded_scan.build_bounded_filtered_scan(mailbox_var, scan_cap,
  target_max, condition_expr, ...)`** — new helper that emits the safe
  bounded-slice + in-loop filter pattern. The only sanctioned way to
  filter a bound slice by message property.

### Distribution

- **New `apple-mail.plugin` build artifact**: `tools/build-artifacts.sh` now
  emits `apple-mail.plugin` (byte-identical to `apple-mail-plugin.zip`)
  alongside the existing `.zip` and `.mcpb`. The `.plugin` extension is the
  canonical upload format for Claude Desktop's **Customize → Add plugin →
  Upload plugin** flow (Cowork), which was previously documented only as a
  generic `.zip` upload. Stale `.mcpb` files from 3.2.1 / 3.3.0 / 3.3.1
  cleaned from repo root; `.gitignore` covers the new `.plugin` artifact.

### Documentation

- **`search_emails` subject-only fast path**: narrow subject lookups (no sender,
  body, attachment, or read-status filters) now scan only the requested page
  size and skip per-message date/sender/read-status reads. No-hit lookups on
  large Exchange mailboxes that previously took 48–115s now complete inside
  the wrapper request ceiling. `recent_days` still controls the bounded slice
  for searches that include other filters.
- **`search_emails` recent-window early break**: bounded scans with a
  `date_from` lower bound now read `date received` first and `exit repeat`
  once messages cross the cutoff, avoiding subject/sender/read-status reads
  on messages outside the window.
- **`full_inbox_export` AppleScript syntax fix**: per-field `(try … end try)`
  expressions were invalid AppleScript inside a concatenation and aborted the
  tool with `-2741`. Replaced with per-field variable assignments inside a
  `try` block, then concatenated. Repro: `max_emails=1` through `--raw`.
- **`full_inbox_export` named-flag input**: `fields` now accepts a
  comma-separated string in addition to a list, so generated mcporter wrappers
  that flatten the list parameter still work without `--raw`.
- **`tools/patch_mcporter_wrapper.py`**: post-generation patch renames the
  mcporter global `--timeout <ms>` (which collides with per-tool `timeout`
  seconds) to `--request-timeout-ms`, and optionally repoints embedded
  `start_mcp.sh` paths for relocated plugin roots.
- **`check_wrapper_surface.py`** now flags the global `--timeout <ms>` flag
  in generated wrappers and reminds operators to run `patch_mcporter_wrapper.py`.
- **`validate_manifests._tracked_plugin_files`** is more defensive when
  `git ls-files` returns nothing while `plugin/` exists on disk.

## 3.4.0 — 2026-05-26

Hardening release: 15 real bugs fixed (1 HIGH security, 8 type-safety / None-handling,
3 silent-error / resource, 3 AppleScript-injection / shell-quoting) plus a new lint +
static-analysis + property-test baseline. No breaking changes to MCP tool signatures
or return shapes.

### Security

- **HIGH — `create_rich_email_draft` path traversal**: `output_path` accepted from
  the caller was written directly to disk without `validate_save_path` / sensitive-dir
  guard. An attacker could pass `output_path="~/.ssh/authorized_keys"` (or `~/.aws/credentials`,
  `~/.claude/settings.json`, `~/Library/Keychains/*`) and silently corrupt the file with
  a draft `.eml` body. Now resolved with `os.path.realpath(os.path.expanduser(...))`
  and rejected against the shared `SENSITIVE_DIRS` list before any write.
- **`search_emails` forgotten-wiring**: `escaped_sender = escape_applescript(sender)`
  was computed but never used; the raw `sender` string flowed into the AppleScript
  filter fragment. Now wired correctly so quote / backslash / newline injection
  characters are escaped before they reach `osascript`.
- **`compose.py` shell-quote consistency**: 6 `do shell script "cat '{path}'"` /
  `"rm -f '{path}'"` call sites in `_send_html_email` / `reply_to_email` /
  `forward_email` rewritten to `"cat " & quoted form of "{path}"`, matching the
  safe pattern already used for `body_temp_path`. Single-quoted bare paths are
  brittle if `tempfile.gettempdir()` ever returns a path containing a quote.

### Reliability

- **`validate_save_path` NUL-byte contract change** (minor API): paths containing
  `\x00`–`\x1F` or `\x7F` previously raised `ValueError` from `os.path.realpath`,
  bubbling an uncaught exception out of the MCP tool boundary. Now returns the
  standard structured-error string, matching every other validator in `core.py`.
  Surfaced by a new Hypothesis property test.
- **`analytics.py` entire-mailbox export file-handle leak**: the batch-export
  `on error -- Continue` handler skipped `close access fileRef`, leaking a kernel
  fd per failed message. Now closes inside a guarded `try / close access / end try`
  block, mirroring the single-email export path.
- **`core.fetch_replied_ids_impl` silent except**: caught `Exception` and returned
  empty `set()` for ALL non-timeout errors (`OSError`, `PermissionError`, broken
  Mail connection). Triage tools (`get_awaiting_reply`, `get_needs_response`)
  then falsely reported every sent message as awaiting reply. Now logs at
  `WARNING` with exception class + message before returning, while still
  returning empty so callers keep working.
- **`update_email_status` bulk-action silent fallback**: bulk
  `set read status of every message …` failures fell through to the per-message
  loop without surfacing the bulk error. Now captures `errMsg`/`errNum` in the
  `on error` block and emits a `BULKERR|errNum=… errMsg=…` row so callers see
  the real failure.
- **`subprocess.run(["open", "-a", "Mail", ...])` in `create_rich_email_draft`**:
  raised `CalledProcessError` / `FileNotFoundError` uncaught when Mail.app
  wasn't available or the `.eml` was malformed. Now wrapped in try/except
  returning a structured error.

### Type-safety (mypy: 27 errors → 0 across 16 source files)

- **`compose.py` `Optional[str]` flowing into non-None operations** (5 sites):
  `account.strip()` on `str | None` → `AttributeError`; `"Account: " + account`
  string concatenation with `None` → `TypeError`; `escape_applescript(account)`
  silently stringifying `None` to the literal `"None"` reaching synthesised
  AppleScript. Each fixed with an `assert account is not None` immediately
  after the `_resolve_account` error guard, documenting the invariant that
  a non-`None` account and a `None` error are mutually exclusive.
- **`_build_found_message_lookup` return type tightened** from
  `Tuple[str, Optional[object]]` to `tuple[str, ToolError | None]` —
  reflects the actual runtime invariant and stops mypy noise at every
  call site.
- **`inbox.py` `**dict[str, int | str | None]` typed-kwargs unpacking** (4 sites):
  a heterogeneous-value dict was spread into functions with per-param types,
  hiding potential `TypeError`s at runtime. Replaced with explicit kwargs at
  every call site. Same file: `body` variable shadowing (`Dict[str, Any]`
  then re-assigned `str`) fixed by renaming to `text_body`; `item` dict in
  `list_mailboxes` annotated as `Dict[str, Any]`.
- **`core.parse_email_list` missing annotations** on `emails` and `current_email`
  (residual pre-existing mypy warning) — annotated explicitly.

### Testing & static analysis

- **+279 tests** (suite 367 → 646+), all green:
  - +90 AppleScript script-idiom regression tests (`test_applescript_script_idioms.py`)
  - +12 `osacompile` parse-checks per builder (skips on Linux, runs on macOS CI)
  - +25 Hypothesis property tests on `escape_applescript`, `validate_account_name`,
    `validate_save_path` — found the NUL-byte bug
  - +33 `jsonschema` contract tests for `get_inbox_overview`, `list_inbox_emails`,
    `get_awaiting_reply`, `search_emails`, `get_email_thread`
  - +70 bug-fix regression tests (`test_compose_none_handling.py`,
    `test_compose_security.py`, `test_core_validators.py`, `test_search_escaping.py`,
    `test_inbox_typed_kwargs.py`, `test_analytics_resource_safety.py`,
    `test_core_fetch_replied_ids.py`, `test_manage_bulk_action_errors.py`)
- **New dev dependencies** under `[project.optional-dependencies] dev`:
  `ruff`, `mypy`, `pytest-cov`, `hypothesis`, `jsonschema`. Install with
  `pip install -e ".[dev]"`.
- **`tools/dev-check.sh lint` tier**: runs `ruff check`, `ruff format --check`,
  and `mypy` on the plugin source. Wired into the `release` tier.
- **`tools/pre-commit-validate.sh`**: now runs `ruff check` on staged Python files.
- **CI**: `.github/workflows/ci.yml` installs dev deps and runs `ruff check`
  on `plugin/ tools/ tests/`.
- **`pyproject.toml`**: `[tool.ruff]`, `[tool.ruff.lint]` (rules E, F, I, B,
  UP, SIM, RET, PTH), `[tool.mypy]` (permissive baseline, no `disallow_untyped_defs`),
  `[tool.pytest.ini_options]`.
- **Coverage baseline**: 78% measured (lowest: `__main__.py` 48%, `manage.py` 62%).

## 3.3.1 — 2026-05-26

Hotfix for a 3.3.0 regression in `get_awaiting_reply`: the Phase 2 inbox
header-extraction AppleScript used `header value of header named "X" of
msg`, which is not valid Mail.app dictionary syntax and failed to parse
with osascript `-2740` ("A application constant or consideration can't
go after this identifier"). Replaced with the standard `headers of
aMessage` iteration that filters by `name of aHeader` and reads
`content of aHeader`. The INBOXHDR row protocol consumed by the Python
parser is unchanged; tests cover the parser behavior, not the broken
AppleScript form, so no test churn was required.

Reproduced on live TU Exchange inbox (24K messages): pre-fix returned
`AppleScript error: ... syntax error ... (-2740)`; post-fix returns 4
sent emails awaiting reply over a 7-day window.

## 3.3.0 — 2026-05-26

Phase 2 + Phase 3 hardening: faster analysis paths, structured JSON across
the smart-inbox surface, and one targeted breaking change to
`list_inbox_emails` JSON mode.

### Breaking

- **`list_inbox_emails` JSON mode now returns a Python `dict`, not a JSON
  string.** Stable shape: `{"emails": [...], "errors": [...]}` for every
  `output_format="json"` success and per-account-timeout path.
  - `errors` is always present (empty list when nothing timed out).
  - Account-not-found in JSON mode also returns a dict (`{"error":
    "account_not_found", "account": ..., "available_accounts": [...],
    "emails": []}`).
  - Account-listing timeouts surface as
    `{"emails": [], "errors": ["__account_listing__"]}`.
  - When deprecated aliases (`limit`, `unread_only`) are used, a `warnings`
    list is attached to the same dict.
  - **`UNBOUNDED_SCAN_REQUIRED` refusal errors remain a JSON-encoded string**
    so text-mode and JSON-mode callers see the same payload for that hard
    refusal path.
  - Migration: callers that did `json.loads(result)` on the
    `list_inbox_emails` JSON output should drop the `json.loads` call. The
    repo CLI (`apple-mail list-inbox --json`) handles dicts and strings
    transparently through `_print_result`.

  See `plugin/apple_mail_mcp/tools/inbox.py` and
  `tasks/reference/robustness-backlog-2026-05-22.md` (Phase 3) for context.

### Performance

- **`get_statistics` (`account_overview` scope) uses Mail.app's cheap
  mailbox-count APIs** instead of per-message unread scans. AppleScript now
  emits a `MBOX|||name|||total|||unread` header row per sampled mailbox
  (via `count of messages of aMailbox` + `unread count of aMailbox`); the
  per-message `read status` fetch is gone. `total_emails` and `unread` now
  reflect true mailbox-wide totals across the sampled mailboxes;
  sample-bounded stats (`flagged`, `with_attachments`, `top_senders`,
  `mailbox_distribution` ROW-derived stats) still respect `days_back`.
- **`get_needs_response` reply matching moved to Python.** The inbox
  AppleScript emits a flat `MSG|||message_id|||...` row per candidate;
  replied detection runs as an O(1) set lookup in Python via
  `fetch_replied_ids` and `_normalize_message_id_token` (was O(N×M)
  AppleScript `repeat with repliedRef`). Header-based detection only
  (`In-Reply-To`, `References`) — no subject substring matching.

### Reliability

- **Silent per-message `on error` skips replaced with `errors[]`.** Inner
  per-message failures in `account_overview` are now counted per mailbox
  and surfaced as a single
  `__APPLE_MAIL_MCP_ERROR__|||mailbox|||N message(s) skipped due to read
  errors` line, parsed into the JSON `errors[]`.

### JSON / schema consistency

- **Smart-inbox tools accept `output_format="json"` and return dicts with
  stable keys + `errors[]`:**
  - `get_needs_response` → `{account, mailbox, days_back, max_results,
    high_priority, normal_priority, skipped_replied_count, errors}`
  - `get_awaiting_reply` → `{account, days_back, max_results, awaiting,
    errors}`
  - `get_top_senders` → `{account, mailbox, days_back, top_n,
    group_by_domain, senders, total_analysed, mailbox_count,
    unique_senders, scan_cap, errors}`
  - Error and timeout paths return dicts in JSON mode.
- `inbox_dashboard` JSON path returns a Python dict (already true in code;
  verified and documented).

### Docs

- `docs/AGENT_LIVE_TESTING.md` gains a "`--raw` examples for advanced
  wrapper options" subsection covering `get-inbox-overview`,
  `get-statistics` (three scopes), smart-inbox triage, `inbox-dashboard`
  JSON mode, and `full-inbox-export`.

See `tasks/reference/robustness-backlog-2026-05-22.md` Phase 2 + Phase 3 for the
backlog this batch closes.
