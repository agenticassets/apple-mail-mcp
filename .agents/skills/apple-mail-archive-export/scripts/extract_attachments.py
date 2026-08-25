#!/usr/bin/env python3
"""
Extract every attachment out of an exported .eml archive into a browsable,
content-deduplicated tree with a manifest.

  ./extract_attachments.py --out ARCHIVE/10-export [--dest ARCHIVE/20-attachments]
  ./test_extract_attachments.py          # the hostile-filename gate

Reads `eml/` and `messages.jsonl`; writes only under --dest. Nothing in
`10-export/` or the raw snapshot is ever opened for writing.

Two traps this exists to avoid, both of which produce a clean-looking full run.

**`get_payload(decode=True)` returns `None` for a `message/rfc822` part.** A
nested email's payload is a *list holding a Message*, not an encoded string, so
the part reads as multipart and the decode path declines. The obvious
`data = part.get_payload(decode=True) or b""` therefore writes a zero-byte file
for each of the 102 attached emails in this corpus and counts every one a
success. It is the exact mirror of the `set_payload()` bug that destroyed 61
attached emails during export while the report read `39,269 written, 0 parse
errors` (references/pitfalls.md section 1). So a part whose bytes cannot be
obtained is counted as a failure and fails the run, never written as an empty
file.

**"Attachment" is not one number, and choosing a definition silently changes
it.** This archive already carried two disagreeing counts: `search.py --stats`
said 43,482 and `export-report.json` said 45,506 parts filled. Both are right
about different sets, so a count with no stated rule cannot be checked. The rule
is stated below, every leaf part lands in exactly one bucket, and the buckets are
asserted to sum to the parts examined.

**The attachment rule**, applied to every MIME entity in order:

  1. `multipart/*` is structure: never an attachment, never a leaf.
  2. A declared filename (`Content-Disposition: filename` or `Content-Type:
     name`, RFC 2231 collapsed then RFC 2047 decoded) makes it an attachment.
  3. `Content-Disposition: attachment` makes it one, named or not.
  4. `message/rfc822` is one whole nested email, serialized to a single `.eml`
     blob. Its inner parts are not enumerated, so a nested email counts once
     rather than once per part inside it.
  5. Any remaining `image/*`, `audio/*`, `video/*`, `application/*` leaf is an
     attachment. This is what catches inline images carrying no filename.
  6. Everything else is a body part and is excluded: `text/plain` and
     `text/html` alternatives, `text/calendar` invite bodies, and the
     `message/delivery-status` / `text/rfc822-headers` parts of a bounce report.

Rule 6 is load-bearing. It excludes 1,844 unnamed `text/calendar` parts here,
which are the *body* of a meeting invite rather than a file anyone attached; Mail
detaches them to disk exactly like an attachment, which is most of why
`export-report.json` counts 2,024 more parts than `search.py` does. Every
excluded part is counted by content type in the report, so disagreeing with this
rule does not require re-running anything to see what it cost.

**Layout.** Bytes are stored once under `blobs/<aa>/<bb>/<sha256>`, extensionless
because a hash is not a filename. Every occurrence is a hard link from a
human-navigable view, so dedup costs no browsability and the links cost no bytes:

  by-folder/<Mailbox>/<YYYY>/<date>_<name>    mirrors the .eml tree
  by-sender/<address>/<YYYY>/<date>_<name>    "the contract Meagan sent in 2024"
  by-type/<ext>/<YYYY>/<date>_<name>          "just get me the PDFs"

**Filenames arrive from untrusted email and are treated as hostile.** Each is
reduced to a basename, stripped of control characters and separators, capped in
*bytes*, guarded against Windows reserved names, and every resolved path is
asserted inside its view root before anything is written.
`test_extract_attachments.py` proves that against the real filesystem.

Exits non-zero if any accounting sum fails to close, if any attachment yielded no
bytes, or if any path or link check trips.
"""

from __future__ import annotations

import argparse
import email
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import Message
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_emlx import parse_sender, walk_numbered  # noqa: E402

VIEWS = ("by-folder", "by-sender", "by-type")
BINARY_MAINTYPES = frozenset({"image", "audio", "video", "application"})

