"""Names: playlists, topics, the teaser description (unit text-render)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

# The house form for the Concerts folders (Teasers and Setlists alike): "YYYY-MM-DD | Artist".
CONCERT_NAME = "{date} | {artist}"
# An older dated name: "2026/11/14 - Artist", "2026-11-14 Artist", "2026-11-14 | Artist".
OLD_CONCERT_NAME = re.compile(r"^\s*(\d{4})[/-](\d{2})[/-](\d{2})\s*(?:[-|]\s*)?(.+?)\s*$")


def playlist_name(date: str, artist: str) -> str:
    """'YYYY-MM-DD | Artist'."""
    return CONCERT_NAME.format(date=date, artist=artist)


def canonical_concert_name(name: str | None) -> str | None:
    """The house form of a dated playlist name, or None when the name carries no leading date."""
    m = OLD_CONCERT_NAME.match(name or "")
    if not m:
        return None
    return playlist_name(f"{m.group(1)}-{m.group(2)}-{m.group(3)}", m.group(4))


def topic_name(start: datetime, title: str, location: str) -> str:
    """'YYYY-MM-DD | Title | Venue' (venue is the location before the first comma)."""
    return f"{start.strftime('%Y-%m-%d')} | {title} | {location.split(',')[0].strip()}"


def teaser_description(artist: str, date: str) -> str:
    """The teaser playlist's description."""
    return f"Teaser for {artist} on {date}: every liked song by them, in the order they were liked."


# The two playlists of one show share the name; the description's first word tells them apart. A setlist
# playlist's description starts with this marker, a teaser's with "Teaser for".
SETLIST_MARK = "Setlist:"
SETLIST_LINK = "Setlist playlist: "


def setlist_description(artist: str, date: str, setlistfm_url: str, teaser_url: str | None = None) -> str:
    """The setlist playlist's description: the show, the setlist.fm page, and the teaser playlist when one exists."""
    text = f"{SETLIST_MARK} {artist} on {date}, in setlist order, from {setlistfm_url}"
    if teaser_url:
        text += f" Teaser playlist: {teaser_url}"
    return text


def is_setlist_playlist(pl: Mapping[str, Any]) -> bool:
    """Whether a Spotify playlist row is a setlist playlist (description starts with the marker)."""
    return str(pl.get("description") or "").lstrip().startswith(SETLIST_MARK)


def with_setlist_link(description: str | None, url: str) -> str:
    """The description with `Setlist playlist: <url>` appended once."""
    text = (description or "").rstrip()
    if SETLIST_LINK in text:
        return text
    return f"{text} {SETLIST_LINK}{url}".strip()
