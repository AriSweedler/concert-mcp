"""The CLI over fake services: legacy input forms, the output contract, exit codes, stdout discipline."""

from __future__ import annotations

import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from concert_mcp import cli
from concert_mcp.flows import services
from concert_mcp.flows.concert_add import ALIASES, KNOWN
from concert_mcp.flows.event_delete import DELETE_ALIASES, DELETE_KNOWN
from concert_mcp.flows.services import Services
from concert_mcp.result import FixInput
from conftest import settings_env
from fakes import plain

ADD_FLAGS = [
    "add",
    "--concert",
    "--dry-run",
    "--no-send",
    "--json",
    "--title",
    "Zed Fox",
    "--start",
    "2026-11-14 20:30",
    "--location",
    "The Quarry, San Francisco",
]
ADD_RECORD_KEYS = {
    "ok",
    "dryRun",
    "calendar",
    "summary",
    "start",
    "end",
    "location",
    "description",
    "dropped",
    "teaser",
    "setlist",
    "topic",
    "reply",
    "sent",
}
HEALTH_ROW = re.compile(r"^(PASS|FAIL|SKIP)  \S+\s+.+$")


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, svc_cli: Services) -> Services:
    """The CLI's environment: every CONCERT_MCP_* dir under tmp_path, HOME in tmp, services replaced by the fakes."""
    for k, v in settings_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(services, "build_services", lambda settings=None: svc_cli)
    return svc_cli


def run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str], stdin: str = ""):
    """Run main(argv) with `stdin` as its input; returns (exit code, stdout, stderr without colour)."""
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = cli.main(argv)
    cap = capsys.readouterr()
    return code, cap.out, plain(cap.err)


def one_json_line(out: str) -> dict[str, Any]:
    """stdout must be exactly one JSON line."""
    assert out.endswith("\n") and out.count("\n") == 1, out
    return json.loads(out)


def assert_clean_stdout(out: str, err: str) -> None:
    assert "[INFO]" not in out and "[WARN]" not in out and "[ERROR]" not in out
    assert "Traceback" not in out and "Traceback" not in err
    for line in err.splitlines():
        assert line.startswith(("[INFO]", "[WARN]", "[ERROR]", "[DEBUG]")), err


# --- read_request: the legacy flag / JSON / stdin parse ---------------------------------------------------------------


def test_read_request_flags_fold_aliases_and_repeat_lists() -> None:
    ev = cli.read_request(
        ["--artist", "Zed Fox", "--description", "Doors: 7:30 PM", "--notes", "Seat: GA", "--attendees", "a@b.c"],
        io.StringIO(""),
        known=KNOWN,
        aliases=ALIASES,
        json_hint=cli.ADD_JSON_HINT,
    )
    assert ev == {"title": "Zed Fox", "description": ["Doors: 7:30 PM", "Seat: GA"], "attendees": ["a@b.c"]}


def test_read_request_unknown_flag_lists_allowed_without_date_time() -> None:
    with pytest.raises(FixInput) as e:
        cli.read_request(["--bogus", "x"], io.StringIO(""), known=KNOWN, aliases=ALIASES, json_hint="h")
    assert e.value.error == "unknown flag --bogus"
    assert e.value.fix == "allowed flags: " + ", ".join("--" + k for k in sorted(KNOWN - {"date", "time"}))


def test_read_request_del_hint_keeps_date() -> None:
    with pytest.raises(FixInput) as e:
        cli.read_request(
            ["--bogus", "x"],
            io.StringIO(""),
            known=DELETE_KNOWN,
            aliases=DELETE_ALIASES,
            list_keys=frozenset(),
            json_hint="h",
            hint_hidden=frozenset(),
        )
    assert e.value.fix == "allowed flags: " + ", ".join("--" + k for k in sorted(DELETE_KNOWN))


def test_read_request_missing_value() -> None:
    with pytest.raises(FixInput) as e:
        cli.read_request(["--title"], io.StringIO(""), known=KNOWN, aliases=ALIASES, json_hint="h")
    assert (e.value.error, e.value.fix) == ("--title needs a value", "pass a value after --title")


