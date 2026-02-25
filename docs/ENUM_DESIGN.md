# Enum Types

## Progress

| Step | Description | Status |
|------|-------------|--------|
| 1 | EnumType in type system | Done |
| 2 | Parser: detect enum base, parse members, auto() | Done |
| 3 | Sema: register enum, validate members | Done |
| 4 | Sema: member access (Color.Red), .name, .value | Done |
| 5 | Sema: ==/!= between same enum type, reject cross-type | Done |
| 6 | Codegen: enum class, name helper, operator<< | Done |
| 7 | is/is not as ==/!= for enums | Done |
| 8 | Truthiness: all enum values are truthy | Done |
| 9 | Enum as record field, list element, Optional member | Done |
| 10 | Cross-module enum import | Done |
| **Later** | IntEnum (int-compatible, arithmetic, ordering) | Not started |
| **Later** | StrEnum (str-compatible, auto = lowercased name) | Not started |
| **Later** | Flag / IntFlag (bitwise combinable, powers of 2) | Not started |
| **Later** | Iteration (for c in Color) | Not started |
| **Later** | Value lookup (Color(0)) | Not started |
| **Later** | Configurable underlying type | Not started |
| **Later** | Optional[Enum] niche optimization (sentinel value instead of std::optional) | Not started |
| **Later** | match/case exhaustiveness checking | Not started |

---

## Overview

Enums are symbolic constants grouped under a named type. They are one of the most
common Python patterns -- the TPy compiler itself uses 4 enum definitions. Enums
are also a prerequisite for `match`/`case` (simplest target) and a stepping stone
toward full union types / ADTs.

```python
from enum import Enum

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

c: Color = Color.Red
print(c)           # Color.Red
print(c.name)      # Red
print(c.value)     # 0

if c == Color.Green:
    print("green!")
```

Maps to C++ `enum class`:

```cpp
enum class Color : int32_t { Red = 0, Green = 1, Blue = 2 };
```

---

## Design Principles

1. **Match CPython semantics**: `print()`, `.name`, `.value`, comparison rules, `auto()`
   start value (1), truthiness (always true) -- all match CPython behavior exactly.

2. **Type safety**: Different enum types are incompatible. `Color.Red == Direction.North`
   is a compile error, not `False`. Comparing an enum to an integer is also an error
   (unlike `IntEnum`).

3. **Value types**: Enums are small (underlying integer), passed by value, copyable.
   `is_value_type()` returns `True`.

4. **Zero overhead**: Maps directly to C++ `enum class`. No heap allocation, no
   indirection. The generated name/value helpers are `constexpr` or `inline`.

---

## v1: Base `Enum`

### Syntax

Standard Python syntax. Members are `name = literal_int` assignments in the class body.

```python
from enum import Enum, auto

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

class Direction(Enum):
    North = auto()    # 1
    South = auto()    # 2
    East = auto()     # 3
    West = auto()     # 4
```

Rules:
- Base class must be `Enum` (imported from `enum` module)
- Members must have integer literal values or `auto()` calls
- No methods in enum body (v1)
- No duplicate member names
- No duplicate values (aliases are a Python feature we reject for now)
- Negative values allowed (`Error = -1`)
- Empty enums forbidden (at least one member required)

### `auto()`

`auto()` generates sequential values starting at 1, matching CPython:

```python
class Direction(Enum):
    North = auto()    # 1
    South = auto()    # 2
    East = auto()     # 3
    West = auto()     # 4
```

In v1, a single enum must use either all-explicit or all-`auto()` values. Mixed
auto/explicit is deferred to v2 (CPython supports it: `auto()` continues from the
last highest value).

### Member Access

```python
c: Color = Color.Red        # attribute access on the enum type
```

`Color.Red` is a compile-time constant of type `Color`. In codegen it maps to
`Color::Red`.

### `.name` and `.value`

```python
c = Color.Red
print(c.name)      # "Red"   -- str
print(c.value)     # 0       -- Int32 (underlying type)
```

`.name` returns a `str` (compile-time known string literal).
`.value` returns the underlying integer type (default `Int32`).

### Printing

Match CPython's `str()` format:

```python
print(Color.Red)       # Color.Red
print(Color.Red.name)  # Red
print(Color.Red.value) # 0
```

### Comparison

**`==` and `!=`**: Supported between values of the same enum type.

```python
c == Color.Red           # ok
c != Color.Green         # ok
Color.Red == Color.Red   # True
```

Cross-type comparison is a compile error:

