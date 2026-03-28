# Error Return Design

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Generic mechanism: `@error_return(E)` decorator, `raise E` codegen (`std::unexpected`), `try/except` parsing + caller enforcement, `std::expected<T, E>` return + unwrap, `try/except/else` | Done |
| 2 | Iterator migration: auto-add `@error_return(StopIteration)` on `__next__`, `StopIteration` built-in type, for-loop codegen using `std::expected` directly | Done |
| 3 | Cleanup: all iterators unified on `__next__()` -> `std::expected`, removed legacy compat wrappers | Done |

### Future Extensions

See [EXCEPTION_DESIGN.md](EXCEPTION_DESIGN.md) for the full two-tier exception model design.

| Feature | Notes |
|---------|-------|
| Multiple exception types | `@error_return(E1, E2)` -- return `std::expected<T, std::variant<E1, E2>>` |
| `next()` builtin | `next(it)` -- with forwarding, can be `@error_return(StopIteration)` itself |
| General exception model | C++ exceptions for non-control-flow errors -- `try`/`except`/`finally`/`raise` with stack unwinding |

---

## Overview

`@error_return(E)` is a decorator that transforms a function's exception-based
control flow into a result-type return. `raise E` inside the function body compiles
to returning an error variant -- no C++ exceptions, no stack unwinding.

```python
from tpy import Int32, error_return, ControlFlow

class NotFound(Exception, ControlFlow):
    pass

@error_return(NotFound)
def find(items: list[Int32], target: Int32) -> Int32:
    for i in range(len(items)):
        if items[i] == target:
            return i
    raise NotFound
```

Callers **must** handle the error return -- calling without `try/except` is a
compile error:

```python
# Compile error: unhandled error return 'StopIteration' from '__next__'
value = it.__next__()

# Correct: handle the exception
try:
    value = it.__next__()
except StopIteration:
    print("done")
```

This bridges the gap between Python's exception-based protocols (iterators, parsers,
search functions) and TPy's zero-cost compiled output.

---

## Design Principles

1. **Sound by construction**: the compiler enforces that every error return is
   handled. You cannot accidentally ignore an error. This is Rust's `Result<T, E>`
   discipline with Python's `try/except` syntax.

2. **Zero-cost**: no C++ exceptions, no stack unwinding, no RTTI. `raise E` compiles
   to `return std::unexpected(E{})`. `try/except` compiles to a value check.

3. **CPython compatible**: `try/except` is valid Python. `raise StopIteration` is
   valid Python. The same source file runs in both runtimes.

4. **Incremental**: this is NOT the general exception model. It covers the
   "exceptions as control flow" pattern (StopIteration, StopAsyncIteration,
   KeyError for dict.pop, etc.). Real exceptions (C1/C2 in the roadmap) are a
   separate, future design.

---

## Detailed Design

### Exception Type

Error types must inherit from `Exception` (for CPython compatibility) and `ControlFlow`
(to mark them as return-only exceptions):

```python
class NotFound(Exception, ControlFlow):
    pass
```

`Exception` and `BaseException` are registered as empty records in TPy. In C++
they map to empty structs in `::tpy::` with real inheritance:

```cpp
namespace tpy {
struct BaseException {};
struct Exception : BaseException {};
}

// User error type:
struct NotFound : Exception {};
```

`StopIteration` is a built-in type in the runtime (`::tpy::StopIteration`).

Exception types can have data fields. `raise E(args)` passes constructor arguments,
and `except E as e` binds the error value for field access. See `EXCEPTION_DESIGN.md` E6.

### `@error_return(E)` Decorator

#### Syntax

```python
@error_return(StopIteration)
def __next__(self) -> Int32:
    ...
```

The decorator takes exactly one argument: the exception type. The argument must be
a known exception type name (resolved at parse time or sema time).

For `__next__` methods specifically, the compiler auto-adds
`@error_return(StopIteration)` if the decorator is absent. This means existing
`__next__` methods with `raise StopIteration` continue to work without changes.

#### Parser Representation

New field on `TpyFunction`:

```python
@dataclass
class TpyFunction:
    ...
    error_return: str | None = None  # exception type name, e.g. "StopIteration"
```

The parser resolves `@error_return(StopIteration)` and sets
`error_return = "StopIteration"` on the function node.

#### Semantic Effect

In sema, a function with `error_return = "StopIteration"` has its
`FunctionInfo` annotated:

```python
@dataclass
class FunctionInfo:
    ...
    error_return_type: TpyType | None = None  # e.g. StopIterationType
```

The **declared** return type remains `T` (what the user wrote). The **effective**
return type for callers is `T | E` -- but this is not exposed as a union type in the
type system. Instead, it's tracked via `error_return_type` on `FunctionInfo`.

