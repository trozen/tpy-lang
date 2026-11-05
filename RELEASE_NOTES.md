# Release Notes

## 0.7.0 (2026-11-05)

298 commits since 0.6.2. A breaking release -- read Migration first.

### Migration

- **Module settings are a `__tpy__` dict**: `# tpy:` directive comments
  are gone. Write `__tpy__ = {"mod": "native", "includes": [...],
  "links": [...]}` after the docstring; a leftover comment is an error
  (`'# tpy:' directives were replaced by __tpy__`). Unknown keys and
  non-literal values are errors.
- **Removed**: `@inline` and `FStr` (an f-string is a plain `str`
  everywhere); `@native_preserves_refs` (use `@native(...,
  elements=True)` and `mutates="elements"`); `@copy_returns_warn` (use
  `borrows=` / `element_of=`); `unsafe_interior_mutable[Ptr[T]]`.
- **Fixed-width wrap helpers renamed, no aliases**: `T.trunc(x)` ->
  `T.wrap(x)`, `T.add_wrap` / `sub_wrap` / `mul_wrap` / `shl_wrap` /
  `shr_wrap` -> `T.wrap_add` / ... / `T.wrap_shr`. Signed `wrap_shr` is
  arithmetic: `int8.wrap_shr(-1, 1)` is `-1`.
- **8- and 16-bit arithmetic promotes**: `+ - * // % ** << ~` and unary
  `-` on `int8`..`uint16` compute in the default int, so
  `uint8(200) + uint8(100)` is `300`; `& | ^ >>` keep the narrow type.
  Overflow raises `OverflowError` in every build mode, and an integer
  value narrows into a fixed-width slot through a run-time check (an
  out-of-range parameter default from a `Final` is still unchecked,
  `BUGS.md#fixed-int-default-final-out-of-range`).
- **Behavior now follows CPython** (output can change):
  - `sys.exit` raises `SystemExit`, so `finally`, `with` and a bare
    `except` see it; a `str` code goes to stderr with status 1.
  - Ctrl-C raises `KeyboardInterrupt` in synchronous programs (was a
    hard kill); an uncaught one exits with status 130. Every
    `asyncio.run` installs its own SIGINT handler.
  - A None access the compiler cannot rule out raises a catchable
    `AttributeError` / `TypeError` instead of panicking.
  - Text: `str(0.1 * 3)` is `0.30000000000000004`, `str(float("inf"))`
    is `inf`, a `char` reprs quoted (`list(s)` prints `['a', 'b']`), a
    plain `Enum` member prints `Color.RED`, a record without `__str__`
    prints `<__main__.X object at 0x...>`.
  - Aliasing: `min` / `max` (with `key=`) and `dict.get(k, d)` return
    the object itself, `deref(h)` binds a borrow, and `ba += x` on a
    `bytearray` mutates in place -- each was a silent copy. Rebinding a
    reference local keeps the old object for its aliases (it was
    overwritten in place). `next(it, d)` accepts reference elements and
    copies, with a warning, when bound.
  - A value-type loop variable (int, `str`, `bytes`, tuple) copies its
    element when the loop may write the source: `for v in y: y[0] = 50;
    print(v)` prints the old element. A record loop variable aliases, as
    in CPython.
  - Parameter defaults resolve where the `def` is written, not at the
    caller; `-1 in d.values()` compares integers by value.
  - `isinstance(h, Dog)` on an `Rc` / `Box` / `Arc` / `Weak` handle
    tests the handle, so it is False, with a warning pointing at
    `isinstance(p := deref(h), Dog)`; 0.6 looked through to the object.
  - `asyncio.sleep(0)` always yields once; timer wake-ups follow
    CPython's loop order.
  - `random`: a one-value draw (`randrange(1)`, `randint(a, a)`,
    `choice([x])`) consumes random state as CPython does, so seeded
    sequences after one differ from 0.6.
  - `io.FileIO(fd, mode="r", closefd=True)`: `mode` now comes before
    `closefd`, so a positional `FileIO(fd, False)` must become
    `closefd=False`; `readable()` / `writable()` / `seekable()` on a
    closed stream raise `ValueError`.
  - Overloads rank by declared parameter shape (exact > generic >
    subclass > protocol > bare `T`), and declaration order never breaks
    a tie: `m(3)` over `m(int)` / `m(float)` runs the `int` variant, and
    a genuine tie is an `Ambiguous overload` error.
