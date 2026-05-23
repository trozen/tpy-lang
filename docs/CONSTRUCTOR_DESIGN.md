# Constructor and Member Initialization Design

## Status

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | Auto-declare fields from `__init__` (infer type from parameter assignment, top-level only, no inheritance) | Done |
| **Phase 2** | Warnings and safety: split-point model, uninitialized field detection, branch-assign errors, conditional `= default` | Done |
| **Phase 3** | `@dataclass` decorator (auto-generate `__init__` from annotations) | Done |

## Problem Statement

TPy's constructor codegen has several related issues that need a unified design:

1. **Initializer list gaps**: When `__init__` has control flow (if/else, loops), field assignments inside branches are not extracted to the C++ member initializer list. Fields are default-constructed then body-assigned, which breaks for non-default-constructible types, `@nocopy` types, and future `const` fields.

2. **Member declaration verbosity**: Currently fields must be declared both as class-level annotations AND assigned in `__init__`. This is redundant for simple cases and diverges from Python conventions where `self.x = x` in `__init__` is sufficient.

3. **No uninitialized field detection**: If a field is declared but never assigned in `__init__`, it silently has undefined value in C++ (UB for primitives).

4. **`= default` constructor emitted unconditionally**: Every class with a parameterized `__init__` also gets `ClassName() = default;`, which fails when fields are non-default-constructible.

## Current Behavior

### Field Declaration

Fields are declared via class-level type annotations:

```python
class Point:
    x: Int32
    y: Int32
```

These generate C++ struct members:

```cpp
struct Point {
    int32_t x;
    int32_t y;
};
```

### Constructor Generation

Simple `self.field = value` assignments at the top level of `__init__` are extracted to the C++ initializer list. Everything else goes in the body.

```python
class Dog(Animal):
    breed: str

    def __init__(self, name: str, age: Int32, breed: str):
        super().__init__(name, age)
        self.breed = breed
```

Generates:

```cpp
explicit Dog(std::string_view name, int32_t age, std::string_view breed)
  : Animal(name, age), breed(breed) {}
```

### The Problem: Control Flow in `__init__`

When `__init__` has any compound statements, field assignments inside those statements are NOT extracted to the initializer list:

```python
class Wrapper:
    tag: Int32

    def __init__(self, p: Own[Point] | None, tag: Int32):
        if p is not None:
            self.tag = tag
        else:
            self.tag = Int32(-1)
```

Generates:

```cpp
Wrapper() = default;
explicit Wrapper(std::optional<Point> p, int32_t tag) {
    if ((p.has_value())) {
        this->tag = tag;
    } else {
        this->tag = -1;
    }
}
```

The `tag` field is default-constructed (to 0 for int32_t), then reassigned. This works for trivial types but would break for:
- `@nocopy` types (deleted copy assignment)
- Non-default-constructible types
- Future `const` fields

---

## Design Decisions

### Decision 1: Auto-Declaring Fields from `__init__`

**Question**: Should `self.field = expr` in `__init__` auto-declare a field if it wasn't declared as a class annotation?

In CPython, class fields are created by `self.field = value` assignments in `__init__` -- class-level annotations are just type hints, they don't create instance attributes. TPy should support this convention for CPython compatibility.

**Chosen approach: Auto-declare supported, explicit annotations recommended.**

The recommended style is to declare fields as class-level annotations (makes struct layout visible at a glance), but auto-declaration from `__init__` is supported for CPython compatibility and convenience.

```python
# Recommended: explicit annotations + __init__
class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

# Supported: __init__ only (CPython-compatible, no annotations)
class Point:
    def __init__(self, x: Int32, y: Int32):
        self.x = x  # Auto-declares field x: Int32
        self.y = y  # Auto-declares field y: Int32

# Annotations only -- requires @dataclass or explicit __init__ to construct
class Point:
    x: Int32
    y: Int32
```

Type inference rules for auto-declared fields:
- `self.f = param` where param has declared type -> use param type **(implemented)**
- `self.f = literal` -> use literal type (warn in `@noalloc` if deducing `int` -> BigInt) **(planned)**
- `self.f = expr` -> use expression's inferred type **(planned)**

When both an annotation and an `__init__` assignment exist, the annotation type takes precedence (the assignment is checked for compatibility).

Auto-declaration only applies to top-level `self.field = expr` statements in `__init__`. Assignments inside control flow (if/else, for, while) do NOT auto-declare fields -- if the field wasn't declared as an annotation, it's an error. This may be relaxed in the future.

### Decision 2: Two-Section `__init__` Model

**Question**: How should we handle `__init__` bodies with control flow?

**Chosen approach: Enforce a two-section structure with a split point.**

`__init__` is divided into two sections at a split point:

```
def __init__(self, ...):
    super().__init__(...)    # optional, must come first
    self.x = expr            # init section  -> C++ member initializer list
    self.y = expr            # init section  -> C++ member initializer list
    # <-- split point
    if ...:                  # body section
        ...
    for ...:                 # body section
        ...
```

