# Live smoke results (2026-11-04)

This was the first time the harness ran inside a real goose:

- **goose:** release build of `main` @ `043d71b6b`, version 1.58.0
- **provider:** goose's built-in `local` provider (llama.cpp, Metal)
- **machine:** Apple M5 Pro, 48 GB unified memory
- **sandbox:** its own `HOME` and `GOOSE_PATH_ROOT`
- **fixture:** `fixtures/toyrepo`, with a local bare `origin`
- **driver:** `run.sh`, with `--max-turns 40` and `--output-format stream-json`

Every number here comes from the logs of these runs. Where a number is an
estimate, it says so.

## Model

**`lmstudio-community/Qwen3.6-35B-A3B-GGUF:Q4_K_M`**: a 21.17 GB GGUF, plus a
0.90 GB vision `mmproj`. The download took 316 s.

The model lives in the sandbox HF cache at
`$SMOKE_ROOT/home/.cache/huggingface/hub/models--lmstudio-community--Qwen3.6-35B-A3B-GGUF`.
To reuse it from another HOME, set
`HF_HUB_CACHE=$SMOKE_ROOT/home/.cache/huggingface/hub`. I checked that
`goose local-models list` then shows it as downloaded.

### How it was chosen

**How goose expresses tool-calling support.** `goose local-models search`
does not report whether a model can call tools. goose decides that at
request time:

- It dry-runs the GGUF's embedded chat template with the tool list
  (`crates/goose-local-inference/src/llamacpp/mod.rs:64-112`).
- It uses native tool calling if llama.cpp returns a tool-call parser.
  Otherwise it falls back to text emulation (`ToolCallingMode::Auto`).
- `native_tool_parsing.rs:23` also parses the Qwen3-Coder XML format,
  `<function=...>`.

So in practice, "tool-calling capable" means a model whose template llama.cpp
can parse. The vendored llama.cpp (`llama-cpp-sys-2` 0.1.146) has the
`qwen35moe`, `qwen3moe` and `qwen3next` architectures.

**How the recommendation works.** `--ram-gb 24` recommends the
highest-quality variant that fits in 0.85 × 24 GB
(`hf_models.rs:1053-1072`).

**The candidates:** `search --ram-gb 24 --quant Q4_K_M --json`, all Q4_K_M.

| Model | Q4_K_M size | Notes |
|---|---|---|
| **Qwen3.6-35B-A3B** (lmstudio-community) | 21.17 GB | newest Qwen MoE, 3B active; Qwen XML tool format; hybrid thinking |
| Qwen3-Coder-30B-A3B-Instruct (unsloth) | 18.56 GB | coder-tuned MoE, 3B active, no thinking |
| Qwen3-30B-A3B-Instruct-2507 (unsloth) | 18.56 GB | older general MoE |
| Devstral-Small-2-24B-Instruct-2512 (unsloth) | 14.33 GB | dense 24B, so roughly 8× the active parameters per token of an A3B MoE |
| gpt-oss-20b (unsloth) | 11.62 GB | smaller |

**Why Qwen3.6-35B-A3B:**

- It is the most capable model whose download fits about 24 GB.
- It has only about 3B active parameters, so decode is fast on Metal. The
  measured decode rate is about 64 tok/s.
- llama.cpp has its architecture and tool format.
- It drove tool calls on the first try: a live
  `agent-harness_coord__worktree_list` call worked.
- unsloth's Q4_K_M did not fit the 24 GB recommendation. lmstudio-community's
  did, so that is the one I used.

**Settings.** All defaults:

- `tool_calling: auto`, which resolved to the native path
- temperature 0.8
- thinking on
- context sized by goose to 103,680 tokens (`n_ctx_seq`)
- flash attention on

## Results

The frontier columns are empty because there is no API key on this machine.
To fill them, re-run `run.sh` with `GOOSE_PROVIDER=<provider>`,
`MODEL=<model>` and the key exported. The runs are:

- `qa-check`, `start-day` and `bmad-story` run 1: `runs/20261104-014048`
- `bmad-story` run 2: `runs/20261104-023434`

