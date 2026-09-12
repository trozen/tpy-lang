# F-String Design

## Progress

| Feature | Status | Notes |
|---------|--------|-------|
| Regular f-strings -> `std::format` | **Done** | Format string + args validated at compile time, per-type wrapping, `!s`/`!r`, format specs |
| `FStr` compile-time-only type | **Done** | Keeps f-string decomposed (format template + typed parts) instead of lowering to `std::format` |
| Call macro decomposition (`MacroArg.as_fstring()`) | **Done** | `is_static_str` detection for literals, ternaries of literals, and `Final[str]` name references (local or imported) |
| Tuple-based dispatch | **Done** | Heterogeneous `std::tuple` with per-type wrapping to native generic functions |
| Macro context introspection | **Done** | `first_param`, `get_field_type`, `get_method_return_type`, `qualified_name` -- auto-discover logger by name with qualified type check |
| `FStr[wrap_fn]` protocol-based wrapping | **Planned** | Per-type wrapping via overload set, eliminates `@inline` requirement. See design below |
| `StaticStr` type | **Planned** | String literals in FStr context get `const char*` type for zero-cost static storage |

### Future Extensions

| Feature | Notes |
|---------|-------|
| Format specs in `FStr[wrap_fn]` | Does `wrap_fn` see the spec? Async logging wants spec at output time, other use cases may need it earlier |
| `FStr[sql_bind]`, `FStr[html_escape]` | Generic mechanism beyond logging -- SQL parameterization, template auto-escaping |

---

## Regular F-Strings

### Overview

F-strings in TPy compile to `std::format(...)`. The format string and args are
validated at compile time.

```python
name = "world"
x = 42
s = f"hello {name} x={x}"
# -> std::format("hello {} x={}", name, x)
```

### Pipeline

**Parser** (`parse/parser.py`):
- Walks Python's `ast.JoinedStr` nodes
- Creates `TpyFString` with a list of `str | TpyFStringValue` parts
- Validates format specs against C++ `std::format` support
- Rejects Python-only features: `=` alignment, `z` option, `,`/`_` grouping,
  `n`/`%` type codes

**Sema** (`sema/expressions.py`):
- Analyzes each expression part for type correctness
- Validates formattability: expression must be a formattable primitive type
  (`int`, `float`, `bool`, `str`, `char`, `Enum`), a container type, or
  conform to `Stringable`/`Representable` protocol
- Validates conversions: `!s` requires `__str__` or `__repr__`, `!r` requires
  `__repr__`
- Returns `str` type (owned `std::string`)

**Codegen** (`codegen_cpp/expressions.py`):
- Builds the `std::format(fmt, args...)` call
- Per-type wrapping for Python-compatible formatting:
  - `bool` without format spec -> `::tpy::bool_to_str()` (prints `True`/`False`)
  - `bool` with format spec -> `static_cast<int>()` (for `{:d}` etc.)
  - `float`/`float32` without spec -> `::tpy::float_to_str()` (Python-style)
  - `BigInt` without spec -> `.to_string()`
  - `int8`/`uint8` -> `static_cast<int>()` (avoid char interpretation)
  - `Enum` -> `static_cast<int>()`
  - Containers -> runtime `to_str` helpers
  - User types -> `::tpy::__str__()` or `::tpy::__repr__()`
  - `!r` conversion -> `::tpy::__repr__()`
  - `!s` conversion on user types -> `::tpy::__str__()`
- Optimization: pure literal f-strings (`f"hello"`) use `std::string("hello")`
  instead of `std::format`

### Supported format specs

Standard C++ `std::format` specs: `<>^` alignment, `+- ` sign, `#` alternate,
`0` zero-pad, width, `.precision`, type codes (`d`, `x`, `o`, `b`, `f`, `e`,
`g`, `s`, `c`).

Not supported: `=` alignment, `z`, `,`/`_` grouping, `n`/`%` type codes,
format specs on `int` (BigInt).

