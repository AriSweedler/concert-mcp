"""The venue registry flow on fakes: lookup order, the registry hit, visits, the ticket fallback, refresh, list."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from concert_mcp.config import Settings
from concert_mcp.flows.services import Services
from concert_mcp.flows.venues import (
    load_registry,
    refresh_venue,
    render_venues,
    resolve_venue,
    venues_list,
    venues_outcome,
)
from concert_mcp.text.venues import VenueHit
from fakes import FakeCalendar, FakeChat, FakeClock, FakeContext, FakeVenueLookup, MemoryState

LA = ZoneInfo("America/Los_Angeles")
HIT = VenueHit(
    "Cafe Du Nord",
    ("2174 Market St", "San Francisco CA 94114", "United States"),
    website="https://cafedunord.com",
    source="places",
)
OSM_HIT = VenueHit(
    "Cafe du Nord", ("2174 Market Street", "San Francisco CA 94114", "United States"), source="nominatim"
)
TICKET = "Cafe Du Nord\n2174 Market St.\nSan Francisco, CA 94114\n1 General Admission\n$20.00\n"


def make(settings: Settings, *, places: Any = None, geocoder: Any = None) -> Services:
    clock = FakeClock(datetime(2026, 10, 26, 12, 0, tzinfo=LA))
    return Services(
        settings=settings,
        calendar=FakeCalendar(calendars=[]),
        chat=FakeChat(),
        context=FakeContext(route=None, session_id=None, text=""),
        state=MemoryState(clock=clock),
        clock=clock,
        places=places,
        geocoder=geocoder,
    )


def test_places_first_then_stored(settings: Settings) -> None:
    places, osm = FakeVenueLookup({"cafe du nord": HIT}), FakeVenueLookup({"cafe du nord": OSM_HIT})
    svc = make(settings, places=places, geocoder=osm)
    r = resolve_venue("Cafe Du Nord, San Francisco", "", svc, date="2026-11-04")
    assert r is not None and r.new is True
    assert r.venue.address_lines == ["2174 Market St", "San Francisco CA 94114", "United States"]
    assert r.venue.website == "https://cafedunord.com" and r.venue.visits == ["2026-11-04"]
    assert places.queries == ["Cafe Du Nord, San Francisco"] and osm.queries == []
    assert load_registry(svc)["cafe du nord"].name == "Cafe Du Nord"


def test_second_show_hits_the_registry_with_no_lookup(settings: Settings) -> None:
    places = FakeVenueLookup({"cafe du nord": HIT})
    svc = make(settings, places=places)
    resolve_venue("Cafe Du Nord", "", svc, date="2026-11-04")
    r = resolve_venue("cafe du nord, 2174 Market St", "", svc, date="2026-12-01")
    assert r is not None and r.new is False
    assert r.venue.visits == ["2026-11-04", "2026-12-01"]
    assert places.queries == ["Cafe Du Nord"]  # one lookup for two shows
    r3 = resolve_venue("Cafe Du Nord", "", svc, date="2026-12-01")
    assert r3 is not None and r3.venue.visits == ["2026-11-04", "2026-12-01"]  # a repeat date is not a new visit


def test_nominatim_when_places_is_absent_or_fails(settings: Settings) -> None:
    osm = FakeVenueLookup({"cafe du nord": OSM_HIT})
    svc = make(settings, places=None, geocoder=osm)
    r = resolve_venue("Cafe Du Nord, San Francisco", "", svc, date="2026-11-04")
    assert r is not None and r.venue.source == "nominatim" and r.venue.name == "Cafe du Nord"

    broken = FakeVenueLookup(error=RuntimeError("quota"))
    svc = make(settings, places=broken, geocoder=FakeVenueLookup({"cafe du nord": OSM_HIT}))
    r = resolve_venue("Cafe Du Nord", "", svc, date="2026-11-04")
    assert r is not None and r.venue.source == "nominatim"


def test_ticket_text_when_no_lookup_answers(settings: Settings) -> None:
    svc = make(settings, places=FakeVenueLookup(), geocoder=FakeVenueLookup())
    r = resolve_venue("Cafe Du Nord", TICKET, svc, date="2026-11-04")
    assert r is not None and r.venue.source == "ticket"
    assert r.venue.address_lines == ["2174 Market St", "San Francisco CA 94114", "United States"]
    assert r.venue.website is None


def test_unknown_venue_without_any_address_is_still_remembered(settings: Settings) -> None:
    svc = make(settings)
    r = resolve_venue("The Quarry, San Francisco", "no address here", svc, date="2026-11-14")
    assert r is not None and r.venue.address_lines == [] and r.venue.source == "caller"
    assert r.venue.location_text("The Quarry, San Francisco") == "The Quarry, San Francisco"
    assert [v.name for v in venues_list(svc)] == ["The Quarry"]
    assert resolve_venue("", "", svc, date="2026-11-14") is None


def test_dry_run_writes_nothing(settings: Settings) -> None:
    svc = make(settings, places=FakeVenueLookup({"cafe": HIT}))
    r = resolve_venue("Cafe Du Nord", "", svc, date="2026-11-04", dry_run=True)
    assert r is not None and r.venue.website == "https://cafedunord.com"
    assert load_registry(svc) == {}


def test_refresh_keeps_visits_and_list_orders_by_visits(settings: Settings) -> None:
    svc = make(settings, places=FakeVenueLookup())
    resolve_venue("Cafe Du Nord", "", svc, date="2026-11-04")
    resolve_venue("Cafe Du Nord", "", svc, date="2026-12-01")
    resolve_venue("The Quarry", "", svc, date="2026-11-14")
    svc.places = FakeVenueLookup({"cafe du nord": HIT})
    v = refresh_venue("cafe du nord", svc)
    assert v is not None and v.website == "https://cafedunord.com" and v.visits == ["2026-11-04", "2026-12-01"]
    names = [x.name for x in venues_list(svc)]
    assert names == ["Cafe Du Nord", "The Quarry"]
    out = venues_outcome(svc)
    assert out.data["count"] == 2
    assert out.reply.splitlines()[0] == "Cafe Du Nord | 2 visits | San Francisco CA 94114 | https://cafedunord.com"
    assert refresh_venue("Nowhere Hall", svc) is None


def test_render_venues_empty() -> None:
    assert render_venues([]).startswith("No venues yet")
