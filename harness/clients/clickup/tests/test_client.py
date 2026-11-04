from unittest.mock import MagicMock

import pytest

from harness_clickup.client import ClickUpAPIError, ClickUpClient, SilentStatusChange
from harness_clickup.config import ClickUpConfig


def _config(**overrides):
    defaults = {"token": "pk_test", "timeout_seconds": 5.0, "max_retries": 3}
    return ClickUpConfig(**{**defaults, **overrides})


def _response(status, json_body=None, headers=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.ok = 200 <= status < 300
    resp.headers = headers or {}
    resp.json.return_value = json_body if json_body is not None else {}
    resp.content = b"{}" if json_body is not None else b""
    resp.text = text
    return resp


def test_create_task_sends_expected_payload():
    session = MagicMock()
    session.request.return_value = _response(200, {"id": "abc123", "name": "x", "tags": []})
    client = ClickUpClient(_config(), session=session)

    task = client.create_task(
        "list1", name="x", description="d", tags=["t1"], due_date_ms=123, parent="p1"
    )

    assert task.id == "abc123"
    _, kwargs = session.request.call_args
    assert kwargs["json"] == {
        "name": "x",
        "description": "d",
        "tags": ["t1"],
        "due_date_time": False,
        "due_date": 123,
        "parent": "p1",
    }
    assert kwargs["headers"]["Authorization"] == "pk_test"


def test_create_task_includes_priority_when_given():
    session = MagicMock()
    session.request.return_value = _response(200, {"id": "abc123", "name": "x", "tags": []})
    client = ClickUpClient(_config(), session=session)

    client.create_task("list1", name="x", priority=2)

    _, kwargs = session.request.call_args
    assert kwargs["json"]["priority"] == 2


def test_create_task_omits_priority_by_default():
    session = MagicMock()
    session.request.return_value = _response(200, {"id": "abc123", "name": "x", "tags": []})
    client = ClickUpClient(_config(), session=session)

    client.create_task("list1", name="x")

    _, kwargs = session.request.call_args
    assert "priority" not in kwargs["json"]


def test_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr("harness_clickup.client.time.sleep", lambda _s: None)
    session = MagicMock()
    session.request.side_effect = [
        _response(429, headers={"Retry-After": "1"}),
        _response(200, {"id": "abc", "name": "x", "tags": []}),
    ]
    client = ClickUpClient(_config(), session=session)

    task = client.create_task("list1", name="x")

    assert task.id == "abc"
    assert session.request.call_count == 2


def test_exhausts_retries_and_raises_on_persistent_429(monkeypatch):
    monkeypatch.setattr("harness_clickup.client.time.sleep", lambda _s: None)
    session = MagicMock()
    session.request.return_value = _response(429, headers={"Retry-After": "1"})
    client = ClickUpClient(_config(max_retries=2), session=session)

    with pytest.raises(ClickUpAPIError):
        client.create_task("list1", name="x")

    assert session.request.call_count == 2


def test_raises_clickup_api_error_on_4xx():
    session = MagicMock()
    session.request.return_value = _response(404, text="not found")
    client = ClickUpClient(_config(), session=session)

    with pytest.raises(ClickUpAPIError) as exc_info:
        client.get_task("missing")

    assert exc_info.value.status == 404


def test_delete_task_hits_expected_endpoint():
    session = MagicMock()
    session.request.return_value = _response(200)
    client = ClickUpClient(_config(), session=session)

    client.delete_task("task123")

    args, _ = session.request.call_args
    assert args[0] == "DELETE"
    assert args[1].endswith("/task/task123")


def test_get_tasks_paginates_until_short_page(monkeypatch):
    monkeypatch.setattr("harness_clickup.client._PAGE_SIZE", 1)
    session = MagicMock()
    session.request.side_effect = [
        _response(200, {"tasks": [{"id": "1", "name": "a", "tags": []}]}),
        _response(200, {"tasks": []}),
    ]
    client = ClickUpClient(_config(), session=session)

    tasks = client.get_tasks("list1")

    assert [t.id for t in tasks] == ["1"]
    assert session.request.call_count == 2


def test_ensure_tag_swallows_already_exists_error():
    session = MagicMock()
    session.request.return_value = _response(400, text="tag already exists")
    client = ClickUpClient(_config(), session=session)

    client.ensure_tag("space1", "frontend")  # should not raise


def test_ensure_tag_reraises_unexpected_error(monkeypatch):
    monkeypatch.setattr("harness_clickup.client.time.sleep", lambda _s: None)
    session = MagicMock()
    session.request.return_value = _response(500, text="server error")
    client = ClickUpClient(_config(max_retries=1), session=session)

    with pytest.raises(ClickUpAPIError):
        client.ensure_tag("space1", "frontend")


def test_update_task_sends_only_provided_fields():
    session = MagicMock()
    session.request.return_value = _response(200, {"id": "t1", "name": "x", "tags": []})
    client = ClickUpClient(_config(), session=session)

    # allow_silent_status: this test is about the PUT payload, not the
    # comment-before-status gate, which has its own tests below.
    client.update_task("t1", status="complete", allow_silent_status=True)

    args, kwargs = session.request.call_args
    assert args[0] == "PUT"
    assert args[1].endswith("/task/t1")
    assert kwargs["json"] == {"status": "complete"}


def test_update_task_includes_priority_when_given():
    session = MagicMock()
    session.request.return_value = _response(200, {"id": "t1", "name": "x", "tags": []})
    client = ClickUpClient(_config(), session=session)

    client.update_task("t1", priority=1)

    _, kwargs = session.request.call_args
    assert kwargs["json"] == {"priority": 1}


def test_add_tag_to_task_hits_expected_endpoint():
    session = MagicMock()
    session.request.return_value = _response(200)
    client = ClickUpClient(_config(), session=session)

    client.add_tag_to_task("t1", "duplicate")

    args, _ = session.request.call_args
    assert args[0] == "POST"
    assert args[1].endswith("/task/t1/tag/duplicate")


def test_add_comment_returns_new_comment_id():
    session = MagicMock()
    session.request.return_value = _response(200, {"id": 555})
    client = ClickUpClient(_config(), session=session)

    comment_id = client.add_comment("t1", "Superseded by X")

    assert comment_id == "555"
    _, kwargs = session.request.call_args
    assert kwargs["json"] == {"comment_text": "Superseded by X", "notify_all": False}


def test_add_task_link_hits_expected_endpoint():
    session = MagicMock()
    session.request.return_value = _response(200)
    client = ClickUpClient(_config(), session=session)

    client.add_task_link("old1", "new1")

    args, _ = session.request.call_args
    assert args[0] == "POST"
    assert args[1].endswith("/task/old1/link/new1")


# --- a status change requires a comment -------------------------------------


def _guard_client():
    session = MagicMock()
    session.request.return_value = _response(
        200, {"id": "t1", "name": "x", "tags": [], "status": {"status": "needs review"}}
    )
    return ClickUpClient(_config(), session=session), session


def test_status_change_without_a_comment_is_refused():
    client, session = _guard_client()

    with pytest.raises(SilentStatusChange) as excinfo:
        client.update_task("t1", status="needs review")

    message = str(excinfo.value)
    assert "t1" in message
    assert "needs review" in message
    # Actionable, not merely a refusal.
    assert "add_comment" in message
    assert "allow_silent_status" in message
    # And nothing was sent: a guard that refuses after the write is no guard.
    session.request.assert_not_called()


def test_status_change_is_allowed_after_commenting_on_that_task():
    client, session = _guard_client()

    client.add_comment("t1", "Result: done, evidence below.")
    client.update_task("t1", status="needs review")

    # Positive half, so this cannot pass by the PUT never happening.
    methods = [call.args[0] for call in session.request.call_args_list]
    assert methods == ["POST", "PUT"]


def test_a_comment_on_one_task_does_not_unlock_another():
    """The guard is per task -- commenting on t1 must not free t2."""
    client, _session = _guard_client()
    client.add_comment("t1", "unrelated")

    with pytest.raises(SilentStatusChange):
        client.update_task("t2", status="complete")


def test_non_status_updates_never_need_a_comment():
    """Renaming or re-describing is not a status flip. Don't gate it."""
    client, session = _guard_client()

    client.update_task("t1", name="[DUPLICATE of x] n")
    client.update_task("t1", description="d")
    client.update_task("t1", priority=2)

    assert session.request.call_count == 3


def test_explicit_escape_hatch_allows_a_silent_flip():
    client, session = _guard_client()

    client.update_task("t1", status="complete", allow_silent_status=True)

    methods = [call.args[0] for call in session.request.call_args_list]
    assert methods == ["PUT"]
