# THIR Migration Completion Ledger

> **Operating model (2026-07-12):** the goal is COMPLETION (deleting the AST
> codegen), driven by the zero-whole-body-fallback loop in CLAUDE.md "THIR
> migration" (metric = migrated cases/fallback bodies, smallest per-construct
> residual prioritizes the next cluster, per-case machinery keeps it honest). AST
> body emit arms stay intact until the final atomic cutover. The deletion targets
> and completion model below map what blocks that cutover. The "sequence against routing %" /
> throughput-campaign framing predates this and is no longer how the work is
> driven.

The **deletion roadmap** for the THIR codegen migration: the single place that
answers *"what stands between us and retiring the AST body/form codegen path, and
what is the status of each piece."*

Companion docs, different jobs:
- `IR_DESIGN.md` -- the design + the distilled migration findings + the F1->F-final
  rung ladder (what *has* landed).
- `THIR_FORM_INVENTORY.md` -- the form-dispatch spec (the borrow/storage machinery
  THIR must subsume).
- `THIR_EMIT_INVENTORY.md` -- the emit-arm map: the finite AST codegen surface
  (~380 dispatch arms) with each arm's THIR status, parallel/serial tag, and
  leverage. The *what-to-port* checklist + the fan-out/sequencing plan; the
  shape meter (`tpyc/thir/shape.py`) measures progress against it.
- **This ledger** -- the completion/deletion tracker (what is *left* and what gates
  each deletion). **Sequence against this, not against routing %.**

## Why this exists

We have been sequencing by **routing ROI** (highest-value cells first). That is
right for *validation* -- it put F1/F2 under the whole-corpus byte-diff over real
code early. But ROI-sequencing **structurally diverges from completion**: the goal
is not "route N% of bodies," it is "delete the AST body/form codegen," and the two
part ways exactly in the low-ROI tail. "Circle back later" does not happen on its
own. This ledger keeps the tail visible and the end-state in view.

The real done-signal is zero fallback across every in-scope body kind plus a closed
ledger. Routing count is a useful dial but overstates progress in the tail; physical
AST deletion happens once, at the final cutover.

## North star and scope

