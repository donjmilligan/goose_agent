"""Validate the harness against a real goose binary, with no model and no tokens.

Skipped unless GOOSE_BIN names a goose executable. Run it with
`harness/recipes/tests/goose_validate.sh [path/to/goose]`.

Everything runs in a throwaway sandbox: GOOSE_PATH_ROOT and HOME point at a
temporary directory, so the real ~/.config/goose, ~/.agents and ~/.local are
never read or written. Covers:

- `goose recipe validate`, `--explain` and `--render-recipe` on every recipe;
- `goose mcp-probe` against the coord, clickup and slack servers: the exact
  tool lists, a clean tool error from every clickup/slack tool when no token
  is set, and real coord calls (gen_hash, claim_signals) through goose's client;
- after `harness/install.sh` into the sandbox: `goose review --dry-run` finds
  the four checks, `goose recipe list` finds the seven recipes, and summon's
  `load` tool (called over `goose acp`, no model) lists and loads all ten
  personas. Every `agent-harness_*__*` tool the recipes name must be in the
  session's live tool list.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest
import recipe_schema as rs
import yaml

GOOSE_BIN = os.environ.get("GOOSE_BIN", "")
pytestmark = pytest.mark.skipif(
    not (GOOSE_BIN and os.access(GOOSE_BIN, os.X_OK)),
    reason="set GOOSE_BIN to a goose executable to run the live goose validation",
)

RECIPES_DIR = Path(__file__).resolve().parent.parent
HARNESS_DIR = RECIPES_DIR.parent
RECIPE_PATHS = sorted(RECIPES_DIR.glob("*.yaml"))
CHECK_NAMES = {p.stem for p in (HARNESS_DIR / "checks").glob("*.md") if p.name != "README.md"}
TIMEOUT = 300

# The contract, spelled out here on purpose (not imported) so a drift in
# recipe_schema.CONTRACT_TOOLS is caught too; see test_contract_matches_schema.
SERVER_TOOLS = {
    "coord": [
        "claim_signals",
        "worktree_create",
        "worktree_list",
        "gen_hash",
        "changed_paths",
        "verify_assertions",
    ],
    "clickup": [
        "get_task",
        "list_tasks",
        "get_comments",
        "list_statuses",
        "create_task",
        "add_comment",
        "update_task",
        "set_status",
    ],
    "slack": ["read_channel", "read_thread", "post_message", "reply", "find_claims"],
}
SERVER_DIRS = {
    "coord": HARNESS_DIR / "coord",
    "clickup": HARNESS_DIR / "clients" / "clickup",
    "slack": HARNESS_DIR / "clients" / "slack",
}
TOKEN_VARS = {"clickup": "CLICKUP_API_TOKEN", "slack": "SLACK_BOT_TOKEN"}
SCRUBBED_ENV = {
    "VIRTUAL_ENV",
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "XDG_DATA_HOME",
    "CLICKUP_API_TOKEN",
    "SLACK_BOT_TOKEN",
    "HARNESS_ENV_FILE",
    "HARNESS_SLACK_ALLOWED_CHANNELS",
    "HARNESS_CLAIM_CHANNEL",
    "HARNESS_ALLOW_DONE",
    "GOOSE_PROVIDER",
    "GOOSE_MODEL",
    "GOOSE_MODE",
    "GOOSE_RECIPE_PATH",
}


def _uv_dir(*args: str) -> str | None:
    uv = shutil.which("uv")
    if not uv:
        return None
    out = subprocess.run([uv, *args], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


@pytest.fixture(scope="session")
def sandbox(tmp_path_factory) -> dict[str, Any]:
    base = tmp_path_factory.mktemp("goose-sandbox")
    (base / "root").mkdir()
    (base / "home").mkdir()
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_ENV}
    # Reuse the caller's uv cache and managed Pythons so the sandboxed HOME
    # doesn't force a fresh download. These are uv's, not goose's, state.
    for var, args in (("UV_CACHE_DIR", ("cache", "dir")), ("UV_PYTHON_INSTALL_DIR", ("python", "dir"))):
        if var not in env and (value := _uv_dir(*args)):
            env[var] = value
    env.update(
        GOOSE_PATH_ROOT=str(base / "root"),
        HOME=str(base / "home"),
        GOOSE_DISABLE_KEYRING="1",
    )
    return {"base": base, "env": env, "home": base / "home"}


def goose(sandbox, *args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [GOOSE_BIN, *args],
        cwd=cwd or sandbox["base"],
        env=sandbox["env"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=TIMEOUT,
        check=False,
    )
    if check:
        assert result.returncode == 0, f"goose {' '.join(args)}\n{result.stdout}\n{result.stderr}"
    return result


@pytest.fixture(scope="session")
def toy_repo(sandbox) -> Path:
    """A clone with an origin that has `main` and `feature/login-page`."""
    base = sandbox["base"] / "toy"
    origin, repo = base / "origin.git", base / "repo"
    env = {
        **sandbox["env"],
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    def git(*args: str, cwd: Path = base) -> None:
        subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)

    base.mkdir()
    git("init", "-q", "--bare", str(origin))
    git("init", "-q", "-b", "main", str(repo))
    git("commit", "-q", "--allow-empty", "-m", "init", cwd=repo)
    git("remote", "add", "origin", str(origin), cwd=repo)
    git("push", "-q", "origin", "main", "main:feature/login-page", cwd=repo)
    return repo


# --- recipes -----------------------------------------------------------------


@pytest.mark.parametrize("path", RECIPE_PATHS, ids=lambda p: p.stem)
def test_goose_recipe_validate_accepts_recipe(sandbox, path):
    result = goose(sandbox, "recipe", "validate", str(path))
    assert "recipe file is valid" in result.stdout


@pytest.mark.parametrize("path", RECIPE_PATHS, ids=lambda p: p.stem)
def test_goose_explain_lists_every_declared_parameter(sandbox, path):
    declared = yaml.safe_load(path.read_text(encoding="utf-8"))["parameters"]
    out = goose(sandbox, "run", "--recipe", str(path), "--explain").stdout
    shown = set(re.findall(r"^\s+- ([a-z_]+) \(", out, re.MULTILINE))
    assert shown == {p["key"] for p in declared}
    required = {p["key"] for p in declared if p["requirement"] == "required"}
    missing = set(re.findall(r"--params ([a-z_]+)=your_value", out))
    assert missing == required


def _required_params(path: Path) -> dict[str, str]:
    params = yaml.safe_load(path.read_text(encoding="utf-8"))["parameters"]
    return {p["key"]: f"SENTINEL-{p['key']}" for p in params if p["requirement"] == "required"}


@pytest.mark.parametrize("path", RECIPE_PATHS, ids=lambda p: p.stem)
def test_goose_renders_recipe_with_its_required_params(sandbox, path):
    values = _required_params(path)
    args = [a for k, v in values.items() for a in ("--params", f"{k}={v}")]
    out = goose(sandbox, "run", "--recipe", str(path), "--render-recipe", *args).stdout
    assert not re.search(r"\{\{|\{%", out), "unrendered template left in the recipe"
    rendered = yaml.safe_load(out)
    assert rendered["title"] == yaml.safe_load(path.read_text(encoding="utf-8"))["title"]
    for key, value in values.items():
        assert value in out, f"{key} was not substituted"
    for check in (rendered.get("retry") or {}).get("checks", []):
        assert "SENTINEL-project_dir" in check["command"]


@pytest.mark.parametrize("path", RECIPE_PATHS, ids=lambda p: p.stem)
def test_goose_refuses_to_render_without_required_params(sandbox, path):
    # Positive control for the render test: goose does enforce `required`.
    result = goose(sandbox, "run", "--recipe", str(path), "--render-recipe", check=False)
    assert result.returncode != 0
    assert "--params" in result.stdout + result.stderr


# --- MCP servers through goose's client ----------------------------------------


def probe(sandbox, server: str, steps: list[dict]) -> list[dict]:
    script = sandbox["base"] / f"probe-{server}.json"
    script.write_text(json.dumps({"steps": steps}), encoding="utf-8")
    command = f"uv run --project {SERVER_DIRS[server]} harness-{server}-mcp"
    result = goose(sandbox, "mcp-probe", command, "--script", str(script))
    return json.loads(result.stdout)["results"]


def test_contract_matches_schema():
    assert {k: set(v) for k, v in SERVER_TOOLS.items()} == rs.CONTRACT_TOOLS


@pytest.mark.parametrize("server", sorted(SERVER_TOOLS))
def test_server_exposes_exactly_its_contract_tools(sandbox, server):
    (listing,) = probe(sandbox, server, [{"action": "listTools"}])
    assert [t["name"] for t in listing["result"]["tools"]] == SERVER_TOOLS[server]


def _dummy_arguments(schema: dict) -> dict[str, Any]:
    samples = {"string": "x", "integer": 1, "number": 1, "boolean": False, "array": ["x"]}
    props = schema.get("properties", {})
    out = {}
    for name in schema.get("required", []):
        kind = props[name].get("type") or props[name].get("anyOf", [{}])[0].get("type", "string")
        out[name] = samples[kind]
    return out


@pytest.mark.parametrize("server", sorted(TOKEN_VARS))
def test_every_tool_without_a_token_returns_a_clean_tool_error(sandbox, server):
    (listing,) = probe(sandbox, server, [{"action": "listTools"}])
    tools = listing["result"]["tools"]
    calls = [{"action": "callTool", "name": t["name"], "arguments": _dummy_arguments(t["inputSchema"])} for t in tools]
    # One probe session: every later call succeeding also proves the server
    # survived each error rather than crashing.
    results = probe(sandbox, server, calls)
    assert [r["name"] for r in results] == SERVER_TOOLS[server]
    for r in results:
        assert r["result"]["isError"] is True, r
        text = r["result"]["content"][0]["text"]
        assert TOKEN_VARS[server] in text, text
        assert "Traceback" not in text


def test_coord_gen_hash_through_goose(sandbox):
    results = probe(
        sandbox,
        "coord",
        [
            {"action": "callTool", "name": "gen_hash", "arguments": {}},
            {"action": "callTool", "name": "gen_hash", "arguments": {"length": 12}},
            {"action": "callTool", "name": "gen_hash", "arguments": {"length": 0}},
        ],
    )
    default, long, bad = (r["result"] for r in results)
    assert default["isError"] is False and re.fullmatch(r"[a-z0-9]{4}", default["structuredContent"]["hash"])
    assert re.fullmatch(r"[a-z0-9]{12}", long["structuredContent"]["hash"])
    assert _coord_refusal(bad, "gen_hash")["error"]["code"] == "invalid_argument"


def _coord_refusal(result: dict, tool: str) -> dict:
    """A coord refusal must reach goose as a tool error, its envelope in the text."""
    assert result["isError"] is True, result
    assert "structuredContent" not in result
    (content,) = result["content"]
    prefix = f"Error executing tool {tool}: "
    assert content["text"].startswith(prefix), content["text"]
    payload = json.loads(content["text"].removeprefix(prefix))
    assert payload["ok"] is False
    return payload


def test_coord_refusal_is_a_tool_error_through_goose(sandbox, toy_repo):
    results = probe(
        sandbox,
        "coord",
        [
            {
                "action": "callTool",
                "name": "worktree_create",
                "arguments": {"slug": "../bad", "branch": "feature/bad", "repo": str(toy_repo)},
            },
            {"action": "callTool", "name": "verify_assertions", "arguments": {"paths": []}},
            {"action": "callTool", "name": "worktree_list", "arguments": {"repo": str(toy_repo)}},
        ],
    )
    bad_slug, no_paths, listing = (r["result"] for r in results)
    error = _coord_refusal(bad_slug, "worktree_create")["error"]
    assert error["code"] == "invalid_argument" and "invalid slug" in error["message"]
    assert _coord_refusal(no_paths, "verify_assertions")["error"]["code"] == "invalid_argument"
    # Same session, after two refusals: success is still structured and not an error.
    assert listing["isError"] is False and listing["structuredContent"]["count"] == 1


def test_coord_claim_signals_through_goose(sandbox, toy_repo):
    results = probe(
        sandbox,
        "coord",
        [
            {
                "action": "callTool",
                "name": "claim_signals",
                "arguments": {"topic": "Login Page", "repo": str(toy_repo)},
            },
            {
                "action": "callTool",
                "name": "claim_signals",
                "arguments": {"topic": "nothing-here", "repo": str(toy_repo)},
            },
        ],
    )
    hit, miss = (r["result"]["structuredContent"] for r in results)
    assert hit["ok"] and hit["matched"] and hit["remote_branches"] == ["feature/login-page"]
    # The miss is only meaningful because the same call did read two branches.
    assert miss["ok"] and not miss["matched"] and miss["remote_branches"] == []
    assert miss["remote_branch_count"] == 2 and miss["worktree_count"] == 1


# --- discovery after install.sh ------------------------------------------------


@pytest.fixture(scope="session")
def installed(sandbox) -> dict[str, Any]:
    result = subprocess.run(
        [str(HARNESS_DIR / "install.sh")],
        env=sandbox["env"],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return sandbox


def test_goose_review_discovers_the_four_checks(installed, toy_repo):
    (toy_repo / "change.txt").write_text("x\n", encoding="utf-8")
    result = goose(installed, "review", "--dry-run", cwd=toy_repo)
    out = result.stdout + result.stderr
    count = re.search(r"discovered (\d+) check\(s\)", out)
    assert count and int(count.group(1)) == 4
    assert set(re.findall(r"^  - ([a-z0-9-]+) \(scope:", out, re.MULTILINE)) == CHECK_NAMES


def test_goose_recipe_list_finds_the_installed_recipes(installed, toy_repo):
    listing = json.loads(goose(installed, "recipe", "list", "--format", "json", cwd=toy_repo).stdout)
    home_recipes = installed["home"] / ".agents" / "recipes"
    found = {r["name"] for r in listing if Path(r["path"]).parent == home_recipes}
    assert found == {p.stem for p in RECIPE_PATHS}


class Acp:
    """Minimal `goose acp` stdio client: newline-delimited JSON-RPC."""

    def __init__(self, env: dict[str, str], cwd: Path):
        self.proc = subprocess.Popen(
            [GOOSE_BIN, "acp"],
            cwd=cwd,
            env=env,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.next_id = 0

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.put(line)

    def call(self, method: str, params: dict) -> dict:
        assert self.proc.stdin is not None
        self.next_id += 1
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}) + "\n"
        )
        self.proc.stdin.flush()
        while True:
            message = json.loads(self.lines.get(timeout=TIMEOUT))
            if message.get("id") == self.next_id and "method" not in message:
                assert "error" not in message, message
                return message["result"]

    def close(self) -> None:
        self.proc.terminate()
        self.proc.wait(timeout=30)


@pytest.fixture(scope="session")
def acp_session(installed, toy_repo):
    # session/new needs a provider and model to resolve; nothing here prompts,
    # so a dummy ollama on a closed port is never contacted.
    env = {
        **installed["env"],
        "GOOSE_PROVIDER": "ollama",
        "GOOSE_MODEL": "none",
        "OLLAMA_HOST": "http://127.0.0.1:9",
        "GOOSE_MODE": "auto",
    }
    client = Acp(env, toy_repo)
    client.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
    session_id = client.call("session/new", {"cwd": str(toy_repo), "mcpServers": []})["sessionId"]
    yield client, session_id
    client.close()


def summon_load(acp_session, arguments: dict) -> dict:
    client, session_id = acp_session
    return client.call(
        "_goose/unstable/tools/call",
        {
            "sessionId": session_id,
            "extensionName": "summon",
            "name": "load",
            "arguments": arguments,
        },
    )


def _listed(text: str, heading: str) -> set[str]:
    section = text.split(f"{heading}:\n", 1)[1].split("\n\n", 1)[0]
    return set(re.findall(r"^• ([a-z0-9-]+) - ", section, re.MULTILINE))


def test_summon_load_lists_the_personas_and_recipes(acp_session):
    result = summon_load(acp_session, {})
    text = result["content"][0]["text"]
    assert _listed(text, "Agents") == rs.PERSONAS
    assert _listed(text, "Recipes") == {p.stem for p in RECIPE_PATHS}


@pytest.mark.parametrize("persona", sorted(rs.PERSONAS))
def test_summon_loads_each_persona(acp_session, persona):
    result = summon_load(acp_session, {"source": persona})
    assert result.get("isError") is not True
    assert result["content"][0]["text"].startswith(f"# Loaded: {persona} (agent)")


def test_every_tool_the_recipes_name_is_live(acp_session):
    client, session_id = acp_session
    live = {t["name"] for t in client.call("_goose/unstable/tools/list", {"sessionId": session_id})["tools"]}
    named = set()
    for path in RECIPE_PATHS:
        named |= set(re.findall(r"agent-harness_[a-z]+__[a-z_]+", path.read_text(encoding="utf-8")))
    assert named, "the recipes name no harness tools; the check below would be vacuous"
    assert named - live == set()
