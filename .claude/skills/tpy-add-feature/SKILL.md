---
name: tpy-add-feature
description: Structured procedure for adding new TurboPython language features or extending existing ones. Starts with a scope assessment to choose between a quick implementation and a full design pass. Forces the design to be presented to the user before any code change beyond trivial. Invoke at the start of any feature work -- new language construct, new built-in, new stdlib module, or extension to an existing feature.
---

# tpy-add-feature

The tpyc compiler is tightly coupled across parse / sema / codegen, and
features that don't consider sibling concepts and existing patterns
produce duplication that's expensive to unwind later. Default to a
careful design pass before any code change.

## Arguments

Free-text description of the feature or extension. Forms:

- A new language construct or builtin (e.g. `"add match exhaustiveness check for enum subjects"`, `"support starred unpacking in tuple assignment"`)
- An extension to an existing feature (e.g. `"extend Optional narrowing to while conditions"`, `"allow @readonly on dataclass methods"`)
- A stdlib addition or extension (e.g. `"add re.split"`, `"extend collections.deque with rotate"`)
- An upstream issue reference (e.g. `upstream #19`)
- A TODO.md substring or line reference

**Before Phase 0**, restate what you understand the feature to be in one
sentence and proceed. If the request is ambiguous, ask the user to
identify it before starting Phase 0 -- the cost of a round-trip is much
lower than designing for the wrong feature.

**Defect, not feature.** If restating (or designing) reveals the
request is really a defect in an existing feature -- the capability
exists but misbehaves -- stop and recommend re-entering via
`/tpy-fix-bug`, carrying the minimal example and what you've learned.
Feature mode would design around the bug instead of fixing it. (The
inverse gate exists in `/tpy-fix-bug`'s Phase 3 for
missing-feature-not-violated-invariant.)

## Phase 0: Scope assessment

Before anything else, classify the work and state the classification:

- **Trivial:** doc-only change, new error message text, mechanical
  rename, adding a missing existing-pattern alias. The change fits one
  file and follows an established pattern verbatim.
  -> Skip to implementation; state briefly what you're changing and why.

- **Localized:** real new behavior, but narrowly scoped to a single
  sema rule, a single codegen path, or a stdlib function that follows
  an existing template. You can enumerate every touched site upfront.
  -> Do Phases 1, 3, 4, 6 (skip the sibling survey and risk check);
  present and wait for sign-off.

- **Architectural:** touches the type system, crosses parse/sema/codegen
  boundaries, introduces a new invariant other phases must respect, or
  has plausible interaction with sibling concepts (Optional <-> Union
  <-> Ptr, methods <-> functions <-> constructors, generators <-> async
  <-> context managers). Anything in `tpyc/typesys.py`, `tpyc/sema/`,
  `tpyc/thir/` that introduces a new kind of fact is usually
  here.
  -> Full procedure (Phases 1-6); consider spawning an `Explore`
  sub-agent for the sibling survey.

If unsure, default to architectural. The user may up- or downgrade the
classification before you proceed.

## Phase 1: Restate the feature

State what the feature does at the user level, concretely:

- **Minimal Python example** using the feature. Write it down -- don't
  hand-wave. The example is the contract.
- **Expected C++ emission** (rough sketch) for that example. You don't
  need exact codegen, but the shape (what types are produced, what
  helpers are called) should be clear.
- **Where this lands in `docs/LANGUAGE_FEATURES.md`**: new entry, or
  status change for an existing entry (Open -> Planned -> Working)?
- **Prior discussion**: search `TODO.md`, recent commits, design docs
  in `docs/`, and `BUGS.md` for related work. Reference any prior
  thinking so you don't redesign from scratch.

If you can't write the minimal Python example in 10 lines or fewer,
the feature scope may be too broad -- consider splitting.

## Phase 2: Survey siblings (architectural only)

The same design space typically interacts with sibling concepts. Check
before designing -- the right move is often to extend a pattern, not
duplicate it.

Project-aware heuristics:

- **Type-shape siblings:** feature on `Optional`? also check `Union`,
  `Ptr`, `Box`, `Own`. Feature on `list`? also `dict`, `set`, `tuple`,
  `Array`, `Span`. Feature on records? also enums, protocols.
- **Callable siblings:** feature on functions? also methods,
  constructors, staticmethods, lambdas, async functions, generators.
  Mirror should be symmetric across all of them.
