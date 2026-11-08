"""The movie add flow (unit flows-movie): a ticket screenshot becomes `[Movie] <film>` on the personal calendar.

The ticket text decides every field it prints: the film, the theater and its address, the showtime, and the end
(start plus runtime); the caller's strings fill only what the text lacks. No playlist, no setlist, no lineup: the
plain add path does the insert, the duplicate guard and the reply. The Letterboxd page is looked up once, and a
miss leaves the line out rather than failing the add.
"""

from __future__ import annotations

import re

from concert_mcp.flows.concert_add import AddRequest, _note_lines, add_event
from concert_mcp.flows.services import Services
from concert_mcp.log import kv, log_warn
from concert_mcp.result import FixInput, NeedUser, Outcome, ProviderError, outcome_from
from concert_mcp.text.dates import parse_time
from concert_mcp.text.movie import MovieTicket, movie_lines, parse_movie_ticket, show_start

PREFIX = "[Movie]"


def is_movie_title(title: str) -> bool:
    """A calendar_add title the user or model prefixed with [Movie]."""
    return re.match(r"^\s*\[movie\]", title, flags=re.IGNORECASE) is not None


def _film_of(title: str) -> str:
    """The caller's title without a [Movie] prefix or a trailing (year)."""
    t = re.sub(r"^\s*\[movie\]\s*", "", title, flags=re.IGNORECASE).strip()
    return re.sub(r"\s*\((?:19|20)\d{2}\)$", "", t)


def _letterboxd(t: MovieTicket | None, film: str, svc: Services) -> str | None:
    if svc.films is None or not film:
        return None
    try:
        return svc.films.film_url(film, t.year if t else None)
    except Exception as e:  # noqa: BLE001 - a lookup failure is a missing line, never a failed add
        log_warn(f"letterboxd lookup failed | {kv(film=film, error=type(e).__name__)}")
        return None


def movie_request(
    *,
    title: str,
    start: str,
    location: str,
    source_text: str,
    notes: str,
    svc: Services,
    dry_run: bool,
) -> AddRequest:
    """The AddRequest for one movie: the ticket's fields over the caller's, the derived lines trusted."""
    now_s = svc.clock.time()
    sid, _ = svc.context.live_route(now_s)
    text = source_text or svc.context.recent_text(now_s, sid)
    t = parse_movie_ticket(text)

    film = t.film if t else _film_of(title)
    start_s = show_start(t, svc.clock.now()) if t else start
    # Without a runtime the generic default end applies, and the reply marks it assumed.
    end_s = (parse_time(start_s, svc.settings.tz, "start") + t.runtime).isoformat() if t and t.runtime else None
    return AddRequest(
        title=f"{PREFIX} {film}" if film else "",
        start=start_s,
        end=end_s,
        location=t.location if t else location,
        description=_note_lines(notes),
        trusted=tuple(movie_lines(t, _letterboxd(t, film, svc))) if t else (),
        source=source_text,
        concert=False,
        dry_run=dry_run,
    )


def add_movie(
    *,
    title: str = "",
    start: str = "",
    location: str = "",
    source_text: str = "",
    notes: str = "",
    svc: Services,
    dry_run: bool = False,
) -> Outcome:
    """event_add's movie entry. Never raises."""
    try:
        req = movie_request(
            title=title, start=start, location=location, source_text=source_text, notes=notes, svc=svc, dry_run=dry_run
        )
    except (FixInput, NeedUser, ProviderError) as e:
        return outcome_from(e)
    return add_event(req, svc)
