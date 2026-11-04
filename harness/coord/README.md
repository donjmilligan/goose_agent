# harness-coord

Deterministic coordination tools for the goose agent harness. These are the
steps a model would otherwise improvise: checking claims, creating worktrees,
generating ids, listing changed files, and scanning tests for vacuous absence
assertions. They ship as a stdio MCP server named `coord` and as a CLI that
hooks and recipe `retry.checks` can call.

Every git call goes through `harness_coord.gitcmd.run_git`. It never prompts
for credentials (`GIT_TERMINAL_PROMPT=0`), and network calls time out after 15 s.

## Tools

goose exposes these as `coord__<tool>`. Every successful result is a JSON object
with an `ok` field. A refusal or failure is an MCP tool error (`isError: true`),
so goose reports the call as failed. Its text is
`Error executing tool <tool>: {"ok": false, "error": {"code": ..., "message": ...}}`.
The CLI prints the same `{"ok": false, ...}` object and exits 1.

| Tool | What it does |
|---|---|
| `claim_signals(topic, repo=".")` | Runs `git ls-remote --heads origin` and `git worktree list --porcelain` and returns the remote branches and worktrees whose names match `topic`. Matching is case-insensitive, and `-`, `_`, `/`, `.` and spaces count as the same character. It also returns `remote_branch_count` and `worktree_count`. Those totals are the positive control: if nothing matches and the counts are above 0, everything was read and nothing matched. If `ls-remote` fails, the call is a tool error with code `network`, never an empty list. |
| `worktree_create(slug, branch, base="origin/main", repo=".")` | Creates `<main-checkout>.wt/<slug>` on a new branch from `base`, using `--no-track` so a bare `git push` can't reach `base`. It resolves the main checkout even when called from inside a linked worktree. It refuses an invalid slug (`[a-z0-9][a-z0-9._-]{0,63}`, no `..`). It refuses a target path that already exists (code `path_exists`, checked first, so re-creating a live worktree says so). It refuses a target that resolves inside any checkout, symlinks included (code `path_inside_repo`). It refuses a branch that already exists locally or on origin (code `branch_exists`). It never pushes. |
| `worktree_list(repo=".")` | Returns `count` and `worktrees`. Each worktree has `path`, `head`, `branch`, `detached`, `bare`, `locked` and `prunable`. |
| `gen_hash(length=4)` | Returns a lowercase alphanumeric id built with `secrets`, for example the `{hash}` in a decision-record filename. Never invent one. |
| `changed_paths(base="origin/main", repo=".")` | Returns `committed` (files changed between merge-base(base, HEAD) and HEAD), `uncommitted` (staged, unstaged and untracked files), and `paths` (the sorted union of both). |
| `verify_assertions(paths, repo=".")` | Ported from vanta-lms `scripts/verify_test_assertions.py`. Relative `paths` resolve against `repo`, not the server's working directory, so pass the same worktree you gave `changed_paths` and its repo-relative paths work as they are. Absolute paths are used as given. It flags a test that asserts a spy collector is empty when nothing else in the test proves the code ran (see below). It returns `clean`, `files_scanned` (the positive control), `findings`, `parse_errors` and `missing`. |

### What `verify_assertions` flags, and why so little

A collector is a local that the test sets to `x = []` and then appends to
with `x.append(...)`. A test is flagged when it asserts
`x == []`, `not x`, or `len(x) == 0` and has no positive assertion. An empty
list looks the same whether the code declined to act or never ran.

The check is narrow on purpose. A broader version produced dozens of false
positives, and a guard that fails on day one gets switched off. A deliberate
absence assertion is exempt when it has `# absence-ok: <reason>` on the line
before it (or on the same line). A bare `# absence-ok:` with no reason is
rejected.

There is one change from the original. A file that won't parse is reported
in `parse_errors` and fails the scan. The original treated it as clean.

## CLI

```bash
uv run --project harness/coord harness-coord claim-signals harness-coord
uv run --project harness/coord harness-coord worktree-create my-slug feature/my-slug [--base origin/main]
uv run --project harness/coord harness-coord worktree-list
uv run --project harness/coord harness-coord gen-hash [--length 4]       # prints just the hash
uv run --project harness/coord harness-coord changed-paths [--base origin/main]
uv run --project harness/coord harness-coord verify-assertions tests/ [--repo .] [--json]
```

Every subcommand except `gen-hash` and `verify-assertions` prints JSON. The
subcommands that touch a repo also take `--repo`.

| Exit | Meaning |
|---|---|
| 0 | ok / clean |
| 1 | refused or failed (JSON `error` on stdout). For `verify-assertions`, findings. |
| 2 | usage error. For `verify-assertions`, a missing path or an unparseable file. |

## Registering with goose

Add this to `~/.config/goose/config.yaml` (the harness plugin's `.mcp.json`
template does the same):

```yaml
extensions:
  coord:
    enabled: true
    type: stdio
    name: coord
    cmd: uv
    args: [run, --project, /absolute/path/to/goose_agent/harness/coord, harness-coord-mcp]
    timeout: 300
```

## Development

```bash
cd harness/coord
uv sync && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright
```

The tests use real git: a temporary bare `origin` and a clone under
`tmp_path`. They need no network and no tokens.