---

## FStr: F-String Decomposition

### Motivation

Zero-copy async logging: the caller packs typed data into a ring buffer, a
consumer thread formats later. The C++ logger API takes a format string +
variadic typed args:

```cpp
logger.debug("s={} i={}", wrapToLog(some_string), some_int);
```

Regular f-strings lower to `std::format(...)` which eagerly formats into a
`std::string` -- defeating the purpose of async logging. FStr keeps the f-string
decomposed so individual typed args can be packed into the logger buffer without
formatting.

### Current implementation

#### FStr type

`FStr` is a compile-time-only type (`is_compile_time_only() = True`). It has no
C++ representation. When an f-string is passed to an `FStr` parameter, the
compiler keeps it decomposed (format template + individual typed expressions)
instead of lowering to `std::format`.

Resolved via type alias at import time: `_register_compile_time_type_alias`
checks the type factory and registers `FStr -> FStrType` for types where
`is_compile_time_only()` is True.

#### @inline decorator

`@inline` marks a function/method for call-site body inlining. The body must be
a single call expression. At each call site the body is cloned, parameters are
substituted with actual arguments, and the result is analyzed. No C++ function
is generated.

FStr parameters require `@inline` because the f-string literal must reach the
call macro at the call site -- a regular function would only see a parameter
name, not the f-string structure.

Works for both methods and free functions.

#### Call macro decomposition

`MacroArg.as_fstring()` returns `(format_template, [MacroFStringPart])`. Each
part carries the expression AST node, its resolved type, and format spec.
`MacroFStringPart.is_static_str` is True for: string literals, ternaries of
literals (recursively), and name references that resolve to a module-level
`Final[str]` constant -- local or imported. Used to route parts through
zero-alloc wrappers (pointer-only) versus copying wrappers in log-style macros.

F-string validity checks (formattable types, `__str__`/`__repr__`) are skipped
for FStr context (`for_fstr=True`) -- the macro handles per-type dispatch.

#### macro_deps

`macro_deps("module")` binds the module name in the macro namespace for
qualified calls (`module.func()`). Individual function names are not injected
into user scope.

#### Tuple dispatch

The macro packs wrapped args into a heterogeneous tuple. A native generic
function receives it and uses `std::apply` to expand into the logger call.

Single-element tuples use parenthesized init `T(expr)` instead of brace init
`T{expr}` to avoid GCC 14 C++23 ambiguity.

#### Macro context introspection

`CallMacroContext` provides methods for macros to discover the calling
function's context -- the enclosing class (for methods) or function parameters
(for free functions):

- `in_method` (bool) -- True when the call site is inside a method body
- `self_type` (TypeInfo | None) -- the current class, or None
- `first_param` (tuple[str, TypeInfo] | None) -- first parameter of the
  current function (self for methods, first declared param for free functions)
- `self_field(name)` -- returns AST for `self.<name>`
- `get_field_type(type_info, name)` -- TypeInfo for the named field, or None
- `get_method_return_type(type_info, name)` -- TypeInfo for the method's
  return type, or None
- `qualified_name(type_info)` -- module-qualified type name (e.g.
  `"log_infra.LogHandle"`)

This enables single-arg macro calls like `log(f"...")` where the macro
discovers the logger by checking `first_param` for a `_logger` field or
`get_logger()` method -- same code path for both methods and free functions.
The qualified name check ensures the field/method returns the right type, not
just any type with the same short name.

#### Example

Explicit logger (via `@inline` pass-through):

```python
from tpy import FStr, int32, inline
from log_infra import LogHandle
from log_macro import log_debug

class Module:
    _logger: LogHandle

    def __init__(self, name: str) -> None:
        self._logger = LogHandle(name)

    @inline
    def log(self, fs: FStr) -> None:
        log_debug(self._logger, fs)

def main() -> None:
    m = Module("M")
    s = "hello"
    i: int32 = 42
    m.log(f"s={s} i={i}")
```

