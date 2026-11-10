"""adapters/openclaw.py on a fake runner, a fake urlopen, a fake clock and a fixture sqlite under tmp_path."""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from concert_mcp.adapters.openclaw import NullContext, OpenClawChat, OpenClawContext
from concert_mcp.config import Settings
from concert_mcp.proc import RunResult
from concert_mcp.text import routes
from concert_mcp.text.routes import ChatTarget
from fakes import FakeClock, FakeRunner, FakeUrlopen, http_error, ms

TELEGRAM = "https://api.telegram.org/bot"
TOKEN = "123456:FAKE-TOKEN-FOR-TESTS"
CHAT = "-1001234567890"


def _routes_ready() -> bool:
    """text/routes.py belongs to the text-music unit; until it lands the context tests cannot be meaningful."""
    try:
        routes.pick_live_route([], 0)
        routes.user_texts([])
    except NotImplementedError:
        return False
    return True


needs_routes = pytest.mark.skipif(not _routes_ready(), reason="text/routes.py is still a stub (unit text-music)")


# --- helpers -------------------------------------------------------------------------------------------------------


def target(thread: str | None = "7", account: str | None = None) -> ChatTarget:
    return ChatTarget(channel="telegram", chat=CHAT, thread=thread, account=account)


def send_prefix(settings: Settings) -> tuple[str, ...]:
    return (settings.openclaw_bin, "message", "send")


def chat(settings: Settings, runner: FakeRunner, clock: FakeClock, urlopen: FakeUrlopen | None = None) -> OpenClawChat:
    return OpenClawChat(settings, clock, runner, urlopen=urlopen or FakeUrlopen())


def write_config(settings: Settings, body: Any) -> None:
    settings.openclaw_config.parent.mkdir(parents=True, exist_ok=True)
    settings.openclaw_config.write_text(json.dumps(body) if not isinstance(body, str) else body, encoding="utf-8")


def route_entry(when: datetime, *, with_route: bool = True, thread: int = 7) -> str:
    entry: dict[str, Any] = {"lastInteractionAt": ms(when)}
    if with_route:
        entry["delivery"] = {
            "route": {
                "channel": "telegram",
                "target": {"to": f"telegram:{CHAT}:topic:{thread}"},
                "thread": {"id": thread},
                "accountId": None,
            }
        }
    return json.dumps(entry)


def user_event(text: str | list[dict[str, Any]], role: str = "user") -> str:
    return json.dumps({"message": {"role": role, "content": text}})


def build_db(
    settings: Settings,
    *,
    sessions: list[tuple[str, str]] = (),
    events: list[tuple[str, int, str | None]] = (),
) -> Path:
    """The two OpenClaw tables the adapter reads, with the given rows."""
    db = settings.session_db
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("create table session_nodes(current_session_id TEXT, entry_json TEXT)")
    con.execute("create table transcript_events(session_id TEXT, created_at INTEGER, event_json TEXT)")
    con.executemany("insert into session_nodes values (?, ?)", list(sessions))
    con.executemany("insert into transcript_events values (?, ?, ?)", list(events))
    con.commit()
    con.close()
    return db


# --- send ----------------------------------------------------------------------------------------------------------


def test_send_argv_with_account_and_thread(settings: Settings, now: datetime) -> None:
    runner = FakeRunner({send_prefix(settings): (0, "", "")})
    clock = FakeClock(now)
    status = chat(settings, runner, clock).send(target(thread="7", account="acct-1"), "hello **there**")
    assert status == f"SENT to telegram {CHAT} topic 7"
    assert len(runner.calls) == 1
    argv, _env, _cwd = runner.calls[0]
    assert argv == (
        settings.openclaw_bin,
        "message",
        "send",
        "--channel",
        "telegram",
        "--target",
        CHAT,
        "-m",
        "hello **there**",
        "--account",
        "acct-1",
        "--thread-id",
        "7",
    )
    assert clock.slept == []


