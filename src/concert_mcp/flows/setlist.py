"""The setlist.fm lookup flow (unit flows-music).

Exact date first; when that finds nothing, the artist's shows that year within a day of the date, at a venue
sharing a word with the one on the ticket. `setlist_playlist` calls it when no page is given. `find_setlist` never
raises for a provider failure: a failure is a result the caller words; only a bad date (the caller's input)
propagates.
"""

from __future__ import annotations

from concert_mcp.flows.services import Services
from concert_mcp.result import ProviderError
from concert_mcp.text.dates import concert_date
from concert_mcp.text.setlists import SetlistResult, near_date, rank_by_venue, setlist_row, setlistfm_date

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
            return SetlistResult(False, "none", artist, date, venue)
        return SetlistResult(True, how, artist, date, venue, setlist=rows[0])
    except ProviderError as e:
        return SetlistResult(False, "failed", artist, date, venue, reason=str(e.error)[:REASON_LIMIT])
    except Exception as e:  # noqa: BLE001 - an unexpected setlist.fm answer is a failed lookup, not a crash
        return SetlistResult(False, "failed", artist, date, venue, reason=f"{type(e).__name__}: {e}"[:REASON_LIMIT])
