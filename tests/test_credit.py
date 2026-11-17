"""text/credit.py: the line that says what made a thing, and every maker reads it from there."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from concert_mcp.text.credit import CREATED_BY, CREATED_BY_SHORT, PROJECT_URL
from concert_mcp.text.description import from_lines, to_html
from concert_mcp.text.naming import setlist_description, teaser_description
from concert_mcp.text.partiful import invite_description
from concert_mcp.text.records import todo_notes

SRC = Path(__file__).resolve().parents[1] / "src" / "concert_mcp"


def test_the_credit_is_the_owners_wording() -> None:
    assert CREATED_BY == "Created by github.com/AriSweedler/concert-mcp"
    assert CREATED_BY_SHORT == "Created by AriSweedler/concert-mcp"
    assert PROJECT_URL == "https://github.com/AriSweedler/concert-mcp"


def test_no_maker_writes_its_own_copy() -> None:
    holders = sorted(p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py") if "Created by" in p.read_text())
    assert holders == ["text/credit.py"]


def test_every_maker_ends_with_the_credit() -> None:
    start = datetime(2026, 11, 14, 20, 30)
    # The calendar links the short form; Partiful and Tasks link the address they are given; Spotify links nothing.
    assert to_html(from_lines(["Doors: 7:30 PM"])).endswith(f'<a href="{PROJECT_URL}">{CREATED_BY_SHORT}</a>')
    assert invite_description(["Doors: 7:30 PM"], start).endswith(f"\n\n{CREATED_BY}")
    assert todo_notes("https://partiful.com/e/x", "https://calendar.example/e").endswith(f"\n\n{CREATED_BY}")
    assert teaser_description("Zed Fox", "Ari").endswith(f" · {CREATED_BY_SHORT}")
    assert setlist_description("https://www.setlist.fm/setlist/x.html", "2026-11-20").endswith(f" · {CREATED_BY_SHORT}")
