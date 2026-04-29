# Class-Level Static Fields (`ClassVar` / class-body `Final`) -- Design

## Progress

v1 (Phases 1-4) landed the storage split and `ClassName.X` access for
`Final[T] = value` plus `@native` extern bindings. Phases 5-6 add
instance-side reads (`obj.X`) and MRO walk for inherited reads
(`Child.X` -> `Parent::X`) by extending the same plumbing -- the
separate `class_constants` dict, the `_analyze_field_access` branch,
and the qualified-name codegen helper. Phases 7+ remain additive
against the same surface and are sketched in
[Later Phases](#later-phases).

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Parser: keep class-body `AnnAssign` items in `TpyRecord.fields` as today; defer the field-vs-class-constant split to sema | Done (v1) |
| 2 | Sema registration: detect `Final[T]` on a class-body field, validate per the rules in [Forms Supported](#forms-supported), route to `RecordInfo.class_constants` (separate dict from `fields`); reject mutation, name conflicts, subclass overrides | Done (v1) |
| 3 | Sema + codegen for `ClassName.MEMBER` access: new branch in `_analyze_field_access`; codegen emits `static constexpr` / `static inline const` members on regular classes, no class-body emission for `@native`, qualified `<cpp_qname>::<member>` at all use sites | Done (v1) |
| 4 | Tests + validation polish for v1 (see [Test Plan](#test-plan)) | Done (v1) |
| 5 | `obj.X` instance-side reads: fallthrough in `_try_find_field` to `class_constants` (walks MRO via the same path as Phase 6) | Done |
| 6 | MRO walk: `Child.X` resolves through `mro_ancestors` to the declaring ancestor; emit `<owner_qname>::<member>` | Done |
| 7 | Mutable `ClassVar[T] = value`: recognize `ClassVarType` in `register_record`, allow mutation, codegen `static inline T X = value` | Done |
| 8 | Subclass override semantics: `Final` blocks override; non-final `ClassVar` shadows with type compatibility check | Planned |
| 9 | Generic classes with class constants: `Final[T_independent] = value` -- emit on the class template, validate initializer doesn't reference type params | Planned |
| 10 | `native_field("rename")` on class constants -- per-symbol rename | Done |

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
5. **Class-scoped reads, instance-scoped through fall-through.** `ClassName.X` is the primary read form; `obj.X` (Phase 5) and `Child.X` walking up to a declaring `Parent.X` (Phase 6) both fall through the same `class_constants` lookup. Codegen always emits the *declaring* class's qualified C++ name (`<owner_qname>::<member>`), so the access path doesn't depend on which class is named at the call site.

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

v1 looked only at the directly-named record. Phase 6 adds an `mro_ancestors` walk so `Child.X` resolves through the inheritance chain to the declaring ancestor; the access path sets `class_constant_owner` to that ancestor so codegen always emits the declaring class's qualified name.

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

### Phase 5 + 6: Instance-Side Reads and MRO Walk (Done)

Phases 5 and 6 share the same plumbing -- a single `Registry`-level
helper `find_class_constant_owner(record, field_name)` walks
`record.class_constants` then `iter_ancestor_records(record)` and returns
the declaring `RecordInfo` (or `None`). All three sema entry points use
it:

- **Phase 5** (`obj.X`, `self.X`): `_try_find_field` falls through to the
  helper after instance fields and properties miss. Codegen emits
  `<declaring_qname>::<member>` ignoring `obj`. Mutation through the
  instance (`obj.X = ...`) is rejected by the existing Final reassignment
  guard.
- **Phase 6** (`Child.X`): `_try_class_constant_access` uses the same
  helper, so `Child.X` resolves through the MRO to the declaring ancestor
  -- including multi-level (`Grandchild` -> `Mid` -> `Grand`). Codegen
  emits the *declaring* class's qname, never the looked-up class's, so it
  doesn't rely on C++ inheriting statics into derived class scope.

Edge cases handled:

- **Cross-module inherited constants**. The codegen path qualifies via
  `Registry.record_qualification(owner, current_module)` (which reads
  `RecordInfo.defining_module` directly) instead of the short-name
  `imported_record_qualification` lookup -- so a transitive ancestor that
  the accessing module never imported still emits the correct
  `::<defining_module>::<Parent>::<X>`.
- **Optional receiver matches CPython's `AttributeError`-on-`None`**.
  `obj: C | None; obj.X` warns ("Potential None access") and codegen
  wraps the access in `({ ::tpy::deref_check(obj); <Owner>::<X>; })` so
  reading through a `None` panics rather than silently succeeding (which
  would diverge from CPython). Narrowing (`if obj is not None: obj.X`)
  drops both the warning and the runtime check.
- **Side effects on `obj`**. When `expr.obj` is a non-name expression
  (e.g. `f().LIMIT`, `lst[i].LIMIT`), codegen wraps the access in a GCC
  statement expression `({ static_cast<void>(<obj_cpp>); <Owner>::<X>; })`
  so the receiver is evaluated for its effects and the constant is the
  yielded value. Plain-name receivers emit the bare qualified form.
- **Multi-base ambiguity**. `class C(A, B)` where both `A` and `B`
  declare the same class constant rejects unqualified `C.X` with the
  same message shape as the instance-field ambiguity check; the helper
  `find_class_constant_parent_branches` (in `protocols.py`, mirrors the
  instance-field counterpart) walks each direct parent's MRO via
  `find_class_constant_owner` and returns the contributing branches.
  The check fires only when the constant isn't declared directly on the
  child.

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

Initializer allow-list: tighter than `Final`. Mutable `ClassVar` accepts
numeric / `Char` / `bool` / tuple-of-allowed; `StrView` is rejected
because a write through a temporary (`C.X = make_string()`) would store
a view into freed storage. `Final[StrView]` remains the right form for
read-only string constants because constexpr forbids the dangerous
mutation. Lift later if/when TPy gains an owned-string `static inline`
storage shape that survives mutation.

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

### Phase 9: Generic Classes (T-Independent)

`class C[T]: MAX: Final[Int32] = 10`. The initializer doesn't reference
any type parameter. Emit on the class template; access via `C<T>::MAX`.
Same allow-list as the non-generic case. The new validation: confirm the
initializer's free names don't include any of the class's type parameters
(cheap walk over the initializer's `TpyName` references).

T-dependent class constants (`class C[T]: ZERO: Final[T] = T()`,
per-monomorphization initializers) are deferred to a future extension --
see [Future Extensions](#future-extensions).

### Phase 10: `native_field("rename")` on Class Constants

`FieldInfo` already carries `native_name`; the v1 codegen path for class
constants uses `record.native_name` for the class qname and the field name
verbatim for the member. Change: when the class constant's
`FieldInfo.native_name` is set, emit `<cpp_qname>::<native_name>` instead
of `<cpp_qname>::<member>`.

Whole-class `@native("Ns::C")` rename covers most cases (per-member Python
and C++ identifiers usually match). Per-member rename is needed when the
C++ side chose a different identifier (e.g. `g_flag` for a leading-`g_`
global convention).

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
| `obj.X` instance-side read where `X` is a class constant | resolves through `_try_find_field` -> `class_constants` (with MRO); codegen emits `<declaring_qname>::<X>` and ignores the instance | 5 |
| `Child.X` where `X` is declared on `Parent` only | resolves via `mro_ancestors` walk to the declaring ancestor; codegen emits `Parent::X` | 6 |
| Cross-module access via `from mod import MyClass; MyClass.X` | works -- existing `IMPORTED_NAME` binding path resolves through to the record | v1 |
| Chained module access `import mod; mod.MyClass.X` | falls through to a generic field-not-found error today (lookup helper only handles bare-`TpyName` LHS); revisit with cross-module v1.x polish | v1 -> later |
| Mutation `MyClass.X = ...` on `Final` | error -- reuses existing `Cannot reassign Final variable` path | v1 |
| Mutation `MyClass.X = ...` on non-final `ClassVar` | works -- writes to `static inline` storage | 7 |
| Mutation `obj.X = ...` on non-final `ClassVar` | works -- routes to the same class-scoped storage as `MyClass.X = ...` | 5 + 7 |
| `Final[T] = value` where `value` is not a compile-time constant | error -- reuses existing module-level `Final` constant initializer check | v1 |
| `Final[T] = value` where `value` references another class's `Final` (cross-module) | error today, mirrors module-level "cross-module Final references not yet supported" (`statements.py:2462-2463`) | v1 |
| Generic class `class C[T]: X: Final[T_independent] = ...` | error in v1; supported in Phase 9 | v1 -> 9 |
| Generic class `class C[T]: X: Final[T] = ...` (T-dependent) | error in v1 and Phase 9; deferred to a future extension | v1 -> future |
| Forward reference between class constants in same body (`A: Final[int] = 1; B: Final[int] = A + 1`) | works in C++ (later `static constexpr` sees earlier ones in class scope); requires a test | v1 |
| `@dataclass` class with class constants alongside instance fields | works -- `@dataclass` iterates `record.fields`, class constants live in `record.class_constants` | v1 |
| `native_field("rename")` on class constant | works on @native classes -- emits `<cpp_qname>::<native_name>` instead of `<cpp_qname>::<member>`; rejected on regular classes | 10 |

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
- `error_name_conflict_field/` -- class constant and instance field share a name.
- `error_subclass_override_final/` -- child re-declares parent's Final.
- `error_generic_class_const/` -- generic class with `Final[T] = ...` rejected (relaxed in Phase 9).

V1 native cases under `tests/cases/native/`:

- `native_class_final_extern/` -- `@native class X: FLAG: Final[bool]`, hand-written `<native_types.hpp>` provides `static constexpr bool FLAG`. Verifies `X::FLAG` use-site emission.
- `error_native_final_with_value/` -- TPy initializer on `@native` class rejected.
- `error_native_c_final/` -- `@native_c` class with `Final[T]` rejected.

### Phase 5 + 6 cases under `tests/cases/class_const/`:

- `obj_reads_class_const/` -- read class constant via an instance and via `self`; codegen emits qualified `ClassName::X`.
- `child_reads_parent_const/` -- `Child.X` resolves through MRO to `Parent::X`; instance-side read through a child also resolves to the declaring parent.
- `child_reads_grandparent_const/` -- multi-level MRO walk to a grandparent.
- `cross_module_inherited_const/` -- importing `Child` from a module where `LIMIT` is on `Parent`; codegen must qualify the declaring ancestor's namespace even though `Parent` was never imported into the accessing module.
- `optional_reads_class_const/` -- `obj: C | None; obj.X` warns and emits a runtime null-check before yielding the constant, matching CPython's `AttributeError`-on-`None`. Narrowing drops both the warning and the check.
- `obj_with_side_effects_reads_class_const/` -- `f().X` and `lst[i].X` evaluate the receiver for its side effects via a GCC statement expression, then yield the qualified constant.
- `error_reassign_class_const_via_instance/` -- `obj.X = ...` on a `Final` class constant still rejected by the existing reassignment guard.
- `error_ambiguous_class_const_multibase/` -- `class C(A, B)` where both `A` and `B` declare `X`: rejected with the same message shape as instance-field ambiguity.

### Later-phase cases (added when the phase ships):

- **Phase 7**: `classvar_mutable/` -- `ClassVar[T] = value`, read and mutate via `ClassName.X`. `classvar_final_explicit/` -- `ClassVar[Final[T]] = value` aliases `Final[T] = value`. `error_classvar_no_value/` -- `ClassVar[T]` without initializer rejected. `error_classvar_native/` -- `ClassVar` on `@native` class rejected.
- **Phase 8**: `subclass_shadow_classvar/` -- child redeclares non-final `ClassVar`, both storages observable. `error_subclass_shadow_type_mismatch/` -- shadow with incompatible type rejected.
- **Phase 9**: `generic_class_const_t_independent/` -- `class C[T]: MAX: Final[Int32] = 10` with multiple instantiations.
- **Phase 10**: `native_class_final_renamed/` -- `@native class X: FLAG: Final[bool] = native_field("g_flag")` emits `X::g_flag`. `error_native_field_class_const_on_regular_class/` -- `native_field()` on a non-`@native` class constant rejected via `_partition_class_constants` (distinct from the instance-field path).

---

## Future Extensions

Items past Phase 10. Phases 5-10 themselves are covered in [Later Phases](#later-phases).

| Feature | Notes |
|---------|-------|
| T-dependent class constants on generic classes (`class C[T]: ZERO: Final[T] = T()`) | Per-monomorphization initializer evaluation. Restrictions: initializer must be valid for every concrete `T` used in the program (e.g. `T()` requires every monomorphization to have a constexpr default constructor). For non-constexpr-default-constructible monomorphizations, fall back to `static inline const T X{};` rather than `static constexpr`. Deferred -- Phase 9's T-independent form covers known use cases; revisit when a concrete need surfaces |
| Instance-final (`Final[T]` no value, regular class) | PEP 591 form; needs init-time set-once tracking. Separate feature, not a class-constant extension |
| Record-typed `Final[T] = T(...)` | Today the inner-type allow-list (`is_final_allowed_inner`, shared with module-level Final) accepts only numeric/Char/StrView/bool/tuple. Lifting it to user-defined records is feasible in three tiers: (a) **constexpr-eligible records** -- all-primitive fields, no `__del__`, default args themselves constexpr; emit `static constexpr T X = T{...};` and mark the record's `__init__` `constexpr`. Cleanest tier. (b) **Non-constexpr value-type records** (fields needing dynamic alloc like `String` / `list[T]`); emit `static inline const T X = T{...};` -- C++17 inline makes cross-TU well-defined, runs at static-init. (c) **Reference-type records / records with `__del__`**; harder -- needs ownership/destructor story (does the static destruct at exit, interact with `tpy_terminate_handler`?). Implementation: new predicate `is_constexpr_constructible(record_info)` (recursive over fields), extend `is_final_allowed_inner`, extend `__init__` codegen to emit `constexpr` when eligible, codegen picks constexpr vs inline-const per-record. Module-level Final gets the same lift for free. M effort. |
| Bare `X` in a method body resolving to class scope | Python's resolution: bare `X` in a method body looks at locals then globals, never class scope. We match Python -- explicit `self.X` or `ClassName.X` is always required. Listed only to record the choice |
| `ClassVar` in protocols | Class-side protocol members; separate design |
| Class-level `@native` static methods | Different feature (not storage); declare as `@staticmethod` with `@native` |
| `__init_subclass__` / metaclass interactions | Out of scope; TPy doesn't support metaclasses generally |
