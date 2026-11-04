from unittest.mock import MagicMock

from harness_clickup.client import ClickUpClient, ClickUpStatus, ClickUpTask
from harness_clickup.config import ClickUpConfig


def _response(status, json_body=None):
    resp = MagicMock()
    resp.status_code = status
    resp.ok = 200 <= status < 300
    resp.headers = {}
    resp.json.return_value = json_body if json_body is not None else {}
    resp.content = b"{}" if json_body is not None else b""
    resp.text = ""
    return resp


def _client(*responses):
    session = MagicMock()
    session.request.side_effect = list(responses)
    return ClickUpClient(ClickUpConfig(token="pk_test", max_retries=3), session=session), session


def _comment(i):
    return {
        "id": str(i),
        "comment_text": f"c{i}",
        "user": {"username": "don"},
        "date": str(1000 - i),
    }


def test_task_from_api_reads_list_id():
    task = ClickUpTask.from_api({"id": "t1", "name": "x", "list": {"id": "L9"}})

    assert task.list_id == "L9"
    assert ClickUpTask.from_api({"id": "t1", "name": "x"}).list_id is None


def test_get_comments_parses_comments():
    client, session = _client(_response(200, {"comments": [_comment(1), _comment(2)]}))

    comments = client.get_comments("t1")

    assert [(c.id, c.text, c.user, c.date_ms) for c in comments] == [
        ("1", "c1", "don", 999),
        ("2", "c2", "don", 998),
    ]
    args, kwargs = session.request.call_args
    assert args == ("GET", "https://api.clickup.com/api/v2/task/t1/comment")
    assert kwargs["params"] is None


def test_get_comments_pages_back_from_oldest_comment():
    first = [_comment(i) for i in range(25)]
    second = [_comment(i) for i in range(25, 30)]
    client, session = _client(
        _response(200, {"comments": first}), _response(200, {"comments": second})
    )

    comments = client.get_comments("t1")

    assert len(comments) == 30
    assert session.request.call_count == 2
    second_call = session.request.call_args_list[1]
    assert second_call.kwargs["params"] == {"start": 1000 - 24, "start_id": "24"}


def test_get_comments_stops_if_page_repeats():
    page = [_comment(i) for i in range(25)]
    client, session = _client(
        _response(200, {"comments": page}), _response(200, {"comments": page})
    )

    comments = client.get_comments("t1")

    assert len(comments) == 25
    assert session.request.call_count == 2


def test_get_list_statuses_parses_types():
    client, session = _client(
        _response(
            200,
            {
                "id": "L1",
                "statuses": [
                    {"status": "to do", "type": "open", "orderindex": 0, "color": "#ddd"},
                    {"status": "in review", "type": "custom", "orderindex": 1},
                    {"status": "complete", "type": "closed", "orderindex": 2},
                ],
            },
        )
    )

    statuses = client.get_list_statuses("L1")

    assert statuses == [
        ClickUpStatus("to do", "open", 0, "#ddd"),
        ClickUpStatus("in review", "custom", 1, ""),
        ClickUpStatus("complete", "closed", 2, ""),
    ]
    args, _ = session.request.call_args
    assert args == ("GET", "https://api.clickup.com/api/v2/list/L1")
