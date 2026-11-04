from __future__ import annotations

import io
import json
import subprocess
import urllib.error
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import claim_gate
import pytest
from conftest import Repo, run_script

SESSION_START = {"event": "SessionStart", "session_id": "test-session"}


def banner_of(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert set(document) == {"banner"}
    return document["banner"]


@pytest.fixture
def busy_repo(repo: Repo, tmp_path: Path) -> Repo:
    repo.git("push", "-q", "origin", "HEAD:refs/heads/feature/someone-else")
    repo.git("push", "-q", "origin", "HEAD:refs/heads/fix/other-topic")
    repo.git("worktree", "add", "-q", str(tmp_path / "wt-unpushed"), "-b", "feature/unpushed")
    return repo


def test_lists_remote_branches_worktrees_and_says_slack_was_skipped(busy_repo: Repo) -> None:
    banner = banner_of(
        run_script("claim_gate.py", {**SESSION_START, "working_dir": str(busy_repo.path)})
    )
    assert "Remote branches on origin: 3 total" in banner
    assert "feature/someone-else" in banner
    assert "fix/other-topic" in banner
    assert "wt-unpushed" in banner
    assert "[feature/unpushed]" in banner
    assert "Local worktrees (2)" in banner
    assert "Slack signal SKIPPED: SLACK_BOT_TOKEN and HARNESS_CLAIM_CHANNEL not set" in banner
    assert "CLAIM" in banner


def test_only_default_branch_is_reported_as_none_besides_it(repo: Repo) -> None:
    banner = banner_of(
        run_script("claim_gate.py", {**SESSION_START, "working_dir": str(repo.path)})
    )
    assert "Remote branches on origin: 1 total" in banner
    assert "None besides the default branch." in banner
    assert "  · main" not in banner


def test_unreachable_origin_is_reported_not_hidden(repo: Repo, tmp_path: Path) -> None:
    repo.git("remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"))
    banner = banner_of(
        run_script("claim_gate.py", {**SESSION_START, "working_dir": str(repo.path)})
    )
    assert "could not read origin's branches" in banner
    assert "Remote branches on origin" not in banner
    assert "Local worktrees (1)" in banner


def test_outside_a_repo_still_exits_zero_with_the_checklist(tmp_path: Path) -> None:
    banner = banner_of(run_script("claim_gate.py", {**SESSION_START, "working_dir": str(tmp_path)}))
    assert "could not read origin's branches" in banner
    assert "could not list worktrees" in banner
    assert "Claim gate" in banner


@pytest.mark.parametrize("stdin", ["", "not json", "[]"])
def test_unusable_payload_falls_back_to_cwd(stdin: str) -> None:
    banner = banner_of(run_script("claim_gate.py", stdin))
    assert "Claim gate" in banner


class FakeSlack:
    def __init__(self, responses: Mapping[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str], Mapping[str, str], float]] = []

    def __call__(self, url: str, headers: Mapping[str, str], timeout: float) -> dict[str, Any]:
        parsed = urllib.parse.urlparse(url)
        method = parsed.path.rsplit("/", 1)[-1]
        params = dict(urllib.parse.parse_qsl(parsed.query))
        self.calls.append((method, params, headers, timeout))
        response: Any = self.responses[method]
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(params)
        return dict(response)


HISTORY = {
    "ok": True,
    "messages": [
        {"ts": "3.0", "user": "U1", "text": "_CLAIM fo-abcd slice 1 - feature/open-claim_"},
        {
            "ts": "2.0",
            "user": "U2",
            "text": "_CLAIM fo-abcd slice 2 - feature/done-claim_",
            "reply_count": 1,
        },
        {
            "ts": "1.0",
            "user": "U3",
            "text": "_CLAIM fo-abcd slice 3 - feature/chatter-only_",
            "reply_count": 2,
        },
        {"ts": "0.5", "user": "U4", "text": "lunch?"},
    ],
}
THREADS = {
    "2.0": [
        {"ts": "2.0", "text": "_CLAIM fo-abcd slice 2 - feature/done-claim_"},
        {"ts": "2.1", "text": "_COMPLETION slice 2: DONE_"},
    ],
    "1.0": [
        {"ts": "1.0", "text": "_CLAIM fo-abcd slice 3 - feature/chatter-only_"},
        {"ts": "1.1", "text": "how is it going?"},
        {"ts": "1.2", "text": "fine, mentions _COMPLETIONIST_ only"},
    ],
}


