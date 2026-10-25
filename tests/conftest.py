"""Shared fixtures: a frozen clock, settings under tmp_path, fake services, a fictional ticket."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from concert_mcp import config
from concert_mcp.config import Settings, load_settings
from concert_mcp.flows.services import Services
from fakes import FakeCalendar, FakeChat, FakeClock, FakeContext, FakeSetlistFm, FakeSpotify, MemoryState, ms

NOW = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone(timedelta(hours=-7)))

TICKET_TEXT = (
    "ZED FOX\n"
    "with special guest Zork.\n"
    "The Quarry, San Francisco\n"
    "Sat Nov 14 2026\n"
    "Doors 7:30 PM Show 8:30 PM\n"
    "2 tickets $42.00\n"
    "Ticketweb order https://www.ticketweb.com/order/abc123\n"
)

ZED_FOX = {
    "name": "Zed Fox",
    "id": "artist-zedfox",
    "external_urls": {"spotify": "https://open.spotify.com/artist/artist-zedfox"},
    "followers": {"total": 500000},
    "genres": ["indietronica"],
}


def _liked_row(name: str, uri: str, added_at: str) -> dict[str, Any]:
    return {
        "name": name,
        "artists": ["Zed Fox"],
        "artist_ids": ["artist-zedfox"],
        "album": "Test Album",
        "uri": uri,
        "url": f"https://open.spotify.com/track/{uri.rsplit(':', 1)[-1]}",
        "duration_ms": 200000,
        "added_at": added_at,
    }


LIKED_ROWS = [
    _liked_row("First Light", "spotify:track:t1", "2019-03-01T10:00:00Z"),
    _liked_row("Second Wind", "spotify:track:t2", "2021-06-15T10:00:00Z"),
    _liked_row("Third Rail", "spotify:track:t3", "2024-01-20T10:00:00Z"),
]


def settings_env(tmp_path: Path, *, send: str = "1") -> dict[str, str]:
    """Every CONCERT_MCP_* dir under tmp_path; the gws key file is named but not created."""
    return {
        "CONCERT_MCP_CONFIG_DIR": str(tmp_path / "config"),
        "CONCERT_MCP_STATE_DIR": str(tmp_path / "state"),
        "CONCERT_MCP_CACHE_DIR": str(tmp_path / "cache"),
        "CONCERT_MCP_OPENCLAW_HOME": str(tmp_path / "openclaw"),
        "CONCERT_MCP_GWS_KEY_FILE": str(tmp_path / "gws.key"),
        "CONCERT_MCP_SEND": send,
        "CONCERT_MCP_OPERATOR_NAME": "Admin",
    }


@pytest.fixture(autouse=True)
def _no_fallback_bin_dirs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings must not find the developer machine's gws or openclaw: tests see a bare box unless they say otherwise."""
    monkeypatch.setattr(config, "FALLBACK_BIN_DIRS", ())


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(settings_env(tmp_path), home=tmp_path)


@pytest.fixture
def settings_nosend(tmp_path: Path) -> Settings:
    return load_settings(settings_env(tmp_path, send="0"), home=tmp_path)


@pytest.fixture
def ticket_text() -> str:
    return TICKET_TEXT


@pytest.fixture
def forum_route(now: datetime) -> dict[str, Any]:
    return {
        "channel": "telegram",
        "target": {"to": "telegram:-1001234567890:topic:7"},
        "thread": {"id": 7},
        "accountId": None,
        "lastInteractionAt": ms(now),
    }


def make_services(settings: Settings, now: datetime, *, route: dict[str, Any] | None, text: str) -> Services:
    clock = FakeClock(now)
    return Services(
        settings=settings,
        calendar=FakeCalendar(
            calendars=[{"id": "cal-concerts", "summary": "Concerts"}, {"id": "primary", "summary": "Personal"}]
        ),
        spotify=FakeSpotify(artists=[ZED_FOX], liked=LIKED_ROWS),
        setlistfm=FakeSetlistFm({}),
        chat=FakeChat(),
        context=FakeContext(route=route, session_id="s1" if route is not None else None, text=text),
        state=MemoryState(clock=clock),
        clock=clock,
    )


@pytest.fixture
def svc(settings: Settings, now: datetime, forum_route: dict[str, Any], ticket_text: str) -> Services:
    """Services on fakes with a live Telegram forum topic and the ticket text as the recent context."""
    return make_services(settings, now, route=forum_route, text=ticket_text)


@pytest.fixture
def svc_cli(settings: Settings, now: datetime, ticket_text: str) -> Services:
    """Services on fakes with no live chat, as a CLI run sees it."""
    return make_services(settings, now, route=None, text=ticket_text)
