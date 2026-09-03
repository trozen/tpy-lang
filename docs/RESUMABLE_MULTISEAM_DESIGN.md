# Resumable region/loop multi-seam -- design (THIR migration, wave: res.region + res.pseudo_stmt)

DESIGN-ONLY document. No code was changed. Follows /tpy-add-feature: scope
assessment -> current-state map -> alternatives -> recommendation -> open
questions. All file:line references are against branch `thir-wave2`
(worktree tpy-t1, master a7d44ebf2).

---

## 1. Scope assessment: ARCHITECTURAL

Evidence:

- The change spans both sides of the sema->codegen boundary: the THIR
  lowering (`tpyc/thir/lower/resumable.py`), the THIR node set + emitter
  (`tpyc/thir/nodes.py:2010` `THIRResumableBody`, `tpyc/thir/emit.py:2920`
  `ResumableLeafEmitter`), and the 4.2k-line AST-side skeleton
  (`tpyc/codegen_cpp/gen_async.py`) at ~6 new delegation sites plus one new
  emission entry point (`gen_coro_finally_top_def`, gen_async.py:1523).
- It removes four whole-body gates (`res.region`, `res.pseudo_stmt`,
  `res.for_await`, `res.finally`) whose admission interacts with the
  pending-return machinery and the finally-chain replay. (The seam contract
  this used to cite lived in the deleted AST statement emitter; the
  constraint it recorded -- a cell lifting either gate must revisit the
  seam -- still holds.)
- It surfaces one genuine lowering-order problem (declaration registration
  vs BB-id order inside regions, section 2.6) that needs a design decision,
  not just an arm widening.
- Estimated unlock is the largest single remaining resumable lever
  (~103-140 fallback bodies: `res.pseudo_stmt` ~76 + `res.region` ~27 +
  the `res.finally` / `res.for_await` tails; counts from the 2026-07-14
  whole-corpus drilldown, pre-wave-3. Those instruments are gone with the
  cutover; re-measure against the reject sites in
  `scripts/thir_migration/review/bins_*.json` instead).
- It must land as multiple cells in dependency order (section 4), not one
  change.

Per the feature discipline this requires user approval of the design
before implementation.

---

## 2. Current-state map

### 2.1 The leaf-seam model (what already works)

The settled decision (docs/IR_DESIGN.md:1201-1215): the resumable
state-machine SKELETON -- CFG build, frame struct, case labels, region
replay, suspend/resume plumbing -- stays shared machinery for both paths,
like signatures; only user-source LEAVES route through THIR, per-body
all-or-nothing.

Concretely, `lower_resumable` (tpyc/thir/lower/resumable.py:180) walks the
CFG cached on the function (`gen_async.py:1763` via
`rcfg.resumable_state(func).cfg`) and returns a `THIRResumableBody`
(nodes.py:2010) holding six id()-keyed maps:

| map | keyed by | consumed at (gen_async.py) |
|---|---|---|
| `leaves` | id(TpyStmt) in `bb.stmts` / RaiseT | `_walk_inline` 3386-3388, 3406-3408 |
| `conds` | id(Branch.cond) | `_walk_inline` 3428-3429 |
| `await_args` | id(await operand call) | `_emplace_args` 3933-3935 |
| `return_values` | id(TpyReturn) | `_async_return_value_cpp` statements.py:3886-3895 |
| `yield_values` | id(TpyYield) | `_emit_generator_yield` 4097-4098 |
| `suspend_exprs` | id(operand / receiver expr) | `_suspend_expr_cpp` 3944-3946 |

`ResumableLeafEmitter` (emit.py:2920) does the lookups; a missing key is a
hard error (lowering and seam must agree, emit.py:2947-2952). The emitter
is installed for the duration of `_emit_state_machine` via
`_thir_leaf_scope` (gen_async.py:1781, 1852-1859).

### 2.2 How the CFG expands the pseudo-statements

The builder (`tpyc/codegen_cpp/resumable_cfg.py`) decomposes suspending
compounds into BBs and injects four SYNTHETIC leaf statements plus two
synthetic-payload Yield flavors:

