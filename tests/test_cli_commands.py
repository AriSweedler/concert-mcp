"""The CLI subcommands test_cli.py does not reach: try-image, failures, gemini, venues, the spotify records, and the
teaser and setlist-find variants with --description and --songs. Fakes everywhere; the model is a stub."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from concert_mcp.adapters import chat_model_http, openclaw
from concert_mcp.flows import auth, playlists
from concert_mcp.flows.services import Services
from concert_mcp.result import ProviderError
from conftest import one_json_line, run
from fakes import FakeSetlistFm, FakeSpotify, FakeVenueLookup
from test_failures import OCR, T0, assistant, shot
from test_flow_setlist import api_row
from test_flow_venues import HIT

pytest.importorskip("mcp")


class StubModel:
    """ChatModelClient as try-image constructs it: (base_url, model); one canned tool call."""

    answer: tuple[str | None, dict[str, Any], str] = ("healthcheck", {}, "")

    def __init__(self, base_url: str, model: str) -> None:
        self.at = (base_url, model)

    def first_tool_call(self, **kw: Any) -> tuple[str | None, dict[str, Any], str]:
        return self.answer

    def complete(self, system: str, user: str, **kw: Any) -> str:
        return "it was a movie"


@pytest.fixture
def openclaw_json(cli_env: Services, tmp_path: Path) -> Path:
    """OpenClaw's config with a model, an OCR command (a script that echoes) and a tool filter."""
    ocr = tmp_path / "ocr"
    ocr.write_text('#!/bin/sh\necho "OCR of $1"\n', encoding="utf-8")
    ocr.chmod(0o700)
    cfg = cli_env.settings.openclaw_config
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"model": {"primary": "llama/qwen"}}},
                "models": {"providers": {"llama": {"baseUrl": "http://model/v1"}}},
                "tools": {"media": {"models": [{"command": str(ocr)}]}},
                "mcp": {"servers": {"concert": {"toolFilter": {"include": ["healthcheck", "queue_status"]}}}},
            }
        )
    )
    return cfg


# ---------- try-image ----------