- **Wrapper-construct siblings:** feature on generators? also async
  (shared resumable-frame codegen) and context managers (similar
  `__enter__`/`__exit__` shape).
- **Phase siblings:** feature touching sema? where does parser feed
  it and codegen consume it? Where else does this concept appear?
- **Prior patterns:** grep `tpyc/sema/` and `tpyc/thir/lower/` for
  similar existing features. Reusing an existing emit helper or sema
  rule is almost always preferable to a new parallel one.
- **Tests at risk:** which existing `tests/cases/` snapshots will
  change? List them in the Phase 6 report; approving the design
  approves regenerating them (CLAUDE.md snapshot policy).

For wide blast radius, spawn an `Explore` sub-agent with a focused
prompt: "given this feature design, which sites in the codebase share
the same invariant or extend the same pattern? Report file:line + the
pattern." A fresh-context survey catches patterns the main thread
misses due to momentum.

## Phase 3: Sketch the design

**Precedent check first -- ALL classifications, including localized.**
The localized classification *claims* the feature "follows an existing
template"; this is where that claim gets cashed. Grep for the existing
feature most similar in shape (same boundary, same type family, same
emit pattern) and read it. Name it in the design: the template being
followed, the helper being extended, or -- if genuinely nothing fits --
"new pattern, because <reason>". A localized feature whose author
can't point at its template is an architectural feature that skipped
the survey. This is the single cheapest defense against the recurring
slop class (parallel emit paths, re-derived predicates, near-identical
sema rules that drift apart).

Where each piece of the feature lives:

- **Parser** (`tpyc/parse/`) -- new AST node? new syntax form? new
  `# tpy:` directive? Usually parser changes are small and confined.
- **Sema** -- which sub-module (`statements.py` / `expressions.py` /
  `calls.py` / `methods.py` / `protocols.py` / `narrowing.py` /
  `value_range.py` / `flow_facts.py` / `match.py` / ...)? What new
  facts does sema decide (they reach the emitter on a THIR node, see
  Lowering / emit)? Mutation propagation?
- **Type system** (`tpyc/typesys.py`, `tpyc/coercions.py`,
  `tpyc/type_def_registry.py`) -- new TypeDef? new TypeRefNode shape?
  new coercion rule?
- **Lowering / emit** (`tpyc/thir/lower/`, `tpyc/thir/emit.py`) --
  which lowering arm? A new sema->codegen fact goes on a THIR node or
  the lowering context (CLAUDE.md "Compiler Front-end Performance");
  `tpyc/codegen_cpp/` only for the skeleton: headers, signatures,
  record/protocol/enum drivers.
- **Runtime** (`runtime/cpp/include/tpy/`) -- new header support? New
  template, new helper, new concept?
- **Stdlib** (`lib/tpy/`) -- new module or stub? Parallel
  `lib/cpy/tpy/` stub for CPython compat?

**State the invariant** the feature establishes that other phases must
respect. This is the load-bearing claim; getting it wrong is what
produces "fix for X that doesn't consider Y" bugs later. Examples of
well-formed invariants:

- "After Phase 2, every `OptionalType(P_ref)` carries a `pointer_repr`
  flag if `P` is a reference type."
- "The `@auto_readonly` clone produces a mutable and a const method
  entry with identical bodies and mirrored receiver const-ness; every
  call site dispatches on whichever fits its receiver."

If you can't state the invariant in one sentence, finish Phase 3
before continuing.

**Pitfalls walk and scope matrix -- ALL classifications above trivial.**
Walk `docs/PITFALLS.md` once and, for each entry that can apply to the
feature, say in one line how the design respects it (or why it cannot
apply). Then fill the scope matrix for the construct: positions (free
function, method, constructor, module-level statement, generator body,
async body, comprehension, closure, context-manager body, `try`/`finally`,
`@error_return` body, `match` arm) by shapes (scalar, tuple, `Optional`,
union, `str`/`bytes`, `Own[T]`, `readonly[T]`, `Ptr`/`Span`, `Box`/`Rc`)
and slot kinds (local, param, return, field, container element, global). Every applicable cell is marked covered-by-section,
not-applicable-because, or gap-filed-as; an empty cell is a finding the
reviewer will probe.
Module-level statements and globals rank last: their target design is
TODO.md "Module scope is the body of __tpy_init", so a global cell is
gap-filed against it rather than patched per position.

