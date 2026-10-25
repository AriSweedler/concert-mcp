"""flows/playlists.py: every CLI record shape on FakeSpotify, plus a smoke test of the interactive auth."""

from __future__ import annotations

import socket
import threading
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from concert_mcp.config import load_settings
from concert_mcp.flows import auth
from concert_mcp.flows import playlists as pl
from concert_mcp.flows.services import Services
from concert_mcp.result import FixInput, ProviderError
from conftest import settings_env
from fakes import FakeClock, FakeRunner, FakeSpotify, FakeUrlopen, MemoryState

ZED_FOX = {
    "name": "Zed Fox",
    "id": "artist-zedfox",
    "external_urls": {"spotify": "https://open.spotify.com/artist/artist-zedfox"},
    "followers": {"total": 500000},
    "genres": ["indietronica"],
}
ZED_FOX_FEAT = {
    "name": "ZED FOX feat. Mr Example",
    "id": "artist-jwfeat",
    "external_urls": {"spotify": "https://open.spotify.com/artist/artist-jwfeat"},
    "followers": {"total": 10},
    "genres": [],
}


def playlist(playlist_id: str, name: str, owner: str = "user1", total: int = 2) -> dict[str, Any]:
    return {
        "id": playlist_id,
        "name": name,
        "description": "d",
        "public": False,
        "owner": {"id": owner, "display_name": "Fake User" if owner == "user1" else "Someone"},
        "external_urls": {"spotify": f"https://open.spotify.com/playlist/{playlist_id}"},
        "items": {"total": total},
    }


def row(uri: str, artists: list[str]) -> dict[str, Any]:
    return {"name": uri, "artists": artists, "artist_ids": [], "uri": uri, "url": None, "added_at": None}


@pytest.fixture
def spotify(svc: Services) -> FakeSpotify:
    fake = FakeSpotify(
        artists=[ZED_FOX, ZED_FOX_FEAT],
        liked=[row("spotify:track:t1", ["Zed Fox"]), row("spotify:track:t2", ["ZED FOX feat. Mr Example"])],
        playlists=[playlist("pl1", "Road Trip"), playlist("pl2", "Not Mine", owner="user2")],
        items={"pl1": [row("spotify:track:t1", ["Zed Fox"])]},
    )
    svc.spotify = fake
    return fake


# ---------- require_spotify ----------


def test_require_spotify_returns_the_api(svc: Services, spotify: FakeSpotify) -> None:
    assert pl.require_spotify(svc) is spotify


def test_require_spotify_raises_when_none(svc: Services) -> None:
    svc.spotify = None
    with pytest.raises(ProviderError) as exc:
        pl.require_spotify(svc)
    assert exc.value.provider == "spotify-unconfigured"
    assert exc.value.error == (
        f"not authorized yet | path='{svc.settings.spotify_credentials}' hint='run: concert-mcp spotify auth'"
    )
    assert exc.value.reply == "Spotify is not set up on this machine. Admin: run `concert-mcp spotify auth`."


@pytest.mark.parametrize(
    "call",
    [
        lambda svc: pl.me(svc),
        lambda svc: pl.liked(svc),
        lambda svc: pl.list_playlists(svc),
        lambda svc: pl.playlist_tracks("pl1", svc),
        lambda svc: pl.create_playlist("x", svc),
        lambda svc: pl.add_tracks("pl1", ["spotify:track:t1"], svc),
        lambda svc: pl.rename_playlist("pl1", "x", svc),
        lambda svc: pl.unfollow_playlist("pl1", svc),
        lambda svc: pl.search_artist("x", svc),
    ],
)
def test_every_command_requires_spotify(svc: Services, call: Any) -> None:
    svc.spotify = None
    with pytest.raises(ProviderError) as exc:
        call(svc)
    assert exc.value.provider == "spotify-unconfigured"


# ---------- me / liked / playlists / playlist_tracks ----------


