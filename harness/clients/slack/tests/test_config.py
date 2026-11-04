from __future__ import annotations

from pathlib import Path

import pytest

from harness_slack import config


def test_bot_token_missing_raises_with_guidance() -> None:
    with pytest.raises(config.MissingTokenError, match="SLACK_BOT_TOKEN"):
        config.bot_token()


def test_bot_token_present_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.TOKEN_VAR, "xoxb-from-env")
    assert config.bot_token() == "xoxb-from-env"


def test_allowed_channels_unset_is_empty() -> None:
    assert config.allowed_channels() == frozenset()


def test_allowed_channels_parses_comma_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.ALLOWED_CHANNELS_VAR, " C01 , C02,, ")
    assert config.allowed_channels() == frozenset({"C01", "C02"})


def test_env_file_unset_loads_nothing() -> None:
    assert config.load_env_file() is None
    with pytest.raises(config.MissingTokenError):
        config.bot_token()


def test_env_file_fills_gaps_without_overriding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "harness.env"
    env_file.write_text("SLACK_BOT_TOKEN=xoxb-from-file\nHARNESS_SLACK_ALLOWED_CHANNELS=C0FILE\n")
    monkeypatch.setenv(config.ENV_FILE_VAR, str(env_file))
    monkeypatch.setenv(config.TOKEN_VAR, "xoxb-from-env")

    assert config.load_env_file() == env_file
    assert config.bot_token() == "xoxb-from-env"
    assert config.allowed_channels() == frozenset({"C0FILE"})
