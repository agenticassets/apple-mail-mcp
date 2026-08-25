# Native reply: live timing, the frontmost requirement, and the timeout cliff

**Date:** 2026-08-24 · **Host:** Darwin 25.5 · **Account:** iCloud (personal mailbox, draft-only)
**Method:** working-tree package via `.venv/bin/python`, never MCP tools (those run the *installed*
plugin and answer cleanly from stale code). Every reply below was `mode="draft"`; nothing was sent.

All numbers are wall-clock from a real mailbox on one host and one session. They are a calibration
point, not a portable constant.

## 1. Where the time goes

Five live draft replies. **Fit the model on the successful ones only** — see the warning below.

| Run | body chars | chunks | elapsed | result |
|-----|-----------:|-------:|--------:|--------|
| A | 51 | 1 | 33.4 s | success (Finder held the front; tool reclaimed it) |
| B | 57 | 1 | 37.0 s | success |
| C | 1662 | 21 | 47.6 s | success |
| D | 3060 | 39 | 62.1 s | success |
| — | 1599 | 20 | 80.0 s | `REPLY_BODY_MISMATCH` — **excluded from the fit** |

```
least squares over A-D:  0.70 s per chunk   34.2 s fixed overhead   R2 = 0.98
```

**Measure only successful runs.** A reply that fails verification also burns a 20-attempt fallback
poll with a 1 s delay per attempt, which the success path never runs. That is the entire difference
between run C (21 chunks, 47.6 s) and the excluded run (20 chunks, 80.0 s). An earlier version of
this note solved a line through the failed run, charged its ~32 s of polling to typing, and reported
2.26 s per chunk — three times the truth. Everything that followed from that number was wrong; §3
records what it claimed and why it was not real.

**Fixed overhead dominates everything an agent will realistically send.** A one-chunk reply spends
~98% of its time not typing, and even a 39-chunk reply spends 55% of it. This is the lag visible in
the UI: the compose window appears, then nothing happens for roughly half a minute before the first
character lands.

Only **4.0 s of that 34.2 s is literal `delay` statements** on the success path
(1.2 + 0.4 + 0.3 + 0.6 + 0.1 + 0.4 + 1.0). The remaining **~30.8 s is real Mail / Accessibility /
Apple Event work**, so tuning sleeps cannot recover it. Structural cost centres, in rough order of
suspicion:

- `resolveReplyBodyEditor` walks `entire contents` of the compose window and issues one Apple Event
  per element for `AXRole` (`typing_scripts.py:73-84`). The early exit fires only on `AXTextArea`;
  an `AXWebArea` is recorded as a fallback **without** exiting, so a WebKit compose window with no
  `AXTextArea` walks 100% of its subtree. It runs once per guard attempt, up to 4.
- Drafts is fully enumerated **2-4 times** per reply (once pre-save, once per resolver attempt), and
  each pass reads **two** properties per draft — `id` *and* `message id`, the expensive header read.
- `fullDraftRfcSnapshot` returns `missing value` when Drafts holds more than `DRAFT_LIST_CAP` (75)
  rows. It bails rather than truncating, so a 76-draft mailbox gets no identity at all and drops
  into the 20-attempt fallback scan. That is a cliff, not a gradient.
- The 20-attempt verifier poll does **not** run on the success path; it engages only when the Drafts
  resolver failed, or on an abort probe.

**Not yet measured:** the per-element AX cost and the actual element count. That single unknown is
the difference between "the AX walk is the whole problem" and "the AX walk is 2 s". Instrumenting
the compose script with `(current date)` deltas resolves it in one reply.

## 2. Mail must be frontmost — now enforced by the tool

System Events delivers keystrokes to whatever application is frontmost. Typing a reply while Mail
sits in the background does not fail loudly; it types the body into whoever *is* in front.

The old code called `activate` once and slept 0.4 s. `activate` is a request the window server may
defer, and it defers routinely for a background `osascript` — which is exactly how this runs under
an MCP host. The result was a reply that failed in the focus guard and reported
`REPLY_WINDOW_FOCUS_FAILED`, sending the caller to grant Accessibility permissions for a problem no
permission can fix.

