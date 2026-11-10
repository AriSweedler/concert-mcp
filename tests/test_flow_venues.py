"""The venue registry flow on fakes: lookup order, the registry hit, visits, the ticket fallback, refresh, list."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from concert_mcp.config import Settings
from concert_mcp.flows.services import Services
from concert_mcp.flows.venues import (
    refresh_venue,
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


def test_nominatim_when_places_is_absent_or_fails(settings: Settings) -> None:
    osm = FakeVenueLookup({"cafe du nord": OSM_HIT})
    svc = make(settings, places=None, geocoder=osm)
    r = resolve_venue("Cafe Du Nord, San Francisco", "", svc, date="2026-11-04")
    assert r is not None and r.venue.source == "nominatim" and r.venue.name == "Cafe du Nord"

    broken = FakeVenueLookup(error=RuntimeError("quota"))
    svc = make(settings, places=broken, geocoder=FakeVenueLookup({"cafe du nord": OSM_HIT}))
    r = resolve_venue("Cafe Du Nord", "", svc, date="2026-11-04")
    assert r is not None and r.venue.source == "nominatim"


def test_unknown_venue_without_any_address_is_still_remembered(settings: Settings) -> None:
    svc = make(settings)
    r = resolve_venue("The Quarry, San Francisco", "no address here", svc, date="2026-11-14")
    assert r is not None and r.venue.address_lines == [] and r.venue.source == "caller"
    assert r.venue.location_text("The Quarry, San Francisco") == "The Quarry, San Francisco"
    assert [v.name for v in venues_list(svc)] == ["The Quarry"]
    assert resolve_venue("", "", svc, date="2026-11-14") is None


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