- `AsyncForIterSetup` (resumable_cfg.py:431): head of every CFG-decomposed
  for-loop. Sync loops (`_build_for`, :1015) get one per loop plus an
  `AsyncForAdvance` TERMINATOR (:393) on the cond BB; async-for
  (`_build_async_for`, :1068) gets the `is_async=True` flavor plus a Yield
  whose `AwaitPayload.async_for_uid` is set (:1139-1149) and a synthesized
  `TryRegion`/`ExceptRegion` catching StopAsyncIteration into a `TpyBreak`
  handler (:1115-1171).
- `WithEnter` (:500): head of a sync `with` whose body suspends
  (`_build_with`, :1383); pushes a `WithRegion` (:323) per item.
- `AsyncWithSetup` (:514): head of `async with` (`_build_async_with`,
  :1444); synthesizes AENTER/AEXIT Yields (`AwaitPayload.async_with_kind`,
  :174-183, :1526-1545, :1596-1623) and reuses the CFG-based-finally
  machinery (TryRegion with `captured_exc_field` + `FinallyRegion` +
  `AsyncFinallyExit`).
- `AsyncFinallyExit` (:449): the single replay site of a CFG-based finally
  (`_build_try`, :1191; appended at :1359-1364): pending-return check +
  saved-exception rethrow. Emitted by `_emit_async_finally_exit`
  (gen_async.py:3532-3616) -- pure skeleton, ZERO user-expression renders.

Regions: `TryRegion` (:234), `FinallyRegion` (:272), `ExceptRegion` (:295),
`WithRegion` (:323). Every BB carries `region_stack` (:554), and the
emitter replays it as nested C++ `try` wraps per case (`_emit_case`,
gen_async.py:2745-2833), with catch clauses from
`_emit_try_region_catches` (:3092) / `_emit_with_region_catches` (:3042)
and normal-exit cleanup from `_emit_exit_region_finallies` (:3281).
Helper-based finallies (no await inside the finally body) become member
functions emitted by `gen_coro_finally_top_def` (:1523-1543).

Note: regions are not exclusively about suspensions -- a suspension-free
`if`/`try`/`with` inside a decomposed loop is force-decomposed when it
contains a `break`/`continue` targeting that loop
(resumable_cfg.py:808-834), so admitting regions also admits those.

### 2.3 Where the gates reject today (exact sites)

All in `tpyc/thir/lower/resumable.py`, inside `_lower_resumable`'s
"CFG shape classification" loop (:305-332):

```
306  for bb in cfg.blocks.values():
307      if bb.region_stack:
308          return _reject("res.region")
309      if bb.entry_narrowings:
310          return _reject("res.narrowed_resume")
311      for stmt in bb.stmts:
312          if isinstance(stmt, (rcfg.AsyncForIterSetup, rcfg.WithEnter,
313                               rcfg.AsyncWithSetup, rcfg.AsyncFinallyExit)):
314              return _reject("res.pseudo_stmt")
315      t = bb.terminator
316      if isinstance(t, rcfg.AsyncForAdvance):
317          return _reject("res.for_await")
318      if isinstance(t, rcfg.MatchDispatch):
319          return _reject("res.match")
```

plus, earlier:

```
302  if cfg.finally_helpers:
303      return _reject("res.finally")
```

and in `_payload_reject` (:148-150) the defensive twin that becomes live
once the gates above lift:

```
148  if payload.async_with_kind is not None or payload.async_for_uid is not None:
149      # Unreachable behind the region/pseudo-stmt gates; defensive.
150      return "res.await_synthetic"
```

Gate order is first-reject: a sync for-with-await hits `res.pseudo_stmt`
at its iter-init BB; an async-for usually the same (its iter-init BB is
created before the region push); pure try/except-around-await hits
`res.region`. The tags are therefore lossy across each other -- one more
reason the counts (76/27) must be re-measured per cell.

### 2.4 The complete inventory of user-expression renders inside the rejected territory

This is the crux. Almost everything the skeleton emits for regions and
pseudo-statements is structural (frame-field names, emplace/reset, catch
wraps, state transitions). The ONLY places it renders user source are:

