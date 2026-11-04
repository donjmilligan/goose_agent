#!/usr/bin/env python3
"""PreToolUse guardrail for goose's shell tool.

goose pipes the hook payload as JSON on stdin (``tool_name``, ``tool_input``,
``working_dir``; see ``crates/goose/src/hooks/mod.rs`` ``HookContext``). The
developer extension is exposed unprefixed, so the shell tool arrives as
``shell`` with its command in ``tool_input.command``.

Blocking protocol (``classify_output`` in hooks/mod.rs): exit 2 denies and
goose shows stderr to the model; exit 0 with empty stdout allows. Anything
else is read as a hook failure, so this script never writes to stdout and
allows (exit 0) on any input it cannot understand: a broken guard must not
brick the session.

Chained commands are split on ``&&``, ``||``, ``;``, ``|``, ``&``, newlines,
subshell parentheses and backticks; ``sh -c``/``bash -c``/``eval`` scripts
are checked recursively. ``cd`` and branch switches earlier in the chain are
tracked so ``git switch -c feat && git commit`` on main is not a false block.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import PurePath

SHELL_TOOL_PATTERN = r"^(developer__)?shell$"
SHELL_TOOL = re.compile(SHELL_TOOL_PATTERN)
PROTECTED_BRANCHES = frozenset({"main", "master"})
GIT_TIMEOUT_S = 3
MAX_DEPTH = 4

SEPARATOR_CHARS = frozenset("();|&\n")
REDIRECT_CHARS = frozenset("<>")
ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.DOTALL)
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
WRAPPERS = frozenset({"command", "builtin", "exec", "nohup", "time", "sudo", "env", "xargs"})

GIT_GLOBAL_OPTS_WITH_VALUE = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"}
)
COMMIT_OPTS_WITH_VALUE = frozenset(
    {
        "-m",
        "--message",
        "-F",
        "--file",
        "-C",
        "--reuse-message",
        "-c",
        "--reedit-message",
        "-t",
        "--template",
        "--author",
        "--date",
        "--fixup",
        "--squash",
        "--trailer",
        "--cleanup",
        "--pathspec-from-file",
    }
)
COMMIT_SHORT_WITH_VALUE = frozenset("mFCctuS")
PUSH_OPTS_WITH_VALUE = frozenset({"-o", "--push-option", "--repo", "--receive-pack", "--exec"})
DOCKER_GLOBAL_OPTS_WITH_VALUE = frozenset(
    {"-c", "--context", "-H", "--host", "--config", "-l", "--log-level"}
)
COMPOSE_OPTS_WITH_VALUE = frozenset(
    {
        "-f",
        "--file",
        "-p",
        "--project-name",
        "--env-file",
        "--profile",
        "--project-directory",
        "--ansi",
        "--progress",
        "--parallel",
    }
)
ENV_FILES_ALLOWED = frozenset({".env.example", ".env.template"})


@dataclass
class Context:
    cwd: str
    simulated_branch: dict[str, str] = field(default_factory=dict)

    def git(self, cwd: str, *args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", cwd, *args],
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_S,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        if result.returncode != 0:
            return None
        return result.stdout

    def current_branch(self, cwd: str) -> str | None:
        if cwd in self.simulated_branch:
            return self.simulated_branch[cwd]
        out = self.git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
        if out is None:
            out = self.git(cwd, "symbolic-ref", "--short", "HEAD")
        name = (out or "").strip()
        return name if name and name != "HEAD" else None

    def push_destination(self, cwd: str, branch: str = "") -> str | None:
        """The remote branch `git push` would update for `branch`, from `@{push}`.

        Under `push.default=upstream`, even `git push origin feature/x` goes to
        feature/x's upstream -- which is `main` for a branch created with
        `git switch -c feature/x origin/main`.
        """
        if not branch and cwd in self.simulated_branch:
            return None
        out = self.git(cwd, "rev-parse", "--abbrev-ref", f"{branch}@{{push}}")
        if not out or "/" not in out.strip():
            return None
        return out.strip().split("/", 1)[1]

    def protected(self, cwd: str) -> frozenset[str]:
        out = self.git(cwd, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        if out and "/" in out.strip():
            return PROTECTED_BRANCHES | {out.strip().split("/", 1)[1]}
        return PROTECTED_BRANCHES


def tokenize(command: str) -> list[str]:
    lexer = shlex.shlex(command.replace("`", " ; "), posix=True, punctuation_chars="();<>|&\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def split_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token and set(token) <= SEPARATOR_CHARS:
            segments.append([])
        elif token and set(token) & REDIRECT_CHARS and set(token) <= REDIRECT_CHARS | {"&"}:
            skip_next = True
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def strip_prefix(tokens: list[str]) -> tuple[list[str], dict[str, str]]:
    """Drop `VAR=value` assignments and wrappers like `env`/`sudo`/`command`."""
    env: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        assignment = ASSIGNMENT.match(tokens[i])
        if assignment:
            env[assignment.group(1)] = assignment.group(2)
            i += 1
            continue
        if PurePath(tokens[i]).name in WRAPPERS:
            i += 1
            while i < len(tokens) and tokens[i].startswith("-"):
                i += 1
            continue
        break
    return tokens[i:], env


def evaluate_command(command: str, ctx: Context, depth: int = 0) -> list[str]:
    if depth > MAX_DEPTH:
        return []
    reasons: list[str] = []
    for segment in split_segments(tokenize(command)):
        tokens, env = strip_prefix(segment)
        if not tokens:
            continue
        program = PurePath(tokens[0]).name
        args = tokens[1:]
        if program in SHELLS:
            script = shell_c_script(args)
            if script is not None:
                reasons += evaluate_command(script, ctx, depth + 1)
        elif program == "eval":
            reasons += evaluate_command(" ".join(args), ctx, depth + 1)
        elif program == "cd":
            target = args[0] if args else "~"
            ctx.cwd = os.path.normpath(os.path.join(ctx.cwd, os.path.expanduser(target)))
        elif program == "git":
            reasons += check_git(args, ctx)
        elif program in ("docker", "docker-compose"):
            reasons += check_docker(program, args, env)
        elif program == "gh":
            reasons += check_gh(args)
    return reasons


def shell_c_script(args: list[str]) -> str | None:
    for i, arg in enumerate(args):
        if arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]:
            return args[i + 1] if i + 1 < len(args) else None
    return None


def check_git(args: list[str], ctx: Context) -> list[str]:
    cwd = ctx.cwd
    reasons: list[str] = []
    i = 0
    while i < len(args) and args[i].startswith("-"):
        option = args[i]
        value = None
        if option in GIT_GLOBAL_OPTS_WITH_VALUE and i + 1 < len(args):
            value = args[i + 1]
            i += 1
        if option == "-C" and value is not None:
            cwd = os.path.normpath(os.path.join(cwd, os.path.expanduser(value)))
        if option == "-c" and value and value.split("=", 1)[0].lower() == "core.hookspath":
            reasons.append(
                "`git -c core.hooksPath=...` bypasses the repository's hooks. "
                "Fix what the hook reports instead."
            )
        i += 1
    if i >= len(args):
        return reasons
    subcommand, rest = args[i], args[i + 1 :]

    if subcommand == "commit":
        reasons += check_commit(rest, ctx, cwd)
    elif "--no-verify" in rest:
        reasons.append(no_verify_reason(subcommand))
    if subcommand == "push":
        reasons += check_push(rest, ctx, cwd)
    elif subcommand in ("add", "stage"):
        reasons += check_add(rest, ctx, cwd)
    elif subcommand in ("checkout", "switch"):
        track_branch_switch(subcommand, rest, ctx, cwd)
    return reasons


def no_verify_reason(subcommand: str) -> str:
    return (
        f"`git {subcommand} --no-verify` skips the hooks that gate this repository. "
        "Fix what the hook reports; if it is wrong, say so and let a human decide."
    )


def check_commit(args: list[str], ctx: Context, cwd: str) -> list[str]:
    reasons: list[str] = []
    no_verify = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            break
        if arg == "--no-verify":
            no_verify = True
        elif arg in COMMIT_OPTS_WITH_VALUE:
            i += 1
        elif arg.startswith("-") and not arg.startswith("--"):
            for position, flag in enumerate(arg[1:], start=1):
                if flag == "n":
                    no_verify = True
                if flag in COMMIT_SHORT_WITH_VALUE:
                    if position == len(arg) - 1 and flag not in "uS":
                        i += 1
                    break
        i += 1
    if no_verify:
        reasons.append(no_verify_reason("commit"))
    branch = ctx.current_branch(cwd)
    if branch and branch in ctx.protected(cwd):
        reasons.append(
            f"`git commit` on `{branch}` is not allowed. "
            "Work in a feature branch in its own worktree."
        )
    return reasons


def check_push(args: list[str], ctx: Context, cwd: str) -> list[str]:
    force = False
    every_branch = False
    positionals: list[str] = []
    end_of_options = False
    i = 0
    while i < len(args):
        arg = args[i]
        if end_of_options or not arg.startswith("-") or arg == "-":
            positionals.append(arg)
        elif arg == "--":
            end_of_options = True
        elif arg.startswith("--"):
            name = arg.split("=", 1)[0]
            if name == "--force":
                force = True
            elif name in ("--all", "--mirror", "--branches"):
                every_branch = True
            elif name in PUSH_OPTS_WITH_VALUE and "=" not in arg:
                i += 1
        else:
            for position, flag in enumerate(arg[1:], start=1):
                if flag == "f":
                    force = True
                if flag == "o":
                    if position == len(arg) - 1:
                        i += 1
                    break
        i += 1

    refspecs = positionals[1:]
    if any(spec.startswith("+") for spec in refspecs):
        force = True

    reasons: list[str] = []
    if force:
        reasons.append(
            "`git push --force`/`-f`/`+refspec` can destroy other people's commits. "
            "Use `--force-with-lease`, and only to your own feature branch."
        )
    protected = ctx.protected(cwd)
    if every_branch:
        reasons.append(
            "`git push --all`/`--mirror` also pushes "
            f"{'/'.join(sorted(protected))}. Push your feature branch by name."
        )
        return reasons

    current = ctx.current_branch(cwd)
    targets: set[str] = set()
    if refspecs:
        for spec in refspecs:
            target = refspec_target(spec, current)
            if target:
                targets.add(target)
            if target and ":" not in spec:
                destination = ctx.push_destination(cwd, target)
                if destination:
                    targets.add(destination)
    else:
        if current:
            targets.add(current)
        destination = ctx.push_destination(cwd)
        if destination:
            targets.add(destination)
    for target in sorted(targets & protected):
        reasons.append(
            f"pushing to `{target}` is not allowed. "
            "Push a feature branch; a human merges into the default branch."
        )
    return reasons


def refspec_target(spec: str, current: str | None) -> str | None:
    spec = spec.lstrip("+")
    source, _, destination = spec.partition(":")
    target = destination or source
    if target in ("HEAD", "@"):
        return current
    return target.removeprefix("refs/heads/")


def track_branch_switch(subcommand: str, args: list[str], ctx: Context, cwd: str) -> None:
    if "--" in args:
        return
    creating = {"-b", "-B"} if subcommand == "checkout" else {"-c", "-C", "--create"}
    for i, arg in enumerate(args):
        if arg in creating and i + 1 < len(args):
            ctx.simulated_branch[cwd] = args[i + 1]
            return
    positionals = [arg for arg in args if not arg.startswith("-")]
    if len(positionals) != 1:
        return
    name = positionals[0]
    is_branch = (
        subcommand == "switch"
        or name in PROTECTED_BRANCHES
        or ctx.git(cwd, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}") is not None
    )
    if is_branch:
        ctx.simulated_branch[cwd] = name


def is_secret_path(path: str) -> bool:
    parts = [part.lower() for part in PurePath(path.replace("\\", "/")).parts]
    if not parts:
        return False
    name = parts[-1]
    if name.startswith(".env") and name not in ENV_FILES_ALLOWED:
        return True
    if name.endswith((".pem", ".key")):
        return True
    return any("credentials" in part or "secret" in part for part in parts)


def check_add(args: list[str], ctx: Context, cwd: str) -> list[str]:
    pathspecs: list[str] = []
    every_file = tracked_only = include_ignored = False
    end_of_options = False
    for arg in args:
        if end_of_options or not arg.startswith("-"):
            pathspecs.append(arg)
        elif arg == "--":
            end_of_options = True
        elif arg in ("-A", "--all", "--no-ignore-removal"):
            every_file = True
        elif arg in ("-u", "--update"):
            tracked_only = True
        elif arg in ("-f", "--force"):
            include_ignored = True

    flagged = {path for path in pathspecs if is_secret_path(path)}
    if pathspecs or every_file or tracked_only:
        listing = ["ls-files", "-z", "--modified"]
        if not tracked_only:
            listing.append("--others")
        if not include_ignored:
            listing.append("--exclude-standard")
        listing += ["--", *(pathspecs or [":/"])]
        out = ctx.git(cwd, *listing)
        if out:
            flagged |= {path for path in out.split("\0") if path and is_secret_path(path)}
    if not flagged:
        return []
    return [
        f"`git add` would stage secret-looking files: {', '.join(sorted(flagged))}. "
        "Keep secrets out of git (.gitignore them); `.env.example`/`.env.template` are fine."
    ]


def check_docker(program: str, args: list[str], env: dict[str, str]) -> list[str]:
    if program == "docker":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            if args[i] in DOCKER_GLOBAL_OPTS_WITH_VALUE:
                i += 1
            i += 1
        if i >= len(args) or args[i] != "compose":
            return []
        args = args[i + 1 :]

    named_project = "COMPOSE_PROJECT_NAME" in env
    subcommand = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--project-name") or (arg.startswith("-p") and arg[:2] != "--"):
            named_project = True
        if arg in COMPOSE_OPTS_WITH_VALUE:
            i += 1
        elif not arg.startswith("-") and subcommand is None:
            subcommand = arg
        i += 1
    if subcommand == "down" and not named_project:
        return [
            f"`{program}{' compose' if program == 'docker' else ''} down` without "
            "`-p`/`--project-name` can tear down another worktree's stack. "
            "Name the project explicitly."
        ]
    return []


def check_gh(args: list[str]) -> list[str]:
    positionals = [arg for arg in args if not arg.startswith("-")]
    admin = any(arg == "--admin" or arg.startswith("--admin=") for arg in args)
    if positionals[:2] == ["pr", "merge"] and admin:
        return [
            "`gh pr merge --admin` bypasses branch protection and required reviews. "
            "Agents never merge; leave the PR for a human."
        ]
    return []


def evaluate_payload(raw: str) -> list[str]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        return []
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not SHELL_TOOL.match(tool_name):
        return []
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return []
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return []
    working_dir = payload.get("working_dir")
    cwd = working_dir if isinstance(working_dir, str) and working_dir else os.getcwd()
    return evaluate_command(command, Context(cwd=cwd))


def main() -> int:
    try:
        reasons = evaluate_payload(sys.stdin.read())
    except Exception:
        return 0
    if not reasons:
        return 0
    sys.stderr.write("agent-harness guard: " + " | ".join(reasons) + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
