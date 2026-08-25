#!/usr/bin/env python3
"""Fail closed on personal or machine identity committed into this public repo.

``Agentic-Assets/apple-mail-mcp`` is world-readable, and the point of the
codebase is reading real mailboxes, so live mail data is always one test run
away from the working tree. Root ``AGENTS.md`` documented the countermeasure as
a manual ``git diff --cached | grep`` a contributor was supposed to remember.
Prose is skipped exactly when someone is moving fast, which is exactly when a
real address or an absolute home-directory path gets pasted into a doc, a
fixture, or a task handoff. Every other invariant in this repo that matters is a
gate; this makes that one a gate too.

Three rules, scanned across every tracked text file:

1. An email address whose domain is not a reserved documentation domain or a
   known synthetic placeholder.
2. An absolute ``/Users/<name>/...`` path with a real-looking username segment.
3. An uppercase account/EventKit UUID, the shape Apple writes into Mail's
   account directory names and Calendar's item identifiers.

Enforcement is a ratchet, not a clean-tree assertion: ``KNOWN_IDENTITY_HITS``
grandfathers what is already published (a force-push does not unpublish it, so
failing the build over history helps nobody) while any *new* hit fails.

Violation output names the file, the line, and the rule. It deliberately does
not echo the matched value: this gate's own output would otherwise become a
fresh copy of the leak, in a terminal scrollback or a CI log.

Exit 0 when compliant; exit 1 and print violations to stderr otherwise.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Which files get scanned
# ---------------------------------------------------------------------------

# Whole subtrees that are never authored here. Excluding a subtree is the
# strongest kind of exemption, so each entry needs a reason that is about the
# files themselves and not about how noisy they are:
#
#   * plugin/wheelhouse/ — 67 tracked vendored wheels. Several embed the
#     package author's real address in METADATA. Nothing in this repo edits
#     them; they are replaced wholesale on a dependency bump.
#   * archive/ — frozen historical snapshot, retained for provenance only.
#
# `.agents/` was skipped in the first version of this gate on the theory that it
# is all vendored third-party skill docs. It is not: `.agents/skills/` also holds
# this repo's OWN first-party agent skills (`finalize-apple-mail-mcp`,
# `mail-scripting-dictionary`, and friends), and `.claude/skills/*` are symlinks
# into it. Skipping 68 files of content we author — including skill docs that get
# edited by hand while looking at live mailbox output — is the wrong shape for
# this gate, so the prefix is gone. All 68 files are clean under the three rules
# below; the only hits were four placeholder home paths (`/Users/alice/...`,
# `/Users/name/...`), covered by PLACEHOLDER_USER_SEGMENTS, and two
# `@company.com` addresses, covered by SYNTHETIC_TEST_DOMAINS. Neither needed a
# ratchet entry, so a re-vendor cannot break a baseline here.
SKIP_PREFIXES = (
    "plugin/wheelhouse/",
    "archive/",
)

# Binary and archive formats. The NUL sniff below catches most of these on its
# own; naming them is belt-and-braces, because a container that happens to keep
# its first 8 KiB NUL-free would otherwise be decoded and scanned as if it were
# source, producing hits nobody can act on.
SKIP_SUFFIXES = (
    ".whl",
    ".zip",
    ".mcpb",
    ".plugin",
    ".png",
    ".scpt",
)

# Tracked SQLite coverage database. It stores absolute local paths for every
# measured file, so it trips rule 2 on every run and can never be "fixed".
SKIP_BASENAMES = frozenset({".coverage"})

BINARY_SNIFF_BYTES = 8192

# ---------------------------------------------------------------------------
# Rule 1 — email addresses
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# RFC 2606 / RFC 6761 reserved names. These can never resolve to a real
# mailbox, so an address at one of them is documentation by construction. This
# is the only part of the allowlist that is justified by a standard rather than
# by inspection of this tree.
RESERVED_EMAIL_DOMAINS = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "example.test",
        "invalid",
        "test",
        "localhost",
    }
)

# Placeholder domains this repo's fixtures and skill docs already use, found by
# enumerating every distinct non-reserved domain in the tracked tree and keeping
# only the ones that cannot plausibly belong to a person we correspond with:
# generic role nouns (`company.com`, `vendor.com`), foo/bar stand-ins, and
# one- and two-letter stubs used to keep synthetic ``Message-ID`` fixtures
# short (``<a@x.com>``).
#
# The split between this set and the ratchet is the whole design, so it is worth
# stating plainly. A domain belongs HERE when an address at it is *inherently*
# not identity — no allowlist rot is possible, because no real person will ever
# appear at `bar.com` in this repo. A domain belongs in KNOWN_IDENTITY_HITS
# when addresses at it *are* identity and some are already published: the
# company domain, any founder's personal domain, any `.edu`, and every real
# vendor, publisher, conferencing, or mail-provider domain (`gmail.com`,
# `google.com`, `apple.com`, and friends). Grandfathering those per-file keeps
# the existing text legal while still failing on the next one, which an
# allowlist entry would not.
#
# `x.com` is the one entry with real tension: it is a live domain (X/Twitter),
# but here it is only ever the single-letter stub in synthetic header fixtures,
# ~60 occurrences across 8 test modules. Ratcheting it would mean 8 baseline
# entries that churn every time a test gains or loses a fixture line, and churn
# is what teaches people to edit baselines blind. Same reasoning for `news.com`
# and `calendar.com`: generic nouns carrying `no-reply@`/`noreply@` locals in
# fixtures, never a correspondent.
SYNTHETIC_TEST_DOMAINS = frozenset(
    {
        "a.com",
        "acme.com",
        "b.com",
        "bar.com",
        "c.com",
        "calendar.com",
        "client.com",
        "clienta.com",
        "company.com",
        "domain.tld",
        "ex.com",
        "f.com",
        "news.com",
        "partner.com",
        "unwanted.com",
        "vendor.com",
        "x.com",
        "y.com",
        "z.com",
    }
)

ALLOWED_EMAIL_DOMAINS = RESERVED_EMAIL_DOMAINS | SYNTHETIC_TEST_DOMAINS

# ---------------------------------------------------------------------------
# Rule 2 — absolute home-directory paths
# ---------------------------------------------------------------------------

# Requires at least one path-segment character after the slash, which is what
# makes the rule stronger rather than weaker: a bare mention with nothing
# following (prose, or the documented grep pattern in AGENTS.md itself) carries
# no identity and is not a violation. That is also why there is no file-level
# exemption for AGENTS.md or CLAUDE.md — the gate must be able to scan its own
# rulebook, and file exemptions are how gates rot.
ABSOLUTE_USERS_PATH_RE = re.compile(r"/Users/([A-Za-z0-9._-]+)")

# Username segments that are obviously stand-ins. Same asymmetry as the domain
# allowlist and the same reason it is safe: a real username cannot masquerade as
# a placeholder, so exempting these cannot hide a real one. Kept minimal on
# purpose — every name added here is a name the gate stops seeing.
#
#   * `example` — the fixture spelling in tests/core/test_metadata_index_contract.py
#   * `...`     — an already-redacted path, e.g. `/Users/.../Mail`
#   * `alice`   — the canonical documentation stand-in, in a bundled skill doc's
#                 shell snippet under `.agents/skills/plugin-settings/`
#   * `name`    — literally the word "name" as a metavariable, and in every case
#                 in this tree it appears inside a "don't do this" example of a
#                 hardcoded absolute path in `.agents/skills/plugin-structure/`.
#                 The angle-bracket form `/Users/<name>/` never matched anyway
#                 (`<` is outside the segment character class); this covers the
#                 bare form.
PLACEHOLDER_USER_SEGMENTS = frozenset({"...", "alice", "example", "name"})

# ---------------------------------------------------------------------------
# Rule 3 — account / calendar-item UUIDs
# ---------------------------------------------------------------------------

# Uppercase only, and deliberately so. Apple Mail's per-account directory names
# and EventKit's calendar-item identifiers are uppercase, so uppercase-only
# targets the actual threat. A case-insensitive version would additionally
# match every lowercase plugin id, bundle id, and MCPB identifier in the repo —
# hundreds of legitimate hits, i.e. a rule nobody could keep green.
#
# AGENTS.md's prose grep is the looser `[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-` prefix
# form: case-insensitive and only the first two groups. That is right for a
# human eyeballing a diff and wrong for a gate. This narrowing is intentional.
ACCOUNT_UUID_RE = re.compile(r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}")

# ---------------------------------------------------------------------------
# Ratchet baseline
# ---------------------------------------------------------------------------

# Keyed by repo-relative POSIX path -> total occurrence count across all three
# rules.
#
# NOT keyed by line number. These files get edited for reasons that have nothing
# to do with identity, and a line-keyed baseline would fail the build every time
# something above a hit moved. That failure carries no information, so the fix
# people learn is "bump the baseline" — which is worse than no gate, because it
# looks like a gate. Counts survive that churn and still fail closed on an extra
# hit in a known file.
#
# NOT keyed by basename either: `progress-log.md`, `issue-summary.md`, and
# `todo.md` recur across `tasks/`, so one basename entry would silently exempt a
# different file, and the failure message would not say which one to fix.
#
# Lowering a number here is always a valid change; so is deleting an entry.
# Raising one is not — redact the new hit instead. The staleness test in
# tests/infra/test_no_committed_identity.py enforces that direction, so a fixed
# file must be removed from this dict rather than left as slack the next leak
# can hide inside.
#
# What is grandfathered, by category:
#   * the company domain and founders' domains in README/docs/skill metadata
#   * `.edu` and real vendor/publisher/conferencing domains quoted in dated
#     research and audit notes
#   * absolute home paths in `tasks/archive/**` reports only (rewriting a
#     published archive changes history without unpublishing anything). Every
#     grandfathered absolute path now lives under `tasks/archive/**`: the two
#     that were in still-consulted `tasks/reference/**` docs were redacted to
#     `~/`-relative form, because "we still read this doc" and "a real machine
#     username is in it" is the exact pair this gate exists to break up.
#   * uppercase EventKit item identifiers in one archived live-write report
#
# Generated from the tree, not transcribed: 37 files / 85 occurrences
# (64 addresses, 15 absolute paths, 6 UUIDs) as of 2026-08-17.
KNOWN_IDENTITY_HITS: dict[str, int] = {
    "CHANGELOG.md": 1,
    "docs/AGENT_LIVE_TESTING.md": 1,
    "docs/findings-allow-full-scan-audit-2026-06-09.md": 2,
    "docs/live-testing-reports/LIVE_FIELD_REPORT_2026-06-04.md": 1,
    "plugin/apple_mail_mcp/calendar_core/eventkit.py": 1,
    "plugin/apple_mail_mcp/calendar_core/records.py": 1,
    "tasks/CLAUDE.md": 2,
    "tasks/active/v4-performance-consolidation-2026-05-27/learnings-and-parking-lot.md": 1,
    "tasks/active/v4-performance-consolidation-2026-05-27/phase-plan.md": 5,
    "tasks/active/v4-performance-consolidation-2026-05-27/progress-log.md": 1,
    "tasks/archive/2026-05-21/CLI_TESTING_REPORT_2026-05-21.md": 1,
    "tasks/archive/2026-05-21/README.md": 2,
    "tasks/archive/2026-05/robustness-completion-audit-2026-05-22.md": 7,
    "tasks/archive/2026-05/robustness-next-steps-2026-05-22.md": 3,
    "tasks/archive/2026-05/whose-elimination-2026-05-22/02-mcp-architecture-research.md": 4,
    "tasks/archive/2026-06/issues/reply-body-insertion-failure-2026-06-18.md": 0,
    "tasks/archive/2026-06/shipped/codex-claude-plugin-setup-2026-06-07/progress-log.md": 1,
    "tasks/archive/2026-06/shipped/codex-mcp-tool-registration-incident-2026-06-08/issue-summary.md": 2,
    "tasks/archive/2026-06/shipped/codex-mcp-tool-registration-incident-2026-06-08/native-reply-and-draft-lifecycle-issue.md": 1,
    "tasks/archive/2026-06/shipped/codex-mcp-tool-registration-incident-2026-06-08/progress-log.md": 1,
    "tasks/archive/2026-07/shipped/agentic-1214-reply-fixes/plan-2026-07-10.md": 1,
    "tasks/archive/2026-07/shipped/agentic-1214-reply-fixes/reports/phase1-domain-2026-07-10.md": 4,
    "tasks/archive/2026-07/shipped/agentic-1214-reply-fixes/reports/phase1-linear-triage-2026-07-10.md": 1,
    "tasks/archive/2026-07/shipped/agentic-1214-reply-fixes/reports/phase7-live-verification-2026-07-10.md": 1,
    "tasks/archive/2026-07/shipped/agentic-1277-compose-draft-verification/closeout-2026-07-11.md": 1,
    "tasks/archive/2026-07/shipped/agentic-1277-compose-draft-verification/plan-2026-07-11.md": 1,
    "tasks/archive/2026-07/shipped/apple-calendar-tools/reports/phase1-codebase-map-2026-07-10.md": 1,
    "tasks/archive/2026-07/shipped/apple-calendar-tools/reports/phase1-platform-apis-2026-07-10.md": 1,
    "tasks/archive/2026-07/shipped/apple-calendar-tools/reports/phase5-gates-2026-07-10.md": 2,
    "tasks/archive/2026-07/shipped/apple-calendar-tools/reports/phase5-live-smoke-2026-07-10.md": 7,
    "tasks/archive/2026-07/shipped/apple-calendar-tools/reports/phase6-fixes-2026-07-10.md": 1,
    "tasks/archive/2026-07/shipped/apple-calendar-tools/reports/phase8-live-writes-2026-07-10.md": 6,
    "tasks/archive/2026-07/shipped/apple-calendar-tools/reports/phase9-live-fixes-2026-07-10.md": 3,
    "tasks/reference/live-test-baseline-2026-05-21.md": 7,
    "tasks/reference/phase-plan-3.1.7.md": 5,
    "tasks/reference/robustness-backlog-2026-05-22.md": 1,
    "tests/inbox/test_gmail_unread_crash_regression.py": 1,
}


class IdentityScanError(RuntimeError):
    """Raised when the tracked-file enumeration itself cannot be trusted."""


def _tracked_paths(root: Path) -> list[str]:
    """Repo-relative paths of every tracked file, via ``git ls-files -z``.

    Git is the authority on what is committed, which is the thing this gate is
    about. Walking the filesystem would scan untracked scratch files (noise a
    contributor cannot act on) and miss nothing in return, and calling
    ``git check-ignore`` per path would be one subprocess per file to answer a
    question ``ls-files`` already answered in one.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover
        raise IdentityScanError(f"git ls-files failed in {root}: {exc}") from exc
    return [rel for rel in result.stdout.decode("utf-8", errors="replace").split("\0") if rel]


