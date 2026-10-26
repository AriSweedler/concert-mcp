"""GooglePlacesClient and NominatimClient on FakeUrlopen: request shape, parsing, empty answers, failures."""

from __future__ import annotations

import json

import pytest

from concert_mcp.adapters.places_http import (
    NOMINATIM_SEARCH,
    PLACES_FIELDS,
    PLACES_SEARCH,
    GooglePlacesClient,
    NominatimClient,
    nominatim_hit,
    places_hit,
)
from concert_mcp.config import Settings
from concert_mcp.result import ProviderError
from fakes import FakeUrlopen, http_error

PLACE = {
    "id": "ChIJabc",
    "displayName": {"text": "Cafe Du Nord", "languageCode": "en"},
    "formattedAddress": "2174 Market St, San Francisco, CA 94114, USA",
    "websiteUri": "https://cafedunord.com/",
    "location": {"latitude": 37.7676, "longitude": -122.4292},
    "addressComponents": [
        {"longText": "2174", "shortText": "2174", "types": ["street_number"]},
        {"longText": "Market Street", "shortText": "Market St", "types": ["route"]},
        {"longText": "San Francisco", "shortText": "SF", "types": ["locality", "political"]},
        {"longText": "California", "shortText": "CA", "types": ["administrative_area_level_1", "political"]},
        {"longText": "United States", "shortText": "US", "types": ["country", "political"]},
        {"longText": "94114", "shortText": "94114", "types": ["postal_code"]},
    ],
}

OSM = {
    "osm_type": "way",
    "osm_id": 123,
    "lat": "37.7676",
    "lon": "-122.4292",
    "name": "Cafe du Nord",
    "address": {
        "amenity": "Cafe du Nord",
        "house_number": "2174",
        "road": "Market Street",
        "city": "San Francisco",
        "state": "California",
        "ISO3166-2-lvl4": "US-CA",
        "postcode": "94114",
        "country": "United States",
    },
    "extratags": {"website": "https://cafedunord.com"},
}


def test_places_hit_builds_maps_lines_from_components() -> None:
    hit = places_hit(PLACE)
    assert hit.name == "Cafe Du Nord"
    assert hit.address_lines == ("2174 Market St", "San Francisco CA 94114", "United States")
    assert hit.website == "https://cafedunord.com/"
    assert hit.place_id == "ChIJabc" and hit.lat == 37.7676 and hit.lng == -122.4292
    assert hit.source == "places"


def test_places_hit_falls_back_to_the_formatted_address() -> None:
    hit = places_hit({"displayName": {"text": "X"}, "formattedAddress": "1 Main St, Townsville, TX 75001, USA"})
    assert hit.address_lines == ("1 Main St", "Townsville", "TX 75001", "USA")


def test_nominatim_hit_reads_the_address_dict_and_extratags() -> None:
    hit = nominatim_hit(OSM, name="Cafe Du Nord")
    assert hit.name == "Cafe du Nord"
    assert hit.address_lines == ("2174 Market Street", "San Francisco CA 94114", "United States")
    assert hit.website == "https://cafedunord.com"
    assert hit.place_id == "osm:way:123" and hit.source == "nominatim"
    assert nominatim_hit({"address": {}}, name="Fallback").name == "Fallback"


def test_places_client_posts_the_field_mask_and_key(settings: Settings) -> None:
    fake = FakeUrlopen({("POST", PLACES_SEARCH): {"places": [PLACE]}})
    client = GooglePlacesClient(settings, urlopen=fake, api_key="k" * 39)
    hit = client.find_venue("Cafe Du Nord, San Francisco")
    assert hit is not None and hit.name == "Cafe Du Nord"
    [(method, url, headers, body)] = fake.requests
    assert method == "POST" and url == PLACES_SEARCH
    low = {k.lower(): v for k, v in headers.items()}
    assert low["x-goog-api-key"] == "k" * 39
    assert low["x-goog-fieldmask"] == PLACES_FIELDS
    assert json.loads(body or b"{}") == {"textQuery": "Cafe Du Nord, San Francisco", "pageSize": 1}


def test_places_client_empty_answer_is_none_and_errors_are_provider_errors(settings: Settings) -> None:
    fake = FakeUrlopen({("POST", PLACES_SEARCH): {}})
    assert GooglePlacesClient(settings, urlopen=fake, api_key="k" * 39).find_venue("nowhere") is None
    fake = FakeUrlopen({("POST", PLACES_SEARCH): http_error(403, "denied", url=PLACES_SEARCH)})
    with pytest.raises(ProviderError) as ei:
        GooglePlacesClient(settings, urlopen=fake, api_key="k" * 39).find_venue("x")
    assert ei.value.provider == "places" and "403" in ei.value.error


def test_places_client_needs_a_key(settings: Settings) -> None:
    assert GooglePlacesClient.if_configured(settings) is None
    with pytest.raises(ProviderError) as ei:
        GooglePlacesClient(settings, urlopen=FakeUrlopen())
    assert ei.value.provider == "places-unconfigured"


def test_nominatim_client_sends_a_user_agent_and_one_result(settings: Settings) -> None:
    fake = FakeUrlopen({("GET", NOMINATIM_SEARCH): [OSM]})
    hit = NominatimClient(settings, urlopen=fake).find_venue("Cafe Du Nord, San Francisco")
    assert hit is not None and hit.address_lines[0] == "2174 Market Street"
    [(method, url, headers, _body)] = fake.requests
    assert method == "GET" and url.startswith(NOMINATIM_SEARCH)
    assert "limit=1" in url and "extratags=1" in url and "addressdetails=1" in url
    assert any(k.lower() == "user-agent" and v.startswith("concert-mcp/") for k, v in headers.items())
    assert NominatimClient(settings, urlopen=FakeUrlopen({("GET", NOMINATIM_SEARCH): []})).find_venue("x") is None
