"""The delete flow: find by title and date, numbered candidates, pick (unit flows-calendar).

The legacy `cal-del.py` as a function over Services: one match is removed, several are listed with numbers and kept
in the candidates file for the next call's `pick`, none is reported. Never raises, prints or exits.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any
from zoneinfo import ZoneInfo

from concert_mcp.flows.concert_add import resolve_calendar
from concert_mcp.flows.queue import queue_forget
from concert_mcp.flows.services import Services
from concert_mcp.flows.topics import topic_forget_event
from concert_mcp.result import FixInput, Kind, NeedUser, Outcome, ProviderError, crash_outcome, outcome_from
from concert_mcp.text.candidates import (
    Candidate,
    candidate_from_event,
    filter_candidates,
    pick_index,
    search_window,
)
from concert_mcp.text.dates import validate_tz
from concert_mcp.text.reply import (
    NO_CANDIDATES_REPLY,
    WHICH_EVENT_REPLY,
    render_candidates,
    render_none_found,
    render_removed,
    render_would_look,
)
from concert_mcp.text.titles import looks_like_pick, squash

DELETE_ALIASES = {
    "artist": "title",
    "summary": "title",
    "event": "title",
    "name": "title",
    "venue": "location",
    "when": "date",
    "day": "date",
    "cal": "calendar",
    "eventId": "id",
    "number": "pick",
    "choice": "pick",
}
DELETE_KNOWN = frozenset({"title", "date", "calendar", "id", "pick", "location", "tz"})


def _optional(v: object) -> str | None:
    s = str(v or "").strip()
    return s or None


@dataclass(frozen=True)
class DeleteRequest:
    title: str = ""
    date: str | None = None
    calendar: str | None = None
    id: str | None = None
    pick: str | None = None
    tz: str | None = None
    dry_run: bool = False

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, dry_run: bool = False) -> DeleteRequest:
        """Fold DELETE_ALIASES, refuse unknown keys (FixInput). `location` is accepted and ignored (legacy)."""
        if not isinstance(raw, Mapping):
            raise FixInput("input must be a JSON object", "send one {...} object")
        ev = {DELETE_ALIASES.get(k, k): v for k, v in raw.items()}
        unknown = sorted(set(ev) - DELETE_KNOWN)
        if unknown:
            raise FixInput(f"unknown keys: {', '.join(unknown)}", f"use only: {', '.join(sorted(DELETE_KNOWN))}")
        return cls(
            title=str(ev.get("title") or ""),
            date=_optional(ev.get("date")),
            calendar=_optional(ev.get("calendar")),
            id=_optional(ev.get("id")),
            pick=_optional(ev.get("pick")),
            tz=_optional(ev.get("tz")),
            dry_run=dry_run,
        )


def _zone(req: DeleteRequest, svc: Services) -> ZoneInfo:
    return ZoneInfo(validate_tz(req.tz or svc.settings.tz))


def calendar_pairs(req: DeleteRequest, svc: Services) -> list[tuple[str, str]]:
    """(id, name) of the calendars to search: the named one, else Concerts and the default calendar."""
    s = svc.settings
    if req.calendar:
        return [resolve_calendar(req.calendar, svc, dry_run=req.dry_run)]
    default = (s.default_calendar, s.default_calendar_name)
    if req.dry_run:
        return [(f"<id of {s.concerts_calendar}>", s.concerts_calendar), default]
    return [resolve_calendar(s.concerts_calendar, svc, dry_run=False), default]


def calendar_names(req: DeleteRequest, svc: Services) -> list[str]:
    """How the reply names the calendars searched."""
    if req.calendar:
        return [req.calendar]
    return [svc.settings.concerts_calendar, svc.settings.default_calendar_name]


def find_candidates(req: DeleteRequest, svc: Services) -> list[Candidate]:
    """Matching events on the named calendar or on Concerts + the default calendar, sorted by start."""
    tz = _zone(req, svc)
    t_min, t_max = search_window(req.date, svc.clock.now(), tz)
    found: list[Candidate] = []
    for cal_id, cal_name in calendar_pairs(req, svc):
        if req.dry_run:
            continue
        items = svc.calendar.list_events(cal_id, time_min=t_min, time_max=t_max, q=req.title, max_results=50)
        found += filter_candidates(items, req.title, cal_id, cal_name, tz)
    return sorted(found, key=lambda c: c.start)


def remove(c: Candidate, svc: Services, *, dry_run: bool) -> None:
    """Delete the event and forget it in the setlist queue and the topic registry.

    The poller must forget the show, and the topic that owned the event must be free to own the next one.
    """
    if dry_run:
        return
    svc.calendar.delete_event(c.calendar_id, c.id)
    # The event is gone; a state-file hiccup must not turn that into a failure.
    with contextlib.suppress(Exception):
        queue_forget(c.id, svc)
    with contextlib.suppress(Exception):
        topic_forget_event(c.id, svc)


def removed_outcome(c: Candidate, dry: bool) -> Outcome:
    return Outcome(
        Kind.DRY_RUN if dry else Kind.OK,
        reply=render_removed(c, dry),
        data={"deleted": not dry, **c.to_json()},
    )


def _stored_candidates(svc: Services) -> list[Candidate]:
    rows = svc.state.read_json(svc.settings.candidates_file, [])
    if not isinstance(rows, list):
        return []
    try:
        return [Candidate.from_json(d) for d in rows]
    except Exception:  # noqa: BLE001 - an unreadable list is no list
        return []


def delete_event(req: DeleteRequest, svc: Services) -> Outcome:
    """The whole delete flow: pick, id, title search, one match / list / none. Never raises."""
    try:
        return _delete_event(req, svc)
    except (FixInput, NeedUser, ProviderError) as e:
        return outcome_from(e)
    except Exception as e:  # noqa: BLE001 - a crash becomes a reply, never a traceback on the MCP stream
        return crash_outcome(e, svc.operator)


def _delete_event(req: DeleteRequest, svc: Services) -> Outcome:
    s = svc.settings
    dry = req.dry_run
    pick = req.pick
    title = squash(req.title)
    # A numbered list lives one context window; after that a "delete 2" must not reach into a list the user ignored.
    mtime = svc.state.mtime(s.candidates_file)
    fresh = mtime is not None and svc.clock.time() - mtime <= s.context_window_s
    if mtime is not None and not fresh:
        svc.state.remove(s.candidates_file)
    # A small model answers a numbered list by putting the number in `title`; while a list is waiting, that is a pick.
    if not pick and looks_like_pick(title) and mtime is not None:
        if not fresh:
            return Outcome(Kind.NEED_USER, reply=NO_CANDIDATES_REPLY, error="candidates list expired")
        pick, title = title, ""

    if pick:
        n = pick_index(pick)
        cands = _stored_candidates(svc) if fresh else []
        if n is None or not 1 <= n <= len(cands):
            return Outcome(Kind.NEED_USER, reply=NO_CANDIDATES_REPLY, error="no such candidate")
        c = cands[n - 1]
        remove(c, svc, dry_run=dry)
        svc.state.remove(s.candidates_file)
        return removed_outcome(c, dry)

    if req.id:
        cal_id, cal_name = resolve_calendar(req.calendar or s.concerts_calendar, svc, dry_run=dry)
        e = svc.calendar.get_event(cal_id, req.id)
        c = replace(candidate_from_event(e, cal_id, cal_name, _zone(req, svc)), id=req.id)
        remove(c, svc, dry_run=dry)
        return removed_outcome(c, dry)

    if not title:
        return Outcome(Kind.NEED_USER, reply=WHICH_EVENT_REPLY, error="title is required")

    found = find_candidates(replace(req, title=title), svc)
    names = calendar_names(req, svc)
    if not found:
        if dry:
            return Outcome(Kind.DRY_RUN, reply=render_would_look(title, req.date, names))
        return Outcome(Kind.OK, reply=render_none_found(title, req.date, names), data={"deleted": False})
    if len(found) == 1:
        remove(found[0], svc, dry_run=dry)
        return removed_outcome(found[0], dry)

    rows = [c.to_json() for c in found]
    svc.state.write_json(s.candidates_file, rows)
    return Outcome(Kind.OK, reply=render_candidates(title, found), data={"deleted": False, "candidates": rows})
