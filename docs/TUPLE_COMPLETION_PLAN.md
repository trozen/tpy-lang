# Tuple completion plan

Status: OPEN. Scope decided 2026-09-21; U0 done, U1 is next.
Measured on `04a797ddf2` (2026-09-21). Every figure here is valid for that
tree only -- re-run the matrix before acting on a cell.

The tracked plan for making a tuple element behave as designed. `RELEASE_PLAN.md`
points here for the 0.6.0 tuple requirement; the entries themselves stay in
`BUGS.md` / `TODO.md`, and this file only orders them, sizes them and records
which release each unit belongs to.

## The rule

`docs/PITFALLS.md`, `tuple-equals-scalar`: element `i` of `tuple[T1, T2, ...]`
at position P behaves exactly as `Ti` would as a standalone value at P -- same
aliasing, copying, ownership, storage form and view-ness. Same SEMANTICS, not
the same C++ spelling: a reference element keeps the `T*` stand-in, because a
tuple of references is not assignable. The design entry is TODO: "A tuple
element does NOT behave like the same type would as a singleton at that
position".

## Where it stands

Element form x position, a singleton program and a tuple program per cell, run
under TPy and CPython (mutate-and-observe for the reference rows, the emitted
C++ form for `str` / `bytes`). `ok` = the tuple element behaves as the
singleton does. The table below is a READING of the committed instrument, not
the instrument: the cells, their verdicts, warnings, emitted forms and
CPython parity live in
`scripts/thir_migration/review/tuple_element_matrix.expected.json`, and
`python scripts/thir_migration/review/tuple_element_matrix.py --report` prints
the raw grid.

| element form | param | return | field | collection | local | global |
|---|---|---|---|---|---|---|
| `Own[T]` (ref) | ok | ok | ok (both reject) | ok (both reject) | ok (both reject) | ok (both reject) |
| borrow (ref) | ok alias | ok alias | ok copy+warn | D1 | ok alias | ok alias |
| value type | ok | ok | ok | ok | ok | ok |
| `int` (BigInt) | ok | ok | ok | ok | ok | ok |
| `str` | D3 | ok | ok | ok | D4 | ok |
| `bytes` | D3, D6 | ok (D6) | ok (D6) | ok (D6) | D4, D6 | D6 |
| mixed Own+borrow | D5 | ok | ok copy+warn | D1 | ok alias | D2 |

- **D1** `BUGS.md#borrowed-tuple-at-own-call-arg` -- a borrowed tuple at an
  owning container insert: literal source is a hard error, a local source an
  unsupported-construct reject, only the call source warns like the scalar.
- **D2** `BUGS.md#global-tuple-ref-storage-form` -- the MIXED tuple global
  (an owned element beside a borrowed one, from a call) copies its borrowed
  element SILENTLY; the all-borrow tuple global is a tuple of pointer slots
  and aliases (closed in U1).
- **D3 / D4** `BUGS.md#str-tuple-element-local-owned` and design-entry instance
  (1) -- a `str` / `bytes` element is owned storage at a param and a local where
  the scalar is a free view.
- **D5** `BUGS.md#consume-own-element-of-mixed-tuple` -- the `Own` element of a
  mixed param cannot be consumed (loud).
- **D6** `BUGS.md#bytes-tuple-element-subscript-read-rejects` -- `t[0]` on a
  `bytes` element rejects at every position (loud).

Positions outside that grid, same method. Aliases correctly: unpack of a call
(and through a relay), swap, a two-reference return, a method returning a field
tuple, a generator yield (unpacked or whole), `for a, b in xs`, `enumerate`,
`zip`, `dict.items()`, a closure read, a `return` under a mutating `finally`.
Wrong or missing: see units 1 and 2.

## Units

Each unit is its own branch through `/tpy-fix-bug` or `/tpy-add-feature`. A
unit starts by RE-PROBING its entries: of the five entries re-measured on
2026-09-21, three had moved since they were written. A unit is done when its
matrix cells and its entries' repros match CPython (or warn), its entries are
deleted from `BUGS.md`, and the box here is ticked with the merge commit.

### 0.6.0

- [x] **U0 -- the matrix as an instrument.** DONE 2026-09-21:
  `scripts/thir_migration/review/tuple_element_matrix.py` (163 cells) with its
  committed table, gated by `tests/test_tuple_element_matrix.py`. It follows
  the property-position sweep rather than adding cases: a reject cell would
  need an `error_` case each, and a cell that diverges from CPython cannot
  share a cpy-parity case with the cells that do not. The pytest gate is
  toolchain-free (~7 s) and reads VERDICT and WARNINGS only, so no branch
  outside this plan pays more than that; the `nocopy` row (the `borrow` row
  over a `@nocopy` record) puts part of the silent-copy class into that
  verdict column. The emitted C++ FORM of the probe slot and the CPython
  parity are recorded but not gated -- the form is what makes a silent copy
  visible without a build (`extern std::tuple<Cell, int32_t>` beside the
  scalar's `Cell*`), the parity is the `--exec` half (about ten minutes for
  all cells, `--exec-moved` for the moved ones).
  **How a later unit uses it:** re-probe at the start (`--update
  --exec-moved`, so the table matches the tree), fix, rerun the same at the
  end, and the commit's diff of the table IS the unit's claim -- every moved
  cell is either the fix or a regression, and the commit body says which. A
  cell whose parity reads "?" in `--report` is stale and owed a build.
