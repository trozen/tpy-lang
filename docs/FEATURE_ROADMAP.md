# Feature Roadmap

Major features and design decisions for TurboPython, organized by theme.
Each entry captures the design impact, current state, dependencies, and rough effort.

Effort scale: **S** (days), **M** (1-2 weeks), **L** (2-4 weeks), **XL** (4+ weeks).

For tactical items and bugs, see `TODO.md`.
For current feature status, see `LANGUAGE_FEATURES.md`.

---

## Highest Internal Impact

Features that reshape compiler internals (change the model, not just add to it).
For comparison: move semantics changed how every variable is handled (3-tier model,
liveness analysis, gen_expr vs gen_expr_deref). Most features below are additive --
they don't change how existing code compiles. These two do:

1. **Narrowing generalization** (part of union types / isinstance) -- **Done.** The
   narrowing system tracks type sets ("which types from the union are still possible?")
   with set-based merge logic, invalidation rules, and loop-entry reset. Existing
   None-narrowing is a special case. Supports if/elif/else isinstance, assert isinstance,
   while-loop isinstance, and assignment narrowing.

2. **Generic Optional codegen fix** (A3) -- **Done.** Changed what C++ is emitted for
   `T | None` patterns when `T` is a type parameter. All Optional-related codegen paths
   now use `uses_pointer_repr()` instead of `is_value_type()`.

3. **Dynamic dispatch** (B4 + B5) -- method definition codegen goes from "always
   non-virtual" to "decide per method: virtual or not?" affecting every class hierarchy.
   Method call codegen needs a static-vs-vtable dispatch decision. Protocol conformance
   checking goes from "yes/no validation" to "generate conformance adapters" -- the
   protocol system starts producing codegen artifacts, not just type-checking results.

Everything else -- effect system, closures, generators, exceptions, Box, match/case,
dict, tuples -- is **additive**: important and sometimes touching many files, but the
existing compiler model stays the same for existing code.

---

## Implementation Roadmap

