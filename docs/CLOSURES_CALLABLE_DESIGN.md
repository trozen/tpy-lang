# Closures, Callable Types, and Generators

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 1a | `Fn` type + lambda expressions (including captures) | Done |
| 1b | `Callable` type (`std::function`, type-erased callable) | Done |
| 2 | Named function references as values (`apply(double, 42)`) | Done |
| 3 | Nested `def` with captures, `nonlocal` keyword | Done |
| 4 | Generator functions (`yield`) | Done |
| 5 | `@noalloc` enforcement, `FnOnce` semantics for `Own[T]` captures | Not started |

### Future Extensions

| Feature | Notes |
|---------|-------|
| `yield from` / delegating generators | Forward to sub-iterator; useful for recursive generators (tree traversal) |
| `gen.send(value)` / `gen.throw(exc)` | Two-way generator communication; rarely used outside async frameworks |
| Generator liveness optimization | Only promote yield-crossing variables to struct fields; keep others as stack locals in `__next__()`. Reduces struct size, no behavior change. |
| ~~Generator yield in for-loops (state machine)~~ | **Done** -- for-loops with yields lowered to while-loops; iterator state (counters for range, begin/end for containers, `std::expected` for `__next__()`) hoisted to struct fields. |
| `async`/`await` | Reuses state machine infrastructure from generators |
| Recursive closures | Closure calling itself -- needs `std::function` self-reference. Currently rejected with dedicated diagnostic ("cannot call itself"). |
| Nested-in-nested `def` | `def` inside `def` inside `def`. Currently rejected. Requires saving/restoring more sema state in `nested_def_scope`. |
| ~~Escaping `str` param capture~~ | **By design** -- rejected with error (string_view dangles). Workaround: use `String` param or `name_copy = String(name)` local. Silent copy would violate no-implicit-copy principle. |
| ~~Escape detection for field/container storage~~ | **Done** -- `self.field = nested_func`, `container.append(nested_func)`, and `return` from methods all trigger escape detection. Method escape finalization added; `OwnType` unwrapping for container element hints. |
| Escaping `nonlocal` via `Rc[T]` | `Rc[T]` (`std::shared_ptr<T>`) would allow mutable shared state between closure and enclosing scope, enabling `nonlocal` in escaping closures. |
| Variadic `Callable` | `Callable[..., R]` accepting any args -- needs `*args` (D17) |
| Method references | `obj.method` as a value -- partial application binding `self` |
| `Fn \| None` (optional zero-cost) | Template-based optional callable via `Optional[Protocol]` pattern (`std::nullptr_t` default). Currently an error -- use `Callable \| None` instead. |
| `Fn` as local variable annotation | `f: Fn[[int32], int32] = lambda x: x + 1` -- use `Fn` as type context for a named lambda, codegen as `auto` (zero-cost). Only valid when not reassigned (reassignment would need `std::function`). Currently an error. |

---

## Overview

Closures and callable types are a foundational feature for Python compatibility and for
unlocking functional patterns (callbacks, higher-order functions, `sorted(key=...)`,
event handlers). This design also covers generators (`yield`), because a generator is
structurally a resumable closure -- both capture local state into a struct, differing
only in that closures have one entry point while generators have N entry points (one
per yield).

### Design Philosophy

TPy uses **two explicit callable types** with different cost models, inspired by
Rust's `impl Fn` vs `dyn Fn` distinction:

- **`Fn[[A, B], R]`** -- zero-cost, template-based. Valid only in parameter position.
  Monomorphized at each call site. Use when performance matters.
- **`Callable[[A, B], R]`** -- type-erased, `std::function`. Works everywhere
  (params, fields, returns, containers). Flexible, pays for type erasure.

The cost model is **visible in the source** -- no hidden context-dependent behavior.
Most users start with `Callable` (it always works), and switch to `Fn` when they
care about performance. `@noalloc` requires `Fn`.

The compiler **auto-infers** captures (no capture lists needed, like Rust).

### Inspiration from Other Languages

