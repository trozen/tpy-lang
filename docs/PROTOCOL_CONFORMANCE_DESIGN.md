# Protocol Conformance: Built-in, Conditional, and Nominal Impls

**Status: exploratory starting point, NOT a finalized specification.** This
captures the problem, the design space, and the decisions still open, so the
eventual spec starts from shared framing rather than a blank page. Expect the
concrete syntax and several mechanism choices below to change. Nothing here is
committed; treat every "proposed" as "candidate".

Related prior design: `docs/PROTOCOL_DESIGN.md` (structural protocols,
monomorphized per concrete type) and `docs/DYNAMIC_PROTOCOL_DESIGN.md`
(`@dynamic` runtime polymorphism). This doc is about a third axis: letting
*built-in* and *generic-container* types participate in protocol conformance,
including *conditional* conformance.

## 1. Motivation

We want type-directed code -- a serializer, a formatter, a hasher -- writable
in **pure stdlib**, recursing over arbitrary typed shapes. Two concrete goals
gate on this:

- **Zero-copy typed serialization** -- `json.dumps(data)` where
  `data: dict[str, int32]` serializes in place, no `JsonValue` wrapper, no
  O(n) deep copy. (See `TODO.md`; the implicit `to_union` deep-copy was
  reverted as a footgun, leaving a clean rejection in its place.) Same shape
  for a future `csv` / `repr`.
- **Moving `print` / `str` out of the compiler.** They are compiler-blessed
  today (`gen_print` + the C++ `printing.hpp` printers) for exactly one
  reason: containers and scalars cannot conform to a `Display`-style protocol
  in stdlib, so the compiler hardcodes their formatting. Fix conformance and
  `print` becomes ordinary stdlib code.

Neither is buildable today. The blocker, reduced to probes:

- A built-in does **not** satisfy a protocol whose required method exists only
  as an intrinsic (a C++ free function), not as a declared stub method:
  `int32` fails a `Stringable {__str__}` bound (`Type argument 'int32' does
  not satisfy bound 'Stringable'`) -- because `__str__` for int types lives
  only as `tpy::__str__(int32_t)` in `runtime/cpp/include/tpy/dunder.hpp`, and
  is NOT declared on `int32`'s stub (which declares `__add__`, `__int__`, ...
  but not `__str__`). Structural conformance consults the TPy method surface,
  which doesn't see the C++ free function. Same for `str`/`bool`/`float`. (A
  protocol requiring a dunder the built-in DOES declare as a stub method --
  e.g. `__add__` on `int32` -- is expected to conform; the gap is specifically
  intrinsic methods that exist only in C++.)
- There is no **conditional** conformance: no way to state "`list[T]`
  conforms to `P` when `T` conforms to `P`". Conformance is per-concrete
  structural match only.
- (minor) type-arg inference does not fire for a protocol-bounded generic from
  a built-in argument (needs explicit `[T]`).

Note the dispatch *kernel* already works: `def f[T: P](x: T) -> str:
return x.m()` resolves and monomorphizes per concrete type for a USER type
(verified). What is missing is conformance **coverage**, not dispatch.

## 2. What exists today

(Approximate locations; verify against current source.)

- **Conformance decision** -- `tpyc/sema/protocols.py`
  `classify_protocol_conformance(actual, protocol)` returns
  `EXPLICIT | STRUCTURAL | None`.
  - *Built-in fast-path*: `_check_builtin_extends` string-matches a hardcoded
    `record.extends_protocols` list (e.g. `list` carries `"Iterable[T]"`),
    set during stub registration. It is a POSITIVE fast-path only: on a match
    it returns `EXPLICIT`; on no-match, control FALLS THROUGH to the structural
    check (it does NOT short-circuit). So built-ins are already subject to
    structural conformance against user protocols.
  - *Structural path*: `type_has_method_with_signature` looks methods up via
    `registry.get_record_for_type(actual)`. Built-ins DO have a queryable
    `RecordInfo` with method tables (from the `lib/tpy/` stubs), and this path
    IS reached for them -- so a built-in conforms structurally to a protocol
    whose methods it DECLARES as stub methods. The gap: intrinsic dunders that
    exist only as C++ free functions (`tpy::__str__(int32_t)`, etc.) are NOT
    declared on the stub, so the structural check doesn't see them and the
    built-in fails any protocol requiring such a method. So Goal A is NOT
    "widen a gate" -- it is "make the intrinsic (C++-only) methods visible to
    structural conformance" (see section 7).