```python
Color.Red == Direction.North   # error: cannot compare Color with Direction
Color.Red == 0                 # error: cannot compare Color with int
```

**`is` and `is not`**: Accepted as synonyms for `==`/`!=` on enums. In CPython,
enum members are singletons so `is` works. In C++, enum values are integers so
`==` is the right mapping. Accepting `is` keeps CPython-compatible code working.

**Ordering (`<`, `<=`, `>`, `>=`)**: Compile error, matching CPython's `TypeError`:

```python
Color.Red < Color.Green    # error: ordering not supported for Color
```

### Truthiness

All enum values are truthy, matching CPython. Even `value == 0` is truthy:

```python
class Status(Enum):
    Off = 0
    On = 1

if Status.Off:         # true (all enums are truthy)
    print("always")
```

This differs from `IntEnum` where `bool()` follows integer semantics.

### Function Parameters and Return Types

```python
def describe(c: Color) -> str:
    if c == Color.Red:
        return "red"
    return "other"

def default_color() -> Color:
    return Color.Blue
```

### Record Fields

```python
class Pixel:
    color: Color
    x: Int32
    y: Int32
```

### Optional

```python
def find_color(name: str) -> Color | None:
    if name == "red":
        return Color.Red
    return None
```

---

## Type System

### `EnumType`

New type class in `typesys.py`, following `@dataclass(frozen=True)` convention:

```python
@dataclass(frozen=True)
class EnumType(TpyType):
    name: str                                    # "Color"
    members: tuple[str, ...]                     # ("Red", "Green", "Blue")
    member_values: tuple[tuple[str, int], ...]   # (("Red", 0), ("Green", 1), ("Blue", 2))
    underlying_type: TpyType                     # INT32 (default)
    module_name: str | None                      # for cross-module resolution

    def to_cpp(self) -> str:           # "Color"
    def is_value_type(self) -> bool:   # True
    def qualified_name(self) -> str | None:
        # follows NamedType pattern for cross-module type qualification
        if self.module_name:
            return f"{self.module_name}.{self.name}"
        return None
```

Note: `member_values` uses `tuple[tuple[str, int], ...]` (not `dict`) because
`TpyType` is `frozen=True` and dicts are not hashable. A helper property
`member_value_map` can provide dict-like lookup when needed.

`EnumType` is a value type. It participates in the type system like `Int32` or
`bool` -- passed by value, copyable, comparable with `==`.

`Optional[Color]` correctly uses `std::optional<Color>` (not pointer repr)
because `EnumType.is_value_type()` returns `True`, and `OptionalType.uses_pointer_repr()`
returns `False` for value-type inner types.

`Final[Color]` is out of scope for v1. The `is_constexpr_eligible()` function
in `typesys.py` would need to add `EnumType` to support enum constants, but
this can wait until there is a use case.

### AST Node

New `TpyEnum` AST node in `parse/nodes.py`:

```python
@dataclass
class TpyEnum:
    name: str
    members: list[tuple[str, int]]   # auto() already resolved to int by parser
    loc: SourceLocation | None = None
```

`TpyModule` gets a new `enums: list[TpyEnum]` field (alongside existing `records`,
`functions`, etc.). This keeps enums cleanly separated from records throughout
the pipeline -- no need for `is_enum` guards in record registration or codegen.

Similarly, `ModuleInfo` gets an `enums: dict[str, EnumType]` field for
cross-module import resolution (alongside existing `records`, `protocols`,
`type_aliases`).

---

## Parser

### Module Recognition

Add `"enum"` to `SPECIAL_MODULES` in `parse/parser.py` (alongside `"typing"`,
`"tpy"`, etc.). This enables `_resolve_type_name()` to track names imported
via `from enum import Enum, auto`. Without this, `Enum` and `auto` are
unresolvable -- the same mechanism used for `from typing import Protocol`.

### Detection

In `_parse_class()`, add an `_is_enum_base()` check (parallel to the existing
`_is_protocol_base()`):

```python
def _is_enum_base(self, base: ast.expr) -> bool:
    """Check if a base class expression refers to enum.Enum."""
    if isinstance(base, ast.Name):
        resolved = self._resolve_type_name(base.id)
        return resolved == ("enum", "Enum")
    return False
```

When detected, the class is parsed via a dedicated `_parse_enum()` path instead
of `_parse_class()`, producing a `TpyEnum` node stored in `TpyModule.enums`.

### Body Parsing

Enum body contains `Assign` nodes (not `AnnAssign`):

