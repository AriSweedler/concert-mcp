"""Reply delivery and the output contract: the only module that knows SENT / NOT SENT / NO_REPLY / FIX.

Ported from the legacy cal-add.py `out()` and `send_reply()`: the flow returns an Outcome and never prints; this
module decides whether the reply goes to the live chat, dedupes it, sends it, and renders what the model (or the
terminal) sees. `fix_guard` is the retry-loop breaker: the third FIX for one tool inside the context window becomes
a reply that asks the user instead of another FIX the small model would loop on.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable

from concert_mcp.flows.services import Services
from concert_mcp.log import log_debug, log_warn
from concert_mcp.result import NO_REPLY_TEXT, Delivery, Kind, Outcome, crash_outcome
from concert_mcp.text.routes import chat_target

FIX_GUARD_ADD = (
    "I could not get the details right from that after a few tries. "
    "Reply with the artist, date, show time and venue in one line and I will add it."
)
FIX_GUARD_CALENDAR = (
    "I could not get the details right from that after a few tries. "
    "Reply with the title, date, time and place in one line and I will add it."
)
FIX_GUARD_DELETE = (
    "I could not work out which event you mean after a few tries. "
    "Reply with the event name and date in one line and I will look again."
)
FIX_GUARD_OTHER = (
    "I could not get that right after a few tries. "
    "Reply with the artist and the date as YYYY-MM-DD and I will try once more."
)
# The reply fix_guard sends on the third FIX in a row, per tool; tools not listed get FIX_GUARD_OTHER.
FIX_GUARD_REPLIES = {
    "concert_add": FIX_GUARD_ADD,
    "calendar_add": FIX_GUARD_CALENDAR,
    "event_delete": FIX_GUARD_DELETE,
}
FIX_GUARD_LIMIT = 3


def deliver(outcome: Outcome, svc: Services, *, send: bool) -> Delivery:
    """Decide whether and where the reply goes, dedupe, send it, log it. Never raises."""
    try:
        return _deliver(outcome, svc, send=send)
    except Exception as e:  # noqa: BLE001 - delivery must never take the flow's result down with it
        log_warn(f"delivery failed | error='{type(e).__name__}: {e}'")
        return Delivery(False, f"NOT SENT: {type(e).__name__}")


def _deliver(outcome: Outcome, svc: Services, *, send: bool) -> Delivery:
    reply = outcome.reply
    if not reply:
        return Delivery(False, "NOT SENT: no reply")
    if outcome.kind is Kind.FIX:
        return Delivery(False, "NOT SENT: fix")
    if outcome.kind is Kind.DRY_RUN:
        return Delivery(False, "NOT SENT: dry run")
    if not send:
        return Delivery(False, "NOT SENT: delivery disabled")
    settings = svc.settings
    now_s = svc.clock.time()
    _sid, route = svc.context.live_route(now_s)
    if route is None:
        return Delivery(False, "NOT SENT: no live chat (CLI run)")
    target = chat_target(route)
    if target is None:
        return Delivery(False, "NOT SENT: no chat target in the route")
    last = svc.state.read_json(settings.sent_log, {})
    if isinstance(last, dict) and last.get("text") == reply:
        at = last.get("at", 0)
        if isinstance(at, int | float) and now_s - at < settings.dedupe_window_s:
            return Delivery(False, "NOT SENT: identical reply was sent less than 10 minutes ago")
    log_debug(f"sending reply | channel='{target.channel}' thread='{target.thread}' chars='{len(reply)}'")
    status = svc.chat.send(target, reply)
    if status.startswith("SENT"):
        with contextlib.suppress(OSError):
            svc.state.write_json(settings.sent_log, {"text": reply, "at": now_s})
        return Delivery(True, status)
    return Delivery(False, status)


def render_tool_result(outcome: Outcome, delivery: Delivery, *, tool: str | None = None) -> str:
    """FIX: ..., the NO_REPLY block, reply + status, or the JSON record."""
    if outcome.kind is Kind.FIX:
        text = "FIX: " + (outcome.fix or "")
        if tool:
            text += f" Call {tool} once more with only that change."
        return text
    if delivery.sent:
        return NO_REPLY_TEXT
    if outcome.reply:
        return f"{outcome.reply}\n{delivery.status}"
    return json.dumps(outcome.to_json(), ensure_ascii=False)


def _read_fix_log(svc: Services) -> dict[str, list[float]]:
    try:
        log = svc.state.read_json(svc.settings.fix_log, {})
    except Exception:  # noqa: BLE001 - an unreadable log is an empty log
        return {}
    if not isinstance(log, dict):
        return {}
    out: dict[str, list[float]] = {}
    for key, value in log.items():
        if isinstance(value, list):
            out[str(key)] = [float(t) for t in value if isinstance(t, int | float)]
    return out


def _write_fix_log(svc: Services, log: dict[str, list[float]]) -> None:
    try:
        svc.state.write_json(svc.settings.fix_log, log)
    except Exception as e:  # noqa: BLE001 - the guard is advisory; the call's result matters more
        log_warn(f"fix log not written | error='{type(e).__name__}: {e}'")


def fix_guard(outcome: Outcome, svc: Services, tool: str) -> Outcome:
    """Turn the third FIX within the context window into a reply that asks the user."""
    log = _read_fix_log(svc)
    now = svc.clock.time()
    window = svc.settings.context_window_s
    times = [t for t in log.get(tool, []) if now - t < window]
    if outcome.kind is not Kind.FIX:
        log[tool] = []
        _write_fix_log(svc, log)
        return outcome
    times.append(now)
    if len(times) >= FIX_GUARD_LIMIT:
        log[tool] = []
        _write_fix_log(svc, log)
        log_warn(f"repeated FIX; asking the user instead | tool='{tool}' error='{outcome.error}'")
        return Outcome(
            Kind.NEED_USER,
            reply=FIX_GUARD_REPLIES.get(tool, FIX_GUARD_OTHER),
            error=f"repeated FIX: {outcome.error}",
        )
    log[tool] = times
    _write_fix_log(svc, log)
    return outcome


def run_tool(
    fn: Callable[[], Outcome],
    svc: Services,
    *,
    tool: str | None = None,
    send: bool | None = None,
    guard: bool = False,
) -> tuple[Outcome, Delivery, str]:
    """Run a flow, guard, deliver and render; a crash becomes crash_outcome."""
    try:
        outcome = fn()
    except Exception as e:  # noqa: BLE001 - a tool never raises; the crash becomes a reply
        log_warn(f"flow crashed | tool='{tool}' error='{repr(e)[:300]}'")
        outcome = crash_outcome(e, svc.operator)
    if guard and tool:
        outcome = fix_guard(outcome, svc, tool)
    delivery = deliver(outcome, svc, send=svc.settings.send if send is None else send)
    text = render_tool_result(outcome, delivery, tool=tool)
    return outcome, delivery, text