Both are under `$SMOKE_ROOT`, which for this report was
`/private/tmp/claude-501/-Users-donjmilligan-Code/bae10d27-f9d2-4082-87b6-40f98d4a3860/scratchpad/fo7mkc-a3`.

### Summary

| Recipe | Local result | Harness tools actually called | Worktree outside the repo | `retry.checks` | Wall clock | Output tok/s (end to end) | Frontier |
|---|---|---|---|---|---|---|---|
| `qa-check` | **PASS** (DONE) | `changed_paths`, `verify_assertions` | n/a | n/a | 80 s | 11.0 | needs an API key |
| `start-day` | **PASS with one miss**: no task recommended (D8) | `worktree_list`, `claim_signals` | n/a, and none created | n/a | 281 s | 12.8 | needs an API key |
| `bmad-story` run 1 | **FAIL**: killed at 2700 s. The qa delegate looped on D1 | parent: `worktree_list`, `claim_signals`, `worktree_create`. dev: `claim_signals`, `changed_paths`, `verify_assertions`. qa: `changed_paths`, `verify_assertions`, `worktree_create` | **yes**: `toyrepo.wt/subtract` | not reached; by hand: both pass | 2706 s | 15.5 | needs an API key |
| `bmad-story` run 2 | **FAIL**: parent hit `--max-turns 40` with no `final_output`. All 3 delegates hit 30 turns (D1, D3). qa caught a real vacuous test | parent: `worktree_list`, `claim_signals`, `worktree_create` (D11), `verify_assertions` (D12). sm: `claim_signals`. qa: `changed_paths` | **yes**: `toyrepo.wt/subtract` | not reached; by hand: both pass | 4024 s | 12.8 | needs an API key |

The tool names in this table drop the `agent-harness_coord__` prefix.
Speed for all runs: **decode ≈ 69 tok/s, prefill ≈ 817 tok/s**, and about
80 % of model time goes to prefill (see Model speed).

### `qa-check`

| | Local: Qwen3.6-35B-A3B Q4_K_M | Frontier |
|---|---|---|
| Result | **PASS**. It followed all 5 steps and reported **DONE**: gate green (3 passed), verify-assertions clean, slow lane "none" | needs an API key |
| Harness tools called | `agent-harness_coord__changed_paths` ×1, `agent-harness_coord__verify_assertions` ×1 | needs an API key |
| Other tools | `shell` ×1, for the gate, run from the worktree | needs an API key |
| Worktree outside the repo | n/a. It checked the pre-made `toyrepo.wt/qa-multiply` | needs an API key |
| `retry.checks` | n/a. The recipe has none | needs an API key |
| Wall clock | **80 s** | needs an API key |
| Model requests | 4. Prompt 12.5k–13.6k tokens each, 822 output tokens in total, 74.5 s of model time | needs an API key |
| Speed | 11.0 output tok/s end to end. Fit: prefill 977 tok/s, decode 40 tok/s (4 points; see the all-runs fit below) | needs an API key |

### `start-day`

| | Local: Qwen3.6-35B-A3B Q4_K_M | Frontier |
|---|---|---|
| Result | **PASS with one miss.** It wrote nothing; the only write was a fetch, which the recipe allows. It skipped Slack and ClickUp and said so. It ran fetch, log and worktree_list, then `gh` (unauthenticated, reported). It found the unpushed `feature/multiply` worktree as an active claim. The output followed the template. **Miss:** Phase 4 said "Cannot recommend a specific task", even though `docs/stories/1.1-subtract.md` is `Status: Ready` (defect D8) | needs an API key |
| Harness tools called | `agent-harness_coord__worktree_list` ×1, `agent-harness_coord__claim_signals` ×1 (topic `multiply`; `remote_branch_count: 1` is the positive control) | needs an API key |
| Other tools | `shell` ×10 | needs an API key |
| Worktree outside the repo | n/a. It is read-only, and created no worktree, as it should | needs an API key |
| `retry.checks` | n/a | needs an API key |
| Wall clock | **281 s** | needs an API key |
| Model requests | 13. Prompt 12.8k–16.5k tokens each, 3,479 output tokens, 271.8 s | needs an API key |
| Speed | 12.8 output tok/s end to end. Fit: prefill 905 tok/s, decode 56 tok/s | needs an API key |

