"""server.py: the ten tools, their string-only schemas, the instructions, and the output contract on fake services."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

pytest.importorskip("mcp")

from concert_mcp.config import Settings  # noqa: E402
from concert_mcp.flows.services import Services  # noqa: E402
from concert_mcp.result import NEED_USER  # noqa: E402
from concert_mcp.server import (  # noqa: E402
    DESCRIPTIONS,
    INSTRUCTIONS,
    TOOLS,
    build_server,
    concert_add,
    healthcheck,
    queue_status,
)
from concert_mcp.text.reply import NO_CANDIDATES_REPLY, WHICH_EVENT_REPLY  # noqa: E402
from conftest import make_services  # noqa: E402
from fakes import FakeCalendar  # noqa: E402

TEN = {
    "movie_add",
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


async def test_registers_exactly_the_ten_tools(svc: Services) -> None:
    assert set(await _tools(svc)) == TEN
    assert {fn.__name__ for fn in TOOLS} == TEN


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
    rules = INSTRUCTIONS.splitlines()[1:]
    assert [line.split(" ", 1)[0] for line in rules] == [f"{i}." for i in range(1, len(rules) + 1)]


# ---------- the output contract through the tool functions ----------


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


# ---------- every tool body, the listing, and the crash paths outside run_tool ----------


def test_every_tool_body_runs_on_the_bound_services(svc_cli: Services, ticket_text: str) -> None:
    from concert_mcp import server

    build_server(svc_cli)
    assert server.calendar_add(title="Dinner", start="2026-11-20 19:00", location="ZZZ Bistro").startswith(
        "**Added to Personal**"
    )
    assert "Godzilla" in server.movie_add(
        film="Godzilla Minus Zero", start="2026-11-11 14:30", theater="AMC", source_text=""
    )
    assert server.teaser_playlist(artist="Zed Fox", date="2026-11-14").startswith("**Teaser")
    assert server.setlist_find(artist="Zed Fox", date="2026-11-14").startswith("**Setlist**")
    assert server.setlist_playlist(url="https://open.spotify.com/x").startswith("FIX:")
    assert server.lineup(artist="Zed Fox", date="2026-11-14", venue="The Quarry").startswith("FIX:")
    listing = server.tool_listing(svc_cli, ["lineup", "nope", "healthcheck"])
    assert [t["name"] for t in listing] == ["lineup", "healthcheck"] and "inputSchema" in listing[0]
    assert server.call_tool("nope", {}) == "(the model called 'nope', which is not a tool)"
    assert server.call_tool("queue_status", {"extra": "dropped"}).startswith("The setlist queue is empty.")


def test_crash_text_without_settings_and_a_delivery_layer_crash(
    svc_cli: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concert_mcp import server

    monkeypatch.setattr(server, "load_settings", lambda: (_ for _ in ()).throw(OSError("no home")))
    assert "Admin: check the gateway log" in server._crash_text(RuntimeError("x"))
    build_server(svc_cli)
    monkeypatch.setattr(server, "run_tool", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("delivery broke")))
    text = healthcheck()
    assert text.startswith("Something broke inside the concert tool (RuntimeError).") and "NOT SENT" in text