def test_me(svc: Services, spotify: FakeSpotify) -> None:
    assert pl.me(svc) == {
        "id": "user1",
        "display_name": "Fake User",
        "url": "https://open.spotify.com/user/user1",
        "product": None,
    }


def test_liked_all(svc: Services, spotify: FakeSpotify) -> None:
    rec = pl.liked(svc)
    assert (rec["artist"], rec["count"]) == (None, 2)
    assert [t["uri"] for t in rec["tracks"]] == ["spotify:track:t1", "spotify:track:t2"]
    assert spotify.calls == [("liked_rows", {"refresh": False, "deadline": None})]


def test_liked_refresh_flag_is_passed(svc: Services, spotify: FakeSpotify) -> None:
    pl.liked(svc, refresh=True)
    assert spotify.calls == [("liked_rows", {"refresh": True, "deadline": None})]


def test_liked_by_artist_exact_name_case_insensitive(svc: Services, spotify: FakeSpotify) -> None:
    rec = pl.liked(svc, artist="zed fox")
    assert (rec["artist"], rec["count"]) == ("zed fox", 1)
    assert rec["tracks"][0]["uri"] == "spotify:track:t1"
    assert len(spotify.calls) == 1


def test_liked_by_artist_falls_back_to_a_substring_on_a_fresh_fetch(svc: Services, spotify: FakeSpotify) -> None:
    rec = pl.liked(svc, artist="Mr Example")
    assert rec["count"] == 1
    assert rec["tracks"][0]["uri"] == "spotify:track:t2"
    assert spotify.calls == [
        ("liked_rows", {"refresh": False, "deadline": None}),
        ("liked_rows", {"refresh": True, "deadline": None}),
    ]


def test_liked_by_unknown_artist_is_empty(svc: Services, spotify: FakeSpotify) -> None:
    assert pl.liked(svc, artist="Nobody")["count"] == 0


def test_list_playlists(svc: Services, spotify: FakeSpotify) -> None:
    rec = pl.list_playlists(svc)
    assert rec["count"] == 2
    assert rec["playlists"][0] == {
        "name": "Road Trip",
        "id": "pl1",
        "url": "https://open.spotify.com/playlist/pl1",
        "public": False,
        "tracks": 2,
        "owner": "Fake User",
        "owner_id": "user1",
        "description": "d",
    }


def test_playlist_tracks(svc: Services, spotify: FakeSpotify) -> None:
    rec = pl.playlist_tracks("pl1", svc)
    assert (rec["playlist"], rec["count"]) == ("pl1", 1)
    assert rec["tracks"][0]["uri"] == "spotify:track:t1"


# ---------- create_playlist ----------


def test_create_playlist(svc: Services, spotify: FakeSpotify) -> None:
    rec = pl.create_playlist("Fresh Finds", svc, description="new things", public=True)
    assert rec["created"] is True
    assert (rec["name"], rec["id"], rec["public"], rec["description"]) == ("Fresh Finds", "pl1", True, "new things")
    assert spotify.calls[-1] == (
        "create_playlist",
        {"name": "Fresh Finds", "description": "new things", "public": True},
    )


def test_create_playlist_defaults_private_with_empty_description(svc: Services, spotify: FakeSpotify) -> None:
    pl.create_playlist("  Fresh Finds  ", svc)
    assert spotify.calls[-1] == ("create_playlist", {"name": "Fresh Finds", "description": "", "public": False})


def test_create_playlist_refuses_an_existing_name(svc: Services, spotify: FakeSpotify) -> None:
    rec = pl.create_playlist(" road trip ", svc)
    assert rec["created"] is False
    assert (rec["id"], rec["name"]) == ("pl1", "Road Trip")
    assert "dry_run" not in rec
    assert spotify.created == []


def test_create_playlist_dry_run(svc: Services, spotify: FakeSpotify) -> None:
    assert pl.create_playlist("Fresh Finds", svc, dry_run=True) == {
        "created": False,
        "dry_run": True,
        "name": "Fresh Finds",
    }
    assert spotify.created == []


