# Forward queue after thread-member-completeness (2026-09-07)

Candidate work surfaced while fixing AGENTIC-2794. A menu, not a roadmap.
Verify each before acting; confidence is stated per item.

## Hardening

- **A "bound reported but not armed" gate** (confidence: verified gap)
  Every bound now has a marker channel, but nothing checks that a tool which
  *can* stop early actually emits one. A test that enumerates the bounded tools
  and asserts each has a reachable marker + a JSON field would have caught
  RC-2's never-arming ceiling flag directly, since that bug was a comparison
  against the wrong value with fully working plumbing behind it.

- **Assert live-verification actually ran against a reachable account**
  (confidence: verified gap) Three defects in this branch passed every mocked
  test and were caught only live: the `mailbox="All"` scan narrowing, the date
  floor making `thread_incomplete` useless, and the invisible `max_messages`
  truncation. There is currently no artifact recording which live checks ran on
  which commit. A `tasks/`-side live-acceptance stamp, or a CLI subcommand that
  emits one, would make that reviewable instead of narrative.

- **Fixture-strength check for bounded-scan tests** (confidence: verified gap)
  The first cut of `thread_fixtures.py` used mailbox streams shorter than the
  old 50-message bound, so every new test passed against the *buggy* code too.
  It was caught by hand. Any test claiming to pin a scan bound should be run
  against the pre-fix behavior once, or the fixture should assert its own
  stream length exceeds the bound under test.

## Simplification

- **`export.py`'s thread-id dedup loop** (confidence: verified, behavior-identical)
  `raw_thread_ids` / `seen_thread_ids` / `ordered_thread_ids` collapses to
  `list(dict.fromkeys(normalize_message_ids(raw_thread_ids)))` — five lines to
  one. Left untouched because it is unmodified context in this diff and the
  function is live-verified; easy win next time someone is in there.

- **`ThreadMarkers.bounded` has no source caller** (confidence: verified)
  Only `tests/search/test_thread_member_completeness.py` reads it, left over
  from when `thread_incomplete` was the union of both bounds. Either a caller
  was meant to branch on it or the test is pinning an unused API. Decide and
  remove one of them.

- **`ERROR_MAILBOX|||` is defined in three modules** (confidence: verified,
  pre-existing) `search/records.py`, `analytics/dashboard.py`, and
  `analytics/attachments_helpers.py` each carry a copy. `dashboard.py` predates
  this branch, so per-module wire constants are the established convention, not
  new drift. Centralizing is a repo-wide decision worth making deliberately.

- **`ThreadMarkers` vs `AttachmentScan` container style** (confidence: cosmetic)
  One is `__slots__` + hand-written `__init__`, the other a `@dataclass`. Worth
  settling the convention rather than letting the next container pick a third.

## Robustness

- **`thread.py` sizes its slice from `count of messages` first**
  (confidence: hypothesis, pre-existing shape) That property reads stale in
  both directions. `script.py` uses the sturdier pattern: slice optimistically
  at the cap, recover against the count only on error. Stale-high merely
  produces a conservative ceiling warning; stale-low silently under-scans
  without warning, which is the failure mode this branch exists to eliminate.

- **`thread_payload_caveats` dedupes inconsistently** (confidence: verified)
  In `export_thread_scope.py`, the `errors` loop and the matched/returned line
  check `not in caveats`; the `warnings` loop and `candidate_scan_incomplete`
  do not. Uniform behavior would change output when a payload carries a
  repeated warning, so it is a real inconsistency, not a pure cleanup.

## Correctness / bugs

- **`include_sent=False` does not bound the thread scan** (confidence: verified)
  It filters the export candidates only, so a Sent-resident member can be
  enumerated by `get_email_thread` and then silently dropped from the export.
  Same class as RC-5. Deserves its own issue.

- **Text mode never recovers a missing anchor** (confidence: verified)
  `_retain_anchor` lives in the JSON payload builder, and the `FOUND N` banner
  is rendered inside AppleScript, so text mode can print `FOUND 0` for a thread
  whose anchor was fetched successfully. Documented and routed around in the
  skills; not fixed.

## New features / capability

- **Derive expected member count from the anchor's References chain**
  (confidence: hypothesis) The anchor already yields its `References` header,
  which lists ancestor Message-IDs. Comparing that count against members found
  would give a *positive* completeness signal rather than only bound-based
  caveats — it could say "this thread has at least N messages and we found M".
  Caveat: clients truncate References, so it is a lower bound at best.

- **`scan_messages` on `export_emails(scope="thread")`** (confidence: verified gap)
  The export passes `max_emails` through as `max_messages` but exposes no scan
  control, so the documented remedy for a thread-scan ceiling is unreachable
  from the export call. Today's workaround is calling `get_email_thread`
  directly and exporting by ids.

## Process / docs

- **A guardrail against skills contradicting the code** (confidence: verified gap)
  The first draft of the updated skills told agents `date_floor_hit` makes
  `thread_incomplete` true — the exact bug being fixed, reintroduced at the
  instruction layer and multiplied across eight files by the reference-sync
  script. A skill-review pass caught it; nothing automated would have. The
  existing `tests/cross_cutting/test_id_first_guidance.py` shows the pattern is
  affordable: assert that JSON field names named in skills exist in the payload,
  and that claims about which flag covers which condition match the source.

- **`search/__init__.py`'s facade docstring drifts with every module split**
  (confidence: verified) It said "the six submodules" while there were ten. Now
  unnumbered. Any docstring that counts things in the same repo should either be
  gate-checked or stop counting.

## Evaluation

- **Re-run the original reproduction against the installed plugin after promotion**
  (confidence: verified gap) Everything here was verified against repo source,
  because MCP tools execute the *installed* payload. The fix is not proven for
  the reporting user until the marketplace promotes the payload and the same six
  steps pass through the connector.
