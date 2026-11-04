import pytest

from harness_clickup.config import ClickUpConfig


def test_token_read_from_process_env(monkeypatch):
    monkeypatch.setenv("CLICKUP_API_TOKEN", "pk_env")

    assert ClickUpConfig.from_env().token == "pk_env"


def test_missing_token_raises_with_actionable_message():
    with pytest.raises(RuntimeError) as excinfo:
        ClickUpConfig.from_env()

    message = str(excinfo.value)
    assert "CLICKUP_API_TOKEN" in message
    assert "HARNESS_ENV_FILE" in message


def test_missing_token_allowed_when_not_required():
    assert ClickUpConfig.from_env(require_token=False).token == ""


def test_env_file_loaded_when_harness_env_file_set(monkeypatch, tmp_path):
    env_file = tmp_path / "harness.env"
    env_file.write_text("CLICKUP_API_TOKEN=pk_file\n")
    monkeypatch.setenv("HARNESS_ENV_FILE", str(env_file))

    assert ClickUpConfig.from_env().token == "pk_file"


def test_exported_token_beats_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / "harness.env"
    env_file.write_text("CLICKUP_API_TOKEN=pk_file\n")
    monkeypatch.setenv("HARNESS_ENV_FILE", str(env_file))
    monkeypatch.setenv("CLICKUP_API_TOKEN", "pk_env")

    assert ClickUpConfig.from_env().token == "pk_env"


def test_env_file_ignored_when_harness_env_file_unset(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("CLICKUP_API_TOKEN=pk_cwd\n")
    monkeypatch.chdir(tmp_path)

    assert ClickUpConfig.from_env(require_token=False).token == ""


def test_missing_env_file_named_in_error(monkeypatch, tmp_path):
    missing = tmp_path / "nope.env"
    monkeypatch.setenv("HARNESS_ENV_FILE", str(missing))

    with pytest.raises(RuntimeError) as excinfo:
        ClickUpConfig.from_env()

    assert str(missing) in str(excinfo.value)
    assert "does not exist" in str(excinfo.value)


def test_token_not_in_repr():
    config = ClickUpConfig(token="pk_secret_value")

    rendered = repr(config)
    assert "pk_secret_value" not in rendered
    assert "timeout_seconds=30.0" in rendered
