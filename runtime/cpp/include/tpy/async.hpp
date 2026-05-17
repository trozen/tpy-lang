/**
 * TurboPython Runtime - Async primitives
 *
 * Waker, CancelledError. `Poll<T>` is a pure-TPy class today (lives in
 * `lib/tpy/tpy/_core/_types.py`); the structural Awaitable<T> concept
 * is generated per-call-site by codegen (matching how Iterator<T> is
 * handled), so neither is defined here.
 *
 * The `Awaitable` contract: a type T is awaitable iff it has a method
 *   def __poll__(self, w: Waker) -> Own[Poll[U]]
 * for some U (lowering to a `::tpystd::tpy::Poll<U>` value return at the
 * C++ ABI level). `await x` lowers to a sequence of __poll__ calls; each call
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
 * ExecutorHandle -- opaque value-type handle to the running TPy
 * `Executor` instance. Stored as `void*` because the concrete type is
 * generated from TPy code and its C++ class name isn't visible from
 * async.hpp; the ops thunks registered by
 * `register_executor_ops_from<ExecT>(self)` cast it back at the call
 * site. Declared above Waker so Waker can hold one by value.
 */
struct ExecutorHandle {
    void* ptr = nullptr;
    bool is_null() const noexcept { return ptr == nullptr; }
};

/**
 * Waker -- a small POD value that lets a parked task be re-scheduled.
 *
 * - exec is an opaque handle to the TPy `Executor` instance the parked
 *   task lives in. ExecutorHandle wraps a `void*` because the executor's
 *   concrete C++ type is generated from TPy code (not visible from
 *   async.hpp); dispatch routes through `tpy::executor_ops` set up at
 *   executor construction time.
 * - task_id indexes into the executor's task table.
 * - generation changes when a slot completes or is reused; wake()
 *   checks generation matches before scheduling, so wakes from
 *   completed/cancelled tasks are silent no-ops.
 */
struct Waker {
    ExecutorHandle exec{};
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

// Plain (single) global; v1 asyncio is single-executor per process.
// Multi-threaded async (v3+) needs the TLS revert -- mirrors the
// `_current_executor` TPy global in `lib/tpy/asyncio/_executor.py`.
// See TODO.md for the cross-system note.
inline ExecutorOps executor_ops{};

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
/// ops table is overwritten with the same values each time, so repeated
/// calls are idempotent for a fixed `ExecT`. The nested-executor
/// backstop lives in TPy (`Executor.__init__` checks the
/// `_current_executor` module global) rather than here, so this is a
/// pure write.
template <typename ExecT>
inline void register_executor_ops_from(ExecT&) noexcept {
    executor_ops.mark_runnable = &mark_runnable_thunk<ExecT>;
    executor_ops.register_timer = &register_timer_thunk<ExecT>;
}

// Waker: POD with no user-meaningful state; a bare `Waker()` repr is
// enough for print/REPL.
inline std::ostream& operator<<(std::ostream& os, const Waker&) {
    return os << "Waker()";
}

/// Waker::wake dispatches into the running executor via the global ops
/// table. Three early-out paths:
///   * `exec.is_null()`: default-constructed Waker, no target.
///   * `executor_ops.mark_runnable == nullptr`: the executor that
///     stamped this Waker has been torn down (`_ExecutorScope.__del__`
///     clears the ops table on exit). Silent no-op rather than UB on a
///     stale pointer.
///   * any exception thrown by the underlying `mark_runnable` (e.g.
///     `std::bad_alloc` from the runnable-queue push): swallowed. wake()
///     preserves a `noexcept` contract; the most we'd do on OOM is drop
///     the wake, and the caller can't usefully react to it anyway.
inline void Waker::wake() const noexcept {
    if (exec.is_null()) return;
    if (executor_ops.mark_runnable == nullptr) return;
    try {
        executor_ops.mark_runnable(exec.ptr, task_id, generation);
    } catch (...) {
        // Drop the wake; cannot propagate from a noexcept context.
    }
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

/// Clear the ExecutorOps dispatch table. Called from the TPy
/// `_ExecutorScope.__del__` so any Waker stamped against the now-destroyed
/// executor that fires later (e.g. a held-Future's saved `_waiter`) becomes
/// a silent no-op in `Waker::wake` (its `mark_runnable == nullptr` check
/// catches the empty table).
inline void clear_executor_ops() noexcept {
    executor_ops = ExecutorOps{};
}

/// Bridge for TPy-side awaitables: register a timer with the running
/// executor at a deadline expressed in steady_clock seconds (matching
/// `time.monotonic()`'s domain). The TPy wrapper `_register_timer_at`
/// pre-checks `is_null()`, but this shim re-checks defensively so direct
/// callers from C++ contexts (test harnesses, future native bindings)
/// can't accidentally dereference a null handle through the function
/// pointer.
inline void executor_register_timer_seconds(ExecutorHandle exec_handle,
                                            double deadline_seconds,
                                            Waker waker) {
    if (exec_handle.is_null()) return;
    if (executor_ops.register_timer == nullptr) return;
    executor_ops.register_timer(exec_handle.ptr, deadline_seconds, waker);
}

}  // namespace tpy
