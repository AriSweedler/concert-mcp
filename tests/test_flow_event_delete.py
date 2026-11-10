"""flows/event_delete.py end to end on the fakes, with the legacy cal-del.py replies as goldens."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from concert_mcp.flows.event_delete import DELETE_KNOWN, DeleteRequest, delete_event
from concert_mcp.flows.queue import QueueEntry, queue_add, queue_list
from concert_mcp.flows.topics import topic_event, topic_remember
from concert_mcp.result import FixInput, Kind
from concert_mcp.text.reply import NO_CANDIDATES_REPLY
from concert_mcp.text.topics import TopicEntry

LA = ZoneInfo("America/Los_Angeles")
QUARRY = "The Quarry, San Francisco"
ANNEX = "The Annex, San Francisco"
TEASER_URL = "https://open.spotify.com/playlist/pl1"
CANDIDATE_KEYS = {"id", "calendarId", "calendar", "summary", "when", "start", "location", "htmlLink", "description"}

REMOVED_GOLDEN = (
    "**Removed from Concerts**\n"
    "• **Artist:** Zed Fox\n"
    "• **When:** Sat Nov 14, 8:30 PM – 10:30 PM PST\n"
    "• **Where:** The Quarry, San Francisco"
)
TEASER_ROW = f'• **Teaser:** [playlist]({TEASER_URL}) left in place; say "delete the teaser playlist" to remove it'
TWO_GOLDEN = (
    "I found 2 events like **Zed Fox**. Reply with the number to delete:\n"
    "1. **Zed Fox** on Concerts: Sat Nov 14, 8:30 PM – 10:30 PM PST, The Quarry, San Francisco\n"
    "2. **Zed Fox** on Personal: Fri Nov 20, 9:00 PM – 11:00 PM PST, The Annex, San Francisco"
)


def seed_zed_fox(svc, *, description: str = "") -> dict[str, Any]:
    return svc.calendar.seed_event(
        "cal-concerts",
        summary="Zed Fox",
        start=datetime(2026, 11, 14, 20, 30, tzinfo=LA),
        location=QUARRY,
        description=description,
    )


def seed_second(svc) -> dict[str, Any]:
    return svc.calendar.seed_event(
        "primary",
        summary="Zed Fox",
        start=datetime(2026, 11, 20, 21, 0, tzinfo=LA),
        location=ANNEX,
    )


def queue_row(event_id: str) -> QueueEntry:
    return QueueEntry(
        event_id=event_id,
        calendar_id="cal-concerts",
        artist="Zed Fox",
        date="2026-11-14",
        venue=QUARRY,
        setlistfm_url=None,
        song_count=0,
        until="2027-01-14",
        added="2026-10-25T19:00:00+00:00",
    )


def candidates_file(svc):
    return svc.state.read_json(svc.settings.candidates_file, None)


class TestOneMatch:
    def test_deletes_forgets_and_replies_the_golden(self, svc):
        e = seed_zed_fox(svc)
        queue_add(queue_row(e["id"]), svc)
        queue_add(queue_row("other"), svc)

        out = delete_event(DeleteRequest(title="Zed Fox"), svc)

        assert out.kind is Kind.OK
        assert svc.calendar.deleted == [("cal-concerts", e["id"])]
        assert [q.event_id for q in queue_list(svc)] == ["other"]
        assert out.reply == REMOVED_GOLDEN
        assert out.data["deleted"] is True
        assert out.data["id"] == e["id"]
        assert out.data["calendarId"] == "cal-concerts"
        assert out.data["calendar"] == "Concerts"
        assert candidates_file(svc) is None

    def test_deleting_the_topic_event_frees_the_topic(self, svc):
        e = seed_zed_fox(svc)
        topic_remember("-100:7", TopicEntry(e["id"], "cal-concerts", "Zed Fox", "2026-11-14", "t"), svc)
        topic_remember("-100:8", TopicEntry("other", "cal-concerts", "Other", "2026-12-01", "t"), svc)

        out = delete_event(DeleteRequest(title="Zed Fox"), svc)

        assert out.kind is Kind.OK
        assert topic_event("-100:7", svc) is None
        assert topic_event("-100:8", svc) is not None

    def test_teaser_line_in_the_description_adds_a_left_in_place_row(self, svc):
        seed_zed_fox(svc, description=f"Doors: 7:30 PM\nTeaser playlist: {TEASER_URL}")
        out = delete_event(DeleteRequest(title="Zed Fox"), svc)
        assert out.reply == REMOVED_GOLDEN + "\n" + TEASER_ROW


class TestSeveralMatches:
    def test_lists_them_numbered_and_keeps_the_candidates(self, svc):
        first, second = seed_zed_fox(svc), seed_second(svc)
        out = delete_event(DeleteRequest(title="Zed Fox"), svc)

        assert out.kind is Kind.OK
        assert out.reply == TWO_GOLDEN
        assert svc.calendar.deleted == []
        assert out.data["deleted"] is False
        stored = candidates_file(svc)
        assert [c["id"] for c in stored] == [first["id"], second["id"]]
        assert all(set(c) == CANDIDATE_KEYS for c in stored)
        assert stored[0]["calendarId"] == "cal-concerts" and stored[1]["calendarId"] == "primary"
        assert out.data["candidates"] == stored


class TestPick:
    @pytest.fixture
    def listed(self, svc):
        seed_zed_fox(svc)
        second = seed_second(svc)
        delete_event(DeleteRequest(title="Zed Fox"), svc)
        assert candidates_file(svc) is not None
        return second

    def test_pick_that_is_not_a_number_is_no_candidates(self, svc, listed):
        out = delete_event(DeleteRequest(pick="two"), svc)
        assert out.kind is Kind.NEED_USER
        assert out.reply == NO_CANDIDATES_REPLY

    def test_bare_number_title_with_an_expired_list_asks_again(self, svc, listed):
        svc.clock.sleep(svc.settings.context_window_s + 1)
        out = delete_event(DeleteRequest(title="2"), svc)
        assert out.kind is Kind.NEED_USER
        assert out.reply == NO_CANDIDATES_REPLY
        assert svc.calendar.deleted == []
        assert candidates_file(svc) is None

    def test_bare_number_title_is_a_pick_while_a_list_is_waiting(self, svc, listed):
        out = delete_event(DeleteRequest(title="2"), svc)
        assert out.kind is Kind.OK
        assert svc.calendar.deleted == [("primary", listed["id"])]
        assert candidates_file(svc) is None


class TestById:
    def test_gets_the_event_then_deletes_it(self, svc):
        e = seed_zed_fox(svc)
        queue_add(queue_row(e["id"]), svc)
        out = delete_event(DeleteRequest(id=e["id"]), svc)
        assert out.kind is Kind.OK
        names = [name for name, _ in svc.calendar.calls]
        assert names == ["list_calendars", "get_event", "delete_event"]
        assert svc.calendar.deleted == [("cal-concerts", e["id"])]
        assert out.reply == REMOVED_GOLDEN
        assert queue_list(svc) == []

    def test_unknown_id_is_an_error_outcome(self, svc):
        out = delete_event(DeleteRequest(id="nope"), svc)
        assert out.kind is Kind.ERROR
        assert out.data["provider"] == "gws"


class TestNoneAndInput:
    def test_none_found_names_the_calendar_asked_for(self, svc):
        out = delete_event(DeleteRequest(title="Zed Fox", calendar="Personal"), svc)
        assert out.reply == "I found no event like **Zed Fox** on Personal. Nothing deleted."

    def test_unknown_tz_is_a_fix(self, svc):
        out = delete_event(DeleteRequest(title="Zed Fox", tz="Mars/Olympus"), svc)
        assert out.kind is Kind.FIX
        assert out.error == "unknown tz 'Mars/Olympus'"

    def test_a_crash_inside_becomes_the_crash_reply(self, svc):
        def boom(*args, **kwargs):
            raise RuntimeError("kaboom")

        svc.calendar.list_events = boom
        out = delete_event(DeleteRequest(title="Zed Fox"), svc)
        assert out.kind is Kind.ERROR
        assert out.reply == "Something broke inside the concert tool (RuntimeError). Admin: check the gateway log."
        assert out.error.startswith("RuntimeError('kaboom')")


class TestDryRun:
    def test_dry_run_with_a_match_removes_nothing(self, svc):
        # A dry run never searches, so a match can only come through an id; a calendar id passes through as given.
        cal = "shows@group.calendar.example"
        e = svc.calendar.seed_event(
            cal, summary="Zed Fox", start=datetime(2026, 11, 14, 20, 30, tzinfo=LA), location=QUARRY
        )
        out = delete_event(DeleteRequest(id=e["id"], calendar=cal, dry_run=True), svc)
        assert out.kind is Kind.DRY_RUN
        assert out.reply == (
            f"**Would remove from {cal}**\n"
            "• **Artist:** Zed Fox\n"
            "• **When:** Sat Nov 14, 8:30 PM – 10:30 PM PST\n"
            "• **Where:** The Quarry, San Francisco"
        )
        assert svc.calendar.deleted == []
        assert out.data["deleted"] is False
        assert out.to_json()["dryRun"] is True

    def test_dry_run_with_a_title_looks_without_calling_the_calendar(self, svc):
        seed_zed_fox(svc)
        out = delete_event(DeleteRequest(title="Zed Fox", dry_run=True), svc)
        assert out.kind is Kind.DRY_RUN
        assert out.reply == "**Would look for** Zed Fox on Concerts and Personal, then remove the one match."
        assert svc.calendar.calls == []
        assert svc.calendar.deleted == []


class TestFromMapping:
    def test_unknown_key_is_a_fix(self):
        with pytest.raises(FixInput) as exc:
            DeleteRequest.from_mapping({"title": "X", "foo": 1})
        assert exc.value.error == "unknown keys: foo"
        assert exc.value.fix == "use only: calendar, date, id, location, pick, title, tz"
        assert exc.value.fix == f"use only: {', '.join(sorted(DELETE_KNOWN))}"

    def test_not_an_object_is_a_fix(self):
        with pytest.raises(FixInput) as exc:
            DeleteRequest.from_mapping(["Zed Fox"])  # type: ignore[arg-type]
        assert exc.value.error == "input must be a JSON object"
