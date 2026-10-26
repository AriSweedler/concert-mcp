"""The MCP server: seven coarse tools for a small model (unit server).

Every parameter is a `Param`: a string with default "" whose validator runs `clean_param` before pydantic sees the
value, so a number, a null or a quoted string from the model becomes a clean string and never a schema error (which
a small model retries on). A missing value is handled in code. Every tool body is a thin adapter onto one flow through
`flows.deliver.run_tool`, and every tool returns the legacy output-contract string. This is the only module in the
package that imports the MCP SDK.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BeforeValidator, Field

from concert_mcp import __version__
from concert_mcp.config import load_settings
from concert_mcp.flows.concert_add import add_concert, add_plain
from concert_mcp.flows.deliver import run_tool
from concert_mcp.flows.event_delete import DeleteRequest, delete_event
from concert_mcp.flows.healthcheck import healthcheck_outcome
from concert_mcp.flows.queue import queue_outcome
from concert_mcp.flows.services import Services, build_services
from concert_mcp.flows.setlist import setlist_outcome
from concert_mcp.flows.teaser import teaser_outcome
from concert_mcp.log import set_level
from concert_mcp.result import Outcome, crash_outcome
from concert_mcp.text.titles import clean_param, is_yes

# Every tool parameter: the schema stays {"type": "string"}, and 2 -> "2", None -> "", "\"x\"" -> "x" before validation.
Param = Annotated[str, BeforeValidator(clean_param)]

# Injected into the model's system prompt by OpenClaw (first 2000 chars).
INSTRUCTIONS = (
    "Concert tools (calendar, Spotify teaser, setlist.fm). Rules:\n"
    "1. Call one tool once. Never call the same tool again unless its result starts with FIX:.\n"
    "2. If a result starts with NO_REPLY, your entire reply is exactly: NO_REPLY\n"
    "3. If a result starts with FIX:, change only what it names and call once more. "
    "If it fails again, tell the user in one sentence what you need from them.\n"
    "4. Any other result: send its text to the user unchanged. Never invent a link, time or price.\n"
    "5. Ticket screenshot or ticket text -> concert_add with artist, start (the show time, not doors; "
    "a bare date is fine when the text has the show time), venue, and the whole text in source_text. "
    "A repeat in the same topic updates that topic's event; pass another=yes only when the user asks for a "
    "second event there.\n"
    "6. Plain event (dinner, flight, appointment) -> calendar_add. Keep the user's [Type] prefix.\n"
    '7. "delete"/"remove" an event -> event_delete. '
    "If the user answers a numbered list with a number, call event_delete with pick=that number.\n"
    '8. Playlist alone -> teaser_playlist. Setlist alone -> setlist_find. "what is queued" -> queue_status. '
    '"are you healthy"/"what is missing" -> healthcheck.\n'
    "9. Do not print tool calls as text; call the tool."
)

# Tool descriptions for the model: short and literal (docs/DESIGN.md §4, each under 300 chars).
DESCRIPTIONS = {
    "concert_add": (
        "Add a concert to the calendar and do everything that goes with it: teaser playlist, setlist.fm lookup, "
        "the chat reply. Call once per ticket with artist, start and venue as read from the ticket; paste the ticket "
        "text into source_text. If the result starts with NO_REPLY, answer exactly NO_REPLY."
    ),
    "calendar_add": (
        "Add a plain (non-concert) event to the personal calendar, keeping the title as the user wrote it. "
        "For a concert ticket use concert_add instead. If the result starts with NO_REPLY, answer exactly NO_REPLY."
    ),
    "event_delete": (
        "Delete one calendar event by artist or title. When several match, the tool lists them with numbers and asks "
        "the user; when the user answers with a number, call this again with pick set to that number. "
        "If the result starts with NO_REPLY, answer exactly NO_REPLY."
    ),
    "teaser_playlist": (
        "Make or top up the Spotify teaser playlist for one concert: every song the user liked by the artist, oldest "
        'first, named "YYYY-MM-DD | Artist". concert_add already does this; call only when asked for the playlist '
        "alone. NO_REPLY means answer exactly NO_REPLY."
    ),
    "setlist_find": (
        "Look up one show on setlist.fm by artist and date. concert_add already does this; call only when asked "
        "about a setlist alone. NO_REPLY means answer exactly NO_REPLY."
    ),
    "queue_status": (
        "List the concerts waiting for the weekly setlist.fm check. Takes no parameters. "
        "NO_REPLY means answer exactly NO_REPLY."
    ),
    "healthcheck": (
        "Report which credentials and tools are present for Google Calendar, Spotify, setlist.fm and OpenClaw, "
        "and what fixes each missing one. Takes no parameters."
    ),
}

ANNOTATIONS = {
    "concert_add": ToolAnnotations(),
    "calendar_add": ToolAnnotations(),
    "event_delete": ToolAnnotations(destructiveHint=True),
    "teaser_playlist": ToolAnnotations(),
    "setlist_find": ToolAnnotations(readOnlyHint=True),
    "queue_status": ToolAnnotations(readOnlyHint=True),
    "healthcheck": ToolAnnotations(readOnlyHint=True),
}

# The Services the tools run on. build_server(svc) binds a given bundle (tests); None means build the real one on
# the first tool call, so `tools/list` needs no credentials and the server starts on a bare box.
_svc: Services | None = None


def _services() -> Services:
    global _svc
    if _svc is None:
        _svc = build_services()
    return _svc


def _crash_text(exc: BaseException, operator: str | None = None) -> str:
    """The crash reply when the failure happened outside run_tool (the services could not be built)."""
    if operator is None:
        try:
            operator = load_settings().operator_name
        except Exception:
            operator = "Admin"
    return f"{crash_outcome(exc, operator).reply}\nNOT SENT: the tool crashed before delivery"


def _run(name: str, make: Callable[[Services, dict[str, str]], Outcome], **args: str) -> str:
    """Build the services and run the flow through run_tool (guarded, delivered, rendered).

    Never raises: a failure before run_tool (services that will not build) is the same crash reply run_tool itself
    produces for a flow that crashes.
    """
    try:
        svc = _services()
    except Exception as exc:
        return _crash_text(exc)
    try:
        return run_tool(lambda: make(svc, args), svc, tool=name, guard=True)[2]
    except Exception as exc:  # run_tool already turns a flow crash into a reply; this covers the delivery layer itself
        return _crash_text(exc, svc.operator)


def concert_add(
    artist: Annotated[
        Param,
        Field(
            description='Headliner as printed on the ticket, e.g. "Zed Fox". '
            'Openers may stay in: "Zed Fox with special guest Zork."'
        ),
    ] = "",
    start: Annotated[
        Param,
        Field(
            description='Show date and time, e.g. "2026-11-14 20:30" or "2026-11-14 8:30 PM". Use the show time, '
            "not doors. A bare date is fine when the ticket text states the show time."
        ),
    ] = "",
    venue: Annotated[Param, Field(description='Venue, and city when shown, e.g. "The Quarry, San Francisco".')] = "",
    source_text: Annotated[
        Param,
        Field(
            description="The ticket text you read these from (OCR or pasted email), whole and unedited. "
            "Leave empty if there is none."
        ),
    ] = "",
    notes: Annotated[
        Param,
        Field(
            description='Extra facts, one per line, e.g. "Seat: GA". Leave empty when unsure; Doors, Purchased and '
            "Opener lines are found in the text automatically."
        ),
    ] = "",
    another: Annotated[
        Param,
        Field(
            description='"yes" only when the user asks for a second, separate event in this chat topic. Leave empty '
            "otherwise: a repeat call in a topic updates the topic's event."
        ),
    ] = "",
) -> str:
    """Add a concert and everything that goes with it; returns the output-contract string."""
    return _run(
        "concert_add",
        lambda svc, a: add_concert(
            artist=a["artist"],
            start=a["start"],
            venue=a["venue"],
            source_text=a["source_text"],
            notes=a["notes"],
            another=is_yes(a["another"]),
            svc=svc,
            dry_run=svc.settings.dry_run,
        ),
        artist=artist,
        start=start,
        venue=venue,
        source_text=source_text,
        notes=notes,
        another=another,
    )


def calendar_add(
    title: Annotated[
        Param,
        Field(
            description="Event title exactly as the user wrote it, including a [Type] prefix if they used one, "
            'e.g. "[Dinner] Team dinner".'
        ),
    ] = "",
    start: Annotated[
        Param, Field(description='Date and time of the event, e.g. "2026-11-14 19:00" or "2026-11-14 7:00 PM".')
    ] = "",
    location: Annotated[Param, Field(description='Where it happens, e.g. "ZZZ Bistro, San Francisco".')] = "",
    notes: Annotated[Param, Field(description="Extra facts, one per line. Leave empty when unsure.")] = "",
    calendar: Annotated[
        Param, Field(description="Calendar name when the user named one; empty means the personal calendar.")
    ] = "",
) -> str:
    """Add a plain event to the personal calendar."""
    return _run(
        "calendar_add",
        lambda svc, a: add_plain(
            title=a["title"],
            start=a["start"],
            location=a["location"],
            notes=a["notes"],
            calendar=a["calendar"],
            svc=svc,
            dry_run=svc.settings.dry_run,
        ),
        title=title,
        start=start,
        location=location,
        notes=notes,
        calendar=calendar,
    )


def event_delete(
    title: Annotated[Param, Field(description='Artist or event title to remove, e.g. "Zed Fox".')] = "",
    date: Annotated[Param, Field(description="Day of the event as YYYY-MM-DD when known.")] = "",
    pick: Annotated[
        Param,
        Field(
            description="After the tool listed numbered matches and the user answered with a number, pass that "
            'number here, e.g. "2", and leave title empty.'
        ),
    ] = "",
) -> str:
    """Delete one event by title, or by pick after a numbered list."""
    return _run(
        "event_delete",
        lambda svc, a: delete_event(
            DeleteRequest(
                title=a["title"], date=a["date"] or None, pick=a["pick"] or None, dry_run=svc.settings.dry_run
            ),
            svc,
        ),
        title=title,
        date=date,
        pick=pick,
    )


def teaser_playlist(
    artist: Annotated[Param, Field(description='Artist name, e.g. "Zed Fox".')] = "",
    date: Annotated[Param, Field(description="Concert date as YYYY-MM-DD.")] = "",
) -> str:
    """Make or top up the teaser playlist alone."""
    return _run(
        "teaser_playlist",
        lambda svc, a: teaser_outcome(a["artist"], a["date"], svc, dry_run=svc.settings.dry_run),
        artist=artist,
        date=date,
    )


def setlist_find(
    artist: Annotated[Param, Field(description='Artist name, e.g. "Zed Fox".')] = "",
    date: Annotated[Param, Field(description="Concert date as YYYY-MM-DD.")] = "",
    venue: Annotated[Param, Field(description="Venue name to break ties.")] = "",
) -> str:
    """Look up one show on setlist.fm alone."""
    return _run(
        "setlist_find",
        lambda svc, a: setlist_outcome(a["artist"], a["date"], a["venue"], svc),
        artist=artist,
        date=date,
        venue=venue,
    )


def queue_status() -> str:
    """List the concerts waiting for the weekly setlist check."""
    return _run("queue_status", lambda svc, a: queue_outcome(svc))


def healthcheck() -> str:
    """Report which credentials and tools are present."""
    return _run("healthcheck", lambda svc, a: healthcheck_outcome(svc.settings, probe=False, svc=svc))


TOOLS: tuple[Callable[..., str], ...] = (
    concert_add,
    calendar_add,
    event_delete,
    teaser_playlist,
    setlist_find,
    queue_status,
    healthcheck,
)


def build_server(svc: Services | None = None) -> MCPServer:
    """MCPServer('concert') with the seven tools; services are built lazily on the first tool call."""
    global _svc
    _svc = svc
    mcp = MCPServer("concert", instructions=INSTRUCTIONS, version=__version__)
    for fn in TOOLS:
        name = fn.__name__
        mcp.tool(
            name=name,
            description=DESCRIPTIONS[name],
            annotations=ANNOTATIONS[name],
            structured_output=False,
        )(fn)
    return mcp


def main() -> None:
    """Serve over stdio; nothing but JSON-RPC on stdout."""
    settings = load_settings()
    set_level(settings.log_level)
    build_server().run()
