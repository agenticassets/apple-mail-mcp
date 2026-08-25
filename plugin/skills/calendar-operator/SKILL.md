---
name: calendar-operator
description: This skill should be used when the user asks "what's on my calendar", "am I free Thursday", "add an event", "block time for this", "clean up my test calendar", or "which calendar tool should I use". Covers bounded reads, timezone handling, safe event creation, ID-first updates and deletes, and calendar management with list_calendars, list_events, create_event, delete_events, and manage_calendars. Do NOT use for coordinating meetings with other people or invitation workflows (see meeting-scheduler), and do NOT use for email work (see apple-mail-operator).
---

# Calendar Operator

Operate the Apple Calendar tool surface safely: bounded reads, timezone-correct
writes, ID-first mutations, and dry-run-first deletes.

## When To Use This Skill

| Signal | Skill |
|--------|-------|
| Read my schedule, add/edit/delete my own events, calendar cleanup | **calendar-operator** (this skill) |
| Find a time with other people, invitations, cross-timezone meetings | `meeting-scheduler` |
| Anything email (inbox, drafts, replies) | `apple-mail-operator` and siblings |

## Bootstrap

1. Call `list_calendars()` first. It returns every calendar with `calendar_id`,
   `writable`, `is_default`, the active `engine` (`applescript` or `eventkit`), and
   `eventkit_available` diagnostics.
2. Note the writable calendars. Writes to subscribed or delegated calendars return
   `CALENDAR_READ_ONLY`.
3. `DEFAULT_CALENDAR` (environment variable) sets the create target only; reads always
   scope explicitly or fan out.

## Bounded reads (the core doctrine)

Every event read requires a window and every window is capped. This differs from the
mail tools in one important way: with no calendar-scoping argument (`calendar` or
`calendars` on `list_events`, `calendars` on `check_availability`), both fan out
across every calendar (capped at 20 plus a wall-clock budget) instead of erroring
like mail's account scoping. Prefer explicit scoping:

- `list_events(days_ahead=7)` for the upcoming week across calendars.
- `list_events(calendar="Work", days_ahead=1, days_back=0)` for today on one calendar.
- `list_events(start="2026-08-01", end="2026-08-31", query="review")` for a bounded search.
- `list_events(calendar="Work", start="2026-01-01", end="2026-03-31", participant_query="person@example.com")` for historical meetings with a person. Page with `offset`, then use `get_events_by_id` only for exact relevant ids.
- Page with `offset` when `truncated` is true; never widen the window instead.

`participant_query` matches attendee display names and email addresses without returning
participant metadata in the list response. On the EventKit fast path it also matches
organizer name and email; Calendar.app scripting exposes attendees but not an organizer
field. Combine it with `query` when a title term is known; both filters must match. Keep
historical searches calendar-scoped and in 30-90 day windows, then retrieve exact ids for
full notes and attendee metadata.

On `UNBOUNDED_CALENDAR_SCAN`, supply an explicit non-empty window; on
`CALENDAR_WINDOW_TOO_WIDE`, narrow the window; on
`CALENDAR_WINDOW_TOO_DENSE`, narrow further or scope to one calendar. Check
`calendar_errors` and `budget_exhausted` on every fan-out response; partial results are
normal on slow stores.

Recurring coverage (AppleScript engine): `list_events` and `check_availability` project
recurring occurrences from masters whose start date falls within the last 400 days, so a
standing series created earlier can be missing with no per-row error. When the AppleScript
recurring pass runs, the response carries `recurring_lookback_days` and a
`recurring_coverage_note`; if a long-running series looks absent, tell the user it may
predate the horizon and that the EventKit fast path expands natively. See
`references/calendar-safety-limits.md`.

## Timezones

Pass `timezone` (IANA, for example `America/Chicago`) whenever the user's zone matters,
and read `resolved_timezone` back. Events return `start`/`end` in the requested zone
plus `start_utc`/`end_utc`. Naive datetimes are interpreted in the requested zone;
offset-aware ISO 8601 strings need no `timezone` parameter. The event lands on the
correct absolute instant regardless of the Mac's own zone.

## Creating and editing events

