#!/usr/bin/env bash
# Validate the harness against a real goose binary, with no model and no tokens.
#
#   harness/recipes/tests/goose_validate.sh [path/to/goose] [pytest args...]
#
# The goose binary comes from the first argument, else $GOOSE_BIN, else `goose`
# on PATH. All goose state lives in a pytest temp sandbox (GOOSE_PATH_ROOT and
# HOME are overridden), so the real ~/.config/goose and ~/.agents are untouched.
# See test_goose_live.py for what is checked.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
harness="$(cd "$here/../.." && pwd)"

if [ $# -gt 0 ] && [ -x "$1" ] && [ ! -d "$1" ]; then
    GOOSE_BIN="$1"
    shift
fi
GOOSE_BIN="${GOOSE_BIN:-$(command -v goose || true)}"
if [ -z "$GOOSE_BIN" ] || [ ! -x "$GOOSE_BIN" ]; then
    echo "error: no goose binary; pass its path or set GOOSE_BIN" >&2
    exit 2
fi
GOOSE_BIN="$(cd "$(dirname "$GOOSE_BIN")" && pwd)/$(basename "$GOOSE_BIN")"
export GOOSE_BIN
unset VIRTUAL_ENV

for project in coord clients/clickup clients/slack; do
    uv sync --quiet --project "$harness/$project"
done

cd "$harness/.."
exec uv run --no-project --with pytest --with pyyaml \
    pytest "$here/test_goose_live.py" -q -p no:cacheprovider "$@"
