"""Matching a setlist.fm song to a Spotify track (unit text-music). Pure.

setlist.fm and Spotify spell the same thing differently: `Tomboys Don’t Cry` with a curly apostrophe against
`Tomboys Don't Cry`, `K.Flay` against `K Flay`, `Song (acoustic)` against `Song - Acoustic`. Every comparison here
runs on the loose form of a name: lower case, accents folded, nothing but letters and digits kept.
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


def pick_track(
    candidates: Sequence[Mapping[str, Any]], song_name: str, artist: str, artist_id: str | None = None
) -> Mapping[str, Any] | None:
    """The first candidate that is the song by the artist (loose name match on both); None when none is."""
    for t in candidates:
        if names_match(song_name, str(t.get("name") or "")) and artist_match(t, artist, artist_id):
            return t
    return None


def candidate_summary(candidates: Sequence[Mapping[str, Any]]) -> str:
    """`Name by A, B; Name2 by C` for a log line; `none` when the search returned nothing."""
    if not candidates:
        return "none"
    return "; ".join(
        f"{t.get('name')} by {', '.join(str(a.get('name')) for a in (t.get('artists') or []))}" for t in candidates
    )
