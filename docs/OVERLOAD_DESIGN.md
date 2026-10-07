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
| Partial union stubs | Stub takes `Dog \| Cat` when impl has `Dog \| Cat \| Bird`. Needs `resolve_overload` to match concrete arg against union stub params via member containment. |
| Non-union overloads | Overloads distinguished by coercion-compatible types (e.g., `int32` vs `float`). Needs a different dispatch mechanism since isinstance doesn't apply. Start with union-only -- it's the natural pattern. |
| Overload on arity | Different parameter counts per stub. Maps to C++ overloads with different parameter counts. |
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
either way. The parser records the decorator as `TpyFunction.overload_form`
and validates each def against it (a `@overload` stub with a body or a
native binding, a `@dispatch` variant with neither); sema's
`_require_overload_form` validates the group (no mixing under one name, a
`@overload` set ends in an implementation, a `@dispatch` set does not).
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
  types (sema rejects the overload set up front). C++ realises overloads
  by parameter shape only, so two stubs with the same params and
  different return types would produce an "ambiguating new declaration"
  linker error. The check applies to free functions and methods
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

At call sites the compiler resolves against **stub signatures** (not the
implementation). Resolution runs in two passes in `sema/overloads.py`;
declaration order of the stubs does not affect the winner.

```python
d = Dog("Rex")
result = describe(d)  # resolves to stub 1: describe(Dog) -> str
# result type: str (not str | int32)
```

#### First pass: strict tier-ranked matching

Each candidate overload classifies every argument into a `(tier, widening_cost)`
pair. Candidates with any argument that fails to classify are rejected. The
survivors are ranked by the score vector `(aggregate tier counts, total
widening cost)` and the lowest-scoring candidate wins. A unique winner in
this pass skips pass two entirely.

**Specificity tiers** (`MatchTier` in `sema/overloads.py`, strongest first --
lower numeric value beats higher):

