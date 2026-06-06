# Implementation plan: unify tuple borrow element on `T*` (pointer form)

## Status: COMPLETED (2026-06-04, branch `unify-tuple-borrow-pointer-form`)

All phases (0, A, B, C, D, E) landed; full suite green. A follow-up
storage-provenance pass (2026-06-05, after an adversarial soundness audit)
closed the remaining binding/return/consumer-escape holes: storage-source
tuple locals now alias (`auto&&` binding, borrow registration, deferred
mutation-marking), borrow-tuple returns from storage roots lift with a sema
provenance gate (Own params / consuming self / locals rejected -- the gate
also made concrete Own-param borrow returns rejected generally, fixing the
scalar Own[Optional]/[Union] dangling-return BUGS entry), tuple loop vars
over generators are ephemeral (UAF closed; direct re-yield still allowed),
and walrus tuple bindings take the borrow form. Notable deltas from
the plan discovered during the Phase B pass:

- **Blast radius was larger than the measured estimate** (~24 additional
  reconciliation sites beyond the listed ones), including: generator/coro
  frame fields and frame locals, branch-hoisted tuple locals (new
  `borrow_form_tuple_locals` form), unpack binding (now a uniform
  `unwrap_ref(tuple_elem_ref(...))` reference binding), loop-var
  storage-form classification (now gated on native-iterable sources),
  const-on-read propagation, and lambda trailing return types.
- **Generics were made to work** (user decision; the plan's default was to
  reject): generic tuple elements use the new `tpy::val_or_ptr_t<T>` trait
  (pointer sibling of `val_or_ref_t`) with `to_val_or_ptr<Dest>` /
  `tuple_elem_ref` construction/read helpers, keeping generic and concrete
  tuple ABIs compatible.
- **Recursive-union wrappers and unions were EXCLUDED** from the `T*` form
  (they keep their `X&` / pointer-variant borrow forms); the durable-wrapper
  share remains a BUGS.md entry.
- **Access-as-value:** runtime `print_element` and `__hash__` gained `T*`
  deref overloads; tuple `==` / `!=` / ordering route through deref-aware
  `tpy::tuple_eq` / `tpy::tuple_lt`, and an `in` needle is lifted to the
  stored shape. `match` on tuple patterns is not supported by sema at all,
  so no comparison path exists there.
- The durable-copy sema rejection and its `copies_durable` fact plumbing
  were removed; the fresh-dangle rejection stays. The plan's
  "reclassify as an outlives-escaped-borrow lifetime fact" did NOT happen
  -- the fact was deleted outright. The
  `error_tuple_local_durable_*` cases were flipped to mutation-asserting
  happy cases (`tuple_local_durable_*`).
- **Phase 0's `TupleForm` facade was NOT built as designed.** The
  centralization landed as predicates/renderers on `TupleType`
  (`_element_is_pointer_repr` / `has_pointer_repr_element` /
  `_element_to_cpp_param`) plus
  `tuple_borrow_cpp` / `tuple_storage_cpp` on the types layer; the
  access-as-value layer is distributed across the consumer sites rather
  than a single facade op. A smaller THIR down payment than planned.
- **The "lifetime gate" audit was not performed as a dedicated step.**
  Coverage as shipped: the fresh-dangle rejection (kept), rvalue
  generator/coro args lifted into the awaiter frame (`_param_borrows`
  treats borrow tuples as POINTER kind), and the pre-existing ephemeral
  re-yield checks. The caller-mutates/frees-the-source-while-suspended
  hazard remains the same partial (warning-level) enforcement as
  pointer-form Optional params (BUGS.md:291 family).
- Borrow->storage conversion is ONE per-element dest-shape-dispatching
  converter (`to_storage_elem[_move]` under `tuple_to_storage[_move]`);
  tuples mixing nullable-Optional and plain reference slots work. The
  interim `tuple_to_value_storage[_move]` pair was removed.
- `statements.py` `_cpp_decl_type` kept the `auto` declaration for
  initialized tuple locals (the proven Optional precedent) instead of the
  planned explicit-type switch; explicit borrow form is emitted only for
  no-init branch-hoisted locals.

