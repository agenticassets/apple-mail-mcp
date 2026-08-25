# Native reply: live results, 2026-08-25

**Status:** live testing complete and stopped at the operator's instruction. Three root causes
fixed in the working tree, all confirmed by live runs. Numbers below are
measured, not projected. Read [`session-degradation-test-plan-2026-08-25.md`](session-degradation-test-plan-2026-08-25.md)
first for why the previous sweep was void; this file is what replaced it with real data.

**Instrument:** every run embeds a unique `RUN-<uuid>` token in the body, so no earlier draft can
satisfy a body-contains match. The verdict is decided by an **independent re-read of the reported
draft**, not by the tool's own self-report: `PASS` requires the nonce to be present *and* the typed
region to equal the expected body byte-for-byte. That closes the contamination channel that voided
the 2026-08-24 sweep. Harness lives in the session scratchpad (`bench.py` / `run.py`); it drives the
**working-tree package**, never the installed plugin.

---

## 1. Two real defects found, both fixed

### 1.1 A single Accessibility sample aborted healthy replies, and blamed the display

The preflight took **one** sample of `count of windows` for Mail and aborted on zero, with
remediation telling the user their screen was asleep or their Accessibility grant had lapsed.

Measured: with a full-screen app holding the front, `frontmost of process "Mail"` reads **true from
the first sample** while Accessibility reports **0 windows for ~0.3 s** during the Space transition,
then 1 steadily. Mail's own scripting dictionary reported **8 windows the entire time**.

| sample | t | frontmost | AX windows | Mail dict windows |
|---|---|---|---|---|
| 1 | +0.0 s | true | **0** | 8 |
| 2 | +0.0 s | true | **0** | 8 |
| 3 | +0.3 s | true | 1 | 8 |
| 4-25 | to +5 s | true | 1 | 8 |

One run in this session (chunk 200) aborted at **0.79 s** inside exactly that gap.

**This is very likely the "it degrades over time" symptom.** Mail keeps its windows on the Space it
was launched into. Any full-screen app puts the user on a different Space, and Accessibility
enumerates nothing for an app parked on another one. A freshly relaunched Mail opens onto the Space
you are on, which is why restarting Mail "fixes" it — and why **no restart is actually needed**.
While measuring, the front app was observed to be a full-screen app with `AXFullScreen = true`, and
Mail read AX 0 / dict 8 continuously for 40 s.

**Fix:** `accessibilityWindowCountSettled()` — the zero has to hold across
`AX_WINDOW_SETTLE_ATTEMPTS` samples before it aborts. The healthy path pays one AX read. The abort
detail now carries **Mail's own window count beside the Accessibility one**, which is the whole
diagnosis: "Mail has no window" and "Accessibility cannot see Mail's windows" need different fixes.
Remediation rewritten to lead with the Space case and to stop recommending a Mail restart.

### 1.2 The typed body was truncated because nothing waited for the editor

Every pre-fix failure was **pure truncation, never substitution**: a clean prefix, then Mail's
quote. Autocorrect was not involved. Every cut also landed **mid-chunk**, not on a chunk boundary,
and ended in a non-breaking space where typing stopped.

`keystroke` returns when events are **posted**, not when WebKit has processed them, and the loop
deliberately skips its inter-chunk delay after the *final* chunk — so the script went from the last
`keystroke` straight to `save`. Whatever had not drained was absent from the draft.

**Fix, part one:** `waitForTypedBodyToSettle()` polls the editor's own `AXValue` before returning.
Not a flat sleep and not fatal (the case-sensitive verification against the saved draft still
decides correctness).

### 1.3 The settle budget had to scale with body length, and a flat one was the rest of the defect

Part one shipped with a **flat** 24-attempt / 6 s budget, and that only moved the boundary. A
2,400-character body at chunk 300 drained inside 6 s; a 5,000-character body at the same chunk size
did not.

| run | chunk | body | budget | landed | lost | verdict |
|---|---:|---:|---:|---:|---:|---|
| F3 | 300 | 5,000 | 6 s | 3,179 | 1,821 | **FAIL** |
| G1 | 300 | 5,000 | 50 s | 5,000 | 0 | **PASS** |

Same configuration, same host, one variable. **The tail was late, not dropped** — which is why
waiting is the fix rather than retyping.

