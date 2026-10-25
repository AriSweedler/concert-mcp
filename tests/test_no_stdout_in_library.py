"""stdout is the MCP transport: nothing under src/ may call print() except cli.py and flows/auth.py."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "concert_mcp"
ALLOWED = {SRC / "cli.py", SRC / "flows" / "auth.py"}


def _print_calls(path: Path) -> list[int]:
    """Line numbers of every call to the builtin print (comments and docstrings cannot contain one)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
    ]


def test_source_tree_exists() -> None:
    assert (SRC / "cli.py").exists()
    assert (SRC / "flows" / "auth.py").exists()


def test_no_print_outside_cli_and_auth() -> None:
    offenders = {
        str(path.relative_to(SRC)): lines
        for path in sorted(SRC.rglob("*.py"))
        if path not in ALLOWED and (lines := _print_calls(path))
    }
    assert offenders == {}, f"print() outside cli.py and flows/auth.py: {offenders}"


def test_detector_sees_print_in_cli() -> None:
    """The rule has teeth: the detector finds the prints that are allowed in cli.py."""
    assert _print_calls(SRC / "cli.py")