def fake_slack() -> FakeSlack:
    return FakeSlack(
        {
            "conversations.history": HISTORY,
            "conversations.replies": lambda params: {
                "ok": True,
                "messages": THREADS[params["ts"]],
            },
        }
    )


def run_main(env: Mapping[str, str], http_get: FakeSlack, cwd: Path) -> str:
    stdout = io.StringIO()
    stdin = io.StringIO(json.dumps({**SESSION_START, "working_dir": str(cwd)}))
    assert claim_gate.main(stdin=stdin, stdout=stdout, env=env, http_get=http_get) == 0
    return json.loads(stdout.getvalue())["banner"]


SLACK_ENV = {"SLACK_BOT_TOKEN": "xoxb-secret-token", "HARNESS_CLAIM_CHANNEL": "C0123"}


def test_slack_lists_only_claims_without_a_completion_reply(repo: Repo) -> None:
    slack = fake_slack()
    banner = run_main(SLACK_ENV, slack, repo.path)
    assert "Open claims in C0123, last 36h (2):" in banner
    assert "feature/open-claim" in banner
    assert "feature/chatter-only" in banner
    assert "feature/done-claim" not in banner
    assert "lunch?" not in banner
    assert "SKIPPED" not in banner
    assert "xoxb-secret-token" not in banner

    methods = [call[0] for call in slack.calls]
    assert methods == ["conversations.history", "conversations.replies", "conversations.replies"]
    history_params = slack.calls[0][1]
    assert history_params["channel"] == "C0123"
    assert float(history_params["oldest"]) > 0
    assert all(call[2]["Authorization"] == "Bearer xoxb-secret-token" for call in slack.calls)
    assert all(0 < call[3] <= claim_gate.SLACK_CALL_TIMEOUT_S for call in slack.calls)


@pytest.mark.parametrize(
    "env",
    [
        {"SLACK_BOT_TOKEN": "xoxb-secret-token"},
        {"HARNESS_CLAIM_CHANNEL": "C0123"},
        {"SLACK_BOT_TOKEN": "  ", "HARNESS_CLAIM_CHANNEL": "C0123"},
    ],
)
def test_slack_is_skipped_and_never_called_without_both_vars(
    repo: Repo, env: Mapping[str, str]
) -> None:
    slack = fake_slack()
    banner = run_main(env, slack, repo.path)
    assert "Slack signal SKIPPED" in banner
    assert "Open claims were NOT checked" in banner
    assert slack.calls == []
    assert run_main(SLACK_ENV, slack, repo.path) and slack.calls != []


@pytest.mark.parametrize(
    ("history", "expected"),
    [
        ({"ok": False, "error": "not_in_channel"}, "not_in_channel"),
        (urllib.error.URLError("no route to host"), "no route to host"),
        (TimeoutError("timed out"), "timed out"),
        (ValueError("bad json"), "bad json"),
    ],
)
def test_slack_failures_are_reported_and_never_raise(
    repo: Repo, history: Any, expected: str
) -> None:
    slack = FakeSlack({"conversations.history": history})
    banner = run_main(SLACK_ENV, slack, repo.path)
    assert "Slack signal failed" in banner
    assert expected in banner
    assert "Open claims were NOT checked" in banner
    assert "Remote branches on origin: 1 total" in banner


def test_unexpected_error_still_yields_a_banner_and_exit_zero(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_: object) -> list[str]:
        raise KeyError("boom")

    monkeypatch.setattr(claim_gate, "branch_lines", explode)
    banner = run_main({}, fake_slack(), repo.path)
    assert "Claim gate could not run (KeyError)" in banner


def test_every_subprocess_and_network_call_is_time_limited(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[float | None] = []
    real_run = subprocess.run

    def recording_run(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("timeout"))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(claim_gate.subprocess, "run", recording_run)
    run_main(SLACK_ENV, fake_slack(), repo.path)
    assert len(seen) == 2
    assert all(timeout == claim_gate.GIT_TIMEOUT_S for timeout in seen)
