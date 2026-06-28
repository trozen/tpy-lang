# Feature Roadmap

Major features and design decisions for TurboPython, organized by theme.
Each entry captures the design impact, current state, dependencies, and rough effort.

Effort scale: **S** (days), **M** (1-2 weeks), **L** (2-4 weeks), **XL** (4+ weeks).

Status convention (leading marker in the Status column):
- 🚧 in progress / partial (some shipped, more to go)
- 🆕 not started
- 🔬 research (design phase, no implementation yet)
- Plain `Done` (or `Done (scope qualifier)`) means the declared scope is complete.

For tactical items see `TODO.md`; for known compiler defects see `BUGS.md`.
For current feature status, see `LANGUAGE_FEATURES.md`.

---

## Implementation Roadmap

### Phase A: Quick Wins (unblock real programs)

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| A1 | Final constants | S | Done | [I](#final--constant-globals) |
| A2 | Type aliases | S | Done | [I](#type-aliases) |
| A3 | Generic Optional codegen fix | M | Done | [I](#generic-optional-codegen-fix) |
| A4 | String ownership (context-dependent str) | M | Done | [I](#string-ownership) |
| A5 | Enums | M | Done | [I](#enums) |
| A6 | Keyword args + default values | M | Done | [VII](#keyword-arguments-and-default-values) |
| A6b | Generic default values (`T()`, instantiation validation) | S | Done | [VII](#generic-default-values) |
| A7 | `__bool__` protocol | S | Done | [VII](#__bool__-protocol) |
| A8 | Constructor init-list: warn/error on branching field assignment | S | Done | [II](#constructor-init-list-for-branching-init) |
| A9 | Iterable[T] protocol | M | Done | [I](#iterablet-protocol) |
| A10 | Tuple type + unpacking | M-L | Done | [I](#tuple-type) |
| A10a | Reference elements in tuples | M | Done | [I](#reference-elements-in-tuples) |
| A10b | Lazy list repeat (`[val]*N`) | S | Done | [I](#lazy-list-repeat-valn) |
| A11 | dict type | L | Done | [VII](#dict-type) |
| A12 | Mutable `Span[T]` + `Span[readonly[T]]` + `__span__` protocol | M | Done | [I](#mutable-span--spanreadonlyt--__span__-protocol) |

### Phase B: Polymorphism Foundation

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| B1 | Box[T] heap ownership | S-M | Done | [II](#boxt----heap-ownership) |
| B2 | Union types / ADTs | L | Done | [I](#union-types--algebraic-data-types) |
| B3 | Match/case | M-L | Done | [VI](#matchcase-with-pattern-matching) |
| B4 | Dynamic dispatch -- @dynamic protocols | L | Done | [II](#dynamic-dispatch-dynp) |
| B5 | Per-method type parameter bounds | S-M | Done | [I](#type-parameter-bounds----per-method) |
| B6 | `# tpy:` directives | S-M | Done | [I](#tpy-directives) |
| B7 | Float32 type | S | Done | [I](#float32-type) |
| B8 | Dataclasses | M | Done (Phase 1; auto `__eq__`/`frozen`/`__hash__` pending) | [VII](#dataclasses) |
| B9 | List comprehensions | M | Done | [VI](#list-comprehensions) |
| B10 | `@overload` dispatch flattening | M | Done | [VII](#overload-dispatch-flattening) |
| B11 | List slicing | M | Done | [VII](#list-slicing) |
| B12 | `Self` type | S | Done | [I](#self-type) |
| B13 | Bi-directional type inference | M | Done | [I](#bi-directional-type-inference) |
| B14 | `Optional[StaticProtocol]` codegen | S | Done | [II](#optionalstaticprotocol-codegen) |
| B15 | `isinstance` on static protocols (`if constexpr` + narrowing) | M | Done | [II](#isinstance-on-static-protocols) |
| B16 | `for/else`, `while/else` | S | Done | [VI](#forelse--whileelse) |
| B17 | Built-in / conditional protocol conformance | L | 🔬 Research | [II](#built-in--conditional-protocol-conformance) |

### Phase C: Error Handling + Effects

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| C1 | Exception model (design) | M | Done | [III](#exception-model-tryexceptraise) |
| C2 | Exception implementation | L | Done | [III](#exception-model-tryexceptraise) |
| C3 | @noalloc enforcement | L | 🚧 Parsed only | [IV](#noalloc-enforcement) |
| C4 | Effect framework (@nothrow, @pure) | M-L | 🆕 Not started | [IV](#effect-system-generalized) |
| C5 | Cyclic dependency handling | L | Done (v1 conservative) | [V](#cyclic-dependency-handling) |
| C6 | `@error_return` annotation | M | Done | [III](#error_return-annotation) |

### Phase D: Functional + Python Compat

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| D1a | `Fn` type + lambda expressions (including captures) | M | Done | [VI](#closures--nested-functions) |
| D1b | `Callable` type (`std::function`, type-erased) | M | Done | [I](#callable--function-pointer-types) |
| D2 | Named function references as values | S | Done | [I](#callable--function-pointer-types) |
| D3 | f-strings | M | Done | [VII](#f-strings) |
| D4 | with statement | M | Done | [VI](#with-statement-context-managers) |
| D5 | Nested `def` with captures, `nonlocal` | M-L | Done | [VI](#closures--nested-functions) |
| D6 | Properties (@property) | M | Done | [VII](#properties) |
| D7 | Literal types (Literal[...]) | M | Done (overload + narrowing surface) | [III](#literal-types) |
| D8 | `@override` decorator | S | Done | [VII](#override-decorator) |
| D9 | set type | L | Done | [VII](#set-type) |
| D10 | bytes type | M | Done | [VII](#bytes-type) |
| D11 | Dict comprehension | S-M | Done | [VI](#dict-comprehension) |
| D12 | Set comprehension | S | Done | [VI](#set-comprehension) |
| D13 | Generator expressions | M | Done | [VI](#generator-expressions) |
| D14 | Walrus operator (`:=`) | S-M | Done | [VI](#walrus-operator) |
| D15 | `Any` type | M | Done | [I](#any-type) |
| D16 | Dynamic attributes (`__getattr__`/`__setattr__`/`__delattr__`) | M-L | Done | [VII](#dynamic-attributes) |
| D17 | `*args` (variadic positional arguments) | M | Done (homogeneous) | [VI](#args--kwargs) |
| D18 | `**kwargs` (variadic keyword arguments) | M-L | Done | [VI](#args--kwargs) |
| D19 | Recursive type aliases | M | Done (non-generic, generic non-recursive, and generic recursive) | [I](#recursive-type-aliases) |
| D20 | Mutual recursion (cross-type cycles) | M-L | Done (same-module) | [I](#mutual-recursion) |
| D21 | TypedDict | M | Done | [VII](#typeddict) |
| D22 | Multiple inheritance (mixins) | L | Done | [VII](#multiple-inheritance) |
| D23 | Nested classes | M | Done | [VII](#nested-classes) |
| D24 | Class-level constants (`Final` + `ClassVar`, `@native` extern, MRO, generics) | L | Done | [VII](#class-level-constants) |

### Phase E: Advanced Safety

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| E1 | Send/Sync markers | S-M | 🚧 Phases 1-3 done (markers + wrappers + diagnostics); Phases 4-6 (enforcement) in [`docs/SEND_SYNC_DESIGN.md`](SEND_SYNC_DESIGN.md) | [IV](#thread-safety-markers-send--sync) |
| E2 | Container mutation during iteration | S-M | Done | [IV](#container-mutation-during-iteration) |
| E3 | del statement | S-M | Done | [VI](#del-statement-explicit-destruction) |
| E4 | Ptr escape analysis | XL | 🚧 Partial | [IV](#ptrt-escape-analysis--lifetime-tracking) |
| E5 | Auto-detect readonly | M-L | Done | [IV](#auto-detect-readonly-from-method-body) |
| E6 | Dead code detection | M | 🆕 Not started | [VIII](#dead-code-detection) |
| E7 | Error recovery / multi-error diagnostics | L | 🚧 Partial (infra only) | [VIII](#error-recovery--multi-error-diagnostics) |
| E8a | Drop flag (`__tpy_owned_`) | S | Done | [IV](#move-safe-destructors) |
| E8b | Sentinel field optimization (eliminate drop flag) | M | 🆕 Not started | [IV](#move-safe-destructors) |
| E9 | Integer range analysis / bounds check elision | M-L | 🚧 Partial | [VIII](#integer-range-analysis--bounds-check-elision) |

### Phase F: Compile-Time Power

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| F1 | Compile-time evaluation | XL | 🆕 Not started | [V](#compile-time-evaluation-constexpr--comptime) |
| F2 | Macro system | XL | 🚧 Phases 1/2/7 done; function macros + quote templates shipped | [V](#macro-system--metaprogramming) |
| F3 | Generators / yield | L-XL | Done | [VI](#generators-yield) |
| F4 | Typestate | XL | 🔬 Research | [VIII](#typestate-object-lifecycle) |
| F5 | Self-interpret (TPy eval in tpyc) | XL | 🆕 Not started | [V](#self-interpret-tpy-eval-in-tpyc) |
| F6 | Alternative backends | XL | 🆕 Not started | [V](#alternative-backends) |
| F7 | Decorator definitions in library code | M-L | 🚧 Phase 1 done | [V](#decorator-definitions-in-library-code) |
| F8 | Compile-time conditional compilation / build profiles | M | 🆕 Not started | [V](#compile-time-conditional-compilation--build-profiles) |
| F9 | Frontend plugin API (DSL / non-Python source) | XL | ✅ v1 shipped | [V](#frontend-plugin-api) |
| F10 | IR migration (THIR / MIR) | XL | 🆕 Not started | [V](#ir-migration-thir--mir) |
| F11 | Compile-time module parameters (`compile_param`/`instantiate`) | L | 🚧 Phase 0 POC (CPython); sema/codegen not started | [V](#compile-time-module-parameters) |

### Phase G: Concurrency (Future)

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| G1 | async/await + minimal asyncio | XL | 🚧 v1 + v1.5 (M1-M11) + v2 M1/M2 shipped (epoll reactor + streams); multi-threaded executor + async generators remain -- see [`docs/ASYNC_PROGRESS.md`](ASYNC_PROGRESS.md); design in [`docs/ASYNC_DESIGN.md`](ASYNC_DESIGN.md) | [IX](#asyncawait-or-alternative-model) |
| G1.5 | asyncio runtime port C++ -> TPy | M | Done -- runtime port complete (Phases 0-4 + v1.2 steps 1-7); the executor, run loop, scheduling, sleep, and type-erasure are pure TPy, leaving only a small `async.hpp` (CancelledError + resume-case helper). See [`docs/ASYNC_PROGRESS.md`](ASYNC_PROGRESS.md#v1x-milestone-asyncio-runtime-tpy-port-must-precede-v15). | [IX](#asyncawait-or-alternative-model) |
| G2 | Channels | L | 🟡 SPSC done (Send/Sync Phase 4) | [IX](#channels) |

Phases are not strictly sequential -- items from different phases can be interleaved
based on what's most needed. E1 (Send/Sync markers) is recommended early regardless
of phase, to avoid costly retrofitting when concurrency arrives.

### Phase H: Interop & Distribution (Future)

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| H1 | CPython interop (extension mode + embedding hooks) | XL | 🚧 v1.0 in progress (free funcs: all scalars + void return + str/bytes copy-in; local `.so` build) -- design in [`docs/CPYTHON_INTEROP.md`](CPYTHON_INTEROP.md) | [XI](#cpython-interop) |
| H2 | Project / build / dependency tooling (`tpx` porcelain) | XL | 🔬 Research -- design in [`docs/PROJECT_TOOLING_DESIGN.md`](PROJECT_TOOLING_DESIGN.md) | [XII](#project--build--dependency-tooling) |
| H3 | Windows support + target-aware conditional compilation | XL | 🔬 Research -- needs design (see note below) | -- |

**H3 -- Cross-platform / Windows.** Linux + macOS/*BSD work today via a
three-layer pattern (C++ `#ifdef` in runtime `.cpp`, `native_global` for
divergent constants, host-filtered `# tpy: include/link(platform=)`); the
macOS reactor (kqueue) / signal (self-pipe) backend and the platform-correct
`socket`/`os` constants are the worked examples. See
[LANGUAGE_FEATURES.md "Platform Support"](LANGUAGE_FEATURES.md) for the
pattern and rule (keep stdlib `.py` platform-neutral; push divergence into
the C++ facade). Two structural gaps must be designed before Windows lands,
**neither built speculatively**:
1. **Target-awareness.** `platform=` filtering keys on the build host's
   `sys.platform`, not a target -- so cross-compilation picks the wrong
   includes/links. A `--target` notion (and routing `#ifdef`/`native_global`
   selection through it) is the prerequisite for cross-builds.
2. **Source-level platform conditional.** No compile-time `PLATFORM` constant
   with dead-branch elimination, so stdlib `.py` can't branch on platform in
   *logic* (only constants/syscalls diverge today). Worth adding only when
   logic genuinely needs to diverge.

The Windows runtime work itself (Winsock2 socket backend, IOCP reactor,
`WSAStartup`/`WSACleanup` lifecycle, `-lws2_32`) slots into the existing
`#ifdef _WIN32` pattern -- scoping a real port will surface exactly which of
the two structural pieces it needs, rather than building them up front.

### Future Extensions (not planned near-term)

Extensions to completed features. Not actively planned but tracked here so they
don't get lost.

| Feature | Parent | Notes |
|---------|--------|-------|
| Bare `Final` | A1 | Infer type from initializer (`x: Final = 1`) |
| Final arithmetic initializers | A1 | Needs constexpr checked ops or plain-op codegen |
| Final for class/local constants | A1 | Extend beyond module-level |
| Final for non-primitive types | A1 | `Final[list[T]]` -- needs deep immutability |
| Cross-module Final references | A1 | Use imported Finals in initializers |
| Virtual methods in inheritance | B4 | `@dynamic` protocols cover the use case; class-based virtual dispatch is a distant future consideration |
| Diamond multiple inheritance | D22 | Currently rejected outright; users redirect to `@dynamic`. Would need C++ `virtual public` codegen and a sema design for shared-subobject field/init semantics. |
| Full cooperative `super()` chain | D22 | v2.3 ships single-hop MRO-aware resolution; full Python cooperative super (where `super()` inside an ancestor method dispatches via the runtime instance's MRO) would need per-most-derived-type method specialization or runtime MRO -- both fight TPy's static dispatch. |
| `__exit__` exception args + return | D4 | Pass `(exc_type, exc_val, exc_tb)` to `__exit__`, return `True` to suppress. Exceptions are done (C1/C2); needs runtime type-info passing |
| `@contextmanager` decorator | D4 | `yield`-based context managers via `contextlib.contextmanager`. Generators done (F3); now implementable |
| `async with` | D4 | `__aenter__`/`__aexit__` async context managers. Blocked on async (G1) |
| IIFE init-list for branching `__init__` | A8 | Generate `field([&]{ if (...) return x; else return y; }())` in member init-list, removing the need for helper functions. Handles all types including `@nocopy`/const fields. |
| `type[T]` parameter type | -- | Compile-time-only phantom type representing a class. `type[T]` params generate no runtime code; `cls(args)` desugars to `T(args)`. Enables factory functions (`def create[T](cls: type[T], ...) -> T`), deserialization (`from_json(Point, data)`), and CPython-compatible patterns where classes are passed as values. |
| `@classmethod` | above | Sugar on top of `type[T]`. `cls` parameter implicitly typed `type[Self]`. Desugars to `@staticmethod` with implicit `T: Self` type param. For CPython compatibility -- idiomatic TPy equivalent is `@staticmethod def create[T: MyClass](...) -> T`. |

---

## I. Type System Foundations

### Union Types / Algebraic Data Types

Tagged unions: `A | B` as a first-class type (beyond `T | None`).

```python
Shape = Circle | Rect

def area(s: Shape) -> float:
    if isinstance(s, Circle):
        return 3.14159 * s.radius * s.radius
    elif isinstance(s, Rect):
        return s.width * s.height
```

**Why it matters**: Two polymorphism models -- ADTs for closed hierarchies (stack-allocated,
`@noalloc`-compatible, exhaustive matching) and `Dyn[P]` for open extension (heap-allocated,
vtable-based). Most languages pick one; having both is powerful.

The TPy compiler itself has 824 `isinstance` calls across 35 files. ADTs with exhaustive
match are the natural replacement, and a prerequisite for self-hosting via tag dispatch.

Maps to `std::variant` in C++. No heap allocation, no vtable. Compatible with `@noalloc`.

**Current state**: Done (core). `A | B | C` annotations, `isinstance` narrowing (if/elif/else
chains with negative narrowing), `assert isinstance`, while-loop narrowing, assignment
narrowing, type aliases, `A | B | None` with `std::monostate`. Maps to `std::variant`.
See `docs/UNION_TYPES_DESIGN.md` for full phase list and future extensions.

Remaining extensions (not blocking "Done" status): equality on unions, `isinstance(x, (A, B))`
tuple form, common-method dispatch, generic unions, recursive unions. Match/case and
exhaustiveness checking are tracked separately (B3).

Low-prio extension -- **parameterized-generic isinstance narrowing** (`isinstance(d, dict[str, V])`
or an alias of it): bare `isinstance(d, dict)` already narrows a union to its single
dict/list/set member, matching CPython. The parameterized form is currently *rejected* because
CPython raises "cannot be a parameterized generic" at runtime -- accepting it would be a TPy-only
divergence. It would only add value for disambiguating two same-kind members
(`list[int] | list[str]`), which CPython can't do either (type erasure). If pursued, it needs the
divergence-acknowledgement machinery (diagnostic + explicit opt-in spelling + doc note), so it's a
`/tpy-add-feature` design pass, not a free win. Technical shape noted in `BUGS.md` (the bare-generic
isinstance residual entry).

**Dependencies**: Enums (done). Match/case (B3, consumer of unions).

**Effort**: L (done for core; future extensions are incremental)

---

### Tuple Type

Fixed-size heterogeneous container. Foundation for multiple returns and destructuring.

```python
def divmod(a: int, b: int) -> tuple[int, int]:
    return (a // b, a % b)

q, r = divmod(17, 5)
```

**Why it matters**: Tuples are pervasive in Python (used in 44 files of the compiler source).
Multiple return values are the most common use case. Destructuring assignment (`a, b = f()`)
is a core Python pattern. Also needed for dict iteration (`for k, v in d.items()`).

Maps to `std::tuple<T...>` in C++. Design questions: variadic type params, named tuples,
interaction with ownership (move each element on destructure?).

**Current state**: Done (core). Tuple type, unpacking, and context-dependent reference
elements are all working:
- `tuple[T1, T2, ...]` annotations and type inference
- Tuple literals `(a, b)`, single-element `(a,)`, nested tuples
- Compile-time element access `t[0]`, `t[-1]` via `std::get<N>`
- Function parameters and return types
- Generic type inference (`tuple[T, T]` infers `T`)
- Equality comparison (`==`, `!=`) with element-wise type checking
- Python-style printing `(1, 'hello')`, `(42,)` for single-element
- Immutability enforced: `t[0] = x` is rejected by sema
- Context-dependent reference elements (see below)
- Tuple unpacking: `a, b = expr` with fresh vars, reassignment, and `_` discard
- For-loop unpacking: `for a, b in items` (desugared at parse time)

**Remaining**: `Optional`/`Union` elements, ordering comparisons (`<`, `>`),
nested unpacking (`a, (b, c) = ...`).

**Dependencies**: None (core tuple type and unpacking done). Dict iteration needs both
tuple and dict.

**Effort**: Done (core); S-M remaining for edge cases

---

### Reference Elements in Tuples

Currently tuple elements must be value types or wrapped in `Own[T]`. This prevents
surprising implicit copies when returning objects from functions (e.g. changing
`-> Point` to `-> tuple[Int32, Point]` would silently copy the Point).

The goal is to support bare reference types in tuples. The key observation is that
TPy already has context-dependent semantics for `T`:

| Context | `Point` means | C++ |
|---------|---------------|-----|
| Parameter | mutable reference | `Point&` |
| Parameter (`@readonly`) | const reference | `const Point&` |
| Return | mutable reference | `Point&` |
| Field | owned | `Point` (member) |
| List element | owned | `Point` (in vector) |

`T` is reference in params/returns and owned in fields/containers -- no `Ref[T]` or
`Own[T]` needed, the context decides. There is precedent for context-dependent type
mapping: `str` resolves to `std::string` or `std::string_view` depending on context
(`PendingStrType`).

**Proposed approach: Context-dependent tuple elements** -- tuple elements follow the
same context rules as standalone `T`:

```python
# Return context: Point is reference (like -> Point)
def find(items: list[Point], id: Int32) -> tuple[Point, bool]:
    ...  # -> std::tuple<const Point&, bool>

# Field context: Point is owned (like field: Point)
class Cache:
    last: tuple[Point, bool]  # std::tuple<Point, bool>

# Container context: Point is owned (like list[Point])
results: list[tuple[Point, bool]]  # std::vector<std::tuple<Point, bool>>

# Own[T] still works for explicit ownership in returns
def make(x: Int32) -> tuple[Own[Point], bool]:
    return (Point(x, x), True)  # std::tuple<Point, bool>, owned copy
```

This gives zero-copy tuple returns (consistent with `-> T`), works in containers
and fields (consistent with `list[T]` and field declarations), and requires no new
type annotations. `Own[T]` in tuples remains meaningful: it forces value semantics
in a return context where the default would be reference.

**Implementation approach**: Similar to `PendingStrType`, reference-type tuple elements
could use a pending representation that resolves during sema/codegen based on context.
`TupleType.to_cpp()` would need context to choose between `const T&` (reference) and
`T` (owned) for each element.

**Resolved design questions**:
- Local variables: `t = (p, True)` -- reference (like `p2 = p`). Declaration uses `auto`,
  tuple literal captures lvalue record elements by reference, rvalue elements by value.
- Function parameters: `f(data: tuple[Point, bool])` -- tuple passed by const ref;
  elements follow the tuple's own representation (determined when the tuple was created).
- Generic code: `def f[T]() -> tuple[T, bool]` -- uses `tpy::val_or_ref_t<T>`
  trait that resolves at C++ instantiation time.
- Dangling references: per-element dangling check on tuple return literals prevents
  returning references to locals/temporaries.
- Readonly methods: `@readonly` methods returning tuple with reference elements use
  `const T&` for reference elements (matching the const method semantics).

**Current state**: Implemented. Context-dependent tuple element semantics are fully working.

**Effort**: M (completed)

---

### Mutable Span + `Span[readonly[T]]` + `__span__` Protocol

Three related changes that make Span consistent with the rest of the language and enable
zero-cost user-defined iteration for contiguous containers.

#### Part 1: Mutable `Span[T]` + `Span[readonly[T]]`

Make `Span[T]` mutable by default, matching the language convention (everything is mutable
by default). Use `Span[readonly[T]]` for explicit const views.

| TPy | C++ | Consistent with |
|-----|-----|-----------------|
| `Span[T]` | `std::span<T>` | `Ptr[T]` -> `T*` |
| `Span[readonly[T]]` | `std::span<const T>` | `Ptr[readonly[T]]` -> `const T*` |

Existing coercions (`list[T]` -> `Span[T]`, `Array[T,N]` -> `Span[T]`) continue to work.
Functions that don't need to mutate elements use `@readonly` on the function, as with any
other parameter type.

**Breaking change**: Current `Span[T]` maps to `std::span<const T>`. All existing uses
become mutable. Acceptable at POC stage -- test snapshots need updating.

#### Part 2: `__span__` Protocol (Zero-Cost User-Defined Iteration)

```python
class ArrayList[T, N: int]:
    def __span__(self) -> Span[T]:
        return Span[T](self._storage.ptr(), self._size)
```

A dunder protocol for user-defined types to opt into zero-cost iteration via Span.
The user writes a single `__span__` method. The compiler detects it and uses it for:

- **For-loops**: `for item in container` calls `__span__()`, then iterates via C++
  range-based for. `__span__` takes precedence over `__iter__` (warning emitted if both).
  ```cpp
  auto& __obj = container;
  auto __span = __obj.__span__();
  for (auto& x : __span) { ... }
  ```
- **Implicit coercion**: passing a type with `__span__` where `Span[T]` or
  `Span[readonly[T]]` is expected calls `__span__()`. A `Span[readonly[T]]` return cannot
  coerce to mutable `Span[T]`.
- **Dual overloads**: When `__span__` returns mutable `Span[T]`, the compiler generates
  two C++ overloads -- non-const (user body, returns `span<T>`) and const (thin wrapper,
  returns `span<const T>`). When returning `Span[readonly[T]]`, only a const overload is
  generated. This follows the `__getitem__` dual-overload pattern.

**CPython compatibility**: `__span__` is not meaningful in CPython. The `Spannable[T]`
protocol doubles as a CPython mixin base class that auto-generates `__iter__` from `__span__()`.
User types inheriting `Spannable` get iteration for free in both runtimes.

**`Spannable[T]` protocol**: Readonly protocol for types with `__span__()`. Enables
generic functions accepting any span-producing type
(`def sum_all(c: Spannable[Int32])`). Builtins (list, Array, Span, Span[readonly[T]]) conform via `extends`. User types conform structurally -- `__span__() -> Span[T]`
satisfies the readonly protocol via covariant return. `Spannable[T]` values coerce to
`Span[readonly[T]]` and support for-loop iteration.

**Why it matters**: Previously user-defined types could only iterate via `__iter__`/`__next__`,
which allocates an iterator object and generates a loop with `__next__()` calls.
Builtin types use `NativeIterable[T]` for zero-cost C++ range-based for, but this protocol
was not user-extensible. `__span__` bridges the gap for any contiguous container with no
overhead. `ArrayList[T, N]` now uses `__span__` instead of `__iter__`/`ArrayListIter`.

**Current state**: Part 1 done. Part 2 done. `Spannable[T]` protocol done.

**Dependencies**: Part 2 needs Part 1 (done).

**Effort**: Part 2: S-M (sema protocol detection + codegen dispatch + iterator synthesis)

---

### Lazy List Repeat (`[val]*N`)

**Done.** `[val]*N` uses deferred resolution via `PendingListType`, with three-tier
outcome based on usage:

- **Constant count, unmutated** -> `Array[T, N]` (stack-allocated, supports subscript)
- **Variable count, unmutated** -> `ListRepeatType` (lazy `tpy::repeat_range<T>`, no allocation)
- **Variable count with subscript** -> `list[T]` (auto-promoted, repeat_range has no operator[])
- **Mutated** (e.g. `.append()`) -> `list[T]` (auto-promoted)

`ListRepeatType` conforms to `Iterable[T]`, `Sized`, `NativeIterable[T]`, and
`NativeRangeConstructible`. Inline repeat cannot be passed directly to `Span` --
assign to a variable first. Assigned variables materialize to `list[T]` when passed
to `Span` params.

Explicit annotation (`z: list[T] = [v]*N`) always produces `list[T]`.

---

### Generic Optional Codegen Fix

**Done.** `T | None` in generic contexts uses `std::optional<T>` instead of `T*`/`nullptr`.
All 33 codegen sites use `uses_pointer_repr()`. `Optional[T]` is equivalent to `T | None`.

---

### String Ownership

**Done.** Context-dependent `str` (PendingStrType): `str` resolves to `std::string` for
locals/fields and `std::string_view` for parameters. This eliminates dangling references
without introducing a separate type. String concatenation, `str()` conversions, and f-strings
all produce `std::string` and are safe to store. The explicit `String` and `StrView` types
remain available for when the user wants to control the representation.

**Dependencies**: N/A (done)

**Effort**: M (done)

---

### `memoryview` (deferred -- valuable part is lifetime-gated)

CPython-compatible `memoryview(...)` builtin. **Deferred** after a design pass: the
*safe* subset is near-redundant with `BytesView`, and the *valuable* subset is a
lifetime feature that belongs with the region work below.

What a design+spike (branch `feat-memoryview`) established, so a future attempt
starts warm:

- **Implement as pure TPy, not hand-written C++.** A `@builtin_type`-with-body
  record (no `@native`) emits a real C++ struct from its TPy body -- confirmed by
  spike; precedent is `asyncio._executor.Task`/`TaskState`. `@builtin_type` is
  pure name/registry binding, orthogonal to `@native`. (`_BLOCKED_BUILTINS` in
  `macro_loader.py` is the macro sandbox allowlist, unrelated.)
- **Read-only memoryview adds little over `BytesView`** -- same zero-copy const
  byte view, just a CPython builtin *name* and a buffer-introspection API
  (`.tobytes/.readonly/.nbytes/...`) instead of `BytesView`'s text API
  (`decode/find/split`). Only real benefit: ported code calling bare
  `memoryview(b)` compiles.
- **The new capability -- writable views over `bytearray` (`mv[i] = x` writes
  through), `.obj`, real `release()`, resize-safety -- is an ownership/lifetime
  feature**, the same root cause as Coroutine/Task borrow safety below. A
  span/`Ptr`-backed view dangles when the source local drops or the `bytearray`
  resizes; CPython prevents this via a refcounted export-lock that TPy's
  `bytes`/`bytearray` don't have. Faithful keep-alive needs either a copy (not a
  view) or `Rc`/an export-counter -- not v1-sized. Building a documented-unsafe
  writable view under a trusted CPython name is the wrong tradeoff.
- **Two storage models surfaced:** (a) `BytesView | Span[UInt8]` variant +
  runtime readonly check; (b) Codex's exporter-backed `obj:bytes|bytearray` +
  offset/length/readonly/released, deriving spans per-access -- gives cheap
  `.obj` + soft resize-detection but needs a borrowed-reference field
  (`Ptr`/`Ref[T]`) and still dangles without refcounting.
- **Open prerequisites for any attempt:** (1) bytearray must *provably* select
  the writable arm (both `bytes`/`bytearray` coerce to `BytesView`; overload
  exactness is correctness-critical -- needs a `.readonly == False` test);
  (2) the unqualified `memoryview(...)` -> record-constructor path needs
  root-causing -- no `@builtin_type`-with-body record is built via a bare
  `Name(...)` today (`Task` uses factories), so the path may not exist;
  (3) negative-index / slice normalization should reuse existing
  `bytes`/`list_slice` helpers (gets Python slice semantics for free).
- The principled generalization beyond bytes is generic `memoryview[T]` over the
  `Spannable[T]` protocol (typed buffers like `list[T]`, `Array[T,N]`), matching
  CPython's buffer-exporter set -- **not** a type-erased "any object via
  `tpy.unsafe` reinterpret" view, which diverges from CPython (it rejects
  non-buffer objects) and is unsafe for non-POD element types.

Cross-ref: shares its blocker with **Coroutine/Task borrow safety (gated on
IR/regions)** (Section IV) and the **Mutable Span + `__span__`** primitive above.

**Dependencies**: THIR/MIR region work (for the writable/lifetime-safe form);
`Ref[T]` (for the exporter-backed model).

**Effort**: M for read-only-only (low value); L for the faithful writable form
(rides on regions).

---

### Enums

**Done.** Int-based enums (`enum class` in C++) with `auto()`, `Enum.member` access,
cross-module import, `==`/`!=` comparison, `print()` support, `isinstance()` narrowing.
Stepping stone to union types and match/case.

---

### Type Aliases

**Done.** Both `Shape = A | B` and `type Shape = A | B` (Python 3.12). Maps to
`using` in C++. Cross-module import works. Not yet supported: `isinstance(x, Shape)`
on aliases, `isinstance(x, (A, B))` tuple form.

---

### Type Parameter Bounds -- Per-Method

```python
class ArrayList[T, N: int]:
    def append(self, value: Own[T]) -> None: ...          # works for any T

    def append_default[T: Default](self) -> Ptr[T]: ...   # only requires Default
```

Class-level and function-level bounds already work (`class C[T: Sized]`,
`def f[T: Comparable](...)`). The missing piece is **per-method bounds** on class
type parameters -- constraining individual methods without restricting the whole class.

The proposed syntax reuses `T` in the method's type param list. Since `T` already
exists at the class level, the compiler treats `[T: Default]` as an additional bound
on the existing `T` rather than a new type variable. This is valid Python 3.12+ syntax
(methods can have their own type param lists).

Maps to C++ `requires` clauses on individual member functions:
```cpp
T* append_default() requires std::default_initializable<T> { ... }
```

**Why it matters**: Containers like ArrayList work for any `T`, but specific methods
need additional capabilities -- `append_default()` needs `T()`, a hypothetical `sort()`
would need `Comparable`. Without per-method bounds, the choice is: constrain the whole
class (wrong) or accept opaque C++ errors when the bound is violated (bad UX).

**Current state**: Done. Methods can have their own type parameters (new params like
`[U]` or per-method bounds on class params like `[T: Comparable]`). Sema infers
method type args from arguments or accepts explicit `obj.method[U](args)` syntax.
Codegen emits method-level `template<typename U>` headers and `requires` clauses
for B5 bounds.

**Dependencies**: Type bounds (done). Built-in trait protocols like `Default` (not yet
defined -- needed for full use cases like `append_default`).

---

### Callable / Function Pointer Types

```python
def apply(f: Callable[[Int32], Int32], x: Int32) -> Int32:
    return f(x)
```

**Why it matters**: Blocks callbacks, higher-order functions (`map`, `filter`, `sorted`
with key), event handling, C interop (function pointer params), and eventually closures.
Also required for the DOOM port (Phase 2).

Design space:
- **C function pointers** (`void (*)(int)`) -- zero overhead, `@noalloc` compatible, no closures
- **`std::function<R(Args...)>`** -- supports closures, but allocates
- **Template-based** (like protocol params) -- zero overhead, supports closures, monomorphizes

Two type kinds implemented: `Fn` (template-based, zero overhead, monomorphized) and
`Callable` (type-erased `std::function`). Named function references as values also work.

**Current state**: Done. `CallableType(is_template=True)` for zero-cost callable template
params (monomorphized at each call site). `CallableType(is_template=False)` for type-erased
`std::function<R(Args...)>` (supports closures, storable in fields/containers). Named
function references can be passed as values. Lambda expressions work as both `Fn` and
`Callable`.

**Dependencies**: Done.

**Effort**: L (done)

---

### Any Type

```python
from typing import Any, cast

def process(data: Any) -> None:
    print(data)                # universal op (ops dispatch)
    if isinstance(data, str):
        print(data.upper())    # narrowed (borrow); outer Any survives
    n: int = data              # auto-coerce: runtime check, panic on mismatch
    s = cast(str, data)        # explicit checked extraction
```

Type-erased owning value cell. v1 holds any **copyable** value type or
user type. Universal operations -- `print`, `str`, `repr`, f-string,
`bool`, `==`/`!=`, `hash`, `x is None` -- work on raw `Any` via a small
per-type ops table. Where the target type is statically known (annotated
assignment, non-overloaded function arg, return, container insert), the
compiler auto-coerces with a runtime check that panics on mismatch. Method
calls, attribute access, subscript, iteration, `len`, binary operators
require narrowing first via `isinstance` (non-consuming borrow) or
`typing.cast(T, x)`.

**Why it matters**: motivating use case is **dynamic configuration /
argument systems** where the schema is determined at runtime
(config-driven CLI, plugin-registered options, schema documents). Static
declaration via macros isn't possible there. Also a migration crutch for
porting untyped Python code. Better-typed alternatives exist for several
originally-cited motivations: `json.loads` -> recursive `JsonValue` ADT;
`argparse.Namespace` -> macro API.

**Design**: see `docs/ANY_TYPE_DESIGN.md`. Highlights:
- `std::any`-backed storage + per-type `AnyOps` table (~24-32 bytes total).
- v1 is **copyable contents only**; `Own[T]` / `@nocopy` storage rejected.
- Move-only contents and binary-op auto-coerce are explicitly deferred to a
  Future Extensions table; documented rationale.
- `isinstance(x, T)` is **non-consuming**: exposes a `T const&` borrow in
  the true branch; the outer `Any` survives.
- `typing.cast(T, x)` reused as the explicit checked-extraction mechanism.
  In CPython it stays a static no-op; in TPy compiled binaries it gets
  runtime panic-on-mismatch semantics. mypy/pyright already cannot check
  casts on `Any`, panic-test cases skip the cpy phase, and the divergence
  is consistent with auto-coerce. No `lib/cpy/` shim needed.
- `==` / `!=` via ops `equals` slot (type mismatch -> False).
  `equals` / `hash` slots are conditionally generated (null if T isn't Eq /
  Hashable; runtime panic on use).
- `x is None` allowed; general `x is y` rejected at compile time.
- `Any | None`, `Any | T`, `Own[Any]` rejected as redundant.
- `@noalloc` rejection of `Any` deferred until broader `@noalloc`
  enforcement lands (parsed-only today; one-off Any rejection would be
  misleading).
- Inheritance walk on `cast` / `isinstance` deferred to v2.

**Current state**: Done. All 9 phases landed. Full feature surface:
type system, runtime header (`tpy::Any` = `std::any` + per-type ops),
into-Any codegen with view-to-owned upgrade, universal ops on raw `Any`
(`print`/`str`/`repr`/f-string/`bool`/`==`/`!=`/`hash`/`is None`),
`typing.cast(T, x)` checked extraction, non-consuming `isinstance`
narrowing (const T& borrow, outer survives), auto-coerce out (annotated
assignment, function arg, return, container insert), narrow-required
diagnostics on raw `Any` (attr/method/call/iter/subscript/len/binop/
non-None-is), and `set[Any]` / `dict[Any, V]`
support via `std::hash<tpy::Any>` + runtime hashable check.

**Dependencies**: isinstance narrowing (done). Move semantics + `@nocopy`
(done).

**Effort**: M (done).

---

### Recursive Type Aliases

```python
type JsonValue = str | float | bool | None | list[JsonValue] | dict[str, JsonValue]

value: JsonValue = json_parse(text)
if isinstance(value, str):
    print(value)
elif isinstance(value, dict):
    for k, v in value.items():
        process(v)  # v: JsonValue
```

Type aliases that reference themselves. The compiler detects the self-reference and
emits a wrapper struct around the variant instead of a plain `using` alias:

```cpp
struct JsonValue {
    std::variant<std::monostate, bool, double, std::string,
                 std::vector<JsonValue>,
                 tpy::ordered_map<std::string, JsonValue>> data;
    JsonValue(bool v) : data(v) {}
    JsonValue(double v) : data(v) {}
    // ... constructors for each alternative
};
```

This works because `std::vector<JsonValue>` and `ordered_map<..., JsonValue>` only
need `JsonValue` complete when they allocate (at runtime), not at type definition
time. The existing `isinstance` narrowing and `match`/`case` work through the
wrapper's `.value` member.

**Safety constraint**: every recursive path must go through at least one container
or pointer (`list`, `dict`, `set`, `Box`, `Optional`). Direct recursion
(`type Bad = int | Bad`) is infinite size and rejected at compile time.

**Why it matters**: Prerequisite for any tree-structured data in TPy libraries --
JSON values, ASTs, expression trees, HTML/XML DOMs, configuration trees. Also a
self-hosting prerequisite (the TPy compiler's own AST is recursive unions). Since
the goal is to implement libraries in pure TPy (not hard-coded in the compiler),
this is needed for a TPy-native `json` library to define its value type.

Mutual recursion (e.g. `Expr` referencing `BinOp` which contains `Expr` fields)
is a harder extension requiring forward declarations across types. Can be a
separate follow-up.

**Current state**: Done (non-generic). Self-referencing union aliases compile
to a C++ wrapper struct with a `.value` variant field, forwarding constructor,
and `operator==`. isinstance narrowing and match/case work through the wrapper.
Safety validation rejects direct and fixed-size recursion. The alias name is
callable as a constructor (e.g. `Tree(42)`). Annotation-driven inference works
for nested list/dict literals (`x: Tree = [1, [3, 4]]` infers as `list[Tree]`).

**Phase 1 shipped (generic non-recursive aliases)**: `type Pair[T] = tuple[T, T]`,
`type Result[T, E] = T | E`, multi-param, alias-in-alias, container element,
cross-module (short-name + qualified). Substitution happens at parse-resolution
via `substitute_type_params_structural`. Generated C++ uses the expanded body
directly; the alias has no C++-level identity. `isinstance(x, Pair)` rejected
(no runtime identity). See `docs/GENERIC_RECURSIVE_ALIASES_DESIGN.md`.

**Phase 2 shipped (generic recursive aliases)**: `type Tree[T] = T | list[Tree[T]]`
compiles and runs end-to-end via a templated wrapper struct. Parse-resolution
emits an `AliasRef(name, args)` for both in-body self-references and use sites;
an early-sema pass (`_finalize_generic_recursive_aliases`) converts them to a
dedicated `RecursiveAliasInstanceType` keyed on `(qname, type_args)`.

**Not yet supported**:
- Bounds on alias type params (`type Tree[T: Hashable] = ...`) -- rejected at
  parse time (storage shape leaves room; later addition is sema-only).
- Mutual recursion across generic aliases -- validator detects + diagnoses.
- `isinstance(x, Tree)` fold-to-True on generic recursive aliases (deferred).

**Dependencies**: Union types (done). Match/case (done for unions).

**Effort**: M (done)

---

### Mutual Recursion

```python
type Expr = Literal | BinOp | Call | IfExpr

class BinOp:
    left: Expr
    op: str
    right: Expr

class Call:
    func: Expr
    args: list[Expr]
```

Cross-type recursive cycles where a union references classes that contain the union.
This is the AST pattern -- the most important data structure for compilers, interpreters,
expression evaluators, and tree-structured domains.

**The C++ size problem**: `Expr` is a `std::variant<..., BinOp, ...>` and `BinOp`
contains `Expr` fields. The sizes are mutually dependent, and `std::variant` requires
all alternatives to be complete types. This is fundamentally circular -- pointer
indirection is required somewhere to break the cycle.

**Approach A -- Explicit `Box[Expr]` (recommended first):**

```python
from tplib import Box

type Expr = Literal | BinOp | Call

class BinOp:
    left: Box[Expr]     # heap-allocated to break size cycle
    op: str
    right: Box[Expr]
```

Generated C++:
```cpp
struct Expr;  // forward declaration

struct BinOp {
    Box<Expr> left;     // pointer indirection, Expr can be incomplete
    std::string op;
    Box<Expr> right;
};

struct Expr {
    std::variant<Literal, BinOp, Call> data;  // BinOp is complete here
};
```

User explicitly marks which fields are boxed. No hidden allocations. Matches Rust's
approach (`Box<Expr>`). `Box[T]` already exists in tplib.

**Approach B -- Implicit auto-boxing (future sugar):**

```python
class BinOp:
    left: Expr      # compiler detects cycle, auto-inserts Box
    op: str
    right: Expr
```

Compiler detects the cycle and auto-boxes the recursive fields. Emits a warning:
"field 'left' auto-boxed due to recursive type cycle". Cleaner syntax but hidden
allocation. Could be opt-in via a directive (`# tpy: auto-box`).

Approach A is more aligned with TPy's philosophy (explicit, no hidden costs).
Approach B can layer on top later.

**Implementation requires:**
- Cycle detection across type definitions in the dependency graph
- Topological sort of struct definitions with forward declarations
- C++ forward declarations emitted before the types that reference them
- `Box[T]` working with incomplete types (already the case -- Box stores a pointer)

**Why it matters**: Self-hosting prerequisite -- the TPy compiler's AST is a set of
mutually recursive types (824 isinstance calls across 35 files). Also needed for
any tree-structured library: expression evaluators, HTML/XML parsers, configuration
languages, protocol buffers. Combined with recursive type aliases (D19), this gives
TPy full algebraic data type support.

**Current state**: Done (same-module). General cycle detection across records
and type aliases. Recursive union aliases are tagged via metadata
(`recursive_union_names`); codegen emits wrapper structs and resolves expanded
unions via member-set mapping. `Box(Lit(1))` auto-coerces to `Box[Expr]` via
the wrapper's implicit constructor. Works with `isinstance`, `match`/`case`,
mixed unions (primitives + records), return values, local variables, fields
(via `Box`), and both source orderings (alias first or classes first).

**Not yet supported**:
- Cross-module mutual recursion (alias and member classes in different modules)

**Dependencies**: Recursive type aliases (D19, done). `Box[T]` (done). Union types (done).

**Effort**: M-L (done)

---

### Final / Constant Globals

**Done.** `Final[T]` for module-level constants. Emits `inline constexpr` (or
`extern const` for BigInt). Supports fixed-width ints, float, bool, str, Char, BigInt.
`__name__` is a synthetic `Final[str]`. ALL_CAPS without `Final` warns.
See Future Extensions table for planned enhancements.

---

### `# tpy:` Directives

Per-module configuration via source comments in the file preamble (before any code):

```python
# tpy: include("mylib/mylib.h")       # add #include "mylib/mylib.h" to generated header
# tpy: include("<SDL2/SDL.h>")         # add #include <SDL2/SDL.h> (angle-bracket)
# tpy: link("SDL2")                    # add -lSDL2 linker flag
# tpy: link("m", platform="linux")     # platform-filtered: only link on Linux
# tpy: native_module                   # declaration-only module (no .hpp/.cpp generated)
```

**Why it matters**: Multiple features depend on this infrastructure -- `default-int`
(already designed in INTEGER_INFERENCE_DESIGN.md), `range-check` toggle, `include`/`link`
for native interop (blocks real C library integration and DOOM port), and potentially
`profile=noalloc` for module-level performance profiles.

Without a directive system, each of these needs its own ad-hoc mechanism. A unified
`# tpy:` parser lets all of them share one implementation.

**Current state**: Done. Three directives implemented:
- `include(path)` -- emits `#include` in the generated header (quoted or angle-bracket)
- `link(lib)` / `link(lib, platform=name)` -- adds `-llib` linker flag with optional
  platform filter (`"linux"`, `"macos"`, `"windows"`)
- `native_module` -- marks the module as declaration-only (no `.hpp` or `.cpp`
  generated); for modules that only declare `@native` bindings to existing
  C/C++ entities. Includes are propagated to importing modules.

Directives must appear in the file preamble (before any code). Unknown directives and
malformed arguments produce warnings. The parser uses Python's `ast.literal_eval` for
argument parsing (call-style syntax).

Remaining: `default-int` per-module override, `range-check` toggle. These are
the first members of a broader **compilation-options / semantic-knobs**
surface (panic abort-vs-throw, `str` 8-bit-vs-utf8, bounds/deref/div-zero/
overflow checks, `assert` stripping, `@noalloc` enforcement) that wants one
coherent home rather than ad-hoc flags -- design captured in
`PROJECT_TOOLING_DESIGN.md` "Compilation options (semantic knobs)".

**Dependencies**: None (pure infrastructure). Enables many other features.

**Effort**: S-M (done)

---

### Float32 Type

```python
from tpy import Float32

x: Float32 = 1.5   # -> float (C++)
```

`Float32` -> `float` (C++ single precision). Currently `float` maps to `double` (64-bit).
No single-precision float type exists.

**Why it matters**: GPU programming, graphics, and memory-sensitive applications need
32-bit floats. Also useful for C interop where APIs expect `float` rather than `double`.

**Current state**: Done. Float32 type and Float64 alias implemented. Float32 maps to C++
`float` (single precision). Float64 is an alias for `float` (C++ `double`). Mixed arithmetic:
Float32 + Float32 -> Float32, Float32 + float -> float (widening), Float32 + int -> Float32.
Implicit coercion from int/float literals to Float32. Full operator support, print, f-string,
str/bool/float conversions.

**Dependencies**: None. Mirrors existing fixed-width int registration pattern.

**Effort**: S (register type, add coercion rules, codegen mapping)

---

### Iterable[T] Protocol

**Done.** `Iterator[T]` and `Iterable[T]` are now built-in protocols in the `tpy` module.

```python
from typing import Iterator, Iterable
from tpy import Int32

def sum_all(items: Iterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total
```

- `Iterator[T]`: protocol with `__next__(self) -> T` and `__iter__(self) -> Self`
- `Iterable[T]`: protocol with `__iter__(self) -> Iterator[T]`
- `iter(x)` builtin: calls `x.__iter__()`, returns `Iterator[T]`
- Auto-synthesis of `__iter__` on types with `__next__` (returning self)
- For-loop support for `Iterator[T]` and `Iterable[T]` parameter types
- Bounded type parameters: `T: Iterable[Int32]`
- Return-type protocol conformance checking in the protocol system

---

### Self Type

```python
class Builder:
    def set_name(self, name: str) -> Self:
        self.name = name
        return self

    def with_offset(self, other: Self) -> Int32:
        return self.value + other.value
```

`Self` refers to the type of the current class. Enables method chaining, builder
patterns, and self-referential parameter types without repeating the class name.

**Current state**: Done. `Self` works in return types and parameter types of record
methods (instance methods, not `@staticmethod`). Also works in protocol method
signatures (existing). For generic classes, `Self` resolves to the full generic type
(e.g. `Stack[T]`). `Self` is substituted to `NominalType(record.name, type_args)` at
registration time -- no codegen changes needed.

Remaining: `self: Own[Self]` for consuming methods (tracked in TODO.md).
Inheritance narrowing (Self returning subclass type) would require CRTP -- deferred.

**Dependencies**: None.

**Effort**: S (done)

---

### Bi-Directional Type Inference

Propagate expected types downward through call expressions to improve type inference
and overload resolution. See `docs/BIDIRECTIONAL_CALL_INFERENCE_DESIGN.md` for full
design.

**Why it matters**: Currently type inference is bottom-up only. This means expressions
like `connect("localhost", Int32(8080))` can't infer that `8080` should be `Int32` from
the parameter type. Bi-directional inference enables coercion-aware argument matching
and return-type-based overload filtering.

**Current state**: Done (core). Return-type inference fallback (Phase 1), nested call
context propagation, and partial explicit type args (Phase 2) are all working. See
`docs/BIDIRECTIONAL_CALL_INFERENCE_DESIGN.md` for details.

Remaining extensions tracked in the design doc (no practical use case found for either):
coercion-aware return-type matching, overload filtering by return type.

**Dependencies**: None.

**Effort**: M (done for core; future extensions are incremental)

---

## II. Polymorphism & Dispatch

### Box[T] -- Heap Ownership

```python
from tplib import Box
from tpy import Int32

b = Box[Int32](42)
print(b.get())       # 42
b.set(100)
print(b.get())       # 100 -- auto-deref through Deref[T] protocol
```

Library-level `Box[T]` in `tplib` using unsafe primitives (`tpy.unsafe`). `@nocopy`
(move-only, via `__del__`) + `Deref[T]` (auto-deref). Stores `Ptr[T]`, allocates via
`unsafe_alloc`/`unsafe_init`, cleans up via `unsafe_drop`/`unsafe_free`.

**Why it matters**: Enables heap-allocated objects with clear ownership. Gates
polymorphic containers (`list[Box[Shape]]`) and record fields typed as `@dynamic`
protocols. Foundation for `Rc[T]`, `Arc[T]`.

**Current state**: Working. Library `tplib.Box[T]` works for both concrete types
and `@dynamic` protocol types. `Covariant[T]` marker protocol enables
`Box[Circle]` -> `Box[Shape]` coercion when Circle implements `@dynamic Shape`.

The compiler auto-generates a converting move constructor for types marked
`Covariant[T]`, using C++ `std::is_base_of_v` constraint. Generalizes to
`Rc[T]`, `Arc[T]`, and any user-defined smart pointer.

**Effort**: Done

---

### Dynamic Dispatch (@dynamic protocols)

**Done.** The `@dynamic` decorator on protocols enables runtime dispatch via vtables.
The compiler generates abstract base classes, owning/ref adapter templates, and C++20
concepts for each `@dynamic` protocol. Syntax uses bare protocol types for dynamic
dispatch (`pet: Pet`) and type bounds for static dispatch (`T: Pet`).

```python
@dynamic
class Pet(Protocol):
    def make_noise(self) -> str: ...

class Dog(Pet):
    def make_noise(self) -> str: return "Woof"

def greet(pet: Pet) -> None:      # dynamic dispatch via Pet&
    print(pet.make_noise())

greet(Dog())                       # implicit upcast, zero cost
```

Working: locals, function params, method params, constructor params, return types
(provably long-lived), cross-module, `@dynamic` extending `@dynamic` (base class
inheritance chain), direct C++ inheritance, structural conformance (adapter wrapping),
conditional/loop reassignment (hoisted slots), `Optional[@dynamic]` rejection.

Remaining: `list[Box[P]]` heterogeneous containers, generic `@dynamic` protocols.
Record fields typed as `@dynamic` protocol work via `Box[T]` + `Covariant[T]`.

See [docs/DYNAMIC_PROTOCOL_DESIGN.md](DYNAMIC_PROTOCOL_DESIGN.md) for full progress.

---

### Constructor Init-List: Warn/Error on Branching Field Assignment

**Done.** Implemented as a two-section `__init__` model. See `docs/CONSTRUCTOR_DESIGN.md`
for the full design.

The leading prefix of `super().__init__()` calls and first-time `self.field = expr`
assignments is the *init section* and maps to the C++ member initializer list. Everything
after the first non-field statement is the *body section*.

At the split point, fields not yet initialized are checked:
- No default constructor -> error
- Default-constructible -> warning (will be zero/default-constructed in C++; absent in CPython)
- Has class-level default -> silent

Assigning a `@nocopy` or `__del__` field inside control flow in the body is an error.
All other body-section field assignments are silently allowed (e.g. accumulation in a loop).

`= default` is now emitted conditionally: only when all fields are C++-default-constructible.

---

### Optional[StaticProtocol] Codegen

**Done.** `Optional[Protocol]` parameters use pointer repr (`const T*`) with a default
template argument of `std::nullptr_t` and a `requires` clause for the concept constraint.
Narrowing (`if items is not None:`) uses `!= nullptr`, and protocol operations inside
narrowing bodies are wrapped in `if constexpr (!std::same_as<T, std::nullptr_t>)` to
prevent instantiation when the argument is omitted or `None`.

All call patterns work: concrete values (`f(nums)` -> `&nums`), explicit `None`
(`f(None)` -> typed nullptr), and omitted optional args (template defaults to
`std::nullptr_t`). Works for constructors, methods, and free functions, including
generic protocols like `Optional[Sequence[int]]`.

**Dependencies**: Protocol params in methods (done). Optional narrowing (done).

**Effort**: S (done)

---

### isinstance on Static Protocols

`isinstance(x, Sized)` where `Sized` is a static protocol should map to
`if constexpr (tpy::Sized<decltype(x)>)` in C++. This is a compile-time capability
check, not a runtime type test.

```python
def __init__(self, items: Iterable[T] | None = None) -> None:
    if items is not None:
        if isinstance(items, Sized):
            # items is narrowed to Iterable[T] & Sized -- len() is available
            pass
        for item in items:
            self.append(item)
```

Inside the `isinstance` branch, the variable is narrowed to also satisfy the checked
protocol (protocol narrowing), enabling calls like `len(items)` that require `Sized`.

**Why it matters**: This is the key pattern for ArrayList's constructor -- accept any
`Iterable`, but optimize when the input is also `Sized` (Span, list, Array). Without
this, the only options are separate overloads (needs union dispatch flattening B10) or
losing the size information.

**Current state**: Done. `isinstance(x, Protocol)` on protocol-typed parameters generates
`if constexpr (tpy::Concept<T>)` in C++. Works for same-protocol checks, cross-protocol
checks (e.g. `Sequence` param checking `Hashable`), and negation (`not isinstance`).
Concrete types are rejected with a clear error. Protocol operations inside the `if constexpr`
branch are available at the C++ template instantiation level.

**Dependencies**: `Optional[StaticProtocol]` codegen (B14, done).

**Effort**: M (done)

---

### Built-in / Conditional Protocol Conformance

A third conformance axis beyond structural protocols (B4/B15) and `@dynamic`
runtime polymorphism: letting **built-in** types (`Int32`, `str`, `bool`,
`float`) and **generic-container** types (`list[T]`, `dict[K, V]`) participate
in protocol conformance, including **conditional** conformance
(`list[T]` conforms to `Stringable` iff `T` does).

**Why it matters**: This is the gate for **type-directed code written in pure
stdlib** -- a serializer / formatter / hasher that recurses over arbitrary
typed shapes. Two concrete goals depend on it:

- **Zero-copy typed serialization** -- `json.dumps(data)` over
  `data: dict[str, Int32]` with no `JsonValue` wrapper and no O(n) deep copy.
- **Moving `print` / `str` out of the compiler** -- they are compiler-blessed
  today (`gen_print` + C++ `printing.hpp`) only because containers and scalars
  can't conform to a `Display`-style protocol in stdlib. Fix conformance and
  `print` becomes ordinary stdlib code.

The core blocker: a built-in does not satisfy a protocol whose required method
exists only as a C++ intrinsic (e.g. `tpy::__str__(int32_t)`) rather than a
declared stub method, because structural conformance consults the TPy method
surface. There is also no mechanism today for conditional conformance.

**Current state**: 🔬 Research. Problem framing + design space captured in
`docs/PROTOCOL_CONFORMANCE_DESIGN.md` (explicitly exploratory -- syntax and
several mechanism choices still open). No implementation.

**Dependencies**: Structural protocols (B4/B15, done). Generic containers (done).

**Effort**: L (new conformance machinery spanning sema + the built-in stub surface)

---

## III. Error Handling

### Exception Model (try/except/raise)

```python
try:
    result = parse(data)
except ValueError as e:
    print("bad data:", e.message)
```

TurboPython uses a **two-tier exception model**:

1. **Return exceptions** (`@error_return`) -- zero-cost control-flow errors compiled to
   `std::expected<T, E>`. For patterns where the "exception" is an expected outcome
   (iterator exhaustion, lookup miss, parse failure). No stack unwinding, `@noalloc` compatible.

2. **Throw exceptions** (C++ exceptions) -- standard stack-unwinding exceptions for genuine
   errors (I/O failures, invalid arguments, runtime violations). Same semantics as Python's
   `raise`/`except` model.

Both tiers use the same Python syntax (`raise E`, `try`/`except`), but the compiler routes
to different C++ mechanisms based on the `ReturnException` marker protocol.

**Current state**: Done. Full two-tier model implemented (phases E1-E8). See
`docs/EXCEPTION_DESIGN.md` for comprehensive design documentation. Includes:
- `@error_return(E)` with auto-propagation, `except ReturnException` catch-all
- C++ `throw`/`catch` for non-ReturnException types
- Exception types with data fields, `except E as e` binding
- Multiple `except` handlers, bare `except:`, re-raise
- `try`/`except`/`else`/`finally` with catch-all + duplication codegen
- `raise`/`return`/`break`/`continue` inside `finally` blocks
- `raise <expr>` (pre-constructed exception variables and function results)
- Nested `try`/`finally` with correct propagation

**Future extensions** (low priority): mixed-tier `try`/`except`, multiple return exception
types (`@error_return(E1, E2)`), custom base exception classes, exception chaining.

**Effort**: M + L (complete)

---

### `@error_return` Annotation

Transforms `raise X` into Result-type returns for control-flow exceptions. Distinct from
real exceptions (C1/C2) which use stack unwinding for error handling.

```python
from tpy import error_return

class NotFound(Exception):
    pass

@error_return(NotFound)
def find(items: list[Int32], target: Int32) -> Int32:
    for i in range(len(items)):
        if items[i] == target:
            return i
    raise NotFound
```

`raise E` compiles to `return std::unexpected(E{})`. Callers must use `try/except` --
calling without it is a compile error. Maps to `std::expected<T, E>` in C++.

**Why it matters**: Some Python patterns use exceptions for normal control flow rather than
error signaling (e.g. `StopIteration` in iterators). These are better compiled as
`std::expected` returns rather than C++ exception unwinding.
`@error_return` makes this transformation explicit and opt-in, separating the
control-flow-exception pattern from the general exception model (C1/C2).

**Current state**: Done. All three phases complete: generic `@error_return(E)` mechanism
(Phase 1), auto-add on `__next__` with `StopIteration` built-in type (Phase 2), and
cleanup with unified `__next__()` -> `std::expected` and direct for-loop codegen
(Phase 3). Built-in exceptions (`StopIteration`, `Exception`, `BaseException`) use
qualified names internally and emit as `::tpy::X` in C++ to avoid clashes with
user-defined classes of the same name. See `docs/ERROR_RETURN_DESIGN.md` for full design.

**Dependencies**: None for the basic annotation. Interacts with the general exception model
(C1/C2) but can be implemented independently.

**Effort**: M (done)

---

### Literal Types

```python
from typing import Literal, overload

@overload
def open_file(path: str, mode: Literal["r", "w"]) -> TextIO: ...
@overload
def open_file(path: str, mode: Literal["rb", "wb"]) -> BinaryIO: ...
@overload
def open_file(path: str, mode: str) -> TextIO: ...  # fallback

open_file("data.txt", "r")    # -> TextIO (matched Literal["r", "w"])
open_file("data.txt", "rb")   # -> BinaryIO (matched Literal["rb", "wb"])
mode: Literal["rb"] = "rb"
open_file("data.txt", mode)   # -> BinaryIO (annotated local retains Literal)
unannotated = "r"
open_file("data.txt", unannotated)  # -> TextIO (bare local falls through)
```

Compile-time checked value sets for `@overload` dispatch. `Literal["r", "w", ...]`
in parameter annotations enables overload resolution based on literal values.
Supports string, integer (including negative), and bool values.
Multi-value `Literal` supported. Direct literal arguments and `Literal`-annotated
locals dispatch to `Literal` overloads; bare (unannotated) locals fall through
to plain type overloads. Out-of-set assignments to a `Literal`-annotated local
are rejected at compile time.

**Why it matters**: Catches a common class of Python `ValueError` at compile time.
Natural extension of the type system. Works well with overloads (different return types
per literal value). Key enabler for `open()` binary mode dispatch.

**Current state**: Done for the overload + narrowing surface (Phases 1-5) and for
Literal returns (Phase 6 return position). Literal fields, union flattening, and
the Final variant of Phase 5 are future extensions -- see below. See
`docs/LITERAL_TYPES_DESIGN.md` for the full design.

- **Phase 1 (done)**: `Literal["a", "b", ...]` with string values. Ordering-independent:
  `Literal` stubs are preferred over plain stubs regardless of declaration order.
  Works for free functions, methods, and builtins with `@cpp_template`.
- **Phase 2 (done)**: Unified `LiteralType` with `LiteralValue(tag, value)`.
  Integer and bool literals (`Literal[1, 2]`, `Literal[True]`).
  `IntLiteralType` matches `LiteralType` with int base during overload resolution.
  Mixed types in a single `Literal[...]` rejected at parse time.
- **Phase 3 (done)**: Equality narrowing for `Literal`-annotated params.
  `if mode == "rb":` narrows `Literal["r", "w", "rb", "wb"]` to `Literal["rb"]`,
  enabling dispatch to more specific overload stubs within branches.
- **Phase 3a (done)**: Dead branch elimination -- single-value Literal comparisons
  fold to `true`/`false` in generated C++.
- **Phase 3b (done)**: Literal overload flattening. Each `@overload` stub with
  Literal params gets a per-literal C++ specialization with name mangling
  (`func__lit_value`) and dead branch elimination. Works for functions and methods.
- **Phase 3c (done)**: Multi-value Literal dead branch elimination -- comparisons
  against Literal-narrowed variables fold when the value set is provably disjoint.
- **Phase 4 (done)**: `match`/`case` exhaustiveness for Literal subjects, with
  per-arm narrowing to the matched value set (enabling dispatch to more specific
  overloads from within arm bodies). Missing-value warning consistent with enum/bool/union.
- **Phase 5 (annotated locals done; Final variant deferred)**: `x: Literal["r", "w"] = "r"`
  retains `LiteralType` on the local, dispatches to the Literal-specialized overload,
  and survives branch joins where every assigned value is in the declared set. Works for
  `Literal[str]`, `Literal[bool]`, `Literal[int]`. Out-of-set assignments (init and
  reassignment) are rejected with a clear diagnostic. Bare `x = "r"` deliberately stays
  `StrType` (Option A was rejected -- would ripple through 45+ `isinstance(StrType)`
  sites). The Final variant (`x: Final = "r"` infers `Literal["r"]`) is deferred: it
  requires three A1 future-extensions (bare `Final`, local `Final`, `Final[Literal[...]]`)
  all not started today.
- **Phase 6 (return position done; fields + union flattening deferred)**: Literal as
  a general type. Return positions (`def get_mode() -> Literal["r", "rb"]`) work --
  `LiteralType` in return position is accepted by parser/sema, and codegen emits
  view storage (`std::string_view` for str-base, value form for int/bool) since all
  Literal values have static storage. Field types (`class C: mode: Literal["debug", "release"]`)
  and union flattening (`Literal["a"] | Literal["b"]` == `Literal["a", "b"]` via
  `make_union()`) remain.

**Minor extensions** (separate from the phase ladder): Literal type in `Final`
variables (variant of Phase 5); collision-free name mangling for unusual literal
chars (current scheme collides on e.g. `Literal[","]` vs `Literal["_"]`).
Cross-function literal inference (caller -> param) is explicitly out of scope.

**Dependencies**: Phase 3: Narrowing infrastructure. Phase 3b: Phase 3.
Phase 5: Audit of str type checks.

**Effort**: Phase 5: M. Phase 6: L.

---

## IV. Safety & Effects

### Coroutine/Task borrow safety (gated on IR/regions)

A `create_task`'d coroutine captures its reference-typed args by reference and
is driven by the event loop independently of the spawning frame, so borrowing a
local into one that escapes (or is never joined) dangles -- today a silent
footgun (see BUGS.md). The right shape is a **lifetime feature**, not a
borrow-tracker special case: `create_task` declares a run-stable /
`'static`-equivalent **bound on its coroutine argument** (Rust's
`spawn: F: 'static`), enforced by the general region checker so a user's own
`spawn` is safe with no compiler edits; and a structured **`TaskGroup`** API
(cf. `std::thread::scope`) lets a task borrow a local *and* be proven joined at
block exit. Blocked on the THIR/MIR region work -- a prototype escape+discharge
check (branch `async-borrow-escape`) confirmed the boundary but had to hardcode
`Task`/`gather` and key on string roots, which fights the migration, so it was
not merged. **Effort**: L (rides on region inference; `TaskGroup` is its own M).

### @noalloc Enforcement

`@noalloc` is parsed and stored but zero enforcement exists. This is a core TPy
differentiator.

**Why it matters**: The promise of "opt-in constraints for hot paths" is meaningless without
enforcement. Designing what "no allocation" means precisely requires tracking which
operations allocate and how effects propagate through calls.

Operations that allocate: `list.append`, `str` concatenation -> `std::string`, `BigInt`
arithmetic, `Box::new`, `std::string` construction, container growth.

**Current state**: `is_noalloc` flag propagated in sema, zero enforcement code.

**Dependencies**: Needs string ownership model (done -- context-dependent `str`), exception
model (exceptions allocate), `Box[T]` (heap allocation).

**Effort**: L (new sema pass tracking allocation effects, call-chain propagation)

---

### Effect System (Generalized)

Generalize the existing ad-hoc effects (`@readonly`, `@noalloc`) into a small
composable system:

| Effect | Meaning |
|--------|---------|
| `@readonly` | No mutation of params (done) |
| `@noalloc` | No heap allocation (parsed, not enforced) |
| `@nothrow` | No exceptions |
| `@pure` | readonly + noalloc + nothrow + no IO |

Effects propagate: calling a function with effect X from a context without effect X
is an error. `@pure` enables compile-time evaluation and memoization.

**Why it matters**: Without a unified model, each effect is a separate special case in
sema. A general framework makes it cheaper to add new effects and reason about
interactions. Also provides the foundation for `@noalloc` enforcement and `@nothrow`.

**Current state**: `@readonly` fully implemented, `@noalloc` parsed. No framework.

**Dependencies**: Exception model (determines `@nothrow`). `@noalloc` enforcement.

**Effort**: M-L (framework design, retrofit existing effects, propagation analysis)

---

### Thread Safety Markers (Send / Sync)

> **Full design + phased roadmap + open questions: [`docs/SEND_SYNC_DESIGN.md`](SEND_SYNC_DESIGN.md).** The summary below is the high-level pitch; the design doc is the source of truth.


```python
class Counter:         # implicitly Sendable (all fields are value types)
    count: Int32

class SharedCache:     # NOT Sendable (contains Ptr)
    data: Ptr[Buffer]

ch: Channel[Counter]      # ok
ch: Channel[SharedCache]  # compile error: not Sendable
```

**Why it matters**: Concurrency is deferred, but the type-level decision should be designed
**now**. Retrofitting Send/Sync after concurrency ships is exactly what Swift went through
with `Sendable` -- years of warnings and gradual migration. The compiler already has
`is_value_type`. Extending to `is_sendable` (value types + types with only sendable fields)
is incremental.

**Current state**: Phases 1-4 done. `is_send` / `is_sync` auto-derivation on all
built-in types (`type_traits.hpp`) and records; `Send[T]` / `Sync[T]` marker
wrappers, `T: Send` bounds, opt-in/opt-out decorators (Phase 2); the
diagnostic surface -- `assert_send[T]()` / `assert_sync[T]()` compile-time
assertions, why-not chains at enforcement sites, and `tpyc --explain-send`
(Phase 3); and the first enforcement site, `Channel[T]` (`T: Send`) on the
single-threaded executor (Phase 4, SPSC). The multi-threaded executor +
`spawn`/`Task`-migration enforcement (Phase 5) and `Arc`/`Mutex`/`RwLock`
(Phase 6) are v3+. See `docs/SEND_SYNC_DESIGN.md`.

**Dependencies**: None -- markers added now, enforce when concurrency arrives.

**Effort**: S for markers (done), M for full propagation and checking

---

### Container Mutation During Iteration

```python
for item in items:
    items.append(item * 2)  # compile error: mutating 'items' while iterating
```

**Why it matters**: Python catches `RuntimeError: dictionary changed size during iteration`
at runtime. TPy can catch this at compile time for the common cases. The compiler already
knows which methods are `@readonly` (non-mutating) and the for-loop already "borrows"
the container (`auto&& __obj_N = expr`).

This is a simplified, high-value subset of full borrow checking. No lifetime annotations,
no escape analysis -- just a scoped rule: during for-each over `x`, mutating method calls
on `x` are an error.

**Current state**: Done. Sema detects mutating method calls on the iteration target
inside for-loop bodies and reports a compile-time warning. Uses hard-coded mutation
method sets for list, dict, and set.

**Dependencies**: `@readonly` annotation on container methods (done).

**Effort**: S-M (done)

---

### Ptr[T] Escape Analysis / Lifetime Tracking

Track pointer provenance across function boundaries to catch dangling references
at compile time.

**Why it matters**: Currently only local escapes are caught (return-local, loop-local).
Cross-function pointer provenance is unchecked. The safety model says "runtime checks
where it's hard" -- the boundary of compile-time vs runtime is still undefined.

Design space:
- **Minimal** (current): catch local escapes, runtime checks for rest
- **Region-based**: track "stack frame" vs "heap" provenance
- **Full lifetime inference**: Rust-style, with annotations

TPy's philosophy ("never force annotations") suggests leaning toward runtime-heavy
with optional compile-time opt-in.

**Recommendation**: Defer until more real-world code exists to inform the design.

**Current state**: Local escape detection works. Cross-function: nothing.

**Dependencies**: Interacts with `Box[T]` (heap provenance), `@noalloc`.

**Effort**: XL (design-heavy, affects sema deeply)

---

### Auto-Detect Readonly from Method Body

Infer `@readonly` automatically by analyzing method bodies (bottom-up inference).

Currently dunders in `IMPLICIT_READONLY_METHODS` are implicitly readonly, but regular
methods need explicit `@readonly` annotation. Auto-inference could remove the annotation
burden in most cases -- if a method only reads `self` fields and calls other readonly
methods, it is readonly.

**Why it matters**: Reduces annotation noise. Most methods are readonly in practice.
Also improves None-safety narrowing (more methods known readonly = fewer false
invalidations).

**Current state**: Done. `tpyc/sema/mutation_propagation.py::infer_method_const`
runs in Phase 2 (call-graph fixpoint) and back-propagates const inference: any
method with `self_mutated=False` (Phase 1 + Phase 2 facts) gets `is_readonly=True`
implicitly, reusing all existing readonly checks (codegen `const`, receiver
enforcement, protocol conformance). Excluded: staticmethods, consuming methods
(`Own[Self]` receiver), explicit `@readonly` (already set), `auto_readonly` mutable
clones, constructors / in-place operators (`_NEVER_INFER_CONST`), and methods
that return a borrow from self's mutable storage (would silently change `T&`
to `const T&` and override the user's declared return type -- inherently-const
view returns are still inferred).

**Dependencies**: `@readonly` system (done). Effect framework would generalize this.

**Effort**: M-L (done)

---

### Move-Safe Destructors

Prevent double-drop when objects with `__del__` are moved.

**E8a: Drop flag (`__tpy_owned_`) -- Done**

Classes with `__del__` get a hidden `bool __tpy_owned_ = true` field. The compiler
generates custom move constructor (sets source flag to `false`), destroy-and-reconstruct
move assignment (`this->~T()` + placement `new`), and a destructor guarded by
`if (!__tpy_owned_) return;`. `__del__` also implies `@nocopy` (copying would create
two owned objects that both run cleanup).

This handles all current cases: temporaries moved into functions, auto-move at last
use, variable reassignment (including loops and conditionals), and inheritance chains
where both parent and child have `__del__`.

**E8b: Sentinel field optimization -- Not started**

The drop flag adds a `bool` per object (often 8 bytes with padding). For classes
where a field has a natural "empty" state after move, that field can serve as the
sentinel instead:

| Field type | Post-move null state | Usable as sentinel? |
|---|---|---|
| `Ptr[T]`, `Ptr[readonly[T]]` | Need explicit `other.p = nullptr` | Yes, with codegen |
| `T \| None` (maps to `T*`) | Need explicit null | Yes, with codegen |
| `UninitHeapStorage[T]` | Nulls on move (heap pointer) | Yes, naturally |
| `std::unique_ptr<T>` | Guaranteed null after move | Yes, naturally |
| `std::optional<T>` | Unspecified by standard | No, unreliable |
| Value types (`Int32`, `bool`) | No null state | No |

The optimization: if a class has at least one pointer-like field, skip `__tpy_owned_`
and instead check that field in the destructor. The move constructor explicitly nulls
the sentinel field on the source. Classes with only value-type fields keep the flag.

Most real-world RAII classes (owning a pointer/handle) would benefit. The `|None` case
works because we control the move constructor and can emit explicit nullification.

**Current state**: E8a done. E8b not started.

**Dependencies**: `__del__` support (done). Move semantics (done).

**Effort**: E8a: S (done). E8b: M (sentinel detection, codegen changes for
per-field nullification, destructor guard rewriting)

---

## V. Compile-Time Features

### Compile-Time Evaluation (constexpr / comptime)

```python
N: Final = 1024
MASK: Final = (1 << N) - 1      # evaluated at compile time

@comptime
def make_lookup() -> Array[Int32, 256]:
    result = Array[Int32, 256]()
    for i in range(256):
        result[i] = Int32(popcount(i))
    return result

TABLE: Final = make_lookup()    # embedded in binary
```

**Why it matters**: TPy already has `Array[T, N]` where N must be a literal. Comptime would
allow computed N, lookup tables, format string validation, and const-propagation.
Makes `@pure` functions useful (pure + comptime args = evaluated at compile time).

Two approaches: embedded interpreter (like Zig comptime) or compile-and-exec during
compilation. The embedded interpreter is simpler but limited; compile-and-exec is more
powerful but requires a working TPy-to-native pipeline during compilation.

**Current state**: Not started. `Final[T]` constants done (primitive literals only).

**Dependencies**: `Final` constants (done). `@pure` effect (determines eligibility).

**Effort**: XL (interpreter or compile-during-compile infrastructure)

---

### Macro System / Metaprogramming

Compile-time code generation via decorators:

```python
@derive(Eq, Hash)
class Point:
    x: Int32
    y: Int32

@serialize("json")
class Config:
    name: str
    value: Int32
```

See LANGUAGE_FEATURES.md "Compile-Time Hooks" for detailed design (`@compile_time` /
`__generate__` hooks).

**Why it matters**: Eliminates boilerplate, enables library-level code generation without
compiler changes. Similar to Rust proc_macro, Zig comptime, Python metaclasses.

**Current state**: Phases 1, 1c, 2, and 7 done. Four macro kinds shipped:
class macros (Phase 1, `@class_macro`), call-site macros (Phase 2, `@call_macro`),
builder-trace macros (Phase 7, `@builder_macro` / `@builder_method` /
`@builder_returns` / `@builder_terminal`), and function macros
(`@function_macro`, rewriting a whole free-function body via a dedicated
`sema/function_macros.py` pass 5.5). Quote templates (`quote` / `quote_expr` /
`quote_fun` in `macro_api.py`) also shipped. `@dataclass` reimplemented as a
class macro with `Field`/`field()` descriptors. `asdict()`/`astuple()`
implemented as `@call_macro` functions that expand at compile time.
`argparse.ArgumentParser` implemented as a builder-trace macro
(`BuilderContext` walks the builder pattern at compile time and synthesizes
records + parse functions). Builder-trace expansion runs in a dedicated pass
5.5 between class-constants analysis and record-method-body analysis, so
synthesized methods get full pass-6 sema. See `docs/MACRO_DESIGN.md` for the
full design.

Remaining (Phase 3+): string-based method generation, hygiene, broader CPython
compatibility, replacing codegen special cases
(`__repr__`/`__hash__`/`operator<=>`) with macro-generated AST.
Companion type creation (`cls.add_companion_type`) for generating helper
types (e.g. key enums for O(1) JSON field dispatch).

**Dependencies**: None for Phase 1 (done). Compile-time evaluation (F1) needed for
Phase 4 (TpyMini VM). `@dataclass` replacement validates the API.

**Effort**: XL total (Phase 1: M, done; remaining phases incremental)

---

### Decorator Definitions in Library Code

Move built-in decorator definitions (`@native`, `@cpp_template`, `@readonly`, `@pure`,
`@noalloc`, `@error_return`, etc.) from hardcoded parser logic to `.py` library files.
Decorators would declare their argument schemas (positional/keyword types, valid targets,
combination constraints) as data, and the parser would validate against these schemas
instead of ad-hoc per-site checks.

Currently, decorator arg validation uses a centralized `_DECORATOR_ARG_SCHEMAS` dict in the
parser (Phase 1, done). The next step is moving these schemas to `.py` definitions so that
new decorators can be added without compiler changes. Ties closely with the macro system --
macro decorators already use a separate path (`_extract_decorator_kwargs`), and unifying
both under a single schema mechanism is the end goal.

**Current state**: Phase 1 done. `_DecoratorArgSchema` + `_validate_decorator_args` provide
centralized arg validation for all built-in decorators. Target validation and combination
constraints remain in parse methods.

**Dependencies**: Macro system (F2) for user-defined decorators with compile-time hooks.

**Effort**: M (Phase 2: target validation + combination constraints in schema),
L (Phase 3: decorator definitions in `.py` files)

---

### Compile-Time Conditional Compilation / Build Profiles

A feature-flag system that lets modules pick between implementations at compile
time. The initial driver is stdlib: the `re` module needs to swap between
`_bindings.cppstd.re` (std::regex, zero deps), `_bindings.pcre2.re` (PCRE2, closest to
CPython semantics), and potentially a vendored SRE port, without changing user
code. Similar needs exist elsewhere -- allocator choice, `@noalloc` profile
gating, stdlib variants for embedded targets, debug vs release behavior,
target-specific backends.

Surface sketch (to be designed):

```python
# Library module picks an implementation at compile time
# tpy: if feature(re_backend) == "pcre2"
from _bindings.pcre2.re import *  # tpy: re-export
# tpy: elif feature(re_backend) == "std"
from _bindings.cppstd.re import *
# tpy: else
from _bindings.sre.re import *
# tpy: endif
```

Or via a static `if` the compiler folds at sema time:

```python
from tpy import feature

if feature("re_backend") == "pcre2":
    from _bindings.pcre2.re import *
else:
    from _bindings.cppstd.re import *
```

Flags set via `tpyc --feature re_backend=pcre2`, project config
(`pyproject.toml` or a dedicated `tpyc.toml`), or `# tpy:` module-level
directives for local overrides.

**Why it matters**: avoids forcing one stdlib backend on everyone; lets
embedded users strip out features; cleanly swaps implementations for
experimentation and benchmarking. Without this, we either pick one backend and
live with its limits, or ask users to rewrite imports (`import _bindings.pcre2.re
as re`) -- the latter scales poorly across a real project.

**Scope considerations**:
- Per-module vs global: likely both. Project-wide default plus per-module
  override via `# tpy:` directive.
- Type-system implications: if two branches expose different types (PCRE2's
  `Pattern` vs std::regex's), downstream type checking must see exactly one
  branch. Simplest: require both branches to present a structurally identical
  surface (or a shared protocol).
- Interaction with the macro system (F2): feature queries could be exposed to
  macros, letting macro-authored modules adapt.
- Interaction with `@noalloc` and other profile gates (IV): those are per-
  function/class attributes today; a generalized profile system may subsume
  them.

**Current state**: Not started. `# tpy:` directives exist (module-level flags
like `profile=noalloc`) but are not evaluated as conditionals.

**Dependencies**: Design coordination with macro system (F2) and profile
gates. No hard blockers. Distinct from compile-time module parameters (F11):
this swaps *which implementation* a module imports; F11 monomorphizes *one*
module body over `constexpr` values.

**Effort**: M (design + parser/sema support for static `if` or directive-based
blocks; feature-flag resolution pipeline; CLI + project-config surface).

---

### Compile-Time Module Parameters

`compile_param()` declares a module-level constant whose value is supplied at
instantiation time; a single Python source compiles into multiple C++
translation units, each with a different `constexpr` value:

```python
# engine/solver/__init__.py
from tpy import compile_param

HIGH_PRECISION: bool = compile_param("HIGH_PRECISION")
```

`instantiate()` then produces a specialized TU per parameter combination, so
write-once logic gets zero-overhead specialization with no runtime branch on
the hot path (e.g. high- vs low-precision physics solvers running in the same
frame).

**Why it matters**: It is the zero-cost answer to "same logic, different
compile-time constants" -- precision/capacity/mode knobs that today force
either runtime branches or duplicated source. Distinct from F8 (build profiles
swap *which module* is imported); this monomorphizes *one* module body over
`constexpr` values, with guaranteed dead-branch elimination via `if constexpr`.

**Current state**: 🚧 Phase 0 POC done (CPython prototype of `compile_param()`
/ `instantiate()` + sub-module propagation + factory registry). Phases 1-5
(sema recognition, `constexpr` codegen, per-combination TU generation,
dependency-graph duplication, `Int32`/`Int64` params, auto-registration) not
started. See `docs/COMPILE_PARAM_DESIGN.md`.

**Dependencies**: None hard. Coordinates with F8 (conditional compilation) and
the project-config surface (which could declare instances without an in-source
`instantiate()` call).

**Effort**: L (sema + per-TU codegen + dependency-graph duplication)

---

### Self-Interpret (TPy Eval in tpyc)

Run/interpret TPy code during compilation for macro expansion and REPL improvement.

Two approaches:
- **Embedded interpreter**: simpler, limited to a subset of TPy. Sufficient for
  `@compile_time` hooks and `@derive` macros. Could reuse existing sema for type checking.
- **Compile-and-exec**: compile TPy to native during compilation (like Zig comptime).
  More powerful but requires a working TPy-to-native pipeline during compilation itself.

**Why it matters**: Enables the macro system (compile-time hooks need to execute TPy code).
Also improves the REPL (currently awkward -- compiles to C++ for each input).

**Current state**: Not started.

**Dependencies**: Macro system (primary consumer). Compile-time evaluation.

**Effort**: XL (interpreter or compile-during-compile infrastructure)

---

### Cyclic Dependency Handling

TPy can do better than Python (which silently partially-initializes modules) since we
have the full dependency graph at compile time.

Cycles are fine when modules contain only **declarations** (constants, types, functions)
-- there is no init-order issue because nothing runs at import time. The problem is only
with **module-level initialization code** that depends on state from another module in
the cycle.

Approach (all three shipped in v1):
- **Allow cycles by default** when all modules in the cycle are declaration-only
  (types, functions, constants). This is the common case.
- **Error on cycles with init-order dependency**: when a module has top-level statements
  that depend on a not-yet-initialized module in the cycle (partially-initialized state).
- **C++ forward declarations**: for types used only by pointer/reference across the cycle
  boundary, emit forward declarations to break the include cycle.

**Why it matters**: Real-world projects inevitably have circular imports. Python silently
partially-initializes; TPy has the full dependency graph at compile time and can do better
-- accept the safe shapes, reject the unsafe ones with a clear error.

**Current state**: Done (v1 conservative). `tpyc/compiler.py::_compute_compile_order`
runs Tarjan SCC over the import graph and topo-sorts the condensation. Each non-trivial
SCC goes through `_reject_unsupported_cycle` (the v1 conservatism gate). Cycle-member
forward-declaration headers are emitted to break complete-type cycles for pointer /
reference cross-references. Workspace-wide two-pass sema runs for cycle members so
re-exports through the cycle resolve. Universal re-export with cycle-aware `using`
suppression is in place.

Accepted today: declaration-only cycles (types referenced by pointer / reference / in
containers across the boundary), function and record re-exports through the cycle,
dataclass/enum/protocol mutual references, three-module cycles, overload-method cycles.
See `tests/cases/imports/mutual_*` for the happy-path coverage.

Rejected today (with clear errors): top-level init cycles, by-value cross-cycle fields
or in containers, by-value `__init__` params, by-value method params on inline
templates, by-value generic / template function params across the cycle, concrete
inheritance across the cycle, symmetric by-value field cycles. See
`tests/cases/imports/error_mutual_*` for the rejected shapes.

**Future extensions** (not blocking "Done"):
- Relax the by-value gate where the cycle is actually breakable (e.g. when one side
  can be lowered to pointer storage).
- Diamond / four-plus-module cycle shapes not yet exercised at scale.

**Dependencies**: Multi-module compilation (done). Header organization (done).

**Effort**: L (done; gate relaxations are incremental)

---

### Alternative Backends

Investigate non-C++ code generation targets: LLVM IR, Cranelift, WASM, or direct
machine code.

**Why it matters**: C++ as an intermediate language has downsides -- slow compilation,
complex error messages from the C++ compiler, dependency on a C++ toolchain. An LLVM
backend would give faster compile times and direct control over optimization. WASM
would enable browser deployment.

However, C++ is currently the right choice for a POC: it's readable, debuggable, and
leverages the entire C++ ecosystem (STL, libraries, tooling). Alternative backends are
a longer-term consideration.

**Current state**: Not started.

**Dependencies**: Stable type system and codegen architecture (changing backends is
easier when the frontend is stable).

**Effort**: XL (entirely new codegen pipeline)

---

### Frontend Plugin API

Translate non-Python source languages into a shared frontend IR that lowers to
`TpyModule` and reuses TPy's sema + codegen pipeline. First concrete targets: a
Turbo Pascal POC and a larger internal DSL with project-file-driven source
discovery and native runtime bindings.

```
                              +-----------------+
  source  ----[ plugin ]--->  | FrontendModule  | --[ lowering ]--> TpyModule
  (.pas, .myl, ...)           |     (IR)        |                       |
                              +-----------------+                    sema / codegen
```

The plugin is a purely syntactic frontend: it parses source files and emits IR.
Lowering owns the IR -> `TpyModule` adapter; sema and codegen are unchanged. The
IR is plain dataclasses (serializable), versioned via `api_version`, and reuses
TPy's type system symbolically (`NamedType`, `OptionalType`, `UnionType`, etc.).

**Plugin contract** (v1): `FrontendPlugin.parse(ctx, module_name, file_path) ->
FrontendOutput`; optional `module_name()` / `resolve_module()` hooks let a plugin
own module naming + import resolution (both default to `None`, keeping the
path-based behaviour); CLI flags `--dsl-plugin` and `--dsl-opt`; decorator manifest
extends the compiler's decorator registry; diagnostics via `FrontendDiagnostic`.
Trust boundary: plugins run unrestricted Python (compile-time trusted code).

**Why it matters**: Lets out-of-tree translators ship as Python modules instead of
forking the compiler. Plugin shapes the design supports include schema-driven
codegen (protobuf-style), schema-only data descriptors (JSON Schema, Avro), and
data files with compile-time validation (CSV / JSON data). The IR is the
contract; the compiler internals stay free to evolve.

**Current state**: v1 shipped. `tpyc/frontend_plugin.py`,
`tpyc/frontend_ir/`, the `--dsl-plugin` / `--dsl-opt` CLI flags, and
the test-harness `options.json` `plugin` / `dsl_opts` mechanism are
all in place. The first concrete plugin (Turbo Pascal POC) lives at
`frontends/pascal/` and exercises the surface end-to-end. See
`docs/FRONTEND_PLUGIN_DESIGN.md` for the spec, including the deferred
fields (`WorkspaceContext.find_files` / `resolve_module`) that v1
declines to implement until a second plugin's lookup pattern is in
hand.

**Dependencies**: None (parser + sema + codegen are the consumers; the plugin
contract sits on top).

**Effort**: XL (IR types, lowering pass, module resolver provider chain,
discovery dispatch, diagnostics envelope, CLI, plugin loader).

---

### IR Migration (THIR / MIR)

Replace the current in-place-annotated-AST + sema-side-tables model with two
explicit IRs between sema and codegen:

- **THIR** (typed higher-level IR): self-contained snapshot of the
  fully-resolved program -- types, dispatch decisions, narrowing facts, all
  materialized as values rather than scattered across AST fields and analyzer
  dicts. Decouples codegen from a live `SemanticAnalyzer` instance.
- **MIR** (mid-level IR): lower-level form used for liveness, move/copy
  lowering, and the borrow checker. Loan tracking gets precise field-path
  storage keys instead of today's string-based `BorrowTracker`; branch-merge
  joins become precise (per-path) rather than conservative-union.

```
parse -> sema -> [THIR] -> MIR liveness / move / borrow -> [MIR] -> codegen
```

**Why it matters**: Three concrete pain points today --
1. **Tight coupling**: codegen cannot run without a live sema. Sema state is
   spread across AST node fields, `SemanticContext` `id()`-keyed dicts, and
   `BorrowTracker` string maps. There is no self-contained "this is what sema
   produced" artifact.
2. **Hard to debug**: no way to dump the fully-typed, fully-resolved program
   between sema and codegen.
3. **Borrow-checker precision**: today's branch-join merges borrows
   conservatively (union of both branches); a move on one path can falsely
   conflict with a borrow on a mutually exclusive path.

Beyond the immediate fixes, THIR is the natural cache boundary for any future
incremental compilation work, and MIR is what the planned LSP target depends on
(structured per-construct facts that survive across compiler versions).

**Current state**: Not started. See `docs/IR_DESIGN.md` for the full design --
THIR node set, lowering plan, MIR node set, liveness + move/copy + borrow
passes, rollout phasing, and what *doesn't* change. Per the design doc's
status table, every step (THIR nodes, lowering, codegen migration, MIR nodes,
liveness, move lowering, borrow checker, opt-in safe mode, MIR-backed codegen,
retirement of old logic) is "Not started".

**Dependencies**: None within tpyc (it's a self-contained refactor). Blocks:
LSP target (per CLAUDE.md), incremental front-end caching, and any future
non-C++ backend (F6) that wants a stable IR to consume.

**Effort**: XL (multi-quarter refactor; sequenced in rollout phases per
IR_DESIGN.md).

---

## VI. Control Flow & Expressions

### Match/Case with Pattern Matching

```python
match expr:
    case Lit(value=v):
        return v
    case Add(left=l, right=r):
        return eval(l) + eval(r)
```

Maps to `switch` on `variant.index()` (not `std::visit` -- avoids indirect dispatch
overhead). Class patterns narrow the subject variable for the block body.

**Why it matters**: The natural consumer of union types and enums. Exhaustiveness checking
is a compile-time guarantee Python doesn't have.

**Current state**: Done. Supports union, enum, primitive, record, and Optional subjects.
Switch-based codegen for unions/enums/int/bool, if/elif for records/str/float. Or-patterns,
guard clauses, as-patterns, positional and keyword field bindings. Exhaustiveness warnings
for all supported subject types.

**Dependencies**: Enums (done). Union types (done). Narrowing system (done).

**Effort**: M-L (parser + sema narrowing + codegen)

---

### with Statement (Context Managers)

```python
with open(path) as f:
    data = f.read()
# f.__exit__() called automatically
```

Maps to C++ try/catch duplication -- `__exit__()` is emitted in both the catch handler
(exception path) and the normal path, ensuring cleanup on early return, exception, or panic.

**Current state**: Done. Duck-typed `__enter__`/`__exit__` protocol. `__exit__` accepts
0 params (TPy-native) or 3 params (CPython-compatible -- exception params stripped at
parse time). `as`-variable visible after `with` block (CPython scoping). Multiple
context managers (`with a() as x, b() as y:`) emit nested try/catch blocks (LIFO exit order).
`__enter__` return type determines the `as`-binding type (can differ from context manager
type).

**Dependencies**: `__enter__` / `__exit__` protocol (done, duck-typed).

**Effort**: M (done)

---

### Closures / Nested Functions

```python
def make_adder(n: Int32) -> Callable[[Int32], Int32]:
    def add(x: Int32) -> Int32:
        return x + n    # captures n
    return add
```

**Why it matters**: Prerequisite for lambda, callbacks, higher-order functions, and
functional patterns. The hard design question is capture semantics: by value vs by
reference, lifetime of captures, interaction with ownership model.

Maps to C++ lambda or functor struct with captured fields.

**Current state**: Done. Nested `def` with captures and `nonlocal` supported. Capture
analysis infers which outer variables are referenced. Codegen emits C++ lambdas with
appropriate capture lists. `nonlocal` declarations validated in sema.

**Dependencies**: Done.

**Effort**: L (done)

---

### List Comprehensions

```python
squares = [x * x for x in range(10)]
evens = [x for x in items if x % 2 == 0]
```

Expression that builds a list from an iterable with optional filtering. Maps to an
immediately-invoked lambda (IIFE) containing a loop + `push_back`. Single generator
only (no nested `for x in a for y in b`).

See `docs/COMPREHENSION_DESIGN.md` for full design including phased rollout,
codegen strategy, and future extensions (dict/set comprehensions, generator expressions).

**Current state**: Done (Phase 1+2). Single-generator list comprehensions with optional
filter clause. Codegen uses IIFE pattern (`[&]() { ... }()`). Supports range() counter
optimization and begin/end iteration for native containers. Tuple unpacking in generators,
annotation propagation, and Array optimization have all shipped (a fixed-length comprehension
produces a stack `std::array` built by aggregate construction via `tpy::array_from_index` --
exactly-N in-place element construction, so non-copyable / non-default-constructible elements
are included; only elements calling `@error_return` functions stay on the `std::vector` path).

**Dependencies**: None for basic form. Filter clause needs bool coercion (done).

**Effort**: M (done)

---

### Generators (yield)

```python
def fibonacci() -> Iterator[int]:
    a, b = 0, 1
    while True:
        yield a
        a, b = b, a + b
```

Maps to state-machine struct implementing `Iterator`. Stack-allocated for simple
generators (single yield point); struct-based with `__next__()` for multiple yield points.

**Current state**: Done. Generator functions with `yield` are fully supported. Codegen
transforms the function body into a state-machine struct with `__next__()` method.
Simple generators (single yield in a loop) use an optimized inline path. See
`tpyc/codegen_cpp/gen_generators.py`. Known limitation: generic generators with multiple
yield points are guarded with a sema error (template struct + out-of-line `__next__()`
linkage issue).

**Dependencies**: Iterator protocol (done). Tuple unpacking (done).

**Effort**: L-XL (done)

---

### Lambda

```python
items.sort(key=lambda x: x.priority)
result = map(lambda x: x * 2, values)
```

Maps to C++ lambda expressions. Syntactic sugar over closures -- `lambda x: expr`
is equivalent to a single-expression closure.

**Current state**: Done. Lambda expressions are fully supported with type inference for
parameters (from `Fn`/`Callable` context), capture analysis, and C++ lambda codegen.
Works as arguments to higher-order functions, stored in variables, and in comprehension
filters.

**Dependencies**: Done.

**Effort**: M (done)

---

### *args / **kwargs

```python
def log(fmt: str, *args: Any) -> None:
    print(fmt.format(*args))

def connect(host: str, **kwargs: Any) -> Connection:
    port = kwargs.get("port", 8080)
    timeout = kwargs.get("timeout", 30.0)
    ...

# The most common stdlib pattern:
os.path.join("a", "b", "c")        # *args
dict(name="Alice", age=30)          # **kwargs
```

Variadic positional (`*args`) and keyword (`**kwargs`) arguments. Fundamental Python
feature used pervasively in stdlib APIs.

**Why it matters**: Prerequisite for CPython stdlib compatibility. The project goal
is to run CPython code without changes -- many stdlib APIs depend on these:
- `os.path.join(*paths)` -- the single most-used `os.path` function
- `print(*args)` -- already special-cased in TPy, but user-defined variadic functions can't exist
- `argparse.add_argument(*name_or_flags, **kwargs)` -- central argparse API
- `dict(**kwargs)`, `str.format(*args)` -- core builtins
- Nearly every library with configuration-heavy APIs uses `**kwargs`

Without `*args`/`**kwargs`, stdlib stubs must use overloads or list/dict parameters,
which breaks API compatibility with CPython code.

#### `*args` -- Variadic Positional (D17)

The simpler case. Design options:

1. **Homogeneous `*args: T`** -- all args have the same type. Maps to
   `std::initializer_list<T>` or a parameter pack constrained to same type:
   ```python
   def join(*parts: str) -> str:     # all args are str
       ...
   # -> join(std::initializer_list<std::string_view> parts)
   ```
   This covers `os.path.join`, `print`, `max`/`min`, and many common patterns.

2. **Heterogeneous `*args`** -- args have different types. Maps to C++ parameter packs:
   ```python
   def log(fmt: str, *args) -> None:  # args can be mixed types
       ...
   # -> template<typename... Args> void log(string_view fmt, Args&&... args)
   ```
   Much harder -- requires variadic templates, forwarding, and the ability to iterate
   over a parameter pack. C++17 fold expressions help but don't solve all cases.

3. **Fixed overloads** -- for known small arities, generate overloads for 1-8 args.
   Covers most practical cases without true variadics. Pragmatic but limited.

Recommendation: start with homogeneous `*args: T` (option 1) -- it covers the
majority of stdlib patterns and maps cleanly to C++. Heterogeneous `*args` can
follow later with parameter packs.

#### `**kwargs` -- Variadic Keywords (D18)

The harder case. Python `**kwargs` is a `dict[str, Any]` at runtime -- keys are
strings, values are arbitrary. This is fundamentally dynamic.

Design options:

1. **`dict[str, Any]` passthrough** -- the simple approach. `**kwargs` becomes a
   `dict[str, Any]` parameter. Callers pack kwargs into a dict, callees unpack with
   `kwargs.get("key", default)`. Correct but loses type safety and has runtime overhead:
   ```python
   def connect(**kwargs: Any) -> Connection:
       ...
   # -> connect(tpy::ordered_map<std::string, std::any> kwargs)
   ```

2. **Typed kwargs via TypedDict** -- Python 3.12 `Unpack[TypedDict]` (PEP 692):
   ```python
   class ConnectOptions(TypedDict):
       port: Int32
       timeout: float

   def connect(**kwargs: Unpack[ConnectOptions]) -> Connection:
       ...
   # -> connect(int32_t port = 8080, double timeout = 30.0)
   ```
   This compiles to named parameters with defaults -- zero overhead, fully typed.
   But only works when the caller knows the kwargs schema at compile time.

3. **Compile-time resolution** (like current kwargs) -- when the caller uses literal
   keyword names (`connect(port=8080, timeout=5.0)`), resolve to positional args
   at compile time. This is what TPy already does for regular kwargs. The `**kwargs`
   definition would just allow the function to accept arbitrary named args, with
   the compiler matching known names to parameters. Unknown names could go into
   a dict fallback.

4. **Forwarding only** -- support `**kwargs` in signatures that forward to other
   functions (`def wrapper(**kwargs): return inner(**kwargs)`) by treating it as
   a compile-time passthrough. No runtime dict, just template forwarding. Very limited
   but handles the common "pass through configuration" pattern.

Recommendation: TypedDict approach (option 2) is the cleanest for TPy -- it's fully
static and matches the direction Python typing is heading. The `dict[str, Any]`
fallback (option 1) is needed for full CPython compat but depends on `Any` (D15).
Start with option 2 when the kwargs schema is known, fall back to option 1 when it isn't.

**Current state**: `*args: T` done (homogeneous). `**kwargs: Unpack[TypedDict]` done
(option 2). Compiles to `void f(const TD& kwargs)` -- single struct parameter. Call-site
kwargs are split between regular params and TypedDict fields, then packed into a
constructor call. `f(**td_instance)` passes the struct directly. Works with mixed
positional + kwargs, on methods, and with forwarding (`inner(**kwargs)`).

Not yet supported: `@overload` stubs with `*args`, heterogeneous `*args` (needs `Any`),
untyped `**kwargs` (option 1, needs `Any`).

**Dependencies**: `Any` type (D15) for heterogeneous `*args` and untyped `**kwargs`.

**Effort**: D17 done. D18 done (TypedDict approach). Untyped fallback depends on `Any`.

---

### del Statement (Explicit Destruction)

```python
buf = LargeBuffer(1024)
process(buf)
del buf          # destructs immediately, marks as de-initialized
use(buf)         # compile error: use after del
```

**Why it matters**: Gives users explicit control over object lifetime. Leverages existing
`init_tracker` infrastructure. Useful for large resources where you want deterministic
cleanup before scope end.

**Current state**: Done. `del x` (variable unbinding) and `del obj[key]`
(element deletion via `__delitem__`) both shipped. Use after `del` is a
compile-time error; re-assignment after `del` is supported. Works on locals,
parameters, globals, nonlocals, loop variables, generator-bound names, and
module-level variables. Early destruction (move-sink) is emitted only when
the variable is the sole owner of its value; aliases, parameters, and
globals just unbind the name. See LANGUAGE_FEATURES.md.

**Limitation:** pointer-locals that were initially aliases
(e.g. `a = b; a = new_value; del a`) skip early destruction conservatively,
even after reassignment to an owned value.

**Dependencies**: `init_tracker` (done). Move semantics (done).

**Effort**: S-M (done)

---

### Dict Comprehension

```python
scores = {name: len(name) for name in names}
filtered = {k: v for k, v in d.items() if v > 0}
```

Builds a `dict[K, V]` from an iterable with optional filtering. Same IIFE codegen
pattern as list comprehensions, producing `tpy::ordered_map<K, V>`.

See `docs/COMPREHENSION_DESIGN.md` for design sketch.

**Current state**: Done. Single-generator dict comprehensions with optional filter
clause and tuple unpacking (`for k, v in`). Codegen uses IIFE pattern, producing
`tpy::ordered_map<K, V>`. Annotation hints propagate key/value types.

**Dependencies**: Dict type (done). List comprehension infrastructure (B9, done).

**Effort**: S-M (done)

---

### Set Comprehension

```python
unique_lengths = {len(name) for name in names}
```

Builds a `set[T]` from an iterable with optional filtering. Same generator model
as list/dict comprehensions.

**Current state**: Done. IIFE-based codegen with loop + `insert`, supports filtering
and tuple unpacking. Annotation propagation supported.

**Dependencies**: Set type (D9). List comprehension infrastructure (B9).

**Effort**: S (incremental once set type and list comprehensions exist)

---

### Generator Expressions

```python
total = sum(x * x for x in items)
has_negative = any(x < 0 for x in items)
words = list(word.lower() for word in raw_words)
```

Lazy iteration expressions. In Python these produce generator objects; in TPy the
key optimization is **fusing** the generator into a consuming builtin to avoid
any intermediate allocation.

**Codegen strategy** (tiered):

1. **Fused IIFE** (primary): When the generator is the sole argument to a known
   builtin (`sum`, `any`, `all`, `min`, `max`, `list`, `dict`), fuse the loop
   into the consumer. Zero allocation, single pass:
   ```cpp
   // sum(x * x for x in items)
   auto total = [&]() {
       int64_t __acc = 0;
       for (auto& x : items) { __acc += x * x; }
       return __acc;
   }();
   ```

2. **Materialize fallback**: When passed to a user-defined function or stored in a
   variable, materialize to `list[T]` (equivalent to wrapping in `list(...)`).
   A warning could suggest using an explicit list comprehension instead.

A future alternative for user-defined consumers is passing a callable (lambda) to a
template function, but fused IIFE covers the high-value cases with zero new runtime
infrastructure.

See `docs/COMPREHENSION_DESIGN.md` for full design.

**Current state**: Done. `tpy::make_generator<T>(lambda)` wrapper satisfying
`Iterable[T]`. Supports range sources, container sources, filter clauses, tuple
unpacking, outer local capture.

**Dependencies**: List comprehension infrastructure (B9). Builtin function awareness
in sema for fusion.

**Effort**: M

---

### Walrus Operator

```python
if (n := len(items)) > 10:
    print(f"too many: {n}")

filtered = [y for x in items if (y := transform(x)) is not None]
```

Assignment expression (`:=`) that assigns and returns a value. PEP 572.

**Why it matters**: Common in filter-and-transform patterns, especially in
comprehension filters where you want to compute a value, test it, and keep it.
Also useful in `while` loops (`while (line := read_line()) is not None`).

Maps to C++ pre-declaration + inline assignment expression. Value types use
`T x{}; ... (x = expr)`. Non-value types use `std::optional<T> x; ... (x = expr, *x)`
with `narrowed_vars` for subsequent access. C++ `&&`/`||` preserve short-circuit
semantics naturally, so walrus in `and`/`or` chains works without special handling.

**Current state**: Done. Works in `if` conditions, `while` conditions, `and`/`or`
chains, general expression positions, and comprehension filters (PEP 572 scope leak).
Optional narrowing (`is not None`) and truthiness narrowing work through walrus.

**Dependencies**: None.

**Effort**: S-M (done)

---

### for/else, while/else

```python
for item in items:
    if item == target:
        print("found")
        break
else:
    print("not found")  # runs only if loop completed without break
```

Python's `for/else` and `while/else` execute the `else` block when the loop
terminates normally (without `break`). Maps to a goto pattern in C++:

```cpp
for (auto& item : items) {
    if (item == target) {
        std::cout << "found" << "\n";
        goto __after_else_0;
    }
}
{
    std::cout << "not found" << "\n";
}
__after_else_0:;
```

**Why it matters**: Niche Python feature but used for search patterns. Simple to
implement since it's purely syntactic sugar over a goto past the else block.

**Current state**: Done. Both `for/else` and `while/else` are implemented. Codegen
emits the else body unconditionally after the loop, followed by a label; `break`
inside the loop is replaced with `goto __after_else_N` which jumps past the else
block. No flag variables or conditional branches. Nested loops are handled via a
label stack (inner loops without else push an empty sentinel so their breaks remain
plain `break`).

**Dependencies**: None.

**Effort**: S (parser + codegen flag variable injection)

---

## VII. Python Compatibility

### f-strings

**Done.** f-strings with expressions, nested field access, and method calls.
Maps to `std::ostringstream`-based concatenation in C++.

---

### Dataclasses

```python
@dataclass
class Point:
    x: Int32
    y: Int32
    # auto-generates __init__, __eq__, __repr__
```

**Current state**: Phase 1 done. `@dataclass` auto-generates `__init__` from field
annotations with default value support. `from dataclasses import dataclass` import,
field ordering validation, user `__init__` wins over synthesis. CPython-compatible.
See `docs/DATACLASS_DESIGN.md` for full design and remaining phases (auto `__eq__`,
`frozen=True`, auto `__hash__`).

**Dependencies**: None for basic form. Full `@dataclass` needs default values (done).

**Effort**: M (Phase 1 done; Phases 2-4 incremental)

---

### Keyword Arguments and Default Values

```python
def connect(host: str, port: Int32 = Int32(8080), timeout: float = 30.0) -> None:
    ...

connect("localhost", timeout=5.0)
```

**Why it matters**: Extremely common Python pattern. Without defaults and kwargs, every
function needs all arguments specified. Blocks many stdlib-like APIs.

**Current state**: Done. Default parameter values and keyword arguments at call sites are
both implemented. Kwargs are resolved to positional args at compile time in sema -- codegen
sees only positional args. Supported for user functions, methods, constructors, and generic
functions. Overloaded builtins (e.g., `range`, `len`) reject kwargs with a clear error.

**Dependencies**: None.

**Effort**: M

---

### Generic Default Values

```python
def first_or[T](items: list[T], fallback: T = T()) -> T:
    if len(items) > 0:
        return items[0]
    return fallback
```

Two related improvements to defaults on generic type parameters:

1. **`T()` default-construction syntax**: Allow `T()` as a default value when `T` is a type
   parameter. Maps to `T{}` in C++ (value-initialization). Works for functions and methods
   with class-level or method-level type params. Also works with kwargs gap-filling.

2. **Instantiation-time validation**: When a generic function with defaults (e.g.,
   `fallback: T = 0`) is instantiated with a concrete type, the compiler validates that the
   default expression is compatible with that type. `f[str]()` with `= 0` produces a clear
   sema error instead of a cryptic C++ compilation failure.

**Current state**: Done. Both features implemented: `TpyTypeParamConstruct` AST node for
`T()` defaults, and `_validate_generic_defaults` in sema for instantiation-time checking.

**Dependencies**: A6 (defaults done).

**Effort**: S

---

### dict Type

```python
d: dict[str, Int32] = {"a": 1, "b": 2}
for k, v in d.items():
    print(k, v)
```

Maps to a custom `tpy::ordered_map<K, V>` -- a hash map (`std::unordered_map<K, Node*>`)
combined with an intrusive doubly-linked list to preserve Python 3.7+ insertion order.

**Why it matters**: Used in 24 files of the compiler source. Most Python programs use dicts.
Phased: Phase 1 covers literals, subscript, `len`, `in`, `for k in d`, `print`,
`get`/`pop`/`clear`. Phase 2 adds `get(key, default)`, `keys()`/`values()`/`items()`,
`update()`, `setdefault()`, `del d[k]`. Phase 3 adds `dict(pairs)` constructor.

**Current state**: Phase 3 done. Covers `DictType` in type system, dict literals `{k: v}`,
subscript read/write `d[k]`/`d[k]=v`, `del d[k]`, `len(d)`, `k in d`, `for k in d`
iteration, `print(d)`, methods `get()`/`get(key, default)`/`pop()`/`clear()`/`update()`/
`setdefault()`, views `keys()`/`values()`/`items()` with zero-allocation iteration,
`dict(pairs)` constructor from `Iterable[tuple[K, V]]`,
annotation hint propagation for nested dicts, dangling return detection. Runtime backed
by `tpy::ordered_map<K,V>` with insertion-order preservation.
Design document: `docs/DICT_DESIGN.md`.

**Dependencies**: Key validation uses `Hashable` protocol. User records as keys need user-defined `__hash__` + `__eq__`.

**Effort**: L (type + literals + methods + iteration + `ordered_map` C++ runtime)

---

### `@overload` Dispatch Flattening

```python
from typing import overload

class ArrayList[T, N: int]:
    @overload
    def __getitem__(self, index: Int32) -> T: ...
    @overload
    def __getitem__(self, s: slice) -> Span[readonly[T]]: ...
    def __getitem__(self, index: Int32 | slice) -> T | Span[readonly[T]]:
        if isinstance(index, slice):
            return self._get_span(index.start, index.stop)
        return self._storage[index]
```

When `@overload` stubs are present, the compiler splits the implementation method into
separate C++ overloads -- one per `@overload` stub. Each overload gets its own parameter
and return type from the corresponding stub. The implementation body contains the
isinstance dispatch logic; the compiler extracts each branch into its overload.

```cpp
// Generated from @overload stubs:
T& operator[](int32_t index) { return _storage[index]; }
std::span<T> operator[](tpy::Slice s) { return _get_span(s.start, s.stop); }
```

**Why it matters**: Enables methods that accept different types and return different
types per variant -- the key pattern for user-defined slicing (`__getitem__` with
`Int32` vs `slice`), constructor variants, and any method where the return type depends
on the argument type.

The approach uses standard Python `@overload` syntax (PEP 484). In CPython, `@overload`
stubs are type-checker-only annotations (ignored at runtime); the implementation body
runs and handles all cases via isinstance dispatch. In TPy, the stubs declare per-overload
signatures and the compiler generates separate C++ functions. Same source works in both
runtimes.

Only methods with explicit `@overload` stubs are flattened -- no automatic pattern
detection. This keeps the behavior explicit and avoids subtle errors from auto-deduction
of per-branch return types.

Key use cases: `__getitem__` with `Int32 | slice` (different return types), constructor
variants (`ArrayList` from span vs iterable), `pop()` vs `pop(index)`.

**Current state**: Done. Two modes: **(a) stubs + impl** (bodyless stubs followed by
one implementation; body is specialized per-stub via dead-branch elim) and **(b) bodied
stubs** (each `@overload` carries its own body, no trailing impl needed). Mode (a)
supports arity-variant stubs: stubs may have fewer params than the impl when the
omitted trailing params have defaults. Dead-branch elim handles isinstance if/elif/else,
match/case on union subjects, `is None` on Optional params, and equality checks on
literal-defaulted params. Exhaustiveness checking ensures stubs cover all union variants
(relaxed for params not present in every stub). Literal[...] dispatch, cross-module
imports, and methods all work. CPython compatible via `lib/cpy/typing.py` runtime
dispatch shim (arity + isinstance).

**Dependencies**: Union types (done), isinstance narrowing (done). `slice` type needed
for the `__getitem__` use case (orthogonal).

**Effort**: M (done)

---

### `__bool__` Protocol

**Done.** `__bool__()` structural detection, `bool()` dispatch, implicit truthiness
in `if`/`while`/`not`/`and`/`or`, `__len__() != 0` fallback, `Truthy` protocol bound.

---

### List Slicing

```python
items: list[Int32] = [10, 20, 30, 40, 50]
sub = items[1:3]       # Span[Int32] -> [20, 30] (zero-copy view)
last = items[-2:]      # Span[Int32] -> [40, 50]
copy = items[:]        # Span[Int32] -> full view
```

**Design decision**: Slicing returns `Span[T]` (zero-copy view into the original
container), not a new list. This matches TPy's performance-first philosophy -- no
allocation, no element copies. The source's mutability is preserved: mutable list
gives `Span[T]`, `@readonly` context gives `Span[readonly[T]]`.

Python returns a new list from slicing. Users who need an independent copy can
explicitly construct one: `copy: list[T] = list(items[1:3])`.

**Phase 1 (built-in types)**: `list[T]`, `Array[T, N]`, `Span[T]`,
`Span[readonly[T]]` slicing. Returns `Span[T]` / `Span[readonly[T]]`. String slicing
returns `StrView` (existing). No step support initially.

**Phase 2 (user types)**: Add `slice` built-in type. User types support slicing
via `__getitem__` with `@overload` dispatch (B10):

```python
from typing import overload

class ArrayList[T, N: int]:
    @overload
    def __getitem__(self, index: Int32) -> T: ...
    @overload
    def __getitem__(self, s: slice) -> Span[readonly[T]]: ...
    def __getitem__(self, index: Int32 | slice) -> T | Span[readonly[T]]:
        if isinstance(index, slice):
            return self.__span__()[index.start:index.stop]
        return self._storage[index]
```

**Phase 3 (step)**: Support `items[::2]`, `items[::-1]`. Step slicing returns a
new `list[T]` (elements are not contiguous). String step slicing returns `str`
(owned string).

**Current state**: All phases done.

- **Phase 1 (done)**: Built-in container slicing (`list[T]`, `Array[T,N]`, `Span[T]`,
  `Span[readonly[T]]`) returns `Span[T]` / `Span[readonly[T]]`. String slicing returns
  `StrView`. Indices clamped (Python semantics), negative indices supported. `@readonly`
  context and `Span[readonly[T]]` source propagate to `Span[readonly[T]]` result.
- **Phase 2 (done)**: `slice` built-in type. User records with `@overload __getitem__`
  accept both `Int32` (index) and `slice` (range) parameters.
- **Phase 3 (done)**: Stepped slicing with a two-type design -- `basic_slice`
  (start, stop) for `a[1:3]` returns zero-copy views, `slice` (start, stop, step) for
  `a[1:3:2]` returns owned copies. `basic_slice` coerces to `slice` (C++ implicit
  via `Slice(BasicSlice)` ctor). C++ structs: `tpy::BasicSlice` and `tpy::Slice`.
  Callable constructors `basic_slice(start, stop)` and `slice(start, stop, step)`
  available; slice objects can be stored, passed, and returned. Stepped slice
  assignment (`a[::2] = [...]`) supported with runtime length/step-compatibility
  check; step=1 allows resize; generator RHS accepted.

**Effort**: S (Phase 1, done), M (Phase 2, done), S (Phase 3, done)

---

### Properties

```python
class Circle:
    _radius: float

    @property
    def radius(self) -> float:
        return self._radius

    @radius.setter
    def radius(self, value: float) -> None:
        if value < 0:
            panic("negative radius")
        self._radius = value

c = Circle()
c.radius = 5.0       # calls setter
print(c.radius)      # calls getter
```

Maps to C++ getter/setter methods, with field-access syntax desugared in codegen.

**Why it matters**: Common Python OOP pattern for encapsulation. Without properties,
users must use explicit `get_x()`/`set_x()` methods, which is un-Pythonic.

**Current state**: Done. Getter (`@property`), setter (`@x.setter`), inheritance, readonly propagation (const/mutable overloads for non-value return types), borrow tracking through `return_borrows_from`, union and optional return types all working. Augmented assignment (`obj.x += 1`) not yet supported. `@x.deleter` not supported.

**Dependencies**: None.

**Effort**: M (done)

---

### Class-Level Constants

```python
# Pure-TPy class constant
class HttpClient:
    TIMEOUT: Final[int] = 30
    DEFAULT_HEADERS: Final[StrView] = "User-Agent: tpy"

    def fetch(self, url: StrView) -> None:
        timeout = HttpClient.TIMEOUT
        ...

# @native extern binding (the original motivating use case)
# tpy: native_module
# tpy: cpp_namespace("x::core")
# tpy: include("<x/build_opts.hpp>")
@native
class BuildOpts:
    FLAG: Final[bool]            # binds to ::x::core::BuildOpts::FLAG
```

`Final[T] = value` in a class body declares a class-scoped immutable constant
(PEP 591 implicit-`ClassVar` rule). On `@native` classes, `Final[T]` without
an initializer binds to a C++ `static` member declared in the user's header.
Use site emits `<declaring_qname>::<member>` -- including instance-side
reads (`obj.X`) and inherited reads (`Child.X` -> `Parent::X` via MRO).
Mutable `ClassVar[T] = value` (PEP 526) is a `static inline` slot with
well-defined cross-TU semantics; subclasses may *shadow* a non-final
`ClassVar` by redeclaring it with matching finality and type. Generic
classes with class constants ship in a later phase.

**Why it matters**: TPy currently has only instance fields and module-level
globals -- no class-scoped storage. The `@native` case is the loudest symptom
(forces `native_global("ns::Class::FIELD")` with the namespace duplicated), but
the gap is general -- idiomatic Python class constants (`MyClass.TIMEOUT`,
lookup tables on a type) hit the same wall today, with the misleading error
`'ClassName' is not a variable` at every use site.

**Current state**: Done (all 10 phases). Design in `docs/CLASSVAR_DESIGN.md`.
Working surface: `Final[T] = value` (read-only class constant,
`static constexpr`), `ClassVar[T] = value` (mutable class slot,
`static inline`, with allow-list narrower than Final to avoid
dangling-view hazards), `ClassVar[Final[T]] = value` (explicit alias for
`Final[T] = value`), `@native` extern `Final[T]` no-value binding,
forward refs within a class body, instance-side `obj.X` reads and
writes, aug-assign on both forms, MRO walk for `Child.X` -> `Parent::X`
(emits the declaring class's qname including for cross-module ancestors
the accessing module never imported), side-effecting and optional
receivers handled cleanly on both read and write sides,
`native_field("rename")` per-symbol rename on `@native` class
constants, subclass shadow of non-final `ClassVar` with
same-type/finality (multi-base validates against every declaring
ancestor), T-independent class constants on generic classes
(`class C[T]: MAX: Final[Int32] = 10`, accessed via instance or self
through the parameterized qname `C<int32_t>::MAX`), and full validation
gates (name conflicts, Final-override blocking, cross-finality
rejection, multi-base ambiguity, T-dependent rejection on generics,
bare-class access on generic rejection, subclass-of-generic rejection,
Final-mutation rejection, instance-write CPython-divergence warning
matching mypy/pyright). Deferred items live in `docs/CLASSVAR_DESIGN.md`
"Future Extensions": T-dependent class constants on generics
(per-monomorphization initializers), bare-class access on parameterized
generics (`C[Int32].X` syntax), inheritance through fixed-type-arg
parents (`class Child(C[Int32])`).

**Dependencies**: None. Reuses module-level `Final` allow-list, `RecordInfo`
plumbing (`native_name`, `module`), and `_get_qualified_cpp_name`.

**Effort**: L (S-M for v1; remainder split across Phases 5-10)

---

### `@override` Decorator

```python
from typing import override

class Dog(Animal):
    @override
    def speak(self) -> str: return "woof"
```

Python 3.12's `typing.override`: marks methods that override a parent/protocol method.
Error if the method doesn't actually override anything (typo protection).

**Why it matters**: Catches method name typos at compile time. C++ codegen already emits
`override` automatically for detected overrides -- this adds explicit user intent.

**Current state**: Done. `from typing import override` resolves. Sema validates `@override`
methods against the full parent class chain and all explicitly implemented protocols.
Error if no match (typo protection). For parent class overrides (non-virtual), a warning
is emitted explaining that `Parent`-typed references use static dispatch and suggesting
`@dynamic` protocols for runtime dispatch (the suffix is omitted when the class already
implements a `@dynamic` protocol for the same method). `@override` suppresses the
existing implicit method-hiding warning for that method, replacing it with the more
targeted non-polymorphic warning. `@override __init__`/`__del__` skips the
non-polymorphic warning (constructors are never polymorphically dispatched) but still
errors if the parent has no matching method. `@override` + `@staticmethod` is a parse
error. Duplicate method definitions in the same class are now also a sema error.

**Dependencies**: None.

**Effort**: S (done)

---

### set Type

```python
s: set[Int32] = {1, 2, 3}
s.add(4)
s |= {5, 6}
if 2 in s:
    print("found")
for x in s:
    print(x)
```

Maps to `tpy::ordered_set<T>` -- a hash set (`std::unordered_map<T, Node*>`) combined
with an intrusive doubly-linked list to preserve insertion order (matching Python 3.7+
dict ordering convention, though CPython sets don't guarantee order).

**Why it matters**: Common Python data structure for membership testing and deduplication.
Less critical than dict but still frequently used.

**Current state**: Done. Covers `SetType` in type system, set literals `{a, b}`,
`set()` constructor, `len(s)`, `x in s`, `for x in s` iteration, `print(s)`,
methods `add()`/`remove()`/`discard()`/`pop()`/`clear()`/`copy()`/`update()`,
operators `|` (union), `&` (intersection), `-` (difference), `^` (symmetric
difference), augmented assignment `|=`/`&=`/`-=`/`^=` (in-place mutation),
comparison `==`/`!=`, nested container support (`list[set[T]]`, `tuple[set[T], ...]`).
Operator dispatch uses declarative type registry (`__ior__`, `__iand__`, etc.)
rather than hard-coded paths. Runtime backed by `tpy::ordered_set<T>` with
insertion-order preservation.

**Dependencies**: `Hashable` protocol (for elements).

**Effort**: L (done)

---

### bytes Type

```python
data: bytes = b"hello"
first_byte: Int32 = data[0]
```

Maps to `std::vector<uint8_t>`. `bytearray` is a mutable alias. `BytesView` maps to
`std::span<const uint8_t>` for zero-copy views.

**Why it matters**: Needed for binary I/O, network protocols, and file handling.

**Current state**: Done. `bytes` (immutable), `bytearray` (mutable), and `BytesView`
(zero-copy span view) types implemented. Literals (`b"hello"`), subscript, iteration,
`len()`, `print()`, comparison, `encode()`/`decode()` conversions, search helpers.
Context-dependent type resolution via `PendingBytesType`. See `TODO.md` for remaining
follow-ups (mutation tracking for BytesView borrows, `__hash__`).

**Dependencies**: Done.

**Effort**: M (done)

---

### Dynamic Attributes

```python
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--name", type=str)
args = parser.parse_args()
print(args.name)  # attribute set dynamically at runtime
```

Support for `__getattr__`/`__setattr__` -- accessing attributes not declared as
fields at compile time. Needed for CPython stdlib compatibility where libraries
like `argparse`, `types.SimpleNamespace`, and `json` produce objects with
dynamically-set attributes.

**Why it matters**: The project goal is to run CPython code without changes.
`argparse.Namespace` is the canonical example -- `args.name` accesses an attribute
that was set dynamically by `add_argument("--name")`. Without dynamic attribute
support, `argparse` and similar stdlib modules cannot be stubbed with compatible
APIs. Users would need to rewrite code to use TPy-specific alternatives, which
defeats the compatibility goal.

The compatibility-first approach: make `args.name` work on a `Namespace` object
(even if slow). Users who care about performance can then gradually migrate to
typed patterns:

```python
# Step 1: CPython-compatible, works unchanged (uses dynamic attributes + Any)
args = parser.parse_args()
print(args.name)

# Step 2: Typed for performance (user opts in when ready)
@dataclass
class Args:
    name: str
    count: Int32
args: Args = parse_args_typed(Args, sys.argv)
print(args.name)  # static field access, zero overhead
```

Design options:
- **`__getattr__`/`__setattr__` dunders**: Generate `std::unordered_map<std::string, std::any>`
  fallback for attribute access. `__getattr__` is only called when normal lookup fails
  (matching Python semantics). Heavy, but fully compatible.
- **Declaration-based**: Require a type stub that declares the expected dynamic
  attributes, so the compiler knows what's available. Similar to TypedDict but
  for attributes. More static, but requires stubs for each library.

The `__getattr__` approach is needed for full CPython compat. Declaration-based
stubs can layer on top for common libraries (argparse, json) to provide better
type checking and performance when the attribute set is known.

**Current state**: Done. v1 + v1.5 (phases 7-9) shipped. User-defined
`__getattr__` / `__setattr__` / `__delattr__` recognized and routed
through sema-synthesized `TpyMethodCall`s. Builtin `getattr` / `setattr`
/ `delattr` work for both literal and runtime names; `hasattr` and 3-arg
`getattr` wrap the dunder call in a try/catch lambda IIFE that converts
`AttributeError` to a boolean / default. With a literal name, declared
members fold to compile-time True / direct access. With a runtime name,
the builtins route unconditionally to the dunder (Option A; CPython
divergence documented in `docs/DYNAMIC_ATTRS_DESIGN.md` divergence #8).

`AttributeError` is a throw-tier exception (inherits `Exception`).
`raise AttributeError(name)` from any function compiles to a normal C++
throw and propagates through the call stack; catchable via
`try/except AttributeError`. Unhandled at top-level, the runtime's
terminate handler prints "TurboPython panic: uncaught tpy::AttributeError:
<name>". CPython parity preserved -- the raise/catch shape is identical
on both backends.

v1 is fallback-only: declared field/method/property/class-constant
writes never route through `__setattr__` (intentional CPython divergence
that avoids the recursion gotcha). `__getattr__` return type must be a
value type, `Any`, or `Own[T]`; bare reference / view types rejected.
Inheritance via MRO works for all three dunders. TPy does not recognize
CPython's `object.__setattr__` escape syntax -- code that defines
`__setattr__` and writes declared fields in `__init__` works under TPy
but recurses under CPython; tests using that shape are TPy-only via
`no_cpython.txt`.

Phase 9 (dynamic-name 2-arg builtins: `getattr(obj, name_var)`,
`setattr(obj, name_var, v)`, `delattr(obj, name_var)`) is deferred
pending a divergence call (route all dynamic to dunder vs runtime
dispatch + dunder fallback). See `docs/DYNAMIC_ATTRS_DESIGN.md` for the
full design including divergences, future extensions, and the broader
adjacent design space.

**Dependencies**: `Any` type (D15, done) -- dynamic attributes typically
return `Any`. `D22` multi-inheritance for the MRO routing.

**Effort**: M-L. v1 + v1.5 (phases 7-9) shipped. v2 features driver-dependent.

---

### TypedDict

```python
from typing import TypedDict
from tpy import Int32

class UserInfo(TypedDict):
    name: str
    age: Int32
    active: bool

def process(info: UserInfo) -> None:
    print(info["name"])     # -> str (compile-time resolved)
    print(info["age"])      # -> Int32
    # info["unknown"]       # compile error: key not in UserInfo
    # info[variable]        # compile error: key must be string literal

# Construction (keyword arguments)
user = UserInfo(name="Alice", age=Int32(30), active=True)
```

A dict-like type where keys are fixed string literals with per-key value types.
Requires compiler support because the return type of `d["key"]` depends on which
string literal is used -- this can't be expressed with regular generics.

**Why it matters**: Two main use cases:
1. **Typed `**kwargs`** (PEP 692): `def connect(**kwargs: Unpack[ConnectOptions])` where
   `ConnectOptions` is a TypedDict. This is the clean path for D18 -- kwargs become
   named parameters, fully typed, zero overhead.
2. **Structured data interchange**: JSON-like data with known schemas, config dicts,
   API responses. A TypedDict gives dict syntax with struct safety.

**C++ mapping**: A TypedDict is a struct that pretends to be a dict. Codegen emits
a plain C++ struct; `d["name"]` compiles to `d.name` (field access). No hash map
overhead. `.get()` and `"key" in td` are supported; remaining dict-like API
(`.keys()`, `.items()`, iteration) can be generated as methods on the struct.

```cpp
struct UserInfo {
    std::string name;
    int32_t age;
    bool active;
};
// d["name"] -> d.name (sema resolves string literal to field at compile time)
```

**`total` parameter**: Python supports `total=False` for optional keys. In TPy this
maps to `Optional[T]` fields:
```python
class Partial(TypedDict, total=False):
    name: str       # Optional -- may be absent
    age: Int32      # Optional
```

**Current state**: Done. `class Foo(TypedDict):` with string-literal subscript,
keyword-only construction, `total=False` for optional fields (maps to `Optional[T]`,
`d["key"]` panics on absent), `**kwargs: Unpack[TD]` for typed variadic keywords.
Field `= default` values are ignored with a warning (matches CPython runtime).
`.get("key")` and `.get("key", default)` for safe access (returns `Optional[T]` or `T`).
`"key" in td` for field presence checks (`has_value()` for `total=False`, constant
`True` for `total=True`).

**Not yet supported** (see also `docs/LANGUAGE_FEATURES.md` TypedDict entry):
- Per-field `NotRequired[]` / `Required[]` (PEP 655)
- Dict-like methods: `.keys()`, `.values()`, `.items()`, `.update()`, `.pop()`, `.setdefault()`
- `len(td)`, `del td["key"]`, iteration (`for k in td`)
- Dict literal construction (`{"name": "Alice"}` as TypedDict)
- `**td` unpacking in dict contexts
- Type parameters on TypedDict
- TypedDict inheritance
- All keys must be compile-time string literals (no dynamic access)

**Dependencies**: None for basic form. Typed `**kwargs` (D18) is the primary consumer.
`Unpack` from `typing` needed for PEP 692 integration.

**Effort**: Basic done. Remaining: S-M (dict-like API, `total=False`)

### Multiple Inheritance

```python
class LoggingMixin:
    def log(self, msg: str) -> None:
        print(f"[{type(self).__name__}] {msg}")

class Serializable:
    def to_json(self) -> str: ...

class Service(LoggingMixin, Serializable):
    def run(self) -> None:
        self.log("starting")
```

Multiple base classes, primarily for the mixin pattern. Python uses C3 linearization
(MRO) to resolve method order; C++ uses virtual inheritance for diamond cases.

**C++ mapping**: C++ natively supports multiple inheritance. The straightforward mapping
is `class Service : public LoggingMixin, public Serializable { ... }`. Diamond inheritance
requires `virtual` base classes. TPy should start with the simple non-diamond case
(error on diamond) and add virtual inheritance later if needed.

**Scope**: Mixin-style multiple inheritance where base classes provide independent
functionality. Diamond inheritance (where two bases share a common grandparent) is
a future extension. `super()` uses MRO-aware single-hop resolution (v2.3); full
Pythonic cooperative `super()` chaining (see "Non-goals" below) is not planned.

**Current state**: Working. Static multiple inheritance: `class Child(A, B, ...)`
emits non-virtual C++ multiple inheritance. C3 linearization (MRO) is computed at sema
registration; method resolution, field inheritance, and `isinstance` fold compile-time
via MRO membership. Diamonds are rejected with a diagnostic pointing at `@dynamic`.
Multiple bases may declare `__init__`; the child must invoke each explicitly via
`BaseN.__init__(self, ...)` -- `super().__init__(...)` covers only the MRO-first
`__init__` base, so multi-base classes with >1 `__init__` bases still need explicit
calls for the rest.

**Shipped** (static, non-virtual):
- C3 linearization + diamond rejection (`Diamond inheritance not supported: '{anc}' is
  reachable from multiple bases of '{record}' (via '{p1}' and '{p2}'). Use @dynamic for
  runtime polymorphism.`).
- Cross-base conflict detection: field-conflict error, method-conflict error (requires
  child override for disambiguation).
- Source-order / MRO-order consistency check (base decls must match C3 linearization).
- `super()` in multi-base: allowed when the method is unambiguous across direct parents;
  `__del__` rejected outright (C++ auto-invokes each base destructor).
- `isinstance(child, Mixin)` folds to True at compile time for any base in the MRO;
  downcasts still warn/fold to False (the existing hierarchy-isinstance behavior).
- **`BaseN.method(self, ...)`** for non-static methods: routes
  `ClassName.method(args)` through instance-method machinery when `ClassName` is a
  strict ancestor of the current record and the first arg is literally `self`. Method
  lookup walks `ClassName`'s MRO so inherited definitions resolve. Codegen emits
  `ClassName::method(args)` inside the child method body (the implicit `this->`
  qualifies the call). `BaseN.__init__(self, ...)` is only legal inside the child's own
  `__init__`; `BaseN.__del__(self)` is rejected.
- Multi-base `__init__` coverage validator: every base with `__init__` must be invoked
  explicitly from the child's `__init__` (either form). Base-init calls must be
  top-level statements; nesting in control flow is rejected with a targeted error.
  C++ MIL is emitted in declaration order regardless of how the user writes the calls
  (avoids `-Wreorder` in generated code); out-of-declaration-order source is flagged
  with a sema warning since C++ evaluates init arguments in MIL order too.

**Restrictions** (with targeted diagnostics):
- Diamond inheritance is rejected outright; users with shared ancestors should
  make the shared base a `@dynamic` protocol. Tracked in "Future Extensions".
- Full Pythonic cooperative `super()` chaining is not implemented; v2.3 provides
  MRO-aware single-hop resolution at the child's `super()` call site only.
  Tracked in "Future Extensions".

The static-vs-dynamic tradeoff that all class hierarchies hit -- heterogeneous
containers, polymorphic mixin parameters -- is not a D22 gap; it is the
language-wide answer that `@dynamic` is for.

**v2.2 -- same-name fields across bases (shipped).** Two ancestors may declare
fields with the same name, regardless of privacy. Each subobject legitimately
owns its own slot; the user disambiguates reads, writes, and augmented
assignments via `BaseN.field` (the unbound-self field form, mirroring v2.1
`BaseN.method(self, ...)`). Entry points:
`ExpressionAnalyzer._try_unbound_self_field_access` in `tpyc/sema/expressions.py`
(strict-ancestor check, walks BaseN's MRO via `lookup_record_field`, substitutes
generic parent type params, propagates readonly); codegen emits
`this->BaseN::field` in `tpyc/codegen_cpp/expressions.py:_gen_field_access`.
Unqualified `self.field` errors with an "ambiguous, use `A.field` / `B.field`"
diagnostic when more than one direct parent's MRO reaches the name
(`ExpressionAnalyzer._try_find_field` + `ProtocolAnalyzer.find_field_parent_branches`).
A child field that shadows an inherited one emits a warning at the child's decl
site (any inheritance shape, not just multi-base).

**v2.3 -- MRO-aware `super()` resolution (shipped).** `super().method()` in a
multi-base child walks the child's C3 MRO and dispatches to the first ancestor
whose *own* method table defines `method` (matching Python's `__dict__` walk;
inherited methods on an ancestor don't count, so codegen may target a
grandparent directly). Entry point: `_resolve_super_parent_type` in
`tpyc/sema/methods.py`. Two direct parents both defining the method is no
longer an ambiguity error; the MRO-first one wins. `super().__init__()`
covers one `__init__` base (the MRO-first one) -- the coverage validator
still requires explicit `BaseN.__init__(self, ...)` calls for other bases.
`super().__del__()` remains rejected in multi-base. Single-hop only; full
cooperative chaining is tracked in "Future Extensions".

**Effort**: L (shipped through v2.3).

---

### Nested Classes

```python
class Tree:
    class Node:
        value: Int32
        left: Ptr[Tree.Node]
        right: Ptr[Tree.Node]

        def __init__(self, value: Int32) -> None:
            self.value = value
            self.left = None
            self.right = None

    root: Ptr[Tree.Node]
```

Class definitions inside another class body. Standard Python feature.

**C++ mapping**: Nested struct/class. `Tree::Node` in C++. Straightforward codegen
since C++ natively supports nested types with the same semantics.

**Why it matters**: Common pattern for helper types that belong to a parent class.
Also a prerequisite for macro companion type creation (`cls.add_companion_type`) --
macros generating helper types (e.g. key enums for O(1) JSON field dispatch) need
the compiler to support nested type definitions.

**Current state**: Done. Arbitrary nesting depth supported (`Outer.Mid.Deep`). Both
nested classes and enums work. Short names resolve inside the class body (`kind: Kind`
instead of `kind: Container.Kind`) for CPython compatibility. Nested classes and enums
inside generic parents are not supported. Nested protocols not supported.

**Dependencies**: None beyond existing class support.

**Effort**: M (done -- parser, sema, codegen, expression resolution)

---

## VIII. Compile-Time Safety (Python Runtime Checks)

Things Python checks at runtime that TPy can verify statically.

### Already Done

| Python Runtime Check | TPy Status |
|---------------------|------------|
| `TypeError: unsupported operand type(s)` | Done (sema type checking) |
| `AttributeError: has no attribute` | Done (record field checking) |
| `TypeError: takes N positional arguments` | Done (call checking) |
| `UnboundLocalError: referenced before assignment` | Done (init_tracker) |
| Bounds checks (`IndexError`) | Runtime (`normalize_index` in `__getitem__`) |

### Could Do

| Python Runtime Check | TPy Opportunity |
|---------------------|-----------------|
| `RuntimeError: dict changed size during iteration` | Container mutation check (see IV) |
| `IndexError: list index out of range` (some cases) | Bounds check elision via integer range analysis (see below) |
| `OverflowError` for fixed-width ints | Compile-time range analysis for literal arithmetic (see below) |
| `RecursionError` | Detect unbounded recursion in simple cases |
| `TypeError: unhashable type` | Working -- `Hashable` protocol check for dict keys and `hash()` |
| Use after `.close()` / resource lifecycle | Typestate analysis (see below) |
| Missing match cases | Exhaustive pattern matching (see VI) |

### Integer Range Analysis / Bounds Check Elision

Track provable value ranges of integer variables to elide redundant runtime checks.

```python
i: Int32 = 0
while i < len(arr):
    x = arr[i]      # i is in [0, len(arr)) -- bounds check elided
    i += 1

for i in range(len(arr)):
    x = arr[i]      # same -- i provably in bounds
```

**Why it matters**: Every `__getitem__` call goes through `normalize_index` (negative
index normalization + OOB panic). In tight loops where the index is provably non-negative
and in bounds, this is redundant overhead. The canonical `while i < len(arr)` and
`for i in range(len(arr))` patterns are extremely common and always safe.

This is a prerequisite for competitive performance with hand-written C++ in
array-heavy code. Users can work around it today with `unchecked_get()`, but
automatic elision is the goal.

**Scope**: Integer range analysis is broader than just bounds elision:
- **Bounds check elision**: skip `normalize_index` when index is provably in `[0, size)`
- **Overflow check elision**: skip `add_check`/`mul_check` when result provably fits
- **Negative index elision**: skip negative normalization when index is provably >= 0
- **Static OOB detection**: error at compile time for provably-OOB access (e.g. `arr[10]`
  on `Array[T, 3]`)

**Approach**: Attach `[lo, hi]` interval to each integer variable, propagate through
assignments, comparisons (branch narrowing), and arithmetic. Start with the simple
case: loop counter bounded by `len()`. Full interval arithmetic is a larger effort.

**Current state**: Partial. `ValueRange` class in `sema/value_range.py` tracks `[lo, hi]`
intervals with symbolic `hi_len_of` bounds. Bounds check elision works for simple patterns
(variable index with range provably in `[0, len(container))`). Flow facts integrate range
tracking with control flow. `unchecked_get()` remains as a manual escape hatch. Not yet
done: complex index expressions, overflow check elision, static OOB detection for Array.

**Dependencies**: None for the basic loop pattern. Full interval arithmetic interacts
with overflow checking.

**Effort**: M for loop-bounded patterns (partial), L for general interval arithmetic

---

### Typestate (Object Lifecycle)

Track object state transitions at compile time:

```python
class File:
    @states("closed", "open")

    def open(self: File["closed"], path: str) -> File["open"]: ...
    def read(self: File["open"]) -> str: ...
    def close(self: File["open"]) -> File["closed"]: ...

f = File()
f.read()   # compile error: File is in state "closed"
```

**Why it matters**: Python programs are full of implicit state machines (connections, files,
iterators, builders). Failures are runtime `ValueError` / `AttributeError`. No mainstream
language has fully solved typestate, but even a limited version (just "consumed" vs "alive",
which TPy's move semantics already partially provide) is valuable.

**Current state**: Not started. Speculative / research-level.

**Dependencies**: Effect system (state transitions are effects).

**Effort**: XL (research + design + implementation)

---

### Dead Code Detection

Warn on unreachable code (after `return`/`break`/`continue`, unreachable branches)
and unused variables/imports.

**Why it matters**: Python doesn't warn about dead code. Compiled languages typically do.
Easy wins: unreachable statements after `return`, unused local variables, unused imports.
Harder: unused functions/methods (needs whole-program analysis).

**Current state**: Not started.

**Dependencies**: None for basic detection. Whole-program analysis for unused functions.

**Effort**: M (basic unreachable + unused locals; L for whole-program)

---

### Error Recovery / Multi-Error Diagnostics

Currently the compiler stops at the first error. Better UX: continue analysis after
errors and report multiple diagnostics per compilation.

```
file.py:5: error: Unknown type 'Foo'
file.py:12: error: No matching overload for 'bar(Int32)'
file.py:18: warning: Unused variable 'x'
3 errors, 1 warning
```

**Why it matters**: Stopping at the first error forces users into a fix-one-recompile loop.
Reporting all errors at once is much more productive, especially for large files.

Implementation approach: introduce an `Invalid` type that propagates through analysis
without triggering cascading errors. Expressions involving `Invalid` silently produce
`Invalid` rather than new error messages.

**Current state**: Partial -- infrastructure only. `SemanticContext.emit_error()` collects
non-fatal diagnostics, but the analyzer still raises `SemanticError` (fail-fast) on most
error paths. No `Invalid` type and no widespread recovery: a single semantic error still
aborts the module. Activating multi-error reporting requires converting the bulk of
`raise SemanticError(...)` sites to `emit_error()` + `Invalid`-propagation.

**Dependencies**: None, but touches sema broadly (every error path needs graceful recovery).

**Effort**: L (pervasive changes across sema to not bail on first error)

---

## IX. Concurrency (Future)

### async/await or Alternative Model

Full design in [`docs/ASYNC_DESIGN.md`](ASYNC_DESIGN.md). Summary:

- **Lowering**: state-machine struct with `poll(waker) -> Poll[T]` (Rust-shaped). Shared "resumable-frame" abstraction in codegen, not C++20 coroutines (preserves a future LLVM backend). The frame is shape-neutral: non-simple generators (`yield`) now share it with `await` (the simple-generator lambda peephole stays separate), so `yield` and `await` lowering are unified.
- **Awaitable protocol**: structural, like `Iterator[T]` -- type is awaitable iff it has `poll(self, waker: Waker) -> Poll[T]`.
- **Cancellation**: exception-based via `cancel_pending` flag + `CancelledError` thrown at the next suspension; per-case try/except/finally re-establishment in codegen.
- **Executor**: single-threaded for v1; multi-threaded executor and `Send`/`Sync` constraints are v3+.
- **Task[T]**: purpose-built type-erased poll-box (~50 LOC C++ template), independent of `@dynamic` infrastructure.
- **Milestones (status)** -- track in [`docs/ASYNC_PROGRESS.md`](ASYNC_PROGRESS.md):
  - **v1 (shipped)**: `async def`/`await`/`run`/`sleep`/`create_task`/`Task`/`Future`/`cancel`.
  - **v1.5 (shipped, M1-M11)**: `async with`/`async for`/`gather`/`gather_list`/`wait_for`/`Event` + arbitrary `await` placement (if/while/for/with/try/finally via CFG-lite) + async methods on user/generic classes + sync `with` upgrade for 4-arg `__exit__`.
  - **runtime port (shipped)**: the asyncio executor/run-loop/scheduling/type-erasure are now pure TPy on a small `async.hpp` bridge (G1.5).
  - **v2 (shipped, M1/M2)**: sync primitives (`Lock`/`Semaphore`/`BoundedSemaphore`/`Queue`), epoll I/O reactor (`EpollReactor` + `sock_recv`/`sock_sendall`/`sock_accept`/`sock_connect`), and the asyncio streams layer (`open_connection`/`start_server`/`StreamReader`/`StreamWriter`/`Server`); graceful SIGINT shutdown.
  - **v3+ (future)**: multi-threaded executor + `Send`/`Sync` enforcement at task migration, async generators, `@error_return` async, `__await__` adaptation.

### Channels

Go-style channels for inter-thread communication. Fixed-size channels are a natural
fit for `@noalloc` contexts.

**Current state**: SPSC `channel[T: Send](cap) -> (Sender[T], Receiver[T])`
shipped as the first Send/Sync enforcement site (Phase 4) -- intra-process on
the single-threaded executor, `Rc`-backed FIFO ring, blocking async
`send`/`recv`, explicit `close()`. See `docs/CHANNEL_DESIGN.md`. MPSC, `try_send`,
and the Arc-backed cross-thread channel are deferred.

### Thread Safety

Send/Sync markers (see IV) should be designed before concurrency ships.

**Current state**: Marker layer done -- Send/Sync Phases 1-4 shipped (auto-derivation,
`Send[T]`/`Sync[T]` wrappers, diagnostics, and `Channel[T]` enforcement). Only the
*cross-thread* half remains: the multi-threaded executor (Phase 5) and `Arc`/`Mutex`/`RwLock`
(Phase 6), both v3+. See section IV and `docs/SEND_SYNC_DESIGN.md`.

---

## X. Self-Hosting Prerequisites

Features needed for the compiler to compile itself, roughly by priority:

| Feature | Compiler Usage | Section |
|---------|---------------|---------|
| dict | 24 files | VII |
| tuple + unpacking | 44 files | I (done) |
| f-strings | 32 files | VII |
| enum | 17 files | I |
| exceptions (try/except) | 9 try/except blocks | III |
| default arguments | pervasive | VII |
| keyword arguments | pervasive | VII (done) |
| list comprehensions | common | VI |
| isinstance + narrowing | 824 calls, 35 files | I (ADTs replace) |
| closures/lambda | moderate | VI |
| string methods | pervasive | I (str) |
| `with` statement | moderate | VI |
| walrus operator | if useful | - |
| own parser | replaces Python `ast` | - |

See `SELF_HOSTING.md` for the full gap analysis.

---

## XI. Interop & Distribution (Future)

### CPython Interop

Full design in [`docs/CPYTHON_INTEROP.md`](CPYTHON_INTEROP.md). Tooling hooks
(the `ext` target kind, TPy as a PEP 517 build backend) are reserved in
[`docs/PROJECT_TOOLING_DESIGN.md`](PROJECT_TOOLING_DESIGN.md); this is the
interop *semantics*. Distinct from `@native` ([`docs/NATIVE_INTEROP.md`](NATIVE_INTEROP.md)),
which is the inverse (C++ visible to TPy). Summary:

- **Governing constraint**: the representation gap. TPy is unboxed/
  monomorphized/homogeneous; CPython is boxed `PyObject*` everywhere. The two
  cannot share live data, so interop is a *boundary-marshalling* story --
  cheap where the boundary is thin (primitives, bulk numeric buffers),
  expensive/fraught where thick (nested containers, live objects, callbacks).
- **Two directions, extension leads**: extension (CPython imports a TPy-built
  `.so` -- the Cython/PyO3 flow, the v1 target) and embedding (a TPy binary
  calls libpython -- reserved hooks only).
- **v1 goals**: write extensions in TPy; expose free functions, classes +
  methods, and enums + constants.
- **Shared marshalling layer** (the keystone): one direction-agnostic
  `to_py`/`from_py`, serving both directions. `PyObject*` into TPy is an owned
  `Own[PyRef]`. **v1.0 is copy-in for all input** -- `str`/`bytes` *and*
  numeric buffers (numpy/`memoryview` via the buffer protocol): a spike showed
  zero-copy needs a new foreign-borrow primitive, so zero-copy (str + buffer)
  is the first post-v1.0 work (phase 3.5, IR-gated). Containers cross by O(n)
  *copy* (a declared no-alias divergence); classes get correct reference
  semantics because the PyObject owns the instance.
- **abi3 / limited API**: committed, **floor 3.12** (spike-validated: a real
  `Py_LIMITED_API` extension compiled, imported, ran). One wheel for all
  CPython versions, and it keeps `Python.h` macros out of generated TUs.
  Glue is **hand-rolled raw C-API** (nanobind weighed and rejected for v1).
- **Exception/panic bridge**: `raise` -> `PyErr_*`; a `tpy_panic` at the
  boundary is *converted to a Python exception*, never an abort of the host.
  (Needs a small runtime change -- `tpy_panic` is `std::exit(1)` today; a
  macro-gated throwing `TpyPanic` for `.so` builds, per spike.)
- **GIL as an ambient capability**: a flow-fact (`gil_held`, modeled on
  `flow_facts.py`'s `init_terminated` -- a flow-sensitive fact, *not* the
  Send/Sync type-trait family) gates Python-touching ops; a future
  `with nogil:` removes it for its scope (compile error to touch a `PyRef`
  inside). v1 ships the *demand* (marshalling requires the capability,
  ambient-always) so `nogil` checking is purely additive later.
  "Python-touching" is library-declared (`requires_gil`), not hardcoded.
- **Reserved hooks (not v1, not locked out)**: embedding, callbacks/opaque
  `PyRef`, and async <-> `asyncio`.
- **Validated against the codebase + three spikes run**: feasible, mostly on
  existing machinery. abi3 empirically validated (floor 3.12, hand-rolled);
  panic needs a small runtime change; zero-copy input is gated on the
  foreign-borrow primitive (so v1.0 is copy-in). Two genuinely net-new pieces
  remain: a CPython-extension `.so` build-output mode (the `ext` target / PEP
  517 backend; overlaps H2) and that foreign-borrow primitive (a pre-existing
  escape-analysis gap; the first post-v1.0 work). See the "Design validation"
  and "Alternatives and spike outcomes" sections in the design doc.

**Phasing**: marshalling layer + cpython facade -> extension codegen + PEP 517
backend (free functions) -> buffer protocol -> classes + methods -> enums +
constants -> containers -> (later) `nogil`/`with gil` -> (later) embedding/
callbacks/async.

**Dependencies**: `@native` (done), `Span`/buffer protocol incl. raw-pointer
`Span(ptr,len)` (done), exceptions (done). The GIL `gil_held` flow fact is a
small extension of `flow_facts.py`; `with nogil:` needs new save/restore
scaffolding in `_analyze_with`. The zero-copy str/buffer borrow rides on the
foreign-borrow primitive / Ptr escape analysis (E4, partial) -- v1 sidesteps
it via copy-in.

---

## XII. Tooling & Project Layer

### Project / Build / Dependency Tooling

A user-facing project layer -- how someone builds, tests, and manages
dependencies for a TurboPython **application or library**. Distinct from
`BUILD_PIPELINE.md` (the compiler's internal source -> C++ -> binary pipeline),
which this layer drives.

The shape under discussion is a cargo-like porcelain command, `tpx`
(`tpx new/init/build/run/test/add/clean`, `tpx build --release`), kept separate
from the `tpy` runner so `tpy file.py` stays unambiguous (cargo-vs-rustc split).
It sits on a stable `[tool.tpy]` contract in `pyproject.toml` describing how to
*compile and link* (not how to resolve Python packages), and reuses the existing
Python packaging ecosystem (`uv` for resolution/locking/virtualenvs,
`pylock.toml`, a `tpy-build.lock`) rather than reinventing a resolver.

**Why it matters**: TurboPython is on pypi.org and meant for public use, so a
single familiar command surface and reproducible builds are part of the
product, not just internal convenience. This follows the project north star
(Rust semantics + tooling vocabulary, Python syntax + ergonomics): the language
is already Rust-shaped (`Own`/borrow/`readonly`/`Box`/`Rc`/`Weak`, two-tier
errors), and the tooling should feel like cargo/rustup over a Python base.

**Current state**: 🔬 Exploratory. Design discussion (incl. a Codex
cross-check) captured in `docs/PROJECT_TOOLING_DESIGN.md`; no code yet. Goals,
non-goals, the `[tool.tpy]` contract sketch, and the `uv` reuse / native
dependency plane are framed; concrete spec deferred to a `/tpy-add-feature`
pass.

**Dependencies**: None hard. Coordinates with build profiles (F8) and
compile-time module parameters (F11) for the build-config surface.

**Effort**: XL (CLI, project model, lockfile/reproducibility story, native
dependency plane).

