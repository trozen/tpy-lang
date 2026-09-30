# Constructor and Member Initialization Design

## Status

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | Auto-declare fields from `__init__` (infer type from parameter assignment, top-level only; subclasses supported at sema time) | Done |
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
    x: int32
    y: int32
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

    def __init__(self, name: str, age: int32, breed: str):
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
    tag: int32

    def __init__(self, p: Own[Point] | None, tag: int32):
        if p is not None:
            self.tag = tag
        else:
            self.tag = int32(-1)
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
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

# Supported: __init__ only (CPython-compatible, no annotations)
class Point:
    def __init__(self, x: int32, y: int32):
        self.x = x  # Auto-declares field x: int32
        self.y = y  # Auto-declares field y: int32

# Annotations only -- requires @dataclass or explicit __init__ to construct
class Point:
    x: int32
    y: int32
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
    super().__init__(...)    # must come first; warned when skipped and an ancestor defines __init__
    self.x = expr            # init section  -> C++ member initializer list
    self.y = expr            # init section  -> C++ member initializer list
    # <-- split point
    if ...:                  # body section
        ...
    for ...:                 # body section
        ...
```

**Init section** -- the contiguous leading prefix of:
- Parent-initializer calls, `super().__init__(...)` or `Parent.__init__(self, ...)`: first, one per direct base (a single-base child makes one; a multi-base child's per-base calls follow one another). A call placed after any other statement, a second call initializing the same direct base, and a call naming an ancestor that no direct base constructs with that ancestor's `__init__` (one past a direct base defining its own) are compile errors, since C++ constructs each direct base exactly once, ahead of the body. Owed when a class in the parent's ancestry defines `__init__`, an `@native` one such as `Exception` included (`class E(Exception)` calls `super().__init__(...)`, rendered `: ::tpy::Exception(...)`; a return exception makes the same call, which renders as its declared `message` field's init, `: message(...)`, since its struct has no thrown base). Skipping an owed call -- from a hand-written `__init__` or a macro-synthesized one -- is a warned divergence: the C++ base is built by its default constructor (a zero-argument `__init__` CPython never calls, or default-initialized fields CPython leaves unset; for an exception base, an empty message); it stays a compile error when the base has no default constructor (`cpp_default_init` NONE). Builtin container bases are owed nothing (CPython's `__new__` has already built the empty container; the call itself does not lower yet, BUGS.md#builtin-container-base-init-unlowered); protocol bases are not parent classes and are owed nothing. `super().__init__(...)` runs the nearest `__init__` in the parent's MRO, as in CPython
- `self.field = expr` assignments, each field at most once -- to an own field, or to an INHERITED one (the base constructor owns that member, so the write itself runs in the body, but it does not end the section)

A docstring or `pass` anywhere in the prefix runs nothing and is skipped. The init section ends at the first statement that does not match the above, OR when the same field is assigned a second time. Because an inherited-field write does not end the section, a later own-field init that does not read it runs in the member initializer list, BEFORE the write: `self.tag = pick(1); self.a = pick(2)` (`tag` inherited) calls `pick(2)` first, where CPython calls `pick(1)` first (BUGS.md#inherited-field-write-runs-after-member-inits). Own fields are laid out in `__init__` assignment order (see Phase 1), so the C++ member initializer list, which runs in declaration order, runs in source order. Every statement in the init section goes to the C++ member initializer list, unless it is **demoted**: an assignment the member initializer list cannot hold becomes a C++ assignment in the constructor body, after the field was default-constructed. The demotion reasons (one message constant each, `CTOR_DEMOTE_*` in `tpyc/codegen_cpp/emit_prims.py`, chosen by `_member_init_demote_reason` in `tpyc/thir/lower/functions.py` before the source is lowered, except the temporary, which is read off the lowered source):
- a prior statement in the body would run before this initializer (the leading chain is broken);
- the value is a function defined in the body, or references a local defined earlier in the body (parameters, globals and module functions are bound at member-init time);
- the value **binds** a local -- a walrus (`self._r = Resource((n := seed))`): the member initializer list has no scope to declare `n` in;
- the value reads a `self.<field>` written by an earlier inherited-field assignment, or one that has only a class-level default (not in place until the member initializer list has run);
- the value needs a codegen temporary the member initializer list cannot declare (an argument temporary, the once-evaluated left operand of an `and` / `or`, a varargs call).

The member-init itself lowers through the same field-write families as a method's `self.f = src` (`lower_member_init_value`, `tpyc/thir/lower/field_write.py`), over a slot that direct-initializes, so a constructor admits exactly the sources a method admits.

A demoted assignment is only valid when the field's type is default-constructible. When it is not (Decision 3 -- e.g. a `@nocopy` / `__del__` record without a zero-argument constructor), the demotion is a located error naming the reason: ``field '_r' of non-default-constructible type 'Resource' must be initialized before any local variable is bound or any other statement runs in this constructor: the assigned expression binds a local (`:=`)``, followed by the `@staticmethod` factory recipe. CPython runs the walrus shape; it is a known rejection (`BUGS.md#nocopy-field-walrus-init-rejects`), pinned by `tests/cases/records/error_nocopy_del_field_walrus`.

