"""flows/setlist.py on FakeSetlistFm: exact date, venue tie-break, within a day, none, and the failures."""

from __future__ import annotations

from typing import Any

import pytest

from concert_mcp.flows.services import Services
from concert_mcp.flows.setlist import find_setlist, setlist_outcome
from concert_mcp.result import FixInput, Kind
from fakes import FakeSetlistFm

ARTIST = "Zed Fox"
DATE = "2026-11-14"
SEARCH_URL = "https://www.setlist.fm/search?query=Zed%20Fox"


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


def test_exact_date(svc: Services) -> None:
    fake = with_setlistfm(
        svc, FakeSetlistFm({(ARTIST, "14-11-2026", None): {"setlist": [api_row("abc123", "14-11-2026")]}})
    )
    res = find_setlist(ARTIST, DATE, None, svc)
    assert res.found is True
    assert res.match == "exact date"
    assert res.setlist.id == "abc123"
    assert res.setlist.date == DATE
    assert res.setlist.venue == "The Quarry"
    assert res.setlist.songs == ("First Light", "Second Wind", "Third Rail")
    assert (res.artist, res.date, res.venue) == (ARTIST, DATE, None)
    assert fake.calls == [{"artist": ARTIST, "date": "14-11-2026", "year": None, "page": 1}]


def test_exact_date_accepts_slash_date(svc: Services) -> None:
    with_setlistfm(svc, FakeSetlistFm({(ARTIST, "14-11-2026", None): {"setlist": [api_row("abc123", "14-11-2026")]}}))
    res = find_setlist(ARTIST, "2026/11/14", "The Quarry", svc)
    assert (res.found, res.date) == (True, DATE)


def test_venue_breaks_a_same_day_tie(svc: Services) -> None:
    rows = [api_row("other", "14-11-2026", venue="The Other Hall"), api_row("right", "14-11-2026", venue="The Quarry")]
    with_setlistfm(svc, FakeSetlistFm({(ARTIST, "14-11-2026", None): {"setlist": rows}}))
    res = find_setlist(ARTIST, DATE, "The Quarry", svc)
    assert (res.match, res.setlist.id) == ("exact date", "right")


def test_no_venue_keeps_the_api_order(svc: Services) -> None:
    rows = [api_row("first", "14-11-2026", venue="The Other Hall"), api_row("second", "14-11-2026", venue="The Quarry")]
    with_setlistfm(svc, FakeSetlistFm({(ARTIST, "14-11-2026", None): {"setlist": rows}}))
    assert find_setlist(ARTIST, DATE, None, svc).setlist.id == "first"


def test_within_a_day_with_a_venue_word(svc: Services) -> None:
    fake = with_setlistfm(
        svc,
        FakeSetlistFm(
            {
                (ARTIST, "14-11-2026", None): None,
                (ARTIST, None, "2026"): {
                    "setlist": [
                        api_row("far", "01-03-2026", venue="The Quarry"),
                        api_row("near-wrong-venue", "13-11-2026", venue="The Other Hall"),
                        api_row("near", "13-11-2026", venue="The Quarry"),
                    ]
                },
            }
        ),
    )
    res = find_setlist(ARTIST, DATE, "Quarry", svc)
    assert (res.found, res.match, res.setlist.id) == (True, "within a day", "near")
    assert fake.calls == [
        {"artist": ARTIST, "date": "14-11-2026", "year": None, "page": 1},
        {"artist": ARTIST, "date": None, "year": "2026", "page": 1},
    ]


def test_within_a_day_without_a_venue_takes_the_nearest(svc: Services) -> None:
    with_setlistfm(
        svc,
        FakeSetlistFm(
            {
                (ARTIST, None, "2026"): {
                    "setlist": [api_row("after", "15-11-2026", venue="The Other Hall"), api_row("far", "20-11-2026")]
                },
            }
        ),
    )
    res = find_setlist(ARTIST, DATE, None, svc)
    assert (res.match, res.setlist.id) == ("within a day", "after")


def test_none_found(svc: Services) -> None:
    with_setlistfm(svc, FakeSetlistFm({}))
    res = find_setlist(ARTIST, DATE, "The Quarry", svc)
    assert res.found is False
    assert res.match == "none"
    assert res.setlist is None
    assert res.search_url == SEARCH_URL
    assert res.reason is None
    assert (res.artist, res.date, res.venue) == (ARTIST, DATE, "The Quarry")


