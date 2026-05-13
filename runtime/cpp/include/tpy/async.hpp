/**
 * TurboPython Runtime - Async primitives
 *
 * Poll<T>, Waker, CancelledError. The structural Awaitable<T> concept
 * is generated per-call-site by codegen (matching how Iterator<T> is
 * handled), so it is not defined here.
 *
 * The `Awaitable` contract: a type T is awaitable iff it has a method
 *   tpy::Poll<U> __poll__(tpy::Waker w);
 * for some U. `await x` lowers to a sequence of __poll__ calls; each call
 * returns either Poll::pending() (the awaiting frame parks) or
 * Poll::ready(value) (the value is consumed). The dunder name matches
 * how other TPy/typing structural protocols spell their required
 * methods (`__iter__`, `__hash__`, `__lt__`, ...) and signals "runtime
 * protocol method -- prefer `await` / `poll_once` to direct calls".
 *
 * Cancellation flows through the standard C++ exception path:
 * Task::cancel() flags the frame; the next poll throws CancelledError
 * at the resumed-await site.
 */

#pragma once

#include <cstdint>
#include <exception>
#include <memory>
#include <optional>
#include <type_traits>
#include <utility>

#include "core.hpp"

namespace tpy {

// Forward declaration so ExecutorOps's SpawnFn signature can name
// AnyTaskBox before the struct itself is defined further down.
struct AnyTaskBox;

/**
 * CancelledError -- thrown into a coroutine at the resumed-await position
 * when its task is cancelled. Inherits BaseException (not Exception) so
 * `except Exception:` does not silently swallow it.
 */
struct CancelledError : BaseException {
    CancelledError() : BaseException("CancelledError") {}
    using BaseException::BaseException;
};

/**
 * Waker -- a small POD value that lets a parked task be re-scheduled.
 *
 * - exec is an opaque handle to the TPy `Executor` instance the
 *   parked task lives in. Stored as void* because the executor's
 *   concrete C++ type is generated from TPy code (not visible from
 *   async.hpp); dispatch routes through `tpy::executor_ops` set up at
 *   executor construction time.
 * - task_id indexes into the executor's task table.
 * - generation changes when a slot completes or is reused; wake()
 *   checks generation matches before scheduling, so wakes from
 *   completed/cancelled tasks are silent no-ops.
 */
struct Waker {
    void* exec = nullptr;
    // task_id / generation are int32_t so TPy code can pass them
    // directly to `list[T]` indexing (which requires Int32) without a
    // cast. They are conceptually non-negative slot indices; the signed
    // type is a TPy interop concession, not a semantic claim.
    int32_t task_id = 0;
    int32_t generation = 0;

