"""``manage_calendars`` tool: create/rename/delete calendars, delete triple-gated."""

from typing import Any

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.core import AppleScriptTimeout, inject_preferences
from apple_mail_mcp.server import DESTRUCTIVE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import calendar as calendar_tools
from apple_mail_mcp.tools.calendar.helpers import (
    calendar_delete_blocked,
    calendar_write_blocked,
    error_json,
    finish,
    output_format_error,
    timeout_error,
)


def _render_manage_text(payload: dict[str, Any]) -> str:
    """Compact human-readable rendering for manage_calendars payloads."""
    action = payload.get("action")
    calendar = payload.get("calendar")
    if action == "create":
        return f"created calendar {calendar!r}"
    if action == "rename":
        return f"renamed {payload.get('from')!r} to {calendar!r}"
    if payload.get("dry_run"):
        return (
            f"dry-run: calendar {calendar!r} holds {payload.get('event_count')} event(s). "
            f"{payload.get('next_step', '')}".strip()
        )
    return f"deleted calendar {calendar!r} ({payload.get('event_count')} event(s))"


def _resolve_exact_target(
    name: str | None,
    calendar_id: str | None,
    *,
    timeout: int | None,
) -> dict[str, Any]:
    """Exact-only selector for rename/delete, retaining the stable ID."""
    engine = calendar_tools.get_engine()
    calendars, _errors = engine.list_calendars(timeout=timeout)
    if calendar_id:
        for cal in calendars:
            if str(cal.get("calendar_id")) == calendar_id:
                return cal
        raise ToolError(
            code="CALENDAR_NOT_FOUND",
            message=f"No calendar has calendar_id {calendar_id!r}. Call list_calendars first.",
        )
    if name:
        matches = [cal for cal in calendars if str(cal.get("name")) == name]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ToolError(
                code="AMBIGUOUS_CALENDAR_SELECTOR",
                message=f"Calendar name {name!r} identifies {len(matches)} calendars.",
                remediation={
                    "candidates": [
                        {"name": str(cal.get("name")), "calendar_id": str(cal.get("calendar_id"))} for cal in matches
                    ],
                    "preferred": "Pass calendar_id from list_calendars.",
                },
            )
        raise ToolError(
            code="CALENDAR_NOT_FOUND",
            message=(
                f"No calendar is named exactly {name!r}. Rename and delete accept exact names or "
                "calendar_id only; fuzzy matching is disabled for destructive targets."
            ),
            remediation={"candidates": [str(c.get("name")) for c in calendars][:10]},
        )
    raise ToolError(
        code="CALENDAR_NOT_FOUND",
        message="Pass name (exact) or calendar_id for rename/delete.",
    )


