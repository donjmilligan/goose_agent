#!/usr/bin/env bash
# Live smoke of the agent-harness against a real goose binary and a local model.
#
# Re-runs, in a throwaway sandbox (its own HOME and GOOSE_PATH_ROOT):
#   1. harness/install.sh
#   2. a toy git repo with a local bare origin (make-toyrepo.sh)
#   3. goose run --recipe qa-check | start-day | bmad-story
# and writes logs plus a per-recipe summary under $SMOKE_ROOT/runs/<stamp>/.
# See harness/smoke/README.md.
#
# Required:  GOOSE_BIN  absolute path to a goose binary
#            MODEL      model id as printed by `goose local-models list`
# Optional:  SMOKE_ROOT       sandbox dir (default: $TMPDIR/harness-smoke)
#            RECIPES          space-separated subset (default: "qa-check start-day bmad-story")
#            MAX_TURNS        goose --max-turns per recipe (default: 40)
#            RUN_TIMEOUT      wall-clock seconds per recipe before it is killed (default: 2700)
#            SUBAGENT_MAX_TURNS  GOOSE_SUBAGENT_MAX_TURNS for delegates (default: 60). goose's
#                             --max-turns does not reach delegates, which default to 1000.
#                             The recipes now pass max_turns on every delegate call, which
#                             takes precedence; this stays as the fallback (D3).
#                             30 was too few for dev in the 2026-11-04 run (RESULTS.md).
#            HF_HUB_CACHE     where the model already lives, if not under the sandbox HOME
#            GOOSE_PROVIDER   default: local
set -euo pipefail

