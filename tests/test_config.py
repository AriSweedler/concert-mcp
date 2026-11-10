"""config.py: defaults, precedence, parsing, legacy fallback, the env table, redaction and migration."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from concert_mcp import config
from concert_mcp.config import (
    ENV_PREFIX,
    env_table,
    load_settings,
    migrate,
    read_config_file,
    redacted,
)


def test_env_prefix() -> None:
    assert ENV_PREFIX == "CONCERT_MCP_"


def test_gws_bin_found_in_a_fallback_dir_when_path_misses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A launchd-run gateway has a bare PATH; Homebrew's dir (and ~/.local/bin) are still searched.
    local_bin = tmp_path / ".local/bin"
    local_bin.mkdir(parents=True)
    gws = local_bin / "gws"
    gws.write_text("#!/bin/sh\n")
    gws.chmod(0o755)
    monkeypatch.setattr(config, "FALLBACK_BIN_DIRS", (str(tmp_path / "nowhere"), "~/.local/bin"))
    s = load_settings({"PATH": str(tmp_path / "empty")}, home=tmp_path)
    assert s.gws_bin == str(gws)
    assert s.openclaw_bin == "openclaw"  # not in any dir: the bare name, which the healthcheck reports


def test_env_over_toml_over_default(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        'tz = "Europe/Paris"\noperator_name = "Toml"\ngws_timeout_s = 7\nsend = false\n'
    )
    env = {"CONCERT_MCP_CONFIG_DIR": str(config_dir), "CONCERT_MCP_OPERATOR_NAME": "Env"}
    s = load_settings(env, home=tmp_path)
    assert s.tz == "Europe/Paris"  # toml over default
    assert s.operator_name == "Env"  # env over toml
    assert s.gws_timeout_s == 7  # toml ints are coerced
    assert s.send is False  # toml booleans are coerced
    assert s.default_calendar_name == "Personal"  # default when neither sets it


def test_read_config_file_absent_and_invalid(tmp_path: Path) -> None:
    assert read_config_file(tmp_path / "nope.toml") == {}
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = not = toml\n")
    with pytest.raises(ValueError, match="bad.toml"):
        read_config_file(bad)


def test_bool_garbage_names_the_env_var(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="CONCERT_MCP_SEND"):
        load_settings({"CONCERT_MCP_SEND": "maybe"}, home=tmp_path)


def test_int_garbage_names_the_env_var(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="CONCERT_MCP_GWS_TIMEOUT_S"):
        load_settings({"CONCERT_MCP_GWS_TIMEOUT_S": "soon"}, home=tmp_path)


def _write(path: Path, text: str = "{}") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_redacted_never_reads_file_contents_and_marks_legacy(tmp_path: Path) -> None:
    secret = "SECRET-refresh-token-value"
    _write(tmp_path / ".config/ari-spotify/credentials.json", f'{{"refresh_token": "{secret}"}}')
    _write(tmp_path / ".config/concert-mcp/setlistfm.json", f'{{"api_key": "{secret}"}}')
    s = load_settings({}, home=tmp_path)
    rows = redacted(s)
    by_field = {name: (value, status) for name, value, status in rows}
    assert secret not in " ".join(value for _n, value, _s in rows)
    assert by_field["spotify_credentials"][1] == "LEGACY"
    assert by_field["setlistfm_credentials"][1] == "EXISTS"
    assert by_field["liked_cache"][1] == "MISSING"
    assert by_field["tz"] == ("America/Los_Angeles", "-")
    assert by_field["send"] == ("True", "-")
    assert [name for name, _v, _s in rows] == [name for name, _d, _m in env_table_fields()]


def env_table_fields() -> list[tuple[str, str, str]]:
    return [(name[len(ENV_PREFIX) :].lower(), default, meaning) for name, default, meaning in env_table()]


def test_migrate_copies_with_mode_600_and_skips_existing(tmp_path: Path) -> None:
    legacy_creds = _write(tmp_path / ".config/ari-spotify/credentials.json", '{"refresh_token": "x"}')
    legacy_queue = _write(tmp_path / ".local/state/ari-spotify/setlist-queue.json", "[]")
    s = load_settings({}, home=tmp_path)
    assert {"spotify_credentials", "setlist_queue"} <= s.legacy_in_use

    result = migrate(s, tmp_path, env={})
    by_src = {src: (dst, status) for src, dst, status in result}
    dst_creds, status = by_src[legacy_creds]
    assert status == "copied"
    assert dst_creds == tmp_path / ".config/concert-mcp/spotify.json"
    assert dst_creds.read_text() == '{"refresh_token": "x"}'
    assert stat.S_IMODE(dst_creds.stat().st_mode) == 0o600
    dst_queue, status = by_src[legacy_queue]
    assert status == "copied"
    assert dst_queue == tmp_path / ".local/state/concert-mcp/setlist-queue.json"
    assert legacy_creds.exists() and legacy_queue.exists()  # never deletes
    statuses = {status for _s, _d, status in result}
    assert "skipped: no legacy" in statuses  # setlistfm and liked cache had no legacy file

    again = migrate(s, tmp_path, env={})
    assert {status for _s, _d, status in again} == {"skipped: exists", "skipped: no legacy"}
