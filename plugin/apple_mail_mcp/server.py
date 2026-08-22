"""FastMCP server instance, the ToolError boundary, and user preferences."""

import functools
import inspect
import os
from collections.abc import Callable
from types import UnionType
from typing import Any, ParamSpec, Protocol, TypeVar, Union, cast, get_args, get_origin, get_type_hints

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.version import __version__

P = ParamSpec("P")
R = TypeVar("R")


class _AppleMailMCP(Protocol):
    """Typed subset of FastMCP used by this package.

    The installed FastMCP runtime has a typed ``tool`` method, but mypy treats
    it as untyped through the dependency boundary in strict mode. This protocol
    keeps the package strict without changing the ``@mcp.tool`` source pattern
    that manifest validators inspect.

    ``mcp`` is a ``_ToolErrorEnvelopeServer`` facade over the FastMCP instance,
    not the instance itself; every member below either overrides registration
    (``tool``) or is forwarded verbatim by ``__getattr__``.
    """

    def tool(
        self,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        icons: list[Any] | None = None,
        meta: dict[str, Any] | None = None,
        structured_output: bool | None = None,
    ) -> Callable[[Callable[P, R]], Callable[P, R]]: ...

    def remove_tool(self, name: str) -> None: ...

    def run(self) -> None: ...


def _returns_text(fn: Callable[..., Any]) -> bool:
    """Report whether ``fn``'s declared return type can carry a JSON string.

    FastMCP derives a structured-output schema from the return annotation and
    validates the returned value against it. Three tools declare a container
    return (``list[str]``, ``dict[...]``); handing those the JSON error
    envelope fails that validation and the agent gets a pydantic
    ``Input should be a valid list`` message with the real error code buried
    in ``input_value``. That is strictly worse than the raise it replaced, so
    the boundary skips them and their ``ToolError`` still propagates with its
    own message intact.
    """
    try:
        annotation = get_type_hints(fn).get("return", Any)
    except (NameError, TypeError):  # unresolvable hints: keep the raise
        return False
    if annotation is Any or annotation is str:
        return True
    if get_origin(annotation) in (Union, UnionType):
        return str in get_args(annotation)
    return False


def _envelope_tool_errors(fn: Callable[P, R]) -> Callable[P, R]:
    """Wrap a tool so an *uncaught* ``ToolError`` becomes the JSON envelope.

    ``ToolError`` is the package's structured-error type, and the agent-facing
    contract (``tools/CLAUDE.md``) says those errors arrive as JSON with
    ``code`` / ``message`` / ``remediation``. Most tools honour that by
    catching ``ToolError`` and returning ``serialize_tool_error(...)``
    themselves, but shared choke points below the tool bodies — notably
    ``core.run_applescript``'s ``INVALID_TIMEOUT`` guard — can raise past a
    tool that has no such handler. This boundary makes the envelope true for
    every registered tool instead of only the ones that remembered.

    Deliberately narrow: only ``ToolError`` is converted. Every other
    exception propagates untouched, because turning arbitrary failures into
    tidy JSON is the silent-failure pattern this package is removing.

    ``functools.wraps`` is load-bearing, not cosmetic: FastMCP derives each
    tool's name, description, and input schema by introspecting the function
    it is handed (``Tool.from_function`` reads ``__name__`` / ``__doc__`` and
    runs ``func_metadata``, whose ``inspect.signature`` follows the
    ``__wrapped__`` link ``wraps`` sets). A bare ``*args, **kwargs`` wrapper
    would publish 41 tools with empty schemas.
    """
    if not _returns_text(fn):
        return fn
    if inspect.iscoroutinefunction(fn):
        async_fn = cast(Callable[P, Any], fn)

        @functools.wraps(fn)
        async def async_boundary(*args: P.args, **kwargs: P.kwargs) -> Any:
            try:
                return await async_fn(*args, **kwargs)
            except ToolError as exc:
                return serialize_tool_error(exc)

        return cast(Callable[P, R], async_boundary)

    @functools.wraps(fn)
    def sync_boundary(*args: P.args, **kwargs: P.kwargs) -> Any:
        try:
            return fn(*args, **kwargs)
        except ToolError as exc:
            return serialize_tool_error(exc)

    return cast(Callable[P, R], sync_boundary)


class _ToolErrorEnvelopeServer:
    """Delegating facade over ``FastMCP`` that installs the error boundary.

    Every tool in the package registers through exactly one call —
    ``@mcp.tool(...)`` — so that decorator is the only place a package-wide
    guarantee can be made without editing ~40 tool modules. This facade
    intercepts registration, wraps the function, and hands the wrapper to
    FastMCP; the ``@mcp.tool`` *source* pattern that manifest validators grep
    for is unchanged, as is every other attribute of the FastMCP instance
    (``run``, ``remove_tool``, ``_tool_manager``, …), which ``__getattr__``
    forwards verbatim.
    """

    def __init__(self, server: Any) -> None:
        self._server = server

    def __getattr__(self, name: str) -> Any:
        return getattr(self._server, name)

    def tool(self, *args: Any, **kwargs: Any) -> Callable[[Callable[P, R]], Callable[P, R]]:
        register = self._server.tool(*args, **kwargs)

        def decorator(fn: Callable[P, R]) -> Callable[P, R]:
            return cast(Callable[P, R], register(_envelope_tool_errors(fn)))

        return decorator