    // Re-schedule the parked task. No-op if the slot's generation has
    // moved on (the parked task already completed or was cancelled).
    void wake() const noexcept;
};

/**
 * ExecutorOps -- dispatch table for executor entry points called from
 * C++ contexts that don't know the concrete executor type. Covers the
 * three cross-language boundaries: `mark_runnable` (Waker::wake),
 * `spawn` (make_user_task), and `register_timer`
 * (executor_register_timer_seconds).
 *
 * Populated by `register_executor_ops_from<ExecT>(exec_ref)` (called
 * from the TPy `Executor.__init__`). The function pointers capture
 * `ExecT` via templated thunks so the table is rebound per concrete
 * executor type. When no executor is running the table stays unset and
 * the C++ entry points silently no-op (or panic in the spawn case --
 * `asyncio.create_task` outside `asyncio.run` should panic).
 */
// Not noexcept: the underlying TPy `Executor.mark_runnable` does a
// `list.append(slot_id)` which can throw `std::bad_alloc`. A noexcept
// thunk that called it would call std::terminate on OOM. The outer
// `Waker::wake` keeps its noexcept guarantee by absorbing any throw
// into a silent drop (see Waker::wake's body).
using MarkRunnableFn = void (*)(void* exec, int32_t task_id,
                                int32_t generation);
// AnyTaskBox is forward-declared above; passing by rvalue reference
// keeps the function-pointer type usable with the forward declaration
// (the move into the thunk body requires the full type at thunk
// instantiation, which happens once AnyTaskBox is defined).
using SpawnFn = int32_t (*)(void* exec, AnyTaskBox&& box);
using RegisterTimerFn = void (*)(void* exec, double deadline_seconds,
                                 Waker waker);

struct ExecutorOps {
    MarkRunnableFn mark_runnable = nullptr;
    SpawnFn spawn = nullptr;
    RegisterTimerFn register_timer = nullptr;
};

// thread_local to match `current_executor` below: a Waker stamped on
// thread A must not dispatch through thread B's ops table. v1 asyncio
// is single-threaded per run, but two concurrent `asyncio.run` calls
// on different threads must not race here.
inline thread_local ExecutorOps executor_ops{};

// Thread-local pointer to the running executor. Stored as `void*`
// because the concrete type is the TPy `Executor` class (compiled
// elsewhere); the ops table thunks know how to cast it back. Declared
// here (rather than near ExecutorHandle below) so the nested-run guard
// in `register_executor_ops_from` can reference it.
inline thread_local void* current_executor = nullptr;

template <typename ExecT>
inline void mark_runnable_thunk(void* exec, int32_t task_id,
                                int32_t generation) {
    static_cast<ExecT*>(exec)->mark_runnable(task_id, generation);
}

template <typename ExecT>
inline int32_t spawn_thunk(void* exec, AnyTaskBox&& box) {
    return static_cast<ExecT*>(exec)->spawn(std::move(box));
}

template <typename ExecT>
inline void register_timer_thunk(void* exec, double deadline_seconds,
                                 Waker waker) {
    static_cast<ExecT*>(exec)->register_timer(deadline_seconds, waker);
}

/// Register thunks that dispatch into the concrete `ExecT`'s methods.
/// Called from the TPy Executor's __init__ on each construction; the
/// thread-local ops table is overwritten with the same values each
/// time, so repeated calls are idempotent for a fixed `ExecT`. Panics
/// if a running executor is already installed on this thread (nested
/// `asyncio.run` or a leaked `_ExecutorScope`); the nested-run path in
/// `async_run` rejects this earlier with a better message, this is the
/// backstop. The check is keyed on `current_executor` rather than the
/// ops table itself so that raw `Executor()` construction in unit tests
/// (without a `_ExecutorScope`) is unaffected -- those tests never set
/// `current_executor`.
template <typename ExecT>
inline void register_executor_ops_from(ExecT&) noexcept {
    if (current_executor != nullptr) {
        tpy_panic("register_executor_ops_from: another executor is "
                  "already running on this thread "
                  "(nested asyncio.run or leaked _ExecutorScope)");
    }
    executor_ops.mark_runnable = &mark_runnable_thunk<ExecT>;
    executor_ops.spawn = &spawn_thunk<ExecT>;
    executor_ops.register_timer = &register_timer_thunk<ExecT>;
}

/**
 * Poll<T> -- the result of polling an Awaitable.
 *
 * Pending: the awaitable has not yet produced a value; the caller
 * should park (the awaitable has saved the Waker and will fire it
 * when ready).
 *
 * Ready(v): the value v has been produced. After consumption with
 * value() &&, the Poll is empty; polling again is the awaitable's
 * job to handle (most awaitables panic on re-poll-after-Ready).
 */
template <typename T>
class Poll {
    static_assert(!std::is_reference_v<T>,
                  "Poll<T&> uses the reference specialization");

    std::optional<T> value_;

public:
    Poll() = default;

    static Poll<T> pending() noexcept { return Poll<T>{}; }

    static Poll<T> ready(T value) {
        Poll<T> p;
        p.value_.emplace(std::move(value));
        return p;
    }

    template <typename... Args>
    static Poll<T> ready_emplace(Args&&... args) {
        Poll<T> p;
        p.value_.emplace(std::forward<Args>(args)...);
        return p;
    }

