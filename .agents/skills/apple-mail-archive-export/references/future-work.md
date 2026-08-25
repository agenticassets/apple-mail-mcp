# Future work: known gaps, and how I would close them

Read this when extending the skill, when something in it feels clumsy, or when a
user's request lands slightly outside what the bundled scripts do. It is a
deliberate record of what is *missing* rather than what exists, written while the
reasoning was fresh.

Everything here is a judgement call, not a mandate. Where I have a
recommendation I say so; where the tradeoff is genuinely open I say that instead.
Nothing below is required for the documented workflow to be correct.

## Contents

1. The one I would build first
2. Robustness gaps
3. Capability gaps users will ask for
4. Simplification and structure
5. Things that look like gaps but are not
6. Testing and evaluation
7. Verified-fixed history

---

## 1. The one I would build first

**A synthetic fixture store plus a `selftest.sh`.**

Every trap in `pitfalls.md` is currently defended by prose and by the user's real
mail. That is backwards: the traps are known and enumerable, so they should be
regression tests that run in a second against fake data.

Build `assets/fixture-store/` as a miniature `V10` tree - a dozen hand-authored
`.emlx` files plus the sibling directories - covering exactly the cases that have
bitten:

| Fixture | Guards against |
|---|---|
| plain message, no attachments | baseline |
| `.partial.emlx` + `Attachments/<id>/<part>/file` | dropping detached payloads |
| `.partial.emlx` + `.emlxpart` sidecar | double-encoding the already-encoded source |
| `message/rfc822` attached email | the `set_payload` string-vs-list bug |
| uuencoded part | the legacy encoding path |
| `.pages`-style bundle directory payload | the re-zip path |
| stub with **no** payload on disk | filling a permanent gap with zero bytes |
| folded `Received:` header, Exchange-style | header injection landing inside a fold |
| body line beginning `From ` | mboxrd escaping and its reversal |
| filename containing a literal newline | line-delimited pipelines miscounting |
| nested child `.mbox` under a parent | the parent absorbing its descendants |
| `.emlx` with no Envelope Index row | database-driven enumeration skipping it |
| message with no `From:` and no `Date:` | envelope fabrication and date fallback |

Then `scripts/selftest.sh` runs the whole pipeline against it and asserts the
exact expected report, exit codes, and `.eml` bytes.

Why this is first: **both bugs found so far would have been caught by it in under
a second**, with no access to anyone's real mail, no Full Disk Access, and no
2 GB of scratch space. It also makes the skill safely editable by someone who has
never read `emlx-format.md`, which is the actual barrier to anyone improving it.
It is the highest ratio of confidence gained to effort spent by a wide margin.

## 2. Robustness gaps

### ~~Sampling in `verify_export.py` is uniform~~ (done, and it paid for itself immediately)

Implemented. `choose_sample()` now forces in every message with a nested-email
payload, an `.emlxpart` sidecar, a re-zipped bundle directory, a rare payload
extension, an unusually high part count, or the `oversize-` filename shape, then
fills the remaining budget uniformly. Nothing forced in is ever dropped to respect
the budget; the overflow is reported instead.

Worth recording what this cost and bought, because it is the clearest argument in
this document for stratifying any sampled verification:

- **The estimate that motivated it was too kind.** The original note reasoned that
  a uniform 2,500-message sample had a ~79% chance of catching the 24-message
  `message/rfc822` bug. Measured against the actual corpus at the default seed, a
  uniform draw caught **zero of 26** nested-email messages. The analytic estimate
  assumed independence that the real file layout does not have.
- **It found a new bug on its first run.** The stratified sample immediately
  surfaced a message whose entire body was missing from the delivered archive, a
  detached `text/calendar` payload with no `X-Apple-Content-Length` stub. See
  `pitfalls.md` § 1. No count in the report was wrong; only a byte-comparison of a
  structurally unusual message could have caught it.
- **Cost: 949 forced messages of 39,269 (2.4%) and a few seconds of selection.**

Detecting nested emails needs a content sniff, not a filename rule. Attached emails
arrive named anything; an extension-based test found 3 of 24, and requiring two
recognizable header names inside 2 KB found 12 of 24, because Microsoft ARC-Seal
and DKIM blobs fill the window before any ordinary header appears. Testing the
*shape* of the first 8 KB - a run of `Name: value` lines and folded continuations -
finds 24 of 24.