def test_create_playlist_empty_name(svc: Services, spotify: FakeSpotify) -> None:
    with pytest.raises(FixInput) as exc:
        pl.create_playlist("   ", svc)
    assert (exc.value.error, exc.value.fix) == ("empty playlist name", "pass --name")


# ---------- add_tracks ----------


def test_add_tracks_skips_present_uris(svc: Services, spotify: FakeSpotify) -> None:
    rec = pl.add_tracks("pl1", ["spotify:track:t1", " spotify:track:t3 ", "spotify:track:t4"], svc)
    assert rec == {"added": 2, "skipped": 1, "playlist": "pl1"}
    assert spotify.added == [("pl1", ["spotify:track:t3", "spotify:track:t4"])]


def test_add_tracks_with_nothing_new_does_not_post(svc: Services, spotify: FakeSpotify) -> None:
    assert pl.add_tracks("pl1", ["spotify:track:t1"], svc) == {"added": 0, "skipped": 1, "playlist": "pl1"}
    assert spotify.added == []


def test_add_tracks_dry_run(svc: Services, spotify: FakeSpotify) -> None:
    rec = pl.add_tracks("pl1", ["spotify:track:t1", "spotify:track:t3"], svc, dry_run=True)
    assert rec == {"added": 0, "would_add": 1, "skipped": 1, "dry_run": True}
    assert spotify.added == []


def test_add_tracks_validates_uris(svc: Services, spotify: FakeSpotify) -> None:
    with pytest.raises(FixInput) as exc:
        pl.add_tracks("pl1", ["spotify:track:t1", "https://open.spotify.com/track/x"], svc)
    assert exc.value.fix == "use spotify:track:<id>"
    assert "https://open.spotify.com/track/x" in exc.value.error
    assert spotify.added == []


# ---------- rename_playlist ----------


def test_rename_playlist(svc: Services, spotify: FakeSpotify) -> None:
    rec = pl.rename_playlist("pl1", " Road Trip 2 ", svc)
    assert rec == {"renamed": True, "id": "pl1", "from": "Road Trip", "to": "Road Trip 2"}
    assert spotify.renamed == [("pl1", "Road Trip 2")]
    assert spotify.calls[0] == ("playlist", {"playlist_id": "pl1", "fields": "id,name,owner(id)"})


def test_rename_playlist_is_idempotent(svc: Services, spotify: FakeSpotify) -> None:
    rec = pl.rename_playlist("pl1", "Road Trip", svc)
    assert rec == {"renamed": False, "id": "pl1", "name": "Road Trip", "reason": "already that name"}
    assert spotify.renamed == []


def test_rename_playlist_owner_check(svc: Services, spotify: FakeSpotify) -> None:
    with pytest.raises(ProviderError) as exc:
        pl.rename_playlist("pl2", "Mine Now", svc)
    assert exc.value.provider == "spotify"
    assert exc.value.error == "not the owner; Spotify only lets the owner rename | playlist='Not Mine' owner='user2'"
    assert exc.value.reply == "Spotify refused the request. Admin: check the host log."
    assert spotify.renamed == []


def test_rename_playlist_dry_run(svc: Services, spotify: FakeSpotify) -> None:
    rec = pl.rename_playlist("pl1", "Road Trip 2", svc, dry_run=True)
    assert rec == {"renamed": False, "dry_run": True, "id": "pl1", "from": "Road Trip", "to": "Road Trip 2"}
    assert spotify.renamed == []


def test_rename_playlist_empty_name(svc: Services, spotify: FakeSpotify) -> None:
    with pytest.raises(FixInput) as exc:
        pl.rename_playlist("pl1", "  ", svc)
    assert (exc.value.error, exc.value.fix) == ("empty name", "pass --name")


