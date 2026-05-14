# Async/Await Design

## Milestone roadmap

Milestones are explicit and ordered. Items below their introducing milestone may be advanced if the implementation needs them; nothing here is set in stone, but the milestone shape is.

### v1 -- core async (the foundational milestone)

The minimum that lets you write meaningful async programs and validates the codegen + runtime contract.

| Item | Description |
|------|-------------|
| `async def`, `await` | Statement and expression positions. |
| Sema exclusions | Reject `async def` + `@error_return`, `async def` + `yield` (async generators), `async def` + `@noalloc`, and `__await__` on user types. Each rejection is "not yet supported", not "forbidden forever". |
| Resumable-frame abstraction | Compiler-internal abstraction for state-machine lowering with proper try/except/finally re-establishment (Codegen Transform section). |
| `async def` lowering | `Coroutine[T]` struct conforming to `Awaitable[T]`. |
| Coroutine value model | `Coroutine[T]` is a single-use, must-use value type. Sema enforces "consumed by `await` or by `Task` wrapping" -- can't await twice, can't drop unused, can't store after consumption. |
| `Task[T]` via purpose-built poll-box | Type-erased C++ runtime template (~50 LOC) holding a heap-allocated coroutine frame. Independent of TPy's `@dynamic` infrastructure. |
| Cancellation | `cancel_pending` flag on the frame; throw `CancelledError` at the resumed-await position; propagation via C++'s native exception mechanism (no manual unwind bitset). |
| Runtime in `tpy` | `Awaitable[T]`, `Coroutine[T]`, `Poll[T]`, `Waker`, `CancelledError`. |
| `asyncio` library | `run`, `create_task`, `sleep`, `Task`, `Future` (manual completion, single-awaiter), `CancelledError`. Internal `Executor` (not public). |
| Simple executor | Deque + timer min-heap. ~150-200 lines of TPy. |

### v1.1 -- asyncio runtime port to TPy (DONE; must precede v1.5)

The v1 asyncio runtime (Executor + run loop + spawn registration + sleep timer) shipped in C++ for v1 implementation speed. The design specified `~150-200 lines of TPy` for the executor; this milestone closed that gap. **Landed before v1.5** so `gather` / `wait_for` / `async with` / `async for` can be written as TPy on a TPy executor instead of more C++ template machinery on top of the C++ executor.

Phased; each phase shipped independently. Detailed plan + per-phase scope + compiler-gap list is in `docs/ASYNC_PROGRESS.md`.

