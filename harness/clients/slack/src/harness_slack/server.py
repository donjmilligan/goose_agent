"""stdio MCP server `slack`. goose exposes these tools as `slack__<tool>`."""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from typing import Any

from mcp.server.fastmcp import FastMCP
from slack_sdk import WebClient

from . import claims, client, config

SERVER_NAME = "slack"
TOOL_NAMES = frozenset({"read_channel", "read_thread", "post_message", "reply", "find_claims"})


def build_server(get_client: Callable[[], WebClient]) -> FastMCP:
    """`get_client` is called per tool call, so the server starts and lists tools
    without a token; a missing token surfaces as that call's error."""
    mcp = FastMCP(SERVER_NAME)

    @mcp.tool()
    def read_channel(channel: str, hours: float = 24, limit: int = 200) -> dict[str, Any]:
        """Read top-level messages in a channel from the last `hours`, oldest first.

        `channel` is a channel ID (e.g. C0123ABCD). Returns at most `limit` messages,
        the newest ones when there are more. Thread replies are not included; use
        read_thread for a message whose reply_count is non-zero.
        """
        return client.read_channel(get_client(), channel, hours=hours, limit=limit)

    @mcp.tool()
    def read_thread(channel: str, thread_ts: str) -> dict[str, Any]:
        """Read a thread: the parent message followed by every reply, oldest first."""
        return client.read_thread(get_client(), channel, thread_ts)

    @mcp.tool()
    def post_message(channel: str, text: str) -> dict[str, Any]:
        """Post a new top-level message. Only channel IDs listed in
        HARNESS_SLACK_ALLOWED_CHANNELS are accepted; anything else is refused."""
        return client.post_message(get_client(), channel, text)

    @mcp.tool()
    def reply(channel: str, thread_ts: str, text: str) -> dict[str, Any]:
        """Reply in a thread. Only channel IDs listed in
        HARNESS_SLACK_ALLOWED_CHANNELS are accepted; anything else is refused."""
        return client.reply(get_client(), channel, thread_ts, text)

    @mcp.tool()
    def find_claims(channel: str, topic: str, hours: float = 36) -> dict[str, Any]:
        """List `_CLAIM` messages mentioning `topic` (case-insensitive) that have no
        matching `_COMPLETION` reply in their thread, scanning the last `hours`.

        `messages_scanned` is the positive control: an empty `open_claims` with a
        non-zero scan count means no open claims; with zero it means nothing was read.
        """
        return claims.find_claims(get_client(), channel, topic, hours=hours)

    return mcp


@cache
def _default_client() -> WebClient:
    return client.make_web_client()


def main() -> None:
    config.load_env_file()
    build_server(_default_client).run("stdio")


if __name__ == "__main__":
    main()
