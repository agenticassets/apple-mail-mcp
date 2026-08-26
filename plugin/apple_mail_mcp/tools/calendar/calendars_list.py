"""``list_calendars`` tool: enumerate calendars with writability and defaults."""

from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.calendar_core import resolve_calendar_selector
from apple_mail_mcp.core import AppleScriptTimeout, inject_preferences
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import calendar as calendar_tools
from apple_mail_mcp.tools.calendar.helpers import error_json, finish, output_format_error, timeout_error


def _render_calendars_text(payload: dict[str, Any]) -> str:
    lines = [f"{len(payload['calendars'])} calendar(s) [engine: {payload['engine']}]"]
    for cal in payload["calendars"]:
        writable = "writable" if cal.get("writable") else "read-only"
        default = " (default)" if cal.get("is_default") else ""
        lines.append(f"- {cal.get('name')}{default}  [{writable}]  id: {cal.get('calendar_id')}")
    for err in payload.get("calendar_errors", []):
        lines.append(f"! {err}")
    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Calendar List")
@inject_preferences
def list_calendars(output_format: str = "json", timeout: int | None = None) -> str:
    """
    List every calendar in Calendar.app with id, writability, and defaults.

    Start calendar work here: the returned names feed the ``calendar`` /
    ``calendars`` parameters on every other calendar tool, and the
    ``writable`` flag shows which calendars accept writes (subscribed and
    delegated calendars do not).

    Response fields: ``calendars`` (calendar_id, id_kind, name, writable,
    description, is_default), ``default_calendar`` (from the
    ``DEFAULT_CALENDAR`` environment variable, else the engine default when
    the EventKit fast path is active), ``engine`` (``applescript`` or
    ``eventkit``), and ``eventkit_available`` (fast-path diagnostic with a
    reason such as ``dependency_missing`` or ``not_determined``).

    Args:
        output_format: "json" (default) or "text".
        timeout: Optional AppleScript timeout in seconds (default: 120s).

    Returns:
        JSON payload (or text summary) of all calendars.
    """
    fmt_error = output_format_error(output_format)
    if fmt_error:
        return fmt_error
    try:
        engine = calendar_tools.get_engine()
        calendars, errors = engine.list_calendars(timeout=timeout)
    except AppleScriptTimeout:
        return timeout_error("list_calendars", timeout)
    except ToolError as exc:
        return error_json(exc)

    default_id_getter = getattr(engine, "default_calendar_id", None)
    engine_default_id = default_id_getter() if callable(default_id_getter) else None
    default_calendar = _server.DEFAULT_CALENDAR or engine.default_calendar_name()
    default_calendar_id = engine_default_id
    if _server.DEFAULT_CALENDAR:
        try:
            default_calendar_id = str(resolve_calendar_selector(_server.DEFAULT_CALENDAR, calendars)["calendar_id"])
        except ToolError:
            default_calendar_id = None
    elif default_calendar and not default_calendar_id:
        try:
            default_calendar_id = str(resolve_calendar_selector(default_calendar, calendars)["calendar_id"])
        except ToolError:
            default_calendar_id = None
    for cal in calendars:
        cal["is_default"] = bool(default_calendar_id) and cal.get("calendar_id") == default_calendar_id
    available, reason = calendar_tools.eventkit_status()
    payload: dict[str, Any] = {
        "calendars": calendars,
        "default_calendar": default_calendar,
        "engine": engine.name,
        "eventkit_available": {"available": available, "reason": reason},
        "calendar_errors": errors,
    }
    return finish(payload, output_format, _render_calendars_text)