### `raise E` in Function Body

#### Sema Validation

When analyzing `raise E` inside a function:

1. Check that the current function has `@error_return(E)` (or the auto-added
   equivalent for `__next__`).
2. The exception type in the `raise` must match the declared error_return type.
3. `raise E` without matching `@error_return` is an error:
   `"'raise StopIteration' requires @error_return(StopIteration) on the function"`
4. `raise E` in a function with `@error_return(F)` where `E != F` is an error.

This replaces the current `"raise StopIteration can only be used inside __next__"`
check with a more general rule.

#### Codegen

`raise StopIteration` emits:

```cpp
return std::unexpected(StopIteration{});
```

Normal `return expr` emits the value as-is -- `std::expected<T, E>` has implicit
construction from `T`:

```cpp
return result;  // implicit std::expected<int32_t, StopIteration>(result)
```

### Function Return Type in C++

A function `def f() -> T` with `@error_return(E)` generates:

```cpp
std::expected<T, E> f() { ... }
```

For the `__next__` case:

```python
@error_return(StopIteration)
def __next__(self) -> Int32:
    ...
```

Generates:

```cpp
std::expected<int32_t, StopIteration> __next__() {
    if (this->current < this->limit) {
        int32_t result = this->current;
        this->current = ::tpy::add_check<int32_t>(this->current, 1);
        return result;
    }
    return std::unexpected(StopIteration{});
}
```

The method keeps its original name `__next__` in the generated C++.

### `try/except` -- Caller Side

#### Syntax (Phase 1 -- limited)

Phase 1 supports `try/except` **only** for calling functions with
`@error_return`. General exception handling (C++ exceptions) is future work.

Supported forms:

```python
# Form 1: try/except block
try:
    value = it.__next__()
except StopIteration:
    break

# Form 2: try/except with else
try:
    value = it.__next__()
except StopIteration:
    print("done")
else:
    print(value)  # only runs if no exception
```

Not supported in Phase 1:
- `try/except/finally`
- Multiple `except` clauses
- `except E as e` with throw-style exceptions (E7)
- `try` without `except`
- Nested `try`
- `try` around multiple statements that each have different error_return types

#### Parser

New AST nodes:

```python
@dataclass
class TpyTryExcept(TpyStmt):
    """try/except for error_return functions."""
    try_body: list[TpyStmt]
    exception_type: str           # "StopIteration"
    except_body: list[TpyStmt]
    else_body: list[TpyStmt]     # may be empty

@dataclass
class TpyRaise(TpyStmt):
    """raise E -- generalized from TpyRaiseStopIteration."""
    exception_type: str           # "StopIteration"
```

`TpyRaiseStopIteration` is replaced by the general `TpyRaise` node.

Remove `"try"` from `FORBIDDEN_CONSTRUCTS`. Parse `ast.Try` nodes, but validate
the limited form (single except clause, no finally, etc.).

#### Sema -- Caller Enforcement

When analyzing a call expression (in `sema/calls.py` or `sema/expressions.py`):

1. If the callee has `error_return_type is not None`:
2. Check that the call is inside a `TpyTryExcept` whose `exception_type` matches.
3. If not -- **compile error**:
   `"call to '__next__' may raise 'StopIteration' which is not handled; wrap in try/except"`

The check walks up the statement context to find an enclosing `TpyTryExcept`.

**Special case -- for-loops**: When analyzing `for x in expr`, the for-loop
machinery is an implicit `try/except StopIteration` consumer. Calls to `__next__`
from for-loop codegen don't need explicit try/except -- the for-loop handles it.

#### Codegen

```python
try:
    value = it.__next__()
except StopIteration:
    break
```

Generates:

```cpp
{
    auto __result_0 = it.__next__();
    if (!__result_0.has_value()) {
        // except StopIteration body
        break;
    }
    int32_t value = *__result_0;
    // ... rest of try body after the call ...
}
```

With `else`:

```python
try:
    value = it.__next__()
except StopIteration:
    print("done")
else:
    process(value)
```

Generates:

```cpp
{
    auto __result_0 = it.__next__();
    if (!__result_0.has_value()) {
        ::tpy::print("done");
    } else {
        int32_t value = *__result_0;
        process(value);
    }
}
```

### For-Loop Integration

The for-loop is the primary consumer of `__next__` with
`@error_return(StopIteration)`. The codegen uses `std::expected` directly:

```python
for x in counter:
    print(x)
```

Where `counter` has `__next__()` with `@error_return(StopIteration)`:

```cpp
auto& __obj_0 = counter;
while (true) {
    auto __result_0 = __obj_0.__next__();
    if (!__result_0.has_value()) break;
    int32_t x = *__result_0;
    ::tpy::print(x);
}
```