### `bmad-story`, run 1 (`runs/20261104-014048`)

| | Local: Qwen3.6-35B-A3B Q4_K_M | Frontier |
|---|---|---|
| Result | **FAIL: killed by `RUN_TIMEOUT` (2700 s) while the qa delegate was looping.** The parent never reached step 4 or `final_output`. The work itself was right: the branch was pushed, the gate is green, and the mutations are recorded. See the step log below | needs an API key |
| Harness tools, parent | `agent-harness_coord__worktree_list`, `agent-harness_coord__claim_signals` (topic `subtract`, no match), `agent-harness_coord__worktree_create` | needs an API key |
| Harness tools, dev delegate | `agent-harness_coord__claim_signals`, `agent-harness_coord__changed_paths`, `agent-harness_coord__verify_assertions` (clean, 1 file) | needs an API key |
| Harness tools, qa delegate | `agent-harness_coord__changed_paths`, `agent-harness_coord__verify_assertions`, `agent-harness_coord__worktree_create` (a scratch `qa-1.1` worktree for its mutation; removed afterwards) | needs an API key |
| Other tools | parent: `delegate` ×2 (dev, qa), `shell` ×3, `read_image` ×1 (on a `.md` file, which failed). dev: `shell` ×15, `edit` ×8, `recipe__final_output` ×1. qa: `shell` ×8, `edit` ×3 | needs an API key |
| Worktree outside the repo | **Yes.** It is `work/toyrepo.wt/subtract` on `feature/subtract`, a sibling of `work/toyrepo`, which is what `git worktree list` shows. `delegate(working_dir=<that path>)` was accepted, because the run started from `work/` | needs an API key |
| Branch | `feature/subtract` @ `4e1faec`, **pushed** to the bare origin. Commits: `53baeaf Add subtract(a, b) function and tests` and `4e1faec Update story to Review with Dev record` | needs an API key |
| `retry.checks` | **Never ran in goose**, because `final_output` was never reached. Run by hand on the same worktree (`logs/bmad-story.retry-checks-manual.log`): check 1, the gate, gave `4 passed`, exit 0. Check 2 gave `verify-assertions: clean (1 file(s) scanned in tests/test_calc.py)`, exit 0 | needs an API key |
| Wall clock | **2706 s**, killed. The dev delegate took 1,064 s and the qa delegate 1,342 s before the kill | needs an API key |
| Model requests | 91 recorded in the parent process, which includes the delegates. Prompts 18k median and 23.4k max; 41,612 output tokens; 2,681 s | needs an API key |
| Speed | 15.5 output tok/s end to end. Fit: prefill 817 tok/s, decode 65 tok/s | needs an API key |

**Step log for run 1:**

1. **Step 0.** `worktree_list` → `claim_signals` → `worktree_create`, and the
   returned path equals `<repo>.wt/subtract`. Correct, in order.
2. **Step 1.** **Skipped.** The orchestrator decided the story "already looks
   Ready" and never delegated to `sm`. That deviates from the recipe.
3. **Step 2.** The dev delegate did everything right:
   - edited the 3 listed files, committed and pushed early;
   - ran the gate: 4 passed;
   - ran two mutations, each red for the right reason: `a + b` gave
     `assert 8 == 2`, and `0` gave `assert 0 == -3`. Both were restored;
   - ran `changed_paths`, then `verify_assertions` (clean);
   - set the story to `Status: Review`, committed and pushed;
   - reported DONE through `recipe__final_output`.
4. **Step 3.** The qa delegate also did its work correctly:
   - read the story and the diff, then ran `changed_paths` and
     `verify_assertions`;
   - re-ran the gate: 4 passed;
   - ran one mutation in a scratch worktree: `a + b` gave `assert 8 == 2`.
     It restored it, `git status` was clean, and it removed the scratch
     worktree;
   - wrote a PASS report.

   It then tried to submit the report as `<function=final_output>`. That is
   the name in goose's nudge, but the real tool is `recipe__final_output`. The
   call was dropped. goose re-sent "You MUST call the `final_output` tool NOW"
   more than 30 times over 22 minutes, and the model answered with the same
   wrong name each time (defects D1, D2). Delegates are not bounded by
   `goose run --max-turns` (D3).