```python
class Color(Enum):
    Red = 0          # ast.Assign(targets=[Name("Red")], value=Constant(0))
    Green = auto()   # ast.Assign(targets=[Name("Green")], value=Call(Name("auto")))
```

Parser validates:
- Each statement is a simple `name = value` assignment
- Value is an integer literal (positive or negative) or `auto()` call
- No other statements (methods, nested classes, etc. are rejected in v1)
- `Pass` and docstrings are allowed but ignored

### `auto()` Recognition

`auto()` calls are identified by checking that the function name resolves to
`("enum", "auto")` via `_resolve_type_name()` -- the same import-tracking
mechanism used for `Enum` itself. This prevents false positives from a
user-defined function named `auto`.

### `auto()` Resolution

`auto()` is resolved at parse time. The parser tracks the next auto value
(starting at 1, incrementing by 1) and substitutes it:

```python
class Direction(Enum):
    North = auto()    # parser resolves to 1
    South = auto()    # parser resolves to 2
```

In v1, mixing `auto()` and explicit values in the same enum is rejected with:
"Mixed auto() and explicit values are not yet supported; use all auto() or all
explicit values."

---

## Semantic Analysis

### Registration (Phase 1)

In `register_enum()`:

1. Validate no duplicate member names
2. Validate no duplicate values
3. Create `EnumType` with members and values
4. Register in `TypeRegistry` and bind in namespace
5. Register each member as a module-level constant (for `Color.Red` access)

### Member Access (Phase 2)

`Color.Red` is an attribute access where the target is a type, not a value.
This is a **special case in the expression analyzer** -- it does not go through
`RecordInfo` or the method lookup infrastructure. Instead:

1. `Color` -> look up in namespace, find `EnumType`
2. `.Red` -> look up in `EnumType.member_values`, find value 0
3. Result type: `Color` (the `EnumType`)

This is similar to how static methods or class-level constants work. The
expression analyzer needs a new branch for `EnumType` in attribute access
resolution (before falling through to `RecordInfo` / method lookup).

### `.name` and `.value`

When the target expression has an `EnumType` (i.e., an enum value, not the
enum type itself):

- `.name` -> result type is `str`
- `.value` -> result type is the enum's underlying type (default `Int32`)

These are also special-cased in the expression analyzer -- not synthesized
methods on a `RecordInfo`. Other attribute access on enum values is an error.

### `is` / `is not` Lowering

The `is` operator on enum types is lowered to `==` in `sema/operators.py`
(not in codegen). When the operator analyzer sees `is` or `is not` with both
operands of the same `EnumType`, it rewrites to `==` / `!=` and proceeds with
normal comparison logic. This keeps codegen simple -- it never sees `is` on enums.

### Comparison Type Checking

In the operator analyzer:

- `enum == enum` (same type): allowed, result `bool`
- `enum != enum` (same type): allowed, result `bool`
- `enum == other_enum`: error
- `enum == int`: error
- `enum < enum`: error (no ordering on base `Enum`)
- `enum is enum` (same type): allowed, maps to `==`

---

## Code Generation

### Enum Declaration (Header)

For `class Color(Enum): Red = 0; Green = 1; Blue = 2`:

```cpp
// --- enum class ---
enum class Color : int32_t {
    Red = 0,
    Green = 1,
    Blue = 2,
};

// --- name accessor ---
inline std::string_view __tpy_enum_name(Color __e) {
    switch (__e) {
        case Color::Red: return "Red";
        case Color::Green: return "Green";
        case Color::Blue: return "Blue";
        default: tpy_panic("invalid enum value");
    }
}

// --- print support (matches CPython: "Color.Red") ---
inline std::ostream& operator<<(std::ostream& __os, Color __e) {
    return __os << "Color." << __tpy_enum_name(__e);
}
```

The `default: tpy_panic(...)` in the name switch matches the runtime's pattern
for invalid states. In normal usage it is unreachable (enum values only come
from named members), but guards against invalid `static_cast` from C++ interop.

No `__tpy_enum_value` helper is generated -- `.value` codegen emits
`static_cast<int32_t>(c)` directly, avoiding unnecessary wrapper functions.

All helpers use a `__tpy_` prefix to avoid name collisions. They go in the
header alongside the enum declaration.

### Member Access Codegen

| TPy | C++ |
|-----|-----|
| `Color.Red` | `Color::Red` |
| `c.name` | `__tpy_enum_name(c)` |
| `c.value` | `static_cast<int32_t>(c)` |
| `c == Color.Red` | `c == Color::Red` |
| `print(c)` | `std::cout << c` (uses `operator<<`) |

