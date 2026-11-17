"""The concert record's pure half (unit text-records): one JSON file per concert, and what it can tell.

A record is a local cache of everything known about one show: who, when, where, the fact lines the calendar event
carries, and the expressions made of it (the calendar event, the Telegram topic that owns it, the Partiful invite).
Adding a fact to a show is then a lookup, and the record alone says which expressions exist, which can now be made
and which no longer match the facts. The calendar stays the place a person edits; the record follows it.

A record's file is named `<YYYY-MM-DD>-<artist>.json`, and `<YYYY-MM-DD>-<HHMM>-<artist>.json` for a second show
by that artist on that day.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from concert_mcp.text.credit import CREATED_BY
from concert_mcp.text.partiful import (
    DOORS_PREFIX,
    EXPECTED_PREFIX,
    PARTIFUL_PREFIX,
    SETLIST_PLAYLIST_PREFIX,
    SETLIST_PREFIX,
    SETLISTFM_PREFIX,
    TEASER_PREFIX,
    TICKETS_PREFIX,
    invite_description,
    invite_title,
    line_value,
    show_links,
    ticket_link,
)
from concert_mcp.text.reply import event_link
from concert_mcp.text.venues import venue_name_of

# The invite to-do is made only for a show more than this many days away, and is due this many days before it.
TODO_LEAD_DAYS = 30
PURCHASED_PREFIX = "Purchased: "
OPENER_PREFIX = "Opener: "
VENUE_PREFIX = "Venue: "

# fact name -> the description line that carries it; `openers` is every Opener line, the rest the first of its kind.
FACT_LINES: Mapping[str, str] = {
    "doors": DOORS_PREFIX,
    "expected": EXPECTED_PREFIX,
    "purchased": PURCHASED_PREFIX,
    "venue_website": VENUE_PREFIX,
    "tickets": TICKETS_PREFIX,
    "teaser_playlist": TEASER_PREFIX,
    "setlist": SETLISTFM_PREFIX,
    "setlist_playlist": SETLIST_PLAYLIST_PREFIX,
    "partiful": PARTIFUL_PREFIX,
}


@dataclass(frozen=True)
class ConcertRecord:
    artist: str
    start: str  # ISO 8601, as the calendar has it
    location: str
    lines: tuple[str, ...]  # the calendar event's description, a fact per line
    calendar_id: str
    event_id: str
    end: str | None = None
    html_link: str | None = None
    topic: str | None = None  # "<chat>:<thread>" of the Telegram topic that owns the show
    invite: Mapping[str, Any] | None = None  # what the Partiful invite was made from: {url, title, description, links}
    # A guess at where the setlist will be (the artist's latest setlist.fm page when the concert was added). It is
    # not the show's page: that is the `setlist.fm:` line among `lines`, which the user gives.
    setlist_guess: str | None = None
    # The Google Task that reminds the owner to invite people: {id, link, task_list}; None until it is made.
    todo: Mapping[str, Any] | None = None
    updated: str = ""

    @property
    def date(self) -> str:
        return self.start[:10]

    def to_json(self) -> dict[str, Any]:
        return {
            "artist": self.artist,
            "start": self.start,
            "end": self.end,
            "location": self.location,
            "lines": list(self.lines),
            "calendar": {"calendar_id": self.calendar_id, "event_id": self.event_id, "html_link": self.html_link},
            "topic": self.topic,
            "invite": dict(self.invite) if self.invite else None,
            "setlist_guess": self.setlist_guess,
            "todo": dict(self.todo) if self.todo else None,
            "updated": self.updated,
        }

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> ConcertRecord:
        calendar = d.get("calendar") if isinstance(d.get("calendar"), Mapping) else {}
        invite = d.get("invite")
        todo = d.get("todo")
        return cls(
            artist=str(d.get("artist") or ""),
            start=str(d.get("start") or ""),
            end=str(d["end"]) if d.get("end") else None,
            location=str(d.get("location") or ""),
            lines=tuple(str(ln) for ln in d.get("lines") or ()),
            calendar_id=str(calendar.get("calendar_id") or ""),
            event_id=str(calendar.get("event_id") or ""),
            html_link=str(calendar["html_link"]) if calendar.get("html_link") else None,
            topic=str(d["topic"]) if d.get("topic") else None,
            invite=dict(invite) if isinstance(invite, Mapping) else None,
            setlist_guess=str(d["setlist_guess"]) if d.get("setlist_guess") else None,
            todo=dict(todo) if isinstance(todo, Mapping) else None,
            updated=str(d.get("updated") or ""),
        )


def artist_slug(artist: str) -> str:
    """'Rodrigo y Gabriela' -> 'rodrigo-y-gabriela': the artist as a file name's last part."""
    return re.sub(r"[^a-z0-9]+", "-", artist.lower()).strip("-") or "concert"


