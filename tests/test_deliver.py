"""flows/deliver.py: the NOT SENT statuses, dedupe, the sent log, the four rendered shapes, fix_guard and run_tool."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from concert_mcp.config import Settings
from concert_mcp.flows import deliver as mod
from concert_mcp.flows.deliver import (
    FIX_GUARD_ADD,
    FIX_GUARD_DELETE,
    FIX_GUARD_REPLIES,
    deliver,
    fix_guard,
    render_tool_result,
    run_tool,
)
from concert_mcp.flows.services import Services
from concert_mcp.result import NO_REPLY_TEXT, Delivery, Kind, Outcome
from conftest import make_services
from fakes import FakeChat, FakeClock

REPLY = "**Added to Concerts**\n• **Artist:** Zed Fox"
OK = Outcome(Kind.OK, reply=REPLY, data={"id": "evt1"})
FIX = Outcome(Kind.FIX, error="start 'tomorrow' is not a date", fix='start must look like "2026-11-03 20:00"')


def _sent_log(svc: Services) -> dict[str, Any]:
    return svc.state.read_json(svc.settings.sent_log, {})


# ---------- deliver: every NOT SENT status ----------


def test_fix_is_never_delivered(svc: Services) -> None:
    fix_with_reply = Outcome(Kind.FIX, reply="should not go out", fix="x")
    assert deliver(fix_with_reply, svc, send=True) == Delivery(False, "NOT SENT: fix")
    assert svc.chat.attempts == []


def test_route_without_a_chat_target(settings: Settings, now: datetime, ticket_text: str) -> None:
    route = {"channel": "telegram", "target": {}, "thread": None, "accountId": None}
    svc = make_services(settings, now, route=route, text=ticket_text)
    assert deliver(OK, svc, send=True) == Delivery(False, "NOT SENT: no chat target in the route")
    assert svc.chat.attempts == []


# ---------- deliver: the happy path, the sent log and dedupe ----------


def test_identical_reply_within_600_s_is_deduped_then_allowed_at_601(svc: Services) -> None:
    assert deliver(OK, svc, send=True).sent is True
    clock: FakeClock = svc.clock  # type: ignore[assignment]
    clock.advance(599)
    assert deliver(OK, svc, send=True) == Delivery(False, "NOT SENT: identical reply was sent less than 10 minutes ago")
    assert len(svc.chat.sent) == 1
    clock.advance(2)  # 601 s after the first send
    again = deliver(OK, svc, send=True)
    assert again.sent is True
    assert len(svc.chat.sent) == 2
    assert _sent_log(svc)["at"] == svc.clock.time()


def test_send_failure_three_times_is_not_sent_and_no_log(svc: Services) -> None:
    svc.chat = FakeChat(fail_sends=3)
    d = deliver(OK, svc, send=True)
    assert d.sent is False
    assert d.status.startswith("NOT SENT: openclaw message send failed 3 times: ")
    assert _sent_log(svc) == {}


def test_deliver_never_raises(svc: Services) -> None:
    class Boom:
        def send(self, target: Any, text: str) -> str:
            raise RuntimeError("socket closed")

        def rename_topic(self, chat_id: str, thread_id: str, name: str) -> str:
            return "topic not renamed: RuntimeError"

    svc.chat = Boom()
    assert deliver(OK, svc, send=True) == Delivery(False, "NOT SENT: RuntimeError")


# ---------- render_tool_result: the four shapes ----------


def test_render_no_reply_is_the_json_record() -> None:
    outcome = Outcome(Kind.OK, data={"count": 0, "entries": []})
    text = render_tool_result(outcome, Delivery(False, "NOT SENT: no reply"))
    assert json.loads(text) == {"ok": True, "count": 0, "entries": []}
    assert text == json.dumps(outcome.to_json(), ensure_ascii=False)


# ---------- fix_guard ----------


def _fix_log(svc: Services) -> dict[str, Any]:
    return svc.state.read_json(svc.settings.fix_log, {})


def test_guard_errors_never_break_the_call(svc: Services) -> None:
    class BrokenState:
        def read_json(self, path: Any, default: Any) -> Any:
            raise OSError("disk gone")

        def write_json(self, path: Any, obj: Any, *, mode: int | None = None) -> None:
            raise OSError("disk gone")

        def remove(self, path: Any) -> None:
            pass

        def mtime(self, path: Any) -> float | None:
            return None

    svc.state = BrokenState()
    assert fix_guard(FIX, svc, "concert_add") == FIX
    assert fix_guard(OK, svc, "concert_add") == OK


# ---------- run_tool ----------


def test_run_tool_crash_becomes_the_crash_reply(svc_cli: Services) -> None:
    def boom() -> Outcome:
        raise RuntimeError("kaboom")

    outcome, delivery, text = run_tool(boom, svc_cli, tool="concert_add", send=True)
    assert outcome.kind is Kind.ERROR
    assert outcome.reply == "Something broke inside the concert tool (RuntimeError). Admin: check the gateway log."
    assert outcome.error == "RuntimeError('kaboom')"
    assert delivery == Delivery(False, "NOT SENT: no live chat (CLI run)")
    assert text == outcome.reply + "\nNOT SENT: no live chat (CLI run)"


def test_run_tool_guard_turns_the_third_fix_into_a_delivered_reply(svc: Services) -> None:
    for _ in range(2):
        run_tool(lambda: FIX, svc, tool="concert_add", guard=True)
    outcome, delivery, text = run_tool(lambda: FIX, svc, tool="concert_add", guard=True)
    assert outcome.kind is Kind.NEED_USER
    assert delivery.sent is True
    assert text == NO_REPLY_TEXT
    assert svc.chat.sent[-1][1] == FIX_GUARD_ADD


def test_module_is_the_only_place_that_spells_the_statuses() -> None:
    # The contract strings live here, so the server and CLI can only relay them.
    assert mod.NO_REPLY_TEXT is NO_REPLY_TEXT
    assert FIX_GUARD_REPLIES == {
        "concert_add": FIX_GUARD_ADD,
        "calendar_add": mod.FIX_GUARD_CALENDAR,
        "event_delete": FIX_GUARD_DELETE,
        "setlist_playlist": mod.FIX_GUARD_SETLIST_PLAYLIST,
    }


def test_guard_tolerates_a_garbage_log(svc: Services) -> None:
    svc.state.write_json(svc.settings.fix_log, {"concert_add": "nope", "other": [1, "x", 2.5]})
    assert fix_guard(FIX, svc, "concert_add") == FIX
    assert _fix_log(svc)["concert_add"] == [svc.clock.time()]