### `bmad-story`, run 2 (`runs/20261104-023434`, `SUBAGENT_MAX_TURNS=30`, `RUN_TIMEOUT=4800`)

This run used a fresh toy repo, the same model, and delegates capped at 30
turns (D3).

| | Local: Qwen3.6-35B-A3B Q4_K_M | Frontier |
|---|---|---|
| Result | **FAIL: the parent hit `--max-turns 40`** ("I've reached the maximum number of actions…") **without calling `recipe__final_output`.** goose exited 0 with no structured result. The branch is pushed and green, but the qa fix round was never re-reviewed | needs an API key |
| sm delegate | Ran. It rewrote the story to the sm template and committed `e85b8ad`. Then it **hit 30 turns** after 15 wrong-name `final_output` nudges (D1) | needs an API key |
| dev delegate | **Hit 30 turns mid-work, with edits uncommitted.** It spent turns on `git checkout --` and rewrote files twice. The parent then committed and pushed the work itself as `f7239bc` | needs an API key |
| qa delegate | **Found a real defect:** `tests/test_calc.py:20` `test_subtract_importable` asserted only that the import worked, and its listed mutation (dropping it from `__all__`) cannot go red. It **hit 30 turns** after 23 wrong-name nudges (D1). Its verdict still reached the parent through goose's "max turns reached; last response" text | needs an API key |
| Fix round | The parent called `delegate(resume: "<qa id>", working_dir: …)`. That is the wrong subagent, and goose rejected it: "Error: 'resume' only accepts 'instructions'; remove working_dir". The parent then fixed the test itself (`b18a173`, `assert subtract(1, 0) == 1`) and pushed. qa did not run again before the turn cap | needs an API key |
| Harness tools, parent | `agent-harness_coord__worktree_list` ×7, `agent-harness_coord__claim_signals` ×1, `agent-harness_coord__worktree_create` ×3 (the 2nd and 3rd returned D11's misleading error), `agent-harness_coord__verify_assertions` ×1 (`missing`, D12) | needs an API key |
| Harness tools, delegates | sm: `agent-harness_coord__claim_signals`. qa: `agent-harness_coord__changed_paths`. dev: none, because it ran out of turns before `changed_paths` and `verify_assertions` | needs an API key |
| Other tools | parent: `delegate` ×4, `shell` ×21, `edit` ×2. sm: `shell` ×13, `write` ×1. dev: `shell` ×28, `write` ×1. qa: `shell` ×6 | needs an API key |
| Worktree outside the repo | **Yes.** `work/toyrepo.wt/subtract` on `feature/subtract` (`git worktree list`). All three `delegate(working_dir=…)` calls were accepted | needs an API key |
| Branch | `feature/subtract` @ `b18a173`, **pushed** to the bare origin. Commits: `e85b8ad`, then `f7239bc`, then `b18a173` | needs an API key |
| `retry.checks` | **Never ran in goose.** The run ended at the turn cap, before `final_output`. Run by hand afterwards (`logs/bmad-story.retry-checks-manual.log`): check 1, the gate, gave `5 passed`, exit 0. Check 2 gave `verify-assertions: clean (1 file(s) scanned in tests/test_calc.py)`, exit 0 | needs an API key |
| Wall clock | **4024 s**: sm 787 s, dev 772 s, qa 974 s, and the rest in the parent | needs an API key |
| Model requests | 130. Prompts 19.8k median and 37.4k max; 51,428 output tokens; 4,012 s | needs an API key |
| Speed | 12.8 output tok/s end to end. Fit: prefill 811 tok/s, decode 74 tok/s | needs an API key |

**What the two `bmad-story` runs show.** The harness mechanics work with a
local model:

- claim, worktree outside the repo, delegates by persona, push;
- `verify_assertions`, and a qa that catches a vacuous test.

The run outcomes fail for three reasons:

1. **D1 and D2.** Every sm and qa delegate (one sm and two qa runs) wasted
   15–30+ turns on the wrong `final_output` name. The only delegate in
   either run that called `recipe__final_output` successfully was the run-1
   dev. The run-2 dev ran out of turns doing the work itself.
2. **Turn budgets.** Delegates are unbounded unless capped (D3). Capped at 30,
   the cap is eaten by D1.
3. **Model slips.** It skipped sm in run 1, called `read_image` on a `.md`
   file, called `worktree_create` again after it succeeded, and resumed the
   wrong subagent.

Neither run reached goose's `retry.checks`. Their commands pass on both
branches when run by hand.

## Model speed

Method: `reqlog_watch.py` captures every per-request log goose writes. Each
log gives the prompt and output tokens, the start (file birth time) and the
end (mtime). The llama.cpp backend serves one request at a time. I fitted
`time = prompt_tokens / prefill_rate + output_tokens / decode_rate` across
all **238** recipe requests in the four recipe runs. The fit predicts
7,119 s against 7,039 s measured.

| Metric | Value | Source |
|---|---|---|
| Decode | **≈ 69 tok/s** | fit over 238 requests. Per-run fits: 40–74 tok/s, and the qa-check fit has only 4 points. Cross-check: a request with a tiny prompt (436 prompt tokens, 1,152 output tokens; the title request in `goose run --no-session --no-profile`) ran at 61.8 tok/s |
| Prefill | **≈ 817 tok/s** | same fit; per-run fits 811–977 tok/s |
| Output tok/s, end to end | 11–15.5 per run; 13.8 over all runs | output tokens ÷ model-busy time, prefill included |
| Prompt per turn | 12.5k tokens at the start of a session, growing to 37.4k; 19.6k average | request logs |
| Share of model time spent on prefill | **≈ 80 %** (estimate from the fit: about 5,710 s prefill against about 1,409 s decode) | fit |
| Model load | 1.2–4.7 s (cold, then warm page cache) | `model_load_ms` in the request log |
| Average turn | 29.6 s: about 24 s prefill and 6 s decode (409 output tokens on average) | request logs |

The prompt floor is about 12.5k tokens. The harness's share of it, measured in
characters from the applied prompt (the token figures are estimates at about
4 characters per token):

- `WORKING_AGREEMENT.md`, 12.8k characters;
- the 19 `agent-harness_*` tool schemas, 9.2k characters;
- together about 35 % of the 62k-character applied prompt. That is about
  5.5k tokens, or about 6.7 s of re-prefill per turn at 817 tok/s.

`goose run --stats` is not usable for this (defect D6).

## Does local GPU help?

These answers come from the measurements above only.

**What the GPU gives you:**

- **Decode is fine.** About 69 tok/s on the M5 Pro's GPU. A typical turn
  emits about 410 tokens, including thinking, and that takes about 6 s.
- **Prefill is where the time goes.**
  - The llama.cpp backend builds a new context for every request
    (`inference_engine.rs:337,414`), so every turn re-reads the whole prompt
    at about 817 tok/s.
  - Prompts are 12.5k–37k tokens, so that costs 15–45 s per turn: about
    80 % of all model time.
  - qa-check needed 4 turns, or 80 s. start-day needed 13 turns, or 281 s.
    One bmad-story took 91–130 requests, or 45–67 minutes, and still did not
    finish.
- **It is sequential.** One request at a time, so fan-out's "5 parallel
  delegates" would queue on one GPU. That was not measured; it follows from
  the backend's single model slot.

**Verdict:**

- For the short, read-mostly recipes (`qa-check`, `start-day`), a local model
  on this GPU is usable. It produced correct reports in about 1.5 and 5
  minutes, with no key and no network. start-day's single miss (D8) is a
  recipe gap more than a model one.
- For `bmad-story` and `fan-out`, it is not practical today. Two things are
  in the way, and neither is a GPU limit:
  - about 80 % of model time goes to re-prefilling an unchanged prompt
    prefix (D5);
  - every delegate burns its turns on the wrong-name `final_output` nudge
    (D1, D2).
- **Estimate:** with prompt-prefix KV reuse, which is a Rust change in
  `goose-local-inference`, a turn would drop from about 30 s to about 8 s.
  That assumes about 1k new prompt tokens per turn plus decode.
- Fixing D1, D2 and D3 is required before any model can finish
  `bmad-story` reliably. Whether a frontier model would also trip on D1 is
  untested: it needs an API key, which is why those columns are empty.

## Defects found outside `harness/smoke`

I didn't fix any of these during the smoke. The harness ones (D3's recipe
side, D8 to D12) were fixed afterwards; each says so in its **Status** line.
Each has the location, what I observed against what I expected, and the
evidence. Unless a defect names a run, its log
paths are relative to `$SMOKE_ROOT/runs/20261104-014048/logs/`.

