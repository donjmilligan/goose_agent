# Harness recipes

These are goose recipes for the BMAD flow and the daily working loop. They
are plain goose recipe YAML and use no Rust changes. They call:

- the persona agent files in `harness/agents/`, through
  `delegate(source: "<persona>")`;
- the `coord`, `clickup` and `slack` MCP extensions, which the
  `agent-harness` plugin provides.

No recipe declares `extensions`, so each session uses your enabled extensions.
That includes the default `developer` and `summon` and the plugin's three
servers.

| Recipe | What it does | Writes |
|---|---|---|
| `bmad-plan` | Runs analyst → pm → architect to produce `docs/brief.md`, `docs/prd.md` and `docs/architecture.md`, then stops for your review | Those three docs only |
| `bmad-shard` | The po shards the PRD and architecture into `docs/epics/` and `docs/stories/`, then the requirement → story trace is checked | Epic and story files. ClickUp tasks only after you approve |
| `bmad-story` | Builds one story: sm finalises it, dev builds it in a coord worktree, qa reviews it. The result follows the completion protocol | Its own worktree and branch, pushed, with no PR |
| `fan-out` | Plans a batch and shows the table, then waits for approval. Then it claims, runs one dev delegate per item in its own worktree, and verifies each report | Worktrees and branches. Slack claims if `claim_channel` is set. ClickUp comment, then status (ceiling: review) |
| `start-day` | Orients you, reads claims, reconciles the board and recommends **one** task | Nothing |
| `end-day` | Drains findings to ClickUp (with your approval), then inventories and classifies your branches and proposes cleanup | Approved ClickUp comments and tasks, and deletions you confirm one at a time. Never merges |
| `qa-check` | Runs `agent-harness_coord__changed_paths`, then the gate, then verify-assertions on the changed tests. It offers the slow lane but never runs it unasked | Nothing, unless you approve the slow lane |

## Parameters

Pass parameters with `--params key=value`, repeated once per parameter. Path
parameters are absolute and have no trailing slash, because worktrees are
derived as `<project_dir>.wt/<slug>`.

| Recipe | Required | Optional (default) |
|---|---|---|
| `bmad-plan` | `idea`, `project_dir` | none |
| `bmad-shard` | `project_dir` | `prd_path` (`docs/prd.md`), `architecture_path` (`docs/architecture.md`), `clickup_list` (`none`) |
| `bmad-story` | `story_path`, `gate_command`, `project_dir`, `slug`, `branch` | `base` (`origin/main`), `harness_coord` (`harness-coord`), `task_id` (`none`) |
| `fan-out` | `scope`, `project_dir`, `gate_command` | `base` (`origin/main`), `max_agents` (`5`), `claim_channel` (`none`) |
| `start-day` | `project_dir` | `claim_channel` (`none`), `clickup_list` (`none`), `focus` (`any`) |
| `end-day` | `project_dir` | `clickup_list` (`none`), `stale_days` (`14`) |
| `qa-check` | `project_dir`, `gate_command` | `slow_lane_command` (`none`), `base` (`origin/main`) |

Setting `none` on `claim_channel`, `clickup_list` or `task_id` turns that
integration off. The recipe then says that it skipped it, rather than
staying silent. A `claim_channel` also has to be listed in
`HARNESS_SLACK_ALLOWED_CHANNELS`, or the Slack server refuses to post.

## Examples

Run the recipes that pause for you with `--interactive`. Those are
`bmad-plan`, `bmad-shard`, `fan-out`, `end-day` and `qa-check`. Run
`fan-out` and `bmad-story` from the directory that holds both the repo and
its `.wt/` sibling (see the limits below).

```bash
R=~/Code/goose_agent/harness/recipes

goose run --interactive --recipe $R/bmad-plan.yaml \
  --params idea="A CLI that syncs ClickUp tasks to local markdown" \
  --params project_dir=$HOME/Code/myproj

goose run --interactive --recipe $R/bmad-shard.yaml \
  --params project_dir=$HOME/Code/myproj --params clickup_list=901234567

cd ~/Code && goose run --recipe $R/bmad-story.yaml \
  --params story_path=docs/stories/1.1-sync-command.md \
  --params gate_command="uv run pytest -q && uv run ruff check ." \
  --params project_dir=$HOME/Code/myproj \
  --params slug=sync-command --params branch=feature/sync-command \
  --params harness_coord="uv run --project $HOME/Code/goose_agent/harness/coord harness-coord"

cd ~/Code && goose run --interactive --recipe $R/fan-out.yaml \
  --params scope="docs/stories/1.2-*.md" \
  --params project_dir=$HOME/Code/myproj \
  --params gate_command="uv run pytest -q" \
  --params claim_channel=C0123456789

goose run --recipe $R/start-day.yaml --params project_dir=$HOME/Code/myproj
goose run --interactive --recipe $R/end-day.yaml --params project_dir=$HOME/Code/myproj
goose run --interactive --recipe $R/qa-check.yaml \
  --params project_dir=$HOME/Code/myproj.wt/sync-command \
  --params gate_command="uv run pytest -q"
```

