"""SetlistFmClient on FakeUrlopen and FakeClock: key file, headers, 404/429/500 handling, the probe."""

from __future__ import annotations

import json
import urllib.error
from datetime import UTC, datetime

import pytest

from concert_mcp.adapters.setlistfm_http import (
    PROBE_ARTIST,
    SETLISTFM_API,
    SetlistFmClient,
)
from concert_mcp.config import Settings
from concert_mcp.result import ProviderError
from fakes import FakeClock, FakeUrlopen, http_error

NOW = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)
SEARCH = f"{SETLISTFM_API}/search/setlists"


def header(request: tuple[str, str, dict[str, str], bytes | None], name: str) -> str | None:
    for k, v in request[2].items():
        if k.lower() == name.lower():
            return v
    return None


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def fake() -> FakeUrlopen:
    return FakeUrlopen()


@pytest.fixture
def client(settings: Settings, clock: FakeClock, fake: FakeUrlopen) -> SetlistFmClient:
    return SetlistFmClient(settings, clock, urlopen=fake, api_key="key-abc")


# ---------- key file ----------


def test_constructor_without_key_is_unconfigured(settings: Settings, clock: FakeClock) -> None:
    with pytest.raises(ProviderError) as ei:
        SetlistFmClient(settings, clock, urlopen=FakeUrlopen())
    assert ei.value.provider == "setlistfm-unconfigured"
    assert "no setlist.fm key yet" in ei.value.error
    assert str(settings.setlistfm_credentials) in ei.value.error
    assert "concert-mcp spotify setlist-auth" in ei.value.error


def test_if_configured(settings: Settings, clock: FakeClock, fake: FakeUrlopen) -> None:
    assert SetlistFmClient.if_configured(settings, clock, urlopen=fake) is None
    settings.setlistfm_credentials.parent.mkdir(parents=True, exist_ok=True)
    settings.setlistfm_credentials.write_text(json.dumps({"api_key": "k"}))
    assert isinstance(SetlistFmClient.if_configured(settings, clock, urlopen=fake), SetlistFmClient)


# ---------- get ----------


def test_search_setlists_url_year(client: SetlistFmClient, fake: FakeUrlopen) -> None:
    fake.add("GET", SEARCH, {"total": 1})
    client.search_setlists("Zed Fox", year="2026")
    assert fake.requests[-1][1] == f"{SEARCH}?artistName=Zed+Fox&year=2026&p=1"


def test_404_is_none(client: SetlistFmClient, fake: FakeUrlopen) -> None:
    fake.add("GET", SEARCH, http_error(404, '{"code":404}'))
    assert client.search_setlists("Nobody", date="01-01-2026") is None


def test_429_backoff_grows_then_exhausts(client: SetlistFmClient, fake: FakeUrlopen, clock: FakeClock) -> None:
    fake.add("GET", SEARCH, lambda _req: http_error(429))
    with pytest.raises(ProviderError) as ei:
        client.get("/search/setlists", {"artistName": "X"}, retries=3)
    assert clock.slept == [1, 2, 3]
    assert ei.value.provider == "setlistfm"
    assert ei.value.error == "setlist.fm kept rate-limiting | path='/search/setlists'"


def test_urlerror_is_unreachable(client: SetlistFmClient, fake: FakeUrlopen) -> None:
    fake.add("GET", SEARCH, urllib.error.URLError("offline"))
    with pytest.raises(ProviderError) as ei:
        client.search_setlists("Zed Fox")
    assert ei.value.error == "setlist.fm unreachable | path='/search/setlists' reason='offline'"


def test_probe_returns_total(client: SetlistFmClient, fake: FakeUrlopen) -> None:
    fake.add("GET", SEARCH, {"total": 42, "setlist": []})
    assert client.probe() == 42
    assert fake.requests[-1][1] == f"{SEARCH}?artistName={PROBE_ARTIST}&p=1"


def test_latest_setlist_is_the_bare_artist_search(client: SetlistFmClient, fake: FakeUrlopen) -> None:
    fake.add("GET", SEARCH, {"total": 2, "setlist": [{"id": "new"}, {"id": "old"}]})
    assert client.latest_setlist("Zork") == {"total": 2, "setlist": [{"id": "new"}, {"id": "old"}]}
    url = fake.requests[-1][1]
    assert url == f"{SEARCH}?artistName=Zork&p=1"
