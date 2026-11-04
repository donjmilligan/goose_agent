# Agent harness architecture: a `harness/` tree on goose's own extension points

**Date:** 2026-11-04
**Status:** ACCEPTED
**Scope:** new top-level `harness/` tree. No changes under `crates/`, `ui/` or any other upstream path.
**Author:** Claude + Don Milligan (approved plan `shiny-puzzling-cupcake`, fan-out `fo-ggjl`)

## Context

`goose_agent` is an unmodified fork of goose at `71dd9a9aa`. It should become
a harness for multi-agent development work, carrying over what the vanta-lms
Claude configuration learned: evidence discipline, claim-before-work, one
worktree per session, the completion protocol, and ClickUp/Slack
coordination. It should also adopt the BMAD persona chain (Analyst → PM →
Architect → PO → SM → Dev → QA).

goose already ships most of the mechanism. All of the following was verified
in source at `71dd9a9aa`:

- **Persona files.** `summon` reads them from `~/.agents/agents`,
  `.agents/agents` and `.claude/agents`
  (`crates/goose/src/agents/platform_extensions/summon.rs:252-292`).
- **Recipes.** They support `sub_recipes`, `retry.checks` and
  `response.json_schema`, and are discovered in `~/.agents/recipes`
  (`crates/goose/src/recipe/local_recipes.rs:22-47`).
- **Plugins.** A plugin can provide hooks, `.mcp.json` and skills. User
  plugins live in `~/.agents/plugins/<name>`
  (`crates/goose/src/plugins/mod.rs:35-37`, `config/paths.rs:15,33`).
- **Review checks.** `goose review` runs them
  (`crates/goose/src/checks/mod.rs:297-304`).
- **Global hints.** These are loaded into every session's system prompt
  (`crates/goose/src/hints/load_hints.rs:232-270`,
  `agents/prompt_manager.rs:90-100,235`).

What goose lacks:

- worktree isolation for delegates
- Slack and ClickUp integration
- a claim protocol
- the working agreement
- BMAD personas and recipes

There is no `cargo` or `goose` binary on this machine's PATH, so Rust
changes could not be gated here.

## Options Considered

### Option A: a self-contained `harness/` tree, zero Rust, Python MCP servers
- Pro: the fork still rebases on upstream with no conflicts, because nothing
  under `crates/` changes.
- Pro: every piece plugs into a documented goose discovery path, so goose
  sees the harness the same way it sees any user's config.
- Pro: the MCP servers are small Python stdio projects (FastMCP, `uv`) that
  can be tested without network or tokens. Each server's tests run on their
  own.
- Con: it cannot add worktree isolation to `summon` itself, so isolation is a
  protocol plus a `agent-harness_coord__worktree_create` tool, not an enforced property.
- Con: goose refuses per-file symlinks for agents, recipes, checks and hints
  (see Decision), so those are installed as copies and have to be
  re-installed after an edit.

### Option B: Rust MCP servers in `crates/goose-mcp`
- Pro: they would ship with goose as built-in extensions, with no separate
  runtime.
- Con: every upstream rebase touches the workspace.
- Con: they cannot be built or gated on this machine today.
- Con: `AGENTS.md` sends third-party servers toward the MCP Registry, not
  into the goose tree.

### Option C: change `summon` to create a worktree per delegate
- Pro: this is the only option that makes isolation structural.
- Con: it is a Rust change to a core platform extension, it needs cargo, and
  it is upstream-design territory (`AGENTS.md` contribution workflow).
  Deferred to wave 2.

## Decision

**Option A.** The harness is a `harness/` tree:

- `clients/clickup`, `clients/slack` and `coord`: Python MCP servers. The
  extension names are `clickup`, `slack` and `coord`.
- `plugin/agent-harness`: hooks plus `.mcp.json.template`.
- `agents`: BMAD and specialist personas.
- `checks`: `goose review` checks.
- `recipes`: the BMAD flow, fan-out, day cycle and QA.
- `WORKING_AGREEMENT.md`, `install.sh`, this record, and tests.

**How `install.sh` gets each piece into goose was decided by reading the
loader, not assumed:**

- **Agents, recipes and checks are copies, not symlinks.**
  - `summon` and recipe loading read files through
    `skills::read_source_file`. That function opens the final path component
    with `O_NOFOLLOW` (`crates/goose/src/skills/supporting_files.rs:303-307`).
  - `goose review` also skips anything whose `file_type()` is not a regular
    file (`checks/mod.rs:431`).
  - So a per-file symlink is skipped with a warning. Each copy is recorded in
    a manifest with its checksum, so the installer can update or remove
    exactly what it wrote and nothing else.
  - **Rejected alternative: symlink the whole directory.** Both loaders
    canonicalize the scan root first (`summon.rs:214`,
    `local_recipes.rs:40-44`, `checks/mod.rs:351`), so a link to the entire
    `~/.agents/agents` directory *would* be read.
  - It only works when that directory does not exist yet, though.
    `~/.agents/agents` and `~/.agents/recipes` are shared with every other
    tool that uses the `.agents` convention. A user who already has one
    would get a skipped install.
  - A link to a subdirectory inside the directory doesn't help either,
    because `summon` scans one level only (`read_dir`, `*.md`,
    `summon.rs:217-230`).
  - Copies work in both cases and coexist with the user's own files.
