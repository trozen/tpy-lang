# Async/Await v1 implementation progress

Tracking implementation of the v1 design from `docs/ASYNC_DESIGN.md`. Each
section captures what shipped, what's next, and any decisions made along the
way.

## PR 1: Sync try/finally codegen rewrite (DONE)

Commit: `codegen: unify try/finally and `with` lowering on a single
inline-emit pattern`.

Replaced the goto-based finally dispatch (per-action labels, shared __retval
optional, needs_*_copy bookkeeping) with the unified shape

    try { body } catch (...) { <finally>; throw; } <finally>;

with `return` / `break` / `continue` inside the try body emitting the chain
of enclosing finally bodies inline before the actual exit. Same shape will
drive the resumable-frame async codegen in PR 3 -- each suspension's case
body re-establishes the active try/finally structure using the same emit
helpers.

Notable side effect: fixed a latent bug where an `@error_return`
propagate-out from inside a try-with-finally skipped the finally body
(regression test: `cases/error_return/auto_propagate_finally`).

## PR 2: Async runtime types + sema plumbing (DONE)

Slice A landed:

- C++ runtime types in `runtime/cpp/include/tpy/async.hpp`: `tpy::Poll<T>`
  (with void / reference / move-only / non-default-constructible
  specializations), `tpy::Waker` POD, `tpy::CancelledError` :
  `BaseException`. `Waker::wake()` is now hooked up by the PR 6
  executor.
- TPy stubs in `lib/tpy/tpy/_builtins/_exceptions.py` for `CancelledError`
  (re-exported from `tpy`).
- `is_async: bool` on `TpyFunction`; parser accepts `async def`, parses
  `await` as `TpyAwait` expression node.
- Sema rejection rules:
  - `await` outside `async def` -> "only allowed inside async def" (sema).
  - `async def` body -> "async def codegen not yet implemented" (sema)
    until PR 3 lands.
  - `async def` + `yield` -> "async generators not yet supported" (parse).
  - `async def` + `@error_return` -> "not yet supported" (parse).
  - `async def` + `@noalloc` -> "not yet supported" (parse).
  - `async def` + `@native` / `@export` -> "not yet supported" (parse).
  - async methods on classes / protocols -> "not yet supported" (parse).
  - nested `async def` -> "not yet supported" (parse).
- Test cases under `tests/cases/async/` validating each rejection path.
- Side fix in `tpyc/codegen_cpp/expressions.py` (static-method-on-generic
  cpp_template paths) so `inferred_type_args` flow into template
  expansion -- needed by future async constructors and a general
  improvement for any generic static method.

### Deferred from PR 2 (intentional)

- `Awaitable[T]` / `Waker` / `Poll[T]` as user-facing TPy types.
  Initial attempt hit type-system integration friction (parser placeholder
  qname mismatch, type_def_registry registrations needed, generic-class
  static-method inference). Defer the user-facing surface to PR 3, where
  async-def codegen produces these types directly via codegen-emitted C++
  and the integration story is concrete.
- "User-defined `__await__` rejection" sema rule. Not strictly necessary
  for v1 sema correctness; will land alongside PR 3's `__await__`
  resolution path.

## v1 STATUS: SHIPPED

The v1 async surface is implemented end-to-end. User programs of the
shape

    import asyncio

    async def fetch(x: int) -> int:
        return x * 2

    async def main_coro() -> None:
        a = await fetch(5)
        b = await fetch(10)
        await asyncio.sleep(0.1)
        print(a + b)

    asyncio.run(main_coro())

compile and run on TPy and CPython both. See sections below for what
exact slice ships in v1 and what's deferred.

## PR 3 SHIPPED -- Resumable-frame codegen + `async def` lowering

This is the load-bearing piece. The plan below captures everything I
learned in PR 2 plus a concrete sequencing for PR 3 implementation. The
existing generator codegen (`tpyc/codegen_cpp/gen_generators.py`) is the
closest reference for the shape of the state-machine struct.

### Step 0: User-facing TPy types

Re-introduce `Awaitable[T]` / `Waker` / `Poll[T]` as user-visible types,
properly integrated with the type system this time:

1. Add `register()` calls in `tpyc/type_def_registry.py`:
   - `tpy.Waker` (TC.STRUCT or new TC.ASYNC_PRIM, is_value_type=True)
   - `tpy.Poll` with `param_kinds=(TYPE,)` and a `make_poll(t)` factory
   - `tpy.Awaitable` is structural, no registration needed
2. Add singletons / factories in `tpyc/typesys.py`:
   - `WAKER = NominalType("Waker", (), _module_qname="tpy.Waker")`
   - `make_poll(t) -> NominalType("Poll", (t,), _module_qname="tpy.Poll")`
3. Re-add stubs in `lib/tpy/tpy/_core/_async.py` (or back in `_types.py`
   if simpler, with native_module directive) plus CPython stubs in
   `lib/cpy/tpy/__init__.py`. The PR 2 attempt is preserved in commit
   history -- start from there but with the registry-backed type lookup.
4. Verify: a hand-written `class Foo: def poll(self, w: Waker) -> Poll[T]: ...`
   compiles and conforms to the structural Awaitable[T] check.

The "type mismatch: Poll[Int32] != Poll[Int32]" issue I hit in PR 2 was
caused by parser placeholder qname=None vs sema-resolved qname='tpy.Poll'
not collapsing on equality. The registry registration is exactly the fix
because it gives the parser a known qname for the canonical type.

### Step 1: AST + sema annotations for `await`

- For each `TpyAwait` node, sema records:
  - `expr.suspension_index: int` -- ordinal among suspensions in the
    enclosing `async def` body.
  - `expr.awaited_coro_struct: NominalType | None` -- if the awaitable
    type resolves to a concrete coroutine struct (free `async def` call,
    method on concrete record, typed coroutine field), the parent frame
    will inline that struct as a sub-future field. Otherwise, treat as
    type-erased (Box / Task heap allocation).
  - `expr.parent_function: TpyFunction` -- back-pointer for codegen.
- Sema collects per-async-def:
  - `func.suspension_count: int`
  - `func.async_locals_to_hoist: list[(name, type)]` -- locals live across
    at least one suspension (mirrors `func.generator_locals`).
  - `func.try_stack_at_suspension: list[list[FrameInfo]]` -- list of
    enclosing try/except/finally frames at each suspension point. This
    is the data the resumable-frame codegen needs to re-establish each
    case body's source-level structure.

The `try_stack_at_suspension` machinery is the load-bearing data
structure. Compute it during a fresh per-async-def sema pass (after
body analysis) by walking the AST and tracking enclosing TpyTry /
TpyWith frames. The same shape will eventually replace generator
codegen's implicit context tracking when generators migrate (deferred).

### Step 2: Resumable-frame abstraction

New module `tpyc/codegen_cpp/gen_async.py` (modeled on
`gen_generators.py`). Don't extend gen_generators -- the design
explicitly says these stay separate so the LLVM-backend path is
preserved.

The emitter produces, per `async def f() -> T`:

```cpp
struct __FCoro {
    int32_t __state;
    bool __cancel_pending;
    // captured params (per parameter capture rules in design doc)
    // hoisted locals (set on the AST by sema)
    // sub-future fields: std::optional<SubCoroStruct> __sub_0, __sub_1, ...
    //   for each statically-known awaited type
    // type-erased sub-future fields: std::unique_ptr<AsyncFrameBase<U>> __sub_N
    //   for each dynamic-awaitable site (Awaitable[U] / Task[U] / unions)

    // private: extracted finally helpers
    void __finally_outer();  // each unique source-level finally body
    void __finally_inner();  // emitted once, called from each case

    enum { S_INITIAL = 0, S_AFTER_AWAIT_0 = 1, ..., S_DONE = K };

    __FCoro(/* captured params per design "Parameter capture" rules */);

    tpy::Poll<T> poll(tpy::Waker waker) {
        switch (__state) {
        case S_INITIAL:
            try {
                // body up to first suspension; emit sub-future field
                // assignment, advance state, fallthrough
            } catch (...) {
                // wrap the active try/finally re-establishment
                __finally_outer();
                throw;
            }
            [[fallthrough]];
        case S_AFTER_AWAIT_0: {
            try {
                if (__cancel_pending) { __cancel_pending = false; throw tpy::CancelledError(); }
                auto r = __sub_0->poll(waker);
                if (r.is_pending()) return tpy::Poll<T>::pending();
                // bind result, reset sub-future, advance state, fall through
                <result_var> = std::move(r).value();
                __sub_0.reset();
                __state = S_AFTER_AWAIT_1;  // or S_DONE
                // body between this and next suspension
            } catch (...) {
                __sub_0.reset();
                __finally_outer();
                throw;
            }
            [[fallthrough]];
        }
        // ... more cases ...
        case S_DONE:
            tpy_panic("poll after Ready");
        }
    }
};
```

Critical sub-tasks:

- **State integer** lowering: each suspension gets an enum value. `INITIAL`
  is 0, suspensions in source order are 1..K, `DONE` is K+1. After a
  Ready return, set state to DONE.
- **Hoisted locals**: every local that crosses at least one suspension is
  a frame field. Locals confined within a single (case, case+1) interval
  stay as case-body locals.
- **Sub-future fields**: `std::optional<SubCoroT>` for statically-known
  inlined sub-coroutines; `std::unique_ptr<AsyncFrameBase<U>>` for
  type-erased ones (uses Task's poll-box machinery, which lands fully in
  PR 4 -- start with a minimal version sufficient for the dynamic-await
  cases that arise here).
- **Per-suspension try/except/finally re-establishment**: each case body
  is wrapped in the C++ try/except/finally structure that was active at
  the corresponding source-level suspension. This is the "unified"
  pattern from PR 1 -- the same `_emit_finally_chain` / inline finally
  helpers should be reusable. Generators already collect their own
  enclosing-try state; the data flow is similar but the consumer is the
  case body, not a goto label.
- **Finally-body deduplication**: each unique source-level finally body
  is emitted as a private member function; case bodies call them.
- **`cancel_pending` flag**: declared on the frame, default-initialized
  false. At each resumed-await position (inside the case body's try
  wrappers), check and throw CancelledError if set. Cancellation
  delivery itself (the actual `Task.cancel()` -> `frame.cancel()` ->
  `__cancel_pending = true` plumbing) lands in PR 5 alongside Task[T].
- **Frame cleanup on caught exceptions**: every `catch (...)` that wraps
  an in-flight `await` site does `__sub_n.reset()` as the first action
  before any user handler runs.

### Step 3: `async def` as a TpyFunction-shaped emitter

- Recognize `func.is_async` in the function codegen entry point
  (`gen_function_def` in `tpyc/codegen_cpp/functions.py`); dispatch to
  the new `gen_async.py` instead of regular function lowering.
- Emit a struct (per-async-def, like generators) plus a free function
  that is the user-visible name; the free function builds and returns
  the struct (matching the `Coroutine[T] = Own[__FCoro]` model).