The sections below are the original working plan, kept for archaeology.
- **Repros** (recreate under `/tmp/agents/`, they are not in the repo):
  - #1 param silent-copy: `def gen(p: tuple[Int32, Box]) -> Iterator[tuple[Int32, Box]]: yield p`
    then consumer `pair[1].val = 99; print(b.val)` -- TPy 5, CPython 99.
  - #3 local-use build-fail: `def f(b: Box) -> None: t = (1, b); t[1].val = 99`.
  - function-return: `def f(b: Box) -> tuple[Int32, Box]: return (1, b)`.
  (Box is a 1-field record with `__init__`.)

## Goal

A non-value tuple element (record / list / dict / set / recursive-union
wrapper) currently has two borrow representations in generated C++:

- `T&` -- plain reference elements, via `TupleType.to_cpp_return()` ->
  per-element `to_cpp_return()` (NominalType non-value -> `T&`, RefType -> `T&`).
- `T*` -- pointer-repr `Optional` elements, via the existing
  `has_pointer_repr_optional_element()` path + `tuple_to_pointer` /
  `tuple_to_storage` / `to_pointer_form` runtime helpers.

`T&` cannot be a local, a generator/coro frame field, or a default-
constructed slot (references are not default-constructible or rebindable),
so any context that must HOLD a tuple with a plain reference member demotes
it to STORAGE form (`std::tuple<..., T>`, owns a value copy). That silent
demotion is the root cause of:

- **#1 (HIGH, silent divergence):** param-sourced reference-member tuple
  copies at the yield/return boundary. `def gen(p: tuple[Int32, Box]):
  yield p` then mutate -> TPy 5 vs CPython 99. No diagnostic.
- **#3 (MED, build fail):** durable reference-member tuple used locally
  (`t = (1, b); t[1].val = 99`) -> `std::tuple<int, Box&>` not
  constructible, raw g++ error.
- **durable-wrapper-member gap (LOW)** and the **call-return source
  residual (LOW)** -- both close once the local is pointer-form.

**Target:** render ALL non-value tuple borrow elements as `T*` / `const T*`
(the form already proven for `Optional` elements), so tuple locals / params
/ frame fields are both constructible AND aliasing. Storage form
(fields / containers / `Own[]` slots) stays value-owning `std::tuple<..., T>`;
the borrow<->storage conversion rides the EXISTING `tuple_to_pointer` /
`tuple_to_storage` machinery, generalized from "Optional element" to "any
pointer-repr element".

This is a UNIFICATION (one borrow form, one predicate), not a parallel path:
it shrinks special-casing, which is THIR-aligned. The `Optional`-in-tuple
precedent (14+ passing tests) proves the predicate-driven approach works
today; THIR (IR_DESIGN item 9) would later make tuple-form a first-class
fact and remove the per-site predicate dispatch, but is NOT a prerequisite.

## Current-state model (verified)

- `tuple[Int32, Box]` element_types = `(Int32, NominalType(Box))` -- the
  element is a bare nominal, NOT wrapped in `RefType`. Form is derived
  per-context by `TupleType` methods calling `to_cpp` / `to_cpp_return` /
  `to_cpp_stored` on the bare element.
- `TupleType.to_cpp()` -> `std::tuple<int, Box>` (storage, value Box).
- `TupleType.to_cpp_return()` -> `std::tuple<int, Box&>` (borrow, `T&`).
- `TupleType.to_cpp_stored()` -> per-element `to_cpp_stored()`; bare
  NominalType inherits `to_cpp()` so this is still `std::tuple<int, Box>`.
  (Only `RefType.to_cpp_stored()` gives `val_or_ref<T>`.)
- Two predicates split the world:
  - `has_ref_elements()` -- any non-value, non-Own, non-TypeParamRef
    element. NB this is a SUPERSET of the `T&` case: it also returns True for
    pointer-repr `Optional` (PTR_OPTIONAL) elements, which already render `T*`
    and work -- so it is not purely "the broken path". The broken subset is the
    plain-reference (BORROW_REF) elements rendering `T&`.
  - `has_pointer_repr_optional_element()` -- any pointer-repr `Optional`
    element. These render `T*` and use the conversion helpers. (the
    working path)