**D1 · goose (Rust): the `final_output` nudge names the wrong tool**
- **Location:** `crates/goose/src/agents/final_output_tool.rs:14,17`. The
  tool is `recipe__final_output`, but `FINAL_OUTPUT_CONTINUATION_MESSAGE`
  says "You MUST call the `final_output` tool NOW".
- **Observed:** the qa delegate obeyed literally, about 30 times, in run 1.
  In run 2, sm got 15 nudges and qa got 23, and each hit the 30-turn cap
  with 0 successful `recipe__final_output` calls
  (`runs/20261104-023434/logs/bmad-story.subagent-*.session.json`).
- **Expected:** the nudge names the real tool.
- **Evidence** (the last 3 requests all end the same way):
  ```
  llm_request generated_text: ...Verdict: PASS\n</think>\n\n<tool_call>\n<function=final_output>\n<parameter=summary>\n## QA Report ...
  request tools: ['recipe__final_output']
  qa session: ~30× user "You MUST call the `final_output` tool NOW..." → assistant [thinking only]
  ```

**D2 · goose-local-inference (Rust): a call to an unknown tool is dropped silently**
- **Location:** the native tool path, `llamacpp/inference_native_tools.rs`.
- **Observed:** the call to the nonexistent `final_output` turned into an
  assistant message with only thinking in it. It produced no tool request
  and no "unknown tool" error, so the model never learned its mistake.
