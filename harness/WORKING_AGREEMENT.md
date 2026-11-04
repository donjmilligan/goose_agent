# Agent harness: working agreement

This loads into every goose session through the global hints file. It holds
across projects. A project's own `AGENTS.md` or `.goosehints` adds to it, and
wins where the two conflict.

## 1. Two kinds of work

**Latent work** is model work: judgment, pattern matching, prose, ambiguous
inputs. Every run costs tokens and can come out different. You cannot inspect
how it reached an answer.

**Deterministic work** is code: same input, same output. You pay once to write
it, nothing per run, and you can test it.

- **The rule:** if asking the same question twice must give the same correct
  answer, it is deterministic. Do not do it in a model reply. Write or call
  the script. Arithmetic, date maths, hashing, file lookups, JSON and CSV
  transforms, regexes and structured API calls never happen "in your head".
- **The meta-loop:** the model writes the script, and from then on the script
  constrains the model. A failure in latent space becomes a tested behaviour
  in deterministic space, and the old way of failing can no longer happen.
- For every feature, fix or investigation, ask first whether it is latent or
  deterministic. If it is both, split it: the deterministic part becomes a
  script plus tests, and the latent part becomes a prompt plus a check.
- The harness already does this for its own conventions. Random ids come from
  `coord__gen_hash`, claim signals from `coord__claim_signals`, and worktrees
  from `coord__worktree_create`. Absence-only assertions are flagged by
  `harness-coord verify-assertions`. Use these tools rather than improvising
  what they do.

## 2. The context window is the lever

The context window is your only control over the model. Load the spec, the
contract, the files that matter and concrete examples, and leave the rest
out. Vague or bloated context gives vague or bloated output. When a task goes
wrong, the first question is "what was in the window?", not "was the model
dumb?" Curate the context before you prompt, and give each delegate exactly
the context it needs.

## 3. Evidence discipline

- Back every factual claim with something a reader can re-check: a file path
  with a line number, a command and its output, a test run. "I believe" is
  not evidence.
- Check the live artifact, not a summary of it. A memory, an earlier message
  or a plan that says "X exists in Y" is a pointer, so read Y before you act
  on it.
- Verify adversarially. Ask "how would this fail?", not "does this look
  right?"
- A grep count tells you how many lines matched, not what they say, so read
  the matches. A SHA, task id or path quoted from memory is unverified until
  you read it back.
- Report failures by class:
  - auth or access: give the exact error and stop
  - schema drift: rediscover, adapt, and report the drift
  - empty result: say whether there is no data or the query was wrong

  Never return a confident wrong answer, and diagnose before you retry. The
  same call with the same inputs fails the same way.

### A green test is not evidence until you have seen it go red

Before you cite a test as proof that a change works, break the change on
purpose and watch a test fail for the right reason. Then restore it. If
nothing fails, the test does not test that behaviour, so say that instead of
reporting the green run. The diagnostic question is: **what would this test
do if the feature were deleted?**

Three shapes cause almost every vacuous test:

1. **The conclusion is an absence.** For example `assert x not in y`,
   `calls == []`, or a count of 0. These pass when the code never ran,
   because absence is also what breakage produces. Pair each one with a
   positive assertion that proves the path ran. If an absence check really
   is all you need, mark it `# absence-ok: <reason>`.
2. **The check measures the half that cannot vary.** It reads an id that
   never drifts instead of the name that does, or what the test runner
   imported instead of what the app declares. Or it runs against a stale
   server, a missing `.env`, or a worktree with no hooks. The command passes
   and describes the wrong thing.
3. **One side of the assertion is computed from the other.** For example
   `A | (All - A) == All`. That holds by construction and cannot fail. Fix it
   by pinning what a human decided, not what the code derived from it.

### When a mutation changes nothing, say which of three things happened

| What happened | Response |
|---|---|
| The assertion cannot fail, whatever the input | Fix the **assertion** (shapes 1-3) |
| The assertion is sound, but no fixture reaches the mutated branch | Add the **fixture** |
| The mutation really does preserve behaviour | Say so, and say why the distinction matters |

Reporting "the mutation escaped" without naming the case is itself an
unverified claim.

### An asserted absence needs a positive control

"Nothing implements this" and "this does not exist" need a second search in
the same breath, one that does find something real:

```
grep -rn 'risk_ratio' src/           -> 0 hits
grep -rn 'impact_ratio' src/bias/    -> 3 hits   <- control
```

Without the control, the first line looks exactly like a typo, a wrong path,
a filter on the wrong language, or a grep that never ran. Write both lines
anywhere the absence matters: reports, decision records, code comments.

### A framework number carries its population

When a threshold, weight, window or constant comes from a published framework
or study, say whom the source measured, at the place the number lives. If that
population is not yours, say so there too:

```python
#: Population: <who the source measured>. <Is that ours? Say it plainly.>
#: Source: <author, work, year> - <what it actually states>.
CONSTANT = value
```

If you don't know where a number came from, write
`#: Population: UNKNOWN - treated as unsourced.` Don't hunt for a plausible
citation to attach.

## 4. Scope discipline

- Make the smallest change that solves the problem, and say what is out of
  scope.
- "While I'm here" changes are forbidden. Note what you found and put it on
  the task tracker. Don't bundle it into this change.
- **Lanes:** a lane is the set of directories a stream of work owns. Each
  project names its own, for example by service or by crate. Stay in your
  lane by default. Crossing into another lane, or into a shared path
  (contracts, CI, docs, agent configuration), is a deliberate act that you
  announce before you do it, never a side effect. If you need something from
  another lane that doesn't exist yet, record the need and build against a
  mock. Never stub the other side yourself.

