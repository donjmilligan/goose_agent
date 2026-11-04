"""guard.py fed real goose PreToolUse payloads over stdin, as goose runs it.

Every block case is paired with the nearest allow case, so a guard that
blocked everything (or nothing) fails half of each pair.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import Repo, run_script


def payload(command: str, cwd: Path, tool_name: str = "shell") -> dict[str, Any]:
    return {
        "event": "PreToolUse",
        "session_id": "test-session",
        "matcher_context": tool_name,
        "tool_call_id": "call-1",
        "tool_name": tool_name,
        "tool_input": {"command": command},
        "working_dir": str(cwd),
    }


def guard(command: str, cwd: Path, tool_name: str = "shell") -> subprocess.CompletedProcess[str]:
    result = run_script("guard.py", payload(command, cwd, tool_name))
    assert result.stdout == "", "stdout is goose's decision channel and must stay empty"
    return result


def assert_blocked(result: subprocess.CompletedProcess[str], *words: str) -> None:
    assert result.returncode == 2, result.stderr
    assert result.stderr.startswith("agent-harness guard: ")
    for word in words:
        assert word in result.stderr


def assert_allowed(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


# (blocked command, nearest allowed command, words the block reason must contain)
PAIRS = [
    ("git commit --no-verify -m x", 'git commit -m "mention --no-verify"', ["--no-verify"]),
    ("git commit -nm x", "git commit -am x", ["--no-verify"]),
    ("git commit -m x -n", "git commit -m -n", ["--no-verify"]),
    ("git push --no-verify origin feature/x", "git push origin feature/x", ["--no-verify"]),
    ("git -c core.hooksPath=/dev/null commit -m x", "git -c user.name=x commit -m x", ["hooks"]),
    (
        "git push --force origin feature/x",
        "git push --force-with-lease origin feature/x",
        ["--force-with-lease"],
    ),
    ("git push -f origin feature/x", "git push -u origin feature/x", ["--force"]),
    (
        "git push -uf origin feature/x",
        "git push --force-with-lease=feature/x:abc123 origin feature/x",
        ["--force"],
    ),
    ("git push origin +feature/x", "git push origin feature/x:feature/y", ["+refspec"]),
    (
        "git push --force-with-lease origin main",
        "git push --force-with-lease origin feature/main-fix",
        ["`main`"],
    ),
    ("git push origin main", "git push origin feature/main-fix", ["`main`"]),
    ("git push origin HEAD:main", "git push origin HEAD:feature/x", ["`main`"]),
    (
        "git push origin feature/x:refs/heads/master",
        "git push origin feature/x:refs/heads/feature/x",
        ["`master`"],
    ),
    ("git push --all origin", "git push --tags origin", ["--all"]),
    ("git push origin --delete main", "git push origin --delete feature/old", ["`main`"]),
    ("gh pr merge 12 --admin --squash", "gh pr merge 12 --squash", ["--admin"]),
    ("docker compose down", "docker compose -p wt1 down", ["--project-name"]),
    ("docker-compose down -v", "docker-compose --project-name wt1 down -v", ["--project-name"]),
    (
        "docker --context x compose -f c.yml down",
        "docker compose -f down.yml up -d",
        ["--project-name"],
    ),
    ("docker compose down", "COMPOSE_PROJECT_NAME=wt1 docker compose down", ["--project-name"]),
    ("git add .env", "git add .env.example", [".env"]),
    ("git add config/server.pem", "git add .env.template", ["server.pem"]),
    ("git add deploy.key", "git add keyboard.py", ["deploy.key"]),
    ("git add aws_credentials.json", "git add README.md", ["aws_credentials.json"]),
    ("git add config/secrets/prod.yaml", "git add config/settings/prod.yaml", ["secrets"]),
    ("git add -- .env.local", "git add -- notes.txt", [".env.local"]),
    # chained, piped, nested and prefixed forms
    (
        "echo ok && git push --force origin feature/x",
        'echo "git push --force" && git status',
        ["--force"],
    ),
    ("git status; git push origin main", "git status; git push origin feature/x", ["`main`"]),
    ("git status\ngit push -f origin feature/x", "git status\ngit log -1", ["--force"]),
    ("git log | git push -f origin feature/x", "git log | head -1", ["--force"]),
    (
        "true || git push -f origin feature/x",
        "true || git push origin feature/x",
        ["--force"],
    ),
    ("git push -f origin feature/x &", "git push origin feature/x &", ["--force"]),
    (
        "bash -c 'git push -f origin feature/x'",
        "bash -c 'git push origin feature/x'",
        ["--force"],
    ),
    ("sh -lc 'git commit --no-verify -m x'", "sh -lc 'git commit -m x'", ["--no-verify"]),
    ("eval git push -f origin feature/x", "eval git push origin feature/x", ["--force"]),
    ("echo $(git push --force)", "echo $(git rev-parse HEAD)", ["--force"]),
    ("echo `git push --force`", "echo `git rev-parse HEAD`", ["--force"]),
    ("(git push -f origin feature/x)", "(git push origin feature/x)", ["--force"]),
    ("FOO=1 git push -f origin feature/x", "FOO=1 git push origin feature/x", ["--force"]),
    (
        "env GIT_TRACE=1 git push -f origin feature/x",
        "env GIT_TRACE=1 git push origin feature/x",
        ["--force"],
    ),
    (
        "/usr/bin/git push -f origin feature/x",
        "/usr/bin/git push origin feature/x",
        ["--force"],
    ),
    (
        "git push -f origin feature/x 2>&1 > /dev/null",
        "git push origin feature/x 2>&1 > /dev/null",
        ["--force"],
    ),
]


@pytest.mark.parametrize(("blocked", "allowed", "words"), PAIRS, ids=[p[0] for p in PAIRS])
def test_block_and_paired_allow(repo: Repo, blocked: str, allowed: str, words: list[str]) -> None:
    assert_blocked(guard(blocked, repo.path), *words)
    assert_allowed(guard(allowed, repo.path))


def goose_denial(stderr: str) -> str:
    """The text goose shows the model (hooks/mod.rs `HookChainOutcome::denial`)."""
    return (
        f"Tool call denied by policy hook `agent-harness`: {stderr.strip()}. "
        "Do not retry; this is a policy denial, not a transient failure."
    )


@pytest.mark.parametrize("blocked", [p[0] for p in PAIRS] + ["git commit -m x"])
def test_reason_reads_cleanly_inside_goose_denial(repo: Repo, blocked: str) -> None:
    if blocked == "git commit -m x":
        repo.checkout("main")
    result = guard(blocked, repo.path)
    assert result.returncode == 2
    denial = goose_denial(result.stderr)
    assert ".. Do not retry" not in denial, denial
    assert denial.endswith("transient failure.")


def test_every_reason_is_reported_together(repo: Repo) -> None:
    result = guard("git add .env && git push --force origin main", repo.path)
    assert_blocked(result, ".env", "--force", "`main`")
    assert_allowed(guard("git add README.md && git push origin feature/x", repo.path))


def test_commit_blocked_on_main_and_allowed_on_feature(repo: Repo) -> None:
    repo.checkout("main")
    assert_blocked(guard("git commit -m x", repo.path), "`main`")
    repo.checkout("feature/x")
    assert_allowed(guard("git commit -m x", repo.path))


def test_commit_blocked_on_master_too(repo: Repo) -> None:
    repo.checkout("master")
    assert_blocked(guard("git commit -m x", repo.path), "`master`")


def test_git_dash_c_points_branch_detection_at_that_repo(repo: Repo, tmp_path: Path) -> None:
    repo.checkout("main")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert_blocked(guard(f"git -C {repo.path} commit -m x", elsewhere), "`main`")
    repo.checkout("feature/x")
    assert_allowed(guard(f"git -C {repo.path} commit -m x", elsewhere))


def test_cd_earlier_in_the_chain_moves_branch_detection(repo: Repo, tmp_path: Path) -> None:
    repo.checkout("main")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert_blocked(guard(f"cd {repo.path} && git commit -m x", elsewhere), "`main`")
    assert_allowed(guard(f"cd {elsewhere} && git commit -m x", repo.path))


def test_switching_branch_earlier_in_the_chain_is_tracked(repo: Repo) -> None:
    repo.checkout("main")
    assert_allowed(guard("git switch -c feature/new && git commit -m x", repo.path))
    assert_allowed(guard("git checkout -b feature/new && git commit -m x", repo.path))
    repo.checkout("feature/x")
    assert_blocked(guard("git switch main && git commit -m x", repo.path), "`main`")
    assert_blocked(guard("git checkout main && git commit -m x", repo.path), "`main`")


def test_bare_push_uses_the_current_branch(repo: Repo) -> None:
    repo.checkout("main")
    assert_blocked(guard("git push", repo.path), "`main`")
    assert_blocked(guard("git push origin", repo.path), "`main`")
    repo.checkout("feature/x")
    assert_allowed(guard("git push", repo.path))
    assert_allowed(guard("git push origin", repo.path))


@pytest.mark.parametrize("command", ["git push", "git push origin feature/x"])
def test_push_checks_where_the_branch_actually_goes(repo: Repo, command: str) -> None:
    """With push.default=upstream, a branch made from origin/main pushes to main."""
    repo.git("push", "-q", "origin", "HEAD:refs/heads/feature/x")
    repo.git("config", "push.default", "upstream")
    repo.git("branch", "--set-upstream-to=origin/main")
    assert_blocked(guard(command, repo.path), "`main`")
    repo.git("branch", "--set-upstream-to=origin/feature/x")
    assert_allowed(guard(command, repo.path))


def test_upstream_push_really_does_land_on_main(repo: Repo) -> None:
    """Positive control for the test above: git itself, not just the guard."""
    repo.git("config", "push.default", "upstream")
    repo.git("branch", "--set-upstream-to=origin/main")
    (repo.path / "change.txt").write_text("x")
    repo.git("add", "change.txt")
    repo.git("commit", "-q", "-m", "change")
    result = subprocess.run(
        ["git", "-C", str(repo.path), "push", "origin", "feature/x"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "feature/x -> main" in result.stderr


def test_origin_default_branch_is_protected_whatever_its_name(repo: Repo) -> None:
    repo.git("push", "-q", "origin", "HEAD:trunk")
    repo.git("remote", "set-head", "origin", "trunk")
    assert_blocked(guard("git push origin trunk", repo.path), "`trunk`")
    assert_allowed(guard("git push origin trunk-feature", repo.path))


def test_broad_add_expands_to_the_untracked_files_it_would_stage(repo: Repo) -> None:
    (repo.path / ".env").write_text("TOKEN=x\n")
    assert_blocked(guard("git add -A", repo.path), ".env")
    assert_blocked(guard("git add .", repo.path), ".env")
    (repo.path / ".gitignore").write_text(".env\n")
    (repo.path / ".env.example").write_text("TOKEN=\n")
    assert_allowed(guard("git add -A", repo.path))
    assert_allowed(guard("git add .", repo.path))


def test_add_of_a_directory_sees_secrets_inside_it(repo: Repo) -> None:
    (repo.path / "deploy").mkdir()
    (repo.path / "deploy" / "id_rsa.pem").write_text("x")
    (repo.path / "src").mkdir()
    (repo.path / "src" / "app.py").write_text("x")
    assert_blocked(guard("git add deploy", repo.path), "id_rsa.pem")
    assert_allowed(guard("git add src", repo.path))


def test_force_add_sees_ignored_secrets(repo: Repo) -> None:
    (repo.path / ".gitignore").write_text("*.key\n")
    (repo.path / "conf").mkdir()
    (repo.path / "conf" / "tls.key").write_text("x")
    assert_allowed(guard("git add conf", repo.path))
    assert_blocked(guard("git add -f conf", repo.path), "tls.key")


@pytest.mark.parametrize("tool_name", ["shell", "developer__shell"])
def test_both_shell_tool_names_are_guarded(repo: Repo, tool_name: str) -> None:
    assert_blocked(guard("git push -f origin feature/x", repo.path, tool_name), "--force")
    assert_allowed(guard("git push origin feature/x", repo.path, tool_name))


@pytest.mark.parametrize("tool_name", ["write", "remote__shell_exec", "shell_exec", "todo__write"])
def test_non_shell_tools_are_allowed(repo: Repo, tool_name: str) -> None:
    assert_allowed(guard("git push -f origin main", repo.path, tool_name))
    assert_blocked(guard("git push -f origin main", repo.path, "shell"), "--force")


@pytest.mark.parametrize(
    "stdin",
    [
        "",
        "not json",
        "[1, 2]",
        '{"tool_name": "shell"}',
        '{"tool_name": "shell", "tool_input": null}',
        '{"tool_name": "shell", "tool_input": {"command": 42}}',
        '{"tool_name": "shell", "tool_input": {"command": "git push -f \\""}}',
        '{"tool_name": 7, "tool_input": {"command": "git push -f"}}',
    ],
)
def test_unusable_input_is_allowed_without_crashing(stdin: str) -> None:
    result = run_script("guard.py", stdin)
    assert result.returncode == 0
    assert result.stdout == ""
    assert "Traceback" not in result.stderr


def test_unusable_input_positive_control(repo: Repo) -> None:
    result = run_script(
        "guard.py",
        '{"tool_name": "shell", "tool_input": {"command": "git push -f"}, '
        f'"working_dir": "{repo.path}"}}',
    )
    assert_blocked(result, "--force")


def test_missing_working_dir_falls_back_to_cwd_without_crashing() -> None:
    result = run_script(
        "guard.py", {"tool_name": "shell", "tool_input": {"command": "git push -f x y"}}
    )
    assert_blocked(result, "--force")
    result = run_script("guard.py", {"tool_name": "shell", "tool_input": {"command": "git status"}})
    assert_allowed(result)


@pytest.mark.parametrize(
    "command",
    [
        "git",
        "git -C",
        "git push --",
        "git push -o",
        "git commit -m",
        "docker",
        "docker compose",
        "gh",
        "cd",
        "bash -c",
        "eval",
        "&&;|",
        ">",
        "git add",
        "(((",
        "$(",
    ],
)
def test_degenerate_commands_never_crash(repo: Repo, command: str) -> None:
    result = guard(command, repo.path)
    assert result.returncode in (0, 2)
    assert "Traceback" not in result.stderr
