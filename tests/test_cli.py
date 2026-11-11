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
from concert_mcp.flows.services import Services
from concert_mcp.result import FixInput
from conftest import assert_clean_stdout, one_json_line, run

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


def test_read_request_flags_fold_aliases_and_repeat_lists() -> None:
    ev = cli.read_request(
        ["--artist", "Zed Fox", "--description", "Doors: 7:30 PM", "--notes", "Seat: GA", "--attendees", "a@b.c"],
        io.StringIO(""),
        known=KNOWN,
        aliases=ALIASES,
        json_hint=cli.ADD_JSON_HINT,
    )
    assert ev == {"title": "Zed Fox", "description": ["Doors: 7:30 PM", "Seat: GA"], "attendees": ["a@b.c"]}


def test_read_request_missing_value() -> None:
    with pytest.raises(FixInput) as e:
        cli.read_request(["--title"], io.StringIO(""), known=KNOWN, aliases=ALIASES, json_hint="h")
    assert (e.value.error, e.value.fix) == ("--title needs a value", "pass a value after --title")


def test_read_request_bad_json_and_non_object() -> None:
    with pytest.raises(FixInput) as e:
        cli.read_request([], io.StringIO("not json"), known=KNOWN, aliases=ALIASES, json_hint=cli.ADD_JSON_HINT)
    assert e.value.error.startswith("input is not valid JSON: ")
    assert e.value.fix == cli.ADD_JSON_HINT
    with pytest.raises(FixInput) as e:
        cli.read_request(["[1, 2]"], io.StringIO(""), known=KNOWN, aliases=ALIASES, json_hint="h")
    assert (e.value.error, e.value.fix) == ("input must be a JSON object", "send one {...} object")


# --- add -------------------------------------------------------------------------------------------------------------


def test_add_same_record_from_flags_stdin_and_aliased_positional(cli_env: Services, monkeypatch, capsys) -> None:
    _, out_flags, _ = run(monkeypatch, capsys, ADD_FLAGS)
    stdin = json.dumps({"title": "Zed Fox", "start": "2026-11-14 20:30", "location": "The Quarry, San Francisco"})
    _, out_stdin, _ = run(monkeypatch, capsys, ["add", "--concert", "--dry-run", "--no-send", "--json"], stdin)
    positional = json.dumps({"artist": "Zed Fox", "datetime": "2026-11-14 20:30", "venue": "The Quarry, San Francisco"})
    _, out_pos, _ = run(monkeypatch, capsys, ["--json", "--dry-run", "--no-send", "add", "--concert", positional])
    assert one_json_line(out_flags) == one_json_line(out_stdin) == one_json_line(out_pos)


def test_add_unknown_flag_json_is_the_retry_record(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["add", "--json", "--bogus", "x"])
    rec = one_json_line(out)
    assert code == 1
    assert rec["ok"] is False and rec["retry"] is True
    assert rec["error"] == "unknown flag --bogus"
    assert rec["fix"].startswith("allowed flags: --allowPast, --another, --attendees, --calendar")
    assert "sent" in rec


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


def test_del_invalid_json_has_its_own_hint(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["del"], "nope")
    assert code == 1
    assert out == f"FIX: {cli.DEL_JSON_HINT}\n"


# --- teaser / setlist-find / queue -----------------------------------------------------------------------------------