- [ ] **U1 -- no silent tuple divergence.** Size: 2-3 weeks; the frame entries
  are the risk. Order: the three HIGH first.
  - [x] the live-source owned-call unpack copy (was HIGH, entry removed) --
    DONE on `u1-tuple-silent`: sema tags an `Own` element target owned only when
    the unpack CONSUMES its source (`is_auto_move_use`, the scalar decl's
    verdict); a live source borrows through the storage lift. Cell
    `x__unpack_live_own__T` differs -> same. Also closed the owned-tuple
    PARAM's silent copy at a non-last-use unpack (unfiled; same cause) and
    turned the `@nocopy` live unpack from an error into the alias the scalar
    twin takes. Sibling filed: `BUGS.md#for-head-owned-yield-copies-before-move`.
  - [x] the relayed owned-call unpack (was HIGH, entry removed) -- STALE on
    re-probe: direct and relayed renders are identical, a `@nocopy` element
    compiles and matches CPython; the dual-emit-era "correct" AST render it
    cited (`tuple_to_pointer` over a call temporary) was the wrong side.
    Pinned as a section of `tuple/unpack_own_source_live_borrows`.
  - [x] `BUGS.md#global-tuple-ref-storage-form` (was HIGH, D2), the ALL-BORROW
    half -- DONE on `u1-tuple-silent`: a tuple of references at a global is
    the tuple of pointer slots (`std::tuple<Box*, Box*>`); a fresh literal
    element is `Own` on the binding (storage, as the local); the form is a
    type-level fact (`TupleType.takes_borrow_slot_as_global`) so importing
    modules agree; rebind from a function body gets the scalar's error. Cell
    `borrow__global__T` differs -> same. The MIXED global (`mixed__global__T`)
    stays storage and SILENT: entry narrowed to it and lowered to MED; its
    stopgap warning is the U3 decision, its full form -- a pointer at
    per-assignment static backing, the scalar global's own shape -- is the
    remaining work and also unlocks the module-level REBIND of an owning
    tuple global (`tg = mk(7)` after `gx, gk = tg`), refused at sema
    meanwhile. Shipped position gaps from this unit: a module-level unpack of
    a tuple-of-references global (`ga, gb = pair_g` at module scope) and of
    an IMPORTED one from a function body both still reject at
    `stmt.tuple_unpack` (the source arm admits same-module readonly globals);
    a later module-level literal with a FRESH element into a borrow-slot
    tuple global (`P = (copy(W), 2)`) rejects where the scalar allocates a
    static slot -- the per-assignment backing item above.
  - [ ] `BUGS.md#borrow-unpack-target-rebind-writes-through` -- confirmed live
    2026-09-21, cell `x__rebind_unpack__T` (prints 6 where CPython prints 1)
  - [ ] `BUGS.md#tuple-literal-subscript-store-skips-copy-check`
  - [ ] `BUGS.md#own-tuple-arg-nested-member-silent-copy`
  - [ ] `BUGS.md#walrus-tuple-binding-not-copy-checked`
  - [ ] `BUGS.md#tuple-return-under-finally-captures-early`
  - [ ] `BUGS.md#tuple-repack-yield-silent-elem-copy` (frame)
  - [ ] `BUGS.md#resumable-alias-identity` (frame)
  - [ ] `BUGS.md#frame-tuple-param-elem-alias-slot-address` (frame)
  - [ ] `BUGS.md#tuple-unpack-view-outlives-reseat` (dangling view)
  - [ ] `BUGS.md#loop-body-view-unpack-target-dangles` (dangling view)
  - Settled by the instrument: `x, k = h.f` on a field tuple with a REFERENCE
    element takes the `tuple_to_pointer` lift and aliases (cell
    `x__unpack_field__T`), so it is not a U1 item. The whole-tuple copy seen
    earlier is the value-element render -- no divergence, but a hidden
    allocation for an owning `str` / `bytes` element, filed as
    `BUGS.md#field-tuple-unpack-copies-whole-tuple` (perf, U7).
