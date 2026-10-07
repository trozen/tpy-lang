# Tuple completion plan

Status: OPEN. Scope decided 2026-09-21; U0, U1 and U4 done. U2's rows
land with U5: each is an element-form question, so they are the
acceptance cells of the elementwise-form unit on the lowering's slot
contract (decided 2026-09-29), not a batch of lowering arms on their own.
Measured on `04a797ddf2` (2026-09-21); the matrix cells re-run 2026-09-29
on `c7435b494b` plus the D6 fix (U2 D6). Every figure here is valid for that
tree only -- re-run the matrix before acting on a cell.

The tracked plan for making a tuple element behave as designed. `RELEASE_PLAN.md`
points here for the 0.7.0 tuple requirement; the entries themselves stay in
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
| borrow (ref) | ok alias | ok alias | ok copy+warn | ok copy+warn | ok alias | ok alias |
| value type | ok | ok | ok | ok | ok | ok |
| `int` (BigInt) | ok | ok | ok | ok | ok | ok |
| `str` | D3 | ok | ok | ok | D4 | ok |
| `bytes` | D3 | ok | ok | ok | D4 | ok |
| mixed Own+borrow | ok (D5) | ok | ok copy+warn | D1 | ok alias | D2 |

- **D1** -- a MIXED tuple LOCAL (an owned element beside a borrowed one,
  from a literal or a call) at an owning container insert (`xs.append(t)`)
  or a whole-tuple return warns its borrowed elements and then rejects
  (loud); the call source copies and warns like the scalar. The all-borrow
  row took the scalar's warning in U3 D1.
  The return of an ELEMENT of a list of such tuples has no borrow-form arm
  yet: the concrete function is refused
  (`BUGS.md#tuple-elem-return-borrow-form-rejects`) and its generic twin is
  ill-formed C++ (`BUGS.md#generic-tuple-elem-return-ill-formed`); a `min` /
  `max` / `next` result over them is the same gap.
- **D2** `BUGS.md#global-tuple-ref-storage-form` -- the MIXED tuple global
  (an owned element beside a borrowed one, from a call) copies its borrowed
  element SILENTLY; the all-borrow tuple global is a tuple of pointer slots
  and aliases (closed in U1).
- **D3 / D4** `BUGS.md#str-tuple-element-local-owned` and design-entry instance
  (1) -- a `str` / `bytes` element is owned storage at a param and a local where
  the scalar is a free view.
