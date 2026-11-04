"""stdio MCP server `coord`. Goose exposes these as `coord__<tool>`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import assertions, coord
from .gitcmd import CoordError

mcp = FastMCP("coord")


def _call(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return fn()
    except CoordError as exc:
        return {"ok": False, "error": exc.as_dict()}


@mcp.tool()
def claim_signals(topic: str, repo: str = ".") -> dict[str, Any]:
    """Remote branches (`git ls-remote --heads origin`) and local worktrees whose names
    match `topic` (case-insensitive; `-`, `_`, `/`, `.` and spaces are equivalent).

    `remote_branch_count` and `worktree_count` are the positive control: no match with
    counts > 0 means "read everything, nothing matched". A failed ls-remote returns
    `ok: false` with an error, never an empty list."""
    return _call(lambda: coord.claim_signals(topic, repo))


@mcp.tool()
def worktree_create(
    slug: str, branch: str, base: str = "origin/main", repo: str = "."
) -> dict[str, Any]:
    """Create a worktree at `<main-checkout>.wt/<slug>` on a new `branch` from `base`.

    Refuses: an invalid slug, a target that resolves inside any checkout, an existing
    target path, or a branch that already exists locally or on origin. Does not push."""
    return _call(lambda: coord.worktree_create(slug, branch, base, repo))


@mcp.tool()
def worktree_list(repo: str = ".") -> dict[str, Any]:
    """Every worktree of `repo` (path, head, branch, flags) from `git worktree list`."""
    return _call(lambda: coord.worktree_list(repo))


@mcp.tool()
def gen_hash(length: int = 4) -> dict[str, Any]:
    """A fresh random lowercase-alphanumeric id from `secrets` (e.g. for decision records).
    Use this instead of inventing one."""
    return _call(lambda: coord.gen_hash(length))


@mcp.tool()
def changed_paths(base: str = "origin/main", repo: str = ".") -> dict[str, Any]:
    """Files changed between merge-base(base, HEAD) and HEAD (`committed`), plus staged,
    unstaged and untracked files (`uncommitted`); `paths` is the sorted union."""
    return _call(lambda: coord.changed_paths(base, repo))


@mcp.tool()
def verify_assertions(paths: list[str]) -> dict[str, Any]:
    """Scan Python tests for spy collectors asserted empty with no positive assertion
    proving the code ran. Directories are searched for `test_*.py`. A deliberate absence
    assertion is exempted by `# absence-ok: <reason>` on the line before; a bare pragma is
    not accepted. `files_scanned` is the positive control."""
    if not paths:
        return {"ok": False, "error": {"code": "invalid_argument", "message": "paths is empty"}}
    return assertions.scan(paths).as_dict()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
