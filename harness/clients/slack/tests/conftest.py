from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from slack_sdk import WebClient

from harness_slack import config

CHANNEL = "C0HARNESS1"
OTHER_CHANNEL = "C0ELSEWHERE"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (config.TOKEN_VAR, config.ALLOWED_CHANNELS_VAR, config.ENV_FILE_VAR):
        monkeypatch.delenv(var, raising=False)


def _page(messages: list[dict[str, Any]], next_cursor: str = "") -> dict[str, Any]:
    return {"ok": True, "messages": messages, "response_metadata": {"next_cursor": next_cursor}}


def fake_client(
    history_pages: list[list[dict[str, Any]]] | None = None,
    threads: dict[str, list[dict[str, Any]]] | None = None,
) -> MagicMock:
    """A WebClient mock whose history is served in pages keyed by cursor index."""
    pages = history_pages if history_pages is not None else [[]]
    thread_map = threads or {}
    client = MagicMock(spec=WebClient)

    def history(**kwargs: Any) -> dict[str, Any]:
        index = int(kwargs.get("cursor") or 0)
        more = str(index + 1) if index + 1 < len(pages) else ""
        return _page(pages[index], more)

    def replies(**kwargs: Any) -> dict[str, Any]:
        return _page(thread_map[kwargs["ts"]])

    client.conversations_history.side_effect = history
    client.conversations_replies.side_effect = replies
    client.chat_postMessage.return_value = {
        "ok": True,
        "channel": CHANNEL,
        "ts": "1700000099.000100",
    }
    return client


def msg(ts: str, text: str, **extra: Any) -> dict[str, Any]:
    return {"ts": ts, "user": "U0AGENT", "text": text, **extra}