- **`chr()` and `char(n)` are limited to 0..127 for now**: `chr(200)`
  raises `ValueError` (0.6 stored a truncated byte); CPython accepts up
  to `0x10FFFF`.
- **A `JoinHandle` dropped without `join()` detaches** the thread, as
  Rust's does, instead of panicking.
- **Newly refused, each with a located error**:
  - a read before a function-wide local binding of the same name
    (`'x' is read before it is bound on line 4 ...`; 0.6 read the
    module's `x`);
  - an annotation on a local after its first binding;
  - an implicit `float` -> `float32` conversion at a declared slot
    (literals still adapt);
  - an empty container literal whose first typed store is narrower than
    a later one (`ws = []; ws.append(int8(1)); ws.append(int64(2))`:
    `'ws' holds int8 elements ... annotate its first binding`), or a
    store after a typed use fixed the element type;
  - an int value or default in an inferred float dict
    (`d = {"a": 0.5}; d.get("b", 1)`: `write 1.0 instead of 1`; 0.6
    converted it silently);
  - a callback that would narrow its argument or whose return does not
    fit the `Callable` slot (`write 'lambda x: cb(x)'`; 0.6 truncated);
  - a TPy-bodied `@dispatch` / `@overload` constructor: a class takes
    one `__init__` (bodyless `@native` constructor sets stay);
  - a write into a fresh call result (`make().n = 5`);  - the entry module inside an import cycle; a generic type naming a
    cycle peer's class in a header position;
  - builtin calls breaking CPython 3.12's positional-only / keyword-only
    rules (`s.find("a", start=1)`; `sorted(xs, f)` is refused too, with
    a misleading message, `BUGS.md#builtin-default-key-diagnostics`);
  - a write to another module's `Final` (`sys.platform = "x"`);
  - a non-constant class-body name read by a parameter default;
  - readonly: an explicit `@readonly` method returning its own storage
    at a mutable type must declare `-> readonly[...]`; a
    `__deref__ -> readonly[T]` at a plain `T` parameter; re-pointing a
    `readonly[Ptr]` field. A readonly holder no longer makes the
    pointee of its `Ptr[T]` / `Span[T]` readonly -- spell
    `Ptr[readonly[T]]` for that;
  - module-level code in a native module other than imports and
    declarations (was dropped silently);
  - protocol type-parameter bounds (`P[T: ValueType]` refuses
    `P[list[int32]]`), duplicate or constrained protocol type params;
  - a `ValueType` field holding a reference type through a part or an
    unbounded `T`;
  - in a generic generator `-> Iterator[T]`, yielding a fresh value
    (declare `Iterator[Own[T]]` or bind it first);
  - redefining a nested `def` name in the same block; `nonlocal` in a
    nested def that an escaping closure calls.
- **C++ spellings `@native` companion code sees**: `tpy::sys_exit` is
  gone (`main()` calls `::tpy::run_main`); the None-check helpers are
  `none_*` and throw `NoneAttributeError` / `NoneTypeError` (base
  `NoneAccessError`); a `Final[T] = native_global(...)` is declared
  `extern const T`, so its definition must be `const`; overload and
  `@dispatch` variants get their own C++ names (`describe__Dog`); a
  `Literal[str]` result is `std::string` (a getter no longer returns a
  view); a module part that is a C++ keyword or ends in `_` gets a
  trailing `_` in its namespace (`util_` -> `util__`); a call omitting a
  `@native` binding's defaulted argument passes the TPy default, not the
  C++ one; `Ptr[char]` is one character, not a C string; importing
  through a native module runs its imports' initializers.
- **CPython stubs**: fixed-width arithmetic under CPython returns plain
  `int`s, which changes width-sensitive dispatch there.
- **Caches**: the precompiled header moved to one shared
  `~/.cache/tpyc/pch/`. The 0.6 per-program `__tpyc__/pch/` directories
  (about 200MB each) and the REPL's `~/.cache/tpyc/pch_<key>` can be
  deleted by hand. Shared-cache entries unused for `$TPYC_CACHE_MAX_AGE`
  days (default 3) are evicted; exec markers live 30 days. The first
  build after upgrading rebuilds everything.

### Language and compiler

- **MIR lifetime warnings**: the MIR ownership analysis runs on every
  compile and warns where a reference may outlive or lose what it
  points into -- an argument pointing into a container the callee
  modifies, an assignment freeing a value a view still reads, a view
  returned from a temporary, an iteration whose element a call may
  move. Advisory: generated code is unchanged, and a MIR failure only
  skips that body's checks.
- **Inference**: a literal-seeded local widens to the typed values
  stored later; mixed fixed-width operators widen (`int8 + int32` is
  `int32`); a container literal's element type is settled by all its
  stores and typed uses, empty literals and nested rows included; an
  integer constant folds at every sink.
- **Overloads and dispatch**: one shape ranking for calls, operators,
  subscripts and membership (`x in h` is `h.__contains__(x)`); record
  operators answer like their explicit dunder (`3 < c` runs
  `c.__gt__(3)`, `!=` negates `__eq__`); `@overload` stubs are
  copies of the implementation (methods, generics, generators,
  coroutines, `@error_return`); a `Literal` is a value set over its base;
  `isinstance` on a static type folds the dead branch.
- **Builtins**: keyword arguments per the CPython 3.12 signatures
  (`sorted(xs, key=f, reverse=True)`, `max(xs, key=f)`); `max(xs)` /
  `min(xs)` over an iterable, `next(it, default)`, `print(*xs)`,
  `for line in f`; `is` / `is not` test identity.
- **Comprehensions** take several `for` clauses with filters at each
  level, and an outer clause may iterate an owned source
  (`[p.v for ps in batches() for p in ps]`).
- **Closures**: a captured name is never moved; an escaping nested
  `def` that calls sibling defs no longer dangles; a closure that keeps
  its own copy of an object warns.
- **`str` and `bytes` under one view rule**, decided at the binding:
  `y = names[i]` owns a copy where a view could dangle; `bytes` works
  wherever `str` does in tuples and slices.
- **Tuples**: tuple locals read like tuple parameters; tuples at
  container elements, arguments, returns, `Own[tuple]` slots and
  `-> tuple[...] | None`; tuple globals; `a, b = stack.pop()`; unpack
  targets bind like scalar names (`docs/TUPLE_COMPLETION_PLAN.md`).
- **A builtin base is an ordinary base**: subclass `dict`, `list`,
  `set`, `bytearray` and the builtin exceptions, with iteration, `in`,
  comparison, printing, upcasts, `isinstance` and `super()`.
- **Protocols**: type-parameter bounds are honored; a protocol argument
  takes the concrete type's form, so a record binds in place instead of
  being copied; `@runtime_checkable` is accepted.
- **Field writes and arguments** convert at the slot's declared form:
  about 190 well-typed field writes and 44 argument shapes that were
  refused now compile.
- **Scoping**: a name bound anywhere in a function is local to the whole
  function; module statements form one initialization body; dotted
  class and enum paths (`pkg.mod.Color.RED`) resolve everywhere.
- **Fixed silent bugs** include: rebinding the iterated name inside a
  loop changed what the loop iterated, and a comprehension read a
  moved-from element after its first iteration (both regressions since
  0.5.0); `u > -1` on a `uint64` was False; `o.get().v += 1` evaluated
  the call twice; `q = d.get(k); r = q` emptied the dict entry; escaping
  nested defs and several `str` views read freed memory;
  `next(iter([3, 4]), 0)` read freed memory; a narrowing proved in `try`
  and read in `finally` segfaulted.

### Generated C++

A module's C++ consults only its import closure, so library headers are
identical in every program; calls to empty module initializers are
gone. Checked-arithmetic helpers are inlined with a cold throw path in
every build mode, and constants fold instead of calling them. More
parameters and loop bindings are `const T&`; `raise` no longer copies
the exception; `is` renders `::tpy::same_object`. Generated code no
longer depends on Python's string hash seed.

### CPython interop and embedding

- **TPy programs run under CPython**: the wheel ships the `tpy` stubs
  and the real `tplib`, so a TPy program runs under CPython 3.12+ or
  PyPy after `pip install tpy-lang`; `TPY_CPYTHON_FAST=1` selects a
  faster stub arithmetic mode.
- **`--no-signals`** compiles the Ctrl-C layer out for embedded builds.
  A host that keeps it gets `tpy::install_interrupt_handler()` and
  `tpy::request_interrupt()`. Extension modules and `--no-main` hosts
  keep their own SIGINT handler; `SystemExit` crosses `@export` with its
  code.
- **`@native`**: bound `__getitem__` stubs render at subscripts and
  through `Ptr` receivers; omitted trailing defaults are spelled from
  the TPy signature; native modules may form import cycles. New binding
  keywords: `transient=`, `checks_signals=`, `adopts=`.

### Library

- **Signals**: `signal.signal(signum, handler)`, `default_int_handler`,
  SIGHUP..SIGWINCH; sleep, input, sockets, thread joins and file I/O are
  interruptible.
- **`subprocess.Popen`** (pipes, `wait` / `poll` / `terminate` /
  `kill`, context manager), `io.BufferedWriter`, `os.kill`, `os.waitpid`,
  `os.waitstatus_to_exitcode`.
- **`sys.platform`, `sys.byteorder`, `sys.maxsize`** and a new
  `platform` module (`system()`, `machine()`) report the build target;
  `sys.platform == ...` folds to a constant.
- **asyncio**: scheduling follows CPython's loop; cancellation reaches
  `Queue`, `Lock`, `Semaphore`, `Event` and `Future`; `Future.cancel`.
- **`bytearray`** slice assignment, extended slices included.
- **Text**: one shortest-round-trip float conversion; f-strings and
  `print` render Optionals, `Ptr` and unions as their payload or `None`;
  enum and default record text as in CPython 3.12.
- `str.join` reserves once (up to 5x faster on large joins); `BigInt` converts
  from every integer type.

### Tooling

- **Build caches**: one shared precompiled header for the CLI, the REPL
  and tests, and compile steps share ccache entries across programs --
  building a new `print("hi")` program after another one drops from
  about 5.6s to 0.05s (default compiler settings).
- CLI: Ctrl-C during a build exits cleanly; `--dump-mir` shows MIR's
  verdict per body.
- Nightly CI on GitHub Actions, including native macOS.

### Known issues

Tracked in `BUGS.md`. The silent classes that matter most: call
arguments, operands and f-string parts run their side effects in the C++
compiler's order rather than left to right (`f(random(), random())`
draws in reverse order); a value moved at its last use while a closure,
a `finally` or an alias still reads it; a view (a `str` slice, a `Span`,
a view held by a closure or a generator) or an element reference
outliving its buffer when a temporary dies or the container grows or is
rebound through a call, an alias or a nested function -- MIR warns for
some of these shapes, not all (they are retired by the MIR ownership
checker, not patched piecemeal); a fact kept past a write the analysis
does not see, which can skip a None or divide-by-zero check;
`isinstance(x, int)` is False for a fixed-width int or a `bool`, which
also steers which `@overload` copy runs, and some overload sets pick the
wrong variant; constructor field initialization in an order CPython
would not run; an `@error_return` call skipping an enclosing handler or
`finally`; int/float comparisons beyond 2**53; some bindings and
builtins copy where CPython shares; some inferred integer widths
truncate a later wider value; generator and asyncio cleanup on
exceptions and cancellation; comparing two string literals
(`"ab" > "aa"`); and `Arc` handing a mutable payload to two threads.
Loud: accepted programs that fail the C++ build (tuple, Optional/union
and generic forms); shapes without a lowering, rejected at the line;
tuple reference elements at consuming positions.

