"""flows/lineup.py on the fakes: find_lineup for every answer shape, the opener's setlist page, the lineup tool."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from concert_mcp.flows.lineup import add_lineup_to_event, find_lineup, lineup_outcome
from concert_mcp.flows.topics import make_topic_entry, topic_remember
from concert_mcp.result import NEED_USER, Kind
from concert_mcp.text.lineup import Lineup, Opener
from conftest import make_services
from fakes import FakeGemini, FakeSetlistFm

LA = ZoneInfo("America/Los_Angeles")
PAGE = "https://www.setlist.fm/setlist/zork/2026/the-hollow-oakland-ca-1a2b3c4d.html"
FULL = json.dumps(
    {"openers": ["Zork", "Zed Fox"], "doors": "7:00 PM", "opener_on_stage": "8:00 PM", "headliner_on_stage": "9pm"}
)
PARTIAL = json.dumps({"openers": [], "doors": "7:30 PM", "opener_on_stage": None, "headliner_on_stage": None})
SOURCES = ("https://venue.example/events/zed-fox",)
ZORK_ROW = {
    "id": "1a2b3c4d",
    "url": PAGE,
    "eventDate": "02-10-2026",
    "artist": {"name": "Zork"},
    "venue": {"name": "The Hollow", "city": {"name": "Oakland"}},
    "sets": {"set": [{"song": [{"name": "Hum"}]}]},
}


def setlistfm_with_zork() -> FakeSetlistFm:
    return FakeSetlistFm({("Zork", None, None): {"setlist": [ZORK_ROW]}})


# ---------- find_lineup ----------


def test_unavailable_without_a_model(svc) -> None:
    assert find_lineup("Zed Fox", "2026-11-14", "The Quarry", None, svc) == Lineup(
        "unavailable", reason="Gemini is not set up"
    )


def test_setlistfm_failure_keeps_the_opener_without_a_page(svc) -> None:
    svc.gemini = FakeGemini(FULL)
    svc.setlistfm = FakeSetlistFm(fail=True)
    lu = find_lineup("Zed Fox", "2026-11-14", "The Quarry", None, svc)
    assert lu.openers == (Opener("Zork", None),)


def test_no_setlistfm_keeps_the_opener_without_a_page(svc) -> None:
    svc.gemini = FakeGemini(FULL)
    svc.setlistfm = None
    assert find_lineup("Zed Fox", "2026-11-14", "The Quarry", None, svc).openers == (Opener("Zork", None),)


# ---------- add_lineup_to_event ----------


def test_add_lineup_survives_a_patch_failure(svc) -> None:
    svc.gemini = FakeGemini(FULL)
    svc.calendar.fail = "patch_event"
    step = add_lineup_to_event(
        artist="Zed Fox",
        date="2026-11-14",
        venue="The Quarry",
        website=None,
        openers_known=False,
        calendar_id="cal-concerts",
        event_id="evt9",
        description=None,
        svc=svc,
    )
    assert step.lines[0] == "Headliner: Zed Fox"


# ---------- lineup_outcome (the tool) ----------


def test_tool_needs_an_artist_outside_a_topic(svc_cli) -> None:
    svc_cli.gemini = FakeGemini(FULL)
    out = lineup_outcome("", "", "", svc_cli)
    assert out.kind is Kind.NEED_USER
    assert out.reply == NEED_USER.format(what="artist")
    assert lineup_outcome("Zed Fox", "", "", svc_cli).reply == NEED_USER.format(what="date")


def test_tool_in_a_topic_takes_the_show_from_the_event_and_patches_it(svc) -> None:
    svc.gemini = FakeGemini(FULL, SOURCES)
    svc.setlistfm = setlistfm_with_zork()
    e = svc.calendar.seed_event(
        "cal-concerts",
        summary="Zed Fox",
        start=datetime(2026, 11, 14, 20, 30, tzinfo=LA),
        location="The Quarry\n123 Example St\nSan Francisco, CA 94110",
        description="Doors: 7:30 PM",
    )
    topic_remember(
        "-1001234567890:7",
        make_topic_entry(
            event_id=e["id"], calendar_id="cal-concerts", summary="Zed Fox", start=e_start(e), now=svc.clock.now()
        ),
        svc,
    )
    out = lineup_outcome("", "", "", svc)
    assert out.kind is Kind.OK, out
    assert out.data["artist"] == "Zed Fox" and out.data["date"] == "2026-11-14" and out.data["venue"] == "The Quarry"
    assert "Zed Fox at The Quarry on 2026-11-14" in svc.gemini.prompts[-1][0]
    assert out.reply.endswith("\nAdded to the calendar event.")
    [(_, eid, fields)] = svc.calendar.patched
    assert eid == e["id"]
    assert fields["description"] == (
        "Doors: 7:30 PM\nHeadliner: Zed Fox\nOpener: Zork\n"
        f"Expected: doors 7:00 PM, Zork 8:00 PM, Zed Fox 9:00 PM\nOpener setlist (recent): {PAGE}"
    )


def test_tool_nothing_found_golden(svc_cli) -> None:
    svc_cli.gemini = FakeGemini("nothing here")
    out = lineup_outcome("Zed Fox", "2026-11-14", "", svc_cli)
    assert (
        out.reply == "**Lineup:** Zed Fox, 2026-11-14\nNo opener or set times announced yet in the sources I can find."
    )


def e_start(e: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(e["start"]["dateTime"])


@pytest.fixture
def svc(settings, now, forum_route, ticket_text):
    return make_services(settings, now, route=forum_route, text=ticket_text)
