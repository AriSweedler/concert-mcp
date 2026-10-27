"""The lineup of a show, the grounded-model prompt that asks for it, and the validation of the answer
(unit text-parse). Pure.

A grounded model (Gemini with Google Search) is asked one question per concert: who opens, and what set times the
venue or the promoter announced. The answer is JSON in a fixed shape; everything in it is treated as a claim and
checked here before it reaches the calendar: an opener equal to the headliner, the venue or a ticket platform is
dropped, a time that does not parse as a clock time is dropped, and nothing is ever invented to fill a gap.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from concert_mcp.text.derive import PLATFORMS
from concert_mcp.text.titles import normalize_name

MAX_OPENERS = 3
OPENER_SETLIST_PREFIX = "Opener setlist (recent): "
EXPECTED_PREFIX = "Expected: "
LINEUP_LINE_PREFIXES = ("Opener: ", EXPECTED_PREFIX, OPENER_SETLIST_PREFIX)

# The shape the model must answer in. Sent inside the prompt, not as `responseSchema`: the Gemini API refuses a
# `google_search` tool together with a JSON response schema, and the grounding is the point of the call.
LINEUP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "openers": {"type": "array", "items": {"type": "string"}},
        "doors": {"type": ["string", "null"]},
        "opener_on_stage": {"type": ["string", "null"]},
        "headliner_on_stage": {"type": ["string", "null"]},
    },
    "required": ["openers", "doors", "opener_on_stage", "headliner_on_stage"],
}

# "7:30 PM", "7pm", "19:30", "7.30 p.m."
_CLOCK_RE = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*([ap])\.?m\.?\b|\b(\d{1,2}):(\d{2})\b", re.I)


@dataclass(frozen=True)
class GroundedAnswer:
    """What a grounded model returned: its text and the pages it cited."""

    text: str
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class Opener:
    name: str
    setlist_url: str | None = None


@dataclass(frozen=True)
class Lineup:
    """The checked answer: openers (with their most recent setlist.fm page), the announced set times, the sources."""

    status: Literal["found", "none", "unavailable", "failed"]
    openers: tuple[Opener, ...] = ()
    doors: str | None = None
    opener_time: str | None = None
    headliner_time: str | None = None
    sources: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def has_times(self) -> bool:
        return any((self.doors, self.opener_time, self.headliner_time))

    def to_json(self) -> dict[str, Any]:
        rec: dict[str, Any] = {
            "status": self.status,
            "openers": [{"name": o.name, "setlist_url": o.setlist_url} for o in self.openers],
            "doors": self.doors,
            "opener_on_stage": self.opener_time,
            "headliner_on_stage": self.headliner_time,
            "sources": list(self.sources),
        }
        if self.reason is not None:
            rec["error"] = self.reason
        return rec


SEARCH = "https://lite.duckduckgo.com/lite/?q="
NOT_A_VENUE_SITE = (
    "duckduckgo.",
    "bing.",
    "google.",
    "yelp.",
    "facebook.",
    "instagram.",
    "tripadvisor.",
    "wikipedia.",
    "songkick.",
    "bandsintown.",
    "ticketmaster.",
    "axs.",
    "dice.fm",
    "eventbrite.",
    "ticketweb.",
    "seetickets.",
    "foursquare.",
    "mapquest.",
    "yellowpages.",
    "x.com",
    "twitter.",
    "youtube.",
    "tiktok.",
    "reddit.",
)
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def search_page(query: str) -> str:
    """A search engine's results page for the query: the one page url_context can always read (pure)."""
    from urllib.parse import quote_plus

    return SEARCH + quote_plus(" ".join(query.split()))


def urls_in(text: str) -> tuple[str, ...]:
    """The http(s) URLs a ticket text carries (the event or receipt pages), deduplicated, in order (pure)."""
    seen: dict[str, None] = {}
    for m in URL_RE.finditer(text or ""):
        seen.setdefault(m.group(0).rstrip(".,;"), None)
    return tuple(seen)


def listing_pages(website: str) -> tuple[str, ...]:
    """A venue website and the pages its event listing usually lives on (pure)."""
    base = website.rstrip("/")
    return (base + "/", base + "/shows", base + "/calendar", base + "/events")


def website_prompt(venue: str) -> str:
    """Find the venue's own website from a results page; the model reads the page named here (pure)."""
    return (
        f"Read {search_page(venue + ' official website')} . Which result is the official website of the venue "
        f"{venue}? Not a listing, ticket or social site: the venue's own domain. Answer with one JSON object "
        '{"website": "https://..."} or {"website": null}, nothing else.'
    )


def parse_website(answer: GroundedAnswer) -> str | None:
    """The venue website the model named, when it is a real site of the venue's own (pure)."""
    obj = _json_object(answer.text)
    site = str((obj or {}).get("website") or "").strip()
    if not site.startswith(("http://", "https://")):
        return None
    host = site.split("/")[2].lower()
    if any(bad in host for bad in NOT_A_VENUE_SITE):
        return None
    return site.rstrip("/") + "/"


def lineup_pages(
    artist: str, date: str, venue: str, website: str | None, ticket_urls: Sequence[str] = ()
) -> tuple[str, ...]:
    """Every page the model is asked to read for one show: the venue's listing, the ticket's pages, a
    results page (pure)."""
    pages: list[str] = list(listing_pages(website)) if website else []
    pages += [u for u in ticket_urls if u not in pages]
    pages.append(search_page(f"{artist} {venue} {date} tickets"))
    return tuple(pages)