- **Bounds** -- a `T: P` bound is verified at call sites
  (`tpyc/sema/bound_check.py`); a bounded body's `x.m()` resolves via the
  protocol's method signature, monomorphized per concrete `T`.
- **The one conditional precedent** -- `tuple` conforms to
  `Hashable`/`Comparable`/`Equatable` iff all element types conform
  (`protocols.py`, the `TupleType` branch). This is exactly conditional
  conformance, but hardcoded for tuples and three protocols, with the
  element-wise semantics baked into the compiler.
- **Built-in method bodies** -- container stubs (`_list.py`, `_dict.py`) have
  NO TPy method bodies (all `...` / `@native` / `@cpp_template`); a method on
  `list[T]` cannot today call a protocol method on its element `T`.

## 3. Decomposition (and the Rust mapping)

The capability splits into three facets. In Rust they are all facets of one
construct -- the trait `impl` -- which is the central insight: Rust *unifies*
what we currently have no single home for.

- **A -- built-in / scalar conforms to a user protocol.**
  Rust: `impl MyTrait for i32` (a *local* trait for a foreign/primitive type,
  allowed by the orphan rule because you own the trait). TPy's structural
  model can do a *subset*: recognize a method the built-in *already has*. It
  cannot *supply* a method the type lacks -- that needs B2.

- **B1 -- conditional verdict** (`list[T]: P when T: P`).
  Rust: the `where T: P` clause on `impl<T: P> P for Vec<T>`. The verdict
  "does `Vec<i32>: P` hold?" is "does the conditional impl apply (i32: P)?".
  Generalize the hardcoded tuple rule.

- **B2 -- the element-wise implementation** (the method body that recurses).
  Rust: the `{ ... }` body of that same `impl`. This is the hard part and has
  no precedent: structural conformance can *recognize* an implementation but
  cannot *supply* one, and container stubs have nowhere to put a body.

**Key consequence.** Rust puts B1 and B2 in one place (the `impl` block holds
both the `where`-bound and the body). TPy splits them only because it has no
impl construct: the verdict is computed by the structural checker, and the
body has nowhere to live. A structural system fundamentally cannot express B2
("supply an implementation"). That gap is the whole feature.

## 4. Proposal: keep structural, add nominal impls

Have **both** conformance mechanisms; they are complementary, not redundant.

- **Structural protocols (unchanged, the default).** "Does this type already
  have the shape?" Zero boilerplate; conformance inferred from the method
  surface. Right for "my type already implements this" and capability/marker
  protocols. (Python precedent: `typing.Protocol`.)

- **Nominal impls (new).** "Here is HOW this type conforms, with bodies."
  Used exactly where structural cannot reach: supplying a method a type
  lacks, foreign/built-in types, and conditional/generic conformance. (Python
  precedent: `abc.register` for nominal verdict; `functools.singledispatch`
  for nominal external impl *with* bodies, including for built-ins.)

**Coexistence rule (candidate).** A type conforms iff it structurally matches
OR an impl exists. Conditional conformance is impl-only. Where an impl exists
it is authoritative for those methods. You write an impl only when structural
cannot do the job, so overlap is rare; a genuine structural-vs-impl conflict
is an error.

This keeps the ergonomic zero-boilerplate path for the easy cases and adds a
precise, body-supplying path for the hard ones -- without forcing Rust-level
impl boilerplate everywhere.

## 5. Nominal impls in Python syntax

Constraints: TPy source must be valid Python (IDEs/type-checkers/LLMs), AND it
must run under CPython for the `cpy` test phase. So the construct cannot add a
method to a built-in type at runtime (CPython forbids `list.__to_json__ = ...`).

**The anchor: `functools.singledispatch`.** Python already has "implement
behavior for a type externally, including built-ins, via a registry +
dispatch on the receiver's type." That is precisely nominal-external-impl. So
the candidate framing:

> **TPy nominal impl = `functools.singledispatch` with static `where`-bounds,
> monomorphized.** Static, compile-time, monomorphized dispatch under TPy (the
> Rust-trait behavior); degrades to ordinary `singledispatch` runtime dispatch
> under CPython (so tests run). It sidesteps "can't add methods to built-ins"
> because dispatch is a registry, not a method bolted onto `list`.

Candidate surface (decorator carrying protocol + receiver type + where-bound +
body):

