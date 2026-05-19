---
name: tpy-add-feature
description: Structured procedure for adding new TurboPython language features or extending existing ones. Starts with a scope assessment to choose between a quick implementation and a full design pass. Forces the design to be presented to the user before any code change beyond trivial. Invoke at the start of any feature work -- new language construct, new built-in, new stdlib module, or extension to an existing feature.
disable-model-invocation: true
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
  `tpyc/codegen_cpp/` that introduces a new kind of fact is usually
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
- **Prior patterns:** grep `tpyc/sema/` and `tpyc/codegen_cpp/` for
  similar existing features. Reusing an existing emit helper or sema
  rule is almost always preferable to a new parallel one.
- **Tests at risk:** which existing `tests/cases/` snapshots will
  change? Snapshot policy applies; consult before regenerating.

For wide blast radius, spawn an `Explore` sub-agent with a focused
prompt: "given this feature design, which sites in the codebase share
the same invariant or extend the same pattern? Report file:line + the
pattern." A fresh-context survey catches patterns the main thread
misses due to momentum.

## Phase 3: Sketch the design

Where each piece of the feature lives:

- **Parser** (`tpyc/parse/`) -- new AST node? new syntax form? new
  `# tpy:` directive? Usually parser changes are small and confined.
- **Sema** -- which sub-module (`statements.py` / `expressions.py` /
  `calls.py` / `methods.py` / `protocols.py` / `narrowing.py` /
  `value_range.py` / `flow_facts.py` / `match.py` / ...)? What new
  facts on AST nodes does sema set? Mutation propagation?
- **Type system** (`tpyc/typesys.py`, `tpyc/coercions.py`,
  `tpyc/type_def_registry.py`) -- new TypeDef? new TypeRefNode shape?
  new coercion rule?
- **Codegen** (`tpyc/codegen_cpp/`) -- which emit path? Read sema's
  facts off the AST or off a side table? (Strongly prefer AST nodes
  per CLAUDE.md's perf section.)
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
- "Every callable AST node has a `is_readonly` attribute set during
  Phase 1 body analysis; codegen reads it to pick const/non-const
  emission."
- "The `@readonly_propagate` clone produces two callable entries with
  identical bodies but mirrored receiver const-ness; sema and codegen
  must dispatch on whichever fits the call site."

If you can't state the invariant in one sentence, finish Phase 3
before continuing.

## Phase 4: Plan tests and docs

**Tests:**

- Happy path (one or more `tests/cases/<group>/<name>/`)
- Error cases (`error_<name>/`) for invalid input the compiler should
  reject -- with `# tpyc: error(/regex/)` annotations
- Panic cases (`panic_<name>/`) for runtime failures, if applicable
- Edge cases: empty / zero / boundary / None / generic-parameter shapes
- Adjacent-feature interactions surfaced in Phase 2
- CPython compatibility: does the feature have a `lib/cpy/tpy/`
  equivalent? Default to yes; only skip with `no_cpython.txt` if the
  feature truly depends on C++-only behavior (e.g. `@native` interop)

**Existing tests at risk:** which snapshots will change? Consult the
snapshot policy in `CLAUDE.md` -- changes to expected output for
*existing* tests require a heads-up to the user before
`update_snapshots.py` runs.

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
- **CPython compat:** is the feature dual-target? If not, what's the
  justification?
- **Blast radius:** files, phases, snapshots. Quantify if possible
  ("~30 snapshots will regenerate, all expected").

Sometimes the cleanest design touches things in awkward ways. That is
itself a signal: the feature may not fit the existing model well.
Flag this as a design-level question rather than force-fitting.

## Phase 6: Present and wait

Write the design (compressed for localized features) and present it
to the user. **Start with a short summary of the feature** (1-3 sentences) --
the user must be able to confirm scope ("yes, that's the feature
we're talking about") in a few seconds, before reading any design
details. The summary names the user-visible surface (what Python
code becomes valid that wasn't before, or what existing surface
changes).

**Keep the design itself terse.** Convey the most important info
in short bullets; the user will ask follow-up questions for
anything they want to dig into. Aim for the whole report to be
readable in under a minute. Cover (each in one line or two):

- Classification (trivial / localized / architectural)
- User-visible surface (the minimal Python example from Phase 1)
- Invariant established (one sentence from Phase 3)
- Phases touched (one line each: parser / sema / typesys / codegen / runtime / stdlib -- only the ones that apply)
- Tests + docs plan (brief)
- Risks (architectural only; brief)
- Adjacent issues uncovered (brief)
- Proposed branch (see below)

Do NOT paste large code sketches, exhaustive sibling-survey results,
or full design-doc-style elaboration. The user will ask if they want
depth.

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

**Do not start coding.** The user picks the path:

- (a) proceed with the sketched design (and proposed branch, if any)
- (b) different design -- e.g. a different phase boundary, a different
  invariant, or a different home for the new behavior
- (c) more analysis needed before deciding
- (d) defer -- file as TODO/open question; not the right time to build

When the user signals "just build it" or applies time pressure, push
back once: a wrong design is much harder to unwind than a wrong fix.
The cost of one round-trip is much lower than the cost of a feature
that surfaces later as multiple bugs and a redesign.

Once the user signals (a), create the proposed branch (if any) with
`git checkout -b <new-branch> master` and start implementing -- one
approval covers both the design and the branch. Do not push, force,
or use `-D`.

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
  don't (e.g. AST nodes lacking a fact that codegen would need).
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
