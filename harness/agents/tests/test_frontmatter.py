"""Validate harness personas and review checks against goose's own parsers.

The rules below mirror goose source at 71dd9a9aa. Serde in both structs has no
`deny_unknown_fields`, so goose silently drops an unknown key; these tests turn
that silent misconfiguration into a failure.

- crates/goose/src/sources.rs:20-33 `parse_frontmatter`: `content.split("---")`,
  parts[1] is the YAML, parts[2..] re-joined is the body, fewer than 3 parts
  means "no frontmatter".
- crates/goose/src/agents/platform_extensions/summon.rs:88-94 `AgentMetadata`
  {name (required), description, model}.
- summon.rs:96-108 a "missing field" error makes the file silently not an agent.
- summon.rs:34 and :782 descriptions are truncated to 160 chars in the listing.
- summon.rs:209-246 every `*.md` in the directory is scanned (README.md too);
  the first file to claim a name wins.
- summon.rs:1208-1219 `model` becomes `goose_model` paired with the caller's
  provider, so pinning one breaks other providers.
- crates/goose/src/checks/mod.rs:24-34 `CheckFrontmatter` {name, description,
  model, turn-limit, tools, severity-default}.
- checks/mod.rs:88-94 a check must start with `---`.
- checks/mod.rs:138-152 repo-local checks: name must equal the file stem.
- checks/mod.rs:438 README.md is skipped in checks dirs.
- crates/goose-cli/src/commands/review/orchestrator.rs:210,677 severity is
  low|medium|high|critical, default medium.
- crates/goose-cli/src/commands/review/handler.rs:291-305 any check with
  `tools` makes `goose review --no-orchestrate` bail.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

HARNESS = Path(__file__).resolve().parents[2]
AGENTS_DIR = HARNESS / "agents"
CHECKS_DIR = HARNESS / "checks"

AGENT_KEYS = {"name", "description", "model"}
AGENT_REQUIRED = {"name", "description"}
CHECK_KEYS = {"name", "description", "model", "turn-limit", "tools", "severity-default"}
CHECK_REQUIRED = {"name", "description", "severity-default"}
SEVERITIES = {"low", "medium", "high", "critical"}
DESCRIPTION_BUDGET = 160

EXPECTED_PERSONAS = {
    "analyst",
    "pm",
    "architect",
    "po",
    "sm",
    "dev",
    "qa",
    "regression",
    "devsecops",
    "drift-identifier",
}
EXPECTED_CHECKS = {
    "vacuous-assertions",
    "scope-creep",
    "missing-tests-in-diff",
    "unsourced-numbers",
}

CONTRACT_TOOLS = {
    "agent-harness_coord__claim_signals",
    "agent-harness_coord__worktree_create",
    "agent-harness_coord__worktree_list",
    "agent-harness_coord__gen_hash",
    "agent-harness_coord__changed_paths",
    "agent-harness_coord__verify_assertions",
    "agent-harness_clickup__get_task",
    "agent-harness_clickup__list_tasks",
    "agent-harness_clickup__get_comments",
    "agent-harness_clickup__list_statuses",
    "agent-harness_clickup__create_task",
    "agent-harness_clickup__add_comment",
    "agent-harness_clickup__update_task",
    "agent-harness_clickup__set_status",
    "agent-harness_slack__read_channel",
    "agent-harness_slack__read_thread",
    "agent-harness_slack__post_message",
    "agent-harness_slack__reply",
    "agent-harness_slack__find_claims",
}

STATUSES = ("DONE", "DONE_WITH_CONCERNS", "BLOCKED", "NEEDS_CONTEXT")
PERSONA_SECTIONS = ("## Inputs", "## Outputs", "## Hard rules", "## Stop conditions")
COMPLETION_HEADING = "## Completion protocol"

TOOL_REF = re.compile(r"(?<![A-Za-z0-9_-])([a-z][a-z0-9_-]*?__[a-z][a-z0-9_]*)")


def rust_parse_frontmatter(text: str) -> tuple[object, str] | None:
    parts = text.split("---")
    if len(parts) < 3:
        return None
    return yaml.safe_load(parts[1].strip()), "---".join(parts[2:]).strip()


def tool_refs(text: str) -> set[str]:
    return set(TOOL_REF.findall(text))


def status_present(body: str, status: str) -> bool:
    return re.search(rf"(?<![A-Z_]){status}(?![A-Z_])", body) is not None


def _frontmatter_errors(
    text: str, stem: str, allowed: set[str], required: set[str]
) -> tuple[list[str], dict, str]:
    parsed = rust_parse_frontmatter(text)
    if parsed is None:
        return ["no frontmatter"], {}, ""
    meta, body = parsed
    if not isinstance(meta, dict):
        return ["frontmatter is not a mapping"], {}, body
    errors = [f"unknown key {k!r}" for k in sorted(set(meta) - allowed)]
    errors += [f"missing key {k!r}" for k in sorted(required - set(meta))]
    for key in required & set(meta):
        if not isinstance(meta[key], str) or not meta[key].strip():
            errors.append(f"empty {key!r}")
    if meta.get("name") != stem:
        errors.append(f"name {meta.get('name')!r} != file stem {stem!r}")
    if len(str(meta.get("description", ""))) > DESCRIPTION_BUDGET:
        errors.append(f"description over {DESCRIPTION_BUDGET} chars")
    if "model" in meta:
        errors.append("model is pinned; leave it to the caller's provider")
    unknown_tools = tool_refs(body) - CONTRACT_TOOLS
    errors += [f"tool {t!r} not in contract" for t in sorted(unknown_tools)]
    return errors, meta, body


def validate_agent(text: str, stem: str) -> list[str]:
    errors, _, body = _frontmatter_errors(text, stem, AGENT_KEYS, AGENT_REQUIRED)
    if not body:
        return errors
    errors += [f"missing section {s!r}" for s in PERSONA_SECTIONS if s not in body]
    h2 = re.findall(r"^## .*$", body, flags=re.MULTILINE)
    if not h2 or h2[-1] != COMPLETION_HEADING:
        errors.append(f"last section is not {COMPLETION_HEADING!r}")
        return errors
    tail = body.split(COMPLETION_HEADING, 1)[1]
    errors += [f"missing status {s}" for s in STATUSES if not status_present(tail, s)]
    if "Evidence rules" not in tail:
        errors.append("missing evidence rules")
    return errors


def validate_check(text: str, stem: str) -> list[str]:
    if not text.lstrip().startswith("---"):
        return ["check must start with ---"]
    errors, meta, body = _frontmatter_errors(text, stem, CHECK_KEYS, CHECK_REQUIRED)
    if meta.get("severity-default") not in SEVERITIES | {None}:
        errors.append(f"severity-default {meta.get('severity-default')!r} invalid")
    if "turn-limit" in meta and not (
        isinstance(meta["turn-limit"], int) and meta["turn-limit"] > 0
    ):
        errors.append("turn-limit must be a positive integer")
    if "tools" in meta:
        errors.append("tools breaks `goose review --no-orchestrate`")
    if meta and not body:
        errors.append("empty body")
    return errors


def persona_files() -> list[Path]:
    return sorted(p for p in AGENTS_DIR.glob("*.md") if p.name != "README.md")


def check_files() -> list[Path]:
    return sorted(p for p in CHECKS_DIR.glob("*.md") if p.name != "README.md")


GOOD_AGENT = """---
name: demo
description: A demo persona.
---
## Inputs
x
## Outputs
`agent-harness_coord__gen_hash`
## Hard rules
x
## Stop conditions
x
## Completion protocol
DONE, DONE_WITH_CONCERNS, BLOCKED, NEEDS_CONTEXT.
Evidence rules: cite.
"""

GOOD_CHECK = """---
name: demo
description: A demo check.
severity-default: high
---
Flag things.
"""


def test_persona_roster_is_the_planned_set():
    assert {p.stem for p in persona_files()} == EXPECTED_PERSONAS


def test_check_roster_is_the_planned_set():
    assert {p.stem for p in check_files()} == EXPECTED_CHECKS


@pytest.mark.parametrize("path", persona_files(), ids=lambda p: p.stem)
def test_persona_is_valid(path: Path):
    text = path.read_text()
    meta, _ = rust_parse_frontmatter(text)
    assert meta["name"] == path.stem
    assert validate_agent(text, path.stem) == []


@pytest.mark.parametrize("path", check_files(), ids=lambda p: p.stem)
def test_check_is_valid(path: Path):
    text = path.read_text()
    meta, _ = rust_parse_frontmatter(text)
    assert meta["severity-default"] in SEVERITIES
    assert validate_check(text, path.stem) == []


def test_names_are_unique_across_personas_and_checks():
    names = [rust_parse_frontmatter(p.read_text())[0]["name"] for p in persona_files() + check_files()]
    assert len(names) == len(EXPECTED_PERSONAS) + len(EXPECTED_CHECKS)
    assert len(set(names)) == len(names)


def test_readme_is_not_parsed_as_an_agent_but_names_every_persona():
    text = (AGENTS_DIR / "README.md").read_text()
    for name in EXPECTED_PERSONAS:
        assert f"**{name}**" in text
    assert rust_parse_frontmatter(text) is None  # absence-ok: paired with roster presence above


def test_personas_reference_contract_tools():
    refs = set().union(*(tool_refs(p.read_text()) for p in persona_files()))
    assert {"agent-harness_coord__worktree_create", "agent-harness_coord__verify_assertions", "agent-harness_coord__gen_hash"} <= refs
    assert refs <= CONTRACT_TOOLS


def test_personas_call_verify_assertions_with_the_worktree_as_repo():
    """Smoke defect D12: relative paths resolve against `repo`, else the server's cwd."""
    ref = "agent-harness_coord__verify_assertions"
    call = re.compile(r"agent-harness_coord__verify_assertions\(paths=[^()]*?,\s*repo=")
    texts = {p.stem: p.read_text() for p in persona_files()}
    callers = {name for name, text in texts.items() if ref in text}
    assert {"dev", "qa"} <= callers
    for name in callers:
        assert len(call.findall(texts[name])) == texts[name].count(ref), name


