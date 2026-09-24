---
name: tpy-ready
description: Merge-readiness gate to run before /prep-merge. Confirms the branch is complete, documented, tracked, and that you'd still build it this way -- then hands off to /prep-merge. Assumes /tpy-review already ran; does not re-run the deep review.
---

# /tpy-ready

A readiness + retrospective gate to run before `/prep-merge`.

This is **not** a code review -- `/tpy-review` hunts defects, `/verify` runs the app, `/prep-merge` does the squash. `/tpy-ready` answers a different question: *is this work actually done, tracked, documented, and would you still build it this way?* Keep it honest and crisp -- when the branch is ready, say so in one line; do not manufacture blockers (spirit over letter).

## Arguments

Optional base ref. Defaults to `master` (same grammar as `/tpy-review`).

- `/tpy-ready` -- check the current branch vs `master` (including uncommitted changes)
- `/tpy-ready HEAD~3` -- check the last 3 commits
- `/tpy-ready origin/master` -- check against the remote master

## Context

Current branch: !`git branch --show-current`
Argument: $ARGUMENTS
Status: !`git status --short`
Commits vs master: !`git log master..HEAD --oneline 2>/dev/null || echo "(none)"`
Diff stat vs master: !`git diff master --stat 2>/dev/null || echo "(none)"`

## Steps

### 1. Resolve scope

If `$ARGUMENTS` is non-empty use it; otherwise use `master`. Resolve to a stable SHA via `git rev-parse <ref>`. The check covers committed + uncommitted state (`git diff <BASE>` without a second ref). If there are no changes vs base, say so and stop.

Read the diff and summarize in 1-2 lines what the branch does -- this frames every check below.

### 2. Ready to merge? (completeness)

Scan the *added* lines of the diff for merge-blockers:

- Leftover WIP: `TODO` / `FIXME` / `XXX` / `HACK` introduced in this diff, `breakpoint()`, stray debug prints, commented-out code.
- Stubs / half-finished work introduced here: `pass`-only bodies, `raise NotImplementedError`, `...` placeholders.
- Behavior changes without a test: cross-check against `tests/cases/` additions. Depth is `/tpy-review`'s test-coverage job -- here just confirm *something* covers each behavior change.
- Pre-existing test sources edited: `git diff <BASE> -- 'tests/cases/*/src/main.py'` restricted to cases that existed at `<BASE>`. List every hunk on a subject line; each is restore-or-justify. "Simplified to keep a gate green" is a blocker, not a justification.
- Snapshot churn labelled: when `expected/` files changed for pre-existing cases, the branch summary bins the hunks by shape (improvement / neutral / regression in generated-code terms) with one example each; a change presented as "the approved class" whose hunks are mostly collateral is a blocker until the bins are honest.
- Tests green: do **not** run `pytest` yourself by default. Ask whether the suite was run and is green; if unknown, list it as an open item. Run `uv run pytest` only if the user asks.

### 3. Docs current?

Per CLAUDE.md, docs ship in the same commit as the behavior. Flag warranted-but-missing updates:

- `docs/LANGUAGE_FEATURES.md` -- feature status, new behavior, type-mapping table.
- `docs/STDLIB_ROADMAP.md` -- stdlib module changes.
- `docs/ARCHITECTURE.md` -- type-system / sema-structure changes.
- `BUGS.md` -- discovered defects entered; fixed bugs' entries removed/updated; right section used.
- `TODO.md` -- no DONE items left behind; deferred work captured.
- `README.md` -- CLI changes.

### 4. Anything else for this PR? (loose ends)

Compare the branch's *intent* (the conversation / commit messages) against what actually landed:

- Things discussed or promised but not done.
- Edge cases named but not handled.
- Adjacent code left in a worse state by this change.
- Pre-existing bugs discovered but not filed in `BUGS.md`.

### 5. Outstanding reviewer items?

