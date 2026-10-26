"""Google Calendar through the gws CLI (unit adapter-gws).

Ported from cal-add.py's gws(), gws_env() and keychain_reachable() and cal-del.py's delete_event(). Every argv is the
legacy one; the only change is that failures raise ProviderError instead of printing a record and exiting.
"""

from __future__ import annotations

import json as _json
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from concert_mcp.config import Settings
from concert_mcp.log import kv, log_debug
from concert_mcp.proc import Runner, run
from concert_mcp.result import ProviderError
from concert_mcp.text.reply import provider_reply

KEYRING_ENV = "GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND"
DELETE_OUT = "gws-del.out"


class GwsCalendar:
    """Calendar implemented by shelling out to gws, exactly the legacy argv."""

    def __init__(
        self,
        settings: Settings,
        runner: Runner = run,
        *,
        platform: str = sys.platform,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.settings = settings
        self.runner = runner
        self.platform = platform
        self.environ: dict[str, str] = dict(os.environ if environ is None else environ)

    def env(self) -> dict[str, str]:
        """The environment for gws: the file keyring backend when the key file exists.

        The file backend needs no keychain, so plain ssh and the gateway both work and nothing can trip gws's
        credential deletion.
        """
        env = dict(self.environ)
        if self.settings.gws_key_file.exists():
            env[KEYRING_ENV] = "file"
        return env

    def keychain_reachable(self) -> bool:
        """On macOS, whether the login keychain answers; True elsewhere.

        gws keeps its encryption key in the login keychain and DELETES credentials.enc when decryption fails, which
        is what happens in a session without keychain access (plain ssh).
        """
        if self.platform != "darwin":
            return True
        keychain = Path.home() / "Library/Keychains/login.keychain-db"
        return self.runner(["security", "show-keychain-info", str(keychain)], 10).ok

    def raw(self, *args: str, cwd: str | None = None) -> str:
        """Run gws with the given args and return stdout; ProviderError on any failure.

        stdout only: gws prints keyring chatter on stderr, which would corrupt the JSON.
        """
        env = self.env()
        op = self.settings.operator_name
        if env.get(KEYRING_ENV) != "file" and not self.keychain_reachable():
            raise ProviderError(
                "gws-keychain",
                "login keychain not reachable from this session; gws would delete its credentials",
                provider_reply("gws-keychain", op),
            )
        argv = [self.settings.gws_bin, *args]
        log_debug("gws " + kv(argv=argv, cwd=cwd))
        r = self.runner(argv, self.settings.gws_timeout_s, env=env, cwd=cwd)
        if r.error:
            raise ProviderError("gws-missing", f"gws did not run: {r.error}", provider_reply("gws-missing", op))
        if r.returncode != 0:
            what = "delete" if list(args[1:3]) == ["events", "delete"] else "request"
            raise ProviderError(
                "gws",
                f"gws {' '.join(args[:2])} exited {r.returncode}: {r.tail}",
                provider_reply("gws", op, what=what),
            )
        return r.stdout

    def json(self, *args: str) -> Any:
        """raw() parsed as JSON; ProviderError('gws-nojson') when it is not."""
        out = self.raw(*args)
        try:
            return _json.loads(out)
        except ValueError:
            op = self.settings.operator_name
            raise ProviderError("gws-nojson", "gws returned no JSON", provider_reply("gws-nojson", op)) from None

    def _items(self, answer: Any) -> list[dict[str, Any]]:
        if not isinstance(answer, Mapping):
            return []
        items = answer.get("items", [])
        return list(items) if isinstance(items, list) else []

    def list_calendars(self) -> list[dict[str, Any]]:
        """gws calendar calendarList list."""
        return self._items(self.json("calendar", "calendarList", "list"))

    def list_events(
        self,
        calendar_id: str,
        *,
        time_min: datetime,
        time_max: datetime,
        q: str = "",
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """gws calendar events list."""
        params = {
            "calendarId": calendar_id,
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "singleEvents": True,
            "maxResults": max_results,
            "q": q,
        }
        return self._items(self.json("calendar", "events", "list", "--params", _json.dumps(params)))

    def get_event(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        """gws calendar events get."""
        params = {"calendarId": calendar_id, "eventId": event_id}
        return self.json("calendar", "events", "get", "--params", _json.dumps(params))

    def insert_event(
        self,
        calendar_id: str,
        *,
        summary: str,
        start: datetime,
        end: datetime,
        location: str,
        description: str | None,
        attendees: Sequence[str],
    ) -> dict[str, Any]:
        """gws calendar +insert."""
        cmd = [
            "calendar",
            "+insert",
            "--calendar",
            calendar_id,
            "--summary",
            summary,
            "--start",
            start.isoformat(),
            "--end",
            end.isoformat(),
            "--location",
            location,
        ]
        if description:
            cmd += ["--description", description]
        for a in attendees:
            cmd += ["--attendee", str(a)]
        return self.json(*cmd)

    def patch_event(
        self,
        calendar_id: str,
        event_id: str,
        *,
        summary: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        location: str | None = None,
        description: str | None = None,
    ) -> None:
        """gws calendar events patch --json with the given fields only (a None is left as it is)."""
        params = {"calendarId": calendar_id, "eventId": event_id}
        body: dict[str, Any] = {}
        if summary is not None:
            body["summary"] = summary
        if start is not None:
            body["start"] = {"dateTime": start.isoformat()}
        if end is not None:
            body["end"] = {"dateTime": end.isoformat()}
        if location is not None:
            body["location"] = location
        if description is not None:
            body["description"] = description
        if not body:
            return
        self.raw("calendar", "events", "patch", "--params", _json.dumps(params), "--json", _json.dumps(body))

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        """gws calendar events delete, run from a temp cwd because it writes an output file.

        gws events delete writes its empty response to a file in cwd (legacy ran from /tmp); a fresh temp dir keeps
        concurrent deletes apart and is removed afterwards along with the file.
        """
        params = {"calendarId": calendar_id, "eventId": event_id}
        d = tempfile.mkdtemp(prefix="concert-mcp-gws-")
        try:
            self.raw("calendar", "events", "delete", "-o", DELETE_OUT, "--params", _json.dumps(params), cwd=d)
        finally:
            Path(d, DELETE_OUT).unlink(missing_ok=True)
            shutil.rmtree(d, ignore_errors=True)