(a) `_emit_with_ctx_bind` (gen_async.py:3649-3664), used by both
    `WithEnter` (:3618) and `AsyncWithSetup` (:3642):
    `ctx_expr = self.expressions.gen_expr(item.context_expr)` at :3656.
    The borrowed-vs-owned wrap (`&(...)` / `.emplace(...)`) and the
    `is_already_pointer_source` classifier (:3659, AST-shape-based) stay
    skeleton. The `as`-target bind (:3629-3638) is a frame-field name +
    the `generator_frame_slot_locals` classification -- skeleton.

(b) `_emit_async_for_iter_setup` (:3711-3753):
    - async flavor: `gen_expr(stmt.iterable_expr)` at :3726;
    - sync begin_end/next/iter_next strategies: `_for_src_access`
      (:3692-3709) -> `gen_expr(iterable_expr)` at :3703, wrapped by
      `_maybe_unwrap_narrowed_optional` (:3702);
    - range strategy: `_emit_for_range_setup` (:3755) ->
      `gen_range_args(range_call)` at :3763 (renders each range-arg
      expression; the `static_cast` wrapping is skeleton).

(c) `_for_advance_parts` (:3782-3847) for the `next` / `iter_next`
    strategies RE-RENDERS the iterable via `_for_src_expr` (:3849-3855,
    `gen_expr(stmt.iterable)`) when the pre-scan allocated no `__for_src`
    field -- i.e. only when the iterable is a re-evaluable named
    expression. Everything else in the advance (counters, exhaustion test,
    loop-var bind incl. `tuple_to_pointer` and `&(...)` forms) is
    skeleton driven by `GeneratorForInfo` strategy metadata.

(d) Finally-helper bodies: `gen_coro_finally_top_def`
    (gen_async.py:1523-1543) emits each helper body with
    `self.statements.gen_stmt(out, stmt)` at :1540-1541 -- a separate
    emission entry point called right after `gen_coro_poll_def` at every
    call site (generator.py:669-724, 1045-1047), currently OUTSIDE the
    `_thir_leaf_scope`, and its body statements are NOT in `cfg.blocks`
    (they live in `cfg.finally_helpers`, resumable_cfg.py:592-594).

(e) Except-handler bodies: emitted via `_walk_inline` on the handler-entry
    BB (gen_async.py:3182-3184). Handler-entry BBs ARE ordinary
    `cfg.blocks` members (created at resumable_cfg.py:1325), so their leaf
    statements already flow through the existing `emit_leaf_stmt` /
    `render_cond` seams -- no new render hook needed. The catch-clause
    header itself (`_emit_except_handler_header`,
    statements.py:3742-3759) renders only the exception TYPE and binding
    name -- structural.

(f) Returns inside regions: routed through `_make_async_return`
    (gen_async.py, relocated there 2026-08-31 with the leaf-dispatch half
    of `_async_return_value_cpp` -- both used to live in statements.py,
    which the cutover deletes) which already delegates the VALUE render to
    the seam (`_async_return_value_cpp` -> `leaf.render_return_value`). Two scaffolding paths become reachable
    for routed bodies once regions admit: the pending-slot store
    (`to_borrow=False`) and the pre-finally `__tpy_async_ret_N`
    capture. For the admitted return shapes (value scalars,
    value-opt scalars, value tuples, container storage) both are
    render-identical (the `_wrap_view_to_storage` / `_async_ret_to_borrow`
    wraps are no-ops for them -- the latter short-circuits on anything but
    a pointer-repr Optional -- and the tuple-literal-targeted arm in
    `_async_return_value_cpp` returns the same target-typed brace-init at
    every scaffolding site); the load-bearing comment in
    `_async_return_value_cpp` pins this and must be updated by any cell
    that widens the return-shape gate. Container returns render bare at
    every site, EXCEPT an empty literal, which the lowering rejects
    (`return.empty_container_literal`): the AST spells an empty literal's
    type only when a target is passed, and this render has none.
    [ROUTED 2026-07-17 for LEAF-NESTED returns (`res.leaf_return`): a
    return inside a non-suspending leaf compound lowers to
    THIRResumableReturn, whose emit calls back into `_make_async_return`
    / `_make_generator_resumable_return` via
    `_EmitState.resumable_return_hook` -- scaffolding stays skeleton in
    every position, the value rides the same `return_values` table as
    ReturnT terminators. Refined 2026-08-31: the scaffolding is still
    skeleton everywhere, but a routed body's finally-DEFERRED capture no
    longer reaches back into the AST recipe for it -- the recipe is decided
    at lowering and read through `render_deferred_return`, because deciding
    it at the hook offers no way to reject, and the AST's alternative there
    is an emit-time mutation of sema state. GENERATOR helper returns route too (the hook's
    in_generator_finally_helper arm renders the fixed __finally_stop
    pair); ASYNC helper returns stay a named reject
    (`res.finally_return`, the Poll-replay render); an early-return
    narrowing leaf `if` composes via the BB-local post-if arm
    (`_apply_leaf_post_if`, THIRStmtSeq).]