    bool is_pending() const noexcept { return !value_.has_value(); }
    bool is_ready() const noexcept { return value_.has_value(); }

    // Move out the contained value. Caller must check is_ready() first.
    T value() && {
        if (!value_.has_value()) tpy_panic("Poll::value() called on Pending");
        T v(std::move(*value_));
        value_.reset();
        return v;
    }

    const T& value() const& {
        if (!value_.has_value()) tpy_panic("Poll::value() called on Pending");
        return *value_;
    }
};

// Void specialization: no payload.
template <>
class Poll<void> {
    bool ready_ = false;

public:
    Poll() = default;

    static Poll<void> pending() noexcept { return Poll<void>{}; }
    static Poll<void> ready() noexcept {
        Poll<void> p;
        p.ready_ = true;
        return p;
    }

    bool is_pending() const noexcept { return !ready_; }
    bool is_ready() const noexcept { return ready_; }
    void value() const {
        if (!ready_) tpy_panic("Poll::value() called on Pending");
    }
};

// Reference specialization: payload is a pointer internally.
template <typename T>
class Poll<T&> {
    T* ptr_ = nullptr;

public:
    Poll() = default;

    static Poll<T&> pending() noexcept { return Poll<T&>{}; }
    static Poll<T&> ready(T& value) noexcept {
        Poll<T&> p;
        p.ptr_ = &value;
        return p;
    }

    bool is_pending() const noexcept { return ptr_ == nullptr; }
    bool is_ready() const noexcept { return ptr_ != nullptr; }
    T& value() const {
        if (!ptr_) tpy_panic("Poll::value() called on Pending");
        return *ptr_;
    }
};

// Stream insertion for the user-facing async types -- enables
// print(x) / REPL auto-echo. Without these, any expression-statement
// in the REPL whose value is a Poll / Waker / Task fails C++ build at
// the implicit `std::cout << result` site.

// Poll: render as `Poll.ready(<value>)` / `Poll.pending()` /
// `Poll.ready()` (void) to match the `poll_ready` / `poll_pending`
// constructor names users see.
template <typename T>
std::ostream& operator<<(std::ostream& os, const Poll<T>& p) {
    if (p.is_pending()) return os << "Poll.pending()";
    if constexpr (requires(std::ostream& s, const T& v) { s << v; }) {
        return os << "Poll.ready(" << p.value() << ")";
    } else {
        return os << "Poll.ready(...)";
    }
}

inline std::ostream& operator<<(std::ostream& os, const Poll<void>& p) {
    return os << (p.is_pending() ? "Poll.pending()" : "Poll.ready()");
}

// Waker: POD with no user-meaningful state; a bare `Waker()` repr is
// enough for print/REPL.
inline std::ostream& operator<<(std::ostream& os, const Waker&) {
    return os << "Waker()";
}

namespace detail {

// Empty placeholder for TaskState<T>::result_ when T is void; selected
// via std::conditional_t below so void TaskStates don't carry an unused
// std::optional.
struct EmptyResult {};

}  // namespace detail

/**
 * AnyTask -- type-erased task interface for the executor's runnable list.
 *
 * The executor stores spawned tasks as `shared_ptr<AnyTask>` and only
 * needs poll-and-cancel semantics; the concrete value type T lives one
 * level down (TaskState<T> inherits AnyTask).
 */
struct AnyTask {
    virtual bool poll_any(Waker w) = 0;  // returns true iff Done (Ready or threw)
    virtual void cancel_any() = 0;
    virtual ~AnyTask() = default;
};

/**
 * TaskState<T> -- shared backing for a `Task<T>`. Also acts as the
 * executor's `AnyTask` so a spawned task is one heap allocation total.
 *
 * Owns the coroutine frame and caches the result/exception once the
 * frame completes, so the executor's runnable list and the user's
 * `await task` observe the same completion state.
 *
 * The executor drives the frame via `poll_any()`, which caches the
 * value (storing it for a later awaiter). The user's `await` path
 * drives via `__poll__()`, which either drives once and returns the
 * fresh value, or returns the cached value if already completed by
 * the executor.
 *
 * Single-awaiter for v1: a second `__poll__()` after `Ready` panics.
 *
 * Type-erased over the concrete coroutine struct via virtual frame
 * methods; the concrete CoroT lives inline in TaskStateImpl<T, CoroT>
 * below, so the make_shared block holds both the state and the
 * coroutine frame.
 */
template <typename T>
struct TaskState : AnyTask {
    [[no_unique_address]] std::conditional_t<
        std::is_void_v<T>, detail::EmptyResult, std::optional<T>> result_;
    std::exception_ptr exc;
    Waker awaiter;
    bool done = false;
    bool has_result = false;
    bool has_exc = false;
    bool executor_owned = false;