def iter_tracked_text_files(root: Path | None = None) -> Iterator[tuple[str, str]]:
    """Yield ``(repo_relative_posix_path, decoded_text)`` for scannable files.

    Decoding uses ``errors="replace"`` rather than skipping on a decode error:
    a file that is mostly text with one bad byte still needs scanning, and a
    replacement character cannot manufacture a false hit for any of the three
    patterns.
    """
    base = root or ROOT
    for rel in _tracked_paths(base):
        if rel.startswith(SKIP_PREFIXES) or rel.endswith(SKIP_SUFFIXES):
            continue
        if rel.rsplit("/", 1)[-1] in SKIP_BASENAMES:
            continue
        path = base / rel
        if not path.is_file():
            # Tracked directory symlinks (`.claude/skills/*` -> `.agents/skills/*`)
            # land here. There is no text to scan through them, and their targets
            # are enumerated under their own paths.
            continue
        blob = path.read_bytes()
        if b"\0" in blob[:BINARY_SNIFF_BYTES]:
            continue
        yield rel, blob.decode("utf-8", errors="replace")


def _domain_is_allowed(domain: str) -> bool:
    """True when ``domain`` matches an allowlist entry exactly or as a subdomain."""
    candidate = domain.lower().rstrip(".")
    return any(candidate == allowed or candidate.endswith(f".{allowed}") for allowed in ALLOWED_EMAIL_DOMAINS)


