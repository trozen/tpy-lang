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
- notes written: a Migration list for breaking changes, known
  limitations as classes.

Defects are fix priorities, not release criteria. The gate always runs
on the release commit itself, never the one before it: the version bump
changes the `harness/stdlib_render` snapshot (`tpy.version` folds
`__version__`), so regenerate it in that commit first. Tag after the
release commit (annotated; the build hook bakes `git describe`), then
`uv build --wheel` + `uv publish`.

## 0.7.0 (prepared when the trigger above fires)

Carried from 0.6.0:

- Tuples: no silent divergence and the everyday shapes compile --
  `docs/TUPLE_COMPLETION_PLAN.md`, the rest of U1 plus U2-U4 (the
  everyday rejects, the policy flips, the mixed-param diagnostics)
- Iterating a tuple (`for b in (b1, b2):`), the aliasing spelling for
  reference elements; ranks above nested comprehensions -- TODO:
  "Iterating a tuple"
- Nested / multi-`for` comprehensions (list/dict/set + genexprs) --
  TODO: "Nested comprehensions"
- `collections.defaultdict` -- TODO: "collections: the rest of the
  module"
- The 0.6.0 known limitations, silent miscompiles first

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