**End state (this ledger's scope):** THIR is the codegen input for the
function / method / constructor **body + member-init-list** surface, and the AST
body/form codegen is deleted -- the F-final criterion in `IR_DESIGN.md`: *"the gate
excludes nothing form-related and the AST form-codegen is retired."*

**In scope:** body / expression / statement emission + the ctor MIL, across all
`(callable-kind x form x statement-shape)` cells.

**Out of scope (boundary -- so this map is not mistaken for "all of codegen"):**
- **Signatures** (params / return types) -- stay on the AST path (the M1 precedent;
  only bodies/MIL route). Whether signatures ever move to THIR is an open question,
  not part of the body migration.
- **Structural emission** -- record layout, protocol / enum / module-init /
  extension-glue. Not part of the THIR body migration.
- **MIR** -- ownership / move-optimization / loan-checking is a separate later
  stage (see the `IR_DESIGN.md` Status table); THIR completion is a prerequisite,
  not part of it.

## The completion model

THIR body coverage is a matrix of three axes; a body/MIL routes only when **every**
cell it touches is admitted:

- **Callable kind:** free function / instance method / constructor /
  static-property-dunder method / generic-record method / nested-record method.
- **Form / type-family** (the form ladder): scalar / record / Optional / tuple /
  union / generic-slot / str-bytes-view.
- **Statement & expression shape:** straight-line var-decl / assign / return /
  if-elif-else / while / range-for / aug-assign / for-over-container (value-scalar
  incr 30, record-element incr 31, tuple-unpack + dict-view incr 69) / expression
  statements (print + bare free-function
  call, incr 32) / container method-calls (list/dict scalar slice, stmt + value
  position, incr 33; one-level value-F1-record FIELD receivers
  `self.field.method()`, incr 98 -- container/Optional/non-F1/chain/subscript/
  call receivers deferred, the bulk `recv.field_nonf1` blocked on the
  non-F1-record frontiers) / container-literal locals (list/Array/dict/set scalar
  elements, incr 34) / logical and-or-not + inline chained compares (bool slice,
  incr 36) / break-continue (else-free loops, incr 66) / del (trivial del-var +
  list/dict del-item, incr 67; dynamic `del obj.attr` via the sema-synthesized
  `__delattr__` call) / global (scalar globals, incr 68) / sync `with`
  (fresh-target slice + the emit-side finally frames, incr 78) / try/finally
  (the finally_only tier + re-emittable finally frames + the plain-value
  branch-decl hoist, incr 79) / try/except throw tier + raise (catch arms,
  bindings, else labels, ctor/bare raise, incr 80) / match (the M1-M4
  ladder: scalar switches, if/elif chains, captures + all guard shapes,
  union switches incl. guarded, incr 81-86; record patterns + union
  field conditions/bindings, incr 92; optional partitions over
  pointer-repr subjects, incr 93; value-repr optional dispatch, the
  chain-optional tiers, str-switch, polymorphic dispatch, and
  storage-form subjects landed on later branches -- see the per-tier
  paragraphs below; parked tail = Literal subjects,
  M4c wrapper subjects, resumable) / docstring-`pass` trivia in any
  body position (incr 90) / comprehensions (the C1+C2 decl-init
  slice, incr 91; C3 routine rows -- 3-arg range, field iterables,
  Char slots, print-arg position, Array range arm -- incr 94;
  owned-move elements (list/set element + dict value) routed incr 95;
  open: Array-source arm, call-arg/return positions,
  narrowed-Optional iterables, C4) **(covered)**
  / async-await + yield (foundation + wave 2 route free/method async defs
  and value-scalar generators incl. methods through the shared-skeleton
  leaf seam, 2026-07-07 -- R2/R4/R4b/R1/R5b/R5c-param done; regions /
  non-value yields+returns / generic-record methods / str-Own-tuple-union
  params / ERASED-BORROWED awaits remain, TODO.md) **(covered)**
  / nonlocal (no-code face) + plain-assert messages (computed/lazy
  messages incl. gated clean-receiver field reads, constant-condition
  folds) **(covered)**
  / try-except return tier + the @error_return surface (function bodies,
  binds/discards, expression unwraps, pass-through returns) **(covered;
  aliasing borrow binds, method-call callees, owned-str first-decl binds
  still reject)**
  vs for-over-container (generators) /
  genexpr + the comprehension C3/C4 rows **(not)**. (`raise <expr>`
  now routes in a PLAIN body -> `<expr>.__raise__();`; a resumable /
  @error_return body still defers -- a conservative gap, the AST emit
  there is verified identical.)

A **deferred cell** is one `(kind x form x shape)` the eligibility gate rejects.
Two kinds, treated oppositely:

- **Self-contained** -- needs only work in its own frontier (e.g. ctor non-init
  bodies, ctor inheritance). *Discipline: finish per-frontier before moving on --
  do not leave these scattered.*
- **Cross-axis-blocked** -- needs a shared rung (e.g. a `tuple` *field* needs the
  F3 form rung). *Parked against that rung; closes for every callable frontier at
  once when the rung lands. Tracked here, not forgotten.*

## Deletion targets (AST component -> gate -> status)

The AST body/form codegen retires component-by-component as its cells fill. Each
deletion is the concrete milestone that forces its tail closed.

| AST component to delete | Deletion gate (cells that must route) | Status |
|---|---|---|
| **Ctor MIL emit** -- `records.py` `_extract_field_inits` / `_extract_base_inits` / `_get_non_init_stmts` + the ` : f(v)... {}` tail write (since the out-of-line ctor fix the AST signature spans TWO sites -- the in-struct decl in `_gen_record_decl` and the namespace-scope def in `_gen_ctor_def`; a full-THIR ctor emission must subsume both) | every ctor MIL field cell + body: scalar (M3a), record/Optional copy+move (M3b), ctor-call / param-field-read sources + own-optional params (M3b-rvalue), docstring/`pass` trivia body (M3c-trivia), non-init body + demotion (M3c-demotion), inheritance -- single + multi base + inherited-field writes (M3d), and str/list/dict/tuple/union/bytes fields (F3+) + cross-module/native/generic records | **PARTIAL** -- the whole scalar/record/Optional + body + inheritance surface lands (ctor self-contained tail complete); native + cross-module record field/base MIL routes (non-F1-record stages 1+2); GENERIC-record ctor MIL routes (F5 stages A-C); **wave-6 (the ctor frontier, 2026-07-07) routed the F3+ field families -- str/StrView/bytes, container literals + param copies/moves, `None` into any Optional/Ptr, unions, tuples -- plus the AST-demote mirror (bare-name / nested-def / body-local inits demote to the body like the AST), the ctor param grid (`_ctor_param_eligible`: any Own / String / Ptr / union / value-repr Optional), widened base-init args, record/container/str field writes, and the mutation-keyed ctor-arg record-rvalue rows**; the remaining `ctor.mil_field` mass (8,616 bodies) is generics-gated (`UninitStorage[T]`-family fields, `Box._ptr = heap_take(...)`) + async plumbing (`Waker`) -- see IR_DESIGN.md Wave-6 |
| **Body statement + expression emit** -- `statements.py` `gen_body` per-statement path + `expressions.py` `gen_expr`, for routed callables | every statement shape + expression form across every callable kind | **PARTIAL** -- straight-line shapes over the F1/F2 + scalar (fixed-int/bool/both-float-widths/BigInt incl. the runtime-BigInt `.to_fixed_check` narrows, incr 73-76; enums of every flavor incl. truthiness/`.value`/`.name`, incr 77) + str (F6 S1-S5 + cross-type coercions) + bytes (F6 S6 values + the incr-46 tail: subscript/slices/iteration/concat/aug-assign) slice, incl. logical/chained-compare exprs and scalar/slice-object type-constructor calls; + the thir-param-grid waves' value-repr Optional/union params+returns, membership, container params (now compositional -- see narrative below), tuple returns/unpack, method receivers incl field-chains, stepped range, str/bytes ctor decls + bitwise binops, generic-record instantiation, in-branch first-decls, Optional[record] field writes, print_optional_val |
| **Form / conversion machinery** -- `context.py` `convert` + `CppForm`/`FormValue`, the ~12 detection predicates + ~22 local side-sets + the `RefType` wrapper (see `THIR_FORM_INVENTORY.md`) | **F-final**: the `Form` tag + `THIRFormConvert` subsume all form dispatch | **PARTIAL** -- F1/F2 forms carried + F3 tuple read/write + the F6 str+bytes view->owned converts (S1/S6 -- both `view_to_owned_conv` family arms validated; reused at container-element slots by S5; extended to the cross-type str-family coercions in incr 42, the materializing arms lowering to the same THIRFormConvert) + the F4 union arms (U1 value + U2 pointer-variant, incr 49-50: `to_[const_]ptr_variant` / `to_value_variant<...>` carried, plus the structural form validator as the second gate) + the U3 narrowing extractions (incr 51: `THIRIsinstance` / `THIRNarrowAlias`, the `narrowed_vars` rename mirrored as lowering scope) + the U2 write-arm tail (incr 52: monostate write arms + union field-to-field copies) + the U4 narrowing tail (incr 53-54: `THIRAssert` persistent extractions + re-assert bump, while-isinstance loop-entry aliases, compound-`and` `THIRNarrowedRead` inline reads) + the D15 Any-isinstance narrow (`THIRAnyIsinstance` / `THIRAnyNarrowAlias`: typeid checks + any_cast branch aliases over the shared narrow-if skeleton, with the declared-type consumer mirrors -- print args RAW, truthiness `to_bool` -- pinned in `test_thir_any_narrow.py`; excluded rungs: while/assert-Any positions, compound conditions, global/indirect subjects, NoneType members' monostate arm) + the polymorphic-isinstance narrow (`THIRDynIsinstance`: the C++17 if-init cast condition composed via the extracted `narrow_cast_rhs` chokepoint shared with the AST emit, branch reads spelled `(*__p_ptr)` through `THIRName.cpp` and `_NarrowScope.spelled`; excluded faces: pointer-shaped subjects (`Ptr`/pointer-repr Optional -- the bare-name cast-arg spelling), tuple checks (multi-fact), while/assert positions (the fresh-reference-local face), resumables, `self` subjects, negation, and protocol-vs-protocol checks (the `if constexpr` structural-fold family, different machinery)); + the call-arg lifts and temps (incr 56-65: `THIRUnionArgLift` ptr-variant member/None arg lifts incl. the deep-const slot spelling + `ptr_variant_to_const`, `THIRCtorCall`, `THIRArgTemp` value-union/record-rvalue/Own-slot/optional-ptr arg temps with the `move`/`addr_of` wraps, `THIRMove` last-use moves, `THIROptionalPtrArg` nullptr/&(name)/optional_to_ptr faces, record-arg/method-receiver/self-receiver pass-throughs); F4 remainder (readonly narrowing subjects), F5, the remaining F6 tail, and RefType removal pending |

These interlock: deleting `gen_body`/`gen_expr` requires the form machinery gone
(F-final) and every statement shape routed. The **ctor MIL emit is the first
independently-deletable sub-component** -- it dies once ctor cells route (still
gated on F3+ for non-record fields).

## Deferred-cell registry

The map; the linked `TODO.md` cells carry the per-cell detail. Status: DONE /
deferred (self-contained) / blocked-on-`<rung>`.

### Callable-kind axis (M) -- `TODO.md` "method frontier" + "form rung F1/F2" cells
- Free functions: **DONE** (incl. readonly). Instance methods: **DONE** (M1/M2).
- Constructors: M3a scalar **DONE**; M3b record/Optional copy **DONE** + move
  **DONE** + rvalue (ctor-call / param field-read sources + own-optional params +
  the `copy()`-on-Optional source) **DONE**; M3c-trivia (docstring / `pass` non-init
  bodies) **DONE**; M3c-demotion (hoist/demotion split + non-trivia body lowered via
  the shared `_body_eligible`/`_lower_stmt` machinery -- only the `chain_broken`
  cascade needed explicit reproduction; the other demote triggers were subsumed by
  the existing eligibility gate) **DONE**; M3d inheritance (single + multi same-module
  F1 base: `super().__init__` / `BaseN.__init__` -> parent-order-sorted `THIRBaseInit`s;
  direct inherited-field writes -> body via the chain-stays-alive +
  `body_written_self_fields` + `expr_reads_self_field` mirror) **DONE**. **The ctor
  self-contained tail is COMPLETE.** Wave-6 (the ctor frontier, 2026-07-07) then
  routed the previously cross-axis-blocked mass: `str`/`list`/`dict`/`set`/
  `Array`/`tuple`/`union`/`bytes` MIL fields (F3+), demoted field writes via the
  AST-demote mirror + the record/container/str field-write arms, the ctor param
  grid, widened base-init args, and the mutation-keyed ctor-arg record-rvalue
  rows (see IR_DESIGN.md Wave-6). Wave-7 (the generics frontier, 2026-07-07)
  then routed the generics-gated remainder: the ctor INSTANTIATION form
  (`Cell[Int32]()` / `Poll[T]()` / inferred, `THIRCtorCall.type_cpp =
  render_type(call_type)`), the `UninitStorage[T]()`-family MIL hoists
  (zero-arg native instantiation), generic native/template free callees
  (`unsafe_take`/`unsafe_release`), and the T-operand compares
  (see IR_DESIGN.md Wave-7). Still open on the ctor axis: `self.<record
  field>` read sources (MIL-ordering-sensitive), non-trivia body statements
  outside the statement-shape slice (match / with / try / for-container / ...),
  the `heap_take(std::move(own_param))` MIL source (own-param move through
  call args in MIL context), the non-generic MIL source fams (`optional.name`
  / `nominal.call` / `record.call`), and `Waker` (async frontier). **NATIVE + CROSS-MODULE
  records DONE (non-F1-record stages 1+2); GENERIC records DONE (F5 stages A-C):**
  `_f1_record` now admits any non-generic concrete user record (native records
  need only the native_field-rename stamp in THIR field access -- `_field_cpp`;
  type via native_cpp_names, methods via fi.native_name already agreed;
  cross-module records qualify via native_cpp_names exactly as the resolver does,
  no stamp) AND generic user records whose args are in-slice (concrete: stage A;
  TypeParamRef: stage B/C -- ctor MIL `T` fields + method-body `T` reads and
  writes). Native +124012 (stage 1, ctor-dominated) + cross-module 463 (stage 2)
  + generics 169568 -> 245285 (F5 A-C) = 44745 -> 245285 bodies. The ctor MIL for
  a generic record's `T` field is DONE (bare-`T` copy / `Own[T]` move) and a
  method-body `T` field WRITE routes via the BORROW->STORAGE convert's
  TypeParamRef emit arm; the residual is `T` local decls (`Own[T]` method params
  now land -- see the `Own[T]` cell in the callable-kind axis). COVERAGE CAVEAT (/tpy-ready second-opinion):
  `_f1_record` gates ~56 sites but the win is ctor-dominated, so some non-ctor
  consumers (tuple element, union member, optional-ptr borrow) may be
  zero-witness for the newly-admitted native/cross-module classes -- the
  byte-diff proves identity only for witnessed flows. The high-risk shapes
  (Optional/Own/native-in-union) were probe-verified byte-identical during
  /tpy-review, and the byte-diff self-catches any divergence when a corpus case
  reaches such a site; a systematic per-site witness pass over the new classes
  is a cheap open followup.
- Static / property / dunder-operator methods: **DONE** (increment 48) -- all
  method kinds funnel their bodies through `gen_body`, so the differences are
  signature-only (the `static` prefix, the setter's `set_` rename, the getter's
  ref-return arm) and the cell only widens the gate. Static methods lower like
  free functions (no receiver; `record_name` kept so `_param_is_const` reads the
  method's FunctionInfo off the owning record, the `_get_method_mutated_params`
  mirror); property getters/setters lower like instance methods (the
  getter+setter pair shares one method name in the registry -- a two-entry
  overload list carved out of the shared-impl hijack gate, since each has its
  own body). Dunder-operator methods were ALREADY admitted as plain instance
  methods (`__eq__`/`__lt__`/`__str__`/... route since M1; the C++ `operator==`
  friend wrappers delegating to them are structural emission, not bodies) --
  now pinned by units. Deferred rows: inplace dunders (`__iadd__` ...; the AST
  forces const params via CONST_PARAMS_METHODS, a verdict `_param_is_const`
  does not mirror -- their `return self` render IS mirrored now, the
  return-slot tail's `ret.record_self` arm, so the const-params verdict is
  the sole remaining blocker), `@readonly` statics (emitted with the readonly
  verdicts dropped),
  pointer-repr Optional/union getter returns (the `in_property_getter`
  return-the-field-storage arm; rejected by the general return gate).
  `@total_ordering`-synthesized comparison bodies (same-record compare operands,
  a bare `self` deref to `(*this)` in value position) now ROUTE via the widened
  compare arm (commit 2bcd8d8dc: `dataclass_order`, `total_ordering_*`, the
  dataclass eq/order records flipped).
- Generic-record methods (templated `self`): **F5 stages A-C DONE** -- the whole
  generic USER-record axis routes. `_method_self_type` now yields the templated
  self `Record[T, ...]` (a TypeParamRef per type param) instead of None, opening
  the sig/ctor feed; `_f1_record_type_arg_ok` admits concrete args (`Pair[int]`,
  stage A -- external mentions) and TypeParamRef args (`Pair[T]`, stage B/C); the
  ctor MIL handles `T` fields fed by `T`/`Own[T]` params (stage B); method bodies
  route over `T` VALUES in the READ direction (stage C -- `T` field read / `T`
  param / `T` return, a form-neutral VALUE pass-through since the C++
  `val_or_ref_t<T>` traits resolve value-vs-ref per instantiation) AND the WRITE
  direction (a `T` field write via the BORROW->STORAGE convert's TypeParamRef
  emit arm -- a plain `field = v` copy / `field = std::move(v)` move, the same
  `e.move` decision as the sibling Optional/union/tuple arms). Routing
  169568 -> 245285 bodies. **Remaining generic-user-record cell** (self-contained,
  small): a `T` LOCAL decl (`x = self.value`) is unhandled and falls back
  byte-identically. (`Own[T]` method PARAMS now land -- see the `Own[T]` cell in
  the callable-kind axis; landing them surfaced + fixed a latent no-op-move-convert
  validator bug in this cell's `T`-field-write move arm.)
  **NB the `sig.receiver_record` mass (~1.65M) is NOT generic user records** --
  it is BUILTIN-type methods (str/int/list/dict/float/Char/Span/...), whose
  receiver is a builtin stub (not `is_user_record`) and whose bodies emit via
  specialization/native, OUT of the user-record body-migration scope. A prior
  survey conflated builtin generics (`list`/`dict`/`Span`) with generic user
  records; the generic-user-record surface is small and largely captured here.
- Generic FREE functions (`def f[T](x: T) -> T`): **DONE** -- a routine gate
  widening, no new machinery. `_function_eligible` no longer wholesale-rejects
  `func.type_params` for free functions: the resolver already spells each `[T]`
  param/return as a `TypeParamRef`, so F5's T-value arms (`_is_type_param_slot`,
  `_f1_param_eligible` / `_eligible_return` admitting TypeParamRef, the field/name
  read arms) route the body verbatim, and the template signature stays AST. The
  whole generic-free-function slice routes: `sig.generic_fn` 33k -> 9.8k.
  INT-kind params (`[N: int]`) route since the generics-foundation branch:
  `_seed_int_kind_tparams` seeds `N` as an INT TypeParamRef binding (bare-name
  reads + the two `int_type_param_*` coerce rows). The `T` LOCAL decl residual
  is shared with the generic-record-methods cell (falls back byte-identically as
  `T& y = x;`).
- Method-level `[U]` generics (`def m[U](self, x: U) -> U`): **DONE** -- the
  same routine widening; the `func.is_method` reject under `func.type_params`
  is dropped, leaving only the INT-kind rejection (shared with free
  functions). A method's own `[U]` slots spell as `TypeParamRef` exactly like
  a free function's; on a generic record the record's `T` rides the F5
  self-feed while the method's `U` rides the same slots, so both compose with
  no new arm. The method template SIGNATURE (const-inferred
  `val_or_cref_t<U>` / `const U&`) stays AST-owned. `Own[U]` method params
  ride `_own_type_param_slot` (already landed). `sig.generic_fn` -> 0 -- the
  SIGNATURE axis of generics is closed. NB the 9.8k tally was first-reject
  MASKING (the recurring lesson): only +11 bodies route solo; the mass
  re-attributes to the generic-method BODY tail, now honestly visible --
  T-typed binop returns (`stmt.return:binop.shape` +4.7k), `stmt.for_each`
  +1.9k, call-rvalue args +0.9k. Units
  `test_thir_generics.py::TestGenericFreeFunction{,Emit}` method rows.
- `Own[T]` params + returns (`def take[T](x: Own[T]) -> Own[T]`, and `Own[T]`
  METHOD params -- F5's filed residual): **DONE** -- a routine gate widening,
  `_own_type_param_slot` (predicates.py, the ownership-transfer sibling of
  `_is_type_param_slot`) added to `_f1_param_eligible` + `_eligible_return`. A
  direct `return <own-param>` passes bare (verified: no `std::move` on the
  return -- the move only arises at an intermediate local decl, the deferred
  `Own[T]` local-decl cell). Admitting the METHOD param surfaced a LATENT F5 bug:
  an `Own[T]`-param written into a `T` field (`self.item = item`) lowers to a
  STORAGE->STORAGE `THIRFormConvert` carrying `move=True` (emit already correct:
  `std::move(item)`), but the validator's no-op check ignored `move` and rejected
  it -- fixed to exempt move-carrying converts (aligns the validator with the
  node's documented `move`-in-identity contract). A /tpy-review round then caught
  the MIRROR case: a value-bound `T` (`T: ValueType`) `Own[T]` method param is
  passed by value and COPIED at a field write (`move=False`, mirrors the AST
  method body -- unlike the ctor MIL, which moves), so the field-write arm built a
  `move=False` STORAGE->STORAGE convert that IS a genuine no-op and crashed the
  validator. Fixed at the lowering site (statements.py): emit the bare source
  instead of a no-op convert when the source is already storage-form of the
  field's type with no move. Corpus-unwitnessed -> new case
  tests/cases/generics/value_bound_own_param_method. `sig.param_type` 70k->51k +
  `sig.return_type` 55k->35k (the own:typeparam param 19k + return 20k slices).
  HYGIENE FOLLOWUP (not a bug -- the two paths correctly mirror an AST asymmetry):
  the "is this `Own[T]` param movable" decision is computed twice -- unconditional
  in the ctor MIL (`own_param_names`), value-type-excluded in ordinary statements
  (`movable_locals`). Worth unifying into one helper so a future frontier can't
  reintroduce a MIL-vs-statement mismatch.
- Nested-record methods: **deferred (self-contained)** -- the feed walks top-level
  records only.
- Non-value **call arguments** (the `gen_call_arg` coercion cascade: auto-move,
  view->owned, union/tuple lifts) + call / `copy()`-write optional sources:
  **OPENED (incr 56-65)** -- the cascade splits on a probe-verified temp
  boundary. Temp-free rows landed (incr 56-57): record names into ref slots,
  user-record method calls, the ptr-union member/None inline lift, ctor
  rvalues into `Own[union]` slots, union-coerced literals. The **arg-temp
  facility is OPEN (incr 58)**: `THIRArgTemp` + the TempSink/CtxTempSink
  emission seam over the LIVE module-cumulative `ctx.temps` counter
  (THIR and AST bodies interleaved in one module number continuously),
  flushed at the four simple-statement positions (expr stmt / decl init /
  name assign / return), with two arms -- member-valued scalars into
  value-union slots and record-ctor rvalues into same-nominal ref slots.
  Follow-up rows landed (incr 59): deep-const (readonly-annotated or
  `deep_const_borrow_params`) ptr-union slots incl. `ptr_variant_to_const`,
  upcast Child->Parent NAME args, method ctor-rvalues into const slots.
  **Wave-3 tails landed (incr 60-65)**: the F2d non-ctor source face now
  shares free-call lowering's callee-shape head (`_plain_free_callee_ok`,
  incr 60); the Own[T]-slot cascade (incr 61) -- lvalue copy+move temps
  (`THIRArgTemp.move`), the temp-free last-use `std::move(name)`
  (`THIRMove`, Own params seeded into `lc.movable_locals` mirroring
  `seed_param_locals`), bare rvalues (coerced literals / scalar rvalues
  into `Own[scalar]`, same-nominal ctor / by-value record calls into
  `Own[record]`), plus fixes for two latent slot-blind holes (the scalar
  pass-through arm and the F2d arg loop both admitted Own-cascade slots
  bare -- the shared `_own_cascade_fires` guard); the pointer-repr
  Optional slot faces (incr 62, `THIROptionalPtrArg` + the `addr_of`
  temp wrap): nullptr / `&(name)` / bare already-pointer pass /
  `optional_to_ptr` field lift / ctor-rvalue `&(__tmp_N)` -- incl.
  NARROWED union subjects, which mirror as `&(<alias / inline get>)` on
  both paths (the review-round fix: the temp-free name face is
  lowered directly, so the face must mirror rather than reject);
  readonly-slot
  ctor rvalues route bare (incr 63 -- the ref-param temp arm keys on
  `is_ref_param()`, which the readonly wrapper defeats; the old
  "`const A` temp" note was wrong, probe-verified); field writes are the
  FIFTH flushable arg-temp position (incr 64); self-receiver method
  calls (`self.helper()` -> `this->helper()`, THIRSelf on the `is_arrow`
  render -- method AND ctor bodies; the largest single routing jump of
  the migration, ~+1.9k bodies) and method VALUE-union args (same-union
  names / coerced literals bare, member-valued scalars through the
  variant arg temp; value variants are const-blind so inherited methods'
  first-pass AST loop renders identically) (incr 65).
  Optional PARAMS landed (incr 71, the Optional-param cell):
  `_f1_param_eligible` admits pointer-repr `Optional[F1-record]` params,
  and every borrow-name face mirrors (params + OPTIONAL_TO_PTR locals
  uniformly, keyed on the DECLARED type -- narrowing is sema-side, so
  the mirror is per-node with no gate-side flow state): unproven
  reads/writes/aug-targets/method-calls via `deref_check`
  (`THIRFieldAccess.deref_check` widened to name receivers +
  `THIRMethodCall.deref_check`), proven ones via the indirect renders
  (the names join `lc.pointers`: `p->x`, `(*p)` record-slot args, the
  Own-slot `(*p)` copy+move temp, bare optional-slot passes), the None
  identity test (`THIRIsNone`, `(p ==|!= nullptr)`, operand-order
  canonicalized), truthiness (`if p:` bare / `not p` -> `(!(p))`,
  un-narrowed reads only), Optional-narrowing assert facts (no emit --
  the assert lowers as its bare condition), and the pointer-repr
  Optional RETURN slot (`prescan.ret_ptr_opt`: None -> `nullptr`,
  already-pointer names bare, F1-record names `&(name)` via
  `THIROptionalPtrArg`; field/call sources deferred). Still-deferred
  Optional-adjacent faces: const-SPELLING borrow-local decls off an
  Optional-ptr receiver (REF_ALIAS / OPTIONAL_TO_PTR / tuple-alias /
  union locals + borrow-tuple returns -- gate-rejected via
  `_const_exact_field_receiver_ok`; the AST const is now correct here
  (the narrowed-Optional-receiver seeding bug is fixed by seeding
  `const_indirect_locals` from `deep_const_borrow_params`). Widening this
  gate is now unblocked BUT requires a matching THIR change: an Optional-ptr
  param lands in `deep_const_borrow_params`, NOT `const_borrow_params`, so
  `_f1_const_rooted_source` (which consults `_param_is_const` =
  `const_borrow_params`) must also consult `_param_is_deep_const` for the
  Optional-ptr param base name -- else THIR spells the routed borrow-local
  non-const while the AST spells it const and the byte-diff diverges), `readonly[A | None]` sources at
  `_is_borrow_ptr_local` (the write/return `ptr_to_optional` gate does
  not unwrap readonly), narrowed Optional names into UNION slots
  (`_union_member_lift_arg` keys on the declared type), and the
  storage-field / call return sources above.
  **Protocol slots LANDED** (`thir-protocol-boundary`, `_protocol_arg_slot` /
  `_protocol_arg_temp`): a bare @dynamic or single-required-structural slot
  takes the Adapter / RefAdapter / concrete-materialization / `auto`-rvalue
  temp rows. `Own[P]` and the typed-null `Optional[P]` / protocol-union
  spellings stay deferred; so does an `Iterable[Own[T]]` slot, whose
  `::tpy::own_iter(std::move(x))` rewrite lives in `gen_call_arg` itself
  rather than in a protocol pre-arm.
  **Still deferred**: the elif-chain-abandon + statement-expr temp
  relocation (ARCHITECTURAL -- design first), method POINTER-variant union slots
  (the deep-const threading differs between the AST's own-record and
  inherited first-pass arg loops), coerce-wrapped lvalues into Own slots
  (the AST's rendered-identity `needs_copy` split), record field reads
  into Own slots (unsupported during lowering), `Own[T] | None` slots
  (`ptr_to_optional[_move]` wrap arms), borrow-returning callee args
  (copy-through-temp), the `Own[Opt[P_ref]]` param lift at optional-ptr
  slots, and subscript sources. Two structural notes for future rows:
  `_LowerCtx.movable_locals` mirrors ONLY seed_param_locals' Own-param
  branch (the owned-movable-tuple and expensive-copy-value-Optional
  param branches are unmirrored -- inert while the Own-slot rows admit
  scalar/F1-record payloads only; a frontier reusing the set against
  tuple/Optional sources must extend the seeding), and the validator
  has no position/shape rules for `THIRMove` / `THIROptionalPtrArg`
  (the byte-diff backstops; add rules if either node gains new
  positions). Covariant slots DISSOLVED into the F5
  rung: covariant conversion requires a generic record, and generics are
  gate-rejected wholesale -- not a self-contained call-arg tail. Upcasts
  into Own slots are sema-rejected outright (no THIR face needed). (The
  no-cascade subset OUTSIDE it -- bare-name container args into non-Own
  concrete container slots, where `own is None` and no branch fires --
  landed separately as incr 35's pass-through widening.)
  **Mirror-vs-gate-reject criterion for pre-existing AST miscompiles**
  (made explicit after incr 56-59 decided it case-by-case): MIRROR
  byte-identically when the divergence is spelling-only and the bad C++
  is toolchain-caught identically on both paths AND the mirror needs no
  new machinery (the const-blind member lift, the narrowed-alias-into-
  variant render); GATE-REJECT when mirroring would reproduce wrong
  runtime behavior (the while-condition stale-snapshot hoist -- since
  fixed on the AST path, so that arm now awaits an ordinary mirror of
  the restructured loop head, not a gate) or would
  add mechanism solely to reproduce a bug (the method mutated-ref rvalue
  arm). A rejected row cites its BUGS.md entry at the gate.
- **Record return slots, BOTH directions: LANDED.** BORROW (`-> Box` -> C++
  `Box&`, `_record_borrow_return`): the return arm admits only bare record
  borrow names (record params / REF_ALIAS locals -- `return name;`,
  render-identical; face `ret.record_borrow`). STORAGE (`-> Own[Box]` -> `Box`
  by value, `_record_storage_return`): bare names (owned local NRVO / `Own`
  rvalue-ref-param C++ implicit move -- render bare) plus record-rvalue ctor /
  by-value calls (`return Box(n);`, the bare expansion via
  `_record_rvalue_source_shape`; face `ret.record_storage`). Its ctor face's arg
  loop was UNIFIED onto the shared pass-through cascade
  (`_shared_pass_through_arg`, the free-call face's set): a non-mutated (const)
  ctor slot admits every temp-free row the ctor lowering already emits via
  `_lower_call_arg` -- adding Ptr/str/bytes/char/container/slice/enum/value-tuple/
  record-NAME to the former scalar-only subset (`return Rec(self.ptr)` etc., the
  dominant `return.record_source.call` blocker). A MUTATED slot (`T&`) keeps only
  the by-value rows: a temp/literal/owned-conversion into a non-const ref is the
  mutated-String-param AST miscompile (BUGS.md), so reference-type rows reject
  there; the record-rvalue arg stays mutation-keyed. The return-slot
  TAIL then landed `return self` (`return (*this);` via THIRSelf(deref=True),
  face `ret.record_self`), `return recv.field` (the bare storage-form field
  read, face `ret.record_field`), and record-rvalue sources at the
  borrow-classified slot (VALUE-type records only -- a non-value record
  rvalue at a plain `-> Box` slot is a sema dangling-return error).
  Pointer-locals (`(*p)` + move) and narrowed names stay AST
  (`return.record_source`); sema itself rejects borrow returns of
  locals/temporaries AND borrowed-source `Own` returns without `copy()`, so
  those shapes never reach the gate. The validator's `_borrow_legal_return`
  learned the storage direction (a BORROW name at an `Own[record]` return is
  the spelled-convert-free NRVO/implicit-move shape). Landed WITH the
  co-blocking **owned record local decl** (`_owned_record_decl_ok`, face
  `decl.owned_record`): a single-assignment record-rvalue init lowers as the
  plain value decl `Box b = Box(n);` / `Box x = make(1);` (the binding
  classifier's OTHER arm -- no indirection, `.` reads via the existing
  declared-type-keyed receiver gates, last-use moves via sema's movable set);
  reassigned names keep the F2d REBIND_SLOT machinery, hoisted / move-through
  ones stay AST. The `return.record_source` residual (~3.3k) is storage-slot
  CALL sources blocked on the general call-arg rows (coerced-literal args,
  nested-call rvalue args, ctor shapes) -- the call-arg axis, not
  return-specific.
- **Slot-hoist pointer-repr locals: v1 LANDED (branch thir-ptr-local-slots).**
  The `OPT_PTR_SLOT` classifier verdict (shared `classify_local_binding`, the
  Optional sibling of REBIND_SLOT) + `THIRPtrLocalDecl`/`THIRPtrLocalRebind`
  route pointer-repr `Optional[T]` locals with None / exact-type F1-rvalue
  inits (`T* x = nullptr;` with the `std::optional<T>` rebind-slot pre-decl;
  `T __slot_N = ...; T* x = &__slot_N;`), None reseats (`x = nullptr;`) and
  rvalue reseats through the pre-declared slot (THIRAssign's rebind-slot arm,
  `&*(__slot_N = ...)`), plus ptr-variant union locals from concrete-member /
  whole-union rvalues (value-variant slot + `to_ptr_variant`; reseat via
  `.emplace` + re-lift) and concrete-member lvalue addresses (`v{&(name)}`).
  Slot NUMBERING mirrors `SlotState` allocation order per body; union
  slot-holders are excluded from THIRAssign's optional-slot reseat arm via
  `_EmitState.union_slot_locals`. Branch-first hoists LANDED (if-head +
  lazy function-top slots via `THIRIf.hoist_slots` / `BRANCH_RVALUE`, plus
  the `PTR_ADDR` lvalue name/subscript reseats; inner-scope non-value
  hoists still reject -- `if.hoist_inner_scope` (any in_branch body:
  branch, loop, with, try)). Wave-5 (branch `thir-grind-wave5`, dial
  1893 -> 1905): the `branch_scope()` registry (context.py
  `_BRANCH_SCOPED_SETS`) centralized every branch-scoped lc name-set
  restore (fixed the sibling-branch `value_opt_locals` leak -- corpus
  case `optional/branch_local_opt_reuse`); branch-FIRST admission went
  per-arm in the borrow-decl cascade (POINTER/REF_ALIAS/OPTIONAL_TO_PTR/
  REBIND_SLOT/tparam-call/owned-record route in-branch; dyn/OPT_PTR_SLOT/
  storage-tuple/comprehension/copy-record/move-through keep `fn_top`
  guards pending oracle witnesses); the WITH family gained the
  OPTIONAL_STORAGE hoist flavor (`_optional_storage_hoist_entry`, shared
  with the if cascade) + in-branch VALUE/REF targets + the ASSIGN_OPT
  optional-slot target assign (`with.opt_slot_target`); MATCH arms admit
  branch-first decls at every tier; the standalone unpack's
  declared-name tail covers hoist-predeclared fresh targets. Wave-5
  post-review increments (dial -> 1913): the BORROW-TUPLE HOIST family
  (`if.hoist_borrow_tuple` / `foreach.hoist_borrow_tuple` --
  `std::tuple<..., T*> name;` predecl via a shared admission
  `_borrow_tuple_hoist_ok` (non-const sources only; const bit /
  owning-call / walrus / pending-or-literal elements / resumables
  reject as named rungs); reseats `btuple.reseat_literal` /
  `btuple.reseat_lift` keyed on the DERIVED borrow classification
  `_borrow_tuple_local_type` (declared ptr-repr tuple minus storage
  registrations -- params excluded from RESEAT, the AST param-reassign
  arm is a filed BUGS.md break); the loop flavor threads
  `THIRForEach.hoisted_tuple_lift_cpp` into the shared
  `loop_var_binding`); the nested container-elem tuple read
  (`subscript.tuple_elem_recv`: `std::get<N>(__getitem__(items, i))`
  receivers, eligibility off the container's DECLARED element tuple);
  and the container-literal element widenings (set record elements,
  record METHOD-rvalue elements + `setitem.record_method_rvalue`
  exact-type values via BORROW_BIND, value-union LITERAL elements).
  Remaining deferred rungs
  (const indirection, Own[Opt] lifts, polymorphic slots, pointer-copy /
  storage-lift reseats, slotless union reseats) are named rejects -- see
  the TODO.md entry. Witnesses:
  `pointers/optional_basic`, `none_safety/concrete_assignment_proves`,
  `inference/reassign_none_branch_narrowing`; the union emit arms are
  corpus-zero-witness (host bodies co-block on call-track shapes) and
  unit-pinned in `test_thir_ptr_locals.py`.
- **Call-cascade cells: LANDED (branch thir-call-cascade).** Own-move /
  Own-lvalue rows joined `_record_ctor_arg_supported` (temp_args threaded
  into the ctor arg tail scoped to Own-slot args; the two bare reject sinks
  now record note_detail); marker/qualcall F1-record RVALUE returns admitted
  at BORROW_BIND/STORAGE uses (`Rc<State> r = Rc.new_(...)`); ctor
  list-literal args brace-init in place (`Numbers({1, 2, 3})`, list-slot /
  ctor-position only); `self` as a record call arg (`on_init((*this))` via
  the `_record_pass_through_arg` self arm); set method receivers via the
  dedicated `_set_method_recv` family (deliberately NOT a
  `_container_scalar_read` widening -- sets have no subscript/for-each
  consumers) with stub args threading the RAW param type
  (`method_arg_stub`); owned-str RVALUE element args bind the `T&&` slot
  bare; free-call-result method receivers (`make(3).get()`); empty
  instantiations (`set()`/`list()`/`dict()` spelled default ctor) +
  zero-args-all-defaults marker calls omit trailing defaults
  (`datetime::now()`). Deferred residue: the TODO.md call-cascade entry.
- **Ctor-MIL cells: LANDED (branch thir-ctor-mil-cells).** Value-repr
  Optional field bare-copy from a same-typed optional param
  (`ctor.mil_field.optional.name`, scalar/enum inners, `value(value)`);
  Callable field bare-copy (`on_event(cb)`); pointer-repr tuple fields from
  spelled literals (`tuple_to_storage` over a per-slot-admitted brace init,
  with `THIRRecordCopy` rendering `copy(p)` as the copy-ctor call `T(p)`).
  Deferred residue: the TODO.md ctor-MIL entry (genrec fields pending the
  `_f1_record` generics policy; the `assign.field_write_shape` body-assign
  sibling reuses the same element builder).
- **Ptr[T] value family: LANDED.** `_eligible_ptr_value` (pointee must spell
  byte-identically: F1-record / eligible scalar / Char / void; readonly
  pointee -> `const T*`) joins every value-slot set at once: return + param
  sig gates (ctor params via `_f1_param_eligible`), field-read results
  (`return self._p`, the stdlib handle shape), field writes (plain value
  assign -- the lowering dispatch gained the matching arm), ctor MIL value
  fields, call returns, the shared pass-through arg row
  (`_ptr_pass_through_arg`), and local decl slots (`Node* q = f(p);`). All
  renders are the existing bare value renders; face `ptr.value_slot`.
  MEMBER access through a Ptr (`p.val` -> `::tpy::deref_check(p).val`, the
  non-null-proof render) is NOT mirrored -- such bodies fall back. Routing
  +3448 bodies (300355 -> 303803); ctor fallback 19.4k -> 17.6k
  (`ctor.param_type` 7.1k -> 5.0k).
- **Non-`DEFAULT`-linkage call symbols** (`@native` / `@native_c` / `@export(binding="C")`
  callees): **MOSTLY LANDED (incr 95+96)** -- `_free_callee_kind` classifies the
  free-callee emit (plain / imported `callee_cpp` / native `native_name` / positional
  `cpp_template`), so C++ `@native`, `@cpp_template`, and cross-module TPy callees all
  route. Residual deferred rows: extern-C / `@native_c` (raw unqualified symbol -- a
  different spelling arm), `@export(binding="C")` linkage, `native_cpp_return_type`
  static_cast wraps, the bespoke-arm builtins, non-positional templates, and the
  pre-arm/kwarg-dependent arg rows for native callees (`call.native_arg_shape`).
- **Shadow-colliding records** (a member named like a same-module type -- the
  member/type-name collision fix): **whole-record reject** (`sig.member_shadows_type`
  for method bodies, `ctor.member_shadows_type` for constructors). The AST path
  qualifies local type references inside such a record (`::mod::day`) via the
  `_qualify_shadowed_nominals` flag; THIR emit renders raw baked names (ctor-call
  callee, base-init) that don't consult the flag, so it defers the whole record to
  AST. Rare (currently ~just `datetime`). To CLOSE before AST deletion: teach THIR
  emit to qualify a shadowed local ref -- read the same `RecordInfo.shadows_local_type`
  fact and qualify the ctor-call/base-init/type sites under the flag, instead of
  gate-rejecting. Surfaced by /tpy-review of the member/type-name collision fix.

- **Empty container literal at a resumable return** (`return.empty_container_literal`,
  `_lower_resumable_return_value`): the async return render is position-blind, and the
  AST spells an empty literal's type only when a target is passed, so it emits a bare
  `= {}` where THIR spells `std::vector<T>{}`. Both compile identically; the reject
  exists purely to mirror the AST spelling. To CLOSE before AST deletion: delete the
  guard and let THIR spell the typed form -- the untargeted `{}` was the AST path's
  limitation, not a correctness constraint. Surfaced by the /tpy-ready retrospective of
  the resumable container-return cell.

### Form ladder (F) -- `IR_DESIGN.md` rung ladder + `THIR_FORM_INVENTORY.md`
- F1 (record locals + Optional read), F2 (reseatable pointer-locals + Optional
  write/return + move): **DONE**.
- **Value-scalar family widening DONE (increments 73-76)** -- `_eligible_scalar`
  now spans fixed-ints, bool, BOTH float widths, and BigInt, plus the
  `_eligible_enum` sibling (same-module non-@native top-level enums), so
  every scalar-keyed gate admits them at once. Mechanisms (reused by any
  future family): target-typed literal renders via `_slot_literal_retype`
  at the slot sites (Float32 `f` suffix, BigInt ctor wraps),
  `THIRCoerce.wrap` `{0}` templates for the scalar-cast coercion family
  (incl. the previously-unrouted float64 casts + `fixed_int_widening` +
  `bigint_to_fixed_int`), and per-side `THIRBinOp.left_cast`/`right_cast`
  operand wraps (int-enum underlying casts, mixed BigInt/float compares).
  The runtime-BigInt NARROW positions landed as increment 76: subscript
  indices / dict keys / `del c[k]` / slice bounds
  (`.to_fixed_check<T>()` at the receiver's declared key width via
  `_bigint_index_disposition` + `_narrow_bigint_index`, one helper shared
  by gates and lowering; a BigInt-keyed receiver passes the key through
  unnarrowed),
  BigInt range counters, FixedInt-target aug-assigns fed BigInt values
  (`THIRBinOp.right_cast`), BigInt-arg `E(x)` (underlying-typed wrap),
  BigInt-keyed dict receivers, and the resolved-container-kind retype fix
  (ARRAY literal elements thread their scalar target; vector elements
  stay bare). Still-deferred narrow shapes (all literal-render mismatches,
  each gate-rejected explicitly): out-of-int32-range literal indices
  headed for a narrow (a BigInt-keyed receiver's big literal key routes --
  it renders unnarrowed through the shared literal renderer),
  literal slice bounds resolving BigInt (the AST render is ill-formed C++
  -- BUGS.md), literal-BigInt `E(x)` args, the BigInt-arg NESTED
  `Outer.Kind(v)` form (its method-call gate keeps the reject);
  subscript-TARGET aug-assigns
  (`xs[0] += b`) ride the statement-shape axis (subscript writes are not
  in the slice at all). Float32/enum/BigInt members in value unions are
  admitted by the widened predicates wherever the union gates key on
  `_eligible_scalar` (byte-diff-validated).
- **Enum remainder DONE (increment 77)** -- `_eligible_enum` spans every
  registered enum flavor: cross-module (qualified), @native (rename map +
  `PrintForm.REPR` `::tpy::__repr__` print arm), nested (`Outer::Kind`;
  sema stamps `enum_member_of` on the chained access too). New rows on
  `THIREnumWrap` `{0}` wraps: truthiness (if/while/assert/`not`; the
  plain-enum literal-`true` fold gate-rejects CALL operands -- the AST
  drops their side effects, BUGS.md), IntEnum unary minus, `.value`,
  nested `Outer.Kind(v)` from_value; IntEnum arithmetic rides
  the existing resolved-binop operand casts. `.name` (initially RETRACTED
  post-review -- sema typed it owned `str` while the value is a
  static-storage view, so the AST rendered it bare into owned-str sinks,
  ill-formed C++) is RE-ADMITTED via `fix-enum-name-strview`: sema now
  types `.name` StrView, both paths take the standard view->owned copy at
  owned sinks, and the wrap lowers BORROW-tagged (`enum.name` face,
  corpus-witnessed). enum ITERATION (`for c in Color:`, the
  `enum_iterable` for-loop arm) now ROUTES (`foreach.enum` face), as does
  name-lookup subscript `Color[name]` (`subscript.enum_from_name`).
  Still deferred: match-over-enum (the match-statement axis).
- F3 tuples: **PARTIAL** (increments 22-24) -- storage->borrow read
  (`tuple_to_pointer`: borrow-form tuple return + storage-tuple `auto&&` alias locals)
  + borrow->storage write (`tuple_to_storage`, tuple-field write off a borrow tuple
  param) DONE for pointer-repr tuples of scalar / F1-record / `Optional[F1-record]`
  elements. Value-result + record + Optional element subscript reads landed via the
  statement-shape axis (increments 25-27, the tuple-READ frontier; see below). **Deferred
  (blocked-on later F3 cells / the statement-shape axis):** storage-Name alias sources,
  reassignable BORROW_TUPLE / OPTIONAL_BORROW_TUPLE, the
  `tuple_to_storage_move` `Own[tuple]` move arm, loop-var / unpack sources,
  tuple-literal MIL construction. **Note:** further F3 form cells gain little routing
  until more of the statement-shape axis (the remaining subscript cells, for-loops,
  tuple-unpack) lands -- most corpus tuples are accessed that way.
- F4 unions: **OPENED (U1 value unions + the structural form validator,
  incr 49)** -- value-form scalar-member unions (`Int32 | Float64 [| None]`
  -> `std::variant<...>`, no borrow/storage duality): params, locals
  (decl + reassign), returns, same-type compares (variant's own operators,
  the rb=None bare arm), same-union bare-name call args (member-valued args
  hoist `__tmp_N` variant temps -- the gen_call_arg cascade frontier ->
  AST); a `None` source renders `std::monostate{}`, target-typed at
  lowering. Narrowing-divergent union reads (`_union_binding_divergent`)
  and union-vs-member compares are gate-rejected -- both are pre-existing
  AST miscompiles (BUGS.md). Routing unchanged at U1 (corpus union code
  narrows / prints / matches, all still gated -- the F2 precedent: units
  are the per-rung net). **U2 pointer-variant conversions DONE (incr 50)**
  -- the actual form rung: F1-record-member pointer unions (`A | B` ->
  borrow `std::variant<A*, B*>` / storage `std::variant<A, B>`). The
  `THIRFormConvert` union arms (`to_[const_]ptr_variant` at BORROW,
  `to_value_variant<...>` at STORAGE) mirror `context.convert`'s;
  `LocalBinding.PTR_VARIANT` locals (bare borrow copy of a same-union
  name; `to_[const_]ptr_variant` field lift, const from the receiver;
  name-source reseats -- field reseats stay AST, a per-reseat const
  chain); union-field writes from borrow names; borrow passthroughs
  (params / same-type args / borrow returns -- all bare); the ctor MIL
  own-param move (`u(std::move(v))`, `Own[A | B]` admitted at the ctor
  param gate); and the `Own[...]`-param STORAGE name-form fix (an own
  param owns its storage -- the validator's sink rule caught the BORROW
  mislabel). WITH it: the validator's sink table (pointer-lifted
  field-write/MIL sinks reject BORROW values; BORROW returns need a
  borrow-legal type) and the `THIRBytesLiteral.owned` -> form fold.
  Gate-rejected (pre-existing AST miscompiles, BUGS.md): storage union
  fields at returns / call args, const-lifted locals into mutable slots.
  **U2 write-arm tail DONE (incr 52)** -- None-member ptr unions (the gate
  admits void-like members; monostate is form-neutral in both spellings)
  with the monostate write arms (`w = None` decl/rebind,
  `recv.field = None`, `return None` -- all `std::monostate{}`,
  target-typed like the U1 arms) and field-to-field copies
  (`recv1.f = recv2.f`, a storage-to-storage plain assign -- a field
  source is not a ptr-variant source, no `to_value_variant` lift). Solo
  routing 28861 -> 28873 bodies / 3285 cases. **U2 call-arg tail DONE
  (incr 57, Wave-1 cell B)** -- member-typed record NAMES + None
  literals into ptr-union slots (`THIRUnionArgLift`:
  `std::variant<A*, B*>{&(a)}` / `{std::monostate{}}`, with the
  indirect `&((*p))` / `&((*this))` renders), same-module record-ctor
  rvalues into `Own[union]` slots (`THIRCtorCall`, the raw-name
  `_gen_call` record-branch render; F2d rebind-slot ctor inits now
  share the node), and union-coerced int literals into value-union
  slots (gate-only -- the THIRCoerce render was already bare).
  **Readonly ptr-variant slots DONE (incr 59)**: a deep-const slot (a
  `readonly[...]` annotation or the callee's `deep_const_borrow_params`
  verdict) spells const pointees on the member/None lift and takes the
  `ptr_variant_to_const` wrap on already-union names
  (`THIRUnionArgLift.const_wrap`), with the narrowed-arg bare-alias
  skip mirrored. Member-VALUE sources into value-union slots temp via
  the arg-temp facility (incr 58, `THIRArgTemp` at the flushable
  statement positions). NB the member lift is const-blind on the AST path
  (BUGS.md shape 4 of the storage-union-at-borrow entry) -- mirrored
  byte-identically per the F4 precedent. **U3 isinstance-narrowing reads DONE (incr 51)**
  -- the stateful `_gen_if` narrowing emit over routed unions:
  `THIRIsinstance` conditions (`std::holds_alternative<M>(v)` per check
  member, OR-joined; template args carry the ptr `*` + the U2
  const-pointee chain), `THIRNarrowAlias` branch-entry extractions
  (`auto& __v = *std::get<A*>(v);` / value-union `const auto&` for
  params) with reads renamed via the lowering-scoped `lc.narrow.narrowed`
  mirror of `ctx.narrowed_vars`; the else complement alias, the flat
  elif chain + the concrete-else-fact chain break
  (`THIRIf.else_is_nested`), the exhaustiveness constant-fold
  (`if (true)`, dead implicit-else suppressed), and the early-return
  implicit else (persistent post-if alias via the `_lower_stmts`
  chokepoint; subject retyped for the rest of the scope). Eligibility
  threads a `narrowed` set: branch walks retype the subject to the
  member, rebinding writes reject, remaining-union (tuple-form) facts
  keep the variant un-extracted. Routing 28778 -> 28788 bodies.
  **U4 narrowing tail DONE (incr 53-54)** -- `while isinstance` loop-entry
  extraction (the branch-alias shape leading the `THIRWhile` body, popped
  at the brace; a body assert on the same subject stays AST -- the AST
  redeclares the alias there, the BUGS.md `_gen_while` collision),
  `assert isinstance` persistent narrowing (the new `THIRAssert` node:
  `if (!(<cond>)) ::tpy::raise_assertion_error(...)` with None/
  str-literal messages; the alias appended by `_lower_stmts` like the
  post-if alias; a re-assert on a persistently extracted subject mirrors
  the sema fold `if (!(true))` + the suffix-bumped `__v_2` re-extraction,
  gated by `lc.narrow.persistent_narrowed` / `lc.narrow.subject_union`),
  and compound `and` conditions in if/while/assert position
  (`_compound_narrow_info`: one isinstance leaf in the `&&` tree, other
  leaves eligible bool conditions; subject reads after the leaf lower to
  `THIRNarrowedRead` -- the alias-free `(*std::get<A*>(v))` /
  `std::get<T>(v)` get -- via the condition-scoped `lc.inline_narrowed`
  fact). Solo routing 28861 -> 28871 bodies / 3284 cases.
  U4-deferred: `or` trees, multi-subject compounds, compound re-asserts,
  re-dispatch on a narrowed subject (branch/loop-scoped bump), readonly
  subjects. Narrowed record call-args / method receivers landed with the
  Wave-1 call-arg cells (incr 56 -- the alias rename rides the existing
  `lc.narrow` scope; narrowed member args into union slots keep the
  AST's `already_union` bare-alias render, incr 57). Deferred beyond
  F4: `match` captures (statement shape), recursive-alias wrappers
  (reference ABI), protocol unions, async/generator union frames,
  ternary-arm normalization, tuple-unpack binds. F5 generic-slot, F-final
  (RefType removal + retirement): **not started.**
- F6 str/bytes view-split: **OPENED (S1 str values; S2 f-strings DONE, incr 39;
  S3 concat/`+=` DONE, incr 40; S4 subscript/slice/iteration DONE, incr 41;
  cross-type str-family coercions DONE, incr 42;
  S5 dict[str]/container-of-str DONE, incr 43;
  S6 bytes values DONE, incr 44;
  S4 leftovers + literal call args DONE, incr 45;
  bytes tail -- subscript/slices/iteration + concat/aug-assign DONE, incr 46;
  S4 leftovers wave 2 DONE, incr 47;
  S4 leftovers wave 3 -- container-returning call iterables + slice-ctor
  call args DONE, for/call-arg cell)**
  -- S1: str/StrView params,
  literal-init locals (PendingStrType resolved through ViewVarInfo at lowering),
  print args, comparisons, `len(s)`, owned-str returns, same-type call args,
  and owned-str FIELD reads at the return slot + f-string args
  (`ret.str_field` / `fstr.str_field` via the shared `_str_field_value_read`;
  the STORAGE member read renders bare -- StrView fields (BORROW, need the
  view->owned copy at owned sinks) and String fields are the follow-up rungs,
  as are the remaining value positions: print args, compare operands,
  call-arg slots); the
  view->owned copy is an explicit `THIRFormConvert` (str BORROW -> STORAGE,
  `std::string(x)`). **S2 f-strings DONE (increment 39, `THIRFString`)**: the
  `_gen_fstring` mirror -- all-literal `std::string("...")` (incl. the
  embedded-NUL explicit-length arm), interpolated `std::format` / NUL `vformat`,
  brace escaping, per-arg wrap templates carried on the node (str-family/
  str-literal bare, bool `bool_to_str`, double + bare-float-literal
  `float_to_str`, int8 `static_cast<int>`, IntLiteral resolved through the
  default int); the owned (STORAGE) result composes into every S1 sink (decl
  init, return, print/call arg, compare operand). S2-deferred rows (gate-
  rejected): `!r`/`!s` conversions, format specs, user/union `__str__`,
  `_container_to_str` containers, Char. The BigInt `({0}).to_string()` /
  enum `static_cast<int>({0})` / Float32
  `::tpy::float_to_str(static_cast<double>({0}))` rows LANDED with the
  value-binding frontier (increments 73-75) -- see that entry below. S4: str subscript `s[i]` -> Char (the checked
  `::tpy::__getitem__` / bounds-safe operator[] container emit verbatim),
  non-stepped slices `s[a:b]` -> `::tpy::str_slice` view results (`THIRStrSlice`,
  BORROW, view sinks only -- an owned sink arrives as a `strview_to_str`
  TpyCoerce, the deferred coercion cell), `for c in s` Char loop vars (str/
  StrView NativeIterable[Char], incl. pending-resolved str locals), Char
  params/returns/prints/call-args, char-literal compare operands
  (`THIRCharLiteral` `'x'`, the `_comparison_targets` char-arm mirror;
  Char-targeted literals at decl/reassign/return stay AST), and the reassigned
  StrView-param widening (the reject now keys on the exact AST trigger,
  `param_needs_copy_for_reassign`). S3: same-type `a + b` (the resolved
  `__add__` template; an owned STORAGE `String` result feeding the S1 sinks,
  plus String-typed locals / len / print args / the identity `string_to_str`
  coercion) and the in-place appends -- str `+=` statements and the
  `x = x + y` self-append peephole -- both lowering to `THIRStrAppend`
  (`t += v;`; NB scalar aug-assign lowers as the `THIRAssign`+`THIRBinOp`
  desugar -- if a general aug-assign node ever lands, fold this in rather
  than growing two parallel aug-assign systems); Char operands of concat,
  `str * n`, String params/returns, and
  aug-assigned str PARAMS stay AST (the last is a pre-existing AST miscompile,
  see BUGS.md). **S5 dict[str]/container-of-str DONE (increment 43)**:
  owned-`str` container elements/keys/values across the already-routed
  container shapes -- `dict[str, scalar|str]` / `list[str]` / `Array[str, N]`
  params and literal-init locals (`set[str]` literals for decl/len/iteration),
  subscript reads with str keys (`d["a"]` / `d[k]` / `d[a + b]` -- the key
  renders bare; owned-str keys never take the `view_key_target` static-storage
  literal pin, which fires only for VIEW-typed keys), `len`, method calls with
  str args into non-Own str slots (`d.pop(k)` / `d.get(k, v)`; builtin-container
  methods never take the `_wants_str_literal_pin` path) and owned-str results
  (`xs.pop()`, STORAGE), iteration with str loop vars (a fresh view var
  usage-resolved to `std::string_view` or an owned `std::string` copy, spelled
  by the shared `loop_var_binding`), and str-element container literals with
  the per-slot view->owned wrap (a BORROW str source into an owned
  `std::string` element slot reuses the S1 `THIRFormConvert` ->
  `std::string(x)`; literals and owned/String/rvalue sources land bare -- the
  `make_vector`/`make_ordered_*` move arm cannot fire, str is a value type and
  never in `movable_locals`). A str element/value subscript read carries its
  resolved shape (BORROW when its view var resolved StrView, driving the S1
  owned-sink copy; STORAGE when owned -- the `const std::string&` element
  copies implicitly at owned sinks, bare on both paths). S5-deferred rows
  (gate-rejected): StrView/BytesView-keyed or -element containers (view
  containers; their literal keys DO pin to static storage via
  `view_key_target`), bytes keys/elements (S6), and `Own[str]` method-arg
  slots (`xs.append(s)` / `st.add(s)` / `setdefault` defaults -- the
  owned-copy wrap and the copy-into-temp + `std::move(__tmp_N)` shapes).
  Container subscript WRITES landed in the wave-2 setitem cell
  (`THIRSetItem`: `d[k] = v` + aug read-modify-write on name receivers of
  the scalar/owned-str families; the widened-family gate also covers
  field-receiver targets `self.xs[i] = v` for the admitted element rows).
  F1-RECORD elements landed in the wave-4 record-writes cell
  (`setitem.record_rvalue/copy/name`: exact/covariant record rvalues --
  sema's `is_covariant_generic_upcast`, the `s._pool[key] = Box(conn)`
  shape -- `copy(name)` copy-constructs, and plain-name bare-copy /
  last-use-move values, name and field receivers both); sibling rungs in
  the same cell: `field_write.optrec` covariant-upcast admission,
  `own.record_copy` (`copy(name)` into `Own[record]` method slots),
  `method.record_discard` (discarded record results at stmt position),
  and the pointer-repr Optional[container] method-arg faces incl. the
  literal `&(__tmp_N)` hoist (`argtemp.optptr_container_literal`).
  **S6 bytes values DONE (increment 44)**: the bytes twin of S1 --
  bytes/BytesView params, literal-init locals (PendingBytesType through
  ViewVarInfo), owned-bytes returns, comparisons, `len(b)`, same-type call
  args, `BytesPrinter` print args (`PrintForm.BYTES`). Two shapes with no str
  analog: (1) a bytes LITERAL's render is target-dependent (`THIRBytesLiteral`
  carries an `owned` flag decided per sink at lowering -- owned
  `bytes_literal_owned`/empty-vector at target-less positions
  (print/compare/owned sinks), static-storage span `bytes_literal`/empty-span
  at view sinks: view decl-init/reassign and the gen_call_arg span pin into
  bytes/BytesView slots, coerce-peeled like the AST); (2) bytes `==` is a
  @native free-function dunder (no cpp_template) -> a native binop emit arm
  (`::tpy::bytes_eq(l, r)`, gen_call_from_fi's native shape) now admitted by
  the compare gate. The formerly-UNREACHABLE bytes arm of `_emit_form_convert`
  (`::tpy::bytes_copy`) is now exercised and byte-diff-validated at both owned
  sinks (decl init off a view source, owned return of a view param).
  S6-deferred rows (gate-rejected): cross-type bytes coercions
  (bytes<->BytesView, incl. any value or
  literal at a BytesView return -- always coerce-wrapped), bytes literals into
  wrapped (readonly/Own) slots (the AST renders those owned, not span),
  `bytearray` (a reference type, different axis). NB the mixed owned/view
  reassign/compare shapes (`t = b`, `t != v`, `v == b"..."`) route
  byte-identically but are a pre-existing AST miscompile (invalid C++, see
  BUGS.md) -- when fixed, the byte-diff flags the THIR arms to update in
  lockstep.
  **Bytes tail DONE (increment 46)**: the S4/S3 twins for bytes --
  subscript `b[i]` -> UInt8 (`::tpy::bytes_getitem(b, i)`, bytes'
  `__getitem__(Int32)` being a @native free-function dunder rather than the
  containers' checked `::tpy::__getitem__` template, dispatched at emit on the
  bytes receiver; the bounds-safe operator[] branch is shared verbatim;
  name receivers only, like the str twin), slices (`THIRStrSlice` reused
  as-is -- the node already carries the resolved @cpp_template, so
  `::tpy::bytes_slice` / `::tpy::bytes_stepped_slice` ride the same emit;
  non-stepped -> span VIEW/BORROW, stepped + `slice`-var index -> owned
  vector STORAGE; name/field/call receivers like str incr 45; an owned DECL
  sink carries no coerce -- the pending local's owned resolution -- and takes
  the S6 decl-init BORROW wrap `::tpy::bytes_copy(...)`, while an owned
  RETURN of a slice arrives as the still-deferred `bytesview_to_bytes` coerce
  -> AST), iteration (`for x in b:` over bytes/BytesView/pending-bytes names
  and bytes fields off F1-record receivers -- NativeIterable[UInt8], the
  for-each gate's explicit bytes exclusion lifted; the loop var is the plain
  value-scalar `uint8_t` typed copy, no new emit), and concat/aug-assign --
  `a + b` admits the template-less @native `__add__` (`::tpy::bytes_concat`,
  the emit's native binop arm the compare gate already used for `bytes_eq`;
  owned `bytes` result, STORAGE, paren-wrapped in value position; literal
  operands render OWNED -- the resolved overload's param is owned bytes;
  the `bytearray` overload's operand rejects), and `t += v` on an owned-bytes
  LOCAL lowers through the existing generic aug-assign desugar to the
  unwrapped concat-and-assign `t = ::tpy::bytes_concat(t, v);` (NO
  `THIRStrAppend` -- there is no bytes in-place append; the desugar's
  `paren_wrap=False` reproduces the statement-RHS shape). Deferred rows:
  aug-assigned bytes PARAMS (the AST skips the owned-copy prologue and
  silently rebinds the span param to the concat's dying temporary -- the
  silent-dangle bytes face added to the str aug-assign-param BUGS.md entry;
  rejected, not reproduced), non-name `b[i]` subscript receivers (the gate
  stays name-only, matching str), `len(a + b)` / `len(b[1:3])` (the len gate
  is name-arg-only for every family), bytes-keyed/element containers and the
  cross-type coercion + wrapped-literal-slot + `bytearray` rows above.
  Cross-type str-family coercions
  (str<->StrView<->String) DONE (incr 42): the sema TpyCoerce arms are
  position-disposed at lowering (`_coerce_disposition`, mirroring the
  coercions.py lambdas off the node's context / expected-type / literal
  facts) -- identity positions extend the THIRCoerce passthrough (a
  view-target coerce sets BORROW itself), materializing positions
  (`std::string(x)`: strview_to_str at INIT/ASSIGN/RETURN, str_to_string at
  non-literal ARG, strview_to_string everywhere) lower to the S1 view->owned
  THIRFormConvert (the family respelling rides result_type -- one emit
  chokepoint); slices flow into owned sinks, str literals into StrView/String
  slots, and the String rows (compare operands, f-string args, non-Own
  String call slots -- no coercion involved, the concat result renders bare)
  are widened alongside; the multi-overload str-literal pin guard now
  coerce-peels like the AST's gen_call_arg. Coercion cells still deferred:
  `Own[...]` ARG slots (the gen_call_arg cascade frontier), the
  `Optional[str/StrView]` per-element arms (statement-expression hoist),
  `char_to_str/string/strview` (own wrap renders, the Char edge).
  **S4 leftovers DONE (increment 45)**: stepped slices
  `s[a:b:c]` -> `::tpy::str_stepped_slice` over `::tpy::Slice{lo, hi, step}`
  (an OWNED `std::string` STORAGE result, bare at every sink -- `THIRStrSlice`
  grew `stepped`/`step`), slice-typed variable indices `s[sl]` (the bare name
  into the template's `{0}`; `basic_slice`/`slice` PARAMS admitted -- ctor
  LOCALS `sl = basic_slice(1, 3)` are deferred, an `Int32 | None` Optional-slot
  ctor), Char-targeted literal DECLS (`c: Char = 'x'` -> `char c = 'x';`) and
  Char-slot literal call args (`take('a')` -- call args now lower against
  their param slots; literal reassign/return into Char are sema type errors,
  so those gate rejects are defensive), and non-name slice receivers/iterables
  (str-family fields off F1-record receivers slice AND iterate; owned-str call
  results slice -- a call-result ITERABLE is an rvalue `auto` capture,
  deferred).
  **S4 leftovers wave 2 DONE (increment 47)**: slice-object ctor
  LOCALS (`sl = basic_slice(1, 3)` / `slice(a, b, c)` -- the same
  pure-@cpp_template expansion as the scalar ctors, admitted by the TpyCall
  slice-object lowering arm over the shared `_template_init_call_fi` shape
  check; a `None` bound in the value-repr `Int32 | None` slot lowers
  to a STORAGE-form None -> `std::nullopt`, int literals / fixed-int names
  render bare -- a BigInt bound never arises, sema rejects it at the ctor;
  the local declares as `resolved_type.to_cpp()` -> `::tpy::BasicSlice` /
  `::tpy::Slice`), Char record fields (`self.c: Char` -- reads/writes off
  F1-record receivers and the ctor MIL scalar arm widened to
  `_eligible_char`; the field access render is type-independent, the Char
  value lands only in positions whose own gates admit it; str-literal
  values into Char fields are defensively rejected -- sema type-errors
  them), call-result ITERABLES (`for c in full(s):` -- a str-returning
  call is a value-type rvalue per `is_lvalue_iterable`, captured owning
  `auto __obj_N = ...;`; the fact rides `THIRForEach.iterable_lvalue`,
  decided statically from the admitted iterable shapes), and non-name
  `s[i]` char-subscript receivers (the subscript gate now takes the shared
  `_str_slice_receiver_ok` set: names, str-family F1-record fields,
  eligible str-returning calls).
  **S4 leftovers wave 3 DONE (incr 55, for/call-arg cell)**: container-returning
  call iterables (`for x in make_list():` -- call lowering widened with
  `container_ret_ok` for list/dict/set returns; the capture verdict rides
  `THIRForEach.iterable_lvalue` via `_call_iterable_lvalue`, mirroring
  is_lvalue_iterable's call arm: an `Own[...]` return is a by-value rvalue
  (owning `auto __obj_N =`), a borrow / readonly borrow return a C++ lvalue
  (`auto& __obj_N =`); bytes-returning calls and subscript iterables stay
  gate-excluded), and slice-object ctor rvalues as CALL ARGS
  (`use(s, basic_slice(1, 3))` -- `_slice_ctor_pass_through_arg`: the bare
  template expansion into a by-value slice-object param slot; Own-wrapped
  and union slots stay on the AST path). Solo routing 28861 -> 28877
  bodies / 3286 cases; integrated with the sibling incr 52-54 cells on the
  same branch: **28899 bodies / 3287 cases**. Plus, still remaining: reassigned
  owned-str params (owned-copy prologue), `Literal[str]` bindings, String
  params/returns (`const std::string&` signatures), `bytearray` (a
  reference type, different axis).
- F2 carry-over deferred cells (container locals, cross-module/native/generic
  records, subscript sources, narrowing-needed `->` deref, name-alias/REF_ALIAS
  reseat sources): **blocked-on-F3+** or the relevant frontier; see the F1/F2 TODO
  cell (items C-G).

### Statement / expression shapes -- **AXIS OPENED (increment 25), STILL MOSTLY UNCOVERED**
The form ladder and callable axis are tracked; the statement-shape axis was the
untracked gap and is now being enumerated + driven.
Routed so far: var-decl / assign / return / if-elif-else / while / range-for /
aug-assign, plus the no-op trivia `pass` / docstring (M3c-trivia, `THIRNoOpStmt`),
**value-result tuple subscript reads** (`t[N]` -> `std::get<N>(t)`, `THIRSubscript`,
increment 25 -- the first deliberate cell of this axis; admits value-scalar tuple params
to carry routing), **record-element subscript reads** (`t[N].field` off a plain-record
element -> `std::get<N>(t)->field` / `.field` per the element-form arrow mirror, increment
26), and **Optional[record]-element member access** (`t[N].field` on an unproven Optional
-> `deref_check(<T*>).field`, a `deref_check` flag on `THIRFieldAccess` + the storage-source
`optional_to_ptr` lift, increment 27), and the **write position** (`t[N].field = / +=
<scalar>` off a record element -> `std::get<N>(t)->field = ...`, the position-neutral
`_field_over_subscript_ok` added to the scalar-field-write + aug-assign gates, increment 28).
**The tuple-subscript FAMILY is COMPLETE** (record-element reads + writes; value + Optional
reads). And **container subscript READS** (`c[i]` off a `list[scalar]` / `dict[fixed-int,
scalar]` param -> `::tpy::__getitem__(c, i)` / bounds-safe `c[static_cast<std::size_t>(i)]`,
increment 29) -- this is where `THIRSubscript` reaches its canonical shape (`index: THIRExpr`
+ `bounds_safe`, emit dispatched tuple-vs-container on receiver type). Subscript follow-ons
still on AST: Optional-field / tuple-field writes THROUGH a tuple subscript (`t[N].opt =
None` -- the `_f2b`/`_f1_tuple` write gates still name-only); and, for containers -- BigInt /
view-typed keys+indices (the `.to_fixed_check` narrow / static-storage literals),
record/Optional/container *element* results (borrow form),
narrowed-`Optional` receivers, non-name receivers (container-literal-init locals landed
incr 34), and the container-write residue (field-receiver targets + slices; name-receiver
`c[i] = v` / `+=` / `del` landed in the wave-2 setitem cell). And **container iteration**
(`for x in <NativeIterable>:` over a value-scalar element -> the begin/end loop, a new
`THIRForEach`; + the `len(c)` builtin via a `native_name` on `THIRCall`; increment 30) --
`range(len(c))` now routes, lighting up the bounds-safe subscript branch. The
**record-element loop** (`for x in <list[record]>:` -- the loop var a borrow alias `auto&&` /
`const auto&`, field access `.field` exactly like a record param; increment 31) completes the
for-each family; it needed only sema's `const_loop_var` threaded onto `THIRForEach` (the emit
already produced the record binding) + a `_container_record_iter` param predicate. **KEY
FINDING (value-scalar shape):** completing the loop *shape* gains little routing (5120 -> 5128
bodies) because loop *bodies* overwhelmingly use `print` / `.append` (371 of 393 for-loop
files); the routing bottleneck is the common **body constructs** (`print`, method calls like
`.append`, the `Int32(0)` constructor-init), not the statement shapes -- those are the next
high-leverage unblocks. (The record-element cell was the exception: +48 bodies, 5128 -> 5176,
because admitting the `list[record]` param unblocks whole record-iterating functions.)
**`print` landed (incr 32): +453 bodies (5176 -> 5629) -- the biggest single-cell gain**, since
print gated a huge tail of small functions (main + helpers); this confirms the body-construct
model. **Container method-calls landed (incr 33, `THIRMethodCall`): +11 bodies (5629 -> 5640)**
-- the shape is covered (all three `gen_call_from_fi` emit arms -- `@cpp_template`, `@native`
free function, plain/renamed member -- in statement and value position, on the admitted
list/dict param family), but the modest gain exposed the next co-blocker: most `.append` sites
sit on container-*literal locals* (`xs = [...]` / `xs = []`), whose var-decls don't route, so
those functions stay AST regardless. **Container-literal locals landed (incr 34,
`THIRContainerLiteral`): +38 bodies (5640 -> 5678)** -- list (vector AND the read-only
Array demotion) / dict / set literal decls route with scalar elements, and the local enters
`declared` so the param-receiver shapes (method calls / subscripts / len / iteration) light
up on locals; `_container_scalar_read` + the len gate widened to `Array[scalar, N]`.
Rejected-by-design (aliasing/emit fidelity): REASSIGNED container-literal locals (the AST
makes them pointer-locals -- `a = b` rebinds the alias; a value decl would silently copy).
(Both-literal FIXED-int-target binops -- AST emits `add_check<intN>`, no fold -- ROUTE since
wave 15 with paren_wrap=False; only the target-less/BigInt-fold literal case stays AST.)
The measured next co-blockers: **container
locals as call args** (`f(xs)` -- the borrow-ref pass looks like another gen_call_arg
pass-through for non-Own container params; keeps `main()`-shaped callers on AST), the
`Int32(0)` constructor-init, and the str/BigInt/f-string print args. Literal-locals cells
still deferred: `[0] * n` (`TpyListRepeat`), nested container literals (nested-container
COMPREHENSION elements landed via `_lower_comp_container_elem`; nested container LITERAL
decl-inits still deferred), str/record/Optional
elements, empty-`Array` literals, container reassignment + aliasing (`ys = xs`); subscript
writes on name receivers landed in the wave-2 setitem cell. **Container call args landed (incr 35,
`_container_pass_through_arg`): +11 bodies (5678 -> 5689)** -- a bare-name arg with a
builtin-container binding into a NON-Own concrete container param passes through as the bare
name in free-call + method-call validation (no new node/emit); `Own[container]`
(auto-move), `Span` (as_mut_span conversion), and protocol (`Iterable`) slots stay AST.
**F-strings landed (incr 39, `THIRFString`): +27 bodies (22165 -> 22192 solo)** --
the last measured co-blocker for `main()`-shaped functions (str VALUES landed with
the F6 S1 cell, the `Int32(0)` constructor-init with incr 37); scope + deferred
rows in the F6 row above. The modest gain reflects f-strings co-occurring with
other ineligible constructs (bare numeric-literal call args, containers) in most
corpus `main()`s. **Str concat/`+=` landed (incr 40, `THIRStrAppend`): +21 bodies
(22165 -> 22186 solo)** -- same modest-gain pattern (concat co-occurs with
still-deferred shapes); scope in the F6 row. **Str subscript/slice/iteration
landed (incr 41, `THIRStrSlice`/`THIRCharLiteral`): +331 bodies (22165 -> 22496
solo)** -- Char values enter the slice; scope in the F6 row. Increments 39-41
were parallel cells integrated sequentially; the INTEGRATED tally on the merged
tree is **22545 bodies / 3271 cases** (+380 over the 22165 base -- the cells'
solo gains plus cross-cell composition; 22642 / 3280 after the master merge).
**Bytes values landed (incr 44, `THIRBytesLiteral`/`PrintForm.BYTES`): +4493
bodies (22642 -> 27135 solo)** -- bytes shapes pervade the implicitly-compiled
stdlib bindings (socket/io/http/re), so the S1-twin cell carries outsized
routing; scope + the target-dependent literal render + the native-dunder
compare arm + deferred rows in the F6 row above.
**Logical and/or/not + inline chained
compares landed (incr 36, `THIRUnaryNot`): +2435 bodies (5689 -> 8124) -- the biggest
single-cell gain to date** (conditions gate everything); bool-result `&&`/`||` over bool
operands and the chained-compare pair fold reuse `THIRBinOp`'s bare-operator arm. The
truthiness arm-deletion rung later replaced the remaining type predictor with structured
`THIRTruthy` modes for str/bytes emptiness, value-form Optional, Any, record `__bool__`,
record `__len__`, and always-true user records. These modes are consumed uniformly by
if/while/assert, unary `not`, logical operands, ternary conditions, and comprehension
filters. Optional dotted-field truthiness deliberately remains a lowering-time fallback:
the true branch needs a dotted-path narrowing fact that THIR does not yet carry. TRAP for
whoever routes dotted-path narrowing: the field guard (`truthy.optional_field_narrow`)
keys on MODE (fires only while the occurrence type is still Optional -> IS_TRUTHY),
unlike the name guard (`truthy.optional_name_narrow`), which keys on the DECLARED type.
An already-narrowed Optional field classifies RECORD_BOOL/RECORD_LEN and slips past the
field guard; that shape is unreachable today only because field `is`-checks themselves
reject the body -- routing them arms the divergence (AST unwraps `(*box->f)`, THIR would
emit the bare optional) with no guard firing. Make the field guard declared-type-keyed
(symmetric with the name guard) before or with that widening. Still
deferred: value-semantics `and`/`or` (non-bool result -> `_gen_logical_value` temp+ternary),
the statement-expr chained arm (a
non-`_is_simple_expr` intermediate binds `_cmp` temps), bool-literal conditions
(dead-branch elimination). The incr-36 byte-diff also exposed and closed a latent compare
gap: the rb=None derived-comparison arm admitted RECORD operands (`not (self <= other)` from
`@total_ordering` -- the AST derefs `(*this)`); compare operands are now pinned to resolved
scalars (widened to the str slice when the F6 S1 cell merged: str operands emit the same
bare/templated compare on both paths). **Scalar type-constructor calls landed
(incr 37, `cpp_template` on `THIRCall`): +94 bodies (5689 -> 5783)** -- `Int32(0)` /
`UInt32(x)` / `Int64(a + b)` / `Float64(1.5)` / `bool(n)` / zero-arg ctors route in every
covered expression position; sema's fully-substituted `__init__` template expands
receiver-less at emit, gated to positional-only templates + eligible-scalar result/args.
Rejected-by-design (AST): only the str-LITERAL native-ctor arg (`float("nan")`'s constexpr
fold), enum / borrowing-view ctors, out-of-int32-range literals (the AST's `static_cast`
wrap), unary-minus FLOAT args (int negations fold since incr 45). (Runtime native-function
ctors -- `int(str)`/float/bytes/`Char(s)` -> `from_str`/`char_from_str` -- ROUTE since
wave 15 via `native_free_ctor`.)
Method-call cells still deferred: `set`/`Span`/`Array` receivers (params not
admitted), str/bytes args (owned-copy conversion), `Own[record]` args (ownership
boundary -- the `gen_call_arg` temp facility), non-name receivers
(`self.items.append(...)`), `self.helper()` call sites,
consuming receivers, generic `inferred_type_args` methods. (User-record method
receivers + record NAME args landed with incr 56 -- see the Wave-1 entry below.
Consuming receivers landed with the calls-wave2 `move_receiver` render:
`std::move(name).take()` for bare non-pointer, non-narrowed name receivers.)
**Bare numeric-literal call args + negated int literals landed (incr 45)**: a
bare FLOAT literal (FloatLiteralType) into a double param slot passes through
(`f(3, 1.5)` -- repr(v) bare on both paths; Float32 slots arrive
`float_literal_to_float32`-coerce-wrapped and stay AST, a BigInt slot's
`::tpy::BigInt(v)` wrap stays AST, inf/nan literals reject), for free calls
AND record-ctor rvalue sources (`P(1.5, ...)`; `_record_rvalue_source_shape` also
gained the missing fi/arity check -- an omitted-default ctor call no longer
routes). Negated INT literals (`-3`) fold to plain literals at lowering
(mirroring `_gen_unaryop`'s literal-negation branch), lighting up every
admitted literal position at once: call/method/ctor args, decl inits, compare
operands, subscript indices, slice bounds/steps, `range(-3, 3)` bounds (the
inline-literal decision extended to the negated arm), print/f-string args.
Deferred: negated FLOAT literals (`-1.5` takes the resolved `__neg__` template
render `-(1.5)`), `-x` over names (same template arm), negations outside the
+-int32 literal range (suffix/cast renders).
**Temp-free call-arg rows landed (incr 56-57, the Wave-1 cells; integrated
28935 -> 28999 bodies / 3295 -> 3299 cases)**. Incr 56 (cell A,
`_record_pass_through_arg` + `_record_method_call_supported`): bare-name
F1-record args into non-Own same-record ref slots (bare name for `const A&`
AND `A&`; F2 pointer-locals render `(*p)` via `THIRName.deref`; narrowed
aliases rename through `lc.narrow`), and user-record method calls on
bare-name receivers (the plain `receiver.method(args)` member arm of
`THIRMethodCall` + `is_arrow` for pointer-local receivers; single-overload
MRO-resolved methods, scalar/Char/str results or void-in-stmt-position).
Incr 57 (cell B): the union-slot rows in the U2 entry above. Gate-rejected
at the time (pinned): record RVALUES to free fns (landed since -- the
incr-58 arg-temp row) and to methods (the const-slot inline landed since,
incr 59; the MUTATED-ref-param shape is the BUGS.md method-rvalue
miscompile -- never mirror), `self` receiver/arg rows, upcast
(Child->Parent) args (landed since -- incr 59), multi-overload methods,
Own/readonly/Optional/protocol slots (readonly union slots landed since --
incr 59). NB `_member_valued_union_slot` also closed a latent slot-blind
hole in the bare-scalar arg arm (a scalar name into a value-union slot
routed and emitted bare while the AST temps -- unexercised by the corpus,
caught by cell B's probes).
**Arg-temp facility + follow-up rows landed (incr 58-59; integrated
29029 bodies / 3301 cases)**: record-ctor rvalues to FREE fns hoist
`A __tmp_N = A(7);` and member-valued scalars into value-union slots hoist
variant temps -- `THIRArgTemp` (number-free node) over the
TempSink/CtxTempSink emission seam drawing from the live module-cumulative
`ctx.temps` counter, flushed at the four simple-statement positions (incl.
ctor demotion bodies); while/elif conditions and nested calls stay
gate-rejected (originally honoring the BUGS.md while-condition
stale-snapshot entry; that miscompile is fixed -- the AST head now
re-evaluates condition temps per iteration via a while(true) restructure
-- so the while arm awaits a mirror of the corrected emission).
Upcast Child->Parent NAME args pass bare; method ctor-rvalues into CONST
same-record slots inline via `THIRCtorCall` (`const_borrow_params`-keyed);
deep-const ptr-union slots spell const pointees + the
`ptr_variant_to_const` wrap (`THIRUnionArgLift.const_wrap`). Still
deferred: the Own COPY arm (the last-use MOVE now lands on record-method
calls -- see the call-arg-completeness wave below), protocol / optional-ptr
CTOR `&(__tmp)` (the non-ctor optional-ptr faces now land on record-method
calls, same wave), covariant slots, elif-chain-abandon + statement-expr
temp relocation, the field-write flush position, readonly ref-slot ctor
temps, `self.helper()` sites, method union-slot args.
**Call-arg-completeness wave (branch `thir-call-arg-completeness`)**: three
cells bringing the fuller call-arg cascade to paths that carried a reduced
subset. (1) A movable `Own[T]` param forwarded into a user-record method
moves at last use (`recv.m(std::move(p))`) -- `_own_move_arg` on
`_record_method_call_supported` (the copy half stays AST: the method-arg
lowering does not thread `temp_args`). (2) Pointer-repr `Optional[record]`
NON-ctor arg faces (`nullptr` / `&(name)` / bare-pass / `optional_to_ptr`
lift) on record-method calls -- `_optional_ptr_arg(temps_ok=False)` (the
ctor `&(__tmp)` face stays AST, same reason). (3) UNIFICATION: the
by-value record-returning FREE-call face of `_record_rvalue_source_shape`
now shares the plain-callee argument classifiers used by free-call lowering,
so `return build(s, xs, r)` /
`x = build(...)` route the str / container / record / Own-move /
optional-ptr / union arg shapes a free call already carries, not just
scalars; the constructor face keeps its own mutation-keyed loop, a
str-literal-multi-overload pin guards the view-form pin, and a `narrowed`
set is threaded through `_owned_record_decl_ok` (gate + lowering) so a
narrowed subject stays off the `temps_ok` temp rows. Byte-diff-neutral by
construction; whole-body routing gain is marginal (these arg shapes
co-occur with other per-body blockers -- first-reject masking), the value
is the duplication removal + compositional readiness.
Deferred container-iteration cells: `dict[int, record]` key iteration (param not admitted --
its value read is a record borrow), `set`/`Span`/`Array` containers (params not yet admitted),
str/bytes-key dicts, `dict.items()`/tuple-unpack, non-name iterables (str-family FIELDS off
F1-record receivers route since incr 45 -- lvalues, the same `auto&` capture; subscript/call/
literal receivers stay deferred, a call result being an rvalue `auto` capture), generators / user iterators (the
`__iter__`/`__next__` fallback), consuming iteration (VALUE-family hoisted loop
vars + for-loop branch-decl predecls routed wave 14; non-value hoists still defer)
(`for/else` and while/else LANDED on thir-match-dynattrs-args --
`__after_else_N` labels with break reroute). (Audit-note: the gate keys
`is_native_iterable` off the use-site type -- re-check it when narrowed-`Optional` containers
land, since a narrowed value-repr Optional could then reach it.) Beyond subscript/iteration (separate frontiers): value-tuple locals, standalone
record/Optional-element binds / borrow returns, tuple-unpack. **Test-coverage follow-on:** the storage-tuple-alias receiver form (`a = h.pair;
a[N].field` read/write) is covered only by unit byte-diff, not a build+run corpus case -- add
one (the aliasing fn must stay THIR-routable, i.e. no `print()` inside it) for exec/cpy
coverage of the `.`-access + `optional_to_ptr` alias paths.
**Routine statement shapes landed (the statement-shape session, incr 66-69):**
`break`/`continue` in else-free loops (bare `break;`/`continue;` -- else-loop
breaks now route too via `goto __after_else_N`, landed on
thir-match-dynattrs-args; the gate threads an `in_loop`
flag through the body walk); `del` -- the trivially-destructible del-var face
(the AST's FIRST skip: comment only, no code) plus the owning-local
move-sink (`THIRDelVar`, landed on thir-match-dynattrs-args with the
alias bookkeeping in `_Prescan`) and
single-target `del c[k]` on bare-name list/dict bindings (the no-method-fi
`::tpy::__delitem__(c, k);` fallback; multi-target del shares one comment
across N lines, deferred); the `global` statement + scalar-global
reads/writes (`global`-declared names with eligible-scalar module types seed
the scope like params -- writes lower as bare `g = v;` reassignments, reads
render bare; the seeded set rides `_Prescan.global_seeded`; BigInt /
native-linkage / non-scalar globals stay unseeded and keep the body AST;
READ-ONLY module-global access without a `global` statement is a deferred row
-- seeding those risks local-shadowing misroutes); dict-view iteration
(`for v in d.values():` / `d.keys()` -- the view call is an rvalue iterable,
owning `auto __obj_N =` capture, `_dict_view_iterable_ok`); and tuple-unpack
loops (`for a, b in ps:` / `for k, v in d.items():` -- the parser's synthetic
`__for_tup_N` loop var binds `auto&&` via the shared loop_var_binding tuple
arm, the head TpyTupleUnpack lowers to `THIRTupleUnpack`:
`const auto& __tup_N = __for_tup_M;` + per-target `std::get<i>` decls, with
`__tup_N` reproducing the per-function `ctx.unpack_counter`; slice =
all-new plain value-scalar targets incl. `_` discards, over
`list[tuple[scalar]]`-family names (`_container_scalar_tuple_iter`, a new
param family admitted ONLY as the unpack iterable) or `d.items()`).
Unpack-deferred rows: ref/owned/const-ref elements, reused (was-declared)
targets, record/str elements, standalone `a, b = expr` statements,
generator/async frames.
**Sync `with` landed (incr 78, `THIRWith`/`THIRWithItem` -- the statement-axis
wedge): +6 bodies solo (33319 -> 33325)** -- the first exception-shaped emit
(the fixed `_emit_with_try_catch` template: suppress / exc-val / elided-catch
cleanup-only arms, multi-manager LIFO nesting with the `layer_terminates`
fold over a lowering-computed `body_terminates` via the same
`stmts_terminate` the AST reads) and the first **emit-side finally-frame
stack** (`_EmitState.finally_frames`), the two decisions the try/except tiers
reuse: `return` in a with body renders the `__tpy_ret_N` capture (the shared
per-function `iter_counter`) + the inline `__exit__` chain;
`break`/`continue` walk only frames pushed inside the innermost loop (the
mirrored `loop_depth` = `len(ctx.loop_else_labels)`). `__ctx_N` draws from
the module-cumulative `ctx.with_counter` through a ctx-backed sink
(`CtxWithCounter`, the CtxTempSink pattern). Slice: F1-record managers -- a
declared borrowed name (incl. the F2 pointer-local `*(...)` deref) or a
ctor / by-value scalar-arg call rvalue -- with fresh never-reassigned
targets (scalar VALUE `auto` / F1-record REF `auto&`) or none. The cell also
widened `_ctor_shape_ok` to no-`__init__` records (the implicit default
ctor's zero-arg `Name()` render), unlocking default-ctor calls for every
ctor face. Deferred with-rows: already-declared / reassigned targets (the
assign / optional-slot / `T*` pointer-local arms -- one family: the reuse
shape always puts an already-declared target in the function), bodies that
first-declare post-with-visible vars (`_emit_branch_decls` hoist),
str/Char/enum enter-type targets (name-form classification),
str-arg-ctor / cross-module / field-access / `self` / value-type managers,
temp-registering managers (a walrus manager is a pre-existing AST build
failure -- see BUGS.md), and the async / resumable-generator lowerings
(their own frontiers). Corpus witness: `control_flow/with_manager_shapes`
(all with faces); units `tpyc/thir/test_thir_with.py`.
**try/finally, finally_only tier landed (incr 79, `THIRTry` -- try tier
T1): +6 bodies solo (33340 -> 33346; +15 more with the witness case,
33361 / 3331 cases; the incr-78 entry's 33325 was the with branch's own
pre-squash corpus -- 33340 is the tally re-measured on the master it
landed as, 403389c2cf)** -- the unified
`try { body } catch (...) { F; throw; } F;` emit
(`_gen_try_finally_only` over `_emit_try_with_finally`), with
`_FinallyFrame` generalized to the re-emittable stmt-list arm (the with
frame's fixed `__exit__` render unchanged): the chain walker pops frames
while walking (nested exits redirect through OUTER frames) and reports
termination, adding the `[[maybe_unused]] __tpy_ret_N` capture +
suppressed trailing return/break/continue arms the with cell deferred;
counters advance per finally copy exactly like the AST's repeated
`gen_stmt` runs. Sema hoists EVERY try/finally-bound name its scan scope
didn't hold into `if_branch_decls` (incl. rebinds of names declared
outside an enclosing loop -- the loop body is its own sema scope), and
the AST emit skips already-declared names: the gate mirrors both halves
(already-declared -> skip the predecl; fresh -> `_emit_branch_decls`'
plain-value tail arm only, straight-line function scope only), and
`lower_function`'s blanket hoisted-vars reject gained the try-covered
carve-out. Corpus witness: `control_flow/try_finally_shapes`; units
`tpyc/thir/test_thir_try.py`.
**try/except throw tier + raise landed (incr 80): +493 bodies
(33361 -> 33854 / 3332 cases)** -- `_gen_try_throw`'s C++ try/catch:
one catch arm per handler (headers pre-rendered at lowering via
`error_return_to_cpp` over the sema-qualified name; the catch parameter
IS the `as` binding, entering the handler scope typed like sema binds
it), bare `except:` -> `catch (...)`, `else` jumping past via
`goto __after_else_N` with N drawn from the module-cumulative
`ctx.try_except_counter` through a ctx-backed sink (`CtxTryCounter`, the
third CtxTempSink instance; the counter's other consumers --
`__try_tmp_N`/`__er_N` unwraps and the return tier's `__except_N` -- are
all gate-rejected), and except+finally wrapping the whole try/except in
the T1 finally frame (`body_terminates` = the WHOLE statement's fact
there). `THIRRaise` mirrors `_gen_raise`'s throw-tier arms: ctor form
`throw <cpp>(args);` (args through the shared `_lower_ctor_call_args` /
`_record_ctor_arg_supported` machinery -- since wave 14 also the optional-ptr
record, mutated-ref-slot rvalue-temp, and inherited-`__init__` position-blind
forms, not just scalar/str), no-arg
`throw <cpp>{};`, bare `throw;` -- which also re-admits raise-terminated
finally bodies (the suppressed-rethrow/[[maybe_unused]] arms now fire on
raise too; a raise never walks the frame stack, the throw propagates
through the emitted catches). Deferred try-rows: the **return tier**
(PARKED on the @error_return call rung -- every such call is
gate-rejected and sema forces them into exactly these trys, so the
tier's goto dispatch `__except_N`/`__err_opt_N`/`try_except_label` lands
with that rung); non-value hoist arms (optional-storage / pointer /
rebind-slot / dynamic-base / borrow-tuple predecls); fresh hoists inside
branches (the enclosing if's own hoist pre-declares and the AST skips
the try's) or loops (the scope_tracker storage hoist layers on top);
throw-tier try-body first-declares sema does NOT hoist (the da_new rule:
no finally/else and a falling-through handler -> the decl lives inside
the C++ try scope) NOW ROUTE: try/except/else/finally bodies pass
`branch_decls_ok=True` like if/loop branches, and the branch-first decl
slot-type gate is unified with the function-scope gate (a branch-first
decl is genuinely block-local -- escaping vars are hoisted into
`declared`, never a first decl -- so it decls the same plain copy);
async/generator trys (own frontiers).
**@error_return + the return tier landed (branch thir-error-return):**
the three coordinated gates opened together -- G1 (`sig.error_return`,
sync bodies only; resumables keep rejecting), G2 (the return-tier try:
`_gen_try_return`'s goto dispatch mirrored as the THIRTry return-tier
emit arm, `__except_N`/`__after_try_N`/`__err_opt_N` off the shared
try_except_counter sink, `as` bindings reading through
`auto& name = *__err_opt_N;`, finally wrapping via the T1 frame), G3
(the return-tier raise: `THIRRaise.return_tier` -> the finally-aware
`return ::tpy::make_unexpected(<cpp>(args));`). The caller surface:
`THIRErrorReturnBind`/`THIRErrorReturnDiscard` mirror the statement-level
`__try_tmp_N` blocks (predecl spelled from the callee's SUCCESS type),
`THIRErrorReturnUnwrap` the expression-level `__er_N` statement
expression (value and pointer forms -- the pointer form has no corpus
witness; it is pinned byte-identically by a unit against the AST oracle,
the borrow-container fallible-callee iterable shape); all four
dispositions
(goto-except with/without capture, finally-aware propagate, panic) read
the emit state exactly like the AST ctx (`error_return_cpp`,
`try_except_label`/`try_except_err_opt`, `in_except_tier`). Bare
`return` renders `return {};`, void bodies append the trailing success
return, `return <er call>` inside an @error_return body passes the
expected through raw. Deferred rows (each a named
`error_return.*` detail): aliasing borrow-result binds
(`_error_return_result_aliases` -> pointer-local target), method-call /
coerce-wrapped callees at statement positions, owned-str/bytes
first-decl binds (non-hoisted), field/subscript assign targets,
Optional/ptr-variant/property pass-through return slots. Corpus:
the whole `error_return/` group byte-identical, 14/29 compiling cases
fully routed (incl. both finally-composition cases); units
`tpyc/thir/test_thir_error_return.py`. Corpus witnesses:
`control_flow/try_finally_shapes` + `control_flow/try_except_shapes`
(all 15 try/raise faces); units `tpyc/thir/test_thir_try.py`. The
raising-finally-after-return shape (finally ran twice, second exception
won) is now FIXED on both paths via the per-`try` `bool __fin_ran_N`
exit-site guard (see EXCEPTION_DESIGN.md "finally Codegen"); THIR mirrors
the guarded shape byte-for-byte.
**match frontier opened -- M1 scalar switch tiers landed (incr 81): +18
bodies (33854 -> 33872 / 3333 cases)** -- `THIRMatch`/`THIRMatchArm`
over enum + fixed-int subjects: pre-rendered case labels
(`_enum_member_cpp` / `_switch_literal_label`), or-patterns as stacked
labels, wildcard regrouped last as `default:`, the branch-decl hoist
(try discipline verbatim), synthetic `default: break;`, the
`::std::unreachable()` tail, and the break-escaping-a-switch
`goto __loop_break_N` emit interaction (`_EmitState.switch_depth` +
per-loop label slots on all three loop emitters). The match counter is
per-FUNCTION (`reset_scope`), so emit numbers `__match_subject_N` off
`_EmitState` (iter_counter precedent), not a ctx sink. Frontier
inventory (instrumented, 263 codegen-reaching stmts across 220 cases):
switch_union 97 / if_elif_record 30 / if_elif 25 / switch_enum 18 /
switch_primitive 17 / guarded_union 17 / optional 25 / polymorphic 14 /
switch_str 8 / guarded_record 6 / if_elif_guarded 5 / overload 1.
**M2 if/elif tier landed (incr 82): +5 bodies (33872 -> 33877 / 3333
cases)** -- the unguarded `==` chain over bool/BigInt/float/str
subjects below the str switch-dispatch threshold (`_should_switch_str`
mirrored); source-order arms, wildcard-last gate-enforced, labels carry
`_gen_literal_cond`'s RHS. Faces `match.if_elif` + `match.if_elif_else`.
**M3a+M3b captures + chain guards landed (incr 83): +6 bodies (33877 ->
33883 / 3333 cases)** -- `THIRMatchBinding` (assign/copy/ref modes off
`_emit_binding`'s value arms + `bind_by_value`), capture/`as` on all
routed tiers, and the `if_elif_guarded` strategy (standalone-if + goto
`__match_end_N`, the second per-function counter draw; guards gate to
bool-typed call-free exprs -- rendered raw, no truthy wrap, no flush
point in the arm block). Faces `match.bind_{copy,ref,assign}` +
`match.if_elif_guarded` + `match.guard_arm`.
**M3c in-switch guard chains landed (incr 84, M3 COMPLETE): +7 bodies
(33883 -> 33890 / 3333 cases)** -- the THIRMatchArm group/entries
restructure (`_SwitchEntry` mirror), same-label merging by rendered-
label key, cross-entry binding dedup ahead of the guard chain,
`__match_default_N` goto fallbacks (`default_goto` folded at lowering).
Faces `match.switch_guard_chain` + `match.default_goto`. A live M3b
lowering bug (unconditional if_elif_guarded promotion) was caught by
the whole-corpus byte-diff mid-cell.
**M4a union-subject switch landed (incr 85): +13 bodies (33890 -> 33903
/ 3334 cases)** -- plain (non-wrapper) unions: source-order arms,
numeric index labels, `__case_{i}` aliases through `lc.narrow` (subject
retyped to sema's arm fact), `as` via `from_case_var`, or-pattern
stacked indices, `case None:` monostate, in-place `default:`;
`is_ptr_variant` folded type-level. Faces `match.switch_union` /
`union_alias` / `union_none_arm` / `union_default`; new corpus witness
`match/union_none_member`. **M4c (recursive-alias wrapper subjects,
the `.value` indirection) is CROSS-AXIS-BLOCKED, parked against the
wrapper-form rung:** wrapper-typed params/locals are themselves
function-gated, so no wrapper match can route until that form lands --
verified by building the widened arm and watching a bare-arm `Tree`
match reject at the FUNCTION gate (the match arm never fired); the
widening was reverted rather than shipping a dead emit arm + a
permanently zero-witness face. `match/union_recursive_bare` (new case)
is the ready witness for when the rung lands.
**M4b guarded union landed (incr 86, the approved M1-M4 ladder
COMPLETE): +7 bodies (33903 -> 33910 / 3335 cases)** -- per-variant-
index guard groups (`__case_{idx}` -- VARIANT-index naming), wildcard
broadcast + truncation + default coalescing, `__match_end_N` (2nd
counter draw, before the switch head; the trailing label emits
UNINDENTED -- a mirrored AST quirk), guards = M3b rule + subject-name
reject (the AST renders guards before narrowing applies). Face
`match.guarded_union`. Most corpus guarded-union stmts use FIELD
conditions (gated with keywords), so the +7 is the guard-only subset.
Match tiers still parked (the tail):
`Literal[...]` subjects need the literal-fact fold in THIR expression
lowering -- the same row that keeps them out of M1's switch (they are
the corpus `mode: Literal[...]` dispatchers); value patterns
(named-constant compares) still reject. PARKED (record patterns and
optional partitions LANDED as incr 92/93; value-repr optional subjects
LANDED as the O2 multi-arm dispatch on thir-nested-defs -- the
has_value split + inner switch/chain over `__match_inner_N`, scalar
and str inners; the non-prefix if/elif-optional chain tiers, the
str-switch discriminator tier, `Optional[enum]` names, and the O1
record-inner dispatch LANDED on thir-match-dynattrs-args): the
optional tiers' remainder (field-access subjects; guarded record-inner
chains), overload-specialized (1),
resumable (generator/async) matches (own frontier), M4c wrapper
subjects (cross-axis, see above).
**P1 polymorphic/@dynamic dispatch LANDED (thir-match-drill):** the
poly_if_elif chain (`_gen_match_polymorphic_if_elif` mirror: C++17
if-init `Sub* __mpoly_i = <cast>` + `Sub& __case_i` alias, subject
reads renamed via `lc.narrow` like the U3 mechanic, or-arms as
`||`-joined null tests, wildcard/capture else) and the poly_guarded
tier (`_gen_match_polymorphic_guarded` mirror: standalone-if arms,
field conds around the ALIAS, guards lowered inside the narrowed
window, `goto __match_end_N` + INDENTED end label). Casts compose
through the shared `narrow_cast_rhs` chokepoint as (prefix, suffix)
pairs around the emit-numbered subject (deref-view depth for Box/Rc
wrapper subjects included); guarded-vs-chain dispatch shares the AST's
`pattern_has_field_condition` (moved module-level). Subject const-ness
mirrors `_is_const_borrow_source` via `_poly_subject_const`
(predicates.py) -- and the dyn-isinstance if arm was ALIGNED to the
same predicate (it read `_narrow_subject_const`, a latent near-miss vs
the AST's fact source). Excluded rungs: pointer-repr sources
(`match.poly_ptr_source`), resumable-leaf bodies
(`match.poly_resumable`), literal facts / foreign fact keys
(`match.poly_facts`), or-arm `as` bindings (`match.poly_or_bind`),
positional patterns. Faces `match.poly_if_elif` /
`match.poly_guarded` / `match.poly_or_arm`; units
`test_thir_poly_match.py`.
**Storage-form subjects LANDED (thir-match-drill, same wave):** the
union switch/guarded tiers, the record tiers, and the pointer-repr O1
partition admit field/subscript LVALUE subjects (`h.pet`, `xs[0]`,
`self.f` -- `_match_expr_subject_ok`: the shared
`_match_subject_is_lvalue` fact + a direct declared/`self` root).
Union expr subjects fold VALUE-variant (`std::get` without `*` --
`is_ptr_variant_source`'s field/subscript arms; names keep the
type-level fold); the O1 field source lifts via `optional_to_ptr`
(`THIRFormConvert` BORROW) off `_lower_field_source` and binds by
value; union/record expr subjects lower field_prechecked /
subscript_prechecked (the route vetted the shape; the sink ladders
gate sinks, not the subject bind). Expression subjects carry no sema
facts (verified) -- keyed facts on one gate-reject. The
`forbidden_writes` gate narrowed to NAME-level writes (rebind / del /
unpack): through-writes (`bb.val = 9`) alias exactly like the AST's
binding emit and now mirror -- including BUGS.md's const-subject
capture-mutation face (toolchain-caught, non-corpus), pinned lockstep
by `test_capture_through_write_mirrors`. Scalar tiers and the optional
chains stay name-only. Face `match.optional_subject_lift`; units
`TestMatchStorageFormSubjects` / `TestMatchStorageFormSubjectRungs`.
Wave witnesses (thir-match-drill flips): the match/poly_* seven,
`match/optional_field_subject`, `match/warn_subject_mutation_prefix`,
`exceptions/raise_ctor_union`. (The older M1 witness list below names
the scalar-tier cases only.) Corpus
witnesses: the match/ group (`literal_int`, `enum_value`,
`break_in_match_arm`, `two_matches_same_scope`, `guard_basic`, and the
new `arm_declared_local` / `union_none_member` / `union_recursive_bare`);
units `tpyc/thir/test_thir_match.py`.
**INTEGRATED tally for the thir-compr-rock branch (increments 90-93 +
the fallback tooling): 34383 -> 35874 bodies / 3344 cases (+1491)** --
trivia +1447, comprehensions +7, record patterns +25, optional
partitions +11 (incl. its witness case), +1 composition; combined
whole-corpus byte-diff green (6806 passed).
**Optional-partition tier landed (increment 93, the
thir-match-optpart branch): +11 bodies (34383 -> 34394 / 3344 cases,
incl. the new witness case)** -- pointer-repr Optional[F1-record] name
subjects whose arms partition (a None prefix + narrowed non-None arm;
`_gen_match_optimized_optional`, the `__match_inner_N` second name off
the SAME counter draw as the subject). The partition routing fact
is literally shared: `_partition_optional_cases` moved to module level
(`partition_optional_cases`) and both paths call it (discipline #6,
by identity rather than mirror). In: bare class arms (the no-field
`{ }` block), wildcard, capture/`as` vs the deref alias
(`auto& v = __match_inner_N;`, ref/copy modes), 0-1 unguarded
binding-free None arms, the no-None-arm `if (subj != nullptr)` form,
param + OPTIONAL_TO_PTR local subjects, nested matches (the inner name
snapshots its draw before the None body emits). Out: value-repr
subjects (their std::optional binding is function-gated -- ALL 25
parked stmts turned out to be value-repr, keyword-pattern, or
field-subject shapes, so the corpus witness `match/optional_partition_record`
is new), guards, or-patterns, keyword patterns, and written-through
captures -- the AST renders a capture-alias mutation against the const
deref ref, ill-formed C++ (BUGS.md: sema's const verdict never sees
capture-alias mutations; direct subject writes in the arm attribute
fine and stay routed). Faces `match.optional_partition` /
`optional_none_arm` / `optional_value_only` / `optional_inner_bind`.
**with/ctor multiplier tail landed (increments 87-89, the
thir-with-tail branch): +9 bodies (33910 -> 33919 / 3335 cases; +5
ctor-str, +4 with-targets)** -- the three queued short-session cells. (1) Str-arg ctor slots:
`_record_ctor_arg_supported` admits str-family slots via the
free-call pass-through rule (`_str_pass_through_arg`), unlocking
`Logger("A")`-style ctors at every consumer face at once (with
managers, record-rvalue arg temps, Own[union]/method/readonly ctor
args); a MUTATED `String` slot rejects -- the AST body emit for such a
ctor is itself ill-formed C++ (BUGS.md: mutation rendered against the
untouched `const std::string&` param). Face `ctor.str_arg`
(corpus-witnessed). Also fixed the dead name-vs-index mutated check in
`_raise_eligible`. (2) str/Char/enum with-enter targets:
`_with_target_arm`'s VALUE arm widened; the declared entry carries the
RESOLVED enter type so body reads classify like the AST's
`var_types[name] = enter_type` (owned str -> STORAGE, StrView -> BORROW
via `_str_name_form`). Face `with.str_target`. (3) The already-declared
with-target family: `WithTargetArm.PTR_DECL`
(`T* g = &(__enter__());`, the name joins the F2 pointer set) +
`ASSIGN_PTR` (a later `with` over the same name: `g = &(__enter__());`),
gated to plain pointer-locals over the SAME F1 record --
`_with_target_arm` stays the ONE shared gate/lowering routing fact
(discipline #6), now fed each side's pointer/rebind state. NB the sema
scan counts the with-rebind itself in `rvalue_reassigned`, so that set
cannot split pure two-with reuse from mixed rvalue reassigns; the mixed
family rejects at the REASSIGN site instead (probed: with-then-rvalue,
decl-then-with, reuse-in-branch all stay AST). The value /
value-repr-Optional target reuse is NOT a deferred cell but a BUGS.md
gate-reject (the AST emits `x = &(__enter__())` into a value slot --
ill-formed). Faces `with.ptr_target` / `with.ptr_target_reuse`.
**match record patterns + union field conditions/bindings landed
(increment 92, the thir-match-record branch): +25 bodies solo on the
34383 base (34408 / 3343 cases pre-integration)** -- the parked record-pattern tier
(if_elif_record 30 + guarded_record 6 stmts) plus the union tiers'
keyword remainder: literal field conditions pre-rendered as
(prefix, suffix) pairs around the emit-time base (`__match_subject_N` /
`__case_{idx}`; `== v` compares + the `field=None` repr fold --
has_value / monostate), field captures via
`THIRMatchBinding.subject_suffix` (field type joins the arm scope;
value scalars/Char/enums/resolved-str only), or-pattern record arms as
per-alternative condition groups (`or_conds`; the AST's empty-alt skip
and wildcard-alt clear mirrored). `_match_union_route` reuses
`MatchGenerator._pattern_has_field_condition` (one shared routing
fact); guarded-union truncation treats field conditions like guards,
needs_extraction counts keywords, field-cond bindings emit INSIDE the
composed `if (conds && guard)` block. Gate-rejected ill-formed AST
renders (BUGS.md): record-subject or-pattern bindings (as-over-or +
or-alt captures, silently dropped) and a guarded-union field-cond
entry whose guard reads its own capture. Still deferred: class/`as`
field sub-patterns (union field guards / nested records / type
guards), non-F1 records, hoisted non-plain-value captures. (Union
or-alt keywords LANDED on thir-nested-defs: the switch tier's
`__case_{i}_{j}` per-alternative duplication and the guarded tier's
keyword-bearing alternative distribution.) Faces
`match.if_elif_record` / `guarded_record` / `record_or` / `field_cond`
/ `field_none` / `field_bind` / `union_field_cond`, all
corpus-witnessed (record_basic, record_guard, field_none,
union_field_binding, union_field_value*, union_positional*); units in
`test_thir_match.py`.
Remaining with-rows: bodies that first-declare post-with-visible vars
(`_emit_branch_decls` hoist), cross-module / field-access / `self` /
value-type managers, temp-registering managers (the BUGS.md walrus
entry), async / resumable lowerings (own frontiers). Units
`tpyc/thir/test_thir_with.py` (TestValueEnterTargets, TestPtrTargetReuse,
TestStrArgManager) + `test_thir_callargs.py` (TestCtorStrArgSlots).
**Body-wide trivia landed (increment 90): docstring / `pass` in ANY
routed body position** -- the M3c-trivia arms moved from the ctor-only
`_lower_ctor_body_stmt` seam into `_stmt_eligible` / `_lower_stmt`
(the seam and its `stmt_fn` hook are deleted; ctor bodies route trivia
through the shared path). `pass` keeps its `loc` (the `// pass` source
line), a docstring lowers loc-less (the AST emits neither comment nor
code for a bare string-literal statement at ANY position --
`is_docstring` is exactly gen_stmt's skip predicate). Found by the
fallback tally: `stmt.pass_stmt` first-blocked 850 body-compilations
corpus-wide. Face `stmt.trivia`; units
`test_thir_core.py::TestTriviaBodies`. Routing 34383 -> **35830 bodies /
3343 cases** (+1447 -- the largest single-cell gain since incr 44:
pass/docstring-ONLY bodies plus every body where trivia was the last
blocker).
**Comprehension frontier opened -- C1+C2 tiers landed (increment 91,
`THIRComprehension`)**: list/set/dict comprehensions at a fresh local's
decl-init, the `_gen_comprehension_iife` stmt-expr mirror. Slice
(user-approved): range1/range2 counter loops (per-bound counter draws --
the comprehension emitter's scheme, unlike the statement range-for's
single draw; literal bounds inline strictly on `TpyIntLiteral`),
bare-name container iterables the container gates admit,
`d.values()`/`d.keys()` views (`d.items()` for the tuple-unpack form,
the incr-69 `__tup_N` lines inlined), filters (`&&`-joined
`_condition_eligible` conditions), scalar/str/F1-record loop vars,
scalar/str element slots via the S5 `_lower_container_elem` wrap, and
the sized-list reserve arms (incl. BigInt `to_size_checked`). ONE
shared `_comp_route` computes the gate/lowering fact (discipline #6).
Two structural notes: (1) the node is THIR's first multi-line
EXPRESSION render -- inner lines indent off the new
`_EmitState.stmt_indent_level`, stamped per statement; (2) elements
lower through ordinary `_lower_expr`, while filters use
`_condition_eligible` (no `temp_args` opt-in), so no arg-temp can
arise inside the loop and the scoped `_emit_iter_temps` flush seam
stays un-mirrored until the C3/C4 rows. Deferred (the approved
follow-up): **C3** -- Array demotion (`array_from_index` lambda;
NB literal-bound small range comps resolve to Arrays, so they reject
today), non-decl positions (print/call arg, return -- 32 corpus
list-comps sit in `print(...)`), field/subscript/call iterables,
3-arg range, narrowed-Optional iterables, Char element
slots (owned-move elements -- `__dk_N` key sequencing +
`_comp_owned_move_scope` -- ROUTED, incr 95); **C4** -- genexpr (the make_generator lambda family:
range-counter captures, lvalue IIFE, moved-source; builtin-arg
positions dominate the corpus). Faces
`comp.{list,set,dict,range,begin_end,reserve,filter,unpack}`
(`comp.unpack` is corpus-zero-witness -- unit-pinned only, the
flush.assign precedent); units `test_thir_comprehensions.py`.
Routing 35830 -> **35837 bodies / 3343 cases** (+7 solo -- the
tally's ~47 first-blocked hosts mostly carry other blockers too).
**Bool-field truthiness conditions landed (incr 94, the thir-stmt-tail
branch): +1268 bodies (35874 -> 37142)** -- `_condition_eligible`'s
`TpyFieldAccess` arm admits BOOL-typed field reads the value gate
already admits (a bool value's truthiness render IS its value render;
`not <bool field>` was already in via the `_unary_not_eligible` bool
arm -- a pre-existing positive/negative asymmetry, now closed). Bool
only, mirroring the name arm's scope pin -- non-bool scalar fields
(int truthiness) stay AST. Top ROUTINE cell of the stmt.*
sub-classifier's first read; the cascade (condition-blocked bodies
route whole) mirrors incr 36's conditions-gate-everything finding.
Face `cond.bool_field`; units `test_thir_core.py::TestBoolFieldCondition`.
**The free-callee symbol family landed (incr 95+96, thir-stmt-tail):
+7132 bodies (37142 -> 44274, the biggest single-cell gain to date)**
-- `_plain_free_callee_ok` generalizes to the shared `_free_callee_kind`
routing fact: cross-module TPy callees pre-render `THIRCall.callee_cpp`
via the EXTRACTED `imported_free_callee_cpp` (the AST's `_gen_call`
elif now calls the same helper), C++ `@native` frees ride the len
hardcode's `native_name` arm, positional-only `@cpp_template` frees the
scalar-ctor `cpp_template` arm. This closes the ledger's
"DEFAULT-linkage call-symbol" completeness-blocker row for the C++
native + template + imported-TPy subset; still parked: extern-C /
@native_c raw symbols, `native_cpp_return_type` static_cast wraps,
bespoke-arm builtins (copy/copy_iter/own_iter/try_parse/print; ord-fold routed),
non-positional templates, and the pre-arm/kwarg-dependent arg rows for
native callees (`call.native_arg_shape`, ~3k weighted). Faces
`call.imported` / `call.native_free` / `call.template_free`.
**Comprehension C3 routine rows landed (increment 97, the thir-comp-c3
branch -- the session's parallel cell)**: 3-arg range (begin/end over the Range object -- the
substituted `::tpy::Range<T>(...)` template call, rvalue capture),
field-access iterables (`recv.items` off `_field_receiver_ok`
receivers, typed on the DECLARED field type so narrowed
Optional/union fields reject; `_lower_field_source` reuse), Char
element slots, the print-arg position (PrintForm LIST/SET/DICT ->
the container-printer wraps; `_comp_expr_ok` extracted
position-independent, layered at the statement gate over
`_print_arg_ok`), and the Array-demotion RANGE arm
(`::tpy::array_from_index<E, N>` per-index lambda, bounds
untargeted, stop encoded in N). Still open: the Array-SOURCE
indexing arm (now test_fallback's canonical out-of-slice shape),
call-arg position (container param-slot pins), RETURN position
(blocked on the signature axis: container returns reject at
`_eligible_return`; widening it is owned-name return moves, a
signature row, not a comprehension row),
narrowed-Optional iterables, genexpr (C4) (owned-move elements ROUTED, incr 95). Faces
`comp.{range3,field_iter,print_arg,array_range}` (`array_range`
corpus-witnessed; the others unit-pinned). Routing 35874 -> 35884
bodies / 3344 cases (+10 solo, pre-integration base; the integrated
thir-stmt-tail tally was in the since-dropped per-increment history).

**THIR param/return/compositional waves landed (branch thir-param-grid,
8 waves)**: value-repr `Optional[scalar]`/`Optional[str]`/union params +
returns, dict/set membership + set/container params, str/Char value-unions,
tuple returns + tuple-unpack, `list.append` str/record elems, method
receivers (incl field-access chains `self.buf.append` / `self.field.m()`),
range arith + stepped literal/variable bounds, str/bytes ctor decls +
bitwise binops, generic-record arg-ful instantiation into `Own[T]` slots,
in-branch first-decls (Slice 1 in-place value decls, Slice 2 if/elif/else
value hoists reusing the try/match hoist scaffold), `Optional[record]`
field writes, and un-narrowed `print_optional_val`. **Container PARAM
admission is now COMPOSITIONAL**: one `_container_param_renders` predicate
("`list`/`dict`/`set`/`Array`/`Span` of any fully-concrete element, each
element USE gated by the body walk") replaces the enumerated
element-family whitelist -- `_container_record_iter` / `_set_scalar_read`
deleted. The read / for-each / comprehension-loop-var / subscript-value-leaf
sides likewise collapsed to compositional predicates
(`_for_each_elem_binding_ok`); FORM-sensitive sides (call-args,
field-writes, container-literal element STORAGE, and container
MUTATION/setitem, still enumerated on `_container_scalar_read`) do NOT
collapse until the form fact is materialized on the node.
Shapes ~21% -> ~24% (1,087 -> ~1,239 of 5,179); bodies 43.6% -> ~47.4%.

**Value-repr `Optional[str]` RETURN + `Optional[bytes]` param/return landed**
(the owned-view twin of the value-repr `Optional[scalar]` return; the str
param cell landed in the 8-wave batch but the RETURN sink stayed AST, and
bytes was absent entirely). Generalized to the str-OR-bytes VIEW FAMILY via a
shared `_value_opt_view` (`_value_opt_str` + the new `_value_opt_bytes`) at the
family-neutral sites (param + return gates, name read, None-test, truthiness,
`!`/is-None operands, arg-split shim, name.optval rejects, return arms); the
view-family emit already spells the owned copy per family (`std::string` vs
`::tpy::bytes_copy`). `-> str|None` -> `std::optional<std::string>`,
`-> bytes|None` -> `std::optional<std::vector<uint8_t>>`; `bytes|None` param ->
`std::optional<std::span<const uint8_t>>`. Return sources: `return None` ->
`std::nullopt`, `return "lit"`/`b"lit"` -> bare owned literal, and
`return <Optional[view] param>` -> the arg-split shim
(`x ? std::make_optional(<conv>(*x)) : std::nullopt`, `THIROptViewArg`). A
view-form value SOURCE (`x = "y"; return x`) is GATE-REJECTED (a pre-existing
AST miscompile -- a view does not convert to the owned optional, BUGS.md); owned
results (f-string/concat) ride a later widening. Three str-SPECIFIC sinks stay
`_value_opt_str`-keyed (if-expr str-result, `print_optional_val`, value-tuple
return element). Bytes-optional `print` stays AST; bytes-optional truthiness
stays ROUTED, mirroring the pre-existing AST miscompile byte-identically (no
`is_truthy(span)` overload, BUGS.md -- the shape can never reach a green exec
case, so the AST-miscompile mirror criterion keeps it routed). Shapes
1,241 -> 1,249 of 5,186 (24.1%).

Uncovered shapes remaining: the parked match tail (above), the try
return tier + expression raise (rows above),
generator iterables, expression statements' deferred receiver/arg cells
(listed above), `async`/`await`, `yield` / generators, comprehensions,
`nonlocal`, plain (non-isinstance) `assert` messages, chained
comparisons with non-simple intermediates (the statement-expr arm) and
value-semantics `and`/`or` (the bool-result and structured-truthiness slices landed;
Optional dotted-field narrowing remains a deliberate fallback). **Action:** continue
enumerating + driving these as a tracked axis
before claiming `gen_body`/`gen_expr` deletion is near.

## Sequencing discipline (the plan)

1. **Drive the form ladder (F3 -> F-final) as the spine.** Highest leverage: each
   rung unblocks form cells in *every* callable frontier at once, and F-final is the
   gate on retiring the form machinery.
2. **Finish a frontier's self-contained tail before moving on.** Opening a frontier
   commits us to closing its self-contained cells (do not leave them scattered).
3. **Park cross-axis-blocked cells against their rung** -- tracked here, closed when
   the shared rung lands.
4. **Hold per-component deletion as the real done-signal** -- late and
   form-ladder-gated, but the explicit endpoint, so high routing is never mistaken
   for completion.
5. **Pre-existing AST-path bugs, two policies by output well-formedness.**
   A shape whose AST render is well-formed C++ but semantically wrong (e.g.
   the BigInt-keyed dict int32 round-trip) is MIRRORED byte-identically and
   filed in BUGS.md ("mirrored, not endorsed") -- when the AST fix lands,
   both paths change together under the byte-diff. A shape whose AST render
   is ILL-FORMED C++ or where a byte-identical mirror is impossible (the
   literal BigInt slice bounds, the truthiness CALL-operand drop) is
   GATE-REJECTED with the re-admission trigger noted at the gate and in
   BUGS.md -- never mirrored (reproducing a build failure or a lost side
   effect), never silently fixed (breaking byte-parity). The enum `.name`
   owned-sink shape ran the full course: gate-rejected at incr 77,
   sema-fixed on `fix-enum-name-strview`, then re-admitted with the
   byte-diff validating both paths' new copy in lockstep.
6. **When the gate and lowering must agree on a routing fact, ONE shared
   function computes it** -- the byte-diff is the backstop, not the
   mechanism: it only catches a drift where the corpus happens to witness
   the disagreement region (the match frontier's M3b promotion bug was
   caught by exactly one case, `guard_wildcard`). `_match_strategy` /
   `_match_union_route` are the pattern; a condition hand-copied into both
   sides is a latent drift.

## Per-cell test convention (2026-07-02)

THIR cells default to **scaffold units only** (routes/ineligible pairs for the
gate + a targeted `test_byte_identical` per new emit arm -- the byte-diff in
miniature). A dedicated corpus case is added ONLY when the shape occurs nowhere
in the corpus (the whole-corpus byte-diff would be vacuous for it) AND a unit
cannot express it (multi-module, exec-observable). Rationale: a THIR cell adds
no language behavior; corpus-case exec runs the AST-path binary (byte-identity
transfers its coverage to THIR), so a new case's functional/exec phases only
re-test constructs the reviewed corpus already covers. Under-routing stays
guarded by the routes-units (invisible to the byte-diff); over-routing by the
byte-diff itself.

## Maintaining this ledger

- Flip cells / update statuses when a rung lands or a deferral is discovered.
- **Counter-order dual-path risk:** THIR emit reproduces `SlotState` /
  loop-index / temp-counter allocation order in a second code path with only
  the corpus byte-diff guarding drift -- a structural-unification candidate
  (single allocator consulted by both paths) once THIR becomes the sole
  codegen path.
- **Landed: per-face witness tally** (`tpyc/thir/faces.py`): the `--thir-codegen`
  summary now prints `tpy| thir faces: N/M witnessed; zero-witness: <names>` --
  which registered gate/lowering faces (currently the call-arg machinery's) had
  zero corpus witnesses. A zero-witness face is a byte-diff blind spot; give it a
  unit or a corpus witness before deleting its component's AST path. Register new
  faces in `THIR_FACES` when adding gate arms / lowering renders. All ten
  originally-zero-witness faces are UNIT-pinned (increment 72: each unit asserts
  the face fired via `testutil._lower_ctx_witnessed`); `flush.assign` stays on
  the corpus zero-witness list permanently -- the parser emits `TpyVarDecl` for
  every name-target assign, so only macro-built / frontend-IR ASTs reach it.
- **Landed: print wrap-arg cell** (`print.wrap_arg`): container / value-tuple /
  F1-record NAME print args route inside their kind-keyed printer wraps
  (`_wrap_print_form` in checks.py, the routing fact shared by validation and
  `_lower_print_arg`; Dict/Set/ListPrinter with Array on ListPrinter, the new
  `PrintForm.TUPLE` -> `::tpy::TuplePrinter`, records raw via their emitted
  operator<<). Excluded (deferred rungs): pointer-local names (AST derefs),
  `self` (`(*this)`), bytearray / Span / dict-view / varargs printers,
  call/subscript/field print sources. Landing it unmasked + fixed a real THIR
  emit divergence: SINGLE-element tuple literals parenthesize
  (`std::tuple<T>(x)`, the GCC C++23 brace-init ambiguity) -- the
  THIRTupleLiteral emit now mirrors `_gen_tuple_literal`'s tail.
- **Landed: print/compare-sink wave (`thir-wave-next`, 2026-07-22, +43 flips,
  dial 2065 -> 2108).** Eight construct families: user-record `__contains__`
  membership (`binop.user_membership` -- name/field receivers, set-LITERAL
  rvalue receivers, F1-record needles); str-call membership receivers
  (`"x" in str(e)` through the `.find()` arm); the target-less both-literal
  BigInt fold (`binop.literal_fold`, `_ExprUse.literal_fold_ok` -- print args
  + compare operands only; slot-threaded positions render the full operator
  expr on the AST path and keep rejecting, unit-pinned); str LITERALs into
  value-repr `Optional[str]` method slots; structural-protocol slots taking
  F1-record FIELD reads (`len(r.cookies)`; @dynamic pinned AST); user-record
  setitem str-LITERAL values; F1-record dict/set KEYS (the shared
  `_dict_key_shape_ok` slice; `_any_value_dict` deliberately narrower);
  for-head user-iterator FIELD reads + STRUCTURAL protocol params (the
  NativeIterable/Spannable/Own-elem/resumable exclusions mirror
  `_gen_for_each`'s peephole split -- the corpus byte-diff caught 4
  divergences mid-wave before the exclusions landed, all four now
  unit-pinned); bytes method-chain receivers (`recv(32).decode()`);
  hasattr/getattr-default probe stmt-exprs (`call.dyn_hasattr` /
  `call.dyn_getattr_default`, the shared `_lower_dyn_synth_call` gate);
  Own[container] param consumes via the Own-slot move/copy arms'
  `allow_unrouted_name` opt-in (general reads still reject, unit-pinned).
  Parked residuals recorded in TODO.md's calls-wave frontier entry
  (datetime kitchen-sink tails, tuple_ref_member_compare, math_numeric,
  the extern-C `sig.linkage` param-ABI design cell, enumerate/zip heads).
- **Landed: tuple-unpack method-call sources** (`tuple_unpack.src_method_call`):
  `a, b = obj.pair()` routes via a `storage_ret_ok` escape threaded through
  `_container_method_call_supported` / `_record_method_call_supported` result
  checks (mirroring free-call lowering's; position-pinned to the unpack
  source) + the
  method arm in `_tuple_unpack_source`. Residual rocks in that row:
  `src_call` (free calls failing their own gates) and `source_family`
  (non-scalar elements, str first).
- **Landed: distinct-SHAPE tally** (`tpyc/thir/shape.py`): the routed-body count
  (299k+) is body-weighted -- the stdlib links into every case, so one body
  counts once per case; that measures throughput, not migration progress. The
  shape tally fingerprints each candidate body (routed or fallback) with a
  structural signature (`kind | <sorted AST node-kinds> | p:<param families> |
  r:<return family>`), invariant across the stdlib-repeat AND cross-module
  structural twins, then dedups. The `--thir-codegen` summary prints
  `tpy| thir shapes: R/T distinct shapes routed (P%); K partial; M blocked (top
  reasons by distinct shapes: ...)`; full per-signature detail dumps to
  `$THIR_SHAPES_JSON`. FIRST FULL-CORPUS READ (2026-07-06, post-Own[T] merge):
  **691 / 5136 distinct shapes routed = 13.5%** (vs ~57% by BODIES -- the routed
  bodies are the common repeated ones; the long tail of 4445 distinct blocked
  shapes is largely untouched). Excluding known out-of-scope frontiers
  (async/generator/overload/builtin-receiver-method/...) the IN-SCOPE figure is
  **691 / 4583 = 15.1%** (+184 partial). The distinct-shape view confirms the
  frontier ranking: the biggest remaining shape mass is `sig.param_type` (631
  distinct shapes) + `sig.return_type` (402) -- the param/return type-family
  slots (records/ptr/containers/tuple/union) -- then a LONG low-leverage tail
  (`stmt.var_decl:call.ret_type` 348 shapes / only 2446 bodies) that body-count
  hides entirely. A "partial" shape (both routed + fallback bodies) means the
  signature is too coarse to separate a type-driven routing split; counted as
  not-yet-routed. Same coarseness contract as the fallback tally: an ORDINAL
  instrument (which shapes are big), not a precise census.
- **Landed: per-component AST-fallback tally** (`tpyc/thir/fallback.py`): the
  `--thir-codegen` summary prints `tpy| thir fallback: body N -- <top reasons>`
  and `... ctor M -- ...` -- per deletion target, how many candidate bodies the
  gate rejected, keyed by FIRST-reject reason (`sig.*` signature gates, `body.*`
  function-level facts, `stmt.*` the first ineligible statement's shape,
  `expr.*` a landmark construct inside it -- comprehension / genexpr / lambda /
  await / walrus, `ctor.*` constructor gates). Full counts dump to
  `$THIR_FALLBACK_JSON`. First-reason attribution is deliberately coarse (a
  body may hold several blockers, and the landmark scan tags the first
  landmark found anywhere in the rejecting statement -- the two
  approximations compound), so read the counts as ORDINAL (which rocks
  are big), not cardinal per-construct totals; they measure "what would
  have to land first", which is the sequencing question. First full-corpus read
  (2026-07-05, body-compilations summed over ~3.3k cases): the mass is
  signature-level -- `sig.receiver_record` 2.85M (non-F1 records: cross-module
  / native / value receivers), `sig.special_callable` 377k, `sig.generic_fn`
  56k, `sig.linkage` 46k, `sig.param_type` 41k -- then `stmt.expr_stmt` 13.8k,
  `stmt.if` 11k, `stmt.var_decl` 10.1k, `stmt.return` 8.1k; comprehension-
  family landmarks first-block only ~67 (the statement-shape tail is small
  next to the receiver/param form frontiers). NB that first read predates the
  no-body exclusion (native decls / overload stubs / native ctors are not
  fallback -- they have no body emit and are excluded from the fold since),
  which deflates `sig.special_callable` and part of `sig.receiver_record` in
  later runs. The `stmt.*` rows are per-case-compilation sums: a drill over
  one all-stdlib compile found ~249 DISTINCT stdlib bodies first-blocked on
  statement shapes (77 return / 67 var-decl / 54 if ...) -- each multiplies
  across every importing case, so expression-level cells inside those bodies
  are the highest-routing-leverage statement work.
  **CORRECTED read (2026-07-06, after F5 + the bodyless-binding tally-exclusion
  fix): `body` fallback 254097, `ctor` 19385.** The exclusion now drops
  method-style `@native` / `@cpp_template` bindings (only `native_function` was
  dropped before), so the whole builtin-type method/ctor surface leaves the
  tally: `sig.receiver_record` 1648972 -> 30439 (the remainder is BODIED methods
  on non-F1 receivers), `ctor.non_f1_record` 94108 -> 3631. The earlier 1.65M /
  2.85M `sig.receiver_record` figures were dominated by bodyless builtin
  bindings, NOT generic user records -- a survey conflated the two. Post-fix top
  body reasons: `sig.param_type` 65k, `sig.return_type` 51k, `sig.generic_fn`
  33k (generic FREE functions -- now routed, the row falls to 9.8k method-level
  generics). The `Own[T]` param+return cell then took `sig.param_type` 70k->51k
  and `sig.return_type` 55k->35k (the own:typeparam slices). Then the `stmt.*`
  tail.
- **Landed: stmt.* sub-classifier** (the detail slot in `fallback.py`): gate
  reject arms record the blocking SUB-construct via `note_detail`
  (set-if-empty, cleared per statement), and the chokepoint composes
  `stmt.<shape>:<detail>` -- so the tally now names what's inside a bare
  statement-shape row. First composed corpus read (2026-07-05, stmt.*
  weighted total 47.8k): `call.imported_symbol` 14.2k +
  `call.native_or_template` 1.8k = the call-symbol qualification blocker
  (the completeness row below), `method.receiver_shape` 7.2k (non-bare-name
  receivers: `self.field.method()` etc.), `cond.field_access` 2.4k (landed
  as incr 94), `binop.shape` 2.2k, `assign.field_write_shape` 2.0k; str
  methods rank individually (`method.str.rfind` 714, `.startswith` 693,
  `.find` 346) and are NOT a big rock. The stdlib drill (distinct bodies)
  gives the same ordinal ranking.
- **Landed: wave-6 call/method sub-family drill (2026-07-21).** New routed
  families: container-family FIELD receivers (`_method_field_receiver_ok`
  gates on the same `_container_method_recv` predicate as the method-family
  dispatch table); the Own-slot copy-temp on USER-method args (containers
  included); inherited-`__init__` and plain-`@native` / `@native_c` ctors
  (both ride the no-own-overloads `ri.init_params` fallback --
  `_ctor_effective_params`; `@native_c` aggregates via
  `THIRCtorCall.brace_init`); user-module `@native def` and C-linkage free
  callees (`call.native_c_free` kind: raw symbol on `callee_cpp`); the
  TypedDict family (ctor via the init_params fallback, subscript read
  `d["k"]` -> `d.k` with the total=False `typed_dict_field_check` wrap,
  total=True writes/aug as field lvalues); Deref-guard container MEMBER
  stubs (`g.append(4)` -> `g.__deref__().push_back(4)`;
  function=True/cpp_template stubs stay AST). Dial 1913 -> 1961/3485.
- **Landed: wave-7 container-ret + Iterable-slot families (2026-07-21).**
  Container-returning USER methods route at native/template arg positions
  (`::tpy::__len__(g.get())` -- `_native_container_call_arg` + STORAGE-use
  threading), at DISCARD (`method.container_discard`), and through
  Arc-chained guards (pointer-receiver deref chains,
  `g->__deref__().push_back(3)`). The stub-method `Iterable[Own[T]]` slot
  family landed (literal / bare-name / call-rvalue rows, the explicit
  `own_iter(x)` arm, the movable last-use `::tpy::own_iter(std::move(b))`
  wrap -- STUB loops only; free natives bind bare). Latent THIR divergence
  fixed: container-element converts apply BEFORE the move wrap
  (`std::move(std::string((*a)))`, the make_vector face), and seeded
  value-opt VIEW params count as movable elements. Dial 1974 -> 1988/3485.
