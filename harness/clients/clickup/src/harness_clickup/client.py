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

import requests

from .config import ClickUpConfig

API_BASE = "https://api.clickup.com/api/v2"
_PAGE_SIZE = 100


class ClickUpAPIError(RuntimeError):
    def __init__(self, method: str, url: str, status: int, body: str):
        super().__init__(f"{method} {url} -> HTTP {status}: {body[:500]}")
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
        )


class SilentStatusChange(RuntimeError):
    """Raised when a status change is attempted with no comment behind it.

    Every status change carries a comment -- intent, reason, or result. A
    status nobody explained is a status nobody can audit.
    """


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
        for attempt in range(self._config.max_retries):
            response = self._session.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
                timeout=self._config.timeout_seconds,
            )
            if response.status_code == 429:
                time.sleep(self._retry_after_seconds(response))
                continue
            if response.status_code >= 500:
                time.sleep(min(2**attempt, 30))
                continue
            if not response.ok:
                raise ClickUpAPIError(method, url, response.status_code, response.text)
            if not response.content:
                return {}
            return response.json()
        raise ClickUpAPIError(method, url, 429, "exhausted retries after repeated rate limiting")

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
                f"/list/{list_id}/task",
                params={
                    "include_closed": str(include_closed).lower(),
                    "subtasks": str(include_subtasks).lower(),
                    "page": page,
                },
            )
            batch = data.get("tasks", [])
            tasks.extend(ClickUpTask.from_api(t) for t in batch)
            if len(batch) < _PAGE_SIZE:
                break
            page += 1
        return tasks

    def get_task(self, task_id: str) -> ClickUpTask:
        return ClickUpTask.from_api(self._request("GET", f"/task/{task_id}"))

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
        return ClickUpTask.from_api(self._request("POST", f"/list/{list_id}/task", json_body=body))

    def delete_task(self, task_id: str) -> None:
        self._request("DELETE", f"/task/{task_id}")

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
        return ClickUpTask.from_api(self._request("PUT", f"/task/{task_id}", json_body=body))

    def ensure_tag(
        self, space_id: str, tag_name: str, *, fg: str = "#FFFFFF", bg: str = "#2D3B6B"
    ) -> None:
        """Create a space tag if it doesn't already exist. Idempotent."""
        try:
            self._request(
                "POST",
                f"/space/{space_id}/tag",
                json_body={"tag": {"name": tag_name, "tag_fg": fg, "tag_bg": bg}},
            )
        except ClickUpAPIError as exc:
            if exc.status not in (400, 409):
                raise

    def add_tag_to_task(self, task_id: str, tag_name: str) -> None:
        """Attach an existing space tag to a task. The tag must already exist
        on the task's space — see ensure_tag()."""
        self._request("POST", f"/task/{task_id}/tag/{tag_name}")

    def add_comment(self, task_id: str, text: str, *, notify_all: bool = False) -> str:
        """Post a comment on a task. Returns the new comment's id."""
        result = self._request(
            "POST",
            f"/task/{task_id}/comment",
            json_body={"comment_text": text, "notify_all": notify_all},
        )
        self._commented.add(task_id)
        return str(result["id"])

    def add_task_link(self, task_id: str, links_to: str) -> None:
        """Create a generic (untyped) link between two tasks. ClickUp's REST
        API has no "duplicate" relationship type — callers wanting that
        semantic should pair this with a tag and/or a comment."""
        self._request("POST", f"/task/{task_id}/link/{links_to}")
