"""The topic registry's pure half: keys, entries, and what an update keeps (unit text-render).

A Telegram forum topic owns one concert event. The registry maps a topic key to that event so a second
`concert_add` in the same topic updates the event instead of creating another.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# A description line the ticket text cannot supply: kept from the old description when the new lines lack it,
# so a Spotify or setlist.fm hiccup during an update never erases a link the event already had.
KEPT_LINE_PREFIXES = ("Teaser playlist: ", "Setlist: ", "Setlist playlist: ", "Venue: ")


def topic_key(chat: str, thread: str) -> str:
    """'<chat>:<thread>': the one id a topic has."""
    return f"{chat}:{thread}"


@dataclass(frozen=True)
class TopicEntry:
    event_id: str
    calendar_id: str
    summary: str
    date: str
    updated: str

    def to_json(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "calendar_id": self.calendar_id,
            "summary": self.summary,
            "date": self.date,
            "updated": self.updated,
        }

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> TopicEntry:
        return cls(
            event_id=str(d.get("event_id") or ""),
            calendar_id=str(d.get("calendar_id") or ""),
            summary=str(d.get("summary") or ""),
            date=str(d.get("date") or ""),
            updated=str(d.get("updated") or ""),
        )


def carry_lines(old_description: str | None, lines: Sequence[str]) -> list[str]:
    """The new lines, plus each kept-prefix line of the old description whose prefix the new lines lack."""
    out = list(lines)
    have = {ln.partition(": ")[0] for ln in out}
    for ln in (old_description or "").splitlines():
        if ln.startswith(KEPT_LINE_PREFIXES) and ln.partition(": ")[0] not in have:
            out.append(ln)
    return out


def _when(value: Mapping[str, Any] | None) -> str:
    if not isinstance(value, Mapping):
        return ""
    return str(value.get("dateTime") or value.get("date") or "")


def changed_fields(event: Mapping[str, Any], *, summary: str, start: datetime, location: str) -> list[str]:
    """'title X -> Y', 'when', 'where': what the update changes on the live event; empty when nothing did."""
    out: list[str] = []
    old = (event.get("summary") or "").strip()
    if old != summary:
        out.append(f"title {old} -> {summary}" if old else "title")
    old_start = _when(event.get("start"))
    try:
        same_start = bool(old_start) and datetime.fromisoformat(old_start) == start
    except ValueError:
        same_start = False
    if not same_start:
        out.append("when")
    if (event.get("location") or "").strip() != location.strip():
        out.append("where")
    return out
