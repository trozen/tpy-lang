# Release Notes

## 0.5.1 (2026-07-24)

Toolchain-preflight patch release.

- **C++ toolchain capability preflight**: every resolved compiler is
  probed once (cached; ~200ms, then a file stat) against the runtime's
  actual C++23 floor (`<expected>`, `<format>`, `<ranges>`, statement
  expressions). Auto-detection skips non-viable compilers -- a box
  whose only system compiler is too old (e.g. g++-11/12, clang 18)
  self-heals to the next viable one or the `[bundled]` zig toolchain
  instead of dying mid-build in template errors. An explicit
  `--cxx`/`$CXX` selection is always honored, with a prominent
  warning when it fails the probe. `--cxx list` now separates
  unsupported compilers into their own section, names the default
  auto selection, and prints the probe-cache location.
- **Python 3.12+ floor guard** at CLI entry: a clear requirement
  message instead of an opaque `SyntaxError` under older interpreters.

## 0.5.0 (2026-07-24)

309 commits since 0.4.0.

### Language and compiler

- **Threads and shared state**: OS threads (`tpy.thread.spawn` /
  `JoinHandle`, Send+move API with full inference), `Atomic[T]`
  (`tpy.atomic`), atomic shared ownership `Arc[T]` / `Weak[T]`
  (`tplib.arc`), blocking locks `Mutex[T]` / `RwLock[T]` and `Condvar`
  (`tpy.sync`), and a blocking cross-thread MPSC channel
  (`tplib.channel`). Cross-thread safety is compile-checked via the
  Send/Sync marker layer; canonical shared-mutable form is
  `Arc[Mutex[T]]`.
- **Exception-cleanup correctness cluster**: `finally` / `with
  __exit__` cleanup runs exactly once when it raises, a terminating
  `finally` runs on the try's fall-through path, reference-type
  return materialization defers past inline `finally` chains,
  exceptions escaping `__del__` fail fast (noexcept boundary,
  declared divergence), and the `except (A, B)` tuple form parses.
- **Operator semantics**: borrow-returning dunders alias like method
  calls (CPython aliasing, not silent copies), integer-division
  parity, and a value-union argument double-evaluation fix.
- **Optional/narrowing fixes**: the missing value-Optional deref
  family + narrow kills (suspension points, sinks), narrowed
  storage-Optional fields consumed correctly, accessor codegen for
  generic and Optional returns, and protocol-iterator view dangling.
- **Nested functions**: nested defs and lambdas in methods can access
  `self`; nested defs in resumable (async/generator) bodies emit as
  frame member functions; nested generator defs are cleanly rejected.
- **Generics**: multi-param generic-protocol bound inference,
  associated-type inference (`spawn(task)` fully inferred),
  class-shadowed method bounds through subclass receivers, sibling
  type params substituted into bounds, and inherited-ctor using-decls
  for generic bases.
- **Async**: bound coroutines (`await obj.method()` across modules),
  `Own[T]` returns move out of the coroutine frame instead of
  copying, and the async borrow-return ABI mirrors the sync
  convention.
- **Exceptions**: `OSError` with CPython-exact constructors,
  `str(e)`, and errno-to-subclass mapping; a
  `ConnectionError`/`BrokenPipeError` taxonomy; `ValueError` for a
  negative user `__len__`.
- **Enums**: `match` on alias-imported enums (exhaustiveness +
  identity), enum members as default parameter values, `.name`
  returns `StrView`.
- **Macros**: `@function_macro` extends to record methods; type args
  flow through `TypeInfo.type_args`.
- `@hotpath` decorator parsed (reserved; no effect yet).

### CPython extension authoring (new)

TPy source can now compile to an importable CPython extension module:
mark a module `# tpy: ext_module` and its `@export` surface crosses
the boundary -- functions (bool / fixed-width ints / float / str /
bytes / containers / enums / kwargs; `Span[T]` numeric params via the
buffer protocol), user classes via `PyType_FromSpec` (methods,
dunders, `@property` getsets, inheritance, identity-preserving borrow
returns), and exceptions (built-in and user-defined, with faithful
data fields). Ships with a dedicated ext-exec test harness and a
`Python.h` facade self-check. See `docs/CPYTHON_INTEROP.md`.