- For each async def `def f(p1: T1, p2: T2) -> T`:
  - The "body" sema sees an `async def` body and is short-circuited
    today (PR 2's "not yet implemented" diagnostic). Lift that guard
    once codegen is ready.
  - The struct is emitted in the .hpp (templates always inline; for
    non-template async defs the body could move to .cpp eventually --
    start with always-in-hpp for simplicity).
  - The "free function" `T f(args)` is replaced by `__FCoro f(args)` in
    a new dispatcher that constructs and returns the coroutine struct.

### Step 4: `await` lowering at call sites

- For each `TpyAwait` inside an async def body:
  - If the awaited value's type resolves to a concrete coroutine struct
    (statically known): emit a sub-future field assignment + `poll()`
    call sequence; bind the Ready value to the `await` expression's
    result slot.
  - Otherwise: heap-allocate the awaitable as a `Box<dyn Future>` /
    `unique_ptr<AsyncFrameBase<U>>` and poll through that. Slice B may
    skip this case and only support statically-known awaits; the dynamic
    case can land alongside Task in PR 4.

### Step 5: Outside-async-def diagnostics

- Stay rejected: `await` at top level (sema already errors).
- Stay rejected: nested async def, async methods (parse already errors).
- Lift the global "async def codegen not yet implemented" guard in
  `_analyze_function`.

### Step 6: Tests

- Smallest case: `async def f() -> int: return 42` -- compiles to a
  struct with single-state poll. Awaited via a hand-rolled poll loop in
  a test (no executor yet).
- Two-step case: `async def caller(): return await f() + 1` -- two state
  case, sub-future field for f's struct.
- try/finally-around-await: validates per-suspension re-establishment.
- await-inside-loop: at least one suspension inside a `while`.

### Open questions for PR 3 implementation

- Where exactly does `func.try_stack_at_suspension` get computed? Best
  candidate: a fresh sema pass after body analysis but before codegen.
  Generators today do something similar implicitly; the pattern can be
  adapted.
- How does the heap-allocated dynamic-await case interact with PR 4's
  Task type-erasure? Pin the shared `AsyncFrameBase<T>` virtual base in
  the runtime now (probably in async.hpp) so PR 3 and PR 4 use the
  same vtable layout.
- Generator migration: defer per design. But the resumable-frame helpers
  (`emit_finally_helper`, `try_stack_at_point`, hoisted-local
  collection) should be factored out of `gen_async.py` as reusable
  utilities so generators can adopt them later without redesign.

## PR 4 SHIPPED -- Task[T] poll-box

`tpy::AsyncFrameBase<T>` virtual base + `tpy::AsyncFrameImpl<T, CoroT>`
wrapper. `tpy::Task<T>` shares a `TaskState<T>` with the executor when
spawned; the state owns the `unique_ptr<AsyncFrameBase<T>>`, caches
completion values/exceptions, and stores the current awaiter waker. One
virtual call per poll, the same indirection any type-erased poll-box
pays. `Task<T>::from_coro` builds the heap-allocated frame.

`tpy.Task` registered in the type system (`is_value_type=True` so
move-only Task locals work in sync functions). User code can hold and
move Task values; awaiting a Task[T] dispatches via the
type-erased path (sub-future field is `optional<Task<T>>`).

`Future[T]` manual-completion awaitable ships in v1 as a single-awaiter
reference-type awaitable. Multi-awaiter Future is a v2 feature.

## PR 5 SHIPPED -- Cancellation

`Task.cancel()` flips `__cancel_pending` on the held coroutine frame
(via the AsyncFrameImpl override). The next poll's resume case checks
the flag, throws `CancelledError`, which propagates through the case
body's catch wrapper -- which resets in-flight sub-futures and runs
the wrapper try-finally (when present) before re-throwing.

`tpy::CancelledError` inherits BaseException directly, matching CPython
3.8+ so `except Exception:` does not silently swallow it.

`task_poll_cancelled<T>(Task<T>&)` is a runtime convenience for tests:
polls and returns true iff CancelledError escaped.

## PR 6 SHIPPED -- asyncio v1 library + concurrent scheduling

`tpy::Executor` is a runnable-queue + timer-min-heap driver. Main and
spawned tasks live in indexed slots; `Waker::wake()` marks the matching
slot runnable when the slot generation still matches. Timers store the
parked task's waker, so sleep wakes only the task that awaited it.
`current_executor` is thread-local; awaitables that need timer access
consult it on poll.

`asyncio.run(coro)` lowers to `tpy::async_run` -- sets up the executor,
schedules the top-level coro as the main executor slot, then drains the
runnable queue and sleeps until timer wakers fire. Panics with a clear
message if no runnable task and no pending timer can make progress (v1
has no I/O reactor; that's the v1 contract).

`asyncio.sleep(seconds)` returns a Task[None] wrapping a SleepFuture
that registers its deadline with the current executor and returns
Pending until `now() >= deadline`. Real wall-clock timing.

`asyncio.create_task(coro())` registers the task with the current
executor and returns a `Task[T]` handle that shares the underlying
`TaskState<T>` with the executor's wrapper. The executor owns polling
for spawned tasks; the user's `await task` parks its waker on the shared
state and reads the cached result/exception after completion. Tasks
created without a user-held handle still run to completion as long as
the main coro is also pending. Tests:
- `tests/cases/async/asyncio_concurrent_tasks/` -- two `create_task`'d
  doublers run their sleep concurrently (both "pre" prints land before
  either "post").
- `tests/cases/async/asyncio_fire_and_forget/` -- a task whose handle
  is dropped before completion still runs to completion via the
  executor's spawned-list reference, as long as the main coro yields
  long enough.

`Future.set_result(...)` / `set_exception(...)` wake the stored waiter,
so a spawned task can unblock a coroutine awaiting the same Future
without broad re-polling. `Executor::wait_for_event` is strict: returns
false (caller panics with "no progress possible") if there are no
timers, so a coro parked on a Future with no setter and no timers
surfaces as a clear panic rather than spinning.

Task<T> internals: `shared_ptr<TaskState<T>>` (was `unique_ptr<frame>`).
TaskState owns the frame, caches result/exception once done, and is
single-awaiter for v1 (a second user-poll after Ready panics).

When `async_run` exits (main coro Ready, panic, or unhandled exception),
remaining spawned tasks are cancelled and drained: each gets
`__cancel_pending = true`, then we poll N times so the next resume case
body observes the flag and throws `CancelledError`. The wrapper-try's
`__finally_top()` runs on the way out so user cleanup code executes.
Bounded to 8 polls to avoid spinning if a hand-rolled awaitable ignores
the flag. Test: `tests/cases/async/asyncio_drain_finally/`.

Deferred to v1.x:
- Slot reuse/compaction for the executor task table (currently dead
  slots remain allocated; generations already make stale wakers safe).
- Warning/reporting when bounded cancellation drain leaves a task
  pending (today the task is silently dropped after the fixed poll
  budget).
- I/O reactor (epoll, etc.) -- v2.

## What's deferred from v1 design

- **async with / async for**: v1.5, blocked on the sync `with` upgrade
  to support `__exit__(exc_type, exc_val, exc_tb)`.
- **gather / wait_for**: v1.5.
- **General `except` handlers around `await`**: v1.5. The narrow v1
  shape `try: <single top-level await>; except E: ...` is supported for
  throw-tier exceptions, including catching `CancelledError`; arbitrary
  statements, `else`, `finally`, nested handlers, and multiple awaits
  under the same try still need the region-with-try-stack lowering.
- **Nested or partial try/finally around `await`**: v1.5. Only the
  wrapper-try shape (entire async-def body wrapped in one
  `try:` ... `finally:` with no `except` handlers) is supported.
  Per-suspension re-establishment of arbitrary try/except/finally
  needs the case-body algorithm in PR 3 plan section 7.1
  (region-with-try-stack codegen) -- non-trivial, deferred.
- **Awaits inside if/while/for/with sub-bodies, and general try bodies**:
  v1.5. Currently rejected with a clear "bind the await to a local
  before the control-flow statement" diagnostic, except for the narrow
  try/except shape above.
- **I/O reactor**: v2.

## Future v1.x / v2 work

## v1.5 milestone

### M1 SHIPPED -- Sync `with` upgrade to 4-arg `__exit__`

Prerequisite for `async with` (which inherits the same suppression contract).
Hard cutover -- no compat shim for the old 1-arg shape.

- Parser (`tpyc/parse/parser.py`): stop stripping the 3 exception params on
  `__exit__`; unannotated slots get synthesized `TypeRefNode`s --
  `exc_type` / `exc_tb` -> `None` (`std::monostate`), `exc_val` ->
  `Optional[BaseException]` (`const BaseException*`).
- Sema (`tpyc/sema/registration.py`): validate the `__exit__` signature
  -- return must be `bool` or `None`; `exc_type` / `exc_tb` must be `None`;
  `exc_val` must be `None` or `Optional[BaseException]`. Per-with-item
  flags `exit_can_suppress` and `exit_takes_exc_val` flow to codegen +
  liveness.
- Sema (`tpyc/sema/calls.py`): `isinstance(exc_val, X)` on
  `Optional[BaseException]` emits a targeted v1.5 diagnostic pointing at
  `if exc_val is not None:` (class-based dispatch deferred to M2;
  exception slicing through `Optional[BaseException]` would make
  `dynamic_cast`-based isinstance silently wrong).
- Codegen (`tpyc/codegen_cpp/statements.py::_emit_with_try_catch`):
  three-way catch -- `catch (BaseException& __exc)` with optional
  `if (!__exit__(...)) throw;` suppression check (when `__exit__ -> bool`),
  `catch (...)` for foreign exceptions (best-effort cleanup, always
  rethrows), plus normal-path `__exit__({}, nullptr, {})` on
  fall-through / return / break / continue. Call site varies the exc_val
  arg shape based on the user's annotation (`{}` for `None`,
  pointer/nullptr for `Optional[BaseException]`).
- Liveness (`tpyc/liveness.py`): a `with` whose `__exit__` can suppress
  no longer terminates on body-termination alone -- the bool could be
  `True` and control would fall through.
- Runtime (`runtime/cpp/include/tpy/file.hpp`): `TextFile::__exit__` and
  `BinaryFile::__exit__` now take the typed 3-arg signature.
- Tests: 5 new `tests/cases/control_flow/with_exit_*` cases covering
  suppress / no-suppress / normal-path / bad return / isinstance
  diagnostic. Snapshots regenerated across all `with`-using cases.

Deferred to M2: class-based exception dispatch via
`isinstance(exc_val, X)`. Requires either fixing exception slicing
through `Optional[BaseException]` at value-conversion boundaries, or
restricting dynamic_cast to known-polymorphic sources (catch blocks).

## v1.x milestone: asyncio runtime TPy port (must precede v1.5)

The v1 asyncio runtime (Executor + run loop + spawn registration + sleep
timer) is implemented in C++ for v1 shipping speed. The design doc
specifies the executor as `~150-200 lines of TPy`; that port is a v1.x
milestone, not deferred indefinitely. **Must land before v1.5 starts**:
`gather`, `wait_for`, `async with`, `async for` all want to be TPy code,
and porting them on top of a still-C++ executor would mean writing more
C++ template machinery only to throw it away.

### Phase 0 -- DONE

`SleepFuture` ported to TPy (`lib/tpy/asyncio/__init__.py`). C++ keeps a
single bridge helper `executor_register_timer_seconds(deadline_seconds,
waker)` that hides the executor pointer + chrono details. Validates the
shape: TPy class with `__cancel_pending` field passes through the parser
without name mangling, and `Task<std::monostate>::from_coro<TPyClass>`
(`Task<void>` at the time -- migrated to `std::monostate` by the
position-aware-None fix) works for TPy-emitted struct CoroT.

Also rehomed the user-facing async primitives to `tpy.coro` (canonical
home, mirrors Rust `std::task` / Tokio `tokio::task`):
  * `Task[T]`, `Waker`, `Poll[T]`, `Awaitable[T]` (compiler-known
    types -- qnames updated to `tpy.coro.Task` / `tpy.coro.Waker` /
    `tpy.coro.Poll` / `tpy.coro.Awaitable`).
  * `poll_ready[T]`, `poll_pending[T]`, `poll_ready_none`, `poll_once[T]`.

`tpy/__init__.py` re-exports them for back-compat (`from tpy import
Task` keeps working). `CancelledError` stays in
`tpy._builtins._exceptions` alongside the rest of the exception
family. `Future[T]` and `InvalidStateError` are conceptually
async-runtime primitives but stay in `lib/tpy/asyncio/__init__.py`
for v1 by convention; asyncio re-exports them as `asyncio.Future` /
`asyncio.InvalidStateError`. (The earlier blocker -- sub-modules of
`tpy/` couldn't call `tpy.copy` -- has been fixed by routing the
relevant `@builtin_function` codegen via the resolved function's
qname rather than the user-facing import path, so the placement is
no longer load-bearing.)

### Phase 1 -- Compiler bindings (prerequisite) -- DONE

All compiler bindings the rest of the port needs are landed. None
required new compiler features.

- `time.sleep_until_steady(deadline_seconds)` -- DONE. Wraps
  `std::this_thread::sleep_until` for a steady_clock-seconds deadline
  (matching `time.monotonic()`'s domain). Lives in
  `runtime/cpp/include/tpy/stdlib/time.hpp`; bound from
  `lib/tpy/time.py`. Lets the run loop wait for the next timer in TPy.
- `_get_current_executor()` / `_set_current_executor(e)` /
  `_clear_current_executor()` -- DONE. Thin getters/setters for the
  `thread_local Executor* current_executor` pointer, surfaced as
  `tpy::ExecutorHandle` (POD value type mirroring `Waker`). Bindings
  live in `lib/tpy/asyncio/_executor.py`. Storage type is `void*` so
  Phase 2's TPy-side executor pointer can ride on the same handle
  without changing the bridge signatures; only the casts in the C++
  bridges need swapping. Stays a C++ thread_local for now (TPy has no
  thread-local syntax); Phase 3 will wrap save/restore in a TPy
  `_ExecutorScope` class with `__del__`.
- `AnyTaskBox` -- DONE. `@native("tpy::AnyTaskBox")` wrapper around
  `shared_ptr<AnyTask>` (NOT `unique_ptr` as originally drafted: the
  user-facing `Task<T>` and the executor's slot table share the same
  heap state via `shared_ptr` so the fire-and-forget pattern works).
  Move-only on both sides; methods `poll_any` / `cancel_any` / `empty`
  / `reset` resolve as members of the @native struct. TPy code cannot
  construct a non-empty box today; Phase 2 will wire up a factory
  alongside the executor body port. Has a `TODO(async-v1.x)` to remove
  the @native wrapper once shared-ownership smart pointers land in TPy
  -- at that point `TaskState[T]` moves to TPy and the slot table
  holds a @dynamic protocol adapter directly.
- Timer slot type: plain `tuple[float, Waker]`, sorted via
  `heapq.heappush` / `heappop` on `list[tuple[float, Waker]]`. (The
  tuple<->Comparable conformance gap has been closed; no dedicated
  Timer class needed.) No new work.
- `task_poll_cancelled[T](t) -> bool` -- DONE (already shipped in
  Phase 0 under `tpy.coro` as a real TPy generic body; replaces the
  C++ template). Validated the binding plumbing on something safe.

Test: `tests/cases/async/executor_bindings_smoke/` -- smoke-tests
all bindings (parse + compile + link + behavior on the empty-box /
null-handle paths and the in-vs-out-of-asyncio.run handle distinction).
Full poll-an-AnyTaskBox-to-completion coverage waits for Phase 2's
factory wiring.

### Phase 2 -- Executor body to TPy

Staged leaf-up to avoid resolving the full C++-to-TPy dispatch problem
in one PR. The TPy `Executor` is implemented in
`lib/tpy/asyncio/_executor.py` as a parallel implementation to
`tpy::Executor`; runtime callers (`make_user_task`,
`executor_register_timer_seconds`, `Waker::wake()`, `async_run`) still
talk to the C++ struct. Phase 3 swings asyncio.run onto the TPy
executor; Phase 4 deletes the dead C++ struct. Keeps `make_user_task`,
`AnyTask`/`AnyTaskImpl`, `TaskState`, `Task` in C++ (see "Blocked"
below).

**Sub-step 2.1 (DONE) -- `Slot` leaf class.** Three fields
(`box: AnyTaskBox`, `generation: UInt32`, `runnable: bool`) and an
`is_done()` reflecting `box.empty()`. Test:
`tests/cases/async/executor_slot_smoke/`. Required removing
`# tpy: native_module` from `_executor.py` so the module generates
real codegen for the new class.

**Sub-step 2.2 (DONE) -- `Executor` TPy class.** All 9 methods land
with TPy bodies: `spawn`, `mark_runnable`, `poll_slot`,
`drain_runnable`, `slot_done`, `has_live_tasks`, `register_timer`,
`wait_for_event`, `run_until`, `drain_spawned_with_cancel`. Three
deviations from the doc:

- *Timer heap shape*: original Phase-2 workaround used
  `list[tuple[float, UInt64]]` paired with `dict[UInt64, Waker]`
  instead of `list[tuple[float, Waker]]` -- `heapq[T: Comparable]`
  requires tuple elements to satisfy Comparable, and `Waker` (a POD
  value type) doesn't. Consolidated in v1.2 step 3 into a single
  `list[TimerEntry]` where `TimerEntry` holds
  `(deadline: float, waker: Waker)` with `__lt__` on `deadline`
  (slot_id / generation live inside the Waker); the parallel dict
  and the explicit monotonic counter are gone.
  Resolution depended on the v1.2 step 3 fix for the
  `is_value_type<T>` spec-emission ordering bug, which previously
  blocked using a user `ValueType` record as a `list` element type
  in a generic context.
- *Waker construction*: a free helper `_make_waker(handle, task_id,
  generation)` lives next to the class. The TPy class can't host a
  `@cpp_template` method body (compiler restricts that to `@native`
  classes), and brace-initializer literals collide with cpp_template's
  `{...}` substitution syntax. A new C++ helper `tpy::make_waker` plus
  a templated `tpy::make_executor_handle<T>(T&)` keep async.hpp ignorant
  of the generated Executor's C++ class name.
- *`wait_for_event` initial implementation bypassed `waker.wake()`* in
  favour of `self.mark_runnable(_waker_task_id(w), _waker_generation(w))`
  because `Waker::wake()` dispatched into `tpy::Executor*` on the C++
  side. Phase 2.4 landed the ops-table dispatch (see below) and
  `wait_for_event` now calls `waker.wake()` directly; the
  `_waker_task_id` / `_waker_generation` accessors stay around for
  tests that want to inspect a waker's payload without dispatching.

**Sub-step 2.3 (DONE) -- Test-only AnyTaskBox factory.**
`_make_any_task_for_test[T](coro: Awaitable[T]) -> Own[AnyTaskBox]`
bound to a templated C++ helper that builds a `TaskStateImpl<T, CoroT>`
and wraps it. Lets tests drive the TPy executor without going through
`make_user_task` (which still requires a running C++ Executor). Phase 3
unifies this with the production create_task path.

Test: `tests/cases/async/tpy_executor_smoke/` -- spawn + drain +
run_until on a coroutine that returns immediately; multiple-spawn
fan-out; past-deadline timer fire mechanics; idle-drain + cancel-drain
no-op paths.

**Sub-step 2.4 (DONE) -- Waker dispatch via ops table.** Lets external
holders of a Waker (timer-fired wakers, `Future.set_result()` callers,
etc.) target a TPy-side Executor. Three changes:

- *`Waker.exec` type*: `Executor*` -> `void*`. Storage is opaque; the
  ops table knows how to interpret it. `Executor*` -> `void*` is an
  implicit conversion so existing `Waker{this, id, gen}` construction
  in C++ code continued to compile.
- *`tpy::ExecutorOps`* global function-pointer table holding a
  `mark_runnable` thunk. `register_executor_ops_from<ExecT>(ExecT&)`
  is a templated helper that wires a per-executor-type thunk into the
  table; the thunk casts `void*` back to `ExecT*` and forwards to its
  `mark_runnable` method. Idempotent for instances of the same
  `ExecT` -- repeated calls set the same function pointer.
- *`Waker::wake()` dispatch*: now goes through `executor_ops.mark_runnable`
  if it's been set; falls back to the legacy `static_cast<Executor*>(exec)
  ->mark_runnable(...)` cast otherwise. Phase 4 removes the fallback
  together with the C++ Executor struct.

`Executor.__init__` calls `_register_executor_ops_from(self)` so every
TPy Executor wires up dispatch automatically. Mixing C++ and TPy
Executors in the same process is unsupported (the last `__init__` wins
the ops slot) but doesn't arise in practice -- existing tests use one
or the other, never both. Phase 3 + 4 collapse this to a single
production code path.

Test: `tests/cases/async/tpy_executor_wake_dispatch/` covers
(i) external `waker.wake()` returning a parked slot to the runnable
queue, (ii) stale-generation wakes being silently dropped, and
(iii) a countdown coroutine driven to completion through repeated
register_timer + wait_for_event + `waker.wake()` cycles.

**Original doc sketch (kept for reference):**

- `slots: list[Slot]` where `Slot` is a TPy class wrapping
  `AnyTaskBox`, `generation: UInt32`, `runnable: bool`.
- `runnable_q: list[UInt32]` used as a deque (or a proper deque type
  if one lands).
- `timer_heap: list[TimerEntry]` driven via `heapq.heappush` /
  `heappop`. (v1.2 step 3 consolidation -- see 2.2 above.)
- Methods: `spawn`, `mark_runnable`, `poll_slot`, `drain_runnable`,
  `slot_done`, `has_live_tasks`, `wait_for_event`, `run_until`,
  `drain_spawned_with_cancel`. All pure TPy logic plus calls into the
  Phase 1 bindings.
- Mutation-during-iteration safety: same as in C++ today, indices into
  `slots`, no held references across `poll_any`.

### Phase 3 -- async_run to TPy + RAII scope guard -- DONE

Production asyncio now drives through the TPy `Executor`. Shape ended
up slightly different from the original sketch: `async_run` remains a
small C++ template shell (around 25 lines), but its body delegates the
run loop entirely to a non-generic TPy helper. Phase 4 collapses the
shell once a TPy-side compiler feature is in place.

**Sub-step 3a (DONE) -- ExecutorOps extension.** Added `SpawnFn` and
`RegisterTimerFn` slots to `tpy::ExecutorOps` alongside the
`MarkRunnableFn` from Phase 2.4. `register_executor_ops_from<ExecT>(self)`
now wires up all three thunks at TPy Executor construction. The
`make_user_task` and `executor_register_timer_seconds` C++ entry
points dispatch through the table (`current_executor` as opaque void*)
with the legacy `static_cast<Executor*>(exec)->method(...)` cast as a
fallback for the still-living C++ Executor case. Phase 4 removes the
fallback together with the C++ Executor struct.

**Sub-step 3b (DONE) -- async_run helpers.** Two new C++ template
helpers in `runtime/cpp/include/tpy/async.hpp`:

- `make_executor_owned_task<T, CoroT>(coro)` -- builds a
  `shared_ptr<TaskStateImpl<T, CoroT>>` with `executor_owned = true`
  and wraps it in a `Task<T>` without spawning. Lets the C++ shell
  keep its half of the shared TaskState for result extraction while
  TPy drives the slot table.
- `task_to_any_box<T>(task)` -- mirror the Task's underlying state
  into an `AnyTaskBox`. shared_ptr ref-count goes up by one; both
  views observe the same completion state.

**Sub-step 3c (DONE) -- `_ExecutorScope` RAII guard.** Wraps the
current-executor thread-local: `__init__` writes `executor`'s handle,
`__del__` clears it. v1 always starts from null (asyncio.run rejects
nesting at the call site) so no save/restore is needed; the class
could grow a `_prev: ExecutorHandle` field if nestable-runtime support
ever lands, but ExecutorHandle isn't currently storable as a TPy class
field because the codegen for `@native` value-type records emits the
wrong namespace in the `is_value_type` specialization. Recorded as a
follow-up TPy compiler fix.

**Sub-step 3d (DONE) -- `asyncio.run` body.** Shape:

```python
@cpp_template("::tpy::async_run({0})")
def run[T](coro: Awaitable[T]) -> T: ...   # stays cpp_template
```

with `tpy::async_run` reduced to:

```cpp
template <typename CoroT>
inline auto async_run(CoroT&& coro)
    -> decltype(/* ResultT */) {
    if (current_executor != nullptr) tpy_panic(...);
    auto task = make_executor_owned_task<ResultT>(std::forward<CoroT>(coro));
    {
        AnyTaskBox box = task_to_any_box(task);
        ::tpystd::asyncio::_run_drain_main_task(std::move(box));
    }
    if constexpr (std::is_void_v<ResultT>) {
        task.__poll__(Waker{}).value();
        return;
    } else {
        return task.__poll__(Waker{}).value();
    }
}
```

calling the non-generic TPy helper:

```python
def _run_drain_main_task(box: Own[AnyTaskBox]) -> None:
    executor = Executor()
    scope = _ExecutorScope(executor)
    main_id = executor.spawn(box)
    try:
        executor.run_until(main_id)
    finally:
        try:
            executor.drain_spawned_with_cancel(main_id)
        except BaseException:
            pass
```

**Why a C++ shell stays around the TPy body:**

- *void/T uniform return*: TPy compiles `def run[T] -> T` to a function
  template returning `val_or_ref_t<T>`, which substitutes to `void&` for
  T=None and fails. The C++ shell handles this with `if constexpr
  (std::is_void_v<ResultT>)`. A TPy-side fix would require either a
  void specialization in `val_or_ref_t` or a compiler change to handle
  generic-T-can-be-void returns more gracefully.
- *Coro consumption*: passing the just-returned coro rvalue through a
  TPy parameter chain loses rvalue-ness; the C++ shell forwards the
  rvalue directly into `make_executor_owned_task` so template deduction
  binds `CoroT` to a value type rather than a reference (which would
  trip `std::optional<CoroT>` inside `TaskStateImpl`).
- *Result extraction*: keeping the user-facing `Task<T>` handle alive
  past the run loop lets the shell call `task.__poll__(Waker{}).value()`
  to read the cached result (or rethrow the cached exception). The TPy
  helper receives an `AnyTaskBox` cloned from the same shared state.

**`Executor.poll_slot` reallocation-safety fix.** During Phase 3 testing,
`asyncio_drain_finally` (and any test where the main coro spawns a
nested task) segfaulted because `poll_slot` held a `Slot&` reference
across the recursive `poll_any` call. When the nested spawn pushed a
new slot, `slots` reallocated and the cached reference became dangling.
The fix re-indexes `self.slots[idx]` after the `poll_any` returns,
mirroring the same comment in the C++ Executor's `poll_slot`. Generic
TPy port lesson: any TPy code that drives a coroutine while holding a
reference into a container the coroutine might mutate has to re-index
after the call.

Tests: existing `asyncio_*` suite (50 cases) plus 2.2/2.4 smokes all
pass. No new Phase-3-specific test case -- the existing
`asyncio_drain_finally`, `asyncio_concurrent_tasks`,
`asyncio_fire_and_forget`, etc. exercise the new TPy run loop because
production `asyncio.run` now goes through it.

**Compiler items surfaced during Phase 3 (resolution status):**

1. `@native` value-type records emit `is_value_type` specialization in
   the module's own namespace instead of the renamed `@native` target
   namespace. **Resolved in v1.2 step 3** (`_emit_value_type_spec`
   per-record placement now respects `record_info.native_name` for the
   qualified target; a follow-up pass also covers native records that
   `sort_records_by_inheritance` filters out of the main emit loop, so
   generic-context use like `def f[T: ValueType](x: NativeT)` works).
2. `def f[T] -> T` returning `val_or_ref_t<T>` fails for `T = None`
   (void). **Resolved in v1.2 step 1** -- the `void`-specialized
   `val_or_ref_t` lives in `runtime/cpp/include/tpy/type_traits.hpp`,
   and `asyncio.run` is now pure TPy without a C++ shell.
3. TPy parameter coro values become const lvalues; deducing C++ template
   args from them yields `const T&` instead of a moveable rvalue
   reference. **Resolved in v1.2 step 2** -- `@cpp_template` arg
   generation now routes through `gen_call_arg(inline_template=True)`
   so the auto-move-at-last-use logic fires for `Own[T]` args.

### Phase 4 -- Cleanup -- DONE

What got removed from `runtime/cpp/include/tpy/async.hpp`:

- The `tpy::Executor` struct (the entire v1 runnable-queue +
  timer-heap driver, ~150 lines including the `Slot`, `Timer`, and
  `TimePoint` nested types). Production now uses the TPy `Executor`
  in `lib/tpy/asyncio/_executor.py`.
- The forward declaration `struct Executor;` at the top of the file.
- The legacy `static_cast<tpy::Executor*>(exec)->mark_runnable(...)`
  fallback in `Waker::wake()`.
- The legacy spawn-fallback in `make_user_task` (`current_executor
  ->spawn(state)`).
- The legacy register-timer fallback in
  `executor_register_timer_seconds` (the `Executor::TimePoint`
  conversion and `current_executor->register_timer(...)` call).
- The `current_executor` thread-local's `Executor*` declared type
  (now `void*` -- ops thunks know how to interpret it).
- The `tpy::waker_task_id` / `tpy::waker_generation` accessor helpers
  + their TPy bindings (originally added so Phase 2's pre-2.4
  `wait_for_event` could bypass `Waker::wake`; obsoleted when 2.4
  wired wake dispatch through the ops table).
- The `_make_executor_owned_task` and `_task_to_any_box` TPy bindings
  in `lib/tpy/asyncio/_executor.py` (unused since async_run's TPy
  helper switched to taking the box directly; the C++ helpers
  `make_executor_owned_task` and `task_to_any_box` stay in async.hpp
  because the C++ shell uses them).
- `<chrono>`, `<thread>`, `<queue>`, `<deque>`, `<vector>` includes
  in async.hpp (only the `Executor` struct used these).

What stays in C++ (after v1.1; `tpy::async_run` shell removed in v1.2,
see "v1.2 step 1" section below):

- `tpy::make_user_task<T, CoroT>` -- `asyncio.create_task` lowering.
  Dispatches via `executor_ops.spawn`.
- `tpy::executor_register_timer_seconds` -- SleepFuture's
  `_register_timer_at` bridge. Dispatches via `executor_ops.register_timer`.
- `tpy::make_executor_owned_task<T, CoroT>`, `tpy::task_to_any_box<T>`
  -- helpers used by the TPy `asyncio.run` function.
- `tpy::make_any_task_for_test<T, CoroT>` -- test-only factory used by
  `tests/cases/async/tpy_executor_smoke/` etc.
- `tpy::AnyTask`, `tpy::AnyTaskBox`, `tpy::TaskState<T>`,
  `tpy::TaskStateImpl<T, CoroT>`, `tpy::Task<T>` -- the type-erased
  task machinery. Each will move to TPy if/when the compiler features
  in the "Blocked" section below land.
- `tpy::Poll<T>`, `tpy::Waker`, `tpy::CancelledError`,
  `tpy::ExecutorHandle`, `tpy::ExecutorOps` -- primitives and runtime
  dispatch infrastructure.

All 50 async tests + the full 3719-test suite pass after the cleanup.

### v1.2 step 1 -- `val_or_ref_t<void>` + pure-TPy `asyncio.run` -- DONE

Closes the BUGS entry `def f[T] -> T returning val_or_ref_t<T> fails
C++ substitution for T = None`. Removes the `tpy::async_run` C++
template shell that was retained in Phase 3 specifically because TPy
couldn't express the void-uniform return-type shape.

Changes:

- `runtime/cpp/include/tpy/type_traits.hpp` -- refactored the three
  generic-T aliases (`val_or_ref_t`, `val_or_cref_t`,
  `param_val_or_ref_t`) to dispatch through `detail::*_impl` structs,
  then added void specializations so `val_or_ref_t<void> = void` (was
  `void&`, ill-formed). `param_val_or_ref_t<void> = void` is defensive
  (TPy doesn't emit `void` params in practice).
- `lib/tpy/asyncio/__init__.py` -- `run[T]` is now a pure-TPy generic
  with body: nested-run check (raises `RuntimeError`, was C++
  `tpy_panic`), `_make_executor_owned_task[T](coro)`,
  `_task_to_any_box[T](task)`, `_run_drain_main_task(box)`,
  `return task.__poll__(Waker()).value()`. The final `return` works
  uniformly for void and non-void T: today (post position-aware-None
  fix) the T=None case lowers to `Poll<std::monostate>::value()`
  returning `std::monostate{}` through the primary template -- v1.2's
  original argument relied on the `Poll<void>::value()` specialization
  + `val_or_ref_t<void>` and the literal `return void_expr;` form;
  both forms compile cleanly.
- `runtime/cpp/include/tpy/async.hpp` -- `tpy::async_run` template
  removed (~30 lines).
- `tests/cases/generics/generic_func_return_none/` -- new regression
  test exercising `def drain[T](aw: Awaitable[T]) -> T` with `T=None`
  via an awaitable returning `Poll[None]`. Locks in `val_or_ref_t<void>`.
- Snapshot drifts in 22 async tests: call sites changed from
  `::tpy::async_run(coro)` to `::tpystd::asyncio::run<T>(coro)`.

API shape: `run[T](coro: Own[Awaitable[T]])`. The `Own[]` annotation
expresses ownership transfer -- `asyncio.run` consumes the coro --
matching CPython's semantic intent. Originally the v1.2 step 1 port
also embedded a manual `std::move({0})` in
`_make_executor_owned_task`'s `@cpp_template` as a forwarding
workaround; v1.2 step 2 (below) made that redundant by routing
cpp_template calls through `gen_call_arg`'s auto-move logic.

Behavioural change worth noting: the nested-`asyncio.run` guard moved
from `tpy_panic` (uncatchable, process-terminating) to a `RuntimeError`
(catchable TPy exception). Matches CPython's behaviour ("asyncio.run()
cannot be called from a running event loop" is a `RuntimeError` there).
The `tests/cases/async/panic_run_reentry/` case was renamed accordingly.

### v1.2 step 2 -- cpp_template auto-move for `Own[T]` args -- DONE

`@cpp_template` calls previously generated args via `_gen_expr_deref`
(bare expression, no auto-move), so a `coro: Own[T]` local passed to a
cpp_template-bound consumer needed a manual `std::move({N})` in the
template string. They now route through `gen_call_arg(inline_template=
True)` -- the same helper `@native` calls already used -- so auto-move
at last use fires uniformly. Manual `std::move({N})` removed from
`_make_executor_owned_task` (asyncio) and `unsafe_init` (tpy.unsafe).
Snapshot drifts in `auto_move/consuming_method_*` and
`pointers/unsafe_alloc` (cleaner output, runtime identical).

Closes the codegen part of the old "Awaitable[T] rvalue forwarding"
bug. The remaining piece is a sema-diagnostic gap when a non-`Own`
borrow or a non-last-use `Own[T]` reaches a consuming cpp_template --
tracked in `BUGS.md` under Safety / borrow checker.

### v1.2 step 3 -- `is_value_type` emission ordering + timer_heap cleanup -- DONE

Fixed the two BUGS entries on `is_value_type<T>` specialization (the
old "non-`@native` ValueType emitted after instantiation" + "`@native`
value-type record specialized in wrong namespace"). Both shared a root
cause: the spec was emitted in a batch at file end, after inline
method bodies, using `{module_ns}::{cpp_name}` regardless of whether
the record was `@native`-renamed elsewhere.

Fix: moved the emission to right after the per-record class-decl
loop in `tpyc/codegen_cpp/generator.py` (`_emit_value_type_spec`),
and routed the qualified name through `record_info.native_name` when
present. Adds two regression tests
(`tests/cases/records/value_type_inline_method_use/` for the
ordering bug and `tests/cases/native/native_value_type_namespace/`
for the rename bug).

Asyncio cleanup: `Executor.timer_heap` collapsed from
`list[tuple[float, UInt64]]` + parallel `_timer_wakers: dict[UInt64, Waker]`
to a single `list[TimerEntry]` with `__lt__` on deadline. The
multi-paragraph apology comment that documented the workaround is
gone. Net: 3779 tests pass. (TimerEntry was initially declared
`ValueType` to work around the heapq `item: T` rvalue-binding gap;
that base was dropped in a later commit once heapq's signatures
moved to `Own[T]`.)

### v1.2 step 4 -- type-erasure stack to TPy -- DONE

The entire type-erasure stack now lives in TPy. Deletes ~250 lines of
hand-written C++ template machinery from `runtime/cpp/include/tpy/async.hpp`;
replaces it with @dynamic-protocol-driven Adapter codegen plus a TPy
TaskState + Task implementation built on `Rc[T]` and `Box[AnyTask]`.

What landed (final cutover after the infrastructure prep in 80e9eb99b
and the master merge bringing the None and narrowed-field-LHS fixes):

- **TPy types** in `lib/tpy/asyncio/_executor.py`:
  * `@dynamic AsyncFrame[T]` protocol -- `__poll__` + `cancel`. Type-
    erased frame interface; concrete coro structs are held through
    `Box[AsyncFrame[T]]` via the codegen-generated Adapter.
  * `@dynamic AnyTask` protocol -- non-generic; the executor's slot
    table holds `Box[AnyTask]`.
  * `class TaskState[T]` -- the shared backing for a `Task[T]`. Owns
    a `Box[AsyncFrame[T]] | None` frame, `UninitArrayStorage[T, 1]`
    result cache, exception cache, awaiter waker, plus the
    done/has_result/has_exc/executor_owned flags.
  * `class TaskStateView[T]` -- adapter that exposes
    `Rc[TaskState[T]]` through the non-generic `AnyTask` protocol.
  * `class Task[T]` -- claims `tpy.Task` via `@builtin_type`,
    wraps `Rc[TaskState[T]]`. Replaces the @native stub in
    `tpy._core/_types.py` (removed in the cutover).
  * Maker helpers: `task_from_coro`, `make_executor_owned_task`,
    `task_to_any_box`. Plus `_box_coro` cpp_template bridge that
    handles the Box-with-Adapter wrap at the call site (TPy generics
    can't yet express `[T, CoroT: AsyncFrame[T]]` bounds).
  * `_executor_spawn_via_handle` cpp_template -- dispatches into
    `Executor.spawn` from the now-pure-TPy `asyncio.create_task`
    (replaces the C++ `executor_ops.spawn` thunk).

- **Codegen**:
  * `gen_async.py` auto-emits `void cancel() { __cancel_pending = true; }`
    on every generated coro struct. Required for structural conformance
    to `@dynamic AsyncFrame[T]`.
  * `generator.py` bucketed `protocol_reexports` for @dynamic protocols
    so cross-module references emit `using ::ns::DynProto;` declarations
    (filtered to non-implicit-stdlib sources to avoid the stdlib stub
    build's namespace-not-yet-declared issue; static protocols are
    excluded since their concept-form alias syntax differs).

- **C++ deletions** in `runtime/cpp/include/tpy/async.hpp`
  (wrapped in `#if 0` blocks during the cutover; can be physically
  removed in a follow-up cleanup):
  * `AnyTask`, `TaskState<T>`, `TaskStateImpl<T, CoroT>`, `Task<T>`,
    `AnyTaskBox`, `detail::EmptyResult`.
  * `make_user_task`, `make_executor_owned_task`,
    `make_any_task_for_test`, `task_to_any_box`,
    `task_poll_cancelled` (the last was already deleted in
    `99a251c07`).
  * `ExecutorOps::SpawnFn` field + `spawn_thunk` (no longer
    needed -- `create_task` calls `Executor.spawn` directly via
    the cpp_template handle dispatch).

- **Updated** `tpy.coro` to drop the Task import and the
  cpp_template-based `task_from_coro` (now in `asyncio` as pure TPy).
  Compiler hardcoding for `tpy.Task` (in
  `tpyc/type_def_registry.py`, `tpyc/typesys.py`,
  `tpyc/sema/expressions.py`) is unchanged -- the qname is preserved
  via `@builtin_type("tpy.Task")` on the new TPy class; only the
  `cpp_formatter` updated to point to
  `::tpystd::asyncio::_executor::Task<T>`.

- **Test updates**: 10 tests updated `from tpy.coro import Task` to
  `from asyncio import Task` and a few added `cancel()` methods to
  hand-rolled awaitables that previously only had `__cancel_pending`
  fields. `error_create_task_non_coro` deleted (the sema check no
  longer fires for the now-TPy `asyncio.create_task`; the equivalent
  diagnostic could be re-added when sema is updated for the new
  function shape). `executor_bindings_smoke` removed its
  `AnyTaskBox` probe (no longer a @native type).

**Layering note**: TaskState / Task / AsyncFrame / AnyTask all live in
`asyncio/_executor.py` rather than a separate `_task.py`. Splitting
them across two sibling submodules of asyncio triggered tpyc's
parent-package auto-include logic (any dotted module reference
includes the parent's `.hpp` first), creating a cycle:
`_executor.hpp` would include `asyncio.hpp`, which has `using
::tpystd::asyncio::_executor::*` declarations referencing a not-yet-
declared namespace. Putting everything in one sibling means
asyncio's `__init__.py` has only one sibling import path
(`from ._executor import ...`), no cycle.

**Known limitations**:
- Exception storage on `TaskState[T]` slices the dynamic type
  (caught `BaseException as e` then assigned to `self.exc` field
  loses the polymorphic type that C++ `std::exception_ptr` preserved).
  Workaround for the common case: dedicated `exc_was_cancelled: bool`
  flag, and `__poll__` raises a fresh `CancelledError()` instead of
  the stored exception. Cancellation through tasks works correctly;
  other exception types lose their concrete class through Task
  storage. Tracked as a TPy-side gap (would need a `current_exception`
  / `exception_ptr` equivalent at the TPy layer).
- Auto-readonly inference is too aggressive on methods that call
  non-const methods through `Box[GenericDynProto[T]]` fields. Worked
  around in `TaskState.cancel_any` and `Task.cancel` via a self-write
  token (`self.done = self.done`, `self._cancelled = True`) that
  defeats the inference. A real compiler fix would track method calls
  through @dynamic-protocol-typed fields for const inference.

What's left in `runtime/cpp/include/tpy/async.hpp`: `Waker`,
`Poll<T>` (still C++ -- the four specializations need a generic-class
spec compiler feature to port), `CancelledError`, `ExecutorOps` (now
just `mark_runnable` + `register_timer`), `ExecutorHandle` +
`current_executor` thread-local, plus the small bridge helpers
(`make_executor_handle`, `make_waker`,
`executor_register_timer_seconds`).

3901 tests pass.

### Blocked -- stays C++ until compiler features land

These pieces depend on language features TPy doesn't have today.
Listing them so we don't accidentally try to move them and waste
binding work:

- `make_user_task<T, CoroT>` -- thin templated factory; stays C++ as
  ~10-line helper. Constructs `AsyncFrameImpl<T, CoroT>`,
  `shared_ptr<TaskState<T>>`, and an `AnyTaskBox`. Returns the box
  for the TPy executor to spawn.
- `Task<T>::from_coro<CoroT>` -- generic factory; stays C++.
- `AsyncFrameBase<T>` virtual base + `AsyncFrameImpl<T, CoroT>` -- the
  generic-over-T type-erasure machinery. Generic `@dynamic` protocols
  now exist (see `docs/DYNAMIC_PROTOCOL_DESIGN.md` step 12), so this
  C++ scaffolding can be replaced with a `@dynamic Awaitable[T]` +
  `Adapter[Awaitable[T], CoroT]` pair from TPy. Port not yet wired.
- `TaskState<T>` -- `Rc[T]` is now available (`tplib.Rc`,
  `::tpy::Rc<T>` runtime template), so `TaskState[T]` can be ported.
  Port not yet wired -- TaskState's logic is small and the C++ shape
  already works.
- `Poll<T>` storage -- stays C++ (primitive with void / reference /
  move-only / non-default-constructible specializations).
- The main-coro spawn closure in `async_run` -- captures
  `shared_ptr<frame>` + `shared_ptr<TaskValueSlot<ResultT>>` + a
  `has_result` flag. Generic-over-ResultT closure construction is
  C++-template territory; stays as a small templated helper.

### Compiler gaps that, when closed, expand what's portable

- **Generic `@dynamic` protocols** -- shipped (see
  `docs/DYNAMIC_PROTOCOL_DESIGN.md` step 12). Adapter codegen emits a
  per-T-instantiation vtable; each `Adapter[Awaitable[Int32]]` is a
  distinct runtime type. Now usable to replace `AsyncFrameBase` /
  `AsyncFrameImpl`; port not yet wired.
- **Shared-ownership smart pointer in TPy** -- `Rc[T]` shipped as a
  pure-TPy class in `lib/tpy/tplib/rc.py` (uses `tpy.unsafe` for the
  heap block). Non-atomic single-threaded refcount; `@nocopy` with
  explicit `.clone()`. Construct via `Rc.new(value)`. Unblocks the
  `TaskState[T]` TPy port. Atomic `Arc[T]` for multi-threaded use is a
  v3+ item.
- **`thread_local` storage in TPy**. Would let the executor's
  `current_executor` move out of C++ entirely. Low priority since the
  v1 executor is single-threaded.
- **`tpy.coro.poll_once`** -- shipped. Real TPy body
  `def poll_once[T](aw: Awaitable[T]) -> Poll[T]: return aw.poll(Waker())`
  in `lib/tpy/tpy/coro/__init__.py`, plus a CPython stub at
  `lib/cpy/tpy/coro/__init__.py` that drives CPython coroutines via
  `.send(None) -> StopIteration` and delegates to `aw.poll(Waker())`
  for hand-rolled awaitables. Adopted in `no_await_async`,
  `await_assign`, and `handwritten_awaitable` test cases (replaced
  `cpp_template` boilerplate). Two compiler gaps that had blocked it
  are now fixed:
  - `f()` for `async def f() -> T` sema-types as `Awaitable[T]`
    (mirrors `Iterator[T]` for generators), so structural matching
    against an `Awaitable[T]` parameter works without per-call
    special-cases.
  - Implicit T inference for protocol-typed args recurses substitution
    into compound positions
    (`_infer_protocol_type_arg_structurally`).

## Decisions taken

- **try/finally codegen unified across sync and async.** A single
  inline-emit pattern; the goto-based path is gone. Snapshot churn was
  bounded; net codegen is ~300 lines shorter.
- **Coroutine[T] = Own[generated coroutine struct].** No new type-system
  kind; the generated struct carries an `is_coroutine_for` marker for
  diagnostic / type-print specialization. Reuses existing Own-style
  consume tracking. (Rust-style: anonymous struct + structural protocol
  + move semantics; TPy's Own gives single-await-for-free.)
- **Resumable-frame is a separate codegen path from generators.** Future
  generator migration is out of scope for v1; the abstraction is factored
  so generator codegen can adopt it later without redesign.
- **Waker = `@native` value type backed by C++ POD.** Visible to TPy code
  for `Future[T]._waiter` storage and `waker.wake()` calls.
- **Statically-known sub-coroutine inlining**: direct calls to `async def`s
  / methods on concrete records / typed coroutine field accesses inline
  the sub-coroutine's frame as a parent frame field. Variables typed as
  `Awaitable[T]` (structural) / `Task[T]` / unions box to the heap.
