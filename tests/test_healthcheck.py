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
from concert_mcp.flows.healthcheck import CHECK_NAMES, healthcheck_outcome, run_healthcheck
from concert_mcp.flows.services import Services
from concert_mcp.result import Kind
from concert_mcp.text.health import ANCHORS, Check, HealthReport, render_health
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


def test_bare_tmp_settings_table(bare: Settings) -> None:
    report = run_healthcheck(bare)
    assert [c.name for c in report.checks] == list(CHECK_NAMES)
    rows = by_name(report)

    assert rows["python"].status == "PASS"
    assert rows["mcp-sdk"].status in ("PASS", "FAIL")  # PASS when the SDK is installed, FAIL with the uv hint
    # Read-only: the two directories are not created, only judged creatable.
    assert rows["config-dir"] == Check("config-dir", "PASS", f"{bare.config_dir} absent (created on first write)")
    assert rows["state-dir"] == Check("state-dir", "PASS", f"{bare.state_dir} absent (created on first write)")
    assert not bare.config_dir.exists() and not bare.state_dir.exists()

    assert rows["gws-binary"].status == "FAIL"
    assert rows["gws-binary"].anchor == "google-calendar"
    assert rows["gws-binary"].fix == "install the gws CLI, or set CONCERT_MCP_GWS_BIN to its path"
    assert rows["gws-keyfile"] == Check("gws-keyfile", "FAIL", "missing", "google-calendar", mod.KEYFILE_FIX)
    assert "GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file gws auth login" in mod.KEYFILE_FIX
    assert rows["gws-auth"] == Check("gws-auth", "SKIP", "pass --probe")
    assert rows["concerts-calendar"] == Check("concerts-calendar", "SKIP", "pass --probe")

    assert rows["spotify-credentials"] == Check(
        "spotify-credentials", "FAIL", "missing", "spotify", "concert-mcp spotify auth"
    )
    assert rows["spotify-auth"] == Check("spotify-auth", "SKIP", "pass --probe")
    assert rows["liked-cache"] == Check("liked-cache", "SKIP", "absent: the first teaser will be slow (~40 s)")

    assert rows["setlistfm-key"] == Check(
        "setlistfm-key", "FAIL", "missing", "setlistfm", "concert-mcp spotify setlist-auth"
    )
    assert rows["setlistfm-auth"] == Check("setlistfm-auth", "SKIP", "pass --probe")

    assert rows["openclaw-binary"].status == "FAIL"
    assert rows["openclaw-binary"].anchor == "openclaw"
    assert rows["openclaw-binary"].fix == "install openclaw, or set CONCERT_MCP_OPENCLAW_BIN to its path"
    assert rows["openclaw-config"] == Check("openclaw-config", "SKIP", "absent")
    assert rows["openclaw-session-db"] == Check(
        "openclaw-session-db", "SKIP", "absent or unreadable: replies are not delivered"
    )
    assert rows["setlist-queue"] == Check("setlist-queue", "PASS", "0 shows queued")

    assert report.failures == sum(1 for c in report.checks if c.status == "FAIL")
    assert report.failures >= 5
    assert report.ok is False


def test_every_fail_row_has_a_known_anchor_and_a_fix(bare: Settings) -> None:
    for c in run_healthcheck(bare).checks:
        if c.status == "FAIL":
            assert c.anchor in ANCHORS, c
            assert c.fix, c


def test_render_of_the_bare_table_matches_the_doctor_shape(bare: Settings) -> None:
    report = run_healthcheck(bare)
    text = render_health(report)
    lines = text.split("\n")
    assert lines[-1] == f"{report.failures} failing"
    fix_lines = [ln for ln in lines if ln.startswith("  ->")]
    assert len(fix_lines) == report.failures
    for ln in lines[:-1]:
        if not ln.startswith("  ->"):
            assert ROW_RE.match(ln), ln
    assert "FAIL  gws-keyfile" in text
    assert "(README.md#google-calendar)" in text
    assert "(README.md#spotify)" in text
    assert "(README.md#setlistfm)" in text


def test_send_false_skips_the_openclaw_rows(tmp_path: Path) -> None:
    settings = load_settings(bare_env(tmp_path, send="0"), home=tmp_path)
    rows = by_name(run_healthcheck(settings))
    for name in ("openclaw-binary", "openclaw-config", "openclaw-session-db"):
        assert rows[name] == Check(name, "SKIP", "delivery off (CONCERT_MCP_SEND=0)")


def test_only_filters_by_name_in_check_order(bare: Settings) -> None:
    report = run_healthcheck(bare, only=["python"])
    assert [c.name for c in report.checks] == ["python"]
    report = run_healthcheck(bare, only=["setlist-queue", "python", "no-such-check"])
    assert [c.name for c in report.checks] == ["python", "setlist-queue"]


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


def test_gws_binary_passes_when_the_configured_path_exists(tmp_path: Path) -> None:
    gws = _write(tmp_path / "bin" / "gws", "#!/bin/sh\n", mode=0o755)
    settings = load_settings({**bare_env(tmp_path), "CONCERT_MCP_GWS_BIN": str(gws)}, home=tmp_path)
    row = by_name(run_healthcheck(settings, only=["gws-binary"]))["gws-binary"]
    assert row.status == "PASS"
    assert row.evidence == str(gws)


