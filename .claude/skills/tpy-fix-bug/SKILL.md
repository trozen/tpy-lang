---
name: tpy-fix-bug
description: Structured procedure for fixing tpyc compiler bugs. Starts with an impact assessment to choose between a quick fix and a full architectural analysis. Forces the analysis to be presented to the user before any code change beyond trivial fixes. Invoke at the start of any bug-fix work -- a user-reported defect, a BUGS.md entry, a failing test, a discovered crash.
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

**Missing feature, not violated invariant.** Sometimes the root cause is
that the behavior was never designed -- the code path doesn't exist, not
that an existing invariant is broken. That's a different problem: bug-fix
mode assumes a correct design exists to restore, but here there is none
to restore. When the root cause reads as "this capability is
unimplemented" rather than "this invariant is violated," stop here. Do
not continue into Phases 4-7 -- designing a feature without the design
gate is exactly what `/tpy-add-feature` exists to prevent. Surface the
finding to the user and recommend re-entering via `/tpy-add-feature`,
carrying forward the reproducer (Phase 1) and the root-cause statement
(this phase) so the design pass starts from what you've already learned.

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

**Precedent check first -- ALL classifications, including localized.**
Before writing any new mechanism, grep for existing code that already
solves the same sub-problem: an invalidation helper, a conversion
routine, a predicate, an emit path, a tracker. The compiler is old
enough that most sub-problems have a prior solution; a fix that
hand-rolls a parallel version of one creates a mirror that silently
drifts (the recurring slop class: duplicated predicates between sema
and codegen, parallel sweep logic in two trackers). The sketch must
either name the existing helper it reuses/extends, or state why none
fits. "I didn't look" is not a reason.

**Fix the class, not the instance.** If Phase 4 found sibling
manifestations of the same root cause, the default recommendation is
the fix that covers the whole class -- a chokepoint helper every site
routes through beats per-site patches. Recommending the
single-instance fix requires (a) a stated reason the general fix is
disproportionate now, and (b) BUGS.md entries for every sibling left
unfixed.

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

### CPython-parity assessment

When the fix changes what *accepted* code does (not merely what is
accepted or rejected), dispatch ONE `cpython-parity` agent with the
Phase 1 reproducer and the proposed post-fix semantics. Ask it to state
CPython's observable behavior for the same source and a verdict per
divergence class:

- **reference-vs-copy / aliasing and mutation visibility** (fields,
  container elements, tuples, unions, captures, match bindings, walrus,
  loop variables) -- historically the largest divergence source
- **evaluation order and side-effect count** (args including kwargs in
  written order; assert messages only on failure; operands once)
- **exception semantics** (catchable exception vs panic; finally /
  `__exit__` ordering and coverage on all exit paths; chaining)
- **scoping and binding lifetime** (loop vars rebind the enclosing
  scope; closure capture semantics; del)
- **numeric and string semantics** (arbitrary precision vs fixed width;
  division; byte-vs-codepoint indexing)
- **equality / identity / truthiness** (cross-type `==`, `is`,
  container truthiness)
- **accepted-then-ignored syntax** (Python syntax that parses but loses
  meaning must error or warn -- never silently drop)

The verdict gates the design:

- **Match** -- observable behavior equals CPython's. Done.
- **Acknowledged divergence** -- unavoidable under TPy's model. The
  design MUST then include all three: (a) a compile-time diagnostic at
  the divergence site, (b) an explicit user spelling that acknowledges
  or avoids it and silences the diagnostic (the escape hatch -- the
  model is the field-assignment copy warning silenced by `copy()`), and
  (c) a `docs/LANGUAGE_FEATURES.md` note. A divergence missing any of
  the three is an incomplete design, not a smaller one.
- **Silent divergence** -- never an acceptable outcome; rework the
  design or surface it to the user as an explicit decision to make.

When an acknowledged divergence needs an escape hatch, reuse the
existing acknowledgment spellings (`copy()`, `.clone()`, an explicit
annotation, a directive) before minting a new one -- the
acknowledgment surface should stay as coherent as the type surface.

Skip the agent only when the fix is purely rejects-valid (changes
diagnostics, not behavior) or internal (refactor, perf) -- and say so
in the report.

### Plan the regression tests

Sketch the test plan with the fix, not after approval:

- **Reproducer test** -- the Phase 1 case as a snapshot test, named by
  its observed post-fix mode (`panic_` for a now-checked runtime
  failure, `error_` for a now-rejected program, `warn_`-style
  annotations for a new diagnostic).
- **Inverse test -- required, not optional.** A happy-path case proving
  the fix does not over-trigger: the nearest valid code that must keep
  compiling cleanly / keep its facts / keep its old output. Soundness
  fixes are judged as much by what they leave alone as by what they
  catch.
- **Sibling tests** for every class member the fix covers (one per
  construct family is enough; the fix-the-class rule above decides the
  set).
- Reference-type cases follow CLAUDE.md's mutate-after-the-boundary
  rule -- a read-only test is parity-blind and can hide a silent copy.

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
**Lead with the bug shown concretely, not described abstractly.**
Open the report with a minimal code example the user can read at a
glance: the reproducer snippet, plus what it does now (the wrong
output, or the error + where it surfaces) versus what it should do.
Follow it with a one-sentence summary naming the symptom and the
affected feature. The user must be able to grasp *what is broken*
from the example and confirm scope ("yes, that's the bug") in
seconds, before reading any analysis. The example shows the visible
symptom, not the root cause.

