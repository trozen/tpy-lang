---
name: tpy-fix-bug
description: Structured procedure for fixing tpyc compiler bugs. Starts with an impact assessment to choose between a quick fix and a full architectural analysis. Forces the analysis to be presented to the user before any code change beyond trivial fixes. Invoke at the start of any bug-fix work -- a user-reported defect, a BUGS.md entry, a failing test, a discovered crash.
disable-model-invocation: true
---

# tpy-fix-bug

The tpyc compiler is tightly coupled; narrow patches at the symptom site
routinely leave the underlying invariant violation in place and produce
new symptoms in adjacent features. Default to careful analysis before any
code change.

## Arguments

Free-text identifier of the bug to address. Accepted forms:

- `upstream #N` -- upstream issue number tagged in a BUGS.md entry
  (e.g. `upstream #11`).
- `BUGS.md:LINE` -- line reference into BUGS.md (e.g. `BUGS.md:110`).
- A short BUGS.md substring -- a distinctive phrase from the entry's
  first line (e.g. `enum.Enum @native`, `Ptr[T] | None`).
- `file:line` -- the crash / failure site
  (e.g. `tpyc/codegen_cpp/expressions.py:3462`).
- A reproducer path -- `/tmp/agents/.../repro.py` or similar.
- Free text describing the bug -- when none of the above fit (e.g. a
  fresh user report not yet in BUGS.md).

Also accepted: positional references that resolve against the immediate
prior conversation turn (e.g. `the first one`, `the HIGH small one`).
Only resolve these against the *current* turn's listing -- do not guess
from older context.

**Before Phase 0**, resolve the argument to a specific bug:

1. If the argument names a BUGS.md entry (any of the first three forms,
   or a positional reference into a list you just produced), Read the
   relevant lines and quote the entry's first line back to the user in
   one sentence. Then proceed.
2. If the argument is a `file:line`, reproducer path, or free-text
   description with no clear BUGS.md match, restate what you understand
   the bug to be in one sentence and proceed. If BUGS.md likely already
   tracks it, grep for the symptom first and reference the entry.
3. If no argument is given, the bug must be unambiguous from the prior
   conversation. If it is not, ask the user to identify it before
   starting Phase 0.

Do not skip the restatement -- it is the cheap round-trip that catches
"wrong bug" before the analysis is wasted.

## Phase 0: Impact assessment

Before anything else, classify the bug and state the classification:

- **Trivial:** typo in a string literal, off-by-one in a comment, wrong
  identifier in an error message, missing import. The fix is mechanical
  and the failure mode is contained to the line in question.
  -> Skip to the fix; state briefly what you're changing and why.

- **Localized:** real defect, but narrowly scoped (single function, single
  phase) and you can plausibly enumerate every site where the same code
  path runs.
  -> Do Phases 1-3, skip the cross-feature survey, present the analysis,
  wait for sign-off.

- **Architectural:** the bug touches a shared invariant, a duplicated
  representation, a phase-ordering issue, or a feature whose design has
  tension with others. Anything in `sema/`, `codegen_cpp/`, or `typesys.py`
  that crosses multiple concepts is usually here.
  -> Full procedure (Phases 1-7); consider spawning an `Explore` sub-agent
  for the cross-feature survey.

If unsure, default to architectural. The user may up- or downgrade the
classification before you proceed.

## Phase 1: Reproduce

Concrete failing case in `/tmp/agents/`. Capture the exact error message
and the `file:line` where the failure surfaces. If you can't reproduce,
stop -- the report may be inaccurate or stale.

## Phase 2: Trace information flow

The crash site is rarely where the bug lives. Trace how data reaches the
symptom:
- What's in scope at the failure point?
- What was set/unset by which prior phase?
- Which invariants does the failure site assume hold but don't?
- Does the failure depend on the path taken to get here?

The bug usually lives at the prior site that was supposed to set up the
invariant -- not where the violated invariant first gets noticed.

## Phase 3: Root cause

Articulate the architectural invariant being violated -- not "the line
that crashed." Examples of well-formed root-cause statements:

- "`current_ns` is None when reached during MIL hoist, so all
  binding-based dispatch in `_gen_field_access` falls through silently."
- "`OptionalType(PtrType(T))` and `PtrType(T)` are byte-identical in C++
  but distinct in TPy, so every boundary needs its own coercion rule."
- "Readonly-method return-const projects `const` onto `OptionalType`
  even when the return is a fresh pointer, asymmetric with `PtrType`."

If you can't state the root cause in one sentence at the architectural
level, you haven't finished Phase 2.

## Phase 4: Survey related features (architectural bugs only)

The same root cause typically produces multiple symptoms. Find them
before fixing.

Project-aware heuristics:

