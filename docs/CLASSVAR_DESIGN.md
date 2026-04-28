# Class-Level Static Fields (`ClassVar` / class-body `Final`) -- Design

## Progress

v1 (Phases 1-4) lands the storage split and `ClassName.X` access for
`Final[T] = value` plus `@native` extern bindings. Phases 5+ are additive
against the same plumbing (the separate `class_constants` dict, the
`_analyze_field_access` branch, the qualified-name codegen helper) and
are sketched in [Later Phases](#later-phases) so v2 readers can push back
early.

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Parser: keep class-body `AnnAssign` items in `TpyRecord.fields` as today; defer the field-vs-class-constant split to sema | Done (v1) |
| 2 | Sema registration: detect `Final[T]` on a class-body field, validate per the rules in [Forms Supported](#forms-supported), route to `RecordInfo.class_constants` (separate dict from `fields`); reject mutation, name conflicts, subclass overrides | Done (v1) |
| 3 | Sema + codegen for `ClassName.MEMBER` access: new branch in `_analyze_field_access`; codegen emits `static constexpr` / `static inline const` members on regular classes, no class-body emission for `@native`, qualified `<cpp_qname>::<member>` at all use sites | Done (v1) |
| 4 | Tests + validation polish for v1 (see [Test Plan](#test-plan)) | Done (v1) |
| 5 | `obj.X` instance-side reads: fallthrough in `_try_find_field` to `class_constants` (and ancestors after Phase 6) | Planned |
| 6 | MRO walk: `Child.X` resolves through `mro_ancestors` to the declaring ancestor; emit `<owner_qname>::<member>` | Planned |
| 7 | Mutable `ClassVar[T] = value`: recognize `ClassVarType` in `register_record`, allow mutation, codegen `static inline T X = value` | Planned |
| 8 | Subclass override semantics: `Final` blocks override; non-final `ClassVar` shadows with type compatibility check | Planned |
| 9 | Generic classes with class constants: T-independent first (9a), T-dependent per-monomorphization (9b) | Planned |
| 10 | `native_field("rename")` on class constants -- per-symbol rename | Planned (followup) |

### Out of Scope

| Feature | Why permanently out of scope |
|---------|------------------------------|
| Instance-final (`Final[T]` with no value, set once in `__init__`) on regular classes | Different feature; needs init-time set-once tracking. PEP 591 reserves this form, so we keep the syntax compatible by erroring on it for now and revisit as a separate design |
| `ClassVar` in protocols | Protocols already support instance-field declarations; class-side protocol members need separate design |
| Class constants on `@native_c` classes | C structs have no static members; reject permanently |

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

| Class-body form | Semantics | C++ emission (regular class) | C++ emission (`@native` class) | Lands in |
|---|---|---|---|---|
| `X: T` / `X: T = value` | **Instance field** (unchanged) | non-static member | non-static member | -- |
| `X: Final[T] = value` | Implicit `ClassVar[Final[T]]` per PEP 591 -- class-scoped, immutable | `static constexpr T X = value;` (or `static inline const T X` for non-constexpr-eligible types like `std::tuple` of non-literals) | **error** -- "Final initializer conflicts with C++-owned storage" | v1 |
| `X: Final[T]` *(no value)* | Extern class constant | **error** -- "Final without an initializer in a class body is not yet supported on regular classes; use `Final[T] = value`" | bind to C++ `<cpp_qname>::X` (no TPy-side struct emission; use site emits `<cpp_qname>::X`) | v1 |
| `X: ClassVar[T] = value` | Mutable class-scoped storage | `static inline T X = value;` | **error** -- "ClassVar with initializer conflicts with C++-owned storage" | Phase 7 |
| `X: ClassVar[Final[T]] = value` | Explicit form of `Final[T] = value` | same as `Final[T] = value` | same as `Final[T] = value` | Phase 7 |
| `X: ClassVar[T]` *(no value)* | Not a useful Python form (no initializer, no extern semantic on regular class) | **error** -- "ClassVar without initializer is not supported; use `Final[T]` for `@native` extern bindings" | **error** -- same | -- |

PEP 591's "implicit `ClassVar`" rule means `Final[T] = value` in a class body is a class constant, not an instance field. Type checkers (mypy, pyright) already see TPy code that way; matching them avoids surprising the IDE/LLM tooling.

The `Final[T]` (no value) reading on `@native` classes is the one place we deviate from PEP 591's instance-final reading. The deviation is safe because `@native` classes have no TPy-side `__init__` body to set instance finals from, so PEP 591's reading is unreachable; binding to a C++ `static` is the only useful interpretation.

**`ClassVar` lands in Phase 7.** PEP 591's implicit-ClassVar rule covers the read-only class-constant use case via `Final[T] = value`, so v1 doesn't need `ClassVar` recognition at all -- it errors with "ClassVar at class level lands in Phase 7" until then. The mutable `ClassVar[T] = value` form and the explicit `ClassVar[Final[T]] = value` form land together in Phase 7 with a coherent story for cross-module mutation safety.

---

## Design Principles

1. **Match Python type-checker semantics.** Code that works in TPy should pass mypy/pyright with the same meaning. PEP 526 + PEP 591 define what `ClassVar` and `Final` mean; we don't invent new spellings.
2. **Class constants are not instance fields.** Distinct storage in `RecordInfo` (`class_constants: dict[str, FieldInfo]`), distinct codegen path, distinct member access path. Mixing them in the same list invites bugs in field iteration, `__init__` synthesis, and serialization.
3. **`@native` reuses the same syntax surface.** Same `Final[T]` annotation, same `ClassName.X` access -- the `@native` decorator is the only thing that changes the codegen path (extern reference vs. local definition).
4. **No mutation of `Final`.** Reuse the existing `Final` reassignment guard (`statements.py:2362-2367`) -- extend it to class constants. Mutable `ClassVar` (without `Final`) lands in Phase 7.
5. **Restrict v1 to `ClassName.X` reads.** `obj.X` instance-side reads of class attributes are a Python-native feature but require a fall-through path in `_try_find_field` (after fields, before properties). Restricting to `ClassName.X` for v1 keeps the field-access codegen path narrow; instance-side reads land in Phase 5.

---

## Compilation Pipeline

This section covers v1 (Phases 1-4). Later-phase additions are sketched in
[Later Phases](#later-phases) and reuse the v1 plumbing described here.

### Parser (`tpyc/parse/parser.py`)

The parser does **no** field-vs-class-constant routing. `_parse_class` body iteration (`parser.py:1370-1464`) keeps treating `AnnAssign` items as `FieldInfo` exactly as today. The annotation flows through unchanged as a `TypeRefNode` (parser-emitted) or a resolved `TpyType`; both `Final[T]` (already representable as `FinalType`) and `ClassVar[...]` (a new marker, see below) survive into sema.

The split happens in sema's `register_record`, where `linkage`, the resolved annotation, and the presence/absence of an initializer are all known together. Doing it in the parser would require either inspecting unresolved AST shapes (brittle for aliased imports) or running type resolution in the parser (architectural mixing).

**`ClassVar` representation.** `Final[T]` already has a wrapper type (`FinalType`) and is recognized by `tpyc/parse/type_resolver.py:255`. Add a parallel `ClassVarType` wrapper recognized by the same resolver (gated entry: `"typing:ClassVar"`). For v1 it exists only so we can produce a "ClassVar at class level lands in Phase 7" error and reserve the spelling; no codegen path consumes it. Phase 7 turns it on.

### Sema (`tpyc/sema/registration.py`, `tpyc/sema/expressions.py`)

**Registration -- the split.** `register_record` (`registration.py:265-340`) inspects each `FieldInfo` whose annotation is `FinalType(...)` or `ClassVarType(...)`:

- `ClassVarType(...)` -> error ("ClassVar at class level lands in Phase 7"). Reserved spelling.
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
- Generic class with class constant -> error ("class constants on generic classes land in Phase 9").
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

## Later Phases

These phases extend the v1 plumbing additively. Each section sketches the
sema/codegen change without re-stating shared infrastructure (the
`class_constants` dict, the `_analyze_field_access` branch, the
qualified-name emission helper). Sub-designs here are deliberately terse --
they get fleshed out at implementation time.

### Phase 5: Instance-Side Reads (`obj.X`)

Python lets `instance.CLASS_CONST` fall back to the class. Drop the v1
`obj.X` rejection: in instance attribute resolution (`_try_find_field`
under `_analyze_field_access`), after instance fields don't match, check
`record.class_constants` (and ancestors, once Phase 6 lands).

Codegen continues to emit `<cpp_qname>::<member>` -- C++ permits
`obj.STATIC` syntax but the qualified form is unambiguous and matches the
`ClassName.X` path. Mutation through an instance (`obj.X = ...`) is
rejected on `Final` constants exactly like `MyClass.X = ...`; for non-final
`ClassVar` (Phase 7) it routes to the same static storage.

### Phase 6: MRO Walk for `Child.X`

When `X` is not declared directly on the named class, walk `mro_ancestors`
to find the declaring class. Use site emits `<declaring_owner_qname>::<member>`,
not the looked-up class's qname (so `Child.PARENT_CONST` emits
`Parent::PARENT_CONST`, not `Child::PARENT_CONST`). This avoids relying on
C++ inheriting statics into derived class scope -- the qualified name is
always the declaring class's, regardless of Python-level access path.

Combined with Phase 5, this makes the standard Python idiom work:
`instance.PARENT_CONST` resolves through both instance-side fallback and
MRO walk to `<parent_qname>::PARENT_CONST`.

### Phase 7: Mutable `ClassVar[T] = value`

Recognize `ClassVarType(T)` in `register_record` (parallel to `FinalType`):

- `ClassVarType(T)` with value, regular class -> route to `class_constants`
  with an `is_final=False` flag on the entry (or split into a separate
  `class_vars` dict -- decide at implementation time based on which read
  sites benefit from uniform iteration).
- `ClassVarType(FinalType(T))` with value -> alias for `Final[T] = value`,
  same routing, `is_final=True`.
- `ClassVarType(T)` no value -> error ("ClassVar without an initializer is
  not supported").
- `ClassVarType(...)` on `@native` class -> error (TPy-owned mutable
  storage conflicts with extern binding; users wanting a mutable extern
  static today reach for module-level `native_global`).

Storage: `static inline T X = value;` (no `const`). The `inline` keyword
gives it well-defined cross-TU semantics in C++17+; mutation through any
module is visible to all others, matching Python semantics.

Mutation paths: extend the existing assignment statement handler to allow
`MyClass.X = ...` and (Phase 5) `obj.X = ...` when `X` is a non-final
class constant. The existing `Cannot reassign Final variable` guard
already discriminates by finality; the same predicate gates the new path.

Initializer allow-list: same as module-level `Final` (numeric / `Char` /
`StrView` / `bool` / tuple). Lift later if real code needs richer
initializers.

### Phase 8: Subclass Override

V1 (Phase 2) rejects all subclass redeclarations of class constants
unconditionally. Phase 8 relaxes the non-final case:

- Child redeclares parent's non-final `ClassVar[T]`: shadow. Child gets
  its own `static inline T X = value;`. `Child.X` resolves to child's
  storage, `Parent.X` resolves to parent's. Matches Python semantics
  (child's `__dict__` overrides parent's lookup).
- Type compatibility on shadow: child's declared type must be the same as
  the parent's, or a subtype. Unrelated types -> error.
- Child redeclares parent's `Final` -> error (unchanged from v1).
- Warning on shadow: emit a hint making the override intent explicit
  (decide phrasing during implementation; possibly require an explicit
  `@override`-style marker if shadowing causes confusion in practice).

Codegen change is small: each declaring class emits its own static; use
sites emit the owner's qname (which falls out of the Phase 6 MRO walk
naturally -- the walk stops at the first declaring class).

### Phase 9: Generic Classes

Two sub-phases:

- **9a (T-independent)**: `class C[T]: MAX: Final[Int32] = 10`. The
  initializer doesn't reference any type parameter. Emit on the class
  template; access via `C<T>::MAX`. Same allow-list as the non-generic
  case. The new validation: confirm the initializer's free names don't
  include any of the class's type parameters (cheap walk over the
  initializer's `TpyName` references).

- **9b (T-dependent)**: `class C[T]: ZERO: Final[T] = T()`. Per-monomorphization;
  the initializer is evaluated for each instantiation. Restrictions:
  - Initializer must be valid for every concrete `T` used in the program.
    Practically `T()` (default-construct) requires every monomorphization
    to have a constexpr default constructor.
  - For non-constexpr-default-constructible monomorphizations, fall back
    to `static inline const T X{};` rather than `static constexpr`.
  - 9b is not strictly necessary if 9a covers real use cases; revisit
    when we hit a concrete need.

### Phase 10: `native_field("rename")` on Class Constants

`FieldInfo` already carries `native_name`; the v1 codegen path for class
constants uses `record.native_name` for the class qname and the field name
verbatim for the member. Change: when the class constant's
`FieldInfo.native_name` is set, emit `<cpp_qname>::<native_name>` instead
of `<cpp_qname>::<member>`.

Followup priority. Whole-class `@native("Ns::C")` rename covers the common
case (per-member Python and C++ identifiers usually match). Per-member
rename is needed when the C++ side chose a different identifier (e.g.
`g_flag` for a leading-`g_` global convention).

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

### `ClassVar` mutable (Phase 7, shown for direction)

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

| Case | Behavior | Phase |
|---|---|---|
| `X: Final[T] = value` on `@native` class | error -- "Final initializer conflicts with C++-owned storage; use `Final[T]` without value to bind, or remove `@native` if you want TPy to own the constant" | v1 |
| `X: Final[T]` on regular class | error -- "Final[T] without an initializer in a class body is not yet supported; use `Final[T] = value` for a class constant, or initialize in `__init__` (instance-final, also unsupported)" | v1 |
| `X: Final[T]` on `@native_c` class | error -- "`@native_c` classes have no static members; declare a free `native_global` instead" | v1 |
| `X: ClassVar[...]` (any form) | error in v1 -- "ClassVar at class level lands in Phase 7; use `Final[T] = value` for class constants" | v1 -> 7 |
| `X: Final[T] = value` and `X: T` (instance field) on same class | error -- "name 'X' is both a class constant and an instance field" | v1 |
| Subclass redeclares a parent's class `Final` | error -- "cannot override Final class constant 'X' from base 'B'" | v1 |
| Subclass redeclares a parent's non-final `ClassVar` | shadow with optional warning; child storage independent of parent's | 8 |
| `obj.X` instance-side read where `X` is a class constant | error in v1 -- "class constants must be accessed as `<ClassName>.X`"; supported in Phase 5 | v1 -> 5 |
| `Child.X` where `X` is declared on `Parent` only | error in v1 -- "no class constant 'X' on 'Child'; declared on 'Parent' (use `Parent.X`)"; supported in Phase 6 | v1 -> 6 |
| Cross-module access via `from mod import MyClass; MyClass.X` | works -- existing `IMPORTED_NAME` binding path resolves through to the record | v1 |
| Chained module access `import mod; mod.MyClass.X` | falls through to a generic field-not-found error today (lookup helper only handles bare-`TpyName` LHS); revisit with cross-module v1.x polish | v1 -> later |
| Mutation `MyClass.X = ...` on `Final` | error -- reuses existing `Cannot reassign Final variable` path | v1 |
| Mutation `MyClass.X = ...` on non-final `ClassVar` | works -- writes to `static inline` storage | 7 |
| Mutation `obj.X = ...` on non-final `ClassVar` | works -- routes to the same class-scoped storage as `MyClass.X = ...` | 5 + 7 |
| `Final[T] = value` where `value` is not a compile-time constant | error -- reuses existing module-level `Final` constant initializer check | v1 |
| `Final[T] = value` where `value` references another class's `Final` (cross-module) | error today, mirrors module-level "cross-module Final references not yet supported" (`statements.py:2462-2463`) | v1 |
| Generic class `class C[T]: X: Final[T_independent] = ...` | error in v1; supported in Phase 9a | v1 -> 9 |
| Generic class `class C[T]: X: Final[T] = ...` (T-dependent) | error in v1 and 9a; supported in Phase 9b | v1 -> 9 |
| Forward reference between class constants in same body (`A: Final[int] = 1; B: Final[int] = A + 1`) | works in C++ (later `static constexpr` sees earlier ones in class scope); requires a test | v1 |
| `@dataclass` class with class constants alongside instance fields | works -- `@dataclass` iterates `record.fields`, class constants live in `record.class_constants` | v1 |
| `native_field("rename")` on class constant | error in v1; supported in Phase 10 | v1 -> 10 |

---

## Test Plan

### v1 (Phase 4) cases under `tests/cases/class_const/`:

- `final_class_constant/` -- pure-TPy `Final[T] = value` in class body, read via `ClassName.X`.
- `final_strview_constant/` -- `Final[StrView] = "..."` -- exercises the `final_type_str_to_strview` path.
- `forward_ref_within_class/` -- `A: Final[int] = 1; B: Final[int] = A + 1`.
- `dataclass_with_class_const/` -- `@dataclass` class that also has a class constant; verify constant doesn't appear in synthesized `__init__`.
- `error_reassign_final_class_const/` -- mutation rejected.
- `error_final_no_value_regular/` -- helpful error pointing at the right form.
- `error_classvar_at_class_level/` -- `ClassVar[...]` rejected with "lands in Phase 7" hint.
- `error_obj_reads_class_const/` -- `obj.X` rejected with "use `<ClassName>.X`" hint (relaxed in Phase 5).
- `error_name_conflict_field/` -- class constant and instance field share a name.
- `error_subclass_override_final/` -- child re-declares parent's Final.
- `error_child_reads_parent_const/` -- v1 requires explicit `Parent.X`; `Child.X` rejected when only declared on `Parent` (relaxed in Phase 6).
- `error_generic_class_const/` -- generic class with `Final[T] = ...` rejected (relaxed in Phase 9).

V1 native cases under `tests/cases/native/`:

- `native_class_final_extern/` -- `@native class X: FLAG: Final[bool]`, hand-written `<native_types.hpp>` provides `static constexpr bool FLAG`. Verifies `X::FLAG` use-site emission.
- `error_native_final_with_value/` -- TPy initializer on `@native` class rejected.
- `error_native_c_final/` -- `@native_c` class with `Final[T]` rejected.

### Later-phase cases (added when the phase ships):

- **Phase 5**: `obj_reads_class_const/` -- read class constant via instance; verify codegen emits qualified `ClassName::X`.
- **Phase 6**: `child_reads_parent_const/` -- `Child.X` resolves through MRO to `Parent::X`; multi-level inheritance variant.
- **Phase 7**: `classvar_mutable/` -- `ClassVar[T] = value`, read and mutate via `ClassName.X`. `classvar_final_explicit/` -- `ClassVar[Final[T]] = value` aliases `Final[T] = value`. `error_classvar_no_value/` -- `ClassVar[T]` without initializer rejected. `error_classvar_native/` -- `ClassVar` on `@native` class rejected.
- **Phase 8**: `subclass_shadow_classvar/` -- child redeclares non-final `ClassVar`, both storages observable. `error_subclass_shadow_type_mismatch/` -- shadow with incompatible type rejected.
- **Phase 9a**: `generic_class_const_t_independent/` -- `class C[T]: MAX: Final[Int32] = 10` with multiple instantiations.
- **Phase 9b**: `generic_class_const_t_dependent/` -- `class C[T]: ZERO: Final[T] = T()` with constexpr-default-constructible `T`s.
- **Phase 10**: `native_class_final_renamed/` -- `@native class X: FLAG: Final[bool] = native_field("g_flag")` emits `X::g_flag`.

---

## Future Extensions

Items past Phase 10. Phases 5-10 themselves are covered in [Later Phases](#later-phases).

| Feature | Notes |
|---------|-------|
| Instance-final (`Final[T]` no value, regular class) | PEP 591 form; needs init-time set-once tracking. Separate feature, not a class-constant extension |
| Record-typed `Final[T] = T(...)` | Today the inner-type allow-list (`is_final_allowed_inner`, shared with module-level Final) accepts only numeric/Char/StrView/bool/tuple. Lifting it to user-defined records is feasible in three tiers: (a) **constexpr-eligible records** -- all-primitive fields, no `__del__`, default args themselves constexpr; emit `static constexpr T X = T{...};` and mark the record's `__init__` `constexpr`. Cleanest tier. (b) **Non-constexpr value-type records** (fields needing dynamic alloc like `String` / `list[T]`); emit `static inline const T X = T{...};` -- C++17 inline makes cross-TU well-defined, runs at static-init. (c) **Reference-type records / records with `__del__`**; harder -- needs ownership/destructor story (does the static destruct at exit, interact with `tpy_terminate_handler`?). Implementation: new predicate `is_constexpr_constructible(record_info)` (recursive over fields), extend `is_final_allowed_inner`, extend `__init__` codegen to emit `constexpr` when eligible, codegen picks constexpr vs inline-const per-record. Module-level Final gets the same lift for free. M effort. |
| Bare `X` in a method body resolving to class scope | Python's resolution: bare `X` in a method body looks at locals then globals, never class scope. We match Python -- explicit `self.X` or `ClassName.X` is always required. Listed only to record the choice |
| `ClassVar` in protocols | Class-side protocol members; separate design |
| Class-level `@native` static methods | Different feature (not storage); declare as `@staticmethod` with `@native` |
| `__init_subclass__` / metaclass interactions | Out of scope; TPy doesn't support metaclasses generally |
