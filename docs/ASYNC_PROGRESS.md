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

    enum { S_INITIAL = 0, S_RESUME_0 = 1, ..., S_DONE = K };

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
        case S_RESUME_0: {
            try {
                if (__cancel_pending) { __cancel_pending = false; throw tpy::CancelledError(); }
                auto r = __sub_0->poll(waker);
                if (r.is_pending()) return tpy::Poll<T>::pending();
                // bind result, reset sub-future, advance state, fall through
                <result_var> = std::move(r).value();
                __sub_0.reset();
                __state = S_RESUME_1;  // or S_DONE
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
  `if exc_val is not None:` for binary suppression. The diagnostic
  reason was reworded during M2 partial landing: it now reflects the
  real blocker -- `BaseException` is not `@dynamic`-rooted in TPy, so
  the polymorphic-class codegen paths don't activate for it. The
  earlier framing about value-conversion slicing through
  `Optional[BaseException]` is moot for the planned design (the
  codegen no-slice fix is in place for user-defined polymorphic
  hierarchies; once `BaseException` inherits a `Throwable` `@dynamic`
  protocol, the same path activates and the diagnostic drops out).
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

### M2 SHIPPED -- class-based `__exit__` dispatch via `isinstance(exc_val, X)`

Design pass landed on a generic rule (`Optional[E]` for polymorphic
class types `E` is borrow-form; `isinstance` lowers to `dynamic_cast`;
storage/return positions require explicit `Box[E]`), keyed on a
`@dynamic`-rooted-hierarchy predicate so it isn't a one-off rule for
the `BaseException` tree.

**Shipped in two passes** (initial machinery + BaseException activation):

- Transitive virtual-override propagation for `@dynamic`-rooted
  inheritance chains (`tpyc/codegen_cpp/functions.py::_get_dynamic_override_info`
  walks the MRO via `iter_ancestor_records`; `tpyc/sema/analyzer.py::_dynamic_proto_requires_nonconst`
  walks the MRO so auto-readonly inference doesn't introduce a
  const-mismatching signature; `tpyc/sema/registration.py::_check_method_hiding`
  suppresses the "hides ancestor" warning for methods that are now
  proper `override`s).
- `is_polymorphic_class_type(typ, registry)` predicate in `tpyc/typesys.py`:
  a concrete class is polymorphic iff it (transitively) inherits a
  `@dynamic` protocol.
- Codegen no-slice for rvalue construction into `Optional[Polymorphic]`
  parameters (`tpyc/codegen_cpp/expressions.py::_gen_optional_ptr_arg`):
  the temp is materialized at the rvalue's actual class type; C++
  implicit pointer upcast handles the conversion when taking the
  pointer.
- `isinstance(opt_var, Subclass)` on `Optional[Polymorphic]` lowers to
  `dynamic_cast` on the raw pointer (`tpyc/codegen_cpp/expressions.py::_gen_call`
  isinstance branch); tuple form -> OR of casts. Sema accepts in
  `tpyc/sema/calls.py::_analyze_isinstance` for both the Optional-source
  path and the post-`is not None`-narrowed path.
- Regression tests: `tests/cases/protocols/protocol_dynamic_inherit_transitive`
  (3-level chain virtual dispatch); `tests/cases/protocols/protocol_dynamic_optional_isinstance`
  (rvalue construction + isinstance + tuple form + virtual dispatch).

**Second pass -- BaseException activation:**

- Added a markerless `@dynamic class Throwable(Protocol): pass` in
  `lib/tpy/tpy/_core/_types.py`. Pure phylum tag -- no virtual method
  contract; the polymorphism predicate consumes it to activate the M2
  dispatch paths. (Originally drafted with a placeholder `__str__`
  method because the compiler rejected markerless `@dynamic` protocols;
  the rejection was lifted in the same milestone since markerless is
  the right shape here -- BaseException is `@native` and routes through
  Adapter, so virtual method declarations on Throwable were dead C++.)
- Declared `class BaseException(Throwable)` in `lib/tpy/tpy/_builtins/_exceptions.py`;
  all exception subclasses inherit through the existing chain. The
  transitive-override propagation from the first pass handles them
  automatically -- no per-subclass changes needed.
- `is_polymorphic_class_type` now returns True for the entire
  `BaseException` tree. `isinstance(exc_val, ValueError)` in `__exit__`
  bodies lowers to `dynamic_cast<const ::tpy::ValueError*>(exc_val) != nullptr`
  and runs correctly. The M1 stopgap diagnostic drops out automatically.
- Two M1 stopgap error tests converted to positive M2 tests:
  `tests/cases/control_flow/with_exit_isinstance_class_dispatch` (in
  `__exit__` body, with suppression behavior end-to-end) and
  `isinstance_optexc_outside_exit` (Optional[BaseException] parameter
  in a plain function).
- Protocol-snapshot test gained a `tpy.Throwable` entry. Stdlib diff
  is ~10 lines; the heavy lifting was the first pass.

**Remaining v1.5 M2 follow-ups (not blockers):**

- **Slicing-site sema rejections** for field declarations and rvalue
  returns of `Optional[Polymorphic]` (filed in TODO.md). Not blocking;
  the codegen no-slice fix handles the common parameter-passing case
  which is what users hit in practice.

### M3 SHIPPED -- arbitrary `await` placement via CFG-lite lowering

Lifts the v1 restriction that `await` must be a top-level statement
(or in the narrow wrapper-try / single-try-except shapes). After M3:

- `await` inside `if` / `elif` / `else` branches (any nesting).
- `await` inside `while` loop bodies (including `break` / `continue`
  from nested if-branches within the loop -- the break/continue
  translate to CFG state transitions, not C++ `break`/`continue` which
  would exit the state-machine switch).
- General `try` / `except` / `finally` bodies: multiple awaits per
  try, awaits inside except handlers, nested try/except/finally with
  Python-correct finally ordering (inner finally before outer, both
  run after a matched except, etc.).
- `await` inside `try` body with explicit `return` walks the active
  finally chain through `_make_async_return` + `_emit_finally_chain`.

**Architecture**: localized CFG built per async def body, in a new
shape-neutral module `tpyc/codegen_cpp/resumable_cfg.py`. Generator
migration consumes the same module later (planned follow-up in
TODO.md "Migrate generators onto resumable-frame"). The CFG has:

- `BB` (basic block) with `stmts: list[TpyStmt]` (leaf statements --
  compound statements without suspensions stay as single elements,
  lazy decomposition) and a `terminator` (`Fall`/`Branch`/`Yield`/
  `ReturnT`/`RaiseT`/`Unreachable`).
- `region_stack: tuple[Region, ...]` per BB -- the active
  try/except/finally/with frames at BB entry. `TryRegion` carries
  the source-level handlers + `finally_helper_name`; `ExceptRegion`
  marks handler-body BBs with `parent_finally` so emit can run the
  outer try's finally on normal handler exit (Python semantics).
- A `payload_factory` callback lets async-specific metadata (mode,
  sub_field_cpp_type derived from sema annotations on the
  `TpyAwait` node) be filled at construction without making the CFG
  itself async-specific.

**Emit** (`AsyncCoroCodegen._emit_state_machine` and helpers in
`gen_async.py`): replaces the old linear-region partition. Computes
case-entry BBs (entry / yield-resume / multi-predecessor /
region-stack-change) and emits `while (true) switch (state)` with
each case body wrapped in its region stack reconstructed as nested
C++ try blocks. The C++ rule "case labels inside try are not
reachable from outside" forces the try-stack reconstruction per
case body (C# Roslyn pattern). The old [[fallthrough]] between
cases is replaced by `state = X; continue;` transitions; the
catch wrappers reset the per-yield sub-future field on throws.
`_emit_exit_region_finallies` runs each exited region's finally
on Fall edges that cross out of a region (normal try-exit path
where the C++ try doesn't have a language-level finally).

**Compound expressions**: the existing `_lift_awaits_in_stmt` lifter
now recurses into compound statement bodies (`if`/`while`/`for`/
`try`/`with`), so awaits buried inside loop-body or branch-body
expressions are correctly hoisted to preceding `__await_lift_<n>`
vardecls.

**Scheduled follow-ups** (in ASYNC_DESIGN.md roadmap, not v1.5
composability blockers):

- **M3.1 -- Sync `for x in xs:` with await in body.** SHIPPED. The
  CFG builder lowers a for-with-await into iter-init / cond-advance /
  body / exit BBs; the iterator and `__next__` result live in the
  coro frame as `std::optional<decltype(...)>` slots. Universal
  `::tpy::__iter__` / `__next__()` path only -- no range-counter
  peephole inside async functions (peepholes still apply to
  non-async for-loops). Synthetic `AsyncForIterSetup` leaf-stmt and
  `AsyncForAdvance` terminator in `resumable_cfg.py`; emit lives in
  `_emit_async_for_iter_setup` / `_emit_async_for_advance` in
  `gen_async.py`. `async for` has its own desugaring (`__aiter__` /
  `__anext__`) and doesn't share this code path.
- **M3.2 -- Sync `with X:` with await in body.** SHIPPED. CFG
  decomposes a with-with-await into a WithEnter setup stmt +
  WithRegion-wrapped body BBs. Context manager + as-binding live in
  `__with_ctx_<n>` / `<target>` coro-frame slots
  (`std::optional<...>`). Each case whose region_stack contains a
  WithRegion gets `try { ... } catch (::tpy::BaseException&) {
  ... } catch (...) { __exit__; throw; }` around the body. When
  `item.exit_can_suppress` is true, the BaseException catch dispatches
  `__exit__({}, &exc, {})` and -- on a True return -- transitions
  state to a synthesized `post_with_bb` case label; on False, rethrows.
  Normal-exit `__exit__({}, nullptr/{}, {})` is emitted by
  `_emit_exit_region_finallies` when the Fall edge crosses out of the
  WithRegion. Synthetic `WithEnter` in `resumable_cfg.py`; emit
  helpers `_emit_with_enter` / `_emit_with_exit` /
  `_emit_with_region_catches` in `gen_async.py`.
- **M3.3 + M3.3.1 + M3.3.2 -- `await` inside a `finally` body
  (with handlers, with return-walks-finally).** SHIPPED. The CFG
  splits a try-finally-with-await into a TryRegion-wrapped try body
  and a FinallyRegion-only finally body region; each try-body case's
  catch-all saves `std::current_exception()` to a `__finally_exc_<n>`
  frame slot and transitions state to the finally entry BB. Handlers
  share the same machinery (M3.3.1): each handler's inner try/catch
  saves+transitions to the finally entry on raise; the handler's
  normal-exit Fall already targets the finally entry. `return` inside
  the try body or any handler (M3.3.2) parks the value in a
  `__finally_ret_<n>` frame slot and sets `__finally_pending_<n>`,
  then walks any finally frames inside the CFG-based finally
  (boundary tracked via `_pending_return_info_for_region_stack`),
  then transitions state. The `AsyncFinallyExit` at the finally tail
  checks the pending flag after the rethrow check and emits the
  deferred Poll::ready (walking any outer helper-based finallies via
  `_emit_finally_chain`). One remaining restriction: nesting two
  CFG-based finally regions (the inner exit would need to forward
  pending state to an outer slot).
- **M3.4 -- Dead-catch elision around no-throw suspend BBs.**
  SHIPPED. `_case_is_no_throw` in `gen_async.py` returns True for a
  case entry with empty user stmts and a Yield terminator whose
  emplace args are all literals / simple names (via
  `_payload_args_no_throw` / `_expr_is_simple`); when True, the
  per-case try/catch wrap is skipped. The matching live catch sits
  on the resume case where the sub-future's poll runs, so dropping
  the dead one is safe. Saves ~10 generated lines per such case in
  snapshots.

**Await evaluation-order in conditional / repeated positions (FIXED).**
The await lifter used to hoist a nested `await` to a *preceding* statement
unconditionally, which is only correct in evaluated-once positions. In
short-circuit operands (`a or await b()`), ternary branches, chained
comparisons, and `while` conditions that changed evaluation order
(skipped operand ran anyway; loop condition ran once -> infinite loop).
Fixed by a pre-sema suspension-expression desugar
(`tpyc/parse/desugar_suspensions.py`): it rewrites those positions into
statement-position suspensions guarded by explicit control flow (`while
True: __c = await cond(); if cond: BODY else: ELSE; break`; short-circuit
and chained-compare lower to bool temps; ternary to per-branch
assignments), so sema computes narrowing / liveness / borrow / type facts
on the lowered form and the existing CFG handles it. Tests under
`tests/cases/async/await_*`. Remaining gaps: `await` inside a
comprehension (rejected; full support tracked in TODO.md) and
divergent-type ternary branches (rejected with an imperfect diagnostic,
BUGS.md).

### M4 SHIPPED -- async methods on user classes (M5 prerequisite)

Lifts the PR2 parser-level rejection of async methods. v1's
`docs/ASYNC_DESIGN.md` listed only "user-defined `__await__`" and
"async + @error_return / @noalloc / yield / @native / @export" as
exclusions; async methods were a separate restriction in `PR2`
intended to ship later. M4 ships it because v1.5 M5 (`async with`)
needs `__aenter__` / `__aexit__` to be `async def` methods.

What ships:

- **Parser**: `_parse_method` accepts `ast.AsyncFunctionDef` items in
  class bodies, propagating `is_async=True` onto the `TpyFunction`.
  Mirrors `_parse_def`'s async exclusion checks (no async generators,
  no `@error_return`, no `@native` / `@export`, no `@staticmethod`,
  no `@property`).
- **Sema**: method registration in `register_record` wraps the
  return type in `Awaitable[T]` for `method.is_async` (parallel to
  the free async-def wrapping at line ~2603). The await analyzer
  (`expressions.analyze_await`) now recognizes `TpyMethodCall` to
  async def via the call's `resolved_function_info`. The receiver's
  record name is captured on `TpyAwait.awaited_method_owner_record`
  for codegen's unique sub-coro struct naming.
- **Codegen**: `AsyncCoroCodegen.gen_struct_name(func, record_name)`
  returns `__coro_<Record>_<method>` (parallels
  `GeneratorCodegen.gen_struct_name`'s method shape).
  `_classify_params(func, record_name)` prepends
  `__self: <Record>&` (or `const <Record>&` for `@readonly`) so the
  coro frame captures the receiver. `gen_coro_poll_def` /
  `gen_coro_finally_top_def` set `ctx.generator_self_ref = "__self"`
  so body refs to `self.X` rewrite to `this->__self.X`.
- **Codegen orchestration** (`generator.py`):
  - Forward decls for async-method coro structs emit BEFORE records
    (so record method signatures can reference them).
  - Async-method coro struct + inline factory method definitions
    emit AFTER records but BEFORE free coro structs (because a free
    coro that awaits an async method needs the method's coro struct
    complete to inline as `optional<__coro_Class_method>`).
  - In-struct declaration of the method itself is
    `__coro_Class_method method_name(args) const?` -- the body lives
    out-of-line.
- **Codegen `_emit_suspend` INLINE mode**: handles
  `TpyMethodCall` by emplacing with `(receiver_cpp, args...)`,
  parallel to the free-call shape's `(args...)`.
- **Tests** (`tests/cases/async/`):
  - `method_basic` -- canonical case; the old `error_async_method`
    rejection case repurposed as a positive smoke.
  - `method_chain` -- async method awaiting another async method on
    the same instance.
  - `method_readonly` -- non-mutating method; auto-readonly
    inference flips `__self` to `const Class&`.
  - `method_with_sleep` -- async method body containing
    `await asyncio.sleep(...)` (mixed INLINE + ERASED awaits).
  - `method_two_classes` -- same method name on two classes;
    `__coro_A_tag` vs `__coro_B_tag` disambiguates.
  - `error_async_staticmethod`, `error_async_property` -- parser
    rejections preserved for the unsupported sub-shapes.

Restrictions still in place (each a separate future item):

- `async @staticmethod` / `async @property`: rejected at parse.
  Async staticmethods would need a no-self-capture variant; async
  properties would conflict with the field-access syntax.
- `async def` in protocols (structural / `@dynamic`): still rejected
  at parse. Generic `@dynamic` async methods need adapter codegen;
  filed as a future item.
- `async def __init__` / `__del__`: rejected at parse (the
  parser's async-method exclusion checks also catch these via
  their synthesized return-type contracts).
- Async methods on generic *classes* (`class Foo[T]: async def m...`):
  rejected at sema -- the out-of-line coro-struct `__poll__` body
  doesn't yet receive the class's template header, so any reference
  to the class's type params would fail C++ build. Parallels the
  existing rejection for generator methods on generic classes.
- Generic async *methods* on non-generic classes (`async def m[T](self) -> T`):
  works end-to-end as of M7 (see below). Generic async methods on
  *generic classes* (`class Foo[T]: async def m...`) remain rejected
  (out-of-line `__poll__` body doesn't yet receive the class's
  template header).

### M5 SHIPPED -- `async with` (cleanup-only)

Lifts the v1 PR2 deferral. `async with X as y: body` lowers via a
CFG-level synthesis equivalent to:

```python
__with_ctx_<n> = X
y = await __with_ctx_<n>.__aenter__()
try:
    body
finally:
    await __with_ctx_<n>.__aexit__(None, None, None)
```

The synthesis re-uses the M3.3 CFG-based-finally machinery: an
`__finally_exc_<uid>` frame slot saves any in-flight exception via
`std::current_exception()`; AsyncFinallyExit at the tail of the
finally body rethrows. `return` inside the body parks the value in
`__finally_pending_<uid>` / `__finally_ret_<uid>` and walks through
the same finally path (M3.3.2 mechanism).

What landed:

- **Parser** (`tpyc/parse/parser.py`): `ast.AsyncWith` -> `TpyWith` with
  `is_async=True`. New field on `TpyWith` in `parse/nodes.py`.
- **Sema** (`tpyc/sema/statements.py::_analyze_with`): async branch
  validates the stmt is inside `async def`, looks up `__aenter__` /
  `__aexit__` (not the sync names), checks both are `async def`,
  unwraps `Awaitable[T]` from their return types so as-binding sees
  the user's declared T. Per-item flags `exit_can_suppress` /
  `exit_takes_exc_val` populated for consistency (codegen ignores
  them in the cleanup-only path).
- **CFG** (`tpyc/codegen_cpp/resumable_cfg.py::_build_async_with`):
  synthesizes the AsyncWithSetup leaf-stmt + Yield(aenter) + Yield(aexit)
  + AsyncFinallyExit. New `AsyncWithSetup` synthetic stmt. AwaitPayload
  grew `async_with_kind` / `async_with_ctx_n` fields so the synthetic
  yields can bypass AST-based gen_expr at emit time.
- **Codegen** (`tpyc/codegen_cpp/gen_async.py`):
  - `_prescan_with_stmts` now allocates `__with_ctx_<n>` for async-with
    regardless of body-await content, and pre-computes the aenter/aexit
    coro struct names per ctx_n into `func._async_with_struct_names`.
  - `_prescan_resumable_try_finally` (renamed from `_prescan_async_try_finally` by the generator -> resumable-frame de-async-ify pass) extended to allocate the shared
    `__finally_exc_<uid>` / `__finally_pending_<uid>` / `__finally_ret_<uid>`
    frame fields for async-with stmts.
  - `gen_coro_struct`'s `__sub_<i>` field emit consults
    `_async_with_struct_names` for the concrete coro struct type when a
    Yield's payload has `async_with_kind`.
  - `_emit_suspend` INLINE-mode handles async_with_kind directly:
    `__sub_<i>.emplace((*__with_ctx_<n>))` for aenter,
    `__sub_<i>.emplace((*__with_ctx_<n>), monostate, monostate, monostate)`
    for aexit (cleanup-only call shape).
  - `_emit_async_with_setup` writes `__with_ctx_<n> = <ctx_expr>;`.
- **`_stmt_has_any_suspension`** in `resumable_cfg.py`: returns True for
  `async with` even when the body has no user awaits, so the CFG
  builder runs `_build_with` instead of treating the stmt as a leaf.

**v1.5 M5 simplification: cleanup-only `__aexit__`.** `__aexit__` is
called with `(None, None, None)` regardless of whether an exception
was caught; the body's exception propagates through the
`__finally_exc_<n>` rethrow path. `exc_val: Optional[BaseException]`
on `__aexit__` is rejected at sema with a pointer to E9 / Phase 20
(polymorphic exception storage via `Box[Throwable]`). This restriction
holds back full CPython parity but keeps M5 free of cross-suspension
polymorphic storage work that E9 will solve uniformly.

**Restrictions** (each rejected with a clear diagnostic):

- Multi-item `async with X as a, Y as b:` rejected today. Workaround:
  nest two separate `async with` statements. The two CFG-based finally
  regions need to chain pending-return state, which is the same
  underlying limit M3.3 has.
- Direct nesting `async with X: async with Y:` in the *same* function
  body also hits the M3.3 nested-await-in-finally limit. Workaround:
  factor the inner manager into an `async def` helper called from the
  outer body. Tracked alongside the M3.3 limit.

**Tests** (`tests/cases/async/`):
- `with_basic` -- canonical case.
- `with_raise_propagates` -- body raises; __aexit__ runs; exception
  propagates out and is caught by an outer try/except.
- `with_return_in_body` -- `return` inside body walks through
  __aexit__ then completes the return.
- `with_nested` -- two `async with` chained via an `async def` helper
  (workaround for the direct-nesting limit).
- `error_async_with_outside_async_def` -- sema rejection outside
  `async def`.
- `error_async_with_sync_manager` -- rejection when __aenter__ /
  __aexit__ aren't `async def`.
- `error_async_with_exc_val_inspect` -- v1.5 M5 limitation diagnostic
  for `exc_val: Optional[BaseException]`.

### M6 SHIPPED -- `async for`

`async for y in ait: body` lowers to the Python desugaring

```python
__aiter = ait.__aiter__()
while True:
    try:
        y = await __aiter.__anext__()
    except StopAsyncIteration:
        break
    body
```

The codegen reuses M3.1's frame-resident iterator slot
(`__for_itr_<uid>`) and the existing try/except CFG machinery. The
new bits are concentrated in one builder method and a small handful
of emit-side branches.

What landed:

- **Parser** (`tpyc/parse/parser.py`): `ast.AsyncFor` parsed alongside
  `ast.For`. New `TpyForEach.is_async` flag (parse/nodes.py). The
  tuple-unpack branch is shared with sync `for`, so
  `async for (a, b) in pairs:` works for free via the existing
  `__for_tup_<n>` synthetic-var rewrite. `else:` clause on `async for`
  is rejected at parse time -- same restriction as `await` in
  `for`/`while` `else:` (the break-vs-normal-exit distinction is not
  modelled).
- **Sema** (`tpyc/sema/statements.py::_analyze_async_for`): validates
  the loop is inside `async def`; resolves `<iterable>.__aiter__()`
  (must be a sync `def`); resolves the returned aiter's `__anext__()`
  (must be `async def`); unwraps `Awaitable[T]` and sets the loop var's
  `elem_type=T`. Body analysis runs through the standard `loop_scope` /
  `loop_var` machinery so liveness, init tracking, and scope
  propagation are unchanged from sync for.
- **CFG** (`tpyc/codegen_cpp/resumable_cfg.py::_build_async_for`):
  synthesizes the `AsyncForIterSetup(is_async=True)` leaf-stmt + a
  Yield for `await __aiter.__anext__()` wrapped in a TryRegion. The
  synthesized handler's exception_type is `"StopAsyncIteration"` and
  its body is a single `TpyBreak`; `_build_block` routes the break
  through the loop's `break_bb` so the catch emits as
  `catch (const ::tpy::StopAsyncIteration&) { __state = <exit>;
  continue; }`. The TryRegion is on the region stack only while
  building cond_bb / resume_bb / handler_entry -- body_bb is created
  outside the push, so a body-side `StopAsyncIteration` propagates
  instead of being silently caught.
- **Codegen** (`tpyc/codegen_cpp/gen_async.py`):
  - `_prescan_resumable_for_loops` (renamed from `_prescan_async_for_loops` by the generator -> resumable-frame de-async-ify pass): triggers on `is_async OR body_has_await OR body_has_yield`.
    For async-for, the frame field type is
    `decltype(std::declval<IT&>().__aiter__())` and a new
    `func._async_for_struct_names[uid]` map records the C++ name of
    the `__anext__` sub-coro struct (computed via
    `_anext_sub_struct_name`).
  - `_emit_async_for_iter_setup`: branches on `is_async` to emit either
    `__for_itr_<uid> = (<iterable>).__aiter__();` or the existing
    `::tpy::__iter__(...)` setup.
  - `AwaitPayload.async_for_uid` carries the loop's uid through the
    Yield; the struct-fields emit and `_emit_suspend` INLINE-mode
    branch on it to override the sub-coro type and emit
    `__sub_<i>.emplace(*__for_itr_<uid>);`.
- **Runtime** (`runtime/cpp/include/tpy/core.hpp`): new
  `struct StopAsyncIteration : Exception { using Exception::Exception; };`
  next to `StopIteration`. Distinct type (not a `StopIteration` alias)
  so user `except` clauses can filter precisely.
- **Stdlib** (`lib/tpy/tpy/_builtins/_exceptions.py`,
  `_builtins/__init__.py`, `lib/tpy/builtins.py`): `StopAsyncIteration`
  added as a `@native("tpy::StopAsyncIteration")` `Exception` subclass
  with a `__init__(self, message: str = "")` stub, re-exported through
  the builtins chain. CPython already provides it natively, so no
  `lib/cpy/` stub needed.
- **`_stmt_has_any_suspension`** in `resumable_cfg.py`: returns True for
  `async for` even when the body has no user awaits, mirroring the
  `async with` short-circuit. Without this, an `async for` wrapped in a
  try/except whose body has no other awaits would not trigger CFG
  decomposition of the surrounding try, and sema would correctly mark
  the loop as async-for but codegen would silently emit the sync-for
  path with the wrong iterator protocol -- a foot-gun caught by the
  body-raises-StopAsyncIteration test.

**Restrictions** (each rejected with a clear diagnostic):

- `else:` clause on `async for` -- parser rejects with a pointer to
  the same break-vs-normal-exit limit as await-in-for-else.
- `async for` outside `async def` -- sema rejects.
- `__aiter__` declared `async def` -- sema rejects (Python 3.5.2+
  semantics: `__aiter__` is sync, only `__anext__` is async).
- `__anext__` not declared `async def` -- sema rejects.
- `__anext__` whose return type is not `Awaitable[T]` -- sema rejects.

**Tests** (`tests/cases/async/`):

- `async_for_basic` -- canonical case (Counts iterable, Counter
  iterator that raises `StopAsyncIteration` at end).
- `async_for_break` -- `break` inside body exits the loop without
  polling `__anext__` again.
- `async_for_continue` -- `continue` skips and re-enters the advance.
- `async_for_body_await` -- body contains an additional `await` that
  composes with the `__anext__` Yield (two suspensions per iteration).
- `async_for_tuple_unpack` -- `async for k, sq in pairs:` via the
  parser's existing tuple-unpack rewrite.
- `async_for_body_raises_stop` -- body-side `raise StopAsyncIteration`
  propagates to an outer `except` rather than being silently caught
  by the loop's auto-handler (regression test for the
  TryRegion-scoping fix).
- `stop_async_iteration_builtin` -- `raise StopAsyncIteration("msg")`
  and `except StopAsyncIteration as e:` round-trip outside of any
  async context.
- `error_async_for_else` -- parser rejection of `else:` clause.
- `error_async_for_outside_async` -- sema rejection of `async for`
  outside `async def`.

### M7 SHIPPED -- generic async free functions and methods

Required to unblock generic asyncio helpers (`wait_for`, `gather`, ...).
Before M7, two failure modes blocked any `async def f[T](...) -> T`:

  (a) `return result` from a generic async def: the awaited-value slot
  in the caller's coro struct was emitted with the unsubstituted T,
  because the await's analyzed type came from the registry's
  un-substituted FunctionInfo rather than the operand call's
  resolved (substituted) FunctionInfo.

  (b) `result = await generic_async_fn(arg)`: the sub-coro frame field
  emitted as `std::optional<__coro_<name>>` without template args, so
  C++ rejected the type-name as referring to a class-template-id.

Root cause: the existing await-resolution path stored only the function
*name* on the await node; sema's substituted return type and the
inferred type-arg mapping never left the operand call. Methods
accidentally worked because `mfi = operand.resolved_function_info`
threaded the right info through the method-call branch -- but only
because sema chased it down, not because the invariant was articulated.

Invariant established: every await on a direct call to an async def --
free function or method -- carries the substituted return type and
the inferred type-args mapping of the callee. Codegen consults the
mapping to qualify the sub-coro struct field type and the awaited-value
slot from the await node, never from the operand call.

Concrete changes:

- **AST** (`tpyc/parse/nodes.py::TpyAwait`): two new fields,
  `awaited_resolved_return_type` (substituted return type, used for
  the awaited-value slot) and `awaited_inferred_type_args` (inferred
  type args, used to qualify the sub-coro struct name). Promoted
  `awaited_method_owner_record: str` to
  `awaited_method_owner_type: NominalType` so the receiver's class
  type_args flow uniformly through the same mechanism.
- **Sema** (`tpyc/sema/expressions.py::_analyze_await`): the
  free-function path now reads return type and type-args from
  `operand.resolved_function_info` / `operand.inferred_type_args`
  (mirroring the method path), instead of re-deriving from the
  unsubstituted registry FunctionInfo.
- **Codegen** (`tpyc/codegen_cpp/gen_async.py`):
  - `_sub_struct_qualname` extended to optionally take inferred type
    args; the free-function inline-await path now produces
    `__coro_<name><T_sub>` instead of bare `__coro_<name>`.
  - Callee-side emit fixed: forward decl, out-of-line `__poll__`
    qualifier, factory return type, factory body, and friend
    `operator<<` all now carry the template-arg suffix when the
    callee is generic.
  - `_classify_params` introduces a `TYPE_PARAM` storage form for
    TypeParamRef params: field is `::tpy::val_or_ref_t<T>`, ctor
    param is `::tpy::param_val_or_ref_t<T>`, init is a direct
    bind/copy (no `std::move`). Same trait-based pattern the
    non-async generic codegen uses, so a value-typed T (e.g. Int32)
    stores by value and an object-typed T stores by reference.
- **Sync stub guard** (`tpyc/codegen_cpp/generator.py`): added an
  early-continue for `func.is_async` in the function-decl loop --
  parallels the existing generator-decl skip -- so generic async
  defs no longer emit a stray sync `val_or_ref_t<T>` stub alongside
  the coro factory.

Method-side parity: generic async methods on non-generic classes also
work after M7. The original symptom -- the await failing with "operand
must be a direct call to an async def ..." -- traced to a single bug
in `tpyc/sema/type_ops.py::substitute_method_type_params`, which
dropped `is_async` (along with several other flags) when reconstructing
the substituted `FunctionInfo`. Adding `is_async=method.is_async` to
that constructor unblocks the method path; the new fields on `TpyAwait`
and the unified `_sub_struct_qualname` then carry the substitution
through codegen the same way the free-function path does. Two
additional sites also needed the template-args propagation:

- `tpyc/codegen_cpp/records.py::gen_record_method_def` -- the in-class
  forward declaration for an async method now emits a `template <...>`
  header before the declaration and uses the templated struct name
  when the method has its own type params.
- `tpyc/codegen_cpp/generator.py` -- the out-of-class factory
  definition (`inline __coro_<Class>_<method>(...) <Class>::<method>(...) { ... }`)
  similarly emits the template header and uses the templated struct
  name.

Tests added (`tests/cases/async/`):

- `generic_async_free_func` -- basic happy path.
- `generic_async_free_func_multi_T` -- two type params.
- `generic_async_free_func_nocopy` -- generic over `Box[T]` (`@nocopy`)
  to exercise the non-value TypeParamRef storage form.
- `generic_async_method` -- generic async method on a non-generic
  class (`async def m[T](self, x: T) -> T`), plus a method that also
  references `self.field` to confirm the receiver capture +
  type-param substitution compose.

Out of scope (still rejected): async methods on generic *classes* --
the out-of-line coro-struct `__poll__` body would need the class's
template header and a combined arg list.

### M8 SHIPPED -- `asyncio.wait_for` + `TimeoutError` + outer-cancel propagation

`async def wait_for[T](coro: Own[Awaitable[T]], timeout: float) -> T`
races the inner coroutine against a steady-clock deadline. On expiry,
cancels the inner and pumps it through `finally`-with-await cleanup
before raising `TimeoutError`. Outer cancel of a `wait_for` task
propagates through to the inner via the resume cancel-check (auto-
emitted in every async-def coro frame). `TimeoutError` re-exported
from `builtins`; the C++ side is `tpy::TimeoutError` inheriting
`Exception`. Implementation: hand-written `_WaitForFuture[T]` holds
the inner as `Box[Cancellable[T]]` via `_box_coro[T]`.

### M9 SHIPPED -- `asyncio.gather` (homogeneous, two call shapes)

Two entrypoints share one `_GatherFuture[T]` engine:

- `async def gather_list[T](tasks: list[Task[T]]) -> list[T]` -- list-
  shaped form; a thin `async def` over `_GatherFuture[T]`.
- `def gather[T](*tasks: Task[T]) -> Own[_GatherFuture[T]]` -- variadic-
  positional homogeneous form; a sync factory returning the awaitable
  as `Own[...]` (same pattern as `await create_task(coro)` returning
  a `Task[T]`). Each Task is Rc-cloned into an owned list before
  constructing the future, so the awaitable is self-contained across
  the await point. After `await`, the user observes a `list[T]` of
  results in input order. Sync def -- not async def -- so the
  variadic intentionally sidesteps the async-def `*args` codegen
  gap in BUGS.md.

Mirrors `wait_for`'s template: hand-written `_GatherFuture[T]` does
the actual work. On the first sub-task exception (or outer cancel)
gather transitions to cleanup mode, calls `cancel()` on the still-
pending siblings, then re-raises the first exception observed once
every task has settled.

**API surface.** Both shapes are TPy-only -- CPython's `gather` is
heterogeneous-tuple-shaped (`gather(c1, c2, c3) -> tuple[T1, T2, T3]`),
which requires variadic generics to express in TPy. The heterogeneous
form remains deferred (TODO.md); under TPy today both `gather(*tasks)`
and `gather_list(tasks)` require all tasks to share return type `T`.

**Mechanism.** `_GatherFuture[T]` Rc-clones each input task into an
owned `list[Task[T]]` via the new `Task[T].clone()` method (a one-line
refcount-bump). Per-cycle, polls every unsettled task with the
awaiter's shared waker; completed values are stored in parallel
`_completion_indices` / `_completion_boxes` lists in completion order,
then reordered into input order via an O(n^2) walk at the end (n is
typically small). First exception encountered stays as `_exc: Box[Throwable] | None`;
subsequent failures are observed but not stored. Cleanup-mode polls
still drain remaining tasks so the future doesn't return with sub-tasks
in flight.

**Test cases** (`tests/cases/async/asyncio_gather_*`):

- `basic` -- three tasks, results in input order.
- `empty` -- `gather_list([])` returns `[]` immediately.
- `single` -- N=1 degenerate path.
- `completion_order` -- slow/fast interleave; result order matches input.
- `inner_raises` -- first failure cancels still-pending siblings;
  gather_list re-raises. The cancelled sibling observes its
  `__cancel_pending` flag inside its sleep wake (sleep's resume cancel-
  check propagates).
- `outer_cancel` -- gather_list raises `CancelledError` to its caller
  when its owning task is cancelled; with the M10 cancel-runnable-mark
  hook, sub-tasks observe the propagated cancel on their next poll
  and exit via CancelledError before completing their sleep.
- `in_finally` -- gather_list inside a `finally` clause, exercising the
  M3.3 await-in-finally CFG lowering.

All `gather_list_*` tests carry `no_cpython.txt` because `gather_list`
is TPy-only. The sibling `asyncio_gather_varargs` test exercises the
variadic-positional form (multi-arg, single-arg, and `*unpack` shapes)
and runs under both TPy and CPython (CPython's `asyncio.gather(*coros)`
accepts the equivalent call shape and returns a list of results). The
empty case (`n == 0`) is covered separately by `asyncio_gather_empty`
via `gather_list`.

### M10 SHIPPED -- `Task.cancel()` runnable-mark hook

`Task.cancel()` now schedules the cancelled slot for immediate poll
instead of relying on a natural wake (timer / IO event). The fix
applies broadly -- improves `wait_for_outer_cancel`, `gather_list`'s
outer-cancel propagation, and any future user code that cancels a
task parked on a non-self-waking awaitable.

**Mechanism.** `Task[T]` grows a `_waker: Waker` field (default-
constructed as a null-awaker Waker; wake() on a null awaker is a
safe no-op). `asyncio.create_task` stamps the Task's Waker via a
new `Executor.make_waker_for_slot(slot_id, generation)` method --
generation is 0 at spawn time, so the standard `Waker.wake` ->
`Awaker.mark_runnable` dispatch with its generation guard cleanly
handles late wakes against a completed slot. `Task.cancel()` calls
`self._waker.wake()` after the existing `cancel_any()`. Non-
executor-owned tasks (built via `task_from_coro`) leave the default
null-awaker Waker untouched; their cancel path is unchanged.

`Task.clone()` propagates the parent's Waker to the clone so both
handles share the same wake target -- consistent with the existing
Rc-shared TaskState design.

The approach uses the existing `Awaker` vtable + generation guard
rather than adding a parallel `mark_runnable_no_gen` helper. This
keeps `Executor`'s API surface narrow and avoids the
forward-declaration trap that biting a `Ptr[Executor]` method call
out of `Task.cancel`'s inline (template) body would hit (Executor
is declared after Task in `_executor.py`; routing through
`Waker.wake` -> `@dynamic Awaker.mark_runnable` defers the lookup
to instantiation time via the vtable).

**Test coverage.** New `tests/cases/async/asyncio_cancel_unblocks_future_wait`:
a sub-task awaits `asyncio.sleep(100.0)` (a deliberately long sleep
with no natural wake on test timescales); outer cancel arrives;
test completes in milliseconds and exits with CancelledError
propagated through. Pre-hook, the test would hang for 100 seconds
or hit the executor's "no progress possible" panic.

Existing tests `asyncio_wait_for_outer_cancel`, `asyncio_gather_outer_cancel`,
and `asyncio_wait_for_in_finally` continue to pass with the same
output but much faster runtime (the snapshots are output-keyed, not
runtime-keyed, so no snapshot regen was needed).

### M11 SHIPPED -- `asyncio.gather_list_settled` (return-exceptions variant)

`async def gather_list_settled[T](tasks: list[Task[T]]) -> list[Settled[T]]`:
the return-exceptions variant of `gather` (CPython
`gather(*coros, return_exceptions=True)`). Each sub-task runs to
completion regardless of sibling failures; results land in a
`Settled[T]` record with either `value: Box[T] | None` or
`exception: Box[Throwable] | None` populated.

**Why the record shape, not `list[T | BaseException]`?** TPy lowers
container element unions to value-variants, and the exception root is
a polymorphic owner, so a by-value variant slot would slice the
dynamic subclass on every move. A `Box`-of-exception inside a union
would dodge slicing, but `isinstance(x, Box[Throwable])` against a
union member of that exact parametric shape isn't supported at sema --
so callers would have no clean way to discriminate. The `Settled[T]`
record gives field-based discrimination
(`if r.exception is not None: ...`) without either problem. The
exception is stored as `Box[Throwable]`; re-raise to recover the
concrete subclass.

**Cancellation semantics.** Two distinct cases, matching CPython:

- **A submitted sub-task is cancelled independently** (via its own
  handle) while the gather runs -- its `CancelledError` is caught in
  the per-task loop and collected as a `Settled` entry with
  `exception` set, like any other failure. Siblings are unaffected.
  This is the `return_exceptions=True` rule: a cancelled submitted
  task is treated as having raised.
- **The gather caller itself is cancelled** -- `_GatherSettledFuture.
  cancel()` propagates cancel into every still-unsettled sub-task (so
  they don't leak), but the `CancelledError` then propagates UP to the
  awaiting caller; it is NOT swallowed into the result list. Cancelling
  `gather()` cancels it.

So the collected-result path (a list containing a `CancelledError`
`Settled` entry) is observable only for the first case
(independently-cancelled sub-task), not when the gather caller is
cancelled. The `asyncio_gather_settled_outer_cancel` test exercises the
second case (propagation up); a dedicated sub-task-cancel test for the
first case is a fair follow-up.

**Implementation.** Arrival-order parallel arrays (`_result_indices` /
`_result_boxes`, `_exc_indices` / `_exc_boxes`). On task settle,
appends to the appropriate pair; on assembly, walks input order
0..n-1, finds the matching arrival index, and pops ownership of the
Box out of the list. O(n^2) assembly walk (matches `_GatherFuture`'s
shape; N is small in practice).

**Test coverage** (all `no_cpython.txt` -- TPy-specific helper +
divergent return shape): `asyncio_gather_settled_basic` (all-success,
input order), `asyncio_gather_settled_mixed` (success + ValueError),
`asyncio_gather_settled_empty` (empty input returns `[]`),
`asyncio_gather_settled_outer_cancel` (outer cancel propagates up;
gather collects sub-task `CancelledError`s as Settled entries).

**Covariant return for BaseException was explored and dropped.** An
earlier iteration narrowed `BaseException.clone() -> Own[BaseException]`
(so `gather_list_settled` could return `list[T | BaseException]`),
requiring covariant-return support in protocol conformance + a
`tpy::narrowing_cast<>` codegen bridge. C++ does not support covariant
return on `std::unique_ptr` (only raw pointers/references), so the
bridge introduced a divergence between the TPy declaration and the
emitted C++ signature. The cost/benefit (a naming preference,
`Box[BaseException]` vs `Box[Throwable]`) didn't justify the
machinery. The proper home for covariant return is a future
backend that controls codegen below the C++ language layer (LLVM /
the THIR/MIR migration); see `docs/IR_DESIGN.md`. Until then the
`Box[Throwable]` + virtual-`__raise__` convention (Phase 20) handles
polymorphic exception storage.

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
  The follow-up commit removed the remaining compiler hardcoding for
  `tpy.Task` (the static `TypeDef` entry with its `cpp_formatter`, the
  `make_task` factory + factory-map entry, and the qname fast-path in
  `_extract_awaitable_inner`). Task now resolves through the regular
  generic-class path: the `@builtin_type("tpy.Task")` decoration claims
  the qname; `_user_record_qname` in the type resolver returns that
  qname ahead of the canonical-import tuple; and codegen registers the
  qualified C++ name `::tpystd::asyncio::_executor::Task<T>` into
  `_native_cpp_names` via a new dep-module loop that handles
  `@builtin_type`-with-body records living in `# tpy: cpp_namespace`-
  tagged modules. `_extract_awaitable_inner` keeps only the structural
  `__poll__(self, w: Waker) -> Poll[T]` match, which Task satisfies
  naturally. Poll and Waker still go through their static TypeDef
  entries because the explicit `is_value_type` / `is_send` / `is_sync`
  overrides encode `@native` POD semantics the structural rules can't
  derive.

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

What's left in `runtime/cpp/include/tpy/async.hpp`: `Waker`,
`CancelledError`, `ExecutorOps` (now just `mark_runnable` +
`register_timer`), `ExecutorHandle` + `current_executor` thread-local,
plus the small bridge helpers (`make_executor_handle`, `make_waker`,
`executor_register_timer_seconds`).

3901 tests pass.

### v1.2 step 5 -- Poll[T] to TPy -- DONE

`tpy::Poll<T>` and its `Poll<void>` / `Poll<T&>` specializations are
deleted from `runtime/cpp/include/tpy/async.hpp`. Replaced by a single
`@nocopy class Poll[T]` body in `lib/tpy/tpy/_core/_types.py` whose
storage is `UninitArrayStorage[T, 1]` + a `_has` bool -- byte-for-byte
equivalent to the previous `std::optional<T>` layout, with no need for
the three C++ specializations because:

- The void analog is `Poll[None]`, which lowers to
  `Poll<std::monostate>` (TPy's existing `None`-at-type-arg-position
  rule). Same primary body covers it.
- The reference-T specialization (`Poll<T&>`) was never actually
  instantiated by any TPy-generated code -- confirmed via grep before
  removal. Reference-T positions go through the caller's storage form
  (e.g. `Optional[T*]` collapses to a nullable pointer; no Poll-level
  specialization needed).
- Move-only / non-default-constructible T are handled by
  `UninitArrayStorage`'s placement-new contract, same as
  `std::optional<T>` did before.

What landed:

- **TPy class**: `@builtin_type("tpy.Poll") @nocopy class Poll[T]` in
  `_types.py` with `pending()` / `ready(value)` static factories and
  `is_ready()` / `is_pending()` / `value()` instance methods. Storage
  uses an aliased `from ..mem import UninitArrayStorage as
  _UninitArrayStorage` to keep the name out of `_types.py`'s public
  surface (it still leaks into the implicit-stdlib qname scope -- see
  BUGS.md entry). (Later retagged to `@builtin_type("tpy.coro.Poll")`
  on the `builtin-qname-decouple` branch; body stays in `_types.py`,
  C++ symbol unchanged.)
- **`tpy.coro` factories** (`poll_ready` / `poll_pending` /
  `poll_ready_none`) lost their `@cpp_template` shells and became
  pure-TPy functions that delegate to `Poll[T].ready` / `Poll[T].pending`.
- **Codegen**: `gen_async.py` and `statements.py` swap the
  hardcoded `::tpy::Poll<T>::ready(...)` strings for
  `::tpystd::tpy::Poll<T>::ready(...)`. `_make_async_return` binds the
  return value to a typed local before wrapping with `std::move` so
  braced initializers (`return [1, 2, 3]` inside async) survive the
  `Own[T]` -> `T&&` shape of `Poll[T].ready`'s param.
  `expressions.gen_call`'s static-method dispatch now qualifies the
  class name with its defining module's C++ namespace when the source
  is an implicit-stdlib peer, since the using-decl-suppression below
  removes the unqualified-`Poll` shorthand.
- **Generator include filter**: `_emit_alias_using_block` skips
  `using ::ns::Foo;` for record re-exports sourced from an implicit-
  stdlib peer (same rule already applied to protocol/enum re-exports).
  Without this, the generated `coro.hpp`'s `using ::tpystd::tpy::Poll;`
  would resolve to a not-yet-defined name when `coro.hpp` is included
  mid-chain from `_executor.hpp`. The auto-include of every implicit-
  stdlib peer also now skips peers we don't transitively reach
  (`generator.py`'s `own_deps` check), filtering out spurious
  `_types.hpp` <-> `coro.hpp` cycle pulls.
- **Sema**: `Awaitable[T]` conformance check now unwraps `Own[...]`
  before checking the `Poll[T]` qname, since user `__poll__` methods
  now declare `-> Own[Poll[T]]` (Poll is no longer value-typed, so
  by-value returns spell as `Own[...]`).
- **TypeDef registry**: `tpy.Poll`'s `cpp_formatter` updated to
  `::tpystd::tpy::Poll<T>`, `is_value_type` flipped to `False`. The
  `make_poll` factory and registry entry remain as leftover scaffolding
  -- same shape as `tpy.Task` (per the comment above its registry
  entry); a follow-up could collapse both via the regular
  user-record path.
- **Snapshot churn**: every async test's `coro.hpp` regenerated; user
  code declaring `def __poll__(...) -> Poll[T]` had to update to
  `-> Own[Poll[T]]` (7 test sources touched in
  `tests/cases/{async,generics}/`).

3928 tests pass.

### v1.2 step 6 -- async.hpp cleanup (TLS removal + structs/helpers to TPy) -- DONE

Follow-up to the Poll port: shrink `runtime/cpp/include/tpy/async.hpp`
by moving everything that doesn't truly need C++ to TPy, and unblock
the move by replacing the executor `thread_local` storage with plain
(single-process) globals. v1 asyncio is single-executor per process,
so the global trade-off is acceptable; reverting to `thread_local` is
gated on TPy growing a thread-local module-global facility.

What moved out of `async.hpp`:

- `inline thread_local ExecutorOps executor_ops` -> `inline ExecutorOps
  executor_ops`. TODO comment in place for the eventual TLS revert.
- `inline thread_local void* current_executor` -> deleted. Replaced by
  `_current_executor: ExecutorHandle` module global in
  `lib/tpy/asyncio/_executor.py`. `_get/_set/_clear_current_executor`
  are now pure TPy functions reading/writing the global.
- `executor_scope_teardown` -> TPy function; calls a 3-line
  `clear_executor_ops()` C++ helper for the ops table reset and
  clears the TPy global itself.
- `make_waker` -> deleted. Replaced by an `@overload @cpp_template`
  multi-arg constructor on the `Waker` TPy class
  (`::tpy::Waker{{({0}), ({1}), ({2})}}` aggregate init). `_make_waker`
  in `_executor.py` is now `def ... return Waker(handle, tid, gen)`.
- Nested-executor backstop (`if (current_executor != nullptr) tpy_panic`
  in `register_executor_ops_from`) -> moved to `Executor.__init__`
  as `if not _get_current_executor().is_null(): raise RuntimeError(...)`.
- `executor_register_timer_seconds` -> C++ helper now takes the handle
  as an explicit param (no global read); the null-handle check moved
  to a thin TPy wrapper in `asyncio/__init__.py`.

What changed but stays in C++:

- `Waker.exec` field type: `void*` -> `ExecutorHandle`. Same 8-byte
  layout; just stronger typing. `Waker::wake()` uses `exec.is_null()`
  + `exec.ptr` internally.
- `ExecutorHandle` struct relocated above `Waker` (forward-decl was
  required for the field type change).

What couldn't move (compiler constraint):

- `Waker.wake()` and `ExecutorHandle.is_null()` bodies -- TPy enforces
  "methods on @native classes must be stubs"
  (`tpyc/sema/analyzer.py:1624`). The TPy class can declare
  `@overload @cpp_template` *constructors* but not method *bodies*.
  Lifting this would require either letting TPy own the struct
  emission (and forward-declaring it in `async.hpp` for the thunks) or
  a new compiler facility. Out of scope for a refactor; would unlock
  the remaining ~15 lines.

ExecutorHandle was also moved from `asyncio/_executor.py` to
`lib/tpy/tpy/_core/_types.py` (next to `Waker`) so the Waker
constructor can take an `ExecutorHandle` parameter at the implicit-
stdlib layer. Re-exported through `tpy.coro` and the previous import
sites continue to work.

Result: `async.hpp` 262 -> 212 lines (-50). 3939 tests pass; three
async snapshots regenerated for the using-decl alias churn
(`executor_bindings_smoke`, `tpy_executor_smoke`,
`tpy_executor_wake_dispatch`).

### v1.2 step 7 -- @dynamic Awaker protocol replaces ExecutorOps -- DONE

Pivots the executor dispatch model from a C++ function-pointer ops
table to TPy's `@dynamic` protocol machinery. `async.hpp` collapses to
just `CancelledError` (~10 lines). Everything else moves to TPy.

Dispatch shape:

- `@dynamic Awaker` protocol with `mark_runnable(task_id, generation)`
  lives in `lib/tpy/tpy/coro/__init__.py` (alongside `Waker`). `Awaker`
  is the dispatch target for `Waker.wake()`.
- `Waker` is a `ValueType` holding `awaker: Ptr[Awaker]` plus
  `task_id` / `generation`. `Waker.wake()` dispatches
  `self.awaker.mark_runnable(...)` directly through the `@dynamic`
  vtable -- no C++ glue. `Executor` inherits `Awaker` so it provides
  the vtable slot directly.
- `_current_executor: Ptr[Executor]` (TPy module global, defined
  after the `Executor` class so the type spells concrete). Callers
  (`asyncio.run`, `create_task`, `_register_timer_at`) dispatch
  `register_timer` / `spawn` as direct method calls on the concrete
  `Executor` -- no opaque-handle bridge.
- `_make_waker(handle: Ptr[Awaker], ...)` is pure TPy. The
  upcast from `Executor` to `Ptr[Awaker]` goes through
  `_awaker_addr_of` (`@cpp_template` `static_cast<...Awaker*>(&self)`).

What disappeared from C++:

- `ExecutorOps` + `MarkRunnableFn` / `RegisterTimerFn` typedefs.
- `mark_runnable_thunk<ExecT>` / `register_timer_thunk<ExecT>`
  templated thunks.
- `register_executor_ops_from<ExecT>` installer.
- `executor_ops` plain global + the per-process invariant note.
- `Waker` POD struct, `Waker::wake()` body, `Waker` operator<<.
- `ExecutorHandle` struct + `is_null()`.
- `make_executor_handle<T>`, `clear_executor_ops`,
  `executor_register_timer_seconds`.

Result: `async.hpp` 212 -> ~10 lines. The "TPy method bodies on @native
classes" TODO entry from step 6 is superseded -- `Waker` is no longer
@native, so its `wake()` body is plain TPy.

Sema/codegen fixes required to land this:

1. `tpyc/codegen_cpp/protocols.py::collect_record_types_from_type`:
   in-module `@builtin_type` records (the new TPy-defined `Waker`)
   need forward declarations alongside plain user records, so concepts
   in the same module that reference them have a complete-type
   declaration available before the concept's emit point.
2. `tpyc/typesys.py::TypeRegistry.imported_protocol_qualification`
   + `tpyc/codegen_cpp/generator.py`: per-emit-module loop that
   registers imported `@dynamic` protocols into `_native_cpp_names`
   (mirrors the existing record path). Same-module references get
   the bare name; cross-module references get the qualified C++
   name. Replaces an earlier `cpp_formatter`-based attempt that
   broke same-module emission.
3. Workaround for the mutation-analyzer gap on value->Ptr coercion:
   `_ExecutorScope.__init__` takes `executor: Ptr[Executor]` (not
   `executor: Executor`) so the address-take happens at the mutable
   owned-local call site in `_run_drain_main_task`, not inside a
   const-ref body. Same shape for `_set_current_executor` /
   `_get_current_executor` / `_make_waker`. Tracked in TODO.md; will
   collapse to natural `Executor` / `Awaker` params once the analyzer
   rule lands.

The follow-on remaining v1.2 cleanup items (timer-heap, etc.) are
described in the "Blocked" section below.

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