Still true: prefer `--all` when the account is small enough to afford it, and say
the coverage percentage out loud when it is not.

### No resume after a failed run

A 39,269-message export is one process. If it dies at 80%, the next attempt starts
from zero. `--only <mailbox>` gives coarse restartability, but nothing finer.

Fix: append each completed `(mailbox, emlx_id)` to a manifest as it is written, and
skip anything already listed unless `--restart` is passed. Cheap, and it turns a
long export from something you babysit into something you can interrupt.

### Single-threaded, and the bottleneck is known

188 seconds for 15 GB is fine, so this is not urgent - but attachment
re-encoding dominates wall time, not message parsing. A `ProcessPoolExecutor`
fanned out over top-level mailboxes would parallelize cleanly, since mailboxes are
independent.

One constraint if you do it: **keep every SQLite write in the parent process.** The
index is built with `cur.lastrowid` to link the FTS rows, and concurrent writers
would either serialize on the lock or misattribute rows.

### Snapshot integrity is proven once and never rechecked

`snapshot_account.sh` writes SHA-256 manifests for both trees at capture time, and
nothing ever looks at them again. For an archive meant to outlive the account, add
a `--reverify` mode that re-hashes the snapshot against its stored manifest to
detect bit rot or a bad copy.

Important detail if you compare against the *live* store rather than the manifest:
Mail toggles plist `flags` bit 7 while running, so whole-file hashes drift
benignly. Compare the RFC-822 message region between the length header and the
plist trailer. See the drift entry in `pitfalls.md`.

### Date fallback when the `Date:` header is missing or malformed

Messages with no parseable `Date:` sort to the front and get a 1970 mbox envelope
timestamp. The exporter already reads `date-received` out of the plist trailer for
other purposes, and that is a better fallback than the epoch. Confirm the current
behavior before changing it, and prefer: `Date:` header, then plist
`date-received`, then epoch as a last resort with the message flagged in the report.

### Flattened mailbox names can theoretically collide

Nested mailboxes are flattened for mbox output by replacing `/` with `.`, so a
child mailbox `a/b` and a literal top-level mailbox named `a.b` would write to the
same file. Vanishingly unlikely, currently silent. A collision check that appends a
disambiguating suffix would cost three lines.

## 3. Capability gaps users will ask for

### Attachment-only extraction - shipped

`scripts/extract_attachments.py` now closes this. It went further than the sketch
that used to sit here, in two ways worth recording because both were discovered
rather than designed.

The sketch proposed one file per occurrence under `attachments/<mailbox>/`. That
would have written 14.46 GB to store 8.62 GB of distinct bytes, because 85% of
attachments on business mail are the same signature logo repeated. Content
addressing plus hard-linked views gives the same browsable tree for the smaller
number, so dedup is not an optimization to add later; it decides the layout.

The sketch also treated collision handling as "three lines". It is not, because a
declared filename is attacker-controlled and can be encoded: `../../../escape.txt`
survives inside an RFC 2047 base64 word or split across RFC 2231 continuations, so
any sanitizer that runs before decoding waves it through. Decode first, sanitize
second, and keep a test that asserts containment for the encoded forms rather than
only the literal `../` case.

Remaining gap: the extractor deliberately does not descend into `message/rfc822`
attachments, matching the exporter, so attachments nested inside forwarded emails
are stored as the containing `.eml` rather than individually. See the counting
discussion in `pitfalls.md` before changing that, since it moves several published
totals at once.

### Whole-store export in one command

"I'm wiping this Mac, archive everything" is a common framing, and the workflow
currently handles one account per invocation. `discover_accounts.py` already knows
every account; a `--all-accounts` mode that loops and writes one archive per
account, with a combined top-level README and a single roll-up report, would match
what people actually ask for.

### Mark which messages Mail no longer lists

The archive is a superset of what Mail displays, because enumeration walks the
filesystem and Mail deletes index rows without unlinking files. On one account that
was 531 exported versus 520 in the Envelope Index - eleven recovered Drafts.