Generates:
```cpp
::mylog::log_dispatch(m._logger, "s={} i={}",
    std::tuple<::mylog::DeferredStr, int32_t>{::mylog::defer_str(s), i});
```

Auto-discovered logger (via context introspection):

```python
from log_macro import log

class Module:
    _logger: LogHandle

    # In a method: macro finds self._logger by name
    def log_auto(self, tag: str, n: int32) -> None:
        log(f"tag={tag} n={n}")

# In a free function: macro inspects first param's type for _logger
def log_from_free(mod: Module, val: int32) -> None:
    log(f"free={val}")
```

Generates:
```cpp
void Module::log_auto(std::string_view tag, int32_t n) const {
    ::mylog::log_dispatch(this->_logger, "tag={} n={}", ...);
}

void log_from_free(Module& mod, int32_t val) {
    ::mylog::log_dispatch(mod._logger, "free={}", ...);
}
```

### Current limitations

- **@inline body**: single call expression only. Future: multi-statement via
  C++ expression blocks (`({ stmt; stmt; expr; })`)
- **@inline return type**: always void
- **@inline substitution**: positional args, `self.field`, direct names only.
  No kwargs, binary ops, ternaries, subscripts.
- **No definition-time type checking**: errors surface at call sites (like C++
  templates)
- **Field access**: inlined body accesses fields directly at the call site
  (e.g. `m._logger`), bypassing any future access control

---

## Future Design: FStr[wrap_fn]

The long-term direction eliminates `@inline` for the logging use case and moves
per-type wrapping to a protocol-based system.

### Core idea

`FStr[F]` means "decompose this f-string at the call site, apply transform `F`
to each expression." `F` is an overload set dispatched per-type by the compiler.

```python
def log_debug(logger: LogHandle, fs: FStr[log_wrap]) -> None:
    # Regular function, no @inline needed.
    # Compiler splits fs into (fmt, wrapped_tuple) at call site.
    log_dispatch(logger, fs.fmt, fs.args)
```

Call site:
```python
log_debug(self._logger, f"s={s} i={i}")
# Compiler transforms to:
log_debug(self._logger, "s={} i={}", (log_wrap(s), log_wrap(i)))
# Which resolves to:
log_debug(self._logger, "s={} i={}", (defer_str(s), i))
```

### Wrapping overload set

```python
# String wrapping -- deferred copy
def log_wrap(s: str) -> DeferredStr: ...

# Static strings -- pointer only, no copy
def log_wrap(s: StaticStr) -> StaticStr: ...

# Arithmetic types -- passthrough
def log_wrap[T: AnyFixedInt](x: T) -> T: return x
def log_wrap(x: float) -> float: return x
def log_wrap(x: bool) -> bool: return x

# User types opt in via protocol
def log_wrap[T: LogWrappable](x: T) -> T.LogWrapped:
    return x.__log_wrap__()
```

No catch-all -- putting an unwrappable type in the f-string is a compile error.
Forces explicit decisions about how each type enters the log buffer.

### StaticStr

String literals inside FStr context have type `StaticStr` instead of `str`.
The compiler already detects literals, ternaries of literals, and `Final[str]`
name references (`_is_static_str`).
`StaticStr` maps to `const char*` or a pointer wrapper -- static storage
duration, zero cost.

### Generic mechanism

`FStr[F]` is not logging-specific. Other use cases:

- `FStr[sql_bind]` -- SQL parameterization with bind params
- `FStr[html_escape]` -- template rendering with auto-escaping
- `FStr[identity]` -- plain decomposition, no wrapping

### Field access solved

With `FStr[wrap_fn]`, the function receives `(fmt, tuple)` as regular
parameters. No `@inline`, no body inlining, no field access from outside the
class. The decomposition + wrapping happens at the call site; the method body
is a regular function that accesses its own fields normally.

