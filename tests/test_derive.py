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


def test_derive_derived_lines_on_the_ticket(ticket_text: str) -> None:
    # derived_lines_on_the_ticket:
    assert derived_lines(ticket_text) == ["Doors: 7:30 PM", "Purchased: 2 tickets, $42.00 on Ticketweb"]

    # openers_from_text_only_on_its_own_line:
    assert openers_from_text(ticket_text) == ["Zork."]
    assert openers_from_text("Zed Fox with special guest Zork.\nThe Quarry") == []
    assert openers_from_text("  w/ support from A, B and C  \n") == ["A", "B", "C"]
    assert openers_from_text("with Zork.") == []  # 'with' alone is not the opener form
    assert openers_from_text("") == []


def test_derive_derived_lines_doors_normalisation() -> None:
    # derived_lines_doors_normalisation:
    for text, doors in [
        ("Doors 7.30 p.m.", "Doors: 7:30 PM"),
        ("doors open 7pm", "Doors: 7 PM"),
        ("Doors: 7:00PM", "Doors: 7:00 PM"),
        ("Door @ 8 pm", "Doors: 8 PM"),
        ("Doors at 7:30 P.M. sharp", "Doors: 7:30 PM"),
    ]:
        assert derived_lines(text) == [doors]

    # derived_lines_purchased_forms:
    assert derived_lines("1 ticket $45") == ["Purchased: 1 ticket, $45"]
    assert derived_lines("2 x tickets, $90 on Ticketmaster") == ["Purchased: 2 tickets, $90 on Ticketmaster"]
    assert derived_lines("Total $ 1,234.56") == ["Purchased: $1,234.56"]
    assert derived_lines("3 tickets via AXS") == ["Purchased: 3 tickets on AXS"]
    assert derived_lines("Just a venue name and Ticketweb") == []
    assert derived_lines("") == []

    # derived_lines_take_the_total_not_the_subtotal:
    assert derived_lines(TICKETWEB_RECEIPT) == ["Doors: 7:00 PM", "Purchased: 1 ticket, $28.30 on Ticketweb"]

    # derived_lines_total_on_the_same_line_and_grand_total:
    assert derived_lines("Subtotal: $40.00\nFees: $12.50\nTotal: $52.50 via AXS") == ["Purchased: $52.50 on AXS"]
    assert derived_lines("2 tickets\n$30 each\nGrand Total $72.00") == ["Purchased: 2 tickets, $72.00"]

    # derived_lines_without_a_total_label_take_the_largest_amount:
    assert derived_lines("Qty: 2\n2 x $20.00\n$46.50 charged") == ["Purchased: 2 tickets, $46.50"]
    assert derived_lines("Quantity 3 General Admission $15") == ["Purchased: 3 tickets, $15"]

    # derived_lines_platform_is_word_bounded:
    assert derived_lines("1 ticket at the Grand Opera") == ["Purchased: 1 ticket"]
    assert derived_lines("1 ticket via RA") == ["Purchased: 1 ticket on RA"]

    # norm_claims:
    assert norm_claims(" $ 42.00 ") == "$42.00"
    assert norm_claims("https://x.example/a/") == "https://x.example/a"
    assert norm_claims("7:30 PM.") == "7:30pm"
    assert norm_claims(42) == "42"

    # build_source:
    raw, norm = build_source("caller text", "context text")
    assert raw == "caller text\ncontext text"
    assert norm == "callertextcontexttext"
    assert build_source(None, "ctx") == ("ctx", "ctx")
    assert build_source("caller", "") == ("caller", "caller")
    assert build_source(None, "") == ("", None)
    assert build_source("   ", "") == ("   ", None)

    # verify_line_without_a_source:
    assert verify_line("Seat: GA", None) is True
    assert verify_line("Price: $42.00", None) is False
    assert verify_line("Doors: 7:30 PM", None) is False

    # merge_lines_with_no_source_drops_every_claim:
    kept, dropped = merge_lines(derived=[], openers=[], caller=["Seat: GA", "Price: $5"], source_norm=None, title="X")
    assert kept == ["Seat: GA"]
    assert dropped == ["Price: $5"]

    # coerce_description:
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

    # check_attendees:
    assert check_attendees(None) == []
    assert check_attendees([]) == []
    assert check_attendees(["name@example.com", "other@example.org"]) == ["name@example.com", "other@example.org"]
    for bad in (["name"], "name@example.com", ["name@example.com", "nope"], 42):
        with pytest.raises(FixInput) as ei:
            check_attendees(bad)
        assert ei.value.error == "attendees must be a list of email addresses"
        assert ei.value.fix == 'use ["name@example.com"]'


TICKETWEB_RECEIPT = """Wed Nov 4 2026
8:00 PM (Doors 7:00 PM)
All Ages
Cafe Du Nord
2174 Market St.
San Francisco, CA 94114
1 General Admission
$20.00
General Admission
SUBTOTAL
$20.00
SERVICE FEE
$8.30
DELIVERY FEE - ETICKETS
$0.00
TOTAL
$28.30
Payment Type:
charged: Visa ****0000
($28.30)
ticketweb.com
"""


# ---- openers_from_text -----------------------------------------------------------------------------------------------


# ---- norm_claims / build_source --------------------------------------------------------------------------------------


# ---- verify_line -----------------------------------------------------------------------------------------------------


@pytest.fixture
def source_norm(ticket_text: str) -> str:
    _, norm = build_source(None, ticket_text)
    assert norm is not None
    return norm


def test_derive_verify_line(source_norm: str) -> None:
    # verify_line:
    for line, ok in [
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
    ]:
        assert verify_line(line, source_norm) is ok

    # merge_lines_key_match_is_case_insensitive_and_ignores_the_value:
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

    # merge_lines_drops_an_opener_equal_to_the_title:
    kept, _ = merge_lines(derived=[], openers=["zed fox", "Zork."], caller=[], source_norm=source_norm, title="Zed Fox")
    assert kept == ["Headliner: Zed Fox", "Opener: Zork."]

    # merge_lines_keeps_dropped_order:
    kept, dropped = merge_lines(
        derived=[],
        openers=[],
        caller=["Link: https://evil.example/x", "Seat: GA", "Price: $99", "Show: 11 PM"],
        source_norm=source_norm,
        title="X",
    )
    assert kept == ["Seat: GA"]
    assert dropped == ["Link: https://evil.example/x", "Price: $99", "Show: 11 PM"]

    # merge_lines_squashes_caller_lines:
    kept, _ = merge_lines(derived=[], openers=[], caller=["  Seat:   GA  "], source_norm=source_norm, title="X")
    assert kept == ["Seat: GA"]


# ---- merge_lines -----------------------------------------------------------------------------------------------------


def test_merge_lines_derived_beats_caller(ticket_text: str, source_norm: str) -> None:
    kept, dropped = merge_lines(
        derived=derived_lines(ticket_text),
        openers=["Zork."],
        caller=["Doors: 9 PM", "Seat: GA", "", "   ", "Price: $99"],
        source_norm=source_norm,
        title="Zed Fox",
    )
    assert kept == [
        "Doors: 7:30 PM",
        "Purchased: 2 tickets, $42.00 on Ticketweb",
        "Headliner: Zed Fox",
        "Opener: Zork.",
        "Seat: GA",
    ]
    assert dropped == ["Price: $99"]


# ---- coerce_description / check_attendees ----------------------------------------------------------------------------