## 0.6.2 (2026-09-30)

Licensing and packaging patch release. The compiler, runtime and library
are unchanged from 0.6.1.

- **TurboPython is licensed under Apache 2.0 with the LLVM Exceptions.**
  Earlier releases declared no license. `LICENSE` and `NOTICE` ship in
  the wheel and the sdist. The exception covers the runtime and library
  code compiled into every program, so a binary built with `tpy` can be
  distributed without carrying the license text or a notice. The bundled
  third-party libraries (PCRE2, zlib, Mbed TLS, date, the Mozilla CA
  bundle) keep their own licenses; `NOTICE` lists them.
- **Package metadata**: the PyPI summary describes the project as a
  Python-to-C++ compiler, and the project page gains documentation,
  issue-tracker and changelog links.
- **README** (the PyPI project page) reworked: it opens with a status
  note and a runnable example, states the C++23 compiler requirement in
  the installation section, shows the ownership copy warning, and adds
  Status and License sections. The build-cache and signal details moved
  to the documentation site.

## 0.6.1 (2026-09-29)

REPL start-up patch release.

- **`tpy` no longer dies before its first prompt** on an interpreter whose
  `readline` is editline -- uv-managed CPython 3.13+ (what `uv tool
  install tpy-lang` gets) and macOS Pythons. Editline rejects a history
  file GNU readline wrote with `EINVAL`, and GNU readline reads editline's
  file literally; the REPL now loads such a file line by line, so the
  history survives switching interpreters, and a history file it cannot
  write at exit is skipped instead of printing a traceback.

