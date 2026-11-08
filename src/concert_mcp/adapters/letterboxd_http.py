"""The Letterboxd page of a film, found by its slug over urllib (unit adapter-movie).

Letterboxd's API is partner-gated, so the page is guessed: the year-suffixed slug, then the bare one, each fetched
once and kept only when its og:title names the film and the year. A miss or a network error is None, never a failed
add: the event goes in without the line.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from concert_mcp.config import Settings
from concert_mcp.log import kv, log_debug
from concert_mcp.text.movie import letterboxd_candidates, letterboxd_matches

TIMEOUT_S = 8
# Letterboxd answers urllib's default agent with 403; a browser agent gets the page.
BROWSER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)"


class LetterboxdClient:
    """FilmLookup over https://letterboxd.com/film/<slug>/."""

    def __init__(self, settings: Settings, *, urlopen: Callable[..., Any] = urllib.request.urlopen) -> None:
        self._settings = settings
        self._urlopen = urlopen

    def film_url(self, film: str, year: int | None) -> str | None:
        """The canonical film page, or None when no candidate slug is this film."""
        for url in letterboxd_candidates(film, year):
            req = urllib.request.Request(url, headers={"User-Agent": BROWSER_AGENT})
            try:
                with self._urlopen(req, timeout=TIMEOUT_S) as resp:
                    html = resp.read(200_000).decode("utf-8", errors="replace")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                log_debug(f"letterboxd miss | {kv(url=url, error=type(e).__name__)}")
                continue
            if letterboxd_matches(html, film, year):
                return url
            log_debug(f"letterboxd page is another film | {kv(url=url)}")
        return None
