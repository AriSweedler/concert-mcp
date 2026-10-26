"""flows/event_delete.py end to end on the fakes, with the legacy cal-del.py replies as goldens."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from concert_mcp.flows.event_delete import DELETE_KNOWN, DeleteRequest, delete_event, find_candidates
from concert_mcp.flows.queue import QueueEntry, queue_add, queue_list
from concert_mcp.flows.topics import topic_event, topic_remember
from concert_mcp.result import FixInput, Kind
from concert_mcp.text.reply import NO_CANDIDATES_REPLY, WHICH_EVENT_REPLY
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

    def test_searches_concerts_then_the_default_calendar(self, svc):
        seed_zed_fox(svc)
        delete_event(DeleteRequest(title="Zed Fox"), svc)
        listed = [args["calendar_id"] for name, args in svc.calendar.calls if name == "list_events"]
        assert listed == ["cal-concerts", "primary"]
        assert all(args["q"] == "Zed Fox" for name, args in svc.calendar.calls if name == "list_events")

    def test_a_date_narrows_the_window_to_the_day_give_or_take(self, svc):
        seed_zed_fox(svc)
        out = delete_event(DeleteRequest(title="Zed Fox", date="2026-11-14"), svc)
        assert out.kind is Kind.OK and out.data["deleted"] is True
        _, args = next(c for c in svc.calendar.calls if c[0] == "list_events")
        assert args["time_min"] == datetime(2026, 11, 13, tzinfo=LA)
        assert args["time_max"] == datetime(2026, 11, 16, tzinfo=LA)

    def test_a_date_two_days_off_finds_nothing(self, svc):
        seed_zed_fox(svc)
        out = delete_event(DeleteRequest(title="Zed Fox", date="2026-11-17"), svc)
        assert out.data == {"deleted": False}
        assert svc.calendar.deleted == []

    def test_word_matching_finds_a_longer_title(self, svc):
        svc.calendar.seed_event(
            "cal-concerts", summary="ZED FOX & Friends", start=datetime(2026, 11, 14, 20, 30, tzinfo=LA)
        )
        out = delete_event(DeleteRequest(title="zed fox"), svc)
        assert out.kind is Kind.OK
        assert out.data["summary"] == "ZED FOX & Friends"
        assert len(svc.calendar.deleted) == 1

    def test_cancelled_events_are_skipped(self, svc):
        svc.calendar.seed_event(
            "cal-concerts", summary="Zed Fox", start=datetime(2026, 11, 14, 20, 30, tzinfo=LA), status="cancelled"
        )
        out = delete_event(DeleteRequest(title="Zed Fox"), svc)
        assert out.data == {"deleted": False}
        assert svc.calendar.deleted == []

    def test_a_named_calendar_is_the_only_one_searched(self, svc):
        seed_second(svc)
        out = delete_event(DeleteRequest(title="Zed Fox", calendar="Personal"), svc)
        assert out.kind is Kind.OK
        assert svc.calendar.deleted == [("primary", "evt1")]
        listed = [args["calendar_id"] for name, args in svc.calendar.calls if name == "list_events"]
        assert listed == ["primary"]

    def test_delete_refused_by_the_calendar_is_an_error_outcome(self, svc):
        seed_zed_fox(svc)
        svc.calendar.fail = "delete_event"
        out = delete_event(DeleteRequest(title="Zed Fox"), svc)
        assert out.kind is Kind.ERROR
        assert out.reply == "Google Calendar refused the request. Admin: check `gws auth` on the host."
        assert out.data["provider"] == "gws"


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

    def test_candidates_are_sorted_by_start_across_calendars(self, svc):
        second = seed_second(svc)
        first = seed_zed_fox(svc)
        out = delete_event(DeleteRequest(title="Zed Fox"), svc)
        assert [c["id"] for c in out.data["candidates"]] == [first["id"], second["id"]]


class TestPick:
    @pytest.fixture
    def listed(self, svc):
        seed_zed_fox(svc)
        second = seed_second(svc)
        delete_event(DeleteRequest(title="Zed Fox"), svc)
        assert candidates_file(svc) is not None
        return second

    def test_pick_deletes_that_one_and_clears_the_file(self, svc, listed):
        out = delete_event(DeleteRequest(pick="2"), svc)
        assert out.kind is Kind.OK
        assert svc.calendar.deleted == [("primary", listed["id"])]
        assert out.reply.startswith("**Removed from Personal**\n• **Artist:** Zed Fox\n")
        assert candidates_file(svc) is None
        assert out.data["deleted"] is True and out.data["id"] == listed["id"]

    def test_pick_with_a_trailing_dot_is_accepted(self, svc, listed):
        out = delete_event(DeleteRequest(pick="2."), svc)
        assert out.kind is Kind.OK
        assert svc.calendar.deleted == [("primary", listed["id"])]

    def test_pick_out_of_range_is_no_candidates(self, svc, listed):
        out = delete_event(DeleteRequest(pick="9"), svc)
        assert out.kind is Kind.NEED_USER
        assert out.reply == NO_CANDIDATES_REPLY
        assert out.error == "no such candidate"
        assert svc.calendar.deleted == []

    def test_pick_that_is_not_a_number_is_no_candidates(self, svc, listed):
        out = delete_event(DeleteRequest(pick="two"), svc)
        assert out.kind is Kind.NEED_USER
        assert out.reply == NO_CANDIDATES_REPLY

    def test_pick_from_an_expired_list_is_no_candidates_and_removes_it(self, svc, listed):
        # The list is one context window old: an explicit pick must not delete from it.
        svc.clock.sleep(svc.settings.context_window_s + 1)
        out = delete_event(DeleteRequest(pick="2"), svc)
        assert out.kind is Kind.NEED_USER
        assert out.reply == NO_CANDIDATES_REPLY
        assert svc.calendar.deleted == []
        assert candidates_file(svc) is None

    def test_bare_number_title_with_an_expired_list_asks_again(self, svc, listed):
        svc.clock.sleep(svc.settings.context_window_s + 1)
        out = delete_event(DeleteRequest(title="2"), svc)
        assert out.kind is Kind.NEED_USER
        assert out.reply == NO_CANDIDATES_REPLY
        assert svc.calendar.deleted == []
        assert candidates_file(svc) is None

    def test_pick_within_the_window_still_works(self, svc, listed):
        svc.clock.sleep(svc.settings.context_window_s - 1)
        out = delete_event(DeleteRequest(pick="2"), svc)
        assert out.kind is Kind.OK
        assert svc.calendar.deleted == [("primary", listed["id"])]

    def test_pick_with_no_file_is_no_candidates(self, svc):
        out = delete_event(DeleteRequest(pick="1"), svc)
        assert out.kind is Kind.NEED_USER
        assert out.reply == NO_CANDIDATES_REPLY
        assert svc.calendar.calls == []

    def test_bare_number_title_is_a_pick_while_a_list_is_waiting(self, svc, listed):
        out = delete_event(DeleteRequest(title="2"), svc)
        assert out.kind is Kind.OK
        assert svc.calendar.deleted == [("primary", listed["id"])]
        assert candidates_file(svc) is None

    def test_bare_number_title_without_a_list_is_searched_as_a_title(self, svc):
        out = delete_event(DeleteRequest(title="2"), svc)
        assert out.kind is Kind.OK
        assert out.reply == "I found no event like **2** on Concerts or Personal. Nothing deleted."
        assert any(args["q"] == "2" for name, args in svc.calendar.calls if name == "list_events")

    def test_pick_in_dry_run_deletes_nothing_but_clears_the_list(self, svc, listed):
        out = delete_event(DeleteRequest(pick="2", dry_run=True), svc)
        assert out.kind is Kind.DRY_RUN
        assert out.reply.startswith("**Would remove from Personal**")
        assert svc.calendar.deleted == []
        assert out.data["deleted"] is False


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

    def test_id_on_a_named_calendar(self, svc):
        e = seed_second(svc)
        out = delete_event(DeleteRequest(id=e["id"], calendar="Personal"), svc)
        assert out.kind is Kind.OK
        assert svc.calendar.deleted == [("primary", e["id"])]
        assert out.data["calendar"] == "Personal"

    def test_unknown_id_is_an_error_outcome(self, svc):
        out = delete_event(DeleteRequest(id="nope"), svc)
        assert out.kind is Kind.ERROR
        assert out.data["provider"] == "gws"


class TestNoneAndInput:
    def test_empty_title_asks_which_event(self, svc):
        out = delete_event(DeleteRequest(title="   "), svc)
        assert out.kind is Kind.NEED_USER
        assert out.reply == WHICH_EVENT_REPLY
        assert out.error == "title is required"
        assert svc.calendar.calls == []

    def test_none_found_with_a_date(self, svc):
        out = delete_event(DeleteRequest(title="Zed Fox", date="2026-11-14"), svc)
        assert out.kind is Kind.OK
        assert (
            out.reply == "I found no event like **Zed Fox** around 2026-11-14 on Concerts or Personal. Nothing deleted."
        )
        assert out.data == {"deleted": False}

    def test_none_found_without_a_date(self, svc):
        out = delete_event(DeleteRequest(title="Zed Fox"), svc)
        assert out.reply == "I found no event like **Zed Fox** on Concerts or Personal. Nothing deleted."

    def test_none_found_names_the_calendar_asked_for(self, svc):
        out = delete_event(DeleteRequest(title="Zed Fox", calendar="Personal"), svc)
        assert out.reply == "I found no event like **Zed Fox** on Personal. Nothing deleted."

    def test_bad_date_is_a_fix(self, svc):
        out = delete_event(DeleteRequest(title="Zed Fox", date="next friday"), svc)
        assert out.kind is Kind.FIX
        assert out.error == "date 'next friday' is not YYYY-MM-DD"
        assert out.fix == 'send the day as "2026-10-24"'
        assert svc.calendar.calls == []

    def test_unknown_tz_is_a_fix(self, svc):
        out = delete_event(DeleteRequest(title="Zed Fox", tz="Mars/Olympus"), svc)
        assert out.kind is Kind.FIX
        assert out.error == "unknown tz 'Mars/Olympus'"

    def test_unknown_calendar_is_a_fix(self, svc):
        out = delete_event(DeleteRequest(title="Zed Fox", calendar="Work"), svc)
        assert out.kind is Kind.FIX
        assert out.error == "calendar 'Work' matched 0 calendars"
        assert out.fix == "use one of: Concerts, Personal"

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

    def test_dry_run_with_a_date_says_around(self, svc):
        out = delete_event(DeleteRequest(title="Zed Fox", date="2026-11-14", dry_run=True), svc)
        assert out.reply == (
            "**Would look for** Zed Fox around 2026-11-14 on Concerts and Personal, then remove the one match."
        )

    def test_find_candidates_in_dry_run_is_empty_without_calendar_calls(self, svc):
        seed_zed_fox(svc)
        assert find_candidates(DeleteRequest(title="Zed Fox", dry_run=True), svc) == []
        assert svc.calendar.calls == []


class TestFromMapping:
    def test_aliases_fold(self):
        req = DeleteRequest.from_mapping(
            {"artist": "Zed Fox", "when": "2026-11-14", "cal": "Concerts", "eventId": "e1", "number": "2"}
        )
        assert req == DeleteRequest(title="Zed Fox", date="2026-11-14", calendar="Concerts", id="e1", pick="2")

    def test_more_aliases(self):
        assert DeleteRequest.from_mapping({"event": "X"}).title == "X"
        assert DeleteRequest.from_mapping({"name": "X"}).title == "X"
        assert DeleteRequest.from_mapping({"summary": "X"}).title == "X"
        assert DeleteRequest.from_mapping({"day": "2026-01-01"}).date == "2026-01-01"
        assert DeleteRequest.from_mapping({"choice": "3"}).pick == "3"

    def test_empty_strings_become_none_except_title(self):
        req = DeleteRequest.from_mapping({"title": "", "date": "", "calendar": "", "id": "", "pick": "", "tz": ""})
        assert req == DeleteRequest(title="")

    def test_location_is_accepted_and_ignored(self):
        req = DeleteRequest.from_mapping({"title": "X", "venue": "The Quarry"})
        assert req.title == "X"

    def test_dry_run_is_carried(self):
        assert DeleteRequest.from_mapping({"title": "X"}, dry_run=True).dry_run is True

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
