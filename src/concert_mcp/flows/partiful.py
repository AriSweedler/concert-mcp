"""The Partiful invite and the Partiful login (unit flows-partiful).

A concert on the calendar becomes a Partiful invite when its ticket link is known: no model decides it. The link
arrives with the ticket text (`concert_add` finds a `tickets: <URL>` line) or later on its own (`tickets_add`), and
without one nothing is created on Partiful and the user is asked for it. The calendar event gains `Tickets: <url>`
as soon as the link is known and `Partiful: <url>` once the page exists, each rendered as a link by the calendar
adapter; an event that already has a Partiful line gets no second page.

The login is a conversation: `partiful_login` with no code has Partiful text one to the configured number, and with
the code it stores the session. When a call finds no usable session it says "Partiful is signed out" and texts a
code itself, so the next message can be the code. No flow here raises past its boundary.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from concert_mcp.adapters.protocols import PartifulApi
from concert_mcp.flows.invite_todo import todo_step
from concert_mcp.flows.records import find_record, forget_event, remember_concert, remember_invite
from concert_mcp.flows.services import Services
from concert_mcp.flows.setlist_playlist import event_for_show
from concert_mcp.flows.teaser import resolve_artist
from concert_mcp.flows.topics import live_event, topic_event
from concert_mcp.log import kv, log_info, log_warn
from concert_mcp.result import FixInput, Kind, NeedUser, Outcome, ProviderError, crash_outcome, need, outcome_from
from concert_mcp.text.candidates import missing_link_lines, patched_description
from concert_mcp.text.dates import concert_date
from concert_mcp.text.description import description_of, lines_of
from concert_mcp.text.partiful import (
    EVENT_URL,
    PARTIFUL_PREFIX,
    TICKETS_PREFIX,
    InviteResult,
    event_id_of,
    event_payload,
    freeform_location,
    image_record,
    invite_description,
    invite_title,
    latest_album_cover,
    line_value,
    location_parts,
    merged_links,
    phone_tail,
    place_query,
    same_venue,
    show_links,
    structured_location,
    ticket_link,
)
from concert_mcp.text.reply import (
    PARTIFUL_EVENT_LINKED,
    PARTIFUL_NO_EVENT_REPLY,
    PARTIFUL_NO_PHONE,
    PARTIFUL_NOT_UPDATED,
    PARTIFUL_UPDATED,
    render_partiful,
    render_partiful_code_sent,
    render_partiful_signed_in,
)
from concert_mcp.text.routes import chat_target, forum_target
from concert_mcp.text.topics import topic_key
from concert_mcp.text.venues import venue_name_of

REASON_LIMIT = 160
TICKETS_FIX = 'pass the ticket page\'s address in tickets, e.g. "https://www.ticketweb.com/event/zed-fox-tickets/123"'
WOULD_LOGIN_REPLY = "**Would log in to Partiful** (dry run: no code texted, nothing stored)"


@dataclass(frozen=True)
class InviteRequest:
    """One concert as its calendar event has it, and the ticket link when one is known."""

    artist: str
    start: datetime
    location: str
    lines: tuple[str, ...]
    tickets: str | None


def request_code(svc: Services) -> str | None:
    """Have Partiful text a login code to the configured number; the number's last four digits, or None when no
    number is set or the text could not be sent. Never raises."""
    phone = svc.settings.partiful_phone
    if not phone or svc.partiful is None:
        return None
    try:
        svc.partiful.send_code(phone)
    except Exception as e:  # noqa: BLE001 - the caller's reply says "signed out" either way
        log_warn(f"partiful login code not sent | error='{type(e).__name__}: {str(e)[:200]}'")
        return None
    return phone_tail(phone)


def place_info(location: str, api: PartifulApi) -> dict[str, Any]:
    """The invite's `locationInfo`: the Maps place Partiful's own search finds for the venue, under the venue's
    name as the calendar spells it; else the location as written. A place whose name is not the venue's is not
    used: a wrong address on a public page is worse than none. A dead session propagates (this is the first
    signed call of an invite); any other failure keeps the words."""
    parts = location_parts(location)
    name = parts[0] if parts else ""
    try:
        place = api.find_place(place_query(location)) if name else None
    except ProviderError as e:
        if e.provider == "partiful-signed-out":
            raise
        log_warn(f"partiful place lookup failed | error='{str(e.error)[:200]}'")
        place = None
    if place and same_venue(name, str(place.get("name") or "")):
        return structured_location(place, name)
    return freeform_location(", ".join(parts))


def poster_image(artist: str, api: PartifulApi, svc: Services) -> dict[str, Any] | None:
    """The invite's poster: the cover of the artist's newest album on Spotify, uploaded to Partiful. None, and
    Partiful's default poster, when Spotify is not set up, knows no cover, or anything on the way fails."""
    if svc.spotify is None:
        return None
    try:
        found, _corrected = resolve_artist(artist, svc.spotify)
        cover = latest_album_cover(svc.spotify.artist_albums(found.id))
        if cover is None:
            return None
        data = api.download(cover["url"])
        path = api.upload_image(data, "image/jpeg")
    except Exception as e:  # noqa: BLE001 - the invite matters more than its poster
        log_warn(f"partiful poster not made | error='{type(e).__name__}: {str(e)[:200]}'")
        return None
    return image_record(
        path=path,
        size=len(data),
        width=cover["width"],
        height=cover["height"],
        content_type="image/jpeg",
        name=f"{cover['name']}.jpg",
        now=svc.clock.now(),
    )


