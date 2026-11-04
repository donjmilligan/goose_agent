# Harness personas

These are goose agent files. Each one is markdown with `name` and
`description` frontmatter. Goose finds them in `.agents/agents/`,
`.goose/agents/`, `.claude/agents/` or `~/.agents/agents/`, and you run one
with `delegate(source: "<name>")` through the `summon` extension. The file
body becomes the delegate's instructions.

A delegate cannot delegate in turn. The orchestrator, meaning you or a
recipe, runs the chain one persona at a time, or up to 5 in parallel (the
goose cap).

The review checks for `goose review` are in `../checks/`.

## Roster

BMAD chain:

- **analyst**: idea -> `docs/brief.md`. Frames the problem; designs nothing.
- **pm**: brief -> `docs/prd.md`, with testable `FR-n` and `NFR-n`
  requirements and a first cut of epics.
- **architect**: PRD -> `docs/architecture.md`, plus
  `docs/decisions/YYYY-MM-DD-<hash>-<slug>.md`. The hash comes from
  `coord__gen_hash`. Rulings are written as PROPOSED, with the options laid
  out.
- **po**: checks that the PRD and the architecture agree, then shards them
  into `docs/epics/<n>-<slug>.md` and `docs/stories/<epic>.<n>-<slug>.md`
  (Draft).
- **sm**: takes ONE Draft story to Ready. That means adding context files,
  contracts, ACs, a test plan with the mutation for each test, gates, an
  out-of-scope list and stop conditions.
- **dev**: builds ONE Ready story in its own worktree (`coord__worktree_create`).
  Tests go in the same commit, and the dev mutation-checks them, runs the
  gates and pushes the branch. The story ends in Review. There is no PR and
  no merge.
- **qa**: adversarial review of the story branch. QA re-runs a mutation, runs
  `coord__verify_assertions`, checks every AC, and gives a PASS, CONCERNS or
  FAIL verdict. QA never fixes anything.

Specialists, which you can run at any point:

- **regression**: maps the blast radius of a diff, runs the targeted tests,
  and reports gaps with proposed tests.
- **devsecops**: security review of a diff or path. Reports findings only.
- **drift-identifier**: read-only audit of docs against code against
  instruction files, with a scored report.

## Flow

```
  idea / ticket / thread
          |
          v
      [analyst] ==> docs/brief.md
          |
          v
        [pm] =====> docs/prd.md
          |
          v
    [architect] ==> docs/architecture.md + docs/decisions/*.md
          |
          v
        [po] =====> docs/epics/<n>-*.md + docs/stories/<e>.<n>-*.md  (Draft)
          |
          |   one story at a time; up to 5 stories in flight
          v
        [sm] =====> story: Draft -> Ready
          |
          v
        [dev] ====> <repo>.wt/<slug>, branch story/<e>.<n>-<slug>, pushed
          |         story: Ready -> In Progress -> Review
          v
        [qa] =====> PASS | CONCERNS | FAIL
          |   \
          |    +==> FAIL: back to [dev] with the findings
          v
       (human) ===> merge, task to Done

  side lane, any time:  [regression]  [devsecops]  [drift-identifier]
```

Any persona can stop with NEEDS_CONTEXT and a set of options instead of a
pick. The chain waits for a human ruling, then re-runs that persona.

## When to delegate which

- A new idea with no brief: start at **analyst**.
- A brief exists but there is no PRD: **pm**.
- A PRD exists but there is no architecture: **architect**.
- A PRD and an architecture exist but there are no stories: **po**.
- A Draft story you want built next: **sm**, then **dev**, then **qa**.
- A small, already-specified fix: skip the planning personas. Write or ask
  for one story, then run **sm**, **dev** and **qa**.
- After a fix, refactor or dependency upgrade: **regression**.
- When a diff touches authentication, secrets, input parsing, infrastructure,
  hooks or agent permissions: **devsecops**.
- Before a milestone, or when the docs are not to be trusted: **drift-identifier**.

## Rules every persona carries

- It ends with exactly one status: DONE, DONE_WITH_CONCERNS, BLOCKED or
  NEEDS_CONTEXT. DONE is never "merged", and only a human moves a task to
  Done.
- Evidence is something the reader can re-check, such as `path:line`, a
  command's output or a test result. A green test only counts once it has
  been seen to go red. An absence needs a positive control.
- On a ruling or an ambiguity, it writes two or three options, each with a
  worked example, and does not pick.
- It calls MCP tools only by their contract names (`coord__*`, `clickup__*`,
  `slack__*`). `tests/` checks that every name is in the contract.

## Install notes (for install.sh)

These are confirmed against goose source at `71dd9a9aa`.

- Agent files are read with `O_NOFOLLOW`
  (`crates/goose/src/skills/supporting_files.rs:306`). That means a
  **per-file symlink is not read**. Symlink the directory instead, for
  example `~/.agents/agents -> harness/agents`, because the directory itself
  is canonicalized (`summon.rs:214`).
- Goose scans every `*.md` in an agents directory, this README included
  (`summon.rs:227`). The README has no frontmatter, so goose skips it
  silently. `tests/` checks that it stays that way.
- Global review checks are read from `~/.config/goose/checks` and
  `~/.config/agents/checks` (`checks/mod.rs:297`). They are not read from
  `~/.agents/checks`. Per-file symlinks are skipped there too
  (`checks/mod.rs:431`, `is_file()` on the entry), so link the directory.