**Fix:** `ensureMailFrontmost()` (`reply_window_scripts.py`) activates Mail and then *polls*
`frontmost of process "Mail"` up to 5 times, naming the app that held the front if it never
succeeds. It is called twice: once **before** the `reply` command, so the compose window opens into
an already-frontmost app, and again on **every guard attempt**, because raising a window inside Mail
does not make Mail the frontmost application and anything can take the front back in between.

A front that cannot be claimed now reports `REPLY_MAIL_NOT_FRONTMOST` rather than a focus or
adoption failure, and its remediation explicitly says not to switch off `native_format` and not to
grant more permissions.

**Live verification:** with Finder deliberately activated immediately before the call —
`front app before call: Finder` — the tool reclaimed the front (`front app after call: Mail`) and
the reply succeeded in 33.4 s with a persisted draft and no error code. This is the exact scenario
that failed before the change.

A locked screen, an active screen saver, or a full-screen Space that excludes Mail all still block
it. That is a foreground-attention limit of GUI scripting, not a defect, and an agent should report
it rather than retrying.

## 3. The timeout projection is sound — a retracted finding, kept as a warning

An earlier revision of this note reported a timeout cliff at ~3,040 characters and called it the
lane's top defect. **It was not real.** It came from solving a two-point line through the failed
1599-char run, whose 80.0 s included ~32 s of fallback polling that only runs when verification
fails. That produced 2.26 s per chunk where the truth is 0.70 s.

`reply_runner.py` projects `chunks x (0.35 + 0.65) = chunks x 1.0 s` and grants
`max(120, projected + 20 + 30)`. Against the corrected model (`0.70c + 34.2`):

| chunks | body chars | timeout granted | real | margin |
|-------:|-----------:|----------------:|-----:|-------:|
| 1 | 80 | 120 s | 34.9 s | +85.1 s |
| 21 | 1,680 | 120 s | 48.9 s | +71.1 s |
| 39 | 3,120 | 120 s | 61.4 s | +58.6 s |
| 100 | 8,000 | 150 s | 104.0 s | +46.0 s |
| 480 | 38,400 | 530 s | 369.4 s | +160.6 s |

The margin is positive at every length and widens with the body, because the model over-projects
per-chunk cost by ~43% — the safe direction. **Live-confirmed:** a 3,060-char / 39-chunk reply, the
exact length the retracted finding called fatal, completed in 62.1 s using 41% of its budget.

One real inaccuracy was found and fixed: `_NATIVE_TYPING_FIXED_OVERHEAD_SECONDS` read `20` against a
measured 34.2 s. The 30 s slack absorbed the gap, so nothing failed, but a constant named for the
fixed overhead should hold the fixed overhead rather than lean on the cushion. It is now `35`.

**The lesson is the measurement discipline, not the constant.** Timing a GUI automation path from a
run that failed charges the failure handler's cost to the happy path. Fit only successful runs, and
prefer three points to two — two points cannot show you that they disagree.

## 4. `TYPING_CHUNK_SIZE`: 200 and 250 are not safe — measured, not argued

> **⚠️ SUPERSEDED 2026-08-25 — read
> [`session-degradation-test-plan-2026-08-25.md`](session-degradation-test-plan-2026-08-25.md) instead.**
>
> The passes recorded in this section were verified against drafts left by *previous runs*, not by the
> run being measured; every result row carried `exact_id_verified: false`. **The "cliff between 160 and
> 200" claimed below is withdrawn**, and the "Decision: shipped at 160" subsection at the end of this
> section is void — the shipped value is **120**.
>
> What survives: the **200/250 failures are real** (contamination can only fake passes, never failures),
> and 80 and 120 have clean, uniquely-attributed passes. Everything else here should be read as a record
> of what was believed on 2026-08-24, not as evidence.