This matches the Rust pattern: the macro (or compiler transformation) runs at
the call site and produces a value; the function is a regular function that
receives and forwards it.

### Open questions

- How does `FStr[F]` interact with the function signature in C++? The `F`
  transform changes the tuple element types, so the C++ signature needs to be
  generic.
- Should `StaticStr` be a general-purpose type (usable outside FStr) or only
  exist within FStr decomposition context?
- How are the wrapping rules associated with the FStr parameter? Type parameter
  (`FStr[log_wrap]`), decorator, or some other mechanism?
- Can `log_wrap` overloads live in a different module from the function that
  uses `FStr[log_wrap]`? How are they resolved?
- Could a context-based logger (e.g. scope-local or passed as first arg) work
  with `FStr[wrap_fn]` to avoid passing the logger explicitly every time?

---

## Design Alternatives Considered

### Avoiding explicit logger argument

Passing `self._logger` on every call is verbose. Alternatives explored:

**Scope-based logger context**: push a logger when entering a scope, macro picks
up the current one:

```python
def run(self) -> None:
    with log_scope(self._logger):
        log_debug(f"s={s} i={i}")      # uses self._logger from stack
        self.process()                  # nested calls also use it

def process(self) -> None:
    log_debug(f"processing...")         # still uses self._logger
```

Would use a thread-local stack of logger pointers. Runtime cost is one
thread-local read per log call -- negligible for async logging. Downside:
thread-local state is implicit and can be surprising.

**Module object argument**: pass the module/object, macro extracts the logger
field by type:

```python
log_debug(self, f"s={s} i={i}")
# macro finds LogHandle field on self, generates:
# log_dispatch(self._logger, "s={} i={}", ...)
```

Works for both methods (`self`) and free functions (`module` parameter). But
still accesses `_logger` from outside the class -- same field access issue as
`@inline`.

**Macro context introspection** (implemented): macro discovers the logger from
the calling context by field name:

```python
log(f"s={s} i={i}")
# macro checks for _logger field or get_logger() method
# on self (methods) or first parameter (free functions)
```

`CallMacroContext.first_param` returns self for methods and the first declared
parameter for free functions. Combined with `get_field_type`, `get_method_return_type`,
and `qualified_name`, the macro uses one code path for both cases.

### Format specs in FStr decomposition

Format specs like `f"{pi:.2f}"` are preserved in the format template as
`"{:.2f}"`. The spec is part of the template string, not the expression.

Open question for `FStr[wrap_fn]`: does `wrap_fn` see the format spec? If
`log_wrap(pi)` wraps a float, and the spec is `:.2f`, the wrapping happens
independently of the spec. The spec is applied later by the consumer (logger
backend). This is the right behavior for async logging (wrapping = buffer
packing, spec = formatting at output time), but other use cases (e.g.
`FStr[html_escape]`) might need the spec earlier.

### Relationship to C++ std::format_args

C++20 `std::make_format_args(args...)` creates a type-erased `format_args`
object that captures references. Similar concept to FStr decomposition but:
- Type-erased (single type, loses per-arg type info)
- Captures references (lifetime tied to scope, not suitable for async)
- No per-type wrapping hook

FStr's typed tuple approach is closer to what async loggers need: each arg
maintains its original type for efficient buffer packing.

### Rust comparison

Rust logging crates (`tracing`, `slog`, `log`) use proc macros at the call
site. There is no method wrapper -- the macro IS the API:

```rust
tracing::info!(count = user_count, "processing {} items", count);
```

The macro generates typed capture code directly. The logger handle is either
global, thread-local, or passed explicitly. Rust chose not to provide
`self.log(...)` syntax because of the same tension: the macro needs call-site
access to args but the logger is behind `self`.

TPy's `FStr[wrap_fn]` future design is similar to Rust's approach -- the
compiler transformation runs at the call site, the function is regular. The
difference is that TPy uses f-string syntax with a wrapping overload set instead
of a custom macro syntax.
