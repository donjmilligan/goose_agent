# agent-harness

This is a multi-agent development harness for goose. It is built entirely on
goose's own extension points: it contains **no Rust**, and nothing under
`crates/` changes, so this fork still rebases cleanly on upstream.

The harness adds what goose lacks for parallel agent work:

- Slack and ClickUp tools
- a claim protocol and per-session worktrees
- guardrail hooks
- the BMAD persona chain and recipes
- a working agreement that loads into every session

The architecture, and why it looks like this, is recorded in
[`decisions/2026-11-04-s0id-harness-architecture.md`](decisions/2026-11-04-s0id-harness-architecture.md).

## Layout

```
harness/
├── WORKING_AGREEMENT.md      # loaded into every goose session (see "Hints" below)
├── install.sh                # links/copies everything into goose's discovery dirs
├── decisions/                # append-only decision records
├── tests/                    # install.sh tests (temp HOME)
├── clients/clickup/          # uv project → MCP server `clickup`   (harness-clickup-mcp)
├── clients/slack/            # uv project → MCP server `slack`     (harness-slack-mcp)
├── coord/                    # uv project → MCP server `coord` + `harness-coord` CLI
├── plugin/agent-harness/     # goose plugin: hooks/hooks.json, .mcp.json.template
├── agents/                   # personas: analyst pm architect po sm dev qa regression devsecops drift-identifier
├── checks/                   # `goose review` checks
└── recipes/                  # bmad-plan bmad-shard bmad-story fan-out start-day end-day qa-check
```

Each subtree has its own owner. `install.sh` skips any subtree that is not
present and says so.

## Goose extension points used

All of these were read from source at `71dd9a9aa`:

| What | Where goose looks | Source | How `install.sh` installs it |
|---|---|---|---|
| Personas (`summon` `delegate source:`) | `~/.agents/agents`, `<config>/agents`, `~/.claude/agents`, and the project `.agents/agents` | `crates/goose/src/agents/platform_extensions/summon.rs:252-292` | **copy** each `*.md` that has frontmatter |
| Recipes (`goose run --recipe <name>`) | `.`, `GOOSE_RECIPE_PATH`, `<config>/recipes`, `.goose/recipes`, `.agents/recipes`, `~/.agents/recipes` | `crates/goose/src/recipe/local_recipes.rs:22-47` | **copy** `*.yaml`/`*.yml`/`*.json` (tree preserved, `tests/` excluded) to `~/.agents/recipes` |
| Review checks (`goose review`) | `$HOME/.config/goose/checks`, `$HOME/.config/agents/checks`, and the project `.agents/checks`. It does **not** read `~/.agents/checks` | `crates/goose/src/checks/mod.rs:297-304` | **copy** `*.md` (not README.md) to `$HOME/.config/goose/checks` |
| Plugin: hooks, MCP servers, skills | `Paths::plugins_dir()` = `~/.agents/plugins/<name>`, or `$GOOSE_PATH_ROOT/.agents/plugins` | `crates/goose/src/plugins/mod.rs:35-37`, `config/paths.rs:8-38`, `plugins/discovery.rs:56-100` | **symlink** `~/.agents/plugins/agent-harness` → `harness/plugin/agent-harness`, and render `.mcp.json` |
| Global hints | `<config>/<name>` for each `CONTEXT_FILE_NAMES` entry (default `.goosehints`, `AGENTS.md`), plus `~/.agents/AGENTS.md` | `crates/goose/src/hints/load_hints.rs:13-24,232-270` | **copy** the agreement into `<config>`, plus a marked include block in `<config>/.goosehints` |

`<config>` is `$GOOSE_PATH_ROOT/config` if that is set. Otherwise it is
`$XDG_CONFIG_HOME/goose`, and if that is unset too, `~/.config/goose`. This
holds on macOS as well, because goose uses etcetera's `choose_app_strategy`,
which picks Xdg on macOS (`config/paths.rs:22-27`).

### Why copies instead of symlinks

- goose reads agent and recipe files through `skills::read_source_file`.
  That function opens the final path component with `O_NOFOLLOW`
  (`crates/goose/src/skills/supporting_files.rs:303-307`), so a symlinked
  persona or recipe is skipped with a warning.
