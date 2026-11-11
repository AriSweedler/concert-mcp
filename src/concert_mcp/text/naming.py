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


# A Spotify description is one line (the API refuses a newline, and a link in it cannot be clicked), so its parts
# are set apart by a dot and it ends with where the playlist came from, for whoever it is shared with. The name
# already carries the artist and the date; the description says only what the name does not.
SEPARATOR = " · "
CREDIT = "Created by AriSweedler/concert-mcp"


def teaser_description(artist: str, owner: str) -> str:
    """The teaser playlist's description: whose liked songs these are."""
    return f"{owner}'s liked songs from {artist}{SEPARATOR}{CREDIT}"


# The two playlists of one show share the name; the description's first word tells them apart. A setlist
# playlist's description starts with "Setlist," ("Setlist:" on one made before 2026-11-10), a teaser's does not.
SETLIST_READ = "Setlist, according to "
SETLIST_MARKS = ("Setlist,", "Setlist:")
SETLIST_LINK = "Setlist playlist: "  # the calendar event's line, not a playlist's


def setlist_description(setlistfm_url: str, read_date: str) -> str:
    """The setlist playlist's description: the page the songs came from and the day it was read. The page fills in
    over weeks, so the day moves forward each time the playlist is brought up to date."""
    return f"{SETLIST_READ}{setlistfm_url} on {read_date}{SEPARATOR}{CREDIT}"


def is_setlist_playlist(pl: Mapping[str, Any]) -> bool:
    """Whether a Spotify playlist row is a setlist playlist (description starts with a marker, old or new)."""
    return str(pl.get("description") or "").lstrip().startswith(SETLIST_MARKS)


def has_dated_setlist_description(pl: Mapping[str, Any]) -> bool:
    """Whether the row carries the description with a read date: only those are brought up to date, so a playlist
    made before 2026-11-10 keeps the text it was given."""
    return str(pl.get("description") or "").lstrip().startswith(SETLIST_READ)
