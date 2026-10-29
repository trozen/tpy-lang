# Overloading: `typing.overload` and `tpy.dispatch` (B10)

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
| - | `slice` type integration (`__getitem__` with `int32 \| slice`) | Done |

## Future Extensions

| Feature | Notes |
|---------|-------|
| Partial union stubs | Stub takes `Dog \| Cat` when impl has `Dog \| Cat \| Bird`. Call resolution takes a member at a union stub parameter (a union injection, "Call resolution"); the stub's specialization does not lower yet (`sig.overload_set.narrow_param`, and an `isinstance` over the stub's union `if.cond_facts_unmirrored`). |
| Non-union overloads | Overloads distinguished by coercion-compatible types (e.g., `int32` vs `float`). Needs a different dispatch mechanism since isinstance doesn't apply. Start with union-only -- it's the natural pattern. |
| Overload on arity | Different parameter counts per stub. A free function's variants get distinct C++ names (`f__int32`, `f__int32__int32`, see "C++ names of variants"); a method's map to C++ overloads with different parameter counts. |
| Dead branch elimination generalization | Extend dead-branch elimination beyond overload dispatch -- e.g. when isinstance/match has only a single possible type, eliminate the check entirely even without `@overload`. |
| `register`-style `@dispatch` variants; `singledispatch` | A `@area.variant` registration form (variants named `_`, as in `functools.singledispatch`) is the only spelling type checkers accept for self-contained variants; `singledispatch` itself maps onto a first-argument-only `@dispatch` set with a generic fallback variant. Low priority; tracked in TODO.md. |
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
def describe(x: Bird) -> int32: ...
def describe(x: Dog | Cat | Bird) -> str | int32:
    if isinstance(x, Dog):
        return "woof: " + x.name
    elif isinstance(x, Cat):
        return "meow: " + x.name
    else:
        return x.wing_count
```

Generated C++:

```cpp
std::string describe__Dog(const Dog& x) {
    return std::string("woof: ") + x.name;
}

std::string describe__Cat(const Cat& x) {
    return std::string("meow: ") + x.name;
}

int32_t describe__Bird(const Bird& x) {
    return x.wing_count;
}

