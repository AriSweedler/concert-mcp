"""Venue lookup over urllib: Google Places API (New) Text Search when a key is stored, else OpenStreetMap's
Nominatim (unit adapter-music).

Places bills the `websiteUri` field at its Enterprise SKU (1,000 free calls a month, far above a few venues a
month); Nominatim is free with one request a second and an identifying User-Agent. Each client makes ONE request
per lookup, and the flow stores the answer, so neither limit is ever near.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from concert_mcp.adapters.protocols import StateStore
from concert_mcp.adapters.setlistfm_http import load_api_key, save_api_key, user_agent
from concert_mcp.config import Settings
from concert_mcp.log import kv, log_debug
from concert_mcp.result import ProviderError
from concert_mcp.text.reply import provider_reply
from concert_mcp.text.venues import VenueHit, maps_lines

PLACES_SEARCH = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELDS = (
    "places.id,places.displayName,places.formattedAddress,places.addressComponents,places.location,places.websiteUri"
)
NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
PROBE_VENUE = "Carnegie Hall, New York"


def load_places_key(path: Path) -> str | None:
    """The api_key in the Places key file; None when absent."""
    return load_api_key(path)


def save_places_key(path: Path, key: str, state: StateStore) -> None:
    """Write {"api_key": key} with mode 600."""
    save_api_key(path, key, state)


def _text(d: Any, *keys: str) -> str:
    """d[k1][k2]... as a string, '' when any step is missing."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(k)
    return str(cur) if cur not in (None, "") else ""


def _post_json(
    urlopen: Callable[..., Any],
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: int,
    provider: str,
    operator: str,
) -> Any:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    return _send(urlopen, req, timeout, provider, operator)


def _get_json(
    urlopen: Callable[..., Any], url: str, headers: dict[str, str], timeout: int, provider: str, operator: str
) -> Any:
    req = urllib.request.Request(url)
    for k, v in headers.items():
        req.add_header(k, v)
    return _send(urlopen, req, timeout, provider, operator)


def _send(urlopen: Callable[..., Any], req: urllib.request.Request, timeout: int, provider: str, operator: str) -> Any:
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001 - a body that cannot be read is not worth a second error
            body = ""
        raise ProviderError(
            provider, f"{provider} refused | status='{e.code}' body='{body}'", provider_reply(provider, operator)
        ) from e
    except urllib.error.URLError as e:
        raise ProviderError(
            provider, f"{provider} unreachable | reason='{e.reason}'", provider_reply(provider, operator)
        ) from e
    except ValueError as e:
        raise ProviderError(
            provider, f"{provider} gave an unreadable answer | error='{e}'", provider_reply(provider, operator)
        ) from e


def places_hit(place: dict[str, Any]) -> VenueHit:
    """One `places[]` entry of a Text Search answer as a VenueHit (pure)."""
    parts: dict[str, str] = {}
    for comp in place.get("addressComponents") or []:
        types = set(comp.get("types") or [])
        long, short = _text(comp, "longText"), _text(comp, "shortText")
        if "street_number" in types:
            parts["street_number"] = long
        elif "route" in types:
            parts["route"] = short or long
        elif "locality" in types or ("postal_town" in types and "locality" not in parts):
            parts["locality"] = long
        elif "administrative_area_level_1" in types:
            parts["region_short"] = short or long
        elif "postal_code" in types:
            parts["postal_code"] = long
        elif "country" in types:
            parts["country"] = long
    lines = maps_lines(**parts)
    if not lines and (formatted := _text(place, "formattedAddress")):
        lines = tuple(x.strip() for x in formatted.split(",") if x.strip())
    loc = place.get("location") or {}
    return VenueHit(
        name=_text(place, "displayName", "text") or "",
        address_lines=lines,
        website=_text(place, "websiteUri") or None,
        place_id=_text(place, "id") or None,
        lat=(float(loc["latitude"]) if isinstance(loc, dict) and loc.get("latitude") is not None else None),
        lng=(float(loc["longitude"]) if isinstance(loc, dict) and loc.get("longitude") is not None else None),
        source="places",
    )


