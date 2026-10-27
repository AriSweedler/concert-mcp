"""flows/concert_add.py end to end on the fakes, with the legacy cal-add.py replies as goldens."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from concert_mcp.flows.concert_add import (
    KNOWN,
    AddRequest,
    add_concert,
    add_event,
    add_plain,
    prepare_event,
    resolve_calendar,
)
from concert_mcp.flows.queue import queue_list
from concert_mcp.flows.services import Services
from concert_mcp.result import NEED_USER, FixInput, Kind
from concert_mcp.text.venues import VenueHit
from fakes import (
    FakeCalendar,
    FakeChat,
    FakeClock,
    FakeContext,
    FakeGemini,
    FakeSetlistFm,
    FakeSpotify,
    FakeVenueLookup,
    MemoryState,
    ms,
)

LA = ZoneInfo("America/Los_Angeles")
ARTIST = "Zed Fox with special guest Zork."
START = "2026-11-14 20:30"
VENUE = "The Quarry, San Francisco"
TEASER_URL = "https://open.spotify.com/playlist/pl1"
SETLIST_URL = "https://www.setlist.fm/setlist/zed-fox/2026/the-quarry-san-francisco-ca-7b9c1a2e.html"
GWS_REFUSED = "Google Calendar refused the request. Admin: check `gws auth` on the host."
NOT_SET_UP = "• **Teaser:** not made (Spotify is not set up: run `concert-mcp spotify auth`)"

ZED_FOX = {
    "name": "Zed Fox",
    "id": "artist-zedfox",
    "external_urls": {"spotify": "https://open.spotify.com/artist/artist-zedfox"},
    "followers": {"total": 500000},
}
LIKED = [
    {
        "name": n,
        "artists": ["Zed Fox"],
        "artist_ids": ["artist-zedfox"],
        "album": "A",
        "uri": f"spotify:track:t{i}",
        "url": f"https://open.spotify.com/track/t{i}",
        "duration_ms": 1,
        "added_at": f"202{i}-01-01T00:00:00Z",
    }
    for i, n in enumerate(("First Light", "Second Wind", "Third Rail"), 1)
]
SETLIST_ROW = {
    "id": "7b9c1a2e",
    "url": SETLIST_URL,
    "eventDate": "14-11-2026",
    "artist": {"name": "Zed Fox"},
    "venue": {"name": "The Quarry", "city": {"name": "San Francisco"}},
    "tour": {"name": "Fall 2026"},
    "sets": {"set": [{"song": [{"name": "First Light"}, {"name": "Second Wind"}]}]},
}


def eventedit(event_id: str, calendar_id: str = "cal-concerts") -> str:
    eid = base64.urlsafe_b64encode(f"{event_id} {calendar_id}".encode()).decode().rstrip("=")
    return f"https://calendar.google.com/calendar/u/0/r/eventedit/{eid}"


def golden_added(event_id: str = "evt1", *, notes: str = "", when: str = "Sat Nov 14, 8:30 PM – 10:30 PM PST") -> str:
    return (
        "**Added to Concerts**\n"
        "• **Artist:** Zed Fox\n"
        f"• **When:** {when} (end assumed)\n"
        "• **Where:** The Quarry, San Francisco\n"
        "• **Doors:** 7:30 PM\n"
        "• **Purchased:** 2 tickets, $42.00 on Ticketweb\n"
        "• **Opener:** Zork.\n"
        f"• **Teaser:** [2026-11-14 | Zed Fox]({TEASER_URL}) 3 liked songs. Move it to Concerts › Teasers in Spotify.\n"
        "• **Setlist:** not on setlist.fm yet; checking weekly for 2 months"
        f"{notes}\n"
        f"[Open in Google Calendar]({eventedit(event_id)})"
    )


def services(
    settings,
    now: datetime,
    *,
    route: dict[str, Any] | None,
    text: str,
    calendar: FakeCalendar | None = None,
    spotify: FakeSpotify | None | str = "default",
    setlistfm: FakeSetlistFm | None = None,
    chat: FakeChat | None = None,
    places: Any = None,
    gemini: Any = None,
) -> Services:
    clock = FakeClock(now)
    return Services(
        settings=settings,
        places=places,
        gemini=gemini,
        calendar=calendar
        or FakeCalendar(
            calendars=[{"id": "cal-concerts", "summary": "Concerts"}, {"id": "primary", "summary": "Personal"}]
        ),
        spotify=FakeSpotify(artists=[ZED_FOX], liked=LIKED) if spotify == "default" else spotify,
        setlistfm=setlistfm if setlistfm is not None else FakeSetlistFm({}),
        chat=chat or FakeChat(),
        context=FakeContext(route=route, session_id="s1" if route is not None else None, text=text),
        state=MemoryState(clock=clock),
        clock=clock,
    )


def concert(svc: Services, **kw: Any):
    args: dict[str, Any] = {"artist": ARTIST, "start": START, "venue": VENUE, "source_text": ""}
    args.update(kw)
    return add_concert(svc=svc, **args)


class TestHappyPath:
    def test_inserts_the_event_as_the_legacy_did(self, svc, ticket_text):
        out = concert(svc, source_text=ticket_text)
        assert out.kind is Kind.OK, out
        [ins] = svc.calendar.inserted
        assert ins["calendar_id"] == "cal-concerts"
        assert ins["summary"] == "Zed Fox"
        assert ins["location"] == VENUE
        assert ins["start"] == {"dateTime": datetime(2026, 11, 14, 20, 30, tzinfo=LA).isoformat()}
        assert ins["end"] == {"dateTime": datetime(2026, 11, 14, 22, 30, tzinfo=LA).isoformat()}
        assert ins["description"] == (
            "Doors: 7:30 PM\nPurchased: 2 tickets, $42.00 on Ticketweb\nHeadliner: Zed Fox\nOpener: Zork.\nTeaser playlist: "
            + TEASER_URL
        )
        assert ins["attendees"] == []

    def test_reply_is_the_golden_byte_for_byte(self, svc, ticket_text):
        out = concert(svc, source_text=ticket_text)
        assert out.reply == golden_added()

    def test_data_carries_the_legacy_record(self, svc, ticket_text):
        out = concert(svc, source_text=ticket_text)
        d = out.data
        assert d["calendar"] == "Concerts"
        assert d["summary"] == "Zed Fox"
        assert d["start"] == datetime(2026, 11, 14, 20, 30, tzinfo=LA).isoformat()
        assert d["end"] == datetime(2026, 11, 14, 22, 30, tzinfo=LA).isoformat()
        assert d["location"] == VENUE
        assert d["description"] == [
            "Doors: 7:30 PM",
            "Purchased: 2 tickets, $42.00 on Ticketweb",
            "Headliner: Zed Fox",
            "Opener: Zork.",
            f"Teaser playlist: {TEASER_URL}",
        ]
        assert d["dropped"] == []
        assert d["topic"] == "2026-11-14 | Zed Fox | The Quarry"
        assert d["id"] == "evt1"
        assert d["htmlLink"] == svc.calendar.inserted[0]["htmlLink"]
        assert d["teaser"]["name"] == "2026-11-14 | Zed Fox"
        assert d["setlist"]["found"] is False
        assert out.to_json()["ok"] is True

    def test_queues_the_show_for_two_months(self, svc, ticket_text):
        concert(svc, source_text=ticket_text)
        [q] = queue_list(svc)
        assert q.event_id == "evt1"
        assert q.calendar_id == "cal-concerts"
        assert q.artist == "Zed Fox"
        assert q.date == "2026-11-14"
        assert q.venue == VENUE
        assert q.until == "2027-01-14"
        assert q.setlistfm_url is None and q.song_count == 0
        assert q.added == svc.clock.now().isoformat(timespec="seconds")

    def test_renames_the_forum_topic(self, svc, ticket_text, forum_route):
        # The chat id is the bare group id: the "telegram:" prefix and ":topic:n" suffix of the route's target stripped.
        chat = forum_route["target"]["to"].removeprefix("telegram:").split(":topic:")[0]
        out = concert(svc, source_text=ticket_text)
        assert svc.chat.renamed == [(chat, "7", "2026-11-14 | Zed Fox | The Quarry")]
        assert out.data["topic_status"] == "topic renamed to 2026-11-14 | Zed Fox | The Quarry"

    def test_context_text_alone_is_enough_for_the_derived_lines(self, svc):
        # svc's FakeContext carries the ticket text as the recent OCR / chat text; source_text is empty.
        out = concert(svc)
        assert out.reply == golden_added()

    def test_the_teaser_is_made_before_the_insert(self, svc, ticket_text):
        concert(svc, source_text=ticket_text)
        assert svc.spotify.created[0]["name"] == "2026-11-14 | Zed Fox"
        assert svc.spotify.added == [("pl1", ["spotify:track:t1", "spotify:track:t2", "spotify:track:t3"])]

    def test_duplicate_guard_looks_in_a_one_minute_window(self, svc, ticket_text):
        concert(svc, source_text=ticket_text)
        _, args = next(c for c in svc.calendar.calls if c[0] == "list_events")
        start = datetime(2026, 11, 14, 20, 30, tzinfo=LA)
        assert args["calendar_id"] == "cal-concerts"
        assert args["time_min"] == start
        assert args["time_max"] == start + timedelta(minutes=1)
        assert args["q"] == "Zed Fox"


class TestDuplicate:
    def test_existing_event_is_patched_not_reinserted(self, svc, ticket_text):
        e = svc.calendar.seed_event(
            "cal-concerts",
            summary="Zed Fox",
            start=datetime(2026, 11, 14, 20, 30, tzinfo=LA),
            location=VENUE,
            description="Doors: 7:30 PM",
        )
        out = concert(svc, source_text=ticket_text)
        assert out.kind is Kind.DUPLICATE
        assert svc.calendar.inserted == []
        assert svc.calendar.patched == [
            ("cal-concerts", e["id"], {"description": f"Doors: 7:30 PM\nTeaser playlist: {TEASER_URL}"})
        ]
        assert out.reply.startswith("**Already on Concerts** (nothing new created)\n• **Artist:** Zed Fox\n")
        assert out.reply.endswith(f"[Open in Google Calendar]({eventedit(e['id'])})")
        assert out.data["id"] == e["id"]
        assert out.to_json()["duplicate"] is True
        assert [q.event_id for q in queue_list(svc)] == [e["id"]]

    def test_nothing_is_patched_when_the_links_are_already_there(self, svc, ticket_text):
        svc.calendar.seed_event(
            "cal-concerts",
            summary="zed fox",
            start=datetime(2026, 11, 14, 20, 30, tzinfo=LA),
            description=f"Teaser playlist: {TEASER_URL}",
        )
        out = concert(svc, source_text=ticket_text)
        assert out.kind is Kind.DUPLICATE
        assert svc.calendar.patched == []

    def test_a_different_title_at_the_same_minute_is_not_a_duplicate(self, svc, ticket_text):
        svc.calendar.seed_event(
            "cal-concerts", summary="Zed Fox tribute night", start=datetime(2026, 11, 14, 20, 30, tzinfo=LA)
        )
        out = concert(svc, source_text=ticket_text)
        assert out.kind is Kind.OK
        assert len(svc.calendar.inserted) == 1


class TestDryRun:
    def test_writes_nothing_and_says_would(self, svc, ticket_text):
        out = concert(svc, source_text=ticket_text, dry_run=True)
        assert out.kind is Kind.DRY_RUN
        assert svc.calendar.inserted == [] and svc.calendar.patched == []
        assert svc.calendar.calls == []
        assert queue_list(svc) == []
        assert svc.chat.renamed == []
        assert svc.spotify.created == []
        assert out.reply.startswith("**Would add to Concerts**\n• **Artist:** Zed Fox\n")
        assert "• **Teaser:** would make 2026-11-14 | Zed Fox (3 liked songs)" in out.reply
        assert "[Open in Google Calendar]" not in out.reply
        assert out.to_json()["dryRun"] is True
        assert "id" not in out.data and "topic_status" not in out.data


class TestShowTime:
    def test_start_at_doors_is_corrected_to_the_certain_show_time(self, svc, ticket_text):
        out = concert(svc, start="2026-11-14 19:30", source_text=ticket_text)
        assert out.kind is Kind.OK
        [ins] = svc.calendar.inserted
        assert ins["start"]["dateTime"] == datetime(2026, 11, 14, 20, 30, tzinfo=LA).isoformat()
        assert ins["end"]["dateTime"] == datetime(2026, 11, 14, 22, 30, tzinfo=LA).isoformat()
        assert out.reply == golden_added(notes="\nStart corrected to 8:30 PM, the show time in the text.")

    def test_inferred_show_time_refuses_a_start_at_doors(self, settings, now):
        text = "ZED FOX\nThe Quarry\nDoors 7:30 PM\n8:30 PM\n"
        svc = services(settings, now, route=None, text=text)
        out = concert(svc, start="2026-11-14 19:30")
        assert out.kind is Kind.FIX
        assert out.error == "start 7:30 PM is not the show time"
        assert out.fix == 'set start to "2026-11-14 8:30 PM" (the show time in the text; 7:30 PM is doors)'
        assert svc.calendar.inserted == []

    def test_date_only_start_takes_the_show_time_from_the_text(self, svc, ticket_text):
        out = concert(svc, start="2026-11-14", source_text=ticket_text)
        assert out.kind is Kind.OK
        assert svc.calendar.inserted[0]["start"]["dateTime"] == datetime(2026, 11, 14, 20, 30, tzinfo=LA).isoformat()
        assert out.reply == golden_added(notes="\nStart taken from the text (8:30 PM).")

    def test_date_only_start_with_no_text_asks_for_the_show_time(self, settings, now):
        svc = services(settings, now, route=None, text="")
        out = concert(svc, start="2026-11-14")
        assert out.kind is Kind.NEED_USER
        assert out.reply == NEED_USER.format(what="show time")
        assert out.reply.startswith("I could not read the show time from that.")
        assert out.error == "show time is required"

    def test_date_only_is_not_read_from_the_text_for_a_plain_event(self, settings, now):
        # Only a concert takes its show time from the ticket text; a plain event's bare date is just a date.
        svc = services(settings, now, route=None, text="")
        out = add_plain(title="Dinner", start="2026-11-14", location="ZZZ Bistro", svc=svc)
        assert out.kind is Kind.OK
        assert svc.calendar.inserted[0]["start"]["dateTime"] == datetime(2026, 11, 14, 0, 0, tzinfo=LA).isoformat()
        assert "Start taken from the text" not in out.reply


class TestWindow:
    def test_past_start_asks_for_the_right_date(self, svc, ticket_text):
        out = concert(svc, start="2024-01-01 20:00", source_text=ticket_text)
        assert out.kind is Kind.NEED_USER
        assert out.reply == (
            "That reads as Mon Jan 1 2024, 8:00 PM, which is in the past. "
            "Reply with the right date (include the year) and I will add it."
        )
        assert out.error.startswith("start 2024-01-01T20:00:00-08:00 is in the past")
        assert svc.calendar.inserted == []

    def test_allow_past_lets_it_through(self, svc):
        req = AddRequest(title="Old Show", start="2024-01-01 20:00", location="Somewhere", allow_past=True)
        out = add_event(req, svc)
        assert out.kind is Kind.OK

    def test_far_future_start_asks_for_the_right_date(self, svc, ticket_text):
        out = concert(svc, start="2029-11-14 20:30", source_text=ticket_text)
        assert out.kind is Kind.NEED_USER
        assert (
            out.reply
            == "That reads as Wed Nov 14 2029, over two years from now. Reply with the right date and I will add it."
        )
        assert out.error.endswith("is over two years out")


class TestRequiredFields:
    def test_empty_artist_is_needed(self, svc):
        out = concert(svc, artist="  ")
        assert out.kind is Kind.NEED_USER
        assert out.error == "artist is required"
        assert out.reply == NEED_USER.format(what="artist")

    def test_concert_prefix_alone_is_an_empty_artist(self, svc):
        out = concert(svc, artist="[Concert]")
        assert out.error == "artist is required"

    def test_plain_empty_title_is_needed(self, svc):
        out = add_plain(title="", start=START, location="ZZZ Bistro", svc=svc)
        assert out.kind is Kind.NEED_USER
        assert out.error == "title is required"
        assert out.reply == NEED_USER.format(what="title")

    def test_empty_start_is_needed(self, svc):
        out = concert(svc, start="")
        assert out.error == "date and time is required"
        assert out.reply == NEED_USER.format(what="date and time")

    def test_empty_venue_is_needed(self, svc, ticket_text):
        out = concert(svc, venue="", source_text=ticket_text)
        assert out.kind is Kind.NEED_USER
        assert out.error == "venue is required"
        assert out.reply == NEED_USER.format(what="venue")
        assert svc.calendar.inserted == []

    def test_a_ticket_platform_as_the_artist_is_needed(self, svc, ticket_text):
        out = concert(svc, artist="DICE", source_text=ticket_text)
        assert out.kind is Kind.NEED_USER
        assert out.error == "artist 'DICE' is a ticket platform, not the headliner"
        assert (
            out.reply
            == "DICE is a ticket platform, not the artist. Reply with the headliner's name and I will add the event."
        )
        assert svc.calendar.inserted == []

    def test_the_venue_as_the_artist_is_needed(self, svc, ticket_text):
        out = concert(svc, artist="The Quarry", venue="The Quarry, San Francisco", source_text=ticket_text)
        assert out.kind is Kind.NEED_USER
        assert out.error == "artist 'The Quarry' is the venue, not the headliner"
        assert svc.calendar.inserted == []

    def test_an_opener_not_in_the_text_is_dropped(self, svc, ticket_text):
        out = concert(svc, artist="Zed Fox with The Back Alley", source_text=ticket_text)
        assert out.kind is Kind.OK, out
        assert out.data["dropped"] == ["Opener: The Back Alley"]
        # Zork. is on the ticket's own "with special guest" line, so the text supplies the opener instead.
        assert "Opener: Zork." in svc.calendar.inserted[0]["description"]
        assert "Back Alley" not in svc.calendar.inserted[0]["description"]

    def test_garbage_start_is_a_fix(self, svc):
        out = concert(svc, start="tomorrow night")
        assert out.kind is Kind.FIX
        assert out.error == "start 'tomorrow night' is not a date"
        assert out.fix == 'start must look like "2026-11-03 20:00" (24h) or "2026-11-03 8:00 PM"'


class TestNotes:
    def test_a_claim_not_in_the_text_is_dropped_with_a_note(self, svc, ticket_text):
        out = concert(svc, source_text=ticket_text, notes="Price: $999")
        assert out.kind is Kind.OK
        assert "Price" not in svc.calendar.inserted[0]["description"]
        assert out.data["dropped"] == ["Price: $999"]
        assert out.reply == golden_added(notes="\nLeft out (not in the text I was given): Price: $999")

    def test_a_caller_doors_line_loses_to_the_derived_one(self, svc, ticket_text):
        out = concert(svc, source_text=ticket_text, notes="Doors: 9 PM")
        assert out.kind is Kind.OK
        desc = svc.calendar.inserted[0]["description"]
        assert "Doors: 7:30 PM" in desc and "9 PM" not in desc
        assert out.data["dropped"] == []
        assert out.reply == golden_added()

    def test_a_verified_note_is_kept_as_a_bullet(self, svc, ticket_text):
        out = concert(svc, source_text=ticket_text, notes="Seat: GA\n\nOrder: https://www.ticketweb.com/order/abc123")
        assert out.kind is Kind.OK
        desc = svc.calendar.inserted[0]["description"].split("\n")
        assert "Seat: GA" in desc and "Order: https://www.ticketweb.com/order/abc123" in desc
        assert "• **Seat:** GA" in out.reply
        assert out.data["dropped"] == []

    def test_with_no_text_at_all_every_claim_line_is_dropped(self, settings, now):
        svc = services(settings, now, route=None, text="")
        out = concert(svc, notes="Doors: 7:30 PM\nSeat: GA")
        assert out.kind is Kind.OK
        assert out.data["dropped"] == ["Opener: Zork.", "Doors: 7:30 PM"]
        # With no text nothing verifies: the title's opener and the clock-time note go; the claim-free note survives.
        assert out.data["description"][:1] == ["Seat: GA"]


class TestTeaserAndSetlist:
    def test_no_spotify_means_a_not_set_up_bullet_and_the_event_still_lands(
        self, settings, now, forum_route, ticket_text
    ):
        svc = services(settings, now, route=forum_route, text=ticket_text, spotify=None)
        out = concert(svc)
        assert out.kind is Kind.OK
        assert len(svc.calendar.inserted) == 1
        assert NOT_SET_UP in out.reply
        assert "Teaser playlist:" not in (svc.calendar.inserted[0]["description"] or "")
        assert out.data["teaser"]["ok"] is False

    def test_spotify_failure_is_a_bullet_and_the_event_still_lands(self, settings, now, forum_route, ticket_text):
        spotify = FakeSpotify(artists=[ZED_FOX], liked=LIKED, fail="search_artists")
        svc = services(settings, now, route=forum_route, text=ticket_text, spotify=spotify)
        out = concert(svc)
        assert out.kind is Kind.OK
        assert len(svc.calendar.inserted) == 1
        assert "• **Teaser:** not made (" in out.reply
        assert NOT_SET_UP not in out.reply

    def test_setlist_found_adds_the_link_line_and_the_queue_count(self, settings, now, forum_route, ticket_text):
        setlistfm = FakeSetlistFm({("Zed Fox", "14-11-2026", None): {"setlist": [SETLIST_ROW]}})
        svc = services(settings, now, route=forum_route, text=ticket_text, setlistfm=setlistfm)
        out = concert(svc)
        assert out.kind is Kind.OK
        assert f"• **Setlist:** [setlist.fm]({SETLIST_URL}) 2 songs so far; checking weekly for 2 months" in out.reply
        desc = svc.calendar.inserted[0]["description"].split("\n")
        assert desc[-2:] == [f"Teaser playlist: {TEASER_URL}", f"Setlist: {SETLIST_URL}"]
        [q] = queue_list(svc)
        assert q.setlistfm_url == SETLIST_URL and q.song_count == 2
        assert out.data["setlist"]["found"] is True

    def test_setlist_lookup_skipped_when_setlistfm_is_not_set_up(self, settings, now, forum_route, ticket_text):
        svc = services(settings, now, route=forum_route, text=ticket_text)
        svc.setlistfm = None
        out = concert(svc)
        assert out.kind is Kind.OK
        assert "• **Setlist:** lookup skipped (setlist.fm is not set up" in out.reply
        assert len(queue_list(svc)) == 1

    def test_link_lines_never_appear_as_bullets(self, svc, ticket_text):
        out = concert(svc, source_text=ticket_text)
        assert "Teaser playlist:" not in out.reply
        assert "**Setlist:** not on setlist.fm yet" in out.reply


class TestCalendarFailures:
    def test_refused_insert_is_an_error_and_nothing_is_queued(self, settings, now, forum_route, ticket_text):
        cal = FakeCalendar(
            calendars=[{"id": "cal-concerts", "summary": "Concerts"}, {"id": "primary", "summary": "Personal"}],
            fail="insert_event",
        )
        svc = services(settings, now, route=forum_route, text=ticket_text, calendar=cal)
        out = concert(svc)
        assert out.kind is Kind.ERROR
        assert out.reply == GWS_REFUSED
        assert out.data["provider"] == "gws"
        assert queue_list(svc) == []
        assert svc.chat.renamed == []
        assert out.to_json()["ok"] is False

    def test_refused_calendar_list_is_an_error(self, settings, now, forum_route, ticket_text):
        cal = FakeCalendar(calendars=[{"id": "cal-concerts", "summary": "Concerts"}], fail="list_calendars")
        svc = services(settings, now, route=forum_route, text=ticket_text, calendar=cal)
        out = concert(svc)
        assert out.kind is Kind.ERROR
        assert out.reply == GWS_REFUSED
        assert svc.calendar.inserted == []

    def test_a_concerts_calendar_that_does_not_exist_is_a_fix(self, settings, now, forum_route, ticket_text):
        cal = FakeCalendar(calendars=[{"id": "primary", "summary": "Personal"}])
        svc = services(settings, now, route=forum_route, text=ticket_text, calendar=cal)
        out = concert(svc)
        assert out.kind is Kind.FIX
        assert out.error == "calendar 'Concerts' matched 0 calendars"
        assert out.fix == "use one of: Personal"

    def test_a_crash_inside_becomes_the_crash_reply(self, svc, ticket_text):
        def boom(*args, **kwargs):
            raise RuntimeError("kaboom")

        svc.calendar.insert_event = boom
        out = concert(svc, source_text=ticket_text)
        assert out.kind is Kind.ERROR
        assert out.reply == "Something broke inside the concert tool (RuntimeError). Admin: check the gateway log."
        assert out.error.startswith("RuntimeError('kaboom')")
        assert out.fix is None


class TestPlainEvents:
    def test_plain_event_lands_on_the_personal_calendar_untouched(self, settings, now, forum_route):
        # A live forum topic, but no ticket text in the context: a plain event derives nothing and renames nothing.
        svc = services(settings, now, route=forum_route, text="")
        out = add_plain(
            title="[Dinner] ZZZ Bistro", start="2026-11-20 19:00", location="ZZZ Bistro, San Francisco", svc=svc
        )
        assert out.kind is Kind.OK
        [ins] = svc.calendar.inserted
        assert ins["calendar_id"] == "primary"
        assert ins["summary"] == "[Dinner] ZZZ Bistro"
        assert ins["description"] == ""
        assert out.data["calendar"] == "Personal"
        assert out.reply.startswith("**Added to Personal**\n• **Artist:** [Dinner] ZZZ Bistro\n")
        assert out.reply.endswith(f"[Open in Google Calendar]({eventedit('evt1', 'primary')})")
        assert "Teaser" not in out.reply and "Setlist" not in out.reply
        assert svc.spotify.calls == [] and svc.setlistfm.calls == []
        assert queue_list(svc) == []
        assert svc.chat.renamed == []
        assert out.data["topic_status"] == "topic not renamed: no live chat"
        assert out.data["teaser"] is None and out.data["setlist"] is None
        # No calendar lookup for the default calendar: "primary" is an id.
        assert [n for n, _ in svc.calendar.calls] == ["list_events", "insert_event"]

    def test_plain_event_keeps_a_concert_prefix(self, svc):
        out = add_plain(title="[Concert] Someone", start="2026-11-20 19:00", location="X", svc=svc)
        assert out.data["summary"] == "[Concert] Someone"

    def test_plain_event_ignores_the_ticket_text_in_the_context(self, svc):
        # The svc fixture's context carries a ticket (Doors 7:30 PM, Show 8:30 PM, 2 tickets $42.00). A dinner added
        # minutes later must not inherit its lines, and its 7:00 PM start is not "the doors time".
        out = add_plain(title="Dinner", start="2026-11-20 19:00", location="ZZZ Bistro", svc=svc)
        assert out.kind is Kind.OK
        assert out.data["description"] == []
        assert svc.calendar.inserted[0]["start"]["dateTime"] == datetime(2026, 11, 20, 19, 0, tzinfo=LA).isoformat()
        assert "Start corrected" not in out.reply

    def test_plain_event_at_the_doors_time_is_not_a_fix(self, svc):
        out = add_plain(title="Dinner", start="2026-11-20 19:30", location="ZZZ Bistro", svc=svc)
        assert out.kind is Kind.OK
        assert svc.calendar.inserted[0]["start"]["dateTime"] == datetime(2026, 11, 20, 19, 30, tzinfo=LA).isoformat()

    def test_plain_title_with_a_with_clause_is_kept_whole(self, svc):
        out = add_plain(title="[Dinner] Team dinner with Sam", start="2026-11-20 19:00", location="ZZZ Bistro", svc=svc)
        assert out.data["summary"] == "[Dinner] Team dinner with Sam"
        assert out.data["description"] == []

    def test_plain_caller_lines_are_still_verified_against_the_text(self, svc):
        # The hallucination filter applies to every event: a clock time not in the context text is dropped.
        out = add_plain(
            title="Dinner",
            start="2026-11-20 19:00",
            location="ZZZ Bistro",
            notes="Doors: 7:30 PM\nMeet: 6:15 PM",
            svc=svc,
        )
        assert out.data["description"] == ["Doors: 7:30 PM"]
        assert out.data["dropped"] == ["Meet: 6:15 PM"]

    def test_named_calendar_is_resolved_by_name(self, svc):
        out = add_plain(title="Thing", start="2026-11-20 19:00", location="X", calendar="concerts", svc=svc)
        assert out.kind is Kind.OK
        assert svc.calendar.inserted[0]["calendar_id"] == "cal-concerts"
        assert out.data["calendar"] == "Concerts"

    def test_plain_notes_are_lines_in_the_description(self, settings, now):
        svc = services(settings, now, route=None, text="")
        out = add_plain(
            title="Dinner", start="2026-11-20 19:00", location="ZZZ Bistro", notes="Table: 4\n\nWith: Sam", svc=svc
        )
        assert svc.calendar.inserted[0]["description"] == "Table: 4\nWith: Sam"
        assert "• **Table:** 4" in out.reply


class TestTopicRename:
    def test_send_disabled_means_no_rename(self, settings_nosend, now, forum_route, ticket_text):
        svc = services(settings_nosend, now, route=forum_route, text=ticket_text)
        out = concert(svc)
        assert out.kind is Kind.OK
        assert svc.chat.renamed == []
        assert out.data["topic_status"] == "topic not renamed: no live chat"

    def test_no_live_chat_means_no_rename(self, svc_cli):
        out = concert(svc_cli)
        assert out.kind is Kind.OK
        assert svc_cli.chat.renamed == []
        assert out.data["topic_status"] == "topic not renamed: no live chat"

    def test_a_plain_telegram_chat_is_not_a_forum_topic(self, settings, now, ticket_text):
        route = {"channel": "telegram", "target": {"to": "telegram:12345"}, "thread": None, "accountId": None}
        svc = services(settings, now, route=route, text=ticket_text)
        out = concert(svc)
        assert out.kind is Kind.OK
        assert svc.chat.renamed == []
        assert out.data["topic_status"] == "topic not renamed: not a Telegram forum topic"

    def test_the_general_topic_is_not_renamed(self, settings, now, ticket_text, forum_route):
        route = {**forum_route, "thread": {"id": 1}}
        svc = services(settings, now, route=route, text=ticket_text)
        out = concert(svc)
        assert out.data["topic_status"] == "topic not renamed: not a Telegram forum topic"

    def test_chat_status_is_passed_through(self, settings, now, forum_route, ticket_text):
        chat = FakeChat(rename_status="topic not renamed: HTTP 400 Bad Request: not enough rights")
        svc = services(settings, now, route=forum_route, text=ticket_text, chat=chat)
        out = concert(svc)
        assert out.kind is Kind.OK
        assert out.data["topic_status"] == "topic not renamed: HTTP 400 Bad Request: not enough rights"


class TestFromMapping:
    def test_aliases_fold(self):
        req = AddRequest.from_mapping(
            {"artist": "Zed Fox", "venue": VENUE, "datetime": START, "notes": ["Seat: GA"], "text": "the ticket"},
            concert=True,
        )
        assert req.title == "Zed Fox"
        assert req.location == VENUE
        assert req.start == START
        assert req.description == ("Seat: GA",)
        assert req.source == "the ticket"
        assert req.concert is True and req.teaser is True and req.dry_run is False

    def test_summary_and_timezone_aliases(self):
        req = AddRequest.from_mapping({"summary": "X", "timezone": "Europe/Paris"})
        assert req.title == "X" and req.tz == "Europe/Paris"

    def test_date_and_time_make_a_start(self):
        req = AddRequest.from_mapping({"title": "X", "date": "2026-11-14", "time": "8:30 PM"})
        assert req.start == "2026-11-14 8:30 PM"

    def test_an_explicit_start_wins_over_date_and_time(self):
        req = AddRequest.from_mapping({"title": "X", "date": "2026-11-14", "time": "8:30 PM", "start": START})
        assert req.start == START

    def test_unknown_key_is_a_fix_naming_the_allowed_keys(self):
        with pytest.raises(FixInput) as exc:
            AddRequest.from_mapping({"title": "X", "foo": 1})
        assert exc.value.error == "unknown keys: foo"
        assert exc.value.fix == (
            "allowed keys: allowPast, another, attendees, calendar, description, end, location, source, start, title, tz"
        )
        assert exc.value.fix == f"allowed keys: {', '.join(sorted(KNOWN - {'date', 'time'}))}"

    def test_description_string_splits_on_a_literal_backslash_n(self):
        req = AddRequest.from_mapping({"title": "X", "description": "Doors: 7:00 PM\\nSeat: GA"})
        assert req.description == ("Doors: 7:00 PM", "Seat: GA")

    def test_description_that_is_not_a_list_is_a_fix(self):
        with pytest.raises(FixInput) as exc:
            AddRequest.from_mapping({"title": "X", "description": 42})
        assert exc.value.error == "description must be a list of lines"

    def test_attendees_without_an_at_sign_are_a_fix(self):
        with pytest.raises(FixInput) as exc:
            AddRequest.from_mapping({"title": "X", "attendees": ["sam"]})
        assert exc.value.error == "attendees must be a list of email addresses"
        assert exc.value.fix == 'use ["name@example.com"]'

    def test_attendees_are_kept_and_passed_to_the_insert(self, svc):
        req = AddRequest.from_mapping(
            {"title": "Dinner", "start": "2026-11-20 19:00", "location": "ZZZ Bistro", "attendees": ["sam@example.com"]}
        )
        assert req.attendees == ("sam@example.com",)
        out = add_event(req, svc)
        assert out.kind is Kind.OK
        assert svc.calendar.inserted[0]["attendees"] == [{"email": "sam@example.com"}]

    def test_allow_past_and_optional_strings(self):
        req = AddRequest.from_mapping({"title": "X", "allowPast": "yes", "end": "", "calendar": None, "tz": ""})
        assert req.allow_past is True
        assert req.end is None and req.calendar is None and req.tz is None

    def test_not_an_object_is_a_fix(self):
        with pytest.raises(FixInput) as exc:
            AddRequest.from_mapping(["Zed Fox"])  # type: ignore[arg-type]
        assert exc.value.error == "input must be a JSON object"
        assert exc.value.fix == "send one {...} object"

    def test_flags_are_carried(self):
        req = AddRequest.from_mapping({"title": "X"}, concert=True, teaser=False, dry_run=True)
        assert (req.concert, req.teaser, req.dry_run) == (True, False, True)


class TestValidation:
    def test_unknown_tz_is_a_fix(self, svc):
        out = add_event(AddRequest(title="X", start=START, location="Y", tz="Mars/Olympus"), svc)
        assert out.kind is Kind.FIX
        assert out.error == "unknown tz 'Mars/Olympus'"
        assert out.fix == "use an IANA name like America/Los_Angeles, or omit it"

    def test_end_before_start_is_a_fix(self, svc):
        out = add_event(AddRequest(title="X", start=START, location="Y", end="2026-11-14 19:00"), svc)
        assert out.kind is Kind.FIX
        assert out.error == "end must be after start and within 12 hours"
        assert out.fix == "fix end, or omit it for start + 2 hours"

    def test_an_explicit_end_is_not_assumed(self, svc, ticket_text):
        req = AddRequest(
            title="Zed Fox", start=START, location=VENUE, end="2026-11-14 23:00", source=ticket_text, concert=True
        )
        out = add_event(req, svc)
        assert out.kind is Kind.OK
        assert "(end assumed)" not in out.reply
        assert "• **When:** Sat Nov 14, 8:30 PM – 11:00 PM PST" in out.reply

    def test_teaser_off_skips_spotify(self, svc, ticket_text):
        req = AddRequest(title="Zed Fox", start=START, location=VENUE, source=ticket_text, concert=True, teaser=False)
        out = add_event(req, svc)
        assert out.kind is Kind.OK
        assert svc.spotify.calls == []
        assert "Teaser" not in out.reply
        assert out.data["teaser"] is None
        assert len(queue_list(svc)) == 1


class TestPrepareEvent:
    def test_is_pure_and_in_order(self, settings, now, ticket_text):
        req = AddRequest(title=ARTIST, start=START, location=VENUE, source=ticket_text, concert=True)
        p = prepare_event(req, context_text="", now=now, settings=settings)
        assert p.title == "Zed Fox"
        assert p.openers == ("Zork.",)
        assert p.start == datetime(2026, 11, 14, 20, 30, tzinfo=LA)
        assert p.end == datetime(2026, 11, 14, 22, 30, tzinfo=LA)
        assert p.end_assumed is True
        assert p.tz == "America/Los_Angeles"
        assert p.lines == (
            "Doors: 7:30 PM",
            "Purchased: 2 tickets, $42.00 on Ticketweb",
            "Headliner: Zed Fox",
            "Opener: Zork.",
        )
        assert p.dropped == () and p.notes == ()
        assert p.topic == "2026-11-14 | Zed Fox | The Quarry"
        assert p.raw_source == ticket_text
        assert p.source_norm is not None

    def test_openers_come_from_the_text_when_the_title_has_none(self, settings, now, ticket_text):
        req = AddRequest(title="Zed Fox", start=START, location=VENUE, source=ticket_text, concert=True)
        p = prepare_event(req, context_text="", now=now, settings=settings)
        assert p.openers == ("Zork.",)
        assert "Opener: Zork." in p.lines

    def test_context_text_is_appended_to_the_caller_source(self, settings, now, ticket_text):
        req = AddRequest(title="Zed Fox", start=START, location=VENUE, source="caller text", concert=True)
        p = prepare_event(req, context_text=ticket_text, now=now, settings=settings)
        assert p.raw_source == "caller text\n" + ticket_text

    def test_empty_source_norm_when_there_is_no_text(self, settings, now):
        req = AddRequest(title="Zed Fox", start=START, location=VENUE)
        p = prepare_event(req, context_text="", now=now, settings=settings)
        assert p.source_norm is None and p.raw_source == ""


class TestResolveCalendar:
    def test_default_and_primary(self, svc):
        assert resolve_calendar(None, svc, dry_run=False) == ("primary", "Personal")
        assert resolve_calendar("", svc, dry_run=False) == ("primary", "Personal")
        assert resolve_calendar("Primary", svc, dry_run=False) == ("primary", "Personal")
        assert svc.calendar.calls == []

    def test_an_id_is_its_own_name(self, svc):
        cal = "abc@group.calendar.example"
        assert resolve_calendar(cal, svc, dry_run=False) == (cal, cal)
        assert svc.calendar.calls == []

    def test_dry_run_never_asks(self, svc):
        assert resolve_calendar("Concerts", svc, dry_run=True) == ("<id of Concerts>", "Concerts")
        assert svc.calendar.calls == []

    def test_a_name_is_matched_case_insensitively_as_a_substring(self, svc):
        assert resolve_calendar("concert", svc, dry_run=False) == ("cal-concerts", "Concerts")

    def test_ambiguous_or_missing_is_a_fix(self, svc):
        svc.calendar.calendars.append({"id": "cal-2", "summary": "Concerts (old)"})
        with pytest.raises(FixInput) as exc:
            resolve_calendar("Concerts", svc, dry_run=False)
        assert exc.value.error == "calendar 'Concerts' matched 2 calendars"
        assert exc.value.fix == "use one of: Concerts, Concerts (old), Personal"


class TestVenueRegistry:
    QUARRY = VenueHit(
        "The Quarry",
        ("1 Rock Rd", "San Francisco CA 94110", "United States"),
        website="https://thequarry.example",
        source="places",
    )

    def test_a_found_venue_writes_the_maps_form_and_a_venue_line(self, settings, now, forum_route, ticket_text):
        places = FakeVenueLookup({"the quarry": self.QUARRY})
        svc = services(settings, now, route=forum_route, text=ticket_text, places=places)
        out = concert(svc, source_text=ticket_text)
        assert out.kind is Kind.OK, out
        [ins] = svc.calendar.inserted
        assert ins["location"] == "The Quarry\n1 Rock Rd\nSan Francisco CA 94110\nUnited States"
        assert "Venue: https://thequarry.example" in ins["description"].split("\n")
        assert out.data["location"] == ins["location"]
        assert out.data["venue"]["visits"] == ["2026-11-14"]
        # The reply, the topic and the setlist tie-break use the short form, never the address block.
        assert "• **Where:** The Quarry, San Francisco\n" in out.reply
        assert "• **Venue:** https://thequarry.example\n" in out.reply
        assert out.data["topic"] == "2026-11-14 | Zed Fox | The Quarry"
        assert places.queries == ["The Quarry, San Francisco"]

    def test_the_second_show_needs_no_lookup(self, settings, now, forum_route, ticket_text):
        places = FakeVenueLookup({"the quarry": self.QUARRY})
        svc = services(settings, now, route=forum_route, text=ticket_text, places=places)
        assert concert(svc, source_text=ticket_text).kind is Kind.OK
        # The same topic: `another` asks for a second event instead of an update of the first.
        out = concert(svc, artist="Other Act", start="2026-12-01 20:00", source_text="", another=True)
        assert out.kind is Kind.OK, out
        assert places.queries == ["The Quarry, San Francisco"]
        assert out.data["venue"]["visits"] == ["2026-11-14", "2026-12-01"]
        assert svc.calendar.inserted[1]["location"] == svc.calendar.inserted[0]["location"]

    def test_no_address_anywhere_keeps_the_location_as_written(self, svc, ticket_text):
        out = concert(svc, source_text=ticket_text)
        assert out.kind is Kind.OK
        assert svc.calendar.inserted[0]["location"] == VENUE
        assert "Venue:" not in svc.calendar.inserted[0]["description"]
        assert out.data["venue"]["source"] == "caller" and out.data["venue"]["visits"] == ["2026-11-14"]

    def test_a_plain_event_never_touches_the_registry(self, svc):
        out = add_plain(title="[Dinner] Team dinner", start=START, location="ZZZ Bistro, San Francisco", svc=svc)
        assert out.kind is Kind.OK, out
        assert out.data["venue"] is None
        assert svc.state.read_json(svc.settings.venues_file, {}) == {}


class TestTopicRegistry:
    """A Telegram forum topic owns one event: a repeat add there updates it unless `another` asks for a second."""

    def test_the_insert_registers_the_topic(self, svc, ticket_text):
        out = concert(svc, source_text=ticket_text)
        rows = svc.state.read_json(svc.settings.topics_file, {})
        assert rows == {
            "-1001234567890:7": {
                "event_id": out.data["id"],
                "calendar_id": "cal-concerts",
                "summary": "Zed Fox",
                "date": "2026-11-14",
                "updated": svc.clock.now().isoformat(timespec="seconds"),
            }
        }

    def test_a_repeat_in_the_topic_updates_the_event(self, svc, ticket_text):
        first = concert(svc, artist="DICE with The Back Alley", source_text="", start="2026-11-14 20:00")
        assert first.kind is Kind.NEED_USER  # the platform guard; nothing registered yet
        first = concert(svc, artist="Wrong Name", source_text="", start="2026-11-14 20:00")
        assert first.kind is Kind.OK, first
        out = concert(svc, source_text=ticket_text)
        assert out.kind is Kind.OK, out
        assert out.data["updated"] is True
        assert out.data["id"] == first.data["id"]
        assert len(svc.calendar.inserted) == 1
        [(cal, eid, fields)] = svc.calendar.patched
        assert (cal, eid) == ("cal-concerts", first.data["id"])
        assert fields["summary"] == "Zed Fox"
        assert fields["start"] == {"dateTime": datetime(2026, 11, 14, 20, 30, tzinfo=LA).isoformat()}
        assert fields["end"] == {"dateTime": datetime(2026, 11, 14, 22, 30, tzinfo=LA).isoformat()}
        assert fields["location"] == VENUE
        assert fields["description"].startswith("Doors: 7:30 PM\nPurchased: 2 tickets, $42.00 on Ticketweb\n")
        assert out.reply.startswith("**Updated on Concerts**\n• **Artist:** Zed Fox\n")
        assert "\nChanged: title Wrong Name -> Zed Fox\n" in out.reply  # the live text already set the show time
        assert out.reply.endswith(f"[Open in Google Calendar]({eventedit(first.data['id'])})")
        assert out.data["changed"] == ["title Wrong Name -> Zed Fox"]
        # The queue row and the topic name follow the new facts.
        [q] = queue_list(svc)
        assert (q.event_id, q.artist, q.date) == (first.data["id"], "Zed Fox", "2026-11-14")
        assert svc.chat.renamed[-1][2] == "2026-11-14 | Zed Fox | The Quarry"

    def test_an_update_keeps_a_link_the_new_run_could_not_make(self, settings, now, forum_route, ticket_text):
        svc = services(settings, now, route=forum_route, text=ticket_text)
        first = concert(svc, source_text=ticket_text)
        assert f"Teaser playlist: {TEASER_URL}" in svc.calendar.inserted[0]["description"]
        svc.spotify = None  # Spotify is down for the update
        out = concert(svc, source_text=ticket_text)
        assert out.kind is Kind.OK and out.data["updated"] is True
        assert svc.calendar.patched[-1][2]["description"].endswith(f"Teaser playlist: {TEASER_URL}")
        assert "Nothing changed; the description was refreshed" in out.reply
        assert first.data["id"] == out.data["id"]

    def test_another_inserts_a_second_event_and_takes_the_topic(self, svc, ticket_text):
        first = concert(svc, source_text=ticket_text)
        out = concert(svc, artist="Other Act", start="2026-12-01 20:00", source_text="", another=True)
        assert out.kind is Kind.OK, out
        assert out.data["updated"] is False
        assert len(svc.calendar.inserted) == 2 and svc.calendar.patched == []
        rows = svc.state.read_json(svc.settings.topics_file, {})
        assert rows["-1001234567890:7"]["event_id"] == out.data["id"] != first.data["id"]
        assert rows["-1001234567890:7"]["summary"] == "Other Act"

    def test_a_deleted_event_frees_the_topic(self, svc, ticket_text):
        first = concert(svc, source_text=ticket_text)
        svc.calendar.delete_event("cal-concerts", first.data["id"])  # gone behind our back
        out = concert(svc, artist="Other Act", start="2026-12-01 20:00", source_text="")
        assert out.kind is Kind.OK, out
        assert out.data["updated"] is False
        assert len(svc.calendar.inserted) == 2
        assert svc.state.read_json(svc.settings.topics_file, {})["-1001234567890:7"]["event_id"] == out.data["id"]

    def test_a_cancelled_event_frees_the_topic(self, svc, ticket_text):
        first = concert(svc, source_text=ticket_text)
        for e in svc.calendar.events["cal-concerts"]:
            if e["id"] == first.data["id"]:
                e["status"] = "cancelled"
        out = concert(svc, artist="Other Act", start="2026-12-01 20:00", source_text="")
        assert out.kind is Kind.OK and out.data["updated"] is False
        assert len(svc.calendar.inserted) == 2

    def test_the_title_guard_duplicate_also_registers_the_topic(self, svc, ticket_text):
        e = svc.calendar.seed_event(
            "cal-concerts", summary="Zed Fox", start=datetime(2026, 11, 14, 20, 30, tzinfo=LA), location=VENUE
        )
        out = concert(svc, source_text=ticket_text)
        assert out.kind is Kind.DUPLICATE
        assert svc.state.read_json(svc.settings.topics_file, {})["-1001234567890:7"]["event_id"] == e["id"]

    def test_outside_a_forum_topic_nothing_is_registered_and_the_title_guard_stands(self, settings, now, ticket_text):
        dm = {
            "channel": "telegram",
            "target": {"to": "telegram:12345"},
            "thread": None,
            "accountId": None,
            "lastInteractionAt": ms(now),
        }
        svc = services(settings, now, route=dm, text=ticket_text)
        concert(svc, source_text=ticket_text)
        assert svc.state.read_json(svc.settings.topics_file, {}) == {}
        out = concert(svc, artist="Other Act", start="2026-12-01 20:00", source_text="")
        assert out.kind is Kind.OK and out.data["updated"] is False
        assert len(svc.calendar.inserted) == 2
        dup = concert(svc, source_text=ticket_text)
        assert dup.kind is Kind.DUPLICATE

    def test_a_plain_event_never_touches_the_topic_registry(self, svc):
        add_plain(title="[Dinner] Team dinner", start=START, location="ZZZ Bistro, San Francisco", svc=svc)
        assert svc.state.read_json(svc.settings.topics_file, {}) == {}

    def test_dry_run_updates_nothing(self, svc, ticket_text):
        concert(svc, source_text=ticket_text)
        out = concert(svc, artist="Other Act", start="2026-12-01 20:00", source_text="", dry_run=True)
        assert out.kind is Kind.DRY_RUN
        assert svc.calendar.patched == [] and len(svc.calendar.inserted) == 1

    def test_from_mapping_reads_the_another_key(self):
        assert AddRequest.from_mapping({"title": "X", "another": True}).another is True
        assert AddRequest.from_mapping({"title": "X"}).another is False
        assert AddRequest.from_mapping({"title": "X"}, another=True).another is True


class TestLineup:
    """The lineup step after the insert: one grounded call, lines patched in, bullets in the reply."""

    PAGE = "https://www.setlist.fm/setlist/zork/2026/the-hollow-oakland-ca-1a2b3c4d.html"
    FULL = '{"openers": ["Zork"], "doors": "7:30 PM", "opener_on_stage": "8:00 PM", "headliner_on_stage": "9:00 PM"}'
    ZORK_ROW = {
        "id": "1a2b3c4d",
        "url": PAGE,
        "eventDate": "02-10-2026",
        "artist": {"name": "Zork"},
        "venue": {"name": "The Hollow", "city": {"name": "Oakland"}},
        "sets": {"set": [{"song": [{"name": "Hum"}]}]},
    }

    def svc_with(self, settings, now, forum_route, text, *, gemini: FakeGemini):
        return services(
            settings,
            now,
            route=forum_route,
            text=text,
            gemini=gemini,
            setlistfm=FakeSetlistFm({("Zork", None, None): {"setlist": [self.ZORK_ROW]}}),
        )

    def test_ticket_without_an_opener_gets_opener_expected_and_setlist_lines(self, settings, now, forum_route):
        text = "ZED FOX\nThe Quarry, San Francisco\nSat Nov 14 2026\nDoors 7:30 PM Show 8:30 PM\n"
        svc = self.svc_with(settings, now, forum_route, text, gemini=FakeGemini(self.FULL, ("https://v.example",)))
        out = concert(svc, artist="Zed Fox", source_text=text)
        assert out.kind is Kind.OK, out
        [ins] = svc.calendar.inserted
        assert ins["description"] == f"Doors: 7:30 PM\nTeaser playlist: {TEASER_URL}"
        [(_, eid, fields)] = svc.calendar.patched
        assert eid == "evt1"
        assert fields["description"] == (
            f"Doors: 7:30 PM\nTeaser playlist: {TEASER_URL}\nHeadliner: Zed Fox\nOpener: Zork\n"
            f"Expected: doors 7:30 PM, Zork 8:00 PM, Zed Fox 9:00 PM\nOpener setlist (recent): {self.PAGE}"
        )
        assert out.reply == (
            "**Added to Concerts**\n"
            "• **Artist:** Zed Fox\n"
            "• **When:** Sat Nov 14, 8:30 PM – 10:30 PM PST (end assumed)\n"
            "• **Where:** The Quarry, San Francisco\n"
            "• **Doors:** 7:30 PM\n"
            "• **Opener:** Zork\n"
            "• **Expected:** doors 7:30 PM, Zork 8:00 PM, Zed Fox 9:00 PM\n"
            f"• **Opener setlist (recent):** {self.PAGE}\n"
            f"• **Teaser:** [2026-11-14 | Zed Fox]({TEASER_URL}) 3 liked songs. Move it to Concerts › Teasers in Spotify.\n"
            "• **Setlist:** not on setlist.fm yet; checking weekly for 2 months\n"
            f"[Open in Google Calendar]({eventedit('evt1')})"
        )
        assert out.data["lineup"]["openers"] == [{"name": "Zork", "setlist_url": self.PAGE}]
        assert out.data["description"][-1] == f"Opener setlist (recent): {self.PAGE}"
        assert len(svc.gemini.prompts) == 1

    def test_ticket_with_an_opener_keeps_its_opener_and_gains_the_times(self, settings, now, forum_route, ticket_text):
        svc = self.svc_with(settings, now, forum_route, ticket_text, gemini=FakeGemini(self.FULL))
        out = concert(svc, source_text=ticket_text)
        [(_, _, fields)] = svc.calendar.patched
        assert fields["description"].count("Opener: ") == 1
        assert "Opener: Zork." in fields["description"] and "Opener: Zork\n" not in fields["description"]
        assert "• **Expected:** doors 7:30 PM, Zork 8:00 PM, Zed Fox 9:00 PM" in out.reply
        assert f"• **Opener setlist (recent):** {self.PAGE}" in out.reply

    def test_nothing_found_leaves_the_event_and_the_reply_as_they_were(self, settings, now, forum_route, ticket_text):
        svc = self.svc_with(settings, now, forum_route, ticket_text, gemini=FakeGemini("no idea"))
        out = concert(svc, source_text=ticket_text)
        assert out.reply == golden_added()
        assert svc.calendar.patched == []
        assert out.data["lineup"]["status"] == "none"

    def test_a_model_failure_never_fails_the_add(self, settings, now, forum_route, ticket_text):
        svc = self.svc_with(settings, now, forum_route, ticket_text, gemini=FakeGemini(fail=True))
        out = concert(svc, source_text=ticket_text)
        assert out.kind is Kind.OK
        assert out.reply == golden_added()
        assert out.data["lineup"]["status"] == "failed"

    def test_without_a_model_nothing_is_asked(self, svc, ticket_text):
        out = concert(svc, source_text=ticket_text)
        assert out.reply == golden_added()
        assert "lineup" not in out.data
        assert svc.calendar.patched == []

    def test_dry_run_asks_nothing(self, settings, now, forum_route, ticket_text):
        gemini = FakeGemini(self.FULL)
        svc = self.svc_with(settings, now, forum_route, ticket_text, gemini=gemini)
        out = concert(svc, source_text=ticket_text, dry_run=True)
        assert out.kind is Kind.DRY_RUN
        assert gemini.prompts == []
