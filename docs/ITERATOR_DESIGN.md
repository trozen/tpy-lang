# Iterator Design

## Status

| Feature | Status | Notes |
|---------|--------|-------|
| `range()` as `Range[T]` type | **Done** | Generic over int32/BigInt |
| `range()` counter-loop optimization | **Done** | `for i in range(n)` → C-style `for` |
| `Iterator[T]` protocol | **Done** | `__next__()` with `@error_return(StopIteration)` -> `std::expected` |
| User `__next__` + `raise StopIteration` | **Done** | Auto `@error_return(StopIteration)`, `std::expected` codegen |
| `__iter__` container→iterator separation | **Done** | Structural detection via `get_iterable_element_type()` |
| `NativeIterable[T]` (C++ containers) | **Done** | Marker protocol for `begin()`/`end()`; opt-in fast-path via `Iterable[T] \| NativeIterable[T]` + isinstance narrowing |
| `Spannable[T]` protocol | **Done** | `__span__() -> Span[readonly[T]]`; used for `Span[T]` coercion and contiguous iteration |
| `Iterable[T]` protocol | **Done** | Structural conformance; works as parameter type and union arm |
| Iterator consumption semantics | **Done** | `auto&&` binding preserves in-place mutation and move-only owning iterators |
| `__next__()` explicit calls | **Done** | Direct calls require `try/except StopIteration`. Inside a generator or `async def` that `try` must not also need frame states of its own (no `await`/`yield` in it, no `break`/`continue` leaving it) -- see the `ReturnException` row in Validation Rules |
| Consuming iteration (`__iter__(self: Own[Self])`) | **Done** | See `docs/CONSUMING_ITERATION_DESIGN.md` |
| `OwnIter[T]` runtime type | **Done** | Drain iterator for `list[T]`, owns moved `std::vector<T>` |
| `Iterator[Own[T]]` coercion to `Iterator[T]` | **Done** | Strips `Own` on each element |
| C++ `begin()`/`end()` on iterators | **Done** | `next_iter_mixin` CRTP adds begin/end from `__next__`; builtin containers native |
| Iterator combinators | **Done** | `enumerate()`, `zip()`, `reversed()`, `map()`, `filter()` |
| `next()` builtin | **Todo** | `next(it)` and `next(it, default)` |
| `iter()` builtin | **Done** | `iter(obj)` calls `__iter__()`; two-arg form (sentinel) TODO |
| `__reversed__` / `reversed()` user types | **Todo** | `reversed()` builtin works on built-in containers; user `__reversed__` is a roadmap item |
| `__contains__` / `in` for user types | **Todo** | `in` falls back to `__iter__`+`__next__` for non-builtins; user `__contains__` dispatch is a roadmap item |
| Generator functions (`yield`) | **Done** | Resumable-frame state-machine struct implementing `Iterator[T]` |
| `yield from` | **Todo** | Delegation to sub-generators |
| Generator `send()`/`throw()`/`close()` | **Todo** | Coroutine protocol |
| `StopIteration` with value | **Todo** | Generator return values via `raise StopIteration(value)` |
| `itertools` module | **Todo** | `chain`, `islice`, `count`, `cycle`, `repeat`, etc. |
| Async iterators | **Todo** | `async for`, `__aiter__`, `__anext__` |

---

## Architecture

### Sema / codegen split

**Sema** reasons about iterability in Python terms: "does this type have `__iter__() -> Iterator[T]` (declared, auto-synthesized from `__next__`, or inherited), or is it a supported iterator shape?" The single entry point is `get_iterable_element_type(type, registry)` in `tpyc/modules/type_resolution.py`. No physical-lowering knowledge lives in sema.

**Codegen** picks the physical lowering. `_gen_for_each_loop` in `codegen_cpp/statements.py` has a priority-ordered dispatch:

```
for x in expr:
    body

  1. enum iteration               → range-for over EnumUtil<E>::members
  2. OwnIter[T] / CopyIter[T]     → range-for (they expose begin/end natively)
  3. auto-consuming iteration     → user __iter__(Own[Self]) path
  4. range(...) literal call      → C-style counter loop (or Range<T> begin/end)
  5. concrete NativeIterable OR
     NativeIterable[T] / Spannable[T]
     protocol param                → C++ range-based for (begin/end)
  6. universal default             → auto&& __itr = ::tpy::__iter__(src);
                                     for (;;) { r = __itr.__next__();
                                                if (!r.has_value()) break;
                                                x = unwrap_ref(*r); ... }
```

Only a concrete type `is_native_iterable(T)` (i.e. extends the `NativeIterable[T]` marker) reaches branch 5 for the container form; user records fall through to branch 6 unless they opt in via a plain `Iterable[T]` param and `isinstance(x, NativeIterable)` narrowing (see "NativeIterable fast-path narrowing" below). Spannable protocol params also reach branch 5, because the compiler synthesizes `begin()`/`end()` on records that declare `__span__()`.

Two implementation details are load-bearing:

- **`auto&&` binding for `__itr`** in the universal default. Universal-reference deduces `T&` for reference returns (iterator self& -- in-place consumption, no copy, works with move-only owning iterators) and lifetime-extends value returns (container -> `native_iterator` fallback).
- **`SpanIter.__iter__` is non-const.** Both the stub and runtime return `SpanIter&`. A const `__iter__` would contradict Python semantics (iterator needs `__next__` to mutate) and block the `auto&&` universal path.

### NativeIterable fast-path narrowing

`NativeIterable[T]` is primarily a marker protocol used internally (sema recognizes built-in containers that extend it; codegen uses it to select C++ range-for over the `__iter__`/`__next__` adapter). It is NOT recommended as a plain parameter type -- plain `Iterable[T]` handles the common case.

Users who want explicit control over iteration strategy (e.g. writing a hot loop where they know the argument has real `begin`/`end`) can narrow a plain `Iterable[T]` param:

```python
def sum_fast(it: Iterable[T]) -> T:
    total: T = ...
    if isinstance(it, NativeIterable):
        # narrowed to NativeIterable[T] -- C++ range-for (begin/end)
        for x in it: ...
    else:
        # Iterable[T] -- universal __iter__/__next__
        for x in it: ...
```

Because `NativeIterable[T]` extends `Iterable[T]`, sema threads the source's element type through `parent_protocols` to produce the narrowed `NativeIterable[int32]`; codegen emits `if constexpr (::tpy::NativeIterable<T_it, int32_t>)` with both template args. The older `Iterable[T] | NativeIterable[T]` union form still works but is no longer required. Sema's protocol-isinstance narrowing is stored in `then_type_facts` and propagated into codegen via `CodeGenContext.protocol_narrowings` (save/restored around every if/elif/else/while body). `codegen_cpp/types.py::get_resolved_type(TpyName)` consults it first, so the for-loop dispatch inside each branch sees the narrower type and picks the matching peephole.

The narrowing only fires when the check protocol inherits from the declared one (via `ProtocolChecker.protocol_inherits_from`); cross-protocol checks against an unrelated multi-arg protocol are rejected at sema with a clear error rather than silently emitting a wrong-arity C++ concept.

Automatic dispatch (emitting both branches inside every `Iterable[T]` template, gated by `if constexpr (NativeIterable<T>)`) is a planned follow-up; benchmark-gated since modern inlining often erases the difference.

### Key Types

| Type | Role | C++ |
|------|------|-----|
| `Iterator[T]` | Protocol for lazy iterators (`__next__` + `@error_return(StopIteration)`) | `std::expected<T, StopIteration>` return |
| `Iterable[T]` | Protocol for types with `__iter__()` | Concept: `requires { ::tpy::__iter__(t); }` |
| `NativeIterable[T]` | Marker protocol for C++ containers (begin/end) | `tpy::NativeIterable` concept |
| `Spannable[T]` | Protocol for types with `__span__() -> Span[readonly[T]]` | `tpy::Spannable` concept |
| `Range[T]` | Built-in range iterator (generic) | `tpy::Range<T>` |

