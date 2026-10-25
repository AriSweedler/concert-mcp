"""Delete candidates and the duplicate guard (unit text-render)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from concert_mcp.result import FixInput
from concert_mcp.text.dates import when_of
from concert_mcp.text.titles import title_matches

TEASER_LINE_PREFIX = "Teaser playlist: "


@dataclass(frozen=True)
class Candidate:
    id: str
    calendar_id: str
    calendar: str
    summary: str
    when: str
    start: str
    location: str
    html_link: str | None
    description: str

    def to_json(self) -> dict[str, Any]:
        """Legacy keys: id, calendarId, calendar, summary, when, start, location, htmlLink, description."""
        return {
            "id": self.id,
            "calendarId": self.calendar_id,
            "calendar": self.calendar,
            "summary": self.summary,
            "when": self.when,
            "start": self.start,
            "location": self.location,
            "htmlLink": self.html_link,
            "description": self.description,
        }

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> Candidate:
        """Inverse of to_json."""
        return cls(
            id=str(d.get("id") or ""),
            calendar_id=str(d.get("calendarId") or ""),
            calendar=str(d.get("calendar") or ""),
            summary=str(d.get("summary") or ""),
            when=str(d.get("when") or ""),
            start=str(d.get("start") or ""),
            location=str(d.get("location") or ""),
            html_link=d.get("htmlLink"),
            description=str(d.get("description") or ""),
        )


def candidate_from_event(event: Mapping[str, Any], calendar_id: str, calendar_name: str, tz: ZoneInfo) -> Candidate:
    """A Candidate from a Google Calendar event ('(no title)' when it has no summary)."""
    when, start = when_of(event, tz)
    return Candidate(
        id=str(event.get("id") or ""),
        calendar_id=calendar_id,
        calendar=calendar_name,
        summary=event.get("summary") or "(no title)",
        when=when,
        start=start.isoformat(),
        location=event.get("location") or "",
        html_link=event.get("htmlLink"),
        description=event.get("description") or "",
    )


def filter_candidates(
    events: Iterable[Mapping[str, Any]],
    wanted_title: str,
    calendar_id: str,
    calendar_name: str,
    tz: ZoneInfo,
) -> list[Candidate]:
    """Non-cancelled events whose title matches, as Candidates."""
    return [
        candidate_from_event(e, calendar_id, calendar_name, tz)
        for e in events
        if e.get("status") != "cancelled" and title_matches(wanted_title, e.get("summary") or "")
    ]


def search_window(date: str | None, now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[day-1d, day+2d) for a date, else [now-30d, now+365d); FixInput for a bad date."""
    if date:
        try:
            day = datetime.strptime(str(date)[:10], "%Y-%m-%d").replace(tzinfo=tz)
        except ValueError:
            raise FixInput(f"date {date!r} is not YYYY-MM-DD", 'send the day as "2026-10-24"') from None
        return day - timedelta(days=1), day + timedelta(days=2)
    local = now.astimezone(tz)
    return local - timedelta(days=30), local + timedelta(days=365)


def duplicate_of(events: Iterable[Mapping[str, Any]], title: str) -> Mapping[str, Any] | None:
    """The non-cancelled event whose summary equals title (case-insensitive), if any."""
    want = title.lower()
    for e in events:
        if (e.get("summary") or "").strip().lower() == want and e.get("status") != "cancelled":
            return e
    return None


def missing_link_lines(description: str | None, lines: Sequence[str | None]) -> list[str]:
    """The given lines that are not already in the description."""
    have = description or ""
    return [ln for ln in lines if ln and ln not in have]


def patched_description(description: str | None, missing: Sequence[str]) -> str:
    """The description with the missing lines appended."""
    return "\n".join([(description or "").rstrip(), *missing]).strip()


def teaser_url_in(description: str) -> str | None:
    """The value of the 'Teaser playlist: ' line."""
    for ln in (description or "").splitlines():
        if ln.startswith(TEASER_LINE_PREFIX):
            url = ln[len(TEASER_LINE_PREFIX) :].strip()
            return url or None
    return None


def pick_index(n: str) -> int | None:
    """'2' or '2.' -> 2; None when not a number."""
    try:
        return int(str(n).strip().rstrip("."))
    except ValueError:
        return None
