from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture(autouse=True)
def _isolated_git_config(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Keep the developer's global git config (signing, hooks) out of the tests."""
    home = tmp_path_factory.mktemp("home")
    global_config = home / ".gitconfig"
    global_config.write_text(
        "[user]\n\tname = Harness Test\n\temail = test@example.invalid\n"
        "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@dataclass
class Repos:
    origin: Path
    work: Path


@pytest.fixture
def repos(tmp_path: Path) -> Repos:
    """A bare `origin` with `main` plus three feature branches, and a clone of it."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True
    )
    work = tmp_path / "src" / "proj"
    work.parent.mkdir()
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    (work / "README.md").write_text("hello\n")
    git(work, "add", "README.md")
    git(work, "commit", "-m", "initial")
    git(work, "push", "-u", "origin", "main")
    for branch in ("feature/harness-coord", "feature/Harness_Slack", "fix/unrelated"):
        git(work, "push", "origin", f"main:refs/heads/{branch}")
    git(work, "fetch", "origin")
    return Repos(origin=origin, work=work.resolve())
