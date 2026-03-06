# Iterator Design

## Status

| Feature | Status | Notes |
|---------|--------|-------|
| `range()` as `Range[T]` type | **Done** | Generic over Int32/BigInt |
| `range()` counter-loop optimization | **Done** | `for i in range(n)` → C-style `for` |
| `OptIterator[T]` protocol | **Done** | Structural: `__next_opt__() -> Optional[T]` |
| User `__next__` + `raise StopIteration` | **Done** | Transformed to `__next_opt__` in codegen |
| User `__next_opt__` (TurboPython-specific) | **Done** | Direct optional-return pattern |
| `__iter__` container→iterator separation | **Done** | Structural detection, no protocol |
| `NativeIterable[T]` (C++ containers) | **Done** | Marker protocol for `begin()`/`end()` |
| Iterator consumption semantics | **Done** | `auto&` for lvalues, `auto` for rvalues |
| `__next__()` panic stub | **Done** | Direct calls compile but panic at runtime |
| `Iterable[T]` protocol | **Todo** | Needs return-type conformance in protocol system |
| C++ `begin()`/`end()` on iterators | **Todo** | Make generated types usable with `std::ranges` |
| `next()` builtin | **Todo** | `next(it)` and `next(it, default)` |
| `iter()` builtin | **Todo** | `iter(obj)` calls `__iter__()`, two-arg `iter(callable, sentinel)` |
| `__reversed__` / `reversed()` | **Todo** | User-defined reverse iteration |
| `__contains__` / `in` for user types | **Todo** | Currently `in` only works on built-in containers |
| Iterator combinators | **Todo** | `enumerate()`, `zip()`, `filter()`, `map()` |
| Generator functions (`yield`) | **Todo** | State-machine class implementing OptIterator |
| `yield from` | **Todo** | Delegation to sub-generators |
| Generator `send()`/`throw()`/`close()` | **Todo** | Coroutine protocol |
| `StopIteration` with value | **Todo** | Generator return values via `raise StopIteration(value)` |
| `itertools` module | **Todo** | `chain`, `islice`, `count`, `cycle`, `repeat`, etc. |
| Async iterators | **Todo** | `async for`, `__aiter__`, `__anext__` |

---

## Architecture

### Three For-Loop Paths

The `for` statement dispatches to one of three codegen paths based on the iterable's type:

```
for x in expr:
    body

    ┌─ OptIterator[T]?  ──→  while-loop (or range counter-loop)
    │
    ├─ has __iter__()?   ──→  __iter__() + while-loop
    │
    └─ NativeIterable?   ──→  C++ range-based for
```

**Path 1: OptIterator** — types with `__next_opt__() -> Optional[T]`. Includes `Range[T]` and user-defined iterators. Range calls get an additional optimization to C-style counter loops.

**Path 2: `__iter__` protocol** — types with `__iter__()` returning an OptIterator. The container is materialized first, then `__iter__()` is called to obtain a separate iterator object.

**Path 3: NativeIterable** -- C++ containers with `begin()`/`end()` (`list`, `Array`, `Span`, `str`). Uses C++ range-based `for` directly.

### Key Types

| Type | Role | C++ |
|------|------|-----|
| `OptIterator[T]` | Structural protocol for lazy iterators | `tpy::OptIterator` concept |
| `NativeIterable[T]` | Marker protocol for C++ containers | `tpy::NativeIterable` concept |
| `Range[T]` | Built-in range iterator (generic) | `tpy::Range<T>` |

---

## OptIterator Protocol

`OptIterator[T]` is a **structural protocol** — any type with `__next_opt__() -> Optional[T]` conforms automatically, no declaration needed.

### C++ Concept

```cpp
template<typename T, typename ElemT>
concept OptIterator = requires(T& t) {
    { t.__next_opt__() } -> std::same_as<std::optional<ElemT>>;
};
```

### Two Authoring Patterns

Users can define iterators in two ways:

**Pattern 1: Python-compatible** (`__next__` + `raise StopIteration`):

```python
class Counter:
    current: Int32
    limit: Int32

    def __init__(self, limit: Int32) -> None:
        self.current = 0
        self.limit = limit

    def __iter__(self) -> Counter:
        return self

    def __next__(self) -> Int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration
```

