"""MCP tool logic as plain functions over an injected client.

The MCP server in ``server.py`` is a thin adapter over these, so every rule
here is unit-testable without an MCP transport.

Two rules are structural rather than advisory:

* ``set_status`` posts its comment before it changes the status, in the same
  call. A silent flip cannot happen, and nothing depends on in-memory state
  that a server restart would lose.
* An agent's ceiling is review: statuses whose ClickUp type is ``done`` or
  ``closed`` are refused unless the operator sets ``HARNESS_ALLOW_DONE=1``.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any

from .client import ClickUpClient, ClickUpComment, ClickUpStatus, ClickUpTask

ALLOW_DONE_VAR = "HARNESS_ALLOW_DONE"
TERMINAL_STATUS_TYPES = frozenset({"done", "closed"})
_PRIORITIES = {1: "urgent", 2: "high", 3: "normal", 4: "low"}


class ToolRefused(ValueError):
    """The request was refused before any write reached ClickUp."""


def allow_done_from_env() -> bool:
    return os.environ.get(ALLOW_DONE_VAR, "").strip() == "1"


def _task(task: ClickUpTask) -> dict[str, Any]:
    return {**asdict(task), "tags": list(task.tags)}


def _comment(comment: ClickUpComment) -> dict[str, Any]:
    return asdict(comment)


def _status(status: ClickUpStatus) -> dict[str, Any]:
    return asdict(status)


def get_task(client: ClickUpClient, task_id: str) -> dict[str, Any]:
    return _task(client.get_task(task_id))


def list_tasks(
    client: ClickUpClient, list_id: str, include_closed: bool = False
) -> list[dict[str, Any]]:
    return [_task(t) for t in client.get_tasks(list_id, include_closed=include_closed)]


def get_comments(client: ClickUpClient, task_id: str) -> list[dict[str, Any]]:
    return [_comment(c) for c in client.get_comments(task_id)]


def list_statuses(client: ClickUpClient, list_id: str) -> list[dict[str, Any]]:
    return [_status(s) for s in client.get_list_statuses(list_id)]


def create_task(
    client: ClickUpClient,
    list_id: str,
    name: str,
    description: str = "",
    parent: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    if not name.strip():
        raise ToolRefused("create_task needs a non-empty name.")
    return _task(
        client.create_task(list_id, name=name, description=description, parent=parent, tags=tags)
    )


def add_comment(client: ClickUpClient, task_id: str, text: str) -> dict[str, Any]:
    if not text.strip():
        raise ToolRefused("add_comment needs non-empty text.")
    return {"task_id": task_id, "comment_id": client.add_comment(task_id, text)}


def update_task(
    client: ClickUpClient,
    task_id: str,
    name: str | None = None,
    description: str | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    if name is None and description is None and priority is None:
        raise ToolRefused("update_task needs at least one of name, description, priority.")
    if priority is not None and priority not in _PRIORITIES:
        raise ToolRefused(f"priority must be one of {sorted(_PRIORITIES)} (1 urgent .. 4 low).")
    return _task(client.update_task(task_id, name=name, description=description, priority=priority))


def set_status(
    client: ClickUpClient,
    task_id: str,
    status: str,
    comment: str,
    *,
    allow_done: bool = False,
) -> dict[str, Any]:
    """Comment on a task, then move it to ``status``.

    Every check runs on live reads before the first write, so a refused call
    leaves no stray comment behind.
    """
    if not comment.strip():
        raise ToolRefused(
            "set_status needs a comment: state the decision, what was done, the evidence, "
            "and anything still open. No silent status changes."
        )

    task = client.get_task(task_id)
    if task.list_id is None:
        raise ToolRefused(f"Task {task_id} has no list; cannot verify {status!r} is allowed.")

    statuses = client.get_list_statuses(task.list_id)
    target = next((s for s in statuses if s.status.lower() == status.strip().lower()), None)
    if target is None:
        available = ", ".join(repr(s.status) for s in statuses)
        raise ToolRefused(f"{status!r} is not a status on list {task.list_id}: {available}.")

    if target.type in TERMINAL_STATUS_TYPES and not allow_done:
        raise ToolRefused(
            f"Refusing to move {task_id} to {target.status!r} (type {target.type!r}): an agent's "
            f"ceiling is review. A human closes the task, or the operator sets "
            f"{ALLOW_DONE_VAR}=1."
        )

    comment_id = client.add_comment(task_id, comment)
    updated = client.update_task(task_id, status=target.status)
    return {
        "task": _task(updated),
        "comment_id": comment_id,
        "previous_status": task.status,
    }