(g) Resume-narrowing re-establishment (`_emit_resume_narrowings`,
    gen_async.py:3321-3347 -> `statements._emit_isinstance_extractions`):
    driven by sema facts stamped on BBs, no user-expression renders, BUT
    the LOWERING side has no way yet to lower leaves under narrowed types
    (it walks with a flat `declared` map). This is the separate
    `res.narrowed_resume` gate (now `_entry_narrowings_reject`) and stays OUT of
    this wave (see 4.4). [ROUTED 2026-07-17 for the variant-get slice:
    `_resume_narrow_envs` mirrors the walker's inline chains -- per-BB
    alias+fact envs keyed off the skeleton's case-entry set, extraction
    stays skeleton; the poly-self / readonly-subject / non-union-fact
    families remain named rejects -- see TODO.md.]

Oracle evidence that (a)-(c) are the whole render surface:

- `tests/cases/async/async_for_basic/expected/src/main.cpp:8-62`
  (`__coro_total`): the only non-already-seamed user render is `(c)` in
  `__for_itr_0.emplace((c).__aiter__());` (:13). The StopAsyncIteration
  catches, `__sub_0.emplace(*__for_itr_0)`, resets and state transitions
  are all skeleton; `s = 0`, `s += x`, and the return value are existing
  seam sites.
- `tests/cases/async/await_in_with/expected/src/main.cpp` (`__coro_caller`):
  only `Tracer("outer")` inside `__with_ctx_0.emplace(...)` is a new
  render; both `__exit__` catch arms and the normal-exit `__exit__` call
  are skeleton.
- `tests/cases/async/async_bind_await_except/expected/src/main.cpp`
  (`__coro_main_coro`): try/except around await needs ZERO new renders --
  handler leaf `print("caught")` is an ordinary BB leaf; catch headers and
  `c.reset()` are skeleton. (This case additionally carries a prebuilt
  slot await -- `res.await_prebuilt`, a separate cell -- illustrating the
  interlock: region admission alone does not flip it.)

### 2.5 What the lowering must additionally COVER (not render)

- Handler-entry and finally-body BBs are lowered automatically by the
  existing `for bb_id in sorted(cfg.blocks)` loop (resumable.py:412) once
  the gates lift -- but see 2.6 for the ordering caveat.
- Synthetic-payload Yields: `async_for_uid` (skeleton emplaces
  `*__for_itr_<uid>`, gen_async.py:4136-4138) and `async_with_kind`
  (skeleton synthesizes `(*__with_ctx_<n>)` receivers, :4124-4135) need NO
  arg lowering, but their `bind_target` (the `async for` loop var / `as`
  target) must register in `declared` like a VARDECL await bind
  (resumable.py:493-501 precedent).
- `AsyncForAdvance.stmt.var` (the sync loop var) must register in
  `declared` with `stmt.elem_type` before body leaves lower.
- Leaf statements inside regions are ordinary leaves; the leaf-mode
  rejects in `_lower_stmt_dispatch` (tpyc/thir/lower/statements.py,
  the `lc.resumable_leaf_mode` block) continue to bound the slice --
  now partial per construct (2026-07-18 grind cells): `res.leaf_try`
  fires only for finally-carrying trys (except-only trys fall through
  to the sync arm); `res.unpack` only off the routed source/target
  slices -- rvalue-source, value-tuple-name, one-shot await-lift
  (`auto&& __tup_N = (*lift);` move-outs, 2026-07-18), and the
  pointer-alias slices (2026-07-19: borrow-tuple CALL/NAME sources with
  `frame_ptr_elem` alias targets, the one-shot `tuple_to_pointer`
  bridge, and the deref'd pointer loop-holder head with
  `frame_ptr_addr` targets); in-branch
  `res.leaf_field_write` only outside
  plain_frame_fields; `res.finally_return` only for async helpers
  (generator bare returns route). `res.leaf_with`, `res.leaf_match`,
  `res.nested_def` remain whole rejects. (`res.leaf_return` ROUTED
  2026-07-17 -- see 2.4 (f); the reason survives only as the
  unreachable await-valued-leaf-return guard.)

