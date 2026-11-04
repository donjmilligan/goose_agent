"""Generic ClickUp REST API v2 client.

Uses a personal API token (ClickUp -> Settings -> Apps -> API Token), which
carries ClickUp's standard per-token rate limit (~100 req/min on paid plans).

Vendored from vanta-lms ``api_clients/clickup_api_handler`` (generic half
only). Two properties carried over on purpose:

* No local state file. ClickUp is the only source of truth; callers read live
  state before writing.
* The token is sent only in the Authorization header and never appears in an
  error message or log line.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

from .config import ClickUpConfig

API_BASE = "https://api.clickup.com/api/v2"
_PAGE_SIZE = 100
_COMMENT_PAGE_SIZE = 25

#: Methods whose repeat has the same effect as one call. Only these are resent
#: after a 5xx or a transport error: a POST that hit a 502 may still have
#: created the task, and resending it creates a duplicate.
_IDEMPOTENT_METHODS = frozenset({"GET", "PUT", "DELETE"})

_MAX_BACKOFF_SECONDS = 30


class ClickUpAPIError(RuntimeError):
    """A ClickUp call failed. ``status`` is None when no HTTP response arrived."""

    def __init__(self, method: str, url: str, status: int | None, body: str):
        shown = f"HTTP {status}" if status is not None else "no response"
        super().__init__(f"{method} {url} -> {shown}: {body[:500]}")
        self.method = method
        self.url = url
        self.status = status
        self.body = body


@dataclass(frozen=True)
class ClickUpTask:
    id: str
    name: str
    description: str
    tags: tuple[str, ...]
    parent: str | None
    url: str
    status: str
    list_id: str | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> ClickUpTask:
        return cls(
            id=raw["id"],
            name=raw["name"],
            description=raw.get("description") or raw.get("text_content") or "",
            tags=tuple(t["name"] for t in raw.get("tags", [])),
            parent=raw.get("parent"),
            url=raw.get("url", ""),
            status=(raw.get("status") or {}).get("status", ""),
            list_id=(raw.get("list") or {}).get("id"),
        )


@dataclass(frozen=True)
class ClickUpComment:
    id: str
    text: str
    user: str
    date_ms: int | None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> ClickUpComment:
        user = raw.get("user") or {}
        date = raw.get("date")
        return cls(
            id=str(raw["id"]),
            text=raw.get("comment_text") or "",
            user=user.get("username") or user.get("email") or "",
            date_ms=int(date) if date is not None else None,
        )


@dataclass(frozen=True)
class ClickUpStatus:
    status: str
    type: str
    orderindex: int | None = None
    color: str = ""

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> ClickUpStatus:
        orderindex = raw.get("orderindex")
        return cls(
            status=raw["status"],
            type=raw.get("type") or "",
            orderindex=int(orderindex) if orderindex is not None else None,
            color=raw.get("color") or "",
        )


class SilentStatusChange(RuntimeError):
    """Raised when a status change is attempted with no comment behind it.

    Every status change carries a comment -- intent, reason, or result. A
    status nobody explained is a status nobody can audit.
    """


def _is_safe_to_resend(method: str, error: requests.RequestException) -> bool:
    if method in _IDEMPOTENT_METHODS:
        return isinstance(error, requests.ConnectionError | requests.Timeout)
    # A connect timeout means the request never reached the server.
    return isinstance(error, requests.ConnectTimeout)


class ClickUpClient:
    """Thin, retrying wrapper around ClickUp REST API v2.

    This client does not deduplicate writes; callers read live state first
    and only ask it to create what is actually missing.
    """

    def __init__(self, config: ClickUpConfig, session: requests.Session | None = None):
        self._config = config
        self._session = session or requests.Session()
        # Task IDs this client has commented on. `update_task` refuses a
        # status change for anything not in here -- see SilentStatusChange.
        self._commented: set[str] = set()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> Any:
        url = f"{API_BASE}{path}"
        headers = {"Authorization": self._config.token, "Content-Type": "application/json"}
        last_status: int | None = None
        last_body = "no attempt made"
        last_error: requests.RequestException | None = None
        for attempt in range(self._config.max_retries):
            try:
                response = self._session.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    params=params,
                    timeout=self._config.timeout_seconds,
                )
            except requests.RequestException as exc:
                last_status, last_body, last_error = None, f"{type(exc).__name__}: {exc}", exc
                if not _is_safe_to_resend(method, exc):
                    raise ClickUpAPIError(method, url, None, last_body) from exc
                time.sleep(min(2**attempt, _MAX_BACKOFF_SECONDS))
                continue

            last_status, last_body, last_error = response.status_code, response.text, None
            if response.status_code == 429:
                time.sleep(self._retry_after_seconds(response))
                continue
            if response.status_code >= 500 and method in _IDEMPOTENT_METHODS:
                time.sleep(min(2**attempt, _MAX_BACKOFF_SECONDS))
                continue
            if not response.ok:
                raise ClickUpAPIError(method, url, response.status_code, response.text)
            if not response.content:
                return {}
            return response.json()

        attempts = self._config.max_retries
        raise ClickUpAPIError(
            method, url, last_status, f"exhausted {attempts} attempts; last: {last_body}"
        ) from last_error

    @staticmethod
    def _retry_after_seconds(response: requests.Response) -> float:
        reset = response.headers.get("X-RateLimit-Reset")
        if reset:
            try:
                return max(float(reset) - time.time(), 1.0)
            except ValueError:
                pass
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return 5.0

    def get_tasks(
        self, list_id: str, *, include_closed: bool = True, include_subtasks: bool = True
    ) -> list[ClickUpTask]:
        tasks: list[ClickUpTask] = []
        page = 0
        while True:
            data = self._request(
                "GET",
                f"/list/{quote(list_id, safe='')}/task",
                params={
                    "include_closed": str(include_closed).lower(),
                    "subtasks": str(include_subtasks).lower(),
                    "page": page,
                },
            )
            batch = data.get("tasks", [])
            tasks.extend(ClickUpTask.from_api(t) for t in batch)
            if not batch:
                break
            last_page = data.get("last_page")
            if last_page is None:
                if len(batch) < _PAGE_SIZE:
                    break
            elif last_page:
                break
            page += 1
        return tasks

    def get_task(self, task_id: str) -> ClickUpTask:
        return ClickUpTask.from_api(self._request("GET", f"/task/{quote(task_id, safe='')}"))

    def get_comments(self, task_id: str) -> list[ClickUpComment]:
        """All comments on a task, newest first.

        ClickUp returns 25 comments per call; older ones are fetched by passing
        the oldest comment's ``date`` and ``id`` back as ``start``/``start_id``.
        """
        comments: list[ClickUpComment] = []
        seen: set[str] = set()
        params: dict[str, Any] | None = None
        while True:
            data = self._request("GET", f"/task/{quote(task_id, safe='')}/comment", params=params)
            batch = [ClickUpComment.from_api(c) for c in data.get("comments", [])]
            fresh = [c for c in batch if c.id not in seen]
            comments.extend(fresh)
            seen.update(c.id for c in fresh)
            if len(batch) < _COMMENT_PAGE_SIZE or not fresh:
                break
            oldest = batch[-1]
            params = {"start": oldest.date_ms, "start_id": oldest.id}
        return comments

    def get_list_statuses(self, list_id: str) -> list[ClickUpStatus]:
        data = self._request("GET", f"/list/{quote(list_id, safe='')}")
        return [ClickUpStatus.from_api(s) for s in data.get("statuses", [])]

    def create_task(
        self,
        list_id: str,
        *,
        name: str,
        description: str = "",
        tags: list[str] | None = None,
        due_date_ms: int | None = None,
        parent: str | None = None,
        priority: int | None = None,
    ) -> ClickUpTask:
        """priority: ClickUp's scale — 1 Urgent, 2 High, 3 Normal, 4 Low."""
        body: dict[str, Any] = {
            "name": name,
            "description": description,
            "tags": tags or [],
            "due_date_time": False,
        }
        if due_date_ms is not None:
            body["due_date"] = due_date_ms
        if parent is not None:
            body["parent"] = parent
        if priority is not None:
            body["priority"] = priority
        return ClickUpTask.from_api(
            self._request("POST", f"/list/{quote(list_id, safe='')}/task", json_body=body)
        )

    def delete_task(self, task_id: str) -> None:
        self._request("DELETE", f"/task/{quote(task_id, safe='')}")

    def update_task(
        self,
        task_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
        priority: int | None = None,
        assignee_add: list[int] | None = None,
        assignee_remove: list[int] | None = None,
        allow_silent_status: bool = False,
    ) -> ClickUpTask:
        """priority: ClickUp's scale — 1 Urgent, 2 High, 3 Normal, 4 Low.

        assignee_add/assignee_remove are user IDs. ClickUp's update endpoint
        (unlike create_task's plain array) takes assignees as an
        {"add": [...], "rem": [...]} delta, not a full replacement list.

        **A status change requires a comment on the same task first**, from
        this client instance -- see SilentStatusChange.

        Order matters and is the point: comment, then move. The comment is
        what the reviewer opening the task reads first, and writing it before
        the flip is what makes it a reason rather than a justification.

        `allow_silent_status=True` escapes the guard for the genuine
        exception -- a bulk reconciliation, a status corrected right back.
        It has to be typed on purpose, which is the whole design: a silent
        flip stops being something that happens by accident.
        """
        if status is not None and not allow_silent_status and task_id not in self._commented:
            raise SilentStatusChange(
                f"Refusing to set {task_id} to {status!r} with no comment behind it.\n"
                f"Call add_comment({task_id!r}, ...) first -- state the decision, what "
                f"was done, the evidence, and anything still open.\n"
                f"Every status change carries a comment, no silent flips.\n"
                f"If this is a genuine exception (bulk reconciliation, immediate "
                f"correction), pass allow_silent_status=True and say why in the calling "
                f"code."
            )
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if status is not None:
            body["status"] = status
        if priority is not None:
            body["priority"] = priority
        if assignee_add is not None or assignee_remove is not None:
            body["assignees"] = {
                "add": assignee_add or [],
                "rem": assignee_remove or [],
            }
        return ClickUpTask.from_api(
            self._request("PUT", f"/task/{quote(task_id, safe='')}", json_body=body)
        )

    def ensure_tag(
        self, space_id: str, tag_name: str, *, fg: str = "#FFFFFF", bg: str = "#2D3B6B"
    ) -> None:
        """Create a space tag if it doesn't already exist. Idempotent.

        Only the "already exists" refusal is swallowed; any other 400 (an
        invalid name, a missing space) is a real failure and propagates.
        """
        try:
            self._request(
                "POST",
                f"/space/{quote(space_id, safe='')}/tag",
                json_body={"tag": {"name": tag_name, "tag_fg": fg, "tag_bg": bg}},
            )
        except ClickUpAPIError as exc:
            if exc.status in (400, 409) and "already exist" in exc.body.lower():
                return
            raise

    def add_tag_to_task(self, task_id: str, tag_name: str) -> None:
        """Attach an existing space tag to a task. The tag must already exist
        on the task's space — see ensure_tag()."""
        self._request("POST", f"/task/{quote(task_id, safe='')}/tag/{quote(tag_name, safe='')}")

    def add_comment(self, task_id: str, text: str, *, notify_all: bool = False) -> str:
        """Post a comment on a task. Returns the new comment's id."""
        result = self._request(
            "POST",
            f"/task/{quote(task_id, safe='')}/comment",
            json_body={"comment_text": text, "notify_all": notify_all},
        )
        self._commented.add(task_id)
        return str(result["id"])

    def add_task_link(self, task_id: str, links_to: str) -> None:
        """Create a generic (untyped) link between two tasks. ClickUp's REST
        API has no "duplicate" relationship type — callers wanting that
        semantic should pair this with a tag and/or a comment."""
        self._request("POST", f"/task/{quote(task_id, safe='')}/link/{quote(links_to, safe='')}")