This runs in both TurboPython and CPython. The compiler transforms it under the hood.

**Pattern 2: TurboPython-specific** (`__next_opt__`):

```python
class Counter:
    # ... same fields ...
    def __next_opt__(self) -> Int32 | None:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        return None
```

Both patterns produce the same C++ output.

### `__next__` Transformation

When the compiler sees `def __next__(self) -> T`, it:

1. **Sema** — validates `__next__` has an explicit return type annotation and the class doesn't also define `__next_opt__`. Synthesizes a `__next_opt__() -> Optional[T]` entry in the type registry for protocol conformance.

2. **Codegen** — emits the method body as `__next_opt__() -> std::optional<T>`:
   - `return expr` works via C++ implicit `optional` construction
   - `raise StopIteration` → `return std::nullopt;`
   - Also emits a `__next__()` panic stub (so direct calls compile but fail at runtime)

Generated C++ for the Counter example:
```cpp
struct Counter {
  int32_t current;
  int32_t limit;

  Counter& __iter__() { return (*this); }

  std::optional<int32_t> __next_opt__() {
    if (this->current < this->limit) {
      int32_t result = this->current;
      this->current = tpy::int32_add(this->current, 1);
      return result;
    }
    return std::nullopt;
  }

  int32_t __next__() {
    tpy::tpy_panic("__next__() is not directly callable; use a for-loop");
  }
};
```

### Validation Rules

| Rule | Stage |
|------|-------|
| `__next__` must have explicit return type | Sema |
| Cannot define both `__next__` and `__next_opt__` | Sema |
| `raise StopIteration` only inside `__next__` | Sema |
| `raise StopIteration` does not accept arguments | Parser |
| Direct `obj.__next__()` calls → runtime panic | Codegen (panic stub) |

---

## `__iter__` Protocol

The `__iter__` method enables the **container → separate iterator** pattern, where the container creates a fresh iterator each time it's iterated.

### Detection

`__iter__` support is **structural** — the compiler checks if the type has an `__iter__()` method and whether the return type conforms to `OptIterator[T]`. There is no `Iterable[T]` protocol type yet (see Known Limitations).

### Codegen Pattern

```python
for x in container:
    body
```

Generates:
```cpp
auto& __obj_0 = container;           // reference if lvalue
auto  __iter_0 = __obj_0.__iter__(); // always own the iterator
while (auto __opt_0 = __iter_0.__next_opt__()) {
    int32_t x = *__opt_0;
    // body
}
```

The iterator returned by `__iter__()` is always owned (`auto`, not `auto&`) because it's typically a freshly constructed object.

### Container Example

