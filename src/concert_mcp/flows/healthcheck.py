"""Which credentials and tools are present, and what fixes each missing one (unit flows-deliver-health).

One row per check in the homelab doctor's shape (PASS/FAIL/SKIP, name, evidence), a README anchor per FAIL. File
checks read the filesystem directly (they need mode bits); `--probe` makes one real call per provider through the
adapters in `svc`. Never raises: an exception inside a check is that check's FAIL.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import shutil
import sqlite3
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from concert_mcp.config import Settings
from concert_mcp.flows.services import Services
from concert_mcp.result import FixInput, Kind, Outcome, ProviderError
from concert_mcp.text.health import Check, HealthReport, render_health

CHECK_NAMES = (
    "python",
    "mcp-sdk",
    "config-dir",
    "state-dir",
    "gws-binary",
    "gws-keyfile",
    "gws-auth",
    "concerts-calendar",
    "spotify-credentials",
    "spotify-auth",
    "liked-cache",
    "setlistfm-key",
    "setlistfm-auth",
    "places-key",
    "gemini-key",
    "openclaw-binary",
    "openclaw-config",
    "openclaw-session-db",
    "setlist-queue",
)

SKIP_PROBE = "pass --probe"
SKIP_DELIVERY_OFF = "delivery off (CONCERT_MCP_SEND=0)"
SKIP_GWS_AUTH_FAILED = "gws-auth failed"
KEYFILE_FIX = "create the file keyring key: GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file gws auth login"
GWS_AUTH_FIX = "gws auth login"
# The artist one setlist.fm probe searches for; any well-documented act works, the count is only evidence of auth.
PROBE_ARTIST = "Radiohead"
EVIDENCE_MAX = 120


@dataclass
class _Ctx:
    settings: Settings
    probe: bool
    svc: Services | None
    results: dict[str, Check] = field(default_factory=dict)  # earlier rows of this run, by name

    def failed(self, name: str) -> bool:
        """Whether an earlier check of this run ended FAIL (False when it did not run)."""
        row = self.results.get(name)
        return row is not None and row.status == "FAIL"

    def services(self) -> Services:
        """The adapters for a probe, built on demand when the caller passed none."""
        if self.svc is None:
            from concert_mcp.flows.services import build_services

            self.svc = build_services(self.settings)
        return self.svc

    def now_s(self) -> float:
        return self.svc.clock.time() if self.svc is not None else time.time()

    def mtime(self, path: Path) -> float | None:
        if self.svc is not None:
            return self.svc.state.mtime(path)
        try:
            return path.stat().st_mtime
        except OSError:
            return None

    def read_json(self, path: Path, default: Any) -> Any:
        if self.svc is not None:
            return self.svc.state.read_json(path, default)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _short(e: BaseException) -> str:
    """The error text of a provider or input failure, cut to EVIDENCE_MAX."""
    text = e.error if isinstance(e, ProviderError | FixInput) else f"{type(e).__name__}: {e}"
    return str(text)[:EVIDENCE_MAX]


def _check_python(ctx: _Ctx) -> Check:
    v = sys.version_info
    if v >= (3, 11):
        return Check("python", "PASS", f"{v.major}.{v.minor}.{v.micro}")
    return Check("python", "FAIL", f"{v.major}.{v.minor}.{v.micro}", "getting-started", "needs Python >= 3.11")


def _check_mcp_sdk(ctx: _Ctx) -> Check:
    try:
        mod = importlib.import_module("mcp")
    except ImportError as e:
        evidence = f"not importable: {e}"[:EVIDENCE_MAX]
        return Check("mcp-sdk", "FAIL", evidence, "getting-started", "uv sync (needs mcp>=2,<3)")
    try:
        version = importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError:
        version = str(getattr(mod, "__version__", "unknown"))
    return Check("mcp-sdk", "PASS", f"mcp {version}")


def _writable_dir(name: str, path: Path) -> Check:
    """PASS when the directory is writable, or absent under a writable ancestor (the first write creates it).

    The healthcheck is read-only: it creates nothing, so `concert-mcp config` can still list the directory as MISSING.
    """
    if path.is_dir():
        if os.access(path, os.W_OK | os.X_OK):
            return Check(name, "PASS", str(path))
        return Check(name, "FAIL", f"{path}: not writable"[:EVIDENCE_MAX], "configuration", f"chmod u+rwx {path}")
    if path.exists():
        return Check(name, "FAIL", f"{path}: not a directory"[:EVIDENCE_MAX], "configuration", "move the file away")
    ancestor = path.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if ancestor.is_dir() and os.access(ancestor, os.W_OK | os.X_OK):
        return Check(name, "PASS", f"{path} absent (created on first write)")
    return Check(name, "FAIL", f"{path}: cannot create"[:EVIDENCE_MAX], "configuration", f"mkdir -p {path}")


def _check_config_dir(ctx: _Ctx) -> Check:
    return _writable_dir("config-dir", ctx.settings.config_dir)


def _check_state_dir(ctx: _Ctx) -> Check:
    return _writable_dir("state-dir", ctx.settings.state_dir)


def _binary(name: str, binary: str, anchor: str, fix: str, env_var: str) -> Check:
    """PASS with the resolved path; FAIL names the setting that points at a binary outside PATH and the common dirs."""
    found = shutil.which(binary)
    if found:
        return Check(name, "PASS", found)
    if Path(binary).exists():
        return Check(name, "PASS", binary)
    return Check(name, "FAIL", f"{binary} not found", anchor, f"{fix}, or set {env_var} to its path")


def _check_gws_binary(ctx: _Ctx) -> Check:
    return _binary("gws-binary", ctx.settings.gws_bin, "google-calendar", "install the gws CLI", "CONCERT_MCP_GWS_BIN")


def _check_gws_keyfile(ctx: _Ctx) -> Check:
    path = ctx.settings.gws_key_file
    if not path.exists():
        return Check("gws-keyfile", "FAIL", "missing", "google-calendar", KEYFILE_FIX)
    mode = _mode(path)
    if mode != 0o600:
        return Check("gws-keyfile", "FAIL", f"mode {oct(mode)}, want 600", "google-calendar", f"chmod 600 {path}")
    return Check("gws-keyfile", "PASS", "mode 600")


def _gws_fix(e: BaseException) -> str:
    """What fixes a failed gws call: the file keyring key when the keychain guard refused, else a login.

    A plain `gws auth login` is the one thing that does not help a locked keychain (gws would delete its credentials
    in that session), so the keychain case names the key file instead.
    """
    if isinstance(e, ProviderError) and e.provider == "gws-keychain":
        return KEYFILE_FIX
    return GWS_AUTH_FIX


def _check_gws_auth(ctx: _Ctx) -> Check:
    if not ctx.probe:
        return Check("gws-auth", "SKIP", SKIP_PROBE)
    try:
        calendars = ctx.services().calendar.list_calendars()
    except Exception as e:  # noqa: BLE001 - the failure is the evidence
        return Check("gws-auth", "FAIL", _short(e), "google-calendar", _gws_fix(e))
    return Check("gws-auth", "PASS", f"{len(calendars)} calendars")


def _check_concerts_calendar(ctx: _Ctx) -> Check:
    if not ctx.probe:
        return Check("concerts-calendar", "SKIP", SKIP_PROBE)
    if ctx.failed("gws-auth"):
        # The same gws call just failed one row up; repeating its evidence would count the one fault twice.
        return Check("concerts-calendar", "SKIP", SKIP_GWS_AUTH_FAILED)
    from concert_mcp.flows.concert_add import resolve_calendar

    wanted = ctx.settings.concerts_calendar
    try:
        _cal_id, name = resolve_calendar(wanted, ctx.services(), dry_run=False)
    except FixInput as e:
        return Check("concerts-calendar", "FAIL", _short(e), "google-calendar", e.fix)
    except Exception as e:  # noqa: BLE001
        return Check("concerts-calendar", "FAIL", _short(e), "google-calendar", _gws_fix(e))
    # The calendar id is personal data: only the name is evidence.
    return Check("concerts-calendar", "PASS", f"found {name}")


def _credential_file(name: str, path: Path, keys: Sequence[str], *, legacy: bool, anchor: str, auth_cmd: str) -> Check:
    if not path.exists():
        return Check(name, "FAIL", "missing", anchor, auth_cmd)
    mode = _mode(path)
    if mode != 0o600:
        return Check(name, "FAIL", f"mode {oct(mode)}, want 600", anchor, f"chmod 600 {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Check(name, "FAIL", "unreadable", anchor, auth_cmd)
    if not isinstance(data, dict) or any(not data.get(k) for k in keys):
        return Check(name, "FAIL", "incomplete", anchor, auth_cmd)
    evidence = "present, mode 600"
    if legacy:
        evidence += ", LEGACY path: run concert-mcp migrate"
    return Check(name, "PASS", evidence)


def _check_spotify_credentials(ctx: _Ctx) -> Check:
    return _credential_file(
        "spotify-credentials",
        ctx.settings.spotify_credentials,
        ("client_id", "client_secret", "refresh_token"),
        legacy="spotify_credentials" in ctx.settings.legacy_in_use,
        anchor="spotify",
        auth_cmd="concert-mcp spotify auth",
    )


def _check_spotify_auth(ctx: _Ctx) -> Check:
    if not ctx.probe:
        return Check("spotify-auth", "SKIP", SKIP_PROBE)
    api = ctx.services().spotify
    if api is None:
        return Check("spotify-auth", "SKIP", "not configured")
    try:
        me = api.me()
    except Exception as e:  # noqa: BLE001
        return Check("spotify-auth", "FAIL", _short(e), "spotify", "concert-mcp spotify auth --force")
    who = me.get("display_name") or me.get("id") or "signed in"
    return Check("spotify-auth", "PASS", str(who))


def _check_liked_cache(ctx: _Ctx) -> Check:
    mtime = ctx.mtime(ctx.settings.liked_cache)
    if mtime is None:
        return Check("liked-cache", "SKIP", "absent: the first teaser will be slow (~40 s)")
    age_h = max(0.0, ctx.now_s() - mtime) / 3600
    return Check("liked-cache", "PASS", f"{age_h:.1f} h old")


def _check_setlistfm_key(ctx: _Ctx) -> Check:
    return _credential_file(
        "setlistfm-key",
        ctx.settings.setlistfm_credentials,
        ("api_key",),
        legacy="setlistfm_credentials" in ctx.settings.legacy_in_use,
        anchor="setlistfm",
        auth_cmd="concert-mcp spotify setlist-auth",
    )


def _check_setlistfm_auth(ctx: _Ctx) -> Check:
    if not ctx.probe:
        return Check("setlistfm-auth", "SKIP", SKIP_PROBE)
    api = ctx.services().setlistfm
    if api is None:
        return Check("setlistfm-auth", "SKIP", "not configured")
    try:
        res = api.search_setlists(PROBE_ARTIST, page=1)
    except Exception as e:  # noqa: BLE001
        return Check("setlistfm-auth", "FAIL", _short(e), "setlistfm", "concert-mcp spotify setlist-auth --force")
    total = (res or {}).get("total", 0)
    return Check("setlistfm-auth", "PASS", f"{total} setlists for the probe artist")


def _check_places_key(ctx: _Ctx) -> Check:
    """Optional: SKIP when no key is stored (Nominatim answers venue lookups), the credential-file rules otherwise."""
    path = ctx.settings.places_credentials
    if not path.exists():
        return Check("places-key", "SKIP", "optional: concert-mcp venues auth stores a Google Places key")
    return _credential_file(
        "places-key", path, ("api_key",), legacy=False, anchor="configuration", auth_cmd="concert-mcp venues auth"
    )


def _check_gemini_key(ctx: _Ctx) -> Check:
    """Optional: SKIP when no key is stored (the lineup step is skipped), the credential-file rules otherwise."""
    path = ctx.settings.gemini_credentials
    if not path.exists():
        return Check("gemini-key", "SKIP", "optional: concert-mcp gemini auth stores a Gemini key (openers, set times)")
    return _credential_file(
        "gemini-key", path, ("api_key",), legacy=False, anchor="configuration", auth_cmd="concert-mcp gemini auth"
    )


def _check_openclaw_binary(ctx: _Ctx) -> Check:
    if not ctx.settings.send:
        return Check("openclaw-binary", "SKIP", SKIP_DELIVERY_OFF)
    return _binary(
        "openclaw-binary", ctx.settings.openclaw_bin, "openclaw", "install openclaw", "CONCERT_MCP_OPENCLAW_BIN"
    )


def _check_openclaw_config(ctx: _Ctx) -> Check:
    if not ctx.settings.send:
        return Check("openclaw-config", "SKIP", SKIP_DELIVERY_OFF)
    path = ctx.settings.openclaw_config
    if not path.exists():
        return Check("openclaw-config", "SKIP", "absent")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Check("openclaw-config", "SKIP", "unreadable: topics are not renamed")
    telegram = ((data.get("channels") or {}).get("telegram") or {}) if isinstance(data, dict) else {}
    # The token itself is never evidence, never logged.
    if telegram.get("botToken"):
        return Check("openclaw-config", "PASS", "telegram bot token present (topic rename possible)")
    return Check("openclaw-config", "SKIP", "no bot token: topics are not renamed")


def _check_openclaw_session_db(ctx: _Ctx) -> Check:
    if not ctx.settings.send:
        return Check("openclaw-session-db", "SKIP", SKIP_DELIVERY_OFF)
    path = ctx.settings.session_db
    absent = Check("openclaw-session-db", "SKIP", "absent or unreadable: replies are not delivered")
    if not path.exists():
        return absent
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            (n,) = con.execute("select count(*) from session_nodes").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return absent
    return Check("openclaw-session-db", "PASS", f"{n} sessions")


def _check_setlist_queue(ctx: _Ctx) -> Check:
    rows = ctx.read_json(ctx.settings.setlist_queue, [])
    n = len(rows) if isinstance(rows, list) else 0
    return Check("setlist-queue", "PASS", f"{n} shows queued")


# name -> (check, the README anchor its wrapped failure points at)
CHECKS: dict[str, tuple[Callable[[_Ctx], Check], str]] = {
    "python": (_check_python, "getting-started"),
    "mcp-sdk": (_check_mcp_sdk, "getting-started"),
    "config-dir": (_check_config_dir, "configuration"),
    "state-dir": (_check_state_dir, "configuration"),
    "gws-binary": (_check_gws_binary, "google-calendar"),
    "gws-keyfile": (_check_gws_keyfile, "google-calendar"),
    "gws-auth": (_check_gws_auth, "google-calendar"),
    "concerts-calendar": (_check_concerts_calendar, "google-calendar"),
    "spotify-credentials": (_check_spotify_credentials, "spotify"),
    "spotify-auth": (_check_spotify_auth, "spotify"),
    "liked-cache": (_check_liked_cache, "spotify"),
    "setlistfm-key": (_check_setlistfm_key, "setlistfm"),
    "setlistfm-auth": (_check_setlistfm_auth, "setlistfm"),
    "places-key": (_check_places_key, "configuration"),
    "gemini-key": (_check_gemini_key, "configuration"),
    "openclaw-binary": (_check_openclaw_binary, "openclaw"),
    "openclaw-config": (_check_openclaw_config, "openclaw"),
    "openclaw-session-db": (_check_openclaw_session_db, "openclaw"),
    "setlist-queue": (_check_setlist_queue, "configuration"),
}
assert tuple(CHECKS) == CHECK_NAMES


def _run_one(name: str, ctx: _Ctx) -> Check:
    fn, anchor = CHECKS[name]
    try:
        row = fn(ctx)
    except Exception as e:  # noqa: BLE001 - a crashing check is a failing check, never a crashing table
        row = Check(name, "FAIL", f"{type(e).__name__}: {e}"[:EVIDENCE_MAX], anchor, "see the gateway log")
    ctx.results[name] = row
    return row


def run_healthcheck(
    settings: Settings,
    *,
    probe: bool = False,
    svc: Services | None = None,
    only: Sequence[str] = (),
) -> HealthReport:
    """Every check in CHECK_NAMES (or `only`), in CHECK_NAMES order; never raises."""
    ctx = _Ctx(settings=settings, probe=probe, svc=svc)
    wanted = set(only) if only else set(CHECK_NAMES)
    return HealthReport(tuple(_run_one(name, ctx) for name in CHECK_NAMES if name in wanted))


def healthcheck_outcome(settings: Settings, *, probe: bool, svc: Services | None) -> Outcome:
    """The rendered table as the reply; ERROR when anything fails."""
    report = run_healthcheck(settings, probe=probe, svc=svc)
    return Outcome(Kind.OK if report.ok else Kind.ERROR, reply=render_health(report), data=report.to_json())