**Body section** -- everything from the split point onwards. Field modifications in the body are valid (e.g., accumulating into a field in a loop); they become C++ assignments in the constructor body.

**At the split point**, sema checks that all declared fields are initialized:

| Field state at split point | Severity |
|---|---|
| Has class-level default value | silent |
| `T()` builds the field's type with no arguments | warning (field will be zero/default-constructed; semantics differ from CPython where the attribute would not exist) |
| Otherwise | error |

"`T()` builds it with no arguments" is the Python-level question (`_is_default_constructible` in `tpyc/sema/protocols.py`, the rule of the `Default` marker): a primitive, a container, an `Optional`, a `Ptr`, a tuple or array of such, or a record whose `__init__` takes no required argument (an aggregate: whose fields all qualify). It is deliberately not codegen's C++ fact `cpp_default_init` (Decision 3): a record whose `__init__` requires arguments keeps a C++ placeholder `X() = default;`, but a value CPython can only build by calling that `__init__` is not left to the placeholder, so such a field -- `@nocopy` or not -- is an error, and so is a union field. `@nocopy` alone does not decide it; `__del__` also removes a zero-argument record's default constructor in the cases `typesys.del_suppresses_default_ctor` lists.

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

Check each field type. If any field is non-default-constructible (e.g., `@nocopy` without default ctor), skip the `= default` line. Default-constructibility is one type fact, `cpp_default_init` in `tpyc/typesys.py` (NONE / USER_INIT / INERT, see open question 4), and the emission is decided by it (`emits_default_ctor`), so "has a default constructor" means exactly "codegen emitted one".

Additionally, the auto `= default;` is suppressed for any record where `~T` would read indeterminate field state if `T()` were callable -- see `del_suppresses_default_ctor` in `tpyc/typesys.py` for the predicate. Three cases trigger suppression:

- `@nocopy + __del__` (author intent: no safe default; e.g. `Box`, `Rc`, `Weak`).
- `__del__` with `__init__` that has any required parameter (the zero-arg overload was a footgun -- callers must use the parameterized form).
- `__del__` with no `__init__` and any own field whose value-init would leave it indeterminate (raw `Ptr[T]`, primitive scalar without an in-class initializer).

Empty-fields `__del__`-only records (the abstract-base pattern) stay default-constructible so derived classes' MIL can value-init the base subobject. The suppression cascades through the field-level default-constructibility predicate: a record holding a field of a suppressed type also loses its auto `= default;`.

### Decision 4: Uninitialized Field Detection

**Question**: Should the compiler warn/error when a field is declared but not initialized in `__init__`?

**Chosen approach: Severity depends on default-constructibility, not on `@nocopy`/`__del__`.**

See the split-point table in Decision 2. The key distinction is whether `T()` builds the field type with no arguments, not whether it is `@nocopy` or has `__del__` (those properties only matter for branch-body assignments):

