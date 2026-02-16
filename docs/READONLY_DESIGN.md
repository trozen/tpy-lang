# Readonly and Const Design

## Problem Statement

TurboPython needs a way to express and enforce immutability of references. This
enables:

1. **Narrowing safety** -- if a call is readonly, it is unlikely to invalidate
   Optional narrowing facts established before the call. (This is best-effort
   today -- see Known Limitations for soundness gaps.)

2. **C++ const correctness** -- readonly parameters and methods map to `const`
   in C++, enabling concept conformance (e.g., `Sized` requires
   `__len__() const`).

3. **API contracts** -- callers can see at a glance which parameters a function
   may mutate.

## Target Design: `readonly[T]` type modifier

`readonly[T]` is a type-level modifier that means "immutable reference to T."
It maps to `const T&` in C++. This follows C++ and Rust semantics: constness
is a property of the reference, not the object.

```python
from tpy import Int32, readonly

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

# Per-parameter readonly annotation
def distance(a: readonly[Point], b: readonly[Point]) -> float:
    dx = a.x - b.x
    dy = a.y - b.y
    return (dx * dx + dy * dy) ** 0.5

# Mixed mutability: source is readonly, dest is mutable
def copy_into(src: readonly[Point], dest: Point) -> None:
    dest.x = src.x
    dest.y = src.y
```

### Core rules

| On a `readonly[T]` reference     | Allowed?                              |
|----------------------------------|---------------------------------------|
| Read fields                      | Yes                                   |
| Write fields                     | No                                    |
| Call readonly methods             | Yes                                   |
| Call non-readonly methods         | No                                    |
| Pass to `readonly[T]` param      | Yes                                   |
| Pass to mutable `T` param        | No                                    |
| Subscript read (`x[i]`)          | Yes (returns `readonly[Elem]`)        |
| Subscript write (`x[i] = v`)     | No                                    |
| Rebind the variable (`x = other`) | Yes (changes the reference, not the object) |

### Value types are unaffected

Value types (`Int32`, `bool`, `float`, `str`, `Char`, etc.) are always copies.
`readonly[Int32]` is valid syntax but has no effect -- the parameter is a copy
regardless. This means you never need to annotate value-type parameters with
`readonly`.

### `@readonly` decorator (shorthand)

`@readonly` on a function or method means "all non-value-type parameters are
treated as readonly." It enforces the same mutation restrictions as
`readonly[T]` on each parameter.

```python
@readonly
def observe(b: Box) -> Int32:
    return b.value
# Equivalent enforcement to:
# def observe(b: readonly[Box]) -> Int32:
```

For methods, `@readonly` makes `self` readonly, which maps to a `const` method
in C++:

```python
class Box:
    @readonly
    def value(self) -> Int32:
        return self._value
    # C++: int32_t value() const { return _value; }
```

**Note on return types:** `@readonly` does NOT wrap the return type in
`readonly[T]`. Return-type constness comes from the C++ `const` method
qualifier -- a `const` method returning a `T&` member naturally returns
`const T&`. Explicit `readonly[T]` return type annotations are not yet
supported.

Forms:
- `@readonly` -- all params are readonly
- `@readonly(True)` -- same as above (explicit)
- `@readonly(False)` -- opt out of implicit readonly (for dunders)

### Implicit readonly on dunders

Most dunders naturally don't mutate their arguments. These are implicitly
`@readonly` without annotation:

```
__len__  __getitem__  __str__  __repr__  __hash__
__eq__  __ne__  __lt__  __le__  __gt__  __ge__
__add__  __sub__  __mul__  __truediv__  __floordiv__  __mod__  __pow__
__radd__  __rsub__  __rmul__  __rtruediv__  __rfloordiv__  __rmod__  __rpow__
__and__  __or__  __xor__  __lshift__  __rshift__
__neg__  __pos__  __invert__
```

Opt out with `@readonly(False)` for dunders that mutate state:

```python
class CachedLookup:
    @readonly(False)
    def __getitem__(self, idx: int) -> int:
        self._cache_hits += 1
        return self._data[idx]
```

### Local variable deduction