def mark_going(event_id: str, api: PartifulApi, svc: Services) -> bool:
    """Put the account on its own invite's guest list as going, under the name Partiful has for it; whether it
    did. Never raises: the page exists either way."""
    try:
        name = str(api.me().get("name") or "")
        if not name:
            return False
        api.rsvp_going(event_id, name, svc.settings.tz)
    except Exception as e:  # noqa: BLE001
        log_warn(f"partiful rsvp not made | error='{type(e).__name__}: {str(e)[:200]}'")
        return False
    return True


def make_invite(req: InviteRequest, svc: Services, *, dry_run: bool = False) -> InviteResult:
    """Create the Partiful page for one concert. Never raises: every way it can end is an InviteResult."""
    existing = line_value(req.lines, PARTIFUL_PREFIX)
    if existing:
        return InviteResult("exists", url=existing)
    if not req.tickets:
        return InviteResult("need_tickets")
    title = invite_title(req.artist, venue_name_of(req.location), req.start)
    # The ticket link first, as it just arrived; then whatever else the show's lines link to.
    others = [f for f in show_links(req.artist, req.lines) if f["value"] != "Tickets"]
    fields = [{"icon": "link", "value": "Tickets", "url": req.tickets}, *others]
    links = tuple((f["value"], f["url"]) for f in fields)
    if dry_run:
        return InviteResult("dry_run", title=title, links=links)
    api = svc.partiful
    if api is None:
        return InviteResult("failed", reason="Partiful is not set up")
    try:
        if not api.signed_in():
            return InviteResult("signed_out", phone_tail=request_code(svc))
        location = place_info(req.location, api)
        image = poster_image(req.artist, api, svc)
        event_id = api.create_event(
            event_payload(
                title=title,
                start=req.start,
                tz=svc.settings.tz,
                description=invite_description(req.lines, req.start),
                fields=fields,
                image=image,
            )
        )
    except ProviderError as e:
        if e.provider == "partiful-signed-out":
            return InviteResult("signed_out", phone_tail=request_code(svc))
        return InviteResult("failed", reason=str(e.error)[:REASON_LIMIT])
    except Exception as e:  # noqa: BLE001 - concert_add calls this after the event landed; it must not crash it
        return InviteResult("failed", reason=f"{type(e).__name__}: {e}"[:REASON_LIMIT])
    url = EVENT_URL.format(id=event_id)
    log_info("partiful invite created | " + kv(title=title, url=url, poster=image is not None))
    try:
        api.set_location(event_id, location)
    except Exception as e:  # noqa: BLE001 - the page exists; it only lacks its venue
        log_warn(f"partiful location not set | url='{url}' error='{type(e).__name__}: {str(e)[:200]}'")
    going = mark_going(event_id, api, svc)
    return InviteResult("created", url=url, title=title, links=links, poster=image is not None, going=going)


def refresh_invite_links(artist: str, lines: Sequence[str], svc: Services) -> str | None:
    """Bring a show's existing Partiful page up to its fact lines: a link the show gained since the page was made
    (the setlist.fm page, the setlist playlist) is added, and a link someone put on the page by hand is kept.

    None when the show has no page or the page already has every link; else what happened, for the reply.
    Never raises.
    """
    event_id = event_id_of(line_value(lines, PARTIFUL_PREFIX) or "")
    api = svc.partiful
    if not event_id or api is None:
        return None
    try:
        if not api.signed_in():
            return PARTIFUL_NOT_UPDATED.format(why="Partiful is signed out")
        current = api.event_links(event_id)
        merged = merged_links(current, show_links(artist, lines))
        if merged == current:
            return None
        api.set_links(event_id, merged)
    except ProviderError as e:
        signed_out = e.provider == "partiful-signed-out"
        return PARTIFUL_NOT_UPDATED.format(why="Partiful is signed out" if signed_out else str(e.error)[:REASON_LIMIT])
    except Exception as e:  # noqa: BLE001 - the playlist and the calendar event are done; the page only lags
        return PARTIFUL_NOT_UPDATED.format(why=f"{type(e).__name__}: {e}"[:REASON_LIMIT])
    log_info("partiful invite links updated | " + kv(event=event_id, links=len(merged)))
    return PARTIFUL_UPDATED


