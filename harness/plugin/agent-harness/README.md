# agent-harness (goose plugin)

This plugin adds three hooks for multi-agent development:

- a claim gate when a session starts
- a guardrail on the shell tool
- a desktop notification when a turn ends

It also ships the MCP server template for the harness's `clickup`, `slack` and `coord` servers. The scripts use only the Python 3.12 standard library.

```
agent-harness/
├── plugin.json            name, version, description
├── hooks/hooks.json       SessionStart, PreToolUse, Stop
├── scripts/
│   ├── claim_gate.py      SessionStart
│   ├── guard.py           PreToolUse on the shell tool
│   └── notify.py          Stop
├── .mcp.json.template     rendered to .mcp.json by harness/install.sh
├── tests/                 pytest, no network and no tokens needed
└── pyproject.toml         dev-tool config only (pytest, ruff, pyright)
```

## Install

goose discovers plugins in `~/.agents/plugins/<name>/` (`Paths::plugins_dir()`) and in `<project>/.agents/plugins/<name>/`. It names each plugin after its **directory**, so the link must be called `agent-harness`:

```bash
ln -s "$PWD/harness/plugin/agent-harness" ~/.agents/plugins/agent-harness
```

`harness/install.sh` does this for you. It also renders `.mcp.json` from the template. The rendered `.mcp.json` is gitignored. To turn the plugin off, add `"agent-harness"` to `disabledPlugins` in `~/.config/goose/settings.json`.

## How goose runs these hooks

These rules come from `crates/goose/src/hooks/mod.rs`:

- Every hook gets the event as JSON on stdin and runs as `sh -c <command>`, with `${PLUGIN_ROOT}` substituted.
- **stdout is the decision channel.** A `PreToolUse` hook allows only by exiting 0 with empty stdout. It denies by exiting 2, and goose shows stderr to the model as the reason. Anything else counts as a hook failure. By default a failure is logged and the call goes ahead (`on_failure` unset). That is deliberate here: a broken guard must not brick a session.
- `Stop` is a blocking event too. If `notify.py` exited 2 or printed `{"decision":"block"}`, the turn would keep going. So it never writes to stdout and always exits 0.
- `SessionStart` stdout is read as `{"banner": "..."}` (`emit_collecting_banners`). goose shows the banner **to the user only**: `session_start_message` marks it `agent_visible = false`, so the model never sees it. When the model needs the claim signals, it calls `coord__claim_signals`.

### The shell tool's name

The developer extension is registered with `unprefixed_tools: true` in `agents/platform_extensions/mod.rs`. Its tool is `Tool::new("shell", ...)` with `ShellParams { command, timeout_secs }`. So `tool_name` is **`shell`**, not `developer__shell`, and the command is in `tool_input.command`.

The matcher is `^(developer__)?shell$`:

- It is anchored, because goose matches unanchored and a bare `shell` would also match `remote__shell_exec`.
- It still accepts the prefixed form that the older `hello-hooks` example uses.

`guard.py` checks the same pattern itself. A test asserts that the two copies are identical and checks both against the goose source.

## SessionStart: `claim_gate.py`

The claim gate is a port of vanta-lms `claim_gate.py` with the vanta specifics removed. It prints:

- the claim checklist
- `git ls-remote --heads origin`, with the total branch count as a positive control
- `git worktree list`, because an unpushed worktree is invisible to every other signal
- open Slack claims: `_CLAIM` messages from the last 36 h that have no `_COMPLETION` reply in their thread

The Slack check runs only when both `SLACK_BOT_TOKEN` and `HARNESS_CLAIM_CHANNEL` are set. `HARNESS_CLAIM_CHANNEL` must be a channel **ID**. If either variable is missing, the banner says the Slack signal was skipped. It never implies that it checked.

Each git call has an 8 s limit. Each Slack call has a 6 s limit, inside a 12 s total budget. The script always exits 0.

## PreToolUse: `guard.py`

The guard blocks with exit 2 and a reason in each of these cases. Each one has a paired allow case in `tests/test_guard.py`.

| Blocked | Allowed |
|---|---|
| `--no-verify` on any git command; `git commit -n`; `git -c core.hooksPath=…` | a commit message that mentions `--no-verify` |
| `git push --force`, `-f`, `-uf`, or a `+refspec` | `--force-with-lease` to a feature branch |
| any push whose target is `main`, `master`, or `origin/HEAD`'s branch, including `--force-with-lease`, `HEAD:main`, `--delete main`, `--all`/`--mirror` | pushes to feature branches, `--tags` |
| a bare `git push` or `git push origin <branch>` whose real destination (`<branch>@{push}`) is protected | the same command when the upstream is the feature branch |
| `gh pr merge --admin` | `gh pr merge` without `--admin` |
| `docker compose down` / `docker-compose down` without `-p`/`--project-name` | with `-p`, or with `COMPOSE_PROJECT_NAME=…` as a prefix |
| `git commit` while the branch (`git -C <cwd> rev-parse`) is protected | the same on a feature branch |
| `git add` / `git stage` of `.env*`, `*.pem`, `*.key`, `*credentials*`, `*secret*` | `.env.example`, `.env.template` |

