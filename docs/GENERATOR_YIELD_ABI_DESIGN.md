# Generator Yield ABI: borrow vs owned (declaration-driven)

Status: **Implemented** (def-generators + generator expressions; full suite green). Branch: `fix-generator-ref-yield-copy`.
Single shared gate `typesys.yield_uses_borrow_slot` decides the `val_or_ref<T>` borrow slot (excludes
`Optional`/`Union`/tuple/`Own`/`TypeParamRef`/value, which keep their own representation); a `readonly[T]`
element is INCLUDED and borrows too, spelled `val_or_ref<const T>` by `typesys.yield_borrow_slot_cpp`. See the
"IMPLEMENTATION FINDING" notes inline for where reality narrowed the original plan.
Fixes BUGS.md "Generator yields of a bare (non-tuple) non-value type are copied" (HIGH) and the
local-yield / consumer-retention cross-suspension dangle hazards. The caller-mutates/frees-the-borrowed-
container residual is NOT closed (still a non-fatal warning) -- see BUGS.md.

## As-built reconciliation (authoritative -- read this first)

The design reasoning below is preserved as history, but the implementation diverged from it in
several places. Where this section and the prose below disagree, **this section is correct.**

- **Single gate, no enum, no stamped fact.** There is no `YieldABI` enum, no `classify_yield_abi`, and
  no `TpyFunction.generator_yield_abi` field. The borrow-slot decision is one pure predicate
  `typesys.yield_uses_borrow_slot(elem_type) -> bool`, called directly by sema and both codegen paths.
  (The enum/stamped-fact mechanism in "The single yield-ABI classifier" and the typesys section was
  built, then collapsed.) The two remaining VALUE/OWNED distinctions are expressed inline at their two
  sites (the copyable check in `registration.py`; the mutation-marking in `_analyze_yield`).
- **Consumer source classification is type-based, not `FunctionInfo`-based.** `FunctionInfo` gained no
  `is_generator` flag (implementation-plan step 4 was abandoned). The consumer classifies an ephemeral
  source via the iterable's type -- `typing.Iterator` protocol or `GenExprType` with a borrow element
  (`statements.py::_is_ephemeral_borrow_loop_source`), not via `resolved_function_info`.
- **Enforced escape set is narrower than "all five" (see the IMPLEMENTATION FINDING table below).**
  Value-storage stores (field / container / global) COPY and are intentionally NOT rejected;
  return / outer-local stash are caught by the existing dangling / lifetime checks; closures are safe
  (Callable copies, Fn is inline). The one genuinely new rejection is **yield-onward in an outer
  generator**, plus a conservative nested-`def` capture backstop. `next()` results are NOT stamped
  ephemeral (only for-loop vars).
- **The resumable `(*b)` deref was KEPT, not removed** (codegen section says "remove"): `val_or_ref<T>`
  constructs from `T&`, so `(*b)` produces the `T&` the slot needs.
