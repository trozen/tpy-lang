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
| E9 | Polymorphic exception storage via `Box[Throwable]`; pure-TPy exception hierarchy; `raise <expr>` desugar to `__raise__`; slicing-site sema rejection | Partial -- `Box[Throwable]` storage, `raise <expr>` desugar, slicing-site sema rejection, and per-class `raise_X(msg)` runtime helpers all shipped. Pure-TPy hierarchy migration deferred (see [DYNAMIC_PROTOCOL_DESIGN.md](DYNAMIC_PROTOCOL_DESIGN.md) Phase 20 row and [TODO.md](../TODO.md) "Phase 20 follow-up" entry). |

### Runtime exception migration (panic -> catchable throw)

A separate, ongoing track moves runtime panics that correspond to
Python exception types into the throw tier. Modern table-based EH is
zero-cost on the happy path, so well-placed throws cost no more than
panics until they fire (matches `std::vector::at()`).

| Type | Status | Notes |
|------|--------|-------|
| `AttributeError` | Done -- throw-tier | Catchable. Raised by user `__getattr__` / `__setattr__` / `__delattr__` bodies; propagates as a normal C++ throw. `hasattr` and 3-arg `getattr` wrap the dunder call in a try/catch lambda IIFE |
| `AssertionError` | Done -- throw-tier | `assert` failure throws `AssertionError(msg)` via `::tpy::raise_assertion_error(...)` helper |
| `KeyError` | Done -- throw-tier | Dict `__getitem__` / `__delitem__` / `pop`-no-default missing key, set `remove`-missing / `pop`-empty, TypedDict `total=False` field access on absent value all `throw KeyError(...)`. CPython-divergent `str(KeyError(k))` repr-of-key formatting is a v2 follow-up |
| `IndexError` | Done -- throw-tier | List/array/span/string/bytes/bytearray out-of-range indexing and list/bytearray `pop`-empty all `throw IndexError(...)`. Messages match CPython ("list index out of range", "list assignment index out of range" for `__setitem__`/`__delitem__`, "pop index out of range" for indexed pop, etc.) |
| `ZeroDivisionError` | Done -- throw-tier | Catchable. Routed through `::tpy::raise<ZeroDivisionError>(...)` from the constexpr arithmetic helpers (`truediv`/`floordiv`/`fmod` and Float32 variants in `core.hpp`, `div_check`/`mod_check` in `fixed_int.hpp`, `divmod_float`/`divmod_fixed` in `builtins.hpp`, `BigInt::floor_div`/`floor_mod`/`floor_divmod`). Messages match CPython per operation: `float division by zero`, `float floor division by zero`, `float modulo`, `float divmod()`, `integer division or modulo by zero` (for `//` and `divmod`), `integer modulo by zero` (for `%`). One documented divergence: `int / 0` reports `float division by zero` (TPy converts int operands to float before dispatching `/`), where CPython prints `division by zero`; reachable only via the legacy `int`-true-div path covered by `tests/cases/int/panic_int_truediv_zero` |
| `ValueError` panic migrations | Done -- throw-tier | Catchable. Routed through `::tpy::raise<ValueError>(...)`. Migrated runtime sites: `list.remove`/`list.index` missing element; `bytearray.remove` missing value (CPython-aligned `value not found in bytearray`); `bytes()` constructor `negative count` and `bytes must be in range(0, 256)`; `str.split`/`bytes.split` `empty separator`; `str.index`/`str.rindex` `substring not found`; `float()` parse errors; `int()` (BigInt) parse errors; `slice` `step cannot be zero`; extended-slice assignment `attempt to assign sequence of size N to extended slice of size M`; `range()` `arg 3 must not be zero`; `time.sleep` `length must be non-negative`; fixed-int and BigInt `<<` / `>>` `negative shift count`; `open()` `invalid mode: '...'`. Migrated codegen-side panic emitters: range-step-zero check (3 sites in `statements.py` / `expressions.py`); `__len__()` returning a negative value (`__len__() should return >= 0`); enum `from_value` invalid-value (formats the offending value). Enum `from_name` unknown name routes to `KeyError` to match CPython's `EnumMeta.__getitem__`. Also covered: `int(float('nan'))` -> `cannot convert float NaN to integer` (TypeError-shape sister case `int(float('inf'))` lives under OverflowError); `Int*()` parse errors; `from_range` size mismatch (defensive runtime check on container materialization). Out of scope: `pow` negative exponent (TPy-specific limitation: TPy panics where CPython returns float -- proper fix is sema/codegen, not runtime migration). `list.index` "x not in list" still uses literal "x" instead of CPython's `<repr(x)> is not in list` -- v2, requires runtime value-formatting machinery |
| `TypeError` | Done -- throw-tier | Catchable. Routed through `::tpy::raise<TypeError>(...)`. Migrated runtime sites: `ord(s)` and `Char(s)` when `len(s) != 1` (CPython-aligned `expected a character, but string of length N found` formatting); `cast(T, any_val)` when the stored typeid does not match `T` (`Any holds <demangled-actual>, cannot cast to <demangled-expected>`); `hash(any_val)` when the stored type is not Hashable (`unhashable type: '<demangled>'`). User code can `raise TypeError("...")` anywhere |
| `OSError` (file I/O) | Done -- throw-tier | Catchable. Routed through `::tpy::raise<OSError>(...)` from `file.hpp` for nine read-on-write-only / write-on-read-only / flush-on-read-only sites across both text and binary file paths. CPython raises `io.UnsupportedOperation` (a diamond subclass of `OSError + ValueError`); TPy MI does not support diamonds, so we route to `OSError` alone -- `except OSError` catches; `except ValueError` does not (one-sided divergence). Messages keep TPy's longer `read(): file not opened for reading`-style wording (more diagnostic than CPython's terse `not readable`); a future tightening could shorten them once `io.UnsupportedOperation` parity is in scope |
| `NotImplementedError` | Class-only | No runtime panic sites migrated; class added so `raise NotImplementedError("...")` works in user code (catchable as a plain `Exception` subclass) |
| `ArithmeticError` | Done -- class | Base class of `ZeroDivisionError`, `OverflowError`, and `FloatingPointError`, matching CPython's hierarchy. `except ArithmeticError` catches any subtype |
| `LookupError` | Done -- class | Base class of `IndexError` and `KeyError`, matching CPython's hierarchy. `except LookupError` catches either subtype |
| `FloatingPointError`, `RecursionError`, `EOFError`, `PermissionError` | Class-only | Classes exposed for user `raise`; no runtime sites raise them. CPython-parity bases: `ArithmeticError`, `RuntimeError`, `Exception`, `OSError` respectively |
| `OverflowError` (float-to-int) | Done -- throw-tier | Catchable. Routed through `::tpy::raise<OverflowError>(...)` from `bigint.hpp` and `fixed_int.hpp` `from_float` paths when the source is `+inf` or `-inf`. Pairs with `int(float('nan'))` -> `ValueError` (deferred from PR2, also migrated here). Messages match CPython (`cannot convert float infinity to integer`) |
| `OverflowError` (fixed-int arithmetic) | Routed through `raise_fixedint_overflow` (currently panics) | Fixed-width integer overflow (Int8..Int64, UInt8..UInt64) on add/sub/mul/neg/shift/pow/cast/divmod/round and BigInt-to-fixed-int conversion. Unlike CPython (which promotes to unbounded BigInt), TPy panics by default -- but call sites now go through `::tpy::raise_fixedint_overflow(...)` so a future build/module/function-scope policy switch (action=none / panic / throw) can plug in without rewriting them. Helper currently calls `tpy_panic`; behavior unchanged |
| `OverflowError` (BigInt exponent) | Done -- throw-tier | `2 ** huge_value` where `huge_value > 2^64` raises `OverflowError("exponent too large")`. Other BigInt resource limits (OOM, internal invariants) stay panic |
| `RuntimeError` | Class-only | Generic catchall enabling `raise RuntimeError("...")` in user code. No runtime panic sites migrated -- this is a user-facing escape hatch for "this shouldn't happen" runtime conditions |
| `MemoryError` | Class-only | Class exposed for `raise MemoryError("...")` in user code. The single OOM panic site (`bigint.hpp` allocation failure) deliberately stays as `tpy_panic`: catching `MemoryError` is fragile because the handler may itself allocate |
| Internal invariants (uninit slot bookkeeping, "should not happen") | Stays panic | Not Python-exception-shaped |

