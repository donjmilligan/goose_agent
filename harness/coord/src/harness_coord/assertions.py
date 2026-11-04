"""Reject Python tests that assert only that something did NOT happen.

Ported from vanta-lms `scripts/verify_test_assertions.py`; the detection rule
is unchanged.

## The bug this makes unreachable

    def test_degraded_recalc_does_not_write_back(...):
        ...
        assert write_calls == []          # passes if the code never ran

An absence assertion cannot distinguish "the feature correctly declined to
act" from "the feature was never invoked", because absence is also what
breakage produces. Delete the code under test and the assertion still holds.
The failure mode is silence: a vacuous test never flakes, so no amount of
re-running finds it. A parse-level check has no timing dependency.

## The rule, and why it is narrow on purpose

A test is flagged when it asserts that a **spy collector is empty** and
nothing else in the test proves the code under test ran:

    calls = []                                  # collector, seeded empty
    monkeypatch.setattr(mod, "write", lambda *a: calls.append(a))
    do_the_thing(...)
    assert calls == []                          # <- flagged

A first, broader version flagged any absence assertion without a paired
positive and produced ~50 findings on one service, almost all false
positives (`assert detect(...) is None` runs the unit inside the assertion).
A guard with a failing baseline gets disabled within a week, so this catches
one shape precisely rather than gesturing at a category.

**What it does not catch.** `assert phrase not in output` where the code ran
but dropped the upstream text, so the phrase was absent for the wrong
reason. Only mutation-checking finds that. This narrows the gap; it does not
close it.

## Escape hatch

Mark a deliberate absence assertion on the line before (or the same line),
**with a reason**:

    # absence-ok: the parent shape must not have this field at all
    assert "cohens_d" not in ParentSafeOutcomeSummary.model_fields

A bare pragma is not accepted; the reason is what a future reader needs.

## One deliberate change from the original

The original printed parse errors and returned no findings, so an
unparseable file scanned "clean". Here a parse error is reported separately
and makes the scan fail (CLI exit 2): a checker that reads nothing must not
say it found nothing.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OPT_OUT = "absence-ok:"
SKIP_DIRS = frozenset({".venv", "venv", "__pycache__", "node_modules", ".git"})

_EMPTY_LITERALS: tuple[object, ...] = (0, "", None, False)


class UnparseableSource(ValueError):
    pass


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    test_name: str
    snippet: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "line": self.line,
            "test_name": self.test_name,
            "snippet": self.snippet,
        }


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    files_scanned: int = 0
    parse_errors: list[dict[str, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.findings or self.parse_errors or self.missing)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": not (self.parse_errors or self.missing),
            "clean": self.clean,
            "files_scanned": self.files_scanned,
            "findings": [f.as_dict() for f in self.findings],
            "parse_errors": self.parse_errors,
            "missing": self.missing,
        }


def _is_empty_constant(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return node.value in _EMPTY_LITERALS
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in {"set", "dict", "list", "tuple", "frozenset"} and not node.args
    return False


def _classify(test: ast.expr) -> str:
    """Return "absence", "positive", or "neutral" for one assert expression."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return "absence"

    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        op = test.ops[0]
        right = test.comparators[0]
        if isinstance(op, ast.NotIn):
            return "absence"
        if isinstance(op, ast.In):
            return "positive"
        if isinstance(op, ast.Is) and isinstance(right, ast.Constant) and right.value is None:
            return "absence"
        if isinstance(op, ast.IsNot) and isinstance(right, ast.Constant) and right.value is None:
            return "positive"
        if isinstance(op, (ast.Eq, ast.Is)):
            return "absence" if _is_empty_constant(right) else "positive"
        return "positive"

    if isinstance(test, ast.BoolOp):
        parts = {_classify(v) for v in test.values}
        if "positive" in parts:
            return "positive"
        return "absence" if "absence" in parts else "neutral"

    return "positive"


def _opted_out(lines: list[str], lineno: int) -> bool:
    """An `# absence-ok: reason` on the assert's line or the line before."""
    for candidate in (lineno - 1, lineno - 2):
        if 0 <= candidate < len(lines):
            text = lines[candidate]
            if OPT_OUT in text and text.split(OPT_OUT, 1)[1].strip():
                return True
    return False