def lineup_prompt(artist: str, date: str, venue: str, pages: Sequence[str]) -> str:
    """The one question asked per concert; the model reads the pages named here (url_context)."""
    read = " ".join(pages)
    return (
        f"Read these pages: {read} . For the concert by {artist} at {venue} on {date}: who are the opening acts, "
        "and what are the announced set times (doors, opener on stage, headliner on stage)? Answer only from what "
        "those pages say; use null for anything not announced and an empty list when no opener is announced. "
        "Do not guess. Answer with one JSON object and nothing else, in this shape: "
        f"{json.dumps(LINEUP_SCHEMA['properties'], separators=(',', ':'))}"
    )


def _json_object(text: str) -> Mapping[str, Any] | None:
    """The first JSON object in the text (code fences and prose around it ignored); None when there is none."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s)
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(s[start : end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, Mapping) else None


def clock_time(value: object) -> str | None:
    """'7:30 PM' for any clock form the model may echo ('7pm', '19:30', '7.30 p.m.'); None when it is not one."""
    m = _CLOCK_RE.search(str(value or ""))
    if not m:
        return None
    if m.group(3):
        hour, minute, ampm = int(m.group(1)), m.group(2) or "00", m.group(3).upper() + "M"
        if not 1 <= hour <= 12:
            return None
    else:
        hour24, minute = int(m.group(4)), m.group(5)
        if hour24 > 23:
            return None
        ampm = "PM" if hour24 >= 12 else "AM"
        hour = hour24 % 12 or 12
    if int(minute) > 59:
        return None
    return f"{hour}:{minute} {ampm}"


def _not_an_act(name: str, headliner: str, venue: str) -> bool:
    """An opener equal to the headliner, the venue (or its first part) or a ticket platform is not an opener."""
    n = normalize_name(name)
    venue_names = {normalize_name(venue), normalize_name(venue.split(",")[0])}
    platforms = {normalize_name(p) for p in PLATFORMS}
    return not n or n == normalize_name(headliner) or n in venue_names or n in platforms or n in {"tba", "tbd"}


def parse_lineup(answer: GroundedAnswer, *, headliner: str, venue: str) -> Lineup:
    """The checked Lineup of a model answer: garbage is 'none', not an error, because nothing was announced."""
    obj = _json_object(answer.text)
    if obj is None:
        return Lineup("none", sources=answer.sources)
    raw = obj.get("openers")
    names: list[str] = []
    for item in raw if isinstance(raw, Sequence) and not isinstance(raw, str) else []:
        name = " ".join(str(item or "").split())
        if _not_an_act(name, headliner, venue) or any(normalize_name(name) == normalize_name(x) for x in names):
            continue
        names.append(name)
    openers = tuple(Opener(n) for n in names[:MAX_OPENERS])
    lineup = Lineup(
        "found",
        openers=openers,
        doors=clock_time(obj.get("doors")),
        opener_time=clock_time(obj.get("opener_on_stage")),
        headliner_time=clock_time(obj.get("headliner_on_stage")),
        sources=answer.sources,
    )
    if not lineup.openers and not lineup.has_times:
        return Lineup("none", sources=answer.sources)
    return lineup


def expected_line(lineup: Lineup, headliner: str) -> str | None:
    """'Expected: doors 7:00 PM, Zork 8:00 PM, Zed Fox 9:00 PM' from the times a source gave; None without any."""
    parts: list[str] = []
    if lineup.doors:
        parts.append(f"doors {lineup.doors}")
    if lineup.opener_time:
        who = lineup.openers[0].name if lineup.openers else "opener"
        parts.append(f"{who} {lineup.opener_time}")
    if lineup.headliner_time:
        parts.append(f"{headliner} {lineup.headliner_time}")
    return EXPECTED_PREFIX + ", ".join(parts) if parts else None


def opener_setlist_line(lineup: Lineup) -> str | None:
    """'Opener setlist (recent): <url>' (one opener), or 'Zork <url>; Blorp <url>' for several; None without any."""
    with_pages = [o for o in lineup.openers if o.setlist_url]
    if not with_pages:
        return None
    if len(lineup.openers) == 1:
        return OPENER_SETLIST_PREFIX + str(with_pages[0].setlist_url)
    return OPENER_SETLIST_PREFIX + "; ".join(f"{o.name} {o.setlist_url}" for o in with_pages)


def lineup_lines(lineup: Lineup, headliner: str, *, openers_known: bool) -> list[str]:
    """The description lines the lineup adds: Opener lines (unless the ticket named the openers), Expected, the
    opener's recent setlist page. Empty when nothing was found."""
    if lineup.status != "found":
        return []
    out: list[str] = []
    if lineup.openers and not openers_known:
        out.append(f"Headliner: {headliner}")
        out += [f"Opener: {o.name}" for o in lineup.openers]
    if (exp := expected_line(lineup, headliner)) is not None:
        out.append(exp)
    if (page := opener_setlist_line(lineup)) is not None:
        out.append(page)
    return out


def merge_lineup_lines(old_description: str | None, lines: Sequence[str]) -> tuple[list[str], list[str]]:
    """(the description's lines with the lineup lines whose key it lacks appended, the lines appended)."""
    old = [ln for ln in (old_description or "").splitlines()]
    have = {ln.partition(": ")[0] for ln in old if ln.strip()}
    added = [ln for ln in lines if ln.partition(": ")[0] not in have]
    return old + added, added


def latest_setlist_url(search_response: Mapping[str, Any] | None) -> str | None:
    """The page of the newest setlist in a setlist.fm search answer (newest first), preferring one with songs."""
    rows = (search_response or {}).get("setlist") or []
    with_songs = [r for r in rows if any(st.get("song") for st in (r.get("sets") or {}).get("set", []) or [])]
    pick = (with_songs or rows)[:1]
    url = str(pick[0].get("url") or "") if pick else ""
    return url or None
