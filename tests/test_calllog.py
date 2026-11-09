"""calllog (unit calllog): one line per tool call, argument names and the ticket text's length, never its text."""

from __future__ import annotations

import json
from pathlib import Path

from concert_mcp.calllog import FILE, MAX_BYTES, record_call


def test_record_call_writes_names_and_length_only(tmp_path: Path) -> None:
    record_call(tmp_path, "movie_add", {"film": "Godzilla", "source_text": "Order Confirmation " * 3}, now_s=0)
    record_call(tmp_path, "calendar_add", {"title": "[Movie] Godzilla"}, now_s=60)
    lines = [json.loads(ln) for ln in (tmp_path / FILE).read_text(encoding="utf-8").splitlines()]
    assert lines == [
        {"at": "1970-01-01T00:00:00Z", "tool": "movie_add", "args": ["film", "source_text"], "source_text_chars": 57},
        {"at": "1970-01-01T00:01:00Z", "tool": "calendar_add", "args": ["title"], "source_text_chars": 0},
    ]
    assert "Godzilla" not in (tmp_path / FILE).read_text(encoding="utf-8")


def test_record_call_starts_over_past_the_cap(tmp_path: Path) -> None:
    (tmp_path / FILE).write_text("x" * (MAX_BYTES + 1), encoding="utf-8")
    record_call(tmp_path, "healthcheck", {}, now_s=0)
    assert (tmp_path / FILE).read_text(encoding="utf-8").count("\n") == 1


def test_record_call_never_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")
    record_call(blocker / "dir", "healthcheck", {})  # the parent is a file: mkdir fails, the warning is all
