"""``respond_to_invitation`` tool: documented-refusal shim (no RSVP API exists).

The ``full_inbox_export`` precedent: the capability stays discoverable and
the platform limitation is machine-readable. No engine call ever runs.
"""

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.core import inject_preferences
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Respond to Invitation")
@inject_preferences
def respond_to_invitation(
    event_id: str | None = None,
    response: str | None = None,
    output_format: str = "json",
) -> str:
    """
    Unsupported: macOS exposes no public API to RSVP to a calendar invitation.

    ``EKParticipant.participantStatus`` and the whole EventKit attendees array
    are read-only, and Calendar.app's scripting dictionary exposes
    ``participation status`` as readable with no verb to change your own
    response or emit the RSVP email (verified against the SDK headers and
    Apple documentation). This tool performs no engine call and always
    returns a structured ``CALENDAR_RSVP_UNSUPPORTED`` refusal so agents
    learn the limitation instead of retrying.

    Args:
        event_id: Unused. Retained for a discoverable schema.
        response: Unused ("accept" | "decline" | "tentative").
        output_format: Unused. Errors always return the JSON envelope.

    Returns:
        JSON-encoded structured error with ``code="CALENDAR_RSVP_UNSUPPORTED"``.
    """
    del event_id, response, output_format
    return serialize_tool_error(
        ToolError(
            code="CALENDAR_RSVP_UNSUPPORTED",
            message=(
                "No public macOS API can accept, decline, or tentatively respond to a calendar "
                "invitation: EventKit participant status is read-only and Calendar.app has no "
                "scripting verb for it."
            ),
            remediation={
                "manual": "Respond in Calendar.app or the mail client that received the invitation.",
                "server_side": "CalDAV/Exchange/Graph APIs can RSVP when the account is reachable outside macOS.",
            },
        )
    )
