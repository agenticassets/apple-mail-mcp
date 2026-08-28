# Pre-draft verification (canonical)

Canonical source: `plugin/skills/references/pre-draft-verification.md`. After edits, run `python3 tools/validators/sync_skill_references.py` to refresh per-skill copies under `*/references/`.

Load **`email-drafting`** for compose tool selection, native reply defaults, and post-draft checks. This reference is the single source for **already-replied** and **pre-draft thread** rules. For **processing order** (newest first, small batches), see [`recent-first-triage.md`](recent-first-triage.md).

## Already-replied safeguard

Discovery rows self-report reply/draft state. `was_replied_to` remains the backward-compatible raw Mail flag and `mail_was_replied_to` names that raw evidence explicitly. JSON primary reads automatically add bounded Sent evidence; `get_needs_response` does so only with `check_already_replied=True`. `has_sent_reply` is true for an exact Sent In-Reply-To/References match, false only after a complete successful nonmatch, and null when evidence is skipped or incomplete. `reply_state` combines the two: true when either source matches, false only when complete evidence says neither, and null when unknown. `has_draft` remains true / false / null. Structured responses include `sent_reply_scan` and `draft_scan` status objects. This MCP state does not set Mail's answered flag or guarantee a reply arrow in Mail's UI.

1. **Discovery is safe by default:** `get_needs_response(...)` excludes rows where Mail's native reply flag is true or `has_draft=true`; pass `check_already_replied=True` to add bounded Sent evidence, and an unknown result fails open. It reports `skipped_replied_count` / `skipped_drafted_count`. JSON `list_inbox_emails` and `search_emails` expose the composite automatically; `exclude_replied=True` consumes it.
2. **Has-draft check (primary):** `has_draft` is three-state. `true` means a matching draft already exists (trustworthy even from a truncated scan), so never draft a reply. `false` means the scan ran and completed (not truncated) and found no matching draft. `null` means unknown: the scan was skipped or errored, OR the bounded scan (default cap 50 drafts per account) was truncated and this row did not match within the scanned window; check `draft_scan.status` plus the new `draft_scan.truncated` / `draft_scan.total` fields. On `null`, do not read it as "no draft exists": fall back to a manual check with `manage_drafts(action="find", ...)` or the "Missed-replies queue" workflow in `email-drafting`.
3. **Replied check:** `was_replied_to=true` is Mail's native reply flag; when it is true and bullet 2 shows no matching draft, do not draft: abort and report which reply already covers it. **Thread check (manual fallback, only when row fields are absent):** `get_email_thread(account=..., message_id=...)` then search Sent for operator addresses. In that manual fallback, **abort only when the operator sent a message after the latest inbound** (this timing caveat governs the manual Sent-scan fallback, not Mail's native `was_replied_to` flag); an older operator reply before the newest inbound does not count as already handled.
4. **Co-founder / teammate coverage (Agentic Assets commercial mail):** A co-founder or teammate reply to an external partner does **not** close the thread for the operator on Tier-1 partner, client, or investor relationships. Still draft a short courtesy acknowledgment unless the operator already replied in Sent after the inbound.
5. **Courtesy default:** When uncertain on warm human mail, default to a 1–3 sentence courtesy draft. Skipping requires a specific reason, evidenced primarily by the new fields: operator already replied (`was_replied_to=true` / thread check), a verified good draft already exists (`has_draft=true` / `verify_draft`), clear automated noise, or cold PR.
6. **Override:** `get_needs_response` is the only tool that excludes replied/drafted rows by default; override it with `include_already_replied=True` / `include_drafted=True` only when the user says "include already-replied", "include drafted", or "redraft". `list_inbox_emails` / `search_emails` return every row by default (no exclusion), so check each row's `was_replied_to` / `has_draft` before drafting; use `exclude_replied=True` / `exclude_drafted=True` as opt-in narrowing when you want the tool to filter for you instead.

**`include_draft_state`:** all 8 annotated tools accept `include_draft_state: bool = True`. Default `True` runs the bounded Drafts snapshot that produces `has_draft`; pass `False` only for the bare-fastest listing on a huge account, in which case `has_draft` comes back `null` and nothing is excluded for draft state, so fall back to a manual draft check (bullet 2).

## Reply workflow (ID-first)