@mcp.tool(annotations=DESTRUCTIVE_TOOL_ANNOTATIONS, title="Manage Calendars")
@inject_preferences
def manage_calendars(
    action: str = "create",
    name: str | None = None,
    calendar_id: str | None = None,
    new_name: str | None = None,
    dry_run: bool = True,
    confirm_delete_calendar: bool = False,
    force_nonempty: bool = False,
    output_format: str = "json",
    timeout: int | None = None,
) -> str:
    """
    Create, rename, or delete calendars (one multiplexed tool).

    ``create`` and ``rename`` are ordinary writes (allowed under
    --draft-safe, blocked under --read-only). ``delete`` cascades to every
    event on the calendar, so it is the most heavily gated operation in the
    calendar surface: first a ``dry_run=True`` preview (the default) reports
    the exact event count; then ``dry_run=False`` requires
    ``confirm_delete_calendar=True``, plus ``force_nonempty=True`` when the
    count is non-zero. Rename and delete prefer the opaque ``calendar_id``
    returned by ``list_calendars``; an exact current ``name`` is accepted only
    when unique, so duplicate names fail safely. Delete is blocked under --draft-safe
    (``CALENDAR_DELETE_BLOCKED``) unless the operator launched with
    ``CALENDAR_ALLOW_DESTRUCTIVE=1``.

    Args:
        action: "create", "rename", or "delete".
        name: create: the new calendar name; rename/delete: exact, unique
            existing name.
        calendar_id: rename/delete: preferred exact selector from list_calendars.
        new_name: rename: the new name.
        dry_run: delete only: True (default) previews the cascade event count.
        confirm_delete_calendar: Required True for a real delete.
        force_nonempty: Required True to delete a calendar that still has events.
        output_format: "json" (default) or "text".
        timeout: Optional AppleScript timeout in seconds.

    Returns:
        JSON payload describing the action taken or previewed.
    """
    fmt_error = output_format_error(output_format)
    if fmt_error:
        return fmt_error
    if action not in ("create", "rename", "delete"):
        return f"Error: Invalid action {action!r}. Use: create, rename, delete."

    try:
        if action == "create":
            blocked = calendar_write_blocked("manage_calendars")
            if blocked:
                return blocked
            if not name or not name.strip():
                return "Error: name is required for action='create'."
            wanted = name.strip()
            existing = calendar_tools.list_calendar_names(timeout=timeout)
            if wanted in existing:
                raise ToolError(
                    code="CALENDAR_ALREADY_EXISTS",
                    message=f"A calendar named {wanted!r} already exists.",
                )
            created = calendar_tools.get_write_engine().create_calendar(name=wanted, timeout=timeout)
            return finish(
                {"action": "create", "created": True, "calendar": created}, output_format, _render_manage_text
            )

        if action == "rename":
            blocked = calendar_write_blocked("manage_calendars")
            if blocked:
                return blocked
            if not new_name or not new_name.strip():
                return "Error: new_name is required for action='rename'."
            target = _resolve_exact_target(name, calendar_id, timeout=timeout)
            wanted = new_name.strip()
            existing = calendar_tools.list_calendar_names(timeout=timeout)
            if wanted in existing:
                raise ToolError(
                    code="CALENDAR_ALREADY_EXISTS",
                    message=f"A calendar named {wanted!r} already exists; pick another new_name.",
                )
            renamed = calendar_tools.get_write_engine().rename_calendar(
                calendar_id=str(target["calendar_id"]),
                new_name=wanted,
                timeout=timeout,
                selector_kind=str(target.get("id_kind") or "name"),
            )
            return finish(
                {
                    "action": "rename",
                    "renamed": True,
                    "from": target["name"],
                    "calendar": renamed,
                    "calendar_id": target["calendar_id"],
                },
                output_format,
                _render_manage_text,
            )

        # action == "delete"
        blocked = calendar_delete_blocked("manage_calendars")
        if blocked:
            return blocked
        target = _resolve_exact_target(name, calendar_id, timeout=timeout)
        write_engine = calendar_tools.get_write_engine()
        event_count = write_engine.count_events(
            str(target["calendar_id"]),
            timeout=timeout,
            selector_kind=str(target.get("id_kind") or "name"),
        )
        if dry_run:
            payload_preview: dict[str, Any] = {
                "action": "delete",
                "dry_run": True,
                "deleted": False,
                "calendar": target["name"],
                "calendar_id": target["calendar_id"],
                "event_count": event_count,
                "next_step": (
                    "Re-run with dry_run=False and confirm_delete_calendar=True"
                    + (" and force_nonempty=True (the calendar is not empty)" if event_count else "")
                    + " to delete this calendar and every event on it."
                ),
            }
            return finish(payload_preview, output_format, _render_manage_text)
        if not confirm_delete_calendar:
            return serialize_tool_error(
                ToolError(
                    code="CALENDAR_CONFIRMATION_REQUIRED",
                    message=(
                        f"Deleting calendar {target['name']!r} removes it and all {event_count} event(s) on it. "
                        "Pass confirm_delete_calendar=True after reviewing the dry_run preview."
                    ),
                )
            )
        if event_count > 0 and not force_nonempty:
            return serialize_tool_error(
                ToolError(
                    code="CALENDAR_CONFIRMATION_REQUIRED",
                    message=(
                        f"Calendar {target['name']!r} still holds {event_count} event(s); pass force_nonempty=True "
                        "to confirm the cascade delete."
                    ),
                )
            )
        deleted_name = write_engine.delete_calendar(
            calendar_id=str(target["calendar_id"]),
            timeout=timeout,
            selector_kind=str(target.get("id_kind") or "name"),
        )
        return finish(
            {
                "action": "delete",
                "dry_run": False,
                "deleted": True,
                "calendar": deleted_name,
                "calendar_id": target["calendar_id"],
                "event_count": event_count,
            },
            output_format,
            _render_manage_text,
        )
    except AppleScriptTimeout:
        return timeout_error("manage_calendars", timeout)
    except ToolError as exc:
        return error_json(exc)
    except Exception as exc:
        return f"Error: {exc}"
