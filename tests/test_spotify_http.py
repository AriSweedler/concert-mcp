"""SpotifyClient on FakeUrlopen, FakeClock and MemoryState: tokens, retries, paging, the liked cache, playlist calls."""

from __future__ import annotations

import base64
import json
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from concert_mcp.adapters.spotify_http import ACCOUNTS, API, SpotifyClient, SpotifyCredentials
from concert_mcp.config import Settings
from concert_mcp.result import ProviderError
from fakes import FakeClock, FakeUrlopen, MemoryState, http_error

TOKEN_URL = f"{ACCOUNTS}/api/token"
NOW = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)


def creds(**over: Any) -> SpotifyCredentials:
    base = dict(
        client_id="cid",
        client_secret="csecret",
        refresh_token="rt-old",
        access_token="at-valid",
        expires_at=NOW.timestamp() + 3600,
        user_id="user1",
    )
    base.update(over)
    return SpotifyCredentials(**base)


def token_ok(_req: Any = None) -> dict[str, Any]:
    return {"access_token": "at-new", "expires_in": 3600, "refresh_token": "rt-new", "scope": "x"}


def body_of(request: tuple[str, str, dict[str, str], bytes | None]) -> Any:
    return json.loads((request[3] or b"{}").decode("utf-8"))


def header(request: tuple[str, str, dict[str, str], bytes | None], name: str) -> str | None:
    for k, v in request[2].items():
        if k.lower() == name.lower():
            return v
    return None


def track(name: str, uri: str, artist: str = "Zed Fox", artist_id: str = "artist-zedfox") -> dict[str, Any]:
    return {
        "name": name,
        "uri": uri,
        "type": "track",
        "artists": [{"name": artist, "id": artist_id}],
        "album": {"name": "Album"},
        "external_urls": {"spotify": f"https://open.spotify.com/track/{uri.rsplit(':', 1)[-1]}"},
        "duration_ms": 1000,
    }


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def state(clock: FakeClock) -> MemoryState:
    return MemoryState(clock=clock)


@pytest.fixture
def fake() -> FakeUrlopen:
    return FakeUrlopen({("POST", TOKEN_URL): token_ok})


@pytest.fixture
def client(settings: Settings, clock: FakeClock, state: MemoryState, fake: FakeUrlopen) -> SpotifyClient:
    return SpotifyClient(settings, clock, state, urlopen=fake, creds=creds())


# ---------- credentials ----------


def test_load_missing_is_unconfigured(tmp_path: Path) -> None:
    with pytest.raises(ProviderError) as ei:
        SpotifyCredentials.load(tmp_path / "nope.json")
    assert ei.value.provider == "spotify-unconfigured"
    assert "concert-mcp spotify auth" in ei.value.error


def test_load_unreadable_is_unconfigured(tmp_path: Path) -> None:
    p = tmp_path / "spotify.json"
    p.write_text("not json")
    with pytest.raises(ProviderError) as ei:
        SpotifyCredentials.load(p)
    assert ei.value.provider == "spotify-unconfigured"


def test_load_ignores_extra_keys(tmp_path: Path) -> None:
    p = tmp_path / "spotify.json"
    p.write_text(json.dumps({"client_id": "a", "client_secret": "b", "refresh_token": "c", "unknown": 1}))
    c = SpotifyCredentials.load(p)
    assert (c.client_id, c.client_secret, c.refresh_token, c.access_token, c.user_id) == ("a", "b", "c", None, None)


def test_save_writes_mode_600_without_none_fields(tmp_path: Path, state: MemoryState) -> None:
    p = tmp_path / "spotify.json"
    creds(access_token=None, user_id=None).save(p, state)
    assert state.modes[p] == 0o600
    assert "access_token" not in state.files[p]
    assert "user_id" not in state.files[p]
    assert state.files[p]["refresh_token"] == "rt-old"


