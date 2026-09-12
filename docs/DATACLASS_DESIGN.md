# Dataclass Decorator

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Core `@dataclass`: auto `__init__` from annotations, field defaults, ordering validation | Done |
| 2 | Auto `__eq__` (field-by-field comparison, `operator==`/`operator!=`) | Done |
| 3 | `frozen=True` (immutable instances -- all fields readonly) | Done |
| 4 | Auto `__hash__` for frozen dataclasses | Done |

### Future Extensions

| Feature | Notes |
|---------|-------|
| `__repr__` auto-generation | Done. Generates `__repr__` returning `TypeName(field=value, ...)` format; `operator<<` delegates to it |
| `__post_init__` | Done. Synthesized `__init__` calls `self.__post_init__()` after setting fields. No `InitVar` args yet; a child overriding a parent's hook double-calls (BUGS.md) |
| `field(default_factory=...)` | Done. `field(default=X)` and `field(default_factory=X)` where X is a Default-constructible type |
| `field(kw_only=True)` | 2 uses in compiler source. Low priority |
| `order=True` | Done. `@dataclass(order=True)` generates `operator<=>` via `std::tie` for lexicographic field comparison |
| Dataclass inheritance | Done. Child `@dataclass` inherits parent fields into `__init__`, `__eq__`, `__hash__`, and `order` |
| `slots=True` | No-op in TPy (all records already use fixed struct layout) |

---

## Overview

`@dataclass` auto-generates boilerplate methods (`__init__`, `__eq__`, `__hash__`) from
class field annotations. It is the most common class decorator in Python -- the TPy
compiler source itself uses 53 dataclass definitions (23 frozen, 30 mutable).

```python
from dataclasses import dataclass
from tpy import int32

@dataclass
class Point:
    x: int32
    y: int32

p = Point(1, 2)
print(p)              # Point(x=1, y=2)
print(p == Point(1, 2))  # True
```

Maps to a regular C++ struct with a parameterized constructor and `operator==`:

```cpp
struct Point {
    int32_t x;
    int32_t y;
    explicit Point(int32_t x, int32_t y) : x(x), y(y) {}
    friend bool operator==(const Point& lhs, const Point& rhs) {
        return lhs.x == rhs.x && lhs.y == rhs.y;
    }
    friend bool operator!=(const Point& lhs, const Point& rhs) {
        return !(lhs == rhs);
    }
};
```

---

## Design Principles

1. **Python-compatible import**: `from dataclasses import dataclass`. The `dataclasses`
   module is recognized by the compiler as a built-in (like `enum`, `typing`). CPython
   tests work without stubs since `dataclasses` is in Python's stdlib.

2. **Synthesized methods are normal methods**: The compiler creates synthetic `TpyFunction`
   nodes during registration. From sema and codegen's perspective, they are indistinguishable
   from user-written methods. This minimizes special-casing.

3. **Explicit over implicit**: `@dataclass` is opt-in. A class with only field annotations
   and no `__init__` cannot be constructed with positional arguments -- use `@dataclass`
   or define `__init__`. Only `@dataclass` triggers method synthesis.

4. **User methods win**: If a `@dataclass` class has a user-defined `__init__`, `__eq__`,
   or `__hash__`, the user's version takes precedence -- no synthesis for that method.

---

## Phase 1: Core `@dataclass` -- Auto `__init__`

The minimal viable feature: `@dataclass` generates `__init__` from field annotations.

### Syntax

```python
from dataclasses import dataclass
from tpy import int32

@dataclass
class Point:
    x: int32
    y: int32

# Equivalent to writing:
class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y
```

### Field Defaults

Fields can have default values. Fields with defaults must come after fields without
(same rule as Python, same rule as function parameters):

```python
@dataclass
class Config:
    name: str
    value: int32 = 0
    enabled: bool = True

c1 = Config("test")           # value=0, enabled=True
c2 = Config("test", 42)       # enabled=True
c3 = Config("test", 42, False)
```

Error case:

```python
@dataclass
class Bad:
    x: int32 = 0
    y: int32       # error: field without default after field with default
```

### Validation Rules

