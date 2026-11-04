from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import Repos, git

from harness_coord import coord
from harness_coord.gitcmd import CoordError


def test_gen_hash_default_is_four_lowercase_alphanumerics():
    value = coord.gen_hash()["hash"]

    assert re.fullmatch(r"[a-z0-9]{4}", value), value


def test_gen_hash_honours_length_and_varies():
    values = {coord.gen_hash(12)["hash"] for _ in range(20)}

    assert all(re.fullmatch(r"[a-z0-9]{12}", v) for v in values)
    assert len(values) == 20


@pytest.mark.parametrize("length", [0, -1, 65])
def test_gen_hash_refuses_out_of_range_lengths(length: int):
    with pytest.raises(CoordError) as excinfo:
        coord.gen_hash(length)

    assert excinfo.value.code == "invalid_argument"


def test_changed_paths_splits_committed_from_uncommitted(repos: Repos):
    wt = coord.worktree_create("change", "feature/change", repo=repos.work)["path"]
    root = Path(wt)
    (root / "src").mkdir()
    (root / "src" / "committed.py").write_text("x = 1\n")
    git(root, "add", "src/committed.py")
    git(root, "commit", "-m", "add committed")
    (root / "README.md").write_text("edited\n")
    (root / "staged.txt").write_text("s\n")
    git(root, "add", "staged.txt")
    (root / "new dir").mkdir()
    (root / "new dir" / "untracked file.txt").write_text("u\n")

    result = coord.changed_paths(repo=root)

    assert result["merge_base"] == git(repos.work, "rev-parse", "origin/main").strip()
    assert result["committed"] == ["src/committed.py"]
    assert sorted(result["uncommitted"]) == [
        "README.md",
        "new dir/untracked file.txt",
        "staged.txt",
    ]
    assert result["paths"] == [
        "README.md",
        "new dir/untracked file.txt",
        "src/committed.py",
        "staged.txt",
    ]


def test_changed_paths_reports_a_staged_rename_once_by_its_new_name(repos: Repos):
    git(repos.work, "mv", "README.md", "RENAMED.md")

    result = coord.changed_paths(base="origin/main", repo=repos.work)

    assert result["uncommitted"] == ["RENAMED.md"]
    assert result["committed"] == []


def test_changed_paths_clean_branch_has_no_paths_but_a_real_merge_base(repos: Repos):
    result = coord.changed_paths(repo=repos.work)

    assert result["paths"] == []
    assert re.fullmatch(r"[0-9a-f]{40}", result["merge_base"])


def test_changed_paths_unknown_base_is_an_error(repos: Repos):
    with pytest.raises(CoordError) as excinfo:
        coord.changed_paths(base="origin/nope", repo=repos.work)

    assert excinfo.value.code == "git_failed"
