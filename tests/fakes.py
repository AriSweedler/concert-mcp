"""Protocol-level fakes for every side effect, plus a fake subprocess runner and a fake urlopen.

Nothing here touches the network, the keychain, the home directory, gws or openclaw.
"""

from __future__ import annotations

import base64
import copy
import email.message
import io
import json
import re
import time as _time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from concert_mcp.proc import RunResult
from concert_mcp.result import ProviderError
from concert_mcp.text.routes import ChatTarget

GWS_REFUSED = "Google Calendar refused the request. Admin: check `gws auth` on the host."
SPOTIFY_REFUSED = "Spotify refused the request. Admin: check the host log."
SETLISTFM_REFUSED = "setlist.fm refused the request. Admin: check the host log."


def _normalize(name: object) -> str:
    return " ".join(str(name or "").lower().replace("&", "and").split())


def _parse_when(value: Mapping[str, Any], tz: timezone | Any) -> datetime:
    if value.get("dateTime"):
        return datetime.fromisoformat(str(value["dateTime"]).replace("Z", "+00:00"))
    return datetime.fromisoformat(str(value.get("date"))).replace(tzinfo=tz)


def fake_html_link(event_id: str, calendar_id: str) -> str:
    """The API's htmlLink form: eid is base64 of '<eventId> <calendarId>' (padding stripped)."""
    b64 = base64.urlsafe_b64encode(f"{event_id} {calendar_id}".encode()).decode().rstrip("=")
    return f"https://www.google.com/calendar/event?eid={b64}"