---

## Iterator Protocol

`Iterator[T]` is the standard iterator protocol. Iterators define `__next__(self) -> T` with `@error_return(StopIteration)`, which compiles to `std::expected<T, StopIteration>` in C++. The compiler auto-adds `@error_return(StopIteration)` on `__next__` methods, so the decorator is not required explicitly.

### Authoring Pattern

**Python-compatible** (`__next__` + `raise StopIteration`):

```python
class Counter:
    current: int32
    limit: int32

    def __init__(self, limit: int32) -> None:
        self.current = 0
        self.limit = limit

    def __iter__(self) -> Counter:
        return self

    def __next__(self) -> int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration
```

This runs in both TurboPython and CPython. The compiler auto-applies `@error_return(StopIteration)`.

### `__next__` Codegen

When the compiler sees `def __next__(self) -> T`, it:

1. **Sema** -- validates `__next__` has an explicit return type annotation. Auto-adds `@error_return(StopIteration)`.

2. **Codegen** -- emits the method as `__next__() -> std::expected<T, StopIteration>`:
   - `return expr` works via C++ implicit `std::expected` construction
   - `raise StopIteration` -> `return std::unexpected(StopIteration{});`

Generated C++ for the Counter example:
```cpp
struct Counter {
  int32_t current;
  int32_t limit;

  Counter& __iter__() { return (*this); }

  std::expected<int32_t, StopIteration> __next__() {
    if (this->current < this->limit) {
      int32_t result = this->current;
      this->current = tpy::int32_add(this->current, 1);
      return result;
    }
    return std::unexpected(StopIteration{});
  }
};
```

### Validation Rules

