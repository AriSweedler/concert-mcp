"""The concert records: text/records.py against literals, flows/records.py and CachedCalendar on the fakes, and the
flows that write and read a record (concert_add, tickets_add, `concert-mcp concerts`)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from concert_mcp.adapters.state import JsonState
from concert_mcp.flows.concert_add import add_concert
from concert_mcp.flows.partiful import tickets_outcome
from concert_mcp.flows.records import (
    CachedCalendar,
    all_records,
    concerts_outcome,
    find_record,
    forget_event,
    record_for_event,
    remember_concert,
    remember_invite,
    save_record,
)
from concert_mcp.flows.services import Services
from concert_mcp.result import Kind
from concert_mcp.text.description import PlainDescription
from concert_mcp.text.records import (
    ConcertRecord,
    artist_slug,
    event_of_record,
    expressions,
    facts,
    invite_plan,
    patched_record,
    record_name,
    record_of_event,
    record_rows,
    same_show,
)
from concert_mcp.text.reply import PARTIFUL_NO_EVENT_REPLY, render_concerts
from conftest import TICKET_TEXT, run
from fakes import FakeCalendar, FakeClock, FakePartiful, MemoryState

LA = ZoneInfo("America/Los_Angeles")
SHOW = datetime(2026, 11, 14, 20, 30, tzinfo=LA)
NOW = "2026-10-25T12:00:00-07:00"
TICKETS = "https://www.ticketweb.com/event/zed-fox/123"
TEASER = "https://open.spotify.com/playlist/pl1"
PAGE = "https://partiful.com/e/pf1"
TAIL = "\n\nCreated by github.com/AriSweedler/concert-mcp"
TOPIC = "-1001234567890:7"
PHONE = "+15555550100"


def record(**over: Any) -> ConcertRecord:
    base: dict[str, Any] = dict(
        artist="Zed Fox",
        start="2026-11-14T20:30:00-08:00",
        end="2026-11-14T22:30:00-08:00",
        location="The Quarry\n1 Main St\nSan Francisco, CA 94100",
        lines=("Doors: 7:30 PM", f"Teaser playlist: {TEASER}"),
        calendar_id="cal-concerts",
        event_id="evt1",
        html_link="https://www.google.com/calendar/event?eid=abc",
        updated=NOW,
    )
    base.update(over)
    return ConcertRecord(**base)


# ---------- text/records.py ----------


def test_a_record_is_named_by_day_and_artist_and_survives_json() -> None:
    assert artist_slug("Rodrigo y Gabriela") == "rodrigo-y-gabriela"
    assert artist_slug("AC/DC!") == "ac-dc" and artist_slug("???") == "concert"
    assert record_name("2026-11-15T19:00:00-08:00", "Rodrigo y Gabriela") == "2026-11-15-rodrigo-y-gabriela.json"
    late = record_name("2026-11-15T22:30:00-08:00", "Rodrigo y Gabriela", with_time=True)
    assert late == "2026-11-15-2230-rodrigo-y-gabriela.json"
    assert record_name("2026-11-15", "Zed Fox", with_time=True) == "2026-11-15-zed-fox.json"  # an all-day event

    full = record(topic=TOPIC, invite={"url": PAGE, "title": "T", "description": "D", "links": []})
    assert full.date == "2026-11-14"
    assert ConcertRecord.from_json(full.to_json()) == full
    assert full.to_json()["calendar"] == {
        "calendar_id": "cal-concerts",
        "event_id": "evt1",
        "html_link": "https://www.google.com/calendar/event?eid=abc",
    }
    bare = ConcertRecord.from_json({"artist": "Zed Fox", "calendar": "nonsense", "invite": "nonsense"})
    assert (bare.event_id, bare.end, bare.html_link, bare.topic, bare.invite, bare.lines) == (
        "",
        None,
        None,
        None,
        None,
        (),
    )


def test_a_record_follows_its_calendar_event() -> None:
    event = {
        "id": "evt1",
        "summary": "Zed Fox",
        "start": {"dateTime": "2026-11-14T20:30:00-08:00"},
        "end": {"dateTime": "2026-11-14T22:30:00-08:00"},
        "location": "The Quarry",
        "description": "Doors: 7:30 PM\nOpener: Zork",
        "htmlLink": "https://cal.example/evt1",
    }
    made = record_of_event(event, "cal-concerts", now=NOW)
    assert (made.artist, made.start, made.lines) == (
        "Zed Fox",
        "2026-11-14T20:30:00-08:00",
        ("Doors: 7:30 PM", "Opener: Zork"),
    )
    assert (made.html_link, made.topic, made.invite, made.updated) == ("https://cal.example/evt1", None, None, NOW)
    # What the calendar does not hold is kept from the record already known; an all-day event has a date only.
    known = replace(made, topic=TOPIC, invite={"url": PAGE})
    again = record_of_event(
        {"id": "evt1", "start": {"date": "2026-11-14"}, "end": "?"}, "cal-concerts", now=NOW, known=known
    )
    assert (again.start, again.end, again.html_link) == ("2026-11-14", None, "https://cal.example/evt1")
    assert (again.topic, again.invite) == (TOPIC, {"url": PAGE})

    back = event_of_record(made)
    assert back["start"] == {"dateTime": made.start} and back["description"] == "Doors: 7:30 PM\nOpener: Zork"
    assert event_of_record(replace(made, end=None))["end"] == {}

    same = patched_record(made, now="later")
    assert same == replace(made, updated="later")
    moved = patched_record(
        made, now="later", summary="Zed Fox II", start=SHOW, end=SHOW, location="Elsewhere", description="Doors: 6 PM"
    )
    assert (moved.artist, moved.start, moved.end) == ("Zed Fox II", SHOW.isoformat(), SHOW.isoformat())
    assert (moved.location, moved.lines) == ("Elsewhere", ("Doors: 6 PM",))


def test_facts_and_expressions_come_from_the_record_alone() -> None:
    plain = record(lines=("Doors: 7:30 PM", "Opener: Zork", "Opener: Zap"), location="")
    assert facts(plain) == {
        "artist": "Zed Fox",
        "start": "2026-11-14T20:30:00-08:00",
        "doors": "7:30 PM",
        "openers": ["Zork", "Zap"],
    }
    assert invite_plan(plain) is None
    assert expressions(plain) == {
        "calendar": "made",
        "teaser playlist": "not made",
        "setlist playlist": "not made",
        "partiful": "needs the ticket link",
    }

    ticketed = record(lines=("Doors: 7:30 PM", f"Teaser playlist: {TEASER}", f"Tickets: {TICKETS}"))
    assert facts(ticketed)["venue"] == "The Quarry" and facts(ticketed)["tickets"] == TICKETS
    plan = invite_plan(ticketed)
    assert plan == {
        "title": "Concert: Zed Fox at The Quarry 11/14",
        "description": "Doors 7:30, show 8:30" + TAIL,
        "links": [["Tickets", TICKETS], ["Zed Fox songs I like", TEASER]],
    }
    assert expressions(ticketed)["partiful"] == "can be made" and expressions(ticketed)["teaser playlist"] == "made"

    lines = (*ticketed.lines, f"Partiful: {PAGE}", "Setlist playlist: https://open.spotify.com/playlist/pl2")
    # An invite the record did not see made (no snapshot) is simply made; one made from these facts is made too.
    assert expressions(replace(ticketed, lines=lines))["partiful"] == "made"
    assert expressions(replace(ticketed, lines=lines))["setlist playlist"] == "made"
    made = replace(ticketed, lines=lines)
    made = replace(made, invite={"url": PAGE, **invite_plan(made)})
    assert expressions(made)["partiful"] == "made"
    # The page was made before the show had its setlist playlist: the links it was made with are now short one.
    assert expressions(replace(made, invite={"url": PAGE, **plan}))["partiful"] == "out of date"
    # A fact that changes what the invite would say (a doors time corrected) makes the invite out of date.
    later = replace(made, lines=("Doors: 7:00 PM", *lines[1:]))
    assert expressions(later)["partiful"] == "out of date"

    assert same_show(ticketed, "zed fox", "2026-11-14") and not same_show(ticketed, "Zed Fox", "2026-11-15")
    rows = record_rows([replace(plain, start="2027-01-01T20:00:00-08:00"), ticketed])
    assert [r.start[:4] for r, _e in rows] == ["2026", "2027"]
    assert render_concerts(rows[:1]) == (
        "**Concerts** (1 shows)\n"
        "• 2026-11-14 Zed Fox @ The Quarry — calendar made; teaser playlist made; setlist playlist not made; "
        "partiful can be made"
    )
    assert render_concerts([]) == "No concerts recorded yet."


# ---------- flows/records.py ----------


@pytest.fixture
def state() -> MemoryState:
    return MemoryState()


DIR = Path("/state/concerts")


def test_records_are_saved_found_renamed_and_forgotten(state: MemoryState) -> None:
    first = save_record(record(), state, DIR)
    assert first == DIR / "2026-11-14-zed-fox.json"
    assert save_record(record(updated="later"), state, DIR) == first  # the same show again: the same file
    # A second show by the artist that day gets its start time in the name.
    late = record(event_id="evt2", start="2026-11-14T23:00:00-08:00")
    assert save_record(late, state, DIR) == DIR / "2026-11-14-2300-zed-fox.json"
    # A record whose artist was corrected moves to its new name.
    fixed = save_record(record(artist="Zed Fox Trio"), state, DIR)
    assert fixed == DIR / "2026-11-14-zed-fox-trio.json" and state.names(DIR) == [
        "2026-11-14-2300-zed-fox.json",
        "2026-11-14-zed-fox-trio.json",
    ]
    # Files that are not records are skipped, never fatal.
    state.write_json(DIR / "notes.json", ["not", "a", "record"])
    state.write_json(DIR / "empty.json", {"artist": "No event"})
    assert [r.event_id for _p, r in all_records(state, DIR)] == ["evt2", "evt1"]
    assert record_for_event("evt2", state, DIR)[1].start == late.start and record_for_event("nope", state, DIR) is None
    assert forget_event("evt2", state, DIR) is True and forget_event("evt2", state, DIR) is False


class BrokenState(MemoryState):
    """A state store whose directory listing fails."""

    def names(self, directory: Path) -> list[str]:
        raise OSError("disk gone")


def test_the_flows_remember_and_find_shows_and_never_fail_for_the_cache(svc: Services) -> None:
    directory = svc.settings.concerts_dir
    event = svc.calendar.seed_event("cal-concerts", summary="Zed Fox", start=SHOW, description="Doors: 7:30 PM")
    remember_concert(event, "cal-concerts", svc, topic=TOPIC)
    remember_concert({"summary": "No id"}, "cal-concerts", svc)  # nothing to key a record on
    [(path, saved)] = all_records(svc.state, directory)
    assert path.name == "2026-11-14-zed-fox.json" and (saved.topic, saved.lines) == (TOPIC, ("Doors: 7:30 PM",))
    # Remembered again without a topic (a refresh), the topic and the invite the record knew are kept.
    remember_invite("evt1", {"url": PAGE, "title": "T"}, svc)
    remember_invite("nope", {"url": PAGE}, svc)
    remember_concert(event, "cal-concerts", svc)
    kept = record_for_event("evt1", svc.state, directory)[1]
    assert (kept.topic, kept.invite) == (TOPIC, {"url": PAGE, "title": "T"})

    other = svc.calendar.seed_event("cal-concerts", summary="Zork", start=SHOW)
    remember_concert(other, "cal-concerts", svc, topic=TOPIC)
    svc.clock.advance(60)
    remember_concert(other, "cal-concerts", svc, topic=TOPIC)
    assert find_record(svc, topic=TOPIC).artist == "Zork"  # the topic's latest show
    assert find_record(svc, topic="elsewhere:1", artist="zed fox", date="2026-11-14").event_id == "evt1"
    assert find_record(svc, artist="Zed Fox", date="2027-01-01") is None and find_record(svc) is None
    out = concerts_outcome(svc)
    assert out.kind is Kind.OK and out.reply.startswith("**Concerts** (2 shows)\n• 2026-11-14 Zed Fox @ ")
    assert [c["artist"] for c in out.data["concerts"]] == ["Zed Fox", "Zork"]
    assert out.data["concerts"][0]["expressions"]["calendar"] == "made"

    svc.state = BrokenState()
    remember_concert(event, "cal-concerts", svc)
    remember_invite("evt1", {"url": PAGE}, svc)
    assert find_record(svc, topic=TOPIC) is None


def test_the_cached_calendar_keeps_a_record_in_step(state: MemoryState) -> None:
    inner = FakeCalendar(calendars=[{"id": "cal-concerts", "summary": "Concerts"}])
    clock = FakeClock(datetime(2026, 10, 25, 12, 0, tzinfo=LA))
    calendar = CachedCalendar(inner, state, DIR, clock)
    assert calendar.list_calendars() == inner.list_calendars()
    made = calendar.insert_event(
        "cal-concerts", summary="Zed Fox", start=SHOW, end=SHOW, location="The Quarry", description=None, attendees=()
    )
    window = {"time_min": SHOW - timedelta(hours=1), "time_max": SHOW + timedelta(hours=1)}
    assert calendar.list_events("cal-concerts", q="Zed Fox", **window)[0]["id"] == made["id"]
    # An event with no record is not followed: a plain event or a movie never gets one.
    calendar.patch_event("cal-concerts", made["id"], description=PlainDescription("Doors: 7:30 PM"))
    assert calendar.get_event("cal-concerts", made["id"])["description"] == "Doors: 7:30 PM"
    assert state.names(DIR) == []

    save_record(record_of_event(inner.get_event("cal-concerts", made["id"]), "cal-concerts", now=NOW), state, DIR)
    calendar.patch_event(
        "cal-concerts", made["id"], description=PlainDescription(f"Doors: 7:30 PM\nTickets: {TICKETS}")
    )
    assert record_for_event(made["id"], state, DIR)[1].lines == ("Doors: 7:30 PM", f"Tickets: {TICKETS}")
    # A change made by hand in the calendar reaches the record on the next read of the event.
    inner.events["cal-concerts"][0]["description"] = "Doors: 6:00 PM"
    calendar.get_event("cal-concerts", made["id"])
    assert record_for_event(made["id"], state, DIR)[1].lines == ("Doors: 6:00 PM",)
    calendar.delete_event("cal-concerts", made["id"])
    assert state.names(DIR) == [] and inner.deleted == [("cal-concerts", made["id"])]


def test_the_cached_calendar_writes_a_concerts_description_in_reading_order(state: MemoryState) -> None:
    inner = FakeCalendar(calendars=[{"id": "cal-concerts", "summary": "Concerts"}])
    calendar = CachedCalendar(inner, state, DIR, FakeClock(datetime(2026, 10, 25, 12, 0, tzinfo=LA)))
    late_page = PlainDescription(
        f"Purchased: 2 tickets\nTeaser playlist: {TEASER}\nExpected: doors 6:00 PM\nTickets: {TICKETS}\n"
        f"Partiful: {PAGE}"
    )
    # A plain event or a movie (no record) is written as the caller gave it.
    plain = inner.seed_event("cal-concerts", summary="Dinner", start=SHOW)
    calendar.patch_event("cal-concerts", plain["id"], description=late_page)
    assert inner.get_event("cal-concerts", plain["id"])["description"] == late_page

    show = inner.seed_event("cal-concerts", summary="Zed Fox", start=SHOW, description="Doors: 7:30 PM")
    save_record(record_of_event(show, "cal-concerts", now=NOW), state, DIR)
    # Whichever flow patches a recorded concert (the lineup, tickets_add, the setlist), its lines land in order.
    calendar.patch_event("cal-concerts", show["id"], description=late_page)
    in_order = (
        f"Partiful: {PAGE}",
        "Expected: doors 6:00 PM",
        f"Teaser playlist: {TEASER}",
        f"Tickets: {TICKETS}",
        "Purchased: 2 tickets",
    )
    assert inner.patched[-1][2] == {"description": "\n".join(in_order)}
    assert record_for_event(show["id"], state, DIR)[1].lines == in_order  # the record holds what was written
    # A patch that carries no description, or an empty one, is passed as it is.
    calendar.patch_event("cal-concerts", show["id"], summary="Zed Fox Trio")
    calendar.patch_event("cal-concerts", show["id"], description=PlainDescription(""))
    assert inner.patched[-2][2] == {"summary": "Zed Fox Trio"} and inner.patched[-1][2] == {"description": ""}


def test_the_cached_calendar_never_fails_for_the_cache() -> None:
    inner = FakeCalendar(calendars=[{"id": "cal-concerts", "summary": "Concerts"}])
    event = inner.seed_event("cal-concerts", summary="Zed Fox", start=SHOW)
    calendar = CachedCalendar(inner, BrokenState(), DIR, FakeClock(datetime(2026, 10, 25, 12, 0, tzinfo=LA)))
    assert calendar.get_event("cal-concerts", event["id"])["summary"] == "Zed Fox"
    # A record that cannot be read leaves the description as given, and the patch still happens.
    calendar.patch_event("cal-concerts", event["id"], description=PlainDescription(f"Purchased: 2\nPartiful: {PAGE}"))
    assert inner.get_event("cal-concerts", event["id"])["description"] == f"Purchased: 2\nPartiful: {PAGE}"
    calendar.delete_event("cal-concerts", event["id"])
    assert inner.deleted == [("cal-concerts", event["id"])]


def test_json_state_lists_a_directorys_json_files(tmp_path: Path) -> None:
    state = JsonState()
    assert state.names(tmp_path / "concerts") == []
    state.write_json(tmp_path / "concerts" / "b.json", {})
    state.write_json(tmp_path / "concerts" / "a.json", {})
    (tmp_path / "concerts" / "notes.txt").write_text("x")
    (tmp_path / "concerts" / "dir.json").mkdir()
    assert state.names(tmp_path / "concerts") == ["a.json", "b.json"]


# ---------- the flows that write and read a record ----------


def with_partiful(svc: Services, partiful: FakePartiful) -> Services:
    svc.settings = replace(svc.settings, partiful_phone=PHONE)
    svc.partiful = partiful
    return svc


def test_concert_add_records_the_show_and_a_ticket_link_finds_it_there(svc: Services, monkeypatch) -> None:
    out = add_concert(
        artist="Zed Fox with special guest Zork.",
        start="2026-11-14 20:30",
        venue="The Quarry, San Francisco",
        source_text=TICKET_TEXT,
        svc=svc,
    )
    assert out.kind is Kind.OK
    [(path, saved)] = all_records(svc.state, svc.settings.concerts_dir)
    assert path.name == "2026-11-14-zed-fox.json"
    assert (saved.event_id, saved.calendar_id, saved.topic) == (out.data["id"], "cal-concerts", TOPIC)
    assert f"Teaser playlist: {TEASER}" in saved.lines and saved.html_link == out.data["htmlLink"]
    assert expressions(saved)["partiful"] == "needs the ticket link"

    # The ticket link arrives: the show comes from its record. The calendar is read once, never searched.
    with_partiful(svc, FakePartiful())
    svc.calendar.calls.clear()
    made = tickets_outcome(f"tickets: {TICKETS}", "", "", svc)
    assert made.kind is Kind.OK and made.data["status"] == "created"
    assert "list_events" not in [name for name, _args in svc.calendar.calls]
    assert [name for name, _args in svc.calendar.calls][0] == "get_event"
    # What the invite was made from is kept, so a later fact can tell it is out of date.
    kept = record_for_event(out.data["id"], svc.state, svc.settings.concerts_dir)[1]
    assert kept.invite["url"] == PAGE and kept.invite["title"] == "Concert: Zed Fox at The Quarry 11/14"
    assert kept.invite["description"].startswith("Doors 7:30, show 8:30")
    assert kept.invite["links"][0] == ["Tickets", TICKETS]


def test_a_named_topic_finds_its_show_with_no_live_chat(svc_cli: Services, monkeypatch, capsys) -> None:
    # A chat command knows the topic it was typed in; the CLI run has no live chat to ask.
    with_partiful(svc_cli, FakePartiful())
    event = svc_cli.calendar.seed_event(
        "cal-concerts", summary="Zed Fox", start=SHOW, location="The Quarry", description="Doors: 7:30 PM"
    )
    remember_concert(event, "cal-concerts", svc_cli, topic=TOPIC)
    assert tickets_outcome(TICKETS, "", "", svc_cli, dry_run=True).kind is Kind.NEED_USER  # which show?
    named = tickets_outcome(TICKETS, "", "", svc_cli, dry_run=True, topic=TOPIC)
    assert named.kind is Kind.DRY_RUN and named.reply.startswith("**Would make the Partiful** Concert: Zed Fox at")
    # A named topic that owns no show: there is nothing to attach the link to, and the reply says so.
    empty = tickets_outcome(TICKETS, "", "", svc_cli, topic="-1001234567890:99")
    assert empty.kind is Kind.NEED_USER and empty.reply == PARTIFUL_NO_EVENT_REPLY


def test_a_recorded_show_the_calendar_no_longer_has_is_forgotten(svc_cli: Services) -> None:
    with_partiful(svc_cli, FakePartiful())
    event = svc_cli.calendar.seed_event(
        "cal-concerts", summary="Zed Fox", start=SHOW, location="The Quarry", description="Doors: 7:30 PM"
    )
    remember_concert(event, "cal-concerts", svc_cli)
    # The calendar refusing the read is not proof the show is gone: the reply is the refusal, the record stays.
    svc_cli.calendar.fail = "get_event"
    refused = tickets_outcome(TICKETS, "Zed Fox", "2026-11-14", svc_cli, dry_run=True)
    assert refused.kind is Kind.ERROR and refused.data == {"provider": "gws"}
    assert all_records(svc_cli.state, svc_cli.settings.concerts_dir)
    # A cancelled event is gone: its record goes too, and the show is not found.
    svc_cli.calendar.fail = None
    svc_cli.calendar.events["cal-concerts"][0]["status"] = "cancelled"
    gone = tickets_outcome(TICKETS, "Zed Fox", "2026-11-14", svc_cli)
    assert gone.kind is Kind.NEED_USER and gone.error == "no calendar event for the show"
    assert all_records(svc_cli.state, svc_cli.settings.concerts_dir) == []


def test_the_concerts_command_lists_the_records(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _err = run(monkeypatch, capsys, ["concerts"])
    assert code == 0 and out.startswith("No concerts recorded yet.")
    event = cli_env.calendar.seed_event("cal-concerts", summary="Zed Fox", start=SHOW, location="The Quarry")
    remember_concert(event, "cal-concerts", cli_env)
    code, out, _err = run(monkeypatch, capsys, ["concerts"])
    assert code == 0 and out.startswith("**Concerts** (1 shows)\n• 2026-11-14 Zed Fox @ The Quarry — calendar made;")
