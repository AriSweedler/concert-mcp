"""config.py: defaults, precedence, parsing, legacy fallback, the env table, redaction and migration."""

from __future__ import annotations

import re
import stat
from dataclasses import fields
from pathlib import Path

import pytest

from concert_mcp import config
from concert_mcp.config import (
    DERIVED_FIELDS,
    ENV_PREFIX,
    LEGACY_FIELDS,
    Settings,
    env_table,
    legacy_paths,
    load_settings,
    migrate,
    read_config_file,
    redacted,
    with_legacy_fallback,
)


def test_env_prefix() -> None:
    assert ENV_PREFIX == "CONCERT_MCP_"


def test_defaults_from_a_fake_home(tmp_path: Path) -> None:
    s = load_settings({}, home=tmp_path)
    assert s.config_dir == tmp_path / ".config/concert-mcp"
    assert s.state_dir == tmp_path / ".local/state/concert-mcp"
    assert s.cache_dir == tmp_path / ".cache/concert-mcp"
    assert s.spotify_credentials == s.config_dir / "spotify.json"
    assert s.setlistfm_credentials == s.config_dir / "setlistfm.json"
    assert s.liked_cache == s.cache_dir / "liked.json"
    assert s.setlist_queue == s.state_dir / "setlist-queue.json"
    assert s.candidates_file == s.state_dir / "delete-candidates.json"
    assert s.sent_log == s.state_dir / "last-sent.json"
    assert s.fix_log == s.state_dir / "fix-log.json"
    assert s.gws_bin == "gws"  # no PATH in the env and no fallback dir (the fixture empties them)
    assert s.gws_key_file == tmp_path / ".config/gws/.encryption_key"
    assert s.gws_timeout_s == 60
    assert s.openclaw_bin == "openclaw"
    assert s.openclaw_home == tmp_path / ".openclaw"
    assert s.openclaw_config == s.openclaw_home / "openclaw.json"
    assert s.session_db == s.openclaw_home / "agents/main/agent/openclaw-agent.sqlite"
    assert s.ocr_last == s.openclaw_home / "media/ocr-last.txt"
    assert s.tz == "America/Los_Angeles"
    assert (s.concerts_calendar, s.default_calendar, s.default_calendar_name) == ("Concerts", "primary", "Personal")
    assert (s.context_window_s, s.dedupe_window_s, s.teaser_timeout_s, s.http_timeout_s) == (900, 600, 150, 30)
    assert (s.liked_cache_ttl_s, s.setlist_queue_days, s.default_duration_h) == (21600, 61, 2)
    assert (s.past_grace_h, s.max_future_days) == (12, 730)
    assert s.spotify_redirect_uri == "http://127.0.0.1:8888/callback"
    assert s.spotify_scopes == "user-library-read playlist-read-private playlist-modify-private playlist-modify-public"
    assert (s.op_vault, s.op_spotify_item, s.op_setlistfm_item) == (
        "Private",
        "Spotify - concert-mcp",
        "setlist.fm - API key",
    )
    assert s.operator_name == "Admin"
    assert s.send is True
    assert s.rename_topics is True
    assert s.log_level == "info"
    assert s.legacy_in_use == frozenset()


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


def test_gws_bin_found_on_path(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gws = bindir / "gws"
    gws.write_text("#!/bin/sh\n")
    gws.chmod(0o755)
    s = load_settings({"PATH": str(bindir)}, home=tmp_path)
    assert s.gws_bin == str(gws)


def test_xdg_dirs_override_the_defaults(tmp_path: Path) -> None:
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / "xc"),
        "XDG_STATE_HOME": str(tmp_path / "xs"),
        "XDG_CACHE_HOME": str(tmp_path / "xk"),
    }
    s = load_settings(env, home=tmp_path)
    assert s.config_dir == tmp_path / "xc/concert-mcp"
    assert s.state_dir == tmp_path / "xs/concert-mcp"
    assert s.cache_dir == tmp_path / "xk/concert-mcp"


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


def test_tilde_in_env_values_means_the_given_home(tmp_path: Path) -> None:
    s = load_settings({"CONCERT_MCP_GWS_KEY_FILE": "~/keys/gws"}, home=tmp_path)
    assert s.gws_key_file == tmp_path / "keys/gws"


def test_read_config_file_absent_and_invalid(tmp_path: Path) -> None:
    assert read_config_file(tmp_path / "nope.toml") == {}
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = not = toml\n")
    with pytest.raises(ValueError, match="bad.toml"):
        read_config_file(bad)


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "Yes", "on"])
def test_bool_true_forms(tmp_path: Path, raw: str) -> None:
    assert load_settings({"CONCERT_MCP_SEND": raw}, home=tmp_path).send is True


@pytest.mark.parametrize("raw", ["0", "false", "No", "OFF"])
def test_bool_false_forms(tmp_path: Path, raw: str) -> None:
    assert load_settings({"CONCERT_MCP_RENAME_TOPICS": raw}, home=tmp_path).rename_topics is False