```text
# 1. Discover (bounded): was_replied_to/has_draft on each row are the primary replied-state check
results = search_emails(..., output_format="json")  # or list_inbox_emails
message_id = results["items"][0]["message_id"]      # list_inbox_emails uses ["emails"]

# 2. Thread check (recommended context; run when row fields are absent, has_draft=null, or extra certainty is needed)
get_email_thread(account=..., message_id=message_id, output_format="json")

# 3. Draft in-thread (default native_format=True; needs Mail focus + Accessibility)
reply_to_email(message_id=message_id, reply_body="...", mode="draft")
```

- Never pass `subject_keyword` to `reply_to_email` or `forward_email` (`TARGET_SELECTOR_DEPRECATED`).
- Never use `compose_email`, `create_rich_email_draft`, or `manage_drafts(action="create")` for thread replies. `manage_drafts(action="create", in_reply_to=...)` refuses up front with `CREATE_CANNOT_THREAD` (create cannot set In-Reply-To/References); use `reply_to_email(message_id=...)` to thread, or `manage_drafts(action="find", in_reply_to=...)` to locate an already-saved reply draft.
- On `REPLY_WINDOW_FOCUS_FAILED`, `REPLY_SUBJECT_GUARD_MISMATCH` (window opened but its title never matched), or `REPLY_BODY_TYPING_INTERRUPTED` (focus lost partway through typing; the partial compose window was discarded, nothing partial left behind): log `draft_deferred` in action_log with intended one-sentence body summary, continue other courtesy drafts in the batch, and report the blocker. Retry with Mail visible when practical. Do not switch to `native_format=false`; it is gated (`WINDOWLESS_FALLBACK_DISABLED`) and reserved for deliberate headless/CI via `allow_windowless_fallback=True`, which agents must never set.
- `reply_to_email` types the body in small chunks and verifies the FULL saved body above the quote (case-sensitive) before returning success. It retries once (delete + retype) on a placement mismatch, but only when Mail exposed the compose draft id and the verifier resolved the same id (it never deletes a draft it cannot prove it created; on Exchange the id often drifts, so expect the no-retype path). If the body still does not match, it returns `REPLY_BODY_MISMATCH` with an `artifact_identity_verified` flag in the remediation. When that flag is `true` the named artifact is one this call created: inspect it with `verify_draft(draft_id=...)`, then get the user's explicit confirmation to delete that specific draft before retrying, so no truncated or miscased duplicate is left behind. Deleting a draft always needs that confirmation, even for a draft this call just created. The delete is the guarded `manage_drafts(action='delete', draft_id=..., expected_in_reply_to=..., expected_subject=..., expected_to=...)`, which **previews only** (`manage_drafts` defaults to `dry_run=True`); read the preview back, then repeat the identical call with `dry_run=False` to delete. When it is `false` the id is reported as `suspect_artifact_message_id` and came from a subject scan, not from this compose: on Exchange the same subject can match a reply draft you wrote in the thread earlier, so inspect only and leave any deletion to the user.
- Known limitation (2026-07-10 live finding): accented or composed characters can corrupt during native typing (observed "Renée" saved as "Renae"; smart quotes, em dashes, and ellipsis type fine). The verifier fails closed with `REPLY_BODY_MISMATCH` rather than saving silently, but until the typing-fidelity follow-up ships, prefer ASCII spellings in `reply_body` (for example "Renee") and mention the substitution to the user when a name is affected.
- `expected_body_contains` in `verify_draft` matches only above the first quote boundary (a needle found only inside the quoted original reports `body_needle_only_in_quote: true` and fails), and its `body_preview` caps at 5000 characters, so for very long replies verify a needle from the body prefix, not the tail.

## JSON key reminder

| Discovery tool | Id list key |
|----------------|-------------|
| `search_emails(output_format="json")` | `results["items"]` |
| `list_inbox_emails(output_format="json")` | `results["emails"]` |

Every row in both also carries `was_replied_to` and `has_draft`; the response carries a top-level `draft_scan` object: `status` (`ok` / `error` / `skipped`), `scanned`, `total`, `truncated`, and `accounts[]` (each with `account`, `status`, `scanned`, `total`, `truncated`). A `truncated` scan (mailbox `total` above `scanned`, or `total` unknown) is why a nonmatching row can report `has_draft=null` rather than `false`. Text (non-JSON) output shows the same signal inline via `[REPLIED]` / `[HAS DRAFT]` markers; JSON remains the recommended format for programmatic checks.
