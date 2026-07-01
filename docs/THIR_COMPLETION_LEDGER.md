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
  if-elif-else / while / range-for / aug-assign **(covered)** vs match / with /
  try-except / for-over-container / async-await / yield / comprehension /
  break-continue / del-global-raise-assert / chained-compare / and-or-not **(not)**.

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
| **Body statement + expression emit** -- `statements.py` `gen_body` per-statement path + `expressions.py` `gen_expr`, for routed callables | every statement shape + expression form across every callable kind | **PARTIAL** -- straight-line shapes over the F1/F2 + scalar/bool/double-float slice |
| **Form / conversion machinery** -- `context.py` `convert` + `CppForm`/`FormValue`, the ~12 detection predicates + ~22 local side-sets + the `RefType` wrapper (see `THIR_FORM_INVENTORY.md`) | **F-final**: the `Form` tag + `THIRFormConvert` subsume all form dispatch | **PARTIAL** -- F1/F2 forms carried; F3-F6 + RefType removal pending |

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
  builtin calls / ...), and `str`/`list`/`dict`/`tuple`/`union` MIL fields (F3+) +
  cross-module / native / generic records (their frontiers).
- Static / property / dunder-operator methods: **deferred (self-contained)** --
  separate emit paths.
- Generic-record methods (templated `self`): **blocked-on-F5** (generic-slot form).
- Nested-record methods: **deferred (self-contained)** -- the feed walks top-level
  records only.
- Non-value **call arguments** (the `gen_call_arg` coercion cascade: auto-move,
  view->owned, union/tuple lifts) + call / `copy()`-write optional sources:
  **deferred (self-contained frontier)** -- not isolable, must land whole.

### Form ladder (F) -- `IR_DESIGN.md` rung ladder + `THIR_FORM_INVENTORY.md`
- F1 (record locals + Optional read), F2 (reseatable pointer-locals + Optional
  write/return + move): **DONE**.
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
- F4 unions (`to_ptr_variant`/`to_value_variant`), F5 generic-slot,
  F6 str/bytes view-split, F-final (RefType removal + retirement): **not started.**
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
reads). Subscript follow-ons still on AST: Optional-field / tuple-field writes THROUGH a
subscript (`t[N].opt = None` -- the `_f2b`/`_f1_tuple` write gates still name-only). Beyond
the tuple family (separate frontiers): container subscript (`items[i]`, dynamic index/bounds),
value-tuple locals, standalone record/Optional-element binds / borrow returns, for-loops,
tuple-unpack. **Test-coverage follow-on:** the storage-tuple-alias receiver form (`a = h.pair;
a[N].field` read/write) is covered only by unit byte-diff, not a build+run corpus case -- add
one (the aliasing fn must stay THIR-routable, i.e. no `print()` inside it) for exec/cpy
coverage of the `.`-access + `optional_to_ptr` alias paths.
Uncovered shapes include: `match`, `with`, `try`/`except`/`finally`,
`for`-over-container, `async`/`await`, `yield` / generators, comprehensions,
`break`/`continue`, `del`/`global`/`nonlocal`/`raise`/`assert`, chained
comparisons, logical `and`/`or`/`not` (short-circuit). **Action:** continue enumerating +
driving these as a tracked axis before claiming `gen_body`/`gen_expr` deletion is near.

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

## Maintaining this ledger

- Flip cells / update statuses when a rung lands or a deferral is discovered.
- **Planned:** extend the `--thir-codegen` non-vacuity tally to report per-component
  AST-fallback coverage (how much of each component still falls back to AST), so the
  gap to each deletion is *measured*, not estimated. Today the tally reports only
  total routed bodies/cases (5092 bodies / 1223 cases as of increment 28).