## 0.6.0 (2026-09-29)

378 commits since 0.5.1. A breaking release -- read Migration first.

### Migration

- **Scalar type names are lowercase**: `Int8`..`Int64` -> `int8`..`int64`,
  `UInt8`..`UInt64` -> `uint8`..`uint64`, `Float32` / `Float64` ->
  `float32` / `float64`, `Char` -> `char`. No aliases. `float64` stays
  an alias of `float`, `int` stays BigInt, and `String` / `StrView` /
  `BigInt` and the protocol names keep CapWords. `--default-int` takes
  `int32` / `int64` / `BigInt`. `from tpy import Int32` fails at the
  import line with `'Int32' not found in module 'tpy'`, and a bare old
  name fails with `Unknown type: Int32`.
- **`typing.overload` vs `tpy.dispatch`**: `typing.overload` keeps only
  CPython's form -- bodyless stubs plus one implementation. Same-named
  variants that each carry a body (or are `@native` / `@cpp_template`)
  use `@tpy.dispatch`. The old inferred form ran under CPython only
  through a `sitecustomize` patch and failed mypy/pyright.
- **One numeric type per value**: an inferred join of an int and a float
  -- `a if c else 2.5`, `[1, 2, 2.5]`, a local rebound from int to float,
  an aug-assign accumulator -- is a compile error with a fix-it (write
  `2.0`, `float(a)`, or annotate). A declared `float` slot converts.
  Previously the int was widened to a double (`3.0` where CPython prints
  `3`) or the float truncated.
