import ast
import re
from pathlib import Path

import pytest
import yaml

import recipe_schema as rs

RECIPES_DIR = Path(__file__).resolve().parent.parent
HARNESS_DIR = RECIPES_DIR.parent
EXPECTED_RECIPES = {
    "bmad-plan", "bmad-shard", "bmad-story", "fan-out", "start-day", "end-day", "qa-check",
}
RECIPE_PATHS = sorted(RECIPES_DIR.glob("*.yaml"))

WRITE_TOOLS = {
    "agent-harness_clickup__create_task", "agent-harness_clickup__add_comment", "agent-harness_clickup__update_task", "agent-harness_clickup__set_status",
    "agent-harness_slack__post_message", "agent-harness_slack__reply", "agent-harness_coord__worktree_create",
}


def load(name: str) -> tuple[dict, str]:
    text = (RECIPES_DIR / f"{name}.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text), text


def validate_text(text: str, recipe_dir: Path = RECIPES_DIR) -> list[str]:
    return rs.validate_recipe(yaml.safe_load(text), text, recipe_dir)


def test_every_expected_recipe_exists_and_nothing_else():
    assert {p.stem for p in RECIPE_PATHS} == EXPECTED_RECIPES


@pytest.mark.parametrize("path", RECIPE_PATHS, ids=lambda p: p.stem)
def test_recipe_is_valid(path):
    text = path.read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    assert document["title"] and document["description"]
    assert rs.validate_recipe(document, text, path.parent) == []


@pytest.mark.parametrize("path", RECIPE_PATHS, ids=lambda p: p.stem)
def test_recipe_declares_exactly_the_parameters_it_uses(path):
    text = path.read_text(encoding="utf-8")
    declared = {p["key"] for p in yaml.safe_load(text).get("parameters", [])}
    used, errors = rs.template_variables(text)
    assert errors == []
    assert declared, "every harness recipe takes at least one parameter"
    assert used - rs.BUILT_IN_PARAMS == declared


# Each recipe's delegate sources, exactly. A presence check, so the absence
# check inside validate_recipe (unknown persona) is not vacuous.
EXPECTED_SOURCES = {
    "bmad-plan": {"analyst", "pm", "architect"},
    "bmad-shard": {"po"},
    "bmad-story": {"sm", "dev", "qa"},
    "fan-out": {"dev", "qa", "regression", "devsecops"},
    "start-day": set(),
    "end-day": set(),
    "qa-check": set(),
}


@pytest.mark.parametrize("name", sorted(EXPECTED_SOURCES))
def test_delegate_sources_are_personas(name):
    _, text = load(name)
    sources = rs.source_references(text)
    assert sources == EXPECTED_SOURCES[name]
    assert sources <= rs.PERSONAS


# Smoke defect D3: `goose run --max-turns` does not reach delegates, which
# otherwise run up to 1000 turns. Builders get 60, reviewers 30.
EXPECTED_MAX_TURNS = {
    "bmad-plan": {"analyst": 40, "pm": 40, "architect": 40},
    "bmad-shard": {"po": 60},
    "bmad-story": {"sm": 30, "dev": 60, "qa": 30},
    "fan-out": {"dev": 60, "qa": 30, "regression": 30, "devsecops": 30},
}


@pytest.mark.parametrize("name", sorted(EXPECTED_SOURCES))
def test_every_delegate_call_passes_its_turn_cap(name):
    _, text = load(name)
    calls = rs.delegate_max_turns(text)
    expected = EXPECTED_MAX_TURNS.get(name, {})
    assert {source for source, _ in calls} == set(expected)
    for source, cap in calls:
        assert cap == expected[source], (source, cap)
    assert rs.delegate_turn_cap_errors(text) == []


def test_bmad_plan_delegates_in_order_analyst_pm_architect():
    _, text = load("bmad-plan")
    order = [m.group(1) for m in rs.SOURCE_REF_RE.finditer(text)]
    assert order == ["analyst", "pm", "architect"]


# Tools each recipe must reference. Paired with validate_recipe's check that
# every reference is in the contract.
REQUIRED_TOOLS = {
    "bmad-shard": {"agent-harness_clickup__list_tasks", "agent-harness_clickup__create_task"},
    "bmad-story": {
        "agent-harness_coord__worktree_create", "agent-harness_coord__worktree_list", "agent-harness_coord__claim_signals",
        "agent-harness_coord__changed_paths", "agent-harness_coord__verify_assertions",
        "agent-harness_clickup__add_comment", "agent-harness_clickup__set_status", "agent-harness_clickup__list_statuses",
    },
    "fan-out": {
        "agent-harness_coord__claim_signals", "agent-harness_coord__worktree_create", "agent-harness_coord__worktree_list",
        "agent-harness_coord__gen_hash", "agent-harness_coord__changed_paths", "agent-harness_coord__verify_assertions",
        "agent-harness_slack__find_claims", "agent-harness_slack__post_message", "agent-harness_slack__reply",
        "agent-harness_clickup__get_task", "agent-harness_clickup__get_comments", "agent-harness_clickup__list_tasks",
        "agent-harness_clickup__list_statuses", "agent-harness_clickup__add_comment", "agent-harness_clickup__set_status",
    },
    "start-day": {
        "agent-harness_coord__worktree_list", "agent-harness_coord__claim_signals", "agent-harness_slack__read_channel",
        "agent-harness_slack__read_thread", "agent-harness_slack__find_claims", "agent-harness_clickup__list_tasks",
        "agent-harness_clickup__list_statuses", "agent-harness_clickup__get_task", "agent-harness_clickup__get_comments",
    },
    "end-day": {"agent-harness_coord__worktree_list", "agent-harness_clickup__list_tasks", "agent-harness_clickup__add_comment", "agent-harness_clickup__create_task"},
    "qa-check": {"agent-harness_coord__changed_paths", "agent-harness_coord__verify_assertions"},
}


@pytest.mark.parametrize("name", sorted(REQUIRED_TOOLS))
def test_recipe_references_its_contract_tools(name):
    _, text = load(name)
    refs = rs.tool_references(text)
    assert REQUIRED_TOOLS[name] <= refs
    assert rs.tool_reference_errors(text) == []


VERIFY_REF = "agent-harness_coord__verify_assertions"
VERIFY_CALL_RE = re.compile(r"agent-harness_coord__verify_assertions\(paths=[^()]*?,\s*repo=")


@pytest.mark.parametrize("name", ["bmad-story", "fan-out", "qa-check"])
def test_verify_assertions_is_always_called_with_the_worktree_as_repo(name):
    # Smoke defect D12: without repo, the repo-relative paths changed_paths
    # returns resolve against the MCP server's cwd, the session's directory.
    document, _ = load(name)
    instructions = document["instructions"]
    mentions = instructions.count(VERIFY_REF)
    assert mentions >= 1
    assert len(VERIFY_CALL_RE.findall(instructions)) == mentions


def test_start_day_writes_nothing():
    _, text = load("start-day")
    refs = rs.tool_references(text)
    assert refs & WRITE_TOOLS == set()
    assert {"agent-harness_slack__find_claims", "agent-harness_clickup__list_tasks", "agent-harness_coord__claim_signals"} <= refs


def test_end_day_never_changes_a_status_or_merges():
    _, text = load("end-day")
    refs = rs.tool_references(text)
    assert "agent-harness_clickup__set_status" not in refs
    assert "agent-harness_clickup__add_comment" in refs
    assert "Never merge" in text
    assert "gh pr merge --" not in text


@pytest.mark.parametrize("name", ["bmad-story", "fan-out"])
def test_status_changes_follow_a_comment(name):
    _, text = load(name)
    comments = [m.start() for m in re.finditer(r"agent-harness_clickup__add_comment", text)]
    statuses = [m.start() for m in re.finditer(r"agent-harness_clickup__set_status", text)]
    assert comments and statuses
    assert comments[0] < statuses[0]
    assert "never set done or closed" in text.lower()


def test_fan_out_posts_slack_claims_only_when_a_channel_is_set():
    document, text = load("fan-out")
    claim_param = next(p for p in document["parameters"] if p["key"] == "claim_channel")
    assert claim_param["requirement"] == "optional"
    assert claim_param["default"] == "none"
    assert '"none" means Slack claims are off' in text
    assert "agent-harness_slack__post_message(channel=\"{{claim_channel}}\"" in text
    assert "Post the claim, if claims are on" in text


def test_fan_out_caps_concurrency_at_five_and_forbids_merges():
    document, text = load("fan-out")
    cap = next(p for p in document["parameters"] if p["key"] == "max_agents")
    assert int(cap["default"]) <= 5
    assert "**No merges and no PRs.**" in text
    assert "**Plan before any write.**" in text
    assert "**Rulings stop work.**" in text
    assert text.index("Present the plan and stop") < text.index("agent-harness_coord__worktree_create(slug")


def test_bmad_story_completion_protocol_schema():
    document, _ = load("bmad-story")
    schema = document["response"]["json_schema"]
    assert schema["properties"]["status"]["enum"] == ["DONE", "DONE_WITH_CONCERNS", "BLOCKED", "NEEDS_CONTEXT"]
    assert {"status", "branch", "sha", "evidence"} <= set(schema["required"])
    assert {"gate", "mutation", "verify_assertions", "qa"} <= set(schema["properties"]["evidence"]["required"])


def test_bmad_story_retry_runs_gate_and_verify_assertions_in_the_worktree():
    document, _ = load("bmad-story")
    commands = [c["command"] for c in document["retry"]["checks"]]
    assert len(commands) == 2
    assert all(c.startswith('cd "{{project_dir}}.wt/{{slug}}" && ') for c in commands)
    assert commands[0].endswith("{{gate_command}}")
    assert "{{harness_coord}} verify-assertions $files" in commands[1]
    assert "git diff --name-only --diff-filter=AM" in commands[1]


def test_interactive_recipes_have_no_response_schema():
    # ops_recipe.rs (RecipeOperation::run) injects a continuation message at
    # every turn end until final_output is called, so a recipe with
    # response.json_schema cannot pause for a human. These recipes pause.
    for name in ("bmad-plan", "bmad-shard", "fan-out", "start-day", "end-day", "qa-check"):
        document, _ = load(name)
        assert "response" not in document, name
    document, _ = load("bmad-story")
    assert "json_schema" in document["response"]


def test_no_recipe_uses_sub_recipes_or_hardcodes_server_commands():
    for path in RECIPE_PATHS:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "sub_recipes" not in document, path.stem
        assert "extensions" not in document, path.stem
    assert RECIPE_PATHS


# --- validator self-tests: each guard goes red on a broken recipe -----------

GOOD = """\
version: 1.0.0
title: t
description: d
parameters:
  - key: who
    input_type: string
    requirement: required
    description: x
instructions: |
  Hello {{who}}. Use agent-harness_coord__gen_hash and delegate(source: "dev", max_turns: 60).
"""


def test_validator_accepts_the_good_fixture():
    assert validate_text(GOOD) == []


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda t: t + "subrecipes: []\n", "recipe: unknown key 'subrecipes'"),
        (lambda t: t + "settings:\n  max_turn: 3\n", "settings: unknown key 'max_turn'"),
        (lambda t: t.replace("title: t\n", ""), "recipe: missing required key 'title'"),
        (lambda t: t.replace("version: 1.0.0\n", ""), "recipe: missing required key 'version'"),
        (lambda t: t.replace("Hello {{who}}", "Hello {{who}} {{where}}"), "{{where}} is used but not declared"),
        (lambda t: t.replace("Hello {{who}}", "Hello"), "'who' is declared but never used"),
        (lambda t: t.replace("agent-harness_coord__gen_hash", "agent-harness_coord__gen_hashes"), "agent-harness_coord__gen_hashes is not in the MCP"),
        (lambda t: t.replace("agent-harness_coord__gen_hash", "github__create_pr"), "github__create_pr is not in the MCP"),
        (lambda t: t.replace('source: "dev"', 'source: "developer"'), "source 'developer' is not one"),
        (lambda t: t.replace(", max_turns: 60", ""), "source 'dev' passes no max_turns"),
        (lambda t: t.replace("max_turns: 60", "max_turns: 0"), "max_turns must be at least 1"),
        (lambda t: t.replace('delegate(source: "dev", max_turns: 60)',
                             'delegate(working_dir: "x", source: "dev", max_turns: 60)'),
         "call opens with 'working_dir'"),
        (lambda t: t.replace('delegate(source: "dev", max_turns: 60)',
                             'delegate(source: "dev", max_turns: 60) and '
                             'delegate(resume: "<id>", max_turns: 5, instructions: "x")'),
         "resume accepts only instructions"),
        (lambda t: t.replace("requirement: required", "requirement: optional"), "optional parameter needs a default"),
        (lambda t: t.replace("input_type: string", "input_type: text"), "input_type 'text'"),
        (lambda t: t.replace("Hello", "Hello {% if who %}x{% endif %}"), "minijinja block/comment syntax"),
        (lambda t: t.replace("Hello", "Hello ${#arr}"), "minijinja block/comment syntax"),
        (lambda t: t.replace("Hello {{who}}", "Hello {{who}} {{ who.name }}"), "unsupported expression"),
        (lambda t: t + "retry:\n  max_retries: 0\n  checks:\n    - type: shell\n      command: 'true'\n",
         "retry.max_retries: must be an integer greater than 0"),
        (lambda t: t + "retry:\n  max_retries: 1\n  checks:\n    - type: python\n      command: 'true'\n",
         "type 'python'"),
        (lambda t: t + "response:\n  json_schema:\n    type: object\n    required: [a]\n    properties: {}\n",
         "required 'a' is not a declared property"),
        (lambda t: t + "sub_recipes:\n  - name: x\n    path: missing.yaml\n",
         "path 'missing.yaml' does not exist"),
        (lambda t: t + "extensions:\n  - type: stdio\n    name: x\n    cmd: y\n", "missing required key 'args'"),
    ],
)
def test_validator_rejects_mutation(mutate, expected):
    errors = validate_text(mutate(GOOD))
    assert any(expected in e for e in errors), errors


