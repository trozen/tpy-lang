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
- **Phase E DONE** (`try`/`except`/`finally` and `with` generators).
  Three fixes:
  - **Fix 1** (`_emit_try_region_catches`, 2 sites): `_emit_sub_reset` is
    now guarded by `isinstance(payload, AwaitPayload)` -- generators have
    no sub-futures, so the reset is a no-op for generator shape.
  - **Fix 2** (`gen_coro_struct` field emit): `__finally_ret_<n>` fields
    are skipped for generator shape (the prescan runs during the trial
    build under the async shape and would allocate the field with the wrong
    type; the field is suppressed at struct-emit time instead).
  - **Fix 3** (`generator.py`): `GEN_DEFERRED_SUSPENDING_COMPOUNDS` is now
    empty -- both `TpyTry` and `TpyWith` are handled by the resumable path.
  - **E2 (yield in finally)**: `_make_generator_resumable_return` now
    checks `ctx.async_pending_return_flag`; when a CFG-based finally is
    active, it sets the pending flag and transitions to the finally entry
    (mirroring `_make_async_return`). `_emit_async_finally_exit` has a
    generator branch that emits `make_unexpected(StopIteration)` instead
    of `Poll::ready`. New tests: `gen_resumable_try_except`,
    `gen_resumable_try_finally`, `gen_resumable_with`,
    `gen_resumable_yield_in_finally`. Async byte-identical (152/152).
  - **E3 (`return` in helper-based finally)**: `gen_coro_finally_top_def`
    sets `ctx.in_generator_finally_helper = True` for generator shape so
    `return` emits `this->__finally_stop = true; return;` (void) instead of
    `goto __done;`. Every helper call site uses `_emit_finally_helper_call`
    which, when `ctx.generator_has_finally_stop` is set, appends a
    `if (this->__finally_stop)` check that returns StopIteration -- correctly
    suppressing any pending exception (Python: `return` in `finally` wins).
    `__finally_stop` is a struct field added only when at least one helper body
    contains a `return` (via `_stmts_have_return` scan). New test:
    `gen_resumable_return_in_finally`. Full suite 4194 passed.
- **Phase F SCOPED (2026-05-27)** -- see "Phase F approach" below.
  Generics in scope (F2); generic-class methods initially deferred but
  forward-compatible (F4, completed 2026-05-28); legacy struct path
  deleted once parity proven (F5). Then new surface (G).
- **Phase F2 DONE (2026-05-27)** (generic free-fn generators). Lifted the
  `func.type_params` early-out in `_compute_resumable_generator_eligible`;
  generic (`[T]` type-param) multi-yield generators now route onto the
  resumable frame. Added `gen_async._is_templated_coro(func)` (true for
  explicit type params OR a static-protocol-typed param) as the single
  source of truth for the inline-header-vs-`.cpp` body placement; the
  generator orchestration in `generator.py` uses it to emit the templated
  struct + `__next__` + factory inline in the header (mirroring the generic
  `async def` branch). All four resumable emit functions were already
  template-aware (`_emit_template_header` / `_struct_name_templated`), so no
  emission changes were needed -- F2 is purely gate + orchestration.
  Verified across shapes: for-loop over `list[T]`, `try`/`finally`,
  multi-type-param `tuple[K, V]` yield. The `gen_generators.py` generic
  reject is RETAINED as a fallback guard (a generic generator whose body the
  resumable CFG can't build -- e.g. `match`+yield -- falls to legacy and
  gets a clean "not yet supported" diagnostic instead of a broken build).
  **Proto-param generators (`def gen(it: Iterable[T])`) are gate-excluded**
  (`get_all_protocol_params` check): the resumable for-loop frame field
  would be typed against the abstract concept (`Iterable<T>`) instead of the
  deduced template param `T_it` -- an ill-formed field decl -- so they route
  to legacy for a clean reject (a strict improvement: previously they were
  silently routed to resumable and produced a broken C++ build). That
  substitution is the separate TODO.md "protocol-typed params" feature.
  Tests: converted `error_gen_generic_multi_yield` ->
  `gen_generic_multi_yield` (now positive), new `gen_generic_for`,
  `gen_generic_tuple_yield`, `error_gen_proto_param_multi_yield`. Full suite
  4207 passed; async byte-identical (the `_is_templated_coro` helper is used
  only in the generator orchestration branches -- async still keys off
  `func.type_params`; see the latent-async note below).
