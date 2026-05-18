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

### 8. Optional: meta-review the recommendation

Draft the Recommendation in step 7 but DO NOT print it yet -- decide here whether to run a meta-review first, then print the (possibly folded) section.

For routine reviews -- few findings, no Critical, no design-doc changes -- skip and print as drafted. For larger or design-heavy reviews, dispatch a meta-reviewer that challenges the recommendation list before the user sees it.

Trigger meta-review when ANY of these holds:

- More than ~10 findings across all severities
- One or more Critical findings
- The diff includes design docs (`docs/*_DESIGN.md`, `docs/ARCHITECTURE.md`, `docs/IR_DESIGN.md`)

State the trigger reason in one short line ("Meta-review: triggered (Critical present)" or "Meta-review: skipped (routine)"). Note: routine compiler work routinely touches `tpyc/sema/`, `tpyc/codegen_cpp/`, and dispatches several specialists, so don't gate on those alone -- gate on signal in the findings themselves.

When triggered, dispatch ONE fresh-context `general-purpose` Agent with the *aggregated report + drafted recommendation list + diffstat (`git diff <BASE> --stat`) + the file-bucket classification from step 2*. Do NOT pass the raw diff. The diffstat and file list let the agent spot-check severity claims and "hallucinated issue" suspicions without re-discovering scope.

Prompt it to flag:

- over-zealous suggestions disproportionate to actual impact
- recommendations that contradict project conventions in `CLAUDE.md`, `docs/LANGUAGE_FEATURES.md`, `BUGS.md`, or `TODO.md`
- hallucinated issues -- claims that refer to code or behavior that doesn't actually exist
- severity/bucket mismatches (e.g. a Critical that's really a Suggestion, or vice versa)

Output: the same recommendation list, with each item annotated `[meta: keep]`, `[meta: downgrade -> <bucket> because <reason>]`, or `[meta: drop because <reason>]`.

Fold the verdicts into the printed Recommendation. Keep the meta-reviewer's one-line reason next to any downgraded or dropped item so the user sees the dissent rather than a silent edit.

### 9. Ask the user

After printing the Recommendation section, ask which subset to act on. Mention the default options inline (one line each):

- **"go ahead"** / "proceed" / "yes" -- implement every "Handle now" bullet AND file every "File and defer" bullet into `BUGS.md` (defects) or `TODO.md` (gaps) per its bucket. "Track or skip" items are dropped per the recommendation.
- **named subset** -- the user names which bullets to skip, add, or re-bucket; act on that modified set.
- **"no"** / "stop" -- end the skill; the user handles followup manually.

If only "Track or skip" items remain (no Handle now, no File and defer), no prompt is needed -- end with a one-liner ("nothing to act on; safe to commit") and stop.

### 10. Execute on approval

Only after explicit approval:

- Apply each "Handle now" item in order. If an item turns out to need real design analysis or root-cause work (not a mechanical fix), STOP and surface to the user -- recommend invoking `/tpy-fix-bug` or `/tpy-add-feature` for that item rather than improvising.
- File each "File and defer" item into `BUGS.md` or `TODO.md` using the existing structure of each file (one-line summary, short context, `file:line` where relevant). Don't double-file.
- Run targeted `uv run pytest -k <pattern>` for cases plausibly affected as you make changes. After all items are applied, run `uv run pytest` once to confirm nothing else regressed.
- If a fix changes expected output for *existing* test snapshots, consult the user before running `update_snapshots.py` (per CLAUDE.md's snapshot policy).

Do NOT commit, stage, or push -- the user reviews your changes before commit per CLAUDE.md.

## Important

- During the review phase (steps 1-9), do NOT run `uv run pytest`, regenerate snapshots, or make code changes. Specialists are forbidden from these and so are you. The developer runs the suite before requesting review; flag concerns rather than verifying via pytest.
- Step 10 (post-approval execution) is the ONLY phase where code changes and pytest are allowed, and only on the user-approved subset.
- Do NOT commit, stage, push, or otherwise modify the user's git state -- not during review, not during execution.
- Do NOT regenerate snapshots unless an approved "Handle now" item explicitly calls for it, and ask first.
