"""Environment-only configuration for the Slack harness client.

Nothing here reads a repo-relative path. A deployment points `HARNESS_ENV_FILE`
at a dotenv file if it wants one; otherwise the process environment is the
whole configuration.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

TOKEN_VAR = "SLACK_BOT_TOKEN"
ALLOWED_CHANNELS_VAR = "HARNESS_SLACK_ALLOWED_CHANNELS"
ENV_FILE_VAR = "HARNESS_ENV_FILE"


class MissingTokenError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            f"{TOKEN_VAR} is not set. Export a Slack bot token (xoxb-...) or point "
            f"{ENV_FILE_VAR} at a dotenv file that defines it."
        )


def load_env_file() -> Path | None:
    """Load `HARNESS_ENV_FILE` if set. Variables already in the environment win."""
    raw = os.environ.get(ENV_FILE_VAR, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    load_dotenv(path, override=False)
    return path


def bot_token() -> str:
    token = os.environ.get(TOKEN_VAR, "").strip()
    if not token:
        raise MissingTokenError()
    return token


def allowed_channels() -> frozenset[str]:
    """Channel IDs that post_message/reply may write to. Empty means none."""
    raw = os.environ.get(ALLOWED_CHANNELS_VAR, "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())