1. **Must have field annotations**: `@dataclass` on a class with no annotations is an error.
2. **No explicit `__init__`**: If `@dataclass` class has user-defined `__init__`, warn
   (the decorator is redundant for init generation -- user's `__init__` wins).
3. **Field ordering**: Fields without defaults before fields with defaults.
4. **Compatible types**: Field default expressions must be compatible with the field type
   (checked by sema when analyzing the synthesized `__init__`).

### Implementation Strategy

**Note**: The original design used `is_dataclass`/`is_ordered` flags on
`TpyRecord`. These have been replaced by the macro system: macros call
`set_match_args()` and generate methods directly. Only `is_frozen` remains
as a compiler-enforced flag.

**Parser** (`tpyc/parse/parser.py`):
- Recognize `@dataclass` decorator (resolved via `_resolve_decorator()`).

**Registration** (`tpyc/sema/registration.py`):
- Macros generate `__init__` via `build_init()` and register fields via `set_match_args()`.
- The synthesized `__init__` is identical to what a user would write -- existing
  init-list extraction in codegen handles it automatically.

**Module resolver** (`tpyc/modules/`):
- Register `dataclasses` as a known built-in module that exports `dataclass`.
- The decorator itself is a compile-time directive -- no runtime code needed.

**Codegen** (`tpyc/codegen_cpp/records.py`):
- No changes needed. The synthesized `__init__` flows through existing codegen
  (init-list extraction, parameter mapping, etc.).

### CPython Compatibility

`from dataclasses import dataclass` is Python's stdlib -- CPython tests work without
any stubs. The generated `__init__` matches Python's `dataclass` semantics for the
features we support.

---

## Phase 2: Auto `__eq__`

Python's `@dataclass` generates `__eq__` by default (comparing all fields).

### Behavior

```python
@dataclass
class Point:
    x: int32
    y: int32

p1 = Point(1, 2)
p2 = Point(1, 2)
p3 = Point(3, 4)

print(p1 == p2)  # True
print(p1 == p3)  # False
print(p1 != p3)  # True
```

### Implementation

Synthesize a `__eq__` method that compares all fields:

```python
# Synthesized (conceptual):
def __eq__(self, other: Point) -> bool:
    return self.x == other.x and self.y == other.y
```

The existing `_gen_binary_operators` in codegen already maps `__eq__` to `operator==`
and `__ne__` to `operator!=`. So synthesizing the sema-level `__eq__` method is
sufficient -- codegen generates the C++ operator overloads automatically.

**Skip if**: User defines their own `__eq__`.

**Field type constraint**: All fields must support `==` comparison. Error if a field
type doesn't support equality (e.g., a record without `__eq__`). This is checked
during sema analysis of the synthesized method.

### Edge Cases

- **Zero fields**: `__eq__` returns `True` (all instances are equal). Valid Python behavior.
- **Reference-type fields**: Comparison uses the field's `operator==`, which for records
  compares by value (if they have `__eq__`), not by identity.
- **Optional fields**: `std::optional` has built-in `==`, works automatically.

---

## Phase 3: `frozen=True`

Frozen dataclasses are immutable after construction. This is heavily used in the
compiler source (23 frozen dataclasses for type system types).

### Syntax

```python
@dataclass(frozen=True)
class Point:
    x: int32
    y: int32

p = Point(1, 2)
p.x = 3  # error: cannot assign to field of frozen dataclass 'Point'
```

### Implementation

`frozen=True` sets all fields as readonly. In C++ this maps to `const` members:

```cpp
struct Point {
    const int32_t x;
    const int32_t y;
    explicit Point(int32_t x, int32_t y) : x(x), y(y) {}
};
```

**Consequences of const fields**:
- No `= default` constructor (const fields must be initialized).
- No move/copy assignment (const fields can't be reassigned).
- This is correct for frozen dataclasses: they are immutable value types.

**Parser**: Accept `@dataclass(frozen=True)` argument. Store as
`is_frozen: bool = False` on `TpyRecord`.

**Sema**: When `is_frozen`, reject any `self.field = ...` outside of `__init__`.
This reuses the `@readonly` infrastructure on methods -- all non-`__init__` methods
are implicitly `@readonly`.

**Alternative approach**: Instead of C++ `const` fields (which prevent move), mark all
methods except `__init__` as `@readonly` and reject field mutation in sema. Fields
stay non-const in C++ but mutation is caught at compile time by the TPy compiler. This
preserves move semantics (important for passing frozen dataclasses by value).

The alternative approach is better -- it matches how `@readonly` already works and
doesn't break move semantics. Frozen dataclasses would be movable but not field-mutable.

---

## Phase 4: Auto `__hash__` for Frozen Dataclasses

Python's `@dataclass(frozen=True)` auto-generates `__hash__` (since the object is
immutable, it's safe to hash). Mutable dataclasses set `__hash__ = None` (unhashable)
by default.

### Behavior

```python
@dataclass(frozen=True)
class Point:
    x: int32
    y: int32

d: dict[Point, str] = {Point(1, 2): "a", Point(3, 4): "b"}
```

### Implementation

Synthesize `__hash__` that combines field hashes:

```python
# Synthesized (conceptual):
def __hash__(self) -> uint64:
    return hash(self.x) ^ (hash(self.y) * uint64(31))
```

Uses a simple hash-combining strategy. The exact algorithm can follow Python's
`tuple.__hash__` approach (which is what CPython's dataclass uses internally).

**Field type constraint**: All fields must be `Hashable`. Error if a field type
doesn't implement `__hash__`.

**Skip if**: User defines their own `__hash__`.

**Mutable dataclasses**: Do NOT generate `__hash__`. If a user wants a hashable mutable
dataclass, they must define `__hash__` manually (same as Python with
`@dataclass(unsafe_hash=True)` -- we don't support that flag initially).

---

## Design Decisions

### D1: Import Path

**Chosen: `from dataclasses import dataclass`**

Python-compatible. CPython tests work without stubs. The `dataclasses` module is
registered as a built-in module in the compiler (like `enum`, `typing`).

Alternative considered: `from tpy import dataclass`. Rejected -- diverges from Python,
no benefit.

### D2: `__repr__` Auto-Generation

**Chosen: Synthesize `__repr__` for `@dataclass` (CPython parity).**

`@dataclass` generates a `__repr__` method returning `TypeName(field=value, ...)`
format via `std::ostringstream`. The existing `operator<<` detects `__repr__` and
delegates to it. User-defined `__repr__` takes precedence (no synthesis).

For dataclass children, `__repr__` includes all fields (parent + own), matching
CPython behavior. Non-dataclass records still use the existing inline `operator<<`
formatting.

### D3: Interaction with Existing Record Features

`@dataclass` composes with other record features:

| Feature | Interaction |
|---------|-------------|
| `@nocopy` | Compatible. Frozen dataclasses could imply `@nocopy` (immutable + no copy = pure value forwarding). Mutable `@dataclass` + `@nocopy` is valid (move-only dataclass). |
| `__del__` | Compatible. User can define `__del__` on a `@dataclass`. Drop flag logic applies normally. |
| Inheritance | Deferred. Phase 1 rejects `@dataclass` on classes with bases (except protocol conformance). |
| Generic type params | Compatible. `@dataclass class Pair[T]: ...` should work -- synthesized `__init__` uses the type params. |
| `@dynamic` protocol | Rejected. `@dataclass` + `@dynamic` makes no sense (protocols have no fields). |

### D4: Frozen vs `@readonly`

**Chosen: Frozen uses sema-level enforcement, not C++ `const` fields.**

`frozen=True` makes all non-`__init__` methods implicitly `@readonly` and rejects
field assignment outside `__init__`. C++ fields stay non-const, preserving move
semantics. This is consistent with how `@readonly` already works in TPy.

### D5: `= default` Constructor for Dataclasses

**Current: `= default` is emitted (shared with all records).**

The existing codegen emits `ClassName() = default;` for all records that have a
parameterized constructor. This applies to dataclasses too. The `= default` constructor
is never callable from TPy code (sema requires the correct number of arguments), but
it exists in the C++ header. Suppressing it specifically for dataclasses would require
changes to the shared record codegen -- deferred to a future cleanup.

---

## Examples

### Basic Dataclass

```python
from dataclasses import dataclass
from tpy import int32

@dataclass
class Point:
    x: int32
    y: int32

def main() -> None:
    p = Point(1, 2)
    print(p)
    print(p.x, p.y)

main()
```

Output:
```
Point(x=1, y=2)
1 2
```

### Defaults

```python
from dataclasses import dataclass
from tpy import int32

@dataclass
class Color:
    r: int32
    g: int32
    b: int32
    a: int32 = 255

def main() -> None:
    red = Color(255, 0, 0)
    print(red)
    semi = Color(255, 0, 0, 128)
    print(semi)

main()
```

Output:
```
Color(r=255, g=0, b=0, a=255)
Color(r=255, g=0, b=0, a=128)
```

### Frozen Dataclass (Phase 3)

```python
from dataclasses import dataclass
from tpy import int32

@dataclass(frozen=True)
class Vec2:
    x: int32
    y: int32

def main() -> None:
    v = Vec2(3, 4)
    print(v)
    # v.x = 5  -- would be a compile error

main()
```

### Equality (Phase 2)

```python
from dataclasses import dataclass
from tpy import int32

@dataclass
class Point:
    x: int32
    y: int32

def main() -> None:
    a = Point(1, 2)
    b = Point(1, 2)
    c = Point(3, 4)
    print(a == b)  # True
    print(a == c)  # False
    print(a != c)  # True

main()
```

---

## Open Questions

1. **Generic dataclasses**: `@dataclass class Pair[T]: first: T; second: T` -- the
   synthesized `__init__` needs generic parameters. Should work naturally since
   `TpyFunction` already supports type params from the enclosing record, but needs
   testing.

2. **Reference-type fields in dataclass**: `@dataclass class Wrapper: item: Point`
   where `Point` is a record. The synthesized `__init__` parameter should use `Own[Point]`
   (taking ownership) matching existing constructor conventions. Or should it follow the
   same context-dependent rules as regular `__init__` params?

3. **Dataclass + Protocol conformance**: A `@dataclass` that also conforms to a
   `Protocol` -- the synthesized methods should satisfy protocol requirements. E.g.,
   a protocol requiring `__eq__` would be satisfied by the auto-generated `__eq__`.
   Needs verification.