**Keep the analysis itself terse.** Convey the most important info
in short bullets; the user will ask follow-up questions for
anything they want to dig into. Aim for the whole report to be
readable in under a minute. Cover (each in one line or two):

- Classification (trivial / localized / architectural)
- Root cause (architectural-level, one sentence)
- Proposed fix (which files / phases, shape of the change)
- Precedent (the existing helper/pattern the fix reuses or extends --
  or "new mechanism, because <reason>")
- Tests (one line: reproducer + inverse + sibling cases from Phase 5's
  plan; existing snapshots that will change, so the approval covers
  regeneration)
- Confidence (see the scale below)
- CPython parity (one line: match / acknowledged divergence with its
  warning + escape hatch / skipped-because-rejects-valid)
- Patch alternative (only if Phase 6 produced one)
- Adjacent issues surfaced during analysis (brief)
- Proposed branch (see below)

**Confidence scale.** Rate the recommended approach and say what caps
it -- the rating is about the *fix design*, not the bug's existence:

- **High** -- root cause reproduced and verified in code; sibling
  survey done (or N/A); no unverified assumption left that could
  change the fix's shape.
- **Medium** -- root cause solid but at least one material assumption
  is unverified (name it: an uninspected consumer, an untested sibling,
  an interaction taken on faith from docs/comments).
- **Low** -- diagnosis rests on inference or conflicting evidence, or
  the blast radius is unexplored.

For Medium and Low, also name the cheapest probe that would raise the
rating (a snippet to compile, a file to read, a test to run). A Low
rating on an architectural fix means the default recommendation is the
more-analysis path, not the fix.

Beyond that leading reproducer, do NOT paste large code excerpts,
internal trace dumps, or full file lists. The small illustrative
example up front is required; the Phase 2 trace and file dumps are
not -- the user will ask if they want that depth.

**Proposed branch line.** Check `git branch --show-current` and pick:

- **On `master`**: propose a new branch off master with a short
  kebab-case name derived from the bug (e.g. `fix-readonly-deref`,
  `fix-optional-ptr-coercion`).
- **On an unrelated branch** (different scope from the approved
  fix): propose a new branch off master with a fitting name.
- **On a fitting branch** (already a sensible home for this fix,
  e.g. finishing in-progress work on the same area): say "current
  branch `<name>` is a fitting home -- staying put" and skip the
  new branch.

Don't propose deleting or cleaning up the old branch -- leave that
to the user.

**Do not start coding. Propose; don't poll.** Lead with your
recommendation: the fix you would design from scratch -- the cleanest
long-term shape, usually the architectural fix for a real root cause,
unless the analysis showed a strong case against it. State it clearly
enough that the user can just confirm; don't dress it up as option (a)
of a menu when there's nothing else live. Include the proposed branch
(if any) as part of the recommendation -- one confirmation covers both.

Add alternatives ONLY when they are genuinely present in the analysis:

- a **patch + tracked BUGS.md follow-up**, when the architectural fix is
  too costly for the current context;
- a **redesign-the-feature** path, when the architectural fix is awkward
  in revealing ways (the feature may not fit the existing design) -- hand
  off to `/tpy-add-feature` for the design pass rather than force-fitting
  a fix;
- a **more-analysis** path, when something material is genuinely
  unresolved.

For many bugs there is only one sensible path. In that case, propose it
and ask for a yes -- not "pick (a), (b), or (c)."

When the user signals "just fix it" or applies time pressure, push back
once: ask whether they want the patch (with architectural fix tracked)
or the real fix. The cost of one round-trip is much lower than the cost
of a wrong fix that surfaces later as new bugs in different features.

Sometimes the architectural fix is invasive in awkward ways. That is
itself a signal: the feature may not fit the existing design well. Flag
this as a design-level question -- do not reflexively force-fit with a
patch.

Once the user confirms a coding path (the recommendation or a coding
alternative), create the proposed branch (if any) with
`git checkout -b <new-branch> master` and start implementing -- one
approval covers both the fix direction and the branch. Do not push,
force, or use `-D`.

**Definition of done.** The fix is complete when ALL of these hold --
the final report states each:

- The Phase 1 reproducer re-run shows the designed post-fix behavior;
  the report includes before/after.
- The Phase 5 test plan landed (reproducer + inverse + siblings) and
  the full suite is green (targeted `-k` runs during development; one
  full `uv run pytest` at the end).
- `BUGS.md`: the entry this fix closes is REMOVED (not struck
  through); residuals and unfixed siblings are filed.
- The area's docs are grepped for claims the fix falsifies (the
  relevant `docs/*_DESIGN.md`, `LANGUAGE_FEATURES.md`, safety docs) and
  corrected in the same commit -- behavior changes routinely outlive
  stale doc claims about the old behavior.
- Snapshot changes to existing tests match what the approval
  enumerated; anything beyond it goes back to the user before
  `update_snapshots.py`.

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