- **Expected:** a tool error naming the available tools.
- **Evidence:** `bmad-story.qa-subagent.session.json`: the last 60 messages
  alternate between the nudge and thinking-only assistant turns.

**D3 · harness recipes and goose: delegates are not bounded by `--max-turns`**
- **Status:** harness side fixed on `fix/harness-smoke-defects` (`e19981342`). Every `delegate(source: ...)` in the recipes passes `max_turns` (dev and po 60, analyst, pm and architect 40, sm and reviewers 30), and the recipe validator rejects a call without one. The goose side (`--max-turns` not reaching delegates) is unchanged.
- **Location:** `summon.rs:1433-1438`. A delegate's turn limit comes from
  `params.max_turns`, then `GOOSE_SUBAGENT_MAX_TURNS`, then
  `default_max_turns()`, which is 1000 (`config/base.rs:1339-1341`).
  `bmad-story.yaml` steps 1–3 (lines 91-139) and `fan-out` pass no
  `max_turns` to `delegate`.
- **Observed:** the qa delegate ran for 22 minutes, well past the parent's
  `--max-turns 40`.
- **Expected:** the recipes pass `max_turns` on each delegate call, or
  document `GOOSE_SUBAGENT_MAX_TURNS`.
- **What I did:** `run.sh` now sets `GOOSE_SUBAGENT_MAX_TURNS`. Run 2 used
  30, and the default is now 60 because 30 was too few for dev.

**D4 · goose CLI (Rust): headless `goose run` never fires `SessionStart`**
- **Location:** `crates/goose-cli/src/session/mod.rs`.
  `interactive()` (lines 586-590) calls `emit_session_start`.
  `headless()` (lines 1333-1343) only emits `SessionEnd`.
- **Observed:** the plugin's `claim_gate.py` SessionStart hook does not run
  for any `goose run --recipe …`. That covers every recipe in this smoke,
  plus `fan-out`.