- **Type-shape siblings:** bug in `OptionalType`? Also check `UnionType`,
  `PtrType`, `BoxType`, `OwnType`. Bug in `Span`? Also `list`, `Array`,
  `tuple`. Bug in `dict`? Also `set`, `frozenset`.
- **Callable siblings:** bug in functions? Also methods, constructors,
  staticmethods, lambdas, async functions, generators.
- **Wrapper-construct siblings:** bug in generators? Also async (shared
  resumable-frame codegen) and context managers (similar
  `__enter__`/`__exit__` shape).
- **Phase siblings:** bug in sema? Check parser feeds it correctly and
  codegen consumes it correctly. Where else does this concept appear
  across phases?
- **Predicate / invariant search:** grep for the broken predicate or the
  assumed-but-violated invariant. Every site that asks the same question
  is a potential symptom.
- **Test corpus search:** `grep -r` for similar patterns in
  `tests/cases/` to see what's already exercised vs. what's an unmapped
  edge.

For wide blast radius, spawn an `Explore` sub-agent with a focused
survey prompt: "given this root cause, which sites in the codebase share
the same invariant assumption? Report file:line + the pattern." A
fresh-context survey often catches patterns the main thread misses
because of momentum.

## Phase 5: Sketch the architectural fix

The change that fixes the root cause. Note:

- **Blast radius:** which files, which phases.
- **Real risk:** behavior changes that affect runtime semantics, ABI
  shifts, performance regressions on hot paths, edge cases that need new
  test coverage.
- **Whether the fix unifies a duplicated concept** or removes a special
  case -- that's a sign the surface area is shrinking, which is good.

Do **not** list snapshot churn (regenerated `.hpp`/`.cpp` for cases whose
generated code legitimately changes) as a cost. It's mechanical
follow-through; the `--update-snapshots` workflow exists exactly so this
is cheap. If many snapshots will regenerate, mention it factually
("~50 snapshots will regenerate") without framing it as a downside.

## Phase 6: Patch alternative (only if needed)

The narrow change that hides the symptom. Only when the architectural
fix is genuinely too costly for the current context.

If proposing a patch, state explicitly:

- What new bug surface it introduces.
- Which BUGS.md entry tracks the architectural follow-up.
- Why the patch is acceptable now.

A patch without a tracked follow-up is invisible tech debt. A patch that
*reduces* the architectural fix's blast radius later (e.g. by adding a
defensive check that the architectural fix can keep) is acceptable; a
patch that *complicates* the architectural fix later (e.g. by adding a
parallel code path the proper fix has to remove) is not.

## Phase 7: Present and wait

Write the analysis (compressed for localized bugs) and present it.
**Do not start coding.** The user picks the path:

- (a) architectural fix
- (b) patch + tracked follow-up in BUGS.md
- (c) different direction -- e.g. redesign the feature itself if the
  architectural fix is awkward in revealing ways
- (d) more analysis needed before deciding

When the user signals "just fix it" or applies time pressure, push back
once: ask whether they want the patch (with architectural fix tracked)
or the real fix. The cost of one round-trip is much lower than the cost
of a wrong fix that surfaces later as new bugs in different features.

Sometimes the architectural fix is invasive in awkward ways. That is
itself a signal: the feature may not fit the existing design well. Flag
this as a design-level question -- do not reflexively force-fit with a
patch.

## Throughout: track new issues uncovered

Bug-fix work routinely surfaces adjacent defects -- a related symptom
the chosen fix doesn't cover, an unrelated crash in the same code path,
a sema rule that turns out to be wrong, a stale comment that misled
your analysis. Record each one rather than silently leaving it.

Sources to watch:

- **Phase 1 reproduction:** alternate reproducers that fail in
  *different* ways (different error, different phase, different code
  path) -- usually a separate bug, not the same one.
- **Phase 2 trace:** invariant violations in adjacent code that aren't
  on the path to the reported symptom but would crash on the next
  user input that touches them.
- **Phase 4 cross-feature survey:** symptoms of the same root cause
  that the chosen fix legitimately won't cover (e.g. when the user
  picks the patch path, or when the architectural fix is scoped to
  one binding kind out of several).
- **Implementation:** new failures the fix exposes -- existing tests
  that start failing for a *different* reason than what the fix
  addresses, or fresh crashes the unblocked code path now reaches.

For each new issue:

1. Check `BUGS.md`; if not already tracked, add a new entry there
   (defects belong in `BUGS.md`, not `TODO.md` -- see CLAUDE.md).
2. If the issue blocks the current fix from being correct, surface it
   to the user immediately -- do not paper over it.
3. If the issue is independent, note it in the final report so the
   user can decide whether to address it now or later.

Do not file follow-ups only in your end-of-turn summary -- that's
invisible after the conversation ends.
