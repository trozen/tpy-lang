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

> **Compaction convention (ratified 2026-08-04):** at wave close, fold that
> wave's per-batch review-round entries into the wave's own entry -- keep
> DECISIONS, PROBED INVARIANTS, and LESSONS; drop the pass/fail bookkeeping
> prose ("suite green, N passed"). Those three categories are never deleted.
> Compaction applies to the just-closed wave only; older entries are not
> rewritten retroactively.

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

### The cutover deletion budget -- THIR's OWN code that dies with the AST emitter

Deleting `codegen_cpp`'s body/form emit is not the end of the cleanup, and the
size numbers say why. Measured 2026-07-26 (non-test LOC):

| | LOC |
|---|---|
| `tpyc/thir/` total | 48,567 |
| `tpyc/codegen_cpp/` -- the emitter THIR replaces | 36,488 |

THIR is **33% larger than the emitter it exists to delete**, and essentially
all of the excess is migration scaffolding rather than compilation:

- **~10,200 lines of ADMISSION logic** -- functions returning bool / a reject
  reason that never build a node. `lower/checks.py` is ~78% this, `lower/
  predicates.py` ~51%. Every one answers "can THIR mirror this shape?", a
  question with **no meaning once THIR is mandatory** -- there is nothing to
  fall back to, so the gate can only reject a program the compiler must accept.
  These do not migrate; they DELETE, together with `ThirUnsupported` and the
  try-lower-catch boundary at each entry point.
- **~3,000 lines of instrumentation** -- `faces.py` (1,360), `dump.py`,
  `validate.py`, `fallback.py`, `shape.py`: the coverage/measurement apparatus
  the migration steers by. It has no post-cutover consumer either.

Subtract both and THIR lands at ~35k against the AST emitter's ~36.5k --
**parity**. That is the honest read of what the migration buys: STRUCTURE (a
typed IR, one sema->codegen boundary, the cache point) at roughly equal size,
not code reduction.

The reduction is collectable only AFTER cutover, and only deliberately. While
byte-identity is the contract, each lowering arm must reproduce the AST's
INCIDENTAL splits rather than replace them -- `retype_scalars`,
`field_prechecked`, the resumable frame-emplace bare tuple element are all
mirrors of emit accidents, not of semantics. Once the oracle is gone those
arms can merge. Plan the post-cutover simplification pass as its own phase;
"the AST emitter is deleted" is the halfway mark, not the finish.

`subscript_prechecked` used to be listed alongside those; design round 15
verified it is NOT one. The AST has a single consumer-blind element emitter
(`codegen_cpp/expressions.py:6098 _gen_subscript`) with no `len`/`print`
split to mirror, so the prechecked rows were THIR-internal admission
scaffolding around `ret_ok`'s missing container arm. **COLLECTED (decision
32):** `ret_ok` grew the container arm (face `subscript.container_elem`) and
the two per-sink rows, their faces (`len.elem_subscript`,
`print.elem_subscript`) and the bypass argument are deleted. The remaining
`subscript_prechecked=True` call sites are receiver-position prechecks, a
different fact.

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
  relocation (design resolved 2026-07-22: the AST emit no longer burns a
  temp number for temp-only elif conditions -- `TempState.probe_checkpoint`
  / `rollback_discarded` in `codegen_cpp/context.py` is the seam the THIR
  mirror consumes; walrus-mixed conditions keep the legacy burn and stay
  rejected), method POINTER-variant union slots
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

- **Async BORROW-form returns** (`return.borrow_form`, `_lower_resumable_return_value`):
  a bare reference-type async return has a pointer Poll payload (`Poll<C*>`, the async
  borrow-return ABI); the `&(...)` lift and its alias-source renders are unported, so
  the body falls back (8 async cases marked no_thir carry the shape or its erased
  task-layer consumers). The generic TRAIT form (`val_or_ptr_t<T>` +
  `to_val_or_ptr` lift) IS mirrored (the `async_ret_val_or_ptr` THIRCoerce). To CLOSE:
  mirror the borrow lift (form-driven, one arm) plus the await-site pointer-alias
  binding consumers.

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
  plain-enum literal-`true` fold keeps a side-effecting CALL operand as a
  `static_cast<void>` discard), IntEnum unary minus, `.value`,
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
non-`_is_duplicable_expr` intermediate binds `_cmp` temps), bool-literal conditions
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
`tpyc/thir/test_thir_try.py`. NEW RESIDUAL (finally-deferred return
capture): EVERY eligible `return <ref-type name>` under a
non-suspending finally now defers materialization past the chain on
the AST path (structural candidacy, sema-stamped
`TpyReturn.finally_deferred_capture`); THIR rejects the stamp
(`return.finally_deferred_capture`, both the sync dispatch and
`_lower_resumable_return_value`) and falls the body back -- porting
needs the borrow-capture/materialize split mirrored in
`_emit_finally_return`. Witnesses carry `no_thir.txt`:
`exceptions/finally_mutates_returned_local`,
`exceptions/finally_mutates_returned_local_indirect`,
`exceptions/finally_return_nocopy_move`,
`async/async_finally_mutates_returned_local`.
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
   literal BigInt slice bounds) is
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

### D2 wave (2026-07-27): top-level statements + the value-position element gate

The first lowering entry added since the three original ones. **Top-level
statements had no THIR seam at all**, so fallback could not be registered for
them and the per-case ratchet was structurally blind to `__tpy_init` -- a case
could be counted migrated on its function bodies while emitting its entire
top level from the AST path.

`lower_top_level` seeds the GLOBAL variable model rather than a function-local
one: names pre-declared at namespace scope (a write is an assign), non-value
globals as writable pointer slots, `Final` globals read-only, slot temps as
`static __global_slot_N`. Everything below that seeding reuses the ordinary
statement/expression arms, which is why the construct surface was small.

New renders, each mirroring an AST arm: `PtrSlotKind.GLOBAL_RVALUE`,
`THIRImportInit` (the AST's import-chain dedup extracted into the shared
`module_init_targets` -- neither path can drift now), the `Final` skip, and the
order-aware import qualification. Every OTHER slot-allocating node rejects the
whole module-init body: at namespace scope those slots need `static` too, and
only GLOBAL_RVALUE is wired for it.

**Metric impact:** +119 cases marked, -8 unmarked. The additions are the cases
the old dial flattered. The A3 residency baseline is no longer corrupted and
can now be spent.

**The lesson worth carrying:** a rule that fires at "every value position"
needs the opt-out list enumerated, not assumed. The pointer-slot-global deref
broke four consumers that want the raw `T*`; the whole-corpus byte-diff found
three, and the fourth (the F2b field-write lift, `h->v = ptr_to_optional(g)`)
had NO corpus witness and came out of an adversarial dualgen over every
consumption position. Byte-diff green over 3590 cases was not sufficient. The durable guard is
`TestPointerSlotGlobalConsumers` (`tpyc/thir/test_thir_top_level.py`), which
pins each consumer's routing AND byte-identity -- add a case there when
adding a consumer that binds a pointer.

Also landed in the same branch: the value-position container-element gate arm
(decision 32 -- see the mirrored-accident note above), the container-literal
union-record element row, and the two field-write rows (the field-over-field
receiver the aug-assign twin always had, and the value-repr `Optional[scalar]`
bare store).

## Maintaining this ledger

- Flip cells / update statuses when a rung lands or a deferral is discovered.
- **Landed: frame-layout render rows, wave 1** (2026-07-31; +3 flips, dial
  2881 -> 2884/3667). Three rows against the FrameLayoutPlan kinds: the
  dict_items proxy-ref borrow-tuple loop var (advance-gate admit
  `res.loop_btuple_bind`, head-unpack alias targets split
  `frame_ptr_elem` off a borrow-form source vs `frame_ptr_addr` off the
  pointer-holder's storage tuple, discriminated on `lc.pointers`
  membership -- the same set that picked the source arm); value-repr
  `Optional[scalar]` tuple elements at `_value_tuple_element_ok` +
  `_tuple_subscript_value_read` (pointer-repr Optional elements pinned
  out); `__unpack_*` decomposition temps through the ordinary
  single-assign alias renders plus the alias-of-alias bare pointer copy
  (`a = __unpack_0_0;`). LESSON: probe before admitting -- the
  owned-optional `Own[T]|None` storage admit was reverted because both
  its cases move to `res.return_type` / `expr.call` / `res.leaf_try`
  first (an admit with no routing witness is a blind spot); the erased
  adapter handle (2 sole-blocker cases) is the remaining direct-yield
  cell, and the value-variant frame union stays behind the
  `_narrow_binding_supported` fence.
- **Landed: frame-local placement plan (FrameLayoutPlan)** (2026-07-31, the
  `res.local_storage` design item). The resumable frame-local storage form is
  now classified ONCE (`gen_async._frame_layout` -> per-name
  `FrameLocalKind` verdicts + const/payload, cached on
  `ResumableFuncState.frame_layout`) and consumed by all three former
  independent classifiers: the struct field-decl ladder, the body-context
  seeding (`setup_resumable_frame_locals`), and THIR admission (through the
  seam; the old `pointer_aliases` seam channel is deleted). The
  simple-generator peephole builds the plan on demand with its
  empty-by-construction prescan sets; the protocol-local error moved from
  classification to the struct field render. Byte-identical refactor (dial
  unchanged); THIR's per-kind arms now carry only ADMISSION (which render
  families are mirrored), so the remaining `res.local_storage` rejects are
  per-family render cells, and any new placement family (cf. master's
  SOURCE_FORM_SLOT, which landed mid-branch in triplicate and merged into
  one arm) is one builder row + one render row per consumer.
- **Landed: field-write plan-dispatch decomposition** (2026-07-31, the hard
  gate from the readiness retrospective). The 17-predicate admission
  disjunction + ~400-line `TpyFieldAccess`-target if/elif chain in
  `_lower_stmt_dispatch` is now `tpyc/thir/lower/field_write.py`: nine
  (classify, lower) families over frozen plans -- opt-none, class-const,
  value (scalar/value-opt/str/bytes), record (PLAIN + OPT slots merged, one
  source-row cascade), container (its own collapsed tail slice), tuple,
  union, opt-lift (F2b), and a residual for the type-param-slot /
  readonly-btuple shapes no render row claims. The old generic tail's
  borrow/name render survives as ONE named helper (`_lower_tail_value`)
  shared by four families exactly as the chain shared its else arm; the
  five-boolean multiplexing is gone. Byte-identical: every family keeps its
  arm's witness strings, reject tags, and target-vs-value evaluation order
  (temp numbering follows lowering order). New field-write rows now land as
  one classifier row + one render row keyed by a plan enum.
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
- **Landed: field-receiver/opt-record/dunder wave (`thir-forhead-recv-wave`,
  2026-07-22, +29 flips, dial 2109 -> 2138).** Six cells: (1) the
  field-receiver drill -- storage-form tuple FIELD subscripts
  (`std::get<N>(c.data).x`, read + scalar write, `_subscript_recv_tuple`'s
  field arm + the STORAGE form fix for field-rooted Optional elements),
  record-returning user-getitem receivers (`points[0].x`, the shared
  `_record_getitem_idx_recv_ok` gate + the prechecked record construction,
  form BORROW), unproven STORAGE `Optional[record]` field chains
  (`deref_optional_check(h.opt).x`, the new `THIRFieldAccess.opt_deref_check`
  flag), pointer-first-hop user-Deref chains (`r->__deref__().x`/`.sum()`,
  the field emit's arrow first hop + the shared
  `_deref_wrapper_receiver_record` twins), class-constant receiver
  effect/check statement expressions (`THIRClassConstant.recv_eval/recv_wrap`),
  property-getter record receivers, and the Ptr-deref `record_ret_ok`
  RECEIVER slice; (2) the tplib opt-record family (design-track memo A:
  the generics-F1 interlock was STALE) -- owned-optional record slot decls
  (`std::optional<Rc<T>> u = w.upgrade();`, the new branch-scoped
  `value_opt_record_locals` binding class: narrowed `(*u)` reads, has_value
  None-tests, method-gate dispatch on the narrowed inner), storage-sink
  owned-record method rvalues, accessor-receiver optional-field writes
  (`a.get().next = b.clone()`); (3) readonly-param key-function lambdas
  (`_callable_param_cpp(readonly[T])` + `to_cpp_return_const`); (4) the
  record-dunder rvalue arms (memo B: premise refuted -- templates already
  injected) -- result admission + print/field/method/discard consumers,
  incl. the `__rfloordiv__` swap (whose PRE-EXISTING operand-order
  divergence vs CPython is now filed in BUGS.md); (5) extern-C option A
  (memo D) -- EXPORT_C bodies with no str param (str-param residue tagged
  `sig.linkage_c_abi` until cutover) + verbatim raw-symbol callees (the
  refuted "len(name) uncompilable" BUGS candidate: runtime carries
  `__len__(const char*)`); (6) iter-proto REF-target unpacks
  (`for i, p in enumerate(ps):` -- the borrow-tuple head binds
  `auto& __tup_N` via TupleSourceBind.NAME_REF, no lift; the existing "ref"
  emit arm renders the alias). Five stale still-defers pins flipped to
  routes-pins with corpus witnesses in hand. Parked residuals in TODO.md's
  calls-wave frontier entry.
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
- **Landed: capture/kwargs/no-init wave (2026-07-22, 5 cells).** (1) The match
  WHOLE-SUBJECT CAPTURE tier: `_route_hoists` classifies F1-record hoists
  into `_emit_branch_decls`' non-value forms -- borrow-only pointer
  (`T* q;`, kind 'ptr'), single-bind owned-optional slot
  (`std::optional<T> s;`, 'opt_storage'), and the pointer-repr-Optional
  bare-inner pointer ('opt_ptr'); THIRMatchBinding grew assign_addr
  (`q = &(__match_subject_N);`) and assign_move (`std::move` from a
  MATERIALIZED call/ctor rvalue subject, subject_ref=False); the
  chain-optional tier admits storage-form FIELD subjects via the O1
  `optional_to_ptr` lift and binds the full Optional through the hoisted
  pointer. (2) The optional-ptr 'ctor' face realized (both flush_slot rows).
  (3) Value-union ctor-arg literal temps: None -> the monostate temp, a bare
  int literal -> the target-less render (single int-family member only).
  (4) No-init pointer-Optional decls (`h: Handle | None` ->
  `Handle* h = nullptr;`, NO rebind pre-decl) + the slotless reseat arms:
  INLINE_RVALUE (in-place plain block slot, reused by later rvalue reseats)
  and the same-Optional pointer-name copy. (5) TypedDict ctor calls (the
  kwargs-pack rewrite): fi-less `Options(...)` lowers over `init_params`
  (`_typed_dict_ctor_call`), with the free-call ArgTemp and const
  method-slot inline rows. Dial 2138 -> 2152/3502 (+14: match capture x4,
  optional-ptr ctor x2, field_none, auto_move no-init x2,
  kwargs-unpack x5). Parked with verified blockers: container-literal
  REBOUND locals (a whole container pointer-local binding class),
  nested-position ctor arg temps (memo G phase 2, blocked on the AST elif
  cleanup), json qualcall recursive-wrapper literals, kwargs_unpack_get_in
  (kwargs.get lane), the no-init family's _from_global/_return tails.
- **Landed: adapter/temps wave (2026-07-22, 5 cells).** (1) NESTED temp
  threading: `allow_temps` rides through call-shaped args, method-call
  receivers, binop operands (`temps_ok` on `_lower_binop`), the
  coro-factory arm, and the NESTED_ARG ctor gate branch (gates like DIRECT
  with the ridden flush right); the validator's `argtemp_ok` propagates
  through all expression nesting. Plus the dict/set/Array free-call
  literal ArgTemp row (`_ref_param_dictset_literal_arg`). (2) COND-position
  temps (memo G phase 2): TempSink/CtxTempSink grew the checkpoint /
  flush_since / declare_named seam; `_emit_while` mirrors the restructured
  head (`while (true) { <temps> if (!(cond)) break; }`), `_emit_if`
  flushes pre-`if` and nests the elif chain-abandon (same __tmp numbering
  as the AST's discard-and-regenerate); the sgen leaf rides the skeleton's
  checkpoint machinery; the value-scalar WALRUS arm landed (THIRWalrus:
  `(n = v)` + the sink's named pre-decl row); mixed walrus+temps conds
  reject. Latent divergence fixed: sgen yield values thread the yield type.
  (3) Container pointer-local decls (memo I): NAME-reassigned
  container-literal first decls route PtrSlotKind.RECORD_RVALUE
  (`std::vector<T> __slot_N = {..}; std::vector<T>* xs = &__slot_N;`);
  setitem admits the F2d pointer-container receiver; literal-rvalue reseats
  ride INLINE_RVALUE. The fixed-int literal binop arm re-keyed on the
  THREADED target via a `_slot_literal_retype` rebuild
  (`template_override`) -- fixes a latent width divergence (`b: Int64 =
  (4+5)+6` emitted add_check<int32_t>) and the target-less paren/fold
  policy (syntactic involves_variables guard). (4) The SHARED
  `classify_dyn_own_arg` classifier (decisions 4/round-5 cell A): verdicts
  coro_handle/forward/async_factory/inherit/structural extracted
  analyzer-pure into codegen_cpp/protocols.py, consumed by the AST render,
  `_is_dyn_own_wrap_needed`, and THIR's `_dyn_own_conformer_arg`
  (make_unique / make_adapter rows at the free/ctor/method Own[dyn P] arg
  gates); the sema `directly_implements_dynamic` mirror now delegates
  (drift closed). Instantiation ctors admit at NESTED_ARG positions; the
  erased `unique_ptr<P>` payload joined `_own_lvalue_temp_slot`.
  (5) Covariant-ARG admission (round-5 cell B): rvalues bind Own slots
  inline via the converting ctor (`_own_record_rvalue_arg` upcast
  widening); names hoist the `Box<Shape> __tmp_N = std::move(bc);` typed
  temp (`_covariant_temp_arg`). Dial 2155 -> 2189/3513 (+34: nested-temp /
  cond-temps / container ptr-local x16 incl. bonuses, adapter/covariant
  x18 incl. bonuses). Parked with verified blockers: json qualcall
  trio (needs the recursive-union ELEMENT literal family + the qualcall
  ArgTemp row; oracle read, memo III analysis stands), datetime
  strftime/tzenv/timestamp (kwargs-ctor + globals lanes),
  urllib_parse_quote (tuple-for-head lane), walrus_reassign (Optional/str
  walrus slices), user_dunders inplace increment (memo B pricing STALE --
  fragments into record-binop returns + decl slots + aug-assign, not +2).

- **Landed: union-none/globals/upcast wave (`thir-wave-next3`, 2026-07-22
  night, 5 cells, dial 2189 -> 2214, +25).** (1) The json qualcall trio
  (memo III): `_ru_wrapper_arg_slot` + `_ru_container_literal_ok` +
  `_lower_ru_literal` -- list/dict LITERALS into non-generic
  recursive-union wrapper slots hoist `JsonValue __tmp_N = <literal>;`
  (typed list prefix, monostate None, self-describing ordered_map;
  int32-range/finite scalar literals + str-literal dict keys only;
  nocopy wrapper members reject). Face argtemp.recursive_union_literal.
  (2) Union/None lane (round-6 A1/A3 + cell B): THIRIsNone grew
  `union_monostate` (member-blind `std::holds_alternative<std::monostate>
  (v)` on a non-wrapper union NAME binding, commuted included; narrowed
  subjects reject; wrapper unions defer -- the `.value` read);
  `_eligible_ptr_union` admits eligible-SCALAR members of MIXED unions
  (`Int32 | Dog | None` -- renders are member-shape-blind);
  `_union_ctor_temp_arg` + `_ptr_union_slot_kind` take scalar type-ctor
  rvalues (`check(Int32(1))` -> `int32_t __tmp_N = 1;` + address lift /
  the UNION_RVALUE decl slot). (3) Globals write seeding (round-6 lane V
  cell A): native-linkage `global` names seed with the BARE C-name write
  target (`prescan.global_write_cpp`) + `::`-qualified reads;
  `_seed_global_scope` wired into lower_constructor and the resumable
  entry (a global is never a frame field -- writes render the sync
  module-slot form). Optional/str globals stay unseeded (the gated
  Optional-global cell). (4) Free-call arg widenings:
  `_record_rvalue_temp_slot` admits SUBCLASS rvalues (CHILD-typed temp,
  the AST's arg-typed hoist); `_record_pass_through_arg` admits
  readonly[record] slots for bare NAMES; the ternary CONDITION inherits
  the flush right (evaluates once; ARMS stay reset -- pinned).
  (5) Inplace dunders admit (round-6 VI increment A): `_param_is_const`
  grew the CONST_PARAMS_METHODS forced-const arm; `return self` rides
  the record-self arm, the T& return is skeleton. PARKED with verified
  blockers: value-record union isinstance (`Fixed | Zone | None` ctor
  conds -- `_eligible_value_union` member-table widening, oracle
  `const auto& __tz = std::get<Fixed>(tz)`); the whole-union
  call-rvalue ArgTemp at ctor slots (`Box(mk(...))` -- the A2 lane;
  the eval_once pair + union_none_default_param need BOTH);
  submodule-native ctor rvalues (from_pkg_import_submod pair);
  rc_new_arg_passing (generic static-factory rvalue temp, memo-A
  spelling asymmetry); optional_other_nonetype conds (fragments 3+:
  container-borrow / callable / subscript subjects).

- **Landed: valrec-union/coro-temps/returns wave (`thir-wave-next4`,
  2026-07-23, 8 cells).** (1) Value-record unions (the parked
  isinstance rung): `_eligible_value_union`'s member table admits
  non-generic user VALUE records (`_value_record_member` -- `Fixed |
  Zone | None`, datetime's `ZoneInfo | timezone | None`); isinstance
  narrowing, monostate None tests, and the variant ArgTemps ride the
  existing U1 machinery; `_value_union_temp_slot` folds the record row
  into the main flow (Own-unwrapped ctor/call rvalues). Nested-temp
  threading grew three positions the family needed: method-arg record
  rvalues thread `nested_temps` into their nested ctor args (the
  eval_once shape), borrow-tuple RVALUE elements take `elem_temps` +
  the pointer-repr Optional elem-slot unwrap, and call-shaped FIELD
  receivers inherit the statement's flush right. (2) Consuming rvalue
  receivers + coro temps: a consuming fi admits an RVALUE call/method
  receiver (no move -- the AST moves NAME receivers only); a
  coro-factory call rvalue at a STRUCTURAL protocol slot hoists the
  un-spelled `auto __tmp_N = f();` (bypassing the protocol-typed
  lvalue bare-forward; init under use.coro_factory); generic plain
  callees run the protocol-slot pre-arm -- the whole
  poll_once/.value() family (11 async cases). (3) Module-qualified
  ctor rvalues (`pcre2.Code(7)` -- a TpyMethodCall with a synthetic
  ctor fi) ride the record-rvalue temp row via
  `_module_qual_ctor_shape` (marker "qualified" kind; STORAGE-result
  init). (4) Spanlike coerces admit container FIELD inners
  (`return self._data` at Span slots -- the @auto_readonly pair, 8
  witnesses). (5) Method-call record rvalues at STORAGE return slots
  render the bare call (ret.record_methodcall). (6) Bare value-record
  decl slots (the plain spelled copy -- datetime date/datetime);
  recursive-union WRAPPER decls from CALL inits ride the storage_call
  escape with the container reassigned-guard extended to wrappers (a
  reassigned wrapper is an AST pointer-local -- dualgen-caught; NB the
  `_storage_call_ret` wrapper verdict reaches every gate consumer of
  that predicate -- the decl row is the only render-relevant one, the
  rest render the bare call);
  wrapper NAMEs pass bare at same-wrapper marker slots;
  print_optional_val admits value-record-inner method results.
  (7) Borrow-container returns (`-> list[T]` -> `std::vector<T>&`):
  bare name / plain field sources return bare (ret_container_borrow).
  (8) Value-record rvalues admit at NESTED ctor positions
  (`timezone(timedelta(...))` -- temp-free). PARKED with verified
  blockers: value-opt RECORD bindings (`Fx | None` params -- the
  `_value_opt_scalar` 32-consumer widening, a future cell);
  print.arg.scalar_bin_op (RETIRED as a tag: the print arm's own
  shape label overwrote the inner reject reason, so the "3+ operand
  families" reading was the catch-all, not the blocker; the arm now
  reports the inner reason and this tag no longer appears);
  bounded-T method receivers (`factory.create_point(x, y)` -- generics
  lane); negated 3-member isinstance ifs (if.narrow_shape,
  member-count-driven); consuming @native free-form methods
  (`v.pop_last()` -> `::tpy::pop_back(v)`); deferred-inference generic
  methods; the lambda inferred-signature family (nested-def lane);
  wrapper READ lanes (fields/subscripts -- the json read cells);
  `return iter(self._items)` iterator-protocol returns; `-> Any`
  literal returns (into_any at return); exceptions-class isinstance
  dispatch; NativeIterable protocol narrowing.

- **Landed: create_task/res-iterable/aliases wave (`thir-wave-next5`,
  2026-07-23, 7 landed arms across 6 cells, +27 flips, dial
  2265 -> 2292/3533).** (1) The
  create_task cluster: `_marker_call_supported` gains the record-family
  DISCARD row (`asyncio.create_task(...);` -- the bare call statement,
  qualcall twin of method.record_discard) and the record-rvalue STORAGE
  row (`t1 = create_task(reader(b1))` -- `_moved_record_ret` at
  storage+rvalue, admitting non-F1 generic-concrete rets like
  Task[bytes]; sync-lane face unit-pinned). (2) Coro-factory marker
  lift: `_marker_call_kind` gains coro_factory_ok (lifts ONLY the
  async-callee reject, mirroring _free_callee_kind);
  `_dyn_own_coro_factory_arg` admits module-qualified async factories
  (`create_task(asyncio.wait_for(slow(), 5.0))` -- nested adapters) and
  MEMBER async methods (`asyncio.run(b.take())` -- inline method call
  in the wrap); the qualcall ret gate gains the coro_factory_ok
  dyn-protocol escape; `_method_call_arg_ok`'s kind re-derivation passes
  the flag unconditionally (arg rows are kind-keyed only). (3)
  `_record_method_arg_ok` gains the none-unit row
  (`fut.set_result(None)` on Future[None] -> `std::monostate{}`).
  (4) The resumable for-head iterable lowers under the ITERABLE result
  use (the skeleton's source capture is position-blind gen_expr) --
  generator factories, container-returning calls, and dict-view
  iterables route in res for-heads (gen_resumable_delegate trio).
  (5) Res-match hook mode admits VALUE-kind hoists (a resumable's
  locals are frame fields -- decl suppressed, face
  match.hoist_value_frame) and ASSIGN-mode whole-subject bindings (the
  frame-field write both paths emit before the arm-body point);
  pointer/optional hoist kinds and copy/ref bind modes stay rejected.
  (6) REF_ALIAS decls off PLAIN pointer-local sources
  (`Point& alias = (*p);` -- deref forced at the alias arm; the old
  reject pin re-pinned routed; Optional-declared sources stay AST).
  (7) Generator-factory rvalues at container instantiations
  (`list(gen())` -> the bare factory inside the construct template,
  face call.inst_gen_arg; map/filter builtin factories stay AST).
  PARKED with probe-verified blockers: the reactor pair's method-call
  CONFORMER args (non-async awaitable-record rvalues into Own[@dynamic]
  slots + Optional-ptr receivers); res.local_storage heterogeneous
  frame locals; sgen tuple yields (tuple_to_pointer bridge); the
  SpanIter value family; tuple literals with container/union elements
  at storage decls (3 render sub-shapes); Optional[container] params;
  optional_other_nonetype cond subjects; generic generator factories
  and generic member-factory method targs (generics lane). REFUTED:
  the wave-next4 "value-opt RECORD bindings = biggest lever" claim --
  the fresh corpus map has ZERO cases blocked on name.optval_read.

- **Landed: globals/walrus/value-select wave (`thir-wave-next6`, stacked
  on `thir-design-round7`, 2026-07-23, 9 cells, +40 flips, dial
  2292 -> 2332/3538).** Consumed FOUR standing design approvals in one
  wave. (1) GlobalSlot (round-6 approval): non-value record/container
  globals seed read-only into scope + lc.pointers
  (`_pointer_slot_global_type`; Final/native excluded), riding the
  pointer-local arms -- `(*g)` value derefs, `g->` receivers AND field
  writes, the addr-Ptr coerce `&(*g)` (the AST's indirect pre-deref
  mirrored at the THIR coerce arm for pointer-locals too), `T& q =
  (*g)` alias binds, resumable/ctor bodies via the shared seeding. The
  skeleton-owned seams strip the leaf deref (`_strip_slot_leaf_deref`:
  async-for iterables, sync/async with managers, erased await
  operands -- the corpus caught 3 divergences the dualgen missed). The
  simple-gen lane REJECTS global iterables: its AST oracle emits
  uncompilable `.begin()` on the slot pointer (pre-existing, filed in
  BUGS.md). (2) Globals cell B (round-6, ungated by the valueopt-deref
  fixes): same-module value-Optional scalar globals seed read-only AND
  via `global` decls, registered in lc.value_opt_locals (bare
  whole-optional reads, narrowed `(*g)`, `= std::nullopt` writes);
  `global`-declared pointer-slot globals seed as slots (rebinding is
  sema-forbidden); imported value-opt globals stay unseeded (pinned).
  (3) The walrus target-class ladder (round-7 approval, rungs 1-4):
  THIRWalrus gains init/tail/addr_of/slot fields -- ptr-Optional
  targets (`T* t = nullptr;` + optional_to_ptr assigns, walrus
  None-compare operands), borrow-alias pointer targets (`(q = &(b),
  *q)` incl. ref-returning call sources, walrus field receivers, len
  args), value-opt + owned-viewfam reassigns, owned non-value slots
  (`std::optional<T>` + walrus-deref dot reads; comprehension sources
  lower directly; container walrus print args), borrow tuples
  (non-reassigned predecl + literal/tuple_to_pointer lift; reassigned
  `__slot_N.emplace` + bare-name tail + collapsed-element reads/writes;
  the btuple field-elem write row; F3 tuple-field reseat sources).
  `walrus_predeclared` mirrors the AST's function-scoped asymmetry.
  Boundaries pinned: hoisted targets, resumable bodies, first-decl
  viewfam, Optional-element writes, IS_TRUTHY operands. (4)
  THIRValueSelect tier A (round-7 approval): value-position and/or on
  a dedicated node -- once-evaluated LHS (`auto&&`/string_view temps
  off the shared counter), lazy in-branch RHS, bare/nonempty truthy
  modes, mixed-operand casts, chains; the form tag carries the runtime
  spelling so view chains compose with the owned-sink copy. Record
  results / pointer-select / isinstance-LHS stay rejected; bool
  positions keep the bool arm (the AST splits on RESULT type alone --
  the two stale bool-op pins re-pinned). (5) sgen tuple-literal yields
  (round-7 de-design): the resumable Yield tuple arm mirrored in
  _lower_loop_body. (6) compile-time asserts (round-7 cluster 3):
  assert_send/assert_sync statements lower to THIRNoOpStmt with
  trivia_loc (leading comments emit, no source line). (7) The
  record-ctor/method union-arg rows (round-7 cluster 1 union
  sub-cell): ctor slots gain the free-call union rows (pass-through /
  coerced literals / Own[union] ctor rvalues), method slots the
  non-dcbp ptr-variant pass-through, the Own cascade union payloads
  (`Sink(std::move(v))`), union-returning calls land bare at
  storage/borrow-bind sinks with plain ptr-variant returns as bare
  decl sources (Own[union] factories keep the UNION_RVALUE slot).
  PARKED with verified blockers: union_field_assign(+_nullable)
  (`name.union_binding_divergent` -- the narrowing-divergent-read BUGS
  family); the send_sync lambda-call-arg trio; walrus try-hoisted
  targets (the AST's forward-declared hoist model); the iterator-global
  for-head (`decltype` iterator slots); Final non-value globals (bare
  namespace-scope renders); imported value-opt globals. Lesson: the
  corpus byte-diff catches skeleton-seam double-derefs dualgen misses
  -- probe every skeleton-owned position (with/await/async-for) when a
  name class joins lc.pointers.

- **Landed: ctor-arg / instantiation-arg wave (`thir-wave-next7`,
  2026-07-23, 5 cells + 2 harvests).** The round-7 expr.call cluster-1
  remainder plus the cluster-6 own_iter basket, driven off a fresh
  per-case site clustering (the 117 expr.call sole-blocker cases split
  by RAISE SITE, then per-family verification). (1) Ctor-arg rows:
  flush-less DIRECT positions admit the temp-free move-source slice
  (`[Box(h1)]` -> `std::move(h1)`); `Own[list]` slots admit the bare
  brace list literal (`Summer([1, 2, 3])`, the qualcall row's ctor
  face); tuple LITERALS at ctor tuple slots ride the borrow/value
  tuple builders; a whole value-opt NAME passes bare into the SAME
  Optional slot (argparse builder ctors); `_protocol_union_ctor_arg`
  widened to Optional[protocol] slots + container names
  (`Counter(words)` -> `&(words)`). (2) The borrow-tuple builder's
  pointer-repr Optional elem faces: `None` -> `nullptr`, plain lvalue
  name -> `&(name)` (btuple.elem_optptr); None excluded from the
  rvalue-unwrap like the AST. (3) Consuming instantiation args: the
  stub-method consuming-__iter__ wrap extracted to
  `_consuming_iter_wrap` and consumed by the instantiation arm's
  last-use branch (`dict(pairs)` -> `own_iter(std::move(pairs))`);
  dict-view rvalues render inline (`dict(m.items())` /
  `list(d.keys())`, call.inst_view_arg). (4) Callee-kind specials:
  TRY_PARSE renders `EnumUtil<E>::try_parse(s)`; a @native
  cpp_return_type callee routes at free-call positions with the
  composed `static_cast<declared>(::sym(args))` template
  (ret_cast_ok, other positions keep rejecting). (5) The dcbp union
  const-wrap threaded through the user-method arg loop
  (`z.names(other)` -> `ptr_variant_to_const<...>(other)`);
  print-arg FieldAccess form gains the storage tuple field
  (TuplePrinter). DIVERGENCE fixed en route: the field print form
  misfired on NARROWED value-Optional container fields (AST prints
  print_optional_val over the WHOLE field) -- the arm now declines
  declared-Optional fields; the corpus byte-diff caught it the moment
  the tuple row unblocked the witness body. PARKED with verified
  blockers: dict literals into ctor slots (spelled ordered_map render,
  pinned); the sumprod float-literal Iterable args (wrong-arm
  interlock -- needs the protocol-slot ArgTemp hoist, reverted); the
  overload_set per-stub emission families (ret_mismatch /
  db_isinstance -- per-stub dead-branch machinery); list[tuple]
  storage-call decls (the `_container_scalar_read` axis); the
  gen-method union-param narrowed/Fn residue; void-body lambda args;
  protocol-receiver subscripts; isinstance cond.call narrowing folds.

- **Landed: isinstance families + arg-ladder clean tiers wave
  (`thir-wave-next8`, 2026-07-24, 6 cells + harvests).** The round-8
  design-track queue consumed as ordinary cells. (1) F6 wrapper-union
  isinstance narrowing (`_eligible_wrapper_union` incl. AliasRef
  registry resolution; `.value` variant access via
  `_narrow_variant_cpp`; the DECLARED-union consumer keying via
  `lc.narrow.subject_union` -- gen_print/gen_subscript read
  ctx.var_types, so a narrowed alias prints through the `__str__`
  visitor / subscripts through the fi-fallback raw `recv[idx]`);
  narrowed-alias setitem + qualcall wrapper-slot args + the
  wrapper-elem REF_ALIAS decl (local subjects only). (2) F1 folded
  isinstance (`if (true)/(false)` + the shadowing re-extraction from
  the ORIGINAL union; folded-FALSE extracts the CHECKED member) + F2
  readonly-qualified subjects (const-pointee spellings, sync +
  resumable lanes). (3) Arg-ladder clean tiers: iter-rvalue auto
  temps (gen-factory / iter() / dict-view at structural slots, the
  coro-factory temp's sibling, init under ITERABLE use); deref-coerce
  args (inline Ptr deref_check / slot-typed `__deref__()` copy temp);
  Optional[Own] last-use bare moves; native-protocol value args
  (__hash__); readonly empty-container inline binds (the temp arms
  now exclude declared-readonly slots -- dualgen caught the inline
  `take_ro({1, 2, 3})`). (4) Void print-body lambdas
  (THIRPrintChain body, args TEMP-FREE -- an enclosing-statement
  flush would miscompile; sep/end/file + non-print + self-capture
  stay AST) + ctor lambda args + callable-field calls. (5) F4
  dynamic_cast: Ptr / Optional-ptr / readonly subjects on the if-init
  form; THIRDynIsinstanceMulti for the tuple OR-chain + root-class
  identity check; the value-position chain (CONDITION/TRUTHY uses
  excluded -- the negated form's post-if alias is unmirrored); the
  `_ptr_read_derefs` guard closes the double-deref / receiver-arrow
  seam for spelled subjects. (6) Subscript receiver families:
  protocol template-param receivers (the shared checked dunder),
  varargs views with range-PROVEN indexes, Own[container] param
  READS (position-pinned name; writes keep the Own exclusion).
  DIVERGENCES caught en route (corpus + dualgen): the for-each over a
  narrowed alias (AST renders the generic __iter__ loop --
  foreach.narrowed_src reject), the readonly-slot literal temp, the
  const-alias write/elem-decl AST bugs (filed in BUGS.md; THIR
  gate-rejects both), the func_ref_callable `(*this)` receiver.
  PARKED: overload per-stub emission (round-8 decision 9,
  APPROVED-UNCONSUMED -- the next wave's opening deep cell);
  traits_records (a generics-lane field rung); the negated poly cond
  (post-if alias); unproven varargs indexes; F5 constexpr folds
  (design).

- **Landed: overload per-stub + combinator/dataclass rows wave
  (`thir-wave-next9`, 2026-07-24, 4 cells + a mini-cell + harvests).**
  The round-9 approvals consumed. (1) Per-@overload-stub lowering
  (decisions 9+13): the `thir_overload_key` interception seam --
  `(id(impl), id(stub))` ctx key set in both per-stub try/finally
  blocks, a CONSUMING gen_body lookup (nested bodies never inherit
  it), driver/lower_module all-or-nothing seeding -- verified
  ZERO-CHURN standalone by a full-corpus byte-diff before the
  lowering landed; then per-stub `_LowerCtx`/`_Prescan` signature
  overrides (stub param types drive binding classes, stub return
  type the return slots), the shared `build_overload_narrowing` map,
  isinstance-if and match dead-branch folds (THIRFoldedBlock splice;
  match folds burn a `__match_subject` counter slot; a
  folded-terminating True branch truncates enclosing lists and sets
  `THIRFunction.suppress_trailing_comments`), and per-stub return
  coercion (wrong-target strip incl. the Optional-inner KEEP).
  Boundaries: generic_stub (admission-side template test --
  protocol-param stubs carry no type_params), arity, db_compare
  (literal groups emit via the mangled path, which never sets the
  key), narrow_param, partial folds, match guards/sub-patterns all
  keep rejecting. (2) Combinator admission rungs (decision 14; the
  "machinery" refuted -- plain @cpp_template stubs): range /
  nested-combinator / gen-factory rvalues at native Iterable slots,
  and the instantiation ladder's combinator + genexpr rows
  (`list(map(f, xs))` -- inner container names stay bare, no
  own_iter). (3) Dataclass dunder-pair rows (decision 15): the
  fstring wrap-table's readonly-TypeParamRef unwrap; bare member
  reads at SUBSTITUTED protocol slots (whole-optional /
  optional-record / value-container / F1-record fields --
  `::tpy::repr_of(this->label)`); bare field-eq pairs
  (optional-str / optional-record / value-element list+set --
  sema tags no optional_safe_eq for the record pairs). (4) D-tier
  record-borrow args: inline T&-returning calls and checked
  record-element subscripts at record ref slots, the record-getitem
  `auto` protocol temp (BORROW_BIND record-getitem result row).
  (5) The comprehension/borrow-tuple arg mini-cell: a list/set/dict
  comprehension at a plain container ref slot hoists the slot-typed
  `({...})` ArgTemp; a storage F3-tuple FIELD read at a borrow-tuple
  slot wraps `tuple_to_pointer` with the AST's want_const pair (the
  threaded deep-const verdict OR a const-rooted source -- the dualgen
  smoke caught the missing caller-side disjunct). Parked there:
  stdlib/os_fs (the inline comp render), set/set_comp_owned_move (the
  comp-internal ctor temp seam).
  DIVERGENCE-CLASS finds: the AST fold's body-global
  overload_terminated truncation drops post-loop statements (filed
  in BUGS.md -- THIR mirrors byte-identically; fix AST-first);
  record-element list eq turned out pre-existing-routed (pinned).
  REVIEW ROUND (7 specialists, applied autonomously): the terminated
  truncation re-scoped to the function-level list only (the AST's
  _gen_buffered_body break never applies to nested bodies --
  probe-confirmed byte divergence on fold-in-loop-with-trailing
  statements, fixed + smoked); _written_names widened to compound
  statements' own binding targets and walrus targets (the by-ref
  match-capture rebind write-through -- the pre-existing plain-match
  AST flavor is FILED in BUGS.md); the borrow-tuple const verdict
  gained the const-rooted-source disjunct; local imports hoisted;
  boundary pins added (literal sub-pattern, return-mismatch,
  write-guard units, non-F1 repr field).
  PARKED: overload arity increment-3 (2 sole cases) + the
  method.overload_set family tail (re-price now the machinery
  exists); elem-receiver field writes (recursive_record_mutual,
  nested elem-of-elem); enumerate_rvalue (genexpr element lane);
  filter_none (print lane); container_literal / expr.coerce /
  optional_other_nonetype / decl.tuple_literal_shape families
  (verified 3+-way fragmenting -- dropped per doctrine).

- **Wave-next10 -- F5 constexpr + M4c wrapper match + unpack/deref rows
  (`thir-wave-next10`, 2026-07-24, 4 cells + 2 harvests, +14 flips,
  dial 2425 -> 2439/3569).**
  (1) F5 constexpr concept-if (decision 16), probe-first verification
  of the parked inc-1 arm: the committed arm had a fatal missing
  import (BOOL) -- the "green baseline" was a stale-tree artifact, the
  arm had never run. Fixes: negation moved into the render_concept
  hook mirroring gen_truthy_expr's exact pairs (`(!(concept))` wrap
  vs the nullable-single-protocol same_as polarity flip); the hook
  reads the RAW param type (current_func_params), not the
  branch-retyped binding; the nullable-protocol `is not None`
  statement guard (`_get_nullproto_constexpr_guards` -- constexpr
  nullptr_t + deref reads, unmirrored) rejects; protocol-member union
  None-tests reject at the monostate arm (the AST's protocol
  ptr-compare arm precedes it at EVERY position -- the F5 admission
  had newly routed protocol_union_optional into a silent
  holds_alternative divergence, caught by the census byte-diff).
  Census 13/13 IDENTICAL. Inc-2: NativeIterable/Spannable protocol
  params route the begin/end range-for via the container route
  (foreach.native_proto_param); the two still-defers pins flipped to
  routing pins.
  (2) M4c wrapper match (decision 17): switch_union admits value-repr
  non-generic wrapper NAME subjects on the unguarded tier
  (THIRMatch.wrapper_value threads `.value` through the switch head +
  std::get positions); the wrapper member-init decl row
  (`Tree a = Leaf(42);`), wrapper container-literal elements, and the
  free-call wrapper arg rows (same-wrapper bare name; member-name
  typed temp with the _maybe_move mirror -- the AST's value branch has
  NO membership check, so the row keys already_union only).
  (3) Ref-container unpack targets (os.walk-family loop heads): the
  for-head "ref" bind widens from F1 records to reference-family
  containers (type-agnostic emit; the name arm's source gate cannot
  produce container elements). EXPOSED + FENCED a pre-existing latent
  divergence, reachable pre-wave through the name arm with no corpus
  witness: a PENDING-str unpack target at an Own[str] sink -- the
  AST's _is_str_view_source misses the view binding (unpack targets
  absent from its runtime-view bookkeeping) and hoists the owned
  copy+move temp, while THIR's S1 arm inline-converted. Pending-str
  unpack targets now register per-function and the S1 arm rejects
  them (call.arg_unpack_pending_view). Reconciling the AST-side
  bookkeeping (the temp is arguably an accident -- the binding IS a
  view) is a DESIGN item: AST-first snapshot-churning change.
  CLOSED: the AST side was the real defect. `get_resolved_type`'s
  `var_types` short-circuit returned the entry raw, skipping the
  deferred-type resolution the same function does on its other
  path, so the target read as neither view nor owned and every
  view-form predicate keyed on it answered "not a view". Routing
  that short-circuit through `resolve_type()` fixed all four owned
  sinks (three emitted uncompilable C++), and BOTH fences plus the
  `pending_view_unpack_targets` set they fed are deleted. Zero case
  flips -- the shape's corpus witnesses stay blocked on unrelated
  constructs -- so the gain is deleted fences, not dial movement.
  (4) deref_to_target Ptr template row: `_deref_codegen`'s PtrType arm
  is position-uniform (`::tpy::deref_check(x)`), so the coerce joins
  the {0}-template family; the record-wrapper `.__deref__()` flavor
  keeps its dedicated arg row. Unlocked the generics bound-chain
  family (5 flips).
  FLIPS (14): isinstance_protocol(+_cross), iterable_native_
  narrowing_bare, union_recursive(+_bare), isinstance_narrows_to_
  child_proto, span_like_builtin, union_user_ptr_indirect (bonus 3),
  yield_frame_local_borrow, bound_chain_triple(+_hint,quad_hint),
  bound_factory_inference, covariant_custom.
  FRONTIER VERIFIED-AND-DROPPED (3+-way fragmenting): container_
  literal (13: protocol-slot float lists / generic-T Own returns /
  ptr-variant union elements / nested tuple-dict),
  field_write_shape. **The `call.native_arg.record_nonf1` drop verdict
  was WITHDRAWN: the "fragmenting" reading was an artifact of a missing
  container guard, which filed lists/dicts/sets under the record branch
  of `_native_arg_reject`. Those args now tag `call.native_arg.
  container`, and a spy over the family found ZERO record args -- one
  coherent `_is_len_call` frontier (non-name reads into a Sized/Iterable
  slot), NOT three rows. Its own cited example, `len(subscript)`, is a
  container.** PARKED with verified blockers: res.return_type
  (10 async own/borrow returns -- tpy-m2 fix-yield-escape avoid-zone
  overlap), expr.lambda 9 (overload_fn_param family -- deferred with
  the overload tail re-price), os_walk trio (the pending-view design
  item above + method_call rungs), tuple_optional_yield pair
  (optional-element unpack rungs), gen_tuple_yield_ref (generic Box
  element rung), standalone-unpack rungs (optional/rvalue/fixed-int
  targets -- distinct renders each).

- **Wave-next11 -- ctor renderer, async return slots, frame nested defs
  (`thir-wave-next11`, 2026-07-24, 4 cells + 3 harvests, +14 flips,
  dial 2439 -> 2453/3570).**
  (1) lower_constructor threads render_concept (ctor-body concept-ifs
  route; zero corpus witnesses -- every committed ctor constexpr body is
  nullproto-guarded -- so the routing pin is the witness). The
  verification dualgen EXPOSED+FIXED an admitted-but-unwitnessed
  divergence: a STRUCTURAL rvalue at a ctor protocol slot took the
  shared protocol temp row, but the AST ctor loop renders those inline
  (_gen_protocol_arg hands single-required slots to gen_call_arg); the
  ctor gate now admits protocol-slot args only for NAMES and @dynamic
  slots.
  (2) Async Own[F1-record] return slots: the return-type gate was the
  only blocker -- the position-blind value tail already carried the
  frame-slot deref + last-use THIRMove. 3 flips.
  (3) Async borrow-return SELF rung: bare F1-record slots admit and the
  BORROW arm lifts exactly the receiver (`Res* __tpy_async_ret =
  &(__self);`, res.return_self_borrow); alias-name sources keep the
  return.borrow_form fence. The return-await forward and
  suspending-finally shapes route via CFG scaffolding the leaf never
  sees -- 4 flips including chain/with-as/finally.
  (4) Frame nested defs: a resumable-body `def` is a struct MEMBER
  (gen_async scaffolding), so the leaf lowers THIRFrameNestedDef (the
  marker line) with up-front name registration
  (collect_frame_nested_defs); the sync lambda lowering is untouched.
  7 flips.
  FLIPS (14): async_own_return_nocopy, async_own_param_return,
  async_own_return_run;
  async_borrow_return_nocopy/_chain/_finally_suspend, with_as_borrow;
  nested_def in_async_def/_capture/_method, in_generator_capture/
  _method_mut, in_resumable_finally, in_simple_generator.
  FRONTIER (fresh map, verified): comp tuple-unpack heads
  (list_comp_unpack/comp_array_unpack + maybe itertools_basic -- the
  ONE multi-case comp rung; narrowed-optional ternary elements and
  tuple-literal elements are separate rungs) = next wave's opener.
  PARKED: copy(self) async return (1 case, unknown chain);
  resumable:expr.call cluster (await_task/queue/channel = the asyncio
  sink adjacency; bind_generic/delegate = generics lane);
  in_generic_async (chain-walked to iter.user_iterator.name);
  res.local_storage / leaf_field_write / field.result_type
  (per-shape classification lanes, unverified this wave).

- **Wave-next12 -- tuple-unpack borrow-form param sources + return copy
  (`thir-wave-next12`, 2026-07-24, 3 cells + harvests, +8 flips,
  dial 2455 -> 2463/3573; full exec suite 9473 green; 7-specialist review
  applied, 0 Critical).**
  OPENER DISSOLVED: the queued "comp tuple-unpack heads" premise was
  stale -- the comp route already lowers `for k, v in ...` heads
  (comprehensions.py handles unpack); the witness cases were blocked on
  UNRELATED constructs (list_comp_unpack on a `list[String]` owned-string
  element, comp_array_unpack on array-comp unpack + tuple-literal
  elements, itertools_basic on a call iterable). Textbook verify-first
  save.
  (1) Standalone `a, b = p` where `p` is an already-borrow-form tuple
  PARAM (`tuple[T,...]` passes `const std::tuple<T*,...>&`): bind
  NAME_REF (`auto& __tup = p;`) with no tuple_to_pointer lift, mirroring
  the AST's `not is_storage_form_source` name arm. GATE-ONLY -- the
  NAME_REF bind + ref-alias emit already existed (for-each iter-proto
  head). +2 (tuple_rvalue_ref, tuple_param_const_inferred). Two stale
  boundary pins (record-element param "ineligible") updated to the
  now-lifted routing.
  (2) New opt_ptr bind: pointer-repr Optional[F1-record] tuple elements
  bind a plain nullable pointer local (`const T* a = std::get<i>(__tup);`,
  const from `_param_is_const`), registered lc.pointers + declared so the
  None-test / `->` reads ride the existing `T | None` param machinery --
  no new downstream arms; the emitter's default decl arm renders it. +3
  (tuple_optional_param, tuple_rvalue_optional, tuple_optional_param_mutate).
  (3) `return copy(p)` of a plain F1-record intercepted before the
  record-return arm -> THIRReturn(THIRCopy) (`return Point(p);`), mirroring
  the decl/assign copy_record rows (the special-builtin call gate rejects
  copy() in the generic tail). +3 (own_param, own_to_ref_param,
  return_own).
  FLIPS (8): tuple_rvalue_ref, tuple_param_const_inferred,
  tuple_optional_param, tuple_rvalue_optional, tuple_optional_param_mutate,
  own_param, own_to_ref_param, return_own.
  REVIEW: codegen/safety/parity/arch/convention/docs clean; safety fix
  (opt_ptr re-derives the bare Optional via _optional_ptr_borrow so a
  readonly-wrapped target can't crash lowering) + 2 test-coverage pin
  promotions (opt_ptr storage-local boundary, mixed record+scalar order
  byte-identity). Arch reuse-helper suggestion skipped (explicit form
  clearer; linear scan is the idiom).
  PARKED (verified rungs for a future wave): opt_ptr for-each mirror
  (`for a, b in list[tuple[T|None,...]]` -- the storage-source
  optional_to_ptr lift, meatier than the already-borrow param; any
  borrow-LOCAL opt_ptr source rung MUST keep the has_opt_ptr param-only
  gate -- a storage-local `std::optional` element bound as a bare `T*`
  would dangle, UAF); subscript
  tuple source (`_, np = addrs[0]`, tuple_unpack.src_subscript, +1 with a
  value-scalar-tuple rvalue-source widen, kitchen-sink case may not flip);
  copy(record) in append/setitem positions (unprobed). AVOIDED per queue:
  overload-fold arms (bug #2 in flight). NOT clean this wave (fragment /
  meaty / design): is-not-None non-record optionals (net ~0 -- bodies
  reject downstream), aug_assign (atomic/subscript/field split), genexpr
  (make_generator vs list/set-materialize lane), sig.special_callable
  (consuming-method model), nesteddef.self_capture.

- **Wave-next13 -- resumable branch decls, leaf matches, marker-call
  capture positions, overload rows (`design-round11-corrections`,
  2026-07-24/25, 8 cells + 5 harvests, 29 markers deleted, dial 2463 -> 2493 (corpus 3573 -> 3578 after merging master's 4 new marked cases, so the +30 dial delta includes one case that became
  unmarked-clean outside this branch's deletions)).**
  (1) Branch-nested frame decls: the frame_slot emplace and borrow-tuple
  write families route inside if/match/try arms -- every frame write is
  position-blind, so the branch arm reuses the leaf arm's lowerings
  (extracted as `_lower_frame_slot_write` /
  `_lower_borrow_tuple_frame_write`). Coro-handle slots and the pointer
  families keep their named reject; the pass-1 registration walk now
  descends try bodies (an except-only leaf try reaches lowering).
  The frame_slot brace-init prefix reads the RESOLVED frame local type
  with the sema branch-decl override applied (the AST's `var_types`
  chain), not the first decl's expression type. +5.
  (2) Non-suspending leaf matches fall through to the sync match tiers
  (a suspending match is a MatchDispatch terminator, so a leaf match is
  suspension-free by construction) -- the landed leaf_try_except mirror.
  +3.
  (3) Tuple-result ternaries lower, propagating their arms' form so a
  storage arm still rejects at the lifting sinks. +2.
  (4) Marker-call result POSITIONS with no typed value slot: the `with`
  manager, the for-head iterable, and a discarded container statement;
  a CALL source under `raise` lowers in receiver position. +4.
  (5) Overload rows (decision 20 + the chain it opened): short stubs whose
  omitted params need no prologue local; the real
  missing_params/impl_defaults into `build_overload_narrowing`; Optional
  impl params shadowed by a stub's concrete type (multi-member unions
  still reject); the `plain` family; genuine (non-mangled, non-template)
  method stub sets at the CALL SITE; the str-literal `param_view_t("..")`
  pin mirrored per-arg at free AND method call sites. `_LowerCtx.params`
  now carries the SIGNATURE params a body is lowered against, so a
  stub-narrowed read no longer derefs the impl's Optional spelling. +8
  (the kwarg trio came free with the call-site row).
  (6) Own[T] ELEMENT-SLOT args (method_call M1a): a dict/set LITERAL
  into an `Own[container]` slot renders off its own resolved type (the
  same coincidence the list-literal row rested on -- the shape check
  pins the literal's family to the peeled slot's), and a scalar VALUE
  into a value-repr `Optional[scalar]` element slot passes bare
  (`xs.append(Int32(1))` -> `push_back(1)`, std::optional converts).
  +2. The ctor-side dict-literal boundary pin became a routing pin.
  Residue: `Own[str]` enum-name sources and the USER-record `Own[T]`
  slot (tplib ArrayList.append) are separate gates, unbuilt.
  (7) VALUE-TUPLE container elements (+1): a `list[tuple[str, Int32]]`
  receiver joins the method-receiver family whitelist, and the
  subscript arm admits a value-tuple element read in the standalone
  unpack-SOURCE position (`a, b = addrs[0]` -> the existing
  `auto __tup_N = ::tpy::__getitem__(addrs, 0);` capture) with
  `_tuple_unpack_source` accepting the subscript that feeds it. This
  is the round-11 rung-2 park, unblocked: the element read -- not the
  source gate -- was the real blocker. Value-POSITION tuple element
  reads keep rejecting; the fallback-machinery test re-keyed onto a
  reference-element tuple.
  (8) STRUCTURAL-PROTOCOL UNION method args (method_call M1b, +4): a
  NAME into a slot that is a union of structural protocols
  (`ArrayList.extend`'s `Spannable[T] | Iterable[Own[T]]`) passes bare
  -- the C++ method is a template whose concept picks the branch, so
  there is no variant to lift and no span conversion. The `&(b)` that
  blocked this came from a CTOR-position face
  (`_protocol_union_ctor_arg` == 'addr', where the Spannable overload
  really does bind a pointer); it is now scoped to non-method args.
  Dynamic-protocol and non-protocol union members stay out, and the
  SOURCE families are restricted to container/span/record (defensive
  today -- sema type-errors an off-family source at these slots).
  LOAD-BEARING COINCIDENCE, pinned: a movable last-use container
  renders bare on both paths only because the AST's consuming
  `own_iter(std::move(..))` rewrite keys on a BARE `Iterable[Own[T]]`
  ptype and misses a UnionType -- widening that keying means this row
  needs the same rewrite.
  MERGE (master 06ce56980, narrowed-union subscript keying): its THIR
  hunk and cell 7's subscript admission are DISJOINT (the new arm
  keys on the declared receiver family, so a narrowed-union receiver
  falls through to the narrowed-member arm), and master's 4 new
  marked cases were byte-diffed by the overlay in the post-merge run
  (9510 passed, full exec).
  DEBUGGING TRAIL (two refuted hypotheses, both cheap to re-walk):
  the union LIFT (`_arg_ptr_union_slot`) is never consulted for this
  slot, and neither optptr slot predicate can match a None-less union
  -- the spy on `_lower_call_arg` (arg node + form) is what localized
  the real face. Reach for that spy first next time.
  FILED (AST-side, pre-existing): a container literal first declared in a
  TRY body of a resumable types its frame slot from one arm's fixed-size
  Array while the writes emplace a vector (uncompilable) -- BUGS.md.
  FIXED in-flight: `_stub_template_param` read `.inner` off an OwnType
  (crash on an `Own[P]` stub param); the two copies are now one helper.
  PARKED (verified, flips nothing alone): the subscript tuple-unpack
  source (`_, np = addrs[0]`) needs the subscript arm to admit a
  VALUE-TUPLE element read first (`subscript.elem.tuple`); the
  `for_each:tuple.ref_target` pair (counter, dict_readonly_view_read);
  ptr_field_method_call / for_generic_iter_protocol (a TypeParamRef
  marker result at a T-SLOT sink -- the M5 return-slot duality, which
  fails THIR validation if admitted).

- **Wave-next14 -- str-family field reads at the return/yield sinks
  (`thir-copy-row-unify`, 2026-07-25, 4 cells + 3 harvests, +9 flips,
  dial 2494 -> 2503/3579; full exec suite 9527 green; two review rounds,
  7 + 3 specialists, 1 Critical -- a wrong claim in a test comment of
  mine -- and 3 Warnings, all applied).**
  SELECTION: the 9 `field.result_type` sole-blocker cases (5 resumable +
  4 body) split on inspection into THREE shapes, not one -- 5 sharing a
  str-family field read at a str/view return-or-yield sink, 3 container
  field reads (arg / for-head iterable / membership needle), 1 narrowing
  edge (`b.value + 1` after an invalidating call). Took the 5; the other
  two groups stay open.
  (1) A str-family FIELD inner under a view-TARGET coerce: `return
  self.s` at a `StrView` slot arrives as a str_to_strview coerce, and the
  coerce threaded `field_owned_str_ok` for its MATERIALIZING disposition
  only, so the identity view-target form rejected at the field result
  ladder. Threaded for the view-target pair, keyed on the DECLARED field
  type (`_field_decl_type`). New face coerce.str_field_view. +2
  (return_view_through_method, overload_method_nested_generic_ctor).
  (2) The same read at both RESUMABLE str sinks -- the async return
  scaffolding's `__tpy_async_ret` decl and the value-yield tail -- via
  the `_str_field_value_read` + field_prechecked precheck the SYNC return
  tail already ran. New faces res.return_str_field / res.yield_str_field.
  +3 (with_nested, gen_union_param_readonly, narrowed_str_field_yield).
  HARVEST BONUS: async_borrowed_rvalue_arg (+1, unprobed).
  BROKEN AST ORACLE FOUND (filed in BUGS.md): a NARROWED `str | None`
  field at a VIEW return slot emits a bare `return this->opt;` --
  `std::optional<std::string>` does not convert to `std::string_view`,
  g++-confirmed. THIR would emit the correct `(*this->opt)`, so the
  declared-type keying in (1) is LOAD-BEARING, not tidiness: it keeps the
  shape unrouted rather than silently diverging-and-fixing. The sibling
  sinks (sync return, yield) render the same shape correctly, so the
  defect is specific to the view-return arm.
  STALE BOUNDARY PIN: `test_strview_field_return_still_defers`
  (test_thir_wave_valrecunion.py) asserted exactly the shape cell 1
  routes. It was written to stop the SPANLIKE field branch claiming the
  str case -- that invariant is still real, so the pin was re-pointed to
  assert it positively (coerce.str_field_view fires, span face does not,
  byte-identical) rather than deleted. NB it did not surface in a grep of
  the obvious vocabulary (str_to_strview / field_owned_str / str_field):
  wave-named test files carry their own naming.
  SPLIT WORTH REMEMBERING: a StrView member at an OWNED-str slot needs
  the view->owned copy, so sema wraps a MATERIALIZING coerce -- it never
  reaches the bare-field arms. Only same-family (no-conversion) reads do.
  (3) CONTAINER field bare reads (+2). The container-field trio's
  predicate question -- does a container field read bare at these
  positions? -- is YES at two of the three: the RESUMABLE for-head
  iterable (`(__self.nodes).begin()`; the sync for-head has its own
  pre-existing arm, foreach.container_field, so only the resumable one
  reaches the ladder) and the `std::ranges::contains` haystack
  (`item in self.xs`). The first is a ladder arm scoped to ITERABLE; the
  second is a precheck at the ranges-contains arm rather than a RECEIVER
  widening, which would also re-route method receivers that arm says
  nothing about. New predicate _plain_container_read; faces
  field.container_iterable / binop.membership_container_field.
  DECLARED-type keyed, and an EXISTING pin caught the expr-typed version
  over-admitting: test_optional_container_field_rejects fired on a
  narrowed `Optional[list]` field, a shape no corpus case covers, so the
  byte-diff could not have. Same rule as cell 1, found the hard way twice.
  Also hardened _field_decl_type to answer None for a non-NAME receiver
  instead of raising AttributeError -- its contract assumes the receiver
  gate ran first, and a caller that gets the order wrong should reject.
  THIRD case of the trio DROPPED: a container field as an AWAIT arg
  lowers through the resumable await-args path, and the sync call form
  does not route either -- it sits behind expr.call.
  (4) Leaf try/FINALLY in a resumable when nothing crosses it (+1). The
  finally tier rejected wholesale as "interlocks the finally-frame stack
  with the async return scaffolding". Reading the oracle shows that
  interlock only materializes when a control transfer CROSSES the
  finally; without one the emit is the plain sync duplicated-body try --
  the same reasoning that already admits the except-only leaf try. Admit
  when no return/break/continue appears inside (_leaf_finally_crossing,
  walking sub_bodies()). Conservative about break: it does not track
  which loop a break binds to. Face res.leaf_try_finally. The other three
  res.leaf_try cases all return from inside the try -- that IS the
  genuine named rung, unchanged.
  TWO MORE STALE PINS re-pointed (four this wave): the mid-frame leaf
  finally now routes, and a frame-slot decl inside such a try now reaches
  the branch arm. REVIEW CAUGHT ME OVERCLAIMING on the second: I wrote
  that it emits the BUGS.md try-body broken slot type; it does not (slot
  and write agree). That defect needs a try/EXCEPT pair declaring one
  name with differently-shaped literals per arm. Inferred from a memory
  note instead of dumping the C++ -- the exact habit the wave doctrine
  warns about, applied to my own comment rather than a target.
  OPEN residue on the field.result_type tag:
  warn_expr_narrowing_invalidated_by_call (a post-invalidation Optional
  field read, its own narrowing lane), and the await-arg container field.
  DROPPED AFTER VERIFYING (do not re-price without new information):
  res.narrowed_resume (3) is the explicitly-parked list in
  _entry_narrowings_reject -- the polymorphic-self dynamic_cast family
  and Optional narrowing, each needing its own render mirror; and
  list_comp (5), which wave-next12 already documented as fragmented.

- **Wave-next15 -- the expr.call partition opens
  (`thir-copy-row-unify`, 2026-07-25, 7 cells + 4 harvests, +16 flips,
  dial 2503 -> 2519/3579; full exec suite 9547 green; codegen + safety
  review clean, 0 findings).**
  Built entirely from design round 12's pre-approved bundle. The premise
  that made it possible: `expr.call` is raised from THIRTY distinct
  sites, so the "65-case unfragmentable elephant" was a tag artifact --
  63 of 64 cases reject at exactly ONE gate. Every cell below turned out
  to be a ROUTING gap with the render machinery already built.
  (1) Bare FIELD read at a ctor REF slot whose referent IS the field's
  declared type (+4: generic_parent_protocol{,_concrete},
  generic_func_multi, generic_int_param_method_return). New predicate
  `_field_read_ref_ctor_arg`, face ctor.field_read_ref_arg. The rule is
  slot/field type EQUALITY, not a family list, which is why one row
  covers containers and open-T alike. The gate alone was not enough --
  the arg loop lowers through the generic VALUE position where the
  ladder re-asks the result-type question -- so the shape lowers
  directly, scoped to the ctor position the gate validated.
  (2) Dict/set literals at a ctor container slot (+2 of 4:
  getattr_basic, narrow_via_local; the other two chain-walk into the
  method-call lane). `_container_literal_arg` was list-only by
  docstring; the ctor position emits dict/set INLINE exactly like its
  stub-method twin. What separates a ctor arg from a FREE-call arg here
  is the temp hoist, not the container family.
  (3) Arg-less `bytearray()` at the type-ctor arm (+2). ARG-LESS is
  evidence, not caution: `bytearray(n)` / `bytearray(b"..")` never reach
  this arm (they resolve as plain calls). An arg rule shipped first and
  was found unreachable -- removed and pinned.
  (4) Vararg pack at a GENERIC callee slot (+2 probed, +2 harvest
  bonus). `_lower_vararg_pack` already did the array temp, the ref/value
  split and the readonly const override; the generic arg loop simply
  never called it. Face call.generic_vararg_pack.
  (5) Open-T method results: `_tparam_value(ret)` in the marker-call
  gate (the record-method sibling has had it since it was written) plus
  `Own[TypeParamRef]` in `_borrow_legal_return` (+2). The second was a
  CRASH not a fallback -- an admitted body failed the validator walk.
  M5 IS DE-LISTED FROM THE DESIGN QUEUE: it was two rows, never a
  slot-model decision, and the "prerequisite for several parked rungs"
  claim was false.
  MEMO CORRECTIONS FOUND WHILE BUILDING (the memos were right about
  structure, wrong on two prices): the list_comp "cheapest fragment"
  (list_comp_unpack) is NOT cheap -- its element slot is `String`, i.e.
  the parked 129-call-site family; and the itertools call-iterable
  fragment did not yield on drilling (the iterable is not the node kind
  assumed) -- reverted rather than chased.
  STALE PIN re-pointed (a fifth this session):
  test_dict_literal_ctor_arg_stays_ast -> the dict literal must render
  SPELLED and INLINE, asserted positively with the exact render text.
  A PREDICTED BOUNDARY THAT DOES NOT EXIST, recorded so it is not
  re-asserted: "bare open-T must keep failing `_borrow_legal_return`" is
  false -- it was already legal via the not-a-value-type row, since
  TypeParamRef.is_value_type() is False while OwnType(T) reports True.
  Own[union] IS a real boundary for exactly that reason.
  (6) Narrowing `assert isinstance` in a resumable FLAT BB (+1,
  gen_while_assert_narrow_suspend). The flat walk rejected because only
  the compound-body walk mirrored the alias _gen_assert emits inline --
  it would otherwise have silently DROPPED it. Append it via
  _append_assert_narrow. The emitted ALIAS is BB-local, and the oracle
  says why: the CFG flows the assert's then_type_facts into every
  successor's entry_narrowings, and each resume case re-emits
  `const auto& __a = std::get<..>(a);`. Carries the frame-field
  alias-collision fence its sibling arms have. Face
  res.flat_assert_narrow.
  CORRECTION -- READ THIS BEFORE WIDENING THIS ARM (review Critical, found
  independently by safety AND codegen, fixed in-branch): a BB-local ALIAS
  does NOT mean "no scope guard". The arm MUST snapshot `lc.narrow` and
  record `declared[var]` through `postif_saved` BEFORE calling
  `_append_assert_narrow`, which mutates both IN PLACE with no restore of
  its own. The BB driver's `saved_narrow = lc.narrow` holds a REFERENCE,
  so without the snapshot its restore is a no-op and the narrowing leaks
  into every later BB -- emitting an out-of-scope `__a` from a sibling
  branch. The driver's own snapshot does not cover it: it sits inside
  `if env:`, and the asserting BB's entry env is empty by construction.
  `_apply_leaf_post_if` had always snapshotted for exactly this reason.
  COST of the fix: a re-assert after a suspension now falls back (the BB
  restore pops the persistent-narrowing fact `_reassert_bump_info` reads). A SIXTH stale pin
  (test_narrowing_assert_leaf_defers) re-pointed: it guarded that the
  alias must not go MISSING, which appending satisfies more directly
  than rejecting did.
  DESIGN FORK FOUND, PARKED (do not build without a decision): the
  stub-ret `_f1_record` row (list_pop_ref_type). The memo said "key it on
  the sink"; on drilling, the sink flag reaching that gate is
  `storage_ret_ok = result_use is STORAGE`, and the witness arrives with
  result_use = **BORROW_BIND** -- the very sink the memo warns aliases a
  temp -- while the oracle still emits an owned copy (`Box second =
  ::tpy::pop_back(heap);`) because the DECL materializes. No flag
  currently threaded to that gate distinguishes "owned decl that
  materializes" from "borrow binding that would alias". Fencing it needs
  a NEW sink signal threaded through, which is a design decision, not an
  arm. Everything needed to resume is here.
  STILL OPEN from the bundle (no decision needed): drops A residue
  (self/dynamic_cast render mirror, 2) and B (leaf_try return-crossing,
  3 -- the suspending half already routes; the missing half is the
  sync-rendered chain arm at statements.py:4234-4270).

- **WAVE-NEXT16 -- the round-13 approved queue, built end to end
  (`design-round13-corrections`, 2026-07-25/26, 11 cells + 3 harvests,
  +40 flips, dial 2522 -> 2562/3585; full exec suite 9591 green with
  every case rebuilt and run).**
  Built from design round 13's four standing approvals (decisions 24-27)
  plus three rows the grind found on the way.
  (1) **Container METHOD receivers, element axis** (decision 26 G1): a
  method receiver renders bare whatever its element is, so the element
  families the READ-shaped predicates must exclude (open `T`, unit,
  `Callable`, non-wrapper union) get their own front,
  `_container_method_elem`, consumed only by the method-receiver
  classification. The matching insert-arg rows already existed as
  helpers (`_none_unit_arg`, `_tparam_name_pass_arg`,
  `_union_member_ctor_rvalue`) -- they had simply never been wired into
  `_container_method_arg_ok`.
  (2) **The decl storage sink** (decision 24, memo XXXI's 7 measured
  flips): `storage_call` generalised from `_native_record_rvalue_call_shape`
  (@native free calls only) to any F1-record / owned-container rvalue
  call or record-returning dunder binop, keeping the existing
  reassigned/hoisted/move_through + `is_rvalue_source` guards; plus the
  generic value-record decl slot and the reference-element span slot.
  (3) **Generic free-call args** (decision 26, K1): the TypeParamRef
  branch gained value-tuple pass-through, the container-literal ref-slot
  temp and the exact-type F1-record rvalue temp.
  (4) **Receiver chains** (decision 26 G2/G5): a container-returning
  inner call composes as a receiver, a field off a call / element result
  is an admitted receiver, and an owned rvalue result lands in the
  borrow/storage value sinks. THE PARKED DESIGN FORK FROM WAVE-NEXT15 IS
  RESOLVED and needed no new sink signal: the discriminator for
  `Box second = ::tpy::pop_back(heap);` is the CALLEE's return
  convention (`is_rvalue_source`), not the consumer's sink flag -- a
  borrow-returning stub still rejects, which is exactly the aliasing
  case the memo feared.
  (5) **`tpy.String` in the str slice** (decision 25, memo XXXV): the
  docstring blocker ("String params spell `const std::string&`, a shape
  the param emit does not reproduce") was measured false -- that
  signature comes from the SKELETON emitter, so no body arm renders it.
  Four rows, and the O2 String-ctor scalar-arg row on top.
  (6) **Nested defs capturing the receiver**: a captured `self` spells
  `this` in the capture list and the lowering scope keeps the receiver
  alive for exactly that case.
  (7) **Three small rows the grind found on the way**: a Callable-typed
  binding returns bare (`return f;` -- the plain-name twin of the
  closure-name arm), a span NAME passes bare into the same span slot,
  and an `Array` result joins the rvalue-call decl families.
  (8) **Ptr-variant union returns**: the variant holds POINTERS, so a
  member-typed record binding returns its address (`return &(d);`) and
  an isinstance-narrowed subject returns the extraction alias's
  (`return &(__pet);`). This is the RETURN half of the narrowing fence
  below; the for-loop / container-element binding half is still open.
  THREE RENDER SPLITS THE HARVEST CAUGHT (each was a real divergence, not
  a pin artifact): a BORROW-returning dunder binop aliases an operand
  (`const Acc& c = ((a) + (b));`), a same-union NAME into a union element
  slot lifts through `::tpy::to_value_variant`, and the span decl slot
  must not swallow nested spans.
  ONE LATENT DIVERGENCE FOUND BY DUALGEN AND FENCED: union isinstance
  narrowing keyed `std::get<T*>` on the union TYPE while codegen keys it
  on the BINDING (`ctx.ptr_variant_locals` -- params and pointer-variant
  local decls only). A for-loop element over `list[A | B]` therefore
  rendered a pointer variant where the AST reads the value variant. No
  corpus case witnessed it; cell 1's union-element admission would have
  made it reachable. THIR now tracks the binding set and REJECTS the
  bindings it cannot classify -- the value-variant render for those
  bindings is a follow-up row, not a fence.
  THE SAME FOLD SURVIVED AT TWO MORE SITES, caught by the readiness
  retrospective's second opinion and fixed here: both union MATCH tiers
  (`match.py`'s unguarded and guarded arms) re-derived `is_ptr_variant`
  from the union TYPE for NAME subjects, with a comment asserting that
  "the admitted bare names keep the type-level fold" -- exactly the claim
  dualgen had just refuted. That one was not latent-by-luck: a for-each
  element match over `list[A | B]` ROUTED and emitted `*std::get<N>` where
  the AST emits `std::get<N>`, on master too. Both tiers now consult the
  binding set through the same helpers. THE GENERALIZABLE DEFECT CLASS,
  worth an audit of its own: THIR deriving a BINDING fact from a TYPE
  where codegen keys on a binding SET.
  STALE PINS re-pointed (SIX): the record-element container decl pin (its
  "stays AST" was a gate statement, not a render split -- the shape is
  byte-identical when routed), the generator-method nested-def pin (that
  nested def is not a lambda at all; it becomes a frame member), three
  "String stays AST" pins (ctor param, field-over-call read, with-target
  -- all three byte-identical under dualgen), and the member-name union
  return pin, whose own comment already quoted the `&(d)` render it was
  guarding against.

- **WAVE-17 -- design round 14's decisions 28 + 29, built end to end
  (`thir-wave17-queue`, 2026-07-26, 8 cells + 2 harvests, +32 flips,
  dial 2562 -> 2594/3585).**
  Round 14 was the first round whose queue was priced from VERIFICATION
  rather than tags, and the pricing mostly held: the four verified tracks
  landed, and the two it had NOT verified to the same depth (drop B, and
  drop A's second half) turned out to be scaffolding rather than arms.
  Cells, in build order:
  (1) NESTED-CONTAINER ELEMENT LVALUES in the two native sinks that
  consume a whole container -- `::tpy::__len__(::tpy::__getitem__(groups,
  "a"))` and `ListPrinter(...)`, plus the field-off-element form
  (`len(g.rows[0].cells)`). Both sinks PRECHECK the element read, because
  the value-position element gate rejects a container result; what
  licenses the precheck is the REF_ALIAS shape check, which excludes
  exactly what the precheck would drop (unproven Optional element, slice,
  unroutable index). That is why the len row keys on `_is_len_call`, not
  on the bare `_is_len_native` symbol its field twin uses.
  (2) GENERIC CALLEES THAT ARE OVERLOAD GROUPS. The gate rejected any
  generic callee with more than one registered stub on the premise that
  "overload groups literal-mangle the callee"; the oracles refute it
  (`apply<int32_t>(f1, 5)`). What the group changes is WHICH stub supplies
  the template args -- and the ARITY depends on it, so `fis[0]` spelled
  the wrong list. The generic arg tail also never carried the callable
  family, so every `Fn[...]`-slot call fell back one step later. THE
  BIGGEST HARVEST OF THE WAVE: 12 cases beyond the 5 probed.
  (3) VALUE-REPR `Optional[scalar]` RESUMABLE FRAME LOCALS. Pass 1
  pre-registers every frame field in `declared`, so the decl arm read as a
  reassign and never registered the binding; and the plain frame-field
  write lowered its init with no whole-optional admission. DUAL GENERATION
  FOUND A PRE-EXISTING MISCOMPILE (BUGS.md): the AST renders a value-opt
  name's truthiness in a resumable as the bare `if (v)` -- has_value alone
  -- where the sync path emits `::tpy::is_truthy(v)`. `g(0)` takes the
  branch under TPy and skips it under CPython. It predates this cell (the
  param form already routed), so THIR REJECTS the shape rather than mirror
  a miscompile.
  (4) FRAME-EMPLACE TUPLE ELEMENTS + MATCH KEYWORD CAPTURES + the
  value-tuple match hoist. Dualgen caught the boundary the code comment
  did not name: a dict VALUE keeps the `tuple_to_storage` wrap even inside
  a frame, so the bare form keys on the same `retype_scalars` axis that
  already splits list from dict/set elements. The capture row admits
  records / containers / value tuples (all plain `auto&` aliases) but the
  POINTER-HOISTED capture must reject -- and that check has to run at
  LOWERING, because the hoist registers `lc.pointers` only after the arm
  gate has seen a pointer snapshot.
  (5) THREE MISSING METHOD-CALL ARG ROWS: the callable family beyond
  lambdas, the args of a USER-deref chain (whose kind `_marker_call_kind`
  cannot name -- it rejects every deref-marked call by construction), and
  `copy(p)` into an `Own[record]` slot in both the record-method and
  static-method faces.
  (6) `isinstance(self, Sub)` IN SYNC METHOD BODIES -- NO FLIPS, and worth
  recording why. Both self exclusions came out and the cast spelling
  (`this` / `&__self`, const under `@readonly`) plus the narrowed read
  (`(*__self_ptr)`, which flips the access from `this->` to `.`) are
  mirrored and byte-identical. But the one corpus case is now blocked
  SOLELY on the assert-position form, whose persistent `const Dog& __self
  = *dynamic_cast<...>(this);` is a SECOND alias maker, and the resumable
  case needs the `__self_narrowed` rename. The memo priced "both self
  gates = 3 flips"; the gates were necessary and not sufficient.
  (7) OPEN-T ITERABLE FIELD in a for-head. THIR had no type-param-bounds
  threading on the for-head route at all; the bound now reaches it the way
  the AST reads it. A NativeIterable / Spannable bound keeps rejecting
  (the begin/end peephole), as do resumable bodies.
  VERIFIED AND PARKED (see TODO.md's wave-17 residue entry): the leaf
  try/finally crossed by a return is NOT arm-widening -- the AST renders a
  `__fin_ran_N` flag, a numbered eager-capture temp and an INLINED finally
  body before the return. An experiment that simply dropped the crossing
  check produced exactly that divergence, which is the cheapest possible
  proof that the rung is scaffolding.

### Error-return + module-init-write wave (2026-07-27): 2264 -> 2099 fallback units

The first wave steered by FALLBACK UNITS rather than case flips, and the two
metrics disagreed sharply in a way worth recording: routing the
`@error_return` statement shapes cleared 113 first-reject units but made only
TWO cases fully clean, because a case flips only when its LAST body routes.
Units move per body; flips move per case. Steer by both, and never price a
construct's flips from its first-reject count.

**The @error_return statement sites.** The three statement-handled positions
(pass-through return, bind, discard) admitted a bare `TpyCall` only, so every
method or static callee fell its whole body back. The AST render is uniform --
`_gen_error_return_call` sets `error_return_stmt_handled` and calls
`gen_expr`, and that flag suppresses the expression-level unwrap for a method
call exactly as for a free one -- so the mirror is pure admission widening,
position-scoped to `error_return_raw` through `_plain_method_fi_ok`,
`_marker_call_kind` and `_method_call_arg_ok`. Expression position keeps
rejecting: there the render is the `__er_N` statement expression. Three
sibling rows came with it -- the receiver added to the nested-call gate (a
method call renders its receiver BEFORE its args, so it consumes the
statement-handled flag the same way), the str/bytes bind-predecl families, and
the rebind-slot pointer reseat (`name = &*(__slot_N = <unwrap>);`).

**The module-init write faces.** `_lower_global_slot_write` carried ONE of the
five module-scope faces of `_gen_pointer_local_rebind`. The other four (slot
reuse on a later rvalue write, a `None` source, a pointer-slot-global source,
any rvalue source rather than the two admitted shapes) plus the
BORROW-returning ptr-Optional pass-through, the annotation-only global decl,
the `native_global(...)` binding, imported-global reads, `Char` globals and
the module-scope loop-variable shadow took the top-level component from 141
to 83.

**Two more consumers of the pointer-slot-global deref rule.** D2's lesson --
"a rule firing at every value position needs its opt-out list ENUMERATED" --
recurred twice as soon as the new write faces made those top levels reachable:
`g is None` compared the DEREF where the oracle compares the slot pointer, and
a ptr-repr Optional global in truthy position derefed for the same reason
(`_truthiness_mode` returns None for a pointer-repr Optional, so the IS_TRUTHY
deref-strip never saw it). Both were caught by byte-identity pins on the newly
routed cases, not by reasoning. Consumers five and six.

**The native-global spelling.** Routing the `native_global(...)` decl made its
READS reachable at module scope, where they emitted the bare name against an
oracle that spells `::`-qualified. A function body already got the split
spelling; module init did not seed it. The pin caught it -- routing a DECL can
make an unrelated READ reachable, so a new decl arm needs a byte-identity pin
that exercises a read.

**Deletion-relevant:** the imported-global seeding is now shared
(`_seed_imported_globals`), so the function-body and module-init entries
cannot drift on which imported globals are readable or how they spell.

### Field-write + containers wave (2026-07-27): 2104 -> 2045 fallback units

**Unbound-self base-class field access.** `BaseN.field` inside a descendant
method is `_gen_field_access`'s only receiver-INDEPENDENT arm: the syntactic
class-name receiver has no value type, so the parent spelling IS the render.
That is why it gets its own admission predicate rather than a row inside
`_field_receiver_ok` -- that predicate has ~70 callers and most of them read
the receiver binding, so widening it would have opened 70 gates to a receiver
no lowering site could lower. Seven enumerated admission sites instead.

**`copy()` is three renders, not one.** The instinct "peel the copy" is wrong
at two of the three: a plain record NAME copy-CONSTRUCTS (`T(x)`), only a
record CONSTRUCTOR argument and a pointer-repr Optional argument are
identities. Reading `_gen_copy_expr` before writing the arm is what caught it.

**Routing exposes the neighbours.** Three defects surfaced the moment
previously-fallback bodies started emitting: `deref_check` receiving a
pointer-slot global with the value-position deref still attached (caught by
the corpus byte-diff), the optrec arm claiming whole-Optional sources that
belong to the lifting tail (caught by the THIR validator), and a whole-Optional
field read tagged VALUE where the tail needed to know it was member STORAGE.
None were reachable before; all three are the same shape of bug -- an arm whose
guard was looser than its gate.

**A restriction must ADD, not REPLACE.** The ref-element tuple container arm
first returned its own verdict when `has_ref_elements` held, which turned the
previous fall-through into a hard reject and regressed five resumable
frame-tuple pins. The arm adds a row; the storage-direct rules still apply
when it does not fire.

**Four "stays AST" pins were scope markers, not render splits.** The two
`copy()` field-write pins, the Span-field ctor pin and the value-opt inner
param pin each carried a stated reason; three of the four reasons were a
restatement of the old gate's own rule ("outside the exact same-type pin",
"the slice does not open this"), which is a migration state, not a
correctness bar. The fourth (a lifetime concern on the Span field) is real
but identical on both paths and enforced by BorrowTracker in sema, before
either emitter runs. Read the reason before widening past a pin -- and
convert the pin rather than deleting it, so the shape keeps a guard.

Only three of the four were found by reading; the fourth surfaced as a unit
failure in the harvest run. Grepping the THIR test files for pins on the
boundary BEFORE building the arm would have found it -- that step is in the
wave procedure and skipping it cost a round trip.

**THIR as an oracle for AST defects -- the value units cannot count.** Two
pre-existing LOUD AST miscompiles surfaced only because this wave widened
admission: an unbound-self read of a `@native`-RENAMED base field spells the
unrenamed member (the early return predates its own rename override), and
printing a NARROWED value-repr `Optional[scalar]` GLOBAL emits the bare
optional. Neither has a corpus witness; both fail the C++ build. In both,
THIR is already RIGHT and the AST is not.

**The policy that governs those two, written down because this wave decided
it ad hoc twice:** when the broken shape falls INSIDE an arm the wave admits,
REJECT it (mirroring output that does not compile buys nothing, and the
rejection is a clean un-reject point once the AST is fixed) -- that is the
`@native`-rename case. When it merely becomes more REACHABLE because other
bodies now route, FILE it and leave the arm alone -- that is the value-opt
global print. Reject narrowly, file broadly.

**Per-arm witness ledger for this wave** (corpus witness vs unit-pin only):
unbound-self read/write/aug-assign, `copy()` at Optional[record] fields, the
F2b call/field sources, ref-element tuple container elements, tuple-literal
field writes, the Span and Send[Callable] MIL cells, value-opt args and
globals, and the value-opt MIL inner scalar all have flipped corpus cases.
`field_write.record_copy_ctor` (`copy(T(...))` into a PLAIN record field) has
NO corpus witness and rests on its unit pin alone -- every corpus
`copy(ctor)` writes an Optional field.

**Second half of the wave: the SHALLOW components pay, the deep one does
not.** After the first seven arms measured 2104 -> 2064, five more cells
(cells 8-12) took it to 2051 and the dial from +20 to +30 flips. Where they
landed is the whole lesson: ctor 115 -> 94 and top_level 83 -> 69 both paid
at close to 1:1, while body moved 1783 -> 1765 across the same effort. A
ctor or a module init is a SHALLOW body -- clearing its one blocker clears
the unit -- where a function body has a queue behind it.

The five: a fits-i64 BigInt binop at a SLOT-THREADED position (the AST
folds only when `target_type is None`, so the reject there was over-broad);
a global slot pointing AT a borrow-returning call's storage (`pt =
&(points->load(0));`, the address-of catch-all the gate's own docstring
already named); container / type-param / `String` base-init args (all bare
param-name renders); the `Own[...]` unwrap the bare-name field write was
missing (a property setter's param, which is also why the oracle MOVES);
and the empty-container ctor MIL cell (`items(std::vector<T>())`).

**Two cells were probed, verified BLOCKED, and reverted rather than
landed.** `ctor.mil_field.optional.name` (12 units) and
`ctor.mil_field.genrec_concrete.name` (5) both turned out to be blocked one
layer BELOW the gate being ground -- at `_optional_ptr_borrow` and
`_f1_record` respectively, binding predicates with many consumers each.
Widening the MIL gate alone just admits a row whose lowering rejects; that
was built, observed, and reverted. Both are filed as design-track items.
The transferable rule: when a gate widening does not move the reject TAG
out of the body, the blocker is not in that gate.

**A row that CRASHES is worse than one that rejects.** Adding Callable to
the bare-name field write raised `unhandled THIRFormConvert: CallableType
Form.VALUE->Form.STORAGE` at emit rather than falling back -- the family has
no borrow->storage convert arm. Caught by the cell's own pins; the boundary
pin now records it so the family is not re-added without the arm.

**Third pass: the two families I had wrongly called blocked.** A first
"verified-blocked" claim was FALSE -- the resumable component (123 units)
had never been probed at all, and `container_lit.slot_family` (24, in this
wave's own approved track) had never been touched. Both yielded:

* `container_lit.slot_family` was the largest untouched family on the
  frontier (14 sole-blocker cases). `_lower_ru_literal` already existed but
  was wired only at the CALL-ARG position, and its admission only covered
  the non-generic `AliasRef` form. Sema types the three forms differently --
  `list[AliasRef]` plain, the alias INSTANCE for an outer `Tree[int]`
  literal, `list[Tree[int]]` for a nested one -- and all three render the
  same spelled container, so one classifier now covers them.
* The resumable `stmt.raise` guard was a deferral a PRIOR review had already
  verified as unnecessary and written up in TODO.md, naming the exact tests
  to update. Three pins keyed on it (the entry named two; the corpus run
  found the third).

**The lesson worth keeping:** "verified-blocked" has to mean PROBED, not
"I did not get to it". Both of these were one read away from being obvious,
and one of them was in the wave's own approved track list.

**A pin caught a divergence the corpus could not.** The generic wrapper arm
initially admitted `None` elements, which render `nullptr` instead of
`std::monostate{}` because the monostate emit keys on the element's result
type being the UnionType. No committed case puts `None` in a generic
wrapper literal, so the 3600-case byte-diff was green -- the cell's own pin
is what failed.

**The reachability arithmetic, measured rather than asserted** (whole-corpus
`THIR_FALLBACK_JSON` at 2045 units):

| slice | units |
|---|---|
| `expr.call` + `expr.method_call` + `decl.slot_type` + `return.slot_type` | 721 |
| everything else | 1324 |

So the 105 units still needed for the wave's target are NOT arithmetically
closed off -- the non-elephant residue is 1324. What closes them off is the
SHAPE of that residue: after this wave the largest non-elephant tags are
`stmt.match` 63, `stmt.return` 44, then a long tail of 23/22/21/20/18...,
and the three biggest were each probed to a blocker one layer BELOW the
gate:

* `stmt.match` 63 -- per-arm `LiteralType` TYPE FACTS can rewrite arm
  bodies; the top tiers reject facts. Strategy-selection widening built and
  reverted.
* `ctor.mil_field.optional.name` 12 -- `_optional_ptr_borrow` requires an
  F1-record inner, so the source NAME read rejects. Built and reverted.
* `genrec_concrete.name` 5 -- `_f1_record` rejects a generic instantiation
  whose type arg is a recursive union. Same shape.

All three are BINDING-PREDICATE widenings with many consumers each, i.e.
design-track work of the same kind as the pointer-slot-global deref rule.
That is the honest statement of where this frontier ends: not "no units
left" but "no more GRIND units left -- the next 105 need a design round
first."

#### Frontier verification (2026-07-27, closing the field-write + containers wave)

The wave's unit target (>=164) was missed at 59. The reason is structural
and is now measured rather than asserted -- split every non-elephant tag by
whether ANY case carries it as its SOLE blocker (`fallback_final.json` x
`blockers.json`):

* **437 units live in tags no case is blocked on alone.** Routing them
  flips nothing and, under body-component interlock, moves no unit. Five of
  the histogram's biggest apparent levers are here (`expr.fstring` 20,
  `sig.overload_set.db_compare` 18, `stmt.tuple_unpack` 16,
  `return:binop.shape.==` 14, `sig.member_shadows_type` 13).
* **348 units in 17 tags of >=13 units are real levers**, and all 17 are now
  probed to a named blocker. Two design-track predicates account for 129 of
  them (`_optional_ptr_borrow` 85 across five tags, and the `lc.pointers`
  keying the borrow-tuple return shares with it, 44).
* **590 units in 200 tags of <=12 units each** (mean ~3). No single one can
  move a wave.

So on this frontier a >=100-unit target is not reachable by tag selection;
it needs one of the two design items. Recompute the split (a five-line pass
over the two dumps) before pricing the next wave.

#### Late cells

* **Non-value `try` hoists** take the OPTIONAL_STORAGE predecl
  (`std::optional<T> name;`, engaging assigns, deref reads) that the if
  cascade and the with family already carried -- the try path simply never
  passed `opt_storage` to the shared `_lower_hoist_predecls`. Two of the
  seven witnesses clear; the rest need the POINTER flavor (reassigned /
  borrow-only names) or the in-branch scope model.
* **Bytes loop elements** resolve through the shared view-family resolver
  before spelling, the same resolution the str element already took -- a
  `PendingViewType` element reached the binding unresolved and diverged.

#### CORRECTION + design round: the `lc.pointers` question (2026-07-27)

The wave's closing note priced `_optional_ptr_borrow` at ~111 units and
paired it with the borrow-tuple-literal return (44) as "one shared root
cause", on the evidence that both experiments diverged on
`ptr_optional_collapse` and `tuple_readonly_return`.

**That overlap was a bookkeeping error and the pairing is withdrawn.**
Re-probed directly under the widening, those two cases are byte-IDENTICAL;
they were the RETURN experiment's divergences, mis-attributed. The
`_optional_ptr_borrow` widening diverges on exactly one case,
`union_recursive_optional` -- and that site never reads `lc.pointers`. Its
lossy proxy is `lc.narrow.narrowed` standing in for the AST's `already_union`
disjunction (`codegen_cpp/expressions.py:366`), a set Optional `is None`
narrowing never populates.

What the round established instead:

* **`_optional_ptr_borrow` is ~13 direct units and ZERO flips**, measured
  base-vs-widened over 170 candidate cases (400 -> 387). ~90% of the tag
  family is interlocked. It is an enabler, not a lever -- never price it
  standalone.
* **The borrow-tuple ladder's real defect is a MISSING DISJUNCT, not a
  set-population difference.** The AST's `is_already_pointer_source`
  (`codegen_cpp/context.py:2971`) is `is_indirect_name(expr) OR
  isinstance(get_expr_type(expr), PtrType)`; THIR mirrors only the first.
  `ctx.pointer_locals` is empty on the AST side at the failing site too, so
  position has nothing to do with it. Filed in BUGS.md as a LIVE THIR-only
  defect (a `Ptr[T]` name in a borrow-form tuple literal emits `&(p)` ->
  `Node**` into a `Node*` slot), reproducing at the CALL-ARG position on
  master with no corpus witness. **CLOSED:** fixed by a shared
  `_already_pointer_source(expr, lc)` (predicates.py) carrying both
  disjuncts and taking an EXPRESSION, so the subscript row is covered too;
  four rows route through it, the pointer-repr-Optional row DEFERS rather
  than lifting. Corpus witness `tests/cases/tuple/borrow_tuple_ptr_element`.
  The companion entry claiming four FURTHER sites would inherit the lift was
  withdrawn on probe: three reject before reaching any pointer test, the
  `with` manager is sema-rejected, and the generic-tuple `TypeParamRef`
  guard mirrors `is_indirect_name` (which does NOT deref a `Ptr[T]`), where
  the name-only spelling is correct. That distinction is now the third hard
  finding in THIR_EMIT_INVENTORY.md.
* **A seed-only repair is inert and worth landing**: making `lc.pointers`'
  seed + `admission_pointers()` filter faithful to codegen's
  `seed_param_locals` measured 0 unit change and 0 divergence over 152
  cases. THIR's set is a deliberately PARTIAL mirror and that narrowness is
  currently load-bearing as an implicit fence.

**The transferable lesson:** two experiments sharing a failure set is a
strong signal -- which is exactly why the failure sets must be recorded from
the probe output, not from memory. A phantom overlap justified a whole design
round.

### Truthiness / borrow-tuple / bounded-tparam wave (2026-07-27)

Six cells off master `d7c45bc01` (baseline 2034 fallback units: body 1755,
ctor 88, resumable 122, top_level 69).

**The wave's framing finding -- run the sole-blocker census BEFORE setting a
unit target.** Summing, per tag, the fallback bodies of only those cases the
tag blocks ALONE gives 685 of the 2034 units, and 234 of those sit inside
`decl.slot_type` / `expr.call` / `expr.method_call`, which fragment. A unit
target much above ~80 therefore cannot be met from the non-elephant frontier;
the elephants pay only in verified SUB-FAMILY slices. Two were carved here.

**Both enum truthiness wraps substitute a VALUE render.** `gen_truthy_expr`
calls `gen_expr` and wraps the result, so the IntEnum arm's `CONDITION` use
was the odd one out -- and the name/field-only shape gate it forced rejected
call, method-call, subscript and ternary operands. Deleting the gate and
lowering both arms under `TRUTHY` left every enum case byte-identical. The
`stays_ast` pin whose comment STATED the CONDITION reason became a routing
pin; a walrus operand replaced it as the boundary.

**An ALWAYS_TRUE truthiness operand is a discard.** `(static_cast<void>(x),
true)` throws the result away, so a record-returning method or property read
there needs exactly the widened return set `stmt_position` already grants --
`_ExprUse.truthy_discard` carries the fact. A `__bool__` record's operand is
CONSUMED by the dunder and keeps the value-position gate.

**Match guards render through `gen_truthy_expr` too.** Lowering used a plain
`CONDITION` use and rejected any non-bool guard at `match.guard_type`; routing
it through `_lower_truthy` left all 162 match cases byte-identical. A
native-int guard has no WRAP (its render IS its value) so the bool-result
check still rejects it -- the existing pin's premise ("the AST renders the
guard raw") was simply wrong and is corrected.

**The borrow-tuple decl trap: two same-typed shapes, told apart only by the
CALLEE's declared return type.** `auto p = pair_of(b);` and `auto t =
make_pair(5);` produce identical element lists, but the second returns
`Own[tuple[..]]` and binds owning STORAGE (`std::get<1>(t).val`, not
`->val`). `get_expr_type` strips the Own wrapper, so keying the arm on the
expression type mis-registered the local and the corpus byte-diff caught it;
the fix reads `fi.return_type`. The name-source sibling has the same trap in
the other direction -- a `storage_tuple_locals` name is the AST's `auto&&`
durable alias and is excluded by set membership, the precondition
`_is_borrow_form_name` documents.

**The builtin-module `@cpp_template` slice carves out of `expr.method_call`.**
`tpy.unsafe.unsafe_ptr(arr)` / `tpy.Int32(10)` reach the AST's builtin-module
arm, and every `@cpp_template` branch of it renders through
`gen_template_or_native_call` with the RESOLVED fi -- the same expansion the
same-module static template already took. The marker classifier rejected the
whole family one line before that branch. The `@native` branches
(receiver-threaded / `gen_call_arg` loops) and the special-handling builtins
keep rejecting. This made `_marker_call_kind` carry a THIRD copy of the
expand-and-check-positional block, now folded into `_template_kind`.

**A defect the routing exposed: match arms emitted their leading comments.**
Every AST match emitter calls `emit_source_comment(case.loc)` alone; THIR's
arm emitters used the combined `stmt` sink, so a `#` comment between
`match x:` and its first `case` came out that the AST never wrote. Invisible
until the guard cell routed `match/guard_truthiness`, whose arm carries
exactly such a comment -- the whole-corpus byte-diff caught it. New
`CommentSink.case_` at all 15 arm sites.

**A receiver-shape row, not a `_field_receiver_ok` widening.** Reading a field
off a protocol-bounded type param (`item.value` under `[T: HasValue]`) got two
new rows in the receiver disjunction rather than a row inside
`_field_receiver_ok` -- the same reasoning the unbound-self arm used one wave
earlier: ~75 sites read that predicate and most consult the receiver BINDING,
which a type-param receiver does not have.

- **The arg-row site grind -- `expressions.py:6982`, the free-call arg
  disjunction** (2026-07-28; commits `b6ac7ace`, `61161a55`, `c5259d50`,
  `da5fc30e`). The first wave driven by `probe_sites.py` rather than by tag
  selection: one raise site, its 16 sole-blocker cases ground until 15 no
  longer reject there. Dial 2610 -> 2617/3611 (+7); `body:expr.call` 209 ->
  204 units. Eleven new rows -- an int literal at a native fixed-int slot
  (sema does not coerce a native callee's args, so it keeps IntLiteralType); a
  `bytearray` name at a `bytearray` slot on both ladders; a borrow-form tuple
  PARAM name passed bare; a Callable VALUE at a template's `Fn` slot; the
  temp-free half of the Own cascade on the native ladder; a `self`-capturing
  lambda inside a simple-generator method; `len()` over a `@property` read and
  over a module variable; a NAME at a required multi-protocol union; a
  comprehension inline at a native `Iterable` slot; a `Spannable[T]` coerce;
  and a two-lvalue ternary at an `Own[T]` slot.

  **A raise-site histogram is the measurement that makes a big tag workable.**
  `expr.call` carries no `note_detail`, so the residual dump shows it as one
  opaque 209-unit blob and every previous wave skipped it as "fragmenting". It
  is not: its sole-blocker cases concentrate into four sites, and the largest
  one turned out to be thirteen independent rows against a single disjunction
  -- exactly the multi-cell TRACK shape, none of which is visible from the tag.

  **Absence from a mirrored set is NEVER evidence -- this cost two fixes before
  the premise itself was abandoned.** The borrow-tuple bare-pass row read "not
  in `lc.storage_tuple_locals`" as proof of borrow form. First failure: the
  resumable lane never fills that set (codegen keeps those names in
  `storage_form_tuple_locals`), so an owning tuple frame local would have
  passed bare and dropped its `tuple_to_pointer` lift; an existing fence pin
  caught it and the row was made to decline in that lane. That patch was
  treating a symptom. Second failure, found when the review's const-tracking
  finding was probed: a for-each loop var over a storage container is not in
  the set EITHER, so `peek(it)` rendered bare against an AST that lifts -- a
  live wrong-value divergence, corpus-invisible because every case with that
  shape falls back for an unrelated reason. The row now keys on POSITIVE
  evidence (the name is a pointer-repr tuple param). The general rule: a
  predicate standing in for one of codegen's `*_locals` sets must test for
  membership in something, never for absence -- absence conflates "known not to
  be X" with "this lane does not track X", and the second is the common case.

  **An arm that pays no case and cannot be witnessed should not ship.** Two
  storage-side lift rows were built here, then removed at review: they flipped
  nothing (their target still falls back at `subscript.elem.tuple` regardless)
  and no unit-sized shape witnessed them, so they would have shipped as
  unpinned admissions in the family that had just produced the wrong render
  above. Rebuild them with the unified classifier, alongside the element read
  that actually unblocks the case (TODO.md carries both).

  **A guard broader than its docstring is a latent divergence waiting for a
  gate widening.** `_protocol_union_ctor_arg` said "CTOR positions only" but
  keyed on `not method_arg`, which also holds for free calls, and returned the
  address-of lift for REQUIRED protocol unions. The AST's `_gen_protocol_arg`
  splits on `has_none` instead -- a required union monomorphizes to one
  template param and renders the plain value. The arm was unreachable only
  because the gate rejected those args; widening the gate turned it into a
  `describe(&(nums))` divergence the corpus byte-diff caught immediately.

  **A render assertion is not a routing pin, and "stays AST" is not a
  boundary.** Two arms here move the reject one layer DOWN rather than routing
  the body (module-var `len` -> `field.module_var_type`, ternary -> `expr.ifexpr`).
  Their first pins asserted the emitted C++, which a fallback reproduces
  byte-for-byte, so both passed while proving nothing; converting them to
  `_fn(...) is not None` made them FAIL and exposed the gap. They now assert
  the fallback REASON, which is the widening's true and complete effect.
  Symmetrically, three boundary shapes here (bytearray into a `bytes` slot, an
  unroutable property getter) DO route, so a `stays AST` assertion would pass
  vacuously -- they assert the reject reason instead. Where no non-vacuous
  assertion could be constructed (the resumable self-capturing-lambda
  exclusion, which routes in every shape probed), no pin was written and the
  gap was filed.

  **A "the row would be dead" comment is a dated claim, not an invariant.**
  `_coerce_wrap` guarded off the `_SPAN_METHOD_COERCIONS` protocol-actual
  branch with "protocol params/locals reject upstream, so the row would be
  dead". A `Spannable[T]` param inside a template body now reaches it, so the
  row was live and rejecting a case. When a widening lands near such a comment,
  re-test its premise rather than trusting it.

  **Parked, with its blocker named:** `set/set_comp_owned_move` needs its arg
  temp emitted INSIDE the comprehension's loop body (`if (is_small(__tmp_2))`).
  `THIRComprehension` has no per-condition temp sink -- `state.temps.create`
  flushes at the enclosing statement, outside the stmt-expr entirely, which is
  the exact bug the case was written to guard. Routing it is a structural
  addition to the comprehension node, not a gate widening, so it stops here for
  a design decision.
- **The instantiation-gate site grind -- `expressions.py:4386`, the
  generic-type instantiation gate** (2026-07-28; commits `3c9e28a72`,
  `77cfd452b`, `b7762bc95`). The second `probe_sites.py` wave, against
  `expr.call`'s second-largest raise site: its twelve remaining sole-blocker
  cases ground until NONE rejects there. Dial 2620 -> **2626/3615** (+6);
  suite 9937 green, full exec. Six rows.

  Rows, and the AST arm each mirrors -- all inside `_gen_call`'s `call_type`
  branch:

  1. `Array[T, N]([...])` -- the resolved-ctor template arm skips an
     array-literal first arg outright, so the call lands in the generic tail,
     which spells the target type and hands the literal `call_type` as its
     brace target. Gated to Array's param-less `__init__`.
  2. `Span(p, n)` -- the value-view sibling of the str-family
     `_viewfam_ctor_call_fi` arm. Placed AHEAD of the bare-source view arm,
     because the AST reaches the resolved-template arm first; the shared shape
     check was extracted as `_template_ctor_call_fi`.
  3. The instantiation RESULT gate goes element-blind. It had been reusing
     `_storage_call_ret`, whose element check exists for DOWNSTREAM reads; the
     ctor's sema-substituted template already spells the whole container, so a
     record or tuple element renders exactly what a scalar one does.
  4. A spelled `list[Int32](it)` routes like the inferred `list(it)`: sema
     folds the spelling into `call_type` and into the template, and no AST
     call-gen branch reads the node's own `type_args`.
  5. `set([Node(2)])` -- the spelled container around the literal's braces.
  6. An owning CALL rvalue at an instantiation ARG slot (`set(make_nodes())`,
     `set(copy(b))`) -- the chain's next site, cleared in the same wave.

  **The gate that is wrong is the one whose verdict the render does not
  read.** Three of the six rows are one finding: the instantiation gate was
  keyed on facts about the RESULT'S CONSUMERS (element family, spelled type
  args) rather than on what its own render spells. A gate should encode the
  render's requirements; the sinks the value flows into already run their own.

  **Lowering a literal against the CALL's type is not the same as lowering it
  against the AST's `target_type`.** The AST's `_gen_array_literal` dispatches
  the RENDER on the literal's own kind and uses `target_type` only for element
  targeting; `THIRContainerLiteral` fuses both into `result_type`. Passing
  `e.call_type` through would have rendered a `set` result as a second
  `ordered_set`; passing nothing let the literal's own sema type (a read-only
  literal DEMOTES to `Array[T, N]`) turn on the Array element retype and emit
  `::tpy::BigInt(2)` where the AST renders a bare `2`. The correct target is a
  LIST of the RESULT's element slot -- neither of the two obvious spellings.
  Only adversarial `dualgen.py` found this; no corpus case has a BigInt
  element in that position.

  **A value-CATEGORY key beat a callee key.** The instantiation arg ladder had
  five callee-shaped arms (ctor / slice / generator factory / dict view /
  combinator template) and still missed `copy_iter(..)`, `copy(..)`,
  `make_nodes()` and `heapq.merge(..)` -- four unrelated callees. One
  `is_rvalue_source` arm covers all of them, and its justification is stronger
  than any callee list: the own_iter / last-use rows below exist to consume a
  BINDING, which an rvalue does not have. The boundary (a call returning a
  BORROW) keeps rejecting, and is pinned.

- **The free-call RESULT-gate site grind + the shadowing-record qualification
  fix -- `expressions.py:4055` and `functions.py:404`** (1961 -> **1895** fallback units, +31 flips) (2026-07-28; commits
  `0508d7d65`, `c1e44632c`, `e851289ce`, `92d969946`, `fd519dddb`). The third `probe_sites.py` wave
  against `expr.call`, plus the single largest whole-family reject in the
  corpus. Twelve rows.

  Rows, and the AST arm each mirrors:

  1. `yield copy(p)` at the simple-generator `__val` slot, and `copy(p)` at a
     record container-ELEMENT slot -- the shared copy-construct row the decl /
     setitem / field-write / return sinks already took. The element row is
     gated on the SLOT being that record, so a slot needing an element wrap
     keeps the wrapping tail.
  2. A redundant `copy()` dropped at the `into_any` coerce: `make_any` already
     copy-constructs into the cell, so the AST emits `make_any(c)`, never
     `make_any(Counter(c))`. Ptr-variant unions stay excluded -- there
     `copy()` converts REPRESENTATION (`to_value_variant`) and dropping it
     would store dangling pointers.
  3. A generator-FACTORY call as the slice-assign RHS: `list_set_slice`
     consumes it as an iterable, so it takes the iterable-position admission
     rather than the storage slot set.
  4. The composite sibling of the bare-`T` generic arg row: a NAME bound to
     the still-unsubstituted slot (`first(items)` at `list[T]` ->
     `first<T>(items)`) binds the ref template param bare. Own slots stay on
     the move row above it.
  5. A list literal at a generic callee's substituted container slot hoists
     the same named `__tmp_N` the concrete free-call row hoists -- a
     `std::vector<T>&` param cannot bind a brace prvalue.
  6. A GENERIC generator factory in iterable position spells exactly like any
     other generic free call, so only the DEF stays on the AST path. Arg
     positions keep rejecting (unprobed slot render), and are pinned.
  7. The container-element twin of the borrow-tuple field wrap, plus the
     subscript READ that feeds it (`consume(items[0])` ->
     `tuple_to_pointer<...>(::tpy::__getitem__(items, 0))`).
  8. The scalar/str LITERAL sibling of the recursive-union wrapper temp
     (`show(42)` -> `Value __tmp_N = 42;`).
  9. A REF_ALIAS decl whose source is an and/or/ternary CHOICE of two
     alias-legal names (`x = a or b`).
  10. Shadowing records (below).
  11. A marker call's `@call_macro` expansion (`dataclasses.asdict(p)`), which
     the free-call ladder already lowered via `call.macro_expansion` while the
     marker path rejected outright. Pays no corpus case: the case that would
     flip also uses `astuple`, whose expansion is a bare TUPLE LITERAL with no
     generic `_lower_expr` arm (sinks lower those against their slot). Ships
     because it is independently WITNESSABLE -- `asdict` alone routes with zero
     fallback, byte-identical. **An arm that pays no case but can be witnessed
     is legitimate; one that cannot be witnessed at all is the dead row this
     wave's review had to delete.**
  12. The `auto&&` storage-tuple alias widened from FieldAccess-only to the two
     other sources the AST admits -- a container SUBSCRIPT, and a NAME that
     already aliases storage -- mirroring `is_storage_form_source`. Admitted
     CONST-FREE on the new shapes: the AST's const verdict lives in a
     body-global set whose CONSUMERS drive later borrow reads, so admitting a
     const one would mean mirroring the whole consumer topology rather than one
     value. Rejecting const sources makes the widening provably
     const-neutral, and that boundary is pinned.

  **FIRST-REJECT unit counts OVERSTATE what clearing a row pays.** The new
  `probe_site_units.py` census priced `call.arg_shape.tuple` at 13 units and
  `call.arg_shape.union` at 10; landing both paid **+2 routed bodies**. A unit
  only drops when the row is that body's LAST blocker -- otherwise the body
  chains straight to the next one and stays a fallback. Rank rows by
  SOLE-blocker mass; treat a first-reject histogram as a map of WHERE work is,
  never as a forecast of what it yields.

  **The biggest single lever was a rendering-CONTEXT mismatch, not a missing
  arm.** `sig.member_shadows_type` rejected an entire record family (13 body +
  5 ctor units in one case) because the AST emits a shadowing record inside
  `qualify_shadowed_nominals()` while THIR pre-renders its type spellings in
  one up-front LOWERING pass, where that context was never live. No shape was
  unsupported. Lowering those bodies inside the same context fixed the
  rendered types outright; only the ctor CALLEE needed code, because it spells
  the raw `func_name` rather than a rendered type. **When a whole family is
  rejected wholesale, ask WHEN the render happens before concluding that WHAT
  it renders is unsupported.**

  **That qualification follows the ENCLOSING record, not the constructed
  one.** `day(...)` inside `clock` qualifies because `clock` has a member named
  `day`; `day` itself shadows nothing. Keying the ctor callee on the
  constructed record's flag would have left every real case unqualified while
  looking correct in isolation -- it is pinned both ways.

  **`decl.slot_type` (166 units, the corpus's largest single site) is NOT a
  row.** `probe_slot_families.py` breaks it into tuple (29), list (24), dict
  (12), set (6) and pointer-repr Optional/union decls -- all of them decls
  whose init is neither a literal nor a storage call, i.e. the borrow-vs-
  storage form frontier (`IR_DESIGN.md` open question 9). It needs the form
  design, not gate widenings.

  Two new probes upstreamed into the wave skill: `probe_site_units.py` (ranks
  a tag's raise sites by fallback UNITS rather than sole-blocker cases) and
  `probe_slot_families.py` (breaks down a site whose entire mass carries one
  detail string).

  **NEXT SESSION IS NOT A WAVE (user decision, 2026-07-28): the A5 baseline
  + the D4 design round.** The dial (`N/3615 migrated`) counts
  USER-module cases with zero fallback, and deleting the AST body emitter
  needs FIVE hard gates -- waves move only A1 (markers -> 0). Grinding A1 to
  zero still leaves the emitter undeletable, so measure the other gates
  before buying more A1:

  * **A3 -- AST-arm residency: ALREADY MEASURED, AND IT IS A DEAD END. Do not
    re-run it.** The 2026-07-27 run cost 1h52m and produced `A-U = 31`
    functions / 3.3%, which **does not measure "AST emit left to delete"**:
    `U` already covers 97% of functions, because signatures, record decls,
    protocol emission and headers stay AST-emitted regardless of routing and
    call the same `gen_expr`/`gen_stmt` helpers a fallback body calls. The
    number is a floor on exclusively-fallback code, not a size. What survives
    from that gate is the **zero-witness face list**, which prints free on
    every `--thir-codegen` run (15 open at this wave's close). Note the hole
    feeding it: `_witness()` has no rollback, so an arm that witnesses BEFORE
    it can raise reads as witnessed even when it never lowered -- that defeats
    the detector itself, and is filed in TODO.md.
  * **A5 -- stdlib fallback.** Unmanaged: resolved on paper only
    (`IR_DESIGN.md`:893-895), no instrument, and the single datum is a stale
    2026-07-05 drill showing ~249 blocked stdlib bodies. The migration is
    deliberately scoped to user modules so a case migrates on its own code and
    not its imports' -- right for grinding, and exactly why this surface is
    unmeasured. It could rival the whole remaining corpus.
  * **D4 -- the post-cutover verification story.** Today's net is the
    dual-path byte-diff with AST as the oracle. At cutover that oracle is
    DELETED and nothing specifies what replaces it, nor when snapshots become
    THIR-authored. Decide before the last mile: it is the one hole where being
    wrong is unrecoverable. Siblings from the same inventory: D3 (skeleton
    ownership never transfers per any doc) and D1 (= A5). D2 is DONE.

  Grind economics for whenever waves resume, measured over ~30 rows this
  session: **leaf gate rows pay 0-2 units each; the one structural find paid
  17.** At ~1900 units the tail is ~1000 rows worked shape-by-shape, so prefer
  structural finds and the classifier-undercoverage sweep (diff the AST's
  per-type dispatch arms against the THIR enum -- `gen_print` had 13
  predicates where `PrintForm` had 11 members; two free rows). The
  pin-comment mine is now EXHAUSTED: a re-scan of all 407 boundary pins
  returns only fences ("must keep rejecting"), not invitations.

  **THE SOLE-BLOCKER CEILING, measured 2026-07-28 -- the number that bounds a
  single wave.** For each reason, the units held in cases where it is the ONLY
  blocker (i.e. the most a row clearing it can possibly pay):

  | reason | sole-units | total units |
  |---|---|---|
  | `stmt.var_decl:decl.slot_type` | 84 | 166 |
  | `expr.method_call` | 70 | 158 |
  | `expr.call` | 51 | 179 |
  | `stmt.return:return.slot_type` | **17** | 111 |
  | `expr.container_literal` | 12 | 22 |
  | `stmt.match` | 10 | 63 |

  **Corpus-wide the ceiling is 664 of 1961 units.** Everything else sits in
  bodies with two or more blockers, where clearing one changes nothing until
  the last one goes. Two consequences: (1) `return.slot_type` reads as the
  #2 lever at 111 units and is actually worth 17 -- price by the sole column,
  never the total; (2) no single family holds 100 units, so a 100-unit wave
  needs either two elephants cleared end-to-end or the borrow-vs-storage form
  design that `decl.slot_type` is waiting on. This is the per-body form of the
  set-cover finding already recorded for cases.

  **AND THE CEILING IS AN UPPER BOUND, NOT AN ESTIMATE.** A case's recorded
  reason is its FIRST reject, so "sole blocker" means one reason was *seen*,
  not that one blocker exists -- the rest are masked behind it. Two rows
  landed at the end of this wave (`h.set((x, y))` at a method tuple slot,
  `",".join(self._parts)` at a native Iterable slot) each cleared the only
  reason their case recorded, and each case STILL fell back, to a reason that
  had been invisible until then. So 628 is what the corpus could pay if every
  masked blocker happened to be already-routed; the real figure is lower by an
  unknown amount that only grinding reveals. Plan waves against it as a cap,
  and expect a tail of rows that are correct, needed, and pay nothing
  measurable on their own.

  **FINAL TALLY, and the correction that matters more than it.** 1961 ->
  **1895** units (body 1628 / ctor 83 / resumable 122 / top_level 62), a
  -66 reduction; routed bodies 10702 -> 10763; +31 case flips, dial
  2626 -> 2657/3615 (989 -> 958 markers on disk). 37 commits, corpus byte-diff green at
  every step. TWO /tpy-review rounds are included: the first deleted a row that
  never fired (see below) and added byte-identity to five converted pins; the
  second covered the four rows that landed after it. Full suite green with exec
  (10018 passed, 3614 cases built+run, nothing cached).

  **This tally was wrong four times before it was right, and the last two
  disagreed with each other** (the headline said 1896 while this paragraph said
  1897; the true figure is 1895). Each earlier fix patched the location the
  author remembered instead of grepping for every occurrence of the old
  figures, and one commit message even claimed doing them together made a
  further partial fix impossible. It did not. **Re-derive the number from a
  measurement run and grep every stale figure -- never transcribe a tally from
  a summary, including your own.**

  A FIFTH error, caught by `/tpy-ready` after all of the above: the flip count
  read +36 and the marker baseline 974, both DERIVED rather than measured (+36
  was "20 earlier + 16 at close"; 974 was a MID-WAVE probe count, not the branch
  base). Ground truth is three mutually-confirming counts -- `git diff master
  --diff-filter=D` shows 31 deleted markers, master holds 989 and HEAD 958, and
  2626 + 31 = 2657 matches the measured dial exactly. **A figure you can derive
  by arithmetic is the one most likely to be wrong: derive the dial delta and
  the flip count from the SAME marker census, and cross-check them against each
  other before writing either.**

  The sole-blocker ceiling recorded above is a cap on ONE-ROW-PER-CASE yield,
  NOT on reachable work -- an earlier draft of this entry said otherwise and
  was wrong. Chain-walking clears multi-blocker cases (11 of this wave's flips
  came that way), so the whole 1895 is in play. The measured distribution says
  where: **532 of the 974 cases marked AT THAT MEASUREMENT sit at exactly ONE
  unit** (the wave closed at 958 marked), i.e. one arm
  from flipping. What actually limits a wave is RATE, and the rate is set by
  the KIND of row: this wave's single structural fix (the shadowing-record
  rendering context) paid 17 units, while leaf arg-ladder rows paid 0-2 each.
  Hunt whole-family rejects -- a rendering-context mismatch, a routing
  decision, a gate excluding a wrap the lowering already applies -- before
  grinding ladders.

  **THE CHEAPEST LEAD SOURCE FOUND THIS SESSION: the `stays_ast` PIN COMMENTS.**
  Ten of the wave's rows came from grepping those pins and reading WHY each
  said the shape stays AST -- a minute per candidate against ~15 for a case
  probe. The comment's grammar sorts them:

  | comment says | means | outcome |
  |---|---|---|
  | "the AST renders X" / "has no convert arm" / "read positions only" | the render exists, or is not needed | 5 for 5 became rows |
  | "not built" | the row is anticipated, not forbidden | build it deliberately, with its OWN predicate rather than by widening a sibling's |
  | "has no ladder row" / "no matching node yet" | genuinely missing THIR machinery | left alone |
  | "must NOT open" | a fence guarding a real frontier | left alone -- see the record_borrow design stop |

  **A pin whose reason is RIGHT can still yield a row.** `print(self)`'s
  comment correctly said the AST renders `(*this)`; the first attempt shipped
  bare `this` and adversarial dualgen caught it in seconds. The fix was
  applying `THIRSelf.deref` -- the retag the record call-arg tail already does
  -- not the gate widening alone. **Dualgen is what separates "the render
  exists" from "the render exists and this position applies it".**

  **The unifying rule behind every row that paid:** when one ladder admits a
  shape and a sibling ladder does not, the sibling is usually missing an
  ADMISSION, not a RENDER -- because the render is chosen by position, and
  position is what the ladders share. Free-call <-> method <-> marker <-> ctor
  arg ladders; read <-> write field ladders; record <-> protocol result
  ladders. Every one of those pairings paid at least one row this wave.

### Gate A5 -- the stdlib baseline (2026-07-28, master `1596bea69`)

  A5 had no instrument; the only datum was a stale 2026-07-05 drill (~249
  blocked stdlib bodies). It has one now:
  `scripts/thir_migration/thir_stdlib_fallback.py` lifts the user-module
  scoping gate in `compiler.py:_make_codegen` for a measurement, compiles one
  entry program per `lib/tpy` module, discards the C++, and classifies every
  body. **44 seconds, 88 modules, zero crashes** -- the cost objection that
  kept this gate unmeasured for three weeks was imaginary.

  | population | count |
  |---|---|
  | routed | 710 |
  | **fallback** | **289** |
  | not attempted | 32 |
  | not a body-migration candidate | 662 |

  **71% of candidate stdlib bodies (710/999) already route.** The 289 split
  237 sync functions / 27 constructors / 14 generators / 11 async. The 32
  not-attempted are all `<top_level>` in binding-only modules (`builtins`,
  `typing`, `tpy._builtins.*`, `_bindings.*`) that emit no `.cpp` at all --
  nothing to lower, not a gap. Eight macro modules are excluded (they run
  under CPython at compile time and are not codegen targets).

  Concentration, by module: `tplib.requests` 44, `asyncio` 35,
  `asyncio._executor` 20, `datetime` 17, `tplib.array_list` 14, `tpy.atomic`
  14, `http.client` 13, `urllib.parse` 13 -- the top eight hold 170 of 289
  (59%). By reason: `expr.method_call` 81, `expr.call` 30,
  `stmt.var_decl:decl.slot_type` 12, `stmt.assign:assign.field_write_shape`
  10, `stmt.return:return.slot_type` 10. 78 distinct reasons, 41 of them
  singletons; top-5 = 143 (49%), top-20 = 208 (72%).

  **The mix is the USER corpus's mix.** The call frontier alone
  (`expr.method_call` + `expr.call`) is 111 of 289, and the next tier is the
  same slot-type/field-write families the waves are already grinding. So the
  stdlib tail is largely NOT additive work: most of it is the same set cover,
  and wave rows aimed at the user corpus clear stdlib bodies for free. What
  IS stdlib-specific: `res.*` (16 -- the asyncio executor and the coroutine
  machinery, the sink that is always opened last) and `sig.inplace_dunder`
  (5, `tpy.atomic`'s in-place operator surface).

  **This is a FLOOR, and the routed figure is UNVERIFIED.** An import-only
  entry program instantiates almost nothing, and resumable frames and generic
  monomorphizations only attempt lowering at emission time, so the real count
  under a using corpus is higher by an unknown amount. And the 710 routed
  bodies emitted THIR C++ that **was never compared to anything**: stdlib has
  no snapshot and no byte-diff oracle anywhere (only exec output). That is
  gate D4's open hole, and A5 is the reason it has to close first -- a
  stdlib routing wave would otherwise be grinding a number no oracle checks.

### Gate D4 -- post-cutover verification (2026-07-28, decided)

  The question the inventory left open: what replaces the dual-path byte-diff
  once the AST oracle is deleted, and when do snapshots become THIR-authored.

  **For USER modules, nothing replaces it.** The byte-diff is migration
  scaffolding layered on top of the project's actual verification model, which
  predates THIR and still runs on every case: committed snapshots (3615
  compiling cases), exec (3631 runtime oracles -- exactly ONE compiling case
  has neither `output.txt` nor `panic.txt`, `native/directives_native_module`),
  cpy parity on 3057 of them (558 carry `no_cpython.txt`), comp diagnostics +
  `# tpyc:` annotations, and the `tpyc/` unit suite. Cutover returns
  verification to that model. So D4's user half is a TEARDOWN AND TRANSITION
  problem, not a design problem.

  **The one thing genuinely lost, stated rather than buried:** today the
  emitted C++ has two independent authors, so a WRONG SNAPSHOT is still caught
  -- AST and THIR must agree with each other as well as with the file. After
  cutover a wrongly-regenerated snapshot is self-consistent. The compensating
  controls are exec + cpy and the standing rule that snapshot churn on existing
  tests needs approval. This is the pre-THIR regime; accept it, do not build
  something to plug it.

  **DECISION -- two-commit cutover, and the proof is free.**
  `_thir_flag_conflict` (`tests/conftest.py`:1256) currently FORBIDS THIR under
  `--update-snapshots` ("snapshots must capture the default (AST) codegen
  path"). The cutover therefore splits:

  1. Flip snapshot authorship to THIR and regenerate. **The correctness
     criterion is `git diff tests/cases` EMPTY.** Non-empty means the corpus
     was not ready, and it says exactly where.
  2. Delete the AST body emitter, in a commit that touches NO `expected/` file.

  The order is forced: the proof only works while the AST path still exists to
  have authored the baseline. One combined commit would fold a divergence and a
  ~16k-line deletion into the same unreadable diff.

  **DECISION -- the stdlib oracle, and it gates A5. LANDED 2026-07-28 as
  `--thir-stdlib`, and it found 434 diverging cases on its first run.**
  Baseline: 436 failed / 9588 passed, across 12 modules, in THREE classes.
  The lesson is the gate's whole argument in one number: **710 stdlib bodies
  were already routing, and a divergence class that no user case can express
  had been sitting behind them unseen.**

  **GROUND TO ZERO 2026-07-29 (436 -> 0, 10031 passed).** Three commits, two
  of them not what the baseline entry predicted:

  * **382 of the 436 were a DOCSTRING dropping its leading comment trivia** --
    not the `lower_top_level` gap the entry named. The AST's `gen_stmt` emits
    inline comments for every statement BEFORE the None-code suppression, so a
    `# tpy: <directive>` header preceding a module docstring reaches
    `__tpy_init`; `_lower_stmt_dispatch` lowered the docstring to a
    payload-free `THIRNoOpStmt`. **Landed as `39a80d242` from the
    interop-overlay session -- two instruments built for different gates
    (the interop overlay and the stdlib oracle) converged on the same
    one-liner in the same week.** **`_assert_byte_identical` ran with the
    comment echo OFF, so every THIR unit written to date was structurally
    blind to this class** -- it now takes a `comments` switch, and the
    module-init shape is pinned by `TestDocstringTrivia`.
  * **`datetime` was an AST accident, fixed AST-first** (`d18c69397`, AST-only
    change-set, user-approved): `_maybe_move` peels `TpyCoerce` to decide
    movability and then wrapped whatever the sink rendered, so a BigInt
    narrowed into a fixed-int slot got `std::move` over a scalar prvalue. Zero
    committed snapshots changed. The Own[T] arg temp already drew that
    lvalue-vs-rvalue line; `_maybe_move` now does too.
  * **`urllib.parse` was the ALREADY-FILED pending-view design item** reaching
    a second sink (`b3d19fa65`), fenced like `call.arg_unpack_pending_view`.
    Both fences are now deleted -- see the wave-next10 entry's CLOSED note for
    the AST root cause (`get_resolved_type`'s unresolved `var_types` return).

  **Two findings worth carrying past this gate.** (1) The oracle reports only
  the FIRST diverging module per case, so its per-module counts are LOWER
  BOUNDS -- datetime read 21 and was 49 once class 1 cleared. Re-measure after
  every class; never subtract. (2) A regression case for a movability rule
  must be checked against the PRE-fix compiler: the obvious short `datetime`
  repro binds `const BigInt&` at its unpack target, is therefore never
  movable, and passed vacuously.

  The gap this vehicle was built to close (confirmed at the time, closed
  since): the overlay regenerated only `local_mods` (`conftest.py`:966) and
  `expected/src/` holds only case-local modules (the single `_bindings`
  snapshot under `imports/from_pkg_import_submod` is a case-local package, not
  `lib/tpy/_bindings`). Stdlib emission had no byte-diff oracle anywhere.
  **Vehicle: extend the overlay to NON-LOCAL modules and byte-diff the
  THIR-emitted stdlib `.cpp` against the SAME-RUN AST-emitted `.cpp` already on
  disk** -- both artifacts exist in the case's output dir today, so there is no
  capture phase, no new snapshot, and no committed churn. Flag-gated, and NOT
  deduped: every non-local module is regenerated for every case, so the flag
  roughly doubles codegen per case. A content-hash dedup keyed on the emitted
  AST text was considered and rejected, and the reason is structural rather
  than superstition about proxy keys: **a stdlib module's lowering inputs
  include per-case sema/registry state** -- which monomorphizations the case
  requested, what the registry holds -- so two cases emitting identical AST
  text are not thereby known to present identical lowering inputs. Pairing
  with `--no-exec` is a COST recommendation, not a correctness one: the link
  set comes from the explicit module list, never a walk of the output dir, so
  `_thir_lib/` is never compiled or linked. This is cheaper than round 15's
  pre-run-capture shape and strictly closer to the property being checked
  (AST-vs-THIR directly, rather than both-vs-snapshot). **It had to land before
  any stdlib routing work** -- 710 stdlib bodies already lowered with nothing
  checking their output. It landed, and the divergences it found are recorded
  above.

  **DECISION -- the `_witness()` no-rollback fix is now a HARD CUTOVER
  PREREQUISITE -- LANDED 2026-07-28, and it exposed NINE falsely-covered
  faces.** Witnesses are journalled per lowering attempt and undone when the
  body falls back; corpus zero-witness went **15 -> 24 of 711**, so the
  detector's own coverage claim had been inflated by 60%. The nine
  (`arg.native_comprehension`, `arg.native_int_literal`,
  `call.inst_genexpr_arg`, `containerlit.tuple_borrow_storage`,
  `decl.ru_instance_literal`, `decl.ru_wrapper_literal`,
  `res.frame_tuple_literal`, `subscript.record_elem_borrow`,
  `top_level.global_ptr_copy`) are filed in TODO.md for pins. The journal is a
  WINDOW rather than a running tally -- `None` between attempts, with
  `commit_attempt()` at every routed branch -- so that witnesses recorded
  outside a lowering attempt (`thir/emit.py` records faces too) are never
  journalled and so never rollback-eligible.
  **CORRECTION, measured after the fact: `commit_attempt()` is DEFENCE IN
  DEPTH, not load-bearing, and the commit message for `ca57e429d` overstates
  it.** Disabling it corpus-wide leaves the zero-witness list byte-identical,
  because `begin_attempt` precedes every `fold_attempt` and RESETS the journal
  -- so a rollback already can only drain its own body's witnesses. The
  original claim (a neighbour's fallback draining a routed body's emit
  witnesses) cannot happen for exactly that reason. Keep the call: it makes
  the window explicit instead of implied by a call-order coincidence that a
  future fold-without-begin would break silently. The lesson is the ordinary
  one -- **an argued invariant is a hypothesis until an experiment removes the
  mechanism and shows the number move.**
  The retrospective then found the reasoning still too generous: the journal
  is ONE FLAT SLOT, so in the very scenario cited to justify the call (a
  lowering that straddles emit, i.e. a nested attempt) an inner commit would
  CLOSE THE OUTER BODY'S WINDOW -- the mechanism would participate in that
  hazard, not defend against it. The honest justification is narrower and
  stronger: a definite end to the window is what lets `rollback_witnesses`
  ASSERT the window was opened, which is the enforcement the 6-site
  convention otherwise lacked. That assert landed with this branch, and
  attempts-must-not-nest is now stated where the code can be read.
  **The finding that outlives the nine faces: the standing three-unit
  convention's "routing pin (face witnessed)" was satisfiable by a body that
  never routed, so it needs restating as "routed AND witnessed" -- and every
  pre-2026-07-28 `zero-witness N of M` figure in this ledger is INCOMPARABLE
  with post-fix ones.** Filed in TODO.md.
  After teardown, `faces.py` is the
  primary internal detector for admitted-but-unwitnessed arms. It currently
  increments at the call site, so an arm that witnesses BEFORE it can raise
  reads as witnessed even when it never lowered -- and that is precisely how a
  dead arm (`decl.alias_choice_src`) passed the project's own check. A detector
  that reports false coverage is worse than no detector once it is the only
  one left.

  **Teardown inventory.** DIES: the overlay (~321 THIR-touching lines in
  `conftest.py`), the ratchet, 958 `no_thir.txt` markers, the 5 CLI flags
  (`--thir-codegen` / `--no-thir` / `--thir-classify` / `--thir-check-flip` /
  `--thir-stdlib` -- the last dies with the rest: it diffs THIR against AST
  output that will no longer exist) and `_thir_flag_conflict`,
  `thir/fallback.py` (269), `tests/
  test_thir_harness.py` (169), the dial itself. SURVIVES, and becomes the ONLY
  internal net: `faces.py` (1462), `validate.py` (386), `dump.py` (786),
  adversarial `dualgen.py`. Note the workflow loss to plan for: `--no-thir`
  pure-AST mode disappears with the path it selects.

- **Landed: docstring leading-comment trivia** (`stmt.trivia`): a docstring
  statement now lowers as `THIRNoOpStmt(trivia_loc=loc)` instead of a bare
  no-op. The AST's `gen_stmt` flushes a statement's leading `#` comments BEFORE
  dispatch and only then lets the `None` simple-stmt code suppress the
  statement's own source line; the docstring arm modelled just the suppression,
  so the preceding comment block vanished on the THIR path at four sites
  (module top level, function body, constructor body, method body). Four
  sibling skip arms already set `trivia_loc` -- this one was the odd one out,
  and its comment (plus `THIRNoOpStmt`'s docstring) asserted the false premise
  that the AST emits no comment at all for a docstring. A class-body docstring
  is unaffected: the AST filters it before `gen_stmt`, so neither path emits
  its trivia. The corpus never caught this because no case had the shape;
  `tests/cases/records/docstring_leading_comments` now pins all four plus both
  boundaries.

- **Landed: interop ext-exec THIR overlay** (`tests/test_interop_exec.py`):
  the `tests/interop` corpus now runs the byte-diff and the `no_thir.txt`
  ratchet, closing a harness-shaped blind spot -- an AST/THIR divergence in an
  extension module was previously invisible. It emits BOTH sides itself rather
  than diffing THIR against `expected/`, because those snapshots come from the
  real `tpyc` CLI at the default `emit_source_comments=False`: a
  THIR-vs-snapshot diff cannot see a source-comment divergence at all, which is
  exactly the class the docstring-trivia fix above belongs to. Interop cases
  are tallied on their own summary line and kept out of the migration dial, the
  fallback histogram, and the face-witness union -- all three are keyed to
  `tests/cases`, and the face exclusion errs toward a false zero-witness
  (wasted work, never a missed divergence).

### Method arg-gate wave (2026-07-29): the expr.method_call ARG site ground to its floor

Dial 2661 -> 2666/3623 (+5 flips), markers 962 -> 957. Full suite green
(10103 passed, exec + cpy). Five cells against ONE raise site (the
`_require_method_call_arg` raise, `expressions.py:1029` on this tree), which
the fresh histogram priced at 9 sole-blocker cases -- not the 16-18 the
2026-07-28 tree showed; the free-call wave had already drained it. The other
lesson from the re-measure: the site ORDER flipped (shape gate 17 > marker 10
> arg 9), so the next `expr.method_call` track should re-histogram first.

**What landed.** (a) Whole value-repr `Optional[Callable]` NAME passes bare
at matching slots on the marker/free/method ladders, plus the marker
ladder's func-ref row (`os.walk(top, onerror=cb)` / `onerror=boom`); the
binding read is admitted in `_unrouted_binding_read` scoped to
whole-optional reads, with a name-arm guard keeping the narrowed
invocation (`cb(x)` under `is not None`) on the AST path. (b) The VIEW twin
at the USER-record method position (`conn.request(m, u, body, hdrs)` at
`body: bytes | None`): the method loop is target-less, so the AST passes
the whole optional bare where the free-call position takes the
`_opt_view_arg_shim` split -- the row and its lowering arm key off
`method_arg and not method_arg_stub` for exactly that reason. (c) A
container FIELD read binds bare at a marker callee's container ref slot
(`heapq.heappush(self.heap, ...)`), lowered `field_prechecked` past the
copy-vs-alias fence, declared-type keyed so a narrowed `Optional[list]`
field stays out. (d) An Own[T]-returning method rvalue binds the SAME open
`Own[T]` slot bare (`self._storage.init(ui, other._storage.take(ui))` in a
generic `__move__`). (e) The builtin `setattr(obj, "name", v)` statement
delegates its synthesized `__setattr__` call to the existing dynamic-attr
write mirror.

**A dead row caught before it shipped: sema SUBSTITUTES generic marker-call
params.** The heappush `Own[T]` item slot arrives as `Own[TimerEntry]` on
`resolved_function_info`, so the raw-`Own[TypeParamRef]` ctor-rvalue row
written for it could never fire -- the existing `own.record_rvalue` row was
already paying that arg. The row was deleted the moment its witness read
zero; the general rule stands: check what sema actually stores on the call
node before writing a slot-shape row against the stub's declared params.

**The ratchet caught a real regression, exactly as designed.** The first
setattr delegation captured EVERY `__setattr__` macro expansion, including
the runtime-name form (`setattr(h, name, value)` on a str-slotted
`__setattr__`) that already routed through the general method arm --
`dynamic_attrs/dyn_name_setattr` went routed -> fallback and the corpus
ratchet failed the run. The fix keys the delegation on the literal-name +
coerced-value shape the mirror admits; the regression pin states the
invariant. Note the failure MODE: a delegation to a narrower arm can
DE-ROUTE bodies the general arm already handled, and only the ratchet (not
the byte-diff -- fallback emits identical bytes) sees it.

**Residue at the site: the four `needs_copy`-fork cases** (`enum/
enum_name_owned_sinks`, `async/future_drop_on_cancel`,
`auto_move/same_stmt_aug_target_consume`, `list/tplib_array_list_slice`) --
unchanged, still gated on the TODO.md dead-end entry's design decision
(mirror `_gen_own_arg`'s rendered-identity `needs_copy` by string or by
coercion kind). The arg gate cannot go lower without that decision.

**Review round:** the three whole-value-opt pass-through predicates
collapsed onto one shared core keyed by the family check
(`_whole_value_opt_name_arg`); the adjacent by-value open-T return shape
got its byte-identity pin (the true negatives -- mismatched T, a C++-ref
return at a same-T Own slot -- are sema-rejected and cannot compile).
New faces, all corpus-witnessed: `arg.value_opt_callable`,
`method.optview_whole_arg`, `arg.container_field_marker`,
`arg.own_tparam_method_rvalue`.

### Method-family grind, harvests 2-4 (2026-07-29): the expr.method_call family to its floor

Dial 2666 -> 2701/3623 (+35 flips across three harvests: +15 shape-gate,
+11 marker/receiver-gate, +9 receiver/consumer), markers 957 -> 922. Full
exec suite green after each harvest. With the arg-gate wave above, the
session total is +40 flips, and every `expr.method_call` site is at its
documented residue.

**Shape-gate harvest (+15).** The 2-arg getattr builtin delegated to the
result-blind dyn-attr read mirror (the general arm's result gate rejects
the dunder's Any / owned-str returns; runtime names ride allow_name_arg);
owned-bytes and value-tuple LITERAL elements at container inserts (names
keep deferring pending the S6 convert / move cascade; the pointer-repr
tuple keeps its storage lift); F1-record elements at the set method
receiver; Own[record] protocol-method rvalues at storage sinks; a new
SCALAR-receiver stub family row in `_METHOD_RECV_FAMILY_TABLE`
(`v.as_integer_ratio()` -- value-tuple results at storage/statement
sinks, plus the TuplePrinter wrap for value-tuple call results under
print); open-T results at the shared stub result core (`return
self.items.pop()`); container-property reads bound bare at native slots
(`len(f.items)`, BORROW_BIND -- the alias-decl sink stays fenced);
value-opt owned-view/scalar method results at storage decls (`host =
full.hostname`); bytes-view receivers at the str-list iterable override.
Cascades: `zero_division_caught`, `panic_float_divmod_zero`,
`str_pending_tuple_return`.

**Marker/receiver-gate harvest (+11).** `@cpp_template` methods on
@native record receivers (the Ptr-template arm generalized via
`_native_record_recv`) plus the `cpp_return_type` static_cast wrap
(THIRCoerce over the member call, threaded via a `ret_cast_ok` knob
scoped to the record arm whose tail renders it); generic super() /
unbound-self calls as a new `super_generic` marker kind (parent spelled
via its own `to_cpp` -- the AST's exact call -- targs composed at
lowering via lc.render_type; the non-generic form keeps its F1 pin); the
same-T marker arg row (a NAME bound to the slot's own bare type param
passes bare); explicit-targs folding widened from statics to
module-qualified and builtin-module callees under the same equality pin;
TypedDict `.get` and membership mirrors (the composed value_or /
make_optional / operand-effect / has_value templates -- byte-verified
against the AST arms by direct probe); bytes literal and str/bytes SLICE
subscript receivers; owned-bytes-element lists admitted at
`_storage_call_ret`; protocol / bounded-tparam FIELD receivers
(`self.factory.make()` -- the family dispatch holds the record's tparam
bounds). Cascades: `bytes/rstrip_chars`,
`protocol_bound_record_with_bounds`, `protocol_user_bound_record`.

**Receiver/consumer harvest (+9).** Module-variable receivers
(`os.environ`) across the PINNED consumers -- method receiver
(`(*environ).update({...})`, with the dict/set literal rendering in
place at record-method container slots), native-slot arg
(`::tpy::__len__((*environ))`), and user-`__contains__` membership --
while the unpinned local-alias decl keeps deferring; `self`
callable-field invocations (`(*this).on_event(x)` via THIRSelf.deref);
view-family receivers generally (str/bytes fields, bytes-returning
calls, view-valued properties). Cascades: `sys_argv`, `stdlib/random`,
`async/stream_eof`, `async/stream_readuntil`,
`init_field_binding_dispatch`.

**The ratchet caught two more de-routings, same failure mode as the
setattr one:** the two old fence pins (bytes-split iterable, str-field
receiver) and the module-var len pin encoded reasons these widenings
removed -- each converted to a routing pin when the corpus/unit run
failed. The running lesson: a fence pin's stated reason is a contract;
when a widening removes the reason, the pin conversion is part of the
cell, not cleanup.

**Sema substitutes call-site params -- twice.** The `Own[TypeParamRef]`
ctor-rvalue row (arg-gate wave) and the "mismatched-T super arg"
boundary pin (review round) both died the same way: `resolved_
function_info.params` arrives SUBSTITUTED on the call node, so a
raw-slot row can never fire and a name-mismatch negative can never
compile. Check what sema stores before writing slot-shape rows against
the stub's declared params.

**Parked on named frontiers this session:** the builtin-type ctor pair
(`tpy.Int32(10)` -- the type-ctor machinery has no method-call seam),
the `{cpp}`-substitution pair (partial explicit targ lists that also
fail the equality pin), the `Rc.new` both-targs pair (the
`::tpy::Adapter<P, C>` method-targ spelling is the dyn-protocol
adapter machinery), `stdlib/urllib_parse` (chained off the family to an
is-binop shape), `set/optional_set_forward_ref` (chained to the
Optional[set] field write -- the field-write dispatch owes its
decomposition first), and the four `needs_copy` fork cases at the arg
gate (unchanged, awaiting the design decision).

**Review round (7 specialists over the whole 40-flip batch):**
codegen-correctness probed all four composed-template sites AST-vs-THIR
(TypedDict get/membership, super_generic, ret-cast, Ptr/native
cpp_template args) -- identical; safety and parity clean. Applied:
boundary pins for the non-scalar template arg and the substituted-T
super arg (the latter as an adjacent-shape routing pin -- see the sema
note above), the bytes-split unit byte-identity, the module-var
docstring consumer list, and two comment-hygiene fixes.

### needs_copy fork resolution (2026-07-29): the coercion-KIND mirror + the four arg-gate cases

The DEAD-END design fork at the Own-slot arg gate is RESOLVED by
measurement, and its four cases flipped (`enum/enum_name_owned_sinks`,
`async/future_drop_on_cancel`, `auto_move/same_stmt_aug_target_consume`,
`list/tplib_array_list_slice`). Dial 2701 -> 2705/3626, markers 921.
Full exec suite green (10156 passed, every case built+run).

**The probe.** `_gen_own_arg`'s `needs_copy` compares the RENDERED
coerce-wrapped arg against the bare escaped name -- render-string
identity, with no gate-time equivalent. A full-corpus probe (hooked
`gen_expr`, every TpyCoerce whose peeled inner is a simple TpyName --
598 renders across 3626 compiling cases) shows a pure coercion-KIND
classifier agrees with the string test on EVERY instance: 0
disagreements, 0 unknown kinds. The one nuance is the name-render
factor: 3 instances (two generator frame-slot `(*buf)` derefs, one
qualified module global) render non-plain, flipping the string test to
needs_copy=False on an identity-KIND chain -- so the mirror must also
require the name to render plain, a fact THIR owns natively
(`lc.pointers` / `lc.frame_slots` / narrowing / locals_). Verdict: the
KIND mirror is sound; the rendered-string variant was never needed.

**The mirror.** `_coerce_disposition` IS the KIND classifier (it already
mirrored the coercions.py lambdas); the change lifts its blanket
`Own[...]` reject for exactly one face behind an explicit `own_slot_arg`
knob (`strview_to_str` at an `Own` ARG slot -> materialize, the lambda's
`isinstance(b, OwnType)` branch). `_own_lvalue_temp_slot` peels a coerce
chain: all-identity over a NAME keeps the copy temp (scalar payloads;
the identity verdict certifies source/slot share one C++ type); any
wrapping link over a NAME is the needs_copy=False bare-rvalue face --
unwitnessed, still AST (boundary-pinned on `bigint_to_fixed_int`); over
a FIELD the AST test never runs, so the temp hoists with the wrapped
render as its init. New `Own[str]` payload row: the typed brace-init
temp (`std::string __tmp_N{...};` -- the view->owned CONVERSION), FIELD
sources only; `own_flush`'s stub-receiver exclusion gets the
`is_any_str_type` carve-out (a cpp_template callee binds lvalues
natively EXCEPT at str slots); the shape-blind `Own[str]` bare/convert
lowering arm now re-checks its own gate (`_str_owned_slot_arg`) instead
of trusting the widened gate -- without that it swallowed the field face
bare (caught by byte-diff, the exact latent-hole class the arm's comment
warned about).

**Companion rows** (each its own blocker in the four cases): the
subscript-aug-assign statement threads `allow_temps` into its value
(`d[len(b.items)] += k.take(b)` hoists `auto __tmp_N = b;` ahead of the
one setitem line; THIRSetItem already flushed); a user record's own
slice `__getitem__` overload renders the plain member call over the
BasicSlice initializer (`a.__getitem__(::tpy::BasicSlice{1, 4})` --
gen_call_from_fi's tail synthesized as the THIRStrSlice template;
NAME receiver, F1 record, Span result, non-stepped only -- the stepped
form boundary-pinned).

**Pins:** `test_thir_wave_needs_copy.py` -- identity-chain copy
(routing + face), wrap-chain-over-name fallback (boundary), str-field
temp, coerced-enum-name temp with the wrapped init, the while-condition
loop-head hoist (the restructured head is a flush position too),
aug-setitem flush with the same-statement-conflict copy, the record
slice member call (+ negative-bound render), stepped-slice fallback.
Faces: `argtemp.own_str`, `subscript.record_slice_method`.

### Generator-method family + Ptr native member (2026-07-29): the last method_call grindables

The two remainders the floor measurement left grindable, both landed:
7 flips (`iterators/gen_method_temp_receiver{,_simple,_union}`,
`iterators/gen_default_args`, `iterators/gen_method_typeparam`,
`native/native_include_propagation_transitive`, plus the
`native/native_property_via_ptr` cascade). Dial 2705 -> 2712/3626,
markers 914. Full exec suite green (10163 passed, every case built+run).

**The member-gen iterable row** (`_member_gen_call_iterable_ok`) was the
sole gate for all five iterator cases (`iterable_override` bypasses the
whole plain-method gate block), and its three restrictions were the three
blockers: (1) the bare-name receiver check -- widened to the ctor-rvalue
lift slice (`_gen_recv_ctor_temp`); the lowering renders the receiver as
a THIRArgTemp (`Counter __tmp_N = Counter(..);` + `__tmp_N.each()`,
mirroring `_gen_method_call`'s `is_temporary` lift -- the frame/peephole
captures the receiver by reference), flushed at the for-head brace scope
or the iterator-object decl, with nested ctor-arg temps (the union case's
`Dog __tmp_1`) riding `_RECORD_TEMP_FLUSH_USE` innermost-first; the
validator learned the receiver-position ArgTemp under flushable
positions. (2) exact arity -- replaced with the shared `_call_arity_ok`
(omitted trailing defaults ride the emitted C++ signature). (3) the
`fi.type_params` blanket -- relaxed to inferred-targ calls, whose
spelling the member tail's `method_targs` suffix already composed
(`f.items<int32_t>(42)`); the markers predicate takes `targs_ok=True`
per its own documented contract.

**The Ptr native member** (`native/native_include_propagation_transitive`,
`s.outer.inner.flag` off `s: Ptr[S]`): `_ptr_template_method_supported`
gained the cpp_template-less branch -- a plain @native MEMBER (renamed
method / property getter) on a `Ptr[record]` NAME receiver, deref-check
face only (`::tpy::deref_check(s).outer()`). The discriminator is sema's
`ptr_non_null` node fact, the exact test `_gen_method_call`'s is_pointer
arm reads -- NOT `needs_optional_runtime_check` (the Optional marker,
which a first probe wrongly assumed; the raw-Ptr deref-check spelling has
its own channel). The proven `s->outer()` face is unwitnessed and
rejects; notably the AST's post-access narrowing does not fire for the
@native property chain (a repeat access stays checked on both paths --
byte-pinned so an AST-side change surfaces loudly).

**Pins:** `test_thir_wave_gen_method.py` -- for-head + decl receiver
lifts (routing + `method.gen_recv_temp`), non-generator temp receiver
boundary, omitted-default + inferred-targ renders, the checked Ptr member
(routing + `method.ptr_native_member`) and the repeat-access byte pin.

### Indirection consolidation (2026-07-29): one resolver for a name read's deref

Hygiene refactor, no new admissions, zero flips by design: a bare NAME
read's indirection verdict now lives in ONE function --
`_name_read_deref(name, binding_type, lc, use)` (`lower/expressions.py`,
next to `_ptr_read_derefs`) -- and consumers state their POSITION instead
of re-deriving the verdict. The position rides a new `_ExprUse.
indirect_read` flag: a gen_expr_deref position (call args, container
elements, raise/decl name sources, `_INDIRECT_DEREF_COERCIONS` inners,
cpp_template/@native-free receivers-as-arguments) fully derefs any
pointer-local; every other position derefs only the always-indirect
bindings (frame slots, walrus slots, pointer-slot globals, F2d rebound
containers -- the old name-arm `container_ptr` block, now inside the
resolver). Deleted: ~10 post-hoc `_ptr_read_derefs` + `replace(lowered,
deref=True)` patches across `_lower_call_arg`/arg-temp rows/element
lowering/`_lower_ptr_name_src`/the raise arm, and `_lower_truthy`'s
independent `e.name in lc.pointers` re-derivation (now the resolver's
indirect verdict; the anti-stack guard against an operand that already
carries the deref stays). Full-corpus byte-diff green before and after
(10178 passed, dial 2713/3631 unchanged). Deliberate keeps: the deref
STRIPS at wrap-owning seams (`_strip_slot_leaf_deref`, the isnone/truthy
global-slot opt-outs) express "machinery applies its own indirection" and
consume, not re-derive; `THIRUnionArgLift.deref` and the THIRSelf
`self_is_pointer` patches are node-level facts outside the name arm.

**Surfaced en route:** the truthiness probes found a pre-existing AST bug
-- a U3-narrowed union member's record truthiness emits the bare alias
(`if (__x)`, invalid C++) because `gen_truthy_expr` keys the mode on the
DECLARED union while the read substitutes the alias (BUGS.md entry).
THIR's occurrence-typed mode would render the CORRECT dispatch, i.e.
silently fix the oracle -- so the truthy arm gate-rejects the composition
(`truthy.narrowed_record_mode`), per the reject-broken-oracle doctrine.
Un-reject when the AST is fixed.

**Pins:** `test_narrowed_record_mode_truthiness_stays_ast` (the new reject's
boundary, `test_thir_core.py`); the existing
`test_rebound_container_truthiness_derefs_once` anti-stack pin and the
whole routing/byte suite unchanged -- the refactor is corpus-verified,
not pin-verified.

### Generic-call arg-site wave (2026-07-29): the expr.call TRACK's biggest site emptied

Dial 2713 -> 2724/3631 (+11 flips), markers 918 -> 907. Full suite green
(10196 passed, exec + cpy). The fresh histogram put `body:expr.call` at 37
sole-blocker cases across TWELVE raise sites, the largest
(`_lower_generic_plain_call`'s arg gate, `expressions.py:7175` on this
tree) holding 10; the track ground that site to zero. Eight of its ten
cases flipped; the other two moved to their true blockers
(`tpy_executor_wake_dispatch` -> decl.slot_type,
`functools_reduce` -> the un-ported list-concat binop). Three bonus flips
(`await_task`, `channel_hold_both_ends`, `user_deref_chain`) fell out of
the same rows.

**What landed.** (a) The substituted-slot concrete tail gains the
`Own[@dynamic P]` erasure rows (coro factory + structural conformer), the
`Own[None]` monostate row, the `Own[value-tuple]` literal row (the shared
`_tuple_literal_arg` now unwraps the Own the lowering arm already
unwrapped), and the `Own[str]` str-literal face. (b)
`_own_lvalue_temp_slot` / `_own_scalar_rvalue_arg` resolve IntLiteralType
use-site types like `_resolved_scalar` -- a literal-seeded loop var or a
bare literal into an `Own[T]`-resolved-BigInt slot was rejecting on the
literal type alone. (c) The `_lower_call_arg` tail no longer
literal-retypes against an Own-WRAPPED slot: gen_call_arg threads the RAW
ptype, on which every literal target predicate is False, so the AST
renders `heappush<::tpy::BigInt>(h4, 42)` bare -- the retype was a latent
divergence waiting for its first witness. (d) `T()` defaults filling
omitted params lower as the new `THIRDefaultConstruct` node
(`int32_t{}`). (e) A conformer NAME binds a still-OPEN single structural
protocol slot bare (`drive_implicit<T>(t)` at `Awaitable[T]` inside a
generic body). (f) Both ctor arg loops now pass `nested_temps`, so a
call-shaped arg's OWN args flush at the enclosing statement
(`Holder(wrap(Int32(42)))` hoists the inner ref-slot temp) -- this
converted the nested mutated-slot pin from stays-AST to routes
(dualgen-verified byte-identical), the pin's stated reason being exactly
the threading gap removed. (g) The lambda param family admits list params
(`std::vector<T>&` via to_cpp_param); the len()-body witness routes, the
list-concat body still falls back whole.

**Lesson re-confirmed: the gate had drifted BELOW its own lowering.** Four
of the seven cells needed no new render at all -- the lowering arms
(monostate, make_adapter, tuple value render, the copy+move cascade)
already existed and were reachable from the concrete free-call gate; only
the generic tail's hand-copied subset had fallen behind. When a generic
case rejects, diff the generic tail against `_plain_call_arg_ok` before
reading any render code.

**Pins:** `test_thir_wave_generic_own_args.py` (15 units: routing +
byte-identity per row, pointer-repr tuple / owned-str-name / list-concat
boundaries); the converted
`test_ctor_mutated_slot_rvalue_nested_routes` (`test_thir_callargs.py`).

### Expr.call track, second grind (2026-07-29): ctor-arg + result-gate sites emptied

Dial 2724 -> 2742/3631 (+18 flips), markers 907 -> 889. Full suite green
(10212 passed, exec + cpy, every case re-verified). The two 6-case sites
that tied for the track's top after the generic-arg wave -- the record-CTOR
arg gate (`_record_ctor_arg_supported`) and the call-RESULT gate
(`_call_use_supported`) -- both ground to zero, plus the 3/2/2 mid-tail
sites (instantiation iterator args, the copy() faces, the free-call arg
gate's open-slot name). Five bonus flips (`sorted_key_user_type`,
`gen_template_no_leak`, and three others) fell out of shared rows.

**What landed.** Ctor-arg rows: `copy(name)` into `Own[record]`
(`Holder(copy(b))` -> `Holder(Box(b))`); func-ref / callable-value NAMEs
into Callable ctor slots; `copy(src[i])` of an OPEN-T element into
`Own[T]` (`Owned<T>(T(__getitem__(src, 0)))` -- new `_copy_open_elem_arg`
+ THIRCopy arm); the @dataclass default_factory fill (an empty container
instantiation into an `Own[container]` slot, STORAGE use). Result-gate
rows: @cpp_template record rvalues at STORAGE (`Point p = Point{};`);
DISCARDED @native record rvalues (`open(missing)` for its raise);
@error_return record rvalues at the field-RECEIVER position and the raw
statement bind (`error_return_raw` threads into the gate). Iterator
rvalues at instantiation args: `copy_iter(it)` gets its own arm
(`::tpy::copy_iter<Elem>(<it>)`) and the module-qualified GENERIC
generator factory routes (`list(heapq.merge(a, b))` -- the
`_marker_call_kind` generic+generator reject lifted under `generator_ok`,
the vararg pack's temp riding the statement flush). The copy() builtin
gains the open-T CALL-rvalue face (`U(f(init))`) and the Span NAME face
(a view copy). A Callable FIELD read binds callable slots bare; a
borrow-returning lambda body lowers under BORROW_BIND
(`map(lambda p: scale(p, 2), pts)`).

**The byte-diff caught a live AST miscompile.** Pinning the
callable-field row with a param named `f` shadowing a module function
`f`: sema resolves the BINDING (CPython-correct), but the AST emit's
registry-first ordering renders the MODULE function's call with args
zip-truncated to its params (`return f();` for `return f(v)`). Filed in
BUGS.md; THIR gate-rejects the composition (`call.callable_shadow`) per
the broken-oracle rule.

**A row written and REVERTED, as the doctrine demands.** The
same-wrapper method-call rvalue arg row (`show(v.inner.get())` at a
recursive-union slot) had no routable witness: every reachable shape
dies downstream (the corpus case at the F1 union-type-arg fence, the
constructed probes at the wrapper-result gates). Admitting it would have
been unwitnessed surface, so it came back out in the same commit.

**Residue, honestly named.** `set/set_comp_owned_move` needs a
comp-FILTER flush point (arg temps inside the comprehension loop body);
`union/union_mutual_mixed` sits behind the deliberate F1 union-type-arg
spelling fence; `calls/ref_collect_no_source_clobber` moved to a
str-field-over-subscript read. The track's remaining sole-blocker tail is
six 1:1 singles (expr callee, isinstance tuple, cross-module dup-name
union, gen-proto param, os.walk kwarg, argparse widths).

**Pins:** `test_thir_wave_result_gate.py` (16 units: routing +
byte-identity per row, template-at-VALUE / er-at-print / concrete-elem
copy boundaries, the callable-shadow reject).

### Decl-slot track, first grind (2026-07-30): the slot gate's mechanical families

Dial 2743 -> 2760/3632 (+17 flips), markers 889 -> 872. Full suite green
(10253 passed, exec ran all 3631 cases). The fresh census put
`stmt.var_decl:decl.slot_type` at 51 sole-blocker cases / 166 units --
the corpus's largest single site -- and this grind took its mechanical
families to 107 units. The prior "needs the form design, not gate
widenings" verdict (2026-07-28) turned out to cover only the
tuple/ptr-repr half: five of the six clusters fell to ordinary rows.

**What landed.** (a) `_var_decl_type` resolves pending containers
through the shared sema record -- the root cause behind every bare
container alias (`b = a`) reading as an unclassifiable PendingListType
at the gate. (b) The reference-select family: container/record and-or
with Python operand semantics (`_lower_container_select` -- the
`__len__`-truthy ternary aliasing the chosen operand; a plain user
record folds `true`; an rvalue RHS rides the hoisted
`std::optional<T> __logical_slot_N` pointer-select, lazily emplaced so
short-circuit holds), the container ternary (bare arm render, spelled
list-literal arms), the all-rvalue select/ternary as a plain-copy value
decl, and the REF_ALIAS emit arm's render-then-flush fix so a chain's
`auto&& __tmp_N` hoist precedes the alias decl. `THIRValueSelect` now
carries `truthy_mode: TruthinessMode` (the review folded the boolean
pile into the existing enum) and `ptr_select_cpp`. (c) The
iterator/protocol rows: the `__next__`-keyed native-iterator value slot
(`SpanIter`), the structural-protocol / Self `auto` slot, the
`@native(function=True)` free-form method call (`b.__iter__()` ->
`::tpy::__iter__(b)`), the SpanIter instantiation (pre-substituted
`{cpp}` ctor template), protocol-result admissions at the free-call /
print gates, the Own[Self]/protocol-result storage sink in template
bodies, the raw protocol-operand binop (`(a + b)`; `//` maps to `/`
per the AST arm -- review-caught, a verbatim `//` is a C++ comment),
the open-T element alias (`T& v = __getitem__(this->items, ...)`), and
the container FIELD alias (`const std::vector<T>& xs = this->tags;`).

**The review paid.** codegen-correctness caught the `//` render on an
admitted-but-unwitnessed shape (invalid C++, no corpus witness -- the
skill's dualgen-the-boundary rule exists for exactly this); test-coverage
caught eight byte-identity-only pins missing their routing half (a
fallback body is byte-identical BY DESIGN, so those pins could not
detect re-rejection); cpython-parity found the `it = iter(c)`
returns-self aliasing divergence -- PRE-EXISTING, shared by both paths
(`auto` strips the helper's reference and copies), concealed by the
flipped case's read-only shape; filed in BUGS.md rather than fixed here
(both paths must change together, snapshot-churning).

**Residue, honestly named.** The tuple family (11 cases: storage-tuple
call decls, whole-tuple last-use move unpacks, per-element-Own
rebind/realias) is the next sub-track. Fenced or deeper singles:
`span_iter_arraylist` (@auto_readonly multi-overload `__iter__`),
`const_borrow_optional_ptr_recv` (the BUGS.md narrowed-receiver
const-drop fence), `generic_param_element_rebind` (open-T `T*` reseat
rung), `warn_borrow_chain_through_reassign` (reassigned record
subscript reseats), `inheritance_multi_base_field_readonly`
(class-qualified base-field alias source), tplib's Box/Rc str-lvalue
Own-temp decls, and the or/ternary shapes over Optional operands
(`ternary_optional_mixed_form`, `warn_auto_move_optional`).

**Pins:** `test_thir_wave_refselect.py` (11), `test_thir_wave_iterdecl.py`
(6), `test_thir_wave_protoslot.py` (7), plus the TestNameAlias additions
and two fence conversions (container ternary decl, record select).

### Decl-slot track, second grind (2026-07-30): the own-record tuple family

Dial 2760 -> 2764/3632 (+4 flips), markers 872 -> 868; decl.slot_type
107 -> 99 units. Full suite green (10261 passed, exec). The
`tuple[Own[A], Own[B]]` family: storage decls from calls, the `t[N].field`
value read (subscript-receiver family + record-field receiver widened
through the Own unwrap, nested tuples recurse), and the NAME-source
unpack holder binds (NAME_MOVE at last use / NAME_COPY otherwise). The
context's own "DELIBERATELY PARTIAL" seeding comment predicted the exact
gap this hit: the owned-movable tuple-PARAM branch of seed_param_locals
was unmirrored, and the &&-param source diverged (copy vs move) the
moment a consumer could see it -- caught by the pin's byte-diff, fixed by
mirroring the branch. One fence converted (own name source), boundaries
pinned (ternary source, nested chain read).

**Track closed at its mechanical floor.** Remaining decl.slot_type
sole-blockers: the reassignable BORROW_TUPLE family (the REGISTERED
deferred F3 cell -- not a wave row), and per-case singles (the BUGS.md
narrowed-receiver const-drop fence, @auto_readonly multi-overload,
open-T `T*` reseat rungs, class-qualified base-field aliases, tplib
Box/Rc str-lvalue Own temps, Optional-form ternaries). The neighboring
tags are likewise floored or design-shaped: expr.call / expr.method_call
residues are the documented 1:1 tails of their closed tracks,
return.slot_type waits on the form design, container_literal is four
unrelated shapes, stmt.match is parked. The next wave needs either the
F3 deferred cells or a fresh probe of the sub-10-case tags.

### Return-slot rows (2026-07-30): the resumed grind past the premature floor

Dial 2764 -> 2773/3632 (+9 flips), markers 868 -> 859; return.slot_type
111 -> 99 units. Full suite green (10267 passed, exec). The first floor
call leaned on the 2026-07-28 "return.slot_type waits on the form
design" verdict WITHOUT re-probing -- the same stale-verdict mistake
this session had already disproved for decl.slot_type, and the user
caught it. The re-measure (a per-case return-slot dump, the census
script's frame var being wrong for this site) named mechanical rows the
session's own families unlock:

**What landed.** The structural-protocol / native-iterator return slots
join ret_supported (the C++ signature already spells the concrete/auto
type; values render bare through the call arms). The container-FIELD
arg into a structural protocol slot rides the ITERABLE result family
(`return iter(self.items)` -> `::tpy::__iter__(this->items)`); the
protocol-slot arg gate's F1-record field row widened to containers.
The container return families gain bytearray. The review's one finding
(the SpanIter return slot admitted but unwitnessed) pulled the thread
one level down: the Span METHOD result (`self.__span__()`) had no row,
and adding method.span_ret both witnessed the return arm AND unfenced
the @auto_readonly clone pair -- the overload gate had admitted the
pair all along; the Span result was the only blocker. Nine flips:
for_user_iter_protocol_return, warn_user_container_method_borrow,
bytearray_return_own, for_span_protocol, plus five collateral
(async_await_proto_param_self, cross_module_refclass_ctor,
readonly_const_method_mutable_param, ctor_field_init_containers,
traits_records).

**Residue, honestly named.** return.slot_type's remaining sole-blockers:
the Own[tuple] returns (the registered tuple_to_storage_move F3 cell),
the Any-coerce return, generic Optional[T]/Span[T] returns, the
resumable Poll tuple. Opened but not taken: the module-qualified
builtin-ctor fold (`tpy.Int32(10)` -> `10`, 2 cases,
method.marker.builtin_module.ctor) and the module-qualified generic
free functions (3 cases) -- the next method_call rows; container_literal
stays four unrelated shapes.

**Pins:** `test_thir_wave_retslot.py` (6: protocol/SpanIter/auto_readonly
returns routed, bytearray own+borrow+field returns, the Own-NAME-source
fence), `test_thir_wave_owntuple.py` (4), converted fences in
test_thir_tuples.py / test_thir_containers.py.

### Gate D3 -- skeleton ownership + the assembled cutover checklist (2026-07-30, decided)

The last open design gate from the 2026-07-28 inventory. Decision, in one
line: **this migration's end state is bodies-only -- the structural skeleton
stays in `codegen_cpp` as the permanent printer layer, and ownership never
transfers.** "Skeleton" means: the module driver (`generator.py`), headers
and emit ordering, signatures, record/protocol/enum drivers, the ctor
member-init driver, the resumable + simple-generator frames at their leaf
seams, and type rendering. CLAUDE.md's "the AST codegen is DELETED" is
re-scoped to the AST *body* emitters in the same change-set as this entry.
Folding the resumable skeleton into the IR (the IR_DESIGN.md "emitter
becomes a printer" trajectory) is a named FUTURE project, deliberately not
part of this migration -- architectural cleanups do not ride migrations.

**The finding that gives D3 teeth: the skeleton calls the body expression
emitter directly.** `records.py:403` renders class-constant defaults via
`gen_expr`; `functions.py:1907` renders Final-global initializers via
`gen_expr` -- and A3 measured 97% of codegen functions shared between
fallback bodies and structural emission. While any skeleton position calls
`gen_expr`, cutover commit 2 cannot delete the expression dispatch: that
would be the permanent hybrid at expression granularity. (Param defaults
are already clean -- they render through the dedicated constant renderer
`default_to_cpp`, not `gen_expr`.) Hence a new pre-cutover work item:
inventory every skeleton -> `gen_expr`/`gen_stmt` call site, and per site
either route it through THIR lowering or move it to a small dedicated
renderer. The cutover gate is "the skeleton calls zero body-emitter
helpers" -- that is what makes commit 2 a wholesale deletion plus dead-code
pruning instead of archaeology.

**The cutover checklist, assembled in one place** (previously scattered
across gate entries; all of it is parked until markers approach zero --
none of it moves the dial, and body-lowering waves cannot invalidate it):

1. A1: `no_thir.txt` markers -> 0 (dial 2773/3632, 859 left at this
   writing) -- the standing wave work.
2. A5: stdlib fallback -> 0 (289 at the 2026-07-28 baseline), then
   re-measure with a using corpus -- the baseline is a FLOOR (import-only
   entry programs never attempt monomorphizations or resumable frames).
3. Interop corpus: its own dial to migrated (tallied separately).
4. The skeleton call-site inventory above, ground to zero.
5. `faces.py` call-site increment fix (an arm that witnesses before it can
   raise reads as covered) + pins for the nine zero-witness faces -- both
   filed in TODO.md; after teardown faces.py is the primary internal net,
   and a detector reporting false coverage is worse than none.
6. The two-commit cutover per Gate D4: flip snapshot authorship to THIR
   with the `git diff tests/cases` EMPTY proof, then delete the body
   emitters in a commit touching no `expected/` file.
7. Teardown per the D4 inventory. Post-cutover, `ThirUnsupported` is an
   internal compiler error: new language features land sema + THIR lowering
   together -- there is no fallback to hide behind.
8. Adversarial dualgen sweep over the POSITION-ENUMERATION matrices before
   the AST path goes: the wide pointee accessor's consumer positions and
   `_name_read_deref`'s admission classes have only ever had their
   completeness demonstrated by the corpus byte-diff (three waves each
   caught a missed position reactively) -- at cutover that detector is
   deleted, so the sweep is the last chance to prove the matrices closed
   (2026-08-04 retro; moot for `_name_read_deref` if the inversion filed
   in TODO.md lands first).

What changed NOW rather than at the checklist: `--thir-codegen` implies
`--thir-stdlib` (the stdlib oracle rides every measurement run instead of
depending on someone remembering a flag; ~10-15% wall on a comp-only run)
and the measurement run prints the migrated-case dial it previously
omitted. Grind economics stay settled per D4: byte-identity holds for
every cell until cutover commit 1 -- the diff-empty proof is the only
cheap proof the cutover has, and output-equivalence would destroy it.

### stmt.match traced probe + nested sub-pattern cell (2026-07-30)

Dial 2773 -> 2776/3632 (+3 flips: `match/nested_field_none`,
`match/record_union_field_pattern`, `union/union_field_nested_variant`),
markers 859 -> 856. Full exec suite green (10271 passed, all 3631 cases
built+run). The design-queue item "stmt.match per-arm type facts (63u,
best flips-per-design-hour)" was MISPRICED -- the stale-verdict failure
mode again, this time on a DESIGN item: the 2026-07-27 probe drilled one
Literal subject and generalized. A settrace probe over `_match_route`'s
inner helpers (innermost None/False return per fallback unit; script
pattern worth reusing for any many-return-site gate) attributed 62 of the
tag's 68 units:

| units | cases | inner site | what it is |
|---|---|---|---|
| 27 | 23 | `_match_strategy` fall-through | recursive-alias-instance / generic / dyn subjects -- the M4c-generic frontier, all multi-blocked, NOT match work |
| 18 | 8 | `_match_keywords_ok` nested rejects | nested class sub-patterns -- a render recursion, NO facts machinery |
| 6 | 5 | `_match_strategy` LiteralType early-out | the actual facts slice; 2 of 5 cases co-blocked on the overload mangled-path design |
| 3 | 3 | `_f1_record` under `_union_arm_ok` | mutual-recursion union members |
| ~8 | ~8 | singles | hoists, rvalue subject, ptr captures -- existing parks |

**The facts fear dissolves on inspection:** sema already stamps the
Literal-driven overload resolution on call nodes (`resolved_function_info`
with LiteralType params; the AST derives `f__lit_r` from it at the call
site), so "facts can rewrite arm bodies" reduces to a mangled-callee
SPELLING -- the parked overload design -- plus an arm-scope retype the
union tier already performs. Design queue re-scoped accordingly.

**What landed: the nested-sub-pattern cell, record tiers.** The AST's two
recursions mirror onto the existing pair model: CONDITIONS compose
(prefix, suffix) around the tier's runtime base -- a union-field guard
folds `std::holds_alternative<T>` / `std::get<T>` INTO the pair, so
arbitrary nesting depth costs nothing -- while BINDINGS switch base to the
guard's `__field_` extraction temp (mode `field_alias`; its spelled name
derives from the runtime base AT EMIT, mirroring `parent_sfx`) or an `as`
name, via a base-name map threaded through the record-tier emits. One
ordered row list keeps the AST's single-walk interleaving. Admission is
record-tier-only (`nested_ok` -- the union/poly/optional tiers' emits have
no map yet and keep rejecting, pinned). `_match_record_arm_always` now
uses the AST's own recursive `_sub_has_field_condition` -- its
literal-only check would have mis-read `Outer(inner=Inner(child=None))`
as an always-arm. Chain-walked one arm past the cell: the bare str
literal at a value-union ctor slot joins `_value_union_temp_slot` (the
int-literal row's sibling), unmasked by `record_union_field_pattern`.

**The two-level guard chain is admitted with zero corpus witnesses** --
the recursion composes it for free -- so its byte-identity pin
(`test_two_level_union_guard_chain_byte_identical`) is the arm's only
check; dualgen smoked interleaved captures around a temp, the guarded
tier, depth-3 paths, and bool/float/str members (zero fallback,
identical). The old union-field-guard fence converted to a routing pin;
as-of-literal, `_ as x`, or-alt nesting, and the union-TIER boundary keep
fences.

**Remaining rungs at the site, honestly named:** union-tier nested
admission (the switch/guarded-union arm walks + or-alt body duplication
-- `nested_type_pattern_combos`/`edges` and `union_nested_type_pattern`
each hold 3 match bodies there, plus an expr.call chain each), the
optional-tier as-form guards (`opt_wrapper`), and the Literal slice
above. Note for the union-tier rung: those cases' nested READS are
parity-blind (they only print) -- the reference-type mutation rule
applies when their exec coverage is extended.

### Nested sub-patterns, union-tier rung (2026-07-30)

Dial 2776 -> 2778/3632 (+2: `match/union_nested_type_pattern`,
`async/asyncio_queue_nocopy` -- the latter collateral from the ctor
rows), markers 854; stmt.match 63 -> 42 units. Full exec suite green
(10272 passed, all 3631 built+run). The union SUBJECT tiers admit the
same recursion: the switch tier is cond-free BY ROUTE (field-condition
patterns go guarded; the gate admits only compile-time type guards
there), the guarded tier composes conds around `__case_` and threads the
base-name map through both binding positions, and the optional PARTITION
tier came along for free through its record-tier reuse (only the
optional CHAIN tiers keep the fence, pinned).

**The finding worth keeping: sema stamps `resolved_type` on a nested
class sub only when it had FIELDS or a UNION to resolve** -- an
exact-match compile-time guard (`Box(value=str())` on a non-union field)
carries None, and its bound type must be re-derived through the
pattern's type-arg substitution (`substitute_type_params_simple`,
mirroring sema's `_build_type_subst`). The first gate draft required
`resolved_type` unconditionally and silently kept all three probe cases
rejecting -- the reject read as "union tier" when it was really "sema
does not stamp what you assumed"; check what sema stores on the NODE
before gating on it (the standing lesson, sub-pattern edition).

Chain rows: `ctor.own_str_literal` (a str literal passes BARE into an
`Own[str]` ctor slot -- the auto-move cascade never fires for a
prvalue) and the nested ctor-arg tail peeling `Own` over an eligible
scalar. `nested_type_pattern_combos`/`edges` each sit at ONE remaining
blocker: ctor temp rows at a flush-less union/optional storage DECL
position (`temps_ok=False` at the decl init while the AST flushes
`__tmp_N` before the decl) -- that is the registered decl-form
frontier's flush-rights question, named and not chased.

### stmt.match Literal slice (2026-07-30)

Dial 2778 -> 2784/3632 (+6 flips: `match/literal_type_{str,int}`,
`match/warn_nonexhaustive_literal`, and three collateral
`calls/literal_local_*` cases), markers 848. Full exec suite green
(10276 passed). Literal subjects dispatch on the base tier; arm facts
register into the new `lc.literal_facts` scope; LiteralType becomes
transparent to THIR's own classification (scalar peel; a str-based
Literal resolves to the family VIEW type -- its values are
static-lifetime literals, so the AST gives such bindings string_view
storage everywhere).

**Two divergences found and closed by the standing nets, one lesson
each.** (1) `calls/literal_local_from_literal_call` diverged owned-vs-
view under the first draft (base-type resolution) -- caught by the
LOCAL targeted run right after flipping; the check-flip candidate list
measures FALLBACK only, so a flip must always be re-verified with the
case's own snapshot compare before the marker goes. (2)
`calls/narrow_literal_eq`'s `nested_fold` diverged on the FULL corpus
run: the fold fence's first draft keyed on match-arm facts, but the
AST seeds `ctx.literal_facts` from `==`-NARROWING too -- the fence now
keys on the operand's TYPE (declared or read LiteralType) as well.
The general form of both: a "transparency" widening changes every
consumer of the classification at once; price the FORM axis (view vs
owned) and every FACT CHANNEL (match arms vs narrowing) before
trusting the first green probe.

**A third lesson, from the shared-row misstep:** widening
`_shared_pass_through_arg`'s first row to `_resolved_scalar` broke
FIVE pinned arms across native/protocol/resumable rows -- row
identity (which row admits = which face witnesses = which render arm
fires) is load-bearing; a new admission belongs in a NEW slot-keyed
row whose domain no existing row admits (`_literal_scalar_slot`).

Residue, named: `literal_type_dead_branch`/`narrowing` park on the
overload mangled-path design exactly as scoped (their bodies reject at
`sig.overload_set` + `call.literal_overload`, byte-identical); the
str-based Literal-slot pass-through ARG row (a fact-typed name into a
`Literal[str-...]` slot) falls back byte-identically -- a small row
when a paying witness appears; the 5+-alternative discriminator
switch against a Literal subject stays AST (pinned).

### Element-borrow pointer decls + classmethod-through-instance (2026-07-30)

Dial 2798 -> 2803/3651 (+5), markers 853 -> 848. Full exec suite green
(10329 passed, all 3650 cases built+run). Two cells, both opened by
splitting a coarse blocker tag by the thing it was actually keyed on
rather than by sampling its cases.

**`decl.slot_type` is ONE raise site with a TYPE-shaped split.**
`probe_sites.py` attributed all 28 of its sole-blocker cases to a single
line -- the value-slot gate's fall-through -- which reads as "no site to
attack" until you notice the gate is the TAIL of the borrow cascade. The
useful measurement was a second trace over `_borrow_local_binding`
recording the shared classifier's verdict AND the `return None` line
taken: 18 OTHER, 6 REF_ALIAS + 5 POINTER + 2 OPTIONAL_TO_PTR at the
field-receiver fall-through, 3 REBIND_SLOT. THE LESSON: when a tag maps
to one site, re-probe the PREDICATE that site consumes -- the histogram
you need may be over verdicts, not over line numbers.

**Cell 1, the POINTER row (3 cases).** `p = ps[0]; p = ps[1]` binds a
reseatable `T*`, and the RESEATS already routed
(`reseat.subscript_elem`) -- only the decl was missing. The
FormConvert-over-the-element draft hit the validator immediately: a
container-element read is ALREADY borrow form, so the convert is the
no-op node -- the same trap the bare-name POINTER arm documents. The
`&(...)` went into a new PTR_ADDR arm of the pointer-local DECL emit
(the UNION_ADDR/PTR_ADDR-reseat precedent). Two chain rows followed: a
later RVALUE reseat needs the decl to pre-declare its `std::optional<T>`
rebind slot (the AST's lvalue-init branch), and the open-T twin's return
needed its own deref arm -- an open-T slot never reaches the record
return ladder (`_f1_record` is False for it), so the generic tail was
rendering the raw pointer.

**Three pins converted, one shape rebuilt.** Two boundary pins stated the
exact reason this cell removes; the third
(`test_for_lowering_reject_falls_back_at_sync_boundary`) was a FALLBACK-
MECHANISM test that merely happened to use the newly-routing shape --
its shape was swapped for a nested-container element (still alias-only)
so the mechanism keeps being tested. Worth restating: re-run every pin
whose reason the widening touches, and read what each pin is FOR before
converting it.

**Cell 2, classmethod-through-an-instance (2 cases).** `p.origin()`
renders as an ordinary member call; the fi-kind gate rejected it because
the PARSER sets `is_staticmethod=is_staticmethod or is_classmethod` and
clears `has_self`, so both `fi.is_staticmethod` and `not fi.is_method`
fire on a classmethod. Keying the two apart routes it; a plain
`@staticmethod` through an instance carries the same bit, is a different
shape, and keeps rejecting (pinned + dualgen-verified).

**One widening reverted by the standing net.** A target-less float-literal
list (`math.dist([1.0, 2.0], ...)`) seeds its element slot from the first
literal and never resolves it, so the slot is a `FloatLiteralType` and the
family lands on `other`. Admitting it as "the base scalar slice under
another spelling" was wrong twice over: THIR's element render emitted
`std::array<1.0, 3>` (`FloatLiteralType.to_cpp()` returns the literal
digits -- the pending-leaf hazard the borrow-tuple hoist gate already
documents) and the AST's arg-temp hoist was missing besides. `probe_one`
caught both before commit. A pending leaf is not a spelling variant.

**Residue at `decl.slot_type`, honestly named.** The remaining rows are a
1:1 tail, each a distinct emit: the `optional_to_ptr` lift over sources
the OPTIONAL_TO_PTR gate does not carry (a reassigned optional, a tuple-
element storage optional, a mixed-form ternary -- 3 cases); a bare
pointer-optional NAME copy decl (`const Point* q = a;`); a borrow-tuple
decl from a storage lvalue (its reseat already routes, but the field
flavor's source is const and flips every element pointer -- the deferred
const rung); a property-getter storage-optional slot; a base-class-
qualified field read; a user `__getitem__` borrow return; a readonly
borrow-tuple element. The two `const_borrow_optional_ptr_recv` /
`weak_cycle_breaks` rows sit behind `_const_exact_field_receiver_ok`,
whose comment fences them against a BUGS.md entry ("borrow locals off a
narrowed Optional receiver drop inferred constness") that IS NO LONGER
IN BUGS.md -- the citation outlived the entry. That alone does not prove
the AST bug is gone, so the fence stays until someone reads the AST's
current emit for the shape; filed in TODO.md, worth 2 cases. The habit
worth keeping: a fence's cited justification is evidence, not proof --
re-read the cited entry before trusting OR removing the fence.

At `expr.method_call`, `probe_sites` splits 15 sole-blocker cases into
two 6-case sites: `_record_method_call_supported` (of which the
classmethod pair is now cleared; the rest are a receiver-family row, a
`method.ret_type` row, and one case where sema hands a generic record's
method an fi with `is_method=False` -- worth understanding before
mirroring) and `mk is None` in `_marker_call_kind`, which splits again
into module-qualified record ctors (2), unresolved-fi generic module
calls (2), and generic statics (2).

### Borrow-tuple literal return (2026-07-30)

Dial 2803 -> 2809/3651 (+6), markers 848 -> 842. Full exec suite green
(10334 passed). `stmt.return`'s NINE sole-blocker cases all sat at one
raise line, and the fix was one arm: `return (n, p)` at a borrow-tuple
return slot had no route to `_lower_borrow_tuple_literal`, even though
that builder already carried the NAME / SUBSCRIPT / already-pointer
element rows for the decl and call-arg sinks. Seven of the nine went
clean on the arm alone; the remaining two chain on a Span-element read
(`_container_elem_family`'s `span_ok` fence -- a NAMED deferral, left
alone) and a field-element row.

**The measurement lesson, restated with a second instrument.** For
`decl.slot_type` the useful histogram was over the shared classifier's
VERDICTS, not line numbers. Here `probe_sites` pointed at
`statements.py:3354` for `expr.list_comp` -- which is the generic
`_lower_stmt` re-raise wrapper, not a site at all. When a tag's "site"
is a re-raise or a cascade tail, the number is noise; go to the
predicate the site consumes (`probe_pred.py`, a frame-scoped line trace
over one function, now in the wave toolkit's shape).

**A face witnessed too early makes the coverage metric lie.** The first
draft witnessed `ret.btuple_literal` BEFORE calling the builder, so a
body whose element fell outside the builder's slice still counted the
face and then fell back. The boundary pin caught it. Witness after the
node exists.

**One pin restated rather than converted.**
`test_optional_record_element_deferred` asserted that a
`tuple[Int32, R | None]` return stays AST because `_value_opt_scalar`
rejects the element. That reason is STILL TRUE -- the value-tuple family
does not claim it -- but the BORROW-tuple family now does. Converting it
to "routes" would have thrown away what it was actually pinning, so it
now asserts the observable form instead (`std::tuple<int32_t, R*>{a,
nullptr}`, not a `std::optional<R>` element). A pin whose REASON
survives but whose CONCLUSION flips is not the same thing as a pin the
change obsoletes.

**Two cells opened and abandoned, both recorded so nobody re-prices
them.** (1) A target-less float-literal list (`math.dist([1.0, 2.0],
...)`) seeds its element slot from the first literal; admitting the
`FloatLiteralType` slot as "the base scalar slice under another
spelling" emitted `std::array<1.0, 3>` (`to_cpp()` returns the literal
digits) AND dropped the AST's arg-temp hoist. (2) A value-tuple
comprehension ELEMENT slot: `_comp_elem_slot_ok` excludes the family
because `_lower_container_elem` branches on the element NODE for it, so
a node-gated arm looked right -- but the paying case
(`list/comp_element_copy_warn`) has a `tuple[Int32, Cell]` element,
which is pointer-repr, i.e. the F3 borrow/storage form frontier rather
than a spelling gap. The exclusion was correct as written.
[SUPERSEDED 2026-07-31: the thir-grind-loop branch landed that exact
case via the Own[ptr-Optional tuple] consuming/storage rows (the
non-move tuple_to_storage NAME copy + the storage-context CONST_REF
literal ladder) -- the F3 pricing above no longer blocks it.]

**Residue at the tags measured this session, honestly named.**
`assign.field_write_shape`'s 7 sole-blocker cases are 7 distinct shapes
in two loose families: an Optional FIELD written with a concrete
non-None value (str literal / container name at last use / tuple
literal -- three different value families, one case each) and a field
write off a SUBSCRIPT receiver (a one-level-field-of-name subscript
plus a doubly-nested one; and a `varargs<T>` receiver). `expr.list_comp`
splits into an array route, an owned-`String` element slot, and the
pointer-repr tuple element above. None of these is a site; they are the
1:1 tail the doctrine predicts once a tag's big rungs are cleared.

### Tail cells: Optional[str] field write, consuming methods, record needle (2026-07-30)

Dial 2809 -> 2815/3651 (+6 across three cells), markers 842 -> 836. Full
exec suite green (10345 passed). All three came out of the residue the
previous entry named, and the session's measurement lesson sharpened into
a rule of thumb:

**A tag concentrates when it names a MECHANISM and fragments when it names
a SHAPE.** `sig.special_callable` (6 sole-blocker cases) turned out to be
ONE flag, `is_consuming`. `stmt.return` was ONE missing route. By
contrast `assign.field_write_shape` (7 cases) is 7 distinct target/value
shapes, `stmt.aug_assign` (6) is 6 unrelated targets, `expr.list_comp`
(8) is four routes, and `if.cond_binop.is not.optional_other_nonetype`
(5) splits by the Optional's INNER type (Callable / dict / Own[record] /
subscript source). Probing a shape-named tag hoping for a mechanism is
how a session burns its measurement budget.

**Consuming methods (+2) -- the cell worth reading.** `is_consuming` sat
in a blanket `sig.special_callable` reject, but a consuming body differs
from an ordinary one in exactly ONE way: `self` is an rvalue ref, so
returning one of its fields moves. The `&&` suffix and the
`__tpy_owned_ = false` prologue a `__del__` record gets are both
structural-emitter lines THIR already shares -- verified, not assumed.
The move is applied at a chokepoint BEFORE the return ladder: the AST
wraps the FINAL return expression, so every specialized arm (record /
optional / tuple / container) would otherwise have to re-apply it and a
missed one drops a move SILENTLY. Only the plain value families are
threaded; everything else rejects by name. That is the shape of a safe
widening when the AST applies a transform at a point the mirror does not
have.

**A pin whose premise my own change removed.** `TestAutoOwnCloneCarveout`
asserted "no overload_set fold for the pair", and its comment named the
dependency outright: the consuming twin was "already rejected as
is_consuming" BEFORE reaching that gate. Dropping the blanket reject let
the twin reach it and fold on its own merits. Probed to confirm it is the
TWIN and not the borrowing clone (the carve-out's actual subject), then
restated the pin to assert exactly one fold attributable to the twin.
Third pin-restatement of the session; the recurring shape is a pin that
observes a consequence rather than its subject.

**Optional[str] field write (+1).** `_value_opt_scalar` excludes the str
family for a PARAM-shape reason -- the `optional<string_view>` vs
`optional<string>` arg split -- that a FIELD has no equivalent of, its
storage always being owned. The new predicate is scoped to field sinks
and says so. LITERAL values only: a NAME or binop source raises the
owned-vs-view question the literal row never has.

**Record-rvalue membership needle (+2).** A needle is a value position,
so a ctor rvalue renders bare inside `contains(...)` exactly like the
NAME row already admitted; the row was name-only for no reason the render
supports.

**One cell backed out at the validator.** The container twin of the
Optional-field write (`h.s = initial` at a `set[T] | None` field, moving
at last use) looked like a sibling one-liner. It is not: a container name
reads BORROW form and an `optional<container>` field sink is
pointer-lifted, so THIR's validator rejected the plain assign --
correctly. The give-away was reaching for `uses_pointer_repr()` on a
FIELD type at all: that query describes the BORROW form, and a field is
always storage form. The row is the F2b frontier and needs its own cell.

**Residue, still honestly named.** `assign.field_write_shape` keeps its
Optional-container row (above), its tuple-literal row, and two
receiver-DEPTH fences (a field-of-name subscript nested twice; a
`varargs<T>` receiver, whose subscript spells `args[cast]` rather than
`__getitem__`). The three `auto_own[Self]` cases chain to
`sig.overload_set.ret_mismatch` -- the clone-pair frontier, where the
pair's two members have different return types. (Superseded: the
clone-pair carve-out landed the next day. Only `auto_own_basic` was
actually blocked there; the other two chain to `return.slot_type`.) `expr.call` (113 distinct
blocked shapes) remains the largest tag never measured this session.

### auto_own clone pair + open-T consuming return (2026-07-30)

Dial 2815 -> 2816/3651 (+1: `auto_move/auto_own_basic`), markers 836 ->
835. Full exec suite green (10352 passed). Two halves of one cell, landed
together on purpose.

**The carve-out.** A `self: auto_own[Self]` method expands into a
borrowing + consuming pair, and the overload gate already exempted such a
pair: `_clone_auto_own` gives each half an independent body (the
consuming one deep-copies), so neither can hijack the other with an
unspecialized impl. Only the borrowing half carried a flag, so its twin
folded at the gate on the pair's return mismatch. The fix sets a
symmetric `is_auto_own_consuming_clone` at CONSTRUCTION rather than
re-deriving the pairing in the gate -- `FunctionInfo` does not carry the
clone flags, and `is_consuming` alone would exempt any consuming method
in a 2-entry set. Tag the distinction where it is decided.

**Why it did not ship alone.** The carve-out flips NOTHING by itself --
it is an enabler, and the doctrine says land those inside the wave that
consumes them. What makes it pay is the second half: the consuming-return
move chokepoint widened on both axes, the FIELD axis to an open-T slot
and the RETURN axis additionally to `Own[T]` (`own_return_t<T>`). The two
are deliberately NOT unified: `Own[T]` on the return is the transfer the
clone exists for; an `Own[T]` FIELD is a different question (what the
storage holds). `_is_type_param_slot` unwraps readonly/Ref but not
`OwnType`, which is what enforces the split.

**The axis discipline paid off immediately.** This is the same chokepoint
that carried the previous wave's byte divergence (gated on the field type
while the AST's ladder branches on the return slot). Before trusting the
widening, both newly-admitted (field, return-slot) pairs were traced
through the AST ladder by hand to confirm neither hits an early exit --
and a reviewer independently re-derived the same trace. The hole did not
reopen.

**A pin round-tripped, which is a signal worth naming.**
`TestAutoOwnCloneCarveout` asserted "no overload_set fold for the pair".
The previous wave RELAXED it to "exactly one fold" when removing the
`is_consuming` short-circuit made the twin fold; this wave restored the
original. A pin that has to be weakened is often reporting an incomplete
change rather than a changed truth -- worth asking, at the moment of
weakening, whether the missing piece is the actual work.

**The arm has zero EXECUTABLE coverage, and the green suite hides that.**
The consuming half (`own_return_t<T> first() &&`) is emitted but
unreachable from valid source: a named receiver always resolves to the
BORROWING overload (`is_consuming_receiver` is hard-coded False for
general method calls), and the rvalue-receiver form
(`Pair[Node](a, b).first()`) is the C++-build bug filed in BUGS.md this
wave. So "3650 cases built+run" proves the moved return COMPILES, never
that it RUNS. Given this chokepoint shipped a byte divergence one wave
ago, that distinction is worth stating rather than assuming.

**Probing is the standing requirement at this chokepoint, not optional.**
A hand-trace of the AST ladder plus one reviewer left a gap that
adversarial dualgen closed in about two minutes: `T` = scalar, `str` (the
owned-string family, which has its own ladder exit), record, a two-param
record returning the second field, and an explicit `self: Own[Self]` with
an `Own[T]` return -- all byte-identical, zero fallback; `list[T]` and
`readonly[T]` fields correctly still fold. Only ONE (field, return-slot)
pair is actually live, incidentally: an `Own[T]` FIELD is sema-rejected
("a field owns its value inline") and a bare open-T RETURN of a field is
too ("Cannot return local or temporary as reference"), so the axis
asymmetry documents which axis the `Own` belongs to rather than guarding
a reachable shape.

**Two pin failures, one lesson.** The routing pin here asserted C++ text
the AST fallback emits identically (so reverting the widening left it
green), and the carve-out pin asserted a FOLD COUNT that was an
implementation artifact (hence its round-trip). Both asserted OBSERVED
OUTPUT rather than the PROPERTY. The fix pattern is what the units now
do: witness the face via `_lower_ctx_witnessed` AND assert byte-identity
-- never bytes alone, because bytes are what fallback also produces.

**The wave's own dial-mover was parity-blind.** `auto_own_basic` binds
`x = p.first()` with `T` = a RECORD across a return boundary and only
READ it -- the exact defect the sibling commit fixed in four other cases,
sitting in the one case this wave exists to flip, and missed when those
four were enumerated. It now mutates through the borrow and reads the
field back. The general lesson: when a rule is worth applying to the
corpus, apply it FIRST to the cases the current change touches.

**Residue.** `auto_own_iter` / `auto_own_iter_user` do NOT chain here at
all: their consuming clones return `Own[Int32]` / `Own[Iterator[Int32]]`
with CONCRETE wrapped types and their return sources are not `self.field`
reads, so they never reach this gate -- they fail the separate
`ret_supported` check, which admits `Own[T]` only for generic `T`. The
`sig.overload_set` residue is now `arity` (4 cases, one missing render:
the prologue local for a stub's omitted params) and `generic_stub` (2,
template specializations). `db_compare` is a PRINCIPLED reject, not
residue: literal-only groups emit through the AST's mangled-name path, so
admitting them would count bodies migrated while emission stays AST.

### thir-grind-loop wave: three site grinds (2026-07-31)

Dial 2816 -> 2844/3651 (+28), markers 835 -> 807. Full exec suite green
(10379 passed, all cases built+run). Three cells, each a single raise
site ground to its floor, plus a batched review round.

**Cell 1 -- the container-literal elem gate to zero.** The census's
`expr.container_literal` tag (12 sole-blocker cases) attributed to ONE
site (`_container_lit_elem_ok`), and the standing "four unrelated
shapes" verdict was another sampling artifact. Seven element families
landed: Int/FloatLiteralType slots via `_resolved_scalar` in the family
classifier (a native call-arg literal keeps literal-typed element slots
that decl positions resolve); the qualcall protocol-arg hoist for PLAIN
module callees (`auto __tmp_N = std::array<double, 3>{...}` with the
protocol's element type substituted into the self-spelling -- never
`std::array<1.0, 3>`; range rvalues ride the same hoist); `copy(name)`
elements via `copy_plain_record_source` with the lowering's pointers
threaded through the gate; jagged tuple members (a nested list LITERAL
renders its bare brace inside the storage tuple) with the consuming
chain landed in the same cell (the tuple-elem-over-container-subscript
method receiver, pending-container resolution at the non-name
receiver-type read, container members in the RECEIVER-position
storage-tuple element row); union-element NAMEs split by BINDING (a
tracked ptr-variant local lifts `to_value_variant`, a narrowed alias
reads bare, untracked bindings REJECT -- the binding-form fence
consulted, not re-derived; and a ptr-variant local is excluded from the
element move mirror, it is a non-owning alias); plain-record tuple
members via the slot-info ladder's storage-context CONST_REF rule
(`const P*` + `&(c)`), carried into the borrow ladder with the Optional
force-REF ordering preserved; `tpy.String` admitted into
`_owned_str_slot` (the form axis already treated it as owned); wrapper
scalar literals; TypeParamRef elements (`return {x};`).

**Cell 2 -- builtin-module marker rows.** The `expr.method_call` marker
gate's biggest site: the builtin-module @cpp_template TYPE ctor
(`tpy.Int32(10)`) carved out of the module-ctor reject (non-generic
overloads only, mirroring the AST arm's guard); the explicit-targ
equality pin relaxed to a PREFIX pin (`unsafe_cast[UInt32](p)` spells
one of [T, U] and the AST renders from inferred_type_args alone); and
`_shared_pass_through_arg`'s scalar row switched to `_resolved_scalar`
-- which shadowed the dedicated `arg.native_int_literal` row (deleted
with its face) and legitimately opened three pinned shapes, each
converted with dualgen byte-identity evidence. The walrus-arg await
routes now; the erased-operand reject composition got a fresh witness (a
ternary of task handles). Parked at the same site with fresh probe
evidence: the Rc.new structural-conformer pair (the adapter-in-targ
composition, the classify_dyn_own_arg design rung).

**Cell 3 -- the return.slot_type gate 8/9.** All nine sole-blocker cases
at the prescan `ret_supported` gate; six return-type families landed:
Own[value scalar] (a no-op spelling, unwrapped for the scalar rows);
Own[structural protocol] (the same `auto` slot as the bare protocol);
Any returns wrapping `make_any(..)` via the shared elem-into-Any row
(coerce peeled, containers fenced); Own[storage-tuple] literals (the
spelled brace-init; non-value member NAMES reject -- the borrow ladder's
aliasing-safe render is a different row); value-bound Optional[T]
(return-slot scoped); generic Span[T] (the auto_readonly getter pair);
Own[record|scalar] unions (scalar members join the storage-variant
family). The ninth (`async/await_tuple_own_unpack`, an
Own[Poll[tuple[Own[..]]]] instance return) stays on the generic
type-arg spelling fence.

**Review round** (six specialists): safety-model and cpython-parity
CLEAN -- every flipped case mutates across its alias boundary, uses
@nocopy, or carries the copy warning; every new admission defers its
copy-vs-alias verdict to pre-existing facts. Applied: an import hoist,
three stale TODO.md refreshes, face asserts on the marker pins, boundary
pins for the wrapper-scalar and copy-element rows, unit pins for the two
return rows that had corpus-flip coverage only.

**Lesson repeated for the third time:** "it fragments" verdicts written
from case sampling keep dissolving under `probe_sites.py` -- container
literals (12 cases/1 site), return slots (9 cases/1 site). Histogram
first, always.

### thir-grind-loop wave 2: the btuple track + comp-route rows (2026-07-31)

Dial 2844 -> 2856/3651 (+12), markers 807 -> 795. Full exec suite green
at each cell (last: 10397 passed). Three cells extending the same
branch, plus the six-specialist review round applied below.

**The Own[ptr-Optional tuple] consuming track** (the G1 boundary's
parked "separate element row", built end to end): the ref-element tuple
LITERAL at an Own[tuple[T | None, ..]] append slot renders
`tuple_to_storage_move<S>(..)` over the borrow tuple with the AST's
per-element `_maybe_move` mirrored as elem wraps (`std::move(&(a))`,
keyed on `_is_move_source`, in both the direct-borrow and
`tuple_value_to_borrow` paths); a `copy(x)` element takes the shared
copy-construct row. The NON-move family: a borrow-tuple-returning CALL
(append and dict setitem -- moving from a returned pointer would alias
caller storage), the setitem tuple LITERAL (the dict store copies; the
AST's setitem path never moves elements), the whole-element passes
(bare `__getitem__`, storage-to-storage), and the matching borrow-param
bare bind. The unpack side: STORAGE_WRAP gained an expression source
(`a0, b0 = pairs[0]` lifts the rendered element read; mutable
receivers only) and the call-borrow RVALUE capture (`a, b = both(t1,
t2)` -- the result is already borrow-form, no lift). The whole
tuple_optional family flipped (7 cases + socket_connect_timeout via the
owned-tuple nested-element widening, tuple-source-scoped so an
Own-tuple DECL stays an unrouted slot).

**Comp-route rows**: array-SOURCE unpack heads (the per-index lambda
binds `auto& __tup_N = __obj_N[__i_N];` + per-var `std::get` decls);
range-arm value-TUPLE elements (node-gated); non-value tuple element
slots (a literal rides the landed CONST_REF/borrow rows, a whole
loop-var NAME copies via non-move tuple_to_storage); items() RECORD
unpack targets (`const auto&`, const sources only) with record-value
dicts admitted through `_dict_view_iterable_ok`. This supersedes the
"Tail cells" F3 pricing of `comp_element_copy_warn`. Residue at the
site: the narrowing-ternary element (per-element narrowing machinery,
boundary-pinned) and the call-iterable comp (`os.listdir`).

**Review round** (six specialists; convention clean): ONE Critical --
the subscript-wrap unpack row's mutable-receiver gate read
`const_locals` only, missing readonly-typed PARAMS; a
`readonly[list[tuple[P|None,..]]]` param source emitted a non-const
`tuple_to_pointer` over a const binding, a hard C++ error where the AST
fallback compiled. Fixed by consulting `_param_is_const` like the
sibling storage-tuple-alias check, pinned + dualgen-verified (the
readonly source now defers byte-identically). Applied alongside:
boundary pins (setitem NAME value, values() record loop), the
`_ptr_optional_tuple` / `_btuple_pass_arg` / `_unpack_target_cpps`
dedupe extractions, the merged `_lower_call_arg` tuple-literal arm, the
`THIRTupleValueToBorrow` post_init exclusivity assert, and the TODO /
ledger staleness refreshes. Filed: the append-vs-setitem
copy-warning inconsistency (pre-existing, BUGS.md).

**Lesson:** the review's Critical came from exactly the class the
memory warns about -- a type-level/local-set predicate standing in for
a fuller const verdict. `const_locals` is a BINDING set that does not
cover params; every mutable-receiver gate needs the `_param_is_const`
pair. Same defect class as the ptr_variant_locals fence.

### thir-grind-loop wave 3: inplace-dunder aug + field.result_type (2026-07-31)

Dial 2856 -> 2863/3651 (+7), markers 795 -> 788. Full exec suite green
(10408 passed). Two cells on the same branch, plus the six-specialist
review round below.

**The inplace-dunder aug row** (`stmt.aug_assign`, the last big
aug-assign slice): an aug-assign whose sema resolution is an IN-PLACE
dunder (`stmt.resolved_inplace`) lowers as the statement-position
method call the AST emits -- `b += 10` on Atomic -> `b.__iadd__(10);`,
`s |= {3}` -> the cpp_template arm `::tpy::set_update(s, ..)` --
mirrored on THIRMethodCall's three dispatch arms via the member-name
rule shared with the plain method-call arm (`_method_member_cpp`).
Bare non-pointer NAME targets and markers-clean FIELD targets (whose
receiver gates apply during lowering); scalar and set-literal values
only. Flips: atomic/atomic_ops, atomic/atomic_shared,
list/list_iadd_pending. Residue at the site: the ArrayList record-RMW
(`items[0] += 5` desugars to the record setitem/getitem lane),
pointers/warn_borrow_field_path, readonly/auto_readonly_usage_const.

**The field.result_type cell**: the str-family field RESULT row now
tags only the str form (str/StrView via `_str_view_family_value`,
hoisted from `_str_field_value_read`) and delegates receiver admission
to the receiver ladder below -- so record-element subscripts
(`src[0].name`), ArrayList getitem chains, and deref_check Optional
pointers (`p.name`) route without per-receiver result rows. `String`
keeps its fence (no view sink at the return convert) EXCEPT off a call
receiver where the whole resolved slice renders bare -- the two
existing fence pins encode the split and each caught one over-wide
draft of this row. Callable-field calls gained the container-FIELD arg
precheck (`self.cb(self.data)` bare into the std::function `T&` slot,
`field_prechecked` like the membership haystack). Flips:
calls/callable_field_mutation, calls/ref_collect_no_source_clobber,
inheritance/inheritance_upcast_ptr, tplib/json_model_with_dataclass.

**Review round** (six specialists; safety-model and cpython-parity
clean -- parity probed the callable-field dict variant and the
Rc+Atomic field-target aug against both backends): no Critical.
Applied: the aug row's missing boundary pin (narrowed Optional
receiver stays AST), the `_container_field_bare_read` and
`_method_member_cpp` dedupe extractions, TODO staleness
(ref_collect_no_source_clobber was still listed as open residue).

**Lesson:** both fence pins that caught the str-row drafts encode
POSITION-dependent verdicts for the same type (`String` routes at a
call-receiver print sink, stays AST at a return slot). A RESULT-row
widening that collapses receiver shapes must re-run every pin keyed on
the TYPE it widens, not just the receiver shapes it adds -- the wave
skill's step-2 pin sweep would have caught both drafts before the
corpus run.

### thir-grind-loop wave 4: three field_write_shape rows (2026-07-31)

Dial 2863 -> 2868/3651 (+5), markers 788 -> 783. Full exec suite green
(10412 passed). Three cells + harvest, plus the review round below.

**Chained container-elem receivers** (`_borrow_elem_subscript_shape`):
a field over an admitted record-element subscript now types via
`_field_decl_type` and recurses, so `a.bs[0].as_[0].val = 30` renders
the nested `::tpy::__getitem__` / bare member chain (recursion on
strictly smaller receivers). Flip: records/recursive_record_mutual.

**Span/varargs record elements** (`_container_elem_family`): the span
arm's hardcoded scalar slice became a caller-supplied `span_elem_ok`
gate (the record family passes `_f1_record`; str/bytes span elements
still ride later cells), and the arm admits the span-backed
`varargs[T]` body view. Flips: calls/varargs_ref_mutation +
readonly/readonly_span_ref_elem_read + tuple/tuple_ref_span harvested.

**Optional[container] field writes** (the field-assign tail): an
Optional[container/Array] FIELD stores value-repr regardless of the
position-blind `uses_pointer_repr`, so `val_opt_container` keys the
tail BEFORE `ptr_opt_field` and the convert targets the INNER family
type -- `h.s = std::move(initial);`, never the ptr_to_optional lift.
Flip: set/optional_set_forward_ref.

**Review round** (six specialists; safety-model verified all three
areas with adversarial dualgen, cpython-parity + convention clean):
ONE real defect caught by architecture-fit and confirmed divergent by
dualgen -- the tail's `val_opt_container` used `_plain_container_read`
(no Array) while the gate admits `is_array` post-unwrap, so an
Optional[Array] param-source write emitted `ptr_to_optional(xs)` where
the AST copies bare. Fixed by mirroring the gate's family set; pinned.
Also applied: committed boundary pins for all three arms (the
recurring dualgen-only gap test-coverage keeps catching -- Optional
element chain, varargs Optional elements, the non-move copy sibling),
the `span_elem_ok=elem_ok` dedupe, TODO staleness (two un-parks + the
field-write decomposition debt note).

**Lesson:** the round's one divergence is again the gate/tail
family-set mismatch class -- two predicates spelling "the same" family
slice independently. When a gate and its render tail must agree,
derive both from one predicate or mirror the set explicitly with a
comment naming the twin.

### thir-grind-loop wave 5: field_write_shape site CLEARED (2026-07-31)

Dial 2868 -> 2871/3651 (+3), markers 783 -> 780. The
`assign.field_write_shape` census site (6 sole-blocker cases) is
CLEARED end to end across waves 4-5. This wave's cells:

**Optional[tuple] literal write** (`s.auth = ("user", "pw")`): gate
and render branch both unwrap the value-repr Optional to the inner
value tuple; the optional absorbs the spelled brace-init. Flip:
tplib/requests_redirect_cross_host.

**Pointer-local record copy** (`self.result = (*saved);`): a
`field_write.ptr_local_copy` gate row + the deref in the record-NAME
render arm (pointers are never movable). The old fence pin encoding
the ws.pointers reject converted to a routing pin. Flip:
pointers/escape_hoist_method.

**The warned record-copy family at COPY sinks** (the
warning_implicit_copy chain, four sources): a `T&`-returning call at
the field write and at the checked setitem (the new
`record_copy_sink` _ExprUse flag threaded into `_call_use_supported`
and a setitem value row), a FIELD-read source, and a record-element
SUBSCRIPT source -- all render bare where the sink's copy-assign
absorbs the reference. Decls off the same borrow-returning call keep
binding REF_ALIAS (boundary-pinned). Flip:
returns/warning_implicit_copy.

Full remote suite green at the close (see the branch's final run).
Session total for the thir-grind-loop branch: dial 2816 -> 2871
(+55 flips), markers 835 -> 780, five review rounds applied (rounds
3-4 in this stretch; one Critical and one dualgen-confirmed
divergence caught and fixed across them).

### thir-slot-type-wave: the decl.slot_type decl-ladder rows (2026-07-31)

Markers 806 -> 794 (12 flips, repo-verified as of the review-round
commit; the subsequent master merge imported one marker-bearing case
and the readiness pass added one migrated witness case, landing the
branch at dial 2904/3693, 789 markers). The wave started against the
pre-merge corpus, where the queue memory recorded 2871/3651 --
master-side case growth moved the denominator mid-wave, so no
comparable start dial exists. Site: the
`decl.slot_type` slot_ok ladder (29 sole-blocker census cases,
`thir/lower/statements.py`). Eight cells:

**Storage-call tuple decl** (`decl.storage_call_tuple`): an owning
non-value-element tuple call result decls the storage copy (`auto` /
the spelled collapsed type). The owning-signal split (Own-declared
return vs per-element all-Own vs the borrow-tuple F1 alias) was
tightened by a corpus byte-diff catch: stamping a borrow-tuple return
storage DIVERGED, and the boundary pin asserts that shape routes via
the borrow-decl arm instead.

**Readonly span peel + Optional name-copy** (`_peel_readonly`,
`decl.opt_name_copy`): stacked-readonly span elements resolve through
one peel helper at three sites; a same-repr pointer-Optional name init
re-tags OTHER -> OPTIONAL_TO_PTR for the bare pointer copy.

**Reassigned borrow-tuple decls** (`decl.btuple_lift` /
`decl.btuple_literal` / `decl.btuple_rebind_slot`): ONE fixed borrow
shape across all bindings -- the tuple_to_pointer lift, the
REF-capture literal, and the owning-call `std::optional<...>` slot +
emplace (the statement twin of the walrus btuple slot). Const
bindings, borrow-call inits, owning calls at RESEAT position, and the
mixed own-borrow hybrid stay named rejects.

**Narrowed Optional-ptr receiver borrow locals**: the gate-reject
mirrored a since-fixed AST bug (inferred-const receivers used to drop
constness); THIR now reads the same deep-const verdict
(`_opt_ptr_param_deep_const`) the AST's seed_param_locals reads, and
the stale fence pin converted to a four-body byte pin over both const
verdicts.

**Reassigned Optional local off an lvalue field init**: the shared
classifier verdict flipped (AST-transparent), reseats ride the
slotless pointer arms plus the new `reseat.opt_field_lift` rung. The
INLINE_RVALUE block slot moved to its own emit registry -- registering
it in `rebind_slots` let the THIRAssign rebind-slot special case
hijack later field lifts into `p = &*(__slot_1 = ...)` (byte-diff
catch, pinned as a regression).

**Optional[value-record] slot** (`decl.opt_value_record`) and the
**Array last-use alias move** (`decl.move_through_array`): the
value-record twin of the owned-optional record slot, and the
expensive-copy value container admitted into the move-through decl
arm.

Cleared: 4 tuple realias/rebind cases, const_borrow_optional_ptr_recv,
optional_field_to_local_rebind, property_value_record_return,
reassign_list_alias_no_mutation, plus the first harvest's 4
(span_method_builtin, warn_auto_move_optional,
tuple_own_call_local_use, gen_yield_view_from_frame). Site tail
(~12): every remaining case chains into an unported expression family
(class-attr reads, record-getitem subscripts, call-receiver chains,
the Optional[StrView] append shim, the own-borrow hybrid tuple reads),
is fenced (the const borrow-tuple row, the mixed hybrid realias, the
literal-init owned-view optional whose narrow flips the None-test
render -- documented at the gate), deferred (the ternary ptr-select
render mode), or parked (the Rc/Box `.new` adapter fork).

Lesson (hijack class): emit-side slot registries are per-CONCEPT, not
per-name -- a plain block slot and the decl-time rebind slot share a
name key but not a consumer, and the shared dict let one consumer's
special case capture the other's plain assigns. Same-name registries
merged "for convenience" are the divergence seed.
### thir-longtail-grind wave 1: the expr.call ARG site (2026-07-31)

Dial 2904/3693 -> 2907/3694, markers 787. Site: the free-call arg gate
at `thir/lower/expressions.py`'s `_lower_free_call_arg` ladder -- the
top row of the fresh census (`body:expr.call` 16 sole-blocker, five of
them at this one raise site). Three cells; the site is now empty except
`set/set_comp_owned_move`, which stays parked on the comprehension
per-condition temp-sink design fork.

**Borrow-tuple NAME rows** (`arg.btuple_storage_name` + the bare-bind
widening). The gate had lift rows for a storage F3-tuple FIELD and
SUBSCRIPT but none for a NAME, so `consume(it)` over a list of
storage-form tuples fell the whole body back. The storage half takes
the same `tuple_to_pointer` with the AST's want_const pair; the bare
half widens `_borrow_tuple_name_arg`'s name set from params to
`_borrow_tuple_bare_names`, which adds a REASSIGNED pointer-repr tuple
local (the AST's `borrow_form_tuple_locals`). Resumable frame names are
excluded: the lane's owning tuple slots reach neither
`storage_tuple_locals` nor a borrow render the AST agrees on, so
admitting them drops the lift -- the owning-tuple-frame fence pin
caught exactly that on the first attempt. The for-each storage
registration also gained a readonly peel: the AST asks through
`ctx.get_expr_type`, which always strips readonly, so a
`readonly[list[..]]` source registers its loop var storage there and
did not here, leaving the const half of the new row unreachable.

**Recursive-union borrow call** (`arg.recursive_union_borrow_call`). A
wrapper-returning accessor at a `const Value&` slot binds inline. It
asks the SLOT rather than the arg's value category, unlike its record
twin: `call_returns_cpp_ref` gives every union return value semantics,
so a wrapper accessor always reads as an rvalue, and an rvalue binds a
const-ref slot fine. A mutable ref slot stays out. NO routing witness
-- `union/union_mutual_mixed` moves to the method-call receiver gate --
so the pin asserts the face is witnessed and that `expr.call` is gone
from the reasons.

**Own[Optional[record]] slot** (`own.opt_ptr_name_rebuild`). Own forces
the OWNING value form where a bare Optional would use pointer repr. The
ctor-rvalue half needed no new predicate -- `_own_optional_record_rvalue_arg`
already existed at the ctor gate and is now wired into the free-call gate
too (the review caught a duplicate written beside it; the duplicate is
deleted). The NAME half is the new row: `gen_expr_deref`'s null-safe
rebuild `r ? std::optional<Rec>(std::move(*r)) : std::nullopt` under the
Own-slot move, via the new node `THIROwnOptRebuild`, the payload sibling
of `THIROptViewArg`. Gated to the LAST-USE slice -- a boundary pin
written during the review round caught that a non-last-use occurrence
takes the AST's Own-slot copy-TEMP cascade (`Holder(std::move(__tmp_N))`),
an entirely different render. Narrowed occurrences are excluded too.

**LESSON -- the wholesale movable_locals seed.** The AST's
`movable_locals` is a WORKING set grown at the var-decl arms as they
emit; `_gen_error_return_var_decl` is not one of them, so an
unwrap-bound local never becomes auto-movable there. THIR seeds the
whole per-function sema fact up front. The gap was latent until the
Own[Optional[record]] rows removed the `expr.call` fallback masking
`tplib/json_model_user_type`'s macro-generated `__json_decode__`, and
the corpus byte-diff surfaced it the same run as
`events.push_back(std::move(__elem_1))`. Fixed with a discard at the
bind; every other AST add site is decl-driven too, so the wholesale
seed remains the general hazard -- the rest are fail-safe (a name the
AST adds and THIR does not simply copies), but the next widening that
unmasks one will look exactly like this.

**Review-round consolidation**: the four `_borrow_tuple_*_arg`
predicates hit the project's fourth-twin fold threshold and now share one
`_borrow_tuple_arg` body parameterized on (shape test, type lookup);
`_borrow_tuple_local_type` moved from statements.py to checks.py, taking
the storage set as a parameter instead of `lc` and removing a
function-local import. The recursive-union row's `is_ref_param()`
mutable-slot guard was dead code (it answers `uses_pointer_repr()` first,
always False for a wrapper) and is replaced by a note saying what a real
mutability signal would have to be.

**Pre-existing AST bug found**: the resumable borrow-form frame field
assigned a storage tuple has a SECOND site (a tuple-literal decl, not
only the loop advance). Folded into the existing BUGS.md entry.
### thir-longtail-grind wave 2: del, print-element, try-hoist (2026-08-01)

Dial 2907/3694 -> 2918/3694, markers 776. Three sites off the fresh
census's flat tail, 11 flips.

**Element-blind `del` receivers** (`delitem.container`, 5 flips). The del
emit `::tpy::__delitem__(c, k)` never constructs, converts or reads the
element slot, so the container receiver is admitted regardless of element
family and the key slice alone decides byte-parity. That is exactly the
reasoning `_any_value_dict` already carried for a `dict[K, Any]` VALUE,
so the del gate COLLAPSES from three disjuncts (scalar-read, dict-Any
escape, user record) to two, and the `delitem.any_value` face is deleted
as dead. `_record_has_delitem` also dropped its no-type-args guard --
the del render spells the receiver name bare and never the record type,
so a monomorphized generic reaches no part of the emit, and its read-side
sibling `_record_getitem_key` already had no such guard. The sixth case
at this site chain-walks to a module-var receiver (`os.environ`).

**F1-record tuple elements at a print sink** (`print.tuple_record_elem`,
4 flips). One row, two renders: a BORROW-form tuple holds `T*` elements
and print is a VALUE position, so the referent streams
(`(*std::get<1>(t))`); a STORAGE-form one holds the element by value and
streams bare. The split reuses the arg rows' storage-vs-borrow evidence
(a field read is always a storage source, a NAME only when registered as
one). `THIRPrintArg` gained a `deref` flag; the subscript lowers
prechecked, since the predicate validated the tuple shape.

**try hoists inside a branch or loop** (2 flips). `_gen_try` emits the
predecl AT THE TRY, so a nested try declares into the enclosing C++ block
on both paths -- the `in_branch or loop_depth > 0` guard mirrored
nothing.

**LESSON -- three fence pins whose reason had gone stale.** The guard was
written for a real hazard (THIR scopes `declared` per block, the AST's
declared_vars is body-global, so a later same-named decl outside the loop
could classify differently), and three pins encoded it. Probing the named
hazard directly rather than trusting the prose: the value flavor agrees
byte-for-byte, the non-value flavor still rejects at the OPTIONAL_STORAGE
flavor gate -- which was doing the actual holding all along -- and a read
after the branch with no binding rejects (`name.global_read`) rather than
mis-rendering. All three pins were CONVERTED to pin the new truth plus
the hazard itself, not deleted. A fence's stated reason is a hypothesis
about the other path; test it against that path before believing it.

Remaining at these sites: `iterators/iter_builtin` (method-call gate),
and two try cases needing the OPTIONAL_STORAGE per-flavor rungs
(container and reassigned-record hoists) that the if cascade already
carries -- a separate cell.
### thir-longtail-grind wave 3: tuple literals + the kind-blind lift (2026-08-01)

Dial 2918/3694 -> 2924/3694, markers 770. Two sites, 6 flips.

**Slot-less value-tuple literals** (`expr.value_tuple_self_typed`, 3
flips). Tuple literals had no generic `_lower_expr` arm AT ALL -- every
sink lowered them against its own slot, so a literal reaching
`_lower_expr` with no slot threaded from the position (`t = astuple(p)`,
an unpack source) fell the whole body back. The AST spells the tuple's
own type there, so the slot IS the expression type: take it from sema.
Only a VALUE tuple qualifies; a pointer-repr one has a borrow-vs-storage
form the position decides, and keeps deferring.

**Two borrow-tuple element sources** (2 flips). An element read off
another BORROW-form tuple already yields the element POINTER
(`std::get<i>(p)` is `T*`), so it passes through with no `&(...)` --
taking its address would build a `T**`. It was a named reject calling
itself "the pass-through face, not sliced"; the read lowers prechecked
from the lift decision, since the generic tuple-subscript arm has no row
for it standalone. Separately, `rvalue_ok` was granted only to the
call-arg sink, but a container-literal element is equally a flush point
for the `tuple_value_to_borrow` source tuple, so a ctor rvalue at a
pointer-repr Optional element slot renders there the same way.

**The storage lift is kind-blind** (1 flip). A storage-form tuple NAME at
a NATIVE/TEMPLATE callee's tuple slot takes the same `tuple_to_pointer`
lift as at a plain callee (`str(t)`). The row was gated to plain callees
for no reason the AST shares -- the lift wraps the read rather than
hoisting a temp, so it is position-independent.

**LESSON -- a fence pin that argued for its own conversion.** The astuple
pin's reason was "a bare TUPLE LITERAL has no generic `_lower_expr` arm
... that peel is its own rung" -- it named the missing arm rather than a
render split, so building the arm converted it. Its predecessor also
insisted on asserting the FALLBACK rather than byte-identity, because a
fallback body emits byte-identical AST by construction; the replacement
keeps that discipline by asserting the FACE plus the exact render string.

**LESSON -- two reviewers, one Critical, on a gate keyed off the wrong
fact.** The element pass-through decided "already a pointer" from the
subscripted object's static TYPE alone. A STORAGE-form tuple local (an
`auto&&` alias) has the same type but holds its elements BY VALUE, so
`std::get<i>` there is a `T&` and the address-of is required -- the bare
pass-through would have emitted a `T&` into a `T*` slot. The file already
had the right helper (`_subscript_yields_borrow_ptr`, which gates on
`storage_tuple_locals`); the row now calls it instead of re-deriving.
Same defect class as the wave-1 borrow-tuple NAME split: a type-level
test standing in for a BINDING-form fact. No corpus case witnessed it --
the codegen and safety reviewers each found it independently by probe.

Remaining at the native-arg site: five unrelated 1:1 arg shapes (a None
literal at a template slot, a vararg pack at a native slot, an open-T
field at a Sized slot, a str name at a template slot, a storage tuple
LITERAL at a Hashable slot), each its own row.

### thir-longtail-grind wave 4: the native-arg 1:1 tail (2026-08-01)

Dial 2924/3694 -> 2928/3694, markers 766. All six sole-blocker cases at
`call.native_arg.other` rejected at ONE gate but carried six unrelated arg
shapes, so this wave is five independent 1:1 rows rather than a site
grind. All five landed and the site is EMPTY.

**`call.template_arg_dropped`.** A `@cpp_template` body that never
substitutes `{i}` DISCARDS that arg's render, so no shape check on it can
matter -- `filter(None, xs)` expands to
`::tpy::builtin_filter_truthy<T>({1})`, where the None predicate selects
the callee and then vanishes. The arg index is threaded to the gate for
this.

**Pending view-var str slots.** A generic callee's slot can still carry
the parser's unresolved view var: `repr(b)` where `b: str =
make_default()` arrives with a `PendingStrType` slot that the nominal
check missed even though arg and slot both resolve to `str`.

**Vararg packs at native callees.** `gen_call_arg` renders the pack the
same way whatever the callee kind and the temp hoists at the same flush
point, so the native/template exclusion mirrored nothing.

**`arg.native_protocol_open_field`.** `len(self.value)` inside a generic
body has the field typed as the open `T` and the slot still the
unsubstituted protocol, so the exact-match rows could not fire.

**`arg.native_protocol_tuple_literal`.** A tuple LITERAL at the same kind
of slot (`hash((1, Box(5)))`): the monomorphized slot threads no target,
so the literal spells its own sema type with every element captured BY
VALUE. The element literal types have to be RESOLVED before spelling --
the first attempt emitted `std::tuple<1, Box>`, caught by the byte-diff.

**LESSON -- a boundary pin that cannot fail is not a boundary pin.** The
test reviewer bug-injected all four and found THREE whose boundary was
undetectable: two put the boundary case in the same program and asserted
on emitted C++, but the neighbouring row's render is textually IDENTICAL,
so deleting the restriction changed nothing observable; the third had no
boundary case at all. Fixed by pinning face COUNTS (2-and-1 rather than
`>= 1`) so a swallowed neighbour shows up as 3-and-0, giving the peel its
own face so it is countable at all, and adding the missing
`call.vararg_view_elem` reject. Both injections are now verified caught.
Where a widened row's render coincides with the row next door, only a
count discriminates -- a string assertion is decoration.

### thir-longtail-grind wave 5: the container-method receiver collapse (2026-08-01)

Dial 2929/3694 -> 2930/3694, markers 764. The bundled version of the cell
wave 3 measured, reverted and PRICED in TODO.md -- taken now because the
two arg rows it needed came with it, exactly as that entry prescribed.

**`_container_method_recv` collapses six element predicates into one
element-blind test.** A method receiver renders bare whatever the element
is (`xs.push_back(...)`), so the element reaches only the arg and result
gates -- the argument `_container_method_elem`'s own docstring already
made for its own families, generalized. `_set_method_recv` stays a
separate disjunct: it answers a method-surface question, not an element
one.

**`arg.own_tuple_call_rvalue`.** An owning CALL whose result IS the
`Own[tuple]` element slot binds the by-value slot with no lift, so the
insert renders bare whatever the element family -- the tuple twin of
`_own_record_rvalue_arg`'s call row. The borrow-tuple rows beside it each
render a conversion, which is why they stay element-gated.

**`arg.ptr_addr_of_elem` + the `Own[Ptr[T]]` coerce admission.** TPy takes
the address at a Ptr slot, so a record-element lvalue lifts
`&(::tpy::__getitem__(items, 0))`. The gate row alone was not enough: the
coerce disposition rejected the whole `Own[...]` family on the grounds
that "the arg cascade owns the render decision" -- true for a reference
payload, but `Own[Ptr[T]]` is a NO-OP spelling (Own over a value type),
so there is no cascade and the address-taking render stands.

**REVIEW ROUND**: both new arg rows shipped with corpus coverage only --
no unit pins at all, which the test reviewer caught. Added, with the
counts as the boundary (`ps.append(p0)` feeds an ALREADY-pointer source to
the same slot and must ride the pass-through row, whose render differs
from the lift only in the absence of `&`). Both reverts verified caught.
The converted fence pin had also dropped its routing assertion, leaving
only byte-identity -- which cannot notice the widening being reverted,
since fallback and routing render this body identically. Restored.

**LESSON -- price a cell, then take it when its price is paid.** Standalone
the collapse was byte-identical corpus-wide and worth ZERO flips, so wave
3 reverted it and wrote down the measurement plus what it was waiting for.
Two rows later that condition held and the same change was worth two
cases. The recorded measurement is what made the second attempt cheap --
no re-deriving, and the drift-risk fence pin it trips was converted with
the evidence already in hand: the receiver is element-blind, and the
value-opt-VIEW family is actually protected by its ARG and read-position
gates, which is where its render genuinely differs.

### thir-longtail-grind wave 6: both remaining sites measured (2026-08-01)

Dial 2930/3694 -> 2932/3694, markers 762. Both multi-case sites re-probed
at site level; the measurement is the durable output.

**`body:expr.call` (12) is FULLY FRAGMENTED** -- 2+2+1x7 over nine raise
sites. The pending-list pair landed (`iterators/gen_proto_param_*`, 2
flips): a literal-seeded local (`xs = [1, 2]`) is still `PendingListType`
at a protocol param slot, and the arg-temp arm rejected rather than risk
a crash in `render_type`. Sema's resolution is final by lowering time --
the lookup is keyed by `literal_id`, populated once before either path
runs -- so asking for it gives the same type the AST reaches later. The
residual guard stays for a genuinely unresolvable binding.

**Expression callees** got their construct (`calls/expr_callee`, 0 flips
-- it chain-walks to a container literal). `make_adder(10)(5)` /
`fns[i](x)` have a non-Name func and every call arm reads `func_name`, so
the whole construct fell back. New `callee_expr` field on `THIRCall`
renders the callee parenthesized ahead of the args.

**`body:stmt.var_decl:decl.slot_type` (16) is ONE site but mostly PARKED
families.** Seven are the Rc/Box/Weak spelling forks and the mixed-own
hybrid tuples. The four-case remainder is NOT a slot-ladder row at all:
every one is a REFERENCE-type slot off an LVALUE init (subscript / field),
i.e. a borrow-alias decl the classified-local cascade ABOVE the ladder
owns. Widening slot_ok would be the wrong site.

**LESSON -- "byte-identity rides the corpus case" is a claim to check.**
The expression-callee pins said exactly that, and it was FALSE: the
corpus case still carries `no_thir.txt` and falls back on an unrelated
blocker, so nothing byte-diffed the new render against the oracle. A cell
that flips ZERO cases has no corpus net by construction -- its unit pins
are the only net, and a byte-identity pin is not optional there. The same
review also refuted the "no negative boundary exists" claim: three of the
arm's four guards are genuinely unreachable (sema rejects them first),
but `**kwargs` is reachable and was untested. Both fixed.

### thir-longtail-grind wave 7: instantiation-arg movability (2026-08-01)

Dial 2932/3694 -> 2933/3694, markers 761. One row, and it is a BUG rather
than a widening.

**The consuming instantiation wrap keys on MOVABILITY, not last use.**
`list(dirnames)` where `dirnames` comes from `for _, dirnames, _ in
os.walk(..)` was rejected: the arm asked `all_last_uses` alone, and an
unpack target IS a last use, so it went looking for the consuming
`::tpy::own_iter(std::move(x))` wrap and rejected when none applied. But
such a target is never MOVABLE -- it aliases a slot the generator frame
overwrites each iteration -- and the AST renders it bare. Gated on
`_is_move_source`, which asks both facts, exactly as the AST's
`_is_last_use_movable` does.

Second instance of this defect class on one branch (the first was the
error-return `movable_locals` seed). **A last-use fact used without the
movability fact beside it is now a known THIR failure mode** -- worth
grepping for at the next audit.

**LESSON -- byte-identity makes a routing pin mandatory, and I got this
wrong three times.** The review caught the pin for this fix asserting
only the two renders plus byte-identity. Reverting the fix makes the body
fall back SILENTLY, and the AST emits the identical text -- so every
assertion still passed. Worse, the program I chose never routed the bare
arm at all: its `list(items)` render came from the fallback the whole
time, so the arm under test was unexercised. Fixed by rebuilding the pin
on a shape that genuinely routes (a borrowed param rather than an unpack
target), asserting `_fn(...) is not None` for both bodies, and giving the
bare arm its own face so the two arms are countable. Revert-injection now
verified caught. THE GENERAL RULE: in a byte-identical migration, a pin
without a routing or face assertion proves nothing about routing, no
matter how specific its render strings look.

### thir-longtail-grind: readiness retrospective (2026-08-01)

**The flip count is 28, not 29.** 28 `no_thir.txt` markers deleted
(789 -> 761); the dial moved 2904 -> 2933 while the denominator moved
3693 -> 3694, and the branch added zero test cases. One dial point is
unattributed -- report the marker delta, which is what the branch
actually did.

**The pin-discipline lesson is 0-for-5 as documentation, and this
branch is where that became undeniable.** The three-unit rule has been
in CLAUDE.md since 2026-07-28 and this ledger teaches it four separate
times. Five pins on this branch alone could not fail. Three were caught
by review; TWO more were found only by auditing the branch's own added
tests at the readiness gate -- and one of those pinned this branch's own
`movable_locals` bug fix, so the fix shipped with a vacuous test. Writing
the rule down a fifth time is not a remedy; the enforcement proposals are
in TODO.md. The working detector on this branch was revert-injection, and
the record shows the REVIEWER running it every time and the author never
running it before presenting -- that asymmetry is the actual finding.

**Every collapse-shaped cell here wanted `dualgen.py` and the ledger
cannot show it got one.** The del-gate three-into-one, the
`_container_method_recv` six-into-one, `_record_has_delitem`'s dropped
type-args guard, the kind-blind storage lift, the removed vararg
native/template exclusion -- each admits a whole family on one to five
witnesses, which is the exact shape CLAUDE.md names dualgen as the ONLY
detector for. Every defect caught in that class here was found by a
reviewer's ad-hoc probe instead. Record the probe per cell.

### The arm ledger could not report its own headline (2026-08-01)

The arm residual is the per-arm deletion-progress view: residual = fallback
bodies keeping that AST arm alive, and **residual 0 = no body needs the
arm**. The generated `THIR_ARM_LEDGER.md` had reported `0/67 arms
deletable` since it was created. That number was not a measurement -- it
was structurally unreachable.

**`record_arm_residual` is a counter over OBSERVED kinds.** An arm that
reaches residual 0 stops being seen, so it is ABSENT from the dump rather
than present with 0. The renderer computed `deletable = [k for k, v in
items if v == 0]` over that dump: empty by construction, whatever the
tree. It would have printed `0/N deletable` on the day fallback hit zero.
Confirmed against a fresh dump before the fix: min value 1, zero keys
equal to 0.

**The real answer was 4.** Seeding the dump from a derived `arm_universe()`
(`thir/fallback.py`) makes zero reportable: `continue`, `del_attr`,
`type_param_construct`, `chained_compare` are at residual 0 on master
(dial 2933/3694). All four have real AST emit arms and THIR lowering
counterparts. `continue` (was 6) and `type_param_construct` (was 1) are
present->absent since the last regeneration, which is a SOUND inference in
that direction only because the census walk got WIDER in between
(`_walk_deep` reaches nested container fields the flat walk missed) -- a
kind that stopped being seen genuinely stopped occurring.

**Residual 0 is not a deletion license.** It is measured over fallback
BODIES. The Gate D3 checklist-item-4 skeleton positions
(`records.py:404/1480/1619`, `functions.py:1924`) call `gen_expr` on
arbitrary expressions from outside that population, so for the two
EXPRESSION arms here, "no body needs this" is not "nothing needs this".
The two STATEMENT arms are unaffected by that caveat -- and none of the
four gets deleted anyway, per the no-piecemeal contract.

**THE DOC IS DELETED, and that is the durable half of this entry.** It was
also stale by half the migration -- last regenerated at dial 1182->1240,
read at 2933 (movers since: `with`/`with_item` 103->15, `assert` 62->12,
`lambda` 60->11, `break` 54->6, `star_unpack` 24->1). Both failures are
the same one: a committed COPY of tool output cannot be told apart from a
current one, so it rots silently and a cold reader -- exactly the person
the file exists for -- is the one who cannot detect it. The same rot hit
the hand-copied zero-witness FACE list in TODO.md: it names 8, the tree
has 26, and 20 of those were never filed. So the summary lines are now
the canonical data (`--thir-codegen` prints arm-residual unconditionally,
on the same footing as the face tally; `$THIR_ARM_RESIDUAL_JSON` gates
only the dump), and prose carries only JUDGMENT -- why a face is
permanently unwitnessed, which arm is worth a wave. Data belongs to the
tool; a dated snapshot in this ledger is honest in a way a
current-looking generated file is not. Do not reintroduce a generated
status doc without staleness detection that fires on ordinary runs.

**LESSON -- a metric whose DONE state is the ABSENCE of a key cannot
report done.** This is the pin-discipline finding's family: an instrument
reading green while proving nothing. The distinguishing feature is that no
amount of staring at the output reveals it -- the missing rows are the
signal, so the only detector is a diff against the key space the metric
COULD name. Seed the key space, or accept that the metric is blind at
exactly the value you built it to find.

**LESSON -- the underivable part of a key space rots immediately.**
`arm_universe()` derives concrete `TpyStmt`/`TpyExpr`/`TpyPattern`
subclasses mechanically, but six body dataclasses carry no shared base and
must be named. The first draft missed two of them (`f_string_value`,
`function`) -- and that was caught by the observed-vs-universe check on a
smoke run, not by review. The summary now emits a WARNING line naming any
observed kind missing from the universe, and unions observed over the seed
so drift can only fail to report a zero, never lose a count.

Pins: `test_arm_universe_covers_what_the_walk_names` (asserts the baseless
trio specifically -- the part that rots) and
`test_arm_universe_excludes_non_body_nodes` (decl containers and
type-annotation refs would park at 0 forever and report non-arms as
deletable). Both verified by revert-injection.

### thir-movable-audit: a raw fact read as a derived one (2026-08-01)

No dial movement -- a correctness pass, not a wave. `lc.movable_locals`
(which drives auto-`std::move` at last use) was seeded wholesale from
`analyzer.function_movable_locals`. That fact means "sema proved this
local OWNED"; the set the move sites read means "owned AND declared by an
arm that PROMOTES". The AST grows the second from the first at nine
var-decl arms. THIR seeded the first and read it as the second.

**The divergence was LIVE, not latent.** Instrumenting both move
predicates and joining their verdicts on the shared `TpyName` (the THIR
overlay re-emits the same node objects, so the join is exact) found 10
disagreements across 6 corpus cases, **5 of them in unmarked, routed
cases** -- a value-typed sema-owned local (a view-promoted `str`, a
BigInt) and a ptr-variant local, which aliases its `__slot_N` rather than
owning it. Byte-identical only because the arms those names reached did
not act on the verdict. After the fix: 1, in a marked case.

**The cheap fix was REFUTED by measurement, not by argument.** Hoisting
`_container_elem_move_source`'s compensations (value-type filter,
ptr-variant filter, value-Optional exception) to the query point regresses
17 cases: the promotion rule is per-ARM, not global -- the frame and
owned-tuple arms promote value-typed names, the tier-1 fallthrough does
not. No type-keyed filter over the raw set can express an arm-keyed rule.
The AST-side consolidation onto one `promote_movable` (previously filed
twice as a cleanup) is what made the mirror auditable: the promoting-arm
set became greppable from one place.

**LESSON 1 -- a shared analyzer fact and a codegen working set are two
facts.** THIR mirrors codegen's WORKING sets elsewhere (`pointers`,
`storage_tuple_locals`); this one was seeded from the analyzer instead
because the raw set was conveniently to hand and agreed on the shapes
that happened to be migrated. When a lowering reads an `analyzer.*` map
directly, ask whether codegen reads THAT map or a set it derives from it.

**LESSON 2 -- a compensating filter can be load-bearing for a reason
other than the one it documents.** `_container_elem_move_source`'s
value-type filter was documented as correcting the raw set. With the set
fixed it should have been redundant -- removing it diverged one case, and
not the expected way: a frame-promoted `Int32` yielded as a TUPLE element
is legitimately movable and the AST renders it bare. The filter is a SINK
rule that was misattributed to the set. Its two sinks then turned out to
DISAGREE (the container-literal sink does move such a local on the AST
path) -- a pre-existing unwitnessed divergence, now filed. Before deleting
a compensation, remove it and read what actually breaks; the reason in
the comment is a hypothesis.

**LESSON 3 -- when THIR seeds from a sema fact, the seed and the working
set must not share a name.** The whole defect is one identifier,
`movable_locals`, meaning "sema-owned" on one path and "promoted at a decl
arm" on the other; the seeding line read correctly in isolation on both
readings. The split into `sema_movable_locals` + `movable_locals` is what
makes a future misuse visible at the call site, and it is the cheap rule
to apply pre-emptively to the remaining `analyzer.*` seeds.

**LESSON 4 -- a known-wrong AST render gets MIRRORED, not diverged from,
and "unreachable today" is a claim to probe.** The
frame-local promotion is imprecise for an await-bind decl, which I
justified in-code as unreachable because such bodies fall back. Review
falsified it: `async/asyncio_queue_nocopy` routes with exactly that bind,
and a two-line probe shows the shape diverging today. The AST side is
itself wrong there (a `@nocopy` payload emits an uncompilable copy) --
both now in BUGS.md. Direction of divergence is the severity axis: THIR
moving where the AST copies is SILENT (a hollowed source), THIR copying
where the AST moves is LOUD (a @nocopy payload fails the C++ build), so
the first deserves the narrow fix and the second can wait.

The narrow fix was then attempted and REFUTED, which is the more useful
record: excluding await inits from the frame promotion breaks
`await_in_with` and `await_in_with_multi_cm`, because a BigInt bound by
`x = await f()` IS promoted on the AST path and moves at its return. The
divergence was never about `await` -- it is `_frame_ptr_locals` failing to
equal the AST's frame-field `pointer_locals` membership for one
classification. Two probes agreeing on a hypothesis (an `Own[record]`
copying, a `@nocopy` failing) still under-determined the RULE; the corpus
is what discriminated. Probe a dormancy claim, and probe the fix too.

Tooling this left behind: `test_thir_movable_set.py`'s verdict-join
detector (the only mechanism that catches this class -- the byte-diff
cannot, since a wrong verdict no arm reads emits identical C++), and
`_assert_routes_byte_identical` in `testutil.py`. The detector runs on
four fixtures; wiring it to the corpus is filed in TODO.md.

### The await-bind move gap: the same lesson, third instance (2026-08-01)

Not a wave -- an AST defect the move-verdict detector surfaced on its
first corpus run, plus the THIR mirror it forced.

`x = await f()` ASSIGNS a frame field rather than DECLARING a local, so it
never reaches `_gen_var_decl`'s promotion and `movable_locals` under-covered
every await-bound name. Its last use COPIED: for a `@nocopy` payload that is
`error: use of deleted function` with no front-end diagnostic -- valid Python
the C++ build rejects. Fixed by promoting at the resume-bind seam
(`gen_async.py`), which is where the binding actually happens.

**THREE consumers had each patched around the incomplete set.** Two died with
it; the third turned out to be a different rule wearing the same clothes:

1. the async direct-ready return passed `sema_movable_locals | movable_locals`
   -- the union, with a comment naming the exact gap it was compensating for;
2. THIR's `_own_move_source_slice` carried a value-type filter, added because
   "a sema-movable scalar would over-move where the AST renders bare" -- true
   only because scalars were never promoted;
3. `_container_elem_move_source` carried the same filter for what looked like
   the same reason -- but removing it diverged the TUPLE element sink. It was
   not a set correction at all: the helper serves TWO sinks that the AST
   itself treats differently (a container literal moves a value-typed movable
   payload, a tuple literal renders it bare), and it was applying the tuple
   rule to both. SPLIT by sink, which closed a routed divergence a list
   literal had been emitting all along. Two of three compensations were the
   set's fault; assuming the third was too would have been wrong.

   The split took two attempts, and the difference was method. The first
   threaded the flag through `_lower_tuple_literal` -- which is not the
   lowerer a yield tuple uses -- and broke both sinks. The second took the
   call sites from an instrumented stack trace, then classified all 17
   callers of the shared helper before touching any. Guessing at lowering
   topology failed three times on this branch and instrumenting worked three
   times. "Instrument first" is advice nobody disagrees with and nobody
   follows under pressure, so the actionable form is a TRIGGER: a hypothesis
   a unit test can settle never gets a corpus run. `test_thir_movable_set.py`
   could have settled at least two of the three in seconds each; instead they
   cost ~10 minutes apiece to be told "no".

Each read as locally justified, each cited the symptom rather than the cause,
and each silently blocked the fix from reaching its own sink. That is the
signature: **when two or more consumers independently compensate for one set,
the set is the bug.** Grep for the compensation, not the symptom.

Also a limit of the detector, worth knowing: it joins the base predicate
(`_is_move_source` / `_is_last_use_movable`), NOT the final emit decision. A
sink that overrides the verdict downstream -- exactly consumer (2) -- reads as
`0 divergences` while the byte-diff differs. The two checks are complementary;
neither subsumes the other.

LESSON: a filter justified by "the other path renders bare HERE" is a
hypothesis about why, not a fact. Both filters above were right about the
render and wrong about the reason, so both outlived the condition that made
them correct.

### thir-alias-decl wave: the decl.slot_type live cluster (2026-08-01)

The four-case `decl.slot_type` cluster the 08-01 site measurement had
diagnosed as "a classifier question, not a slot spelling one" landed as
four decl rows, +4 flips (`operators/getitem_key_return`,
`readonly/readonly_tuple_value_mix`, `tplib/requests_cookies_domain`,
`inheritance/inheritance_multi_base_field_readonly`). Pins in
`test_thir_wave_alias_decl.py`; three earlier boundary pins that had
pinned these exact shapes as rejecting were flipped to routing pins in
place (test_thir_tuples / wave_fieldrecv / wave_unbound_self).

- **User-getitem borrow subscript** (`r = e[k]` -> `Node& r = e[k];`):
  REF_ALIAS row over the existing prechecked record-getitem emit (form
  BORROW). The subscript node carries no resolved fi -- the predicate
  re-resolves `__getitem__` from the registry like the AST fallback, and
  widens the index shapes with a record-typed NAME key (the borrow shim's
  `T&` key param takes the bare name). Own-returning getitems and the
  reassigned POINTER sibling stay pinned out.
- **Borrow-tuple param element** (`b = p[1]` ->
  `const Counter& b = (*std::get<1>(p));`): the first REFERENT consumer
  of a borrow-tuple element. `deref: bool` on THIRSubscript (the deref
  twin of THIRName's flag, beside `opt_deref_check`), set by the
  REF_ALIAS arm off the analyzer-pure `_borrow_tuple_param_elem_subscript`
  -- PARAM receivers only, exactly because the storage-vs-borrow
  membership question that needs walk state cannot arise for a param;
  `Own[tuple]` params (storage form, `std::get` yields `T&`) pinned out.
- **Base-qualified field** (`nums = A.buf` -> `this->A::buf`):
  `_unbound_self_field_ok` joined the borrow-decl admission beside the
  receiver-shape gate (the class-name receiver has no value type), and
  `_lower_field_source` grew the same THIRSelf-receiver spelling as the
  read arm, resumable fence included. Const rides the raw sema
  ReadonlyType via `_f1_is_const`; the mutable-method sibling is
  witnessed non-const in the unit fixture.
- **Field-of-rvalue-call copy decl** (`jar = s.get(url).cookies;`):
  NOT a borrow row -- the measurement's one misread. `Session.get`
  returns `Own[Response]`, so the field is a member of a dying temporary:
  classifier verdict OTHER was already correct, the copy is the only
  legal emit (and C++ selects the move ctor on the xvalue member), and
  the missing piece was an `_owned_record_decl_ok` disjunct
  (`_field_over_call_ok` + rvalue receiver). Opened by explicit user
  ratification as a value-position copy row; the BORROW-returning
  receiver sibling (`h.peek().jar` -- a LIVE object's member, the
  REF_ALIAS design stop) is pinned closed.

LESSON: the registry re-probe that preceded this wave found both
form-design wake conditions stale (Q9 resolved, F3 landed) and two more
entries partially landed (G4 survivors, F5 negation) -- four of eleven
design-gated entries had rotted since 2026-07-30. A parked cell's wake
line is a claim about OTHER work, so it rots faster than the cell itself;
re-probe the wake, not just the blocking body.

### F1 type-arg fence re-scope: three family arms, zero direct flips (2026-08-01)

The approved re-scope from the 08-01 measurement landed:
`_f1_record_type_arg_ok` grew three arms, each admitting exactly the
slice the measurement proved byte-identical at lowering time. Full
corpus green (byte-diff + ratchet + move-verdict join), 0 flip
candidates -- expected: the fence was the PREREQUISITE, the case yield
(the `expr.method_call` receiver trio + `union_mutual_*`) arrives with
the reverted receiver row's re-land, which is the next wave.

- **Enum args** (`Pair[Color]`): admit iff the resolver-side spelling
  equals `to_cpp()` (`enum_cpp_name == name` -- the resolver falls
  through to `to_cpp()` itself -- or `== to_cpp()`), the
  `_f1_dyn_protocol_type_arg` equality pattern. Both name maps populate
  in the generator's setup block, before lowering.
- **Tuple args** (`Pair[tuple[Int32, str]]`): both paths spell
  `std::tuple<...>` recursing elements, so element-wise recursion
  through the fence IS the equality check; a PendingView element
  rejects before anything can call its raising `to_cpp()`.
- **Union args** (`Pair[Num]` / `Pair[Tree]`): admit iff
  `union_alias_names` already holds the members AT LOWERING TIME. Both
  paths read the same map, so a registered alias is identical by
  construction; the check mechanically excludes the module-LOCAL plain
  alias (registered mid-emission at the generator's header pass, AFTER
  lowering -- the real hole the measurement isolated). Local RECURSIVE
  aliases register in the setup block and are admitted.

Pins in `test_thir_wave_f1_typeargs.py` (12 units): per-family routing
+ byte-identity + boundary, plus adversarial pins on the three measured
holes (PendingView arg; local plain alias; the un-imported
@dynamic-protocol arg riding the BUGS.md bare-vs-qualified `Cancellable`
entry -- AST-first, the fence must not route the uncompilable shape).
Three old boundary pins that had pinned enum/tuple args as rejecting
moved to the new boundary in place (test_thir_ctor / _methods flipped
their fixture arg to a local plain-alias union; test_thir_generics'
enum pin became a routing pin). Adjacent arg-row gaps surfaced and
pinned as still-rejecting, NOT opened here: an enum MEMBER-ACCESS arg
into an `Own[T]` ctor slot, and a wrapper-union value into a ctor `T`
slot.

The TODO re-probe rider was answered en passant:
`own_generic_set_forward_ref` now rejects at
`_method_nonname_receiver_ok` (field-chain receiver `h.c.item`), i.e.
purely the receiver row -- the spelling half of its blocker is gone.

### The receiver-row re-land: the F1 re-scope's yield arrives (2026-08-02)

The wave the fence re-scope was priced for: four rows at the
method-receiver and wrapper-union seams, +5 flips
(`set/own_generic_set_forward_ref`, `tplib/rc_in_tuple`,
`union/union_mutual_mixed`, `union/union_mutual_basic`,
`union/union_mutual_reverse_order`). `union/union_mutual_contexts` stays
marked on two OTHER parked families (`container_lit.slot_family` -- the
`t: Tree = [1, [2, 3]]` decl -- and an `if.narrow_shape` fragment), not
on anything this wave touched. Pins in
`test_thir_wave_f1_receivers.py`; two old chain boundary pins flipped to
routing pins in place (test_thir_containers / test_thir_methods).

- **Field-CHAIN method receiver** (`h.c.item.add(x)`):
  `_chain_field_receiver_ok` -- every parent link a plain-value
  F1-record member, innermost link `_field_receiver_ok`-admitted --
  wired at RECEIVER-POSITION scope only (`_method_field_receiver_ok`),
  exactly the scope the reverted first cut should have kept: the shared
  `_field_receiver_ok` also feeds read/write sinks carrying deref/lift
  decisions a chain render does not. The arg-position twin rides
  `_protocol_slot_arg`'s field arm (`len(h.c.item)` bare at a structural
  slot), and `_native_protocol_field_arg`'s list/set element whitelist
  widened to F1-record elements (the element never appears in the bare
  member-read render).
- **Tuple-element F1-RECORD subscript receiver** (`t[0].get()`):
  `_tuple_record_elem_subscript_recv` -- NAME receivers, plain
  (non-Own, non-Optional) record elements. The arrow decision reuses
  `_subscript_yields_borrow_ptr` (the field-read rule): a borrow-form
  tuple param's element is a bare `T*` (`std::get<0>(t)->get()`), a
  storage local's a value (`std::get<0>(pair).get()`). The
  `tuple_to_pointer` call-site lift needed no work -- the
  `_borrow_tuple_storage_name_arg` row already admitted record
  elements.
- **Wrapper-union borrow method return** (`show(v.inner.get())`):
  `method.ru_wrapper_ret` joined `_record_method_call_supported`'s
  record_ret_ok family -- the accessor's `Value&` binds the
  `const Value&` wrapper slot inline; the arg side
  (`arg.recursive_union_borrow_call`) had been built and sat
  zero-witness waiting for exactly this ret row.
- **Member-CTOR rvalue at a wrapper-union arg slot**
  (`eval_expr(Lit(42))` -> `Expr __tmp_N = Lit(...);`):
  `_ru_wrapper_member_rvalue_arg`, the ctor sibling of the M4c
  literal/NAME rows, same create_typed ArgTemp; is_rvalue_source keeps
  borrow-returning call sources out (pinned).

LESSON: the "receiver trio + union_mutual family" price was quoted
against the fence alone, but the family's blockers were STACKED -- the
fence hid a ret-gate gap, which hid an arg-temp gap. Post-fence
re-probing per case (rather than trusting the original trio/family
labels) found each next blocker in minutes; the labels from the
pre-fence map were all stale by one layer.

### Mechanical residue, first slice: the owning reseat + the indirect
### opt-ptr lift (2026-08-02)

Two rows off the F3/parked-registry residue, +3 flips
(`tplib/rc_recursive_type`, `pointers/escape_explicit_rc`,
`tplib/weak_cycle_breaks`). Pins in `test_thir_wave_owning_reseat.py`.

- **Method-call rvalue at the REBIND_SLOT decl and reseat**
  (`cur = a.clone()` -> `Rc<Node>* cur = &__slot_1;` ...
  `cur = &*(__slot_2 = nxt->clone());`): the classifier already returned
  REBIND_SLOT for the rvalue-reassigned name; only the two eligibility
  gates (`_borrow_local_binding`'s F2d arm, `_rebind_rvalue_source_ok`)
  were ctor/free-call-shaped. Both grew the method-call disjunct
  `_owned_record_decl_ok` already carried; the method-call lowering's own
  gates validate callee/args from there. A borrow-returning method decl
  stays pinned out (not an rvalue source).
- **OPTIONAL_TO_PTR lift off a field of a method-call receiver**
  (`parent_ref = child.get().parent` -> `Weak<Node>* parent_ref =
  ::tpy::optional_to_ptr(child.get().parent);`): the
  `_indirect_field_receiver_ok` arm from the receiver-row wave, applied
  at the borrow-decl classifier -- restricted to METHOD-call receivers
  (a free-call receiver could hand the lift a dying temporary).
  `_f1_is_const`'s OPTIONAL_TO_PTR arm learned the non-Name receiver:
  const iff the inner call binds const (readonly ref return, or a
  const-rooted receiver picking the const twin) -- the const sibling is
  pinned (`const Rc<Node>* nxt = ...` off a readonly param).

Notable: `weak_cycle_breaks` flipped WITHOUT touching
`_const_exact_field_receiver_ok` (the registry had queued it behind that
fence's stale-BUGS citation) -- its actual blocker was the receiver
shape, not the const fence. The remaining F3 residue (mixed-own hybrid
tuple locals with element writes, the borrow-form tuple local binding
set, Own[tuple] returns, OPTIONAL_BORROW_TUPLE, the Pending-str
`Box(s)`/`Rc.new(s)` ctor+view-temp family, the Rc rvalue-arg row, the
generic-record MIL field default) is queued as the next slice -- each
needs its own binding/temp machinery, not a gate widening.

### Mechanical residue, second slice: Own-method rvalues at two more
### sinks (2026-08-02)

Two rows reusing the shared `_method_rvalue_f1_record` disjunct, +2 flips
(`tplib/rc_new_arg_passing`, `tplib/rc_field_default_init`). Pins in
`test_thir_wave_own_method_rvalues.py`.

- **The borrow-param arg temp** (`read_rc(Rc.new(Counter(3)))` ->
  `Rc<Counter> __tmp_1 = ...; read_rc(__tmp_1)`):
  `_record_rvalue_temp_arg` grew the method-rvalue disjunct and the
  ArgTemp hoist arm's method-call exclusion was re-derived from the
  oracle: PLAIN and MODULE-QUALIFIED Own-returning methods hoist the
  create-lend-drop temp (the old defer-pin for `use(sub.make(9))`
  flipped to a verified routing pin -- the AST hoists there too);
  NATIVE record calls and MARKER-position args render inline. The
  marker half was caught by the corpus byte-diff mid-wave
  (`stdlib/ospath_roundout` diverged: THIR hoisted
  `samestat(s, os.stat(d))`'s inner call where the qualcall loop
  renders inline) -- fixed by threading a `marker_arg` flag from
  `_lower_marker_method_arg` into `_lower_call_arg`, since neither
  const-ness nor the callee's own fi discriminates (the OUTER callee's
  emit path does).
- **The ctor MIL field** (`self.shared = Rc.new(Val(0))` ->
  `shared(Rc<Val>::new_<Val>(Val(0)))`): `_is_record_value_source` grew
  the same disjunct and the MIL tail lowers the method source at
  STORAGE use (face `mil.record_method_rvalue`); a borrow-returning
  method at the field slot stays pinned out.

LESSON: "the AST renders qualified non-ctor record calls inline" was a
one-example generalization (os.stat at a MARKER call) that a second
example (sub.make at a FREE call) refuted -- the axis was the enclosing
callee's arg loop, not the arg's callee kind. The corpus byte-diff
caught the wrong first guess within one measurement run; the defer-pin
it violated was doing exactly its job.

### Mechanical residue, third slice: Pending-view Own args + the movable
### tuple-return member (2026-08-02)

Two rows, +3 flips (`tplib/box_str_lvalue`, `tplib/rc_new_str_lvalue`,
`tplib/weak_outlives_payload`). Pins in
`test_thir_wave_pending_view_own.py`; the wave-1 PendingView hole pin
flipped from "keeps rejecting" to "resolves and routes" (the hole is
RESOLVED, not fenced, as of this slice), and contlit's storage-tuple
NAME-member boundary flipped to a routing pin with the non-last-use
sibling as the new boundary.

- **Pending-view type-args resolve through the fence**:
  `_f1_record_type_arg_ok` gained a PendingViewType arm that resolves
  via the existing `_resolve_pending_view` mirror (sema's usage
  resolution is final pre-lowering) and recurses. The raw `to_cpp()`
  still raises by design -- admission is safe because the spell sites
  in the routed paths go through the resolver (`lc.render_type`). The
  Own[T] slot's VIEW payload then takes the brace-init copy temp
  (`std::string_view __tmp_1{s};` + `std::move(__tmp_1)`):
  `_own_lvalue_temp_slot` resolves Pending on both sides and admits a
  str-family NAME at a view-resolved slot (form-blind -- an owned or
  view local renders the same bare name inside the braces), and the
  ArgTemp arm's brace-init render extends from `is_str_type` to the
  view payload. The owned-str NAME source stays pinned out (its
  view/owned form split is a real render axis).
- **Own[tuple] returns admit MOVABLE NAME members**: the storage-tuple
  return gate's "non-value members must be rvalues" rule grew the
  movable-last-use disjunct; `_lower_container_elem` already carried
  the `std::move(name)` render. A non-last-use name member (the same
  name feeding two members) stays pinned out.

SKIPPED with evidence, back to the registry: (a) the mixed-own tuple
LOCAL binding set -- the AST computes `const_borrow_form_tuple_locals`
via a per-function FIXPOINT over binding chains
(codegen_cpp/statements.py:356), and mirroring a fixpoint is a design
decision (mirror strategy vs moving the fact), not a gate widening;
(d) OPTIONAL_BORROW_TUPLE -- zero THIR handling, same class. Both stay
in the F3-residue registry entry as design-gated.

### Grind wave 4: the method_call junk-drawer site, two rows (2026-08-02)

Fresh corpus map (blockers.json, 746 marked): the sole-blocker ranking is
a long tail (top tag `body:expr.method_call` = 10 cases over FOUR raise
sites; second `expr.call` 8). The top site (the record-method shape gate)
held four unrelated shapes -- two on parked lanes (deferred-inference
generics; the borrow-form auto-tuple decl, the same binding-set design
class as the mixed-own hybrids) and two live, both landed. +6 flips
(`iterators/span_iter_arraylist`, `auto_move/consuming_method_reassign`,
plus four the harvest surfaced: `imports/cross_module_builtin_type_field
_decl`, `tuple/tuple_own_outer_return`, `threading/mutex_wrap_cross
_module`, `records/nocopy_del_field_via_factory`). Pins in
`test_thir_wave_grind4.py`.

- **`method.native_iter_ret`**: a VALUE-typed native iterator method
  result (SpanIter) at a STORAGE sink -- the decl slot admitted the
  shape all along (`_native_iter_value_slot`'s docstring even names
  `a.__iter__()`); only the record-method ret gate was missing the row.
  Opening it also unblocked the @auto_readonly `__iter__` clone pair
  (iterdecl's boundary pin converted to a routing pin -- its stated
  fence reason WAS this ret gate).
- **Consuming POINTER-LOCAL receivers**: `std::move(*w).take()` -- the
  consuming_ok gate dropped its pointer exclusion, the method-call emit
  folds the arrow into the deref (`std::move(*{recv})` + `.` access),
  and the node invariant now permits move_receiver+is_arrow. The
  narrowed-Optional-ptr receiver turned out to be the SAME shape on
  both paths (pinned routing, not boundary).

Remaining method_call sole-blockers, all parked or priced for wave 5:
deferred generics / function-macro bodies / overload+generic (lanes),
iterator `__next__` protocol returns (parked), `os.walk` generator
factory (generics-lane), requests_session (a G3 arg sub-shape),
rc_dyn_structural (the G4 both-targs 1:1).

### Grind wave 5: the value-union ctor-arg temp chain at decl sinks
### (2026-08-02)

The `body:expr.call` sole-blocker tag is fully fragmented (8 cases over
six raise sites); the top pair (`match/nested_type_pattern_combos`,
`match/nested_type_pattern_edges`) was one blocker STACK five links deep,
all landed by chain-walking. +2 flips. Pins in
`test_thir_wave_grind5.py`.

- The union ptr-slot and opt-ptr-slot DECL inits thread `allow_temps`
  (a decl statement is a flush position -- the AST hoists a member
  ctor's value-union arg temp before the `__slot_N` line).
- `_ptr_union_slot_kind` admits a sema-coerced scalar LITERAL as a
  whole-union rvalue (`b3: Int32 | Container = 99` -> the bare literal
  slot init; the variant's converting ctor picks the single matching
  member).
- `_union_ctor_temp_arg` admits GENERIC member ctors
  (`Outer(Box("abc"))` -- `_ctor_instantiation_ok` beside
  `_ctor_shape_ok`).
- Container-literal ELEMENTS inherit the enclosing flush right
  (`allow_temps` threaded through `_lower_checked_container_elem` /
  `_lower_container_elem` off `use.allow_temps`), so an element ctor's
  union/Own arg temps hoist at the decl statement exactly like the AST.
  This also flipped ctorargs7's not-last-use Own-copy pin to a routing
  pin (the AST hoists `auto __tmp_1 = a;` there -- byte-verified).
- The EMIT's ptr-decl arms flush pended temps before their slot lines
  and the VALIDATOR grants THIRPtrLocalDecl inits the flush position --
  the ordering divergence the corpus byte-diff caught mid-wave (temps
  after the slot lines) was exactly this missing drain.
- Boundary: a ternary ARM stays flushless (its temp must not hoist
  eagerly); pinned.

The rest of the fragmented expr.call tail: argparse/fixed_width_types,
owned_optional_local_move_out, cross_module_dup_name_union,
isinstance_typeparam_tuple (1:1 singles, next grind iterations);
set_comp_owned_move stays the parked comprehension-temp-sink design
fork.

### Grind wave 6: the type-ctor str-parse overload (2026-08-02)

+1 flip (`argparse/fixed_width_types`). `Int32(tok)` on a str token
resolves the from_str `__init__` overload whose own @cpp_template
carries the parse render (`::tpy::from_str_check<int32_t>(tok)`); the
scalar-ctor arg gate admits a str-family arg at a str param slot beside
the scalar check. The witness lives in a builder-trace-macro-synthesized
parse body, so the row's reach is any `IntN(str)` conversion. Pins in
`test_thir_wave_grind6.py`.

Probe notes for wave 7, recorded before parking:
`auto_move/owned_optional_local_move_out` is a two-link stack -- the
`Boxed(tmp)` ctor arg's ORACLE render is `::tpy::ptr_to_optional_move`
(the FormConvert move lift the emit already carries), NOT the
THIROwnOptRebuild the existing Own-opt row spells, and the gate rejects
before either (instrument `_own_opt_ptr_name_arg`); plus a
`call.ret_type.optional_record` return link.
`imports/cross_module_dup_name_union`'s member-NAME lift rejects
silently in `_union_member_lift_arg` -- likely the colliding-short-name
spelling fence (two `Point`s from different modules in one variant;
probe BOTH members' spellings before widening).

### Grind wave 7: the owned-optional move-out stack (2026-08-02)

+1 flip (`auto_move/owned_optional_local_move_out`) -- one case, FOUR
stacked links, each landed by chain-walking. Pins in
`test_thir_wave_grind7.py`; optrecord's narrowed-field-read defer pin
converted to routing (its stated fence WAS the last link).

- **ptr-repr Optional NAME at an `Optional[Own[record]]` BY-VALUE slot**
  (`Boxed(tmp)` on `tmp: Box | None` -- the `std::optional<Box>` param):
  `_opt_own_ptr_opt_name_arg` + a FormConvert STORAGE move arm renders
  `::tpy::ptr_to_optional_move(tmp)` at a movable last use. The COPY
  half (`ptr_to_optional`, a non-last-use source) stays pinned out.
  NB the wave-6 probe note guessed the slot was Own[Optional]; it is
  Optional[Own] -- a different row family than THIROwnOptRebuild's.
- **Storage-optional free-call returns**: `_call_use_supported` gained
  the `_storage_optional_return_type` STORAGE escape the method gate
  already had (`std::optional<Box> r = move_out(true);`).
- **Narrowed owned-optional record NAME receivers**: the field-access
  receiver chain admits a `value_opt_record_locals` name; the name read
  derefs and the field appends (`(*r).v`).

`imports/cross_module_dup_name_union` (the union-lift colliding-name
fence) stays the wave-8 opener.

### Grind wave 8: the short-name collision override (2026-08-02)

+1 flip (`imports/cross_module_dup_name_union`). The `_ctor_shape_ok`
fence rejected any ctor whose LAST-WRITE-WINS short-name registry lookup
disagreed with sema's qname-first type resolution ("reject the
ambiguous shape rather than mirror the override") -- but the AST emits
FROM THE TYPE, and the THIRCtorCall lowering already reads
`get_record_for_type`, so validating against the type-resolved record
is the honest mirror, not an override. Instrumentation showed
`get_record("Point")` returning world/Point while sema (and the oracle)
resolved screen/Point. Pins: both colliding records exercised in ONE
fixture (ctors spell their own modules' qualifications; both union
members lift fully qualified) per the
survey-both-colliding-types-in-one-container rule; the old
falls-back boundary pin in test_thir_methods.py converted to routing
(both Tag ctors route qualified).

### Grind wave 9: comp unpack heads with record targets + the
### storage-form tuple return at an Own slot (2026-08-02)

+1 flip (`list/list_comp_unpack_clone`). Two pieces, chained by the one
case. (1) `_comp_route`'s unpack arm now admits record-element tuple
iterables (`allow_record`, the for-head unpack's knob) and drops the
const-only F1-target admission: the AST's `_emit_inline_tuple_unpack`
keys the ref binding (`auto&` / `const auto&`) for EVERY non-value
target on `gen.const_loop_var` alone, so `_unpack_target_cpps` now
threads that flag instead of hardcoding `const auto&` (the const-only
arm was DEAD in practice -- a plain record-element tuple is a value
type and not expensive-copy, so `worth_const_ref` never set the flag;
the const side IS reachable via a str+record tuple and is pinned).
(2) Routing the body exposed a latent mis-lowering: `_lower_call_arg`'s
Own[ptr-repr tuple] slot arm wrapped EVERY tuple-returning call in
`tuple_to_storage`, but a STORAGE-form return -- `Own[tuple[..]]` or
the all-Own per-element synthesis -- already matches the owning slot
and passes bare (`push_back(make_pair(1, 10))`;
`needs_tuple_storage_lift`'s call verdict); only borrow-form and
MIXED-render returns owe the copy lift. The wrap had never materialized
before: every prior witness fired in bodies that later fell back whole
(witness-before-raise), so the corpus byte-diff could not see it --
the divergence surfaced the moment the first consumer body routed.
Boundary walked, not forced: the site's remaining cases are other axes
(storage-optional unpack targets need the `storage_form_optional_locals`
mirror THIR explicitly does not have yet -- a future multi-cell track;
narrowed-ternary elements; inline-comp render residue).

### Grind wave 10: the top_level:expr.call site, three rows (2026-08-02)

+4 flips (`generics/generic_func_regression`, `pascal/bubble_sort`,
`pascal/panic_array_index` -- caused by this wave's rows -- plus
`int/panic_int32_from_str`, which was already clean before the wave and
harvested as a stale marker in the same check-flip pass). (1) The zero-arg
container instantiation with EXPLICIT type args (`items = list[Int32]()`)
rides the existing `call.instantiation_empty` arm: the `not e.type_args`
exclusion was a phantom fence -- sema folds the spelling into `call_type`
and the zero-arg render reads nothing else (the user-generic subscript
form is excluded by `subscript_callee`, which builtins never set, and
already routes on its OWN arm -- the boundary pin asserts non-capture,
not rejection). Array joins the family disjunct (`Array[Int32, 8]()`
locals). (2) The same call synthesized by the pascal frontend arrives
with a CONSTRUCTOR fi and dies at the ctor gate instead --
`_ctor_instantiation_ok` gets a zero-arg builtin-container row (same
`type_cpp()` render, no arg arms to diverge). (3) The global ptr-slot's
address-of catch-all widens from method calls to free calls
(`p = &(get_item<Point>((*points), 0));`) via the `use.addr_call`-scoped
`call.recv_borrow_ret` row. LESSON: the first cut keyed that row on bare
RECEIVER use and the unit suite's design-stop pin
(`TestRecordBorrowCallReturnDesignStop`) caught the over-capture -- a
field read off the same borrow-returning call shape (`shared(a).x`) is
the REF_ALIAS place/loan frontier and must stay AST; the row is now
position-scoped by a dedicated `_ExprUse.addr_call` flag, the design
fence re-verified green. `calls/unsafe_cast_arg_type_context` chain-walked
off the site (now a method-ARG gate reject at `parg_list.append(carg_ptr)`
-- a pointer-slot GLOBAL name arg, the M-family's business) and stays
marked; the tag's other two attributed singles
(`generics/generic_func_multi` at the generic plain-arg gate,
`records/optional_field_ctor_param_optional` at a ctor-arg family) are
1:1 residues at their own sites.

### Grind wave 11: the ptr-Optional tuple-field element stack
### (2026-08-02)

+1 flip (`tuple/tuple_mixed_elem_field`), one case chaining three rows.
(1) The DECL lift: `first = h.t[0]` classifies OTHER via the
classifier's tuple carve-out (the AST pre-lifts tuple-element reads at
the CONSUMER, so `reads_storage_form_optional` deliberately returns
False for tuple receivers) -- the decl IS that consumer, so the OTHER
re-tag arm gains the subscript row and the borrow-local lowering wraps
the prechecked subscript read in the optional_to_ptr FormConvert
(`Box* first = ::tpy::optional_to_ptr(std::get<0>(h.t));`). Const rides
the existing `_f1_const_rooted_source` subscript-receiver arm (pinned
via a readonly receiver). (2) The SAME-repr pointer-Optional NAME
element at a borrow-tuple literal slot passes bare (`h.set((n, y))` ->
`{n, &(y)}`): the builder's optptr arm only knew the addr_of render and
deferred already-pointer sources. Converting that row un-deferred THREE
existing boundary pins (return-position btuple literal, post-if
narrowed ctor arg, resumable yields) -- each byte-diffed IDENTICAL and
converted to a routing pin per the conversion rule. (3) The
`is [not] None` subject over the same element read takes the pre-lifted
pointer compare (`optional_to_ptr(std::get<0>(h.t)) == nullptr`).
Pins in `test_thir_wave_grind11.py` (+ the three conversions); the
local-tuple-receiver sibling stays deferred (field receivers only).
The tag's other two sole cases are DOCUMENTED fences, not rows:
`list_append_optional_strview_from_str` waits on the value-opt read
arms keying on the BINDING rather than the narrow (the literal-init
divergence note inside `_owned_view_opt_whole_src`), and
`ternary_optional_mixed_form` needs per-arm Form threading through
`expr.ifexpr` (the res.btuple_source theme). NB: a lesson re-learned --
a broad `str.replace` on a test file clobbered nine sibling defer pins
sharing the same tail; caught by the file's own suite run, redone
scoped.

### Grind wave 12: the flushless Own-slot MOVE rescue (2026-08-02)

+1 flip (`async/await_bind_move`). `_own_lvalue_arg` (the Own-slot
copy+move gate row) is temps_ok-gated because its COPY half hoists a
`__tmp_N`; the MOVE half renders `std::move(<name read>)`
position-independently and already had a flushless rescue on the
native/template branch -- the plain branch gains the identical rescue
(`_own_move_source_slice`), unblocking the await-bound frame local at
an `Own[T]` param inside a resumable leaf (`take(std::move((*p)))`;
the name's own read supplies the deref; the move-verdict join covers
the verdict, 5 joined nodes on the flipped case). The rest of
`resumable:expr.call` PARKS with verified reasons: `await_bind_no_move`
+ `async_own_copy_escape` need the COPY half's temp at a leaf position
-- the leaf emitters have no hoist-line drain (the deliberately-
deferred structural piece already noted on `decl.record_slot_resumable`
in TODO.md; wiring the drain is a design decision, presented not
improvised); `channel/channel_async` chains through the coro-factory
adapter into the asyncio library surface (avoid-listed, S1/S2);
`async_bind_generic` + `gen_resumable_delegate_generic_call` are
generics-lane (parked). Pins in `test_thir_wave_grind12.py`: the
deref-move routing pin and the copy-shape boundary (still defers).

### The ifexpr Form-threading wave: per-arm ternary normalization
### (2026-08-02)

+3 flips (`control_flow/ternary_optional_mixed_form`,
`control_flow/ternary_optional_record`,
`auto_move/ternary_lvalues_into_own`; dial 2971/3696) -- the first
design item off the parked queue, approved as three cells against the
`expr.ifexpr` result gate. (A) The gate admits pointer-repr
Optional[F1-record] results; `_lower_if_expr` normalizes each arm to
the `T*` the result renders as, mirroring `_gen_if_expr`'s
`_ptr_optional_branch`: `None` -> a BORROW None literal (`nullptr`),
an already-pointer Optional binding -> bare, a storage-form Optional
field -> the F2 `optional_to_ptr` FormConvert, a plain F1-record name
-> the return ladder's position-independent `THIROptionalPtrArg`
addr-of. The whole ternary carries Form.BORROW -- the Form fact the
item's name promised, consumed by every sink. Narrowed-occurrence arms
key on the ANALYZED vs DECLARED type split and reject (sema refuses
the record narrowed-arm join outright, so no witness can exist). (B)
Sinks: the OPTIONAL_TO_PTR classifier re-tag row for ternary inits
(the classifier's `reads_storage_form_optional` does not walk arms) +
the bare-bind decl arm, and the ptr-opt return-ladder ternary row.
(C) A plain F1-record ternary of lvalue NAME arms admits as a bare
BORROW lvalue (the AST's gen_expr_deref arms); the Own-slot COPY half
captures it into its `auto __tmp_N` -- `ternary_lvalues_into_own`
flipped with zero consumer work. Two rider rows fell out of grinding
`ternary_optional_record` to zero: the BORROW-returning ptr-opt free
call binding bare at its decl (`Point* r1 = get_or_none(true, p);`,
`ptr_opt_passthrough`'s second consumer -- the guard requires the
CALL's analyzed type be the borrow Optional, after the ctor-call
rvalue `Inner(7)` slipped the first cut and broke four unit files),
and the whole ptr-opt name print (`::tpy::print_optional(r2)`, new
PrintForm.OPT_PTR). `_f1_is_const`'s OPTIONAL_TO_PTR receiver bump is
now explicitly field/subscript-shaped (a ternary init would have
crashed on `.obj`; the AST's `is_const_union_source` returns False for
non-lvalue-chain shapes, so skipping the bump is the mirror). THREE
former fences un-deferred, each byte-verified then converted per the
rule: the binding-facts "optional call-return local is an owned slot"
fence (the oracle renders the bare bind -- the claim was stale), the
Own-slot ternary arg-gate reject, and the resumable erased-ternary
await operand (`await (t if c else u)` routes -- the record arm's
BORROW lvalue is exactly what the erased operand consumes).
Boundaries pinned in `test_thir_wave_ifexpr.py`: a record ternary
with a CALL arm defers (a C++ prvalue mixed with an lvalue), an
Optional METHOD-CALL arm defers, a container-literal ternary ELEMENT
stays out (the AST's in_container_element carve-out renders storage
there; the element gate keeps the body fenced), and an Own-declared
`Own[T | None]` callee keeps the slot lane (a bare bind would
dangle). Suite green (10722 passed), move-verdicts 0/746.
`returns/return_mixed_safe_sources_ternary` peeled its ifexpr blocker
but stays multi-blocked (call-arm ternary + `call.ret_type.record_borrow`).

### The resumable-leaf temp-drain wave (2026-08-02)

+2 flips (`async/await_bind_no_move`, `async/async_own_copy_escape`;
dial 2973/3696). The queue's second design item -- and the
investigation REPRICED it: the drain already existed at the emit
layer. `emit_leaf_stmt` renders through the same render-then-flush
statement arms as a sync body (`_emit_stmt` renders the value, flushes
`state.temps`, then writes the line), on the ctx-backed TempSink
(`temps=CtxTempSink(ctx)` in the seam constructor -- shared `__tmp_N`
numbering with the AST counter). What blocked the witnesses was
lowering-side gating, and the two had DIFFERENT root causes. (1)
`await_bind_no_move` was the real drain case: the leaf frame-field
assign (`_lower_frame_field_assign`) lowered its init without
allow_temps, so the Own-slot COPY half (`auto __tmp_1 = (*p);
first = size_of(std::move(__tmp_1));`) rejected -- opening
allow_temps there is the whole fix, zero emit changes. (2)
`async_own_copy_escape` needed no temp at all: its oracle is the
inline `C __tpy_async_ret = C(__self);` -- the resumable return tail
lowered its value at the default VALUE use, so the record-returning
`copy(self)` failed `_call_use_supported`. The tail now lowers with
the sync return arm's STORAGE result use (the scaffolding IS a
storage decl sink) plus the sync sink's `_lower_copy_record`
interception row (copy() is rejected by the special-builtin gate in
the generic call tail, so every sink intercepts it; the resumable row
uses the DEFAULT excluded-source set, not the sync sink's
admission_pointers override -- that knob is preserved verbatim for a
filed defect and must not propagate). Boundaries pinned in
`test_thir_wave_leafdrain.py`: a copy-temp INSIDE a return value
still defers (the skeleton composes the value render into its own
scaffolding line; no oracle has verified a flush point there), and a
frame_slot emplace write with a temp-needing init still defers (the
emplace arg composes inside the skeleton's line -- its own rung).
The function-top HOIST-line family (rebind slots, RECORD_HOISTED,
dyn-protocol slots; `hoist_drainable=False` loud rejects) is
explicitly NOT this item -- in a resumable those need frame-field
homes, a separate design (`decl.record_slot_resumable` stays).
The wave-12 copy-defers pin byte-verified and converted to the
routing pin (`TestAwaitBoundCopyRoutes`). Suite green (10731 passed,
exec 3695 built+run), move-verdicts 0/747.

### The value-opt rekeying wave: binding-keyed reads on the kind map
### (2026-08-02)

+2 flips (`list/list_append_optional_strview_from_str`,
`control_flow/ternary_optional`; dial 2975/3696). The queue's third
design item, four cells. (1) CONSOLIDATION, its own byte-neutral
commit: the three parallel value-opt sets (`value_opt_locals` /
`_view_` / `_record_`) became one kind-tagged map
(`value_opt_bindings: name -> ValueOptKind SCALAR|VIEW|RECORD`) with
the `_value_opt_binding_kind` resolver folding the param lookup;
kind-blind consumers (the None-test cascade) test membership alone;
`branch_scope` snapshots by whole-copy. (2) The READ-ARM REKEYING:
the VIEW name row now honors `allow_whole_optional` exactly like its
scalar/record siblings, so a whole-optional consumer reads the
BINDING regardless of sema's narrow -- the fence's probe-caught
divergence class (`(*src1).has_value()` where the AST renders
`src1.has_value()`) is dead, and the narrowed value-opt name
truthiness routes as `::tpy::is_truthy(<whole>)` (the
`truthy.optional_name_narrow` fence's binding-keyed half). (3) The
LITERAL/None-init owned-view decl opened
(`std::optional<std::string> src1 = "one";` / `= std::nullopt;` --
`_owned_view_opt_whole_src`'s NB note resolved), plus the
`Own[Optional[StrView]]` element-slot coercion rows: the NARROWED
occurrence arrives as the identity `str_to_strview` coerce and passes
the whole optional BARE (C++'s optional converting ctor absorbs it);
the un-narrowed `optional_str_to_strview` renders the once-evaluated
`({ auto __ov = ...; })` statement-expression shim (VALUE form -- the
stmt-expr is a prvalue optional-of-view). The narrow fact is
load-bearing at exactly this site while reads key on the binding --
the reason this was an audit, not a toggle. (4) The VALUE-repr
Optional ternary: both arms wrap in the spelled optional
(`std::optional<std::string>(std::nullopt)` /  `(..)("hello")`) for
C++ ternary deduction, witnessed at the opt-view return row --
literal arms only, a NAME arm keeps the named reject (pinned).
Boundaries pinned in `test_thir_wave_valueopt.py`: the name-arm
ternary defers, and a value-opt view PARAM at the Own element slot
stays out (the borrow `optional<string_view>` binding is a different
shim family, unwitnessed). Suite green (10736 passed, exec 3695
built+run), move-verdicts 0/747.

### The storage-optional comp/genexpr unpack mirror (2026-08-02)

+1 flip (`tuple/comp_tuple_unpack_optional`; dial 2976/3696) -- the
queue's last design item, opening the track the wave-9 lesson
predicted. `lc.storage_opt_locals` now mirrors codegen's
`storage_form_optional_locals` for COMP/GENEXPR unpack targets
binding a ptr-repr Optional[F1-record] tuple element: the comp
route's per-target family gate and `_unpack_target_cpps` gained the
optional row (the same `auto&` ref binding as F1-records --
`auto& p = std::get<0>(__tup_1);`), the shared
`_container_scalar_tuple_iter` iterable gate widened behind a
comp-only `allow_storage_opt` flag (the for-STATEMENT head keeps its
own reject), and registration scopes itself with the body walk like
the loop-var storage-form registration. Reads of the registered name
render the bare STORAGE optional (`name.storage_opt_whole`; a
NARROWED occurrence is a named later rung), and a `T*` arg slot lifts
via `::tpy::optional_to_ptr(p)` -- the NAME sibling of the field
'lift' face, riding THIROptionalPtrArg. The GENEXPR grew unpack heads
(`THIRGenExpr.unpack_targets`/`unpack_target_cpps`; the lambda body
binds `auto& __tup_N = *__beg++;` + per-target lines, the `__tup_N`
drawn off the shared per-function counter at emit exactly like the
comp) -- scalar and storage-optional elements only, a moved
container-literal source stays out. Boundaries pinned in
`test_thir_wave_stgopt.py`: the for-statement head
(`tuple.ref_target`), the narrowed target read, and filtered
genexprs all keep deferring. Future rows this track opens: the
for-head producer, narrowed reads, the None-test, the const twin --
ordinary cells once witnessed. Suite green (10740 passed), move-verdicts
0/747.

### Long-tail wave 1: the expr.method_call sole-blocker tag (2026-08-03)

+6 flips (dial 2982/3696; branch `thir-longtail`): `iterators/
iter_builtin`, `inference/deferred_generic_{basic,multi_param}`,
`protocols/{rc_dyn_structural,overload_nested_generic_call}`,
`tuple/readonly_method_borrow_tuple_return`. Four cells against the
tag's fresh site histogram (8 sole-blocker cases, 4 raise sites):

- Protocol-receiver ZERO-ARG @cpp_template dunder stubs
  (`it.__next__()` on `Iterator[T]` inside the try-unwrap): the same
  template expansion on both paths; arg-carrying templates keep
  rejecting (pinned, `test_thir_wave_prototemplate.py`).
- The pending deferred-generic minimal fi now carries the method-kind
  bits (sema-side commit, snapshot-neutral) and the record-method arg
  gate grew the scalar-into-raw-`T`-slot row; record/str args into a
  pending `T` slot keep rejecting (`test_thir_wave_pendingfi.py`).
- Same-module generic statics with representational substitution
  spell the Adapter method-targ override via the shared
  substitute_type_params_simple + dynamic_adapter_type mirror
  (`Rc<Pet>::new_<::tpy::Adapter<Pet, Cat>>(..)`,
  `test_thir_wave_reprsubst.py`); the module-qualified static arm
  keeps rejecting repr-subst calls.
- The mixed owned+borrow tuple (`tuple[Own[A], B]`) at the
  SINGLE-BINDING `auto` alias decl: the documented port recipe landed
  as the exclusion swap (fully-owned stays out; `_f1_tuple` admits
  Own elements all along) + the record-method `btuple_ret_ok` knob.
  The REASSIGNED mixed local (the const-borrow-form FIXPOINT family,
  still design-gated) and the mixed unpack keep rejecting -- pinned
  (`test_thir_wave_mixedtuple.py`); the old still-defers pin whose
  reason was "unported, tracked migration work" converted to routing.

Parked with one-liners: `stdlib/os_walk` (the 07-29 kwarg residue),
`tplib/requests_session` (bytes/dict union-slot members sit outside
the F4 U2 member slice -- widening the slice is a family-boundary
design call), `macros/function_macro_post_sema_mutate` (macro lane).
Suite comp-green (10748 passed), move-verdicts 0/747.

### Long-tail wave 2: the isnot-optional cond tag (2026-08-03)

+4 flips (dial 2986/3696): `auto_move/auto_move_own_optional_{narrowed,
param}`, `dict/dict_optional_value_access`, `list/list_optional_subscript`
-- 4 of the tag's 5 sole-blocker cases (all at ONE raise site, the truthy
fallback's binop re-tag). Two cells:

- `Optional[Own[record]]` PARAMS (`Own[Point] | None`, a by-value
  `std::optional<Point>`) ride the RECORD-kind value-opt seed: has_value
  None-tests, position-blind `(*p)` narrowed derefs. The pin run caught
  the SPELLING SPLIT with the mirror-image `Own[A | None]` form
  (`std::optional<A>&&` + pointer_locals, `a->v` operator-> reads) --
  that family stays fenced; two stale fences whose reasons this seed
  removed were dualgen-byte-verified and converted
  (`test_thir_optparam` / `test_thir_wave_optrecord`).
- Storage-form Optional container-subscript consumers: the has_value
  None-test over the bare `__getitem__` read, the checked field read
  (`deref_optional_check(...)`.x -- the container twin of the
  Optional-member arm), the OPTIONAL_TO_PTR decl off the subscript, and
  the storage-opt LOOP VAR (`for v in d.values():` -- the for-STATEMENT
  producer of the storage-opt set, container route only; T* arg slots
  lift via optional_to_ptr). Two stale reject pins converted after
  dualgen verification (varargs / field-chain optional elements).

The corpus sweep caught a REAL divergence the loop-var admission opened
(`list/list_optional_readonly_field`: a @readonly method's const
iteration -- consumers spell `const P*`, the unmirrored const twin) plus
a binding-hole in the name-copy OPTIONAL_TO_PTR row (type equality
admitted a STORAGE-bound source). Both closed: a const source now
rejects the loop whole (an unregistered storage loop var must never
enter `declared` -- every Optional-ptr consumer is type-keyed), and the
name-copy row requires a POINTER-bound source. Lesson re-learned: the
elem-gate widening reached the FIELD-iterable path through the shared
route classifier -- a widening's reach is the classifier's, not the
witnessed call site's.

Remaining tag residue: `calls/callable_optional_param` (the narrowed
`(*f)(x)` invocation deref, unmirrored) and
`optional/opt_dict_key_iter_narrow` (Optional-ptr borrow param with a
CONTAINER inner + narrowed iteration/setitem) -- next cells, not parked.
THIR units green (4298), move-verdicts 0/750.

### Long-tail wave 2 addendum: callable-optional (2026-08-03)

+1 flip (`calls/callable_optional_param`; dial 2987/3696): the
`Callable | None` param's has_value None-test plus the `.value()`
invocation unwrap (`f(x)` -> `f.value()(x)`, declared-type keyed like
_gen_call's Optional[Callable] check, spelled via callee_cpp). The
plain-Callable inverse is pinned; the narrowed-invocation still-defers
fence converted. Tag residue parked with a one-liner:
`optional/opt_dict_key_iter_narrow` needs the container-inner widening
of `_optional_ptr_borrow`, a fence 35 consumers key on -- a
family-boundary call, not a wave cell.

### Long-tail wave 3: ctor:expr.call cleared + comp call iterables (2026-08-03)

+7 flips (dial 2994/3696): the whole `ctor:expr.call` tag (5 cases:
`auto_move/consuming_method_{dtor_suppress,inherited_del,both_del}`,
`inheritance/subclass_unbound_self_base_init_nocopy`,
`records/dataclass_subclass_explicit_super_init`), `stdlib/os_fs`, and
the bonus capture `pascal/swap_var`. Two cells:

- The inline-template Own-slot lvalue skip: a value-scalar NAME into an
  `Own[..]` slot of a native/template callee renders BARE (gen_call_arg's
  inline_template Own arm) instead of the copy+move temp; threaded via a
  new `inline_template` flag on `_lower_call_arg`; the move half
  (`_own_move_source_slice`) still outranks the skip (pinned: the movable
  record name moves, `test_thir_wave_ownlvalue.py`). One row cleared the
  whole tag -- all five cases rejected on the same `unsafe_init(p, v)`
  shape.
- Container-returning CALL iterables in comprehensions: the free-call and
  module-qualified method-call twins of the for-each fallback arms
  (`[n for n in os.listdir(tmp) if ..]` -> the `__obj_N` capture).
  Iterator-protocol combinator results keep rejecting (pinned,
  `test_thir_wave_compcall.py`); two stale "call iterable stays AST"
  fixtures updated (the return-position set-comp fence converted, the
  fallback landmark re-seated on a generator-call iterable).

list_comp tag residue parked with named reasons:
`none_safety/optional_comp_narrowing` (the storage-opt NARROWED-read
rung), `stdlib/itertools_basic` (iterator-combinator comp source),
`match/capture_rebind_comprehension` (branch-decl slot family),
`list/comp_array_error_return_fallback` (reseat.opt_storage_source
interaction). Suite comp-green (10764), move-verdicts 0/756.

### Long-tail wave 4: the walrus rungs (2026-08-03)

+2 flips (dial 2996/3696): `bool/truthy_walrus_binding`,
`tuple/tuple_walrus_own_local_use`. Three rungs in one cell
(`test_thir_wave_walrus2.py`):

- The ALWAYS_TRUE truthy walrus: the wrap composes over the inline
  assign; the owned-slot arm supplies the `(r = make(..), *r)`
  comma-deref tail.
- The enum first-decl walrus joins the scalar row (`Color c;` predecl +
  render_type spelling); the enum-fence pin converted.
- The OWNED-tuple walrus off an `Own[tuple[..]]` call: sema strips the
  element Own from the EXPR type, so the owned-ness reads off the
  callee's DECLARED return (the btuple alias-decl rule); the value
  lowers with tuple_source into the deferred `std::optional<std::tuple>`
  slot, and walrus-slot tuples read STORAGE elements (the borrow-ptr
  classifier's storage exclusion -- dot access). Two intermediate
  divergence candidates were caught by probe byte-diffs mid-cell (the
  copy+move temp, the arrow read) and fixed before any commit.

Parked: `tuple/tuple_own_walrus_plain_branch` (a sibling-branch borrow
binding -- the if.hoist_type mixed-binding family), plus the documented
`elif_walrus_temp_mixed` / `error_return/walrus_borrow_alias` parks.
THIR units green (4310).

### Long-tail wave 5: the aug-assign tag cleared (2026-08-03)

+4 flips (dial 3000/3696): `readonly/auto_readonly_usage_const`,
`imports/cross_module_dup_name_ptr`, `pointers/warn_borrow_field_path`,
`operators/aug_assign_subscript` -- the whole `body:stmt.aug_assign`
sole-blocker tag, one cell (`test_thir_wave_augassign2.py`):

- The scalar-aug target ladder grew the borrow-call field receiver and
  the raw-Ptr receiver field rows (both render identically on the two
  sides of the synthetic `target = (target OP value)`).
- `_list_inplace_extend` admits a clean list FIELD target
  (`::tpy::list_extend(this->items, ..)`).
- The user-record aug-setitem: the record-getitem operator[] read + the
  fixed `::tpy::__setitem__` dunder write, gated on the record carrying
  a `__setitem__` (setitem.record_aug).

THIR units green (4315).

### Long-tail wave 6: two isinstance rows (2026-08-03)

+2 flips (dial 3002/3696): `any/isinstance_global_any`,
`generics/isinstance_typeparam_bound` -- the `if:cond.call` tag's
grindable half (`test_thir_wave_isinstance2.py`):

- The STATIC type-param isinstance (`isinstance(x, Animal)` on a
  bounded-T subject): the `::tpy::isinstance_static<M, decltype(x)>()`
  trait spelled at the truthy arm; no extraction alias exists and
  subject reads render the bare name whatever sema narrowed, so branch
  facts are inert (cond.isinstance_static).
- The module-level `Any` GLOBAL subject: `_any_narrow_info` consults
  global_ns for a VARIABLE binding of exactly Any -- the value-typed
  global reads bare and the extraction alias is name-based, so the D15
  machinery renders it like a local's. The old global fence converted.

Parked: the Box deref-view init-statement family
(`protocols/{deref_view_rebind_invalidates,box_isinstance_deref_view}`
-- the C++17 if-init `dynamic_cast` over `(*b).__deref__()`), pinned
stays-AST. The wave-end harvest sweep found zero extra captures (the
direct per-cell harvests were complete). Suite comp-green (10778),
move-verdicts 0/756.

### Long-tail wave 7: the ref-target tag cleared (2026-08-03)

+7 flips, 6 user + 1 interop (dial 3008/3696; interop 29/34): `dict/dict_readonly_view_read`,
`tuple/tuple_optional_{in_list,const_iter,yield_method,yield_mutate}`,
`dict/dict_view_aliasing`, `interop/class_method_containers` -- the
`for_each:tuple.ref_target` tag was LOSSY (three sub-shapes) and its
grind opened five rows (`test_thir_wave_reftarget.py` + two converted
fences):

- container-VALUE dict views + the readonly-peeled ref-target family;
- the opt_ptr unpack bind mirrored at the FOR head (the stgopt track's
  named "for-head producer" rung -- `T* x = std::get<i>` off the lifted
  head, pointer-registered targets);
- FIELD and container-returning METHOD-call iterables at the unpack
  route (the single-var fallbacks' twins);
- `_iteration_yields_const` recurses view calls to their RECEIVER and
  reads an inferred-@readonly method's self off the method fi -- a live
  divergence the newly-captured `dict_view_aliasing` exposed mid-cell
  (const element pointers), closed byte-verified;
- `_storage_call_ret` admits value-tuple-element lists (the bytes-row
  precedent).

`stdlib/counter` advanced two rows (the unpack method-call head + the
value-tuple-list native arg) and PARKS at its remaining blocker: the
`Optional[Iterable[T]]-protocol` ctor literal-temp face
(`std::array<std::string, 3> __tmp_1 = {..}; Counter<..>(&(__tmp_1))`).
Two prediction-fences converted (the stgopt for-head boundary; the
resumable tuple-unpack leaf, which now routes whole). Suite comp-green,
THIR units 4321, move-verdicts 0/756.

### Long-tail wave 8: the expr.coerce tag cleared (2026-08-03)

+5 flips, 4 user + 1 interop (dial 3012/3696; interop 30/34):
`globals/global_ref_mutate_decl`, `any/hash_any`,
`list/list_append_str_rvalue`, `bytes/bytesview_from_param`, plus the
harvest-swept `interop/containers`. One site
(`_coerce_disposition`, the coercions.py-lambda KIND mirror) held the
whole tag; three disposition rows closed it
(`test_thir_wave_coerce2.py`):

- a scalar cast into an `Own[value-scalar]` slot is "template" -- the
  cast rvalue binds the slot natively (the Own[Ptr] precedent;
  `push_back((v).to_fixed_check<int32_t>())`, no copy+move temp);
- `strview_to_str` over an RVALUE source at an `Own[str]` slot is
  "materialize" -- gen_call_arg's non-simple-lvalue branch binds the
  owned conversion `T&&` bare (`push_back(std::string(str_slice(..)))`);
  the NAME source keeps its temp row;
- `bytes_to_bytesview` is "identity" (a bytes value IS the span), with
  one twist at the identity lowering: a bytes LITERAL source flips its
  form to BORROW so it renders the static `bytes_literal` span.

Two stale prediction-fences now route and were converted dualgen-first
(the bytes cross-type deferral in `test_thir_bytes.py`; the
widening-coerce FIELD at the scalar slot in
`test_thir_wave_needs_copy.py` -- its predicted "temp carries the cast"
never materializes, the cast renders inline). Suite comp-green, THIR
units 4326, move-verdicts 0/758.

### Long-tail wave 9: the subscript.slice_shape tag cleared (2026-08-03)

+4 flips, 4 user (dial 3016/3696; interop 30/34):
`dict/view_key_membership`, `argparse/explicit_sys_import`,
`argparse/no_argv_uses_sys_argv`,
`view_lifetime/warn_view_source_mutation`. One raise site
(expressions.py's slice arm), three receiver rows
(`test_thir_wave_slice2.py`):

- a str-LITERAL receiver is position-neutral (const char[N]) and lands
  bare in the `str_slice` template;
- a bytearray NAME receiver rides the same template
  (`::tpy::bytes_slice(ba, ...)`, span-view result);
- a pointer-slot module-var container receiver (`sys.argv[1:]`, the
  argparse builder expansion's read): the slice template is a PINNED
  consumer of the `(*slot)` read, threaded via `_module_var_slice_recv`
  + `allow_ref_pointer` at the slice arm -- the same admission class as
  the module-var print/method/native-slot consumers.

Boundaries pinned: bytearray FIELD receivers and the plain indexed
module-var read keep rejecting; the non-stepped module-var slice at a
list DECL slot rejects on the separate `decl.slot_type` gate (noted in
the routing pin). Harvest sweep: zero extra captures. THIR units 4334,
suite comp-green.

### Long-tail wave 10: the ptr_alias_borrow tag cleared (2026-08-03)

+9 flips, 9 user (dial 3025/3696; interop 30/34): the four sole-blocker
cases (`pointers/pointer_local_sharing`,
`records/rebind_slot_alias_survives`,
`none_safety/warn_loop_field_fact_stale_after_rebind`,
`none_safety/warn_field_narrowing_invalidated_by_root_write`) plus five
harvest captures (`auto_move/warn_auto_move_mixed_reassign`,
`auto_move/warn_auto_move_reassigned_from_param`,
`none_safety/warn_expr_narrowing_invalidated_by_call`,
`readonly/warn_readonly_call_with_unknown_arg_invalidates`,
`tuple/unpack_ref_alias_chain`).

The T&/T* "representation change with no form spelling" trap had an
escape the arms never took: the AST spells it as a plain address-of, so
the PTR_ADDR emit carries it with NO convert node
(`test_thir_wave_ptralias.py`):

- reassigned record-name alias DECL: `Point* x = &(a);`
  (decl.ptr_name_addr; pointer-local / global-slot / spelled sources
  stay out -- bare-pointer-copy renders);
- the reseat twins: `x = &(b);` over a record param's T& lvalue
  (reseat.param_name) and `result = &(pick(seed));` over a
  borrow-returning call (reseat.borrow_call, discriminated by
  call_returns_cpp_ref; value-returning calls keep the rebind-slot
  machinery -- pinned);
- the unproven value-opt scalar FIELD operand's checked unwrap
  (`deref_optional_check(local->value)`) -- the missing FIELD twin in
  `_lower_unproven_opt_scalar`, which also cleared two of the three
  `return:field.result_type` cases via harvest.

Two stale validator-trap fences converted dualgen-first (the record
param-reseat fence in test_thir_forms.py, the name-alias decl fence in
test_thir_ptr_locals.py); the OPT-pointee twin keeps its fence. THIR
units 4341, full comp sweep green, move-verdicts clean.

### Long-tail wave 11: the top_level method-call trio (2026-08-03)

+3 flips, 3 user (dial 3028/3696; interop 30/34):
`none_safety/loop_foreach_optional_body_narrowing`,
`none_safety/optional_container_none_writes`,
`calls/unsafe_cast_arg_type_context`. The top_level tag was LOSSY as
usual: all three rejected at the container-method ARG gate, on two rows
(`test_thir_wave_optelem.py`):

- a pending int LITERAL at a value-repr `Optional[fixed-int]` element
  slot renders the bare digits (`vals.push_back(3)` -- sema leaves the
  literal unresolved at the Optional slot; std::optional's converting
  ctor wraps), widening `_value_opt_scalar_elem_arg`;
- a ptr VALUE at an `Own[Ptr[T]]` element slot renders bare for name
  and call sources alike (`arg.own_ptr_value`; Own on a value type is
  a no-op spelling, the native stub's inline-template arg takes no
  copy temp). The PEELED arg must be ptr-typed itself: the argrow
  pin's exactly-once face count caught the addr-of-coerce over-capture
  (`ps.append(items[0])` must keep the `&(...)` lift row) pre-commit.

PARKED: `imports/tpy_module_copy` -- the builtin-module marker-call
kind (`t.copy(s)`; the TODO clause-4 blanket reject, opened only after
the AST arg-loop verification). Harvest sweep: zero extra captures.
THIR units 4346, suite comp-green.

### Long-tail wave 12: the setitem.family tag cleared (2026-08-03)

+5 flips, 5 user (dial 3033/3696; interop 30/34):
`none_safety/dict_value_narrowed_optional`,
`inference/assign_target_hint_field`,
`tuple/element_vs_singleton_collection`, `bytes/view_source_mutation`,
plus the harvest-swept `bytes/bytes_literal_readable`. Six rows, all
chain-walked out of one gate (`test_thir_wave_setitem2.py`):

- the owned-bytes value slot (S6 `bytes_copy` materialize in the value
  tail; owned literal render);
- the whole Optional[str/bytes] value store -- None as nullopt, the
  un-narrowed view-inner name via the ARG split with a consuming move
  wrap (`THIROptViewArg` grew `moved`); the NARROWED occurrence
  renders a deref-spelled condition and stays AST (occurrence-typed
  guard -- the dualgen boundary probe caught the divergence
  pre-commit);
- `len()` over an owned bytes/str container ELEMENT read;
- the open-K generic dict FIELD write (a `dict_key_ok` override on the
  family shell, setitem-gate only);
- the plain RECORD-element tuple value slot's `tuple_to_storage` CALL
  lift (literals pinned out);
- `_subscript_recv_tuple` reads a DICT receiver's VALUE slot
  (`d[1][0].n = 24` -- get_iterable_element_type would have yielded
  the key axis).

One stale fence converted (the bytes-element write in
test_thir_containers.py). THIR units 4352, suite comp-green,
move-verdicts clean.

### Long-tail wave 13: the overload arity tag cleared (2026-08-03)

+4 flips, 4 user (dial 3037/3696; interop 30/34):
`classmethod/overload_group`, `calls/callable_object_overloads`,
`calls/overload_arity_method`, `calls/overload_arity_literal_default`.
The short-stub LIVE-default rung (`test_thir_wave_overload3.py`):

- omitted impl params emit as prologue locals (`::tpy::BigInt factor =
  ::tpy::BigInt(1);` -- THIROverloadDefault beside THIRParamCopy),
  through the shared renderer: `default_to_cpp` grew an analyzer-keyed
  core both paths call. Value-scalar/Char locals only;
- the literal-eq fold is MIRRORED (`_overload_resolve_static` grew the
  `_resolve_literal_eq_statically` half over `_overload_literal_facts`
  -- the IntLiteralType->LiteralType promotion of
  `_inject_literal_overload_facts`), so `if count == 0:` collapses per
  stub exactly like the AST specializer;
- the conservative db_compare detail row is GONE: fact-carrying
  compares fold through the mirror, fact-less ones are plain runtime
  compares on both paths, literal-only groups reject upstream --
  verified by a whole-corpus byte-diff over the change;
- the unmirrored folds stay fenced as `db_literal_fold`: membership
  over a fact param (pinned) and any BOOL literal fact (the
  bool-default fence re-tags the old arity fence in
  test_thir_overload_stub).

Harvest sweep: zero extra captures (the db_compare family's other
members are multi-blocked). THIR units 4355, full comp suite green.

### Long-tail wave 14: the tuple-literal / union-literal cluster (2026-08-03)

+4 flips, 4 user (dial 3041/3696; interop 30/34):
`dict/nested_dict_print`, `set/set_nested`, `union/union_print`,
`tuple/tuple_of_list_literals` (chain-walked to CLEAN across three
cells), plus the four aug_assign queue entries found already flipped
(stale sole_cases). Rows (`test_thir_wave_tuplelit2.py`):

- `_decl_tuple_nested`: container / value-union ELEMENTS at the
  tuple-literal DECL sink, with `_cpp_decl_type`'s auto-vs-typed split
  mirrored (`auto` iff ref elements) and storage registration for
  pointer-repr-element tuples;
- the MIXED-union container-literal family: scalar/str literals bare,
  `None` as the monostate member, nested ARRAY literals via the AST's
  union_prefix render (`std::vector<T>{...}` -- also through
  Optional[list] elements), member-record CTOR rvalues at mixed unions
  (`_union_member_ctor_slot` dropped its all-record restriction),
  nested tuple literals with value-union elements;
- `t[0][0] = 9`: the tuple-element container WRITE receiver
  (`__setitem__(std::get<0>(t), 0, 9)`) via
  `_tuple_container_elem_read`, RECEIVER positions only (a value-sink
  read keeps the tuple-shape reject, pinned);
- the demoted-array ALIAS row: `_alias_ref_container` admits Array
  (`ys = xs` -> `std::array<...>& ys = xs;` -- a demoted list literal
  keeps list alias semantics); bytearray aliases keep rejecting.

Two stale fences converted dualgen-first (the live-source alias; the
earlier wide-decl reassign pin). Harvest sweep: zero extra captures.
THIR units 4371, suite comp-green.

### Long-tail wave 15: park sweep + the const-ref unpack element (2026-08-03)

+1 flip (dial 3042/3696; interop 30/34): `iterators/gen_tuple_yield_ref`
-- the generator/zip tuple-unpack arm's fresh-const-ref fence
over-rejected VALUE elements (`const ::tpy::BigInt& i = std::get<0>(...)`
binds identically off borrow and storage tuples); it now defers only
non-value const-ref targets (`test_thir_wave_geniter.py`; the read-only
record-target sibling pinned byte-identical).

Parks verified by probe this wave (registry updated):

- `async/asyncio_gather_settled_{exc_positions,all_fail,mixed,
  subtask_cancel}`: the STORED-EXCEPTION raise (`raise r.exception` at
  the resumable raise arm) -- the avoid-list asyncio library surface.
- `decl.slot_type` four (`async_readonly_tuple_param`,
  `tuple_reassign_borrow_tuple_ok`, `mixed_own_tuple_optional_element`,
  `mixed_own_tuple_local_realias`): confirmed the design-gated
  mixed-own/borrow-tuple fixpoint family (already registered).
- `generators/isinstance_union_frame`: the `_narrow_binding_supported`
  fence (already registered).

### Long-tail wave 16 (first cell): match-arm container hoists (2026-08-03)

+1 flip (dial 3043/3696; interop 30/34):
`match/branch_decl_pending_list_leak`. The match hoist admission
extends its non-value slice from F1-records to scalar-read CONTAINERS,
reusing both if-cascade flavors (`test_thir_wave_matchhoist.py`):

- single-bind OPTIONAL_STORAGE (`std::optional<std::vector<T>> xs;`,
  plain arm assigns, `->`/deref reads);
- the rvalue-reassigned pointer + rebind-slot form (new plumbing:
  THIRMatch grew `hoist_slots` beside `hoist_decls`, the THIRIf arm
  mirrored at the match emit -- `std::vector<T>* xs;` + the match-head
  `std::optional<T> __slot_N;` with the shared slot counter), threaded
  through the record tier only (other tiers fence the flavor, pinned
  on a scalar-subject match).

Two stale fences converted dualgen-first (test_thir_match.py's leaked
container arm decl; test_thir_wave_match_capture.py's reassigned
container branch decl). Remaining match cells for the next iteration:
nested match statements (nested_capture_distinct_name), the CALL
subject with as-captures (match_ptr_variant_call), the union-field
optional narrow-in-body (capture_optional_narrow_in_body at
_lower_match_union); pascal/variant_record no longer exists (stale
sole_cases). Harvest sweep: zero. THIR units 4378, suite comp-green.

### Long-tail wave 16 (second cell) + session close (2026-08-03)

+1 flip (dial 3044/3696; interop 30/34):
`match/nested_capture_distinct_name` -- the VALUE hoist flavor is
position-neutral (the AST renders `T t;` at the match site wherever
the match sits), so the in-branch/in-loop reject moved below the
value admission and a nested match's leaked capture hoists inside the
outer arm (pinned in `test_thir_wave_matchhoist.py`).

PARTIAL, committed fail-closed: toward the CALL subject with
as-captures (`union/match_ptr_variant_call`) -- the union switch
admits Call/MethodCall rvalue subjects (by-value dispatch local, bind
off the shared lvalue fact), the subject lowers at the STORAGE sink,
and the record-method result gate admits ptr-variant union returns
(`method.ptr_union_ret`). One blocker remains in the method-call gate.

Session summary (the /loop grind, 2026-08-02..03): dial 2976 -> 3044
(+68 cases), interop 29 -> 30/34, sixteen waves, six applied review
rounds, all on `thir-longtail`. Remaining grindable queue:
`union/match_ptr_variant_call`'s last rung,
`match/capture_optional_narrow_in_body` (the union-field
narrow-in-body at `_lower_match_union`), `res.match_strategy` (5),
`resumable:expr.method_call` (5), and the 3s/2s/1s tail; parks are in
TODO.md's registry.

### Review round 7 (the wave-16 increment) -- correction (2026-08-03)

The "PARTIAL, committed fail-closed" call-subject set from the session
close is REVERTED: architecture review found the subject-use ternary
had landed in the POLY tier (unreachable by the union admission) and
the `method.ptr_union_ret` row was storage-sink-wide with zero
witnesses (the coverage Critical). The cell restarts clean next
session against `_lower_match_union` with a scoped result flag
(registered in TODO's parked registry). Also applied: the ptr_slot
hoist fence moved from the consumer into `_route_hoists`
(`ptr_slot_ok`, record tiers only -- the routing/consuming split
restored); two new pins (the in-loop VALUE hoist routes identical;
a nested match's CONTAINER capture keeps the in-branch reject); the
garbled face-registry comments repaired. Dial unchanged (3044/3696);
THIR units 4381, full comp suite green, move-verdicts 0/761.

### Design round 14, Tier 1: four call-lane gate widenings (2026-08-03)

The round-14 measurement (fresh probe_corpus off 2c6948bae3) found the
call-lane sole-blockers collapsed (body:expr.call 76 -> 4,
body:expr.method_call 84 -> 6; units unchanged, the weight is
multi-blocked now) and priced the residue by forced-open byte-diff.
Tier 1 = the four cells whose render arms already existed -- gate
admissions only. +5 flips, 5 user (dial 3049/3696):
`auto_move/auto_move_own_optional_{ctor,method}`, `stdlib/os_walk`,
`inheritance/generic_base_inherited_ctor`,
`records/optional_field_ctor_param_optional`. Rows
(`test_thir_wave_t1gates.py`):

- `_opt_own_record_name_arg` added to the CTOR chain and the
  record-METHOD ladder (one root, two sites): a record NAME moved into
  an `Optional[Own[T]]` slot renders bare `std::move(p)`; the shared
  `_lower_call_arg` arm already enforced the move verdict, so the copy
  shape keeps falling back (pinned).
- `_none_value_opt_arg` added to `_marker_call_arg_ok`: the os_walk
  "kwargs" tag was LOSSY -- sema had already normalized the kwargs; the
  real reject was the sema-FILLED `onerror=None` default at its
  `Optional[Callable]` slot (`std::nullopt`). Boundary: a None at a
  ptr-repr Optional[record] marker slot keeps the `optptr.none` face.
- `_ctor_instantiation_ok` grew the inherited-init arity fallback
  `_ctor_shape_ok` already had (param-less synthetic fi + registry
  triples): `TypedM[Int32](7)` -- the instantiation spelling over an
  inherited param-ful `__init__` (`ctor.inherited_instantiation`).
- New `call_pass` face in `_optional_ptr_arg_face`: a BORROW-returning
  call already typed as the slot's ptr-repr Optional passes bare
  (`Edge(find(pts, 3))` -- the AST's OptionalType-arg arm returns
  `arg_gen` unwrapped), lowered under `ptr_opt_passthrough`; an
  Own-declared optional return is storage-form and stays out (pinned,
  the optional_to_ptr lift is unmirrored).

### Design round 14, Tier 2: three call-lane render arms (2026-08-03)

The forced-open DIVERGENT cells: each needed one render arm beside its
admission. +3 flips, 3 user (dial 3052/3696): `stdlib/counter`,
`tplib/requests_session`, `view_lifetime/view_elem_into_owned_container`.
Rows (`test_thir_wave_t2renders.py`):

- `_protocol_union_literal_temp_arg` (the 'addr' face's literal
  sibling; the nullable-protocol slot classification extracted into the
  shared `_nullable_protocol_slot`): a container literal at an
  `Iterable[T] | None` ctor slot hoists a temp typed as the literal's
  own Array demotion and lifts its address
  (`std::array<std::string, 3> __tmp_1 = {..};` + `&(__tmp_1)` --
  `_gen_protocol_arg`'s temporary tail). Ctor positions only, like
  'addr'. Closes the parked `stdlib/counter` registry entry.
- `_union_bytes_literal_temp_arg`: a member-typed bytes LITERAL at a
  pointer-variant union slot OUTSIDE the F4 U2 record/scalar slice
  (`bytes | dict | None` -- widening `_eligible_ptr_union` would open
  every narrowing/extraction consumer, so the one rvalue shape carries
  its own slot check): the owned-bytes temp + `pv{&__tmp_N}` lift
  through the existing THIRUnionArgLift machinery. A bytes NAME at the
  same slot stays out (pinned).
- `_bytes_owned_slot_arg` (the S6 twin of `_str_owned_slot_arg`; the
  deferred "view-form NAMES pending a witness" note cashed in): a
  bytes param name, a narrowed `bytes | None` deref, and a
  `bytesview_to_bytes`-coerced slice at an `Own[bytes]` element slot
  materialize `::tpy::bytes_copy(x)` via the shared S6 FormConvert
  (the coerce peels -- it IS the copy). The forced-open probe DROPPED
  the copy (the dangling-view aliasing class), confirming the old gate
  was fail-safe. An owned bytes LOCAL keeps the AST cascade (pinned).
- Rider: the chained bytes element read the chokepoint unmasked
  (`app[0][0]` -> `bytes_getitem(::tpy::__getitem__(app, 0), 0)`):
  `bytes_recv_ok` grew the one-level nested-subscript face mirroring
  the container-in-container nested_ok row.

### Design round 14, Tier 3: the three chained cells (2026-08-03)

The approved 10 -> 9 -> 8 order. +3 flips, 3 user (dial 3055/3696):
`generics/isinstance_typeparam_tuple`, `generics/generic_func_multi`,
`generics/generic_static_method`. Rows (`test_thir_wave_t3cells.py`):

- (10) `_lower_static_isinstance` extracted from the truthy-condition
  arm and re-consumed at a VALUE position (`return isinstance(x, (Dog,
  Cat))` on a bounded-T subject): the same spelled
  `isinstance_static<M, decltype(x)>()` disjunction, no extraction
  alias, position-independent. Non-tparam isinstance keeps rejecting in
  value position (pinned).
- (9) the time-boxed top_level TempSink look came back SMALL: the
  module-init body is an ordinary AST flush position, so the
  NominalType global-slot init now threads `allow_temps=True` and the
  generic ref-slot literal temps land as plain locals ahead of the
  `static __global_slot_N` init (`int32_t __tmp_1 = 10; ... static
  Pair<...> __global_slot_1 = create_pair<...>(__tmp_1, __tmp_2);`).
  One-line use change; emit-side CtxTempSink already flushed per leaf.
- (8) the 3-row chain, landed in reject order: the marker gate grew
  the record-method ladder's `_storage_optional_return_type` escape
  (STORAGE-gated -- `method.qualcall.storage_opt_ret`);
  `_value_opt_member_arg`'s Own exclusion narrowed to NON-value
  payloads (Own on a value scalar is a no-op spelling, so `99` lands
  bare at the `Own[T] | None` slot; an `Own[record] | None` slot keeps
  the cascade, pinned); `_print_optval_opt`'s NAME arm grew the
  storage-form `Optional[Own[record]]` row (`print_optional_val(c3)`
  member-blind over the bare name; keyed on the RESOLVED type so a
  narrowed read self-excludes). As predicted by the round-14
  measurement, `print.arg.optional_name` co-unlocked nothing beyond
  the case itself.

### Review round (design round 14) + readiness retrospective (2026-08-03)

Five specialists; cpython-parity + architecture-fit clean. Applied:
a VACUOUS boundary pin (asserted a face name that does not exist in the
registry, so it always passed) rewritten against the marker-ladder
shape it actually guards; four missing boundary pins added (chained
bytes slice-index / two-level receivers, the storage-opt escape at a
non-storage sink, the narrowed Optional[Own[record]] print read, the
top-level subclass-rvalue shape guard); phase markers stripped from pin
docstrings; inferable Int32() spellings simplified; a second stale
TODO bullet fixed. LESSON, generalized by the readiness second opinion:
a face-count assert in a test proves NOTHING on its own -- the registry
assert fires only on WITNESS, so a nonexistent name in `.get()` reads
as zero forever, and a real face can be witnessed by a body that later
falls back. Every face assert needs a routing assert beside it
(`_assert_routes_byte_identical` / routed-set `_fn` checks); the pin
discipline already says this for ROUTING pins -- it applies equally to
boundary pins' zero-count asserts, whose honest form is face==0 PLUS
the fallback/identity claim. Three fallback-safe gate/render
asymmetries carried to TODO's parked registry (opt-own move-verdict
placement, inherited-arity kwargs mix, bytes-literal union arm under
readonly_target).

### Design round 15: the overload literal/mangled path (2026-08-03)

The round-14 queue ruling's next design item, built as four approved
cells on branch `thir-design-r15` after a fresh probe_corpus (641
marked / 330 sole / 1 clean -- `pointers/optional_unsafe_access`, a
free flip committed first). +10 flips total, dial 3055 -> 3065/3696.
The family measured 8 sole cases with exactly {db_compare,
call.literal_overload} plus `overload_literal_flatten` one tag away --
all nine flipped, plus the free one.

- Cell A+B (admission + emit seam + free-call spelling): literal-only
  groups lower per stub against the IMPL signature (stub return +
  injected literal facts; `_literal_stub_facts` zip),
  `_gen_literal_specialized_function`/`_method` set `thir_overload_key`,
  and `_free_callee_kind` threads `literal_mangled_name` through the
  plain (bare escaped) and imported (qualified) spellings on
  `callee_cpp`. THIRFoldedBlock grew `trivia_loc`: the AST emits the
  chain head's preceding `#` comments before the fold dispatch even
  for an all-dead chain -- a LATENT gap in the pre-existing fold arm,
  first witnessed by `literal_multivalue_fold`'s and-contradiction
  comments (the UPDATE-5 lesson again: comment divergences are
  invisible to every pin whose `comments` echo is off).
- Cell C (fold extensions): `_overload_resolve_static` gained bool
  truthiness, membership, and the or-coverage/and-contradiction chain
  fallthrough by IMPORTING the AST's own `_check_literal_in` /
  `_check_literal_chain` -- one source of truth, no verdict drift.
  Every db_literal_fold fence dropped (admission walks, short-stub
  BOOL fence, `_overload_reject_detail`'s membership row).
- Cell D (partial folds): THIRFoldedIfChain renders the AST live path
  (clean `if / else if`, NO condition source comments, else from the
  last original node). The binop compare fence refined from name-based
  to VERDICT-based: undecidable compares over lc.literal_facts lower
  plain (the live branches); decided verdicts (bare "true"/"false"
  renders) and untracked flow-sensitive facts keep rejecting. Declined
  shapes, pinned: branch decls, concrete extractions, temps past the
  first branch, True-after-dynamic (an AST DEFECT -- the fold drops
  the dynamic prefix; filed in BUGS.md, reproduced by the judge
  fixture).
- Cell E (method half): `method_literal_mangled_cpp` mirrors the AST
  member resolution (@native rename > mangled > escaped; mangled
  spelled RAW, unlike the free call's escaped form);
  `_plain_method_fi_ok` gained `literal_mangled_ok` opened only by the
  record arm, builtin families re-fenced. Rider: the union-return
  view-insert fence over-rejected OWNED sources -- an owned-str FIELD
  read (`return self.data_name` into `Int32 | str`) renders bare and
  now rides the return arm's field_prechecked rails; view sources
  pinned fenced.

Two AST defects filed in BUGS.md while re-pinning: bodied literal
stubs emit duplicate unmangled definitions against a mangled call
site (the C++ never compiles), and the fold's True-after-dynamic
branch drop. Measurement corrections: the family paid 9 (not ~6);
`flatten`'s "second tag" was NOT the method call alone -- behind it
sat the union-return owned-str fence, a one-row rider rather than a
family. Boundary pins added: literal-stub arity, decided-compare
fence, literal-mode incompatible return (the AST's dead-code suppress
stays unmirrored), union-return view source, cross-module mangled
spelling, bare literal at a Literal slot.

### With-family waves: stmt.with + res.leaf_with drained (2026-08-03)

Fresh census after the with-target storage rework (7b6e556cd2) put
`body:stmt.with` on top (10 sole cases, all its new fail-closed
markers) with `resumable:res.leaf_with` second (6). Both drained in
two cells on `thir-r16-resumable`; +19 flips (the 16 with-family
sole/multi cases incl. `with_statement`, plus `coro_with_as_ref`,
`with_target_hoisted_aliases`, `with_target_reuse_read_after`).

Cell 1 (sync): the kept-owned-manager hoist
(`_with_manager_needs_hoist` mirror -- `std::optional<CM> __slot_N`
via hoist_lines, slot drawn AFTER the manager expr renders);
`_with_target_arm` drops its rebind-slot conjunct (the AST's
already-declared arm binds `name = &(...)` regardless -- the with
aliases the manager, never the slot); the in-branch fence narrows to
PTR_DECL; with-owned hoists gain the pointer flavor (`T* name;`,
borrow-only names -- the hoisted with-as target); borrowed
FIELD-ACCESS managers admit with a ctx_manager row in the field
result ladder. Four stays-AST pins converted (their stated reasons
were exactly the removed rejects); boundary pins: delegating manager
(a GLOBAL-returning `__enter__` binds plain -- note a FIELD read IS
the manager's own storage and hoists, which refuted the first pin
fixture), top-level + rvalue-reassigned rejects, multi-item hoist.

Cell 2 (resumable leaf): the 5202 blanket reject replaced by real
classification in `_lower_with` -- FRAME_SLOT emplace / FRAME_FIELD
assign target arms (frame-resident check FIRST, mirroring
`_gen_with`'s dispatch order), the `__with_ctx_<K>` owned-manager
frame home read from `resumable_state(func).with_owned_ctx_map` (the
same source the AST ctx copies -- no drift, no emit-time hook).
FRAME_FIELD admits only bare-read value families
(scalar/Char/enum/str/bytes): the Optional-enter target is a plain
field for WRITES but its narrowed reads deref -- dualgen caught the
bare-read divergence before any pin would have. A return nested in a
leaf with rejects (`with.leaf_return`): the ctx return hook walks the
AST finally_stack, which the THIR with emit does not populate. One
collateral pin re-fixtured (the leaf-match boundary's example shape
now routes; swapped to the still-rejected Optional-enter form).

Lesson repeated: both probe-caught defects (optional-enter bare read,
the "delegating" fixture that actually owned its storage) were
boundary-shape errors invisible to the routed-case corpus -- the
dualgen-then-pin discipline caught each within the wave.

`with.opt_slot_target` is now zero-witness corpus-wide (the pointer
flavor took its last witness); TODO's ## Next entry on deleting or
re-witnessing the ASSIGN_OPT arm stands.

### Match-dispatch hook wave: res.match_strategy drained (2026-08-03)

The resumable MatchDispatch hook mode admitted three more tiers,
draining the queue's res.match_strategy item (5 sole cases flipped:
gen_match_{optional,union,nonlvalue_value_bind},
capture_rebind_in_generator, nested_capture_reuse_generator; commit
a49484259e + the flip commit).

- if_elif_record joined the hook-admitted kinds (arm bodies as
  body_key hooks; the record chain emit routed through the hook-aware
  _emit_match_arm_body).
- The load-bearing piece is capture re-keying (_hook_mode_binding):
  at the dispatch terminator the BB walk's `declared` has not seen
  non-hoisted generator locals, so captures compute block-local
  copy/ref modes -- the frame facts decide the real render instead
  (plain frame field -> assign, frame_slot -> the new frame_emplace
  mode `c.emplace(...)`), everything else rejects. The dispatch site
  then registers hook-admitted capture names into `declared` from
  frame_local_types (the AST's var_types registration) so arm-body
  reads lower -- the missing half that surfaced as name.global_read
  after the binding admission.
- optional_partition admits the VALUE-repr O2 dispatch only (None arm
  hooked, inner arms via the shared scalar walk -- which now uses the
  same re-keying); pointer-repr O1 keeps rejecting.
- The or-bind arm stays rejected: it re-walks one body per
  alternative, and re-walking an arm's BB chain would re-split its
  resume cases.

Three stays-AST pins converted to routing pins (their fixtures were
exactly the admitted shapes); TestMatchDispatchTierBoundaries pins the
record/O2 routings + or-bind/guarded-record/pointer-repr rejects (all
dualgen-probed first).

Scouted for the next cell (recorded in the task list): the
expr.method_call trio (frame_local_last_use_move x2 +
coro_for_tuple_unpack_ref) rejects at the arg gate on a
TpyCoerce(bytearray->bytes) wrapping a movable local at an Own[bytes]
slot -- the AST peels the render-identical coerce and applies the
last-use move (`std::move((*buf))`); mirroring touches the
move-verdict join. The two reactor_* cases at the method SHAPE gate
are the PARKED socket/native-method family -- skipped per the park
registry.

### Coerced-move arg wave: the bytearray/bytes identity pair (2026-08-03)

The expr.method_call frontier's mechanical residue (commit 8d7189b57f
+ 3 flips: the frame_local_last_use_move pair and stdlib/base64 as
coerce collateral). `_coerce_disposition` learned the
bytearray<->bytes coercions (identity in every position -- both spell
vector<uint8_t>, no codegen lambda); `_own_move_source_slice` peels
the coerce for its name checks with a MOVE-ONLY bytes slot verdict
(`_own_bytes_identity_move_slot` -- the non-move halves must not see
bytes: the AST passes a non-moved bytes lvalue BARE to an
inline_template callee, so widening the shared `_own_lvalue_temp_slot`
would have made the copy row diverge); the method-arg gate admits the
move slice up front, mirroring _maybe_move's move-first ordering, and
the render's coerce-over-frame-slot fence yields to the move check.
One prior reject pin converted (bytearray at a plain bytes param now
rides the identity passthrough, dualgen'd IDENTICAL); boundary pins
for the sync/resumable non-last-use fallbacks.

The two reactor_* cases sharing the tag are the PARKED socket/native
family (method SHAPE gate) -- untouched per the park registry.
coro_for_tuple_unpack_ref advanced to its real blocker: the
tuple-literal arg row (`pairs.append((Item(1), Item(2)))`), a separate
future cell.

### Plain pointer-local record return + base-init None spelling (2026-08-03)

Two small cells off the fresh census (commits f9d193c335 + 1400e3451c
+ the review round), +10 flips total (dial 3095 -> 3102 after the
suite): the census also handed 2 FREE match-dispatch collateral flips
(async_match_binding, match_capture_across_yield -- zero code change).

- `return best;` where `best` is a plain F1 `T*` alias local at a
  record return slot takes the same is_indirect_name arm as its
  ptr-Optional sibling (`ret.record_ptr_local`): deref + the move at a
  movable last use via the shared `_is_move_source` audit -- a borrow
  return's alias local never promotes (renders bare `(*best)`), an Own
  return's reassigned rebind-slot local moves
  (`return std::move((*best));`, review-round pinned). +7 flips:
  returns/return_{param,global}_ref, interop/
  warn_export_class_return_self, the with-family riders
  with_target_{delegating_manager,multi_item_mixed} (both had been
  blocked on the bare-global `return SHARED;` inside `__enter__`).
  One forms pin converted (its stated reason was the removed reject).
- A base-init `None` arg carries its SLOT's spelling on
  `THIRLiteral.none_cpp` (`{}` variant / `std::nullopt` value-optional
  / bare `nullptr` pointer-repr -- all three pinned after the review
  round), rendered by the same `default_to_cpp_from_analyzer` the AST
  arm calls. Flips defaults/baseinit_union_none (the deliberate
  reject-witness case); the os_error pair stays on the parked
  cross-module-record family.

Also scouted and registered (TODO.md, missing machinery): the
comprehension-var SHADOW priority mirror (`comp_local_names`) -- the
remaining list_comp singles are one shadow cell + three unrelated rows.

### copy() reseat + Own-arg rows (2026-08-03)

The call.builtin_special decl/discard residue, two cells (+5 flips,
commits e2f4601311 + 03f145efba):

- `copy(x)` of a plain F1-record name as a RESEAT rvalue rides the
  rebind machinery: the OPTIONAL_STORAGE engaging assign, the
  BRANCH_RVALUE lazy slot, the F2d rebind-slot reseat
  (`saved = &*(__slot_N = Point(p));` -- the corpus escape cases hit
  THIS arm, not BRANCH_RVALUE; the review round pinned the genuinely
  branch-hoisted and single-bind-optional flavors separately), and the
  OPT_PTR_SLOT exact-type reseat all intercept `_lower_copy_record`
  before the generic call tail (the special-builtin gate rejects
  copy() there); the opt-slot arm keys the copy on the slot's own
  record -- a SUBTYPE copy keeps rejecting (dualgen'd). Flips
  pointers/escape_{explicit_storage,local_safe}.
- `copy(name)` into a same-nominal Own slot at a FREE call
  (`consume(Box(b))`): the inline free-call arg gate gains the
  `_copy_record_own_arg` row -- the `_lower_call_arg` render intercept
  already existed, only the gate row was missing. A pointer-local
  source keeps rejecting at the intercept's live-set re-run (boundary
  pinned). Flips the auto_move discard trio.

Named residue at the same tag: the ptr-variant union copy (a two-line
`to_value_variant` slot + `to_ptr_variant` alias fan-out --
union/union_copy_ptr_variant), a one-source-to-two-statements shape
adjacent to the registry's del-fan-out machinery note.

### TypedDict membership fold + the union CALL subject redo (2026-08-03)

Two cells (+2 flips, commits 5691673548 + cf9a37dcc6):

- The total=True TypedDict membership fold renders the operand-effect
  comma form (`(static_cast<void>(r), true)` / `false` for `not in`),
  closing the typed_dict_in arm's last half; the old boundary pin
  converted. Flips typed_dict/typed_dict_get_in. Named residue at the
  in.record tag: the no-__contains__ iterator-probe IIFE
  (list/arraylist_membership) and the field-of-call __contains__
  receiver (tplib/requests_cookies_expiry).
- The REGISTERED redo of the union-switch CALL subject landed clean:
  a Call/MethodCall rvalue returning a non-wrapper ptr-variant union
  materializes into the by-value dispatch local (subject_ref=False,
  is_ptr_variant folds the `*std::get` deref); the method's union
  return admits through the SCOPED match_union_subject _ExprUse flag
  (nothing storage-wide -- the revert's complaint), decl consumers
  keep rejecting (dualgen'd + pinned). Flips
  union/match_ptr_variant_call; the registry entry is marked DONE.

Scouted alongside: the setitem.recv.field_parent trio is the
module-global container receiver family (os.environ -- the
`(*qualified_global)` deref across setitem/reads/membership), a
coherent future cell.

### Tuple-return call sources + the span-coerce method arg (2026-08-03)

Two small cells closing the iteration (+2 flips, commits 99114b2da5 +
7190806206): the value-tuple return source gate admits TpyMethodCall
(module-qualified stub calls -- `return math.frexp(2.0)`; the generic
tail's call gates own the result rows; the user-method flavor pinned
as a bonus witness), and the method-arg ladder gains the free-call
ladder's `_span_coerce_arg` row (an Array local at a by-value Span
method slot renders the as_mut_span wrap). A PRE-EXISTING
whole-compile crash was probe-found and filed in BUGS.md: a ctor-arg
call at the tuple-unpack source builds a THIRArgTemp under a
non-flushable position and the validator raises OUT of the fallback
boundary. Named residue: the two Own[tuple]-return name sources (the
parked F3 family), the record-rvalue-on-call-receiver and
datetime-optional method-arg singles.

### Designed-queue items 1+2+5+6: binding-fact machine-check, finally-deferred return, del multi-target, comp filter temps (2026-08-04)

Three approved designed-queue items landed in one autonomous grind
iteration.

**Binding-fact machine-check (queue item 1).** `tpyc/binding_audit.py`
joins per-function UNIONS of the four mirrored binding sets (pointers /
ptr_variant / optional / storage_tuple) across both passes, move_audit
discipline throughout (id(func) keys with strong refs, the fallback.py
journal seams, routed-bodies-only, a `joined` denominator on the summary
line beside the move-verdict tally). Captures fire at the scope-restore
seams (`restore_local_scope`, `branch_scope`) where branch-scoped adds
are still live; the AST window opens in `setup_body_scope` and closes at
the next `reset_scope` / module end. The subset assertion (AST union
must be within THIR's) FAILS the case in both the corpus and interop
runners. The first corpus run paid for itself: (1) a real
misattribution -- `_resumable_frame_ctx` restores the PREVIOUS
emission's `pointer_locals`/`movable_locals` at exit, so lazily-closed
windows credited the residue (`exc_val` from a Tracer.__exit__) to every
async fn emitted after it; the window now closes before that restore,
regression-pinned. (2) Two emit-internal temp families
(`__await_lift_*`, `__for_tup_*`) the AST classifies for its own
rendering -- excluded by prefix, documented in the module. (3) Four
documented-partial producer families now machine-acknowledged AT their
arms via `acknowledge_binding_partial` (the ACK channel in the same
record): the Own[Opt[T_ref]] and owned-tuple param seeds, the owned-
tuple walrus target (THIR keys those reads on walrus_slot_locals), and
the mixed-own call decl -- the last is a REAL design fork (mirroring
storage membership alone measurably flips `std::get<1>(x)->val` to
`.val`; parked in TODO.md with the divergence evidence). One REAL
missed producer was seeded: the fully-owned tuple call decl
(`t = make()` at `tuple[Own[T], V]`) now registers
storage_tuple_locals + promote_movable at the decl-arm entry, the AST's
pre-shape twin. Pins: `test_binding_audit.py` (join arithmetic, missing
-name report, subset direction, fallback rollback, window isolation).

**Finally-deferred return (queue item 2).** `THIRFinallyDeferredReturn`
mirrors `_deferred_return_recipe`'s two-recipe table (A: `&(lvalue)` /
`std::move(*p)`; B: bare pointer local / `ptr_to_optional_move`) with
the capture-before-chain interleave in emit (ptr name drawn from the
shared iter counter BEFORE the chain buffer, `[[maybe_unused]]` under a
terminating finally). Stamped-but-uncovered shapes REJECT; the
emit-time retraction is never mirrored; `res.finally_return` stays
rejected (zero witnesses). Dualgen'd across nested frames, with-frame
crossings, @error_return, value-type eagerness -- all committed as pins
(`test_thir_wave_finally_deferred.py`).
`finally_mutates_returned_local_indirect` + `finally_return_nocopy_move`
flip; `finally_mutates_returned_local` cleared its 7 return sites but
stays on main()'s `call.ret_type.record_borrow` design stop.

**del multi-target (queue item 5).** The del_item arm loops targets
(per-target admission, one rejecting target folds the statement);
`THIRDelItem` emits one `::tpy::__delitem__` line per target;
single-target keeps the THIRExprStmt render byte-for-byte. The old
"one comment across N lines" reject-pin converted to a routing pin.
`dict/dict_del` clean; mixed receiver kinds + user-record __delitem__ +
three-target pinned (`test_thir_wave_del_multi.py`).

**Comp filter temps (queue item 6).** `temps_ok` on the comprehension
condition lowering (the existing while/sgen cond-temp parameter, one
call-site away) + a checkpointed `flush_since` at loop-body indent
before the `if` -- checkpointing matters: a naive full flush swept an
OUTER walrus predecl into the loop scope (caught by the corpus
byte-diff on `list/comp_nested_list_cond`, fixed same wave).
`set/set_comp_owned_move` flips; the owned-ELEMENT position stays
fenced (pinned); the old comp-filter fence in the generic-own-args
pins converted to a routing pin.

Lesson worth keeping: the check found five distinct producer families in
its FIRST corpus run -- every one either a misattribution in the new
recorder, an emit-internal name, or a documented partial nobody had a
detector for. The acknowledgment channel turns those comments into
machine state deleted with the mirror, which is exactly the shape the
original entry asked for ("catch a missed producer on first witness").

### Designed-queue item 4: the borrow-tuple const fixpoint (2026-08-04)

`ensure_borrow_tuple_const` -- the lazy `_compute_borrow_tuple_const`
mirror on `_LowerCtx`: OR const over every binding source of a
reassigned/hoisted ptr-repr-tuple local, to a fixpoint over name chains,
BOTH sets (plain + nullable) in one pass, over THIR's own verdicts
(`_param_is_const`, `const_locals`, the const tuple sets). Consumers:
the existing `decl.btuple_*` arm spells `to_cpp_return_const()` and
threads `is_const` into the lift; the btuple reseat arm's
`_borrow_tuple_source_ok` gate drops its const-source rejections (the
deferred rung the fixpoint unlocks) and the reseat FormConvert targets
the decl verdict; the walrus btuple arm REJECTS a fixpoint-const name
(no witnessed const render there). The MIXED own-borrow call decl gets
its direct-bind row (`decl.btuple_reassigned` -- the render already IS
the local's shape; materializing would copy the borrowed half). Both
const sets joined into the binding-fact subset check.

Cases: `tuple_reassign_borrow_tuple_ok` + `mixed_own_tuple_local_realias`
clean. Two fence pins converted (annotated-const decl, mixed-call init);
name-source and ternary INITS keep rejecting (pinned). Dualgen probing
surfaced a PRE-EXISTING AST miscompile -- one const source + a later
element write emits a write through `const Box*` (g++ rejects valid
Python); filed in BUGS.md, THIR mirrors the oracle byte-identically.

Process lesson (cheap to keep): the first cut of this cell REBUILT a
decl row that already existed (`decl.btuple_lift` et al.) because the
gate widening made the existing arm fire divergently -- grep for the
construct's existing faces BEFORE writing a new arm; the raise-site
histogram names the site, not whether an arm already half-covers it.

### Genrec track cell A: the match union-switch tiers (2026-08-04)

The `_wrapper_union_like` accessor (thir predicates): the shared duck view
of a wrapper-union-like subject -- a recursive-alias WRAPPER union or a
generic instance (`RecursiveAliasInstanceType`), both carrying the same
duck API. Cell A rekeys the match KIND classifier through it; the whole
downstream match flow was already duck-keyed (`needs_wrapper()`,
`wrapper_info()` via `_union_index_members`, `.value` via
`_narrow_variant_cpp`), so the cell is essentially two lines -- the
`stmt.match` axis drained across ALL 21 genrec cases byte-identically,
exactly the design's 19/22 shared-axis prediction. Boundaries pinned:
the wrapper slice stays NAME-only + unguarded-only, and a plain union
keeps the bare-variant render (`test_thir_wave_genrec_match.py`).

The boundary probe caught a PRE-EXISTING unwitnessed divergence (confirmed
by stash-bisect): a member-valued scalar arg at a GENREC slot passed bare
where the AST hoists `Tree<T> __tmp_N = v;` -- the scalar arg disjunct's
`_member_valued_union_slot` guard keyed `isinstance(pt, UnionType)` and a
genrec slot escaped it. CONTAINED by rekeying that guard through the
accessor (the shape now rejects -> byte-identity restored); the proper
temp row is cell B's work, where the member-name/literal typed-temp rows
rekey the same way. Residue after cell A matches the designed cell map:
B (call/ctor args: generic_arg_shape x6 + the arg tags + the qualcall
pair), C (return slots x~11), D (ctor MIL fields x6), E (tuple-unpack
x3, opt_slot_pointee, the optional-cond single).

### Genrec track cell B: call/ctor arg rows + decl converting-ctor rows (2026-08-04)

`_ru_wrapper_arg_slot` rekeys on the accessor (the generic instance
Ref-peels -- unlike the non-generic wrapper it is not a value type, so
its param slot arrives Ref-wrapped; the UnionType path's unwrap stays
byte-frozen), members read via the new `_wrapper_like_members`
(`wrapper_info().full_members`, both classes). The already-union
exclusions in the literal/member-name/member-rvalue rows gain the
RecursiveAliasInstanceType class; the generic-call arg tail gains the
four wrapper rows against the SUBSTITUTED slot (`leaf_count(t)` at
`Tree[T]` resolved `Tree[int]` binds the name bare into the
`leaf_count<::tpy::BigInt>` instantiation). Decl side:
`_wrapper_member_ctor_slot` rekeys on the accessor and gains the LITERAL
sibling (`leaf: Tree[int] = 9` -> the plain spelled copy). Two latent
gaps found and fixed on the way: the ru-literal decl rows never
registered `declared[name]` (every later use of the declared name
rejected -- the reason the family looked call-blocked), and cell A's
containment (the `_member_valued_union_slot` guard) is now re-admitted
properly through the temp rows.

The genrec dial after B: 6 of 21 cases fully CLEAN
(alias_name_collision, cross_module x3 flavors, tree_str,
two_module_qualified -- all six flipped), all 21 byte-identical
(two_param stays marked: an `iter.method_call_shape` residue outside
the track). Residue matches cells C/D/E: return slots (x~11), ctor MIL
fields (x6), tuple unpacks (x3) + the box_field/optional singles and
one tree_int `==` fold.

### Genrec track cell C, first slice: Own[genrec] return rows + the elem fold (2026-08-04)

`prescan.ret_genrec` (`_own_genrec_return`) admits the `Own[Tree[T]]`
return slot into `ret_supported` -- no member restriction (the wrapper is
one C++ value type; SOURCE rows gate): a container literal returns the
ru-instance spelled render (`return std::vector<Tree<int32_t>>{1, 2,
3};`), a scalar member value returns bare through the generic tail
(`return 7;`); everything else is the named `return.genrec_source`
reject (NAME sources pinned fenced). `_ru_elem_ok` gains the fixed-int
ctor fold (`Int32(1)` elements render their bare tokens, same int32
bounds as the raw literal) -- shared with the decl/arg ru-literal rows.
Remaining cell-C flavors for the next slice: the bare genrec BORROW
return (`-> Tree[Int32]` field reads), the tuple-of-genrec return, the
`Own[genrec] | None` optional slots, and the genrec-returning CALL
rvalue at arg positions (`leaf_count(make_leaf())` -- the argtemp row).

### Genrec track cells C-remainder + D: field rows, borrow returns, the Own ctor-arg cascade (2026-08-04)

Three more row families on the accessor: (1) the ctor MIL-field arm for
generic-instance fields -- the `Own[Tree[T]]` param move rides the
type-agnostic M3b-move arm verbatim; a container literal renders
ru-instance spelled (`mil.genrec_literal`); (2) `_record_borrow_return`
admits the bare `-> Tree[Int32]` slot (the same borrow direction --
`Tree<int32_t>&`, `return this->t;`); (3) `_own_lvalue_temp_slot` gains
the generic-instance payload arm, so `Holder(seed)` at an
`Own[Tree[Int32]]` ctor slot rides the Own-slot cascade -- and the
ru-literal decl rows now PROMOTE MOVABILITY (they never did; the
temp-free `std::move(seed)` at a movable last use needs it, and the
move-verdict join is the second witness). All 21 genrec cases stay
byte-identical. Remaining residue: the genrec-returning-call argtemp
row (`call.ret_type.other` / ctor-arg-call singles), the borrow-call
decl rows (`g = h.get()` -- `decl.slot_type`), the open-T MIL field
(box_field's `genrec_open`), tuple-of-genrec returns/unpacks, the
optional slots, and the tree_int `==` fold.

### Genrec track cell E, first slice: the ctor-arg rows (2026-08-04)

The ctor-arg gate gains the M4c wrapper NAME row (`Summary(seed)` /
`Pair(t)` -- a same-wrapper name, non-generic alias or generic instance,
binds the borrow ctor slot bare) and the `Own[genrec]` container-literal
row (`Holder([1, 2])` renders the ru-instance spelling INLINE -- a
prvalue into the by-value Own slot, placed OUTSIDE the temp_args block
since it needs no flush; the first cut sat inside it and never fired,
caught by the corpus chain-walk). `generic_recursive_ctor_arg` goes
CLEAN (the 7th of 21); one old fence
(`test_wrapper_union_ctor_arg_keeps_rejecting`) converted -- its stated
reason was exactly the missing row. Remaining genrec residue: the
borrow-call decl (`g = h.get()`), the call-subject match boundary, the
genrec-returning-call argtemp row, open-T MIL, tuple/optional flavors,
and the tree_int `==` fold.

### Genrec track cell E, second slice: the borrow-call/field tail (2026-08-04)

Three tail rows: (1) the borrow-call decl alias -- `_borrow_local_binding`'s
REF_ALIAS call gate admits the generic instance (`g = h.get()` ->
`Tree<int32_t>& g = h.get();`; the shared `classify_local_binding` already
said REF_ALIAS, only THIR's F1 filter blocked); (2)
`_ru_wrapper_borrow_call_arg` -- a BORROW-returning wrapper call binds a
same-wrapper slot bare (`leaf_count(h.get())`); (3)
`_ru_wrapper_field_arg` -- a same-wrapper FIELD read binds bare
(`leaf_count(self.t)` / `leaf_count(h.t)`), with the render prechecking
the field arm so its result gate does not re-ask. The method-arg ladder
also gains the four wrapper rows (the record-method twins). Three more
cases go clean (return_readonly, record_field, record_field_cross_module)
-- 10 of 21; all byte-identical throughout.

### The poly early-return narrowing cell (designed-queue item 3) + the branch comp decl row (2026-08-04)

The last approved design-queue item. `if not isinstance(v, Sub):
return/raise` and `assert isinstance(v, Sub)` on a poly-dispatch subject
now lower: `THIRDynNarrowAlias` (the persistent cast-and-cache alias,
`[const ]Sub& __v = *<cast_rhs>;`, composed via the shared
`_poly_cast_context`/`narrow_cast_rhs` chokepoints), the negated-guard IF
arm (`THIRUnaryNot` over the single-check `THIRDynIsinstanceMulti`), the
chain-walked `_poly_post_if_fact`, and the poly assert arm +
`_append_assert_narrow` tail. Re-narrowing chains bump the alias
(`__p` -> `__p_2`) and anchor every cast to the ORIGINAL declared type
via the new `lc.narrow.poly_source` (the AST's `lookup_var_type`
semantics; `declared` retypes to the member for the rest of the walk).

Design-vs-reality: the planned name-render override map and
`THIRIf.post_extractions` were NOT needed -- the existing
`lc.narrow.narrowed` rename already IS the override layer, and the
statement-level alias slots into `_lower_stmts`' post-if pass beside the
union arm. Two real discoveries instead: (1) a polymorphic-CLASS record
param is seeded `Ref[Pet]`, and `_poly_subject_decl`'s wrapper check
rejected the Ref -- the peel unlocked the whole class-subject side of
every poly arm (the dyn-protocol side never Ref-wraps, which is why the
landed if-init slice worked); (2) the peel must NOT widen
`_poly_isinstance_value_info` -- the corpus byte-diff caught
`short_circuit` (`isinstance(p, Dog) and len(p.bark()) > t`) where the
`&&`-RHS narrowed read renders the AST's inline static_cast, unmirrored;
Ref subjects are fenced out of the value-position arm and pinned.
Fences: else-bodied guards, `self` subjects (receiver arms render
`this->`, unreached by the rename -- dualgen caught the divergence),
resumable frames. `protocols/isinstance_early_return_narrowing` (15
bodies, the full scope-discipline battery: loops/try/match/with sibling
chains) went CLEAN in one slice and flipped.

The BARE comp-shadow half collapsed to removing the comp decl arm's
fn_top gate (the render is a position-independent stmt-expr; hoisted
names and classified shadows already reject via prescan + the route's
shadow check) -- `match/capture_rebind_comprehension` flipped.

### Genrec tail: the Own[genrec]-returning call argtemp row (2026-08-04)

`leaf_count(make_leaf())` hoists the AST's argtemp
(`Tree<int32_t> __tmp_N = make_leaf();`, prvalue init, bare name
passed): `_ru_wrapper_own_call_arg` (keyed on the Own-DECLARED return
matching the slot's instance; free calls only), the gate row, the
`argtemp.ru_wrapper_call` lowering row, and the STORAGE result family
`call.genrec_own_ret`. The chain's real lesson:
`_recursive_union_borrow_call_arg` was ALREADY capturing the call and
binding the fresh prvalue inline to the `const&` slot (C++-legal via
temporary lifetime extension, but not the AST's render) -- the
borrow-vs-owned discriminator is the DECLARED return spelling, now
excluded there for both flavors; the non-generic flavor falls back
(pinned). `union/generic_recursive_return` flipped -- genrec 11 of 21.

### Batch-4 review round (2026-08-04)

Codegen-correctness, safety-model, architecture-fit,
convention-compliance all clean (byte-composition traced to the AST
chokepoints; poly_source anchoring verified against lookup_var_type
semantics). Applied: the non-generic Own-call-arg boundary pin
(test-coverage's warning -- the exclusion's second flavor had no unit
witness), the self-subject ASSERT fence pin, the resumable-guard fence
pin. Filed: the isinstance_early_return_narrowing mutate-and-observe
gap (TODO, cpython-parity). Suite green start to end: 11022 passed,
dial 3134 -> 3137, 0 move/binding divergences.

### Genrec tail: compare pair, Own-tuple rows, MIL move, protocol-method rows (2026-08-04)

Four more tail cells, four flips (genrec 15 of 21): (1) the
same-instance wrapper compare pair -- a `_union_compare_pair` arm keyed
`RecursiveAliasInstanceType == RecursiveAliasInstanceType` (the wrapper
struct's own operator, bare render; the NON-generic pair stays out,
boundary-pinned) -- `tree_int` flipped; (2) the Own[genrec] tuple slots
-- `_value_tuple_return_element_ok` / `_owned_tuple_call_ret` / the
standalone unpack's "move" target arm gain the genrec sibling of their
Own[F1-record] rows, and a container literal at the Own[genrec] element
slot takes the ru-instance render (`containerlit.genrec_own_elem`) --
`return_tuple` + `_readonly` flipped, the Own-tuple LOCAL decl slot
keeps its fence; (3) `mil.generic_record_move` -- an Own-param move
into a generic-record field `_f1_record` rejects (`Box[Tree[T]]`)
rides the type-agnostic M3b arm; verified against the AST side, whose
std::move wrap is unconditionally source-keyed, so the arm cannot
diverge for any field shape -- box_field's ctor routes, its ctor-ARG
side stays on the parked `_f1_record_type_arg_ok` fork; (4) the
protocol-method rows -- the wrapper NAME arg row, the
`method.protocol_genrec_storage_ret` result row, and
`_ru_wrapper_own_call_arg` widened to METHOD sources (the record
flavor routes too; the old fence pin converted) --
`protocol_method` flipped.

### Batch-5 review round (2026-08-04)

Codegen-correctness (all renders verified against oracles; the
compare-pair op-permissiveness and MIL type-agnosticism both traced to
matching AST behavior), safety-model and conventions clean. Applied:
the protocol-rows unit pin (test-coverage's critical -- the rows were
corpus-witnessed only), the MIL non-move boundary pin, the
name-source-at-tuple-element routing pin (from the cell's dualgen
probe). TODO item 7 refreshed (15/21) and the parked MIL entry split
into its landed MOVE half and the still-parked ctor-ARG fork. Suite
green throughout: 11030 passed, dial 3137 -> 3141, 0 move/binding
divergences.

### Genrec dict-view iteration + the poly-match FIELD/SUBSCRIPT subject (2026-08-04)

Two cells + a harvest, six flips: (1) `_container_genrec_elem` -- the
genrec sibling of `_container_record_elem` -- admits the dict-view
iteration over `dict[K, DictTree[K, V]]`, with the open-K key
admission scoped to that ONE predicate (the values-view render is
key-blind; `_dict_key_shape_ok` untouched for every other consumer);
`generic_recursive_two_param` flipped -- genrec 16 of 21, the
remaining five all parked (field_alias's match boundary, the two
optional flavors, box_field's ctor-ARG fork, the const-borrow-pointer
try hoist). (2) The poly match lowered its SUBJECT with the default
VALUE use, so any non-name subject rejected at the field/subscript
result gates; a field subject binds the lvalue borrow
(`auto& __match_subject_N = o.pet;`), so non-name subjects now lower
under BORROW_BIND, where the existing F1 rows admit them --
`match/poly_expr_{field,const,guard}` flipped, and the harvest picked
up `poly_expr_subscript` + `protocols/dyn_recursive_alias_method` as
side effects (the subscript flavor disproved the pin comment's
"sema-rejected" claim; corrected). Dial 3141 -> 3147.

### Batch-6 review round (2026-08-04)

Codegen-correctness + safety clean (BORROW_BIND verified unreachable
for previously-routed subject kinds; the open-K admission verified
single-consumer). Applied: the keys()-over-open-K routing pin, the
plain-open-V dict-view boundary pin, and the SUBSCRIPT-subject pin
(mirroring the corpus shape). One nit carried: the harvest commit's
subject line ran 83 chars (left as-is per the no-amend rule). Suite
green throughout: 11036 passed, 0 move/binding divergences.

### The stored-exception raise + the container equality pair (2026-08-04)

Two cells. (1) The gather_settled quartet's stored-exception raise
(`raise r.exception` on a narrowed Optional[Box[Throwable]] field),
landed as the ordinary cell its avoid-list re-price recommended: the
non-call raise operand lowers at RECEIVER (keeping indirect_read, whose
branch precedes the receiver exclusion in `_name_read_deref`, so every
routed pointer-local raise still derefs -- dualgen-verified), and the
field result gate gains the generic-record RECEIVER row
(`field.generic_record_recv`) for instantiations `_f1_record` rejects.
All four async cases flipped; the raise_expr frontier is fully
drained. (2) The @dataclass __eq__ chain's container rows:
`_container_compare_pair` (same-type ==/!=, the container's own
operator) and the compare-operand ladder's container-FIELD row
(BORROW_BIND; the field gate's container row widened
ITERABLE -> ITERABLE|BORROW_BIND, DECLARED-keyed). One stale dict
fence converted. No flips yet -- the dataclass cluster still carries
the fstring/repr tag (the per-family repr arg rows, next wave).

### Batch-7 review round (2026-08-04)

Codegen-correctness + safety clean (the RECEIVER widening traced as
admission-only; other BORROW_BIND field consumers verified pre-gated
away from containers; ordered_set/map operator== confirmed
order-correct in the runtime). Applied: the binop.container_eq face
assert, the Array-pair fence (the reviewer's real find: the Array
flavor is pair-admitted but has no operand read row -- fenced until
wired), and the note that the minimal sync raise fixture routes via
pre-existing narrowed-field machinery (the new face's witness is the
quartet itself). Skipped as guarded: the narrowed-global raise
combination (corpus byte-diff covers; no reachable witness found).
Suite green: 11043 passed, dial 3147 -> 3151, 0 divergences.

### The dataclass repr/print field rows + a dead-row deletion (2026-08-04)

The fstring/repr half of the dataclass cluster: (1) the dict flavor of
the repr field rows (`_native_protocol_field_arg`'s container branch,
the native-arm whole-member split, `_value_elem_container`) --
`::tpy::dict_to_str(this->lookup)`; (2) `arg.protocol_record_field` --
a concrete F1-record field at a still-PROTOCOL slot of a TEMPLATE
callee (the `repr_of({0})` fi's param stays Representable, so the
exact-match regime never fires; the AST's Adapter wrap is
@dynamic-only, verified, so the bare bind is exact); (3)
`print.record_field` -- the F1-record FIELD print arg streams RAW
under BORROW_BIND like the record-call row. The fstring rejects
cleared cluster-wide; `records/dataclass_field` flipped (dial 3152).
Residue per case: the asdict expr-stmt calls, dict_comp, and the
tuple-field `==` shapes.

Also: the `field.generic_record_recv` row (batch-7's raise cell) was
DELETED one run after landing -- the corpus faces tally exposed it as
zero-witness, and probes showed every raise-operand field flavor
(narrowed and plain) routes through pre-existing RECEIVER admissions;
the cell's real fix was the operand USE change alone. The dead-row
doctrine's fastest catch yet: the faces tally, not review, found it.

### Batch-8 review round (2026-08-04)

Clean on code: the protocol-slot record row verified against the AST's
@dynamic-only Adapter wrap; the dict widening's two consumers verified
render-shared; the deletion verified reference-free. Applied: ledger
entries + a stale class-docstring citation of the deleted row. Suite
green: 11046 passed, 0 move/binding divergences.

### Pointer-slot container globals in the print wrap row (2026-08-04)

A container GLOBAL printed at top level is a pointer-slot name the
container print rows excluded; the hoisted-container disjunct gains
`lc.prescan.global_slots` (the same kind-keyed wrap over the slot
deref, `ListPrinter((*nums))`; Optional bindings still excluded --
verified non-vacuous at top level, where `declared` keeps the real
OptionalType). Four flips: list_from_range, list_from_iterator,
empty_list_inference, module_scope_empty_literals (harvest). Dial
3152 -> 3156. The dataclass asdict/astuple residue is PARKED on the
TODO-42 macro-peel fork; the deref-check call-receiver field family
(`field.receiver_shape`) is the next open group.

### Batch-9 review round (2026-08-04)

Clean; the reviewer hand-verified the Optional exclusion fires at top
level (a broken exclusion would null-deref -- UB class) and that all
four oracles show the deref render. Applied: the Optional-container-
global boundary pin (fixture Own-returned to satisfy sema) + this
ledger sync. Suite green: 11047 passed, 0 move/binding divergences.

### The deref-checked CALL receiver rows (2026-08-04)

`find(points, 5).x` / `.mag()` -- an unproven field or method access
off a BORROW-returning ptr-Optional call wraps the raw `T*` result in
`::tpy::deref_check(...)`: the shared `_optional_checked_recv_call`
core (Own-declared returns excluded), the field flavor lowered under
ptr_opt_passthrough into the existing deref_check emit, and the method
flavor admitted at the nonname-receiver + optional-check gates with
the same passthrough threading. `pointers/optional_param` flipped +
`optional_chain_access` harvested (dial 3157). The batch-10 review
PROBED the Own exclusion and found it load-bearing: the AST's own emit
for that shape is uncompilable C++ (deref_check has no
std::optional overload) -- filed in BUGS.md, boundary-pinned; the
review also verified the passthrough threading is admission-only and
rests on all three iterable_override predicates rejecting
needs_optional_runtime_check (now commented at the site).

### Batch-10 review round (2026-08-04)

Applied: the Own-flavor boundary pin, the BUGS.md filing for the
pre-existing uncompilable deref_check-over-Own emit, the cross-file
invariant comment, this ledger sync. Suite green: 11049 passed, 0
move/binding divergences.

### The double-subscript field receiver arm (2026-08-04)

`nested[0][0].x` -- the borrow-elem subscript shell resolves a
DOUBLE-subscript receiver (the inner subscript is a nested-container
element lvalue via `_container_ref_alias_elem_subscript`), so every
level renders the same __getitem__ nest with `.` member access. No
full flip on its own (the receiver-shape family's remaining cases
carry other sub-shapes); routing-pinned. One fallback-frontier LABEL
pin updated (the doubly-nested len arg resolves one receiver level
deeper before rejecting). Landed after the batch-10 round; reviewed
by the full-branch pass.

### Full-branch /tpy-review round (2026-08-04)

Seven specialists over master..HEAD (87 files, 46 commits) + a
meta-review. Codegen-correctness (live compile probes of the
finally-deferred return and negated poly narrow included) and
safety-model fully clean; zero Criticals stood after meta-review
re-bucketed the two docs-sync counts. Applied: the genrec item-7
count fix (22 cases / 16 flipped / 6 parked -- tuple_param_element
named as the sixth, probe-verified still blocked on its BORROW-form
tuple-param return/unpack slots), the gather_settled entry reworded
past-tense on the deleted row, this ledger entry pair, the vacuous
genrec-match boundary assert fixed to test the real render, a direct
unit for the binding-audit ACK-suppression arithmetic, and the
fourth-copy note on the arg-ladder debt entry. Dropped visibly:
commit-subject lengths (no-amend + squash-merge), two low-confidence
pin suggestions (corpus-guarded).

### The @dynamic protocol RETURNs bundle -- queue item 1 (2026-08-04)

All six designed pieces, 9 flips (protocol_dynamic_{return,
return_readonly,global,cross_module,own_param}, own_polymorphic_param,
self_referential_protocol, dyn_proto_self_return_{method,generic}):
ret_dyn_borrow (`P&`/`const P&`, name sources) + ret_dyn_own
(unique_ptr<P>, classify_dyn_own_arg-keyed -- 'forward' bare,
conformers through the shared _lower_dyn_own_conformer wrap); the
erased-decl borrow-call rung (`Pet* r = &echo(d);`); Own[P] binding
reads admitted (unmirrored Own-slot args still reject per ladder);
protocol globals via the DYN_PROTOCOL rebind (static
`__global_slot_N` spelling via slot_static/slot_prefix; joins the
_rejects_global_slot allowlist; branch/loop writes included); the
_own_dyn_method_recv family + _recv_own_dyn arrow mirror; protocol
chain results at RECEIVER positions and Own[P] method results bare.
Converted stale fences: Own[P] param, protocol return, dyn global.
Boundary pins: mixed ternary arm, erased-source global write.
sema pre-rejects returning a dyn-protocol LOCAL (dangling check), so
the borrow name row's local flavor is param/global-only by
construction.

### Own[union]/wrapper-union returns -- queue item 2 cells A-C (2026-08-04)

8 flips (union/narrow_{list,dict,tuple}_subscript, repr_fstring_union,
union_own_mixed_value, union_recursive_int_float, own_union_consume,
wrapper_param_const). The widened variant-member class lives in
_ptr_union_member_wide, consumed ONLY by member-shape-blind machinery
(the Own[union] return slot, _call_ret_union_ok, the UNION_RVALUE decl
twin, the isinstance info gate); _eligible_ptr_union keeps the narrow
set -- the documented widening hazard stands. New return facts:
ret_own_wrapper (None -> monostate, scalar literals, ru container
literals incl. the outer literal typed AS the wrapper, member-container
names) and ret_wrapper_borrow (name sources). Union NAME prints route
via the __str__ visitor (PrintForm.STR) -- FENCED out of resumable
bodies: the flat-CFG persistent assert-narrow alias leaks past its
branch on the AST side (`__str__(__a)` in the else arm), so the
un-narrowed read would diverge; the BB-scope leak pin keeps its
fallback detector through that fence. AliasRef-vs-resolved-union
duality closed at three sites (_ru_wrapper_name_arg bindings, the
ru-literal element slot, the container-method element slot). 2D (the
Optional-of-wrapper pointee rekey) is NOT in this wave -- it rides the
scoped pointee accessor shared with 3-G1/5-P-A.

### Union-returns wave addendum: the ratchet catch + review round (2026-08-04)

The full-suite ratchet caught union_mutual_basic DE-ROUTING after the
wave landed: `return Lit(v)` at Own[Expr] had ridden the plain
Own[union] arm incidentally (wrapper unions passed its member check),
and the new ret_own_wrapper arm intercepted without a record-ctor
source row. Fixed by making the two facts mutually exclusive
(_own_storage_union_return now rejects needs_wrapper) and adding the
ctor-rvalue row to the wrapper arm -- the running lesson holds: the
ratchet proves no WRONG flip, and a NEW fact must enumerate every
source its slot previously reached through sibling arms. The
check-flip harvest then yielded 2 collateral flips
(recursive_union_empty_list, union_recursive_protocol_method).
/tpy-review round (7 specialists + meta-review): codegen-correctness,
safety-model, convention-compliance clean. Applied as one commit: the
resumable assert-narrow alias-leak defect filed in BUGS.md (the STR
fence's reason -- pre-existing AST, compile-verified by two
specialists); the STR fence rekeyed from is_generator to
resumable_leaf_mode (simple peephole generators keep normal scoping
and may route); the AliasRef-vs-resolved-union duality folded into
the single _resolve_plain_alias accessor (5 sites); the erased-decl
METHOD-call routing pin, the dyn-own forward method-call boundary
pin, and the int32-edge wrapper-insert boundary pin;
wrapper_param_const's read-only-aliasing test-adequacy gap filed in
TODO.md (pre-existing, needs a deliberate mutate-and-observe redesign
+ snapshot consultation).

### The scoped pointee-accessor waves -- queue items 2D/3-G1/G2/G3 (2026-08-04)

The ONE accessor build the queue's dependency note called for:
_opt_pointee_wide (F1 records | wrapper-union-likes | open type params |
@dynamic protocols | containers) behind _optional_ptr_borrow_wide (the
ptr-repr flavor; + the force_pointer_repr VALUE-scalar class, safe only
under its uses_pointer_repr guard) and _storage_optional_return_wide
(the F1|wrapper pair, incl. the REVERSE `Own[Optional[W]]` spelling).
Landed across three slices + the harvest round (commits 4f21d44aed,
ea98cf3939, 514c49f91c, 9048080578): the return facts and their source
rows (pointee-name/field addr lifts, the container-element subscript
lift, the narrowed value-local deref+move), OPT_STORAGE_CALL (a new
PtrSlotKind: `std::optional<T> __slot_N` + optional_to_ptr lift),
lazily-slotted branch-hoisted ptr-opt locals (bare-copy/None reseats;
the AST allocates no if-head slot there), the ifexpr call arm, the wide
param seeds/None-tests/print_optional row, the deref-name arg row, the
None-narrowed Optional[wrapper] isinstance subject, wrapper NAME
elements in container literals (with the element move mirror), G2's
sync generic-tuple return literal (the resumable to_val_or_ptr builder
reused) and G3's Array borrow returns + Array pointer-slot globals.

LESSON (the wave's catch): the remote byte-diff -- not the local units
-- caught three consumer-position leaks of the widened class, one per
consumer kind: the PRINT row (container pointees spell kind-keyed
print_optional template args), a FIELD row (a None-narrowed Optional
field retypes its expr but stores std::optional -- key the DECLARED
type), and a DECL row (OPT_STORAGE_CALL over-captured plain
`Own[Box]`-returning calls -- require the storage-optional callee).
All three are boundary-pinned in test_thir_wave_pointee.py now.

Two sibling-lookup fixes rode along: substitute_method_type_params now
propagates is_property_getter/setter (the fact-drop class the
deep_const audit named; an inherited GENERIC property's resolved fi was
unrecognizable as an accessor), and the four record-dunder predicates
share _record_method_with_parents (get_method misses inherited dunders
through generic parents; getitem routed while setitem/delitem silently
rejected -- consolidated after review).

12 flips: generic_recursive_own_optional_return,
generic_optional_{record,mutate,none_join}, getitem_optional_ref,
union_recursive_optional, coro_pointer_local_no_leak{,_reverse},
reassign_none_use_before_anchor, const_borrow_local_dict,
inferred_readonly_borrow_const, implicit_return_none. PARKED:
inherited_accessor_subst (write-through-property, REF_ALIAS-adjacent).
Partials (one call-composition blocker each): ref_generic_tuple_return,
tuple_generic, generic_int_param_array_subst.

### Queue item 4 -- async coro-param drill + erased adapter handle (2026-08-04)

Four rows, each clearing its witness: the Own[@dynamic P] frame-PARAM
family (bare unique_ptr<P> field; the forward-move read rides the
existing arg rows) + the Own[T] return slot (STORAGE Poll<T>, the
position-blind tail); the owned-erased handle's dedicated decl arm
(_lower_erased_handle_write -- member-assign of the own-arg render,
slot keyed on the FRAME type since sema's declared map carries the bare
structural wrap, movability promoted, subtracted from
plain_frame_fields so a branch bind can never silently drop the
make_adapter wrap; CFG-split branch binds are BB leaves and route
through the arm anyway); the resumable protocol-param loop
(protocol_param_ok threads per-name in leaf mode -- a suspension-free
loop over a bare-rendering param NAME emits the sync universal
::tpy::__iter__ shape inside the case block); and the frame_opt_ptr
unpack bind (a __for_tup_* VALUE holder with STORAGE-optional elements
admits via opt_tuple_holders, the head unpack mutable-ref-binds it and
lifts each Optional target via optional_to_ptr).

LESSON (the wave's catch): the collateral flip
(nested_def/in_generic_async) exposed a latent COMMENT divergence -- the
frame nested-def marker line spelled the ESCAPED member name
(`// def double_: frame member`) where the AST keeps the PYTHON name.
A marker that only shows on keyword-colliding names is invisible until
a case that routes one flips; the byte-diff caught it at harvest, not
any unit. Source comments are part of the byte contract.

5 flips: async_method_static_protocol_param, async_bind_cross_module,
async_bind_template_fallback, gen_resumable_tuple_unpack_optional,
nested_def/in_generic_async. PARKED: the start_server trio
(async-def-NAME-into-Callable lambda-bridge synthesis -- item 7's
render family, confirmed by re-probe).

### Queue item 5 -- Optional[dyn P] family (P-A) + nullproto constexpr (P-B) (2026-08-04)

P-A, over the pointee accessor's dyn class: the None-narrowed
Optional[dyn P] method receiver joins the protocol family
(method.opt_dyn_recv; `->` via the pointer set); the
structural-conformer Adapter/RefAdapter arg temps (brace init +
`&(__tmp_N)`), with the adapter face checked BEFORE the
polymorphic-subclass ctor face -- polymorphic_source_inner passes for
ANY distinct record under a dyn-protocol inner, so the old order was a
LATENT wrong 'ctor' admission (`Cat __tmp` instead of the Adapter),
masked only by whole-body fallback; _protocol_union_ctor_arg's 'addr'
verdict got the same structural-conformer exclusion. The
OPT_PROTO_RVALUE local decl types the slot at the conformer's class
(inheriting) or `auto` (structural, monomorphized) with the deduced
`auto* p = &__slot_N;` pointer line.

P-B, the nullable STATIC-protocol param machinery:
_nullable_static_protocol_param (Optional[proto] | nullable
all-protocols union -> the monomorphized `const T_x*`) seeds the param
into lc.pointers -- closing the seed_param_locals partial the context
docstring had documented -- and keys the guard swap
(_lower_nullproto_guard_if: `if x is not None:` -> `if constexpr
(!std::same_as<T_x, std::nullptr_t>)`, _lower_constexpr_if's branch
model). Name reads deref at every value position ((*items) at len
args, loop captures, subscript receivers, required-union pass-onward,
including the guard-retyped protocols-only union spelling); None args
spell the typed null static_cast<std::nullptr_t*>(nullptr); the 'addr'
lift runs at free/ctor/METHOD positions alike (the required union has
no None member, so ArrayList.extend's bare method bind is inert to the
verdict -- the old blanket method exclusion protected nothing).
_protocol_union_ctor_arg + _nullable_protocol_slot moved to
predicates.py as the shared gate/lowering verdict.

9 flips: opt_dyn_{readonly,structural,generic_protocol}_param,
opt_dyn_protocol_{local,param} (P-A);
isinstance_optional_protocol, protocol_optional_param,
protocol_union_optional, protocol_narrowed_to_union (P-B).

### Deref inversion + queue items 6-7 (2026-08-05)

Dial 3203 -> 3212/3729 (+9 flips over three cells), markers 526 -> 517.
Full comp suite green each cell (11100 passed; move-verdict and
binding-fact joins clean throughout).

**Cell 1 -- the `_name_read_deref` inversion (the 2026-08-04 retro's
mechanical followup).** The value-position arm's accumulating admission
classes (containers, protocol flavors) flipped to the exclusion-based
mirror of the AST rule: outside RECEIVER/BORROW_BIND any pointer-set
name derefs, EXCEPT the record class (`_record_class_binding` --
_f1_record's record-ness legs without the spelling constraints; a
formatter-carrying builtin is NOT a record, which reproduces the old
container admission by construction) and the ptr-repr Optional binding
(None tests key on the pointer; the nullable-static-protocol param stays
out of the carve-out). Zero behavior change: corpus byte-diff green,
dial unchanged. The `_protocol_union_arg` rename rode the touch.

**Cell 2 -- queue item 6, the Own[P|None] storage bundle (+3 flips).**
The reverse-nesting own-optional landed as one sync piece set: param
seeding (pointers + optional_locals, replacing the two _binding_ack
partials; the binding stays declared Own[Optional[P]] and rows key on
`_own_storage_opt_param`), has_value None-tests, arrow reads (read and
write positions), `return std::move(x)`, the arg rows (NAME move /
same-Optional call bare / optional_to_ptr borrow-forward / the
method-ladder ctor-rvalue wiring), the pure-lift decl+reseat rows, the
REASSIGNED OPT_STORAGE_CALL slot reuse (`__slot_1 = make(43); z =
optional_to_ptr(__slot_1);` -- opt_storage_call_locals fences other
reseat shapes off the slot), the field-write whole-optional move, and
the per-element-own tuple return + tuple_to_pointer'd unpack capture
with opt_ptr targets. Two stale pins converted -- each had held only
via WHOLE-BODY fallback and was re-adjudicated by dualgen (zero
fallback, byte-identical): the own-optional call-init fence and the
narrowed-occurrence face==0 pin (the AST is narrow-blind for Optional
None-narrowing, so the null-safe rebuild is the correct render there).
Flips: auto_move/scalar_own_optional,
records/ctor_field_init_own_optional, tuple/own_tuple_unpack_optional.

**Cell 3 -- queue item 7, the callable-reference bundle (+6 flips).**
Generic fn-ref template-args suffix (one shared predicate serves
decl/return/arg); the MIL callable-lambda field row (self-capturing
lambdas stay fenced); the async coroutine-factory wrapper synthesis
(`_async_factory_wrap_cpp` mirroring _maybe_wrap_async_coro_factory) at
the name arm, Callable return row, and ctor-arg ladder; std::function
frame PARAM + LOCAL admission (bare value fields); the Own[P]-forward
row in the qualcall ladder, now also serving METHOD-shaped
callable-FIELD invocations via the shared classify_dyn_own_arg
'forward' verdict; the callable-field receiver arm's resumable-method
`__self` (Record&) dot flavor. The start_server trio RE-PROBED: its
remaining blocker is chained subscript receivers, not the callable
bridge -- ordinary rows for the next census. Flips:
async/async_fn_as_callable_{arg,field,return},
calls/callable_field_print, calls/func_ref_generic{,_cross_module}.

**Pins:** the Own[P|None] routing/boundary set
(test_thir_binding_facts.TestOwnOptionalStorageBundle + the converted
param-read/field-write pins, the own-slot-forward fence, the
scalar-twin fence), the OPT_STORAGE_CALL slot-reuse pin
(test_thir_ptr_locals), the narrowed-rebuild pin
(test_thir_wave_call_argrow), test_thir_wave_callable_ref.py (targs
suffix, MIL lambda render, the wrapper-lambda byte string), and the
MIL self-capture boundary (test_thir_wave_mil_views).

### Genexpr C4 + flat-tail waves 1-2 (2026-08-05)

Dial 3212 -> 3221/3729 (+9 flips over three waves), markers 517 -> 508.
Full exec suite green (11112 passed, all 3728 built+run). The designed
queue closed with item 8; the flat-tail grind then opened on the fresh
census (513 marked / 299 sole, max sole tag 6 -- the long tail is the
work now).

**Item 8 -- genexpr C4 (+4: genexpr_basic, genexpr_builtins,
enumerate_rvalue, literal_binding_resolves).** THIRGenExpr grew the
range counter-lambda flavor (1/2/3-arg bound captures, step-nonzero +
fixed-int overflow checks), &&-joined filter conditions wrapping the
yield (cond/yield temps flush per-iteration via checkpoint/flush_since),
str loop-var bindings, and the POSITION matrix: a structural user slot
hoists the un-spelled auto temp (argtemp.genexpr_proto) while
native/template slots keep the inline render (the blanket
_lower_call_arg intercept now defers to the protocol block); method
slots admit position-blind (str.join); ITERABLE-use value positions
route (list(...)/extend) while other value positions keep the
dispatch-tail reject. Three stale fences converted (range, filter,
filtered-unpack). Residue: genexpr_ref_mutate (borrow-slot yield +
for-head genexpr source), list_from_iter_nested (call.inst_arg_shape).

**Flat-tail wave 1 (+2: generic_int_param_array_subst,
requests_cookies_send).** VALUE-Array call results land bare at any
sink (call/method.array_value_ret -- std::array is a value container,
span-like no-duality) + the matching-slot arg rows
(_value_array_call_arg, free+method ladders); a container LITERAL at an
Optional[container] slot hoists the spelled typed temp + `&(__tmp_N)`
lift (optptr.container_temp, flush-gated). The site's third case
(ref_generic_tuple_return, the dict(map(lambda ...)) generic
composition) remains.

**Flat-tail wave 2 (+3: the async start_server trio).** The trio's
whole blocker was ONE admission row: a user-record __getitem__
subscript returning a record borrow as a METHOD receiver
(`server.sockets[0].getsockname()[1]`) -- the method arm's BORROW_BIND
receiver lowering already rendered the operator[] chain
byte-identically, so _method_nonname_receiver_ok just gains the row.
Two speculative render-side rows probed DEAD during the wave and were
dropped rather than committed (the disable-and-dualgen check -- worth
repeating on future "obvious" render rows).

**Pins:** TestGenexprC4Cells (range byte + negative step, filter byte,
the temp-vs-inline split) + the three converted genexpr fences;
test_thir_wave_tail1.py (Array-value rows, the optional-container
literal temp, the sync start_server chain twin
`std::get<1>(srv.sockets[0].getsockname())`).

### Flat-tail waves 3-5 (2026-08-05)

Dial 3221 -> 3229/3729 (+8 flips), markers 508 -> 500. Full exec suite
green (11118 passed, all built+run).

**Wave 3 (+4: the async reactor pair, match_union_primitive,
isinstance_narrow_set).** _dyn_own_conformer_arg admits Own[record]-
returning METHOD rvalues (`wait_for(loop.sock_accept(srv), ..)` -- the
BORROW_BIND lowering already rendered the make_adapter wrap; the
matching resumable fence converted). The ptr-union decl classifier:
scalar/str LITERAL inits take the bare-literal UNION_RVALUE slot
whatever type sema stamped (single-member-of-family keys out ambiguous
variants -- boundary-pinned); a non-value NON-record member lvalue NAME
falls to UNION_ADDR. _union_pass_through_arg widened to the WIDE member
class (the by-value variant copy is member-shape-blind).
union/ternary_mixed_form's mixed-variant ternary init parked as an
ordinary later row.

**Wave 4 (+3: the ptr-local rvalue-frame trio).** The resumable
frame-field ptr-slot rows, each reading the skeleton's ptr_slot_map:
FRAME_RVALUE (`saved = &*(__ptr_slot_fN = Point(9));`),
FRAME_STORAGE_CALL (the Own-opt call fill + optional_to_ptr re-lift),
and the slotless subscript-element re-point's Optional flavor. The
rebind-slot-holder fence converted: its protective claim (the sync
rebind-slot arm stays unreachable) is now satisfied by the frame arm
capturing the shape first.

**Wave 5 (+1: gen_subclass_opt_rebind).** The FRAME_RVALUE subclass
slicing flavor: a subclass ctor rvalue into the base-typed frame slot
(sema's warned "upcast narrows") admits via the plain registry
subclass relation -- is_polymorphic_subclass_fact keys on
dynamic-dispatch sources, which a data-only base is not, and the
polymorphic flavor never reaches lowering (sema errors first).

**Scouted, not taken:** stmt.match's 4 sole split into the PARKED
genrec call-subject boundary (field_alias), the genrec-adjacent
match_hoisted_wrapper_local, and capture_optional_narrow_in_body (the
class-pattern keyword-capture registration -- an ordinary later row);
coro_for_tuple_unpack_ref's tuple_value_to_borrow double-convert and
ref_generic_tuple_return's dict-map-lambda composition stay next.

**Pins:** TestPtrUnionDeclRows (+ the ambiguous-literal boundary),
TestOwnRecordMethodRvalueConformer, TestFramePtrSlotReseats, the two
converted fences (awaitable-record-factory, rebind-slot-holder).

### Flat-tail waves 6-8 (2026-08-05)

Three cells, +7 flips (dial 3229 -> 3236/3729; markers 500 -> 493).
Review round 4 clean (two trivial suggestions applied: the wide-member
consumer list, two pins moved onto _assert_routes_byte_identical).

**Wave 6 (+4: the if.narrow_shape union quartet, incl.
union_mutual_contexts -- its last blocker).** The single raise site
(the narrow_ok ladder) drilled to two legs over its four sole-blocker
cases. (a) folded+else: sema's exhaustiveness fold with an EXPLICIT
else -- the chain skeleton already lowers it (`if (true)`, dead else
arm extracting its excluded member from the else facts, the AST's dead
emit); the fence leg dropped, witnessed if.narrow_folded_else. The F1
inner-fold flavor with else stays fenced (different render family).
(b) a non-member ELSE fact (the remaining NULLABLE union, `B | None`
on an `A | B | None` subject) tolerated when NO else body exists --
no else extraction runs and both paths' post-if arms take concrete
members only (AST _narrows_to_union_member / THIR _narrow_fact_member);
witnessed if.narrow_nc_else_fact, the with-else shape keeps rejecting.
The leg conversion exposed one STALE fence: the str-member union
isinstance "member-keyed still defers" pin held on the else-fact leg,
not its stated member-eligibility reason (the isinstance gate already
rides the wide class) -- re-adjudicated by dualgen, converted.

**Wave 7 (+2: capture_optional_narrow_in_body, optional_basic).** Two
rows. Match keyword captures of VALUE-repr Optional fields
(Optional[scalar] / owned-inner Optional[str]): the gate admits, the
shared capture arm registers the `auto&` alias as a value-opt binding
(SCALAR/VIEW kind), so the body's None-test and narrowed derefs ride
the existing binding-keyed arms; POINTER-repr fields keep the
hoisted-pointer rung. And the str-concat operand rung for NARROWED
value-repr Optional[str] BINDINGS (_opt_view_narrowed_concat_operand:
the declared type fails _str_concat_operand, the analyzed type is the
narrowed str, the VIEW name arm renders `(*t)`); registration is
load-bearing -- an unregistered name would render bare -- and the
bytes flavor keeps rejecting.

**Wave 8 (+1: ternary_mixed_form).** The WIDE ptr-union ternary row:
_ptr_union_source_ok admits a TpyIfExpr whose arms each re-classify,
and _lower_if_expr normalizes per arm (`((c) ? (p) :
(::tpy::to_ptr_variant(h.pet)))`) via _lower_ptr_union_ternary_arm --
a same-union ptr-variant BINDING name bare, a value-variant field
lvalue through the FormConvert union BORROW emit. A CALL arm keeps
rejecting (bare render is a later rung); the const-field and
member-typed arm shapes are sema-errors before lowering, probed.

**Pins:** TestNarrowFoldedElseRows, TestPtrUnionTernary (both in
test_thir_unions.py -- construct-named, not wave-named),
TestNarrowedOptViewConcat, the TestKeywordCaptureFamilies additions,
and the converted str-member isinstance routing pin.

### Flat-tail waves 9-10 (2026-08-05)

Two cells, +6 flips (dial 3236 -> 3242/3729; markers 493 -> 487).
Review round 5: no Critical; applied as one commit (the F1-record
predicate reuse, the missing annotation, the TODO test-gap rewording,
the kind-blind rationale + boundary-gap notes).

**Wave 9 (+1: coro_for_tuple_unpack_ref).** The plain-record
ALL-RVALUE tuple-literal append row: the Own[btuple] literal gate's
ptr-Optional element restriction gains its F1-record sibling
(_btuple_literal_elems_rvalue), scoped to all-rvalue literals -- the
CONST_REF storage rule the borrow builder does not carry only fires
on lvalue members, and an all-rvalue literal rides the
tuple_value_to_borrow source-tuple path into the same consuming
tuple_to_storage_move lift (the AST's double-convert, a quirk-mirror
render). A last-use movable local element keeps rejecting (its
per-element move render is its own rung); a borrowed lvalue element
is a sema error. calls/ref_generic_tuple_return was traced in the
same session to the PARKED generics-lane lambda-inference family and
recorded in TODO, not ground.

**Wave 10 (+3 direct, +2 collateral: the tplib json family).** The
value-opt NAME assign-target row: a registered value-opt name at the
raw TpyAssign TARGET position is a whole-BINDING write, lowered with
allow_whole_optional and rendered bare (`color = __color_2;`). The
shape only arises in macro-generated bodies (user code reaches the
binding via the var-decl reassign arm, which never lowers the target
as an expression) -- the @model decode was the witness; a NARROWED
target never reaches the row (the TpyAssign entry guard falls back
first, parity-verified). Kind-blind on purpose (the write consumes
the whole optional for every kind). Collateral: json_model_basic +
json_model_field_rename.

**Scouted, not taken:** the cross-module global-record RECEIVER
family (os.environ x3 -- filed in TODO as the next mechanical cell:
module-attr slot render + several receiver-gate rows).

**Pins:** TestPlainRecordBtupleLiteral (routing + the spelled
double-convert render + the last-use boundary),
TestValueOptAssignTarget (macro-generated twin; the
unregistered-target boundary is documented unpinnable -- parser never
emits the shape, failure direction is a safe fallback).

### Flat-tail waves 11-12 (2026-08-05)

Two cells, +6 flips (dial 3242 -> 3248/3729; markers 487 -> 481).
Review round 6: no Critical; applied as one commit (the shared
_plain_or_opt_own_slot peel replacing the gate/lowering duplication,
the dotted-form over-admit documented at _module_var_recv, the DEAD
container-route for-head leg dropped -- disable-and-probe showed the
user-iterator route carries os_environ_iter alone -- the set-literal
flavor pinned, a dated docstring phrase removed).

**Wave 11 (+3: the os.environ trio).** The cross-module global-record
RECEIVER family: _module_var_recv (the cross-module twin of
_global_record_recv, composing _bare_module_recv +
_module_var_read_cpp) admitted at the user-record setitem receiver,
the record-getitem index/receiver admission, the container/record del
receiver resolver, and the user-iterator for-head arm; the
record-getitem subscript arm and the del emit lower the receiver at
RECEIVER use, and the for-head ITERABLE capture joins the module-var
arm's pinned-consumer list (`auto& __src_N = (*environ);`). A bare
BINDING read keeps rejecting (no pinned consumer) and the
container-subscript flavor stays AST (the pre-existing fence held).

**Wave 12 (+3: the call.arg_shape.optional trio).** Three rows: a
value-opt-returning CALL rvalue at the SAME value-opt slot binds bare
(_value_opt_call_ret_arg; borrow-returning callees and the view
flavor stay out); copy(record) at an Own-OPTIONAL slot rides the
shared peel into the same copy-construct rvalue
(`consume_optional(Box(b))`); and the ptr-opt container-literal temp
face KIND-matches a bare list literal (its sema type is the
Array-inferred flavor) with the temp init self-spelled via
typed_brace_cpp -- the one divergence dualgen caught mid-wave
(`std::vector<std::string>{"a"}` vs the bare brace).

**Pins:** TestModuleVarReceiverSurface (the dict-backed Env twin
routes setitem/getitem/del/iterate with spelled renders; bare-binding
boundary), TestValueOptArgRows (+ the view-flavor boundary), the
list- and set-literal flavors on TestOptionalContainerLiteralArg.
Residue noted for a later rung: the .get/.keys/in/len module-var
receiver flavors are corpus-guarded only (they ride pre-existing
pinned consumers).
