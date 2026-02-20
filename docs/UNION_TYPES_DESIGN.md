# Union Types and Type Narrowing

## Vision

TurboPython should support union types (`A | B | C`) with compile-time
`isinstance()` narrowing, mapping to `std::variant<A, B, C>` in C++. This
provides type-safe alternatives without inheritance or dynamic dispatch, and
generalizes the existing Optional narrowing (`T | None`) to arbitrary type
unions.

```python
class Circle:
    radius: float

class Rect:
    width: float
    height: float

Shape = Circle | Rect

def area(s: Shape) -> float:
    if isinstance(s, Circle):
        return 3.14159 * s.radius * s.radius
    elif isinstance(s, Rect):
        return s.width * s.height
```

## Roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | `UnionType` in type system + parser accepts `A \| B \| C` | **Done** |
| **Phase 2** | Sema: type compatibility (member -> union assignment) | **Done** |
| **Phase 3** | `isinstance()` special form + narrowing facts | **Done** |
| **Phase 4** | Narrowing generalization (`narrowed_types` dict) | **Done** |
| **Phase 5** | Codegen: `std::variant`, `holds_alternative`, `std::get` | **Done** |
| **Phase 6** | Tests | **Done** |
| **Phase 7** | `A \| B \| None` with `std::monostate`, `is None`/`is not None` on unions | **Done** |
| **Phase 8** | Type aliases (`Shape = Circle \| Rect`) | Not designed |
| **Phase 9** | Equality `==`/`!=` on unions (if all members support it) | Not designed |
| **Phase 10** | Unify `narrowed_types` with `non_none_vars` | **Done** |
| **Phase 11** | `assert isinstance(x, T)` codegen for unions | **Done** |
| **Later** | Assignment narrowing: `v = Rect(...)` narrows `v: A \| B` to `Rect` when RHS type is a known member | Not designed |
| **Later** | While-loop condition narrows union types (codegen extraction) | Not designed |
| **Later** | `isinstance(x, (A, B))` tuple form (narrow to subset of union) | Design only |
| **Later** | Exhaustiveness checking (isinstance chains + match/case) | Design only |
| **Later** | isinstance on non-name expressions (`x.field`, `x[i]`) | Not designed |
| **Later** | Common-method dispatch (call shared method without narrowing) | Design only |
| **Later** | Copy/nocopy enforcement for unions with `@nocopy` members | Not designed |
| **Later** | Generic unions (`Union[T, U]` in generic context) | Not designed |
| **Later** | `match`/`case` structural pattern matching | Design only |
| **Later** | Recursive unions / ADT patterns | Design only |

## Design Principles

1. **Python-compatible syntax**: `A | B | C` uses standard Python 3.10+ type
   union syntax. `isinstance(x, T)` is the standard narrowing mechanism.

2. **Zero-cost narrowing**: all type narrowing happens at compile time. After
   `isinstance(s, Circle)`, `s.radius` compiles to a direct field access on the
   narrowed `std::get<Circle>(s)` -- no runtime dispatch overhead.

3. **OptionalType stays**: `T | None` remains `OptionalType(T)` for backward
   compatibility. The new `UnionType` only applies to multi-type unions without
   None, or is wrapped inside `OptionalType` when None is present.

4. **Value-type semantics depend on members**: `std::variant` is stack-allocated
   and fixed-size. If all members are value types (`int | bool`), the union is
   a value type (passed as `const&`, no pointer-local). If any member is a
   non-value type (`Dog | Cat` with records), the union is non-value (passed as
   `&` for mutable access to narrowed members, uses pointer-local when
   reassigned).

---

## Phase 1: UnionType in the Type System + Parser

### New type class

File: `tpyc/typesys.py` (near `OptionalType` at line 708)

```python
@dataclass(frozen=True)
class UnionType(TpyType):
    """Union of multiple types: A | B | C.

    Members are stored in canonical sorted order for equality/hashing.
    """
    members: tuple[TpyType, ...]

    def to_cpp(self) -> str:
        inner = ", ".join(m.to_cpp() for m in self.members)
        return f"std::variant<{inner}>"

    def is_value_type(self) -> bool:
        return True

    def to_cpp_param(self, name: str) -> str:
        return f"const {self.to_cpp()}& {name}"

    def to_cpp_const_param(self, name: str) -> str:
        return f"const {self.to_cpp()}& {name}"

    def to_cpp_return(self) -> str:
        return self.to_cpp()

    def to_cpp_return_const(self) -> str:
        return self.to_cpp()

    def __str__(self) -> str:
        return " | ".join(str(m) for m in self.members)
```

