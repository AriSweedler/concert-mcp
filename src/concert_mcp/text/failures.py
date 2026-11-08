"""Screenshot turns the bot failed, found in OpenClaw's transcript (unit text-failures).

Pure. A screenshot turn is a user message whose text OpenClaw built as `[Image] / User text: / Description:` and
whose `__openclaw.media` names the image. It failed when the model never answered it: the next assistant message in
the session stopped with `error` (the Orin unreachable: "Connection error."), or none came within the grace period.
OpenClaw re-queues an unanswered message as text items ("[Earlier unanswered user message...]"); those are retries
of the same turn, not new ones.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

ANSWERED = frozenset({"toolUse", "stop"})
_REQUEST_RE = re.compile(r"^\[Image\]\nUser text:\n(?P<caption>.*?)\nDescription:\n(?P<ocr>.*)$", re.DOTALL)


@dataclass(frozen=True)
class Failure:
    event_id: str
    session_id: str
    at_ms: int
    request: str  # the user turn exactly as OpenClaw built it
    caption: str
    ocr: str
    image: str  # the staged copy OpenClaw gave the model
    chat: str  # the conversation and topic, for the record
    error: str  # what the model call said, or "no reply"

    def slug(self) -> str:
        """The folder name: sortable time, then the event id's head."""
        when = datetime.fromtimestamp(self.at_ms / 1000, tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"{when}-{self.event_id[:8]}"


def _screenshot(msg: Mapping[str, Any]) -> tuple[str, str, str, str] | None:
    """(request, caption, ocr, image) for an original screenshot turn; None for anything else."""
    content = msg.get("content")
    if msg.get("role") != "user" or not isinstance(content, str):
        return None
    m = _REQUEST_RE.match(content)
    media = [x for x in msg.get("__openclaw", {}).get("media", []) if x.get("kind") == "image" and x.get("path")]
    if not m or not media:
        return None
    return content, m.group("caption"), m.group("ocr"), str(media[0]["path"])


def _chat(msg: Mapping[str, Any]) -> str:
    t = msg.get("__openclaw", {}).get("transport", {})
    return ":".join(str(t.get(k, "")) for k in ("channel", "conversationRef", "threadId", "messageId"))


def find_failures(events: Iterable[tuple[str, int, Mapping[str, Any]]], *, now_ms: int, grace_ms: int) -> list[Failure]:
    """Failed screenshot turns among (session_id, created_at_ms, event) rows in time order."""
    out: list[Failure] = []
    open_turns: dict[str, tuple[str, int, Mapping[str, Any], tuple[str, str, str, str]]] = {}

    def close(session: str, error: str) -> None:
        event_id, at, msg, (request, caption, ocr, image) = open_turns.pop(session)
        out.append(Failure(event_id, session, at, request, caption, ocr, image, _chat(msg), error))

    for session, at, ev in events:
        msg = ev.get("message") or {}
        shot = _screenshot(msg)
        if shot:
            if session in open_turns:
                close(session, "no reply before the next screenshot")
            open_turns[session] = (str(ev.get("id", "")), at, msg, shot)
            continue
        if session not in open_turns or msg.get("role") != "assistant":
            continue
        stop = msg.get("stopReason")
        if stop in ANSWERED:
            open_turns.pop(session)
        elif stop == "error":
            close(session, str(msg.get("errorMessage") or "model error"))
    for session, (_, at, _, _) in list(open_turns.items()):
        if now_ms - at > grace_ms:
            close(session, "no reply")
    return sorted(out, key=lambda f: f.at_ms)
