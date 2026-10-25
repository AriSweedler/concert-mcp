"""JSON state files with atomic writes (unit adapter-openclaw).

The queue, the delete candidates, the sent log and the fix log are shared between the MCP server and CLI runs, so
every write goes to a sibling .tmp file and lands with os.replace; a reader never sees a half-written file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


class JsonState:
    """StateStore on the file system."""

    def read_json(self, path: Path, default: T) -> T:
        """The parsed file, or default when missing, unreadable or invalid."""
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def write_json(self, path: Path, obj: Any, *, mode: int | None = None) -> None:
        """mkdir -p, write <path>.tmp created with the final mode, os.replace.

        The temp file is opened with `mode` from the start (and fchmod-ed in case it already existed), so a
        credentials file is never world-readable, not even between its creation and the rename.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode if mode is not None else 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if mode is not None:
                os.fchmod(fd, mode)
            f.write(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)

    def remove(self, path: Path) -> None:
        """Delete the file if it exists."""
        Path(path).unlink(missing_ok=True)

    def mtime(self, path: Path) -> float | None:
        """Modification time, or None when missing."""
        try:
            return Path(path).stat().st_mtime
        except OSError:
            return None
