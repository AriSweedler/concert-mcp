"""The setlist.fm lookup flow (unit flows-music).

Exact date first; when that finds nothing, the artist's shows that year within a day of the date, at a venue
sharing a word with the one on the ticket. `find_setlist` never raises for a provider failure, because
concert_add calls it and the event must land either way; only a bad date (the caller's input) propagates.
"""

from __future__ import annotations

from concert_mcp.flows.services import Services
from concert_mcp.result import FixInput, Kind, NeedUser, Outcome, ProviderError, crash_outcome, need, outcome_from
from concert_mcp.text.dates import concert_date
from concert_mcp.text.reply import provider_reply, render_setlist_standalone
from concert_mcp.text.setlists import SetlistResult, near_date, rank_by_venue, search_url, setlist_row, setlistfm_date

REASON_LIMIT = 160


def find_setlist(artist: str, date: str, venue: str | None, svc: Services) -> SetlistResult:
    """Exact date first, else within a day at a venue sharing a word; never raises except FixInput for a bad date."""
    if svc.setlistfm is None:
        return SetlistResult(False, "unavailable", artist, date, venue, reason="setlist.fm is not set up")
    api = svc.setlistfm
    date = concert_date(date)
    try:
        res = api.search_setlists(artist, date=setlistfm_date(date))
        rows = rank_by_venue([setlist_row(s) for s in (res or {}).get("setlist", [])], venue)
        how = "exact date"
        if not rows:
            res = api.search_setlists(artist, year=date[:4])
            rows = near_date([setlist_row(s) for s in (res or {}).get("setlist", [])], date, venue)
            how = "within a day" if rows else "none"
        if not rows:
            return SetlistResult(False, "none", artist, date, venue, search_url=search_url(artist))
        return SetlistResult(True, how, artist, date, venue, setlist=rows[0])
    except ProviderError as e:
        return SetlistResult(
            False, "failed", artist, date, venue, reason=str(e.error)[:REASON_LIMIT], search_url=search_url(artist)
        )
    except Exception as e:  # noqa: BLE001 - the calendar event must land whatever setlist.fm does
        return SetlistResult(
            False,
            "failed",
            artist,
            date,
            venue,
            reason=f"{type(e).__name__}: {e}"[:REASON_LIMIT],
            search_url=search_url(artist),
        )


def setlist_outcome(artist: str, date: str, venue: str, svc: Services) -> Outcome:
    """The standalone setlist_find tool's Outcome."""
    try:
        if not artist:
            raise need("artist")
        if not date:
            raise need("date")
        res = find_setlist(artist, date, venue or None, svc)
        if res.match == "unavailable":
            return Outcome(
                Kind.ERROR,
                reply=provider_reply("setlistfm-unconfigured", svc.operator),
                error="setlistfm not configured",
            )
        if res.match == "failed":
            return Outcome(
                Kind.ERROR, reply=f"Setlist lookup failed: {res.reason}", error=res.reason, data=res.to_json()
            )
        return Outcome(Kind.OK, reply=render_setlist_standalone(res), data=res.to_json())
    except (FixInput, NeedUser, ProviderError) as e:
        return outcome_from(e)
    except Exception as e:  # noqa: BLE001 - a tool never raises
        return crash_outcome(e, svc.operator)