| Rule | Stage |
|------|-------|
| `__next__` must have explicit return type | Sema |
| `raise StopIteration` only inside `__next__` (or `@error_return(StopIteration)` functions) | Sema |
| `raise StopIteration` does not accept arguments | Parser |
| Direct `obj.__next__()` calls require `try/except StopIteration` | Sema (caller enforcement) |
| Inside a generator or `async def`, a `try` that needs frame states of its own (holds an `await`/`yield`, or a `break`/`continue` leaving it) must not also hold a call its `ReturnException` handler would catch -- the frame has no edge for the failing unwrap (BUGS.md#frame-try-next-error-return-miscompiles). Either condition alone is fine | Sema + resumable CFG |

---

## `__iter__` Protocol

The `__iter__` method enables the **container → separate iterator** pattern, where the container creates a fresh iterator each time it's iterated.

### Detection

`__iter__` support is **structural** -- the compiler checks if the type has an `__iter__()` method and whether the return type conforms to `Iterator[T]`. There is no `Iterable[T]` protocol type yet (see Known Limitations).

### Codegen Pattern

```python
for x in container:
    body
```

Generates:
```cpp
auto& __obj_0 = container;           // reference if lvalue
auto  __iter_0 = __obj_0.__iter__(); // always own the iterator
for (;;) {
    auto __r_0 = __iter_0.__next__();  // std::expected<T, StopIteration>
    if (!__r_0.has_value()) break;
    int32_t x = *__r_0;
    // body
}
```

The iterator returned by `__iter__()` is always owned (`auto`, not `auto&`) because it's typically a freshly constructed object.

### Container Example

```python
class NumberRange:
    start: int32
    limit: int32

    def __init__(self, start: int32, limit: int32) -> None:
        self.start = start
        self.limit = limit

    def __iter__(self) -> Own[RangeIter]:
        return RangeIter(self.start, self.limit)

class RangeIter:
    current: int32
    limit: int32

    def __init__(self, start: int32, limit: int32) -> None:
        self.current = start
        self.limit = limit

    def __next__(self) -> int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration
```

Each `for x in NumberRange(0, 5)` creates a fresh `RangeIter`, so the container can be iterated multiple times.

---

## Lvalue Binding (Consumption Semantics)

Python iterators are consumed — iterating twice over the same iterator yields nothing the second time. TurboPython preserves this by choosing `auto&` (reference) vs `auto` (copy) based on the expression's C++ value category:

| Expression | Value Category | Binding | Why |
|---|---|---|---|
| `iter` | lvalue (variable) | `auto&` | Reference to existing iterator |
| `obj.field` | lvalue (field access) | `auto&` | Reference to storage in object |
| `items[0]` | lvalue (subscript) | `auto&` | Reference to container element |
| `obj.get_it()` | lvalue (method, T& return) | `auto&` | Non-value return = reference |
| `Counter(5)` | rvalue (constructor) | `auto` | Own the temporary |
| `make_iter()` | rvalue (Own[T] return) | `auto` | Own the returned value |

The check is implemented in `_is_lvalue_iterable()` which recursively walks the expression:
- `TpyName` → lvalue
- `TpyFieldAccess`, `TpySubscript` → recurse into object
- `TpyMethodCall` → lvalue if return type is non-value and not Optional (C++ `T&`)
- `TpyCall` → rvalue if constructor; otherwise same return-type check as methods
- Everything else → rvalue

---

## Range Optimization

`for i in range(...)` is optimized to C-style counter loops:

```python
for i in range(10):          # → for (int32_t i = 0; i < 10; ++i)
for i in range(2, 10):       # → for (int32_t i = 2; i < 10; ++i)
for i in range(10, 0, -1):   # → for (int32_t i = 10; i > 0; --i)
for i in range(0, 10, 3):    # → for (int32_t i = 0; i < 10; i = int32_add(i, 3))
```

| Step | Comparison | Increment |
|------|-----------|-----------|
| +1 (default) | `<` | `++i` |
| -1 | `>` | `--i` |
| literal > 0 | `<` | checked add |
| literal < 0 | `>` | checked add |
| variable | ternary `step > 0 ? i < stop : i > stop` | checked add |
| 0 | n/a | panics at runtime |

Non-literal start/stop arguments are pre-evaluated into temporaries to match Python's evaluate-once semantics.

int32 uses `tpy::int32_add()` for overflow checking; BigInt uses `+=`.

When range optimization can't apply (e.g., zero step detected at codegen time), it falls back to the general Iterator while-loop path using `Range<T>.__next__()`.

---

## Generator Yield Slot Shape

Iterator yields hand out references like function returns -- mutations through the yielded value flow back to the iterable, matching CPython semantics. The iterator slot type for a generator is therefore the **borrow form** of the yield type (see `LANGUAGE_FEATURES.md` "Borrow Form vs Storage Form" for the canonical definition), computed via `elem_type.to_cpp_return()` for tuple yields:

| Python yield type | C++ iterator slot |
|---|---|
| `tuple[int32, Point]` | `std::tuple<int32_t, Point&>` |
| `tuple[P \| None, P \| None]` | `std::tuple<P*, P*>` |
| `tuple[int32, int32]` | `std::tuple<int32_t, int32_t>` (value form -- borrow=value for primitives) |
| `Point` (bare reference type) | `::tpy::val_or_ref<Point>` (a pointer-holding value wrapper -- `Point&` directly would make `std::optional<T&>` ill-formed pre-C++26) |
| `readonly[Point]` | `::tpy::val_or_ref<const Point>` |
| `int32` (bare value type) | `int32_t` (value form -- copies are free) |
| `T` (an unsubstituted type param) | `::tpy::yield_slot_t<T>` when this generator LENDS, else `T`. The trait is `val_or_ref<T>`, which holds a value `T` by value and a reference `T` by pointer, so the element's form is settled at INSTANTIATION and the generic frame renders what its monomorphic twin does; it is idempotent, since a call site that infers `T` as a borrow (`Ref[X]`) substitutes `val_or_ref<X>` already |

The single decision point is `_iter_slot_for_yield(elem_type, cpp_elem, generic_borrows)` in `tpyc/codegen_cpp/gen_generators.py`. The generator frame's `__next__()` return type routes through it.

Whether an open `T` lends is ONE verdict per generator, since one slot type serves the whole frame: a generator lends what it yields when every yield source outlives a suspension, otherwise it hands out a value. Sema settles it AFTER every body in the module (`_settle_deferred_generic_yields`, `tpyc/sema/analyzer.py`) and records it on `TpyFunction.generic_yield_borrows`. Each source's provenance is the ordinary dangling walk read CLOSED-WORLD (`is_dangling_return(..., assume_unknown_calls_safe=False)`, `tpyc/sema/compatibility.py`): a slot CHOICE cannot trust a callee it cannot see through, so a call lends only where its resolved signature borrows from operands that themselves lend. The late settle is what makes the verdict order-independent -- a callee's `return_borrows_from` is filled when the CALLEE's body finishes, so a verdict taken during the generator's own body would differ depending on which of the two definitions came first. It is a verdict, never a diagnostic: an unrooted source picks the value slot rather than an error, because the same body also instantiates at value `T`s, which borrow nothing.

When the yielded expression's natural form doesn't match the slot (e.g. `yield self.field` where the field is stored in storage form), `gen_yield_value` in `codegen_cpp/statements.py` bridges via `tuple_to_pointer`. Pointer-form sources (rvalue tuple literals, pointer-form locals) pass through unchanged.

The asymmetry with field/container slots is by design: fields and containers own their data (storage form, `std::optional<P>`); iterators borrow into the iterable (borrow form, `P*`). The conversion happens at the boundary.

**Lifetime caveat:** the yielded tuple holds references into the iterable's backing storage. Structural mutation of the iterable between `__next__` calls (`items.append(...)`, `dict[k] = ...` triggering rehash) dangles already-yielded references. The borrow checker doesn't track iterator-captured containers today; see BUGS.md.

---

## NativeIterable (C++ Containers)

`NativeIterable[T]` is a **marker protocol** for types with C++ `begin()`/`end()`. It generates standard C++ range-based for loops:

```python
for x in items:  # items: list[int32]
```

```cpp
for (int32_t x : items) {
    // body
}
```

Conforming built-in types: `list[T]`, `Array[T, N]`, `Span[T]`, `str` (as `NativeIterable[char]`).

This path is not user-extensible — it requires the C++ type to support `std::ranges::begin()`/`std::ranges::end()`.

---

## Known Limitations

- **No `next()` builtin**: Direct `obj.__next__()` calls require `try/except StopIteration`. The `next()` builtin function is not yet implemented.

- **`match/case` guard-derived `protocol_narrowings` aren't applied to case bodies.** `_emit_case_body` save/restores the dict around bodies (so persistent narrowings don't leak across cases), but isinstance checks inside a `case` guard expression don't push protocol facts into `protocol_narrowings`, so the case body sees the unrefined type.

