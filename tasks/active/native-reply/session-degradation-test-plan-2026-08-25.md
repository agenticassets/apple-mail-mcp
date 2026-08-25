# Native reply: why the chunk-size sweep was invalid, and how to test degradation properly

**Status:** findings recorded, test plan written, **no new live testing run yet** (deliberately — the
instrument is broken and must be fixed first).
**Date:** 2026-08-25. **Supersedes** the § 4 conclusion in
[`live-timing-and-frontmost-2026-08-24.md`](live-timing-and-frontmost-2026-08-24.md).

Read this before running any further native-reply timing experiment.

**Linear:** every finding below is filed. `AGENTIC-2522` is the tracking issue and carries the
state-of-knowledge table (what is proven, what is withdrawn, what is untested) — start there.
Children: `AGENTIC-2517` (verifier accepts a foreign draft — blocks re-measurement),
`AGENTIC-2518` (`DRAFT_LIST_CAP` and the false verification timeout), `AGENTIC-2520` (the
inter-chunk delay, the likely real fix), `AGENTIC-2521` (the Accessibility tree walk),
`AGENTIC-2519` (compose-window cap blind on Tahoe).

---

## 0. The one-paragraph version

The 2026-08-24 sweep concluded there was a corruption cliff in `TYPING_CHUNK_SIZE` "between 160 and
200" and shipped 160. **That conclusion is withdrawn.** All four 160 "passes" were verified against a
draft that a *previous run* had created, not their own. Every row in the sweep carries
`exact_id_verified: false`. The mechanism is a chain that starts with the test account holding **1,045
drafts** against a `DRAFT_LIST_CAP` of **75**. What survives the audit: 200 and 250 genuinely corrupt
at 2,400 characters, and 80 and 120 genuinely pass. Where the cliff actually sits is **unknown** — it
may be between 120 and 160.

---

## 1. What was overturned, and the evidence

### 1.1 The sweep verified the wrong artifact

`reply_to_email` returns a `draft_id`. Across the fifteen 2,400-character runs:

| chunk size | runs | reported an id no earlier run had | reported the id the **immediately preceding run** reported |
|---|---:|---:|---:|
| 80 | 3 | **3** | 0 |
| 120 | 4 | **4** | 0 |
| 160 | 4 | **0** | **4** |

Every 160 run inherited its predecessor's draft id. Not one produced an artifact traceable to itself.

Corroborating tell: 160's elapsed time was **31.5 s on all four runs, to the tenth of a second, across
twelve minutes**, while 80 spread 0.8 s and 120 spread 0.9 s over the same session. Four independent
GUI-automation runs landing in a single 0.1 s bin is not a typing path being measured. It is the same
cached artifact being re-found.

### 1.2 The causal chain

1. The production test account's Drafts mailbox holds **1,045 messages** (1,126 across all accounts).
2. `DRAFT_LIST_CAP = SCAN_BOUNDS["DRAFT_LOOKUP"]` is **75**
   ([`plugin/apple_mail_mcp/constants.py`](../../../plugin/apple_mail_mcp/constants.py)), whose comment
   reads "drafts mailboxes are usually small."
3. The draft-identity resolver opens with `if totalDrafts > draftCap then return missing value`
   ([`reply_draft_resolver_scripts.py:26-27`](../../../plugin/apple_mail_mcp/tools/compose/reply_draft_resolver_scripts.py)).
   At 1,045 > 75 it returns immediately. **This host has never resolved a native reply draft identity.**
4. With no identity, `_verify_saved_reply_draft` falls to a subject-scan over the newest 75 drafts
   ([`saved_draft_checks.py:521-554`](../../../plugin/apple_mail_mcp/tools/compose/saved_draft_checks.py)),
   accepting **any** draft whose subject matches and whose body contains the expected body.
5. Every sweep run typed a **byte-identical body** into a reply to the **same source message**. So every
   prior successful draft was a valid false-positive match.

The verifier was not lying — `exact_id_verified: false` and `draft_id_source: "verification_fallback"`
are stamped on every row. The sweep read past them.

### 1.3 The model fits every observation

- Runs 7-10 (200/250) failed: they were the *first* 2,400-char runs, so no matching draft existed yet
  to fall back on. **Failures cannot be manufactured by this channel — contamination only fakes passes.**