Runtime helpers (`runtime/cpp/include/tpy/format.hpp`):
- `to_pointer_form<Dest,Src>` (819): same->same, ptr->ptr (qual adjust),
  `optional<T>` -> `T*`. (Baseline lacked the value `T` -> `T*` addressof case;
  Phase A landed it -- see the REVISION / Phase A sections below.)
- `to_optional_form<Dest,Src>` (790): `T*` -> `optional<T>`, same->same.
- `borrow_value_elem<Dest>(Src&)` (904): value -> `T*` (addressof),
  same->same. Used by `tuple_value_to_borrow` -- ALREADY handles value->ptr.
- `tuple_to_pointer` / `tuple_to_storage` / `tuple_to_storage_move` /
  `tuple_value_to_borrow` -- per-element dispatch on Dest slot type.

## Target-state model

- Borrow form of a non-value tuple element = `T*` (mutable) / `const T*`
  (readonly). Replaces `T&` everywhere a tuple element is borrowed.
- Storage form unchanged: `std::tuple<..., T>` (value-owns the element)
  for fields, containers, `Own[]` slots.
- One predicate: rename/generalize `has_pointer_repr_optional_element()` ->
  `has_pointer_repr_element()` = "any element whose borrow form is `T*`",
  covering Optional-pointer-repr AND plain non-value elements. Retire
  `has_ref_elements()` (or redefine it as an alias of the unified predicate;
  decide during step 1 -- some callers want "has a borrow element at all",
  which is now the same set).
- Reads of a stored value element into pointer form go through
  `tuple_to_pointer` -> `to_pointer_form`, which gains a value->`T*` case.
- Generator/coro frame field for a borrowed tuple param holds the pointer
  form `std::tuple<int, Box*>` (constructible, aliasing) instead of
  demoting to storage `std::tuple<int, Box>`.

## REVISION after /co-validate (Codex + self review)

Two reviews converged on: doable now, NOT THIR-gated, unification is the right
direction. But both surfaced that a naive "render `T*` + generalize one
predicate" switch is unsound for value-level tuple operations and conflates
two distinct pointer semantics. Key accepted changes:

- **DECISIVE (verified): value-level tuple operations leak pointer semantics.**
  The runtime tuple printer (`print_element`, printing.hpp) has no
  deref-on-pointer overload, so a `Box*` element prints its ADDRESS; the same
  hits `==`, `match`, `in`, and any generated `std::tuple` operation. No test
  exercises print/compare/match on a pointer-repr element today (even for the
  Optional path), so this is unguarded. A bare predicate switch would expand
  this latent hazard. => We need an element-aware ACCESS layer, not just a
  rendering switch.
- **Do NOT collapse nullable-Optional `T*` and non-null-borrow `T*` into one
  semantic predicate.** They share a C++ spelling but differ: Optional may be
  null (`None`); a bare borrow is non-null and behaves like `T`. Unify the
  FORM-CONVERSION machinery, keep per-element semantics (nullable / access
  mode) distinct.
- **Build a small tuple-form facade FIRST (pull a slice of THIR item 9
  forward, not throwaway).** A codegen-internal layer (not a new type system):
  `TupleForm.{STORAGE,BORROW}` + per-element metadata `{storage_type,
  borrow_type, nullable, access_mode}`, with centralized operations:
  render-for-form, storage->borrow, borrow->storage (copy), borrow->storage
  (move), and **access-element-as-source-value** (the deref-aware accessor
  that print/`==`/match/`in`/subscript/unpack all route through). Add bare
  non-value borrow pointers as a SECOND element kind under this facade; keep
  the existing Optional path intact initially, then migrate call sites to ask
  the facade instead of inspecting predicates.
- **Separate runtime helper for borrow->value-storage** (`T* -> T` deref-copy
  / deref-move) -- do NOT overload `to_optional_form` to grow this; keep
  nullability + copy/move explicit.
- **`addressof` must be lvalue-only**: deleted rvalue overloads / constraints
  so the value->`T*` conversion can't manufacture a dangling pointer from a
  temporary.
