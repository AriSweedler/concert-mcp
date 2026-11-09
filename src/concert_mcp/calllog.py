"""A line per tool call, on disk (unit calllog): what the model asked for, never what it said.

`<state_dir>/calls.jsonl` gets one JSON object per call: when, the tool, which arguments the model passed and how
long `source_text` was. No argument values, so nothing from a chat lands in the file. `bin/concert-replay.sh` in the
homelab prints the lines a replay produced, which is how a bot turn is debugged without reading the gateway's
transcript: a movie event with no runtime line and `source_text_chars: 0` says the model dropped the ticket text.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path

from concert_mcp.log import kv, log_warn

FILE = "calls.jsonl"
MAX_BYTES = 1_000_000  # about ten thousand calls; the file starts over past this


def record_call(state_dir: Path, tool: str, args: Mapping[str, object], now_s: float | None = None) -> None:
    """Append one call; a failure to write is a warning, never a failed tool."""
    line = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_s if now_s is not None else time.time())),
        "tool": tool,
        "args": sorted(args),
        "source_text_chars": len(str(args.get("source_text") or "")),
    }
    path = state_dir / FILE
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_BYTES:
            path.unlink()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, separators=(",", ":")) + "\n")
    except OSError as e:
        log_warn(f"call log not written | {kv(path=str(path), error=type(e).__name__)}")
