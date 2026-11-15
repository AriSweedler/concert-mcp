"""The Partiful invite's pure half (unit text-partiful): what the page says and the shapes Partiful's API takes.

Pure: no I/O. An invite is a public Partiful event for one concert: the title `Concert: <artist> at <venue> <m/d>`,
the doors and show times as its description, the venue as a Maps place, the cover of the artist's latest album as its
poster, and two link fields under the venue, `Tickets` and `<Artist> songs I like` (Partiful's `customFields`, each
`{icon, value, url}`). The shapes here were read from invites made on partiful.com and checked against the live API
on 2026-11-14.

The ticket link is what turns a concert into an invite, and it is found here, by rule, not by a model: a line
`tickets: <URL>` in the text.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from concert_mcp.text.venues import venue_key

TICKETS_PREFIX = "Tickets: "
PARTIFUL_PREFIX = "Partiful: "
TEASER_PREFIX = "Teaser playlist: "
DOORS_PREFIX = "Doors: "
EXPECTED_PREFIX = "Expected: "
# The description's last line. Partiful shows a description as plain text and turns an address in it into a link
# (checked on a page, 2026-11-14: `[text](url)` stays as typed, `github.com/…` becomes a link), so the address is
# the link text.
CREATED_BY = "Created by github.com/AriSweedler/concert-mcp"
EVENT_URL = "https://partiful.com/e/{id}"
BUCKET = "getpartiful.appspot.com"
STORAGE = f"https://firebasestorage.googleapis.com/v0/b/{BUCKET}/o"

_URL_RE = re.compile(r"https?://[^\s<>\"']+")
# A line of chat or ticket text that names the ticket link: `tickets: <URL>`, nothing else on the line.
_TICKETS_LINE_RE = re.compile(r"^[ \t]*tickets?[ \t]*:[ \t]*(https?://\S+)[ \t]*$", re.I | re.M)
_CLOCK_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\b")
_EXPECTED_DOORS_RE = re.compile(r"^doors (\d{1,2})(?::(\d{2}))?\b")

# What the create form on partiful.com saves besides the inputs (its defaults on 2026-11-14).
EVENT_DEFAULTS: Mapping[str, Any] = {
    "showHostList": True,
    "showGuestCount": True,
    "showGuestList": True,
    "showActivityTimestamps": True,
    "displayInviteButton": True,
    "allowGuestPhotoUpload": True,
    "enableGuestReminders": True,
    "rsvpsEnabled": True,
    "allowGuestsToInviteMutuals": True,
}


@dataclass(frozen=True)
class InviteResult:
    """What one attempt to make an invite came to.

    `need_tickets`: no ticket link, so nothing was made. `signed_out`: Partiful has no usable session; `phone_tail`
    is the end of the number a login code was texted to, None when none was sent. `exists`: the calendar event
    already carries a Partiful link.
    """

    status: Literal["created", "exists", "need_tickets", "signed_out", "dry_run", "failed"]
    url: str | None = None
    title: str | None = None
    links: tuple[tuple[str, str], ...] = ()  # (label, url) of each link field, in page order
    poster: bool = False
    going: bool = False  # the account itself is on the guest list as going
    reason: str | None = None
    phone_tail: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "url": self.url,
            "title": self.title,
            "links": [list(pair) for pair in self.links],
            "poster": self.poster,
            "going": self.going,
            "reason": self.reason,
        }


def ticket_link(value: str) -> str | None:
    """The first http(s) URL in a value given as the ticket link ('https://…', 'tickets: https://…'); None when it
    holds none. Punctuation a sentence leaves behind the URL is dropped."""
    found = _URL_RE.search(value)
    return found.group(0).rstrip(".,;)") if found else None


def ticket_link_in(text: str) -> str | None:
    """The ticket link a text names on a line of its own, `tickets: <URL>`; None otherwise.

    Only that form counts: a ticket's own text is full of other addresses (the order page, the venue), and none of
    them is where a friend buys a ticket.
    """
    found = _TICKETS_LINE_RE.search(text)
    return ticket_link(found.group(1)) if found else None


def line_value(lines: Sequence[str], prefix: str) -> str | None:
    """The value of the first line that starts with `prefix`; None when there is none or it is empty."""
    for ln in lines:
        if ln.startswith(prefix):
            return ln[len(prefix) :].strip() or None
    return None


def short_clock(when: datetime) -> str:
    """'8:30' for 20:30: the twelve-hour clock without its AM or PM, as the invites are written."""
    return f"{when.hour % 12 or 12}:{when.minute:02d}"


def invite_title(artist: str, venue: str, start: datetime) -> str:
    """'Concert: Zed Fox at The Quarry 11/14'; without a venue, 'Concert: Zed Fox 11/14'."""
    place = f" at {venue}" if venue else ""
    return f"Concert: {artist}{place} {start.month}/{start.day}"


def invite_description(lines: Sequence[str], start: datetime) -> str:
    """'Doors 7:30, show 8:30' from the event's doors time and its start ('Show 8:30' when no doors time is known),
    then, after a blank line, the line that says what made the page.

    The doors time is the ticket's own (`Doors: 7:30 PM`, `Doors: 7 PM`), else the announced one the lineup step
    found (`Expected: doors 6:00 PM, …`).
    """
    doors = _CLOCK_RE.match(line_value(lines, DOORS_PREFIX) or "") or _EXPECTED_DOORS_RE.match(
        line_value(lines, EXPECTED_PREFIX) or ""
    )
    show = short_clock(start)
    times = f"Doors {int(doors.group(1))}:{doors.group(2) or '00'}, show {show}" if doors else f"Show {show}"
    return f"{times}\n\n{CREATED_BY}"


def link_fields(artist: str, tickets: str, playlist: str | None) -> list[dict[str, str]]:
    """The link fields under the venue: Tickets, then '<Artist> songs I like' when there is a teaser playlist."""
    fields = [{"icon": "link", "value": "Tickets", "url": tickets}]
    if playlist:
        fields.append({"icon": "music-note", "value": f"{artist} songs I like", "url": playlist})
    return fields


def utc_stamp(when: datetime) -> str:
    """The form Partiful stores a time in: UTC, milliseconds, Z."""
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def event_payload(
    *,
    title: str,
    start: datetime,
    tz: str,
    description: str,
    fields: Sequence[Mapping[str, str]],
    image: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The `event` createEvent takes: public, a start and no end, as every concert invite on the account is."""
    event: dict[str, Any] = {
        **EVENT_DEFAULTS,
        "title": title,
        "startDate": utc_stamp(start),
        "timezone": tz,
        "description": description,
        "status": "UNSAVED",
        "visibility": "public",
        "customFields": [dict(f) for f in fields],
    }
    if image:
        event["image"] = dict(image)
    return event


