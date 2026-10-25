"""text/routes.py: the live chat route, its target, the forum-topic guard and user transcript text.

Every id here is fictional: chat -1001234567890, account acct-1, session ids s1..s4.
"""

from __future__ import annotations

import json

from concert_mcp.text.routes import ChatTarget, chat_target, forum_target, forum_topic, pick_live_route, user_texts

CHAT = "-1001234567890"
CUTOFF_MS = 1_000_000.0


def route(
    *, channel: str | None = "telegram", to: str | None = f"telegram:{CHAT}:topic:7", thread=7, account="acct-1"
) -> dict:
    r: dict = {"target": {"to": to} if to is not None else {}}
    if channel is not None:
        r["channel"] = channel
    if thread is not None:
        r["thread"] = {"id": thread}
    if account is not None:
        r["accountId"] = account
    return r


def entry(t: float | None, route_obj: dict | None) -> str:
    e: dict = {}
    if t is not None:
        e["lastInteractionAt"] = t
    if route_obj is not None:
        e["delivery"] = {"route": route_obj}
    return json.dumps(e)


# ---------- pick_live_route ----------


def test_pick_live_route_newest_with_route_inside_window() -> None:
    rows = [
        ("s1", entry(CUTOFF_MS + 100, route(to=f"telegram:{CHAT}:topic:3", thread=3))),
        ("s2", entry(CUTOFF_MS + 500, route())),
        ("s3", entry(CUTOFF_MS + 300, route(to=f"telegram:{CHAT}:topic:9", thread=9))),
    ]
    sid, r = pick_live_route(rows, CUTOFF_MS)
    assert sid == "s2"
    assert r == route()


def test_pick_live_route_ignores_old_no_route_and_garbage() -> None:
    rows = [
        ("old", entry(CUTOFF_MS - 1, route())),
        ("at_cutoff", entry(CUTOFF_MS, route())),
        ("no_route", entry(CUTOFF_MS + 900, None)),
        ("no_channel", entry(CUTOFF_MS + 900, {"target": {"to": CHAT}})),
        ("no_time", entry(None, route())),
        ("garbage", "{not json"),
        ("list", "[1, 2]"),
        ("live", entry(CUTOFF_MS + 10, route())),
    ]
    assert pick_live_route(rows, CUTOFF_MS) == ("live", route())


def test_pick_live_route_empty_and_all_stale() -> None:
    assert pick_live_route([], CUTOFF_MS) == (None, None)
    assert pick_live_route([("s1", entry(CUTOFF_MS - 5, route()))], CUTOFF_MS) == (None, None)
    assert pick_live_route([("s1", "garbage")], CUTOFF_MS) == (None, None)


# ---------- chat_target ----------


def test_chat_target_strips_prefix_and_topic() -> None:
    assert chat_target(route()) == ChatTarget(channel="telegram", chat=CHAT, thread="7", account="acct-1")


def test_chat_target_plain_chat_without_thread_or_account() -> None:
    assert chat_target(route(to=CHAT, thread=None, account=None)) == ChatTarget("telegram", CHAT, None, None)


def test_chat_target_thread_and_account_are_strings() -> None:
    t = chat_target(route(thread=42, account=12345))
    assert t is not None
    assert t.thread == "42" and isinstance(t.thread, str)
    assert t.account == "12345" and isinstance(t.account, str)


def test_chat_target_missing_chat_is_none() -> None:
    assert chat_target(route(to=None)) is None
    assert chat_target(route(to="")) is None
    assert chat_target(route(to="telegram:")) is None
    assert chat_target({}) is None


# ---------- forum_topic ----------


def test_forum_topic_happy_path() -> None:
    assert forum_topic(route(to=CHAT)) == (CHAT, "7")
    assert forum_topic(route(to=CHAT, thread="7")) == (CHAT, "7")


def test_forum_topic_refusals() -> None:
    assert forum_topic(route(to=CHAT, thread=1)) is None
    assert forum_topic(route(to=CHAT, thread="1")) is None
    assert forum_topic(route(to="123456789")) is None
    assert forum_topic(route(to=f"telegram:{CHAT}")) is None
    assert forum_topic(route(to=CHAT, channel="discord")) is None
    assert forum_topic(route(to=CHAT, channel=None)) is None
    assert forum_topic(route(to=CHAT, thread=None)) is None
    assert forum_topic({**route(to=CHAT), "thread": "7"}) is None
    assert forum_topic(None) is None
    assert forum_topic({}) is None


def test_forum_target_judges_the_stripped_chat_id() -> None:
    # The flows rename through the same bare chat id the send goes to, whatever form the store's `to` takes.
    assert forum_target(chat_target(route())) == (CHAT, "7")
    assert forum_target(chat_target(route(to=CHAT))) == (CHAT, "7")
    assert forum_target(chat_target(route(to=f"telegram:{CHAT}", thread=1))) is None
    assert forum_target(chat_target(route(to="telegram:12345", thread=None))) is None
    assert forum_target(chat_target(route(channel="discord"))) is None
    assert forum_target(None) is None


# ---------- user_texts ----------


def event(role: str, content) -> tuple[str]:
    return (json.dumps({"message": {"role": role, "content": content}}),)


def test_user_texts_str_and_list_content() -> None:
    rows = [
        event("user", "first message"),
        event("assistant", "ignored reply"),
        event(
            "user",
            [
                {"type": "text", "text": "second"},
                {"type": "image", "url": "x"},
                "junk",
                {"type": "text", "text": "third"},
            ],
        ),
        event("tool", [{"type": "text", "text": "ignored"}]),
        event("user", 42),
    ]
    assert user_texts(rows) == ["first message", "second", "third"]


def test_user_texts_skips_bad_rows() -> None:
    rows = [("{not json",), ("[]",), (json.dumps({"message": None}),), (json.dumps({}),), event("user", "kept")]
    assert user_texts(rows) == ["kept"]
    assert user_texts([]) == []
