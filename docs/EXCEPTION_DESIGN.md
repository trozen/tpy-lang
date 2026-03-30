# Exception Handling Design

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| E1 | `@error_return(E)` mechanism: decorator, `raise E` codegen (`std::unexpected`), `try/except` caller enforcement, `std::expected<T,E>` return, `try/except/else` | Done (see [ERROR_RETURN_DESIGN.md](ERROR_RETURN_DESIGN.md)) |
| E2 | Iterator migration: auto-add `@error_return(StopIteration)` on `__next__`, for-loop codegen via `std::expected` | Done |
| E3 | `ReturnException` marker protocol: split exception types into throw vs return categories, compiler enforcement | Done |
| E4 | Auto-propagation: `@error_return(E)` functions auto-forward matching errors from callees without `try/except` | Done |
| E5 | `except ReturnException` catch-all for return exceptions | Done |
| E6 | Exception types with data fields, `except E as e` binding | Done |
| E7 | General C++ exceptions: `try`/`except`/`finally`/`raise` with stack unwinding for non-control-flow errors | Done |
| E8 | Multiple `except` handlers, bare `except:`, re-raise (`raise` with no argument) | Done |

### Future Extensions

| Feature | Notes |
|---------|-------|
| Multiple return exception types | `@error_return(E1, E2)` -- `std::expected<T, std::variant<E1, E2>>` |
| Exception chaining | `raise X from Y` -- low priority, niche use case |
| `except ReturnException as e` | Bind catch-all value -- needs type-erased wrapper or variant; distant future |
| `@noalloc` interaction | `@noalloc` functions can use `@error_return` (zero-cost) but cannot throw C++ exceptions |
| Custom base exception classes | User-defined exception hierarchies with `except BaseClass` catching subclasses |
| `raise` inside `finally` | Done. Uses catch-all + duplication codegen. `return`/`break`/`continue` in try-with-finally use goto transformation. Nested try/finally supported. |
| Mixed-tier `try`/`except` | Currently ReturnException and non-ReturnException handlers cannot be in the same `try` block. Wrap goto dispatch inside C++ `try`: return-tier gotos inside `try {}`, throw-tier in `catch` handlers. Would eliminate the nested-try workaround for functions that can both return errors and throw. |

---

## Overview

TurboPython has a **two-tier exception model**:

1. **Return exceptions** (`@error_return`) -- zero-cost control-flow errors compiled to `std::expected<T, E>`. For patterns where the "exception" is an expected outcome (iterator exhaustion, lookup miss, parse failure). No stack unwinding, no RTTI, `@noalloc` compatible.

2. **Throw exceptions** (C++ exceptions) -- standard stack-unwinding exceptions for genuine errors (I/O failures, invalid arguments, runtime violations). Same semantics as Python's `raise`/`except` model.

Both tiers use the same Python syntax (`raise E`, `try`/`except`), but the compiler routes to different C++ mechanisms based on the exception type's category.

---

## Design Principles

1. **Explicit tier separation**: exception types are statically classified as either return or throw. The same type cannot be both. This prevents confusion about performance characteristics and calling conventions.

2. **Visible cost model**: `@error_return(E)` on a function signature makes zero-cost error handling immediately visible. Throw exceptions need no annotation -- they're the default for `raise`.

3. **CPython compatible**: both tiers use standard Python syntax (`try`/`except`, `raise`). Source files run unchanged in CPython.

4. **Sound by construction**: return exceptions must be handled (compiler-enforced). Throw exceptions propagate implicitly like in Python.

---

## Tier 1: Return Exceptions (`@error_return`)

See [ERROR_RETURN_DESIGN.md](ERROR_RETURN_DESIGN.md) for the full existing design. Summary:

- `@error_return(E)` on a function wraps its return type in `std::expected<T, E>`
- `raise E` compiles to `return std::unexpected(E{})`
- Callers must handle with `try`/`except` or auto-propagate (E4)
- For-loops implicitly handle `StopIteration` from `__next__`

### `ReturnException` Marker Protocol (E3)

Exception types are split into two categories using a marker protocol:

```python
# In tpy._typing or tpy._builtins._exceptions
class ReturnException(Protocol):
    """Marker for return exception types. These can only be used with @error_return,
    never with C++ throw."""
    ...
```

Built-in control-flow exceptions:

```python
class StopIteration(Exception, ReturnException): ...
```

User-defined control-flow exceptions:

```python
from tpy import ReturnException

class NotFound(Exception, ReturnException):
    pass

class ParseError(Exception, ReturnException):
    pass
```

**Compiler enforcement rules:**

| Context | `ReturnException` type | Non-`ReturnException` type |
|---------|-------------------|----------------------|
| `@error_return(E)` | Allowed | Compile error |
| `raise E` in `@error_return` function | Allowed (returns `std::unexpected`) | Compile error |
| `raise E` in regular function | Compile error | Allowed (C++ `throw`) |
| `except E` in `try/except` for `@error_return` call | Allowed (value check) | Compile error |
| `except E` in `try/except` for throw | Compile error | Allowed (C++ `catch`) |

This makes the tier visible at the exception class definition -- you know from the type alone whether it's zero-cost or stack-unwinding.

### Auto-Propagation (E4)

When an `@error_return(E)` function calls another `@error_return(E)` function (same error type), errors auto-propagate without `try`/`except`:

```python
@error_return(ParseError)
def parse_atom() -> Expr:
    token = next_token()  # auto-propagates ParseError (same E)
    if token.kind == "number":
        return NumLit(token.value)
    raise ParseError

@error_return(ParseError)
def parse_expr() -> Expr:
    left = parse_atom()    # auto-propagates
    op = parse_op()        # auto-propagates
    right = parse_atom()   # auto-propagates
    return BinOp(left, op, right)

def main() -> None:
    # Non-error_return caller must handle explicitly
    try:
        expr = parse_expr()
    except ParseError:
        print("parse failed")
```

**Rules:**

- `@error_return(E)` calls `@error_return(E)` (same E) -> auto-propagate, no `try`/`except` needed
- `@error_return(E)` calls `@error_return(F)` (different E) -> must handle with `try`/`except`
- Non-`@error_return` function calls `@error_return(E)` -> must handle with `try`/`except`
- To handle locally instead of propagating -> use `try`/`except` as normal

**Codegen** -- each auto-propagated call emits:

```cpp
auto __tmp = parse_atom();
if (!__tmp.has_value()) return tpy::make_unexpected(__tmp.error());
Expr left = *__tmp;
```

This gives the ergonomics of `throw` (exit from deeply nested calls) with the performance of `std::expected` (no stack unwinding), while keeping every function's error contract explicit in its signature.

### `except ReturnException` Catch-All (E5)

A `try`/`except ReturnException` block catches any return exception, regardless of concrete type:

```python
def main() -> None:
    try:
        expr = parse_expr()     # @error_return(ParseError)
        value = lookup(expr)    # @error_return(NotFound)
    except ReturnException:
        print("something failed")
```

Codegen -- every `@error_return` call in the try block gets the same goto target:

```cpp
auto __tmp1 = parse_expr();
if (!__tmp1.has_value()) goto __except_1;
Expr expr = *__tmp1;
auto __tmp2 = lookup(expr);
if (!__tmp2.has_value()) goto __except_1;
Value value = *__tmp2;
goto __after_try_1;
__except_1:;
std::cout << "something failed" << "\n";
__after_try_1:;
```

No type matching needed -- the `has_value()` check is type-independent.

`except ReturnException as e` is not supported initially (would require a type-erased wrapper or variant across different error types).

---

## Tier 2: Throw Exceptions (C++ Exceptions) (E7, E8)

For genuine errors -- I/O failures, invalid arguments, runtime violations -- TurboPython uses C++ exception machinery.

### Exception Hierarchy

Non-`ReturnException` exceptions map to C++ classes that inherit from `std::exception`:

```python
# Built-in (in tpy._builtins._exceptions)
class BaseException: ...
class Exception(BaseException): ...
class ValueError(Exception): ...
class TypeError(Exception): ...
class RuntimeError(Exception): ...
class IndexError(Exception): ...
class KeyError(Exception): ...
class IOError(Exception): ...
class FileNotFoundError(IOError): ...
```