# Messages over this are recorded as deferred rather than parsed. The default
# clears this corpus's two 910 MB drafts, which hold four real attachments no
# other artifact counted; parsing one costs a few GB of RAM.
MAX_MESSAGE_BYTES = 2 * 1024**3

# APFS allows 255 bytes per path component, and the view name is "<date>_<name>"
# or "<date>_<id>.<part>_<name>" after a collision. Cap in BYTES, not characters:
# a 100-character CJK name is 300 bytes.
MAX_NAME_BYTES = 180
MAX_EXT_BYTES = 20

WIN_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
# NUL, every other control character, both separators, the colon macOS legacy
# tools still read as a separator, and the Windows-illegal set.
ILLEGAL = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')
_NOT_EXT = re.compile(r"[^a-z0-9]+")


class UnsafePath(Exception):
    """A sanitized name still resolved outside its view root."""


class BlobConflict(Exception):
    """An existing blob's size disagrees with content hashing to its name."""


# ---------------------------------------------------------------------------
# Filenames from untrusted email
# ---------------------------------------------------------------------------


def declared_filename(part: Message, stats: Stats, where: str = "") -> tuple[str | None, bool]:
    """(filename as the sender wrote it, header was unreadable).

    `get_filename()` already reads Content-Disposition `filename` then
    Content-Type `name` and collapses RFC 2231 charsets and continuations. It
    does NOT decode RFC 2047 encoded words, which is why three names in this
    corpus sit in the search index literally as `=?gb2312?B?...?=`.

    It can also raise. compat32 wraps any header holding surrogates in a
    `Header` with charset UNKNOWN8BIT, whose `append()` calls
    `encode('ascii', 'surrogateescape')` - and that handles the low surrogates
    `\\udc80-\\udcff` that a byte-level header decode produces while raising
    `UnicodeEncodeError` on a lone high surrogate. A single such header must not
    end a 39,269-message run with a truncated manifest and no report, so the
    call is guarded, counted, and reported. The second return value says a name
    *was* declared but could not be read, which is still evidence of an
    attachment: the caller promotes the part rather than letting an unreadable
    header downgrade it to a body part.
    """
    try:
        raw = part.get_filename()
    except Exception as exc:
        stats.filename_unreadable += 1
        stats.problem(eml=where, error=f"unreadable filename header: {exc!r}")
        return None, True
    if not raw:
        return None, False
    if "=?" in raw:
        try:
            raw = str(make_header(decode_header(raw)))
            stats.filename_rfc2047 += 1
        except Exception:
            # Unknown charset or malformed word. Keep the literal text rather
            # than dropping the name, and count it so the fallback is visible.
            stats.filename_rfc2047_failed += 1
    return raw, False


def truncate_bytes(s: str, limit: int) -> str:
    encoded = s.encode("utf-8", "replace")
    if len(encoded) <= limit:
        return s
    return encoded[:limit].decode("utf-8", "ignore")


def sanitize_filename(raw: str | None, fallback: str, stats: Stats) -> str:
    r"""Reduce a sender-supplied string to one safe path component.

    Order matters. The basename step runs while the separators are still
    separators: once ILLEGAL has turned `\` into a space, `..\..\x` no longer
    looks like a path and a later split would happily keep the traversal.
    """
    name = raw or ""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        # Lone surrogates survive a surrogateescape header decode. Resolve them
        # to a definite string instead of handing them to the filesystem.
        name = name.encode("utf-8", "replace").decode("utf-8")
        stats.filename_surrogates += 1
    name = unicodedata.normalize("NFC", name)

    # Basename only. One step kills ../.., absolute POSIX paths, Windows drive
    # paths and UNC paths, because every one of them ends in a final component.
    for sep in ("/", "\\"):
        name = name.rsplit(sep, 1)[-1]

    name = ILLEGAL.sub(" ", name)
    # Collapse internal whitespace runs, matching what export_emlx.safe_component
    # already did to the .eml filenames so the two trees read alike. Measured
    # cost on this corpus: 79 names lose a double space (`Innovation  Entre` ->
    # `Innovation Entre`). The manifest keeps original_name, so nothing is lost.
    name = " ".join(name.split())
    name = name.strip(" .")

    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if stem.upper() in WIN_RESERVED:
        stem = "_" + stem
    if stem.startswith("-"):
        stem = "_" + stem  # so no shell or CLI can read the name as a flag
    ext = truncate_bytes(ext, MAX_EXT_BYTES)
    stem = truncate_bytes(stem, MAX_NAME_BYTES - len(ext.encode("utf-8")) - 1)

    out = (f"{stem}.{ext}" if ext else stem).strip(" .")
    if not out or out in (".", ".."):
        stats.filename_synthesized += 1
        return fallback
    if out != (raw or ""):
        stats.filename_rewritten += 1
    return out


