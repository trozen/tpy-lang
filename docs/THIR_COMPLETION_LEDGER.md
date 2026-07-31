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