| # | Tier | Matches |
|---|------|---------|
| 1 | `EXACT_CONCRETE` | `arg == param` after stripping `Readonly` / `Own` / `Ref` / `Optional`; also `IntLiteral` -> fixed-int in range, `None` -> `Void`, `Callable` -> `Fn`, pending views -> resolved views. |
| 2 | `EXACT_GENERIC_SHAPE` | Generic param with concrete outer container and `TypeParamRef` inside: `list[T]` matching `list[int32]`, `Ptr[T]`, `tuple[T, T]`, etc. The outer shape pins the match before `T` is substituted. |
| 3 | `PROTOCOL_EXPLICIT` | Protocol conformance via declared `extends` / `implemented_protocols` / protocol-to-protocol inheritance, plus compiler-intrinsic short-circuits: `GenExpr` / `CopyIter` / `OwnIter` satisfying `Iterable`; `Enum` satisfying `Hashable` / `Comparable` / `Equatable`; `Tuple` satisfying `Hashable`; `ValueType` / `Default` marker protocols; `Stringable` (satisfied by any type with `__str__` **or** `__repr__`, mirroring Python's `object.__str__ -> __repr__` fallback). |
| 4 | `PROTOCOL_STRUCTURAL` | Protocol conformance proved only by walking required methods/fields. |
| 5 | `GENERIC_PROTOCOL_EXPLICIT` | Generic overload whose protocol param contains `TypeParamRef`, matching via an explicit (tier 3) conformance path after inference. |
| 6 | `GENERIC_PROTOCOL_STRUCTURAL` | Same, but structural conformance. |
| 7 | `GENERIC_WILDCARD` | Bare `TypeParamRef` param (possibly wrapped in `Ref` / `Readonly`). Accepts any argument. |

**Ranking rule.** The primary sort key is the negated per-tier count vector
(more high-tier matches first, lexicographically). The secondary key is the
sum of per-argument widening costs.

**Widening cost** (`_scalar_widening_cost` / `_type_args_widening_cost`) is 0
for exact matches and positive when an argument is widening to a param type
at the same tier:

- Fixed-int -> wider fixed-int: `max(1, (target_bits - source_bits) / 8)` plus
  a sign-flip penalty if the signedness differs.
- Fixed-int -> `BigInt`: 8.
- Fixed-int -> float: 16.
- Float -> wider float: 1.
- `IntLiteralType` and `UnknownElementType` (empty-list element) bias toward
  `default_int_type`: the cost is computed as if the source were
  `default_int_type`. This is how `sum([])` resolves to the `Iterable[int32]`
  overload at cost 0 under the default config, matching CPython's
  `sum([]) == 0` (int).
- For protocol params, cost is aggregated over matched type-arg positions
  (e.g. `list[int32]` vs `Iterable[int64]` scores 4 on the single element
  slot). Non-parameterised containers fall back to their `get_element_type()`,
  so `bytearray` vs `Iterable[uint8]` scores 0 and vs `Iterable[int32]` scores
  positive.

**Container literals written in the call.** A list literal is a pending
container wherever it is written (its storage is decided later). A dict
or set literal is one only where it has to wait:
written as an argument of a call that scores several candidates, each
declaring the parameter it lands at plainly -- no view, no type parameter
(`CallAnalyzer.open_written_literals`; a candidate that reads the literal
through `Iterable[T]` reads the type its own values give, so for `max`,
`sum` or a generic candidate it is typed at once) -- and with no
declared parameter to type it at, it keeps its numbers as the literals
written (`ExpressionAnalyzer._await_winner`) and its record awaits the
winner's slot, like a written empty container's
(`OpenCallArgs.awaiting`); a candidate accepts it when every written
value fits the part it is written at
(`TypeCompatibility._written_arg_entries`), and the winner's slot analyzes
the literal again at its parameter, which types it. Anywhere else a dict
or set literal takes its type from its own values at once.

**Undecided container arguments.** An argument whose type is a pending
container (`PendingContainerType`: an unannotated list / dict / set local,
a list, dict or set literal or an empty container written in the call,
settled or still open) goes through one arm (`_undecided_container_arm`), shared by
both passes, Regime C's manual loops and the operator matcher, at a
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

The first key of a candidate's rank, in the strict pass, the coercion
pass and Regime C alike, is its CONTAINER WIDENING: the sum of
`_scalar_widening_cost` over those leaves, each cell counted once and
cells tied both ways as one. A
declared view (`Iterable[int64]`) decides nothing, so it widens 0 (its
per-element conversion cost stays in the tier cost below). Lower
container widening wins outright -- an overload never widens a list that
another applicable overload takes as it is -- and among equal container
widening the order above applies unchanged (tiers, then cost, in the
strict pass; the existing keys in the coercion pass, one named key,
`CoercionFit.rank()`, that Regime C's coercion fallback shares, literal
penalty and generic tie-break included). An applicable container at a
concrete parameter is an `EXACT_CONCRETE` match at cost 0: its widening
is counted once, in the first key. Nothing is decided while scoring: the winner is
applied afterwards (`CallAnalyzer.apply_winner`, see `docs/ARCHITECTURE.md`
"pending_num"). A pending dict is ranked at a one-argument protocol as
the dict it spells, never by its value type.

**Concrete-over-generic invariant.** Because generic tiers (2, 5, 6, 7) all
rank weaker than their concrete counterparts (1, 3, 4), concrete overloads
always beat equally-matching generics. Stub ordering cannot change this.

**Ambiguity.** When the top candidates tie on both score components AND have
distinct signatures, the compiler refuses to pick arbitrarily. Declaration
order is **not** used as a tiebreaker. The call site reports:

```
Ambiguous overload for 'describe': multiple candidates match equally:
  describe(Greeter); describe(Farewell)
```

Signatures that collapse to identical param-type tuples (e.g. a substituted
generic and its concrete twin in the unified pool) are deduped before the
ambiguity check -- picking either produces identical codegen.

#### Second pass: coercion fallback

Runs only when the first pass produced zero candidates. Accepts implicit
coercions (narrowing conversions, `Callable` -> `Fn`, `Deref[T]`, subclass
upcasts) and ranks by count of non-coercion matches first, then by number of
narrowing conversions, with an `IntLiteralType` penalty derived from
`default_int_type` to break ties deterministically; a concrete signature
wins a remaining tie against a generic instantiation.

A generic whose type parameter was bound by the JOIN of scalar fixed ints of
different widths (`f[T](x: T, y: T)` at `(int32, int64)` binds `T = int64`)
converts an argument, so it is never a first-pass match: it ranks here, at
its instantiation, like any conversion. `max(a64, b32)` picks
`max[T: AnyFixedInt]` (one exact argument) over `max(int, int)` (none), and
`f(int32(1), int64(2))` picks a concrete `f(x: int64, y: int64)` over the
joined generic (a tie).

#### Worked example: `sum([])`

1. Arg type is `PendingListType(UnknownElement, size=0)`.
2. Candidates after pre-substitution: `sum(Iterable[int32|int64|int|float|float32])`.
   The generic `sum[T: AnyFixedInt](Iterable[T])` fails inference (no element
   info) and is dropped.
3. Each candidate classifies as `PROTOCOL_EXPLICIT` (the empty
   `PendingListType` shortcut in `protocols.py::classify_protocol_conformance`
   returns `EXPLICIT` for any single-type-arg protocol).
4. `_type_args_widening_cost` extracts the element via `get_element_type()`
   (returns `UnknownElement`), then `_scalar_widening_cost(UnknownElement, <elem>,
   default_int=int32)` gives 0 for `int32`, 4 for `int64`, 8 for `int`
   (`BigInt`), 16 for `float` / `float32`.
5. Score vectors: all tie on tier counts (one `PROTOCOL_EXPLICIT` each) and
   differ only on total cost. The `int32` candidate wins at cost 0.
6. Codegen emits `tpy::builtin_sum<int32_t>(std::vector<int32_t>{})`.

The same mechanism makes `sum([1, 2, 3])` resolve to `int32` via the
`IntLiteralType` branch of `_scalar_widening_cost`.

#### Keyword arguments

When a call supplies keyword arguments, each candidate overload independently
expands the positional `arg_types` list by inserting kwarg types at their
matching param slots (`_expand_arg_types_with_kwargs` in
`sema/overloads.py`). Overloads that don't declare a given kwarg name (or
where a kwarg collides with a filled positional slot, or a required kwonly
is missing) are rejected at the candidate level. Defaulted positional gaps
between the last positional and the rightmost kwarg are filled with the
param's own type so the gap doesn't skew cross-overload scoring. The
kwarg's type then participates in tier ranking like any other arg, so a
call like `f(xs, start=1.0)` cleanly picks the `Iterable[float]` overload
over an otherwise-tied `Iterable[int]` sibling. A kwarg name that no
overload accepts produces a targeted *"got unexpected keyword argument"*
diagnostic before the generic *"no matching overload"* fallback. Applies
to both free-function and method `@overload` groups; not to overloaded
builtins (which still reject kwargs at the call-site gate).

