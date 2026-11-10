"""text/titles.py: opener split, matching, normalisation, small-model parameter cleaning."""

from __future__ import annotations

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


def test_titles() -> None:
    # split_openers:
    for title, expected in [
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
    ]:
        assert split_openers(title) == expected

    # split_openers_keeps_a_one_letter_head_whole:
    assert split_openers("A w/ B") == SplitTitle("A w/ B", ())

    # split_opener_list:
    assert split_opener_list("X, Y and Z & W / V") == ["X", "Y", "Z", "W", "V"]
    assert split_opener_list("  ,  ") == []
    assert split_opener_list("Band of Horses") == ["Band of Horses"]

    # strip_concert_prefix:
    assert strip_concert_prefix("[Concert] X") == "X"
    assert strip_concert_prefix("[concert]X") == "X"
    assert strip_concert_prefix("X [Concert]") == "X [Concert]"
    assert strip_concert_prefix("[Dinner] ZZZ Bistro") == "[Dinner] ZZZ Bistro"

    # title_matches:
    assert title_matches("zed fox", "ZED FOX & Friends")
    assert title_matches("zed a fox", "Zed Fox")  # single-letter words ignored
    assert title_matches("Zed Fox and Friends", "Zed Fox & Friends")  # & vs and
    assert title_matches("Fox", "Zed Fox")  # substring
    assert title_matches("", "anything")
    assert title_matches(None, "anything")  # type: ignore[arg-type]
    assert not title_matches("Zed Fox", "Zork.")
    assert not title_matches("Zed Fox Zork", "Zed Fox")

    # normalize_name:
    for raw, expected in [
        ("Zed Fox & Friends", "zed fox and friends"),
        ("  ZED   FOX ", "zed fox"),
        ("A&B", "aandb"),
        (None, ""),
        ("", ""),
        (42, "42"),
    ]:
        assert normalize_name(raw) == expected

    # squash:
    assert squash("  Zed \n  Fox ") == "Zed Fox"
    assert squash(None) == ""
    assert squash(7) == "7"

    # clean_param:
    for raw, expected in [
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
    ]:
        assert clean_param(raw) == expected

    # looks_like_pick:
    for raw, expected in [
        ("2", True),
        ("2.", True),
        (" 12 ", True),
        ("two", False),
        ("123", False),
        ("", False),
        ("2 please", False),
        ("2026-11-14", False),
    ]:
        assert looks_like_pick(raw) is expected
