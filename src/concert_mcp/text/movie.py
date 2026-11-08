"""A movie ticket read from OCR text, and the event lines it yields (unit text-movie).

Pure: no I/O, no clock. A confirmation screen (Fandango's is the worked example) prints the film with its year, a
rating line ending in the runtime, the theater and its address, then the showtime, seats, ticket count and
auditorium. `parse_movie_ticket` finds that shape or returns None, so it doubles as the test of whether a screenshot
is a movie ticket at all.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

PLATFORMS = ("Fandango", "Atom Tickets")

_RUNTIME_RE = re.compile(r"(?:^|,\s*)(\d{1,2})\s*(?:hr|hrs|h)\b\s*(?:(\d{1,2})\s*(?:min|mins|m)\b)?", re.IGNORECASE)
_FILM_RE = re.compile(r"^(?P<film>.+?)\s*\((?P<year>(?:19|20)\d{2})\)$")
_SHOWTIME_RE = re.compile(
    r"^(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+(?P<month>[a-z]{3})[a-z]*\.?\s+(?P<day>\d{1,2})"
    r"(?:,?\s+(?P<year>\d{4}))?\s+(?:at\s+)?(?P<time>\d{1,2}:\d{2}\s*[ap]\.?m\.?)$",
    re.IGNORECASE,
)
_SEATS_RE = re.compile(r"^seats?:\s*(.+)$", re.IGNORECASE)
_TICKETS_RE = re.compile(r"^tickets?:\s*(\d+)", re.IGNORECASE)
_AUDITORIUM_RE = re.compile(r"^(?:auditorium|theater|theatre|screen|cinema)\s+\w+$", re.IGNORECASE)
_TOTAL_RE = re.compile(r"(?:total|order total|amount)\D{0,20}(\$\d+(?:\.\d{2})?)", re.IGNORECASE)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


@dataclass(frozen=True)
class MovieTicket:
    film: str
    year: int | None
    runtime: timedelta | None
    theater: str
    address: tuple[str, ...]
    month: int
    day: int
    show_year: int | None  # printed on the ticket; usually absent, and then the next such date is meant
    time: str  # "2:30 PM"
    seats: tuple[str, ...]
    auditorium: str
    tickets: int | None
    platform: str | None
    total: str | None

    @property
    def location(self) -> str:
        return ", ".join([self.theater, *self.address])


def _lines(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines() if ln.strip()]


def _runtime(line: str) -> timedelta | None:
    m = _RUNTIME_RE.search(line)
    if not m:
        return None
    return timedelta(hours=int(m.group(1)), minutes=int(m.group(2) or 0))


def parse_movie_ticket(text: str) -> MovieTicket | None:
    """The ticket in an OCR'd confirmation screen; None when the text has no film-runtime-showtime shape."""
    lines = _lines(text)
    rt_i = next((i for i, ln in enumerate(lines) if i > 0 and _runtime(ln) and len(ln) <= 40), None)
    show_i = next((i for i, ln in enumerate(lines) if _SHOWTIME_RE.match(ln)), None)
    if rt_i is None or show_i is None or show_i < rt_i + 2:
        return None

    title_line = lines[rt_i - 1]
    fm = _FILM_RE.match(title_line)
    film, year = (fm.group("film").strip(), int(fm.group("year"))) if fm else (title_line, None)
    theater, address = lines[rt_i + 1], tuple(lines[rt_i + 2 : show_i])

    sm = _SHOWTIME_RE.match(lines[show_i])
    assert sm is not None  # show_i was found by this match
    after = lines[show_i + 1 : show_i + 6]
    seats = next((tuple(s.strip() for s in m.group(1).split(",")) for m in map(_SEATS_RE.match, after) if m), ())
    tickets = next((int(m.group(1)) for m in map(_TICKETS_RE.match, after) if m), None)
    auditorium = next((ln for ln in after if _AUDITORIUM_RE.match(ln)), "")
    platform = next((p for p in PLATFORMS if p.lower() in text.lower()), None)
    total = m.group(1) if (m := _TOTAL_RE.search(text)) else None
    time = re.sub(r"\s*([ap])\.?m\.?$", lambda x: f" {x.group(1).upper()}M", sm.group("time"), flags=re.IGNORECASE)
    return MovieTicket(
        film=film,
        year=year,
        runtime=_runtime(lines[rt_i]),
        theater=theater,
        address=address,
        month=_MONTHS.index(sm.group("month").lower()[:3]) + 1,
        day=int(sm.group("day")),
        show_year=int(sm.group("year")) if sm.group("year") else None,
        time=time,
        seats=seats,
        auditorium=auditorium,
        tickets=tickets,
        platform=platform,
        total=total,
    )


def show_start(t: MovieTicket, now: datetime) -> str:
    """The showtime as "YYYY-MM-DD h:mm AM": the printed year, else the first such date not more than a day past."""
    year = t.show_year or now.year
    if t.show_year is None and datetime(year, t.month, t.day) < datetime(now.year, now.month, now.day) - timedelta(
        days=1
    ):
        year += 1
    return f"{year:04d}-{t.month:02d}-{t.day:02d} {t.time}"


def fmt_runtime(d: timedelta) -> str:
    """2h 15min, as the calendar skill writes it."""
    h, m = divmod(int(d.total_seconds()) // 60, 60)
    return f"{h}h {m}min" if m else f"{h}h"


def movie_lines(t: MovieTicket, letterboxd: str | None) -> list[str]:
    """Description lines, in the calendar skill's order: seats, runtime, Letterboxd, purchase record."""
    where = ", ".join(p for p in (t.auditorium, ("seats " + " + ".join(t.seats)) if t.seats else "") if p)
    bought = ", ".join(
        p
        for p in (
            f"{t.tickets} ticket{'s' if t.tickets != 1 else ''}" if t.tickets else "",
            t.total or "",
        )
        if p
    )
    purchase = (
        f"Purchased on {t.platform}: {bought}" if t.platform and bought else (f"Purchased: {bought}" if bought else "")
    )
    return [
        ln
        for ln in (
            where,
            f"Runtime: {fmt_runtime(t.runtime)}" if t.runtime else "",
            f"Letterboxd: {letterboxd}" if letterboxd else "",
            purchase,
        )
        if ln
    ]


def letterboxd_slug(film: str) -> str:
    """Letterboxd's film slug: ASCII, lowercase, apostrophes dropped, every other run of non-alphanumerics a dash."""
    s = unicodedata.normalize("NFKD", film).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"['’]", "", s).replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def letterboxd_candidates(film: str, year: int | None) -> list[str]:
    """Film page URLs to try, year-suffixed first (the slug of a remake or a shared title), then the bare slug."""
    slug = letterboxd_slug(film)
    slugs = ([f"{slug}-{year}"] if year else []) + [slug]
    return [f"https://letterboxd.com/film/{s}/" for s in slugs]


def letterboxd_matches(html: str, film: str, year: int | None) -> bool:
    """The page's og:title names this film (and this year, when known)."""
    m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    if not m:
        return False
    title = m.group(1).lower()
    return film.lower() in title and (year is None or f"({year})" in title)
