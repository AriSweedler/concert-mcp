"""GwsCalendar on a FakeRunner: the legacy argv, the keychain guard, and every failure mapped to a ProviderError."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from concert_mcp.adapters.gws import GwsCalendar
from concert_mcp.config import Settings
from concert_mcp.result import ProviderError
from fakes import FakeRunner

PDT = timezone(timedelta(hours=-7))
START = datetime(2026, 11, 14, 20, 30, tzinfo=PDT)
END = START + timedelta(hours=2)

REFUSED = "Google Calendar refused the request. Admin: check `gws auth` on the host."
REFUSED_DELETE = "Google Calendar refused the delete. Admin: check `gws auth` on the host."
KEYCHAIN = (
    "The login keychain is locked in this session, so I did not touch Google Calendar. "
    "Admin: create the gws file keyring key (README.md#google-calendar) or run this from a session with keychain access."
)
MISSING = "The calendar tool is not reachable right now. Try again in a minute."
NOJSON = "Google Calendar gave an unreadable answer. Admin: check the host."


def gws_calls(runner: FakeRunner, settings: Settings) -> list[tuple[tuple[str, ...], dict | None, object]]:
    return [c for c in runner.calls if c[0][0] == settings.gws_bin]


def security_calls(runner: FakeRunner) -> list[tuple[tuple[str, ...], dict | None, object]]:
    return [c for c in runner.calls if c[0][0] == "security"]


def make(settings: Settings, runner: FakeRunner, *, platform: str = "linux", key_file: bool = True) -> GwsCalendar:
    if key_file:
        settings.gws_key_file.write_text("k", encoding="utf-8")
    return GwsCalendar(settings, runner, platform=platform, environ={"PATH": "/usr/bin", "HOME": "/nonexistent"})


# --- env() and the keychain guard --------------------------------------------------------------------------------


def test_env_has_file_backend_only_when_key_file_exists(settings: Settings) -> None:
    runner = FakeRunner()
    cal = make(settings, runner, key_file=False)
    assert "GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND" not in cal.env()
    assert cal.env()["PATH"] == "/usr/bin"

    settings.gws_key_file.write_text("k", encoding="utf-8")
    assert cal.env()["GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND"] == "file"
    assert cal.env()["PATH"] == "/usr/bin"


def test_env_does_not_mutate_the_given_environ(settings: Settings) -> None:
    environ = {"PATH": "/usr/bin"}
    settings.gws_key_file.write_text("k", encoding="utf-8")
    cal = GwsCalendar(settings, FakeRunner(), platform="linux", environ=environ)
    assert cal.env()["GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND"] == "file"
    assert environ == {"PATH": "/usr/bin"}


def test_darwin_without_key_file_and_locked_keychain_refuses_before_gws_runs(settings: Settings) -> None:
    runner = FakeRunner({("security",): (1, "", "SecKeychainCopySettings: User interaction is not allowed.")})
    cal = make(settings, runner, platform="darwin", key_file=False)
    with pytest.raises(ProviderError) as ei:
        cal.list_calendars()
    assert ei.value.provider == "gws-keychain"
    assert ei.value.error == "login keychain not reachable from this session; gws would delete its credentials"
    assert ei.value.reply == KEYCHAIN
    assert gws_calls(runner, settings) == []
    argv = security_calls(runner)[0][0]
    assert argv[:2] == ("security", "show-keychain-info")
    assert argv[2].endswith("Library/Keychains/login.keychain-db")


def test_darwin_with_reachable_keychain_runs_gws(settings: Settings) -> None:
    runner = FakeRunner(
        {
            ("security",): (0, "", ""),
            (settings.gws_bin, "calendar", "calendarList", "list"): (0, json.dumps({"items": [{"id": "c1"}]}), ""),
        }
    )
    cal = make(settings, runner, platform="darwin", key_file=False)
    assert cal.list_calendars() == [{"id": "c1"}]
    assert len(security_calls(runner)) == 1
    assert len(gws_calls(runner, settings)) == 1


def test_darwin_with_key_file_skips_the_keychain_check(settings: Settings) -> None:
    runner = FakeRunner({(settings.gws_bin,): (0, json.dumps({"items": []}), "")})
    cal = make(settings, runner, platform="darwin", key_file=True)
    assert cal.list_calendars() == []
    assert security_calls(runner) == []
    _, env, _ = gws_calls(runner, settings)[0]
    assert env is not None and env["GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND"] == "file"


def test_linux_never_calls_security(settings: Settings) -> None:
    runner = FakeRunner({(settings.gws_bin,): (0, json.dumps({"items": []}), "")})
    cal = make(settings, runner, platform="linux", key_file=False)
    assert cal.keychain_reachable() is True
    assert cal.list_calendars() == []
    assert security_calls(runner) == []


def test_keychain_guard_runs_on_every_call(settings: Settings) -> None:
    runner = FakeRunner(
        {
            ("security",): (0, "", ""),
            (settings.gws_bin,): (0, json.dumps({"items": []}), ""),
        }
    )
    cal = make(settings, runner, platform="darwin", key_file=False)
    cal.list_calendars()
    cal.list_calendars()
    assert len(security_calls(runner)) == 2


# --- the argv of each call -------------------------------------------------------------------------------------------


def test_list_calendars_argv_and_items(settings: Settings) -> None:
    answer = {"kind": "calendar#calendarList", "items": [{"id": "c1", "summary": "Concerts"}]}
    runner = FakeRunner({(settings.gws_bin,): (0, json.dumps(answer), "keyring chatter on stderr")})
    cal = make(settings, runner)
    assert cal.list_calendars() == [{"id": "c1", "summary": "Concerts"}]
    argv, env, cwd = gws_calls(runner, settings)[0]
    assert list(argv) == [settings.gws_bin, "calendar", "calendarList", "list"]
    assert cwd is None
    assert env is not None and env["GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND"] == "file"


def test_list_calendars_non_dict_answer_is_empty(settings: Settings) -> None:
    runner = FakeRunner({(settings.gws_bin,): (0, json.dumps([1, 2]), "")})
    assert make(settings, runner).list_calendars() == []
    runner = FakeRunner({(settings.gws_bin,): (0, json.dumps({"items": "nope"}), "")})
    assert make(settings, runner).list_calendars() == []


def test_list_events_params_json(settings: Settings) -> None:
    runner = FakeRunner({(settings.gws_bin,): (0, json.dumps({"items": [{"id": "e1"}]}), "")})
    cal = make(settings, runner)
    t_min = START
    t_max = START + timedelta(minutes=1)
    assert cal.list_events("cal-concerts", time_min=t_min, time_max=t_max, q="Zed Fox") == [{"id": "e1"}]
    argv = list(gws_calls(runner, settings)[0][0])
    assert argv[:5] == [settings.gws_bin, "calendar", "events", "list", "--params"]
    assert len(argv) == 6
    params = json.loads(argv[5])
    assert params == {
        "calendarId": "cal-concerts",
        "timeMin": t_min.isoformat(),
        "timeMax": t_max.isoformat(),
        "singleEvents": True,
        "maxResults": 50,
        "q": "Zed Fox",
    }
    assert params["singleEvents"] is True


def test_list_events_max_results_and_empty_q(settings: Settings) -> None:
    runner = FakeRunner({(settings.gws_bin,): (0, json.dumps({}), "")})
    cal = make(settings, runner)
    assert cal.list_events("primary", time_min=START, time_max=END, max_results=7) == []
    params = json.loads(gws_calls(runner, settings)[0][0][5])
    assert params["maxResults"] == 7
    assert params["q"] == ""


def test_get_event_argv(settings: Settings) -> None:
    runner = FakeRunner({(settings.gws_bin,): (0, json.dumps({"id": "e1", "summary": "Zed Fox"}), "")})
    cal = make(settings, runner)
    assert cal.get_event("cal-concerts", "e1") == {"id": "e1", "summary": "Zed Fox"}
    argv = list(gws_calls(runner, settings)[0][0])
    assert argv[:5] == [settings.gws_bin, "calendar", "events", "get", "--params"]
    assert json.loads(argv[5]) == {"calendarId": "cal-concerts", "eventId": "e1"}


def test_insert_event_exact_argv_and_parsed_json(settings: Settings) -> None:
    created = {"id": "evt1", "htmlLink": "https://www.google.com/calendar/event?eid=abc"}
    runner = FakeRunner({(settings.gws_bin,): (0, json.dumps(created), "")})
    cal = make(settings, runner)
    out = cal.insert_event(
        "cal-concerts",
        summary="Zed Fox",
        start=START,
        end=END,
        location="The Quarry, San Francisco",
        description="Doors: 7:30 PM\nOpener: Zork.",
        attendees=["a@example.com", "b@example.com"],
    )
    assert out == created
    argv = list(gws_calls(runner, settings)[0][0])
    assert argv == [
        settings.gws_bin,
        "calendar",
        "+insert",
        "--calendar",
        "cal-concerts",
        "--summary",
        "Zed Fox",
        "--start",
        START.isoformat(),
        "--end",
        END.isoformat(),
        "--location",
        "The Quarry, San Francisco",
        "--description",
        "Doors: 7:30 PM\nOpener: Zork.",
        "--attendee",
        "a@example.com",
        "--attendee",
        "b@example.com",
    ]


def test_insert_event_without_description_or_attendees(settings: Settings) -> None:
    runner = FakeRunner({(settings.gws_bin,): (0, json.dumps({"id": "evt1"}), "")})
    cal = make(settings, runner)
    cal.insert_event(
        "primary",
        summary="[Dinner] ZZZ Bistro",
        start=START,
        end=END,
        location="ZZZ Bistro",
        description=None,
        attendees=[],
    )
    argv = list(gws_calls(runner, settings)[0][0])
    assert "--description" not in argv
    assert "--attendee" not in argv
    assert argv[-2:] == ["--location", "ZZZ Bistro"]

    runner = FakeRunner({(settings.gws_bin,): (0, json.dumps({"id": "evt2"}), "")})
    cal = make(settings, runner)
    cal.insert_event("primary", summary="x", start=START, end=END, location="y", description="", attendees=[])
    assert "--description" not in gws_calls(runner, settings)[0][0]


def test_patch_event_passes_only_the_given_fields_as_json(settings: Settings) -> None:
    runner = FakeRunner({(settings.gws_bin,): (0, "", "")})
    cal = make(settings, runner)
    assert cal.patch_event("cal-concerts", "e1", description="Doors: 7:30 PM\nTeaser playlist: https://x") is None
    argv = list(gws_calls(runner, settings)[0][0])
    assert argv[:5] == [settings.gws_bin, "calendar", "events", "patch", "--params"]
    assert json.loads(argv[5]) == {"calendarId": "cal-concerts", "eventId": "e1"}
    assert argv[6] == "--json"
    assert json.loads(argv[7]) == {"description": "Doors: 7:30 PM\nTeaser playlist: https://x"}
    assert len(argv) == 8


def test_patch_event_sends_summary_times_and_location(settings: Settings) -> None:
    runner = FakeRunner({(settings.gws_bin,): (0, "", "")})
    cal = make(settings, runner)
    cal.patch_event("cal-concerts", "e1", summary="Zed Fox", start=START, end=END, location="The Quarry")
    argv = list(gws_calls(runner, settings)[0][0])
    assert json.loads(argv[7]) == {
        "summary": "Zed Fox",
        "start": {"dateTime": START.isoformat()},
        "end": {"dateTime": END.isoformat()},
        "location": "The Quarry",
    }


def test_patch_event_with_nothing_to_change_runs_no_command(settings: Settings) -> None:
    runner = FakeRunner({(settings.gws_bin,): (0, "", "")})
    cal = make(settings, runner)
    cal.patch_event("cal-concerts", "e1")
    assert gws_calls(runner, settings) == []


def test_delete_event_runs_in_a_temp_cwd_that_is_removed(settings: Settings) -> None:
    seen: dict[str, object] = {}

    def fake_delete(argv: list[str], kw: dict) -> object:
        from concert_mcp.proc import RunResult

        cwd = Path(str(kw["cwd"]))
        seen["cwd"] = cwd
        seen["existed"] = cwd.is_dir()
        (cwd / "gws-del.out").write_text("", encoding="utf-8")  # what gws does with -o
        return RunResult(0, "", "")

    runner = FakeRunner({(settings.gws_bin,): fake_delete})
    cal = make(settings, runner)
    assert cal.delete_event("cal-concerts", "e1") is None
    argv = list(gws_calls(runner, settings)[0][0])
    assert argv[:7] == [settings.gws_bin, "calendar", "events", "delete", "-o", "gws-del.out", "--params"]
    assert json.loads(argv[7]) == {"calendarId": "cal-concerts", "eventId": "e1"}
    assert len(argv) == 8
    cwd = seen["cwd"]
    assert isinstance(cwd, Path)
    assert seen["existed"] is True
    assert cwd.name.startswith("concert-mcp-gws-")
    assert not cwd.exists()


def test_delete_event_cleans_up_when_gws_fails(settings: Settings) -> None:
    seen: dict[str, Path] = {}

    def fake_delete(argv: list[str], kw: dict) -> object:
        from concert_mcp.proc import RunResult

        seen["cwd"] = Path(str(kw["cwd"]))
        return RunResult(1, "", "Error: 404 Not Found")

    runner = FakeRunner({(settings.gws_bin,): fake_delete})
    cal = make(settings, runner)
    with pytest.raises(ProviderError) as ei:
        cal.delete_event("cal-concerts", "gone")
    assert ei.value.provider == "gws"
    assert ei.value.error == "gws calendar events exited 1: Error: 404 Not Found"
    assert ei.value.reply == REFUSED_DELETE
    assert not seen["cwd"].exists()


# --- failures ----------------------------------------------------------------------------------------------------


def test_nonzero_exit_is_provider_error_with_legacy_wording(settings: Settings) -> None:
    runner = FakeRunner({(settings.gws_bin,): (1, "", "Error: invalid_grant\nToken has been expired or revoked.")})
    cal = make(settings, runner)
    with pytest.raises(ProviderError) as ei:
        cal.list_events("primary", time_min=START, time_max=END)
    assert ei.value.provider == "gws"
    assert ei.value.error == "gws calendar events exited 1: Error: invalid_grant\nToken has been expired or revoked."
    assert ei.value.reply == REFUSED


def test_nonzero_exit_tail_falls_back_to_stdout_and_is_capped(settings: Settings) -> None:
    runner = FakeRunner({(settings.gws_bin,): (2, "x" * 400, "")})
    cal = make(settings, runner)
    with pytest.raises(ProviderError) as ei:
        cal.list_calendars()
    assert ei.value.error == "gws calendar calendarList exited 2: " + "x" * 300
    assert ei.value.reply == REFUSED


def test_insert_failure_says_request_not_delete(settings: Settings) -> None:
    runner = FakeRunner({(settings.gws_bin,): (1, "", "boom")})
    cal = make(settings, runner)
    with pytest.raises(ProviderError) as ei:
        cal.insert_event("primary", summary="x", start=START, end=END, location="y", description=None, attendees=[])
    assert ei.value.error == "gws calendar +insert exited 1: boom"
    assert ei.value.reply == REFUSED


def test_runner_error_is_gws_missing(settings: Settings) -> None:
    from concert_mcp.proc import RunResult

    runner = FakeRunner({(settings.gws_bin,): lambda argv, kw: RunResult(-1, "", "", f"binary not found: {argv[0]}")})
    cal = make(settings, runner)
    with pytest.raises(ProviderError) as ei:
        cal.list_calendars()
    assert ei.value.provider == "gws-missing"
    assert ei.value.error == f"gws did not run: binary not found: {settings.gws_bin}"
    assert ei.value.reply == MISSING


def test_timeout_is_gws_missing(settings: Settings) -> None:
    from concert_mcp.proc import RunResult

    runner = FakeRunner({(settings.gws_bin,): lambda argv, kw: RunResult(-1, "", "", "timed out after 60 s")})
    cal = make(settings, runner)
    with pytest.raises(ProviderError) as ei:
        cal.get_event("primary", "e1")
    assert ei.value.provider == "gws-missing"
    assert ei.value.error == "gws did not run: timed out after 60 s"


def test_not_json_is_gws_nojson(settings: Settings) -> None:
    runner = FakeRunner({(settings.gws_bin,): (0, "not json", "")})
    cal = make(settings, runner)
    with pytest.raises(ProviderError) as ei:
        cal.insert_event("primary", summary="x", start=START, end=END, location="y", description=None, attendees=[])
    assert ei.value.provider == "gws-nojson"
    assert ei.value.error == "gws returned no JSON"
    assert ei.value.reply == NOJSON


def test_raw_returns_stdout_only_and_passes_timeout(settings: Settings) -> None:
    seen: dict[str, object] = {}

    def fake(argv: list[str], kw: dict) -> object:
        from concert_mcp.proc import RunResult

        seen["timeout_s"] = kw["timeout_s"]
        return RunResult(0, "stdout text", "keyring: unlocked")

    runner = FakeRunner({(settings.gws_bin,): fake})
    cal = make(settings, runner)
    assert cal.raw("calendar", "events", "patch") == "stdout text"
    assert seen["timeout_s"] == settings.gws_timeout_s