: "${GOOSE_BIN:?set GOOSE_BIN to an absolute goose binary path}"
: "${MODEL:?set MODEL to a local model id (goose local-models list)}"
case "$GOOSE_BIN" in /*) ;; *) echo "error: GOOSE_BIN must be absolute" >&2; exit 2 ;; esac
[ -x "$GOOSE_BIN" ] || { echo "error: $GOOSE_BIN is not executable" >&2; exit 2; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
HARNESS="$(cd "$HERE/.." && pwd -P)"
SMOKE_ROOT="${SMOKE_ROOT:-${TMPDIR:-/tmp}/harness-smoke}"
RECIPES="${RECIPES:-qa-check start-day bmad-story}"
MAX_TURNS="${MAX_TURNS:-40}"
RUN_TIMEOUT="${RUN_TIMEOUT:-2700}"
SUBAGENT_MAX_TURNS="${SUBAGENT_MAX_TURNS:-60}"
GATE="uv run pytest -q"

mkdir -p "$SMOKE_ROOT"
SMOKE_ROOT="$(cd "$SMOKE_ROOT" && pwd -P)"

# --- sandbox: never the real ~/.config/goose, ~/.agents or ~/.local ---------
REAL_HOME="$HOME"
export GOOSE_PATH_ROOT="$SMOKE_ROOT/root"
export HOME="$SMOKE_ROOT/home"
export GOOSE_DISABLE_KEYRING=1
export HARNESS_NOTIFY=0
export GOOSE_PROVIDER="${GOOSE_PROVIDER:-local}"
export GOOSE_MODEL="$MODEL"
export GOOSE_SUBAGENT_MAX_TURNS="$SUBAGENT_MAX_TURNS"
unset VIRTUAL_ENV
# Drop any activated virtualenv from PATH, and put the goose under test first
# so the plugin's hooks and recipes see the same binary.
PATH="$(printf '%s' "$PATH" | tr ':' '\n' | grep -v '/\.venv/bin$' | paste -sd: -)"
PATH="$(dirname "$GOOSE_BIN"):$PATH"
export PATH
mkdir -p "$GOOSE_PATH_ROOT" "$HOME"
if [ -z "${HF_HUB_CACHE:-}" ] && [ -d "$REAL_HOME/.cache/huggingface/hub" ] && [ ! -d "$HOME/.cache/huggingface/hub" ]; then
    echo "note: the sandbox HOME has no HF cache; set HF_HUB_CACHE if the model lives elsewhere" >&2
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
RUN="$SMOKE_ROOT/runs/$STAMP"
LOGS="$RUN/logs"
WORK="$RUN/work"
mkdir -p "$LOGS" "$WORK"
echo "run dir: $RUN"

if ! "$GOOSE_BIN" local-models list >"$LOGS/local-models-list.txt" 2>&1 \
    || ! grep -qF "$MODEL" "$LOGS/local-models-list.txt"; then
    echo "error: model $MODEL is not downloaded in this sandbox (see $LOGS/local-models-list.txt)" >&2
    exit 1
fi

# --- step 2: install ---------------------------------------------------------
bash "$HARNESS/install.sh" >"$LOGS/install.log" 2>&1
echo "install: ok ($LOGS/install.log)"

# --- step 3: toy repo + a change for qa-check -------------------------------
REPO="$(bash "$HERE/make-toyrepo.sh" "$WORK")"
QA_WT="$REPO.wt/qa-multiply"
git -C "$REPO" worktree add -q -b feature/multiply "$QA_WT" origin/main
cat >>"$QA_WT/src/toycalc/calc.py" <<'PY'


def multiply(a: int, b: int) -> int:
    """Return the product of a and b."""
    return a * b
PY
cat >>"$QA_WT/tests/test_calc.py" <<'PY'


def test_multiply_returns_product() -> None:
    from toycalc.calc import multiply

    assert multiply(3, 4) == 12
PY
git -C "$QA_WT" commit -qam "Add multiply() with its test"
(cd "$REPO" && $GATE >"$LOGS/gate-precheck.log" 2>&1) || { echo "error: fixture gate is red" >&2; exit 1; }
echo "toy repo: $REPO (qa worktree $QA_WT)"

# --- step 4: recipes -------------------------------------------------------
REQLOG_DIR="$GOOSE_PATH_ROOT/state/logs"
mkdir -p "$REQLOG_DIR"

# run_recipe NAME CWD -- goose run args...
run_recipe() {
    local name="$1" cwd="$2" rc start end watcher pid waited=0
    shift 3
    local out="$LOGS/$name.stream.jsonl" err="$LOGS/$name.stderr.log" req="$LOGS/$name.requests.jsonl"
    python3 -I "$HERE/reqlog_watch.py" watch "$REQLOG_DIR" "$req" &
    watcher=$!
    start="$(date +%s)"
    (cd "$cwd" && exec "$GOOSE_BIN" run --recipe "$name" -n "smoke-$name-$STAMP" \
        --max-turns "$MAX_TURNS" --output-format stream-json "$@" </dev/null >"$out" 2>"$err") &
    pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        if [ "$waited" -ge "$RUN_TIMEOUT" ]; then
            echo "timeout after ${RUN_TIMEOUT}s" >>"$err"
            pkill -TERM -P "$pid" 2>/dev/null || true
            kill -TERM "$pid" 2>/dev/null || true
            break
        fi
        sleep 5
        waited=$((waited + 5))
    done
    rc=0
    wait "$pid" || rc=$?
    end="$(date +%s)"
    sleep 1
    kill -TERM "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true

    "$GOOSE_BIN" session export --name "smoke-$name-$STAMP" --format json \
        -o "$LOGS/$name.session.json" >/dev/null 2>&1 || true
    # Delegates run in their own sessions, which `session list` does not show.
    local sub subs
    subs="$(grep -o '"subagent_session_id":"[^"]*"' "$out" 2>/dev/null | sed 's/.*:"//; s/"$//' | sort -u || true)"
    for sub in $subs; do
        "$GOOSE_BIN" session export --session-id "$sub" --format json \
            -o "$LOGS/$name.subagent-$sub.session.json" >/dev/null 2>&1 || true
    done
    {
        echo "recipe: $name"
        echo "exit: $rc"
        echo "wall_clock_s: $((end - start))"
        if grep -q "^timeout after" "$err"; then echo "killed: RUN_TIMEOUT ${RUN_TIMEOUT}s reached"; fi
        echo "tool calls (main session):"
        python3 -I "$HERE/transcript.py" --tools "$LOGS/$name.session.json" | sed 's/^/  /' || true
        for sub in $subs; do
            echo "tool calls (delegate session $sub):"
            python3 -I "$HERE/transcript.py" --tools "$LOGS/$name.subagent-$sub.session.json" | sed 's/^/  /' || true
        done
        echo "git worktree list:"
        git -C "$REPO" worktree list | sed 's/^/  /'
        echo "origin branches:"
        git -C "$REPO" ls-remote --heads origin | sed 's/^/  /'
        echo "model requests:"
        python3 -I "$HERE/reqlog_watch.py" summary "$req" | sed 's/^/  /'
    } >"$LOGS/$name.summary.txt"
    echo "== $name: exit $rc, $((end - start))s ($LOGS/$name.summary.txt)"
}

for recipe in $RECIPES; do
    case "$recipe" in
        qa-check)
            run_recipe qa-check "$QA_WT" -- \
                --params project_dir="$QA_WT" --params gate_command="$GATE" --params base=origin/main
            ;;
        start-day)
            run_recipe start-day "$REPO" -- --params project_dir="$REPO"
            ;;
        bmad-story)
            # From the repo's parent, so summon accepts <repo>.wt/<slug> as a
            # delegate working_dir (harness/recipes/README.md, "goose limits").
            run_recipe bmad-story "$WORK" -- \
                --params story_path=docs/stories/1.1-subtract.md \
                --params gate_command="$GATE" \
                --params project_dir="$REPO" \
                --params slug=subtract --params branch=feature/subtract \
                --params harness_coord="uv run --project $HARNESS/coord harness-coord"
            # The recipe's two retry.checks, run again by hand, so their
            # outcome is on record even when goose never reached them.
            {
                echo "## retry check 1: gate"
                (cd "$REPO.wt/subtract" && $GATE) && echo "exit=0" || echo "exit=$?"
                echo "## retry check 2: verify-assertions on changed tests"
                (
                    cd "$REPO.wt/subtract" || exit 1
                    files="$(git diff --name-only --diff-filter=AM "origin/main...HEAD" | grep -E '(^|/)(test_[^/]*|[^/]*_test)\.py$' || true)"
                    if [ -z "$files" ]; then
                        echo "verify-assertions: no changed Python test files"
                    else
                        # shellcheck disable=SC2086 # one path per word, as in the recipe
                        uv run --project "$HARNESS/coord" harness-coord verify-assertions $files
                    fi
                ) && echo "exit=0" || echo "exit=$?"
            } >"$LOGS/bmad-story.retry-checks-manual.log" 2>&1
            ;;
        *) echo "unknown recipe: $recipe" >&2; exit 2 ;;
    esac
done

"$GOOSE_BIN" session list --format json >"$LOGS/sessions.json" 2>/dev/null || true
echo "done: $RUN"