### Normalization rules

A helper function `make_union(*types)` builds a normalized `UnionType`:

1. **Flatten**: if any member is itself a `UnionType`, splice its members in
2. **Deduplicate**: remove duplicate types (using structural equality)
3. **Sort**: canonical order for deterministic `__eq__`/`__hash__`
4. **Collapse**: if one member remains after dedup, return that type directly
   (not wrapped in `UnionType`)
5. **None handling**:
   - `T | None` (single non-None type) -> `OptionalType(T)` (existing, unchanged)
   - `A | B | None` (2+ non-None types) -> `UnionType(A, B, NoneType)`
     (`NoneType` is kept as a regular member, mapped to `std::monostate`)

Examples:
- `(A | B) | C` -> `UnionType(A, B, C)`
- `A | A | B` -> `UnionType(A, B)`
- `A | None` -> `OptionalType(A)` (existing behavior, unchanged)
- `A | B | None` -> `UnionType(A, B, NoneType)`

### Canonical ordering

Members are sorted by a stable key (e.g., their `__str__()` representation or
a dedicated `sort_key()` method). This ensures `A | B` and `B | A` produce the
same `UnionType` and the same C++ `std::variant<A, B>`.

### is_value_type() rationale

`UnionType.is_value_type()` returns `all(m.is_value_type() for m in members)`.
`std::variant` is always stack-allocated, but the value-type classification
controls parameter passing and the pointer-local variable model:

- **All value-type members** (`int | bool`): value type -- passed as `const&`,
  no pointer-local indirection
- **Any non-value member** (`Dog | Cat`): non-value type -- passed as `&`
  (mutable ref, so `std::get` yields mutable access), uses pointer-local (`T*`)
  when reassigned

### Parser Changes

File: `tpyc/parse/parser.py` (lines 739-752, `_parse_type_annotation`)

Currently, the BitOr handler only accepts `T | None` or `None | T`, raising
`ParseError("Union types not yet supported")` for anything else.

### New logic

```
_parse_type_annotation(BinOp(BitOr)):
    Collect all arms from the left-associative BinOp chain:
        A | B | C  parses as  BinOp(BinOp(A, |, B), |, C)
    Recursively flatten left side, append right side.

    Separate None arms from type arms.

    If no type arms: error (None | None is meaningless)
    If 1 type arm + None: OptionalType(T)  (existing behavior)
    If 2+ type arms + no None: UnionType(A, B, C)
    If 2+ type arms + None: UnionType(A, B, NoneType)  (None as variant member)

    Apply readonly normalization:
        readonly[A] | readonly[B] -> readonly[A | B]
        readonly[A] | B -> error (mixed readonly)
        readonly[T] | None -> readonly[T | None]  (existing)
```

The parser calls `make_union()` for normalization.

---

## Phase 2: Sema -- Type Compatibility

File: `tpyc/sema/compatibility.py`

### New rules for `check_type_compatible()`

| Source | Target | Rule |
|--------|--------|------|
| `T` | `Union[..., T, ...]` | OK -- member wraps into union |
| `Union[A, B]` | `Union[A, B, C]` | OK -- subset union |
| `Union[A, B, C]` | `Union[A, B]` | Error -- can't narrow implicitly |
| `Union[A, B]` | `A` | Error -- must narrow with isinstance first |
| `Union[A, B]` | `Optional[Union[A, B]]` | OK -- like T -> Optional[T] |

### Member-to-union assignment

When the source type `T` is assigned to a target of type `Union[..., T, ...]`,
the sema accepts it. Codegen relies on C++ implicit conversion from `T` to
`std::variant<..., T, ...>`.

### Operations on union types

Initially, unions are opaque -- no field access, no method calls, no operators
until narrowed. The only permitted operations are:
- Assignment (member -> union, union -> union with subset check)
- `isinstance()` check (Phase 4)
- Equality comparison `==`/`!=` (if all members support it) -- can be deferred