// describe(d) with d: Dog
::tpyapp::main::describe__Dog(d);
```

No union wrapper function is emitted. Each stub becomes a standalone C++
function with its own name (see "C++ names of variants" below), and each call
names the variant the compiler resolved.

### C++ names of variants

Each variant of an overload set that is emitted as a C++ function of its
own -- a bodied `@dispatch` variant, a `typing.overload` stub's
specialization, of a free set or of a record's method / operator dunder
set -- gets its own C++ name when the set has more than one such
variant. The call then names the variant sema resolved, so C++ overload
resolution never re-decides it (left to C++, a protocol variant's `T&`
template beats a concrete variant's `const Dog&` for a non-const argument,
and two constrained templates over unrelated protocols are unordered).

- The name is the Python name plus a suffix spelled from the declared
  parameter types (`describe__Dog`, `prod__Iterable_int32__int32`,
  `seed__noargs`), or from the literal values for a variant with a `Literal`
  parameter (`pick__lit_r__rb`). When two variants of a set spell the same
  suffix (`a.Dog` vs `b.Dog`, `Literal["a-b"]` vs `Literal["a_b"]`), each of
  them takes its declaration index (`pick__lit_a_b__v0`, `pick__lit_a_b__v1`);
  so does a variant whose readable name the module already binds -- a def,
  record, protocol, enum or import (`describe__Dog__v0` beside a user
  `def describe__Dog`). Module globals are not reserved yet
  (BUGS.md#imported-variant-name-collides-with-local-def).
- It is decided once, at registration (`name_overload_variants`), and stored
  on the variant's `FunctionInfo` (`variant_cpp_name`, read through `root`).
  The definition, the declaration, the cross-module re-export (one `using`
  per variant), every call (same-module, imported, `import m; m.f(x)`, a
  from-import alias) and a function reference (`apply(label, 7)` names the
  variant matched to the `Callable` slot) read that one fact.
- Kept: `@native` / `@cpp_template` variants keep their runtime symbols (C++
  chooses among the hand-written runtime overloads), so `math.log` -- one
  native and one bodied variant -- keeps `log` for its bodied one. A
  `typing.overload` generator or coroutine implementation is emitted once,
  not per stub, and keeps its name.
- Methods and operator dunders (since 2026-10-27). A record's sets are
  named in one record-wide pass after inheritance is resolved
  (`TypeRegistrar.name_record_method_variants`), with the same allocator;
  the names reserved are the struct's whole member namespace over the MRO
  (methods, fields, properties and their `set_<prop>` setters, class
  constants, the members codegen adds), so a collision takes the
  declaration index (`set_foo__int32__v0` beside a property
  `foo__int32`). A clone family -- an `@auto_readonly` / `auto_own` pair,
  which C++ tells apart by the receiver -- shares one name. Every call the
  program writes names the variant: `c.m__Dog(d)`, a protocol-typed
  variant deduced at its argument (`c.m__Named(cat)`), `super().m(...)` /
  `Base.m(self, ...)`, a static call, a subscript
  (`s.__getitem____int32(0)`, `s.__getitem____slice(...)`), `in`
  (`b.__contains____int32(3)`), an in-place `op=`. An operator over a named
  dunder variant renders its member call (the variant's `cpp_template` is
  stamped `({self}).__mul____int({0})` in place of the shared C++ operator
  spelling, so every operator path reads one fact): `v * 3` ->
  `(v).__mul____int(::tpy::BigInt(3))`, `!=` derived from a named `__eq__`
  -> `(!(v).__eq____int(...))`. A reflected one is the right operand's
  member call (`x * v` -> `(v).__rmul____int(x)`), which C++ evaluates
  right operand first, where Python runs `x` first: an evaluation-order
  difference postponed with the rest of that policy
  (BUGS.md#subexpression-right-to-left-eval). The names reserved include
  every variant name a class inherits from a set it does not redefine
  (`TypeRegistry.inherited_variants`). A member the class declares under
  one of them -- a method or set of any kind, a field, a class constant, a
  property or its `set_<prop>` setter (`Child.m__int32` beside `Base`'s
  `m(int32)` variant) -- is refused at its declaration ("'Child.m__int32'
  has the same name the compiler gives the inherited variant
  'Base.m(x: int32)'; rename it"), since it would hide the variant from
  the calls naming it. Only source names are refused: a variant name the
  class's own set generates avoids the inherited ones with the `__vN`
  suffix. A set the class redefines is hidden whole, so its variants keep
  their names and hide the base's. A generated name counts as a
  member for the type-shadowing census, so a type spelled like a variant is
  referred to qualified inside the struct.
- The plain name stays, as one forwarder per variant with the variant's own
  signature -- template head, parameters, defaults, return, receiver
  qualifier -- defined beside the variant (`return this->m__int(x);`, a
  parameter of the variant's spelling cannot hide it, nor a static one's,
  called through its class (`return S::m__int32(x);`); a parameter moved
  on as every delegating shim moves it (`forward_param`: an ownership
  transfer, or a by-value one whose copy is not known cheap), a consuming
  receiver `std::move(*this).m__int(x)`). What C++ calls on its own -- a protocol
  concept and a protocol-bounded body, a `@dynamic` adapter, a callable
  object's `operator()`, the friend operator / `operator[]` shims, runtime
  helpers -- reaches the plain name and sees exactly the overload set it
  always did, picking among it by C++ rules
  (BUGS.md#dispatch-method-variant-reresolved-by-cpp). A variant with a
  `Literal` parameter has no forwarder: no plain-name member could tell
  its values apart (a `typing.overload` literal-only group keeps only its
  general stub under the plain name). Two variants whose C++ parameters
  coincide (`str` / `StrView`) with the same receiver const-ness are
  refused in sema with the identical-parameters diagnostic, since their
  forwarders would clash. Generator and coroutine variants keep the plain
  name (as for free sets).
- A method implementing a slot of a `@dynamic` protocol (the record's or
  an ancestor's) may not be a set: the slot is one virtual, chosen by its
  name. *'C.m' implements @dynamic protocol method 'DP.m' and cannot be a
  @dispatch set; give the other variants different names*.
- A constructor written in TPy is one `__init__`; a constructor set only
  declares the C++ constructors a class binds. An `__init__` in set form
  (`@dispatch` `__init__` variants, or `typing.overload` stubs over an
  implementation, a lone one included) with a TPy-bodied variant is refused at its first set-form def in sema, before any body
  is analyzed: *'Q.__init__' cannot be @dispatch: a class takes one
  __init__ -- use a union parameter, parameter defaults, or a
  @classmethod factory* (the `@overload` form advises dropping the stubs
  and keeping the implementation). A construction names what it builds -- as Rust's
  named constructors -- so a second way to build is a named factory, and
  no ranking runs at a construction beyond a binding set: `@native` /
  `@cpp_template` `__init__` stubs bind C++ constructor overloads (the
  builtin types, the exception family), which sema ranks as any set and
  C++ then chooses among.
- A literal argument is pinned to the parameter sema picked: a call to a
  TPy overload set (not generic, not `@native` / `@cpp_template`) renders a
  str or numeric literal as a value of that parameter's own type, so the
  shared name binds the picked variant even where C++ alone would not (a
  bare `"x"` or `7` converts to a `bool` variant by a standard conversion,
  a bare `3` to every arithmetic variant alike). The pin recognizes a
  numeric literal by sema's type (an int or float literal type), so it
  covers more than a literal token. A str literal takes the view form
  (`std::string_view("x")`). A constant over literals (`3`, `1 + 2`, `+3`,
  `- -3`) renders by its value: an int at an int parameter its typed render
  (`static_cast<int16_t>(3)`, `::tpy::BigInt(7)`), any other the cast its
  coercion row spells (`static_cast<double>(3)`), a float at `float32` a
  cast from its double value (`static_cast<float>(0.1)`, never the `f`
  suffix, which rounds the decimal text to float once). Any other
  literal-typed argument -- a walrus, a conditional with equal branches --
  is evaluated once and converted from its own C++ type to the parameter's
  through the coercion table (`::tpy::BigInt((w = 3))`,
  `((w = 3)).to_fixed_check<int16_t>()` for a BigInt `w`); a scalar
  parameter always takes the cast, even from the same type, because a
  conditional's literal arms render at C++'s own literal types
  (`-2147483648` is a `long`), and only a BigInt value at an `int`
  parameter stays bare; a narrower fixed width and `float` -> `float32`,
  which have no row, take a plain cast. An Optional parameter wraps the result in the
  optional type itself (`std::optional<double>(0.1)`, since a payload value
  would lose to a plain variant's standard conversion). A value-union
  parameter does the same at the member sema landed the argument in
  (`TpyCoerce.union_member`): `v.b(1)` at `b(x: int64 | str)` beside
  `b(x: float)` renders
  `v.b(::tpy::Union<int64_t, std::string>(static_cast<int64_t>(1)))`, so
  C++ binds the union variant, not the `double` one. The pin fires
  where the variants share one C++ name (`FunctionInfo.reresolved_by_cpp`,
  read by `tpyc/thir/lower/checks.py` `_literal_pin_slot`); at a named
  method or operator variant, which no longer needs it to pick, because a
  method call's literal argument otherwise renders target-less
  (BUGS.md#method-literal-arg-renders-target-less) -- a free call threads
  the parameter type into the literal itself; and at any call where the union member differs
  from the type the argument renders as, since the bare literal may convert
  to no alternative (`pick(3)` at `pick(x: uint8 | str)` renders
  `pick(::tpy::Union<std::string, uint8_t>(static_cast<uint8_t>(3)))`). A
  walrus or conditional renders at its storage's type (a BigInt under
  `--default-int=BigInt`), so at a union member it always takes the pin,
  converted from that type (`((z = 3)).to_fixed_check<int32_t>()`). The
  pin is one step of the argument sink (`_lower_call_arg`), so every
  argument position takes it: a free or method call, a constructor, each
  `*args` element, an awaited coroutine call's frame. A parameter
  DEFAULT does not (BUGS.md#union-int-literal-narrow-member-ill-formed). `@native` / `@cpp_template` stubs (builtin `float`,
  `float32`, ...) take no pin. At an operator the literal operand at the
  dunder's parameter is pinned wherever sema resolves the dunder: `v * 3` at
  `__mul__(float)` / `__mul__(int)` renders `((v).__mul____int(::tpy::BigInt(3)))`,
  the reflected arithmetic `3 * v`, the in-place
  `v.__iadd____int(::tpy::BigInt(3))`, the `v = v + 3` an `+=` falls back to, a
  comparison and each link of a chained one, the explicit `v.__mul__(3)`,
  and a `!=` derived from such a set's `__eq__`, which then renders
  `(!((v).__eq____int(::tpy::BigInt(3))))`
  (`_operator_literal_pins`, `tpyc/thir/lower/expressions.py`). A chained
  comparison's middle operand evaluated once into a temp binds the value
  as written and takes the pin where the pinned link compares it
  (`v > (t := 3) > 1` renders `({ auto&& _cmp1 = (t = 3);
  ((v).__gt____int(::tpy::BigInt(_cmp1))) && (_cmp1 > 1); })`), so the next link
  compares the value sema typed. A str literal at an Optional str
  parameter takes the optional type (`std::optional<std::string_view>("x")`).
  An operand that needs a conversion and is not a literal is converted by
  sema itself at a record's dunder ("Operators" below), so the operator's
  C++ takes a value of the winner's parameter type (`((c) == (::tpy::BigInt(m)))`
  for `m: int32`), as the explicit call's argument does.
- A membership test `x in h` (and `not in`) over a TPy `__contains__` set
  is the call `h.__contains__(x)` and lowers as that call (`not in` its
  negation): the winner receives the needle as the call's argument --
  converted at its parameter in sema, literal pin included -- so `n8 in b` at `__contains__(int16)` / `(int32)` renders
  `b.__contains____int16(static_cast<int16_t>(n8))` and `3 in b`
  `b.__contains____int16(static_cast<int16_t>(3))`. A native container's
  `__contains__` (dict, set, bytes) is a lookup by value, not a call at its
  key slot, so its needle is not converted (`2**40 in d` over
  `dict[int32, str]` is False, not a range check).
- Not an interop API (a known limitation): the name follows the parameter
  types' spelling and, on a collision, the declaration order, so
  hand-written C++ should not call a variant -- or the set -- by name.

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

Current scope is union-typed parameters only. Non-union overloads (e.g., `int32` vs
`float` via coercion) are a future extension.

## Syntax and Semantics

### Two decorators

Overloading has two forms, and each has its own decorator so that the
form is chosen by name rather than inferred from whether a def has a body:

| | `typing.overload` | `tpy.dispatch` |
|---|---|---|
| Variant def | bodyless stub (`...` / `pass`) | its own implementation: a body, `@native` or `@cpp_template` |
| Trailing same-name impl | required (one) | forbidden |
| Under stock CPython | runs the impl (PEP 484) | runtime dispatcher in the `tpy` stub package |
| mypy / pyright | accepted | same-name redefinition flagged (as for `multipledispatch`) |

Both register one overload set in sema and share call-site resolution
(below); the compiler emits one specialized C++ function per variant
either way, each free variant under its own name. The parser records the decorator as `TpyFunction.overload_form`
and validates each def against it (a `@overload` stub with a body or a
native binding, a `@dispatch` variant with neither); sema's
`_require_overload_form` validates the group (no mixing under one name, a
`@overload` set ends in an implementation, a `@dispatch` set does not).
Each `@dispatch` variant's body is analyzed like a plain def and its facts
(which parameters it mutates or lends back, whether it writes `self`) live
on that variant's own `FunctionInfo` (`FunctionInfo.body`, found by
`typesys.body_function_info` / `body_method_info`), so every variant's
signature is the one it would have as the only def of its name. A
`@overload` implementation has no `FunctionInfo` of its own -- its stubs
are the callables -- so its facts land on one stub by position (the last
for a free function, the first for a method) until the implementation is
expanded into per-stub variants (TODO.md "`@overload` becomes `@dispatch`
variants").
`@dispatch` is the form the builtin stubs use throughout (`lib/tpy/tpy`),
and the convenient one for arity-variant APIs in larger programs;
`@overload` is the form to reach for when the source must also type-check
and run under stock CPython.

```python
from tpy import dispatch

@dispatch
def area(w: int32) -> int32:
    return w * w

@dispatch
def area(w: int32, h: int32) -> int32:
    return w * h
