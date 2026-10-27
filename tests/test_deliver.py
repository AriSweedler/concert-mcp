"""flows/deliver.py: the NOT SENT statuses, dedupe, the sent log, the four rendered shapes, fix_guard and run_tool."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

from concert_mcp.config import Settings
from concert_mcp.flows import deliver as mod
from concert_mcp.flows.deliver import (
    FIX_GUARD_ADD,
    FIX_GUARD_DELETE,
    FIX_GUARD_OTHER,
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


def test_no_reply_is_not_sent(svc: Services) -> None:
    assert deliver(Outcome(Kind.OK, data={"count": 0}), svc, send=True) == Delivery(False, "NOT SENT: no reply")
    assert svc.chat.attempts == []


def test_fix_is_never_delivered(svc: Services) -> None:
    fix_with_reply = Outcome(Kind.FIX, reply="should not go out", fix="x")
    assert deliver(fix_with_reply, svc, send=True) == Delivery(False, "NOT SENT: fix")
    assert svc.chat.attempts == []


def test_dry_run_is_not_sent(svc: Services) -> None:
    dry = Outcome(Kind.DRY_RUN, reply="**Would add to Concerts**")
    assert deliver(dry, svc, send=True) == Delivery(False, "NOT SENT: dry run")
    assert svc.chat.attempts == []


def test_send_false_disables_delivery(svc: Services) -> None:
    assert deliver(OK, svc, send=False) == Delivery(False, "NOT SENT: delivery disabled")
    assert svc.chat.attempts == []


def test_no_live_route_is_a_cli_run(svc_cli: Services) -> None:
    assert deliver(OK, svc_cli, send=True) == Delivery(False, "NOT SENT: no live chat (CLI run)")
    assert svc_cli.chat.attempts == []


def test_route_without_a_chat_target(settings: Settings, now: datetime, ticket_text: str) -> None:
    route = {"channel": "telegram", "target": {}, "thread": None, "accountId": None}
    svc = make_services(settings, now, route=route, text=ticket_text)
    assert deliver(OK, svc, send=True) == Delivery(False, "NOT SENT: no chat target in the route")
    assert svc.chat.attempts == []


# ---------- deliver: the happy path, the sent log and dedupe ----------


def test_sent_writes_the_sent_log(svc: Services) -> None:
    d = deliver(OK, svc, send=True)
    assert d.sent is True
    assert d.status.startswith("SENT to telegram ")
    assert "topic 7" in d.status
    assert len(svc.chat.sent) == 1
    target, text = svc.chat.sent[0]
    assert text == REPLY
    assert target.channel == "telegram"
    assert _sent_log(svc) == {"text": REPLY, "at": svc.clock.time()}


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


def test_a_different_reply_is_not_deduped(svc: Services) -> None:
    assert deliver(OK, svc, send=True).sent is True
    other = Outcome(Kind.OK, reply=REPLY + "\nmore")
    assert deliver(other, svc, send=True).sent is True
    assert len(svc.chat.sent) == 2


def test_dedupe_survives_a_garbage_sent_log(svc: Services) -> None:
    svc.state.write_json(svc.settings.sent_log, ["not", "a", "dict"])
    assert deliver(OK, svc, send=True).sent is True


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


def test_render_fix_without_tool_is_the_legacy_line() -> None:
    not_sent = Delivery(False, "NOT SENT: fix")
    assert render_tool_result(FIX, not_sent) == 'FIX: start must look like "2026-11-03 20:00"'


def test_render_fix_with_tool_appends_the_call_once_more_sentence() -> None:
    not_sent = Delivery(False, "NOT SENT: fix")
    assert render_tool_result(FIX, not_sent, tool="concert_add") == (
        'FIX: start must look like "2026-11-03 20:00" Call concert_add once more with only that change.'
    )


def test_render_fix_with_no_fix_text() -> None:
    assert render_tool_result(Outcome(Kind.FIX, error="x"), Delivery(False, "NOT SENT: fix")) == "FIX: "


def test_render_sent_is_exactly_the_no_reply_block() -> None:
    text = render_tool_result(OK, Delivery(True, "SENT to telegram some-chat topic 7"))
    assert text == NO_REPLY_TEXT
    assert text.startswith("NO_REPLY\n")
    assert text.endswith("Your entire reply must be exactly: NO_REPLY")


def test_render_not_sent_is_reply_then_status() -> None:
    text = render_tool_result(OK, Delivery(False, "NOT SENT: no live chat (CLI run)"))
    assert text == REPLY + "\nNOT SENT: no live chat (CLI run)"


def test_render_no_reply_is_the_json_record() -> None:
    outcome = Outcome(Kind.OK, data={"count": 0, "entries": []})
    text = render_tool_result(outcome, Delivery(False, "NOT SENT: no reply"))
    assert json.loads(text) == {"ok": True, "count": 0, "entries": []}
    assert text == json.dumps(outcome.to_json(), ensure_ascii=False)


# ---------- fix_guard ----------


def _fix_log(svc: Services) -> dict[str, Any]:
    return svc.state.read_json(svc.settings.fix_log, {})


def test_two_fixes_pass_through_and_the_log_grows(svc: Services) -> None:
    clock: FakeClock = svc.clock  # type: ignore[assignment]
    t0 = clock.time()
    assert fix_guard(FIX, svc, "concert_add") == FIX
    assert _fix_log(svc) == {"concert_add": [t0]}
    clock.advance(30)
    assert fix_guard(FIX, svc, "concert_add") == FIX
    assert _fix_log(svc) == {"concert_add": [t0, t0 + 30]}


def test_third_fix_within_the_window_asks_the_user_and_clears(svc: Services) -> None:
    clock: FakeClock = svc.clock  # type: ignore[assignment]
    fix_guard(FIX, svc, "concert_add")
    clock.advance(400)
    fix_guard(FIX, svc, "concert_add")
    clock.advance(400)  # 800 s after the first: still inside the 900 s window
    out = fix_guard(FIX, svc, "concert_add")
    assert out.kind is Kind.NEED_USER
    assert out.reply == FIX_GUARD_ADD
    assert out.reply == FIX_GUARD_REPLIES["concert_add"]
    assert out.error == "repeated FIX: start 'tomorrow' is not a date"
    assert out.fix is None
    assert _fix_log(svc) == {"concert_add": []}


def test_a_success_clears_the_tool_log_and_passes_through(svc: Services) -> None:
    fix_guard(FIX, svc, "concert_add")
    fix_guard(FIX, svc, "concert_add")
    assert len(_fix_log(svc)["concert_add"]) == 2
    assert fix_guard(OK, svc, "concert_add") == OK
    assert _fix_log(svc) == {"concert_add": []}
    # Back to zero: two more FIXes are still only two.
    fix_guard(FIX, svc, "concert_add")
    assert fix_guard(FIX, svc, "concert_add") == FIX


def test_entries_older_than_900_s_are_pruned(svc: Services) -> None:
    clock: FakeClock = svc.clock  # type: ignore[assignment]
    fix_guard(FIX, svc, "concert_add")
    fix_guard(FIX, svc, "concert_add")
    clock.advance(901)
    out = fix_guard(FIX, svc, "concert_add")
    assert out == FIX  # the two stale ones are gone; this is the first fresh one
    assert _fix_log(svc) == {"concert_add": [clock.time()]}


def test_logs_are_per_tool(svc: Services) -> None:
    fix_guard(FIX, svc, "concert_add")
    fix_guard(FIX, svc, "concert_add")
    assert fix_guard(FIX, svc, "event_delete") == FIX
    assert len(_fix_log(svc)["concert_add"]) == 2
    assert len(_fix_log(svc)["event_delete"]) == 1


def test_guard_replies_per_tool_and_the_default(svc: Services) -> None:
    for tool, expected in (
        ("event_delete", FIX_GUARD_DELETE),
        ("calendar_add", FIX_GUARD_REPLIES["calendar_add"]),
        ("teaser_playlist", FIX_GUARD_OTHER),
    ):
        fix_guard(FIX, svc, tool)
        fix_guard(FIX, svc, tool)
        out = fix_guard(FIX, svc, tool)
        assert out.kind is Kind.NEED_USER
        assert out.reply == expected
    assert FIX_GUARD_REPLIES["calendar_add"].startswith("I could not get the details right")
    assert "title, date, time and place" in FIX_GUARD_REPLIES["calendar_add"]


def test_guard_tolerates_a_garbage_log(svc: Services) -> None:
    svc.state.write_json(svc.settings.fix_log, {"concert_add": "nope", "other": [1, "x", 2.5]})
    assert fix_guard(FIX, svc, "concert_add") == FIX
    assert _fix_log(svc)["concert_add"] == [svc.clock.time()]


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


def test_run_tool_happy_path_sends_and_renders_no_reply(svc: Services) -> None:
    outcome, delivery, text = run_tool(lambda: OK, svc, tool="concert_add", guard=True)
    assert outcome == OK
    assert delivery.sent is True
    assert text == NO_REPLY_TEXT
    assert len(svc.chat.sent) == 1


def test_run_tool_crash_becomes_the_crash_reply(svc_cli: Services) -> None:
    def boom() -> Outcome:
        raise RuntimeError("kaboom")

    outcome, delivery, text = run_tool(boom, svc_cli, tool="concert_add", send=True)
    assert outcome.kind is Kind.ERROR
    assert outcome.reply == "Something broke inside the concert tool (RuntimeError). Admin: check the gateway log."
    assert outcome.error == "RuntimeError('kaboom')"
    assert delivery == Delivery(False, "NOT SENT: no live chat (CLI run)")
    assert text == outcome.reply + "\nNOT SENT: no live chat (CLI run)"


def test_run_tool_send_none_uses_settings_send(settings_nosend: Settings, now: datetime, forum_route: dict) -> None:
    svc = make_services(settings_nosend, now, route=forum_route, text="")
    assert svc.settings.send is False
    _outcome, delivery, text = run_tool(lambda: OK, svc)
    assert delivery == Delivery(False, "NOT SENT: delivery disabled")
    assert text == REPLY + "\nNOT SENT: delivery disabled"
    assert svc.chat.attempts == []
    # An explicit send=True overrides the setting.
    _outcome, delivery, _text = run_tool(lambda: OK, svc, send=True)
    assert delivery.sent is True


def test_run_tool_fix_is_rendered_with_the_tool_sentence_and_not_sent(svc: Services) -> None:
    outcome, delivery, text = run_tool(lambda: FIX, svc, tool="concert_add", guard=True)
    assert outcome == FIX
    # A FIX carries no reply, so the first rule in deliver() is the one that fires.
    assert delivery == Delivery(False, "NOT SENT: no reply")
    assert text.startswith("FIX: ") and text.endswith(" Call concert_add once more with only that change.")
    assert svc.chat.attempts == []


def test_run_tool_guard_turns_the_third_fix_into_a_delivered_reply(svc: Services) -> None:
    for _ in range(2):
        run_tool(lambda: FIX, svc, tool="concert_add", guard=True)
    outcome, delivery, text = run_tool(lambda: FIX, svc, tool="concert_add", guard=True)
    assert outcome.kind is Kind.NEED_USER
    assert delivery.sent is True
    assert text == NO_REPLY_TEXT
    assert svc.chat.sent[-1][1] == FIX_GUARD_ADD


def test_run_tool_without_guard_never_touches_the_fix_log(svc: Services) -> None:
    for _ in range(4):
        _o, _d, text = run_tool(lambda: FIX, svc, tool="concert_add", guard=False)
        assert text.startswith("FIX: ")
    assert _fix_log(svc) == {}


def test_run_tool_guard_without_a_tool_name_is_a_no_op(svc: Services) -> None:
    for _ in range(4):
        outcome, _d, _t = run_tool(lambda: FIX, svc, guard=True)
        assert outcome == FIX
    assert _fix_log(svc) == {}


def test_module_is_the_only_place_that_spells_the_statuses() -> None:
    # The contract strings live here, so the server and CLI can only relay them.
    assert mod.NO_REPLY_TEXT is NO_REPLY_TEXT
    assert FIX_GUARD_REPLIES == {
        "concert_add": FIX_GUARD_ADD,
        "calendar_add": mod.FIX_GUARD_CALENDAR,
        "event_delete": FIX_GUARD_DELETE,
        "setlist_playlist": mod.FIX_GUARD_SETLIST_PLAYLIST,
    }


@pytest.mark.parametrize("kind", [Kind.OK, Kind.DUPLICATE, Kind.NEED_USER, Kind.ERROR])
def test_every_delivered_kind_goes_out(svc: Services, kind: Kind) -> None:
    assert deliver(Outcome(kind, reply=f"reply for {kind}"), svc, send=True).sent is True
