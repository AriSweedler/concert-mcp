"""text/setlists.py: setlist.fm row shaping, venue ranking, near-date fallback and the legacy records."""

from __future__ import annotations

import pytest

from concert_mcp.text.setlists import (
    Setlist,
    SetlistResult,
    near_date,
    rank_by_venue,
    search_url,
    setlist_row,
    setlistfm_date,
)


def api_row(
    *, id: str = "abc123", date: str = "14-11-2026", venue: str = "The Quarry", songs=("First Light", "Second Wind")
) -> dict:
    return {
        "id": id,
        "url": f"https://www.setlist.fm/setlist/zed-fox/2026/{id}.html",
        "artist": {"name": "Zed Fox"},
        "eventDate": date,
        "venue": {"name": venue, "city": {"name": "San Francisco"}},
        "tour": {"name": "Cure Tour"},
        "sets": {
            "set": [{"song": [{"name": s} for s in songs[:1]]}, {"encore": 1, "song": [{"name": s} for s in songs[1:]]}]
        },
    }


def row(date: str, venue: str, id: str = "x") -> Setlist:
    return Setlist(
        id=id,
        url=f"https://www.setlist.fm/{id}",
        artist="Zed Fox",
        date=date,
        venue=venue,
        city=None,
        tour=None,
        song_count=0,
        songs=(),
    )


def test_setlist_row_flips_date_and_flattens_songs() -> None:
    s = setlist_row(api_row())
    assert s.date == "2026-11-14"
    assert s.songs == ("First Light", "Second Wind")
    assert s.song_count == 2
    assert s == Setlist(
        id="abc123",
        url="https://www.setlist.fm/setlist/zed-fox/2026/abc123.html",
        artist="Zed Fox",
        date="2026-11-14",
        venue="The Quarry",
        city="San Francisco",
        tour="Cure Tour",
        song_count=2,
        songs=("First Light", "Second Wind"),
    )


def test_setlist_row_skips_unnamed_songs_and_tolerates_missing_parts() -> None:
    s = setlist_row(
        {
            "id": "e",
            "url": "u",
            "eventDate": "01-02-2026",
            "sets": {"set": [{"song": [{"name": ""}, {}, {"name": "Only"}]}]},
        }
    )
    assert s.songs == ("Only",)
    assert s.song_count == 1
    assert (s.artist, s.venue, s.city, s.tour) == ("", "", None, None)
    assert setlist_row({}).date == ""


def test_setlistfm_date() -> None:
    assert setlistfm_date("2026-11-14") == "14-11-2026"
    assert setlistfm_date("2026-11-14T20:30:00") == "14-11-2026"


def test_setlist_to_json_with_and_without_songs() -> None:
    s = setlist_row(api_row())
    rec = s.to_json()
    assert list(rec) == ["id", "url", "artist", "date", "venue", "city", "tour", "song_count"]
    assert s.to_json(songs=True)["songs"] == ["First Light", "Second Wind"]


def test_rank_by_venue_orders_by_shared_words_and_is_stable() -> None:
    rows = [
        row("2026-11-14", "Great American Music Hall", "a"),
        row("2026-11-14", "The Quarry", "b"),
        row("2026-11-14", "Quarry Silver Spring", "c"),
    ]
    ranked = rank_by_venue(rows, "The Quarry")
    assert [r.id for r in ranked] == ["b", "c", "a"]
    # Legacy normalize keeps punctuation: "quarry," is its own word, so only "the" is shared with b.
    assert [r.id for r in rank_by_venue(rows, "The Quarry, San Francisco")] == ["b", "a", "c"]
    # Ties keep the input order.
    tied = rank_by_venue([row("d", "Hall A", "a"), row("d", "Hall B", "b"), row("d", "Hall C", "c")], "Hall")
    assert [r.id for r in tied] == ["a", "b", "c"]


def test_rank_by_venue_no_venue_or_single_row_keeps_order() -> None:
    rows = [row("d", "Zebra Hall", "a"), row("d", "The Quarry", "b")]
    assert [r.id for r in rank_by_venue(rows, None)] == ["a", "b"]
    assert [r.id for r in rank_by_venue(rows, "")] == ["a", "b"]
    assert rank_by_venue(rows[:1], "Quarry") == rows[:1]
    assert rank_by_venue([], "Quarry") == []


def test_near_date_within_a_day_with_venue_word() -> None:
    rows = [
        row("2026-11-13", "The Quarry", "day_before"),
        row("2026-11-15", "The Quarry", "day_after"),
        row("2026-11-16", "The Quarry", "two_days"),
        row("2026-11-14", "Great American Music Hall", "wrong_venue"),
        row("", "The Quarry", "no_date"),
        row("not-a-date", "The Quarry", "garbage"),
    ]
    assert [r.id for r in near_date(rows, "2026-11-14", "The Quarry, San Francisco")] == ["day_before", "day_after"]


def test_near_date_nearest_first_and_no_venue_filter() -> None:
    rows = [row("2026-11-15", "Anywhere", "one_off"), row("2026-11-14", "Elsewhere", "exact")]
    assert [r.id for r in near_date(rows, "2026-11-14", None)] == ["exact", "one_off"]
    assert [r.id for r in near_date(rows, "2026-11-14", "")] == ["exact", "one_off"]
    assert near_date(rows, "2026-11-14", "The Quarry") == []
    assert [r.id for r in near_date(rows, "2026-11-12", None, max_days=3)] == ["exact", "one_off"]


def test_search_url_quotes_spaces() -> None:
    assert search_url("Zed Fox") == "https://www.setlist.fm/search?query=Zed%20Fox"
    assert search_url("Simon & Garfunkel") == "https://www.setlist.fm/search?query=Simon%20%26%20Garfunkel"


def test_setlist_result_found() -> None:
    s = setlist_row(api_row())
    res = SetlistResult(
        found=True, match="exact date", artist="Zed Fox", date="2026-11-14", venue="The Quarry", setlist=s
    )
    assert res.url == s.url
    assert res.song_count == 2
    rec = res.to_json()
    assert rec == {"found": True, "match": "exact date", **s.to_json()}
    assert res.to_json(songs=True)["songs"] == ["First Light", "Second Wind"]
    assert "search_url" not in rec


def test_setlist_result_not_found() -> None:
    res = SetlistResult(
        found=False, match="none", artist="Zed Fox", date="2026-11-14", venue=None, search_url=search_url("Zed Fox")
    )
    assert res.url is None
    assert res.song_count == 0
    assert res.to_json() == {
        "found": False,
        "artist": "Zed Fox",
        "date": "2026-11-14",
        "venue": None,
        "match": "none",
        "search_url": "https://www.setlist.fm/search?query=Zed%20Fox",
    }
    assert res.to_json(songs=True) == res.to_json()


@pytest.mark.parametrize("match", ["unavailable", "failed"])
def test_setlist_result_error_carries_reason(match: str) -> None:
    res = SetlistResult(
        found=False,
        match=match,
        artist="Zed Fox",
        date="2026-11-14",
        venue="The Quarry",
        reason="setlist.fm refused",
    )
    rec = res.to_json()
    assert rec["found"] is False
    assert rec["match"] == match
    assert rec["error"] == "setlist.fm refused"
    assert rec["search_url"] is None