def _iter_test_functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test"
        ):
            yield node


def _collector_names(func: ast.AST) -> set[str]:
    """Locals seeded `x = []` AND appended to in the same test.

    `x = []` alone is often an empty fixture; the `.append` is what makes
    emptiness ambiguous between "declined to act" and "never ran".
    """
    seeded: set[str] = set()
    appended: set[str] = set()
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.List)
            and not node.value.elts
        ):
            seeded.update(t.id for t in node.targets if isinstance(t, ast.Name))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
        ):
            appended.add(node.func.value.id)
    return seeded & appended


def _asserts_collector_is_empty(test: ast.expr, collectors: set[str]) -> str | None:
    """The flagged shape, and only it: `c == []`, `not c`, `len(c) == 0`."""

    def _name_of(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name) and node.id in collectors:
            return node.id
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "len"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in collectors
        ):
            return node.args[0].id
        return None

    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _name_of(test.operand)
    if (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], (ast.Eq, ast.Is))
        and _is_empty_constant(test.comparators[0])
    ):
        return _name_of(test.left)
    return None


def check_source(source: str, path: Path) -> list[Finding]:
    """Findings for one file's source. Raises UnparseableSource on a syntax error."""
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise UnparseableSource(f"{path}: could not parse: {exc}") from exc

    lines = source.splitlines()
    findings: list[Finding] = []
    for func in _iter_test_functions(tree):
        collectors = _collector_names(func)
        if not collectors:
            continue

        flagged: list[ast.Assert] = []
        has_positive = False
        for node in ast.walk(func):
            if not isinstance(node, ast.Assert):
                continue
            if _asserts_collector_is_empty(node.test, collectors) is not None:
                if not _opted_out(lines, node.lineno):
                    flagged.append(node)
            elif _classify(node.test) == "positive":
                has_positive = True

        if flagged and not has_positive:
            first = flagged[0]
            snippet = lines[first.lineno - 1].strip() if first.lineno - 1 < len(lines) else ""
            findings.append(Finding(path, first.lineno, func.name, snippet))
    return findings


def iter_test_files(root: Path) -> Iterator[Path]:
    """An explicit file is scanned whatever its name; a directory yields `test_*.py`."""
    if root.is_file():
        yield root
        return
    for file in sorted(root.rglob("test_*.py")):
        if not SKIP_DIRS.intersection(file.relative_to(root).parts):
            yield file


def scan(paths: Iterable[str | Path], repo: str | Path = ".") -> ScanResult:
    """Relative paths resolve against `repo`, so the repo-relative paths that
    `changed_paths(repo=...)` returns can be passed straight through."""
    result = ScanResult()
    targets = [Path(repo) / p for p in paths]
    result.missing = [str(t) for t in targets if not t.exists()]
    if result.missing:
        return result
    for target in targets:
        for file in iter_test_files(target):
            result.files_scanned += 1
            try:
                result.findings.extend(check_source(file.read_text(encoding="utf-8"), file))
            except (UnparseableSource, UnicodeDecodeError) as exc:
                result.parse_errors.append({"path": str(file), "message": str(exc)})
    return result


def format_report(result: ScanResult, targets: list[str]) -> tuple[str, str]:
    """(stdout, stderr) text for the CLI."""
    if result.missing:
        return "", f"no such path: {', '.join(result.missing)}\n"
    err: list[str] = [f"{e['message']}\n" for e in result.parse_errors]
    if result.findings:
        err.append(
            f"\n{len(result.findings)} test(s) assert only that something did NOT happen, "
            "with nothing proving the code under test ran:\n\n"
        )
        for f in result.findings:
            err.append(f"  {f.path}:{f.line}  {f.test_name}\n      {f.snippet}\n")
        err.append(
            "\nAdd one positive assertion proving the path executed, or mark the line\n"
            f"  # {OPT_OUT} <why absence is the whole point here>\n"
        )
    out = ""
    if result.clean:
        out = (
            f"verify-assertions: clean ({result.files_scanned} file(s) scanned in "
            f"{', '.join(targets)})\n"
        )
    return out, "".join(err)
