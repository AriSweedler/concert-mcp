"""Pure date and time parsing and rendering (unit text-parse)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from concert_mcp.result import FixInput, NeedUser, need
from concert_mcp.text.derive import CLOCK_RE, DOORS_RE, TIME_LITERAL

__all__ = [
    "DOORS_RE",
    "SHOW_RE",
    "START_FORMATS",
    "apply_show_time",
    "check_duration",
    "check_window",
    "clock",
    "concert_date",
    "default_end",
    "doors_time",
    "far_future_reply",
    "fmt_range",
    "is_date_only",
    "parse_time",
    "past_reply",
    "show_time",
    "start_from_date",
    "validate_tz",
    "when_of",
]

# The legacy six formats plus two no-space forms a small model tends to emit ("8:30PM", "8PM").
START_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %I:%M %p",
    "%Y-%m-%d %I %p",
    "%Y-%m-%d %I:%M%p",
    "%Y-%m-%d %I%p",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
)
SHOW_RE = re.compile(
    r"\b(?:show(?:time)?|music|starts?|performance)\s*(?:at|:|@|starts?|begins?)?\s*" + TIME_LITERAL, re.I
)
_DATE_ONLY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DOTTED_RE = re.compile(r"(\d)\.(\d)")


def parse_time(s: str, tz: str, what: str = "start") -> datetime:
    """Parse a start/end string in one of START_FORMATS or ISO 8601 into an aware datetime; FixInput otherwise."""
    s = re.sub(r"\s+", " ", str(s).strip())
    for fmt in START_FORMATS:
        try:
            return datetime.strptime(s.upper() if "%p" in fmt else s, fmt).replace(tzinfo=ZoneInfo(tz))
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(s.replace(" ", "T", 1).replace("Z", "+00:00"))
    except ValueError:
        raise FixInput(
            f"{what} {s!r} is not a date", f'{what} must look like "2026-11-03 20:00" (24h) or "2026-11-03 8:00 PM"'
        ) from None
    return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(tz))


def is_date_only(s: str) -> bool:
    """True for a bare YYYY-MM-DD."""
    return _DATE_ONLY_RE.fullmatch(str(s).strip()) is not None


def validate_tz(tz: str) -> str:
    """Return tz when it is an IANA name; FixInput otherwise."""
    try:
        ZoneInfo(tz)
    except Exception:
        raise FixInput(f"unknown tz {tz!r}", "use an IANA name like America/Los_Angeles, or omit it") from None
    return tz


def clock(txt: str) -> time:
    """'7:30 PM' / '7pm' / '7.30 p.m.' -> time."""
    t = _DOTTED_RE.sub(r"\1:\2", txt)
    t = re.sub(r"[\s.]", "", t).upper().replace("PM", " PM").replace("AM", " AM")
    return datetime.strptime(t, "%I:%M %p" if ":" in t else "%I %p").time()


def doors_time(text: str) -> time | None:
    """The doors time the text states, if any."""
    d = DOORS_RE.search(text)
    return clock(d.group(1)) if d else None


def show_time(text: str) -> tuple[time | None, bool]:
    """(time, certain): the show time the text states. Certain when it says 'show/showtime/music/starts <t>'; inferred
    when there is a doors time and exactly one other clock time; else (None, False)."""
    m = SHOW_RE.search(text)
    if m:
        return clock(m.group(1)), True
    doors = doors_time(text)
    if doors is not None:
        others = {clock(t) for t in CLOCK_RE.findall(text)} - {doors}
        if len(others) == 1:
            return others.pop(), False
    return None, False


def default_end(start: datetime, hours: int = 2) -> datetime:
    """start + hours."""
    return start + timedelta(hours=hours)


def check_duration(start: datetime, end: datetime) -> None:
    """FixInput unless start < end <= start + 12 h."""
    if end <= start or end - start > timedelta(hours=12):
        raise FixInput("end must be after start and within 12 hours", "fix end, or omit it for start + 2 hours")


def past_reply(start: datetime) -> str:
    """The reply for a start in the past."""
    return (
        f"That reads as {start.strftime('%a %b %-d %Y, %-I:%M %p')}, which is in the past. "
        "Reply with the right date (include the year) and I will add it."
    )


def far_future_reply(start: datetime) -> str:
    """The reply for a start over two years out."""
    return (
        f"That reads as {start.strftime('%a %b %-d %Y')}, over two years from now. "
        "Reply with the right date and I will add it."
    )


def check_window(
    start: datetime,
    now: datetime,
    *,
    allow_past: bool,
    past_grace_h: int = 12,
    max_future_days: int = 730,
) -> None:
    """NeedUser when start is in the past (unless allowed) or over max_future_days out."""
    if start < now - timedelta(hours=past_grace_h) and not allow_past:
        raise NeedUser(f"start {start.isoformat()} is in the past", past_reply(start))
    if start > now + timedelta(days=max_future_days):
        raise NeedUser(f"start {start.isoformat()} is over two years out", far_future_reply(start))


def apply_show_time(
    start: datetime, end: datetime, end_assumed: bool, text: str
) -> tuple[datetime, datetime, list[str]]:
    """Move start to the show time the text states (with a note), or FixInput when start is the doors time.

    The FIX for an inferred show time is literal (the exact start string to pass), so a small model has nothing to
    compose: it copies the value and calls once more.
    """
    st, certain = show_time(text)
    if st is None or start.time() == st:
        return start, end, []
    fixed = start.replace(hour=st.hour, minute=st.minute)
    if not certain:
        raise FixInput(
            f"start {start.strftime('%-I:%M %p')} is not the show time",
            f'set start to "{fixed.strftime("%Y-%m-%d %-I:%M %p")}" '
            f"(the show time in the text; {start.strftime('%-I:%M %p')} is doors)",
        )
    new_end = end + (fixed - start) if end_assumed else end
    return fixed, new_end, [f"Start corrected to {fixed.strftime('%-I:%M %p')}, the show time in the text."]


def start_from_date(date_str: str, text: str, tz: str) -> tuple[datetime, str]:
    """A date-only start plus the show time from the text; NeedUser when the text has no show time."""
    try:
        day = datetime.strptime(str(date_str).strip(), "%Y-%m-%d")
    except ValueError:
        raise FixInput(
            f"start {date_str!r} is not a date", 'start must look like "2026-11-03 20:00" (24h) or "2026-11-03 8:00 PM"'
        ) from None
    st, _ = show_time(text)
    if st is None:
        raise need("show time")
    start = datetime.combine(day.date(), st, tzinfo=ZoneInfo(tz))
    return start, f"Start taken from the text ({st.strftime('%-I:%M %p')})."


def fmt_range(start: datetime, end: datetime) -> str:
    """'Sat Nov 14, 8:30 PM – 10:30 PM PST' (cross-day repeats the date)."""
    same_day = start.date() == end.date()
    a = start.strftime("%a %b %-d, %-I:%M %p")
    b = end.strftime("%-I:%M %p" if same_day else "%a %b %-d, %-I:%M %p")
    return f"{a} – {b} {start.tzname()}"


def concert_date(text: str) -> str:
    """YYYY-MM-DD, YYYY/MM/DD or an RFC3339 prefix -> YYYY-MM-DD; FixInput otherwise."""
    head = str(text).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(head, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise FixInput(f"unreadable concert date {text!r}", 'date must look like "2026-11-03"')


def when_of(event: Mapping[str, Any], tz: ZoneInfo) -> tuple[str, datetime]:
    """(rendered when, start) for a Google Calendar event, timed or all-day."""
    st, en = event.get("start") or {}, event.get("end") or {}
    if st.get("dateTime"):
        a = datetime.fromisoformat(st["dateTime"].replace("Z", "+00:00")).astimezone(tz)
        b = datetime.fromisoformat((en.get("dateTime") or st["dateTime"]).replace("Z", "+00:00")).astimezone(tz)
        return fmt_range(a, b), a
    day = datetime.fromisoformat(st.get("date")).replace(tzinfo=tz)
    return day.strftime("%a %b %-d (all day)"), day