def test_send_runner_error_is_did_not_run_and_stops_at_once(settings: Settings, now: datetime) -> None:
    runner = FakeRunner({send_prefix(settings): lambda argv, kw: RunResult(-1, "", "", "binary not found: openclaw")})
    clock = FakeClock(now)
    status = chat(settings, runner, clock).send(target(), "hi")
    assert status == "NOT SENT: openclaw message send did not run: binary not found: openclaw"
    assert len(runner.calls) == 1
    assert clock.slept == []


# --- bot_token -----------------------------------------------------------------------------------------------------


# --- rename_topic --------------------------------------------------------------------------------------------------


def test_rename_topic_without_token(settings: Settings, now: datetime) -> None:
    urlopen = FakeUrlopen()
    status = chat(settings, FakeRunner(), FakeClock(now), urlopen).rename_topic(CHAT, "7", "2026-11-14 | Zed Fox")
    assert status == "topic not renamed: no bot token (FileNotFoundError)"
    assert urlopen.requests == []


def test_rename_topic_ok_posts_form_and_truncates_name(settings: Settings, now: datetime) -> None:
    write_config(settings, {"channels": {"telegram": {"botToken": TOKEN}}})
    urlopen = FakeUrlopen({("POST", TELEGRAM): {"ok": True, "result": True}})
    name = "N" * 129
    status = chat(settings, FakeRunner(), FakeClock(now), urlopen).rename_topic(CHAT, "7", name)
    assert status == "topic renamed to " + "N" * 128
    assert len(urlopen.requests) == 1
    method, url, _headers, body = urlopen.requests[0]
    assert method == "POST"
    assert url == f"{TELEGRAM}{TOKEN}/editForumTopic"
    assert body is not None
    form = urllib.parse.parse_qs(body.decode())
    assert form == {"chat_id": [CHAT], "message_thread_id": ["7"], "name": ["N" * 128]}


def test_rename_topic_url_error_names_the_type(settings: Settings, now: datetime) -> None:
    write_config(settings, {"channels": {"telegram": {"botToken": TOKEN}}})
    urlopen = FakeUrlopen({("POST", TELEGRAM): urllib.error.URLError("no network")})
    status = chat(settings, FakeRunner(), FakeClock(now), urlopen).rename_topic(CHAT, "7", "x")
    assert status == "topic not renamed: URLError"


