"""stdio MCP server ``clickup`` exposing the harness ClickUp tools.

The client is built on first tool call, not at startup, so the server starts
and lists its tools without a token; a missing token surfaces as a tool error.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import tools
from .client import ClickUpClient
from .config import ClickUpConfig

mcp = FastMCP("clickup")

_client: ClickUpClient | None = None


def _get_client() -> ClickUpClient:
    global _client
    if _client is None:
        _client = ClickUpClient(ClickUpConfig.from_env())
    return _client


@mcp.tool()
def get_task(task_id: str) -> dict[str, Any]:
    """Read one ClickUp task: name, description, status, tags, parent, list_id, url."""
    return tools.get_task(_get_client(), task_id)


@mcp.tool()
def list_tasks(list_id: str, include_closed: bool = False) -> list[dict[str, Any]]:
    """List the tasks (including subtasks) in a ClickUp list, live from ClickUp."""
    return tools.list_tasks(_get_client(), list_id, include_closed)


@mcp.tool()
def get_comments(task_id: str) -> list[dict[str, Any]]:
    """Read every comment on a task, newest first."""
    return tools.get_comments(_get_client(), task_id)


@mcp.tool()
def list_statuses(list_id: str) -> list[dict[str, Any]]:
    """List a ClickUp list's statuses with their type (open, custom, done, closed)."""
    return tools.list_statuses(_get_client(), list_id)


@mcp.tool()
def create_task(
    list_id: str,
    name: str,
    description: str = "",
    parent: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Create a task (or a subtask, when parent is a task id). Not retried on server errors,
    so read the list first and only create what is missing."""
    return tools.create_task(_get_client(), list_id, name, description, parent, tags)


@mcp.tool()
def add_comment(task_id: str, text: str) -> dict[str, Any]:
    """Post a comment on a task."""
    return tools.add_comment(_get_client(), task_id, text)


@mcp.tool()
def update_task(
    task_id: str,
    name: str | None = None,
    description: str | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    """Change a task's name, description or priority (1 urgent .. 4 low).
    Status changes go through set_status."""
    return tools.update_task(_get_client(), task_id, name, description, priority)


@mcp.tool()
def set_status(task_id: str, status: str, comment: str) -> dict[str, Any]:
    """Move a task to a status. Posts the comment first, then changes the status; an empty
    comment is refused. Done/closed-type statuses are refused unless HARNESS_ALLOW_DONE=1."""
    return tools.set_status(
        _get_client(), task_id, status, comment, allow_done=tools.allow_done_from_env()
    )


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