An earlier revision of this section recommended keeping 80 on a *theoretical* margin argument
against a documented 320-480 character truncation floor. That reasoning was never tested, and the
number it leaned on turned out not to be the binding constraint. The recommendation survives; the
evidence for it is now direct, and the real failure boundary is **far below** 320.

### The sweep

Run after the correction-panel fix in §6, so Mail was responsive on every run (`mail_rtt_before_s`
0.10-0.13 throughout). Body length held fixed; **chunk size was the only variable**. Same source
message, same account, draft mode, nothing sent.

At **2,400 characters**:

| `TYPING_CHUNK_SIZE` | chunks | runs | verified | wall clock |
|---------------------|--------|------|----------|------------|
| 80 | 30 | 2 | **2 / 2** | 39.0, 39.1 s |
| 120 | 20 | 4 | **4 / 4** | 33.4, 33.8, 33.9, 34.3 s |
| 160 | 15 | 3 | **3 / 3** | 31.5, 31.5, 31.5 s |
| 200 | 12 | 2 | **0 / 2** — `REPLY_BODY_MISMATCH` | 69.6, 71.3 s |
| 250 | 10 | 2 | **0 / 2** — `REPLY_BODY_MISMATCH` | 68.6, 70.7 s |

At **1,200 characters** every size passed, including 200 and 250 (30.6 / 28.1 / 26.6 / 25.3 s for
80 / 120 / 200 / 250). **A short body does not discriminate.** Anyone re-testing this on a one- or
two-paragraph reply will conclude 250 is fine, and be wrong.

### Two things this settles

**Large chunks corrupt the body, and the boundary is a cliff.** Everything at or below 160 passed
9 of 9; everything at or above 200 failed 4 of 4. The mechanism follows from §6: the per-chunk
Escape *rejects* an autocorrect suggestion, so a bigger chunk types more text between rejections and
lets a substitution land before the rejection arrives. Chunk size is not a throughput dial; it is
how often the body gets protected.

**The failures are slower than the successes.** 69-71 s against 31-39 s, because a mismatch burns
the delete-and-retype path. So 200 and 250 are not a speed-versus-safety trade at all — they are
worse on both axes. That disposes of the question as asked.

### What is actually on the table

Against 80, the real upside is small and the cliff is close:

- **120** — 4/4 verified, saves 5.2 s of 39.0 (13%), two tested steps below the cliff.
- **160** — 3/3 verified and the most consistent times measured (31.5 s three times), saves 7.5 s
  (19%), but sits *directly adjacent* to the first failing size with no tested margin.

Both are safer than they look in one respect: a bad chunk size fails **loudly** as
`REPLY_BODY_MISMATCH`, not as a quietly wrong email. The cost of guessing high is a 70 s failed run,
not a corrupted message sent to a recipient.

Neither is a pure speed knob. `chunk_count` gates `REPLY_BODY_TYPING_BUDGET_EXCEEDED`, so the
accepted-body ceiling moves with it: ~38,400 characters at 80, ~57,600 at 120, ~76,800 at 160. That
ceiling is a deliberate limit on how long the native path may own the foreground, and it should be
moved on purpose, not inherited from a throughput tweak.

### Decision: shipped at 160, fall back to 120

**160 ships** (Cayman, 2026-08-25). It was the fastest verified size and the most consistent
measured — 31.5 s on all three runs — and saves 7.5 s of 39.0 against the old 80.

**If native replies start failing `REPLY_BODY_MISMATCH`, set `TYPING_CHUNK_SIZE` to 120.** That is
the pre-tested fallback: verified 4/4 at ~33.9 s, one step further from the cliff, costing ~2.4 s.
No other constant is coupled to it for correctness. The fallback is called out at the top of the
constant's comment block in `compose/constants.py` rather than buried in this file, because whoever
hits the failure will be reading the code, not the notes.

The accepted risk is stated plainly: 160 sits directly adjacent to the first failing size, so it has
no *tested* margin and the cliff could move down on a loaded machine. That risk is bounded because
the failure is **loud** — the full-body verifier returns `REPLY_BODY_MISMATCH`; it is never a quietly
wrong email sent to a recipient. The cost of being wrong is one ~70 s failed run plus a retry.