def synthetic_name(part: Message, num: str) -> str:
    """A name for a part that declared none, derived from its content type."""
    ctype = part.get_content_type()
    if ctype == "message/rfc822":
        return f"attached-message-{num}.eml"
    kind = "part" if part.get_content_disposition() == "attachment" else "inline"
    return f"{kind}-{num}{mimetypes.guess_extension(ctype) or '.bin'}"


def assert_inside(root: Path, target: Path) -> None:
    """The last line of defence, applied to every path before it is used."""
    r, t = os.path.realpath(root), os.path.realpath(target)
    if t != r and not t.startswith(r + os.sep):
        raise UnsafePath(f"{target} resolves to {t}, outside {r}")


# ---------------------------------------------------------------------------
# Classification and decoding
# ---------------------------------------------------------------------------


def classify(part: Message, filename: str | None) -> tuple[bool, str]:
    """(is_attachment, reason) per the attachment rule in the module docstring."""
    ctype = part.get_content_type()
    if filename:
        return True, "declared-filename"
    if part.get_content_disposition() == "attachment":
        return True, "disposition-attachment"
    if ctype == "message/rfc822":
        return True, "nested-email"
    if part.get_content_maintype() in BINARY_MAINTYPES:
        return True, "unnamed-binary-part"
    return False, f"body-part:{ctype}"


def part_bytes(part: Message) -> tuple[bytes | None, str]:
    """Decoded bytes for one leaf part, or (None, why) when they cannot be had.

    The `message/rfc822` branch is this function's whole reason to exist; see the
    module docstring for what `or b""` does there.
    """
    if part.get_content_type() == "message/rfc822":
        payload = part.get_payload()
        if isinstance(payload, list):
            if len(payload) != 1:
                return None, f"rfc822-payload-list-len-{len(payload)}"
            return payload[0].as_bytes(), "nested-message"
        if isinstance(payload, str):
            # Never parsed into a Message. The raw text still is the message.
            return payload.encode("utf-8", "surrogateescape"), "nested-message-raw"
        return None, f"rfc822-payload-{type(payload).__name__}"
    data = part.get_payload(decode=True)
    return (None, "get_payload-returned-None") if data is None else (data, "decoded")


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------


@dataclass
class Stats:
    messages_seen: int = 0
    messages_parsed: int = 0
    messages_parse_failed: int = 0
    messages_deferred_oversize: int = 0
    messages_without_metadata: int = 0
    entities: int = 0
    containers: int = 0
    leaf_parts: int = 0
    attachments: int = 0
    skipped_body_parts: int = 0
    extracted: int = 0
    unavailable: int = 0
    undecodable: int = 0
    unique_blobs: int = 0
    duplicate_occurrences: int = 0
    bytes_occurrences: int = 0
    bytes_unique: int = 0
    empty_payloads: int = 0
    stub_with_content: int = 0
    links_created: int = 0
    links_deduped: int = 0
    link_failures: int = 0
    filename_rfc2047: int = 0
    filename_rfc2047_failed: int = 0
    filename_unreadable: int = 0
    filename_surrogates: int = 0
    filename_synthesized: int = 0
    filename_rewritten: int = 0
    named_leaf_parts: int = 0  # the search.py --stats definition, recomputed
    inline_attachments: int = 0
    disposition_attachments: int = 0
    linking: bool = True
    by_reason: Counter = field(default_factory=Counter)
    by_excluded_type: Counter = field(default_factory=Counter)
    by_extension: Counter = field(default_factory=Counter)
    problems: list = field(default_factory=list)

    def problem(self, **detail) -> None:
        self.problems.append(detail)

    def check_invariants(self) -> list[str]:
        """The sums that cannot balance if an occurrence went missing.

        Closed partitions rather than spot checks: a later code path that
        forgets to count itself breaks a sum instead of quietly shrinking a
        total. Every leaf part is in exactly one of attachments or
        skipped_body_parts, and every attachment in exactly one of extracted,
        unavailable or undecodable.
        """
        checks = [
            ("parsed + parse_failed + deferred == messages_seen",
             self.messages_parsed + self.messages_parse_failed
             + self.messages_deferred_oversize, self.messages_seen),
            ("containers + leaf_parts == entities",
             self.containers + self.leaf_parts, self.entities),
            ("attachments + skipped_body_parts == leaf_parts",
             self.attachments + self.skipped_body_parts, self.leaf_parts),
            ("extracted + unavailable + undecodable == attachments",
             self.extracted + self.unavailable + self.undecodable, self.attachments),
            ("by_reason total == attachments",
             sum(self.by_reason.values()), self.attachments),
            ("by_excluded_type total == skipped_body_parts",
             sum(self.by_excluded_type.values()), self.skipped_body_parts),
        ]
        if self.linking:
            checks += [
                ("unique_blobs + duplicate_occurrences == extracted",
                 self.unique_blobs + self.duplicate_occurrences, self.extracted),
                (f"links_created + links_deduped == extracted x {len(VIEWS)} views",
                 self.links_created + self.links_deduped,
                 self.extracted * len(VIEWS)),
            ]
        return [f"{label}: {lhs:,} != {rhs:,}"
                for label, lhs, rhs in checks if lhs != rhs]


