"""text/lineup.py: the prompt, the parse of a full, partial and garbage answer, the validator, the lines."""

from __future__ import annotations

import json

import pytest

from concert_mcp.text.lineup import (
    LINEUP_SCHEMA,
    GroundedAnswer,
    Lineup,
    Opener,
    clock_time,
    expected_line,
    latest_setlist_url,
    lineup_lines,
    lineup_prompt,
    merge_lineup_lines,
    opener_setlist_line,
    parse_lineup,
)

FULL = json.dumps(
    {"openers": ["Zork", "Blorp"], "doors": "7:00 PM", "opener_on_stage": "8pm", "headliner_on_stage": "21:00"}
)
PARTIAL = json.dumps({"openers": [], "doors": "7:30 PM", "opener_on_stage": None, "headliner_on_stage": None})
SOURCES = ("https://venue.example/events/zed-fox", "https://tickets.example/zed-fox")
PAGE = "https://www.setlist.fm/setlist/zork/2026/the-hollow-oakland-ca-1a2b3c4d.html"


def test_prompt_names_the_show_the_hint_and_the_shape() -> None:
    text = lineup_prompt("Zed Fox", "2026-11-14", "The Quarry, San Francisco", "https://venue.example")
    assert text.startswith(
        "For the concert by Zed Fox at The Quarry, San Francisco on 2026-11-14: who are the opening acts"
    )
    assert "The venue's website is https://venue.example." in text
    assert "use null for anything not announced" in text
    assert json.dumps(LINEUP_SCHEMA["properties"], separators=(",", ":")) in text
    assert "website" not in lineup_prompt("Zed Fox", "2026-11-14", "The Quarry", None).split("Answer")[
        0
    ].lower().replace("venue's event page", "")


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("7:30 PM", "7:30 PM"),
        ("7pm", "7:00 PM"),
        ("7.30 p.m.", "7:30 PM"),
        ("19:30", "7:30 PM"),
        ("00:15", "12:15 AM"),
        ("12:00 PM", "12:00 PM"),
        ("doors at 8 PM sharp", "8:00 PM"),
        ("not announced", None),
        (None, None),
        ("13 PM", None),
        ("25:00", None),
    ],
)
def test_clock_time(raw: object, want: str | None) -> None:
    assert clock_time(raw) == want


def test_parse_full_answer() -> None:
    lu = parse_lineup(GroundedAnswer(FULL, SOURCES), headliner="Zed Fox", venue="The Quarry, San Francisco")
    assert lu.status == "found"
    assert lu.openers == (Opener("Zork"), Opener("Blorp"))
    assert (lu.doors, lu.opener_time, lu.headliner_time) == ("7:00 PM", "8:00 PM", "9:00 PM")
    assert lu.sources == SOURCES


def test_parse_partial_answer_keeps_only_what_was_given() -> None:
    lu = parse_lineup(GroundedAnswer(PARTIAL), headliner="Zed Fox", venue="The Quarry")
    assert lu.status == "found"
    assert lu.openers == ()
    assert (lu.doors, lu.opener_time, lu.headliner_time) == ("7:30 PM", None, None)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "I could not find anything about this show.",
        '```json\n{"openers": [], "doors": null, "opener_on_stage": null, "headliner_on_stage": null}\n```',
        '{"openers": "Zork"}',
        "[1, 2, 3]",
        '{"openers": ["Zed Fox", "The Quarry", "DICE", "TBA", ""], "doors": "soon"}',
    ],
)
def test_garbage_or_empty_answers_are_none_never_an_error(text: str) -> None:
    lu = parse_lineup(GroundedAnswer(text, SOURCES), headliner="Zed Fox", venue="The Quarry, San Francisco")
    assert lu == Lineup("none", sources=SOURCES)


def test_code_fences_and_prose_around_the_object_are_ignored() -> None:
    text = "Here is what I found:\n```json\n" + FULL + "\n```\nSources: the venue page."
    lu = parse_lineup(GroundedAnswer(text), headliner="Zed Fox", venue="The Quarry")
    assert lu.status == "found" and [o.name for o in lu.openers] == ["Zork", "Blorp"]


