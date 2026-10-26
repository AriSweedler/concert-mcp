"""text/topics.py: the topic key, the carried lines and the changed-fields note."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from concert_mcp.text.topics import TopicEntry, carry_lines, changed_fields, topic_key

LA = ZoneInfo("America/Los_Angeles")
START = datetime(2026, 11, 14, 20, 30, tzinfo=LA)


def test_topic_key_is_chat_colon_thread():
    assert topic_key("-1001234567890", "7") == "-1001234567890:7"


def test_entry_round_trips_and_tolerates_missing_keys():
    e = TopicEntry("evt1", "cal", "Zed Fox", "2026-11-14", "2026-10-26T10:00:00-07:00")
    assert TopicEntry.from_json(e.to_json()) == e
    assert TopicEntry.from_json({}) == TopicEntry("", "", "", "", "")


class TestCarryLines:
    def test_keeps_link_lines_the_new_text_lacks(self):
        old = "Doors: 7:30 PM\nTeaser playlist: https://t\nSetlist: https://s\nVenue: https://v"
        assert carry_lines(old, ["Doors: 8:00 PM"]) == [
            "Doors: 8:00 PM",
            "Teaser playlist: https://t",
            "Setlist: https://s",
            "Venue: https://v",
        ]

    def test_a_new_link_line_wins_over_the_old_one(self):
        old = "Teaser playlist: https://old"
        assert carry_lines(old, ["Teaser playlist: https://new"]) == ["Teaser playlist: https://new"]

    def test_ticket_lines_are_never_carried(self):
        assert carry_lines("Doors: 7:30 PM\nPurchased: $20.00\nOpener: X", ["Doors: 8 PM"]) == ["Doors: 8 PM"]

    def test_no_old_description(self):
        assert carry_lines(None, ["A: b"]) == ["A: b"]


class TestChangedFields:
    def event(self, **over):
        e = {
            "summary": "DICE",
            "start": {"dateTime": START.isoformat()},
            "location": "Cafe Du Nord, San Francisco",
        }
        e.update(over)
        return e

    def test_title_change_names_both(self):
        assert changed_fields(self.event(), summary="Zed Fox", start=START, location="Cafe Du Nord, San Francisco") == [
            "title DICE -> Zed Fox"
        ]

    def test_same_values_change_nothing(self):
        assert changed_fields(self.event(), summary="DICE", start=START, location="Cafe Du Nord, San Francisco") == []

    def test_time_and_place(self):
        later = datetime(2026, 11, 14, 21, 0, tzinfo=LA)
        assert changed_fields(self.event(), summary="DICE", start=later, location="Elsewhere") == ["when", "where"]

    def test_an_all_day_event_counts_as_a_time_change(self):
        e = self.event(start={"date": "2026-11-14"})
        assert changed_fields(e, summary="DICE", start=START, location="Cafe Du Nord, San Francisco") == ["when"]