---

## Test Coverage

Tests live in `tests/cases/iterators/`:

| Test | What it covers |
|------|----------------|
| `for_range` | Basic `range(n)` |
| `for_range_step` | `range(start, stop, step)` |
| `for_range_edge_cases` | Empty ranges, negative steps |
| `for_range_bigint` | `range()` with BigInt args |
| `for_range_mixed` | Mixed int32/BigInt range args |
| `for_range_snapshot` | Generated C++ for range loops |
| `iterable_protocol_ops` | `Iterable[T]` protocol parameter (nested loops, `in`, protocol-to-protocol forwarding) |
| `iterable_native_narrowing` | `Iterable[T] \| NativeIterable[T]` + isinstance narrowing; elif / nested-if save/restore |
| `iterable_native_narrowing_user_iter` | Narrowing with a user iterator (tpyc-only, no_cpython.txt) |
| `for_native_iterator` | `Iterator[T]` protocol parameter |
| `for_user_iterator` | User `__next__` pattern |
| `for_user_iterator_dunder` | User `__next__` + `raise StopIteration` |
| `for_inherited_iterator` | Iterator via class inheritance |
| `for_iter_protocol` | `__iter__()` container→iterator pattern |
| `for_iterator_consumption` | Second loop over consumed iterator is empty |
| `for_iterator_field_consumption` | Consumption via `obj.field` |
| `for_iterator_subscript_consumption` | Consumption via `items[idx]` |
| `for_iterator_method_consumption` | Consumption via `obj.method()` |
| `gen_basic` | Simple while-loop generator |
| `gen_sequential` | Multiple sequential yields |
| `gen_conditional` | Yield in if/else |
| `gen_fibonacci` | Mutable state generator (while-loop) |
| `gen_early_return` | Bare return in generator |
| `gen_nested_while` | Nested while-loops |
| `gen_list_consume` | `list(gen())` consumption |
| `gen_for_range` | Simple for-range generator (lambda path) |
| `gen_for_container` | Simple for-container generator (lambda path) |
| `gen_for_complex` | Complex generator with yield in for-loop over container |
| `gen_for_range_complex` | Complex generator with yield in for-loop over range |
| `gen_for_multi` | Multiple for-loops (container + range) in one generator |
| `gen_for_user_iter` | For-loop over user `__iter__()` type in complex generator |
| `gen_two_generators` | Two complex generators in same module |
| `panic_direct_dunder_next` | `obj.__next__()` without try/except |
| `panic_range_zero_step` | `range(0, 5, 0)` panics |
| `panic_range_variable_zero_step` | Variable zero step panics |
| `error_for_bad_next` | `__next__` with wrong return type |
| `error_for_bad_next_arity` | `__next__` with wrong arity |
| `error_iter_bad_return` | `__iter__` returning non-iterator |
| `error_next_no_return_type` | `__next__` without return annotation |
| `error_next_dual_definition` | Duplicate `__next__` definitions |
| `error_raise_stop_outside_next` | `raise StopIteration` outside `__next__` |
| `error_raise_stop_with_args` | `raise StopIteration("msg")` with args |
| `error_native_iterable_*` | Various `NativeIterable` type errors |
| `error_range_bigint_protocol` | Range with non-conforming type |