For types with `__iter__()` returning an iterator:

```cpp
auto& __obj_0 = container;
auto __iter_0 = __obj_0.__iter__();
while (true) {
    auto __result_0 = __iter_0.__next__();
    if (!__result_0.has_value()) break;
    int32_t x = *__result_0;
    // body
}
```

**NativeIterable types** (list, Array, Span, str) are unaffected -- they continue
to use C++ range-based `for` with `begin()`/`end()`.

### Auto-Add on `__next__`

When the compiler sees `def __next__(self) -> T` without an explicit
`@error_return` decorator:

1. Auto-add `@error_return(StopIteration)` at parse time (or early sema).
2. This makes `raise StopIteration` valid in the body without any decorator.
3. The user-visible behavior is identical to writing the decorator explicitly.

This means **all existing `__next__` methods continue to work unchanged**.

---

## C++ Runtime Support

### Required Header

`std::expected` is C++23. The project already requires C++23 (for `std::ranges`),
so no new compiler requirements.

Add `#include <expected>` to `tpy.hpp` (or a new `tpy/expected.hpp` if we need
helpers).

### StopIteration Type

```cpp
// runtime/cpp/include/tpy/exceptions.hpp
namespace tpy {

struct StopIteration {};

}  // namespace tpy
```

---

## CPython Compatibility

`try/except` is standard Python syntax. `raise StopIteration` is standard Python.
The same source code runs in both CPython and TPy:

```python
class Counter:
    current: Int32
    limit: Int32

    def __init__(self, limit: Int32) -> None:
        self.current = 0
        self.limit = limit

    def __next__(self) -> Int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration

    def __iter__(self) -> Counter:
        return self
```

- **CPython**: `__next__` raises `StopIteration` exception, caught by for-loop.
- **TPy**: `__next__` returns `std::expected<int32_t, StopIteration>`, checked by
  for-loop codegen.

The `@error_return` decorator needs a CPython stub that's a no-op:

```python
# lib/cpy/tpy/__init__.py
def error_return(exc_type):
    """No-op in CPython -- error_return is a TPy compile-time annotation."""
    def decorator(func):
        return func
    return decorator
```

For `__next__`, the decorator is auto-added silently by the compiler, so CPython
never sees it -- no stub needed for that case.

---

## Error Messages

| Situation | Error |
|-----------|-------|
| `raise E` without `@error_return(E)` | `'raise StopIteration' requires @error_return(StopIteration) on the enclosing function` |
| `raise E` with wrong exception type | `'raise ValueError' does not match @error_return(StopIteration)` |
| Unhandled error_return call | `call to '__next__' may return 'StopIteration' which must be handled with try/except` |
| `except WrongType` for the call | `'__next__' returns 'StopIteration', not 'ValueError'` |
| `try/finally` (not yet supported) | `'finally' is not yet supported` |
| Multiple except clauses | `only a single 'except' clause is supported` |
| `except ControlFlow as e` | `'except ControlFlow as' binding is not supported` |

---

## Test Plan

### New Tests

| Test | What it covers |
|------|----------------|
| `error_return/basic` | `@error_return(StopIteration)` on a function, try/except caller |
| `error_return/try_else` | try/except/else block |
| `error_return/for_loop` | For-loop consuming `__next__` with error_return |
| `error_return/auto_next` | `__next__` without explicit decorator (auto-added) |
| `error_return/iter_protocol` | `__iter__` returning iterator with `__next__` |
| `error_raise_no_decorator` | `raise StopIteration` without `@error_return` |
| `error_raise_wrong_type` | `raise ValueError` with `@error_return(StopIteration)` |
| `error_unhandled_error_return` | Calling error_return function without try/except |
| `error_wrong_except_type` | `except ValueError` when function returns `StopIteration` |

### Existing Tests (should continue passing)

All `tests/cases/iterators/` tests should continue working since `__next__` with
`raise StopIteration` is already the supported pattern. The generated C++ will
change from `std::optional` to `std::expected` but runtime behavior is identical.

---

## Resolved Design Decisions

1. **Naming**: `@error_return` -- describes the mechanism accurately (error is
   returned, not thrown). Not misleading like `@raises` which suggests real
   exceptions.

2. **Implicit `@error_return` on `__next__`**: Silent auto-add, no diagnostic.
   It's always required, so no point in being noisy about it.

3. **`next()` builtin**: Implement with explicit try/except for now. Error
   forwarding (future extension) would allow `next()` to be
   `@error_return(StopIteration)` itself, propagating without boilerplate.

4. **`for/else` interaction**: `for x in it: ... else: ...` where the else runs
   when the loop completes without `break`. The StopIteration catch is what
   triggers the else body. This works naturally with the while-loop codegen.
