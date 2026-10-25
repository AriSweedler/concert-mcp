"""The concert and plain-event add flows (unit flows-calendar).

`prepare_event` is the pure half (validation and derivation in the legacy `cal-add.py` order); `add_event` is the
orchestration: teaser, setlist lookup, duplicate guard, insert, queue, topic rename. Flows return an Outcome and never
raise, print or exit; `flows/deliver.py` turns the Outcome into text.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from concert_mcp.config import Settings
from concert_mcp.flows.queue import make_entry, queue_add
from concert_mcp.flows.services import Services
from concert_mcp.flows.setlist import find_setlist
from concert_mcp.flows.teaser import make_teaser
from concert_mcp.log import log_warn
from concert_mcp.result import FixInput, Kind, NeedUser, Outcome, ProviderError, crash_outcome, need, outcome_from
from concert_mcp.text.candidates import duplicate_of, missing_link_lines, patched_description
from concert_mcp.text.dates import (
    apply_show_time,
    check_duration,
    check_window,
    default_end,
    fmt_range,
    is_date_only,
    parse_time,
    start_from_date,
    validate_tz,
)
from concert_mcp.text.derive import (
    build_source,
    check_attendees,
    coerce_description,
    derived_lines,
    merge_lines,
    openers_from_text,
)
from concert_mcp.text.naming import topic_name
from concert_mcp.text.reply import (
    dropped_note,
    render_added,
    render_body,
    render_dry_add,
    setlist_bullet,
    setlist_line,
    teaser_bullet,
    teaser_line,
)
from concert_mcp.text.routes import chat_target, forum_target
from concert_mcp.text.titles import split_openers, squash, strip_concert_prefix

ALIASES = {
    "artist": "title",
    "summary": "title",
    "datetime": "start",
    "timezone": "tz",
    "venue": "location",
    "notes": "description",
    "text": "source",
}
KNOWN = frozenset(
    {
        "title",
        "start",
        "end",
        "tz",
        "calendar",
        "location",
        "description",
        "attendees",
        "source",
        "allowPast",
        "date",
        "time",
    }
)

NO_LIVE_CHAT = "topic not renamed: no live chat"
NOT_FORUM_TOPIC = "topic not renamed: not a Telegram forum topic"


def _text(v: object) -> str:
    return str(v or "")


def _optional(v: object) -> str | None:
    s = _text(v).strip()
    return s or None


@dataclass(frozen=True)
class AddRequest:
    title: str = ""
    start: str = ""
    location: str = ""
    end: str | None = None
    tz: str | None = None
    calendar: str | None = None
    description: tuple[str, ...] = ()
    attendees: tuple[str, ...] = ()
    source: str = ""
    allow_past: bool = False
    concert: bool = False
    teaser: bool = True
    dry_run: bool = False

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        concert: bool = False,
        teaser: bool = True,
        dry_run: bool = False,
    ) -> AddRequest:
        """Fold aliases, refuse unknown keys (FixInput), coerce description and attendees."""
        if not isinstance(raw, Mapping):
            raise FixInput("input must be a JSON object", "send one {...} object")
        ev = {ALIASES.get(k, k): v for k, v in raw.items()}
        unknown = sorted(set(ev) - KNOWN)
        if unknown:
            raise FixInput(
                f"unknown keys: {', '.join(unknown)}",
                f"allowed keys: {', '.join(sorted(KNOWN - {'date', 'time'}))}",
            )
        start = ev.get("start")
        if "date" in ev and "time" in ev and not start:
            start = f"{ev['date']} {ev['time']}"
        return cls(
            title=_text(ev.get("title")),
            start=_text(start),
            location=_text(ev.get("location")),
            end=_optional(ev.get("end")),
            tz=_optional(ev.get("tz")),
            calendar=_optional(ev.get("calendar")),
            description=tuple(coerce_description(ev.get("description"))),
            attendees=tuple(check_attendees(ev.get("attendees"))),
            source=_text(ev.get("source")),
            allow_past=bool(ev.get("allowPast")),
            concert=concert,
            teaser=teaser,
            dry_run=dry_run,
        )


@dataclass(frozen=True)
class PreparedEvent:
    title: str
    openers: tuple[str, ...]
    start: datetime
    end: datetime
    end_assumed: bool
    tz: str
    location: str
    lines: tuple[str, ...]
    dropped: tuple[str, ...]
    notes: tuple[str, ...]
    attendees: tuple[str, ...]
    topic: str
    raw_source: str
    source_norm: str | None


def prepare_event(req: AddRequest, *, context_text: str, now: datetime, settings: Settings) -> PreparedEvent:
    """Pure validation and derivation in the legacy order; raises FixInput / NeedUser."""
    raw_source, source_norm = build_source(req.source, context_text)
    tz = validate_tz(req.tz or settings.tz)

    title, openers = squash(req.title), []
    if req.concert:
        # "Artist with special guest X": the opener is a description line, not part of the title. A plain event's
        # title ("[Dinner] Team dinner with Sam") stays as the user wrote it.
        split = split_openers(title)
        title, openers = strip_concert_prefix(split.title), list(split.openers)
    if not title:
        raise need("artist" if req.concert else "title")
    if not req.start:
        raise need("date and time")

    notes: list[str] = []
    if req.concert and is_date_only(req.start):
        # The caller only knew the day; the ticket text states the show time.
        start, note = start_from_date(req.start, raw_source, tz)
        notes.append(note)
    else:
        start = parse_time(req.start, tz, "start")
    end = parse_time(req.end, tz, "end") if req.end else default_end(start, settings.default_duration_h)
    end_assumed = not req.end
    check_duration(start, end)
    check_window(
        start,
        now,
        allow_past=req.allow_past,
        past_grace_h=settings.past_grace_h,
        max_future_days=settings.max_future_days,
    )

    location = squash(req.location)
    if not location:
        raise need("venue")

    derived: list[str] = []
    if req.concert:
        # The ticket parsers read the live context (the last OCR, the recent chat), which a plain event added minutes
        # after a ticket must not inherit: no show-time check, no Doors/Purchased/Opener lines. The caller's own lines
        # are still verified against that text below.
        start, end, more = apply_show_time(start, end, end_assumed, raw_source)
        notes += more
        derived = derived_lines(raw_source)
        if not openers:
            openers = openers_from_text(raw_source)
    lines, dropped = merge_lines(
        derived=derived,
        openers=openers,
        caller=req.description,
        source_norm=source_norm,
        title=title,
    )
    return PreparedEvent(
        title=title,
        openers=tuple(openers),
        start=start,
        end=end,
        end_assumed=end_assumed,
        tz=tz,
        location=location,
        lines=tuple(lines),
        dropped=tuple(dropped),
        notes=tuple(notes),
        attendees=tuple(req.attendees),
        topic=topic_name(start, title, location),
        raw_source=raw_source,
        source_norm=source_norm,
    )


def resolve_calendar(name: str | None, svc: Services, *, dry_run: bool) -> tuple[str, str]:
    """(calendar id, calendar name) for a name, an id, or the default."""
    s = svc.settings
    if not name or name.lower() == "primary":
        return s.default_calendar, s.default_calendar_name
    if "@" in name:
        return name, name
    if dry_run:
        return f"<id of {name}>", name
    items = svc.calendar.list_calendars()
    hits = [c for c in items if name.lower() in (c.get("summary") or "").lower()]
    if len(hits) != 1:
        names = ", ".join(sorted(c.get("summary", "") for c in items))
        raise FixInput(f"calendar {name!r} matched {len(hits)} calendars", f"use one of: {names}")
    return hits[0]["id"], hits[0]["summary"]


def add_event(req: AddRequest, svc: Services) -> Outcome:
    """The whole add flow: prepare, teaser, setlist, duplicate guard, insert, queue, topic rename. Never raises."""
    try:
        return _add_event(req, svc)
    except (FixInput, NeedUser, ProviderError) as e:
        return outcome_from(e)
    except Exception as e:  # noqa: BLE001 - a crash becomes a reply, never a traceback on the MCP stream
        return crash_outcome(e, svc.operator)


def _add_event(req: AddRequest, svc: Services) -> Outcome:
    s = svc.settings
    now_s = svc.clock.time()
    now = svc.clock.now()
    sid, route = svc.context.live_route(now_s)
    context = svc.context.recent_text(now_s, sid)

    p = prepare_event(req, context_text=context, now=now, settings=s)
    cal_id, cal_name = resolve_calendar(s.concerts_calendar if req.concert else req.calendar, svc, dry_run=req.dry_run)
    date = p.start.strftime("%Y-%m-%d")

    # The teaser runs before the insert so one insert carries its link; neither it nor the lookup is ever fatal.
    teaser = make_teaser(p.title, date, svc, dry_run=req.dry_run) if (req.concert and req.teaser) else None
    setlist = find_setlist(p.title, date, p.location, svc) if req.concert else None
    t_line, s_line = teaser_line(teaser), setlist_line(setlist)
    lines = list(p.lines) + [ln for ln in (t_line, s_line) if ln]

    when = fmt_range(p.start, p.end) + (" (end assumed)" if p.end_assumed else "")
    body = render_body(
        p.title,
        when,
        p.location,
        lines,
        [teaser_bullet(teaser, timeout_s=s.teaser_timeout_s, dry=req.dry_run), setlist_bullet(setlist)],
    )
    notes = list(p.notes) + ([dropped_note(p.dropped)] if p.dropped else [])
    common: dict[str, Any] = {
        "calendar": cal_name,
        "summary": p.title,
        "start": p.start.isoformat(),
        "end": p.end.isoformat(),
        "location": p.location,
        "description": lines,
        "dropped": list(p.dropped),
        "teaser": teaser.to_json() if teaser else None,
        "setlist": setlist.to_json() if setlist else None,
        "topic": p.topic,
    }
    if req.dry_run:
        return Outcome(Kind.DRY_RUN, reply=render_dry_add(cal_name, body, notes), data=common)

    def finish(event_id: str | None, html_link: str | None, duplicate: bool) -> Outcome:
        """Queue the show for the setlist poller, rename the Telegram topic, build the reply."""
        more_notes = list(notes)
        if req.concert and event_id:
            # Every concert is queued (2026-10-25): the page fills in over weeks, so the poller keeps checking.
            entry = make_entry(
                event_id=event_id,
                calendar_id=cal_id,
                artist=p.title,
                start=p.start,
                venue=p.location,
                setlist=setlist,
                now=now,
                days=s.setlist_queue_days,
            )
            # The event exists by now; a queue write that fails is a note on the reply, never a crash without the link.
            try:
                queue_add(entry, svc)
            except Exception as e:  # noqa: BLE001 - the reply with the calendar link matters more than the queue
                log_warn(f"setlist queue not written | error='{type(e).__name__}: {e}'")
                more_notes.append(f"Not queued for the setlist check: {type(e).__name__}")
        if req.concert and route and s.send and s.rename_topics:
            ft = forum_target(chat_target(route))
            topic_status = svc.chat.rename_topic(ft[0], ft[1], p.topic) if ft else NOT_FORUM_TOPIC
        else:
            topic_status = NO_LIVE_CHAT
        reply = render_added(calendar=cal_name, body=body, notes=more_notes, html_link=html_link, duplicate=duplicate)
        return Outcome(
            Kind.DUPLICATE if duplicate else Kind.OK,
            reply=reply,
            data={**common, "id": event_id, "htmlLink": html_link, "topic_status": topic_status},
        )

    # Duplicate guard: a retried insert must not create a second event.
    existing = svc.calendar.list_events(cal_id, time_min=p.start, time_max=p.start + timedelta(minutes=1), q=p.title)
    e = duplicate_of(existing, p.title)
    if e is not None:
        # The event predates the playlist or the setlist page: add the missing link lines to its description.
        missing = missing_link_lines(e.get("description"), [t_line, s_line])
        if missing:
            svc.calendar.patch_description(cal_id, e["id"], patched_description(e.get("description"), missing))
        return finish(e.get("id"), e.get("htmlLink"), True)

    r = svc.calendar.insert_event(
        cal_id,
        summary=p.title,
        start=p.start,
        end=p.end,
        location=p.location,
        description="\n".join(lines) if lines else None,
        attendees=p.attendees,
    )
    return finish(r.get("id"), r.get("htmlLink") or "", False)


def _note_lines(notes: str) -> tuple[str, ...]:
    return tuple(ln for ln in notes.splitlines() if ln.strip())


def add_concert(
    *,
    artist: str,
    start: str,
    venue: str,
    source_text: str = "",
    notes: str = "",
    svc: Services,
    dry_run: bool = False,
) -> Outcome:
    """concert_add's entry: an AddRequest on the Concerts calendar with teaser and setlist."""
    req = AddRequest(
        title=artist,
        start=start,
        location=venue,
        source=source_text,
        description=_note_lines(notes),
        concert=True,
        dry_run=dry_run,
    )
    return add_event(req, svc)


def add_plain(
    *,
    title: str,
    start: str,
    location: str,
    notes: str = "",
    calendar: str = "",
    svc: Services,
    dry_run: bool = False,
) -> Outcome:
    """calendar_add's entry: a plain event on the personal (or named) calendar."""
    req = AddRequest(
        title=title,
        start=start,
        location=location,
        calendar=calendar or None,
        description=_note_lines(notes),
        concert=False,
        dry_run=dry_run,
    )
    return add_event(req, svc)