- **Lifetime gate before relaxing any diagnostic (hard prerequisite).** A
  pointer-form frame field aliases the caller across suspensions. Prove the
  borrow checker rejects generator/coro escapes where the pointee may die
  before resume BEFORE relaxing the durable rejection. (Same hazard class as
  pointer-form Optional params -- BUGS.md:291 -- which today is only a
  non-fatal warning, so enforcement is partial; do not assume it's airtight.)
- **Reclassify, don't delete, the durable fact.** The durable-copy hazard
  becomes an "outlives-escaped-tuple-borrow" lifetime fact, not a deletion:
  param/global/member-rooted borrows are shareable, but a local that merely
  looks durable can still dangle when returned/yielded. Keep the fresh-dangle
  rejection unchanged.
- **Decide generics NOW, don't leave open:** for this change, REJECT generic
  non-value tuple-element cases (`tuple[T, ...]`, T a non-value type param, in
  a local/frame/borrow context) with a clean sema diagnostic; defer the
  generic pointer-borrow representation (`val_or_ref_t<T>` sibling) to a
  follow-up. Bounds scope.
- **Central return-ABI switch:** route ALL tuple borrow rendering through the
  facade; confirm no site bypasses `_element_to_cpp_param` (to_cpp_return /
  to_cpp_return_const / to_cpp_param_type).

Net effect: the change is larger than a predicate generalization -- it adds a
small tuple-form facade up front -- but that facade is the thing that makes
the value-operation access correct AND is a down payment on THIR item 9
(aligned, not throwaway). Revised phased steps below supersede the originals.

### Phase 0 (NEW) -- tuple-form facade (codegen-internal, no behavior change yet)

- Introduce the `TupleForm` + per-element-metadata abstraction and the
  centralized ops (render / convert storage<->borrow copy+move / access-as-value).
- Re-express the EXISTING Optional-pointer-repr path in terms of the facade,
  keeping generated code byte-identical (pure refactor; existing tuple tests
  stay green). This proves the facade covers the current behavior before any
  semantic change.

### Phase A -- runtime (`format.hpp`), self-contained, test first

1. Extend `detail::to_pointer_form<Dest,Src>` (819): add a branch for
   `Dest = T*` and `Src` a value/lvalue (non-pointer, non-optional) ->
   `return std::addressof(s);`. Mirror `borrow_value_elem`'s value->ptr
   arm. Keep the const static_assert. This lets `tuple_to_pointer` lower a
   stored value element to a pointer.
2. Storing a `T*` borrow tuple back into a value-storage tuple
   (`std::tuple<..., T>`, NOT optional) needs a `T*` -> `T` (deref-copy) case.
   RESOLVED (Phase A, landed): a separate `to_value_storage_form` /
   `tuple_to_value_storage` (+ `_move`) helper -- NOT an overload of
   `to_optional_form` -- so nullability and copy-vs-move stay explicit.
3. Unit-exercise via a focused tpy test before touching codegen broadly.
   (DONE: standalone C++ test, not in repo; Phase A is committed + verified.)

### Phase B -- typesys (`TupleType`), the representation switch

4. `TupleType._element_to_cpp_param(t, const)` (2913): for a non-value,
   non-Own, non-TypeParamRef element, emit `T*` / `const T*` instead of
   delegating to `t.to_cpp_return()` (which yields `T&`). Concretely:
   route plain non-value elements through the same pointer rendering
   `OptionalType.uses_pointer_repr()` elements already get. Value, Own,
   TypeParamRef, and nested-tuple elements unchanged.
   - TypeParamRef stays `val_or_ref_t<T>` (generic proxy) -- it is neither
     `T&` nor `T*` at the source level; leave as-is unless the survey of
     generic tuple call sites shows it must also become a pointer. FLAG as
     open question (generics + pointer-form tuples).
5. `has_pointer_repr_optional_element()` (2900) -> generalize to
   `has_pointer_repr_element()`; include plain non-value elements. Update
   all callers (sema + codegen, ~10 sites from survey). Decide fate of
   `has_ref_elements()` (2894): likely becomes the same set -> collapse to
   one predicate; keep a thin alias only if a caller semantically wants
   "borrow element present" distinct from "pointer-repr element present"
   (after unification they coincide).
6. `to_cpp_stored()` (2870) / storage form: confirm a stored tuple element
   stays value (`std::tuple<..., Box>`); no change expected, but verify the
   `val_or_ref` path (RefType element) doesn't double-wrap.

### Phase C -- codegen reconciliation (the ~15 sites)

7. Replace every `has_pointer_repr_optional_element()` call with the unified
   predicate (statements 210/952, expressions 1001/4976/5082, records 1270,
   gen_generators 363/445, context 1478, etc.).
8. `statements.py:952` -- remove the "demote tuple-local decl to `auto`"
   hack: a pointer-form tuple local is now a real declarable type
   (`std::tuple<int, Box*>`), so declare it explicitly and bind aliasing.
9. Tuple-local DECL + INIT: a `t = (1, b)` local now declares pointer form
   and the literal builds it via `tuple_value_to_borrow` (already does
   value->ptr). Reads (`t[1]`, unpack) go through `tuple_to_pointer` /
   already-pointer-form. Verify `_maybe_wrap_tuple_to_pointer` /
   `_maybe_wrap_tuple_to_storage` (statements 1511/1546) now fire for plain
   refs (they keyed on the Optional predicate).
10. Generator/coro frame field (gen_async 359-380, gen_generators 363-447):
    a borrowed tuple param's frame field becomes pointer form
    (`std::tuple<int, Box*>`); the ctor stores the pointer (aliases caller),
    the yield hands out `T*`/deref. Remove the storage-demotion. This is
    the site that fixes #1.
11. Consumption: `unwrap_ref(std::get<N>(t))` patterns that assumed `T&`
    switch to deref-of-`T*` (`*std::get<N>(t)` or the existing
    pointer-form read path). Subscript `t[i]` of a non-value element returns
    the deref'd pointer (a borrow), mirroring Optional.
12. `_gen_tuple_literal` (expressions 5136-5237): the mixed
    `std::tuple<T*, T&, U>` construction collapses to `std::tuple<T*, U>` --
    one borrow shape. Simplify the per-slot decision.
13. Field / container storage (records 1270, expressions 5082): a tuple
    field / `list[tuple[...]]` element stores value form; writing a
    pointer-form borrow into it uses `tuple_to_storage` (now value-target,
    Phase A.2). Reading back into a pointer-form local uses
    `tuple_to_pointer`.

### Phase D -- sema interaction with the just-merged hazard work

14. The just-merged `update_tuple_member_local_facts` /
    `_check_tuple_member_local` (compatibility.py) REJECT a bound-local
    tuple with a non-value member at yield/return because it could not
    alias. After this fix it CAN alias, so:
    - **Relax the DURABLE-copy rejection** (`copies_durable_tuple_member_vars`)
      -- the durable case now shares correctly; remove the rejection and the
      provenance tracking for the durable fact.
    - **KEEP the FRESH-dangle rejection** (`owns_fresh_tuple_member_vars`):
      a freshly-constructed local member still dangles even as `T*` (the
      pointer would point at a dead local), so `t = (i, Box(i)); return t`
      stays rejected and pointed at `Own[...]`. Pointer form does not make a
      fresh local outlive the function.
    - Net: `_derive_tuple_member_hazards` keeps the fresh branch + provenance
      (alias/ternary still matter for the fresh-dangle), drops the durable
      branch.
15. Param-source #1 and local-use #3 now COMPILE and ALIAS -- add/repoint
    tests (below). The param-sourced silent-copy BUGS entry and the
    durable-local-use build-fail entry are RESOLVED; update BUGS.md.

### Phase E -- tests + docs

16. FLIP the just-added `error_tuple_local_durable_*` cases (alias_yield,
    alias_chain, ternary, ternary_one_arm, self_assign, annotated_alias,
    alias_branch) from error -> happy cases that MUTATE the shared member
    after the boundary and ASSERT propagation (TPy == CPython == 99).
    These become the regression guards that the durable case now SHARES.
17. KEEP fresh-dangle error cases (error_tuple_local_fresh_wrapper_alias_return
    and the literal fresh cases) -- still rejected.
18. NEW happy cases: #1 param-sourced (`def gen(p: tuple[Int32, Box]): yield p`
    + mutate, assert 99), #3 local-use (`t = (1, b); t[1].val = 99`, assert),
    call-return source (`u = make(); yield u`), durable wrapper member.
    - **Test-adequacy (cpython-parity):** every happy case MUST mutate the
      shared member after the boundary and observe it (or use a `@nocopy`
      element type) -- a read-only output match is parity-blind and would pass
      even if TPy silently copied. Also add a print / `==` / `match` over a
      tuple-with-reference-member to lock the access-as-value layer (today
      `print_element` would emit a pointer address -- recipe step 3).
    - **Dedicated `addressof` round-trip:** a case that forces the
      `to_pointer_form` value->`T*` branch (stored value tuple -> pointer-form
      read) and the rvalue `static_assert`, not just implicit coverage via #1.
    - Add a `tpyc/` unit test asserting `value_form()` and the
      `has_ref_elements` / `has_pointer_repr_optional_element` equivalence,
      incl. the `force_pointer_repr` value-inner Optional corner, so the
      byte-identical claim is mechanically guarded.
