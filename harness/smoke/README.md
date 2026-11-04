# harness/smoke: live smoke test

Runs the harness inside a real goose, against goose's built-in local provider
(llama.cpp with Metal), in a throwaway sandbox. It never touches your real
`~/.config/goose`, `~/.agents` or `~/.local`. Results from the first run are in
[`RESULTS.md`](RESULTS.md).

| File | What it is |
|---|---|
| `run.sh` | installs the harness into the sandbox, builds the toy repo, runs `qa-check`, `start-day` and `bmad-story`, and writes a summary per recipe |
| `make-toyrepo.sh DEST` | copies `fixtures/toyrepo` to `DEST/toyrepo`, `git init`s it, and pushes `main` to a bare `DEST/origin.git`, so claims, fetches and pushes work with no network |
| `fixtures/toyrepo/` | a tiny `uv` Python package: `add()`, two pytest tests, and one story (`docs/stories/1.1-subtract.md`) |
| `reqlog_watch.py` | keeps every goose per-request LLM log during a run (goose keeps only the last 10) and summarises tokens, timing and a prefill/decode fit |
| `transcript.py` | renders a `stream-json` log as a readable transcript; `--tools` counts tool calls in an exported session |

## Prerequisites

- a goose binary (release build; local inference is compiled in on macOS)
- `uv`, `git`, `python3`
- a downloaded local model in the sandbox. The sandbox is `$SMOKE_ROOT`, with
  `HOME=$SMOKE_ROOT/home` and `GOOSE_PATH_ROOT=$SMOKE_ROOT/root`:

```bash
export SMOKE_ROOT=/path/to/sandbox
HOME=$SMOKE_ROOT/home GOOSE_PATH_ROOT=$SMOKE_ROOT/root GOOSE_DISABLE_KEYRING=1 \
  $GOOSE_BIN local-models search "Qwen3.6-35B-A3B GGUF" --ram-gb 24 --quant Q4_K_M
HOME=$SMOKE_ROOT/home GOOSE_PATH_ROOT=$SMOKE_ROOT/root GOOSE_DISABLE_KEYRING=1 \
  $GOOSE_BIN local-models download 'lmstudio-community/Qwen3.6-35B-A3B-GGUF:Q4_K_M'
```

The model lands in `$SMOKE_ROOT/home/.cache/huggingface/hub/`.

## Run

```bash
GOOSE_BIN=/abs/path/to/goose \
MODEL='lmstudio-community/Qwen3.6-35B-A3B-GGUF:Q4_K_M' \
SMOKE_ROOT=/path/to/sandbox \
  harness/smoke/run.sh
```

| Variable | Default | Meaning |
|---|---|---|
| `GOOSE_BIN` | required | absolute path of the goose under test |
| `MODEL` | required | id from `goose local-models list` |
| `SMOKE_ROOT` | `$TMPDIR/harness-smoke` | the sandbox |
| `RECIPES` | `qa-check start-day bmad-story` | which recipes to run, in order |
| `MAX_TURNS` | `40` | `goose run --max-turns` for each recipe |
| `RUN_TIMEOUT` | `2700` | seconds before a recipe is killed |
| `SUBAGENT_MAX_TURNS` | `60` | exported as `GOOSE_SUBAGENT_MAX_TURNS`. `--max-turns` does not reach delegates, which otherwise get 1000 turns (RESULTS.md, D3). The 2026-11-04 run used 30, and the dev delegate ran out of turns; 60 has not been run yet |
| `GOOSE_PROVIDER` | `local` | set it, plus an API key, to run the same smoke against a frontier model |

Each run writes `$SMOKE_ROOT/runs/<stamp>/`:

- `work/toyrepo`, `work/toyrepo.wt/`, `work/origin.git`: the repo the recipes worked on
- `logs/<recipe>.stream.jsonl`: `goose run --output-format stream-json`
- `logs/<recipe>.session.json`: the exported main session
- `logs/<recipe>.subagent-<id>.session.json`: one per delegate; `goose session list` does not show these
- `logs/bmad-story.retry-checks-manual.log`: the recipe's two `retry.checks`, re-run by hand after the recipe
- `logs/<recipe>.requests.jsonl`: one line per model request (prompt and output tokens, start, end)
- `logs/<recipe>.summary.txt`: exit code, wall clock, tool calls per session, `git worktree list`, origin branches, model speed

Logs and models stay in the sandbox. Nothing in it belongs in git.

## How each recipe is driven

| Recipe | cwd | Parameters |
|---|---|---|
| `qa-check` | `toyrepo.wt/qa-multiply` (branch `feature/multiply`, one commit adding `multiply()` and a test) | `project_dir` = that worktree, `gate_command="uv run pytest -q"` |
| `start-day` | `toyrepo` | `project_dir` = `toyrepo`; Slack and ClickUp left at `none` |
| `bmad-story` | `work/`, the repo's parent, so `summon` accepts `toyrepo.wt/subtract` as a delegate `working_dir` | `story_path=docs/stories/1.1-subtract.md`, `gate_command="uv run pytest -q"`, `slug=subtract`, `branch=feature/subtract`, `harness_coord="uv run --project <harness>/coord harness-coord"` |

## Measuring speed

goose's `--stats` reports real numbers only for the MLX backend. For llama.cpp
it divides the last request's output tokens by the whole run's wall clock,
which is meaningless for a multi-turn recipe. `reqlog_watch.py` uses goose's
own request logs instead. Each file's birth time is the request start and
its mtime is the end. The file carries `prompt_tokens`, `output_tokens` and
`model_load_ms`.

The "end to end" tokens/sec includes prefill. `fit_prefill_tok_per_s` and
`fit_decode_tok_per_s` come from a least-squares fit of
`time = prompt/prefill + output/decode` across the requests.

The fixture's own `tests/` are not part of the harness test suites. Run
pytest on `harness/<slice>` directories, not on `harness/` as a whole.
