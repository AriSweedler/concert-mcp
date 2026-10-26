"""The venue registry: resolve a concert's venue to a Maps-form address and a website, and remember it
(unit flows-add).

Order on a miss: Google Places when a key is stored, Nominatim when lookups are on, then the address block in the
ticket text. A lookup that fails is a log line and the next source, never a failed insert.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from concert_mcp.flows.services import Services
from concert_mcp.log import kv, log_info, log_warn
from concert_mcp.result import Kind, Outcome
from concert_mcp.text.venues import Venue, VenueHit, address_from_text, venue_key, venue_name_of


def load_registry(svc: Services) -> dict[str, Venue]:
    raw = svc.state.read_json(svc.settings.venues_file, {})
    out: dict[str, Venue] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, dict):
                venue = Venue.from_json(v)
                out[str(k)] = venue
    return out


def save_registry(reg: dict[str, Venue], svc: Services) -> None:
    svc.state.write_json(svc.settings.venues_file, {k: v.to_json() for k, v in sorted(reg.items())})


def lookup_venue(name: str, hint: str, svc: Services) -> VenueHit | None:
    """One hit from the first source that answers: Places, Nominatim, the ticket text."""
    query = f"{name}, {hint}" if hint and hint.lower() != name.lower() else name
    for label, client in (("places", svc.places), ("nominatim", svc.geocoder)):
        if client is None:
            continue
        try:
            hit = client.find_venue(query)
        except Exception as e:  # noqa: BLE001 - a lookup is never fatal to the insert
            log_warn("venue lookup failed | " + kv(source=label, query=query, error=f"{type(e).__name__}: {e}"))
            continue
        if hit is not None and hit.address_lines:
            return hit
    return None


@dataclass(frozen=True)
class Resolved:
    venue: Venue
    new: bool  # first time this venue was seen


def resolve_venue(location: str, text: str, svc: Services, *, date: str, dry_run: bool = False) -> Resolved | None:
    """The registry entry for the location's venue, looked up and stored on a miss; None when the name is empty.

    The `hint` for a lookup is the rest of the caller's location string (a city, an address)."""
    name = venue_name_of(location)
    if not name:
        return None
    hint = location[len(name) :].strip(" ,\n")
    reg = load_registry(svc)
    key = venue_key(name)
    venue = reg.get(key)
    new = venue is None
    if venue is None:
        hit = lookup_venue(name, hint, svc) or address_from_text(text, name)
        if hit is None:
            hit = VenueHit(name=name, address_lines=(), source="caller")
        venue = Venue.from_hit(hit, date=date)
        venue.name = venue.name or name
        log_info(
            "venue "
            + kv(name=venue.name, source=venue.source, lines=len(venue.address_lines), website=bool(venue.website))
        )
        changed = True
    else:
        changed = venue.visited(date)
    if changed and not dry_run:
        reg[key] = venue
        try:
            save_registry(reg, svc)
        except Exception as e:  # noqa: BLE001 - the registry is a convenience; the event matters more
            log_warn(f"venue registry not written | error='{type(e).__name__}: {e}'")
    return Resolved(venue, new)


def refresh_venue(name: str, svc: Services) -> Venue | None:
    """Look one venue up again (or for the first time) and store it, keeping its visits."""
    reg = load_registry(svc)
    key = venue_key(name)
    old = reg.get(key)
    hit = lookup_venue(old.name if old else name, "", svc)
    if hit is None:
        return old
    venue = Venue.from_hit(hit, date="")
    if old:
        venue.visits, venue.first_seen = old.visits, old.first_seen
        venue.website = venue.website or old.website
    reg[key] = venue
    save_registry(reg, svc)
    return venue


def venues_list(svc: Services) -> list[Venue]:
    """Every venue, most visited first, then by name."""
    return sorted(load_registry(svc).values(), key=lambda v: (-len(v.visits), v.name.lower()))


def render_venues(venues: list[Venue]) -> str:
    if not venues:
        return "No venues yet: the first concert_add fills the registry."
    rows = []
    for v in venues:
        n = len(v.visits)
        site = v.website or "no website"
        where = (
            v.address_lines[1]
            if len(v.address_lines) > 1
            else (v.address_lines[0] if v.address_lines else "no address")
        )
        rows.append(f"{v.name} | {n} visit{'' if n == 1 else 's'} | {where} | {site}")
    return "\n".join(rows)


def venues_outcome(svc: Services) -> Outcome:
    venues = venues_list(svc)
    data: dict[str, Any] = {"venues": [v.to_json() for v in venues], "count": len(venues)}
    return Outcome(Kind.OK, reply=render_venues(venues), data=data)
