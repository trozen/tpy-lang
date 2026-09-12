# THIR cutover review instruments (2026-09-01)

Artifacts and scripts behind `docs/THIR_CUTOVER_REVIEW.md`. Every figure in
that report was measured at tree `c8c9268ac`; re-run rather than reuse.

**This directory is the post-cutover fix queue.** The AST body emitters are
deleted, so every site binned `BREAKS` below names a shape that now fails to
compile with a `ThirRejectError`. The queue is the `BREAKS` rows of
`bins_*.json` (each with its reproducer under `probes/<slice>/`) plus the
`BREAKS_AT_CUTOVER` entries of `program_verdicts.json`. A row closes when a
lowering arm in `tpyc/thir/lower/` handles the shape and carries its three
units (routing pin, render pin, boundary pin).

Scripts (run from the repo root with `uv run python`):

- `inventory_sites.py OUT.json` -- every `ThirUnsupported(...)` construction
  site: file, line, enclosing function, reason literal.
- `reprobe.py OUT_DIR` -- **the queue's re-measurement.** Runs every
  BREAKS probe through the front end on the current tree and classifies it
  (REJECTS with the live tag / COMPILES / FRONTEND_ERROR / CRASH), joined
  with its bin row into `OUT_DIR/results.json`. Run it before choosing a
  batch; the bins are a one-time binning and rows close as arms land. Its
  output is not committed (40 seconds to regenerate); the figures below are
  the 2026-09-03 run.
