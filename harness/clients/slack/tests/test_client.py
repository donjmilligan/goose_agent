from __future__ import annotations

import pytest
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler
from slack_sdk.web import SlackResponse

from harness_slack import client, config

from .conftest import CHANNEL, OTHER_CHANNEL, fake_client, msg

NOW = 1_700_000_000.0


def test_read_channel_computes_oldest_from_hours() -> None:
    slack = fake_client([[msg("1699999000.000100", "hi")]])

    result = client.read_channel(slack, CHANNEL, hours=24, now=NOW)

    assert result["oldest"] == f"{NOW - 24 * 3600:.6f}"
    slack.conversations_history.assert_called_once_with(
        channel=CHANNEL, oldest=result["oldest"], limit=client.PAGE_SIZE
    )
    assert result["count"] == 1


def test_read_channel_follows_cursor_and_returns_oldest_first() -> None:
    slack = fake_client(
        [
            [msg("1699999004.0", "four"), msg("1699999003.0", "three")],
            [msg("1699999002.0", "two"), msg("1699999001.0", "one")],
        ]
    )

    result = client.read_channel(slack, CHANNEL, now=NOW)

    assert [m["text"] for m in result["messages"]] == ["one", "two", "three", "four"]
    assert slack.conversations_history.call_count == 2
    assert slack.conversations_history.call_args_list[1].kwargs["cursor"] == "1"


def test_read_channel_stops_at_limit_keeping_newest() -> None:
    slack = fake_client(
        [
            [msg("1699999004.0", "four"), msg("1699999003.0", "three"), msg("3.0", "x")],
            [msg("1699999002.0", "two")],
        ]
    )

    result = client.read_channel(slack, CHANNEL, limit=2, now=NOW)

    assert [m["text"] for m in result["messages"]] == ["three", "four"]
    assert slack.conversations_history.call_count == 1


@pytest.mark.parametrize(("hours", "limit"), [(0, 10), (-1, 10), (24, 0)])
def test_read_channel_rejects_non_positive_window(hours: float, limit: int) -> None:
    slack = fake_client()
    with pytest.raises(ValueError):
        client.read_channel(slack, CHANNEL, hours=hours, limit=limit, now=NOW)
    slack.conversations_history.assert_not_called()


def test_read_channel_normalizes_message_fields() -> None:
    raw = msg("1699999000.0", "hello", reply_count=2, thread_ts="1699999000.0", blocks=[{}])
    result = client.read_channel(fake_client([[raw]]), CHANNEL, now=NOW)

    assert result["messages"] == [
        {
            "ts": "1699999000.0",
            "thread_ts": "1699999000.0",
            "user": "U0AGENT",
            "text": "hello",
            "reply_count": 2,
        }
    ]


def test_read_thread_paginates_replies() -> None:
    slack = fake_client()
    slack.conversations_replies.side_effect = [
        {
            "ok": True,
            "messages": [msg("1.0", "parent")],
            "response_metadata": {"next_cursor": "c2"},
        },
        {"ok": True, "messages": [msg("2.0", "child")], "response_metadata": {"next_cursor": ""}},
    ]

    result = client.read_thread(slack, CHANNEL, "1.0")

    assert [m["text"] for m in result["messages"]] == ["parent", "child"]
    assert slack.conversations_replies.call_args_list[1].kwargs == {
        "channel": CHANNEL,
        "ts": "1.0",
        "limit": client.PAGE_SIZE,
        "cursor": "c2",
    }


def test_post_message_refused_when_allowlist_unset() -> None:
    slack = fake_client()
    with pytest.raises(client.PostingRefused, match="unset"):
        client.post_message(slack, CHANNEL, "hello")
    slack.chat_postMessage.assert_not_called()


def test_post_message_refused_for_channel_off_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.ALLOWED_CHANNELS_VAR, CHANNEL)
    slack = fake_client()
    with pytest.raises(client.PostingRefused, match=OTHER_CHANNEL):
        client.post_message(slack, OTHER_CHANNEL, "hello")
    slack.chat_postMessage.assert_not_called()


def test_post_message_refuses_channel_name_even_when_id_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.ALLOWED_CHANNELS_VAR, CHANNEL)
    slack = fake_client()
    with pytest.raises(client.PostingRefused, match="channel ID"):
        client.post_message(slack, "#agent-chat", "hello")
    slack.chat_postMessage.assert_not_called()


def test_post_message_allowed_channel_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.ALLOWED_CHANNELS_VAR, f"{OTHER_CHANNEL},{CHANNEL}")
    slack = fake_client()

    result = client.post_message(slack, CHANNEL, "hello")

    slack.chat_postMessage.assert_called_once_with(channel=CHANNEL, text="hello")
    assert result == {"channel": CHANNEL, "ts": "1700000099.000100"}


def test_reply_refused_for_channel_off_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.ALLOWED_CHANNELS_VAR, CHANNEL)
    slack = fake_client()
    with pytest.raises(client.PostingRefused):
        client.reply(slack, OTHER_CHANNEL, "1.0", "done")
    slack.chat_postMessage.assert_not_called()


def test_reply_allowed_channel_posts_in_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.ALLOWED_CHANNELS_VAR, CHANNEL)
    slack = fake_client()

    result = client.reply(slack, CHANNEL, "1.0", "done")

    slack.chat_postMessage.assert_called_once_with(channel=CHANNEL, thread_ts="1.0", text="done")
    assert result["thread_ts"] == "1.0"
    assert result["ts"] == "1700000099.000100"


def test_reads_ignore_allowlist() -> None:
    slack = fake_client([[msg("1699999000.0", "hi")]])
    assert config.allowed_channels() == frozenset()
    assert client.read_channel(slack, OTHER_CHANNEL, now=NOW)["count"] == 1


def test_slack_api_error_becomes_harness_error_with_code() -> None:
    slack = fake_client()
    response = SlackResponse(
        client=None,
        http_verb="POST",
        api_url="https://slack.com/api/conversations.history",
        req_args={},
        data={"ok": False, "error": "not_in_channel"},
        headers={},
        status_code=200,
    )
    slack.conversations_history.side_effect = SlackApiError("boom", response)

    with pytest.raises(
        client.SlackHarnessError, match="conversations_history failed: not_in_channel"
    ):
        client.read_channel(slack, CHANNEL, now=NOW)


def test_make_web_client_uses_env_token_and_rate_limit_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.TOKEN_VAR, "xoxb-test-token")

    web = client.make_web_client()

    assert isinstance(web, WebClient)
    assert web.token == "xoxb-test-token"
    handlers = [h for h in web.retry_handlers if isinstance(h, RateLimitErrorRetryHandler)]
    assert len(handlers) == 1
    assert handlers[0].max_retry_count == client.RATE_LIMIT_RETRIES


def test_make_web_client_without_token_raises() -> None:
    with pytest.raises(config.MissingTokenError):
        client.make_web_client()
