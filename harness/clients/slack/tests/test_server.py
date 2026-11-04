from __future__ import annotations

import asyncio
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError
from slack_sdk import WebClient

from harness_slack import config, server

from .conftest import CHANNEL, OTHER_CHANNEL, fake_client

CONTRACTED_TOOLS = {"read_channel", "read_thread", "post_message", "reply", "find_claims"}


def _no_client() -> WebClient:
    raise AssertionError("listing tools must not build a Slack client")


def test_server_name_and_tools_match_contract() -> None:
    mcp = server.build_server(_no_client)
    tools = asyncio.run(mcp.list_tools())

    assert mcp.name == "slack"
    assert {t.name for t in tools} == CONTRACTED_TOOLS
    assert server.TOOL_NAMES == CONTRACTED_TOOLS


def test_tool_signatures_match_contract() -> None:
    tools = {
        t.name: t.inputSchema for t in asyncio.run(server.build_server(_no_client).list_tools())
    }

    def params(name: str) -> dict[str, object]:
        props = tools[name]["properties"]
        return {key: props[key].get("default", "required") for key in props}

    assert params("read_channel") == {"channel": "required", "hours": 24, "limit": 200}
    assert params("read_thread") == {"channel": "required", "thread_ts": "required"}
    assert params("post_message") == {"channel": "required", "text": "required"}
    assert params("reply") == {"channel": "required", "thread_ts": "required", "text": "required"}
    assert params("find_claims") == {"channel": "required", "topic": "required", "hours": 36}


def test_post_message_tool_refuses_off_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.ALLOWED_CHANNELS_VAR, CHANNEL)
    slack = fake_client()
    mcp = server.build_server(lambda: slack)

    with pytest.raises(ToolError, match="Refusing to post"):
        asyncio.run(mcp.call_tool("post_message", {"channel": OTHER_CHANNEL, "text": "hi"}))
    slack.chat_postMessage.assert_not_called()


def test_post_message_tool_allowed_channel_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.ALLOWED_CHANNELS_VAR, CHANNEL)
    slack = fake_client()
    mcp = server.build_server(lambda: slack)

    asyncio.run(mcp.call_tool("post_message", {"channel": CHANNEL, "text": "hi"}))

    slack.chat_postMessage.assert_called_once_with(channel=CHANNEL, text="hi")


def test_stdio_server_lists_contracted_tools_without_token() -> None:
    env = {k: v for k, v in os.environ.items() if k not in {config.TOKEN_VAR, config.ENV_FILE_VAR}}
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "harness_slack.server"], env=env
    )

    async def probe() -> set[str]:
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.serverInfo.name == "slack"
            return {t.name for t in (await session.list_tools()).tools}

    assert asyncio.run(asyncio.wait_for(probe(), timeout=30)) == CONTRACTED_TOOLS


def test_missing_token_surfaces_as_tool_error_naming_the_variable() -> None:
    mcp = server.build_server(server._default_client)

    with pytest.raises(ToolError, match="SLACK_BOT_TOKEN is not set"):
        asyncio.run(mcp.call_tool("read_channel", {"channel": CHANNEL}))
