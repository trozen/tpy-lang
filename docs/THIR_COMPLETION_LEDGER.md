# THIR Migration Completion Ledger

> **CLOSED 2026-09-03.** The migration finished when the four AST body
> emitters were deleted; see the final entry, "Cutover step 5 executed". This
> file is a historical record -- per-wave history, the deletion-target model,
> the gates and their baselines, and the lessons. Every metric, marker,
> ratchet, dial and dual-path gate it describes is gone. For the invariant the
> compiler holds today, see CLAUDE.md "THIR and the codegen boundary"; for the
> live fix queue, `scripts/thir_migration/review/`.

> **Operating model (2026-07-12):** the goal is COMPLETION (deleting the AST
> codegen), driven by the zero-whole-body-fallback loop in CLAUDE.md "THIR
> migration" (metric = migrated cases/fallback bodies, smallest per-construct
> residual prioritizes the next cluster, per-case machinery keeps it honest). AST
> body emit arms stay intact until the final atomic cutover. The deletion targets
> and completion model below map what blocks that cutover. The "sequence against routing %" /
> throughput-campaign framing predates this and is no longer how the work is
> driven.
>
> **Metric update (2026-08-29):** the migrated-CASES half of that metric is
> SATURATED -- 3746/3746 user cases, zero `no_thir.txt` markers repo-wide, and
> the interop corpus done at 34/34 cases / 285 bodies with zero fallback. It can
> no longer select work. The live metric is **stdlib fallback BODIES** against
> gate A5 (`tests/test_thir_stdlib_gate.py`), and past that the cutover
> checklist in the Gate D3 entry -- which was re-measured 2026-08-29 and is the
> authoritative statement of what is left. Anything in this file that reads as
> CURRENT state rather than as a dated measurement predates that re-measurement
> unless it says otherwise.

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

  **[The `(covered)` / `(not)` tags above are a 2026-07 snapshot; do not read
  them as current.]** Spot-checked 2026-08-29 at `fbfbb5b0d`, two of them are
  now wrong in the safe direction: genexpr is tagged `(not)` but has a full
  lowering arm (`thir/lower/comprehensions.py::_lower_genexpr`, with its own
  `genexpr.*` reject tags for the residual shapes), and the parenthetical's
  "a resumable / @error_return body still defers" for `raise <expr>` is
  contradicted by `_lower_raise`, whose comment records that both contexts
  render identically and route here too. Since the user case dial is saturated
  and stdlib fallback is at ZERO, treat every tag in this bullet as a
  lower bound on coverage rather than a status.

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

**[Staleness note, 2026-08-29.]** Every body count in the table above is a
2026-07 *routing-attempt* tally against the then-current tree, kept as a record
of relative mass -- not as current state. In particular `the remaining
ctor.mil_field mass (8,616 bodies)` is long superseded: user cases now fall back
ZERO bodies and stdlib is at 16. The three **PARTIAL** statuses are still
literally correct in the only sense that matters -- the AST components are still
present, because deletion happens once at the atomic cutover -- but the "what is
left" narrative in each Status cell describes the 2026-07 frontier, not today's.
For what is actually left, read the re-measured cutover checklist in the Gate D3
entry. Structural residues named here that ARE still live: `RefType` (still in
`tpyc/typesys.py`) and the form-machinery removal that F-final gates.

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

[Re-measured 2026-08-29 at `fbfbb5b0d` (same method, non-test LOC): `tpyc/thir/`
is **84,249**, `tpyc/codegen_cpp/` **38,642** -- THIR is now **118%** larger, not
33%. The scaffolding grew with it: `lower/checks.py` 14,109 + `lower/
predicates.py` 10,772 (against the ~10,200-line admission estimate drawn from
them a month earlier), and `faces.py` alone went 1,360 -> 3,118. The **parity
conclusion above no longer holds and should not be quoted**; nobody has re-run
the admission-vs-lowering split on the current tree, so the post-subtraction
figure is unknown rather than "~35k". The QUALITATIVE claim is unchanged and is
the part worth keeping: the migration buys structure, not code reduction, and
the reduction is collectable only after cutover.]

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
  [ALL THREE deferred rows have since LANDED, and this list contradicts later
  entries in this file. Inplace dunders admitted in the round-6 VI increment
  (`_param_is_const` grew the forced-const arm; see the "Maintaining this
  ledger" bullet for that wave) -- `tpyc/thir/lower/functions.py` and
  `predicates.py` both carry it now. `@readonly` statics admit at the same
  site (the readonly verdicts are signature-only, and a static body has no
  `self`, so the sole readonly-keyed body effect is unreachable). The
  property-getter union return routes via `_LowerCtx.ret_union_borrow`, which
  derives the storage-by-reference flavor ahead of `ret_ptr_union`. Verified
  2026-08-29 against `fbfbb5b0d` by reading the fences, not by re-measuring.]
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
  byte-identically. [LANDED since: the open-T value slot routes at the decl
  gate (face `decl.type_param_slot`, `lower/statements.py`), for an rvalue
  source that is not reassigned / hoisted / moved-through. Those three
  indirection flavors bind an alias instead and are their own rows, so the
  cell is narrowed rather than closed -- checked 2026-08-29 at `fbfbb5b0d`.]
  (`Own[T]` method PARAMS now land -- see the `Own[T]` cell in
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
  [The first two residual rows LANDED and were never back-updated here:
  `_free_callee_kind` gained a `NATIVE_C` arm on 2026-07-21 (`6f1b5e66b`) and
  `EXPORT_C` was folded into the same verbatim raw-symbol arm on 2026-07-22
  (`228d58b6b`) -- see `thir/lower/checks.py` around the
  `FunctionLinkage.NATIVE_C, FunctionLinkage.EXPORT_C` test. The other four rows
  were not re-checked on 2026-08-29.]
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
  [CLOSED, exactly as prescribed. `member_shadows_type` no longer appears
  anywhere in `tpyc/` -- neither reject face exists. `thir/lower/expressions.py`
  reads `RecordInfo.shadows_local_type` and qualifies the type spelling inline
  instead of rejecting the record. Verified 2026-08-29 at `fbfbb5b0d`.]

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
  task-layer consumers). [The REJECT is still live -- `resumable.py` still fences
  the bare reference-type return and `test_thir_wave_async_ret.py`'s
  keeps-rejecting pin still passes -- but the parenthetical evidence is stale:
  there are ZERO `no_thir.txt` markers in the repo as of 2026-08-29, so no case
  carries the shape any more. Checked at `fbfbb5b0d`.] The generic TRAIT form
  (`val_or_ptr_t<T>` +
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
  Still deferred: match-over-enum (the match-statement axis). [LANDED --
  `thir/lower/match.py` carries a full `switch_enum` strategy: enum subjects
  select it in `_match_strategy`, `_match_route` admits it, and
  `_match_case_label` / `_enum_member_cpp` render the member labels. Checked
  2026-08-29 at `fbfbb5b0d`.]
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
  [The first of the three LANDED: `_coerce_disposition` grew an `own_slot_arg`
  flag (`thir/lower/predicates.py`) and a view-typed str source at an `Own[str]`
  arg slot lowers to a brace-init moving `THIRArgTemp` (face `argtemp.own_str`,
  `thir/lower/expressions.py`). The other two were not re-checked. Checked
  2026-08-29 at `fbfbb5b0d`.]
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
  [`bytearray` is no longer an untouched axis: `is_bytearray_type` is threaded
  through the ordinary container family in `thir/lower/{predicates,checks,
  statements}.py` (binop operands, coercions, container literals, subscripts,
  aug-assign), with dedicated routing pins across `test_thir_bytes.py`,
  `test_thir_containers.py`, `test_thir_ctor.py` and several wave files. Narrow
  bytearray shapes still stay AST (e.g. an `Own[bytearray]` param read, a
  field-from-slice write). The **String** row above IS still true --
  `_resolved_str_value`'s docstring records that the `const std::string&` param
  slot is spelled by the skeleton and "no body arm renders it". Checked
  2026-08-29 at `fbfbb5b0d`.]
- F2 carry-over deferred cells (container locals, cross-module/native/generic
  records, subscript sources, narrowing-needed `->` deref, name-alias/REF_ALIAS
  reseat sources): **blocked-on-F3+** or the relevant frontier; see the F1/F2 TODO
  cell (items C-G).

### Statement / expression shapes -- **AXIS OPENED (increment 25), STILL MOSTLY UNCOVERED**
The form ladder and callable axis are tracked; the statement-shape axis was the
untracked gap and is now being enumerated + driven.

**[The heading's "STILL MOSTLY UNCOVERED" is a 2026-07 status and is no longer
true.]** This whole section is the incremental record of that axis being opened;
it was never rewritten as rows landed, so it reads as a to-do list when it is a
diary. Every rung it names as pending has since been worked -- the user case dial
is saturated and stdlib fallback is at ZERO. Read it as history; for what is
actually uncovered, use the re-measured cutover checklist in the Gate D3 entry
and the live stdlib gate.
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
(dead-branch elimination). [The FIRST of the three landed: `THIRValueSelect`
(`thir/nodes.py`, documented as "`_gen_logical_value`'s value slice") is built by
`_lower_value_select` in `thir/lower/expressions.py`, admitting scalar / float /
BigInt / str results and container/record results via `_lower_container_select`.
The other two were not re-checked. Checked 2026-08-29 at `fbfbb5b0d`.] The incr-36 byte-diff also exposed and closed a latent compare
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
str/bytes-key dicts, `dict.items()`/tuple-unpack [both landed: `_dict_view_iterable_ok`
in `thir/lower/predicates.py`, consumed by the for-head and comprehension paths,
and `THIRTupleUnpack` for the unpack targets -- the completion model's own bullet
already lists dict-view + tuple-unpack iteration as incr 69; checked 2026-08-29],
non-name iterables (str-family FIELDS off
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
[LANDED since, on both sides, and the marker list is void -- the repo has
ZERO `no_thir.txt` files as of 2026-08-29. The sync dispatch lowers the stamp
through `_finally_deferred_recipe` (`ret.finally_deferred`) and the resumable
side through the leaf finally bridge (`res.leaf_deferred_capture`). The
`return.finally_deferred_capture` reject SURVIVES, but narrowed: it now fires
only for a stamped return outside the two mirrored recipes. Checked at
`fbfbb5b0d`.]
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
[The rung LANDED and so did M4c -- see the wave-next10 entry below
("M4c wrapper match (decision 17)"): `switch_union` admits value-repr
non-generic wrapper NAME subjects, threading `.value` through the switch
head and the `std::get` positions, and `test_thir_wave_wrapper_match.py`
pins the slice. Remaining boundary per that file: guarded matches, field
subjects, non-ctor decl inits.]
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
subjects (cross-axis, see above). [M4c is no longer parked -- it landed
in wave-next10; see the bracketed note at the M4c paragraph above.]
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

**[This list and its Action are CONSUMED -- do not work from them.]** Spot-checked
2026-08-29 at `fbfbb5b0d`: `async`/`await` and `yield`/generators lower through
`thir/lower/resumable.py` + `simple_gen.py`; comprehensions and generator
iterables through `thir/lower/comprehensions.py` (genexpr included); `nonlocal`
routes as a no-code face in `thir/lower/statements.py`; the non-simple-intermediate
chained compare has its own arm (`chained_compare.stmt_expr` in
`thir/lower/expressions.py`); and Optional dotted-field narrowing has a landed
read path rather than being a blanket fallback. The parked match tail is
separately corrected above (M4c landed). The Action's own criterion is met from
the other side: the dial is saturated, so this axis no longer selects work.

## Sequencing discipline (the plan)

**[Read 1-4 as history, 5-6 as standing policy (2026-08-29).]** Items 1-4 are a
SELECTION strategy for a phase that is over: the case dial is saturated and can
no longer select work, and the residual deferrals that remain are individually
tracked rather than parked against rungs. What sequences the remaining work is
the re-measured cutover checklist in the Gate D3 entry and the live stdlib
fallback gate. Items 5 (the two AST-bug policies, by output well-formedness) and
6 (one shared function computes any fact the gate and lowering must agree on)
are invariants, not sequencing, and both still bind.

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
- **Landed: body-authored diagnostics re-homed, and the site instrument
  corrected** (2026-08-31, branch thir-rehome-body-diagnostics; suite 13425
  green, 3763 cases full exec, dial 3766/3766, one existing snapshot
  regenerated for a message change).

  **Name the key before quoting a figure -- three exist here and they are
  not interchangeable.** K1 = total BODY sites; K2 = BODY + deliberate
  (user-facing message shape); K3 = BODY + deliberate + witnessed by a
  committed diagnostic. On the CORRECTED instrument, base -> tip is
  K1 42 -> 40, K2 8 -> 6, K3 3 -> 1. An "8 -> 1" figure was stated during
  this work and is WRONG: it pairs K2's start with K3's end. Recorded
  rather than deleted, because the same two-keys-one-number error has now
  been made on the stdlib dial and here.

  The instrument was itself over-counting: the uncorrected script read
  base as K1 45. Its dead-set seed treated a function as dying when all
  its frozen gate dispositions were AST_ARM and it hosted a raise. But a
  disposition is a statement about ONE CALL, not about the enclosing
  function, and a frame emitter carries its routing check INSIDE the body
  it emits -- selecting between two renderers -- so it runs for every
  body, routed or not, and the raises it hosts outside that arm survive.
  Verified by instrumenting rather than by reading tags: the raises fire
  with the leaf BOUND. THREE sites moved BODY -> SKELETON from the rule
  itself: one directly, plus two entailed by reachability closure once
  their only caller stopped being falsely dead. A FOURTH site moved on
  this branch for an unrelated reason -- a bookkeeping entry naming it was
  removed when its diagnostic changed author -- and attributing it to the
  rule was itself a miscount, made in the paragraph that exists to record
  a miscount. The docstring's "BODY is a lower bound, never an upper one"
  was false BECAUSE of that clause; removing it makes the claim true.

  Of the eight K2 sites the scoping pass examined, four were already
  DEAD CODE -- guards mutually exclusive by construction, a total sema
  dispatch upstream, an identical sema raise firing first, and a loop
  guard asserting exactly the disjunction its raise tests. They still
  COUNT in K2 until the modules go, which is the gap between "sites
  needing work" and "sites the instrument reports". A fifth was never a
  blocker (skeleton, running before the routing decision).

  `match.py::_variant_index` is now DEAD FROM USER SOURCE, not merely
  unwitnessed: a 14-program adversarial sweep recorded 50 AST-path scans
  with zero misses, every non-member arm being rejected by sema first.
  Methodological note worth more than the verdict -- the same sweep run
  under routed codegen records ZERO scans, because the routed path uses
  THIR's own index lookup, so it would have "confirmed" any hypothesis
  put to it. The AST-path run is the one carrying evidence.

  It nonetheless still COUNTS in K2, because that key is keyed on a
  message's SHAPE -- a user-facing sentence rather than an `internal:`
  invariant -- and not on whether any program can reach it. Phrased as
  the invariant check it has become, K2 would read 5. Left as it is
  rather than re-phrased mid-count, but a reader comparing K2 against a
  list of reachable diagnostics should expect exactly this one to be
  absent from the latter.

  What remains in K2 after this: one architectural item, the
  suspension-inside-a-dynamic-dispatched-match rejection, which is layered
  behind two earlier resumable fences and partly blocked on how the
  cutover writes the leaf seam.

  **A second failure class sits outside this count entirely and is not
  measured by anything.** The countdown tracks diagnostics that stop
  existing; it is blind to CORRECT code that loses its only emitter. Two
  concrete witnesses surfaced here: an `async def` with a `@dynamic`
  protocol param and no match at all, which the AST compiles and runs
  correctly today, and a simple-generator condition whose temp comes from
  a variant-union argument. Both fold, so no case can carry them without
  failing the ratchet, and the ratchet only runs past codegen success.
  These want an ablation sweep of the fence families, separate from the
  diagnostics countdown.
- **Landed: the cutover gate's OPEN set emptied** (2026-08-31, branch
  thir-discharge-gen-async-open; gate OPEN 4 -> 0, dial unchanged at
  3765/3765, suite 13423 green with full exec, zero snapshots regenerated).
  All four survivors were in `gen_async.py`. Two lanes:
  (1) The resumable return scaffolding (`_make_async_return`,
  `_make_generator_resumable_return`, the leaf-dispatch half of
  `_async_return_value_cpp`) moved out of the dying `statements.py` into the
  surviving `gen_async.py`, and the finally-deferred return recipe DECISION
  moved from AST emit time to lowering time -- a new `_resumable_deferred_recipe`
  rejects the whole body for a stamped shape the mirror cannot spell, which
  is available at lowering and is not available at the hook, where routing is
  already committed. The sync arm had made exactly this choice already; the
  resumable arm had opted out. New seam `ResumableLeafEmitter.render_deferred_return`;
  the sync emit and the seam were unified onto one `_deferred_return_triple`.
  The AST recipe and its emit-time retraction of `all_last_uses` stay behind
  in the dying module, now `leaf is None`-guarded.
  (2) `_extra_template_args_for_await` moved from CFG-build time to struct-emit
  time and now spells the sub-coro capture type from the EMPLACE render rather
  than a bare `gen_expr`, then routes through the existing emplace-argument
  seam. That repaired TWO pre-existing wrong-code defects nobody had looked
  for -- a narrowed `Optional` argument and an `Own[<static protocol>]`
  argument each made the frame field's deduced type disagree with what the
  emplace passed, ill-formed C++ on valid Python CPython runs. Both were
  invisible because all nine corpus cases render the two spellings
  identically; the equality now holds by construction, through one chokepoint.
  **Neither defect could be covered by a corpus case, for DIFFERENT reasons
  -- do not collapse them.** The `Own[<static protocol>]` shape has no
  lowering at all (the static-protocol resumable param rung), so its case
  falls back, and with zero markers a falling-back case fails the ratchet.
  The narrowed-`Optional` shape is not blocked by any rung: its case fails
  the BYTE-DIFF, which no marker exempts. Coverage is therefore units plus
  manual build probes, and the two shapes unblock independently.
  A cutover consequence of the first: once the fallback is gone, an
  `Own[<static protocol>]` coro param turns valid Python into an internal
  error rather than a diagnostic, so `AST_ONLY_DIAGNOSTICS` will not flag it.
  **One thing the deletion must now also strip, and no gate reports it:**
  `gen_async.py` -- which SURVIVES -- imports `tpyc/move_audit.py`, which the
  cutover DELETES, for the suppression around the discarded capture render.
  Before this wave the only `codegen_cpp` importer of it was `expressions.py`,
  which dies anyway. The reverse-import gate cannot see this: it is scoped to
  the four body emitters, not to the detector modules that go with them. The
  two go together -- the audit is a dual-path join and is meaningless once
  there is one author -- so the deletion removes the import and its two calls
  along with the module, but it has to know to.
  Also surfaced and filed, not fixed: a `return <value>` in a NON-suspending
  async finally helper emits into the void helper (THIR mirrors it, so it
  survives the cutover), and a `T | None` local from an `Own[T] | None` call
  binds a pointer into a payload-typed slot (AST-only, so it dies AT the
  cutover -- but adding a case for it before then would bake the miscompile
  into a committed snapshot).
- **Landed: the six-forks batch** (2026-08-21, branch thir-forks-0821; 6
  flips, dial 3627 -> 3633; fallback bodies 387 -> ~381 user; cutover gate
  OPEN 5 -> 4). The 2026-08-20 handoff's six "decision-bound" forks were
  scouted in one parallel batch and EVERY premise shrank on verification --
  three de-designed to ordinary work, one fork measured dead, the largest
  raise site was 5 cases, not 27 (census tag conflated site-touch with the
  fork). What landed:
  (1) match: a non-lvalue subject on the switch scalar tiers copies into
  the dispatch local (`match.scalar_rvalue_subject`), plus the native
  record-rvalue call at the RECEIVER position (`call.native_record_recv` --
  the pascal `deref(s).kind` shape; the scouted "sole blocker" claim was
  wrong, stmt.match masked expr.call). Zeroes the `value_pattern` arm.
  (2) NoneType/monostate at FOUR value slots (the scout found a ctor-MIL
  row the fork description missed): free-call arg, decl slot, field write,
  ctor MIL. All renders pre-existed; four narrow admissions.
  (3) frame nested-def MEMBER bodies lower through the leaf seam
  (`res.nested_def_body`, `THIRResumableBody.nested_def_bodies`,
  `leaf.emit_nested_def_body`): the fork's sub-body-routing concept is NOT
  LIVE -- a 13/13 decision-test probe routed every member body, and the
  marker-standing-in audit found the class has ONE member. Erases the
  8-case dial overstatement; `gen_nested_def_body` reclassified
  OPEN -> AST_ARM (gate 5 -> 4).
  (4) conditional-operand arg temps, core: the eager-only gate
  (`argtemp.cond_eager` / the `argtemp.cond_defer` exit check on the
  conservative would-defer guess) then the D1 deferred render -- THIR emit
  opens the AST's own `TempState.conditional_region` through CtxTempSink
  (byte-identity BY CONSTRUCTION), with `THIRArgTemp.movable` as the
  AUDITED defer fact (None = unaudited keeps rejecting). Four splice
  points: logical BinOp RHS, ValueSelect RHS, IfExpr arms, chained
  stmt-expr operands i>=2. The periphery pair flipped the SAME DAY (the
  user called slice (c) back in): the list-repeat arg row, the
  MUTATED-slot ctor container hoist (const slots keep the inline brace --
  a unit fixture caught the divergence the mutated-only corpus case was
  blind to), the value-select RHS grant, the for-head iterator-ternary
  route, the comp-as-ternary-arm rung (LIST-only), comp elements as a
  flushable position (the degrade relocation IS the per-iteration
  flush_since), Sized/pending/Array-demote in the native comp predicate,
  and the coerce arm riding allow_temps through. Fork 4 total: 5/5 cases.
  (5) `THIRFormConvert.materialize` tri-state (None = the legacy family
  derivation) -- bytearray is the one view-family member whose
  (family, form) pair does not determine the render, so validate.py forces
  an explicit decision on bytearray STORAGE converts; the coerce
  chokepoint's materialize disposition tags True; the bytearray decl slot
  admits ONLY the `bytesview_to_bytearray` coerced-view shape (the
  bytes-param identity coercion keeps rejecting -- its AST oracle is
  wrong-code, filed in BUGS.md; the owned dunder rvalue has no routable
  witness while the bytearray binop arm rejects).
  Alongside on its own AST-first branch (fix-walrus-temp-burn): the
  walrus-elif temp-counter burn fix (`rollback_walrus_probe`), churn
  measured at ONE case / 2 lines against the feared corpus-wide
  renumbering.
  LESSONS: verify-the-premise went 6-for-6 (27 -> 5 cases; the five-case
  bytearray residue list was dead; 13/13 nested defs route; churn 1 case);
  and the scouted "~10 FormConvert construction sites" were 73 -- the
  SECOND grep-standing-in-for-an-instrument miss in two sessions (gate
  item 4 went 2 -> 26 -> 5 the same way): a class, not an anecdote.
  CUTOVER CONSOLIDATION SURFACE left by this branch (fold into the
  printer-layer temp-machinery pass): the standalone TempSink's region
  banking mirrors TempState._close_region verbatim (only unit callers use
  it -- every production seam passes CtxTempSink, so the mirror's sole
  drift guard is test_thir_condtemp); THIRArgTemp.would_defer reaches the
  printer's _slot_spellable through the acknowledged context<->nodes
  cycle; and the conditional-operand concept lives in three encodings
  (the lowering exit check, validate.py's eager_only axis, the emit
  regions).
- **Landed: the shared emit primitives get a home outside the body
  emitters** (2026-08-20; cutover gate OPEN 34 -> 5, no dial movement -- this
  is skeleton work, not a routing wave). `tpyc/codegen_cpp/emit_prims.py`
  holds 25 primitives hoisted out of `statements.py` (-778) and
  `expressions.py` (-102): `setup_body_scope`, `seed_param_locals` and its
  scoped ctor-window wrapper, `promote_movable`, `compute_borrow_tuple_const`,
  `ptr_slot_field_type`, `_get_cpp_declared_type` and the shared predicates.
  Each moved with its body and docstring intact -- only the `self` receiver
  became an explicit `ctx`/`types`/`protocols` parameter -- and the old
  methods were deleted outright, so there is one owner, not a copy.
  WHY it is the piece that re-prices item 4: 31 of the 34 OPEN calls were
  never "route it through THIR" work, they were helpers living in a module
  slated for deletion but called from the layer that SURVIVES. Moving them
  discharges the call without lowering anything.
  `test_cutover_gate.py::test_shared_prims_module_never_names_a_body_emitter`
  keeps the new module from becoming a second body emitter by importing one.
  LESSON: a mechanical extraction this size gets its regression coverage from
  the whole-corpus byte-diff, not from new units -- these helpers had none
  before either, and zero snapshot churn IS the assertion.
- **Landed: bytes view->owned coerce + its field-write row** (2026-08-20;
  fallback bodies 389 -> 387, 0 flips, dial unchanged at 3627/3739). Steered
  by ARM-RESIDUAL rather than the case dial: the cutover deletes AST body
  arms, and an arm at residual N is held alive by N bodies, so the cheapest
  deletions read off the bottom of that list, not off the case markers.
  `bytesview_to_bytes` had no `_coerce_disposition` entry, so every position
  holding one fell back; its lambda is an unconditional `bytes_copy({0})` --
  the `strview_to_string` shape -- so it now materializes into the S6
  view->owned FormConvert everywhere below the `Own[...]` reject. With the
  coerce routable the bytes field-write family took the SLICE row its str
  twin already had, plus the shared unbound-self receiver OR.
  LESSONS: (1) the fence docstring was RIGHT -- it said a bytes slice
  "rejects deeper at its coerce", and widening the admission alone moved the
  reject from `assign.field_write_shape` to `expr.coerce` exactly as
  written; read the fence's stated reason as a chain hint, not an obstacle.
  (2) A leg that ablates to no effect on the drilled case can still be
  load-bearing: the receiver OR looked dead (both probe shapes routed
  without it) until a DISCRIMINATING case -- `Base.b = ...` from a derived
  method, the ancestor-subobject spelling -- dropped alone. (3) The
  per-body arm map must cover PLUGIN-frontend cases: a first pass keyed on
  `src/main.py` silently skipped the four pascal cases and read
  `value_pattern` as cleared when its sole body is `pascal/variant_record`.
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
    the detector itself, and is filed in TODO.md. [FIXED 2026-07-28 in
    `ca57e429d`: witnesses are journalled per lowering attempt and rolled back
    when the body falls back. The `15 open` figure above is also pre-fix and
    INCOMPARABLE with any later one -- 2026-08-29 reads 69 zero-witness of
    1435.]
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

  **The hole is CLOSED as of 2026-08-25** (see the 2026-08-25 entry at the
  end of this file). `--thir-stdlib` supplied the oracle from 2026-07-28 on,
  but as an opt-in flag with no CI row it was not a gate and it silently
  rotted back to 49 failing cases. There are now two: the always-on
  `tests/test_thir_stdlib_gate.py` (import-only floor, every plain pytest)
  and a `thir-stdlib` nightly row (the wide corpus form). The FLOOR caveat
  above is also retired -- see the same entry for the measurement.

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
  [Understated as of 2026-08-29: "the one thing lost" names only the byte-diff,
  but cutover deletes **three** cross-path detectors. `tpyc/move_audit.py` and
  `tpyc/binding_audit.py` both landed after this entry was written; each is a
  dual-path JOIN whose AST-side recorder lives inside the deleted emitter, and
  `move_audit`'s own docstring calls itself the ONLY detector for its divergence
  class (a wrong move verdict at a site whose render ignores it emits identical
  C++). Neither is covered by "exec + cpy". Re-decide D4's user half against
  three detectors, not one -- see cutover-checklist item 8.]

  **RE-DECIDED 2026-09-01 against all three, and the answer does not change:
  ACCEPT the loss; build no successor.** The reasoning, which is the part
  worth keeping rather than the verdict: what the two audits catch is a fact
  that is wrong, harmless TODAY, and harmful only once some future render
  starts consulting it -- and at that moment the emitted C++ changes, so the
  ordinary snapshot check catches it. The single path where a wrong verdict
  escapes is a change that adds the consuming render AND regenerates the
  snapshot in the same commit. That is not a new exposure: it is exactly the
  "a wrongly-regenerated snapshot is self-consistent" risk this entry already
  examined and accepted, with snapshot-churn approval as the compensating
  control. Three detectors instead of one widens the surface, not the class.
  Building single-path invariants for facts currently checked differentially
  would be real design work and would delay the cutover for a risk already
  accepted in its general form.
  **The one thing to actually do: run both audits across the full corpus in
  the commit before the deletion and record the joined-node counts here.** It
  buys no ongoing protection -- it pins that the verdicts agreed at the moment
  the second author was removed, so a later suspicion has a baseline to argue
  from instead of nothing. It rides a suite run that has to happen anyway.
  **BASELINE, measured 2026-09-01 on a green full suite (13436 passed, 23
  skipped) at the tree that empties the known-unmigrated set: the move join
  reports 0 divergences over 1012 JOINED NODES, and the binding join 0 gaps
  over 11958 JOINED BODIES.** Name the key when quoting these: nodes and
  bodies are different denominators and neither is a case count. An earlier
  figure of 1010 joined nodes elsewhere in the tree is a different tree, not
  a contradiction -- the join count moves with the corpus. Re-measure at the
  deletion commit rather than carrying these forward; they are recorded now
  because this is the run that had to happen anyway, not because they are
  final.
  Do NOT read this as a decision that the audits were low value: `move_audit`
  caught a wrong-code move at a position the byte-diff could not see, which is
  why it exists. The decision is that its class is bounded by the snapshot
  regime once there is one author, not that it never mattered.

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
  [Superseded within its own entry: the paragraph above records the journal +
  rollback fix LANDING on 2026-07-28 (`ca57e429d`), and this paragraph -- drafted
  against the pre-fix tree -- then asserts the defect as live 40 lines later. The
  defect is FIXED; witnesses are journalled per lowering attempt and rolled back
  when the body falls back. The residual hazards are different ones, enumerated
  in cutover-checklist item 5: 69 zero-witness faces of 1435, the census having
  no reader that survives cutover, the attempt-seam-less `lower_module` entry,
  and the ~224 faces whose witness fires at a GATE rather than at the render.]

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
  [Re-measured 2026-08-29 at `fbfbb5b0d`: **this inventory is stale in every
  number and missing about half the surface.** It predates the stdlib gate, the
  committed cutover gate, both audit modules (`move_audit.py`,
  `binding_audit.py`), the migration scripts and two CI rows. The marker count
  is 0, not 958. Two of the three SURVIVES entries are misleading rather than
  wrong: `faces.py` survives but its only READER (`conftest.py`) is on the DIES
  list, so the census would survive unread; and the "ONLY internal net" framing
  omits that cutover deletes THREE cross-path detectors -- the corpus byte-diff
  plus `move_audit.py` and `binding_audit.py`, both dual-path joins whose
  AST-side recorders live inside the deleted emitter. Do not execute from this
  list; rewrite it first. See cutover-checklist items 5, 7 and 8.]

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
pruning instead of archaeology. [That gate statement is one-directional and
therefore not sufficient on its own: THIR itself imports FROM the four doomed
body-emitter modules at 8 production sites, and the committed scan does not
look that way. See checklist item 4 below.]

**The cutover checklist, assembled in one place.** RE-MEASURED 2026-08-29 at
`fbfbb5b0d`; the previous version of this block is superseded, and four of its
eight items were wrong in ways that would have mis-scoped the endgame. Every
figure below names how it was derived. **Treat any number here as valid only
for the tree it names** -- that rule is what this re-measurement exists to
enforce, since the last version carried a stale figure in every item that had
one. **And an item states its OWN status only.** A cross-referencing status
claim ("item N moved too", "every other item still reads as it did") is stale
the moment a neighbour changes, because nothing enforces it; one such sentence
here was wrong three times in three different ways before it was deleted. Each
item carries its own date and its own derivation, so a reader gets the answer
by reading the item, never by trusting a sibling's summary of it.

1. **A1: `no_thir.txt` markers -> 0. DONE.** Zero repo-wide (`tests/cases` and
   `tests/interop` both). Nothing left here.
2. **A5: stdlib fallback -> 0. MET 2026-08-30** -- 0 bodies, 0 name-collapsed,
   1245 routed over 2843 classified. `tests/test_thir_stdlib_gate.py` IS the
   number and its ceiling is armed at zero; re-run it, do not copy it. Count
   BODIES, not distinct names, and never subtract figures from the two keys.
   Nothing here selects work any more: a non-zero reading is a regression, not
   a backlog row. The tail that closed A5 had been recorded as decision-bound
   (filed defects, design forks, chained sites); each row's status had been
   read off a reject tag rather than ablated, and all of them were wrong in
   the pessimistic direction.
3. **Interop corpus. DONE, verified per BODY.** 34/34 cases, 285 bodies, zero
   fallback of ANY component, zero markers. The case dial alone does not prove
   this -- its "migrated" test excludes two non-ratchet components -- so it was
   confirmed by an emit-side census that spied the AST body arms directly: 0
   AST-arm body emissions, 0 AST ctor-tail extractions. The CPython glue
   emitter depends only on skeleton modules and needs no porting.
4. **Skeleton call-site inventory: 0 OPEN -- DISCHARGED 2026-08-31.** The scan
   is COMMITTED as `tpyc/codegen_cpp/test_cutover_gate.py`, so this number is
   reproducible rather than re-derived -- which is the fix for the grep that
   once priced this at 2 against a real 26. The count went 34 -> 5 -> 4 -> 0,
   and the test now asserts the OPEN set is EMPTY rather than counting down.
   **Both halves of this item's former framing were wrong, and the framing is
   what misdirected the work that closed it, so it is recorded rather than
   deleted.** It called three of the four "~320 lines of relocation" whose
   "home is wrong rather than whose logic is missing": relocating them moved
   the OPEN entry instead of discharging it, because the finally-deferred
   return recipe is reachable on the routed path and reaches an expression
   render, so the recipe DECISION had to move to lowering time behind a new
   seam. It called the fourth, `_extra_template_args_for_await`, "the only
   genuine routing work": that one turned out to be the better-understood of
   the two, discharged by reusing the existing emplace-argument seam, and it
   was sitting on two wrong-code defects nobody had looked for. The lesson is
   the one this ledger keeps recording: a per-item status assembled from
   reading tags rather than from an ablation errs, and it errs confidently.
   **The reverse direction: DISCHARGED 2026-08-31, and now gated.** THIR
   lowering used to import FROM the four doomed modules -- 7 `import`
   statements across 4 files in `thir/lower/`, reaching 7 names (three
   expression-shape predicates, two literal-fact folds, two render constant
   tables), two of them through `ExpressionGenerator` as a namespace for its
   statics. Deleting the modules would have broken THIR at import time and no
   gate said so. All seven moved to `emit_prims` (plus `_flatten_chain`, which
   only `check_literal_chain` calls), losing the leading underscore that a
   cross-package import made meaningless, and `expressions` / `builtins` /
   `statements` now call them there too, so there is one definition rather
   than a relocated copy. `test_no_layer_outside_the_body_emitters_imports_one`
   scans the whole package for a recurrence and freezes the five modules that
   legitimately still name one of the four -- the composition root plus four
   TYPE_CHECKING annotations, all deleted WITH them. The freeze is
   bidirectional: an entry that stops matching the source fails too. Test
   files are frozen in a SECOND table rather than excluded, because one of the
   three is not a deletion: `thir/testutil.py` holds the routing and
   byte-identity helpers every pin in the suite calls, so it survives and the
   cutover has to EDIT it (only `_constant_positions` is dual-path).
   **Two residues this leaves, both for the deletion commit, neither
   detectable by anything:**
   - **The seven relocated names have no skeleton consumer.** Measured: every
     caller is one of the four doomed modules or `thir/lower/`. Post-cutover
     `emit_prims` holds ~200 lines whose only consumer is `thir/lower/`, in
     the printer package. Their end home is `tpyc/thir/`; moving them there
     today would have made the dying modules import from `thir`, which is
     worse, so this is deliberate but temporary. `emit_prims`' docstring says
     so at the section.
   - **~1151 prose lines under `tpyc/thir/**` (non-test) name a symbol
     defined ONLY in one of the four modules** -- NAME THE KEY: lines whose
     text matches one of the 734 symbols exclusive to those modules, counted
     2026-08-31; 158 of them say "Mirror". These are mirror references, not
     imports, so they block nothing; they become dangling pointers to deleted
     code the moment the cutover lands. The set is far too large to sweep in
     the deletion commit, so the cutover has to STATE a policy (rewrite the
     text to name the invariant, or accept the dangling references) rather
     than discover the size. A first estimate of this put it at ~15 by
     grepping four CLASS names instead of the symbol population -- the
     recorded probe-a-subset failure again, off by two orders of magnitude.
5. **The faces detector. The increment-site defect is ALREADY FIXED** (witnesses
   are journalled per attempt and rolled back on fallback). Three records
   asserted otherwise, one of them written three days after the fix; they are
   corrected. What remains is different and larger:
   - **69 zero-witness faces of 1435, not nine.** "Nine" was a delta against a
     711-face registry, never a total.
   - 46 of the 69 ARE witnessed, by the stdlib sweep, which does not fold its
     witnesses into the census. Folding it drops the list to 23. Of those, 11
     have no unit-test mention at all -- the dangerous class.
   - **The census survives and its only reader does not.** `faces.py` is on the
     SURVIVES list; the code that reports it is in `conftest.py`, on the DIES
     list, behind an opt-in flag. Nothing ratchets it: no test asserts on the
     count, no nightly row runs it. A detector nobody runs is the failure this
     item was written to prevent, and it was not written down anywhere.
   - One lowering entry (`lower_module`, the unit-test path) has no attempt
     seam, so 694 witnesses per suite run escape rollback. Measured blast
     radius of fixing it: zero test changes.
   - For ~224 faces the witness fires at a GATE, so "witnessed" means a row
     admitted, not that the named render ran. Post-cutover a reader will assume
     the stronger meaning. That semantics needs writing down.
6. **The two-commit cutover per Gate D4.**
   **TIME-BOXED PREREQUISITE, and it is unrecoverable if missed: run a large
   adversarial `dualgen` sweep in the commit BEFORE the deletion.** `dualgen`
   needs two authors, so it dies permanently at commit 2 and section C
   records that no successor exists. It is the only instrument that has ever
   caught the out-of-corpus AST/THIR divergence class -- the one the
   acceptance record (section H, near the end of this file) prices as the
   flip's real residual risk. Everything else on this checklist can be done
   after the fact; this cannot. Stated here as well as in section H because
   this checklist is what a reader is pointed at for cutover readiness.
   **RUN 2026-09-01, and it paid: 162 adversarial probes over three integer
   widths, 4 divergences, 39 fallbacks (at Int32), 16 filed BUGS entries.**
   Every figure here names its key. The entry count has been wrong FOUR times -- 9 on an unnamed key,
   then 13, then 15, each correction written in the same commit that filed
   more entries and never re-derived against them. Derive it from BUGS.md at
   the moment of writing, or do not state it. Full
   result in TODO.md, including the 39 shapes written out; neither the probes
   nor their batch runner were kept, both having been second copies of things
   that already exist. One of the four appears at BigInt ONLY -- the first option-gated
   AST/THIR divergence anyone has caught, and the reason a single-width sweep
   would have been a weaker instrument than it looked. The divergences
   are the class this prerequisite exists for, and THIR is the better author
   wherever the two disagree observably -- but the number that should change
   a reader's expectations
   is the 39: nearly a quarter of hand-written probes hit a routing gap in a
   corpus that reads 3767/3767 with zero fallback. The dial measures the
   corpus, not the language, and this is the first measurement that separates
   the two. Rerun before the deletion if the tree has moved much since, with
   `dualgen.py` over fresh probes: pass it every integer width (it takes them
   as trailing arguments and DEFAULTS TO Int32 alone, so the default is the
   mistake -- the BigInt-only divergence is why), and build what it says is
   identical, since several of the filed entries are programs both paths agreed
   on and neither can compile. (No count: the two attempts to put one here were
   both wrong, and the advice does not need one.) `dualgen.py` has no build stage of its own, so
   that second half is a manual `tpy` run per probe.
   **DECIDED 2026-09-02: flip, then delete, then fix rejects as they
   surface; two pre-flip crash fixes; ordered steps in
   `docs/THIR_CUTOVER_REVIEW.md` Phase 0.**
   Mechanically ready otherwise -- the corpus
   dial is saturated, so THIR can author every snapshot. The blockers:
   - **Error cases had never been lowered through THIR at all. A DETECTOR now
     exists (2026-08-30); the re-homing work does not.** The overlay runs after
     codegen SUCCEEDS, so every case that fails at codegen was invisible to the
     dial, the ratchet, the byte-diff and both audits. `compile_with_diagnostics`
     now re-emits with THIR on whenever the AST run raises `CodeGenError` and
     requires the SAME diagnostic
     (`tests/conftest.py::_assert_thir_raises_too`); a body THIR rejects passes,
     because the AST re-emits it and raises. Measured over the whole corpus at
     that commit: of 1570 `error_*` cases only **30 reach codegen**; 20 raise
     from the SKELETON, 8 pass only via a THIR body reject, and **2 were
     already wrong** -- THIR routed them, emitted code and raised nothing (one a
     use-after-scope with observed wrong output, one ill-formed C++). Both are
     `tests/cases/generators/error_gen_rebind_slot_*`; the cause was
     `_rejects_lambda_hoist` walking for `THIRNestedDef` only while the
     simple-generator peephole is lambda-rendered too. Fixed at the root by
     generalizing it to `cross_scope_rebind_site(outer, inner)` and applying
     it at the sgen seam. `tpyc/thir/` still contains zero `raise CodeGenError`,
     so every one of these passes by FALLBACK -- the diagnostics still have to be
     re-homed before the emitters are deleted, and the detector only guarantees
     nothing regresses meanwhile. 33 further raise sites in those files have no
     case witnessing them.
     **The gate is green but NOT cutover-safe, and the blocker is countable:
     those 8.** Each passes only because THIR rejects the body and the AST
     re-emits and raises; commit 2 deletes that re-emit, so on that commit all
     8 stop being diagnostics and become ICEs on valid-to-reject source. Count
     them down to zero as the diagnostics are re-homed -- 8 is the number to
     re-measure, not a caveat.
     **RE-MEASURED 2026-08-30: it was 10 of the ones the CORPUS REACHES, not
     8, and 9 are now re-homed.** Say the qualifier every time -- the method
     enumerates `error_*` cases, so it is blind by construction to a
     diagnostic no case reaches, and a probe found one the same day. The
     population-complete twin is the static site inventory
     (`scripts/thir_migration/thir_diagnostic_sites.py`), which counts raise
     SITES rather than cases and goes to zero when the four modules are
     deleted -- that, not the case count, is the definition of done. The
     30/20 split held exactly; the bucket boundary did not. The two extra rows
     are the `records/error_nocopy_del_field_*` pair, whose raise sits in the
     ctor member-init extraction -- textually inside a module the cutover
     KEEPS, but the ctor lowering returns before it, so it is body code. The
     figure was re-derived two ways that agreed: a sweep compiling all 1570
     `error_*` cases and joining reject to raise on the same live stack frame,
     and a static call-graph trace from all 78 `raise CodeGenError` sites.
     A ratchet now carries the verdict (`tests/conftest.py::AST_ONLY_DIAGNOSTICS`),
     because matching the diagnostic TEXT cannot distinguish a re-homed
     diagnostic from a re-parked one -- the AST re-emit produces identical
     text either way. **It must classify by CALLER and must hold skeleton
     diagnostics apart**: a first cut asking only "did THIR raise it" flagged
     all 20 skeleton rows, which need no work at all.
   - **FOUR body diagnostics, not three -- and the fourth was missed by a
     file-based inventory.** `context.py::use_rebind_slot` (the cross-scope
     rebind-slot reject) lives in `codegen_cpp/context.py`, a file the cutover
     KEEPS, but every one of its callers is in a file the cutover DELETES
     (`statements.py` x7, `expressions.py` x1). **Lesson: inventory raise sites
     by CALLER, not by file.** A diagnostic is a body concern when the bodies
     reach it, regardless of which module spells the `raise`.
     **SUPERSEDED 2026-08-30: the full inventory is 78 raise sites, 42 BODY /
     36 SKELETON, of which 12 are BODY and deliberate.** That reading came
     from a throwaway tool; the committed one
     (`scripts/thir_migration/thir_diagnostic_sites.py`) refines it to
     37 BODY / 29 SKELETON / 12 BOTH, of which 8 are BODY and deliberate --
     and it RECONCILES rather than contradicts (37 + the 5 re-homed = 42;
     29 + the other 7 BOTH = 36). The BOTH bucket is the difference: the
     first tool restricted callers to `codegen_cpp/` and so could not see a
     THIR caller. Re-run the script rather than trusting either figure, and
     note that the five re-homed diagnostics classifying BOTH is the
     instrument SEEING the re-homing -- reporting them as BODY would say it
     had not happened. **7 BODY+deliberate sites have no witnessing case**,
     which is the list this work needed and did not have; three of them have
     since been probed unreachable (two `_resolve_cpp_type` rows, one
     `@overload`-specialized match row traced dead through its caller
     guards) and one reads as an internal assertion, so the live residue is
     small -- but derive it from a fresh run, not from this sentence.
     Five of the twelve
     are load-bearing (a committed case witnesses them); the rest are
     unwitnessed. Four unwitnessed ones were probed and turned out
     unreachable -- sema rejects those shapes earlier with a better
     diagnostic.
     **That was FOUR probes, and an earlier draft of this line generalized
     them into a claim about all seven. A fifth probe falsified it the same
     day**: an `await` inside a `match` on a `@dynamic` subject -- ordinary
     Python, no corpus case -- reaches a deliberate diagnostic in `match.py`
     with no THIR mirror anywhere. It is now a committed case and a second
     `AST_ONLY_DIAGNOSTICS` entry. The ratchet going UP was the right
     outcome: it recorded a gap that had been invisible, and the population
     it tracks is smaller than the one the static inventory sees, so agreement
     between them is not evidence.
     **A witness is an ablation; a deliberate-LOOKING message is a
     hypothesis -- and so is "probed, therefore unreachable" until the probe
     exists.** Four more raise-in-a-surviving-file rows exist beyond
     `use_rebind_slot` (three in the ctor base-init extraction, one in the
     simple-generator while-cond), so that shape is a family, not an
     exception. Also of note: 20 further BODY sites wear an internal-shaped
     message (`Unsupported <thing>: {type(pattern).__name__}`) over real TPy
     pattern nodes, all in `match.py`. Their reachability is UNESTABLISHED --
     two constructed witnesses for them were both falsified -- so they are
     neither safely demoted to internal errors nor safely budgeted as work.
     **MEASURED 2026-09-01, and the QUESTION was wrong before the answer
     was.** Reachability of an AST raise site is not what makes the deletion
     unsafe. Delete a site whose shape THIR also refuses and a diagnostic
     merely changes; delete one whose shape THIR can lower and the rejection
     was the defect. It is dangerous in exactly one direction: a shape the
     AST REFUSES and THIR RENDERS, where the refusal disappears and the
     program compiles to unreviewed output. That asymmetry is directly
     testable and does not require establishing reachability at all -- and it
     cannot be faked by a fallback, since a fallback re-emits through the AST
     and would raise too, so THIR succeeding where the AST raises means THIR
     ROUTED.
     **672 generated programs over the tier-selection matrix: ZERO
     asymmetries, in either direction** -- re-derivable, which an earlier
     version of this entry was not: the instrument is
     `scripts/thir_migration/asym/check_asym.py` and it GENERATES its probes,
     so the claim is a command rather than a number somebody wrote down. Two
     rounds -- every pattern kind
     against every subject kind with and without a guard (480), then
     multi-arm programs carrying enough homogeneous literal arms to actually
     SELECT the switch tiers with one odd arm spliced at each position (192).
     The second round exists because the first probed the STRING tier without
     entering it -- that one is arm-count gated, and four arms sat below the
     threshold of five. **It is the only one that was**: the primitive, enum
     and union switches are selected from the subject TYPE alone, so the
     single-arm round already entered those three, and the rationale
     originally given for the whole second round held for one family of four. Verified by grepping the emitted C++ for real
     `switch` statements rather than trusting the intent.
     **The first run of this matrix ALSO recorded three probes where THIR fell
     back, and reported them as agreement** -- the field was collected and
     never printed, so "120 emitted identically on both paths" was true and
     misleading, three of the 120 being identical only because the AST emitted
     them twice. Two distinct shapes, now in TODO.md's queue. The instrument
     buckets a fallback separately and prints it; counting one as agreement is
     the single reading that makes a dual-path diff worthless, and it is the
     same mistake the corpus ratchet exists to prevent one level up.
     **The negative result has a mechanism, which is what makes it worth
     something: SEMA gates the whole class.** 552 of the 672 were refused
     before either emitter ran, every one a located `SemanticError`
     **about the pattern or the subject -- which took a fix to become true.**
     An earlier run had 74 of those 552 refused as UNDEFINED NAMES, because a
     subject was generated with only its own preamble and an arm naming
     another type resolved to nothing: 74 crossings counted as "sema gated the
     pattern class" that were never probed at all. Every subject now carries
     every preamble, the name errors are zero, and the totals are unchanged --
     the 74 came back as genuine tier refusals instead ("int literal pattern
     not valid for subject type", "None literal pattern requires an Optional
     subject"). **The coincidence is worth flagging**: a headline that does not
     move is not evidence a fix did nothing, and here the denominator was wrong
     while the number was right. The codegen sites sit behind that
     check as defence in depth, which `_variant_index`'s own docstring
     already said of itself. The other 120 emitted on both paths -- 113 identically, and 7 only
     because THIR fell back, which is not the same thing.
     **The controls are the load-bearing half**, per the prober that once
     reported zero folds everywhere including on a positive control: the five
     committed cases known to refuse AT CODEGEN all report both-refuse, so
     the detector demonstrably sees a refusal when one exists -- and THIR
     raises for all five, which is the re-homing working.
     **NAME THE POPULATION, because three different ones are in play in this
     one bullet and an earlier draft ran them together.** The instrument
     reports 39 BODY sites, of which 34 are internal-shaped (29 in `match.py`,
     2 in `records.py`, 3 in `statements.py`) and 5 deliberate; `match.py`
     holds 32 BODY sites in all. The paragraph this note follows is about a
     fourth figure -- the sites in `match.py` wearing the specific
     `Unsupported <thing>: {type(pattern).__name__}` message, which is 20 and
     is STILL exactly 20. **An earlier version of this very bullet said that
     figure "reads 29 today", which was the population conflation the bullet
     exists to warn about, committed inside it**: 29 is every `match.py` BODY
     site the instrument calls internal, a broader set adding nine the "20"
     never named (two field-value sub-pattern asserts, an unsupported-literal
     raise carrying no `__name__`, two field-pattern guards, three lowercase
     or-pattern-alternative raises, and an unresolved-class-pattern guard on
     the union switch path). The figure did not drift; the
     sentence swapped sets. None of those is "the
     34", and the asymmetry result above is scoped to none of them: it is
     scoped to match SHAPES, which is why it needs no reachability count.
     This does NOT prove any of those sets unreachable; it makes three
     independent attempts that failed for an understood reason. Going
     further means hand-reading `match.py`'s internal-shaped guards for
     shapes sema admits. Nothing here has a closing window -- both emitters
     exist until the deletion -- so it is available afterwards on the same
     terms.
   - **A `match`-defect branch moved the ratchet the WRONG WAY first, and the
     correction is the reusable lesson (2026-08-30).** Converting a bare
     `assert` into a real diagnostic stops an ICE -- but putting that
     diagnostic in a module the cutover DELETES buys the fix with new
     AST-only debt, and the ratchet duly grew. Two of the three entries that
     branch added were pre-existing reality a triage newly WITNESSED, which
     is the instrument working; the third was self-inflicted and was moved
     into sema before merge, taking the entry back out. **Ask where a new
     diagnostic should LIVE, not only whether it should exist** -- the same
     branch's enum and primitive rejections were already decided in sema, so
     the union one was the inconsistent case and its own sibling docstrings
     said so. A diagnostic decidable in sema belongs there: same message,
     survives the cutover, and lowering never sees the body.
   - **A NEW risk class the re-homing introduces: lowering can now reject by
     RAISING.** Before it, a too-broad THIR predicate cost a fallback --
     invisible and safe, because the AST re-emitted the body. Now five
     predicates raise `CodeGenError` directly, so a too-broad one REJECTS
     VALID CODE, and neither the byte-diff nor the ratchet can see it because
     raising IS the outcome they observe. The invariant, which two of the five
     sites already argue individually and which should be stated once
     branch-wide: **a lowering arm may raise only where its predicate is
     provably equal to the AST's; anywhere it is merely close, it must keep
     falling back.** The nested-def cross-scope rebind is the worked example
     of the second case -- it deliberately does NOT raise, because its
     predicate is broader than the AST's raise condition.
   - **`emit_prims` is a way-station for the five pattern predicates moved out
     of `match.py`** (subject-is-lvalue, the optional-case partition, the two
     field-condition walks, the bare-reference test) -- NOT the five reject
     helpers, which belong there permanently.
     Moving them out of the dying `match.py` was right -- THIR importing a
     module the cutover deletes is the gate's own documented limit -- but
     after cutover they are AST-pattern predicates sitting in the permanent
     PRINTER layer with callers only in `thir/lower/`. Move them into THIR
     when the emitters go, in the same pass as the docstring restatement
     below.
   - **Deletion chore the re-homing leaves behind.** Three THIR functions now
     mirror a verdict whose AST twin the cutover deletes: `_overload_adjusted_return`
     and, in the ctor lowering, `_ast_demotes_init` and `_ctor_demote_reason`.
     Unifying them now would put a shared helper in the permanent home with
     exactly one caller left after the cutover, so the mirrors are deliberate
     and temporary. What must not survive is their DOCSTRINGS: each names the
     AST symbol it mirrors, and those symbols are scheduled for deletion. When
     the emitters go, restate each docstring as the invariant it enforces
     rather than as a reference to a function that no longer exists. Until
     then the mirrors are instrumented -- all three feed diagnostic TEXT, and
     the error-path gate fails on any text mismatch for a covered shape.
   - **What re-homing them actually costs, measured on all ten.** Three sites
     were nearly free: THIR already evaluated the exact condition and threw
     the answer away, with a source comment saying it rejected so the AST
     would raise. Two more needed THIR to learn a condition it never
     evaluated, because the rejects that shadowed them fired for unrelated
     reasons. The tenth is not re-homable at all and is filed as a defect --
     see the narrowed-name match entry below.
   - **The count is a FLOOR, not a ceiling, and the first site proved it.**
     The method only sees raise sites some `error_*` case reaches. Re-homing
     the polymorphic-`Optional` init path immediately surfaced its REBIND
     sibling: still AST-authored, no corpus case, so the ratchet is silent and
     it becomes a crash at cutover. Filed. Expect more of these to appear
     while doing the work rather than while measuring it.
   - **One of the ten is a WRONG rejection, and it blocks the cutover twice
     over.** `iterators/error_gen_match_nested_narrowed_ptr_bind` rejects a
     program that compiles, runs and matches CPython (verified end-to-end with
     a running binary against the exact committed source). THIR cannot raise
     its diagnostic because it rejects narrowed-name match subjects outright
     -- and that reject is NOT resumable-specific: a plain sync `def` with a
     nested narrowed match falls back too. So the residue is not one error
     case but an entire un-routed shape of ordinary valid Python that ICEs at
     cutover, invisible to every gate because no corpus case carries it.
     Mirroring the AST guard into THIR would reach a zero on the ratchet while
     making a known-wrong rejection permanent AND leaving the sync shape
     ICE-ing; the AST fix plus the routing lane is the work that actually
     unblocks the cutover. Filed in `BUGS.md` and `TODO.md`.
   - ~~`class_const` / `final_global` fallback is excluded from the ratchet by
     design~~ **RESOLVED 2026-08-30**: the residue reached zero and
     `NON_RATCHET_COMPONENTS` is now empty, so both positions are ratcheted
     like every body.
   - `tests/interop/*/expected/` is authored by the real CLI at the default
     `thir_codegen=False`; commit 1 must flip that default too.
   - **A constraint that DISSOLVES here rather than blocking:** the AST-first
     rule (fix the oracle in its own change-set) and the always-on THIR
     byte-diff are jointly unsatisfiable today. An AST fix landed alone leaves
     the byte-diff red until its THIR mirror follows, so for a divergence
     needing an oracle fix there is no green-at-every-commit sequence -- both
     rules are right and the pair is not satisfiable. After cutover there is no
     second author, no mirror and no byte-diff, so the tension goes away on its
     own. Do not weaken either rule to buy a green intermediate commit.
7. **Teardown. 15,887 lines net, and NOT the hard part** -- roughly a week of
   mechanical work. The body/skeleton boundary is already drawn and enforced by
   the committed gate. 13 shared helpers (302 lines) are pure predicates that
   relocate cleanly; 41 AST arms and 36 guards delete outright; 6 seam sites
   collapse into a direct THIR emit. Six functions (686 lines) are structurally
   BOTH and must be re-homed rather than deleted.
   **The D4 inventory this item refers to is stale in every number and missing
   about half the surface** -- it predates the stdlib gate, the cutover gate,
   both audit modules, the migration scripts and two CI rows. **REWRITTEN
   2026-09-01 at tree `0abcc8feb`; see "Gate D4 teardown inventory" at the end
   of this file, and read that rather than the numbers in this item.** The
   headline correction is that the deletion is smaller and the surface around
   it larger than stated here: the four modules are 15,759 lines and only
   THREE real import sites, but the corpus byte-diff SURVIVES (cases assert
   against committed AST-authored snapshots, not a live AST emit), while the
   two audits, the interop overlay and `dualgen.py` do not.
   Post-cutover `ThirUnsupported` becomes an internal error with no fallback
   behind it, so every reachable raise site turns into a hard compile failure
   on user source. There are **651 raise sites**, and the split was MEASURED
   2026-08-29 by `scripts/thir_migration/thir_reject_reach.py` (committed).
   The result reframes this item rather than sizing it:
   - **Only 16 of 651 sites are reached by any program we compile.** 633 are
     never reached by the corpus or the stdlib at all. Adding the 275 THIR
     unit-test files -- the only population that reaches a raise site ON
     PURPOSE -- takes it to 279 reached / 372 unreached.
     [Re-measured 2026-09-01 at a later tree: **655 sites, 283 reached, 372
     unreached, 241 fatal-capable**, of which 81 sit at guard depth 0-1. The
     shape of the finding is unchanged and the unreached count landing on 372
     twice is coincidence, not stability -- the site population itself moved.
     Quote the dated figure that matches the tree you are on.]
   - **The reason the unreached bucket is huge is structural, and it gets
     WORSE as the migration improves.** THIR raises only to reject: 29,829
     routed attempts produced zero raises. A corpus case reaches a site only
     by falling back, which the per-case ratchet forbids and the saturated
     dial makes impossible. **The healthier the migration gets, the blinder
     the program corpora become.** The pin corpus strictly dominates them for
     reach, so boundary pins are not extra rigor -- they are the only
     instrument that still sees anything.
   - **A valid, passing, snapshot-tested case is already an ICE-in-waiting.**
     `CH: Final[Char] = Char(65)` -- ordinary Python at a module-level Final --
     reached a raise site and survived ONLY because `final_global` sat in
     `NON_RATCHET_COMPONENTS`. Those two non-ratcheted components were the one
     hole through which a valid program could reach a fallback, and this is
     what came through it. **Both were closed 2026-08-30** (the `Char(n)`
     type-ctor now routes and the exclusion set is empty), so that hole is
     shut. Ten more sites were ordinary stdlib code that would ICE the moment
     the stdlib routed; the stdlib fallback tail is now zero, so re-measure
     that list rather than trusting it.
   - The fatal-capable set is **238**, not 203: a site reached only as the
     inner decider of a recomposed reject still kills the body through its
     composer.
   - **Driving the unreached set to zero is 200-400 engineer-hours with no
     set-cover shortcut** -- the densest arm grouping tops out at 9 sites. But
     not all 372 want a widened arm: a defensive tail like `stmt.unhandled`
     should become a DELIBERATE diagnostic, not new lowering. Triaging
     widen-vs-diagnose is a read, roughly 15 hours, and is the cheaper first
     pass.
   - **Cheapest thing that stops the bleeding: ratchet the reached SET.** The
     instrument's JSON is the artifact; reached -> unreached means a dead arm,
     unreached -> reached means new evidence.
   - **The structural argument this raises, which is a decision rather than a
     task:** after cutover a reject becomes an observable compile error, so
     the corpus REGAINS reach and pins get easy; before cutover it is
     invisible. That argues for a STAGED cutover -- AST emitter still present
     but a loud diagnostic on any fallback -- so the first 372 discoveries
     land on us rather than on users.
8. **The position-enumeration matrices. The premise is wrong in the direction
   that matters: cutover deletes FOUR detectors, not one.** The corpus
   byte-diff is named; `tpyc/move_audit.py` and `tpyc/binding_audit.py` are
   not, and both are dual-path joins whose AST-side recorders live inside the
   deleted emitter. `move_audit`'s own docstring calls itself the ONLY detector
   for its divergence class. Three matrices lose their sole net on the cutover
   commit while this checklist reads as satisfied.
   **A FOURTH joined them 2026-08-30 and dies the same way**: the error-path
   gate (`tests/conftest.py::_assert_thir_raises_too` plus its
   `AST_ONLY_DIAGNOSTICS` record) is reached only from the handler for a
   `CodeGenError` raised by the AST emit, so deleting that emit removes its
   trigger. Part of its coverage survives in each case's own `diag.txt`
   snapshot; the AST-vs-THIR comparison does not. Counting it is the point --
   it was built to close the hole the other three leave, and it leaves the
   same kind of hole behind.
   - **The stdlib's RENDER is no longer among the losses (2026-08-30).**
     `lib/tpy` had no committed C++ anywhere, so both stdlib checks compared
     the two authors against each other and both would have gone with the AST
     emitter. Its AST-authored emission is now committed as an ordinary test
     case, `tests/cases/harness/stdlib_render` (88 modules, 110 library files,
     ~1.37 MB), which imports every non-macro module and snapshots `"*"` via
     the `snapshot_lib_modules` options.json key -- so the render is compared,
     built and run by the normal case machinery and needs no bespoke authoring
     path. (A first attempt built one: a separate committed tree at
     `tests/stdlib_expected/` plus ~270 lines of hand-rolled comparison inside
     the gate. It was replaced the same week; a plain case does the whole job,
     and `snapshot_lib_modules` grew glob support to say so in one entry.)
     That buys the stdlib render only -- the two audits and the user-corpus
     byte-diff are untouched -- and any case may additionally pin a library
     module's emission at its own options with the same key. Measured while
     committing it, correcting one standing claim about
     instantiation-dependence and, later, one of its own: over ten
     stdlib-heavy cases, 63 library file emissions differed from the sweep's
     and NONE outside the include block (lib/tpy's generics and resumable
     frames lower to C++ templates in their defining module) -- ten cases are
     not the corpus. **The companion claim that the same emission over
     Int32/Int64/BigInt was byte-identical is WITHDRAWN (2026-08-30).** It came
     from the same ten-case population, in which only two cases set
     `default_int` at all and neither imports an affected module, so the
     measurement structurally could not see the effect. Re-measured over the
     mega-entry at each option, holding the compiled population equal per
     comparison: 164 file compares, 153 identical, **11 differing across 7
     modules** (`_datetime_cal`, `_datetime_fmt`, `_datetime_parse`,
     `collections`, `datetime`, `math`, `urllib.parse`) -- `collections.hpp`
     turns `int32_t i = 0` into `int64_t i = 0`. The committed render is
     Int32-only and says nothing about the other two widths. The populations
     had to be held equal because most of the library does not COMPILE above
     Int32 at all (filed in BUGS.md): 30 module/option pairs fail sema, 17
     distinct modules.
   - **Coverage measured by a committed instrument**
     (`scripts/thir_migration/thir_matrix_reach.py`), which patches the
     predicate, records (site, class) on every TRUE verdict and sweeps the
     corpus plus the stdlib. Wide pointee accessor: **102 of 350 cells**
     (35 sites x 10 classes), 33 of 35 sites, **9 of 10 classes**. Ptr-union
     sibling: 43 of 117, 11 of 13 sites, 9 of 9 classes. 28 pointee cells are
     witnessed exactly ONCE. Only `char` has no witness anywhere; the stdlib
     adds exactly one cell the corpus does not.
     **An earlier figure of "32 of ~140 cells, five of ten classes never
     reached" is RETRACTED, not refined.** It came from a sweep in which 214
     of 700 sampled cases silently failed for want of per-case options -- a
     third of the population dropped, which is the instrument failure this
     project keeps paying for. Layering the options removes the class of
     failure entirely. Read a zero as absence of witness, never as proof of
     deadness: the script cannot separate unreachable from untested and says
     so in its own output.
   - **There are at least NINE such matrices, not the two named here.** The
     largest is the arg-table's family x row space. **Three different cell
     counts are in circulation and they do NOT conflict -- they are three
     KEYS**, which is this project's oldest recurring error: 359 is the count
     of `_ArgRow(...)` constructions, 194 the distinct row NAMES, and ~386 the
     (family, row) CELLS, since a shared row counts once per family carrying
     it. An older entry's 367 is the cell count on an earlier tree. Name the
     key or the figure means nothing. The table's own module docstring states
     this item's hazard verbatim. It is not probe-sweepable; it needs a
     coverage assertion.
   - The `_name_read_deref` caveat here went stale ONE DAY after it was
     written: the inversion landed 2026-08-05. It discharges the class axis,
     not the position axis, which is the larger one.
   - Sweeping the two named matrices is 30-55 focused hours, measured from a
     real probe batch with a 37% first-draft yield. **Prefer partition tests
     over a sweep**: a sweep proves today's tree and rots on the next widening,
     while this repo already has two precedents for a total-partition test that
     FAILS when a new member appears. Three of the matrices admit that
     treatment for 1-2 days.

**Honest total: 4-6 weeks if the decisions come promptly**, with two genuinely
unknown quantities that could move it either way -- the 33 unwitnessed
diagnostics, and the never-reached bucket of the 651. The critical path is NOT
the deletion. It is the four `gen_async.py` decisions and the error-diagnostic
re-homing, both of which are blocked on judgement rather than effort.
**[STALE 2026-09-01 -- BOTH critical-path items are discharged, so this figure
no longer describes anything. Corrected in place rather than deleted, because
what the estimate got RIGHT is the useful part: it predicted the bottleneck
would be judgement, not effort, and that is exactly how both items cleared --
by decisions, not by grinding.** The four `gen_async.py` decisions landed
2026-08-21 and the cutover gate's OPEN set is now empty; the error-diagnostic
re-homing finished 2026-09-01, and the site instrument reads
`BODY + deliberate, witnessed: 0`, which is the same fact as an empty
`AST_ONLY_DIAGNOSTICS` measured a different way.
**The two unknowns both moved, and NAME THE KEY on each because they are not
the same measurement.** The "33 unwitnessed diagnostics" came from a reading
that counted raise sites in the doomed files; the committed instrument now
reports **5** in the narrower `BODY + deliberate, NO witnessing case` key --
user-facing diagnostics that die at the cutover with no case able to watch
them fire. 33 and 5 are NOT a delta; they are two keys, and the second is the
one to work. Each of the 5 needs a re-home or an explicit accept.
The never-reached bucket stopped being an open quantity at all: it is now 372
of 655 raise sites, and section H accepts it as priced risk rather than
scheduled work, because probing was measured and cannot close it -- 58 probes
reached 19 sites with 3 of them the intended target.
**No replacement figure is offered on purpose.** The residue is the 5
diagnostics, the time-boxed `dualgen` sweep, the two cutover commits, and a
teardown this same section calls roughly a week of mechanical work. Anyone
wanting a number should derive it from those four, at the tree they are
standing on.]
What changed NOW rather than at the checklist: `--thir-codegen` implies
`--thir-stdlib` (the stdlib oracle rides every measurement run instead of
depending on someone remembering a flag; ~10-15% wall on a comp-only run)
and the measurement run prints the migrated-case dial it previously
omitted. [Both halves superseded 2026-08-25, by the entry near the end of
this file: the wide stdlib oracle is now DEFAULT-ON for every run (with
`--no-thir-stdlib` as the opt-out and `--thir-stdlib` a no-op/force), so it
no longer rides on `--thir-codegen`; and the `~10-15%` figure was measured
and found stale by 2-3x -- the real cost is `+14.0s of 273.3s = +5.1%`
comp-only.] Grind economics stay settled per D4: byte-identity holds for
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

### Long-tail waves: value-record globals, property is-None, membership, try-hoists (2026-08-05)

Four waves + harvest, +13 flips (dial 3248 -> 3261/3729; markers
481 -> 468). Full exec suite green (11161 passed, all 3728 cases
built+run); move-verdicts 0 over 814 joined nodes. Review round:
3 specialists clean (codegen-correctness, safety-model,
cpython-parity); applied as one commit (ledger + stale TODO row, the
field-over-call membership boundary pin, the er.bind_alias face
assert, the 12-flag consolidation filed in TODO).

**Wave 1 (+4: the datetime UTC quartet).** _readonly_global_type
gains the VALUE-record row: a value-record global (`UTC: timezone`)
is a plain namespace-scope object, never a pointer slot, so it seeds
read-only exactly like a scalar global (bare same-module render,
qualified imported spelling via imported_variable_cpp). Routing the
datetime bodies exposed a LATENT move divergence: the THIR
copy-record and owned-record decl arms promoted into the movable
working set unconditionally, while the AST's plain decl arm promotes
only NON-value locals -- a sema-movable VALUE record (expensive
innards) then moved at its last-use dict-literal element where the
AST brace-init copied (`make_ordered_map` vs `ordered_map{{...}}`).
Both arms now carry the AST's value-type filter. Pins:
TestValueRecordGlobalSeed, TestValueRecordDeclNoPromote.

**Wave 2 (+2: property_optional, urllib_parse).** The @property
is-None family: the AST's Optional detection sees the field-access
node and its storage-form classifier admits any FieldAccess, so a
property subject tests has_value over the getter call whatever the
repr (the getter skips the plain-method optional_to_ptr lift on
is_property_getter). Three rows: the gate row in
_is_none_compare_operand (static-protocol inners keep the AST's
pointer-compare arm), allow_whole_optional threading through the
property delegation + the property-scoped ptr-repr Optional ret row,
and the OPTIONAL_TO_PTR decl off a property (`n = w.node` ->
`optional_to_ptr(w.node())`). Faces isnone.property_subject,
decl.opt_property_lift. A plain METHOD-call subject keeps falling
back (borrow-form nullptr compare, pinned).

**Wave 3 (+2 direct, +2 harvest: set_frozen_dc_field,
requests_cookies_expiry; set_in_record, json_model_field_name_scope).**
The binop.shape.in.record site, all three rows: a container-FIELD
haystack composes into the same `.contains` member as a name receiver
(field-prechecked past the field arm's result gate, the ranges arm's
model; face binop.contains_field_recv); a user __contains__ off a
call-rvalue field receiver rides _field_over_call_ok; and the
universal __iter__/__next__ membership loop for a user iterable with
no __contains__ lands as THIRMembership.iter_loop with the fixed
statement-expression emit (scalar needle over a bare declared name
only; face binop.iter_membership -- corpus-unwitnessed since
arraylist_membership still falls back on expr.list_comp, so the unit
pin is the witness). The fallback tag-composition pin's fixture moved
to a still-rejecting view-keyed set.

**Wave 4 (+3: plain_borrow_return_tier, datetime_zoneinfo_errors,
generic_recursive_return_readonly_error_return).** The try.hoist
site, all three flavors, on the with family's model: borrow-only
non-value hoists take the pointer predecl; the CONST borrow-decl
sibling spells `const T* v;` (try.hoist_const_ptr, lc.const_locals
registration); VALUE-record hoists ride _try_hoist_type_ok's new
record row (`ZoneInfo z;` + plain body assign -- NB the helper is
shared with the if cascade and with family, so the row widens all
three uniformly, matching the AST's uniform _emit_branch_decls).
Chain-walking the genrec case added the aliasing @error_return bind:
`v = &(::tpy::unwrap_ref(*tmp));` into a hoisted pointer target
(THIRErrorReturnBind.alias_bind + er.bind_alias, the AST's
aliases-and-pointer-local arm; the loc omission was caught by
dualgen as a missing comment-trivia line -- comments=ON earns its
default again), and the raw_stmt_handled ret row (a statement-handled
@error_return call renders bare whatever the success type; the
caller's unwrap block owns consumption). Boundary pinned:
rvalue-reassigned hoisted names keep rejecting (THIRTry has no
rebind-slot field).

Lesson repeated from waves 5-7: the lossy tag names the FIRST reject,
not the family -- binop.shape.is was three property shapes, try.hoist
was three hoist flavors, in.record was three receiver/loop rows; the
per-case oracle read (thir-verify-before-propose) picked every row.

### Scalar pointees + the btuple branch-hoist track (2026-08-05)

Waves 8-9 + review round 3, +5 flips (dial 3269 -> 3274/3729; markers
460 -> 455). Sealing suite green (11179 passed).

**Wave 8 (+1: dict_methods).** _opt_pointee_wide admits CONCRETE
scalar/Char pointees (`v = d.get("a")` -> the `int32_t* v =
::tpy::dict_get(d, "a")` borrow). The consumer-row dualgen sweep
caught the narrowed-value-read bare-`v` divergence pre-corpus; review
round 3's codegen-correctness then caught the REAL Critical the sweep
missed: an UNPROVEN whole read at a value-repr Optional sink
(`return v` / `take(v)`) deref'd unconditionally -- UB on the None
case, and reachable from any `v = d.get(k)` with no narrowing. The
scalar family now resolves FIRST in _name_read_deref (ahead of the
generic indirect-read branch the call-arg path took): proven-narrowed
reads deref `(*v)`, wired whole-optional sinks stay bare, unwired
whole reads REJECT (name.scalar_ptr_opt_unwired). LESSON: a
consumer-row sweep is only as good as its sink list -- the swept
probe covered print/None-test/pass/return-of-call but not
return-of-BOUND-NAME, and the two lower through different arms.

**Wave 9 (+4: the tuple trio + the walrus).** The btuple branch-hoist
track: owning tuple-call sources hoist the rebind slot at the if head
(`std::optional<std::tuple<...>> __slot_N;`) and emplace per branch
(`t = ::tpy::tuple_to_pointer<{borrow}>(__slot_N.emplace(...));`,
THIRAssign.btuple_borrow_cpp); sibling-name sources copy bare
(`u = t;`); the hoist predecl takes the const fixpoint verdict
(`const Box*` -- surfaced by CONVERTING the old walrus fence pin,
whose fixture never mutates through the local); the hoisted
owning-call walrus rides the walrus node's existing slot machinery
with an owning-call walrus VALUE counting as a binding source. The
emit assign-arm orders the emplace branch before the ptr-Optional
reseat and structurally excludes borrow-tuple targets from it
(branch-order-proof, pinned with the reversed fixture). Params and
non-owning-call walrus binds keep their fences, pinned.

### The deref-view isinstance family (2026-08-05)

Wave 10 + review round 4, +2 flips (dial 3274 -> 3276/3729; markers
455 -> 453). Full suite green (11182 passed; tally 12230 bodies).

`isinstance(b, Dog)` through a Deref wrapper (Box/Rc over a @dynamic
payload): the C++17 if-init cast of the deref PAYLOAD pointer
(`Dog* __b_ptr = dynamic_cast<Dog*>(&(b.__deref__()));`, shared
narrow_cast_rhs chokepoint; dyn_adapter_cast for structural
conformers). Sema keys the fact under deref_view_key(var) and never
retypes the wrapper; the THIR branch registers lc.deref_view_spelled
(explicit save/restore, classified in the scope-registry guard) and
member calls carrying deref_narrowed_to -- the method arm's FIRST
dispatch -- read `(*__b_ptr)` (zero-arg plain methods, the witnessed
slice). Post-branch calls revert to the deref chain; the
rebind-invalidates flavor fences (if.deref_view_shape, pinned).

THE DE-ROUTING INCIDENT (lesson, paid in-line): the same-module
@dynamic-protocol type-arg admission was first landed GLOBALLY in
_f1_record_type_arg_ok -- the harvest tally then showed 38 bodies
DE-ROUTED (12228 -> 12190): F1-False shapes (Optional-conformer
field writes, setitem families) had been falling through to WORKING
arms, and the widened verdict committed them to rejecting paths.
The row moved into _deref_wrapper_record_ok, scoped to the
user-Deref receiver where no wrapper spelling renders; the tally
recovered to 12230. THE ROUTED-BODY TALLY IS THE DETECTOR for
shared-predicate widenings -- run the harvest after ANY of them; a
speculative unit guard for the de-routed class was attempted in
round 4 and DROPPED (the guessed shape routes fine -- only the
tally sees this regression class).

### Forwarded aliases + frame comp writes (2026-08-05)

Waves 11-12 + review round 5, +6 flips (dial 3276 -> 3282/3729;
markers 453 -> 447). Full suite green (11186 passed; tally 12230).
Also PARKED (TODO.md): the mixed walrus+temps family (the AST's
clear-and-burn temp numbering is a design fork -- mirror the quirk vs
fix the AST first) and the expression-level @error_return unwrap
track (`__er_N` goto stmt-exprs, zero THIR machinery).

**Wave 11 (+3: the proto-param alias trio).** `xs = it` in a
resumable is a compile-time rename: the decl emits trivia only
(decl.forwarded_alias) and every read DELEGATES to a synthesized
backing-param name (name.forwarded_alias) so the param's own binding
type / frame classification / deref verdicts apply -- the AST's
generator_storage_name substitution. Round 5 gated the delegation's
flavors (narrowed / spelled / self / function-ref fall back -- the
AST substitutes at its render tail, after those take precedence).

**Wave 12 (+3: the nested-list-local trio).** A comp init crossing a
suspension renders its ordinary statement-expression INSIDE the
frame-slot emplace (`rows.emplace(({ ... })));`) -- the sync decl's
comp branch mirrored at the frame sink (res.frame_comp_write); round
5 aligned its shadow-check pointer filter with the other comp call
sites (the unfiltered set was over-conservative, not wrong).

### The leaf finally bridge + storage-opt comps + yield-slot rows (2026-08-05)

Waves 13-15 + review round 6, +8 flips (dial 3282 -> 3290/3729;
markers 447 -> 439). Full exec suite green (11203 passed; tally
12233, move-verdicts 0). Review: 7 specialists, 3 clean
(codegen-correctness, cpython-parity with direct mutation probes,
convention); round applied as one commit (dead helper deleted,
Own[record]-yield + two comp-fence pins added, stale TODO lines
fixed). PARKED (TODO.md): the yield-position borrow walrus (the AST
emits a DEAD frame_slot member plus a shadowing case-block pointer
local -- "fix the AST first" territory); comp elements calling
@error_return callables joined the er-expression track entry.

**Wave 13 (+3: the async finally trio).** A return crossing a leaf
try/finally routes: THIR's _emit_frame_wrapped mirrors each finally
frame onto the AST ctx.finally_stack (statements._push_finally) so
_make_async_return's chain walk inlines the finally bodies -- shared
guard numbering, shared live_finally_guards, and a shared pre-value
IterCounter (CtxIterCounter backs both paths' temp draws, fixing the
__tpy_async_ret_N off-by-one). The deferred pointer-capture flavor
(Own returns mutated by the finally) rides the hook's own
_deferred_return_recipe -- the return.finally_deferred_capture fence
became the res.leaf_deferred_capture witness. Break/continue
crossings keep the fence (_leaf_finally_crossing_loop; the
return-inclusive walk was deleted as dead in round 6).

**Wave 14 (+3: storage-opt comp loop vars + synth iterables).** A
ptr-repr Optional[F1] comp ELEMENT registers the loop var in
storage_opt_locals (the for-statement container leg's comp twin,
const-source fence kept): narrowed member access derefs at the
ACCESS site ((*item).x -- field.storage_opt_recv), a protocol-slot
arg passes the WHOLE optional (repr_of(item)), an unproven filtered
access wraps deref_optional_check (NOT the optional_to_ptr lift),
and the None-test spells has_value. The rows also serve the
for-statement loop var and comp unpack targets (two fence pins
converted). Separately, a Spannable conformer (synthesized
begin()/end(): __span__ + SpanIter __iter__, no explicit begin/end)
admits as a comp iterable -- the AST's comp loop is unconditional
there; no storage-form registration for the synth family
(register_loop_var_storage_form early-returns on non-native-iterable
sources), so Optional/ptr-tuple elements and unpack heads reject.

**Wave 15 (+2: yield-slot rows).** Own[container] yield slots peel
Own at the gate and the per-yield dispatch (the render is the plain
container borrow; the peel also reaches the record family --
Iterator[Own[Node]] pinned routed in round 6). A ternary of
frame-slot containers hands out the branch-picked borrow at the
yield AND at native protocol arg slots (len(a if flag else b) --
_container_ternary_arg + the ifexpr container NAME-arm row). A
record field off self reads bare (`return __self.a;`, BORROW_BIND).
LESSON (the tally as detector, paid a third time): the ifexpr
container arm first REJECTED non-NAME arms, but literal-arm
container ternaries (`[1] if c else [2]`) already routed through the
generic tail -- 5 bodies de-routed (12233 -> 12228) and two existing
pins failed; the arm is NAME-arm-gated with fall-through now. The
"re-run pins whose reason your widening touches BEFORE the corpus
run" rule would have caught it a full harvest earlier.

### Bounded fields, tuple yields, generic factories (2026-08-05)

Waves 16-18 + review round 7, +9 flips (dial 3290 -> 3299/3729;
markers 439 -> 430). Full exec suite green (11213 passed; tally
12233, move-verdicts 0/820). Review: 5 specialists; the unpack
promotion verified SOUND (double-gated on sema_movable_locals, every
consumer joins the move audit), the leaf self-field admission
verified closed (a suspending loop never reaches the leaf arm).
Round applied as one commit: dead generic-factory reject branches
deleted (call.callee_kind subsumes them), the bare tuple-NAME yield
leg re-keyed order-independently by TYPE (value tuples / borrow-form
params; owned-shaped params and pointer names keep the fence -- the
storage sets are a lowering-order-populated PARTIAL mirror, unsafe
as the sole gate), an rvalue-source fence on the generic-subscript
element, boundary pins (non-admitted factory position, Own-tuple
param yield, Spannable bound), tag-tightened fences, and stale
TODO/docstring fixes.

**Wave 16 (+3: bounded open-T field iterables).** A non-suspending
for over an open-T SELF field in a resumable leaf: an Iterable-bound
T rides the open-T field leg (the leaf gate admits self-field
iterables -- `__self.items` is the ordinary field respell), a
NativeIterable/Spannable-bound T takes the AST's begin/end member
peephole via the native-bound field leg on the container route
(foreach.native_bound_field, tparam_bounds threaded). Non-self
receivers keep the fence.

**Wave 17 (+3: tuple-yield sources).** A GENERIC tuple literal at
the yield slot rides the return arm's builder (spelled val_or_ptr_t
brace-init + to_val_or_ptr wraps, res.btuple_yield_generic; the
generic-element leg also admits container-subscript LVALUES off
plain declared names). A tuple NAME yields bare when the type says
the AST's tuple_to_pointer wrap no-ops: value tuples and borrow-form
params (`return v;` / `return p;`).

**Wave 18 (+3: generic factories + unpack promotion).** The
for-iter setup and coro-handle frame write are allow_temps statement
positions (ref-slot literal temps flush where the AST flushes);
_free_callee_kind's generic factory rejects are generator_ok/
coro_factory_ok-conditioned (the explicit-targ spelling rides the
generic arm); _var_decl_names includes tuple-unpack targets so the
frame promotion mirrors _gen_tuple_unpack's promote_movable (the
channel `producer(std::move((*tx)))` move).

### Readiness-gate addendum: two construction rules + a D3 note (2026-08-05)

From the branch retrospective (second-opinion reviewed). Two recurring
defect classes this branch paid for, promoted from per-wave lessons to
construction RULES for future arms:

1. **Lowering predicates key on sema/type facts, never on
   lowering-order-populated sets.** The bare tuple-yield leg was first
   keyed on `storage_tuple_locals` (a PARTIAL mirror populated during
   the walk; join-ordered resumable BBs can read it before the decl
   that populates it) and had to be re-keyed by TYPE in review round 7
   (`_bare_yield_tuple_name_ok`). An order-populated set inside an
   admission predicate is a design smell.

2. **A new arm inserted above an existing fall-through must
   gate-and-fall-through, never reject.** The ifexpr container arm's
   initial reject de-routed 5 previously-routing bodies (wave 15).
   The detection rule (re-run pins a widening touches BEFORE the
   corpus run) catches this late; the construction rule prevents it at
   write time. Corollary: edits to SHARED predicates warrant a
   same-wave pin check -- the review-batch cadence is the right
   granularity only for purely additive arms.

D3 cross-reference: the wave-13 leaf finally bridge (THIR mirrors
finally frames onto the AST ctx.finally_stack so _make_async_return's
chain walk emits, with THIR-rendered finally bodies via the mirrored
frames' writer closures) is a LEAF-SEAM interaction in Gate D3's
terms: the chain walk is skeleton (permanent printer layer), the
bodies it inlines are THIR-lowered. Checklist item 4's call-site
inventory should count _make_async_return as a skeleton position
whose body renders already come from THIR through the bridge.

Also pinned at the gate: the unpack-target promotion's up-front-vs-
statement-order timing skew (wave 18) is UNREACHABLE -- both mixed
loop+unpack binding flavors reject at pre-existing fences
(tuple.reused_target / foreach.var_shadow), now unit-pinned
(TestMixedLoopUnpackBindingFences).

### The binop reject-surface wave (2026-08-05/06)

Eight cells ground `_lower_binop`'s shared `reject()` surface
(expressions.py, the top cross-tag raise site: 14 of 226 sole-blocker
cases) to zero; 13 flips (dial 3299 -> 3312/3729). Site selection came
from a NEW cross-tag instrument: with the tag histogram flat (top tag 5
cases), a sites_all.py variant of probe_sites histogramed raise SITES
over ALL sole-blocker cases at once, with re-raise trampolines
attributed to the inner site via exception-chain inheritance -- the
per-tag probe under-attributes both.

Cells: slot-threaded nested binops (`_ExprUse.slot_threaded`: resolved
operand slots, fixed-int call args, return values -- the AST threads a
target at all three, so both-literal sub-binops never fold there);
value-record optionals (`_value_opt_value_record`, USER records only --
builtin nominals carry record info too and must stay on the scalar/view
rows, caught by pins) + Ptr field-chain None subjects
(`_chained_field_read_ok`); Char concat/repeat operands (emit already
applies rb operand wrappers -- gate-only); resolver-less scalar binops
(post-sema macro synthesis; raw-operator render, NAME operands only);
pointer-repr tuple-literal compares (tuple_eq/tuple_lt via
template_override, pending elements resolved before spelling) +
membership needles (tuple_to_storage FormConvert); TypedDict membership
over call receivers; universal-loop membership over structural protocol
params (is_native_in admits only tpy.NativeIterable to
ranges::contains); value-opt record eq pairs + list_concat.

Lesson: three de-routings-by-pin were caught by re-running touched pin
files BEFORE the corpus run (the readiness-gate construction rule
paying off); the `_value_opt_value_record` builtin-nominal over-match
was caught the same way. An out-of-context `_f1_record` probe
false-negatived (memory's activate_compiler rule) and cost a
divergence-chase: the print wrap's F1 verdicts must be probed
in-context.

### The print-arg site wave (2026-08-06)

Seven cells ground the print-arg admission gate (statements.py, the #2
site: 13 sole-blocker cases) to zero; 7 flips (dial -> 3319/3729), six
cases chained out to other sites (method-call, aug-assign, genexpr,
special-form isinstance, native-arg families). Rows: print(None);
pointer-bound RAW-wrap names (the wrap render now lowers with the
full-deref use -- every AST printer-wrap arm renders via
gen_expr_deref); bytearray/dict-view/range call results; container/
tuple literals (typed-brace CTAD + borrow-form TuplePrinter);
value-tuple container-element subscripts (BORROW_BIND); container-binop
wraps (list_concat generalized to the four set operators; set ordering
compares admitted -- the "ordering has no witnessed corpus shape" note
was falsified by set_operators); container property reads (ITERABLE
use); opt-ptr call prints (ptr_opt_passthrough); slice objects (name +
object-index slice reads); union-field is-None monostate tests.

Review round (7 specialists + meta, all findings verified, none
dropped): ledger entries (this text), the union-FIELD print STREAMING
arm pinned (admitted-but-unwitnessed -- its motivating case still falls
back on a later blocker, so no corpus witness existed; dualgen showed
byte-identical), `_resolve_pending_tuple_elems` extracted from three
same-diff copies, list-ordering + return-row boundary pins added.

Lesson (meta-observation worth keeping): TWO findings this round were
the same defect class -- an admission landed without the three-unit
contract (the union-field STREAM arm unpinned; the set-ordering widening
without its reject boundary). The wave self-check should grep new
`_wrap_print_form` / pair-predicate legs for a matching pin BEFORE
review, not rely on review to catch it.

### The for-each iterable wave (2026-08-06)

Seven cells ground the for-each route selector (statements.py, the #3
cross-tag site: 12 sole-blocker cases) to zero; 10 flips (dial 3319 ->
3329/3729), 2 cases chained out (setitem / genexpr-unpack families).
Rows: BigInt stepped ranges (literal steps take a `__step_N` temp with
NO overflow check -- the emit's is_big_int_type arm; variable BigInt
steps stay fenced) + the ZERO-literal-step range-OBJECT foreach (the
counter loop's decline path, a dispatcher fall-through to the container
route); `for x in self` on user-iterator records (the `(*this)` capture
via the THIRSelf deref retag); user-iterator container-ELEMENT
iterables (the element gate's ITERABLE-use whole-lvalue admission --
which over-admitted NATIVE-iterable elements and rendered the wrong
loop form until the restored fence pin caught it); free container-call
iterables at tuple-unpack heads; open-T param iterables; proven-
narrowed ptr-opt dict key loops; value-opt scalar unpack targets;
explicit `own_iter` loops (the ConsumingIter wrap re-spelled with the
call's ARG, plus the THIRForEach.consuming elem-bind flag the implicit
route had never needed); F1-record genexpr elements (the borrow-slot
fence guarded only the `val_or_ref<T>` slot spelling).

Two defects the verify run caught: (1) THE ATTEMPT-CONTAMINATION FIND
-- admitting a value-opt-scalar dict element let a GENERATOR body's
lowering attempt poison the AST re-emit on fallback (a narrowed loop
var lost its deref); surgical ablation proved the eligibility entry
causal, fencing the route selector did NOT stop it, so the row is
REVERTED and PARKED in TODO.md as the attempt-rollback design class.
(2) The native-element over-admission above -- caught only because a
fence pin existed; the wrong render was a whole different loop FORM,
invisible to the sole corpus witness (a user-iterator element).

### The call-arg wave (2026-08-06)

Three cells at the free-call arg ladder (the #4 site: 10 live cases; 1
parked design case excluded); 4 flips (dial -> 3333/3729), 3 chained
out (method-arg literal temps, imported-symbol callee, generic-tuple
return), 1 sub-family given back. Rows: OWN-element tuple NAME moves at
the matching rvalue slot (`consume(std::move(t))`, _is_move_source
joining the move audit) + the sync-body widening of
_borrow_tuple_bare_names (single-assignment ptr-repr tuple DECL locals
pass bare; generator/async lanes keep the reassigned-only slice); enum
members at native protocol slots; Sized container-call rvalues; member
generator factories at Iterable slots under the ITERABLE result wire.

THE SHADOWING LESSON: a kind-blind wrapper-union-literal head leg
DUPLICATED the existing argtemp.recursive_union_literal /
ru_wrapper_literal row family, took over its cases with a subtly
different render, and DIVERGED on the mutual-recursion shapes the
original gates deliberately exclude (union_mutual_mixed/_contexts). The
existing rows' pins caught it as witness counts dropping to ZERO --
REVERTED whole; the union pair's widening must go through the existing
arm's gate. Before building a "new" row, grep faces.py for the
construct: an existing face with the same name-shape is the strongest
evidence the row exists.

### The method-arg wave (2026-08-06)

Seven cells at the method-call arg gate (the top cross-tag site after
the call-arg wave); 8 flips (dial -> 3341/3729). Rows: value-opt
ValueType-record method slots (ctor rvalues inline + member NAMEs via
the converting optional ctor); generator-factory method arg TEMPS
(container literals + record ctor rvalues materialize the named
scope-local the frame borrows -- dualgen showed the container-literal
flavor was an admitted-but-DIVERGENT latent shape rendering inline
where the AST hoists, and the `sum(...)` iterable wire arm was not
threading allow_temps; unmirrored temporary shapes now RAISE rather
than fall through); the full protocol-slot faces on user-record
methods (the Adapter/RefAdapter hoists, protocol_slots + flush
threaded for exactly the protocol slot); the strview_to_str coerce
over a view NAME peeled at Own[str] element args and user-record
setitem values (a declared-StrView module constant arrives wrapped
where a pending-resolved local goes bare); bytearray NAMEs and
container-returning CALL rvalues joining the native-Iterable bare-bind
family.

LESSON (repeated, now twice in two waves): re-run the pin files
adjacent to a widening BEFORE the corpus run -- two stale fence pins
(bytearray extend, StrView name at Own[str]) failed in the harvest run
because their stated reasons were exactly what the wave implemented.
Fences converted on dualgen-verified byte-identity.

### The marker-cells wave (2026-08-06)

Five cells at the two marker sites (builtin-module ctor /
module-native + the special-form classifier); 6 flips (dial ->
3347/3729, auto_move_user_copy_no_warn riding along). Rows:
union-subject isinstance at VALUE positions (the narrowing condition's
holds_alternative chain with no extraction alias, spelled on the bare
original name -- assert-narrowed and branch-narrowed subjects route
bare too, dualgen-verified); the module-qualified float("nan") fold
twin; @native(binding="C")/@export cross-module callees via the
qualified kind over the module-qualified NATIVE symbol; copy()
extracted into _lower_copy_special (free + qualified spellings) with a
new str-NAME arm; the imported-name conditional-qualification arm (a
LOCAL shadow emits the bare unqualified call).

Filed: the pre-existing AST crash on copy() of a str LOCAL
(PendingStrType at _gen_copy_expr) -- BUGS.md; THIR's NominalType
guard falls back gracefully. Review round 3 confirmed all lowering
clean (codegen-correctness/safety-model/cpython-parity deep-traced);
its fix round extracted the float-fold and strview-peel duplications
into shared helpers and added the boundary pins the batch owed.

SECOND ATTEMPT-CONTAMINATION REPRO (worktree-verified pre-existing): a
failed generator lowering attempt drops the to_value_variant wrap from
an UNRELATED ctor member-init in the same pass -- see the PARKED
TODO.md entry; the contaminated emit is not narrowing-local, which
strengthens the rollback-fix framing.

### The subscript-receiver wave (2026-08-06)

Eight cells at the subscript read gate + its receiver resolution; 12
flips (dial -> 3359/3729). Rows: container-returning CALL receivers
interpolate into the checked dunder (READ-only -- the shared recv-type
resolver has no call arm, so setitem/del/aug keep their slice; two
result-gate composition rows opened the view and protocol families;
the FREE-call result gate still lacks its composition row -- pinned as
the residual boundary); None-narrowed Optional[view] receivers read
their `(*s)` deref (str + bytes, param and STORAGE-local flavors --
NO narrow-set test: sema hard-errors on the un-narrowed use, so
reaching the gate implies the proof); the recv-type resolver
substitutes a raw TypeParamRef field decl via the expr type (read and
write ride the same resolver); value-union subscript elements copy
the whole variant bare; btuple ternary-of-names alias decls;
container-binop decl inits (any binop rvalue, delegating to
is_rvalue_source -- aliasing dunders excluded there); substituted
Own[T]|None slots (record-name move with the Own-lift coerce peel --
two independent guards; None -> nullopt).

PARKED: the OwnIter explicit-decl family -- the decl `auto` slot and
the begin/end for-head route both need the registry-gap question
answered (a probe routed the protocol loop and DIVERGED; findings in
the TODO entry, both halves built and reverted).

### The method-receiver wave (2026-08-06)

Three cells at the method receiver gates; 5 flips (dial ->
3364/3729). Rows: None-narrowed Optional[view] receivers join the
view family (type-keyed -- the same sema-implied proof); a plain
@staticmethod through an instance renders the ordinary member call
(the classmethod rule's witnessed sibling; two fences converted);
function=True natives on @native-record receivers take the
receiver-prepend spelling on the ptr-template arm; scalar-value
rvalue receivers (ctor / marker-call / binop results) compose stub
members bare via one shared predicate.

Review round 4 (waves 7-8): codegen-correctness traced the
sema-invariant behind the narrowed-Optional widenings to the actual
hard-error sites; safety-model verified the read-only claim and the
coerce-peel guards; the one Critical (unpinned STORAGE-local flavor)
was dualgen-verified byte-identical and pinned in the fix round,
along with the scalar-receiver predicate consolidation and the
set-binop decl pin.

### The composition-rows wave (2026-08-06)

Waves 9-10 (the free-call/ctor/return composition rows); 11 flips
(dial -> 3375/3729). Rows: free-call container-at-receiver + readonly
[scalar] results + structural CALL-rvalue protocol temps; three ctor
member-init legs (value-opt literal, the Send-peeled own-param set --
closing a mirror gap against _extract_field_inits -- and the owned-str
call rvalue); the type-param-bearing field-decl substitution at the
shared recv resolver; the async self-FIELD borrow-return rung
(`&(__self.inner)`, BORROW_BIND value lowering); the callable
container-literal element family; return-ladder call sources (storage
rvalue passthrough + the borrow passthrough via the DEDICATED
borrow_ret_passthrough flag, scoped to the return arm only).

THE SHADOWING LESSON, SECOND FIRING (now at the RESULT gate): a coarse
F1-record-at-RECEIVER row stole the er-unwrap / template-call /
native-iter arms' witnesses AND opened the PARKED REF_ALIAS design
stop -- REVERTED whole; the flag-scoped passthrough replaced it. A
result-gate row must be family-precise; grep faces.py before building
result rows too.

Review round 5: parity probed single-eval + aliasing empirically;
codegen-correctness verified the revert complete (zero stray refs, the
flag has ONE construction site) and surfaced the pre-existing
StrView-field-from-call-rvalue certain-UAF -- filed in BUGS.md (the
MIL leg mirrors it byte-identically per the owned-bytes precedent).

### The vararg-flush wave (2026-08-06)

Wave 12's vararg cell; 2 flips (dial -> 3380/3729). Two allow_temps
widenings at positions where the AST flushes the pack's std::array:
NAME-target scalar augs (the aug is a flushable statement like its
subscript-target sibling) and the ERASED/BORROWED await operand (the
emplace line; the for-head-setup precedent). The trio's third case
(ctor MIL vararg) is PARKED in TODO.md: the AST's temps.rollback ->
demote BURNS a temp counter number (`__tmp_2` where the up-front
demote says `__tmp_1`) -- a demote trigger was built, probed divergent,
and REVERTED; closing it is the AST-first counter-reset fix (snapshot
churn, needs approval). Fence:
TestVarargPackAugValue::test_ctor_mil_vararg_source_stays_ast.

### The field-gate wave (2026-08-06)

Wave 13; 5 flips (dial -> 3385/3729), all five sole-blockers at ONE
raise (field.result_type), four independent rows: allow_whole_optional
threaded at the VALUE_OPT field-write sink for same-family FIELD
sources; copy()'s container arm lowering FIELD sources with the
BORROW_BIND copy-source use (STORAGE stays for call sources); the new
field.suspend_borrow arm (`await self.evt` -> `&(__self.evt)`, the
skeleton owns the wrap); field_owned_str_ok threaded on the frame-field
assign like the sync decl sink (`q = (*__self.s);`). Bytes-field frame
reads stay deferred (fenced). Pins: test_thir_wave_fieldresult2.py.

### The genarg wave (2026-08-06)

Wave 14; 4 flips (dial -> 3389/3729), the four sole-blockers at the
_generic_plain_arg_ok raise: lambdas at still-open Fn slots (tuple
params admitted via _lambda_routable minus pointer-repr elements; the
sibling-open tuple read via _open_sibling_value_tuple, scoped to the
subscript value-read predicate); container-element subscripts binding
open slots bare (free-call ladder + _tparam_slot_arg); nested
value-tuple NAMES at bounded-T slots (literals fenced); the ptr-repr
Optional[wrapper] pointer-local deref at a same-wrapper slot (sema
admits the occurrence only where proven non-null -- the sema-implied
-proof rule). Exposed and closed alongside: argtemp.iter_proto threads
allow_temps (a factory's own ref-slot literal temps flush first), and
the comp route gained the VALUE-yielding generator-factory source arm
(comp.genfac_source; unpack-over-call keeps rejecting, pinned in
test_fallback.py). Two premise-fixtures updated: the freecall-ret
arg-position fence now routes; test_fallback's comp fixture moved to
the unpack shape.

LADDER-DEBT NOTE (the TODO.md stop-loss): this wave ADDED near
-duplicate rows again -- the open-slot compare (factored into a local
helper at review), the _tparam_slot_arg subscript widening, and the
lambda/nested-tuple rows in _generic_plain_arg_ok. The (source-shape,
sink-family) table fold is overdue by its own 2026-08-04 stop-loss.

Review round 6 (batch over waves 12-14): parity clean; the vacuous
`or c._thir_fallback` fence assertion fixed (the lesson: an or-tail
turns a scoped fallback assert into assert-anything); the claimed-but
-untested pointer-receiver copy boundary probed -- it ROUTES, so the
claim became a routing pin, not a fence; boundary pins added for the
value-opt field-write sink (subscript source) and the open-slot elem
row (field-access source); comp.genfac_source face registered.

### The asdict track + setitem/tuple-key waves (2026-08-06)

Waves 15-16; 5 flips (dial -> 3394/3729): the elem-field-chain setitem
receiver (`root.kids["a"].kids["b"] = v` -- the container-FIELD-off
-record-element arm; deeper chains fenced; flips
recursive_record_dict_field); the asdict/astuple expansion track --
container-ctor calls at VALUE sinks (also resolving the f-string
combinator fence: `f"{list(map(lambda ...))}"` routes), `dict({...})`
instantiation, scalar/str FIELD reads at non-wrapper union element
slots, field_str_ok threading at dict values + tuple-literal elements,
the dict/comp unique-member union rows, and the dict-elem comp arm
(flips dataclass_asdict_mixed); the nested storage-decl families
(dict-of-scalar-read-dict + nested value-tuple results, the self-typed
nested tuple literal; flips dataclass_asdict_nested and, via harvest,
str_pending_nested_tuple); the tuple-keyed dict family (value-tuple
keys, the membership needle at both contains arms, the `pairs[0][0]`
chain read; flips dict_in_tuple_key). asdict_optional stays marked on
the TERNARY element at an Optional[dict]-member union slot (per-arm
target typing -- next in the track). PARKED: isinstance inside
`and`/`or` conditions (condition-scoped narrowed-read renames -- a
third rename regime; TODO.md).

Review round 7: the comp-at-union-slot arm had ZERO units and
TestCompDictElement's docstring CLAIMED the row it did not exercise --
the docstring masked the gap (lesson: a docstring claiming coverage is
itself a review surface). Fixed with a dedicated face
(comp.union_member_source) + routing/boundary pins; the duplicated
membership-needle predicate factored into _value_tuple_needle_ok
(witnesses stay at call sites); nested-tuple-key and dict-name-elem
boundaries pinned; the record-valued storage-decl boundary probed
UNREACHABLE (sibling arms own those shapes -- noted in the pin file);
the _vu_field decided-vs-consumed instance appended to the tracked
TODO entry.

### The pointer-rows wave (2026-08-06)

Wave 17; 7 flips (dial -> 3401/3729, crossing 3400): the reassigned
borrow-call decl (decl.ptr_call_addr -- THIRPtrLocalDecl's PTR_ADDR
over the bare borrow-call render, placed ahead of the generic
borrow-call arm that would swallow calls into the REF_ALIAS render;
rvalue reseats ride the existing rebind-slot machinery; flips
return_borrow_aug_assign, plus two stays-AST fences converted after
byte-verification); the proven Optional-ptr alias source
(decl.alias_opt_ptr_deref_src, keyed on the deref-check marker; flips
json_model_user_type); the field-write receiver/source rows (property
-getter receivers, Callable-field NAME sources -- the old crash-fear
fence converted, its FormConvert path is never reached by the PLAIN
render -- and str-over-getitem with literal-index resolution; flips
inherited_accessor_subst + escaping_field); and the opt-tuple yield
elements + slot-free frame pointer reseats (container-element lvalue
lifts, pointee-typed passthrough, `prev = nullptr;`/`prev = it;`
rendering slot-free at both resumable write sites; flips the
tuple-yield trio).

STILL MARKED from the wave's probes: arraylist_move_nontrivial (the
moved-local pointer-classification mismatch, filed in TODO.md) and
asdict_optional (the ternary per-arm target typing).

Review round 8 (meta-review skipped this round: all findings were the
self-verifying docs/pins species confirmed verbatim in rounds 6-7):
the slot-free reseat admission extracted into
_slot_free_ptr_reseat_ok (the dead ptr_frame_locals disjunct removed
-- it was already folded into lc.pointers); BOTH proposed reject
boundaries PROBED as routing via sibling families (a value-returning
reassigned call is REBIND_SLOT, an inline-lambda callable write
renders itself) -- pinned as documenting routing pins instead; the
unproven opt-ptr alias boundary is sema-unreachable (the
sema-implied-proof rule) -- noted in the pin file; the wave-number
docstring label dropped.

### The frame-slot wave (2026-08-06)

Wave 18; 6 flips (dial -> 3407/3729): the del arm's frame-slot
exclusion dropped (the AST's _gen_del_var_code never special-cases
frame slots -- the bare `{ auto __del_sink = std::move(t); }` sink is
position-blind; flips the asyncio fire-and-forget/drain pair, plus the
old list-frame-slot fence's shape); Own[value-type] PARAM reads (the
no-op Own spelling renders the bare name; LOCALS keep the reject --
the widening without the param scoping DIVERGED on heapq_merge's
harmless std::move loop var, now pinned as a unit fence); the
THIRCoroHandleMove node (a NAME-source handle write's two-line
`emplace(std::move(*c)); c.reset();` pair; flips the async_bind move
pair + coro_optional_nonvalue_local via harvest). PARKED: the
sync-body concrete-coro handle pair (the erased-vs-concrete retype
decision -- a cell; TODO.md).

Review round 9 (meta skipped, round-8 rationale): three proposed
boundaries probed -- the coro self-write and ternary-source shapes are
SEMA-UNREACHABLE (noted in the pin file; the rejects guard defensive
residue), the narrowed-param del is INTERIOR to the skip ladder
(routes; pinned as such); the Own[Int32] LOCAL boundary is real and
now unit-pinned (the heapq flavor keeps own_read). The stale
sliced-out test name renamed; ledger entry written.

### The reseat/er-bind wave (2026-08-06)

Wave 19; 3 flips (dial -> 3410/3729): the POINTEE-typed pointer reseat
source (`saved = p;` -- the escape-hoisted `Point* p` copies bare;
flips escape_hoist_optional_target; the same-Optional GLOBAL flavor
stays blocked on the pointer-slot-global one-place consumer guard),
and the er-bind gate's TRY-HOISTED optional exemption (`p =
::tpy::unwrap_ref_move(*__try_tmp_N);` into the predecled optional --
the bind arm's default render verbatim; flips factory_error_return +
json_model_file_io). PARKED: the non-value hoisted-loop-var pair (two
predecl+rebind render families -- sync pointer-repoint vs resumable
optional-copy -- whose selection fact needs mirroring; TODO.md).
Deferred to the next stretch: the native record-eq / tuple-ordering
site (needs the native-operator==-vs-__eq__ discriminator mirrored).

Review round 10: the pointee-reseat type-mismatch boundary is a sema
error (type-checked assign); the er-bind's OTHER optional_locals
flavor (if-cascade-hoisted) probed -- it falls back safely at its own
try.hoist gate BEFORE the er-bind arm, pinned as the stays-AST
boundary; the parked entry's misattributed oracle-comment reference
corrected (the aliasing note belongs to dict_items_postloop, the
async sibling documents the orphaned-predecl regression).

### The tuple/slice/union-decl wave (2026-08-06)

Wave 20; 3 flips (dial -> 3413/3729): literal tuple ordering compares
(`(1, 2) < t` -- IntLiteralType elements resolve via
resolve_int_literals inside `_tuple_compare_pair`, matching the sibling
call sites; flips tuple_ordering), slice-object field reads
(`index.start` off the split slice-overload NAME receiver renders bare
via `_slice_object_type` -- the field.slice_recv face; flips
overload_getitem_slice), and ptr-union decl sources (SELF-rooted field
chains with const from the method's readonly-ness, plus union
container-element reads off a bare-NAME receiver with const from the
receiver binding -- the subscript.value_union_elem gate rows; flips
union_readonly_method_field). The Own[container] literal at a FREE-call
slot rides the plain-arg ladder (`consume({...})` inline spelled).

Review round 11: the subscript decl-source gate TIGHTENED to bare-NAME
receivers -- the arm's const verdict reads the receiver name while the
AST oracle recurses a field chain to its root, so a chained receiver
(`h.pets["k"]`) would mint the mutable variant where the oracle mints
const; a call receiver is additionally the dangling rvalue-container
borrow shape THIR declines to route. Field-receiver reject pinned. The
hand-rolled UnionType/needs_wrapper admission in the subscript read
gate replaced with the shared `_eligible_ptr_union` (narrower: keeps
generic-record members and all-value unions out). The Own-literal
free-arg leg gained its routing pin + the non-Own borrow boundary
(probed: routes via the ordinary literal arms). Mutable self-chain
flavor pinned beside the readonly one; faces.py comment placement and
the stale keeps-rejecting docstring fixed.

### The coerce-cast/lambda-btuple/alias-instance wave (2026-08-06)

Wave 21; 3 flips (dial -> 3416/3729), all in the expr.call long tail:
the own-coerce-cast leg (`take(big)` -- a REAL scalar-cast coerce over
a plainly-declared scalar local at an Own[scalar] slot binds the cast
rvalue bare, the AST's needs_copy=False flip; plus copy(scalar-name)'s
type-blind general tail; flips pointers/own_coercion), the lambda
borrow-tuple return (`-> std::tuple<std::string, Point*>` via
to_cpp_return, admitted ONLY for a same-typed generic-call body through
the lambda_btuple_ret use flag; the record dict-element print arg rides
BORROW_BIND; flips calls/ref_generic_tuple_return), and the
RecursiveAliasInstanceType type-arg admission (`Box[Tree[int]]` joins
the byte-identical slice -- both paths spell through the same
recursive_alias_cpp_names map; the wrapper borrow-call arm lands in
_lower_call_arg as the qualcall twin; flips
union/generic_recursive_box_field). Two disable-probes killed
speculative legs before commit (a dead Own-slot render intercept, a
dead w_genrec widening); a third proved the trailing-return spelling
branch load-bearing via a real byte divergence.

Review round 12: the meta-review's R5 probe found the own-coerce-cast
leg OVER-ADMITTING narrowed `int | None` names -- and the AST side
emits ill-typed C++ there (the narrowing deref is dropped under the
coerce; g++-confirmed, filed in BUGS.md) -- fixed by requiring the
DECLARED type be the scalar itself, with the divergent shape pinned as
a stays-AST fence. The alias-instance slice gained its local-union-
alias boundary pin (the UnionType sibling's reject through the new
arm); the scalar-GLOBAL coerce flavor probed as ROUTING (value-scalar
globals are not pointer slots) and its vacuous boundary claim was
corrected; the dict-elem witness and the call-receiver subscript-print
reject were pinned; four stale TODO.md sites pruned; the ~266-consumer
blast radius of the slice widening filed as a coverage TODO.

### The union-lift/property-receiver wave (2026-08-06)

Wave 22; 2 flips (dial -> 3418/3729): the union value-lift arm (a
ptr-variant-BOUND union name at a value-variant Own[union] arg slot
takes `to_value_variant<...>(p)` -- the AST Own-cascade's lift, keyed
on lc.ptr_variant_locals and positioned BEFORE the copy/move machinery;
flips lists/container_store_borrow_to_storage) and container-valued
property receivers (`c.items.append(4)` -> `c.items().push_back(4)` --
the container flavor of the view-field property row; flips
records/property_ref_semantics). The lift probe found the FREE-call
flavor already ROUTING DIVERGENTLY in the tree (an ill-typed ptr-variant
copy temp; dualgen-only, no corpus witness) -- the arm fixed it and the
routing pin doubles as the regression guard. PARKED: wrapper-union
match SUBJECT captures beyond the NAME slice (two witnesses; each needs
a new subject-bind render -- TODO.md).

Review round 13 (no meta pass -- no Critical): the container-property
arm now gates through the SHARED _container_method_recv predicate (the
hand-rolled is_list/dict/set admitted any set element where the family
predicate slices them -- the admission paths could drift); the lift arm
gained the defensive narrowed-name guard (the AST skips the wrap for a
narrowed name; all current admissions exclude them, but the arm is not
gate-trusted); the set-property and value-variant-container-arg
witnesses were pinned; the spliced faces.py comment was reattached.
cpython-parity verified the lift's copy-where-CPython-aliases is warned
(not silent) and the property-receiver aliasing observes correctly.

### The assert/setitem long-tail wave (2026-08-06)

Wave 23; 2 flips (dial -> 3420/3729): the whole-optional element copy
at a value-opt setitem slot (`items[i] = items[0]` -- the
std::optional element passes bare, TYPE-keyed on element equality;
flips both none_safety warn-subscript cases) and the
PROTOCOL-isinstance ASSERT arm (`if (!(<concept>))
raise_assertion_error();` -- the F5 constexpr-if arm's assert flavor;
its witness case chains into the parked name.union_binding_divergent
family, so the arm is pinned on minimal fixtures instead of a flip).
PARKED: the SELF-subject poly assert (needs the __self receiver-alias
model) and the sgen generic-record receiver (the generic-peephole
slice proof).

Review round 14: safety traced the assert arm's "no retype" claim to a
REAL gap -- a child-protocol fact retypes downstream dispatch on the
AST side (protocol_narrowings feeds the NativeIterable for-loop
peephole) while THIR's declared entry stayed stale. The probe showed
the stale type falls back honestly at the for-head gate (no divergence
reached the byte-diff), and the fix -- the constexpr-if branches'
declared-retype in the arm -- turns that fallback into routing, pinned
on the peephole fixture. The two prescribed reject boundaries proved
sema/codegen-unreachable (cross-type element assigns reject at sema;
generator protocol-union params reject at async codegen) and are
documented as such. The "same-container" comments were corrected to
the type-keyed truth, with the cross-container routing pin proving it;
the consumed TODO recipe entry is marked landed.

### The member-scan/element-rows wave (2026-08-06)

Wave 24; 2 flips (dial -> 3422/3729): the ptr-union member-scan
widening (str + concrete-container members ride
_ptr_union_view_member_ok -- to_cpp_ptr_variant pointer-spells every
member uniformly, so the shape-blind machinery covers them; flips
match/protocol_narrow_no_bleed by completing the wave-23 assert arm's
chain) and the nested-container element rows (the setitem move row,
_record_elem_subscript_arg's container flavor, and the for-head
container route's element-subscript leg; flips
dict/nested_container_element_alias). Two fences in
test_thir_wave_elem_len.py guarded exactly the widened positions
(their hazard was an iter-proto misrender; the container-route leg
renders the oracle's begin/end capture) -- byte-verified, converted.

Review round 15: safety and parity CLEAN with deep verification (the
move verdict funnels through the shared move-audit join; the readonly
-into-mutable hazard is sema-rejected upstream; the borrow-tracker gap
for subscript iterables PREDATES the diff; the live-name copy
divergence is warned and probed against CPython). Applied: the
dict/set flavor pins + the named non-container reject pin, the
local-alias element boundary for the member scan, the narrow-predicate
docstring reconciled with the _ptr_union_member_wide split it
contradicted, the stale still-REJECT class docstring corrected, and
four stale TODO entries marked landed/resolved.

### The tail-probe wave (2026-08-06)

Wave 25; 1 flip (dial -> 3423/3729): stepped slices at record @overload
getitem (the 3-part `::tpy::Slice{lo, hi, step}` initializer; the emit
already picked the spelling off the stepped flag; flips
calls/overload_getitem_both_slices, converting the wave-20 stepped
fence). The wave's other probes ended in parks and a bug: the truthy
Optional-FIELD condition needs the dotted-path narrow fact (the code
names the gap; PARKED), the tuple-literal unpack chains into REF_ALIAS
(PARKED), and the narrowed pointer-repr Optional[container] subscript
probe DIVERGED against the oracle -- because the ORACLE is broken (the
AST's narrowed-Optional unwrap misses the pointer-repr flavor and
drops to a raw operator[], skipping bounds; filed in BUGS.md with the
fix shape; the probe was REVERTED per the AST-first rule). Two recipes
filed: the Own-element tuple param subscript reads and the (AST-
blocked) narrowed-container receiver row.

Review round 16 (scoped: architecture/coverage/docs -- the 55-line
diff): architecture and docs clean (the reverted probe left zero
residue; the BUGS cross-reference verified verbatim). Coverage's
boundary ask was probed: an int-VARIABLE step is a supported bound and
ROUTES byte-identically -- pinned as documenting, with the
negative-step render asserted at unit level.

### The own-tuple/native-optptr wave (2026-08-06)

Wave 26; 2 flips (dial -> 3425/3729): Own-element tuple param subscript
reads (the tuple value-read gate's family check gains _own_record_tuple
-- borrow and storage coincide, `std::get<N>(p)` bare; flips
tuple_argpass_own_named_source_mixed, the filed recipe's shape) and the
whole-optional pass at native same-optional slots (`repr(opt_none)` ->
the bare `T*`; slot-equality-keyed since repr is generic over the arg;
flips records/container_repr_dispatch). The isinstance-TERNARY ctor
witness stays in the parked isinstance-in-conditions family.

Review round 17 (scoped): the inline native row extracted to the named
_native_optptr_name_arg predicate per the chain's convention, dropping
a dead runtime-check guard (the marker field never exists on a bare
NAME node -- coverage caught it); the str-element mixed-tuple flavor
probed as ROUTING and pinned documenting; the mismatched-slot boundary
is unconstructible (the callee's resolved param IS the arg's own
optional type). Docs clean; no faces spliced.

### The nested-opt/borrow-ternary wave (2026-08-06)

Wave 27; 2 flips (dial -> 3427/3729): the restricted NESTED ctor-arg
tail gains the None/str-literal value-opt rows (both temp-free,
mirroring the direct loop; flips match/poly_field_none), and the
borrow-call ternary arms land twice -- the pointer-reseat ladder's
ternary-of-borrow-calls rung (`b = &(((flag) ? (g.itself()) :
(h.itself())));`) and the record-ifexpr arm slice widening from
NAME-only to name-or-borrow-call arms (a `T&` call result is an lvalue
like the name arm; flips auto_move/move_borrow_reassign_consume, and
the old prvalue fence's mixed shape routes byte-identically --
converted). Also parked: generic_bounded_func (the REF_ALIAS
alias-decl frontier) and with_target_readonly_enter (branch-hoist
const).

Review round 18: safety verified the widened value categories
empirically (the ternary lvalue copy-constructs at Own-temp consumers;
literal-only rows admit no lvalue source) and flagged the reseat
rung's container flavor as incidentally unreachable -- pinned
documenting so a future gate loosening surfaces here. Coverage's two
boundary asks landed: the value-returning ctor-arm ternary reject and
the NAME-at-nested-value-opt reject. Tracked: a readonly-strip gap in
call_returns_cpp_ref's equality (harmless today -- a const mismatch
fails at the C++ step).

### The own-tuple-args wave (2026-08-06)

Wave 28; 2 flips (dial -> 3429/3729): Own-element tuple NAME args at
the `std::tuple<...>&&` slot. A FIRST attempt (a preempting arm +
free-ladder delegation) was caught by the corpus byte-diff CAPTURING
the still-live flavors of param_forward_own_copyable_warn and fully
REVERTED -- the byte-diff working exactly as designed. The corrected
build: _own_tuple_move_arg's element compare widens to per-element
modulo-Own (flips tuple_per_element_own_arg_copy_ok), and the new
_own_tuple_borrow_lift_arg row lifts a BORROW-form binding through the
F3 tuple_to_storage conversion, ordered after the move arm and
excluding storage-form bindings / Own-tuple params (flips
tuple_per_element_own_name_copy_warn). The still-live STORAGE flavor
(the AST's `auto(p)` decay-copy) is genuinely unmirrored -- a future
row, pinned as a fallback boundary. NB the reverted attempt's recipe
claimed an existing auto(p) arm; that was WRONG (the case always fell
back) -- corrected in the TODO entry.

Review round 19 (scoped): the copy-pasted shape-compare prologue
extracted into the shared _own_tuple_shape_match helper (both arms
call it before their distinct verdicts); coverage verified all three
units present, the mismatched-element boundary sema-unreachable, and
param_forward's continued fallback held by the every-run byte-diff.

### The long-tail singles wave (2026-08-07)

Wave 29; 7 flips (dial -> 3436/3729), five 1:1 cells off the tail:

1. copy_iter at the for-head (flips imports/tpy_builtin_internal_path):
   the AST's OwnIter/CopyIter peephole is the container route's owning
   rvalue capture; a copy_iter call admits directly (is_copy_iter),
   own_iter (consuming binding) and IntLiteral elems stay fenced. A
   first attempt keyed on is_native_iterable was reverted -- CopyIter
   is NOT in that classifier; the AST dispatches these on a dedicated
   registry predicate.
2. Module-qual ctor at a structural protocol slot (flips
   stdlib/io_read_size): the protocol arg-temp's init is a storage
   sink, so a `_module_qual_ctor_shape` init lowers under STORAGE and
   the marker result gate admits the F1-record -- the record-rvalue
   temp arm's twin. @dynamic slots keep their adapter fence.
3. bytearray aug-concat (flips tplib/requests_stream + _redirect):
   `got += chunk` on a bytearray LOCAL takes the same
   `bytes_concat`-and-assign render as owned bytes; the bytes row's
   local-only condition is kept mirrored (param dangling concern does
   not translate but has no witness). Converted the stale
   test_bytearray_aug_stays_ast fence to a routing pin.
4. Union field-write sources (flips union/union_field_assign +
   _nullable): an assign-narrowed same-union ptr-variant NAME lifts
   whole via to_value_variant (the divergent-read fence opens ONLY at
   the union-typed sink -- member-typed sinks keep the miscompile
   fence), and a ptr-variant-returning free CALL admits via the new
   `union_value_lift` use flag -- the ptr_opt_lift sink flag's union
   twin.
5. Stub-method container-call args (flips dict/warn_dict_copy):
   `a.update(make_dict())` / `a.update(copy(b))` -- the bare call under
   the cpp_template, STORAGE-threaded; the slot match is
   element-Own-blind (stub slots spell `dict[K, Own[V]]`).

Also probed and re-routed to the parked isinstance-conditions entry:
the datetime_zoneinfo pair's census tag (`binop.shape.==.record`)
names the operand family, but the blocker is the condition-scoped
narrowed-read rename (value-position `&&` chains install no inline
narrow facts) -- already filed. tplib/requests_pool's residue is a
module-qual setitem KEY, a separate shape.

### The kwargs/self tail wave (2026-08-07)

Wave 30; 4 flips (dial -> 3440/3729) + two parks:

1. Record-NAME print sink (flips kwargs_print_file_textio): `file=f`
   on an open() TextFile / user Writable record local is the bare
   lvalue under the pinned `::tpy::as_ostream(<sink>)` consumer --
   the module-var sink row's name sibling.
2. print flush= + empty-args file-only (flips kwargs_print_flush +
   _file_writable): a literal True appends `<< std::flush` (new
   THIRPrint.flush flag; sema pins flush= to a bool literal, so the
   guard mirrors a proven fact), False is a no-op; `print(file=s)`
   emits just the end token. Converted test_print_flush_kwarg_rejects
   (stale fence) to a routing pin. Empty-args with sep/end kwargs
   keeps deferring (gen_print's emit-nothing arm).
3. `return self` at `-> Optional[Self]` (flips records/self_type):
   the bare `this` token -- the receiver already IS the `T*`; both
   auto_readonly clones share it. Guarded to the sync-method self
   (self_cpp == "this", un-narrowed).

Parks: tplib/requests_pool's setitem KEY (2-piece: an owned-str
call-rvalue arg row + flush-position threading for the subscript
WRITE-target's index -- the drafted arg row was REVERTED as
unwitnessed); comp sources that are iterator-object factory chains
(stdlib/itertools_basic -- pre-statement arg-temp hoist + the same
not-in-is_native_iterable classifier chase as the for-head lane).
Probed and left on their parked families: the sgen dict-view tuple
rungs (gen_dict_items_mutate, tuple_optional_yield_compose), the er
comp (comp_array_error_return_fallback).

Review round 20 (waves 29-30): codegen-correctness and docs-sync
clean (the union-name lift, `return this`, bytearray concat and
copy_iter routes each traced to their oracles). Applied: the
modulo-Own type-args strip extracted to the shared _unown_type_args
(was hand-copied at the stub-slot gate and the container-literal
method-arg arm); the union field-write FormConvert's move flag forced
honest (to_value_variant deref-copies, so move=True there was a claim
no render can honor -- scoped to the convert, the storage-form
THIRMove arm untouched); a genuine fallback boundary (Own-elem slot)
plus the set leg added to the stub-container arg pins; a stale
TODO.md flip reference corrected. Skipped as established convention:
the fixture Int32 spellings and the was-a-fence comment phrasing.

### The copy/tuple-return wave (2026-08-07)

Wave 31; 2 flips (dial -> 3442/3729) + one 3-unit clear:

1. copy() of a ptr-variant union binding (union/union_copy_ptr_variant,
   3 of 4 units cleared; still marked -- the 4th is a branch-position
   ptr-union decl, the parked branch-hoist lane): the whole-variant
   deep copy via to_value_variant (the STORAGE FormConvert on the bare
   name), keyed on the BINDING type -- an assign-narrowed local reads
   as its member but still copies the variant; the plain-record copy
   row now excludes ptr-variant bindings (it had mis-captured them via
   the narrowed read type, saved from divergence only by the divergent
   fence raising inside its inner lowering); the slot-kind classifier
   admits the copy call as UNION_RVALUE.
2. Storage-form Own-element tuple NAME returns (flips
   tuple/tuple_alias_own_member_escape_ok +
   tuple_own_return_param_member): `return pair;` bare through the
   generic tail, gated on storage_tuple_locals + the shared
   _own_tuple_shape_match. A FIRST placement in the Own-storage-tuple
   arm was dead code -- the return classifies as the WIDENED value
   tuple (Own[record] elements admitted there), so the row lives in
   the ret_vt NAME rung. The borrow-form sibling is sema-unreachable
   (borrowed element into an Own slot is a SemanticError).

Probed and re-routed to parked families: exceptions/
finally_mutates_returned_local (the borrow-record-at-RECEIVER reject
is the documented REF_ALIAS design stop -- a prior general admission
was reverted for exactly this), none_safety/narrowed_field_print (the
dotted-path narrow fact), iterators/consuming_for_loop (the
iterator-decl residue), stdlib/re_basic (fstring interpolations over
multi-family method rets).

### The own-elem tuple-literal wave (2026-08-07)

Wave 32; 1 flip (dial -> 3443/3729): a tuple LITERAL at an
Own-ELEMENT tuple slot (`read_owned((A(1), A(2)))` at
`std::tuple<A, A>&&`) renders the spelled brace-init with each element
against its Own-peeled by-value slot -- the Own-storage-tuple RETURN
arm's arg twin, same storage-direct member rules (flips
tuple/param_own_tuple_never_consumed_warn; rvalue, movable-last-use
and copy() members dualgen-verified). tuple/tuple_ref_readonly is a
different family (a @readonly borrow-tuple RETURN of const-rooted
field reads) and keeps deferring, pinned. New park: the nested-def
rebind-slot pair is an EMIT-model limitation (_rejects_lambda_hoist
is the documented protection; slot allocation would need to scope to
the nested-def emit).

### The pointer-receiver getitem wave (2026-08-07)

Wave 33; 1 flip (dial -> 3444/3729): a pointer-slot LOCAL receiver at
a record-getitem field read (`acc[0].name` on a rebind-slot ArrayList
local) derefs at the name read (`(*acc)[0].name`) -- indirect_read
threaded at the PRECHECKED record-getitem construction, admission via
a ptr_recv_ok kwarg consumed only by the field-over-getitem gate (the
subscript arm's other positions keep excluding pointer receivers; a
first edit at the un-prechecked construction was dead code and was
reverted). Flips list/arraylist_move_nontrivial. The nested-tuple
subscript-chain sibling (tuple/tuple_own_nested_inline, `pp[0][0].fd`)
is a different receiver rung -- next.

Review round 21 (waves 31-33 + the tuple_per_element_own_name_move
harvest flip): codegen-correctness (live-probed the ptr-variant copy
as a genuine deep copy), safety-model (traced the divergent read-type
inert at the whole-variant sink; the _lower_copy_record exclusion is
genuine bug-avoidance -- Dog(pet) from a variant local would be
invalid C++), and the combined convention/docs pass all clean.
Applied from test-coverage: the own-elem tuple-literal arm's SAME-SITE
reject boundary (a ternary member -- neither rvalue nor movable name),
the movable-NAME element move witness ({std::move(a), 4}), and the
bare ptr-receiver subscript reject pin (ptr_recv_ok stays scoped to
the field-over-getitem gate). Skipped: the fold-on-third-site dedup
note (conditional).

### The assign-narrowed method-receiver wave (2026-08-07)

Wave 34; 1 flip (dial -> 3446/3729): an ASSIGN-narrowed ptr-variant
union NAME as a METHOD receiver (`c: Circle | Rect = Circle(5.0);
c.area()`) renders the inline bare-get read
(`(*std::get<Circle*>(c)).area()`) -- the field row's method twin
(THIRNarrowedRead receiver at the method arm; the SHAPE gate and the
ARG gate both resolve the receiver to the member sema retyped the
read to). The arg-gate half was caught by the pin, not the dualgen: a
witness fires at gate time, so a body can witness an arm and STILL
fall back at a later statement -- a dualgen fallback count of 1 says
which COUNT, not which BODY; identify the failing body before reading
a probe as a boundary. Flips union/union_assign_narrowing_method;
isinstance-narrowed receivers stay on their alias arms (pinned
witness==0).

### The F3 str-element wave (2026-08-07)

Wave 35; 1 flip (dial -> 3447/3729): an owned-str element joins the
F3 tuple family (_f1_tuple_element_ok) -- str is a value type
spelling `std::string` in BOTH forms, so every per-element conversion
rides it untouched. A SHARED-predicate widening: return literal, call
decl, arg pass and unpack positions all dualgen-verified before the
corpus run (the F3 machinery is position-blind by design; str was an
omission, not an exclusion). Flips calls/ref_strip_ctor_type_arg
(`return (str(p), p)` -> the borrow-tuple literal builder's spelled
`std::tuple<std::string, Point*>{...}` -- the ret.btuple_literal arm
existed; only the slot classification blocked it). StrView elements
stay out (views split forms), pinned.

### The opt-view field-return wave (2026-08-07)

Wave 36; 1 flip (dial -> 3448/3729): an owned-str FIELD read at the
value-opt view return (`return sub.value` through the narrowed ptr
local -> `return sub->value;`) lands bare in the
`std::optional<std::string>` return -- the field arm gates its own
receiver shapes, the row threads field_owned_str_ok so the result
gate admits the owned member. Flips
records/property_optional_field_local; StrView members type as views
and keep deferring, pinned.

Review round 22 (waves 34-36): both combined passes clean.
codegen+safety verified the wave-34 gates against constructed
divergence attempts (none reproduce -- the lowering re-checks), the
F3 str seam against the runtime's same-type short-circuit (no extra
copy), and the opt-view field guard against view leakage. Coverage
confirmed the F3 pin genuinely exercises four positions in one
fixture and the dial arithmetic across the harvest commit. Filed: the
triplicated assign-narrowed-union test (collapse onto one predicate
in predicates.py at next touch -- checks.py cannot import the
expressions.py original).

### The MIL tuple wave (2026-08-07)

Wave 37; 2 flips (dial -> 3450/3729): the ctor MIL's F1-tuple field
gains two rows. A storage-form-tuple-returning CALL stores bare
(`t(make_pair(5))` -- _storage_form_tuple_return, the AST's
needs_tuple_storage_lift call verdict; no tuple_to_storage wrap), and
an Own-param element at its LAST USE moves inside the literal wrap
(`{1, std::move(b)}` -- the elem_ok gate takes own_params and the
elem() builder wraps THIRMove). Flips tuple/tuple_own_call_field_store
+ tuple/tuple_param_yield_storage_arg. A copy()-of-Own-param element
stays out (the copy row is plain-param only), pinned via the
still-live flavor.

### The base-init ctor-default wave (2026-08-07)

Wave 38; 1 flip (dial -> 3451/3729): a materialized-default CTOR
rvalue at the base-init slot (`: Base(a, Fixed(5), 2)` -- sema fills
omitted defaults and kwargs into positional args before codegen, so
the base-init cell sees plain positional TpyCalls). The arg row
admits an F1 ctor rvalue whose own args are scalar literals/names
(the no-flush contract holds: such a ctor cannot register a temp).
Flips defaults/super_init_materialized_default; a ctor arg wrapping a
record field-read stays out, pinned.

### The bytes-ternary wave (2026-08-07)

Wave 39; 1 flip (dial -> 3452/3729): a bytes ternary mixing a VIEW
arm and a bytes-LITERAL arm (`b if b is not None else b"none"`)
renders the raw mixed ternary -- the span converts from the literal's
owned temporary (alive to the full expression's end), the whole
ternary stays BORROW, and the owned sink wraps it in bytes_copy.
Flips bytes/bytes_param_return; a literal + CALL arm mix keeps
deferring, pinned.

CORRECTION (same day): the first build of the arm keyed on ANY view
arm and broke test_bytes_result_rejected -- and that fence turned out
LOAD-BEARING: the PLAIN bytes-param flavor's AST emit is UNCOMPILABLE
(`return ((c) ? (b) : (bytes_literal_owned(..)))` -- span vs vector,
no common type; hidden because no exec witness exercises it; compile-
verified and filed in BUGS.md). The arm now keys on the value-opt
PARAM deref flavor only (_value_opt_view_param), the fence pin stays
as the bug's documenting boundary, and the wave commit went in red --
the corpus notification's exit code is NOT the pytest verdict when a
command chain follows it; check the tail before committing.

Review round 23 (waves 37-39 + the fix): both combined passes clean.
Coverage build-verified the BUGS.md uncompilable-emit claim and
confirmed the CORRECTION narrative against the two commits; codegen
traced the ternary's C++ overload resolution (why the value-opt
flavor compiles and the plain-param flavor cannot) and verified all
three arms reuse previously-vetted predicates. Applied: the BUGS.md
size tag ([MED] -> [MED small]).

### The discard-subscript wave (2026-08-07)

Wave 40; 1 flip (dial -> 3453/3729): the `_` unpack half desugars to
a bare subscript statement evaluated for its bounds check
(`::tpy::__getitem__(items, 0);`) -- the expr-stmt arm admits
non-slice subscripts and the element gate's DISCARD position takes
the record element lvalue (dropped, no alias escapes). Flips
tuple/tuple_literal_unpack_alias; a discarded SLICE subscript keeps
deferring, pinned via witness==0.

### The own-tuple-param return wave (2026-08-07)

Wave 41; 1 flip (dial -> 3454/3729): `return p;` of an Own-element
tuple PARAM (`std::tuple<A, A>&& p`) at the widened value-tuple
return -- the rvalue-ref binding is already storage form, so the bare
name rides the generic tail (the wave-31 storage-local row's param
leg; found by the census REFRESH after 25 flips of drift moved the
case's tag). Flips tuple/param_unpack_own. Also this wave: three
parks committed (hoisted container unpack targets -- 3 coordinated
pieces; Own[scalar] loop-var moves -- the value-move verdict split;
temp-demoted ctor inits -- blocked on attempt rollback), and the
fresh census maps 107 marked sole-blockers with the un-parked
remainder nearly all belonging to parked families on inspection.

Review round 24 (waves 40-41): clean, no findings. The reviewer
traced the discard-subscript dispatch (slices excluded at two
independent gates; checked-optional elements still raise), probed
both tuple-param flavors (the non-Own sibling is a pointer-repr
param routed by a different pre-existing arm -- structurally
unreachable by the new gate, so no boundary pin is needed), and
verified the C++20 implicit-move-on-return matches the AST's bare
render. All three parks' claims verified against the cited sites.

MERGE-CYCLE NOTES (2026-08-11, pre-squash): the residual 275
unmigrated cases decompose as ~107 marked sole-blockers (census
2026-08-07, ALL parked-family or filed-recipe on inspection) plus
multi-tag cases chaining into the same families -- the next session
needs a design round, not a re-measure. expressions.py crossed 13k
lines this branch (+1307); a split is DELIBERATELY DEFERRED to the
cutover consolidation (the file dissolves into the printer layer +
per-construct lowering modules there; splitting now would churn every
open recipe's line references).

SESSION CLOSE (2026-08-07): the autonomously grindable queue is
EXHAUSTED. The fresh census (107 marked sole-blockers) leaves no
mechanical single outside a parked family or a filed multi-piece
recipe -- the remaining tail is design-session territory (the parked
entries in TODO.md, each with its analysis and oracle). Session
total: waves 29-41, reviews 20-24, dial 3429 -> 3454/3729 (25
flips), one AST bug filed (the uncompilable bytes ternary), nine
parks with recipes.

DESIGN-ROUND WAVE (2026-08-11, thir-design): the attempt-poisoning
park RESOLVED-EMPTY by re-bisection. Repro 1 re-run on the
scope-leak-fixed base: the generator body now ROUTES (the session's
resumable waves cleared its old res.yield_type reject), an
instrumented AST+CFG before/after dump across the attempt shows zero
mutated leaves, and the fallen-back bodies re-emit byte-identically
-- the deref loss was THIR's OWN render, not contamination. Root
cause: value-opt narrows have no extraction alias (the AST derefs in
place), and value-opt-scalar loop vars / unpack targets never
registered in lc.value_opt_bindings, so narrowed reads fell through
to the plain-name arm and rendered the bare optional SILENTLY (sema
had retyped the read to the scalar). Fixed by registering the binding
at three missing sites: the resumable AsyncForAdvance loop var, the
resumable pass-1 tuple-unpack targets, and the sync for-head +
standalone unpack target loops -- the STANDALONE one was a latent
divergence already reachable on master (tuple[T | None, ...] param
unpack + narrowed read), found only by dualgen, not the corpus. The
_dict_view_iterable_ok value-opt leg is re-admitted; the still-defers
pin converted back to routes; new pins: narrowed loop-var yield
routes with (*val), items()-unpack sibling (sync + frame), standalone
narrowed target, view-flavor boundary defers. LESSON: both
"poisoning" repros (this and the H-ctor scope leak) were ordinary
bugs whose symptom moved with the routing set; treat "the attempt
changed the output" as a missing-registration or emission-order
hypothesis before reaching for rollback machinery.

ISINSTANCE-CONDITIONS WAVE (2026-08-11, thir-design, cells A/C/B of
the design memo): three flips. Cell A: multi-var `&&` compounds --
_compound_isin_hits shared scan, _multi_narrow_cond_info (N leaves,
distinct subjects), _lower_multi_compound_cond (the single-var arm now
delegates), _lower_multi_narrow_if (one alias per subject, no-else
gate); flip union/union_isinstance_compound. Cell C: isinstance
ternaries -- _ifexpr_isin_narrow_info (bare single-member check,
2-member subject, scalar result) + _lower_narrowed_ternary (per-arm
inline gets, else = the exact complement); _lower_isinstance_cond
moved to expressions.py; flip records/ctor_field_init_union; one
stale ifexpr fence converted. Cell B: Any compounds --
_any_narrow_cond_info grows the single-leaf &&/|| form; the && spine
installs the condition-scoped any_cast spelling via lc.narrow.spelled
(the poly-narrow regime, condition-scoped -- NOT a new rename
regime); flip any/isinstance_inline_and. VERIFICATION NOTE: the
park's "third rename regime must be designed" premise was STALE --
inline_narrowed/spelled existed; the cells were arm plumbing. STILL
OPEN in-family: cell D (expression-position &&-chains, the datetime
zoneinfo pair, held for a design look at leaf sequencing), and the
or-chain family (same-subject multi-leaf ORs with sema exhaustiveness
folds -- `(holds<A*>(v) || true)` -- union-fact branches, negation;
union_isinstance_or_chain + union_isinstance_tuple), a distinct
render family, NOT the cell-C complement shape.

CELL D (2026-08-12, thir-design): expression-position isinstance
chains DE-DESIGNED on bisection -- the held design question ("how do
narrow facts accumulate across leaf kinds outside a statement
context") dissolved: a probe matrix showed the if-position chain,
the expression-position chain with a narrowed FIELD read, and the
record-eq compare in if-position ALL already routed; sema's retype
plus the assign-narrowed receiver arm carry condition-scoped facts
at value positions with no install machinery. The only gaps were
two operand rows: (1) a condition-scope-narrowed union NAME at a
compare slot -- `_narrowed_union_compare_operand` (the
`_assign_narrowed_union_recv` invariant widened to value unions),
gating lt/rt as the member and rendering THIRNarrowedRead
(`std::get<ZoneInfo>(tz) == waw`), face
binop.narrowed_union_operand; (2) a union-FACT narrowed name at a
native/template union slot (`repr(tz)` under `tz is not None` -- the
slot substitutes to the SMALLER occurrence union) --
`_native_union_name_arg` admits nested-member occurrence unions, the
render staying the bare full-variant name. Flips
stdlib/datetime_zoneinfo_basic + _convert; boundary pins: alias-in-
scope uses the alias (row must not fire), un-narrowed union compare
keeps rejecting. LESSON (the streak again): the "design flavor" was
an artifact of reading the park instead of bisecting -- one dualgen
matrix settled in minutes what the entry framed as a leaf-
sequencing design problem.

OR-CHAIN WAVE (2026-08-12, thir-design): the isinstance-conditions
cluster's last family, three shapes in one cell chain. (A) Pure
or-chains (`isinstance(v, A) or isinstance(v, B)`):
_or_chain_narrow_info + _lower_or_chain_cond compose per-leaf holds
tests along the source `||` tree (a sema-folded exhaustiveness leaf
renders `true`); branch facts ride the shared narrow-if skeleton,
including the AST's DEAD else-extraction past a tautological chain
-- _chain_post_if_fact grew the or-chain form (both helpers moved to
predicates.py with _flatten_binop_leaves for import direction). (B)
Mixed bool leaves (`isinstance(v, (A, B)) or flag`): each `||` node
installs the left subtree's single-member COMPLEMENT (the AST's
false-branch remainder) for its right side; the complement-read
render is pinned. (C) Match guards (`case _ if isinstance(v, (A,
B))`): _lower_match_guard takes the bare-isinstance holds render
(face match.guard_isinstance); a narrowed-subject guard keeps
deferring (the alias read is not modeled there). Flips
union/union_isinstance_or_chain + union/union_isinstance_tuple; the
cell-A `or`-fence converted to routes. NOTE the two_member/negated_or
oracles commit DEAD extractions on unreachable paths (sema's facts on
folded chains) -- mirrored as-is per the oracle discipline, worth a
sema look someday but NOT via THIR.

MECHANICS-PARKS WAVE (2026-08-12, thir-design): two parks closed, one
pruned, 2 flips (dial 3462 -> 3464/3730). (1) requests_pool setitem
KEY: piece 1 (owned-str call rvalue at the Own[str] slot) had been
absorbed by intervening waves; piece 2 landed as flush threading --
the setitem statement arm lowers its write target under allow_temps,
the container-subscript arm forwards the enclosing use's flushability
into the INDEX (the general rule: allow_temps rides through
call-shaped positions), and the validator's SetItem arm accepts
INDEX-side arg temps (receiver stays temp-free -- tightened at review
to destructure rather than blanket-relax). (2) Hoisted container
unpack targets: the null-initialized pointer predecl
(THIRForEach.hoist_ptr_inits), per-iteration re-point via the
EXISTING frame_ptr_elem bind (reuse, not a new render), post-loop
reads via lc.pointers registered at the predecl; rode along -- the
tuple-iterable element gate admits container elements and the
container-element tuple CHAIN (`pairs[1][1]`) landed as a read row +
LIST-only print-wrap leg (dict/set narrowed out at review until
witnessed). (3) The iterator-decl residue entry was STALE -- all
three listed cases already unmarked and clean; pruned. Review round
(2 agents): the validator destructure, the aug-assign index-temp
boundary pin, the false method-receiver-pin claim fixed (that flavor
rides the pre-existing tuple_elem_subscript arm), the
_lower_hoist_predecls fifth-flavor-param fold note updated.
REMAINING iterator lane: the itertools comp-source park only
(classifier chase + pre-statement flush + owning generic capture).

ITERTOOLS COMP-SOURCE CELL (2026-08-12, thir-design): the last
iterator-lane park, and its premise DE-DESIGNED -- the parked
"classifier chase" (@native iterable records not in
is_native_iterable) was wrong: itertools is pure-TPy generators. The
chain-walk landed five widenings: the comp route's module-qualified
genfac twin (TpyMethodCall arm); _genfac_like_call in predicates.py
-- the overload-seam-aware verdict (a stub fi carries
is_generator=False while the impl is the generator; the protocol
Iterator[T] return admits, and sema FORBIDS that return type on
non-generator user functions, so the widening is safe by
construction) shared by the comp route, the marker gate's
iterable_gen, and the arg-temp leg; the gen-factory ref-slot arg temp
(argtemp.genfac_ref_slot -- `auto __tmp_N = count();`, the
readonly-ref-generator flavor of gen_call_arg's is_temporary_expr
hoist); protocol_hoist widened to generic_qualified callees (the
first-pass literal hoist runs for generic module callees -- the
oracle's `auto __tmp_4 = std::array{..}` + `cycle<int32_t>(__tmp_4)`);
and the marker ladder's lambda row. Flip stdlib/itertools_basic; one
stale fence converted (test_thir_wave_compcall's combinator reject).
Boundary pins: the genexpr flavor defers on its own lane. ALL THREE
mechanics parks from the committed order are now closed.

ITERTOOLS CELL REVIEW ROUND (2026-08-12): the "safe by construction"
claim in the cell's ledger entry above is CORRECTED -- @native /
@cpp_template callees (map/zip/filter/reversed/iter) return
Iterator[T] with is_generator=False and are exempt from sema's
protocol-return prohibition, so _genfac_like_call now EXCLUDES them
(native_function / cpp_template / is_stub) and matches the protocol
by qualified name (typing.Iterator, not the bare name -- a user
@dynamic protocol named Iterator would have misclassified). Lockstep
completed: the TpyCall comp leg and the arg-temp leg's CALLEE gate
now ride the shared predicate. Pins added: the generic_qualified
literal hoist (argtemp.marker_protocol_literal under cycle), the
genfac-arg-at-non-generator-callee boundary.

UNION-SPLITS CLUSTER (2026-08-13, thir-avoid-list): avoid-list item 1
closed end to end -- every case in the fresh census flipped (~10; dial
3465 -> 3474 with the long-tail cells below). Cells: (1) wrapper
literal args -- the container-literal hoist row in the plain free-call
ladder + the coerced-int bare pass (the wrapper's converting ctor
absorbs an INT literal; other literal kinds type at the member). (2)
property-union borrow -- the ret_union_borrow prescan fact: a property
getter returns the STORAGE variant by reference (the signature layer's
is_property_getter arm), so the return is the bare self-field read and
the consuming decl lifts via to_[const_]ptr_variant. (3) slotless
reseat -- PtrSlotKind.UNION_INLINE_SLOT declares a fresh value-variant
__slot_N at the reseat feeding to_ptr_variant; the emplace flavor
keeps UNION_RVALUE, the member-lvalue (address) flavor stays AST. (4)
branch copy-decl -- the branch-first decl gate narrowed from a blanket
reject to rvalue-reassigned names; a copy of an isinstance-narrowed
union name reads the pre-narrow binding via lc.narrow.subject_union.
(5) wrapper value-call chain (4 legs): _ru_wrapper_value_call_arg (the
non-generic already_union twin of the Own-call argtemp row), the
call.wrapper_value_ret result-gate rung, wrapper-union GLOBALS seeded
read-only (direct storage -- pointer_globals excludes needs_wrapper),
and the top-level literal init's assign-sink ru-literal render. (6)
builtin value nominal members -- _builtin_value_member admits e.g.
basic_slice into _eligible_value_union (excluding the STR/BYTES type
CLASSES, not qnames -- the review caught tpy.String slipping a
qname-keyed exclusion). (7) the narrowing-divergent pair -- the
"BUGS-blocked" claim verified SINK-SPECIFIC: member-typed sinks stay
fenced (the miscompile family); whole-variant sinks thread
allow_union_divergent (print STR row, the str()-ctor slot keyed
structurally on the resolved overload consuming the whole union, the
same-union name reassign at both assign arms). LESSONS: (a) a fence's
blanket claim ("no green case can exercise it") deserves a sink-by-
sink re-read -- two green corpus cases sat behind it; (b) three stale
fences converted to routing pins in one branch (narrowed-branch-copy,
non-generic-Own-call, borrow-result-bind) -- when a pin's stated
reason is exactly what the change removes, conversion is the wave
step, not a review afterthought.

LONG-TAIL CELLS (2026-08-13, thir-avoid-list, same branch): the
kwargs-temps avoid-list item measured EMPTY (zero marked cases, zero
temp/flush tags -- dissolved by intervening waves) and make_adapter's
residue lives inside async cases (the sink, opened last), so the
expr.call sole-blocker tail opened instead. Landed: len over a
str/bytes-returning call arg (the family restriction guards pointer-
locals, which an rvalue call result never is; a record-with-__len__
call rides the sema-resolved dunder lane); the ref-returning
@error_return callee at the raw er-bind (call.er_ref_bind -- the
alias-bind arm already owned the render, the result gate was the only
gap). PARKED (design): expression-position POLYMORPHIC isinstance
under && (the short_circuit divergence) -- the &&-RHS read needs a
cast-driven spelled-install regime at expression positions; the
_poly_isinstance_value_info docstring documents the exclusion.

LONG-TAIL CELLS 2 (2026-08-13, thir-avoid-list): three more expr.call
sole-blocker cells + the res.btuple_source pair. (1) Own-tuple
decay-copy args: a still-live STORAGE-form Own-element tuple NAME at
the `std::tuple<...>&&` slot renders the C++23 decay-copy
(`sink(auto(p))`, new THIRDecayCopy node); keyed on the slot's
is_owned_movable (ALL non-value elements Own -- the review round
caught the any-Own key admitting a MIXED slot the AST never
decay-copies, and the pinned boundary probe confirmed the guard
load-bearing). (2) dict[K, V]([tuples]) instantiation: the documented
dict exclusion of the container-literal instantiation arm opens --
elements retarget to tuple[K, V], a union V absorbs via the variant
ctor; the stale dict-over-a-literal fence converted. (3) Owning-tuple
frame slots: the OWNING_TUPLE_SLOT third signal becomes a real owning
frame_slot (general emplace write + a call.own_tuple_storage_ret
result rung + storage_tuple_locals registration so element reads
render VALUE-form off the BINDING -- the dot-vs-arrow divergence the
byte-diff caught live is the binding-form defect class again); the
subscript btuple lift (tuple_to_pointer over a container element) is
the reseat_lift's frame twin; two stale fences converted, and a
first-draft emplace rung proved DEAD (the classification alone routes
the shape -- disable-and-dualgen caught it). LESSON: the owning-slot
classification is correct BY CONSTRUCTION because it defers to the
shared AST-side FrameLocalKind instead of re-deriving form from types
-- the right shape for every frame-classification mirror.

LONG-TAIL CELLS 3 (2026-08-13, thir-avoid-list): three more sole-tag
pairs. (1) sgen ptr-tuple loop elements: `_sgen_loop_var_ok` admits
pointer-repr tuple elements; the binding form registers per the
SKELETON's dispatch -- the builtin-NativeIterable branch (list / dict /
set / views AND Span / Array / varargs) registers
storage_form_tuple_locals (dot member reads), a direct-iterator source
yields the BORROW tuple (arrow) and stays out. The first cut keyed a
4-container whitelist; the review round re-keyed it on the AST's
actual predicate pair (is_native_iterable + not direct-iterator,
checked on the RAW element like the AST's registration site) and the
Span flavor is unit-pinned. The tuple-yield gate also admits a
borrow-form NAME relayed whole (`yield pair`). (2) wrapper match
subject sources (M4c): a hoisted pointer-local subject derefs into
the alias, a borrow-returning call subject binds by reference (the
`_wrapper_borrow_return` disjunct is ablation-verified load-bearing
for the non-generic `-> Expr` convention that call_returns_cpp_ref
cannot see), and the container-method element-literal row widens to
generic wrapper instances via _wrapper_union_like. (3) global-slot
receivers at record __getitem__: the receiver admission takes
`ptr_recv_ok` for pointer-slot GLOBAL names (`(*g)[i]` at top level
and in function bodies); the sibling call sites (write-target /
borrow-decl / field-over-getitem) deliberately keep the exclusion --
extend them when a witness appears. Stale parked-family notes naming
gen_dict_items_mutate / tuple_optional_yield_compose are superseded
by these entries.

LONG-TAIL CELLS 4 (2026-08-13, thir-avoid-list): the with-target and
top-level unpack pairs. (1) Optional-enter with targets in resumable
frames: the `P* m;` frame member takes the FRAME_FIELD bare-pointer
bind (`m = __ctx_N.__enter__();` -- the AST's frame-resident bind is
type-independent, so gate == classification predicate and no
Optional-enter flavor can diverge at the bind); post-suspension reads
ride the opt_ptr classification's pointer bindings; the type consults
frame_local_types (the target may not be in `declared` yet). The
value-repr Optional enter keeps deferring (pinned). (2) Top-level
unpack-alias global writes: the global-slot write's lvalue tail gains
the address-of catch-all for a plain LOCAL name (the desugared
top-level tuple unpack, `a = &(__unpack_0_0);`), same-type gated; the
subclass source keeps the reject (polymorphic arm's business, pinned)
and the review round excluded lc.pointers sources -- an IMPORTED
pointer-slot global sits in lc.pointers, not global_ptr_slots, and
the AST copies its bare pointer rather than taking the address-of (a
latent divergence caught structurally, no witness existed). Also
parked: the poly-self narrowed-resume pair (the dynamic_cast install
regime across resume BBs, filed with the expression-position poly
item) and the overload ret-mismatch pair (a ret-mismatch +
db-isinstance + generic + auto_readonly composition -- a dedicated
overload-lane wave).

LONG-TAIL CELLS 5 (2026-08-13, thir-avoid-list): union frame slots +
in-branch top-level writes. (1) Union frame slots
(isinstance_union_frame, superseding the stale parks-registry line
above naming it a _narrow_binding_supported fence): a ptr-repr union
generator local is an ordinary frame_slot<std::variant<...>> -- the
classification widens WITHIN the shared AST-side FRAME_SLOT verdict
(never re-deriving it), the union-element pop admits at the STORAGE
sink (method.container_union_ret, shielded downstream at sync decls --
pinned), and _narrow_variant_cpp threads the R1c slot deref so the
condition and extraction share the (*t) spelling (the bare cond was
the one divergence, byte-diff-caught). Value/wrapper unions keep the
VALUE-verdict defer (pinned). (2) In-branch top-level global-slot
writes: the review round REFUTED the first cut's in_branch key with
two reproduced divergences -- the AST drops `static` ONLY inside
for-each bodies (its sole namespace-push site; if/while/with/try keep
it), and an imported pointer-global copy is bare in FORM but
QUALIFIED in spelling. Re-keyed on lc.in_for_body (set at the foreach
body lowering when elem_type is present) with the global_cpp exclusion
on the ptr-copy row; the if-scoped write and the imported-copy flavors
are boundary-pinned deferring, and the earlier fence conversion that
encoded the wrong premise is reverted. The mirrored for-body render
exposes the AST's block-scoped-slot latent UAF (BUGS.md, wording
corrected to the for-only premise). LESSON: "current_ns leaves
global_ns in the branch" was a spy result over ONE case generalized to
all branches -- key a mirror on the AST's PREDICATE (the namespace
push site), never on one probe's coincidence.

LONG-TAIL CELLS 6 (2026-08-14, thir-avoid-list): the match-subject
pair. (1) Scalar field-chain match subjects: the route's non-name kind
gate widens to if_elif / switch_primitive / switch_enum (the
`auto& __match_subject_N = <chain>;` bind is kind-independent on the
AST side -- one shared bind site); the scalar tail reads non-name
subject types via get_expr_type; guarded SWITCH-kind field matches
route too (guards never demote fixed-int/enum subjects out of the
switch kinds -- only the chain scalars demote to the excluded
if_elif_guarded; pinned with the subscript-chain flavor). Flips
gen_method_match_yield; the dump test's guaranteed-fallback fixture
(a subscript-chain match that now routes) re-seats on the mixed-tuple
frame shape. (2) ptr-Optional field captures in resumable matches
(gen_match_optional_field): the capture binds the registered P* frame
member through optional_to_ptr; the admission threads an opt_frame
exemption (hook mode only). THE REVIEW'S COMPILED REPRO: the first cut
dropped the AST's subject-LVALUE guard -- a call-rvalue subject with
the capture and a suspension in the arm is a silent UAF where the AST
raises "would dangle across a suspension", and NEITHER the byte-diff
nor the ratchet can see it (the AST refuses the shape, so no corpus
case can exist). The exemption is now withheld when subject_rvalue
(both the gate and the bind arm), pinned deferring. LESSON: when the
AST path REFUSES a shape with a diagnostic, the mirror's job is to
fall back so the diagnostic surfaces -- an admission that compiles
what the AST rejects is invisible to every corpus detector, so hunt
for the AST's user-facing errors around any new exemption.

LONG-TAIL CELLS 7 (2026-08-14, thir-avoid-list): generic simple
generators, both flavors. The generic-RECORD receiver gate and the
generic-FUNCTION type_params gate drop on one verified argument: every
type-spelling position in the sgen peephole (template header, counter
decl, typed value loop-var copy, iterator-slot decltypes, yield slot,
__val binding) is SKELETON-owned in gen_generators.py and computed
identically whether or not a THIR leaf is present -- so the drops
expose only leaf renders, each type-neutral (auto/auto&&) or gated by
its own arm (Own[T]/readonly[T] yields, generic tuple NAMES, mixed
tuples, movable T elements all fall back rather than mis-render;
boundary-pinned). A generic TUPLE yield takes the resumable
generic-tuple builder's to_val_or_ptr brace-init
(sgen.tuple_yield_generic). Flips gen_method_generic_multi_param,
gen_method_temp_receiver_generic, gen_ref_compose; four stale fences
converted (the fourth -- a vacuous subset assert in genarg2 -- found
by the review round). The review also fenced the BOUNDED-T iterable
with a pointer-repr tuple element: the AST's storage_form registration
resolves the T bound at its site, the sgen mirror keys the raw type,
so the flavor rejects until a bound-following witness lands (the
dot-vs-arrow channel again -- the third binding-form near-miss this
branch; the mirror MUST key the AST's resolution, not the raw type).

LONG-TAIL CELLS 8 (2026-08-14, thir-avoid-list): Optional globals +
Char ctor args. (1) ptr-repr Optional globals seed as pointer slots:
the documented exclusion lifts for the F1-record flavor -- the AST's
classification takes the raw non-value check and its slot spelling
unwraps to the INNER (`Point* g{};`, review-verified end to end incl.
the pre-existing Optional-aware top-level writes and sema's rebind
prohibition closing the function-write flavor); all read shapes ride
the seeded pointer binding (bare copies, null tests, narrowed derefs;
the direct-narrow and imported flavors pinned by the review round). A
first-draft reseat row proved DEAD under ablation. Optional[container]
globals stay honestly unseeded (a future arm). (2) Char args at
scalar type-ctors: the slot check admits Char slots and the arg check
Char-valued args (both ablation-live; the cast lives in the ctor's
@cpp_template so the bare arg render is exact; Char(s)-from-str is
native_function-excluded, and the review verified the owned-str call
site is behavior-unchanged). Flips int/type_constructors +
warn_auto_move_narrowed_optional_deferred_from_global.

LONG-TAIL CELLS 9 (2026-08-14, thir-avoid-list): comp slots + str
globals (supersedes the round-6 lane-V "Optional/str globals stay
unseeded" record). (1) Comprehension inits at container global slots:
the write arm forwards to the shared comp machinery with the
frame-branch pointer filter (the static slot line wraps the
stmt-expr); scalar and dict comps route, unrouted comp SHAPES gate
inside the comp lowering. (2) Write-seeded str globals
(warn_global_walrus_view_rebind flipped): plain owned assigns + the
str-local read duality; dualgen caught the self-append peephole firing
for the seeded global where the AST's global-write arm RETURNS EARLY
past the fold -- the exclusion mirrors exactly that asymmetry (the
TpyAssign fold site is guardless on BOTH sides, verified). Bytes
globals stay unseeded deliberately (the AST's bytes view-assign bug,
BUGS.md) and native-linkage str globals are excluded (THIRStrAppend
spells the bare TPy name). String-global writes, the aug-assign
flavor, and dict comps at global slots pinned by the review round.
Three stale guaranteed-reject fixtures re-seated on bytes WITH a
comment naming why bytes stays out -- the rot mechanism that already
hit them once.

LONG-TAIL CELLS 10 (2026-08-14, thir-avoid-list): Array
instantiations + container borrow aliases + a clean revert. (1)
list(arr)/set(arr): the instantiation arg-family gate admits Array
names (construct<...> over the deref'd pointer-slot global); the
function-body flavor defers at the last-use gate (Array has no
consuming __iter__ -- probe-verified honest, tag-pinned). (2)
Container borrow-call alias decls (generic_bounded_func flipped): the
record borrow-call row's container twin (`std::vector<T>& items =
identity<...>(t);`) with ref_alias registration; the const fact keys
BOTH spellings (declared ReadonlyType AND fi.is_readonly -- the
review's latent-trap catch); the reassigned fence and the
mutation-through-alias proof pinned; the free-call result gate gains
the sink-global BORROW_BIND container rung (one sink byte-verified,
the ternary sink fenced upstream). (3) The storage-opt CONST loop
element: attempted, found to un-fence THREE load-bearing guards with
unverified downstream renders, REVERTED CLEAN, and parked with the
finding -- the fences did exactly their job, and the park names the
set that must convert together.

LONG-TAIL CELLS 11 (2026-08-14, thir-avoid-list): the dyn-getattr and
error_return singles wave -- 5 flips (getattr_concrete,
borrow_local_alias, expr_unwrap, nocopy_result_storage,
finally_mutates_returned_local). (1) A str/bytes-valued dyn-getattr
field as a method receiver mirrors the view-field property arm (the
synthesized __getattr__ call composes under the view family); the
scalar-valued flavor tag-pinned deferring. (2) The er FIRST-DECL alias
bind (`T* v;` predecl + `v = &(::tpy::unwrap_ref(*tmp));` -- the
auto-propagate body with no try hoist): routed only when the
_is_const_indirect verdict is provably non-const (readonly sema type /
readonly callee / a method receiver beyond a plain mutable name reject
rather than mirror the const-set topology). (3) A ref-returning call
at the Own[record] STORAGE return renders bare and the slot copies --
the same RECEIVER+passthrough lowering as the borrow-slot arm; the er
receiver flavor (`return positive(x, y).updated()`) composes on the
er.unwrap stmt-expr. (4) The er-assign FIELD target
(`this->p = ::tpy::unwrap_ref_move(*tmp);` -- THIRErrorReturnBind
grows a target expr); container/Optional field targets fall back
fail-safe at their RECEIVER-use lowering; a stale scalar-field fence
converted to a routing pin. (5) A T&-returning record call under a
member read composes bare via a DEDICATED field-recv use flag (the
reverted blanket record-at-RECEIVER row stays reverted; the flag never
leaves the field-read receiver slot -- position-scoping pinned by a
value-sink reject twin).

REVIEW ROUND (2026-08-14, long-tail cells 11, base 86630777e): full
suite green via rpytest (11621 passed, dial 3512/3730, move-verdicts
and binding-facts clean). safety-model CLEAN (all five ownership arms
verified structurally -- notably: implicit-move-on-return never applies
to a call's lvalue-ref result, so the storage-return copy is
guaranteed; the field-recv transient claim holds structurally because
`_field_receiver_ok` hard-requires a NAME receiver on every persistent
alias path). codegen-correctness found ONE reproduced Critical: the er
FIELD-TARGET arm missed `_reject_nested_error_return_arg`, admitting
`self.p = make(inner(n))` -- a shape whose AST render is itself
ill-formed C++ (pre-existing, now filed in BUGS.md); the gate is added
and the fallback pinned, keeping both paths in lockstep rather than
silently diverging from (or blessing) the broken oracle. test-coverage:
one missing boundary pin (ret.record_ref_call_storage) added via the
ternary-source reject twin; the inst-arg boundary was already carried
by the pre-existing borrowed-return pin. Four fences on the borrow-call
field-read shape (incl. the DesignStop class) converted to routing pins
after the transient-vs-binding distinction was verified -- the er-callee
flavor dualgen'd byte-identical before conversion.

LONG-TAIL CELLS 12 (2026-08-14, thir-avoid-list): six cells, nine
flips, dial 3512 -> 3520/3730 (return_mixed_safe_sources trio via the
StrView-instantiation value-sink fold; cross_module_iter via the
Own[list[T]] literal-arg inline row; gen_generic via the generic-kind
imported-name refinement -- a LOCAL generic shadowing a builtin spells
the bare explicit-targ call, mirroring the tail's
conditional-qualification arm; gen_tuple_loop_var_relay +
tuple_local_own_member_yield via two tuple-yield rungs -- the
container-elem tuple_to_pointer lift and the storage-form Own-elem
NAME, the latter's first draft BREAKING two load-bearing fences on
byte divergence until the slot's has_pointer_repr_element
discriminator -- the AST's own storage-vs-borrow split -- was added;
str_pending_list via the target-typed return list-repeat, whose
untargeted first draft demoted to the Array flavor -- a probe-caught
real divergence). REVIEW ROUND: full suite green (11632 passed);
codegen-correctness verified all five arms against the AST oracle
(the storage-name rung's const flavors provably fold identically; the
zero-arg StrView admission is gate-imprecise but render-safe -- the
deeper dispatch rejects); test-coverage caught a LEFTOVER DEBUG PRINT
in the working tree (removed) and asked for the Own[dict] literal-arg
boundary pin (added). Lesson repeated twice this wave: the byte-diff
catches a wrong draft only when a fence or probe covers the shape --
both first drafts were corrected pre-commit by fences/probes, not by
the corpus.

LONG-TAIL CELLS 13 (2026-08-14, thir-avoid-list): the deref pair + the
view-key family -- 4 flips (deref_view_rebind_invalidates,
dyn_box_optional_readonly_deref, strview_dict_key, bytesview_dict_key),
dial -> 3524/3730. (1) The deref-view isinstance narrow admits
REBIND-SLOT pointer locals (`&((*b).__deref__())` cast arg); the
in-branch reseat invalidation rides sema's per-node deref_narrowed_to
marker -- probe-verified pre-rebind alias read + post-rebind plain
chain, and the old "defers whole" fence converted. (2) The shared
deref receiver resolver gains the markers-clean FIELD flavor
(`(*this->val).__deref__().speak()`); the resolver serves BOTH twins,
so the field-READ twin got its own pin after review flagged it. (3)
The view-key family: _dict_key_shape_ok admits StrView/BytesView +
owned-bytes keys; the subscript index and membership needle retag
bytes literals to the static view spelling; Own[view] insert slots
admit LITERALS only -- the NAME flavor's copy+move view temp is
unmirrored, dualgen-caught as a divergence and gated out with a
boundary pin (found via TWO followup leaks: the pending-str resolver
seeing through Own, then an unidentified row -- closed with an early
reject at the family arg gate). Also: the Optional-container raw
subscript READ was NOT blessed -- the AST render drops
index-normalization + the bounds panic (the fi lookup runs on the
un-unwrapped Optional), filed in BUGS.md with the widening cleanly
reverted. REVIEW ROUND: suite 11641 passed + one stale
guaranteed-fallback fixture re-seated; codegen-correctness CLEAN (all
three view_key_target consumers mirrored; no owned-bytes leak; the
field-read twin probe-verified); test-coverage's three
admitted-but-unwitnessed shapes each dualgen-probed IDENTICAL and
pinned.

LONG-TAIL CELLS 14 (2026-08-14, thir-avoid-list): the protocol-args
wave -- 3 flips (bytes/hash, iterable_method_params +
generic_infer... partial), dial -> 3527/3730. (1) The bytes family
joins the native protocol-slot value slice (`hash(b"x")`). (2) The
Iterable-param stub args complete: _protocol_bare_name_arg joins the
view + container family gates, and its new Iterable[Own[T]] leg is
the structural complement of _protocol_arg_slot's documented
exclusion -- the reviewer verified the own_iter rewrite requires a
resolvable record for __iter__, which a protocol type never produces.
(3) The instantiation-arg protocol-name row, hardened post-review
with the slot's own protocol check (every reachable single-arg
cpp_template ctor is Iterable-shaped today -- the cross-check makes a
future non-Iterable stub re-verify instead of silently reusing the
bare render). (4) The open-T set receiver row (mirrors the list
family's admission; per-method gates unchanged -- reviewer-verified
low-risk). TWO SPECULATIVE EDITS BUILT AND ABLATED as dead (a
tuple-literal row in the generic T branch + a peel in
_tuple_literal_arg): the minimal fixture routed via the pre-existing
btuple.value_arg row, the ablation left the corpus identical, and the
reviewer confirmed zero residue -- ablate-don't-argue, again. Suite
11650 passed; move-verdicts and binding-facts clean.

LONG-TAIL CELLS 15 (2026-08-14, thir-avoid-list): the generic-tuple
val_or_ptr track opens -- 2 flips (tuple_generic, tuple_own_element),
dial -> 3531/3730, plus the vararg/generic wave before it (2 flips:
varargs_field_subscript_arg via BORROW_BIND ref-pack elements;
generic_func_forward_type_param via the borrow-container call
passthrough + the same-T field row -- the passthrough also routed two
UNTARGETED fence shapes, both converted after byte-verification).
Track cells: THIRSubscript.elem_ref (`tuple_elem_ref(std::get<N>(p))`
-- _gen_subscript's TypeParamRef arm); the borrow-form tuple NAME at a
substituted mixed slot with the STORAGE-form exclusion (the
binding-form defect class caught by the cell's OWN dualgen boundary
probe -- the gate now threads storage_tuple_locals); the Own-peeling
copy elem row; the storage-tuple record element bare read. REVIEW
ROUND: suite 11656 passed; reviewer flagged two
currently-unreachable-but-fragile gaps, both hardened at the gate
(the Optional-element guard on the bare read; the second-registry
note on the exclusion) and the elem-type derivation unified onto the
routing-eligibility source. Also filed earlier this stretch: the
ref-pack pointer-name `&xs` T** AST bug (both paths, toolchain-caught).

LONG-TAIL CELLS 16 (2026-08-14, thir-avoid-list): the ASYNC SINK opens
-- 3 flips (async_bind_run_sync, async_alias_finally_suspend,
truthy_frame_cond), dial -> 3534/3730. (1) Coro-frame locals: the
deferred-erasure decl (`std::optional<__coro_add_one> c = add_one(41);`)
with the consumer erasure riding an existing adapter row. THE REVIEW
CAUGHT TWO REPRODUCED CRITICALS in the first draft: the arm admitted
ERASED Own[Cancellable[T]] bindings (dropping make_adapter entirely --
uncompilable) and spelled the frame UNQUALIFIED cross-module; fixed by
gating on ConcreteCoroType + the shared sub_struct_qualname helper
(renderer-free slice: no owner, no targs -- those flavors need the
codegen TypeResolver and stay gated). (2) The resumable alias-bind
gains name/frame-slot sources (`ys = &((*xs));`). (3) The resumable
param-reassign-copy gate: dropped for ASYNC (the frame member respells
owned at the skeleton; str/bytes/BigInt dualgen-verified), KEPT for
GENERATORS -- the suite caught the sgen peephole's capture respell
diverging, so the removal was narrowed rather than reverted. Two more
fences converted (the run-arg bound-handle, the while-head str). The
reviewer also confirmed the branch-scope dict restore and the
frame-classification reasoning (`is_owned_in_coro_frame` is
reassignment-independent).

ASYNC SINK CELLS 2-3 (2026-08-14, thir-avoid-list): 4 flips
(poll_in_field, asyncio_queue, async_for_narrowed_optional,
async_bind_rebind), dial -> 3538/3730; review round 9 clean on
codegen/safety/parity, two pin-idiom fixes applied. (1)
return.consuming_self_field gains `_csf_pair`: a BUILTIN-record field
at an Own[same-type] return slot (`_slot: Poll[Int32]` ->
`Own[Poll[Int32]]`) renders the same bare `return
std::move(this->_slot);` as the scalar families -- keyed on the
IDENTITY pair, container/str/bytes excluded (review says those AST
renders may match too; residue noted in TODO). Two speculative legs
(the value-record _plain_value widening + the ret Own-unwrap disjunct)
were ABLATED after `_csf_pair` covered the witnessed shape. (2) The
await INLINE gate admits Own[value] slots (Queue[Int32].put): the
emplace arg is exactly the sync `gen_call_arg` render, so the fix was
`temp_args=True` at the INLINE arg loop -- the Own-slot copy+move
`auto __tmp_N = i;` flushes before the suspend line; the Own[str]
slot stays fenced (owned brace-init temp unwitnessed at this
position). The first probe DIVERGED (bare `i` where the oracle
hoists the temp) -- the gate widening alone was NOT the cell; the
flush plumbing was. (3) res.for_narrowed_optional converted from
reject to DEREF-STRIP: the begin_end skeleton owns the narrowed
value-Optional unwrap (`_maybe_unwrap_narrowed_optional` over the
leaf render, gen_async ~4530), so the leaf must supply the BARE name
-- the rung predated the value-opt arms whose deref'd name would now
double-deref (`(*(*s))`, seen in the ablation probe). (4) The sync
concrete-coro handle rebind arm (the parked cell CLOSED): the decl
admits reassigned handles, rebinds mirror `_gen_concrete_coro_write`
-- call source re-emplaces (THIRFrameSlotWrite), name source is the
move pair (THIRCoroHandleMove, `d.emplace(std::move(*c)); c.reset();`
-- optional's move-assign is deleted under reference members);
self-write stays rejected. The still-defers pin whose reason this
removed was CONVERTED to the routing pin per the wave rule.

ASYNC SINK CELLS 4-7 (2026-08-14, thir-avoid-list): 5 flips
(async_method_isinstance_self, await_tuple_own_unpack,
async_method_typeparam_on_generic_class, tpy_executor_smoke,
tpy_executor_wake_dispatch), dial -> 3543/3730, sink 12/18; review
round 10. (1) Resumable poly-self if-init narrow: the gate's
"__self_narrowed collision" exclusion guarded the ALIAS emissions,
not the if-INIT form this arm mirrors (`__self_ptr =
dynamic_cast<...>(&__self)` -- no frame-field collision); dropped
for the single-fact/then-only slice, plus the readonly-method
`const_locals.add("self")` seed the resumable path was missing (the
sync/simple-gen seeders' twin -- the first probe diverged only on
the missing `const`). NB the "mutable flavor" pin first read
readonly because sema Phase-2 INFERS is_readonly for a non-mutating
method -- the fixture must actually mutate. (2) The generic
Own[T]-slot tuple LITERAL gate row: the lowering row
(own_btuple_literal, consuming storage lift) already existed; only
the gate blocked it. Bad element ownership is SEMA-rejected
(check_own_lvalue_into_own runs on the substituted slot), so the
gate admits by shape alone. (3) Generic async METHODS at factory
positions: dropped `fi.is_async and fi.type_params` from
_plain_method_fi_ok -- the call spells inline (member targs, the
sync generic-method render); frame-type-spelling positions gate
generics themselves (decl arm / res.await_generic). The
bound-handle flavor (`m = b.echo(5)`) also routes: the SKELETON
owns the templated frame spelling. (4) The `_builtin_value_record`
family (tpy.coro.Waker): a non-native registry ValueType record
admitted at the decl slot, call STORAGE result, method receiver,
and pass-through arg; the first cut lacked the NATIVE guard and
admitted Span projections -- two span fences caught it, the guard
is `ri is not None and not ri.is_native`. Plus the VoidType
return-None skip (the AST keys POLL_VOID_READY_RETURN on VoidType,
never the value). Six stale fences converted/fixed this wave --
re-run pins BEFORE the corpus run next time (the wave rule; skipped
again and paid a cycle). The test_compiler un-migrated fixture took
three tries: the async and nested-def-only shapes both now migrate;
the stable park is the ENCLOSING-body branch rebind slot coexisting
with a nested def.

ASYNC SINK CELLS 8-10 (2026-08-14, thir-avoid-list): 3 flips
(executor_bindings_smoke, async_coro_factory_return,
async_xmod_dunder_name_collision), dial -> 3545/3730 (re-measured
at the merge gate -- the hand-carried 3546 was off by one), sink
15/18;
review round 11. (1) Raw Ptr[T] VALUE frame locals (bare `T*` field,
sync pointer rows) + the Ptr-returning CALL rvalue `is None` subject
(`(get_cell() == nullptr)` -- the name row's rvalue sibling). (2)
The already-erased Own[dyn] call decl
(`std::unique_ptr<Base> c = make(41);` -- the 'forward' verdict) as
a standalone arm BEFORE the Base*-pointer registration path: the
first cut inside `_lower_dyn_protocol_decl` landed the name in
lc.pointers and the move read double-deref'd (`std::move((*c))`) --
the caller registers EVERY dyn_node name as a pointer, so the value
flavor had to bypass that call site entirely. Plus the
return-position async-factory erasure (`return add_one(n)` ->
`make_adapter<Base>(add_one(n))`, `_dyn_own_coro_factory_arg`
reused at the return arm). The protocols fence
(own_dynamic_protocol_local_defers) converted to a routing pin --
the unique_ptr receiver arrow rides the pre-existing own-dyn
receiver machinery, dualgen-verified. (3) Module-qualified ctor
managers (`async with svc.Gate()`) + async-for sources
(`async for v in svc.Ticker(3)`): the with/for setup lowerings were
lowering under plain VALUE use -- threading ctx_manager / ITERABLE
(the sync twins' flags) plus `_module_qual_ctor_shape` at the
manager gate and ITERABLE-position record rvalues at the marker
lane's record_ret. Unit pins for the xmod arms use a tmp-path
two-module fixture (TestXmodCtorAsyncSinks); NB a NAME-bound
manager is NOT a fence (it rides the borrowed F1-lvalue slice) --
the real adjacent reject is the qualified NON-ctor factory call.
The remaining sink residue (2 grindable cases) is the
OPTIONAL-RETURN coro family -- a coordinated cell, drilled and
parked in TODO.md's sink map.

POST-SINK DESIGN ROUND A (2026-08-14, thir-post-sink): 4 flips
(await_optional_field_borrow, async_finally_mutates_returned_local,
coro_for_loop_post_use, list/branch_decl_pending_list), dial ->
3549/3730 -- **the ASYNC SINK is 18/18 COMPLETE**. Round A premise
probes dissolved ALL FOUR parks (the de-design streak): (1) the
moved-container receiver-mismatch entry was STALE (a misattribution;
wave 33 fixed the real blocker) -- deleted. (2) The optional-return
coro family was the already-approved designed-queue item 6 Wave 2:
slot admissions keyed on async_return_form, the storage None->nullopt
rung (ret_value_opt stays scalar for the sync arm), the ptr-opt FIELD
lift (sync ret.ptr_opt_field's async twin; NAME sources keep
return.borrow_form), ValueOptKind.RECORD frame locals, the frame-slot
emplace flush (allow_temps -- its always-defers fence converted), and
the branch-nested rvalue-reseat delegation to the landed FRAME_RVALUE
row. (3) The hoisted-loop-var family had de-designed incrementally:
the shared _opt_storage_hoist_flavor threaded to the foreach site,
with the RESUMABLE rung opened for leaf-mode non-frame-field names;
NB the suspension-crossing flavor ALSO routes (the assumed frame-field
boundary does not exist in that shape -- the exclusion stays
defensive). (4) The try-site rvalue-reassigned hoist rode the pointer
flavor once probed: the reseats fill the lazily-allocated FUNCTION-TOP
__slot_N (BRANCH_RVALUE) -- the fence's "if-head slot" premise was
wrong; two fences converted, and an ablation killed a speculative
rebind_slot registration (the predecl entry already registers it).
Remaining from Round A: the temp-demote cell is priced and BLOCKED
behind the AST temp-counter fix (Round B).

POST-SINK DESIGN ROUND B (2026-08-14, thir-post-sink): the approved
AST-first temp-counter fix + the dynamic MIL demote -- 1 flip
(records/record_ctor_field_init_varargs), dial -> 3550/3730. Two
change-sets per the AST-first rule: (1) `codegen: MIL probe restores
the temp counter on demote` -- probe_checkpoint + rollback_discarded
(the elif-probe precedent), has_named_since fallback for the walrus
corner; the enumerated snapshot churn was exactly ONE case
corpus-wide (__tmp numbers shift down -- the burned probe numbers
reclaimed). (2) The THIR dynamic demote: `_attempt_ctor_mil_init`
with a whitelist (`call.vararg_pack_flush` only -- demoting a reject
the AST HOISTS would byte-diverge; a change-detector pin freezes the
set), local delta rollback of the faces/move-verdict journals (the
flat one-window design forbids nested begin/rollback), branch_scope
+ declared copy, AND -- review-caught -- the FUNCTION-scoped walrus
sets by explicit copy (branch_scope deliberately skips them; a
walrus ARG can lower before the whitelisted reject). The nondef-ctor
guard's observable contract is the AST's CodeGenError still firing
(pinned). Review round: codegen-correctness traced the walrus-corner
safety on the AST side (reset_scope wipes between passes) and the
journal-rebind aliasing (fresh attribute reads everywhere); the
hoist rung's frame_slots exclusion was ABLATED AS DEAD corpus-wide
(in-loop awaits still leave the hoisted var a leaf-local) and
deleted -- a genuine frame-field flavor would surface in the corpus
byte-diff, not mis-render silently.

POST-SINK DESIGN ROUND C (2026-08-15, thir-post-sink): ALL THREE
remaining design memos DISSOLVED on premise verification (streak
~29/34) -- the design queue holds NO genuine fork. (1) Field-path
narrow facts: the registry premise was FALSE (sema keys dotted paths
with full invalidation; both paths consume the per-occurrence retype
statelessly; the feared silent-unwrap-drop is structurally impossible
-- unrouted reads raise). Landed as one truthy field row, 2 flips.
(2) The poly "spelled-install regime": the oracle RE-EXTRACTS per BB
(no cross-BB state) and the regime the second entry wanted was built
by the sink waves (lc.narrow.spelled + _poly_cast_context). The
expression-position `&&` leg LANDED (binop.poly_inline_narrow --
static_cast spell after the validated dynamic_cast, INHERIT only,
|| and structural keep fences; 1 flip + the dynpostif value-position
fence converted). The resumable poly-self cell is READY in TODO.md
(bounded resumable.py extension + two verify-first rows). (3) The
"view-tuple family" premise was FALSE -- oracles spell OWNED storage
everywhere; re-tagged the OWNED-BYTES TUPLE wave (2 cells, 3 flips,
READY in TODO.md). Dial -> 3553/3730 this round (5 flips across
rounds A-C landed cells on thir-post-sink).

POST-SINK DESIGN ROUND D (2026-08-15, thir-post-sink): the two
architecture items SEQUENCED -- no design work opened. (1) The
nested-def rebind-slot emit model stays PARKED, scheduled LAST before
the cutover: M-cost (an _EmitState hoist-scope stack; nested-def
first, the sgen leaf-emitter drain hook second), 3 sole-blocker
flips, gated on the BUGS.md nonlocal-rebind slot-model decision and
re-probed after the union rebind-kill lane; the test_compiler
un-migrated fixture swaps to a synthetic reject in the migrating
change-set; check the A5 stdlib breakdown for these tags before
cutover scheduling. (2) The REF_ALIAS place/loan design stop is
CLOSED AS DISSOLVED: the waves consumed the whole reachable family
via sink-specific _ExprUse flags, the pinned `shared(a).x` shape and
the decl bind both ROUTE, and the census carries zero units behind
the reject -- the stop had conflated render mirroring with
soundness, which is sema's job on both paths; IR_DESIGN item 9
(resolved as the Form-tag design) was never a prerequisite, and the
"permanent fence contradicts the cutover" tension is MOOT (nothing
is fenced). Standing rule recorded in TODO.md: future
borrow-return positions open as sink-specific flags with boundary
pins, never blanket rows, never waiting on MIR. With rounds A-D
complete the design queue is EMPTY: two READY waves (~5 flips)
remain, then the live blocker mass and the cutover path.

ROUND C WAVES (2026-08-15, thir-roundc-waves): 4 flips, dial ->
3557/3730. (1) Owned-bytes tuple CELL 1 (2 flips): `_owned_bytes_slot`
+ the unpack-elem bytes leg; the return/decl slots cascaded free from
the shared value-tuple family. CELL 2 ATTEMPTED AND REVERTED: the
Own[str]/Own[bytes]-param bare-read leg byte-DIVERGED -- the fence
test_own_str_arg_in_while_condition caught that the AST seeds those
params MOVABLE (the leg's original exclusion reason was RIGHT); the
inline view-conv row went with it (lost its only witness). The
movable-seed prerequisite is recorded in TODO's wave entry. Also
filed: the PendingBytesType-under-pytest harness wart. (2) The
resumable poly-self cell (2 flips: isinstance_self_in_generator +
_field): the gate's poly-self leg (`polymorphic_source_inner` over
self_type), the `_resume_alias_name` collision bump
(`__self_narrowed`), the BB install routing self through
`lc.narrow.spelled`, and the poly Branch-cond leg (the no-alias
dynamic_cast check; the extraction is the ARM's skeleton emission).
The field-write-through-spelled-self flavor verified byte-identical
without extra rows. Two fences converted; the tuple-form tripwire
(res.cond for `isinstance(self, (A, B))`) still stands.

GRIND WAVES (2026-08-15, thir-grind-waves): 4 cells, 5 flips, dial ->
3562/3730. (1) The Own[str/bytes]-param bare-read cell (1 flip:
tuple/unpack_view_target_owned_sinks, closing bytes cell 2): round C's
"movable-seed" prerequisite DISSOLVED on probing -- the AST never seeds
value-payload Own params movable (seed_param_locals is non-value only;
no sink renders std::move(v), probed across return/decl/field/setitem/
forward). The real blocker was a FORM conflation: the param-implies-view
verdicts (_str_name_form/_bytes_name_form, the S1/S6 rows' param faces)
read every param as BORROW, so an admitted Own[str] param took the
inline std::string(v) convert where the AST hoists the copy+move temp.
The mirror: _own_viewfam_param + prescan.owned_viewfam_params carve the
owned-form params out of every view verdict; _own_lvalue_temp_slot
admits the Own[str]-param NAME (argtemp.own_str, ablation-verified);
the free-call ladder gains the S1/S6 view->owned rows (re-landing the
inline view-conv arg row); the self-append fold excludes Own[str]
params (the AST peephole checks the DECLARED type -- divergence caught
by adversarial dualgen, v = v + "x" folded to += where the AST spells
the plain concat-assign). Three fences converted, three boundary pins
added (Own[bytes]-param argtemp, Own[StrView] reads, reassigned param).
LESSON RE-PAID: the reverted attempt's recorded diagnosis was a
HYPOTHESIS -- probing the oracle five ways found a different mechanism
than the fence comment asserted. (2) The Optional view<->str identity
coerce at ARG slots (1 flip: str/optional_str_strview_arg): the
coercion lambda is identity exactly at the plain non-Own ARG slot (both
sides optional<string_view>); disposition arm + free-ladder row (CALL
rvalue inners only) + allow_whole_optional threaded to the inner call's
result gate. Boundaries: decl-init/return rebuilds and NAME sources
defer. (3) The generic value-tuple literal (1 flip:
generics/generic_infer_compound_t_literal_coerce): a bare tuple LITERAL
at a value-tuple-resolved T slot renders the inline spelled brace
prvalue via _lower_tuple_literal -- no ref-slot temp, unlike the
str/list literal rows. Nested flavor included; the genarg2 stays-AST
fence converted (caught by the harvest run's pin failure, not my pin
grep -- grep for the SHAPE, not the tag, when hunting fences).
Boundary: record-element tuples defer. (4) The Own[scalar] loop-var
movable seed (2 flips: stdlib/heapq_merge +
tplib/requests_redirect_method, the latter cleared by cell 1): the
for-each seed gains the AST is_consuming test's Own-elem-type half
(hoist-guarded, loop-scoped); name.own_read admits movable-seeded
Own[value-scalar] LOCALS beside params, the move-source rows rendering
push_back(std::move(x)) off the same working set. Boundary: the
hoisted read-after-loop flavor keeps its reject. The expr.call
sole-blocker tail is EMPTY after this wave; the remaining mass is
multi-blocker set-cover plus a ~35-tag 1:1 singles tail.

GRIND WAVES 2 (2026-08-15, thir-grind-waves cont.): 3 cells, 3 flips,
dial -> 3565/3730. (1) CopyIter at Iterable[Own[T]] slots (1 flip:
list/copy_iter): the copy-suppressing adapter binds the monomorphized
template param bare (NAME lvalue / copy_iter(..) rvalue through its
special-builtin arm); qname-keyed, disjoint from the movable OwnIter
sibling (boundary-pinned NAME flavor). (2) Borrow-tuple FIELD elements
(1 flip: tuple/tuple_ref_readonly): the `&(<member read>)` lift beside
the NAME/subscript branches, RECEIVER use. The leg's adversarial
dualgen caught a PRE-EXISTING AST miscompile -- a narrowed-Optional
field element renders the un-deref'd `&(this->maybe)` (optional<T>*
into the T* slot, g++-verified ill-formed) -- filed in BUGS.md; the
leg excludes Optional-declared fields so the shape defers on both
paths. (3) assert isinstance(self, Sub) (1 flip:
protocols/isinstance_self_method): the poly assert's self exclusion
lifted -- the persistent `const Dog& __self = *dynamic_cast<..>(this)`
alias via _poly_cast_context's existing self arm, reads renaming
through the SPELLED map (the receiver arm never consults `narrowed` --
the naive _register_dyn_narrow left reads at `this->`, caught by the
first probe). Delta review (safety-model clean; test-coverage found
the face-count-only routing pin and two missing boundary pins, all
applied). THREE stale fences caught by corpus/suite runs rather than
the pre-widening pin grep -- third occurrence this session: grep for
the SHAPE across tests, not the tag, before widening an arm.

GRIND WAVES 3 (2026-08-16, thir-grind-waves cont.): 1 cell, 1 flip,
dial -> 3566/3730. The wrapper member-container NAME element
(union/union_recursive_alias -- the last union-family sole blocker):
one gate leg in _container_lit_elem_ok's wrapper rows sufficed; the
make/move switch and element move mirror rendered
`make_vector<Tree>(1, std::move(inner))` with no emit work. The member
spells `list[AliasRef]` vs the binding's one-level expansion, so the
gate's equality goes through the binding's ELEMENT (== the wrapper) +
the unique-list-member shape; a non-member container name at the slot
is a sema type error, so element-equality is the structural guard.
Copy flavor + dict-value flavor pinned. REVIEWED at the merge cycle
(2026-08-16, safety-model + docs-sync): sound; the pins tightened to
_assert_routes_byte_identical (they were fallback-satisfiable) and
the structural-equality dependency noted at the gate leg.
SCOUTED AND FENCED: the generic-overload emission pair
(sig.overload_set.generic_stub) splits into a moderate
single-emission-with-defaults cell (overload_generator) and a per-stub
template-emission TRACK (overload_generic_mixed, skeleton-seam) --
decomposition in TODO.md. Session close: the remaining sole tail is
scout-sized-or-bigger items (parked rebind hoists, walrus design
family, resumable flavors, the overload track).

MULTI-BLOCKER MASS 1 (2026-08-16, thir-multi-mass): 4 cells + a review
round, 2 flips (tplib/requests_form_data,
tuple/mixed_own_tuple_param_from_call), dial -> 3568/3730. First wave
against the multi-blocker set-cover mass. NEW MEASUREMENT: full
per-case raise-site SETS over all 170 blocked cases
(/tmp/agents/thir-wave/sites_multi.py -- the sole-only histograms hid
where the multis converge); expressions.py's free-call arg-ladder
terminal touches 33 of them and was the wave's site. Cells: (1) tuple
FIELD reads at native/template tuple slots -- the F3 lift row went
kind-blind (gate row + plain_kind dropped, mirroring the NAME row) and
a VALUE-tuple regime landed in _native_protocol_field_arg + the field
result gate (arg.native_value_tuple_field / field.value_tuple); clears
the five tuple_to_str dataclass-__repr__ cases. (2) Bytes-member
ptr-variant unions: _ptr_union_view_member_ok admits owned bytes -- the
bytes-literal side-row's recorded "do not widen" fear was probed with
an adversarial consumer sweep (narrowing/extraction/decl/field/return:
every routed body byte-identical, the rest keep their own gates); free
gate rows for the bytes-literal temp + the NEW dict-literal typed temp
(unionlift.dict_literal_temp; predicates folded into
_union_literal_temp_arg at review). (3) Frame-capturing
container-literal ArgTemps: the plain generator-factory instantiation
arm threads allow_temps like its qualified sibling;
_container_literal_arg gains frame_capturing (readonly LIST literal
hoists at a generator callee); the empty readonly-rvalue INLINE arm
declines frame callees (dualgen-proven load-bearing). (4) Mixed
own/borrow + owned-movable tuple call-pass: _mixed_own_borrow_tuple
widens _btuple_pass_arg (the free-call gate leg refactored to call the
SAME predicate -- no drift), call.own_tuple_pass binds the
owned-movable prvalue bare; converts test_thir_tuples' stale
still-defers pin. REVIEW ROUND (7 specialists; 4 clean): safety-model
found a REAL Critical -- the frame gate row admitted dict/set literals
whose render arm is not frame-aware, falling through to the INLINE bind
the frame borrows past (dualgen-confirmed DIVERGENT, zero corpus
witness -- the byte-diff was green throughout); fixed by restricting
the gate to ArrayLiteral + boundary pin. That is the SECOND consecutive
wave where a gate/render pair split across two predicates hid a drift
the corpus could not see: when gate and render cannot key one
predicate, the unpaired slice needs an explicit reject or a dualgen
probe per literal kind. LESSON RE-PAID: `git checkout <path>` during a
disable-probe wiped the cell's uncommitted edits -- disable-probes
revert by re-edit, never by checkout. Site residue (chains moved):
Iterable[Own[T]] args need the consuming own_iter move mirror + auto
ArgTemp (move-audit-sensitive; those cases also block at for_each
1854 / sgen.yield_type), Sized-at-len subscript/comp args, the
tuple-optional literal family (tuple_optional_own_param),
ptr_optional_collapse, and singles. Next sites by touched-case count:
functions.py:2029 ctor.mil mass (27), expressions.py:2430 binop.shape
(28), statements.py:8561 decl.slot_type (24), expressions.py:5397
subscript.recv_type (22).

MULTI-BLOCKER MASS 2 (2026-08-16, thir-multi-mass cont.): 1 cell + a
delta-review round, 2 more flips (int/warn_mixed_sign_compare,
tuple/tuple_user_record_field), dial -> 3570/3730. The binop terminal
(the fresh census's top greedy pick): (1) the mixed-sign fixed-int
compare fence became a lowering -- THIRBinOp template_override spelled
from codegen's _CMP_HELPER (imported, one table, no drift); converts
THREE stale test_thir_core ineligible-pins (value / logical-operand /
chained-pair flavors, all byte-identical). (2)
_ptr_tuple_field_compare_pair: both-FIELD ptr-repr tuple pairs take
tuple_eq/tuple_lt over BARE member reads (the dataclass __eq__ chain);
declared-type keyed. DELTA REVIEW (safety-model + test-coverage):
safety-model found a compile-verified DIVERGENT Critical -- a
PROVEN-narrowed value-opt operand reads its inner fixed int in THIR's
gate while the AST's raw resolved type stays the Optional (target
suppresses cmp_*), so the row emitted cmp_less((*a), b) vs the AST's
bare ((*a) < b) -- a different BOOLEAN for negatives, zero corpus
witness. Fixed with the narrowed-unwrap identity guard; five guard
boundary pins added. THAT IS THE THIRD gate-vs-AST fact-derivation
drift in three consecutive review rounds: whenever a THIR gate consumes
a MUNGED operand fact (narrowed inner, unwrapped optional, split
predicate), the AST's corresponding arm must be checked for which RAW
fact it keys on -- and the divergence class is corpus-invisible by
construction, so the check is a review/dualgen obligation, not a
byte-diff hope. Residue at the site: the is/is-not None families
(optional_other_nonetype x3, union_other_nonetype x3 -- coherent next
cells), readonly_dict_reads' `in`-tparam pair, op_own_return_fresh's
`+`. The dataclass_asdict pair moved to the asdict macro-expansion
comps (site expressions.py:9136, greedy #2 -- the comp-element
container-literal family).

MULTI-BLOCKER MASS 7 (2026-08-17, thir-multi-mass cont.): 2 cells + a
review round, routing-only (no flips), dial holds 3576/3730. (1)
FIELD-receiver dict views in COMP sources. THE PRIOR BATCH'S RECORDED
SEAM WAS WRONG: it claimed the method-family dispatch returns fam=None
for field receivers -- spying shows the container family IS found; the
actual gate is the ITERABLE override in the method-call arm
(`iterable_override`), which called `_dict_view_iterable_ok` with the
default `field_recv_ok=False`. Threading it there + at the comp route
admits the flavor (render is receiver-blind past admission). The
for-head call sites keep the default: their storage-tuple loop-var
registration is name-receiver-keyed (the json_model_nested
binding-gap catch), now BOUNDARY-PINNED rather than prose-only. A
FENCE REASON RECORDED FROM INFERENCE IS A HYPOTHESIS -- this one was
committed as fact in a commit message and cost a re-derivation.
(2) List-literal comp iterables: the AST captures the braced init-list
itself (`auto __obj_N = {"a", "bb"};`) and iterates begin/end with the
sized reserve -- a route row, no new render; non-empty, unpack-less,
non-consuming. REVIEW (safety-model): no Criticals; it independently
verified the widened override is unreachable from the sync for-head
AND the resumable/async for paths (tested plain/async/generator/
readonly receivers). Two findings applied: a stale comment that
contradicted its own commit's code, and the container-literal
`resolve_pending_container` invariant made explicit. STILL DEFERRED,
each pinned: list_comp_global_shadow (loop var shadows a pointer-slot
GLOBAL -- needs the comp's render-state scoping, a design question);
the dataclass_asdict pair (rvalue-into-borrow at a non-arg tuple sink
-- the astuple ptr-repr family).

MULTI-BLOCKER MASS 6 (2026-08-16, thir-multi-mass cont.): 3 cells + a
batch review, 5 FLIPS (argparse help_basic, metavar, nargs,
optional_list_absent, list_and_const_actions), dial -> 3576/3730. The
argparse Own[Optional[container]] chain, ground end to end: (1) MIL --
an Optional[CONTAINER] field takes the type-agnostic M3b-move from its
Own storage-optional param (the is_opt row was F1-pointee-only);
(2) OPT_PTR_SLOT reseats from container-returning by-value call
rvalues (the accumulator copy); (3) the ctor-arg inline
materialization (`std::move(p ? std::optional<V>(std::move(*p)) :
std::nullopt)` -- gen_expr_deref's Own[Optional] arm); (4) the
subscript receiver resolver's narrowed_ok (READ-only): a sema-narrowed
Optional[container] FIELD receiver types at its inner
(`(*a2.coord)[0]`), SETITEM keeps the declared slice; (5) the
container copy tail admits pointer-local sources (OPT_PTR + REBIND
flavors, `V((*acc))`). Converted fences: test_thir_containers'
narrowed-field pin (read-routes/write-defers). BATCH REVIEW
(safety-model, compile-verifying): CRITICAL -- the ctor-arg
materialization is gen_call_arg's LAST-USE routing; a still-live
source hoists a temp (unmirrored). Fixed with the _is_move_source gate
(which also records to move_audit, closing the join blindness);
boundary-pinned; all five flips stay CLEAN. The durable witness for
the macro-shaped copy row is the flipped nargs/optional_list_absent
pair (no synthetic fixture reaches the macro shape).

MULTI-BLOCKER MASS 5 (2026-08-16, thir-multi-mass cont.): 2 cells + a
batch review, 1 flip (none_safety/nested_field_narrowing), dial ->
3571/3730. (1) Macro-transparent print args: the print-arg gate treats
a TUPLE-LITERAL-producing call-macro arg as its expansion (the AST
classifies by TYPE; the broad first cut regressed hasattr/isinstance
bool-macro args and was restricted -- their call-typed rows already
classify by result type); the wrap arm classifies VALUE tuple literals
TUPLE (a GENERAL admission, plain print((x, y)) pinned at review); the
all-value astuple expansion routes, the pointer-repr flavor stays
pinned on the unmirrored spelled storage-tuple print render. (2) The
one-link chain Optional-field family: None-test subject, the stateless
narrowed chain READ, and the truthy flavor, all sharing
_plain_record_field_link (the intermediate link must be a plain
DECLARED record); two-link chains + Optional links boundary-pinned.
BATCH REVIEW (safety-model + test-coverage over five cells): NO
Criticals -- first clean-of-Criticals round this branch; safety-model
still found the read predicate relying INCIDENTALLY on subject-gate
ordering for its link guard (now carried in the predicate itself,
unit-checked on the real compile). Residue: the other three
optional_other_nonetype cases are distinct subject flavors
(Optional[String] param + return.record_source; tuple-element
subscript with runtime-check marker; Span|None coercion) each with
additional other-site blockers.
RATCHET-CATCH CODA (same day): the post-review full suite caught TWO
de-routings the targeted runs missed. (a) The review round's link
guard on _narrowed_opt_field_read over-restricted: the AST's read arm
derefs on the leaf declared-vs-analyzed mismatch ALONE
(link-type-blind), and dataclass_asdict_mixed's macro chain read (link
receiver a macro temp outside locals_) is a live witness -- REVERTED;
the read predicate's pin now asserts link-blindness. A REVIEWER'S
RECOMMENDATION IS A HYPOTHESIS TOO: this one shipped a regression only
the ratchet could see. (b) The macro print substitution had to become
CONDITIONAL on the call node failing _wrap_print_form -- when the call
classifies, its path lowers the expansion as an expression whose
element admissions differ. Also converts the stale deep-chain-subject
fence (one-link chains route). Suite green 11769, joins clean.

MULTI-BLOCKER MASS 4 (2026-08-16, thir-multi-mass cont.): 1 cell (five
asdict-expansion arms), no flips yet, dial holds 3570/3730. Landed: the
dict-CONSTRUCTION call element (dict({...}) at a same-type dict slot),
multi-list-member union comp disambiguation by the comp's OWN sema type
(converts the two-list-members stays-AST fence), container FIELD reads
at union element slots (bare variant copy, BORROW_BIND), dict-LITERAL
storage-tuple members (self-describing inline), and
_dict_view_iterable_ok FIELD receivers (declared-type keyed;
len/print/list()/for-head consumers dualgen-verified) + the
dict-literal print-arg row. PINS ARE MACRO-DRIVEN: hand-spelled
`dict({...})` / mixed-value dict literals resolve through different
sema paths than the macro's stamped nodes, so the fixtures invoke
asdict itself. THE REMAINING BLOCKER FOR BOTH dataclass_asdict FLIPS,
spied precisely: `print(astuple(x))` arrives at the print-arg gate as
the UNEXPANDED macro call (func=astuple, call_type=None) -- the gate
must consult `e.macro_expansion` (the free-call arm at
call.macro_expansion already does) and the expansion needs the SPELLED
storage-tuple-literal print render
(`TuplePrinter(std::tuple<ordered_map<..>>(<stmt-expr>))`). One cell:
macro-consult at the print gate + wrap classification over the
expansion + the spelled tuple render. Two flips behind it.

MULTI-BLOCKER MASS 3 (2026-08-16, thir-multi-mass cont.): 1 cell, no
flips, dial holds 3570/3730. The comp-element arm of the
container-literal gate (the asdict recursion's dict-value comps): a
list/dict comp at a matching container element slot gate-admits -- the
RENDER arm already dispatched the target-typed `({...})` stmt-expr
(previously reachable only via the dict-comp VALUE widening), so the
cell is one gate arm + pins (set comps boundary-pinned). Both
dataclass_asdict cases advance byte-identically; their remaining
blockers, spied precisely: dataclass_asdict_fuzz needs a CALL rvalue at
a `str | dict[str, Int32]` union ELEMENT slot (the nested asdict
sub-expansion -- the variant ctor-temp flavor of the element gate);
dataclass_asdict_dict_tuple needs `_comp_lowering_route` to admit the
dict-comp-over-`.items()` shape (comp.route None). Two flips waiting
behind those two arms.

### The inherited-builtin-container receiver row (2026-08-17)

One cell, dial 3576 -> 3578/3731 (1 flip,
`inheritance/inheritance_builtin_overloads`, plus one new case). A user
record inheriting a builtin container (`class MyList(list[Int32])`)
now routes both its inherited method calls and its constructor.

THE PARKED DESIGN PREMISE WAS FALSE, and verifying it first is what
made this a row instead of a concept. TODO.md had this parked as
needing a new concept -- resolve a record receiver to its inherited
container type, thread it through the container family's shape/arg
gates "which key on `is_list(recv_t)`" -- plus an arrow-vs-dot receiver
form. Measured: `_container_method_call_supported` and
`_container_method_arg_ok` key on the RESULT type and the fi and never
touch `recv_type`; only the family PREDICATE looks at the receiver.
And the receiver render needed nothing at all -- `ml->push_back(10)` is
the same arrow a `list` pointer binding already gets. A disposable
probe (predicate widened, nothing else) took the case straight to
CLEAN + IDENTICAL before any design was written.

WHAT IT ACTUALLY NEEDED was the AST's own fork, not a derived fact.
`_gen_method_call` falls past its user-record arm exactly when
`record_info.get_method(name)` is None, so an INHERITED method lands in
the same builtin-stub branches a plain container receiver takes while
an OVERRIDE takes the record arm (`o.append(3)` vs `p.push_back(3)`,
spy-confirmed on both). The method name is therefore threaded into
`_method_recv_family` and every table predicate, and
`_inherited_container_base` mirrors that raw call. Keying the row on
the receiver TYPE alone -- the obvious shape, and the one the park
described -- would have gate-admitted the override into a family whose
render is wrong: the same gate/render split that produced the last
three review Criticals, caught here at design time by asking what the
AST keys rather than what the shape looks like.

No face on the new row. A classification predicate runs before the
family's shape gate can still reject, so a witness there would read as
routed for a body that falls back; the routing pin carries the claim.

The ctor's non-F1-base reject admits a builtin-container parent when
the body has no base-init call (the base never reaches the MIL then).
THE BASE-INIT REJECT IS LOAD-BEARING, and the first draft of this entry
said the opposite. Two arity-error probes (`super().__init__()`,
`list.__init__(self, ...)`) were generalized into "sema rejects every
spelling, so the guard has no witness" -- but `super().__init__([1, 2,
3])` seeds the base's elements, compiles, and runs. The review caught
the claim; the CODE was right either way, only the reasoning behind it
was invented. Pinned now by `test_base_init_ctor_stays_ast`. A fence
reason derived from a probe covers exactly the spellings probed.

Sliced scope: `str`/`bytes`/`bytearray`/scalar bases are NOT covered
(only `_container_elem_family` + set); bytearray and the generic
`class Bag[T](list[T])` shape are boundary-pinned. Dict and set
inheritance are admitted and carry an executed case
(`inheritance_builtin_dict_set`) that mutates through a call boundary,
not just unit byte-identity -- the review's point that a unit pin never
builds the C++ it asserts about.

Three adjacent pre-existing bugs found and filed, all AST-side: a
`super().<method>()` call on a builtin base spells the TPy method name
against the C++ base type (uncompilable); a container-inheriting record
with no explicit `__init__` emits `using std::vector<int32_t>::list;`
(uncompilable -- the injected-class-name is `vector`, not `list`); and
`x in t` is rejected on a set-inheriting record, because membership
resolves dunders against the record alone while the method-call path
walks to the builtin base.

### The unemitted @overload clone (2026-08-17)

One cell, 2 flips (`calls/overload_getitem_slice_generic`,
`readonly/auto_readonly_overload_const`), dial 3578 -> 3580/3731.

THE REJECT WAS A PHANTOM. `_collect_method_overload_groups` hands a
name's stubs to the FIRST non-stub method (`pending_stubs.pop`), and
method expansion has already split an `@auto_readonly` / `auto_own`
impl into two clones -- so only one is registered. The AST emits THAT
one once per stub, taking each specialization's const-ness or move-ness
from the stub, and never names the twin. THIR was attempting the twin,
failing, and tallying a fallback for a body that does not exist.

The approved design was to PAIR the twin with its polarity-matching
stub. That was wrong and the spy said so: the lookup key is minted by
the AST emitter (`thir_overload_key`), which has no group for the twin
either, so a paired entry would have had no consumer. Printing
`key[0]` against the record's methods in ONE process showed every
specialization keying off the single registered impl -- ids are not
comparable across processes, and an earlier cross-process comparison
had made the pairing look plausible.

The fix is therefore a SKIP, not a lowering. `unemitted_overload_clones`
computes the dead ids once per module (not per callable -- the naive
form is quadratic in a record's method count).

THE OVER-SKIP DIRECTION IS THE DANGEROUS ONE and has no automatic
detector: skipping a live body tallies NO fallback, so the ratchet
cannot see it, and the AST re-emits byte-identically, so the byte-diff
cannot either. Both directions are pinned explicitly -- an ordinary
auto_readonly pair and a plain @overload set keep every half attempted.

Converted `test_composed_overload_set_still_defers`, whose fence ("4+
entries keeps rejecting") described the same phantom; the composed
auto_own shape now routes with zero fallback.

Also lands one protocol-arg row: a str LITERAL at a structural slot
hoists the un-spelled `auto __tmp_N = "hello";` temp (the raw
`const char[N]` the protocol deduces on -- a spelled temp would give a
string_view). Advances `iterators/iterable_builtin_types`; no flip.

SCOPE CORRECTION worth recording: the two remaining parked sites were
scoped as one cell each. They are not. The protocol-arg family is N
narrow rows across DIFFERENT gates, and no single row flips a case --
`iterable_builtin_types` still needs the view family's Iterator-protocol
return (`str.__iter__` is @cpp_template, so it misses the
native-function form), while `own_iter_to_iterable` and
`overload_template_stub_cross_module` never reach the protocol-arg gate
at all. Scope a family by probing every member's reject SITE, not by
the shared tag.

Review round on the above (2026-08-17). The skip moved from the two
driver loops into `iter_module_callables` itself -- the feed list whose
whole purpose is that the drivers "never drift", and which a third
consumer (`thir/dump.py`) reads without any skip, so it would have
reported the dead clone as un-routed. Excluding it at the source is what
makes that correct by construction rather than by convention.

The composed-set pin was strengthened: zero-fallback plus byte-identity
cannot distinguish "twin skipped, rest routed" from "whole group
over-skipped so nothing was attempted" -- both are silent. It now asserts
the skip set names exactly one of the two impls.

A boundary pin for the str-literal row at a @dynamic slot was written and
then DELETED: sema rejects a str at a @dynamic slot for non-conformance,
so the pin could never fail. An unfalsifiable pin is worse than none --
the reason lives at the guard instead.

The review also surfaced a pre-existing AST bug, filed: `auto_own[Self]`
composed with `@overload` emits every specialization twice with identical
signatures (`is_readonly` comes from the stub, `is_consuming` from the
impl), which g++ rejects. Reported as a silent polarity collapse;
building the repro showed it is a hard compile error.

### The subscript field-receiver row (2026-08-17)

One cell, 1 flip (`tuple/tuple_own_nested_inline`), dial 3580 ->
3581/3731. A `.field` read whose receiver is a SUBSCRIPT --
`pp[0][0].fd`, `h.pair[0].v`, `xs[0].v`, borrow tuples of reference
elements, list and dict subscripts -- composes the subscript's own
render with the member tail. Three more tuple cases advanced past the
site, and two stale fences converted (`test_chain_rooted_receiver_*`,
`test_tuple_over_tuple_chain_*`, both dualgen-proven identical).

THE BOUNDARY WAS MEASURED, NOT REASONED. A blanket subscript admission
DIVERGED: THIR dropped `::tpy::deref_check(...)` on a
`tuple[P | None, Ptr[Tag]]` element -- a missing NULL CHECK, not a byte
nit. The predicate keys the element resolving to an F1 record; the
reject-unit asserts the AST still emits the check. This is the value of
probing an over-broad admission first and reading the diff, rather than
shipping the narrow guess: the narrow guess would have been "exclude
needs_optional_runtime_check", which was measured NOT to be the driving
fact (the element is a Ptr, not an unproven Optional).

SITE STATUS, precisely: the SUBSCRIPT shape is zero corpus-wide bar the
deliberate Ptr fence. Two cases keep the `field.receiver_shape` tag but
are unrelated shapes -- a Ptr-returning METHOD CALL receiver
(`h.get_node().value`) and a deep NATIVE field chain (`self.s.a.q.flag`).
Reject tags are lossy; the shape is done, the tag is not.

TWO CENSUS-TOOL BUGS FOUND AND FIXED, both of which INFLATE progress:
`sites_multi.py` (and the group variant) counted (site, reason) PAIRS as
touched cases, so a case with three reasons at one site counted three
times -- the site that motivated this cell read as 13 cases and is 6,
and every earlier session's ranking was inflated the same way. And a
case with no `src/main.py` (a frontend-plugin `pascal/` case) returned
an empty raise list, i.e. read as a FLIP CANDIDATE; of seven such
"raised nothing" cases, four were unprobed and three block in the
RESUMABLE path the prober never reaches. Zero were real.

A NEAR-MISS worth recording: converting one stale fence with an
unanchored `str.replace` rewrote EVERY `assert _fn(...) is None` in the
file, turning five unrelated reject-pins into routing-pins. The targeted
run caught it; the files were restored and the conversions redone
anchored. On a test file full of near-identical assertions, an
unanchored replace silently deletes exactly the boundary coverage that
catches over-admission.

### Borrow-tuple source: field-rooted subscript (2026-08-17)

Routing-only, NO flip; dial holds 3581/3731. `_borrow_tuple_source_ok`
admitted a subscript over a NAME (`rows[0]`) but not over a FIELD
(`self.store[k]`), though the row directly below it already admits
field-rooted sources. One-line widening, byte-identical, pinned with the
readonly-root boundary.

THE CELL STOPPED SHORT OF THE SITE, deliberately and with the reason
recorded. `statements.py`'s decl.slot_type is the biggest remaining site
(24 cases, 2 sole-site flips) but the rejecting SLOT TYPES are a long
tail of ~20 families -- no dominant shape. The two coherent clusters are
pointer-repr Optional slots (~10 cases, holding the other sole-site flip
`argparse/custom_type_list`, shape: the `optional_to_ptr` lift) and
borrow tuples (~7). This cell took the tuple cluster's source row;
`tuple_borrow_const_deep_source` went 3 rejects -> 2 and did NOT flip,
because its remaining decls reject BEFORE the borrow-tuple cascade at a
different guard, and following them leads into the borrow-tuple const
fixpoint -- which already carries a BUGS entry for emitting ill-formed
C++. Not a design fork, just work that wants its own session.

Method note for the next reader: the slot histogram (spy the reject and
bucket by `vtype`) is what showed there was no dominant family. Site
size alone would have suggested a single big arm; there isn't one.

Merge review of the whole branch (2026-08-17). No Criticals from seven
specialists; codegen-correctness, safety-model and cpython-parity clean.

The finding that mattered was NOT about THIR: `docs/LANGUAGE_FEATURES.md`
presents builtin-container inheritance as working while this branch filed
two bugs against exactly the documented pattern -- a bodyless child
(`class Child(list[Int32]): pass`) does not compile, and `x in child` is
rejected on a set/dict-inheriting child. Both are now Limitations there,
along with the `super().<method>()` miscompile. `dict`/`set` were also
missing from "Supported builtin parents" though this branch's own case
proves them.

A THIRD doc claim there was measured STALE and removed: "Cannot inherit
from builtins with forwarded type parameters (`class Child[T](list[T])`)"
-- that compiles and runs (`Bag[Int32]()` -> `2 5 6`). Nobody reported it;
it turned up because the reviewer's finding sent us to read the section.

A reviewer flagged the new `_subscript_field_recv_ok` as possibly making
the tuple-scoped `_field_over_subscript_ok` dead. It does not: that
predicate stays live for WRITE targets, where this row is not consulted.
The real asymmetry is that the sibling carries `_field_markers_clean` and
this row does not -- correct, because the marker-bearing field shapes take
early returns further up the field arm and never reach the receiver gate.
Probed (a property getter off a subscript routes byte-identically) and
recorded at the predicate, so the next reader does not redo it.

### Orchestrated flip wave: 13 cells, 12 flips (2026-08-18)

Dial **3581/3731 -> 3593/3731**, markers 150 -> 138. Fourteen commits,
~60 pins, full suite green with FULL exec (11873 passed, 3730 built+run,
0 cache skips), move-verdicts 0/925, binding-facts 0/11359, interop
31/34. Run as an orchestration: read-only scouts ablated candidate sites
in parallel, implementers landed cells serially in one worktree.

Flips: `tuple_ref_own_mix`, `tuple_unpack_module_own` (mixed borrow+Own
unpack source; module-level global-slot target rung) / `tuple_literal_list`
(owned-str genexpr unpack target) / `iterable_builtin_types` (view-family
Iterator-protocol return) / `frames_resumable` (Own frame-field reads) /
`tuple_copy_whole_ref_element` (`copy(<tuple literal>)` storage brace) /
`dyn_inherit_ref_param` (protocol-method optional-ptr arg + narrowed
ptr-repr Optional container view) / `requests_files` (None at a
multi-member ptr-variant union arg + Own[bytes] literal ctor arg) /
`custom_type_list` (ptr-repr Optional container decl + list/set narrowed
for-each) / `narrowed_field_subscript_lhs` (narrowed-field setitem +
its Optional MIL lift) / `tuple_call_relay_shares`,
`tuple_ternary_field_return` (F3 borrow-tuple return: bare relay + the
one-lift ternary). This closes the previous entry's open loop --
`argparse/custom_type_list` was named there as the pointer-repr Optional
cluster's other sole-site flip.

**THE THREE BIGGEST SITES BY TOUCHED-CASE COUNT ARE DEAD ENDS, proven
case-by-case rather than argued.** `field_write.py` (14 cases, ONE reason
string hiding SEVEN render shapes), `functions.py` ctor-MIL (21 cases),
`expressions.py` method-call args (13 cases): each was ablated per case
and each buys ZERO flips. Two of them additionally CRASH the emitter if
admitted blind (`Any`/`None` field slots; a value-optional MIL arm whose
render reads `source.name` unconditionally -- a latent AttributeError one
gate edit away). Do not re-grind them on site size.

**The census sole-site label was wrong SEVEN times.** Per-body lowering
aborts at the first `ThirUnsupported`, so a second blocker in the SAME
STATEMENT is invisible; and some "sites" are one shared raise for a whole
dispatch ladder (one such: 6 cases, 2 labelled sole, exactly 1 flips).
Rank by ablation, never by census count. The flips that did land came
from PAIRED small sites -- two rows in different files, neither of which
flips anything alone.

**Three method fixes that paid for themselves immediately:**
1. **Count routed bodies, not byte-identity.** A fallen-back body re-emits
   through the AST byte-identically, so identity proves nothing about
   routing. This exposed 8 false "identical" verdicts in one brief, and a
   whole target where three successive ablations each printed IDENTICAL
   while `routed` never moved.
2. **A byte-diff harness must be self-checking.** One scout's harness
   mapped generated `<out>/<case>/main.d/{include,src}/...` onto
   `expected/main.d/...`, found no counterpart, silently skipped, and
   printed IDENTICAL having compared ZERO files -- voiding its brief. Strip
   the `.d` component and print an explicit `cmp=N`.
3. **Look for the already-landed sibling row before designing.** The
   cheapest flip was a disjunct its sibling gate already carried verbatim,
   docstring and all; 3 of another brief's 5 rows were the same story
   (`btuple_slot` threaded at the decl sink but not the return sink;
   `narrowed_ok` on the read gate's field leg but not the setitem gate;
   `_is_borrow_ptr_local` nineteen lines below the arm that needed it).

**`move_audit` IS STRUCTURALLY BLIND at copy/alias-position arms** --
measured `joined=0` (`ast_recorded=0`) at several sites here, because the
AST asks no move question on those nodes, so the join denominator is
empty and it reports green regardless. It cannot be cited as coverage
there; only the byte-diff and mutation-observing cases cover alias
semantics. A live wrong-code divergence was found this way and fixed
inside its cell: the natural element lowering for `copy((1, b))` emitted
`std::move(b)` where the AST copies -- a move out of a `copy()` argument.

Three pre-existing defects filed, none fixed here (all AST-side, so each
needs its own change-set): a borrow-form unpack target rebinding THROUGH
its alias (`ref, owned = split(p); ref = q` silently mutates `p`; TPy 80
vs CPython 8); an unproven `Optional[dict]` receiver emitting
`dict_values((*d))` with NO deref_check while the compiler's own warning
on that line promises one; and (re-confirmed, already at BUGS.md:33/:217)
the narrowed ptr-repr Optional subscript emitting raw `operator[]` --
that one BLOCKS a THIR row, since mirroring the correct checked read
diverges from a broken oracle.

Merge review (2026-08-18): seven specialists, ZERO code defects.
codegen-correctness, safety-model, cpython-parity and architecture-fit
all clean; parity's structural argument is that with no `expected/**`
file changed, these commits only widen WHICH predicate accepts a shape,
never what is emitted. The meta-review pass dropped two findings as
repo-wide conventions rather than branch defects, and STRENGTHENED the
one real code finding: a routing pin asserting only render strings was
flagged, and the sibling offered as mitigation turned out to filter only
`resumable:`-prefixed fallback keys -- so a `body:`-prefixed fallback (the
exact shape its own boundary sibling asserts) slipped past BOTH. Both now
assert the whole dict.

### Orchestrated flip wave 2: 11 cells, 12 flips (2026-08-19)

Dial **3593/3731 -> 3605/3731**, markers 138 -> 126. Fourteen commits, full
suite green with FULL exec (11965 passed, 3730 built+run, 0 cache skips),
move-verdicts 0/926, binding-facts 0/11380, interop 31/34. Same orchestration
as wave 1: read-only scouts ablated candidates in parallel, implementers
landed cells serially in one worktree.

Flips: `record_print_fields` + `tuple_hash` (value-tuple compare operand;
native-protocol tuple per-element capture fork) / `dataclass_asdict_dict_tuple`
+ `dataclass_asdict_fuzz` (print-sink tuple storage spelling + dict-comp tuple
value slot) / `truthy_frame_value_opt` + `narrowed_value_opt_frame_faces`
(value-optional resumable frame shapes) / `generic_optional` (scalar rvalue at
a pointer-repr Optional arg) / `tuple_borrow_const_deep_source` (field-receiver
subscript at a storage-tuple alias + const registration) / `bytearray_aliases`
(bytearray ref-alias + rvalue ctor arg) / `key_lambda_generic_element` (open
value-tuple call family) / `global_walrus_shapes` (scalar name at a value-opt
ternary arm) / `union/isinstance_static` (const-folded match guard).

**EVERY ROW WAS A GATE-ONLY WIDENING RIDING AN EXISTING RENDER ARM.** No new
THIR node types, no new C++ emit code, no design decisions, across 11 cells.
Confirmed independently by the merge review.

**TWO MEASUREMENT INSTRUMENTS WERE FOUND BROKEN, and both had been steering
the work:**
1. **`Compiler._thir_routed_bodies` is BLIND to resumable frames**
   (`compiler.py:3513` sums `thir_functions + thir_constructors`, never
   `thir_resumables`). A generator/async flip leaves the count UNCHANGED, so
   the "count routed bodies before/after" rule -- the standard defence against
   mistaking a fallback for a flip -- reads a real flip as a fallback. Two of
   this wave's flips would have been discarded on that evidence. Use a
   `lower_resumable` wrapper reporting `res_routed=N/M` for those shapes.
2. **`sites_multi.py` cannot see resumable frame-gate rejects at all.**
   `resumable.py`'s `_reject` does `note(reason); return None` and never
   constructs a `ThirUnsupported`, which is what the census hooks. Every
   generator/async case's site set in that file is a LOWER BOUND missing a
   whole class. Measured on a case whose live `res.yield_type` fallback
   appears nowhere in its census entry.
   (A third instrument failed earlier in the session: one scout's byte-diff
   harness mapped `<out>/<case>/main.d/...` onto `expected/main.d/...`, found
   no counterpart, silently skipped, and printed IDENTICAL having compared
   ZERO files. Strip the `.d` component and print an explicit `cmp=N`.)

**THE THREE LARGEST RAISE SITES ARE DEAD ENDS, proven case-by-case, and the
census sole-site label was wrong NINE times.** Per-body lowering aborts at the
first `ThirUnsupported`, so a second blocker in the SAME STATEMENT is invisible
to the census; and some "sites" are one shared raise for a whole dispatch
ladder (one such: 6 cases, 2 labelled sole, exactly 1 flipped). Rank by
ablation, never by census count. The flips came from PAIRED small sites --
two rows in different files, neither flipping anything alone.

**THE BLAST SWEEP, NOT THE FLIP PROOF, VALIDATES A GATE'S KEY.** Three rows
this wave were byte-identical on their own flip case with a WRONG key, each
exposed only by a neighbour: a print-sink row keyed on `elem_capture` turned a
record alias into a copy; a bytearray ctor row placed in the predicate SHARED
with the free-call loop diverged there (the AST hoists a temp where the ctor
passes bare); an owned-str arg row in its wide form broke a pin. A flip proof
over the target cases is not evidence for a gate's key.

**`move_audit` IS STRUCTURALLY BLIND at copy/alias-position arms** (`joined=0`,
`ast_recorded=0` -- the AST asks no move question there, so the join
denominator is empty and it reports green regardless). It cannot be cited as
coverage for those rows; only the byte-diff and mutation-observing cases can.
One shipped wrong-code divergence was found and fixed this way (a
native-protocol tuple literal moving what the AST aliased -- SEVEN shapes, six
fixed, the seventh filed pending an AST-oracle decision), plus one near-miss
caught during scouting before it landed.

**Look for the already-landed SIBLING row before designing.** Nine rows this
wave were verbatim siblings one sink over: `decl.opt_none` needed mirroring to
the frame-field arm, the list comp's node-gated tuple row to the dict leg,
`narrowed_ok` to the setitem gate, `btuple_slot` to the return sink,
`_is_borrow_ptr_local` nineteen lines below the arm that needed it. No site
histogram surfaces these; only reading the sibling gate does.

Four defects filed (one since fixed): a resolved-scalar rvalue passed by VALUE
into a pointer-repr Optional CTOR slot -- ill-formed C++ in a body that ROUTES,
no fallback masking it (FIXED by `9869d5d67`, regression-pinned); a hard
`THIRValidationError` when any arg face hoists a `THIRArgTemp` inside a
short-circuit RHS -- a CRASH, not a fallback, so it BLOCKS CUTOVER (the ternary
form is already fenced, `and`/`or` are not -- an incomplete fence, not a
missing one); an AST-side hoist inside a short-circuit RHS evaluated
UNCONDITIONALLY (CPython skips it); and a dropped `tuple_to_pointer` borrow
lift at a relayed `Own[tuple]` unpack, changing what the binding aliases.

**Pin hygiene, two failures worth repeating:** a routing pin filtering fallback
keys by the `resumable:` PREFIX let a `body:`-prefixed key through -- the exact
shape its own boundary sibling asserts; pin the WHOLE dict (or the exact dict,
when a fixture legitimately carries a deliberate boundary reject). And FIVE
pins needed conversion because a row removed their stated reason -- **two of
them were found only by the full suite, not by the authors' shape-greps**, so
the fence sweep is not a substitute for running everything. Watch specifically
for a pin that keeps passing for a NEW reason: byte-identity and the ratchet
are both blind to that.

Merge review (2026-08-19): six specialists over 14 commits. Zero code defects;
codegen-correctness, architecture-fit and cpython-parity clean. Parity
confirmed the new `_open_value_tuple` predicate cannot reach the filed
relayed-`Own[tuple]` bug (it admits only value-typed or unbound-type-param
elements), so the wave does not widen that defect's reach.

### Orchestrated flip wave 3: 11 cells, 11 flips (2026-08-19)

Dial **3605/3731 -> 3619/3731**, markers 126 -> 112. Fifteen commits, full suite
green with FULL exec (12003 passed, 3730 built+run, 0 cache skips),
move-verdicts 0/926, binding-facts 0/11397, interop 31/34.

Flips: `nested_def/rebind_slot_in_nested_def` + the `_ctor` flavor (a nested def
draining its OWN hoist lines inside its lambda) / `calls/overload_generator` +
`calls/overload_generic_mixed` (two INDEPENDENT gates) /
`native/native_transitive_fields` (a Ptr-valued field-chain intermediate) /
`error_return/walrus_borrow_alias` (an `@error_return` METHOD call unwrapped in
expression position) / `records/macro_quote` (a macro-authored staticmethod) /
`auto_move/auto_move_ctor_copy_warnings` (an `Own`-param MIL copy at NON-last
use) / `records/optional_field_own_return` (an `Own[T]|None` call assigned BARE)
/ `match/capture_not_rebound_still_aliases` (a ptr-hoisted field capture binding
by address) / `error_return/borrow_result_source_intact` (an aliasing ER bind at
a rebind-slot target) / `control_flow/with_target_pointer_bearing_value` (a
borrow-tuple frame field as a resumable with-target) /
`records/ctor_param_name_collision` (a value-position isinstance over a
resumable frame subject) / `list/comp_array_error_return_fallback` (a
comprehension source at the OPTIONAL_STORAGE reseat).

Every row was a gate widening or a placement fix riding an EXISTING THIR node
type. No new node types across the wave. Confirmed by the merge review.

**A CELL WAS DELIBERATELY NOT LANDED BECAUSE THE AST EMITS AN ACCIDENT.**
`generators/yield_loop_body_local_borrow` looked like a proven SMALL flip, but
its oracle carries `::tpy::frame_slot<std::vector<int32_t>> x;` as a frame
member that is NEVER REFERENCED in `__next__` -- the case block declares a
SHADOWING `std::vector<int32_t>* x = nullptr;` for the same name. The AST emits
a dead frame slot plus a shadowing local. Per the AST-first rule this needs an
AST-side fix (drop the dead slot, its own snapshot-churning change) BEFORE any
THIR row. `TODO.md` and the named fence at `expressions.py:5834` already park
it; the implementer found the fence by reading the oracle after the scout's
brief omitted it. Mirroring would have planted the accident permanently.

**COST LABELS DERIVED FROM TRIAGE RATHER THAN ABLATION WERE SYSTEMATICALLY TOO
PESSIMISTIC.** A triage classified 11 remaining sole-site cases as NEEDS RENDER
("a cell, not a row"). Sizing them by ablation found SIX were SMALL and proven
to flip alone, every one reusing an existing node type. The orchestrator twice
declared the cheap tail exhausted on the strength of those labels and was twice
wrong. Probe before pronouncing -- including about your own estimates.

**THREE SCOUT CHARACTERIZATIONS WERE CORRECTED BY MEASUREMENT, one of which
would have caused a real bug.** A brief quoted an oracle as `this->s->a.q.flag`;
it is actually `::tpy::deref_check(this->s).a.q.flag` -- mirroring the arrow
would have DROPPED A NULL CHECK (the AST emits `->` only when sema proved
non-null). The same brief had the rejecting node wrong (the third hop, not the
second) and predicted a new render arm that was not needed at all. A second
brief called two cases "one shared predicate"; they are two independent gates,
and the cross terms prove neither clears the other's case.

**A FILED BUGS ENTRY WAS WRONG AND WAS CORRECTED.** `BUGS.md:130` claimed its
fix would RETIRE two of this branch's markers. Checked: both inner locals are
genuinely rvalue-rebound inside the nested def, so a correct prescan reserves
the same slots on their own merit -- the fix would make the old reject fire
WIDER, not narrower. No contention with the flip; the sentence was corrected.

**THREE IMPLEMENTERS LANDED ROWS NARROWER THAN THEIR SCOUT SPECIFIED**, each
justified by reading the AST arm: a match-capture key mirroring `_emit_binding`'s
full declared-var ladder (with a DISCRIMINATING case built to prove one conjunct
load-bearing -- an owned-optional hoist where the AST spells `std::move` and
dropping the conjunct emits `&(...)`); a value-position isinstance arm that
already EXISTED and only excluded generators (one conjunct dropped, nothing
else); and a comprehension admission placed at the reseat rather than in the
shared predicate whose other callers keep vetted source lists.

**A NON-COMPILING NEGATIVE WAS REPRODUCED AND COMPILED before its fix landed.**
The `Own[T]|None` bare assign: with only the admission row, THIR hands a
`std::optional<Point>` to `ptr_to_optional`, which takes a `T*` --
`error: no matching function for call to 'ptr_to_optional(std::optional<Point>)'`
against the real runtime header. Both halves now share ONE predicate so
admission and lift-suppression cannot drift.

Merge review (2026-08-19, six specialists over 15 commits): zero code defects.
codegen-correctness, cpython-parity and architecture-fit clean. Two findings
worth keeping: codegen observed that although `move_audit` is blind at the ctor
MIL, a wrong move/copy verdict there still CHANGES EMITTED TEXT (`std::move(...)`
vs bare) and the byte-diff catches it -- so the blind spot is real but not an
uncovered hole for that row. And THREE reviewers independently converged on one
unwitnessed conjunct (`frame_local_types` in the match-capture ladder): it is a
STRICT SUBSET of what the AST treats as a frame field (missing params, `__self`,
forwarded aliases), provably inert today because the resumable match gate
rejects every record-field capture before it runs, but the docstring implied a
completeness it does not have. Documented at the guard rather than widened.

SHORT-CIRCUIT ARG-TEMP FENCE (2026-08-19, fix-argtemp-shortcircuit): the
nested-temp threading landed in the 2026-07-22 adapter/temps wave gave
`_lower_binop` operands the enclosing statement's flush right unconditionally,
including `and`/`or` operands whose RHS may never run. The validator has always
reset `argtemp_ok` at a logical binop (as it does at a ternary's arms), so any
arg face that hoists a `THIRArgTemp` there built a node the validator then
rejected with a hard `THIRValidationError` -- which escapes the per-body
fallback boundary and kills the compile instead of falling back. Four faces
reached it (`optptr.ctor_rvalue`, `optptr.container_temp`, `optptr.scalar_temp`,
`argtemp.own_copy`), on both operands, in both `and` and `or`. `_lower_binop`
now drops `temps_ok` for `_LOGICAL_OPS`, matching the ternary fence
(`_lower_if_expr` grants the right to the condition only). Deliberately a FENCE,
not a render: the AST's own hoist there sits OUTSIDE the short circuit and is
filed as a CPython evaluation-order divergence, so mirroring it would bake in
behavior already known to be wrong -- the shape can only route after the AST
oracle is fixed. No corpus case held the shape (dial unchanged). Pinned per face
in `tpyc/thir/test_thir_shortcircuit_argtemp.py`, each with an inverse routing
pin at a flushable position; deleting the fence turns nine of them into errors.

**CORRECTION (2026-08-19): the stated reason above no longer holds.** The AST
oracle was fixed by `143d7f1dc`, which defers a conditional-operand temp into
the operand it belongs to -- so the hoist no longer sits outside the short
circuit and the evaluation-order divergence it cited is closed for `and`/`or`,
both ternary arms, and the chained comparison. The fence may still be wanted,
but it now needs a different justification: the AST splits per create-site and
per type, keeping a NON-MOVABLE payload and every `create_typed` face eager
while deferring the movable ones, so only the eager slice is an ordinary
byte-identical mirror. The deferred slice needs a render THIR does not have.

### Orchestrated flip wave 4: six lowering cells + an AST fix, 7 flips (2026-08-19)

Dial **3619/3737 -> 3627/3739**, corpus markers 118 -> 112, interop 32/34 (2
markers). Thirteen commits, full suite green with FULL exec (12110
passed, 23 skipped, 3738 built+run; 12107 passed / 26 skipped comp-only),
move-verdicts 0 divergences over 927 joined nodes, binding-facts 0 gaps over
11475 joined bodies.

**Read the dial delta precisely.** Migrated moved +8, but that is SEVEN corpus
flips PLUS one NEW case added unmarked that routes clean
(`control_flow/ternary_self_arm`). Marker math: 118 - 7 flips + 1 new MARKED
case (`operators/short_circuit_brace_arg`) = 112. The interop flip
(`tests/interop/dunder_identity`) is tallied separately and is NOT inside the
3627. The figure in this very entry was nearly hand-copied from memory;
re-measuring changed it and corrected a "+8 flips" claim into "7 flips + 1 new
clean case".

Flips: `operators/coercion_unified` + `pointers/ptr_to_value_coercion` (the
`Ptr[T]` -> record deref-coerce at the decl and return borrow slots) /
`operators/op_borrow_return_alias` + `operators/op_readonly_borrow_return` +
`tests/interop/dunder_identity` (borrow-dunder alias decl + lvalue ternary
borrow return, with a `_f1_is_const` companion fix adding the TpyBinOp /
TpyUnaryOp arms) / `operators/op_borrow_branch_hoist` (reseat a hoisted pointer
over a borrow-dunder render) / `optional/opt_field_iter_narrow` +
`generators/gen_readonly_param_iter` (the `Optional[container]` field at its
narrowed READ and its ctor member-init-list WRITE, five rows).

**TWO CELLS FLIPPED NOTHING AND ARE STILL THE WAVE'S LARGEST GAINS.** The
record-ternary `self`-arm deref shipped no flip because the shape ALREADY
ROUTED -- and emitted `this` where the AST emits `((*this))`, i.e.
uncompilable C++ from a routed body with NO corpus witness. And the resumable
frame-field walrus row -- a five-way dispatch on the frame-layout PLAN kind
plus a new `THIRWalrus.emplace_cpp` field and emit mode -- routed **+11
resumable bodies** (`gen_walrus_frame_shapes` 0/10 -> 8/10,
`async_walrus_frame_shapes` 2/6 -> 5/6) with zero flips: both witness cases
also block at a free-call argument terminal (`call.native_arg.other` /
`call.arg_shape.optional`), which no walrus rung can clear.

**AN AST REGRESSION WAS FOUND AND FIXED MID-WAVE.** A deferred
conditional-operand temp banked `__tmp.emplace({1, 2, 3})`, which is ill-formed
because `emplace` deduces `initializer_list` independently of the container's
element type. Regression from the conditional-operand temp deferral already on
master. Repaired at the chokepoint (`TempState._register`, new `_as_expression`
helper) rather than at the symptom site, with zero snapshot churn;
`operators/short_circuit_brace_arg` is its regression guard (added marked --
it does not route).

The `Ptr[T]` cell landed a deliberate FENCE beside its rows: a
`Ptr[readonly[T]]` source at a MUTABLE slot, where the AST itself emits
ill-formed C++. Filed, not mirrored, per the AST-first rule.

Also in the wave: seven stale pins/comments converted (each had kept passing
for a reason a row had removed), and `dualgen.py` hardened to refuse a vacuous
`IDENTICAL` -- it now prints `cmp=N` and errors when it compared zero files.

**EVERY ONE OF THE SIX CELLS PRODUCED A CORRECTNESS FINDING, AND NOT ONE CAME
FROM THE CORPUS, THE BYTE-DIFF, THE RATCHET, OR A GREEN SUITE.** All six came
from adversarial dualgen / boundary probes aimed at the gate being widened. The
`self`-arm defect is the sharpest instance: a routed body emitting uncompilable
C++ that no instrument in the standing set can see, because no case holds the
shape. Probe the gate you are widening; the suite only covers what the corpus
already contains.

**TWO SUPPOSEDLY-DEAD LEGS WERE SETTLED BY BUILDING A DISCRIMINATOR, one in
each direction.** In the first, a scout's stub had read as "no effect" for a
STRUCTURAL reason -- the plain tail was gated on a set the leg's own targets
were subtracted from, so ablating the leg could not move anything, and the leg
was real. In the second, three discriminating shapes were built and none
reached the arm, so the leg was correctly dropped. "Try hard to build a
discriminator before dropping a dead leg" paid twice in one wave, and the two
payoffs pointed opposite ways.

**A REVIEWER'S CAUSAL CLAIM WAS REFUTED BY ABLATION.** codegen-correctness
reported that the `Optional[container]` cell INTRODUCED the compare-operand
divergence (`this->lst` vs `(*this->lst)`). Ablating the new read row left the
divergence unchanged, proving it pre-existing; the filed BUGS entry stands as
written and the cell needed no change. One ablation run settled what two agents
disagreed about.

**RANK BY ABLATION, NEVER BY CENSUS COUNT -- again.** The census's "sole-site"
label was wrong for 2 of 3 candidates this wave, and a recorded "~10-case
cluster" measured as THREE cases, all dead. This is the third consecutive wave
in which census-derived rankings misdirected the opening move.

**THE PAYING UNIT OF WORK IS NOW THE SITE PAIR, NOT THE SITE.** 69 of the 118
blocked cases reject at exactly TWO sites. Ranking single sites by touch-count
is therefore the wrong instrument at this stage: the modal blocked case needs
two rows in different files, neither of which flips anything alone. Select
pairs, and price both halves before opening a cell.

Seven defects filed (`BUGS.md`), one pointer each:
- A `Ptr[readonly[T]]` source at a NON-readonly record slot emits an ill-formed
  bind at both the local-decl and the return position (AST-side; THIR fences).
- A container LITERAL at a union param builds the ptr-variant from the
  literal's own expression type instead of the union member's.
- THIR's method-call argument lowering lacks the TypeParamRef temp-hoist its
  free-call sibling has (latent divergence, no case covers the site).
- An `IntN(0)` constructor call loses its cast when it is a ternary arm
  (latent, `-Werror=conversion` only).
- `match self:` emits an ill-formed match subject -- a non-const reference
  bound to a pointer rvalue, then a `dynamic_cast` of a `Pet**`.
- A WALRUS iterable in a RESUMABLE `for` head is rendered TWICE: the side
  effect runs twice and `begin()` points into destroyed storage (silent UB).
- A NARROWED `Optional[container]` field at an EQUALITY operand loses its
  unwrap on the THIR path (latent divergence; the ablation above).

The pre-existing record-rvalue/mutated-method-param entry also gained a
container-literal witness (`bx.take([1, 2, 3])`), which needs no short circuit
or other context -- so its fix must cover literal args, not just record
rvalues.

### Cutover checklist item 4 -- the skeleton call-site inventory (2026-08-20)

Item 4 asked for "every skeleton -> `gen_expr`/`gen_stmt` call site,
inventoried and ground to zero". The inventory is now mechanical and lives
where it cannot rot: `tpyc/codegen_cpp/test_cutover_gate.py` scans the
fifteen skeleton modules for every call whose receiver chain crosses
`self.expressions` / `self.statements` / `self.statements.match` /
`self.statements.builtins`, and freezes each with the disposition that makes
it acceptable today. Adding a call fails a unit; discharging one means
deleting its entry in the same change.

**The Gate D3 entry's grep found 4. The scan finds 78 calls (62 distinct
sites) in four modules.** Classified by the only question that matters for
the deletion commit -- does the call fire when the enclosing body ROUTED?

| disposition | calls | what it is |
|---|---|---|
| SEAM | 6 | `statements.gen_body` -- the per-body routing entry point itself; its dispatch collapses at cutover |
| AST_ARM | 38 | guarded by `thir_ctor is None` / `thir_top_level is None` / `leaf is None`; deleted with the AST path |
| OPEN | 34 | fires on the routed path -- the actual cutover gate |

**The finding that re-prices item 4: 31 of the 34 OPEN calls are in
`gen_async`, not in the two sites D3 named.** The resumable frame skeleton
leans on `statements` for far more than leaf emission -- `setup_body_scope`,
`nested_def_signature` + `gen_nested_def_body`, `_make_async_return`,
`_emit_finally_chain` (x7), `_emit_except_handler_header`,
`_emit_isinstance_extractions`, `_gen_range_overflow_check`,
`promote_movable`, and six shared predicates/type helpers
(`_is_plain_nonvalue`, `_is_const_indirect`, `_extract_int_literal`,
`ptr_slot_field_type`, `_get_cpp_declared_type`,
`_maybe_unwrap_narrowed_optional`). None of these is a fallback arm; the
leaf seam replaces only the LEAF renders. They are not "route it through
THIR" work at all -- they are *relocation* work: helpers that live in a
module slated for deletion but are called from a layer that survives. Where
they land post-cutover is an open design question, and it gates the
"wholesale deletion" property just as hard as an unrouted `gen_expr` does.

**The two sites D3 named are routable, and the experiment says so.** A
throwaway probe lowered Final-global and class-constant initializers through
`_LowerCtx` + `_lower_expr` + `_emit_expr` and byte-compared against
`gen_expr`: 22 of 25 distinct class-constant renders and 4 of 6 Final-global
renders matched with nothing but `prescan.global_readonly` seeded for the
sibling constant names, `((A) + (B))` and
`((((((A) + (B))) * (::tpy::BigInt(10)))) - (::tpy::BigInt(1)))` included.
The three misses are all seeding, not machinery: sibling CLASS-constant
names were not in the seeded set (`mul_check`/`add_check` renders rejected at
`name.global_read`), the top-level bare int literal needs
`_slot_literal_retype` against the declared type (`100` vs
`::tpy::BigInt(100)`), and a tuple constant needs the stored-vs-view element
form. **The dedicated-renderer option is the wrong one here:** the domain
includes overflow-checked arithmetic (`::tpy::mul_check<int32_t>(BASE, 2)`),
so a "small constant renderer" would be a second copy of the arithmetic
renderer -- exactly the permanent wart the AST-first rule exists to prevent.
The domain was measured, not assumed: 202 distinct Final-global renders over
a 606-case sample (stdlib included, since the skeleton is shared) -- 127
coerced literals, 33 binops over Final names and literals, 17 one-arg
primitive-constructor calls, 4 tuple literals, and one macro expansion.

Routing them does not close the gate outright, it converts OPEN to AST_ARM
(the skeleton would call `gen_expr` only when the constant did not lower),
which is the same shape every body position already has and is what makes
the deletion wholesale. Two decisions were left to the user rather than
taken here: whether a non-body lowering position is in scope for this
migration at all, and whether its fallbacks join `_thir_fallback` (and
therefore the per-case ratchet) or stay a separate, untallied residue.

**Outcome, same branch: 34 OPEN -> 5.** The measurement above stands as
written; what follows is what the branch then did with it, because the
34 -> 5 delta is the reusable fact, not the 34.

Both escrowed decisions were taken in-branch and ratified by the user at
the review gate. A non-body position IS in scope: `tpyc/thir/constants.py`
routes the class-constant default and the `Final` global initializer,
converting their two OPEN calls to AST_ARM. Their fallbacks are TALLIED in
`_thir_fallback` but, at the time, EXCLUDED from the ratchet
(`fallback.NON_RATCHET_COMPONENTS = {class_const, final_global}`, read via
`ratchet_total`) -- neither of the two options the entry named, but a third:
no case's `no_thir.txt` marker was ever classified against a non-body
position, so folding these into the ratchet would have failed previously-clean
unmarked cases without any regression having occurred. The residue stayed
visible and drivable to zero, at the honest and known cost that a case whose
constant initializers still emitted through AST passed the ratchet and
counted migrated on the dial.

**Closed 2026-08-30: `NON_RATCHET_COMPONENTS` is now EMPTY.** The residue it
covered reached zero (the nonfinite float literal and the `Char(n)` type-ctor
were the last two shapes), so the two components fold into the ratchet with
no case regressing -- and the dial overstatement they carried is gone. Those
positions render through the same `gen_expr` the cutover deletes, so counting
them was always the honest reading.

The other 29 were relocation, and got relocated rather than routed:
`tpyc/codegen_cpp/emit_prims.py` gives the shared primitives a home outside
the modules slated for deletion (see its `Landed:` bullet). That is what
re-closes item 4 from 34 to 5 -- the paragraph above calls the placement
"an open design question", and for these it is now answered.

**The 5 survivors are all in `gen_async`, and all genuinely undecided**
(now 0 -- ALL RESOLVED 2026-08-31, see checklist item 4 and the wave entry
under "Maintaining this ledger"; the "needs a decision, not a move" verdict
below held for three of the four and was wrong for the fourth)
(was 4 -- `gen_nested_def_body` resolved 2026-08-21, see the six-forks
entry under "Maintaining this ledger") --
they are not relocation. `gen_nested_def_body` (a nested def inside a coro
finally), `_make_async_return` / `_make_generator_resumable_return` (the
deferred-return recipes the leaf emitter reaches through),
`_extra_template_args_for_await` (dispatches an arbitrary expression), and
the ReturnT terminator in `_walk_inline` (`gen_stmt` runs the active finally
chain around the `Poll<T>::ready`). Each needs a decision about where the
behavior lives post-cutover, not a move.

### The thir-tail four-wave push (2026-08-21)

Dial **3635/3739 -> 3678/3739**, corpus markers 104 -> 61 (the 2 interop
markers unchanged). Forty-three flips across four waves stacked on one
branch: resumable-frames, readonly-dynamic, misc-expr, and tuple-core --
the two middle-sized ones grown as lane branches and merged back.

**A wrong-KEY divergence was found and fixed at the top-level tuple
reseat.** A namespace-scope storage-tuple write (`g: tuple[T | None,
T | None] = (t1, t2)` after a first binding) was keyed through
`_borrow_tuple_local_type` as if the target were a borrow LOCAL, so the
reseat emitted the borrow literal BARE -- ill-formed C++ (`g =
std::tuple<T*, T*>{t1, t2};`, no `optional<T> <- T*` conversion) from a
routed body with no corpus witness (the corpus sibling's None element
used to fall the whole body back). Dualgen-proven, fixed by keying the
write on the global's storage form, and pinned as a regression unit in
`test_thir_tuples.py`.

**Resumable-frames produced five pre-commit catches, none from the
corpus or a green suite.** (1) dualgen: admitting an Own[CONTAINER] sgen
capture flips the skeleton's iterable-strategy classification
(`ctx.var_types` is seeded by the AST's gen_body, not the leaf path) --
scaffolding divergence, shape pinned AST. (2) dualgen: the newly
readable mixed frame name exposed the `tuple_to_storage` lift row
wrapping a bare mixed pass (`show((*p))`); `_own_tuple_shape_match` now
excludes MIXED slots. (3) a unit pin: the frame-slot `tuple_source`
threading pre-empted the Own[tuple]-declared callee's
`call.own_tuple_storage_ret` row (face shift only, byte-identical). (4)
a latent alias leak: the resumable BB loop restored `lc.narrow` by
IDENTITY while `branch_scope` restores by replacement, so an arm-scoped
hook-mode install leaked the last arm's alias into every later BB --
masked until the blanket rejects lifted; the loop now restores by value
snapshot. (5) a latent validator crash: a non-branch borrow-tuple name
init fell through to the storage-lvalue lift tail and died on the
no-op-convert check.

**Flagged for a follow-up sweep: the narrow-kill / guarded-hook state
stack.** The mid-BB narrow KILL (a top-level rebind pops the alias at
the rebinding leaf) and the guarded_union dispatch-hook mode both
install per-arm state on `lc.narrow` that the resume-env walk must
restore exactly; catch (4) above shows how easily an identity-vs-value
restore mismatch slips through when blanket rejects stop masking it.
Every save/restore around the resumable BB walk deserves one deliberate
pass before more hook modes land.

**A named cutover-consolidation mirror pair:** `predicates.py
_renders_own_borrow_tuple` <-> the codegen context's
`renders_own_borrow_tuple` (`codegen_cpp/context.py`). The THIR
predicate deliberately re-derives the AST-side fact so the two paths
agree statement-for-statement; at cutover the pair collapses to one
definition, and until then any change to either side must touch both.

### The thir-tail six later waves (2026-08-22)

Dial **3678/3739 -> 3724/3739**, corpus markers 61 -> 15 (the 2 interop
markers unchanged). Six more waves stacked on the same branch --
optional-core (7 flips), iterables-comp (11), optional-decl-print (11),
tuple-unpack-ops (8), stdlib-method (5), odds (4) -- for 46 flips here
and **89 for the branch: 106 markers -> 17** (104 corpus + 2 interop at
the start, 15 corpus + 2 interop now). Marker count re-measured with
`find tests -name no_thir.txt`, not carried from a wave report.

**THE TAIL WAS SCOUTED BEFORE IT WAS GROUND, AND THE SCOUTING PAID --
BUT EVERY SCOUT VERDICT WAS A HYPOTHESIS, NOT A PLAN.** Ten read-only
scouts mapped the ten clusters (blocker, reject site, proposed row,
predicted flips) before a line was written, and the predictions were
close enough to partition the work into non-overlapping lanes. They were
also wrong in a specific, repeatable way: **the scout locates the reject,
the implementer discovers the shape.** The falsified premises, one per
cluster, are the durable record:

- **iterables-comp**: the consuming-iterable wrap does NOT belong as a
  rung on the free-call ladder. The AST's consuming arm runs in
  `gen_call_arg` for EVERY position, so keying the wrap on any position
  flag would have left per-position divergences; it landed position-blind
  in `_lower_call_arg`.
- **stdlib-method**: two near-misses in one cluster. Cell A keyed on the
  container WRAP (as scouted) would have REGRESSED value-tuple f-string
  returns -- a value-tuple call and a container-ctor call both route today
  at the plain VALUE sink and ITERABLE has no tuple row. Cell C's
  `movable_local` key is TRUE for `Own` PARAMS too (`_LowerCtx` seeds
  every non-value Own param movable): the broad key gave a fully green
  corpus AND byte-identical dualgen on every param shape buildable, and
  broke SEVEN committed fences. Seven pins asserting the same boundary is
  a design decision, not staleness -- narrowed to `not is_param`.
- **odds**: pascal/m17's blocking field is `PStr[255]`, the pascal
  runtime's own GENERIC USER-RECORD, not tpy `String`. `_f1_record` is
  False for it, so the case needs the record-spelling slice widened, not
  the scouted result row. The scouted String row landed anyway (verified
  independently) and flips nothing.
- **tuple-unpack-ops**: user dunders DO carry a `cpp_template` --
  `sema/protocols.py` injects `get_dunder_cpp_template`, so the existing
  unary arm already renders them and no new render row was needed, only
  the DECL admission.
- **optional-core**: the cross-module import blocker was not an alias-
  resolve miss in `_ru_wrapper_field_arg` (disable-and-probe showed that
  leg DEAD and it was reverted) -- the row was simply absent from the
  marker/qualcall ladder.
- **optional-decl-print**: cell F was mis-scoped as "const threading into
  an existing row". No arm accepted a storage-opt NAME source at all,
  const or not, and the decl sits in-branch, so it also needed
  branch-first admission.

**EVERY LATENT DEFECT THIS HALF OF THE BRANCH FOUND CAME FROM ADVERSARIAL
DUALGEN OR A BLAST SWEEP AROUND THE GATE BEING WIDENED. NOT ONE CAME FROM
THE CORPUS BYTE-DIFF, THE RATCHET, OR A GREEN SUITE.** Five worth naming:

- A namespace-scope storage-tuple write was keyed through
  `_borrow_tuple_local_type` as if the target were a borrow LOCAL, so the
  reseat emitted the borrow literal bare -- ill-formed C++ from a routed
  body with no corpus witness. (Carried over from the first four waves;
  repeated here because the same wrong-key shape recurred.)
- Routing `pascal/set_of`'s top level made THIR emit `a = &*(__slot_1 =
  ...)`, spelling a `__slot_N` identifier module scope never declares
  (the AST spells `__global_slot_1`): the raw name-target assign arm
  bypassed `_lower_global_slot_write` entirely, while the var-decl arm had
  delegated to it all along. Only reachable from a frontend AST and only
  once the rest of the body routes. **"CLEAN" is not "flips", and it is
  not "correct" either** -- the scout had checked fallback only.
- The PRE-EXISTING `reseat.opt_ptr_copy` arm MIS-CLAIMED a shape: a
  storage-opt source carries the same DECLARED ptr-repr Optional type as a
  `T*` binding, so that arm emitted `first = it` and dropped the
  `optional_to_ptr` lift. Found by the neighbour sweep, not by the drilled
  case.
- The wrapper-REFERENCE-element unpack rung admitted a NAME source as well
  as the call source; the AST binds `auto& __tup_N = p;` there (the
  is_ref holder rule) where the arm's bind spelled `const auto&`. The
  implementer's own probes all used the corpus's CALL source -- the rung
  is now scoped to call sources.
- A VIEW-inner value-opt LOCAL's `is None` emitted `v == nullptr` instead
  of `!v.has_value()`, because the is-None `value_repr` disjunction was
  missing the declared-type `_value_opt_view` row.

**PIN ROT IS A CROSS-LANE COST, AND A LANE'S OWN TARGETED RUNS ARE
STRUCTURALLY BLIND TO IT.** About nineteen committed fences were
invalidated by later widenings and had to be converted to routing pins
(commits `c2b54a56c` 9, `95bf7e126` 4, `ee675fa11` 3, `5e509168f` 3).
Several were found ONLY by a full `pytest tpyc/` run: shape-grepping the
obviously-related files missed them -- one ctor-arg fence survived a
384-test sweep of the four ctor-arg pin files and fell to the full suite,
and the storage-opt loop-var work broke a pin in `test_thir_ptr_locals.py`
that no grep of the optional/print files would reach. The structural
reason: a lane runs the files ITS shapes touch, but a widening invalidates
fences keyed on a REASON, and the reason can be stated in a file about a
different construct. Budget one full unit run per lane before merge, and
treat "my targeted files are green" as no evidence at all about fences.
Each conversion must name the exact C++ now emitted -- flipping a
`is None` assertion to `is not None` is a WEAKER claim than the fence it
replaces, because a whole-body fallback satisfies it.

**TEN AGENTS IN TWO-LANE ROUNDS, WITH EXPLICITLY PARTITIONED FILE
TERRITORIES, COST EXACTLY ONE TEXTUAL MERGE CONFLICT** (the arg ladder in
`lower/expressions.py`). The partition was by RESERVED ARM, not by
directory: each lane's brief listed the predicates and render arms it
owned and the ones another lane held. One deviation was needed and handled
without breaking the partition -- the own-dunder rebind reseat needed
`_rebind_rvalue_source_ok`, which another lane owned, so the disjunct went
at the REBIND_SLOT **call site** instead of inside the shared predicate.
That is better scoping anyway (the reseat is the only witnessed sink), and
it is the general move when a predicate a lane needs has other callers.

**The merge-time hazard is the "pre-existing failures" claim.** Three
lanes independently reported the same four unit failures as pre-existing,
each proving it by checking out its own base and re-running. All four
proofs were correct AND the failures were not really pre-existing: they
were stale fences created by a SIBLING lane's widening, already merged
into the later lanes' bases. A lane cannot distinguish "broken before I
started" from "broken by the lane that merged just before me", so the
orchestrator must re-verify every such claim after the merge and route
each fence to the lane whose widening removed its reason. Left
unconverted, they read as a red suite of unknown provenance.

**Follow-ups for a future wave.** The 15 remaining corpus markers are no
longer one population:

- **AST-bug-blocked oracles** (the fix belongs on the AST side first, per
  the migration contract): `none_safety/narrowed_local_optional_reads` and
  `none_safety/optional_container_literal` both sit on the raw
  `(*lst)[i]` subscript defect (BUGS.md's two proven-ptr-repr
  `Optional[container]` subscript entries), and
  `tuple/element_vs_singleton_global` on the tuple-of-reference-type
  global borrow-slot entry.
- **By-design residue**: `native/extern_c_str_param` stays marked on
  `sig.linkage_c_abi` -- the C-ABI story is decided at cutover, not
  before.
- **One design fork**: `tuple/mixed_own_tuple_local_decl_paths` has 3 of 6
  bodies routing; `loop_carried` needs the if-hoist's `in_branch` scope
  fence lifted, which requires THIR's hoist registration to be
  FUNCTION-scoped rather than registering into the enclosing scope's
  `declared` copy. The try-hoist and walrus sinks were deliberately left
  unbuilt because the case cannot flip without it and an admitted-but-
  caseless arm would carry its own fixtures for no dial movement.
- **Named tracks, each a cell**: `str/fstr_decompose` and
  `argparse/custom_type` and `tplib/json_model_nested` (the macro/FStr
  surface), `pascal/m17_with_stmt` (widen the record-spelling slice to a
  generic user-record instance -- a blanket-precheck experiment confirmed
  the rest of that chain already renders byte-identically),
  `iterators/iterable_long_str_elems` (the global-vs-local arg gap, now
  filed in TODO.md: `declared` CONTAINS module globals, so no arg
  predicate can tell an owned-str GLOBAL from a local at an `Own[str]`
  slot; `lc.prescan.global_readonly` holds the answer and is not threaded
  in).
- **Not scouted, not attempted here** (five markers, carried in from
  before this push -- listed so the accounting closes at 15 corpus
  markers, plus the two interop markers, which this push did not touch
  either): `list/list_repeat_lazy` was scouted and then dropped on a
  stub probe -- the decl reject hides a ~7-arm chain (a lazy-repeat node
  and render, the decl slot family, the `len(x)` and `consume(x)` name
  args, the for-head over a repeat name, the print wrap, and the separate
  fixed-count statement-expr arm), and its Iterable arm additionally sits
  on the documented `render_type` crash over a still-pending arg type;
  `auto_move/auto_consuming_ctor_user` was deferred for a ctor-MIL ladder
  collision with a parallel lane; `records/dataclass_asdict_optional` was
  skipped as a no-sibling singleton (`_container_lit_elem_ok` has no arm
  for asdict's optional-wrapped ternary elements, and one case does not
  pay for one); and
  `protocols/protocol_generic_arg_infer_compound_return` (the open-T
  tuple `val_or_ptr` family) plus `bytes/bytes_optional_param_borrow`
  (the view-param reassign-copy FORM respell -- a deliberate reject that
  needs a new binding kind) were both scouted MEDIUM+ in the
  stdlib-method cluster and never attempted, that lane's budget having
  gone to its earlier cells. So for these five the recorded scout verdict
  is all that is known: nothing below it has been measured on this tree.

**Two mirror pairs to consolidate at cutover**, both deliberate
re-derivations of an AST-side fact so the paths agree
statement-for-statement, both collapsing to one definition when the AST
body emitters go: `predicates.py::_renders_own_borrow_tuple` <->
`codegen_cpp/context.py::renders_own_borrow_tuple`, and the THIR
`_value_opt_*` / `_optional_print_formatter` mirrors of `builtins.py`'s
`_optional_container_formatter` / `_optional_print_inner_cpp`. Until
cutover, a change to either side must touch both.

**The narrow-kill / guarded-hook state stack still wants one deliberate
pass** (flagged in the four-wave entry, unchanged here): both install
per-arm state on `lc.narrow` that the resume-env walk must restore
exactly, and the later waves added more hook modes on top of it.

### The final marker-tail push (2026-08-22/23)

Dial **3724/3739 -> 3733/3739**, corpus markers 15 -> 6, interop markers
unchanged at 2 -- **17 markers -> 8**. Nine flips:
`bytes/bytes_optional_param_borrow`,
`auto_move/auto_consuming_ctor_user`,
`protocols/protocol_generic_arg_infer_compound_return`,
`pascal/m17_with_stmt`, `list/list_repeat_lazy`,
`argparse/custom_type`, `str/fstr_decompose`,
`iterators/iterable_long_str_elems`, `tplib/json_model_nested`. Marker
count re-measured with `find tests -name no_thir.txt` (6 under
`tests/cases`, 2 under `tests/interop`), not carried from a wave report;
the dial is the comp-phase figure from the branch's last full green run
(12509 passed, 0 byte-diffs, 0 move-verdict divergences, 0 binding-fact
gaps), and the review-round commit that followed it flipped no case.
Zero snapshot churn: not one `expected/` file changed across the whole
branch.

Three read-only scouts partitioned the tail (small-arms, shape-widening,
macro-family); two grew as lane branches and merged back; an adjudicator,
a cell-G implementer, a `json_model_nested` implementer, a completeness
verifier and a review round followed on the integration branch.

**EVERY MIGRATABLE CASE IN THE CORPUS IS NOW DONE.** The eight survivors
are not a backlog of un-ground shapes -- each is blocked on an AST-side
fix or a design decision, enumerated at the bottom of this entry. This is
the end of the grind loop as a way of moving the dial.

**THE ADJUDICATION: WHEN TWO SCOUTS DISAGREE, MEASURE A DISCRIMINATOR
MATRIX -- DO NOT PICK THE MORE CONFIDENT REPORT.** The owned-`str` NAME
at an `Own[str]` slot drew CONTRADICTORY keys from two scouts (one:
thread `global_readonly` so a GLOBAL can be told from a LOCAL; the other:
no global-vs-local difference exists, so key it shape-blind). An
adjudicator ran 8 sources x 3 positions through the AST oracle and
measured **both wrong**. The owned global, the owned plain-function local
and the `Own[str]` param all render `std::string __tmp_N{x};` + move;
`StrView` names, view locals and bare `str` params take the S1 inline
`std::string(x)` convert. The real key is **owned-form vs view-form**,
which `declared`/`locals_` plus `param_names` already answer --
`global_readonly` is never consulted. The matrix bought three things a
verdict-pick could not:

- **A third lockstep site both scouts missed.** `_own_move_source_slice`
  calls the shared predicate with NO `locals_`; leaving it alone made a
  frame-promoted local's last use diverge (the bare `std::move(t)` the
  AST emits inside a resumable frame became a copy temp). Threading
  `declared` + `param_names` there is what makes the row byte-identical.
- **A reproduced miscompile in the "cheap" option.** Widening only the
  GATE, not the renders, dropped the copy temp entirely
  (`xs.push_back(NEEDLE)` where the AST hoists and moves) plus the
  knock-on temp-counter shift. `_own_lvalue_temp_slot` is the SHARED
  verdict for gate and render; they move together or not at all.
- **The correct disposition of three committed fences.** One scout had
  called `test_str_owned_local_arg_ineligible` "THE boundary pin, it MUST
  still reject". It fences a shape the AST renders identically to the
  global: keeping it rejecting is what would have made the row wrong.

The same matrix also fenced the row: at an `Own[bytes]` slot the AST
passes an owned name BARE (`str` is carved out of `gen_call_arg`'s
`inline_template` lvalue skip), so the bytes sibling stays rejecting on
its own render and got a new boundary pin.

**THE MIRROR WAS INCOMPLETE IN A SECOND WAY, AND ONLY A FAILED FLIP
PREDICTION FOUND IT.** The adjudicated key reads the RAW declared type.
The AST's own form test (`_is_str_view_at_runtime`) reads the RESOLVED
type, so a name declared `PendingStrType` -- neither `String` nor `str`
-- was invisible to the mirror. The macro lane had predicted
`tplib/json_model_nested` would flip on the merge with cell G "with no
further work"; it did not. **A prediction that a case will flip on merge
is a testable claim: test it, and when it fails, diagnose rather than
assume the merge was incomplete.** Instrumenting the predicate took one
spy run and named the exact binding (`__elem_13`, declared
`PendingStrType(var_id=8)`, oracle render identical to cell G's). The fix
resolves the pending binding through `_resolve_pending_view` at the form
check. Not macro-specific: reproduced hand-written, an `@error_return`
callee's str result bound in a `while` body, which is the shape that
leaves a predeclared local pending.

Behind it sat a SECOND blocker the macro lane had reported as cleared.
Its report folded json rows 3/4/5 into one `field_recv_ok=True` flag
flip; rows 3 and 4 were, row 5 (the value-opt TUPLE fence) was merely
SHADOWED by the `Own[str]` reject and re-surfaced the moment the first
cell landed. **A reject that disappears when an earlier reject is fixed
was never cleared -- it was hidden.** Clearing it needed six load-bearing
legs (an is-None gate leg and its `value_repr` classifier twin -- ablate
the classifier alone and the result is a DIVERGENCE, a pointer compare
where the AST spells `has_value()`, not a reject; the whole-binding NAME
write, the narrowed deref read, the value-tuple pass-through arg row, and
a `_resolve_tuple_pending` call at the literal's result type).

**THE GATE SET HAS A STRUCTURAL BLIND SPOT: A THIR-ONLY CRASH.** The
completeness verification of the raw-vs-resolved mirror family found the
var-decl fallthrough spelling `stmt.resolved_type.to_cpp()` raw, where
the AST spells through `type_to_cpp` and resolves pending slots first.
`e = src(); tup = (e, 1)` off a `str`-returning call compiles on the AST
path and RAISES on the THIR one. **A crash is neither a fallback nor a
byte-diff, so the ratchet and the corpus byte-diff are both blind to it**
-- and the move-verdict and binding-fact joins never run on a body that
died at emit. Only an adversarial probe found it, and it was pre-existing
on master (verified against a `git archive` snapshot). Record this
alongside the standing note that a fallback emits byte-identical AST:
the gate set catches divergence and fallback, and nothing in it catches a
THIR-only crash on a shape no corpus case reaches.

That audit is also the clearest evidence to date that **a
positive-polarity gate over a raw type is safe and a negative-polarity
one is not**: across 1144 sampled cases exactly ONE family predicate
flipped on resolution (`is_array` at the `len()` walrus leg, which
rejects and falls back -- reject-valid, never a divergence), and every
raw `isinstance(..., NominalType)` site that flips is conjoined with a
record/protocol test that is False for the str/bytes/list families. One
site (`_user_iterator_iterable`) answers YES on the resolved type, and
there the raw read is PROTECTIVE: resolving it would let the universal
`::tpy::__iter__` route claim a str loop.

**"UNCONSTRUCTIBLE" MUST BE COMPILED, NOT ASSERTED.** The MIL 1-arg
container-ctor widening admits all four container names; the lane pinned
`list`/`set`/`reversed` and skipped `dict(pairs)` on the judgment that
sema rejects it. The review round compiled it: it routes, byte-
identically, and had been a silently-admitted arm with no test. The
sibling judgment held -- `Array(x)` really is sema-rejected in every
one-arg spelling -- so that one is recorded in the module docstring
rather than as an unfalsifiable pin. The asymmetry is the lesson: a
same-shaped "cannot be built" claim was right once and wrong once, and
the only thing that separated them was a compile.

The same round raised a `@native_c` boundary that asserted only `assert
fell` (satisfied by a fallback for any unrelated reason) to an exact
fallback dict plus a face-absence assertion, and pinned the
`is_dyn_protocol` exclusion in `_native_protocol_field_arg`, which had no
boundary pin in either of its two positions -- ablating it routes the
body and DIVERGES, so the fence is load-bearing and was untested.

**A VACUOUS PROBE READS EXACTLY LIKE A PASS.** The verifier's first
`argtemp.optptr_container_literal` probe reported clean. It was not: an
unrelated `Array[Int32, 2] | None` row in the same body rejected, fell
the WHOLE body back, and every other row in it re-emitted through the
AST. Re-run split (one shape per body, `fallback: {}` asserted) it
compared what it claimed to compare. **A probe must report what it
actually compared, not that it found no difference** -- and an exact
empty fallback dict is the cheapest way to say so.

That arm was in fact diverging, and the shape of the miss is instructive:
the committed pin used a DICT literal, and dict/set literals render
self-describing (`ordered_map<K,V>({{..}})`), so the pin could not see
the LIST-only divergence (`__tmp_1 = {1, 2, 3}` against the AST's
`std::vector<int32_t>{1, 2, 3}`). **A boundary pin on the wrong sibling
of a family is not weaker coverage, it is zero coverage.** The related
"BigInt elements may not even compile" worry did NOT hold on compiling it
(`BigInt(int32_t)` is not explicit) -- a spelling divergence, not latent
wrong code, and worth stating because the wrong severity guess would have
routed it to BUGS.md instead of a same-branch fix.

**A NON-BYTE-DIFF GATE EARNED ITS KEEP.** The macro lane recorded a
`field_recv_ok` docstring as stale after dualgen AND the corpus byte-diff
both came back clean. The BINDING-FACT JOIN then failed
(`storage_tuple_locals in __json_encode__: missing __kv_38`) -- exactly
what the docstring predicted. Root cause: the tuple-unpack for-head
branch never performed the storage-form loop-var registration the AST
does for EVERY for head; the single-var branch did. Only the docstring's
"name-receiver-keyed" wording was inaccurate; the fact was right. Two
gates agreeing is not three.

**SCOUT PREMISES FALSIFIED AGAIN -- INCLUDING TWO THIS LEDGER RECORDED.**
The scout-locates-the-reject / implementer-discovers-the-shape pattern
held for a second push, and this time two of the falsified premises were
written down here as follow-up guidance for exactly this work:

- **`pascal/m17_with_stmt` did NOT need the record-spelling slice
  widened.** The previous entry recorded `_f1_record` as False for
  `PStr[255]`. Measured at the raise, it is **True** (the raw-`int` type
  arg passes `_f1_record_type_arg_ok`); the whole blocker was that the
  result ladder admits `_f1_record` at RECEIVER / ITERABLE / BORROW_BIND
  / SUSPEND but not at VALUE, and the fix is one result row on the
  type-ctor arg loop, verbatim sibling of the free-call ladder's.
- **The `render_type` crash that blocked `list/list_repeat_lazy` no
  longer existed.** The recorded reason for parking the case ("routing
  the lazy decl exposes a latent crash; the fix belongs at the
  protocol-arg boundary") had already been closed by an earlier
  `resolve_pending_container`. Nothing verified it before the case was
  re-opened. **A recorded blocker is a measurement with a timestamp;
  re-probe it before letting it park a case for another wave.**
- **Macro-generated bodies pose NO structural problem for THIR.** The
  three "macro family" cases are three DISJOINT tracks, not one shared
  blocker -- an optional-slot reseat plus a pointer-local record copy; an
  `@inline` driver/expansion pair plus three marker-arg rows and a
  `@native` record ctor; and a dict-view for-head flag. The family name
  was a grouping convenience that read as a shared cause.
- **Three `json_model_nested` rows were ONE flag flip.** Per-field
  ablation isolated them correctly as symptoms and then presented them as
  three independent rows; `field_recv_ok=True` cleared all three.

Two more premises fell inside the lanes: the whole-optional/container
branch the scout wanted carried over into the type-ctor row is
UNCONSTRUCTIBLE (sema rejects `str(h.opt)`, `str(h.bs)`, `Span(h.xs)`
before lowering sees them) and would have been dead code; and the lazy
list-repeat print leg does NOT need `resolve_pending_container` -- the
declared binding is already resolved, and the `pendinglisttype` tag that
suggested otherwise came from `analyzer.get_expr_type`, which is
diagnostic-only. A textbook lossy tag.

**Two wrong KEYS caught by the blast sweep, not by the flip case.** The
list-repeat protocol-arg admission first landed in the position-shared
protocol gate, where the AST's METHOD arg loop passes a repeat INLINE
while the free-call loop hoists a temp; and the decl row first admitted
REBOUND lazy locals, which the AST binds through a pointer slot. Both
were miscompiles that the target case could never witness. The
`fstr_expansion` arm likewise needed the consumer's `use` threaded plus a
STATEMENT-side marker-chain peel -- the verbatim `macro_expansion` mirror
the scout proposed leaves a void `@inline` expansion rejecting at VALUE.

**SUPERSEDED 2026-08-23** (see the marker-floor entry at the end of this
file): five of these eight are cleared and three of the blocking reasons
below were wrong when written. Kept as the dated record of what was
believed at the time.

**THE REMAINING 8 MARKERS ARE THE BLOCKED SET, NOT THE UNGROUND SET.**
Six corpus, two interop:

- **AST-bug-blocked oracles** (the fix belongs AST-side first, per the
  migration contract): `none_safety/narrowed_local_optional_reads` and
  `none_safety/optional_container_literal`, both on the raw `(*lst)[i]`
  subscript defect -- THIR renders the CORRECT checked read and diverges
  against a wrong oracle. `tuple/element_vs_singleton_global` sits on the
  tuple-of-reference-type global borrow slot, whose snapshot pins
  wrong-but-shipping output (a silent copy where CPython aliases).
- **By-design residue**: `native/extern_c_str_param` stays marked on
  `sig.linkage_c_abi`; the C-ABI str story is decided at cutover, and
  BUGS.md carries the wrong-code entry that decision must resolve.
- **One design fork**: `tuple/mixed_own_tuple_local_decl_paths`. The
  rebind and if-hoist sinks route; `loop_carried` needs the if-hoist's
  `in_branch` scope fence lifted, which requires THIR's hoist
  registration to be FUNCTION-scoped rather than registering into the
  enclosing scope's `declared` copy. The try-hoist and walrus sinks stay
  deliberately unbuilt: the case cannot flip without the fork, and an
  admitted-but-caseless arm carries its own fixtures for no dial movement.
- **Deliberately unscheduled**: `records/dataclass_asdict_optional`, one
  giant macro-expanded body whose first raise hides at least three more.
  Two of them are storage-vs-borrow FORM questions, not arm widenings --
  THIR classifies `dict[str, Int32] | None` as pointer-repr and
  normalizes the ternary arms to `T*`, while the AST at that container-
  ELEMENT position emits the value/storage spelling. Re-price it against
  the form work, not against the arm ladder.
- **Two interop markers**, `class_properties` and
  `container_exposed_elements`, untouched by this push as by the last.

So the next dial movement is not a wave. It is: fix the two AST subscript
defects and the tuple-global borrow slot; decide the C-ABI str story;
decide function-scoped hoist registration; and re-price the asdict body
against the storage-vs-borrow work.

**Five BUGS entries filed, each reproduced before filing** -- four
approved in the review round, one (the str self-append divergence) raised
by the applier and carried on the same terms. Two of the crashes hit BOTH
paths: an unannotated tuple local carrying a pending-str element inside a
GENERATOR body fails the resumable-frame pending assertion (sema-level,
identical on both paths), and the same tuple one nesting level out
(`ts = [(e, 1)]`, `o = (e, 1) if c else None`) raises
`PendingStrType should be resolved before codegen` on the AST path too --
only the DECL's own type is spelled through the resolving helper, so a
composite one level down reaches a bare `to_cpp()`. The third crash is
THIR-only and latent (a nested tuple LITERAL spelling its own result type
bare). The remaining two are a loud reject-valid (`",".join([e])` with a
pending-str element fails `Iterable[str]` conformance) and a byte-diff
divergence where **the AST is the accident and the fix is still
AST-side**: `x = x + y` on an `@error_return`-unwrap-seeded str local
folds to `+=` on THIR where the AST emits `str_concat`, because the AST's
reassign arm reads a `ctx.var_types` entry that was never populated. Per
the contract that is its own change-set, not a THIR patch.

**Mirror pairs to consolidate at cutover, updated.** The two carried from
the previous entry stand (`_renders_own_borrow_tuple`, the `_value_opt_*`
/ `_optional_print_formatter` set). Add `_owned_form_str_name` <-> the
AST's `_is_str_view_at_runtime`: THIR now deliberately re-derives the
AST's resolved-type form test, and this branch proved the two halves
drift silently when only one resolves.


### The AST-first subscript fix and the last two blocked markers (2026-08-23)

Branch `ast-fix-0823`, base `486521878`. **Markers 8 -> 6, dial 3733/3739
-> 3738/3742.** Suite green with full exec: 12546 passed, 3741 built+run,
0 byte-diff divergences, 0 move-verdict divergences over 987 joined
nodes, 0 binding-fact gaps over 11846 joined bodies. Three scouts, four implementer lanes (three in isolated
worktrees), seven review specialists plus a meta-review.

**This entry supersedes the next-actions list of the 2026-08-22/23 push.**
That list named five items. Two are done, and two of its premises were
false. Corrected record:

- `none_safety/narrowed_local_optional_reads` -- FLIPPED. The AST defect
  was real and is fixed.
- `tuple/element_vs_singleton_global` -- FLIPPED, and **it was never
  AST-bug-blocked.** The ledger recorded it as sitting on the
  tuple-of-reference-type global borrow slot. Deletion-bisection showed
  that shape already lowered; the real blockers were two ordinary
  lowering arms (a hoisted pointer-slot global, and an F3 tuple global
  from a mixed-own call). The borrow-slot bug remains open and HIGH, but
  it is not on the marker's critical path.
- `none_safety/optional_container_literal` -- STILL MARKED, and its
  recorded blocker is now wrong too. The subscript defect it was filed
  under is fixed and its snapshot moved, but it did not flip: two
  unrelated arms remain, `decl.opt_slot_source` and
  `assign.field_write_shape`.
- The C-ABI str story and the function-scoped hoist registration fork are
  untouched and still open, as is re-pricing
  `records/dataclass_asdict_optional` against the storage-vs-borrow form
  work -- the fifth item of that list, dropped from an earlier draft of
  this entry.

**The lesson: a blocked-marker attribution is a HYPOTHESIS, and this
ledger was the thing asserting it.** Three of the six recorded blocking
reasons were wrong -- not stale, wrong at the time of writing. Each was
established by reading a reject tag rather than by bisecting the case.
The tags name the FIRST raise, not the blocker, and a case's marker
survives for whatever reason is cheapest to believe. Bisect before
recording a blocking reason, and write down the instrument used.

**The AST defect.** A narrowed pointer-repr `Optional[container]`
subscript READ rendered a raw `(*lst)[i]`: the `__getitem__` fi lookup
ran on the un-unwrapped `Optional`, missed, and fell through to
`operator[]`. Silent CPython wrong-answer divergence -- `lst[-1]`
returned garbage, a missing dict key printed a default AND silently
inserted it, and a `readonly[dict] | None` receiver emitted C++ that did
not compile at all. Filed TWICE (both entries now removed). The filed
scope was also incomplete: `_gen_aug_assign_subscript_code` had no
`obj_type` unwrap at all, so `x[i] += v` emitted a raw read nested inside
a checked write. Two edits, and the churn was measured -- codegen run
twice in-process over every non-error case -- at exactly 3 hunks in 2
files, with the stock arm reproducing committed snapshots byte-for-byte.
A THIRD pre-existing expected file moved --
`element_vs_singleton_global/expected/diag.txt` -- from that case's
comment edit shifting a warning's line number, not from the fix.

**Three THIR-side corrections the lanes made to their own briefs.** The
THIR fix site named in TODO.md was unsound to widen: the `(*recv)` deref
render keys on `lc.pointers`, which a narrowed LOCAL is never in -- it is
seeded from params, pointer-slot globals, imported slots and match
captures, but not from ordinary locals -- so the local would have been
handed the inner container with no deref. The real site
was the already-landed setitem sibling, extracted so both sides share it.
A minimality claim ("this arm is unreachable") was false for one of two
arms. And a scout's blocking statement was off by one -- the rejecting
statement was the one BEFORE the one named, poisoned by sema hoisting.

**Instrument note.** Four blockers in the new regression case were each
NOT what their tag implied: a plain un-narrowed probe showed two of the
four shapes already routed, making them whole-family gaps (bytearray had
no subscript READ row at all) rather than Optional-receiver gaps.

**Marker arithmetic is not flip arithmetic.** The first subscript lane
cleared one marker and shipped a new regression case carrying its own
`no_thir.txt` -- net zero, plus an inflated dial denominator. A
newly-authored case that cannot route is a marker like any other. The
follow-up cleared all four of its blockers rather than splitting the
case.

**Three BUGS entries filed, three removed.** Removed: the identity
`tuple_to_storage` divergence, closed by `99484958f` and confirmed by
bisection plus an independent dualgen -- now pinned by a corpus case and
a routing-asserting unit, since it had been admitted-but-unwitnessed.
Filed: a pointer-repr tuple local whose literal RHS value-captures an
owned element emits ill-formed C++ at decl and reassign (the trigger is
the element's CAPTURE, not its freshness; the repair is a design fork);
a 3-deep nested container literal at a pointer-repr Optional arg slot
gaining a brace level under THIR (the body ROUTES, so it is a live
contract violation invisible only for want of a case); and the
share-storage warning asserting "rebound on each iteration" at module
scope, where there is no iteration. Two claims inside the tuple-global
entry were corrected in place: its "dead temporary" reason for
mixed-stays-storage (the owned element is by value in a static-lifetime
object, so nothing dangles), and its predicted post-fix output -- though
the SECOND correction was itself over-stated and has been re-hedged. The
entry's original `42 43 44` is what CPython prints and what full parity
requires; `42 43 2` follows only if the mixed-stays-storage carve-out
survives the re-justification this same entry now demands. The endpoint
is conditional on that decision, and calling the original figure false
was wrong.

**This entry was itself fact-checked before the branch was handed over,**
on the principle that produced it: a record nobody verified is exactly
what cost the previous push. The check found six defects in this text --
a wrong technical claim about `lc.pointers` (repeated into TODO.md), the
over-stated endpoint above, a miscount of filed BUGS entries, a dropped
item, and two omissions. All are fixed above. Write the record, then
falsify it.

**Corpus coverage added.** `tests/cases/enum/enum_container_positions`
pins an enum at all three newly-routed container positions (dict key, set
element, value-tuple element). That combination had NO corpus case on
either path before -- the AST always supported it and nothing tested it.
Routes clean, CPython parity, no markers.

**This entry was fact-checked before the branch was handed over**, and
did not survive its own thesis: six defects, including the headline claim
above, which originally read "every recorded blocking reason was wrong,
six for six". Two of the six had no record to be wrong about, and the one
record that WAS exact had been miscounted as wrong. A BUGS count, the
tooling diagnosis, two extern-C overreaches and a size estimate were also
off. Write the record, then falsify it -- and be most suspicious of the
claim that flatters the session.

**Mirror pairs to consolidate at cutover, updated.** Add
`_narrowed_ptr_opt_recv` / `_narrowed_ptr_opt_name` <-> the AST's
narrowed-Optional unwrap in `_gen_subscript` and
`_gen_aug_assign_subscript_code`. The AST keeps that unwrap as two
copies with different repr gates scattered across four sites; THIR has
one extracted pair. A future unification belongs on the AST side and is
a behavior-risk refactor, not a cleanup.


### The marker floor: six recorded blockers, six wrong (2026-08-23)

Branch `markers-zero-0823`, base `bc0729398`. **Markers 6 -> 1, dial
3738/3742 -> 3741/3742.** Suite green with full exec: 12581 passed, 3741
built+run, 0 byte-diff divergences, 0 move-verdict divergences over 987
joined nodes, 0 binding-fact gaps over 11855 bodies. Five read-only
scouts, four implementer lanes in isolated worktrees, five review
specialists. NO generated C++ snapshot changed anywhere -- every cleared
case was already emitting byte-identical AST output through fallback.

**The record was unreliable, but score it honestly: of the six markers,
THREE had a recorded reason that was wrong when written, ONE was stale,
and TWO had no recorded reason at all -- only a grouping line.** No
`no_thir.txt` records a reason; five of the six carry nothing but
`--thir-classify` boilerplate. The reasons live in this ledger and in
TODO.md, which is exactly why they went unchecked for so long:

- `interop/class_properties` -- recorded as blocked, carried untouched
  across three pushes. It was STALE: zero fallback, zero ratchet total,
  clean byte-diff including the `_ext.cpp` glue. Deleting the file was
  the entire fix.
- `tuple/mixed_own_tuple_local_decl_paths` -- recorded as the one
  remaining DESIGN FORK, needing function-scoped hoist registration. The
  rationale was MISATTRIBUTED: it quoted the comment on
  `if.hoist_inner_scope`, which guards `is_nonvalue_flavor` (dyn-protocol
  / plain-nonvalue / ptr-repr Optional). A ptr-repr `TupleType` is none of
  those, so the case never reached that fence -- it rejected exactly 38
  lines lower at a separate `in_branch` disjunct. No fork; three
  INDEPENDENT sinks, ~30 lines of new logic plus a shared-helper
  extraction (+116/-51 across four files).
- `interop/container_exposed_elements` -- recorded as interop work. It is
  not: a plain non-`@export` scratch with a module-local `IntEnum`
  rejects at the identical sites. Four ordinary clauses in
  `predicates.py`, nothing in `tpyc/interop/` participating.
- `records/dataclass_asdict_optional` -- recorded as unschedulable, "at
  least three hidden blockers, two of them storage-vs-borrow FORM
  questions, re-price against the form work". Measured: 22 bodies, ONE
  falls back; five independent gates; ONE form question, and it is
  THREADING an already-decided AST fact, not a new concept. One wave.
- `none_safety/optional_container_literal` -- **the one record that was
  EXACT.** TODO.md named `decl.opt_slot_source` and
  `assign.field_write_shape`; two render rows fixed exactly those two.
  (A scout reported "5 sites" by counting rejecting source lines rather
  than rows -- a reminder that a re-measurement can also be wrong.)
- `native/extern_c_str_param` -- the one recorded reason that HELD. It is
  genuine by-design residue, and the correct action is to NOT clear it
  (below).

**The instrument, stated plainly: reject tags name the FIRST raise, not
the blocker population.** Every one of the above was established by
reading a tag instead of bisecting. The technique that works is to stub
the rejecting node and re-probe until the population stops growing -- in
a SCRATCH copy or an in-process monkeypatch, never in the shared
checkout. One scout stubbed tracked files directly and contaminated four
concurrent agents' measurements; the tree was reverted, re-probed to
exact baseline, and every affected finding re-verified unchanged, but
that was luck. The brief now says where to stub.

**A tooling gap hid two markers for three pushes.** Every probe script
ENUMERATES `tests/cases` only (`discover_cases()`, or a hard-coded
`tests/cases/<case>` in `probe_sites.py` and `sites_multi.py`), so no
interop case was ever reached. The gap is ENUMERATION, not resolution:
handed the directory, `_case_entry.entry_src` resolves an interop case
correctly. The scripts did not error -- they simply never looked, which
reads as "nothing to see". Two further traps:
`probe_file.py` omits `no_main`, so its emit is not the one the harness
compares; and it patches `note_detail` on only `expressions`/`statements`,
so gates raising from `checks.py` report `[None]` -- the decisive
`method.set.add` detail was invisible until that was fixed. Closed by
`.claude/skills/tpy-thir-wave/scripts/probe_interop.py`, verified against
the real harness across all 34 interop cases before landing.

**MARKER ARITHMETIC IS NOT FLIP ARITHMETIC.** A lane on the previous push
cleared one marker and shipped a new regression case carrying its own
`no_thir.txt` -- net zero, plus an inflated dial denominator. Check new
cases for markers before claiming a delta.

**What the blast sweep caught that no flip proof could.** The asdict
lane's sweep found a still-open pre-existing THIR divergence -- a
str-view element in a printed value-tuple takes a spurious owned copy,
on a shape that ROUTES today -- and then established that its own new
rows would have imported that defect into two further positions. Both
were proven divergent before it narrowed the rows to exclude non-field
str/bytes elements. Without the sweep this branch would have tripled a
defect's footprint with every gate green.

**An architectural ceiling, correctly refused.** Threading the
"immediate element of an Optional container" fact wanted a 19th
`_ExprUse` boolean; `test_thir_core.py` fails that BY DESIGN and directs
the author to fold into a sink-kind enum. The lane declined to make that
call and used the documented per-call `_lower_expr` keyword instead (the
`cond_eager` precedent), plus one `_LowerCtx` bool used only to reject
deeper positions. Review confirmed no enum refactor is owed: this is a
single-position fact, not a recurring sink-kind axis.

**`native/extern_c_str_param` stays marked, deliberately.** Clearing it
means mirroring a render proven wrong AT RUNTIME: a linked driver returns
`f_eq("hello") == 0` because the C ABI respells str params as
`const char*` while the body renders ABI-blind, so `s == "hello"`
compares addresses. The silent set is wider than BUGS.md recorded --
param-to-param compare, `in` tuples, and `match` on a C-ABI str param is
entirely DEAD (`!=` and orderings were already recorded). One loud bug is
unfiled -- TPy calling its own export with a `str`; the `__param_s`
reassign is a fourth face of the family already filed at BUGS.md
349/350/355. The fix is AST-side (~10 lines: rename the param in the
`extern "C"` definition, materialize a `string_view` at entry), after
which the marker clears as a consequence -- or, under the better shape
(an ordinary namespaced function plus an `extern "C"` shim), the fence
simply deletes with ZERO THIR work. **The "settled at cutover" label is
doing no work**: the `const char*` respell is a signature decision, and
per Gate D3 the signature printer stays in `codegen_cpp` permanently, so
cutover does not MOVE it. What cutover does force is the body-side fence:
Gate D3 item 7 makes `ThirUnsupported` an ICE, so the reject at
`functions.py:581` must be gone by then. The label is still doing the
wrong work -- it parks a live miscompile behind a milestone that will
demand the fence's removal without fixing the render underneath.

**Four BUGS entries filed** (plus one TODO entry), each reproduced
independently before filing -- two loud C++ build failures the front-end
says nothing about (the
mixed own+borrow walrus double-decl; the Optional container-element
ternary arm with no lift, whose union twin lifts via `to_value_variant`),
and two byte-diff divergences, one of them LIVE (the print-tuple copy
above). The
ternary entry has a second facet: the AST's element-position flag is
sticky over the subtree, so a ternary at a nested BORROW position takes
the wrap too. THIR declines to mirror either facet.

**Corpus coverage added.** `tests/cases/enum/enum_container_positions`
pins an enum at all three newly-routed container positions (dict key, set
element, value-tuple element). That combination had NO corpus case on
either path before -- the AST always supported it and nothing tested it.
Routes clean, CPython parity, no markers.

**This entry was fact-checked before the branch was handed over**, and
did not survive its own thesis: six defects, including the headline claim
above, which originally read "every recorded blocking reason was wrong,
six for six". Two of the six had no record to be wrong about, and the one
record that WAS exact had been miscounted as wrong. A BUGS count, the
tooling diagnosis, two extern-C overreaches and a size estimate were also
off. Write the record, then falsify it -- and be most suspicious of the
claim that flatters the session.

**Mirror pairs to consolidate at cutover, updated.** Add
`_borrow_tuple_hoist_entry` <-> the AST's if/try hoist predecl logic, and
`_optional_container_storage_inner` <-> `_gen_array_literal`'s own
Optional unwrap. Both are hand-maintained lockstep mirrors today.

## 2026-08-23 -- the C-ABI residue, closed from the AST side (markers 1 -> 0)

The last `no_thir.txt` in the corpus was `native/extern_c_str_param`,
held by the `sig.linkage_c_abi` fence: a `binding="C"` signature respells
str-family params to `const char*` (`gen_c_params`) while the body
renders against `std::string_view`, so THIR declined to mirror a seam
whose AST side was wrong. `s == "hello"` compiled to a raw pointer
comparison that is always false.

**The fence was not the thing to fix, and neither was the render.** Sema
never checked that a C-linkage signature holds types a C caller can
spell, and `str` was only the loudest face -- `int` emits
`const ::tpy::BigInt&`, `list[T]` emits `std::vector<T>&`, a
`@native(binding="C")` struct by value emits `S&`, and every one of those
compiled silently inside `extern "C"`. A representability gate
(`is_c_abi_allowed` in typesys, hooked at `register_function` and at
`native_global`) makes the whole class a compile error, after which the
fence is unreachable and deletes.

**Marker arithmetic, honestly.** This closed the last marker without
migrating a body: the case that carried it no longer exists (it is an
`error_` case now), and the two new positive cases route with no
fallback. Zero snapshot churn on any existing case.

**A design claim that was wrong until compiled.** The design report put
the blast radius at one case. It was five -- `-> None` resolves to
`VoidType`, not `NoneType`, so every `@export(binding="C") ... -> None`
case failed the new gate until the return check accepted both. Reasoning
about a type's identity is not the same as compiling it.

**Deliberately not gated:** `@native(binding="C")` class fields and stub
methods. tpyc emits nothing for them, so they stay under the documented
"the author asserts the C side" `@native` contract. The rule is exactly
"police what tpyc itself writes into an `extern "C"` declaration".

**Deferred, filed in TODO.md:** the marshaling wrappers (the only shape
that can express arity-changing marshaling and returns) and
`@export(binding="C")` classes.

**A claim in this branch's own commit log is wrong.** The review-round
commit says converting `unsafe_cstr` from `@cpp_template` to `@native`
made "both faces go away". Only one did. The ill-formed-literal face
(`"lit".c_str()`) is closed; the coercion-temporary face is
RE-EXPRESSED, not fixed -- `unsafe_cstr(v)` for a `str`-typed `v` still
materializes a `std::string` bound to the helper's `const std::string&`,
and the pointer dies with the full expression. It is now valid C++ that
dangles instead of invalid C++ that did not build, which for the
literal shape trades a loud failure for a silent one.

That trade was taken deliberately: the INTENDED spelling
(`c_fn(unsafe_cstr(x))`, consumed in place) went from "does not compile"
to correct, and the regressed cell is a misuse of an `unsafe_` primitive
whose sibling already behaves that way on master -- `unsafe_ptr(s + "x")`
dangles silently there today. The family-wide property is filed in
BUGS.md rather than papered over at this one call site. Two reviewers
called it Critical and one declined; the deciding evidence was the
pre-existing sibling, which none of the three had.

**Second review round, and why it was needed.** The first round could not
review its own fix commit, and it never dispatched a runtime-cpp
specialist because `runtime/` was empty when it ran -- the fix then ADDED
a hand-written C++ helper. A review round's output needs its own round;
the bucket that was empty at classification time is exactly the one that
grows.

## 2026-08-25 -- the stdlib oracle: rotted, fixed, and gated

Branch `thir-stdlib-routing`. Closes the D4 stdlib-oracle hole and puts a
permanent gate under it. Prerequisite for cutover item 2 (A5), which could
not honestly start while the oracle it grinds against was red.

### What was broken

`--thir-stdlib` reported **49 failing cases**. All 49 were witnesses of ONE
hunk: `lib/tpy/_datetime_parse.py:447` in `_strptime_impl`, where THIR
dropped `.to_fixed_check<int32_t>()` on a subscript index. Cross-checked
arithmetically -- 54 cases import `datetime`/`zoneinfo`/`requests`, minus 3
`error_*` (never reach codegen) and 2 comment-only hits = exactly 49.

**The harness reports only the FIRST diverging module per case**, so "every
failure names `_datetime_parse`" was not evidence it was the only one. A
separate sweep of all 88 non-macro `lib/tpy` modules, both paths, full
byte-diff, was needed to establish that it genuinely was.

### Root cause: two type oracles

Sema caches a type per name OCCURRENCE. Codegen declares the C++ slot from
the variable's FINAL retro-widened type in `ctx.var_types`. Every AST
checked-narrow arm keys on the declared type (`TypeResolver.get_resolved_type`,
whose `is_runtime_bigint` IGNORES the `expr_type` it is handed and re-reads
`get_resolved_type`); every THIR narrow arm keyed on
`analyzer.get_expr_type(node)`. A literal-seeded local later widened to `int`
therefore read a stale `Int32` in THIR and lost the narrow.

Fixed by generalizing the helper that already threaded the declared map for
the mixed-sign compare gate (`_operand_type` -> `_declared_type`, plus
`_narrow_key_type`/`_has_widened_int_name`) and re-keying every checked-narrow
sink on it: subscript read/`__setitem__`/`__delitem__`, slice bound, enum
`from_value` arg, `FixedInt += BigInt` (name and element), `__contains__`
needle, resolved-binop param slot, f-string arg.

### Three things the diagnosis got wrong, each caught by a different gate

1. **"The cast is lost in the boolean-context operand path."** False. Line
   442 in the same function is also a boolean-context operand, inside an
   `and`-chain, and KEEPS the cast. Instrumentation at both sites showed the
   difference is the type oracle per occurrence, not the syntactic position.
   The structural hypothesis came from reading the surface shape; two
   independent measurements refuted it.
2. **"Row 8 (call-arg narrow) is a same-gap-by-reading, unwitnessed."** It
   was witnessed -- `b + p` against a user dunder's `Int32` param diverged
   live. Found only by the blast sweep, not by the analysis.
3. **A row that was not in the survey at all** (resolved-binop param slot)
   needed its own arm and face.

### The parent defect, found by the sibling survey

The narrow family was a SYMPTOM. `p = 0; q = p + 1; p = gi()` emits
`int32_t q = ::tpy::add_check<int32_t>(p, 1);` against a `::tpy::BigInt p`
slot -- a hard g++ error on valid TPy. The retro-widen writer
(`sema/local_deduction.py`) updates `ctx.var_types`/scope/namespace without
invalidating the `expr_types` already recorded. The checked-narrow arms only
dodged it by asking the other oracle. **BOTH codegen paths share the defect,
so no byte-diff can ever see it** -- only a C++ build does. Filed HIGH in
BUGS.md with two consumers (`[x] * n` repeat count, builtin overload
selection on `chr`/`abs`).

Consequence for this branch: composite-over-widened-local sinks REJECT
(`_NARROW_UNMIRRORED`) rather than mirror, because the AST's render for them
is ill-formed. Those reject legs become removable once the parent is fixed.
This is the "do not mirror an AST accident" rule applied deliberately, and it
means this change ADDS fallback legs while removing divergence.

### The review found the same bug class surviving in two gates

`_record_getitem_idx_recv_ok` (`predicates.py`) and `_user_record_setitem_ok`
(`checks.py`) kept a LEADING short-circuit on the stale per-occurrence type,
ahead of the correctly-keyed disposition call -- so a composite index over a
widened local against a user-record `__getitem__`/`__setitem__` was ADMITTED
where it must reject, dropping the narrow with the body ROUTED (`fallback:
{}`). `_narrow_bigint_index`'s own docstring asserts "'reject' never reaches
lowering (the gates exclude it)"; that invariant was false at exactly these
two gates. **Pre-existing, not introduced here** -- before this branch the
same shape diverged at the arm instead of the gate.

Worth recording HOW it was found: the `safety-model` specialist's dispatch
table was EMPTY for this diff (no `sema/`, `typesys.py`, `codegen_cpp/`,
`runtime/` touched) and it was dispatched anyway, on the grounds that the
change is about whether an overflow guard is emitted. It returned the only
Critical in the round. The dispatch table is a floor, not a ceiling.

### Measurement corrections

- **Stdlib fallback re-measured at `189 fallback / 810 routed / 999
  candidates / 31 modules with fallback`** by
  `scripts/thir_migration/thir_stdlib_fallback.py` at master `4ad40383c`,
  against the stale 289 from 2026-07-28. Checklist item 2 updated to point at
  the instrument.

  **A 213-body figure appeared in the first draft of this entry and was
  WRONG** -- it came from a scratch instrument whose dedup key and candidate
  population both differ from the committed script's (`942 routed` and
  `32 of 88 modules` likewise do not reproduce; the committed script yields a
  999-candidate population on either tree). It is recorded here rather than
  quietly deleted because the error is the one this very entry warns about
  two paragraphs up: **two numbers from different counting keys are not a
  delta.** Quote the instrument and the tree with every figure.
- **The FLOOR caveat on the fallback count is retired.** Two independent
  corpus sweeps found zero fallback bodies the import-only sweep had not
  already seen -- one over 495 cases (the stdlib-oracle scout) and one over
  90 entry programs chosen to cover every module that has a fallback (the
  triage scout); they differ in corpus selection, not in result. The reason
  is mechanical: `iter_module_callables` attempts each callable once per
  module, so instantiation count cannot change the body POPULATION. The
  caveat still holds for DIVERGENCE, which is emission-driven.
- **The pin ablation was under-reported.** The implementer's note said
  "reverting the key made 3 of 12 fail"; re-running it against the full
  shared helper gives **12 of 18**. The 3/12 figure came from ablating only
  the earlier subscript-only sub-commit. Recorded here because the commit
  messages cannot be corrected.

### The gate (the actual deliverable)

Divergences had been ground 436 -> 0 on 2026-07-29 and left to an opt-in
flag. `99484958f` then admitted a body carrying a mirror that had always
been wrong, and nothing noticed for three days. Verified mechanically: the
flag defaults False, `pyproject.toml` addopts is only `-n auto`, there is no
`.github/`, and none of the 13 `ci/nightly/configs.json` rows passed it.

Two gates now:
- `tests/test_thir_stdlib_gate.py` -- runs in every plain `uv run pytest`.
  One entry importing every non-macro `lib/tpy` module, emitted both ways and
  byte-diffed. ~6s, no toolchain. One mega-entry rather than 88 separate ones
  is both 12x faster AND strictly wider, since each module is emitted with
  the union of instantiations its siblings request. Self-checking: floors on
  modules/compares/emitting-modules/bytes and a macro-detector sanity bound,
  ablation-proven to fail when the sweep stops covering (a past harness in
  this repo printed IDENTICAL having compared ZERO files).
- A `thir-stdlib` nightly row -- the wide corpus form, reaching the generic
  monomorphizations and resumable frames the import-only floor cannot.

**The general lesson, which is not about this bug: a metric ground to zero
and then left to an opt-in flag is a metric that will silently rot.** The
gate is the deliverable; the divergence fix is the occasion for it.

## 2026-08-25 -- stdlib routing cells 1-2 (fallback 189 -> 171, THIS branch's delta is 18)

Two cells against the stdlib fallback backlog, the first work on cutover
item 2 since the oracle was gated. Branch `thir-stdlib-routing`.

**The delta is 18 bodies, measured on both trees with the committed
instrument:** master `4ad40383c` = 189 fallback / 810 routed; branch = 171 /
828. Three modules move: `tpy.atomic` 14 -> 2, `datetime` 13 -> 8, and
`tplib.array_list` 13 -> 12 (a free rider from the open-tparam row, claimed
by neither cell).

**A "289 -> 171" framing was used repeatedly while this branch was in
flight and is WRONG by roughly 6x.** 289 is the 2026-07-28 baseline; the
drop from 289 to 189 is a month of other work already on master. Attributing
it here would credit this branch with 118 bodies it did not move. Recorded
because a readiness second opinion caught it only by re-running the
instrument against BOTH trees -- two full review rounds did not, since no
reviewer was asked to check the baseline. **A delta claim needs both
endpoints measured on the same instrument; a remembered baseline is not an
endpoint.**

**Stdlib cells are not corpus cells.** There is no `no_thir.txt` and no
ratchet for stdlib, so the dial does not move and the metric is the fallback
count. Stdlib only routes under `thir_all_modules`. The standing hazard: the
arm you widen is SHARED with user modules, where the ratchet and byte-diff
ARE live -- a stdlib win that de-migrates a user case is a net loss, so every
cell verifies both sides.

### Cell 1 -- `tpy.atomic` (14 -> 2 bodies)

All 7 method-call bodies (`store`, `exchange`, `fetch_add/sub/and/or/xor`)
rejected at ONE shape: arg 0, a param still typed as the OPEN `T` at a bare
`T` slot in `_record_method_arg_ok`. The neighbouring row admits a RESOLVED
scalar into a `T` slot; the still-open sibling was missing. New row
`_open_tparam_pass_arg` + face `method.tparam_open_pass_arg`, params-only,
same-`T`-only, TYPE-kind only.

**The `MemoryOrder` default-arg was NOT the blocker** -- the obvious guess
from reading the API. `load()` (order-only) already routed. Decoded by a
stdlib-scoped `probe_loc` sibling; the committed `probe_loc.py` is case-only
and never lifts `thir_all_modules`, so the shared-raise tag stayed lossy
until the instrument was extended.

Second row: `_forced_const_dropped`. THIR's inplace-dunder forced-const
mirror in `_param_is_const` was FLAT where sema's `decide_param_const` has
directly-mutated and addr-escaping short-circuits -- which is precisely why
the `sig.inplace_dunder_mutated_param` reject existed. Mirroring the
short-circuits let the reject be deleted.

A third leg (reassigned copy-for-reassign) was written and then DELETED as
dead: no discriminating case is constructible, since the
`param_needs_copy_for_reassign` types are value types whose
`param_cpp_formatter` renders identically regardless of the const decision,
and `bytearray` rebinding is already sema-rejected. Independently
re-verified in review rather than taken on the author's word -- a leg
dropped on a wrong "unconstructible" argument is how a real safety property
gets deleted.

### Cell 2 -- `datetime` (13 -> 8 bodies, site EMPTIED)

`body:stmt.return:return.record_source.methodcall.borrow` is now 0. One
render row for the return itself -- but the site could not reach zero on
that alone: two adjacent PRE-EXISTING defects sat in `ZoneInfo.fromutc`'s
tail and became visible only once the body started routing.

1. `_post_if_narrow_fact` was return-terminated only, where `_gen_if` and
   THIR's own `_poly_post_if_fact` twin both accept `(TpyReturn, TpyRaise)`
   -- an arm out of sync with its own sibling. A guard-and-raise narrow lost
   its persistent extraction alias.
2. `f(self)` at a VALUE-union arg slot is THIR wrong code: bare `this` into
   a by-value `std::variant` where the AST hoists from the deref'd receiver.
   FENCED, not fixed -- the retag site sat in the concurrent lane's file.
   Filed in BUGS.md with the retag site named so the fence dies with it.

The blast sweep CHANGED THE GATE KEY: a `ret_record_borrow` can be a
`RecursiveAliasInstanceType` whose `is_value_type()` reads through its
substituted body, so the arm keys on `isinstance(..., NominalType) and
is_value_type()`, not on value-ness alone. Found by a neighbour probe, not
by the witness.

### Orchestration lessons

- **Partitioning concurrent lanes BY FILE was wrong.** Both lanes needed
  `predicates.py` and `faces.py`, assigned to neither. One lane had to stage
  hunk-by-hunk to avoid sweeping the other's in-flight work into its commit.
  Partition by SEAM, and expect the shared predicate file to be contended.
- **A shared-tree gate run cannot attribute its own failure.** One lane hit
  a stdlib-gate failure, ablated its own rows, saw it persist, and correctly
  concluded it belonged to the other lane.
- **Site counts taken per-body-instance over-count.** Both cells' briefs
  overstated their body counts (10 vs 7, 14 vs 12) because the ranking was
  built on per-instance rows while the instrument dedupes by
  `(module, name)`. Rank on the instrument's key.
- A converted pin's comment was WRONG AT TIME OF WRITING (it asserted
  raise-arm ifs produce no post-if fact on either path; the AST always
  accepted raise). Second time on this branch that a fence's stated reason
  did not survive checking -- read the fence, do not trust its comment.

### Follow-ups opened

`decide_param_const` is the canonical shared function and THIR re-derives
its branches by hand instead of calling it; two independent drifts are now
known in that one mirror (the forced-const flatness fixed here, and
`_param_is_const`'s missing `is_ref_param()` filter, filed LOW). Folding
the THIR verdict through `decide_param_const` is the unification.

Next cell identified but NOT improvised: the free-call/ctor flavor at a
value-record slot still cannot hoist arg temps -- it rides
`_record_rvalue_source_shape` into the temp-free borrow-block passthrough
and rejects at `expr.call`. Blast radius is much larger than cell 2's, since
every value-record ctor return currently routes through the borrow block.

## 2026-08-25 -- stdlib checks moved pre-merge, and the metric was wrong

User call, and the reasoning is worth keeping: **nightly is not regression
prevention.** The divergence this branch opened with landed on master and sat
three days; a nightly row would have caught it a day after it landed, with
other work already stacked on top. For a migration in its endgame that is
archaeology, not protection.

### What moved

- **The wide stdlib oracle is DEFAULT-ON.** Every case routes lib/tpy through
  THIR and byte-diffs it against the same run's AST output. `--no-thir-stdlib`
  opts out for fast local iteration; `--thir-stdlib` stays accepted as a
  no-op/force so scripts and the nightly row are unaffected.
- **The fallback ratchet is pre-merge**, folded into
  `tests/test_thir_stdlib_gate.py`'s existing mega-entry compile. Both
  properties now come from ONE compile: **5.61s -> 5.57s**, against ~64s for
  the standalone sweep it replaces. Free.
- Snapshot regeneration auto-offs the oracle silently, but an EXPLICIT
  `--thir-stdlib` with `--update-snapshots` still errors -- "on by default"
  and "on because you typed it" are deliberately distinguishable.

### The cost was measured, not remembered

`+14.0s of 273.3s = +5.1%` comp-only, both runs on this tree, 12665 passed
each. **The 10-15% recorded in conftest and CLAUDE.md since 2026-07-30 was
stale by 2-3x.** A dedup design was drafted to avoid the cost and then
DISCARDED once measured -- building a content-addressed cache to save 14s is
exactly the premature front-end optimization CLAUDE.md forbids. Measure
first; the design you do not build is the cheapest one.

### The metric itself was wrong -- 171 was never the cutover number

Folding the ratchet in surfaced it: `thir_stdlib_fallback.py`'s
`merge_module` keys on the **bare body name**, so overloads and a method name
shared across several records in one module collapse to their worst sighting.
Per BODY the tree reads **195 fallback / 1050 routed / 2843 classified**;
the collapsed view reads 171 / 828 / 999. Applying the collapse to the
per-body data reproduces 171 / 828 exactly, so the two are reconciled -- but
deleting the AST body emitter needs each BODY routed, and **A5 has been
under-reported by 24 bodies.** Checklist item 2 now states the per-body
figure and names the key.

**This is the FOURTH counting-key error in one session** (per-instance vs
per-key site rankings, a scratch instrument's 213, a remembered 289 baseline
misattributing 118 bodies, and now a name-collapsed A5). Every one was
plausible, and every one was caught by re-measuring rather than by reasoning.
The standing rule earned four times over: **a number is only meaningful with
its tree, its instrument AND its key; two numbers from different keys are
never a delta.**

### Ratchet design notes

The fallback ratchet fails open in one direction -- a BROKEN sweep classifies
fewer bodies and reads as progress -- so it asserts floors on bodies, routed
count and modules classified BEFORE comparing the number, and both the
ratchet and the floor were demonstrated failing rather than assumed. Same
guard, same reason as the byte-diff gate's compare-count floors, which exist
because a past harness in this repo printed IDENTICAL having compared ZERO
files.

The two gates are COMPLEMENTARY, not redundant: the wide oracle reaches
generic monomorphizations and resumable frames but only for modules some case
IMPORTS, at those cases' option sets; the mega-entry gate covers EVERY module
in lib/tpy including any nothing imports yet. Neither contains the other.

## 2026-08-26 -- the arg-ladder fold, COMPLETE (367 cells, 189 rows, 12 families)

Branch `thir-arg-table`, off master `750896122`. Cashes the standing
stop-loss in the `/tpy-review` follow-ups entry: ten near-duplicate argument
ladders, 352 row instances over 177 distinct shapes, **170 redundant copies =
48% of all rows**, folded into one ordered `(row, sink_family)` table. The
shape the stdlib backlog needs next was already spelled TEN different ways.

Landed: `tpyc/thir/lower/arg_table.py` (shapes, the walk, `register_sink`, the
audit join) plus four sinks -- view 11 rows, protocol 4, native 27, container
42 + a 3-leg prologue. The precedent is one level up in the same file:
`_METHOD_RECV_FAMILY_TABLE`, whose docstring is this design's thesis verbatim.

**The fold is already doing its job, measurably:** `register_sink`'s
import-time identity check caught **11 rows genuinely shared** across families
-- proving reuse rather than coincidental duplication, which is the whole
claim the design rested on.

### What the implementation revised

Four corrections to the approved design, all recorded in TODO.md: the tables
had to stay in `checks.py` (import cycle); `stub_recv` was deliberately not
carried (ambiguous value, and a guessed value in a new table reads as
authoritative later); two shapes exist that the design never named
(`_ArgSink.note` accepting a callable, `_ArgSink.pre` as a family prologue);
and the measured row counts differ slightly from the estimates.

`_ArgSink.pre` runs INSIDE `_walk` rather than in the caller, deliberately --
in the caller it would have been the one part of the fold with a
zero-comparison denominator in the audit join.

### D-1 resolved: benign, and benign FOR A REASON

`_own_lvalue_arg` is admitted at the container ladder with no `temps_ok`
guard where four other ladders gate it. Verdict: benign. Mechanism: on a stub
receiver `own_flush` collapses to `temp_args and is_str_type(ow)`, so every
non-str `Own` payload is position-INVARIANT, and the one position-sensitive
payload has its guard re-derived one layer down in `_lower_call_arg`
(`call.own_str_no_flush`). Gate-reject and lowering-reject reach the same
outcome -- the "no admission preflights" contract working as designed.

**But it is benign only because of three neighbours** (the gate-top view
fence, `_str_owned_slot_arg` running before it, `_own_move_source_slice`
checked before the family gate). Transcribe the row without them and a view
payload renders BARE where the AST hoists a temp -- position-independently.
So for that ladder the migration rule is not "transcribe the row", it is
"transcribe the row WITH its ordering constraints". A fold can convert a
safe-in-context cell into wrong code without moving a single number until
some future case exercises the shape.

### The review found the hole in my own verification

Three specialists came back clean, having verified rather than read -- one
confirmed each `_legacy_<name>` body is BYTE-IDENTICAL to its pre-fold
original via `git show`, which is what makes the audit join's baseline
trustworthy at all.

Then test-coverage found this: **the audit join is gated on a manually-set
env var. Nothing in conftest or CI turns it on** -- unlike `move_audit`,
which conftest enables for every run. Every "0 disagreements" figure quoted
in a commit message came from a one-off local run, and the comparison counts
are printed by an `atexit` hook rather than asserted, so a step that silently
stopped comparing would print zero and fail nothing.

**This is the same defect the immediately preceding branch existed to fix**,
committed by the same author who wrote "a metric ground to zero and then left
to an opt-in flag is a metric that will silently rot" into this file days
earlier -- and worse, because that metric had at least been green once in CI.
The lesson does not transfer by having been written down. It transfers by
being wired to a gate.

**That open question is now ANSWERED, and the answer was that the question
was slightly wrong.** The join is migration SCAFFOLDING: it proves the fold
changed nothing by comparing two implementations of one decision, and after
the fold there is only one. A future wrong EDIT to the table is guarded by
exactly what guarded the equivalent mistake in a ladder -- corpus byte-diff,
face census, pins -- plus `register_sink`, which is strictly NEW protection
(a shared row name reaching a different predicate is an IMPORT-TIME failure).
So the join comes down with the ladders; what is worth adding before H is a
property test over the WALK mechanism, not a permanent legacy canary.

### Steps D-G, and the review that found the hole in the proof

D split the marker ladder into THREE sinks by an ordered FILTER, so nine
`own_ok and` prefixes DISAPPEARED rather than being transcribed. E folded the
reference ladder the other nine were copied from (68 rows). F folded the
generic ladder (27 + a 3-leg prologue). G folded the record-method ladder
(66 rows).

**`register_sink` has now proven 111 rows genuinely shared** across the nine
families -- verified by OBJECT IDENTITY at import, not by name -- against
**eight that looked shared but hold different predicates**, each now named
separately with a pin preventing silent collapse. That ratio is the fold's
argument in one line: most of the duplication was real, and the exceptions
are visible instead of buried in ten or-chains.

**The review round's finding was that the proof was never running.** The
audit join was gated on a hand-set env var that nothing in conftest or CI
turned on -- unlike `move_audit`, which conftest enables for every run. Every
"zero disagreements" figure quoted in commits A-D came from one-off local
runs, and the comparison counts were printed by an `atexit` hook rather than
asserted. **This is the same defect the immediately preceding branch existed
to fix**, committed by the same author who had just written "a metric left to
an opt-in flag will silently rot" into this file. Fixed in `f1aefe200`:
conftest enables it, and the stdlib gate asserts a non-zero comparison count
for EVERY family in the sink registry (an accessor that existed only for
this assertion and came down with it), derived from the registry so
steps E-H are covered without editing the assertion. Cost measured before
committing: +0.7%.

Two things nearly negated that fix and were caught in the same pass: the
detector's own failure message read `getattr(req.a, 'line', '?')` where
`TpyExpr` carries `loc`, so a real disagreement would have printed `?`; and
the test file's `finally: set_enabled(False)` would have DISARMED the
detector for every corpus case sharing the xdist worker.

### The design's predictions were estimates, four times over

Row counts came in high at 37->35, 28->27, 43->42, and low at 26->27; the
count was exact for the first time at G (66). More importantly, at F the
design's STRUCTURAL claim -- that the generic ladder "collapses to substitute
the slot, then run family plain minus/plus its listed cells" -- was false in
three ways: the row ORDER diverges from the first row, **47 of plain's 68
cells are simply absent**, and the "prologue" is two ~40-70 line rejecting
branches. **Verify a design's shape against the code before building to it;
building to a predicted shape is how a fold stops being a transcription.**

Two practices became house patterns because something went slightly wrong
once: **pin the ABSENT cells by name** (F did it for 47, G for 43, so a later
step cannot fill a hole and call it transcription), and **name the tree you
measured against** (a bug entry was corrected for upgrading "the stashed
pre-fold tree" into a named master commit).


### H1 and H2 -- the last ladder, then the scaffolding

**H1** transcribed `_record_ctor_arg_supported`, the hardest of the ten: 62
cells across TWO sinks (the ladder forks into a direct and a restricted
nested tail, sharing cells in a DIFFERENT relative order -- not a subset
filter), 25 new row names, and 35 cells reaching 29 pre-existing cells that
`register_sink` validated cross-module at import.

Its helpers took `lc: _LowerCtx` directly, where every row folded before read
discrete fields -- and `arg_table.py` deliberately does not import
`_LowerCtx` at all. The brief said EXTRACT the discrete facts and stop-and-
report if the surface turned out large, rather than thread a raw `lc` and
quietly convert the request object into a handle on the whole lowering
context. The surface was **four facts** (`inline_narrowed`, `movable_locals`,
`pointers`, `func_name`); each helper gained a `_facts` core that its
`lc`-taking spelling now delegates to, so the two cannot drift. The
extraction was verified behaviour-neutral BEFORE the table was built on it.

Three shapes the table did not have were added rather than flattened. The
one worth recording is **`_ArgRow.decisive`** -- a row that REJECTS mid-chain,
where `pre` runs ahead of everything and a plain row can only admit. It could
have been flattened to an admit-only row, but only via a disjointness
argument (record slot vs str slot), which is exactly the reasoning the
absence-preserving rule forbids. One cell in 367 uses it.

**H2** deleted the scaffolding: ten `_legacy_*` ladders (1636 lines), the
audit join (139), `_ArgReq.subst`, the harness wiring, and the join's own
tests. **Deliberately split from H1** so the migration's most intricate
transcription stayed provable by the join that proved the other nine, and so
the removal of the safety net was reviewable and bisectable on its own. The
alternative -- transcribe and delete in one commit -- would have reviewed the
deletion of the evidence using the evidence.

The teardown was not entirely scaffolding: 19 predicate imports in
`expressions.py` had the legacy ctor ladder as their only consumer, and three
test fixtures built `_ArgReq` positionally and broke on the field removal. An
AST reference scan before and after confirmed no predicate was orphaned.

### What the fold actually delivered, measured

**71 of 189 rows (38%) are shared across two or more families, accounting for
249 of the 367 cells** (fanout `[1:118, 2:26, 3:20, 4:8, 5:6, 6:5, 7:3,
8:3]`). But **lead with what changed, not with the cell count** -- 367 cells
replacing 352 row instances is not smaller, and stating it that way invites
the objection that the win is a restatement. What actually changed:

1. **Widening an already-shared shape is one edit instead of up to eight.**
2. **`register_sink` makes silent cross-family divergence an IMPORT-time
   failure** -- protection the ladders had no equivalent of.

What did NOT change: **62% of rows are single-family**, and 134 named ABSENT
cells prove how routine that is. Adding a shape to a family that lacks it is
still one edit per family. And the enforcement is **nominal, not
structural**: it binds only where someone chose to share a name, and its own
assertion message hands the next author the escape hatch verbatim ("either
share the adapter or give this cell its own row name") -- which is exactly
how all eleven pinned shadow rows came to exist. The fold does not PREVENT
re-divergence. It makes divergence **cost a name**, and makes it visible in
one screen instead of buried across ten or-chains. That is a real win; it is
not enforcement, and calling it enforcement would be the overclaim.

Against that, **the fold exposed rows that shadow a shared name with a
DIFFERENT predicate** -- each given its own name plus an `fn is not` pin so no
later step can collapse them silently. Some are deliberate splits, some are
drift; adjudicating which is post-fold work. The fold's real product is that
the question is now askable.

### What replaced the join, and an adjudication worth keeping

A **mutation-tested** property test over the walk: an exhaustive sweep over
synthetic sinks asserting not just the verdict but the exact ordered trace of
guards and predicates run, the face census, and the reject tag. Seven
independent mutations of `_walk` each fail it.

**Two reviewers described the join as having covered predicate-LOGIC edits,
and that was written into TODO.md as fact. It is wrong.** The join compared
the table against the legacy ladder, and both called the SAME predicate
functions -- the fold moved ladders, not predicates. An edit to a predicate
changes both sides identically and the join stays green. It only ever
protected the TRANSCRIPTION: order, membership, guards, capability filtering
-- exactly what the property test, the row-order pins, the absent-cell pins
and `register_sink` now cover. It took a third reviewer plus a grep of what
the two sides actually called to notice that **a comparison between two
callers of one function cannot detect a change to that function.**

The genuinely uncovered class is narrower: an over- or under-admission for a
shape no corpus case exercises -- already only caught by the byte-diff and
the ratchet before the fold, never by the join.

### One estimate that was badly wrong, corrected here

The design records the payoff as extending a cell's value from `bool` to
`(bool, verdict_token)`, and this file's earlier draft called it the "first
post-fold change, where it repays twice." **It is not a small change.**
Producing the token from `_walk` is trivial, but `_lower_call_arg`'s
~1600-line render cascade independently re-invokes at least 8 of the same
predicates the gate already ran (47 distinct `*_arg(` call sites) to decide
HOW to render, rather than consuming any verdict the gate produced. Making
the token useful means restructuring the renderer to dispatch off it -- **a
second migration of comparable size to this fold.** Do not start it as a
follow-up commit.


### The caveat that belongs at the front, not the back

**The fold removed duplication on the FAMILY axis and left it wholly intact
on the GATE-vs-RENDER axis -- the same defect class the branch existed to
kill, now the larger of the two, and unowned.** `_lower_call_arg`'s
~1600-line cascade independently re-invokes at least 8 of the predicates the
gate already ran, across 47 `*_arg(` call sites, to decide HOW to render
rather than consuming any verdict the gate produced. Consuming it is a second
migration of comparable size. A summary that ends at "ten ladders are one
table" reads as more finished than the work is.

### What the teardown cost, summed -- nobody summed it at the time

Three separately-justified commits removed every mechanism able to measure
the TABLE'S OWN COVERAGE: `_audit`/`_first_row`; the per-family reachability
floor (whose docstring had argued for its own permanence -- "the gate saying
the family has no witness ... is the thing worth knowing"); and finally
`_SINKS`/`registered_families()`, reasoned as "there are no later steps --
the fold is complete." **That reasoning is true of the FOLD and false of the
TABLE**, whose whole purpose is to make the next arg work cheap.

Reachability is a DIFFERENT question from transcription. The join proved the
table matched the ladders; reachability proves the corpus can SEE the table
at all. And the per-cell denominator -- how many of 367 cells any run
witnesses -- was measurable for free the entire time (`_first_row` already
returned the firing row) and was never measured before the tool was deleted,
while the honest-guards paragraph simultaneously named "a shape no corpus
case exercises" as the residual risk. **The instrument for the stated risk
existed, was never pointed at it, and was thrown away.**

The general form, which cost three separate mistakes on this branch: **a
claim about what an instrument covers, believed without checking.** The join
was assumed to be running when it was off; it was assumed to cover
predicate-logic edits when it structurally could not; and its removal was
assumed to cost only what it proved.

**CLOSED, two commits later.** The reach floor was restored (per-family, the
expected set derived from the registry so a future sink needs no edit) and the
per-cell coverage measurement was finally taken: **the corpus witnesses 297 of
367 cells** (stdlib sweep 75, corpus 289, union 297; 70 never decided by
either). The floor demonstrably catches what the byte-diff cannot -- starving
one family of arguments produced ZERO divergences, because the affected bodies
fell back and the AST re-emitted them byte-identically. Per-cell coverage is
REPORTED, not gated: a never-decided cell is often a legitimate transcribed
absence, so a raw floor would cry wolf. Cost 168ns/verdict, ~0.009%.

Two facts that measurement surfaced, both pre-existing and neither introduced
here: the table's sole `decisive` cell decides ZERO arguments on both corpora
(its pre-guard never passes), and **only 52 of 367 cells carry a face at all**
-- so the standing zero-witness face census, the instrument this project leans
on to find un-exercised arms, covers 14% of this table. A review checked the
five families where the ownership-transfer row `own_move` is never decided and
found the preceding rows test disjoint shapes, so it reads as genuine
unreachability rather than an earlier cell shadowing a move decision -- but
that is a static reading, and converting it to a checked fact needs an
adversarial case per family. Recorded, unowned.

### Two aftermath lessons, kept here rather than in CLAUDE.md

**The tracking-label rule earned its place by recurring.** A review found one
`D-1` citation in a code comment and it was fixed; the next round found eight
more, plus one in a test NAME that a comment-only grep missed. The cause was
upstream of the code: the briefs handed to implementers named the drifts by
label, so the labels got written down. The rule now lives in CLAUDE.md as an
invariant; this is the anecdote that produced it, which is exactly the kind of
narrative that does not belong in a file loaded every session -- a point a
reviewer made about the rule's own first draft, which embedded this tally.

**There are now THREE coverage instruments over the same table** -- the face
census, the fallback-reason tally, and arg-cell reach -- with distinct but
overlapping purposes. Each is justified (reach is not journalled per lowering
attempt, faces are; a verdict in a body that then falls back still decided
something). Before adding a fourth, check whether it is genuinely a fourth
QUESTION rather than a fourth mechanism for an existing one.

**A better gate shape than the one shipped, if anyone revisits it:** per-cell
coverage is reported, not gated, because a never-decided cell is often a
legitimate transcribed absence and a raw floor would cry wolf. The middle
ground nobody built is a RATCHET on the unreached SET -- pin today's unreached
cells and fail only when a previously-reached cell drops out. That catches a
regression without penalising a pre-existing deliberate gap.

### Stdlib grind (2026-08-27): 171 -> 52 collapsed, 195 -> 60 per body

FIVE batches, each followed by a full specialist review cycle. Ratchets ended
at `MAX_FALLBACK_BODIES = 60` (per body), `--max-fallback 52` (collapsed), and
`MIN_ROUTED_BODIES = 1185`. Routed 1050 -> 1185 per body; the -135 fallback and
+135 routed conserve exactly, so the drop is real routing rather than bodies
falling out of classification.

Batches one and two are described in detail below (they took it to 111 then 98
collapsed); three through five are summarised at the end. That asymmetry is
itself the entry's first lesson: the record was written after batch two and
then not updated for three more, so it shipped claiming a result 46 bodies
short of the truth until a readiness review caught it.

Batch one: ten lowering cells, a corrected record predicate, a diagnostics
repair, one wrong-code fix, and ~43 repaired test pins. Routed 828 -> 888
(collapsed key).
Full suite green with full exec: 12884 passed, 3745 cases built and run with
nothing skipped via cache, 0 move-verdict divergences over 1010 joined nodes,
0 binding-fact gaps, dial 3746/3746. Zero snapshot churn -- no
`tests/cases/*/expected/` file changed, which is what byte-identity means when
it holds.

**The predicate that was wearing five tags.** `_f1_record` rejected any record
whose TypeDef carries a `cpp_formatter`, and its docstring justified that with
"a formatter means a different C++ shape entirely". True for `list ->
std::vector`; false for `tpy.coro.Waker`, whose formatter returns
`::tpystd::coro::Waker` -- exactly the record spelling. Of 33 formatter-carrying
TypeDefs, Waker is the only one of RECORD category, so the exclusion was a
Waker exclusion wearing a general shape. Two independent drills converged on it
from opposite ends: one found Waker in 19 of the 70 bodies behind the two bare
landmark tags, the other found that a tag reading `ctor.mil_field.nominal.call`
was "not a MIL question at all" -- all five of its bodies were Waker. The gate
now asks the category. Measured 25 collapsed bodies routed against an ablation
that had predicted 30, which is the ordinary gap between a gate ablation and
byte-identical flips: the ablation proves admission, not that the render
matches.

**A reject tag that discarded the answer it had already computed.** 70 bodies
-- 41% of the backlog -- carried a bare `expr.call` / `expr.method_call`
naming only the reject SITE. The detail existed: gates write a precise blocking
shape into the compiler's detail slot at ~430 sites, but only `stmt.*` reasons
ever composed it back. A ~10-line sibling of `stmt_reject_reason` split two
opaque buckets into 33 actionable ones, and every one of the 62 remaining
reasons now names a shape. This also retires the reason `probe_sites.py`
existed: a four-minute traceback-instrumenting probe built around a one-line
composition gap.

**The wrong-code move, and why only one gate could see it.** A cell asked
`_is_move_source` about the AST source NODE and treated a yes as licence to
move the RENDERED value. Those coincide only when nothing converts in between;
at an owned str/bytes element fed a view-form source the copy constructs from a
trivially-copyable view, so the move named the wrong object. THIR emitted
`std::string(std::move((*a)))` where the AST emits `std::string((*a))`.

The move-verdict join reported **0 divergences over 0 joined nodes** for that
case. The AST does not merely reach a different verdict there -- it never ASKS
the move question, so there was nothing to join. The join compares decisions on
shared nodes; wherever THIR asks a question the AST does not, it is structurally
blind, and the byte-diff is the only remaining detector. That is the general
statement of the hazard, and it is why the three gates are complementary rather
than redundant. Fixed by asking the copy question first (`_owned_copy_sink`), so
the move fires exactly where the copy does not. Two later audits, by different
reviewers, each confirmed this was the only new move-deciding site on the
branch.

The proposed prophylactic is in `TODO.md`: any arm that both reads
`.form`/ownership off the lowered value and decides a move should ship a
routing pin with an explicit view-source-at-an-owned-slot case.

**Five pins that could not fail, found by four unrelated routes.** A malformed
`NominalType("Int32", "tpy.Int32")` -- the second positional is `type_args`,
not `_module_qname` -- let a boundary pin pass even with its predicate widened
to admit `list`. Two negative pins matched a bare landmark by exact key, so the
suffix change retired them silently. One had been satisfied since it was
written by an unrelated sibling body in its own fixture, never by the body its
comment described. And one counted faces from `_lower_ctx_witnessed` (bare
`lower_module`) while claiming a result about the codegen path -- the two
DISAGREE about whether a body routes, so the face came from a run in which the
body never routed. That last one is the "witnessed before it can raise" hazard
one layer out: not the face incrementing early, but the LENS being one that
raises.

The shared repair is `testutil._rejects_at` / `_assert_rejects_at`: match the
landmark, pass the shape wherever the pin claims a named reject. The whole-tally
form (`fell == {tag: 1}`) was removed wherever touched -- it fails whenever any
unrelated body in the fixture changes status, and the cheap repair is to
re-record whatever the run printed, which is precisely how a pin stops asserting
what it was written for.

**`_record_class_binding` stays narrow, deliberately.** It still keys on the old
formatter check while its sibling asks the category. An implementer widened it,
measured zero effect, and reverted: `lc.pointers` holds only non-value-type
bindings and the sole RECORD-category formatter-carrying type is a ValueType, so
the shape is unreachable. A routing pin requires the face to be WITNESSED, and
an unreachable shape cannot be -- widening it ships an untestable arm. A
reviewer read the same divergence as an inconsistency worth closing now; the
three-unit contract decides it. Its docstring records the invariant and the
condition under which to widen.

**`_builtin_value_record`: the retirement case is weaker than it first looked.**
At three of its four sites it is spelled `_f1_record(x) or
_builtin_value_record(x)`, and with `_f1_record` corrected two of its faces drop
to zero witnesses -- which reads as proof it was only ever a workaround. It is
not proof. `_f1_record`'s builtin path additionally requires
`_f1_record_type_arg_ok` and excludes `is_compile_time_only`, neither of which
the sibling checks; the zero-witness result is corpus-empirical, and Waker and
Poll both happen to be non-generic. If it is retired, the evidence needed is an
assert-backed equivalence over a GENERIC builtin value record, not a witness
count. Left open as a user decision.

**One new gate/render duplicate, landing immediately after the fold that
existed to reduce them.** `_lower_call_arg` now also calls
`_container_field_pass_arg` at render time, mirroring the arg-table row that
already gated it, following the `_record_field_ref_arg` precedent on the line
above. Not a new mechanism, but the next fold should know the debt grew by one.

**An AST-path UAF, confirmed by building it.** A `-> StrView | None` fed an
owned-str-returning CALL emits `return make(s);` into
`std::optional<std::string_view>` -- a view onto a destroyed temporary. Built
and run, it prints garbage while `len()` reports the correct length; CPython
prints the string. `BUGS.md` had claimed the str side was "safe by
construction", scoped to literals; the face is source-kind-dependent, not
type-dependent. Entry widened and regraded MED. THIR keeps the shape rejecting,
so the migration neither introduces nor inherits it.

**Batch two: 111 -> 98 collapsed, 127 -> 112 per body**, six rows. A Char
payload at an `Own[T]` copy-temp slot; a record field bound bare at a method's
record ref slot; an owned bytes NAME bare at an `Own[bytes]` element slot, plus
the bytes move slot widened from the bytearray coerce chain to a bare owned-form
name; a value-tuple container element read whole; that element read through a
container FIELD receiver and through a module GLOBAL; a matching generic-tuple
call rvalue returned bare. The largest tag, `method.arg_shape`, went 15 -> 6.

**The partition was mine and it was wrong.** The tuple cluster was handed to the
lane owning `statements.py` on the theory that `subscript.tuple_shape` was a
statement-position question, because it appeared under three different `stmt.*`
prefixes. It is not: all five rejects are constructed at ONE site in the tuple
subscript ladder, and the prefixes are composed afterwards by the statement
chokepoint. Only 3 of 12 bodies raised in that lane's files. The lesson is about
reading tags: a composed tag names the position where a reason was FORMATTED,
not where it was decided, so partitioning by tag prefix partitions by the wrong
thing.

That lane also refused the one lever it did own -- passing `subscript_prechecked`
from a var_decl sink -- because `faces.py` records that those per-sink bypasses
were deliberately REPLACED by consumer-blind admission, precisely for skipping
the gate's receiver guards. Re-adding one would have shown progress and
reintroduced a removed design.

**Two silent defects, both found by adversarial dual-generation rather than by
any gate.** The `own_lvalue` row hoisted a copy temp for a ternary at an
`Own[<value type>]` slot, where that slot is a plain by-value param an lvalue
binds directly: the AST's copy arm never fires and the whole render is the bare
conditional. Live for fixed ints; the corpus never reaches it. And
`_field_decl_type` peeled readonly/Ref/Send-Sync but not `Own` while its partner
`_field_receiver_ok` admitted an `Own[record]` binding, so every gate on that
pair silently rejected an `Own`-param receiver's members -- a false negative,
which is why nothing was ever wrong-coded by it and nothing caught it.

**A pin passing for a reason its comment got wrong**, the ninth of the session:
`test_own_bytes_param_at_owned_elem_slot_still_defers` asserted the AST hoists
`auto __tmp_N = v;` for an `Own[bytes]` param. It does not and never did.

**On the handed-off drill, three of its claims were wrong and re-derivation
caught all three**: "3 bodies" was 1, "4 bodies" was not 4 clears (each revealed
a different next blocker), and one body's diagnosed cause was simply not the
cause -- the receiver WAS in `declared`; the real blocker was a `StrView` sibling
element keeping the tuple out of the readable-element set. The lane was told to
verify rather than trust, and that instruction is what made the hand-off safe.

**Batches three, four and five: 98 -> 52 collapsed, 112 -> 60 per body.** Rows
for value-tuple container elements and their field/global receivers; open-T
copy over a subscript source; an open-T call result at a native protocol slot;
the open-`Own[T]` rvalue row reached from the generic lane; a value-optional
member at both ctor families; storage-optional and wrapper call forwarding at
returns; a by-value container return from a method call; a record field at a
method's record ref slot; an owned bytes name and an owned bytes call rvalue at
`Own[bytes]` element slots; a Char payload at an `Own[T]` copy temp; str/bytes
slices over a container element and over a call result; a raw `Ptr[T]` param
and an `Own`-wrapped capture on resumable frames; a value-tuple predecl at a
branch-chain hoist; a str field as a membership haystack; a pointer source at a
flush-less nested ctor slot; a str tuple element as a method receiver.

**Two wrong-code defects were fixed, and both had been live on master.** A
`self` receiver at a user-record subscript emitted `this[...]` where the AST
spells `(*this)[...]` -- C++ that does not compile. And the batch that found it
also found that a `self` operand at a unary operator emits `-(this)`: still
live, filed, and NOT fixed here, because the arm that would route the one
stdlib body behind it was built, seen to expose the divergence, and reverted.

**What the gates could not see, stated plainly, because it is the migration's
central lesson.** Every defect this branch found was invisible to the suite.
The wrong-code move was caught by the byte-diff alone -- the move-verdict join
joined ZERO nodes there, because the AST never asks the move question at that
position, and a join over zero nodes reports clean. The `self`-subscript and
`-self` bugs were caught by generating shapes no committed case contains. Three
further defects sit in the tree right now, unreachable until an ordinary row
lands. Eleven test pins were found that could not fail, by eleven unrelated
routes, every one green in a green suite.

The corollary for whoever runs the next wave: **a green suite is evidence about
the corpus, not about the compiler.** Budget adversarial dual-generation at
every widened arm; it out-yielded ~32 specialist review passes on this branch.

**Three method lessons, each of which cost a lane.**

A composed reject tag names where a reason was FORMATTED, not where it was
decided. `subscript.tuple_shape` appeared under three `stmt.*` prefixes and was
built at one site in the subscript ladder; lanes partitioned by tag prefix were
therefore given work that raises in a file they did not own. Resolve the raise
site before cutting lanes.

Grouping the tail by reason DETAIL rather than by composed tag looks like it
reveals clusters. It does not: it reveals shared raise SITES. Four bodies under
`field.result_type` were four different questions at four caller positions.

An instrument fix is worth exactly the bodies currently sitting under the
catcher's own tag -- a number countable before doing the work. The first such
fix carried 70 bodies and turned two opaque buckets into 33. Later ones carried
about one, and were done anyway because the first had paid so well.

## 2026-08-28 -- the stdlib tail, ground by raise site (fallback 60 -> 39 per body, 52 -> 32 collapsed)

Twenty-one bodies, 25 commits, one orchestrator and sixteen agents. Routed rose
1185 -> 1206 as fallback fell 60 -> 39: the same 21 both ways, which is what
separates real routing from bodies dropping out of classification. All three
ratchets re-armed together at the ground tree.

**The instrument was the wave's actual product.** The tail had flattened to ~1.2
bodies per reject reason, so ranking tags could no longer select work.
`scripts/thir_migration/thir_stdlib_sites.py` records the tpyc frame that
CONSTRUCTED each `ThirUnsupported` and matches it back to the body's composed
reason by containment, longest match first, so the innermost deciding site
outranks the chokepoint that reformatted it. It collapsed 52 near-unique tags
onto about a dozen decision sites: three tags under one `field.result_type`
decision, four ctor-arg details under one ladder, two `urllib.parse` twins under
one return row. Its blind spot is in its docstring rather than left to be
rediscovered -- the resumable and simple-generator gates reject through a helper
that never constructs the exception, so those four bodies print no site and had
to be grepped.

**A shared raise site is still not a lane, and this cost the most.** The
ctor-arg ladder looked like a four-body cluster and was four unrelated
predicates in two files; the `own_generic` / `union` / `optional` suffixes turn
out to tag the SLOT type, never the blocking shape. The three tags at
`field.result_type` failed three different clauses, under two different flags,
threaded from three different sinks. A `truthy.call_nonbool` tag was decided one
level earlier by a silent `return None` in a union member-class predicate, in no
truthiness ladder at all. **The site collapses the search; only reading each
ladder settles the lane.**

**"The body routes" is not the property.** Two cells -- `json::loads` and
`tplib.channel::recv` -- route under admission alone while silently dropping a
`std::move`, and the byte-diff was the only thing that caught either. Admission
and render are separate claims. A third, `_parse_http_date`, produced a
divergence when admitted at the arg ladder (bare where the AST hoists a temp,
plus a renumbered downstream temp); the honest fix was threading the flush into
the tuple-literal lowering, which no tag pointed at.

**Refusals were the highest-value output.** Three of five lanes declined work
rather than forcing it. One reverted a cell on finding the fence it would have
overturned was live and correct -- dropping `not is_param` opens the sgen slice,
where the AST captures `__iter` and THIR captures `__beg/__end/__init`; the
predicate sees identical inputs for both shapes, so no local discriminator
exists. One stopped at a file boundary and handed over rather than reaching
across. One rejected the discriminator its brief specified and proved it half
wrong: `frame_local_types` is only `generator_locals`, so testing it alone calls
a param "not frame-backed" while params ARE frame members.

**Two fences whose recorded reason was wrong when written.** One called a shape
"the filed wrong-code case where the owned slot never gets its view->owned
copy"; the AST does emit the copy, both paths agree byte for byte, and no BUGS
entry describes it. Its neighbour is real and still rejects. Separately, a pin
was invalidated rather than violated: admitting a concrete `bytes` type-arg made
the fixture's deliberately non-F1 record F1, so it routed down an earlier arm.
Its premise, not its invariant, had expired -- re-pointed at the payload the
same commit had fenced out.

**A zero-yield broad widening was completed rather than left.** The `bytes`
type-arg leg gates `_f1_record`, consulted at ~296 call sites, and a controlled
same-tree A/B showed it paid NOTHING on its own -- the body merely moved one
blocker deeper. It also silently invalidated the pin above. Completed with the
companion arg row (A/B: both halves load-bearing) rather than kept as a broad
admission that buys nothing, which is the worst of both.

**Parked deliberately, with reasons, not attempted:** `datetime.__abs__` /
`fromutc` behind the live `-self` -> `-(this)` defect, whose routing arm was
already built and reverted once; the `self`-subscript deref receiver, which
would be the eighth hand-application of a fact already wrong at two sites, and
whose oracle `(*this)[i].__deref__().m()` was confirmed reachable;
`collections::most_common`, AST-blocked with two filed defects in one body and a
second THIR gate behind them; `HTTPSConnection.__init__`, needing a value-category
decision for a prvalue record ternary plus a `copy(pointer-local)` row adjacent
to an open bug; `thread.spawn`, bottoming out in a standing native-record-return
policy fence; `os.walk` and `_request_on`, multi-layer for one body each.

**Method notes for the next wave.**

Measuring against a moving tree is worthless, and it happened twice: a scout
killed its own corpus A/B when implementers landed under it, and a lane
retracted a positive gate reading as contaminated. Scout to completion before
dispatching implementers, or accept that every mid-flight number is provisional
and re-measure on a quiet tree at the end.

File OWNERSHIP does not protect a shared git INDEX. Two commits swept in peers'
staged-but-uncommitted files. Hand implementers a filtered `git apply --cached`
rather than `git add` when lanes share a checkout.

A targeted subset is not the suite. The one unit regression on this branch
escaped because the full THIR run timed out under five concurrent lanes and its
lane fell back to nine files. It was then misattributed by two lanes to
different commits; a six-point bisect settled it in one pass and exonerated
both guesses. **Bisect the tree; do not poll the agents' opinions of it.**

Scratch under `/tmp` filled the filesystem and hard-blocked every tool -- scouts
had copied whole `tpyc` + `lib/tpy` trees and compiled C++ into it. Point agent
scratch at a gitignored repo-local directory instead.

## 2026-08-28 (second round) -- the scouted remainder (fallback 39 -> 21 per body, 32 -> 17 collapsed)

Eighteen more bodies, ~24 commits, three lanes. Routed 1206 -> 1224 -- 18
both ways, ceiling and floor. Across
both rounds of the day: 60 -> 21 by body, 52 -> 17 collapsed, and the ceiling
and floor moved by the same 39 -- the check that separates real routing from
bodies falling out of classification.

**Why there was a second round at all, which is the process lesson.** Round one
partitioned lanes by FILE so five implementers could run concurrently, and only
dispatched cells that fit inside one file. Every cross-file cell was deferred --
and then the round went to review with roughly half its own scouted, measured,
byte-identity-verified work undispatched. Four flips a scout had already
measured were never handed to anyone. The file partition is a sound way to
parallelize edits; it is not a way to select work, and nothing reconciled the
two. If a round defers cells for a mechanical reason, the deferred set is a
work item, not a footnote.

**Three defects filed, none caused by these cells, one of them the kind the
gates cannot see.** A `@nocopy`-element list literal at a GENERIC record's
`Own[list[T]]` ctor param renders a copying brace-init under THIR where the AST
spells `make_vector` -- a live THIR/AST divergence needing generic AND nocopy
together, which is why no corpus case carries it and why the byte-diff never
compared it. Separately: binding one recursive union alias into a
differently-named one crashes the compiler with `RecursionError` (merged into
the pre-existing entry for the same cycle -- it turned out to be a second
trigger, not a second bug). And an `@overload`-ed `async def` emits one
coroutine frame per overload entry, all identically named: a C++ redefinition
that miscompiles today.

**A premise was falsified by the implementer who was told to act on it.** A
lane cleared an `@overload`-ed resumable GENERATOR by dropping one term from a
carve-out, and judged the `async` twin to be the same one-body argument. It is
not: a `...` stub body is not a generator, which is exactly what keeps generator
stubs off the frame entry, but an `async def` stub is still async, so every
entry reaches the frame emitter. The next lane checked before widening, found
the three-frame redefinition, kept the reject, corrected the fixture's stated
reason, and pinned the three-frame emission so the reject cannot later be
re-read as a gap.

**An ill-formed AST render was confirmed by building it, and deliberately not
mirrored.** A genuine `else:` after an isinstance chain over a 3+-member
nullable union extracts a variant alternative that does not exist
(`std::get<std::optional<C>*>` against `variant<monostate, A*, B*, C*>`); g++
rejects it. The cell admitted only the elif-link shape and pinned the genuine
`else` as a boundary.

**"The body routes" is not the property, twice more.** Two cells routed under
admission alone while silently dropping a `std::move`; both were caught by the
byte-diff and both needed a second row. One implementer improved on its brief
here: told to make two edits agree, it found the pointer-local deref+move block
sits BEFORE the wrapper gate, so extending one guard makes the move impossible
to drop by construction rather than by two edits staying in sync.

**Method notes.**

The instrument this round leaned on cannot see a known class of lies in the
metric it measures, and that is worth more attention than it got. The
raise-site spy hooks `ThirUnsupported` construction -- but the resumable and
simple-generator gates reject through a `_reject()` helper that never
constructs one, so four bodies had to be found by grepping the reason string.
Independently, the backlog records six-plus sites that CATCH a
`ThirUnsupported` and re-raise with a fixed tag, discarding the operand's own
reason, and notes the enumeration was built by grepping `raise ... from None`
and therefore misses the bare `except ThirUnsupported: return _reject(...)`
form entirely. Those are the same defect from two directions: a reject that
does not travel as an exception is invisible to the spy, and a reject that is
caught and relabelled is visible but lying. Any future scoping done off the
tally inherits both. Fixing the catchers is diagnostics-only and can be done
at any time; the spy's blind spot needs the frame gates to raise rather than
note, which is a larger change and should be priced before the next wave
trusts a site histogram over a body count.

Scout claims kept failing in the same direction -- confidently, and about
mechanism rather than about size. A blocker attribution named the wrong
predicate; a binding was called pointer-repr when it was value-repr; a
"sole blocker" had a second gate behind it; a caveat about a collapsed metric
row was wrong. Every one was caught by an implementer verifying before
building. Brief implementers to verify, and treat "the scout was wrong" as a
reportable result rather than a failure.

`git commit --only <paths>` does NOT protect a file two lanes both append to:
it commits the working-tree state of the named path, peer edits included. Two
`faces.py` lines again landed under the wrong commit message. A genuinely
shared append-only file needs serialization, not path-scoping.

A broad `pkill -f pytest` killed a peer's run for the second time in one
session. Kill only PIDs you started.

### Stdlib grind (2026-08-28): 17 -> 15 collapsed, 21 -> 16 per body

Five stdlib bodies flipped: the two `asyncio` gather `__poll__` bodies,
`tplib.requests::Session.__exit__`, `tpy.channel::_Send.__poll__`, and
`asyncio::_SockConnect.__poll__`. Routed floor 1224 -> 1229. Ceiling and
floor move by the same five, which is what separates real routing from
bodies dropping out of the classification; the two keys stay separate and
every figure above names the one it uses.

The rows: an Own-slot leg in the generic slot prologue
(`call.generic_own_composite_slot`); `own_tparam_call_rvalue` extended
from the record-method sink to the ctor and marker-qualified sinks
against one shared predicate; a new `value_tuple_field_pass` row plus the
matching field result leg reached from the f-string interpolation; and a
`TpySubscript` receiver leg on the user-Deref wrapper predicate.

**A recorded blocker was falsified, for the fourth consecutive wave.**
`tplib.requests::__exit__` was filed as blocked on the `self`-subscript
deref fact -- the hand-applied "is `self` a pointer here" bit that is
wrong at two of its sites and is under a pending decision. It was not:
the receiver is `self._pool[k]`, which renders as an ordinary member
access, and no bare-`self` value position is constructed anywhere on that
path. The scout established this by BUILDING the shape the hazard
protects (`self[0].close()`) and watching it still reject at an
independent downstream gate while four benign subscript receivers routed.
The row then shipped from a single predicate leg. The recurring shape is
not that records go stale; it is that a blocker reason recorded from a
reject TAG names where a reason was formatted, and a later reader treats
it as where the reason was decided.

**A docstring asserted a guard the code did not have.** The shared
predicate behind the call-rvalue-at-an-Own-slot rows claimed a
borrow-returning callee was excluded by not being an rvalue source.
Measured: `is_rvalue_source` answers True for both the owning and the
borrowing callee, so nothing excluded it. The AST passes such a result
bare into an `own_param_t<T>` (`T&&`) slot, which is ill-formed at a
reference-type instantiation -- filed, with a g++ reduction. The
discriminator is the callee's DECLARED return type. Two lanes reached
this independently, one from a g++ reduction and one from a boundary pin
that failed, and the guard was in place before the row that needed it
landed. The lesson is about the artifact: the docstring is what made the
gap invisible, because anyone reading the predicate would have believed
the case was handled.

**A row registered into a shared listing needs a witness per FAMILY that
carries it, not per shape.** The three-unit rule covers the shape axis and
says nothing about the sink axis. `value_tuple_field_pass` was pinned
under the qualified family only, while the row listing also placed it in
the native family -- an admitted arm whose sole cover was the corpus
byte-diff, which is deleted at cutover. Its sibling in the same wave was
registered correctly, so the two are a matched pair worth reading
together. The skipped pin was justified as needing a hand-written C++
companion carrying a tuple param; that was false, and an ellipsis-stub
native declaration across two in-repo modules is sufficient, as two
existing test files already do.

**Method notes.**

The tag histogram is exhausted as a selection instrument and the site
histogram nearly is: after this wave no site carries more than two rows.
The backlog claimed two sites held four and three; a fresh sweep found
neither, and one of the rows was attributed to a site it does not reject
at. Rank by measurement, never by the previous wave's record of one.

The name-collapsed key hid a body count inside a single row. The
`asyncio::__poll__` row stood for four bodies, and they were three
different questions across two sites -- the "a shared site does not
define a lane" rule firing inside one ROW. Nine of that module's thirteen
same-named bodies already routed. A collapsed row is a search key, not a
work unit.

An orchestration note that cost real time: two lanes were partitioned by
file, which is correct, but the shared face registry is edited by every
lane and belongs to none. One lane dropped its face registration rather
than commit another lane's in-progress line, and its arm shipped
witnessed only by a mechanical routing pin. Serialise the registry edit
or hold it for consolidation; do not leave it to the lane that finishes
second.

**The method that keeps working, stated positively.** Four consecutive waves
have falsified a recorded blocker, and the weak lesson ("records go stale") is
not the useful half. The strong one: the cheap way to test a recorded blocker
is to CONSTRUCT the shape it claims to protect and see whether it actually
rejects. That is how this round established that a container-element receiver
never reaches the hand-applied self-pointer fact -- by building the receiver
the hazard names, watching it reject at an independent downstream gate, and
watching four benign siblings route. The construction then pays a second time,
because it is already the boundary pin the arm needs. A blocker reason read off
a reject tag costs nothing to record and cannot be trusted; a blocker reason
that survived an attempt to build past it is worth the line.

**Where the defects were this round.** Nothing in the compiler diff warranted
backing out; the escalations from the readiness challenge were all in tooling
or orchestration, and three of them were rules already written down and not
applied. Worth noticing as a phase change: on a green, well-pinned diff the
remaining defects migrate out of the code and into the process layer, and the
review dispatch table had gone blind to exactly this kind of wave -- it keyed
"codegen logic" to the AST emitter's directory, so a THIR-only change silenced
three specialists at once. That blind spot widens to total at cutover, when the
AST emitter stops changing at all.

### Self-receiver deref made intrinsic (2026-08-29): a defect fix, zero bodies

Closes the thread two earlier entries left open by name. `-self` lowered to
`-(this)` where the AST spells `-((*this))`, on a body that ROUTED with an
empty fallback map -- ill-formed C++ on ordinary user source, reachable
today. The `self`-receiver deref fact was hand-applied at nine value
positions; it is now set where `THIRSelf` is CONSTRUCTED, from
`self_is_pointer`, and cleared at receiver positions through one node
property that also backs a `validate.py` rule, so the render and its guard
read a single predicate.

**State the value as defect-retirement, not consolidation.** The branch moves
ZERO stdlib bodies. The arm it was meant to unblock was built, verified
(fallback would move 16 -> 15 per body) and backed out a THIRD time, because
routing `timezone.fromutc` exposes a DIFFERENT filed defect -- no post-if
narrowing alias after a negated-isinstance guard -- whose fix is an open
design call. Of the three bodies the decision packet priced, only
`timedelta.__abs__` is actually won by that arm; `ZoneInfo.fromutc` moves to
another reject. What the branch does buy is one live wrong-code defect and
three latent miscompiles retired (`-self`, a `self`-rooted user-deref chain,
and a resumable `return self` that would have emitted `*__self`, a deref of a
reference), plus the first pin coverage this area has ever had.

**Why no gate could see any of it, measured rather than asserted.** Across
the whole case corpus, value-position `self` appears in three distinct
shapes; the rest of the `(*this)` occurrences are one waker call repeated
hundreds of times. So the byte-diff has no witness to compare, the ratchet
sees a routed body, and the move-verdict join has nothing to join. "Suite
green, zero divergences" proves nothing about the positions this branch
touched -- the unit pins are the whole instrument, which is why the
position matrix was pinned rather than probed.

**The invariant changed SHAPE; it did not become unforgettable.** The
positioning helper wraps 3 of 14 field-access and 6 of 10 method-call
constructions -- the rest are receiver-shape-guarded and safe today, and one
arm still builds its receiver with a hardcoded non-deref instead of deriving
it. Construction-site discipline is still required. What changed is the
failure mode: a forgotten position now crashes at an arrow site instead of
silently miscompiling at a value sink. Louder and better, and a trade rather
than an elimination. Note the direction of that trade against the filed
cutover blocker for `THIRValidationError` being uncaught -- every new rule
here enlarges a crash-on-valid-code class whose newest trigger needs no flag
and no macro.

**Three things the design brief got wrong, each caught by an adversarial
probe rather than a gate.** The user-deref chain wanted the arrow on its
first hop, so making the deref intrinsic converted an ill-formed render into
a DIVERGENT one -- and fixing it properly surfaced FOUR arms that had each
dropped the self clause, not the two the brief named. A third consumer class
existed that the brief did not name at all: raw pointer slots, where an
element binds the receiver pointer itself. And one of the three sites listed
as wrong-today was not constructible from source, so it is pinned at the node
level instead of being invented as a case. A survey that enumerates consumers
by grepping one field name will miss the consumers that spell the same fact
differently.

### Post-if narrowing made condition-blind (2026-08-29): a filed silent no-op retired

Closes the entry the previous wave named as its own blocker. Sema stamps a
branch's narrowings by a compositional recursion over the whole boolean
algebra (`sema/narrowing.py`); the AST's post-narrowing arm is driven by that
map and consults the condition only for two whole-condition filters, both
sema-STAMP reads with a `not` peel. THIR recovered the subject from the
condition's SYNTAX instead, through a finite shape recogniser whose
non-recognition path was `return None` -- it neither emitted nor rejected. The
lowering now derives the plan from `else_type_facts` and raises for anything
the AST would extract and no arm here mirrors.

**A finite recogniser cannot cover a compositional producer, and the failure
mode was the silent one.** The smallest counterexample is a negated
`isinstance` leaf inside an `or` chain: the chain reader peels a `not` around
the WHOLE chain but requires a bare call per leaf, so `if not isinstance(u, A)
or flag: raise` lost its `const auto& __u = std::get<A>(u);` entirely. Two
adjacent arms had the same shape of silence -- the generic `if` and `while`
lowerings built their nodes with no fence on concrete BRANCH facts, so an
unrecognised condition carrying one dropped the AST's branch-entry extraction
too. That second class was never filed; it was found by asking the same
question of the neighbouring arms rather than by any gate.

**Why the corpus never saw it, and the reason is a warning about "self-limiting"
defects.** Where the narrowed name is field-read after the guard (`return u.n`),
the missing alias makes the read itself reject, the whole body falls back, and
the byte-diff compares AST output against AST output. The divergence is visible
only when the guard's subject is NOT read afterwards -- or is read in a position
that routes anyway. So the shape that exposes the bug is the one that looks
least worth writing a case for, and a green corpus was never evidence about
this arm at all.

**Measured, both directions.** Five hand-built reproducers were DIVERGENT before
and are byte-identical after (ordinary body, either leaf position, a
three-member nullable union, a generator frame, a finally helper); the two
controls stayed identical throughout; a two-subject guard that previously fell
back at its return now routes and emits BOTH aliases, which is the multi-alias
shape the arm always owed and never had. Whole corpus: 3746/3746 cases still
migrated with zero fallback, so neither fence costs a single body. Stdlib gate
unchanged at 16 per body / 1229 routed, byte-identical -- this branch is a
defect fix, not a routing win.

**The three consumers had drifted apart.** The statement walk, the resumable
leaf arm, and the finally-helper detector all asked the same recogniser, and
the detector's whole job was "would the AST emit here?" -- a question only the
facts can answer. Two of the three also silently dropped a POLYMORPHIC post-if
fact, which nothing had noticed because the union flavour was the one anybody
probed. Where a fence is a stand-in for an arm, it has to be phrased in the
oracle's own terms; phrased in the mirror's terms it degrades to "would WE
emit here?", which is always yes.

### The resumable and simple-generator seams reach the validator (2026-08-30)

THIR's structural validator (`tpyc/thir/validate.py`) had never run on async
or generator bodies. `lower/resumable.py` and `lower/simple_gen.py` published
their leaf tables and their binding facts without ever calling it, so every
rule the file carries -- the no-op form convert, the coerce form-passthrough,
the BORROW-at-a-pointer-lifted-sink family -- was simply unapplied to the two
body kinds whose form handling is hardest to reason about. Both seams now call
`validate_resumable_body` / `validate_simple_gen_body`, each leaf table walked
at the flush right its own lowering grants, with units that fail if the call
sites are ablated. No emitted C++ changed.

**Wiring it in immediately falsified three rules**, each too narrow for a shape
only these bodies carry, and each widened with its reason stated at the rule:
the async return slot's borrow/trait lifts materialize a prvalue out of any
source form but deliberately keep the source's TYPE spelling, so the
result-type rows structurally could not see they are form-producing; a
plain-non-value BORROW form-convert is the `T&` -> reseatable `T*` address-of
lift, and BORROW spells BOTH of those, so its same-form same-type shape is the
lift working rather than a dead node; a frame slot write is a statement
position whose value flushes arg temps exactly like a `THIRAssign` value. That
a first exposure produced three findings is the reading worth keeping: the
rules were written against ordinary bodies and had been generalizing on faith.

**What this does NOT buy, stated so the cutover checklist is not read as
satisfied: the gate applied at these seams is strictly WEAKER than the one an
ordinary body gets, in three known ways.**

1. *Pooled maps are walked at the looser flush right.* `region_exprs` and
   `suspend_exprs` are keyed by expression `id()` and pool entries from
   several populate sites, of which exactly ONE per map grants temps -- the
   await operand and the sync for-head iterable. The other four (the
   bound-method receiver, the range bounds, the with-manager expression, the
   async-for iterable) are temp-free seams with no flush point in the
   skeleton, and an arg temp reaching one of them is not caught, because the
   walk cannot tell which site an entry came from.
2. *`THIRFrameSlotWrite` sits outside the pointer-lifted-sink rule*, which is
   the validator's highest-value check, while resumable frames route
   pointer-repr union locals -- precisely the guarded family -- through that
   node rather than through `THIRAssign`. Filed in BUGS.md; no repro was
   produced and a real mismatch may fail at the C++ build instead.
3. *Two of the three widened rules are keyed on shape or on a string*, not on
   a decision the producer records: the BORROW exemption on form + type alone,
   the async-return exemption on `coercion_name` membership. Both are correct
   today only because lowering does not construct the adjacent shape
   elsewhere. Filed in TODO.md.

So "async and generator bodies are validated now" is true of the WALK and not
of the rule set. The escape hatch is also unchanged: a validation failure is a
`THIRValidationError`, which is caught nowhere and escapes the per-body
fallback boundary as a compiler crash -- already tracked, but now reachable
from two more body kinds.

Three costs of this branch that no gate reports, recorded so a later reader
inherits them rather than re-deriving them:

- The crash surface above is the largest risk accepted here, not a footnote.
  Only `ThirUnsupported` degrades to the AST path; a `THIRValidationError`
  terminates the compile. Wiring the seams extends that reach to async and
  generator bodies, which is where the exotic seam shapes live. The preferred
  structural fix -- run the validator inside the per-body fallback boundary --
  closes three known triggers at once, and this is the change-set that widened
  the surface, so the "its own change-set" deferral is weaker now than when it
  was written.
- The stdlib render case compiles, links and runs about 1.37 MB of library C++
  on every non-cached exec, and re-keys on any toolchain or runtime-header
  change. Cheap next to the corpus, but it is a new fixed cost per cold run.
- Every future stdlib codegen change now churns a 110-file expected tree.
  The snapshot policy says to consult before regenerating existing output;
  that policy assumes a reviewable diff, and at this size the honest
  expectation is that the tree is read by its summary statistics -- file
  count, byte count, which modules moved -- not line by line. A change that
  moves ONE module's emission is reviewable; one that moves eighty is not,
  and the second case is the one to be suspicious of.

## Gate D4 teardown inventory, REWRITTEN 2026-09-01 (tree `0abcc8feb`)

Checklist item 7 said its inventory was "stale in every number and missing
about half the surface" and to rewrite it before executing. This is that
rewrite. Every figure below was measured at `0abcc8feb`; per the checklist
preamble a figure is valid only for the tree it names, so re-measure rather
than inherit these.

**The scope changed since the old inventory, and that is the substantive
correction.** The old one counted lines in the four body emitters. The
deletion's real cost is spread across five more surfaces -- the detectors,
the two committed gates, the harness, the migration tooling and the CI rows
-- and none of those were in it.

**Read this whole inventory knowing it is organized around the DELETION,
while the risk lives in the FLIP that precedes it.** The cutover is two
acts, not one: commit 1 makes `thir_codegen` the default and hands
authorship of every compile in the project to THIR for the first time
(it defaults to `False` today and rides the corpus as an overlay, with the
AST emit still feeding exec); commit 2 removes the emitter that used to be
the author. Everything catalogued below -- lines, imports, detectors, gates,
tooling -- belongs to commit 2 and is mechanical. The part that can produce
a wrong answer rather than a missing one belongs to commit 1, and it is
priced in section H, not here.

### A. The deletion proper

The four AST body emitters, deleted wholesale:

| module | lines |
|---|---|
| `codegen_cpp/expressions.py` | 7042 |
| `codegen_cpp/statements.py` | 5754 |
| `codegen_cpp/match.py` | 2486 |
| `codegen_cpp/builtins.py` | 477 |
| **total** | **15,759** |

For scale, `tpyc/thir/` (non-test) is 85,583 lines across 289 test files, so
the deletion removes about 18% as much code as the replacement already
carries.

### B. Wiring -- smaller than the old inventory implied

Nine import sites reference the four modules from outside them, and only
THREE are real:

- `codegen_cpp/generator.py:29-31` -- `BuiltinGenerator`, `ExpressionGenerator`,
  `StatementGenerator`. The actual construction.
- Six are `TYPE_CHECKING`-only annotations: `functions.py:54`, `records.py:52`,
  `gen_async.py:119-120`, `gen_generators.py:211-212`.

A repo-wide reverse-import scan is already committed and enforcing
(`test_no_layer_outside_the_body_emitters_imports_one`), so this list cannot
silently grow. **The cutover gate's OPEN set is empty** -- no skeleton call
survives routing -- which is the property that makes the deletion wholesale
rather than archaeology.

### C. Detectors that die, and what actually replaces each

This is the half the old inventory omitted entirely.

- **Corpus byte-diff -- SURVIVES, contrary to the standing framing.** Every
  unmarked case asserts THIR against its COMMITTED, AST-authored snapshot
  (`tests/conftest.py:1636`), not against a live AST emit. The snapshots are
  files on disk; deleting the emitter does not touch them. What degrades is
  AUTHORSHIP -- after cutover THIR authors new snapshots, so the oracle stops
  being independent for anything added later. The existing 3767 stay as an
  AST-authored baseline.
- **`move_audit.py` (155 lines) and `binding_audit.py` (273) -- DIE.** Both are
  dual-path joins whose AST-side recorder lives inside the deleted emitter.
  [2026-09-02: wrong for `binding_audit` -- its AST-side recorders live in
  `codegen_cpp/emit_prims.py`, `context.py`, `generator.py` and
  `gen_async.py`, four modules the cutover KEEPS. Only `move_audit`'s
  recorder (`codegen_cpp/expressions.py:630`) sits in a deleted module. Both
  joins still die, because the AST SIDE of the join stops being produced once
  nothing emits through the AST -- but the deletion's mechanical cost here is
  four kept modules to unwire, not zero.]
  Nothing replaces them. Their class is narrow but real: a verdict at a site
  whose render ignores it emits identical C++, so snapshots are blind to it.
  That makes them LATENT-bug detectors -- what they catch bites when a future
  render starts consulting the verdict, not today.
- **The interop overlay (`run_interop_thir_overlay`) -- DIES, and NEEDS NO
  SUCCESSOR. Its unique coverage was never checked before being treated as a
  blocker; when it was, there was none.** The overlay emits both sides itself
  because the CLI the ext-exec harness drives does not route THIR, and it
  turns source comments ON, which the harness's own snapshots do not carry --
  so it covers a comment-placement class the interop snapshot compare cannot
  see. That class is real (a THIR arm once emitted a leading match-arm comment
  the AST never wrote, fixed at 15 sites). **But it is emitted by the ordinary
  module codegen, which the whole `tests/cases` corpus already exercises with
  comments on and byte-diffs on every run -- and the corpus is what caught
  that defect.** What is unique to interop is the CPython glue emitter, and
  the glue is comment-INSENSITIVE: turning comments on changed 33 `.cpp` and
  23 `.hpp` files and ZERO of the 34 `_ext.cpp` files. So interop contributes
  nothing to the class the overlay protects.
  **The reason is structural, not a coincidence about comments: an interop
  case's module `.hpp`/`.cpp` is ORDINARY emission.** `@export` shows up in
  the glue, not in the module -- an exported function's body renders exactly
  as the same source would in a plain case, with no `PyObject`, no `Py_`, no
  export marking anywhere in it. Interop cases exist to test interop; regular
  emission is the corpus's job. An overlay that byte-diffs their module
  emission is checking the wrong layer, which is why it had nothing of its own
  to protect.
  An attempt to build a successor (author interop snapshots at
  `--emit-source`) was made and REVERTED 2026-09-01. It worked -- strictly
  additive, 56 files, +1387 lines, no code line changed -- but it solved a
  problem that did not exist, and it dragged in a real cost: the CLI has no
  knob for `comment_line_numbers`, so every comment carried a `.py` line
  number and a one-line insertion in a case source re-authored that file's
  entire comment set. **The lesson is the ordering: establish that a dying
  detector's coverage is UNIQUE before scoping a replacement for it.** The
  same question is worth re-asking of every other row in this list.
- **The error-path gate (`_assert_thir_raises_too` + `_diagnostic_author` +
  `AST_ONLY_DIAGNOSTICS` + `BODY_DIAGNOSTIC_FUNCTIONS`) -- DIES**, since it
  fires only from the handler for a `CodeGenError` raised by the AST emit.
  **Post-cutover `diag.txt` is sufficient for every case the corpus carries**,
  which is more than the earlier "most of the coverage" wording granted: the
  gate re-raises, so a codegen error already lands in the committed
  diagnostic, and wrong text, a wrong line and a missing error all fail that
  byte compare. Its two unique contributions -- the AST-vs-THIR comparison
  and the author classification -- both lose their subject entirely once one
  author remains. **What dies is coverage of shapes NO CASE CARRIES**, which
  is the same blind class the rest of this file tracks and not something the
  gate was ever going to cover. Do not restate this as "fully covered": the
  denominator is the corpus, not the language.
- **`dualgen.py` -- DIES.** Note it is NOT compiler code: it lives at
  `.claude/skills/tpy-thir-wave/scripts/dualgen.py` (1393 lines of skill
  scripts in total). It needs two authors, so the `/tpy-thir-wave` skill's
  adversarial half retires with the cutover. Worth stating because several
  defect classes in this ledger were found ONLY by dualgen, and no successor
  exists.

### D. Committed gates to retire or rewrite

- `codegen_cpp/test_cutover_gate.py` (559 lines) -- its whole subject is the
  skeleton/body boundary. Retires with the deletion.
- `tests/test_thir_stdlib_gate.py` -- ALREADY SPLIT, so the deletion is a
  whole-file removal with nothing to hand-pick out of it. What survives moved
  to `tests/test_stdlib_render_coverage.py`: the assertion keeping
  `harness/stdlib_render`'s import list equal to `lib/tpy` (without it the
  library's committed render silently narrows) and the snapshot-path
  injectivity check, plus the lib/tpy module scan both halves share. One
  survivor did NOT move and dies with the file: `_assert_every_family_reached`
  asserts every registered arg-table family is reached by SOME stdlib body,
  which is a THIR-only property with no AST in it. It rides the doomed
  compile, and the sweep that reaches all ~2800 stdlib bodies has no cheaper
  host -- rehome it onto a THIR-only compile of the same entry rather than
  letting the deletion take it.
  **DONE 2026-09-01**: it is its own test beside the render-coverage guards,
  compiling the same mega-entry and emitting through THIR alone, with the
  shared compile helper moved across so the two readers cannot drift. Ablated
  to confirm it is load-bearing on its own work -- with the emission loop
  removed, zero of twelve families read as reached. Costs one extra library
  compile; the two tests run in parallel. The per-cell coverage line moved
  with it, being the same question at a finer key.
- `tests/conftest.py` -- 457 THIR references. The overlay, both audit hookups,
  the ratchet, the classify/check-flip options and the marker machinery all
  go; `thir_codegen=True` stops being a test-only flip and becomes the
  default.

### E. Migration tooling -- 2664 lines that stop having a subject

Eight scripts under `scripts/thir_migration/` (`thir_reject_reach` 706,
`thir_matrix_reach` 674, `thir_diagnostic_sites` 525, `thir_stdlib_fallback`
300, `thir_ast_arm_residency` 196, `thir_scan` 121, `thir_stdlib_sites` 106,
`thir_resid_plugin` 36) -- that 2664 is the EIGHT-SCRIPT figure and predates
the directory below. Plus `asym/` (count the files and lines in the tree rather
than trusting a number here: the ones first written into this line went stale
inside the same branch, twice, as the tool was hardened), whose whole question
-- does THIR admit anything the AST refuses -- stops existing when one emitter
does; its README says so. Most measure fallback, reject sites or AST-arm
residency -- all concepts that stop existing. `thir_reject_reach` is the
exception worth keeping in some form: after cutover its unreached bucket
becomes the set of shapes that ICE, which is the post-cutover work queue.
[2026-09-02: the concrete successor is the per-site bins under
`scripts/thir_migration/review/` -- the census reaches 2 of 655 sites from
the program corpora, the read bins name 402 live ones with reproducers.]

Two CI rows key on the same dead concepts: `thir-stdlib` and
`thir-stdlib-fallback` in `ci/nightly/configs.json`.

### F. The real blockers, and they are few

- **ZERO cases now depend on a body-authored diagnostic** (`AST_ONLY_DIAGNOSTICS`
  is `frozenset()`), down from two on 2026-09-01. Note the KEY: this is a CASE
  count, not a raise site count, and it is not comparable to the site figures
  elsewhere in this file.
  **An empty set is NOT an empty blocker list, and reading it that way is the
  mistake this entry exists to prevent.** The ratchet can only see a
  diagnostic some CASE reaches; a body-authored diagnostic with no case is
  invisible to it by construction, and BUGS.md already carries at least one
  filed HIGH instance -- the rebind half of the polymorphic-rvalue-into-an-
  Optional-local reject, whose raise is reached from
  `codegen_cpp/statements.py::_gen_pointer_local_rebind`, a caller the
  deletion removes. Its message builder lives in a module the cutover keeps,
  which is exactly why a file-based reading misses it: classify by CALLER.
  That one is also NOT mechanical to re-home -- the AST gates it on a
  local-form membership whose THIR equivalent is an open question, and an
  ungated mirror would reject valid code. **So the honest state is: zero
  case-covered body diagnostics, at least one uncovered one, and no
  instrument that can enumerate the rest.**
  **The last clause is now FALSE and the enumeration is below** -- the site
  instrument grew the split, and the uncovered set is five, not unbounded.
  Read the successor entry before acting on this paragraph.
  `async/error_async_match_dyn_await`: the verdict now runs ahead of every
  admission gate in the resumable lowering, because the case's body is
  rejected first by an unrelated parameter gate and anything placed after
  admission is never reached.
  **That one is deliberately NOT a mirror, and the property is worth claiming
  rather than leaving implicit.** The AST tests whether an arm walk is in
  progress; THIR tests whether the match's own dispatch suspends. Those are
  different conditions, chosen that way because the AST's is filed as a
  defect -- it refuses a suspension-free polymorphic match that merely sits
  before an enclosing arm's first `await`. The divergence is benign in its
  direction (AST rejects, THIR accepts), so the cutover RETIRES that
  over-rejection as a side effect. A reader's default assumption for a
  re-homing is a faithful mirror, so a deliberate condition change needs
  saying out loud; the two re-homings on this branch used opposite
  methodologies, the other one consolidating both paths onto a single shared
  predicate.
  `iterators/error_gen_match_nested_narrowed_ptr_bind`: both paths now read
  one shared subject-stability predicate, so THIR raises the diagnostic
  instead of rejecting the body and letting the AST raise it.
  **That second one was written against an explicit instruction in a filed
  HIGH bug** ("the THIR mirror must not be written until it is fixed"),
  because the reasoning behind that instruction does not hold: nothing about
  a mirrored raise is permanent, and consolidating the disputed condition
  into ONE shared predicate makes the eventual fix a single-line edit rather
  than two spellings that can drift. The re-homing is also behaviourally
  inert -- identical message, identical committed diagnostic, no snapshot
  moved -- so only the author changed. Whether the condition itself is right
  remains open and is tracked in BUGS.md; if it resolves toward "wrong", the
  fix is to drop one conjunct from the shared predicate. Recording the
  override here rather than silently taking it, since the instruction it
  overrides is still worth its author's reasoning.
- **The five uncovered body diagnostics, enumerated and each accepted
  (2026-09-01).** `scripts/thir_migration/thir_diagnostic_sites.py` now splits
  body-authored raises into witnessed and unwitnessed, so the set the entry
  above called unbounded is five. Each was chased to a verdict; none blocks
  the deletion, and the reasons differ enough that a single blanket "internal
  guard" reading would have been wrong about one of them.
  - `match.py::_variant_index` "type not found in union" -- an internal drift
    check between two readings of one member list, as its own docstring says.
    Sema resolves and rejects arm names first. Dies with its file.
  - `match.py::_switch_literal_label` "cannot use literal in switch case
    label" -- reached only once the primitive-switch tier is selected, and
    THIR's tier gate positively admits int (and bool) literals only, so any
    other literal is declined before a label is rendered. Dies with its file.
  - `records.py::_extract_base_inits` "base __init__ call without resolved
    parent type" -- an internal invariant; sema resolves the parent for every
    base-init call. The enclosing helper survives the cutover but this one
    does not: it is on the AST leg of a ctor tail that routes through THIR
    otherwise, which is why the harness records it by NAME rather than by
    file. THIR's counterpart declines the same condition.
  - `statements.py::_resolve_cpp_type` "variable has no type annotation and
    no initializer" -- every parser construction of a variable declaration
    supplies one or the other; only a compile-time macro can build the
    neither-nor shape. Internal. Dies with its file.
  - `match.py::_gen_match_overload_specialized` "a yield/await inside a match
    on an @overload-specialized parameter is not yet supported" -- the only
    one of the five that refuses VALID PYTHON rather than asserting an
    invariant, and the only one worth the chase. **Its precondition cannot
    hold.** The overload-specialized emitter is reached from the function-def
    router, and generators and async functions divert to the resumable-frame
    emitter before that router, so the specialization map is never populated
    while an arm walk is in progress. Confirmed by emitting an overloaded
    generator: one union-typed factory, no specialization.
    **And the shape the guard names is broken one level earlier**, which is
    the finding, not the verdict: an overloaded generator emits a factory
    whose signature and whose body disagree about constness, and a call site
    resolved against the narrow stub cannot convert to it. Filed HIGH. So the
    guard was never the thing standing between that program and a miscompile.
- **The lowering gaps found by the 2026-09-01 fence sweep** (see TODO.md):
  9 shapes that compile and run correctly today only via fallback, and 7 that
  fold in front of AST renders that do not compile at all. After cutover the
  first group becomes hard errors and the second stops being reachable in its
  broken form. Neither blocks the deletion; both are the queue behind it.

### G. What is NOT a blocker, and was treated as one

[2026-09-02: the "cannot be enumerated by probing" claim below is
superseded -- all 655 raise sites were read and probed one slice per
reviewer: 402 BREAKS with a reproducer each, 141 DEAD, 108 UNSURE, 4
REFUSAL. See `docs/THIR_CUTOVER_REVIEW.md` Phase 0 and
`scripts/thir_migration/review/bins_*.json`.]

- **The partially-re-homed diagnostics -- audited 2026-09-01, one already
  known and the rest clean.** A raise whose message builder survives can
  still lose a CONDITION, because the site-level class asks whether the
  function is reachable, not whether each of its callers is. Splitting all
  fourteen such deliberate sites into their dead and live callers: every one
  has a live THIR caller, and only the polymorphic-rvalue-into-an-Optional-
  local reject has a dead caller with no counterpart -- the rebind half
  already filed in section F. The native C++ template expander looked like
  the risky one (two of its callers die) and is not: THIR's emitter reaches it
  from six distinct FUNCTIONS (eight call sites -- the instrument's caller key
  is per-function, so name which when quoting it). This is a READ, not a build, and it is the cheapest check
  on the list -- worth repeating if the bucket ever grows, because the one
  real instance did hide there.
- Snapshot regeneration -- and the framing above got this backwards, so read
  the D4 decision itself rather than this correction of it. Regeneration is
  not the hazard to avoid; it is commit 1's PROOF. D4 splits the cutover so
  that commit 1 flips authorship to THIR and regenerates, with the
  correctness criterion `git diff tests/cases` EMPTY -- a non-empty diff says
  the corpus was not ready and says exactly where. Commit 2 then deletes the
  emitter touching NO `expected/` file. The order is forced: the proof only
  works while the AST still exists to have authored the baseline. What must
  not happen is regeneration in the DELETION commit, where nothing would be
  left to check it against. Lifting `_thir_flag_conflict`
  (`tests/conftest.py`), which currently forbids THIR under
  `--update-snapshots`, is commit 1's first step.
- Driving the unreached reject set to zero first. Measured 2026-09-01 as not
  achievable by probing at any sane cost -- 58 probes reached 19 sites, only
  3 of them targeted, because a body folds at whichever fence it meets first
  and cannot be reliably aimed. After cutover a reject is an ordinary compile
  error, so the corpus regains reach and the set becomes discoverable for
  free. The cutover is a better instrument for that job than any sweep
  preceding it.

### H. What the cutover SHIPS BROKEN, accepted 2026-09-01

Recorded before the flip rather than after the first bug report, so the
breakage is a decision with reasoning attached and not a surprise. The
decision was taken knowing the list below is incomplete and cannot be
completed.

**The structural argument, stated with the denominator it actually has.** The
cutover changes one thing for a body that already routes -- nothing -- and
one thing for a body that FALLS BACK: it stops having anywhere to fall. So
**for shapes the corpus or the stdlib covers, the cutover cannot introduce
silent wrong code**: those are byte-diffed on every run, both have ZERO
fallback, and neither changes at all.

**Outside that set the guarantee does NOT hold, and an earlier draft of this
section claimed it did.** Two facts kill the absolute version. First, THIR
has never been the primary author anywhere: `thir_codegen` defaults to
`False`, the corpus's own primary emit is the AST path (that is the C++ that
feeds exec), and THIR rides as an overlay -- so commit 1 makes THIR the
author of record for the first time, and its render is verified exactly where
a byte-diff ran. Second, a THIR/AST divergence on valid code with NO corpus
witness is a DEMONSTRATED class, not a hypothetical: section C of this
inventory records defects found only by adversarial `dualgen`, including a
render emitting `::tpy::BigInt(2)` where the AST renders a bare `2`, and a
field-write lift where "byte-diff green over 3590 cases was not sufficient".
Those were divergences, not crashes.

So the honest form is: **loud crashes are the only NEW failure mode the
deletion adds, but the FLIP that precedes it promotes an author whose
out-of-corpus renders have unknown residual divergence.** That is accepted
because 3767 cases plus the whole library is a wide oracle, because the AST
is not a proven oracle either, and because nothing cheaper than the cutover
finds the residue -- not because the residue is zero. Anyone citing this
section for "the cutover is safe" must carry the denominator with it.

**Accepted, with the key named for each:**

- **9 witness SHAPES** (not cases, not sites) found by the 2026-09-01 fence
  sweep: valid Python that compiles and runs correctly today only because a
  fallback re-emits it through the AST. Each becomes a hard error. They are
  enumerated in TODO.md with reproducers. This is the worst category here,
  because the code is correct.
- **7 further shapes** from the same sweep that fold in front of AST renders
  which do not compile at all. These lose a bad-C++ error and gain a crash;
  no working program is affected.
- **At least ONE body-authored diagnostic with no case behind it** -- the
  rebind half of the polymorphic-rvalue-into-an-Optional-local reject, plus a
  second frame-scope shape in the same family, both filed HIGH in BUGS.md.
  These turn a clean diagnostic into a crash on INVALID code, which is
  strictly less bad than the witness category above and is why holding the
  cutover for them while accepting the witnesses would not have followed.
- **An unenumerated remainder** [ENUMERATED 2026-09-02: see the note at
  the head of section G and `docs/THIR_CUTOVER_REVIEW.md`]. In the
  RAISE-SITE key, 372 of 655 sites are
  never reached by anything we compile. Some fraction of those is reachable
  from user source and will surface as crashes. Nobody knows which, and the
  measurement that would settle it does not exist: probing reached 19 sites
  in 58 attempts, only 3 of them the intended target, because a body folds at
  whichever fence it meets first and cannot be reliably aimed.

**Why not wait until the list is empty.** It cannot be certified empty. The
ratchet only sees a diagnostic some CASE reaches, so a body-authored reject
with no case is invisible to it by construction; that is exactly how the
rebind one survived to be found by hand. Waiting for a bar nobody can measure
trades a definite cost -- delay, and spending the AST's value as an
independent oracle on nothing -- for an undefinable benefit.

**What the cutover BUYS on the same axis, which is the other half of the
trade:** after it, a reject is an ordinary compile error rather than an
invisible routing decision. The corpus regains reach, every one of the 372
becomes discoverable by ordinary use, and each arrives with a stack trace
naming its site. The cutover is a better instrument for finding this tail
than any sweep that precedes it.

**What would reverse this decision:** evidence that the out-of-corpus
divergence residue is LARGE, rather than evidence that it is non-zero -- the
latter is already established above and priced in. The cheapest probe of that
residue is the one instrument that has ever sampled it, so:

**SCHEDULED, and it has a closing window: run a large adversarial `dualgen`
sweep in the commit BEFORE the deletion.** `dualgen` needs two authors, so it
dies permanently at commit 2 and no successor exists -- section C says so.
The same "it rides a run that has to happen anyway" logic that justified
capturing the audit baselines applies here with strictly more force, because
the audits catch a latent class this inventory argues is bounded by the
snapshot regime, while `dualgen` is the only thing that has ever caught the
unbounded one. Scheduling nothing against it while scheduling a baseline for
the audits was backwards on value, and is corrected here.
## Cutover step 2 executed, 2026-09-02: THIR authors every body (`e5e9274af`)

The flip landed. `CodeGenOptions.thir_codegen` now defaults to True and the
compiler routes every module -- user code, `lib/tpy` and the stdlib alike --
through THIR; the per-module user-code gate, the `thir_all_modules` lift, the
`--thir-codegen` flag and the `TPY_THIR_CODEGEN` override are gone. An
explicit `thir_codegen=False` still emits through the AST, which the
dual-path helpers and the same-run stdlib oracle need until the body emitters
are deleted. The harness flipped with it: the primary emit (what exec builds,
what `--update-snapshots` writes) is THIR and the AST is the second opinion.
**Proof:** a full regeneration left every file under `tests/cases` and
`tests/interop` byte-identical.

**Audit baselines AT THE FLIP.** The Gate D4 inventory asked for exactly this
re-measurement at the deletion; these are the FLIP's figures, and the
deletion commit must quote its own.

| gate | key | reading |
|---|---|---|
| move-verdict join | joined NODES | 0 divergences / 1,012 |
| binding join | joined BODIES | 0 gaps / 11,972 |
| ratchet | bodies routed / cases | 13,277 / 3,772, zero fallback |
| interop | cases / bodies | 34 of 34, 285 bodies |
| suite | tests | 13,461 passed, 23 skipped |

### The final adversarial `dualgen` sweep

Ran on tree `1be2cf003`, the flip's parent (the flip changed no lowering and
no emission, so it applies to the flipped tree). FRONT END ONLY -- no C++
toolchain ran, so every verdict below is about EMITTED TEXT, and a claim that
a render "would not compile" is an inference, not a measurement.

Population 1 -- the 405 committed per-site reproducers under
`scripts/thir_migration/review/probes/`, x 3 widths:

| bucket | Int32 | Int64 | BigInt |
|---|---|---|---|
| AGREE | 6 | 6 | 5 |
| BOTH_REFUSE | 4 | 4 | 4 |
| DIVERGE | 1 | 1 | 1 |
| FALLBACK | 394 | 361 | 379 |
| FRONTEND_REFUSED | 0 | 33 | 16 |

Population 2 -- the 4,875 whole programs embedded in the THIR unit tests
(`probe_programs.py`'s `collect()`), x 3 widths, byte-diffed (which
`probe_programs.py` itself does not do, and it runs at one width):

| verdict | Int32 | Int64 | BigInt |
|---|---|---|---|
| BOTH_REFUSE | 2 | 3 | 2 |
| BREAKS_AT_CUTOVER | 485 | 443 | 510 |
| DIVERGE | 0 | 4 | 10 |
| DIVERGE_WITH_FALLBACK | 0 | 0 | 2 |
| FRONTEND_REFUSES | 2959 | 3108 | 3019 |
| ROUTES | 1428 | 1316 | 1331 |
| THIR_RAISES_PLAIN | 1 | 1 | 1 |
| *front-end accepted* | 1916 | 1767 | 1856 |

The break ratio is unchanged from the review's `program_verdicts.json`: 25%
(485/1916 here, 478/1901 there; +20 programs from the two intervening
commits' unit tests).

Population 3 -- the generated `match` matrix (`asym/matrix.py`), 672 programs
x 3 widths plus 5 controls per width:

| status | Int32 | Int64 | BigInt |
|---|---|---|---|
| BOTH_EMIT | 113 | 113 | 113 |
| BOTH_REFUSE | 5 | 4 | 4 |
| FRONTEND | 552 | 553 | 553 |
| THIR_FELL_BACK | 7 | 7 | 7 |

0 DIVERGE, 0 ASYMMETRY, 0 THIR_ONLY_REFUSES at every width -- reproducing the
committed `asym_run.log` exactly. **The zero is QUALIFIED by the
instrument's own control:** 2 of the 15 control runs came back FRONTEND
instead of BOTH_REFUSE (`error_async_match_dyn_await` at Int64 and BigInt,
where sema refuses before either emitter runs), so the population-3 zero is
validated at Int32 ONLY. Not new -- the committed log records the identical
failure at the review's tree.

**Four new divergences found; two fixed in this unit, two filed:**

- BUGS.md#thir-module-global-foreach-no-peephole -- `for a in sys.argv:`
  (another module's pointer-slot global) loses the native begin/end peephole
  under THIR; all three widths; quality, and THIR's spelling now ships.
- BUGS.md#thir-folded-wide-literal-drops-int64-cast -- a folded constant
  outside Int32 range loses its `static_cast<int64_t>`; Int64 only.
- BUGS.md#thir-int-methodarg-shift-not-folded -- `c.bump((1 << 33) + 1)` is
  folded by the AST, emitted as the checked-op chain by THIR. Filed as an
  Int64-only spelling difference; the review built and ran the repro and
  found the DEFAULT Int32 width silently panics at run time (`Int32
  overflow in multiplication`), so the row is wrong behaviour rather than
  quality. Re-rated HIGH. A fix (uniform constant folding in both authors)
  exists on the parked branch `thir-fold-wip`; it is not part of the
  switch, because it grew into a redesign of a spelling accident in the
  AST emitter the cutover deletes.
- The AST spelled a generator
  frame's inner tuple at the DEFAULT width against an `int32_t` target;
  Int64 and BigInt; THIR follows the annotation and is the right side.

**Re-confirmed, already on file:** the BigInt literal-wrapping class at eight
further sinks plus a reverse-direction walrus witness (added to that entry),
and the `THIRValidationError` escape on `a, b = f(M())`, filed verbatim
together with its `raise X(f(a))` sibling.

**Lesson:** a front-end-only sweep can DETECT a divergence but cannot rate
one -- it rated the method-arg row an Int64 spelling issue, and the row was
a silent default-width run-time panic, found only when the review built and
ran the repro. Text-level diffing cannot distinguish "two spellings of one
value" from "one folds, one traps", so a sweep's severity column is a
hypothesis until something is built and run.

**Verdict against section H's standing criterion** ("what would reverse this
decision: evidence that the residue is LARGE"): four new shapes out of about
5,300 programs at three widths is not large. The criterion is not met and the
cutover stands. Note that the review RECLASSIFIED one of the four -- the
method-arg fold row moved from an Int64 spelling difference to a silent
default-width run-time panic -- so the count held while the severity did
not; the verdict is about the residue's SIZE and is unaffected.

**The fold was an ACCIDENT of the render sites, on both authors.** Chasing
the method-arg row further showed integer constant folding was never a
policy either path held: each author folded wherever its own render happened
to thread a target type, so the two agreed by coincidence and disagreed
wherever the threading differed. The class is not exotic -- an ordinary
byte-size constant failed the C++ build, which rates HIGH.
A fix giving both authors ONE policy (`int_literals.const_fold_int_target`
deciding the target width for every constant position, 36 snapshot files
churned) was built and reviewed through six rounds, each finding a
neighbouring render site that assumed the old behaviour, and it is PARKED
on the unmerged branch `thir-fold-wip`, with its last review's findings
recorded in that branch's TODO. It is not part of the switch: the switch's
goal is to make THIR the author and delete the AST body emitters, and the
fold work had become a redesign of a spelling accident inside the emitter
the next unit deletes. The method-arg panic itself is filed HIGH
(`BUGS.md#thir-int-methodarg-shift-not-folded`).

**Lesson:** mirroring an accident position by position cost a full round and
produced a sibling table that was wrong in BOTH directions -- positions
listed as agreeing that did not, and positions listed as differing that
already matched. When the divergence is that one author's behaviour is a
by-product of where a value happens to be threaded, the repair is one shared
policy, not a per-position table; the table can only ever be as complete as
the enumeration behind it, and the enumeration is the thing the accident
makes untrustworthy.

## Cutover step 5 executed, 2026-09-03: the AST body emitters are deleted

The migration is over. THIR is the single sema->codegen boundary for every
body, `codegen_cpp` is the printer/skeleton layer, and a body THIR cannot
lower is a `ThirRejectError` naming the blocking construct and its line.
There is no second author and nothing to fall back to.

**What went** (line counts at the deletion's parent, `883448af66`):

| deleted | lines |
|---|---|
| `codegen_cpp/expressions.py` | 7,048 |
| `codegen_cpp/statements.py` | 5,754 |
| `codegen_cpp/match.py` | 2,477 |
| `codegen_cpp/builtins.py` | 477 |
| `move_audit.py` | 155 |
| `binding_audit.py` | 273 |
| `thir/fallback.py` (succeeded by the trimmed `thir/reject.py`) | 446 |
| `codegen_cpp/test_cutover_gate.py` | 559 |
| `thir/test_binding_audit.py` | 222 |
| `thir/test_thir_movable_set.py` (working-set half since restored) | 515 |
| `tests/test_thir_stdlib_gate.py` | 352 |
| `tests/test_thir_harness.py` | 528 |
| `scripts/thir_migration/` (8 scripts + 1 shell, minus `review/`) | 2,687 |
| `scripts/thir_migration/asym/` (2 scripts + README) | 568 |
| `.claude/skills/tpy-thir-wave/` (SKILL.md + 13 scripts) | 1,656 |

23,717 lines: 18,806 of compiler and test code, 4,911 of tooling.

Plus, inside surviving files: the `conftest.py` AST oracle pass, ratchet,
case dial, marker machinery and the seven `--thir-*` pytest options; the
interop overlay in `tests/test_interop_exec.py`; the error-path
diagnostic-author gate (`AST_ONLY_DIAGNOSTICS`, `_diagnostic_author`,
`_assert_both_paths_reject`); `CodeGenOptions.thir_codegen` /
`thir_strict`, the `--thir-strict` CLI and pytest flags and the per-case
`options.json` key; the reject TALLY, `NON_RATCHET_COMPONENTS`,
`ratchet_total` and the arm-residual census in `thir/fallback.py`; and the
two `ci/nightly` rows (`thir-stdlib`, `thir-stdlib-fallback`) with their
pins in `tests/test_nightly_ci.py`.

**What stayed, against the checklist's original wording.** Step 5 in
`docs/THIR_CUTOVER_REVIEW.md` listed `fallback.py`, `shape.py`, the
migration scripts and the nightly rows as one teardown; three of those four
were narrowed by the 2026-09-02 decisions and the review doc is corrected in
this commit.

- `thir/reject.py` is the trimmed successor of `thir/fallback.py`: it keeps
  `ThirUnsupported`, the reject-reason journal (`note` / `note_detail` /
  `begin_stmt`), the composed-tag helpers, the face journal bracket and the
  reject error builder. Only the tally died.
- `thir/shape.py` stays as a module; its per-run recording is gone.
  `thir/test_thir_shape.py` and `review/shapes_unit.py` both keep working.
- `thir/faces.py` stays, and its zero-witness report now prints
  unconditionally.
- `scripts/thir_migration/review/` stays in place as the post-cutover fix
  queue. Its three dual-author probes (`probe_fallback.py`, `probe_site.py`,
  `probe_programs.py`) are retired in place and marked so in its README;
  `inventory_sites.py`, `classify_tests.py` and `shapes_unit.py` still run.

**Detectors retired, and what replaced each.** Four of the five gates that
policed the two-author regime had no successor, because each one's subject
was the disagreement between two authors:

| detector | successor |
|---|---|
| move-verdict join (`move_audit.py`) | none -- the AST-side recorder lived inside `expressions.py` |
| binding-set join (`binding_audit.py`) | none -- its AST recorders lived in surviving files but were reached only from deleted callers |
| error-path gate (`AST_ONLY_DIAGNOSTICS`) | none -- it fires only when an AST emit raises |
| the fallback ratchet + case dial | subsumed: a body that does not lower is now a compile error, so a silent fallback is unrepresentable |
| corpus byte-diff | SURVIVES, against the committed `expected/` tree |

The byte-diff is the one that lives on, and what it lost is authorship
independence, not the check: every case still asserts its emitted C++
against a committed snapshot on disk, but those snapshots are no longer
regenerable from an oracle the migration does not own. The
detector-successor designs (M1, M2, B1) were never built; they were
insurance for a staged window that the 2026-09-02 decision closed.

**A correction to the Gate D4 inventory.** Its section C put both audits'
AST-side recorders inside the doomed modules. Only `move_audit`'s was
(`codegen_cpp/expressions.py:630`); `binding_audit`'s four recorders lived in
files the cutover KEEPS -- `emit_prims.py` (`begin_ast_body`), `context.py`
(`capture_ast`, inside `snapshot_local_scope`), `generator.py` and
`gen_async.py` (`end_ast_body`). The verdict (both die) was right; the
deletion edited four surviving files rather than dropping one module.

**The build-cache key changes once more.** `cli.py`'s options key carried
`"thir_strict"`; removing it misses every warm manifest one time. The flip
commit already paid the same cost for `"thir_codegen"`. Not a defect.

**Baselines at the deletion.** The flip's own audit figures are in the entry
above; both audits are deleted here, so these are their last readings and
nothing re-derives them.

Final full suite on the deletion tree (`a2839c7a7a`, 2026-09-03): 13,370
passed, 23 skipped, 3 expected failures (the three blocked examples in
the examples gate); 3,780 cases built and run; 13,419 bodies lowered
across 3,647 cases; faces 1,451 of 1,473 witnessed. Keys: tests, cases,
bodies, faces -- none comparable to another.
