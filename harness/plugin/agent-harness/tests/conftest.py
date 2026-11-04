from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

HOOK_ENV_VARS = ("SLACK_BOT_TOKEN", "HARNESS_CLAIM_CHANNEL", "HARNESS_NOTIFY")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's global git config and Slack env out of every test."""
    gitconfig = tmp_path_factory.mktemp("home") / ".gitconfig"
    gitconfig.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{key}_NAME", "t")
        monkeypatch.setenv(f"GIT_{key}_EMAIL", "t@example.com")
    for key in HOOK_ENV_VARS:
        monkeypatch.delenv(key, raising=False)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout


@dataclass
class Repo:
    path: Path
    origin: Path

    def git(self, *args: str) -> str:
        return git(self.path, *args)

    def checkout(self, branch: str) -> None:
        self.git("checkout", "-q", "-B", branch)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    """A clone on `feature/x` whose bare origin has `main` as its default."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    git(work, "remote", "add", "origin", str(origin))
    (work / "README.md").write_text("hi\n")
    git(work, "add", "README.md")
    git(work, "commit", "-q", "-m", "init")
    git(work, "push", "-q", "origin", "main")
    git(work, "remote", "set-head", "origin", "main")
    git(work, "checkout", "-q", "-b", "feature/x")
    return Repo(path=work, origin=origin)


def run_script(
    name: str,
    stdin: str | dict[str, Any],
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    payload = stdin if isinstance(stdin, str) else json.dumps(stdin)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, **(env or {})},
    )