19. Update LANGUAGE_FEATURES.md (the tuple-yield ABI paragraph: the
    bound-local form now SHARES for durable members; only fresh members are
    rejected) and BUGS.md (resolve #1, #3, durable-wrapper, call-return
    residual; note any remaining edge).
20. Full `uv run pytest`; snapshot regen across tuple/iterators/generator
    cases (factual: a broad set will regenerate).

## Risks / open questions (for /co-validate to stress)

- **Const-correctness.** `T&`/`const T&` -> `T*`/`const T*`. Readonly tuple
  elements and the readonly-tuple-unpack bug (BUGS.md:46, `T&` bound to
  `const T`) live in this family -- the pointer switch may fix it or need
  explicit const propagation at unpack. Verify the readonly path.
- **Generics / TypeParamRef.** Tuple elements that are `T` (a type param)
  use `val_or_ref_t<T>` proxies, not `T&`/`T*`. Do pointer-form tuples
  compose with generic functions/classes, or does `val_or_ref_t` need a
  pointer-form sibling? Possible scope creep.
- **Subscript-returns-pointer semantics.** `t[i]` of a non-value element
  becomes a deref of `T*` (a borrow). Confirm the borrow-checker treats it
  like the Optional/pointer-form subscript (BUGS.md has a const-propagation
  subscript entry for tuple-of-Optional -- same family, may need the same
  fix).
