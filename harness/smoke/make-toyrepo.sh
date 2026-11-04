#!/usr/bin/env bash
# Copy the toyrepo fixture into DEST/toyrepo, make it a git repo with a local
# bare remote at DEST/origin.git, and push main. No network is needed.
#
# Usage: harness/smoke/make-toyrepo.sh DEST
# Prints the absolute repo path on stdout.
set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "usage: $0 DEST" >&2
    exit 2
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
fixture="$here/fixtures/toyrepo"
mkdir -p "$1"
dest="$(cd "$1" && pwd -P)"
repo="$dest/toyrepo"
origin="$dest/origin.git"

if [ -e "$repo" ] || [ -e "$origin" ]; then
    echo "error: $repo or $origin already exists; use an empty DEST" >&2
    exit 1
fi

git init -q --bare -b main "$origin"
cp -R "$fixture" "$repo"

g() { git -C "$repo" "$@"; }
g init -q -b main
g config user.name "harness smoke"
g config user.email "smoke@example.invalid"
g config commit.gpgsign false
g remote add origin "$origin"
g add -A
g commit -q -m "Add toycalc fixture with add() and its tests"
g push -q -u origin main
g remote set-head origin main

printf '%s\n' "$repo"
