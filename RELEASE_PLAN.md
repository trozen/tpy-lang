# Release plan

The milestone slice of `TODO.md`. Short bullets only -- each item's full
entry lives in the file it points to (search the quoted phrase there).
When a release ships, delete its section and promote the next one.

Bugs are not listed here. Every `BUGS.md` entry tagged `IMM` or `HIGH`
blocks the release, so the must-fix set is whatever those tags currently
name -- a list here would only drift from them.

## 0.6.0 committed tracks (scope set 2026-07-28)

- Interop v1 wrap-up -- usable in real applications (deferred matrix in
  `docs/CPYTHON_INTEROP.md`):
  - Optional/None at the boundary (param + return)
  - PEP 517 backend -> abi3 wheel (phase 2.5; the design-heavy one --
    converges with the pyproject v0 track below)
  - `from tpy import __ext_module__`
  - Remaining exposed-class rungs (static/classmethods once
    `@classmethod` lands) + the `@export`-class validation
    consolidation
- pyproject v0 -- TODO: "pyproject v0: build TPy extensions and apps"
- Per-project compilation options -- TODO: "Per-project compilation
  options in pyproject" (default int, range checks, string/char type)
- Mixed-width int arithmetic decision -- TODO: "Sub-default-int
  arithmetic: promote, or keep width-preserving?" (snapshot-heavy)
- Traits -- TODO: "No `AnyInt` protocol covering" + "trivially-
  relocatable / primitive-value trait"
- Generic-record auto-derive bug (`_work.md`; file the BUGS.md entry
  with a repro when picked up)
- Complete the THIR migration: whole-body fallback -> 0 and the
  AST-codegen deletion cutover (doctrine in CLAUDE.md / IR_DESIGN.md)

## 0.6.0 (queued features; details in TODO.md / `_work.md` where tracked)

- `@classmethod` / `cls` -- alternate constructors everywhere
- `collections.defaultdict` + `namedtuple` (TODO: "`collections.namedtuple`
  / `typing.NamedTuple` support"); `deque` / `OrderedDict` if time allows
- Nested / multi-`for` comprehensions (list/dict/set + genexprs) -- stretch
- Extend the stale-capture warning to in-place mutation of captured
  containers (the fixable half of the closure-snapshot divergence)

## 0.7.0 (queue to triage at 0.7 planning; not commitments)

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
