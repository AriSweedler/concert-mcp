"""adapters/state.py (JsonState) and adapters/clock.py (SystemClock) against tmp_path and the real clock."""

from __future__ import annotations

import json
import stat
import time
from datetime import UTC
from pathlib import Path

from concert_mcp.adapters.clock import SystemClock
from concert_mcp.adapters.state import JsonState


def test_write_creates_parents_and_leaves_no_tmp(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "queue.json"
    JsonState().write_json(target, [{"event_id": "evt1"}])
    assert target.exists()
    assert not target.with_name("queue.json.tmp").exists()
    assert list(target.parent.iterdir()) == [target]


def test_write_format_is_indented_with_trailing_newline(tmp_path: Path) -> None:
    target = tmp_path / "f.json"
    JsonState().write_json(target, {"b": 1, "a": [1, 2]})
    assert target.read_text(encoding="utf-8") == json.dumps({"b": 1, "a": [1, 2]}, indent=2) + "\n"


def test_write_applies_mode_600(tmp_path: Path) -> None:
    target = tmp_path / "secrets.json"
    JsonState().write_json(target, {"client_id": "x"}, mode=0o600)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_write_without_mode_does_not_chmod(tmp_path: Path, monkeypatch) -> None:
    import concert_mcp.adapters.state as state_mod

    calls: list[tuple[object, int]] = []
    monkeypatch.setattr(state_mod.os, "chmod", lambda p, m: calls.append((p, m)))
    monkeypatch.setattr(state_mod.os, "fchmod", lambda fd, m: calls.append((fd, m)))
    target = tmp_path / "plain.json"
    JsonState().write_json(target, {})
    assert calls == []
    assert target.read_text(encoding="utf-8") == "{}\n"


def test_write_with_mode_never_leaves_the_temp_file_world_readable(tmp_path: Path, monkeypatch) -> None:
    """The temp file carries the final mode from its creation: os.open gets the mode, not a later chmod."""
    import concert_mcp.adapters.state as state_mod

    seen: list[int] = []
    real_open = state_mod.os.open

    def spy_open(p, flags, mode=0o777):
        seen.append(stat.S_IMODE(mode))
        return real_open(p, flags, mode)

    monkeypatch.setattr(state_mod.os, "open", spy_open)
    target = tmp_path / "secrets.json"
    JsonState().write_json(target, {"refresh_token": "x"}, mode=0o600)
    assert seen == [0o600]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not target.with_name("secrets.json.tmp").exists()


def test_round_trips_unicode(tmp_path: Path) -> None:
    target = tmp_path / "u.json"
    obj = {"text": "• **Artist:** Sigur Rós – 8:30 PM › Concerts", "emoji": "🎸"}
    state = JsonState()
    state.write_json(target, obj)
    assert state.read_json(target, None) == obj
    # ensure_ascii=False: the characters are written as themselves, not as \\u escapes.
    assert "Sigur Rós" in target.read_text(encoding="utf-8")


def test_write_replaces_an_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "f.json"
    state = JsonState()
    state.write_json(target, {"v": 1})
    state.write_json(target, {"v": 2})
    assert state.read_json(target, None) == {"v": 2}


def test_read_default_on_missing(tmp_path: Path) -> None:
    assert JsonState().read_json(tmp_path / "nope.json", {"d": True}) == {"d": True}
    assert JsonState().read_json(tmp_path / "nope.json", []) == []


def test_read_default_on_corrupt(tmp_path: Path) -> None:
    target = tmp_path / "bad.json"
    target.write_text("{not json", encoding="utf-8")
    assert JsonState().read_json(target, "fallback") == "fallback"


def test_read_default_on_a_directory(tmp_path: Path) -> None:
    assert JsonState().read_json(tmp_path, 7) == 7


def test_remove_is_missing_ok(tmp_path: Path) -> None:
    target = tmp_path / "gone.json"
    state = JsonState()
    state.remove(target)  # no file: no error
    state.write_json(target, 1)
    state.remove(target)
    assert not target.exists()


def test_mtime_none_when_missing_and_float_when_present(tmp_path: Path) -> None:
    target = tmp_path / "m.json"
    state = JsonState()
    assert state.mtime(target) is None
    before = time.time()
    state.write_json(target, {})
    m = state.mtime(target)
    assert isinstance(m, float)
    assert before - 5 <= m <= time.time() + 5


def test_system_clock() -> None:
    clock = SystemClock()
    now = clock.now()
    assert now.tzinfo is UTC
    assert abs(clock.time() - time.time()) < 5
    t0 = time.monotonic()
    clock.sleep(0.01)
    assert time.monotonic() - t0 >= 0.005