def test_try_image_from_a_text_file(
    cli_env: Services, openclaw_json: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(chat_model_http, "ChatModelClient", StubModel)
    text = tmp_path / "ocr.txt"
    text.write_text("Godzilla 7pm", encoding="utf-8")
    code, out, _ = run(monkeypatch, capsys, ["try-image", "--text", str(text), "--caption", "a movie"])
    assert code == 0 and "== Model called healthcheck" in out and "Approve or deny" in out
    code, out, _ = run(monkeypatch, capsys, ["try-image", "--text", str(text), "--json", "--tools", "healthcheck"])
    assert code == 0 and json.loads(out)["tool"] == "healthcheck"


def test_try_image_runs_the_ocr_command_and_the_tee_rule(
    cli_env: Services, openclaw_json: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(chat_model_http, "ChatModelClient", StubModel)
    shot_path = tmp_path / "shot.jpg"
    shot_path.write_bytes(b"\xff\xd8")
    code, out, _ = run(monkeypatch, capsys, ["try-image", str(shot_path)])
    assert code == 0 and f"OCR of {shot_path}" in out
    # an explicit --ocr-cmd ending in -tee runs the command beside it when that exists
    tee = tmp_path / "ocr-tee"
    tee.write_text("#!/bin/sh\necho NEVER\n", encoding="utf-8")
    tee.chmod(0o700)
    (tmp_path / "ocr").write_text('#!/bin/sh\necho "plain $1"\n', encoding="utf-8")
    code, out, _ = run(monkeypatch, capsys, ["try-image", str(shot_path), "--ocr-cmd", str(tee)])
    assert code == 0 and "plain" in out and "NEVER" not in out


def test_try_image_fix_lines(cli_env: Services, openclaw_json: Path, tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(chat_model_http, "ChatModelClient", StubModel)
    code, out, _ = run(monkeypatch, capsys, ["try-image"])
    assert code == 1 and out.startswith("FIX: pass a screenshot path")
    cfg = cli_env.settings.openclaw_config
    cfg.write_text("{}")
    code, out, _ = run(monkeypatch, capsys, ["try-image", "--text", str(cfg)])
    assert code == 1 and out.startswith("FIX: pass --model-url and --model")
    code, out, _ = run(monkeypatch, capsys, ["try-image", str(cfg), "--model-url", "http://m", "--model", "q"])
    assert code == 1 and out.startswith("FIX: pass --ocr-cmd")


# ---------- failures ----------


def test_failures_collect_list_replay(
    cli_env: Services, openclaw_json: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(chat_model_http, "ChatModelClient", StubModel)
    rows = [
        ("s1", T0, shot("e1", str(tmp_path / "gone.jpg"))),
        ("s1", T0 + 63_000, assistant("error", "Connection error.")),
    ]
    monkeypatch.setattr(openclaw.OpenClawContext, "transcript_since", lambda self, since: rows)
    code, out, _ = run(monkeypatch, capsys, ["failures", "collect", "--explain"])
    folder = Path(out.strip())
    assert code == 0 and folder.name == str(T0 // 1000)
    assert (folder / "orin-explanation.txt").read_text() == "it was a movie\n"
    code, out, _ = run(monkeypatch, capsys, ["failures", "list"])
    assert code == 0 and out.startswith(f"{folder.name}  Connection error.  ")
    code, out, _ = run(monkeypatch, capsys, ["failures", "replay", folder.name])
    assert code == 0 and OCR.splitlines()[0] in out and "== Model called healthcheck" in out
    code, out, _ = run(monkeypatch, capsys, ["failures", "replay", "nope"])
    assert code == 1 and out.startswith("FIX: pass a name from")


# ---------- gemini, venues ----------


def test_gemini_auth_command(cli_env: Services, monkeypatch, capsys) -> None:
    monkeypatch.setattr(auth, "gemini_auth", lambda settings, state, *, manual, force: {"ok": True, "manual": manual})
    code, out, _ = run(monkeypatch, capsys, ["gemini", "auth", "--manual"])
    assert code == 0 and json.loads(out) == {"ok": True, "manual": True}

    def refuse(*a: Any, **kw: Any) -> dict[str, Any]:
        raise ProviderError("gemini", "already stored", "")

    monkeypatch.setattr(auth, "gemini_auth", refuse)
    code, _, err = run(monkeypatch, capsys, ["gemini", "auth"])
    assert code == 1 and "already stored" in err


def test_venues_auth_and_lookup(cli_env: Services, monkeypatch, capsys) -> None:
    monkeypatch.setattr(auth, "places_auth", lambda settings, state, *, manual, force: {"ok": True, "force": force})
    code, out, _ = run(monkeypatch, capsys, ["venues", "auth", "--force"])
    assert code == 0 and json.loads(out)["force"] is True

    def refuse(*a: Any, **kw: Any) -> dict[str, Any]:
        raise ProviderError("places", "no key", "")

    monkeypatch.setattr(auth, "places_auth", refuse)
    assert run(monkeypatch, capsys, ["venues", "auth"])[0] == 1
    cli_env.places = FakeVenueLookup({"cafe du nord": HIT})
    code, out, _ = run(monkeypatch, capsys, ["venues", "lookup", "--name", "Cafe du Nord"])
    assert code == 0 and out.splitlines()[0] == HIT.name and HIT.website in out
    code, out, _ = run(monkeypatch, capsys, ["venues", "lookup", "--name", "Cafe du Nord", "--json"])
    assert code == 0 and json.loads(out)["name"] == HIT.name
    code, out, _ = run(monkeypatch, capsys, ["venues", "lookup", "--name", "Nowhere"])
    assert code == 1 and out == "no venue found for 'Nowhere'\n"


# ---------- the spotify records ----------


def test_every_spotify_record_command(cli_env: Services, monkeypatch, capsys) -> None:
    calls: list[str] = []

    def record(name: str) -> Any:
        def fn(*a: Any, **kw: Any) -> dict[str, Any]:
            calls.append(name)
            return {"cmd": name}

        return fn

    for name in (
        "me",
        "liked",
        "list_playlists",
        "playlist_tracks",
        "create_playlist",
        "add_tracks",
        "rename_playlist",
        "unfollow_playlist",
        "search_artist",
    ):
        monkeypatch.setattr(playlists, name, record(name))
    monkeypatch.setattr(auth, "spotify_auth", record("spotify_auth"))
    monkeypatch.setattr(auth, "setlistfm_auth", record("setlistfm_auth"))
    for argv in (
        ["auth", "--manual"],
        ["me"],
        ["liked", "--artist", "Zed Fox", "--refresh"],
        ["playlists"],
        ["playlist-tracks", "--playlist", "p1"],
        ["playlist-create", "--name", "N", "--public"],
        ["playlist-add", "--playlist", "p1", "--uri", "spotify:track:a", "--uri", "spotify:track:b"],
        ["rename", "--playlist", "p1", "--name", "M"],
        ["playlist-unfollow", "--playlist", "p1"],
        ["search-artist", "--name", "Zed"],
        ["setlist-auth", "--force"],
    ):
        code, out, _ = run(monkeypatch, capsys, ["spotify", *argv])
        assert code == 0 and json.loads(out)["cmd"] == calls[-1], argv
    assert calls == [
        "spotify_auth",
        "me",
        "liked",
        "list_playlists",
        "playlist_tracks",
        "create_playlist",
        "add_tracks",
        "rename_playlist",
        "unfollow_playlist",
        "search_artist",
        "setlistfm_auth",
    ]


# ---------- teaser --description, setlist-find --songs ----------


def test_teaser_with_description_needs_both_names_and_reports_spotify(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["teaser", "--artist", " ", "--date", "2026-11-14", "--description", "d"])
    assert code == 1 and "artist" in out
    code, out, _ = run(monkeypatch, capsys, ["teaser", "--artist", "Zed Fox", "--date", " ", "--description", "d"])
    assert code == 1 and "date" in out
    code, out, _ = run(
        monkeypatch, capsys, ["teaser", "--artist", "Zed Fox", "--date", "not-a-date", "--description", "d"]
    )
    assert code == 1 and out.startswith("FIX:")
    cli_env.spotify = None
    code, out, _ = run(
        monkeypatch, capsys, ["teaser", "--artist", "Zed Fox", "--date", "2026-11-14", "--description", "d"]
    )
    assert code == 1 and "Spotify is not set up" in out
    cli_env.spotify = FakeSpotify(artists=[], liked=[])
    code, out, _ = run(
        monkeypatch,
        capsys,
        ["teaser", "--artist", "Zed Fox", "--date", "2026-11-14", "--description", "d", "--dry-run"],
    )
    assert code == 0 and "**Teaser" in out


def test_setlist_find_with_songs_needs_both_names_and_reports_setlistfm(cli_env: Services, monkeypatch, capsys) -> None:
    code, out, _ = run(monkeypatch, capsys, ["setlist-find", "--artist", " ", "--date", "2026-11-14", "--songs"])
    assert code == 1 and "artist" in out
    code, out, _ = run(monkeypatch, capsys, ["setlist-find", "--artist", "Zed Fox", "--date", " ", "--songs"])
    assert code == 1 and "date" in out
    code, out, _ = run(monkeypatch, capsys, ["setlist-find", "--artist", "Zed Fox", "--date", "soon", "--songs"])
    assert code == 1 and out.startswith("FIX:")
    cli_env.setlistfm = None
    code, out, _ = run(monkeypatch, capsys, ["setlist-find", "--artist", "Zed Fox", "--date", "2026-11-14", "--songs"])
    assert code == 1 and "setlist.fm is not set up" in out
    cli_env.setlistfm = FakeSetlistFm(
        {("Zed Fox", "14-11-2026", None): {"setlist": [api_row("1a2b3c4d", "14-11-2026")]}}
    )
    code, out, _ = run(
        monkeypatch, capsys, ["setlist-find", "--artist", "Zed Fox", "--date", "2026-11-14", "--songs", "--json"]
    )
    rec = one_json_line(out)
    assert code == 0 and rec["found"] is True and rec["songs"]