When a local variable is assigned from a readonly expression, it inherits
readonly status for non-value types:

```python
@readonly
def f(p: Point) -> None:
    alias = p           # alias: readonly[Point] (deduced)
    alias.mutate()      # ERROR: non-readonly method on readonly ref
    v = p.x             # v: Int32 (value type, no readonly needed)
    inner = p.box       # inner: readonly[Box] (non-value field of readonly)
```

Reassignment to a non-readonly source removes readonly status:

```python
@readonly
def f(p: Point) -> None:
    alias = p           # alias: readonly[Point]
    alias = Point(0, 0) # alias: Point (fresh object, no longer readonly)
    alias.mutate()      # OK
```

**Control-flow joins:** When branches assign different readonly status to the
same variable, the implementation conservatively merges with union semantics --
if a variable is readonly on either branch, it remains readonly after the join.
This is sound: a variable that might still hold a readonly-rooted reference on
any path must be treated as readonly.

### Dual const/non-const overloads

A `@readonly` method that returns a non-value type gets dual const/non-const
overloads in C++. `@readonly` means "doesn't mutate self", but the return
carries the caller's mutability context -- a const receiver gets a const
reference back, a mutable receiver gets a mutable reference:

```cpp
// Generated for @readonly methods returning non-value types:
const T& method() const;   // readonly receiver -> readonly ref
T& method();               // mutable receiver -> mutable ref

// Generated for @readonly methods returning value types:
T method() const;           // single const overload (copy, no ref duality)
```

At the callsite, C++ overload resolution selects based on the receiver's
constness. Sema enforces readonly at the TurboPython level independently:

```python
def read(c: readonly[Container]) -> Int32:
    return c[0]         # calls const overload, result is readonly[Int32]
                        # (Int32 is value type, so readonly is a no-op)

def write(c: Container) -> None:
    c[0] = 42           # calls non-const overload
```

### Interaction with Optional

`readonly[T | None]` and `readonly[T] | None` are **canonicalized to the same
internal form** by the parser. Both are rewritten to
`ReadonlyType(OptionalType(T))`. This is not a general distribution rule --
it is a specific parser normalization for the `T | None` Optional sugar.

```python
def f(p: readonly[Point | None]) -> None:
    if p is not None:
        print(p.x)      # OK: readonly read after narrowing
        p.x = 5         # ERROR: readonly
```

C++ mapping: `readonly[Point | None]` -> `const Point*` (const nullable
pointer).

### C++ mapping summary

| TurboPython                       | C++                            |
|-----------------------------------|--------------------------------|
| `p: Point` (param)               | `Point& p`                     |
| `p: readonly[Point]` (param)     | `const Point& p`               |
| `x: Int32` (param, value type)   | `int32_t x`                    |
| `x: readonly[Int32]` (param)     | `int32_t x` (same, copy)       |
| `@readonly` method               | `... method() const`           |
| `readonly[Point | None]`         | `const Point*`                 |

### Narrowing integration

- Readonly calls preserve Optional narrowing facts in practice, because they
  are unlikely to mutate the tracked objects. However, this guarantee is
  **best-effort**: container-mediated aliases and global writes can invalidate
  narrowing facts that the readonly system does not track. Full soundness
  requires escape analysis, which is not yet implemented.
- Non-readonly calls clear expression-identity facts.

### Builtins