Right now that surfaces as an unexplained count discrepancy the operator has to
chase down by hand. Better: read the Envelope Index once (on a checkpointed copy,
read-only) and record `in_envelope_index` per message in `index.sqlite`. Then
`search.py --recovered-only` turns a confusing superset into a feature - "here is
mail Mail had already forgotten."

### Thread reconstruction

`References:` and `In-Reply-To:` are on disk but not exploited. Chaining them into
a `thread_id` column would make the archive substantially more useful for anyone
reconstructing a conversation years later, which is a large share of why people
keep old mail at all.

### Duplicate accounting

1,285 Message-IDs appear more than once - the same message filed in two folders.
The archive stores every copy, which is correct for fidelity and wasteful on disk.
Worth *reporting* the duplicate byte count so the user can decide, rather than
deduplicating for them. Hardlinking identical `.eml` files behind an explicit flag
would be a reasonable option; doing it silently would not.

## 4. Simplification and structure

### The shared-primitives question, and its real tension

`unfold()`, the hash-bucket calculation, `.emlx` byte-layout parsing, and payload
discovery all appear in more than one script. The obvious move is a shared
`emlxlib.py`.

Do that **only for the format primitives** - byte layout, bucket math, header
unfolding. Those are mechanical and drift between copies is pure risk.

Do *not* share the payload-discovery logic between `export_emlx.py` and
`verify_export.py`. That duplication is load-bearing: `verify_export.py` is only a
meaningful check because it locates and decodes payloads *independently*. If both
scripts derive their answer from one implementation, they agree by construction and
the verifier can no longer catch a bug in the thing it is verifying. The whole
reason the `message/rfc822` corruption was caught is that the two disagreed.

This tension is worth stating explicitly in any refactor: shared code is better for
maintenance and worse for independent verification, and the right split is not
"share everything that looks similar."

### `export_emlx.py` sits outside the repo's line-budget gate

It is around 980 lines (976 at the time of writing; recount with `wc -l`
rather than trusting this number). This repo warns at 600 LOC and fails on baseline
regression, but `tools/validators/check_module_line_budget.py` only scans
`plugin/apple_mail_mcp/` and `tools/` - `.agents/skills/*/scripts/` is not covered.

Two honest options, and this is a call for the maintainer rather than something to
change unilaterally:

- **Extend `SCAN_ROOTS`** to include skill scripts, then split the module into a
  package (`export_emlx/` with focused modules) as the repo does elsewhere.
  Consistent with existing convention, and applies the same discipline everywhere.
- **Leave skills out of scope deliberately.** Skill scripts are standalone, are
  copied next to archives to run years later with no package context, and a single
  self-contained file is genuinely easier to hand someone. A clear single-file
  script can beat six files that must be kept together.

I lean toward the second for *this* script specifically, because being copyable as
one file into `20-tools/` is a real property worth protecting. But the inconsistency
should be a decision on the record, not an oversight.

### Minor

`search.py` resolves its archive three ways - explicit `--archive`, the
`ARCHIVE_DIR` environment variable, and a working-directory probe. The third is
convenience that overlaps the second; dropping it would remove a branch and a
class of "why did it pick that directory" confusion.

## 5. Things that look like gaps but are not

Recorded so nobody spends effort re-litigating them:

- **DKIM and S/MIME cannot be made verifiable.** Mail stores messages
  LF-normalized rather than in CRLF wire format. The loss happened when Mail wrote
  the files, long before any export. Only a server-side export preserves it.
- **`Bcc` is unrecoverable** for received mail. It was never in the message.
- **Messages predating the local cache cannot be recovered from disk at any
  effort.** Report the earliest `Date:` and escalate to the mail administrator
  while the account still exists. This is the only part that expires, which makes
  it the highest-value thing to raise early.
- **The `xacl_mismatch` counter firing in the hundreds is expected.** See
  `report-fields.md`; byte-comparison is the authority.
- **`filled_emlxpart: 0` is normal.** Sidecars are rare - 15 files in a
  91,402-message store.

## 6. Testing and evaluation

- **Rerun the eval set against the current scripts.** The mbox round-trip
  assertion currently passes only because a test run detected and patched the
  folded-header bug itself; on the fixed skill it should pass outright.