- If `/tpy-review` has not been run on this branch, recommend running it before merge -- this gate assumes it has.
- For findings already triaged: confirm every "handle now" item was actually fixed, and every "file and defer" item actually landed in `BUGS.md` / `TODO.md`.
- Any unresolved manual review comments.
- **Unreviewed tail -- judged, not forced.** Take the HEAD the last review round reviewed (its report names it) and list the branch's own work since then: `git log --first-parent --no-merges <sha>..HEAD` plus each master merge's resolution (`git show --remerge-diff <merge>`); master's commits are not tail. A round never covers its own output, so its fixes are always in the tail. Report the range in one line and decide by content:
  - tests, docs, scripts, snapshot regeneration, or review fixes the round itself specified -> no further round.
  - small mechanical compiler edits (a rename, a moved helper, a one-line guard) -> no further round; name them.
  - substantial new logic in `tpyc/`, `lib/tpy/` or `runtime/` no round has seen -> one more `/tpy-review` round over the tail only (unless the rounds so far are not converging: then stop per `/tpy-review`'s convergence rule).

### 6. Retrospective -- would you build it this way again?

Honest self-critique. For each non-trivial design choice in the diff, ask: knowing what you know now, would you do it the same way? Classify anything you'd change:

- **Back out & redo now** -- the approach is wrong or fragile enough that merging incurs real debt; cheaper to fix before the squash.
- **File a followup** -- defensible now, improvable later. `TODO.md` (gap) or `BUGS.md` (latent defect).
- **Accept** -- you'd genuinely do it the same way.

**Hybrid depth (mirrors `/tpy-review`'s meta-review trigger):** do this yourself by default. For large or design-heavy changes -- more than ~10 files, OR touching `docs/*_DESIGN.md` / `docs/ARCHITECTURE.md` / core sema-codegen structure -- ALSO dispatch ONE fresh-context `general-purpose` Agent with the diffstat + your branch summary + your draft retrospective (NOT the raw diff). Prompt it to challenge: which "accept" calls look like rationalizations, which "back out" calls are over-cautious. Fold its dissent in with a one-line reason next to any item it flips. State the trigger in one line ("Retrospective second-opinion: triggered (15 files)" / "skipped (routine)").

### 7. Verdict

A crisp readiness call -- no manufactured issues. The first word is the verdict, and it is one call: READY or NOT READY; technicalities go underneath as notes, never into the headline. Omit empty sections.

```
# /tpy-ready: <READY | NOT READY>

<what now works: 1-3 bullets, each a 1-3 line Python example and the key generated C++ line>
<diff --stat one-liner>

## Blockers (fix before merge) -- <N>
- <item> (file:line if specific)

## File as followup -- <N>
- <item> -> BUGS.md | TODO.md

## Followup work -- <N>
- <short point>

## Retrospective
- <thing you'd change> -> back out & redo now | followup | accept
```

**Followup work** is the forward-looking picture this branch leaves behind, in order of importance (most important first). Short points, one line each. Draw from what you found in steps 2-6 and the branch's intent:

- Gaps in the feature as shipped -- known-incomplete edges, deferred cases, partial coverage.
- Bugs found that limit the feature -- defects (this branch's or pre-existing) that cap what it can do; these should also be in `BUGS.md`.
- Things this feature unblocks -- adjacent work that was waiting on it and is now feasible.

Distinct from "File as followup" above (which is the bookkeeping list of items to *write into* `BUGS.md`/`TODO.md`): this section is the prioritized *narrative* of where the work goes next. Omit it only if there genuinely is no followup.

- **READY:** end with "Ready -- run `/prep-merge` when you want to squash." Do NOT auto-run `/prep-merge` -- it's a separate explicit step with its own suspicious-file gate.
- **NOT READY:** list blockers. Offer to fix the "fix now" ones and file the followups (on approval). Any commits follow the branch-aware policy in CLAUDE.md (auto-commit on a temporary working branch; ask on master/main).
- If the branch is clean, the report is the verdict line plus the what-now-works bullets: `# /tpy-ready: READY -- nothing outstanding.`

## Important

- This is a readiness gate, not a deep review. Do NOT re-run `/tpy-review`'s specialist fan-out -- reference its results instead.
- Do NOT run `pytest` or regenerate snapshots unless the user asks.
- Do NOT commit, squash, or push as part of the check -- squashing is `/prep-merge` and the user's call.
- Be honest. A clean branch gets a one-line READY. Don't invent work to look thorough.
