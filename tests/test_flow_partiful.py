"""flows/partiful.py on the fakes: the invite a ticket link unlocks, the login, and where both surface
(concert_add, the healthcheck, the server's two tools, the CLI's two commands)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from concert_mcp import server
from concert_mcp.config import Settings
from concert_mcp.flows.concert_add import add_concert
from concert_mcp.flows.healthcheck import run_healthcheck
from concert_mcp.flows.partiful import (
    InviteRequest,
    link_event,
    login_outcome,
    make_invite,
    refresh_invite_links,
    request_code,
    tickets_outcome,
)
from concert_mcp.flows.services import Services
from concert_mcp.flows.topics import make_topic_entry, topic_remember
from concert_mcp.result import NEED_USER, Kind
from concert_mcp.text.health import Check
from concert_mcp.text.reply import PARTIFUL_NO_EVENT_REPLY
from conftest import TICKET_TEXT, make_services, run
from fakes import FakeCalendar, FakePartiful

pytest.importorskip("mcp")

LA = ZoneInfo("America/Los_Angeles")
SHOW = datetime(2026, 11, 14, 20, 30, tzinfo=LA)
PHONE = "+15555550100"
TICKETS = "https://www.ticketweb.com/event/zed-fox/123"
TEASER = "https://open.spotify.com/playlist/pl1"
PAGE = "https://partiful.com/e/pf1"
TITLE = "Concert: Zed Fox at The Quarry 11/14"
LOCATION = "The Quarry\n1 Main St\nSan Francisco, CA 94100"
PLACE = {
    "name": "The Quarry",
    "formattedAddressLines": ["1 Main St", "San Francisco, CA 94100", "United States"],
    "structuredAddress": {"postCode": "94100"},
    "country": "United States",
}
ALBUMS = {
    "artist-zedfox": [
        {
            "name": "Burrow",
            "album_type": "album",
            "release_date": "2025-03-01",
            "images": [{"url": "https://i.example/burrow", "width": 640, "height": 640}],
        }
    ]
}
SIGNED_OUT_TEXTED = (
    "Partiful is signed out. I texted a code to the number ending 0100. "
    "Send me the code, then ask for the Partiful again."
)
CALENDARS = [{"id": "cal-concerts", "summary": "Concerts"}, {"id": "primary", "summary": "Personal"}]


def with_partiful(svc: Services, partiful: FakePartiful | None, *, phone: str = PHONE) -> Services:
    """The fixture's services with a Partiful, the account's phone number and album covers on Spotify."""
    svc.settings = replace(svc.settings, partiful_phone=phone)
    svc.partiful = partiful
    if svc.spotify is not None:
        svc.spotify.albums = {k: [dict(a) for a in v] for k, v in ALBUMS.items()}
    return svc


def seed(svc: Services, *, description: str = f"Doors: 7:30 PM\nTeaser playlist: {TEASER}", **kw: Any) -> dict:
    return svc.calendar.seed_event(
        "cal-concerts", summary="Zed Fox", start=SHOW, location=LOCATION, description=description, **kw
    )


def own_topic(svc: Services, event: dict) -> None:
    entry = make_topic_entry(
        event_id=event["id"], calendar_id="cal-concerts", summary="Zed Fox", start=SHOW, now=svc.clock.now()
    )
    topic_remember("-1001234567890:7", entry, svc)


def request(**over: Any) -> InviteRequest:
    base: dict[str, Any] = dict(
        artist="Zed Fox",
        start=SHOW,
        location=LOCATION,
        lines=("Doors: 7:30 PM", f"Teaser playlist: {TEASER}"),
        tickets=TICKETS,
    )
    base.update(over)
    return InviteRequest(**base)


def description(svc: Services, event_id: str) -> str:
    return svc.calendar.get_event("cal-concerts", event_id)["description"]


# ---------- make_invite ----------


def test_an_invite_carries_the_show_the_links_the_place_and_the_poster(svc: Services) -> None:
    partiful = FakePartiful(place=PLACE)
    res = make_invite(request(), with_partiful(svc, partiful), dry_run=False)
    assert (res.status, res.url, res.title, res.poster, res.going) == ("created", PAGE, TITLE, True, True)
    assert partiful.going == [("pf1", "Pat Example", "America/Los_Angeles")]
    assert res.links == (("Tickets", TICKETS), ("Zed Fox songs I like", TEASER))
    [event] = partiful.events
    assert (
        event["title"] == TITLE
        and event["description"] == "Doors 7:30, show 8:30\n\nCreated by github.com/AriSweedler/concert-mcp"
    )
    assert (event["visibility"], event["timezone"]) == ("public", "America/Los_Angeles")
    assert event["startDate"] == "2026-11-15T04:30:00.000Z"
    assert event["customFields"] == [
        {"icon": "link", "value": "Tickets", "url": TICKETS},
        {"icon": "music-note", "value": "Zed Fox songs I like", "url": TEASER},
    ]
    assert event["image"]["upload"]["path"] == "external/user/u1/obj1" and event["image"]["name"] == "Burrow.jpg"
    assert partiful.downloads == ["https://i.example/burrow"] and partiful.uploads == [(b"JPEG-BYTES", "image/jpeg")]
    assert partiful.queries == ["The Quarry, San Francisco, CA 94100"]
    [(event_id, info)] = partiful.locations
    assert event_id == "pf1" and info["type"] == "structured" and info["displayName"] == "The Quarry"
    # The place lookup is the first signed call: a dead session shows before anything is uploaded.
    assert partiful.calls == [
        "find_place",
        "download",
        "upload_image",
        "create_event",
        "set_location",
        "me",
        "rsvp_going",
    ]


def test_an_invite_is_made_once_only_with_a_ticket_link_and_never_on_a_dry_run(svc: Services) -> None:
    partiful = FakePartiful()
    with_partiful(svc, partiful)
    # A show whose setlist.fm page is known links it as a third field; a show without one has two (above).
    page = "https://www.setlist.fm/setlist/zed-fox/2026/the-quarry-7b9c1a2e.html"
    known = make_invite(request(lines=(f"Teaser playlist: {TEASER}", f"Setlist: {page}")), svc)
    assert known.links[2] == ("setlist.fm", page)
    assert partiful.events[0]["customFields"][2] == {"icon": "link", "value": "setlist.fm", "url": page}
    partiful.calls.clear()
    exists = make_invite(request(lines=(f"Partiful: {PAGE}",)), svc)
    assert (exists.status, exists.url) == ("exists", PAGE)
    assert make_invite(request(tickets=None), svc).status == "need_tickets"
    dry = make_invite(request(lines=()), svc, dry_run=True)
    assert (dry.status, dry.title, dry.links) == ("dry_run", TITLE, (("Tickets", TICKETS),))
    assert partiful.calls == []
    svc.partiful = None
    assert make_invite(request(), svc).reason == "Partiful is not set up"


def test_what_an_invite_does_without_a_place_a_poster_or_a_session(svc: Services) -> None:
    # No place found and no album cover: the location as written and Partiful's default poster.
    partiful = FakePartiful()
    with_partiful(svc, partiful)
    svc.spotify.albums = {}
    res = make_invite(request(), svc)
    assert (res.status, res.poster) == ("created", False) and "image" not in partiful.events[0]
    written = {"type": "freeform", "value": "The Quarry, 1 Main St, San Francisco, CA 94100"}
    assert partiful.locations[0][1] == written
    # A place found under another name (the bare street address) is not the venue; an event with no location asks
    # nothing.
    partiful = FakePartiful(place={**PLACE, "name": "1 Main St"})
    make_invite(request(), with_partiful(svc, partiful))
    assert partiful.locations[0][1] == written
    partiful = FakePartiful(place=PLACE)
    make_invite(request(location=""), with_partiful(svc, partiful))
    assert partiful.queries == [] and partiful.locations[0][1] == {"type": "freeform", "value": ""}
    assert partiful.events[0]["title"] == "Concert: Zed Fox 11/14"

    # A failing place lookup, a failing download and a failing location write each cost only their part.
    for broken in ("find_place", "download", "set_location", "rsvp_going"):
        partiful = FakePartiful(place=PLACE, fail=broken)
        res = make_invite(request(), with_partiful(svc, partiful))
        assert res.status == "created", broken
        assert res.poster is (broken != "download") and res.going is (broken != "rsvp_going")
    # An account Partiful has no name for cannot RSVP: the page is made, and nobody is marked.
    nameless = FakePartiful(name=None)
    assert make_invite(request(), with_partiful(svc, nameless)).going is False and nameless.going == []
    svc.spotify = None
    assert make_invite(request(), with_partiful(svc, FakePartiful())).poster is False


def test_a_dead_session_is_stated_and_a_code_is_texted(svc: Services) -> None:
    partiful = FakePartiful(signed_in=False)
    res = make_invite(request(), with_partiful(svc, partiful))
    assert (res.status, res.phone_tail) == ("signed_out", "0100") and partiful.sent == [PHONE]
    # A stored session Partiful no longer takes shows on the first signed call.
    partiful = FakePartiful(signed_out="find_place")
    res = make_invite(request(), with_partiful(svc, partiful))
    assert (res.status, res.phone_tail) == ("signed_out", "0100") and partiful.events == []
    # No number, or a text that cannot be sent: signed out, and nothing claims a code is on its way.
    assert make_invite(request(), with_partiful(svc, FakePartiful(signed_in=False), phone="")).phone_tail is None
    partiful = FakePartiful(signed_in=False, fail="send_code")
    assert make_invite(request(), with_partiful(svc, partiful)).phone_tail is None
    svc.partiful = None
    assert request_code(svc) is None


def test_a_refusal_or_a_crash_is_a_failed_result_never_an_exception(svc: Services) -> None:
    res = make_invite(request(), with_partiful(svc, FakePartiful(fail="create_event")))
    assert (res.status, res.reason) == ("failed", "Partiful refused | call='create_event' status='500'")
    res = make_invite(request(), with_partiful(svc, FakePartiful(crash="create_event")))
    assert (res.status, res.reason) == ("failed", "RuntimeError: create_event blew up")


def test_link_event_adds_a_line_once_and_reports_a_calendar_refusal(svc: Services) -> None:
    event = seed(svc)
    assert link_event("cal-concerts", event["id"], [f"Tickets: {TICKETS}"], svc) == "The calendar event has the links."
    assert description(svc, event["id"]).endswith(f"\nTickets: {TICKETS}")
    link_event("cal-concerts", event["id"], [f"Tickets: {TICKETS}"], svc)
    assert len(svc.calendar.patched) == 1
    svc.calendar.fail = "patch_event"
    note = link_event("cal-concerts", event["id"], [f"Partiful: {PAGE}"], svc)
    assert note == "Calendar event not updated: fake failure"


def test_an_existing_invite_gains_the_links_its_show_gained(svc: Services) -> None:
    page = "https://www.setlist.fm/setlist/zed-fox/2026/the-quarry-7b9c1a2e.html"
    played = "https://open.spotify.com/playlist/pl2"
    before = (f"Tickets: {TICKETS}", f"Teaser playlist: {TEASER}", f"Partiful: {PAGE}")
    after = (*before, f"setlist.fm: {page}", f"Setlist playlist: {played}")
    partiful = FakePartiful()
    make_invite(request(), with_partiful(svc, partiful))
    by_hand = {"icon": "sparkles", "value": "Pregame at mine", "url": "https://example.com/pregame"}
    partiful.events[0]["customFields"].append(by_hand)

    assert refresh_invite_links("Zed Fox", before, svc) is None  # the page already has every link
    assert refresh_invite_links("Zed Fox", after, svc) == "The Partiful has the links too."
    assert [f["value"] for f in partiful.events[0]["customFields"]] == [
        "Tickets",
        "Zed Fox songs I like",
        "Pregame at mine",
        "setlist.fm",
        "Setlist playlist",
    ]
    assert refresh_invite_links("Zed Fox", after[:2], svc) is None  # a show with no page
    # What stops an update is said, and never raised: the playlist and the calendar event are done by then.
    partiful.session = False
    assert refresh_invite_links("Zed Fox", after, svc) == "Partiful not updated: Partiful is signed out."
    for broken, why in (
        ({"signed_out": "event_links"}, "Partiful is signed out"),
        ({"fail": "set_links"}, "Partiful refused | call='set_links' status='500'"),
        ({"crash": "event_links"}, "RuntimeError: event_links blew up"),
    ):
        partiful = FakePartiful(**broken)
        make_invite(request(), with_partiful(svc, partiful))
        assert refresh_invite_links("Zed Fox", after, svc) == f"Partiful not updated: {why}."
    svc.partiful = None
    assert refresh_invite_links("Zed Fox", after, svc) is None


# ---------- tickets_add ----------


def test_a_ticket_link_in_the_shows_topic_makes_the_invite_and_links_both(svc: Services) -> None:
    partiful = FakePartiful(place=PLACE)
    with_partiful(svc, partiful)
    event = seed(svc)
    own_topic(svc, event)
    out = tickets_outcome(f"tickets: {TICKETS}", "", "", svc)
    assert out.kind is Kind.OK and out.error is None
    assert out.reply == (
        f"**Partiful made:** [{TITLE}]({PAGE})\n• [Tickets]({TICKETS})\n• [Zed Fox songs I like]({TEASER})"
    )
    assert description(svc, event["id"]) == (
        f"Doors: 7:30 PM\nTeaser playlist: {TEASER}\nTickets: {TICKETS}\nPartiful: {PAGE}"
    )
    assert out.data["status"] == "created"
    # Asked again, with or without the link: the page the event already has, and no second one.
    again = tickets_outcome("", "", "", svc)
    assert again.kind is Kind.OK and again.reply == f"This show already has a Partiful: [Partiful]({PAGE})"
    assert len(partiful.events) == 1


def test_the_link_stays_on_the_event_across_a_login(svc: Services) -> None:
    partiful = FakePartiful(signed_in=False)
    with_partiful(svc, partiful)
    event = seed(svc)
    own_topic(svc, event)
    out = tickets_outcome(TICKETS, "", "", svc)
    assert out.kind is Kind.NEED_USER and out.reply == SIGNED_OUT_TEXTED and out.error == "signed_out"
    assert description(svc, event["id"]).endswith(f"\nTickets: {TICKETS}")
    # The code arrives, then "the Partiful again" needs no link: the event has it.
    assert login_outcome("123456", svc).reply == "Partiful is signed in as Pat Example."
    out = tickets_outcome("", "", "", svc)
    assert out.kind is Kind.OK and partiful.events[0]["customFields"][0]["url"] == TICKETS
    assert description(svc, event["id"]).endswith(f"\nPartiful: {PAGE}")


def test_tickets_add_asks_for_what_it_lacks(svc: Services, svc_cli: Services) -> None:
    with_partiful(svc_cli, FakePartiful())
    fix = tickets_outcome("the Ticketweb page", "Zed Fox", "2026-11-14", svc_cli)
    assert fix.kind is Kind.FIX and fix.error == "tickets holds no link: the Ticketweb page"
    assert tickets_outcome(TICKETS, "", "", svc_cli).reply == NEED_USER.format(what="artist")
    assert tickets_outcome(TICKETS, "Zed Fox", "", svc_cli).reply == NEED_USER.format(what="date")
    none = tickets_outcome(TICKETS, "Zed Fox", "2026-11-14", svc_cli)
    assert none.kind is Kind.NEED_USER and none.reply == PARTIFUL_NO_EVENT_REPLY
    # A show with no ticket link yet: nothing is made, and the link is asked for.
    seed(svc_cli)
    need = tickets_outcome("", "Zed Fox", "2026-11-14", svc_cli)
    assert need.kind is Kind.NEED_USER and need.error == "need_tickets"
    assert need.reply == "I need the ticket link to make the Partiful. Reply with it and I will make it."
    assert svc_cli.partiful.calls == [] and svc_cli.calendar.patched == []
    # A topic whose event was deleted elsewhere owns nothing: the show must be named.
    with_partiful(svc, FakePartiful())
    gone = seed(svc)
    own_topic(svc, gone)
    svc.calendar.events["cal-concerts"] = []
    assert tickets_outcome(TICKETS, "", "", svc).reply == NEED_USER.format(what="artist")


def test_tickets_add_by_artist_and_date_dry_run_and_failures(svc_cli: Services) -> None:
    partiful = FakePartiful()
    with_partiful(svc_cli, partiful)
    event = seed(svc_cli, all_day=True)
    dry = tickets_outcome(TICKETS, "Zed Fox", "2026-11-14", svc_cli, dry_run=True)
    assert dry.kind is Kind.DRY_RUN and dry.error is None
    assert dry.reply == (
        f"**Would make the Partiful** {TITLE}\n• [Tickets]({TICKETS})\n• [Zed Fox songs I like]({TEASER})"
    )
    assert partiful.calls == [] and svc_cli.calendar.patched == []

    partiful.fail = "create_event"
    failed = tickets_outcome(TICKETS, "Zed Fox", "2026-11-14", svc_cli)
    assert failed.kind is Kind.ERROR and failed.reply.startswith("Partiful failed: Partiful refused")
    assert failed.error == "Partiful refused | call='create_event' status='500'"
    # No number to text: signed out is the operator's to fix, so it is an error, not a question.
    with_partiful(svc_cli, FakePartiful(signed_in=False), phone="")
    silent = tickets_outcome("", "Zed Fox", "2026-11-14", svc_cli)
    assert silent.kind is Kind.ERROR and silent.reply.startswith("Partiful is signed out. Admin: set")

    svc_cli.calendar.events["cal-concerts"][0]["start"] = {}
    crash = tickets_outcome("", "Zed Fox", "2026-11-14", svc_cli)
    assert crash.kind is Kind.ERROR and "(ValueError)" in crash.reply
    assert event["id"] == "evt1"
    svc_cli.state.files.clear()  # no record of the show: it is searched for, and the calendar refuses the search
    svc_cli.calendar.fail = "list_events"
    refused = tickets_outcome(TICKETS, "Zed Fox", "2026-11-14", svc_cli)
    assert refused.kind is Kind.ERROR and refused.data == {"provider": "gws"}


# ---------- partiful_login ----------


def test_login_is_a_code_texted_and_then_the_code(svc: Services) -> None:
    partiful = FakePartiful(signed_in=False)
    with_partiful(svc, partiful)
    asked = login_outcome("", svc)
    assert asked.kind is Kind.NEED_USER and partiful.sent == [PHONE]
    assert asked.reply == "I texted a Partiful code to the number ending 0100. Send me the code."
    wrong = login_outcome("000000", svc)
    assert wrong.kind is Kind.ERROR
    assert wrong.reply == "Partiful did not accept that code. Ask me to log in to Partiful for a new one."
    done = login_outcome(" 123 456 ", svc)
    assert done.kind is Kind.OK and done.reply == "Partiful is signed in as Pat Example." and partiful.session
    partiful.name = None
    assert login_outcome("123456", svc).reply == "Partiful is signed in."


def test_login_without_a_number_on_a_dry_run_and_through_a_crash(svc: Services) -> None:
    no_phone = login_outcome("", with_partiful(svc, FakePartiful(), phone=""))
    assert no_phone.kind is Kind.ERROR and no_phone.reply == (
        "Partiful has no phone number to text a code to. Admin: set CONCERT_MCP_PARTIFUL_PHONE (README.md#partiful)."
    )
    assert login_outcome("", with_partiful(svc, None)).kind is Kind.ERROR
    partiful = FakePartiful(crash="send_code")
    dry = login_outcome("", with_partiful(svc, partiful), dry_run=True)
    assert dry.kind is Kind.DRY_RUN and dry.reply.startswith("**Would log in to Partiful**") and partiful.calls == []
    assert "(RuntimeError)" in login_outcome("", svc).reply


# ---------- concert_add ----------


def concert(svc: Services, text: str) -> Any:
    return add_concert(
        artist="Zed Fox with special guest Zork.",
        start="2026-11-14 20:30",
        venue="The Quarry, San Francisco",
        source_text=text,
        svc=svc,
    )


def test_concert_add_with_a_tickets_line_expresses_the_show_as_an_invite(
    settings: Settings, now: datetime, forum_route: dict[str, Any]
) -> None:
    text = f"{TICKET_TEXT}tickets: {TICKETS}\n"
    svc = make_services(settings, now, route=forum_route, text=text)
    partiful = FakePartiful(place=PLACE)
    with_partiful(svc, partiful)
    out = concert(svc, text)
    assert out.kind is Kind.OK
    assert f"• [Tickets]({TICKETS})\n• [Partiful]({PAGE})\n• [Teaser playlist]({TEASER}), 3 liked songs" in out.reply
    assert "Partiful not made" not in out.reply
    stored = description(svc, out.data["id"]).splitlines()
    assert f"Tickets: {TICKETS}" in stored and stored[-1] == f"Partiful: {PAGE}"
    assert out.data["partiful"]["status"] == "created" and out.data["description"][-1] == f"Partiful: {PAGE}"
    [event] = partiful.events
    assert event["title"] == "Concert: Zed Fox at The Quarry 11/14"
    assert event["description"] == "Doors 7:30, show 8:30\n\nCreated by github.com/AriSweedler/concert-mcp"
    assert [f["value"] for f in event["customFields"]] == ["Tickets", "Zed Fox songs I like"]

    # The same ticket again in the topic updates the event; the show keeps its one page.
    again = concert(svc, text)
    assert again.kind is Kind.OK and again.reply.startswith("**Updated on Concerts**")
    assert f"• [Partiful]({PAGE})" in again.reply and len(partiful.events) == 1
    assert again.data["partiful"]["status"] == "exists"
    assert again.data["description"].count(f"Partiful: {PAGE}") == 1


def test_concert_add_lands_whatever_partiful_does(
    settings: Settings, now: datetime, forum_route: dict[str, Any]
) -> None:
    text = f"{TICKET_TEXT}tickets: {TICKETS}\n"
    svc = make_services(settings, now, route=forum_route, text=text)
    with_partiful(svc, FakePartiful(signed_in=False))
    out = concert(svc, text)
    assert out.kind is Kind.OK and out.reply.startswith("**Added to Concerts**")
    assert f"\nPartiful not made: {SIGNED_OUT_TEXTED}\n" in out.reply
    assert f"Tickets: {TICKETS}" in description(svc, out.data["id"])
    assert out.data["partiful"]["status"] == "signed_out"

    # Even the calendar refusing the step's own read is a note on a reply that still has its link.
    calendar = FakeCalendar(calendars=CALENDARS, fail="get_event")
    svc = make_services(settings, now, route=None, text=text)
    svc.calendar = calendar
    out = concert(with_partiful(svc, FakePartiful()), text)
    assert out.kind is Kind.OK and "\nPartiful not made: Partiful failed: ProviderError: fake failure\n" in out.reply


# ---------- healthcheck, server, CLI ----------


def test_partiful_health_rows(settings: Settings, svc: Services) -> None:
    def rows(**kw: Any) -> dict[str, Check]:
        report = run_healthcheck(settings, only=["partiful-session", "partiful-auth"], **kw)
        return {c.name: c for c in report.checks}

    plain = rows()
    assert plain["partiful-session"].status == "SKIP" and "log in to Partiful" in plain["partiful-session"].evidence
    assert plain["partiful-auth"] == Check("partiful-auth", "SKIP", "pass --probe")
    path = Path(settings.partiful_credentials)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"refresh_token": "rt-1"}), encoding="utf-8")
    path.chmod(0o600)
    assert rows()["partiful-session"] == Check("partiful-session", "PASS", "present, mode 600")

    assert rows(probe=True, svc=svc)["partiful-auth"] == Check("partiful-auth", "SKIP", "not signed in")
    svc.partiful = FakePartiful(signed_in=False)
    assert rows(probe=True, svc=svc)["partiful-auth"].evidence == "not signed in"
    svc.partiful = FakePartiful()
    assert rows(probe=True, svc=svc)["partiful-auth"] == Check("partiful-auth", "PASS", "Pat Example")
    svc.partiful = FakePartiful(name=None)
    assert rows(probe=True, svc=svc)["partiful-auth"].evidence == "signed in"
    svc.partiful = FakePartiful(signed_out="me")
    failed = rows(probe=True, svc=svc)["partiful-auth"]
    assert (failed.status, failed.evidence, failed.anchor) == ("FAIL", "Partiful is signed out | fake", "partiful")
    assert failed.fix == "tell the bot to log in to Partiful, or run concert-mcp partiful-login"


def test_the_two_tools_and_the_two_commands(svc_cli: Services, cli_env: Services, monkeypatch, capsys) -> None:
    server.build_server(with_partiful(svc_cli, FakePartiful(signed_in=False)))
    fix = server.tickets_add(tickets="the Ticketweb page", artist="Zed Fox", date="2026-11-14")
    assert fix.startswith("FIX: pass the ticket page's address in tickets")
    assert fix.endswith("Call tickets_add once more with only that change.")
    sent = server.partiful_login()
    assert sent == (
        "I texted a Partiful code to the number ending 0100. Send me the code.\nNOT SENT: no live chat (CLI run)"
    )
    assert server.partiful_login(code="123456").startswith("Partiful is signed in as Pat Example.")

    code, out, _err = run(monkeypatch, capsys, ["tickets", "--link", "nope", "--artist", "Zed Fox", "--no-send"])
    assert code == 1 and out.startswith("FIX: pass the ticket page's address in tickets")
    code, out, _err = run(monkeypatch, capsys, ["partiful-login", "--code", "123456", "--no-send"])
    assert code == 0 and out.startswith("Partiful is signed in as Pat Example.")
