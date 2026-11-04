---
name: missing-tests-in-diff
description: Flags behaviour changes and bug fixes in this diff that ship without a test in the same diff that would fail if the change were reverted.
severity-default: high
---

Flag production behaviour that this diff adds or changes with no test in the
same diff to pin it. Tests ship in the same change as the code, not in the
next one. A bug fix ships with a regression test that would have caught the
bug.

Work through the diff in four steps.

1. Sort the changed files into production code, test code, and other. Other
   covers docs, comments, generated files, lockfiles, and pure data or
   config with no logic. Test code means files under a test directory, or
   files named like `test_*`, `*_test.*`, `*.test.*` or `*.spec.*`, plus
   test-only fixtures.
2. List each behaviour change in the production code. That means a new or
   changed branch, return value, error path, validation, permission check,
   query, or external call.
3. For each one, look in the test code of the **same diff** for a test that
   exercises it, and that would fail if that hunk were reverted.
4. Flag each behaviour change that has no such test.

Watch for these patterns in particular:

- The diff touches only production files, but the change is a fix. Signs of
  a fix are a commit-style comment such as "fix", "handle", "guard" or
  "edge case", a new `if` around an existing path, or a changed comparison
  operator. A fix with no regression test is `high`.
- Tests were added, but only for the happy path, while the diff also adds an
  error path or a permission check. Flag the untested path.
- An existing test was edited to match new behaviour, and no new assertion
  pins what changed. The test now agrees with the code by construction.
- New code was added to a module with no test file anywhere in the diff.
  Name the test file the change would naturally belong in.

Do not flag these:

- refactors that preserve behaviour and change no signatures;
- logging-only changes;
- type annotations that do not change behaviour;
- deletions of dead code;
- changes confined to "other" files.

If you cannot tell whether a hunk changes behaviour, say so in the summary
and use `low`.

Place each finding on the production lines that are not covered. Name the
behaviour, and propose the test in one clause: its name, and what it
asserts.
