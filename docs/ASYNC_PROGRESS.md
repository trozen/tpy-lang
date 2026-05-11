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
without name mangling, and `Task<void>::from_coro<TPyClass>` works for
TPy-emitted struct CoroT.

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
for v1: they call `tpy.copy` whose codegen ordering is incompatible
with sub-modules of `tpy` (sub-module compiles before `tpy.copy`'s
overload table is ready). Asyncio re-exports them as `asyncio.Future`
/ `asyncio.InvalidStateError`. The compile-order gap is tracked
below; future move once that lands.

### Phase 1 -- Compiler bindings (prerequisite)

Land the small bindings the rest of the port needs. None of these
require new compiler features.

- `time.sleep_until_steady(deadline_seconds)` -- single C++-bound
  function wrapping `std::this_thread::sleep_until`. Lets the run loop
  wait for the next timer in TPy.
- `_get_current_executor()` / `_set_current_executor(e)` /
  `_clear_current_executor()` -- thin getters/setters for the
  `thread_local Executor* current_executor` pointer. Stays a C++
  thread_local for now (TPy has no thread-local syntax); TPy code uses
  RAII via `__del__` for save/restore.
- `AnyTaskBox` -- `@native("::tpy::AnyTaskBox")` thin wrapper around
  `unique_ptr<AnyTask>`. TPy code stores `list[AnyTaskBox]` in the
  executor's slot table; methods (`poll_any`, `cancel_any`, `empty`,
  `reset`) are `@cpp_template` shims.
- Timer slot type: plain `tuple[float, Waker]`, sorted via
  `heapq.heappush` / `heappop` on `list[tuple[float, Waker]]`. (The
  tuple<->Comparable conformance gap has been closed; no dedicated
  Timer class needed.)
- `task_poll_cancelled[T](t: Task[T]) -> bool` -- TPy generic function
  replacing the C++ template. Test-only utility; converting it
  validates the binding plumbing on something safe.

### Phase 2 -- Executor body to TPy

Move the data structures + methods of `tpy::Executor` to a TPy class in
`lib/tpy/asyncio/_executor.py` (or similar). Keep `make_user_task`,
`AnyTask`/`AnyTaskImpl`, `TaskState`, `Task` in C++ (see "blocked"
below). The TPy executor:

- `slots: list[Slot]` where `Slot` is a TPy class wrapping
  `AnyTaskBox`, `generation: UInt32`, `runnable: bool`.
- `runnable_q: list[UInt32]` used as a deque (or a proper deque type
  if one lands).
- `timer_heap: list[tuple[float, Waker]]` driven via `heapq.heappush` /
  `heappop`.
- Methods: `spawn`, `mark_runnable`, `poll_slot`, `drain_runnable`,
  `slot_done`, `has_live_tasks`, `wait_for_event`, `run_until`,
  `drain_spawned_with_cancel`. All pure TPy logic plus calls into the
  Phase 1 bindings.
- Mutation-during-iteration safety: same as in C++ today, indices into
  `slots`, no held references across `poll_any`.

### Phase 3 -- async_run to TPy + RAII scope guard

`async_run` becomes a TPy generic function. Setup/teardown of
`current_executor` flows through a `_ExecutorScope` class with `__del__`
that swaps the thread_local pointer back, providing exception-safe
cleanup. The main coro is spawned via a small C++ helper (templated
over `ResultT` to construct the result-capturing closure -- see
"blocked" below); everything else is TPy.

### Phase 4 -- Cleanup

Remove dead C++: `tpy::Executor` struct, `tpy::async_run` template,
`tpy::AnyTask`/`AnyTaskImpl` (only if no longer referenced from
`make_user_task`), `tpy::FunctionTask`. The bridge helper from Phase 0
stays.

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
  generic-over-T type-erasure machinery. Would simplify dramatically
  if `@dynamic` gained generic-protocol support
  (`@dynamic class Awaitable[T]`).
- `TaskState<T>` -- could move with a `SharedTaskState[T]` `@native`
  wrapper around `shared_ptr<TaskState<T>>`, but TPy currently models
  only unique ownership (`Box[T]`); shared-ownership exposure is a
  prerequisite. Currently low ROI -- TaskState's logic is small.
- `Poll<T>` storage -- stays C++ (primitive with void / reference /
  move-only / non-default-constructible specializations).
- The main-coro spawn closure in `async_run` -- captures
  `shared_ptr<frame>` + `shared_ptr<TaskValueSlot<ResultT>>` + a
  `has_result` flag. Generic-over-ResultT closure construction is
  C++-template territory; stays as a small templated helper.

### Compiler gaps that, when closed, expand what's portable

- **Generic `@dynamic` protocols**. `@dynamic class Awaitable[T]: def
  poll(self, w: Waker) -> Poll[T]` would obsolete `AsyncFrameBase` /
  `AsyncFrameImpl`. Adapter codegen would build a per-T-instantiation
  vtable (each `Adapter[Awaitable[Int32]]` a distinct runtime type).
  Largest single unblocker.
- **Shared-ownership smart pointer in TPy**. `Rc[T]` / `Arc[T]`
  (refcounted) or just `SharedBox[T]` -- currently only `Box[T]` for
  unique ownership. Would let `TaskState` move to TPy.
- **`thread_local` storage in TPy**. Would let the executor's
  `current_executor` move out of C++ entirely. Low priority since the
  v1 executor is single-threaded.
- **Sub-module-of-`tpy` use of `tpy.copy` (and other
  `@builtin_function`s)**. A class defined in `lib/tpy/tpy/coro/`
  that calls `copy(value)` errors with "No matching overload for
  copy" -- the sub-module is compiled before `tpy.copy`'s overload
  table is registered. Workaround used today: the type stays in a
  module outside `lib/tpy/tpy/` (e.g. `lib/tpy/asyncio/` for
  `Future[T]` / `InvalidStateError`). Fix: order the builtin
  registration before sub-module compilation, or expose copy-like
  builtins via a path that's resolvable from sub-modules.
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