- Run 11 (80) passed with its own id: the first genuine 2,400 success.
- Runs 12-21 passed, 160 always inheriting the previous run's id.
- The shipped-160 default-path run, 11 hours later, **failed** — by then the matching drafts had aged
  out of the newest-75 window, the crutch was gone, and 160 was tested honestly for the first time.

One model explains all of it, with no cliff between 160 and 200 and no session degradation required.

---

## 2. What survives the audit

| Claim | Status |
|---|---|
| 200 and 250 corrupt at 2,400 chars | **Supported** — 4/4 genuine failures |
| 80 is safe at 2,400 chars | **Supported** — 3/3 uniquely attributed |
| 120 is safe at 2,400 chars | **Supported** — 4/4 uniquely attributed |
| Body length interacts with chunk size | **Supported** — 200 passes cleanly at 1,200, fails 4/4 at 2,400 |
| "Cliff between 160 and 200" | **Withdrawn** — 160 has zero attributable passes |
| Where the cliff sits | **Unknown** — plausibly between 120 and 160 |
| Session degradation is real | **Undetermined by the sweep** (see § 3) |

**A run-order confound was checked and refuted.** Sizes were interleaved within batches, and the
200/250 failures occupied the *first four* positions of the 2,400-char sequence while eleven later runs
passed. Mean run position: 200/250 = 2.5, 80 = 8.7, 120 = 9.0, 160 = 12.0. Session degradation predicts
the opposite pattern. Within-size drift across the whole session was under 1 s against a 7.8 s
between-size spread.

**Nuisance variables noted for the record:** the failing 200/250 batch was the only batch overlapped by
concurrent `pytest` runs — which matters, because the mechanism is a timing race.

---

## 3. Session degradation: what is now measured

The sweep recorded only `mail_rtt_before_s` (`count of accounts`), a liveness probe that reads 0.10-0.13 s
whether or not the compose path is degraded. **It could not see the thing being hypothesized.** That is
why § 2 says "undetermined" rather than "refuted."

### 3.1 A degradation reading that turned out to be load — recorded as a caution

An apparent live demonstration was collected today and then **invalidated by its own covariates**. It is
kept here because the failure mode is instructive, not because it is evidence.

After a heavy Drafts property scan on freshly restarted Mail, responsiveness looked catastrophic:
`ax_rtt_s` 0.31 → **4.49 s**, `drafts_rtt_s` 0.22 → **9.80 s**, `mail_rtt_s` 0.16 → 0.50. That reads as a
14x Accessibility regression, on exactly the path the typing code depends on.

**Then the recovery series printed the load average: 103.84.** Three Opus subagents and an unbounded
AppleScript scan were running concurrently. The machine, not Mail, was the bottleneck. One minute later
at load 41.9, and again at load 20.3, every metric was back to fresh baseline:

| metric | fresh baseline | "degraded" (load ~100) | +1 min (load 41.9) | +2 min (load 20.3) |
|---|---:|---:|---:|---:|
| `mail_rtt_s` | 0.16 | 0.50 | 0.15 | 0.15 |
| `ax_rtt_s` | 0.31 | 4.49 | 0.32 | 0.32 |
| `drafts_rtt_s` | 0.22 | 9.80 | 0.22 | 0.23 |

**Two real conclusions survive.** First: **Mail recovered fully without a restart** — in this instance
the degradation was transient and self-clearing, which is a point *against* the restart being necessary.
Second, and more useful: **this is the same error class as § 1.** A measurement was taken, it agreed with
the hypothesis, and it was nearly written up before the confound was checked. § 5.5 and § 5.6 exist
because of this — they were violated by the very measurement offered as evidence for them.

### 3.2 Why the reply path is nonetheless expensive on this mailbox

Re-measured at load 9.5 on the production test account (Gmail-backed IMAP Drafts, 1,045 messages):

| operation | N=10 | N=50 | per-item |
|---|---:|---:|---|
| resolve slice `messages 1 thru N` only | 0.22 s | 0.29 s | ~1-6 ms, near-flat |
| same slice, plus one `id` property read each | 0.59 s | 7.32 s | **37 ms → 141 ms** |

