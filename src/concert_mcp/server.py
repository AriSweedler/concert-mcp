"""The MCP server: ten coarse tools for a small model (unit server).

Every parameter is a `Param`: a string with default "" whose validator runs `clean_param` before pydantic sees the
value, so a number, a null or a quoted string from the model becomes a clean string and never a schema error (which
a small model retries on). A missing value is handled in code. Every tool body is a thin adapter onto one flow through
`flows.deliver.run_tool`, and every tool returns the legacy output-contract string. This is the only module in the
package that imports the MCP SDK.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable, Sequence
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BeforeValidator, Field

from concert_mcp import __version__
from concert_mcp.calllog import record_call
from concert_mcp.config import load_settings
from concert_mcp.flows.concert_add import add_concert, add_plain
from concert_mcp.flows.deliver import run_tool
from concert_mcp.flows.event_delete import DeleteRequest, delete_event
from concert_mcp.flows.failures import save_tool_failure
from concert_mcp.flows.healthcheck import healthcheck_outcome
from concert_mcp.flows.lineup import lineup_outcome
from concert_mcp.flows.movie_add import add_movie, is_movie_title
from concert_mcp.flows.queue import queue_outcome
from concert_mcp.flows.services import Services, build_services
from concert_mcp.flows.setlist import setlist_outcome
from concert_mcp.flows.setlist_playlist import setlist_playlist_outcome
from concert_mcp.flows.teaser import teaser_outcome
from concert_mcp.log import kv, log_info, log_warn, set_level
from concert_mcp.prompts import load_prompt
from concert_mcp.result import Kind, Outcome, crash_outcome
from concert_mcp.text.movie import parse_movie_ticket
from concert_mcp.text.titles import clean_param, is_yes

# Every tool parameter: the schema stays {"type": "string"}, and 2 -> "2", None -> "", "\"x\"" -> "x" before validation.
Param = Annotated[str, BeforeValidator(clean_param)]

# Injected into the model's system prompt by OpenClaw (first 2000 chars): prompts/instructions.txt.
INSTRUCTIONS = load_prompt("instructions")

# Tool descriptions, short and literal (docs/DESIGN.md §4, each under 300 chars): prompts/tools/<name>.txt.
TOOL_NAMES = (
    "concert_add",
    "calendar_add",
    "movie_add",
    "event_delete",
    "teaser_playlist",
    "setlist_find",
    "setlist_playlist",
    "lineup",
    "queue_status",
    "healthcheck",
)
DESCRIPTIONS = {name: load_prompt(f"tools/{name}") for name in TOOL_NAMES}

ANNOTATIONS = {
    "concert_add": ToolAnnotations(),
    "calendar_add": ToolAnnotations(),
    "movie_add": ToolAnnotations(),
    "event_delete": ToolAnnotations(destructiveHint=True),
    "teaser_playlist": ToolAnnotations(),
    "setlist_find": ToolAnnotations(readOnlyHint=True),
    "setlist_playlist": ToolAnnotations(),
    "lineup": ToolAnnotations(),
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
    record_call(svc.settings.state_dir, name, args)
    try:
        outcome, delivery, text = run_tool(lambda: make(svc, args), svc, tool=name, guard=True)
    except Exception as exc:  # run_tool already turns a flow crash into a reply; this covers the delivery layer itself
        _keep_failure(svc, name, args, "crash", repr(exc)[:300], None)
        return _crash_text(exc, svc.operator)
    # An error, or a fix the guard stopped asking for and told the chat about: the user saw a failure, so keep it.
    if outcome.kind is Kind.ERROR or (outcome.kind is Kind.FIX and delivery.sent):
        _keep_failure(svc, name, args, outcome.kind.value, outcome.error or outcome.fix, outcome.reply)
    return text


def _keep_failure(
    svc: Services, tool: str, args: dict[str, str], kind: str, error: str | None, reply: str | None
) -> None:
    """Save a failed tool call under failures/ for replay. Never raises: the reply matters more than the record."""
    try:
        now_s = svc.clock.time()
        sid, route = svc.context.live_route(now_s)
        chat = json.dumps({"session": sid, "route": route}, default=str) if sid else ""
        folder = save_tool_failure(
            svc.settings, tool=tool, args=args, kind=kind, error=error, reply=reply, chat=chat, now_s=now_s
        )
        if folder:
            log_info(f"saved a failed tool call | {kv(tool=tool, folder=str(folder))}")
    except Exception as e:  # noqa: BLE001
        log_warn(f"could not save the failed tool call | {kv(tool=tool, error=repr(e)[:200])}")


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
        # A movie ticket sent here (the model's habit for any ticket) goes to the movie flow: no playlist for a film.
        lambda svc, a: (
            add_movie(
                title=a["artist"],
                start=a["start"],
                location=a["venue"],
                source_text=a["source_text"],
                notes=a["notes"],
                svc=svc,
                dry_run=svc.settings.dry_run,
            )
            if parse_movie_ticket(a["source_text"])
            else add_concert(
                artist=a["artist"],
                start=a["start"],
                venue=a["venue"],
                source_text=a["source_text"],
                notes=a["notes"],
                another=is_yes(a["another"]),
                svc=svc,
                dry_run=svc.settings.dry_run,
            )
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
        # A [Movie] title is the movie flow, which reads the ticket from the recent OCR text.
        lambda svc, a: (
            add_movie(
                title=a["title"],
                start=a["start"],
                location=a["location"],
                notes=a["notes"],
                svc=svc,
                dry_run=svc.settings.dry_run,
            )
            if is_movie_title(a["title"])
            else add_plain(
                title=a["title"],
                start=a["start"],
                location=a["location"],
                notes=a["notes"],
                calendar=a["calendar"],
                svc=svc,
                dry_run=svc.settings.dry_run,
            )
        ),
        title=title,
        start=start,
        location=location,
        notes=notes,
        calendar=calendar,
    )


def movie_add(
    film: Annotated[Param, Field(description='The film as printed on the ticket, e.g. "Zed Fox: The Movie".')] = "",
    start: Annotated[Param, Field(description='Showtime, e.g. "2026-11-11 14:30" or "2026-11-11 2:30 PM".')] = "",
    theater: Annotated[
        Param, Field(description='Theater, and city when shown, e.g. "ZZZ Cinema, San Francisco".')
    ] = "",
    source_text: Annotated[
        Param,
        Field(
            description="The ticket text you read these from (OCR or pasted email), whole and unedited. "
            "Leave empty if there is none."
        ),
    ] = "",
    notes: Annotated[Param, Field(description="Extra facts, one per line. Leave empty when unsure.")] = "",
) -> str:
    """Add a movie: [Movie] <film>, end at start plus runtime, seats, runtime, Letterboxd link, purchase."""
    return _run(
        "movie_add",
        lambda svc, a: add_movie(
            title=a["film"],
            start=a["start"],
            location=a["theater"],
            source_text=a["source_text"],
            notes=a["notes"],
            svc=svc,
            dry_run=svc.settings.dry_run,
        ),
        film=film,
        start=start,
        theater=theater,
        source_text=source_text,
        notes=notes,
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


def setlist_playlist(
    url: Annotated[
        Param,
        Field(
            description="The setlist.fm page the user pasted, e.g. "
            '"https://www.setlist.fm/setlist/zed-fox/2026/the-quarry-san-francisco-ca-3bd6f4a.html". '
            "Leave empty to look the show up by artist and date."
        ),
    ] = "",
    artist: Annotated[Param, Field(description='Artist name when there is no link, e.g. "Zed Fox".')] = "",
    date: Annotated[Param, Field(description="Concert date as YYYY-MM-DD when there is no link.")] = "",
    venue: Annotated[Param, Field(description="Venue name to break ties when looking the show up.")] = "",
) -> str:
    """Build or top up the setlist playlist of one show and link it from the event."""
    return _run(
        "setlist_playlist",
        lambda svc, a: setlist_playlist_outcome(
            a["url"], a["artist"], a["date"], a["venue"], svc, dry_run=svc.settings.dry_run
        ),
        url=url,
        artist=artist,
        date=date,
        venue=venue,
    )


def lineup(
    artist: Annotated[Param, Field(description='Headliner, e.g. "Zed Fox". Empty in a topic that has a show.')] = "",
    date: Annotated[Param, Field(description="Concert date as YYYY-MM-DD. Empty in a topic that has a show.")] = "",
    venue: Annotated[Param, Field(description='Venue when known, e.g. "The Quarry, San Francisco".')] = "",
) -> str:
    """Who opens, the set times and the opener's recent setlist for one show; patches the topic's event."""
    return _run(
        "lineup",
        lambda svc, a: lineup_outcome(a["artist"], a["date"], a["venue"], svc),
        artist=artist,
        date=date,
        venue=venue,
    )


def queue_status() -> str:
    """List the concerts whose setlist.fm page the poll re-reads when asked."""
    return _run("queue_status", lambda svc, a: queue_outcome(svc))


def healthcheck() -> str:
    """Report which credentials and tools are present."""
    return _run("healthcheck", lambda svc, a: healthcheck_outcome(svc.settings, probe=False, svc=svc))


TOOLS: tuple[Callable[..., str], ...] = (
    concert_add,
    calendar_add,
    movie_add,
    event_delete,
    teaser_playlist,
    setlist_find,
    setlist_playlist,
    lineup,
    queue_status,
    healthcheck,
)


def build_server(svc: Services | None = None) -> MCPServer:
    """MCPServer('concert') with the nine tools; services are built lazily on the first tool call."""
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


def tool_listing(svc: Services, names: Sequence[str]) -> list[dict[str, object]]:
    """The named tools as MCP lists them ({name, description, inputSchema}), in the given order."""
    by_name = {t.name: t for t in asyncio.run(build_server(svc).list_tools())}
    return [
        {"name": n, "description": by_name[n].description, "inputSchema": by_name[n].input_schema}
        for n in names
        if n in by_name
    ]


def call_tool(name: str, args: dict[str, str]) -> str:
    """Call one tool function on the services build_server bound, dropping arguments it does not take."""
    fn = {f.__name__: f for f in TOOLS}.get(name)
    if fn is None:
        return f"(the model called {name!r}, which is not a tool)"
    params = inspect.signature(fn).parameters
    return fn(**{k: v for k, v in args.items() if k in params})


def main() -> None:  # pragma: no cover - the stdio loop; test_smoke_stdio.py drives it as a real process
    """Serve over stdio; nothing but JSON-RPC on stdout."""
    settings = load_settings()
    set_level(settings.log_level)
    build_server().run()
