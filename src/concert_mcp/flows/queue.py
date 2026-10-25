"""The weekly setlist check queue (unit flows-calendar).

One JSON list, the shape the legacy `cal-add.py` wrote and the weekly poller reads. Every concert that lands on the
calendar is queued (a setlist.fm page fills in over weeks, so the poller keeps checking until `until` even when songs
are already there); a delete forgets the row.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from concert_mcp.flows.services import Services
from concert_mcp.result import Kind, Outcome
from concert_mcp.text.reply import render_queue
from concert_mcp.text.setlists import SetlistResult


@dataclass(frozen=True)
class QueueEntry:
    event_id: str
    calendar_id: str
    artist: str
    date: str
    venue: str
    setlistfm_url: str | None
    song_count: int
    until: str
    added: str

    def to_json(self) -> dict[str, Any]:
        """The legacy queue row."""
        return {
            "event_id": self.event_id,
            "calendar_id": self.calendar_id,
            "artist": self.artist,
            "date": self.date,
            "venue": self.venue,
            "setlistfm_url": self.setlistfm_url,
            "song_count": self.song_count,
            "until": self.until,
            "added": self.added,
        }

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> QueueEntry:
        """Inverse of to_json; a missing key becomes its empty value."""
        url = d.get("setlistfm_url")
        try:
            song_count = int(d.get("song_count") or 0)
        except (TypeError, ValueError):
            song_count = 0
        return cls(
            event_id=str(d.get("event_id") or ""),
            calendar_id=str(d.get("calendar_id") or ""),
            artist=str(d.get("artist") or ""),
            date=str(d.get("date") or ""),
            venue=str(d.get("venue") or ""),
            setlistfm_url=str(url) if url else None,
            song_count=song_count,
            until=str(d.get("until") or ""),
            added=str(d.get("added") or ""),
        )


@dataclass(frozen=True)
class QueueStatus:
    entries: tuple[QueueEntry, ...]
    active: tuple[QueueEntry, ...]
    expired: tuple[QueueEntry, ...]
    path: Path


def make_entry(
    *,
    event_id: str,
    calendar_id: str,
    artist: str,
    start: datetime,
    venue: str,
    setlist: SetlistResult | None,
    now: datetime,
    days: int,
) -> QueueEntry:
    """A queue row for a show, checked until `days` after it."""
    found = setlist is not None and setlist.found
    return QueueEntry(
        event_id=event_id,
        calendar_id=calendar_id,
        artist=artist,
        date=start.strftime("%Y-%m-%d"),
        venue=venue,
        setlistfm_url=setlist.url if found else None,
        song_count=setlist.song_count if found else 0,
        until=(start + timedelta(days=days)).strftime("%Y-%m-%d"),
        added=now.isoformat(timespec="seconds"),
    )


def _raw_rows(svc: Services) -> list[Any]:
    """The queue file as stored: a list, or nothing when the file is absent or not a list."""
    raw = svc.state.read_json(svc.settings.setlist_queue, [])
    return list(raw) if isinstance(raw, list) else []


def _is_row_for(row: Any, event_id: str) -> bool:
    return isinstance(row, Mapping) and row.get("event_id") == event_id


def queue_list(svc: Services) -> list[QueueEntry]:
    """Every readable row of the queue file."""
    out: list[QueueEntry] = []
    for row in _raw_rows(svc):
        if not isinstance(row, Mapping):
            continue
        try:
            out.append(QueueEntry.from_json(row))
        except Exception:
            continue
    return out


def queue_add(entry: QueueEntry, svc: Services) -> bool:
    """Append unless the event is already queued; returns whether it was added.

    Rows the reader could not parse are carried over untouched: this file belongs to the poller as much as to us.
    """
    rows = _raw_rows(svc)
    if any(_is_row_for(r, entry.event_id) for r in rows):
        return False
    rows.append(entry.to_json())
    svc.state.write_json(svc.settings.setlist_queue, rows)
    return True


def queue_forget(event_id: str, svc: Services) -> bool:
    """Drop the row for an event; returns whether one was dropped."""
    rows = _raw_rows(svc)
    kept = [r for r in rows if not _is_row_for(r, event_id)]
    if len(kept) == len(rows):
        return False
    svc.state.write_json(svc.settings.setlist_queue, kept)
    return True


def _until_date(entry: QueueEntry) -> date | None:
    try:
        return date.fromisoformat(entry.until[:10])
    except ValueError:
        return None


def queue_status(svc: Services) -> QueueStatus:
    """Rows split into active and expired by their `until` date (today in the configured zone)."""
    today = svc.clock.now().astimezone(svc.tz).date()
    entries = sorted(queue_list(svc), key=lambda e: e.date)
    active = tuple(e for e in entries if (u := _until_date(e)) is not None and u >= today)
    expired = tuple(e for e in entries if e not in active)
    return QueueStatus(entries=tuple(entries), active=active, expired=expired, path=svc.settings.setlist_queue)


def queue_outcome(svc: Services) -> Outcome:
    """queue_status's Outcome with the rendered list."""
    status = queue_status(svc)
    return Outcome(
        Kind.OK,
        reply=render_queue(status),
        data={"count": len(status.entries), "entries": [e.to_json() for e in status.entries]},
    )
