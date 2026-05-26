# Plan: Migrate generators onto the resumable-frame abstraction

> Revised after dual review (self + Codex staff-eng review). The original
> "three hooks + extract-first + byte-identical-async gate" framing was
> under-scoped and mis-sequenced; see "Revisions" at the bottom.

## Status

- **Phase A -- DONE.** CFG builder is suspension-generic. Predicates
  renamed `_stmt_has_any_await`/`_stmts_have_any_await` ->
  `_stmt_has_any_suspension`/`_stmts_have_any_suspension` (detect both
  `await` and `yield`); top-level `yield` dispatches to
  `_build_yield_stmt` -> `Yield` terminator carrying a `YieldPayload`;
  for/while-`else`-with-suspension rejection wording generalized. New
  unit tests in `tpyc/codegen_cpp/test_resumable_cfg.py` (5).
  Gate held: async codegen byte-identical (only 2 intentional diag-text
  rewords regenerated), generators untouched, full unit suite green.
  `build_async`->`build` rename deferred to Phase B.
- **Phase B -- DONE.** All 7 resumable-shape divergence points are
  isolated behind `_resumable_*` policy methods in `gen_async.py` (async
  impl inline; async codegen byte-identical -- full suite 4138 passed):
  - #1/#2 body method declarator + prelude -> `_resumable_body_method_decl`
    / `_emit_resumable_body_prelude`.
  - #3 return-lowering ctx -> `_resumable_return_lowering` context mgr.
  - #4 yield-terminator suspend + tail -> `_emit_yield_terminator`.
  - #5 resume -> `_emit_resume_core` (already a discrete method).
  - #6 done case -> `_emit_resumable_done_case`.
  - #7 struct fields/methods -> `_emit_resumable_extra_state_fields`,
    `_emit_resumable_sub_future_fields`, `_resumable_extra_ctor_inits`,
    `_resumable_body_method_fwd_decl`, `_emit_resumable_extra_methods`.
  No base-class split yet (deferred to Phase F); the generator shape
  plugs into these seams in Phase C.
