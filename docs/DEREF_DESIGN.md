# Deref[T] Protocol Design

## Status

| Stage | Description | Status |
|-------|-------------|--------|
| 1 | Add `__deref__` method to Ptr[T] | Done |
| 2 | Sema — generalize auto-deref (field access, method calls) | TODO |
| 3 | Codegen — emit deref calls for user types | TODO |
| 4 | Deref coercion (replace hardcoded `ptr_to_record`) | TODO |
| 5 | ConstPtr support, `__deref_mut__` | Future |

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
3. Repeat until found or no more `__deref__` in the chain

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

## Implementation Roadmap

### Stage 1: Add `__deref__` to Ptr[T]

**Scope:** Small, purely additive, zero risk.

Add `__deref__` method to Ptr's module definition in `tpy.py`:

```python
"__deref__": [MethodDef(params=[], returns=T, cpp="tpy::deref_ptr({self})")]
```

Users can call `ptr.__deref__()` explicitly. Nothing else changes — existing auto-deref still uses the hardcoded `isinstance(PtrType)` paths. User-defined types with `__deref__` also work for explicit calls immediately.

### Stage 2: Sema — generalize auto-deref

**Scope:** Medium. Touches 2 key dispatch points.

Replace hardcoded pointer checks with generic deref chain resolution.

**Field access** (`sema/expressions.py`, `_analyze_field_access`):

Current:
```python
if isinstance(obj_type, (PtrType, ConstPtrType)):
    actual_type = obj_type.pointee
```

New: walk the deref chain until the field is found:
```
current_type = obj_type
deref_depth = 0
while True:
    if current_type has the field → done, record deref_depth
    if current_type has __deref__ → current_type = return type of __deref__, depth++
    else → error "no field X"
```

**Method calls** (`sema/methods.py`, `analyze_method_call`): Same pattern as a new fallback after `_analyze_user_record_method` / `_analyze_builtin_type_method` return None.

**AST annotation:** Store `deref_depth: int` on `TpyFieldAccess` and `TpyMethodCall` so codegen knows how many deref calls to emit.

Ptr[T] enters the chain naturally via its `__deref__` method — no special case needed in sema.

### Stage 3: Codegen — emit deref calls

**Scope:** Medium. Concentrated in `codegen_cpp/expressions.py`.

Two strategies based on type:

- **Ptr[T]:** Keep generating `ptr->field` via existing `is_indirect_name` / `is_pointer()` checks. This is a codegen optimization — Ptr maps to `T*` in C++, so `->` is natural.
- **User Deref types:** Generate `obj.__deref__().field` / `obj.__deref__().method()`. For depth 2+: `obj.__deref__().__deref__().field`.

Key insight: `is_indirect_name` is a codegen-level concept ("this C++ variable is `T*`"), not a semantic one. Deref is the semantic concept. They're orthogonal — user Deref types are structs in C++, not raw pointers.

### Stage 4: Deref coercion

**Scope:** Small.

Replace the hardcoded `ptr_to_record` coercion (`coercions.py`) with a generic rule: if type has `__deref__() -> T` and target is `T`, coerce via `obj.__deref__()`. For Ptr[T], codegen still emits `tpy::deref_ptr()` as a special case.

### Stage 5 (future): ConstPtr, mutability

- Add `__deref__` to ConstPtr[T] (returns const ref to T)
- Consider `__deref_mut__` for mutable deref distinction
- Enforce: field *assignment* through a deref chain requires `__deref_mut__` or Ptr in the chain

## Current hardcoded Ptr sites (reference)

Sites that Stage 2-4 will generalize:

| File | Line | What it does |
|------|------|-------------|
| `sema/expressions.py` | 435 | Field access: `isinstance(PtrType)` → use `pointee` |
| `coercions.py` | 176 | `ptr_to_record` coercion → `tpy::deref_ptr()` |
| `codegen_cpp/expressions.py` | 696 | Method call: `is_pointer()` → use `->` |
| `codegen_cpp/expressions.py` | 731 | Field access: `is_pointer()` → use `->` |
| `codegen_cpp/context.py` | 314 | `is_indirect_name` — stays as codegen optimization |
