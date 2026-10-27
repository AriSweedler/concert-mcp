"""The lineup flow (unit flows-music): who opens, when the acts go on, and the opener's most recent setlist.fm page.

`find_lineup` asks the grounded model once and checks the answer in `text/lineup.py`; it never raises, because
`concert_add` calls it after the insert and a lookup must never cost the user the calendar link. `lineup_outcome`
is the on-demand `lineup` tool: in a Telegram topic the topic's event supplies the show and gets the lines patched in.
"""

from __future__ import annotations

from collections.abc import Sequence

from dataclasses import dataclass
from typing import Any

from concert_mcp.flows.services import Services
from concert_mcp.flows.topics import live_event, topic_event
from concert_mcp.flows.venues import load_registry, save_registry
from concert_mcp.log import kv, log_info, log_warn
from concert_mcp.result import FixInput, Kind, NeedUser, Outcome, ProviderError, crash_outcome, need, outcome_from
from concert_mcp.text.dates import concert_date
from concert_mcp.text.lineup import (
    Lineup,
    Opener,
    latest_setlist_url,
    lineup_lines,
    lineup_pages,
    lineup_prompt,
    parse_website,
    urls_in,
    website_prompt,
    merge_lineup_lines,
    parse_lineup,
)
from concert_mcp.text.reply import render_lineup
from concert_mcp.text.routes import chat_target, forum_target
from concert_mcp.text.topics import topic_key
from concert_mcp.text.venues import venue_key, venue_name_of

REASON_LIMIT = 160
GEMINI_FIX = "Gemini is not set up: run `concert-mcp gemini auth` on the host, then ask again"


def venue_website(location: str, svc: Services) -> str | None:
    """The registry's website for the location's venue, if known."""
    name = venue_name_of(location)
    if not name:
        return None
    venue = load_registry(svc).get(venue_key(name))
    return venue.website if venue else None


def _opener_pages(openers: tuple[Opener, ...], svc: Services) -> tuple[Opener, ...]:
    """Each opener with its most recent setlist.fm page; a setlist.fm failure leaves the opener without one."""
    api = svc.setlistfm
    if api is None:
        return openers
    out: list[Opener] = []
    for o in openers:
        try:
            url = latest_setlist_url(api.latest_setlist(o.name))
        except Exception as e:  # noqa: BLE001 - the opener's name is worth keeping without the page
            log_warn("opener setlist lookup failed | " + kv(opener=o.name, error=f"{type(e).__name__}: {e}"))
            url = None
        out.append(Opener(o.name, url))
    return tuple(out)


def discover_website(venue: str, svc: Services) -> str | None:
    """Ask the model, once per venue, which site is the venue's own, and remember it in the registry; None when unsure."""
    model = svc.gemini
    if model is None or not venue:
        return None
    try:
        site = parse_website(model.ask(website_prompt(venue)))
    except Exception as e:  # noqa: BLE001 - a venue without a website is a lineup without pages, not a failure
        log_warn("venue website lookup failed | " + kv(venue=venue, error=f"{type(e).__name__}: {e}"[:120]))
        return None
    if not site:
        log_info("venue website unknown | " + kv(venue=venue))
        return None
    name = venue_name_of(venue)
    if name:
        reg = load_registry(svc)
        known = reg.get(venue_key(name))
        if known is not None and not known.website:
            known.website = site
            save_registry(reg, svc)
    log_info("venue website found | " + kv(venue=venue, website=site))
    return site


def find_lineup(
    artist: str, date: str, venue: str, website: str | None, svc: Services, *, ticket_urls: Sequence[str] = ()
) -> Lineup:
    """The venue's pages (found once when the registry has none), the ticket's pages and a results page, read in
    one call and checked; never raises."""
    model = svc.gemini
    if model is None:
        return Lineup("unavailable", reason="Gemini is not set up")
    try:
        site = website or discover_website(venue, svc)
        pages = lineup_pages(artist, date, venue, site, ticket_urls)
        answer = model.ask(lineup_prompt(artist, date, venue, pages))
        lineup = parse_lineup(answer, headliner=artist, venue=venue)
    except ProviderError as e:
        return Lineup("failed", reason=str(e.error)[:REASON_LIMIT])
    except Exception as e:  # noqa: BLE001 - the calendar event must land whatever the model does
        return Lineup("failed", reason=f"{type(e).__name__}: {e}"[:REASON_LIMIT])
    log_info(
        "lineup "
        + kv(
            artist=artist,
            date=date,
            status=lineup.status,
            openers=len(lineup.openers),
            times=lineup.has_times,
            sources=len(lineup.sources),
            pages=len(pages),
        )
    )
    if lineup.status != "found":
        return lineup
    return Lineup(
        "found",
        openers=_opener_pages(lineup.openers, svc),
        doors=lineup.doors,
        opener_time=lineup.opener_time,
        headliner_time=lineup.headliner_time,
        sources=lineup.sources,
    )


