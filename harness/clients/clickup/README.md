# harness-clickup

ClickUp REST v2 client plus a stdio MCP server named `clickup`, for the goose agent harness.
The generic client is vendored from vanta-lms `api_clients/clickup_api_handler`. All of the SOW
import code was left out.

## Setup

```bash
cd harness/clients/clickup
uv sync
uv run pytest -q          # no token or network needed
uv run harness-clickup-mcp  # stdio server; Ctrl-C to stop
```

The parent shell may export `VIRTUAL_ENV` for another project. If it does, `unset VIRTUAL_ENV`
first so that uv uses this project's `.venv`.

## Environment

| Variable | Purpose |
|---|---|
| `CLICKUP_API_TOKEN` | Personal API token (ClickUp → Settings → Apps → API Token). It is required at the first tool call, not at startup. It is never logged. |
| `HARNESS_ENV_FILE` | Optional path to a dotenv file. It is loaded with `override=False`, so an exported variable wins. No other `.env` is searched. |
| `HARNESS_ALLOW_DONE` | Set to `1` to let `set_status` move tasks to `done`/`closed`-type statuses. Agents stop at review by default. |

## Tools

| Tool | Notes |
|---|---|
| `get_task(task_id)` | Returns name, description, status, tags, parent, `list_id` and url. |
| `list_tasks(list_id, include_closed=False)` | Includes subtasks. It is paged until ClickUp reports `last_page`. |
| `get_comments(task_id)` | Returns every comment, newest first. |
| `list_statuses(list_id)` | Returns each status with its ClickUp type (`open`, `custom`, `done`, `closed`). |
| `create_task(list_id, name, description="", parent=None, tags=None)` | Never retried on 5xx, so it cannot create duplicates. Read the list first. |
| `add_comment(task_id, text)` | |
| `update_task(task_id, name=None, description=None, priority=None)` | Has no status field. Priority runs from 1 (urgent) to 4 (low). |
| `set_status(task_id, status, comment)` | Reads the task and its list's statuses, then posts the comment, then changes the status. |

`set_status` refuses an empty comment, an unknown status, and any `done`/`closed`-type status
unless `HARNESS_ALLOW_DONE=1`. Every check runs before the first write, so a refused call leaves
no stray comment.

## Register in goose

Add this to `~/.config/goose/config.yaml` under `extensions:`:

```yaml
clickup:
  enabled: true
  type: stdio
  name: clickup
  cmd: uv
  args: [run, --project, /absolute/path/to/harness/clients/clickup, harness-clickup-mcp]
  envs: {}
  env_keys: []
  timeout: 300
```

goose starts stdio extensions with its own environment (`extension_manager/stdio.rs` does not
clear it), so exported variables reach the server. Add `CLICKUP_API_TOKEN` to `env_keys` only if
you keep the token in goose's secret store. Don't list the optional variables there: goose refuses
to load an extension when a listed key resolves to nothing
(`agents/extension.rs` `merge_environments`).

goose names the tools `clickup__<tool>`, for example `clickup__set_status`.

## Client behaviour

- Writes do not depend on a local state file. Callers read live state before they write.
- Rate limits (429) are retried for every method. The wait honours `X-RateLimit-Reset` or
  `Retry-After`.
- 5xx responses, `ConnectionError` and `Timeout` are retried with exponential backoff, but only
  for GET, PUT and DELETE. A POST is resent only after a `ConnectTimeout`, because then the request
  never left the machine.
- When retries run out, the error reports the real last status, or `no response` with the
  transport cause.
- Path segments, including tag names, are URL-encoded.
- `ensure_tag` ignores only the "already exists" refusal.

## Gates

```bash
uv sync && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright
```