### Library

- **HTTPS / networking stack**: `ssl` on vendored mbedTLS 3.6.6
  (client and server-side TLS, bundled Mozilla CA store + system
  trust store), `http.client` with HTTPS and keep-alive,
  `urllib.parse` / `urllib.request.urlopen`, and `tplib.requests`
  (redirects, `Session` connection pooling, cookies, streaming,
  form/multipart uploads, timeouts) with CPython-parity networking
  errno mapping; `SIGPIPE` ignored so closed-peer writes raise.
- **`datetime` v1-v4**: `date` / `time` / `datetime` / `timedelta`
  (including float operators), formatting and parsing, `zoneinfo`.
- **Additions**: `io.FileIO` / `io.BufferedReader`, `http.HTTPStatus`,
  `socket.settimeout` + `makefile()`, `tplib.json` bare-number
  BigInt in `@model`.

### Tooling and packaging

- **Whole-run build cache**: an unchanged `tpy prog.py` rerun skips
  the entire pipeline and execs the cached binary (~100ms).
- **Nightly CI**: a Linux build matrix, macOS rows (osxcross
  cross-compile + best-effort native), and a CPython 3.12/3.13/3.14
  axis; macOS codegen fixes (BigInt literal ambiguity).
- **Packaging**: sdists no longer silently drop `lib/tpy/tplib`
  (hatchling symlink/inode-dedup workaround) -- guarded by a
  packaging smoke test; project metadata gained the repository URL.
- Internal: the THIR codegen migration advanced across ~150 commits
  (per-case byte-diff + ratchet keep it invisible: emitted C++ is
  byte-identical to the AST path).

### Known issues

Tracked in `BUGS.md`. The borrow/view-lifetime cluster (use-after-free
shapes under aliasing + mutation) ships as a known limitation -- it is
retired wholesale by the planned MIR ownership checker rather than
patched piecemeal. Escaping closures keep capture-by-value snapshot
semantics (inherent to the zero-cost closure model; a stale-capture
warning extension is queued for 0.6.0).

## 0.4.0 (2026-06-24)

235 commits since 0.3.0.

### Language and compiler

- **Send/Sync marker layer** (concurrency vocabulary): `Send`/`Sync`
  markers with auto-derivation, marker bounds on type params, a
  diagnostic surface naming the offending capture in a conversion
  chain, and the first enforcement site (`Channel[T: Send]`).
- **`__move__` dunder and movability trait**: a propagated movability
  trait, the `@nomove` opt-out, `nothrow __move__`, and restored
  `ArrayList` movability.
