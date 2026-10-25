"""text/titles.py: opener split, matching, normalisation, small-model parameter cleaning."""

from __future__ import annotations

import pytest

from concert_mcp.text.titles import (
    SplitTitle,
    clean_param,
    looks_like_pick,
    normalize_name,
    split_opener_list,
    split_openers,
    squash,
    strip_concert_prefix,
    title_matches,
)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Zed Fox with special guest Zork.", SplitTitle("Zed Fox", ("Zork.",))),
        ("Zed Fox with special guests Zork. and Quux", SplitTitle("Zed Fox", ("Zork.", "Quux"))),
        ("Ab w/ B & C", SplitTitle("Ab", ("B", "C"))),
        ("Xy + Zed", SplitTitle("Xy", ("Zed",))),
        ("Headliner featuring Someone", SplitTitle("Headliner", ("Someone",))),
        ("Headliner feat. Someone", SplitTitle("Headliner", ("Someone",))),
        ("Headliner feat Someone", SplitTitle("Headliner", ("Someone",))),
        ("Artist with support from X, Y and Z", SplitTitle("Artist", ("X", "Y", "Z"))),
        ("Artist with A / B", SplitTitle("Artist", ("A", "B"))),
        ("Zed Fox", SplitTitle("Zed Fox", ())),
    ],
)
def test_split_openers(title: str, expected: SplitTitle) -> None:
    assert split_openers(title) == expected


def test_split_openers_keeps_a_one_letter_head_whole() -> None:
    assert split_openers("A w/ B") == SplitTitle("A w/ B", ())


def test_split_opener_list() -> None:
    assert split_opener_list("X, Y and Z & W / V") == ["X", "Y", "Z", "W", "V"]
    assert split_opener_list("  ,  ") == []
    assert split_opener_list("Band of Horses") == ["Band of Horses"]


def test_strip_concert_prefix() -> None:
    assert strip_concert_prefix("[Concert] X") == "X"
    assert strip_concert_prefix("[concert]X") == "X"
    assert strip_concert_prefix("X [Concert]") == "X [Concert]"
    assert strip_concert_prefix("[Dinner] ZZZ Bistro") == "[Dinner] ZZZ Bistro"


def test_title_matches() -> None:
    assert title_matches("zed fox", "ZED FOX & Friends")
    assert title_matches("zed a fox", "Zed Fox")  # single-letter words ignored
    assert title_matches("Zed Fox and Friends", "Zed Fox & Friends")  # & vs and
    assert title_matches("Fox", "Zed Fox")  # substring
    assert title_matches("", "anything")
    assert title_matches(None, "anything")  # type: ignore[arg-type]
    assert not title_matches("Zed Fox", "Zork.")
    assert not title_matches("Zed Fox Zork", "Zed Fox")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Zed Fox & Friends", "zed fox and friends"),
        ("  ZED   FOX ", "zed fox"),
        ("A&B", "aandb"),
        (None, ""),
        ("", ""),
        (42, "42"),
    ],
)
def test_normalize_name(raw: object, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_squash() -> None:
    assert squash("  Zed \n  Fox ") == "Zed Fox"
    assert squash(None) == ""
    assert squash(7) == "7"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ""),
        ("", ""),
        ("null", ""),
        ("None", ""),
        ("NULL", ""),
        ("n/a", ""),
        ("undefined", ""),
        ('"Zed Fox"', "Zed Fox"),
        ("'Zed Fox'", "Zed Fox"),
        ("  Zed   Fox ", "Zed Fox"),
        ('"null"', ""),
        ('"Zed" Fox', '"Zed" Fox'),  # quotes that do not wrap the whole value stay
        ('"', '"'),
        ("2026-11-14 8:30 PM", "2026-11-14 8:30 PM"),
    ],
)
def test_clean_param(raw: object, expected: str) -> None:
    assert clean_param(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2", True),
        ("2.", True),
        (" 12 ", True),
        ("two", False),
        ("123", False),
        ("", False),
        ("2 please", False),
        ("2026-11-14", False),
    ],
)
def test_looks_like_pick(raw: str, expected: bool) -> None:
    assert looks_like_pick(raw) is expected