```

### Declaration (`typing.overload`)

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
- Stubs must have body `...` (Ellipsis) or `pass`; a `@overload` stub is never `@native` / `@cpp_template`
- Exactly one non-stub implementation with the same name must follow the stubs
- The implementation's union-typed parameters must be supertypes of each stub's params
- Stubs can have different return types (the key use case)
- Two stubs cannot share identical positional + keyword-only parameter
  types (sema rejects the overload set up front): call-site resolution
  could not tell them apart, and a method set -- whose variants share one
  C++ name -- would produce an "ambiguating new declaration" error. The check applies to free functions and methods
  (`tpyc/sema/registration.py:_reject_same_param_overloads`); method
  overloads that differ only in `is_readonly` / `is_consuming` (auto_readonly
  / auto_own const-qualified clones) are exempt because C++ emits them
  as `&` / `const &` / `&&` qualified overloads. So are BODYLESS `@dispatch`
  bindings (`@native`, `@cpp_template`, `...` stubs) that differ only in a
  type parameter's bound (`max[T: ComparableValue](iterable: Iterable[T])`
  beside `max[T: ComparableRef](...)`): no C++ declaration is emitted for a
  binding, so nothing clashes, and the bound picks the candidate. Bodied
  variants must still differ in a parameter type.

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
- Each stub parameter is a concrete type, a union of implementation members, or
  the implementation type itself. A partial union stub (e.g., `Dog | Cat` when impl
  has `Dog | Cat | Bird`) resolves but does not compile yet (see table above).
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

At call sites the compiler resolves against **stub signatures** (not the
implementation). Resolution runs in two stages over ONE ranking in
`sema/overloads.py` (`rank_overload_candidates`): a strict stage over the
candidates that convert no argument, then, when none does, a conversion
stage. Declaration order of the stubs affects the winner in neither.

```python
d = Dog("Rex")
result = describe(d)  # resolves to stub 1: describe(Dog) -> str
# result type: str (not str | int32)
```

The ranking below covers free-function, method and constructor calls,
Regime C's per-candidate arguments (below) and a record's `__contains__`
overload set (the `in` test converts its needle at the winner's parameter,
above; a literal needle is ranked as the call ranks a literal argument).
Operators and record subscripts rank the same way ("Operators" and
"Subscripts" below).

#### Operators

An operator is a call to a dunder, and its dunder set -- an in-place
`__iadd__` set as one set, `@cpp_template` variants beside bodied ones --
is ranked exactly as the named call to that dunder ranks it
(`OperatorResolver._rank_set`,
`tpyc/sema/operators.py`, with the checkers every call ranks with,
`call_ranking`): the receiver's type parameters substituted
(int-kind ones such as `ArrayList[T, N]`'s `N` included), the receiver kind
ranked ("The receiver kind" below) and the consuming
filter (`filter_receiver_overloads`), the method's own
type parameters inferred from the operand (a class-shadowed name, as in
`Rc[T].__eq__[T: Equatable]`, stays the class's). The operand is ranked as
analyzed: at the forward arithmetic dunder an undecided list is decided by
the winning dunder's parameter, as a call argument is (the comparison,
in-place and reflected dunders see it decided first:
BUGS.md#operator-dunder-container-operand), and a `Literal[...]` value
stays a literal, so `v + x` with `x: Literal[3]` matches
`__add__(self, x: Literal[3])`.

```python
class V:
    @dispatch
    def __add__(self, xs: list[int64]) -> int32: return 64
    @dispatch
    def __add__(self, xs: list[int32]) -> int32: return 32

v = V()
xs = [1, 2]
v + xs      # 32: `list[int32]` costs nothing at the default int, so xs is a
            # std::vector<int32_t>, whichever variant is declared first
```

The resolver tries the forward dunder, the left operand's `__int__`
promotion, the reflected dunder, the right operand's `__int__` promotion
and the mixed fixed-width step in that order (Python's `a.__add__(b)` then
`b.__radd__(a)`). A comparison's reflected dunder is its mirror on the
right operand (`3 < c` asks `c.__gt__(3)`, `3 == c` asks `c.__eq__(3)`,
`builtin_modules.COMPARE_TO_REFLECTED_METHOD`, kept apart from the
arithmetic `BINOP_TO_RMETHOD` that also drives the mixed-width step and the
exported reflected slots), tried only for a record on the right. A `!=`
asks each operand in turn for its `__ne__`, or for its negated `__eq__`
when it defines no `__ne__` (`object.__ne__`), so `l != r` with only
`L.__eq__` and `R.__ne__` runs `L.__eq__`, as CPython does
(`OperatorResolver.receiver_dunders`). The reflected comparison is not
tried when the left operand is a record defining the forward dunder: Python
would call that dunder first, which TPy cannot when its annotation refuses
the operand, so `A(1) == B(1)` with `A.__eq__(self, o: A)` and
`B.__eq__(self, o: A)` is rejected rather than running `B.__eq__`
(BUGS.md#comparison-operand-unanswered-reject). Each dunder
is looked up as its explicit call looks it up, inherited ones included
(`OperatorResolver.dunder_overloads` over
`ProtocolChecker.lookup_record_method_overloads`), so `a < b` runs the
`__lt__` a class inherits, and so does `x in d` with `__contains__`.

**An operator on a record answers like its explicit dunder call**
(`answers_as_call`: the receiver is a user, library or `@native` record):
its set answers whatever operand the call would take, conversions
included, and sema receives the operand at the winner's parameter as the
call receives its argument (`CallAnalyzer.receive_winner_args`, converting
it in place), so the C++ operator runs the variant sema picked:

```python
class B:
    @dispatch
    def __mul__(self, x: int) -> int32: ...    # ((b) * (::tpy::BigInt(n32)))
    @dispatch
    def __mul__(self, x: float) -> int32: ...

b * n32     # n32: int32 -- the `int` variant, as b.__mul__(n32)
3 < c       # c.__gt__(3): ((c) > (::tpy::BigInt(3)))
vi += n32   # vi.__iadd__(::tpy::BigInt(n32)), before any `__add__` fallback
```

A builtin type's set (`int32`, `int`, `float`, `str`, ...) answers only
an operand it takes as it is, a numeric literal (the operand the literal
pin renders), or a `Literal[...]` value converted to its own base type
(`"text:" + mode` with `mode: Literal["r", "w"]`); any other conversion is
left to the later steps, so `n32 + big` (`int32` and `int`) still resolves
through the promotion of the `int32` and stays an `int`. A top tie is the
call's ambiguity -- at a record's set always, at a builtin's when at least
two of the tied candidates would answer -- so `left + right`, where
`right` reaches both `__add__` variants only by a dereference, is
"Ambiguous overload for '__add__'" as `left.__add__(right)` is, not a
fallback to `right.__radd__`
(`tests/cases/operators/error_operator_conversion_tie`,
`tests/cases/operators/error_dispatch_operator_ambiguous`). Not covered:
Python's `NotImplemented` and its subclass-first rule for reflected
operators -- for arithmetic a forward annotation that refuses the operand
acts as `NotImplemented` would, so `a + b` with `A.__add__(self, o: A)` and
`B.__radd__(self, o: A)` runs `B.__radd__` where CPython runs `A.__add__`
(BUGS.md#operator-annotation-refusal-acts-as-notimplemented) -- and an
operator on a type parameter bound to a protocol, which keeps the builtin
rule (BUGS.md#generic-receiver-operator-refuses-conversion).

```python
class V:
    @dispatch
    def __add__(self, x: int | None) -> int32: ...
    @dispatch
    def __add__(self, x: int) -> int32: ...

V() + n     # n: int -- Ambiguous overload for '__add__': multiple candidates
            # match equally:
            #   __add__(int | None)
            #   __add__(int)
```

**An operator literal's home is the other operand** (NumPy's weak scalars,
NEP 50; `literal_home`, `tpyc/sema/operators.py`). Beside a `float` or
`float32` a numeric literal ranks as that type (always: `y * 1e40` stays a
`float32` multiplication); beside a fixed int an int literal ranks as that
type when its coercion row admits the value; beside an `int` an int
literal is an `int`; an int literal beside a float literal is a `float`.
Anywhere else -- beside a record, beside another int literal -- it is priced
from its own home, as a call argument is: `v + 3` at `__add__(int64)` /
`__add__(int32)` picks `int32` under the default `int32` and `int64` under
`--default-int=int64`, and `v * 3` at `__mul__(float)` / `__mul__(int)`
picks `int`. The home feeds the ranking only; which operand's dunder
answers is unchanged (an int literal beside a fixed int it fits is of that
type for the lookup, so `1 << k` with `k: int64` shifts an int64). So the
builtin numeric operators keep their picks and their C++: `x * 1000` on a
`float` stays `((x) * (1000))`, `y * 2.0` on a `float32` stays a `float32`
product, `1 + 2.0` and `2.0 ** 70` stay `float` operations.

The C++ runs sema's pick for a literal operand: a user dunder set shares
one C++ operator, so the literal takes the literal pin (above) wherever
sema resolves the dunder.

Under CPython, the `@dispatch` stand-in (`dispatch` in
`lib/cpy/tpy/__init__.py`) does not rank: it runs the first variant in
declaration order the call binds to (`inspect.Signature.bind`, keywords
by name) that is applicable by type (`isinstance`, a fixed-int literal
by range, a fixed int at a wider fixed-int slot, an `int` at `float`
(converted, by position or by a keyword that binds that parameter -- not
one that names a positional-only parameter and lands in `**kwargs`), a
`Literal` by value, a protocol by
`hasattr`, a builtin container by its class, a `Callable` by arity), and
when none matches by type the first it binds to (`docs/LANGUAGE_FEATURES.md`,
"CPython compatible"). A test whose output CPython also produces declares
the compiler's winner first; that the compiler's pick does not depend on
declaration order is pinned by compiler-only cases (`no_cpython.txt`).
Declaring the winner first is not enough when two calls to one set have
different compiler winners the stub cannot tell apart -- a `Literal`
value vs a plain variable of that value, `@readonly` receiver twins
called on a readonly receiver, aliased classes (`StrView` / `String` are
`str` there), element types, union members; such a
case goes compiler-only (BUGS.md#stub-first-applicable-cannot-split-calls).
`@readonly` twins declare the mutating twin first.

#### Subscripts

**A subscript answers like its explicit dunder call** -- on a record (user,
library, `@native`, inherited, generic, element-typed) and on a builtin
container, `str`, `bytes`, `bytearray`, `Array`, `Span`, the views and `*args`
alike; a tuple (literal index), a TypedDict (field), an enum class (name
lookup) and a protocol receiver keep their own rule. `t[k]` is `t.__getitem__(k)`, `t[k] = v` is
`t.__setitem__(k, v)`, `del t[k]` is `t.__delitem__(k)` and `t[k] op= v` is
`t.__setitem__(k, t.__getitem__(k) op v)`: each form resolves its dunder set
once, through the same ranking with the receiver
(`ExpressionAnalyzer.resolve_subscript_dunder` over
`OperatorResolver.rank_dunder_set`), the variant -- substituted at the
receiver's type args -- is stamped on the node (`TpySubscript.dunder`), and
the key is received at its parameter as the call receives its argument
(`CallAnalyzer.receive_winner_args`). One variant answers as a call to it
alone would (a wrong key is the call's *Type mismatch in argument*), a tie
is the call's *Ambiguous overload*. Every reader -- the result type, the
key's conversion, the C++ render, the `@native` callee, the receiver's
effect credit -- reads that one stamp:

```python
class Table:
    @dispatch
    def __getitem__(self, i: int32) -> int32: ...
    @dispatch
    def __getitem__(self, k: str) -> int32: ...