- `goose review` skips any entry whose `file_type()` is not a regular file
  (`checks/mod.rs:431`).
- Linking the whole directory would work, but only when the user has no
  `~/.agents/agents` yet. That directory is shared with other tools.

**So after editing a persona, recipe, check or the agreement, re-run
`harness/install.sh`.** It updates every copy that is still unmodified. The
plugin is a real symlink, so hooks and MCP servers are always live.

### Hints: how the working agreement reaches every session

goose builds every session's system prompt with `with_hints(working_dir)`
(`agents/prompt_manager.rs:90-100,235`), and that call loads the global
hints. An `@file` include in a global hints file is resolved inside an
**import boundary equal to the config dir** (`load_hints.rs:251-262`):

- The hint file itself, and every include, is canonicalized. Anything whose
  real path is outside the boundary is refused.
- Absolute include paths are refused too (`hints/import_files.rs:202-288`,
  `492-510`).

That means a symlink into `harness/`, or an include of an absolute harness
path, would be silently dropped. The installer does two things instead:

1. It copies `WORKING_AGREEMENT.md` to
   `<config>/agent-harness-working-agreement.md`.
2. It appends this block to `<config>/.goosehints`, creating the file if
   needed:

   ```
   # >>> agent-harness (managed by harness/install.sh; remove with --uninstall) >>>
   @agent-harness-working-agreement.md
   # <<< agent-harness <<<
   ```

The block goes in `.goosehints` rather than `AGENTS.md` because
`.goosehints` is goose-specific, while a global `AGENTS.md` is often shared
with other tools.

If you set `CONTEXT_FILE_NAMES` in `config.yaml` without `.goosehints`, the
agreement stops loading. `install.sh` warns when it sees that.

## Quickstart