Attempting `s.radius` on a `Shape` without narrowing produces a sema error:
"Cannot access field 'radius' on union type 'Circle | Rect'; use isinstance() to narrow first"

---

## Phase 3: isinstance() as Compile-Time Narrowing

### Special form in sema

File: `tpyc/sema/calls.py` (`analyze_call`)

`isinstance(x, T)` is recognized as a special form, similar to `super()`:
- Intercept at the top of `analyze_call()` when `expr.func == "isinstance"`
- First argument: must be a name (variable) -- initially restrict to simple names
- Second argument: must be a TYPE reference (not a value expression)
- Return type: `BoolType`
- Side effect: annotate the call node with narrowing information for codegen

### Validation rules

isinstance checks **exact union membership** -- not subtypes. If `Dog`
extends `Animal`, `isinstance(x, Animal)` on `x: Dog | Cat` does NOT match
both. Only exact members of the union are valid isinstance targets. Subtype
isinstance is a future concern (tied to dynamic dispatch / virtual methods).

```
isinstance(x, T) where x: Union[A, B, C]:
    T must be one of A, B, C exactly (or a subset via tuple form)
    If T is not a member: error "type 'T' is not a member of union 'A | B | C'"

isinstance(x, T) where x: Optional[T]:
    Equivalent to x is not None (narrows to T)
    Note: currently handled by x is not None -- isinstance is an alternative syntax

isinstance(x, T) where x is not a union/optional:
    Warning or error: isinstance is trivially true/false
    (can defer this -- not blocking)
```

### Narrowing facts

The isinstance call produces **type-aware narrowing facts** used in Phase 5.
In the `if` true-branch, `x` is known to be `T`. In the `else` false-branch,
`x` is known to be `Union[A, B, C] - T` (the remaining members).

Example:
```python
s: Circle | Rect | Triangle = ...
if isinstance(s, Circle):
    # s: Circle
    print(s.radius)
elif isinstance(s, Rect):
    # s: Rect
    print(s.width)
else:
    # s: Triangle (only one member left)
    print(s.sides)
```

### Parsing the second argument

`isinstance(x, SomeType)` -- the second argument is a bare name that refers to
a type, not a value. The parser produces a regular `TpyCall` with `TpyName` args.
Sema resolves the second argument as a type reference:
- Look up the name in the type registry (records, builtins)
- If not found as a type: error "isinstance() second argument must be a type"
- Store the resolved type on the expression node for codegen

### Multi-type isinstance (future)

`isinstance(x, (A, B))` -- narrows to `A | B`. The second argument is a tuple
of types. This is standard Python but can be deferred to a later phase.

---

## Phase 4: Narrowing Generalization

This is the core change that makes union narrowing (and future narrowing
extensions) possible. It generalizes the current Optional-only narrowing
system.

### Current state (after Phase 10 unification)

File: `tpyc/sema/context.py`

```python
narrowed_types: dict[str, TpyType]  # variable -> current narrowed type (Optional + Union)
```

File: `tpyc/sema/narrowing.py`

`condition_type_facts()` returns `(true_facts: dict[str, TpyType], false_facts: dict[str, TpyType])`
-- maps variable names to their narrowed types.

File: `tpyc/sema/init_tracker.py`

`FlowState` is a 6-tuple carrying all flow-sensitive state including
`narrowed_types`.

### New design: `narrowed_types`

Add to `SemanticContext`:

```python
narrowed_types: dict[str, TpyType]   # variable -> current narrowed type
```

When `isinstance(x, T)` is True, `narrowed_types["x"] = T`. When the
analyzer queries the type of `x`, it checks `narrowed_types` first -- if
present, returns the narrowed type instead of the declared type.

### Relationship to Optional narrowing

Optional narrowing is a special case of type narrowing:
- `x is not None` where `x: Optional[T]` sets `narrowed_types["x"] = T`
- `if x:` (truthiness) on Optional sets `narrowed_types["x"] = T`
- Reassignment to a non-None value re-narrows: `narrowed_types["x"] = T`

The old `non_none_vars: set[str]` was removed in Phase 10; all narrowing
(Optional and Union) now flows through `narrowed_types`.

### FlowState