**One mechanism per concept.** If the construct is a variant of an
existing one (containers vs records, lambda vs nested def, constructor
vs function, property vs method call, enum vs record methods, generator
vs other frames), the change goes through the shared path and puts the
one real difference at the single site that consumes it. If the shared
path cannot take it within this unit, that is a finding to present, not
a reason to fork.

**Stop rule: a fix that adds an ordering dependence or a rule exception is
at the wrong layer.** Before sketching further, check the change against
what a user must learn to predict the behaviour. If the fix makes a verdict
depend on analysis or source ORDER (which binding came first, which arm is
analyzed last, whether a use sits before or after a loop back edge or a
closure), or adds an exception or special case to a rule the user sees
(a refusal that fires only when X was read earlier, an exemption for one
seed shape), the root cause is a fact decided at the wrong layer -- usually
something single-pass, flow-ordered analysis cannot know yet. Do not add
the patch. Name the missing fact and its home (a whole-function pass, the
Phase-2 fixpoint, a slot verdict, a MIR Place), present that unit as the
fix, and file the symptoms under it. The language rule a user reads should
stay one sentence; everything the implementation cannot yet honour goes in
a "current limitations" list pointing at that unit, never into the rule.

## Phase 4: Plan tests and docs

**Tests:**

- Happy path (one or more `tests/cases/<group>/<name>/`)
- Error cases (`error_<name>/`) for invalid input the compiler should
  reject -- with `# tpyc: error(/regex/)` annotations
- Panic cases (`panic_<name>/`) for runtime failures, if applicable
- Edge cases: empty / zero / boundary / None / generic-parameter shapes
- Adjacent-feature interactions surfaced in Phase 2
- One condensed case per feature, not one per shape: a section per
  covered matrix cell under a one-line comment naming the position, the
  `# tpyc:` annotation on the subject line, output lines prefixed with
  the section name. `error_` variants take the one most representative
  position (the compiler stops at the first error). The corpus is over
  5000 cases; each new case is a cost.
- CPython compatibility: does the feature have a `lib/cpy/tpy/`
  equivalent? Default to yes; only skip with `no_cpython.txt` if the
  feature truly depends on C++-only behavior (e.g. `@native` interop)

**Existing tests at risk:** which snapshots will change? Consult the
snapshot policy in `CLAUDE.md` -- they are listed in the Phase 6
report (count, kind, one C++ example), and approving the design
approves regenerating them.

**Docs:**

- `docs/LANGUAGE_FEATURES.md` -- always update (Working / Planned /
  Open status + surface description). CLAUDE.md is explicit that this
  should land in the same commit.
- `docs/TPY_FOR_AGENTS.md` -- only when the feature changes the
  user-facing surface that coding agents would need to know about.
- `docs/ARCHITECTURE.md` -- only when type-system or sema structure
  changes.
- `docs/STDLIB_ROADMAP.md` -- if a stdlib module is added or its
  coverage changes.
- Dedicated design doc (`docs/<FEATURE>_DESIGN.md`) -- only when the
  design has enough depth to warrant standalone treatment (async,
  protocol, readonly, etc. have these; most features do not).

## Phase 5: Risk check (architectural only)

Stop and consider before presenting:

- **New special case vs unification:** does the design *unify* existing
  patterns (e.g. removing a duplicated emit, merging two similar
  TypeDef cases) or *add* a new parallel pattern? Unification is
  strictly preferred; if you're adding a new special case, justify
  why an existing pattern can't be extended.
- **Sema/codegen mirror duplication:** does this introduce new emit
  paths that duplicate existing logic? Flag and consider extraction.
- **Performance:** any new loops over types/methods/signatures?
  Overload resolution, protocol conformance checking, and mutation
  propagation are the usual size-proportional-cliff offenders. Even
  if benchmarks on the current test corpus look fine, consider
  worst-case scaling.
- **Blast radius:** files, phases, snapshots. Quantify if possible
  ("~30 snapshots will regenerate, all expected").
- **Sliced scope:** if the design deliberately ships a subset of the
  sibling matrix (functions now, generators later; Optional now, Union
  later), the excluded cells are LISTED in the design and filed in
  `TODO.md` as part of the same approval -- "handled for X but not its
  sibling Y" is the recurring decay pattern, and unlisted exclusions
  read as oversights to the next audit.