def test_spotify_credentials_present_mode_600(bare: Settings) -> None:
    _write(bare.spotify_credentials, SPOTIFY_JSON)
    row = by_name(run_healthcheck(bare))["spotify-credentials"]
    assert row == Check("spotify-credentials", "PASS", "present, mode 600")


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


def test_spotify_file_at_the_legacy_path_only_passes_with_the_migrate_hint(tmp_path: Path) -> None:
    _write(tmp_path / ".config" / "ari-spotify" / "credentials.json", SPOTIFY_JSON)
    settings = load_settings(bare_env(tmp_path), home=tmp_path)
    assert "spotify_credentials" in settings.legacy_in_use
    row = by_name(run_healthcheck(settings))["spotify-credentials"]
    assert row.status == "PASS"
    assert row.evidence == "present, mode 600, LEGACY path: run concert-mcp migrate"
    assert "run concert-mcp migrate" in render_health(run_healthcheck(settings))


def test_setlistfm_key_rows(bare: Settings) -> None:
    _write(bare.setlistfm_credentials, json.dumps({"api_key": "k" * 32}))
    assert by_name(run_healthcheck(bare))["setlistfm-key"] == Check("setlistfm-key", "PASS", "present, mode 600")
    _write(bare.setlistfm_credentials, json.dumps({}))
    assert by_name(run_healthcheck(bare))["setlistfm-key"] == Check(
        "setlistfm-key", "FAIL", "incomplete", "setlistfm", "concert-mcp spotify setlist-auth"
    )


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


def test_liked_cache_age_from_the_state_store_and_clock(svc: Services) -> None:
    svc.state.write_json(svc.settings.liked_cache, [])
    svc.clock.advance(3 * 3600)  # type: ignore[attr-defined]
    row = by_name(run_healthcheck(svc.settings, svc=svc))["liked-cache"]
    assert row == Check("liked-cache", "PASS", "3.0 h old")


def test_setlist_queue_counts_rows(bare: Settings) -> None:
    _write(bare.setlist_queue, json.dumps([{"event_id": "a"}, {"event_id": "b"}]), mode=0o644)
    assert by_name(run_healthcheck(bare))["setlist-queue"] == Check("setlist-queue", "PASS", "2 shows queued")
    _write(bare.setlist_queue, "{garbage", mode=0o644)
    assert by_name(run_healthcheck(bare))["setlist-queue"] == Check("setlist-queue", "PASS", "0 shows queued")


def test_setlist_queue_from_the_state_store(svc: Services) -> None:
    svc.state.write_json(svc.settings.setlist_queue, [{"event_id": "a"}])
    assert by_name(run_healthcheck(svc.settings, svc=svc))["setlist-queue"].evidence == "1 shows queued"


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


def test_probe_true_with_the_fake_services(svc: Services) -> None:
    rows = by_name(run_healthcheck(svc.settings, probe=True, svc=svc))
    assert rows["gws-auth"] == Check("gws-auth", "PASS", "2 calendars")
    assert rows["concerts-calendar"] == Check("concerts-calendar", "PASS", "found Concerts")
    assert "cal-concerts" not in rows["concerts-calendar"].evidence  # the id is never evidence
    assert rows["spotify-auth"] == Check("spotify-auth", "PASS", "Fake User")
    assert rows["setlistfm-auth"] == Check("setlistfm-auth", "PASS", "0 setlists for the probe artist")
    assert svc.setlistfm.calls == [{"artist": mod.PROBE_ARTIST, "date": None, "year": None, "page": 1}]  # type: ignore[union-attr]


def test_probe_false_makes_no_provider_call(svc: Services) -> None:
    run_healthcheck(svc.settings, probe=False, svc=svc)
    assert svc.calendar.calls == []  # type: ignore[attr-defined]
    assert svc.spotify.calls == []  # type: ignore[union-attr]
    assert svc.setlistfm.calls == []  # type: ignore[union-attr]


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


def test_check_names_and_registry_agree() -> None:
    assert tuple(mod.CHECKS) == CHECK_NAMES
    assert len(CHECK_NAMES) == 17


# ---------- the outcome ----------


def test_healthcheck_outcome_error_when_failing(bare: Settings) -> None:
    outcome = healthcheck_outcome(bare, probe=False, svc=None)
    assert outcome.kind is Kind.ERROR
    assert outcome.ok is False
    assert outcome.reply == render_health(run_healthcheck(bare))
    assert outcome.data["failures"] == run_healthcheck(bare).failures
    assert [c["name"] for c in outcome.data["checks"]] == list(CHECK_NAMES)


def test_healthcheck_outcome_ok_when_nothing_fails(svc: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CHECK_NAMES:
        monkeypatch.setitem(mod.CHECKS, name, (lambda ctx, n=name: Check(n, "PASS", "ok"), "configuration"))
    outcome = healthcheck_outcome(svc.settings, probe=False, svc=svc)
    assert outcome.kind is Kind.OK
    assert outcome.reply.endswith("\nall good")  # type: ignore[union-attr]
    assert outcome.data == {
        "ok": True,
        "failures": 0,
        "checks": [{"name": n, "status": "PASS", "evidence": "ok", "anchor": None, "fix": None} for n in CHECK_NAMES],
    }
