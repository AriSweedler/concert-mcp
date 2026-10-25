"""flows/teaser.py on FakeSpotify: create, top up, none, dry run, and every failure that must not raise."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from concert_mcp.config import Settings
from concert_mcp.flows.services import Services
from concert_mcp.flows.teaser import liked_by_artist, make_teaser, resolve_artist, teaser_outcome
from concert_mcp.result import FixInput, Kind
from concert_mcp.text.artists import Artist, NoArtistMatch
from concert_mcp.text.naming import teaser_description
from fakes import FakeSpotify, plain

DATE = "2026-11-14"
NAME = "2026-11-14 | Zed Fox"
URIS = ["spotify:track:t1", "spotify:track:t2", "spotify:track:t3"]

ZED_FOX = {
    "name": "Zed Fox",
    "id": "artist-zedfox",
    "external_urls": {"spotify": "https://open.spotify.com/artist/artist-zedfox"},
    "followers": {"total": 500000},
    "genres": ["indietronica"],
}
ZED_FOX_AND_FRIENDS = {
    "name": "Zed Fox & Friends",
    "id": "artist-jwf",
    "external_urls": {"spotify": "https://open.spotify.com/artist/artist-jwf"},
    "followers": {"total": 1200},
}
BAND_OF_HORSES = {
    "name": "Band of Horses",
    "id": "artist-boh",
    "external_urls": {"spotify": "https://open.spotify.com/artist/artist-boh"},
    "followers": {"total": 900000},
}


def liked_row(name: str, uri: str, added_at: str, artist: dict[str, Any] = ZED_FOX) -> dict[str, Any]:
    return {
        "name": name,
        "artists": [artist["name"]],
        "artist_ids": [artist["id"]],
        "album": "Test Album",
        "uri": uri,
        "url": f"https://open.spotify.com/track/{uri.rsplit(':', 1)[-1]}",
        "duration_ms": 200000,
        "added_at": added_at,
    }


LIKED = [
    liked_row("First Light", URIS[0], "2019-03-01T10:00:00Z"),
    liked_row("Second Wind", URIS[1], "2021-06-15T10:00:00Z"),
    liked_row("Third Rail", URIS[2], "2024-01-20T10:00:00Z"),
]


def existing_playlist(playlist_id: str = "pl9", name: str = NAME) -> dict[str, Any]:
    return {
        "id": playlist_id,
        "name": name,
        "description": "old",
        "public": False,
        "owner": {"id": "user1", "display_name": "Fake User"},
        "external_urls": {"spotify": f"https://open.spotify.com/playlist/{playlist_id}"},
        "items": {"total": 1},
    }


def with_spotify(svc: Services, spotify: FakeSpotify | None) -> FakeSpotify | None:
    svc.spotify = spotify
    return spotify


# ---------- resolve_artist / liked_by_artist ----------


def test_resolve_artist_exact_is_not_corrected(svc: Services, capsys: pytest.CaptureFixture[str]) -> None:
    artist, corrected = resolve_artist("Zed Fox", svc.spotify)
    assert artist == Artist("Zed Fox", "artist-zedfox", "https://open.spotify.com/artist/artist-zedfox")
    assert corrected is False
    assert "artist name corrected" not in plain(capsys.readouterr().err)


def test_resolve_artist_searches_ten(svc: Services) -> None:
    resolve_artist("Zed Fox", svc.spotify)
    assert ("search_artists", {"q": "Zed Fox", "limit": 10}) in svc.spotify.calls


def test_resolve_artist_corrected_logs_a_warning(svc: Services, capsys: pytest.CaptureFixture[str]) -> None:
    fake = with_spotify(svc, FakeSpotify(artists=[ZED_FOX_AND_FRIENDS]))
    artist, corrected = resolve_artist("Zed Fox", fake)
    assert (artist.name, corrected) == ("Zed Fox & Friends", True)
    assert "artist name corrected | asked='Zed Fox' spotify='Zed Fox & Friends'" in plain(capsys.readouterr().err)


def test_resolve_artist_no_match_propagates(svc: Services) -> None:
    fake = with_spotify(svc, FakeSpotify(artists=[]))
    with pytest.raises(NoArtistMatch) as exc:
        resolve_artist("ZZZ Test Band", fake)
    assert str(exc.value) == "no Spotify artist matches | artist='ZZZ Test Band'"


def test_liked_by_artist_refreshes_once_when_the_cache_has_none(svc: Services) -> None:
    fake = with_spotify(svc, FakeSpotify(artists=[ZED_FOX], liked=[]))
    artist = Artist("Zed Fox", "artist-zedfox", None)
    assert liked_by_artist(artist, fake, deadline=99.0) == []
    assert [c for c in fake.calls if c[0] == "liked_rows"] == [
        ("liked_rows", {"refresh": False, "deadline": 99.0}),
        ("liked_rows", {"refresh": True, "deadline": 99.0}),
    ]


def test_liked_by_artist_keeps_cache_order(svc: Services) -> None:
    rows = liked_by_artist(Artist("Zed Fox", "artist-zedfox", None), svc.spotify)
    assert [r["uri"] for r in rows] == URIS
    assert len([c for c in svc.spotify.calls if c[0] == "liked_rows"]) == 1


# ---------- make_teaser ----------


def test_created(svc: Services) -> None:
    fake = svc.spotify
    res = make_teaser("Zed Fox", DATE, svc)
    assert res.status == "created"
    assert (res.artist.name, res.date, res.name) == ("Zed Fox", DATE, NAME)
    assert (res.liked_count, res.added, res.already_there, res.exists) == (3, 3, 0, False)
    assert res.tracks == ("First Light", "Second Wind", "Third Rail")
    assert (res.id, res.url) == ("pl1", "https://open.spotify.com/playlist/pl1")
    assert [(p["name"], p["description"], p["public"]) for p in fake.created] == [
        (NAME, teaser_description("Zed Fox", DATE), False)
    ]
    assert fake.added == [("pl1", URIS)]


def test_created_uses_the_caller_description(svc: Services) -> None:
    make_teaser("Zed Fox", DATE, svc, description="my words")
    assert svc.spotify.created[0]["description"] == "my words"


def test_created_accepts_slash_date(svc: Services) -> None:
    res = make_teaser("Zed Fox", "2026/11/14", svc)
    assert (res.date, res.name) == (DATE, NAME)


def test_updated_adds_only_new_uris(svc: Services) -> None:
    fake = with_spotify(
        svc,
        FakeSpotify(
            artists=[ZED_FOX],
            liked=LIKED,
            playlists=[existing_playlist()],
            items={"pl9": [{"uri": URIS[0], "name": "First Light", "artists": ["Zed Fox"], "added_at": None}]},
        ),
    )
    res = make_teaser("Zed Fox", DATE, svc)
    assert res.status == "updated"
    assert (res.liked_count, res.added, res.already_there, res.exists) == (3, 2, 1, True)
    assert (res.id, res.url) == ("pl9", "https://open.spotify.com/playlist/pl9")
    assert fake.created == []
    assert fake.added == [("pl9", URIS[1:])]


def test_updated_with_nothing_new_does_not_post(svc: Services) -> None:
    fake = with_spotify(
        svc,
        FakeSpotify(
            artists=[ZED_FOX],
            liked=LIKED,
            playlists=[existing_playlist()],
            items={"pl9": [{"uri": u, "name": u, "artists": [], "added_at": None} for u in URIS]},
        ),
    )
    res = make_teaser("Zed Fox", DATE, svc)
    assert (res.status, res.added, res.already_there) == ("updated", 0, 3)
    assert fake.added == []


def test_updated_matches_the_name_case_insensitively(svc: Services) -> None:
    fake = with_spotify(
        svc, FakeSpotify(artists=[ZED_FOX], liked=LIKED, playlists=[existing_playlist(name="2026-11-14 | ZED FOX")])
    )
    res = make_teaser("Zed Fox", DATE, svc)
    assert (res.status, res.id) == ("updated", "pl9")
    assert fake.created == []


def test_none_when_nothing_is_liked(svc: Services) -> None:
    fake = with_spotify(svc, FakeSpotify(artists=[ZED_FOX], liked=[]))
    res = make_teaser("Zed Fox", DATE, svc)
    assert res.status == "none"
    assert (res.artist.name, res.name, res.liked_count, res.tracks) == ("Zed Fox", NAME, 0, ())
    assert res.to_json()["reply"] == "No teaser: you have not liked any songs by Zed Fox yet."
    assert fake.created == [] and fake.added == []


def test_dry_run_writes_nothing(svc: Services) -> None:
    fake = svc.spotify
    res = make_teaser("Zed Fox", DATE, svc, dry_run=True)
    assert res.status == "dry_run"
    assert (res.liked_count, res.exists, res.tracks) == (3, False, ("First Light", "Second Wind", "Third Rail"))
    assert fake.created == [] and fake.added == []
    assert not any(c[0] in ("create_playlist", "add_items", "playlist_items") for c in fake.calls)


def test_dry_run_reports_an_existing_playlist(svc: Services) -> None:
    with_spotify(svc, FakeSpotify(artists=[ZED_FOX], liked=LIKED, playlists=[existing_playlist()]))
    res = make_teaser("Zed Fox", DATE, svc, dry_run=True)
    assert (res.status, res.exists) == ("dry_run", True)


def test_unavailable_when_spotify_is_not_configured(svc: Services) -> None:
    with_spotify(svc, None)
    res = make_teaser("Zed Fox", DATE, svc)
    assert (res.status, res.artist, res.name, res.reason) == ("unavailable", None, None, "Spotify is not set up")
    assert res.date == DATE


def test_no_close_artist_is_failed(svc: Services) -> None:
    fake = with_spotify(svc, FakeSpotify(artists=[BAND_OF_HORSES], liked=LIKED))
    fake.search_artists = lambda q, limit=10: [dict(BAND_OF_HORSES)]  # type: ignore[method-assign]
    res = make_teaser("ZZZ Test Band", DATE, svc)
    assert res.status == "failed"
    assert res.reason.startswith("no close Spotify artist match | artist='ZZZ Test Band'")
    assert "Band of Horses" in res.reason


def test_no_artist_at_all_is_failed(svc: Services) -> None:
    with_spotify(svc, FakeSpotify(artists=[]))
    res = make_teaser("ZZZ Test Band", DATE, svc)
    assert (res.status, res.reason) == ("failed", "no Spotify artist matches | artist='ZZZ Test Band'")


def test_timeout_when_liked_songs_take_too_long(svc: Services) -> None:
    with_spotify(svc, FakeSpotify(artists=[ZED_FOX], liked=LIKED, fail="liked"))
    res = make_teaser("Zed Fox", DATE, svc)
    assert (res.status, res.reason) == ("timeout", "Spotify took longer than 150 s")
    assert (res.artist, res.name, res.liked_count) == (None, None, 0)


def test_other_provider_error_is_failed(svc: Services) -> None:
    with_spotify(svc, FakeSpotify(artists=[ZED_FOX], liked=LIKED, fail="create_playlist"))
    res = make_teaser("Zed Fox", DATE, svc)
    assert res.status == "failed"
    assert res.reason == "Spotify API refused | method='create_playlist' status='500'"


def test_unexpected_exception_is_failed_with_its_type(svc: Services) -> None:
    def boom(deadline: float | None = None) -> list[dict[str, Any]]:
        raise RuntimeError("wires crossed")

    svc.spotify.my_playlists = boom  # type: ignore[method-assign]
    res = make_teaser("Zed Fox", DATE, svc)
    assert (res.status, res.reason) == ("failed", "RuntimeError: wires crossed")


def test_deadline_is_now_plus_the_teaser_timeout(svc: Services) -> None:
    make_teaser("Zed Fox", DATE, svc)
    liked_calls = [c for c in svc.spotify.calls if c[0] == "liked_rows"]
    deadline = pytest.approx(svc.clock.time() + svc.settings.teaser_timeout_s)
    assert liked_calls[0][1]["deadline"] == deadline
    # The cap covers the whole step: every paged read carries the same deadline.
    assert svc.spotify.deadlines == [("my_playlists", deadline), ("playlist_items", deadline)]


def test_writes_after_the_deadline_are_a_timeout_not_a_playlist(svc: Services) -> None:
    """Liked Songs took the whole budget: nothing is created, the bullet says Spotify took too long."""
    fake = svc.spotify

    def slow_liked(refresh: bool = False, deadline: float | None = None) -> list[dict[str, Any]]:
        svc.clock.advance(svc.settings.teaser_timeout_s + 1)
        return [dict(r) for r in LIKED]

    fake.liked_rows = slow_liked  # type: ignore[method-assign]
    res = make_teaser("Zed Fox", DATE, svc)
    assert (res.status, res.reason) == ("timeout", "Spotify took longer than 150 s")
    assert fake.created == [] and fake.added == []


def test_bad_date_propagates_as_fix_input(svc: Services) -> None:
    with pytest.raises(FixInput) as exc:
        make_teaser("Zed Fox", "next friday", svc)
    assert exc.value.fix == 'date must look like "2026-11-03"'
    assert svc.spotify.calls == []


def test_bad_date_is_checked_even_when_spotify_is_down(svc: Services) -> None:
    """unavailable wins over the date check: concert_add has already validated its own date."""
    with_spotify(svc, None)
    assert make_teaser("Zed Fox", "garbage", svc).status == "unavailable"


# ---------- teaser_outcome ----------


def test_outcome_needs_an_artist(svc: Services) -> None:
    out = teaser_outcome("", DATE, svc)
    assert out.kind is Kind.NEED_USER
    assert out.reply == "I could not read the artist from that. Reply with it and I will add the event."
    assert out.error == "artist is required"


def test_outcome_needs_a_date(svc: Services) -> None:
    out = teaser_outcome("Zed Fox", "", svc)
    assert out.kind is Kind.NEED_USER
    assert out.reply == "I could not read the date from that. Reply with it and I will add the event."


def test_outcome_unconfigured(svc: Services) -> None:
    with_spotify(svc, None)
    out = teaser_outcome("Zed Fox", DATE, svc)
    assert out.kind is Kind.ERROR
    assert out.reply == "Spotify is not set up on this machine. Admin: run `concert-mcp spotify auth`."
    assert out.error == "spotify not configured"


def test_outcome_ok_reply(svc: Services) -> None:
    out = teaser_outcome("Zed Fox", DATE, svc)
    assert out.kind is Kind.OK
    assert out.reply == (
        "**Teaser playlist**\n"
        "• **Teaser:** [2026-11-14 | Zed Fox](https://open.spotify.com/playlist/pl1) 3 liked songs. "
        "Move it to Concerts › Teasers in Spotify."
    )
    rec = out.to_json()
    assert rec["ok"] is True
    assert rec["created"] is True
    assert rec["name"] == NAME
    assert rec["liked_count"] == 3


def test_outcome_updated_reply_says_topped_up(svc: Services) -> None:
    with_spotify(svc, FakeSpotify(artists=[ZED_FOX], liked=LIKED, playlists=[existing_playlist()]))
    out = teaser_outcome("Zed Fox", DATE, svc)
    assert out.reply == (
        "**Teaser playlist**\n"
        "• **Teaser:** [2026-11-14 | Zed Fox](https://open.spotify.com/playlist/pl9) 3 liked songs "
        "(already existed, topped up). Move it to Concerts › Teasers in Spotify."
    )


def test_outcome_none_reply(svc: Services) -> None:
    with_spotify(svc, FakeSpotify(artists=[ZED_FOX], liked=[]))
    out = teaser_outcome("Zed Fox", DATE, svc)
    assert out.kind is Kind.OK
    assert out.reply == "**Teaser playlist**\n• **Teaser:** none, no liked songs by Zed Fox yet"


def test_outcome_dry_run(svc: Services) -> None:
    out = teaser_outcome("Zed Fox", DATE, svc, dry_run=True)
    assert out.kind is Kind.DRY_RUN
    assert out.reply == "**Teaser playlist**\n• **Teaser:** would make 2026-11-14 | Zed Fox (3 liked songs)"
    assert out.to_json()["dryRun"] is True
    assert svc.spotify.created == []


def test_outcome_failed_is_an_error(svc: Services) -> None:
    with_spotify(svc, FakeSpotify(artists=[]))
    out = teaser_outcome("ZZZ Test Band", DATE, svc)
    assert out.kind is Kind.ERROR
    assert out.reply == "No teaser: no Spotify artist matches | artist='ZZZ Test Band'"
    assert out.error == "no Spotify artist matches | artist='ZZZ Test Band'"
    assert out.to_json()["ok"] is False


def test_outcome_timeout_is_an_error(svc: Services) -> None:
    with_spotify(svc, FakeSpotify(artists=[ZED_FOX], liked=LIKED, fail="liked"))
    out = teaser_outcome("Zed Fox", DATE, svc)
    assert out.kind is Kind.ERROR
    assert out.reply == "No teaser: Spotify took longer than 150 s"


def test_outcome_bad_date_is_a_fix(svc: Services) -> None:
    out = teaser_outcome("Zed Fox", "tomorrow", svc)
    assert out.kind is Kind.FIX
    assert out.fix == 'date must look like "2026-11-03"'
    assert out.reply is None


def test_outcome_never_raises(svc: Services) -> None:
    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("wires crossed")

    svc.spotify.search_artists = boom  # type: ignore[method-assign]
    out = teaser_outcome("Zed Fox", DATE, svc)
    assert out.kind is Kind.ERROR
    assert out.reply == "No teaser: RuntimeError: wires crossed"


def test_services_resolve_spotify_lazily_after_auth(settings: Settings, now: datetime) -> None:
    """A long-running server must see `concert-mcp spotify auth` without a restart: a None is re-resolved per read."""
    from concert_mcp.flows.services import Services
    from fakes import FakeCalendar, FakeChat, FakeClock, FakeContext, MemoryState

    configured: list[FakeSpotify | None] = [None]
    calls = 0

    def factory() -> FakeSpotify | None:
        nonlocal calls
        calls += 1
        return configured[0]

    clock = FakeClock(now)
    svc = Services(
        settings=settings,
        calendar=FakeCalendar(calendars=[]),
        chat=FakeChat(),
        context=FakeContext(route=None, session_id=None, text=""),
        state=MemoryState(clock=clock),
        clock=clock,
        spotify_factory=factory,
    )
    assert make_teaser("Zed Fox", DATE, svc).status == "unavailable"
    assert make_teaser("Zed Fox", DATE, svc).status == "unavailable"
    assert calls == 2  # not configured yet: asked again on every read
    configured[0] = FakeSpotify(artists=[ZED_FOX], liked=LIKED)
    assert make_teaser("Zed Fox", DATE, svc).status == "created"
    assert make_teaser("Zed Fox", DATE, svc).status == "updated"
    assert calls == 3  # resolved once, then kept
    assert svc.setlistfm is None  # no factory: stays None