    TaskState() = default;
    TaskState(const TaskState&) = delete;
    TaskState& operator=(const TaskState&) = delete;

    // Subclass hooks. The concrete TaskStateImpl<T, CoroT> forwards
    // these to its inline `coro` field; once the frame is released
    // (after Ready or an exception) frame_alive() returns false.
    virtual Poll<T> poll_frame(Waker w) = 0;
    virtual void cancel_frame() = 0;
    virtual bool frame_alive() const = 0;
    virtual void release_frame() = 0;

    // User-facing poll: drives the frame if not yet done, returning the
    // freshly-produced value. If the frame already completed via the
    // executor, returns the cached value (or rethrows the cached exc).
    Poll<T> __poll__(Waker w) {
        if (done) {
            if (has_exc) {
                std::rethrow_exception(exc);
            }
            if (!has_result) {
                tpy_panic("Task::__poll__ after Ready was already consumed");
            }
            has_result = false;
            if constexpr (std::is_void_v<T>) {
                return Poll<void>::ready();
            } else {
                T v(std::move(*result_));
                result_.reset();
                return Poll<T>::ready(std::move(v));
            }
        }
        if (executor_owned) {
            awaiter = w;
            return Poll<T>::pending();
        }
        try {
            auto p = poll_frame(w);
            if (p.is_ready()) {
                done = true;
                release_frame();
                if constexpr (std::is_void_v<T>) {
                    return Poll<void>::ready();
                } else {
                    return Poll<T>::ready(std::move(p).value());
                }
            }
            return Poll<T>::pending();
        } catch (...) {
            done = true;
            has_exc = true;
            exc = std::current_exception();
            release_frame();
            throw;
        }
    }

    // AnyTask interface. Drives the frame and caches the result/exc.
    // Returns true iff completed (so the executor can drop the slot).
    bool poll_any(Waker w) override {
        if (done) return true;
        try {
            auto p = poll_frame(w);
            if (p.is_ready()) {
                done = true;
                if constexpr (!std::is_void_v<T>) {
                    result_.emplace(std::move(p).value());
                }
                has_result = true;
                release_frame();
                awaiter.wake();
                return true;
            }
            return false;
        } catch (...) {
            done = true;
            has_exc = true;
            exc = std::current_exception();
            release_frame();
            awaiter.wake();
            return true;
        }
    }

    void cancel_any() override {
        if (frame_alive()) cancel_frame();
    }
};

/**
 * TaskStateImpl<T, CoroT> -- concrete TaskState carrying the coroutine
 * frame inline. `CoroT` is the generated coroutine struct
 * (`__coro_<funcname>`) for an `async def` returning T; it must conform
 * to Awaitable<T> (a `__poll__(Waker) -> Poll<T>` member and a
 * `__cancel_pending` field). std::optional gives us early frame
 * release after Ready/throw without a separate heap allocation.
 */
template <typename T, typename CoroT>
struct TaskStateImpl : TaskState<T> {
    std::optional<CoroT> coro;

    explicit TaskStateImpl(CoroT&& c) : coro(std::move(c)) {}