Slice resolution is nearly free. **Property reads are not**, and the per-item cost *grows with depth* —
37 ms across the newest 10, 141 ms across the newest 50 — consistent with a warm cache at the head and
server round trips behind it. (The earlier figure of ~530 ms/read was taken at load 100+; treat it as
void.)

This is the number that matters, because the verification fallback reads `subject` on up to 75 drafts,
**20 times** (`repeat with verifyAttempt from 1 to 20`, `delay 1`,
[`saved_draft_checks.py:488,557`](../../../plugin/apple_mail_mcp/tools/compose/saved_draft_checks.py)).
At ~140 ms/read that is ~10 s per attempt against a 60 s verification budget — so the poll cannot
finish, and a **correctly saved draft gets reported as `REPLY_DRAFT_VERIFICATION_TIMEOUT`**. The same
arithmetic explains why the 75-draft short-circuit in the identity resolver is load-bearing: without it,
75 drafts × 2 property reads × 3 retry passes would be well over a minute inside the compose script.

### 3.3 The self-reinforcing loop

Confirmed by code reading, not yet by experiment:

1. A `REPLY_BODY_MISMATCH` fires *after* `save`, so the draft is permanent. With identity unavailable,
   `can_retry` is unreachable (it requires `native_draft_identity is not None`,
   [`reply.py:494-503`](../../../plugin/apple_mail_mcp/tools/compose/reply.py)) and the remediation
   explicitly tells the caller not to delete it.
2. That draft now sits in the newest-75 window with a matching subject, so **every subsequent run pays a
   full `content` read plus ~35 string-rewrite passes on it, on each of 20 attempts**.
3. A *timeout* is worse: `subprocess.run(timeout=…)` SIGKILLs the child, so the AppleScript `on error`
   block never runs and **the compose window is never closed**. Nothing in the reply path ever reaps a
   window left by a previous run.
4. More open windows → the adoption scan is **O(W²)**
   ([`reply_window_scripts.py:33-49`](../../../plugin/apple_mail_mcp/tools/compose/reply_window_scripts.py))
   → higher odds `front window` is the wrong one → more failures.

Windows do not survive a Mail restart; drafts do. **That asymmetry is the sharpest available test** of
which accumulator is driving the symptom (§ 5.3).

### 3.4 The timeout projection does not model any of this

`_native_reply_effective_timeout` ([`reply_typing_budget.py`](../../../plugin/apple_mail_mcp/tools/compose/reply_typing_budget.py))
computes `chunk_count × 1.0 s`, plus the editor-drain budget `typing_settle_attempts(bodyLength) × 0.25 s`,
plus a flat 35 s and 30 s slack. Both scaled terms grow with the body; everything in § 3.2 and § 3.3 lives
inside that flat 35 s, which was fitted as the intercept of four successful runs on one machine at one
moment. As drafts and windows accumulate, real fixed overhead grows while the budget does not — until
the SIGKILL fires, which leaks another window, which raises the overhead further.

---

## 4. Fix the instrument before running anything else

**No timing experiment is worth running until a pass cannot be satisfied by a previous run's draft.**
Four changes, in order:

1. **Unique nonce per run.** Embed `RUN-<uuid>` in the body so no earlier draft can match on
   body-contains. Cheapest possible fix and it alone breaks the contamination channel.
2. **Treat unattributed verification as `INDETERMINATE`, never as a pass.** If `exact_id_verified` is
   false, the run produced no evidence. The harness must refuse to score it.
3. **Delete the run's draft afterward**, so Drafts depth is held constant instead of growing monotonically
   with every measurement. *(Requires explicit confirmation before deleting anything pre-existing — see
   § 7.)*
4. **Record the actual diff on mismatch**: first differing offset, expected vs observed ±20 chars. Every
   failure on record is a bare boolean, so autocorrect substitution cannot currently be told apart from
   dropped characters — two different bugs with two different fixes.

---

## 5. The experimental design

### 5.1 Factors

- **Chunk size:** {80, 120, 160, 200}. **200 is the positive control** — it must fail, and if it ever
  stops failing the instrument is lying again. Include it in *every* block; the old data has zero
  late-session 200 observations.
- **Session position block:** early (runs 1-5), mid (11-15), late (21-25).
- **Body length:** fixed at **≥ 2,400 characters**. At 1,200 every size passed including 250 — a short
  body does not discriminate and will wrongly clear a dangerous value.