def test_basic_auth_header() -> None:
    expected = "Basic " + base64.b64encode(b"cid:csecret").decode()
    assert creds().basic_auth() == expected


def test_if_configured_none_when_file_absent(settings: Settings, clock: FakeClock, state: MemoryState) -> None:
    assert SpotifyClient.if_configured(settings, clock, state) is None


def test_if_configured_builds_client_when_file_exists(settings: Settings, clock: FakeClock, state: MemoryState) -> None:
    settings.spotify_credentials.parent.mkdir(parents=True, exist_ok=True)
    settings.spotify_credentials.write_text(json.dumps({"client_id": "a", "client_secret": "b", "refresh_token": "c"}))
    c = SpotifyClient.if_configured(settings, clock, state, urlopen=FakeUrlopen())
    assert isinstance(c, SpotifyClient)
    assert c.user_id is None


# ---------- tokens ----------


def test_access_token_valid_needs_no_http(client: SpotifyClient, fake: FakeUrlopen) -> None:
    assert client.access_token() == "at-valid"
    assert fake.requests == []


def test_access_token_refreshes_when_expired(
    settings: Settings, clock: FakeClock, state: MemoryState, fake: FakeUrlopen
) -> None:
    c = SpotifyClient(settings, clock, state, urlopen=fake, creds=creds(expires_at=NOW.timestamp() + 30))
    assert c.access_token() == "at-new"
    method, url, _headers, body = fake.requests[0]
    assert (method, url) == ("POST", TOKEN_URL)
    assert body == b"grant_type=refresh_token&refresh_token=rt-old"
    assert header(fake.requests[0], "Authorization") == "<redacted>"  # FakeUrlopen records the header, redacted
    assert header(fake.requests[0], "Content-Type") == "application/x-www-form-urlencoded"
    # rotated refresh token stored, expiry from the clock, saved with mode 600
    assert c.creds.refresh_token == "rt-new"
    assert c.creds.expires_at == NOW.timestamp() + 3600
    saved = state.files[settings.spotify_credentials]
    assert saved["refresh_token"] == "rt-new"
    assert saved["access_token"] == "at-new"
    assert state.modes[settings.spotify_credentials] == 0o600


def test_access_token_keeps_refresh_token_when_none_returned(
    settings: Settings, clock: FakeClock, state: MemoryState
) -> None:
    fake = FakeUrlopen({("POST", TOKEN_URL): {"access_token": "at-new", "expires_in": 60}})
    c = SpotifyClient(settings, clock, state, urlopen=fake, creds=creds(access_token=None))
    assert c.access_token() == "at-new"
    assert c.creds.refresh_token == "rt-old"
    assert c.creds.expires_at == NOW.timestamp() + 60


def test_token_request_refused(settings: Settings, clock: FakeClock, state: MemoryState) -> None:
    fake = FakeUrlopen({("POST", TOKEN_URL): http_error(400, '{"error":"invalid_grant"}')})
    c = SpotifyClient(settings, clock, state, urlopen=fake, creds=creds(access_token=None))
    with pytest.raises(ProviderError) as ei:
        c.access_token()
    assert ei.value.provider == "spotify"
    assert "token request refused" in ei.value.error
    assert "grant='refresh_token'" in ei.value.error
    assert "status='400'" in ei.value.error
    assert "invalid_grant" in ei.value.error


def test_token_request_unreachable(settings: Settings, clock: FakeClock, state: MemoryState) -> None:
    fake = FakeUrlopen({("POST", TOKEN_URL): urllib.error.URLError("dns down")})
    c = SpotifyClient(settings, clock, state, urlopen=fake, creds=creds(access_token=None))
    with pytest.raises(ProviderError) as ei:
        c.access_token()
    assert "token request failed" in ei.value.error
    assert "dns down" in ei.value.error


# ---------- api ----------


