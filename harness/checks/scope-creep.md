---
name: scope-creep
description: Flags hunks that do not serve the change's stated purpose, such as drive-by refactors, unrelated renames, formatting churn or stray dependency bumps.
severity-default: medium
---

Flag changes in this diff that do not serve its purpose. Bundled
"while I'm here" edits make a diff harder to review and harder to revert,
and they hide behaviour changes inside noise.

First, establish what the diff is for:

- If the reviewer instructions name a story, a ticket or a list of in-scope
  paths, that is the scope.
- Otherwise, infer the dominant change from the diff itself. That is the set
  of hunks that together implement one behaviour, plus their tests.
- State the scope you used in the first finding's summary, so a reader can
  tell whether you inferred it.

Then flag a hunk when any of these is true:

- It refactors, renames or moves code that the dominant change does not
  call and is not called by.
- It reformats lines it does not otherwise change, such as whitespace,
  quote style or import order, in a file the change barely touches.
- It bumps, adds or removes a dependency that the dominant change does not
  import.
- It changes configuration, CI, lint rules or tooling for no reason the rest
  of the diff needs.
- It deletes or disables existing tests or checks that the dominant change
  does not replace.
- It edits files listed as out of scope, when the reviewer instructions give
  an out-of-scope list.

Do not flag these:

- mechanical follow-through the change forces, such as updating callers of
  a renamed function the change needed to rename;
- tests for the dominant change;
- docs that describe the dominant change;
- lockfile updates that match a dependency change the code uses.

Give one finding per unrelated hunk or file, with its line range. Write the
summary as "unrelated to <scope>: <what it does>; split into its own
change". Use `high` when the unrelated hunk changes runtime behaviour, or
deletes or disables a test or check. Use `low` for formatting-only churn.
