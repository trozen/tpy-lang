# Deref[T] Protocol Design

## Status

| Stage | Description | Status |
|-------|-------------|--------|
| 1 | Add `__deref__` method to Ptr[T] | Done |
| 2 | Sema — generalize auto-deref (field access, method calls) | Done |
| 3 | Codegen — emit deref calls for user types | Done |
| 4 | Deref coercion (replace hardcoded `ptr_to_record`) | TODO |
| 5 | ConstPtr `__deref__`, mutability enforcement | Partial |

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

- `Ptr[T].__deref__()` → `tpy::deref_ptr(ptr)` (checked null deref, returns `T&`)
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
- **Runtime null check**: Emits `tpy::deref_ptr(r).__deref__().field`.

### Stage 4: Deref coercion — TODO

Replace the hardcoded `ptr_to_record` coercion (`coercions.py`) with a generic rule: if type has `__deref__() -> T` and target is `T`, coerce via `obj.__deref__()`. For Ptr[T], codegen still emits `tpy::deref_ptr()` as a special case.

### Stage 5: ConstPtr, mutability — Partial

- ConstPtr[T] has `__deref__` and extends `Deref[T]` — **done**
- ConstPtr auto-deref works for field access and const method calls — **done**
- `__deref_mut__` for mutable deref distinction — **future**
- Transitive constness enforcement (e.g. `ConstPtr[list[T]]` blocking `.append()`) — **future**, requires a full const-propagation system

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