Anything at or above 200 is refuted and must not be reinstated without a fresh ≥2,400-character sweep.

### Re-measuring this

Use a body of **at least 2,400 characters**; shorter bodies pass at every size and prove nothing.
Confirm `mail_rtt_before_s` is sub-second before each run — a run started against a wedged Mail
measures the wedge, which is how the superseded 34.2 s fixed-overhead figure was produced.

## 5. Open failures reproduced live

### `REPLY_BODY_MISMATCH` — root-caused: the comparison is too strict, not the typing

Reproduced at 1599 chars / 20 chunks while 57 chars / 1 chunk succeeded. **Length is not the causal
variable.** The repo's own July live record passed a 5,031-char / ~63-chunk body in 37.6 s and
failed a ~230-char body on a single accented character, which rules length out directly.

The error is not raised by the typing loop. It is raised by the saved-draft verifier's
**case-sensitive exact substring** test (`saved_draft_checks.py:365-366` via `caseSensitiveOffset`),
whose normalizer `flattenForCompare` (`saved_draft_checks.py:200-226`) tolerates only four things:
whitespace, seven smart-punctuation code points, hyphen runs, and sentence-start capitalization.

The structural gap is an **ordering bug inside the normalizer**: it strips line breaks at line 210,
*before* calling `foldSentenceStarts` at line 224, and that handler splits only on `.`, `!`, `?`. By
the time it runs, the paragraph boundaries it would need are already gone. So the first letter of
any paragraph whose preceding line ended in something other than terminal punctuation — a greeting
ending in a comma, a colon-introduced list, a line ending in a name or URL — is left unfolded on
both sides. macOS "Capitalize words automatically" capitalizes it in the draft; the source string
did not. One character differs, offset is 0, and the verdict is `BODY_MISSING`.

Confirmed by porting the five AppleScript handlers to Python line-for-line and running constructed
pairs: single-line body → found; every-paragraph-ends-in-a-period → found; smart punctuation → found;
soft wrap → found; **comma-terminated greeting → missing**; **colon-terminated line → missing**;
one accented character → missing; one autocorrect substitution → missing.

A 57-char single-line body has zero paragraph starts and is **structurally immune**. A
multi-paragraph reply is exposed once per non-terminal-punctuation paragraph break.

**Two facts about severity.** The draft is `save`d inside the compose script *before* Python ever
verifies, so `REPLY_BODY_MISMATCH` is fail-closed against *sending* but still leaves a real Drafts
artifact behind. And for `mode="send"` the native path returns before verification runs at all, so a
sent reply's typed body is never checked.

**Fix:** fold paragraph starts symmetrically — run `foldSentenceStarts` *before* the whitespace
strip and add `return`/`linefeed` to its delimiter set, with `foldFirstChar` skipping leading spaces
so an indented paragraph still folds its first letter. This widens tolerance by exactly one
character per line start; the ALL-CAPS shift-leak that `foldSentenceStarts` exists to catch still
fails on characters 2..n. Second, a pre-flight typeability check in `reply.py` would turn the
remaining genuine corruptions (accented characters, autocorrect) from an 80-second failure plus a
stray artifact into a cheap refusal before any window opens.

### Other

- **`REPLY_QUOTED_ORIGINAL_MISSING` on a body-less source.** Reproduced twice. The source had
  `source_body_chars=0` while the draft's quote was verifiably present. The two-part quote proof
  needs a span of the *source* body, which cannot exist for a body-less message, so this should
  return the documented `QUOTE_PROOF_UNAVAILABLE` sentinel instead. Confirmed by contrast: the same
  call against a source with a 3,798-character body succeeded cleanly.

## 6. macOS autocorrect wedges Mail: `NSCorrectionPanel` starves Apple Events

