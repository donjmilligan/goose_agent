"""`harness-coord`: the same tools as the MCP server, for hooks and recipe checks.

Exit codes: 0 ok, 1 refused/failed (or findings, for verify-assertions),
2 usage error (or unreadable input, for verify-assertions).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from . import assertions, coord
from .gitcmd import CoordError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness-coord", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("claim-signals", help="branches/worktrees matching a topic")
    p.add_argument("topic")
    p.add_argument("--repo", default=".")

    p = sub.add_parser("worktree-create", help="create <repo>.wt/<slug> on a new branch")
    p.add_argument("slug")
    p.add_argument("branch")
    p.add_argument("--base", default="origin/main")
    p.add_argument("--repo", default=".")

    p = sub.add_parser("worktree-list", help="list worktrees")
    p.add_argument("--repo", default=".")

    p = sub.add_parser("gen-hash", help="random lowercase alphanumeric id")
    p.add_argument("--length", type=int, default=4)

    p = sub.add_parser("changed-paths", help="files changed since merge-base, plus uncommitted")
    p.add_argument("--base", default="origin/main")
    p.add_argument("--repo", default=".")

    p = sub.add_parser("verify-assertions", help="flag vacuous spy-collector absence asserts")
    p.add_argument("paths", nargs="*", default=["."])
    p.add_argument("--json", action="store_true", help="print the structured result")
    return parser


def _emit(result: dict[str, Any]) -> None:
    print(json.dumps(result, indent=2))


def _verify(paths: list[str], as_json: bool) -> int:
    result = assertions.scan(paths)
    if as_json:
        _emit(result.as_dict())
    else:
        out, err = assertions.format_report(result, paths)
        sys.stdout.write(out)
        sys.stderr.write(err)
    if result.missing or result.parse_errors:
        return 2
    return 1 if result.findings else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "verify-assertions":
        return _verify(args.paths, args.json)
    try:
        match args.command:
            case "claim-signals":
                result = coord.claim_signals(args.topic, args.repo)
            case "worktree-create":
                result = coord.worktree_create(args.slug, args.branch, args.base, args.repo)
            case "worktree-list":
                result = coord.worktree_list(args.repo)
            case "gen-hash":
                result = coord.gen_hash(args.length)
            case "changed-paths":
                result = coord.changed_paths(args.base, args.repo)
            case _:
                raise AssertionError(f"unhandled command {args.command}")
    except CoordError as exc:
        _emit({"ok": False, "error": exc.as_dict()})
        return 1
    if args.command == "gen-hash":
        print(result["hash"])
    else:
        _emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
