"""flows/healthcheck.py: the per-credential table on a bare tmp home, with files, with probes, and its outcome."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from concert_mcp.config import Settings, load_settings
from concert_mcp.flows import healthcheck as mod
from concert_mcp.flows.healthcheck import CHECK_NAMES, run_healthcheck
from concert_mcp.text.health import Check, HealthReport, render_health
from conftest import make_services, settings_env

ROW_RE = re.compile(r"^(PASS|FAIL|SKIP)  \S+\s+.+$")
TOKEN = "123456789:FAKE-bot-token-for-tests"


def bare_env(tmp_path: Path, *, send: str = "1") -> dict[str, str]:
    """conftest's env plus binaries that cannot exist, so the binary rows do not depend on the machine."""
    return {
        **settings_env(tmp_path, send=send),
        "CONCERT_MCP_GWS_BIN": str(tmp_path / "nowhere" / "gws"),
        "CONCERT_MCP_OPENCLAW_BIN": str(tmp_path / "nowhere" / "openclaw"),
    }


@pytest.fixture
def bare(tmp_path: Path) -> Settings:
    return load_settings(bare_env(tmp_path), home=tmp_path)


def by_name(report: HealthReport) -> dict[str, Check]:
    return {c.name: c for c in report.checks}


def _write(path: Path, text: str, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


SPOTIFY_JSON = json.dumps({"client_id": "a" * 32, "client_secret": "b" * 32, "refresh_token": "r" * 40})


# ---------- the bare table ----------


def test_send_false_skips_the_openclaw_rows(tmp_path: Path) -> None:
    settings = load_settings(bare_env(tmp_path, send="0"), home=tmp_path)
    rows = by_name(run_healthcheck(settings))
    for name in ("openclaw-binary", "openclaw-config", "openclaw-silent-reply", "openclaw-session-db"):
        assert rows[name] == Check(name, "SKIP", "delivery off (CONCERT_MCP_SEND=0)")


# ---------- files ----------


def test_gws_keyfile_mode_644_fails_with_the_mode_and_a_chmod(bare: Settings) -> None:
    _write(bare.gws_key_file, "key", mode=0o644)
    row = by_name(run_healthcheck(bare, only=["gws-keyfile"]))["gws-keyfile"]
    assert row == Check(
        "gws-keyfile", "FAIL", "mode 0o644, want 600", "google-calendar", f"chmod 600 {bare.gws_key_file}"
    )


def test_gws_keyfile_mode_600_passes(bare: Settings) -> None:
    _write(bare.gws_key_file, "key", mode=0o600)
    assert by_name(run_healthcheck(bare))["gws-keyfile"] == Check("gws-keyfile", "PASS", "mode 600")


def test_spotify_credentials_bad_mode_and_incomplete(bare: Settings) -> None:
    _write(bare.spotify_credentials, SPOTIFY_JSON, mode=0o644)
    row = by_name(run_healthcheck(bare))["spotify-credentials"]
    assert row.status == "FAIL"
    assert row.evidence == "mode 0o644, want 600"
    assert row.fix == f"chmod 600 {bare.spotify_credentials}"
    _write(bare.spotify_credentials, json.dumps({"client_id": "a" * 32}), mode=0o600)
    row = by_name(run_healthcheck(bare))["spotify-credentials"]
    assert row == Check("spotify-credentials", "FAIL", "incomplete", "spotify", "concert-mcp spotify auth")
    _write(bare.spotify_credentials, "{not json", mode=0o600)
    row = by_name(run_healthcheck(bare))["spotify-credentials"]
    assert row == Check("spotify-credentials", "FAIL", "unreadable", "spotify", "concert-mcp spotify auth")


def test_setlistfm_legacy_hint(tmp_path: Path) -> None:
    _write(tmp_path / ".config" / "ari-spotify" / "setlistfm.json", json.dumps({"api_key": "k" * 32}))
    settings = load_settings(bare_env(tmp_path), home=tmp_path)
    row = by_name(run_healthcheck(settings))["setlistfm-key"]
    assert row == Check("setlistfm-key", "PASS", "present, mode 600, LEGACY path: run concert-mcp migrate")


def test_liked_cache_age_from_the_filesystem(bare: Settings) -> None:
    _write(bare.liked_cache, "[]")
    row = by_name(run_healthcheck(bare))["liked-cache"]
    assert row.status == "PASS"
    assert re.fullmatch(r"\d+\.\d h old", row.evidence), row.evidence


# ---------- openclaw ----------


def test_openclaw_config_with_a_token_passes_and_never_shows_it(bare: Settings) -> None:
    _write(bare.openclaw_config, json.dumps({"channels": {"telegram": {"botToken": TOKEN}}}), mode=0o600)
    report = run_healthcheck(bare)
    row = by_name(report)["openclaw-config"]
    assert row == Check("openclaw-config", "PASS", "telegram bot token present (topic rename possible)")
    text = render_health(report)
    assert TOKEN not in text
    assert TOKEN not in json.dumps(report.to_json())
    assert "123456789" not in text


def test_openclaw_config_without_a_token_skips(bare: Settings) -> None:
    _write(bare.openclaw_config, json.dumps({"channels": {"telegram": {}}}), mode=0o600)
    row = by_name(run_healthcheck(bare))["openclaw-config"]
    assert row == Check("openclaw-config", "SKIP", "no bot token: topics are not renamed")
    _write(bare.openclaw_config, "{broken", mode=0o600)
    row = by_name(run_healthcheck(bare))["openclaw-config"]
    assert row.status == "SKIP"


def test_openclaw_session_db_counts_sessions(bare: Settings) -> None:
    bare.session_db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(bare.session_db)
    con.execute("create table session_nodes (current_session_id text, entry_json text)")
    con.executemany("insert into session_nodes values (?, ?)", [("s1", "{}"), ("s2", "{}"), ("s3", "{}")])
    con.commit()
    con.close()
    row = by_name(run_healthcheck(bare))["openclaw-session-db"]
    assert row == Check("openclaw-session-db", "PASS", "3 sessions")


def test_openclaw_session_db_without_the_table_skips(bare: Settings) -> None:
    bare.session_db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(bare.session_db)
    con.execute("create table other (x)")
    con.commit()
    con.close()
    row = by_name(run_healthcheck(bare))["openclaw-session-db"]
    assert row == Check("openclaw-session-db", "SKIP", "absent or unreadable: replies are not delivered")


# ---------- probes on the fakes ----------


def test_locked_keychain_points_at_the_key_file_not_at_a_login(settings: Settings, now: datetime) -> None:
    """`gws auth login` is the one command that does not fix a locked keychain (gws would delete its credentials)."""
    from concert_mcp.result import ProviderError
    from fakes import FakeCalendar

    class LockedCalendar(FakeCalendar):
        def list_calendars(self):
            raise ProviderError("gws-keychain", "login keychain not reachable from this session", "locked")

    svc = make_services(settings, now, route=None, text="")
    svc.calendar = LockedCalendar(calendars=[])
    rows = by_name(run_healthcheck(settings, probe=True, svc=svc))
    assert rows["gws-auth"].status == "FAIL"
    assert rows["gws-auth"].fix == mod.KEYFILE_FIX
    assert rows["concerts-calendar"] == Check("concerts-calendar", "SKIP", "gws-auth failed")
    # `--only concerts-calendar` runs without the gws-auth row: it checks on its own and maps the fix the same way.
    rows = by_name(run_healthcheck(settings, probe=True, svc=svc, only=["concerts-calendar"]))
    assert rows["concerts-calendar"].status == "FAIL"
    assert rows["concerts-calendar"].fix == mod.KEYFILE_FIX


def test_probe_failures_are_fail_rows_with_short_evidence(settings: Settings, now: datetime) -> None:
    from fakes import FakeCalendar, FakeSetlistFm, FakeSpotify

    svc = make_services(settings, now, route=None, text="")
    svc.calendar = FakeCalendar(calendars=[], fail="list_calendars")
    svc.spotify = FakeSpotify(fail="me")
    svc.setlistfm = FakeSetlistFm(fail=True)
    rows = by_name(run_healthcheck(settings, probe=True, svc=svc))
    assert rows["gws-auth"].status == "FAIL"
    assert rows["gws-auth"].evidence == "fake failure"
    assert rows["gws-auth"].fix == "gws auth login"
    assert rows["gws-auth"].anchor == "google-calendar"
    # One gws fault is one FAIL: the calendar row that would repeat it is a SKIP.
    assert rows["concerts-calendar"] == Check("concerts-calendar", "SKIP", "gws-auth failed")
    assert rows["spotify-auth"].status == "FAIL"
    assert rows["spotify-auth"].anchor == "spotify"
    assert rows["setlistfm-auth"].status == "FAIL"
    assert rows["setlistfm-auth"].anchor == "setlistfm"
    for row in rows.values():
        if row.status == "FAIL":
            assert len(row.evidence) <= 120


def test_probe_with_unconfigured_spotify_and_setlistfm_skips(settings: Settings, now: datetime) -> None:
    svc = make_services(settings, now, route=None, text="")
    svc.spotify = None
    svc.setlistfm = None
    rows = by_name(run_healthcheck(settings, probe=True, svc=svc))
    assert rows["spotify-auth"] == Check("spotify-auth", "SKIP", "not configured")
    assert rows["setlistfm-auth"] == Check("setlistfm-auth", "SKIP", "not configured")


def test_concerts_calendar_missing_is_a_fail_with_the_fix(settings: Settings, now: datetime) -> None:
    from fakes import FakeCalendar

    svc = make_services(settings, now, route=None, text="")
    svc.calendar = FakeCalendar(calendars=[{"id": "primary", "summary": "Personal"}])
    row = by_name(run_healthcheck(settings, probe=True, svc=svc))["concerts-calendar"]
    assert row.status == "FAIL"
    assert row.anchor == "google-calendar"
    assert "matched 0 calendars" in row.evidence
    assert row.fix == "use one of: Personal"


# ---------- never raises ----------


def test_a_check_raising_becomes_a_fail_with_the_type_name(bare: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(ctx: Any) -> Check:
        raise ZeroDivisionError("division by zero")

    monkeypatch.setitem(mod.CHECKS, "python", (explode, "getting-started"))
    row = by_name(run_healthcheck(bare, only=["python"]))["python"]
    assert row.status == "FAIL"
    assert row.evidence == "ZeroDivisionError: division by zero"
    assert row.anchor == "getting-started"
    assert row.fix


def test_openclaw_silent_reply_rows(bare: Settings) -> None:
    # 2026-11-10: without this setting OpenClaw took the model's NO_REPLY for a missing answer and posted a second
    # message after the tool's own reply.
    def row() -> Check:
        return by_name(run_healthcheck(bare))["openclaw-silent-reply"]

    assert row() == Check("openclaw-silent-reply", "SKIP", "absent")
    _write(bare.openclaw_config, json.dumps({"agents": {"defaults": {"silentReply": {"group": "allow"}}}}), mode=0o600)
    assert row() == Check("openclaw-silent-reply", "PASS", "group replies may end silently (one message per tool call)")
    for cfg in ({}, {"agents": {"defaults": {"silentReply": {"group": "disallow"}}}}, {"agents": {"defaults": {}}}):
        _write(bare.openclaw_config, json.dumps(cfg), mode=0o600)
        got = row()
        assert (got.status, got.anchor, got.fix) == (
            "FAIL",
            "openclaw",
            "openclaw config set agents.defaults.silentReply.group allow",
        )
        assert "second message" in got.evidence
    _write(bare.openclaw_config, "{broken", mode=0o600)
    assert row() == Check("openclaw-silent-reply", "SKIP", "unreadable")


def test_check_names_and_registry_agree() -> None:
    assert tuple(mod.CHECKS) == CHECK_NAMES
    assert len(CHECK_NAMES) == 20


# ---------- the outcome ----------


def test_places_key_rows(bare: Settings) -> None:
    _write(bare.places_credentials, json.dumps({"api_key": "k" * 39}))
    assert by_name(run_healthcheck(bare))["places-key"] == Check("places-key", "PASS", "present, mode 600")
    _write(bare.places_credentials, json.dumps({}), mode=0o644)
    assert by_name(run_healthcheck(bare))["places-key"] == Check(
        "places-key", "FAIL", "mode 0o644, want 600", "configuration", f"chmod 600 {bare.places_credentials}"
    )