**Fix, part two:** the budget is computed from `bodyLength`, in AppleScript so a retype pass
re-scales, and mirrored in Python by `constants.typing_settle_attempts()` so
`reply_runner._native_reply_effective_timeout` can project a timeout that contains the drain. If
those two disagreed, `AppleScriptTimeout` would fire mid-drain and strand a partially typed compose
window — strictly worse than the truncation being fixed.

### 1.4 Chunk size was a proxy for backlog depth, not a safety dial

This retires the framing every previous sweep used, including the one in this file's own § 2.

Chunk 600 on a 2,400-character body failed three runs of four against the 6 s budget — and **passed**
against a 50 s budget, taking **72.6 s** where chunk 300 takes 22.9 s.

| run | chunk | body | budget | elapsed | verdict |
|---|---:|---:|---:|---:|---|
| D2, E1, E3 | 600 | 2,400 | 6 s | 63.8-68.9 s | **FAIL** ×3 |
| C4 | 600 | 2,400 | 6 s | 20.5 s | PASS |
| G2 | 600 | 2,400 | 50 s | 72.6 s | PASS |

Bigger chunks post keystroke events faster; the WebKit editor does not process them any faster. All
a bigger chunk buys is a deeper backlog to wait out, so once the drain is actually waited for, large
chunks are **slower**, not more dangerous. The apparent speed of large chunks in every earlier sweep
was the script saving before the editor had caught up.

**The operator's report on D2 ("I might have caused that one to mess up") was a false alarm.** E1
and E3 reproduced it twice, unattended, with a passing chunk-300 run in between. D2 was real.

---

## 2. Measured results

One fixed source message, `mode="draft"`, inter-chunk delay 0.35 s. Verdict is the independent
re-read described above. Load average 4.3-8.4 across every run; no run was taken on a degraded Mail.

### Before any fix — 2,400 chars

| run | chunk | elapsed | verdict | detail |
|---|---:|---:|---|---|
| A1 | 120 | 60.2 s | **FAIL** | truncated, 216 lost |
| A2 | 200 | 0.8 s | **ABORT** | Accessibility 0-window race (§ 1.1) |
| A3 | 160 | 54.6 s | **FAIL** | truncated, 433 lost |

### With the settle poll but a FLAT 6 s budget — 2,400 chars

| run | chunk | chunks | elapsed | verdict |
|---|---:|---:|---:|---|
| B1 | 120 | 20 | 28.9 s | PASS |
| B3 | 160 | 15 | 25.9 s | PASS |
| B2 | 200 | 12 | 25.3 s | PASS |
| C1 | 250 | 10 | 24.1 s | PASS |
| C2, D1, D3, E2 | 300 | 8 | 22.5-23.4 s | PASS ×4 |
| F1 | 400 | 6 | 22.2 s | PASS |
| F2 | 500 | 5 | 21.3 s | PASS |
| C4 | 600 | 4 | 20.5 s | PASS |
| D2, E1, E3 | 600 | 4 | 63.8-68.9 s | **FAIL** ×3 |

Longer bodies and signatures broke this budget too: F3 (300, 5,000 chars) and F4 (300, 2,400 chars,
signature on) both failed.

### With the shipped SCALED budget — the configurations that had failed

| run | chunk | body | signature | elapsed | verdict |
|---|---:|---:|---|---:|---|
| H1 | 300 | 2,400 | off | 65.7 s | PASS |
| H2 | 300 | 5,000 | off | 68.2 s | PASS |
| H3 | 300 | 2,400 | **on** | 33.5 s | PASS |
| H4 | 300 | 5,000 | **on** | 48.5 s | PASS |
| H5 | 300 | 2,400 | off | 69.2 s | `TYPING_INTERRUPTED` — see below |
| H6 | 600 | 2,400 | off | 26.5 s | PASS |

**Every configuration that failed under the flat budget passes under the scaled one**, including
both 5,000-character bodies and both signature-enabled bodies.

**H5 is not a defect and not a truncation.** It returned `TYPING_INTERRUPTED`: the per-chunk focus
guard detected that the compose window lost focus partway through typing, aborted, and discarded the
partial compose window. **No partial draft was saved and nothing was sent.** The operator was
actively using the machine during that run. This is the guard's designed behaviour and the safe
failure mode.