### Phase A: Quick Wins (unblock real programs)

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| A1 | Final constants | S | Not started | [I](#final--constant-globals) |
| A2 | Type aliases | S | Done | [I](#type-aliases) |
| A3 | Generic Optional codegen fix | M | Done | [I](#generic-optional-codegen-fix) |
| A4 | DynStr (owned strings) | M | Designed | [I](#string-ownership-dynstr) |
| A5 | Enums | M | Not started | [I](#enums) |
| A6 | Keyword args + default values | M | Not started | [VII](#keyword-arguments-and-default-values) |
| A7 | `# tpy:` directives | S-M | Not started | [I](#tpy-directives) |
| A8 | `__bool__` protocol | S | Done | [VII](#__bool__-protocol) |
| A9 | Constructor initializer list codegen | M | Known bug | [II](#constructor-initializer-list-codegen) |

### Phase B: Polymorphism Foundation

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| B1 | Box[T] heap ownership | M | Not started | [II](#boxt----heap-ownership) |
| B2 | Union types / ADTs | L | Partial | [I](#union-types--algebraic-data-types) |
| B3 | Match/case | M-L | Not started | [VI](#matchcase-with-pattern-matching) |
| B4 | Dynamic dispatch -- Dyn[P] | L | Designed | [II](#dynamic-dispatch-dynp) |
| B5 | Virtual methods in inheritance | M | Not started | [II](#virtual-methods-in-inheritance) |

### Phase C: Error Handling + Effects

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| C1 | Exception model (design) | M | Not started | [III](#exception-model-tryexceptraise) |
| C2 | Exception implementation | L | Not started | [III](#exception-model-tryexceptraise) |
| C3 | @noalloc enforcement | L | Parsed only | [IV](#noalloc-enforcement) |
| C4 | Effect framework (@nothrow, @pure) | M-L | Not started | [IV](#effect-system-generalized) |
| C5 | String literal types (Literal[...]) | M | Not started | [III](#string-literal-types) |

### Phase D: Functional + Python Compat

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| D1 | Closures / nested functions | L | Not started | [VI](#closures--nested-functions) |
| D2 | Callable type | L | Not started | [I](#callable--function-pointer-types) |
| D3 | f-strings | M | Not started | [VII](#f-strings) |
| D4 | List comprehensions | M | Not started | [VI](#list-comprehensions) |
| D5 | Dataclasses | M | Not started | [VII](#dataclasses) |
| D6 | dict type | L | Not started | [VII](#dict-type) |
| D7 | Tuple type + unpacking | M-L | Not started | [I](#tuple-type) |
| D8 | Function overloads (@overload) | M | Infra exists | [VII](#function-overloads-overload) |
| D9 | with statement | M | Not started | [VI](#with-statement-context-managers) |
| D10 | Lambda | M | Not started | [VI](#lambda) |
| D11 | List slicing | M | Not started | [VII](#list-slicing) |
| D12 | Properties (@property) | M | Not started | [VII](#properties) |

### Phase E: Advanced Safety

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| E1 | Send/Sync markers | S-M | Not started | [IV](#thread-safety-markers-send--sync) |
| E2 | Container mutation during iteration | S-M | Not started | [IV](#container-mutation-during-iteration) |
| E3 | Override hiding warnings | S | Not started | [II](#virtual-methods-in-inheritance) |
| E4 | del statement | S-M | Not started | [VI](#del-statement-explicit-destruction) |
| E5 | Ptr escape analysis | XL | Partial | [IV](#ptrt-escape-analysis--lifetime-tracking) |
| E6 | Auto-detect readonly | M-L | Not started | [IV](#auto-detect-readonly-from-method-body) |
| E7 | Dead code detection | M | Not started | [VIII](#dead-code-detection) |
| E8 | Error recovery / multi-error diagnostics | L | Not started | [VIII](#error-recovery--multi-error-diagnostics) |
| E9a | Drop flag (`__tpy_owned_`) | S | Done | [IV](#move-safe-destructors) |
| E9b | Sentinel field optimization (eliminate drop flag) | M | Not started | [IV](#move-safe-destructors) |

### Phase F: Compile-Time Power

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| F1 | Compile-time evaluation | XL | Not started | [V](#compile-time-evaluation-constexpr--comptime) |
| F2 | Macro system | XL | Designed | [V](#macro-system--metaprogramming) |
| F3 | Generators / yield | L-XL | Not started | [VI](#generators-yield) |
| F4 | Typestate | XL | Research | [VIII](#typestate-object-lifecycle) |
| F5 | Self-interpret (TPy eval in tpyc) | XL | Not started | [V](#self-interpret-tpy-eval-in-tpyc) |
| F6 | Cyclic dependency handling | L | Not started | [V](#cyclic-dependency-handling) |
| F7 | Alternative backends | XL | Not started | [V](#alternative-backends) |

### Phase G: Concurrency (Future)

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| G1 | async/await or alternative | XL | Not started | [IX](#asyncawait-or-alternative-model) |
| G2 | Channels | L | Not started | [IX](#channels) |

Phases are not strictly sequential -- items from different phases can be interleaved
based on what's most needed. E1 (Send/Sync markers) is recommended early regardless
of phase, to avoid costly retrofitting when concurrency arrives.

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

**Current state**: Not started.

**Dependencies**: None for basic tuple. Destructuring needs sema support for multi-target
assignment. Dict iteration needs both tuple and dict.

**Effort**: M-L (type + codegen + destructuring)

---

### Generic Optional Codegen Fix

**Done.** `T | None` in generic contexts now generates `std::optional<T>` instead of
`T*`/`nullptr`. Concrete non-value types (records, lists) still use pointer representation.

Added `OptionalType.uses_pointer_repr()` method that returns `True` only for concrete
non-value inner types (no `TypeParamRef` anywhere in the type tree). All 33 codegen sites
updated to use this instead of `is_value_type()`. Also: `Optional[T]` from `typing` is
now equivalent to `T | None` at parse time.

**Dependencies**: Interacts with union types (Optional is a degenerate union).

---

### String Ownership (DynStr)

Owned string type: `DynStr` -> `std::string`.

**Why it matters**: Current `str` (`std::string_view`) creates dangling references when
storing `str(numeric)` in a variable. Any program that builds strings dynamically is broken.
This is the most user-visible limitation.

Design (STRING_HANDLING.md): `str` = view (non-owning), `DynStr` = `std::string` (owning).
Implicit widening `str -> DynStr` allowed in normal code, blocked in `@noalloc`.
Concatenation returns `DynStr`. Both are value types (copy silently).

**Current state**: Design doc exists, not implemented.

**Dependencies**: Interacts with `@noalloc` (widening blocked), reassignment inference
(infer `DynStr` when any assignment produces owned string), f-strings (produce `DynStr`).

**Effort**: M (new type, coercion rules, codegen)

---

### Enums

```python
class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2
```

Maps to `enum class Color : int32_t` in C++.

**Why it matters**: Enums are a prerequisite for match/case (simplest case), union types
(enums are the tag), and many real-world patterns (state machines, options, flags).
Int-based enums first; string enums and flag enums later.

**Current state**: Not started.

**Dependencies**: Stepping stone to union types and match/case.

**Effort**: M

---

### Type Aliases

```python
Shape = Circle | Rect                  # old-style assignment
type Shape = Circle | Rect             # Python 3.12 type statement
MaybeShape = Circle | Rect | None      # with None member
```

Maps to C++ `using Shape = std::variant<Circle, Rect>;`. Both syntaxes supported.
Cross-module import works (`from shapes import Shape`), including alias-only import
(member records are implicitly imported for codegen). Nested annotations (`list[Shape]`)
resolve correctly.

**Current state**: Done.

**Not yet supported**: `isinstance(x, Shape)` where Shape is a type alias;
`isinstance(x, (A, B))` tuple form; pattern matching on variants.

**Dependencies**: Needed for union types to be usable.

**Effort**: S

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

```python
MAX_SIZE: Final = Int32(1024)     # -> constexpr int32_t MAX_SIZE = 1024;
PI: Final = 3.14159               # -> constexpr double PI = 3.14159;
```

`Final` in declaration context means full immutability (compile-time constant).
Semantically equivalent to `readonly` but for declarations rather than type positions.

**Current state**: Not started. Infrastructure exists (`@readonly`).

**Dependencies**: Stepping stone to compile-time evaluation.

**Effort**: S (extends existing readonly infrastructure)

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

## II. Polymorphism & Dispatch

### Box[T] -- Heap Ownership

```python
from tpy import Box

b: Box[Node] = Box(Node(42))
print(b.value)   # auto-deref through Deref[T] protocol
```

`Box[T]` wraps `std::unique_ptr<T>`. `@nocopy` (move-only) + `Deref[T]` (auto-deref).

**Why it matters**: Gates dynamic dispatch (`Box[Dyn[P]]`), `Rc[T]`, and any pattern
involving heap-allocated objects with clear ownership. Currently there is no way to
own a heap object in TPy.

Phase 6 of MOVE_SEMANTICS_DESIGN. Well-specified, implementation-ready.

**Current state**: Not started. `@nocopy` and `Deref[T]` protocol both exist.

**Dependencies**: Prerequisites done (`@nocopy`, `Deref[T]`, auto-move). Gates `Dyn[P]`.

**Effort**: M

---

### Dynamic Dispatch (Dyn[P])

```python
class Drawable(Protocol):
    def draw(self) -> None: ...

shapes: list[Box[Dyn[Drawable]]] = [Box(Circle()), Box(Square())]
for s in shapes:
    s.draw()   # runtime dispatch via vtable
```

Phase 9 of PROTOCOL_DESIGN. Requires vtable adapter generation, object-safety validation,
and `Box[Dyn[P]]` as the type-erased container.

**Why it matters**: Without this, heterogeneous collections are impossible. The language is
limited to "C with better syntax" for OOP patterns. Combined with ADTs, this gives TPy
two complementary polymorphism stories:
- ADTs: closed hierarchies, stack-allocated, exhaustive match, `@noalloc`
- `Dyn[P]`: open extension, heap-allocated, vtable, dynamic

This also enables **protocol-typed local variables**: `seq: Sequence[Int32] = items`
where the concrete type is erased behind the protocol. Currently protocol types can only
appear in generic bounds (`T: Sequence`), not as variable types.

**Current state**: Fully designed (PROTOCOL_DESIGN.md Phase 9), nothing built.
All current protocols compile to C++20 concepts (static dispatch only).

**Dependencies**: Requires `Box[T]`. Protocol system (done) provides the foundation.

**Effort**: L (vtable generation, adapter structs, object-safety validation)

---

### Virtual Methods in Inheritance

```python
class Animal:
    def speak(self) -> str: return "..."

class Dog(Animal):
    def speak(self) -> str: return "woof"

a: Ptr[Animal] = Ptr(Dog())
a.speak()   # currently calls Animal.speak (static dispatch)
             # should call Dog.speak (dynamic dispatch)
```

**Why it matters**: Python uses dynamic dispatch by default. TPy currently uses static
dispatch (C++ default). This means method override through base pointers is silently wrong.
At minimum, a warning when child hides parent method without `virtual` is needed.

Also includes **implicit upcasting** (`parent: Animal = Dog()`) and **polymorphic
coercion** (`Dog -> Ptr[Animal]`). Currently assigning a child to a parent type or
pointer is not supported -- these coercions are needed for inheritance to be useful.

**Current state**: Single inheritance works. No `virtual`, no `override`, no vtable.
No upcasting coercion. Registration.py has comments noting the dispatch gap.

**Dependencies**: Related to `Dyn[P]` but simpler (class-based vs protocol-based).
Could implement as: explicit `@virtual` decorator, or auto-detect when override exists.

**Effort**: M (codegen for virtual/override, vtable layout)

---

### Constructor Initializer List Codegen

When `__init__` has only simple `self.field = param` assignments, the codegen emits
C++ member initializer lists (`: x(x), y(y)`). But when `__init__` contains any
control flow (if/else, loops), it falls back to body assignments:

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
- **Non-default-constructible types**: field has no `T()` -- C++ won't compile
- **`@nocopy` (move-only) types**: default-construct + assign requires copy assignment
- **`const` fields** (future): can only be set in initializer list
- **Reference fields** (future): must be bound at construction

**Why it matters**: As more complex types become fields (e.g. `Box[T]`, `@nocopy`
records, `Own[T]`), constructors with any branching will fail to compile in C++.
This is a correctness bug that will surface more frequently as the type system grows.

Design options:
- **(a) Deferred initialization**: wrap fields in `std::optional<T>` when init is
  conditional, unwrap on first use. Overhead: extra byte per field + has_value checks.
- **(b) Factored initializer**: analyze which fields are assigned on all paths and
  extract them to the initializer list; only truly conditional fields use body
  assignment. Requires data-flow analysis of `__init__`.
- **(c) Lambda-in-initializer**: `: field([&]{ if (...) return x; else return y; }())`
  -- keeps initializer list, moves branching into per-field lambdas. Works for all
  types but generates unusual C++.

**Current state**: Known bug. Works for trivial types only.

**Dependencies**: Interacts with `@nocopy`, `Box[T]`, and any non-trivially-constructible
field types.

**Effort**: M (data-flow analysis of `__init__` + codegen restructuring)

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

Operations that allocate: `list.append`, `str` concatenation -> `DynStr`, `BigInt`
arithmetic, `Box::new`, `std::string` construction, container growth.

**Current state**: `is_noalloc` flag propagated in sema, zero enforcement code.

**Dependencies**: Needs `DynStr` (to know what string ops allocate), exception model
(exceptions allocate), `Box[T]` (heap allocation).

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
invalidations). Listed as a Hard Problem in TODO.md.

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
| `Ptr[T]`, `ConstPtr[T]` | Need explicit `other.p = nullptr` | Yes, with codegen |
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

**Current state**: Not started. `Final` not yet implemented.

**Dependencies**: `Final` constants (stepping stone). `@pure` effect (determines eligibility).

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

**Current state**: Not started. Listed in Hard Problems (TODO.md).

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

**Current state**: Not started. Mentioned in TODO.md as investigation item.

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

**Dependencies**: Iterator protocol (done). Tuple unpacking (for `a, b = ...` pattern).

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

```python
name = "world"
msg = f"hello {name}, 1+1={1+1}"
```

Maps to `std::format` (C++20) or `fmt::format`.

**Current state**: Not started.

**Dependencies**: `DynStr` (f-strings produce owned strings).

**Effort**: M (parser + expression codegen inside format specs)

---

### Dataclasses

```python
@dataclass
class Point:
    x: Int32
    y: Int32
    # auto-generates __init__, __eq__, __repr__
```

**Current state**: Not started. TPy classes have partial overlap (fields from annotations).

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

**Current state**: Not started.

**Dependencies**: None.

**Effort**: M (parser already has AST support via Python's ast module; sema + codegen needed)

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

### Function Overloads (@overload)

```python
@overload
def process(x: Int32) -> Int32:
    return x * 2

@overload
def process(x: str) -> str:
    return x + x
```

Maps directly to C++ function overloads.

**Why it matters**: Sema infrastructure for overload resolution already exists (used by
builtins). User-facing `@overload` wiring is the remaining work. Each overload body is
the real implementation (unlike CPython stubs).

**Current state**: Infrastructure exists. User-facing wiring needed.

**Dependencies**: None.

**Effort**: M

---

### `__bool__` Protocol

```python
class Container:
    items: list[Int32]
    def __bool__(self) -> bool:
        return len(self.items) > 0

c = Container([])
if c:            # calls c.__bool__()
    print("has items")
```

**Why it matters**: Python uses `__bool__` for truthiness in `if`, `while`, `and`, `or`,
`not`. Without this, user-defined types can't participate in boolean contexts. Currently
`if obj:` for a user type either fails or has unexpected C++ behavior.

Also needed: `bool()` builtin dispatching to `__bool__`, and a `Truthy` protocol bound.

**Current state**: Done. `__bool__()` structural detection, `bool()` dispatch, implicit truthiness
(`if`/`while`/`not`/`and`/`or`), `__len__() != 0` fallback, and `Truthy` protocol bound all working.

**Dependencies**: Protocol system (done).

**Effort**: S (protocol definition + sema truthiness check + codegen)

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

## VIII. Compile-Time Safety (Python Runtime Checks)

Things Python checks at runtime that TPy can verify statically.

### Already Done

| Python Runtime Check | TPy Status |
|---------------------|------------|
| `TypeError: unsupported operand type(s)` | Done (sema type checking) |
| `AttributeError: has no attribute` | Done (record field checking) |
| `TypeError: takes N positional arguments` | Done (call checking) |
| `UnboundLocalError: referenced before assignment` | Done (init_tracker) |
| Bounds checks (`IndexError`) | Runtime (`tpy::get_item`) |

### Could Do

| Python Runtime Check | TPy Opportunity |
|---------------------|-----------------|
| `RuntimeError: dict changed size during iteration` | Container mutation check (see IV) |
| `IndexError: list index out of range` (some cases) | Static range analysis for `range(len(x))` patterns |
| `OverflowError` for fixed-width ints | Compile-time range analysis for literal arithmetic |
| `RecursionError` | Detect unbounded recursion in simple cases |
| `TypeError: unhashable type` | Compile-time `Hashable` protocol check for dict keys |
| Use after `.close()` / resource lifecycle | Typestate analysis (see below) |
| Missing match cases | Exhaustive pattern matching (see VI) |
| Method override correctness | Static vs dynamic dispatch warning (see II) |

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
file.tp.py:5: error: Unknown type 'Foo'
file.tp.py:12: error: No matching overload for 'bar(Int32)'
file.tp.py:18: warning: Unused variable 'x'
3 errors, 1 warning
```

**Why it matters**: Stopping at the first error forces users into a fix-one-recompile loop.
Reporting all errors at once is much more productive, especially for large files.

Implementation approach: introduce an `Invalid` type that propagates through analysis
without triggering cascading errors. Expressions involving `Invalid` silently produce
`Invalid` rather than new error messages.

**Current state**: Not started. Mentioned in TODO.md.

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

**Current state**: Entirely deferred. Mentioned in TODO.md as investigation items.

---

## X. Self-Hosting Prerequisites

Features needed for the compiler to compile itself, roughly by priority:

| Feature | Compiler Usage | Section |
|---------|---------------|---------|
| dict | 24 files | VII |
| tuple + unpacking | 44 files | I |
| f-strings | 32 files | VII |
| enum | 17 files | I |
| exceptions (try/except) | 9 try/except blocks | III |
| default arguments | pervasive | VII |
| keyword arguments | pervasive | VII |
| list comprehensions | common | VI |
| isinstance + narrowing | 824 calls, 35 files | I (ADTs replace) |
| closures/lambda | moderate | VI |
| string methods | pervasive | I (DynStr) |
| `with` statement | moderate | VI |
| walrus operator | if useful | - |
| own parser | replaces Python `ast` | - |

See `SELF_HOSTING.md` for the full gap analysis.

