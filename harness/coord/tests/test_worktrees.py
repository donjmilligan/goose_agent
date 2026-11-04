from __future__ import annotations

from pathlib import Path

import pytest
from conftest import Repos, git

from harness_coord import coord
from harness_coord.gitcmd import CoordError


def test_creates_sibling_wt_dir_on_new_branch_from_base(repos: Repos):
    result = coord.worktree_create("my-task", "feature/my-task", repo=repos.work)

    expected = repos.work.parent / "proj.wt" / "my-task"
    assert result["ok"] is True
    assert result["path"] == str(expected)
    assert result["branch"] == "feature/my-task"
    assert result["base_sha"] == git(repos.work, "rev-parse", "origin/main").strip()
    assert git(expected, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature/my-task"
    assert (expected / "README.md").read_text() == "hello\n"


def test_new_branch_has_no_upstream_and_is_not_pushed(repos: Repos):
    result = coord.worktree_create("my-task", "feature/my-task", repo=repos.work)

    upstream = git(
        Path(result["path"]), "for-each-ref", "--format=%(upstream)", "refs/heads/feature/my-task"
    )
    assert upstream.strip() == ""
    remote = git(repos.work, "ls-remote", "--heads", "origin")
    assert "refs/heads/main" in remote
    assert "feature/my-task" not in remote


def test_called_from_a_linked_worktree_still_targets_the_main_checkouts_wt_dir(repos: Repos):
    first = coord.worktree_create("first", "feature/first", repo=repos.work)

    second = coord.worktree_create("second", "feature/second", repo=first["path"])

    assert second["path"] == str(repos.work.parent / "proj.wt" / "second")


def test_refuses_a_target_that_resolves_inside_the_repo(repos: Repos):
    """The vanta incident: worktrees nested inside the checkout. Here `proj.wt`
    is a symlink pointing back into the repo, so only symlink resolution sees it."""
    nested = repos.work / "nested-worktrees"
    nested.mkdir()
    (repos.work.parent / "proj.wt").symlink_to(nested)

    with pytest.raises(CoordError) as excinfo:
        coord.worktree_create("sneaky", "feature/sneaky", repo=repos.work)

    assert excinfo.value.code == "path_inside_repo"
    assert not (nested / "sneaky").exists()
    assert "feature/sneaky" not in git(repos.work, "branch", "--list")


def test_ensure_outside_allows_a_sibling_and_refuses_a_child(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()

    coord.ensure_outside(tmp_path / "proj.wt" / "x", [root])
    with pytest.raises(CoordError):
        coord.ensure_outside(root / "x", [root])
    with pytest.raises(CoordError):
        coord.ensure_outside(root, [root])


def test_refuses_a_branch_that_exists_on_origin(repos: Repos):
    with pytest.raises(CoordError) as excinfo:
        coord.worktree_create("coord", "feature/harness-coord", repo=repos.work)

    assert excinfo.value.code == "branch_exists"
    assert "origin" in excinfo.value.message
    assert not (repos.work.parent / "proj.wt" / "coord").exists()


def test_refuses_a_branch_that_exists_only_locally(repos: Repos):
    git(repos.work, "branch", "feature/local-only")

    with pytest.raises(CoordError) as excinfo:
        coord.worktree_create("local", "feature/local-only", repo=repos.work)

    assert excinfo.value.code == "branch_exists"
    assert "locally" in excinfo.value.message


def test_refuses_when_origin_cannot_be_checked(repos: Repos):
    git(repos.work, "remote", "set-url", "origin", str(repos.origin.parent / "gone.git"))

    with pytest.raises(CoordError) as excinfo:
        coord.worktree_create("x", "feature/x", base="main", repo=repos.work)

    assert excinfo.value.code == "network"


def test_refuses_an_existing_target_path(repos: Repos):
    (repos.work.parent / "proj.wt" / "taken").mkdir(parents=True)

    with pytest.raises(CoordError) as excinfo:
        coord.worktree_create("taken", "feature/taken", repo=repos.work)

    assert excinfo.value.code == "path_exists"


@pytest.mark.parametrize(
    "slug", ["", "../escape", "a/b", "-x", ".hidden", "UPPER", "a..b", "a" * 65]
)
def test_refuses_invalid_slugs(repos: Repos, slug: str):
    with pytest.raises(CoordError) as excinfo:
        coord.worktree_create(slug, "feature/ok", repo=repos.work)

    assert excinfo.value.code == "invalid_argument"


def test_refuses_an_unknown_base(repos: Repos):
    with pytest.raises(CoordError) as excinfo:
        coord.worktree_create("x", "feature/x", base="origin/nope", repo=repos.work)

    assert excinfo.value.code == "invalid_argument"
    assert "origin/nope" in excinfo.value.message


def test_refuses_an_invalid_branch_name(repos: Repos):
    with pytest.raises(CoordError) as excinfo:
        coord.worktree_create("x", "bad..branch", repo=repos.work)

    assert excinfo.value.code == "invalid_argument"


def test_worktree_list_reports_every_worktree(repos: Repos):
    coord.worktree_create("listed", "feature/listed", repo=repos.work)

    result = coord.worktree_list(repos.work)

    assert result["count"] == 2
    assert [w["branch"] for w in result["worktrees"]] == ["main", "feature/listed"]
    assert result["worktrees"][0]["path"] == str(repos.work)
    assert all(len(w["head"]) == 40 for w in result["worktrees"])


def test_worktree_list_outside_a_repo_is_an_error(tmp_path: Path):
    with pytest.raises(CoordError) as excinfo:
        coord.worktree_list(tmp_path)

    assert excinfo.value.code == "git_failed"
