#!/usr/bin/env python3
"""SessionStart hook: put the claim gate in front of every session.

Port of vanta-lms `.claude/hooks/claim_gate.py`, made project-agnostic.

* Performs the branch signal: `git ls-remote --heads origin`, with the total
  remote branch count as a positive control (an empty list and an unreachable
  remote must never look alike).
* Performs the worktree signal: `git worktree list`. An unpushed local
  worktree is invisible to every other signal.
* Performs the Slack signal only when `SLACK_BOT_TOKEN` and
  `HARNESS_CLAIM_CHANNEL` are both set: lists `_CLAIM` messages from the last
  36h that have no `_COMPLETION` reply in their thread. When they are not set
  it says plainly that the Slack signal was skipped -- a hook that implied it
  had checked the channel would be worse than no hook.

goose reads SessionStart stdout as `{"banner": "..."}` JSON
(`emit_collecting_banners` in crates/goose/src/hooks/mod.rs) and shows the
banner to the user, so that is the only thing written to stdout.

Never blocks: always exits 0, and every subprocess and network call is
time-limited. A coordination reminder that can break a session start is a
reminder people disable.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TextIO

GIT_TIMEOUT_S = 8
SLACK_CALL_TIMEOUT_S = 6.0
SLACK_BUDGET_S = 12.0
CLAIM_WINDOW_H = 36
MAX_BRANCHES = 15
MAX_THREAD_LOOKUPS = 25
SLACK_API = "https://slack.com/api"
DEFAULT_BRANCHES = frozenset({"main", "master"})
CLAIM = re.compile(r"_CLAIM\b")
COMPLETION = re.compile(r"_COMPLETION\b")

HttpGet = Callable[[str, Mapping[str, str], float], dict[str, Any]]


@dataclass(frozen=True)
class Claim:
    ts: str
    user: str
    text: str


def run_git(cwd: str, *args: str) -> tuple[str | None, str | None]:
    """(stdout, error). Never raises -- offline is a normal state."""
    try:
        result = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return None, f"`git {' '.join(args)}` timed out after {GIT_TIMEOUT_S}s"
    except (OSError, ValueError) as exc:
        return None, str(exc)
    if result.returncode != 0:
        first = next((ln.strip() for ln in result.stderr.splitlines() if ln.strip()), "")
        return None, first or f"`git {' '.join(args)}` exited {result.returncode}"
    return result.stdout, None


def remote_branches(cwd: str) -> tuple[list[str], str | None]:
    out, error = run_git(cwd, "ls-remote", "--heads", "origin")
    if out is None:
        return [], error
    return [
        line.split("refs/heads/", 1)[1] for line in out.splitlines() if "refs/heads/" in line
    ], None


def worktrees(cwd: str) -> tuple[list[str], str | None]:
    out, error = run_git(cwd, "worktree", "list")
    if out is None:
        return [], error
    return [line for line in out.splitlines() if line.strip()], None


def slack_get(url: str, headers: Mapping[str, str], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=dict(headers))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def open_claims(
    token: str,
    channel: str,
    http_get: HttpGet = slack_get,
    now: float | None = None,
) -> tuple[list[Claim], str | None]:
    """Claims in the window with no `_COMPLETION` thread reply, or an error."""
    deadline = time.monotonic() + SLACK_BUDGET_S
    headers = {"Authorization": f"Bearer {token}"}
    oldest = (time.time() if now is None else now) - CLAIM_WINDOW_H * 3600

    def call(method: str, **params: str) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Slack budget of {SLACK_BUDGET_S:.0f}s used up")
        url = f"{SLACK_API}/{method}?{urllib.parse.urlencode(params)}"
        body = http_get(url, headers, min(SLACK_CALL_TIMEOUT_S, remaining))
        if not body.get("ok"):
            raise RuntimeError(f"Slack {method}: {body.get('error', 'unknown error')}")
        return body

    try:
        history = call(
            "conversations.history", channel=channel, oldest=f"{oldest:.6f}", limit="200"
        )
        claims: list[Claim] = []
        lookups = 0
        for message in history.get("messages", []):
            text = str(message.get("text", ""))
            if not CLAIM.search(text):
                continue
            ts = str(message.get("ts", ""))
            if int(message.get("reply_count", 0) or 0) > 0:
                if lookups >= MAX_THREAD_LOOKUPS:
                    raise RuntimeError(
                        f"more than {MAX_THREAD_LOOKUPS} claim threads; read the channel yourself"
                    )
                lookups += 1
                replies = call("conversations.replies", channel=channel, ts=ts, limit="200")
                if any(
                    COMPLETION.search(str(reply.get("text", "")))
                    for reply in replies.get("messages", [])
                    if reply.get("ts") != ts
                ):
                    continue
            claims.append(Claim(ts=ts, user=str(message.get("user", "?")), text=text))
        return claims, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        return [], str(getattr(exc, "reason", None) or exc)


def read_payload(stdin: TextIO) -> dict[str, Any]:
    try:
        if stdin.isatty():
            return {}
        payload = json.loads(stdin.read() or "{}")
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def branch_lines(cwd: str) -> list[str]:
    branches, error = remote_branches(cwd)
    if error:
        return [
            f"  ! could not read origin's branches ({error})",
            "    Run `git ls-remote --heads origin` yourself before claiming.",
        ]
    others = [name for name in branches if name not in DEFAULT_BRANCHES]
    lines = [f"  Remote branches on origin: {len(branches)} total (positive control)."]
    if not others:
        lines.append("    None besides the default branch.")
        return lines
    for name in others[:MAX_BRANCHES]:
        lines.append(f"    · {name}")
    if len(others) > MAX_BRANCHES:
        lines.append(
            f"    … and {len(others) - MAX_BRANCHES} more (`git ls-remote --heads origin`)"
        )
    lines.append("    A branch named for your topic outranks the board AND the channel.")
    return lines


def worktree_lines(cwd: str) -> list[str]:
    entries, error = worktrees(cwd)
    if error:
        return [f"  ! could not list worktrees ({error})"]
    lines = [f"  Local worktrees ({len(entries)}):"]
    lines += [f"    · {entry}" for entry in entries]
    return lines


def slack_lines(env: Mapping[str, str], http_get: HttpGet) -> list[str]:
    token = env.get("SLACK_BOT_TOKEN", "").strip()
    channel = env.get("HARNESS_CLAIM_CHANNEL", "").strip()
    if not token or not channel:
        missing = [
            name
            for name, value in (("SLACK_BOT_TOKEN", token), ("HARNESS_CLAIM_CHANNEL", channel))
            if not value
        ]
        return [
            f"  Slack signal SKIPPED: {' and '.join(missing)} not set.",
            "    Open claims were NOT checked. Read the claim channel yourself.",
        ]
    claims, error = open_claims(token, channel, http_get)
    if error:
        return [
            f"  ! Slack signal failed ({error}).",
            f"    Open claims were NOT checked. Read {channel} yourself.",
        ]
    lines = [f"  Open claims in {channel}, last {CLAIM_WINDOW_H}h ({len(claims)}):"]
    if not claims:
        lines.append("    None without a _COMPLETION reply.")
    for claim in claims:
        text = " ".join(claim.text.split())
        lines.append(f"    · {text[:120]}{'…' if len(text) > 120 else ''}  ({claim.user})")
    return lines


CHECKLIST = [
    "  Claim gate: other agents may be working. Check before you start.",
    "    1. READ    the claim channel. An explicit claim covering your work means it",
    "               is taken. No confirmation either way: wait, don't guess.",
    "    2. BRANCH  checked below. A pushed branch exists from the moment work starts.",
    "    3. WORKTREE checked below. Unpushed worktrees are invisible to everything else.",
    "    4. TASK    read the task's status, assignee and comments.",
    "    5. CLAIM   post `_CLAIM <topic> - <branch>_` BEFORE the board moves;",
    "               reply `_COMPLETION ..._` in its thread when done.",
    "    A follow-up found inside a claimed task needs its OWN claim.",
]


def build_banner(cwd: str, env: Mapping[str, str], http_get: HttpGet) -> str:
    lines = [*CHECKLIST, ""]
    lines += branch_lines(cwd)
    lines += worktree_lines(cwd)
    lines += slack_lines(env, http_get)
    return "\n".join(lines)


def main(
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    env: Mapping[str, str] = os.environ,
    http_get: HttpGet = slack_get,
) -> int:
    try:
        payload = read_payload(stdin)
        working_dir = payload.get("working_dir")
        cwd = working_dir if isinstance(working_dir, str) and working_dir else os.getcwd()
        banner = build_banner(cwd, env, http_get)
    except Exception as exc:
        banner = f"  Claim gate could not run ({type(exc).__name__}). Check claims by hand."
    stdout.write(json.dumps({"banner": banner}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