- **Nested tuples.** `tuple[tuple[..., Box], ...]` -- the conversion helpers
  are flat (BUGS.md LOW/MED nested-tuple entries). This fix does NOT make
  them recurse; nested borrow members stay rejected at sema. Confirm the
  unification doesn't accidentally unblock the (still-miscompiling) nested
  case.
- **Own[tuple] move semantics.** `tuple_to_storage_move` moves pointees
  into optionals. With value-storage targets (Phase A.2) the move path must
  also handle `T*` -> moved `T`. Verify Own[tuple[Box, ...]] still moves,
  not copies.
- **Frame-field lifetime / borrow-checker.** A pointer-form tuple frame
  field aliases a caller value across suspensions -- same cross-suspension
  borrow hazard already tracked for pointer-form Optional params
  (BUGS.md:291 family). Ensure the existing mutation-during-iteration /
  escape checks cover the newly-pointer-form tuple params; don't introduce
  a new silent dangle while fixing the silent copy.
- **Blast radius / sequencing.** ~20 codegen sites + runtime + sema
  relaxation. Recommend landing Phase A (runtime) + B (typesys) behind the
  unified predicate first, get the Optional tests still green, then C, then
  D, verifying tuple-group tests at each phase.
- **Does the durable-rejection relaxation (Phase D) belong in THIS change,
  or a follow-up?** Relaxing it before the codegen actually aliases would
  re-open the silent copy. Must land codegen (C) and sema-relaxation (D)
  together so there is never a window where the durable case is accepted
  but still copies.

## What this does NOT do

- Does not make the borrow/storage conversion recurse into NESTED tuples
  (separate BUGS entries, still rejected at sema).
