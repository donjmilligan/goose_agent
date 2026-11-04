import asyncio
import json
import os
import sys

import pytest
from fake_clickup import make_client
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError

from harness_clickup import server

CONTRACT_TOOLS = {
    "get_task",
    "list_tasks",
    "get_comments",
    "list_statuses",
    "create_task",
    "add_comment",
    "update_task",
    "set_status",
}


def _tools():
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}


def test_server_is_named_clickup():
    assert server.mcp.name == "clickup"


def test_server_lists_exactly_the_contracted_tools():
    assert set(_tools()) == CONTRACT_TOOLS


def test_tool_signatures_match_contract():
    tools = _tools()

    def props(name):
        return set(tools[name].inputSchema["properties"])

    def required(name):
        return set(tools[name].inputSchema.get("required", []))

    assert props("list_tasks") == {"list_id", "include_closed"}
    assert required("list_tasks") == {"list_id"}
    assert props("create_task") == {"list_id", "name", "description", "parent", "tags"}
    assert required("create_task") == {"list_id", "name"}
    assert props("add_comment") == {"task_id", "text"}
    assert props("set_status") == {"task_id", "status", "comment"}
    assert required("set_status") == {"task_id", "status", "comment"}


def test_update_task_tool_has_no_status_field():
    schema = _tools()["update_task"].inputSchema

    assert "status" not in schema["properties"]
    assert set(schema["properties"]) == {"task_id", "name", "description", "priority"}


def _call(name, args):
    return asyncio.run(server.mcp.call_tool(name, args))


def _payload(result):
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


def test_set_status_tool_refuses_done_without_env(monkeypatch):
    client, session = make_client()
    monkeypatch.setattr(server, "_client", client)

    with pytest.raises(ToolError, match="ceiling is review"):
        _call("set_status", {"task_id": "t1", "status": "done", "comment": "finished"})

    assert session.writes == []


def test_set_status_tool_allows_done_with_env(monkeypatch):
    client, session = make_client()
    monkeypatch.setattr(server, "_client", client)
    monkeypatch.setenv("HARNESS_ALLOW_DONE", "1")

    result = _payload(
        _call("set_status", {"task_id": "t1", "status": "done", "comment": "finished"})
    )

    assert result["task"]["status"] == "done"
    assert [w[:2] for w in session.writes] == [("POST", "/task/t1/comment"), ("PUT", "/task/t1")]


def test_tool_without_token_reports_missing_token(monkeypatch):
    monkeypatch.setattr(server, "_client", None)

    with pytest.raises(ToolError, match="CLICKUP_API_TOKEN is not set"):
        _call("get_task", {"task_id": "t1"})


def test_stdio_server_lists_contracted_tools_without_a_token():
    """Real transport: spawn the server over stdio and ask it for tools/list."""
    env = {k: v for k, v in os.environ.items() if k != "CLICKUP_API_TOKEN"}
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "harness_clickup.server"], env=env
    )

    async def probe():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                listed = await session.list_tools()
                return init.serverInfo.name, {t.name for t in listed.tools}

    name, names = asyncio.run(asyncio.wait_for(probe(), timeout=30))

    assert name == "clickup"
    assert names == CONTRACT_TOOLS