t["a"]         # t[std::string_view("a")] -- the str variant
t[n64]         # at __getitem__(int32) / (int): t[::tpy::BigInt(n64)], no narrow
t["a"] = 1     # t.__setitem__(std::string_view("a"), 1)
del t[0]       # t.__delitem__(0)
t[k()] += 1    # { auto __tmp_1 = k(); t.__setitem__(__tmp_1, ... t[__tmp_1] ...); }
mk()[k()] += f()  # { auto&& __tmp_1 = mk(); auto __tmp_2 = k();
                  #   __tmp_1.__setitem__(__tmp_2, ... __tmp_1[__tmp_2] ... f() ...); }
```

The read renders the record's `operator[]` with the key converted (a
literal key takes the literal pin), the store and `del` render the method
call the explicit spelling renders. A store resolves its `__setitem__` on
the key and the value as analyzed, so a record with only a `__setitem__`
takes a store, and the value is coerced to the variant's value parameter
(its `Own` dropped: the move or the warned copy is the store's); a literal
value takes the literal pin at that parameter, as the key does. An
augmented subscript is the explicit `t.__setitem__(k, t.__getitem__(k) op
v)`, with each target part whose repetition would show (a call into a
body, a writing builtin, a walrus, a conditional region holding one:
`TpyAugAssign.hold_parts`, docs/LANGUAGE_FEATURES.md)
evaluated once into an argument temp the statement declares in its own block
and reads by name (`THIRArgTemp.read_id` / `THIRTempRead`); the key is
received at the getter and at the setter apart (`TpySubscript.setitem_index`), and the setter is ranked
on, and receives, the operation's result at its value parameter
(`TpyAugAssign.stored_value`), so a narrowing there is the call's *Type
mismatch*. Each form's call passes the receiver const on its own when its
variant needs it (`TpySubscript.const_dunders`). A BigInt key at a fixed-width
variant is the call's checked narrowing, one `to_fixed_check<T>()` per key.
A slice key -- written (`xs[1:3]`, `xs[::2]`) or a slice-typed value -- is
the argument of type `basic_slice` / `slice` it builds, ranked like any key
(a written slice is never converted to another slice type, so a receiver
without a variant for it answers the no-match rule below, the written
slice named `slice`).

**When no variant takes the subscript**, it answers in the subscript's
terms, not the call's (the explicit `t.__getitem__(k)` keeps *No matching
overload for '__getitem__'*, since the user wrote the dunder there):

- The receiver's type declares no such dunder: *Type 'T' is not
  subscriptable* (read), *Type 'T' does not support item assignment*
  (store, augmented store), *Type 'T' does not support item deletion*
  (`del`) -- a static fact about the TPy type, so it holds where CPython
  would still index the object (a stdlib namedtuple whose TPy type has no
  `__getitem__`, `Any`). A protocol receiver and a receiver with no
  record (a `Callable`) answer the same.
- The dunder exists but no variant takes the arguments: *Type mismatch in
  `<context>`: expected E1 or E2, got A* at the FIRST argument position
  no variant admitting the earlier ones accepts, naming every type the
  variants declare there (a type another listed one takes, `basic_slice`
  at `slice`, is named by it; a stub's `Own` is not shown), through the
  one mismatch formatter (`TypeCompatibility.mismatch_text`). The context
  is the subscript's own noun on a builtin, whose stub parameter names
  the user never wrote (`ExpressionAnalyzer.subscript_arg_context`): the
  key as the receiver plus the stub parameter's name (`list index`,
  `dict key`), the value as `assignment` / `slice assignment`. On a user
  type it is the parameter's name the variants share there (`argument
  'k'`), else `subscript key` / `subscript value`. When every position
  is admitted by some variant (a pending literal the ranking takes
  loosely), the last one is checked as a call to one variant checks it,
  against the one type the variants declare there (`g = [[1]]; g[0] =
  ["a"]`: *Type mismatch in assignment: expected list[int32], got
  list[str]*); where they declare several, a container literal none of
  them takes is the mismatch at that position (`xs[[]]`: *Type mismatch
  in list index: expected int32 or slice, got list[???]*).

```python
xs: list[int32] = [1]
xs["a"]          # Type mismatch in list index: expected int32 or slice, got str
d: dict[str, int32] = {}
d[0:1]           # Type mismatch in dict key: expected str, got slice
c: Callable[[int32], int32] = f
c[:]             # Type 'Callable[[int32], int32]' is not subscriptable
```

`Slicing is not supported for T` remains only where a protocol's
`__getitem__` declares a slice key, which a protocol receiver cannot be
sliced through yet (BUGS.md#protocol-slice-key-rejected), and `del t[a:b]` on a receiver with a
`__delitem__` keeps *deleting a slice is not yet supported*
(BUGS.md#del-slice-rejected).

**The variants the key can be passed to choose the preparation.** Before a
pending argument is prepared or a context-typed one (a lambda, a container
literal) analyzed, the variants are FILTERED by the arguments that need no
context (`CallRanking.admits` over a known key; `CallAnalyzer.
admitted_overloads`, an `@auto_readonly` twin pair as one): a filter, never
a ranking -- a variant only a conversion reaches stays. The survivors then
drive the existing preparation: a container literal's (`MethodAnalyzer.
prepare_container_call`: `xs[0]` on `xs = [1, 2]` leaves the element open
where several `__getitem__` variants would have decided it), and the hint of
the context-typed argument, which is the parameter type every survivor
declares there (none when they differ). One survivor's signature answers as
a call to it alone would; several are ranked on the complete call. So the
store value of `fs[0] = lambda x: x + 1` and of `fs.__setitem__(0, lambda x:
x + 1)` is typed by the int variant's `T`. The same filter serves the
explicit method call.

**A pending number argument stays open only where its width cannot pick
the variant.** An argument (a subscript key or a store's value, a
positional or keyword one) that is an unannotated local whose number type
is still pending is analyzed open exactly when every variant of the call's
shape -- its arity and the keyword names it passes -- that an integer can
be passed to there declares ONE parameter type, a number type naming no
type parameter (`CallAnalyzer.number_arg_stays_open`, asked before the
argument is analyzed; the predicate is the one-callee rule of
`ExpressionAnalyzer.analyze_arg_at_param`, `receives_pending`). The
other arguments' types filter nothing, so `take("a", x)`,
`take(tag="a", k=x)` and the mirror `take(x, "a")` get the verdict of
the set, not of the spelling; an augmented `t[x] += 1` asks it over both
`__getitem__` and `__setitem__`. The set is the call's WHOLE overload
set, whatever subset a site ranks or classifies: a call with a lambda
ranked over its Callable-bearing variants (Regime C) and a container
literal's method preparation ask it over every variant, through the one
entry point every overload probe analyzes an argument with
(`CallAnalyzer.probe_arg`, given the set and the `CallShape`). Then it
ranks at the type its cells have so far -- every number-taking variant declares the same type, so the
width cannot change the winner -- and the winner's parameter receives it
still open: `d[x]`, `d.get(x, "")` on a `dict[int32, str]`, a
`take(int32)` / `take(str)` set, `t["a"] = x` over `__setitem__`
variants taking an `int32` or a `str` value. Elsewhere (two widths, a
type parameter, `Any`, a numeric protocol or union competing for the
number) the use fixes the local as any consumer does, so `for x in xs:
take(x)` over `take(int32)` / `take(int64)` then `xs.append(big64)` is
refused. Whether the use fixes the local does not depend on source order;
a wider store BEFORE the use has already widened the local, and then the
call compiles at the wider variant.

**What a builtin subscript keeps from its own rule** reads the declared
element-access contract of the winner (`element_access(sub, kind)`,
docs/NATIVE_INTEROP.md): a read through a contract variant is a storage
place (value category, may-interrupt, the augmented-target hold), a
container literal's part as its cells have it, and the bounds proof
(`bounds_safe`, taken off the receiver and key as written for a positional
receiver) and the `bounds_safe` raw render stay. A slice of a builtin
(`Iterable[Own[T]]` value) stores the value's elements, so the store
warns no copy of the value itself; a list literal value is hinted as a list
of the receiver's elements, which a protocol slot cannot type.
Not covered: a TypedDict call receiver of an augmented subscript
(BUGS.md#aug-container-call-receiver-rejects).

#### First pass: strict tier-ranked matching

Each candidate overload classifies every argument into a `(tier, widening_cost)`
pair. Candidates with any argument that fails to classify are rejected. The
survivors are ranked by `OverloadFit.rank()` (below) and the
lowest-ranked candidate wins. A winner in this pass skips pass two
entirely.

**Specificity tiers** (`MatchTier` in `sema/overloads.py`, strongest first --
lower numeric value beats higher):

| # | Tier | Matches |
|---|------|---------|
| 1 | `EXACT_CONCRETE` | `arg == param` after stripping `Readonly` / `Own` / `Ref` / `Optional`; also a numeric literal at its home (an int literal at the default int width it fits, a float literal at `float`) or an int literal at a fixed width it fits, `None` -> `Void`, `Callable` -> `Fn`, pending views -> resolved views. |
| 2 | `EXACT_GENERIC_SHAPE` | Param declared with a type parameter inside a concrete outer shape: `list[T]` matching `list[int32]`, `Ptr[T]`, `tuple[T, T]`, `T \| None`, `Callable[[Rec], T]`, etc. |
| 3 | `SUBCLASS` | A subclass argument at a base-class param: a user record (`Dog` at `Animal`), or a `Ptr` pointee (`Ptr[Dog]` at `Ptr[Animal]`, never dropping a `readonly`); an owned base (`Own[Animal]`) ranks the same, and the call then refuses the slicing move. Its cost is the MRO distance (`TypeRegistry.subclass_distance`: 1 for the first ancestor), so the nearer base wins. |
| 4 | `PROTOCOL_EXPLICIT` | Protocol conformance via declared `extends` / `implemented_protocols` / protocol-to-protocol inheritance, plus compiler-intrinsic short-circuits: `GenExpr` / `CopyIter` / `OwnIter` satisfying `Iterable`; `Enum` satisfying `Hashable` / `Comparable` / `Equatable`; `Tuple` satisfying `Hashable`; `ValueType` / `Default` marker protocols; `Stringable` (satisfied by any type with `__str__` **or** `__repr__`, mirroring Python's `object.__str__ -> __repr__` fallback). |
| 5 | `PROTOCOL_STRUCTURAL` | Protocol conformance proved only by walking required methods/fields. |
| 6 | `GENERIC_PROTOCOL_EXPLICIT` | Param declared as a protocol over a type parameter (`Iterable[T]`), matching via an explicit (tier 4) conformance path at its instantiation. |
| 7 | `GENERIC_PROTOCOL_STRUCTURAL` | Same, but structural conformance. |
| 8 | `GENERIC_WILDCARD` | Bare type parameter (possibly wrapped in `Ref` / `readonly` / `Own`: `x: T`, `x: readonly[T]`, `x: Own[T]`). Accepts any argument -- the fallback. |

**A literal's home.** A numeric literal's home is where it ranks
`EXACT_CONCRETE` without a width cost: an int literal at the default int,
a float literal at `float` (`CallRanking.homes`). At a constructor of a
numeric type `T` (`int8(...)`, `float32(...)`), `T` is the home of
literals of its own kind only, as an operator literal's home is the other
operand: `float32(1.5)` takes its `x: float32` variant and `int8(100)`
its `x: int8` one, while the int literal of `float(5)` keeps the int home
and renders its conversion. An integer constant generic (`N` of
`class Box[N: int]`) has no ranking rule of its own: a value read of `N`
is an ordinary value of the default int (sema types it so and converts the
constant once where it is read), so it ranks, infers and converts as any
default-int value does. Under the default `int32`, `pick(N)` beside
`pick(int32)` / `pick(int)` takes the `int32` variant, `take(N)` beside
`take(uint8)` / `take(int64)` the `int64` one, `str(N)` the `int32`
variant and `uint8(N)` the checked narrowing; under
`--default-int=BigInt` `pick(N)` takes `pick(int)` and `str(N)` the `int`
variant.

**Tiers come from the declared parameter.** A parameter declared with the
callee's own type parameter ranks by its DECLARED shape (`_declared_tier`):
bare `T` is the wildcard, a protocol over `T` a generic protocol, any other
shape over `T` a generic shape. The instantiation decides only whether the
argument is accepted, and the cost. This holds throughout the strict pass,
including at the call sites that substitute a generic candidate's type
parameters before ranking, and in the conversion stage (the declared parameter is read through `overload.root`, the
parameters `call_resolves_param` names), so a substituted generic never
scores as an exact match. A parameter typed with the receiver class's type
parameter (`def put(self, x: T)` on `class Box[T]`) is not the callee's own:
it ranks by the receiver's instantiation, as a concrete parameter.

```python
@dispatch
def g[T](x: T) -> str: return "generic"
@dispatch
def g(x: Named) -> str: return "proto"     # Named: a Protocol Dog meets
@dispatch
def g(x: Animal) -> str: return "animal"   # class Dog(Animal)