```python
class Bad:
    x: int32
    y: Handle  # Handle.__init__ requires arguments

    def __init__(self, x: int32):
        self.x = x
        # error: field 'y' has no default constructor and is not initialized

class Warn:
    x: int32
    y: int32   # default-constructible (will be 0 in C++, but absent in CPython)

    def __init__(self, x: int32):
        self.x = x
        # warning: field 'y' not initialized; will be default-constructed in C++

class OK:
    x: int32
    y: int32 = 0   # has class-level default

    def __init__(self, x: int32):
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
    x: int32
    y: int32
    # Auto-generated __init__(self, x: int32, y: int32)
```

This is a separate feature from the core constructor design and will be implemented later. For now, classes without `__init__` require `@dataclass` for constructor synthesis.

Note: classes with only field annotations and no `__init__` cannot be constructed with positional arguments -- the compiler rejects `Point(1, 2)` with an error suggesting `@dataclass` or explicit `__init__`. Zero-argument construction `Point()` works iff every field type (and parent class) is itself default-constructible; otherwise sema rejects with `'Point()' cannot be constructed without arguments: field '...' has no default value`, rather than letting C++ emit a confusing "implicitly deleted" error.

---

## Implementation Details

### Phase 1: Auto-Declare Fields from `__init__` (Done)
- When `self.field = param` appears at top level in `__init__` and `field` is not declared as a class annotation, auto-declare it using the parameter's type. An `Own[T]` param yields a `T` field (a field owns its value inline; `Own[T]` as a field type is redundant), so the param is moved into the field.
- When both annotation and `__init__` assignment exist, the annotation type takes precedence (no duplicate).
- For classes with bases this runs at sema time (after the MRO is resolved), so a `self.f = param` whose name an ancestor already declares reuses the inherited slot (no shadow) and only genuinely new names become own fields. Base-less classes are handled earlier, at parse time.
- C++ struct field order follows `__init__` assignment order (fields not assigned in `__init__` appended at the end), for every record: a base-less one at parse time, one with bases at sema time over its own fields (inherited fields stay in the base). An `@native` record keeps its declared order -- its C++ layout is not ours. This makes the init-list order the source order and avoids `-Wreorder`.
- Future: extend to `self.f = literal` and `self.f = expr` (requires expression type inference at parse/registration time).

### Phase 2: Warnings and Safety (Done)

