# Constructor and Member Initialization Design

## Status

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | Auto-declare fields from `__init__` (infer type from parameter assignment, top-level only, no inheritance) | Done |
| **Phase 2** | Warnings and safety: uninitialized field detection, init-list body-assign warning, conditional `= default` | Todo |
| **Phase 3** | `@dataclass` decorator (auto-generate `__init__` from annotations) | Done |
| **Future** | Improved init-list extraction: ternary rewriting, lambda-in-init-list, cross-branch analysis | Future |

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

# Supported: annotations only (aggregate, no __init__)
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

### Decision 2: Initializer List Strategy for Control Flow

**Question**: How should we handle `__init__` bodies with control flow?

**Chosen approach: Warn and keep current behavior. Extend later.**

Keep the current split: top-level `self.field = value` assignments go to the C++ initializer list, everything else goes in the constructor body. **Done**: sema now emits an error when a field assigned inside control flow has `@nocopy` or `__del__` (including inherited), and a warning for all other field types.

Future extensions could include:
- Ternary rewriting for simple if/else patterns assigning the same field
- Lambda-in-init-list for complex conditional initialization
- Analysis to determine which fields can be extracted even with control flow present

These are not high priority -- the warning makes the current behavior safe, and most constructors don't have complex control flow.

### Decision 3: `= default` Constructor

**Question**: Should every class with a parameterized `__init__` also get `ClassName() = default;`?

Currently this is emitted unconditionally. It enables `ClassName{}` and `std::optional<T>` but requires all fields to be default-constructible.

**Chosen approach: Only emit when all fields are default-constructible.**

Check each field type. If any field is non-default-constructible (e.g., `@nocopy` without default ctor), skip the `= default` line. Need to track default-constructibility as a type property.

### Decision 4: Uninitialized Field Detection

**Question**: Should the compiler warn/error when a field is declared but not initialized in `__init__`?

**Chosen approach: Error for fields without defaults, allow fields with defaults.**

```python
class Bad:
    x: Int32
    y: Int32

    def __init__(self, x: Int32):
        self.x = x
        # Error: field 'y' is not initialized in __init__

class OK:
    x: Int32
    y: Int32 = 0          # has default -- not required in __init__

    def __init__(self, x: Int32):
        self.x = x
        # OK: 'y' has a default value
```

Value-type fields (Int32, float, bool) could have implicit zero-defaults as a pragmatic choice, but record-type fields without defaults must be initialized.

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

This is a separate feature from the core constructor design and will be implemented later. For now, classes without `__init__` remain C++ aggregates (current behavior).

Note: current aggregate behavior (no `__init__` -> struct without constructor) is a pragmatic choice that happens to work with C++ aggregate initialization. It's not CPython-compatible either, but it's useful and doesn't conflict with `@dataclass` -- once `@dataclass` is implemented, plain annotated classes could be tightened to require either `__init__` or `@dataclass`.

---

## Implementation Details

### Phase 1: Auto-Declare Fields from `__init__` (Done)
- When `self.field = param` appears at top level in `__init__` and `field` is not declared as a class annotation, auto-declare it using the parameter's type.
- When both annotation and `__init__` assignment exist, the annotation type takes precedence (no duplicate).
- Disabled for classes with bases (inheritance needs parent field info to avoid shadowing).
- C++ struct field order follows `__init__` assignment order (fields not assigned in `__init__` appended at the end). This matches the init-list order and avoids `-Wreorder` warnings.
- Future: extend to `self.f = literal` and `self.f = expr` (requires expression type inference at parse/registration time).

### Phase 2: Warnings and Safety
- Add a sema pass that checks all declared fields (both annotated and auto-declared) are initialized in `__init__` (or have defaults).
- Warn when a field is body-assigned (not init-list) and its type is `@nocopy` or non-trivially-constructible.
- Only emit `= default` constructor when all fields are default-constructible.

### Phase 3: `@dataclass`
- Implement `@dataclass` decorator that auto-generates `__init__` from class annotations.
- Follow Python's `dataclasses` semantics where practical.

---

## Open Questions

1. ~~**Field ordering with auto-declare**~~: Resolved -- all fields (annotated and auto-declared) are reordered to match `__init__` assignment order. Fields not assigned in `__init__` are appended at the end. This matches the C++ init-list order and avoids `-Wreorder`.

2. **`@noalloc` and auto-declare**: In `@noalloc` mode, `self.x = 42` would infer `int` (BigInt) which is heap-allocated. Should auto-declare be restricted in `@noalloc` to only typed params?

3. **Inherited fields in `__init__`**: Currently `self.inherited_field = value` goes to the constructor body (not init list). Should this be an error pointing users to `super().__init__()` instead?

4. **Default `= default` alternative**: Instead of skipping `= default` for non-default-constructible types, should we generate a "zero-state" default constructor?

5. ~~**Auto-declare + control flow**~~: Resolved -- auto-declare only from top-level statements. Assignments inside control flow require an explicit annotation, otherwise error.