class FakeCalendar:
    """Calendar with in-memory events per calendar id. `fail` names a method that raises ProviderError('gws')."""

    def __init__(
        self,
        calendars: Sequence[Mapping[str, Any]] | None = None,
        events: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        fail: str | None = None,
    ) -> None:
        self.calendars: list[dict[str, Any]] = [dict(c) for c in (calendars or [])]
        self.events: dict[str, list[dict[str, Any]]] = {k: [dict(e) for e in v] for k, v in (events or {}).items()}
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.inserted: list[dict[str, Any]] = []
        self.patched: list[tuple[str, str, str]] = []
        self.deleted: list[tuple[str, str]] = []
        self._n = 0

    def _record(self, method: str, **args: Any) -> None:
        self.calls.append((method, args))
        if self.fail == method:
            raise ProviderError("gws", "fake failure", GWS_REFUSED)

    def _next_id(self) -> str:
        self._n += 1
        return f"evt{self._n}"

    def seed_event(
        self,
        calendar_id: str,
        *,
        summary: str,
        start: datetime,
        end: datetime | None = None,
        location: str = "",
        description: str = "",
        status: str = "confirmed",
        all_day: bool = False,
    ) -> dict[str, Any]:
        """Put an event in a calendar as the API would return it; returns the event dict."""
        event_id = self._next_id()
        end = end or start + timedelta(hours=2)
        if all_day:
            when = {"start": {"date": start.date().isoformat()}, "end": {"date": end.date().isoformat()}}
        else:
            when = {"start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}}
        event = {
            "id": event_id,
            "status": status,
            "summary": summary,
            "location": location,
            "description": description,
            "htmlLink": fake_html_link(event_id, calendar_id),
            **when,
        }
        self.events.setdefault(calendar_id, []).append(event)
        return event

    def list_calendars(self) -> list[dict[str, Any]]:
        self._record("list_calendars")
        return [dict(c) for c in self.calendars]

    def list_events(
        self,
        calendar_id: str,
        *,
        time_min: datetime,
        time_max: datetime,
        q: str = "",
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        self._record(
            "list_events", calendar_id=calendar_id, time_min=time_min, time_max=time_max, q=q, max_results=max_results
        )
        out: list[dict[str, Any]] = []
        needle = q.strip().lower()
        for e in self.events.get(calendar_id, []):
            start = _parse_when(e.get("start") or {}, time_min.tzinfo or UTC)
            if not (time_min <= start < time_max):
                continue
            haystack = " ".join(str(e.get(k) or "") for k in ("summary", "description", "location")).lower()
            if needle and needle not in haystack:
                continue
            out.append(copy.deepcopy(e))
        return out[:max_results]

    def get_event(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        self._record("get_event", calendar_id=calendar_id, event_id=event_id)
        for e in self.events.get(calendar_id, []):
            if e["id"] == event_id:
                return copy.deepcopy(e)
        raise ProviderError("gws", f"gws calendar events exited 1: no such event {event_id}", GWS_REFUSED)

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
    ) -> dict[str, Any]:
        self._record(
            "insert_event",
            calendar_id=calendar_id,
            summary=summary,
            start=start,
            end=end,
            location=location,
            description=description,
            attendees=list(attendees),
        )
        event_id = self._next_id()
        event = {
            "id": event_id,
            "status": "confirmed",
            "summary": summary,
            "location": location,
            "description": description or "",
            "attendees": [{"email": a} for a in attendees],
            "htmlLink": fake_html_link(event_id, calendar_id),
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
        self.events.setdefault(calendar_id, []).append(event)
        self.inserted.append({"calendar_id": calendar_id, **copy.deepcopy(event)})
        return copy.deepcopy(event)

    def patch_description(self, calendar_id: str, event_id: str, description: str) -> None:
        self._record("patch_description", calendar_id=calendar_id, event_id=event_id, description=description)
        for e in self.events.get(calendar_id, []):
            if e["id"] == event_id:
                e["description"] = description
                self.patched.append((calendar_id, event_id, description))
                return
        raise ProviderError("gws", f"gws calendar events exited 1: no such event {event_id}", GWS_REFUSED)

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        self._record("delete_event", calendar_id=calendar_id, event_id=event_id)
        rows = self.events.get(calendar_id, [])
        kept = [e for e in rows if e["id"] != event_id]
        if len(kept) == len(rows):
            raise ProviderError("gws", f"gws calendar events exited 1: no such event {event_id}", GWS_REFUSED)
        self.events[calendar_id] = kept
        self.deleted.append((calendar_id, event_id))


class FakeSpotify:
    """SpotifyApi over configured artists, liked rows, playlists and items. `fail='liked'` raises spotify-timeout."""

    def __init__(
        self,
        artists: Sequence[Mapping[str, Any]] | None = None,
        liked: Sequence[Mapping[str, Any]] | None = None,
        playlists: Sequence[Mapping[str, Any]] | None = None,
        items: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        fail: str | None = None,
    ) -> None:
        self.artists: list[dict[str, Any]] = [dict(a) for a in (artists or [])]
        self.liked: list[dict[str, Any]] = [dict(r) for r in (liked or [])]
        self.playlists: list[dict[str, Any]] = [dict(p) for p in (playlists or [])]
        self.items: dict[str, list[dict[str, Any]]] = {k: [dict(r) for r in v] for k, v in (items or {}).items()}
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.created: list[dict[str, Any]] = []
        self.added: list[tuple[str, list[str]]] = []
        self.deadlines: list[tuple[str, float | None]] = []  # (method, deadline) per paged read
        self.renamed: list[tuple[str, str]] = []
        self.unfollowed: list[str] = []
        self._n = 0

    def _record(self, method: str, **args: Any) -> None:
        self.calls.append((method, args))
        if self.fail == method:
            if method == "liked":
                raise ProviderError("spotify-timeout", "Spotify took longer than 150 s", SPOTIFY_REFUSED)
            raise ProviderError("spotify", f"Spotify API refused | method='{method}' status='500'", SPOTIFY_REFUSED)

    def me(self) -> dict[str, Any]:
        self._record("me")
        return {
            "id": "user1",
            "display_name": "Fake User",
            "external_urls": {"spotify": "https://open.spotify.com/user/user1"},
        }

    def search_artists(self, q: str, limit: int = 10) -> list[dict[str, Any]]:
        self._record("search_artists", q=q, limit=limit)
        want = _normalize(q)
        hits = [a for a in self.artists if want in _normalize(a.get("name")) or _normalize(a.get("name")) in want]
        return [copy.deepcopy(a) for a in hits[:limit]]

    def liked_rows(self, refresh: bool = False, deadline: float | None = None) -> list[dict[str, Any]]:
        self.calls.append(("liked_rows", {"refresh": refresh, "deadline": deadline}))
        if self.fail == "liked":
            raise ProviderError("spotify-timeout", "Spotify took longer than 150 s", SPOTIFY_REFUSED)
        return [copy.deepcopy(r) for r in self.liked]

    def my_playlists(self, deadline: float | None = None) -> list[dict[str, Any]]:
        self._record("my_playlists")
        self.deadlines.append(("my_playlists", deadline))
        return [copy.deepcopy(p) for p in self.playlists]

    def playlist(self, playlist_id: str, fields: str) -> dict[str, Any]:
        self._record("playlist", playlist_id=playlist_id, fields=fields)
        for p in self.playlists:
            if p.get("id") == playlist_id:
                return copy.deepcopy(p)
        raise ProviderError(
            "spotify",
            f"Spotify API refused | method='GET' path='/playlists/{playlist_id}' status='404'",
            SPOTIFY_REFUSED,
        )

    def playlist_items(self, playlist_id: str, deadline: float | None = None) -> list[dict[str, Any]]:
        self._record("playlist_items", playlist_id=playlist_id)
        self.deadlines.append(("playlist_items", deadline))
        return [copy.deepcopy(r) for r in self.items.get(playlist_id, [])]

    def create_playlist(self, *, name: str, description: str, public: bool) -> dict[str, Any]:
        self._record("create_playlist", name=name, description=description, public=public)
        self._n += 1
        playlist_id = f"pl{self._n}"
        pl = {
            "id": playlist_id,
            "name": name,
            "description": description,
            "public": public,
            "owner": {"id": "user1", "display_name": "Fake User"},
            "external_urls": {"spotify": f"https://open.spotify.com/playlist/{playlist_id}"},
            "items": {"total": 0},
        }
        self.playlists.append(pl)
        self.items[playlist_id] = []
        self.created.append(copy.deepcopy(pl))
        return copy.deepcopy(pl)

    def add_items(self, playlist_id: str, uris: Sequence[str]) -> None:
        self._record("add_items", playlist_id=playlist_id, uris=list(uris))
        rows = self.items.setdefault(playlist_id, [])
        for u in uris:
            rows.append({"name": u, "artists": [], "artist_ids": [], "uri": u, "url": None, "added_at": None})
        for p in self.playlists:
            if p.get("id") == playlist_id:
                p["items"] = {"total": len(rows)}
        self.added.append((playlist_id, list(uris)))

    def rename_playlist(self, playlist_id: str, name: str) -> None:
        self._record("rename_playlist", playlist_id=playlist_id, name=name)
        for p in self.playlists:
            if p.get("id") == playlist_id:
                p["name"] = name
        self.renamed.append((playlist_id, name))

    def unfollow_playlist(self, playlist_id: str) -> None:
        self._record("unfollow_playlist", playlist_id=playlist_id)
        self.playlists = [p for p in self.playlists if p.get("id") != playlist_id]
        self.unfollowed.append(playlist_id)

    @property
    def user_id(self) -> str | None:
        return "user1"


class FakeSetlistFm:
    """SetlistFmApi with responses keyed (artist, date, year); a missing key is a 404 (None)."""

    def __init__(
        self, responses: Mapping[tuple[Any, ...], Mapping[str, Any] | None] | None = None, fail: bool = False
    ) -> None:
        self.responses: dict[tuple[Any, ...], Mapping[str, Any] | None] = dict(responses or {})
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def search_setlists(
        self,
        artist: str,
        *,
        date: str | None = None,
        year: str | None = None,
        page: int = 1,
    ) -> dict[str, Any] | None:
        self.calls.append({"artist": artist, "date": date, "year": year, "page": page})
        if self.fail:
            raise ProviderError(
                "setlistfm", "setlist.fm refused | path='/search/setlists' status='500'", SETLISTFM_REFUSED
            )
        res = self.responses.get((artist, date, year))
        return copy.deepcopy(dict(res)) if res is not None else None


class FakeChat:
    """Chat that records sends and renames. The first `fail_sends` sends report the 3-tries failure."""

    def __init__(self, fail_sends: int = 0, rename_status: str = "topic renamed to {name}") -> None:
        self.fail_sends = fail_sends
        self.rename_status = rename_status
        self.sent: list[tuple[ChatTarget, str]] = []
        self.attempts: list[tuple[ChatTarget, str]] = []
        self.renamed: list[tuple[str, str, str]] = []

    def send(self, target: ChatTarget, text: str) -> str:
        self.attempts.append((target, text))
        if self.fail_sends > 0:
            self.fail_sends -= 1
            return "NOT SENT: openclaw message send failed 3 times: boom"
        self.sent.append((target, text))
        topic = f" topic {target.thread}" if target.thread is not None else ""
        return f"SENT to {target.channel} {target.chat}{topic}"

    def rename_topic(self, chat_id: str, thread_id: str, name: str) -> str:
        self.renamed.append((chat_id, thread_id, name))
        return self.rename_status.format(name=name)


class FakeContext:
    """LiveContext with one fixed route and one fixed text."""

    def __init__(self, route: Mapping[str, Any] | None = None, session_id: str | None = None, text: str = "") -> None:
        self.route = dict(route) if route is not None else None
        self.session_id = session_id
        self.text = text
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def live_route(self, now_s: float) -> tuple[str | None, dict[str, Any] | None]:
        self.calls.append(("live_route", {"now_s": now_s}))
        if self.route is None:
            return None, None
        return self.session_id, copy.deepcopy(self.route)

    def recent_text(self, now_s: float, session_id: str | None) -> str:
        self.calls.append(("recent_text", {"now_s": now_s, "session_id": session_id}))
        return self.text


class MemoryState:
    """StateStore in dicts: files, mtimes (from `clock.time()` when a clock is set, else time.time()), modes."""

    def __init__(self, clock: Any | None = None) -> None:
        self.clock = clock
        self.files: dict[Path, Any] = {}
        self.mtimes: dict[Path, float] = {}
        self.modes: dict[Path, int] = {}

    def _now(self) -> float:
        return float(self.clock.time()) if self.clock is not None else _time.time()

    def read_json(self, path: Path, default: Any) -> Any:
        path = Path(path)
        if path not in self.files:
            return default
        return copy.deepcopy(self.files[path])

    def write_json(self, path: Path, obj: Any, *, mode: int | None = None) -> None:
        path = Path(path)
        self.files[path] = json.loads(json.dumps(obj, ensure_ascii=False))
        self.mtimes[path] = self._now()
        if mode is not None:
            self.modes[path] = mode

    def remove(self, path: Path) -> None:
        path = Path(path)
        self.files.pop(path, None)
        self.mtimes.pop(path, None)
        self.modes.pop(path, None)

    def mtime(self, path: Path) -> float | None:
        return self.mtimes.get(Path(path))


class FakeClock:
    """Clock frozen at `now`; sleep() records the seconds and advances the clock."""

    def __init__(self, now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError("FakeClock needs an aware datetime")
        self._now = now
        self.slept: list[float] = []

    def now(self) -> datetime:
        return self._now.astimezone(UTC)

    def time(self) -> float:
        return self._now.timestamp()

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


RunnerEntry = tuple[int, str, str] | Callable[..., RunResult]


class FakeRunner:
    """A Runner backed by a table keyed by argv prefix. Unknown argv is an AssertionError naming it."""

    def __init__(self, table: Mapping[Sequence[str], RunnerEntry] | None = None) -> None:
        self.table: dict[tuple[str, ...], RunnerEntry] = {tuple(k): v for k, v in (table or {}).items()}
        self.calls: list[tuple[tuple[str, ...], Mapping[str, str] | None, str | Path | None]] = []

    def add(self, prefix: Sequence[str], entry: RunnerEntry) -> None:
        self.table[tuple(prefix)] = entry

    def __call__(
        self,
        cmd: Sequence[str],
        timeout_s: float = 60,
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | Path | None = None,
        redact_from: int | None = None,
    ) -> RunResult:
        argv = tuple(str(c) for c in cmd)
        self.calls.append((argv, dict(env) if env is not None else None, cwd))
        best: tuple[str, ...] | None = None
        for prefix in self.table:
            if argv[: len(prefix)] == prefix and (best is None or len(prefix) > len(best)):
                best = prefix
        if best is None:
            raise AssertionError(f"FakeRunner: no entry for argv {list(argv)!r}")
        entry = self.table[best]
        if callable(entry):
            return entry(list(argv), {"timeout_s": timeout_s, "env": env, "cwd": cwd})
        rc, out, err = entry
        return RunResult(rc, out, err, None)


def http_error(
    code: int, body: bytes | str = b"", headers: Mapping[str, str] | None = None, url: str = ""
) -> urllib.error.HTTPError:
    """An HTTPError whose read() returns `body`, for FakeUrlopen routes."""
    data = body.encode("utf-8") if isinstance(body, str) else body
    msg = email.message.Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError(url, code, f"HTTP {code}", msg, io.BytesIO(data))


class FakeResponse:
    """What FakeUrlopen returns: a context manager with read(), headers and status."""

    def __init__(self, body: bytes, status: int = 200, headers: Mapping[str, str] | None = None) -> None:
        self._body = body
        self.status = status
        self.headers = email.message.Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


UrlopenEntry = (
    bytes
    | str
    | Mapping[str, Any]
    | Sequence[Any]
    | urllib.error.HTTPError
    | Exception
    | Callable[..., Any]
    | FakeResponse
)


class FakeUrlopen:
    """A urlopen replacement with routes keyed (method, url_prefix). Records (method, url, headers, body)."""

    def __init__(self, routes: Mapping[tuple[str, str], UrlopenEntry] | None = None) -> None:
        self.routes: dict[tuple[str, str], UrlopenEntry] = dict(routes or {})
        self.requests: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def add(self, method: str, url_prefix: str, entry: UrlopenEntry) -> None:
        self.routes[(method.upper(), url_prefix)] = entry

    def __call__(
        self, req: urllib.request.Request | str, data: bytes | None = None, timeout: float | None = None
    ) -> Any:
        if isinstance(req, str):
            url, headers, body = req, {}, data
            method = "POST" if data is not None else "GET"
        else:
            url = req.full_url
            method = req.get_method().upper()
            headers = {k: v for k, v in req.header_items()}
            body = req.data if isinstance(req.data, bytes) else data
        logged = {k: ("<redacted>" if k.lower() == "authorization" else v) for k, v in headers.items()}
        self.requests.append((method, url, logged, body))
        best: tuple[str, str] | None = None
        for key in self.routes:
            if key[0] == method and url.startswith(key[1]) and (best is None or len(key[1]) > len(best[1])):
                best = key
        if best is None:
            raise AssertionError(f"FakeUrlopen: no route for {method} {url}")
        entry = self.routes[best]
        if callable(entry) and not isinstance(entry, Exception):
            entry = entry(req)
        if isinstance(entry, Exception):
            raise entry
        if isinstance(entry, FakeResponse):
            return entry
        if isinstance(entry, bytes):
            return FakeResponse(entry)
        if isinstance(entry, str):
            return FakeResponse(entry.encode("utf-8"))
        return FakeResponse(json.dumps(entry).encode("utf-8"), headers={"Content-Type": "application/json"})


def ms(dt: datetime) -> int:
    """Epoch milliseconds, the unit OpenClaw's session store uses."""
    return int(dt.timestamp() * 1000)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def plain(s: str) -> str:
    """Strip ANSI escapes from captured stderr."""
    return _ANSI_RE.sub("", s)


class FakeVenueLookup:
    """VenueLookup with canned hits keyed by a lowercase substring of the query; records every query."""

    def __init__(self, hits: Mapping[str, Any] | None = None, *, error: Exception | None = None) -> None:
        self.hits = dict(hits or {})
        self.error = error
        self.queries: list[str] = []

    def find_venue(self, query: str) -> Any:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        q = query.lower()
        for needle, hit in self.hits.items():
            if needle.lower() in q:
                return hit
        return None
