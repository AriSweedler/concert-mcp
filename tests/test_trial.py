"""text/trial.py and flows/try_image.py (the pieces behind `concert-mcp try-image`), adapters/letterboxd_http.py
and proc.py: each on fakes, every branch once."""

from __future__ import annotations

import os
import sys
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from concert_mcp.adapters.letterboxd_http import LetterboxdClient
from concert_mcp.config import Settings
from concert_mcp.flows.try_image import ocr_image, run_trial
from concert_mcp.proc import run
from concert_mcp.result import ProviderError
from concert_mcp.text.trial import (
    Trial,
    render_trial,
)
from fakes import FakeUrlopen, http_error

TOOL = {"name": "healthcheck", "description": "Report.", "inputSchema": {"type": "object", "properties": {}}}


class FakeModel:
    """ChatModelClient's first_tool_call: one canned answer; records what it was asked."""

    def __init__(self, name: str | None, args: dict[str, Any] | None = None, text: str = "") -> None:
        self.answer = (name, args or {}, text)
        self.asked: list[dict[str, Any]] = []

    def first_tool_call(self, *, system: str, user: str, tools: Any) -> tuple[str | None, dict[str, Any], str]:
        self.asked.append({"system": system, "user": user, "tools": list(tools)})
        return self.answer


# ---------- text/trial.py ----------


def test_render_trial_both_shapes() -> None:
    called = Trial(ocr=" Godzilla ", tool="movie_add", args={"title": "Godzilla"}, result=" would add ", elapsed_s=1.26)
    assert render_trial(called) == (
        "== OCR (what the model read)\nGodzilla\n\n"
        "== Model called movie_add (1.3s)\n"
        '{\n  "title": "Godzilla"\n}\n\n'
        "== Would add (dry run: nothing written, nothing sent)\nwould add\n\n"
        "Approve or deny this event."
    )
    silent = Trial(ocr="x", tool=None, model_text="")
    assert (
        render_trial(silent)
        == "== OCR (what the model read)\nx\n\n== Model called no tool. It said:\n(nothing)\n\nApprove or deny this event."
    )


# ---------- flows/try_image.py ----------


def test_ocr_image_runs_the_command_with_the_path_and_the_char_cap(tmp_path: Path) -> None:
    script = tmp_path / "ocr"
    script.write_text('#!/bin/sh\necho "read $1 up to $2 chars"\n', encoding="utf-8")
    script.chmod(0o700)
    assert ocr_image(str(script), tmp_path / "shot.jpg") == f"read {tmp_path / 'shot.jpg'} up to 4000 chars\n"
    with pytest.raises(ProviderError) as ei:
        ocr_image(str(tmp_path / "missing-ocr"), tmp_path / "shot.jpg")
    assert ei.value.provider == "ocr" and "FileNotFoundError" in ei.value.error


def test_run_trial_runs_the_picked_tool_or_reports_the_text() -> None:
    model = FakeModel("healthcheck", {"n": 2, "none": None}, "ok")
    trial = run_trial(
        ocr="text",
        caption="cap",
        system="sys",
        tools=[TOOL],
        model=model,
        call_tool=lambda n, a: f"{n} {a}",  # type: ignore[arg-type]
    )
    assert (trial.tool, trial.args, trial.result) == (
        "healthcheck",
        {"n": "2", "none": ""},
        "healthcheck {'n': '2', 'none': ''}",
    )
    assert model.asked[0]["user"] == "[Image]\nUser text:\ncap\nDescription:\ntext"
    assert model.asked[0]["tools"][0]["type"] == "function"
    quiet = run_trial(
        ocr="t", caption="", system="s", tools=[], model=FakeModel(None, text="hi"), call_tool=lambda n, a: ""
    )  # type: ignore[arg-type]
    assert quiet.tool is None and quiet.model_text == "hi"


# ---------- adapters/letterboxd_http.py ----------


def test_letterboxd_tries_the_year_slug_then_the_bare_one(settings: Settings) -> None:
    page = '<meta property="og:title" content="Godzilla Minus Zero (2026)">'
    fake = FakeUrlopen(
        {
            ("GET", "https://letterboxd.com/film/godzilla-minus-zero-2026/"): http_error(404),
            ("GET", "https://letterboxd.com/film/godzilla-minus-zero/"): page,
        }
    )
    client = LetterboxdClient(settings, urlopen=fake)
    assert client.film_url("Godzilla Minus Zero", 2026) == "https://letterboxd.com/film/godzilla-minus-zero/"
    assert fake.requests[0][2]["User-agent"].startswith("Mozilla/5.0")


def test_letterboxd_misses_on_another_film_or_a_dead_network(settings: Settings) -> None:
    other = '<meta property="og:title" content="Godzilla (1954)">'
    client = LetterboxdClient(settings, urlopen=FakeUrlopen({("GET", "https://letterboxd.com/film/"): other}))
    assert client.film_url("Godzilla Minus Zero", None) is None
    down = LetterboxdClient(settings, urlopen=FakeUrlopen({("GET", "https://"): urllib.error.URLError("down")}))
    assert down.film_url("Godzilla Minus Zero", 2026) is None


# ---------- proc.py ----------


def test_run_never_raises(tmp_path: Path) -> None:
    ok = run([sys.executable, "-c", "print('hi')"], 10)
    assert ok.ok and ok.stdout == "hi\n" and ok.tail == "hi"
    missing = run([str(tmp_path / "no-such-binary")], 1)
    assert not missing.ok and missing.error is not None and missing.error.startswith("binary not found")
    slow = run([sys.executable, "-c", "import time; print('x'); time.sleep(5)"], 0.2)
    assert slow.returncode == -1 and slow.error == "timed out after 0.2 s"
    denied = run([str(tmp_path)], 1)  # a directory: PermissionError, an OSError that is not FileNotFoundError
    assert denied.returncode == -1 and denied.error is not None and "Error" in denied.error
    assert os.path.isdir(tmp_path)
