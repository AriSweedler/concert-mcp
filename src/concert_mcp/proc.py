"""Bounded subprocess runner that never raises: a missing binary, a timeout or an OSError become a RunResult."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from concert_mcp.log import kv, log_debug


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    error: str | None = None  # set on FileNotFoundError / TimeoutExpired / OSError; returncode is then -1

    @property
    def ok(self) -> bool:
        return self.error is None and self.returncode == 0

    @property
    def tail(self) -> str:
        return (self.stderr or self.stdout).strip()[-300:]


def run(
    cmd: Sequence[str],
    timeout_s: float,
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    redact_from: int | None = None,
) -> RunResult:
    """Run `cmd` with captured UTF-8 output under a wall-clock cap. Never raises.

    `redact_from` is the index of the first argv element that carries a secret; the debug log shows the elements
    before it and `<redacted>` in place of the rest.
    """
    argv = [str(c) for c in cmd]
    shown = argv if redact_from is None else argv[:redact_from] + ["<redacted>"]
    log_debug("run " + kv(argv=shown, timeout_s=timeout_s, cwd=cwd))
    try:
        p = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env=dict(env) if env is not None else None,
            cwd=str(cwd) if cwd is not None else None,
        )
    except FileNotFoundError:
        return RunResult(-1, "", "", f"binary not found: {argv[0] if argv else ''}")
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        return RunResult(-1, out, err, f"timed out after {timeout_s} s")
    except OSError as e:
        return RunResult(-1, "", "", f"{type(e).__name__}: {e}")
    return RunResult(p.returncode, p.stdout or "", p.stderr or "", None)


Runner = Callable[..., RunResult]
