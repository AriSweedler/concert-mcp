"""flows/setlist.py on FakeSetlistFm: exact date, venue tie-break, within a day, none, and the failures."""

from __future__ import annotations

from typing import Any

from concert_mcp.flows.services import Services
from concert_mcp.flows.setlist import find_setlist, setlist_outcome
from concert_mcp.result import Kind
from fakes import FakeSetlistFm

ARTIST = "Zed Fox"
DATE = "2026-11-14"


def api_row(
    setlist_id: str,
    event_date: str,
    venue: str = "The Quarry",
    city: str = "San Francisco",
    songs: tuple[str, ...] = ("First Light", "Second Wind", "Third Rail"),
) -> dict[str, Any]:
    """One setlist as the setlist.fm API returns it (eventDate is dd-MM-yyyy)."""
    slug = venue.lower().replace(" ", "-")
    return {
        "id": setlist_id,
        "url": f"https://www.setlist.fm/setlist/zed-fox/2026/{slug}-{setlist_id}.html",
        "eventDate": event_date,
        "artist": {"name": ARTIST},
        "venue": {"name": venue, "city": {"name": city}},
        "tour": {"name": "Fall Tour"},
        "sets": {"set": [{"song": [{"name": s} for s in songs]}]},
    }


def with_setlistfm(svc: Services, fake: FakeSetlistFm | None) -> FakeSetlistFm | None:
    svc.setlistfm = fake
    return fake


# ---------- find_setlist ----------


def test_unexpected_exception_is_failed_with_its_type(svc: Services) -> None:
    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("wires crossed")

    svc.setlistfm.search_setlists = boom  # type: ignore[method-assign]
    res = find_setlist(ARTIST, DATE, None, svc)
    assert (res.match, res.reason) == ("failed", "RuntimeError: wires crossed")


# ---------- setlist_outcome ----------


def test_outcome_needs_an_artist(svc: Services) -> None:
    out = setlist_outcome("", DATE, "", svc)
    assert out.kind is Kind.NEED_USER
    assert out.reply == "I could not read the artist from that. Reply with it and I will add the event."


def test_outcome_needs_a_date(svc: Services) -> None:
    out = setlist_outcome(ARTIST, "", "", svc)
    assert out.kind is Kind.NEED_USER
    assert out.reply == "I could not read the date from that. Reply with it and I will add the event."


def test_outcome_unconfigured(svc: Services) -> None:
    with_setlistfm(svc, None)
    out = setlist_outcome(ARTIST, DATE, "", svc)
    assert out.kind is Kind.ERROR
    assert out.reply == "setlist.fm is not set up on this machine. Admin: run `concert-mcp spotify setlist-auth`."
    assert out.error == "setlistfm not configured"


def test_outcome_failed_is_an_error(svc: Services) -> None:
    with_setlistfm(svc, FakeSetlistFm(fail=True))
    out = setlist_outcome(ARTIST, DATE, "", svc)
    assert out.kind is Kind.ERROR
    assert out.reply == "Setlist lookup failed: setlist.fm refused | path='/search/setlists' status='500'"
    assert out.error == "setlist.fm refused | path='/search/setlists' status='500'"
