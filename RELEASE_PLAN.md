# Release plan

The milestone slice of `TODO.md`. Short bullets only -- each item's full
entry lives in the file it points to (search the quoted phrase there).
When a release ships, delete its section and promote the next one.

## Cadence and gate

A soft trigger, not a deadline: start preparing a release once a cycle
has about 200-300 commits on `master` or 4-6 weeks have passed,
whichever comes first. Dates below are aims.

- **Preparing a release** means triaging its bug gate. The release ships
  when that list is clear; work on `master` does not pause for it.
- **Features are targets, not gates.** What has merged when the gate
  clears ships; the rest moves to the next release.
- **The bug gate is a fixed list, triaged when preparation starts** --
  not the `IMM` / `HIGH` tags, which stay a fix priority. It holds:
  - regressions since the previous release;
  - compiler crashes on valid code;
  - silent miscompiles on everyday shapes.

  Every other open defect ships as a known limitation in the release
  notes. A defect filed later joins the gate only if it is one of the
  three kinds above.
- **Regression check:** at triage and again just before tagging, re-run
  the open `BUGS.md` entries' shapes and the example corpus (`examples/`,
  `../tpy-examples`) against the previous release tag, in a worktree of
  that tag, and mark each hit `REGRESSION since vX.Y.Z` in its headline.

## 0.6.0 (ships when the gate below clears; aim early October 2026)

Bug gate, triaged 2026-09-25. Every entry is a regression since v0.5.0
unless marked otherwise. Batched by likely shared cause; each batch is one
`/tpy-fix-bug` unit, and the batches are independent of each other.

- **B -- dropped null checks** (both segfault where v0.5.0 kept the
  `deref_check`; likely one cause):
  - `optional-list-elem-decl-drops-deref-check`
  - `unnarrowed-optional-elem-field-read`