# ---------------------------------------------------------------------------
# Blob store and views
# ---------------------------------------------------------------------------


def store_blob(dest: Path, sha: str, data: bytes, stats: Stats) -> tuple[Path, bool]:
    """Write the bytes once. Returns (path, True) when this blob is new."""
    blob = dest / "blobs" / sha[:2] / sha[2:4] / sha
    if blob.exists():
        if blob.stat().st_size != len(data):
            raise BlobConflict(f"{blob} is {blob.stat().st_size} bytes but new "
                               f"content with the same hash is {len(data)}")
        return blob, False
    blob.parent.mkdir(parents=True, exist_ok=True)
    tmp = blob.with_name(f".tmp.{os.getpid()}.{sha}")
    tmp.write_bytes(data)
    tmp.replace(blob)  # a torn write can never be mistaken for a blob
    return blob, True


def split_ext(name: str) -> tuple[str, str]:
    stem, dot, ext = name.rpartition(".")
    return (stem, "." + ext) if dot else (name, "")


def link_names(date: str, emlx_id: str, num: str, name: str):
    """Candidate view filenames, most preferred first.

    The plain form keeps the sender's name intact behind a sortable date. The
    second inserts the message id and part number, which are unique per
    occurrence, so it can only ever collide with itself. The counters after it
    exist so an unforeseen case fails loudly rather than looping forever.
    """
    yield f"{date}_{name}"
    yield f"{date}_{emlx_id}.{num}_{name}"
    stem, ext = split_ext(name)
    for i in range(2, 1000):
        yield f"{date}_{emlx_id}.{num}_{stem}-{i}{ext}"


def link_into(view: Path, rel_dir: Path, blob: Path, names, stats: Stats) -> str:
    """Hard-link one occurrence into one view. Returns the path below dest."""
    directory = view / rel_dir
    assert_inside(view, directory)
    directory.mkdir(parents=True, exist_ok=True)
    for candidate in names:
        target = directory / candidate
        assert_inside(view, target)
        # exists() is case-insensitive on a default macOS volume, which is
        # exactly what makes it the right collision test: Report.PDF and
        # report.pdf are one path there, so samefile() decides whether this
        # occurrence is already present or needs a distinct name.
        if not target.exists():
            os.link(blob, target)
            stats.links_created += 1
            return str(target.relative_to(view.parent))
        if target.samefile(blob):
            stats.links_deduped += 1
            return str(target.relative_to(view.parent))
    raise UnsafePath(f"no free name for {blob.name} in {directory}")


def type_bucket(name: str, ctype: str) -> str:
    ext = _NOT_EXT.sub("", split_ext(name)[1].lower())
    if not ext:
        ext = _NOT_EXT.sub("", (mimetypes.guess_extension(ctype) or "").lower())
    return ext[:12] or "_no-extension"