def identity_hits_in_line(line: str) -> list[str]:
    """Redacted descriptions of every identity hit on one line.

    Each returned string names the rule and the minimum needed to find the hit.
    The matched value never appears: for an address the domain is enough to
    locate it, and for a username or a UUID even that much would republish the
    identity into whatever log captures this gate's output.
    """
    hits: list[str] = []

    for match in EMAIL_RE.finditer(line):
        domain = match.group(0).rsplit("@", 1)[1]
        if not _domain_is_allowed(domain):
            hits.append(f"email address (domain {domain.lower().rstrip('.')}, local part redacted)")

    for match in ABSOLUTE_USERS_PATH_RE.finditer(line):
        segment = match.group(1)
        if segment.lower() in PLACEHOLDER_USER_SEGMENTS:
            continue
        hits.append(f"absolute /Users path (segment redacted, {len(segment)} chars)")

    hits.extend("uppercase account/calendar UUID" for _ in ACCOUNT_UUID_RE.finditer(line))
    return hits


def scan_identity(root: Path | None = None) -> dict[str, list[str]]:
    """Map repo-relative path -> one redacted ``line: description`` per occurrence.

    Occurrences, not lines: a line with two addresses contributes two entries, so
    replacing one of them registers as progress and ``len()`` is the ratchet
    count. Clean files are absent rather than empty.

    Grouped by file rather than returned flat because both consumers want it that
    way — the ratchet compares one file's count against one baseline entry, and
    the failure message lists that file's hits. A flat list would have to be
    regrouped by splitting each formatted string back apart.
    """
    hits: dict[str, list[str]] = {}
    for rel, text in iter_tracked_text_files(root):
        for lineno, line in enumerate(text.splitlines(), 1):
            for hit in identity_hits_in_line(line):
                hits.setdefault(rel, []).append(f"{lineno}: {hit}")
    return hits


