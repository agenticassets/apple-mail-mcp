# Reply Body Insertion Failure In Threaded Drafts

Date: 2026-06-18

## Summary

`reply_to_email` can create a threaded Apple Mail draft and report that a draft was created, but the saved draft contains only the Mail signature and the quoted original message. The requested reply body is missing. This creates unsafe false progress for email agents because the draft appears threaded and addressed correctly but is not sendable.

## Live Reproduction

Environment:

- Repo user workflow: Agentic-Inbox triage
- Account: `TU - Cayman`
- Source message id: `83957`
- Source subject: `QM 4862 Independent Study`
- Source sender: `recipient@example.com`
- Tool: `mcp__apple_mail.reply_to_email`
- Mode: `draft`

First call:

```json
{
  "account": "TU - Cayman",
  "message_id": "83957",
  "reply_to_all": false,
  "mode": "draft",
  "include_signature": true
}
```

Result:

```text
Error: Reply draft was created, but Mail did not verify it in the newest Drafts window. No email was sent. Please check Mail Drafts and retry after Mail finishes saving.
```

Draft verification:

- Draft id: `84050`
- To: `recipient@example.com`
- Subject: `Re: QM 4862 Independent Study`
- Threading headers present: `in_reply_to` and `references`
- `has_quoted_original`: true
- Body problem: visible content starts with the default TU signature and quoted original. The intended reply body is absent.

Second call used the same source message and body with `include_signature=false`.

Result:

```text
Error: Reply draft was created, but Mail did not verify it in the newest Drafts window. No email was sent. Please check Mail Drafts and retry after Mail finishes saving.
```

Draft verification:

- Draft id: `84053`
- To: `recipient@example.com`
- Subject: `Re: QM 4862 Independent Study`
- Body problem: same signature or quote-only failure, intended reply body absent.

## Intended Reply Body That Was Lost

```text
Hi Bryson,

That sounds good. The mortgage licensure track makes sense given your interest in underwriting.

For OnCourse, yes, send Angie a short note saying you would like to enroll in the 20-Hour SAFE Comprehensive course and cc me. I do not think anything needs to come from me first, but I can jump in if she needs confirmation.

For meetings, let’s start with a recurring weekly Outlook invite. Virtual is easiest unless there is a specific reason to meet in person. Email is best for course questions, and we can use the weekly meeting for anything more detailed.

Best,
Dr. Seagraves
```

## Related Prior Symptoms

This is not isolated. Similar body-drop or signature-only artifacts have appeared in prior live reply attempts:

- Jeff Fisher reply attempts produced signature-only artifacts around ids `83204` and `83205`.
- Earlier live triage logs noted reply drafts that saved but required exact-id Drafts verification because body insertion could silently fail.
- Attachment plus reply workflows have also shown race-like behavior where the body and attachment path do not reliably compose into one valid draft.

## Expected Behavior

For `reply_to_email(message_id=..., mode="draft")`:

1. Mail creates a native threaded reply draft.
2. The requested `reply_body` is inserted above the quoted original.
3. Signature behavior is deterministic:
   - `include_signature=true` may keep or apply the configured signature.
   - `include_signature=false` should not cause the reply body to be skipped.
4. The tool returns success only after verifying that the saved draft includes the inserted reply body.
5. If insertion fails, the tool should either delete or clearly identify the artifact draft id and return a structured failure.

## Actual Behavior

The draft can be threaded and addressed correctly but missing the reply body. The returned error says verification failed, but it still leaves artifacts in Drafts. Agents must stop manually to avoid duplicate broken drafts.

## Likely Area To Inspect

Start in:

- `plugin/apple_mail_mcp/tools/compose.py`
- Any AppleScript builder used by `reply_to_email`
- Draft verification code that checks newest Drafts after saving

Superseded implementation note, 2026-06-19: the fix path should not depend on
focus/selection or UI paste. Use Mail's scripting dictionary contract instead:
`reply` returns an `outgoing message`, `content` is writable on that outgoing
message, and `message signature` can be set to `missing value` without skipping
body insertion. Verification should assert that a unique body sentinel or
normalized expected body text is present above the quoted original in the saved
Drafts artifact.

## Suggested Fix Contract

Add a regression test around the reply AppleScript builder and verification semantics:

- Ensure `reply_body` insertion happens before save.
- Ensure `include_signature=false` cannot skip body insertion.
- Ensure a body-missing draft causes a structured error with the artifact draft id.
- Prefer exact-id Drafts verification over subject-only verification.

Add or update a live smoke procedure that can be run against a harmless local test message:

1. Create or locate a test inbound message.
2. Call `reply_to_email(..., mode="draft")` with a unique sentinel in `reply_body`.
3. Fetch the saved draft by exact id or newest Drafts lookup.
4. Assert the sentinel is present above the quoted original.
5. Clean up the test draft by exact id.

## Operator Workaround Until Fixed

Do not trust `reply_to_email` success or partial success on TU threaded replies unless exact Drafts verification proves the reply body is present. If a good and bad draft share a subject, do not delete by subject. Discard exact artifact ids manually.
