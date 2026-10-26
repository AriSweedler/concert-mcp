"""Venue records and the pure parts of resolving one: the Maps-form address, the ticket's address block, the key
(unit text-parse).

Google Calendar has no structured place field: `location` is a string, and the Calendar UI draws the map card when
that string is the form Maps itself writes, name and address on separate lines. Every venue the server has seen is
kept in the registry (name, address lines, website, coordinates, the dates visited), so a second show at the same
place needs no lookup.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from concert_mcp.text.titles import normalize_name

# A street line as a ticket prints it: "2174 Market St." / "1300 Van Ness Ave" / "900 Marin Street".
_STREET_RE = re.compile(
    r"^\s*\d{1,6}[A-Za-z]?\s+[A-Za-z0-9'. -]+?\s"
    r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Way|Dr|Drive|Ln|Lane|Pl|Place|Pkwy|Parkway|Hwy|Highway|Ct|Court"
    r"|Ter|Terrace|Sq|Square|Plaza|Broadway|Alley)\.?(?:\s+(?:#|Suite|Ste\.?|Unit|Fl\.?|Floor)\s*\w+)?\s*$",
    re.I,
)
# "San Francisco, CA 94114" / "Brooklyn NY 11211" / "Portland, OR 97209-1234".
_CITY_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z.' -]+?),?\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$")


@dataclass(frozen=True)
class VenueHit:
    """What a lookup returns: the place's name, its address as Maps-form lines, and what else the source knew."""

    name: str
    address_lines: tuple[str, ...]
    website: str | None = None
    place_id: str | None = None
    lat: float | None = None
    lng: float | None = None
    source: str = ""  # "places" | "nominatim" | "ticket"


@dataclass
class Venue:
    """One registry entry."""

    name: str
    address_lines: list[str] = field(default_factory=list)
    website: str | None = None
    place_id: str | None = None
    lat: float | None = None
    lng: float | None = None
    source: str = ""
    visits: list[str] = field(default_factory=list)  # YYYY-MM-DD, sorted, unique
    first_seen: str = ""  # YYYY-MM-DD

    @property
    def key(self) -> str:
        return venue_key(self.name)

    @property
    def city(self) -> str | None:
        """The city from the 'City, ST ZIP' line, when the address has one."""
        for ln in self.address_lines:
            m = _CITY_LINE_RE.match(ln) or re.match(r"^\s*([A-Za-z][A-Za-z.' -]+?)\s+[A-Z]{2}\s+\d{5}", ln)
            if m:
                return m.group(1).strip()
        return None

    def short(self) -> str:
        """'Name, City' for a reply, a topic name or a setlist.fm tie-break; just the name without an address."""
        city = self.city
        return f"{self.name}, {city}" if city else self.name

    def location_text(self, fallback: str) -> str:
        """The calendar's location: Maps form (name, then the address lines) when the address is known."""
        return "\n".join([self.name, *self.address_lines]) if self.address_lines else fallback

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Venue:
        return cls(
            name=str(d.get("name") or ""),
            address_lines=[str(x) for x in d.get("address_lines") or []],
            website=(str(d["website"]) if d.get("website") else None),
            place_id=(str(d["place_id"]) if d.get("place_id") else None),
            lat=(float(d["lat"]) if d.get("lat") is not None else None),
            lng=(float(d["lng"]) if d.get("lng") is not None else None),
            source=str(d.get("source") or ""),
            visits=sorted({str(x) for x in d.get("visits") or []}),
            first_seen=str(d.get("first_seen") or ""),
        )

    @classmethod
    def from_hit(cls, hit: VenueHit, *, date: str) -> Venue:
        return cls(
            name=hit.name,
            address_lines=list(hit.address_lines),
            website=hit.website,
            place_id=hit.place_id,
            lat=hit.lat,
            lng=hit.lng,
            source=hit.source,
            visits=[date] if date else [],
            first_seen=date,
        )

    def visited(self, date: str) -> bool:
        """Record one show date; True when it was new."""
        if not date or date in self.visits:
            return False
        self.visits = sorted({*self.visits, date})
        if not self.first_seen or date < self.first_seen:
            self.first_seen = date
        return True


def venue_key(name: str) -> str:
    """The registry key: normalized name, 'the ' dropped, punctuation out."""
    n = normalize_name(name)
    n = re.sub(r"^the\s+", "", n)
    return re.sub(r"[^a-z0-9 ]+", "", n).strip()


def venue_name_of(location: str) -> str:
    """The venue's name from a location string: the text before the first comma or newline."""
    return re.split(r"[,\n]", str(location or ""), maxsplit=1)[0].strip()


def venue_line(venue: Venue | None) -> str | None:
    """'Venue: <website>' for the description when the registry knows the site."""
    return f"Venue: {venue.website}" if venue and venue.website else None


def maps_lines(
    *,
    street_number: str = "",
    route: str = "",
    locality: str = "",
    region_short: str = "",
    postal_code: str = "",
    country: str = "",
) -> tuple[str, ...]:
    """Maps-form address lines from components: 'Street', 'City ST ZIP', 'Country'; blanks fall away."""
    street = " ".join(x for x in (street_number, route) if x).strip()
    city = " ".join(x for x in (locality, region_short, postal_code) if x).strip()
    return tuple(x for x in (street, city, country) if x)


def address_from_text(text: str, name: str) -> VenueHit | None:
    """The address block a ticket prints: a street line followed by a 'City, ST ZIP' line (name from the caller).

    The country is 'United States' by shape: the ZIP regex only matches US codes."""
    lines = [ln.strip() for ln in str(text or "").splitlines()]
    for i, ln in enumerate(lines[:-1]):
        if not _STREET_RE.match(ln):
            continue
        for nxt in lines[i + 1 : i + 3]:
            m = _CITY_LINE_RE.match(nxt)
            if m:
                street = re.sub(r"\.$", "", ln)
                city_line = f"{m.group(1).strip()} {m.group(2)} {m.group(3)}"
                return VenueHit(
                    name=name or "",
                    address_lines=(street, city_line, "United States"),
                    source="ticket",
                )
    return None