- **The headline use case is the least tested one.** All three evals ran against
  live accounts. Nothing exercises a genuinely decommissioned account, where
  cross-checking against Mail is impossible and the operator has one shot. Worth an
  eval that forbids consulting Mail.app at all.
- **Watch for baseline contamination.** A finished archive left on disk from a
  previous run is readable by a no-skill baseline, and one baseline leaned on a
  previous run's `export_emlx.py` for the hardest part of its answer. Move prior
  archives aside before measuring, or treat the deltas as a lower bound and say so.
- **Untested scale.** The largest real run was 39,269 messages. Behavior at 100k+,
  and on a store with several large accounts, is inferred rather than measured.
- **No eval covers `.emlxpart` sidecars or an attachment-only request**, both of
  which are documented paths.

## 7. Verified-fixed history

Kept because a fixed bug is the best evidence for which checks earn their keep.

| Bug | How it was caught | Now guarded by |
|---|---|---|
| `message/rfc822` parts silently emptied by `set_payload(str)`; 61 attached emails destroyed across 24 messages while the run reported `39,269 written, 0 parse errors` | byte-comparing exported attachments against the originals | `verify_export.py` |
| Flag headers injected inside Exchange's folded `Received:` header, corrupting 85% of an Exchange archive while separator counts, attachment byte-comparison and Message-ID multisets all passed | reconstructing each mbox message and diffing it against its `.eml` | `check_mbox.py` |
| `Status:` written where the stated target was Thunderbird, which ignores it - read state would have silently vanished on import | asking what the *destination client* reads, not what the format allows | both header families written; `importing.md` |
| `plist` referenced before assignment on the oversize path | a 910 MB draft | initialized before the try |
| Fragile correlated subquery linking FTS rows | code review | per-row insert with `cur.lastrowid` |
| A detached single-part body with **no** `X-Apple-Content-Length` stub exported as a completely empty message. No leftover stub, so the stub check passed; `missing_parts` stayed 0; the message looked structurally valid and was simply blank | a stratified verification sample, on its first run | reassembly now triggers on "empty part with a payload at its part number", reported as `filled_unmarked` |
| Oversize messages were unverifiable: `verify_export.py` recovered the message id from the filename and fell back to an empty string for the `oversize-<id>.eml` shape, so every check for them became a silent no-op | reading the verifier while asking what it does with an input it does not understand | both filename shapes parsed; unnameable files fail the gate as `unidentified_files`; oversize always forced into the sample |
| `.emlxpart` sidecars were never verified at all - `index_originals()` walked only `Attachments/`, so the entire already-encoded splice path had no check behind it | comparing what the exporter reads against what the verifier reads | sidecars indexed as `kind: encoded` and compared after decoding |
| Re-zipped bundle parts were skipped outright because their bytes legitimately differ, so an empty bundle part would have passed | asking what the skip was actually asserting | skipped for equality, still asserted non-empty |
| `verbatim + reassembled == total` was documented as absolute but broke on any reassembly failure, with no field naming the shortfall | trying to state the invariant precisely enough to assert it in code | `reassembly_failed` and `unreadable` split out; three closed sums asserted by `Stats.check_invariants()`, non-zero exit on violation |
| `_stub_has_content()` raised `AttributeError` on a stub whose single child was itself multipart, surfacing as a parse error and blaming the wrong thing | reading for type assumptions rather than running anything | inner payload type-checked before `.strip()` |

The pattern worth internalizing: **every one of these passed the checks that
existed at the time.** New checks came from asking "what would still look fine if
this were broken?" - which is a better generator of tests than enumerating what the
code does.

Two later additions to that pattern, both earned the hard way:

**Ask what a check does with input it does not understand.** Four of the entries
above are not wrong logic but *absent* logic: a fallback to an empty id, a `continue`
past bundles, a walk that never visited sidecars. Each read as a check and behaved as
a pass. A gate that cannot say "I did not examine this" will eventually claim it did.

**Bounding a scan for speed makes the bound part of the claim.** While measuring the
unmarked-body bug, a scan of each `.partial.emlx`'s first 1 MB reported 51 affected
messages. The real count was 1; large messages simply carry their stubs past the 1 MB
mark. The number was quoted before the bound was questioned.
