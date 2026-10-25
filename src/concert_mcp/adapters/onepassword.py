"""1Password CLI, used by the one-time auth commands only (unit adapter-music). Values are never logged, not even
at debug level: the field assignment that carries a secret is redacted from the subprocess log line."""

from __future__ import annotations

import shutil

from concert_mcp.log import log_warn
from concert_mcp.proc import Runner, run
from concert_mcp.result import ProviderError

OP_PLACEHOLDER = "FILL_THIS_OUT"


def op_available() -> bool:
    """Whether `op` is on PATH."""
    return shutil.which("op") is not None


def op_read(vault: str, item: str, field: str, runner: Runner = run, timeout_s: int = 120) -> str:
    """`op read op://<vault>/<item>/<field>`; ProviderError('op') on failure, an empty value or the placeholder."""
    ref = f"op://{vault}/{item}/{field}"
    r = runner(["op", "read", ref], timeout_s)
    if not r.ok:
        rc = r.returncode if r.error is None else r.error
        raise ProviderError("op", f"op read failed | ref='{ref}' rc='{rc}' stderr='{r.tail}'", "")
    value = r.stdout.strip()
    if not value or value == OP_PLACEHOLDER:
        raise ProviderError(
            "op",
            f"the 1Password item still holds the placeholder | item='{item}' field='{field}'",
            "",
        )
    return value


def op_store_field(vault: str, item: str, field: str, value: str, runner: Runner = run, timeout_s: int = 90) -> bool:
    """`op item edit <item> --vault <vault> "<field>[password]=<value>"`; best effort, never raises."""
    argv = ["op", "item", "edit", item, "--vault", vault, f"{field}[password]={value}"]
    try:
        r = runner(argv, timeout_s, redact_from=len(argv) - 1)
    except Exception as e:  # noqa: BLE001 - a backup store must never take the auth flow down
        log_warn(f"could not store the field in 1Password | item='{item}' field='{field}' reason='{type(e).__name__}'")
        return False
    if r.ok:
        return True
    reason = (r.error or r.tail)[:160]
    log_warn(f"could not store the field in 1Password | item='{item}' field='{field}' reason='{reason}'")
    return False