```python
from tpy import impl

# impl ToJson for list[T] where T: ToJson   -- B1 = the [T: ToJson] bound,
#                                              B2 = the body
@impl(ToJson)
def _[T: ToJson](self: list[T], w: JsonWriter) -> None:
    for e in self:
        e.__to_json__(w)            # or to_json(e, w) -- see open decision 6.1

# A non-conditional impl for a built-in scalar (Goal A, supplying a body):
@impl(ToJson)
def _(self: int32, w: JsonWriter) -> None:
    w.write_bigint(self)
```

- The receiver is the explicitly-typed first parameter (`self: list[T]` --
  valid Python; `self` may be annotated). The generic `[T: ToJson]` IS the
  where-clause. The body IS B2. `@impl(ToJson)` names the protocol; the target
  type is read off the receiver annotation.
- Multi-method protocols group into a decorated class instead of loose
  functions.
- Goal A when the method already exists needs NO impl -- structural
  recognition handles it. An impl is written only to *supply* a body, a
  conditional bound, or a method the type lacks.

The downstream goals, written under this design (validators):

```python
# json.dumps, zero-copy, pure stdlib:
def dumps[T: ToJson](obj: T) -> str:
    w = JsonWriter()
    obj.__to_json__(w)              # monomorphized; recurses via the impls
    return w.finish()
# (the existing dumps(obj: JsonValue) stays for dynamic JSON.)

# print/str out of the compiler: print[T: Display](x: T) over Display impls,
# with conditional impls for list[T]/dict[K, V]/tuple -- the C++ printers
# become these stdlib impls.
```

## 6. Open decisions (the spec must settle these)

1. **Dispatch surface -- method-protocol vs typeclass/free-function.** THE
   pivot. Method form (`e.__to_json__(w)`, protocol = a set of methods) is
   closest to existing TPy protocols but awkward for built-ins (the method is
   "on" the type). Free-function / singledispatch form (`to_json(e, w)`,
   protocol = a set of free functions registered per type) solves built-ins
   naturally (no method bolted on `list`) and matches the CPython
   `singledispatch` realization, but reframes "protocol" toward "typeclass".
   This choice shapes everything else.
2. **Coherence / overlap.** Structural-match and impl both present; multiple
   impls for one (protocol, type); orphan-style rules (likely trivial since
   you own the protocol). Define precedence and the error cases.
3. **CPython realization of `@impl`.** Almost certainly the `lib/cpy/` stub
   maps it to `singledispatch.register` (or method injection for user
   classes, registry for built-ins) so the `cpy` phase runs identically.
4. **Monomorphization + recursion termination.** A conditional impl recurses
   on a statically-smaller type (one container layer peeled), so it terminates
   by static nesting depth -- same property the C++ printers rely on. Confirm
   the monomorphization machinery handles this without a fixpoint.
5. **Folding in the existing hardcoded paths.** The tuple-Hashable rule and
   the built-in `extends_protocols` strings should become ordinary impls under
   the new mechanism (unify, do not leave a parallel hardcoded path). Verify
   the structural path still serves the zero-boilerplate cases unchanged.
6. **Type-arg inference** for a protocol-bounded generic from a built-in arg
   (the minor probe gap) -- fix alongside, so `dumps([1, 2, 3])` needs no
   explicit `[T]`.

## 7. Suggested slicing

- **Goal A first, independently.** Built-ins ALREADY reach structural
  conformance (no gate to widen); what fails is intrinsic dunders that exist
  only as C++ free functions (`tpy::__str__`, ...) and aren't declared stub
  methods. So Goal A = surface those to conformance -- either declare the
  missing dunders as stub methods on the built-in types (mapped to the C++
  free function, the same way other dunders are), or have conformance consult
  the intrinsic/C++ dunder surface. The existing stdlib `Stringable`/`Truthy`/
  `Representable` protocols (`lib/tpy/tpy/_core/_types.py`) are the direct test
  bed: today `int32` fails `Stringable` for exactly this reason. Contained and
  useful well beyond json. Does NOT by itself unblock json/print (those need B).
- **Goal B (B1 + B2) as the nominal-impl construct**, gated on open decision
  6.1. This is the design-doc-worthy core and the actual unblocker.

## 8. Non-goals (for now)

- `@dynamic` runtime polymorphism interaction (separate axis;
  `docs/DYNAMIC_PROTOCOL_DESIGN.md`).
- Atomic / thread-safety concerns.
- Auto-derivation of impls (`#[derive]`-style); impls are written explicitly
  for now.
