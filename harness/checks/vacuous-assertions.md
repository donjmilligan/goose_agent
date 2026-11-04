---
name: vacuous-assertions
description: Flags added test assertions that would still pass if the feature under test were deleted.
severity-default: high
---

Flag test assertions added or changed in this diff that cannot fail for the
reason the test claims to check. These tests look like evidence and are not,
which makes them worse than having no test.

Apply one diagnostic to every changed test: **what would this test do if the
feature were deleted?** If the answer is "still pass", it is not a test of
that feature. For comparisons, ask the sharper version: can the value under
test change in any direction and the test still pass?

There are three shapes to look for.

1. **An absence-only conclusion.** Examples: `assert x not in y`,
   `assert calls == []`, `assert result is None`, `expect(...).toHaveCount(0)`,
   or "the field is absent". All of these hold when the code under test never
   ran, because breakage also produces an absence. Flag the assertion when the
   same test has no paired presence assertion proving that the path executed.
   A presence assertion is one like "the call happened once with these
   arguments" or "the response contains the other field". Do not flag a line
   that carries `# absence-ok: <reason>` or `// absence-ok: <reason>` with a
   non-empty reason.
2. **A check on the half that cannot vary.** The assertion pins something
   that never drifts, while the thing that can drift goes unchecked.
   Examples:
   - asserting an id or a type when the bug would be in the value;
   - asserting a mock was called with the very argument the test itself just
     passed in;
   - asserting what the test fixture set up, rather than what the code
     produced;
   - a test that reads state from the test runner, not from the app.
3. **Two sides where one is computed from the other.** This is
   self-consistency that holds by construction. Examples:
   - the expected value is computed by calling the function under test, or by
     the same helper it uses;
   - `assert set(a) | set(whole - a) == set(whole)`;
   - comparing a value against a copy of itself after a round-trip that never
     touches the code under test.
   The fix is to pin the literal value a human decided on, not the value the
   code derived.

Also flag a changed test whose assertions are all inside a branch or loop
that the fixture might never enter. A loop over an empty list asserts
nothing.

Do not flag these:

- an absence assertion with its presence pair in the same test;
- snapshot or golden-file tests whose expected file is in the diff;
- assertions in test helpers that are not themselves tests.

For each finding, name the shape (1, 2 or 3 above, or "unreachable") and
write one sentence on what would make the assertion able to fail. Use
`high` for an assertion that is the only check on an acceptance criterion
or a security or permission boundary. Use `medium` otherwise.
