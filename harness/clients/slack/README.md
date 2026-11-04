# harness-slack

A Slack client and stdio MCP server, `slack`, for the goose agent harness. goose
exposes its tools as `slack__<tool>`.

| Tool | Does | Writes? |
|---|---|---|
| `read_channel(channel, hours=24, limit=200)` | Top-level messages from the last `hours`, oldest first, at most `limit` (the newest ones) | no |
| `read_thread(channel, thread_ts)` | The thread parent, then every reply | no |
| `post_message(channel, text)` | A new top-level message | **allowlisted channels only** |
| `reply(channel, thread_ts, text)` | A reply in a thread | **allowlisted channels only** |
| `find_claims(channel, topic, hours=36)` | Open `_CLAIM` messages mentioning `topic` | no |

`channel` is always a channel **ID** (`C0123ABCD`), not a `#name`.

## Posting is allowlisted

Posting is outward-facing, so `post_message` and `reply` refuse any channel ID
that is not in `HARNESS_SLACK_ALLOWED_CHANNELS`. The refusal happens before any
API call. With the variable unset, posting is disabled everywhere. Reads are
unrestricted: the bot can read any channel it is a member of.

## Claims

`find_claims` reads the fan-out claim convention:

```
_CLAIM (Don's session, fan-out fo-ggjl, agent 2/7): `harness-slack`_ — new Slack MCP.
    └ _COMPLETION: `harness-slack`_ — feature/harness-slack @ <sha>, evidence.
```

- A message is a claim or a completion only if its text **starts with** `_CLAIM` or
  `_COMPLETION`. Quoting one mid-sentence does not count.
- The id is the first backticked token.
- A claim is closed by a `_COMPLETION` in **its own thread**, posted after it, with
  the same id (case-insensitive). If either message has no id, any later
  completion in the thread closes it. For a top-level claim, its thread is its
  replies. For a claim posted as a reply (for example, under a batch header), it
  is the thread the claim sits in.
- `topic` is matched case-insensitively against the claim text. An empty topic
  matches every claim.
- A completion posted at top level does not close a claim. The claim stays open
  with `completion_outside_thread: true`, so the mismatch is visible instead of
  silently resolved.
- `messages_scanned` counts the top-level messages and thread replies read. It is
  the positive control: an empty `open_claims` with a non-zero count means there
  are no open claims. With zero, nothing was read; check the channel ID and that
  the bot is in the channel.
- Known limit: only threads whose parent is inside the `hours` window are read.

## Slack app setup (one-time, by a human)

1. Create an app at <https://api.slack.com/apps>, choosing **From scratch**, in your
   workspace.
2. Under **OAuth & Permissions → Bot Token Scopes**, add:
   - `channels:history`: read public channels
   - `groups:history`: read private channels
   - `chat:write`: post and reply
3. **Install to Workspace**, then copy the **Bot User OAuth Token** (`xoxb-…`).
4. Invite the bot to every channel it should read or post in:
   `/invite @<bot name>` in that channel. The history scopes do not apply to
   channels the bot is not a member of.
5. Find a channel's ID under its name → **About** (at the bottom), or in the
   channel URL.

## Environment

| Variable | Required | Meaning |
|---|---|---|
| `SLACK_BOT_TOKEN` | for any tool call | Bot token, `xoxb-…`. The server starts and lists its tools without it; each call then fails with an error naming the variable. Never logged. |
| `HARNESS_SLACK_ALLOWED_CHANNELS` | to post | Comma-separated channel IDs that `post_message` and `reply` may write to. |
| `HARNESS_ENV_FILE` | no | A dotenv file to load at startup. Variables already in the environment win (`override=False`). |

The client uses `slack_sdk`'s `RateLimitErrorRetryHandler`, so it honours
`Retry-After` on HTTP 429 up to 5 times. It also uses the SDK's default
connection-error retry. Every list call follows `next_cursor` pagination.

## Register with goose

In `~/.config/goose/config.yaml`, under `extensions:`:

```yaml
slack:
  enabled: true
  type: stdio
  name: slack
  cmd: uv
  args: [run, --project, /absolute/path/to/harness/clients/slack, harness-slack-mcp]
  envs:
    HARNESS_ENV_FILE: /absolute/path/to/harness.env
  timeout: 300
```

Put `SLACK_BOT_TOKEN` and `HARNESS_SLACK_ALLOWED_CHANNELS` in that env file rather
than in `config.yaml`. The harness plugin's `.mcp.json.template` registers the same
command.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

The tests mock `WebClient`, never touch the network, and pass with no token set.
`tests/test_server.py` also starts the real server over stdio and checks that
`tools/list` returns exactly the five tools above.

`mcp` is pinned to `<2` because mcp 2.x renamed `FastMCP` to `MCPServer`.