### 5.2 Order and power

Randomize size **within** each block. Do not alternate — alternation aliases size with run parity.
5 repeats per size per block = 60 runs, which gives roughly 80% power for a 20-point difference in
failure rate. The old 2-run cells gave about 15%, which is why they proved nothing.

### 5.3 The reset arm (this is the arm that answers the user's actual question)

Run the whole design twice: once with **no Mail restart** for the entire session, once with a **Mail
restart before every run**. Then:

- restart-arm flat **and** no-restart-arm climbing with position → degradation is real and separable
- both arms identical → it is chunk size, and the restart folklore is noise

Add a third, cheaper arm once § 6 lands: **no restart, but close all compose windows between runs**. If
that arm looks like the restart arm, leaked windows are the accumulator and *no restart is ever needed* —
which is the outcome the user wants.

### 5.4 Covariates, before and after every run

`state_probe.py` (scratchpad) already emits these: `mail_rtt_s`, `ax_rtt_s`, `mail_rss_kb`,
`mail_webcontent_rss_kb`, `applespell_rss_kb`, `windowserver_rss_kb`, `drafts_total`,
`outgoing_messages`, `ax_windows`, Mail process elapsed, `loadavg`. Regress elapsed **and** failure on
size *and* covariates jointly. The question then stops being "which story is true" and becomes "which
term carries the coefficient."

### 5.5 Hold constant and log

Source message id, body hash, `include_signature`, inter-run sleep. **No concurrent CPU work** — no
pytest, no builds, no artifact rebuilds. The one batch in the old sweep that had concurrent pytest is
the one that failed.

### 5.6 Abort conditions

Abort the block and record why if `mail_rtt_s` exceeds 1.0 s or `ax_rtt_s` exceeds 2.0 s before a run.
**A run started against a degraded Mail measures the degradation, not the factor under test.** This is
how the superseded "34.2 s fixed overhead" figure was produced.

---

## 6. Code defects found (candidates for fixes, tracked in Linear)

Found by static reading of the full reply path; none fixed yet.

1. **`DRAFT_LIST_CAP = 75` vs a 1,045-draft mailbox** — silently disables identity resolution, the
   retry path, and honest verification on any real mailbox. The comment's premise ("drafts mailboxes are
   usually small") is false here by 14x.
2. **The 20 × 75 verification poll** — turns every non-instant verification into ≥ 20 s and, past
   ~2 s/attempt, into a false `REPLY_DRAFT_VERIFICATION_TIMEOUT` on a *correctly saved* draft.
3. **Compose windows leak on both failure paths** — SIGKILL never runs `on error`; the `on error`
   handler lacks the guard path's `close … saving no` fallback; the success-path close is silently
   conditional and its return value is discarded.
4. **`MAX_OPEN_COMPOSE_WINDOWS` is inoperative** — gated behind `mode == "open"` so it never fires for
   `mode="draft"`, fails open on any error, and counts via `outgoing messages`, which reports 0 on
   Darwin 25.5 even with windows open.
5. **The abort probe pays a full 20-attempt Drafts scan** even when nothing was ever saved.
6. **The timeout projection models typing only** (§ 3.4).
7. **Empty-subject edge case** — if `reply_subject` is empty the subject filter disables itself and all
   75 drafts get full body verification, 20 times.

---

## 7. Optimization candidates — mechanism, not tuning

Chunk size is a mitigation for a problem that may be avoidable outright. These are ranked by expected
value and none has been tested yet.

### 7.1 The inter-chunk delay sits ~50 ms on the wrong side of WebKit's correction timer

WebKit's `Source/WebCore/editing/AlternativeTextController.cpp` defines:

```cpp
const Seconds correctionPanelTimerInterval { 300_ms };
```

The correction panel **arms** when the caret rests at the end of a marked word and **fires 300 ms
later**; moving the selection before it fires cancels it (`stopPendingCorrection()`).

`TYPING_INTER_CHUNK_DELAY` is **0.35 s**. Every chunk boundary pauses *just past the arming threshold* —
which is precisely why a per-chunk Escape had to be invented. The current design pays a correction panel
on every single chunk, then races to dismiss it.