- **Phase C -- IN PROGRESS** (mechanism: shape flag, option (a)).
  - **C0a DONE.** `AsyncCoroCodegen._shape` discriminator + `_resumable_shape`
    context mgr + `_is_generator_shape`. Frame-shape seams now branch on
    shape: method name (`__next__` vs `__poll__`), return type
    (`expected<T, StopIteration>` vs `Poll<T>`, via `_resumable_ret_type_cpp`),
    no waker prelude, done case (`make_unexpected(StopIteration)` vs panic),
    no `__cancel_pending`/sub-future fields, `__iter__()` instead of
    `cancel()`. Generator branches dormant (no consumer yet); async
    byte-identical (152/152).
  - **C0b DONE.** Behavioral seams: `_emit_generator_yield` (push value
    via `gen_yield_value` + advance + `return value`), resume step gated
    off for generators (no poll/bind), `_resumable_return_lowering`
    generator branch (sets `ctx.in_generator_resumable_body` +
    `current_yield_type`), `_emit_unreachable_tail` generator branch
    (fall-off-end -> StopIteration). New `statements.py`
    `_make_generator_resumable_return` (bare return -> walk finally +
    `make_unexpected(StopIteration)`), dispatched ahead of the legacy
    `goto __done` path. New ctx flags `in_generator_resumable_body` /
    `generator_resumable_done_state`. `YieldPayload` carries `yield_stmt`.
    All dormant (shape=async); async byte-identical (152), legacy
    generators unaffected (124), CFG unit (5).
  - **C1 DONE.** `CodeGenerator._resumable_generator_eligible` gates
    routing: free fn, non-generic, no params, non-tuple yield element,
    non-simple, and a flat body of leaf statements only -- a **structural
    allowlist** (`all(not s.sub_bodies() for s in func.body)`) so ANY
    compound / nested-body statement (if/while/for/try/with/**match**)
    stays on the legacy path. The 4 free-generator orchestration slots in
    `generator.py` route eligible generators to `gen_async`'s
    `gen_coro_struct`/`gen_coro_poll_def`/`gen_factory`/
    `gen_coro_forward_decl`/`gen_factory_forward_decl` under
    `_resumable_shape(ResumableShape.GENERATOR)`; everything else stays on
    `gen_generators.py`. Fixed `_payload_args_no_throw` to handle
    `YieldPayload`. New test `iterators/gen_resumable_basic` (+
    `gen_resumable_early_return`). 5 existing generators flipped to the new
    codegen with byte-identical runtime output (`gen_sequential`,
    `gen_two_generators`, `gen_frame_slot_list`, `list_slice_assign`,
    `list_stepped_slice_assign`) -- snapshots regenerated (codegen-shape
    only; output.txt unchanged).
  - **Post-review hardening (DONE):** `_shape` is a `ResumableShape` enum
    (not a magic string); the resume state label is shape-neutral
    `S_RESUME_<i>` (was `S_AFTER_AWAIT_<i>`); `gen_coro_struct` emits a
    shape-aware `// Generator:` comment + `<generator ...>` repr. The gate
    was hardened from a denylist to the structural allowlist above after
    `/code-review` found a top-level `match` with `yield` routed to the
    resumable path and silently dropped trailing yields (it now stays on
    legacy). Pre-existing/shared defects filed in BUGS.md: match/compound-
    wrapping-yield generators don't compile on the legacy path
    (goto-crossing); reference-type brace-literal yields (`yield [1,2]`)
    don't compile (shared yield-return emit).
- **Phase C DONE** (smallest eligible generators run on the resumable
  frame).
- **Params widening DONE** (done before loops as the lower-risk next
  step). Dropped the no-params gate restriction: free, non-generic,
  flat-leaf-body, non-tuple-yield generators **with parameters** now route
  through the resumable emitter, captured via the async `_classify_params`
  machinery (value by value, str as string_view, reference by `T&`,
  `Own[T]` moved in, pointer-repr `Optional` as `T*`) -- shapes match the
  legacy capture for value/str/ref/pointer, and `Own[T]` move-in is
  correct (handles move-only types the legacy by-value-copy capture
  could not). Borrow diagnostics (e.g. escaping-StrView dangle) are
  sema-level and preserved across the flip. 2 existing generators flipped
  (`gen_str_param`, `gen_str_temp_dangle`) with byte-identical output +
  diagnostics; new cases `gen_resumable_param_{value,ref,own}` (own is
  TPy-only: the cpy Box stub proxies `.get()`). Generics still excluded
  (Phase F).
- **Phase D1 DONE** (`if`/`while` generators). The eligibility gate was
  rebuilt around a **trial CFG build**: `gen_async._try_build_cfg(func)`
  attempts `_build_cfg` with `convert_errors=False` and returns None on
  `_CFGNotYetSupported` (caller falls back to legacy); success caches the
  CFG so emit reuses it. The decision is memoized on the func
  (`_resumable_gen_eligible`), consulted from 4 orchestration passes.
  Closed the silent-`match` gap: `_build_stmt` now raises
  `_CFGNotYetSupported` when a statement reaches the leaf-append path
  STILL carrying a suspension (an undecomposed compound like `match`), so
  the trial-build gate is uniformly safe (and async gets a clear error
  instead of a miscompile). `for`/`try`/`with` carrying a suspension are
  deferred via `_stmts_have_suspending_compound(body,
  GEN_DEFERRED_SUSPENDING_COMPOUNDS)`. 3 existing generators flipped
  (`gen_conditional`, `gen_early_return`, `gen_nested_while`) with
  byte-identical runtime output (codegen-shape-only snapshot churn); new
  cases `gen_resumable_{if,while}`. CFG unit tests updated (12).
  **Scope correction:** the original "Phase D approach" note below lumped
  `try`/`with` into D1; that was wrong -- `try`/`with` generators crash on
  the resumable path (`'YieldPayload' has no attribute 'mode'` in the
  await-specific exception/finally emit: `_emit_sub_reset` /
  `_emit_try_region_catches`) and stay on legacy. They are genuine Phase E
  work (matching the original "Revised sequence"). They are pre-existing-
  broken on legacy too (goto-into-try), so deferral is not a regression --
  Phase E is what fixes them.
- **Phase D2 DONE** (`for`-loop generators). Split into D2a (correctness)
  + D2b (perf), gated by a benchmark.
  - **D2a:** routed `for`-with-`yield` generators onto the resumable path.
    Two correctness fixes the universal lowering lacked: (1) non-value
    (non-readonly) loop vars now alias the element via `T*` (pointer-form
    frame field + address-bind) instead of a `frame_slot<T>` value copy
    (which deletes `operator=` -> a compile error); readonly elements use
    `frame_slot.emplace`. (2) temporary/rvalue native-container iterables
    are stored in a `__for_src` frame field so the borrowing iterator
    doesn't dangle. **Tuple-unpack for-loops are NOT yet handled and are
    gate-excluded to the legacy path** (`_stmts_have_tuple_unpack_for_with_suspension`):
    the resumable emit binds only the synthetic `__for_tup` loop var into
    the frame, not the destructured targets, so an unpack target read across
    a `yield` would read a stale frame field. This was caught by
    `/code-review` (the initial D2 incorrectly routed them to the resumable
    path -- the existing tuple-unpack tests passed only because they never
    read an unpack target after a suspension; regression test
    `gen_tuple_unpack_use_after_yield` added). Frame-storing the unpack
    targets is a D2 follow-up (tracked in TODO.md). Filed a pre-existing
    simple-generator temp-iterable dangle in BUGS.md.
  - **Benchmark (the "measure" step):** at `-O3`, universal lowering was
    3.1x slower than the legacy peephole for `range` and 1.75x for native
    containers -- the "concrete iterator inlines away the overhead"
    hypothesis was wrong. Decided D2b is warranted.
  - **D2b:** ported the legacy strategy analysis (`_analyze_for_strategy`,
    reused via a `gen_async.gen_generators` ref) into the resumable
    for-loop emit: a `GeneratorForInfo` per loop drives strategy-specific
    frame fields + setup/advance (range counter, begin/end, next,
    iter_next). Restored the peephole perf (range 0.42s->0.22s, list
    0.39s->0.22s == legacy parity; the residual range gap vs legacy goto is
    the resumable state-machine structure cost, inherent to the migration).
    Also speeds async sync-`for` loops (shared emit) -- 4 async snapshots
    churned (`await_in_for_{range,list,with_break,with_else}`) + the 10 D2a
    iterator flips, all output/diagnostics-identical, regenerated. New
    cases `gen_resumable_for_{nonvalue,temp}`.
- **Phase D3 DONE** (for/while-`else` with a suspension). No `__loop_broke`
  flag was needed: the CFG models break-vs-normal-exit with distinct edges.
  `_build_while`/`_build_for` now run the `else` body on the normal-exit
  edge (cond-false / exhausted) and route `break` to a separate `after_bb`
  that skips it; with no `else`, `after_bb` IS the exit BB so non-else
  loops are structurally unchanged. Removed the
  `_stmts_have_any_suspension(orelse)` rejections. This also fixed a latent
  "break runs the else" bug (the old code built `orelse` into the break
  target) -- untriggered before because no decomposed-loop test combined
  `else` + `break`. `gen_for_else` now routes to the resumable path
  (break-skips-else verified identical); `gen_for_else_tuple_unpack` stays
  on legacy (tuple-unpack is gate-excluded -- see D2 status); the two
  async `error_await_in_*_else` rejection cases became valid and were
  converted to positive tests `await_in_{for,while}_else`; new generator
  case `gen_resumable_while_else`. `await_in_for_with_else` structure
  churned (output unchanged). CFG unit `TestLoopElseStillRejected` ->
  `TestLoopElseSupported`.
- Next: **E** try/except/finally generators (the await-specific
  exception/finally emit -- `_emit_sub_reset` / `_emit_try_region_catches`
  reading `payload.mode` -- must become `YieldPayload`-aware; wire
  `gen_coro_finally_top_def` into the generator orchestration), then
  generics + retire the gate + extract base (F), then new surface (G).
- **Phase D/E prerequisites (surfaced by /tpy-review):**
  - Borrow-form yield slot **DONE**: `_resumable_ret_type_cpp` now
    routes through `gen_generators._iter_slot_for_yield` -- borrow form
    (`std::tuple<T&, ...>` / `std::tuple<P*, ...>`) for tuple yields,
    bare cpp type otherwise. The gate's tuple-yield restriction in
    `_compute_resumable_generator_eligible` was dropped (the `yt is None`
    guard stays). `gen_yield_value` already bridges storage-form sources
    into the borrow-form slot via `_maybe_wrap_tuple_to_pointer` (gated on
    `ctx.current_yield_type`, set by `_resumable_return_lowering`), so no
    callsite changes were needed. One existing test flipped from the
    legacy struct path: `tuple_optional_yield_prev` (multi-yield
    `tuple[P|None, P|None]` generator) -- output byte-identical, snapshot
    regenerated. Other tuple-yield tests stayed where they were: simple
    peephole (`gen_ref`, `gen_ref_range`, `gen_ref_while`,
    `tuple_optional_yield_mutate`) or generic-excluded (`gen_ref_compose`,
    `gen_generic*`). New regression `gen_resumable_tuple_yield` locks
    down a while+if/else multi-yield `tuple[P|None, P|None]` on the
    resumable path. Full suite 4189 passed.
  - Wire `gen_coro_finally_top_def` into the generator orchestration
    slot in `generator.py` **DONE**: the resumable-generator branch
    (the `if self._resumable_generator_eligible(func):` arm) now calls
    `gen_coro_finally_top_def` between `gen_coro_poll_def` and
    `gen_factory`, mirroring the async-coro slot exactly. Latent today
    -- the eligibility gate still excludes try/with so the CFG produces
    no finally helpers, and the method early-returns; Phase E activates
    it without further wiring. The unconditional trailing `cpp.write("\n")`
    added a blank line per eligible-generator main.cpp (33 snapshots
    regenerated, behavior-neutral). The stray-blank quirk is shared with
    async and was filed in TODO.md as a paired cleanup with the
    `frame_slot` unification.
  - De-async-ify the shared plumbing **DONE**: `_build_cfg` /
    `_try_build_cfg` -> `_build_resumable_cfg` / `_try_build_resumable_cfg`;
    `_prescan_async_for_loops` / `_prescan_async_try_finally` ->
    `_prescan_resumable_for_loops` / `_prescan_resumable_try_finally`;
    `CFGBuilder.build_async` -> `CFGBuilder.build`; the shape-neutral
    func-level caches `func._async_{cfg,cfg_builder,for_uid_map,
    for_fields,for_loop_info,for_info_by_uid,try_finally_uid_map,
    try_finally_fields}` -> `_resumable_*`. Kept `_async_`-named: the
    genuinely async-only `_build_async_for` / `_build_async_with` CFG
    builders (handle the `async for` / `async with` AST shapes),
    `_emit_async_for_*` / `_emit_async_with_*`, `func._async_for_struct_names`
    / `_async_with_struct_names` (sub-coro struct names),
    `func._async_lifted_body` / `_async_next_lift_id` (await-arg lifting),
    and the `ctx.in_async_coro_body` / `ctx.async_coro_{return_cpp,done_state}`
    flag set (merging those with the parallel
    `ctx.in_generator_resumable_body` / `generator_resumable_done_state`
    is a separate ctx-flag refactor, out of scope here). Behavior-neutral;
    full suite 4188 passed.
  - Unify resumable frame fields on `frame_slot` **DONE**: the synthetic
    for-loop fields (`__for_itr`/`__for_r`/`__for_src`/range counters
    `__for_i`/`__for_stop`/`__for_step`/`__for_it`/`__for_end`) and the
    `with`-fields (`__with_ctx_<n>`) now emit as `::tpy::frame_slot<T>`
    (matching hoisted user locals), with every write flipping to
    `.emplace(expr)`. Reads stay (both `std::optional<T>` and
    `frame_slot<T>` expose `operator*`/`operator->`/`has_value()`). Same
    memory layout (both carry the engaged bool) so the change is purely
    consistency + the frame_slot debug no-read-before-write panic; no
    perf/correctness delta. 36 snapshots regenerated (18 unique cases x
    hpp + cpp pairs, 126 inserts / 126 deletes -- perfectly balanced
    swaps): every async-for-with-suspension test, every async-with-with-
    suspension test, every resumable generator with a for-loop. Frame-
    size win (bare storage for default-constructible field types,
    dropping the bool) needs the `std::conditional` `frame_field<T>`
    wrapper and stays a separate item in TODO.md.
- Phase D (D1+D2+D3) DONE; Phases E-G not started.

## Phase D approach (DONE -- historical record)

Widened the gate from "flat leaf-only body" to "control flow the CFG can
handle." **Gate mechanism: trial CFG build** -- attempt
`_build_resumable_cfg(func)`; if it raises `_CFGNotYetSupported`
(loop-`else`, nested-await-in-finally), fall back to the legacy path.
Auto-tracks the CFG's real limits instead of a hand-maintained denylist,
and the built CFG is cached so emit reuses it. **Gap closed first:** the
CFG silently mishandled `match` (a leaf with a hidden yield) rather than
raising -- the builder now raises `_CFGNotYetSupported` on a suspension
inside an undecomposable leaf so the trial-build gate is uniformly safe
(the explicit `match` exclusion is gone). Remaining gate checks:
non-generic (Phase F) / non-simple-peephole (kept by design) /
non-tuple-unpack-for-loop (D2 follow-up). The non-tuple-yield restriction
was dropped by the borrow-form yield slot prerequisite.

Sub-sequencing (each regression-free):
- **D1 -- `if` / `while` (peephole-free). DONE.** No peephole concern; the
  CFG already decomposes them (async M3.x). Gate = trial-build succeeds AND
  no `for`/`try`/`with` carrying a suspension (recursively) AND existing
  checks. ~~try/with~~ were in the original D1 list but moved to E: their
  resumable exception/finally emit is still await-specific and crashes on a
  `YieldPayload` (see the scope-correction note in Status). Widens to the
  common control-flow generators with zero perf risk.
- **D2 -- `for` loops. DONE.** The peephole fork was RESOLVED by
  measurement: the user chose "land D2a, benchmark, then decide D2b", and
  the benchmark (universal 3.1x slower for range / 1.75x for containers)
  selected option (i) -- port the peepholes. D2a landed the correctness
  fixes (pointer-form non-value loop var; temp-iterable `__for_src`);
  tuple-unpack for-loops are gate-excluded to legacy (a `/code-review`
  follow-up -- the resumable emit doesn't frame-store unpack targets yet).
  D2b ported the legacy `_analyze_for_strategy`
  into the resumable for-loop emit (strategy-specific fields + setup/advance)
  -- restored peephole perf and also sped async sync-`for` loops. See the
  Status section above for the full record.
- **D3 -- for/while-`else`. DONE.** No `__loop_broke` flag needed -- the
  CFG models break-vs-normal-exit with distinct edges (else on the
  normal-exit edge; `break` -> a separate after-BB that skips it).
  Benefits async too (lifted the await-in-loop-else rejection). See Status.

All three Phase D/E prerequisites listed above have shipped (borrow-form
yield slot; `gen_coro_finally_top_def` wired into the generator
orchestration -- latent today; `_build_cfg` -> `_build_resumable_cfg`
de-async-ify pass; frame_slot unification of synthetic fields). Phase E
(try/except/finally generators) is the next step.

## Goal

Unify the two parallel state-machine codegens. Move the **struct-path**
(multi-yield / yield-in-control-flow) generator codegen
(`tpyc/codegen_cpp/gen_generators.py`) onto the CFG + resumable-frame
emit that powers `async def` (`tpyc/codegen_cpp/resumable_cfg.py` +
`gen_async.py`). The **simple-generator lambda peephole** stays
untouched. Phases A-F are intended to be behavior-preserving (same
`yield` surface, `Iterator[T]` return, `__next__() -> std::expected<T,
StopIteration>` contract); new surface (generic multi-yield, `yield
from`/`send`/`throw`/`close`) is explicitly later and not "free".

Classification: **architectural**.

## What actually has to change (corrected scope)

### 1. The CFG builder is NOT yet shape-neutral
Only the CFG *data types* + region machinery are shape-neutral. The
*builder* still drives decomposition off await-specific predicates
(`_stmt_has_any_await`, `_stmts_have_any_await`, top-level-await
classification). Required: a generic "contains a suspension" predicate
(await OR yield) driving the if/while/for/try/with decomposition, plus
`TpyYield -> YieldPayload` dispatch.

### 2. The customization surface is much bigger than "three hooks"
A resumable **policy interface** must cover at least:
- **Method signature/return type:** `__poll__(Waker)->Poll<T>` vs
  `__next__()->std::expected<T,StopIteration>` (no Waker).
- **Done behavior:** async `S_DONE` panics on second poll; a generator
  must return `StopIteration` on every repeat `__next__()`
  (`gen_async.py:1547`).
- **Suspend exit action / tail:** async emits `continue` to re-enter the
  switch and poll in the resume case (`gen_async.py:2051`); a generator
  `yield` instead `return`s the value and the NEXT call re-enters.
- **Return handling:** generator `return` currently lowers to
  `goto __done` (`statements.py:435`) -> must map to the
  StopIteration/done path; keep rejecting return-with-value.
- **In-flight cleanup / no-throw classification / struct-field layout:**
  generators have no `__sub_n` sub-future fields, no `__cancel_pending`,
  no waker; they DO need `__iter__()` + the iterator-protocol struct
  shape.
- **Suspend value direction:** await binds a value IN (poll result);
  yield pushes a value OUT (and `send()` later binds IN).

### 3. `for...else` is a correctness blocker, not a follow-up
`tests/cases/iterators/gen_for_else` (+ `gen_for_else_tuple_unpack`)
yield inside a `for` whose `else:` also yields. The CFG explicitly does
NOT model break-vs-normal loop exit and rejects suspensions in for/while
`else` (the `orelse` checks in `_build_while` / `_build_for` raise
`_CFGNotYetSupported`). Parity requires the `__loop_broke_<n>` flag
(BUGS.md:26) BEFORE cutover. The Phase-C eligibility gate sidesteps this
by excluding any generator with a loop (linear-body-only).

### 4. For-loop handling is mostly SEMANTIC, not a perf "fork"
Splits into:
- **Correctness/compileability (must port):** pointer-form loop var
  aliasing for non-value native elements (`gen_generators.py:585`; the
  CFG's universal binding doesn't encode this, `gen_async.py:2267`),
  direct `Iterator[T]` handling, native begin/end, temporary-iterable
  frame storage.
- **Perf only (deferrable):** the `range` counter peephole.

### 5. Tuple-unpack loops need explicit frame-field handling
Struct generators assign unpack targets into frame fields because resume
crosses the yield; the ordinary tuple-unpack emitter can create C++
locals that shadow frame fields -> wrong if used after resume.
(`gen_for_tuple_unpack`.)

### 6. Generic multi-yield is NOT free
Today's rejection is tied to out-of-line generator `__next__` emission
(`gen_generators.py:682`, `registration.py:667`). Reaching parity needs
deliberate work on factory placement, method factories, protocol-template
params, and the generic-class-method rejection. Own phase.

## What holds from the original analysis
- Sema feeds both paths `func.generator_locals` already (read by both
  `gen_coro_struct` and `gen_generator_struct`), so the hoisted-locals
  fact needs ~no sema change.
- The simple-generator lambda peephole is cleanly separable via
  `is_simple_generator` and stays out of scope.

## Revised sequence (migrate-then-extract; lower risk)

- **A. Make the CFG suspension-generic.** Generic "contains suspension"
  predicate; wire `TpyYield -> YieldPayload`. No async behavior change
  (async still only produces AwaitPayloads).
- **B. Define the resumable policy interface IN PLACE** in the existing
  emitter (parameterize, do NOT yet split a base class), covering the
  full surface in section 2. Async output must stay byte-identical --
  the gate for this step (parameterizing in place is a far smaller,
  safer change than a broad extraction).
- **C. Migrate the smallest non-loop / non-try generator** (linear
  multi-yield) through the policy. Prove the hook surface on the easy
  case before loops/try.
- **D. Loop semantics:** for/while including the `__loop_broke` for-else
  fix, tuple-unpack frame-field handling, pointer-form aliasing + direct
  iterator + native begin/end + temp-iterable storage; range peephole
  optional.
- **E. try / except / finally generators.**
- **F. Extract any genuinely-shared code** now that two consumers exist
  and the hook surface has proven itself (decide inheritance vs policy
  object empirically; may end up "shared CFG + two thin emitters").
- **G. (Separate, new surface):** generic multi-yield, then `yield
  from` / `send` / `throw` / `close`.

## Risks
- `gen_async.py` (~2.5k lines) is load-bearing; mitigation is the
  byte-identical async gate on step B and full generator regression net
  per step.
- Several pointer-form-slot / borrow gaps in BUGS.md span both paths;
  migrate as-is unless fixed first.
- Struct-path generator snapshots regenerate (expected churn).

## Tests + docs
- Regression net: `tests/cases/iterators/*` and
  `tests/cases/tuple/tuple_optional_yield_*`. Each migration step must
  keep its slice green; struct-path snapshots regenerate, simple-path
  don't.
- New cases for generic multi-yield in phase G.
- Docs: resolve `TODO.md:239` peephole open question; update
  `docs/LANGUAGE_FEATURES.md`.

## Branch
Fresh branch off master: `migrate-generators-resumable`.

## Revisions (from review)
- Dropped "exactly three hooks" -> full policy surface (section 2).
- Corrected "CFG already shape-neutral" -> builder predicates are
  await-specific; needs a generic suspension predicate (section 1).
- `for...else` promoted from follow-up to pre-cutover blocker (section 3).
- For-loop "peephole fork" reframed as mostly semantic parity (section 4).
- Added tuple-unpack frame-field hazard (section 5).
- "Generic multi-yield free" -> its own deliberate phase (section 6).
- Re-sequenced: parameterize-in-place + migrate-smallest-first, extract
  LAST, instead of broad extract-first.
