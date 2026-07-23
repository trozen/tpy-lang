# Readonly and Const Design

## Status

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | `@readonly` decorator (function/method level), implicit readonly dunders, parameter-rooted mutation enforcement, local alias tracking, C++ const generation | Done |
| **Phase 2** | `readonly[T]` type modifier, type-embedded enforcement, expression propagation, control-flow merging, dual const/non-const overloads | Done |
| **Phase 3** | `@auto_readonly` decorator: dual C++ overloads (non-const + const) for user-defined methods; const return type specified via explicit `auto_readonly[T]` annotation on element types | Done |
| **Future** | `@readonly` desugaring, protocol-level readonly contracts, automatic inference from method body, escape analysis for sound narrowing | Planned |

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
- Expression-identity narrowing for fields/subscripts was removed (unsound
  with threading and user-defined `__getitem__`). Narrowing only applies to
  local variable names.

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

2. **Automatic inference** -- methods that never mutate `self` (transitively
   through call edges) are emitted as `const` C++ member functions even without
   an explicit `@readonly`. Two-phase: Phase 1 records direct mutations and
   call edges per body; Phase 2 propagates `self_mutated` through the
   intra-module call graph to a fixpoint. See `LANGUAGE_FEATURES.md` for the
   full rule set, including which methods are exempted (constructors,
   `__del__`, in-place operators, consuming methods, `@auto_readonly` mutable
   clones, methods overriding non-const `@dynamic` virtuals, and methods
   whose return value borrows from `self`).

3. **Container-mediated aliases** -- resolved by type-embedded refactor.
   ReadonlyType propagates through subscript (`readonly[list[T]][i]` ->
   `readonly[T]`), field access chains, and iteration. List literals with
   readonly elements infer `PendingList[readonly[T]]` which rejects assignment
   to `list[T]`. Passing `readonly[list[T]]` to `list[T]` param is caught by
   type compatibility. No taint-tracking needed for these cases.

4. **Narrowing preservation is best-effort** -- readonly calls are treated as
   safe for narrowing fact preservation, but non-readonly calls conservatively
   clear subscript narrowing facts even when the container isn't passed as an
   argument (global aliasing scenario). Full soundness for all edge cases
   requires escape analysis.

6. **No explicit `readonly[T]` return type** -- `@readonly` affects parameter
   enforcement only. Return-type constness comes from C++ method const
   qualification, not from an explicit `readonly[T]` return annotation.

7. **Sema/codegen mismatch for dual overloads** -- sema analyzes each method
   body once, but codegen may generate two C++ overloads (const + non-const)
   from the same body. When a readonly method's body calls other methods whose
   return types differ by constness (e.g. `self.__span__()` returning
   `Span[T]` vs `Span[readonly[T]]`), sema can only see one variant's types.
   This causes false type mismatches when the declared return type matches one
   variant but not the other. `@auto_readonly` (Phase 3) addresses this.

## Phase 3 Implementation: `@auto_readonly`

### Approach: method-expansion cloning

`@auto_readonly` is implemented by expanding each decorated method into
two ordinary methods during sema method expansion (`_clone_auto_readonly`
in `tpyc/sema/method_expansion.py`):

- **Mutable clone**: `is_readonly=False`, `is_alt_mutable_clone=True`,
  return type = `strip_alt(original_return_type)`
- **Const clone**: `is_readonly=True`, deep-copied body,
  return type = `apply_alt(original_return_type)`

`strip_alt` removes `auto_readonly[T]` annotation nodes.
`apply_alt` replaces them with `readonly[T]`. Both recurse structurally.

After cloning, both clones have `auto_readonly=False` and are treated as
ordinary methods by all subsequent compiler passes (sema, codegen).

### Explicit return type annotations

The const variant's return type is controlled entirely by explicit
`auto_readonly[T]` annotations inside the return type:

```
Span[auto_readonly[T]]  ->  mutable: Span[T],  const: Span[readonly[T]]
Ptr[auto_readonly[T]]   ->  mutable: Ptr[T],   const: Ptr[readonly[T]]
auto_readonly[T]        ->  mutable: T,         const: readonly[T]
```

Parts of the return type without `auto_readonly[T]` are unchanged in
both clones. Value types (copied on return) need no annotation.

### Interaction with `IMPLICIT_READONLY_METHODS`