`narrowed_types` is the 7th element of the FlowState tuple:

```python
FlowState = tuple[
    frozenset[str],                    # definitely_assigned
    bool,                              # init_terminated
    frozenset[str],                    # rvalue_vars
    frozenset[str],                    # param_provenance_vars
    frozenset[str],                    # non_null_ptr_vars
    frozenset[tuple[str, TpyType]],    # narrowed_types (as frozenset of pairs)
]
```

### Branch merging

In `merge_branches()`:

```
narrowed_types merge rules:
    - Both branches reach merge: intersection (keep only common narrowings)
    - One branch terminates (return/raise): take the other's narrowings
    - Both terminate: union of narrowings
```

For intersection: a narrowing is "common" if both branches narrow the same
variable, and the narrowed types are equal. If they differ (e.g., one branch
narrows to `Circle`, other to `Rect`), drop the narrowing for that variable.

### Fact invalidation

When a variable is reassigned, remove it from `narrowed_types`:

```python
def update_after_write(self, name, target_type, rhs_type=None, rhs_expr=None):
    self.ctx.narrowed_types.pop(name, None)
    # For Optional targets, re-narrow if RHS is provably non-None
```

### Condition facts for isinstance

`condition_type_facts()` returns type-aware facts for isinstance and
Optional narrowing conditions:

```python
def condition_type_facts(self, condition: TpyExpr
) -> tuple[dict[str, TpyType], dict[str, TpyType]]:
    """Get (true_facts, false_facts) for isinstance narrowing."""
```

For `isinstance(x, T)` where `x: Union[A, B, C]`:
- true_facts: `{"x": T}`
- false_facts: `{"x": Union[remaining members]}`
  (if only one member remains, collapse to that type directly)

### else-branch narrowing (negative narrowing)

After `if isinstance(x, Circle): ...`, the else branch knows `x` is NOT
`Circle`. If `x: Circle | Rect | Triangle`, the else branch narrows to
`Rect | Triangle`. This comes from the false_facts above.

Chained elif example:
```python
if isinstance(s, Circle):
    pass  # s: Circle
elif isinstance(s, Rect):
    pass  # s: Rect  (here s was already Rect | Triangle from else-narrowing)
else:
    pass  # s: Triangle
```

Each elif re-narrows from the previous else-narrowed type.

---

## Phase 5: C++ Code Generation

### Type mapping

File: `tpyc/codegen_cpp/types.py`

```
UnionType(A, B, C)  ->  std::variant<A_cpp, B_cpp, C_cpp>
```

### Variable declaration

Union variables use normal value-type declaration (no pointer-local):
```cpp
std::variant<Circle, Rect> s = Circle{3.0};
```

### isinstance codegen

File: `tpyc/codegen_cpp/expressions.py`

`isinstance(x, T)` generates:
```cpp
std::holds_alternative<T>(x)
```

### Narrowed variable codegen

After isinstance narrowing, sema records the narrowed type for the variable.
Codegen must emit `std::get<T>(x)` for **any** use of a narrowed union
variable in a value context -- not just field access, but also function
arguments, return statements, and assignment to member-typed variables.
This is analogous to how `gen_expr_deref` emits `(*name)` for pointer-locals.

```python
def process_circle(c: Circle) -> None: ...

if isinstance(s, Circle):
    print(s.radius)        # field access
    process_circle(s)      # function argument
    c: Circle = s          # assignment to member type
    return s               # return as member type
```

Generates:
```cpp
if (std::holds_alternative<Circle>(s)) {
    auto& __s = std::get<Circle>(s);
    tpy::print(__s.radius);
    process_circle(__s);
    Circle c = __s;
    return __s;
}
```

The approach is to extract once at the top of the narrowed block into a
reference variable (`auto& __s = std::get<Circle>(s)`) and replace all
uses within that scope. This avoids repeated `std::get` calls and produces
cleaner C++.

### Assignment from member type

```python
s: Shape = Circle(3.0)
```

Generates:
```cpp
std::variant<Circle, Rect> s = Circle{3.0};
```

C++ handles the implicit conversion from `Circle` to
`std::variant<Circle, Rect>` natively.

### Return value

```python
def make_shape() -> Circle | Rect:
    return Circle(3.0)
```

