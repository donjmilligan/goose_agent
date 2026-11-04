from __future__ import annotations

from harness_slack import claims

from .conftest import CHANNEL, fake_client, msg

NOW = 1_700_000_000.0

CLAIM_SLACK = (
    "_CLAIM (Don's session, fan-out fo-ggjl, agent 2/7): `harness-slack`_ — new Slack MCP."
)
CLAIM_CLICKUP = (
    "_CLAIM (Don's session, fan-out fo-ggjl, agent 1/7): `harness-clickup`_ — ClickUp MCP."
)
DONE_SLACK = "_COMPLETION: `harness-slack`_ — feature/harness-slack @ abc1234, 40 passed."
DONE_CLICKUP = "_COMPLETION: `harness-clickup`_ — feature/harness-clickup @ def5678."


def _find(history, threads=None, topic="harness"):
    return claims.find_claims(fake_client(history, threads), CHANNEL, topic, now=NOW)


def _open_ids(result) -> list[str]:
    return [c["id"] for c in result["open_claims"]]


def test_completion_in_thread_closes_only_its_claim() -> None:
    result = _find(
        [
            [
                msg("1699990002.0", CLAIM_CLICKUP),
                msg("1699990001.0", CLAIM_SLACK, reply_count=1),
            ]
        ],
        {"1699990001.0": [msg("1699990001.0", CLAIM_SLACK), msg("1699990005.0", DONE_SLACK)]},
    )

    assert _open_ids(result) == ["harness-clickup"]
    assert result["completed_count"] == 1
    assert result["claims_matched"] == 2


def test_completion_for_another_id_does_not_close() -> None:
    result = _find(
        [[msg("1699990001.0", CLAIM_SLACK, reply_count=1)]],
        {"1699990001.0": [msg("1699990001.0", CLAIM_SLACK), msg("1699990005.0", DONE_CLICKUP)]},
    )

    assert _open_ids(result) == ["harness-slack"]
    assert result["completed_count"] == 0


def test_completion_without_id_in_thread_closes() -> None:
    result = _find(
        [[msg("1699990001.0", CLAIM_SLACK, reply_count=1)]],
        {"1699990001.0": [msg("1699990005.0", "_COMPLETION_ — shipped.")]},
    )

    assert _open_ids(result) == []
    assert result["completed_count"] == 1


def test_claims_posted_as_replies_pair_within_their_thread() -> None:
    header = msg("1699990000.0", "fan-out fo-ggjl batch header", reply_count=3)
    result = _find(
        [[header]],
        {
            "1699990000.0": [
                header,
                msg("1699990001.0", CLAIM_SLACK),
                msg("1699990002.0", CLAIM_CLICKUP),
                msg("1699990003.0", DONE_CLICKUP),
            ]
        },
    )

    assert _open_ids(result) == ["harness-slack"]
    assert result["open_claims"][0]["thread_ts"] == "1699990000.0"


def test_completion_before_claim_in_thread_does_not_close() -> None:
    header = msg("1699990000.0", "batch header", reply_count=2)
    result = _find(
        [[header]],
        {
            "1699990000.0": [
                header,
                msg("1699990001.0", DONE_SLACK),
                msg("1699990002.0", CLAIM_SLACK),
            ]
        },
    )

    assert _open_ids(result) == ["harness-slack"]


def test_top_level_completion_is_flagged_but_does_not_close() -> None:
    result = _find(
        [
            [
                msg("1699990005.0", DONE_SLACK),
                msg("1699990002.0", CLAIM_CLICKUP),
                msg("1699990001.0", CLAIM_SLACK),
            ]
        ]
    )

    by_id = {c["id"]: c for c in result["open_claims"]}
    assert set(by_id) == {"harness-slack", "harness-clickup"}
    assert by_id["harness-slack"]["completion_outside_thread"] is True
    assert by_id["harness-clickup"]["completion_outside_thread"] is False


def test_topic_matches_case_insensitively() -> None:
    history = [[msg("1699990002.0", CLAIM_CLICKUP), msg("1699990001.0", CLAIM_SLACK)]]

    assert _open_ids(_find(history, topic="HARNESS-SLACK")) == ["harness-slack"]
    assert _open_ids(_find(history, topic="ClickUp")) == ["harness-clickup"]
    assert _open_ids(_find(history, topic="figma")) == []


def test_only_text_starting_with_marker_counts() -> None:
    result = _find(
        [
            [
                msg("1699990004.0", "re: _CLAIM (x): `quoted`_ — not a claim"),
                msg("1699990003.0", "_CLAIMS digest: `harness-digest`_"),
                msg("1699990002.0", "  " + CLAIM_SLACK),
            ]
        ]
    )

    assert _open_ids(result) == ["harness-slack"]
    assert result["messages_scanned"] == 3


def test_no_claims_is_distinguishable_from_reading_nothing() -> None:
    quiet = _find([[msg("1699990001.0", "lunch?"), msg("1699990000.0", "sure")]])
    empty = _find([[]])

    assert quiet["open_claims"] == [] and quiet["messages_scanned"] == 2
    assert empty["open_claims"] == [] and empty["messages_scanned"] == 0


def test_scan_counts_replies_once_and_skips_threadless_messages() -> None:
    slack = fake_client(
        [
            [msg("1699990002.0", "no thread"), msg("1699990001.0", CLAIM_SLACK, reply_count=2)],
            [msg("1699990000.0", CLAIM_CLICKUP)],
        ],
        {
            "1699990001.0": [
                msg("1699990001.0", CLAIM_SLACK),
                msg("1699990003.0", "looks good"),
                msg("1699990004.0", DONE_SLACK),
            ]
        },
    )

    result = claims.find_claims(slack, CHANNEL, "harness", hours=36, now=NOW)

    assert result["messages_scanned"] == 5
    assert result["threads_scanned"] == 1
    slack.conversations_replies.assert_called_once()
    assert slack.conversations_history.call_count == 2
    assert (
        slack.conversations_history.call_args_list[0].kwargs["oldest"] == f"{NOW - 36 * 3600:.6f}"
    )
    assert _open_ids(result) == ["harness-clickup"]
