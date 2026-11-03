"""Matching a setlist.fm song to a Spotify track (unit text-music). Pure.

setlist.fm and Spotify spell the same thing differently: `Tomboys Don’t Cry` with a curly apostrophe against
`Tomboys Don't Cry`, `K.Flay` against `K Flay`, `Song (acoustic)` against `Song - Acoustic`. Every comparison here
runs on the loose form of a name: lower case, accents folded, nothing but letters and digits kept.

Spotify also has a song several times over (`Black Wave`, `Black Wave - Seattle Sessions`, the single, the album): the
pick among the matches is the version the artist originally released, by `rank_key`, and `stale_rows` finds the
playlist rows an earlier run filled with another version.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from concert_mcp.text.setlists import Song

# A trailing parenthetical carries stage detail on setlist.fm (`(acoustic)`, `(with Other Band)`) and a version on
# Spotify (`(feat. X)`, `(Remastered)`); Spotify also writes a version after a dash (`Song - Live`, `Song - 2011
# Remaster`).
_TRAILING_PAREN_RE = re.compile(r"\s*[(\[][^()\[\]]*[)\]]\s*$")
_DASH_SUFFIX_RE = re.compile(r"\s+-\s+.*$")
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")
# An apostrophe, straight or curly, joins a word (`Don’t` -> `dont`); any other mark splits one (`K.Flay` -> `k flay`).
_APOSTROPHES_RE = re.compile(r"['’‘`´]")
# Spotify's `album_type`, in the order the artist's original release is likely to carry: the song's album, then a
# single (a pre-release or a later re-cut), then a compilation (a best-of, a soundtrack, a live set).
_ALBUM_ORDER = {"album": 0, "single": 1, "compilation": 2}
_NO_DATE = "9999-12-31"


def loose_words(s: object) -> list[str]:
    """The words of a name in loose form: lower case, accents folded, punctuation gone (`K.Flay` -> ['k', 'flay'])."""
    folded = unicodedata.normalize("NFKD", _APOSTROPHES_RE.sub("", str(s or "")))
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch)).lower()
    return [w for w in _NON_ALNUM_RE.split(ascii_only) if w]


def loose_name(s: object) -> str:
    """The loose form as one token: `K.Flay`, `K Flay` and `kflay` are all 'kflay'."""
    return "".join(loose_words(s))


def core_name(name: str) -> str:
    """The song name without trailing parentheticals and a ` - version` suffix; the name itself when that would leave
    nothing (`(Intro)` stays `(Intro)`)."""
    core = _DASH_SUFFIX_RE.sub("", name.strip())
    while True:
        shorter = _TRAILING_PAREN_RE.sub("", core).strip()
        if shorter == core:
            break
        core = shorter
    return core or name.strip()


def plain_query(song: Song, artist: str) -> str:
    """The unfiltered Spotify search for one setlist song: `<core name> <artist>` (the cover's artist for a cover)."""
    return f"{core_name(song.name)} {song.artist or artist}"


def names_match(song_name: str, track_name: str) -> bool:
    """Loose equality of the whole names or of their cores: `Song (acoustic)` ~ `Song - Acoustic` ~ `Song`, while
    `Hell` !~ `Hello` and `Alive` !~ `Alive Again` (a longer title is another song unless the extra is a version)."""
    whole_a, whole_b = loose_name(song_name), loose_name(track_name)
    if whole_a and whole_a == whole_b:
        return True
    core_a, core_b = loose_name(core_name(song_name)), loose_name(core_name(track_name))
    return bool(core_a) and core_a == core_b


def artist_match(track: Mapping[str, Any], artist: str, artist_id: str | None = None) -> bool:
    """Whether the track credits the artist: by Spotify id when one is known, else by loose name."""
    want = loose_name(artist)
    for a in track.get("artists") or []:
        if artist_id and a.get("id") == artist_id:
            return True
        if want and loose_name(a.get("name")) == want:
            return True
    return False


def version_rank(song_name: str, track_name: str) -> int:
    """How far the track's title is from the plain one: 0 for a plain title (`Black Wave`), 1 for a version the
    setlist names itself (`Song (acoustic)` against `Song - Acoustic`), 2 for a version the setlist does not
    (`Black Wave - Seattle Sessions`, `Giver (softer version)`, `(Sir Sly Remix)`)."""
    track_name = track_name.strip()
    if core_name(track_name) == track_name:
        return 0
    return 1 if loose_name(song_name) == loose_name(track_name) else 2


def album_rank(track: Mapping[str, Any]) -> int:
    """The album type's rank: an album before a single before a compilation; an unknown type last."""
    return _ALBUM_ORDER.get(str((track.get("album") or {}).get("album_type") or "").lower(), len(_ALBUM_ORDER))


def release_key(track: Mapping[str, Any]) -> str:
    """The album's release date as a sortable `YYYY-MM-DD`: Spotify gives `YYYY`, `YYYY-MM` or `YYYY-MM-DD`, and a
    shorter one is padded with the first day of the period; a track without a date sorts last."""
    date = str((track.get("album") or {}).get("release_date") or "").strip()
    if not date:
        return _NO_DATE
    return (date + "-01-01")[:10] if len(date) < 10 else date[:10]


def rank_key(track: Mapping[str, Any], song_name: str) -> tuple[int, int, str]:
    """The sort key that puts the version the artist originally released first: plain title, then album over single
    over compilation, then the earliest release; Spotify's own order breaks the remaining ties."""
    return (version_rank(song_name, str(track.get("name") or "")), album_rank(track), release_key(track))


def pick_track(
    candidates: Sequence[Mapping[str, Any]], song_name: str, artist: str, artist_id: str | None = None
) -> Mapping[str, Any] | None:
    """The candidate that is the song by the artist (loose name match on both) in the version the artist
    originally released (`rank_key`); the first such candidate among equals; None when none is."""
    matching = [
        t for t in candidates if names_match(song_name, str(t.get("name") or "")) and artist_match(t, artist, artist_id)
    ]
    if not matching:
        return None
    return min(matching, key=lambda t: rank_key(t, song_name))


def track_label(track: Mapping[str, Any]) -> str:
    """`Name (Album, YYYY)` for a log line; the album or the year left out when Spotify gave none."""
    album = track.get("album") or {}
    detail = ", ".join(str(x) for x in (album.get("name"), str(album.get("release_date") or "")[:4]) if x)
    name = str(track.get("name") or "")
    return f"{name} ({detail})" if detail else name


def stale_rows(rows: Sequence[Mapping[str, Any]], picks: Sequence[tuple[str, str]]) -> list[tuple[int, str, str, str]]:
    """The playlist rows an earlier run filled with another version of a setlist song, as (position, old uri, new uri,
    song name) in playlist order: a row whose uri is no pick but whose name matches a song whose pick is not in the
    playlist. `picks` is (song name, uri) per found song; `rows` are `track_row` records (name, uri)."""
    pick_uris = {uri for _, uri in picks}
    have = {str(r.get("uri") or "") for r in rows}
    out: list[tuple[int, str, str, str]] = []
    claimed: set[str] = set()
    for i, r in enumerate(rows):
        old = str(r.get("uri") or "")
        if not old or old in pick_uris:
            continue
        for song_name, new in picks:
            if new in have or new in claimed or not names_match(song_name, str(r.get("name") or "")):
                continue
            out.append((i, old, new, song_name))
            claimed.add(new)
            break
    return out


def candidate_summary(candidates: Sequence[Mapping[str, Any]]) -> str:
    """`Name by A, B; Name2 by C` for a log line; `none` when the search returned nothing."""
    if not candidates:
        return "none"
    return "; ".join(
        f"{t.get('name')} by {', '.join(str(a.get('name')) for a in (t.get('artists') or []))}" for t in candidates
    )