def stale_baseline_entries(hits: dict[str, list[str]]) -> list[str]:
    """Baseline entries that now claim more hits than the tree contains.

    Consumed by the staleness test, not by ``main``: an over-generous entry is
    slack, not a leak, so it should fail review rather than block a commit.
    """
    stale: list[str] = []
    for rel, expected in sorted(KNOWN_IDENTITY_HITS.items()):
        found = len(hits.get(rel, ()))
        if found < expected:
            stale.append(f"{rel}: baseline says {expected}, found {found}")
    return stale


def ratchet_regressions(hits: dict[str, list[str]]) -> list[str]:
    """One error string per file carrying more hits than its baseline allows.

    An unlisted file fails on its first hit, because ``.get(rel, 0)`` defaults
    to zero.
    """
    errors: list[str] = []
    for rel, found in sorted(hits.items()):
        allowed = KNOWN_IDENTITY_HITS.get(rel, 0)
        if len(found) > allowed:
            listing = "".join(f"\n      {rel}:{line}" for line in found)
            errors.append(f"{rel}: {len(found)} identity hit(s), ratchet baseline allows {allowed}{listing}")
    return errors


def validate_no_committed_identity(root: Path | None = None) -> list[str]:
    """Return validation errors for newly committed identity."""
    try:
        hits = scan_identity(root)
    except IdentityScanError as exc:
        # Fail closed: an enumeration we cannot trust is not a clean tree.
        return [str(exc)]
    return ratchet_regressions(hits)


def main() -> int:
    errors = validate_no_committed_identity()
    if errors:
        print("committed identity validation FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print(
            "\nRedact the hit (synthetic address, relative or <name>-elided path, "
            "placeholder UUID) — see AGENTS.md § This repo is PUBLIC. Do not raise "
            "KNOWN_IDENTITY_HITS in tools/validators/validate_no_committed_identity.py.",
            file=sys.stderr,
        )
        return 1
    print("committed identity: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