# Initialize FastMCP server
_fastmcp_server = FastMCP(
    "Apple Mail MCP",
    instructions=(
        "Mail.app and Calendar.app automation is single-threaded. All installed "
        "plugin hosts for this macOS user queue every AppleScript call through "
        "one shared cross-process lock, so invoking multiple Apple Mail or Apple "
        "Calendar tools at once does not run them in parallel; the calls queue "
        "and can time out waiting their "
        "turn. Call one tool at a time and wait for its result before "
        "issuing the next. On large Exchange or Gmail mailboxes, prefer "
        "small bounded calls (low max_emails, small recent_days, offset "
        "paging) over large ones. Mode flags gate the two domains "
        "differently: for mail tools, --read-only and --draft-safe block "
        "only the send paths; for calendar tools, --read-only removes every "
        "calendar write and --draft-safe additionally blocks calendar "
        "deletes and attendee invitation sends."
    ),
)

# Advertise THIS package's version in the MCP handshake's ``serverInfo``.
#
# ``FastMCP.__init__`` accepts no ``version`` and constructs the low-level
# ``Server`` without one, so ``Server.create_initialization_options()`` falls
# back to ``importlib.metadata.version("mcp")`` — the MCP SDK's version. Every
# client therefore saw the SDK number (1.29.x) as this server's version, which
# made an installed 3.11.6 and a working-tree 3.11.7 indistinguishable over the
# protocol. ``_mcp_server`` is the only seam the SDK exposes for this;
# ``tests/infra/test_server_version_parity.py`` asserts the handshake really
# carries ``__version__`` so a silent SDK rename cannot restore the defect.
_fastmcp_server._mcp_server.version = __version__

mcp = cast(_AppleMailMCP, _ToolErrorEnvelopeServer(_fastmcp_server))

# Shared MCP tool annotations (see tasks/reference/phase-3-annotation-matrix.md).
READ_ONLY_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

WRITE_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)

IDEMPOTENT_WRITE_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

DESTRUCTIVE_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)

SEND_TOOLS = ("compose_email", "reply_to_email", "forward_email")

# Calendar mode gating (3.10.0). This is deliberately stricter than the mail
# gating above: --read-only removes every calendar write and destructive tool
# from the registry, and --draft-safe blocks calendar deletes and attendee
# invitation sends inside the tool bodies. The equivalent mail actions
# (manage_trash, move_email, create_mailbox) are NOT mode-gated today; keeping
# that asymmetry visible here and in the server instructions is intentional
# (final plan F4/F12), and unifying mail-side gating is a separately scoped
# forward item.
CALENDAR_WRITE_TOOLS = (
    "create_event",
    "update_event",
    "batch_create_events",
    "manage_calendars",
)
CALENDAR_DESTRUCTIVE_TOOLS = ("delete_events",)

# Load user preferences from environment
USER_PREFERENCES = os.environ.get("USER_EMAIL_PREFERENCES", "")

# Default Mail account name. When set, search/list tools default to this
# account instead of fanning out across every configured account. Tests
# monkeypatch ``apple_mail_mcp.server.DEFAULT_MAIL_ACCOUNT`` directly, so
# tools should read this lazily (e.g. ``from apple_mail_mcp import server;
# server.DEFAULT_MAIL_ACCOUNT``) rather than importing the constant once.
DEFAULT_MAIL_ACCOUNT = os.environ.get("DEFAULT_MAIL_ACCOUNT", "").strip() or None
DEFAULT_MAIL_SIGNATURE = os.environ.get("DEFAULT_MAIL_SIGNATURE", "").strip() or None

# Default calendar for create targets only (reads keep their capped fan-out
# default; see tools/calendar docstrings). Tools read this lazily via
# ``server.DEFAULT_CALENDAR`` so tests can monkeypatch it.
DEFAULT_CALENDAR = os.environ.get("DEFAULT_CALENDAR", "").strip() or None

# Operator-level unlock for calendar deletes under --draft-safe. Env-only by
# design: an agent can never grant itself delete power mid-session.
CALENDAR_ALLOW_DESTRUCTIVE = os.environ.get("CALENDAR_ALLOW_DESTRUCTIVE", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# Read-only mode flag — set via --read-only CLI argument.
# When enabled, tools that send email are disabled. Drafts remain available.
READ_ONLY = False

# Draft-safe mode flag — set via --draft-safe CLI argument.
# When enabled, sending is disabled but draft/open workflows remain available.
DRAFT_SAFE = False
