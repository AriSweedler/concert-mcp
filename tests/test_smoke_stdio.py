"""One real process: `python -m concert_mcp serve` over stdio, driven by the MCP client, on a bare tmp home."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from concert_mcp.result import NEED_USER  # noqa: E402

EIGHT = {
    "concert_add",
    "calendar_add",
    "event_delete",
    "teaser_playlist",
    "setlist_find",
    "setlist_playlist",
    "queue_status",
    "healthcheck",
}

pytestmark = pytest.mark.integration


def _params(tmp: Path) -> StdioServerParameters:
    env = {
        **os.environ,
        "CONCERT_MCP_CONFIG_DIR": str(tmp / "config"),
        "CONCERT_MCP_STATE_DIR": str(tmp / "state"),
        "CONCERT_MCP_CACHE_DIR": str(tmp / "cache"),
        "CONCERT_MCP_OPENCLAW_HOME": str(tmp / "openclaw"),
        "CONCERT_MCP_GWS_KEY_FILE": str(tmp / "nokey"),
        "CONCERT_MCP_SEND": "0",
        "HOME": str(tmp),
    }
    return StdioServerParameters(command=sys.executable, args=["-m", "concert_mcp", "serve"], env=env)


def _text(res) -> str:  # noqa: ANN001
    return "".join(c.text for c in res.content if getattr(c, "type", "") == "text")


async def test_stdio_server_lists_and_calls_the_tools(tmp_path: Path) -> None:
    errlog_path = tmp_path / "server-stderr.log"
    with errlog_path.open("w", encoding="utf-8") as errlog:
        try:
            async with stdio_client(_params(tmp_path), errlog=errlog) as (read, write), ClientSession(read, write) as s:
                init = await s.initialize()
                assert "NO_REPLY" in (init.instructions or "")

                tools = await s.list_tools()
                by_name = {t.name: t for t in tools.tools}
                assert set(by_name) == EIGHT
                for name, tool in by_name.items():
                    for pname, prop in tool.input_schema.get("properties", {}).items():
                        assert prop.get("type") == "string", (name, pname)
                        assert prop.get("description"), (name, pname)

                res = await s.call_tool("healthcheck", {})
                assert not res.is_error
                assert "FAIL  gws-keyfile" in _text(res)

                res = await s.call_tool("concert_add", {"artist": "Zed Fox", "start": "tomorrow"})
                assert not res.is_error
                assert _text(res).startswith("FIX: start")

                res = await s.call_tool("concert_add", {})
                assert not res.is_error
                assert _text(res).startswith(NEED_USER.format(what="artist"))
        except BaseException:
            errlog.flush()
            sys.stderr.write("---- server stderr ----\n" + errlog_path.read_text(encoding="utf-8", errors="replace"))
            raise