**Init section** -- the contiguous leading prefix of:
- At most one `super().__init__(...)` call (must be first if present)
- `self.field = expr` assignments, each field at most once

The init section ends at the first statement that does not match the above, OR when the same field is assigned a second time. Every statement in the init section goes to the C++ member initializer list.

**Body section** -- everything from the split point onwards. Field modifications in the body are valid (e.g., accumulating into a field in a loop); they become C++ assignments in the constructor body.

**At the split point**, sema checks that all declared fields are initialized:

| Field state at split point | Severity |
|---|---|
| Has class-level default value | silent |
| Has a default constructor | warning (field will be zero/default-constructed; semantics differ from CPython where the attribute would not exist) |
| No default constructor | error (C++ compile failure) |

Note: `@nocopy` and `__del__` are orthogonal to default-constructibility. A `@nocopy` type that has a default constructor is just a warning, not an error, for missing initialization.

**Instance method calls** (`self.method(...)`) in the init section before all fields are initialized produce a warning -- the method may access uninitialized fields. `@staticmethod` and free function calls are safe and produce no warning.

**Branch assignments in the body** (field assigned inside `if`/`for`/`while`):

| Field type | Severity |
|---|---|
| `@nocopy` | error -- body assignment requires copy/move assignment, which `@nocopy` deletes |
| Has `__del__` | error -- the default-constructed value's destructor runs on reassignment, causing unintended side effects |
| Other | silently allowed -- e.g., `self.total = 0` in init section then `self.total += x` in a loop body is the normal accumulation pattern |

Ternary expressions (`self.x = a if cond else b`) already go to the init-list as a single expression and are the preferred way to handle conditional initialization without a body assignment.

**Top-level body assignments for fields whose type has a suppressed default ctor:** the field init RHS must not reference a body-local. These types have no default ctor (Decision 3), so the field cannot be default-initialized in the MIL and reassigned; the initializer must itself run in the MIL (move-construction). The MIL-hoist path accepts only constructor parameters and module-level references in the RHS; a local-referencing RHS is rejected with a clean sema error recommending a `@staticmethod` factory returning `Own[Self]` on the field's type to encapsulate the computation.

### Decision 3: `= default` Constructor

**Question**: Should every class with a parameterized `__init__` also get `ClassName() = default;`?

Currently this is emitted unconditionally. It enables `ClassName{}` and `std::optional<T>` but requires all fields to be default-constructible.

**Chosen approach: Only emit when all fields are default-constructible.**

Check each field type. If any field is non-default-constructible (e.g., `@nocopy` without default ctor), skip the `= default` line. Need to track default-constructibility as a type property.

Additionally, the auto `= default;` is suppressed for any record where `~T` would read indeterminate field state if `T()` were callable -- see `del_suppresses_default_ctor` in `tpyc/typesys.py` for the predicate. Three cases trigger suppression:

- `@nocopy + __del__` (author intent: no safe default; e.g. `Box`, `Rc`, `Weak`).
- `__del__` with `__init__` that has any required parameter (the zero-arg overload was a footgun -- callers must use the parameterized form).
- `__del__` with no `__init__` and any own field whose value-init would leave it indeterminate (raw `Ptr[T]`, primitive scalar without an in-class initializer).

Empty-fields `__del__`-only records (the abstract-base pattern) stay default-constructible so derived classes' MIL can value-init the base subobject. The suppression cascades through the field-level default-constructibility predicate: a record holding a field of a suppressed type also loses its auto `= default;`.

### Decision 4: Uninitialized Field Detection

**Question**: Should the compiler warn/error when a field is declared but not initialized in `__init__`?

**Chosen approach: Severity depends on default-constructibility, not on `@nocopy`/`__del__`.**

See the split-point table in Decision 2. The key distinction is whether the field type has a default constructor, not whether it is `@nocopy` or has `__del__` (those properties only matter for branch-body assignments):

```python
class Bad:
    x: Int32
    y: Handle  # Handle has no default ctor

    def __init__(self, x: Int32):
        self.x = x
        # error: field 'y' has no default constructor and is not initialized

class Warn:
    x: Int32
    y: Int32   # default-constructible (will be 0 in C++, but absent in CPython)

    def __init__(self, x: Int32):
        self.x = x
        # warning: field 'y' not initialized; will be default-constructed in C++

class OK:
    x: Int32
    y: Int32 = 0   # has class-level default

    def __init__(self, x: Int32):
        self.x = x
        # OK: 'y' has a class-level default value
```

### Decision 5: `@dataclass` Decorator (future)

Auto-generating `__init__` from class annotations (without an explicit `__init__`) is NOT the right default behavior -- it's not CPython-compatible. In CPython, a class without `__init__` simply doesn't initialize instance attributes from annotations.

Instead, this behavior should be opt-in via `@dataclass`, matching Python's `dataclasses.dataclass`:

```python
from dataclasses import dataclass

@dataclass
class Point:
    x: Int32
    y: Int32
    # Auto-generated __init__(self, x: Int32, y: Int32)
```

This is a separate feature from the core constructor design and will be implemented later. For now, classes without `__init__` require `@dataclass` for constructor synthesis.

Note: classes with only field annotations and no `__init__` cannot be constructed with positional arguments -- the compiler rejects `Point(1, 2)` with an error suggesting `@dataclass` or explicit `__init__`. Zero-argument construction `Point()` works iff every field type (and parent class) is itself default-constructible; otherwise sema rejects with `'Point()' cannot be constructed without arguments: field '...' has no default value`, rather than letting C++ emit a confusing "implicitly deleted" error.

---

## Implementation Details

### Phase 1: Auto-Declare Fields from `__init__` (Done)
- When `self.field = param` appears at top level in `__init__` and `field` is not declared as a class annotation, auto-declare it using the parameter's type.
- When both annotation and `__init__` assignment exist, the annotation type takes precedence (no duplicate).
- Disabled for classes with bases (inheritance needs parent field info to avoid shadowing).
- C++ struct field order follows `__init__` assignment order (fields not assigned in `__init__` appended at the end). This matches the init-list order and avoids `-Wreorder` warnings.
- Future: extend to `self.f = literal` and `self.f = expr` (requires expression type inference at parse/registration time).

### Phase 2: Warnings and Safety (Done)

**Split-point detection** (`_check_init_field_assignments` in `tpyc/sema/analyzer.py`):
- Walks `__init__` body to find the split point: the first statement that is not `super().__init__()`, a docstring, or a first-time `self.field = expr` (own or inherited field).
- At the split point, checks every own field not assigned at depth 0 anywhere in the body:
  - Not initialized + no default ctor -> error
  - Not initialized + has default ctor -> warning
  - Has class-level default -> silent
  - Type contains a TypeParamRef in a generic record -> skip (C++ handles at instantiation)
- If `self.method(...)` is called in the init section before all fields are initialized -> warning.

**Branch-body assignment checks**:
- `@nocopy` field assigned inside `if`/`for`/`while` body -> error (move-only type)
- `__del__` field assigned inside `if`/`for`/`while` body -> error (destructor on default-constructed value)
- All other body assignments are silently allowed (e.g., accumulating into a field in a loop).

**`= default` constructor** (`_all_fields_default_constructible` in `tpyc/codegen_cpp/records.py`):
- Generic records (with `type_params`): always emit `= default` (C++ handles constraint at instantiation).
- Non-generic records: emit `= default` only if all fields and the parent are C++-default-constructible (recursive check via `_fld_type_cpp_default_constructible`).
- C++-level constructibility differs from Python-level: a user record with required `__init__` params IS C++-constructible if all its own fields are (because it also emits `= default`).

### Phase 3: `@dataclass`
- Implement `@dataclass` decorator that auto-generates `__init__` from class annotations.
- Follow Python's `dataclasses` semantics where practical.

---

## Open Questions

1. ~~**Field ordering with auto-declare**~~: Resolved -- all fields (annotated and auto-declared) are reordered to match `__init__` assignment order. Fields not assigned in `__init__` are appended at the end. This matches the C++ init-list order and avoids `-Wreorder`.

2. **`@noalloc` and auto-declare**: In `@noalloc` mode, `self.x = 42` would infer `int` (BigInt) which is heap-allocated. Should auto-declare be restricted in `@noalloc` to only typed params?

3. ~~**Inherited fields in `__init__`**~~: Resolved -- `self.inherited_field = value` in `__init__` is accepted as part of the init section (same as own fields), so it goes into the C++ member initializer list. Both `super().__init__(args)` and direct assignment of inherited fields are valid patterns.

4. **`= default` for records with required `__init__` params**: A record like `class Handle: id: Int32; def __init__(self, id: Int32)` gets `Handle() = default;` emitted because `id: Int32` is C++-default-constructible. This is intentional: sema rejects user-level `Handle()` (the `__init__` requires `id`), but the C++ default ctor must exist so internal codegen paths (`std::array<Handle, N>` slots, parent-record `= default;`, `std::variant` default alternative) compile. For aggregate records (no `__init__`) whose fields aren't all default-constructible, sema instead rejects zero-arg `Point()` with a clean diagnostic (see `tpyc/sema/calls.py::_validate_aggregate_zero_arg`). The remaining "implicitly deleted" path -- a subclass `__init__` that omits `super().__init__(...)` over a parent whose own default ctor is implicitly deleted (because of a `@nocopy`/`__del__` field, etc.) -- is rejected by `tpyc/sema/analyzer.py::_require_super_init_for_non_default_base`, so the cascading C++ error is no longer reachable from user code.

5. ~~**Auto-declare + control flow**~~: Resolved -- auto-declare only from top-level statements. Assignments inside control flow require an explicit annotation, otherwise error.
