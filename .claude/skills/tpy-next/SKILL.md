---
name: tpy-next
description: Autonomously select and fully handle the next backlog item -- pick a high-value, bounded entry from BUGS.md / TODO.md / open triage rows, run the /tpy-fix-bug or /tpy-add-feature procedure, implement, pass /tpy-review, and deliver a merge-ready squash branch via /tpy-ready + /prep-merge. Invoke in a fresh session when the user wants backlog burned down without steering. One item per invocation.
---

# tpy-next

End-to-end autonomous handling of one backlog item. The user launches
this in a fresh session and comes back to either (a) a merge-ready
branch, or (b) a ready-to-approve analysis for something that needed a
human decision. Both are successful outcomes; (b) is not failure.

**Autonomy grant -- read this first.** Invoking this skill is the
user's standing approval for work that passes the Phase 2 eligibility
gate: trivial/localized classification, High confidence, CPython-parity
clean, bounded snapshot churn. It is NOT approval for design decisions,
new language semantics, new user-facing warnings/escape hatches, or
architectural changes -- those still stop and present, exactly as the
underlying skills require. The final merge into master always stays
with the user. CLAUDE.md's "never make design decisions autonomously"
continues to bind; this skill only pre-approves the corner where no
design decision exists.

## Arguments

Optional hint, otherwise fully autonomous selection:

- a source (`bugs`, `todo`, `triage`) to restrict scanning
- a substring of a specific entry (`heapq @nocopy`, `posonly`) to skip
  selection and handle that item
- `--dry-run`: do Phase 1 only; print the selection report and stop

## Phase 1: Select

Scan, in order: `BUGS.md` (both sections), the open rows of the newest
`docs/*triage*.md` status table, `TODO.md`. Build a candidate list and
score:

- **User impact** (highest first): miscompile / UB / unsound-safety >
  silent CPython divergence > crash / rejects-valid on idiomatic code >
  ergonomics / diagnostics quality > perf > slop.
- **Boundedness:** BUGS.md size tags are the primary signal -- `small`
  entries with a reproducer and a stated "Fix shape:" are ideal.
  Entries whose fix shape names concrete functions beat entries that
  gesture at a subsystem.
- **Autonomy confidence:** an existing pattern/helper to extend, no
  open semantic questions, blast radius enumerable from the entry.

**Hard exclusions** -- never select, regardless of score:

- entries marked `/tpy-add-feature`-shaped, "design pass", "design
  decision", "warrants a dedicated", "Open question", or gated on
  THIR/MIR/cpp_shape
- anything requiring a NEW user-facing warning or escape-hatch spelling
  (that is a language-surface decision)
- entries the user annotated as theirs / in-progress
- topics matching an existing branch name (`git branch --list` plus
  worktree branches) -- another session may own it; skip, do not steal

**Selection report (always printed before any work):** the chosen item
quoted, its score rationale in 2-3 bullets, and the top 2-3 runners-up
with one-line reasons for passing them over. If NOTHING qualifies, say
so, list the top candidates that need a human decision and why, and
stop -- do not lower the bar to have something to do.

## Phase 2: Analyze, with the eligibility gate

Run the full `/tpy-fix-bug` procedure (or `/tpy-add-feature` if the
item is a feature gap -- the skills' own routing gates decide, follow
them). Everything applies: impact assessment, reproduce, root cause,
precedent check, CPython-parity agent, confidence rating, test plan.

The sub-skill's "present and wait" gate is replaced by this
**eligibility check**. Proceed without waiting iff ALL hold:

1. Classification is **trivial or localized**.
2. Confidence is **High** (per the sub-skill's scale -- a Medium with a
   cheap probe means run the probe first, then re-rate; do not proceed
   on Medium).
3. CPython-parity verdict is **match**, **skipped-because-rejects-valid
   / internal**, or an **already-shipped acknowledged divergence**
   (existing warning + escape hatch). A divergence needing NEW
   acknowledgment surface fails the gate.
4. Expected snapshot churn is limited to the item's own new/changed
   cases plus mechanical churn the analysis enumerates.
5. The fix-the-class rule does not expand scope beyond localized.

If any condition fails: STOP. Present the analysis exactly as the
sub-skill specifies, state which condition failed, and end the run --
the analysis is the session's deliverable. Do not shave the item down
to a patch just to pass the gate; the sub-skills' patch rules
(tracked follow-up etc.) still apply and a patch chosen to dodge the
gate is a misuse of the grant.

## Phase 3: Implement

Create the working branch off master (the sub-skill's branch proposal
applies). Implement per the analysis, honoring the sub-skill's
definition-of-done checklist: reproducer flip with before/after, test
plan landed (reproducer + inverse + siblings), targeted `-k` runs
during development, one full `uv run pytest` at the end, BUGS.md /
TODO.md bookkeeping (delete-when-fixed), area-doc grep for falsified
claims. Auto-commit checkpoints per CLAUDE.md's temporary-branch
policy.

## Phase 4: Review loop

Run `/tpy-review`. Apply every Critical and every cheap Warning; file
deferred Warnings per the review's recommendation. Re-run the suite.
At most ONE more `/tpy-review` round if the first round's fixes were
substantive (changed compiler code, not just tests/docs).

**Bail-out ladder** -- stop implementing and switch to handoff mode
when ANY occurs:

- a review finding or implementation discovery reclassifies the work
  as architectural, or reveals a needed design decision
- the suite cannot be made green within two fix iterations
- scope balloons past the analysis (files or snapshot churn well beyond
  the estimate)
- the same test flips between fixes (a sign of two interacting bugs)

Handoff mode: commit WIP on the working branch with a clear message,
write the final report explaining exactly where it stopped, what is
known, what decision or analysis is needed -- and end. A bounded
retreat with a clean handoff beats an unbounded yak-shave.

## Phase 5: Finish

1. If master advanced since branching: `git merge master` into the
   working branch (merge, NEVER rebase), resolve, re-run the full
   suite. If conflicts are non-mechanical (semantic overlap with the
   advanced master), treat as a bail-out.
2. Run `/tpy-ready`. Handle its "handle now" items; file its
   followups. If it reports NOT READY for non-mechanical reasons,
   bail out with the report.
3. Run `/prep-merge` to produce the squash branch.
4. **Stop there. Never merge into master, never push, never delete
   branches.**

**Final report** (the session's last message): item handled and the
selection rationale, before/after of the reproducer/example, suite
status, review rounds and what they changed, the squash branch name
ready for merge, everything filed in BUGS.md/TODO.md along the way,
and total wall-clock. For bail-outs: the handoff note instead.

## Important

- ONE item per invocation. Do not chain into a second item, even if
  the first was quick -- selection quality degrades with a tired
  context, and the user reviews one branch at a time. (Repetition is
  the user's call, e.g. via /loop.)
- This skill is heavy on session budget (review fan-out + full suites).
  If a usage/session limit interrupts mid-run, the working branch and
  its checkpoints are the recovery point: a later invocation with the
  item substring resumes from the committed state.
- All constraints of the underlying skills and CLAUDE.md apply
  unchanged except the single present-and-wait substitution defined in
  Phase 2. When in doubt whether something needs the user: it does.