- **Expected:** the harness docs say it puts the claim gate in front of
  every session. The claim signals still happen, but only because the
  recipes call `agent-harness_coord__claim_signals` themselves.
- **Evidence:** the goose CLI log says `Loaded plugin hooks rule_count=3
  events=["Stop","SessionStart","PreToolUse"]`. That SessionStart never
  fires headless is read from source; I did not observe it live.

**D5 · goose-local-inference (Rust): no prompt-prefix KV reuse between turns**
- **Location:** `llamacpp/inference_engine.rs:337,414` calls
  `new_context(...)` for every request.
- **Observed:** consecutive requests with 11,357 and then 11,618 prompt
  tokens both took about 12 s.
- **Expected:** the second request re-prefills only the delta.
- **Evidence:** about 76 % of the 3,027 s of model time went to prefill
  (the speed table above).

**D6 · goose CLI (Rust): `--stats` tokens/sec is wrong for llama.cpp**
- **Location:** `session/mod.rs:2184-2215`. Only the MLX backend attaches
  `ProviderStats` (`mlx.rs:362`). For llama.cpp, `--stats` divides the last
  request's output tokens by the whole run's wall clock, which includes the
  title request and the model load.
- **Observed** on the benchmark: `--stats` printed `Tokens/sec: 25.75`. From
  the request log, the main request alone produced 1,135 tokens in 20.9 s
  once the title request ahead of it had finished. That is about 54 tok/s,
  prefill of 4.1k tokens included.
- **Expected:** the per-request generation rate.

**D7 · goose (Rust): session titling is expensive on a local model**
- **Location:** `session_naming.rs` generates the title on the same model,
  with thinking on.
- **Observed:** even with `--no-session`, the title request took 1,385 and
  1,152 output tokens (about 19–22 s) for a title of four words or fewer. It
  ran ahead of the first real request, which waited behind it.
- **Expected:** a cheap title, or none for `--no-session`.
- **Note:** `-n <name>` avoids it, which is why `run.sh` names every session.

**D8 · harness `start-day.yaml:93-105`: no candidate source when Slack and ClickUp are off**
- **Status:** fixed on `fix/harness-smoke-defects` (`31b8a8fdf`). `Status: Ready` stories under `docs/stories/` are always candidates, and Phase 4 recommends one of them when Slack and ClickUp are off.
- **Observed:** with `claim_channel=none` and `clickup_list=none`, Phase 4
  has nothing to rank. The model answered "Cannot recommend a specific
  task", even though `docs/stories/1.1-subtract.md` is `Status: Ready`.
- **Expected:** exactly one recommendation. The recipe could fall back to
  `docs/stories/*` in `Status: Ready` and to unpushed or open branches.
- **Evidence:** the end of `start-day.stream.jsonl`: "**Start here:** Cannot
  recommend a specific task — no ClickUp list and no Slack claims were
  available."

**D9 · harness `bmad-story.yaml:131-136`: the qa brief contradicts itself**
- **Status:** fixed on `fix/harness-smoke-defects` (`dde253084`). The brief and `agents/qa.md` now say the same thing: the mutation and gate run in a throwaway `agent-harness_coord__worktree_create` worktree (slug `qa-<slug>`, branch `qa/<slug>`, base `origin/<story branch>`), never pushed and removed when done. The story worktree stays read-only.
- **Observed:** the brief says "This is read-only: do not edit or commit".
  Two lines later it asks for "Re-run one mutation: break one guarded
  line". qa resolved it by calling `agent-harness_coord__worktree_create`
  for a scratch `qa/1.1-subtract` branch, mutating there, and removing it.
  That is sensible, but it creates a branch and worktree the recipe never
  mentions.
- **Expected:** the brief allows the mutation explicitly: an edit that is
  restored, with `git status` clean, in the story worktree.

