"""Regression tests for defects found while porting the vanta-lms client."""

from unittest.mock import MagicMock

import pytest
import requests

from harness_clickup.client import ClickUpAPIError, ClickUpClient
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


_TASK = {"id": "t1", "name": "x", "tags": []}


@pytest.fixture
def sleeps(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr("harness_clickup.client.time.sleep", calls.append)
    return calls


# --- (a) 5xx retried only for idempotent methods -----------------------------


def test_post_is_not_retried_on_5xx(sleeps):
    session = MagicMock()
    session.request.side_effect = [_response(502, text="bad gateway"), _response(200, _TASK)]
    client = ClickUpClient(_config(), session=session)

    with pytest.raises(ClickUpAPIError) as excinfo:
        client.create_task("list1", name="x")

    assert excinfo.value.status == 502
    assert session.request.call_count == 1


@pytest.mark.parametrize(
    ("method", "call"),
    [
        ("GET", lambda c: c.get_task("t1")),
        ("PUT", lambda c: c.update_task("t1", name="n")),
        ("DELETE", lambda c: c.delete_task("t1")),
    ],
)
def test_idempotent_methods_are_retried_on_5xx(sleeps, method, call):
    session = MagicMock()
    session.request.side_effect = [_response(503, text="unavailable"), _response(200, _TASK)]
    client = ClickUpClient(_config(), session=session)

    call(client)

    assert [c.args[0] for c in session.request.call_args_list] == [method, method]
    assert len(sleeps) == 1


def test_post_is_still_retried_on_429(sleeps):
    """A rate-limited request was rejected before processing, so it is safe to resend."""
    session = MagicMock()
    session.request.side_effect = [
        _response(429, headers={"Retry-After": "1"}),
        _response(200, _TASK),
    ]
    client = ClickUpClient(_config(), session=session)

    assert client.create_task("list1", name="x").id == "t1"
    assert session.request.call_count == 2


# --- (b) transport errors retried with backoff -------------------------------


@pytest.mark.parametrize(
    "error", [requests.ConnectionError("reset"), requests.Timeout("read timed out")]
)
def test_get_retries_transport_errors_with_backoff(sleeps, error):
    session = MagicMock()
    session.request.side_effect = [error, error, _response(200, _TASK)]
    client = ClickUpClient(_config(), session=session)

    assert client.get_task("t1").id == "t1"
    assert session.request.call_count == 3
    assert sleeps == [1, 2]


def test_post_is_not_resent_after_read_timeout(sleeps):
    """The server may have created the task before the response was lost."""
    session = MagicMock()
    session.request.side_effect = [requests.ReadTimeout("read timed out"), _response(200, _TASK)]
    client = ClickUpClient(_config(), session=session)

    with pytest.raises(ClickUpAPIError):
        client.create_task("list1", name="x")

    assert session.request.call_count == 1


def test_post_is_retried_after_connect_timeout(sleeps):
    """A connect timeout means the request never left, so resending cannot duplicate."""
    session = MagicMock()
    session.request.side_effect = [requests.ConnectTimeout("connect"), _response(200, _TASK)]
    client = ClickUpClient(_config(), session=session)

    assert client.create_task("list1", name="x").id == "t1"
    assert session.request.call_count == 2


# --- (c) exhaustion reports the real last status / cause ---------------------


def test_exhaustion_reports_last_5xx_status(sleeps):
    session = MagicMock()
    session.request.return_value = _response(503, text="unavailable")
    client = ClickUpClient(_config(max_retries=2), session=session)

    with pytest.raises(ClickUpAPIError) as excinfo:
        client.get_task("t1")

    assert excinfo.value.status == 503
    assert "429" not in str(excinfo.value)
    assert "unavailable" in str(excinfo.value)


def test_exhaustion_reports_transport_cause(sleeps):
    session = MagicMock()
    session.request.side_effect = requests.ConnectionError("connection refused")
    client = ClickUpClient(_config(max_retries=2), session=session)

    with pytest.raises(ClickUpAPIError) as excinfo:
        client.get_task("t1")

    assert excinfo.value.status is None
    assert "429" not in str(excinfo.value)
    assert "ConnectionError" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, requests.ConnectionError)


def test_exhaustion_on_rate_limit_still_reports_429(sleeps):
    session = MagicMock()
    session.request.return_value = _response(429, headers={"Retry-After": "1"}, text="slow down")
    client = ClickUpClient(_config(max_retries=2), session=session)

    with pytest.raises(ClickUpAPIError) as excinfo:
        client.get_task("t1")

    assert excinfo.value.status == 429


def test_error_message_never_contains_token(sleeps):
    session = MagicMock()
    session.request.return_value = _response(401, text="unauthorized")
    client = ClickUpClient(_config(token="pk_supersecret"), session=session)

    with pytest.raises(ClickUpAPIError) as excinfo:
        client.get_task("t1")

    assert "pk_supersecret" not in str(excinfo.value)
    assert "401" in str(excinfo.value)
    _, kwargs = session.request.call_args
    assert kwargs["headers"]["Authorization"] == "pk_supersecret"


# --- (d) tag names URL-encoded in the path -----------------------------------


def test_add_tag_to_task_url_encodes_tag_name():
    session = MagicMock()
    session.request.return_value = _response(200)
    client = ClickUpClient(_config(), session=session)

    client.add_tag_to_task("t1", "needs review/qa?#")

    args, _ = session.request.call_args
    assert args[1].endswith("/task/t1/tag/needs%20review%2Fqa%3F%23")


# --- (e) ensure_tag swallows only "already exists" ---------------------------


def test_ensure_tag_reraises_other_400s():
    session = MagicMock()
    session.request.return_value = _response(400, text='{"err":"Tag name invalid"}')
    client = ClickUpClient(_config(), session=session)

    with pytest.raises(ClickUpAPIError) as excinfo:
        client.ensure_tag("space1", "bad")

    assert excinfo.value.status == 400


def test_ensure_tag_swallows_409_already_exists():
    session = MagicMock()
    session.request.return_value = _response(409, text='{"err":"Tag already exists"}')
    client = ClickUpClient(_config(), session=session)

    client.ensure_tag("space1", "frontend")

    assert session.request.call_count == 1


# --- (f) pagination honours last_page ----------------------------------------


def test_get_tasks_stops_when_last_page_true(monkeypatch):
    monkeypatch.setattr("harness_clickup.client._PAGE_SIZE", 1)
    session = MagicMock()
    session.request.side_effect = [
        _response(200, {"tasks": [{"id": "1", "name": "a", "tags": []}], "last_page": True}),
        _response(200, {"tasks": [{"id": "2", "name": "b", "tags": []}], "last_page": True}),
    ]
    client = ClickUpClient(_config(), session=session)

    tasks = client.get_tasks("list1")

    assert [t.id for t in tasks] == ["1"]
    assert session.request.call_count == 1


def test_get_tasks_continues_past_short_page_when_last_page_false():
    session = MagicMock()
    session.request.side_effect = [
        _response(200, {"tasks": [{"id": "1", "name": "a", "tags": []}], "last_page": False}),
        _response(200, {"tasks": [{"id": "2", "name": "b", "tags": []}], "last_page": True}),
    ]
    client = ClickUpClient(_config(), session=session)

    tasks = client.get_tasks("list1")

    assert [t.id for t in tasks] == ["1", "2"]
    pages = [c.kwargs["params"]["page"] for c in session.request.call_args_list]
    assert pages == [0, 1]
