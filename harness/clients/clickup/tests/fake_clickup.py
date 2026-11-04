"""A routing stand-in for requests.Session: records every call, never hits the network."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from harness_clickup.client import API_BASE, ClickUpClient
from harness_clickup.config import ClickUpConfig

STATUSES = [
    {"status": "to do", "type": "open", "orderindex": 0},
    {"status": "in progress", "type": "custom", "orderindex": 1},
    {"status": "in review", "type": "custom", "orderindex": 2},
    {"status": "done", "type": "done", "orderindex": 3},
    {"status": "complete", "type": "closed", "orderindex": 4},
]


def _response(body: Any) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.ok = True
    resp.headers = {}
    resp.json.return_value = body
    resp.content = b"{}"
    resp.text = ""
    return resp


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.params: list[Any] = []
        self.task: dict[str, Any] = {
            "id": "t1",
            "name": "Task one",
            "tags": [{"name": "harness"}],
            "status": {"status": "in progress"},
            "list": {"id": "L1"},
            "url": "https://app.clickup.com/t/t1",
        }

    def request(self, method: str, url: str, **kwargs: Any) -> MagicMock:
        path = url.removeprefix(API_BASE)
        sent = kwargs.get("json")
        body: dict[str, Any] = sent or {}
        self.calls.append((method, path, sent))
        self.params.append(kwargs.get("params"))
        if method == "GET" and path == "/task/t1":
            return _response(self.task)
        if method == "GET" and path == "/list/L1":
            return _response({"id": "L1", "statuses": STATUSES})
        if method == "GET" and path == "/list/L1/task":
            return _response({"tasks": [self.task], "last_page": True})
        if method == "GET" and path == "/task/t1/comment":
            return _response({"comments": [{"id": "c1", "comment_text": "hi", "date": "5"}]})
        if method == "POST" and path == "/task/t1/comment":
            return _response({"id": 777})
        if method == "POST" and path == "/list/L1/task":
            return _response({**self.task, "id": "t2", "name": body["name"]})
        if method == "PUT" and path == "/task/t1":
            updated = dict(self.task)
            if "status" in body:
                updated["status"] = {"status": body["status"]}
            for key in ("name", "description", "priority"):
                if key in body:
                    updated[key] = body[key]
            return _response(updated)
        raise AssertionError(f"unexpected call {method} {path}")

    @property
    def writes(self) -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if c[0] != "GET"]


def make_client() -> tuple[ClickUpClient, FakeSession]:
    session = FakeSession()
    client = ClickUpClient(ClickUpConfig(token="pk_test", max_retries=2), session=session)  # type: ignore[arg-type]
    return client, session