**Split-point detection** (`_check_init_field_assignments` in `tpyc/sema/analyzer.py`):
- Walks `__init__` body to find the split point: the first statement that is not a parent-initializer call (`super().__init__()` / `Parent.__init__(self, ...)`), a docstring, `pass`, or a first-time `self.field = expr` (own or inherited field). The leading run of trivia and parent-initializer calls is found once (`init_leading_run_end`, over the same docstring / `pass` predicate THIR's constructor lowering uses); the field walk starts where it ends.
- At the split point, checks every own field not assigned at depth 0 anywhere in the body:
  - Not initialized + `T()` needs arguments (`_is_default_constructible` false) -> error
  - Not initialized + `T()` needs none -> warning
  - Has class-level default -> silent
  - Type contains a TypeParamRef in a generic record -> skip (C++ handles at instantiation)
- If `self.method(...)` is called in the init section before all fields are initialized -> warning.

**Branch-body assignment checks**:
- `@nocopy` field assigned inside `if`/`for`/`while` body -> error (move-only type)
- `__del__` field assigned inside `if`/`for`/`while` body -> error (destructor on default-constructed value)
- All other body assignments are silently allowed (e.g., accumulating into a field in a loop).

**`= default` constructor** (`emits_default_ctor` in `tpyc/typesys.py`, read by `tpyc/codegen_cpp/records.py`):
- Generic records (with `type_params`): emit `= default` unless `__del__` suppresses it (`del_suppresses_default_ctor`); C++ handles the field constraint at instantiation, and `cpp_default_init` answers per instantiation.
- Non-generic records: emit `= default` only if all fields and the parent are C++-default-constructible (`cpp_default_init` is not NONE).
- C++-level constructibility differs from Python-level: a user record with required `__init__` params IS C++-constructible if all its own fields are (because it also emits `= default`).

### Phase 3: `@dataclass`
- Implement `@dataclass` decorator that auto-generates `__init__` from class annotations.
- Follow Python's `dataclasses` semantics where practical.

---

## Open Questions

1. ~~**Field ordering with auto-declare**~~: Resolved -- every record's own fields (annotated and auto-declared, with or without bases; `@native` records excepted) are reordered to match `__init__` assignment order. Fields not assigned in `__init__` are appended at the end. This matches the C++ init-list order and avoids `-Wreorder`.

2. **`@noalloc` and auto-declare**: In `@noalloc` mode, `self.x = 42` would infer `int` (BigInt) which is heap-allocated. Should auto-declare be restricted in `@noalloc` to only typed params?

3. ~~**Inherited fields in `__init__`**~~: Resolved -- `self.inherited_field = value` in `__init__` is accepted as part of the init section (same as own fields) without ending it; the base constructor owns the member, so the write runs in the constructor body, and a later own-field init that reads it is demoted there too. One that does not read it stays in the member initializer list and so runs before the write (BUGS.md#inherited-field-write-runs-after-member-inits). An inherited field may be assigned after `super().__init__(args)`, or without it when no class in the parent's ancestry, TPy or `@native`, defines `__init__` (otherwise the call is required -- see "Single class inheritance" in `docs/LANGUAGE_FEATURES.md`).

4. **`= default` for records with required `__init__` params**: current rule, still open (TODO.md "A record gets a C++ default constructor only when Python `T()` is valid" questions whether such a record should have one at all) -- the C++ default constructor is a PLACEHOLDER, not a TPy construction path, and the one fact about it is `cpp_default_init` (`tpyc/typesys.py`): NONE (no default constructor), USER_INIT (it exists but IS the record's user `__init__` -- a zero-argument or all-defaulted one), INERT (it exists and runs no user code). A record like `class Handle: id: int32; def __init__(self, id: int32)` keeps `Handle() = default;` (INERT): sema still rejects user-level `Handle()` through the separate Python-level rule (`_is_default_constructible` in `tpyc/sema/protocols.py`, the `Default` protocol and zero-argument call validation -- see `tpyc/sema/calls.py::_validate_aggregate_zero_arg` for aggregates), while the placeholder serves the C++ positions that need one (`std::array<Handle, N>` slots, a parent's base subobject, a `std::variant`'s first alternative, and a slot declared before its first value). Codegen's `= default` emission, the parent-initializer checks (`TypeRegistry.base_init_duty`, single- and multi-base: a base a child `__init__` skips is rejected when its verdict is NONE; skipping one whose ancestry defines `__init__` warns, naming the USER_INIT run or the INERT default-initialized fields) and the demoted-field check read the same fact. The constructor split-point check intentionally does not: it asks the Python-level question (Decision 2), so a field whose type's `__init__` requires arguments is an error there although the type has the placeholder. A slot declared before its first value is value-initialized by that default constructor, `P p{};` -- a USER_INIT one runs the zero-argument `__init__` once more than CPython, which the documented contract asks to be side-effect free (see "Placeholders run the default constructor" in `docs/LANGUAGE_FEATURES.md`); a `@native` value type's slot is `::X x{};` and its default constructor is asserted beside its declaration (see `docs/NATIVE_INTEROP.md`). A base subobject runs a parent `__init__` CPython would skip only with a warning: a child `__init__` -- hand-written or `@dataclass`-synthesized -- that skips the parent's initializer while a class in the parent's ancestry (TPy or `@native`) defines `__init__` is warned (see "Single class inheritance" and "Multiple Inheritance (D22)" in `docs/LANGUAGE_FEATURES.md`). A USER_INIT default constructor still runs a user `__init__` CPython would not where a field's init moves out of the member-init list into the constructor body (BUGS.md#demoted-field-init-runs-default-init).

5. ~~**Auto-declare + control flow**~~: Resolved -- auto-declare only from top-level statements. Assignments inside control flow require an explicit annotation, otherwise error.