`@noalloc` does not yet enforce "no may-throw expressions" on hot
paths -- the marker is parsed and threaded through registration but no
sema check rejects raise / try / may-throw expressions today. When that
enforcement lands (separate feature), it will reject any may-throw site
(including `assert`, `d[k]`, `arr[i]`) inside `@noalloc` unless the
compiler can statically prove it's safe (value-range elision, key
narrowing, etc.). Until then, hot paths that need to avoid throw should
use the explicit no-throw APIs (`d.get(k, default)`, in-bounds indexing
proven by value-range, etc.).

### Runtime helper API (`raise<E>` family)

**Pre-E9** (current): runtime sites that surface a Python exception go through
`tpy::raise<E>(...)` -- a function template that does `throw E(...)` directly.
The `raise<E>` template works because every exception class is hand-written
in `core.hpp` so the C++ ctor is visible at template-instantiation time.

**Post-E9**: the exception classes move to TPy code (`lib/tpy/tpy/_builtins/_exceptions.py`),
so the runtime can't `throw E(...)` directly anymore -- the ctors live in
TPy-emitted code, not in the runtime headers. The `raise<E>` template is
replaced by a flat set of per-class forward-declared helpers:

```cpp
// runtime/cpp/include/tpy/throwable.hpp -- declarations only
namespace tpy {
[[noreturn]] void raise_value_error(std::string_view msg);
[[noreturn]] void raise_type_error(std::string_view msg);
[[noreturn]] void raise_index_error(std::string_view msg);
[[noreturn]] void raise_key_error(std::string_view msg);
[[noreturn]] void raise_attribute_error(std::string_view msg);
[[noreturn]] void raise_os_error(std::string_view msg);
[[noreturn]] void raise_zero_division_error(std::string_view msg);
[[noreturn]] void raise_overflow_error(std::string_view msg);
[[noreturn]] void raise_runtime_error(std::string_view msg);
[[noreturn]] void raise_assertion_error(std::string_view msg = "assertion failed");
[[noreturn]] void raise_stop_iteration();
// ... one per built-in exception class the runtime needs to raise
}
```

