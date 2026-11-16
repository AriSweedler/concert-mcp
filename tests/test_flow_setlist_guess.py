"""The setlist guess: flows/setlist_guess.py on the fakes, the line concert_add says, and what the record keeps."""

from __future__ import annotations

from dataclasses import replace

from concert_mcp.flows.records import all_records, record_for_event
from concert_mcp.flows.services import Services
from concert_mcp.flows.setlist_guess import guess_setlist_page
from concert_mcp.result import Kind
from concert_mcp.text.records import ConcertRecord, facts
from concert_mcp.text.reply import setlist_guess_note
from conftest import add_zed_fox as concert
from fakes import FakeSetlistFm

# An earlier night of the tour: the artist's most recent page when the concert is added.
EARLIER = "https://www.setlist.fm/setlist/zed-fox/2026/the-hollow-oakland-ca-1a2b3c4d.html"
OWN = "https://www.setlist.fm/setlist/zed-fox/2026/the-quarry-san-francisco-ca-3bd6f4a.html"
ROWS = {
    "setlist": [
        {"id": "9f8e7d6", "url": "https://www.setlist.fm/setlist/zed-fox/2026/empty-9f8e7d6.html", "sets": {"set": []}},
        {"id": "1a2b3c4d", "url": EARLIER, "sets": {"set": [{"song": [{"name": "First Light"}]}]}},
    ]
}


def test_the_guess_is_the_artists_most_recent_page_or_nothing(svc: Services) -> None:
    assert setlist_guess_note(EARLIER) == f"possible [setlist.fm link]({EARLIER})"
    svc.setlistfm = FakeSetlistFm({("Zed Fox", None, None): ROWS})
    assert guess_setlist_page("Zed Fox", svc) == EARLIER  # the newest page that has songs
    assert guess_setlist_page("Nobody", svc) is None
    svc.setlistfm = FakeSetlistFm({}, fail=True)
    assert guess_setlist_page("Zed Fox", svc) is None  # setlist.fm refusing is not worth a failed add
    svc.setlistfm = None
    assert guess_setlist_page("Zed Fox", svc) is None


def test_concert_add_says_the_guess_and_records_it_apart_from_the_given_page(svc: Services) -> None:
    svc.setlistfm = FakeSetlistFm({("Zed Fox", None, None): ROWS})
    out = concert(svc)
    assert out.kind is Kind.OK
    assert f"\npossible [setlist.fm link]({EARLIER})\n[Open in Google Calendar](" in out.reply
    assert out.data["setlist_guess"] == EARLIER
    # A guess is said and remembered, never written to the event: nothing makes a playlist or a link from it.
    assert all("setlist.fm" not in ln for ln in out.data["description"])
    saved = record_for_event(out.data["id"], svc.state, svc.settings.concerts_dir)[1]
    assert saved.setlist_guess == EARLIER and facts(saved)["setlist_guess"] == EARLIER
    assert ConcertRecord.from_json(saved.to_json()) == saved

    # Once the user has given the show's own page, there is nothing left to guess, and the record's facts say so.
    given = replace(saved, lines=(*saved.lines, f"setlist.fm: {OWN}"))
    assert facts(given)["setlist"] == OWN and "setlist_guess" not in facts(given)
    for e in svc.calendar.events["cal-concerts"]:
        e["description"] += f"\nsetlist.fm: {OWN}"
    again = concert(svc)
    assert again.kind is Kind.OK and "possible" not in again.reply and "setlist_guess" not in again.data


def test_no_page_no_line_and_the_concert_is_still_added(svc: Services) -> None:
    out = concert(svc)  # the fixture's setlist.fm knows no Zed Fox
    assert out.kind is Kind.OK and "possible" not in out.reply and out.data["setlist_guess"] is None
    [(_path, saved)] = all_records(svc.state, svc.settings.concerts_dir)
    assert saved.setlist_guess is None and "setlist_guess" not in facts(saved)
