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
| **Ctor MIL emit** -- `records.py` `_extract_field_inits` / `_extract_base_inits` / `_get_non_init_stmts` + the inline ` : f(v)... {}` write | every ctor MIL field cell + body: scalar (M3a), record/Optional copy+move (M3b), ctor-call sources (M3b-rvalue), own-optional params, non-init body + demotion (M3c), base inits (M3d), and str/list/dict/tuple/union/bytes fields (F3+) + cross-module/native/generic records | **PARTIAL** -- scalar + record/Optional copy & move land; rest deferred |
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
  **DONE**; **deferred (self-contained):** M3b-rvalue (ctor-call sources),
  own-optional params, M3c (non-init body + the ~190-line demotion mirror), M3d
  (base inits / inheritance).
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
- F3 tuples, F4 unions (`to_ptr_variant`/`to_value_variant`), F5 generic-slot,
  F6 str/bytes view-split, F-final (RefType removal + retirement): **not started.**
- F2 carry-over deferred cells (container locals, cross-module/native/generic
  records, subscript sources, narrowing-needed `->` deref, name-alias/REF_ALIAS
  reseat sources): **blocked-on-F3+** or the relevant frontier; see the F1/F2 TODO
  cell (items C-G).

### Statement / expression shapes -- **NOT YET SYSTEMATICALLY ENUMERATED**
The form ladder and callable axis are tracked; the statement-shape axis is not.
Uncovered shapes include: `match`, `with`, `try`/`except`/`finally`,
`for`-over-container, `async`/`await`, `yield` / generators, comprehensions,
`break`/`continue`, `del`/`global`/`nonlocal`/`raise`/`assert`, chained
comparisons, logical `and`/`or`/`not` (short-circuit). **Action:** enumerate these
as a tracked axis before claiming `gen_body`/`gen_expr` deletion is near -- this
ledger surfaced that they are an untracked gap.

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
  total routed bodies/cases (4703 bodies / 1172 cases as of increment 16).