- **E -- lowering rejects and crashes** (mostly THIR lowering arms):
  - `tuple-ref-element-container-field-read`
  - `thir-int-methodarg-shift-not-folded` (needs one spelling rule for
    integer literals first -- TODO: "One spelling rule for integer
    literals")
- **A -- generators** (frame-local declaration and narrowing):
  - `gen-finally-local-assign-internal-error` (crash)
  - `generator-optional-match-arm-not-narrowed`
  - `generator-arg-view-temp-hoist-dangles` -- not a regression: v0.5.0
    warned, master is silent
- **D -- moves past a last use** (liveness and rebind; overlaps the
  liveness work landing on `master` since 2026-09-25, so start it once that
  settles):
  - `comp-element-move-inside-loop`
  - `finally-rebind-eager-move-alias-read`
  - `foreach-rebound-name-reiterated`
  - `nested-list-literal-alias-rebind-clobbers`
  - `lambda-body-reads-invisible-to-liveness` -- not a regression: a
    value moved at its "last use" before a lambda that reads it (added
    2026-09-27)
- **C -- loop variable reuse** (added 2026-09-27; not a regression):
  - `head-first-loop-var-then-head-uninitialized` -- needs
    `for-head-rebind-of-reference-local-rejected` and
    `str-local-rebound-by-for-head-rejected` first, so the head can bind
    without rejecting today's correct programs
- **F -- sibling-arm joins** (added 2026-09-28): `match` arms and `except`
  handlers now join a local's bindings, so programs v0.5.0 compiled only
  because the wider arm came last are refused (the reverse order was a
  silent truncation there); the regressions:
  - `optional-int-widening-refused` (the arm forms)
  - `tuple-element-int-widening-refused` (narrower arm first)
  - `carried-arm-name-loop-binding-unassigned` (`match` / `except` faces)
  - `alias-rebind-over-fresh-list-rejected` (the `except` face)
  - not regressions, the binding forms the join does not reach yet:
    `sibling-arm-loop-target-not-joined`, `walrus-rebind-never-widens`,
    `match-capture-wider-than-arm-binding-truncates`
- **Decision first:** `c-abi-allowlist-overshoot` -- the C-ABI allow-list
  as "C-spellable" or "ABI-compatible"; until decided it ships as a known
  limitation.

Shipped:

- THIR migration (fallback -> 0, then the AST-codegen deletion).
  Residual track: TODO: "The post-cutover fix queue: shapes that are now
  compile errors"
- Methods on enums (instance, `@staticmethod`, `@classmethod`; covers
  `Color.from_str`) -- TODO: "Methods on enums"
- Interop: Optional/None at the `@export` boundary (param + return, and
  value-form fields) -- `docs/CPYTHON_INTEROP.md` type table
- Tuples: the U1 silent-divergence work merged so far --
  `docs/TUPLE_COMPLETION_PLAN.md`

Target (ships if merged when the gate clears):

- `collections.defaultdict` -- TODO: "collections: the rest of the
  module"

## 0.7.0 (prepared when the trigger above fires after 0.6.0)

Carried from 0.6.0:

- Tuples: no silent divergence and the everyday shapes compile --
  `docs/TUPLE_COMPLETION_PLAN.md`, the rest of U1 plus U2-U4 (the
  everyday rejects, the policy flips, the mixed-param diagnostics)
- Iterating a tuple (`for b in (b1, b2):`), the aliasing spelling for
  reference elements; ranks above nested comprehensions -- TODO:
  "Iterating a tuple"
- Nested / multi-`for` comprehensions (list/dict/set + genexprs) --
  TODO: "Nested comprehensions"
- `collections.defaultdict`, if it misses 0.6.0

Queue (triage at 0.7 planning; not commitments):

- Tuples, the structural half -- `docs/TUPLE_COMPLETION_PLAN.md`, units
  U5-U7: one elementwise form rule in THIR, `str` / `bytes` view elements
  (ABI change), the loud tail. U8 (per-element ownership at a mixed tuple
  param) waits on MIR.
- Borrow / provenance `HIGH` entries (loans that outlive their storage,
  silent copies where CPython aliases, borrow-form vs storage-form
  spelling mismatches),
  fixed against the analysis-only MIR rather than patched one by one in
  the AST borrow tracker
- Interop v1 wrap-up: PEP 517 backend -> abi3 wheel (phase 2.5),
  `from tpy import __ext_module__`, exposed-class static/classmethods +
  the `@export`-class validation consolidation
- pyproject v0 -- TODO: "pyproject v0: build TPy extensions and apps";
  per-project options designed with it -- TODO: "Per-project compilation
  options in pyproject"
- Polymorphic `cls` -- FEATURE_ROADMAP: "Polymorphic `cls`
  (`type[Self]`)"
- Mixed-width int arithmetic decision -- TODO: "Sub-default-int
  arithmetic: promote, or keep width-preserving?" (snapshot-heavy)
- Traits -- TODO: "No `AnyInt` protocol covering" + "trivially-
  relocatable / primitive-value trait"
- `namedtuple` -- TODO: "`collections.namedtuple` / `typing.NamedTuple`
  support"; `deque` / `OrderedDict`
- Extend the stale-capture warning to in-place mutation of captured
  containers (the fixable half of the closure-snapshot divergence; no
  TODO entry yet)
- Generic-record auto-derive bug (no repro on file; file the BUGS.md
  entry with one when picked up)
- `@noalloc` enforcement v1 (FEATURE_ROADMAP C3 -- parsed-only today)
- Unicode `str` / build-time string width (`docs/STRING_WIDTH_DESIGN.md`)
- Send/Sync enforcement phases 4-6 (`docs/SEND_SYNC_DESIGN.md`)
- Async remainder: async generators, multi-threaded executor,
  MPMC / `select` channels
- `pathlib` + filesystem bindings (STDLIB_ROADMAP P0)
- `subprocess` + process spawning (STDLIB_ROADMAP P1)
- pytest support (run TPy code under pytest)
- Generic variadic args (`def f(*args: T...)`)
- Missing mixin gaps; overloads x multi-inheritance interactions
- Streaming output dunder -- TODO: "`__stream__` / `__write__` dunder"
- Derive-style default protocols for hash/eq/compare/str
  (Rust-`#[derive]`-inspired; design pass)
- FrameType unification (lambda/coroutine); parser rewrite; CPython
  stubs review; fuzz tracks (borrow/escape/liveness, ownership);
  dead-code detection; multi-error diagnostics; drop-flag elimination;
  effects framework (`@nothrow`/`@pure`)