- **Build flags**: `-O` / `--release` are gone. Every build is optimized
  (`-O3`); `--debug` selects `-g -O0`.
- **`@error_return` exception classes** derive directly from `Exception`
  and are not subclassed; they carry only the fields they declare (a
  `message: str` field gets `__str__`) and are returned as plain values,
  not thrown objects. `@export` on one is refused.
- **Generators are single objects**: a second name aliases the generator
  as in CPython, and a name holding a generator is bound once --
  `g = gen(); ...; g = gen()` is refused. `tpy.copy(g)` is a compile
  error; moving a started coroutine is a compile error, moving a started
  generator a run-time panic.
- **Parent initializer rule**: a subclass `__init__` that skips a base
  initializer warns and the base is value-initialized; a skipped base
  with no default constructor, or a late or nested base-init call, is
  an error.
- **C-linkage signatures** (`binding="C"`) accept only C-representable
  types: `str`, `int` (BigInt), `bytes`, containers, TPy classes, C
  structs by value and `*args` are rejected at the declaration instead
  of emitting a signature no C caller can call; `Ptr[T]` and a
  pointer-form Optional pass.
- **C++ spellings that `@native` companion code sees**: `bytes`,
  `bytearray` and `String` are `::tpy::Bytes`, `::tpy::ByteArray` and
  `::tpy::String` (were `std::vector<uint8_t>` / `std::string`); a
  `bytes` parameter and `BytesView` are `::tpy::BytesView` (was a
  `std::span`); unions are `::tpy::Union<...>` (was `std::variant`). A
  `bytearray` at an owning `bytes` slot is an error asking for
  `bytes(...)`.