**This is the root cause of the "Mail went unresponsive" class, and it is proven by a stack, not
inferred.** An earlier revision of this section blamed a long keystroke burst driving synchronous
WebKit IPC and labelled it "strongly indicated but not proven." That attribution was wrong and is
superseded. The WebKit frames are real, but they are the *caller*, not the cause.

### The stack

`sample` of a wedged Mail put **all 2,293 main-thread samples** in one stack, confirmed identical on
a second sample taken minutes later:

```
IPC::Connection::dispatchMessage
  AuxiliaryProcessProxy::sendMessage
    Messages::WebPage::GetSelectedRangeAsync reply handler
      WebKit::WebViewImpl::showInlinePredictionsForCandidates
        -[NSSpellChecker showCompletionForCandidate:…]
          -[NSSpellChecker _showInlinePredictionForReplacingRange:…]
            -[NSSpellChecker showCorrectionIndicatorOfType:range:primaryString:…]
              -[NSCorrectionPanel showPanelAtRect:inView:primaryString:…]
                -[NSCorrectionPanel _interceptEvents]     <-- nested modal event loop
                  -[NSApplication nextEventMatchingMask:untilDate:inMode:dequeue:]
                    __CFRunLoopRun  (nested)
```

macOS **autocorrect / inline predictions** react to the synthesized keystrokes and open a correction
panel. `_interceptEvents` then spins a **nested modal event loop** that pumps UI events but **does
not dispatch Apple Events**. Mail is not hung, not busy, and not out of permissions — it is sitting
inside AppKit waiting for someone to accept or reject a spelling suggestion that no human is
watching for.

That single fact explains every observation that previously looked contradictory:

| Observation | Explanation |
|-------------|-------------|
| Accessibility answered in 0.2-0.9 s throughout | AX is serviced off the main runloop; only Apple Events are starved |
| Process alive, `S`, ~0-2% CPU, still writing IMAP entries to the unified log | Background threads are unaffected; only main-thread Apple Event dispatch stops |
| AX reported `sheets = 0` | A correction panel is an `NSPanel`, **not** an `AXSheet`. It does not appear in the AX window list at all — see the caution below |
| An earlier occurrence "recovered on its own" | Something dismissed the panel (a stray click or keypress). Nothing healed; the modal was closed |
| This occurrence never recovered | Nothing dismissed it — 29 probes over 10 minutes, `-1712` on every one |

### Reproduction and threshold

Reliable, and it does **not** need a loaded machine or several open windows:

- Freshly restarted Mail, **one** window, `mail_rtt_before_s: 0.1`.
- One native reply, **1,200 characters / 15 chunks** at `TYPING_CHUNK_SIZE = 80`.
- Timed out at **120.2 s**; Mail then refused every Apple Event for the full 10-minute watch.

Runs that succeeded earlier the same day were **1 chunk** and **5 chunks**. Runs that wedged were
**15** and **20**. The threshold on this machine sits between 5 and 15 chunks — consistent with
"enough typed words for autocorrect to find a candidate," not with any per-keystroke cost.

### Recovery: dismiss the panel

Posting an Escape releases the nested loop. Measured, back to back on the wedged process:

```
tell application "System Events" to tell process "Mail" to key code 53   ->  no effect, still -1712
tell application "System Events" to set frontmost of process "Mail" to true
tell application "System Events" to key code 53                          ->  Mail answers in 0.1 s
```

**Which of the two differences mattered is not yet isolated** — the second attempt both dropped the
`tell process` scoping *and* forced frontmost first. The likely reason is that a `tell process`-scoped
key event is delivered through the accessibility path to the process, while an unscoped one is posted
to the session and lands in the frontmost app's event queue, which is the queue `_interceptEvents` is
draining. Treat the working form as: **set frontmost via AX, then post an unscoped `key code 53`.**

### Ties directly to the `REPLY_BODY_MISMATCH` finding in §5

§5 already blamed automatic text substitution for corrupting typed reply bodies. This is the same
subsystem — `NSSpellChecker` — producing a second and worse failure mode. Autocorrect on this path
both **rewrites the body** and **can wedge the application**. Any mitigation should be evaluated
against both.