### Truthiness Codegen

All base `Enum` values are truthy (including `value == 0`), matching CPython.
In CPython, `Enum.__bool__` is inherited from `object` and always returns `True`.

The `gen_truthy_expr` function in `codegen_cpp/expressions.py` gets a new branch
for `EnumType` that emits `true`:

```cpp
// if c:  where c: Color
if (true) {  // enum values are always truthy
```

Since enum variables have no side effects in boolean context (just a name or
member access), emitting `true` without evaluating the expression is safe.
The `not` operator on enums similarly emits `false`.

---

## Cross-Module Support

Enums are declared in the header, so cross-module usage works the same as records:

```python
# colors.py
class Color(Enum):
    Red = 0
    Green = 1

# main.py
from colors import Color
c: Color = Color.Red
```

The import resolution in `sema/analyzer.py` checks `module_info.enums` (the new
category) to find the `EnumType`. The codegen qualifies imported enum types using
`qualified_name()` (following the `NamedType` pattern), producing
`tpy_user::colors::Color` in C++.

Member access (`Color.Red`) resolves through the imported `EnumType` -- the
expression analyzer's enum branch handles it the same way as local enums.

---

## Future: `IntEnum`

`IntEnum` members behave as integers -- they support arithmetic, ordering, and
comparison with plain `int` values.

```python
from enum import IntEnum

class Priority(IntEnum):
    Low = 1
    Medium = 2
    High = 3

Priority.Low == 1          # True (unlike Enum)
Priority.Low + 10          # 11
Priority.Low < Priority.High   # True
print(Priority.Low)        # 1 (not "Priority.Low")
```

### C++ Mapping

`IntEnum` maps to the same `enum class` but with implicit conversions:

```cpp
enum class Priority : int32_t { Low = 1, Medium = 2, High = 3 };

// IntEnum: implicit conversion to underlying type
inline int32_t operator+(Priority a, int32_t b) {
    return static_cast<int32_t>(a) + b;
}
// ... similar for -, *, /, <, <=, >, >=, ==, != with int
```

Or alternatively, use a plain `enum` (not `enum class`) to get implicit int
conversion -- but this loses scoped naming. The better approach is `enum class`
with generated operator overloads.

### Print Format

`IntEnum` prints as the integer value (`1`), not the qualified name (`Priority.Low`).
The `operator<<` overload emits the value instead of the name.

---

## Future: `StrEnum`

`StrEnum` members behave as strings. `auto()` produces the lowercased member name.

```python
from enum import StrEnum, auto

class Mode(StrEnum):
    Read = auto()     # "read"
    Write = auto()    # "write"
    Append = "a"      # explicit string

Mode.Read == "read"   # True
print(Mode.Read)      # read
```

### C++ Mapping

`StrEnum` cannot map to `enum class` (no string underlying type). Options:
- Map to `const char*` constants with a wrapper struct for type safety
- Map to a struct with `static constexpr` members

This depends on TPy's string model (`str` vs `DynStr`) and is deferred until
that is resolved.

---

## Future: `Flag` / `IntFlag`

`Flag` members are powers of 2, combinable with bitwise operators.

```python
from enum import Flag, auto

class Permission(Flag):
    Read = auto()      # 1
    Write = auto()     # 2
    Execute = auto()   # 4

rw = Permission.Read | Permission.Write   # combined flag, value 3
if Permission.Read in rw:
    print("readable")
```

### C++ Mapping

Maps to `enum class` with overloaded `|`, `&`, `~` operators:

```cpp
enum class Permission : uint32_t { Read = 1, Write = 2, Execute = 4 };

inline Permission operator|(Permission a, Permission b) {
    return static_cast<Permission>(
        static_cast<uint32_t>(a) | static_cast<uint32_t>(b));
}
// ... similar for &, ~, ^
```

`auto()` in `Flag` context produces powers of 2 (1, 2, 4, 8, ...) instead of
sequential values. This is a different `_generate_next_value_` strategy.

---

## Future: Iteration

```python
for c in Color:
    print(c)
```

Requires generating a constexpr array of all members and integrating with the
iterator protocol:

```cpp
inline constexpr std::array<Color, 3> __tpy_Color_members = {
    Color::Red, Color::Green, Color::Blue,
};
```

The `for c in Color` loop iterates over this array. This needs the `NativeIterable`
codegen path since the array has `begin()`/`end()`.

---

## Future: Configurable Underlying Type

Default underlying type is `Int32`. For enums with many members or specific C
interop needs, users may want to control the underlying type.

