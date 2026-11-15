"""text/setlists.py: setlist.fm row shaping, venue ranking, near-date fallback and the lookup result."""

from __future__ import annotations

from concert_mcp.text.setlists import (
    Setlist,
    SetlistResult,
    near_date,
    rank_by_venue,
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
    return Setlist(id=id, url=f"https://www.setlist.fm/{id}", date=date, venue=venue)


def test_setlists() -> None:
    # setlist_row_flips_the_date_and_keeps_what_the_lookup_ranks_by:
    s = setlist_row(api_row())
    assert s == Setlist(
        id="abc123",
        url="https://www.setlist.fm/setlist/zed-fox/2026/abc123.html",
        date="2026-11-14",
        venue="The Quarry",
    )

    # setlist_row_tolerates_missing_parts:
    s = setlist_row({"id": "e", "url": "u", "eventDate": "01-02-2026"})
    assert (s.id, s.url, s.date, s.venue) == ("e", "u", "2026-02-01", "")
    assert setlist_row({}) == Setlist(id="", url="", date="", venue="")

    # setlistfm_date:
    assert setlistfm_date("2026-11-14") == "14-11-2026"
    assert setlistfm_date("2026-11-14T20:30:00") == "14-11-2026"

    # rank_by_venue_orders_by_shared_words_and_is_stable:
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

    # rank_by_venue_no_venue_or_single_row_keeps_order:
    rows = [row("d", "Zebra Hall", "a"), row("d", "The Quarry", "b")]
    assert [r.id for r in rank_by_venue(rows, None)] == ["a", "b"]
    assert [r.id for r in rank_by_venue(rows, "")] == ["a", "b"]
    assert rank_by_venue(rows[:1], "Quarry") == rows[:1]
    assert rank_by_venue([], "Quarry") == []

    # near_date_within_a_day_with_venue_word:
    rows = [
        row("2026-11-13", "The Quarry", "day_before"),
        row("2026-11-15", "The Quarry", "day_after"),
        row("2026-11-16", "The Quarry", "two_days"),
        row("2026-11-14", "Great American Music Hall", "wrong_venue"),
        row("", "The Quarry", "no_date"),
        row("not-a-date", "The Quarry", "garbage"),
    ]
    assert [r.id for r in near_date(rows, "2026-11-14", "The Quarry, San Francisco")] == ["day_before", "day_after"]

    # near_date_nearest_first_and_no_venue_filter:
    rows = [row("2026-11-15", "Anywhere", "one_off"), row("2026-11-14", "Elsewhere", "exact")]
    assert [r.id for r in near_date(rows, "2026-11-14", None)] == ["exact", "one_off"]
    assert [r.id for r in near_date(rows, "2026-11-14", "")] == ["exact", "one_off"]
    assert near_date(rows, "2026-11-14", "The Quarry") == []
    assert [r.id for r in near_date(rows, "2026-11-12", None, max_days=3)] == ["exact", "one_off"]

    # setlist_result_url_only_when_found:
    s = setlist_row(api_row())
    res = SetlistResult(
        found=True, match="exact date", artist="Zed Fox", date="2026-11-14", venue="The Quarry", setlist=s
    )
    assert res.url == s.url
    res = SetlistResult(found=False, match="none", artist="Zed Fox", date="2026-11-14", venue=None)
    assert res.url is None