- **The render oracle.** The deleted AST body emitter still exists at
  commit `e5e9274af` (an ancestor of master: the flip commit, where THIR
  authors by default and the AST is the fallback). `git worktree add
  <path> e5e9274af`, then `uv run tpy --dump-code file.py` THERE renders a
  rejecting body through the old emitter. A new lowering arm falls in one
  of four classes against it: (1) byte mirror -- the default, diff the
  comment-stripped `.cpp`/`.hpp` of every new case against the oracle
  before review; (2) the oracle render is ill-formed or dangling -- keep a
  located reject and file the shape in BUGS.md, never mirror it; (3) no
  oracle -- the AST crashed on the shape, so the exec run, CPython parity
  and the unit pins are the only check, and a sink matrix (local / return
  / field / method-arg / container element) is mandatory for a value-form
  row; (4) a valid oracle render deliberately changed -- say so in the
  commit and the case. Name the shared tables and predicates a row must
  reuse in the implementer brief (the op->dunder table, the runtime-view
  predicate, the registry's conversion spellings): parallel implementers
  otherwise grow private copies.
- `container_gates.py` -- **the container-family consolidation's ratchet.**
  AST call counts, per lowering file, of the eight container predicates, the
  family-enumeration disjunctions, `_f1_record` / `_f1_ref` /
  `is_value_type`, and `resolve_pending_container`. The container counts go
  down as gates move onto the reference-type axis; `resolve_pending_container`
  and the literal-construction sites are the two that legitimately stay, so a
  drop there is a regression, not progress. `--json` for a diffable dump.
- `probe_fallback.py`, `probe_site.py`, `probe_programs.py` -- **RETIRED.**
  Each emitted one program through both codegen paths and compared the
  outcomes, so all three stopped working when the AST body emitters were
  deleted. Kept unrun as the record of how the verdicts below were
  produced.

**Container-family consolidation (2026-09-05).** The five stages that moved the
lowering's admission gates off the builtin-container names and onto the value
form were ratcheted with `container_gates.py`, re-run after every commit.
Baseline (2026-09-04, master) -> the five stages -> the branch tip after the
ladder phase below (`container_gates.py` re-run at the tip, 2026-09-05):
container-predicate calls 738 -> 411 -> 411, family enumerations 177 -> 90 ->
90, `is_bytearray_type` 42 -> 11 -> 11, `_f1_record` 301 -> 279 -> 269,
`_f1_ref` 0 -> 25 -> 40, `_f1_container_ref` 0 -> 69 -> 59,
`_bytes_family_ref` 0 -> 9 -> 9, `resolve_pending_container` 43 -> 43 -> 43
(unchanged, as required -- pending resolution is one of the two decisions that
stay container-specific). The ladder phase moves only the axis columns: it
deletes duplicated ARMS, not container-name tests, so the two totals (411
container-predicate calls, 90 family enumerations) are the stages' figures
unchanged. The invariant the stages leave in force: a lowering gate names a
builtin container ONLY where the decision is literal construction (`[..]`,
`{..}`, comprehensions, the `make_*` renders) or pending-literal resolution;
every other admission keys on the value form
(`ValueForm.BORROW_REF`, through `_f1_ref` / `_f1_container_ref`), on
per-TypeDef facts (`cpp_formatter`, `param_cpp_formatter` /
`param_mut_cpp_formatter`, `subscript_borrows`), or on the resolved stub
method's `FunctionInfo`. The ~90 enumerations still counted are the residue,
filed as their own TODO entries. `reprobe.py` was byte-identical against the
pre-branch run at every checkpoint after stage 1 (314 REJECTS / 86 COMPILES;
stage 1 itself flipped two `Array`-slot rows, 316/84 -> 314/86, both of which
compile and run). **Population caveat:** the queue holds only the shapes that
were binned, so a byte-identical re-probe proves "nothing that compiled moved",
not "nothing flipped" -- every flip below was measured by hand against a
checkout of the pre-change lowering and then cased. 44 cases were added and 1
removed (a pinned reject that now routes):

- stage 1, `predicates.py` -- `array_span/array_reference_slots`,
  `calls/own_param_reference_types`, `async/async_own_container_return`,
  `async/error_async_empty_container_return`.
- stage 1, `checks.py` -- `array_span/array_optional_field_borrow`,
  `array_span/error_own_container_optional_decl`.
- stage 1, `expressions.py` -- `operators/select_reference_axis`,
  `operators/ternary_reference_axis`, `list/copy_literal_array`,
  `operators/error_ternary_mixed_category_receiver`.
- stage 1, `statements.py` -- `returns/return_container_source_axis`,
  `dict/dict_setitem_container_call`,
  `dict/error_dict_setitem_container_borrow_call`; removed
  `returns/error_return_bytearray_reassigned` (its "keeps rejecting like every
  container" claim measured FALSE on the base -- the `list` leg routed).
- stage 1, small files -- `generators/sgen_yield_container_param`,
  `generators/gen_container_frame_param`, `match/match_capture_array_field`
  and `inheritance/base_init_container_args`, each with an `error_` sibling,
  plus `async/async_container_frame_param` (its adjacent rejecting shape is
  the generator sibling's).
- stage 2, the element family -- `pointers/container_ptr_slot_rebind`,
  `records/container_field_write_rvalue`,
  `optional/optional_container_return_slot`, each with an `error_` sibling,
  plus new legs on `dict/dict_setitem_container_call`.
- stage 3, the argument sink -- `list/extend_container_field` +
  `list/error_container_field_concrete_slot`.
- stage 4, the bytearray residue -- `list/discarded_container_result`,
  `calls/stub_receiver_shapes`, `records/optional_container_field_write`,
  `records/container_ctor_field_init`, each with an `error_` sibling.
- the /tpy-review round-1 fixes -- `records/borrow_return_field_local`,
  `union/union_reference_axis_members`, `globals/container_global_receiver`,
  plus `bytes/error_duplicate_spelling_union`, `..._union_list` and
  `..._own_return` for the duplicate-C++-spelling union guard, and new legs on
  `records/optional_container_field_write`, `array_span/array_reference_slots`,
  `operators/select_reference_axis` and `calls/own_param_reference_types`.

**The ladder phase (2026-09-05).** The stages moved the ADMISSIONS onto the
axis; five ordered ladders still carried a second ARM per shape, a record one
above a container one. The method that landed them, the same every time: widen
the record admission to `_f1_ref`, make the record block FALL THROUGH where it
used to raise, absorb the container arm's source legs into the record ladder in
ladder order, then delete the container arm -- and before any of that, CLASSIFY
the recorded failure (reject / render diff / crash), because every one of the
five turned out to be an admission problem with zero render diffs. The gate is a
whole-corpus sweep: every `tests/cases/*/*/src/main.py` compiled front-end-only
in a base worktree and in the merged tree, sha256 over the WHOLE emitted tree
(`main.{cpp,hpp}` plus every emitted stdlib module) as the byte-diff. Results:

- **return** (below) -- 137 lines, 13 container source legs, 2 predicates and
  2 prescan slots deleted; 0 render diffs over 5543 cases; 2 flips.
- **if-expr** -- 15 lines, the container arm's 1 source leg (the NAME-arm
  block) and 1 duplicate face row; 0 diffs over 5543 cases. The record arm
  falls through for a container result (the literal / mixed / owned-call /
  comprehension ternaries keep routing to the generic tail), which is the
  whole difference between 42 rejects and none.
  The plain widening also admitted a SILENT COPY -- a container ctor arm beside
  an lvalue arm renders a prvalue `?:` (TPy printed 97, CPython 90) because
  `call_returns_cpp_ref` reads a builtin `__init__` as borrow-returning
  (`BUGS.md#void-method-reads-as-borrow-returning`); the arm now asks the shared
  `is_rvalue_source` classifier instead; the pair it declines is cased as
  `control_flow/error_ternary_container_elem_ctor_arm`, and the one shape the
  merge flips (a bytearray-element ternary, which now binds the element by
  reference) as `control_flow/ternary_container_elem_alias`.
- **field-write** -- 153 lines and 6 container source legs (`copy()`, the
  `copy(T(..))` peel, the literal at plain and `Optional` slots, the `[e] * n`
  repeat, the owned-rvalue call, the NAME row; the shared rvalue tail took the
  seventh difference by admitting both call kinds); `_classify_container`,
  `_lower_container_field` and `_ContainerFieldPlan` are gone for one
  `_RefFieldPlan` with PLAIN / OPT / OPT_TAIL slot kinds, and
  `_record_field_write_ok` is `_ref_field_write_ok` on `_f1_ref`; 0 render
  changes. Its classification named the residue in advance: the two ICEs were
  one missing fact, the bytes family's materialize decision,
  now `_object_source_materialize` keyed on the SOURCE being on the reference
  axis (so it is the object, with no view to copy) and read at both convert
  sites of the merged arm.
- **method-receiver gate** -- the RETURN half only:
  `_record_method_call_supported`'s result chain opens on `_stub_method_ret_ok`
  and eight duplicated rows died. The receiver/overload half stays two-family on
  purpose and it is measured, not assumed: an F1-record row added to
  `_METHOD_RECV_FAMILY_TABLE` renders a scalar-arg method byte-identically and
  rejects a record-argument one at `method.arg_shape`, because the stub arg sink
  has no record pass-through row.
- **method-argument sinks** (below) -- two sinks into one 103-cell `_ArgSink`.

Net: four container arms deleted (20 source legs) and two argument sinks
united, across five ladders. Two committed `diag.txt` files changed reject TAG
without changing verdict (the return ladder, where the arm that refuses them is
now the reference one) and one case's snapshot was regenerated because this
branch edits its source (`list/extend_container_field`); no other committed
`expected/` file outside the added and deleted cases moved.

One row of the merged field-write ladder is deliberately NOT on the axis and
says so in place: `_container_prvalue_field_write_ok` keeps `_f1_container_ref`
because its render threads `_ExprResultUse.STORAGE` where the reference rvalue
row threads the copy sink and pins the source type to the slot -- the pin that
keeps a subclass rvalue from slicing into a base-typed field. Containers have no
subclass, which is why the two rules can differ at all.

**The return ladder merged (2026-09-05), the first of the five.**
`_lower_stmt_dispatch` now has ONE return arm per slot family:
`_record_storage_return` / `_record_borrow_return` read `_f1_ref`, the ladder
carries the container SOURCE shapes the two container blocks used to own (bare
name, pointer-slot global, rebind-slot pointer local, the literals with their
element re-check, the repeat build, the comprehensions, the nested-container
element subscript), and `_container_storage_return` /
`_container_borrow_return` plus the `ret_container_*` prescan slots are gone.
What made it tractable was classifying the recorded 4041-test failure before
merging anything: swept over all 5543 corpus cases, that failure is 100%
REJECTS and 0 render diffs, and it comes from re-pointing the container slots
at the widened record helper (the container arm then claims `Own[Poll[T]]`) --
not from the record arms preempting. The merge is therefore an admission
change: absorb the source shapes, delete the container arms, keep the shared
render tail. Two shapes flip onto arms the record half already took (an lvalue
ternary at a borrow slot, a borrow-returning method call at an owning slot),
cased as `returns/return_container_ifexpr_borrow` and
`returns/return_container_borrow_method_own` with their `error_` siblings; two
committed `error_` cases changed reject TAG (not verdict) because the arm that
rejects them is now the reference one. **The second flip was withdrawn by the
tail review**: the record arm's copy-initialization from a borrow return is a
silent CPython divergence (TPy 2, CPython 3), so extending it to containers
extended the divergence. The container half took a located reject
(`return.container_borrow_call_needs_copy`,
`returns/warn_return_container_borrow_method_own`) until the sema root was
fixed later the same day: an `Own` slot now reads a borrow-returning call as a
borrowed source, so both halves take the same sema error and that reject is
gone. Six container return faces retired into
their record counterparts; the five with no counterpart (literal, repeat, comp,
ptr_local, borrow_global) stay.

**The method-argument sinks merged (2026-09-05).** The builtin-stub and
user-record arg listings are one `_ArgSink` (`_METHOD_ARG_SINK`, 103 cells:
the 49 stub rows, then the 54 a record signature alone reaches), both callers
re-pointed. The earlier measurement said "not a pair, both union orders break
through the PROLOGUE"; what broke was the prologue's SUBJECT, not the caller.
Its container-element legs keyed on `_elem_slot_type`, which peels `Own` if
present, so a record method's plain union param read as an element slot and
took a reject -- keyed on a genuine `Own[...]` slot (which is how the stubs
spell every insert slot the legs are about) the union is render-identical over
the whole corpus (3852 compiling cases, 0 diffs, 0 rejects gained). Of the earlier
verdict's two "ill-formed shapes both orders un-reject", only one was
ill-formed: `container_comp` carried the stub premise "every such slot is a
const ref" into a family where it is false, and the cell now states it
(`_x_comp_slot_const`, reading `const_borrow_params`), which subsumes the
record half's const-checked twin cell. The other routes correctly, builds and
matches CPython, so it is a leg of `list/extend_container_field` now.

**What the corpus could not see** was the `Own[T]` slot, and it took
hand-probing plus the THIR boundary pins to find: a stub's `Own[T]` is a
container INSERT whose const-ref overload binds an lvalue and copies, a user
method's is `own_param_t<T>` (`T&&`), which no lvalue binds. Twelve stub cells
admit an lvalue source at an Own slot, and with the union they decided record
params too -- `push(::tpy::__getitem__(src, i))` into a `T&&` slot, and eleven
more shapes like it. `own_lvalue` takes the disjunction of the two halves'
guards (`temps_ok or overload is None` -- with a plain flush gate there
`xs.append(b)` stops compiling), and the other eleven take
`_x_insert_own_slot`. Six admissions the union gains -- a str name, a bytes
literal, an f-string and a BigInt name at a record `Own` slot; a comprehension
at a `readonly` container slot; a tuple literal with a last-use record element
at a stub `Own[tuple]` slot -- are cased in `calls/method_arg_shared_rows`,
and the ill-formed one in `calls/error_method_own_arg_no_flush`. The whole
corpus sweep saw NONE of this: no case has any of these shapes.

The tail review found three more the sweep could not see. (1) The record-only
`method_ctor_rvalue` row reached a stub receiver, where `req.overload` is None,
and ICE'd; a stub's bare-`T` slot is a LOOKUP argument the runtime takes as
`const T&` (`list.remove` / `index` / `count`, `set.remove` / `discard`, the
dict key and default slots), so the row ROUTES there -- probed on five stub
spellings, all built and CPython-matched. (2) The prologue's view fence
rejected a str NAME at a user record's `Own[StrView]` param, which the base
routed: the fence is about the container INSERT, so it now rejects at a stub
slot only (`calls/method_arg_own_view_slot`; render byte-identical to the
base). The wrapper-union and plain-union legs were probed the same way over
name / field / element / call / literal sources and reject on both trees, so
only the view leg needed the fence. (3) `_x_insert_own_slot`'s own two
directions are cased at last:
`calls/error_method_own_slot_element_read` (the reject at a record slot) and
`calls/method_own_slot_insert_lvalue` (the admission at a stub slot).

**What the phase proved about its own instruments.** The corpus sweep is a
NECESSARY gate, not a sufficient one. Every ladder's sweep came back clean --
no new reject and no render diff over every compiling case (5543 at the return
and if-expr ladders, 5551 at the arg sinks; the return ladder moved two reject
TAGS on cases that still reject) -- and in two of them the merged tree as first
written still miscompiled: the if-expr ladder's silent-copy ternary and the arg
sinks' twelve `Own`-slot cells. Both were found by shape-space probes written
against the merged ladder's arms (23, 45 and 33 hand probes, each compiled in a
base worktree and byte-compared) and, for the arg sinks, by the THIR boundary
pins; the sweep's own verdict was clean in both cases. Read a green sweep as
"nothing the corpus exercises moved", never as "the merge is correct".


Data:

- `sites.json` -- the 655-site inventory.
- `bins_<slice>.json` (nine slices, 655 sites) -- per-site bins (BREAKS /
  DEAD / REFUSAL / INTERNAL / UNSURE) with evidence and, for BREAKS and
  REFUSAL, the probe program under `probes/<slice>/` (probes for DEAD and
  UNSURE sites were not kept). `probes/mine/` are the report author's
  re-verification probes.
- `reach.json` / `reach_census.log` -- the reject-reachability census over
  both populations, from a sweep tool deleted with the migration scripts.
- `asym_run.log` -- the asymmetry probe at three widths (control fails at
  int64/BigInt for one front-end-refused case; valid at int32).
- `shapes_unit.json`, `shapes_corpus.json` -- body-shape coverage of the
  routing unit programs against a whole-corpus shape dump taken before the
  cutover; the tally tool and `tpyc/thir/shape.py` went with the unit tests
  they measured, so these are records only.
- `p2_*.json` -- the seven phase 2 layer reviews' findings.
- `test_claims.json`, `program_verdicts.json` -- the phase 3 instruments'
  outputs.
- Re-probe figures go stale within a batch -- run `reprobe.py` rather than
  reading a number here. For the record: at master `da1a92ee15` (before
  batch 1) 388 of the 402 BREAKS rows rejected over 260 distinct live tags
  (252 of the 299 tag x site groups size one); batch 1 closed ten shapes,
  batch 2 (2026-09-04) 57 rows, batch 3 (2026-09-04) the nine failing NAMED
  programs (`examples/**` and `../tpy-examples`), eight of which compile now.
  Selection rule since batch 3: a failing named program outranks any tag
  count; probe them first.
- `BRIEF_*.md` -- the briefs the review agents worked from.
