"""server.py: the nine tools, their string-only schemas, the instructions, and the output contract on fake services."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

pytest.importorskip("mcp")

from concert_mcp.config import Settings, load_settings  # noqa: E402
from concert_mcp.flows.services import Services  # noqa: E402
from concert_mcp.result import NEED_USER, NO_REPLY_TEXT  # noqa: E402
from concert_mcp.server import (  # noqa: E402
    DESCRIPTIONS,
    INSTRUCTIONS,
    TOOLS,
    build_server,
    concert_add,
    event_delete,
    healthcheck,
    queue_status,
)
from concert_mcp.text.reply import NO_CANDIDATES_REPLY, WHICH_EVENT_REPLY  # noqa: E402
from conftest import make_services, settings_env  # noqa: E402
from fakes import FakeCalendar  # noqa: E402

NINE = {
    "concert_add",
    "calendar_add",
    "event_delete",
    "teaser_playlist",
    "setlist_find",
    "setlist_playlist",
    "lineup",
    "queue_status",
    "healthcheck",
}
FIX_TAIL = "Call concert_add once more with only that change."


async def _tools(svc: Services) -> dict[str, Any]:
    mcp = build_server(svc)
    return {t.name: t for t in await mcp.list_tools()}


# ---------- registration and schema ----------


async def test_registers_exactly_the_nine_tools(svc: Services) -> None:
    assert set(await _tools(svc)) == NINE
    assert {fn.__name__ for fn in TOOLS} == NINE


async def test_every_parameter_is_a_described_string_with_a_default(svc: Services) -> None:
    for name, tool in (await _tools(svc)).items():
        props = tool.input_schema.get("properties", {})
        assert "required" not in tool.input_schema or not tool.input_schema["required"], name
        for pname, prop in props.items():
            assert prop.get("type") == "string", (name, pname)
            assert prop.get("description", "").strip(), (name, pname)
            assert prop.get("default") == "", (name, pname)


async def test_queue_status_and_healthcheck_take_no_parameters(svc: Services) -> None:
    tools = await _tools(svc)
    assert tools["queue_status"].input_schema.get("properties", {}) == {}
    assert tools["healthcheck"].input_schema.get("properties", {}) == {}


async def test_descriptions_are_short_and_literal(svc: Services) -> None:
    for name, tool in (await _tools(svc)).items():
        assert tool.description == DESCRIPTIONS[name]
        assert 0 < len(tool.description) < 300, name
    for name in (
        "concert_add",
        "calendar_add",
        "event_delete",
        "teaser_playlist",
        "setlist_find",
        "setlist_playlist",
        "lineup",
        "queue_status",
    ):
        assert "NO_REPLY" in DESCRIPTIONS[name], name


async def test_annotations_as_designed(svc: Services) -> None:
    tools = await _tools(svc)
    read_only = {n for n, t in tools.items() if t.annotations is not None and t.annotations.read_only_hint}
    destructive = {n for n, t in tools.items() if t.annotations is not None and t.annotations.destructive_hint}
    assert read_only == {"setlist_find", "queue_status", "healthcheck"}
    assert destructive == {"event_delete"}


def test_instructions_fit_the_system_prompt_and_name_the_contract() -> None:
    assert len(INSTRUCTIONS) < 2000
    assert "NO_REPLY" in INSTRUCTIONS
    assert "FIX:" in INSTRUCTIONS
    assert "->" in INSTRUCTIONS and "→" not in INSTRUCTIONS
    assert [line[:2] for line in INSTRUCTIONS.splitlines()[1:]] == [f"{i}." for i in range(1, 10)]


def test_server_carries_the_instructions(svc: Services) -> None:
    assert build_server(svc).instructions == INSTRUCTIONS


# ---------- the output contract through the tool functions ----------


def test_bad_start_is_a_fix_line_with_the_retry_sentence(svc: Services) -> None:
    text = concert_add(artist="Zed Fox", start="tomorrow", venue="The Quarry")
    assert text.startswith("FIX: start")
    assert text.endswith(FIX_TAIL)
    assert "\n" not in text


async def test_null_artist_is_cleaned_and_asks_the_user(svc_cli: Services) -> None:
    mcp = build_server(svc_cli)
    res = await mcp.call_tool("concert_add", {"artist": "null"})
    assert not res.is_error
    assert res.content[0].text == NEED_USER.format(what="artist") + "\nNOT SENT: no live chat (CLI run)"


@pytest.mark.parametrize(
    ("tool", "arguments", "expect"),
    [
        ("event_delete", {"pick": 2}, NO_CANDIDATES_REPLY),  # a number where a string is due
        ("event_delete", {"title": None}, WHICH_EVENT_REPLY),  # an explicit null
        (
            "event_delete",
            {"title": '"Zed Fox"', "date": "2026-11-14"},
            "I found no event like **Zed Fox** around 2026-11-14",
        ),
        ("concert_add", {"artist": None, "start": 2026}, NEED_USER.format(what="artist")),
    ],
)
async def test_non_string_arguments_are_cleaned_not_schema_errors(
    svc_cli: Services, tool: str, arguments: dict[str, Any], expect: str
) -> None:
    """The small model sends numbers and nulls; the contract answers, never pydantic ("Input should be a valid string")."""
    mcp = build_server(svc_cli)
    res = await mcp.call_tool(tool, arguments)
    assert not res.is_error
    assert res.content[0].text.startswith(expect)
    assert "pydantic" not in res.content[0].text


def test_full_ticket_in_a_live_topic_returns_exactly_no_reply(svc: Services, ticket_text: str) -> None:
    build_server(svc)
    text = concert_add(
        artist="Zed Fox", start="2026-11-14 20:30", venue="The Quarry, San Francisco", source_text=ticket_text
    )
    assert text == NO_REPLY_TEXT
    assert len(svc.chat.sent) == 1  # type: ignore[attr-defined]
    assert svc.chat.sent[0][1].startswith("**Added to Concerts**")  # type: ignore[attr-defined]


async def test_call_tool_path_matches_the_function_path(svc: Services, ticket_text: str) -> None:
    mcp = build_server(svc)
    res = await mcp.call_tool("concert_add", {"artist": "Zed Fox", "start": "tomorrow"})
    assert not res.is_error
    assert res.content[0].text.startswith("FIX: start")
    assert res.content[0].text.endswith(FIX_TAIL)


async def test_call_tool_with_no_arguments_is_handled_in_code(svc_cli: Services) -> None:
    mcp = build_server(svc_cli)
    res = await mcp.call_tool("concert_add", {})
    assert not res.is_error
    assert res.content[0].text.startswith(NEED_USER.format(what="artist"))


def test_healthcheck_reports_the_missing_key_file(svc_cli: Services) -> None:
    build_server(svc_cli)
    text = healthcheck()
    assert "FAIL  gws-keyfile" in text
    assert "README.md#" in text


def test_queue_status_on_an_empty_queue(svc_cli: Services) -> None:
    build_server(svc_cli)
    assert queue_status().startswith("The setlist queue is empty.")


class _ExplodingCalendar(FakeCalendar):
    def list_events(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("boom")


def test_a_crash_inside_the_flow_is_a_reply_not_an_exception(
    settings: Settings, now: datetime, ticket_text: str
) -> None:
    svc = make_services(settings, now, route=None, text=ticket_text)
    svc.calendar = _ExplodingCalendar(calendars=[{"id": "cal-concerts", "summary": "Concerts"}])
    build_server(svc)
    text = concert_add(artist="Zed Fox", start="2026-11-14 20:30", venue="The Quarry", source_text=ticket_text)
    assert text.startswith("Something broke inside the concert tool (RuntimeError). Admin: check the gateway log.")


def test_a_failure_to_build_services_is_a_reply_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    import concert_mcp.server as server

    def explode() -> Services:
        raise OSError("no home")

    build_server(None)
    monkeypatch.setattr(server, "build_services", explode)
    text = queue_status()
    assert text.startswith("Something broke inside the concert tool (OSError).")
    assert "NOT SENT" in text


def test_event_delete_with_a_bare_number_picks_from_the_candidates_file(svc_cli: Services, now: datetime) -> None:
    cal = svc_cli.calendar
    assert isinstance(cal, FakeCalendar)
    first = cal.seed_event("cal-concerts", summary="Zed Fox", start=datetime(2026, 11, 14, 20, 30, tzinfo=now.tzinfo))
    second = cal.seed_event("cal-concerts", summary="Zed Fox", start=datetime(2026, 11, 15, 20, 30, tzinfo=now.tzinfo))
    rows = [
        {
            "id": e["id"],
            "calendarId": "cal-concerts",
            "calendar": "Concerts",
            "summary": e["summary"],
            "when": when,
            "start": e["start"]["dateTime"],
            "location": "The Quarry",
            "htmlLink": e["htmlLink"],
            "description": "",
        }
        for e, when in ((first, "Sat Nov 14, 8:30 PM – 10:30 PM PST"), (second, "Sun Nov 15, 8:30 PM – 10:30 PM PST"))
    ]
    svc_cli.state.write_json(svc_cli.settings.candidates_file, rows)
    build_server(svc_cli)
    text = event_delete(title="2")
    assert text.startswith("**Removed from Concerts**")
    assert cal.deleted == [("cal-concerts", second["id"])]
    assert svc_cli.state.read_json(svc_cli.settings.candidates_file, None) is None


def test_dry_run_setting_makes_concert_add_change_nothing(
    tmp_path, now: datetime, forum_route, ticket_text: str
) -> None:
    env = settings_env(tmp_path) | {"CONCERT_MCP_DRY_RUN": "1"}
    svc = make_services(load_settings(env, home=tmp_path), now, route=forum_route, text=ticket_text)
    build_server(svc)
    text = concert_add(
        artist="Zed Fox", start="2026-11-14 20:30", venue="The Quarry, San Francisco", source_text=ticket_text
    )
    assert text.startswith("**Would add to Concerts**")
    assert text.endswith("NOT SENT: dry run")
    assert svc.chat.sent == []  # type: ignore[attr-defined]
    assert svc.calendar.inserted == []  # type: ignore[attr-defined]
    assert svc.spotify.created == []  # type: ignore[attr-defined]