- Does not introduce THIR's first-class tuple-form fact; it generalizes the
  existing predicate-driven dispatch. THIR can later subsume both predicates
  and the per-site wraps.

## Phase B blast radius (measured 2026-06-03)

Flipping `TupleType._element_to_cpp_param` so a BORROW_REF element renders
`T*` (instead of `T&`) was probed against the #1 repro
(`def gen(p: tuple[Int32, Box]) -> Iterator[tuple[Int32, Box]]: yield p`).
The switch alone is not green -- it exposes the full coordinated set of
reconciliations (confirming Phases B/C/D are entangled and must land
together):

1. **Generator/coro frame field + param stay storage form.** The yield
   return type becomes `std::tuple<int, Box*>`, but the param and frame
   field stay `std::tuple<int, Box>` (storage), so `return p` fails
   ("could not convert std::tuple<int, Box> to std::tuple<int, Box*>").
   Fix: render a borrowed reference-member tuple param + frame field in
   pointer form (so it aliases the caller AND matches the yield return).
   Sites: gen_generators.py / gen_async.py param+frame-field classification;
   statements.py setup_body_scope (`storage_form_tuple_locals`).
2. **Call-site tuple literal builds `T&` slots.** `gen((1, b))` emits
   `std::tuple<int, Box&>{1, b}`, not `std::tuple<int, Box*>{1, &b}`, so it
   no longer matches the pointer-form param. Fix: `_gen_tuple_literal`
   borrow-slot construction emits pointer slots + address-of (the existing
   `tuple_value_to_borrow` already does value->pointer); the local
   `has_ref_elements` reimplementation there folds onto the unified path.
3. **Consumption needs the access-as-value layer.** `pair[1].val` ->
   `std::get<1>` yields `Box*`, so member access must deref (`->`/`(*p).`),
   and the same applies to unpack, `print`, `==`, `match`, `in`. Runtime
   `print_element` has no `T*` deref overload (would print an address).
4. **Predicate generalization.** `has_pointer_repr_optional_element` -> a
   unified `has_pointer_repr_element` consulted at the ~15 wrap sites
   (statements / expressions / records / context / gen_generators).
5. **Sema relaxation (Phase D).** Relax the durable-copy rejection (now it
   aliases) to a lifetime/escape fact; keep the fresh-dangle rejection.
   Must land WITH the codegen so there is never a window where the durable
   case is accepted but still copies.

Conclusion: Phase B is a single large coordinated change (cannot be split
into byte-identical sub-steps once the type flips), best done as a focused
push driving the suite back to green, then the durable error-cases flip to
mutation-asserting happy cases. Phases 0 (value_form) and A (runtime
helpers) are the reusable foundation and are committed + green.

## Phase B implementation recipe (mapped 2026-06-03)

Deeper probe past the type switch identified the concrete edits per site.
Critical constraint: the BORROW_REF -> T* render switch is GLOBAL, so there
is no intermediate green checkpoint -- the whole cascade below must land in
one pass before the suite is green again.

1. **typesys** (`TupleType`): `_element_to_cpp_param` renders a BORROW_REF
   element as `T*` / `const T*`, unwrapping a `RefType` element first (its
   to_cpp() is already `T&`, so `T&*` is ill-formed -- use the wrapped type).
   Add `has_pointer_repr_element()` (PTR_OPTIONAL or BORROW_REF) and use it
   at the wrap sites.
   - **PRECONDITION -- pointer-variant unions.** A non-value `UnionType`
     element classifies as BORROW_REF today, but its borrow form is NOT `T*` --
     it is the pointer-variant `std::variant<A*, B*>` (`UnionType.to_cpp_return`).
     A blanket BORROW_REF -> `T*` render would mis-emit it. The render switch
     MUST special-case a pointer-variant union (keep its `to_cpp_return`), or
     add a dedicated `ValueForm.PTR_VARIANT` case at that point. Do NOT add the
     enum case in the foundation -- it would be dead code until this switch
     exists. The construction (step 2) and access (step 3) layers must likewise
     handle the variant shape (it already has `to_ptr_variant` / `std::get`
     machinery, parallel to the pointer-repr-Optional path).
