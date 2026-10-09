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
from tpy import int32, readonly

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32):
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
| Write through a `Ptr[T]` / `Span[T]` field (`x.p.n = 1`) | Yes (the pointee is not the object's storage) |
| Write through a `Ptr[readonly[T]]` / `Span[readonly[T]]` | No |

### Value types are unaffected

Value types (`int32`, `bool`, `float`, `str`, `char`, etc.) are always copies.
`readonly[int32]` is valid syntax but has no effect -- the parameter is a copy
regardless. This means you never need to annotate value-type parameters with
`readonly`.
The same holds for every copy that holds no reference: a value record
(`ValueType`), a value-repr `Optional` / union, a `Ptr` or `Span` handle.
`readonly` over one is dropped where the type is settled (`typesys.make_readonly` /
`canonical_readonly`: a declared signature, a substituted one, a result), so no
reader sees `readonly[int32]`; a handle's referent (`Ptr[readonly[T]]`) is not a
copy and keeps it, and a `readonly[...]` FIELD is a slot that cannot be reassigned.

### `@readonly` decorator (shorthand)

`@readonly` on a function or method means "all non-value-type parameters are
treated as readonly." It enforces the same mutation restrictions as
`readonly[T]` on each parameter.

```python
@readonly
def observe(b: Box) -> int32:
    return b.value
# Equivalent enforcement to:
# def observe(b: readonly[Box]) -> int32:
```

For methods, `@readonly` makes `self` readonly, which maps to a `const` method
in C++:

```python
class Box:
    @readonly
    def value(self) -> int32:
        return self._value
    # C++: int32_t value() const { return _value; }
```

**Note on return types:** the declared return type is the contract (see
"The return type is the contract" below). An explicit `@readonly` method that
returns its own storage must declare it `-> readonly[T]`; returning what a
`Ptr` field points at, `-> T` hands out a mutable `T&` from a `const` method.
A readonly the user did not spell -- an implicitly-readonly dunder, `@pure`, a
frozen record -- declares its borrowed return readonly along with it.

A method that is only INFERRED const (it never mutates `self`, no decorator)
proves its receiver and nothing else: it is emitted `const`, but a return that
borrows a PARAMETER keeps the declared mutable type and that parameter stays
mutable -- `def pick(self, other: B) -> Rec: return other.m` is
`Rec& pick(B& other) const`, the same signature the free-function spelling
gets. Only the declared `@readonly` makes every parameter readonly and with it
whatever the return borrows from them.

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

The implicit call a dunder stands for (`c[i]`, `k in c`, `for x in c:`,
`with c:`, `await c`) is a method call on the receiver for readonly
purposes: a function that only reads `c[i]` still takes `c` mutable, and a
`readonly[...]` record receiver rejects a dunder that is emitted non-const
-- at the subscript, `in` (through `__contains__`, or `__iter__` when there
is none), `for`, `with` and `await` -- with *Cannot call non-readonly method
'__iter__' on readonly reference*. The verdict is the dunder's EMITTED
const-ness, read in the workspace-wide finalize pass after every module's
const inference: an unannotated `__iter__` that neither mutates nor lends
mutable borrows is inferred const and iterates a readonly source, while a
`@readonly(False)` opt-out or a `@dynamic`-protocol override stays non-const
and is rejected. An explicit `c.method()` is judged earlier, on the declared
`is_readonly` (TODO.md "Converge the explicit method call with the
implicit-dunder chokepoint"). Two gaps remain: a non-mutating `__iter__`
that inference keeps non-const is rejected for mutation it does not do
(BUGS.md#readonly-source-nonmutating-iter-rejected), and a readonly protocol
or type-parameter param (`readonly[Iterable[int32]]`, `readonly[T]`)
iterated over a record with a mutating `__iter__` still fails in C++
(BUGS.md#readonly-protocol-param-mutating-iter-cpp-error). One helper
(`tpyc/sema/receiver_calls.py`) holds the receiver effects of every implicit
spelling.

### Local variable deduction

When a local variable is assigned from a readonly expression, it inherits
readonly status for non-value types:

```python
@readonly
def f(p: Point) -> None:
    alias = p           # alias: readonly[Point] (deduced)
    alias.mutate()      # ERROR: non-readonly method on readonly ref
    v = p.x             # v: int32 (value type, no readonly needed)
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

A method whose reference result follows its receiver gets dual
const/non-const overloads in C++: an `@auto_readonly` method, the implicit
`__deref__` / `__getitem__` / `__span__` pair with a reference result, and a
`@property` getter. A const receiver gets a const reference back, a mutable
receiver a mutable one. A `@readonly` method gets ONE `const` overload whose
result is exactly its declared return type:

```cpp
// @auto_readonly / implicit pair / property getter, non-value result:
const T& method() const;   // readonly receiver -> readonly ref
T& method();               // mutable receiver -> mutable ref

// @readonly, `-> readonly[T]` / `-> T` (a pointee) / a value:
const T& method() const;   T& method() const;   T method() const;
```

At the callsite, C++ overload resolution selects based on the receiver's
constness. Sema enforces readonly at the TurboPython level independently:

```python
def read(c: readonly[Container]) -> int32:
    return c[0]         # calls const overload, result is readonly[int32]
                        # (int32 is value type, so readonly is a no-op)

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
pointer), as a parameter and as a declared result alike: a local bound from
`-> readonly[Point | None]`, a local rebound to it and a return relaying it
are the `const Point*` alias of the source (`const Point* p = h.get();`).

Readonly is access, never shape. Whether a type is an Optional, a
pointer-repr Optional or a pointer-variant union is read through every
access and ownership wrapper in whatever order the wrappers were applied
(`typesys.repr_shape`, with the views `pointer_repr_optional` and
`pointer_variant_union`; an `Own` among the wrappers selects the storage
form, so `Own[Point | None]` is not a pointer-repr Optional). Const-ness
keeps reading the `readonly` wrapper itself (`readonly_access`).

### C++ mapping summary

| TurboPython                       | C++                            |
|-----------------------------------|--------------------------------|
| `p: Point` (param)               | `Point& p`                     |
| `p: readonly[Point]` (param)     | `const Point& p`               |
| `x: int32` (param, value type)   | `int32_t x`                    |
| `x: readonly[int32]` (param)     | `int32_t x` (same, copy)       |
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
- `copy(p)`: copying reads the source and never writes it, so a readonly
  source is valid and the result is a mutable `Own[T]` over independent
  storage. This is the spelling that reaches rule 4's mutable slot; the
  borrow itself still cannot

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
   whose return value is `self`'s own storage -- not what a `Ptr` or view
   `self` holds points at).

3. **Container-mediated aliases** -- resolved by type-embedded refactor.
   ReadonlyType propagates through subscript (`readonly[list[T]][i]` ->
   `readonly[T]`), field access chains, and iteration. It projects onto an
   element that is not a value type, and onto a tuple or Optional that
   HOLDS one (`typesys.py make_readonly` over `readonly_protects`, the one constructor every
   projection site asks; a `Ptr` or a view stops it), so `xs[0][1].n = v` off a
   `readonly[list[tuple[int32, Box]]]` and a write through an unpack target
   of a readonly tuple are refused like the record element's write; a
   tuple of plain values (`tuple[int32, str]`) is copied and takes no
   readonly. A tuple parameter holding a reference is readonly in a readonly
   context (an explicit `@readonly` method, an implicitly readonly dunder,
   `@pure`) like a reference parameter: `__add__(self, o: tuple[Own[Tok],
   Box])` takes `std::tuple<Tok, const Box*>&&` and refuses a write through
   the unpacked `Box`. A readonly tuple follows the scalar rule for its
   reference elements: an `Own[...]` slot (`xs.append(t)`) takes a warned
   copy of the whole tuple, as `ys.append(b)` copies a `readonly[Box]`; any
   other mutable tuple slot checks it element by element, as the literal
   `(o[0], o[1])` is, and refuses the reference element -- a mutable
   parameter with *Cannot pass readonly[Box] as mutable Box in argument 'p'
   (tuple element 0)*, a field or subscript store (refused like `self.g =
   b`) with the same message plus *-- use copy(t) to store a mutable
   copy*. List literals with
   readonly elements infer `PendingList[readonly[T]]` which rejects assignment
   to `list[T]`. Passing `readonly[list[T]]` to `list[T]` param is caught by
   type compatibility. No taint-tracking needed for these cases.

4. **Narrowing preservation is best-effort** -- readonly calls are treated as
   safe for narrowing fact preservation, but non-readonly calls conservatively
   clear subscript narrowing facts even when the container isn't passed as an
   argument (global aliasing scenario). Full soundness for all edge cases
   requires escape analysis.

6. **Declared readonly union returns do not lower** -- a `-> readonly[A | B]`
   return and every binding of one are THIR rejects
   (BUGS.md#declared-readonly-return-lowering-gaps), behind the general
   union-result gap its mutable twin `-> A | B` meets first. An implicitly
   readonly method (a dunder, `@pure`, a frozen record) returning a
   pointer-variant union borrow declares exactly that shape.

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

- **Mutable clone**: `is_readonly=False`, `is_auto_readonly_mutable_clone=True`,
  `clone_of` = the const clone, return type = `strip_alt(original_return_type)`
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

Methods in `IMPLICIT_READONLY_METHODS` (`__len__`, `__eq__`, arithmetic
operators, etc.) are readonly by default: one `const` overload, whose
borrowed result is declared `readonly[...]` with it (see "The return type is
the contract"). The reference-returning accessors among them --
`__getitem__`, `__span__`, `__deref__` (`IMPLICIT_AUTO_READONLY_METHODS`) --
instead get the dual pair when their result is a reference: method expansion
marks the result `auto_readonly[...]` whole, as if the user wrote
`@auto_readonly`. An explicit `@readonly` opts such an accessor back into the
single const overload; `@readonly(False)` opts out of readonly entirely.

The general rule: apply `@auto_readonly` whenever the method returns a
reference type and you want the const overload to return a const variant. Mark
the parts of the return type that should become const with `auto_readonly[T]`:
`Span[auto_readonly[T]]`, `Ptr[auto_readonly[T]]`, `auto_readonly[T]`
for a bare type parameter. An unmarked reference result follows the receiver
whole. Value types (copied on return) need no annotation.

### Interaction with `@overload`

`@auto_readonly` applies per-overload. When a method has multiple
`@overload` stubs, only overloads whose return type benefits from propagation
need dual treatment. Example:

```python
class ArrayList[T, N: int]:
    @overload
    @auto_readonly
    def __getitem__(self, index: int32) -> T: ...                       # T is value-copied; no annotation needed
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
borrowed result does -- so the accessor call itself does not demote the
receiver param (`receiver_calls.call_mutates_receiver`). The actual demotion is
rooted back to the receiver at the mutation site:

```python
def read(o: Outer) -> int32:  return o.b.get().v   # const Outer&  (result only read)
def write(o: Outer) -> None:  o.b.get().v = 9       # Outer&        (result field written)
def mutate(o: Outer) -> None: o.b.get().bump()      # Outer&        (mutating method on result)
```

C++ overload resolution then selects `get()` vs `get() const` automatically
from the receiver's const-ness -- no clone is chosen in sema or codegen.

A resumable frame has to name the result type of such a call, because a frame
field cannot be `auto` the way a sync local can, and the two halves return
different C++ types rather than one type under a `const` (`dict_items_view<K,
const V>` vs `dict_items_view<K, V>`). It names it by deduction, not by
selection: the field is spelled `decltype(<call>)` with a `std::declval`
receiver carrying the const-ness the borrow verdict gives that receiver, so
C++ overload resolution still decides which half runs -- codegen only supplies
the receiver qualifier it would have had at the call site.

Which part of a result follows the receiver is DECLARED (`FunctionInfo.following_components`,
read through `typesys.following_components` at the call's specialization -- a marker on a part
that holds no reference, `auto_readonly[T]` at `T = int32`, follows nothing). What a call lends
of its receiver is one fact, `typesys.receiver_lends`, consumed by the call and the existing loan
machinery, never by the binding site:

- **A handle's referent** (`Ptr[auto_readonly[T]]`, `Span[auto_readonly[T]]`, an `Rc`'s payload):
  every copy of the handle reaches it and nothing tracks the copies, so the CALL demotes its
  receiver (`receiver_calls.call_mutates_receiver`), whatever is done with the result -- a
  read-only use keeps the receiver mutable. A `for` statement's `__iter__` is the exception the
  loop owns: its handle is reached only through the loop variable, whose writes climb to the
  iterable (`call_mutates_receiver(handle_climbs=...)`, keyed on the loop variable's iteration
  loans, `iter_loans._loop_var_climbs`; a comprehension or an `in` test takes no exemption).
  Known limit: the climb misses a write made through a callee the loop variable is passed to
  (BUGS.md#loop-element-callee-mutation-not-propagated) or inside a nested def
  (BUGS.md#loop-var-write-routes-miss-write-set), and a comprehension variable does not climb at
  all (BUGS.md#comprehension-loop-var-mutation-not-propagated).
  Both the call-site demotion and the loop exemption are interim approximations of one rule:
  the receiver is mutated exactly when a write reaches it through the handle. The intended
  end state stays in sema (MIR runs after inference and cannot feed it back): (1) close the
  three climb gaps above so the loop exemption is exact; (2) generalise the loop variable's
  trace to any local holding a handle from a declared `@auto_readonly` call, recorded by the
  order-free whole-function pre-pass, with an ALLOW-LIST of harmless uses (a value-field read,
  `is None` / comparison, a `readonly[...]` parameter, a declared or implicit `@readonly` call)
  -- every other use (a write, a non-readonly call, passing, storing, capturing, returning,
  yielding) demotes as today, so the rule stays sound and only gains precision; read-only uses
  of `Ptr`-returning accessors and of `Rc.clone` / `Weak.upgrade` then keep the receiver const.
  Beyond that, exact (annotation-free, least-solution) inference is a MIR-based re-layering:
  docs/CONST_INFERENCE_TARGET_DESIGN.md.
  An `ArrayList` / span loop variable is the element itself (the `SpanIter` step lends it), so
  a write through it reaches the container and demotes the iterated source like any loop write.
- **A reference** (the whole result, a tuple element): the call loans its receiver to whatever
  binds the result (`_register_call_result_borrow` reads `typesys.result_borrow_sources`; a
  `for` over the call files it as the iteration's loan), so a write through any place derived
  from it -- a local, a walrus, a rebind, an unpack, a return, a loop variable -- climbs that
  loan; a read-only alias leaves the receiver const and binds const, since C++ picks the clone by
  the receiver. A receiver that is itself such a call lends ITS receiver (`w.me().ref()` loans
  `w`, `sema.context.lent_receivers`), and the THIR const verdict follows the same chain. A
  place derived through a field of the result (`x = o.b.get().leaf`) is bound eagerly: the
  receiver is demoted at that binding.

A call form is recognized by its resolved callee, so an explicit call, a subscript dispatched to
`__getitem__`, an implicit dunder and a property read all take the same path; an implicit dunder
on a readonly receiver runs the const clone, which lends nothing writable, and a subscript on a
readonly receiver is typed off that clone's result (`h[0]` off a `Ptr[auto_readonly[A]]`
`__getitem__` is a `Ptr[readonly[A]]`). Not yet: a `@property` getter with a component
marker (BUGS.md#auto-readonly-property-component-pair) and an `@auto_readonly` operator dunder
(BUGS.md#auto-readonly-operator-dunder-pair).

### Restrictions

- `@auto_readonly` on `__init__`, `@staticmethod`, or `@classmethod` is
  an error (no `self` to propagate through)
- `@auto_readonly` + `@readonly` on the same method is an error (they
  are different concepts)
- `@pure` remains separate (single const overload, no propagation)

## Pointers and spans are access boundaries

`readonly` protects the storage a reference names: the object, its fields,
the elements of the containers it owns. It does not reach through a `Ptr[T]`
or a `Span[T]` (or any other borrowing view): those are value-typed handles,
and what they point at is not the storage of whatever holds them. This is the
C++ rule (`T* const` derefs to a mutable `T`; a `const std::span<T>` hands out
mutable elements) and Rust's raw-pointer rule.

```python
class M:
    _a: Ptr[A]
    xs: Span[int32]

    def tick(self) -> None:       # void tick() const;
        self._a.bump()

    def poke(self) -> None:       # void poke() const;
        self.xs[0] += 1

def via_ro(m: readonly[M]) -> None:
    m._a.n += 1                   # OK: the pointee is not m's storage
    m._a = other                  # error: the field itself is m's storage
```

To protect what a handle points at, say so in its type: `Ptr[readonly[T]]`,
`Span[readonly[T]]`. A `Ptr[readonly[T]]` derefs into a `T` slot only where
that slot copies the pointee (a value `T`); a reference slot -- a local, a
return, a tuple element of a reference type -- is a type mismatch, since it
would hand the readonly pointee out mutably. `readonly[Ptr[T]]` and
`readonly[Span[T]]` protect only the handle (a copy): on a parameter they have
no effect, exactly like `readonly[int32]` (a declared signature and a
substituted result drop a readonly that protects nothing,
`typesys.canonical_readonly`); on a FIELD
they make the slot readonly -- it cannot be re-pointed after construction --
while what it points at stays writable.

```python
class H:
    ro: readonly[Ptr[A]]

    def poke(self) -> None:
        self.ro.n += 1            # OK: the pointee stays writable
        self.ro = other           # error: Cannot assign to readonly field 'ro'
```

The rule has two halves, decided once per write by the mutation mode of the
place walkers (`_root_name_of_expr` / `addr_taken_roots` given the expression
types, `tpyc/sema/context.py`); the const walkers of every layer step the
same way (`value_category.const_place`, one step predicate,
`typesys.indirection_referent_readonly`):

- **The holder is not written.** A write whose place is reached through a
  handle read out of some object's storage (`self._a.n = 1`,
  `m.xs[0] += 1`, `self._ps[0].bump()`) does not mutate that object, so it
  neither demotes an inferred-const method nor needs a mutable receiver.
- **The referent effect stays with the handle's name.** A write through a
  handle that is a local or a parameter (`p.n = 1`, `s[0].clear()`) is
  credited to that name, so the callee's facts say it writes through that
  parameter. Where the handle was FORMED over some storage -- a record
  coerced to a `Ptr`, `take_ptr(x)`, a mutable `Span` over a container -- the
  formation itself demotes that storage. A loop variable that is itself a
  handle (`for p in ps` over `list[Ptr[A]]`) is written only through its
  referent, so the write does not climb to the iterable, and a call on a
  handle receiver runs on its referent, not on the storage the handle was
  read out of.

Lifetimes are not part of this rule: a borrow taken through a handle stays
conservatively tied to the holder for the borrow checker.

The guarantees of the handle-holding library types (`Rc`, `Arc`, `Weak`,
`Box`, `Mutex`, `RwLock` and their guards) rest on their public accessors,
which declare their payload as following the receiver (`auto_readonly[T]`,
see below). Their `_`-prefixed implementation fields are private by Python
convention only: reaching `_payload` around `get()` (or around a lock)
bypasses the guarantee. A warning for such a read is planned (TODO.md).

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

History: from 2026-02-27 to 2026-10-05 readonly was DEEP through a `Ptr`
field (a `Ptr[T]` field read through a readonly receiver became
`Ptr[readonly[T]]`), with an `unsafe_interior_mutable[Ptr[T]]` field marker to
opt bookkeeping pointers back out. Everything else (params, locals, elements,
spans) was already shallow, and const inference had to follow pointers to
match the field rule. The marker is retired: on a `Ptr` field it is an error
(it is redundant), and the name is reserved for an inline field that stays
mutable through a readonly owner (a C++ `mutable` member, TODO.md).

## The return type is the contract

A body's declared return type is exactly what its signature hands out; it is
never projected from the receiver's const-ness. `-> A` returns a mutable `A&`,
`-> readonly[A]` a `const A&`, element by element for a tuple
(`-> tuple[readonly[A], B]` is `std::tuple<const A*, B*>`). A nullable result
`X | None` follows exactly the rules of `X`: `-> tuple[readonly[A], B] | None`
is `std::optional<std::tuple<const A*, B*>>`, an unmarked
`-> tuple[A, B] | None` takes the bare twin's per-element error, and an
`@auto_readonly` or `@property` result marks it per element, as the bare tuple.

- An explicit `@readonly` method returning a pointee at `-> A` emits
  `A& get() const`.
- An explicit `@readonly` method returning its OWN storage at a mutable return
  type is an error: `self.own` is `readonly[A]` there, and it does not fit an
  `A` (*Cannot return readonly[A] at mutable return type 'A'; declare the
  return as 'readonly[A]'*). The same holds for a `@readonly` function
  returning a parameter's storage.
- An inferred method returning a writable borrow of its own storage is not
  const (the return grants write access); one returning a pointee is.
- A readonly the user did not spell -- an implicitly-readonly dunder
  (`__add__` returning `self` or a parameter), `@pure`, a frozen record --
  wraps what its declared return borrows in `readonly[...]` where that
  readonly is applied (registration; `typesys.readonly_result`, a tuple's
  reference elements one by one): `const Acc&`, and `c = a + b; c.n = 5` is a
  located error. Only an explicit `@readonly` / `readonly[...]` makes the
  user own the return type.
- The only results that follow the receiver are the declared ones: an
  `auto_readonly[...]` marker, the implicit `__deref__` / `__getitem__` /
  `__span__` pair, a `@property` getter, and an `@auto_readonly` method with no
  marker in its result, whose reference result is marked whole. The const
  clone's own declared return carries the readonly, so the return rule holds
  in both clones. An unmarked part of a mixed result (`T | Span[auto_readonly[T]]`)
  returning the receiver's storage is an error in the const clone, and the
  fix names the marker (*mark that part 'auto_readonly[T]'*), never the
  clone's readonly projection.

**One fact: declared result access.** Whether a call's result is readonly is
read off the callee's declared return (`typesys.declared_result_readonly`),
nowhere else: sema types the call with it (so a write through it is a
located error), the signature renders it, and every binding -- a local, a
pre-declared or hoisted local, a `with` / walrus / `match` / closure /
`@error_return` binding, a tuple capture, a returned borrow's escape -- reads
it. The one other source of a const result is the receiver: a callee that
declares its whole result as following the receiver
(`typesys.result_follows_receiver_root`) is bound const where its receiver is
emitted const, since C++ picks the const clone; a bodyless native accessor
that declares a receiver borrow (`list.__getitem__`, `dict.get`) follows it
the same way.

**Per-component following.** Which parts of a result follow the receiver is
decided where the markers sit, in `_clone_auto_readonly`
(`typesys.auto_readonly_components`, stamped on both clones as
`following_components`): the whole result, a `Ptr` pointee, a `Span` element,
a tuple element, a union member. A marker on a part that holds no reference
at the call's specialization is inert (`typesys.following_components`). What a
call lends of its receiver (`typesys.receiver_lends`) is consumed by the call
and the loan machinery, never by a binding site: a handle's referent demotes
the receiver at the call, a reference loans the receiver to whatever binds the
result (see "Usage-dependent receiver const-ness").

The two clones of a pair whose whole result follows the receiver are ONE
callable for THIR and MIR: they publish the receiver-neutral result
(`typesys.receiver_neutral_return`), and each call binds its access at its
own receiver (`call_contract.bound_result`). A pair that differs below the
whole result (a component marker) is two callables.

## Future Roadmap

1. **`@readonly` desugaring** -- rewrite as wrapping all param types with
   `readonly[T]` (and optionally return type)
2. **Protocol-level readonly contracts** -- `Sized.__len__`,
   `Sequence.__getitem__` etc. marked readonly in protocol definition;
   conformance checking ensures implementations are readonly
4. **Automatic inference** -- deduce readonly from method body when provable
5. **Escape analysis** -- track readonly references through containers for
   sound narrowing preservation
