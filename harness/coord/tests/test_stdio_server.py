"""Start the real `harness-coord-mcp` console script over stdio and list its tools.

This catches what in-process tests cannot: a broken entry point or an `mcp`
release that moved `mcp.server.fastmcp` (2.x did).
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from harness_coord import TOOL_NAMES


def _server_command() -> str:
    script = Path(sys.executable).parent / "harness-coord-mcp"
    found = str(script) if script.exists() else shutil.which("harness-coord-mcp")
    assert found, "harness-coord-mcp console script is not installed in this environment"
    return found


async def _probe() -> tuple[str, list[str], dict[str, object]]:
    params = StdioServerParameters(command=_server_command(), args=[])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        tools = await session.list_tools()
        called = await session.call_tool("gen_hash", {"length": 6})
        assert called.structuredContent is not None
        return init.serverInfo.name, [t.name for t in tools.tools], called.structuredContent


def test_stdio_server_starts_and_lists_exactly_the_contracted_tools():
    name, tools, hashed = asyncio.run(asyncio.wait_for(_probe(), timeout=30))

    assert name == "coord"
    assert sorted(tools) == sorted(TOOL_NAMES)
    assert hashed["ok"] is True
    assert len(str(hashed["hash"])) == 6
