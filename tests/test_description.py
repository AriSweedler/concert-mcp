"""text/description.py: the plain and HTML forms of an event description, and the round trip between them."""

from __future__ import annotations

import pytest

from concert_mcp.text.description import (
    HtmlDescription,
    PlainDescription,
    description_of,
    from_lines,
    lines_of,
    to_html,
    to_plain,
)

TEASER = "https://open.spotify.com/playlist/4QsgkFxoGSeW9es97bFQBB"
CONCERT = from_lines(
    [
        "Headliner: Rodrigo y Gabriela",
        "Doors: 6:00 PM",
        "Purchased: 2 tickets, $64.75 (fees & tax in)",
        "Expected: doors 6:00 PM, Rodrigo y Gabriela 7:00 PM",
        "Opener setlist (recent): https://www.setlist.fm/setlist/a/2026/x-1.html",
        "Venue: https://www.thecastro.com/",
        f"Teaser playlist: {TEASER}",
        "Setlist: https://www.setlist.fm/setlist/rodrigo-y-gabriela/2026/the-castro-1.html?a=1&b=2",
        "Setlist playlist: https://open.spotify.com/playlist/5x",
    ]
)
MOVIE = from_lines(
    [
        "Runtime: 2 hr 15 min",
        "Seats: 515, 516 <Auditorium 1>",
        "Letterboxd: https://letterboxd.com/film/godzilla-minus-zero/",
        "Purchased on Fandango: 2 tickets",
    ]
)


def test_a_link_line_is_an_anchor_with_the_label_as_its_text() -> None:
    html = to_html(from_lines(["Doors: 7:00 PM", f"Teaser playlist: {TEASER}"]))
    assert html == f'Doors: 7:00 PM<br><a href="{TEASER}">Teaser playlist</a>'


def test_only_a_line_that_is_one_url_becomes_a_link() -> None:
    two = "Opener setlist (recent): A https://a.example/1; B https://b.example/2"
    assert to_html(from_lines([two, "Note: see https://x.example now"])) == f"{two}<br>Note: see https://x.example now"


def test_text_and_urls_are_escaped() -> None:
    html = to_html(from_lines(["Seats: 5 & 6 <GA>", "Setlist: https://s.example/p?a=1&b=2"]))
    assert html == 'Seats: 5 &amp; 6 &lt;GA&gt;<br><a href="https://s.example/p?a=1&amp;b=2">Setlist</a>'


@pytest.mark.parametrize("plain", [CONCERT, MOVIE, from_lines(["One", "", "Two"]), from_lines([])])
def test_what_the_flows_write_survives_the_round_trip(plain: PlainDescription) -> None:
    assert to_plain(to_html(plain)) == plain


def test_an_event_written_before_html_reads_unchanged() -> None:
    old = "Doors: 7:00 PM\nPurchased: 1 ticket & fees\n" + f"Teaser playlist: {TEASER}"
    assert to_plain(HtmlDescription(old)) == old


def test_html_from_the_calendar_editor_reads_as_lines() -> None:
    stored = HtmlDescription(
        f'<p>Doors:&nbsp;7:00 PM</p><ul><li>Seat: GA</li></ul><a href="{TEASER}" target="_blank">Teaser playlist</a>'
        '<br>Tickets at <a href="https://axs.example">AXS</a> now<br><a href="https://x.example">https://x.example</a>'
    )
    assert lines_of(to_plain(stored)) == [
        "Doors: 7:00 PM",
        "Seat: GA",
        f"Teaser playlist: {TEASER}",
        "Tickets at AXS (https://axs.example) now",
        "https://x.example",
    ]


def test_nothing_stored_is_an_empty_description() -> None:
    assert to_plain(None) == ""
    assert to_plain(HtmlDescription("")) == ""
    assert lines_of(None) == []


def test_description_of_reads_an_event() -> None:
    assert description_of({"description": "Doors: 7:00 PM"}) == "Doors: 7:00 PM"
    assert description_of({}) == ""
