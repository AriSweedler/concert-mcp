"""SetlistFmClient on FakeUrlopen and FakeClock: key file, headers, 404/429/500 handling, the probe."""

from __future__ import annotations

import json
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

import pytest

from concert_mcp import __version__
from concert_mcp.adapters.setlistfm_http import (
    PROBE_ARTIST,
    SETLISTFM_API,
    SetlistFmClient,
    load_api_key,
    save_api_key,
    user_agent,
)
from concert_mcp.config import Settings
from concert_mcp.result import ProviderError
from fakes import FakeClock, FakeUrlopen, MemoryState, http_error

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


def test_user_agent_names_the_package() -> None:
    assert user_agent() == f"concert-mcp/{__version__} (personal concert bot)"
    assert user_agent() == "concert-mcp/0.1.0 (personal concert bot)"


def test_load_api_key(tmp_path: Path) -> None:
    p = tmp_path / "setlistfm.json"
    assert load_api_key(p) is None
    p.write_text("garbage")
    assert load_api_key(p) is None
    p.write_text(json.dumps({"api_key": ""}))
    assert load_api_key(p) is None
    p.write_text(json.dumps({"api_key": "k1"}))
    assert load_api_key(p) == "k1"


def test_save_api_key_writes_mode_600(tmp_path: Path) -> None:
    state = MemoryState()
    p = tmp_path / "setlistfm.json"
    save_api_key(p, "k1", state)
    assert state.files[p] == {"api_key": "k1"}
    assert state.modes[p] == 0o600


def test_constructor_without_key_is_unconfigured(settings: Settings, clock: FakeClock) -> None:
    with pytest.raises(ProviderError) as ei:
        SetlistFmClient(settings, clock, urlopen=FakeUrlopen())
    assert ei.value.provider == "setlistfm-unconfigured"
    assert "no setlist.fm key yet" in ei.value.error
    assert str(settings.setlistfm_credentials) in ei.value.error
    assert "concert-mcp spotify setlist-auth" in ei.value.error


def test_constructor_reads_key_file(settings: Settings, clock: FakeClock, fake: FakeUrlopen) -> None:
    settings.setlistfm_credentials.parent.mkdir(parents=True, exist_ok=True)
    settings.setlistfm_credentials.write_text(json.dumps({"api_key": "from-file"}))
    c = SetlistFmClient(settings, clock, urlopen=fake)
    fake.add("GET", SEARCH, {"total": 0})
    c.search_setlists("X")
    assert header(fake.requests[-1], "x-api-key") == "from-file"


def test_if_configured(settings: Settings, clock: FakeClock, fake: FakeUrlopen) -> None:
    assert SetlistFmClient.if_configured(settings, clock, urlopen=fake) is None
    settings.setlistfm_credentials.parent.mkdir(parents=True, exist_ok=True)
    settings.setlistfm_credentials.write_text(json.dumps({"api_key": "k"}))
    assert isinstance(SetlistFmClient.if_configured(settings, clock, urlopen=fake), SetlistFmClient)


# ---------- get ----------


def test_headers(client: SetlistFmClient, fake: FakeUrlopen) -> None:
    fake.add("GET", SEARCH, {"total": 1})
    client.search_setlists("Zed Fox")
    req = fake.requests[-1]
    assert req[0] == "GET"
    assert header(req, "x-api-key") == "key-abc"
    assert header(req, "Accept") == "application/json"
    assert header(req, "User-Agent") == "concert-mcp/0.1.0 (personal concert bot)"


def test_search_setlists_url_date_and_page(client: SetlistFmClient, fake: FakeUrlopen) -> None:
    fake.add("GET", SEARCH, {"total": 1})
    client.search_setlists("Zed Fox", date="14-11-2026", page=2)
    assert fake.requests[-1][1] == f"{SEARCH}?artistName=Zed+Fox&date=14-11-2026&p=2"


def test_search_setlists_url_year(client: SetlistFmClient, fake: FakeUrlopen) -> None:
    fake.add("GET", SEARCH, {"total": 1})
    client.search_setlists("Zed Fox", year="2026")
    assert fake.requests[-1][1] == f"{SEARCH}?artistName=Zed+Fox&year=2026&p=1"


def test_404_is_none(client: SetlistFmClient, fake: FakeUrlopen) -> None:
    fake.add("GET", SEARCH, http_error(404, '{"code":404}'))
    assert client.search_setlists("Nobody", date="01-01-2026") is None


def test_429_then_200_sleeps_1(client: SetlistFmClient, fake: FakeUrlopen, clock: FakeClock) -> None:
    answers = [http_error(429), {"total": 3}]
    fake.add("GET", SEARCH, lambda _req: answers.pop(0))
    assert client.search_setlists("Zed Fox") == {"total": 3}
    assert clock.slept == [1]


def test_429_backoff_grows_then_exhausts(client: SetlistFmClient, fake: FakeUrlopen, clock: FakeClock) -> None:
    fake.add("GET", SEARCH, lambda _req: http_error(429))
    with pytest.raises(ProviderError) as ei:
        client.get("/search/setlists", {"artistName": "X"}, retries=3)
    assert clock.slept == [1, 2, 3]
    assert ei.value.provider == "setlistfm"
    assert ei.value.error == "setlist.fm kept rate-limiting | path='/search/setlists'"


def test_500_is_provider_error(client: SetlistFmClient, fake: FakeUrlopen) -> None:
    fake.add("GET", SEARCH, http_error(500, "boom"))
    with pytest.raises(ProviderError) as ei:
        client.search_setlists("Zed Fox")
    assert ei.value.provider == "setlistfm"
    assert ei.value.error == "setlist.fm refused | path='/search/setlists' status='500' body='boom'"


def test_403_error_never_carries_the_key(client: SetlistFmClient, fake: FakeUrlopen) -> None:
    fake.add("GET", SEARCH, http_error(403, "forbidden"))
    with pytest.raises(ProviderError) as ei:
        client.search_setlists("Zed Fox")
    assert "key-abc" not in ei.value.error


def test_urlerror_is_unreachable(client: SetlistFmClient, fake: FakeUrlopen) -> None:
    fake.add("GET", SEARCH, urllib.error.URLError("offline"))
    with pytest.raises(ProviderError) as ei:
        client.search_setlists("Zed Fox")
    assert ei.value.error == "setlist.fm unreachable | path='/search/setlists' reason='offline'"


def test_probe_returns_total(client: SetlistFmClient, fake: FakeUrlopen) -> None:
    fake.add("GET", SEARCH, {"total": 42, "setlist": []})
    assert client.probe() == 42
    assert fake.requests[-1][1] == f"{SEARCH}?artistName={PROBE_ARTIST}&p=1"


def test_probe_404_is_zero(client: SetlistFmClient, fake: FakeUrlopen) -> None:
    fake.add("GET", SEARCH, http_error(404))
    assert client.probe() == 0


def test_latest_setlist_is_the_bare_artist_search(client: SetlistFmClient, fake: FakeUrlopen) -> None:
    fake.add("GET", SEARCH, {"total": 2, "setlist": [{"id": "new"}, {"id": "old"}]})
    assert client.latest_setlist("Zork") == {"total": 2, "setlist": [{"id": "new"}, {"id": "old"}]}
    url = fake.requests[-1][1]
    assert url == f"{SEARCH}?artistName=Zork&p=1"
