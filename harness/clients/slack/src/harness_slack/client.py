"""Slack Web API operations for the harness, as plain functions over a WebClient.

Every function takes the `WebClient` as its first argument so tests can pass a
mock and never touch the network. `make_web_client` is the only place a real
client is built, and it is the only place the bot token is read.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

from . import config

PAGE_SIZE = 200
RATE_LIMIT_RETRIES = 5

_MESSAGE_FIELDS = ("ts", "thread_ts", "user", "bot_id", "subtype", "text", "reply_count")


class SlackHarnessError(RuntimeError):
    pass


class PostingRefused(SlackHarnessError):
    """Raised before any API call when a post targets a channel not on the allowlist."""


def make_web_client(token: str | None = None) -> WebClient:
    client = WebClient(token=token if token is not None else config.bot_token())
    client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=RATE_LIMIT_RETRIES))
    return client


def _call(client: WebClient, method: str, **kwargs: Any) -> Any:
    try:
        return getattr(client, method)(**kwargs)
    except SlackApiError as exc:
        error = exc.response.get("error", "unknown_error") if exc.response else "unknown_error"
        raise SlackHarnessError(f"Slack API {method} failed: {error}") from exc


def paginate(client: WebClient, method: str, **kwargs: Any) -> Iterator[dict[str, Any]]:
    cursor: str | None = None
    while True:
        page_kwargs = dict(kwargs, limit=PAGE_SIZE)
        if cursor:
            page_kwargs["cursor"] = cursor
        response = _call(client, method, **page_kwargs)
        yield from response.get("messages", [])
        cursor = (response.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return


def normalize_message(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: raw[key] for key in _MESSAGE_FIELDS if raw.get(key) is not None}


def oldest_ts(hours: float, now: float | None) -> str:
    if hours <= 0:
        raise ValueError(f"hours must be positive, got {hours}")
    return f"{(time.time() if now is None else now) - hours * 3600:.6f}"


def read_channel(
    client: WebClient,
    channel: str,
    hours: float = 24,
    limit: int = 200,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """The newest `limit` top-level messages from the last `hours`, oldest first."""
    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")
    oldest = oldest_ts(hours, now)
    messages: list[dict[str, Any]] = []
    for raw in paginate(client, "conversations_history", channel=channel, oldest=oldest):
        messages.append(normalize_message(raw))
        if len(messages) >= limit:
            break
    messages.reverse()
    return {"channel": channel, "oldest": oldest, "count": len(messages), "messages": messages}


def thread_messages(client: WebClient, channel: str, thread_ts: str) -> list[dict[str, Any]]:
    return [
        normalize_message(raw)
        for raw in paginate(client, "conversations_replies", channel=channel, ts=thread_ts)
    ]


def read_thread(client: WebClient, channel: str, thread_ts: str) -> dict[str, Any]:
    """The thread parent followed by every reply, oldest first."""
    messages = thread_messages(client, channel, thread_ts)
    return {
        "channel": channel,
        "thread_ts": thread_ts,
        "count": len(messages),
        "messages": messages,
    }


def _require_postable(channel: str) -> None:
    allowed = config.allowed_channels()
    if channel in allowed:
        return
    if not allowed:
        reason = f"{config.ALLOWED_CHANNELS_VAR} is unset, so posting is disabled everywhere"
    else:
        reason = f"it is not in {config.ALLOWED_CHANNELS_VAR} ({', '.join(sorted(allowed))})"
    raise PostingRefused(
        f"Refusing to post to channel {channel!r}: {reason}. Posting is outward-facing; "
        f"add the channel ID (not its name) to {config.ALLOWED_CHANNELS_VAR} to allow it."
    )


def post_message(client: WebClient, channel: str, text: str) -> dict[str, Any]:
    _require_postable(channel)
    response = _call(client, "chat_postMessage", channel=channel, text=text)
    return {"channel": response.get("channel", channel), "ts": response.get("ts")}


def reply(client: WebClient, channel: str, thread_ts: str, text: str) -> dict[str, Any]:
    _require_postable(channel)
    response = _call(client, "chat_postMessage", channel=channel, thread_ts=thread_ts, text=text)
    return {
        "channel": response.get("channel", channel),
        "thread_ts": thread_ts,
        "ts": response.get("ts"),
    }
