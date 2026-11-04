"""Deterministic coordination: claim signals, worktrees, hashes, changed paths.

Each function returns a plain JSON-serialisable dict on success and raises
`CoordError` on refusal or failure. The CLI prints that as
`{"ok": false, "error": {...}}` and exits non-zero; the MCP server returns it as
a tool error (`isError: true`) whose text is the same JSON.
"""

from __future__ import annotations

import re
import secrets
import string
from pathlib import Path
from typing import Any

from .gitcmd import NETWORK_TIMEOUT_S, CoordError, git_succeeds, run_git

HASH_ALPHABET = string.ascii_lowercase + string.digits
MAX_HASH_LENGTH = 64
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _normalise(text: str) -> str:
    # `harness coord`, `harness_coord` and `feature/harness-coord` should all
    # meet on the same spelling before the substring test.
    return re.sub(r"[\s_/.-]+", "-", text.strip().lower())


def _remote_branches(repo: str | Path) -> list[str]:
    out = run_git(
        repo, "ls-remote", "--heads", "origin", timeout=NETWORK_TIMEOUT_S, error_code="network"
    )
    return [line.split("refs/heads/", 1)[1] for line in out.splitlines() if "refs/heads/" in line]


def _parse_worktrees(porcelain: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in porcelain.splitlines():
        if not line:
            current = None
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current = {
                "path": value,
                "head": None,
                "branch": None,
                "detached": False,
                "bare": False,
                "locked": False,
                "prunable": False,
            }
            entries.append(current)
        elif current is None:
            continue
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key in ("detached", "bare", "locked", "prunable"):
            current[key] = True
    return entries


def worktree_list(repo: str | Path = ".") -> dict[str, Any]:
    worktrees = _parse_worktrees(run_git(repo, "worktree", "list", "--porcelain"))
    return {"ok": True, "count": len(worktrees), "worktrees": worktrees}


def claim_signals(topic: str, repo: str | Path = ".") -> dict[str, Any]:
    """Remote branches and local worktrees whose names match `topic`.

    The total counts are the positive control: `matches == []` with
    `remote_branch_count == 0` means nothing was read, not that nothing
    matched. A failed `ls-remote` raises rather than reporting an empty list.
    """
    needle = _normalise(topic)
    if not needle.strip("-"):
        raise CoordError("invalid_argument", "topic must contain at least one letter or digit")

    branches = _remote_branches(repo)
    worktrees = _parse_worktrees(run_git(repo, "worktree", "list", "--porcelain"))

    branch_matches = [b for b in branches if needle in _normalise(b)]
    worktree_matches = [
        w
        for w in worktrees
        if needle in _normalise(w["branch"] or "") or needle in _normalise(Path(w["path"]).name)
    ]
    return {
        "ok": True,
        "topic": topic,
        "matched": bool(branch_matches or worktree_matches),
        "remote_branch_count": len(branches),
        "worktree_count": len(worktrees),
        "remote_branches": branch_matches,
        "worktrees": worktree_matches,
    }


def _main_worktree_root(repo: str | Path) -> Path:
    # The first porcelain entry is always the main worktree, so calling this
    # from inside a linked worktree still yields `<main>.wt/`, not
    # `<main>.wt/<linked>.wt/`.
    worktrees = _parse_worktrees(run_git(repo, "worktree", "list", "--porcelain"))
    if not worktrees:
        raise CoordError("not_a_repo", f"{repo} has no worktrees")
    return Path(worktrees[0]["path"]).resolve()


def ensure_outside(target: Path, roots: list[Path]) -> None:
    """Refuse a target that resolves (symlinks included) inside any root."""
    resolved = target.resolve()
    for root in roots:
        root_resolved = root.resolve()
        if resolved == root_resolved or resolved.is_relative_to(root_resolved):
            raise CoordError(
                "path_inside_repo",
                f"refusing worktree at {resolved}: it is inside {root_resolved}. "
                "Worktrees must live outside every checkout.",
            )


def worktree_create(
    slug: str, branch: str, base: str = "origin/main", repo: str | Path = "."
) -> dict[str, Any]:
    """Create `<main-root>.wt/<slug>` on a new `branch` from `base`. Never pushes."""
    if not SLUG_RE.fullmatch(slug) or ".." in slug:
        raise CoordError(
            "invalid_argument",
            f"invalid slug {slug!r}: use 1-64 of [a-z0-9._-], starting with a letter or digit",
        )
    if not git_succeeds(repo, "check-ref-format", "--branch", branch):
        raise CoordError("invalid_argument", f"invalid branch name {branch!r}")

    root = _main_worktree_root(repo)
    existing = [Path(w["path"]) for w in worktree_list(repo)["worktrees"]]
    target = root.parent / f"{root.name}.wt" / slug
    # Existence first: an existing worktree at the target is itself one of the
    # checkouts, so the containment check would report it as inside itself.
    if target.exists():
        raise CoordError("path_exists", f"{target} already exists")
    ensure_outside(target, [root, *existing])

    if git_succeeds(root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"):
        raise CoordError("branch_exists", f"branch {branch!r} already exists locally")
    if _remote_branch_exists(root, branch):
        raise CoordError("branch_exists", f"branch {branch!r} already exists on origin")

    try:
        base_sha = run_git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").strip()
    except CoordError as exc:
        raise CoordError("invalid_argument", f"base {base!r} is not a known commit") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    # --no-track: with base `origin/main`, git would otherwise make main the
    # new branch's upstream, so a bare `git push` would aim at main.
    run_git(root, "worktree", "add", "--no-track", "-b", branch, str(target), base_sha)
    return {
        "ok": True,
        "path": str(target.resolve()),
        "branch": branch,
        "base": base,
        "base_sha": base_sha,
    }


def _remote_branch_exists(repo: Path, branch: str) -> bool:
    out = run_git(
        repo,
        "ls-remote",
        "--heads",
        "origin",
        f"refs/heads/{branch}",
        timeout=NETWORK_TIMEOUT_S,
        error_code="network",
    )
    return bool(out.strip())


def gen_hash(length: int = 4) -> dict[str, Any]:
    if not 1 <= length <= MAX_HASH_LENGTH:
        raise CoordError("invalid_argument", f"length must be 1..{MAX_HASH_LENGTH}, got {length}")
    value = "".join(secrets.choice(HASH_ALPHABET) for _ in range(length))
    return {"ok": True, "hash": value}


def _split_z(out: str) -> list[str]:
    return [p for p in out.split("\0") if p]


def _uncommitted(repo: str | Path) -> list[str]:
    fields = _split_z(run_git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all"))
    paths: list[str] = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        status, path = entry[:2], entry[3:]
        paths.append(path)
        # A rename/copy record is followed by its source path as its own field.
        i += 2 if status[0] in "RC" else 1
    return paths


def changed_paths(base: str = "origin/main", repo: str | Path = ".") -> dict[str, Any]:
    merge_base = run_git(repo, "merge-base", base, "HEAD").strip()
    committed = _split_z(run_git(repo, "diff", "--name-only", "-z", merge_base, "HEAD"))
    uncommitted = _uncommitted(repo)
    return {
        "ok": True,
        "base": base,
        "merge_base": merge_base,
        "committed": committed,
        "uncommitted": uncommitted,
        "paths": sorted(set(committed) | set(uncommitted)),
    }
