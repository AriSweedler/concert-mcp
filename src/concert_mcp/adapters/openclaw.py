"""OpenClaw: reply delivery, Telegram topic rename, live chat and transcript context (unit adapter-openclaw).

Ported from cal-add.py's send_reply(), rename_topic(), current_chat() and recent_context(). Every read of OpenClaw's
host footprint (the session sqlite, the OCR file, openclaw.json) is best effort: a failure degrades to "no live chat"
and empty context, never an exception. The bot token is read for the rename only and never appears in a log line.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from concert_mcp.adapters.protocols import Clock
from concert_mcp.config import Settings
from concert_mcp.log import kv, log_debug
from concert_mcp.proc import Runner, run
from concert_mcp.text.routes import ChatTarget, pick_live_route, user_texts

SEND_TRIES = 3
SEND_RETRY_SLEEP_S = 4
# One `openclaw message send` is a single Bot API call; 20 s is generous, and three tries stay under 70 s.
SEND_TIMEOUT_S = 20
TELEGRAM_TIMEOUT_S = 20
TOPIC_NAME_MAX = 128
TRANSCRIPT_LIMIT = 40


class OpenClawChat:
    """Chat via `openclaw message send` and the Telegram Bot API for topic renames."""

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        runner: Runner = run,
        *,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._runner = runner
        self._urlopen = urlopen

    def send(self, target: ChatTarget, text: str) -> str:
        """Three tries of `openclaw message send`; returns the SENT / NOT SENT status; never raises."""
        cmd = [
            self._settings.openclaw_bin,
            "message",
            "send",
            "--channel",
            target.channel,
            "--target",
            target.chat,
            "-m",
            text,
        ]
        if target.account:
            cmd += ["--account", str(target.account)]
        if target.thread is not None:
            cmd += ["--thread-id", str(target.thread)]
        err = ""
        for attempt in range(SEND_TRIES):
            r = self._runner(cmd, SEND_TIMEOUT_S)
            if r.error:
                return f"NOT SENT: openclaw message send did not run: {r.error}"
            if r.ok:
                break
            err = r.tail[-200:]
            log_debug("openclaw message send failed " + kv(attempt=attempt + 1, returncode=r.returncode))
            if attempt + 1 < SEND_TRIES:
                # Telegram drops a request now and then; a short pause before the next try, none after the last.
                self._clock.sleep(SEND_RETRY_SLEEP_S)
        else:
            return f"NOT SENT: openclaw message send failed {SEND_TRIES} times: {err}"
        topic = f" topic {target.thread}" if target.thread is not None else ""
        return f"SENT to {target.channel} {target.chat}{topic}"

    def bot_token(self) -> tuple[str | None, str | None]:
        """(token, None) from channels.telegram.botToken in the OpenClaw config, or (None, <exception type name>).

        The token is never logged.
        """
        try:
            raw = self._settings.openclaw_config.read_text(encoding="utf-8")
            token = json.loads(raw)["channels"]["telegram"]["botToken"]
        except Exception as e:
            return None, type(e).__name__
        return token, None

    def rename_topic(self, chat_id: str, thread_id: str, name: str) -> str:
        """Bot API editForumTopic; returns 'topic renamed to X' or 'topic not renamed: ...'; never raises."""
        token, why = self.bot_token()
        if token is None:
            return f"topic not renamed: no bot token ({why})"
        short = name[:TOPIC_NAME_MAX]
        data = urllib.parse.urlencode({"chat_id": chat_id, "message_thread_id": thread_id, "name": short}).encode()
        # The URL carries the token: it is built here and handed to urlopen, never logged.
        url = f"https://api.telegram.org/bot{token}/editForumTopic"
        try:
            with self._urlopen(url, data=data, timeout=TELEGRAM_TIMEOUT_S) as r:
                ok = json.loads(r.read().decode()).get("ok")
            return f"topic renamed to {short}" if ok else "topic not renamed: Telegram said no"
        except urllib.error.HTTPError as e:
            return f"topic not renamed: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:120]}"
        except Exception as e:
            return f"topic not renamed: {type(e).__name__}"


class OpenClawContext:
    """LiveContext read from OpenClaw's session store and the last OCR output; best effort, read-only."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self._settings.session_db}?mode=ro", uri=True, timeout=2)

    def live_route(self, now_s: float) -> tuple[str | None, dict[str, Any] | None]:
        """(session_id, route) of the chat that spoke most recently; (None, None) on any error."""
        try:
            con = self._connect()
            try:
                rows = con.execute("select current_session_id, entry_json from session_nodes").fetchall()
            finally:
                con.close()
        except Exception:
            return None, None
        cutoff_ms = (now_s - self._settings.context_window_s) * 1000
        return pick_live_route(rows, cutoff_ms)

    def recent_text(self, now_s: float, session_id: str | None) -> str:
        """Fresh OCR text plus the user's recent messages (the given session's only, when one is given)."""
        parts: list[str] = []
        cutoff = now_s - self._settings.context_window_s
        try:
            if self._settings.ocr_last.stat().st_mtime > cutoff:
                parts.append(self._settings.ocr_last.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
        try:
            con = self._connect()
            try:
                q = "select event_json from transcript_events where created_at > ? and event_json is not null"
                args: list[Any] = [int(cutoff * 1000)]
                if session_id:
                    q += " and session_id = ?"
                    args.append(session_id)
                rows = con.execute(q + f" order by created_at desc limit {TRANSCRIPT_LIMIT}", args).fetchall()
            finally:
                con.close()
            parts.extend(user_texts(rows))
        except Exception:
            pass
        return "\n".join(p for p in parts if p)


class NullContext:
    """LiveContext for runs with no OpenClaw at all: no route, no text."""

    def live_route(self, now_s: float) -> tuple[str | None, dict[str, Any] | None]:
        """Always (None, None)."""
        return None, None

    def recent_text(self, now_s: float, session_id: str | None) -> str:
        """Always ''."""
        return ""
