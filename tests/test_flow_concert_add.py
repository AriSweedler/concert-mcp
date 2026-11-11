"""flows/concert_add.py end to end on the fakes, with the legacy cal-add.py replies as goldens."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from concert_mcp.flows.concert_add import (
    KNOWN,
    AddRequest,
    add_concert,
    add_plain,
)
from concert_mcp.flows.queue import queue_list
from concert_mcp.flows.services import Services
from concert_mcp.result import NEED_USER, FixInput, Kind
from fakes import (
    FakeCalendar,
    FakeChat,
    FakeClock,
    FakeContext,
    FakeGemini,
    FakeSetlistFm,
    FakeSpotify,
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


def event_url(event_id: str, calendar_id: str = "cal-concerts") -> str:
    eid = base64.urlsafe_b64encode(f"{event_id} {calendar_id}".encode()).decode().rstrip("=")
    return f"https://www.google.com/calendar/event?eid={eid}"


def golden_added(event_id: str = "evt1", *, notes: str = "", when: str = "Sat Nov 14, 8:30 PM PST") -> str:
    return (
        "**Added to Concerts**\n"
        "• **Artist:** Zed Fox\n"
        f"• **When:** {when}\n"
        "• **Where:** The Quarry, San Francisco\n"
        "• **Doors:** 7:30 PM\n"
        "• **Purchased:** 2 tickets, $42.00 on Ticketweb\n"
        "• **Opener:** Zork.\n"
        f"• [Teaser playlist]({TEASER_URL}), 3 liked songs"
        f"{notes}\n"
        f"[Open in Google Calendar]({event_url(event_id)})"
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
    def test_reply_is_the_golden_byte_for_byte(self, svc, ticket_text):
        out = concert(svc, source_text=ticket_text)
        assert out.reply == golden_added()

    def test_context_text_alone_is_enough_for_the_derived_lines(self, svc):
        # svc's FakeContext carries the ticket text as the recent OCR / chat text; source_text is empty.
        out = concert(svc)
        assert out.reply == golden_added()


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
        assert out.reply.endswith(f"[Open in Google Calendar]({event_url(e['id'])})")
        assert out.data["id"] == e["id"]
        assert out.to_json()["duplicate"] is True
        assert [q.event_id for q in queue_list(svc)] == [e["id"]]


class TestShowTime:
    def test_start_at_doors_is_corrected_to_the_certain_show_time(self, svc, ticket_text):
        out = concert(svc, start="2026-11-14 19:30", source_text=ticket_text)
        assert out.kind is Kind.OK
        [ins] = svc.calendar.inserted
        assert ins["start"]["dateTime"] == datetime(2026, 11, 14, 20, 30, tzinfo=LA).isoformat()
        assert ins["end"]["dateTime"] == datetime(2026, 11, 14, 22, 30, tzinfo=LA).isoformat()
        assert out.reply == golden_added(notes="\nStart corrected to 8:30 PM, the show time in the text.")

    def test_date_only_start_takes_the_show_time_from_the_text(self, svc, ticket_text):
        out = concert(svc, start="2026-11-14", source_text=ticket_text)
        assert out.kind is Kind.OK
        assert svc.calendar.inserted[0]["start"]["dateTime"] == datetime(2026, 11, 14, 20, 30, tzinfo=LA).isoformat()
        assert out.reply == golden_added(notes="\nStart taken from the text (8:30 PM).")


class TestRequiredFields:
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

    def test_the_venue_as_the_artist_is_needed(self, svc, ticket_text):
        out = concert(svc, artist="The Quarry", venue="The Quarry, San Francisco", source_text=ticket_text)
        assert out.kind is Kind.NEED_USER
        assert out.error == "artist 'The Quarry' is the venue, not the headliner"
        assert svc.calendar.inserted == []


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

    def test_with_no_text_at_all_every_claim_line_is_dropped(self, settings, now):
        svc = services(settings, now, route=None, text="")
        out = concert(svc, notes="Doors: 7:30 PM\nSeat: GA")
        assert out.kind is Kind.OK
        assert out.data["dropped"] == ["Opener: Zork.", "Doors: 7:30 PM"]
        # With no text nothing verifies: the title's opener and the clock-time note go; the claim-free note survives.
        assert out.data["description"][:1] == ["Seat: GA"]


class TestFromMapping:
    def test_date_and_time_make_a_start(self):
        req = AddRequest.from_mapping({"title": "X", "date": "2026-11-14", "time": "8:30 PM"})
        assert req.start == "2026-11-14 8:30 PM"

    def test_unknown_key_is_a_fix_naming_the_allowed_keys(self):
        with pytest.raises(FixInput) as exc:
            AddRequest.from_mapping({"title": "X", "foo": 1})
        assert exc.value.error == "unknown keys: foo"
        assert exc.value.fix == (
            "allowed keys: allowPast, another, attendees, calendar, description, end, location, source, start, title, tz"
        )
        assert exc.value.fix == f"allowed keys: {', '.join(sorted(KNOWN - {'date', 'time'}))}"

    def test_not_an_object_is_a_fix(self):
        with pytest.raises(FixInput) as exc:
            AddRequest.from_mapping(["Zed Fox"])  # type: ignore[arg-type]
        assert exc.value.error == "input must be a JSON object"
        assert exc.value.fix == "send one {...} object"


class TestTopicRegistry:
    """A Telegram forum topic owns one event: a repeat add there updates it unless `another` asks for a second."""

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
        assert out.reply.endswith(f"[Open in Google Calendar]({event_url(first.data['id'])})")
        assert out.data["changed"] == ["title Wrong Name -> Zed Fox"]
        # The queue row and the topic name follow the new facts.
        [q] = queue_list(svc)
        assert (q.event_id, q.artist, q.date) == (first.data["id"], "Zed Fox", "2026-11-14")
        assert svc.chat.renamed[-1][2] == "2026-11-14 | Zed Fox | The Quarry"

    def test_a_cancelled_event_frees_the_topic(self, svc, ticket_text):
        first = concert(svc, source_text=ticket_text)
        for e in svc.calendar.events["cal-concerts"]:
            if e["id"] == first.data["id"]:
                e["status"] = "cancelled"
        out = concert(svc, artist="Other Act", start="2026-12-01 20:00", source_text="")
        assert out.kind is Kind.OK and out.data["updated"] is False
        assert len(svc.calendar.inserted) == 2

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

    def test_found_set_times_link_to_their_source_and_the_opener_page_is_a_link(
        self, settings, now, forum_route, ticket_text
    ):
        source = "https://thequarry.example/shows/zed-fox"
        svc = self.svc_with(settings, now, forum_route, ticket_text, gemini=FakeGemini(self.FULL, sources=[source]))
        out = concert(svc, source_text=ticket_text)
        assert out.kind is Kind.OK, out.reply
        assert f"• [Expected]({source}): doors 7:30 PM, Zork 8:00 PM, Zed Fox 9:00 PM" in out.reply
        assert f"• [Opener setlist (recent)]({self.PAGE})" in out.reply
        assert self.PAGE + ")" in out.reply and f": {self.PAGE}" not in out.reply  # never the bare address

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