### 2.6 A real design problem: declaration registration vs BB-id order

Lowering grows `declared` in `sorted(cfg.blocks)` order, relying on
"BB-id order is AST walk order" (resumable.py:349-353). Regions break
this: `_build_try` allocates `join_bb` BEFORE the try-body BBs
(resumable_cfg.py:1279 vs :1291) and handler entries after both (:1325);
`_build_async_with` allocates `join_bb` (:1576) before the body BBs
(:1581). So a first-declaration (`TpyVarDecl`) inside a try body, whose
name is read after the join, would be LOWERED AFTER the join's reads --
the read either rejects (name not in `declared`) or, worse, takes a
mis-typed arm. The same latent pattern exists for `_build_if`
(join at :944 before arm-internal BBs) but is masked there because a
direct arm-BB decl still has a lower id than the join, and deeper decls
whose values escape the arm are rare/rejected. Region admission makes the
mis-ordering common (every try body is "deeper" than its join).

Any chosen design must fix this. Proposed fix (used by the
recommendation): split leaf lowering into two passes over the SAME
gate-free walk -- pass 1 registers `TpyVarDecl` names/types (and await /
advance / with-target binds) into `declared` in BB order and records the
set of first-decl statement ids; pass 2 lowers leaves, using the recorded
first-decl set (not "name not yet in declared") to choose the DECL arm's
position-blind init render vs the reassign arm (preserving the
resumable.py:345-353 nuance that pre-seeding would misroute
`total = 0` through target-typed wraps). Admission stays read-only
(no shadow-state mutation), satisfying the no-preflight rule: pass 1 is
scope registration consumed immediately by pass 2, exactly like `declared`
today, not a predictive admission walk.

---

## 3. Candidate designs

### Design A -- region-transparent leaf seam (extend the current model)

Regions and pseudo-statements remain 100% skeleton; THIR gains exactly the
renders inventoried in 2.4.

THIR node/field changes:
- ONE new map on `THIRResumableBody` (nodes.py:2010):
  `region_exprs: Mapping[int, THIRExpr]` keyed by id() of the AST
  EXPRESSION node -- `item.context_expr` for with-ctx binds,
  `stmt.iterable` for for-setup/advance (one entry serves both renders:
  `AsyncForIterSetup.iterable_expr` IS the `TpyForEach.iterable` node),
  and each range-call argument for the range strategy. (Reusing
  `suspend_exprs` was considered; a separate map keeps the
  missing-key error messages and witnesses honest.)
- Finally-helper body statements go into the EXISTING `leaves` map
  (keyed by id(stmt), same as BB leaves).

Emitter changes (`ResumableLeafEmitter`, emit.py:2920): one new method
`render_region_expr(expr)` mirroring `render_suspend_expr`.

Skeleton changes (gen_async.py), each a 2-3-line leaf hook in the
established `if leaf is not None:` shape (:3933-3937 precedent):
- `_emit_with_ctx_bind` :3656;
- `_emit_async_for_iter_setup` :3726 and `_for_src_access` :3703
  (the narrowed-optional unwrap at :3702 stays AST-side; bodies with a
  narrowed-optional iterable REJECT in this wave -- rung deferred);
- `_emit_for_range_setup` :3763 (`gen_range_args` per-arg);
- `_for_src_expr` :3855;
- `gen_coro_finally_top_def` :1535-1541 gains the same
  `_thir_leaf_scope(leaf)` install as `gen_coro_poll_def` :1781 (the
  `_thir_resumable_attempt` cache, :1794-1828, makes the second lookup
  free).