def test_api_get_sends_bearer_and_params(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("GET", f"{API}/me", {"id": "user1"})
    assert client.api("GET", "/me", params={"a": "1 2"}) == {"id": "user1"}
    method, url, _h, body = fake.requests[-1]
    assert (method, url, body) == ("GET", f"{API}/me?a=1+2", None)
    assert header(fake.requests[-1], "Authorization") == "<redacted>"


def test_api_appends_params_with_ampersand_when_url_has_query(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("GET", f"{API}/x", {})
    client.api("GET", "/x?offset=50", params={"limit": 50})
    assert fake.requests[-1][1] == f"{API}/x?offset=50&limit=50"


def test_api_absolute_url_used_as_is(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("GET", "https://api.spotify.com/v1/me/tracks?offset=50", {"items": []})
    assert client.api("GET", "https://api.spotify.com/v1/me/tracks?offset=50&limit=50") == {"items": []}


def test_api_empty_body_is_none(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("DELETE", f"{API}/playlists/p1/followers", b"")
    assert client.api("DELETE", "/playlists/p1/followers") is None


def test_api_posts_json_body(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("POST", f"{API}/thing", {"ok": True})
    client.api("POST", "/thing", body={"a": 1})
    req = fake.requests[-1]
    assert body_of(req) == {"a": 1}
    assert header(req, "Content-Type") == "application/json"


def test_api_429_sleeps_retry_after_then_succeeds(client: SpotifyClient, fake: FakeUrlopen, clock: FakeClock) -> None:
    answers = [http_error(429, headers={"Retry-After": "5"}), {"id": "user1"}]
    fake.add("GET", f"{API}/me", lambda _req: answers.pop(0))
    assert client.api("GET", "/me") == {"id": "user1"}
    assert clock.slept == [5]


def test_api_429_retry_after_capped_at_30(client: SpotifyClient, fake: FakeUrlopen, clock: FakeClock) -> None:
    answers = [http_error(429, headers={"Retry-After": "99"}), {"id": "user1"}]
    fake.add("GET", f"{API}/me", lambda _req: answers.pop(0))
    client.api("GET", "/me")
    assert clock.slept == [30]


def test_api_429_without_header_waits_2(client: SpotifyClient, fake: FakeUrlopen, clock: FakeClock) -> None:
    answers = [http_error(429), {"id": "user1"}]
    fake.add("GET", f"{API}/me", lambda _req: answers.pop(0))
    client.api("GET", "/me")
    assert clock.slept == [2]


def test_api_429_exhausted(client: SpotifyClient, fake: FakeUrlopen, clock: FakeClock) -> None:
    fake.add("GET", f"{API}/me", lambda _req: http_error(429, headers={"Retry-After": "1"}))
    with pytest.raises(ProviderError) as ei:
        client.api("GET", "/me", retries=2)
    assert "kept rate-limiting" in ei.value.error
    assert "retries='2'" in ei.value.error
    assert clock.slept == [1, 1]


def test_api_401_resets_expiry_and_retries_once(client: SpotifyClient, fake: FakeUrlopen) -> None:
    answers = [http_error(401, '{"error":"expired"}'), {"id": "user1"}]
    fake.add("GET", f"{API}/me", lambda _req: answers.pop(0))
    assert client.api("GET", "/me") == {"id": "user1"}
    kinds = [(m, u) for m, u, _h, _b in fake.requests]
    assert kinds == [("GET", f"{API}/me"), ("POST", TOKEN_URL), ("GET", f"{API}/me")]
    assert client.creds.access_token == "at-new"


def test_api_401_does_not_loop_forever(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("GET", f"{API}/me", lambda _req: http_error(401, "nope"))
    with pytest.raises(ProviderError) as ei:
        client.api("GET", "/me", retries=3)
    assert "status='401'" in ei.value.error
    assert sum(1 for m, u, _h, _b in fake.requests if u == f"{API}/me") == 4


def test_api_500_is_provider_error_with_status(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("GET", f"{API}/me", http_error(500, "server on fire"))
    with pytest.raises(ProviderError) as ei:
        client.api("GET", "/me")
    assert ei.value.provider == "spotify"
    assert "Spotify API refused" in ei.value.error
    assert "method='GET'" in ei.value.error
    assert "path='/me'" in ei.value.error
    assert "status='500'" in ei.value.error
    assert "server on fire" in ei.value.error


def test_api_urlerror_is_unreachable(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("GET", f"{API}/me", urllib.error.URLError("no route"))
    with pytest.raises(ProviderError) as ei:
        client.api("GET", "/me")
    assert "Spotify API unreachable" in ei.value.error
    assert "no route" in ei.value.error


def test_errors_never_carry_the_token(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("GET", f"{API}/me", http_error(500, "x"))
    with pytest.raises(ProviderError) as ei:
        client.api("GET", "/me")
    assert "at-valid" not in ei.value.error
    assert "csecret" not in ei.value.error


# ---------- paged ----------


def test_paged_follows_next_and_stops(client: SpotifyClient, fake: FakeUrlopen) -> None:
    page2 = f"{API}/me/playlists?offset=50&limit=50"
    fake.add("GET", f"{API}/me/playlists?limit=50", {"items": [{"id": "a"}, {"id": "b"}], "next": page2})
    fake.add("GET", page2, {"items": [{"id": "c"}], "next": None})
    assert [p["id"] for p in client.paged("/me/playlists")] == ["a", "b", "c"]
    assert fake.requests[0][1] == f"{API}/me/playlists?limit=50"
    assert fake.requests[1][1] == page2


def test_paged_deadline_between_pages(client: SpotifyClient, fake: FakeUrlopen, clock: FakeClock) -> None:
    page2 = f"{API}/me/tracks?offset=50&limit=50"
    fake.add("GET", f"{API}/me/tracks?limit=50", {"items": [{"track": track("A", "spotify:track:a")}], "next": page2})
    fake.add("GET", page2, {"items": [], "next": None})
    it = client.paged("/me/tracks", deadline=clock.time() - 1)
    first = next(it)
    assert first["track"]["name"] == "A"
    with pytest.raises(ProviderError) as ei:
        next(it)
    assert ei.value.provider == "spotify-timeout"
    assert ei.value.error == "Spotify took longer than 150 s"
    assert len(fake.requests) == 1


def test_paged_within_deadline_fetches_all(client: SpotifyClient, fake: FakeUrlopen, clock: FakeClock) -> None:
    page2 = f"{API}/me/tracks?offset=50&limit=50"
    fake.add("GET", f"{API}/me/tracks?limit=50", {"items": [{"id": 1}], "next": page2})
    fake.add("GET", page2, {"items": [{"id": 2}], "next": None})
    assert [x["id"] for x in client.paged("/me/tracks", deadline=clock.time() + 100)] == [1, 2]


# ---------- liked cache ----------


def liked_page(fake: FakeUrlopen) -> None:
    fake.add(
        "GET",
        f"{API}/me/tracks",
        {
            "items": [
                {"added_at": "2024-01-20T10:00:00Z", "track": track("Third Rail", "spotify:track:t3")},
                {"added_at": "2019-03-01T10:00:00Z", "track": track("First Light", "spotify:track:t1")},
                {"added_at": "2021-06-15T10:00:00Z", "track": None},
                {"added_at": "2021-06-15T10:00:00Z", "track": track("Second Wind", "spotify:track:t2")},
            ],
            "next": None,
        },
    )


def test_liked_rows_fetches_sorts_and_caches(
    client: SpotifyClient, fake: FakeUrlopen, state: MemoryState, settings: Settings
) -> None:
    liked_page(fake)
    rows = client.liked_rows()
    assert [r["name"] for r in rows] == ["First Light", "Second Wind", "Third Rail"]
    assert rows[0]["artist_ids"] == ["artist-zedfox"]
    assert rows[0]["added_at"] == "2019-03-01T10:00:00Z"
    assert state.files[settings.liked_cache] == rows
    assert settings.liked_cache not in state.modes  # the cache is not a secret


def test_liked_rows_uses_cache_within_ttl(
    client: SpotifyClient, fake: FakeUrlopen, state: MemoryState, settings: Settings
) -> None:
    cached = [{"name": "Cached", "artists": ["X"], "artist_ids": ["x"], "uri": "spotify:track:c", "added_at": "2020"}]
    state.write_json(settings.liked_cache, cached)
    assert client.liked_rows() == cached
    assert fake.requests == []


def test_liked_rows_refetches_when_cache_lacks_artist_ids(
    client: SpotifyClient, fake: FakeUrlopen, state: MemoryState, settings: Settings
) -> None:
    state.write_json(settings.liked_cache, [{"name": "Old", "artists": ["X"], "uri": "spotify:track:o"}])
    liked_page(fake)
    rows = client.liked_rows()
    assert [r["name"] for r in rows] == ["First Light", "Second Wind", "Third Rail"]
    assert len(fake.requests) == 1


def test_liked_rows_refetches_when_stale(
    client: SpotifyClient, fake: FakeUrlopen, state: MemoryState, settings: Settings, clock: FakeClock
) -> None:
    state.write_json(settings.liked_cache, [{"name": "Old", "artists": [], "artist_ids": [], "uri": "u"}])
    clock.advance(settings.liked_cache_ttl_s + 1)
    liked_page(fake)
    rows = client.liked_rows()
    assert len(rows) == 3
    # the advance also expired the access token, so a token POST precedes the one GET
    assert [m for m, _u, _h, _b in fake.requests] == ["POST", "GET"]


def test_liked_rows_refresh_ignores_cache(
    client: SpotifyClient, fake: FakeUrlopen, state: MemoryState, settings: Settings
) -> None:
    state.write_json(settings.liked_cache, [{"name": "Old", "artists": [], "artist_ids": [], "uri": "u"}])
    liked_page(fake)
    rows = client.liked_rows(refresh=True)
    assert [r["name"] for r in rows] == ["First Light", "Second Wind", "Third Rail"]
    assert state.files[settings.liked_cache] == rows


def test_liked_rows_empty_cache_refetched(
    client: SpotifyClient, fake: FakeUrlopen, state: MemoryState, settings: Settings
) -> None:
    state.write_json(settings.liked_cache, [])
    liked_page(fake)
    assert len(client.liked_rows()) == 3


def test_my_playlists_and_playlist_items_honour_the_deadline(
    client: SpotifyClient, fake: FakeUrlopen, clock: FakeClock
) -> None:
    fake.add("GET", f"{API}/me/playlists?limit=50", {"items": [{"id": "a"}], "next": f"{API}/me/playlists?offset=50"})
    with pytest.raises(ProviderError) as ei:
        client.my_playlists(deadline=clock.time() - 1)
    assert ei.value.provider == "spotify-timeout"
    fake.add("GET", f"{API}/playlists/p1/items?limit=50", {"items": [], "next": f"{API}/playlists/p1/items?offset=50"})
    with pytest.raises(ProviderError) as ei:
        client.playlist_items("p1", deadline=clock.time() - 1)
    assert ei.value.provider == "spotify-timeout"


def test_liked_rows_passes_deadline(client: SpotifyClient, fake: FakeUrlopen, clock: FakeClock) -> None:
    page2 = f"{API}/me/tracks?offset=50&limit=50"
    fake.add("GET", f"{API}/me/tracks?limit=50", {"items": [{"track": track("A", "spotify:track:a")}], "next": page2})
    with pytest.raises(ProviderError) as ei:
        client.liked_rows(deadline=clock.time() - 1)
    assert ei.value.provider == "spotify-timeout"


# ---------- endpoints ----------


def test_me(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("GET", f"{API}/me", {"id": "user1"})
    assert client.me() == {"id": "user1"}


def test_search_artists(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("GET", f"{API}/search", {"artists": {"items": [{"name": "Zed Fox"}]}})
    assert client.search_artists("Zed Fox", limit=3) == [{"name": "Zed Fox"}]
    assert fake.requests[-1][1] == f"{API}/search?q=Zed+Fox&type=artist&limit=3"


def test_search_artists_empty(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("GET", f"{API}/search", {"artists": None})
    assert client.search_artists("zzz") == []


def test_my_playlists(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("GET", f"{API}/me/playlists", {"items": [{"id": "p1"}], "next": None})
    assert client.my_playlists() == [{"id": "p1"}]


def test_playlist_fields(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("GET", f"{API}/playlists/p1", {"id": "p1", "name": "N", "owner": {"id": "user1"}})
    assert client.playlist("p1", "id,name,owner(id)")["name"] == "N"
    assert fake.requests[-1][1] == f"{API}/playlists/p1?fields=id%2Cname%2Cowner%28id%29"


def test_playlist_items_accepts_item_and_track_and_skips_episodes(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add(
        "GET",
        f"{API}/playlists/p1/items",
        {
            "items": [
                {"added_at": "2020-01-01T00:00:00Z", "item": track("New API", "spotify:track:n")},
                {"added_at": "2020-01-02T00:00:00Z", "track": track("Old API", "spotify:track:o")},
                {"added_at": "2020-01-03T00:00:00Z", "item": {**track("Pod", "spotify:episode:e"), "type": "episode"}},
                {"added_at": "2020-01-04T00:00:00Z", "item": None},
            ],
            "next": None,
        },
    )
    rows = client.playlist_items("p1")
    assert [(r["name"], r["added_at"]) for r in rows] == [
        ("New API", "2020-01-01T00:00:00Z"),
        ("Old API", "2020-01-02T00:00:00Z"),
    ]


def test_create_playlist_posts_me_playlists(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("POST", f"{API}/me/playlists", {"id": "p9", "name": "2026-11-14 | Zed Fox"})
    pl = client.create_playlist(name="2026-11-14 | Zed Fox", description="d", public=False)
    assert pl["id"] == "p9"
    req = fake.requests[-1]
    assert req[1] == f"{API}/me/playlists"
    assert body_of(req) == {"name": "2026-11-14 | Zed Fox", "description": "d", "public": False}


def test_add_items_chunks_of_100(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("POST", f"{API}/playlists/p1/items", {"snapshot_id": "s"})
    uris = [f"spotify:track:{i}" for i in range(250)]
    client.add_items("p1", uris)
    posts = [body_of(r)["uris"] for r in fake.requests if r[0] == "POST"]
    assert [len(p) for p in posts] == [100, 100, 50]
    assert posts[0][0] == "spotify:track:0"
    assert posts[2][-1] == "spotify:track:249"


def test_add_items_nothing_posts_nothing(client: SpotifyClient, fake: FakeUrlopen) -> None:
    client.add_items("p1", [])
    assert fake.requests == []


def test_rename_playlist(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("PUT", f"{API}/playlists/p1", b"")
    client.rename_playlist("p1", "New Name")
    req = fake.requests[-1]
    assert (req[0], req[1]) == ("PUT", f"{API}/playlists/p1")
    assert body_of(req) == {"name": "New Name"}


def test_unfollow_playlist(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("DELETE", f"{API}/playlists/p1/followers", b"")
    client.unfollow_playlist("p1")
    assert (fake.requests[-1][0], fake.requests[-1][1]) == ("DELETE", f"{API}/playlists/p1/followers")


def test_user_id_from_credentials(client: SpotifyClient) -> None:
    assert client.user_id == "user1"