**Dropping the delay below 300 ms (e.g. 0.25) may stop the panel arming at all.** That would remove the
corruption class rather than mitigate it, delete the Escape race, make chunk size far less
safety-critical, *and* make every run faster. This is the highest-leverage single change identified in
this whole investigation. It must be tested at ≥ 2,400 characters with the § 4 instrument fixes in place,
and 200 must be re-run as the positive control — if the panel truly stops arming, 200 should stop failing.

Compounding detail: after WebKit r232530 the spell-checking range expands to the whole **sentence** on
each typed word, so per-chunk cost grows with body length *within* a run — a plausible mechanism for why
corruption is length-dependent at all.

### 7.2 App-scoped autocorrect default (allowed — not a system setting)

```bash
defaults write com.apple.mail WebAutomaticSpellingCorrectionEnabled -bool false
```

App-domain preference, reversible with `defaults delete`, verified currently unset in both domains.
**This is not the global System Settings toggle**, which remains out of bounds for this project.
Supporting signal: Mail's own Edit → Spelling → Correct Spelling Automatically does *not* persist across
messages, which suggests Mail isn't writing its own value — the condition under which an app-domain
default takes effect. Test, don't assume; the `NSAutomaticSpellingCorrectionEnabled` variant may be read
from `NSGlobalDomain` only.

### 7.3 Accessibility — the log storm is cosmetic, but the *latch* is real

**I initially read the 5,920 `WebProcessPool::initializeAccessibility` entries as a notification storm.
That reading is wrong** and is retracted here before anyone acts on it. WebKit source (PR #47761,
`297202@main`, Jul 2025, fixing bug 295537) puts the `RELEASE_LOG(Process, ...)` **above** the
`if (m_hasReceivedAXRequestInUIProcess) return;` guard. So the line is emitted on every call while the
real initialization work runs **exactly once**. 5,920 lines ≈ 5,920 `os_log` calls, which is negligible.

The per-process Darwin-notification loop I suspected is real but lives inside `#if PLATFORM(IOS_FAMILY)`
— **refuted for macOS**. No upstream bug exists for repeated `initializeAccessibility` logging. And my
own decay curve (4340 → 1084 → 384 → 112/min) argues *against* a feedback loop, which would be flat or
growing; it is an AX client doing one large tree walk then tapering.

**What survives, and it is the better hypothesis.** Two documented, durable effects:

1. **The latch is sticky for Mail's entire lifetime.** Once any AX request arrives,
   `m_hasReceivedAXRequestInUIProcess` is set, and `platformInitializeWebProcess` then stamps
   `shouldInitializeAccessibility = true` on **every WebContent process spawned from that point on**.
   AX stays on until Mail quits. **This is a real mechanism for "a freshly restarted Mail behaves
   differently", and quitting Mail is the only thing that clears it** — precisely the remedy the user
   reports and precisely the one he does not want to need.
2. **AX activity blocks WebContent process suspension.** WebKit PR #43748 exists solely to fix a test
   named `AccessibilityChildrenPreventsProcessSuspensionOnFrontmostTab` — accessing AX children is
   *designed* to keep processes unsuspendable. That matches the observed `markLayersVolatile` retries
   (20 ms doubling to a 2 s ceiling, ~7-8 attempts ≈ 2.5 s per suspension attempt): processes repeatedly
   try to suspend and repeatedly fail to quiesce. Mail's `WebContent.EnhancedSecurity` count grew 3 → 4
   over 19 minutes, so the population of unsuspendable renderers can accumulate within a session.

**The actionable consequence — and it does not require restarting Mail.** The trigger is
`WebViewImpl::accessibilityAttributeValue`, fired by *any* AX attribute read except `Parent` and
`Position`. Our own automation is the client generating that traffic, and
[`typing_scripts.py:73`](../../../plugin/apple_mail_mcp/tools/compose/typing_scripts.py) walks
**`entire contents of targetWindow`** — a full recursive AX tree walk, plausibly a large fraction of
those thousands of attribute reads. **Replacing that walk with a targeted element query is a real
optimization** that cuts AX traffic, reduces suspension blocking, and is entirely within our control.
Rank it directly behind § 7.1.

Log count remains a valid *proxy for AX traffic volume* — just not for initialization work. Keep the
measurement, relabel what it means:

```bash
/usr/bin/log show --last 5m --style ndjson \
  --predicate 'process == "Mail" AND eventMessage CONTAINS "initializeAccessibility"' | wc -l
```