```python
class NumberRange:
    start: Int32
    limit: Int32

    def __init__(self, start: Int32, limit: Int32) -> None:
        self.start = start
        self.limit = limit

    def __iter__(self) -> Own[RangeIter]:
        return RangeIter(self.start, self.limit)

class RangeIter:
    current: Int32
    limit: Int32

    def __init__(self, start: Int32, limit: Int32) -> None:
        self.current = start
        self.limit = limit

    def __next__(self) -> Int32:
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

Int32 uses `tpy::int32_add()` for overflow checking; BigInt uses `+=`.

When range optimization can't apply (e.g., zero step detected at codegen time), it falls back to the general OptIterator while-loop path using `Range<T>.__next_opt__()`.

---

## NativeIterable (C++ Containers)

`NativeIterable[T]` is a **marker protocol** for types with C++ `begin()`/`end()`. It generates standard C++ range-based for loops:

```python
for x in items:  # items: list[Int32]
```

```cpp
for (int32_t x : items) {
    // body
}
```

Conforming built-in types: `list[T]`, `Array[T, N]`, `Span[T]`, `str` (as `NativeIterable[Char]`).

This path is not user-extensible — it requires the C++ type to support `std::ranges::begin()`/`std::ranges::end()`.

---

## Known Limitations

- **No `Iterable[T]` protocol**: `__iter__` support is structural (detected by `get_iter_element_type()`), not protocol-based. Can't write `def f(it: Iterable[T])` as a parameter type. Adding it requires return-type conformance checking in the protocol system.

- **Generic Optional codegen mismatch**: For generic records, `T | None` generates `T*`/`nullptr` instead of `std::optional<T>`. This breaks C++ concepts that expect `std::optional<ElemT>` (e.g., `tpy::OptIterator`).

- **No `next()` builtin**: Direct `obj.__next__()` calls are rejected at runtime (panic stub). The `next()` builtin function is not yet implemented.

- **No C++ range compatibility for user iterators**: User-defined `OptIterator` and `__iter__` types don't expose `begin()`/`end()`, so they can't be used with C++ `std::ranges` algorithms or range-based `for` from external C++ code.

---

## Test Coverage

Tests live in `tests/cases/iterators/`:

| Test | What it covers |
|------|----------------|
| `for_range` | Basic `range(n)` |
| `for_range_step` | `range(start, stop, step)` |
| `for_range_edge_cases` | Empty ranges, negative steps |
| `for_range_bigint` | `range()` with BigInt args |
| `for_range_mixed` | Mixed Int32/BigInt range args |
| `for_range_snapshot` | Generated C++ for range loops |
| `for_native_iterable` | `NativeIterable[T]` protocol parameter |
| `for_native_iterator` | `OptIterator[T]` protocol parameter |
| `for_user_iterator` | User `__next_opt__` pattern |
| `for_user_iterator_dunder` | User `__next__` + `raise StopIteration` |
| `for_inherited_iterator` | Iterator via class inheritance |
| `for_iter_protocol` | `__iter__()` container→iterator pattern |
| `for_iterator_consumption` | Second loop over consumed iterator is empty |
| `for_iterator_field_consumption` | Consumption via `obj.field` |
| `for_iterator_subscript_consumption` | Consumption via `items[idx]` |
| `for_iterator_method_consumption` | Consumption via `obj.method()` |
| `native_iterable_str` | String iteration (`NativeIterable[Char]`) |
| `panic_direct_dunder_next` | `obj.__next__()` panics at runtime |
| `panic_range_zero_step` | `range(0, 5, 0)` panics |
| `panic_range_variable_zero_step` | Variable zero step panics |
| `error_for_bad_next` | `__next_opt__` with wrong return type |
| `error_for_bad_next_arity` | `__next_opt__` with wrong arity |
| `error_iter_bad_return` | `__iter__` returning non-iterator |
| `error_next_no_return_type` | `__next__` without return annotation |
| `error_next_dual_definition` | Both `__next__` and `__next_opt__` |
| `error_raise_stop_outside_next` | `raise StopIteration` outside `__next__` |
| `error_raise_stop_with_args` | `raise StopIteration("msg")` with args |
| `error_native_iterable_*` | Various `NativeIterable` type errors |
| `error_range_bigint_protocol` | Range with non-conforming type |

---

## Implementation Map

| File | Role |
|------|------|
| `runtime/cpp/include/tpy/range.hpp` | `Range<T>` with `__next_opt__()` |
| `runtime/cpp/include/tpy/protocols.hpp` | `OptIterator`, `NativeIterable` concepts |
| `tpyc/modules/tpy.py` | `OptIterator[T]` protocol definition |
| `tpyc/modules/__init__.py` | `get_native_iterator_element_type()`, `get_iter_element_type()` |
| `tpyc/sema/registration.py` | Synthetic `__next_opt__` from `__next__` |
| `tpyc/sema/analyzer.py` | `__next__` validation (return type, dual definition) |
| `tpyc/sema/statements.py` | `raise StopIteration` validation |
| `tpyc/sema/list_literals.py` | `is_type_iterable()`, `get_iterable_element_type_or_none()` |
| `tpyc/parse/parser.py` | `raise StopIteration` parsing |
| `tpyc/parse/nodes.py` | `TpyRaiseStopIteration` node |
| `tpyc/codegen_cpp/statements.py` | All for-loop codegen paths, `_is_lvalue_iterable()` |
| `tpyc/codegen_cpp/functions.py` | `__next__` → `__next_opt__` transform, panic stub |
| `tests/harness/tpy/__init__.py` | CPython simulation of `OptIterator` |