- [ ] **U2 -- everyday shapes compile.** Loud rejects of ordinary Python,
  measured 2026-09-21 (reject tag in brackets). Size: 1-2 weeks, batch-style;
  each row is a lowering arm with a snapshot case and keeps the adjacent
  `error_` pin. File a `BUGS.md` entry for a row only if the reject queue
  (`scripts/thir_migration/review/`) does not already carry it.
  - [ ] unpack of a local tuple: `t = (b, 1); x, k = t` [`stmt.tuple_unpack`];
    the rebind after it (`x = Box(5)`) sits behind the same reject
  - [ ] element read off a container: `t = xs[0]` on `list[tuple[Box, int32]]`
    [`decl.slot_type`]
  - [ ] `xs.append(t)` with a local or param tuple [`method.arg_shape`]
  - [ ] `d["a"] = (b, 1)` [`setitem.family`]
  - [ ] an `Optional` element local: `t: tuple[Box | None, int32] = (b, 1)`
    [`decl.slot_type`]
  - [ ] walrus of a tuple call: `(t := g(b))[1]` [`expr.walrus`]
  - [ ] a tuple local at an `Own[tuple[...]]` arg: `take(t)`
    [`call.arg_shape.own_tuple`]
  - [ ] `BUGS.md#bytes-tuple-element-subscript-read-rejects` (D6), and the
    `bytes` global unpack / `print(G)` rejects behind it
  - [ ] `BUGS.md#rvalue-ref-tuple-unpack-address-of-rvalue` -- `a, b = stack.pop()`
  - [ ] yield re-packing an owning local: `yield (t[0], t[1])` (a located
    error today; decide with `tuple-repack-yield-silent-elem-copy`)
- [ ] **U3 -- policy decisions, then the flips.** Each is small once decided;
  none is decided. Present each on its own, leading with the generated code.
  - [ ] D1 tier: the per-element arg check follows the scalar WARN tier
    (`BUGS.md#borrowed-tuple-at-own-call-arg`; eight `error_` cases stop
    erroring, `@nocopy` sources keep erroring).
  - [ ] TODO: "Decide ONE policy for a borrow-returning call at a
    tuple-element `Own[T]` slot" -- errors today where the scalar warns.
  - [ ] TODO: "Warn at the mixed-tuple module GLOBAL as a stopgap" -- only
    worth taking if D2's full fix slips out of 0.6.0.
  - [ ] The `Optional`-wrapped mixed return ABI (`tuple[Own[A], B] | None`),
    carved out of the design entry's step (b).
- [ ] **U4 -- the mixed-param diagnostics.** The consume itself is MIR work
  (U8); what 0.6.0 owes is that the limitation is SAID. Size: under a day.
  - [ ] `BUGS.md#consume-own-element-of-mixed-tuple` -- replace the generic
    unlowered-shape message with a located diagnostic naming the remedy (take
    the owned element as its own `Own[T]` parameter), pinned by an `error_`
    case for the PARAM flavour; the `p[0].n = 99` build failure gets the same.
  - [ ] `BUGS.md#unconsumed-own-warning-whole-tuple-granular`

### 0.7.0

- [ ] **U5 -- one elementwise form question** (design entry step (c)). The
  structural fix: "what form does element `i` take at position P" is decided
  once, as a THIR fact, and every position consumes it -- instead of each
  element kind re-deriving its form at each site, which is why the matrix
  drifts. Prerequisites: TODO: "Make the tuple RENDER 3-valued instead of
  stacking booleans over it" and TODO: "Sema mirrors codegen's tuple-render
  pair at a different breadth". Needs `/tpy-add-feature`. Size: 2-3 weeks.
  Also owns the container FIELD as a borrow-tuple element: the bind is the
  record's (`T*`), the READER of a container element is what is missing
  (`btuple.elem_container_field` in the lowering).
- [ ] **U6 -- `str` / `bytes` view elements** (D3, D4). A view element at a
  tuple param and a view-safe local, as the scalar has. ABI change with wide
  snapshot churn, and it needs the loan a view inside a tuple takes on its
  source, which is not tracked today. Do it ON U5, not before it: alone it is
  a third per-site form rule. Also closes the `str`-view printed-tuple extra
  copy. Size: 1-2 weeks.
- [ ] **U7 -- the loud tail.** The remaining loud tuple entries in `BUGS.md`
  (about 85 on 2026-09-21, most LOW or exotic), taken as ordinary batch work
  by user-facing frequency. Size: 2-4 weeks. Two the matrix pins:
  `BUGS.md#nocopy-ref-tuple-local-rejected` (`t = (b, 1)` with a `@nocopy`
  `b` is refused where the copyable twin binds `&b` and the scalar aliases;
  cell `nocopy__local__T`) and `BUGS.md#field-tuple-unpack-copies-whole-tuple`.

### Gated on MIR

- [ ] **U8 -- per-element ownership at a mixed tuple param** (D5): the
  per-element borrow/own param ABI and per-place partial move-out. Already
  `deferred: MIR` in `BUGS.md` ("Return/param asymmetry for a MIXED
  owned+borrow tuple", "General destructive move-out of aggregate members").

## Out of scope here

- Tuple-literal membership evaluating lazily where CPython builds the tuple
  first: evaluation order is a postponed language decision, so no single site
  of that class is fixed.
- A reference-type local hoisted out of a branch that aliases a branch-local
  source: the scalar form dangles the same way, so it belongs to the borrow /
  provenance family, not to this plan.
- `namedtuple`, variadic tuples, `match` sequence patterns, list unpacking:
  features, tracked in `TODO.md`.

## Keeping this current

Tick a box with the merge commit when a unit lands, and delete the `BUGS.md`
entries it closes. A defect found while working a unit becomes a line in that
unit (and a `BUGS.md` entry), never a silent widening. When the matrix is
re-run, replace the table and its date rather than appending to it. When U0-U4
are ticked, the 0.6.0 tuple requirement is met.
