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
  frame). Next: **Phase D** -- widen the gate (loops incl. the for/while-
  else `__loop_broke` fix + for-loop peephole parity, tuple-unpack
  frame-field handling), then try/finally, then params/generics.
- **Phase D/E prerequisites (surfaced by /tpy-review):**
  - Borrow-form yield slot: `_resumable_ret_type_cpp` currently uses
    storage form (`type_to_cpp`), correct only for non-tuple yields; emit
    the borrow-form iterator slot (parity with gen_generators'
    `_iter_slot_for_yield`) so the gate's non-tuple-yield restriction can
    be lifted.
  - Wire `gen_coro_finally_top_def` into the generator orchestration
    slot in `generator.py` before widening the gate to try/with
    (Phase E) -- finally helpers aren't emitted on the generator path
    today (latent; gate excludes try/with).
  - De-async-ify the shared plumbing: rename `_build_cfg` ->
    `_build_resumable_cfg` and make the `_prescan_async_*` /
    `_async_*`-named caches shape-neutral (they run for generators too).
- Phases D-G: not started.

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