2. **Construction** (`expressions.py::_tuple_literal_slot_info` +
   `_gen_tuple_literal`): for REF/CONST_REF mode on a BORROW_REF element emit
   the slot as `{base}*` / `const {base}*` (unwrap RefType for base), mirroring
   the existing pointer-repr-Optional branch. Render the element VALUE via an
   `_optional_pointer_form_value`-analog: `&(expr)` for a simple lvalue,
   pass-through for an already-pointer source; extend `want_pointer_form` to
   include BORROW_REF targets. Rvalue elements already route through
   `tuple_value_to_borrow` (value->pointer via `borrow_value_elem` `&s`).
3. **Access-as-value** (consumption): a `std::get<i>` of a BORROW_REF element
   is now `T*`, so member access derefs (`->` / `(*p).`), and tuple-unpack
   binds a pointer local. `print` / `==` / `in` / `match` must read the
   referent: add a `T*` deref overload to runtime `print_element` (and the
   tuple compare path), OR convert borrow->value-storage before these ops.
   This layer is only exercised once T* elements exist (lands with the switch).
4. **Generator/coro frame field** (`gen_generators.py` / `gen_async.py` +
   `statements.py` setup_body_scope): a borrowed reference-member tuple param's
   frame field becomes pointer form (`std::tuple<int, Box*>`), the ctor stores
   the pointer (aliases caller), the yield returns it directly. Remove the
   storage-demotion (re-gate on `has_pointer_repr_element`). This is the #1 fix.
5. **Predicate generalization**: replace `has_pointer_repr_optional_element`
   with `has_pointer_repr_element` at the ~15 wrap/gating sites (statements /
   expressions / records / context / gen_generators) where the intent is
   "borrow-pointer-form / needs storage<->pointer conversion".
6. **Sema (Phase D)**: relax the durable-copy rejection in
   `compatibility.py` (now it aliases) -- reclassify as an
   outlives-escaped-borrow lifetime fact; KEEP the fresh-dangle rejection.
   Land with the codegen so there is never an accepted-but-still-copies window.
7. **Tests**: flip the durable `error_tuple_local_durable_*` cases to
   mutation-asserting happy cases; add #1 (param), #3 (local-use), call-return
   source, durable-wrapper happy cases; keep fresh-dangle error cases.

Because there is no green checkpoint mid-cascade, Phase B is best executed as
one dedicated continuous pass (fresh context budget), driving the
tuple/iterators/union suite back to green before committing.

## Call-site inventory (for the predicate generalization)

`has_pointer_repr_optional_element()` -> generalize to `has_pointer_repr_element()`
at the wrap/gating sites whose intent is "borrow-pointer-form / needs
storage<->pointer conversion" (re-verify each: a few may legitimately stay
optional-only). Sites (line numbers approximate, re-grep):

- records.py:1271 -- field write wrap (tuple_to_storage)
- expressions.py:1001 -- iterator-element tuple_to_pointer wrap
- expressions.py:4996 -- is_storage_tuple element decision
- expressions.py:5102 -- container-element tuple_to_storage wrap
- context.py:1644 -- subscript/get tuple_to_pointer wrap
- gen_generators.py:363, 446 -- for-loop tuple iterator storage-form / auto&&
- statements.py:210 -- Own[tuple] param storage_form_tuple_locals
- statements.py:1478 -- _maybe_wrap_storage_tuple_source (unpack)
- statements.py:1521, 1560 -- _maybe_wrap_tuple_to_pointer / _to_storage
- statements.py:1674, 1894 -- var-decl / reassign tuple_to_storage wrap

`has_ref_elements()` (the TupleType method) call site:
- statements.py:952 -- demote tuple-local decl to `auto` (a pointer-form local
  is now declarable, so revisit: may no longer need the `auto` demotion).

Note: `expressions.py:5156` and `compatibility.py:707` define LOCAL variables
named `has_ref_elements` (not the method) -- the expressions.py one
reimplements element classification on the elem_capture axis (fold per recipe
step 2); the compatibility.py one is `_contains_semantic_ref` (sema coercion).