### Why `TYPING_CHUNK_SIZE` ships at 300

400 and 500 are marginally faster and both passed cleanly, but each has a **single** observation
against 300's four, and the gain over 300 is about 1 s on a ~23 s operation. 600 is the cliff, and it
costs speed rather than safety. 300 is the fastest value whose evidence is deep enough to trust.

---

## 3. Resolved, and what is still open

### Resolved this session

- **D2 retested and reproduced** (E1, E3). Chunk 600 fails against a small drain budget; not
  confounded, and not a per-keystroke tail-drop floor — it is backlog.
- **The 5,000-character arm ran** (F3, G1, H2, H4). Length drives drain time, and the scaled budget
  covers it.
- **The signature path was tested** (H3, H4) after F4 failed on it. `include_signature` defaults to
  **`True`** on `reply_to_email`, `send_email`, and `forward_email`; the benchmark harness passed
  `False` deliberately so signature text could not shift the measured truncation offsets.
- **Chunk 200, the plan's designated "positive control that must fail", does not fail.** That control
  was never measuring corruption; it was measuring backlog depth at save time.

### Still open

- **Closed after live testing stopped — the settle poll now also exits on a length delta.** It had
  only one success condition, `editorText contains bodyTail`, and WebKit rewrites characters in the
  editor (a trailing space becomes a non-breaking space at minimum), so the literal compare missed on
  bodies that had in fact arrived intact and the poll spent its whole budget. Cost, on runs that all
  **passed**: H1 took 65.7 s where the same configuration took 22.9 s under the old flat budget,
  while H3/H4 — where the match evidently did fire — came in at 33.5 s and 48.5 s. The poll now also
  succeeds on the editor having **grown by `bodyLength` since a pre-typing baseline and stopped
  growing**; length survives the substitutions that defeat a text compare. **Not live-verified** —
  live testing was stopped before this landed.

### Found by adversarial review of the diff, after live testing stopped

None of these were observed in the runs above; all are static findings, and all are fixed in the
working tree **without live verification**.

- **The tail exit could fire against the quoted original.** `contains` is positionless and the editor
  is not empty when typing starts. A reply on a thread the operator had replied to before, ending in
  the same sign-off that appears in Mail's quote, would match on the first poll and save undrained —
  the exact truncation § 1.2 fixes, deterministic, and reproduced by the retype pass into a hard
  failure. The tail is now compared against the pre-typing editor first and disabled when already
  present. **A ~40-character signature block ending a reply on an ongoing thread is the ordinary
  case, not an exotic one**, so this would likely have shown up in normal use rather than in a
  benchmark whose bodies were unique nonces.
- **An unreadable editor produced a zero-length wait.** If Accessibility resolves an `AXWebArea`
  rather than an `AXTextArea`, `AXValue` throws, which disabled the delta baseline and ended the poll
  on attempt 1 — pre-fix behaviour, invisibly. The wait is now spent blind when it cannot be
  observed.
- **An explicit caller `timeout` bypassed the projection entirely,** so a value that was generous
  before this work (60 s on a 2,400-character body) now lands inside the drain and strands a typed,
  unsaved compose window. Floored at the projection.
- **Two timeout tests had stopped checking what they claimed.** Their yardstick was a pre-drain
  one-phase model, leaving ~245 s of fabricated margin at the cap boundary. The cap itself admits
  114,000 characters, not the 38,400 its comment claimed.
- **No repeats at 400 and 500** (one clean observation each), so neither is shippable yet.
- **The inter-chunk delay hypothesis is untested.** It now matters less: with the cause identified as
  drain rather than the correction panel, tuning the delay is a speed optimization, not a correctness
  fix.
- **Session degradation across runs was never separately measured**, and Mail was never restarted
  during any of this. § 1.1 explains the reported symptom completely with no accumulation.

## 4. Drafts

Each run leaves one benchmark draft, marked `RUN-<token> BENCHMARK DRAFT - SAFE TO DELETE`, replying
to a self-addressed notification so a stray draft can only ever go to the account owner. **Nothing
was deleted**; cleanup needs explicit confirmation and the newest drafts on this account are the
operator's real work. Drafts total went 1,042 → 1,070 over the session.
