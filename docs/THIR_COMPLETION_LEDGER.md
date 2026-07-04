# THIR Migration Completion Ledger

The **deletion roadmap** for the THIR codegen migration: the single place that
answers *"what stands between us and retiring the AST body/form codegen path, and
what is the status of each piece."*

Companion docs, different jobs:
- `IR_DESIGN.md` -- the design + the per-increment landing log + the F1->F-final
  rung ladder (what *has* landed).
- `THIR_FORM_INVENTORY.md` -- the form-dispatch spec (the borrow/storage machinery
  THIR must subsume).
- **This ledger** -- the completion/deletion tracker (what is *left* and what gates
  each deletion). **Sequence against this, not against routing %.**

## Why this exists

We have been sequencing by **routing ROI** (highest-value cells first). That is
right for *validation* -- it put F1/F2 under the whole-corpus byte-diff over real
code early. But ROI-sequencing **structurally diverges from completion**: the goal
is not "route N% of bodies," it is "delete the AST body/form codegen," and the two
part ways exactly in the low-ROI tail. "Circle back later" does not happen on its
own. This ledger keeps the tail visible and the end-state in view.

The real done-signal is per-component **deletion**, not routing count. Routing % is
a proxy that overstates progress in the tail.

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
  position, incr 33) / container-literal locals (list/Array/dict/set scalar
  elements, incr 34) / logical and-or-not + inline chained compares (bool slice,
  incr 36) / break-continue (else-free loops, incr 66) / del (trivial del-var +
  list/dict del-item, incr 67) / global (scalar globals, incr 68) / sync `with`
  (fresh-target slice + the emit-side finally frames, incr 78) **(covered)**
  vs match / try-except / for-over-container (generators) / async-await /
  yield / comprehension / nonlocal-raise + plain-assert messages **(not)**.

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
| **Ctor MIL emit** -- `records.py` `_extract_field_inits` / `_extract_base_inits` / `_get_non_init_stmts` + the inline ` : f(v)... {}` write | every ctor MIL field cell + body: scalar (M3a), record/Optional copy+move (M3b), ctor-call / param-field-read sources + own-optional params (M3b-rvalue), docstring/`pass` trivia body (M3c-trivia), non-init body + demotion (M3c-demotion), inheritance -- single + multi base + inherited-field writes (M3d), and str/list/dict/tuple/union/bytes fields (F3+) + cross-module/native/generic records | **PARTIAL** -- the whole scalar/record/Optional + body + inheritance surface lands (ctor self-contained tail complete); deferred cells are cross-axis-blocked -- non-record demoted-field-writes + non-record fields (F3+) + native/generic records |
| **Body statement + expression emit** -- `statements.py` `gen_body` per-statement path + `expressions.py` `gen_expr`, for routed callables | every statement shape + expression form across every callable kind | **PARTIAL** -- straight-line shapes over the F1/F2 + scalar (fixed-int/bool/both-float-widths/BigInt incl. the runtime-BigInt `.to_fixed_check` narrows, incr 73-76; enums of every flavor incl. truthiness/`.value`/`.name`, incr 77) + str (F6 S1-S5 + cross-type coercions) + bytes (F6 S6 values + the incr-46 tail: subscript/slices/iteration/concat/aug-assign) slice, incl. logical/chained-compare exprs and scalar/slice-object type-constructor calls |
| **Form / conversion machinery** -- `context.py` `convert` + `CppForm`/`FormValue`, the ~12 detection predicates + ~22 local side-sets + the `RefType` wrapper (see `THIR_FORM_INVENTORY.md`) | **F-final**: the `Form` tag + `THIRFormConvert` subsume all form dispatch | **PARTIAL** -- F1/F2 forms carried + F3 tuple read/write + the F6 str+bytes view->owned converts (S1/S6 -- both `view_to_owned_conv` family arms validated; reused at container-element slots by S5; extended to the cross-type str-family coercions in incr 42, the materializing arms lowering to the same THIRFormConvert) + the F4 union arms (U1 value + U2 pointer-variant, incr 49-50: `to_[const_]ptr_variant` / `to_value_variant<...>` carried, plus the structural form validator as the second gate) + the U3 narrowing extractions (incr 51: `THIRIsinstance` / `THIRNarrowAlias`, the `narrowed_vars` rename mirrored as lowering scope) + the U2 write-arm tail (incr 52: monostate write arms + union field-to-field copies) + the U4 narrowing tail (incr 53-54: `THIRAssert` persistent extractions + re-assert bump, while-isinstance loop-entry aliases, compound-`and` `THIRNarrowedRead` inline reads); + the call-arg lifts and temps (incr 56-65: `THIRUnionArgLift` ptr-variant member/None arg lifts incl. the deep-const slot spelling + `ptr_variant_to_const`, `THIRCtorCall`, `THIRArgTemp` value-union/record-rvalue/Own-slot/optional-ptr arg temps with the `move`/`addr_of` wraps, `THIRMove` last-use moves, `THIROptionalPtrArg` nullptr/&(name)/optional_to_ptr faces, record-arg/method-receiver/self-receiver pass-throughs); F4 remainder (readonly narrowing subjects), F5, the remaining F6 tail, and RefType removal pending |

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
  self-contained tail is COMPLETE.** Remaining ctor cells are cross-axis-blocked, not
  self-contained: demoted **record**-field writes (need the record body-write rung),
  `self.<record field>` read sources (MIL-ordering-sensitive), non-trivia body
  statements outside the statement-shape slice (match / with / try / for-container /
  method calls / ...; print + bare free-function calls now route, incr 32), and
  `str`/`list`/`dict`/`tuple`/`union` MIL fields (F3+) +
  cross-module / native / generic records (their frontiers).
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
  does not mirror, and their `return self` -> `return *this;` is outside the
  slice), `@readonly` statics (emitted with the readonly verdicts dropped),
  pointer-repr Optional/union getter returns (the `in_property_getter`
  return-the-field-storage arm; rejected by the general return gate),
  `@total_ordering`-synthesized comparison bodies (record compare operands,
  the pinned gen_expr_deref divergence).