C++ mapping:

```cpp
namespace tpy {
struct BaseException : std::exception {
    std::string message;
    const char* what() const noexcept override { return message.c_str(); }
};
struct Exception : BaseException {};
struct ValueError : Exception {};
struct TypeError : Exception {};
// ...
}
```

### `raise` Statement

In a regular (non-`@error_return`) function, `raise` compiles to C++ `throw`:

```python
def parse_int(s: str) -> Int32:
    if not s.isdigit():
        raise ValueError("invalid integer")
    return Int32(s)
```

```cpp
int32_t parse_int(std::string_view s) {
    if (!is_digit(s)) {
        throw tpy::ValueError{"invalid integer"};
    }
    return static_cast<int32_t>(std::stoi(std::string(s)));
}
```

`raise <expr>` raises a pre-constructed exception variable or function result:

```python
e = ValueError("bad input")
raise e                    # raise variable
raise make_error(42)       # raise function result
raise factory.create()     # raise method result
```

This compiles to `throw <expr>;` in C++. Only throw-tier (non-ReturnException) exceptions are supported -- return-tier exceptions must use the direct `raise E(args)` form.

### `try`/`except`/`else`/`finally`

Full Python `try` statement maps to C++ `try`/`catch` with RAII cleanup:

```python
def process(path: str) -> None:
    try:
        data = read_file(path)
        result = parse(data)
    except FileNotFoundError:
        print("file not found")
    except ValueError as e:
        print(e.message)
    else:
        save(result)
    finally:
        cleanup()
```

```cpp
void process(std::string_view path) {
    try {
        try {
            auto data = read_file(path);
            auto result = parse(data);
            // else body (only on success)
            save(result);
        } catch (const tpy::FileNotFoundError&) {
            tpy::print("file not found");
        } catch (const tpy::ValueError& __e) {
            tpy::print(__e.message);
        }
    } catch (...) {
        cleanup();  // finally (exception path)
        throw;
    }
    __finally:;
    cleanup();      // finally (normal path)
    // (return/break/continue checks if needed)
}
```

**Supported forms:**

| Form | Status |
|------|--------|
| `try`/`except ExcType` | E7 |
| `try`/`except ExcType as e` | E7 (requires E6 for data fields) |
| Multiple `except` clauses | E8 |
| `except Exception` (catches all subclasses) | E8 |
| Bare `except:` (catch-all) | E8 |
| `try`/`except`/`else` | E7 |
| `try`/`except`/`finally` | E7 |
| `try`/`finally` (no except) | E7 |
| Re-raise (`raise` with no argument) | E8 |

### `finally` Codegen

`finally` uses a duplication pattern: the finally body is emitted twice -- once inside `catch(...)` (exception path, followed by `throw;` for zero-cost re-throw), once at a goto label (normal path). No `std::exception_ptr` allocation, no `__pending` variable:

```cpp
try {
    // try body
    // "return X" becomes: __retval = X; goto __finally;
    // "break" becomes: goto __finally_break;  (separate label with own copy)
} catch (...) {
    cleanup();  // finally body (exception-path copy)
    throw;      // re-throw original exception (zero-cost)
}
__finally:;
cleanup();      // finally body (normal-path copy)
if (__retval) return (*__retval);  // non-void only
```

For throw-tier try/except/finally, an outer try/catch wraps the inner try/catch + handlers to capture exceptions escaping handlers (including re-raises).

Nested try/finally blocks propagate via shared `__retval` (for return) or chained goto labels (for break/continue). Inner `throw;` naturally feeds the outer catch.

### Base Class Catching

`except Exception` catches any exception that inherits from `Exception`, matching Python semantics. This works naturally through C++ inheritance and `catch(const Base&)`:

```python
try:
    risky_operation()
except ValueError:
    print("bad value")      # catches ValueError only
except Exception:
    print("other error")    # catches anything else that inherits Exception
```

```cpp
try {
    risky_operation();
} catch (const tpy::ValueError&) {
    tpy::print("bad value");
} catch (const tpy::Exception&) {
    tpy::print("other error");
}
```

