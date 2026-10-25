"""OpenClaw session routes and transcript text, parsed from rows (unit text-music). Pure."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChatTarget:
    channel: str
    chat: str
    thread: str | None
    account: str | None


def pick_live_route(rows: Iterable[tuple[str, str]], cutoff_ms: float) -> tuple[str | None, dict[str, Any] | None]:
    """(session_id, route) of the newest session with a delivery route after the cutoff; (None, None) otherwise."""
    best: tuple[str, dict[str, Any]] | None = None
    best_t = cutoff_ms
    for sid, raw in rows:
        try:
            e = json.loads(raw)
        except Exception:
            continue
        if not isinstance(e, Mapping):
            continue
        route = (e.get("delivery") or {}).get("route") or {}
        t = e.get("lastInteractionAt") or 0
        if route.get("channel") and t > best_t:
            best, best_t = (sid, dict(route)), t
    return best or (None, None)


def chat_target(route: Mapping[str, Any]) -> ChatTarget | None:
    """The chat/thread/account a route delivers to; None when the route has no chat."""
    to = str((route.get("target") or {}).get("to") or "")
    chat = re.sub(r"^telegram:", "", to).split(":topic:")[0]
    if not chat:
        return None
    thread = (route.get("thread") or {}).get("id")
    account = route.get("accountId")
    return ChatTarget(
        channel=str(route.get("channel") or ""),
        chat=chat,
        thread=str(thread) if thread is not None else None,
        account=str(account) if account else None,
    )


def _is_forum_topic(channel: object, chat: str, thread: str) -> bool:
    """Telegram group (-100…) with a thread other than General (1): the only place a topic can be renamed."""
    return channel == "telegram" and bool(thread) and thread != "1" and chat.startswith("-100")


def forum_topic(route: Mapping[str, Any] | None) -> tuple[str, str] | None:
    """(chat, thread) when the raw route is a Telegram forum topic; None otherwise (the legacy rename_topic guard)."""
    route = route or {}
    thread_obj = route.get("thread")
    thread = str(thread_obj.get("id") or "") if isinstance(thread_obj, Mapping) else ""
    chat = str((route.get("target") or {}).get("to") or "")
    return (chat, thread) if _is_forum_topic(route.get("channel"), chat, thread) else None


def forum_target(target: ChatTarget | None) -> tuple[str, str] | None:
    """forum_topic on a normalized ChatTarget: the route's `to` may carry the `telegram:` prefix and `:topic:n` suffix
    that chat_target strips, so the flows judge the bare chat id, the same one the send goes to."""
    if target is None:
        return None
    thread = target.thread or ""
    return (target.chat, thread) if _is_forum_topic(target.channel, target.chat, thread) else None


def user_texts(event_rows: Iterable[tuple[str]]) -> list[str]:
    """The user messages' text from transcript event rows."""
    texts: list[str] = []
    for row in event_rows:
        try:
            (raw,) = row
            m = json.loads(raw).get("message") or {}
            if m.get("role") != "user":
                continue
            c = m.get("content")
            if isinstance(c, str):
                texts.append(c)
            elif isinstance(c, list):
                texts.extend(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
        except Exception:
            continue
    return texts
