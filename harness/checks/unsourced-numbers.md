---
name: unsourced-numbers
description: Flags added thresholds, weights and windows that have no source or population, and absence claims made without a positive control.
severity-default: medium
---

Flag numbers and absence claims in this diff that a reader cannot trace to
a source. This check has two rules.

## Rule 1: a number carries its source and its population

A **decision number** is a threshold, cutoff, weight, score band, window,
retry budget, rate limit, timeout, sample size or percentage that encodes a
judgment rather than a fact of the platform. When the diff adds or changes
one, the code next to it should say three things:

- **Source**: the person, document, study, spec or measurement it came from.
- **Population**: who or what the source measured, and whether that matches
  where this code runs. For example, "measured on desktop users in 2019; we
  serve mobile" or "vendor benchmark on their hardware".
- Or, in place of both, an honest `UNKNOWN`. For example:
  `Population: UNKNOWN; origin unrecorded, treated as unsourced.` That passes
  this check. An unknown that is stated honestly is better than a plausible
  citation.

Flag a decision number in any of these cases:

- It has no comment at all.
- It cites a source but gives no population or setting. Examples are
  "industry standard", "best practice" and "per research".
- It cites a framework or a study for a number that framework does not
  state. Do this only when the diff itself shows the mismatch, for example
  a comment saying "Cohen's d large = 0.5".

Do not flag these:

- `0`, `1` and `-1`, indices, and sizes of fixed structures;
- unit conversions such as `60`, `1000` and `1024`;
- protocol- or spec-defined values named by their spec, such as HTTP
  statuses or port numbers given with their RFC;
- test-fixture literals;
- values read from configuration whose default is documented elsewhere in
  the diff.

## Rule 2: an absence is proven, not asserted

Flag added comments, docs, commit-style notes and test descriptions that
assert something does not exist, when they give no positive control.
Examples are "nothing calls this", "unused", "no framework governs this",
"not implemented anywhere" and "safe to delete, no references".

A positive control is the same search, run the same way, finding something
that does exist. For example:

```
grep -rn 'old_name' src/   -> 0 hits
grep -rn 'new_name' src/   -> 4 hits   <- control
```

Without the control, the absence cannot be told apart from a typo, a wrong
path, or a search that never ran.

Do not flag an absence claim that sits beside its control, or one that
links to a report containing both searches.

Write each summary as "Rule 1: <constant> has no source/population" or
"Rule 2: absence claim without positive control". Add the one line that
would fix it. Use `high` when the number gates a security, safety, money or
permission decision. Use `medium` otherwise.