Methods in `IMPLICIT_READONLY_METHODS` (`__getitem__`, `__span__`, `__deref__`,
`__iter__`, `__len__`, `__eq__`, arithmetic operators, etc.) are treated as
`@readonly` (single const overload) by default. Dual overloads require an
explicit `@auto_readonly` decorator.

When `@auto_readonly` is applied to a method in `IMPLICIT_READONLY_METHODS`,
the compiler generates dual overloads. Without it, only a single const overload
is generated (which is correct for value-returning methods like `__len__` and
`__eq__`, but not for reference-returning methods like `__getitem__ -> T`).

The general rule: apply `@auto_readonly` whenever the method returns a
reference type and you want the const overload to return a const variant. Mark
the parts of the return type that should become const with `auto_readonly[T]`:
`Span[auto_readonly[T]]`, `Ptr[auto_readonly[T]]`, `auto_readonly[T]`
for a bare type parameter. Value types (copied on return) need no annotation.

### Interaction with `@overload`

`@auto_readonly` applies per-overload. When a method has multiple
`@overload` stubs, only overloads whose return type benefits from propagation
need dual treatment. Example:

```python
class ArrayList[T, N: int]:
    @overload
    @auto_readonly
    def __getitem__(self, index: Int32) -> T: ...                       # T is value-copied; no annotation needed
    @overload
    @auto_readonly
    def __getitem__(self, index: slice) -> Span[auto_readonly[T]]: ... # Span element becomes const
```

Both overloads get dual analysis. The shared implementation body is analyzed
in both contexts (mutable and const).

### Transitive calls

Works naturally. Inside the readonly variant of `__getitem__`, `self` is
readonly, so `self.__span__()` resolves to `__span__`'s readonly variant
(returning `Span[readonly[T]]`). The types flow correctly through each variant
independently.

### Usage-dependent receiver const-ness

A parameter read only *through* an `@auto_readonly` accessor (`Box.get`,
`Rc.get`, a `Deref.__deref__`) keeps a `const` receiver. The mutable clone of
the accessor does not mutate its receiver -- only a mutation *through* the
borrowed result does -- so the accessor call itself no longer demotes the
receiver param (`sema/methods.py` skips the non-readonly-call mutation mark for
`is_auto_readonly_mutable_clone`). The actual demotion is rooted back to the
receiver at the mutation site:

```python
def read(o: Outer) -> Int32:  return o.b.get().v   # const Outer&  (result only read)
def write(o: Outer) -> None:  o.b.get().v = 9       # Outer&        (result field written)
def mutate(o: Outer) -> None: o.b.get().bump()      # Outer&        (mutating method on result)
```

C++ overload resolution then selects `get()` vs `get() const` automatically
from the receiver's const-ness -- no clone is chosen in sema or codegen.

Two predicates intentionally differ in scope: the call-site demotion is
suppressed for *every* `@auto_readonly` mutable clone (`is_auto_readonly_mutable_clone`)
-- including value-returning ones, which read the receiver to produce a copy
and must not demote it -- whereas mutation rooting back through the result
(the `_root_name_of_expr` transparency) applies only to a *borrowing* clone
(`FunctionInfo.borrows_receiver_via_auto_readonly`, which also requires the
result to alias the receiver), since a value copy cannot carry a mutation back.
Mutation rooting is symmetric with subscript element access (`xs[0].v = 9`):
`_root_name_of_expr` is transparent to a borrowing accessor call, and a genuine
through-reference write climbs a field-path borrow root to its owning param
(`mark_param_mutated(..., through_field=True)`).

**Limitation:** binding a named accessor result to a local (`x = o.b.get()`)
keeps the receiver mutable, because a non-const local alias requires a mutable
source (locals are non-const by default). A const receiver for aliased *reads*
awaits never-mutated-local const-binding (see TODO). The SUBSCRIPT spelling
(`x = o.b[k]`) is not subject to this: it stays readonly-compatible, and the
bound borrow local is emitted const when the receiver chain is const-rooted
(e.g. `self` in an inferred-readonly method) -- codegen's `_is_const_indirect`
receiver-const arm, mirrored in THIR's `_f1_is_const`, since C++ overload
resolution picks the const twin regardless of which twin sema resolved
pre-inference.

### Restrictions

