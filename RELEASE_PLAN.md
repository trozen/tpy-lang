# Release plan

The milestone slice of `TODO.md`. Short bullets only -- each item's full
entry lives in the file it points to (search the quoted phrase there).
When a release ships, delete its section and promote the next one.

Bugs are not listed here. Every `BUGS.md` entry tagged `IMM` or `HIGH`
blocks the release, so the must-fix set is whatever those tags currently
name -- a list here would only drift from them. The one exception is
stated per release below.

## 0.6.0 (scope set 2026-09-18)

Bug gate: every `IMM` and `HIGH` entry EXCEPT the borrow / provenance
family, which moves to 0.7.0 (see there). An entry is in that family
when its defect is one of:

- a loan, view or pointer that outlives its storage, or a mutation that
  does not invalidate a live borrow (dangling temporaries, loans blind
  to a write path, frame-held borrows);
- a silent copy where CPython aliases, or a move at the wrong point;
- a borrow-form vs storage-form spelling mismatch (const-ness, pointer
  vs payload slot) at a parameter, tuple element or Optional local.

Features:

- THIR migration -- SHIPPED (fallback -> 0, then the AST-codegen
  deletion). Residual track: TODO: "The post-cutover fix queue: shapes
  that are now compile errors"
- Nested / multi-`for` comprehensions (list/dict/set + genexprs) --
  TODO: "Nested comprehensions"
- Methods on enums (instance, `@staticmethod`, `@classmethod`; covers
  `Color.from_str`) -- TODO: "Methods on enums"
- `collections.defaultdict` -- TODO: "collections: the rest of the
  module"
- Interop: Optional/None at the `@export` boundary (param + return) --
  `docs/CPYTHON_INTEROP.md` type table

## 0.7.0 (queue to triage at 0.7 planning; not commitments)

- Borrow / provenance `HIGH` entries (the family defined under 0.6.0),
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