#### `Fn[...]` parameters and contextual callable typing

Callable args (lambdas, named function refs) need a target type to resolve.
`tpyc/sema/calls.py:_resolve_call_overloads` (shared by user and builtin
paths) classifies overload sets via a *regime predicate* before tier
ranking. For each candidate, `_supplied_fn_slots` walks positional + kwarg
args against `func.params` and reports which Fn-typed slots are filled by
a *supplied* (non-default) arg. Three regimes:

- **Regime A** (zero candidates with supplied Fn slots): the existing
  direct arg analysis -- no two-phase Fn typing needed.
- **Regime B** (exactly one candidate with supplied Fn slots): the
  existing two-phase `_infer_arg_types` path runs against that candidate.
  Lambdas are typed via `_analyze_lambda_with_fn_hint`, which has a
  TPR-return body-inference branch -- this is what lets shapes like
  `map[T, U](Fn[[T], U], Iterable[T])` extract `U` from the lambda body
  without the call site having to pin it.
- **Regime C** (2+ candidates with supplied Fn slots, or 1 with a
  kwarg-supplied Fn slot): per-candidate evidence collection in
  `_resolve_regime_c` + `_build_regime_c_evidence`. Each candidate
  substitutes partial inference into its Fn-slot param to get a hint,
  then synthesises the lambda's "type" as that hint. When the hint
  return is still a `TypeParamRef` and the call-site context can't
  pin it (`_return_tpr_pinnable_from_context` returns False), the
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

Regime C runs the same tier ranking as Regime A/B over the per-candidate
evidence (via `_classify_overload`), then a coercion-pass fallback
mirroring pass 2 above. The legacy structural-match fallback at the user
path's no-match recovery is gated on `contextual_callable_used` so it
can't reason over synthesized callable types. Lambda body errors raised
during a trial propagate through `_build_regime_c_evidence` into
`saved_dry_error`, surfaced via `result.first_contextual_error` when no
candidate passes -- the user sees the body diagnostic (e.g. "Invalid
operand types for '+': int32 and str") instead of a generic
"no matching overload" message.

#### Testing

- Unit tests: `tpyc/test_overloads.py` pins `MatchTier` ordering, the cost
  model, `_score`, the `_classify_strict_match` contract, the
  undecided-container arm (container widening first, one query per
  candidate and argument, joint applicability, a query that writes
  nothing) and the overload paths no case runs end to end (the overloaded
  `raise`, the `typing.overload` form).
- Undecided container arguments: `tests/cases/calls/overload_pending_container`
  (every call path, container kind and position, the ranking CPython's
  dispatch emulation can follow), `tests/cases/calls/overload_pending_container_rank`
  (the declaration orders it cannot), `tests/cases/calls/error_overload_pending_then_wider`,
  `tests/cases/calls/error_overload_pending_no_match`,
  `tests/cases/calls/error_overload_pending_ambiguous`.
- Integration tests for the key invariants: `tests/cases/calls/overload_concrete_beats_protocol`
  (concrete wins regardless of stub order), `tests/cases/calls/error_overload_ambiguous`
  (ambiguity -> diagnostic), `tests/cases/calls/overload_iterable_empty_literal`
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