def link_event(calendar_id: str, event_id: str, lines: Sequence[str], svc: Services) -> str:
    """Add each line the event's description lacks; the reply's note on how that went. Never raises."""
    try:
        event = svc.calendar.get_event(calendar_id, event_id)
        missing = missing_link_lines(description_of(event), lines)
        if missing:
            description = patched_description(description_of(event), missing)
            svc.calendar.patch_event(calendar_id, event_id, description=description)
        return PARTIFUL_EVENT_LINKED
    except ProviderError as e:
        log_warn(f"event not linked | error='{str(e.error)[:200]}'")
        return f"Calendar event not updated: {str(e.error)[:REASON_LIMIT]}"


def _start_of(event: dict[str, Any], svc: Services) -> datetime:
    start = event.get("start") or {}
    when = datetime.fromisoformat(str(start.get("dateTime") or start.get("date") or ""))
    return when if when.tzinfo else when.replace(tzinfo=svc.tz)


def invite_for_event(
    calendar_id: str, event: dict[str, Any], link: str | None, svc: Services, *, dry_run: bool = False
) -> tuple[InviteResult, str]:
    """Express one calendar event as a Partiful page: (the result, the note on the calendar event).

    `link` is a ticket link that just arrived; without one the event's own Tickets line is the link. The link is
    written to the event before Partiful is asked for anything, so a login in between loses nothing, and the
    Partiful line follows once the page exists. Raises only what reading the event's start can raise.
    """
    note = PARTIFUL_EVENT_LINKED
    if link and not dry_run:
        note = link_event(calendar_id, event["id"], [f"{TICKETS_PREFIX}{link}"], svc)
    lines = tuple(lines_of(description_of(event)))
    req = InviteRequest(
        artist=str(event.get("summary") or ""),
        start=_start_of(event, svc),
        location=str(event.get("location") or ""),
        lines=lines,
        tickets=link or ticket_link(line_value(lines, TICKETS_PREFIX) or ""),
    )
    res = make_invite(req, svc, dry_run=dry_run)
    if res.status == "created":
        note = link_event(calendar_id, event["id"], [f"{PARTIFUL_PREFIX}{res.url}"], svc)
        made = {
            "url": res.url,
            "title": res.title,
            "description": invite_description(req.lines, req.start),
            "links": [list(pair) for pair in res.links],
        }
        remember_invite(event["id"], made, svc)
    return res, note


def invite_step(calendar_id: str, event_id: str, link: str | None, svc: Services) -> InviteResult:
    """concert_add's last step for a show whose ticket link is known. Never raises: the event has landed."""
    try:
        event = svc.calendar.get_event(calendar_id, event_id)
        res, _note = invite_for_event(calendar_id, event, link, svc)
        return res
    except Exception as e:  # noqa: BLE001 - the reply with the calendar link matters more than the invite
        log_warn(f"partiful step failed | error='{type(e).__name__}: {str(e)[:200]}'")
        return InviteResult("failed", reason=f"{type(e).__name__}: {e}"[:REASON_LIMIT])


def live_topic_key(svc: Services) -> str | None:
    """'<chat>:<thread>' of the Telegram forum topic the caller speaks from; None anywhere else."""
    _sid, route = svc.context.live_route(svc.clock.time())
    target = forum_target(chat_target(route)) if route else None
    return topic_key(target[0], target[1]) if target else None


def recorded_show(svc: Services, *, topic: str | None, artist: str, date: str) -> tuple[str, dict[str, Any]] | None:
    """(calendar id, event) of the show the records know: the topic's, else the one named by artist and day.

    The record says which event it is, with no search; one read of that event confirms it is still there and
    brings in anything changed by hand (the read refreshes the record). A show the calendar no longer has is
    forgotten, and so is not found.
    """
    record = find_record(svc, topic=topic, artist=artist, date=date)
    if record is None:
        return None
    try:
        event = svc.calendar.get_event(record.calendar_id, record.event_id)
    except ProviderError as e:
        log_warn(f"recorded show not read | error='{str(e.error)[:200]}'")
        return None
    if event.get("status") == "cancelled" or not event.get("id"):
        forget_event(record.event_id, svc.state, svc.settings.concerts_dir)
        return None
    return record.calendar_id, event