| Phase | Item | Description |
|-------|------|-------------|
| 0 (DONE) | `SleepFuture` -> TPy | Validates the shape: TPy class with `__cancel_pending` field works as a `Task<void>::from_coro` `CoroT`. C++ keeps a single bridge helper `executor_register_timer_seconds`. |
| 1 (DONE) | Compiler bindings | `time.sleep_until_steady`, `current_executor` get/set/clear via `ExecutorHandle` POD, `AnyTaskBox` `@native` wrapper around `shared_ptr<AnyTask>` (deviates from the original draft's `unique_ptr` so the user `Task<T>` and the executor slot can share state), `task_poll_cancelled` test util as TPy generic function. No new compiler features required. |
| 2 (DONE) | `Executor` body to TPy | Slot table (`list[Slot]`), runnable deque, timer min-heap, all methods (`spawn`, `mark_runnable`, `poll_slot`, `drain_runnable`, `wait_for_event`, `run_until`, `drain_spawned_with_cancel`). Plus `ExecutorOps` dispatch table so `Waker::wake` / `make_user_task` / `executor_register_timer_seconds` route from C++ into the TPy executor. Deviation: timer-heap uses `list[tuple[float, UInt64]]` + parallel `dict[UInt64, Waker]` instead of `list[tuple[float, Waker]]` (Wakers aren't Comparable, which `heapq[T: Comparable]` requires). |
| 3 (DONE) | `async_run` to TPy | Setup/teardown via a `_ExecutorScope` class with `__del__` (RAII for `current_executor` clear-on-exit; v1 doesn't nest so no save/restore needed). Run loop body is TPy. Main-coro spawn closure + result extraction initially stayed C++ (templated over `ResultT`, used `if constexpr` for the void return-type case); fully ported to TPy in v1.2 step 1 once `val_or_ref_t<void>` was specialized. |
| 4 (DONE) | Cleanup | Removed `tpy::Executor` struct + legacy dispatch fallbacks (`Waker::wake` cast, `make_user_task` spawn fallback, `executor_register_timer_seconds` TimePoint path) + unused TPy bindings. `tpy::async_run` was retained as a ~25-line shell, then removed in v1.2 step 1 below. The residual C++ surface (`Task<T>`, `TaskState<T>`, `AnyTaskBox`, `Poll<T>`, `current_executor` thread-local) is itemized in v1.2 below. |

### v1.2 -- compiler-driven shrinking of v1.1's residual C++ surface

Each item below is blocked on a specific compiler bug or missing feature. Orthogonal to v1.5 -- progress on either track is independent.

**Shipped (v1.2 step 1):**

- `val_or_ref_t<void>` specialization in `runtime/cpp/include/tpy/type_traits.hpp` -- unblocks generic `def f[T] -> T` for `T = None`.
- `asyncio.run` ported to pure TPy in `lib/tpy/asyncio/__init__.py`; `tpy::async_run` C++ template shell removed. `coro: Own[Awaitable[T]]` is the correct annotation for a consuming param.

**Shipped (v1.2 step 2):**

- `@cpp_template` calls now route through `gen_call_arg(inline_template=True)` for arg generation, so the auto-move-at-last-use logic fires for `Own[T]` args in cpp_template calls (`tpyc/codegen_cpp/builtins.py`, `tpyc/codegen_cpp/expressions.py`). Removed redundant manual `std::move({0})` from `_make_executor_owned_task` (asyncio) and `unsafe_init` (tpy.unsafe). Closes the codegen part of the old "Awaitable[T] rvalue forwarding" bug; the remaining piece is a sema-diagnostic gap tracked in `BUGS.md`.

**Compiler bugs still blocking further cleanup (`BUGS.md`):**

| Bug | What it unblocks |
|-----|------------------|
| Ref-type `list[T].pop()` into a local fails C++ build (`val_or_ref_t<T> = T&` can't bind to rvalue) | Ref-type `TimerEntry` → `list[TimerEntry]` (sibling path to the ValueType variant below). Either fix lets `Executor.timer_heap` drop the parallel `_timer_wakers` dict. |
| Non-`@native` ValueType record's `is_value_type` specialization emitted after template instantiation in same TU | Value-type `TimerEntry(ValueType)` → `list[TimerEntry]` (sibling path to the ref-type variant above). |
| `@native` value-type record emits `is_value_type` in wrong namespace | `ExecutorHandle` storable as a TPy field → `_ExecutorScope` can save/restore the prior handle (today just clears, since v1 doesn't nest `asyncio.run`). Needed before nestable runtimes. |
| `@cpp_template` literal `{...}` produces internal error | Clearer diagnostic; pairs with the escape-syntax feature below. |

**Compiler features (`TODO.md`):**

| Feature | Status | What it unblocks |
|---------|--------|------------------|
| Single-threaded shared-ownership smart pointer `Rc[T]` | **shipped** (`lib/tpy/tplib/rc.py`; construct via `Rc.new(value)`) | `TaskState[T]`, `Task[T]`, `AnyTaskBox`, `AnyTask` → TPy. Biggest remaining shrink: removes the type-erasure stack. Now unblocked — implementation work is a v1.2 follow-up commit. |
| Atomic `Arc[T]` + `Weak[T]` | v3+ (multi-threaded executor + multi-awaiter Future) | Cross-thread async surface. Not blocking single-threaded v1.x cleanup. |
| `thread_local` storage in TPy | not started | `current_executor` + handle accessor bridges → TPy. Low priority for single-threaded v1. |
| Generic class specializations for void/reference/move-only `T` | not started | `Poll[T]` → TPy. Removes the last primitive in `runtime/cpp/include/tpy/async.hpp`. |
| `@cpp_template` literal-brace escape syntax (`{{` / `}}`) | not started | `make_executor_handle` / `make_waker` → one-line `@cpp_template` bindings (today both wrap C++ helpers). |

**Cumulative effect** once all land: `runtime/cpp/include/tpy/async.hpp` collapses to just the `Waker` POD + `CancelledError` + optionally the `ExecutorOps` dispatch table (or its TPy-only equivalent if `thread_local` lands). Everything else lives in TPy.

**What the v1.1 port left explicitly C++ for the long term**: `Task<T>::from_coro<CoroT>` (templated factory; could become `@cpp_template` once the brace-escape lands), `Poll<T>` storage (compiler item above), the templated thunk machinery (`mark_runnable_thunk` / `spawn_thunk` / `register_timer_thunk`) used by `ExecutorOps` (only goes away if the dispatch model changes -- not blocked on a compiler item, just an architectural choice).

### v1.5 -- composability surface

Adds the patterns most async code actually needs. Built on v1's frame model; no new compiler primitives.

| Item | Description |
|------|-------------|
| Sync `with` upgrade | `__exit__(exc_type, exc_val, exc_tb)` + `True`-suppresses semantics. Required prerequisite for `async with` parity. See "Sync `with` upgrade" section. |
| `async with` | With full `__aexit__(exc_type, exc_val, exc_tb)` + suppression semantics. |
| `async for` | With `__anext__` throwing `StopAsyncIteration` at iteration end. |
| `gather` | Run multiple coroutines concurrently; cancel-siblings on first failure semantics matching CPython. Result type still under design (see Open Questions). |
| `wait_for` | Race against a timeout; raises `TimeoutError`. |
| `StopAsyncIteration`, `TimeoutError` | Exception types. |

### v2 -- breadth

Library breadth + first real I/O. Each item is sized to land independently.

| Item | Description |
|------|-------------|
| Sync primitives | `Lock`, `Event`, `Queue`, `Semaphore`. Built on `Future[T]`. |
| Multi-awaiter `Future[T]` | If single-awaiter v1 turns out to be limiting in practice. |
| Task introspection | `Task.add_done_callback`, `get_name`, `set_name`, `done`, `result`, `exception`. |
| Public `Reactor` protocol | Designed against the first concrete backend's needs (not before). |
| First I/O reactor | epoll on Linux. Defines fd-backed awaitables. |
| asyncio streams | `StreamReader`, `StreamWriter`, `open_connection`, `start_server`. |

### v3+ -- post-v2

Independent extensions, listed in no particular order. Each is its own design exercise; treat as a placeholder rather than a plan.

| Item | Why it's later |
|------|---------------|
| Additional reactors (kqueue, io_uring, asio, libuv) | One I/O backend in v2 validates the `Reactor` protocol; alternatives can follow. |
| Multi-threaded executor | Requires E1 (Send/Sync) progress; opt-in, single-threaded path keeps working. |
| `@error_return` async (`await foo()?`) | Cross-tier composition needs its own design. |
| Async generators (`async def` + `yield`) | Combines two state machines. |
| `__await__` adaptation | For user CPython interop. |
| `@noalloc` async | Frame allocation control. |
| Subprocess, signals, DNS, SSL | Each its own integration with the v2 reactor. |
| `asyncio.Future` cross-thread completion | Multi-threaded extension. |

### Future extension (separate refactor)

| Item | Description |
|------|-------------|
| Migrate generators onto resumable-frame | Tracked in TODO.md (Refactor). Generators today have their own statement-level codegen; once async ships, the language has two parallel state-machine codegens. Migration unifies them. |

---

## Goals

1. **Standard Python `async def`/`await` syntax** that runs in TPy-compiled form and (within the v1/v1.5 surface) under CPython without source changes.
2. **Performance comparable to native C++ async stacks.** 100k-connection-class workloads must be reachable, even if v1 doesn't ship the I/O reactor needed to demonstrate it.
3. **Composability with the rest of TPy**: integrates with the exception model, value-type ownership, etc.
4. **A user-replaceable asyncio surface** so tplib (or user code) can ship its own scheduler, reactor, and primitive set without touching the language.
5. **Path to LLVM backend**: the lowering must not depend on C++20 coroutines or any other C++ compiler feature. State-machine codegen we own end-to-end.

## Non-goals

- Full CPython `asyncio` surface. v3+ may grow more, but `EventLoopPolicy`, custom loops, and the older callback-style API are out of scope.
- A real I/O reactor in v1; a public `Reactor` protocol before v2.
- Multi-threaded executor before v3.
- Async generators, `__await__` adaptation, `@noalloc` async, `@error_return` async in v1.

---

## Design principles

1. **Compiler emits the state machine.** `async def` lowers to a TPy struct with a `poll(waker) -> Poll[T]` method. C++20 coroutines are not used.

2. **Awaitability is structural.** A type is awaitable iff it has the right `poll` method. Same model as `Iterator[T]`.

3. **Single-threaded by default.** v1 executor is single-threaded; no `Send`/`Sync` constraints surface. Public APIs stay neutral on threading.

4. **Exception-based cancellation.** `Task.cancel()` injects `CancelledError` at the next suspension point.

5. **Minimal compiler surface, maximal library surface.** Compiler knows about a small set of types in `tpy`; everything user-facing lives in `asyncio` and is replaceable.

6. **CPython source compat within scope.** v1/v1.5 surface runs unchanged in both. We don't promise compat for unsupported `Task`/`Future` methods or for asyncio modules outside the supported set.

7. **New resumable-frame abstraction; don't extend generator codegen.** Owning the lowering preserves the LLVM-backend path; absorbing generator codegen would be its own large project. Migrate generators later.

8. **No dependency on `@dynamic` for v1.** `Task[T]` storage is a purpose-built type-erased poll-box, independent of TPy's `@dynamic` protocol infrastructure (which doesn't support generic protocols or owning fields today, and we don't want to block on advancing it).

---

## Decisions (locked)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Lowering | New shared resumable-frame abstraction; state-machine struct with `poll(waker) -> Poll[T]` | Owning the lowering preserves the LLVM-backend path. |
| Awaitable protocol | Structural; type is awaitable iff it has `poll(self, waker: Waker) -> Poll[T]` | Matches `Iterator[T]`. Zero-cost dispatch via monomorphization. |
| `Poll[T]` representation | Custom `tpy::Poll<T>`: tagged value with `Pending` and `Ready[T]` states (special-cased for void/reference/move-only/non-default-constructible T) | Cleanest API; alternatives (`std::variant`, `std::optional`) have known holes. |
| `Coroutine[T]` value model | Single-use, must-use value type; sema enforces consumption-on-await-or-Task-wrap; sub-coroutines live as in-frame fields where statically known | Avoids per-call heap allocation for deep async chains; "single-use" matches CPython. |
| `Task[T]` heterogeneous storage | Purpose-built C++ runtime poll-box: `Task<T>` holds `unique_ptr<AsyncFrameBase<T>>`; `AsyncFrameImpl<CoroT>` wraps a concrete coroutine frame and forwards `poll`. Same pattern as `std::function`. | Self-contained C++ template, ~50 LOC of runtime. Independent of `@dynamic` infrastructure (which doesn't support generic protocols / owning fields today). |
| Executor threading | Single-threaded for v1 | No `Send`/`Sync` work needed. 100k-conn workloads are I/O-bound. |
| Cancellation | Exception-based: `cancel_pending` flag, throw at resumed-await position, propagation through C++ native exception mechanism (per-case try/except/finally re-establishment) | Matches CPython. No manual unwind tracking. |
| Exception storage in `Task` | `std::exception_ptr` to preserve dynamic type after catch | C++ exceptions thrown by value, caught by reference; can't store as `BaseException` value without slicing the dynamic type. |
| `@error_return` interaction | Mutually exclusive with `async def` in v1; sema rejects "not yet supported" | Defers cross-tier design. |
| Naming | CPython names (`Awaitable`, `Coroutine`, `Task`, `asyncio.Future`) | Familiar. |
| Where it lives | `tpy` for compiler-referenced primitives; `asyncio` for the library API | `import asyncio` works the same in both. |
| `lib/cpy/asyncio.py` stub | Not shipped | A stub `from asyncio import *` would import itself via PYTHONPATH. CPython phase uses CPython's stdlib `asyncio`. |
| Public `Reactor` protocol | Out of v1 (v2 item) | Shape will follow the first real backend. |

---

## Coroutine value model

`Coroutine[T]` is the type returned by `async def`. Each `async def` lowers to a distinct generated struct conforming to `Awaitable[T]`.

### Single-use, must-use semantics

A `Coroutine[T]` value is **single-use** (consumed by `await` or `Task` wrap) and **must-use** (constructing one and dropping it without consumption is a sema error). Concretely:

- `await x` consumes `x`. After this, `x` is inaccessible.
- `Task(x)` / `create_task(x)` consumes `x` into a heap-allocated Task.
- `helper(x)` -- passing a coroutine to a function -- moves ownership to the helper. The helper must consume it (or pass it on); sema tracks this transitively.
- Returning a coroutine moves it to the caller, who must consume it.
- A coroutine value at end of scope without any consumption is a sema error.
- Storing a coroutine in a list is allowed (the list owns it); the list must eventually pass each element to `await` or `Task`. (Concrete sema rules for containers covered in Open Questions; v1 may restrict to homogeneous lists for `gather`.)

This matches CPython (a coroutine raises if awaited twice, and "coroutine was never awaited" warns at GC time). The closest existing TPy analog is `Own[T]`; the implementation can lean on the same lifetime tracking infrastructure.

### Where the frame lives

| Context | Allocation |
|---------|-----------|
| Local in `async def` (`x = some_coro()`) | Stack value, until consumed by `await` or `Task` wrap. |
| Awaited inside `async def` (`await some_coro()`) | Moved into a frame field (`__sub_*`) of the parent coroutine's frame -- *when statically known*. Otherwise (e.g. dynamic awaitable held via `Task`), its own heap allocation. |
| Wrapped in `Task` (`create_task(coro)`) | Heap-allocated. The Task owns the heap allocation; address is stable for the task's lifetime. |
| Stored in a list/dict | Stored as the value type (move-only). The container owns it. When awaited, moved into a frame field or wrapped in a Task. |

### Top-level allocation

For a chain `f -> g -> h` where each `await`s the next, *and where each sub-coroutine type is statically visible at its `await` site*, only the top-level Task heap-allocates. `g`'s frame is a field inside `f`'s frame; `h`'s frame is a field inside `g`'s. All in one heap block.

When the awaited type isn't statically known (e.g. awaiting a `Task[T]` whose inner coroutine type is erased, or awaiting through a dynamic protocol), the inner coroutine has its own (small) heap allocation. This is unavoidable cost for dynamic dispatch -- same as Tokio's `Box<dyn Future>`.

### `Task[T]` storage

`Task[T]` is a purpose-built type-erased poll-box, written by hand in the runtime:

```cpp
namespace tpy {

template <typename T>
struct AsyncFrameBase {
    virtual Poll<T> poll(Waker waker) = 0;
    virtual void cancel() = 0;
    virtual ~AsyncFrameBase() = default;
};

template <typename T, typename CoroT>
struct AsyncFrameImpl : AsyncFrameBase<T> {
    CoroT coro;
    explicit AsyncFrameImpl(CoroT&& c) : coro(std::move(c)) {}
    Poll<T> poll(Waker w) override { return coro.__poll__(w); }
    void cancel() override { coro.__cancel_pending = true; }
};

template <typename T>
class Task {
    std::unique_ptr<AsyncFrameBase<T>> frame_;
    // ... task state, result/exception storage, cancel state, etc.
public:
    template <typename CoroT>
    Task(CoroT&& c) : frame_(std::make_unique<AsyncFrameImpl<T, CoroT>>(std::forward<CoroT>(c))) {}

    Poll<T> poll(Waker w) { return frame_->poll(w); }
    void cancel() { if (frame_) frame_->cancel(); }
};

} // namespace tpy
```

About 50-80 LOC of runtime template code. One virtual call per Task poll (the same vtable cost any type-erased poll-box pays). Static `await` of a known coroutine type still goes through structural `Awaitable[T]` -- zero-cost via monomorphization.

This is independent of TPy's `@dynamic` protocol mechanism. `@dynamic` doesn't currently support generic protocols (`Awaitable[T]` is generic over T) or owning fields without `Box[P]`; both are tracked as future items in `docs/DYNAMIC_PROTOCOL_DESIGN.md`. Building Task on `@dynamic` would block v1 on advancing those features. The hand-written poll-box ships immediately.

### Parameter capture

Async params become frame fields; the capture rule depends on whether the coroutine can outlive the calling scope (as it can for any `create_task`'d coroutine):

- **Value types** (int, float, tuple, etc.): copied into the frame.
- **`Own[T]`** params: moved into the frame.
- **Reference types** (list, dict, classes): borrow-checker enforces that the referent outlives the coroutine. For coroutines that are immediately awaited (synchronous-shaped `await some_async(items)`), this is provable and the borrow is fine. For escaping calls (`create_task(some_async(items))` where `items` is local to the caller), the borrow checker rejects it -- the caller must pass `Own[T]` (move ownership in) or wrap in `Box`.

This is escaping-closure semantics; reusing whatever TPy's existing closure capture rules require. A reference borrow that escapes a non-async closure is already rejected; async inherits the same rule. (If gaps surface in TPy's closure-borrow tracking, they're shared bugs.)

---

## Resumable-frame abstraction

The compiler-internal abstraction shared by `async def` lowering (and, eventually, generators) is a **resumable frame**: a struct that holds suspended state and a method that resumes it.

### Components

| Component | Description |
|-----------|-------------|
| State integer | Discriminates which suspension point to resume from. `S_INITIAL = 0`, `S_<n>` for each suspension in source order, `S_DONE`. |
| Captured params | Frame fields per the capture rules above. |
| Hoisted locals | Any local live across at least one suspension is hoisted to a frame field. |
| Expression result slots | When `await x` appears in a non-statement position, each await's intermediate result is a frame field. The lowering breaks compound expressions into a sequence of "compute -> await -> store result" steps. |
| Sub-future fields | Each `await x` allocates a frame field of `x`'s type, wrapped in `std::optional<>` so it can be destroyed in place after `Ready` *or* after a caught exception (see Frame Cleanup). Distinct awaits at non-overlapping suspension intervals can share a single field via `std::variant` (see Open Questions). |
| `cancel_pending` flag | Set by `Task.cancel()`; checked at every suspension's resume point (inside the appropriate try wrappers). |
| Resume table | The body of `poll(waker)` is a `switch (state)` over resume points. Each case re-establishes the source-level try/except/finally structure that was active at the corresponding suspension. |

### Try/except/finally re-establishment

This is the load-bearing part of the abstraction. **Each case body is wrapped in the source-level try/except/finally structure that was active at that suspension point.** This is how throws (from cancellation, from sub-future poll, from user code) propagate through the right user handlers.

If the source is

```python
try:
    x = await a()
    try:
        y = await b()
    except ValueError as e:
        handle(e)
    finally:
        cleanup_b()
finally:
    cleanup_a()
```

then `case S_AFTER_A:` is wrapped in `try { ... } catch (...) { cleanup_a(); throw; }` (the outer finally). `case S_AFTER_B:` is wrapped in two layers. The cancel check at each suspension is inside the wrappers, so a thrown `CancelledError` triggers the right handlers.

Standard state-machine codegen pattern. No `__unwind` bitset, no manual finally-running; let C++'s native exception machinery do it.

### Finally-body deduplication

Naive expansion duplicates each `finally` body at every site that may throw or fall through. For non-trivial finally bodies, this is real code bloat. Codegen emits each finally as a private member function of the frame struct (`__finally_outer()`, `__finally_inner()`, ...) and calls it from each path. The bodies have access to frame fields via `this`.

### Move/pin rules

- A coroutine frame must not move once it's the target of a sub-future field assignment or a Task heap-allocation. Single-use sema enforces this implicitly: by the time it's stashed, it's been consumed (and the storage is its final home).
- Sub-future fields live at stable sub-addresses inside the parent's frame. As long as the parent's allocation doesn't move, sub-futures are pinned automatically.

---

## Codegen transform

### `async def` lowering -- worked example

```python
async def fetch(url: str) -> str:
    try:
        resp = await http_get(url)
        return resp.body
    finally:
        log("done")
```

```cpp
struct __FetchCoro {
    int32_t __state;
    bool __cancel_pending;
    std::string url;
    std::optional<HttpGetCoro> __sub_0;
    HttpResponse resp;

    enum { S_INITIAL = 0, S_AFTER_HTTP_GET = 1, S_DONE = 2 };

    void __finally_outer() { log("done"); }

    tpy::Poll<std::string> poll(tpy::Waker waker) {
        switch (__state) {
        case S_INITIAL:
            try {
                __sub_0.emplace(http_get(std::move(url)));
                __state = S_AFTER_HTTP_GET;
            } catch (...) {
                __finally_outer();
                throw;
            }
            [[fallthrough]];
        case S_AFTER_HTTP_GET: {
            try {
                if (__cancel_pending) {
                    __cancel_pending = false;
                    throw tpy::CancelledError();
                }
                auto r = __sub_0->poll(waker);
                if (r.is_pending()) return tpy::Poll<std::string>::pending();
                resp = std::move(r.value());
                __sub_0.reset();
                __state = S_DONE;
                std::string result = resp.body;
                __finally_outer();
                return tpy::Poll<std::string>::ready(std::move(result));
            } catch (...) {
                __sub_0.reset();   // explicit cleanup of in-flight sub-future
                __finally_outer();
                throw;
            }
        }
        case S_DONE:
            tpy_panic("poll after Ready");
        }
    }
};
```

Each case body is wrapped in the source-level try/finally; the cancel check is inside the wrapper. `__finally_outer()` consolidates the `finally` body. The `catch (...)` resets in-flight sub-future slots before running the finally, so caught exceptions don't leave stale slots in the frame.

### Multi-await in expressions

`x = await a() + await b()` is rewritten in sema to a sequence with intermediate slots:

```python
__r_a = await a()
__r_b = await b()
x = __r_a + __r_b
```

Both `__r_a` and `__sub_b` cross suspension points and become frame fields. `__r_a` outlives the second await; `__sub_b` is alive only between S_BEFORE_B and S_AFTER_B.

### Frame cleanup on caught exceptions

When a `try`/`except` inside a coroutine catches an exception that originated from a sub-future poll, the sub-future field must be explicitly reset before continuing -- otherwise the optional remains "engaged" with a logically-consumed sub-future, and a subsequent re-poll would see a stale state.

Codegen handles this in the catch wrapper: every `catch (...)` that wraps an `await` site does `__sub_n.reset()` as the first action before user `except` handlers run. The user code never observes the leftover slot.

### `await` only inside `async def`

Sema rejects `await` outside `async def` with a clear error pointing at `asyncio.run()`.

---

## Cancellation

Exception-based: `task.cancel()` injects `CancelledError` at the next suspension point. Matches CPython.

### Mechanism

- Each frame has a `cancel_pending: bool`.
- `Task.cancel()` calls into the task's `AsyncFrameBase::cancel()`, which sets `cancel_pending = true` on the coroutine frame, then calls the task's waker.
  - If task state is `Done` or `Cancelled`: no-op.
- At every suspension's resume point (inside the wrapping try/finally for that suspension), the case body checks `cancel_pending`. If set, clear and `throw CancelledError()`.
- The throw propagates through wrapping try/except/finally per the source structure. `try`/`finally` runs cleanup; `except CancelledError as e:` catches if the user wants to consume cancellation.
- The throw exits `poll()`; the Task observes the throw and transitions to `Cancelled`.

### Why per-suspension, not pre-switch

Putting the cancel check before `switch (state)` and throwing from there leaves the user's try/except/finally behind in C++ source structure (no try is open at the throw point). The user expects cancellation at an `await` to be caught by the surrounding `try` -- so the check goes inside the case body's try wrappers.

### Sub-future propagation

Cancelling a parent does not directly cancel child coroutines awaited via `await`. The parent throws `CancelledError` *before* polling the child; the child's frame is destroyed in place via `std::optional::reset()` (or via the frame destructor if the throw escapes). The child never gets to run its own `finally` -- it was suspended, never resumed. Matches CPython.

Cancelling sub-tasks created via `create_task` requires explicit `subtask.cancel()`. A future `TaskGroup` primitive (v2+) handles cooperative cancellation.

### Catching `CancelledError`

`CancelledError` is `BaseException`, so `except Exception` doesn't accidentally swallow it. Code that wants cleanup but propagation uses `try`/`finally`. Code that intentionally consumes cancellation catches it explicitly.

A future sema warning could flag "caught `CancelledError` without re-raising" but is open question, not v1.

---

## `Poll[T]` representation

`tpy::Poll<T>` is a tagged value: `Pending` or `Ready[T]`. C++ shape:

```cpp
template <typename T>
class Poll {
    bool ready_;
    // T payload, possibly via union/optional/aligned-storage
public:
    static Poll<T> pending();
    static Poll<T> ready(T&& value);
    bool is_pending() const;
    bool is_ready() const;
    T&& value() &&;       // move out
    const T& value() const&;
};
```

Special cases:

- **`Poll<void>`** -- just `bool ready_`, `value()` returns void. `Ready` constructor takes no arg.
- **Reference T** (`Poll<T&>`) -- payload is `T*` internally; `value()` returns `T&`.
- **Move-only T** -- payload uses `std::optional<T>` or aligned storage; `value() &&` moves out exactly once.
- **Non-default-constructible T** -- payload uses `std::optional` or aligned storage so `Poll<T>::pending()` doesn't require constructing a T.

Implementation is small (~100 LOC of templated runtime) but each case needs to be tested.

---

## Waker model

A `Waker` is a small value type that lets a parked task be re-scheduled.

```cpp
struct Waker {
    Executor* exec;
    uint32_t task_id;
    uint32_t generation;

    void wake() const {
        if (exec->slots[task_id].generation != generation) return;
        exec->mark_runnable(task_id);
    }
};
```

- `task_id` indexes into the executor's task table.
- `generation` increments each time a slot is reused; `wake()` checks generation matches and silently no-ops on mismatch.
- This makes wakers safely no-op-on-completion: late wakes from timers or `Future` waiter lists, after their parked task completed/cancelled, just don't do anything.

Public Waker shape stays minimal so a future multi-threaded executor doesn't change the API.

---

## Context propagation

Single-threaded v1 uses a thread-local "current executor" pointer:

- `asyncio.run(coro)` sets the thread-local on entry and clears on exit.
- `create_task`, `sleep`, `Future.__poll__` (for waker registration) read the thread-local.
- Calling `create_task` outside `asyncio.run()` raises `RuntimeError("no running event loop")`.
- Calling `asyncio.run()` while one is already running (recursive) raises `RuntimeError("asyncio.run() cannot be called from a running event loop")`. Matches CPython.

Multi-threaded extension (v3+) routes the executor reference through the `Waker`'s context; the user-visible API doesn't change.

---

## `Future[T]` (manual completion)

`asyncio.Future[T]` is the building block for `gather`, `wait_for`, and (later) sync primitives. Distinct from `tpy.Awaitable[T]`: `Future[T]` is a concrete TPy class implementing `Awaitable[T]` with an external completion API.

```python
class Future[T]:
    _done: bool
    _result: UninitStorage[T]      # initialized only when _done and no exception
    _exception: BaseException | None
    _waiter: Waker | None          # single-awaiter; see below

    def __init__(self) -> None:
        self._done = False
        self._exception = None
        self._waiter = None

    def poll(self, waker: Waker) -> Poll[T]:
        if self._done:
            if self._exception is not None:
                raise self._exception
            return Poll.ready(self._result.take())   # consumes; see ownership note
        if self._waiter is not None:
            raise RuntimeError("Future already has a waiter (single-awaiter v1)")
        self._waiter = waker
        return Poll.pending()

    def set_result(self, value: T) -> None:
        if self._done:
            raise InvalidStateError("Future already done")
        self._result.set(value)
        self._done = True
        if self._waiter is not None:
            self._waiter.wake()
            self._waiter = None

    def set_exception(self, exc: BaseException) -> None: ...
    def done(self) -> bool: return self._done
```

### Design choices

- **`UninitStorage[T]`** rather than `T | None`: avoids the "T can be None" ambiguity, supports non-default-constructible T.
- **Single-awaiter for v1.** A second waiter is an explicit `RuntimeError`, not silent replacement. CPython allows multiple awaiters; we treat this as a TPy v1 limitation. Multi-awaiter `Future[T]` is a v2 extension if it turns out to matter.
- **Result ownership: `Future.__poll__` consumes.** `_result.take()` moves the stored value out. After it's been polled to Ready once, the Future is empty -- a subsequent poll raises (also matches the single-awaiter model). CPython allows repeated `Future.result()` after done; v1 diverges intentionally to keep ownership simple. Multi-awaiter v2 will need to address this (probably via reference return or by caching the result).

### Cancellation interaction

If a task awaiting a Future is cancelled, the parent's cancel-throw fires before re-polling. The Future's stored Waker becomes stale (generation mismatch); when `set_result` later wakes, it's a no-op. The Future itself doesn't need cancellation awareness.

---

## Sync `with` upgrade (v1.5 prerequisite)

For `async with` to support `__aexit__(exc_type, exc_val, exc_tb)` + suppression, sync `with` must support the same on `__exit__`. Currently sync `with` codegen is no-args/no-suppression. The upgrade lands together with `async with` in v1.5 to keep the language consistent.

### Changes needed

- **Codegen** for every sync `with` site: wrap body in try/catch, pass `(exc_type, exc_val, exc_tb)` to `__exit__` on the exceptional path, check the bool return to decide whether to suppress.
- **Sema**: `__exit__` signature changes from `__exit__(self) -> None` to `__exit__(self, exc_type, exc_val, exc_tb) -> bool`. All existing user/stdlib `__exit__` definitions need updating.
- **Backwards compat**: a stub adapter could let old-shape `__exit__(self)` work for one release; long-term every `__exit__` adopts the new signature.

This is a real chunk of work in its own right -- separate sub-design before v1.5 ships. The benefit is that sync and async `with` semantics match, and language users can write context managers that suppress exceptions either way.

### Open detail: `type(exc)` and traceback

The desugaring uses `type(exc)` to pass the exception type. TPy doesn't have a runtime-introspectable `type()` for exceptions today; for v1.5 we likely use an internal `__exception_type_token__` (a sentinel value derived from the catch site) instead. Traceback is `None` for v1.5; full Python-style traceback is a future extension.

---

## `async with` / `async for` (v1.5)

### `async with x as y:` desugaring

```python
__cm = x
y = await __cm.__aenter__()
__exc = None
try:
    <body>
except BaseException as e:
    __exc = e
if __exc is not None:
    __suppress = await __cm.__aexit__(type(__exc), __exc, None)
    if not __suppress:
        raise __exc
else:
    await __cm.__aexit__(None, None, None)
```

`type(__exc)` resolution: see Sync `with` upgrade above.

### `async for y in it:` desugaring

```python
__aiter = it.__aiter__()
while True:
    try:
        y = await __aiter.__anext__()
    except StopAsyncIteration:
        break
    <body>
```

`__anext__` throws `StopAsyncIteration` at end of iteration. Sync iteration uses `std::expected<T, StopIteration>` via `@error_return`, but `@error_return` async is rejected in v1. So v1.5's `__anext__` lives in the throw tier: each iteration end is one C++ exception throw. Cost is once per iteration end (not per element), which is acceptable.

A future v3+ extension could move `__anext__` to `@error_return` once async + `@error_return` is designed.

---

## v1 backend: the simple loop

The v1 executor drives tasks against a runnable deque + timer min-heap. Public surface: `asyncio.run()`, `asyncio.create_task()`, `asyncio.sleep()`. The `Executor` class is internal.

```
loop:
    while runnable not empty:
        task_id = runnable.pop()
        try { Poll<T> p = task.__poll__(Waker{exec, task_id, gen}); }
        catch (...) {
            tasks[task_id].state = Failed;
            tasks[task_id].exception = std::current_exception();   // exception_ptr
            wake_dependents(task_id);
        }
        # poll returned Ready -> mark Done, wake dependents
        # poll returned Pending -> task parked itself somewhere
    if timers empty: return
    deadline = timers.peek().deadline
    sleep_until(deadline)
    while timers.peek().deadline <= now():
        timer = timers.pop()
        timer.waker.wake()
    continue
```

~150-200 lines of TPy. No public `Reactor`, no fd handling.

Exception storage uses `std::exception_ptr` to preserve the dynamic type after catch. When a downstream `await` re-throws (e.g. `await failed_task` propagates the stored exception), it does so via `std::rethrow_exception`.

---

## CPython compatibility

### What works under both (v1 + v1.5)

- `async def`, `await`, `async with`, `async for`.
- `import asyncio`; `from asyncio import run, sleep, gather, create_task, wait_for, Task, Future, CancelledError, TimeoutError`.
- The methods on `Task[T]` and `Future[T]` that v1/v1.5 ships.

### What does not match (until v2+)

- `Task.add_done_callback`, `Task.get_loop`, `Task.get_name`, `Task.set_name` -- v2.
- `asyncio.Lock`, `Event`, `Queue`, `Semaphore` -- v2.
- Multi-awaiter `Future`: v2.
- Repeated `await future` after done: v2 (with multi-awaiter).
- The `loop = asyncio.get_event_loop()` pattern -- TPy has no public `Executor` API.

### CPython phase

`lib/cpy/asyncio.py` is **not** shipped (a `from asyncio import *` stub would import itself). CPython phase imports CPython's stdlib `asyncio` directly. Tests using v2+ asyncio surface or TPy-specific behavior carry `no_cpython.txt`.

---

## Interaction with existing features

### Exceptions

`raise` in `async def` propagates out of the next `poll()` call (or immediately on first poll). `try`/`except`/`finally` work via per-suspension try wrappers in case bodies. `CancelledError` is `BaseException` so `except Exception` doesn't catch it.

### `@error_return`

Rejected on `async def` in v1 with "not yet supported". Future extension can add `await foo()?` without breaking v1 source.

### Generators

Generators stay on their existing codegen in v1. An `async def` with `yield` is an async generator -- rejected for v1.

### `with` (sync)

Gets a v1.5 upgrade to support `__exit__(exc_type, exc_val, exc_tb)` + suppression, in tandem with `async with` shipping.

### `for` (sync)

Unchanged.

### Decorators that mutate function bodies

Macro-generated wrappers need to consider whether the wrapped function is async. v1 either rejects async-wrapping decorators or requires explicit support. Decisions deferred to the macro-system extension.

### Borrow checking / readonly

Borrow-tracking and `@readonly` apply to async function bodies the same way as sync. Frame fields participate in the same lifetime analysis. The escaping-call rule (above) handles the case where a coroutine outlives its caller.

---

## Open questions

Deliberately unresolved in v1; flagged for future work.

1. **`Waker` by value vs by reference.** Doc assumes by value (small POD). Confirm during runtime implementation.
2. **`CancelledError` re-throw semantics.** Sema warning for "caught without re-raise"? Probably yes; specifics in implementation.
3. **`gather` exception propagation policy.** CPython 3.11+ has subtle semantics around `return_exceptions=False` (default). v1.5 will match exactly.
4. **`gather` heterogeneous result type.** Three options: `tuple[T1, T2, ...]` (CPython-shape, requires variadic generics), `list[Any]` (CPython runtime; loses type info), or homogeneous-only (`gather(*coros: Iterable[Awaitable[T]]) -> list[T]`). Pick during v1.5 design; lean toward homogeneous as a safe v1.5 starting point with variadic-tuple gather as a v2 extension.
5. **Top-level `await` in REPL.** Out of scope for v1; needed eventually for the REPL.
6. **Sub-future field reuse.** Distinct awaits of the same coroutine type at non-overlapping suspension intervals could share storage via `std::variant<Coro1, Coro2, ...>` (one slot, tagged by current suspension state). Optimization, not correctness; planned approach but not v1 critical.
7. **Frame-field destruction order on throws.** C++ unwinds in stack order; verify it matches Python's lexical destruction order, or document the divergence.
8. **Container support for `Coroutine[T]` values.** `list[Coroutine[T]]` is needed for `gather`; sema for "list elements are single-use" needs concrete rules. v1 may restrict to homogeneous collections; broader rules wait for evidence of need.
9. **`type(exc)` for `with`/`async with` exception args.** v1.5 likely uses an internal exception-type token rather than full Python-style `type()`. Concrete token shape is a v1.5 implementation detail.

---

## Implementation order

1. **Runtime types in `tpy`** (`Awaitable[T]`, `Coroutine[T]`, `Poll[T]`, `Waker`, `CancelledError`). Defines the contract; can validate against hand-written conforming types.
2. **Sema for `async def` / `await` / exclusion rules / Coroutine value model.** Uses a placeholder `Task<T>` type to express the "consumed by Task wrap" rule; the real Task lands in step 5.
3. **Resumable-frame abstraction in codegen.** Largest single piece. Build with the future generator migration in mind. Includes per-suspension try/except/finally re-establishment, finally deduplication, frame cleanup on caught exceptions.
4. **`async def` lowering on top of the abstraction.**
5. **`Task[T]` poll-box (C++ runtime) and `asyncio.Future[T]` (TPy stdlib).**
6. **Cancellation** (`cancel_pending`, throw-at-suspension, propagation, `Task.cancel()`).
7. **`asyncio` library v1**: `run`, `create_task`, `sleep`, simple executor, exception_ptr-backed task storage.
8. **v1.5**: sync `with` upgrade for `__exit__` exc args, `async with`, `async for`, `gather`, `wait_for`. Sync `with` upgrade is a sub-design of its own (see section).

Each step is independently testable. The runtime + sema can validate the protocol shape with hand-written conforming types before the codegen abstraction lands.