- `create_event(title=..., start=..., duration_minutes=60)` or an explicit `end`.
  All-day: `all_day=True` (may omit end for one day). Alarms:
  `alarms_minutes_before=[10, 60]` (max 5). Recurrence: allowlisted RRULE strings such
  as `FREQ=WEEKLY;BYDAY=MO,WE,FR`.
- Conflict detection is on by default (`on_conflict="warn"` reports `conflicts`;
  `"block"` refuses with `EVENT_CONFLICT`; `"allow"` skips).
- **Search then mutate by id**: `update_event` and `delete_events` take exact
  `event_id`s from a prior `list_events`/`get_events_by_id` call. There are no fuzzy
  destructive selectors, by design. Pass `calendar` as a lookup hint to avoid fan-out.
- `update_event` is PATCH-style: only the fields you pass change. On `EVENT_NOT_FOUND`,
  widen `lookup_days_back`/`lookup_days_ahead` or pass the right calendar.
- Recurring targets require `span='all_occurrences'`. `update_event` applies the
  change to the whole series; `delete_events` cannot remove a whole series
  (Calendar.app scripting deletes only individual occurrences), so it verifies
  after deleting and returns `RECURRING_DELETE_INCOMPLETE` with the surviving dates
  when the series is not fully gone. `this_occurrence`/`future_occurrences` return
  `RECURRING_SPAN_UNSUPPORTED`.
- `batch_create_events` creates up to 25 one-off blocking events in one call
  (no attendees, no recurrence in items); use `dry_run=True` first for bulk plans.

## Destructive red lines

| Action | Rule |
|--------|------|
| `delete_events` | Always run the default `dry_run=True` preview and show it before `dry_run=False`; exact ids only; one unresolved id aborts everything |
| Recurring deletes | Calendar.app scripting cannot remove a whole series; `delete_events` verifies after running and returns `RECURRING_DELETE_INCOMPLETE` with surviving dates. For a reliable series delete, direct the user to Calendar.app |
| `manage_calendars(action="delete")` | Three steps: dry-run preview with `event_count`, then `confirm_delete_calendar=True`, plus `force_nonempty=True` when events exist; exact name or `calendar_id` only |
| Draft-safe mode | Deletes return `CALENDAR_DELETE_BLOCKED`; never suggest the `CALENDAR_ALLOW_DESTRUCTIVE` env unlock, that is an operator decision at launch |
| Bulk anything | Respect `TOO_MANY_DELETES` / `BATCH_TOO_LARGE`; split deliberately, never loop to evade caps |

## Modes: what the flags block here

The mode flags gate calendars harder than mail: `--read-only` removes every calendar
write tool; `--draft-safe` additionally blocks deletes and attendee sends while still
allowing personal create/update/rename (reversible, no third parties). For mail tools
the same flags block only the send paths. Read the full matrix in
`references/calendar-safety-limits.md`.

## Troubleshooting (TCC and performance)

- **First call hangs then times out**: a pending Automation consent prompt presents as
  a silent hang. Have the user open System Settings > Privacy & Security > Automation,
  enable Calendar under the host app, and retry. `CALENDAR_ACCESS_DENIED` remediation
  names the exact pane.
- **Slow reads**: Calendar.app AppleScript scans cost tracks total store size
  (community benchmarks: roughly 61 to 112 seconds on modest calendars). The EventKit
  fast path is roughly 3000x faster and activates automatically when
  `pip install 'mcp-apple-mail[eventkit]'` is installed and Calendars full access is
  already granted; check `eventkit_available` in `list_calendars`. A human can grant it
  once by running `apple-mail calendar-grant` from Terminal. Never attempt to trigger
  that consent prompt from a tool call.
- **Calendar.app may launch** when AppleScript runs; this is normal.
- Call one calendar tool at a time; calls serialize behind the same lock as the mail
  tools.

## Platform gaps (be direct with users)

- Invitations: attendee attachment never guarantees delivery
  (`invitation_delivery: "platform_dependent"`); see `meeting-scheduler` for the
  reliable `.ics` drafting path.
- RSVP: `respond_to_invitation` always returns `CALENDAR_RSVP_UNSUPPORTED`; point the
  user to Calendar.app or their mail client.

## Additional Resources

- `references/calendar-safety-limits.md`: bounds, mode matrix, error recovery, platform limitations.