### CPython-parity assessment

Run this for any feature whose surface is valid Python (i.e. CPython
also executes the Phase 1 example) -- which is most of them. Dispatch
ONE `cpython-parity` agent with the Phase 1 minimal example and the
designed semantics; ask for CPython's observable behavior on the same
source and a verdict per divergence class:

- **reference-vs-copy / aliasing and mutation visibility** (fields,
  container elements, tuples, unions, captures, match bindings, walrus,
  loop variables) -- historically the largest divergence source
- **evaluation order and side-effect count** (left-to-right including
  kwargs as written; conditional operands evaluated only when reached).
  Operand/argument ORDER is a postponed language decision
  (`BUGS.md#subexpression-right-to-left-eval`, TODO.md "Evaluation-order
  policy"): a new site is added to that entry and its render pinned.
  Side-effect COUNT is not postponed.
- **exception semantics** (catchable vs panic; finally / `__exit__`
  ordering and coverage; chaining)
- **scoping and binding lifetime** (loop-var rebinding, closure
  capture, del)
- **numeric and string semantics** (precision/overflow, division,
  byte-vs-codepoint)
- **equality / identity / truthiness**
- **accepted-then-ignored syntax** (parsed-but-meaning-dropped is never
  acceptable -- error or warn)

The verdict gates the design:

- **Match** -- done.
- **Acknowledged divergence** -- unavoidable under TPy's model; the
  design MUST include all three: (a) a compile-time diagnostic at the
  divergence site, (b) an explicit user spelling that acknowledges or
  avoids it and silences the diagnostic (the escape hatch -- the model
  is the field-assignment copy warning silenced by `copy()`), and (c)
  a `docs/LANGUAGE_FEATURES.md` note. Missing any of the three makes
  the design incomplete, not smaller.
- **Silent divergence** -- never an acceptable design outcome; rework
  or surface to the user as an explicit decision.

When an acknowledged divergence needs an escape hatch, reuse the
existing acknowledgment spellings (`copy()`, `.clone()`, an explicit
annotation, a directive) before minting a new one -- the
acknowledgment surface should stay as coherent as the type surface.

TPy-only surfaces (`@native` interop, `Ptr` operations, directives)
skip the agent -- say so in the report. The `lib/cpy/` stub plan from
Phase 4 is the *test-time* half of the same contract; this check is
the design-time half.

Sometimes the cleanest design touches things in awkward ways. That is
itself a signal: the feature may not fit the existing model well.
Flag this as a design-level question rather than force-fitting.

## Phase 6: Present and wait

Write the design (compressed for localized features) and present it.
**Lead with code, not with the compiler.** Before any design detail the
report shows, in this order:

1. **What will work** -- the Python shapes that become valid or change,
   each a short snippet with its behavior (output, or the diagnostic for
   what stays rejected), including the sibling shapes the design covers
   and the ones it leaves out.
2. **Generated C++** -- for the key shape(s), the few lines the feature
   emits, and before/after where existing code changes render.

Then one sentence summarizing the surface. The user must be able to
confirm "that's the feature, and that's how it should compile" from
the examples alone.

**Keep the design itself terse.** Convey the most important info
in short bullets; the user will ask follow-up questions for
anything they want to dig into. Aim for the whole report to be
readable in under a minute. Cover (each in one line or two):

- Classification (trivial / localized / architectural)
- Invariant established (one sentence from Phase 3)
- Precedent (the template/helper the design follows or extends -- or
  "new pattern, because <reason>")
- Phases touched (one line each: parser / sema / typesys / codegen / runtime / stdlib -- only the ones that apply)
- Tests + docs plan (brief)
- Confidence (see the scale below)
- CPython parity (one line: match / acknowledged divergence with its
  warning + escape hatch / TPy-only surface)
- Risks (architectural only; brief)
- Adjacent issues uncovered (brief)
- Proposed branch (see below)

**Confidence scale.** Rate the recommended design and say what caps
it:

- **High** -- the design extends a pattern verified in code; the
  sibling survey is done (or N/A); no unverified assumption left that
  could change the design's shape.
- **Medium** -- the core design is solid but at least one material
  assumption is unverified (name it: an uninspected emit path, an
  untested sibling interaction, a runtime helper taken on faith).
- **Low** -- the design rests on inference about how existing phases
  behave, or the sibling space is unexplored.

For Medium and Low, also name the cheapest probe that would raise the
rating (a snippet to compile, a file to read, an existing test to
inspect). A Low rating on an architectural design means the default
recommendation is the more-analysis path, not the build.

The code examples up front are required; compiler internals,
exhaustive sibling-survey results and design-doc-style elaboration are
not -- the user will ask if they want depth. An open decision is never
buried in the bullets: present each on its own, one at a time -- the
Python shape, the C++ each option yields, labelled options with
consequences, recommendation first.

**Proposed branch line.** Check `git branch --show-current` and pick:

- **On `master`**: propose a new branch off master with a short
  kebab-case name derived from the approved scope (e.g.
  `feat-tuple-unpack`, `extend-optional-narrowing`).
- **On an unrelated branch** (different scope from the new work):
  propose a new branch off master with a fitting name.
- **On a fitting branch** (already a sensible home for this work,
  e.g. extending an in-progress feature): say "current branch
  `<name>` is a fitting home -- staying put" and skip the new branch.

Don't propose deleting or cleaning up the old branch -- leave that
to the user.

**Do not start coding. Propose; don't poll.** Lead with your
recommendation: the design you would build from scratch -- the cleanest
long-term shape that fits the existing model, unless the risk check
raised a strong case against it. State it clearly enough that the user
can just confirm; don't dress it up as option (a) of a menu when there's
nothing else live. Include the proposed branch (if any) as part of the
recommendation -- one confirmation covers both.

Add alternatives ONLY when they are genuinely present:

- a **different design** -- a different phase boundary, invariant, or
  home for the behavior, when you see a materially better one or are
  genuinely torn between two;
- a **defer** path, when the design is reasonable but it's not the right
  time to build (file as TODO / open question);
- a **more-analysis** path, when something material is genuinely
  unresolved.

For many features there is only one sensible design. In that case,
propose it and ask for a yes -- not "pick (a), (b), or (c)."

When the user signals "just build it" or applies time pressure, push
back once: a wrong design is much harder to unwind than a wrong fix.
The cost of one round-trip is much lower than the cost of a feature
that surfaces later as multiple bugs and a redesign.

Once the user confirms the design (the recommendation or an agreed
alternative), create the proposed branch (if any) with
`git checkout -b <new-branch> master` and start implementing -- one
approval covers both the design and the branch. Do not push, force,
or use `-D`.

**Definition of done.** The feature is complete when ALL of these hold
-- the final report states each:

- The Phase 1 minimal example compiles and runs with the designed
  semantics; the report shows it (and its output) working.
- The Phase 4 test plan landed (happy path, errors, edge cases,
  sibling interactions, CPython compat or `no_cpython.txt` with
  reason) and the full suite is green (targeted `-k` during
  development; one full `uv run pytest` at the end).
- `docs/LANGUAGE_FEATURES.md` updated in the same commit; the area's
  other docs grepped for claims the new behavior falsifies and
  corrected.
- Sliced-scope exclusions and adjacent issues are actually filed
  (`TODO.md` / `BUGS.md`), not just mentioned in the report.
- Snapshot changes to existing tests match what the approval
  enumerated; extra churn that is neutral or an improvement is listed
  in the final report, a regression or behavior change goes back to
  the user (CLAUDE.md snapshot policy).

## Throughout: track adjacent issues

Feature work routinely surfaces nearby defects, missing diagnostics,
duplicate logic, stale comments, or docs gaps. Record each rather than
silently absorbing into the design.

Sources to watch:

- **Phase 1 prior-discussion search:** related work that was started
  but not finished -- could be the right place to graft this feature.
- **Phase 2 sibling survey:** patterns where the existing
  implementation has gaps the new feature shouldn't paper over.
- **Phase 3 design sketch:** invariants that *should* already hold but
  don't (e.g. a fact the emitter needs that no THIR node carries).
- **Implementation (if you reach it):** existing tests that change in
  unexpected ways, new diagnostics that fire on existing test cases,
  C++ warnings the change exposes.

For each new issue:

1. Check `BUGS.md` (defect) or `TODO.md` (feature gap); add a new
   entry if not already tracked.
2. If the issue blocks the current design, surface it to the user
   immediately -- don't paper over it.
3. If independent, note it in the final report so the user can decide
   whether to address it now or later.