- Generic-record methods (templated `self`): **blocked-on-F5** (generic-slot form).
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
  shares `_call_eligible`'s callee-shape head (`_plain_free_callee_ok`,
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
  reachable from `_expr_eligible`, where `narrowed` is not threaded, so
  the face must mirror rather than reject); readonly-slot
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
  `_const_exact_field_receiver_ok`; the AST drops the receiver's
  INFERRED constness there, the BUGS.md narrowed-Optional-receiver
  entry), `readonly[A | None]` sources at
  `_is_borrow_ptr_local` (the write/return `ptr_to_optional` gate does
  not unwrap readonly), narrowed Optional names into UNION slots
  (`_union_member_lift_arg` keys on the declared type), and the
  storage-field / call return sources above.
  **Still deferred**: the elif-chain-abandon + statement-expr temp
  relocation (ARCHITECTURAL -- design first), protocol slots (adapter
  machinery; typed-null spelling), method POINTER-variant union slots
  (the deep-const threading differs between the AST's own-record and
  inherited first-pass arg loops), coerce-wrapped lvalues into Own slots
  (the AST's rendered-identity `needs_copy` split), record field reads
  into Own slots (not `_expr_eligible`), `Own[T] | None` slots
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
  runtime behavior (the while-condition stale-snapshot hoist) or would
  add mechanism solely to reproduce a bug (the method mutated-ref rvalue
  arm). A rejected row cites its BUGS.md entry at the gate.
- **Non-`DEFAULT`-linkage call symbols** (`@native` / `@native_c` / `@export(binding="C")`
  callees): **deferred (self-contained)** -- `_call_eligible` gates `fi.linkage ==
  DEFAULT` (incr 32), so any caller of a native/export-C free function stays on the AST
  path permanently. THIR emits a bare `name(args)`; these need the `::`-qualified
  (`qualify_native_name`) / raw extern-C symbol the AST call-emit produces. This gate is a
  correctness fix today but a **completeness blocker**: the "delete AST call codegen"
  target is unreachable until THIR learns to emit qualified/extern-C call symbols.

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
  (`.to_fixed_check<int32_t>()` via `_bigint_index_disposition` +
  `_narrow_bigint_index`, one helper shared by gates and lowering),
  BigInt range counters, FixedInt-target aug-assigns fed BigInt values
  (`THIRBinOp.right_cast`), BigInt-arg `E(x)` (underlying-typed wrap),
  BigInt-keyed dict receivers, and the resolved-container-kind retype fix
  (ARRAY literal elements thread their scalar target; vector elements
  stay bare). Still-deferred narrow shapes (all literal-render mismatches,
  each gate-rejected explicitly): out-of-int32-range literal indices,
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
  corpus-witnessed). Still deferred: enum ITERATION (`for c in Color:`,
  the `enum_iterable` for-loop arm) and match-over-enum (the
  match-statement axis).
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
  params) with reads renamed via the lowering-scoped `lc.narrowed`
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
  gated by `_WalkState.persistent_narrowed` / `lc.narrow_subject_union`),
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
  print args, comparisons, `len(s)`, owned-str returns, same-type call args; the
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
  `view_key_target`), bytes keys/elements (S6), `Own[str]` method-arg slots
  (`xs.append(s)` / `st.add(s)` / `setdefault` defaults -- the owned-copy
  wrap and the copy-into-temp + `std::move(__tmp_N)` shapes), and container
  subscript WRITES (`d[k] = v`, `__setitem__` -- not a routed statement shape
  for ANY container family, the pre-existing parked cell).
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
  pure-@cpp_template expansion as the scalar ctors, gated by
  `_slice_ctor_call_eligible` over the shared `_template_init_call_fi`
  shape check; a `None` bound in the value-repr `Int32 | None` slot lowers
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
  call iterables (`for x in make_list():` -- `_call_eligible` widened with
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
view-typed keys+indices (the `.to_fixed_check` narrow / static-storage literals), `dict[K,
Any]` (`any_cast_or_panic`), record/Optional/container *element* results (borrow form),
narrowed-`Optional` receivers, non-name receivers (container-literal-init locals landed
incr 34), and the whole container-write side (`c[i] = v` / `+=` / `del` / slices). And **container iteration**
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
makes them pointer-locals -- `a = b` rebinds the alias; a value decl would silently copy)
and fixed-target binops with two IntLiteral-typed non-name operands (the AST's no-paren
literal-operand branch, position-dependent). The measured next co-blockers: **container
locals as call args** (`f(xs)` -- the borrow-ref pass looks like another gen_call_arg
pass-through for non-Own container params; keeps `main()`-shaped callers on AST), the
`Int32(0)` constructor-init, and the str/BigInt/f-string print args. Literal-locals cells
still deferred: `[0] * n` (`TpyListRepeat`), nested container literals, str/record/Optional
elements, empty-`Array` literals, container reassignment + aliasing (`ys = xs`), subscript
writes (`xs[i] = v`). **Container call args landed (incr 35,
`_container_pass_through_arg`): +11 bodies (5678 -> 5689)** -- a bare-name arg with a
builtin-container binding into a NON-Own concrete container param passes through as the bare
name in `_call_eligible` + `_method_call_eligible` (no new node/emit); `Own[container]`
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
operands and the chained-compare pair fold reuse `THIRBinOp`'s bare-operator arm. Deferred
by design: value-semantics `and`/`or` (non-bool result -> `_gen_logical_value` temp+ternary),
non-bool truthiness operands (incl. `not <int>`), the statement-expr chained arm (a
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
Rejected-by-design (AST): str/bytes/BigInt/Float32/Char conversions (incl. `float("nan")`'s
constexpr fold), enum / borrowing-view ctors, out-of-int32-range literals (the AST's
`static_cast` wrap), unary-minus FLOAT args (int negations fold since incr 45).
Method-call cells still deferred: `set`/`Span`/`Array` receivers (params not
admitted), str/bytes args (owned-copy conversion), `Own[record]` args (ownership
boundary -- the `gen_call_arg` temp facility), non-name receivers
(`self.items.append(...)`), `self.helper()` call sites,
consuming receivers, generic `inferred_type_args` methods. (User-record method
receivers + record NAME args landed with incr 56 -- see the Wave-1 entry below.)
**Bare numeric-literal call args + negated int literals landed (incr 45)**: a
bare FLOAT literal (FloatLiteralType) into a double param slot passes through
(`f(3, 1.5)` -- repr(v) bare on both paths; Float32 slots arrive
`float_literal_to_float32`-coerce-wrapped and stay AST, a BigInt slot's
`::tpy::BigInt(v)` wrap stays AST, inf/nan literals reject), for free calls
AND record-ctor rvalue sources (`P(1.5, ...)`; `_is_record_rvalue_source` also
gained the missing fi/arity gate -- an omitted-default ctor call no longer
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
`_record_pass_through_arg` + `_record_method_call_eligible`): bare-name
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
gate-rejected (the BUGS.md while-condition stale-snapshot entry honored).
Upcast Child->Parent NAME args pass bare; method ctor-rvalues into CONST
same-record slots inline via `THIRCtorCall` (`const_borrow_params`-keyed);
deep-const ptr-union slots spell const pointees + the
`ptr_variant_to_const` wrap (`THIRUnionArgLift.const_wrap`). Still
deferred: the Own copy+move arms, protocol / optional-ptr `&(__tmp)`,
covariant slots, elif-chain-abandon + statement-expr temp relocation, the
field-write flush position, readonly ref-slot ctor temps, `self.helper()`
sites, method union-slot args.
Deferred container-iteration cells: `dict[int, record]` key iteration (param not admitted --
its value read is a record borrow), `set`/`Span`/`Array` containers (params not yet admitted),
str/bytes-key dicts, `dict.items()`/tuple-unpack, non-name iterables (str-family FIELDS off
F1-record receivers route since incr 45 -- lvalues, the same `auto&` capture; subscript/call/
literal receivers stay deferred, a call result being an rvalue `auto` capture), generators / user iterators (the
`__iter__`/`__next__` fallback), hoisted loop vars, `for/else`, consuming/enum iteration. (Audit-note: the gate keys
`is_native_iterable` off the use-site type -- re-check it when narrowed-`Optional` containers
land, since a narrowed value-repr Optional could then reach it.) Beyond subscript/iteration (separate frontiers): value-tuple locals, standalone
record/Optional-element binds / borrow returns, tuple-unpack. **Test-coverage follow-on:** the storage-tuple-alias receiver form (`a = h.pair;
a[N].field` read/write) is covered only by unit byte-diff, not a build+run corpus case -- add
one (the aliasing fn must stay THIR-routable, i.e. no `print()` inside it) for exec/cpy
coverage of the `.`-access + `optional_to_ptr` alias paths.
**Routine statement shapes landed (the statement-shape session, incr 66-69):**
`break`/`continue` in else-free loops (bare `break;`/`continue;` -- else-loops
break via `goto __after_else_N`, gate-rejected; the gate threads an `in_loop`
flag through the body walk); `del` -- the trivially-destructible del-var face
(the AST's FIRST skip: comment only, no code -- the move-sink face for owning
locals reads alias bookkeeping the slice does not track, deferred) and
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
Uncovered shapes remaining: `match`, `try`/`except`/`finally`,
generator iterables, expression statements' deferred receiver/arg cells
(listed above), `async`/`await`, `yield` / generators, comprehensions,
`nonlocal`/`raise`, plain (non-isinstance) `assert` messages, chained
comparisons with non-simple intermediates (the statement-expr arm) and
value-semantics / non-bool-truthiness `and`/`or`/`not` (the bool slice landed
incr 36). **Action:** continue enumerating + driving these as a tracked axis
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
- **Planned:** extend the tally further to per-component AST-fallback coverage
  (how much of each component still falls back to AST), so the gap to each
  deletion is *measured*, not estimated. Today the tally reports total routed
  bodies/cases plus the per-face witness line above.