Possible syntax (to be decided):

```python
# Option A: generic parameter (cleanest)
class SmallColor(Enum[Int8]):
    Red = 0
    Green = 1

# Option B: class variable
class SmallColor(Enum):
    _underlying_ = Int8
    Red = 0
    Green = 1
```

For now, all enums use `Int32`. The underlying type is stored in `EnumType` so
the codegen is ready for configurability when the syntax is decided.

---

## Future: Value Lookup

```python
c = Color(0)    # Color.Red
c = Color(99)   # ValueError (needs exception support)
```

Deferred because it requires either exception support or a different error
handling strategy (panic, Optional return).

---

## Dependencies

**Blocking nothing** -- enums are a standalone feature.

**Blocked by nothing** -- no unimplemented features are required.

**Unlocks**:
- `match`/`case` with exhaustiveness checking (enums are the simplest target)
- Union type tags (enums as discriminators)
- Many real-world patterns (state machines, options, configuration)
- Self-hosting (4 enum definitions in the compiler source)

---

## Future: Optional[Enum] Niche Optimization

Currently `Optional[Color]` maps to `std::optional<Color>`, which adds a `bool`
flag (typically doubling the size from 4 to 8 bytes due to alignment). Since enum
types have a finite set of valid values, the compiler can use an unused underlying
value as a sentinel for `None`, eliminating the extra storage.

For example, if `Color` has members `{0, 1, 2}`, the compiler could use `-1`
(or `INT32_MIN`, or `max_value + 1`) as the `None` sentinel, making
`Optional[Color]` the same size as `Color`:

```cpp
// Before (std::optional):
std::optional<Color> c;  // 8 bytes (4 value + 1 bool + 3 padding)

// After (niche optimization):
Color c;                 // 4 bytes, sentinel value means None
```

This is similar to Rust's niche optimization for `Option<NonZero*>` types.

Implementation considerations:
- Requires generating a sentinel value per enum type at compile time
- Needs custom `has_value()` / `value()` accessors (or a wrapper type)
- The sentinel must not collide with any member value -- pick a value outside
  the member range (e.g., `min_value - 1` or `max_value + 1`, clamped to the
  underlying type's range)
- Falls back to `std::optional` if all underlying values are used (unlikely
  for typical enums with <100 members on Int32)

---

## C++ Value Type Trait

The C++ runtime's `is_value_type` trait (`runtime/cpp/include/tpy/type_traits.hpp`)
has no specialization for user-defined `enum class` types. For v1 this is
acceptable -- enums don't participate in generic containers that dispatch on
`is_value_type`. If enums are later used as generic type arguments (e.g.,
`list[Color]` with template-based container ops), a trait specialization will
need to be generated per enum type.

---

## Test Plan

### CPython Compatibility

Enum tests should be CPython-compatible (no `no_cpython.txt` needed). CPython's
`str(Color.Red)` returns `"Color.Red"` -- the same format TPy generates. The
`.name` and `.value` outputs also match exactly. Only `repr()` differs
(`<Color.Red: 1>` in CPython) but TPy does not implement `repr()`.

### Happy Path

| Test | What it checks |
|------|---------------|
| `basic_enum` | Define enum, assign member, print |
| `enum_compare` | `==`, `!=` between same-type members |
| `enum_name_value` | `.name` and `.value` access |
| `enum_auto` | `auto()` values starting at 1 |
| `enum_negative` | Negative integer values |
| `enum_param_return` | Enum as function parameter and return type |
| `enum_field` | Enum as record field |
| `enum_optional` | `Color \| None` optional enum |
| `enum_cross_module` | Import enum from another module |
| `enum_is` | `is` / `is not` comparison |
| `enum_if_chain` | if/elif chain on enum values |
| `enum_truthiness` | All enum values (including 0) are truthy |
| `enum_not` | `not` operator on enum values |
| `enum_list` | Enum values in a list |
| `enum_cross_module_annotated` | Cross-module enum with explicit type annotations |

### Error Cases

| Test | Expected error |
|------|---------------|
| `error_enum_cross_compare` | Cannot compare Color with Direction |
| `error_enum_int_compare` | Cannot compare Color with int |
| `error_enum_ordering` | Ordering not supported for enums |
| `error_enum_dup_name` | Duplicate enum member name |
| `error_enum_dup_value` | Duplicate enum value |
| `error_enum_no_members` | Empty enum |
| `error_enum_bad_value` | Non-integer member value |
| `error_enum_mixed_auto` | Mixed auto() and explicit values (v1) |