---

## Implementation Map

| File | Role |
|------|------|
| `runtime/cpp/include/tpy/range.hpp` | `Range<T>` with `__next__()` -> `std::expected` |
| `runtime/cpp/include/tpy/protocols.hpp` | `NativeIterable` concept |
| `lib/tpy/tpy/_typing/__init__.py` | `Iterator[T]` / `Iterable[T]` protocol definitions |
| `lib/tpy/tpy/_core/_types.py` | `NativeIterable[T]` / `Spannable[T]` protocol definitions |
| `tpyc/modules/__init__.py` | `get_iterable_element_type()`, `is_native_iterable()`, `ITERABLE_PROTOCOL_QNAMES` |
| `tpyc/modules/type_resolution.py` | `get_iterable_element_type()` -- single source of truth for iteration element types |
| `tpyc/codegen_cpp/context.py` | `protocol_narrowings` dict + save/restore helpers |
| `tpyc/codegen_cpp/types.py` | `get_resolved_type()` consults `protocol_narrowings` |
| `tpyc/codegen_cpp/records.py` | Synthesizes `begin()/end()` for user records with `__iter__ -> SpanIter[T]` or `__span__()` |
| `tpyc/sema/registration.py` | `__next__` auto `@error_return(StopIteration)` |
| `tpyc/sema/analyzer.py` | `__next__` validation (return type) |
| `tpyc/sema/statements.py` | `raise StopIteration` validation |
| `tpyc/sema/list_literals.py` | `is_type_iterable()`, `get_iterable_element_type_or_none()` |
| `tpyc/parse/parser.py` | `raise StopIteration` parsing |
| `tpyc/parse/nodes.py` | `TpyRaise` node |
| `tpyc/codegen_cpp/statements.py` | All for-loop codegen paths, `_is_lvalue_iterable()`, generator for-loop lowering |
| `tpyc/codegen_cpp/gen_generators.py` | Generator function codegen: state machine structs, lambda optimization, for-loop pre-scan |
| `tpyc/codegen_cpp/functions.py` | `__next__` -> `std::expected` codegen |