    Poll<T> poll_frame(Waker w) override { return coro->__poll__(w); }
    void cancel_frame() override { coro->__cancel_pending = true; }
    bool frame_alive() const override { return coro.has_value(); }
    void release_frame() override { coro.reset(); }
};

/**
 * Task<T> -- type-erased poll-box for an Awaitable<T>.
 *
 * Holds a `shared_ptr<TaskState<T>>`; one virtual call per poll()
 * (same indirection any type-erased poll-box pays). Static await of a
 * known coroutine still goes through the structural Awaitable<T> shape
 * directly, so this only kicks in when the awaitable type is erased
 * (Awaitable[T] protocol param, Task[T] re-await, unions of coros).
 *
 * `Task` is move-only externally (TPy semantics: a Task value is
 * consumed by `await`). Internally the state is a shared_ptr because
 * spawned tasks are also referenced by the executor's runnable list:
 * both the user's handle and the executor wrapper share the same
 * TaskState so completion is visible to both.
 */
template <typename T>
class Task {
    std::shared_ptr<TaskState<T>> state_;

public:
    Task() = default;

    template <typename CoroT>
    static Task<T> from_coro(CoroT&& c) {
        Task<T> t;
        t.state_ = std::make_shared<TaskStateImpl<T, CoroT>>(std::forward<CoroT>(c));
        return t;
    }

    // Construction from existing shared state (used by create_task to
    // hand the same state to both the user's Task handle and the
    // executor's spawned-task wrapper).
    static Task<T> from_state(std::shared_ptr<TaskState<T>> s) {
        Task<T> t;
        t.state_ = std::move(s);
        return t;
    }

    Task(Task&&) noexcept = default;
    Task& operator=(Task&&) noexcept = default;
    Task(const Task&) = delete;
    Task& operator=(const Task&) = delete;

    // print(task) / REPL auto-echo. Renders task status; we deliberately
    // don't expose result value here -- consuming the result must go
    // through `await`/poll, not a print side-effect.
    friend std::ostream& operator<<(std::ostream& os, const Task<T>& t) {
        if (!t.state_) return os << "Task(empty)";
        if (!t.state_->done) return os << "Task.pending()";
        if (t.state_->has_exc) return os << "Task.failed()";
        return os << "Task.done()";
    }

    Poll<T> __poll__(Waker w) {
        if (!state_) tpy_panic("Task::__poll__ on empty Task");
        return state_->__poll__(w);
    }

    void cancel() noexcept {
        if (state_) state_->cancel_any();
    }

    bool empty() const noexcept { return state_ == nullptr; }

    const std::shared_ptr<TaskState<T>>& state() const { return state_; }
};

/**
 * AnyTaskBox -- type-erased owning slot entry for the executor's task
 * table. Wraps a shared_ptr<AnyTask> so the user-facing Task<T> and the
 * executor's slot table can share the same heap state: dropping the
 * user handle leaves the spawned task running, and a completed task
 * drops out of the slot table while the user can still observe the
 * cached result via their Task<T> handle.
 *
 * TODO(async-v1.x): remove this @native wrapper once TPy gains a
 * shared-ownership smart pointer (Rc/Arc/SharedBox); at that point
 * TaskState<T> can move to TPy and the slot table can hold a
 * @dynamic-protocol adapter directly. Tracked under "Blocked" in
 * docs/ASYNC_PROGRESS.md's v1.x section.
 */
struct AnyTaskBox {
    std::shared_ptr<AnyTask> task;

    AnyTaskBox() = default;
    explicit AnyTaskBox(std::shared_ptr<AnyTask> t) noexcept
        : task(std::move(t)) {}

    // Move-only TPy semantics (matches Task<T>). The wrapped shared_ptr
    // is copyable internally, and clones happen deliberately at C++
    // construction sites that bridge two views of the same TaskState
    // (e.g. `task_to_any_box(task)` mirrors a `Task<T>`'s state into a
    // separate AnyTaskBox so executor + user-handle both observe
    // completion). What's forbidden is *implicit* copy of an
    // AnyTaskBox value through the TPy API surface -- each spawn site
    // hands ownership to a single slot.
    AnyTaskBox(AnyTaskBox&&) noexcept = default;
    AnyTaskBox& operator=(AnyTaskBox&&) noexcept = default;
    AnyTaskBox(const AnyTaskBox&) = delete;
    AnyTaskBox& operator=(const AnyTaskBox&) = delete;

