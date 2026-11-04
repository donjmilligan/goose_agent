"""The one place this package shells out to git.

Every other module calls `run_git`, so tests exercise real git against
temporary repositories instead of mocking subprocess.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

DEFAULT_TIMEOUT_S = 30.0
NETWORK_TIMEOUT_S = 15.0


class CoordError(Exception):
    """A refusal or failure with a stable machine-readable `code`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _first_line(text: str) -> str:
    # git's stderr is often multi-line and its last line ("and the repository
    # exists.") reads as nonsense alone; the first line carries the cause.
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")


def run_git(
    repo: str | Path,
    *args: str,
    timeout: float = DEFAULT_TIMEOUT_S,
    error_code: str = "git_failed",
) -> str:
    """Run `git -C repo <args>` and return stdout, raising CoordError on any failure."""
    cmd = ["git", "-C", str(repo), *args]
    # Never let git block on a credential prompt: an MCP server has no terminal.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env, check=False
        )
    except FileNotFoundError as exc:
        raise CoordError("git_missing", "git executable not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise CoordError("timeout", f"`git {' '.join(args)}` timed out after {timeout}s") from exc
    if proc.returncode != 0:
        detail = _first_line(proc.stderr) or f"exited {proc.returncode}"
        raise CoordError(error_code, f"`git {' '.join(args)}` failed: {detail}")
    return proc.stdout


def git_succeeds(repo: str | Path, *args: str) -> bool:
    try:
        run_git(repo, *args)
    except CoordError:
        return False
    return True