def test_read_request_json_positional_then_stdin() -> None:
    ev = cli.read_request(
        ['{"artist": "X"}'], io.StringIO('{"ignored": 1}'), known=KNOWN, aliases=ALIASES, json_hint="h"
    )
    assert ev == {"artist": "X"}
    ev = cli.read_request([], io.StringIO('{"title": "Y"}'), known=KNOWN, aliases=ALIASES, json_hint="h")
    assert ev == {"title": "Y"}


def test_read_request_bad_json_and_non_object() -> None:
    with pytest.raises(FixInput) as e:
        cli.read_request([], io.StringIO("not json"), known=KNOWN, aliases=ALIASES, json_hint=cli.ADD_JSON_HINT)
    assert e.value.error.startswith("input is not valid JSON: ")
    assert e.value.fix == cli.ADD_JSON_HINT
    with pytest.raises(FixInput) as e:
        cli.read_request(["[1, 2]"], io.StringIO(""), known=KNOWN, aliases=ALIASES, json_hint="h")
    assert (e.value.error, e.value.fix) == ("input must be a JSON object", "send one {...} object")


# --- add -------------------------------------------------------------------------------------------------------------


def test_add_dry_run_json_record(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, err = run(monkeypatch, capsys, ADD_FLAGS)
    rec = one_json_line(out)
    assert code == 0
    assert set(rec) >= ADD_RECORD_KEYS
    assert rec["ok"] is True and rec["dryRun"] is True
    assert rec["calendar"] == "Concerts" and rec["summary"] == "Zed Fox"
    assert rec["start"].startswith("2026-11-14T20:30")
    assert rec["topic"] == "2026-11-14 | Zed Fox | The Quarry"
    assert rec["reply"].startswith("**Would add to Concerts**")
    assert rec["sent"].startswith("NOT SENT")
    assert_clean_stdout(out, err)


def test_add_same_record_from_flags_stdin_and_aliased_positional(cli_env: Services, monkeypatch, capsys) -> None:
    _, out_flags, _ = run(monkeypatch, capsys, ADD_FLAGS)
    stdin = json.dumps({"title": "Zed Fox", "start": "2026-11-14 20:30", "location": "The Quarry, San Francisco"})
    _, out_stdin, _ = run(monkeypatch, capsys, ["add", "--concert", "--dry-run", "--no-send", "--json"], stdin)
    positional = json.dumps({"artist": "Zed Fox", "datetime": "2026-11-14 20:30", "venue": "The Quarry, San Francisco"})
    _, out_pos, _ = run(monkeypatch, capsys, ["--json", "--dry-run", "--no-send", "add", "--concert", positional])
    assert one_json_line(out_flags) == one_json_line(out_stdin) == one_json_line(out_pos)


def test_add_unknown_flag_is_a_fix_line(cli_env: Services, monkeypatch, capsys) -> None:
    """The FIX line carries the fix, as the legacy bad() printed it; the error text is in the --json record."""
    code, out, err = run(monkeypatch, capsys, ["add", "--bogus", "x"])
    assert code == 1
    allowed = ", ".join("--" + k for k in sorted(KNOWN - {"date", "time"}))
    assert out == f"FIX: allowed flags: {allowed}\n"
    assert "Traceback" not in err


def test_add_unknown_flag_json_is_the_retry_record(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["add", "--json", "--bogus", "x"])
    rec = one_json_line(out)
    assert code == 1
    assert rec["ok"] is False and rec["retry"] is True
    assert rec["error"] == "unknown flag --bogus"
    assert rec["fix"].startswith("allowed flags: --allowPast, --attendees, --calendar")
    assert "sent" in rec


def test_add_flag_without_value(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["add", "--title"])
    assert code == 1
    assert out == "FIX: pass a value after --title\n"


def test_add_invalid_json_stdin(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["add", "--concert"], "{title: Zed Fox}")
    assert code == 1
    assert out == f"FIX: {cli.ADD_JSON_HINT}\n"


def test_add_unknown_json_key_is_a_fix(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["add"], json.dumps({"title": "X", "colour": "red"}))
    assert code == 1
    assert out.startswith("FIX: allowed keys: ")


def test_add_plain_text_dry_run(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, err = run(monkeypatch, capsys, ADD_FLAGS[:-7] + ADD_FLAGS[-6:])  # drop --json
    assert code == 0
    assert out.startswith("**Would add to Concerts**\n• **Artist:** Zed Fox\n")
    assert out.rstrip("\n").splitlines()[-1] == "NOT SENT: dry run"
    assert_clean_stdout(out, err)


# --- del -------------------------------------------------------------------------------------------------------------


def _seed_candidates(svc: Services) -> list[dict[str, Any]]:
    tz = ZoneInfo("America/Los_Angeles")
    cal = svc.calendar
    rows = []
    for day in (14, 15):
        start = datetime(2026, 11, day, 20, 30, tzinfo=tz)
        e = cal.seed_event("cal-concerts", summary="Zed Fox", start=start, location="The Quarry, San Francisco")
        rows.append(
            {
                "id": e["id"],
                "calendarId": "cal-concerts",
                "calendar": "Concerts",
                "summary": "Zed Fox",
                "when": f"{start:%a %b %-d, %-I:%M %p} – 10:30 PM PST",
                "start": start.isoformat(),
                "location": "The Quarry, San Francisco",
                "htmlLink": e["htmlLink"],
                "description": "",
            }
        )
    svc.state.write_json(svc.settings.candidates_file, rows)
    return rows


def test_del_pick_reads_the_candidates_and_removes(cli_env: Services, monkeypatch, capsys) -> None:
    rows = _seed_candidates(cli_env)
    code, out, err = run(monkeypatch, capsys, ["del", "--pick", "2", "--no-send"])
    assert code == 0
    lines = out.rstrip("\n").splitlines()
    assert lines[0] == "**Removed from Concerts**"
    assert "Sun Nov 15" in out
    assert lines[-1] == "NOT SENT: delivery disabled"
    assert cli_env.calendar.deleted == [("cal-concerts", rows[1]["id"])]
    assert cli_env.state.read_json(cli_env.settings.candidates_file, None) is None
    assert_clean_stdout(out, err)


def test_delete_alias_and_json_record(cli_env: Services, monkeypatch, capsys) -> None:
    _seed_candidates(cli_env)
    code, out, _ = run(monkeypatch, capsys, ["delete", "--json", "--no-send", "--number", "1"])
    rec = one_json_line(out)
    assert code == 0
    assert rec["ok"] is True and rec["deleted"] is True
    assert rec["sent"] == "NOT SENT: delivery disabled"


def test_del_bad_date_is_a_fix(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["del", "--title", "Zed Fox", "--date", "2026-13-45"])
    assert code == 1
    assert out == 'FIX: send the day as "2026-10-24"\n'


def test_del_invalid_json_has_its_own_hint(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["del"], "nope")
    assert code == 1
    assert out == f"FIX: {cli.DEL_JSON_HINT}\n"


# --- teaser / setlist-find / queue -----------------------------------------------------------------------------------


def test_teaser_prints_the_standalone_block_and_not_sent(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, err = run(monkeypatch, capsys, ["teaser", "--artist", "Zed Fox", "--date", "2026-11-14", "--no-send"])
    assert code == 0
    assert out.startswith("**Teaser playlist**\n• **Teaser:** ")
    assert "2026-11-14 | Zed Fox" in out
    assert out.rstrip("\n").splitlines()[-1] == "NOT SENT: delivery disabled"
    assert_clean_stdout(out, err)


def test_teaser_with_description_reaches_spotify(cli_env: Services, monkeypatch, capsys) -> None:
    argv = ["teaser", "--artist", "Zed Fox", "--date", "2026-11-14", "--description", "Custom", "--no-send", "--json"]
    code, out, _ = run(monkeypatch, capsys, argv)
    rec = one_json_line(out)
    assert code == 0 and rec["ok"] is True
    assert cli_env.spotify.created[0]["description"] == "Custom"


def test_setlist_find_json(cli_env: Services, monkeypatch, capsys) -> None:
    argv = ["setlist-find", "--artist", "Zed Fox", "--date", "2026-11-14", "--venue", "The Quarry", "--json"]
    code, out, _ = run(monkeypatch, capsys, argv)
    rec = one_json_line(out)
    assert code == 0
    assert rec["found"] is False and rec["match"] == "none"
    assert rec["search_url"].startswith("https://www.setlist.fm/search?query=")
    assert "sent" in rec


def test_setlist_find_songs_adds_the_list_to_the_record(cli_env: Services, monkeypatch, capsys) -> None:
    cli_env.setlistfm.responses[("Zed Fox", "14-11-2026", None)] = {
        "setlist": [
            {
                "id": "s1",
                "url": "https://www.setlist.fm/setlist/zed-fox/2026/s1.html",
                "artist": {"name": "Zed Fox"},
                "eventDate": "14-11-2026",
                "venue": {"name": "The Quarry", "city": {"name": "San Francisco"}},
                "sets": {"set": [{"song": [{"name": "First Light"}, {"name": "Second Wind"}]}]},
            }
        ]
    }
    argv = ["setlist-find", "--artist", "Zed Fox", "--date", "2026-11-14", "--songs", "--json"]
    code, out, _ = run(monkeypatch, capsys, argv)
    rec = one_json_line(out)
    assert code == 0 and rec["found"] is True
    assert rec["songs"] == ["First Light", "Second Wind"]


def test_queue_json(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, err = run(monkeypatch, capsys, ["queue", "--json"])
    rec = one_json_line(out)
    assert code == 0
    assert rec["count"] == 0 and rec["entries"] == []
    assert_clean_stdout(out, err)


def test_queue_text(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["queue", "--no-send"])
    assert code == 0
    assert out.startswith("The setlist queue is empty.\n")


# --- healthcheck / config / migrate ----------------------------------------------------------------------------------


def test_healthcheck_exit_code_is_the_fail_count(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, err = run(monkeypatch, capsys, ["healthcheck"])
    rows = [ln for ln in out.splitlines() if HEALTH_ROW.match(ln)]
    assert rows, out
    fails = [ln for ln in rows if ln.startswith("FAIL")]
    assert code == len(fails) >= 1
    assert "FAIL  gws-keyfile" in out
    assert "README.md#" in out
    assert_clean_stdout(out, err)


def test_healthcheck_json(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["healthcheck", "--json"])
    rec = one_json_line(out)
    assert code == rec["failures"] == sum(1 for c in rec["checks"] if c["status"] == "FAIL")
    assert rec["ok"] is False


def test_healthcheck_only(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["healthcheck", "--only", "python"])
    assert code == 0
    assert out.startswith("PASS  python")


def test_config_marks_paths_and_never_prints_a_secret(cli_env: Services, tmp_path: Path, monkeypatch, capsys) -> None:
    secret = "SECRET-DO-NOT-PRINT-7f3a"
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "spotify.json").write_text(json.dumps({"client_id": "id", "client_secret": secret, "refresh_token": secret}))
    code, out, err = run(monkeypatch, capsys, ["config"])
    assert code == 0
    assert secret not in out and secret not in err
    assert re.search(r"^spotify_credentials\s+EXISTS\s", out, re.M)
    assert re.search(r"^setlistfm_credentials\s+MISSING\s", out, re.M)
    assert_clean_stdout(out, err)


def test_config_json(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["config", "--json"])
    rows = one_json_line(out)
    assert code == 0
    assert {r["field"] for r in rows} >= {"config_dir", "spotify_credentials", "operator_name"}
    assert all(set(r) == {"field", "value", "status"} for r in rows)


def test_migrate_dry_run_lists_no_legacy_rows(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, err = run(monkeypatch, capsys, ["migrate", "--dry-run"])
    assert code == 0
    lines = out.rstrip("\n").splitlines()
    assert len(lines) == 4
    assert all(ln.startswith("skipped: no legacy") and " -> " in ln for ln in lines)
    assert_clean_stdout(out, err)


def test_migrate_dry_run_would_copy(cli_env: Services, tmp_path: Path, monkeypatch, capsys) -> None:
    legacy = tmp_path / ".config" / "ari-spotify"
    legacy.mkdir(parents=True)
    (legacy / "setlistfm.json").write_text('{"api_key": "k"}')
    code, out, _ = run(monkeypatch, capsys, ["migrate", "--dry-run"])
    assert code == 0
    assert "would copy" in out
    assert not (tmp_path / "config" / "setlistfm.json").exists()


# --- spotify ---------------------------------------------------------------------------------------------------------


def test_spotify_playlists_legacy_record(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, err = run(monkeypatch, capsys, ["spotify", "playlists"])
    rec = one_json_line(out)
    assert code == 0
    assert set(rec) == {"count", "playlists"} and rec["count"] == 0
    assert_clean_stdout(out, err)


def test_spotify_unconfigured_is_exit_1_on_stderr(cli_env: Services, monkeypatch, capsys) -> None:
    cli_env.spotify = None
    code, out, err = run(monkeypatch, capsys, ["spotify", "me"])
    assert code == 1
    assert out == ""
    assert "[ERROR]" in err and "Traceback" not in err


def test_spotify_playlist_add_bad_uri_is_a_fix(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["spotify", "playlist-add", "--playlist", "pl1", "--uri", "nope"])
    assert code == 1
    assert out.startswith("FIX: ")


# --- serve / argparse errors / stdout discipline ---------------------------------------------------------------------


def test_no_subcommand_serves(cli_env: Services, monkeypatch, capsys) -> None:
    from concert_mcp import server

    calls: list[str] = []
    monkeypatch.setattr(server, "main", lambda: calls.append("served"))
    code, out, _ = run(monkeypatch, capsys, [])
    assert code == 0 and calls == ["served"]
    assert out == ""


def test_serve_applies_config_dir_to_the_environment(cli_env: Services, tmp_path: Path, monkeypatch, capsys) -> None:
    import os

    from concert_mcp import server

    seen: list[str | None] = []
    monkeypatch.setattr(server, "main", lambda: seen.append(os.environ.get("CONCERT_MCP_CONFIG_DIR")))
    code, _, _ = run(monkeypatch, capsys, ["--config-dir", str(tmp_path / "elsewhere"), "serve"])
    assert code == 0 and seen == [str(tmp_path / "elsewhere")]


@pytest.mark.parametrize(
    "argv",
    [
        ["teaser", "--artist", "Zed Fox"],
        ["bogus-command"],
        ["spotify"],
        ["queue", "--what"],
        ["--config-dir"],
    ],
)
def test_argparse_errors_are_fix_lines_not_usage(cli_env: Services, monkeypatch, capsys, argv: list[str]) -> None:
    code, out, err = run(monkeypatch, capsys, argv)
    assert code == 1
    assert out.startswith("FIX: ") and out.count("\n") == 1
    assert "usage:" not in out and "Traceback" not in err


def test_argparse_error_json(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["--json", "teaser", "--artist", "Zed Fox"])
    rec = one_json_line(out)
    assert code == 1 and rec["ok"] is False and rec["retry"] is True


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0


def test_crash_is_one_stderr_line_and_exit_1(cli_env: Services, monkeypatch, capsys) -> None:
    def boom(settings=None):
        raise RuntimeError("wiring broke")

    monkeypatch.setattr(services, "build_services", boom)
    code, out, err = run(monkeypatch, capsys, ["queue"])
    assert code == 1 and out == ""
    assert err.strip() == "[ERROR] RuntimeError: wiring broke"


def test_keyboard_interrupt_is_130(cli_env: Services, monkeypatch, capsys) -> None:
    def interrupted(settings=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(services, "build_services", interrupted)
    code, out, _ = run(monkeypatch, capsys, ["queue"])
    assert code == 130 and out == ""


def test_bad_log_level_is_not_a_traceback(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, err = run(monkeypatch, capsys, ["--log-level", "loud", "queue"])
    assert code == 1 and out == ""
    assert err.startswith("[ERROR] ValueError:")


def test_build_parser_subcommands() -> None:
    parser = cli.build_parser()
    args, extras = parser.parse_known_args(["add", "--concert", "--title", "X", "--json"])
    assert args.command == "add" and args.concert is True and args.json_mode is True
    assert extras == ["--title", "X"]
    args, extras = parser.parse_known_args(["--json", "spotify", "liked", "--artist", "X", "--dry-run"])
    assert args.spotify_command == "liked" and args.json_mode is True and args.dry_run is True
    assert extras == []