def nominatim_hit(row: dict[str, Any], *, name: str) -> VenueHit:
    """One Nominatim jsonv2 result as a VenueHit (pure). The caller's name wins over OSM's when OSM has none."""
    addr = row.get("address") or {}
    lines = maps_lines(
        street_number=_text(addr, "house_number"),
        route=_text(addr, "road"),
        locality=_text(addr, "city") or _text(addr, "town") or _text(addr, "village") or _text(addr, "municipality"),
        region_short=_text(addr, "ISO3166-2-lvl4").split("-")[-1] if _text(addr, "ISO3166-2-lvl4") else "",
        postal_code=_text(addr, "postcode"),
        country=_text(addr, "country"),
    )
    extra = row.get("extratags") or {}
    website = _text(extra, "website") or _text(extra, "contact:website") or None
    osm_name = _text(row, "name") or _text(addr, "amenity") or ""
    return VenueHit(
        name=osm_name or name,
        address_lines=lines,
        website=website,
        place_id=(f"osm:{_text(row, 'osm_type')}:{_text(row, 'osm_id')}" if _text(row, "osm_id") else None),
        lat=(float(row["lat"]) if row.get("lat") not in (None, "") else None),
        lng=(float(row["lon"]) if row.get("lon") not in (None, "") else None),
        source="nominatim",
    )


class GooglePlacesClient:
    """VenueLookup over Places API (New) Text Search."""

    def __init__(
        self, settings: Settings, *, urlopen: Callable[..., Any] = urllib.request.urlopen, api_key: str | None = None
    ) -> None:
        self.settings = settings
        self.urlopen = urlopen
        key = api_key or load_places_key(settings.places_credentials)
        if not key:
            raise ProviderError(
                "places-unconfigured",
                f"no Google Places key yet | path='{settings.places_credentials}' hint='run: concert-mcp venues auth'",
                provider_reply("places-unconfigured", settings.operator_name),
            )
        self._api_key = key

    @classmethod
    def if_configured(cls, settings: Settings, **kw: Any) -> GooglePlacesClient | None:
        """A client when the key file exists; None otherwise."""
        if not Path(settings.places_credentials).exists():
            return None
        return cls(settings, **kw)

    def find_venue(self, query: str) -> VenueHit | None:
        """The first Text Search result for the query, None when there is none."""
        log_debug("places search " + kv(query=query))
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": PLACES_FIELDS,
            "User-Agent": user_agent(),
        }
        data = _post_json(
            self.urlopen,
            PLACES_SEARCH,
            headers,
            {"textQuery": query, "pageSize": 1},
            self.settings.http_timeout_s,
            "places",
            self.settings.operator_name,
        )
        places = data.get("places") if isinstance(data, dict) else None
        if not places:
            return None
        return places_hit(places[0])


class NominatimClient:
    """VenueLookup over OpenStreetMap's Nominatim; no key, one request per lookup."""

    def __init__(self, settings: Settings, *, urlopen: Callable[..., Any] = urllib.request.urlopen) -> None:
        self.settings = settings
        self.urlopen = urlopen

    def find_venue(self, query: str) -> VenueHit | None:
        log_debug("nominatim search " + kv(query=query))
        params = {"q": query, "format": "jsonv2", "addressdetails": 1, "extratags": 1, "limit": 1}
        url = f"{NOMINATIM_SEARCH}?{urllib.parse.urlencode(params)}"
        rows = _get_json(
            self.urlopen,
            url,
            {"User-Agent": user_agent(), "Accept": "application/json"},
            self.settings.http_timeout_s,
            "nominatim",
            self.settings.operator_name,
        )
        if not isinstance(rows, list) or not rows:
            return None
        return nominatim_hit(rows[0], name=query.split(",")[0].strip())