    bool empty() const noexcept { return task == nullptr; }
    void reset() noexcept { task.reset(); }

    // Drive the held task once. Returns true iff it completed (Ready
    // or threw). Panics on a null box.
    bool poll_any(Waker w) {
        if (!task) tpy_panic("AnyTaskBox::poll_any on empty box");
        return task->poll_any(w);
    }

    // Flag cancellation on the held task. No-op on a null box.
    void cancel_any() noexcept {
        if (task) task->cancel_any();
    }
};


/// Waker::wake dispatches into the running executor via the global
/// ops table. Three early-out paths:
///   * `exec == nullptr`: default-constructed Waker, no target.
///   * `executor_ops.mark_runnable == nullptr`: the executor that
///     stamped this Waker has been torn down (`_ExecutorScope.__del__`
///     clears the ops table on exit). Silent no-op rather than UB on
///     a stale pointer.
///   * any exception thrown by the underlying `mark_runnable` (e.g.
///     `std::bad_alloc` from the runnable-queue push): swallowed.
///     wake() preserves a `noexcept` contract; the most we'd do on
///     OOM is drop the wake, and the caller can't usefully react to
///     it anyway.
inline void Waker::wake() const noexcept {
    if (exec == nullptr) return;
    if (executor_ops.mark_runnable == nullptr) return;
    try {
        executor_ops.mark_runnable(exec, task_id, generation);
    } catch (...) {
        // Drop the wake; cannot propagate from a noexcept context.
    }
}

/**
 * ExecutorHandle -- opaque value-type handle to the currently-running
 * TPy `Executor` instance, mirroring Waker's POD shape. Stored as
 * `void*` because the concrete type is generated from TPy code and
 * its C++ class name isn't visible from async.hpp; the ops thunks
 * registered by `register_executor_ops_from<ExecT>(self)` cast it
 * back at the call site.
 */
struct ExecutorHandle {
    void* ptr = nullptr;
    bool is_null() const noexcept { return ptr == nullptr; }
};

/// Read the current-executor thread-local as an opaque handle. Returns
/// a null handle if no executor is running.
inline ExecutorHandle current_executor_get() noexcept {
    return ExecutorHandle{current_executor};
}

/// Write the current-executor thread-local. Used by the TPy
/// `_ExecutorScope` RAII guard around `asyncio.run`.
inline void current_executor_set(ExecutorHandle h) noexcept {
    current_executor = h.ptr;
}

/// Clear the current-executor thread-local. Used by the smoke test's
/// manual save/restore path; production teardown goes through
/// `executor_scope_teardown` below.
inline void current_executor_clear() noexcept {
    current_executor = nullptr;
}

/// Tear down everything an `_ExecutorScope` set up: zero the
/// ExecutorOps dispatch table and clear the current-executor
/// thread-local. Any Waker stamped against the now-destroyed executor
/// that fires later (e.g. a held-Future's saved `_waiter`) becomes a
/// silent no-op in `Waker::wake` because both `executor_ops.mark_runnable`
/// and `exec` are observed as null.
inline void executor_scope_teardown() noexcept {
    executor_ops = ExecutorOps{};
    current_executor = nullptr;
}

/// Wrap a reference to any object as an opaque ExecutorHandle. The TPy
/// `Executor.poll_slot` uses this to stamp Wakers with a back-pointer
/// to itself (`&self` is the runtime address; void* erasure lets the
/// handle outlive direct C++ knowledge of the generated TPy class).
/// Strips const because the void* storage is type-erased -- the
/// thunks that cast back know the underlying mutability requirements
/// of each call site.
template <typename T>
inline ExecutorHandle make_executor_handle(T& obj) noexcept {
    return ExecutorHandle{const_cast<void*>(
        static_cast<const void*>(&obj))};
}

/// Construct a Waker from an executor handle + task id + generation.
/// TPy-side bridge used by the TPy `Executor.poll_slot` to stamp
/// Wakers with a back-pointer to itself without exposing Waker's
/// individual fields as TPy-mutable. The `exec` pointer is opaque;
/// `Waker::wake` dispatches via the ops table set up at executor
/// construction.
inline Waker make_waker(ExecutorHandle h, int32_t task_id,
                        int32_t generation) noexcept {
    Waker w;
    w.exec = h.ptr;
    w.task_id = task_id;
    w.generation = generation;
    return w;
}

/// Bridge for TPy-side awaitables: register a timer with the current
/// executor at a deadline expressed in steady_clock seconds (matching
/// `time.monotonic()`'s domain). No-op if no executor is running, so
/// hand-rolled awaitables polled from a test harness without
/// `asyncio.run` don't crash. Dispatches via the ops table.
inline void executor_register_timer_seconds(double deadline_seconds,
                                            Waker waker) {
    if (current_executor == nullptr) return;
    if (executor_ops.register_timer == nullptr) return;
    executor_ops.register_timer(current_executor, deadline_seconds, waker);
}

/// asyncio.create_task lowers to this: build a Task<T> from the
/// awaited coro, register it with the running executor for concurrent
/// scheduling, and return a handle that shares the same TaskState.
/// Per docs/ASYNC_DESIGN.md ("Context propagation"): "Calling
/// `create_task` outside `asyncio.run()` raises `RuntimeError('no
/// running event loop')`." Mirrors CPython's behavior.
template <typename T, typename CoroT>
inline Task<T> make_user_task(CoroT&& coro) {
    if (current_executor == nullptr) {
        tpy_panic("asyncio.create_task: no running event loop "
                  "(call asyncio.run(coro) to drive it)");
    }
    if (executor_ops.spawn == nullptr) {
        // Inconsistent state: thread-local executor is set but the
        // spawn op wasn't registered. Should never happen -- the TPy
        // `Executor.__init__` always populates the ops table. If
        // reached, the runtime is in a corrupted state (e.g. ops
        // cleared mid-loop, or a third-party `_set_current_executor`
        // call bypassed Executor construction).
        tpy_panic("asyncio.create_task: executor ops not registered "
                  "(internal invariant violated)");
    }
    auto state = std::make_shared<TaskStateImpl<T, CoroT>>(
        std::forward<CoroT>(coro));
    state->executor_owned = true;
    executor_ops.spawn(current_executor, AnyTaskBox(state));
    return Task<T>::from_state(std::move(state));
}


/// Build an AnyTaskBox from a coroutine without registering with any
/// executor. Test-only path: lets a test drive a TPy `Executor`
/// directly without going through `make_user_task` (which requires a
/// running event loop). Production spawn flows through `make_user_task`
/// and the ExecutorOps `spawn` thunk; this factory is intentionally
/// distinct so test scaffolding doesn't depend on the executor being
/// the current one. Does NOT set `executor_owned = true` on the
/// underlying state -- callers that drive the task through both an
/// executor and a `Task<T>` handle should use `make_executor_owned_task`
/// + `task_to_any_box` instead.
template <typename T, typename CoroT>
inline AnyTaskBox make_any_task_for_test(CoroT&& coro) {
    auto state = std::make_shared<TaskStateImpl<T, CoroT>>(
        std::forward<CoroT>(coro));
    return AnyTaskBox(state);
}

/// Build a `Task<T>` whose state is marked `executor_owned`, ready to
/// be spawned on an executor. Returns the user-facing handle; pair
/// with `task_to_any_box(task)` to get the matching slot-table entry.
/// Used by TPy `asyncio.run` to construct the main task without
/// going through `make_user_task` (which spawns eagerly via the
/// thread-local executor and doesn't return the slot id).
template <typename T, typename CoroT>
inline Task<T> make_executor_owned_task(CoroT&& coro) {
    auto state = std::make_shared<TaskStateImpl<T, CoroT>>(
        std::forward<CoroT>(coro));
    state->executor_owned = true;
    return Task<T>::from_state(std::move(state));
}

/// Clone a `Task<T>`'s underlying TaskState into an AnyTaskBox so the
/// executor's slot table can drive it while the user holds onto the
/// `Task<T>` handle. shared_ptr ref-count goes up by one; both views
/// observe the same completion state.
template <typename T>
inline AnyTaskBox task_to_any_box(const Task<T>& t) {
    return AnyTaskBox(t.state());
}


/// Convenience: poll a Task and return true iff a CancelledError was
/// thrown out of the coroutine. Used by tests; user code should catch
/// CancelledError directly. Discards the value if Ready (returns false).
template <typename T>
inline bool task_poll_cancelled(Task<T>& t) {
    try {
        (void)t.__poll__(Waker{});
        return false;
    } catch (const CancelledError&) {
        return true;
    }
}


// Task<void> uses the primary template; Poll<void>'s `pending()` /
// `ready()` shape works through the same code path because all the
// generic accessors are conditional on the value type.

}  // namespace tpy

