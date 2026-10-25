"""text/derive.py: derived description lines and the hallucination filter."""

from __future__ import annotations

import pytest

from concert_mcp.result import FixInput
from concert_mcp.text.derive import (
    build_source,
    check_attendees,
    coerce_description,
    derived_lines,
    merge_lines,
    norm_claims,
    openers_from_text,
    verify_line,
)

# ---- derived_lines ---------------------------------------------------------------------------------------------------


def test_derived_lines_on_the_ticket(ticket_text: str) -> None:
    assert derived_lines(ticket_text) == ["Doors: 7:30 PM", "Purchased: 2 tickets, $42.00 on Ticketweb"]


@pytest.mark.parametrize(
    ("text", "doors"),
    [
        ("Doors 7.30 p.m.", "Doors: 7:30 PM"),
        ("doors open 7pm", "Doors: 7 PM"),
        ("Doors: 7:00PM", "Doors: 7:00 PM"),
        ("Door @ 8 pm", "Doors: 8 PM"),
        ("Doors at 7:30 P.M. sharp", "Doors: 7:30 PM"),
    ],
)
def test_derived_lines_doors_normalisation(text: str, doors: str) -> None:
    assert derived_lines(text) == [doors]


def test_derived_lines_purchased_forms() -> None:
    assert derived_lines("1 ticket $45") == ["Purchased: 1 ticket, $45"]
    assert derived_lines("2 x tickets, $90 on Ticketmaster") == ["Purchased: 2 tickets, $90 on Ticketmaster"]
    assert derived_lines("Total $ 1,234.56") == ["Purchased: $1,234.56"]
    assert derived_lines("3 tickets via AXS") == ["Purchased: 3 tickets on AXS"]
    assert derived_lines("Just a venue name and Ticketweb") == []
    assert derived_lines("") == []


def test_derived_lines_platform_is_word_bounded() -> None:
    # "RA" must not match inside "Opera" or "Grand".
    assert derived_lines("1 ticket at the Grand Opera") == ["Purchased: 1 ticket"]
    assert derived_lines("1 ticket via RA") == ["Purchased: 1 ticket on RA"]


# ---- openers_from_text -----------------------------------------------------------------------------------------------


def test_openers_from_text_only_on_its_own_line(ticket_text: str) -> None:
    assert openers_from_text(ticket_text) == ["Zork."]
    assert openers_from_text("Zed Fox with special guest Zork.\nThe Quarry") == []
    assert openers_from_text("  w/ support from A, B and C  \n") == ["A", "B", "C"]
    assert openers_from_text("with Zork.") == []  # 'with' alone is not the opener form
    assert openers_from_text("") == []


# ---- norm_claims / build_source --------------------------------------------------------------------------------------


def test_norm_claims() -> None:
    assert norm_claims(" $ 42.00 ") == "$42.00"
    assert norm_claims("https://x.example/a/") == "https://x.example/a"
    assert norm_claims("7:30 PM.") == "7:30pm"
    assert norm_claims(42) == "42"


def test_build_source() -> None:
    raw, norm = build_source("caller text", "context text")
    assert raw == "caller text\ncontext text"
    assert norm == "callertextcontexttext"
    assert build_source(None, "ctx") == ("ctx", "ctx")
    assert build_source("caller", "") == ("caller", "caller")
    assert build_source(None, "") == ("", None)
    assert build_source("   ", "") == ("   ", None)


# ---- verify_line -----------------------------------------------------------------------------------------------------


@pytest.fixture
def source_norm(ticket_text: str) -> str:
    _, norm = build_source(None, ticket_text)
    assert norm is not None
    return norm


@pytest.mark.parametrize(
    ("line", "ok"),
    [
        ("Order: https://www.ticketweb.com/order/abc123", True),
        ("Order: https://other.example/order/abc123", False),
        ("Price: $42.00", True),
        ("Price: $43", False),
        ("Doors: 7:30 PM", True),
        ("Doors: 9 PM", False),
        ("Doors: 7:30", True),  # PLAIN_RE claim present in "7:30 PM"
        ("Starts: 19:30", False),  # PLAIN_RE claim absent
        ("Seat: GA", True),  # no claims
        ("Order: https://www.ticketweb.com/order/abc123/", True),  # trailing slash ignored
        ("Price: $ 42.00", True),  # spaces ignored
        ("Doors: 7:30pm", True),  # case ignored
        ("Two facts: $42.00 and 9 PM", False),  # every claim must be present
    ],
)
def test_verify_line(line: str, ok: bool, source_norm: str) -> None:
    assert verify_line(line, source_norm) is ok