def test_none_when_the_year_search_is_empty(svc: Services) -> None:
    with_setlistfm(svc, FakeSetlistFm({(ARTIST, None, "2026"): {"setlist": [], "total": 0}}))
    assert find_setlist(ARTIST, DATE, None, svc).match == "none"


def test_unavailable_when_setlistfm_is_not_configured(svc: Services) -> None:
    with_setlistfm(svc, None)
    res = find_setlist(ARTIST, DATE, "The Quarry", svc)
    assert (res.found, res.match, res.reason) == (False, "unavailable", "setlist.fm is not set up")
    assert (res.artist, res.date, res.venue, res.search_url) == (ARTIST, DATE, "The Quarry", None)


def test_failed_keeps_the_provider_reason_and_the_search_url(svc: Services) -> None:
    with_setlistfm(svc, FakeSetlistFm(fail=True))
    res = find_setlist(ARTIST, DATE, None, svc)
    assert (res.found, res.match) == (False, "failed")
    assert res.reason == "setlist.fm refused | path='/search/setlists' status='500'"
    assert res.search_url == SEARCH_URL


def test_unexpected_exception_is_failed_with_its_type(svc: Services) -> None:
    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("wires crossed")

    svc.setlistfm.search_setlists = boom  # type: ignore[method-assign]
    res = find_setlist(ARTIST, DATE, None, svc)
    assert (res.match, res.reason) == ("failed", "RuntimeError: wires crossed")


def test_bad_date_propagates_as_fix_input(svc: Services) -> None:
    with pytest.raises(FixInput) as exc:
        find_setlist(ARTIST, "someday", None, svc)
    assert exc.value.fix == 'date must look like "2026-11-03"'
    assert svc.setlistfm.calls == []


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


def test_outcome_found_reply(svc: Services) -> None:
    with_setlistfm(svc, FakeSetlistFm({(ARTIST, "14-11-2026", None): {"setlist": [api_row("abc123", "14-11-2026")]}}))
    out = setlist_outcome(ARTIST, DATE, "The Quarry", svc)
    assert out.kind is Kind.OK
    assert out.reply == (
        "**Setlist**\n"
        "• **Setlist:** [setlist.fm](https://www.setlist.fm/setlist/zed-fox/2026/the-quarry-abc123.html) "
        "3 songs so far"
    )
    # The standalone tool queues nothing, so it promises no weekly check (concert_add does both).
    assert "checking weekly" not in out.reply
    rec = out.to_json()
    assert rec["ok"] is True
    assert (rec["found"], rec["match"], rec["id"], rec["song_count"]) == (True, "exact date", "abc123", 3)
    assert "songs" not in rec


def test_outcome_found_with_no_songs_yet(svc: Services) -> None:
    with_setlistfm(
        svc, FakeSetlistFm({(ARTIST, "14-11-2026", None): {"setlist": [api_row("abc123", "14-11-2026", songs=())]}})
    )
    out = setlist_outcome(ARTIST, DATE, "", svc)
    assert "no songs yet" in out.reply and "checking weekly" not in out.reply


def test_outcome_none_reply_carries_the_search_url(svc: Services) -> None:
    with_setlistfm(svc, FakeSetlistFm({}))
    out = setlist_outcome(ARTIST, DATE, "", svc)
    assert out.kind is Kind.OK
    assert out.reply == ("**Setlist**\n• **Setlist:** not on setlist.fm yet Search: " + SEARCH_URL)
    rec = out.to_json()
    assert (rec["found"], rec["match"], rec["search_url"]) == (False, "none", SEARCH_URL)


def test_outcome_empty_venue_is_none(svc: Services) -> None:
    with_setlistfm(svc, FakeSetlistFm({}))
    assert setlist_outcome(ARTIST, DATE, "", svc).to_json()["venue"] is None


def test_outcome_failed_is_an_error(svc: Services) -> None:
    with_setlistfm(svc, FakeSetlistFm(fail=True))
    out = setlist_outcome(ARTIST, DATE, "", svc)
    assert out.kind is Kind.ERROR
    assert out.reply == "Setlist lookup failed: setlist.fm refused | path='/search/setlists' status='500'"
    assert out.error == "setlist.fm refused | path='/search/setlists' status='500'"


def test_outcome_bad_date_is_a_fix(svc: Services) -> None:
    out = setlist_outcome(ARTIST, "someday", "", svc)
    assert out.kind is Kind.FIX
    assert out.fix == 'date must look like "2026-11-03"'
