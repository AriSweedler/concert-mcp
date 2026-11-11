"""`concert-mcp failures` (unit flows-failures): keep every screenshot the bot failed, on this machine, for replay.

The model is unreachable when this matters most, so no tool is ever called: the record comes from OpenClaw's
transcript after the fact. `collect` writes each failed turn into `<state_dir>/failures/<epoch seconds>/`, once, and
keeps the newest MAX_KEPT. Each folder holds what is needed to replay it (`concert-mcp failures replay`) and to learn
from it:

  image.jpg               the screenshot
  prompt.txt              what the user typed with it
  text.txt                the OCR the model read
  request.txt             the whole user turn as OpenClaw built it
  meta.json               when, the chat and topic, the session, the error
  replay.sh               the try-image line
  orin-explanation.txt    the bot's own model on what went wrong, asked once it is reachable again
  claude-explanation.txt  Claude's account, written by the fallback (beads-afj), to improve the MCP later

A launchd job on the gateway runs `collect` every few minutes.

A failure inside a tool (Google Calendar refusing, a crash, a fix the guard gave up on) is different: the model
answered, so the transcript shows nothing wrong. `save_tool_failure` writes that one from the server, the moment it
happens, into the same folders: `meta.json` says `"kind": "tool"`, `args.json` holds the call, `reply.txt` what the
chat was told, and `request.txt` the ticket text so the bot can be handed it again. No screenshot: the tool never
sees one.
"""

from __future__ import annotations

import json
import shlex
import shutil
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from concert_mcp.config import Settings
from concert_mcp.log import kv, log_info, log_warn
from concert_mcp.prompts import load_prompt
from concert_mcp.result import ProviderError
from concert_mcp.text.failures import Failure, find_failures

LOOKBACK_MS = 14 * 24 * 3600 * 1000  # a failure older than two weeks is not worth replaying
GRACE_MS = 15 * 60 * 1000  # OpenClaw's own retries end well inside this; a turn still open after it got no reply
MAX_KEPT = 100
ORIN_FILE = "orin-explanation.txt"
NOT_EXPLAINED = "Not explained yet: "  # the head of an orin-explanation.txt a later collect retries

Transcript = Callable[[int], list[tuple[str, int, dict[str, Any]]]]
Explainer = Callable[[str, str], str]  # (system, user) -> the model's answer; raises ProviderError when unreachable


def failures_dir(settings: Settings) -> Path:
    return settings.state_dir / "failures"


def replay_line(folder: Path) -> str:
    """The command that replays a saved failure through try-image."""
    return (
        f"concert-mcp try-image {shlex.quote(str(folder / 'image.jpg'))} "
        f'--caption "$(cat {shlex.quote(str(folder / "prompt.txt"))})"'
    )


