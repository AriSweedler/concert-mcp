"""SpotifyClient on FakeUrlopen, FakeClock and MemoryState: tokens, retries, paging, the liked cache, playlist calls."""

from __future__ import annotations

import json
import urllib.error
from datetime import UTC, datetime
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


def test_if_configured_builds_client_when_file_exists(settings: Settings, clock: FakeClock, state: MemoryState) -> None:
    settings.spotify_credentials.parent.mkdir(parents=True, exist_ok=True)
    settings.spotify_credentials.write_text(json.dumps({"client_id": "a", "client_secret": "b", "refresh_token": "c"}))
    c = SpotifyClient.if_configured(settings, clock, state, urlopen=FakeUrlopen())
    assert isinstance(c, SpotifyClient)
    assert c.user_id is None


# ---------- tokens ----------


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


def test_api_429_exhausted(client: SpotifyClient, fake: FakeUrlopen, clock: FakeClock) -> None:
    fake.add("GET", f"{API}/me", lambda _req: http_error(429, headers={"Retry-After": "1"}))
    with pytest.raises(ProviderError) as ei:
        client.api("GET", "/me", retries=2)
    assert "kept rate-limiting" in ei.value.error
    assert "retries='2'" in ei.value.error
    assert clock.slept == [1, 1]


def test_api_401_does_not_loop_forever(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("GET", f"{API}/me", lambda _req: http_error(401, "nope"))
    with pytest.raises(ProviderError) as ei:
        client.api("GET", "/me", retries=3)
    assert "status='401'" in ei.value.error
    assert sum(1 for m, u, _h, _b in fake.requests if u == f"{API}/me") == 4


def test_api_urlerror_is_unreachable(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("GET", f"{API}/me", urllib.error.URLError("no route"))
    with pytest.raises(ProviderError) as ei:
        client.api("GET", "/me")
    assert "Spotify API unreachable" in ei.value.error
    assert "no route" in ei.value.error


# ---------- paged ----------


def test_paged_follows_next_and_stops(client: SpotifyClient, fake: FakeUrlopen) -> None:
    page2 = f"{API}/me/playlists?offset=50&limit=50"
    fake.add("GET", f"{API}/me/playlists?limit=50", {"items": [{"id": "a"}, {"id": "b"}], "next": page2})
    fake.add("GET", page2, {"items": [{"id": "c"}], "next": None})
    assert [p["id"] for p in client.paged("/me/playlists")] == ["a", "b", "c"]
    assert fake.requests[0][1] == f"{API}/me/playlists?limit=50"
    assert fake.requests[1][1] == page2


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


def test_liked_rows_uses_cache_within_ttl(
    client: SpotifyClient, fake: FakeUrlopen, state: MemoryState, settings: Settings
) -> None:
    cached = [{"name": "Cached", "artists": ["X"], "artist_ids": ["x"], "uri": "spotify:track:c", "added_at": "2020"}]
    state.write_json(settings.liked_cache, cached)
    assert client.liked_rows() == cached
    assert fake.requests == []


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


# ---------- endpoints ----------


def test_search_artists(client: SpotifyClient, fake: FakeUrlopen) -> None:
    fake.add("GET", f"{API}/search", {"artists": {"items": [{"name": "Zed Fox"}]}})
    assert client.search_artists("Zed Fox", limit=3) == [{"name": "Zed Fox"}]
    assert fake.requests[-1][1] == f"{API}/search?q=Zed+Fox&type=artist&limit=3"


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


def test_replace_item_removes_the_old_uri_then_inserts_the_new_one_at_the_position(
    client: SpotifyClient, fake: FakeUrlopen
) -> None:
    fake.add("DELETE", f"{API}/playlists/p1/items", {"snapshot_id": "s1"})
    fake.add("POST", f"{API}/playlists/p1/items", {"snapshot_id": "s2"})
    client.replace_item("p1", position=3, old_uri="spotify:track:old", new_uri="spotify:track:new")
    assert [(r[0], r[1]) for r in fake.requests] == [
        ("DELETE", f"{API}/playlists/p1/items"),
        ("POST", f"{API}/playlists/p1/items"),
    ]
    assert body_of(fake.requests[0]) == {"items": [{"uri": "spotify:track:old"}]}
    assert body_of(fake.requests[1]) == {"uris": ["spotify:track:new"], "position": 3}


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


def test_access_token_keeps_refresh_token_when_none_returned(
    settings: Settings, clock: FakeClock, state: MemoryState
) -> None:
    fake = FakeUrlopen({("POST", TOKEN_URL): {"access_token": "at-new", "expires_in": 60}})
    c = SpotifyClient(settings, clock, state, urlopen=fake, creds=creds(access_token=None))
    assert c.access_token() == "at-new"
    assert c.creds.refresh_token == "rt-old"
    assert c.creds.expires_at == NOW.timestamp() + 60


def test_liked_rows_refetches_when_cache_lacks_artist_ids(
    client: SpotifyClient, fake: FakeUrlopen, state: MemoryState, settings: Settings
) -> None:
    state.write_json(settings.liked_cache, [{"name": "Old", "artists": ["X"], "uri": "spotify:track:o"}])
    liked_page(fake)
    rows = client.liked_rows()
    assert [r["name"] for r in rows] == ["First Light", "Second Wind", "Third Rail"]
    assert len(fake.requests) == 1


def test_liked_rows_empty_cache_refetched(
    client: SpotifyClient, fake: FakeUrlopen, state: MemoryState, settings: Settings
) -> None:
    state.write_json(settings.liked_cache, [])
    liked_page(fake)
    assert len(client.liked_rows()) == 3


def test_liked_rows_refresh_ignores_cache(
    client: SpotifyClient, fake: FakeUrlopen, state: MemoryState, settings: Settings
) -> None:
    state.write_json(settings.liked_cache, [{"name": "Old", "artists": [], "artist_ids": [], "uri": "u"}])
    liked_page(fake)
    rows = client.liked_rows(refresh=True)
    assert [r["name"] for r in rows] == ["First Light", "Second Wind", "Third Rail"]
    assert state.files[settings.liked_cache] == rows


def test_a_playlist_description_comes_back_as_it_was_written(client: SpotifyClient, fake: FakeUrlopen) -> None:
    # 2026-11-10: Spotify returned the DICE setlist playlist's description with every slash as &#x2F;, so the
    # setlist.fm and teaser URLs in it never compared equal to the ones the flow held.
    escaped = "Setlist: DICE on 2026-11-04, from https:&#x2F;&#x2F;www.setlist.fm&#x2F;setlist&#x2F;dice&#x2F;x.html Rock &amp; roll"
    plain = "Setlist: DICE on 2026-11-04, from https://www.setlist.fm/setlist/dice/x.html Rock & roll"
    rows = [{"id": "a", "description": escaped}, {"id": "b", "description": "no entities"}, {"id": "c"}]
    fake.add("GET", f"{API}/me/playlists?limit=50", {"items": rows, "next": None})
    assert [pl.get("description") for pl in client.my_playlists()] == [plain, "no entities", None]

    fake.add("GET", f"{API}/playlists/a", {"id": "a", "description": escaped})
    assert client.playlist("a", "id,description")["description"] == plain

    fake.add("POST", f"{API}/me/playlists", {"id": "p9", "description": escaped})
    assert client.create_playlist(name="n", description=plain, public=False)["description"] == plain