Lowering changes (resumable.py): replace the blanket gates (:302-332) with
per-construct admission + the renders above + the two-pass decl
registration (2.6) + `declared` registration for advance/synthetic binds
(2.5). `_payload_reject`'s `res.await_synthetic` (:148-150) becomes a
handled arm (no args to lower; register `bind_target`).

- Fallback boundary: unchanged -- whole-body try-lower-catch at
  `lower_resumable` (:186-195); any reject anywhere (a leaf inside a
  handler, a range arg, a finally-helper compound) still falls the WHOLE
  body back to AST. No within-body mixing: a routed body routes every
  leaf including region internals.
- Byte-diff risk: LOW-MEDIUM. Every new hook replaces exactly one
  `gen_expr` string with the THIR render of the same node; the strings
  around it are untouched. The risky corners are (i) the double render in
  (c) -- mitigated because it only occurs for named re-evaluable
  iterables; (ii) the newly reachable `_make_async_return` scaffolding
  paths (2.4-f) -- value-scalar-only returns keep them identical;
  (iii) temps-flush ordering -- the hooks sit before the existing
  `ctx.temps.flush` calls (:3705, :3727, :3657), same contract as
  `render_cond`.
- Estimated unlock: the full ~103-140 tagged mass is ADDRESSED; actual
  body flips per cell gated by co-blockers (section 6).
- AST-deletion endgame: exactly on the settled trajectory
  (IR_DESIGN.md:1203-1215). The skeleton survives deletion by design;
  after this wave its remaining `statements.gen_stmt` / `gen_expr`
  delegations for routed bodies are all behind seams, so the final
  cutover just deletes the `else:` AST branches.

### Design B -- THIR-owned pseudo-statement nodes

Add `THIRWithCtxBind`, `THIRForIterSetup`, `THIRForAdvance`,
`THIRFinallyExit` nodes; lowering stores them in `leaves` keyed by id() of
the frozen synthetic dataclass (stable: the CFG is built once and cached,
gen_async.py:1865+ / resumable_cfg.py:126-132); `_walk_inline`'s synthetic
branches (:3378-3385) call `emit_leaf_stmt` on them; THIR emit reproduces
the frame-slot emplace / `__enter__` call / counter init / advance-bind
strings.

- THIR changes: 4 new stmt node classes + emit arms that duplicate
  `_emit_with_ctx_bind`, `_emit_async_for_iter_setup`,
  `_for_advance_parts`, `_emit_async_finally_exit` -- including the
  strategy ladder (range/begin_end/next/iter_next), the
  `GeneratorForInfo` metadata plumbing, frame-field naming conventions
  (`__with_ctx_<n>`, `__for_itr_<uid>`, `__finally_*_<n>`), and the
  pending-return two-shape replay.