### Countermeasures this argues for

1. **Dismiss the correction panel as part of typing.** Interleave an Escape between chunks so a
   panel is torn down before the next `tell application "Mail"` blocks on it. Escape *rejects* the
   suggestion, which is also the correct answer for §5's body corruption. **Shipped and live-verified:**
   `dismissTextSuggestionPanel()` in `typing_scripts.py`, called after the settle delay on every
   chunk including the last. The case that previously timed out at 120.2 s completes in 30.9 s.

   Two details are load-bearing and were each established by a measurement the code does not explain,
   so both are pinned in `tests/compose/test_reply_typing_correction_panel.py`:

   - **The key event is posted unscoped.** `tell process "Mail" to key code 53` did *not* release the
     loop; an unscoped `tell application "System Events" to key code 53` did. The nested loop drains
     the frontmost application's event queue, which a process-scoped accessibility-path event does
     not enter. A refactor tucking this inside the neighbouring `tell process "Mail"` block would
     look tidier and silently restore the hang.
   - **It is deliberately unguarded.** An earlier revision wrapped it in a bare `try` on the reasoning
     that a dismissal failure should not abandon a half-typed body. That was wrong twice:
     `keystroke chunkText` is itself unguarded one line above, so the same System Events failure is
     already fatal there; and the swallow buys nothing where it counts, because with a panel up the
     next statement is the `tell application "Mail"` that wedges — there is no next chunk to retry on.
     `tests/core/test_no_bare_applescript_try.py` caught the regression as a ratchet count.
2. **Wrap compose-path Mail tells in `with timeout of N seconds`.** Verified separately: `with
   timeout` does bound the wait and yields `-1712 AppleEvent timed out`. Our compose scripts do not
   use it, so a wedged Mail becomes a context-free subprocess SIGKILL with no script state at all.
   `-1712` says *Mail did not answer*; an `AppleScriptTimeout` only says *something took too long*.
3. **Do not silently change the user's autocorrect settings.** Turning off
   `NSAutomaticSpellingCorrectionEnabled` or Mail's "Check spelling while typing" would fix this,
   but it is a global user setting and is not the tool's to change. If we ever surface it, it is
   advice in a remediation string, not a write.
4. **An Apple Event timeout on this path is not a code defect and not a retry signal.** Re-driving
   the reply opens another compose window while the first modal is still up. The correct response is
   to stop issuing Mail calls, dismiss the panel, then retry once.
5. **Enforce the compose-window cap on the draft path.** Not causal here — this reproduced with one
   window — but still a real gap: `MAX_OPEN_COMPOSE_WINDOWS = 5` is gated behind `mode == "open"`
   (`reply.py:289`) so it never fires for `mode="draft"`, and its counter
   `_count_open_outgoing_messages` uses `count of outgoing messages`, which Tahoe under-reports
   (measured 2 against 13 real windows).

### Caution: AX is not a modal detector

`sheets = 0` and a clean AX window list were both true *while Mail was wedged behind a modal panel*.
A correction panel is invisible to both checks. **Do not** write a guard that concludes "no modal is
up" from AX. The only reliable in-band signal is the one the tool already gets: a Mail Apple Event
that does not return.

### Note on window counts

Mail's scripting layer listed **13** windows during one outage; Accessibility listed **5**. Four of
Mail's entries had `id = -1` and one had an empty name — panels, not message windows. The adoption
logic snapshots ids before `reply` and requires exactly one *new* subject-matching window, so
duplicate `-1` ids are excluded rather than adopted; that is the safe direction and needs no change.

## 7. Live testing is the norm for native reply

Standing instruction from Cayman (2026-08-24): always live-test the native reply path when the
machine allows it. The path is GUI-driven and takes over the screen, so it needs an unlocked
display with Mail able to reach the front.

When the screen is locked or off, activation legitimately fails. **That is not a defect and must not
be reported as one** — record that live verification was unavailable and say so in the handoff.
