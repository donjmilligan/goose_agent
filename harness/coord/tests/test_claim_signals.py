from __future__ import annotations

import pytest
from conftest import Repos, git

from harness_coord import coord
from harness_coord.gitcmd import CoordError


def test_matches_remote_branch_and_reports_totals_as_positive_control(repos: Repos):
    result = coord.claim_signals("harness-coord", repos.work)

    assert result["ok"] is True
    assert result["matched"] is True
    assert result["remote_branches"] == ["feature/harness-coord"]
    assert result["remote_branch_count"] == 4
    assert result["worktree_count"] == 1


def test_matching_ignores_case_and_separator_spelling(repos: Repos):
    result = coord.claim_signals("HARNESS slack", repos.work)

    assert result["remote_branches"] == ["feature/Harness_Slack"]


def test_no_match_is_distinguishable_from_reading_nothing(repos: Repos):
    result = coord.claim_signals("billing-export", repos.work)

    assert result["matched"] is False
    assert result["remote_branches"] == []
    assert result["worktrees"] == []
    # The positive control: everything was read, nothing matched.
    assert result["remote_branch_count"] == 4
    assert result["worktree_count"] == 1


def test_matches_an_unpushed_local_worktree(repos: Repos):
    wt = repos.work.parent / "proj.wt" / "billing-export"
    git(repos.work, "worktree", "add", "-b", "feature/billing-export", str(wt), "main")

    result = coord.claim_signals("billing export", repos.work)

    assert result["remote_branches"] == []
    assert [w["branch"] for w in result["worktrees"]] == ["feature/billing-export"]
    assert result["worktree_count"] == 2


def test_unreachable_origin_is_a_structured_error_not_an_empty_list(repos: Repos):
    git(repos.work, "remote", "set-url", "origin", str(repos.origin.parent / "gone.git"))

    with pytest.raises(CoordError) as excinfo:
        coord.claim_signals("harness", repos.work)

    assert excinfo.value.code == "network"
    assert "ls-remote" in excinfo.value.message


def test_missing_origin_remote_is_an_error(repos: Repos):
    git(repos.work, "remote", "remove", "origin")

    with pytest.raises(CoordError) as excinfo:
        coord.claim_signals("harness", repos.work)

    assert excinfo.value.code == "network"


@pytest.mark.parametrize("topic", ["", "   ", "-/_"])
def test_a_topic_that_would_match_everything_is_refused(repos: Repos, topic: str):
    with pytest.raises(CoordError) as excinfo:
        coord.claim_signals(topic, repos.work)

    assert excinfo.value.code == "invalid_argument"