def save_failure(f: Failure, root: Path) -> Path | None:
    """Write one failure's folder; None when it already exists."""
    folder = root / str(f.at_ms // 1000)
    if folder.exists():
        return None
    folder.mkdir(parents=True, mode=0o700)
    image = Path(f.image)
    if image.exists():
        shutil.copy2(image, folder / "image.jpg")
    else:
        log_warn(f"failed screenshot no longer on disk | {kv(image=f.image)}")
    (folder / "prompt.txt").write_text(f.caption, encoding="utf-8")
    (folder / "text.txt").write_text(f.ocr, encoding="utf-8")
    (folder / "request.txt").write_text(f.request, encoding="utf-8")
    meta = {k: v for k, v in asdict(f).items() if k not in ("request", "caption", "ocr")}
    meta["image_saved"] = (folder / "image.jpg").exists()
    (folder / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (folder / "replay.sh").write_text(f"#!/bin/sh\n{replay_line(folder)}\n", encoding="utf-8")
    (folder / "replay.sh").chmod(0o700)
    (folder / ORIN_FILE).write_text(f"{NOT_EXPLAINED}not asked yet\n", encoding="utf-8")
    return folder


def save_tool_failure(
    settings: Settings,
    *,
    tool: str,
    args: Mapping[str, str],
    kind: str,
    error: str | None,
    reply: str | None,
    chat: str,
    now_s: float,
) -> Path | None:
    """Write one failed tool call's folder and rotate; None when that second already has a folder."""
    folder = failures_dir(settings) / str(int(now_s))
    if folder.exists():
        return None
    folder.mkdir(parents=True, mode=0o700)
    source = args.get("source_text", "")
    notes = args.get("notes", "")
    request = "\n\n".join(x for x in (notes, source) if x) or json.dumps(dict(args), indent=2)
    (folder / "prompt.txt").write_text(notes, encoding="utf-8")
    (folder / "text.txt").write_text(source, encoding="utf-8")
    (folder / "request.txt").write_text(request, encoding="utf-8")
    (folder / "args.json").write_text(json.dumps(dict(args), indent=2) + "\n", encoding="utf-8")
    (folder / "reply.txt").write_text((reply or "") + "\n", encoding="utf-8")
    meta = {
        "kind": "tool",
        "at_ms": int(now_s * 1000),
        "tool": tool,
        "outcome": kind,
        "error": error or "",
        "chat": chat,
        "image_saved": False,
    }
    (folder / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    rotate(settings)
    return folder


def saved_failures(settings: Settings) -> list[Path]:
    """The saved failure folders, oldest first."""
    root = failures_dir(settings)
    if not root.exists():
        return []
    return sorted((p for p in root.iterdir() if p.is_dir() and p.name.isdigit()), key=lambda p: int(p.name))


def rotate(settings: Settings, keep: int = MAX_KEPT) -> list[Path]:
    """Delete all but the newest `keep` folders; returns the deleted ones."""
    folders = saved_failures(settings)
    gone = folders[: max(0, len(folders) - keep)]
    for p in gone:
        shutil.rmtree(p)
    return gone


def explain(folder: Path, ask: Explainer) -> bool:
    """Ask the bot's model what went wrong, once it answers; True when orin-explanation.txt now holds an answer."""
    path = folder / ORIN_FILE
    if not _unexplained(folder):
        return True
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    request = (folder / "request.txt").read_text(encoding="utf-8")
    prompt = load_prompt("explain_failure").format(error=meta["error"], request=request)
    try:
        answer = ask("", prompt).strip()
    except ProviderError as e:
        path.write_text(f"{NOT_EXPLAINED}the model was unreachable ({e.error})\n", encoding="utf-8")
        return False
    path.write_text((answer or f"{NOT_EXPLAINED}the model answered nothing") + "\n", encoding="utf-8")
    return bool(answer)


def collect(
    settings: Settings, transcript: Transcript, now_ms: int, *, ask: Explainer | None = None, explain_max: int = 1
) -> list[Path]:
    """Save every failed screenshot turn not saved yet, rotate to MAX_KEPT, then ask the model to explain up to
    `explain_max` unexplained ones (newest first; one per run keeps a launchd run short). Returns the new folders."""
    root = failures_dir(settings)
    found = find_failures(transcript(now_ms - LOOKBACK_MS), now_ms=now_ms, grace_ms=GRACE_MS)
    saved = [p for p in (save_failure(f, root) for f in found) if p]
    for p in saved:
        log_info(f"saved a failed screenshot | {kv(folder=str(p))}")
    for p in rotate(settings):
        log_info(f"rotated out an old failure | {kv(folder=str(p))}")
    if ask is not None:
        for p in [p for p in reversed(saved_failures(settings)) if _unexplained(p)][:explain_max]:
            if explain(p, ask):
                log_info(f"the model explained a failure | {kv(folder=str(p))}")
    return saved


def _unexplained(folder: Path) -> bool:
    path = folder / ORIN_FILE
    return not path.exists() or path.read_text(encoding="utf-8").startswith(NOT_EXPLAINED)
