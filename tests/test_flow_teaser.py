"""flows/teaser.py on FakeSpotify: create, top up, none, dry run, and every failure that must not raise."""

from __future__ import annotations

from typing import Any

from concert_mcp.flows.services import Services
from concert_mcp.flows.teaser import make_teaser, teaser_outcome
from concert_mcp.result import Kind
from fakes import FakeSpotify

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


def with_spotify(svc: Services, spotify: FakeSpotify | None) -> FakeSpotify | None:
    svc.spotify = spotify
    return spotify


# ---------- resolve_artist / liked_by_artist ----------


# ---------- make_teaser ----------


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


def test_outcome_none_reply(svc: Services) -> None:
    with_spotify(svc, FakeSpotify(artists=[ZED_FOX], liked=[]))
    out = teaser_outcome("Zed Fox", DATE, svc)
    assert out.kind is Kind.OK
    assert out.reply == "**Teaser playlist**\n• **Teaser:** none, no liked songs by Zed Fox yet"


def test_outcome_never_raises(svc: Services) -> None:
    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("wires crossed")

    svc.spotify.search_artists = boom  # type: ignore[method-assign]
    out = teaser_outcome("Zed Fox", DATE, svc)
    assert out.kind is Kind.ERROR
    assert out.reply == "No teaser: RuntimeError: wires crossed"