Once the recipes are installed into `~/.agents/recipes`, `--recipe` also
takes the bare name, such as `--recipe fan-out`. To check a recipe without
running it, use `--explain` (to see its parameters) or `--render-recipe` (to
see it after templating).

## Tests

```bash
uv run --with pytest --with pyyaml pytest harness/recipes -q
```

`tests/recipe_schema.py` mirrors goose's recipe structs field by field. Each
field set cites its source line in `crates/goose/src`. It is stricter than
goose on purpose: goose has no `deny_unknown_fields`, so a mistyped key
(`sub_recipe:`, `retries:`) loads cleanly and silently does nothing.

Every recipe is checked for:

- unknown and missing keys, at every level;
- parameters: each `{{x}}` is declared, and each declared key is used;
- no minijinja `{% %}` or `{# #}` syntax. goose templates the whole file,
  so a `${#x}` in shell would break it;
- every `<ext>__<tool>` names a tool in the MCP interface contract;
- every `delegate(source: ...)` names one of the ten personas;
- every `sub_recipes` path exists.

Four further tests compare the contract with the real tool lists and agent
files once the sibling slices are in the same tree. They skip until then.

### Against a real goose binary

```bash
harness/recipes/tests/goose_validate.sh path/to/goose   # or set GOOSE_BIN
```

`tests/test_goose_live.py` skips unless `GOOSE_BIN` is set. It needs no model
and no tokens, and it runs goose in a temporary `GOOSE_PATH_ROOT` and `HOME`.
It checks:

- `goose recipe validate`, `--explain` and `--render-recipe` on every recipe,
  including that goose refuses to render without the required parameters;
- `goose mcp-probe` on the coord, clickup and slack servers. Each must expose
  exactly its contract tools. Every clickup and slack tool must return a clean
  tool error naming the missing token. coord `gen_hash` and `claim_signals`
  must work through goose's client, and a coord refusal (invalid slug, empty
  `paths`) must reach goose as `isError: true` with its error code in the text;
- after `harness/install.sh` into the sandbox: `goose review --dry-run` finds
  the four checks, and `goose recipe list` finds the seven recipes. Over
  `goose acp`, summon's `load` lists all ten personas and loads each one, and
  every `agent-harness_*__*` tool a recipe names is in the live tool list.

## goose limits these recipes work around

- **A delegate's `working_dir` must sit inside the parent session's
  directory** (`summon.rs` `resolve_working_dir`). The worktrees live at
  `<repo>.wt/<slug>`, outside the repo, so start `fan-out` and `bmad-story`
  from the repo's parent. If a dispatch is still rejected, the briefs fall
  back to absolute paths (`cd <wt> &&`, `git -C <wt>`). A real fix needs a
  `worktree:` parameter on `delegate`, which is Rust work.
- **Delegates cannot delegate** (`summon.rs`, "Delegated tasks cannot spawn
  further delegations"). So `fan-out` runs `dev` personas, not `bmad-story`,
  and does qa and review delegates itself.
- **The pool refills per batch, not per slot.** goose runs every delegate
  call in one message in parallel, capped by `GOOSE_MAX_CONCURRENT_SUBAGENTS`
  (default 5), and returns when the batch finishes.
- **`response.json_schema` cannot pause for a human.** goose re-prompts at
  every turn end until `final_output` is called (`ops_recipe.rs`). Only
  `bmad-story`, which never waits for approval, declares a schema.
- **Retry checks restart the run from the top** with the conversation reset
  (`ops_retry.rs`). They also run in goose's own working directory. So
  `bmad-story`'s checks `cd` into the worktree, and its steps reuse an
  existing worktree instead of recreating it.
- **Declaring `sub_recipes` without `extensions` leaves the session with
  `summon` alone** (`recipe/mod.rs` `ensure_summon_for_subrecipes`). That
  drops `developer` and every plugin server. Personas are agent files, which
  a sub-recipe path cannot point to anyway, so these recipes use
  `delegate(source: ...)` instead. The validator rejects `sub_recipes` that
  come without `extensions`.
- **A recipe that declares `extensions:` loses the plugin's servers.**
  `goose-cli` adds plugin MCP servers only when the session has no recipe
  `extensions:` list, no profile override and no resume (`builder.rs`,
  around line 590). Every `agent-harness_*` tool would then vanish. None of
  these recipes declares `extensions:`. Don't add one unless you also
  register the three servers in it.