- **Newly refused, each with a located error**: an `int32` parameter
  rebound to a wider or float value; an ill-typed or out-of-range
  parameter or field default (`int8 = 200` used to wrap); a float
  pattern or out-of-range literal pattern on an int subject. The dev flag
  `--thir-codegen` is gone.
- **Body codegen is THIR only**: the AST body emitter is deleted. A shape
  THIR cannot lower is a located compile error (`not yet supported by
  C++ code generation (<tag>)`), never a silent fallback. The everyday
  shapes came back through seven reject batches; the residue is in
  `BUGS.md`.
- **`examples/` left the repository**: the programs live in the
  `tpy-examples` repo, verified against a pinned compiler there.

### Language and compiler

- **Enums**: methods on enum bodies -- instance, `@property`,
  `@staticmethod`, `@classmethod`, generic and `@error_return` -- called
  through a member, the type, and across modules.
- **`@classmethod`** with `cls` bound to the defining class: `cls(...)`
  as an alternate constructor returning `Own[Self]`, `cls.CONST`. (A bare
  `-> Self` return is still rejected at the call site,
  `BUGS.md#classmethod-self-return-call-rejects`.)
- **Value types**: a user `ValueType` record binds, passes, returns,
  yields and is captured like a scalar at every position; a slot
  declared before its first value is default-constructed.
- **Generators and async frames**: one frame emitter for every generator
  (the single-yield peephole is gone); a frame `for` evaluates its
  source once and keeps it alive; view params, `*args` packs, `@dynamic`
  params, constructor defaults and cross-module delegation ride the
  frame; generator expressions lower to frames; `close()` unwinds like
  CPython. Faster: unchecked slots, inline `__next__`, `StopIteration`
  as a plain value -- up to 2x on consumer-heavy loops.
- **Tuples**: the U1 silent-divergence unit -- unpack borrows a live
  source, a borrowed element at an owning slot copies and warns, frame
  temp elements, iterator copies and lending fixed; a tuple-of-references
  global is pointer slots (`docs/TUPLE_COMPLETION_PLAN.md`).
- **Unions and buffers**: `::tpy::Union` is one type for the value and
  storage forms and owns Python's comparison rule, at a borrow too;
  `String`, `bytes`, `bytearray` and `BytesView` are distinct C++ types.