g(Dog())   # "animal" in either declaration order: SUBCLASS beats the
           # structural protocol, which beats the bare T
g(3)       # "generic": the only applicable variant
```

**Ranking rule** (`OverloadFit.rank()`, lower is better, the one key of
both stages), compared key by key:

1. **Converted count** -- how many arguments only a conversion accepts
   (second pass, below); 0 for every strict-pass candidate.
2. **Container widening** -- how far the candidate widens an undecided
   container argument (below).
3. **Narrowing** -- how many converted arguments narrow (an integer into a
   fixed-width parameter that does not hold every value of its type: a
   `BigInt`, a wider or sign-changing fixed width -- the `narrows_checked`
   coercion rows); 0 in the strict pass. Between two narrowing candidates
   the conversion widening below compares them, and a narrowing carries
   no width distance between fixed widths, only the sign-flip step:
   `f(uint8)` / `f(int32)` called with an `int64` takes `f(int32)`, while
   `f(int8)` / `f(int16)` called with an `int32` or an `int64` are
   ambiguous. From a `BigInt` the fixed widths are ordered wider first
   (below), so `f(int8)` / `f(int16)` called with an `int` takes
   `f(int16)`.
4. **Conversion widening** -- how far a protocol match widens the argument's
   elements: the widening cost of every `PROTOCOL_EXPLICIT` /
   `GENERIC_PROTOCOL_EXPLICIT` argument, read from the conformance
   (`TypeOperations.protocol_type_args_of`: a `dict[int8, int32]` is an
   `Iterable[int8]`), plus how far a converted fixed int, float or numeric
   literal widens or narrows (`_scalar_widening_cost`; a literal from its
   home, a `BigInt` with the fixed widths ordered wider first). A
   candidate that converts nothing beats one that widens elements, at any
   tier.
5. **Tier counts** -- the negated per-tier count vector (more matches at
   the strongest tier first, lexicographically).
6. **Cost** -- the sum of the remaining per-argument costs: an int
   literal's distance from its home at a fixed width it fits, a subclass
   argument's MRO distance.

**Widening cost** (`_scalar_widening_cost` / `_type_args_widening_cost`) is 0
for exact matches and positive when an argument is widening to a param type:

- Fixed-int -> wider fixed-int: `max(1, (target_bits - source_bits) / 8)` plus
  a sign-flip penalty if the signedness differs.
- Fixed-int -> `BigInt`: 8.
- Fixed-int -> float: 16.
- Float -> wider float: 1.
- `BigInt` -> fixed-int: ordered, wider closer and unsigned after signed of
  a width (int64 1, uint64 2, int32 9, ..., uint8 16); `BigInt` -> `float`
  17, -> `float32` 18. This is the scale of an int literal under
  `--default-int=BigInt`, so `divmod(7, 2)` and `pow(2, 10)` pick their
  `int` variants and `m(3)` at `int32` / `float` the fixed width.
- `IntLiteralType` and `UnknownElementType` (empty-list element) bias toward
  `default_int_type`: the cost is computed as if the source were
  `default_int_type`. This is how `sum([])` resolves to the `Iterable[int32]`
  overload at cost 0 under the default config, matching CPython's
  `sum([]) == 0` (int). `FloatLiteralType` is priced from `float` the same
  way (0 there, 1 at `float32`).

**A numeric literal is priced on that one scale, from its home** -- the
default int width for an int literal that fits it, `float` for a float
literal. At its home it is an exact strict match; at a fixed width it fits,
a strict match whose cost is its distance from the home; anywhere else
(`int` or `float` under the default `int32`, `float32` for a float literal)
it converts, its distance in the conversion-widening key as a typed value's.
So with `m(x: int)` / `m(x: float)`, `m(3)` runs `int` (8 < 16) and
`m(0.5)` `float`; `w(0.25)` at `float32` / `float` runs `float` (exact),
`g(0.1, Dog())` at `(float32, Dog)` / `(float, Animal)` runs the `float`
variant (a strict match, where the `float32` one converts), `j(16777217)` at `int` /
`float32` runs `int`; `p(3)` at `int64` / `Hashable` keeps `int64`, and
`m(1000000000000)` at `int64` / `int` keeps `int64` (the literal does not
fit its `int32` home, so `int64` is the strict match). An int literal costs
the same at `float` and `float32` (BUGS.md#dispatch-int-float-width-tie).
- For protocol params, cost is aggregated over the type args the argument
  instantiates the protocol with, as its conformance gives them (e.g.
  `list[int32]` vs `Iterable[int64]` scores 4 on the single element slot;
  `dict[int8, int32]` vs `Iterable[int32]` scores 3, on its keys). Where the
  conformance gives none, the type-arg tuples are walked pairwise when they
  line up, and non-parameterised containers fall back to their
  `get_element_type()`, so `bytearray` vs `Iterable[uint8]` scores 0 and vs
  `Iterable[int32]` scores positive. A generic user record's declared
  protocol comes back unsubstituted (`Iterable[T]` for a `Bag[int8]`) and
  scores nothing (BUGS.md#extends-protocol-arg-unsubstituted). This cost is
  the element widening, part of the conversion widening key.

**Container literals written in the call.** A list literal is a pending
container wherever it is written (its storage is decided later). A dict
or set literal is one only where it has to wait:
written as an argument of a call that scores several candidates, each
declaring the parameter it lands at plainly -- no view, no type parameter
(`OpenCallArgs.declares_plainly`; a candidate that reads the literal
through `Iterable[T]` reads the type its own values give, so for `max`,
`sum` or a generic candidate it is typed at once) -- and with no
declared parameter to type it at, the overload probe ranks it with its
numbers as the literals written (`ExpressionAnalyzer._probe_keeps_open`);
a candidate accepts it when every written value fits the part it is
written at (`TypeCompatibility._written_arg_entries`), and the winner's
receive meets the literal at its parameter, where its slot step types it
and decides the record the probe made, as a written empty container's. Anywhere else a dict
or set literal takes its type from its own values at once.

**Undecided container arguments.** An argument whose type is a pending
container (`PendingContainerType`: an unannotated list / dict / set local,
a list, dict or set literal or an empty container written in the call,
settled or still open) goes through one arm (`_undecided_container_arm`), shared by
both passes and Regime C's manual loops, at a
parameter that decides it: one that is neither a protocol nor still holds
a type parameter, or a view a generic candidate's call resolved. It asks
the pure compatibility query (`TypeCompatibility.is_type_compatible` in
the argument context, which writes nothing; reached through
`TypeOperations.pending_arg_leaves`, wired to
`PendingNums.overload_leaves`) whether a call to that candidate alone
would accept the container -- widen an open leaf, never narrow one, never
cross int/float for a local -- and returns its leaves (`ContainerMeet`):
each numeric TYPE POSITION (tree leaves, not literal occurrences) with
the cell that decides it, what it holds so far (`PendingNums.known_so_far`;
a literal leaf at its family's default; an empty part holds nothing and
is not listed) and what the parameter wants there. A candidate is
applicable only if its parameters accept every container argument
TOGETHER (`_container_fit`): a cell met at two positions (one local passed
twice, two rows of one nested list) must be wanted at one type by both,
and cells that stores tie -- both ways (a local rebound to another, a row
stored into a nested list) or one way (an element of one appended to the
other) -- must be wanted at types they can hold together. That second
question is the cells' owner's: `PendingNums.wants_fit` runs the settle
walk's own fixed point with each want as one more value, deciding
nothing, and the candidate is applicable when every cell comes out at its
want and every later store checked against a cell still fits it. It also
says which wanted cells are tied both ways -- one list under two names
(`mb = ma`) -- so that they widen as one, as the same name passed twice
does. Values that clash whatever is wanted are not a reason against a
candidate: the call that decides the lists refuses them in its own words.

The first key of a candidate's rank after the converted count, in both
passes and Regime C alike, is its CONTAINER WIDENING: the sum of
`_scalar_widening_cost` over those leaves, each cell counted once and
cells tied both ways as one. A
declared view (`Iterable[int64]`) decides nothing, so it widens 0 (its
per-element conversion cost is the element widening). Lower
container widening wins outright among candidates that convert as many
arguments -- an overload never widens a list that another applicable
overload takes as it is -- and among equal container widening the order
above applies unchanged. An applicable container at a
concrete parameter is an `EXACT_CONCRETE` match at cost 0: its widening
is counted once, in the first key. Nothing is decided while scoring: the winner
receives the arguments afterwards (`CallAnalyzer.receive_winner_args`, see
`docs/ARCHITECTURE.md` "pending_num"). A pending dict is ranked at a one-argument protocol as
the dict it spells, never by its value type.

**Concrete-over-generic invariant.** Because generic tiers (2, 6, 7, 8) all
rank weaker than their concrete counterparts (1, 4, 5), and a bare `T`
(8) weaker than a subclass match (3), concrete overloads beat
equally-matching generics, and a protocol or base-class variant beats a
bare `T`. Declaration order cannot change this. The invariant holds AT
EQUAL CONVERSION QUALITY: the container and element widening keys come
before the tiers, so a generic that converts nothing beats a concrete or
protocol variant that widens. `sum(xs)` over a `list[int8]` takes the
generic `sum[T: AnyFixedInt](iterable: Iterable[T]) -> T` (at `T = int8`,
no element widened) over `sum(iterable: Iterable[int32])` (each element
widened), so it returns `int8` (TODO.md "`sum` over narrow fixed ints
widens only when asked"). Over a `dict[int8, int32]`, its `keys()` or its
`values()`, `sum` reads the elements the conformance names and resolves
the same way.

**Ambiguity.** When the top candidates tie on every rank key AND have
distinct signatures, the compiler refuses to pick arbitrarily, in either
pass. Declaration order is **not** used as a tiebreaker. The call site
reports:

```
Ambiguous overload for 'describe': multiple candidates match equally:
  describe(Greeter)
  describe(Farewell)
