"""The bundle of adapters every flow receives, and the wiring of the real ones."""

from __future__ import annotations

from collections.abc import Callable
from zoneinfo import ZoneInfo

from concert_mcp.adapters.clock import SystemClock
from concert_mcp.adapters.gemini_http import GeminiClient
from concert_mcp.adapters.gws import GwsCalendar
from concert_mcp.adapters.openclaw import OpenClawChat, OpenClawContext
from concert_mcp.adapters.places_http import GooglePlacesClient, NominatimClient
from concert_mcp.adapters.protocols import (
    Calendar,
    Chat,
    Clock,
    GroundedModel,
    LiveContext,
    SetlistFmApi,
    SpotifyApi,
    StateStore,
    VenueLookup,
)
from concert_mcp.adapters.setlistfm_http import SetlistFmClient
from concert_mcp.adapters.spotify_http import SpotifyClient
from concert_mcp.adapters.state import JsonState
from concert_mcp.config import Settings, load_settings
from concert_mcp.log import set_level


class Services:
    """Settings plus one adapter per side effect.

    `spotify`, `setlistfm`, `places` and `gemini` are None while their provider is not set up; `geocoder` is the keyless
    Nominatim lookup, None when venue lookups are off. When a factory is given, a None is
    re-resolved on every read, so a long-running server picks up `concert-mcp spotify auth` (or `setlist-auth`)
    without a restart: the factory is one `Path.exists()` until the credentials file appears, then the client is
    kept. Tests pass fakes (or None) and no factory.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        calendar: Calendar,
        chat: Chat,
        context: LiveContext,
        state: StateStore,
        clock: Clock,
        spotify: SpotifyApi | None = None,
        setlistfm: SetlistFmApi | None = None,
        spotify_factory: Callable[[], SpotifyApi | None] | None = None,
        setlistfm_factory: Callable[[], SetlistFmApi | None] | None = None,
        places: VenueLookup | None = None,
        geocoder: VenueLookup | None = None,
        places_factory: Callable[[], VenueLookup | None] | None = None,
        gemini: GroundedModel | None = None,
        gemini_factory: Callable[[], GroundedModel | None] | None = None,
    ) -> None:
        self.settings = settings
        self.calendar = calendar
        self.chat = chat
        self.context = context
        self.state = state
        self.clock = clock
        self._spotify = spotify
        self._setlistfm = setlistfm
        self._spotify_factory = spotify_factory
        self._setlistfm_factory = setlistfm_factory
        self._places = places
        self._places_factory = places_factory
        self.geocoder = geocoder
        self._gemini = gemini
        self._gemini_factory = gemini_factory

    @property
    def spotify(self) -> SpotifyApi | None:
        if self._spotify is None and self._spotify_factory is not None:
            self._spotify = self._spotify_factory()
        return self._spotify

    @spotify.setter
    def spotify(self, value: SpotifyApi | None) -> None:
        self._spotify = value

    @property
    def setlistfm(self) -> SetlistFmApi | None:
        if self._setlistfm is None and self._setlistfm_factory is not None:
            self._setlistfm = self._setlistfm_factory()
        return self._setlistfm

    @setlistfm.setter
    def setlistfm(self, value: SetlistFmApi | None) -> None:
        self._setlistfm = value

    @property
    def places(self) -> VenueLookup | None:
        if self._places is None and self._places_factory is not None:
            self._places = self._places_factory()
        return self._places

    @places.setter
    def places(self, value: VenueLookup | None) -> None:
        self._places = value

    @property
    def gemini(self) -> GroundedModel | None:
        if self._gemini is None and self._gemini_factory is not None:
            self._gemini = self._gemini_factory()
        return self._gemini

    @gemini.setter
    def gemini(self, value: GroundedModel | None) -> None:
        self._gemini = value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.settings.tz)

    @property
    def operator(self) -> str:
        return self.settings.operator_name


def build_services(settings: Settings | None = None) -> Services:
    """Wire the real adapters: gws, Spotify and setlist.fm (lazily, when configured), OpenClaw, JSON state, clock."""
    settings = settings or load_settings()
    set_level(settings.log_level)
    clock = SystemClock()
    state = JsonState()
    return Services(
        settings=settings,
        calendar=GwsCalendar(settings),
        spotify_factory=lambda: SpotifyClient.if_configured(settings, clock, state),
        setlistfm_factory=lambda: SetlistFmClient.if_configured(settings, clock),
        places_factory=lambda: GooglePlacesClient.if_configured(settings),
        gemini_factory=lambda: GeminiClient.if_configured(settings),
        geocoder=NominatimClient(settings) if settings.venue_lookup else None,
        chat=OpenClawChat(settings, clock),
        context=OpenClawContext(settings),
        state=state,
        clock=clock,
    )
