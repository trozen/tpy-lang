# Feature Roadmap

Major features and design decisions for TurboPython, organized by theme.
Each entry captures the design impact, current state, dependencies, and rough effort.

Effort scale: **S** (days), **M** (1-2 weeks), **L** (2-4 weeks), **XL** (4+ weeks).

For tactical items and bugs, see `TODO.md`.
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
| A10a | Reference elements in tuples | M | Not started | [I](#reference-elements-in-tuples) |
| A10b | `@iter_range` / `__iter_range__` protocol | S-M | Not started | [I](#iter_range--__iter_range__-protocol-zero-cost-user-defined-iteration) |
| A10c | Lazy list repeat (`[val]*N`) | S | Not started | [I](#lazy-list-repeat-valn) |
| A11 | dict type | L | Not started | [VII](#dict-type) |

### Phase B: Polymorphism Foundation

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| B1 | Box[T] heap ownership | S-M | Done | [II](#boxt----heap-ownership) |
| B2 | Union types / ADTs | L | Partial | [I](#union-types--algebraic-data-types) |
| B3 | Match/case | M-L | Not started | [VI](#matchcase-with-pattern-matching) |
| B4 | Dynamic dispatch -- @dynamic protocols | L | Done | [II](#dynamic-dispatch-dynp) |
| B5 | Per-method type parameter bounds | S-M | Done | [I](#type-parameter-bounds----per-method) |
| B6 | `# tpy:` directives | S-M | Not started | [I](#tpy-directives) |
| B7 | Float32 type | S | Not started | [I](#float32-type) |
| B8 | Dataclasses | M | Not started | [VII](#dataclasses) |
| B9 | List comprehensions | M | Not started | [VI](#list-comprehensions) |
| B10 | Union dispatch flattening | M | Not started | [VII](#union-dispatch-flattening) |
| B11 | List slicing | M | Not started | [VII](#list-slicing) |
| B12 | `Self` type | S | Not started | [I](#self-type) |
| B13 | Bi-directional type inference | M | Not started | [I](#bi-directional-type-inference) |
| B14 | `Optional[StaticProtocol]` codegen | S | Done | [II](#optionalstaticprotocol-codegen) |
| B15 | `isinstance` on static protocols (`if constexpr` + narrowing) | M | Not started | [II](#isinstance-on-static-protocols) |

### Phase C: Error Handling + Effects

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| C1 | Exception model (design) | M | Not started | [III](#exception-model-tryexceptraise) |
| C2 | Exception implementation | L | Not started | [III](#exception-model-tryexceptraise) |
| C3 | @noalloc enforcement | L | Parsed only | [IV](#noalloc-enforcement) |
| C4 | Effect framework (@nothrow, @pure) | M-L | Not started | [IV](#effect-system-generalized) |
| C5 | Cyclic dependency handling | L | Not started | [V](#cyclic-dependency-handling) |
| C6 | `@exception_result` annotation | M | Not started | [III](#exception_result-annotation) |

### Phase D: Functional + Python Compat

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| D1 | Closures / nested functions | L | Not started | [VI](#closures--nested-functions) |
| D2 | Callable type | L | Not started | [I](#callable--function-pointer-types) |
| D3 | f-strings | M | Done | [VII](#f-strings) |
| D4 | with statement | M | Not started | [VI](#with-statement-context-managers) |
| D5 | Lambda | M | Not started | [VI](#lambda) |
| D6 | Properties (@property) | M | Not started | [VII](#properties) |
| D7 | String literal types (Literal[...]) | M | Not started | [III](#string-literal-types) |
| D8 | `@override` decorator | S | Not started | [VII](#override-decorator) |
| D9 | set type | L | Not started | [VII](#set-type) |
| D10 | bytes type | M | Not started | [VII](#bytes-type) |

### Phase E: Advanced Safety

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| E1 | Send/Sync markers | S-M | Not started | [IV](#thread-safety-markers-send--sync) |
| E2 | Container mutation during iteration | S-M | Not started | [IV](#container-mutation-during-iteration) |
| E3 | del statement | S-M | Not started | [VI](#del-statement-explicit-destruction) |
| E4 | Ptr escape analysis | XL | Partial | [IV](#ptrt-escape-analysis--lifetime-tracking) |
| E5 | Auto-detect readonly | M-L | Not started | [IV](#auto-detect-readonly-from-method-body) |
| E6 | Dead code detection | M | Not started | [VIII](#dead-code-detection) |
| E7 | Error recovery / multi-error diagnostics | L | Not started | [VIII](#error-recovery--multi-error-diagnostics) |
| E8a | Drop flag (`__tpy_owned_`) | S | Done | [IV](#move-safe-destructors) |
| E8b | Sentinel field optimization (eliminate drop flag) | M | Not started | [IV](#move-safe-destructors) |
| E9 | Integer range analysis / bounds check elision | M-L | Not started | [VIII](#integer-range-analysis--bounds-check-elision) |

### Phase F: Compile-Time Power

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| F1 | Compile-time evaluation | XL | Not started | [V](#compile-time-evaluation-constexpr--comptime) |
| F2 | Macro system | XL | Designed | [V](#macro-system--metaprogramming) |
| F3 | Generators / yield | L-XL | Not started | [VI](#generators-yield) |
| F4 | Typestate | XL | Research | [VIII](#typestate-object-lifecycle) |
| F5 | Self-interpret (TPy eval in tpyc) | XL | Not started | [V](#self-interpret-tpy-eval-in-tpyc) |
| F6 | Alternative backends | XL | Not started | [V](#alternative-backends) |

### Phase G: Concurrency (Future)

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| G1 | async/await or alternative | XL | Not started | [IX](#asyncawait-or-alternative-model) |
| G2 | Channels | L | Not started | [IX](#channels) |

Phases are not strictly sequential -- items from different phases can be interleaved
based on what's most needed. E1 (Send/Sync markers) is recommended early regardless
of phase, to avoid costly retrofitting when concurrency arrives.

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
| IIFE init-list for branching `__init__` | A8 | Generate `field([&]{ if (...) return x; else return y; }())` in member init-list, removing the need for helper functions. Handles all types including `@nocopy`/const fields. |

---

## I. Type System Foundations

### Union Types / Algebraic Data Types

Tagged unions: `A | B` as a first-class type (beyond `T | None`).

```python
@sealed
class Expr:
    pass
class Lit(Expr):
    value: int
class Add(Expr):
    left: Expr
    right: Expr

def eval(e: Expr) -> int:
    match e:
        case Lit(v):   return v
        case Add(l,r): return eval(l) + eval(r)
        # compile error: non-exhaustive match
```

**Why it matters**: Two polymorphism models -- ADTs for closed hierarchies (stack-allocated,
`@noalloc`-compatible, exhaustive matching) and `Dyn[P]` for open extension (heap-allocated,
vtable-based). Most languages pick one; having both is powerful.

The TPy compiler itself has 824 `isinstance` calls across 35 files. ADTs with exhaustive
match are the natural replacement, and a prerequisite for self-hosting via tag dispatch.

Maps to `std::variant` or manual tag+union in C++. No heap allocation, no vtable.
Compatible with `@noalloc`.

**Current state**: Basic union types working (`A | B` annotations, `isinstance` narrowing,
`assert isinstance`, while-loop narrowing, assignment narrowing, type aliases). Maps to
`std::variant`. Missing: `@sealed`, match/case, exhaustiveness checking.

**Dependencies**: Enums (simpler case, good stepping stone). Match/case (consumer of unions).
Interacts with generic Optional codegen (union is a generalization of Optional).

**Effort**: L (type system + sema + codegen + exhaustiveness checker)

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

**Current state**: Partial. Core tuple type is working:
- `tuple[T1, T2, ...]` annotations and type inference
- Tuple literals `(a, b)`, single-element `(a,)`, nested tuples
- Compile-time element access `t[0]`, `t[-1]` via `std::get<N>`
- Function parameters and return types
- Generic type inference (`tuple[T, T]` infers `T`)
- Equality comparison (`==`, `!=`) with element-wise type checking
- Python-style printing `(1, 'hello')`, `(42,)` for single-element
- Immutability enforced: `t[0] = x` is rejected by sema
- Reference types require `Own[T]`: `tuple[Int32, Own[Point]]`
- Not yet supported: `Optional`/`Union` elements, ordering comparisons (`<`, `>`)
- Tuple unpacking: `a, b = expr` with fresh vars, reassignment, and `_` discard
- Nested unpacking (`a, (b, c) = ...`) and for-loop unpacking not yet supported

**Remaining**: `Optional`/`Union` elements, reference elements without `Own[]` (needs
lifetime tracking), nested unpacking, for-loop unpacking.

**Dependencies**: None (core tuple type and unpacking done). Dict iteration needs both
tuple and dict.

**Effort**: Done (core); S-M remaining for edge cases

---

### Reference Elements in Tuples

Currently tuple elements must be value types or wrapped in `Own[T]`. This prevents
surprising implicit copies when returning objects from functions (e.g. changing
`-> Point` to `-> tuple[Int32, Point]` would silently copy the Point).

The goal is to support bare reference types in tuples, matching how standalone
parameters work:

```python
# Current (v1): requires Own[]
def get_pair(p: Point) -> tuple[Int32, Own[Point]]:
    return (Int32(1), p)  # explicit copy

# Future (v2): reference semantics, no copy
def get_pair(p: Point) -> tuple[Int32, Point]:
    return (Int32(1), p)  # p stored by reference
```

C++ mapping would mirror function params: `tuple[Int32, Point]` ->
`std::tuple<int32_t, Point&>`, `tuple[Int32, Own[Point]]` ->
`std::tuple<int32_t, Point>`.

**Key challenge**: lifetime tracking. A `std::tuple<int32_t, Point&>` cannot outlive
the referenced Point. Needs the same provenance/escape analysis that already applies
to reference returns.

**Dependencies**: Provenance tracking for tuple elements, possibly tied to broader
lifetime/borrow analysis.

**Effort**: M

---

### `@iter_range` / `__iter_range__` Protocol (Zero-Cost User-Defined Iteration)

```python
@iter_range
class ArrayList[T, N: int]:
    def __iter_range__(self) -> tuple[Ptr[T], Ptr[T]]:
        return (self._storage.ptr(UInt32(0)), self._storage.ptr(UInt32(self._size)))
```

A decorator + dunder protocol for user-defined types to opt into zero-cost iteration.
The `__iter_range__` method returns a `tuple[Begin, End]` pair. The compiler dispatches
based on the element type:

- **Pointer pair** (`tuple[Ptr[T], Ptr[T]]`): dereference to get elements.
  ```cpp
  auto [__begin, __end] = obj.__iter_range__();
  for (auto* __it = __begin; __it != __end; ++__it) {
      T& x = *__it;
  }
  ```
- **Integer pair** (`tuple[Int32, Int32]`): value IS the element.
  ```cpp
  auto [__begin, __end] = obj.__iter_range__();
  for (int32_t x = __begin; x != __end; ++x) { ... }
  ```

This generalizes beyond containers -- `range()` could return a type whose
`__iter_range__` gives `(Int32(0), Int32(n))`, replacing the current special-case
`range()` optimization in the compiler with a general mechanism.

The `@iter_range` decorator also **synthesizes `__iter__`/`__next__`** from the range
pair, so explicit `iter()` calls work without hand-written iterator classes. For
`ArrayList`, this eliminates the need for a separate `ArrayListIter` class entirely.

**CPython compatibility**: The `@iter_range` CPython stub (in `lib/cpy/`) ignores
`__iter_range__` and generates `__iter__` from `__getitem__`/`__len__` instead:

```python
def iter_range(cls):
    def __iter__(self):
        for i in range(len(self)):
            yield self[i]
    cls.__iter__ = __iter__
    return cls
```

Users never write `__iter__`/`__next__` by hand -- the decorator handles both runtimes.

**Why it matters**: Currently user-defined types can only iterate via `__iter__`/`__next__`,
which allocates an iterator object and generates a while-loop with `__next_opt__()` calls.
Builtin types use `NativeIterable[T]` for zero-cost C++ range-based for, but this protocol
is not user-extensible. `@iter_range` bridges the gap for any contiguous container or
counter-based range with no overhead.

This is the key missing piece for `ArrayList[T, N]` (tplib) to fully replace the builtin
`StaticList[T, N]` with equivalent iteration performance.

**Current state**: Not started.

**Dependencies**: Tuple type (for the return type). Ptr[T] (done).

**Effort**: S-M (sema protocol detection + codegen for begin/end loop + iterator synthesis)

---

### Lazy List Repeat (`[val]*N`)

```python
items = [0] * 10                       # deduces to list[Int32] (as today)
a = ArrayList[Int32, 10]([0] * 10)     # no intermediate list -- iterates lazily
fill_container(items=[1] * 100)        # items: Iterable[Int32] -- lazy
```

Make `[val]*N` produce a lazy `RepeatRange[T]` type instead of eagerly allocating a
`list[T]`. The codegen already emits `tpy::repeat_range<T>(count, {val})` which is
lazy -- the only change is in sema, which currently resolves `[val]*N` to `ListType(T)`
immediately.

With a lazy type that conforms to `Iterable[T]`:
- **Assigned to `list[T]`** or untyped local: materializes to list (default, as today)
- **Passed to `Iterable[T]` param**: stays lazy, consumer iterates without allocation
- **Passed to `Span[T]` param**: materializes to contiguous storage

**Why it matters**: Without this, `ArrayList[Int32, 10]([0]*10)` would first heap-allocate
a `std::vector`, copy into the ArrayList's stack storage, then destroy the vector.
With lazy repeat, the constructor iterates the range directly -- zero heap allocation.
Benefits any container constructor that accepts `Iterable[T]`.

**Current state**: Not started. Codegen (`tpy::repeat_range`) is already lazy; sema
eagerly resolves to `ListType` which forces materialization.

**Dependencies**: Iterable[T] protocol (A9) for the lazy type to conform to.

**Effort**: S (sema type change + deferred materialization in codegen)

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

Probably all three depending on context. The type system needs a new type kind.
Variadic type params for argument types is a design question.

**Current state**: Not started. No `CallableType` or `FunctionPointerType` anywhere.

**Dependencies**: Closures depend on this. `@noalloc` interaction needs effect system.

**Effort**: L (type system + multiple codegen strategies)

---

### Final / Constant Globals

**Done.** `Final[T]` for module-level constants. Emits `inline constexpr` (or
`extern const` for BigInt). Supports fixed-width ints, float, bool, str, Char, BigInt.
`__name__` is a synthetic `Final[str]`. ALL_CAPS without `Final` warns.
See Future Extensions table for planned enhancements.

---

### `# tpy:` Directives

Per-module configuration via source comments:

```python
# tpy: default-int=Int64
# tpy: range-check=off
# tpy: include("SDL2/SDL.h")
# tpy: link("SDL2")
```

**Why it matters**: Multiple features depend on this infrastructure -- `default-int`
(already designed in INTEGER_INFERENCE_DESIGN.md), `range-check` toggle, `include`/`link`
for native interop (blocks real C library integration and DOOM port), and potentially
`profile=noalloc` for module-level performance profiles.

Without a directive system, each of these needs its own ad-hoc mechanism. A unified
`# tpy:` parser lets all of them share one implementation.

**Current state**: Not started. `--default-int` CLI flag exists but per-module override
does not. `# tpy: include`/`link` designed in NATIVE_INTEROP.md but not implemented.

**Dependencies**: None (pure infrastructure). Enables many other features.

**Effort**: S-M (parser + plumbing to sema/codegen)

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

**Current state**: Not started. Not tracked elsewhere.

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
- `try_next(it)` builtin: calls `it.__next_opt__()`, returns `T | None`
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
```

`Self` refers to the type of the current class, enabling method chaining and builder
patterns. In subclasses, `Self` narrows to the subclass type.

Maps to C++ CRTP or deduced `this` (C++23).

**Why it matters**: Common pattern for fluent APIs and builder classes. Without `Self`,
return type must be the concrete class name (breaks in subclasses) or use a type
parameter (verbose).

**Current state**: Not started.

**Dependencies**: None.

**Effort**: S (type alias in sema, codegen mapping)

---

### Bi-Directional Type Inference

Propagate expected types downward through call expressions to improve type inference
and overload resolution. See `docs/BIDIRECTIONAL_CALL_INFERENCE_DESIGN.md` for full
design.

**Why it matters**: Currently type inference is bottom-up only. This means expressions
like `connect("localhost", Int32(8080))` can't infer that `8080` should be `Int32` from
the parameter type. Bi-directional inference enables coercion-aware argument matching
and return-type-based overload filtering.

**Current state**: Not started. Design document exists with phased approach
(Phase 1b: coercion-aware matching, Phase 3: overload filtering by return type).

**Dependencies**: None for Phase 1b. Overloads (B10) for Phase 3.

**Effort**: M (phased implementation, touches sema call resolution)

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

When `__init__` has control flow (if/else, loops), field assignments inside branches
fall back to C++ body-assignment instead of the member initializer list. The field is
default-constructed, then assigned in the body:

```cpp
// Simple __init__ -> initializer list (correct)
Wrapper(int32_t tag) : tag(tag) {}

// __init__ with control flow -> body assignment (problematic)
Wrapper(std::optional<Point> p, int32_t tag) {
    if (p.has_value()) {
        this->tag = tag;     // assignment, not construction
    } else {
        this->tag = -1;
    }
}
```

This works for trivial types (`int32_t`, `double`, `bool`) but breaks for:
- **`@nocopy` (move-only) types with `__del__`**: default-construct leaves fields
  uninitialized, then move-assignment calls the destructor on garbage state (UB).
- **`const` fields** (future `readonly[T]` on fields): can only be set in init-list.

**Approach**: Detect field assignments inside control flow in `__init__` during sema
and report diagnostics:
- **Error** when the field type would cause C++ compilation failure or UB (e.g.
  `@nocopy` types with `__del__`, types without default constructors).
- **Warning** otherwise (trivial types work but the pattern is fragile).

Users can refactor complex initialization into helper functions or `@staticmethod`
factories:

```python
class Resource:
    handle: Handle
    def __init__(self, fd: Int32, wrap: bool):
        self.handle = Handle.create(fd, wrap)  # simple top-level assignment

class Handle:
    @staticmethod
    def create(fd: Int32, wrap: bool) -> Own[Handle]:
        if wrap:
            return Handle(fd)
        else:
            return Handle(Int32(-1))
```

**Current state**: Not started. Simple `self.field = param` at top level uses init-list
correctly. The problem only manifests when `__init__` has branching and the field type
is non-trivial.

**Dependencies**: None.

**Effort**: S (AST walk of `__init__` body in sema + diagnostic emission)

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

**Current state**: Not started.

**Dependencies**: `Optional[StaticProtocol]` codegen (B14). Protocol narrowing is new
-- current narrowing only handles `Optional[T]` -> `T` via `is None` checks.

**Effort**: M (isinstance mapping is S; protocol narrowing in branches is the bulk)

---

## III. Error Handling

### Exception Model (try/except/raise)

```python
try:
    result = parse(data)
except ValueError as e:
    print("bad data:", e.message)
```

**Why it matters**: This is the most design-shaping decision remaining. Affects control flow,
ownership (cleanup during unwinding), `@noalloc` (exceptions allocate), and Python compat.
Currently `raise` and `try` are both blocked at the parser level.

Design options:
- **C++ exceptions**: natural mapping, but incompatible with `@noalloc`, hard cleanup
- **`std::expected<T, E>` / Result types**: clean ownership, but breaks Python semantics
- **Hybrid**: exceptions by default, `@nothrow` for constrained contexts
- **Policy-based**: configurable per function/module (exception vs error code)

The hybrid approach fits TPy's philosophy (unconstrained by default, opt-in constraints).
`@nothrow` would be an effect annotation like `@noalloc` and `@readonly`.

**Current state**: Not started. Only `raise StopIteration` in `__next__` is special-cased.

**Dependencies**: Blocks self-hosting (9 try/except blocks), `Iterable[T]` protocol,
real-world programs. Interacts with `@noalloc` and effect system.

**Effort**: L-XL (design + implementation + ownership cleanup paths)

---

### `@exception_result` Annotation

Transforms `raise X` into Result-type returns for control-flow exceptions. Distinct from
real exceptions (C1/C2) which use stack unwinding for error handling.

```python
@exception_result(StopIteration)
def __next__(self) -> Int32:
    if self.current < self.limit:
        result = self.current
        self.current += 1
        return result
    raise StopIteration
```

**Why it matters**: Some Python patterns use exceptions for normal control flow rather than
error signaling (e.g. `StopIteration` in iterators). These are better compiled as
`std::optional` or `std::expected` returns rather than C++ exception unwinding.
`@exception_result` makes this transformation explicit and opt-in, separating the
control-flow-exception pattern from the general exception model (C1/C2).

`StopIteration` is the first and most common case -- the compiler already handles it
specially in `__next__` methods today. `@exception_result` generalizes this to any
exception type used for control flow.

**Current state**: Not started. The `StopIteration` special case in `__next__` serves as
the prototype for this feature.

**Dependencies**: None for the basic annotation. Interacts with the general exception model
(C1/C2) but can be implemented independently.

**Effort**: M (annotation parsing + sema transformation + codegen)

---

### String Literal Types

```python
def open(path: str, mode: Literal["r", "w", "rb", "wb"] = "r") -> File:
    ...

open("data.txt", "r")    # ok
open("data.txt", "x")    # compile error: "x" not in Literal["r", "w", "rb", "wb"]
```

Compile-time checked string sets for API compatibility (e.g. `open()` mode parameter).
Could map to `enum class` internally while accepting string syntax at TPy level.

**Why it matters**: Catches a common class of Python `ValueError` at compile time.
Natural extension of the type system. Works well with overloads (different return types
per literal value).

**Current state**: Not started.

**Dependencies**: Enums (internal representation). String equality at compile time.

**Effort**: M

---

## IV. Safety & Effects

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

**Current state**: Not on the radar. Concurrency deferred.

**Dependencies**: None -- can add marker protocols now, enforce when concurrency arrives.

**Effort**: S for markers, M for full propagation and checking

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

**Current state**: Not started. Infrastructure exists (`@readonly` on methods).

**Dependencies**: `@readonly` annotation on container methods (partially done).

**Effort**: S-M (scoped analysis in sema, leverage existing readonly info)

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

**Current state**: Not started. `@readonly` infrastructure fully implemented.

**Dependencies**: `@readonly` system (done). Effect framework would generalize this.

**Effort**: M-L (method body analysis, transitive call checking)

---

### Move-Safe Destructors

Prevent double-drop when objects with `__del__` are moved.

**E9a: Drop flag (`__tpy_owned_`) -- Done**

Classes with `__del__` get a hidden `bool __tpy_owned_ = true` field. The compiler
generates custom move constructor (sets source flag to `false`), destroy-and-reconstruct
move assignment (`this->~T()` + placement `new`), and a destructor guarded by
`if (!__tpy_owned_) return;`. `__del__` also implies `@nocopy` (copying would create
two owned objects that both run cleanup).

This handles all current cases: temporaries moved into functions, auto-move at last
use, variable reassignment (including loops and conditionals), and inheritance chains
where both parent and child have `__del__`.

**E9b: Sentinel field optimization -- Not started**

The drop flag adds a `bool` per object (often 8 bytes with padding). For classes
where a field has a natural "empty" state after move, that field can serve as the
sentinel instead:

| Field type | Post-move null state | Usable as sentinel? |
|---|---|---|
| `Ptr[T]`, `ReadOnlyPtr[T]` | Need explicit `other.p = nullptr` | Yes, with codegen |
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

**Current state**: E9a done. E9b not started.

**Dependencies**: `__del__` support (done). Move semantics (done).

**Effort**: E9a: S (done). E9b: M (sentinel detection, codegen changes for
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

**Current state**: Designed in LANGUAGE_FEATURES.md, not implemented.

**Dependencies**: Compile-time evaluation. `@dataclass` is the first practical use case.

**Effort**: XL

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

Approach:
- **Allow cycles by default** when all modules in the cycle are declaration-only
  (types, functions, constants). This is the common case.
- **Error on cycles with init-order dependency**: when a module has top-level statements
  that depend on a not-yet-initialized module in the cycle (partially-initialized state).
- **C++ forward declarations**: for types used only by pointer/reference across the cycle
  boundary, emit forward declarations to break the include cycle.

**Why it matters**: Real-world projects inevitably have circular imports. The current
compiler has no handling -- cyclic imports would cause infinite recursion or undefined
behavior in compilation order.

**Current state**: Not started.

**Dependencies**: Multi-module compilation (done). Header organization (related).

**Effort**: L

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
is a compile-time guarantee Python doesn't have. Start with literal + type patterns (v1),
structural patterns later.

**Current state**: Not started.

**Dependencies**: Enums (simplest case). Union types (class patterns). Narrowing system
(pattern match narrows types in case bodies).

**Effort**: M-L (parser + sema narrowing + codegen)

---

### with Statement (Context Managers)

```python
with open(path) as f:
    data = f.read()
# f.__exit__() called automatically
```

Maps to C++ RAII / scoped blocks. Natural fit.

**Current state**: Not started.

**Dependencies**: `__enter__` / `__exit__` protocol.

**Effort**: M

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

**Current state**: Not started.

**Dependencies**: `Callable` type (the return type). Ownership model (capture semantics).

**Effort**: L (capture analysis, codegen for functor structs)

---

### List Comprehensions

```python
squares = [x * x for x in range(10)]
evens = [x for x in items if x % 2 == 0]
```

Maps to loop + `push_back` or range pipeline. Very Pythonic, high-frequency usage.

**Current state**: Not started.

**Dependencies**: None for basic form. Filter clause needs bool coercion.

**Effort**: M

---

### Generators (yield)

```python
def fibonacci() -> Iterator[int]:
    a, b = 0, 1
    while True:
        yield a
        a, b = b, a + b
```

Maps to state-machine class implementing `OptIterator`. Could be zero-alloc if
state machine is stack-allocated.

**Current state**: Not started. `yield` is in the parser's unsupported keyword list.

**Dependencies**: Iterator protocol (done). Tuple unpacking (done).

**Effort**: L-XL (state machine transformation is non-trivial)

---

### Lambda

```python
items.sort(key=lambda x: x.priority)
result = map(lambda x: x * 2, values)
```

Maps to C++ lambda expressions. Syntactic sugar over closures -- `lambda x: expr`
is equivalent to a single-expression closure.

**Current state**: Not started. `lambda` is in the parser's unsupported keyword list.

**Dependencies**: Closures / nested functions (capture semantics). `Callable` type.

**Effort**: M (once closures are done, lambda is incremental)

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

**Current state**: Not started.

**Dependencies**: `init_tracker` (done). Move semantics (done).

**Effort**: S-M

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

**Current state**: Not started. TPy classes have partial overlap (fields from annotations,
auto-declare from `__init__`). See `docs/CONSTRUCTOR_DESIGN.md` Decision 5 for design notes.

**Dependencies**: None for basic form. Full `@dataclass` needs default values.

**Effort**: M

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

Maps to `std::unordered_map<K, V>`.

**Why it matters**: Used in 24 files of the compiler source. Most Python programs use dicts.
Relatively straightforward type addition but needs: subscript codegen, iteration (produces
tuples), literal syntax, methods (`.get()`, `.items()`, `.keys()`, `.values()`).

**Current state**: Not started.

**Dependencies**: Tuple type (for `.items()` iteration). `Hashable` protocol (for keys).

**Effort**: L (type + literals + methods + iteration)

---

### Union Dispatch Flattening

```python
class ArrayList[T, N: int]:
    def __init__(self, items: Span[T] | Iterable[T] | None = None) -> None:
        self._storage = UninitArrayStorage[T, N]()
        self._size = 0
        if items is not None:
            if isinstance(items, Span):
                for i in range(len(items)):
                    self.append(items[i])
            else:
                for item in items:
                    self.append(item)
```

Compiler optimization: detect functions/methods with union-typed parameters that dispatch
via `isinstance`, and split them into separate C++ overloads. The source stays as one
method (works in CPython as-is), but the compiler emits multiple C++ functions --
eliminating the isinstance branch at call sites where the concrete type is known.

```cpp
// Flattened by compiler:
ArrayList(std::span<const T> items) { /* span path */ }
ArrayList(Iterable auto&& items) { /* iterable path */ }
ArrayList() { /* no-arg path (items=None) */ }
```

**Why it matters**: This replaces `@overload` (dropped -- its CPython semantics are too
different from what TPy would need). The union + isinstance pattern is idiomatic Python,
works unchanged in CPython, and the compiler can optimize it to zero-overhead dispatch.
Key use cases: constructor variants (`ArrayList` from span vs iterable), methods like
`pop()` vs `pop(index)` via `index: Int32 | None = None`.

An optional annotation (e.g. `@flatten_dispatch`) could explicitly request this
optimization for library code where the performance gain matters.

**Current state**: Not started. The union + isinstance pattern works today (no optimization).

**Dependencies**: Union types (partial), default parameter values (A6), isinstance
narrowing (done).

**Effort**: M (sema pattern detection + codegen splitting)

---

### `__bool__` Protocol

**Done.** `__bool__()` structural detection, `bool()` dispatch, implicit truthiness
in `if`/`while`/`not`/`and`/`or`, `__len__() != 0` fallback, `Truthy` protocol bound.

---

### List Slicing

```python
items = [1, 2, 3, 4, 5]
first_three = items[1:3]      # [2, 3]
last_two = items[-2:]         # [4, 5]
reversed_items = items[::-1]  # [5, 4, 3, 2, 1]
```

**Why it matters**: Fundamental Python operation. Used pervasively in real code. Design
question: should slicing return a new list (Python semantics) or a view/Span (zero-copy)?

Likely approach: return a new list by default (Python compat), with a separate
`Span(items, start, end)` for zero-copy views when needed.

**Current state**: Not started.

**Dependencies**: None for basic form. Step/negative slicing adds complexity.

**Effort**: M

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

**Current state**: Not started.

**Dependencies**: None.

**Effort**: M (sema desugaring of attribute access + codegen)

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

**Current state**: Not started.

**Dependencies**: None.

**Effort**: S (decorator parsing + sema check)

---

### set Type

```python
s: set[Int32] = {1, 2, 3}
s.add(4)
if 2 in s:
    print("found")
```

Maps to `std::unordered_set<T>`.

**Why it matters**: Common Python data structure for membership testing and deduplication.
Less critical than dict but still frequently used.

**Current state**: Not started.

**Dependencies**: `Hashable` protocol (for elements).

**Effort**: L (type + literals + methods + iteration)

---

### bytes Type

```python
data: bytes = b"hello"
first_byte: Int32 = data[0]
```

Maps to `std::vector<uint8_t>` or similar.

**Why it matters**: Needed for binary I/O, network protocols, and file handling.

**Current state**: Not started.

**Dependencies**: None for basic form. Slicing for advanced usage.

**Effort**: M (type + literals + methods)

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
| `TypeError: unhashable type` | Compile-time `Hashable` protocol check for dict keys |
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

**Current state**: Not started. `unchecked_get()` on Array/Span/list provides a
manual escape hatch.

**Dependencies**: None for the basic loop pattern. Full interval arithmetic interacts
with overflow checking.

**Effort**: M for loop-bounded patterns, L for general interval arithmetic

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

**Current state**: Not started.

**Dependencies**: None, but touches sema broadly (every error path needs graceful recovery).

**Effort**: L (pervasive changes across sema to not bail on first error)

---

## IX. Concurrency (Future)

### async/await or Alternative Model

**Design space**: C++20 coroutines, green threads, actor model, or structured concurrency
(Kotlin/Swift style).

### Channels

Go-style channels for inter-thread communication. Fixed-size channels are a natural
fit for `@noalloc` contexts.

### Thread Safety

Send/Sync markers (see IV) should be designed before concurrency ships.

**Current state**: Entirely deferred.

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

