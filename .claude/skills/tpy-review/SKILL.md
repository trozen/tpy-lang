---
name: tpy-review
description: Run a parallel multi-agent review of TurboPython compiler changes. Dispatches specialist reviewers (codegen, architecture, tests, safety, runtime, docs) in parallel and aggregates findings into a unified report.
disable-model-invocation: true
---

# /tpy-review

Run a parallel multi-agent review of changes on the current branch.

## Arguments

Optional base ref. Defaults to `master`.

- `/tpy-review` -- review current branch vs `master` (including uncommitted changes)
- `/tpy-review HEAD~3` -- review last 3 commits
- `/tpy-review origin/master` -- review against the remote master

## Context

Current branch: !`git branch --show-current`
Argument: $ARGUMENTS
Status: !`git status --short`

## Steps

### 1. Resolve base ref

If `$ARGUMENTS` is non-empty, use it; otherwise use `master`. Resolve to a stable SHA via `git rev-parse <ref>` and use the SHA for all downstream commands so the review stays consistent even if branches move.

If there are uncommitted changes, the review covers both committed and uncommitted state (`git diff <BASE>` without a second ref).

### 2. Classify changed files

Run `git diff <BASE> --name-only` and bucket each path:

- **codegen-output**: `tests/cases/*/expected/{src,include}/main.{cpp,hpp}`
- **codegen-logic**: `tpyc/codegen_cpp/**/*.py`
- **compiler-py**: any `tpyc/**/*.py`
- **tests**: `tests/cases/**`
- **runtime-cpp**: `runtime/cpp/**`
- **docs**: `docs/**`, `BUGS.md`, `TODO.md`, `README.md`, `CLAUDE.md`
- **other**: anything else

A file can fall into multiple buckets (e.g. a generated `.cpp` is both `codegen-output` and `tests`).

### 3. Decide which specialists to dispatch

Skip a specialist when its scope is empty:

| Specialist | Dispatch when |
|---|---|
| `codegen-correctness` | `codegen-output` OR `codegen-logic` non-empty |
| `architecture-fit` | `compiler-py` non-empty |
| `test-coverage` | always (cheap; catches missing tests for compiler changes) |
| `safety-model` | any of `tpyc/sema/`, `tpyc/typesys.py`, `tpyc/coercions.py`, `tpyc/codegen_cpp/`, or `runtime/cpp/` touched |
| `runtime-cpp-correctness` | `runtime-cpp` non-empty |
| `docs-sync` | always (cheap) |

### 4. Fan out specialists IN PARALLEL

Send **one message with multiple Agent tool calls** -- one per dispatched specialist. Do NOT serialize.

Each prompt should include:
- The base SHA resolved in step 1
- The pre-classified file list relevant to that specialist (so the agent doesn't waste tokens re-discovering)
- A reminder to output in the specialist's documented format

Example prompt skeleton:

```
You are reviewing changes from <SHA> to HEAD.
Base ref: <SHA> (<original-name>)
Files in your scope:
  <list of files from classification>

Follow your documented process and output format. Do NOT run pytest or update_snapshots.py.
```

### 5. Aggregate

Once all specialists return, synthesize a unified report. Be terse -- one bullet per finding, a single short sentence. NO code excerpts, NO "Fix:" sub-bullets. The user will ask for details on any finding they want to drill into; surfacing every detail up front is noise.

- **Dedupe**: when multiple specialists flag the same `file:line` with related issues, merge into one entry crediting all contributing specialists.
- **Group by severity**: Critical first, then Warning, then Suggestion.
- **Filter noise**: if Suggestions total > 10 across all specialists, drop the lowest-signal ones (vague, duplicative of a Warning at the same location).
- **Note clean specialists**: list them explicitly so the user sees what was checked.
- **Minimize code references**: include `file:line` only when the user needs it to find the issue. Generic findings don't need a line reference.

### 6. Present the unified report

Severity carries the action. Make it explicit in the section headers so the user can triage at a glance:

- **Must fix** (Critical) -- blocker; resolve before commit.
- **Should fix** (Warning) -- address before commit if cheap; otherwise file a follow-up in `BUGS.md` (defect) or `TODO.md` (gap) so it doesn't get lost.
- **Track or skip** (Suggestion) -- not a defect. File in `TODO.md` if worth remembering; else skip.

```
# /tpy-review report

Base: <SHA> | <diff --stat one-liner>
Dispatched: <list> | Clean: <list>

## Must fix (Critical, <N>)
- short description [<specialist>] (file:line if specific)

## Should fix (Warning, <N>)
- short description [<specialist>]

## Track or skip (Suggestion, <N>)
- short description [<specialist>]
```

Omit empty severity sections. If everything is clean: `# /tpy-review report: all dispatched specialists clean`.

### 7. Recommend a sign-off plan

After the report, add a short **Recommendation** section: a single flat bullet list the user can scan and approve in one pass, without re-reading the findings above. This is the skill's takeaway -- the user shouldn't have to synthesize the report themselves.

Each bullet: one action + one short reason. No file:line refs here (they're in the report above), no per-specialist attribution, no severity tags. Order by what should happen first.

Cover three buckets, but only as bullets -- do not use sub-headers:
- **Handle now**: every Critical, plus any Warning cheap enough to fix before commit.
- **File and defer**: Warnings worth tracking in `BUGS.md` / `TODO.md` but not blocking; Suggestions worth remembering.
- **Skip**: Suggestions not worth tracking (say so explicitly so the user knows they were considered).

Cap at ~8 bullets; if more, the report itself is too noisy -- collapse related items.

Example:

```
## Recommendation

- Fix the `Optional[T]` borrow-form mismatch before commit -- miscompiles existing code.
- Add a regression test for the empty-tuple case alongside the fix.
- File the `@readonly_propagate` doc gap in TODO.md -- not blocking, but easy to forget.
- Skip the naming nits -- bikeshed, not worth a follow-up.
```

If everything is clean or only trivial suggestions remain, a one-liner is fine: `## Recommendation: nothing to do; safe to commit.`

## Important

- Do NOT run `uv run pytest` -- specialists are forbidden from this and so are you. The developer runs the suite before requesting review; flag concerns rather than verifying via pytest.
- Do NOT regenerate snapshots (`tests/update_snapshots.py`).
- Do NOT make code changes -- this is a review. Surface findings only.
- Do NOT commit, stage, or modify the user's git state.