Most builtins are readonly (they don't mutate arguments):
- `len`, `chr`, `abs`, `min`, `max`, `range`, `copy`, `print`

List/container mutation methods are NOT readonly:
- `list.append`, `list.extend`, `list.insert`, `list.pop`, `list.remove`,
  `list.sort`, `list.reverse`, `list.clear`, `list.__setitem__`

### Enforcement summary

A `readonly[T]` reference prevents:
1. Field writes: `p.x = 5`
2. Subscript writes: `p[i] = v`
3. Non-readonly method calls: `p.mutate()`
4. Passing to mutable `T` param: `mutate(p)` where `mutate(p: T)`
5. Any of the above through deduced-readonly local aliases

Allowed:
- Field reads, subscript reads, readonly method calls
- Constructing new objects
- Writing to globals
- I/O (`print`, etc.)
- Rebinding the variable itself (`p = other_point`)
- Passing value-type expressions derived from readonly refs to any function

## Current Implementation

### Phase 1: `@readonly` decorator (function-level)

- `@readonly` annotation on functions and methods
- `@readonly(False)` opt-out syntax for dunders
- Implicit readonly on dunder methods
- Parameter-rooted mutation enforcement: field/subscript write rejection,
  non-readonly method call on param rejection, non-readonly function call with
  param-derived arg rejection
- Local alias tracking: `alias = param` propagates parameter-rooted status
  through local variables (including transitive aliases)
- `print` is readonly (I/O is fine, doesn't mutate args)
- Global writes allowed (globals are not parameters)
- Narrowing integration: readonly calls preserve facts (best-effort)
- C++ const generation from is_readonly
- Conditional dual const/non-const overloads for non-value, non-Own reference
  return types on `@readonly` methods
- Builtins marked readonly: `len`, `chr`, `abs`, `min`, `max`, `range`,
  `copy`, `print`

### Phase 2: `readonly[T]` type modifier + type-embedded refactor

- `ReadonlyType` wrapper in type system (like `OwnType`)
- `readonly[T]` parsed in type annotations (subscript syntax)
- Maps to `const T&` in C++ for non-value types, `T` for value types
- Type-embedded enforcement (ReadonlyType carried in scope types):
  - `@readonly` wraps all non-value params with `ReadonlyType` at scope
    registration time; `readonly[T]` params carry it from annotation
  - Expression propagation: `readonly[T].field` -> `readonly[FieldType]` for
    non-value fields; `readonly[list[T]][i]` -> `readonly[T]`
  - Method resolution checks `is_readonly` on resolved function info when
    receiver carries `ReadonlyType`
  - Assignment target enforcement: `isinstance(obj_type, ReadonlyType)` check
    on field/subscript write targets
  - Type compatibility rejects `readonly[T]` -> `T` for non-value types
  - Passing readonly arg to mutable param caught by `check_type_compatible()`
- Local alias deduction: `alias = readonly_param` inherits `ReadonlyType` for
  non-value types through variable type deduction
- Control flow merging: if/else and while use scope type snapshots; readonly
  on either branch -> readonly after join (conservative union)
- `readonly[T]` rejected as variable type annotation (deduced from init)
- `readonly[T | None]` and `readonly[T] | None` canonicalized to
  `ReadonlyType(OptionalType(T))` in parser
- `readonly[Protocol]` generates `const T_name&` template params in codegen
- Codegen strips `ReadonlyType` (C++ handles const via method signatures)

### Known Limitations

1. **No `@tpy.readonly` form** -- only bare `@readonly` works, not the
   qualified module form.

2. **No automatic inference** -- regular (non-dunder) methods require explicit
   `@readonly`. Future: bottom-up inference from method body analysis.

3. **Container-mediated aliases not tracked** -- readonly tracking covers
   direct aliases (`alias = param`) and field/subscript chains, but not cases
   where a readonly reference is routed through a container or iterator before
   mutation.

4. **Narrowing preservation is best-effort** -- readonly calls are treated as
   safe for narrowing fact preservation, but global writes and
   container-mediated aliases can still invalidate facts without detection.
   Full soundness requires escape analysis.

6. **No explicit `readonly[T]` return type** -- `@readonly` affects parameter
   enforcement only. Return-type constness comes from C++ method const
   qualification, not from an explicit `readonly[T]` return annotation.

## Future Roadmap

1. **`@readonly` desugaring** -- rewrite as wrapping all param types with
   `readonly[T]` (and optionally return type)
2. **Conservative branch merging** -- properly handle readonly status across
   control-flow joins
3. **Protocol-level readonly contracts** -- `Sized.__len__`,
   `Sequence.__getitem__` etc. marked readonly in protocol definition;
   conformance checking ensures implementations are readonly
4. **Automatic inference** -- deduce readonly from method body when provable
5. **Escape analysis** -- track readonly references through containers for
   sound narrowing preservation
