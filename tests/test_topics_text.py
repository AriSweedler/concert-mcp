"""text/topics.py: the topic key, the carried lines and the changed-fields note."""

from __future__ import annotations

from datetime import datetime

from concert_mcp.text.topics import TopicEntry, carry_lines, changed_fields, ordered_lines, topic_key
from conftest import LA
from conftest import SHOW as START


def test_topics_text() -> None:
    # topic_key_is_chat_colon_thread:
    assert topic_key("-1001234567890", "7") == "-1001234567890:7"

    # entry_round_trips_and_tolerates_missing_keys:
    e = TopicEntry("evt1", "cal", "Zed Fox", "2026-11-14", "2026-10-26T10:00:00-07:00")
    assert TopicEntry.from_json(e.to_json()) == e
    assert TopicEntry.from_json({}) == TopicEntry("", "", "", "", "")


class TestOrderedLines:
    def test_partiful_then_timing_then_links_then_the_rest(self):
        # The order a real event had before 2026-11-14, with every kind of line in it.
        lines = [
            "Headliner: Zed Fox",
            "Purchased: 2 tickets, $42.00 on Ticketweb",
            "Teaser playlist: https://t",
            "Expected: doors 6:00 PM, Zork 7:00 PM, Zed Fox 8:00 PM",
            "Opener: Zork",
            "Tickets: https://tix",
            "Opener setlist (recent): https://o",
            "Partiful: https://p",
            "setlist.fm: https://s",
            "Doors: 6:00 PM",
            "Setlist: https://old",
            "Opener setlist (recent): A https://a; B https://b",
            "Setlist playlist: https://sp",
            "Venue: https://v",
        ]
        assert ordered_lines(lines) == [
            "Partiful: https://p",
            "Doors: 6:00 PM",
            "Expected: doors 6:00 PM, Zork 7:00 PM, Zed Fox 8:00 PM",
            "Teaser playlist: https://t",
            "Tickets: https://tix",
            "Opener setlist (recent): https://o",
            "setlist.fm: https://s",
            "Setlist: https://old",
            "Setlist playlist: https://sp",
            "Venue: https://v",
            "Headliner: Zed Fox",
            "Purchased: 2 tickets, $42.00 on Ticketweb",
            "Opener: Zork",
            "Opener setlist (recent): A https://a; B https://b",
        ]

    def test_a_description_already_in_order_and_an_empty_one_are_unchanged(self):
        assert ordered_lines([]) == []
        lines = ["Doors: 7:30 PM", "Tickets: https://tix", "Purchased: 2 tickets"]
        assert ordered_lines(lines) == lines and ordered_lines(ordered_lines(lines)) == lines


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
