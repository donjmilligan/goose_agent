import pytest
from fake_clickup import make_client

from harness_clickup import tools
from harness_clickup.tools import ToolRefused

# --- set_status: comment first, then status ----------------------------------


def test_set_status_posts_comment_before_changing_status():
    client, session = make_client()

    result = tools.set_status(client, "t1", "in review", "Done: tests green, see abc123.")

    assert session.writes == [
        (
            "POST",
            "/task/t1/comment",
            {"comment_text": "Done: tests green, see abc123.", "notify_all": False},
        ),
        ("PUT", "/task/t1", {"status": "in review"}),
    ]
    assert result["comment_id"] == "777"
    assert result["previous_status"] == "in progress"
    assert result["task"]["status"] == "in review"


def test_set_status_reads_task_and_list_statuses_before_any_write():
    client, session = make_client()

    tools.set_status(client, "t1", "in review", "moving to review")

    first_write = next(i for i, c in enumerate(session.calls) if c[0] != "GET")
    assert [c[:2] for c in session.calls[:first_write]] == [
        ("GET", "/task/t1"),
        ("GET", "/list/L1"),
    ]


def test_set_status_uses_list_spelling_of_status():
    client, session = make_client()

    tools.set_status(client, "t1", "  In Review ", "moving to review")

    assert session.writes[-1] == ("PUT", "/task/t1", {"status": "in review"})


@pytest.mark.parametrize("comment", ["", "   ", "\n\t"])
def test_set_status_refuses_empty_comment(comment):
    client, session = make_client()

    with pytest.raises(ToolRefused, match="needs a comment"):
        tools.set_status(client, "t1", "in review", comment)

    assert session.calls == []
    tools.set_status(client, "t1", "in review", "real reason")
    assert len(session.writes) == 2


@pytest.mark.parametrize("status", ["done", "complete"])
def test_set_status_refuses_done_and_closed_types(status):
    client, session = make_client()

    with pytest.raises(ToolRefused, match="ceiling is review"):
        tools.set_status(client, "t1", status, "finished")

    assert session.writes == []
    assert ("GET", "/list/L1", None) in session.calls


@pytest.mark.parametrize("status", ["done", "complete"])
def test_set_status_allows_done_and_closed_when_allowed(status):
    client, session = make_client()

    tools.set_status(client, "t1", status, "finished", allow_done=True)

    assert [w[:2] for w in session.writes] == [("POST", "/task/t1/comment"), ("PUT", "/task/t1")]
    assert session.writes[-1][2] == {"status": status}


def test_set_status_refuses_unknown_status_without_writing():
    client, session = make_client()

    with pytest.raises(ToolRefused, match="not a status on list L1") as excinfo:
        tools.set_status(client, "t1", "shipped", "reason")

    assert "'in review'" in str(excinfo.value)
    assert session.writes == []


def test_set_status_refuses_task_without_list():
    client, session = make_client()
    del session.task["list"]

    with pytest.raises(ToolRefused, match="has no list"):
        tools.set_status(client, "t1", "in review", "reason")

    assert session.writes == []
    assert session.calls == [("GET", "/task/t1", None)]


def test_allow_done_from_env(monkeypatch):
    assert tools.allow_done_from_env() is False
    monkeypatch.setenv("HARNESS_ALLOW_DONE", "1")
    assert tools.allow_done_from_env() is True
    monkeypatch.setenv("HARNESS_ALLOW_DONE", "true")
    assert tools.allow_done_from_env() is False


# --- update_task has no status path ------------------------------------------


def test_update_task_sends_only_given_fields():
    client, session = make_client()

    result = tools.update_task(client, "t1", name="Renamed", priority=2)

    assert session.writes == [("PUT", "/task/t1", {"name": "Renamed", "priority": 2})]
    assert result["name"] == "Renamed"


def test_update_task_cannot_change_status():
    client, _session = make_client()

    with pytest.raises(TypeError):
        tools.update_task(client, "t1", status="done")  # type: ignore[call-arg]
    assert tools.update_task(client, "t1", description="d")["description"] == "d"


def test_update_task_refuses_empty_update_and_bad_priority():
    client, session = make_client()

    with pytest.raises(ToolRefused, match="at least one"):
        tools.update_task(client, "t1")
    with pytest.raises(ToolRefused, match="priority"):
        tools.update_task(client, "t1", priority=5)

    assert session.calls == []
    tools.update_task(client, "t1", priority=4)
    assert session.writes == [("PUT", "/task/t1", {"priority": 4})]


# --- reads and simple writes -------------------------------------------------


def test_get_task_returns_plain_dict():
    client, _session = make_client()

    task = tools.get_task(client, "t1")

    assert task == {
        "id": "t1",
        "name": "Task one",
        "description": "",
        "tags": ["harness"],
        "parent": None,
        "url": "https://app.clickup.com/t/t1",
        "status": "in progress",
        "list_id": "L1",
    }


def test_list_tasks_excludes_closed_by_default():
    client, session = make_client()

    tasks = tools.list_tasks(client, "L1")

    assert [t["id"] for t in tasks] == ["t1"]
    assert session.params == [{"include_closed": "false", "subtasks": "true", "page": 0}]
    tools.list_tasks(client, "L1", include_closed=True)
    assert session.params[-1]["include_closed"] == "true"


def test_get_comments_and_list_statuses():
    client, _session = make_client()

    assert tools.get_comments(client, "t1") == [
        {"id": "c1", "text": "hi", "user": "", "date_ms": 5}
    ]
    statuses = tools.list_statuses(client, "L1")
    assert [(s["status"], s["type"]) for s in statuses][-2:] == [
        ("done", "done"),
        ("complete", "closed"),
    ]


def test_create_task_passes_parent_and_tags():
    client, session = make_client()

    created = tools.create_task(client, "L1", "Sub", "desc", parent="t1", tags=["a"])

    assert created["id"] == "t2"
    assert session.writes == [
        (
            "POST",
            "/list/L1/task",
            {
                "name": "Sub",
                "description": "desc",
                "tags": ["a"],
                "due_date_time": False,
                "parent": "t1",
            },
        )
    ]


def test_create_task_and_add_comment_refuse_blank_text():
    client, session = make_client()

    with pytest.raises(ToolRefused):
        tools.create_task(client, "L1", "  ")
    with pytest.raises(ToolRefused):
        tools.add_comment(client, "t1", " ")

    assert session.calls == []
    assert tools.add_comment(client, "t1", "note") == {"task_id": "t1", "comment_id": "777"}