- **Checks go to `$HOME/.config/goose/checks`.** goose does not read
  `~/.agents/checks`. The global check dirs are hard-coded as
  `$HOME/.config/goose/checks` and `$HOME/.config/agents/checks`
  (`checks/mod.rs:297-304`). The positive control: the same function does
  list the two `.config` paths.
- **The plugin is a symlink.**
  - Discovery uses `Path::is_dir()`, which follows links
    (`plugins/discovery.rs:185-200`).
  - `.mcp.json` and `hooks/hooks.json` are read with plain `fs` reads
    (`plugins/mcp_servers.rs:53-57`, `hooks/mod.rs:438`).
  - The rendered `.mcp.json` is written beside the template in the harness
    tree and gitignored.
  - Plugins are enabled by default (`plugins/discovery.rs:154-178`), so no
    `settings.json` edit is needed.
- **The working agreement loads through the global `.goosehints`.** It uses
  an include, not a symlink.
  - Global hints are `Paths::in_config_dir(name)` for each
    `CONTEXT_FILE_NAMES` entry (default `.goosehints` and `AGENTS.md`), plus
    `~/.agents/AGENTS.md` (`load_hints.rs:13-24,232-249`).
  - Their include boundary is the config dir itself (`load_hints.rs:251-262`).
  - `read_referenced_files` canonicalizes the hint file *and* every include,
    and rejects anything whose real path is outside that boundary. Absolute
    include paths are rejected outright (`hints/import_files.rs:202-288,
    492-510`).
  - So neither a symlink to `harness/WORKING_AGREEMENT.md` nor an include of
    its absolute path would load. The installer copies the agreement to
    `<config>/agent-harness-working-agreement.md` and appends a marked block
    to `<config>/.goosehints`. The block contains a single line,
    `@agent-harness-working-agreement.md`.
  - We chose `.goosehints` over `AGENTS.md` because `.goosehints` is
    goose-specific. A user's global `AGENTS.md` is more likely to be shared
    with other tools.
  - The block is idempotent. `--uninstall` removes only the block, and
    deletes the file only if the installer created it.
- **The MCP servers get their secrets from the environment.**
  - Plugin servers are registered with `env_keys: []`
    (`plugins/mcp_servers.rs:149-163`), and stdio children inherit goose's
    environment.
  - If `env_keys` listed an optional variable, extension load would fail
    when that variable is unset (`agents/extension.rs:467-492`).
  - So tokens come from the shell environment or from `HARNESS_ENV_FILE`,
    never from `env_keys`.

## Consequences

- The fork's diff against upstream stays confined to `harness/` and the root
  `.goosehints`.
- After editing a persona, recipe, check or the agreement in the harness
  tree, you have to re-run `harness/install.sh` to propagate it. The plugin
  (hooks and servers) is live.
- **The plugin changes the MCP tool names.** goose names a plugin's MCP
  server `<plugin>:<server>` (`plugins/mcp_servers.rs:150`), and its tool
  prefix is `name_to_key` of that name (`config/extensions.rs:22-32`,
  `agents/extension_manager/mod.rs:363`).
  - Loaded via the plugin, the tools are therefore
    `agent-harness_clickup__set_status`, not the `clickup__set_status` the
    original interface contract named.
  - **Resolved at integration, same day:** the plugin stays the single
    registration path (no edits to goose config), and every persona and
    recipe uses the prefixed names. Rejected: top-level extensions via
    `goose configure`, because it is interactive and editing `config.yaml`
    from a shell script risks a user's existing config. Both validators fail
    on a bare `coord__*` / `clickup__*` / `slack__*` reference.
- An agent still *can* skip the worktree step, because isolation is a
  protocol. The `PreToolUse` guardrail hook narrows what it can do in the
  shared checkout, but it does not make isolation structural.
- If a user sets `CONTEXT_FILE_NAMES` without `.goosehints`, the agreement
  silently stops loading. `install.sh` warns when it sees this in
  `config.yaml`.

## Implementation Notes

- `harness/install.sh` takes `--dry-run`, `--uninstall` and
  `--harness-root`. Its manifest is
  `${XDG_STATE_HOME:-~/.local/state}/agent-harness/install-manifest.tsv`.
- It resolves dirs the way goose does:
  - `GOOSE_PATH_ROOT`
  - `XDG_CONFIG_HOME`, used on macOS too, because goose uses etcetera's
    `choose_app_strategy`, which is Xdg on macOS
  - otherwise `$HOME`
- `harness/tests/test_install.py` runs `install.sh` against a temporary HOME.

**Wave 2.** Each of these needs Rust and a working cargo:

1. a `worktree:` parameter on `summon delegate`, so isolation becomes
   structural
2. nested delegation
3. a claim-heartbeat `Operation` through `StateMachineConfig.extra_operations`
4. `CLAUDE.md` in the default `CONTEXT_FILE_NAMES`
5. optionally, a goose option to register plugin servers without the
   `<plugin>:` prefix, which would allow the shorter tool names
6. porting the Figma handler, which is a large export pipeline and low value
   as MCP today