def test_teaser_prints_the_standalone_block_and_not_sent(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, err = run(monkeypatch, capsys, ["teaser", "--artist", "Zed Fox", "--date", "2026-11-14", "--no-send"])
    assert code == 0
    assert out.startswith("**Teaser playlist**\n• [Teaser playlist](")
    assert ", 3 liked songs" in out
    assert out.rstrip("\n").splitlines()[-1] == "NOT SENT: delivery disabled"
    assert_clean_stdout(out, err)


def test_setlist_find_json(cli_env: Services, monkeypatch, capsys) -> None:
    argv = ["setlist-find", "--artist", "Zed Fox", "--date", "2026-11-14", "--venue", "The Quarry", "--json"]
    code, out, _ = run(monkeypatch, capsys, argv)
    rec = one_json_line(out)
    assert code == 0
    assert rec["found"] is False and rec["match"] == "none"
    assert rec["search_url"].startswith("https://www.setlist.fm/search?query=")
    assert "sent" in rec


def test_setlist_playlist_dry_run_needs_no_provider(cli_env: Services, monkeypatch, capsys) -> None:
    cli_env.setlistfm = None
    cli_env.spotify = None
    url = "https://www.setlist.fm/setlist/zed-fox/2026/the-quarry-san-francisco-ca-3bd6f4a.html"
    code, out, _ = run(monkeypatch, capsys, ["--dry-run", "setlist-playlist", "--url", url, "--date", "2026-11-14"])
    assert code == 0
    assert out == f"**Setlist playlist** would make 2026-11-14 | Zed Fox from the songs at {url}\nNOT SENT: dry run\n"


def test_poll_text_and_json(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, err = run(monkeypatch, capsys, ["poll"])
    assert code == 0
    assert out.endswith("\nThe setlist queue is empty.\n") and out.startswith("**Setlist poll** ")
    assert_clean_stdout(out, err)
    code, out, _ = run(monkeypatch, capsys, ["poll"])
    assert code == 0 and out.startswith("**Setlist poll** ") and "(last: 20" in out
    code, out, _ = run(monkeypatch, capsys, ["poll", "--json"])
    rec = one_json_line(out)
    assert code == 0 and rec["ok"] is True and rec["ran"] is True and rec["rows"] == []
    assert rec["sent"] == "NOT SENT: the poll report stays on the terminal"
    code, out, _ = run(monkeypatch, capsys, ["queue"])
    assert code == 0 and "\nLast poll: 20" in out


# --- healthcheck / config / migrate ----------------------------------------------------------------------------------


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


# --- spotify ---------------------------------------------------------------------------------------------------------


def test_spotify_unconfigured_is_exit_1_on_stderr(cli_env: Services, monkeypatch, capsys) -> None:
    cli_env.spotify = None
    code, out, err = run(monkeypatch, capsys, ["spotify", "me"])
    assert code == 1
    assert out == ""
    assert "[ERROR]" in err and "Traceback" not in err


# --- serve / argparse errors / stdout discipline ---------------------------------------------------------------------


def test_serve_applies_config_dir_to_the_environment(cli_env: Services, tmp_path: Path, monkeypatch, capsys) -> None:
    import os

    from concert_mcp import server

    seen: list[str | None] = []
    monkeypatch.setattr(server, "main", lambda: seen.append(os.environ.get("CONCERT_MCP_CONFIG_DIR")))
    code, _, _ = run(monkeypatch, capsys, ["--config-dir", str(tmp_path / "elsewhere"), "serve"])
    assert code == 0 and seen == [str(tmp_path / "elsewhere")]


def test_argparse_errors_are_fix_lines_not_usage(cli_env: Services, monkeypatch, capsys) -> None:
    for argv in [
        ["teaser", "--artist", "Zed Fox"],
        ["bogus-command"],
        ["spotify"],
        ["queue", "--what"],
        ["--config-dir"],
    ]:
        code, out, err = run(monkeypatch, capsys, argv)
        assert code == 1
        assert out.startswith("FIX: ") and out.count("\n") == 1
        assert "usage:" not in out and "Traceback" not in err


def test_argparse_error_json(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["--json", "teaser", "--artist", "Zed Fox"])
    rec = one_json_line(out)
    assert code == 1 and rec["ok"] is False and rec["retry"] is True


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


def test_venues_text_and_json(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["venues"])
    assert code == 0 and out.startswith("No venues yet")
    code, out, _ = run(monkeypatch, capsys, ["venues", "--json"])
    assert code == 0
    rec = json.loads(out)
    assert rec["count"] == 0 and rec["venues"] == []


def test_lineup_without_a_key_is_a_fix_line(cli_env: Services, monkeypatch, capsys) -> None:
    code = cli.main(["lineup", "--artist", "Zed Fox", "--date", "2026-11-14"])
    out = capsys.readouterr().out
    assert code == 1
    assert out.strip() == "FIX: Gemini is not set up: run `concert-mcp gemini auth` on the host, then ask again"