- **Binding rules**: sibling `match` arms and `except` handlers join a
  local's bindings; one definite-assignment rule for loop clauses and
  heads; loop-body locals read after the loop; `elif`-arm locals,
  nested-def shadowing and loop-variable reuse; an inferred local's type
  hints its rebind; a lambda borrow rooted in a body temporary is
  rejected; parameter and field defaults (including `Literal[...]`
  slots) are checked against their slot at the `def`.
- **Liveness**: exception edges, jumps, zero-trip loops and `finally`
  are modelled, so reads after them keep a value live instead of being
  moved from; a comprehension or genexpr that grows its source warns; a
  `for` over a genexpr takes a loan on its source.
- **Match**: three internal errors and two miscompiles on valid Python
  fixed; capture rebinds write through; a capture's mutation is credited
  to the subject.
- **Lambdas and callables**: the body is typed from its slot; class
  names as callables; indexed callable fields invoke.
- **Builtins and syntax**: `input(prompt)`, EOF raises `EOFError`;
  `repr()` for `bytes`, `bytearray` and `range`; bare `return` is
  `return None`; a `global`-declared walrus target; `except` / `with` /
  `match` captures over a same-named class.
- **Fixed for the cut**: an un-narrowed Optional container element read
  keeps its null check (it segfaulted while the warning promised one); a
  generator assigning a local in `finally` no longer crashes the
  compiler; a guarded scalar `match` arm no longer falls into the
  default arm; a generic function instantiated at `float` compiles; an
  unknown name imported from `tpy` is refused at the import.
- **Analysis-only MIR**: a bounded MIR over storage regions, holder
  liveness and retained references, inspectable with `--dump-mir`; no
  effect on generated code yet.
- A defaulted parameter before a required keyword-only one
  (`def f(a, b=10, *, c)`) builds; the default is materialized at the
  call site.

### Generated C++

Readable by design: with `--emit-source` each definition's Python source
is echoed as one block above its C++, temporaries are numbered per
function, member-init lists render one per line, frame structs are
ordered by what they embed, and same-module free calls are
namespace-qualified so ADL cannot hijack them.

### CPython extension authoring

`Optional[T]` crosses the `@export` boundary (params, returns and
value-form fields); defaults, keyword-only and positional-only params
cross, a defaulted one ahead of a required keyword-only one included;
docstrings cross as `__doc__` for functions, classes and enums;
extensions link on macOS.

### Library

- **`zlib` and `gzip`** over a vendored zlib
  (`--zlib=bundled|system|auto|none`).
- **`termios` and `tty`**; `os.set_blocking`, `os.get_blocking`,
  `os.openpty`.
- **`math`**: `log`, `sqrt` and the other domain-checked functions raise
  CPython's `ValueError` / `OverflowError` again.

### Tooling and testing

- Build: the precompiled header is stamped and `--rebuild` refreshes
  it; CLI interrupts are handled.
- Diagnostics are linted for C++ and internal names.
- Testing: `pytest-hosts` runs one xdist session over local and remote
  hosts; ccache is shared across worktrees; worksteal distribution;
  host-independent collection order.
- Nightly containers run at low CPU priority; `_buildinfo` is stamped
  off-tree; sdists walk only packaged directories.

### Known issues

Tracked in `BUGS.md`. The classes that matter most: a value moved at its
last use while an alias, a closure or a `finally` still reads it, and a
rebind of a container local that a multi-hop alias or a live `for`
points into (use-after-free shapes -- retired by the MIR ownership
checker, not patched piecemeal); a view can outlive its source (a
loop element over a temporary, a closure field write, a temporary
hoisted into a frame); call arguments and operands are evaluated in the
C++ compiler's order, not left to right; tuple reference elements at
consuming positions reject; THIR shapes without a lowering arm reject,
located; a variable-free int constant at a method argument is evaluated
at run time and can trap; the C-ABI allow-list overshoots; a `match`
on a literal enum member rejects.

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
