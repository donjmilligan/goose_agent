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
`MODEL=<model>` and the key exported. The run in this section is
`runs/20261104-014048`.

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

BMAD_STORY_RUN2_PLACEHOLDER

## Model speed

Method: `reqlog_watch.py` captures every per-request log goose writes. Each
log gives the prompt and output tokens, the start (file birth time) and the
end (mtime). The llama.cpp backend serves one request at a time. I fitted
`time = prompt_tokens / prefill_rate + output_tokens / decode_rate` across
all 108 recipe requests. The fit predicts 3,039 s against 3,027 s measured.

| Metric | Value | Source |
|---|---|---|
| Decode | **≈ 64 tok/s** | fit over 108 requests. Cross-check: a request with a tiny prompt (436 prompt tokens, 1,152 output tokens; the title request in `goose run --no-session --no-profile`) ran at 61.8 tok/s |
| Prefill | **≈ 826 tok/s** | same fit |
| Output tok/s, end to end | 11–15.5 | output tokens ÷ model-busy time, prefill included |
| Prompt per turn | 12.5k tokens at the start of a session, growing to 23k; 17.8k average | request logs |
| Share of model time spent on prefill | **≈ 76 %** (estimate from the fit: 2,322 s prefill against 717 s decode) | fit |
| Model load | 1.3–4.7 s (cold, then warm page cache) | `model_load_ms` in the request log |
| Average turn | 28 s: about 21 s prefill and 7 s decode (425 output tokens on average) | request logs |

The prompt floor is about 12.5k tokens. The harness's share of it, measured in
characters from the applied prompt (the token figures are estimates at about
4 characters per token):

- `WORKING_AGREEMENT.md`, 12.8k characters;
- the 19 `agent-harness_*` tool schemas, 9.2k characters;
- together about 35 % of the 62k-character applied prompt. That is about
  5.5k tokens, or about 6.7 s of re-prefill per turn at 826 tok/s.

`goose run --stats` is not usable for this (defect D6).

## Does local GPU help?

These answers come from the measurements above only.

**What the GPU gives you:**

- **Decode is fine.** About 64 tok/s on the M5 Pro's GPU. A typical turn
  emits about 425 tokens, including thinking, and that takes about 7 s.
- **Prefill is where the time goes.**
  - The llama.cpp backend builds a new context for every request
    (`inference_engine.rs:337,414`), so every turn re-reads the whole prompt
    at about 826 tok/s.
  - Prompts are 12.5k–23k tokens, so that costs 15–28 s per turn: 76 % of
    all model time.
  - qa-check needed 4 turns, or 80 s. start-day needed 13 turns, or 281 s.
    One bmad-story needs well over 90 turns, which is more than 45 minutes.
- **It is sequential.** One request at a time, so fan-out's "5 parallel
  delegates" would queue on one GPU. That was not measured; it follows from
  the backend's single model slot.

**Verdict:**

- For the short, read-mostly recipes (`qa-check`, `start-day`), a local model
  on this GPU is usable. It produced correct reports in about 1.5 and 5
  minutes, with no key and no network.
- For `bmad-story` and `fan-out`, it is not yet practical. That is mostly
  not a GPU limit: about three quarters of the time is spent re-prefilling an
  unchanged prompt prefix.
- **Estimate:** with prompt-prefix KV reuse, which is a Rust change in
  `goose-local-inference`, a turn would drop from about 28 s to about 8 s.
- A second blocker is not about speed at all: a tool-name mismatch made a
  delegate loop (D1).
- Whether a frontier model beats it on wall clock needs an API key. Those
  columns are empty.

## Defects found outside `harness/smoke`

I didn't fix any of these. Each has the location, what I observed against
what I expected, and the evidence. The log paths are relative to
`$SMOKE_ROOT/runs/20261104-014048/logs/`.

**D1 · goose (Rust): the `final_output` nudge names the wrong tool**
- **Location:** `crates/goose/src/agents/final_output_tool.rs:14,17`. The
  tool is `recipe__final_output`, but `FINAL_OUTPUT_CONTINUATION_MESSAGE`
  says "You MUST call the `final_output` tool NOW".
- **Observed:** the qa delegate obeyed literally, about 30 times.
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
- **Location:** `summon.rs:1433-1438`. A delegate's turn limit comes from
  `params.max_turns`, then `GOOSE_SUBAGENT_MAX_TURNS`, then
  `default_max_turns()`, which is 1000 (`config/base.rs:1339-1341`).
  `bmad-story.yaml` steps 1–3 (lines 91-139) and `fan-out` pass no
  `max_turns` to `delegate`.
- **Observed:** the qa delegate ran for 22 minutes, well past the parent's
  `--max-turns 40`.
- **Expected:** the recipes pass `max_turns` on each delegate call, or
  document `GOOSE_SUBAGENT_MAX_TURNS`.
- **What I did:** `run.sh` now sets `GOOSE_SUBAGENT_MAX_TURNS=30`.

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
- **Observed:** with `claim_channel=none` and `clickup_list=none`, Phase 4
  has nothing to rank. The model answered "Cannot recommend a specific
  task", even though `docs/stories/1.1-subtract.md` is `Status: Ready`.
- **Expected:** exactly one recommendation. The recipe could fall back to
  `docs/stories/*` in `Status: Ready` and to unpushed or open branches.
- **Evidence:** the end of `start-day.stream.jsonl`: "**Start here:** Cannot
  recommend a specific task — no ClickUp list and no Slack claims were
  available."

**D9 · harness `bmad-story.yaml:131-136`: the qa brief contradicts itself**
- **Observed:** the brief says "This is read-only: do not edit or commit".
  Two lines later it asks for "Re-run one mutation: break one guarded
  line". qa resolved it by calling `agent-harness_coord__worktree_create`
  for a scratch `qa/1.1-subtract` branch, mutating there, and removing it.
  That is sensible, but it creates a branch and worktree the recipe never
  mentions.
- **Expected:** the brief allows the mutation explicitly: an edit that is
  restored, with `git status` clean, in the story worktree.

**D10 · harness: `bmad-story.yaml:116` and `agents/dev.md:26,61` disagree**
- **Observed:** the recipe says "Touch only the files the story lists". The
  dev persona says to set the story to `Status: Review` and fill in the Dev
  record. dev followed the persona and committed
  `docs/stories/1.1-subtract.md`, which the story does not list.
- **Expected:** one rule. Either the recipe brief names the story file as
  always in scope, or the persona doesn't edit it.

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
