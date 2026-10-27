"""The topic registry (unit flows-calendar): which event a Telegram forum topic owns.

`<state_dir>/topics.json` maps `<chat>:<thread>` to the event last added from that topic. `concert_add` reads it to
update instead of insert, writes it after every insert or update from a topic, and `event_delete` drops the entry of a
deleted event. A DM or the General topic has no key and is never registered.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from concert_mcp.flows.services import Services
from concert_mcp.result import ProviderError
from concert_mcp.text.topics import TopicEntry


def _rows(svc: Services) -> dict[str, Any]:
    raw = svc.state.read_json(svc.settings.topics_file, {})
    return dict(raw) if isinstance(raw, Mapping) else {}


def topic_event(key: str, svc: Services) -> TopicEntry | None:
    """The registry's entry for a topic, if any."""
    row = _rows(svc).get(key)
    if not isinstance(row, Mapping):
        return None
    entry = TopicEntry.from_json(row)
    return entry if entry.event_id and entry.calendar_id else None


def live_event(entry: TopicEntry, svc: Services) -> dict[str, Any] | None:
    """The event the entry points at, as the calendar has it now; None when it is gone or cancelled."""
    try:
        e = svc.calendar.get_event(entry.calendar_id, entry.event_id)
    except ProviderError:
        return None
    return None if (e.get("status") == "cancelled" or not e.get("id")) else e


def topic_remember(key: str, entry: TopicEntry, svc: Services) -> None:
    """Make `entry` the topic's event."""
    rows = _rows(svc)
    rows[key] = entry.to_json()
    svc.state.write_json(svc.settings.topics_file, rows)


def topic_forget(key: str, svc: Services) -> bool:
    """Drop a topic's entry; returns whether one was dropped."""
    rows = _rows(svc)
    if key not in rows:
        return False
    del rows[key]
    svc.state.write_json(svc.settings.topics_file, rows)
    return True


def topic_for_event(event_id: str, svc: Services) -> str | None:
    """The key of the topic that owns an event, if any; the weekly poll announces a playlist there."""
    for key, row in _rows(svc).items():
        if isinstance(row, Mapping) and row.get("event_id") == event_id:
            return str(key)
    return None


def topic_forget_event(event_id: str, svc: Services) -> bool:
    """Drop every entry pointing at a deleted event; returns whether any was dropped."""
    rows = _rows(svc)
    kept = {k: v for k, v in rows.items() if not (isinstance(v, Mapping) and v.get("event_id") == event_id)}
    if len(kept) == len(rows):
        return False
    svc.state.write_json(svc.settings.topics_file, kept)
    return True


def make_topic_entry(*, event_id: str, calendar_id: str, summary: str, start: datetime, now: datetime) -> TopicEntry:
    return TopicEntry(
        event_id=event_id,
        calendar_id=calendar_id,
        summary=summary,
        date=start.strftime("%Y-%m-%d"),
        updated=now.isoformat(timespec="seconds"),
    )
