from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from conftest import Repos, git
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, TextContent

from harness_coord import TOOL_NAMES, cli, server

VACUOUS = """
def test_does_not_write():
    calls = []
    run(lambda x: calls.append(x))
    assert calls == []
"""


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_verify_assertions_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "test_ok.py").write_text("def test_ok():\n    assert 1\n")
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "test_bad.py").write_text(VACUOUS)
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "test_broken.py").write_text("def x(:\n")

    code, out, _ = _run(capsys, "verify-assertions", str(clean))
    assert code == 0
    assert "clean (1 file(s) scanned" in out

    code, out, err = _run(capsys, "verify-assertions", str(dirty))
    assert code == 1
    assert "test_bad.py:5  test_does_not_write" in err
    assert out == ""

    code, _, err = _run(capsys, "verify-assertions", str(broken))
    assert code == 2
    assert "could not parse" in err

    code, _, err = _run(capsys, "verify-assertions", str(tmp_path / "missing"))
    assert code == 2
    assert "no such path" in err


def test_verify_assertions_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    (tmp_path / "test_bad.py").write_text(VACUOUS)

    code, out, _ = _run(capsys, "verify-assertions", "--json", str(tmp_path))

    payload = json.loads(out)
    assert code == 1
    assert payload["files_scanned"] == 1
    assert [f["line"] for f in payload["findings"]] == [5]


def test_verify_assertions_repo_resolves_relative_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    repo = tmp_path / "proj.wt" / "story"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_bad.py").write_text(VACUOUS)
    monkeypatch.chdir(tmp_path)

    code, out, _ = _run(
        capsys, "verify-assertions", "--json", "--repo", str(repo), "tests/test_bad.py"
    )
    missing_code, _, missing_err = _run(capsys, "verify-assertions", "tests/test_bad.py")

    payload = json.loads(out)
    assert code == 1
    assert payload["files_scanned"] == 1
    assert [f["test_name"] for f in payload["findings"]] == ["test_does_not_write"]
    assert missing_code == 2
    assert "no such path: tests/test_bad.py" in missing_err


def test_usage_error_exits_2(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["no-such-command"])

    assert excinfo.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_gen_hash_prints_only_the_hash(capsys: pytest.CaptureFixture[str]):
    code, out, _ = _run(capsys, "gen-hash", "--length", "6")

    assert code == 0
    assert re.fullmatch(r"[a-z0-9]{6}\n", out), out


def test_claim_signals_cli_prints_json(repos: Repos, capsys: pytest.CaptureFixture[str]):
    code, out, _ = _run(capsys, "claim-signals", "harness", "--repo", str(repos.work))

    payload = json.loads(out)
    assert code == 0
    assert sorted(payload["remote_branches"]) == ["feature/Harness_Slack", "feature/harness-coord"]
    assert payload["remote_branch_count"] == 4


def test_refusal_exits_1_with_structured_error(repos: Repos, capsys: pytest.CaptureFixture[str]):
    nested = repos.work / "inside"
    nested.mkdir()
    (repos.work.parent / "proj.wt").symlink_to(nested)

    code, out, _ = _run(capsys, "worktree-create", "x", "feature/x", "--repo", str(repos.work))

    payload = json.loads(out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["error"]["code"] == "path_inside_repo"


def test_worktree_create_then_list_and_changed_paths_cli(
    repos: Repos, capsys: pytest.CaptureFixture[str]
):
    code, out, _ = _run(capsys, "worktree-create", "cli", "feature/cli", "--repo", str(repos.work))
    path = json.loads(out)["path"]
    assert code == 0

    code, out, _ = _run(capsys, "worktree-list", "--repo", str(repos.work))
    assert code == 0
    assert [w["path"] for w in json.loads(out)["worktrees"]] == [str(repos.work), path]

    (Path(path) / "new.txt").write_text("n\n")
    code, out, _ = _run(capsys, "changed-paths", "--repo", path)
    assert code == 0
    assert json.loads(out)["paths"] == ["new.txt"]


def _tools() -> dict[str, object]:
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}


