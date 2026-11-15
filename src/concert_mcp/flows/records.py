"""The concert records on disk (unit flows-records): one JSON file per concert under `<state_dir>/concerts`.

A record is a cache of what is known about a show (`text/records.py`). `concert_add` writes it; every later write to
the show's calendar event goes through `CachedCalendar`, which keeps the record in step, so no flow has to remember
to; and a read of the event refreshes it, which is how a change made by hand in the calendar reaches the record.
Only shows that have a record are followed: plain events and movies never get one.

Nothing here raises for a record that cannot be read or written, except the calendar's own errors, which pass
through `CachedCalendar` untouched: the record is a cache, and the calendar call it shadows must not fail for it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from concert_mcp.adapters.protocols import Calendar, Clock, StateStore
from concert_mcp.log import log_warn
from concert_mcp.result import Kind, Outcome
from concert_mcp.text.description import PlainDescription
from concert_mcp.text.records import (
    ConcertRecord,
    patched_record,
    record_name,
    record_of_event,
    record_rows,
    same_show,
)
from concert_mcp.text.reply import render_concerts

if TYPE_CHECKING:
    from concert_mcp.flows.services import Services


def all_records(state: StateStore, directory: Path) -> list[tuple[Path, ConcertRecord]]:
    """Every readable record with its file, in file-name order (which is date order)."""
    out: list[tuple[Path, ConcertRecord]] = []
    for name in state.names(directory):
        raw = state.read_json(directory / name, None)
        if isinstance(raw, Mapping):
            record = ConcertRecord.from_json(raw)
            if record.event_id:
                out.append((directory / name, record))
    return out


def record_for_event(event_id: str, state: StateStore, directory: Path) -> tuple[Path, ConcertRecord] | None:
    """The record of a calendar event, with its file."""
    return next((hit for hit in all_records(state, directory) if hit[1].event_id == event_id), None)


def save_record(record: ConcertRecord, state: StateStore, directory: Path) -> Path:
    """Write the record under its name; a second show by the artist that day gets its start time in the name.

    A record whose artist or day changed (a title fixed by a repeat add) moves to its new name.
    """
    known = record_for_event(record.event_id, state, directory)
    path = directory / record_name(record.start, record.artist)
    other = state.read_json(path, None)
    if isinstance(other, Mapping) and ConcertRecord.from_json(other).event_id != record.event_id:
        path = directory / record_name(record.start, record.artist, with_time=True)
    if known and known[0] != path:
        state.remove(known[0])
    state.write_json(path, record.to_json())
    return path


def forget_event(event_id: str, state: StateStore, directory: Path) -> bool:
    """Drop the record of a deleted event; whether there was one."""
    known = record_for_event(event_id, state, directory)
    if known:
        state.remove(known[0])
    return known is not None


# ---------- what the flows call ----------


def remember_concert(event: Mapping[str, Any], calendar_id: str, svc: Services, *, topic: str | None = None) -> None:
    """Record a show from its calendar event (as the calendar returned it). Never raises."""
    try:
        directory = svc.settings.concerts_dir
        known = record_for_event(str(event.get("id") or ""), svc.state, directory)
        record = record_of_event(event, calendar_id, now=svc.clock.now().isoformat(), known=known[1] if known else None)
        if not record.event_id or not record.start:
            return
        if topic:
            record = replace(record, topic=topic)
        save_record(record, svc.state, directory)
    except Exception as e:  # noqa: BLE001 - a cache that cannot be written is a log line, never a failed add
        log_warn(f"concert record not written | error='{type(e).__name__}: {str(e)[:200]}'")


def remember_invite(event_id: str, invite: Mapping[str, Any], svc: Services) -> None:
    """Keep what the show's Partiful invite was made from, so a later fact can tell it is out of date. Never raises."""
    try:
        known = record_for_event(event_id, svc.state, svc.settings.concerts_dir)
        if known:
            save_record(replace(known[1], invite=dict(invite)), svc.state, svc.settings.concerts_dir)
    except Exception as e:  # noqa: BLE001
        log_warn(f"concert record not updated | error='{type(e).__name__}: {str(e)[:200]}'")


