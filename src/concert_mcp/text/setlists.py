"""setlist.fm rows and the lookup result (unit text-music). Pure."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date as date_type
from typing import Any, Literal

from concert_mcp.text import titles


@dataclass(frozen=True)
class Setlist:
    """One setlist.fm search row: what the show lookup ranks by (date, venue) and what it hands on (id, url)."""

    id: str
    url: str
    date: str
    venue: str


@dataclass(frozen=True)
class Song:
    """One song of a setlist.fm page: its name, the original artist when it is a cover, and whether it was tape."""

    name: str
    artist: str | None = None
    tape: bool = False


def songs_of(setlist_json: Mapping[str, Any] | None) -> list[Song]:
    """The songs of one setlist.fm setlist in page order (`sets.set[].song[]`); a nameless row is skipped."""
    out: list[Song] = []
    for st in ((setlist_json or {}).get("sets") or {}).get("set", []) or []:
        for song in st.get("song", []) or []:
            name = str(song.get("name") or "").strip()
            if not name:
                continue
            cover = (song.get("cover") or {}).get("name")
            out.append(Song(name=name, artist=str(cover).strip() if cover else None, tape=bool(song.get("tape"))))
    return out


# A setlist.fm setlist page: https://www.setlist.fm/setlist/<artist>/<year>/<venue>-<id>.html (the id is hex).
SETLIST_PAGE = re.compile(r"^https?://(?:www\.)?setlist\.fm/setlist/[^\s?#]+?-([0-9a-f]+)\.html(?:[?#].*)?$", re.I)


def page_artist_of(url: str) -> str | None:
    """The artist named by a setlist.fm page URL's slug ('zed-fox' -> 'Zed Fox'); None for any other URL."""
    if setlist_id_of(url) is None:
        return None
    slug = (url or "").strip().split("/setlist/", 1)[1].split("/", 1)[0]
    return " ".join(w.capitalize() for w in slug.split("-") if w) or None


def setlist_id_of(url: str) -> str | None:
    """The setlist id in a setlist.fm page URL; None for any other URL."""
    m = SETLIST_PAGE.match((url or "").strip())
    return m.group(1) if m else None


@dataclass(frozen=True)
class SetlistResult:
    found: bool
    match: Literal["exact date", "within a day", "none", "unavailable", "failed"]
    artist: str
    date: str
    venue: str | None
    setlist: Setlist | None = None
    reason: str | None = None

    @property
    def url(self) -> str | None:
        """The setlist.fm page when found."""
        return self.setlist.url if self.setlist else None


def setlist_row(s: Mapping[str, Any]) -> Setlist:
    """A Setlist from one setlist.fm API row (eventDate dd-MM-yyyy flipped to yyyy-MM-dd)."""
    return Setlist(
        id=str(s.get("id") or ""),
        url=str(s.get("url") or ""),
        date="-".join(reversed((s.get("eventDate") or "").split("-"))),
        venue=(s.get("venue") or {}).get("name") or "",
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


@dataclass(frozen=True)
class SyncResult:
    """What sync_setlist_playlist did: the playlist, the songs that landed, the ones swapped for the version the
    artist originally released, the ones Spotify does not have."""

    status: Literal["created", "updated", "dry_run", "unavailable", "failed"]
    name: str
    total: int
    url: str | None = None
    id: str | None = None
    added: int = 0
    already_there: int = 0
    missing: tuple[str, ...] = ()
    teaser_url: str | None = None
    reason: str | None = None
    swapped: tuple[str, ...] = ()
    rebuilt: bool = False  # the playlist held another page's songs and now holds only this page's

    @property
    def on_spotify(self) -> int:
        """Songs found on Spotify (in the playlist now: new, already there, or swapped for the original version)."""
        return self.added + self.already_there + len(self.swapped)

    def to_json(self) -> dict[str, Any]:
        """The setlist playlist record."""
        rec: dict[str, Any] = {
            "status": self.status,
            "name": self.name,
            "songs": self.total,
            "url": self.url,
            "id": self.id,
            "added": self.added,
            "already_there": self.already_there,
            "not_on_spotify": list(self.missing),
            "swapped": list(self.swapped),
            "rebuilt": self.rebuilt,
            "teaser_url": self.teaser_url,
        }
        if self.reason is not None:
            rec["error"] = self.reason
        return rec