| Language | Key idea borrowed |
|----------|------------------|
| **Rust** | `impl Fn` vs `dyn Fn` split (TPy's `Fn` vs `Callable`); auto-inferred capture mode per variable; `Fn`/`FnMut`/`FnOnce` hierarchy maps to TPy's ownership model |
| **C++** | Templates for zero-cost callable params; `std::function` for type erasure; `std::move_only_function` (C++23) for move-only captures |
| **Swift** | Non-escaping by default for params; escaping requires storage/return context; no annotation needed (inferred) |
| **D** | `@nogc` as precedent for `@noalloc` enforcement with closures; `scope` delegates for non-escaping |

---

## I. Two Callable Types: `Fn` and `Callable`

TPy provides two callable types with **explicit, different cost models**:

| Type | C++ representation | Valid positions | Cost |
|------|-------------------|-----------------|------|
| `Fn[[A, B], R]` | Template parameter + concept | Params only | Zero -- monomorphized, inlined |
| `Callable[[A, B], R]` | `std::function<R(A, B)>` | Everywhere | Type-erased, possible heap alloc (SBO) |

This is analogous to Rust's `impl Fn(A, B) -> R` (static dispatch, monomorphized)
vs `Box<dyn Fn(A, B) -> R>` (dynamic dispatch, heap-allocated).

A callable contract says nothing about mutation, so calls through callable
values are treated as opaque, potentially-mutating callees: non-value param
types spell MUTABLE in the C++ signature (`std::function<void(std::vector<
int32_t>&)>`), args run the normal coercion pipeline, and reference args
are conservatively marked mutated for const inference and borrow warnings.
The explicit non-mutating contract is `readonly[...]` inside the param list
(`Callable[[readonly[list[int32]]], None]` keeps `const&` and exempts the
call from the conservative marks). Signature compatibility is contravariant
in params, covariant in returns; bare generic slots (`Fn[[T], R]`) are
exempt from the conservative mutation marks so generic combinators keep
const container params.

### Type System

One type in `typesys.py` with a mode flag (`is_template`) distinguishing
Fn (template) from Callable (type-erased):

```python
@dataclass(frozen=True)
class CallableType(TpyType):
    """Fn / Callable -- param_types, return_type, plus is_template.

    is_template=True: Fn[[...], R] -- zero-cost, template-param rendering.
        Valid only in function parameter position.
    is_template=False: Callable[[...], R] -- type-erased (std::function).
        Valid in all positions: params, fields, returns, containers, locals.
    """
    param_types: tuple[TpyType, ...]
    return_type: TpyType
    is_template: bool = False
```

(Originally split into `FnType` and `CallableType` subclasses; merged into
a single `CallableType` with a flag, since the two shared all structural
behavior and differed only in rendering.)

### `Fn` -- Zero-Cost Callable (Template)

```python
from tpy import Fn

def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)

apply(lambda x: x + 1, 42)        # lambda inlined
apply(double, 42)                   # function pointer, inlined
```

Generated C++:

```cpp
template<typename __F0>
int32_t apply(__F0&& f, int32_t x)
    requires requires(__F0& __fn, int32_t __a0) {
        { __fn(__a0) } -> std::convertible_to<int32_t>;
    }
{
    return f(x);
}
```

Each call site with a different callable monomorphizes a separate instantiation.
The compiler inlines through the callable -- zero overhead.

**Restrictions**: `Fn` is only valid in function/method parameter position. Using
`Fn` as a field type, return type, container element, or local variable annotation
is a compile error:

```python
class Bad:
    handler: Fn[[int32], None]  # ERROR: Fn is only valid in parameter position

def bad() -> Fn[[int32], int32]:  # ERROR: cannot return Fn (use Callable)
    ...
```

The error message suggests `Callable` as the alternative.

**Multiple `Fn` params**: Each `Fn` parameter gets its own template parameter
(`__F0`, `__F1`, etc.), even when two params have the same `Fn` signature. This
allows passing different concrete callables to the same function.

**Return type constraint**: The `requires` clause uses `std::convertible_to`
(not `std::same_as`) to allow numeric coercions (e.g., a callable returning
`int32` satisfies `Fn[[...], int]`).

### `Callable` -- Type-Erased Callable (`std::function`)

```python
from typing import Callable

# Field -- must be type-erased
class Button:
    on_click: Callable[[int32], None]

# Return type -- must be type-erased
def make_adder(n: int32) -> Callable[[int32], int32]:
    return lambda x: x + n

# Parameter -- works but pays type-erasure cost
def register(cb: Callable[[int32], None]) -> None:
    self.on_click = cb
```

Generated C++ for field:

```cpp
struct Button {
    std::function<void(int32_t)> on_click;
};
```

`Callable` uses `std::function` which provides small-buffer optimization (SBO) --
closures smaller than ~16-32 bytes (implementation-defined) avoid heap allocation.
Larger closures heap-allocate. There is an indirect call overhead on every
invocation (vtable-style dispatch).

`Callable` works everywhere: fields, return types, local variables, container
elements, function parameters. When used as a parameter, it's less efficient
than `Fn` but necessary when the callable must be stored:

```python
def register(cb: Callable[[int32], None]) -> None:
    self.on_click = cb  # cb is stored -- must be std::function already
```

#### `std::move_only_function` for Move-Only Captures

When a `Callable` type's parameter or return types include move-only types
(`Own[T]`, `@nocopy` records), the compiler uses C++23
`std::move_only_function` instead of `std::function`:

```python
class Pipeline:
    transform: Callable[[Own[Data]], Own[Data]]
```

```cpp
struct Pipeline {
    std::move_only_function<Data(Data)> transform;
};
```

### `Callable | None` -- Optional Callbacks

```python
class EventEmitter:
    on_error: Callable[[str], None] | None = None

    def set_error_callback(self, cb: Callable[[str], None]) -> None:
        self.on_error = cb

    def emit_error(self, msg: str) -> None:
        if self.on_error is not None:
            self.on_error(msg)
```

Maps to `std::optional<std::function<void(std::string_view)>>`. This always
uses type erasure (even in param position) since templates can't represent
"optional callable." The `| None` makes the cost explicit.

A lambda literal, a function by name, or `None` may be passed directly to a
`Callable[...] | None` *parameter* (`def f(cb: Callable[[int32], None] | None
= None)`): the optional wrapper is peeled to recover the callable shape for
arg inference, then the value coerces back into the optional slot. (A
`Send[Callable[...]] | None` param does not yet accept a lambda/name -- the
marker sits inside the Optional and narrowing/the call path would also need
to peel it; see TODO.)

### When to Use Which

| Use case | Type | Why |
|----------|------|-----|
| Callback param (hot path) | `Fn` | Zero-cost, inlined |
| `sorted(key=...)`, `map`, `filter` | `Fn` | Performance-critical iteration |
| Class field (event handler) | `Callable` | Must store, needs type erasure |
| Return from factory function | `Callable` | Must outlive creator |
| Container (`list[...]`) | `Callable` | Must be uniform type |
| Optional callback (`... \| None`) | `Callable` | Templates can't represent optional |
| `@noalloc` context | `Fn` | `Callable` forbidden (may heap-allocate) |
| Don't care about perf | `Callable` | Works everywhere, simplest |

### Protocol Integration

Both `Fn` and `Callable` accept any value with a matching call signature. This
includes lambdas, named functions, and objects with `__call__`:

```python
class Doubler:
    def __call__(self, x: int32) -> int32:
        return x * 2

def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)

apply(Doubler(), 5)  # works -- Doubler has matching __call__
```

In C++, the concept constraint (`std::invocable` / custom `requires`) already
accepts any callable object, including structs with `operator()`.

**Status**: Fully implemented. Direct `obj(args)` invocation, `@readonly`,
mutable state, recursive `self(args)`, and passing callable objects to
`Fn`/`Callable` parameters all work. Sema validates `__call__` signature
compatibility with Fn/Callable hints at compile time.

### Implicit `Fn` -> `Callable` Coercion

A value accepted as `Fn` can be passed to a `Callable` parameter or stored in
a `Callable` field (wrapping in `std::function`). The reverse is not possible --
`Callable` cannot become `Fn` (the concrete type is erased).

```python
def process(f: Fn[[int32], int32], x: int32) -> None:
    store_callback(f)  # OK: Fn coerces to Callable (wraps in std::function)

def store_callback(cb: Callable[[int32], None]) -> None:
    self.handler = cb
```

---

## II. Lambda Expressions

### Syntax

Standard Python lambda syntax:

```python
items.sort(key=lambda x: x.priority)
result = apply(lambda x: x * 2, value)
squares = list(map(lambda x: x * x, items))
```

Lambdas are single-expression functions. They cannot contain statements, loops,
or assignments (Python restriction, not TPy-specific).

### Type Inference

Lambda parameter types are inferred from context (bidirectional inference).
Both `Fn` and `Callable` parameter types provide context (including a
`Callable[...] | None` param -- the Optional is peeled to recover the shape):

```python
def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)

apply(lambda x: x + 1, 42)
# x is inferred as int32 from the Fn[[int32], int32] parameter type

handler: Callable[[str], None] = lambda s: print(s)
# s is inferred as str from the Callable[[str], None] annotation
```

When context is insufficient, the lambda is rejected with a diagnostic:
"cannot infer parameter types for lambda -- annotate the target or use a named
function."

### C++ Code Generation

Lambdas generate C++ lambda expressions:

```python
apply(lambda x: x + 1, 42)
```

```cpp
apply([](int32_t x) { return ::tpy::add_check(x, int32_t(1)); }, int32_t(42));
```

Non-capturing lambdas generate simple `[]` captures. Capturing lambdas are
discussed in Section III.

### Lambda Type

Each lambda has a unique anonymous type (like C++ lambdas). In the type system,
it satisfies both Fn and Callable via structural compatibility but
has no user-visible type name. For local variables:

```python
f = lambda x: x + 1  # type inferred from usage context
f(42)
```

The variable `f` uses `auto` in C++ (concrete lambda type preserved). If `f` is
reassigned to a different lambda, it becomes `std::function` (the variable is
implicitly `Callable`).

---

## III. Closures (Capturing Lambdas and Nested Functions)

### Capture Analysis

Since Python has no capture lists, the compiler auto-infers captures by analyzing
free variables in the closure body (variables referenced but not defined locally).
This is the same approach as Rust.

```python
def make_scaler(factor: int32) -> Callable[[int32], int32]:
    return lambda x: x * factor  # captures 'factor'
```

The compiler identifies `factor` as a free variable and generates a capturing
C++ lambda:

```cpp
std::function<int32_t(int32_t)> make_scaler(int32_t factor) {
    return [factor](int32_t x) { return ::tpy::mul_check(x, factor); };
}
```

### Capture Mode per Type

The capture mode depends on the variable's type and whether the closure escapes:

| Captured type | Non-escaping | Escaping |
|--------------|-------------|---------|
| Value types (`int32`, `bool`, `float`, `char`) | by copy `[v]` | by copy `[v]` |
| `str` (local `std::string`) | by ref `[&s]` | by copy `[s]` |
| `StrView` (`std::string_view`) | by copy `[sv]` | by copy `[sv]` -- but may dangle! |
| `Ptr[T]` | by copy `[p]` | by copy `[p]` -- lifetime must be valid |
| `Own[T]` / `@nocopy` types | by ref `[&x]` | by move `[x=std::move(x)]` |
| Containers (`list[T]`, `dict[K,V]`) | by ref `[&c]` | by move or by copy |
| Records (value types) | by copy `[r]` | by copy `[r]` |
| Records (reference types) | by ref `[&r]` | by copy `[r]` (deep copy) |

**Inside a generator or `async def`**, the enclosing body's params and locals
are MEMBERS of the resumable frame, and a member has no variable form to name
in a capture list. The entry names it in a C++ init-capture instead -- but the
MODE is the one the table above gives, so the same source gets the same binding
whether or not the enclosing body suspends:

| Frame member | Non-escaping (`Fn`) | Escaping (`Callable`) |
|--------------|---------------------|-----------------------|
| any plain member `n` | `[&n = n]` (a reference to the member) | `[n = n]` (by-value snapshot) |
| the receiver `self` | `[&__self = __self]` | `[&__self = __self]` |

The escaping column is what lets such a lambda be handed to a callee that
stores it: the closure owns its captures and is safe to outlive the frame. The
non-escaping column is what keeps a borrowed reference-type param usable --
`[&xs = xs]` binds the caller's object, so a mutation through the closure is
visible to the caller exactly as it is in the sync twin, where a blanket
snapshot would silently copy the container. Capturing the frame (`[this]`)
would be neither: a stored closure would dangle once the generator's frame
goes, and copying a started generator would leave the copy's closure pointing
at the original frame. (Yielding a `Callable`, or returning one from an
`async def`, still rejects -- but on the yield/return SLOT TYPE, which a
non-capturing lambda hits too, not on what the closure captured.) The receiver
is a handle in both modes -- the frame holds `self` as a reference member, and
the lambda copies that reference, the same aliasing a sync method's `this`
capture gives.

A capture C++ cannot copy has no entry at the ESCAPING mode, and rejects at the
lambda in BOTH lanes -- a `@nocopy` type, or a record with `__del__` (whose copy
constructor C++ deletes), with `Own[T]` peeled to its payload before that
verdict and the `__copy__` escape hatch honored. The by-value entry would be an
ill-formed copy either way, so it is diagnosed here rather than handed to the
toolchain. (The escaping `by move` row of the table above is what a nested `def`
does; a lambda does not move-capture yet, and even where it did, an
`std::function` slot demands a copy-constructible closure -- see the
`std::move_only_function` item in TODO.md.)

One further frame member has no entry: one whose READ spelling is not the bare
name. A reference-type frame LOCAL lives in a `tpy::frame_slot<T>` and reads
`(*ys)`, so no single entry serves both the initializer and the body.

Because the escaping capture is by value, the stale-value-capture warning below
is correct inside a frame too: a captured frame local reassigned after the
capture point warns, and TPy reads the snapshot where CPython's cell reads
the later value.

**Rationale**:
- Value types are cheap to copy, so always capture by copy (eliminates lifetime concerns)
- Non-escaping closures can safely capture by reference (lifetime bounded by the call)
- Escaping closures must own their captures (copy or move) because the closure
  outlives the creating scope
- `Own[T]` / `@nocopy` types can only be moved into escaping closures -- the
  original variable is invalidated (enforced by sema, like existing auto-move)

### Escaping Closure: Move Capture and Variable Invalidation

When an escaping closure captures a move-only type, the original variable becomes
unusable after the closure creation point:

```python
def make_processor(data: Own[Buffer]) -> Callable[[], None]:
    return lambda: process(data)  # moves 'data' into the closure
    # 'data' is no longer usable here (but this is after return, so fine)

def bad_example(data: Own[Buffer]) -> Callable[[], None]:
    f = lambda: process(data)   # moves 'data' into f
    use(data)                   # ERROR: 'data' was moved into closure 'f'
    return f
```

This reuses the existing last-use / auto-move infrastructure in sema. The closure
creation point is treated as a "use" of each moved-captured variable, and subsequent
uses are flagged as use-after-move errors.

### Escaping Closure: Stale Value-Capture Warning

An escaping closure must *own* its captures (by value), so it freezes each captured
local's value at creation. CPython instead late-binds through a cell object and
observes later changes -- a divergence that only manifests when the captured local
is reassigned or mutated after the closure is created:

```python
def hold(f: Callable[[], int]) -> Callable[[], int]: return f

k = 10
f = hold(lambda: k)   # warning: escaping closure captures 'k' by value, reassigned later
k = 20
print(f())            # TPy: 10   CPython: 20
```

The compiler warns at the closure when a captured local is **reassigned** after the
capture point (`_warn_stale_value_captures` in `tpyc/sema/analyzer.py`, covering both
escaping lambdas and escaping nested defs). The acknowledgment is to capture a fresh,
non-reassigned local (`snap = k`, optionally via `copy()`), which eliminates the
divergence. Two sibling cases are not yet warned (both tracked in `BUGS.md`): **in-place
mutation** of a captured container/record (`BUGS.md#escaping-capture-mutation-snapshot`;
the mutation-after-capture fact is untracked -- `closure_written_names` sees only
`nonlocal`/`global` rebinds), and **loop-variable capture** (`for k in ...: append(lambda: k)`),
whose rebinding is the loop back-edge rather than a later statement. Both hold in a
resumable frame as well as a sync body.

### Dangling Reference Prevention

For escaping closures, the compiler must prevent capturing dangling references:

```python
def bad() -> Callable[[], None]:
    local = [1, 2, 3]
    return lambda: print(local)  # 'local' would dangle -- must copy or move
```

The escaping analysis triggers by-copy or by-move capture for `local`, so the
closure owns the data. For `Ptr[T]` and `StrView` captures in escaping closures,
the compiler emits a warning: "escaping closure captures a view type -- the
referenced data must outlive the closure."

### `@readonly` Propagation

Closures created inside `@readonly` functions propagate const to captures:

```python
@readonly
def compute(items: list[int32], f: Fn[[int32], int32]) -> int32:
    total: int32 = 0
    g = lambda x: f(x) + total  # captures 'total' (const) and 'f' (const)
    return g(items[0])
```

All captures from a `@readonly` context are treated as const. Reference captures
use `const&`, pointer captures use `const T*`.

---

## IV. Named Function References

Named functions can be used as values where `Fn` or `Callable` is expected:

```python
def double(x: int32) -> int32:
    return x * 2

def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)

result = apply(double, 42)  # zero-cost via Fn
```

### C++ Representation

For `Fn` params, the function name is passed directly -- C++ templates accept
plain function names and deduce their type:

```cpp
apply(double, int32_t(42));  // 'double' deduced as function pointer type
```

For `Callable` params or fields, the function is wrapped in `std::function`:

```cpp
std::function<int32_t(int32_t)> cb = double;  // wraps function pointer
```

**Generic function references** (implemented): When a generic function is used as a
value, the compiler infers type parameters from the `Fn`/`Callable` hint signature
using `match_type_with_inference`. Bounded type params are validated against protocol
conformance. The codegen emits explicit template instantiation:

```python
def identity[T](x: T) -> T:
    return x

apply(identity, 42)  # hint Fn[[int32], int32] -> infers T=int32
```

```cpp
apply(identity<int32_t>, int32_t(42));  // explicit template instantiation
```

**Limitation**: Generic function refs with `str` type args are rejected. The `str`
type uses `string_view` for params while generic functions use `param_val_or_ref_t<T>`
which resolves to `const string&`. These are incompatible C++ types and any bridge
would require a hidden heap allocation. Use a lambda instead:
`apply_str(lambda x: identity(x), "hello")`.

### Method References

Method references (`obj.method` as a value) are deferred. They require partial
application (binding `self`), which is a form of closure. Phase 4 would naturally
support this:

```python
class Formatter:
    def format(self, x: int32) -> str:
        return str(x)

fmt = Formatter()
apply(fmt.format, 42)  # captures 'fmt', binds 'self'
```

---

## V. Nested `def` with Captures (Phase 3)

### Syntax

```python
def make_adder(n: int32) -> Callable[[int32], int32]:
    def add(x: int32) -> int32:
        return x + n    # captures 'n' from enclosing scope
    return add
```

Nested `def` creates a closure -- a named function that captures variables from
the enclosing scope. Unlike lambda, it can contain statements, loops, and multiple
returns.

### `nonlocal` for Mutable Captures

Python's `nonlocal` keyword declares that a variable is from an enclosing scope
and should be mutable:

```python
def make_counter(start: int32) -> Callable[[], int32]:
    count = start
    def next_val() -> int32:
        nonlocal count
        count += 1
        return count
    return next_val
```

Without `nonlocal`, captured variables are read-only. With `nonlocal`, the
captured variable must be captured by mutable reference (non-escaping) or via
a shared mutable cell (escaping).

**Escaping mutable captures**: When a closure with `nonlocal` escapes, the
captured variable cannot be a simple by-ref capture (it would dangle). Options:

1. **Shared heap cell**: Wrap the variable in a `std::shared_ptr<T>` or similar.
   Both the enclosing scope and the closure reference the same heap-allocated cell.
   This is what Python does internally (cell objects).

2. **Reject**: Disallow `nonlocal` in escaping closures. The user must restructure
   (e.g., use a class with mutable state instead).

3. **Move + return**: Allow it only when the closure is the last use of the variable
   in the enclosing scope (common pattern: create closure, return it immediately).

Option 2 (reject) is the simplest and most consistent with TPy's ownership model.
Option 1 can be added later if there's demand. Option 3 handles the most common
pattern (factory functions) without heap allocation.

**Recommended approach**: Start with option 2 (reject escaping `nonlocal`) with
a clear diagnostic suggesting alternatives. Support non-escaping `nonlocal`
(captured by `&`, safe within the call scope).

### C++ Code Generation

Non-escaping nested function:

```python
def process(items: list[int32]) -> int32:
    total: int32 = 0
    def accumulate(x: int32) -> None:
        nonlocal total
        total += x
    for item in items:
        accumulate(item)
    return total
```

```cpp
int32_t process(std::vector<int32_t>& items) {
    int32_t total = 0;
    auto accumulate = [&total](int32_t x) { total += x; };
    for (auto& item : items) {
        accumulate(item);
    }
    return total;
}
```

Escaping nested function (read-only captures):

```python
def make_adder(n: int32) -> Callable[[int32], int32]:
    def add(x: int32) -> int32:
        return x + n
    return add
```

```cpp
std::function<int32_t(int32_t)> make_adder(int32_t n) {
    return [n](int32_t x) { return ::tpy::add_check(x, n); };
}
```

### Known Limitations (Phase 3)

- **No decorators or type parameters** on nested defs. Rejected at parse time.
- **No nested-in-nested**: `def` inside `def` inside `def` is rejected. Requires
  more thorough sema state isolation in `nested_def_scope`.
- **No recursive nested defs**: The name is bound after the `def` statement, so the
  body cannot reference itself. Gives "Unknown function" error (should be improved).
- **Escaping `str` parameter capture**: Escaping closures that capture a `str` or
  `StrView` parameter are rejected with an error (string_view would dangle after
  the enclosing function returns). Use `String` for owned capture.
- **Codegen context sharing**: The nested def body reuses the outer function's
  codegen context (pointer_locals, reassigned_vars, etc.) rather than having its
  own isolated context. Works for simple cases but may cause issues with
  pointer-slot variables captured by nested defs.

---

## VI. Generator Functions (`yield`) -- Phase 4

### Relationship to Closures

A generator function is structurally a **resumable closure**:

| | Closure | Generator |
|---|---------|-----------|
| Captures local state | Yes (struct fields) | Yes (struct fields) |
| Entry points | 1 (the call) | N (one per yield point) |
| Returns | Once | Multiple times (one per yield) |
| C++ representation | Lambda / functor | State machine struct |

Both capture local variables into a struct. The difference is that a closure
always enters at the top, while a generator resumes at the last yield point.

### Syntax

Standard Python generator syntax:

```python
def fibonacci() -> Iterator[int]:
    a, b = 0, 1
    while True:
        yield a
        a, b = b, a + b

def count_up(n: int32) -> Iterator[int32]:
    i: int32 = 0
    while i < n:
        yield i
        i += 1
```

A function containing `yield` is a generator function. Its return type annotation
is `Iterator[T]`. Calling it returns an iterator object; the body doesn't execute
until the first `__next__()` call.

### Implementation Strategy

**Recommended: manual state machine transformation (option b/c from research)**

The compiler transforms the generator body into a state machine struct + `__next__`
method. This reuses the `std::expected<T, StopIteration>` protocol that already
exists for `__next__()`.

**Rationale for manual state machine over C++20 coroutines:**

| Criterion | C++20 coroutines | Manual state machine |
|-----------|-----------------|---------------------|
| Heap allocation | Yes (default, HALO unreliable) | No (stack or inline) |
| `@noalloc` compatible | No | Yes |
| Codegen complexity | Low (emit `co_yield`) | High (state machine transform) |
| Debuggability | Poor (opaque frames) | Good (visible struct) |
| Protocol integration | Needs adapter for `std::expected` | Native `__next__()` |
| Performance | Good (amortized) | Best (no allocation) |

C++20 coroutines heap-allocate the coroutine frame by default, and HALO (Heap
Allocation eLision Optimization) is unreliable across compilers. Since TPy targets
performance-conscious users and has a planned `@noalloc` profile, the manual
approach gives full control over allocation.

**Future option**: A `# tpy: generator=coroutine` directive could opt into C++20
coroutines for specific modules where the simpler codegen is preferred and
allocation is acceptable.

### State Machine Transformation

Given:

```python
def count_up(n: int32) -> Iterator[int32]:
    i: int32 = 0
    while i < n:
        yield i
        i += 1
```

Generated C++:

```cpp
struct __gen_count_up {
    // State
    int __state = 0;
    // Parameters (captured at creation)
    int32_t n;
    // Locals live across yield points
    int32_t i;

    __gen_count_up& __iter__() { return *this; }

    std::expected<int32_t, ::tpy::StopIteration> __next__() {
        switch (__state) {
        case 0:
            i = 0;
            goto __check;
        case 1:
            i = ::tpy::add_check(i, int32_t(1));
            goto __check;
        }
    __check:
        if (i < n) {
            __state = 1;
            return i;
        }
        return std::unexpected(::tpy::StopIteration{});
    }

    friend std::ostream& operator<<(std::ostream& os, const __gen_count_up&) {
        return os << "<generator count_up>";
    }
};
```

The calling function returns the struct:

```cpp
__gen_count_up count_up(int32_t n) {
    return __gen_count_up{.n = n};
}
```

### Liveness Analysis for Generator Locals

Not all locals need to be in the struct. Only variables **live across a yield
point** are promoted to struct fields. Variables used only between consecutive
yields can remain stack-local within the `__next__()` method:

```python
def gen() -> Iterator[int32]:
    for i in range(10):
        temp = i * i      # 'temp' is NOT live across yield
        yield temp + 1    # only 'i' is live across yield
```

The compiler's existing liveness analysis (`liveness.py`) can be extended to
identify yield-crossing variables.

**Current status**: All locals are promoted to struct fields (no liveness optimization).
This is correct but over-allocates. The optimization is deferred -- it doesn't change
behavior, only reduces generator struct size.

### Control Flow Challenges

Yield inside control flow requires careful state decomposition:

**Yield in if/else:**

```python
def gen(flag: bool) -> Iterator[int32]:
    if flag:
        yield 1
    else:
        yield 2
    yield 3
```

Each yield gets a unique state label. After yield, the switch dispatches to the
correct continuation point.

**Yield in nested loops:**

```python
def matrix_gen(rows: list[list[int32]]) -> Iterator[int32]:
    for row in rows:
        for val in row:
            yield val
```

This requires the outer loop's iterator state to persist across yields from the
inner loop. Both loop variables and iterator state become struct fields.

**Yield in try/finally (future):**

Yield inside `try`/`finally` blocks requires tracking whether cleanup is pending.
Deferred until the exception model (C1/C2) is implemented.

### Integration with Existing Infrastructure

Generator functions produce objects that satisfy `Iterator[T]` and `Iterable[T]`.
They integrate with:

- **For-loops**: `for x in count_up(10)` uses the existing `__next__()` dispatch
- **Generator expressions**: Currently use `tpy::generator_wrapper<T, F>`. Generator
  functions would produce named structs but satisfy the same protocols.
- **Builtins**: `list(gen())`, `sum(gen())`, etc. work via the `Iterable[T]` protocol
- **Type system**: A generator function's return type is `Iterator[T]`. The actual
  C++ return type is the generated struct, but sema sees `Iterator[T]`.

### `generator_wrapper` Reuse

For simple generators (single loop, no complex control flow), the existing
`tpy::generator_wrapper<T, F>` + lambda pattern could be used as a shortcut:

```python
def squares(n: int32) -> Iterator[int32]:
    i: int32 = 0
    while i < n:
        yield i * i
        i += 1
```

Could generate:

```cpp
auto squares(int32_t n) {
    return ::tpy::make_generator<int32_t>(
        [n, i = int32_t(0)]() mutable -> std::optional<int32_t> {
            if (i < n) {
                auto val = ::tpy::mul_check(i, i);
                i = ::tpy::add_check(i, int32_t(1));
                return val;
            }
            return std::nullopt;
        }
    );
}
```

This is simpler to generate and reuses existing infrastructure. The full state
machine transformation (struct + switch) is needed only for generators with
multiple yield points or complex control flow.

**Decision**: Start with the lambda/wrapper approach for simple generators
(Phase 4a), add the full state machine for complex generators (Phase 4b).

---

## VII. `@noalloc` Interaction

| Construct | `@noalloc` allowed? | Reason |
|-----------|-------------------|--------|
| `Fn` param with lambda | Yes | Zero-cost template, no allocation |
| `Fn` param with non-escaping closure | Yes | Captures on stack, monomorphized |
| `Callable` param | **No** | `std::function` may heap-allocate |
| `Callable` field | **No** | `std::function` may heap-allocate |
| `Callable` return | **No** | `std::function` may heap-allocate |
| Generator (struct-based) | Yes | Stack-allocated state machine |
| Generator (C++20 coroutine) | **No** | Heap-allocated frame |

In `@noalloc` functions, `Callable` is forbidden -- use `Fn` for callable
parameters instead:

```
error: 'Callable[[int32], int32]' in @noalloc function 'hot_path' --
       std::function may heap-allocate. Use 'Fn[[int32], int32]' for
       zero-cost callable parameters.
```

This is simple to enforce: `Callable` anywhere in an `@noalloc` context is an
error. `Fn` is always safe.

---

## VIII. Implementation Plan

### Phase 1: `Fn` + `Callable` Types + Lambda Expressions (M effort)

**Goal**: `sorted(key=lambda x: x.name)`, `apply(lambda x: x+1, 42)`, and
similar patterns. Both `Fn` (template) and `Callable` (`std::function`) paths.

**Parser changes:**
- Remove `lambda` from `FORBIDDEN_CONSTRUCTS`
- Parse `ast.Lambda` into a new `TpyLambda` AST node
- Parse `Fn[[T, ...], R]` type annotations into `CallableType(..., is_template=True)`
- Parse `Callable[[T, ...], R]` type annotations into `CallableType(..., is_template=False)`

**Type system changes:**
- Add `CallableType(param_types, return_type, is_template)` to `typesys.py`
  (originally split into separate `FnType` and `CallableType`; merged into
  a single class with a flag)
- Add `LambdaType` (anonymous, unique per lambda) that satisfies both Fn and Callable

**Sema changes:**
- Analyze lambda body: infer param types from context (bidirectional inference),
  check return type
- Support `Fn` in parameter position only (error elsewhere)
- Support `Callable` in all positions
- `Fn` -> `Callable` implicit coercion
- Free variable analysis: identify captures (read-only only in Phase 1)
- Validate `Fn` restriction: error if used as field, return, container element

**Codegen changes:**
- `Fn` parameter -> template parameter with `requires` concept constraint
- `Callable` parameter/field/return -> `std::function<R(Args...)>`
- Lambda -> C++ lambda expression with auto-generated capture list
- Non-capturing lambdas: `[](...) { ... }`
- Capturing lambdas (non-escaping): `[capture_list](...) { ... }`

**Tests**: Lambda with `Fn` params, lambda with `Callable` params, `Fn` in
non-param position (error), type inference for lambda params, non-capturing
and capturing lambdas, `Callable` field, error cases.

### Phase 2: Named Function References (S effort) -- Done

**Goal**: `apply(double, 42)` where `double` is a named function.

- Allow function names as expressions (currently only valid in call position)
- Type: infer `CallableType` from the function's signature
- Codegen: pass the function name directly (C++ templates handle it)
- Generic functions: type params inferred from Fn/Callable hint, bounded params validated, codegen emits explicit template instantiation (`identity<int32_t>`)

### Phase 3: Nested `def` + Escaping Closures + `nonlocal` (Done)

**Goal**: Full nested function support with captures and mutable state.

- Parse nested `def` as closure (capture free variables)
- Escaping analysis in sema (return context, Callable param passing)
- Non-escaping: capture by reference; escaping: capture by value
- `nonlocal` declaration: mark captured variables as mutable
- Non-escaping mutable captures: `[&var]`
- Escaping mutable captures: reject with diagnostic (recommend class-based pattern)
- Assignment to outer variable without `nonlocal`: error
- See Known Limitations (Phase 3) in Section V for remaining gaps

### Phase 4: Generator Functions (L effort)

**5a -- Simple generators (lambda-based):**
- Detect `yield` in function body at parse time
- Transform single-yield-point generators into lambda + `generator_wrapper`
- Reuse existing `make_generator<T>` infrastructure

**5b -- Complex generators (state machine):**
- Yield-point enumeration and state label assignment
- Liveness analysis for yield-crossing variables
- State machine struct generation with `__next__()` method
- Control flow flattening (yield in loops, conditionals)

### Phase 5: `@noalloc` Enforcement (S-M effort)

- Check callable context in `@noalloc` functions
- Reject `std::function` usage (escaping closures, callable fields)
- Allow template-based callable params and non-escaping closures

---

## IX. CPython Compatibility

Closures and lambdas are native Python -- no stubs needed. `typing.Callable` is
standard. `Fn` is TPy-specific and will need a CPython stub in `lib/cpy/tpy/`
(a no-op alias for `Callable`, or identity decorator).

Generator functions are also native Python. The `yield` keyword works identically
in CPython. No `no_cpython.txt` should be needed for generator tests.

`nonlocal` is standard Python. No compatibility concerns.

---

## X. Design Decisions

### Resolved

1. **Two explicit callable types (`Fn` vs `Callable`)**: The cost model is
   visible in the source. `Fn` = zero-cost template (params only). `Callable` =
   type-erased `std::function` (works everywhere). No context-dependent behavior.
   Users start with `Callable` for convenience, switch to `Fn` for performance.

2. **Multiple `Fn` params (same signature)**: Each `Fn` parameter gets its own
   template parameter (`__F0`, `__F1`), even when signatures match. This allows
   passing different concrete callables to the same function.

3. **`Callable | None`**: Supported. Maps to `std::optional<std::function<...>>`.
   Always type-erased. Common pattern for optional callbacks
   (`.set_error_callback(handler)`, `callback: Callable[...] | None = None`).
   `Fn | None` is not supported (templates can't represent optional).

4. **`Fn` -> `Callable` coercion**: Implicit. A value matching `Fn` can be
   passed where `Callable` is expected (wrapped in `std::function`). The reverse
   is not possible (type is erased).

5. **Recursive closures**: Not supported. The nested def name is bound after
   the `def` statement, so the body cannot reference itself. C++ `auto`
   lambdas can't capture themselves; would need `std::function` self-reference.

6. **Generator `send()` and `throw()`**: Future extension. Rarely used outside
   async frameworks.

7. **`yield from` delegation**: Future extension. Follows basic yield support.

8. **`Fn | None` is an error**: `Fn` is always a template parameter and cannot
   represent optionality. Use `Callable | None` for optional callbacks. Error
   message: "Fn cannot be optional -- use Callable | None instead."

---

**Terminal note (2026-09-12).** The lambda-based simple-generator path of Phase
5a was deleted (commit `06ded87f5d`): every generator, single-yield included, now
lowers on the resumable frame. Generator *expressions* still render via
`make_generator`. The body above is history.
