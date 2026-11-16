"""The twelve side-effect boundaries. Flows depend on these; adapters implement them; tests/fakes.py fakes them."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar

from concert_mcp.text.description import PlainDescription
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
        description: PlainDescription | None,
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
        description: PlainDescription | None = None,
    ) -> None: ...

    def delete_event(self, calendar_id: str, event_id: str) -> None: ...


class TasksApi(Protocol):
    def insert_task(self, task_list: str, *, title: str, notes: str, due: str) -> dict[str, Any]: ...

    def get_task(self, task_list: str, task_id: str) -> dict[str, Any]: ...

    def delete_task(self, task_list: str, task_id: str) -> None: ...

    def get_task_list(self, task_list: str) -> dict[str, Any]: ...


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

    def remove_items(self, playlist_id: str, uris: Sequence[str]) -> None: ...

    def replace_item(self, playlist_id: str, *, position: int, old_uri: str, new_uri: str) -> None: ...

    def rename_playlist(self, playlist_id: str, name: str) -> None: ...

    def update_playlist_details(self, playlist_id: str, *, description: str) -> None: ...

    def unfollow_playlist(self, playlist_id: str) -> None: ...

    def artist_albums(self, artist_id: str) -> list[dict[str, Any]]: ...

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


class PartifulApi(Protocol):
    def signed_in(self) -> bool: ...

    def send_code(self, phone: str) -> None: ...

    def login(self, phone: str, code: str) -> None: ...

    def me(self) -> dict[str, Any]: ...

    def create_event(self, event: Mapping[str, Any]) -> str: ...

    def find_place(self, query: str) -> dict[str, Any] | None: ...

    def set_location(self, event_id: str, location_info: Mapping[str, Any]) -> None: ...

    def event_links(self, event_id: str) -> list[dict[str, Any]]: ...

    def set_links(self, event_id: str, fields: Sequence[Mapping[str, Any]]) -> None: ...

    def rsvp_going(self, event_id: str, name: str, timezone: str) -> None: ...

    def upload_image(self, data: bytes, content_type: str) -> str: ...

    def download(self, url: str) -> bytes: ...


class VenueLookup(Protocol):
    def find_venue(self, query: str) -> VenueHit | None: ...


class FilmLookup(Protocol):
    def film_url(self, film: str, year: int | None) -> str | None: ...


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

    def names(self, directory: Path) -> list[str]: ...


class Clock(Protocol):
    def now(self) -> datetime: ...

    def time(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...
