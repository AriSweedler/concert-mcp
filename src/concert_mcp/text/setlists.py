"""setlist.fm rows and the lookup result (unit text-music). Pure."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date as date_type
from typing import Any, Literal
from urllib.parse import quote

from concert_mcp.text import titles


@dataclass(frozen=True)
class Setlist:
    id: str
    url: str
    artist: str
    date: str
    venue: str
    city: str | None
    tour: str | None
    song_count: int
    songs: tuple[str, ...]

    def to_json(self, songs: bool = False) -> dict[str, Any]:
        """The flat setlist record, songs included on request."""
        rec: dict[str, Any] = {
            "id": self.id,
            "url": self.url,
            "artist": self.artist,
            "date": self.date,
            "venue": self.venue,
            "city": self.city,
            "tour": self.tour,
            "song_count": self.song_count,
        }
        if songs:
            rec["songs"] = list(self.songs)
        return rec


@dataclass(frozen=True)
class SetlistResult:
    found: bool
    match: Literal["exact date", "within a day", "none", "unavailable", "failed"]
    artist: str
    date: str
    venue: str | None
    setlist: Setlist | None = None
    search_url: str | None = None
    reason: str | None = None

    @property
    def url(self) -> str | None:
        """The setlist.fm page when found."""
        return self.setlist.url if self.setlist else None

    @property
    def song_count(self) -> int:
        """Songs on the page so far (0 when not found)."""
        return self.setlist.song_count if self.setlist else 0

    def to_json(self, songs: bool = False) -> dict[str, Any]:
        """The legacy setlist-find record."""
        if self.found and self.setlist is not None:
            return {"found": True, "match": self.match, **self.setlist.to_json(songs)}
        rec: dict[str, Any] = {
            "found": False,
            "artist": self.artist,
            "date": self.date,
            "venue": self.venue,
            "match": self.match,
            "search_url": self.search_url,
        }
        if self.reason is not None:
            rec["error"] = self.reason
        return rec


def setlist_row(s: Mapping[str, Any]) -> Setlist:
    """A Setlist from one setlist.fm API row (eventDate dd-MM-yyyy flipped to yyyy-MM-dd)."""
    songs = [
        song.get("name")
        for st in (s.get("sets") or {}).get("set", [])
        for song in st.get("song", [])
        if song.get("name")
    ]
    venue = s.get("venue") or {}
    return Setlist(
        id=str(s.get("id") or ""),
        url=str(s.get("url") or ""),
        artist=str((s.get("artist") or {}).get("name") or ""),
        date="-".join(reversed((s.get("eventDate") or "").split("-"))),
        venue=venue.get("name") or "",
        city=(venue.get("city") or {}).get("name"),
        tour=(s.get("tour") or {}).get("name"),
        song_count=len(songs),
        songs=tuple(songs),
    )


def setlistfm_date(date: str) -> str:
    """'YYYY-MM-DD' -> 'dd-MM-yyyy'."""
    y, m, d = date.strip()[:10].split("-")
    return f"{d}-{m}-{y}"


def _venue_words(venue: str | None) -> set[str]:
    return set(titles.normalize_name(venue or "").split())


def rank_by_venue(rows: Sequence[Setlist], venue: str | None) -> list[Setlist]:
    """Stable sort by shared normalized venue words when a venue is given and there is more than one row."""
    want = _venue_words(venue)
    ranked = list(rows)
    if want and len(ranked) > 1:
        ranked.sort(key=lambda r: -len(want & _venue_words(r.venue)))
    return ranked


def near_date(rows: Iterable[Setlist], date: str, venue: str | None, max_days: int = 1) -> list[Setlist]:
    """Rows within max_days of the date (and sharing a venue word when a venue is given), nearest first."""
    want = _venue_words(venue)
    target = date_type.fromisoformat(date)
    near: list[tuple[int, Setlist]] = []
    for r in rows:
        try:
            delta = abs((date_type.fromisoformat(r.date) - target).days)
        except ValueError:
            continue
        venue_ok = not want or bool(want & _venue_words(r.venue))
        if delta <= max_days and venue_ok:
            near.append((delta, r))
    return [r for _, r in sorted(near, key=lambda t: t[0])]


def search_url(artist: str) -> str:
    """The setlist.fm search page for the artist."""
    return f"https://www.setlist.fm/search?query={quote(artist)}"