The `@{push}` row needs some background. Under `push.default=upstream`, `git push origin feature/x` sends `feature/x`'s commits to its *upstream*. For a branch created with `git switch -c feature/x origin/main`, that upstream is `main`. A test confirms this with git itself.

How the guard reads a command:

- **Parsing:** commands are parsed with `shlex` and split on `&&`, `||`, `;`, `|`, `&`, newlines, `( )`, `$( )` and backticks. Scripts passed to `sh -c` and `bash -c`, and `eval` strings, are checked recursively.
- **Prefixes:** leading `VAR=value` assignments are skipped, and so are the wrappers `env`, `sudo`, `command`, `exec`, `nohup`, `time` and `xargs`.
- **Chain state:** `cd`, `git -C`, and `git switch`/`checkout` earlier in the same chain are tracked. So `git switch -c feat && git commit` run on main is allowed.
- **Broad adds:** `git add .`, `-A`, `-u` and directory adds are expanded with `git ls-files --others --modified --exclude-standard` to the files they would actually stage. With `-f`, ignored files are included too.

If the payload or command can't be parsed (bad JSON, an unclosed quote, a non-shell tool), the guard allows it and never crashes.

Known limits. The guard approximates a shell; it is not one.

- It does not see commands built at runtime, such as `$cmd` or `git $(echo push) -f`.
- It does not see git aliases.
- It does not see scripts that a command executes.
- In code-execution mode, the inner tool calls go through `code_execution.rs` → `call_tool` and bypass `run_pre_tool_hooks`, so this hook never sees them.

The hook is a guardrail, not a sandbox.

## Stop: `notify.py`

The notifier is a port of vanta-lms `notify.py`:

- **macOS:** `terminal-notifier`, falling back to `osascript`
- **Linux:** `notify-send`
- **Windows:** a PowerShell toast

The message is the start of `last_assistant_message`. It is passed as an argv item, or as an environment variable on Windows, and never spliced into a script. That fixes the original's quoting injection. Set `HARNESS_NOTIFY=0` to silence it.

## MCP servers: `.mcp.json.template`

The template uses the format `plugins/mcp_servers.rs` parses: `mcpServers.<name>.{command, args, env, cwd}`. `install.sh` replaces `${HARNESS_ROOT}`, and goose itself expands `${PLUGIN_ROOT}`.

**No `env` block on purpose.** `mcp_servers.rs` copies `env` values literally, and only `${PLUGIN_ROOT}` is expanded. Stdio children also inherit goose's environment (`extension_manager/stdio.rs` never clears it). Because plugin servers have no `env_keys`, a missing optional variable can't fail extension load. So the six contract variables pass through by inheritance:

- `CLICKUP_API_TOKEN`
- `SLACK_BOT_TOKEN`
- `HARNESS_SLACK_ALLOWED_CHANNELS`
- `HARNESS_CLAIM_CHANNEL`
- `HARNESS_ENV_FILE`
- `HARNESS_ALLOW_DONE`

Writing `"SLACK_BOT_TOKEN": "${SLACK_BOT_TOKEN}"` would replace the real token with that literal text. The template's `x-harness-env-passthrough` key records the list for install and tests, and goose ignores unknown keys. If goose is launched from the desktop app, these variables must be in *its* environment, or in `HARNESS_ENV_FILE` for the clients that read it.

**Tool names.** goose names a plugin's MCP extension `<plugin>:<server>`, and `name_to_key` turns that key into `agent-harness_clickup`. Tools loaded through this plugin are therefore `agent-harness_clickup__get_task`, not `clickup__get_task`. The harness personas and recipes use these prefixed names (decided at integration; see `harness/decisions/2026-11-04-s0id-harness-architecture.md`). Registering a server by hand as an ordinary extension named `clickup`, `slack` or `coord` gives the short names instead.

## Fixed from the vanta-lms hooks

- **`pre_commit_check.py`:**
  - Its matcher `Bash(git commit:*)` was permission syntax, so it never fired.
  - It exited 1, which never blocks.
  - It ignored stdin.

  The guard here matches the real tool name with a regex, exits 2, and reads the payload.
- **`notify.py`:** it spliced the message into a PowerShell string.
- **`post_task_gate.py`:** not ported. Running gates belongs in recipes' `retry.checks`, not in a Stop hook that can block the turn.

## Tests and gates

```bash
cd harness/plugin/agent-harness
uv run --with pytest --with ruff --with pyright pytest -q
uv run --with ruff ruff check . && uv run --with ruff ruff format --check .
uv run --with pyright pyright
```

`uv sync && uv run pytest -q` also works, using the `dev` dependency group.
