"""Tests for the assertion scanner, ported from vanta-lms
`ai_service/tests/gate/test_verify_test_assertions.py`.

Assertions here name the **specific line and test flagged**, never merely that
the finding count is non-zero: a checker for vacuous assertions whose own
tests were vacuous would complete the joke it exists to end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness_coord import assertions as checker


def _check(source: str):
    return checker.check_source(source, Path("fixture_test.py"))


VACUOUS = """
def test_does_not_write(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "write", lambda *a: calls.append(a))
    do_the_thing()
    assert calls == []
"""


def test_flags_a_spy_collector_asserted_empty_with_no_positive():
    findings = _check(VACUOUS)

    assert [f.test_name for f in findings] == ["test_does_not_write"]
    assert findings[0].line == 6
    assert "calls == []" in findings[0].snippet


def test_one_positive_assertion_clears_it():
    """A single positive is the evidence the code ran, which gives the absence its meaning."""
    source = VACUOUS.replace(
        "    assert calls == []",
        "    assert result.processed == 1\n    assert calls == []",
    ).replace("    do_the_thing()", "    result = do_the_thing()")

    assert _check(source) == []
    assert len(_check(VACUOUS)) == 1, "control: the unmodified fixture is flagged"


def test_the_opt_out_requires_a_stated_reason():
    with_reason = VACUOUS.replace(
        "    assert calls == []",
        "    # absence-ok: the point is that nothing is written\n    assert calls == []",
    )
    bare = VACUOUS.replace("    assert calls == []", "    # absence-ok:\n    assert calls == []")
    whitespace_only = VACUOUS.replace(
        "    assert calls == []", "    # absence-ok:   \n    assert calls == []"
    )

    assert _check(with_reason) == []
    assert [f.line for f in _check(bare)] == [7], "a bare pragma must not silence the check"
    assert [f.line for f in _check(whitespace_only)] == [7]


def test_the_opt_out_on_the_same_line_counts():
    inline = VACUOUS.replace(
        "    assert calls == []", "    assert calls == []  # absence-ok: writes are forbidden here"
    )

    assert _check(inline) == []


def test_the_opt_out_two_lines_above_does_not_count():
    distant = VACUOUS.replace(
        "    do_the_thing()", "    # absence-ok: too far away\n    do_the_thing()"
    )

    assert [f.test_name for f in _check(distant)] == ["test_does_not_write"]


def test_an_empty_list_that_is_never_appended_to_is_not_a_collector():
    """`x = []` alone is often an empty fixture. The fixture asserts on `seeded`
    itself, which is what makes this discriminate the `.append` requirement."""
    source = """
def test_something():
    seeded = []
    do_work()
    assert seeded == []
"""
    appended = source.replace("    do_work()", "    seeded.append(1)\n    do_work()")

    assert _check(source) == []
    assert [f.test_name for f in _check(appended)] == ["test_something"]


@pytest.mark.parametrize(
    "assertion",
    ["assert calls == []", "assert not calls", "assert len(calls) == 0", "assert calls == list()"],
)
def test_recognises_each_spelling_of_empty(assertion: str):
    findings = _check(VACUOUS.replace("    assert calls == []", f"    {assertion}"))

    assert [f.test_name for f in findings] == ["test_does_not_write"]


def test_calling_the_unit_under_test_inside_the_assertion_is_not_flagged():
    """`assert detect(...) is None` proves execution in the assertion itself."""
    source = """
def test_no_clustering_detected():
    records = []
    records.append(build_record())
    assert detect_clustering(STUDENT, records, NOW) is None
"""
    assert _check(source) == []


def test_non_test_functions_are_ignored():
    helper = VACUOUS.replace("def test_does_not_write", "def helper_does_not_write")

    assert _check(helper) == []


def test_async_tests_and_methods_are_scanned():
    source = """
class TestWriter:
    async def test_async_no_write(self):
        calls = []
        hook(lambda event: calls.append(event))
        assert not calls
"""
    assert [(f.test_name, f.line) for f in _check(source)] == [("test_async_no_write", 6)]


def test_known_gap_a_bound_append_passed_uncalled_is_not_a_collector():
    """Parity with the original: only an `x.append(...)` *call* marks a collector,
    so `hook(calls.append)` is not seen. Kept narrow deliberately; widening it is a
    rule change, not a port."""
    called = VACUOUS
    uncalled = VACUOUS.replace("lambda *a: calls.append(a)", "calls.append")

    assert [f.test_name for f in _check(called)] == ["test_does_not_write"]
    assert _check(uncalled) == []


def test_findings_carry_the_path_they_were_found_in():
    findings = checker.check_source(VACUOUS, Path("some/dir/test_x.py"))

    assert findings[0].path == Path("some/dir/test_x.py")


def test_a_file_that_does_not_parse_raises_rather_than_reading_as_clean():
    with pytest.raises(checker.UnparseableSource, match="bad_test.py"):
        checker.check_source("def broken(:\n", Path("bad_test.py"))


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_scan_walks_test_files_and_counts_them(tmp_path: Path):
    _write(tmp_path, "pkg/test_bad.py", VACUOUS)
    _write(tmp_path, "pkg/test_good.py", "def test_ok():\n    assert 1 == 1\n")
    _write(tmp_path, "pkg/helper.py", VACUOUS)
    _write(tmp_path, ".venv/lib/test_vendored.py", VACUOUS)

    result = checker.scan([tmp_path])

    assert result.files_scanned == 2
    assert [(f.path.name, f.test_name) for f in result.findings] == [
        ("test_bad.py", "test_does_not_write")
    ]
    assert result.clean is False


def test_scan_reads_an_explicitly_named_file_whatever_its_name(tmp_path: Path):
    helper = _write(tmp_path, "helper.py", VACUOUS)

    result = checker.scan([helper])

    assert result.files_scanned == 1
    assert [f.test_name for f in result.findings] == ["test_does_not_write"]


def test_scan_reports_parse_errors_and_missing_paths(tmp_path: Path):
    _write(tmp_path, "test_broken.py", "def broken(:\n")
    _write(tmp_path, "test_fine.py", "def test_ok():\n    assert True\n")

    broken = checker.scan([tmp_path])
    missing = checker.scan([tmp_path / "nope"])

    assert broken.files_scanned == 2
    assert [Path(e["path"]).name for e in broken.parse_errors] == ["test_broken.py"]
    assert broken.as_dict()["ok"] is False
    assert missing.missing == [str(tmp_path / "nope")]
    assert missing.files_scanned == 0


def test_scan_clean_tree_is_clean_with_a_nonzero_file_count(tmp_path: Path):
    _write(tmp_path, "test_fine.py", "def test_ok():\n    assert True\n")

    result = checker.scan([tmp_path])

    assert result.clean is True
    assert result.files_scanned == 1
    assert result.as_dict()["ok"] is True


def test_this_packages_own_tests_have_no_vacuous_spy_assertions():
    """The live gate, dogfooded."""
    tests_dir = Path(__file__).resolve().parent

    result = checker.scan([tests_dir])

    assert result.files_scanned >= 5
    assert result.findings == [], "\n".join(
        f"{f.path}:{f.line} {f.test_name}: {f.snippet}" for f in result.findings
    )
    assert result.parse_errors == []