Track it per run against run index, and track it **before and after** the `entire contents` change — a
large drop is the confirmation that our automation was the traffic source.

### 7.4 Measurement contention — fix before trusting any number

**36 apple-mail MCP client processes are live** across the Codex, Claude, and Cursor hosts, one of them
on a stale 3.11.8 build. Mail's Apple Event dispatch is single-threaded, so any sibling scan blocks every
other Apple Event. Worse: **raw `subprocess` calls to `osascript` bypass the plugin's cross-process
`flock` entirely** — the lock only wraps `run_applescript`. The scratchpad probe and every ad-hoc
measurement in this session did exactly that.

Mail was directly observed at 99.3% CPU with its whole main thread inside
`MFLibraryIMAPStore copyOfAllMessagesWithOptions: → sqlite3_step → pread`, driven by a sibling agent's
raw `osascript`. **Any measurement taken during that is meaningless.** Two consequences: the benchmark
harness must take the same `flock`, and § 5.5 must be enforced, not merely written down.

That sample also proves `messages 1 thru N` **materializes the whole mailbox** through SQLite rather than
binding a slice — consistent with § 3.2's depth-dependent cost.

### 7.5 Baseline readings that were wrong, and the right instruments

Three numbers in my own § 3 baseline were misread. Recorded so they are not repeated:

| I reported | Reality | Why |
|---|---|---|
| AppleSpell ~218 MB | **35-37 MB** (peak 38) | `ps` RSS counts shared library `__TEXT` (579 MB, shared) and mapped files. **Use `footprint`, never `ps` RSS.** |
| 7 WebContent procs, largest 246/359 MB | **Not Mail's.** Mail uses the `WebContent.EnhancedSecurity` variant: 3 at launch → 4 at 19 min, 46-77 MB each | Wrong process-name match |
| AppleSpell "did not restart with Mail" | True but irrelevant — it idle-exits and respawns on demand (`runs = 4` this boot, prior instance exited after 17.3 h) | — |

**AppleSpell is refuted as the accumulator**: footprint flat at 35→37 MB over 28 min, RSS exactly static
while Mail sat at 90% CPU, and **zero** spell/correction/`AlternativeText` frames in two independent
`sample` runs of Mail. Public "AppleSpell leak" reports are uninstrumented forum anecdote, none
Tahoe-era.

**Synthesized-keystroke rate limiting: no evidence.** Apple documents no limit, queue depth, or
backpressure for `CGEventPost`. Two Tahoe-specific items stay on the checklist though:
`CGXSenderCanSynthesizeEvents()` silently drops some synthesized events on macOS 26.5, and Secure Event
Input blocks injection entirely (`ioreg -l -w 0 | grep SecureInput` — no output on this host). Neither is
reported to cause *partial* corruption; their failures are total.

## 8. What has NOT been done

- **No new live native-reply run** since the finding. Deliberate: § 4 first.
- **No drafts deleted.** 1,045 drafts sit on the test account, an unknown number of them test residue
  from prior sessions. Cleanup needs explicit confirmation and must not touch the user's real saved
  compose content.
- **Recovery question, partially answered:** in the one observed instance, Mail returned to fresh-baseline
  responsiveness **on its own within ~1 minute, with no restart** (§ 3.1). That instance was load-induced,
  so it says nothing yet about whether a *run-accumulated* degradation would also self-clear. The § 5.3
  reset arm is what answers that.
- **Instrumentation lesson:** the probe's first `osascript_procs` field used `pgrep -f`, which matched
  every node/shell/agent process whose argv merely contained the string, and reported 52 phantom stray
  interpreters. Fixed to `pgrep -x`. **A covariate that is wrong is worse than one that is missing** —
  it would have manufactured a leaked-process finding out of nothing.

---

## 8. Immediate consequence for the shipped constant

`TYPING_CHUNK_SIZE` currently ships **160**, chosen from the four contaminated measurements. The only
values with clean, uniquely-attributed evidence at 2,400 characters are **80** (3/3) and **120** (4/4).
120 is the fastest of those and costs ~2.4 s against 160's unverified figure. The constant and its
comment block have been corrected accordingly; see [`CHANGELOG.md`](../../../CHANGELOG.md).
