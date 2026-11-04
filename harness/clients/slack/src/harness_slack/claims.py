"""Find open work claims in a channel.

The convention, from the fan-out claim template:

    _CLAIM (<session>, fan-out <batch>, agent <n>/<N>): `<id>`_ — <what changes>.
    _COMPLETION: `<id>`_ — <branch, evidence>.        (posted as a thread reply)

A claim is closed by a `_COMPLETION` in the claim's own thread. For a top-level
claim that is its reply thread; for a claim that was itself posted as a reply,
it is the thread it sits in, after the claim. When both messages carry an id
they must match, so one thread holding several claims closes each separately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from slack_sdk import WebClient

from .client import oldest_ts, paginate, thread_messages

_CLAIM = re.compile(r"^_CLAIM(?![A-Za-z0-9])")
_COMPLETION = re.compile(r"^_COMPLETION(?![A-Za-z0-9])")
_TICKET_ID = re.compile(r"`([^`]+)`")


@dataclass(frozen=True)
class Marker:
    kind: str
    thread: str
    message: dict[str, Any]
    ticket_id: str | None

    @property
    def ts(self) -> float:
        return float(self.message["ts"])

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.ticket_id,
            "ts": self.message["ts"],
            "thread_ts": self.thread,
            "user": self.message.get("user") or self.message.get("bot_id"),
            "text": self.message.get("text", ""),
        }


def _marker(message: dict[str, Any], thread: str) -> Marker | None:
    text = (message.get("text") or "").lstrip()
    if _CLAIM.match(text):
        kind = "claim"
    elif _COMPLETION.match(text):
        kind = "completion"
    else:
        return None
    found = _TICKET_ID.search(text)
    return Marker(kind, thread, message, found.group(1).strip() if found else None)


def _same_ticket(claim: Marker, completion: Marker) -> bool:
    if claim.ticket_id is None or completion.ticket_id is None:
        return True
    return claim.ticket_id.casefold() == completion.ticket_id.casefold()


def _closes(claim: Marker, completion: Marker) -> bool:
    return (
        completion.thread == claim.thread
        and completion.ts > claim.ts
        and _same_ticket(claim, completion)
    )


def pair_claims(markers: list[Marker], topic: str) -> tuple[list[dict], list[dict]]:
    """Split the claims matching `topic` into (open, completed) summaries."""
    needle = topic.casefold()
    claims = [
        m
        for m in markers
        if m.kind == "claim" and needle in (m.message.get("text") or "").casefold()
    ]
    completions = [m for m in markers if m.kind == "completion"]
    open_claims: list[dict] = []
    completed: list[dict] = []
    for claim in claims:
        if any(_closes(claim, c) for c in completions):
            completed.append(claim.summary())
            continue
        summary = claim.summary()
        summary["completion_outside_thread"] = claim.ticket_id is not None and any(
            c.ticket_id is not None and c.thread != claim.thread and _same_ticket(claim, c)
            for c in completions
        )
        open_claims.append(summary)
    return open_claims, completed


def find_claims(
    client: WebClient,
    channel: str,
    topic: str,
    hours: float = 36,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    oldest = oldest_ts(hours, now)
    markers: list[Marker] = []
    messages_scanned = 0
    threads_scanned = 0
    for raw in paginate(client, "conversations_history", channel=channel, oldest=oldest):
        messages_scanned += 1
        parent = _marker(raw, thread=raw["ts"])
        if parent:
            markers.append(parent)
        if not raw.get("reply_count"):
            continue
        threads_scanned += 1
        for reply in thread_messages(client, channel, raw["ts"]):
            if reply.get("ts") == raw["ts"]:
                continue
            messages_scanned += 1
            found = _marker(reply, thread=raw["ts"])
            if found:
                markers.append(found)
    open_claims, completed = pair_claims(markers, topic)
    return {
        "channel": channel,
        "topic": topic,
        "oldest": oldest,
        "messages_scanned": messages_scanned,
        "threads_scanned": threads_scanned,
        "claims_matched": len(open_claims) + len(completed),
        "completed_count": len(completed),
        "open_claims": open_claims,
    }
