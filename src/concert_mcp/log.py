"""Stderr-only logging. stdout is the MCP transport, so nothing in this package may print to it."""

from __future__ import annotations

import re
import sys

_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_RESET = "\033[0m"

_LEVELS = {"debug": 10, "info": 20, "warn": 30, "warning": 30, "error": 40, "err": 40, "off": 100}
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_level = _LEVELS["info"]


def set_level(level: str) -> None:
    """Set the threshold: debug | info | warn | error | off (case-insensitive)."""
    global _level
    key = (level or "").strip().lower()
    if key not in _LEVELS:
        raise ValueError(f"unknown log level {level!r}; use debug, info, warn, error or off")
    _level = _LEVELS[key]


def _emit(threshold: int, color: str, label: str, msg: str) -> None:
    if threshold < _level:
        return
    sys.stderr.write(f"{color}[{label}]{_RESET} {msg}\n")
    sys.stderr.flush()


def log_debug(msg: str) -> None:
    """Debug detail (subprocess argv and the like); off unless the level is debug."""
    _emit(_LEVELS["debug"], _CYAN, "DEBUG", msg)


def log_info(msg: str) -> None:
    """Green [INFO] line on stderr."""
    _emit(_LEVELS["info"], _GREEN, "INFO", msg)


def log_warn(msg: str) -> None:
    """Yellow [WARN] line on stderr."""
    _emit(_LEVELS["warn"], _YELLOW, "WARN", msg)


def log_err(msg: str) -> None:
    """Red [ERROR] line on stderr."""
    _emit(_LEVELS["error"], _RED, "ERROR", msg)


def kv(**fields: object) -> str:
    """The record form used in log lines: key='value' key2='value2'."""
    return " ".join(f"{k}='{v}'" for k, v in fields.items())


def strip_ansi(s: str) -> str:
    """Remove ANSI colour escapes."""
    return _ANSI_RE.sub("", s)
