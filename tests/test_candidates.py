"""text/candidates.py: the cal-del candidate matching, the search window and the duplicate guard."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from concert_mcp.result import FixInput
from concert_mcp.text.candidates import (
    Candidate,
    candidate_from_event,
    duplicate_of,
    filter_candidates,
    missing_link_lines,
    patched_description,
    pick_index,
    search_window,
    teaser_url_in,
)

TZ = ZoneInfo("America/Los_Angeles")
TEASER_URL = "https://open.spotify.com/playlist/pl1"


def event(summary: str | None, *, status: str = "confirmed", **kw: Any) -> dict[str, Any]:
    e: dict[str, Any] = {
        "id": f"id-{(summary or 'none').lower().replace(' ', '-')}",
        "summary": summary,
        "status": status,
        "start": {"dateTime": "2026-11-14T20:30:00-08:00"},
        "end": {"dateTime": "2026-11-14T22:30:00-08:00"},
        "htmlLink": "https://www.google.com/calendar/event?eid=abc123",
    }
    e.update(kw)
    return e


# ---------- Candidate ----------


def test_candidate_json_round_trip_uses_legacy_keys() -> None:
    c = Candidate(
        id="ev1",
        calendar_id="cal-concerts",
        calendar="Concerts",
        summary="Zed Fox",
        when="Sat Nov 14, 8:30 PM – 10:30 PM PST",
        start="2026-11-14T20:30:00-08:00",
        location="The Quarry",
        html_link="https://www.google.com/calendar/event?eid=abc123",
        description=f"Teaser playlist: {TEASER_URL}",
    )
    d = c.to_json()
    assert list(d) == [
        "id",
        "calendarId",
        "calendar",
        "summary",
        "when",
        "start",
        "location",
        "htmlLink",
        "description",
    ]
    assert d["calendarId"] == "cal-concerts"
    assert d["htmlLink"] == c.html_link
    assert Candidate.from_json(d) == c


def test_candidate_from_json_tolerates_missing_optional_keys() -> None:
    c = Candidate.from_json({"id": "x", "calendarId": "primary", "calendar": "Personal", "summary": "Dinner"})
    assert c.location == ""
    assert c.description == ""
    assert c.html_link is None
    assert c.when == ""


def test_candidate_from_event_fills_defaults() -> None:
    c = candidate_from_event(event(None), "cal-concerts", "Concerts", TZ)
    assert c.summary == "(no title)"
    assert c.location == ""
    assert c.description == ""
    assert c.calendar_id == "cal-concerts"
    assert c.calendar == "Concerts"
    assert c.html_link == "https://www.google.com/calendar/event?eid=abc123"
    assert c.start == "2026-11-14T20:30:00-08:00"
    assert c.when.startswith("Sat Nov 14")


def test_candidate_from_event_keeps_location_and_description() -> None:
    c = candidate_from_event(
        event("Zed Fox", location="The Quarry, San Francisco", description="Doors: 7:30 PM"), "c", "Concerts", TZ
    )
    assert c.location == "The Quarry, San Francisco"
    assert c.description == "Doors: 7:30 PM"


# ---------- filter ----------


def test_filter_drops_cancelled_and_non_matching_keeps_word_subset() -> None:
    events = [
        event("Zed Fox"),
        event("ZED FOX & Friends"),
        event("Zed Fox", status="cancelled", id="cancelled-one"),
        event("Bonobo"),
        event(None),
    ]
    got = filter_candidates(events, "zed fox", "cal-concerts", "Concerts", TZ)
    assert [c.summary for c in got] == ["Zed Fox", "ZED FOX & Friends"]
    assert all(c.calendar == "Concerts" and c.calendar_id == "cal-concerts" for c in got)


def test_filter_with_empty_title_keeps_every_live_event() -> None:
    events = [event("Zed Fox"), event("Bonobo", status="cancelled"), event(None)]
    got = filter_candidates(events, "", "primary", "Personal", TZ)
    assert [c.summary for c in got] == ["Zed Fox", "(no title)"]


# ---------- search window ----------


def test_search_window_with_date_is_day_minus_one_to_day_plus_two_in_tz() -> None:
    now = datetime(2026, 10, 25, 12, 0, tzinfo=ZoneInfo("UTC"))
    lo, hi = search_window("2026-11-14", now, TZ)
    day = datetime(2026, 11, 14, tzinfo=TZ)
    assert lo == day - timedelta(days=1)
    assert hi == day + timedelta(days=2)
    assert lo.tzinfo is TZ and hi.tzinfo is TZ


def test_search_window_accepts_a_longer_date_prefix() -> None:
    now = datetime(2026, 10, 25, 12, 0, tzinfo=ZoneInfo("UTC"))
    lo, _ = search_window("2026-11-14T20:30:00-08:00", now, TZ)
    assert lo == datetime(2026, 11, 13, tzinfo=TZ)


def test_search_window_without_date_is_30_days_back_365_forward() -> None:
    now = datetime(2026, 10, 25, 19, 0, tzinfo=ZoneInfo("UTC"))
    lo, hi = search_window(None, now, TZ)
    local = now.astimezone(TZ)
    assert lo == local - timedelta(days=30)
    assert hi == local + timedelta(days=365)
    assert search_window("", now, TZ) == (lo, hi)


def test_search_window_bad_date_is_fix_input() -> None:
    now = datetime(2026, 10, 25, 12, 0, tzinfo=ZoneInfo("UTC"))
    with pytest.raises(FixInput) as exc:
        search_window("Nov 14", now, TZ)
    assert exc.value.error == "date 'Nov 14' is not YYYY-MM-DD"
    assert exc.value.fix == 'send the day as "2026-10-24"'


# ---------- duplicate guard ----------


def test_duplicate_of_is_case_insensitive_and_skips_cancelled() -> None:
    cancelled = event("Zed Fox", status="cancelled", id="old")
    live = event("  ZED FOX ", id="live")
    assert duplicate_of([cancelled, live], "zed fox") is live
    assert duplicate_of([cancelled], "Zed Fox") is None
    assert duplicate_of([event("Zed Fox & Friends")], "Zed Fox") is None
    assert duplicate_of([event(None)], "Zed Fox") is None
    assert duplicate_of([], "Zed Fox") is None


# ---------- description patching ----------


def test_missing_link_lines_and_patched_description() -> None:
    desc = f"Doors: 7:30 PM\nTeaser playlist: {TEASER_URL}"
    setlist_line = "Setlist: https://www.setlist.fm/setlist/x"
    missing = missing_link_lines(desc, [f"Teaser playlist: {TEASER_URL}", None, setlist_line, ""])
    assert missing == [setlist_line]
    assert patched_description(desc + "\n", missing) == f"{desc}\n{setlist_line}"
    assert patched_description(None, [setlist_line]) == setlist_line
    assert patched_description("", []) == ""
    assert missing_link_lines(None, [setlist_line]) == [setlist_line]


def test_teaser_url_in() -> None:
    assert teaser_url_in(f"Doors: 7:30 PM\nTeaser playlist: {TEASER_URL}\nSetlist: x") == TEASER_URL
    assert teaser_url_in("Doors: 7:30 PM") is None
    assert teaser_url_in("") is None
    assert teaser_url_in("Teaser playlist: ") is None


# ---------- pick ----------


@pytest.mark.parametrize(
    ("raw", "want"),
    [("2.", 2), ("2", 2), (" 3 ", 3), ("x", None), ("", None), ("2a", None), ("1.5", None)],
)
def test_pick_index(raw: str, want: int | None) -> None:
    assert pick_index(raw) == want
