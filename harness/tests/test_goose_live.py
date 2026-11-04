"""The plugin, its MCP servers and its hooks, run inside a real goose binary.

Skipped unless ``GOOSE_BIN`` points at a goose executable. No model and no
network are needed:

* ``!<command>`` makes goose run the shell tool directly
  (``agents/state_machine/ops_bang_shell.rs``), and that call still goes
  through ``run_pre_tool_hooks``;
* for the full agent loop (a model-issued tool call, then ``Stop``) goose's
  ``openai`` provider is pointed at ``fake_openai.py``, a scripted local
  endpoint.

Everything lives under a temporary ``HOME`` and ``GOOSE_PATH_ROOT``. A second
plugin, ``zz-hooklog``, records every hook payload goose sends, so the tests
see what ``guard.py`` was actually given. ``install.sh`` installs the real
harness tree, which renders the gitignored ``plugin/agent-harness/.mcp.json``
there; the fixture uninstalls afterwards.

    GOOSE_BIN=/path/to/goose uv run --with pytest pytest harness/tests -q
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

HARNESS_DIR = Path(__file__).resolve().parents[1]
FAKE_OPENAI = Path(__file__).resolve().parent / "fake_openai.py"
GOOSE_BIN = os.environ.get("GOOSE_BIN", "")
NOTIFIERS = ("terminal-notifier", "osascript", "notify-send")
EXPECTED_TOOLS = (
    "agent-harness_clickup__set_status",
    "agent-harness_slack__post_message",
    "agent-harness_coord__gen_hash",
)

pytestmark = [
    pytest.mark.skipif(
        not (GOOSE_BIN and os.access(GOOSE_BIN, os.X_OK)),
        reason="set GOOSE_BIN to a goose binary to run the live plugin checks",
    ),
    pytest.mark.skipif(shutil.which("uv") is None, reason="the plugin's MCP servers run with uv"),
]


@dataclass
class Live:
    root: Path
    home: Path
    repo: Path
    env: dict[str, str]
    hooklog: Path
    requests: Path
    notify_calls: Path

    def goose(
        self, *args: str, stdin: str = "", env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        self.hooklog.write_text("")
        return subprocess.run(
            [GOOSE_BIN, *args],
            check=False,
            cwd=self.repo,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=300,
            env={**self.env, **(env or {})},
        )

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.hooklog.read_text().splitlines() if line]

    def event(self, name: str) -> dict[str, Any]:
        found = [e for e in self.events() if e["event"] == name]
        assert len(found) == 1, (name, self.events())
        return found[0]

    def commits(self) -> list[str]:
        return git(self.repo, "log", "--format=%s").splitlines()


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout


def uv_dir(*args: str) -> str:
    return subprocess.run(["uv", *args], check=True, capture_output=True, text=True).stdout.strip()


def write_hooklog_plugin(plugins_dir: Path, log: Path) -> None:
    plugin = plugins_dir / "zz-hooklog"
    (plugin / "hooks").mkdir(parents=True)
    (plugin / "plugin.json").write_text(json.dumps({"name": "zz-hooklog", "version": "0.0.0"}))
    record = f'cat >> "{log}"; echo >> "{log}"'
    events = ["SessionStart", "PreToolUse", "PreToolUseResult", "PostToolUse", "Stop"]
    hooks = {e: [{"hooks": [{"type": "command", "command": record}]}] for e in events}
    (plugin / "hooks" / "hooks.json").write_text(json.dumps({"hooks": hooks}))


def write_notifier_shims(bin_dir: Path, calls: Path) -> None:
    bin_dir.mkdir()
    for name in NOTIFIERS:
        shim = bin_dir / name
        shim.write_text(f'#!/bin/sh\necho "{name} $*" >> "{calls}"\n')
        shim.chmod(0o755)


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Live]:
    base = tmp_path_factory.mktemp("goose-live").resolve()
    root, home, repo = base / "root", base / "home", base / "repo"
    home.mkdir()
    gitconfig = base / "gitconfig"
    gitconfig.write_text("")
    uv_tool_bin = base / "uv-bin"
    shims = base / "bin"
    notify_calls = base / "notify-calls.txt"
    write_notifier_shims(shims, notify_calls)
    env = {
        "HOME": str(home),
        "GOOSE_PATH_ROOT": str(root),
        "GOOSE_DISABLE_KEYRING": "1",
        "PATH": os.pathsep.join([str(shims), str(uv_tool_bin), os.environ["PATH"]]),
        "TMPDIR": str(base),
        "UV_CACHE_DIR": uv_dir("cache", "dir"),
        "UV_PYTHON_INSTALL_DIR": uv_dir("python", "dir"),
        "UV_TOOL_DIR": str(base / "uv-tools"),
        "UV_TOOL_BIN_DIR": str(uv_tool_bin),
        "GIT_CONFIG_GLOBAL": str(gitconfig),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "HARNESS_NOTIFY": "0",
        "GOOSE_PROVIDER": "openai",
        "GOOSE_MODEL": "fake-model",
        "OPENAI_API_KEY": "fake-key",
    }

    installed = subprocess.run(
        [str(HARNESS_DIR / "install.sh")], env=env, capture_output=True, text=True, check=False
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    hooklog = base / "hooklog.jsonl"
    write_hooklog_plugin(root / ".agents" / "plugins", hooklog)
    (root / "config" / "config.yaml").write_text("GOOSE_TELEMETRY_ENABLED: false\n")

    subprocess.run(["git", "init", "-q", "-b", "feature/x", str(repo)], check=True, env=env)
    git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    (repo / "f.txt").write_text("x\n")
    git(repo, "add", "f.txt")

    requests_dir = base / "fake-openai"
    server = subprocess.Popen(
        [sys.executable, str(FAKE_OPENAI), str(requests_dir)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert server.stdout is not None
        port = server.stdout.readline().split()[1]
        env["OPENAI_HOST"] = f"http://127.0.0.1:{port}"
        yield Live(
            root=root,
            home=home,
            repo=repo,
            env=env,
            hooklog=hooklog,
            requests=requests_dir / "requests.jsonl",
            notify_calls=notify_calls,
        )
    finally:
        server.terminate()
        server.wait(timeout=10)
        subprocess.run(
            [str(HARNESS_DIR / "install.sh"), "--uninstall"],
            env=env,
            capture_output=True,
            check=False,
        )


def offered_tools(requests: Path) -> set[str]:
    names: set[str] = set()
    for line in requests.read_text().splitlines():
        for tool in json.loads(line)["body"].get("tools") or []:
            names.add(tool["function"]["name"])
    return names


def test_goose_discovers_the_plugin_and_loads_its_three_servers(live: Live) -> None:
    result = live.goose("run", "--no-session", "-t", "hello")
    assert result.returncode == 0, result.stdout + result.stderr

    config = (live.root / "config" / "config.yaml").read_text()
    plugin_root = live.root / ".agents" / "plugins" / "agent-harness"
    assert str(plugin_root) in config, config

    tools = offered_tools(live.requests)
    assert "shell" in tools, sorted(tools)
    for name in EXPECTED_TOOLS:
        assert name in tools, sorted(tools)
    assert not any(t.startswith(("coord__", "clickup__", "slack__")) for t in tools)


def test_bang_shell_commit_no_verify_is_blocked_with_no_model(live: Live) -> None:
    before = live.commits()
    result = live.goose("run", "--no-session", "-t", "!git commit --no-verify -m blocked-live")
    assert "denied by policy hook `agent-harness`" in result.stdout, result.stdout
    assert "--no-verify" in result.stdout
    assert ".. Do not retry" not in result.stdout
    assert live.commits() == before
    assert "blocked-live" not in live.commits()

    verdict = live.event("PreToolUseResult")
    assert verdict["tool_name"] == "shell"
    assert verdict["tool_input"] == {"command": "git commit --no-verify -m blocked-live"}
    assert verdict["decision"] == "deny"
    assert verdict["blocked_by"] == "agent-harness"


def test_bang_shell_git_status_is_allowed_with_the_payload_guard_expects(live: Live) -> None:
    result = live.goose("run", "--no-session", "-t", "!git status")
    assert "On branch feature/x" in result.stdout, result.stdout
    assert "denied by policy hook" not in result.stdout

    seen = live.event("PreToolUse")
    assert seen["tool_name"] == "shell"
    assert seen["tool_input"] == {"command": "git status"}
    assert seen["working_dir"] == str(live.repo)
    assert live.event("PreToolUseResult")["decision"] == "allow"


def test_model_issued_commit_is_blocked_and_the_turn_still_stops(live: Live) -> None:
    before = live.commits()
    result = live.goose(
        "run", "--no-session", "--max-turns", "3", "-t", "RUN: git commit --no-verify -m model-live"
    )
    assert "denied by policy hook `agent-harness`" in result.stdout, result.stdout
    assert "model-live" not in live.commits()
    assert live.commits() == before
    assert live.event("PreToolUseResult")["decision"] == "deny"
    assert live.event("Stop")["last_assistant_message"] == "FAKE_DONE"


def test_stop_notifier_is_silent_with_harness_notify_0_and_fires_with_1(live: Live) -> None:
    live.notify_calls.unlink(missing_ok=True)
    quiet = live.goose("run", "--no-session", "--max-turns", "3", "-t", "RUN: git status")
    assert "FAKE_DONE" in quiet.stdout, quiet.stdout
    assert live.event("Stop")["last_assistant_message"] == "FAKE_DONE"
    assert not live.notify_calls.exists()

    live.goose(
        "run",
        "--no-session",
        "--max-turns",
        "3",
        "-t",
        "RUN: git status",
        env={"HARNESS_NOTIFY": "1"},
    )
    assert live.event("Stop")["last_assistant_message"] == "FAKE_DONE"
    assert "FAKE_DONE" in live.notify_calls.read_text()


def test_session_start_banner_is_shown_by_an_interactive_session(live: Live) -> None:
    result = live.goose("session", stdin="/exit\n")
    assert "Claim gate: other agents may be working" in result.stdout, result.stdout
    assert "Slack signal SKIPPED" in result.stdout
    assert live.event("SessionStart")["event"] == "SessionStart"


def test_installed_harness_coord_cli_runs_from_a_goose_shell(live: Live) -> None:
    result = live.goose("run", "--no-session", "-t", "!harness-coord gen-hash --help")
    assert "usage: harness-coord gen-hash" in result.stdout, result.stdout
    assert live.event("PreToolUseResult")["decision"] == "allow"
