# Release plan

The milestone slice of `TODO.md`. Short bullets only -- each item's full
entry lives in the file it points to (search the quoted phrase there).
When a release ships, delete its section and promote the next one.

## Cadence and readiness gate

A release is a dated checkpoint of `master`: it ships what has merged by
the date. Trigger: about 200-300 commits or 4-6 weeks since the last tag.

Readiness gate, run from the commit to be tagged:

- full suite green with `--force-exec`;
- the `tpy-examples` corpus compiles and runs CPython-identical;
- clean-venv install smoke, `_buildinfo` stamp, private-name scan;
- the REPL from the installed wheel starts on a pty, with a history
  file present, under an editline interpreter (`uv run --python 3.14`);
- notes written: a Migration list for breaking changes, known
  limitations as classes.

Defects are fix priorities, not release criteria. The gate always runs
on the release commit itself, never the one before it: the version bump
changes the `harness/stdlib_render` snapshot (`tpy.version` folds
`__version__`), so regenerate it in that commit first. Tag after the
release commit (annotated; the build hook bakes `git describe`), then
`uv build --wheel` + `uv publish`.

## 0.8.0 (aim: before Christmas 2026; gate starts about 2026-12-14)

Ships what has merged by then; a target not merged by about 2026-12-10
moves to 0.9 without holding the release. The themes run in parallel:
THIR and MIR push as far as they get, the rest are bounded units.

THIR: coverage and unification

- The lowering consolidation, through the protocol-slot stages to
  deleting the per-sink rows -- TODO: "One conversion boundary for every
  value sink"; owning iteration on the way -- TODO: "Owning iteration"
- Fewer hardcoded builtin facts in the lowering -- TODO: "[thir] RULE"

MIR: coverage and cleanup

- Storage placement decided by MIR -- TODO: "[MIR] Storage placement
  driven by MIR"
- More bodies lowered and more lifetime faces warned (the 0.7.0 known
  issue "views and element references outliving their buffer"); a sema
  diagnostic family MIR covers moves there and sema's checker for it is
  deleted
- `str` / `bytes` views: MIR's relaxation of the view rule's copies --
  TODO: "`str` / `bytes` views: MIR precision over the one view rule"

sema: one implementation per concept

- One binder for every binding: tuples first -- TODO: "Local binding
  (incl. reassignment) through convert", then
  `docs/TUPLE_COMPLETION_PLAN.md` U2 / U5 / U3 as far as they get;
  calls -- TODO: "One binder: what remains"
- `@overload` stubs as `@dispatch` variants, the rest -- TODO:
  "`@overload` copies: the residue"
- One import-time diagnostic for reading a cycle peer --
  `BUGS.md#cycle-from-import-partially-initialized-accepted`
- Fully qualified local type names in the C++ -- TODO: "Always
  fully-qualify local type references"
- Two or three `[sema hygiene]` units, picked from a survey of sema's
  duplicated paths when the line gets to them

Features

- `Final` infers its type from the value, bare (`X: Final = 5`, PEP 591)
  and through the wildcard (`X: Final[_] = 5`), with `_` accepted in
  annotations generally -- `BUGS.md#bare-final-annotation-rejects`,
  `BUGS.md#type-arg-wildcard-in-annotations-unknown`
- SIMD foundations: the `Simd[T, N]` library type over vector extensions
  (spelling decided at design) -- TODO: "A `Simd[T, N]` library type";
  with it, as far as it gets, TODO: "numeric-primitives layer below"
- Unchecked access proved in numeric inner loops -- TODO: "Checks that
  cannot fail in numeric inner loops"
- Float results do not depend on the C++ compiler --
  `BUGS.md#float-contraction-depends-on-compiler`
- pyproject v0, extensions first (tpy-infer is the first user) -- TODO:
  "pyproject v0: build TPy", "Per-project compilation options in
  pyproject"
- `collections.defaultdict` v1, once the THIR mechanisms it waits on
  land -- TODO: "collections: the rest of the module"
- Decide what `chr()` returns -- TODO: "What `chr()` returns"

Stretch, design only: `@noalloc` enforcement v1 (FEATURE_ROADMAP C3,
parsed-only today; after MIR placement); Unicode `str` / build-time
string width (`docs/STRING_WIDTH_DESIGN.md`).

Also: the 0.7.0 known issues (`RELEASE_NOTES.md`), silent classes first,
as each line meets them.

Queue (not commitments; triage at 0.9 planning):

- Iterating a tuple (`for b in (b1, b2):`) -- TODO: "Iterating a tuple"
- Per-instantiation result form for a generic body, then iterator
  provenance (user, 2026-10-05) -- TODO: "Per-instantiation result form
  for a generic body", "Iterator provenance"
- Rejects the ports filed: `prebound-list-rebound-in-loop-from-comprehension`,
  `str-field-write-through-subscript-rejects`
- The other multi-`for` comprehension refusals -- TODO: "Lift: a name
  bound by two `for` clauses", "Lift: rebinding a capture" (MIR),
  "Lift: a walrus binding a reference inside a comprehension" (MIR)
- Tuples, the structural half -- `docs/TUPLE_COMPLETION_PLAN.md`, units
  U5-U7: one elementwise form rule in THIR, `str` / `bytes` view elements
  (ABI change), the loud tail. U8 (the general partial move: reading a
  tuple param's other element after one was consumed) waits on MIR.
- Borrow / provenance `HIGH` entries (loans that outlive their storage,
  silent copies where CPython aliases, borrow-form vs storage-form
  spelling mismatches),
  fixed against the analysis-only MIR rather than patched one by one in
  the AST borrow tracker
- Interop v1 wrap-up: PEP 517 backend -> abi3 wheel (phase 2.5),
  `from tpy import __ext_module__`, exposed-class static/classmethods +
  the `@export`-class validation consolidation
- Polymorphic `cls` -- FEATURE_ROADMAP: "Polymorphic `cls`
  (`type[Self]`)"
- Traits -- TODO: "No `AnyInt` protocol covering" + "trivially-
  relocatable / primitive-value trait"
- `namedtuple` -- TODO: "`collections.namedtuple` / `typing.NamedTuple`
  support"; `deque` / `OrderedDict`
- Extend the stale-capture warning to in-place mutation of captured
  containers (the fixable half of the closure-snapshot divergence; no
  TODO entry yet)
- Generic-record auto-derive bug (no repro on file; file the BUGS.md
  entry with one when picked up)
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