def test_bool_garbage_names_the_env_var(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="CONCERT_MCP_SEND"):
        load_settings({"CONCERT_MCP_SEND": "maybe"}, home=tmp_path)


def test_int_garbage_names_the_env_var(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="CONCERT_MCP_GWS_TIMEOUT_S"):
        load_settings({"CONCERT_MCP_GWS_TIMEOUT_S": "soon"}, home=tmp_path)


def test_legacy_paths_follow_xdg(tmp_path: Path) -> None:
    paths = legacy_paths(tmp_path, {})
    assert paths["spotify_credentials"] == tmp_path / ".config/ari-spotify/credentials.json"
    assert paths["setlistfm_credentials"] == tmp_path / ".config/ari-spotify/setlistfm.json"
    assert paths["liked_cache"] == tmp_path / ".cache/ari-spotify/liked.json"
    assert paths["setlist_queue"] == tmp_path / ".local/state/ari-spotify/setlist-queue.json"
    xdg = legacy_paths(tmp_path, {"XDG_CONFIG_HOME": str(tmp_path / "xc")})
    assert xdg["spotify_credentials"] == tmp_path / "xc/ari-spotify/credentials.json"


def test_with_legacy_fallback(tmp_path: Path) -> None:
    new, legacy = tmp_path / "new.json", tmp_path / "legacy.json"
    assert with_legacy_fallback(new, legacy) == (new, False)
    legacy.write_text("{}")
    assert with_legacy_fallback(new, legacy) == (legacy, True)
    new.write_text("{}")
    assert with_legacy_fallback(new, legacy) == (new, False)


def _write(path: Path, text: str = "{}") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_legacy_fallback_only_when_the_new_path_is_missing(tmp_path: Path) -> None:
    legacy = _write(tmp_path / ".config/ari-spotify/credentials.json")
    s = load_settings({}, home=tmp_path)
    assert s.spotify_credentials == legacy
    assert s.legacy_in_use == frozenset({"spotify_credentials"})
    _write(tmp_path / ".config/concert-mcp/spotify.json")
    s2 = load_settings({}, home=tmp_path)
    assert s2.spotify_credentials == tmp_path / ".config/concert-mcp/spotify.json"
    assert s2.legacy_in_use == frozenset()


def test_no_legacy_fallback_when_the_env_var_is_set(tmp_path: Path) -> None:
    _write(tmp_path / ".config/ari-spotify/credentials.json")
    wanted = tmp_path / "elsewhere/spotify.json"  # does not exist
    s = load_settings({"CONCERT_MCP_SPOTIFY_CREDENTIALS": str(wanted)}, home=tmp_path)
    assert s.spotify_credentials == wanted
    assert "spotify_credentials" not in s.legacy_in_use


def test_legacy_in_use_covers_all_four_paths(tmp_path: Path) -> None:
    for p in legacy_paths(tmp_path, {}).values():
        _write(p)
    s = load_settings({}, home=tmp_path)
    assert s.legacy_in_use == frozenset(LEGACY_FIELDS)
    assert s.setlist_queue == tmp_path / ".local/state/ari-spotify/setlist-queue.json"


def test_env_table_covers_every_dataclass_field() -> None:
    names = {name for name, _default, _meaning in env_table()}
    expected = {ENV_PREFIX + f.name.upper() for f in fields(Settings) if f.name not in DERIVED_FIELDS}
    assert names == expected
    assert all(name.startswith(ENV_PREFIX) for name in names)
    assert all(meaning for _n, _d, meaning in env_table())


def test_env_table_defaults_carry_no_personal_data() -> None:
    for name, default, meaning in env_table():
        assert "@" not in default, name
        assert "@" not in meaning, name
        assert not re.fullmatch(r"-?\d{9,}", default), name  # no chat ids, no numeric calendar ids


def test_settings_defaults_carry_no_personal_data(tmp_path: Path) -> None:
    s = load_settings({}, home=tmp_path)
    for f in fields(Settings):
        value = getattr(s, f.name)
        if isinstance(value, str):
            assert "@" not in value, f.name
            assert not re.fullmatch(r"-?\d{9,}", value), f.name


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


def test_migrate_dry_run_writes_nothing(tmp_path: Path) -> None:
    _write(tmp_path / ".config/ari-spotify/setlistfm.json", '{"api_key": "k"}')
    s = load_settings({}, home=tmp_path)
    result = migrate(s, tmp_path, dry=True, env={})
    statuses = {status for _s, _d, status in result}
    assert "would copy" in statuses
    assert not (tmp_path / ".config/concert-mcp/setlistfm.json").exists()


def test_settings_is_frozen(tmp_path: Path) -> None:
    s = load_settings({}, home=tmp_path)
    with pytest.raises(AttributeError):
        s.tz = "UTC"  # type: ignore[misc]