- Fallback boundary: same whole-body model.
- Byte-diff risk: HIGH. ~300 lines of structural emission duplicated into
  THIR emit must stay byte-identical against an actively evolving skeleton
  (gen_async changed in every resumable wave so far); every future
  skeleton fix becomes a dual-maintenance edit -- precisely the cost the
  leaf-seam decision was made to avoid (IR_DESIGN.md:1203-1206:
  "justified by ... the dual-maintenance cost of a 4.1k-line evolving
  emitter").
- Endgame: no better than A -- the skeleton is NOT a deletion target, so
  the duplication is permanent, not transitional.

### Design C -- desugar pseudo-statements into THIR-visible source forms

Lower `async for` / `with` / `async with` into explicit THIR region trees
(a `THIRResumableRegion` node carrying try/catch replay structure), i.e.
make THIR own region replay and case wrapping; the skeleton shrinks to
switch/case scaffolding.

- THIR changes: a region node hierarchy mirroring
  TryRegion/WithRegion/FinallyRegion/ExceptRegion + an emitter
  re-implementing `_emit_case` (:2745-2833), `_emit_try_region_catches`
  (:3092-3244), `_emit_with_region_catches` (:3042-3090),
  `_emit_exit_region_finallies` (:3281-3319) and the pending-return ctx
  dance (:2835-2993).
- This is a re-architecture of the emitter, not a seam: the region replay
  is entangled with case-entry computation, no-throw classification
  (:2788), sub-future resets keyed to the resume yield (:3127-3133), and
  the destructor cleanup cases (:2867-2887). Reproducing it byte-identical
  is a multi-week effort with the highest divergence risk of the three,
  and it violates the settled skeleton-stays-shared decision. It would
  only be justified if the endgame REQUIRED deleting gen_async -- it does
  not (structural emission stays, like signatures).

---

## 4. Recommendation: Design A, in these cells (dependency order)

Each cell ends with: focused units in `tpyc/thir/test_thir_resumable.py`,
a full default run (whole-corpus byte-diff; `rpytest`), and a local
residual re-measure. Expected zero snapshot churn in every cell.

- Cell 0 (enabler): two-pass decl registration (2.6) behind the existing
  gates -- pure refactor of `_lower_resumable`'s walk, byte-diff neutral
  by construction; add a regression unit for a decl-in-try/read-after-join
  shape (currently gated, so pin via the pass-1 order directly).
  Also: re-measure `res.*` residuals to re-rank cells 1-6.

- Cell 1 (R6-try): admit `TryRegion`/`ExceptRegion` region stacks WITHOUT
  finally (neither helper- nor CFG-based: `finally_helper_name is None and
  captured_exc_field is None`) and no `MatchDispatch`. Zero new render
  hooks (2.4-e/f); handler BBs lower as ordinary leaves. Update the
  statements.py:3886-3894 comment + add a witness for the pre-finally
  return capture path. Unlocks try/except-around-await bodies
  (`async_bind_await_except`-shaped, minus their other blockers).

- Cell 2 (R6-with): admit `WithRegion` + `WithEnter`; add `region_exprs`
  map + `render_region_expr` + the `_emit_with_ctx_bind` hook. Reject
  remains for narrowed managers only if any surface (none known). Unlocks
  `await_in_with*`, `coro_with_as_ref`, `task_abandoned_with_exit`
  shapes.

- Cell 3 (R6-finally-helper): admit `cfg.finally_helpers` (helper-based,
  suspension-free finallies): lower helper bodies into `leaves`; install
  the leaf scope in `gen_coro_finally_top_def`. Slice restriction:
  reject helper bodies containing `return` in GENERATOR shape initially
  (the `__finally_stop` lowering, gen_async.py:1656-1667, is its own
  render nuance); async-shape returns inside finally helpers keep
  rejecting -- since the leaf-return cell routed `res.leaf_return`,
  helper returns carry their own named reject (`res.finally_return`).

- Cell 4 (R3-sync-loops): admit sync `AsyncForIterSetup` +
  `AsyncForAdvance`: hooks in `_emit_async_for_iter_setup`,
  `_for_src_access`, `_emit_for_range_setup`, `_for_src_expr`; lower
  iterable + range args into `region_exprs`; register the loop var in
  pass 1. Slice: value-scalar/str/bytes loop vars
  (`pointer_form_loop_var` / `borrow_tuple_loop_var` / frame_slot binds
  are skeleton strings but imply non-value element reads in the body --
  admit only what `_res_local_ok` already covers) [slice later WIDENED,
  2026-07-18: pointer-form and frame_slot loop vars now admit -- see the
  deferred-list ROUTED note below; `borrow_tuple_loop_var` keeps the
  reject]; REJECT
  narrowed-optional iterables (the :3702 unwrap) as a named rung.
  Depends on cells 0-1 (loop bodies commonly contain try or break-forced
  regions).

- Cell 5 (R6-finally-cfg): admit CFG-based finally (TryRegion with
  `captured_exc_field`, `FinallyRegion`, `AsyncFinallyExit` -- zero new
  renders) including the pending-return `to_borrow=False` store site
  (identical for the value-scalar return slice; witness it). Unlocks
  `await_in_finally*` shapes.

- Cell 6 (R3-async-for + R5-async-with): admit `is_async` iter-setup and
  the synthetic payload kinds (`async_for_uid`, `async_with_kind`);
  turn `res.await_synthetic` into handled arms (register binds, no arg
  lowering). async-for depends on cell 1 (its synthesized
  StopAsyncIteration region); async-with depends on cell 5 (its
  synthesized CFG finally).

Explicitly DEFERRED (kept as named gates, out of this wave):
`res.narrowed_resume` (needs narrowing-aware lowering scope);
`res.match` (MatchDispatch reuses `gen_match` for the dispatch --
its own seam design); `res.await_prebuilt` [ROUTED 2026-07-17: the
suspension is poll-in-place skeleton and the binding a factory-call-only
emplace; the residue is `res.coro_handle_source` -- see TODO.md];
non-value loop-var element forms [ROUTED 2026-07-18: pointer-form loop
vars ride `lc.pointers` and other non-value loop vars the frame_slot
classification, keyed on the for-prescan; the binds were skeleton all
along, and record yields of the routed names took the deref arm with
them -- the residue is tuple-unpack loops and dict_items proxy loop
vars, see TODO.md]; the narrowed-optional-iterable rung; generator-shape
return-in-finally-helper; multi-item `async with` (already a
`_CFGNotYetSupported`, resumable_cfg.py:1490-1495).

Why this order: cells 1-2 are the cheapest verifications of the region
model (zero/one render hook) and de-risk the decl-order fix on real
corpus mass before the loop cells (4, 6) build on them; 3 and 5 are
independent of 4 and can run in parallel with it in separate worktrees if
desired (all touch resumable.py + gen_async.py though -- internally serial
territory per the conflict map, so parallelizing across THESE cells is
not recommended; parallelize against other tracks instead).

---

## 5. Open questions for the user

1. Approve Design A (region-transparent leaf seam) over B/C?
2. Cell granularity: land as 6 cells on one branch (byte-diff green at
   each checkpoint), or collapse 1+2+3 (regions) and 4+5+6 (loops/CFG
   finally) into two larger checkpoints?
3. The two-pass decl registration (cell 0) changes how the DECL-vs-
   reassign arm is chosen for ALL currently-routed resumable bodies
   (should be byte-diff neutral; the whole-corpus diff proves it). OK to
   treat a clean corpus run as sufficient evidence, or do you want a
   dedicated unit matrix for the arm choice first?
4. `region_exprs` as ONE map for all three render kinds (ctx-bind,
   iterable, range args) vs three maps: one map is less plumbing, three
   give sharper missing-key diagnostics. Default: one map (the witness
   labels still distinguish the kinds).
5. Scope call: is deferring `res.narrowed_resume` + `res.match` +
   non-value loop vars acceptable for this wave (they cap the case-flip
   count in async/), or should narrowed resumes be pulled in (adds a
   narrowing-scope mechanism to lowering -- a design of its own)?

---

## 6. Interlock notes (what this does and does not flip)

- The multi-seam addresses ~103-140 FALLBACK BODIES (res.pseudo_stmt ~76,
  res.region ~27, plus res.for_await / res.finally / res.await_synthetic
  tails hidden behind first-reject ordering). Bodies != case flips.
- The async/ group's CASE flips are co-gated by the sync DRIVER bodies:
  `asyncio.run(main())` is a generic module-qualified call
  (`method.marker.builtin_module.generic`, tpyc/thir/lower/checks.py:
  2527-2537) -- the ~152-body driver slice; the 2026-07-14 drilldown
  measured 144/149 blocked async cases co-blocked on it. Landing the
  multi-seam WITHOUT the generic-module-call cell flips few async cases
  (the ratchet dial moves mainly via bodies + non-async generator/with
  cases); landing both is what converts the async corpus. Recommend
  scheduling the `marker.module.generic` call cell (call-cascade track)
  in the same wave window.
- `res.param_type` residue (post-wave-4: pointer-repr `Optional[F1-record]`
  and F1/value-tuple coro params now ROUTE; remaining -- Own-non-record,
  non-F1-record Optional, non-F1/non-value tuple, protocol
  (static-protocol -> blocked on generics), generic) and str/bytes RETURNS
  (the reverted view-source threading,
  TODO.md:72-75) independently cap flips for the fancier asyncio cases.
- Downstream beneficiaries once regions admit: resumable GENERATORS with
  try/finally (shares the same gates), and the `stmt.with` / foreach
  interplay cells in the generator track (tracks 4/7 coordination on
  `_res_value_ok`).
- No new warnings, no language-surface change, no snapshot changes --
  the wave is byte-diff-bound throughout.