def test_tool_ref_scanner_finds_refs_and_ignores_dunders():
    assert tool_refs("call `agent-harness_coord__nope` then `agent-harness_slack__reply`") == {"agent-harness_coord__nope", "agent-harness_slack__reply"}
    assert tool_refs("def __init__(self): x.__dict__") == set()  # absence-ok: hit case on line above


def test_validators_accept_known_good_inputs():
    assert rust_parse_frontmatter(GOOD_AGENT)[0]["name"] == "demo"
    assert validate_agent(GOOD_AGENT, "demo") == []
    assert validate_check(GOOD_CHECK, "demo") == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda t: t.replace("name: demo\n", "name: demo\ncolor: red\n"), "unknown key 'color'"),
        (lambda t: t.replace("name: demo\n", "name: demo\nmodel: gpt-x\n"), "model is pinned"),
        (lambda t: t.replace("description: A demo persona.\n", ""), "missing key 'description'"),
        (lambda t: t.replace("name: demo", "name: other"), "!= file stem"),
        (lambda t: t.replace("A demo persona.", "x" * 161), "description over 160"),
        (lambda t: t.replace("agent-harness_coord__gen_hash", "agent-harness_coord__make_hash"), "'agent-harness_coord__make_hash' not in contract"),
        (lambda t: t.replace("NEEDS_CONTEXT", "NEEDS_INFO"), "missing status NEEDS_CONTEXT"),
        (lambda t: t.replace("DONE, ", ""), "missing status DONE"),
        (lambda t: t.replace("## Stop conditions\n", ""), "missing section '## Stop conditions'"),
        (lambda t: t + "## Appendix\n", "last section is not"),
        (lambda t: t.replace("Evidence rules", "Notes"), "missing evidence rules"),
    ],
)
def test_agent_validator_rejects(mutate, expected):
    errors = validate_agent(mutate(GOOD_AGENT), "demo")
    assert any(expected in e for e in errors), errors


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda t: t.replace("name: demo\n", "name: demo\nscope: all\n"), "unknown key 'scope'"),
        (lambda t: t.replace("severity-default: high", "severity-default: urgent"), "severity-default 'urgent' invalid"),
        (lambda t: t.replace("severity-default: high\n", ""), "missing key 'severity-default'"),
        (lambda t: t.replace("name: demo\n", "name: demo\ntools: [read]\n"), "tools breaks"),
        (lambda t: t.replace("name: demo\n", "name: demo\nturn-limit: 0\n"), "turn-limit must be"),
        (lambda t: t.replace("Flag things.\n", ""), "empty body"),
        (lambda t: "\n" + t.split("---", 2)[2], "must start with ---"),
        (lambda t: t.replace("Flag things.", "use agent-harness_slack__dm"), "'agent-harness_slack__dm' not in contract"),
    ],
)
def test_check_validator_rejects(mutate, expected):
    errors = validate_check(mutate(GOOD_CHECK), "demo")
    assert any(expected in e for e in errors), errors