```

A crossed tie is one too: with `class Dog(Animal)`,

```python
@dispatch
def t(x: Dog, y: Animal) -> str: ...
@dispatch
def t(x: Animal, y: Dog) -> str: ...

t(Dog(), Dog())   # error: Ambiguous overload for 't'
```

each variant has one exact and one subclass argument at the same cost; a
`t(x: Dog, y: Dog)` variant resolves it
(`tests/cases/calls/error_dispatch_crossed_subclass_ambiguous`). The same
crossed pair with a third `z: int64` parameter, called `t(Dog(), Dog(), n8)`
with `n8: int8`, ties in the second pass and is ambiguous too, though
CPython runs the first declared
(`tests/cases/calls/error_dispatch_coercion_tie_ambiguous`). Typing one
argument as the base picks a variant: `a: Animal = d; t(a, d, n8)` runs
`t(x: Animal, y: Dog, z: int64)`. The DECLARED (static) type picks, not
the runtime type (CPython's `isinstance` would still see a `Dog`), and
binding the subclass at the base type warns "upcast narrows".

**The receiver kind.** A method set whose variants differ in whether they
mutate their receiver (`@readonly` / `@readonly(False)`, the two clones of an
`@auto_readonly` pair) ranks them by the receiver's kind
(`rank_overload_candidates`, shared by calls, operators, `in` and
subscripts):

- On a READONLY receiver the arguments pick, and the receiver kind only
  breaks a tie they leave (the twins of a clone pair). A mutating winner is
  then refused by the receiver's readonly check, as a call to that variant
  alone is -- a worse-fitting readonly variant never runs in its place,
  since CPython would run the argument winner. Spell the argument at the
  readonly variant's type to call it (`p.m(int64(k))`).
- On a MUTABLE receiver the mutating variants rank first, through both
  stages, and the readonly ones only when none of the mutating ones applies
  (TODO.md "Argument types rank before the receiver kind on a MUTABLE
  receiver").

A tie among the variants ranked together stays *Ambiguous overload*.

```python
class B:
    @dispatch
    @readonly(False)
    def __getitem__(self, k: int64) -> int32: ...   # bumps a counter
    @dispatch
    @readonly
    def __getitem__(self, k: int32) -> int32: ...

b[1]            # mutable receiver: the int64 variant, b.__getitem____int64(static_cast<int64_t>(1))
r[1]            # r: readonly[B] -- the int32 variant, the arguments' pick
r[n64]          # n64: int64 -- the arguments pick the mutating int64 variant:
                # "Cannot call non-readonly method '__getitem__' on readonly reference"
f.m(1)          # m(str) mutating, m(int32) readonly: no mutating one applies,
                # so the readonly one runs: f.m__int32(1)