def find_record(svc: Services, *, topic: str | None = None, artist: str = "", date: str = "") -> ConcertRecord | None:
    """The show a caller means: the one the Telegram topic owns, else the one named by artist and day."""
    try:
        records = [r for _path, r in all_records(svc.state, svc.settings.concerts_dir)]
    except Exception as e:  # noqa: BLE001 - an unreadable cache is an empty cache
        log_warn(f"concert records not read | error='{type(e).__name__}: {str(e)[:200]}'")
        return None
    if topic:
        owned = [r for r in records if r.topic == topic]
        if owned:
            return max(owned, key=lambda r: r.updated)
    if artist and date:
        return next((r for r in records if same_show(r, artist, date)), None)
    return None


def concerts_outcome(svc: Services) -> Outcome:
    """`concert-mcp concerts`: every recorded show and where each of its expressions stands."""
    rows = record_rows([r for _path, r in all_records(svc.state, svc.settings.concerts_dir)])
    data = {"concerts": [{**r.to_json(), "expressions": e} for r, e in rows]}
    return Outcome(Kind.OK, reply=render_concerts(rows), data=data)


class CachedCalendar:
    """A Calendar that keeps the concert records in step with the events they shadow.

    Every method is the wrapped calendar's. A patch, a delete or a read of an event that has a record updates,
    drops or refreshes that record afterwards; an event without one is left alone.
    """

    def __init__(self, inner: Calendar, state: StateStore, directory: Path, clock: Clock) -> None:
        self._inner = inner
        self._state = state
        self._directory = directory
        self._clock = clock

    def _follow(self, event_id: str, update: Callable[[ConcertRecord, str], ConcertRecord]) -> None:
        """Apply `update` to the event's record, when it has one. Never raises."""
        try:
            known = record_for_event(event_id, self._state, self._directory)
            if known:
                save_record(update(known[1], self._clock.now().isoformat()), self._state, self._directory)
        except Exception as e:  # noqa: BLE001 - the calendar call already happened; the cache only lags
            log_warn(f"concert record not updated | error='{type(e).__name__}: {str(e)[:200]}'")

    def list_calendars(self) -> list[dict[str, Any]]:
        return self._inner.list_calendars()

    def list_events(
        self, calendar_id: str, *, time_min: datetime, time_max: datetime, q: str = "", max_results: int = 50
    ) -> list[dict[str, Any]]:
        return self._inner.list_events(calendar_id, time_min=time_min, time_max=time_max, q=q, max_results=max_results)

    def get_event(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        event = self._inner.get_event(calendar_id, event_id)
        self._follow(event_id, lambda known, now: record_of_event(event, calendar_id, now=now, known=known))
        return event

    def insert_event(
        self,
        calendar_id: str,
        *,
        summary: str,
        start: datetime,
        end: datetime,
        location: str,
        description: PlainDescription | None,
        attendees: Sequence[str],
    ) -> dict[str, Any]:
        return self._inner.insert_event(
            calendar_id,
            summary=summary,
            start=start,
            end=end,
            location=location,
            description=description,
            attendees=attendees,
        )

    def patch_event(
        self,
        calendar_id: str,
        event_id: str,
        *,
        summary: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        location: str | None = None,
        description: PlainDescription | None = None,
    ) -> None:
        self._inner.patch_event(
            calendar_id, event_id, summary=summary, start=start, end=end, location=location, description=description
        )
        self._follow(
            event_id,
            lambda known, now: patched_record(
                known, now=now, summary=summary, start=start, end=end, location=location, description=description
            ),
        )

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        self._inner.delete_event(calendar_id, event_id)
        try:
            forget_event(event_id, self._state, self._directory)
        except Exception as e:  # noqa: BLE001
            log_warn(f"concert record not removed | error='{type(e).__name__}: {str(e)[:200]}'")
