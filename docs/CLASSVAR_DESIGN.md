# Class-Level Static Fields (`ClassVar` / class-body `Final`) -- Design

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Parser: keep class-body `AnnAssign` items in `TpyRecord.fields` as today; defer the field-vs-class-constant split to sema | Planned |
| 2 | Sema registration: detect `Final[T]` on a class-body field, validate per the rules in [Forms Supported](#forms-supported), route to `RecordInfo.class_constants` (separate dict from `fields`); reject mutation, name conflicts, subclass overrides | Planned |
| 3 | Sema + codegen for `ClassName.MEMBER` access: new branch in `_analyze_field_access`; codegen emits `static constexpr` / `static inline const` members on regular classes, no class-body emission for `@native`, qualified `<cpp_qname>::<member>` at all use sites | Planned |
| 4 | Tests + validation polish (see [Test Plan](#test-plan)) | Planned |

### Out of Scope (for now)

| Feature | Why deferred |
|---------|--------------|
| `ClassVar[T]` recognition (mutable static, and explicit `ClassVar[Final[T]]`) | PEP 591's implicit-`ClassVar` rule means `Final[T] = value` already covers the v1 use case (read-only class constants). Mutable `ClassVar[T] = value` and the explicit `ClassVar[Final[T]] = value` form land together in v2 once we have a story for cross-module mutation safety. Reserving the spelling now (rejecting `ClassVar[...]` with a "not yet supported" error) keeps the future migration clean |
| Instance-final (`Final[T]` with no value, set once in `__init__`) on regular classes | Different feature; needs init-time tracking. PEP 591 reserves this form, so we keep the syntax compatible by only erroring on it for now |
| `ClassVar` in protocols | Protocols already support instance-field declarations; class-side protocol members need separate design |
| Subclass override / shadowing rules | Defer until the inheritance story for instance fields stabilizes; for v1, reject any subclass redeclaration of a parent class constant |
| Reading class constants via instance (`obj.FLAG`) | Python allows this; we restrict to `ClassName.FLAG` for v1 to keep field-access codegen simple. Re-evaluate if real code wants it |
| `ClassName.X` resolution through MRO ancestors | v1 only resolves `X` if it is declared on that exact class. Forces explicit `Parent.X` for inherited constants. Lifts in v2 alongside subclass override rules |
| Generic classes with class constants (`class C[T]: X: Final[T] = ...`) | Type-parameter-dependent class constants need monomorphization rules; reject in v1 |
| Class constants on `@native_c` classes | C structs have no static members; reject |
| `native_field("rename")` on class constants | Per-field rename for class constants is rare; defer. Whole-class rename via `@native("Ns::C")` covers the common case for v1 |

---

## Problem

TPy today has two storage scopes: **instance fields** (anything in a class body) and **module-level globals** (including `Final[T]` constants and `native_global(...)` externs). There is no class-scoped static storage.

This shows up in three places:

1. **`@native` C++ static members.** Binding to `struct X { static constexpr bool FLAG = ...; };` requires `native_global("ns::X::FLAG")` at module scope, which forces the user to repeat `ns::X::` prefix in every binding.
2. **Pure-TPy class constants.** `class HttpClient: TIMEOUT = 30; MAX_RETRIES = 3` is idiomatic Python for grouping constants under a namespace; today the user must hoist them to module level or duplicate them as instance defaults.
3. **Lookup tables on classes.** `class Color: NAMES: ClassVar[list[str]] = ["red", "green", "blue"]` -- shared, read-only data attached to the type, not duplicated per instance.

`@native` is the loudest case (the workaround forces name duplication), but the underlying gap is general.

---

## Forms Supported

PEP 526 (`ClassVar`) and PEP 591 (`Final`) define the canonical Python annotations. We follow them.

| Class-body form | Semantics | C++ emission (regular class) | C++ emission (`@native` class) |
|---|---|---|---|
| `X: T` / `X: T = value` | **Instance field** (unchanged) | non-static member | non-static member |
| `X: Final[T] = value` | Implicit `ClassVar[Final[T]]` per PEP 591 -- class-scoped, immutable | `static constexpr T X = value;` (or `static inline const T X` for non-constexpr-eligible types like `std::tuple` of non-literals) | **error** -- "Final initializer conflicts with C++-owned storage" |
| `X: Final[T]` *(no value)* | Extern class constant | **error** -- "Final without an initializer in a class body is not yet supported on regular classes; use `Final[T] = value`" | bind to C++ `<cpp_qname>::X` (no TPy-side struct emission; use site emits `<cpp_qname>::X`) |
| `X: ClassVar[...]` *(any form)* | Reserved -- v2 | **error** -- "ClassVar at class level is not yet supported; use `Final[T] = value` for class constants" | same error |

PEP 591's "implicit `ClassVar`" rule means `Final[T] = value` in a class body is a class constant, not an instance field. Type checkers (mypy, pyright) already see TPy code that way; matching them avoids surprising the IDE/LLM tooling.

The `Final[T]` (no value) reading on `@native` classes is the one place we deviate from PEP 591's instance-final reading. The deviation is safe because `@native` classes have no TPy-side `__init__` body to set instance finals from, so PEP 591's reading is unreachable; binding to a C++ `static` is the only useful interpretation.

**`ClassVar` is reserved for v2.** PEP 591's implicit-ClassVar rule covers the read-only class-constant use case via `Final[T] = value`, so v1 doesn't need `ClassVar` recognition at all. The mutable `ClassVar[T] = value` form and the explicit `ClassVar[Final[T]] = value` form land together in v2 with a coherent story for cross-module mutation safety.

---

## Design Principles

1. **Match Python type-checker semantics.** Code that works in TPy should pass mypy/pyright with the same meaning. PEP 526 + PEP 591 define what `ClassVar` and `Final` mean; we don't invent new spellings.
2. **Class constants are not instance fields.** Distinct storage in `RecordInfo` (`class_constants: dict[str, FieldInfo]`), distinct codegen path, distinct member access path. Mixing them in the same list invites bugs in field iteration, `__init__` synthesis, and serialization.
3. **`@native` reuses the same syntax surface.** Same `Final[T]` annotation, same `ClassName.X` access -- the `@native` decorator is the only thing that changes the codegen path (extern reference vs. local definition).
4. **No mutation of `Final`.** Reuse the existing `Final` reassignment guard (`statements.py:2362-2367`) -- extend it to class constants. `ClassVar` (without `Final`) is mutable from outside; defer to v2.
5. **Restrict v1 to `ClassName.X` reads.** `obj.X` instance-side reads of class attributes are a Python-native feature but require a fall-through path in `_try_find_field` (after fields, before properties). Restricting to `ClassName.X` for v1 keeps the field-access codegen path narrow.

---

## Compilation Pipeline

### Parser (`tpyc/parse/parser.py`)

The parser does **no** field-vs-class-constant routing. `_parse_class` body iteration (`parser.py:1370-1464`) keeps treating `AnnAssign` items as `FieldInfo` exactly as today. The annotation flows through unchanged as a `TypeRefNode` (parser-emitted) or a resolved `TpyType`; both `Final[T]` (already representable as `FinalType`) and `ClassVar[...]` (a new marker, see below) survive into sema.

The split happens in sema's `register_record`, where `linkage`, the resolved annotation, and the presence/absence of an initializer are all known together. Doing it in the parser would require either inspecting unresolved AST shapes (brittle for aliased imports) or running type resolution in the parser (architectural mixing).

**`ClassVar` representation.** `Final[T]` already has a wrapper type (`FinalType`) and is recognized by `tpyc/parse/type_resolver.py:255`. Add a parallel `ClassVarType` wrapper recognized by the same resolver (gated entry: `"typing:ClassVar"`). For v1 it exists only so we can produce a "ClassVar at class level not yet supported" error and reserve the spelling; no codegen path consumes it.

### Sema (`tpyc/sema/registration.py`, `tpyc/sema/expressions.py`)

**Registration -- the split.** `register_record` (`registration.py:265-340`) inspects each `FieldInfo` whose annotation is `FinalType(...)` or `ClassVarType(...)`:

- `ClassVarType(...)` -> error ("ClassVar at class level is not yet supported"). Reserved for v2.
- `FinalType(T)` with no `default_expr`:
  - on `@native` class: route to class constants (extern binding).
  - on `@native_c` class: error ("`@native_c` classes have no static members in C; use a free `native_global` instead").
  - on regular class: error with hint pointing at `Final[T] = value`.
- `FinalType(T)` with a `default_expr`:
  - on `@native` class: error ("Final initializer conflicts with C++-owned storage").
  - on regular class: route to class constants.

For each routed entry, validate the inner type matches the same allow-list as module-level `Final` (`statements.py:2264-2267`: numeric / `Char` / `StrView` / `bool` / tuple), strip the wrapper, and store on `RecordInfo.class_constants: dict[str, FieldInfo]` (reusing `FieldInfo` -- it already has `name`, `type`, `default_expr`, `native_name`, `loc`; no new dataclass needed). Add `RecordInfo.class_constants_finality: dict[str, bool]` if a future non-Final form needs distinguishing; for v1 every entry is implicitly Final, so this is unnecessary.

Routed entries are removed from `RecordInfo.fields` so downstream passes (instance-field iteration, `__init__` synthesis, `@dataclass`, value-type checks, codegen non-static layout) naturally skip them.

Validation gates:

- Name conflicts: a class constant cannot share its name with an instance field, method, property, or nested type on the same class.
- Subclass override: child class declaring a class constant with the same name as a parent's class constant -> error.
- Generic class with class constant -> error ("class constants on generic classes are not yet supported").
- For pure-TPy class constants, run the same compile-time-constant initializer check that module-level `Final` uses (`statements.py:2453-2476`).

**Member access.** `_analyze_field_access` (`expressions.py:1536`) gets a new branch, ordered after the nested-type / unbound-self checks and before the fall-through `analyze_expr(expr.obj)` that produces today's "is not a variable" error:

```python
if binding and binding.kind in (BindingKind.RECORD, BindingKind.IMPORTED_NAME):
    record_info = self._resolve_record(expr.obj.name, binding)
    if record_info and expr.field in record_info.class_constants:
        field_info = record_info.class_constants[expr.field]
        expr.is_class_constant = True               # codegen flag
        expr.class_constant_owner = record_info     # for cpp_qname lookup
        return field_info.type
```

For v1, lookup is on the directly-named record only -- no MRO walk. `Child.X` finds `X` only if declared on `Child`; users must write `Parent.X` for inherited constants. This keeps the lookup symmetric with the no-override rule.

This eliminates the misleading "is not a variable" error for the natural `ClassName.MEMBER` form.

Reassignment to a class `Final` constant reuses the existing `Cannot reassign Final variable` error path (`statements.py:2362-2367`) -- extend the lookup to also check `record_info.class_constants` when the assignment target is a `TpyFieldAccess`.

### Codegen (`tpyc/codegen_cpp/expressions.py`, `tpyc/codegen_cpp/records.py`)

**Class-body emission** (`records.py`): for each class constant on a non-`@native` record, emit inside the `struct` body:

```cpp
static constexpr <T> <name> = <init>;       // constexpr-eligible inner type
static inline const <T> <name> = <init>;    // non-constexpr-eligible (rare; tuples of non-literals)
```

For `@native` classes: **no class-body emission.** The C++ struct is provided by the user; TPy declares nothing.

The constexpr/inline-const choice falls out of the inner type: numeric / `Char` / `StrView` / `bool` / literal-tuple are all constexpr-eligible in C++23. Non-literal tuple members fall back to `static inline const`. Module-level `Final` already makes the same choice via `final_type_str_to_strview`; reuse the same predicate.

**Use-site emission** (`expressions.py` field-access branch): when `expr.is_class_constant` is set, emit `<cpp_qname>::<member>` where:

- `<cpp_qname>` = `record.native_name` if `is_native`, else `_get_qualified_cpp_name(record)` (existing helper at `typesys.py:60`).
- `<member>` = the field name (no `native_field("...")` rename in v1).

Example: `BuildOpts.FLAG` -> `::x::core::BuildOpts::FLAG`.

---

## C++ Output Examples

### Pure-TPy class constant (`Final` initialized)

```python
class HttpClient:
    TIMEOUT: Final[int] = 30
    DEFAULT_HEADERS: Final[StrView] = "User-Agent: tpy"

    def fetch(self, url: StrView) -> None:
        timeout = HttpClient.TIMEOUT
        ...
```

```cpp
struct HttpClient {
    static constexpr int64_t TIMEOUT = 30;
    static constexpr std::string_view DEFAULT_HEADERS = "User-Agent: tpy";

    void fetch(std::string_view url) {
        auto timeout = HttpClient::TIMEOUT;
        ...
    }
};
```

### `@native` extern class constant (the original bug report)

```python
# build_opts.py
# tpy: native_module
# tpy: cpp_namespace("x::core")
# tpy: include("<x/build_opts.hpp>")
from tpy.extern import native
from typing import Final

@native
class BuildOpts:
    FLAG: Final[bool]
```

```python
# consumer.py
from build_opts import BuildOpts

def f() -> bool:
    return BuildOpts.FLAG
```

C++ for `consumer`:

```cpp
bool f() {
    return ::x::core::BuildOpts::FLAG;
}
```

No declaration of `BuildOpts` is emitted by TPy -- the user's `<x/build_opts.hpp>` provides the struct.

### `ClassVar` mutable (v2, deferred -- shown for direction)

```python
class Counter:
    instances: ClassVar[Int32] = 0
    def __init__(self) -> None:
        Counter.instances += 1
```

```cpp
struct Counter {
    static inline int32_t instances = 0;
    Counter() { Counter::instances += 1; }
};
```

---

## Edge Cases / Validation

| Case | Behavior |
|---|---|
| `X: Final[T] = value` on `@native` class | error -- "Final initializer conflicts with C++-owned storage; use `Final[T]` without value to bind, or remove `@native` if you want TPy to own the constant" |
| `X: Final[T]` on regular class | error -- "Final[T] without an initializer in a class body is not yet supported; use `Final[T] = value` for a class constant, or initialize in `__init__` (instance-final, also unsupported)" |
| `X: Final[T]` on `@native_c` class | error -- "`@native_c` classes have no static members; declare a free `native_global` instead" |
| `X: ClassVar[...]` (any form) | error -- "ClassVar at class level is not yet supported; use `Final[T] = value` for class constants" |
| `X: Final[T] = value` and `X: T` (instance field) on same class | error -- "name 'X' is both a class constant and an instance field" |
| Subclass redeclares a parent's class `Final` | error -- "cannot override Final class constant 'X' from base 'B'" |
| `obj.X` instance-side read where `X` is a class constant | error -- "class constants must be accessed as `<ClassName>.X`" (relax in v2 if needed) |
| `Child.X` where `X` is declared on `Parent` only | error -- v1 requires `Parent.X`; MRO resolution lands in v2 |
| Cross-module access `mod.MyClass.X` | works -- existing `IMPORTED_NAME` binding path resolves through to the record |
| Mutation `MyClass.X = ...` on `Final` | error -- reuses existing `Cannot reassign Final variable` path |
| `Final[T] = value` where `value` is not a compile-time constant | error -- reuses existing module-level `Final` constant initializer check |
| `Final[T] = value` where `value` references another class's `Final` (cross-module) | error today, mirrors module-level "cross-module Final references not yet supported" (`statements.py:2462-2463`) |
| Generic class `class C[T]: X: Final[T] = ...` | error -- "class constants on generic classes are not yet supported" |
| Forward reference between class constants in same body (`A: Final[int] = 1; B: Final[int] = A + 1`) | works in C++ (later `static constexpr` sees earlier ones in class scope); requires a test |
| `@dataclass` class with class constants alongside instance fields | works -- `@dataclass` iterates `record.fields`, class constants live in `record.class_constants` |

---

## Test Plan

New test cases under `tests/cases/class_const/`:

- `final_class_constant/` -- pure-TPy `Final[T] = value` in class body, read via `ClassName.X`.
- `final_strview_constant/` -- `Final[StrView] = "..."` -- exercises the `final_type_str_to_strview` path.
- `forward_ref_within_class/` -- `A: Final[int] = 1; B: Final[int] = A + 1`.
- `dataclass_with_class_const/` -- `@dataclass` class that also has a class constant; verify constant doesn't appear in synthesized `__init__`.
- `error_reassign_final_class_const/` -- mutation rejected.
- `error_final_no_value_regular/` -- helpful error pointing at the right form.
- `error_classvar_at_class_level/` -- `ClassVar[...]` rejected with "not yet supported".
- `error_name_conflict_field/` -- class constant and instance field share a name.
- `error_subclass_override_final/` -- child re-declares parent's Final.
- `error_child_reads_parent_const/` -- v1 requires explicit `Parent.X`; `Child.X` rejected when only declared on `Parent`.
- `error_generic_class_const/` -- generic class with `Final[T] = ...` rejected.

Native cases under `tests/cases/native/`:

- `native_class_final_extern/` -- `@native class X: FLAG: Final[bool]`, hand-written `<native_types.hpp>` provides `static constexpr bool FLAG`. Verifies `X::FLAG` use-site emission.
- `error_native_final_with_value/` -- TPy initializer on `@native` class rejected.
- `error_native_c_final/` -- `@native_c` class with `Final[T]` rejected.

---

## Future Extensions

| Feature | Notes |
|---------|-------|
| `ClassVar[T] = value` (mutable) and explicit `ClassVar[Final[T]] = value` | Land together in v2; needs decision on cross-module mutation safety. v1 reserves the `ClassVar[...]` spelling so v2 doesn't break working code |
| MRO resolution for `Child.X` reading parent's class constant | v1 requires explicit `Parent.X`. v2 walks `mro_ancestors` like instance fields do |
| Subclass override of class constant (non-`Final`) | Needs MRO-aware codegen for the right `static` to bind |
| Instance-final (`Final[T]` no value, regular class) | PEP 591 form; needs init-time set-once tracking |
| Reading class constants via instance (`obj.X`) | Python compatibility win; small fall-through in `_try_find_field` |
| `native_field("rename")` on class constants | Per-field C++ rename; rare for static members |
| `ClassVar` in protocols | Class-side protocol members; separate design |
| Class-level `@native` static methods | Different feature (not storage); declare as `@staticmethod` with `@native` |
| `__init_subclass__` / metaclass interactions | Out of scope; TPy doesn't support metaclasses generally |