def location_parts(location: str) -> list[str]:
    """A calendar location's parts: 'The Quarry\\n1 Main St\\nSan Francisco, CA' and 'The Quarry, San Francisco'
    alike are the venue's name, then the rest, split at every line break and comma."""
    return [part.strip() for part in re.split(r"[\n,]", location) if part.strip()]


def place_query(location: str) -> str:
    """What to ask Partiful's venue search: the venue's name and where it is, without the street.

    Checked live on 2026-11-14: 'The Fillmore, San Francisco, CA 94115' finds the venue, while the same words with
    '1805 Geary Blvd' among them find the bare address, which has no name.
    """
    parts = location_parts(location)
    return ", ".join(parts[:1] + [p for p in parts[1:] if not p[0].isdigit()])


def same_venue(name: str, place_name: str) -> bool:
    """Whether a found place is the venue asked for: one name contains the other, 'the' and punctuation aside."""
    a, b = venue_key(name), venue_key(place_name)
    return bool(a and b) and (a in b or b in a)


def structured_location(place: Mapping[str, Any], display_name: str = "") -> dict[str, Any]:
    """The `locationInfo` of a Maps place, from getLocationDetails' answer: the address without its country line,
    shown under `display_name` (the venue as the calendar spells it) when one is given."""
    lines = [str(ln) for ln in place.get("formattedAddressLines") or []]
    if len(lines) > 1 and lines[-1] == place.get("country"):
        lines = lines[:-1]
    maps_info: dict[str, Any] = {"addressLines": lines}
    if place.get("name"):
        maps_info["name"] = place["name"]
    info: dict[str, Any] = {
        "type": "structured",
        "mapsInfo": maps_info,
        "placeResult": dict(place),
        "hasPostCode": (place.get("structuredAddress") or {}).get("postCode") is not None,
    }
    if display_name:
        info["displayName"] = display_name
    return info


def freeform_location(value: str) -> dict[str, Any]:
    """The `locationInfo` of a location kept as written."""
    return {"type": "freeform", "value": value}


def storage_url(path: str) -> str:
    """Where Partiful's storage serves an uploaded object."""
    return f"{STORAGE}/{path.replace('/', '%2F')}?alt=media"


def image_record(
    *, path: str, size: int, width: int, height: int, content_type: str, name: str, now: datetime
) -> dict[str, Any]:
    """The event's `image` for an uploaded poster, as the site records one."""
    url = storage_url(path)
    stamp = utc_stamp(now)
    return {
        "source": "upload",
        "type": "image",
        "upload": {
            "type": "image",
            "path": path,
            "fileCreatedAt": stamp,
            "uploadedAt": stamp,
            "name": name,
            "size": size,
            "width": width,
            "height": height,
            "contentType": content_type,
            "storageUri": f"gs://{BUCKET}/{path}",
            "url": url,
        },
        "url": url,
        "contentType": content_type,
        "name": name,
        "height": height,
        "width": width,
    }


def latest_album_cover(albums: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """{url, width, height, name} of the newest album's largest cover; a single counts only when there is no album.

    Spotify's `release_date` is `YYYY`, `YYYY-MM` or `YYYY-MM-DD`, which sort as text.
    """
    with_cover = [a for a in albums if a.get("images")]
    full = [a for a in with_cover if a.get("album_type") == "album"]
    pool = full or with_cover
    if not pool:
        return None
    newest = max(pool, key=lambda a: str(a.get("release_date") or ""))
    cover = max(newest["images"], key=lambda i: int(i.get("width") or 0))
    if not cover.get("url"):
        return None
    return {
        "url": str(cover["url"]),
        "width": int(cover.get("width") or 640),
        "height": int(cover.get("height") or 640),
        "name": str(newest.get("name") or "cover"),
    }


def phone_tail(phone: str) -> str:
    """The last four digits of a phone number: all of it that a reply ever shows."""
    return re.sub(r"\D", "", phone)[-4:]
