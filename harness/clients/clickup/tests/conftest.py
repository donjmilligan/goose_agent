import pytest
import requests


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Tests never see a developer's real token or harness settings.

    setenv-then-delenv makes monkeypatch record the original value, so anything
    a test loads via load_dotenv is also removed at teardown.
    """
    for var in ("CLICKUP_API_TOKEN", "HARNESS_ENV_FILE", "HARNESS_ALLOW_DONE"):
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Any real HTTP attempt fails the test instead of reaching ClickUp."""

    def _refuse(*_args, **_kwargs):
        raise AssertionError("tests must not touch the network")

    monkeypatch.setattr(requests.Session, "request", _refuse)