Generates:
```cpp
std::variant<Circle, Rect> make_shape() {
    return Circle{3.0};
}
```

### Runtime header

`<variant>` is part of C++17 standard library (already available with C++23
requirement). May need to add `#include <variant>` to `tpy.hpp` if not
already transitively included. No new runtime helpers needed -- `std::variant`,
`std::holds_alternative`, and `std::get` cover all operations.

---

## OptionalType Interaction

### T | None -- no change

`T | None` remains `OptionalType(T)`, mapping to `std::optional<T>` (value
types) or `T*` (non-value types). Existing code is unchanged.

### A | B | None

`A | B | None` normalizes to `UnionType(A, B, NoneType)` (not wrapped in
`OptionalType`).

C++ representation: `std::variant<A, B, std::monostate>`.

This means:
- `NoneType` maps to `std::monostate` in the variant
- `x = None` generates `x = std::monostate{}`
- `x is None` generates `std::holds_alternative<std::monostate>(x)`
- `x is not None` generates `!std::holds_alternative<std::monostate>(x)`
  and narrows `x` to `UnionType(A, B)` (the remaining non-None members)
- isinstance narrows further within the non-None members

**Decision: use `std::variant<A, B, std::monostate>` -- no `std::optional`
wrapping.** This keeps a single container and avoids double-wrapping overhead.
`std::monostate` is the None sentinel.

### is None / is not None on union-with-None

`x is None` and `x is not None` work on unions containing `NoneType`:

```python
x: int | str | None = ...
if x is not None:
    # x: int | str  (NoneType removed from union)
    if isinstance(x, int):
        # x: int
```

Codegen:
- `x is None` -> `std::holds_alternative<std::monostate>(x)`
- `x is not None` -> `!std::holds_alternative<std::monostate>(x)`

Narrowing: `x is not None` narrows by removing `NoneType` from the union
members. If only one non-None member remains, the narrowed type collapses
to that member directly (not a single-member union).

### isinstance on Optional

`isinstance(x, T)` where `x: T | None` narrows `x` to `T`, equivalent to
`x is not None`. This provides isinstance as an alternative narrowing syntax
for Optional types. Implementation: `_isinstance_facts()` handles
`OptionalType` the same way as `UnionType` -- both set
`narrowed_types["x"] = T`.

---

## Future: match/case

Python 3.10 structural pattern matching. Not implemented now, but the union
type system is designed to support it.

### Syntax

```python
match s:
    case Circle(radius=r):
        return 3.14159 * r * r
    case Rect(width=w, height=h):
        return w * h
```

### Parser

`match`/`case` are soft keywords in Python 3.10+. The AST produces
`ast.Match` with `ast.match_case` nodes. Currently falls through to
ParseError in the TurboPython parser.

### Pattern types

| Pattern | Python AST | Meaning |
|---------|-----------|---------|
| `case Circle():` | `MatchClass` | isinstance check |
| `case Circle(radius=r):` | `MatchClass` with keyword patterns | isinstance + field binding |
| `case x:` | `MatchAs` with name | capture (always matches) |
| `case _:` | `MatchAs` without name | wildcard (always matches) |
| `case 42:` | `MatchValue` | literal comparison |
| `case None:` | `MatchSingleton` | None check |

### Codegen

Translates to `switch (x.index())` with `std::get<N>(x)` in each case arm.
Since we control canonical member ordering, the index-to-type mapping is
deterministic. This is simpler and more efficient than `std::visit` (avoids
same-return-type requirement and lambda overhead).

```cpp
switch (s.index()) {
    case 0: { auto& v = std::get<0>(s); /* Circle branch */ break; }
    case 1: { auto& v = std::get<1>(s); /* Rect branch */ break; }
}
```

### Exhaustiveness

For union types, the compiler can verify that all members are covered by
case patterns. Missing members without a wildcard `case _:` produce a
warning or error.

---

## Future: Algebraic Data Types

Union types enable ADT-like patterns without special syntax:

```python
class Leaf:
    value: int

class Branch:
    left: Tree
    right: Tree

Tree = Leaf | Branch
```

`Tree` is a type alias for `Leaf | Branch`. Combined with match/case and
exhaustiveness checking, this gives full ADT functionality.

### Recursive unions

