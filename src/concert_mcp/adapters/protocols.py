"""The nine side-effect boundaries. Flows depend on these; adapters implement them; tests/fakes.py fakes them."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar

from concert_mcp.text.lineup import GroundedAnswer
from concert_mcp.text.routes import ChatTarget
from concert_mcp.text.venues import VenueHit

T = TypeVar("T")


class Calendar(Protocol):
    def list_calendars(self) -> list[dict[str, Any]]: ...

    def list_events(
        self,
        calendar_id: str,
        *,
        time_min: datetime,
        time_max: datetime,
        q: str = "",
        max_results: int = 50,
    ) -> list[dict[str, Any]]: ...

    def get_event(self, calendar_id: str, event_id: str) -> dict[str, Any]: ...

    def insert_event(
        self,
        calendar_id: str,
        *,
        summary: str,
        start: datetime,
        end: datetime,
        location: str,
        description: str | None,
        attendees: Sequence[str],
    ) -> dict[str, Any]: ...

    def patch_event(
        self,
        calendar_id: str,
        event_id: str,
        *,
        summary: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        location: str | None = None,
        description: str | None = None,
    ) -> None: ...

    def delete_event(self, calendar_id: str, event_id: str) -> None: ...


class SpotifyApi(Protocol):
    def me(self) -> dict[str, Any]: ...

    def search_artists(self, q: str, limit: int = 10) -> list[dict[str, Any]]: ...

    def search_tracks(self, q: str, limit: int = 1) -> list[dict[str, Any]]: ...

    def liked_rows(self, refresh: bool = False, deadline: float | None = None) -> list[dict[str, Any]]: ...

    def my_playlists(self, deadline: float | None = None) -> list[dict[str, Any]]: ...

    def playlist(self, playlist_id: str, fields: str) -> dict[str, Any]: ...

    def playlist_items(self, playlist_id: str, deadline: float | None = None) -> list[dict[str, Any]]: ...

    def create_playlist(self, *, name: str, description: str, public: bool) -> dict[str, Any]: ...

    def add_items(self, playlist_id: str, uris: Sequence[str]) -> None: ...

    def rename_playlist(self, playlist_id: str, name: str) -> None: ...

    def update_playlist_details(self, playlist_id: str, *, description: str) -> None: ...

    def unfollow_playlist(self, playlist_id: str) -> None: ...

    @property
    def user_id(self) -> str | None: ...


class SetlistFmApi(Protocol):
    def search_setlists(
        self,
        artist: str,
        *,
        date: str | None = None,
        year: str | None = None,
        page: int = 1,
    ) -> dict[str, Any] | None: ...

    def get_setlist(self, setlist_id: str) -> dict[str, Any] | None: ...

    def latest_setlist(self, artist: str) -> dict[str, Any] | None: ...


class VenueLookup(Protocol):
    def find_venue(self, query: str) -> VenueHit | None: ...


class GroundedModel(Protocol):
    def ask(self, prompt: str, *, grounded: bool = True) -> GroundedAnswer: ...


class Chat(Protocol):
    def send(self, target: ChatTarget, text: str) -> str: ...

    def rename_topic(self, chat_id: str, thread_id: str, name: str) -> str: ...


class LiveContext(Protocol):
    def live_route(self, now_s: float) -> tuple[str | None, dict[str, Any] | None]: ...

    def recent_text(self, now_s: float, session_id: str | None) -> str: ...


class StateStore(Protocol):
    def read_json(self, path: Path, default: T) -> T: ...

    def write_json(self, path: Path, obj: Any, *, mode: int | None = None) -> None: ...

    def remove(self, path: Path) -> None: ...

    def mtime(self, path: Path) -> float | None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...

    def time(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...