def record_name(start: str, artist: str, *, with_time: bool = False) -> str:
    """'2026-11-15-rodrigo-y-gabriela.json'; with the start time, '2026-11-15-1900-rodrigo-y-gabriela.json'."""
    clock = f"-{start[11:13]}{start[14:16]}" if with_time and len(start) >= 16 else ""
    return f"{start[:10]}{clock}-{artist_slug(artist)}.json"


def _when(value: Any) -> str:
    return str((value or {}).get("dateTime") or (value or {}).get("date") or "") if isinstance(value, Mapping) else ""


def record_of_event(
    event: Mapping[str, Any], calendar_id: str, *, now: str, known: ConcertRecord | None = None
) -> ConcertRecord:
    """The record of a calendar event as the calendar returned it. What the calendar does not hold (the topic, what
    the invite was made from, the setlist guess, the to-do) is kept from the record already known for the event."""
    return ConcertRecord(
        artist=str(event.get("summary") or ""),
        start=_when(event.get("start")),
        end=_when(event.get("end")) or None,
        location=str(event.get("location") or ""),
        lines=tuple(str(event.get("description") or "").splitlines()),
        calendar_id=calendar_id,
        event_id=str(event.get("id") or ""),
        html_link=str(event["htmlLink"]) if event.get("htmlLink") else (known.html_link if known else None),
        topic=known.topic if known else None,
        invite=known.invite if known else None,
        setlist_guess=known.setlist_guess if known else None,
        todo=known.todo if known else None,
        updated=now,
    )


def event_of_record(record: ConcertRecord) -> dict[str, Any]:
    """The record as a calendar event, for the flows that take one."""
    return {
        "id": record.event_id,
        "summary": record.artist,
        "start": {"dateTime": record.start},
        "end": {"dateTime": record.end} if record.end else {},
        "location": record.location,
        "description": "\n".join(record.lines),
        "htmlLink": record.html_link,
    }


def patched_record(
    record: ConcertRecord,
    *,
    now: str,
    summary: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    location: str | None = None,
    description: str | None = None,
) -> ConcertRecord:
    """The record after a calendar patch of the given fields (a None is left as it is)."""
    return replace(
        record,
        artist=record.artist if summary is None else summary,
        start=record.start if start is None else start.isoformat(),
        end=record.end if end is None else end.isoformat(),
        location=record.location if location is None else location,
        lines=record.lines if description is None else tuple(description.splitlines()),
        updated=now,
    )


def facts(record: ConcertRecord) -> dict[str, Any]:
    """What is known about the show, by name: only the facts it has."""
    out: dict[str, Any] = {"artist": record.artist, "start": record.start}
    if record.location:
        out["venue"] = venue_name_of(record.location)
        out["location"] = record.location
    for name, prefix in FACT_LINES.items():
        value = line_value(record.lines, prefix) or (
            line_value(record.lines, SETLIST_PREFIX) if name == "setlist" else None
        )
        if value:
            out[name] = value
    openers = [ln[len(OPENER_PREFIX) :].strip() for ln in record.lines if ln.startswith(OPENER_PREFIX)]
    if openers:
        out["openers"] = openers
    if record.setlist_guess and "setlist" not in out:
        # Only until the user gives the show's own page: a guess beside the real thing says nothing.
        out["setlist_guess"] = record.setlist_guess
    return out


def invite_plan(record: ConcertRecord) -> dict[str, Any] | None:
    """What a Partiful invite made from the record now would hold: {title, description, links}; None while the show
    has no ticket link. The same derivation the invite flow uses, so a stored plan that differs is out of date."""
    tickets = ticket_link(line_value(record.lines, TICKETS_PREFIX) or "")
    if not tickets:
        return None
    start = datetime.fromisoformat(record.start)
    fields = show_links(record.artist, record.lines)
    return {
        "title": invite_title(record.artist, venue_name_of(record.location), start),
        "description": invite_description(record.lines, start),
        "links": [[f["value"], f["url"]] for f in fields],
    }