Definitions live in a TPy stdlib module (`lib/tpy/tpy/_builtins/_raise.py`)
that gets compiled into every program by the existing stdlib-implicit-into-
every-binary machinery. Each is a one-liner -- `def raise_value_error(msg: StrView)
-> Never: raise ValueError(msg)`. The C++ linker resolves the forward
declarations against the TPy-emitted symbols.

The format-string overload is dropped from the runtime surface (callers
build the `std::string` themselves with `std::format` before calling
`raise_X`). Compile-time format-string validation moves to the call site:
where the runtime today writes `raise<TypeError>("{} bad", x)`, post-E9 it
writes `raise_type_error(std::format("{} bad", x))`. One extra std::string
allocation on the slow path -- negligible against the cost of the throw.

Two helpers stay special:

| Helper | Behavior | Defined in |
|--------|----------|------------|
| `raise_assertion_error(msg = "assertion failed")` | Calls into TPy's `AssertionError` ctor + throw | TPy stdlib (forward-declared in `throwable.hpp`) |
| `raise_fixedint_overflow(msg, ...)` | Currently calls `tpy_panic`; designed for a future policy switch (none/panic/throw) on fixed-int arithmetic overflow | `core.hpp` (stays C++; doesn't actually throw today) |

For demangled C++ type names in messages (e.g. `tpy::BigInt` instead of
`N3tpy6BigIntE`), `core.hpp` exposes
`std::string demangle_type_name(const char* mangled)` -- used by the
`Any` cast/hash messages.

### Future Extensions

| Feature | Notes |
|---------|-------|
| Multiple return exception types | `@error_return(E1, E2)` -- `std::expected<T, std::variant<E1, E2>>` |
| Exception chaining | `raise X from Y` -- currently parses with a warning and the cause is dropped (`__cause__`/`__context__` are not modeled); full chaining is low priority, niche use case |
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

The exception hierarchy lives in pure TPy code (`lib/tpy/tpy/_builtins/_exceptions.py`).
The only C++ surface is a thin bridge protocol that inherits `std::exception`:

```python
# lib/tpy/tpy/_builtins/_exceptions.py

@native("tpy::Throwable")
@dynamic
class Throwable(Protocol):
    """The C++ std::exception bridge + the polymorphism contract used by
    Box[Throwable] storage and the `raise <expr>` desugar. Codegen auto-emits
    `clone` and `__raise__` overrides on every implementing class."""
    @readonly
    def clone(self) -> Own[Throwable]: ...
    def __raise__(self) -> Never: ...

class BaseException(Throwable):
    message: str
    def __init__(self, message: str = "") -> None:
        self.message = message
    def __str__(self) -> StrView:
        return self.message

class Exception(BaseException): ...
class ValueError(Exception): ...
class TypeError(Exception): ...
class RuntimeError(Exception): ...
class LookupError(Exception): ...
class IndexError(LookupError): ...
class KeyError(LookupError): ...
class AttributeError(Exception): ...
class AssertionError(Exception): ...
class OSError(Exception): ...
class FileNotFoundError(OSError): ...
# ...
```

The C++ side is the entire native surface for the exception hierarchy:

```cpp
// runtime/cpp/include/tpy/throwable.hpp
namespace tpy {
struct Throwable : std::exception {
    virtual ~Throwable() = default;
    [[noreturn]] virtual void __raise__() const = 0;
    [[nodiscard]] virtual std::unique_ptr<Throwable> clone() const = 0;
};
}
```

`BaseException` and all subclasses are emitted from TPy codegen as classes
inheriting `tpy::Throwable` through normal `class X(Throwable)` codegen.
Codegen also auto-emits three methods on every class transitively implementing
`Throwable`:

- `__raise__()` -- body is `throw *this`. Throws as the override's static class
  (the concrete subclass), preserving dynamic type.
- `clone()` -- body is `std::make_unique<ThisClass>(*this)`. Heap-allocates a
  polymorphic copy at the concrete type.
- `what() const noexcept override` -- body returns `message.c_str()` for
  BaseException-rooted classes (satisfies `std::exception`'s contract).

These are **Throwable-specific compiler support** -- not pure reuse of the
existing `@dynamic` protocol override emission (which only fires when the user
redeclares a method). The auto-emit fires unconditionally for every class in
the Throwable hierarchy, so that user-defined exception classes
(`class MyError(Exception): ...`) get the right behavior without needing to
manually implement `clone` / `__raise__` / `what`. This is a deliberate
exception-ABI helper, named explicitly as such.

Example shape:

```cpp
struct ValueError : Exception {
    // ... TPy-emitted fields, ctors, methods ...
    [[noreturn]] void __raise__() const override { throw *this; }
    [[nodiscard]] std::unique_ptr<Throwable> clone() const override {
        return std::make_unique<ValueError>(*this);
    }
    // what() inherited from BaseException's override (returns message.c_str()).
};
```

### Throwable is the ABI protocol; BaseException is the user extension point (E9)

`Throwable` is the abstract C++ base with the `__raise__` / `clone` / `what`
virtuals -- the ABI boundary that `Box[Throwable]` storage and the `raise`
desugar key on. User-defined exception classes inherit `BaseException` (or a
subclass), never `Throwable` directly. The single direct Throwable implementer
is `BaseException` itself; all other classes in the hierarchy transitively
inherit it.

Sema rule: a concrete class implementing Throwable must inherit BaseException,
*except for the built-in BaseException itself* (which is by definition the root
direct implementer). The rule is structural -- it applies uniformly to stdlib
and user code; there's no "trust the stdlib" loophole. Phrased as a check:
for any concrete class C with `Throwable` transitively in its bases, sema
verifies either `C is BaseException` (the root case) or `BaseException` is in
`C`'s MRO. Otherwise rejected:

> Exception class 'X' implements Throwable but does not inherit BaseException;
> use 'class X(Exception)' or another BaseException subclass as the base.
> Throwable is the ABI protocol; concrete exception classes extend through
> BaseException, which provides `message` and the standard `__str__` /
> `what()` shape.

This is what makes the auto-emit for `what()` safe: every Throwable subclass
has the `message: str` field (inherited from BaseException), so the
`message.c_str()` body is always valid.

### Copy-Constructibility Constraint (E9)

Both `throw *this` and `std::make_unique<ThisClass>(*this)` require the
concrete class to be copy-constructible. C++ throw spec already requires this
for any thrown exception, but TPy's auto-emit extends the constraint to every
Throwable subclass -- including those that might never be thrown directly but
could be stored in `Box[Throwable]` (which uses `clone()`).

Sema check at class registration: any class transitively implementing
`Throwable` must be copy-constructible. Reuses the existing `is_type_nocopy()`
predicate -- every field's type is checked. Diagnostic:

> Exception class 'MyError' has non-copy-constructible field 'handle:
> Box[Resource]'; classes implementing Throwable must be copy-constructible
> (required by auto-emitted `clone()` and `__raise__()`).

### Polymorphic Storage via `Box[Throwable]` (E9)

Because `Throwable` is a `@dynamic` protocol with a virtual `clone`, the existing
`Box[@dynamic Protocol]` machinery (Phase 13 of `DYNAMIC_PROTOCOL_DESIGN.md`)
handles polymorphic exception storage with no special casing:

```python
class TaskState[T]:
    exc: Box[Throwable] | None     # equivalently: Box[BaseException] | None

    def grab(self) -> None:
        try:
            ...
        except BaseException as e:
            self.exc = Box(e.clone())   # explicit clone -> heap copy

    def rethrow(self) -> None:
        if self.exc is not None:
            raise self.exc              # desugars to self.exc.__raise__()
```

Costs are visible at the source level: `e.clone()` is one virtual call + one
heap allocation; `Box(...)` is the owned wrapper; `raise self.exc` is one
virtual call into the override.

Fresh-rvalue construction does not need `clone()` -- the Box-covariant path
heap-allocates the concrete type directly:

```python
self.exc = Box(ValueError("boom"))   # Box<ValueError> -> Box<Throwable>
```

### Slicing-Site Sema Rejection (E9)

The rejection fires on the *conversion shape*, not just the destination type.
Storing a fresh-rvalue of the exact destination type is fine (no slicing
possible -- static and dynamic types coincide); storing a polymorphic borrow
or a concrete subclass is rejected (dynamic type could differ from static
type, leading to silent slicing).

Per-assignment rule:

- **Reject** when:
  - destination is an owned slot of `is_polymorphic_class_type` T (field,
    return, rebind, `Own[T]` arg, container element), AND
  - source is an lvalue / borrow / call result of T or a subclass (catch
    binding, function param, field access, method return, etc.) -- i.e. a
    value whose dynamic type can differ from its static type.
- **Reject** when source is a *concrete subclass* of the destination
  (existing Own[Base] subclass-coercion rule already covers this).
- **Allow** when source is a fresh-rvalue of the exact destination type
  (e.g. `self.exc = BaseException("msg")` where field is `BaseException |
  None`) -- static and dynamic types coincide, no slicing.
- **Allow** `self.x = None` (trivially fine).

Diagnostic on the borrow-storing case:

> Cannot store `BaseException` borrow as owned `BaseException` (the dynamic
> type may be a subclass and would be lost). Use `Box[Throwable]` for owned
> polymorphic storage: `self.exc = Box(e.clone())`.

Diagnostic on the subclass-storing case (existing message preserved):

> Cannot assign `ValueError` to `BaseException` slot (would slice the dynamic
> type). Use `Box[Throwable]` for owned polymorphic storage.

Generalizes to any concrete class that inherits a `@dynamic` protocol -- not
exception-specific. The rule pairs with the broader TODO.md item "Slicing-site
sema rejections for storage / rvalue-return of Optional[Polymorphic]."

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

`raise <expr>` raises a pre-constructed exception variable or function result.
After E9 it lowers via the `__raise__` desugar (parallel to the `__len__` /
`__iter__` dunder-dispatch pattern for `len()` / `iter()`):

```python
e = ValueError("bad input")
raise e                    # peel __deref__ -> Throwable; calls e.__raise__()
raise make_error(42)       # same desugar on the call result
raise self.exc             # Box[Throwable] -- auto-deref then __raise__
```

Lowering rules (single path; no fast-path special case for fresh
construction -- one unified codepath removes the divergence risk between
two forms):

- **`raise <expr>` (any shape, including fresh construction)**: peel
  `__deref__` until you hit a non-Deref type; if that type implements
  `Throwable`, emit `<peeled>.__raise__()` (virtual dispatch, throws the
  dynamic type). For `raise X(args)`, this is `X(args).__raise__()` -- the
  constructed exception's auto-emitted override does `throw *this`, throwing
  as the concrete subclass.
- **Non-Throwable peeled type**: sema rejects with "no `__raise__` method on
  `<type>`; `raise` requires a Throwable expression."

The one-virtual-call overhead vs the previous `throw X(args)` form is invisible
against the cost of a thrown exception. If profiling later shows it matters,
the fresh-construction fast path can be reintroduced as a peephole
optimization (`raise X(args)` -> `throw X(args)`) -- but only after verifying
catch matching, finally interaction, source locations, and debug info are
identical between the two forms.

Only throw-tier (non-ReturnException) exceptions are supported -- return-tier
exceptions must use the direct `raise E(args)` form.

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

`finally` uses a duplication pattern: the finally body is emitted once inside `catch(...)` (exception path, followed by `throw;` for zero-cost re-throw), once on the normal fall-through path, and inline at every `return`/`break`/`continue` site inside the try body. No goto labels, no `std::exception_ptr` allocation, no shared `__pending` variable:

```cpp
try {
    // try body
    // "return X" becomes:
    //     RetCpp __tpy_ret_N = X;   // value captured BEFORE cleanup
    //     cleanup();                // inline finally copy
    //     return __tpy_ret_N;
    // "break"/"continue" emit the inline finally copy, then break/continue.
} catch (...) {
    cleanup();  // finally body (exception-path copy)
    throw;      // re-throw original exception (zero-cost)
}
cleanup();      // finally body (normal fall-through copy)
```

The return expression is evaluated into `__tpy_ret_N` (typed with the function's emitted return type) before the finally bodies run -- Python evaluates the return value first, then `finally`. When the finally body itself returns/raises, the pending return expression is still evaluated (side effects happen) and the captured value is discarded (`[[maybe_unused]]`). Finally-body first bindings are hoisted to function scope by sema, so every emitted copy assigns the same slot and the variable stays visible after the `try`, per Python scoping.

For throw-tier try/except/finally, an outer try/catch wraps the inner try/catch + handlers to capture exceptions escaping handlers (including re-raises).

Nested try/finally blocks compose naturally: an exit-site emission walks the active `FinallyContext` stack from innermost outward, and an inner `throw;` feeds the outer catch.

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
