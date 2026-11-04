"""Environment-only configuration for the ClickUp client.

The token comes from ``CLICKUP_API_TOKEN`` in the process environment. If
``HARNESS_ENV_FILE`` names a dotenv file, it is loaded first with
``override=False``, so an exported variable always beats the file. There is no
repo-layout or git-worktree lookup: the harness runs from many checkouts, and
guessing at parent directories is how a session silently picks up the wrong
credential.

The token is never logged and is excluded from ``repr``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

TOKEN_VAR = "CLICKUP_API_TOKEN"
ENV_FILE_VAR = "HARNESS_ENV_FILE"


def _missing_token_message(env_file: Path | None) -> str:
    lines = [f"{TOKEN_VAR} is not set."]
    if env_file is None:
        lines.append(f"Export it, or set {ENV_FILE_VAR} to a dotenv file that defines it.")
    elif env_file.is_file():
        lines.append(f"{ENV_FILE_VAR}={env_file} exists but does not define {TOKEN_VAR}.")
    else:
        lines.append(f"{ENV_FILE_VAR}={env_file} does not exist.")
    return "\n".join(lines)


@dataclass(frozen=True)
class ClickUpConfig:
    token: str = field(repr=False)
    timeout_seconds: float = 30.0
    max_retries: int = 6

    @classmethod
    def from_env(cls, *, require_token: bool = True) -> ClickUpConfig:
        raw_env_file = os.environ.get(ENV_FILE_VAR, "").strip()
        env_file = Path(raw_env_file).expanduser() if raw_env_file else None
        if env_file is not None:
            load_dotenv(env_file, override=False)

        token = os.environ.get(TOKEN_VAR, "").strip()
        if require_token and not token:
            raise RuntimeError(_missing_token_message(env_file))
        return cls(token=token)
