"""Spotify artist resolution and the teaser result (unit text-music). Pure."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from concert_mcp.result import FixInput
from concert_mcp.text import titles

TEASER_FOLDER = "Concerts / Teasers (move it there in the Spotify app; the API cannot)"


@dataclass(frozen=True)
class Artist:
    name: str
    id: str
    url: str | None


class NoArtistMatch(Exception):
    """No Spotify artist is close enough to the name asked for."""

    def __init__(self, query: str, top_names: Sequence[str]) -> None:
        self.query = query
        self.top_names = tuple(top_names)
        if self.top_names:
            msg = f"no close Spotify artist match | artist='{query}' top_results='{', '.join(self.top_names[:5])}'"
        else:
            msg = f"no Spotify artist matches | artist='{query}'"
        super().__init__(msg)


@dataclass(frozen=True)
class TeaserResult:
    status: Literal["created", "updated", "none", "dry_run", "unavailable", "timeout", "failed"]
    artist: Artist | None
    date: str
    name: str | None
    liked_count: int
    url: str | None = None
    id: str | None = None
    added: int = 0
    already_there: int = 0
    tracks: tuple[str, ...] = ()
    exists: bool = False
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        """The legacy `teaser` record."""
        artist = {"name": self.artist.name, "id": self.artist.id, "url": self.artist.url} if self.artist else None
        rec: dict[str, Any] = {
            "artist": artist,
            "date": self.date,
            "name": self.name,
            "liked_count": self.liked_count,
            "tracks": list(self.tracks),
            "created": self.status == "created",
        }
        if self.status == "none":
            who = self.artist.name if self.artist else "them"
            rec["reply"] = f"No teaser: you have not liked any songs by {who} yet."
        elif self.status == "dry_run":
            rec["dry_run"] = True
            rec["exists"] = self.exists
        elif self.status in ("created", "updated"):
            verb = "created" if self.status == "created" else "updated"
            rec.update(
                {
                    "id": self.id,
                    "url": self.url,
                    "added": self.added,
                    "already_there": self.already_there,
                    "folder": TEASER_FOLDER,
                    "reply": f"Teaser playlist {verb}: {self.name} ({self.liked_count} liked songs) {self.url}",
                }
            )
        else:  # unavailable, timeout, failed
            rec["ok"] = False
            rec["error"] = self.reason
        return rec


def _artist_from(a: Mapping[str, Any]) -> Artist:
    return Artist(name=a.get("name"), id=a.get("id"), url=(a.get("external_urls") or {}).get("spotify"))


def _followers(a: Mapping[str, Any]) -> int:
    return (a.get("followers") or {}).get("total", 0) or 0


def track_row(track: Mapping[str, Any], added_at: str | None = None) -> dict[str, Any]:
    """The flat track record: name, artists, artist_ids, album, uri, url, duration_ms[, added_at]."""
    artists = track.get("artists") or []
    row: dict[str, Any] = {
        "name": track.get("name"),
        "artists": [a.get("name") for a in artists],
        "artist_ids": [a.get("id") for a in artists],
        "album": (track.get("album") or {}).get("name"),
        "uri": track.get("uri"),
        "url": (track.get("external_urls") or {}).get("spotify"),
        "duration_ms": track.get("duration_ms"),
    }
    if added_at is not None:
        row["added_at"] = added_at
    return row


def playlist_row(pl: Mapping[str, Any]) -> dict[str, Any]:
    """The flat playlist record (track total from `items` or `tracks`)."""
    # The 2025 Web API renamed a playlist's `tracks` to `items`; accept both.
    owner = pl.get("owner") or {}
    return {
        "name": pl.get("name"),
        "id": pl.get("id"),
        "url": (pl.get("external_urls") or {}).get("spotify"),
        "public": pl.get("public"),
        "tracks": (pl.get("items") or pl.get("tracks") or {}).get("total"),
        "owner": owner.get("display_name") or owner.get("id"),
        "owner_id": owner.get("id"),
        "description": pl.get("description"),
    }


def item_track(entry: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """entry['item'] or entry['track'] when it is a track."""
    track = entry.get("item") or entry.get("track")
    if track and track.get("type", "track") == "track":
        return track
    return None


def pick_artist(query: str, items: Sequence[Mapping[str, Any]]) -> tuple[Artist, bool]:
    """(artist, corrected): exact normalized match by followers, else word-subset match; NoArtistMatch otherwise."""
    if not items:
        raise NoArtistMatch(query, [])
    want = titles.normalize_name(query)
    exact = [a for a in items if titles.normalize_name(a.get("name")) == want]
    if exact:
        return _artist_from(max(exact, key=_followers)), False
    # Close = one name's words are a subset of the other's ("zed fox" ~ "Zed Fox & Friends"); a single shared
    # word is not enough ("ZZZ Test Band" must not become "Band of Horses").
    want_words = set(want.split())
    close = []
    for a in items:
        words = set(titles.normalize_name(a.get("name")).split())
        if words <= want_words or want_words <= words:
            close.append(a)
    if not close:
        raise NoArtistMatch(query, [a.get("name", "") for a in items])
    return _artist_from(max(close, key=_followers)), True


def liked_hits(rows: Sequence[Mapping[str, Any]], artist: Artist) -> list[dict[str, Any]]:
    """Liked rows credited to the artist by id, or by normalized name for old caches; order kept."""
    want = titles.normalize_name(artist.name)
    hits: list[dict[str, Any]] = []
    for r in rows:
        by_id = artist.id in (r.get("artist_ids") or [])
        by_name = want in [titles.normalize_name(a) for a in (r.get("artists") or [])]
        if by_id or by_name:
            hits.append(dict(r))
    return hits


def sort_oldest_first(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Sorted by added_at ascending."""
    return sorted((dict(r) for r in rows), key=lambda r: r.get("added_at") or "")


def new_uris(wanted: Sequence[str], have: Iterable[str]) -> list[str]:
    """wanted minus have, order kept."""
    have_set = set(have)
    return [u for u in wanted if u not in have_set]


def validate_track_uris(uris: Sequence[str]) -> list[str]:
    """Stripped uris; FixInput when one is not spotify:track:<id>."""
    clean = [u.strip() for u in uris if u and u.strip()]
    bad = [u for u in clean if not u.startswith("spotify:track:")]
    if bad:
        raise FixInput(f"not track uris: {', '.join(bad)}", "use spotify:track:<id>")
    return clean


def find_playlist_named(playlists: Iterable[Mapping[str, Any]], name: str) -> Mapping[str, Any] | None:
    """The playlist whose normalized name equals the normalized name asked for."""
    want = titles.normalize_name(name)
    return next((pl for pl in playlists if titles.normalize_name(pl.get("name")) == want), None)