@pytest.mark.parametrize("check_type", ["shell", "Shell"])
def test_validator_accepts_the_shell_alias(check_type):
    text = GOOD + f"retry:\n  max_retries: 1\n  checks:\n    - type: {check_type}\n      command: 'true'\n"
    assert validate_text(text) == []


def test_validator_accepts_an_existing_sub_recipe_with_extensions(tmp_path):
    (tmp_path / "child.yaml").write_text("x: 1\n")
    text = GOOD + (
        "extensions:\n  - type: platform\n    name: developer\n"
        "sub_recipes:\n  - name: child\n    path: child.yaml\n"
    )
    assert validate_text(text, tmp_path) == []
    missing_ext = GOOD + "sub_recipes:\n  - name: child\n    path: child.yaml\n"
    assert any("session gets only summon" in e for e in validate_text(missing_ext, tmp_path))


def test_validator_reads_a_recipe_nested_under_a_recipe_key():
    nested = "recipe:\n" + "".join(f"  {line}\n" for line in GOOD.splitlines())
    assert validate_text(nested) == []
    assert any("unknown key 'bogus'" in e for e in validate_text(nested + "  bogus: 1\n"))


# --- cross-slice contract checks: run once the sibling slices are stacked ----


def _frontmatter_name(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    meta = yaml.safe_load(text.split("---", 2)[1])
    return meta.get("name") if isinstance(meta, dict) else None


def test_personas_exist_as_agent_files_when_present():
    agents_dir = HARNESS_DIR / "agents"
    if not agents_dir.is_dir():
        pytest.skip("harness/agents/ is not in this tree (slice 5 not stacked)")
    names = {_frontmatter_name(p) for p in agents_dir.glob("*.md")}
    assert rs.PERSONAS <= names


def _mcp_tool_names(root: Path) -> set[str]:
    names = set()
    for path in root.rglob("*.py"):
        if ".venv" in path.parts or "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for deco in node.decorator_list:
                    target = deco.func if isinstance(deco, ast.Call) else deco
                    if isinstance(target, ast.Attribute) and target.attr == "tool":
                        names.add(node.name)
    return names


@pytest.mark.parametrize(
    "ext, root",
    [("coord", "coord"), ("clickup", "clients/clickup"), ("slack", "clients/slack")],
)
def test_contract_matches_the_real_mcp_server_when_present(ext, root):
    server_root = HARNESS_DIR / root / "src"
    if not server_root.is_dir():
        pytest.skip(f"harness/{root}/ is not in this tree (its slice is not stacked)")
    assert _mcp_tool_names(server_root) == rs.CONTRACT_TOOLS[ext]


def _step(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n  ## ", start + len(heading))
    return text[start:end if end != -1 else len(text)]


def _persona(name: str) -> str:
    path = HARNESS_DIR / "agents" / f"{name}.md"
    if not path.is_file():
        pytest.skip("harness/agents/ is not in this tree (slice 5 not stacked)")
    return path.read_text(encoding="utf-8")


def test_bmad_story_dev_brief_and_dev_persona_agree_on_the_story_file():
    """Smoke defect D10: the brief said touch only the listed files, while the
    persona has dev set Status and fill the Dev record in the story file."""
    _, text = load("bmad-story")
    brief = " ".join(_step(text, "## Step 2: dev builds").split())
    persona = " ".join(_persona("dev").split())

    assert "Touch only the files the story lists, plus the story file itself" in brief
    assert "only its Status: line and its Dev record" in brief
    assert "The story file is always in scope" in persona
    assert "only its `Status:` line and its **Dev record**" in persona
    assert "Never edit the story's ACs, test plan or gates" in persona


def test_bmad_story_qa_brief_and_qa_persona_agree_on_the_mutation_worktree():
    """Smoke defect D9: the brief said read-only, then asked for a mutation.
    Both now put the mutation in a throwaway coord worktree that is never pushed."""
    _, text = load("bmad-story")
    brief = " ".join(_step(text, "## Step 3: qa reviews").split())
    persona = " ".join(_persona("qa").split())

    assert "This is read-only: do not edit or commit" not in brief
    assert (
        "agent-harness_coord__worktree_create(slug=qa-{{slug}}, branch=qa/{{slug}}, "
        "base=origin/{{branch}}, repo={{project_dir}})"
    ) in brief
    assert "Never push qa/{{slug}}" in brief
    assert "worktree remove --force {{project_dir}}.wt/qa-{{slug}}" in brief
    assert "branch -D qa/{{slug}}" in brief
    assert "slug `qa-<slug>`, branch `qa/<slug>`, and base `origin/<story branch>`" in persona
    assert "Never push the QA branch" in persona
    assert "git worktree remove" in persona and "git branch -D qa/<slug>" in persona