// Forward declaration of the TPy-side run-loop helper. Defined in the
// generated `tpystd/asyncio.hpp` from `lib/tpy/asyncio/__init__.py`.
// Lives outside namespace tpy so the namespace lines up with TPy's
// codegen choice. Non-generic so the signature is fully concrete --
// async_run hands off the slot-table box and keeps its half of the
// shared TaskState in the Task<T> local for result extraction.
namespace tpystd::asyncio {
void _run_drain_main_task(::tpy::AnyTaskBox&& box);
}  // namespace tpystd::asyncio

namespace tpy {

/**
 * async_run -- `asyncio.run` driver.
 *
 * Thin C++ shell that handles result-type-dependent setup (Task<T>
 * construction with the right ResultT) and result extraction (which
 * needs `if constexpr (is_void_v<T>)` -- a thing TPy can't currently
 * express; tracked as a v1.2 compiler item in `BUGS.md`). The run
 * loop itself (executor construction, thread-local scope, spawn,
 * run_until, drain) lives in the TPy `_run_drain_main_task` helper
 * that this shell calls into.
 *
 * v1 has no I/O reactor -- only timer-driven sleep. A coro that returns
 * Pending with no pending timers panics ("no progress possible") inside
 * `_run_drain_main_task`; that's the v1 equivalent of asyncio's "no
 * current event loop" error.
 */
template <typename CoroT>
inline auto async_run(CoroT&& coro)
    -> decltype(std::declval<CoroT&>().__poll__(std::declval<Waker>()).value()) {
    using ResultT = decltype(std::declval<CoroT&>().__poll__(std::declval<Waker>()).value());
    // Per docs/ASYNC_DESIGN.md ("Context propagation"): asyncio.run
    // cannot be re-entered. Matches CPython's "asyncio.run() cannot be
    // called from a running event loop" RuntimeError.
    if (current_executor != nullptr) {
        tpy_panic("asyncio.run() cannot be called from a running event loop");
    }
    auto task = make_executor_owned_task<ResultT>(std::forward<CoroT>(coro));
    // Delegate the run loop to TPy. This call sets up a TPy Executor
    // internally, drives the task to completion via the slot-table
    // box (which shares the underlying TaskState with our `task`
    // handle), drains spawned tasks with cancellation, and clears
    // the thread-local before returning.
    {
        AnyTaskBox box = task_to_any_box(task);
        ::tpystd::asyncio::_run_drain_main_task(std::move(box));
    }
    // Read the cached result (or rethrow the cached exception). Uses
    // `if constexpr` to handle void uniformly with non-void.
    if constexpr (std::is_void_v<ResultT>) {
        task.__poll__(Waker{}).value();
        return;
    } else {
        return task.__poll__(Waker{}).value();
    }
}

}  // namespace tpy