def test_rename_unknown_playlist_propagates(svc: Services, spotify: FakeSpotify) -> None:
    with pytest.raises(ProviderError) as exc:
        pl.rename_playlist("nope", "x", svc)
    assert "status='404'" in exc.value.error


# ---------- unfollow_playlist ----------


def test_unfollow_playlist(svc: Services, spotify: FakeSpotify) -> None:
    assert pl.unfollow_playlist("pl1", svc) == {"unfollowed": True, "playlist": "pl1"}
    assert spotify.unfollowed == ["pl1"]


def test_unfollow_playlist_dry_run(svc: Services, spotify: FakeSpotify) -> None:
    assert pl.unfollow_playlist("pl1", svc, dry_run=True) == {"unfollowed": False, "dry_run": True, "playlist": "pl1"}
    assert spotify.unfollowed == []


# ---------- search_artist ----------


def test_search_artist_limit_five(svc: Services, spotify: FakeSpotify) -> None:
    rec = pl.search_artist("Zed Fox", svc)
    assert rec["query"] == "Zed Fox"
    assert rec["artists"][0] == {
        "name": "Zed Fox",
        "id": "artist-zedfox",
        "url": "https://open.spotify.com/artist/artist-zedfox",
        "followers": 500000,
        "genres": ["indietronica"],
    }
    assert len(rec["artists"]) == 2
    assert spotify.calls == [("search_artists", {"q": "Zed Fox", "limit": 5})]


def test_search_artist_none(svc: Services, spotify: FakeSpotify) -> None:
    assert pl.search_artist("ZZZ Test Band", svc) == {"query": "ZZZ Test Band", "artists": []}


# ---------- auth (interactive; refusals and one wired smoke test) ----------


def test_spotify_auth_refuses_when_credentials_exist(tmp_path: Path) -> None:
    settings = load_settings(settings_env(tmp_path), home=tmp_path)
    settings.spotify_credentials.parent.mkdir(parents=True)
    settings.spotify_credentials.write_text("{}")
    with pytest.raises(ProviderError) as exc:
        auth.spotify_auth(settings, MemoryState())
    assert exc.value.error == (
        f"credentials already exist | path='{settings.spotify_credentials}' hint='pass --force to log in again'"
    )


def test_spotify_auth_manual_needs_a_tty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(settings_env(tmp_path), home=tmp_path)
    monkeypatch.setattr(auth, "_stdin_is_tty", lambda: False)
    with pytest.raises(ProviderError) as exc:
        auth.spotify_auth(settings, MemoryState(), manual=True)
    assert exc.value.error == "--manual needs a terminal to type into | stdin='not a tty'"