**D10 · harness: `bmad-story.yaml:116` and `agents/dev.md:26,61` disagree**
- **Status:** fixed on `fix/harness-smoke-defects` (`dde253084`). The dev brief and `agents/dev.md` both put the story file in scope, limited to its `Status:` line and Dev record. qa's scope check and fan-out's brief allow the same.
- **Observed:** the recipe says "Touch only the files the story lists". The
  dev persona says to set the story to `Status: Review` and fill in the Dev
  record. dev followed the persona and committed
  `docs/stories/1.1-subtract.md`, which the story does not list.
- **Expected:** one rule. Either the recipe brief names the story file as
  always in scope, or the persona doesn't edit it.

**D11 · harness `coord/src/harness_coord/coord.py:140-142`: re-creating an existing worktree gives a misleading error**
- **Status:** fixed on `fix/harness-smoke-defects` (`36d5bc66e`). `path_exists` is checked before containment, with a regression test that was red first (`path_inside_repo` != `path_exists`).
- **Observed:** the containment check runs before the `path_exists` check.
  So `agent-harness_coord__worktree_create` on a slug whose worktree already
  exists returns `path_inside_repo`, with "refusing worktree at
  …/toyrepo.wt/subtract: it is inside …/toyrepo.wt/subtract". The target is
  reported as inside itself.
- **Expected:** `path_exists` (line 142), or an idempotent success when the
  branch matches.
- **Impact:** `bmad-story`'s "reuse what an earlier attempt left behind"
  depends on the model reading this error correctly. In run 2 the model
  called `worktree_create` three times; the second and third returned this
  error.
- **Evidence:** `runs/20261104-023434/logs/bmad-story.stream.jsonl`.

**D12 · harness `coord/src/harness_coord/server.py:65-71` and `assertions.py:270`: `verify_assertions` resolves paths against the server's cwd, not the repo**
- **Status:** fixed on `fix/harness-smoke-defects` (`720c416dc`). `verify_assertions(paths, repo=".")` resolves relative paths against `repo` (CLI: `--repo`), and the bmad-story, fan-out and qa-check briefs and the dev and qa personas pass the worktree. A live goose test covers it.
- **Observed:** `agent-harness_coord__changed_paths(repo=…)` returns
  repo-relative paths. `agent-harness_coord__verify_assertions(paths)` has
  no `repo` parameter, so it resolves those paths against the MCP server's
  working directory, which is the goose session's directory. `bmad-story`
  must start from the repo's parent (see the goose limits in the recipes
  README), so when the parent session passed `tests/test_calc.py` it got
  back `"ok": false, "files_scanned": 0, "missing": ["tests/test_calc.py"]`.
- **Context:** the same call worked in qa-check and in the delegates, whose
  cwd is the worktree. It is good that the miss was reported as not clean.
- **Expected:** a `repo` argument, the same as `changed_paths` has, or
  briefs that require absolute paths. `qa-check.yaml:57-59` and the dev
  brief in `bmad-story.yaml` (around line 120) say only "the changed test
  files".
- **Evidence:** `runs/20261104-023434/logs/bmad-story.stream.jsonl`.

### Not exercised, so not verified

- **The `PreToolUse` guard (`guard.py`).** No run tried a forbidden command.
  The guard did not block any of the legitimate `git push -u origin
  feature/subtract` and gate commands.
- **The `Stop` hook.** `HARNESS_NOTIFY=0`.
- **The Slack and ClickUp servers.** They loaded; their 13 tools are in
  every request's tool list. They were never called, because the
  integrations were `none` and there is no token.

### Confirmed working

- `install.sh` into a sandbox HOME and `GOOSE_PATH_ROOT`. A re-run prints
  "nothing to do".
- The plugin was discovered from the `GOOSE_PATH_ROOT/.agents/plugins`
  symlink, and all 3 MCP servers started through `uv run`.
- The tool prefix is `agent-harness_<server>__<tool>`, as the README says.
- The working agreement reaches the system prompt through the
  `.goosehints` include: the "Content from
  agent-harness-working-agreement.md" block is present in the applied
  prompt.
- Recipes resolve by bare name from `~/.agents/recipes`.
- `delegate(source: "dev" | "qa")` resolves the installed personas.
- The worktree lands at `<repo>.wt/<slug>`, outside the repo.