def test_verify_line_without_a_source() -> None:
    assert verify_line("Seat: GA", None) is True
    assert verify_line("Price: $42.00", None) is False
    assert verify_line("Doors: 7:30 PM", None) is False


# ---- merge_lines -----------------------------------------------------------------------------------------------------


def test_merge_lines_derived_beats_caller(ticket_text: str, source_norm: str) -> None:
    kept, dropped = merge_lines(
        derived=derived_lines(ticket_text),
        openers=["Zork."],
        caller=["Doors: 9 PM", "Seat: GA", "", "   ", "Price: $99"],
        source_norm=source_norm,
        title="Zed Fox",
    )
    assert kept == ["Doors: 7:30 PM", "Purchased: 2 tickets, $42.00 on Ticketweb", "Opener: Zork.", "Seat: GA"]
    assert dropped == ["Price: $99"]


def test_merge_lines_key_match_is_case_insensitive_and_ignores_the_value(source_norm: str) -> None:
    kept, dropped = merge_lines(
        derived=["Doors: 7:30 PM"],
        openers=[],
        caller=["doors: 7:30 PM", "DOORS 7:30 PM"],
        source_norm=source_norm,
        title="X",
    )
    # "DOORS 7:30 PM" has no colon, so its key is the whole line and it is a new, verified line.
    assert kept == ["Doors: 7:30 PM", "DOORS 7:30 PM"]
    assert dropped == []


def test_merge_lines_drops_an_opener_equal_to_the_title(source_norm: str) -> None:
    kept, _ = merge_lines(derived=[], openers=["zed fox", "Zork."], caller=[], source_norm=source_norm, title="Zed Fox")
    assert kept == ["Opener: Zork."]


def test_merge_lines_keeps_dropped_order(source_norm: str) -> None:
    kept, dropped = merge_lines(
        derived=[],
        openers=[],
        caller=["Link: https://evil.example/x", "Seat: GA", "Price: $99", "Show: 11 PM"],
        source_norm=source_norm,
        title="X",
    )
    assert kept == ["Seat: GA"]
    assert dropped == ["Link: https://evil.example/x", "Price: $99", "Show: 11 PM"]


def test_merge_lines_squashes_caller_lines(source_norm: str) -> None:
    kept, _ = merge_lines(derived=[], openers=[], caller=["  Seat:   GA  "], source_norm=source_norm, title="X")
    assert kept == ["Seat: GA"]


def test_merge_lines_with_no_source_drops_every_claim() -> None:
    kept, dropped = merge_lines(derived=[], openers=[], caller=["Seat: GA", "Price: $5"], source_norm=None, title="X")
    assert kept == ["Seat: GA"]
    assert dropped == ["Price: $5"]


# ---- coerce_description / check_attendees ----------------------------------------------------------------------------


def test_coerce_description() -> None:
    assert coerce_description(None) == []
    assert coerce_description("") == []
    assert coerce_description([]) == []
    assert coerce_description("Doors: 7 PM\\nSeat: GA") == ["Doors: 7 PM", "Seat: GA"]  # literal backslash-n
    assert coerce_description("Doors: 7 PM\nSeat: GA") == ["Doors: 7 PM", "Seat: GA"]
    assert coerce_description("one line") == ["one line"]
    assert coerce_description(["Doors: 7 PM", 42]) == ["Doors: 7 PM", "42"]
    with pytest.raises(FixInput) as ei:
        coerce_description(42)
    assert ei.value.error == "description must be a list of lines"
    assert ei.value.fix == 'use ["Doors: 7:00 PM", "Purchased: 1 ticket, $45"]'
    with pytest.raises(FixInput):
        coerce_description({"Doors": "7 PM"})


def test_check_attendees() -> None:
    assert check_attendees(None) == []
    assert check_attendees([]) == []
    assert check_attendees(["name@example.com", "other@example.org"]) == ["name@example.com", "other@example.org"]
    for bad in (["name"], "name@example.com", ["name@example.com", "nope"], 42):
        with pytest.raises(FixInput) as ei:
            check_attendees(bad)
        assert ei.value.error == "attendees must be a list of email addresses"
        assert ei.value.fix == 'use ["name@example.com"]'