def test_token_never_reaches_stderr_or_the_status(
    settings: Settings, now: datetime, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from concert_mcp import log

    log.set_level("debug")
    try:
        write_config(settings, {"channels": {"telegram": {"botToken": TOKEN}}})
        urlopen = FakeUrlopen({("POST", TELEGRAM): http_error(401, "Unauthorized")})
        c = chat(settings, FakeRunner({send_prefix(settings): (1, "", "boom")}), FakeClock(now), urlopen)
        status = c.rename_topic(CHAT, "7", "x")
        c.send(target(), "hi")
    finally:
        log.set_level("info")
    err = capsys.readouterr().err
    assert TOKEN not in status
    assert TOKEN not in err


# --- OpenClawContext.live_route --------------------------------------------------------------------------------------


@needs_routes
def test_live_route_picks_newest_session_with_a_route_inside_the_window(settings: Settings, now: datetime) -> None:
    now_s = now.timestamp()
    build_db(
        settings,
        sessions=[
            ("s_old", route_entry(now - timedelta(seconds=1000), thread=3)),  # has a route, but outside 900 s
            ("s_live", route_entry(now - timedelta(seconds=60), thread=7)),
            ("s_noroute", route_entry(now - timedelta(seconds=10), with_route=False)),  # newest, but no route
            ("s_garbage", "{not json"),
        ],
    )
    sid, route = OpenClawContext(settings).live_route(now_s)
    assert sid == "s_live"
    assert route is not None
    assert route["channel"] == "telegram"
    assert route["thread"] == {"id": 7}
    assert route["target"] == {"to": f"telegram:{CHAT}:topic:7"}


def test_live_route_missing_db(settings: Settings, now: datetime) -> None:
    assert not settings.session_db.exists()
    assert OpenClawContext(settings).live_route(now.timestamp()) == (None, None)
    # Read-only mode must not have created the file as a side effect.
    assert not settings.session_db.exists()


# --- OpenClawContext.recent_text -------------------------------------------------------------------------------------


def write_ocr(settings: Settings, text: str, age_s: float, now_s: float) -> None:
    settings.ocr_last.parent.mkdir(parents=True, exist_ok=True)
    settings.ocr_last.write_text(text, encoding="utf-8")
    os.utime(settings.ocr_last, (now_s - age_s, now_s - age_s))


def test_recent_text_includes_fresh_ocr(settings: Settings, now: datetime) -> None:
    now_s = now.timestamp()
    write_ocr(settings, "ZED FOX\nDoors 7:30 PM", age_s=120, now_s=now_s)
    assert OpenClawContext(settings).recent_text(now_s, None) == "ZED FOX\nDoors 7:30 PM"


@needs_routes
def test_recent_text_only_user_role_texts_str_and_list_content(settings: Settings, now: datetime) -> None:
    now_s = now.timestamp()
    t = ms(now)
    build_db(
        settings,
        events=[
            ("s1", t - 3000, user_event("first user line")),
            ("s1", t - 2000, user_event("the assistant spoke", role="assistant")),
            ("s1", t - 1000, user_event([{"type": "text", "text": "second user line"}, {"type": "image", "url": "x"}])),
            ("s1", t - 500, None),
        ],
    )
    text = OpenClawContext(settings).recent_text(now_s, "s1")
    # order by created_at desc: newest first
    assert text == "second user line\nfirst user line"


# --- NullContext -----------------------------------------------------------------------------------------------------


def test_null_context(now: datetime) -> None:
    ctx = NullContext()
    assert ctx.live_route(now.timestamp()) == (None, None)
    assert ctx.recent_text(now.timestamp(), "s1") == ""
    assert ctx.recent_text(now.timestamp(), None) == ""


def test_send_without_thread_has_no_thread_flag_and_no_topic_in_status(settings: Settings, now: datetime) -> None:
    runner = FakeRunner({send_prefix(settings): (0, "", "")})
    status = chat(settings, runner, FakeClock(now)).send(target(thread=None), "hi")
    assert status == f"SENT to telegram {CHAT}"
    argv = runner.calls[0][0]
    assert "--thread-id" not in argv
    assert "--account" not in argv
    assert " topic" not in status


def test_recent_text_excludes_stale_ocr(settings: Settings, now: datetime) -> None:
    now_s = now.timestamp()
    write_ocr(settings, "old ticket", age_s=2000, now_s=now_s)
    assert OpenClawContext(settings).recent_text(now_s, None) == ""


@needs_routes
def test_recent_text_filters_strictly_by_session_when_given(settings: Settings, now: datetime) -> None:
    now_s = now.timestamp()
    t = ms(now)
    build_db(
        settings,
        events=[
            ("s1", t - 2000, user_event("from s1")),
            ("s2", t - 1000, user_event("from s2")),
        ],
    )
    ctx = OpenClawContext(settings)
    assert ctx.recent_text(now_s, "s1") == "from s1"
    assert ctx.recent_text(now_s, "s2") == "from s2"
    assert ctx.recent_text(now_s, None) == "from s2\nfrom s1"
    # A session with no rows gives nothing: no fallback to other sessions (legacy behaviour).
    assert ctx.recent_text(now_s, "s_empty") == ""


@needs_routes
def test_recent_text_drops_rows_older_than_the_window(settings: Settings, now: datetime) -> None:
    now_s = now.timestamp()
    t = ms(now)
    build_db(
        settings,
        events=[
            ("s1", t - 901_000, user_event("too old")),
            ("s1", t - 899_000, user_event("just inside")),
        ],
    )
    assert OpenClawContext(settings).recent_text(now_s, "s1") == "just inside"
