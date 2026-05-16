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
 * two cross-language boundaries: `mark_runnable` (Waker::wake) and
 * `register_timer` (executor_register_timer_seconds).
 *
 * Populated by `register_executor_ops_from<ExecT>(exec_ref)` (called
 * from the TPy `Executor.__init__`). The function pointers capture
 * `ExecT` via templated thunks so the table is rebound per concrete
 * executor type. When no executor is running the table stays unset and
 * the C++ entry points silently no-op.
 *
 * `spawn` is NOT in this table: `asyncio.create_task` is pure TPy
 * (`lib/tpy/asyncio/__init__.py`) and dispatches into `Executor.spawn`
 * directly via the `_executor_spawn_via_handle` cpp_template in
 * `_executor.py`. The slot-table payload `Box[AnyTask]` is a TPy type
 * that can't be referenced from this foundational header without an
 * include cycle.
 */
// Not noexcept: the underlying TPy `Executor.mark_runnable` does a
// `list.append(slot_id)` which can throw `std::bad_alloc`. A noexcept
// thunk that called it would call std::terminate on OOM. The outer
// `Waker::wake` keeps its noexcept guarantee by absorbing any throw
// into a silent drop (see Waker::wake's body).
using MarkRunnableFn = void (*)(void* exec, int32_t task_id,
                                int32_t generation);
using RegisterTimerFn = void (*)(void* exec, double deadline_seconds,
                                 Waker waker);

struct ExecutorOps {
    MarkRunnableFn mark_runnable = nullptr;
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
inline void register_timer_thunk(void* exec, double deadline_seconds,
                                 Waker waker) {
    static_cast<ExecT*>(exec)->register_timer(deadline_seconds, waker);
}

/// Register thunks that dispatch into the concrete `ExecT`'s methods.
/// Called from the TPy Executor's __init__ on each construction; the
/// thread-local ops table is overwritten with the same values each
/// time, so repeated calls are idempotent for a fixed `ExecT`. Panics
/// if a running executor is already installed on this thread (nested
/// `asyncio.run` or a leaked `_ExecutorScope`); the TPy `asyncio.run`
/// body rejects this earlier with a `RuntimeError` (matching CPython),
/// this is the backstop. The check is keyed on `current_executor`
/// rather than the ops table itself so that raw `Executor()`
/// construction in unit tests (without a `_ExecutorScope`) is
/// unaffected -- those tests never set `current_executor`.
template <typename ExecT>
inline void register_executor_ops_from(ExecT&) noexcept {
    if (current_executor != nullptr) {
        tpy_panic("register_executor_ops_from: another executor is "
                  "already running on this thread "
                  "(nested asyncio.run or leaked _ExecutorScope)");
    }
    executor_ops.mark_runnable = &mark_runnable_thunk<ExecT>;
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

// The type-erasure stack (Task[T], TaskState[T], AnyTask, etc.) lives
// in `lib/tpy/asyncio/_executor.py` after the v1.2 step 4 cutover; see
// `docs/ASYNC_PROGRESS.md` for the move history.



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



}  // namespace tpy
