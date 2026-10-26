"""Pure venue handling: keys, names, Maps-form lines, the ticket's address block, the registry record."""

from __future__ import annotations

from concert_mcp.text.venues import (
    Venue,
    VenueHit,
    address_from_text,
    maps_lines,
    venue_key,
    venue_line,
    venue_name_of,
)

TICKETWEB = """Wed Nov 4 2026
8:00 PM (Doors 7:00 PM)
All Ages
Cafe Du Nord
2174 Market St.
San Francisco, CA 94114
1 General Admission
$20.00
"""


def test_venue_key_drops_the_article_case_and_punctuation() -> None:
    assert venue_key("The Regency Ballroom") == "regency ballroom"
    assert venue_key("Cafe Du Nord") == venue_key("cafe du nord") == "cafe du nord"
    assert venue_key("Bill Graham Civic Aud.") == "bill graham civic aud"
    assert venue_key("Mezzanine & Co") == "mezzanine and co"


def test_venue_name_of_takes_the_text_before_the_first_comma_or_newline() -> None:
    assert venue_name_of("Cafe Du Nord, 2174 Market St, San Francisco, CA 94114, USA") == "Cafe Du Nord"
    assert venue_name_of("The Regency Ballroom\n1300 Van Ness Ave") == "The Regency Ballroom"
    assert venue_name_of("  The Quarry ") == "The Quarry"
    assert venue_name_of("") == ""


def test_maps_lines_builds_street_city_country_and_drops_blanks() -> None:
    assert maps_lines(
        street_number="2174",
        route="Market St",
        locality="San Francisco",
        region_short="CA",
        postal_code="94114",
        country="United States",
    ) == ("2174 Market St", "San Francisco CA 94114", "United States")
    assert maps_lines(locality="Berlin", country="Germany") == ("Berlin", "Germany")
    assert maps_lines() == ()


def test_address_from_text_reads_the_ticket_block() -> None:
    hit = address_from_text(TICKETWEB, "Cafe Du Nord")
    assert hit == VenueHit(
        name="Cafe Du Nord",
        address_lines=("2174 Market St", "San Francisco CA 94114", "United States"),
        source="ticket",
    )
    assert address_from_text("Zed Fox\nThe Quarry, San Francisco\n2 tickets $42.00", "The Quarry") is None
    assert address_from_text("", "X") is None


def test_address_from_text_allows_one_line_between_street_and_city() -> None:
    text = "900 Marin Street\nSuite 2\nSan Francisco, CA 94124\n"
    hit = address_from_text(text, "The Midway")
    assert hit is not None and hit.address_lines[0] == "900 Marin Street"


def test_venue_record_short_location_and_visits() -> None:
    v = Venue.from_hit(
        VenueHit(
            "Cafe Du Nord",
            ("2174 Market St", "San Francisco CA 94114", "United States"),
            website="https://cafedunord.com",
            source="places",
        ),
        date="2026-11-04",
    )
    assert v.city == "San Francisco"
    assert v.short() == "Cafe Du Nord, San Francisco"
    assert v.location_text("whatever") == "Cafe Du Nord\n2174 Market St\nSan Francisco CA 94114\nUnited States"
    assert v.visits == ["2026-11-04"] and v.first_seen == "2026-11-04"
    assert v.visited("2026-11-04") is False
    assert v.visited("2026-10-01") is True
    assert v.visits == ["2026-10-01", "2026-11-04"] and v.first_seen == "2026-10-01"
    assert venue_line(v) == "Venue: https://cafedunord.com"
    assert Venue.from_json(v.to_json()) == v


def test_venue_without_an_address_keeps_the_fallback_and_has_no_line() -> None:
    v = Venue.from_hit(VenueHit("The Quarry", (), source="caller"), date="2026-11-14")
    assert v.city is None and v.short() == "The Quarry"
    assert v.location_text("The Quarry, San Francisco") == "The Quarry, San Francisco"
    assert venue_line(v) is None
    assert venue_line(None) is None