Prerequisites: `goose` on your PATH, plus [`uv`](https://docs.astral.sh/uv/).
Each MCP server is run with `uv run --project <dir> harness-<name>-mcp`.

```bash
# 1. Preview, then install. Safe to repeat; never overwrites a file it did not create.
harness/install.sh --dry-run
harness/install.sh
```

**2. Set tokens.** The MCP servers are stdio children of goose, so they
inherit goose's environment. Choose one of these:

- Export the variables in the shell that launches goose.
- Point `HARNESS_ENV_FILE` at a `KEY=VALUE` file. The clients read it at
  startup.

Do **not** list the optional variables under an extension's `env_keys`. goose
fails to load the extension when a listed key can't be resolved
(`agents/extension.rs:467-492`). The plugin's servers are registered with no
`env_keys` (`plugins/mcp_servers.rs:149-163`).

| Variable | Used by | Needed for |
|---|---|---|
| `CLICKUP_API_TOKEN` | clickup | any ClickUp call |
| `SLACK_BOT_TOKEN` | slack, the SessionStart hook | reading and posting in Slack |
| `HARNESS_SLACK_ALLOWED_CHANNELS` | slack | posting at all; only channels listed here accept posts |
| `HARNESS_CLAIM_CHANNEL` | the SessionStart hook, fan-out | the claim-channel signal; the hook says so if it is unset |
| `HARNESS_ENV_FILE` | all clients | optional file to read the above from |
| `HARNESS_ALLOW_DONE` | clickup `set_status` | `=1` lets an agent set done/closed; leave it unset |

**3. Enable the plugin.** Nothing to do: discovered plugins are enabled by
default (`plugins/discovery.rs:123-178`). On first discovery, goose adds an
`enabled: true` entry under `plugins:` in `config.yaml`. You can disable the
plugin in either of these ways:

- set that entry to `enabled: false`
- add `"disabledPlugins": ["agent-harness"]` to
  `~/.config/goose/settings.json`

```bash
# 4. First run, from the project you want to plan
cd ~/Code/my-project
goose run --recipe bmad-plan --explain      # shows the recipe's parameters
goose run --recipe bmad-plan --params <name>=<value>
```

**To uninstall:** `harness/install.sh --uninstall`. It removes only what the
manifest says this installer wrote, and only if the artifact is unmodified.
That covers the copies, the plugin link, the rendered `.mcp.json`, the hints
block, and any directories it created that are now empty. A copy you have
edited is kept, with a warning. The manifest lives at
`${XDG_STATE_HOME:-~/.local/state}/agent-harness/install-manifest.tsv`.

For testing, `install.sh` respects `HOME`, `XDG_CONFIG_HOME`,
`XDG_STATE_HOME` and `GOOSE_PATH_ROOT`. Its tests run it against a temporary
HOME: `uv run --with pytest pytest harness/tests -q`.

### Tool names: plugin vs top-level extension

The interface contract names the tools `clickup__*`, `slack__*` and
`coord__*`. That is what goose produces for top-level extensions named
`clickup`, `slack` and `coord`.

Servers loaded through a plugin are named `<plugin>:<server>`
(`plugins/mcp_servers.rs:150`), and the tool prefix is that name passed
through `name_to_key` (`config/extensions.rs:22-32`,
`agents/extension_manager/mod.rs:363`). With the plugin, the tools are
therefore `agent-harness_clickup__set_status` and so on.

If recipes need the bare names, register the three servers as top-level
extensions with `goose configure` (the command is
`uv run --project <harness>/clients/clickup harness-clickup-mcp`, and so on),
and leave the plugin to hooks only. This is an open decision; see the
decision record.

## The BMAD flow

| Step | Recipe | Personas | Output |
|---|---|---|---|
| Plan | `bmad-plan` | analyst → pm → architect | `docs/prd.md`, `docs/architecture.md` |
| Shard | `bmad-shard` | po | `docs/epics/`, `docs/stories/` |
| Build one story | `bmad-story` | sm (context-complete story) → dev (in a `coord__worktree_create` worktree) → qa | branch pushed. `retry.checks` runs the project gate and `harness-coord verify-assertions` |
| Many stories | `fan-out` | one delegate per story, rolling pool of 5 or fewer (goose's `GOOSE_MAX_CONCURRENT_SUBAGENTS`) | branches pushed. Claims are posted only when a claim channel is configured. No merges, no PRs |
| Day cycle | `start-day`, `end-day` | — | inventory and proposals. Nothing is merged |
| Review | `qa-check`, `goose review` | qa, checks | findings |

The specialists `regression`, `devsecops` and `drift-identifier` are called
on demand through `summon`; they are never auto-chained. Every persona ends
with the completion protocol: DONE, DONE_WITH_CONCERNS, BLOCKED or
NEEDS_CONTEXT.

## Lessons ported from vanta-lms

- **Latent vs deterministic.** If it is same-input-same-output, write the
  script. Hashes, claim signals and worktrees are tools, not prompts.
- **The context window is the lever.** Curate it before you blame the model.
- **A green test is not evidence until you have seen it go red.** Run the
  mutation check, and say which of the three outcomes you got.
- **Vacuous assertions come in three shapes.** They are: an absence, the half
  that cannot vary, and a value computed from the other side. The
  `vacuous-assertions` check and `harness-coord verify-assertions` look for
  them.
- **An asserted absence needs a positive control** in the same breath.
- **A framework number carries its population.** The `unsourced-numbers`
  check looks for numbers that don't.
- **Claim before work, using three signals:** the claim channel, remote
  branches and worktrees. Push early so your branch is visible.
- **One worktree per session, outside the repo.** `coord__worktree_create`
  refuses nested paths.
- **Comment before every status change.** `clickup__set_status` posts the
  comment and then flips, so a silent flip can't happen.
- **An agent's ceiling is review.** `set_status` refuses done/closed.
- **No PRs unless asked, and no merges.** The `PreToolUse` hook blocks
  `--no-verify`, force-push, pushes to main, `gh pr merge --admin` and
  committing on the default branch.
- **Decision records** are named `YYYY-MM-DD-<hash>-<slug>.md`, use a
  generated hash, are append-only, and say so when they reverse an earlier
  record.
- **Fixed vanta hook bugs:** a matcher written in permission syntax never
  fired, exit 1 never blocked (goose blocks on exit 2,
  `hooks/mod.rs:897-947`), and a gate was left unwired.

## Not in this wave

These need Rust and a working cargo, which this machine doesn't have:

- a `worktree:` parameter on `summon delegate`
- nested delegation
- a claim-heartbeat operation
- `CLAUDE.md` in the default `CONTEXT_FILE_NAMES`
- registering plugin MCP servers without the `<plugin>:` prefix

See the decision record.
