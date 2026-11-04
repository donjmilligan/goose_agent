"""A static mirror of goose's recipe schema, for validating harness recipes in CI.

goose itself does not reject unknown keys: none of the recipe structs carry
`#[serde(deny_unknown_fields)]`, so a typo such as `sub_recipe:` or
`retries:` deserializes cleanly and silently does nothing. This module is
stricter than goose on purpose. Every field set below cites the struct it
mirrors in crates/goose/src (as of upstream 71dd9a9aa) so drift is checkable.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# crates/goose/src/recipe/mod.rs:43-87 `struct Recipe`. No serde renames or
# aliases on any field. `version` has `#[serde(default = "default_version")]`
# (mod.rs:45, default "1.0.0" at mod.rs:28-30), so goose does not require it;
# the harness does, so a recipe states its own format version.
RECIPE_FIELDS = {
    "version",
    "title",
    "description",
    "instructions",
    "prompt",
    "extensions",
    "settings",
    "activities",
    "author",
    "parameters",
    "response",
    "sub_recipes",
    "retry",
}
RECIPE_REQUIRED = {"version", "title", "description"}

# mod.rs:90-96 `struct Author`
AUTHOR_FIELDS = {"contact", "metadata"}

# mod.rs:99-111 `struct Settings`
SETTINGS_FIELDS = {"goose_provider", "goose_model", "temperature", "max_turns"}

# mod.rs:114-117 `struct Response`
RESPONSE_FIELDS = {"json_schema"}

# mod.rs:120-130 `struct SubRecipe`; `values` is coerced to strings by
# `deserialize_value_map_as_string` (mod.rs:132-155).
SUB_RECIPE_FIELDS = {"name", "path", "values", "sequential_when_repeated", "description"}
SUB_RECIPE_REQUIRED = {"name", "path"}

# mod.rs:198-207 `struct RecipeParameter`
PARAMETER_FIELDS = {"key", "input_type", "requirement", "description", "default", "options"}
PARAMETER_REQUIRED = {"key", "input_type", "requirement", "description"}

# mod.rs:157-162 `enum RecipeParameterRequirement`, `rename_all = "snake_case"`
REQUIREMENTS = {"required", "optional", "user_prompt"}

# mod.rs:175-186 `enum RecipeParameterInputType`, `rename_all = "snake_case"`
INPUT_TYPES = {"string", "number", "boolean", "date", "file", "select"}

# validate_recipe.rs:15
MAX_PARAMETERS = 32

# agents/types.rs:11-25 `struct RetryConfig`
RETRY_FIELDS = {"max_retries", "checks", "on_failure", "timeout_seconds", "on_failure_timeout_seconds"}
RETRY_REQUIRED = {"max_retries", "checks"}

# agents/types.rs:53-61 `enum SuccessCheck`, `#[serde(tag = "type")]` with the
# single variant `Shell` carrying `#[serde(alias = "shell")]` (types.rs:57).
CHECK_FIELDS = {"type", "command"}
CHECK_TYPES = {"Shell", "shell"}

# recipe/recipe_extension_adapter.rs:6-77 `enum RecipeExtensionConfigInternal`,
# `#[serde(tag = "type")]`, variants renamed to these tags (lines 9, 28, 40, 52).
EXTENSION_FIELDS = {
    "stdio": {
        "type", "name", "description", "cmd", "args", "envs", "env_keys",
        "timeout", "cwd", "bundled", "available_tools",
    },
    "builtin": {"type", "name", "description", "display_name", "timeout", "bundled", "available_tools"},
    "platform": {"type", "name", "description", "display_name", "bundled", "available_tools"},
    "streamable_http": {
        "type", "name", "description", "uri", "envs", "env_keys", "headers", "timeout",
        "socket", "client_id", "client_secret_key", "scopes", "bundled", "available_tools",
    },
}
EXTENSION_REQUIRED = {
    "stdio": {"name", "cmd", "args"},
    "builtin": {"name"},
    "platform": {"name"},
    "streamable_http": {"name", "uri"},
}

# mod.rs:25 `BUILT_IN_RECIPE_DIR_PARAM`, injected by goose and exempt from the
# declared-vs-used parameter check (validate_recipe.rs:570-571).
BUILT_IN_PARAMS = {"recipe_dir"}

# The whole file is a minijinja template (template_recipe.rs). goose collects
# `{{ var }}` names via `undeclared_variables` (template_recipe.rs:214-229). The
# harness allows only a bare identifier with optional filters, so the static
# check here sees exactly the variables goose will.
TEMPLATE_EXPR_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
SIMPLE_VAR_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(\|\s*[A-Za-z_][A-Za-z0-9_]*(\([^{}]*\))?\s*)*$")
# `{% ... %}` blocks and `{# ... #}` comments would be interpreted by minijinja
# too, and are easy to type by accident in shell (`${#x}`) or prose.
JINJA_BLOCK_RE = re.compile(r"\{[%#]")

# The interface contract (plan, "Interface contract"): goose exposes MCP tools
# as `<extension key>__<tool>` (agents/extension_manager/mod.rs:363). The servers
# load through the `agent-harness` plugin, which names each one
# `agent-harness:<server>` (plugins/mcp_servers.rs:150); `name_to_key`
# (config/extensions.rs:22-32) turns that into `agent-harness_<server>`.
PLUGIN_KEY_PREFIX = "agent-harness_"
CONTRACT_TOOLS = {
    "coord": {
        "claim_signals", "worktree_create", "worktree_list", "gen_hash",
        "changed_paths", "verify_assertions",
    },
    "clickup": {
        "get_task", "list_tasks", "get_comments", "list_statuses", "create_task",
        "add_comment", "update_task", "set_status",
    },
    "slack": {"read_channel", "read_thread", "post_message", "reply", "find_claims"},
}
TOOL_REF_RE = re.compile(r"(?<![A-Za-z0-9_-])([a-z][a-z0-9_-]*?)__([a-z][a-z0-9_]*)\b")

# Persona agent files delegated to with `delegate(source: "<name>")`
# (agents/platform_extensions/summon.rs:46-58, 1096-1132).
PERSONAS = {
    "analyst", "pm", "architect", "po", "sm", "dev", "qa",
    "regression", "devsecops", "drift-identifier",
}
SOURCE_REF_RE = re.compile(r"\bsource:\s*\"([^\"]+)\"")

# A delegate's turn cap is `DelegateParams.max_turns`, else recipe
# settings.max_turns, else GOOSE_SUBAGENT_MAX_TURNS, else default_max_turns(),
# which is 1000 (summon.rs:46-58, 1433-1438; config/base.rs:1339-1341).
# `goose run --max-turns` caps only the parent, so every `delegate(source: ...)`
# names its cap, right after the source. `resume` accepts only `instructions`
# and is refused if it passes max_turns (summon.rs:1017-1039).
DELEGATE_CALL_RE = re.compile(r"\bdelegate\((\w+):")
DELEGATE_SOURCE_CALL_RE = re.compile(
    r"\bdelegate\(source:\s*\"([^\"]+)\"(?:,\s*max_turns:\s*(\d+)\b)?"
)
DELEGATE_RESUME_CALL_RE = re.compile(r"\bdelegate\(resume:[^)]*")


def recipe_body(document: Any) -> Any:
    """goose accepts a recipe nested under a top-level `recipe:` key (mod.rs:323-333, also validate_recipe.rs:388-389)."""
    if isinstance(document, dict) and "recipe" in document:
        return document["recipe"]
    return document


def _keys(errors: list[str], where: str, value: Any, allowed: set[str], required: set[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{where}: expected a mapping, got {type(value).__name__}")
        return False
    for key in sorted(set(value) - allowed):
        errors.append(f"{where}: unknown key {key!r}")
    for key in sorted(required - set(value)):
        errors.append(f"{where}: missing required key {key!r}")
    return True


def template_variables(text: str) -> tuple[set[str], list[str]]:
    names: set[str] = set()
    errors: list[str] = []
    for match in TEMPLATE_EXPR_RE.finditer(text):
        simple = SIMPLE_VAR_RE.match(match.group(1))
        if simple:
            names.add(simple.group(1))
        else:
            errors.append(f"template: unsupported expression {match.group(0)!r}")
    for match in JINJA_BLOCK_RE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        errors.append(f"template: minijinja block/comment syntax at line {line}")
    return names, errors


def _validate_parameters(errors: list[str], params: Any) -> set[str]:
    declared: set[str] = set()
    if not isinstance(params, list):
        errors.append("parameters: expected a list")
        return declared
    if len(params) > MAX_PARAMETERS:
        errors.append(f"parameters: {len(params)} exceeds the maximum of {MAX_PARAMETERS}")
    for index, param in enumerate(params):
        where = f"parameters[{index}]"
        if not _keys(errors, where, param, PARAMETER_FIELDS, PARAMETER_REQUIRED):
            continue
        key = param.get("key")
        if key in declared:
            # validate_recipe.rs:574-582
            errors.append(f"{where}: duplicate key {key!r}")
        declared.add(key)
        if param.get("input_type") not in INPUT_TYPES:
            errors.append(f"{where}: input_type {param.get('input_type')!r} not in {sorted(INPUT_TYPES)}")
        requirement = param.get("requirement")
        if requirement not in REQUIREMENTS:
            errors.append(f"{where}: requirement {requirement!r} not in {sorted(REQUIREMENTS)}")
        has_default = "default" in param
        if has_default and not isinstance(param["default"], str):
            # mod.rs:203 `default: Option<String>`; quote it so YAML does not
            # turn `no` or `5` into a bool or int first.
            errors.append(f"{where}: default must be a string")
        if requirement == "optional" and not has_default:
            # validate_recipe.rs:654-682
            errors.append(f"{where}: optional parameter needs a default")
        if requirement == "required" and has_default:
            # documentation/docs/guides/recipes/recipe-reference.md:342
            errors.append(f"{where}: required parameter must not have a default")
        if param.get("input_type") == "file" and has_default:
            # validate_recipe.rs:658-666
            errors.append(f"{where}: file parameter must not have a default")
        if param.get("input_type") == "select" and not param.get("options"):
            errors.append(f"{where}: select parameter needs options")
    return declared


def _validate_extensions(errors: list[str], extensions: Any) -> None:
    if not isinstance(extensions, list):
        errors.append("extensions: expected a list")
        return
    for index, ext in enumerate(extensions):
        where = f"extensions[{index}]"
        if not isinstance(ext, dict):
            errors.append(f"{where}: expected a mapping")
            continue
        kind = ext.get("type")
        if kind not in EXTENSION_FIELDS:
            errors.append(f"{where}: type {kind!r} not in {sorted(EXTENSION_FIELDS)}")
            continue
        _keys(errors, where, ext, EXTENSION_FIELDS[kind], EXTENSION_REQUIRED[kind] | {"type"})


def _validate_retry(errors: list[str], retry: Any) -> None:
    if not _keys(errors, "retry", retry, RETRY_FIELDS, RETRY_REQUIRED):
        return
    max_retries = retry.get("max_retries")
    if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 1:
        # agents/types.rs:31-34
        errors.append("retry.max_retries: must be an integer greater than 0")
    for field in ("timeout_seconds", "on_failure_timeout_seconds"):
        if field in retry and (not isinstance(retry[field], int) or retry[field] < 1):
            # agents/types.rs:36-48
            errors.append(f"retry.{field}: must be an integer greater than 0")
    checks = retry.get("checks")
    if not isinstance(checks, list) or not checks:
        errors.append("retry.checks: expected a non-empty list")
        return
    for index, check in enumerate(checks):
        where = f"retry.checks[{index}]"
        if not _keys(errors, where, check, CHECK_FIELDS, CHECK_FIELDS):
            continue
        if check.get("type") not in CHECK_TYPES:
            errors.append(f"{where}: type {check.get('type')!r} not in {sorted(CHECK_TYPES)}")
        if not isinstance(check.get("command"), str) or not check["command"].strip():
            errors.append(f"{where}: command must be a non-empty string")


def _validate_json_schema(errors: list[str], schema: Any, where: str = "response.json_schema") -> None:
    # validate_recipe.rs:483-493 requires a non-empty object that compiles as
    # a JSON schema. The harness also requires each `required` name to be a
    # declared property, the mistake a compiler does not catch.
    if not isinstance(schema, dict) or not schema:
        errors.append(f"{where}: must be a non-empty mapping")
        return
    properties = schema.get("properties", {})
    if schema.get("type") == "object":
        for name in schema.get("required", []):
            if name not in properties:
                errors.append(f"{where}: required {name!r} is not a declared property")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        errors.append(f"{where}.enum: must be a non-empty list")
    for name, sub in properties.items():
        _validate_json_schema(errors, sub, f"{where}.properties.{name}")
    if isinstance(schema.get("items"), dict):
        _validate_json_schema(errors, schema["items"], f"{where}.items")


def validate_recipe(document: Any, raw_text: str, recipe_dir: Path) -> list[str]:
    """Return every schema, template and reference error in one recipe."""
    errors: list[str] = []
    recipe = recipe_body(document)
    if not _keys(errors, "recipe", recipe, RECIPE_FIELDS, RECIPE_REQUIRED):
        return errors

    if not any(isinstance(recipe.get(f), str) and recipe[f].strip() for f in ("instructions", "prompt")):
        # validate_recipe.rs:545-563
        errors.append("recipe: needs a non-empty instructions or prompt")

    if "author" in recipe:
        _keys(errors, "author", recipe["author"], AUTHOR_FIELDS, set())
    if "settings" in recipe:
        _keys(errors, "settings", recipe["settings"], SETTINGS_FIELDS, set())
    if "activities" in recipe and not all(isinstance(a, str) for a in recipe["activities"] or []):
        errors.append("activities: expected a list of strings")
    if "extensions" in recipe:
        _validate_extensions(errors, recipe["extensions"])
    if "response" in recipe and _keys(errors, "response", recipe["response"], RESPONSE_FIELDS, set()):
        if "json_schema" in recipe["response"]:
            _validate_json_schema(errors, recipe["response"]["json_schema"])
    if "retry" in recipe:
        _validate_retry(errors, recipe["retry"])

    if "sub_recipes" in recipe:
        sub_recipes = recipe["sub_recipes"]
        if not isinstance(sub_recipes, list):
            errors.append("sub_recipes: expected a list")
            sub_recipes = []
        for index, sub in enumerate(sub_recipes):
            where = f"sub_recipes[{index}]"
            if not _keys(errors, where, sub, SUB_RECIPE_FIELDS, SUB_RECIPE_REQUIRED):
                continue
            # Relative paths resolve against the parent recipe's directory
            # (recipe/build_recipe/mod.rs:154-170).
            path = Path(str(sub["path"]).replace("{{recipe_dir}}", str(recipe_dir)))
            if not path.is_absolute():
                path = recipe_dir / path
            if not path.is_file():
                errors.append(f"{where}: path {sub['path']!r} does not exist")
        if "extensions" not in recipe:
            # mod.rs:255-271: with sub_recipes and no extensions, goose sets
            # extensions to [summon] alone, silently dropping developer and
            # every plugin extension for the session and its delegates.
            errors.append("sub_recipes: declare extensions explicitly, or the session gets only summon")

    declared = _validate_parameters(errors, recipe.get("parameters", [])) if "parameters" in recipe else set()
    used, template_errors = template_variables(raw_text)
    errors.extend(template_errors)
    used -= BUILT_IN_PARAMS
    # validate_recipe.rs:566-617: both directions are errors in goose.
    for name in sorted(used - declared):
        errors.append(f"parameters: {{{{{name}}}}} is used but not declared")
    for name in sorted(declared - used):
        errors.append(f"parameters: {name!r} is declared but never used")

    errors.extend(tool_reference_errors(raw_text))
    errors.extend(source_reference_errors(raw_text))
    errors.extend(delegate_turn_cap_errors(raw_text))
    return errors


def tool_references(text: str) -> set[str]:
    return {f"{ext}__{tool}" for ext, tool in TOOL_REF_RE.findall(text)}


def tool_reference_errors(text: str) -> list[str]:
    errors = []
    for ref in sorted(tool_references(text)):
        key, tool = ref.split("__", 1)
        ext = key.removeprefix(PLUGIN_KEY_PREFIX)
        if key == ext or tool not in CONTRACT_TOOLS.get(ext, set()):
            errors.append(f"tools: {ref} is not in the MCP interface contract")
    return errors


def source_references(text: str) -> set[str]:
    return set(SOURCE_REF_RE.findall(text))


def source_reference_errors(text: str) -> list[str]:
    return [
        f"delegate: source {name!r} is not one of the personas"
        for name in sorted(source_references(text) - PERSONAS)
    ]


def delegate_max_turns(text: str) -> list[tuple[str, int | None]]:
    """(source, max_turns) for every `delegate(source: ...)` call, in order."""
    return [
        (source, int(cap) if cap else None)
        for source, cap in DELEGATE_SOURCE_CALL_RE.findall(text)
    ]


def delegate_turn_cap_errors(text: str) -> list[str]:
    errors = [
        f"delegate: call opens with {first!r}; put source: or resume: first"
        for first in DELEGATE_CALL_RE.findall(text)
        if first not in ("source", "resume")
    ]
    for source, cap in delegate_max_turns(text):
        if cap is None:
            errors.append(
                f"delegate: source {source!r} passes no max_turns after the source, "
                "so it runs up to goose's default of 1000 turns"
            )
        elif cap < 1:
            errors.append(f"delegate: source {source!r} max_turns must be at least 1")
    for call in DELEGATE_RESUME_CALL_RE.findall(text):
        if "max_turns" in call:
            errors.append("delegate: resume accepts only instructions; remove max_turns")
    return errors
