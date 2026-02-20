# Deref[T] Protocol Design

## Status

| Stage | Description | Status |
|-------|-------------|--------|
| 1 | Add `__deref__` method to Ptr[T] | Done |
| 2 | Sema — generalize auto-deref (field access, method calls) | Done |
| 3 | Codegen — emit deref calls for user types | Done |
| 4 | Deref coercion (replace hardcoded `ptr_to_record`) | Done |
| 5 | ConstPtr `__deref__`, mutability enforcement | Partial |
| 6 | Null-safety for Ptr/ConstPtr auto-deref | Done |
| 7 | Generate `operator*` for types with `__deref__` | Done |
| 8 | Provenance-based null-check elision | Done |

## Motivation

`Ptr[T]` auto-dereferences in field access and method calls via hardcoded `isinstance(PtrType)` checks scattered across sema and codegen. This makes it impossible for user-defined smart pointer types (e.g., `Box[T]`, `Rc[T]`) to get the same behavior.

A `__deref__` protocol (inspired by Rust's `Deref` trait) unifies pointer-like types: any type with `__deref__() -> T` auto-dereferences through field access, method calls, and coercions.

## Design

### The `__deref__` method

Any type defining `__deref__(self) -> T` is deref-able. The compiler auto-dereferences when accessing fields or methods not found on the type itself.

```python
from tpy import Ptr, Int32

# Built-in: Ptr[T] has __deref__() -> T
x: Int32 = 42
p: Ptr[Int32] = Ptr(x)
p.__deref__()  # explicit deref, returns Int32

# User-defined:
class Box[T]:
    _value: T
    def __init__(self, value: T) -> None:
        self._value = value
    def __deref__(self) -> T:
        return self._value

class Point:
    x: Int32
    y: Int32

b: Box[Point] = Box(Point(1, 2))
print(b.x)       # auto-deref: b.__deref__().x
print(b._value)   # no deref needed: Box has _value
```

### Recursive auto-deref (Rust model)

When resolving `obj.field` or `obj.method()`:

1. Try `obj`'s own type first
2. If not found and type has `__deref__() -> T`, try `T`
3. Repeat until found or no more `__deref__` in the chain (max depth: 8)

```python
class Wrapper[T]:
    inner: T
    def __deref__(self) -> T:
        return self.inner

w: Wrapper[Ptr[Point]] = ...
print(w.x)  # Wrapper has no x → deref to Ptr[Point] → deref to Point → found x
```

### C++ mapping

- `Ptr[T].__deref__()` → `tpy::deref_check(ptr)` (checked null deref, returns `T&`)
- User types: `obj.__deref__()` → literal `obj.__deref__()` call in C++
- Field/method access through Ptr keeps the `->` optimization in codegen

## Implementation

### Stage 1: Add `__deref__` to Ptr[T] — Done

Added `__deref__` method to Ptr's module definition in `tpy.py`. Users can call `ptr.__deref__()` explicitly.

### Stage 2: Sema — generalize auto-deref — Done

Replaced hardcoded pointer checks with generic deref chain resolution.

**Field access** (`sema/expressions.py`): `_analyze_field_access` unwraps Optional/Own, then walks a deref chain via `get_deref_target_type()` until the field is found. `deref_depth` is stored on the AST node.

**Method calls** (`sema/methods.py`): `analyze_method_call` uses the same deref chain pattern via `_try_resolve_method()`.

**Type resolution** (`get_deref_target_type`): Looks up `__deref__` on any type via the registry, handles both builtin types (Ptr, ConstPtr — via `extract_type_params`) and user-defined generic records (via `build_type_substitution`).

### Stage 3: Codegen — emit deref calls — Done

Two strategies based on type:

- **Ptr[T] / ConstPtr[T]:** Keep generating `ptr->field` via existing `is_indirect_name` / `is_pointer()` checks.
- **User Deref types:** Generate `obj.__deref__().field` / `obj.__deref__().method()`. For depth 2+: `obj.__deref__().__deref__().field`.

Codegen also handles the interaction with Optional receivers:
- **Narrowed Optional** (`Ref | None` proven non-null): C++ var is still `Ref*`, so codegen emits `r->__deref__().field` (arrow for pointer deref, then user deref chain).
- **Runtime null check**: Emits `tpy::deref_check(r).__deref__().field`.

### Stage 4: Deref coercion — Done

Replaced the hardcoded `ptr_to_record` coercion with a generic deref fallback in `check_type_compatible()`. When `resolve_coercion()` finds no match, the compatibility checker looks up `__deref__() -> T` via `TypeOperations.get_deref_target_type()` and applies a `DEREF_COERCION` sentinel if the target matches.

- `Ptr[T]` → `T`: codegen emits `tpy::deref_check(expr)` (null-checked)
- User Deref types → `T`: codegen emits `expr.__deref__()`
- `ConstPtr[T]` → `T`: excluded for now — C++ generates record params as `T&` (mutable ref) but `deref_check(const T*)` returns `const T&`, causing const-correctness errors. Requires parameter codegen changes (`const T&` for read-only params).

### Stage 5: ConstPtr, mutability — Partial

- ConstPtr[T] has `__deref__` and extends `Deref[T]` — **done**
- ConstPtr auto-deref works for field access and const method calls — **done**
- `__deref_mut__` for mutable deref distinction — **future**
- Transitive constness enforcement (e.g. `ConstPtr[list[T]]` blocking `.append()`) — **future**, requires a full const-propagation system

### Stage 6: Null-safety for Ptr/ConstPtr auto-deref — Done

Replaced unchecked `ptr->field` / `ptr->method()` with `tpy::deref_check(ptr).field` / `tpy::deref_check(ptr).method()` in codegen for `PtrType` and `ConstPtrType`. Every auto-deref through a pointer is now null-checked via the existing `tpy::deref_check()` runtime function, which panics with "null pointer dereference" on null. This matches the behavior of explicit `.__deref__()` calls.

The C++ optimizer can elide redundant null checks on the same pointer in release builds (`-O2`/`-O3`).

### Stage 7: Generate `operator*` for types with `__deref__` — Done

User-defined records with `__deref__()` now get `operator*()` generated in the C++ struct, enabling `*box` syntax for C++ interop. Uses `auto` return type with `decltype(__deref__())` to handle generic type parameters correctly. `Ptr[T]`/`ConstPtr[T]` are excluded since they map to raw `T*`/`const T*` which already support `*ptr` natively.

**Known limitation**: `operator*()` is non-const only, since `__deref__()` is generated as non-const. A const overload requires const method generation for `__deref__`, which depends on the broader const method system (Stage 5).

### Stage 8: Provenance-based null-check elision — Done

Added flow-tracked `non_null_ptr_vars` set to sema context (alongside existing `param_provenance_vars` and `narrowed_types`). The set tracks pointer variables with known non-null provenance through the same save/restore/merge infrastructure used by definite assignment and Optional narrowing.

**Tracking**: When a variable is assigned from `Ptr(x)` or `ConstPtr(x)` (constructor with an argument), it's marked non-null. Assignment from another known non-null variable propagates the fact. Reassignment to unknown source (function return, null constructor, etc.) clears it. Branch merges use intersection (conservative).

**AST flag**: `ptr_non_null: bool` on `TpyFieldAccess` and `TpyMethodCall`, set by sema when the receiver is a `TpyName` in `non_null_ptr_vars` and the receiver type is `PtrType`/`ConstPtrType`.

**Codegen**: When `ptr_non_null` is set, emits `ptr->field` / `ptr->method()` instead of `tpy::deref_check(ptr).field` / `tpy::deref_check(ptr).method()`.

**Scope**: Field access and method calls only. Deref coercion (`DEREF_COERCION`) remains always null-checked. Function parameters have unknown provenance (caller might pass null).

## Deref[T] Protocol Definition

Defined in `tpyc/modules/tpy.py`:

```python
module.protocol("Deref",
    type_params=["T"],
    methods={
        "__deref__": MethodDef(params=[], returns=T, cpp="{self}.__deref__()"),
    },
    cpp_concept="tpy::Deref",
)
```

Both `Ptr[T]` and `ConstPtr[T]` declare `extends=["Deref[T]"]`. User-defined types conform structurally by implementing `__deref__() -> T`.

## Notes

**Cosmetic codegen improvements** (nice to have, not staged): Generate `operator->` for user deref types (needs `requires` clause for non-value targets). Use `*box` / `box->field` instead of `box.__deref__().field` in codegen for user types. Replace `(*x).field` with `x->field` for narrowed Optionals.