- **Borrow/storage form unification** (audit #9): a single
  form-conversion chokepoint, non-value tuple elements unified on the
  `T*` pointer form, owning/alias mixes accepted for reassigned
  borrow-form locals, and the B41 union-tuple-element conversion --
  closing a long tail of silent-copy and aliasing bugs at `yield` /
  `return` / unpack boundaries.
- **Comprehension ownership**: owned elements now *move* out of
  list/set/dict and filtered comprehensions instead of copying;
  per-iteration temps are scoped to the loop body; fixed-size
  comprehensions build by aggregate construction.
- **Safety/borrow checker hardening**: closed forward-reference and
  liveness-invisible holes in the auto-move borrow gate, closed
  view-lifetime dangling holes (audit #10), missing-return
  enforcement with implicit `None`, sound flow-fact kills at loops /
  handlers / `finally` / calls, and warnings for stale value-capture
  in escaping closures.
- **Provenance consolidation**: six name-keyed escape/ownership fact
  sets unified into one `BindingProvenance` record merged by a single
  lattice-driven routine (the proto-`LoanInfo` for the IR migration).
- **`match` fixes**: the Optional-match / arm-dispatch lowering
  cluster, pointer-repr Optional capture with guarded ref arms, and
  copy-vs-dangle handling of arm bindings.
- **Reflected operators** (`__radd__` & friends) emitted as friend
  operators; `@dataclass __post_init__` now called from the
  synthesized `__init__`; `print`/`str` of a union value routed
  through the variant `__str__` visitor; user `ValueType` classes
  made immutable; subclass `__init__` fields auto-declared.
- **Frontend plugin (DSL) IR maturation**: decorator registry with
  native lowering and `macro_data`, ternary / `AugAssign` nodes,
  method-call kwargs threaded through lowering, `Record.base` into
  `TpyRecord.bases`, overload groups.
- **Macros**: a deferred post-sema phase for `@function_macro`, plus
  richer compile-time introspection (`lookup_imported_name`,
  `enum_members`, `lookup_function_signatures`, shared type
  introspection) for function macros.
- **Cross-module identity**: type identity resolved by qname
  (triage #13); qualified + aliased cross-module generic recursive
  aliases; container literals coerced into cross-module recursive
  unions.
- Fixes: the `@overload`-ed generator compiler crash, `cpp_template`
  brace-aware expansion with `{{`/`}}` escaping, and a generic-overload
  link bug.

### Library

- **asyncio v2**: an epoll I/O reactor (Linux) and kqueue + self-pipe
  backend (macOS) with socket CPython parity, `sock_accept` /
  `sock_connect`, a client streams layer (`open_connection`,
  `StreamReader`/`StreamWriter`), `start_server`, graceful SIGINT
  shutdown, and the sync primitives `Lock`, `Semaphore`,
  `BoundedSemaphore`, and `Queue[T]`.
- **`os` / `os.path`**: a substantial new surface -- `scandir`,
  `walk` (top-down, bottom-up, `onerror`), low-level fd I/O,
  filesystem mutators, `stat`, `environ`, `urandom`, system info,
  terminal size, and a pure-TPy POSIX `os.path` (`expanduser`,
  `realpath`, `relpath`, `commonpath`, `normcase`, ...).
- **New stdlib modules**: `itertools` v1
  (count/repeat/cycle/islice/...), `collections.Counter` v1, `csv` v1
  (reader/writer + `DictReader`/`DictWriter`), and `heapq.merge`.
- **Additions**: `io.read(size)` and `SEEK_*`, `json` `load(fp)` /
  `dump(obj, fp)`, `sys.byteorder` / `maxsize` / `maxunicode`,
  `re.sub` count + a UTF fix and lazy `re.finditer`, and more builtin
  exception types.

### Tooling and packaging

- **macOS portability**: `signal` / `os` stdlib and the async reactor
  now compile and run on macOS; the platform-support pattern is
  documented with Windows (H3) on the roadmap.
- **Test harness**: a content-addressed local exec-result cache (a
  fresh checkout self-verifies once then caches; `--force-exec` no
  longer needed for routine runs), a `--cxx` toolchain switch,
  `tpy|`-prefixed harness output, and per-case binary cleanup.
- Runtime: fixed a `UninitArrayStorage` move use-after-free and added
  a `nothrow`-asserted `UninitStorage` slot.

### Known issues

These are tracked in `BUGS.md`; the borrow/lifetime cluster is the
primary motivation for the upcoming THIR/MIR migration, which replaces
the current string-keyed checks with a place/loan model. Highlights a
user may hit:

- **Operand evaluation order** in a two-operand binary expression is
  right-to-left, diverging from CPython's left-to-right when both
  operands have side effects.
- **Escaping closures** capture loop variables / in-place mutations by
  value-snapshot rather than aliasing, diverging from CPython.
- **Enum string surface**: `str(enum)` is rejected and `f"{enum}"`
  prints the underlying int (a design pass is pending).
- A few **narrow generator / borrow shapes** can copy-instead-of-
  reference or dangle; each has a documented workaround in `BUGS.md`
  and is targeted by the IR migration's region checker.

## 0.3.0 (2026-06-04)

285 commits since 0.2.0. First stable release published to PyPI as
`tpy-lang` (0.2.0 and earlier were tag-only, pre-rename).

### Language and compiler

- **Generators rebuilt on the resumable frame** (the same abstraction
  that powers async): `yield` now works in arbitrary control flow --
  `if`/`while`/`for`, `try`/`except`/`finally`, `with`, `match` --
  plus generator delegation and a declaration-driven yield ABI
  (borrow vs owned). The simple-generator lambda peephole is kept for
  the common single-loop shape.
- **async/await maturation**: `await` of protocol-param coroutines,
  `return` inside a suspending `finally`, nested suspending `finally`,
  bounded generic classes in resumable methods, narrowing and
  isinstance facts preserved across suspensions.
- **Generic type aliases**, non-recursive and recursive
  (`type Tree[T] = Leaf[T] | Node[T]`); records and protocols can use
  generic recursive aliases; recursive unions unified on the
  reference-type convention.
- **`match` expansion**: expression subjects for polymorphic dispatch,
  `@dynamic`-protocol dispatch, literal field-value conditions in
  class patterns (including `field=None`).
- **isinstance**: per-instantiation lowering on generic type params,
  `isinstance(self, Sub)` polymorphic dispatch.
- **`@dynamic` protocol + wrapper composition**: `Box[P]` / `Rc[P]`
  over abstract `@dynamic` protocols, `Box[P].set` / `Box[P].take`.
- **`*args` overhaul**: `readonly[T]` varargs with auto-readonly
  inference, generic `*args` iteration, `*unpacking` into generic and
  method varargs, `args[1:]` slicing on non-value slots.
- **Macros**: `@function_macro` body-rewriting macros (spike),
  `CallMacroContext.expected_type` for call macros, frontend
  function-macro application.
- `@auto_readonly` usage-dependent receiver const-ness, covariant
  element upcasts in container literals, bound-based `Ptr[U] -> Ptr[B]`
  coercion, inferred type-param bounds validated against substituted
  bounds.

### Library

- **tplib**: `Rc[T]` shared-ownership smart pointer (single-allocation
  cell, bounded `Rc.new[U: T]` factory) and `Weak[T]` non-owning
  companion for cycle-breaking.
- **asyncio**: executor and `run` ported from C++ to TPy, variadic
  `gather(*tasks)`, `gather_list_settled`, `asyncio.Event`.
- **stdlib**: `io` v1, `heapq`, `time` (`perf_counter` / `monotonic` /
  `process_time`), `math.prod` + faster `isqrt`, `random` gaps
  (`choice`, `shuffle`, auto-seed, `getrandbits` k>32),
  `int.bit_length`, `shl_wrap` / `shr_wrap` on fixed-width ints.
- **Runtime panics are now catchable exceptions**; `IndexError` and
  `KeyError` can be caught.

### Tooling and packaging

- Project renamed to **tpy-lang**; published to PyPI with an sdist
  allowlist so stray working-tree files cannot leak into artifacts.
- `tpy --install-agent-docs` bundles the generated API reference for
  AI coding agents; README gains a PyPI-based Quick Start.

Plus many bug fixes across sema, codegen, and the borrow checker.

## 0.2.0 (2026-05-02)

Highlights since 0.1.0 (~189 commits):

- Macro system matures: argparse builder-trace macro, JSON `@model`
  call-macros, dataclass; class/call/builder macro APIs stabilized.
- New stdlib coverage: `json` (recursive-union JsonValue), `argparse`,
  `base64`, `hashlib`, `socket`, `random` additions.
- Sema: extensive readonly/match/narrowing fixes, generic-T over str
  borrow tracking, recursive-union support, value-variant unions,
  literal-seeded local widening, mutation propagation across
  reassignment.
- Codegen: out-of-line method bodies (small inline in .hpp, large in
  .cpp), Python-faithful repr escapes, class-const phases including
  T-independent constants on generics, D24 native qualification.
- Runtime: float `%` Python floor semantics, faster repeat_range,
  constexpr checked arithmetic, macOS portability (pcre2, socket).
- Build: per-variant PCH cache, sign-conversion clean header.

## 0.1.0 (2026-04-13)

Initial tagged release of the Python-to-C++ proof-of-concept
toolchain.