@dataclass(frozen=True)
class LineupStep:
    """What the lineup step did to an event: the lookup and the lines it appended."""

    lineup: Lineup
    lines: tuple[str, ...]


def add_lineup_to_event(
    *,
    artist: str,
    date: str,
    venue: str,
    website: str | None,
    openers_known: bool,
    calendar_id: str,
    event_id: str,
    description: str | None,
    svc: Services,
    source_text: str = "",
) -> LineupStep:
    """Look the lineup up and patch the lines the event's description lacks into it; never raises."""
    lineup = find_lineup(artist, date, venue, website, svc, ticket_urls=urls_in(source_text))
    wanted = lineup_lines(lineup, artist, openers_known=openers_known)
    merged, added = merge_lineup_lines(description, wanted)
    if added:
        try:
            svc.calendar.patch_event(calendar_id, event_id, description="\n".join(merged))
        except Exception as e:  # noqa: BLE001 - the reply still carries the facts; the log carries the failure
            log_warn("lineup not written to the event | " + kv(event_id=event_id, error=f"{type(e).__name__}: {e}"))
    return LineupStep(lineup, tuple(added))


def _topic_show(svc: Services) -> tuple[dict[str, Any], str, str] | None:
    """(live event, calendar id, event id) of the topic the caller speaks from, when it owns one."""
    _sid, route = svc.context.live_route(svc.clock.time())
    ft = forum_target(chat_target(route)) if route else None
    if not ft:
        return None
    entry = topic_event(topic_key(ft[0], ft[1]), svc)
    if entry is None:
        return None
    live = live_event(entry, svc)
    return (live, entry.calendar_id, entry.event_id) if live is not None else None


def lineup_outcome(artist: str, date: str, venue: str, svc: Services) -> Outcome:
    """The `lineup` tool: the topic's event fills in what the caller left out and receives the lines."""
    try:
        if svc.gemini is None:
            raise FixInput("gemini not configured", GEMINI_FIX)
        show = _topic_show(svc)
        event = show[0] if show else {}
        artist = artist.strip() or str(event.get("summary") or "")
        start = str((event.get("start") or {}).get("dateTime") or (event.get("start") or {}).get("date") or "")
        date = date.strip() or start[:10]
        venue = venue.strip() or str(event.get("location") or "").split("\n")[0]
        if not artist:
            raise need("artist")
        if not date:
            raise need("date")
        date = concert_date(date)
        website = venue_website(venue, svc) if venue else None
        if show is not None:
            step = add_lineup_to_event(
                artist=artist,
                date=date,
                venue=venue,
                website=website,
                openers_known=any(ln.startswith("Opener: ") for ln in str(event.get("description") or "").splitlines()),
                calendar_id=show[1],
                event_id=show[2],
                description=event.get("description"),
                svc=svc,
            )
            lineup, patched = step.lineup, bool(step.lines)
        else:
            lineup, patched = find_lineup(artist, date, venue, website, svc), False
        if lineup.status == "failed":
            return Outcome(
                Kind.ERROR,
                reply=f"Lineup lookup failed: {lineup.reason}",
                error=lineup.reason,
                data={"lineup": lineup.to_json()},
            )
        reply = render_lineup(artist, date, venue, lineup, patched=patched)
        return Outcome(
            Kind.OK, reply=reply, data={"artist": artist, "date": date, "venue": venue, "lineup": lineup.to_json()}
        )
    except (FixInput, NeedUser, ProviderError) as e:
        return outcome_from(e)
    except Exception as e:  # noqa: BLE001 - a tool never raises
        return crash_outcome(e, svc.operator)
