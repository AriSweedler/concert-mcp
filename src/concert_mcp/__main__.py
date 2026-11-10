"""`python -m concert_mcp` runs the CLI."""

from concert_mcp.cli import main

if __name__ == "__main__":  # pragma: no cover - the three-line shim test_smoke_stdio.py runs as a subprocess
    raise SystemExit(main())