### Re-Raise

`raise` with no argument inside an `except` block re-throws the current exception:

```python
try:
    operation()
except ValueError as e:
    log(e.message)
    raise  # re-throw
```

```cpp
try {
    operation();
} catch (const tpy::ValueError& __e) {
    log(__e.message);
    throw;  // C++ re-throw
}
```

---

## Exception Types with Data (E6)

Exception classes can have fields, enabling `except E as e` to access error details:

```python
class ValueError(Exception):
    message: str

class ParseError(Exception, ReturnException):
    line: Int32
    column: Int32
    detail: str
```

C++ mapping:

```cpp
struct ValueError : Exception {
    std::string message;
};

struct ParseError : Exception {
    int32_t line;
    int32_t column;
    std::string detail;
};
```

`raise` with constructor arguments:

```python
raise ValueError("invalid input")
raise ParseError(10, 5, "unexpected token")
```

```cpp
throw tpy::ValueError{"invalid input"};
return tpy::make_unexpected(ParseError{10, 5, "unexpected token"});  // if @error_return
```

`except ... as e` binding:

```python
try:
    value = parse_positive(s)
except ParseError as e:
    print(f"error at {e.line}:{e.column}: {e.detail}")
```

For throw exceptions, `e` is the caught reference. For return exceptions, `e` is extracted from the `std::expected` error value.

---

## Interaction with Other Features

### `@noalloc`

- `@error_return` is allowed in `@noalloc` functions (zero-cost, no allocation)
- `throw` is forbidden in `@noalloc` functions (stack unwinding may allocate)
- `try`/`catch` (throw-style) is forbidden in `@noalloc` functions
- `try`/`except` for `@error_return` calls is allowed in `@noalloc` (just a value check)

### `with` Statement

Both `with` and `finally` use the same catch-all + duplication codegen pattern for cleanup.

### For-Loops

For-loops remain implicit consumers of `@error_return(StopIteration)` from `__next__`. No change needed -- this is already implemented.

### `next()` Builtin

With auto-propagation (E4), `next()` can be `@error_return(StopIteration)` itself:

```python
@error_return(StopIteration)
def next(it: Iterator[T]) -> T:
    return it.__next__()  # auto-propagates StopIteration
```

Without auto-propagation, `next()` requires explicit `try`/`except` internally.

---

## CPython Compatibility

Both tiers use standard Python syntax:

- `raise ValueError("msg")` -- valid Python, valid TPy
- `try`/`except`/`finally` -- valid Python, valid TPy
- `@error_return(E)` -- no-op decorator stub in `lib/cpy/`
- `ReturnException` protocol -- no-op base in `lib/cpy/`

```python
# lib/cpy/tpy/__init__.py
class ReturnException:
    """No-op in CPython -- ReturnException is a TPy compile-time marker."""
    pass
```

The key difference: in CPython, `ReturnException` exceptions use real `throw`/`catch` (Python exceptions). In TPy, they compile to `std::expected`. Same source, different runtime cost.

---

## Error Messages

| Situation | Error |
|-----------|-------|
| `@error_return(E)` where E is not `ReturnException` | `'ValueError' is not a ReturnException type; @error_return requires a ReturnException exception` |
| `raise E` in `@error_return` function, E is not `ReturnException` | `'raise ValueError' cannot be used in @error_return function; ValueError is not a ReturnException type` |
| `raise E` in regular function, E is `ReturnException` | `'raise StopIteration' can only be used inside an @error_return function; StopIteration is a ReturnException type` |
| Unhandled `@error_return` call | `call to 'parse_expr' may return 'ParseError' which must be handled with try/except` |
| `@error_return(E)` calling `@error_return(F)` without handling | `call to 'lookup' may return 'NotFound' which must be handled (current function returns 'ParseError')` |
| `except ReturnException` on throw-style try | `'except ReturnException' can only catch @error_return calls, not thrown exceptions` |
| `throw` in `@noalloc` function | `'raise ValueError' performs stack unwinding, which is not allowed in @noalloc functions` |
