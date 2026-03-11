# @overload Dispatch Flattening (B10)

## Roadmap

| Phase | Feature | Status |
|-------|---------|--------|
| 1 | Parser: recognize `@overload`, validate stub body | Done |
| 2 | Sema: stub grouping, exhaustiveness validation | Done |
| 3 | Sema: call resolution against stubs | Done |
| 4 | Codegen: per-stub specialization, dead branch elimination | Done |
| 5 | Codegen: return type validation per overload | Done |
| - | Cross-module overload import | Done |
| - | Method overloads | Done |
| - | Generic function/method overloads | Done |

## Future Extensions

| Feature | Notes |
|---------|-------|
| Partial union stubs | Stub takes `Dog \| Cat` when impl has `Dog \| Cat \| Bird`. Needs `resolve_overload` to match concrete arg against union stub params via member containment. |
| Non-union overloads | Overloads distinguished by coercion-compatible types (e.g., `Int32` vs `float`). Needs a different dispatch mechanism since isinstance doesn't apply. |
| Overload on arity | Different parameter counts per stub. Maps to C++ overloads with different parameter counts. |
| `slice` type integration | `__getitem__` overloads with `Int32 \| slice` -- canonical use case for user-defined slicing. |
| Move return type validation to sema | Currently done in codegen (post dead branch elimination). Moving to sema would surface errors in IDE diagnostics and avoid reimplementing compatibility rules. |

---

## Overview

Support Python's `@overload` decorator (PEP 484) to generate separate C++ overloads
from a single implementation function. The compiler specializes the implementation body
per overload stub by resolving isinstance/match checks at compile time and eliminating
dead branches.

```python
from typing import overload

@overload
def describe(x: Dog) -> str: ...
@overload
def describe(x: Cat) -> str: ...
@overload
def describe(x: Bird) -> Int32: ...
def describe(x: Dog | Cat | Bird) -> str | Int32:
    if isinstance(x, Dog):
        return "woof: " + x.name
    elif isinstance(x, Cat):
        return "meow: " + x.name
    else:
        return x.wing_count
```

Generated C++:

```cpp
std::string describe(const Dog& x) {
    return std::string("woof: ") + x.name;
}

std::string describe(const Cat& x) {
    return std::string("meow: ") + x.name;
}

int32_t describe(const Bird& x) {
    return x.wing_count;
}
```

No union wrapper function is emitted. Each stub becomes a standalone C++ overload.

## Design Principles

1. **Standard Python syntax**: Uses `@overload` from `typing`, same as mypy/pyright
2. **CPython compatible**: In CPython, `@overload` stubs are ignored at runtime; the
   implementation body handles all cases via isinstance dispatch
3. **Explicit**: Only methods/functions with `@overload` stubs are flattened -- no
   automatic pattern detection
4. **Exhaustive**: Stub parameter types must collectively cover all union variants
5. **Flexible body**: The implementation body can use isinstance, match/case, or any
   other dispatch pattern -- the compiler resolves statically and eliminates dead branches

## Scope

Works for:
- Free functions
- Instance methods
- Static methods
- Generic functions/methods (type params preserved per overload)

Current scope is union-typed parameters only. Non-union overloads (e.g., `Int32` vs
`float` via coercion) are a future extension.

## Syntax and Semantics

### Declaration

```python
from typing import overload

# Stubs: declare per-overload signatures
@overload
def f(x: A) -> R1: ...
@overload
def f(x: B) -> R2: ...

# Implementation: union-typed, contains the dispatch logic
def f(x: A | B) -> R1 | R2:
    if isinstance(x, A):
        return make_r1(x)
    else:
        return make_r2(x)
```

Rules:
- Stubs must have body `...` (Ellipsis) or `pass`
- Exactly one non-stub implementation with the same name must follow the stubs
- The implementation's union-typed parameters must be supertypes of each stub's params
- Stubs can have different return types (the key use case)

### Exhaustiveness

For each parameter position where the implementation has a union type, the compiler
checks that stub types collectively cover all union members:

```python
# Implementation param: Dog | Cat | Bird
# Stub 1 param:        Dog
# Stub 2 param:        Cat
# Coverage:            {Dog, Cat}
# Missing:             {Bird}
# -> error: @overload stubs for 'describe' don't cover all variants
#           parameter 'x': missing Bird
```

Rules:
- Only parameters where the implementation type is a union are checked
- Each stub parameter must be a concrete (non-union) type or match the implementation
  type exactly. Partial union stubs (e.g., `Dog | Cat` when impl has `Dog | Cat | Bird`)
  are a future extension (see table above).
- Stub types must be subsets of the implementation's union members
- Stub parameter names must match implementation parameter names
- Parameters identical across all stubs and implementation are not checked
- Coverage is checked per-parameter independently (no cross-product requirement)
- Return types are NOT checked for exhaustiveness -- each overload's live return
  paths are validated against its declared return type during codegen

### Return type validation

Each overload stub can declare a different return type. During codegen, after dead
branch elimination prunes unreachable branches, every surviving `return` statement
is checked against the stub's declared return type using sema's full compatibility
rules (coercions, Optional wrapping, inheritance, etc.).