def _show_by_name(artist: str, date: str, svc: Services) -> tuple[str, dict[str, Any]] | None:
    """(calendar id, event) of the show found on the calendar by artist and day; asks for whichever is missing."""
    if not artist:
        raise need("artist")
    if not date:
        raise need("date")
    return event_for_show(artist, concert_date(date), svc)


def _kind(res: InviteResult) -> Kind:
    if res.status in ("created", "exists"):
        return Kind.OK
    if res.status == "dry_run":
        return Kind.DRY_RUN
    if res.status == "need_tickets" or res.phone_tail:
        return Kind.NEED_USER
    return Kind.ERROR


def tickets_outcome(
    tickets: str, artist: str, date: str, svc: Services, *, dry_run: bool = False, topic: str = ""
) -> Outcome:
    """The tickets_add tool: a show's ticket link arrived, so the show becomes a Partiful page.

    The show is the topic's event, else the one found by artist and date. The topic is the live chat's, unless the
    caller names one (`<chat>:<thread>`): a chat command knows which topic it was typed in and need not rely on
    which chat spoke last. `tickets` is the link as the user sent it (`https://…` or `tickets: https://…`); empty
    means "use the link the event already has", which is how the invite is asked for again after a login.
    """
    try:
        tickets, artist, date = tickets.strip(), artist.strip(), date.strip()
        link = ticket_link(tickets)
        if tickets and not link:
            raise FixInput(f"tickets holds no link: {tickets[:80]}", TICKETS_FIX)
        named_topic = bool(topic.strip())
        topic = topic.strip() or live_topic_key(svc)
        found = recorded_show(svc, topic=topic, artist=artist, date=concert_date(date) if date else "")
        if found is None:
            # No record of the show yet (it predates the records, or was added elsewhere): the topic registry and
            # the calendar find it, and it is recorded for the next time.
            entry = topic_event(topic, svc) if topic else None
            event = live_event(entry, svc) if entry else None
            if entry and event:
                calendar_id = entry.calendar_id
            else:
                # A caller that named the topic and nothing else meant "this topic's show": there is none.
                hit = None if named_topic and not (artist and date) else _show_by_name(artist, date, svc)
                if hit is None:
                    error = "no calendar event for the show"
                    return Outcome(Kind.NEED_USER, reply=PARTIFUL_NO_EVENT_REPLY, error=error)
                calendar_id, event = hit
                topic = None  # found by name, not through the topic: the topic does not own it
            remember_concert(event, calendar_id, svc, topic=topic)
        else:
            calendar_id, event = found
        res, note = invite_for_event(calendar_id, event, link, svc, dry_run=dry_run)
        kind = _kind(res)
        error = None if kind in (Kind.OK, Kind.DRY_RUN) else (res.reason or res.status)
        reply = render_partiful(res, svc.operator, event_note=note)
        # The step that completes the pair (event and page) makes the invite to-do; the record says if one is due.
        todo = None if dry_run else todo_step(event["id"], svc)
        if todo:
            reply = f"{reply}\n{todo}"
        return Outcome(kind, reply=reply, error=error, data=res.to_json())
    except (FixInput, NeedUser, ProviderError) as e:
        return outcome_from(e)
    except Exception as e:  # noqa: BLE001 - a tool never raises
        return crash_outcome(e, svc.operator)


def login_outcome(code: str, svc: Services, *, dry_run: bool = False) -> Outcome:
    """The partiful_login tool: no code texts one to the configured number; a code stores the session."""
    try:
        phone = svc.settings.partiful_phone
        if svc.partiful is None or not phone:
            reply = PARTIFUL_NO_PHONE.format(operator=svc.operator)
            return Outcome(Kind.ERROR, reply=reply, error="no Partiful phone number configured")
        if dry_run:
            return Outcome(Kind.DRY_RUN, reply=WOULD_LOGIN_REPLY)
        digits = re.sub(r"\D", "", code)
        if not digits:
            svc.partiful.send_code(phone)
            reply = render_partiful_code_sent(phone_tail(phone))
            return Outcome(Kind.NEED_USER, reply=reply, error="the login code is needed")
        svc.partiful.login(phone, digits)
        name = svc.partiful.me().get("name")
        return Outcome(Kind.OK, reply=render_partiful_signed_in(str(name) if name else None), data={"signed_in": True})
    except (FixInput, NeedUser, ProviderError) as e:
        return outcome_from(e)
    except Exception as e:  # noqa: BLE001 - a tool never raises
        return crash_outcome(e, svc.operator)