def test_server_exposes_exactly_the_contracted_tools():
    assert server.mcp.name == "coord"
    assert sorted(_tools()) == sorted(TOOL_NAMES)
    assert TOOL_NAMES == (
        "claim_signals",
        "worktree_create",
        "worktree_list",
        "gen_hash",
        "changed_paths",
        "verify_assertions",
    )


async def _client_call(name: str, args: dict[str, object]) -> CallToolResult:
    async with create_connected_server_and_client_session(server.mcp) as session:
        return await session.call_tool(name, args)


def _call(name: str, args: dict[str, object]) -> CallToolResult:
    return asyncio.run(_client_call(name, args))


def _ok(name: str, args: dict[str, object]) -> dict[str, Any]:
    result = _call(name, args)
    assert result.isError is False, result.content
    assert result.structuredContent is not None
    return result.structuredContent


def _refusal(name: str, args: dict[str, object]) -> dict[str, Any]:
    """A refusal reaches the client as a tool error whose text carries the envelope."""
    result = _call(name, args)
    assert result.isError is True
    assert result.structuredContent is None
    (content,) = result.content
    assert isinstance(content, TextContent)
    prefix = f"Error executing tool {name}: "
    assert content.text.startswith(prefix), content.text
    return json.loads(content.text.removeprefix(prefix))


def test_server_refusal_is_a_tool_error_carrying_the_code(repos: Repos):
    payload = _refusal(
        "worktree_create", {"slug": "../bad", "branch": "feature/bad", "repo": str(repos.work)}
    )

    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_argument"
    assert "invalid slug" in payload["error"]["message"]


def test_server_returns_structured_error_on_network_failure(repos: Repos):
    git(repos.work, "remote", "set-url", "origin", str(repos.origin.parent / "gone.git"))

    payload = _refusal("claim_signals", {"topic": "harness", "repo": str(repos.work)})

    assert payload["ok"] is False
    assert payload["error"]["code"] == "network"
    assert "remote_branches" not in payload


def test_server_success_and_verify_paths(repos: Repos, tmp_path: Path):
    ok = _ok("claim_signals", {"topic": "harness-coord", "repo": str(repos.work)})
    (tmp_path / "test_bad.py").write_text(VACUOUS)
    scanned = _ok("verify_assertions", {"paths": [str(tmp_path)]})
    empty = _refusal("verify_assertions", {"paths": []})

    assert ok["ok"] is True
    assert ok["remote_branches"] == ["feature/harness-coord"]
    assert scanned["clean"] is False
    assert scanned["files_scanned"] == 1
    assert empty["ok"] is False
    assert empty["error"]["code"] == "invalid_argument"


def test_server_verify_assertions_resolves_relative_paths_against_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """D12: changed_paths(repo=...) returns repo-relative paths; feeding them to
    verify_assertions from a session started elsewhere must still find them."""
    repo = tmp_path / "proj.wt" / "story"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_bad.py").write_text(VACUOUS)
    monkeypatch.chdir(tmp_path)

    scanned = _ok("verify_assertions", {"paths": ["tests/test_bad.py"], "repo": str(repo)})
    unanchored = _ok("verify_assertions", {"paths": ["tests/test_bad.py"]})

    assert scanned["ok"] is True
    assert scanned["files_scanned"] == 1
    assert [f["test_name"] for f in scanned["findings"]] == ["test_does_not_write"]
    assert unanchored["ok"] is False
    assert unanchored["missing"] == ["tests/test_bad.py"]


def test_server_verify_assertions_repo_is_optional_and_defaults_to_cwd():
    (tool,) = [t for t in asyncio.run(server.mcp.list_tools()) if t.name == "verify_assertions"]
    schema = tool.inputSchema

    assert schema["required"] == ["paths"]
    assert schema["properties"]["repo"]["default"] == "."