## 5. Claim before work

Plan first. A plan is not a claim. Before you touch the board or open an
editor, check all three claim signals, even if the first one looks decisive.
Each one covers a window the others cannot see.

1. **The claim channel:** `slack__find_claims(channel, topic)` lists claims
   that have no completion yet. If `HARNESS_CLAIM_CHANNEL` is not set, say
   that you skipped this check. Don't pretend it passed.
2. **Remote branches:** a branch exists from the moment work starts, so it
   shows up while the board and the channel are still empty. This is the
   strongest signal.
3. **Worktrees:** another session may already be working locally.

`coord__claim_signals(topic, repo)` runs signals 2 and 3. Its output includes
the total remote branch count as a positive control. If a signal matches your
topic, the work is taken: pick something else or ask. Then post your claim
(what you're taking, and the branch it will land on), but only to a channel in
`HARNESS_SLACK_ALLOWED_CHANNELS`. A claim on a parent task does not cover a
follow-up you find inside it, so claim that separately.

Push your branch early (`git push -u origin <branch>`), right after the first
commit, so your claim shows up in signal 2.

## 6. One worktree per session, outside the repo

- Never work in the shared checkout. Create your worktree with
  `coord__worktree_create(slug, branch)`. It puts the worktree at
  `<repo>.wt/<slug>` off `origin/main` and refuses any path inside the repo.
  Nested worktrees break tooling and get committed by accident.
- Every edit, test run and commit happens inside your worktree, with
  `git -C <worktree>`.
- A fresh worktree does not carry installed dependencies or git hooks across.
  Bootstrap it before you trust a gate that runs there.
- `main` is integration-only:
  - never commit to it, push to it or merge into it
  - keep branches current with `git rebase origin/main`, never by merging
    main in
  - integration is a separate, serialized step that a human runs

## 7. Git and PR safety

- Open no PR unless the user asks for one in the current message. Finishing
  a task is not a request for a PR.
- Never merge. Never use `--no-verify`, `gh pr merge --admin`, or a force
  push. If a hook fails, fix the cause.
- Never commit secrets, `.env` files, keys or credentials. Never run
  `docker compose down` on a project you didn't start.
- Before any irreversible action:
  - state what it does and what it affects, and whether it can be undone
  - wait for confirmation
  - do the reversible steps first

## 8. Decision records

Write a record before any change that touches 3 or more files, introduces a
pattern, or chooses between real alternatives.

- **Path:** `docs/decisions/YYYY-MM-DD-<hash>-<slug>.md`.
- **Hash:** 4 lowercase alphanumeric characters from `coord__gen_hash`.
  **Never invent the hash yourself.** A model asked for "random" does not
  sample uniformly: three sessions once produced the same "random" value
  independently.
- **Template:**
  - Title
  - Date, Status (PROPOSED, ACCEPTED, IMPLEMENTED or SUPERSEDED), Scope,
    Author
  - Context
  - Options Considered, with pros and cons
  - Decision
  - Consequences
  - Implementation Notes
- **Records are append-only.** Never edit a record's decision, or delete a
  record. A reversal is a new record that says it reverses or supersedes the
  old one by filename. Corrections elsewhere say "Corrected <date>: this said
  X". Don't silently overwrite.
- Cite the record in your completion report.

## 9. Task tracker (ClickUp)

- Read the list's real statuses (`clickup__list_statuses`); don't assume
  column names.
- **Comment before every status change, with no exceptions.** Use
  `clickup__set_status(task_id, status, comment)`, which posts the comment
  and then changes the status. `clickup__update_task` cannot change status.
  The comment gives:
  - the decision
  - what you did against the plan, with evidence
  - anything still open
- **An agent's ceiling is review.** Agents never set Done or Closed. A human
  does that after QA. A DONE report is a claim that is ready for QA, not a
  Done ticket.
- Comment the plan when you start, the result (branch, commits, gate output)
  when you finish, and a handoff if you stop partway.
- Anything you are not fixing now goes on the task it belongs to. That
  includes observations, not only bugs. The chat channel is for claims and
  completions only.

## 10. Completion protocol

End every task with exactly one of these:

- **DONE:** every step is complete, every claim has evidence, the tests are in
  the diff, and the branch is pushed.
- **DONE_WITH_CONCERNS:** complete, with issues. List each issue with its
  severity and a proposed follow-up.
- **BLOCKED:** you cannot proceed. Say what blocks you and what you already
  tried.
- **NEEDS_CONTEXT:** information is missing. Say exactly what you need.

"Partially done" is not a status. Include the branch @ sha, the gate commands
and their counts, and the mutation you ran with what went red. You must be
able to explain why the change is correct and exactly where it would break.
A passing test is not understanding.

## 11. Confusion protocol

Stop when you hit high-stakes ambiguity:

- two plausible architectures
- a request that contradicts an existing pattern
- a destructive operation with unclear scope
- missing context that would change the approach

Name the ambiguity in one sentence. Give 2 or 3 options with their real
trade-offs, and ask. Do not guess on architecture. This does not apply to
routine coding.

## Before and after every task

**Before:**

- plan
- check the claim signals (section 5)
- post the claim
- open the task with a plan comment
- create the worktree (section 6)

**After:**

- commit on your branch and push it
- say what needs restarting to take effect
- comment the result on the task, and move it to review only if it is ready
- post the completion to the claim channel
- report the status (section 10)