# ---------------------------------------------------------------------------
# Main pass
# ---------------------------------------------------------------------------


def load_metadata(out: Path) -> dict[str, dict]:
    """Per-message metadata keyed by the .eml path relative to --out.

    messages.jsonl rather than index.sqlite, deliberately: it carries every field
    needed (mailbox, date, subject, sender, message id) and reading it cannot
    touch a WAL or shm file beside an archive that is meant to stay read-only.
    index.sqlite has no attachment table to join anyway - a per-message
    `n_attachments` and a joined `attachments` string are all it records, which
    is why that count needed reconciling in the first place.
    """
    path = out / "messages.jsonl"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]
    return {row["eml"]: row for row in rows}


def emlx_id_from_name(stem: str) -> str:
    if stem.startswith("oversize-"):
        tail = stem[len("oversize-"):]
        return tail if tail.isdigit() else "unknown"
    parts = stem.split("_")
    return parts[1] if len(parts) > 1 and parts[1].isdigit() else "unknown"


def unavailable_marker(dest: Path, rel_dir: Path, date: str, name: str, rel: str) -> str:
    """Leave the known gaps visible in the tree, not only in the manifest."""
    view = dest / "by-folder"
    directory = view / rel_dir
    assert_inside(view, directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / truncate_bytes(f"{date}_{name}.UNAVAILABLE.txt", 250)
    assert_inside(view, target)
    target.write_text(
        f"{name}\n\nThis attachment's payload was never stored on this Mac, so it "
        f"is not in the archive.\nThe message kept its X-Apple-Content-Length stub "
        f"so the gap stays findable.\nSource message: {rel}\n", encoding="utf-8")
    return str(target.relative_to(dest))


def process_message(eml: Path, out: Path, dest: Path, meta: dict, stats: Stats,
                    manifest, write: bool) -> None:
    rel = str(eml.relative_to(out))
    stats.messages_seen += 1
    size = eml.stat().st_size
    if size > MAX_MESSAGE_BYTES:
        stats.messages_deferred_oversize += 1
        stats.problem(eml=rel, error=f"deferred: {size:,} bytes over the parse cap")
        return
    try:
        msg = email.message_from_bytes(eml.read_bytes())
    except Exception as exc:
        stats.messages_parse_failed += 1
        stats.problem(eml=rel, error=f"parse: {exc!r}")
        return
    stats.messages_parsed += 1

    row = meta.get(rel)
    if row is None:
        # Never skip. The exporter walks the filesystem and the index is only
        # enrichment, so a file with no metadata row is expected to be possible
        # and must still have its attachments extracted.
        stats.messages_without_metadata += 1
        row = {}
    emlx_id = str(row.get("emlx_id") or emlx_id_from_name(eml.stem))
    date = (row.get("date") or "")[:10] or "0000-00-00"
    year = date[:4]
    mailbox = row.get("mailbox") or str(eml.parent.relative_to(out / "eml"))
    sender = parse_sender(row.get("from") or "")[1]
    folder_dir = Path(*[sanitize_filename(c, "_unnamed", stats)
                        for c in mailbox.split("/")]) / year
    sender_dir = Path(sanitize_filename(sender, "_unknown-sender", stats)) / year

    for num, part in walk_numbered(msg):
        stats.entities += 1
        if part.get_content_maintype() == "multipart":
            stats.containers += 1
            continue
        stats.leaf_parts += 1
        raw_name, unreadable_name = declared_filename(part, stats, rel)
        if raw_name:
            stats.named_leaf_parts += 1
        is_attachment, reason = classify(part, raw_name)
        if unreadable_name and not is_attachment:
            # A name was declared and could not be read. Promote rather than
            # let an unreadable header quietly turn an attachment into a body.
            is_attachment, reason = True, "undecodable-filename-header"
        if not is_attachment:
            stats.skipped_body_parts += 1
            stats.by_excluded_type[part.get_content_type()] += 1
            continue

        stats.attachments += 1
        stats.by_reason[reason] += 1
        ctype = part.get_content_type()
        disposition = part.get_content_disposition() or ""
        if disposition == "attachment":
            stats.disposition_attachments += 1
        else:
            stats.inline_attachments += 1
        name = sanitize_filename(raw_name, synthetic_name(part, num), stats)
        stub = part.get("X-Apple-Content-Length")
        record = {
            "sha256": None, "bytes": None, "name": name, "original_name": raw_name,
            "content_type": ctype, "disposition": disposition, "rule": reason,
            "part": num, "emlx_id": emlx_id, "message_id": row.get("message_id", ""),
            "mailbox": mailbox, "date": row.get("date", ""),
            "subject": row.get("subject", ""), "eml": rel,
            "unavailable": False, "paths": [],
        }

        try:
            data, how = part_bytes(part)
        except Exception as exc:
            data, how = None, f"raise: {exc!r}"

        if data is None or (stub is not None and not data):
            if stub is not None and not data:
                # The payload was never on disk at export time, so the stub was
                # deliberately preserved. Record the gap; do not invent a file.
                stats.unavailable += 1
                record["unavailable"] = True
                record["note"] = f"X-Apple-Content-Length: {str(stub).strip()}"
                if write:
                    record["paths"] = [
                        unavailable_marker(dest, folder_dir, date, name, rel)]
            else:
                stats.undecodable += 1
                record["note"] = how
                stats.problem(eml=rel, part=num, type=ctype, error=f"no bytes: {how}")
            manifest.write(json.dumps(record, ensure_ascii=False) + "\n")
            continue

        if stub is not None:
            # A stub on a part that does hold content means the export filled it
            # and left the header behind. Benign here, but never silent.
            stats.stub_with_content += 1
            stats.problem(eml=rel, part=num, error="stub header on a filled part")

        sha = hashlib.sha256(data).hexdigest()
        stats.extracted += 1
        stats.bytes_occurrences += len(data)
        stats.empty_payloads += not data
        bucket = type_bucket(name, ctype)
        stats.by_extension[bucket] += 1
        record["sha256"], record["bytes"] = sha, len(data)

        if write:
            blob, fresh = store_blob(dest, sha, data, stats)
            if fresh:
                stats.unique_blobs += 1
                stats.bytes_unique += len(data)
            else:
                stats.duplicate_occurrences += 1
            for view, rel_dir in (("by-folder", folder_dir),
                                  ("by-sender", sender_dir),
                                  ("by-type", Path(bucket) / year)):
                try:
                    record["paths"].append(link_into(
                        dest / view, rel_dir, blob,
                        link_names(date, emlx_id, num, name), stats))
                except OSError as exc:
                    stats.link_failures += 1
                    stats.problem(eml=rel, part=num, view=view, error=f"link: {exc!r}")
        manifest.write(json.dumps(record, ensure_ascii=False) + "\n")


README = """# Attachments extracted from {parsed:,} messages

Generated {when} by `extract_attachments.py` from `{source}`.

{extracted:,} attachment occurrences, {unique:,} unique by SHA-256, {unavailable}
unavailable because the payload was never stored on this Mac (look for the
`.UNAVAILABLE.txt` markers under `by-folder/`).

`blobs/` holds one copy of each distinct attachment, named by its SHA-256 with no
extension. Everything else here is a hard link into it, so the three views cost
inodes but no extra bytes. Deleting a view is safe; deleting `blobs/` is not.

| View | Answers |
|---|---|
| `by-folder/<Mailbox>/<Year>/` | "what came into Sent Items in 2024" |
| `by-sender/<address>/<Year>/` | "the contract Meagan sent in 2024" |
| `by-type/<ext>/<Year>/` | "all my PDFs" |

Names are `<date>_<original name>`, with `<message id>.<part>` inserted when two
different attachments would otherwise land on the same name.

`manifest.jsonl` holds one JSON object per occurrence: sha256, bytes, sanitized
and original name, content type, disposition, the rule that classified it,
message id, mailbox, date, subject, source `.eml`, and the view paths. JSONL
rather than SQLite so a run that dies leaves every completed row readable, with
no WAL to recover and nothing to confuse with `index.sqlite`.

`report.json` holds the counts and the accounting sums.

    # every PDF over 1 MB from 2024
    python3 -c "import json; [print(r['bytes'], r['name']) for r in \\
      map(json.loads, open('manifest.jsonl')) if r['bytes'] and \\
      r['bytes'] > 1e6 and r['name'].lower().endswith('.pdf') \\
      and r['date'][:4] == '2024']"
"""


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path,
                    help="the export dir holding eml/ and messages.jsonl")
    ap.add_argument("--dest", type=Path, default=None,
                    help="output dir; defaults to <out>/../20-attachments")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N messages, for a quick trial run")
    ap.add_argument("--dry-run", action="store_true",
                    help="classify and count without writing blobs or links")
    ap.add_argument("--force", action="store_true",
                    help="allow a non-empty --dest (counts otherwise mix two runs)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--self-test", action="store_true",
                    help="run the hostile-filename gate (test_extract_attachments.py)")
    args = ap.parse_args()

    if args.self_test:
        import test_extract_attachments
        return test_extract_attachments.main()
    if args.out is None:
        ap.error("--out is required (or use --self-test)")

    out = args.out.expanduser().resolve()
    dest = (args.dest or out.parent / "20-attachments").expanduser().resolve()
    write = not args.dry_run
    for bad, why in (
        (not (out / "eml").is_dir(), f"no eml/ under {out}"),
        (dest == out or str(dest).startswith(str(out) + os.sep),
         f"--dest {dest} is inside --out; write to a new directory instead"),
        ("01-raw-snapshot" in dest.parts,
         "refusing to write inside the raw snapshot"),
        (write and dest.exists() and any(dest.iterdir()) and not args.force,
         f"{dest} is not empty; use --force to add to it"),
    ):
        if bad:
            print(why, file=sys.stderr)
            return 2

    mimetypes.init()
    meta = load_metadata(out)
    emls = sorted((out / "eml").rglob("*.eml"))
    if args.limit:
        emls = emls[: args.limit]
    stats = Stats(linking=write)
    t0 = time.time()
    if write:
        dest.mkdir(parents=True, exist_ok=True)
    manifest_path = dest / "manifest.jsonl"
    manifest = (manifest_path if write else Path(os.devnull)).open("w", encoding="utf-8")
    try:
        for i, eml in enumerate(emls, 1):
            process_message(eml, out, dest, meta, stats, manifest, write)
            if i % 2500 == 0:
                print(f"  {i:>6,}/{len(emls):,} messages  {stats.extracted:>7,} "
                      f"attachments  [{time.time() - t0:6.1f}s]", flush=True)
    finally:
        manifest.close()

    broken = stats.check_invariants()
    report = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source_export": str(out), "dest": str(dest),
        "elapsed_seconds": round(time.time() - t0, 1),
        **{k: v for k, v in vars(stats).items()
           if not isinstance(v, (Counter, list))},
        "bytes_saved_by_dedup": stats.bytes_occurrences - stats.bytes_unique,
        "attachments_by_rule": dict(stats.by_reason.most_common()),
        "excluded_by_content_type": dict(stats.by_excluded_type.most_common()),
        "extracted_by_extension": dict(stats.by_extension.most_common(40)),
        "invariants_ok": not broken,
        "invariants_broken": broken,
        "problems": stats.problems[:200],
    }
    if write:
        (dest / "report.json").write_text(json.dumps(report, indent=2))
        (dest / "README.md").write_text(README.format(
            parsed=stats.messages_parsed, when=report["generated"], source=out,
            extracted=stats.extracted, unique=stats.unique_blobs,
            unavailable=stats.unavailable))

    print(json.dumps(report if args.json
                     else {k: v for k, v in report.items() if k != "problems"},
                     indent=2))
    print(f"\nmanifest    : {manifest_path}")
    print(f"unique blobs: {stats.unique_blobs:,}  {stats.bytes_unique / 1e9:.2f} GB")
    print(f"occurrences : {stats.extracted:,}  "
          f"{stats.bytes_occurrences / 1e9:.2f} GB before dedup "
          f"({report['bytes_saved_by_dedup'] / 1e9:.2f} GB saved)")

    ok = not broken and not stats.undecodable and not stats.link_failures
    if broken:
        print("\nACCOUNTING BROKEN - the report does not add up:", file=sys.stderr)
        for line in broken:
            print(f"  {line}", file=sys.stderr)
    if stats.undecodable:
        print(f"\n{stats.undecodable} attachment(s) yielded no bytes; see problems",
              file=sys.stderr)
    if stats.link_failures:
        print(f"\n{stats.link_failures} link failure(s); see problems", file=sys.stderr)
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