def _same_plan(made: Mapping[str, Any], plan: Mapping[str, Any]) -> bool:
    return all(made.get(k) == plan[k] for k in ("title", "description", "links"))


@dataclass(frozen=True)
class TodoPlan:
    """The Google Task that reminds the owner to invite people to a show."""

    title: str
    notes: str
    due: str  # RFC 3339 at midnight UTC: the Tasks API keeps only the date of `due`


def todo_title(artist: str) -> str:
    """'invite people to Zed Fox', the owner's wording (2026-11-15)."""
    return f"invite people to {artist}"


def todo_notes(partiful: str, calendar: str) -> str:
    """The task's notes: plain text, since a task carries no labelled links; bare URLs are clickable there."""
    return f"Partiful: {partiful}\nCalendar: {calendar}\n\n{CREATED_BY}"


def todo_due(show_date: str) -> str:
    """The show's local day less TODO_LEAD_DAYS, as the Tasks API takes a date: '2026-12-16T00:00:00.000Z'."""
    day = date.fromisoformat(show_date) - timedelta(days=TODO_LEAD_DAYS)
    return f"{day.isoformat()}T00:00:00.000Z"


def todo_state(record: ConcertRecord, *, now: datetime) -> str:
    """Where the show's invite to-do stands: `made`, `can be made`, `needs the Partiful page`, `needs the calendar
    link`, or `not needed` when the show starts TODO_LEAD_DAYS days from `now` or sooner (or has passed)."""
    if record.todo:
        return "made"
    if "partiful" not in facts(record):
        return "needs the Partiful page"
    if not record.html_link:
        return "needs the calendar link"
    start = datetime.fromisoformat(record.start)
    if start.tzinfo is None:  # an all-day event: its day starts at midnight UTC as far as this rule cares
        start = start.replace(tzinfo=UTC)
    if start - now <= timedelta(days=TODO_LEAD_DAYS):
        return f"not needed ({TODO_LEAD_DAYS} days or less to go)"
    return "can be made"


def todo_plan(record: ConcertRecord, *, now: datetime) -> TodoPlan | None:
    """The invite to-do to make now, by rule: the show has a calendar event and a Partiful page, starts more than
    TODO_LEAD_DAYS days after `now`, and has no to-do yet. None otherwise."""
    if todo_state(record, now=now) != "can be made":
        return None
    return TodoPlan(
        title=todo_title(record.artist),
        notes=todo_notes(str(facts(record)["partiful"]), str(event_link(record.html_link))),
        due=todo_due(record.date),
    )


def expressions(record: ConcertRecord, *, now: datetime) -> dict[str, str]:
    """Every expression of the show and where it stands, from the record alone (and, for the to-do, the time).

    `made`, `not made`, `can be made` (the facts it needs are here), `needs the ticket link`, or `out of date` (it was
    made from facts that have since changed); the invite to-do as `todo_state` says.
    """
    known = facts(record)
    out = {
        "calendar": "made",
        "teaser playlist": "made" if "teaser_playlist" in known else "not made",
        "setlist playlist": "made" if "setlist_playlist" in known else "not made",
    }
    plan = invite_plan(record)
    if "partiful" in known:
        stale = bool(record.invite) and plan is not None and not _same_plan(record.invite or {}, plan)
        out["partiful"] = "out of date" if stale else "made"
    else:
        out["partiful"] = "can be made" if plan else "needs the ticket link"
    out["invite to-do"] = todo_state(record, now=now)
    return out


def same_show(record: ConcertRecord, artist: str, date: str) -> bool:
    """Whether the record is the show a caller names by artist and day."""
    return record.date == date and artist_slug(record.artist) == artist_slug(artist)


def record_rows(records: Sequence[ConcertRecord], *, now: datetime) -> list[tuple[ConcertRecord, dict[str, str]]]:
    """Each record with its expressions, soonest show first."""
    return [(r, expressions(r, now=now)) for r in sorted(records, key=lambda r: r.start)]
