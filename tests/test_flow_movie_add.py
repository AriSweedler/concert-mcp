"""flows/movie_add.py on the fakes: a Fandango confirmation's OCR text becomes the golden [Movie] event."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from concert_mcp.config import Settings
from concert_mcp.flows.movie_add import add_movie, is_movie_title
from concert_mcp.flows.services import Services
from concert_mcp.result import Kind
from concert_mcp.text.movie import letterboxd_candidates, letterboxd_matches, parse_movie_ticket, show_start
from conftest import make_services
from fakes import FakeFilmLookup

HERE = Path(__file__).parent
FANDANGO = (HERE / "fixtures" / "fandango-movie.txt").read_text(encoding="utf-8")
GOLDEN = json.loads((HERE / "goldens" / "fandango-movie.json").read_text(encoding="utf-8"))
FILM_URL = "https://letterboxd.com/film/godzilla-minus-zero/"
# The day after the screenshot was taken, a Sunday before the Wednesday show.
SENT = datetime(2026, 11, 8, 10, 0, 0, tzinfo=timezone(timedelta(hours=-8)))


@pytest.fixture
def movie_svc(settings: Settings) -> Services:
    svc = make_services(settings, SENT, route=None, text=FANDANGO)
    svc.films = FakeFilmLookup({("Godzilla Minus Zero", 2026): FILM_URL})
    return svc


def inserted_as_golden(svc: Services) -> dict[str, Any]:
    (ev,) = [args for method, args in svc.calendar.calls if method == "insert_event"]  # type: ignore[attr-defined]
    return {
        "calendar": ev["calendar_id"],
        "summary": ev["summary"],
        "start": ev["start"].isoformat(),
        "end": ev["end"].isoformat(),
        "location": ev["location"],
        "description": (ev["description"] or "").split("\n"),
    }


# --- parse_movie_ticket ---


def test_parse_reads_every_field_of_the_fandango_screen() -> None:
    t = parse_movie_ticket(FANDANGO)
    assert t is not None
    assert (t.film, t.year, t.runtime) == ("Godzilla Minus Zero", 2026, timedelta(hours=2, minutes=15))
    assert t.location == GOLDEN["location"]
    assert (t.month, t.day, t.show_year, t.time) == (11, 11, None, "2:30 PM")
    assert (t.seats, t.tickets, t.auditorium, t.platform, t.total) == (
        ("515", "516"),
        2,
        "Auditorium 1",
        "Fandango",
        None,
    )


def test_parse_is_none_for_a_concert_ticket(ticket_text: str) -> None:
    assert parse_movie_ticket(ticket_text) is None


# --- show_start ---


def test_show_start_takes_the_next_such_date() -> None:
    t = parse_movie_ticket(FANDANGO)
    assert t is not None
    assert show_start(t, SENT) == "2026-11-11 2:30 PM"
    assert show_start(t, SENT.replace(month=12)) == "2027-11-11 2:30 PM"


# --- letterboxd text helpers ---


def test_letterboxd_tries_the_year_slug_first() -> None:
    assert letterboxd_candidates("Godzilla Minus Zero", 2026) == [
        "https://letterboxd.com/film/godzilla-minus-zero-2026/",
        FILM_URL,
    ]


def test_letterboxd_page_must_name_the_film_and_year() -> None:
    page = '<meta property="og:title" content="Godzilla Minus Zero (2026)" />'
    assert letterboxd_matches(page, "Godzilla Minus Zero", 2026)
    assert not letterboxd_matches(page, "Godzilla Minus Zero", 2023)


# --- add_movie: the golden ---


def test_fandango_screenshot_becomes_the_golden_event(movie_svc: Services) -> None:
    out = add_movie(title="Godzilla Minus Zero", start="2026-11-11 14:30", source_text=FANDANGO, svc=movie_svc)
    assert out.kind is Kind.OK, out.reply
    assert inserted_as_golden(movie_svc) == GOLDEN


def test_the_ticket_wins_over_a_wrong_caller(movie_svc: Services) -> None:
    out = add_movie(
        title="Godzilla", start="2026-11-11 19:00", location="AMC Metreon", source_text=FANDANGO, svc=movie_svc
    )
    assert out.kind is Kind.OK, out.reply
    assert inserted_as_golden(movie_svc) == GOLDEN


def test_a_movie_never_touches_spotify_or_setlistfm(movie_svc: Services) -> None:
    add_movie(source_text=FANDANGO, svc=movie_svc)
    assert movie_svc.spotify.calls == []  # type: ignore[union-attr]
    assert movie_svc.setlistfm.calls == []  # type: ignore[union-attr]


def test_a_letterboxd_miss_leaves_the_line_out(movie_svc: Services) -> None:
    movie_svc.films = FakeFilmLookup({})
    add_movie(source_text=FANDANGO, svc=movie_svc)
    assert [ln for ln in inserted_as_golden(movie_svc)["description"] if ln.startswith("Letterboxd")] == []


def test_dry_run_returns_the_golden_without_inserting(movie_svc: Services) -> None:
    out = add_movie(source_text=FANDANGO, svc=movie_svc, dry_run=True)
    assert out.kind is Kind.DRY_RUN
    assert movie_svc.calendar.inserted == []  # type: ignore[attr-defined]
    assert {k: out.data[k] for k in ("summary", "start", "end", "location", "description")} == {
        k: GOLDEN[k] for k in ("summary", "start", "end", "location", "description")
    }


# --- the tools that hand a movie to the movie flow ---


def test_is_movie_title() -> None:
    assert is_movie_title("[Movie] Godzilla Minus Zero")
    assert is_movie_title(" [movie]Godzilla")
    assert not is_movie_title("[Dinner] Movie night snacks")


def test_add_movie_with_no_source_reads_the_ticket_from_the_context(movie_svc: Services) -> None:
    out = add_movie(title="[Movie] Godzilla Minus Zero", start="2026-11-11 2:30 PM", svc=movie_svc)
    assert out.kind is Kind.OK, out.reply
    assert inserted_as_golden(movie_svc) == GOLDEN