- **Tuple ephemeral marking is scalar-only.** A tuple source never classifies ephemeral
  (`yield_uses_borrow_slot` excludes tuples); tuple-borrow-member *escapes* are caught by the existing
  dangling-return check instead. **Design risk #4 closed:** `_analyze_yield` (gate unwraps a
  `readonly` wrapper) routes a tuple-typed yield through `check_dangling_reference`, whose per-element
  tuple branch rejects a fresh top-level non-value member (`yield (i, Box(i))` -> "Use Own[Box] for
  this tuple element"); the `Iterator[tuple[..., Own[Box]]]` form is the fix. Same branch / same
  diagnostic covers `return`. A non-value member nested inside an *inner* tuple is a deeper hole --
  codegen's tuple borrow/storage conversion is flat, so the nested borrow slot is miscompiled even for
  a rooted source; it is rejected outright (`_nested_tuple_borrow_member`, "nested borrow slot is
  miscompiled") until codegen recurses (BUGS.md). Nested *value* / nested *Own* members work.
  Regression: `error_gen_tuple_yield_fresh`, `error_gen_tuple_yield_fresh_elem0`,
  `error_gen_tuple_yield_readonly`, `error_gen_tuple_yield_nested`, `error_tuple_nested_ref`,
  `gen_tuple_yield_own`, `gen_tuple_yield_nested_own`, `gen_tuple_yield_ref` (borrow-ref mutation).
  **Still open, but LOUD:** the per-element check is literal-gated, so a non-literal yield/return of
  a tuple *local* assigned a fresh member (`t = (i, Box(i)); yield t`) is not caught by it -- but the
  shape no longer dangles silently. `_check_tuple_member_local` rejects the bare-name yield/return
  when the boundary type leaves the fresh element in borrow form ("Cannot yield tuple local 't':
  element 0 ... would dangle. Use Own[A] ..."), and the spellings that get past it -- an
  `Iterator[tuple[Own[A], int32]]` boundary, or a local bound from an owning call -- hit the THIR
  reject `res.btuple_yield_source` in a resumable frame. Still open because the fix is to make the
  whole-tuple yield WORK (the frame owns the slot; the yield wants a storage-form handoff), not to
  keep rejecting it; the dangle still wants catching at the local-assignment site (BUGS.md).
- **Actual tests** (the "Tests" section names are aspirational): `gen_yield_nocopy`, `gen_yield_del`,
  `gen_method_yield_nocopy`, `gen_own_yield_fresh`, `gen_ref_multi_yield` (mutation guard),
  `genexpr_ref_mutate`, `error_gen_yield_fresh_as_ref`, `error_gen_borrow_yield_onward`,
  `error_gen_borrow_escape_return`, `error_genexpr_fresh_as_ref`.

## Problem

A generator that yields a concrete reference-type element hands the consumer a **copy**, not the
live object -- a silent divergence from CPython (which yields the shared reference) and from TPy's
own `for`-loop iteration (which borrows).

```python
def each(xs: list[Box]) -> Iterator[Box]:
    for b in xs:
        yield b
# for b in each(data): b.val = 99
#   CPython / normal for-loop: writes data[i]
#   TPy today:                 writes a discarded copy -- mutation lost
```

Root cause: the iterator slot (`std::expected<T, StopIteration>` on the resumable frame) is
**value form** for a bare non-tuple element, so
`__next__()` materializes a copy. `std::optional<T&>` / `std::expected<T&>` is ill-formed pre-C++26,
which is *why* the naive slot fell back to value form (`gen_generators._iter_slot_for_yield`).

### The asymmetry that makes this tractable

The reference-preserving mechanism **already exists** -- it is just not applied uniformly:

| Yield element                     | Slot today                     | Behavior      |
|-----------------------------------|--------------------------------|---------------|
| Tuple `tuple[..., T]`             | `std::tuple<T*, ...>` (borrow) | reference ✅  |
| Generic `Iterator[T]` (type param)| `val_or_ref<T>` (pointer for ref) | reference ✅ |
| **Concrete `Iterator[Box]`**      | `Box` (value)                  | **copy ❌**   |
| **Generator expression**          | value                          | **copy ❌**   |

`val_or_ref<T>` (`runtime/cpp/include/tpy/type_traits.hpp`) stores value types by value and
non-value types by pointer (via the tagged `val_or_ref` wrapper struct), so `std::optional<val_or_ref<T>>`
is well-formed for both. Generic generators (`gen_generators.py` `ref_yield` branch, ~lines 160/239)
already route through it; the `gen_ref_compose` test proves mutation propagates through composed
generic generators today. **The concrete reference case is the odd-one-out.**

So the fix is a **unification**, not a new mechanism: make concrete reference-type yields use the
same `val_or_ref` / pointer-slot path the generic path already uses.

## Design: yield-element type drives the slot form, like a function return type

TPy already decides a function's return convention from its declared return type, not its body:

```python
def f() -> Box:        return Box(1)   # REJECTED: "Cannot return local or temporary as
                                       #   reference ... Use Own[Box] to return by value."
def f(b: Box) -> Box:  return b        # OK: borrow of a live object
def f() -> Own[Box]:   return Box(1)   # OK: owned (move out)
```

Generators adopt the identical rule:

- **`Iterator[T]` = borrow.** `__next__()` hands out a reference to a live object
  (slot `val_or_ref<T>` / `T*`). Every `yield` must produce a value that outlives the generator
  frame; a fresh temporary is **rejected** with the same diagnostic shape as `-> T: return <local>`,
  pointing the user at `Iterator[Own[T]]`.
- **`Iterator[Own[T]]` = owned.** `__next__()` returns by value/move (slot `T`). Fresh
  `yield Box(1)` is fine; a borrow `yield b` needs `yield b.clone()` (mirrors `-> Own[Box]`).
  This form already lowers and runs today.

```python
def each(xs: list[Box]) -> Iterator[Box]:        # borrow: zero-copy, CPython semantics
    for b in xs: yield b

def boxes(n: int) -> Iterator[Own[Box]]:         # owned: fresh values
    for i in range(n): yield Box(i)

def bad() -> Iterator[Box]:
    yield Box(1)   # error: cannot yield a local/temporary as a reference; use Iterator[Own[Box]]
```

### Invariant

> A generator's yield-element type determines its `__next__` slot form identically to how a
> function's return type determines its return form: `Iterator[T]` -> borrow slot
> (`val_or_ref<T>` / pointer for non-value), with every `yield` borrow-checked to outlive the frame;
> `Iterator[Own[T]]` -> owned slot (`T`, move). Tuple and value-type elements are unchanged.

This is the same invariant the function-return path already enforces; generators stop being a
special case.

### Cross-suspension borrow invariant (load-bearing -- must hold before implementation)

Yield safety is **NOT** identical to return safety: the generator frame *survives suspension*, so a
yielded borrow outlives the `__next__()` call that produced it. Reusing the function-return dangling
check verbatim is insufficient. The borrow rule:

1. **Source rooting.** A yield under `Iterator[T]` must borrow from state the *frame* owns or
   transitively borrows -- a captured parameter / receiver, a `self.field`, or a loop variable
   aliasing an element of a frame-held container. A borrow of a per-`__next__()`-call local (a
   temporary, or a local that does not live on the frame) is **rejected at sema** -- this is the part
   the function-return machinery does cover.
2. **Validity window (consumer-enforced).** A yielded borrow is an **ephemeral borrow**, valid only
   until the next `resume`/`__next__()` (or frame destruction) -- C++ input-iterator semantics. The
   *producer* classifier only certifies "this yield can supply a borrow"; preventing the *consumer*
   from retaining it past the iteration step is a separate, consumer-side borrow-checker
   responsibility. Concretely, for a `BORROW_REF` iterator item the loop var / `next()` result is
   stamped ephemeral, and these are **rejected** (unless converted via `clone()` / an owning form):
   storing it into a longer-lived field / container / global, returning it, yielding it onward without
   equivalent lifetime proof, or capturing it in a closure / another generator frame. `for x in gen():
   use(x)`, passing `x` to a non-escaping borrowed param, and mutating through `x` are all fine. This
   is the minimum rule that makes the borrow ABI defensible; leaving consumer escape to documentation
   is not acceptable.
3. **Pre-existing tuple parity.** Tuple-borrow yields already hand out element borrows and (today) do
   *not* enforce this consumer-side ephemeral rule, so the same latent "stash a borrow across resume"
   unsoundness exists for them now. The consumer-side check should be built **once, covering scalar and
   tuple borrow yields alike** -- retrofitting tuples closes a pre-existing gap rather than adding a
   parallel path.
4. **Caller-side container hazard (genuine residual).** Distinct from consumer escape: the caller
   mutates or frees the borrowed *container itself* while the generator is live (BUGS.md cross-suspension
   borrow entries). That is a caller/container-lifetime problem the consumer-escape check does not cover;
   it stays at the current partial mitigation (non-fatal mutation-during-iteration warning +
   `return_borrows_from`). The doc must not claim D subsumes those entries.

If the consumer-side ephemeral check is not enforced, D trades "silent copy divergence" for "silent
borrow-across-resume unsoundness" -- strictly worse. **Scope decision (resolved): (i) -- full
soundness in v1.** The consumer-side ephemeral-borrow check ships in v1 and is built once to cover
**scalar and tuple** borrow yields alike, retrofitting the pre-existing tuple gap rather than adding a
parallel path. v1 is not considered complete until a borrow-yield loop var / `next()` result that
escapes its iteration step (stored, returned, captured, or yielded onward without an owning
conversion) is rejected at sema.

#### Resolved consumer-side sub-design (the trigger is frame-slot provenance, not non-value-ness)

The ephemeral check fires when, and only when, the iterated source's item is a **frame-slot-rooted
borrow** -- a generator/iterator `BORROW_REF` yield, scalar or tuple. This is *narrower* than "any
non-value loop var": container-rooted element borrows (`for x in param_list:`) are **durable** and
already correctly treated as such -- `add_loop_var_provenance` (`init_tracker.py`) adds such a loop
var to both `param_provenance_vars` and `safe_to_return_vars` because the borrow roots in storage that
outlives the function. A blanket "reject escape of any non-value loop var" would wrongly reject sound
container code. The distinguishing fact is the *root*: a generator/iterator frame slot is overwritten
on every `__next__()`, so its borrow's validity window ends at the next iteration step; a container's
storage does not.

**Source classification at the consumer** (`for x in <iterable>` / `x = next(it)`):
- call to a generator function (`FunctionInfo` exposes `is_generator` + the yield ABI), genexpr
  producing a borrow, or an `Iterator[T]` protocol-typed value -> **ephemeral** (frame-slot-rooted).
- container (list/dict/Span/user `__iter__`) -> **durable**, existing path, unchanged.

**Representation:** a per-function `ephemeral_borrow_vars: set[str]` (sibling to `safe_to_return_vars`),
populated at the loop / `next()` binding when the source classifies as frame-slot `BORROW_REF`. Such
vars are kept **out of** `safe_to_return_vars` (so the existing return-dangle check rejects returning
them) and added to the new set so the other escape sites consult it. Tuple-unpack targets bound to
borrow elements are added per-target; value elements are not (a `readonly` element IS a borrow, so it
is added -- the set follows `yield_uses_borrow_slot`). Deliberately a named-var set
(with an implied step-bounded region), not a string-keyed `BorrowTracker` heuristic, so it maps to a
future MIR `LoanInfo` with a back-edge-bounded region.

**Escape sites -- IMPLEMENTATION FINDING (the v1 list narrowed).** The sub-design listed five escape
sites to reject. Implementation against TPy's actual value-storage semantics showed most of them are
already safe or already caught, so the enforced set is smaller (verified empirically):

| Sub-design site | TPy reality | Enforcement |
|---|---|---|
| store to value field / container / global (`self.f = x`, `list.append(x)`, `d[k]=x`, `g = x`) | **copies the value** into owned storage (the existing "copies into owned storage" warning path) -- memory-safe, not a borrow-retain | **not rejected** (would over-reject the same code the old copy-ABI accepted) |
| `return x` | a generator loop var is body-scoped and not `safe_to_return` | **already rejected** by the dangling-return check (ephemeral message layered on top) |
| stash into an outer-scope local (`saved = x`; use after loop) | scope-depth lifetime analysis | **already rejected** ("'x' is rebound on each iteration; use copy() or Rc") |
| closure / lambda capture | `Callable` lambdas capture **by value** (copy); `Fn` lambdas are inline / non-escaping | **safe**; a conservative backstop rejects an ephemeral capture inside a nested `def` |
| **`yield x` onward in an outer generator** | re-yields a reference into the inner frame slot; the Stage-built generator-loop-var provenance broadening would otherwise *bless* it | **rejected** (the one genuine new gap) |

So v1 enforcement = keep the borrow **out of provenance / `safe_to_return`** (so the existing dangling /
lifetime checks fire), reject **yield-onward**, and a conservative **nested-`def` capture** backstop.
This is sound (no use-after-free path remains) without over-rejecting the safe value-copy stores.
Tuple-unpack borrow targets are stamped per-element the same way.

**Escape-fix diagnostic:** point the user at a copy -- `x.clone()` for `@nocopy`/explicit-clone types,
a plain copy for copyable types (mirrors the `Own`-return guidance).

### The single yield-ABI classifier (core mechanism)

> SUPERSEDED (see "As-built reconciliation"): shipped as the pure predicate `yield_uses_borrow_slot`,
> not a `{VALUE, BORROW_REF, OWNED}` enum, and not stamped on the AST node -- codegen calls the
> predicate directly.

Do not scatter the borrow-vs-owned decision across `get_iterable_element_type`,
`_iter_slot_for_yield`, resumable emission, and for-loop binding -- each
re-deriving ownership is exactly the consumer-side-dispatch anti-pattern CLAUDE.md forbids. Instead:

- One classifier `yield_abi(declared_elem_type, yield_expr) -> {VALUE, BORROW_REF, OWNED}`:
  - **VALUE** -- value-type element (int/char/Span/...): unchanged, copies are free.
  - **BORROW_REF** -- `Iterator[T]` non-value, yield expr is a durable borrow (rule 1 above):
    slot `val_or_ref<T>`, emit the pointer, consumer derefs.
  - **OWNED** -- `Iterator[Own[T]]`: slot `T`, move into slot.
  - Invalid combinations (`Iterator[T]` + fresh temp; `Iterator[Own[T]]` + bare borrow needing a
    clone) are rejected here with the located diagnostic.
- **Sema** runs the classifier to validate every yield and stamps the result as a fact on the yield /
  generator AST node. **Codegen** reads that one fact for slot type, yield emission, and consumer
  binding -- it never re-derives ownership. Tuple yields fold in as a composite of per-element ABIs.

## Why this beats the alternatives

- **vs "infer the slot from the body" (Option A):** A makes the external ABI depend on the body
  (a generator that happens to yield only borrows borrows; one that yields a fresh value copies),
  so two `-> Iterator[Box]` signatures could have different consumer semantics. Declaration-driven
  is consistent with functions and self-documenting.
- **vs "uniform `T*` slot, fresh values owned by the frame" (Option B):** B removes the copy
  everywhere but trades it for a *new* silent divergence -- a consumer that retains a fresh-value
  yield across iterations sees the frame slot overwritten (TPy) vs distinct live objects (CPython).
  D refuses the fresh borrow at compile time instead, so there is no new silent footgun.

## Implementation plan

### sema (`tpyc/sema/`)
1. **Replace the copyable check with a value-category check.** `registration.py::_validate_generator_yield_copyable`
   currently rejects `@nocopy` bare yields because the slot copies. Under D the `@nocopy` rejection
   *dissolves* (borrow doesn't copy; owned moves). Replace it with: under `Iterator[T]` (T non-value,
   not `Own`), each `yield <expr>` must be a borrow of a source that outlives the frame -- reuse the
   existing dangling-return machinery (the check that emits "Cannot return local or temporary as
   reference"). Under `Iterator[Own[T]]`, fresh yields are fine; a borrow yield needs `.clone()`
   (reuse the `Own`-return rules). Both free-function (`register_function`) and method
   (`register_record`) registration sites must apply it (today both call the copyable check).
2. **Carry the slot-form decision as a fact** on the generator / yield AST node so codegen reads it
   directly (per CLAUDE.md: facts on AST nodes, not consumer-side re-derivation). The yield-element
   `OwnType`-ness already encodes borrow-vs-owned; ensure it survives to codegen (see typesys below).
3. **Consumer-side ephemeral-borrow check (v1, scalar + tuple).** When a `for`-loop / `next()` consumes
   a source whose item is a **frame-slot-rooted** `BORROW_REF` (generator call / genexpr / `Iterator[T]`
   value -- *not* a container; see "Resolved consumer-side sub-design" above), populate the bound loop
   var / result (and per-target tuple-unpack borrow members) into `ephemeral_borrow_vars` and keep them
   out of `safe_to_return_vars`. Reject all five escape sites past the iteration step: (1) return,
   (2) store to longer-lived storage, (3) container insertion, (4) closure/nested-def capture,
   (5) yield-onward -- unless converted via a copy (`.clone()` for `@nocopy`, plain copy otherwise).
   Apply uniformly to tuple-borrow yields (retrofits the pre-existing gap rather than forking a path).
   This is what makes the borrow ABI sound; v1 is incomplete without it (scope decision (i)).
   Representation is a named-var set with an implied back-edge-bounded region (future MIR `LoanInfo`),
   not a string-keyed `BorrowTracker` heuristic.
4. **Plumb the yield ABI onto `FunctionInfo`.** `FunctionInfo` carries no `is_generator` flag today (it
   is only on the `TpyFunction` parse node), so the consumer cannot classify a generator-call source.
   Add `is_generator` + the resolved yield ABI to `FunctionInfo` so a `for x in gen()` consumer reads it
   from `resolved_function_info`.

### typesys (`tpyc/typesys.py`, element resolution)
- **As shipped:** the single gate is the pure predicate `yield_uses_borrow_slot(elem_type)` (no
  `YieldABI` enum, no `classify_yield_abi`, no stamped `generator_yield_abi` field -- see As-built).
- **Audit result (resolved): no accessor change needed.** `Own`-ness is already preserved where the ABI
  is read: on the producer node `func.generator_yield_type` (`OwnType(Node)` for `Iterator[Own[Node]]`,
  bare `NominalType` for `Iterator[Node]` -- verified) and on the consumer's iterable `Iterator[...]`
  protocol type args. `get_iterable_element_type`'s `Own` strip (`modules/type_resolution.py:427`) only
  affects the loop-var *type* (correctly `T`, not `Own[T]`), which is the right behavior -- so it stays.

### codegen (`tpyc/codegen_cpp/`)
- **`gen_generators._iter_slot_for_yield`** (the central decision): extend the `ref_yield` treatment
  from `TypeParamRef`-only to **all non-value element types** under `Iterator[T]`. The slot becomes
  `val_or_ref<T>` (pointer for non-value) instead of value `T`. `Iterator[Own[T]]` keeps value `T`.
  This is the core change and is small because the mechanism exists.
- **Resumable path** (`gen_async.py::_resumable_ret_type_cpp`, `_emit_generator_yield` ->
  `statements.py::gen_yield_value`): the borrow slot is `std::expected<val_or_ref<T>, StopIteration>`.
  CORRECTION (as-built): the `(*b)` deref was **kept**, not removed -- `val_or_ref<T>` constructs from a
  `T&`, so `(*b)` produces exactly the `T&` the slot needs (the original "emit the pointer, no deref"
  plan was wrong about the slot's constructor).
  **Async-shape guard:** `_resumable_ret_type_cpp` is shared by the async (`__poll__` -> `Poll<T>`)
  and generator (`__next__` -> `expected<...>`) shapes. The `val_or_ref` slot change must be gated to
  the *generator* shape only; the async `Poll<T>` return (the function's declared owned `T`) must be
  byte-identical after the change. Async generators (`async def` + `yield`) are rejected wholesale
  upstream, so there is no async-yield slot to consider.
- **Consumer binding** (`statements.py` for-loop-over-generator, `context.py::loop_var_binding`):
  the borrow slot yields `val_or_ref<T>` / `T*`; the consumer binds the loop var as a borrow and
  derefs for member access. The tuple-borrow consumer path already does this; the scalar pointer-slot
  consumer path is the main new consumer work. `Iterator[Own[T]]` binds an owned value (existing path).

### runtime (`runtime/cpp/include/tpy/`)
- No new helper expected: `val_or_ref`, `optional<T*>`/`expected<T*>` slots, and the tuple-pointer
  machinery already exist. Verify `next_iter.hpp` / `native_iterator` unwrap `val_or_ref` consistently
  for the scalar (non-tuple) case.

### migration
- `tests/cases/iterators/gen_ref_compose`: `my_map[T,U](fn, it) -> Iterator[U]` yields `fn(x)`. When
  `fn` returns a borrow (`identity`) this stays valid (borrow); it is only used with borrow-/value-
  returning `fn` today, so it likely needs **no change**. If a fresh-returning `fn` is added, the
  signature would be `Iterator[Own[U]]`. Re-verify on implementation.
- `tests/cases/iterators/error_yield_strview_dangle`: already an error case; `StrView` is a value-type
  view (out of scope). Confirm the diagnostic still fires (and via which check).
- Survey verdict: **~1 real migration, stdlib untouched** (`io.StringIO/BytesIO.__iter__` are borrow
  cases, unaffected). ~12 borrow-generator snapshots regenerate to the zero-copy form.

## Generic generators / `Ref[T]` reconciliation

Already aligned: the generic path *is* the borrow form D generalizes. After the fix, generic and
concrete reference yields share one code path (`val_or_ref<T>` slot). The dangling-yield check applies
to the generic path too -- a generic generator yielding a fresh reference (`yield make_t()` where
`make_t() -> Own[T]`) must declare `Iterator[Own[T]]`, same rule. No conflict with the documented
"reference preservation through generic generators" feature; D removes the concrete/generic asymmetry.

## Generator expressions (in v1) -- IMPLEMENTED

Shipped under the same `yield_uses_borrow_slot` gate (codegen `_genexpr_slot`, sema
`_analyze_generator_expression`): a durable-borrow genexpr borrows (mutable loop var, `val_or_ref<T>`
slot), a fresh-element genexpr is rejected pointing at `[...]`, and the consumer ephemeral check extends
to `GenExprType`. `Optional`/`Union`/tuple/`Own`/value elements keep the value (copy) slot; a `readonly`
element borrows through the same `yield_borrow_slot_cpp` spelling a def-generator uses. Tests:
`tests/cases/iterators/genexpr_ref_mutate`, `error_genexpr_fresh_as_ref`.

`(x for x in data)` over a concrete reference type **also copied** before this (confirmed: mutation did not
propagate). Genexprs are the *most common* generator syntax, so leaving them silently copying while
`def`-generators borrow would leave the headline bug visible through the everyday form and create a
def-vs-genexpr inconsistency. **Decision: include genexprs in v1** under the same classifier:

- A genexpr yielding a **durable borrow** (`(x for x in data)`, `(p.field for p in pts)`) -> BORROW_REF,
  zero-copy, CPython semantics. This is the overwhelming common case and the consumers (`for`, `sum`,
  `list`, `any`, ...) all consume element-by-element, so the per-iteration borrow window holds.
- A genexpr producing a **fresh non-value** element (`(Box(x) for x in xs)`) -> **rejected** by the
  classifier (genexprs have no `Own[]` annotation surface to opt into owned). The diagnostic points at
  the list-comprehension form `[Box(x) for x in xs]`, which already materializes owned storage -- so
  there is a clean, idiomatic escape and no need to invent a genexpr owned-annotation surface.

Value-type genexprs are unchanged. This keeps the rule mechanical (classifier-driven, no body-inferred
ABI exception) and fixes the common syntax in the same release.

## Tests

- `gen_ref_yield_mutate` -- borrow generator, consumer mutates, CPython-compared (the headline fix).
- `gen_own_yield_fresh` -- `Iterator[Own[T]]` with fresh yields.
- `error_gen_yield_fresh_as_ref` -- `Iterator[T]: yield Box(1)` rejected with the "use Iterator[Own[T]]"
  diagnostic.
- `gen_yield_field` / `gen_yield_param` -- borrow of `self.field` / a param outlives the frame.
- `error_gen_yield_dangle` -- borrow of a *local* (does not outlive the frame) rejected (subsumes
  BUGS.md:265/280 for the scalar case).
- generic: `Iterator[U]` (borrow fn) vs `Iterator[Own[U]]` (fresh fn).
- `@nocopy` element under `Iterator[T]` now **accepted** (was rejected) -- flip the existing
  `error_gen_yield_nocopy` cases.
- The resumable frame, the single generator emitter (the single-yield lambda peephole was deleted 2026-09-12).
- **Consumer-escape rejections (scalar + tuple):** `error_gen_borrow_escape_store` (stash a borrow-yield
  loop var into a list/field), `_return` (return it from the consumer), `_capture` (close over it),
  `_yield_onward` (re-yield without `clone()`); the `clone()` / `Own` conversion forms accepted. A
  parallel `error_gen_tuple_borrow_escape` proves the retrofit covers tuple yields.

## Docs

- `docs/LANGUAGE_FEATURES.md`: flip the generators "Restriction"/"Known divergence (bug)" entries to
  the new declaration-driven rule (Working); document `Iterator[Own[T]]`.
- This design doc.
- BUGS.md: mark the HIGH copy entry FIXED (covers both `def`-generators and genexprs in v1); update
  the cross-suspension borrow entries to note the local-yield case is now a hard sema error while the
  caller-mutates-container residual remains (at parity with tuple yields) -- do NOT mark them fully
  fixed.

## Risks / open questions

1. **Consumer scalar pointer-slot binding** is the main new codegen path; the tuple-borrow path proves
   the machinery but scalar is a distinct site (`loop_var_binding` + narrowing/field-access through a
   `val_or_ref`/`T*` loop var).
2. **`Iterator[Box]` semantics change** (copy -> borrow). This is more CPython-faithful, but any code
   that relied on the implicit copy (an independent object per iteration) changes behavior. CPython
   gives a borrow anyway, so the change is toward correctness; the only hard break is fresh-yield
   generators (-> `Iterator[Own[T]]`), caught at compile time.
3. **`@nocopy` + `Iterator[Own[T]]`**: owned yield moves, so `@nocopy` is fine; confirm the move (not
   copy) is what codegen emits for the owned slot.
4. **Tuple-inner fresh yields. (CLOSED -- see As-built reconciliation.)** The dangling/borrow check
   covers fresh values *inside* a tuple yield -- `yield (i, Box(1))` puts a dangling `Box*` in the
   `std::tuple<int32_t, Box*>` slot. `_analyze_yield` routes a tuple-typed yield through
   `check_dangling_reference`, whose existing per-element tuple branch runs the check per member.
5. **Cross-suspension residual** (see the invariant): D hard-errors local-yields but does not close the
   caller-mutates-container hazard; it stays at parity with tuple yields (partial warning). Not a
   regression, but the LANGUAGE_FEATURES / BUGS wording must not claim full borrow safety.