- **Phase F1 DONE (2026-05-27)** (generator methods on the resumable frame).
  Generator methods previously always routed to the legacy
  `gen_generators` emitter (3 orchestration sites: .cpp `__next__` def,
  forward decl, struct + inline factory). Eligible non-simple generator
  methods now route through `gen_coro_*(record_name=...)` under
  `ResumableShape.GENERATOR`, mirroring the async-method emission exactly --
  the inline factory reuses `_factory_args_forwarded(method,
  receiver=(record, "*this"))` (so `Own[T]` / protocol params move in
  correctly), and `_struct_name_templated` / `_emit_template_header` make it
  generic-method-ready by construction. Generator method structs stay in the
  post-records orchestration loop (unlike async methods, which emit early to
  satisfy sub-future field inlining -- generators have no sub-futures, so
  the ordering is unchanged from the legacy path -> minimal churn).
  `_resumable_generator_eligible` works unchanged for methods (its checks
  are body/param-shape based, record-name-agnostic). Verified: multi-yield
  `__iter__` + `while`-loop method, `try`/`finally` method with a param,
  readonly no-param method (const factory + `__self` capture). The
  generic-CLASS-method reject (`registration.py`) still fires before codegen
  (F4, deferred). One existing test flipped shape (`gen_method_complex`,
  output byte-identical, snapshot regenerated); new `gen_resumable_method`.
  Full suite 4208 passed. Remaining F: F3 (tuple-unpack), F5 (retire
  legacy), F6 (cleanup).