- `@auto_readonly` on `__init__`, `@staticmethod`, or `@classmethod` is
  an error (no `self` to propagate through)
- `@auto_readonly` + `@readonly` on the same method is an error (they
  are different concepts)
- `@pure` remains separate (single const overload, no propagation)

## Interior mutability: `unsafe_interior_mutable[Ptr[T]]` fields

`unsafe_interior_mutable[Ptr[T]]` is the dual of `readonly[T]`: it marks one field as
*outside* its owning object's readonly boundary. TPy's readonly is deep --
it propagates through the reachable object graph, including into `Ptr[T]`
fields (a readonly borrow yields readonly sub-borrows). That deepness is
load-bearing (`Rc.get()` returning `self._payload` is only sound because a
`readonly[Rc]` view makes the payload readonly). But some state is genuine
*bookkeeping* that is invisible to a readonly observer -- a refcount reached
through a control-block pointer -- and should be mutable even through a
readonly handle. In C++ this is automatic: `const` does not cross a raw
pointer (`_cell` is `T* const`, `*_cell` stays non-const), exactly like
`std::shared_ptr` being const-copyable. `unsafe_interior_mutable` opts a single field
out of TPy's stricter propagation to match.

**Semantics.** On a field typed `unsafe_interior_mutable[Ptr[T]]`:

- Accessing it through a `readonly[Self]` receiver does **not** wrap the
  pointee in `readonly` (the field keeps its declared mutable shape).
- A non-readonly method call *through* the field (`self._cell.incr()`) does
  **not** demote the enclosing method, so the method can be `@readonly` /
  `@auto_readonly`.
- Reassigning the slot (`self._cell = other`) through a readonly receiver is
  **still rejected** -- that is enforced on the receiver's readonly-ness, which
  `unsafe_interior_mutable` never touches. The hatch is for mutation *beyond* the field
  boundary, not for the field slot itself.

**Soundness contract.** `unsafe_interior_mutable` is an unsafe assertion: the author
guarantees the field's mutation is unobservable through a readonly read.
For `Rc`, the refcount feeds no readonly-observable result, and the payload
(`_payload`) is deliberately *not* interior, so `readonly[Rc[T]].get()` still
yields readonly `T`. Marking observable state interior would create a hole --
keep it to bookkeeping reached through a pointer.

**Implementation.** A transient `InteriorMutableType` marker (parser ->
resolver, mirroring `auto_readonly[...]`) is stripped at field registration
into `FieldInfo.is_interior_mutable`; the stored field type is the plain
`Ptr[T]`, so the marker never flows through codegen or type comparisons. The
flag is read at exactly the readonly-propagation site (`sema/expressions.py`)
and the mutation-rooting site (`sema/methods.py`) -- both already branch on
readonly. Validation (field-only, `Ptr[T]`-only, no `readonly`/`Own`/nested
wrapping) lives in the registration strip pass and `validate_type`.

**Receiver-polarity clone.** `Rc.clone`/`downgrade`/`upgrade` are
`@auto_readonly`: from a `readonly[Rc[T]]` handle they return a
readonly-payload handle (`Own[Rc[readonly[T]]]`), so a TPy caller cannot
launder readonly into mutable `T`. (This is sema-enforced; it is not
backstopped at the C++ type level -- `readonly` strips in `to_cpp`, so a
`@native` function handed the C++ `Rc<T>` could still reach mutable access,
the same as any `readonly`-of-reference. Tracked in `BUGS.md`.) The shared
body builds the per-overload type via
an `auto_readonly[...]` marker in the construction (`Rc[auto_readonly[T]](...)`
-> `Rc[T]` in the mutable half, `Rc[readonly[T]]` in the const half),
transformed by `_clone_auto_readonly` -- a workaround for an inference gap
(generic inference cannot bind a type parameter to a `readonly[T]`; tracked in
`BUGS.md`).

## Future Roadmap

1. **`@readonly` desugaring** -- rewrite as wrapping all param types with
   `readonly[T]` (and optionally return type)
2. **Protocol-level readonly contracts** -- `Sized.__len__`,
   `Sequence.__getitem__` etc. marked readonly in protocol definition;
   conformance checking ensures implementations are readonly
4. **Automatic inference** -- deduce readonly from method body when provable
5. **Escape analysis** -- track readonly references through containers for
   sound narrowing preservation