def test_validator_drops_headliner_venue_platform_and_duplicates_and_caps_at_three() -> None:
    raw = {"openers": ["zed fox", "The Quarry", "Ticketweb", "Zork", "ZORK", "Blorp", "Quux", "Fifth"]}
    lu = parse_lineup(GroundedAnswer(json.dumps(raw)), headliner="Zed Fox", venue="The Quarry, San Francisco")
    assert [o.name for o in lu.openers] == ["Zork", "Blorp", "Quux"]


def test_expected_line_only_from_the_times_given() -> None:
    full = Lineup("found", openers=(Opener("Zork"),), doors="7:00 PM", opener_time="8:00 PM", headliner_time="9:00 PM")
    assert expected_line(full, "Zed Fox") == "Expected: doors 7:00 PM, Zork 8:00 PM, Zed Fox 9:00 PM"
    assert expected_line(Lineup("found", doors="7:00 PM"), "Zed Fox") == "Expected: doors 7:00 PM"
    assert expected_line(Lineup("found", opener_time="8:00 PM"), "Zed Fox") == "Expected: opener 8:00 PM"
    assert expected_line(Lineup("found", openers=(Opener("Zork"),)), "Zed Fox") is None


def test_opener_setlist_line_one_or_several() -> None:
    assert opener_setlist_line(Lineup("found", openers=(Opener("Zork", PAGE),))) == f"Opener setlist (recent): {PAGE}"
    two = Lineup("found", openers=(Opener("Zork", PAGE), Opener("Blorp", None)))
    assert opener_setlist_line(two) == f"Opener setlist (recent): Zork {PAGE}"
    assert opener_setlist_line(Lineup("found", openers=(Opener("Zork"),))) is None


def test_lineup_lines_with_and_without_known_openers() -> None:
    lu = Lineup("found", openers=(Opener("Zork", PAGE),), doors="7:00 PM", headliner_time="9:00 PM")
    assert lineup_lines(lu, "Zed Fox", openers_known=False) == [
        "Headliner: Zed Fox",
        "Opener: Zork",
        "Expected: doors 7:00 PM, Zed Fox 9:00 PM",
        f"Opener setlist (recent): {PAGE}",
    ]
    assert lineup_lines(lu, "Zed Fox", openers_known=True) == [
        "Expected: doors 7:00 PM, Zed Fox 9:00 PM",
        f"Opener setlist (recent): {PAGE}",
    ]
    assert lineup_lines(Lineup("none"), "Zed Fox", openers_known=False) == []
    assert lineup_lines(Lineup("unavailable"), "Zed Fox", openers_known=False) == []


def test_merge_lineup_lines_appends_only_the_keys_the_description_lacks() -> None:
    old = "Doors: 7:30 PM\nHeadliner: Zed Fox\nOpener: Zork.\nTeaser playlist: https://open.spotify.com/playlist/pl1"
    merged, added = merge_lineup_lines(old, ["Headliner: Zed Fox", "Opener: Zork", "Expected: doors 7:30 PM"])
    assert added == ["Expected: doors 7:30 PM"]
    assert merged == old.splitlines() + ["Expected: doors 7:30 PM"]
    assert merge_lineup_lines(None, ["Expected: doors 7:30 PM"]) == (
        ["Expected: doors 7:30 PM"],
        ["Expected: doors 7:30 PM"],
    )
    assert merge_lineup_lines(old, []) == (old.splitlines(), [])


def test_latest_setlist_url_prefers_a_page_with_songs() -> None:
    empty = {"id": "e", "url": "https://www.setlist.fm/setlist/zork/2026/empty-1.html", "sets": {"set": []}}
    full = {"id": "f", "url": PAGE, "sets": {"set": [{"song": [{"name": "A"}]}]}}
    assert latest_setlist_url({"setlist": [empty, full]}) == PAGE
    assert latest_setlist_url({"setlist": [empty]}) == empty["url"]
    assert latest_setlist_url({"setlist": []}) is None
    assert latest_setlist_url(None) is None