- **Phase F3 DONE (2026-05-27)** (tuple-unpack for-loops). `for a, b in xs:`
  generators/coroutines carrying a suspension now route onto the resumable
  frame (the `_stmts_have_tuple_unpack_for_with_suspension` gate exclusion +
  helper + its unit tests are removed). The fix is in the shared
  `_gen_tuple_unpack` (`statements.py`): when the unpack source is a LIVE
  frame field (a suspending loop's `__for_tup`, not an awaitless shadow
  local), each target is ASSIGNED into its frame field instead of declared
  as a shadow C++ local (which wouldn't survive the suspension). Shared by
  generators and coroutines (`in_generator_body`), so it also closed the
  latent async tuple-unpack-after-await gap.
  - **Reference elements alias (correctness, not perf).** The "pointer-alias
    for reference elements" originally filed as a perf follow-up turned out
    to be a CPython-semantics requirement: a value-copy diverges when the
    unpacked record is mutated (CPython: mutation propagates to the source;
    TPy plain loop vars already alias via D2a). So reference (non-value,
    non-readonly, non-Optional) unpack targets now use pointer-form aliasing
    -- `__for_tup` becomes `T*` (aliases the live container element) and the
    targets become `T*` (`&std::get<i>(*__for_tup)`), via a new
    `GeneratorForInfo.pointer_form_unpack_targets`. Value elements copy
    (CPython-equivalent for immutables); pointer-repr Optional elements use
    `optional_to_ptr`; readonly elements fall back to value. Verified
    TPy==CPython for mutation propagation (`process` mutating `it.n` -> the
    source list sees it), for generators and coroutines.
  - **No awaitless regression.** The frame-store gate keys off
    `frame_field_shadows`: an awaitless loop emits `__for_tup` as a shadow
    `auto&&` local, so its targets stay zero-copy `T& a = ...` references
    (e.g. `coro_for_tuple_unpack_ref` reverted to byte-identical). Only
    suspending loops frame-store. The over-broad `generator_locals` hoist
    (dead-field TODO) is why the gate is needed; the sema narrowing would
    subsume it.
  - Tests: existing `gen_tuple_unpack_use_after_yield`, `gen_for_tuple_unpack`,
    `gen_for_else_tuple_unpack`, `async_for_tuple_unpack` flipped to the
    resumable path (output-identical, snapshots regenerated); new
    `gen_resumable_tuple_unpack_ref` (mutation propagation), `_optional`,
    and async `coro_tuple_unpack_mutate`. Full suite 4209 passed. Remaining
    F: F5 (retire legacy), F6 (cleanup).
- **Phase F6 DONE (2026-05-27)** (cleanup: `ResumableFuncState`). The ~16
  string-keyed `getattr(func, "_resumable_*" / "_async_*" / "_with_*")` side
  tables attached to each `TpyFunction` collapsed into one typed
  `func._resumable_state: ResumableFuncState` dataclass (defined in
  `resumable_cfg.py`, the cycle-free home; get-or-create via
  `resumable_state(func)`). Read sites lose the `getattr(..., None) or {}`
  ritual (the dataclass fields default to empty containers); the
  prescan->emit contract is visible in one place; a typo is now a real
  AttributeError instead of a silent `None`/`{}` miscompile. The original
  "cache even when empty" semantics of the three prescans are preserved with
  explicit `for_prescanned` / `with_prescanned` / `try_finally_prescanned`
  flags (an empty result distinct from "not yet run"). Pure internal
  refactor -- zero snapshot churn, full suite 4209 passed. Pairs with the
  eventual THIR/MIR migration (the dataclass is the natural nucleus of
  per-function lowering state). Remaining F: F5 (retire the legacy struct
  path), then G.
- **Phase F5 DONE (2026-05-27)** (retire the legacy struct path). Done in
  two stages to de-risk a multi-file deletion.
  - **Stage 1 -- relocate rejections, flip the gate (no deletion).** New
    sema check `_check_resumable_suspension_shape` (analyzer.py) rejects the
    suspension-generic shapes the resumable CFG can't lower -- `yield`/`await`
    in a `match`, `return` in a suspending `finally`, nested suspending
    `finally` -- with clean located diagnostics for generators AND
    coroutines (verified it preserves `error_await_in_control_flow`'s exact
    message + loc, and does not over-reject the supported return-in-try /
    non-suspending-finally cases). The three pure-AST predicates
    (`stmt_has_any_suspension` / `stmts_have_any_suspension` /
    `stmts_have_any_return`) moved to `parse/nodes.py` so sema and codegen
    share them without a sema->codegen import. The generator gate
    (`_compute_resumable_generator_eligible`) now returns True or raises for
    a non-simple generator -- proto-param -> clean `SemanticError`;
    everything else builds the CFG (the `convert_errors=True` backstop
    raises a clean `CodeGenError` for any residual shape) -- never falls
    through to a legacy branch. Proof the legacy struct path was already
    behaviorally dead: instrumenting `gen_generator_struct` showed the only
    suite consumer was `error_gen_proto_param_multi_yield` (a reject-only
    case); the three not-yet-supported shapes all *failed to compile* on
    legacy today (goto-crossing / duplicate-label C++ errors), so the clean
    reject is a strict improvement, not a regression. Full suite green.
  - **Stage 2 -- delete the dead code (~660 lines).** Removed the
    `gen_generators.py` struct functions (`gen_generator_struct` /
    `gen_generator_next` / `_gen_generator_body` / `gen_generator_factory` /
    `*_forward_decl`) + the struct-only helpers (`_prescan_for_loops`,
    `_pointer_form_loop_vars`, `_for_body_contains_yield`); the
    `statements.py` legacy lowering (the goto-style `_gen_yield`, the
    `_gen_generator_for_*` for-loop cluster, the dead `in_generator_body`
    for-loop dispatch, the `goto __done` return branch); the write-only sema
    `generator_yield_states` / `_yield_counter` bookkeeping; and
    `gen_async._try_build_resumable_cfg` + the `convert_errors` param. Kept
    the simple-generator peephole, `_analyze_for_strategy`,
    `_iter_slot_for_yield`, `_is_named_generator_field`, `GeneratorForInfo`
    (all borrowed by the resumable path), and `gen_struct_name` (the
    resumable generator struct keeps the `__gen_` name). A `yield` reaching
    the linear statement walk now raises an internal-invariant error. Full
    suite 4238 passed. New error cases: `iterators/error_gen_match_yield`,
    `error_gen_return_in_yield_finally`, `error_gen_nested_yield_finally`,
    `error_gen_generic_match_yield`, `async/error_async_match_await`.
    Remaining F: F4 (generator/async methods on generic classes, deferred /
    standalone). Then G (new surface: `yield from`/`send`/`throw`/`close`).
- **Phase F4 DONE (2026-05-28)** (generator/async methods on generic
  classes). Lifted the shared `_reject_method_on_generic_class` sema
  rejection (deleted along with both call sites in `registration.py`).
  Codegen change: extended `_is_templated_coro` / `_emit_template_header` /
  `_struct_name_templated` (and `_classify_params`'s `__self` receiver
  type) to fold in the enclosing record's `type_params` for methods. The
  single-source-of-truth predicate F1/F2 established now answers "does
  this coro struct need a C++ template header?" with `func.type_params OR
  proto-typed params OR the method's enclosing record's type_params`,
  shared between async and generator shapes. Inline factory definitions
  qualify the class name with its template args (`Box<T>::get` /
  `Box<T>::items`). Verified: async method on `Box[T]`, generator method
  on `Box[T]` (multi-yield), and a generator method on a two-param
  `Pair[K, V]` with control flow. Converted the two rejection tests to
  positive (`iterators/gen_method_generic`,
  `async/async_method_on_generic_class`); added
  `iterators/gen_method_generic_multi_param`. Full suite 4263 passed (3
  new positive cases; the 2 error cases became positive).
  **Follow-up coverage (surfaced by /tpy-review):**
  `async/async_method_readonly_on_generic_class` (the `const Box<T>&`
  self-capture branch) and `async/async_method_typeparam_on_generic_class`
  (method with its own `[U]` on a generic class). The latter surfaced a
  real bug missed by the initial F4 work: the out-of-class inline factory
  definition of a member function template of a class template
  (`Box<T>::with_label`) requires **nested** C++ template headers
  (`template <typename T>` then `template <typename U>`), not the flat
  `template <typename T, typename U>` that `_emit_template_header`
  produces (which declares a different entity and triggers "no
  declaration matches"). Fix: added `_emit_member_template_headers` for
  the out-of-class member-definition sites; the flat helper stays
  correct for free-struct definitions and for struct-member methods like
  `__poll__`. **Second-round /code-review** caught two more issues: (1)
  the generator-method in-class declaration in `records.py` switched to
  `_struct_name_templated` (correctly spelling `__gen_X_items<U>`) but
  forgot to call `_emit_template_header` -- a non-simple generator
  method with its own `[U]` type param emitted the templated return
  type with no leading `template <typename U>`, an asymmetry vs the
  async-method branch; (2) `_classify_params`'s receiver-type
  construction called `escape_cpp_name(record_name)` without the
  `.replace('.', '::')` the two factory sites use, so a method on a
  nested record (`Outer.Inner`) would have spelled `__self: Outer.Inner&`
  -- invalid C++. Both fixed; new positive test
  `iterators/gen_method_typeparam` guards the first (the second is
  latent, no nested-record method test exists). The structural migration
  is complete; user-visible completeness is tracked as Phase H below.
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
- Phases D (D1+D2+D3), E, and F (all of F1-F6) DONE; the migration's
  **structural** goal (one struct-shape codegen + the simple-generator
  peephole) is complete. **User-visible completeness** -- closing the
  shapes the resumable frame is now structurally capable of lowering
  but that still cleanly reject -- is tracked as **Phase H** below.
  G (`yield from`/`send`/`throw`/`close`), async generators (PEP 525),
  and `@contextlib.contextmanager` are out of Phase H scope (new
  surface, not completeness gaps).

## Phase H approach: close the rejects (user-visible completeness)

The migration's structural goal (A-F: one struct-shape codegen + the
simple-generator peephole) is complete. Phase H is the user-visible
completeness pass: every shape the resumable frame is now
*structurally* capable of lowering but that still emits a clean
"not yet supported" diagnostic should compile -- the suspension-shape
sema reject becomes effectively empty for the resumable path.

**Definition of complete:** Phase H1-H3 done (the high-priority
CFG-decomposition bundle), and H4-H6 either done or explicitly
deferred in TODO.md with a tracker. H7 stays low-priority indefinitely.

### High priority -- CFG-decomposition family

Shared fix shape: extend the CFG builder to decompose one more compound
shape. Benefits async and generators together; each item converts an
existing `error_*` case to positive.

- **H1 -- `yield`/`await` inside `match`.** `match` is the one compound
  the CFG does not decompose, so a suspension reaching the leaf-append
  path raises `_CFGNotYetSupported` (and sema's
  `_check_resumable_suspension_shape` rejects it earlier). Fix: add a
  `MatchRegion` to `resumable_cfg.py` that lowers each case arm as its
  own basic block, with the discriminant-bind and pattern-test as
  separate terminators. Drop the corresponding sema reject. Converts:
  `iterators/error_gen_match_yield`, `error_gen_method_match_yield`,
  `error_gen_generic_match_yield`, `async/error_async_match_await`.

- **H2 DONE (2026-05-28)** -- `return` inside a suspending `finally`.
  Extended the existing M3.3.2 parking machinery so a `return` originating
  inside the finally body parks into the same `__finally_ret_<n>` /
  `__finally_pending_<n>` slot used for try-body / handler-body returns.
  Mechanism: `FinallyRegion` gained `pending_return_flag` /
  `pending_return_slot` / `finally_exit_bb` fields; a new
  `finally_exit_bb` (allocated outside the FinallyRegion) holds the
  single `AsyncFinallyExit` synthetic, so both normal fall-through and
  a return-in-finally drain through one replay site (jumping back to
  `finally_entry_bb` would have re-executed the body). The CFG prescan
  + try/finally builder now look at `stmt.finally_body` when deciding
  whether to allocate the slot; `_pending_return_info_for_region_stack`
  recognizes the FinallyRegion and returns its parking info with
  `target_bb = finally_exit_bb` and `boundary = 0` (no inner frames to
  skip -- already inside the finally). `AsyncFinallyExit` emit order
  was swapped: pending-return check runs FIRST, clearing the captured
  exception on its way out (Python: return-in-finally swallows
  in-flight exceptions); the rethrow check runs only when no return is
  pending. Sema reject dropped. The CFG layer's parallel reject was
  also dropped. Tests: converted `iterators/error_gen_return_in_yield_finally`
  -> `iterators/gen_return_in_yield_finally` and
  `async/error_async_return_in_await_finally` -> `async/async_return_in_await_finally`;
  new positive cases `iterators/gen_return_in_finally_swallows_exception`,
  `async/async_return_in_finally_swallows_exception` (exception
  swallowing), and `async/async_return_in_finally_overrides_try_return`
  (try-body and finally-body both return -- finally wins).

- **H3 -- Nested suspending `finally`.** Today rejected because the
  inner suspending finally would need to forward its pending exception
  / return to the outer slot. Fix: when building a try whose finally
  suspends inside an outer suspending-finally region, chain the
  pending-exception and pending-return slots so the inner's
  AsyncFinallyExit hands off to the outer's parking machinery instead
  of running them as the final exit. Drop the sema reject. Converts:
  `iterators/error_gen_nested_yield_finally`,
  `async/error_await_in_control_flow`.

### Medium priority -- independent shapes

- **H4 -- multi-yield protocol-typed-param generators.** Today rejected
  at the codegen gate: a `def gen(it: Iterable[T])` makes the struct a
  template (`T_it`) but the resumable for-loop frame field is typed
  against the abstract concept `Iterable<T>` instead of the deduced
  template param -- ill-formed. Fix: substitute the for-loop frame-field
  type (and any other body type referencing the iterable) with the
  deduced template param `T_it`. Drop the codegen-gate reject.
  Converts: `iterators/error_gen_proto_param_multi_yield`.

- **H5 -- narrowing across a suspension inside the narrowed block.**
  Shared async+generator latent limitation filed in `BUGS.md` during
  F5 review: `isinstance(self, Sub)` (or any polymorphic narrowing)
  inside a block that suspends, then accesses subclass state after,
  fails to compile because the narrowed `Sub*` is a C++ local, not a
  frame field. Fix: materialize the narrowing binding as a frame field
  (`Sub* __self_narrowed`) hoisted across the suspension, parallel to
  how user locals are hoisted. Same machinery as flow-fact-narrowing
  surviving a suspension generally.

- **H6 DONE (2026-05-28)** -- generator/async methods on bounded
  generic classes (`class Box[T: Bound]`). The H6 repro surfaced TWO
  bugs F4 had missed: (1) the literal entry -- `_resumable_frame_ctx`
  not passing `record_type_param_bounds` to `setup_body_scope` (body
  emit's `current_type_param_bounds` empty -- mostly latent, but breaks
  move-only `T: Iterator[X]` params and silently skips for-loop strategy
  peepholes); (2) the actual user-visible blocker -- the resumable
  factory's out-of-class member definition (`Cls<T>::method() const`)
  used `template <typename T>` instead of the constrained form,
  non-equivalent to the in-class declaration's `template<Iterable<int32_t>
  T>` -> hard C++ constraint mismatch. Fix: extracted a shared
  `protocols.gen_record_template_parts` (refactor of
  `gen_record_template_header`), added `gen_async._record_template_parts`
  that folds in the record's `type_param_bounds` / `type_param_kinds`,
  and routed `_emit_template_header` + `_emit_member_template_headers`
  through it. Bug (1) was the literal one-line `setup_body_scope` thread.
  Tests: `iterators/gen_method_bounded_on_generic_class`,
  `async/async_method_bounded_on_generic_class`, plus
  `{iterators/gen,async/async}_method_native_iterable_bound` as
  regression guards for the bounds-threading (catch the begin/end
  peephole in a non-suspending body for-loop on a `NativeIterable`-
  bounded T -- without Fix B the for-loop falls back to the universal
  `::tpy::__iter__` shape). Closed the second half of BUGS:240 in the
  same arc: `_resumable_frame_ctx` now save/restores `pointer_locals`
  AND `current_type_param_bounds` so a future caller that nests sync
  body emission around a coro won't see inner-coro state leak out.

### Low priority -- skippable indefinitely

- **H7 -- multi-item `async with X as a, Y as b:`.** Workaround is
  documented (nest two `async with`); skippable until a real workload
  needs it.

### Out of Phase H scope (new surface, separate phases)

- **G -- `yield from` / `send()` / `throw()` / `close()`.** Largest
  user-visible feature gap in Python-generator support; incremental on
  the resumable frame rather than from scratch.
- **Async generators (PEP 525).** `async def f() -> AsyncIterator[T]:
  yield ...` -- combines async + generator semantics. Distinct feature.
- **`@contextlib.contextmanager`.** Generator-based context managers
  via a stdlib decorator; tracked in TODO.md.

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

## Phase F approach (scoped 2026-05-27)

**Classification: architectural.** Reach full parity with the legacy
struct path, then delete it -- leaving one struct-shape codegen (the
resumable frame) plus the untouched simple-generator peephole.

### Key finding that reshaped the original framing

The original "Revised sequence" treated generic multi-yield as a Phase G
item ("NOT free", section 6) because reusing the async machinery for
generics was unproven at the time. It is now confirmed: the async
resumable path **already fully supports generics and methods**. Concretely
in `gen_async.py`: `_emit_template_header`, `_struct_name_templated`,
`_protocol_template_parts` (type params + static-protocol-typed params);
the `if func.type_params:` branch in `generator.py` (~line 1182) emits the
coro **struct + poll body + factory all inline in the header** for template
async coros -- exactly the shape a generic generator needs; and
`gen_coro_poll_def(record_name=...)` already handles async methods.

Therefore the generic-generator rejection (`gen_generators.py` ~line 692,
"generic generator functions with multiple yield points are not yet
supported") and the "out-of-line `__next__` won't link for templates"
limitation are **legacy-path-only**. Generic free-function generators on
the resumable path are machinery-*reuse*, not machinery-*building* -- much
cheaper than the original plan assumed, so generics moves into F.

### Decisions (user-approved)

1. **Generics in F (F2 in scope).** Reuse the proven async-template
   inline-header emission; closes the generic-multi-yield gap (the
   `gen_generators.py` "not yet supported" reject + the TODO.md feature
   entry, not a BUGS.md entry).
2. **Generic-class methods (F4) initially deferred, completed later.**
   Originally kept the rejection (shared with async -- both shapes
   rejected identically, so no regression). F1/F2 preserved the
   forward-compat constraint below. F4 then lifted the rejection by
   widening the single-source-of-truth templated-coro predicate to fold
   in the enclosing record's `type_params`.
3. **Delete the legacy struct path once parity is proven (F5).** The
   simple-generator peephole stays.

### Sub-phases (F1/F2/F3 independent; then F5; F6 cleanup; F4 standalone)

- **F1 -- Generator methods onto the resumable path.** Today every
  non-simple generator *method* routes to `gen_generators.gen_generator_*`
  unconditionally (`generator.py` ~lines 489, 1036, 1192); the resumable
  gate is consulted only for free functions. Route non-generic generator
  methods through `gen_coro_struct`/`gen_coro_poll_def`/factory-method under
  `ResumableShape.GENERATOR` with `record_name`, mirroring the async-method
  emission seams (which already exist and work). **Reuse the async method
  seams rather than inventing generator-specific method plumbing** -- this
  is also what keeps F4 cheap later.
- **F2 -- Generic free-function generators onto the resumable path.** Lift
  the `func.type_params` early-out in `_compute_resumable_generator_eligible`
  (`generator.py` ~line 566); route generic generators through the
  async-template inline-header branch (mirror `generator.py` ~lines
  1182-1188 for `GENERATOR` shape: struct + poll + finally-top + factory all
  in the header); wire `_protocol_template_parts` for the generator shape
  (protocol-typed params); drop the `gen_generators.py` generic-multi-yield
  `SemanticError`. The CFG trial-build is type-agnostic (control flow only),
  so the eligibility gate works unchanged once the type_params early-out is
  removed.
- **F3 -- Tuple-unpack `for` with a suspension.** Frame-store the
  destructured unpack targets so a target read across a `yield`/`await`
  reads a live frame field (D2 follow-up, TODO.md). Removes the last
  correctness-driven gate exclusion (`_stmts_have_tuple_unpack_for_with_suspension`).
  Likely shared with the latent async tuple-unpack-for shape noted in
  TODO.md.
- **F5 -- Retire the legacy struct path.** After F1+F2+F3 land and the full
  regression net is green, delete the struct-mode functions in
  `gen_generators.py` (`gen_generator_struct`, `gen_generator_next`,
  `_gen_generator_body`, the loop-body / for-strategy helpers that the
  resumable path now owns via the ported `_analyze_for_strategy`). Keep
  `is_simple_generator`, `gen_simple_generator_inline`, and the
  `_iter_slot_for_yield` helper that the resumable path borrows. ~500-600 of
  ~948 lines removed. The eligibility gate collapses: anything not a simple
  generator routes to resumable (no more trial-build fallback to legacy,
  since there is no legacy struct path to fall back to) -- so the gate
  becomes "simple peephole vs resumable", and `_CFGNotYetSupported` shapes
  (if any remain) must be made to either compile or raise a clean
  `SemanticError` rather than silently fall back.
- **F6 -- "Extract shared base" reassessment.** The original plan's
  premise ("two struct consumers exist, extract a base") **dissolves after
  F5**: there is then exactly ONE struct consumer (the resumable frame) plus
  the simple peephole, which shares almost nothing structural. So F6 is
  expected to reduce to internal cleanup rather than an inheritance split:
  consolidate the `ResumableShape` branches in `gen_async.py`, and land the
  `ResumableFuncState` typed-dataclass refactor (TODO.md -- collapse the
  ~dozen `getattr(func, "_resumable_*", None) or {}` side tables) which
  pairs naturally with this phase and de-risks the eventual THIR migration.
- **F4 -- generator/async methods on generic classes.** (DONE; see the
  F4 DONE log entry above for the implementation record.) The F1/F2
  forward-compat constraint -- keep the per-method
  out-of-line-vs-inline-header choice parameterized and shared between
  async and generator shapes, with `_reject_method_on_generic_class` as
  the single rejection chokepoint for both -- held, and F4 was a
  natural extension: widen the templated-coro predicate to fold in the
  enclosing record's `type_params`, then lift the shared rejection.
  Methods on generic classes route to the inline-header branch
  automatically once the widened predicate fires, benefiting async and
  generators together. The record-template-args plumbing
  (`_record_template_args`, threaded `record_name` parameters on
  `_is_templated_coro` / `_emit_template_header` / `_struct_name_templated`
  / `_classify_params`'s self-capture) stays generator-agnostic.

### Sequencing + gate

F1, F2, F3 are mutually independent and can land in any order (each
regression-net-green on its own slice). F5 depends on all three. F6 follows
F5. F4 is standalone and out of this phase's required scope. The
byte-identical-async gate continues to apply to every step that touches the
shared `gen_async.py` emit.

### Tests

Per-step regression slices in `tests/cases/iterators/*` and
`tests/cases/tuple/tuple_optional_yield_*`. New cases: a generic
free-function multi-yield generator (F2), a generic generator with a
protocol-typed param (F2), a generator *method* with multi-yield /
control-flow (F1), a tuple-unpack-for generator that reads a target after a
yield (F3 -- the `gen_tuple_unpack_use_after_yield` shape, now flipped to
the resumable path). After F5, confirm no snapshot still references a
`__gen_` struct from the deleted path (every non-simple generator now emits
the `__coro_`-shape struct).

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