def test_spotify_auth_manual_checks_the_id_length(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(settings_env(tmp_path), home=tmp_path)
    monkeypatch.setattr(auth, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(auth, "_getpass", lambda prompt: "short")
    with pytest.raises(ProviderError) as exc:
        auth.spotify_auth(settings, MemoryState(), manual=True)
    assert exc.value.error == "Spotify ids are 32 hex characters | client_id_len='5' client_secret_len='5'"


def test_setlistfm_auth_refuses_when_the_key_exists(tmp_path: Path) -> None:
    settings = load_settings(settings_env(tmp_path), home=tmp_path)
    settings.setlistfm_credentials.parent.mkdir(parents=True)
    settings.setlistfm_credentials.write_text("{}")
    with pytest.raises(ProviderError) as exc:
        auth.setlistfm_auth(settings, MemoryState())
    assert exc.value.error == (
        f"setlist.fm key already stored | path='{settings.setlistfm_credentials}' hint='pass --force to replace it'"
    )


def test_setlistfm_auth_refuses_a_short_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(settings_env(tmp_path), home=tmp_path)
    monkeypatch.setattr(auth, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(auth, "_getpass", lambda prompt: "abc")
    with pytest.raises(ProviderError) as exc:
        auth.setlistfm_auth(settings, MemoryState(), manual=True)
    assert exc.value.error == (
        "that is not a setlist.fm key | length='3' "
        "hint='paste the key from https://www.setlist.fm/settings/api into the 1Password item'"
    )


def test_setlistfm_auth_refuses_the_placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(settings_env(tmp_path), home=tmp_path)
    monkeypatch.setattr(auth, "op_available", lambda: True)
    runner = FakeRunner({("op", "read"): (0, "FILL_THIS_OUT\n", "")})
    with pytest.raises(ProviderError):
        auth.setlistfm_auth(settings, MemoryState(), runner=runner)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_spotify_auth_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, now: Any) -> None:
    """Drive the whole login with a fake browser, a fake 1Password and a fake Spotify: file 600, token stored."""
    port = _free_port()
    env = settings_env(tmp_path) | {"CONCERT_MCP_SPOTIFY_REDIRECT_URI": f"http://127.0.0.1:{port}/callback"}
    settings = load_settings(env, home=tmp_path)
    clock = FakeClock(now)
    state = MemoryState(clock=clock)
    item = settings.op_spotify_item
    vault = settings.op_vault
    runner = FakeRunner(
        {
            ("op", "read", f"op://{vault}/{item}/username"): (0, "a" * 32 + "\n", ""),
            ("op", "read", f"op://{vault}/{item}/credential"): (0, "b" * 32 + "\n", ""),
            ("op", "item", "edit"): (0, "", ""),
        }
    )
    urlopen = FakeUrlopen(
        {
            ("POST", "https://accounts.spotify.com/api/token"): {
                "access_token": "tok",
                "refresh_token": "ref",
                "expires_in": 3600,
                "scope": settings.spotify_scopes,
                "token_type": "Bearer",
            },
            ("GET", "https://api.spotify.com/v1/me"): {"id": "user1", "display_name": "Fake User"},
        }
    )
    monkeypatch.setattr(auth, "op_available", lambda: True)
    threads: list[threading.Thread] = []

    def open_browser(url: str) -> None:
        """The 'browser': read the state from the consent url and hit the callback with a code."""
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert q["client_id"] == ["a" * 32]
        assert q["redirect_uri"] == [settings.spotify_redirect_uri]
        assert q["scope"] == [settings.spotify_scopes]
        assert q["show_dialog"] == ["false"]
        callback = f"{settings.spotify_redirect_uri}?code=the-code&state={q['state'][0]}"
        t = threading.Thread(target=lambda: urllib.request.urlopen(callback, timeout=10).read())
        t.start()
        threads.append(t)

    rec = auth.spotify_auth(settings, state, open_browser=open_browser, runner=runner, urlopen=urlopen, clock=clock)
    for t in threads:
        t.join(timeout=10)

    assert rec == {
        "ok": True,
        "user": "Fake User",
        "user_id": "user1",
        "scope": settings.spotify_scopes,
        "credentials": str(settings.spotify_credentials),
    }
    saved = state.files[settings.spotify_credentials]
    assert (saved["client_id"], saved["client_secret"], saved["refresh_token"]) == ("a" * 32, "b" * 32, "ref")
    assert saved["user_id"] == "user1"
    assert state.modes[settings.spotify_credentials] == 0o600
    token_posts = [r for r in urlopen.requests if r[0] == "POST" and r[1].startswith("https://accounts.spotify.com")]
    assert len(token_posts) == 1
    body = urllib.parse.parse_qs(token_posts[0][3].decode())
    assert body == {
        "grant_type": ["authorization_code"],
        "code": ["the-code"],
        "redirect_uri": [settings.spotify_redirect_uri],
    }
    assert token_posts[0][2]["Authorization"] == "<redacted>"
    edits = [c for c in runner.calls if c[0][:3] == ("op", "item", "edit")]
    assert [list(c[0]) for c in edits] == [
        ["op", "item", "edit", item, "--vault", vault, "refresh token[password]=ref"]
    ]