The example above has `Tree` referencing itself through `Branch.left` and
`Branch.right`. This creates a recursive type:
`std::variant<Leaf, Branch>` where `Branch` contains `Tree` (the variant
itself). C++ `std::variant` cannot contain itself directly (incomplete type).

Solutions:
- Box/pointer indirection: `Branch` stores `std::unique_ptr<Tree>` or similar
- Forward declaration + out-of-line definition
- This is a hard problem -- defer to when Box[T] / heap allocation is designed

### Closed vs. open unions

All unions are currently "open" -- any types can be combined. A potential
future extension is closed unions where the compiler enforces that only
specific types are in the union:
- Type alias: `Shape = Circle | Rect` -- the alias is closed (only Circle and
  Rect), but individual types are still usable independently
- Exhaustiveness: closed aliases enable exhaustive match checking

A `@sealed` decorator (restricting subclasses to the same module) is NOT part
of standard Python (no accepted PEP). It could be a TurboPython-specific
extension in the distant future, but is not part of this design.

### Common-method shortcut

If all members of a union implement the same method (e.g., all have
`__str__()` or a shared protocol method), calling it without narrowing
could be allowed -- the compiler would generate dispatch to each member's
implementation. Not for initial phases, but a natural optimization once
unions and protocols are both mature.

---

## Future: Exhaustiveness Checking

### In match/case

For `match s:` where `s: Circle | Rect | Triangle`, the compiler checks that
all three members appear in case patterns. Missing members without
`case _:` produce a diagnostic.

### In if/elif isinstance chains

Optional lint: detect when an if/elif chain tests all union members and could
be a match statement. Or enforce exhaustiveness on isinstance chains when the
last branch is not a plain `else:`.

---

## Current Limitations

- **isinstance only on simple names**: `isinstance(x, T)` works for variable
  names only, not `x.field` or `x[i]`.

- **No `isinstance(x, (A, B))` tuple form**: narrowing to a subset of union
  members via tuple syntax is future work.

- **While-loop union narrowing codegen**: condition-proven type facts are now
  preserved in loops (via `condition_type_facts`), but codegen doesn't emit
  `std::get<T>()` extraction for loop-body union narrowing.

---

## Deferred Items

- **Type aliases**: `Shape = Circle | Rect` as a named alias for a union type.
  Not needed for Phase 1 -- inline `Circle | Rect` in annotations works.
  Add type alias support as a separate feature later.

- **Copy/nocopy enforcement**: if any union member is `@nocopy`, the whole
  `std::variant` is non-copyable. This should be enforced in sema (error on
  copy of union containing nocopy member), but can be deferred to a later
  phase -- C++ will catch it in the meantime.

- **Generic unions**: `Union[T, U]` in generic function/class context.
  Defer until needed.

---

## Implementation Notes

### Key files to modify

| File | Change |
|------|--------|
| `tpyc/typesys.py` | Add `UnionType` class, `make_union()` helper |
| `tpyc/parse/parser.py` | Lift BitOr restriction, collect union arms |
| `tpyc/sema/context.py` | Add `narrowed_types: dict[str, TpyType]` |
| `tpyc/sema/narrowing.py` | Add isinstance fact extraction, type-aware narrowing |
| `tpyc/sema/init_tracker.py` | Extend FlowState, merge narrowed_types |
| `tpyc/sema/compatibility.py` | Union assignability rules |
| `tpyc/sema/calls.py` | isinstance special-form handling |
| `tpyc/sema/expressions.py` | Query narrowed_types for expression types |
| `tpyc/codegen_cpp/types.py` | UnionType -> std::variant mapping |
| `tpyc/codegen_cpp/expressions.py` | isinstance -> holds_alternative, narrowed access |
| `tpyc/codegen_cpp/statements.py` | Union variable declarations |

### Testing strategy

1. **Parser tests**: `A | B`, `A | B | C`, `A | B | None`, nested unions
2. **Sema error tests**: field access on un-narrowed union, isinstance with non-member
3. **Sema success tests**: member -> union assignment, isinstance narrowing, else-branch narrowing
4. **Codegen tests**: std::variant declaration, holds_alternative, std::get
5. **Execution tests**: full round-trip compile-and-run with union values and isinstance branching
