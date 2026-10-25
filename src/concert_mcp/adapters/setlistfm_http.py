"""setlist.fm API over urllib (unit adapter-music). 2 requests/s, 1440/day; a 404 means no match."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from concert_mcp import __version__
from concert_mcp.adapters.protocols import Clock, StateStore
from concert_mcp.config import Settings
from concert_mcp.log import kv, log_debug
from concert_mcp.result import ProviderError
from concert_mcp.text.reply import provider_reply

SETLISTFM_API = "https://api.setlist.fm/rest/1.0"
PROBE_ARTIST = "Radiohead"


def user_agent() -> str:
    """concert-mcp/<version> (personal concert bot)."""
    return f"concert-mcp/{__version__} (personal concert bot)"


def load_api_key(path: Path) -> str | None:
    """The api_key in the file; None when absent, unreadable or empty."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    key = raw.get("api_key") if isinstance(raw, dict) else None
    return str(key) if key else None


def save_api_key(path: Path, key: str, state: StateStore) -> None:
    """Write {"api_key": key} with mode 600."""
    state.write_json(Path(path), {"api_key": key}, mode=0o600)


class SetlistFmClient:
    """SetlistFmApi over urllib."""

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        *,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        api_key: str | None = None,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.urlopen = urlopen
        key = api_key or load_api_key(settings.setlistfm_credentials)
        if not key:
            raise ProviderError(
                "setlistfm-unconfigured",
                f"no setlist.fm key yet | path='{settings.setlistfm_credentials}' "
                "hint='run: concert-mcp spotify setlist-auth'",
                provider_reply("setlistfm-unconfigured", settings.operator_name),
            )
        self._api_key = key

    @classmethod
    def if_configured(cls, settings: Settings, clock: Clock, **kw: Any) -> SetlistFmClient | None:
        """A client when the key file exists; None otherwise."""
        if not Path(settings.setlistfm_credentials).exists():
            return None
        return cls(settings, clock, **kw)

    @property
    def operator(self) -> str:
        return self.settings.operator_name

    def get(self, path: str, params: Mapping[str, Any], retries: int = 3) -> dict[str, Any] | None:
        """One GET; None on 404; 429 waits 1, 2, 3 s; any other failure is ProviderError('setlistfm')."""
        url = f"{SETLISTFM_API}{path}?{urllib.parse.urlencode(dict(params))}"
        log_debug("setlist.fm get " + kv(path=path))
        for attempt in range(retries + 1):
            req = urllib.request.Request(url)
            req.add_header("x-api-key", self._api_key)
            req.add_header("Accept", "application/json")
            req.add_header("User-Agent", user_agent())
            try:
                with self.urlopen(req, timeout=self.settings.http_timeout_s) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code == 429:
                    if attempt == retries:
                        break
                    self.clock.sleep(1 + attempt)
                    continue
                try:
                    body = e.read().decode("utf-8", "replace")[:200]
                except Exception:  # noqa: BLE001 - a body that cannot be read is not worth a second error
                    body = ""
                raise ProviderError(
                    "setlistfm",
                    f"setlist.fm refused | path='{path}' status='{e.code}' body='{body}'",
                    provider_reply("setlistfm", self.operator),
                ) from e
            except urllib.error.URLError as e:
                raise ProviderError(
                    "setlistfm",
                    f"setlist.fm unreachable | path='{path}' reason='{e.reason}'",
                    provider_reply("setlistfm", self.operator),
                ) from e
        raise ProviderError(
            "setlistfm",
            f"setlist.fm kept rate-limiting | path='{path}'",
            provider_reply("setlistfm", self.operator),
        )

    def search_setlists(
        self,
        artist: str,
        *,
        date: str | None = None,
        year: str | None = None,
        page: int = 1,
    ) -> dict[str, Any] | None:
        """GET /search/setlists by artistName with an optional date (dd-MM-yyyy) or year."""
        params: dict[str, Any] = {"artistName": artist}
        if date:
            params["date"] = date
        if year:
            params["year"] = year
        params["p"] = page
        return self.get("/search/setlists", params)

    def probe(self) -> int:
        """One real call for healthcheck --probe and setlist-auth: the total of a known artist's search."""
        return int((self.search_setlists(PROBE_ARTIST) or {}).get("total", 0) or 0)
