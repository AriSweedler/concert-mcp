"""flows/queue.py: the setlist queue file the weekly poller reads."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from concert_mcp.flows.queue import (
    QueueEntry,
    make_entry,
    queue_add,
    queue_forget,
    queue_list,
    queue_outcome,
    queue_status,
)
from concert_mcp.result import Kind
from concert_mcp.text.setlists import Setlist, SetlistResult

LA = ZoneInfo("America/Los_Angeles")
START = datetime(2026, 11, 14, 20, 30, tzinfo=LA)
LEGACY_KEYS = ["event_id", "calendar_id", "artist", "date", "venue", "setlistfm_url", "song_count", "until", "added"]


def entry(event_id: str = "evt1", *, date: str = "2026-11-14", until: str = "2027-01-14") -> QueueEntry:
    return QueueEntry(
        event_id=event_id,
        calendar_id="cal-concerts",
        artist="Zed Fox",
        date=date,
        venue="The Quarry, San Francisco",
        setlistfm_url=None,
        song_count=0,
        until=until,
        added="2026-10-25T19:00:00+00:00",
    )


def found_setlist() -> SetlistResult:
    return SetlistResult(
        found=True,
        match="exact date",
        artist="Zed Fox",
        date="2026-11-14",
        venue="The Quarry",
        setlist=Setlist(
            id="abc",
            url="https://www.setlist.fm/setlist/zed-fox/2026/the-quarry-san-francisco-ca-abc.html",
            artist="Zed Fox",
            date="2026-11-14",
            venue="The Quarry",
            city="San Francisco",
            tour=None,
            song_count=12,
            songs=tuple(f"Song {i}" for i in range(12)),
        ),
    )


class TestQueueEntry:
    def test_to_json_has_the_legacy_keys_in_order(self):
        assert list(entry().to_json()) == LEGACY_KEYS

    def test_from_json_round_trips(self):
        e = entry("evt9")
        assert QueueEntry.from_json(e.to_json()) == e

    def test_from_json_tolerates_missing_keys(self):
        e = QueueEntry.from_json({"event_id": "evt1"})
        assert e.event_id == "evt1"
        assert e.artist == "" and e.until == "" and e.song_count == 0 and e.setlistfm_url is None

    def test_from_json_tolerates_a_bad_song_count(self):
        assert QueueEntry.from_json({"event_id": "evt1", "song_count": "many"}).song_count == 0


class TestMakeEntry:
    def test_until_is_days_after_the_show_and_added_is_now(self, now):
        e = make_entry(
            event_id="evt1",
            calendar_id="cal-concerts",
            artist="Zed Fox",
            start=START,
            venue="The Quarry, San Francisco",
            setlist=None,
            now=now,
            days=61,
        )
        assert e.date == "2026-11-14"
        assert e.until == "2027-01-14"
        assert e.added == now.isoformat(timespec="seconds")
        assert e.setlistfm_url is None and e.song_count == 0

    def test_setlist_fields_come_from_a_found_result(self, now):
        e = make_entry(
            event_id="evt1",
            calendar_id="cal-concerts",
            artist="Zed Fox",
            start=START,
            venue="The Quarry",
            setlist=found_setlist(),
            now=now,
            days=61,
        )
        assert e.setlistfm_url == found_setlist().setlist.url
        assert e.song_count == 12

    def test_a_not_found_result_leaves_the_setlist_fields_empty(self, now):
        res = SetlistResult(found=False, match="none", artist="Zed Fox", date="2026-11-14", venue=None)
        e = make_entry(
            event_id="evt1", calendar_id="c", artist="Zed Fox", start=START, venue="v", setlist=res, now=now, days=1
        )
        assert e.setlistfm_url is None and e.song_count == 0
        assert e.until == "2026-11-15"


class TestQueueAdd:
    def test_appends_and_writes_a_json_list(self, svc):
        assert queue_add(entry("evt1"), svc) is True
        stored = svc.state.read_json(svc.settings.setlist_queue, None)
        assert isinstance(stored, list)
        assert stored == [entry("evt1").to_json()]
        # The file is a JSON list that pretty-prints: what the legacy wrote with indent=2 and the poller parses.
        assert json.loads(json.dumps(stored, indent=2)) == stored

    def test_is_idempotent_per_event_id(self, svc):
        assert queue_add(entry("evt1"), svc) is True
        assert queue_add(entry("evt1", until="2099-01-01"), svc) is False
        assert queue_list(svc) == [entry("evt1")]

    def test_different_events_are_both_kept(self, svc):
        queue_add(entry("evt1"), svc)
        queue_add(entry("evt2"), svc)
        assert [e.event_id for e in queue_list(svc)] == ["evt1", "evt2"]

    def test_rows_it_cannot_read_are_carried_over(self, svc):
        svc.state.write_json(svc.settings.setlist_queue, ["garbage", {"event_id": "old"}])
        queue_add(entry("evt1"), svc)
        stored = svc.state.read_json(svc.settings.setlist_queue, None)
        assert stored[0] == "garbage"
        assert [r["event_id"] for r in stored[1:]] == ["old", "evt1"]


class TestQueueList:
    def test_empty_when_the_file_is_absent(self, svc):
        assert queue_list(svc) == []

    def test_skips_rows_that_are_not_objects(self, svc):
        svc.state.write_json(svc.settings.setlist_queue, [entry("evt1").to_json(), 42, None, "x"])
        assert [e.event_id for e in queue_list(svc)] == ["evt1"]

    def test_a_file_that_is_not_a_list_reads_as_empty(self, svc):
        svc.state.write_json(svc.settings.setlist_queue, {"event_id": "evt1"})
        assert queue_list(svc) == []


class TestQueueForget:
    def test_removes_the_row_and_writes(self, svc):
        queue_add(entry("evt1"), svc)
        queue_add(entry("evt2"), svc)
        assert queue_forget("evt1", svc) is True
        assert [e.event_id for e in queue_list(svc)] == ["evt2"]

    def test_false_when_nothing_matched(self, svc):
        queue_add(entry("evt1"), svc)
        before = svc.state.mtimes.get(svc.settings.setlist_queue)
        assert queue_forget("nope", svc) is False
        assert svc.state.mtimes.get(svc.settings.setlist_queue) == before

    def test_false_on_an_absent_file(self, svc):
        assert queue_forget("evt1", svc) is False
        assert svc.state.read_json(svc.settings.setlist_queue, None) is None


class TestQueueStatus:
    def test_splits_active_and_expired_at_today_in_the_configured_zone(self, svc):
        # now is 2026-10-25 12:00 -07:00: today in America/Los_Angeles is 2026-10-25.
        queue_add(entry("today", date="2026-08-25", until="2026-10-25"), svc)
        queue_add(entry("yesterday", date="2026-08-24", until="2026-10-24"), svc)
        queue_add(entry("later", date="2026-11-14", until="2027-01-14"), svc)
        st = queue_status(svc)
        assert [e.event_id for e in st.active] == ["today", "later"]
        assert [e.event_id for e in st.expired] == ["yesterday"]
        assert st.path == svc.settings.setlist_queue

    def test_entries_are_sorted_by_date(self, svc):
        queue_add(entry("b", date="2026-12-01", until="2027-01-31"), svc)
        queue_add(entry("a", date="2026-11-01", until="2027-01-01"), svc)
        assert [e.event_id for e in queue_status(svc).entries] == ["a", "b"]

    def test_an_unreadable_until_counts_as_expired(self, svc):
        queue_add(entry("odd", until="someday"), svc)
        st = queue_status(svc)
        assert st.active == ()
        assert [e.event_id for e in st.expired] == ["odd"]

    def test_empty_queue(self, svc):
        st = queue_status(svc)
        assert st.entries == () and st.active == () and st.expired == ()


class TestQueueOutcome:
    def test_empty_queue_reply(self, svc):
        out = queue_outcome(svc)
        assert out.kind is Kind.OK
        assert out.reply == "The setlist queue is empty."
        assert out.data == {"count": 0, "entries": []}

    def test_reply_lists_every_show_and_data_carries_the_rows(self, svc):
        queue_add(entry("evt1"), svc)
        out = queue_outcome(svc)
        assert out.kind is Kind.OK
        assert out.data["count"] == 1
        assert out.data["entries"] == [entry("evt1").to_json()]
        assert out.reply.startswith("**Setlist queue** (1 shows)")
        assert "2026-11-14 Zed Fox @ The Quarry, San Francisco" in out.reply
        assert "checking until 2027-01-14" in out.reply
