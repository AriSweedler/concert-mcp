"""Spotify Web API over urllib (unit adapter-music).

A port of the legacy spotify.py client: credentials file, refresh-token flow, one `api()` call with 429/401 retries,
pagination, and the Liked Songs cache. `die()` became ProviderError; logging stays on stderr and never carries a token.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from concert_mcp.adapters.protocols import Clock, StateStore
from concert_mcp.config import Settings
from concert_mcp.log import kv, log_debug, log_info, log_warn
from concert_mcp.result import ProviderError
from concert_mcp.text.artists import item_track, sort_oldest_first, track_row
from concert_mcp.text.reply import provider_reply

ACCOUNTS = "https://accounts.spotify.com"
API = "https://api.spotify.com/v1"
PAGE = 50

# The shortest a cached access token may have left before it is refreshed anyway.
TOKEN_SLACK_S = 60
# Spotify's Retry-After is honoured up to this many seconds; a longer wait is capped.
MAX_RETRY_AFTER_S = 30
ADD_CHUNK = 100


@dataclass
class SpotifyCredentials:
    client_id: str
    client_secret: str
    refresh_token: str
    access_token: str | None = None
    expires_at: float = 0.0
    scope: str | None = None
    user_id: str | None = None

    @classmethod
    def load(cls, path: Path) -> SpotifyCredentials:
        """Read the credentials file; ProviderError('spotify-unconfigured') when absent or unreadable."""
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("credentials file is not a JSON object")
            known = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in raw.items() if k in known})
        except (OSError, ValueError, TypeError) as e:
            raise ProviderError(
                "spotify-unconfigured",
                f"not authorized yet | path='{path}' hint='run: concert-mcp spotify auth'",
                "",
            ) from e

    def save(self, path: Path, state: StateStore) -> None:
        """Write the credentials file with mode 600; None fields are left out."""
        state.write_json(Path(path), {k: v for k, v in asdict(self).items() if v is not None}, mode=0o600)

    def basic_auth(self) -> str:
        """The client-credentials header for the token endpoint."""
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode("ascii")


def _retry_after(headers: Any) -> int:
    try:
        return int(str(headers.get("Retry-After", "2")).strip())
    except (TypeError, ValueError, AttributeError):
        return 2


def _body(e: urllib.error.HTTPError, limit: int) -> str:
    try:
        return e.read().decode("utf-8", "replace")[:limit]
    except Exception:  # noqa: BLE001 - a body that cannot be read is not worth a second error
        return ""


class SpotifyClient:
    """SpotifyApi over urllib with a refresh-token flow and a Liked Songs cache."""

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        state: StateStore,
        *,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        creds: SpotifyCredentials | None = None,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.state = state
        self.urlopen = urlopen
        self.creds = creds or SpotifyCredentials.load(settings.spotify_credentials)

    @classmethod
    def if_configured(cls, settings: Settings, clock: Clock, state: StateStore, **kw: Any) -> SpotifyClient | None:
        """A client when the credentials file exists; None otherwise."""
        if not Path(settings.spotify_credentials).exists():
            return None
        return cls(settings, clock, state, **kw)

    @property
    def operator(self) -> str:
        return self.settings.operator_name

    @property
    def user_id(self) -> str | None:
        """The signed-in user's id from the credentials file."""
        return self.creds.user_id

    # ---------- tokens ----------

    def token_request(self, form: Mapping[str, str]) -> dict[str, Any]:
        """POST the token endpoint with the client's Basic header; refresh and authorization-code grants alike."""
        data = urllib.parse.urlencode(dict(form)).encode("utf-8")
        req = urllib.request.Request(f"{ACCOUNTS}/api/token", data=data, method="POST")
        req.add_header("Authorization", self.creds.basic_auth())
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        log_debug("spotify token request " + kv(grant=form.get("grant_type")))
        try:
            with self.urlopen(req, timeout=self.settings.http_timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ProviderError(
                "spotify",
                f"token request refused | grant='{form.get('grant_type')}' status='{e.code}' body='{_body(e, 300)}'",
                provider_reply("spotify", self.operator),
            ) from e
        except urllib.error.URLError as e:
            raise ProviderError(
                "spotify",
                f"token request failed | reason='{e.reason}'",
                provider_reply("spotify", self.operator),
            ) from e

    def access_token(self) -> str:
        """A valid access token, refreshing and persisting (rotated refresh token included) when stale."""
        c = self.creds
        if c.access_token and c.expires_at > self.clock.time() + TOKEN_SLACK_S:
            return c.access_token
        tok = self.token_request({"grant_type": "refresh_token", "refresh_token": c.refresh_token})
        c.access_token = tok["access_token"]
        c.expires_at = self.clock.time() + int(tok.get("expires_in", 3600))
        if tok.get("refresh_token"):
            c.refresh_token = tok["refresh_token"]
        c.save(self.settings.spotify_credentials, self.state)
        return c.access_token

    # ---------- Web API ----------

    def api(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
        retries: int = 3,
    ) -> Any:
        """One Web API call: 429 waits Retry-After (capped), 401 refreshes the token; anything else is ProviderError."""
        url = path if path.startswith("http") else f"{API}{path}"
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(dict(params))
        data = json.dumps(body).encode("utf-8") if body is not None else None
        log_debug("spotify api " + kv(method=method, path=path))
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Authorization", f"Bearer {self.access_token()}")
            if data is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with self.urlopen(req, timeout=self.settings.http_timeout_s) as resp:
                    raw = resp.read()
                    return json.loads(raw.decode("utf-8")) if raw else None
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    if attempt == retries:
                        break
                    wait = min(_retry_after(e.headers), MAX_RETRY_AFTER_S)
                    log_warn(f"rate limited, waiting | retry_after_s='{wait}'")
                    self.clock.sleep(wait)
                    continue
                if e.code == 401 and attempt < retries:
                    self.creds.expires_at = 0  # force a refresh and retry
                    continue
                raise ProviderError(
                    "spotify",
                    f"Spotify API refused | method='{method}' path='{path}' status='{e.code}' body='{_body(e, 300)}'",
                    provider_reply("spotify", self.operator),
                ) from e
            except urllib.error.URLError as e:
                raise ProviderError(
                    "spotify",
                    f"Spotify API unreachable | path='{path}' reason='{e.reason}'",
                    provider_reply("spotify", self.operator),
                ) from e
        raise ProviderError(
            "spotify",
            f"Spotify API kept rate-limiting | path='{path}' retries='{retries}'",
            provider_reply("spotify", self.operator),
        )

    def paged(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Every item of a paginated endpoint; ProviderError('spotify-timeout') once a page is due past the deadline."""
        page = self.api("GET", path, params=dict(params or {}, limit=PAGE)) or {}
        while True:
            yield from page.get("items") or []
            nxt = page.get("next")
            if not nxt:
                return
            if deadline is not None and self.clock.time() > deadline:
                raise ProviderError(
                    "spotify-timeout",
                    f"Spotify took longer than {self.settings.teaser_timeout_s} s",
                    provider_reply("spotify", self.operator),
                )
            page = self.api("GET", nxt) or {}

    # ---------- endpoints ----------

    def me(self) -> dict[str, Any]:
        """GET /me."""
        return self.api("GET", "/me") or {}

    def search_artists(self, q: str, limit: int = 10) -> list[dict[str, Any]]:
        """GET /search type=artist."""
        res = self.api("GET", "/search", params={"q": q, "type": "artist", "limit": limit}) or {}
        return list((res.get("artists") or {}).get("items") or [])

    def search_tracks(self, q: str, limit: int = 1) -> list[dict[str, Any]]:
        """GET /search type=track (q may carry `track:` and `artist:` field filters)."""
        res = self.api("GET", "/search", params={"q": q, "type": "track", "limit": limit}) or {}
        return list((res.get("tracks") or {}).get("items") or [])

    def liked_rows(self, refresh: bool = False, deadline: float | None = None) -> list[dict[str, Any]]:
        """Liked Songs as track rows, oldest first, cached at settings.liked_cache for liked_cache_ttl_s."""
        cache = Path(self.settings.liked_cache)
        if not refresh:
            mtime = self.state.mtime(cache)
            if mtime is not None and self.clock.time() - mtime < self.settings.liked_cache_ttl_s:
                rows = self.state.read_json(cache, [])
                if rows and isinstance(rows[0], dict) and "artist_ids" in rows[0]:  # an older cache lacks the ids
                    return list(rows)
        log_info(f"fetching Liked Songs ({PAGE} per page)")
        rows = sort_oldest_first(
            track_row(item["track"], item.get("added_at"))
            for item in self.paged("/me/tracks", deadline=deadline)
            if item.get("track")
        )
        self.state.write_json(cache, rows)
        log_info(f"liked songs cached | count='{len(rows)}' path='{cache}'")
        return rows

    def my_playlists(self, deadline: float | None = None) -> list[dict[str, Any]]:
        """GET /me/playlists, every page; ProviderError('spotify-timeout') past the deadline."""
        return list(self.paged("/me/playlists", deadline=deadline))

    def playlist(self, playlist_id: str, fields: str) -> dict[str, Any]:
        """GET /playlists/<id>?fields=..."""
        return self.api("GET", f"/playlists/{playlist_id}", params={"fields": fields}) or {}

    def playlist_items(self, playlist_id: str, deadline: float | None = None) -> list[dict[str, Any]]:
        """The playlist's tracks as track rows with added_at; entries carry `item` (2025 API) or `track` (older)."""
        rows: list[dict[str, Any]] = []
        for entry in self.paged(f"/playlists/{playlist_id}/items", deadline=deadline):
            track = item_track(entry)
            if track:
                rows.append(track_row(track, entry.get("added_at")))
        return rows

    def create_playlist(self, *, name: str, description: str, public: bool) -> dict[str, Any]:
        """POST /me/playlists (2025 API; the older /users/<id>/playlists answers 403)."""
        body = {"name": name, "description": description, "public": public}
        return self.api("POST", "/me/playlists", body=body) or {}

    def add_items(self, playlist_id: str, uris: Sequence[str]) -> None:
        """POST /playlists/<id>/items in chunks of 100."""
        uris = list(uris)
        for i in range(0, len(uris), ADD_CHUNK):
            self.api("POST", f"/playlists/{playlist_id}/items", body={"uris": uris[i : i + ADD_CHUNK]})

    def rename_playlist(self, playlist_id: str, name: str) -> None:
        """PUT /playlists/<id> {name}."""
        self.api("PUT", f"/playlists/{playlist_id}", body={"name": name})

    def update_playlist_details(self, playlist_id: str, *, description: str) -> None:
        """PUT /playlists/<id> {description}."""
        self.api("PUT", f"/playlists/{playlist_id}", body={"description": description})

    def unfollow_playlist(self, playlist_id: str) -> None:
        """DELETE /playlists/<id>/followers."""
        self.api("DELETE", f"/playlists/{playlist_id}/followers")
