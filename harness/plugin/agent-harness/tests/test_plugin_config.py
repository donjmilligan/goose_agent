"""The plugin's JSON files against what goose's Rust loaders actually accept."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from typing import Any

import guard
import pytest
from conftest import PLUGIN_ROOT, SCRIPTS, Repo

REPO_ROOT = PLUGIN_ROOT.parents[2]
GOOSE_HOOK_EVENTS = {
    "PreToolUse",
    "PreToolUseResult",
    "PostToolUse",
    "PostToolUseFailure",
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "BeforeReadFile",
    "AfterFileEdit",
    "BeforeShellExecution",
    "AfterShellExecution",
    "Stop",
}
CONTRACT_SERVERS = {
    "clickup": "clients/clickup",
    "slack": "clients/slack",
    "coord": "coord",
}
CONTRACT_ENV = {
    "CLICKUP_API_TOKEN",
    "SLACK_BOT_TOKEN",
    "HARNESS_SLACK_ALLOWED_CHANNELS",
    "HARNESS_CLAIM_CHANNEL",
    "HARNESS_ENV_FILE",
    "HARNESS_ALLOW_DONE",
}


def load(name: str) -> Any:
    return json.loads((PLUGIN_ROOT / name).read_text())


def hook_rules() -> dict[str, list[dict[str, Any]]]:
    return load("hooks/hooks.json")["hooks"]


def test_manifest() -> None:
    manifest = load("plugin.json")
    assert manifest["name"] == "agent-harness"
    assert manifest["version"] == "0.1.0"
    assert manifest["description"].strip()


def test_directory_name_is_the_plugin_name() -> None:
    """goose names a discovered plugin after its directory (discovery.rs list_dir_children)."""
    assert PLUGIN_ROOT.name == load("plugin.json")["name"]


def test_hooks_use_only_events_goose_knows() -> None:
    events = set(hook_rules())
    assert events <= GOOSE_HOOK_EVENTS
    assert events == {"SessionStart", "PreToolUse", "Stop"}


def test_every_action_is_a_command_on_an_existing_script() -> None:
    for rules in hook_rules().values():
        for rule in rules:
            for action in rule["hooks"]:
                assert action["type"] == "command"
                assert isinstance(action["timeout"], int) and 0 < action["timeout"] <= 30
                assert "on_failure" not in action, "a broken guard must fail open"
                match = re.fullmatch(
                    r'python3 "\$\{PLUGIN_ROOT\}/scripts/(\w+\.py)"', action["command"]
                )
                assert match, action["command"]
                assert (SCRIPTS / match.group(1)).is_file()


def shell_matcher() -> str:
    (rule,) = hook_rules()["PreToolUse"]
    return rule["matcher"]


def test_matcher_is_the_pattern_the_guard_checks() -> None:
    assert shell_matcher() == guard.SHELL_TOOL_PATTERN


@pytest.mark.parametrize("name", ["shell", "developer__shell"])
def test_matcher_matches_the_shell_tool(name: str) -> None:
    assert re.search(shell_matcher(), name)


@pytest.mark.parametrize("name", ["remote__shell_exec", "shell_exec", "myshell", "write", "edit"])
def test_matcher_skips_other_tools(name: str) -> None:
    assert not re.search(shell_matcher(), name)


def test_shell_tool_name_and_field_match_goose_source() -> None:
    developer = REPO_ROOT / "crates/goose/src/agents/platform_extensions/developer"
    registry = REPO_ROOT / "crates/goose/src/agents/platform_extensions/mod.rs"
    if not developer.is_dir():
        pytest.skip("goose sources not present next to the harness")
    assert 'Tool::new("shell".to_string()' in (developer / "mod.rs").read_text()
    assert re.search(
        r"pub struct ShellParams \{\s*pub command: String,", (developer / "shell.rs").read_text()
    )
    developer_def = registry.read_text().split("developer::EXTENSION_NAME,", 1)[1].split("},", 1)[0]
    assert "unprefixed_tools: true" in developer_def
    assert re.search(shell_matcher(), "shell")


def run_like_goose(command: str, payload: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    """hooks/mod.rs: replace ${PLUGIN_ROOT}, set PLUGIN_ROOT, run with `sh -c`."""
    expanded = command.replace("${PLUGIN_ROOT}", str(PLUGIN_ROOT))
    return subprocess.run(
        ["sh", "-c", expanded],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PLUGIN_ROOT": str(PLUGIN_ROOT), "HARNESS_NOTIFY": "0"},
    )


def test_hook_commands_run_as_goose_runs_them(repo: Repo) -> None:
    rules = hook_rules()
    (pre,) = rules["PreToolUse"][0]["hooks"]
    tool = {
        "event": "PreToolUse",
        "session_id": "s",
        "tool_name": "shell",
        "working_dir": str(repo.path),
    }
    blocked = run_like_goose(pre["command"], {**tool, "tool_input": {"command": "git push -f"}})
    assert (blocked.returncode, blocked.stdout) == (2, "")
    assert "--force" in blocked.stderr
    allowed = run_like_goose(pre["command"], {**tool, "tool_input": {"command": "git status"}})
    assert (allowed.returncode, allowed.stdout, allowed.stderr) == (0, "", "")

    (start,) = rules["SessionStart"][0]["hooks"]
    started = run_like_goose(start["command"], {"event": "SessionStart", "session_id": "s"})
    assert started.returncode == 0
    assert json.loads(started.stdout)["banner"]

    (stop,) = rules["Stop"][0]["hooks"]
    stopped = run_like_goose(stop["command"], {"event": "Stop", "session_id": "s"})
    assert (stopped.returncode, stopped.stdout) == (0, "")


def mcp_template() -> dict[str, Any]:
    return json.loads((PLUGIN_ROOT / ".mcp.json.template").read_text())


def test_mcp_template_registers_exactly_the_contract_servers() -> None:
    servers = mcp_template()["mcpServers"]
    assert set(servers) == set(CONTRACT_SERVERS)
    for name, directory in CONTRACT_SERVERS.items():
        assert servers[name]["command"] == "uv"
        assert servers[name]["args"] == [
            "run",
            "--project",
            f"${{HARNESS_ROOT}}/{directory}",
            f"harness-{name}-mcp",
        ]


def test_mcp_template_only_uses_fields_mcp_servers_rs_parses() -> None:
    for server in mcp_template()["mcpServers"].values():
        assert set(server) <= {"command", "args", "env", "cwd"}


def test_mcp_template_never_shadows_inherited_env() -> None:
    """mcp_servers.rs copies `env` values literally (only ${PLUGIN_ROOT} expands)
    and stdio children inherit goose's environment, so `"X": "${X}"` would
    replace a real token with the literal text "${X}"."""
    for server in mcp_template()["mcpServers"].values():
        assert "env" not in server
    passthrough = mcp_template()["x-harness-env-passthrough"]
    assert set(passthrough) == CONTRACT_ENV


def test_mcp_template_renders_to_valid_json_with_no_placeholders_left() -> None:
    text = (PLUGIN_ROOT / ".mcp.json.template").read_text()
    assert text.count("${HARNESS_ROOT}") == 3
    rendered = json.loads(text.replace("${HARNESS_ROOT}", "/opt/harness"))
    assert "${" not in json.dumps(rendered)
    assert rendered["mcpServers"]["coord"]["args"][2] == "/opt/harness/coord"


def test_rendered_mcp_json_is_ignored_by_git() -> None:
    assert ".mcp.json" in (PLUGIN_ROOT / ".gitignore").read_text().split()


def test_scripts_are_stdlib_only() -> None:
    allowed_roots = {
        "__future__",
        "collections",
        "dataclasses",
        "json",
        "os",
        "pathlib",
        "platform",
        "re",
        "shlex",
        "shutil",
        "subprocess",
        "sys",
        "time",
        "typing",
        "urllib",
    }
    for script in SCRIPTS.glob("*.py"):
        imports = re.findall(r"^(?:from|import) ([\w.]+)", script.read_text(), re.MULTILINE)
        roots = {name.split(".")[0] for name in imports}
        assert roots <= allowed_roots, (script.name, roots - allowed_roots)
        assert roots, script.name


def test_scripts_target_python_312_syntax() -> None:
    for script in SCRIPTS.glob("*.py"):
        ast.parse(script.read_text(), str(script), feature_version=(3, 12))


def test_matcher_works_under_the_rust_regex_subset() -> None:
    """Rust `regex` lacks lookaround and backreferences; keep the matcher plain."""
    assert not re.search(r"\(\?[=!<]|\\\d", shell_matcher())