```

A named variant is reached by its own name, so the receiver's const-ness
picks nothing. Where a readonly variant shares its C++ name with a mutating
one -- a clone family, or a set without variant names -- and is ranked for
a mutable receiver, the call passes the receiver const
(`static_cast<const T&>(recv)`, evaluated once; `std::as_const` is deleted
for an rvalue) at every call of a dunder or method sema ranks: a method
call (a nullable `Ptr` receiver as
`static_cast<const T&>(::tpy::deref_check(p))`), each form of a subscript
on its own (an augmented subscript's getter and setter apart), an `in` test
and an operator (an `@native` operator's template included), so C++ does
not prefer a mutating variant of the same name for the receiver. One
decision makes it, `pins_const_receiver`, carried with the winner
(`RankedDunder.const_receiver`, `ResolvedBinop.const_receiver`,
`TpyMethodCall.const_receiver`, `TpySubscript.const_dunders`).
CPython's `@dispatch` stand-in runs the first applicable variant whatever the
receiver, so the two receivers of one set cannot both match it
(BUGS.md#stub-first-applicable-cannot-split-calls).

**A readonly argument does not re-rank.** Ranking reads signatures only, so
when the best variant takes a mutable parameter and the argument is
`readonly[A]`, the call is rejected ("Cannot pass readonly[A] as mutable
Named in argument 'x'") rather than falling back to a variant that accepts
`readonly` -- CPython would run the best variant, and choosing by the
callee's mutation facts would depend on analysis order. Declare that
variant's parameter `readonly[...]`, or drop `readonly` at the caller
(`tests/cases/calls/error_dispatch_readonly_arg_mutating_variant`).

**An owned base slot does not re-rank either.** `f(x: Own[Animal])` beside
`f[T](x: T)`, called with a `Dog`, picks the `Own[Animal]` variant (the one
CPython runs) and rejects the call: "Type mismatch in argument 'x': expected
Animal, got Dog", as a call to that one function does, since moving a `Dog`
into an owned `Animal` would slice it. Take `x: Animal` (a borrow), a generic
`Own[T]`, or pass an `Animal`
(`tests/cases/calls/error_dispatch_own_base_subclass_arg`).

Signatures that collapse to identical param-type tuples are deduped before
the ambiguity check -- picking either produces identical codegen; the
first declared is kept.

#### Second pass: conversions

Runs only when the first pass produced zero candidates. It classifies
every argument as the first pass does (`_classify_overload` in conversion
mode, with the same cells: tier by the declared shape, container meets,
element widening), and an argument the strict match refuses takes a
`CONVERTED` cell where `type_matches_with_coercion` accepts it: a scalar
conversion (`int8` at `int64`, a `BigInt` narrowing into a fixed width, a
fixed int at `float`), a numeric literal away from its home and from a
fixed width it fits (an int literal at `int` / `float`, a float literal at
`float32`), `Callable` -> `Fn`, `Deref[T]`, a subclass value at a
pointer to its base, a payload converting into an `Optional` parameter.
The cell's cost is a subclass's MRO distance; a converted fixed int, float
or numeric literal widens by `_scalar_widening_cost` (a literal from its
home); a literal its
conversion row's `check_range` refuses is no conversion there (an int
literal a fixed-width parameter cannot hold, a float literal `float32`
cannot), so `h(10000000000, n8)` at `h(int32, int64)` / `h(str, int64)`
has no match and `g(1e300, n8)` at `g(float32, int64)` /
`g(float, int64)` picks `float`. A `Literal[...]` value converts to its own
base type one step more cheaply than to any other type (a cost of 1 on
every other conversion of it, `_converted_cell`), so `f(mode)` with
`mode: Literal["r", "w"]` at `f(str)` / `f(String)` picks `str`, and
`"text:" + mode` takes `str`'s own `__add__` -- the two would otherwise tie.
A pick that already resolves stays: a `Literal` parameter holding the value
is a strict match and wins before any conversion, and a `Literal` value at
a set that converts it to one type only converts it there.
The candidates rank by the same `OverloadFit.rank()`, the converted count
first, so the pass keeps the first pass's order once an argument converts:
`f(Dog(), n8)` at `(Named, int64)` / `(Animal, int64)` picks `Animal`, as
`f(Dog(), m64)` does; `f("r", n8)` at `(str, int64)` /
`(Literal["r", "w"], int64)` picks the `Literal`; `f(d, n8)` at
`(Animal, int64)` / `[T](T, int64)` picks `Animal` whether `d` is a local
or a parameter; `f(puppy, 1.5)` at `(Animal, float)` / `(Dog, float)`
picks `Dog`. A top tie between distinct signatures is ambiguous, as in the
first pass.

A generic whose type parameter was bound by the JOIN of scalar fixed ints of
different widths (`f[T](x: T, y: T)` at `(int32, int64)` binds `T = int64`)
converts an argument, so it is never a first-pass match: it ranks here, at
its instantiation, like any conversion. `max(a64, b32)` picks
`max[T: AnyFixedInt]` (one exact argument) over `max(int, int)` (none), and
`f(int32(1), int64(2))` picks a concrete `f(x: int64, y: int64)` over the
joined generic in either order: both convert the `int32`, and the exact
`int64` outranks the bare `T` the generic declares.

**One rule at every call kind.** A method, free function, constructor,
operator, subscript, `raise`, `__deref__` and `in` call rank with one bundle
of checkers (`CallRanking`, bound by `call_ranking`) and one admissibility
predicate: an argument is admissible at a parameter iff the argument receive
takes it (`TypeCompatibility.is_type_compatible` at an argument). A strict
match and a ranked conversion (`_converted_cell`) keep their cells; every
other conversion the receive takes is a `CONVERTED` cell priced like one
(`CallRanking.received`): a member injected into a union parameter costs the
conversion of its best accepting member plus one widening, so `sys.exit(k)`
with `k: int` narrows to `exit(code: int32)` rather than reaching the
`int32 | str | None` variant at a flat price; a tuple costs the sum of its
elements' conversions; any other receive-only conversion costs 1; a
container literal's open leaf, which the receive decides, costs nothing.
So `h.m(p, 1)` and `g(p, 1)` with `p: A`, at a variant taking `A | B` beside
one taking `str`, both run the union variant (`us.__setitem__(1, P(6))` at
`Own[P | Q]` likewise), and a call answers whenever a call to one variant
alone would. The ranking, the applicability filter (`CallRanking.admits`)
and the union and tuple pricers take a cell from one computation,
`CallRanking.cell`.

**One commit.** The step that selects a call's callee builds its
`Candidate` (`sema/arg_binding.py`) once: the winner as the call takes it
(`matched`, a generic one substituted), the declaration whose rules receive
its arguments (`declared`), the call's binding to it and the written
argument each parameter binds, and the type arguments the ranking
instantiated it at (`OverloadPick.subst` for a dunder, the pool inference
of `_resolve_call_overloads` for a function). A call with one signature
builds it on that signature: a generic function infers its type arguments
there (`_generic_candidate`, through `_infer_arg_types`, the one argument
inference a generic record construction also runs, so a lambda argument
of `G(lambda v: v + 1, 5)` is typed as `g(lambda v: v + 1, 5)`'s is).
`CallAnalyzer.commit_winner` commits every free function, method, builtin
binding and constructor (sole, overload group, inherited, generic, a
record's field constructor) from it, in one order: the `**kwargs` region
and the layout (`lay_out_args`), each laid-out operand received once at
its parameter (`receive_winner_args`; a `*args` element at the element
type), the defaults left to C++ checked at the substitution
(`validate_generic_defaults`), the layout stored (`relayout`), then the
facts a committed call records once -- borrow conflicts, mutation edges,
the `@error_return` duty -- over the operands its binding holds
(`committed_operands`). It never binds or infers the winner again. An
operator or subscript dunder answering as its explicit call keeps its own
receive (an operator operand takes no promotion; a native container's `in`
needle stays unconverted) and records the same facts
(`commit_facts`, handed its operands). What stays per kind is data the lowering reads or
an open rule: the stamp (a constructor's synthesized signature, a
substituted function, `ResolvedBinop`, `dunders`), the `inferred_type_args`
layout, the builtin receive form, the fresh-owned-argument rule and the
one-char literal at `char`.

#### Worked example: `sum([])`

1. Arg type is `PendingListType(UnknownElement, size=0)`.
2. Candidates after pre-substitution: `sum(Iterable[int32|int64|int|float|float32])`.
   The generic `sum[T: AnyFixedInt](Iterable[T])` fails inference (no element
   info) and is dropped.
3. Each candidate classifies as `PROTOCOL_EXPLICIT` (the empty
   `PendingListType` shortcut in `protocols.py::classify_protocol_conformance`
   returns `EXPLICIT` for any single-type-arg protocol).
4. `_type_args_widening_cost` extracts the element (`UnknownElement`), then
   `_scalar_widening_cost(UnknownElement, <elem>, default_int=int32)` gives 0
   for `int32`, 4 for `int64`, 8 for `int` (`BigInt`), 16 for `float` /
   `float32` -- each candidate's element widening.
5. Rank keys: all tie on container widening (0: a declared view decides
   nothing) and differ on element widening, the second key. The `int32`
   candidate wins at 0.
6. Codegen emits `tpy::builtin_sum<int32_t>(std::vector<int32_t>{})`.

The same mechanism makes `sum([1, 2, 3])` resolve to `int32` via the
`IntLiteralType` branch of `_scalar_widening_cost`.

#### Keyword arguments

Every candidate takes the call's arguments where the call's one binding
places them (`bind` in `sema/arg_binding.py`, read over the arguments'
types: `bound_evidence` in `sema/overloads.py`) -- the same placement its
commit makes. A candidate the call does not bind to (a keyword it does not
declare, one naming a positional-only parameter, one colliding with a
filled positional, a required parameter left out, too many positionals)
is not applicable; a keyword naming no parameter, a positional-only one or
the `*args` one goes to a `**kwargs: Unpack[TD]` region where there is one.
A left-out default is no argument, so it neither scores nor skews the
ranking. Each keyword then ranks at the parameter it binds like any other
argument -- a keyword or keyword-only argument is inference evidence at a
generic candidate too -- so a call written with keywords picks the variant
its positional twin picks, and `f(xs, start=1.0)` picks the
`Iterable[float]` overload over an otherwise-tied `Iterable[int]` sibling.
When no candidate binds the call and all refuse it for one reason, that
reason is the diagnostic (*"got multiple values for argument 'x'"*); when
they refuse it for different reasons, *"No matching overload"* lists the
argument types and then each variant, by its parameters, with its reason,
one line apiece (`'get(key: str, default: int32, /)' parameter 'default'
is positional-only ...`); otherwise it lists the argument types, each
keyword as `name=T`. Applies to free functions, methods, constructor
groups, template constructors, subscript and operator dunders and builtins:
a builtin's stub declares CPython 3.12's parameter names and `/` / `*`
markers, so `sorted(xs, key=f)` binds `key` keyword-only and
`sorted(xs, f)` is refused.

#### `Fn[...]` parameters and contextual callable typing

Callable args (lambdas, named function refs) need a target type to resolve.
`tpyc/sema/calls.py:_resolve_call_overloads` (shared by user and builtin
paths) classifies overload sets via a *regime predicate* before tier
ranking. For each candidate, `supplied_fn_slots` reads its binding of the
call and reports which Fn-typed slots are filled by a *supplied*
(non-default) arg, positional or keyword. Three regimes:

- **Regime A** (zero candidates with supplied Fn slots): the existing
  direct arg analysis -- no two-phase Fn typing needed.
- **Regime B** (exactly one candidate with supplied Fn slots): the
  existing two-phase `_infer_arg_types` path runs against that candidate.
  Lambdas are typed via `_analyze_lambda_with_fn_hint`, which has a
  TPR-return body-inference branch -- this is what lets shapes like
  `map[T, U](Fn[[T], U], Iterable[T])` extract `U` from the lambda body
  without the call site having to pin it.
- **Regime C** (2+ candidates with supplied Fn slots): per-candidate
  evidence collection in
  `_resolve_regime_c` + `_build_regime_c_evidence`. Each candidate
  substitutes partial inference into its Fn-slot param to get a hint,
  then synthesises the lambda's "type" as that hint. When the hint
  return is still a `TypeParamRef` and the call-site context can't
  pin it (`_context_pinned` returns None; where it does pin it, the candidate is instantiated at the pinned type), the
  body is analyzed under `SemanticContext.trial_scope()` via
  `_lambda_body_dry_run` so the resolved return type pins the TPR;
  trial state changes (call edges, mutated params, borrow tracker,
  `expr_types`, counters/registries, diagnostics, lambda AST fields)
  roll back unconditionally and the winner's body is re-analyzed by
  `_analyze_single_function_call`. Named function refs go through a
  pure data matcher (`_match_function_to_hint_data`) that returns the
  matched `FunctionInfo` plus inferred type args without mutating the
  AST; the matcher's concrete callable type pins return TPRs for
  named refs.

Candidates that differ only in a type parameter's bound or a parameter's
`readonly` (the value and reference overloads of `max(iterable, key)`) type
their arguments alike, so the regime choice counts them as ONE Fn-bearing
candidate; the pool then picks between them by the bound.

Regime C ranks its per-candidate evidence through the same
`rank_overload_candidates` as Regime A/B (both passes, one tie rule), each
candidate at its own argument types. The legacy structural-match fallback at the user
path's no-match recovery is gated on `contextual_callable_used` so it
can't reason over synthesized callable types. Lambda body errors raised
during a trial propagate through `_build_regime_c_evidence` into
`saved_dry_error`, surfaced via `result.first_contextual_error` when no
candidate passes -- the user sees the body diagnostic (e.g. "Invalid
operand types for '+': int32 and str") instead of a generic
"no matching overload" message.

#### Testing

- Unit tests: `tpyc/test_overloads.py` pins `MatchTier` ordering, the cost
  model (the element widening read from the conformance included), `_score`,
  the `_classify_strict_match` contract (the subclass tier, its MRO-distance
  cost and the `Ptr` pointee upcast that keeps its `readonly`),
  `_declared_tier` (a generic parameter ranks by its declared shape),
  `OverloadFit.rank` (element widening before the tiers, container widening
  before both), the undecided-container arm (container widening first, one query per
  candidate and argument, joint applicability, a query that writes
  nothing) and the overload paths no case runs end to end (the overloaded
  `raise`, the `typing.overload` form).
- Undecided container arguments: `tests/cases/calls/overload_pending_container`
  (every call path, container kind and position, the winner declared first
  so CPython's first-applicable stand-in agrees), `tests/cases/calls/overload_pending_container_rank`
  (compiler-only: the winner declared after a weaker variant), `tests/cases/calls/error_overload_pending_then_wider`,
  `tests/cases/calls/error_overload_pending_no_match`,
  `tests/cases/calls/error_overload_pending_ambiguous`.
- Operators: `tests/cases/operators/dispatch_operator_rank` (every position
  and spelling, the literal pin, the operator literal's home at builtin
  operators, `Literal` values, the winner declared first so CPython agrees),
  `tests/cases/operators/dispatch_operator_conversions` (a record operator
  converting like its explicit call at every position, reflected
  comparisons, inherited dunders),
  `tests/cases/native/native_record_operator_conversion`,
  `tests/cases/operators/error_operator_conversion_tie`,
  the `operators` sections of `tests/cases/calls/dispatch_rank_declared_shape_order`
  (compiler-only: the loser declared first),
  `tests/cases/int_default_override/int64_smoke` (the pin under
  `--default-int=int64`), `tests/cases/operators/error_dispatch_operator_ambiguous`.
- Integration tests for the key invariants: `tests/cases/calls/overload_concrete_beats_protocol`
  (concrete wins regardless of stub order), `tests/cases/calls/dispatch_rank_declared_shape_order`
  (compiler-only: each tier against a weaker one declared first: exact,
  generic shape, nearer base class, protocol, bare `T`; a method set),
  `tests/cases/calls/dispatch_rank_declared_shape` (the winner declared
  first, so CPython agrees: the chosen variant writes the caller's object;
  a `typing.overload` set; the element-widening rule over `sum`),
  `tests/cases/calls/dispatch_variant_names_order`,
  `tests/cases/calls/dispatch_variant_names_protocol_order` and
  `tests/cases/int/generic_int_join_order` (compiler-only twins of the
  sections that declare the winner later),
  `tests/cases/calls/error_overload_ambiguous`
  (ambiguity -> diagnostic), `tests/cases/calls/error_dispatch_crossed_subclass_ambiguous`
  (a crossed subclass tie is ambiguous), `tests/cases/calls/overload_iterable_empty_literal`
  and `tests/cases/builtins/sum_basic` (empty-list default-int biasing),
  `tests/cases/bytes/from_iterable` (non-parameterised container element-type
  fallback for `bytearray` -> `Iterable[uint8]`),
  `tests/cases/str/str_repr_only` (Stringable broadening),
  `tests/cases/calls/overload_kwarg_disambig` (kwarg type breaks positional
  tie on free functions), `tests/cases/calls/overload_kwarg_method` (same
  on methods), `tests/cases/calls/overload_kwarg_distinct_names`
  (overloads with distinct kwonly names + defaulted positional gap),
  `tests/cases/calls/error_overload_kwarg_unknown` (targeted
  unexpected-kwarg diagnostic).
- `Fn[...]` regime tests: `tests/cases/calls/overload_fn_param_arity_2_3_named_ref`
  / `..._lambda` (Regime B picking by arity for named ref + lambda),
  `tests/cases/calls/overload_fn_param_same_arity_different_fn_arity`
  (Regime C picking by lambda arity), `..._named_ref_partial_match`
  (Regime C picking by named ref shape), `..._kwarg_callable` (kwarg-supplied
  Fn args route through Regime C), `..._callable_variable` (callable-typed
  variable disambiguates by hint), `..._single_candidate_body_inference`
  (Regime B preserves `_analyze_lambda_with_fn_hint` body return-TPR
  inference inside an overload group),
  `overload_fn_param_lambda_return_from_body` (Regime C body-trial pins
  unresolved return TPR for both 1-arg and 2-arg lambda candidates),
  `..._return_from_body_capture` (trial scope rolls back capture state),
  `error_overload_fn_param_lambda_body_type_error` (lambda-body
  `SemanticError` surfaces via `saved_dry_error` instead of a generic
  no-match diagnostic),
  `error_overload_fn_param_named_ref_ambiguous` (catch-and-stash ambiguous
  named ref against one Regime C candidate while another rejects).
- Same-params-diff-return rejection:
  `tests/cases/calls/error_overload_same_params_diff_return` (free
  function), `..._method_same_params_diff_return` (method),
  `tests/cases/readonly/auto_readonly_overload_check_passes`
  (regression guard that auto_readonly clones are exempted).

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
    def get(self, index: int32) -> T: ...
    @overload
    def get(self, name: str) -> T: ...
    def get(self, key: int32 | str) -> T:
        if isinstance(key, int32):
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
