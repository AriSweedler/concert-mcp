"""Names: playlists, topics, the teaser description (unit text-render)."""

from __future__ import annotations

import re
from datetime import datetime

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