```python
@overload
def get_value(animal: Dog) -> str: ...
@overload
def get_value(animal: Cat) -> int: ...
def get_value(animal: Dog | Cat) -> str | int:
    if isinstance(animal, Dog):
        return 42  # error: returning 'int' but this overload declares '-> str'
    else:
        return animal.lives
```

### Multiple union parameters

```python
@overload
def f(a: A, b: X) -> R1: ...
@overload
def f(a: B, b: Y) -> R2: ...
def f(a: A | B, b: X | Y) -> R1 | R2:
    ...
```

Coverage: `a` covered by {A, B}, `b` covered by {X, Y}. Passes.

The combination (A, Y) has no stub, so `f(a_val, y_val)` where `a: A, b: Y` would
fail with "no matching overload" at the call site. This is intentional -- the user
chose not to support that combination.

### Call resolution

At call sites, the compiler resolves against **stub signatures** (not the implementation).
The existing two-pass overload resolution engine (`sema/overloads.py`) handles this.

```python
d = Dog("Rex")
result = describe(d)  # resolves to stub 1: describe(Dog) -> str
# result type: str (not str | Int32)
```

## Codegen: Dead Branch Elimination

The key mechanism is a **specialization map** threaded through codegen:

```python
# codegen_cpp/context.py
overload_param_types: dict[str, TpyType]  # param_name -> concrete type
```

### isinstance resolution

```python
def resolve_isinstance_statically(
    self, var_name: str, check_type: TpyType
) -> bool | None:
    """Returns True/False if statically decidable, None if dynamic."""
    if self.overload_ctx is None:
        return None
    concrete = self.overload_ctx.param_types.get(var_name)
    if concrete is None:
        return None
    if concrete == check_type:
        return True
    if isinstance(check_type, UnionType) and concrete in check_type.members:
        return True
    # concrete is a different type entirely
    return False
```

### if/elif/else chain

```python
# Pseudocode for generating an if/elif chain with overload context
for branch in [if_branch, *elif_branches]:
    result = resolve_isinstance_statically(var, branch.check_type)
    if result is True:
        emit_body(branch.body)  # this is the live branch
        return  # skip remaining branches (including else)
    elif result is False:
        continue  # skip this branch entirely
    else:
        emit_conditional(branch)  # dynamic check (non-specialized param)

# If we get here, emit else body (if present)
if else_body:
    emit_body(else_body)
```

### match/case

```python
# Pseudocode for generating match/case with overload context
for arm in match.arms:
    if is_type_pattern(arm.pattern):
        result = resolve_isinstance_statically(subject, arm.pattern.type)
        if result is True:
            emit_body(arm.body)
            return
        elif result is False:
            continue
    elif is_wildcard(arm.pattern):
        emit_body(arm.body)
        return
    # ... other pattern kinds handled normally
```

## Examples

### Method overload (the primary use case)

```python
class Container[T]:
    @overload
    def get(self, index: Int32) -> T: ...
    @overload
    def get(self, name: str) -> T: ...
    def get(self, key: Int32 | str) -> T:
        if isinstance(key, Int32):
            return self._items[key]
        else:
            return self._named[key]
```

```cpp
template<typename T>
struct Container {
    T& get(int32_t key) {
        return _items[key];
    }
    T& get(std::string_view key) {
        return _named[key];
    }
};
```

### Free function with match/case

```python
@overload
def area(s: Circle) -> float: ...
@overload
def area(s: Rect) -> float: ...
def area(s: Circle | Rect) -> float:
    match s:
        case Circle(radius=r):
            return 3.14159 * r * r
        case Rect(width=w, height=h):
            return w * h
```

```cpp
double area(const Circle& s) {
    auto& r = s.radius;
    return 3.14159 * r * r;
}
double area(const Rect& s) {
    auto& w = s.width;
    auto& h = s.height;
    return w * h;
}
```

### Overload with shared logic

```python
@overload
def process(x: Dog) -> str: ...
@overload
def process(x: Cat) -> str: ...
def process(x: Dog | Cat) -> str:
    name = x.name  # shared -- both Dog and Cat have .name
    if isinstance(x, Dog):
        return name + " barks"
    else:
        return name + " meows"
```

Shared code before the dispatch point is emitted in both overloads:

```cpp
std::string process(const Dog& x) {
    auto name = x.name;
    return name + std::string(" barks");
}
std::string process(const Cat& x) {
    auto name = x.name;
    return name + std::string(" meows");
}
```

### Different return types per overload

```python
@overload
def get_value(animal: Dog) -> str: ...
@overload
def get_value(animal: Cat) -> int: ...
def get_value(animal: Dog | Cat) -> str | int:
    if isinstance(animal, Dog):
        return animal.name
    else:
        return animal.lives
```

```cpp
std::string get_value(Dog& animal) {
    return animal.name;
}
tpy::BigInt get_value(Cat& animal) {
    return animal.lives;
}
```

The caller sees the stub's return type, not the union:

```python
dog_val = get_value(d)  # type: str (not str | int)
cat_val = get_value(c)  # type: int (not str | int)
```