- **D5** `BUGS.md#consume-own-element-of-mixed-tuple` -- the mixed param
  takes the ownership transfer of its fully owned twin
  (`std::tuple<Box, Box*>&&`), so reading, writing through and unpacking it
  work and the matrix cells are `ok`; the element PLACES a name binding, a
  method call, an augmented assignment and `sink(p[0])` reject for both
  params alike (TODO: "Tuple parameter element places, owned and mixed
  together"), where `sink(p[0])` takes the conservative rule `return p[0]`
  already moves by (consume the element only where `p` is dead afterwards).
  Only reading another element after a partial consume needs MIR (U8).
- **D6** (closed in U2) -- `t[0]` on a `bytes` element rejected at every
  position; it now reads like the `str` element, and a local bound to it is an
  owned copy (`tests/cases/tuple/bytes_element_read`).

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

Ordering: global-position cells rank LAST in every unit, whatever their
severity (user decision 2026-09-23). A global divergence is filed against the
TODO design entry "Module scope is the body of __tpy_init" rather than fixed
per shape; the mixed tuple global (D2) waits on that entry.

### 0.7.0 (moved from 0.6.0 on 2026-09-25)

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
- [x] **U1 -- no silent tuple divergence.** Size: 2-3 weeks; the frame entries
  are the risk. Order: the three HIGH first. PARTIAL on `u1-tuple-meds`
  (2026-09-23), closed on `u1-close` (2026-09-25): the last two boxes,
  `list-of-zip-ref-element-silent-copy` and
  `frame-tuple-param-temp-element-dangles`, are done. Shipped
  limitations of the unit (valid Python now rejected, loud): an alias of an
  element of a rebound owning tuple frame slot (`BUGS.md#resumable-alias-identity`,
  including a rebind in a branch exclusive with the alias, chained paths and
  a nested-def `nonlocal` rebind); a
  walrus-bound name returned under a finally
  (`BUGS.md#walrus-local-return-under-finally-rejects`).
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
  - [x] the tuple-of-references global (was HIGH, D2), the ALL-BORROW
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
  - [ ] the MIXED tuple global (`BUGS.md#global-tuple-ref-storage-form`,
    D2's remaining half) -- moved out of U1 to the module-scope design
    entry (TODO: "Module scope is the body of __tpy_init"), with the
    module-level rebind of an owning tuple global after an unpack.
  - [x] the rebound borrow unpack target (was MED, entry removed) -- DONE on
    `u1-tuple-meds`: sema marks a borrowed target `is_rebound` (the loop
    body's rebinds for a for head, the function's for a standalone unpack)
    and the target binds `auto* x = &(element)`, the scalar alias's
    pointer local, instead of the `auto&&` alias a rebind wrote through; a
    rebound target marks its source mutable, as the scalar's does. Cell
    `x__rebind_unpack__T` differs -> same. Also silent before and fixed: the
    for head over `zip` / a generator, a branch, a loop and a closure. The
    recursive-wrapper element (`Tree` off `tuple[Tree[T], ...]`) rejects
    when rebound, as its scalar twin does (`error_unpack_wrapper_target_rebind`).
    Filed while probing: `BUGS.md#try-finally-borrow-unpack-target-predeclared`,
    `BUGS.md#method-rvalue-arg-at-mutable-ref-param` (both loud). Left
    open, not tuple-specific (the scalar alias diverges the same way):
    `BUGS.md#match-capture-reuses-alias-writes-through` (HIGH, silent) and
    `BUGS.md#nonlocal-rebind-overwrites-in-place`. The loop-head rebind the
    unpack target now admits is refused for the scalar loop variable:
    `BUGS.md#scalar-loop-var-rebind-rejects`.
  - [x] the tuple-literal member copy check (was four MED entries:
    subscript store, nested member at an `Own` argument, walrus, re-packed
    yield; all removed) -- DONE on `u1-tuple-meds`: one sema walker,
    `check_tuple_literal_members` (`tpyc/sema/compatibility.py`), gives each
    literal member the scalar's rule at its slot, keyed on the sink (return,
    argument, yield, local, field, container). Live and silent before, warned
    now: a `P | None` member stored by subscript (`d[0] = (a, b)`), every
    tuple-literal yield into an `Own` element (a borrowed param, a live owned
    local, the `(t[0], t[1])` re-pack, the `Own` half of a mixed tuple), a
    field re-pack of a live owning tuple, and `return (b, b)` into two `Own`
    elements (two copies where CPython returns one object twice). Stale on
    re-probe, and now declared in sema ahead of the lowering reject: the
    nested member at an `Own` argument / `append` and the nested walrus
    member (rows under U2); a nested `Own` element returned from a borrowed
    source now errors like the direct one. The two per-member predicates the
    field and container sinks kept apart are one. Cell `x__dict_value__T`
    gains the scalar twin's container copy warning. Filed:
    `BUGS.md#list-setitem-ref-tuple-false-readonly`. Deferred, latent: a
    NAMED nested member at an `Own` element of a return or a yield
    (`return (1, h.keep)` / `yield (1, inner)` into
    `tuple[int32, tuple[int32, Own[C]]]`) takes no member rule -- the walker
    recurses into a nested LITERAL only there; both shapes reject in the
    lowering today (`return.tuple_source`, `iter.call.generator`).
  - [x] a walrus-wrapped source (was HIGH, entry removed) -- DONE on
    `u1-tuple-meds`: `arrives_borrowed` answers for the walrus's value, so
    `(d := c)` at a field, a return into `Own`, an append and the scalar
    `return (d := c)` into `Own[C]` warn or error exactly like `c`; the
    bound name takes no last-use exemption (the render never moves)
  - [x] `list(zip(ns, cs))` into a list of tuples (was HIGH, entry removed)
    -- DONE on `u1-zip`: the container-copy warning asks the iterator's
    provenance per tuple element (a combinator, a dict view, a generator),
    so the copy out of named storage warns; `copy_iter()` acknowledges it
    and now also takes a container or view temporary
  - [x] the tuple return under a `finally` (was MED, entry removed) -- DONE on
    `u1-b3`: the finally-deferred return takes one capture per member -- an
    owned reference local at an `Own` element slot is captured by pointer
    and moved out after the chain, every other member is evaluated into a
    temporary before it, so the evaluation order is unchanged. Covers nested
    literals, `return t` of an owning tuple local, method, closure, `with`
    body, match arm, `@error_return` and async bodies. The entry's own shape
    (a borrowed element off a param) already aliased; the live shape was the
    owned element (TPy 1, CPython 2). Also closes the `(b, ys)` container
    member that rejected as "non-last-use" under a finally. The
    `Optional`-element return rejects at `return.slot_type` with or
    without a finally (unchanged).
  - [x] `BUGS.md#resumable-alias-identity` (frame) -- made LOUD on
    `u1-tuple-meds`; the real fix moved to U5. An alias of an element held
    by value in a tuple frame slot the body rebinds now rejects at
    `res.alias_bind` (literal, owning-call, async and mixed-slot-owned
    shapes; was a silent wrong value). SHIPPED LIMITATION: that is valid
    Python rejected -- `t = (A(1), A(2)); saved = t[1]; yield ...; t = ...`
    in a generator no longer compiles, where the record twin (`saved = r;
    r = A(8)`) does. The reject follows the path (a tuple reached through a
    list or another tuple counts) and only fires when a rebind can run after
    the alias; a rebind in a branch exclusive with the alias but later in
    program order still rejects.
  - Found while fixing the frame items, loud, queued elsewhere:
    `BUGS.md#frame-two-hop-tuple-alias-ice` (U7 loud tail) and
    `BUGS.md#tuple-param-rebind-from-readonly-tuple` (a U2 row).
  - [x] the frame tuple param bound from a temporary element (frame,
    dangling; was HIGH, entry removed) -- DONE on `u1-frame`: a tuple literal
    at a generator / coroutine call hoists each temporary element the frame
    would borrow (`A __tmp_1 = A(7); h(std::tuple<A*, A*>{&(__tmp_1),
    &(r)})`, a frame field inside a resumable body), the scalar argument's
    hoist applied per element (`tuple/frame_tuple_param_temp_element`). A
    temporary union element at such a call rejects
    (`BUGS.md#res-param-tuple-element-shapes`).
  - [x] the frame tuple element alias of a pointer element (frame, entry
    removed) -- DONE on `u1-tuple-meds`: an alias of a pointer-repr element
    in a resumable body re-addresses the pointee (`a = &((*std::get<0>(p)));`)
    instead of the element slot (`A**`). Wider than filed: the tuple param
    (readonly or written), the async twin, a method generator, a nested def
    capturing the alias, a borrow-form tuple frame local and the borrowed
    element of a mixed slot all failed the C++ build; all now match CPython
    (`generators/frame_tuple_elem_alias`).
  - [x] the dangling `str`/`bytes` unpack views (both entries removed) -- DONE
    on `u1-b2`. Reseat: an unpack target off a tuple NAME registers that
    tuple as its source storage, as `a = t[i]` does, so a rebind of the
    tuple (plain, narrowed-`Optional`, `= None`, a later loop iteration),
    a `del` of it, or a `nonlocal` rebind or `del` in a nested def --
    including a SIBLING closure the reading closure calls -- makes it own;
    the same reseats make the `int` element's target a copy rather than a
    `const&`. Hoist: a view declared outside the block it was bound in (loop
    body, if/elif/match arm, try body, except handler, loop `else`) stays a
    view only when every binding reads static storage or a name bound in
    that outer scope (a param, a global, a local bound before the block, a
    loop variable over such storage, a view that qualifies); anything else
    -- a call result, an alias of a block-local view, a nested subscript --
    owns (`own_hoisted_view`). Not closed, filed: a hoisted view of a live
    container mutated after the loop (the LOW leaked-loop-variable entry),
    a view declared BEFORE the block and rebound inside it
    (`view-local-escapes-loop-local-source`), a closure writing a viewed
    field (`closure-field-write-dangles-view`). Known precision gap: the
    loop rule decides at the first read after the loop, so a body-local
    source read after the view is copied rather than hoisted with it.
  - Settled by the instrument: `x, k = h.f` on a field tuple with a REFERENCE
    element takes the `tuple_to_pointer` lift and aliases (cell
    `x__unpack_field__T`), so it is not a U1 item. The whole-tuple copy seen
    earlier is the value-element render -- no divergence, but a hidden
    allocation for an owning `str` / `bytes` element, filed as
    `BUGS.md#field-tuple-unpack-copies-whole-tuple` (perf, U7).
- [ ] **U2 -- everyday shapes compile.** Loud rejects of ordinary Python,
  measured 2026-09-21 (reject tag in brackets). These rows are taken INSIDE
  U5, one per step, as its acceptance cells. Each row gets a snapshot case
  and keeps the adjacent `error_` pin. File a `BUGS.md` entry for a row only
  if the reject queue (`scripts/thir_migration/review/`) does not already
  carry it.
  - [x] unpack of a local tuple: `t = (b, 1); x, k = t` -- U5 step 1
    (`tests/cases/tuple/local_tuple_element_places`); the rebind after it
    (`x = Box(5)`) is not covered there
  - [x] element read off a container: `t = xs[0]` on `list[tuple[Box, int32]]`
    off a parameter list, and `a = t[0]` off any tuple name -- U5 step 1
  - [ ] `xs.append(t)` with a MIXED tuple local bound from a call (an owned
    element beside a borrowed one) [`method.arg_shape`, after the warning];
    the all-borrow local and parameter lower since U3 D1. A nested literal
    `xs.append((1, (2, c)))` / `sink((1, (2, c)))` at an `Own` tuple slot
    [`expr.tuple_literal`] (sema warns the nested copy)
  - [ ] `d["a"] = (b, 1)` [`setitem.family`]
  - [ ] an `Optional` element local: `t: tuple[Box | None, int32] = (b, 1)`
    [`decl.slot_type`]
  - [ ] walrus of a tuple call: `(t := g(b))[1]` [`expr.walrus`]; a nested
    literal `(u := (1, (2, c)))` [`expr.walrus`] (sema warns the nested copy)
  - [ ] a tuple local at an `Own[tuple[...]]` arg: `take(t)`
    [`call.arg_shape.own_tuple`]
  - [x] the `bytes` element read `t[0]` (D6), and the `bytes` global unpack /
    `print(G)` rejects behind it (branch `u4-d6`)
  - [ ] `BUGS.md#rvalue-ref-tuple-unpack-address-of-rvalue` -- `a, b = stack.pop()`
  - [x] yield re-packing an owning local: `yield (t[0], t[1])` -- compiles
    (a local literal or a call source) and warns the element copy since the
    tuple-literal member walker; moving the element at the root's last use
    is a perf item, not a reject
  - [ ] an element alias off a tuple local: `a = t[0]` [`decl.slot_type`],
    and `a = p[0]; a = q` off a borrow-tuple param [`decl.slot_type`] where
    the scalar `a = p; a = q` compiles (found 2026-09-22)
  - [ ] a tuple-unpack for head over pre-bound names: `k, v = ...` then
    `for k, v in make_pairs()` [`tuple.reused_target`]
  - [ ] an ENUM element beside a reference element: `return (b, c)` into
    `-> tuple[Box, Color]` [`return.slot_type`] and `b, c = mk(Color.Blue)`
    off `-> tuple[Own[Box], Color]` [`stmt.tuple_unpack`], where the `int32`
    twin compiles (found 2026-09-23)
  - [ ] an owning tuple off a METHOD: `t = h.meth()` for
    `-> tuple[Own[Box], int32]` [`method.ret_type`], the free twin compiles
- [ ] **U3 -- policy decisions, then the flips.** Each is small once decided;
  D1 is decided and done, the rest are not. Present each on its own, leading
  with the generated code.
  - [x] D1 tier (`tuple-own-warn-tier`): every tuple-element `Own` slot,
    return and argument, direct and nested, literal or name, takes the
    scalar's copy warning; a non-copyable element keeps the error. It also
    decided the borrow-returning-call member (warns). `tests/cases/tuple/
    own_element_borrowed_copies` holds a section per position; a tuple local
    bound from a borrow-returning call now warns at every slot. Left loud:
    the call-bound mixed local at an insert (D1 above), a list-literal local
    copied into `Own[list]` element slots (`ys = [1, 2]; return (ys, ys)`,
    `BUGS.md#array-literal-in-own-tuple-return`), an Optional member
    (`BUGS.md#optional-member-own-copy-no-render`), `yield t`
    (`BUGS.md#yield-tuple-name-own-element`) and a name nested in a returned
    literal (`BUGS.md#nested-tuple-name-own-return`); a global name copies
    unwarned (`BUGS.md#global-tuple-name-own-return-unwarned`).
  - [ ] TODO: "Warn at the mixed-tuple module GLOBAL as a stopgap" -- only
    worth taking if D2's full fix slips out of 0.7.0.
  - [ ] The `Optional`-wrapped mixed return ABI (`tuple[Own[A], B] | None`),
    carved out of the design entry's step (b).
- [x] **U4 -- the mixed-param diagnostics.** Landed 2026-10-01 in the
  `tuple-u4` squash; item 1 was superseded by giving the mixed param
  the ownership-transfer ABI.
  - [x] `BUGS.md#consume-own-element-of-mixed-tuple` -- the `p[0].n = 99`
    build failure and the unlowered unpack. Done by giving the mixed param
    the ownership-transfer ABI of its fully owned twin
    (`std::tuple<Box, Box*>&&`, `const Box*` when the body does not write
    through the borrowed element): writes, `mut(p[0])`, the unpack at the
    last use, `return p[0]` into `-> Own[Box]` (a move: the return is the
    tuple's last use) and generator / async frames compile
    (`tuple/mixed_own_param_writes`); the element places that still reject
    do so for both params (`tuple/error_mixed_own_param_element_*`).
  - [x] The unconsumed-`Own` warning per element: an unpacked owned-tuple
    param's dropped element warns, a mixed param warns as its twin does, and
    a move-bound local's consume credits its `Own` param
    (`tuple/param_own_tuple_never_consumed_warn`).
  - [x] The frame alias reject (`BUGS.md#resumable-alias-identity`) says the
    generic "not yet supported (res.alias_bind)"; give it a located
    diagnostic naming the limitation and the workaround that compiles for
    every path (alias after the last rebind). Taking the element by value
    with `copy()` is documented, not suggested: it does not compile yet off
    a mixed slot, a nested tuple or a tuple reached through a container or
    a field (`BUGS.md#tuple-elem-copy-mixed-or-list-rejects`). Done in
    the `tuple-u4` squash (2026-10-01).

- [ ] **U5 -- tuples through the two runtime helpers** (user-approved
  2026-10-07, replacing the "one elementwise form question" design, whose
  attempt on branch `tuple-u3` grew four modules for three tuple shapes and
  is frozen under tag `keep/tuple-u3-20261006`, not to be landed). The
  rule: the runtime already converts a tuple element by element for any
  source form -- a borrowing place renders `::tpy::tuple_to_pointer<Dest>(
  src)` (or nothing when the C++ types match), an owning place renders
  `::tpy::tuple_to_storage<Dest>(src)` with `std::move(src)` at a last use.
  Per place, the tuple-specific source lists in that place's gate are
  DELETED and the layout question is asked of the one existing fact
  (`_borrow_tuple_local_type` / `_subscript_yields_borrow_ptr`); no new
  module, node fact or family enum. One place per short branch off master,
  landing between steps; acceptance per step: the step's pair programs
  (same meaning, one compiled, its twin rejected) compile and match CPython
  after a mutation through the binding, the differential sweep over `int`
  and `int32` elements shows no `ok -> reject` and no render change, the
  full suite is green, and the place's code is shorter.
  - [x] step 0 -- the HIGH fix alone: a consuming store of a mixed tuple
    moves its owned elements only (`tuple_to_storage<S>(std::move(p))`,
    never the pointee-moving lift); `tests/cases/tuple/
    mixed_storage_lift_moves_owned_only`.
  - [x] step 1 -- locals: the element alias (`a = t[N]`) admits any tuple
    NAME and takes its deref from the one arrow decision; the unpack's
    "already borrow form" row reads the layout fact instead of "is a
    param"; the `auto&&` storage alias no longer refuses a const source
    (its const-ness joins `const_storage_tuple_locals`); a literal local
    holding a fresh record INLINE beside a borrowed one (`t = (Box(1), b)`,
    `std::tuple<Box, Box*>`) records that layout for its element reads and
    is aliased, not copied, by `u = t` -- `t[0].n` off it was ill-formed
    C++ before. Sema twin (`sema/context.py readonly_reaches`, asked at
    every projection site): readonly
    projects through a tuple element that holds a reference (`xs[0][1].n =
    v` off a `readonly[list[tuple[int32, Box]]]` is refused like the
    record element; a readonly tuple unpacks with readonly targets). Local
    sweep (sources x `int`/`int32`/mixed/owned x uses, 544 programs): 374
    -> 524 compile, no `ok -> reject`, the 12 render changes are the
    mixed-literal reads above, every changed program matches CPython;
    left: a storage
    alias declared inside a loop body and the alias of an OWNED element
    (step 4). `tests/cases/tuple/local_tuple_element_places`,
    `error_readonly_tuple_element_write`.
  - [ ] step 2 -- container stores: `xs.append(t)` / `d[k] = (b, 1)` /
    nested literals at an owning element slot.
  - [ ] step 3 -- arguments and returns; carries the `-> tuple[...] | None`
    return (`std::optional<R>`) and its escape fix from `tuple-u3` as one
    small commit.
  - [ ] step 4 -- the owned-param element places and the mixed global (D2).
  Also owned here, after the steps: the real fix of
  `BUGS.md#resumable-alias-identity` (the per-rebind-site element ownership
  verdict, decided in sema); the shared root `tpyc/sema/alias_rebind.py`
  admits neither tuple locals nor multi-hop loans (two U1 stopgaps and
  `BUGS.md#finally-mutate-then-rebind-return`,
  `BUGS.md#nested-list-literal-alias-rebind-clobbers` come from it); the
  container FIELD as a borrow-tuple element (`btuple.elem_container_field`).

### After 0.7.0

- [ ] **U6 -- `str` / `bytes` view elements** (D3, D4). A view element at a
  tuple param and a view-safe local, as the scalar has. ABI change with wide
  snapshot churn, and it needs the loan a view inside a tuple takes on its
  source, which is not tracked today. Do it ON U5, not before it: alone it is
  a third per-site form rule. Also closes the `str`-view printed-tuple extra
  copy. Size: 1-2 weeks. It is the tuple part of the one view rule
  (decided 2026-10-01, branch `str-bytes-view-rule`: a view only from
  static storage, a parameter, or a name the function binds -- a tuple
  NAME's element is such a source for both families, an rvalue tuple's
  element is moved out); what U6 adds is the view FORM of an element
  inside a tuple parameter or local, which needs the loan a view inside a
  tuple takes on its source. TODO.md "`str` / `bytes` views: MIR
  precision over the one view rule" holds the rule's record.
- [ ] **U7 -- the loud tail.** The remaining loud tuple entries in `BUGS.md`
  (about 85 on 2026-09-21, most LOW or exotic), taken as ordinary batch work
  by user-facing frequency. Size: 2-4 weeks. Two the matrix pins:
  `BUGS.md#nocopy-ref-tuple-local-rejected` (`t = (b, 1)` with a `@nocopy`
  `b` is refused where the copyable twin binds `&b` and the scalar aliases;
  cell `nocopy__local__T`) and `BUGS.md#field-tuple-unpack-copies-whole-tuple`.

### Gated on MIR

- [ ] **U8 -- the general partial move out of a tuple param** (D5): reading
  another element after one was consumed (`sink(p[0])` then `p[1]`) on a
  mixed or fully owned param needs per-place move tracking (`BUGS.md`
  "General destructive move-out of aggregate members"). Both params already
  take the per-element ownership-transfer ABI, `return p[0]` moves its
  element, and `sink(p[0])` where `p` is dead afterwards and the other
  element places are not MIR-gated (TODO: "Tuple parameter element places,
  owned and mixed together").

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
are ticked, the 0.7.0 tuple requirement is met.
