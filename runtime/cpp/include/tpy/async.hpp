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

#include <chrono>
#include <cstdint>
#include <deque>
#include <exception>
#include <memory>
#include <optional>
#include <queue>
#include <thread>
#include <type_traits>
#include <utility>
#include <vector>

#include "core.hpp"

namespace tpy {

// Forward declaration: Waker only references the executor by pointer.
// The concrete v1 executor is defined below.
struct Executor;

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
 * - exec is the executor the parked task lives in.
 * - task_id indexes into the executor's task table.
 * - generation changes when a slot completes or is reused; wake()
 *   checks generation matches before scheduling, so wakes from
 *   completed/cancelled tasks are silent no-ops.
 */
struct Waker {
    Executor* exec = nullptr;
    uint32_t task_id = 0;
    uint32_t generation = 0;

    // Re-schedule the parked task. No-op if the slot's generation has
    // moved on (the parked task already completed or was cancelled).
    void wake() const noexcept;
};

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
 * Executor -- v1 driver: runnable queue + timer heap.
 *
 * Spawned tasks (asyncio.create_task) and the main asyncio.run task live
 * in indexed slots. Waker::wake() marks a slot runnable if the slot is
 * still live and the generation matches. Timers store the parked task's
 * waker, so sleep only re-schedules the task that awaited it.
 */
struct Executor {
    using TimePoint = std::chrono::steady_clock::time_point;

    struct Timer {
        TimePoint deadline;
        Waker waker;

        bool operator<(const Timer& other) const {
            return deadline > other.deadline;
        }
    };

    struct Slot {
        std::shared_ptr<AnyTask> task;  // null once the task has completed
        uint32_t generation = 0;
        bool runnable = false;

        bool is_done() const noexcept { return task == nullptr; }
    };

    std::priority_queue<Timer> timers;
    std::vector<Slot> slots;
    std::deque<uint32_t> runnable;

    void register_timer(TimePoint deadline, Waker waker) {
        timers.push(Timer{deadline, waker});
    }

    // Take ownership of a spawned task. asyncio.create_task hands its
    // TaskState<T> here for independent polling; the user's Task<T>
    // handle shares the same state via shared_ptr so completion is
    // visible to both the executor (drops on done) and the user's await
    // (returns the cached value).
    uint32_t spawn(std::shared_ptr<AnyTask> task) {
        uint32_t id = static_cast<uint32_t>(slots.size());
        slots.emplace_back();
        auto& slot = slots.back();
        slot.task = std::move(task);
        slot.runnable = true;
        runnable.push_back(id);
        return id;
    }

    void mark_runnable(uint32_t id, uint32_t generation) noexcept {
        if (id >= slots.size()) return;
        auto& slot = slots[id];
        if (slot.is_done() || slot.generation != generation || slot.runnable) {
            return;
        }
        slot.runnable = true;
        runnable.push_back(id);
    }

    bool poll_slot(uint32_t id) {
        if (id >= slots.size()) return false;
        if (!slots[id].runnable || slots[id].is_done()) return false;
        slots[id].runnable = false;
        Waker waker{this, id, slots[id].generation};
        // The frame's poll may recursively spawn new tasks (which calls
        // slots.emplace_back and may reallocate), so we keep no slot
        // reference across the call. The AnyTask pointee itself lives on
        // the heap and is stable; only the slot vector storage moves.
        AnyTask* task = slots[id].task.get();
        if (task->poll_any(waker)) {
            slots[id].task.reset();
            ++slots[id].generation;
        }
        return true;
    }

    bool drain_runnable() {
        bool any_polled = false;
        while (!runnable.empty()) {
            uint32_t id = runnable.front();
            runnable.pop_front();
            any_polled = poll_slot(id) || any_polled;
        }
        return any_polled;
    }

    bool slot_done(uint32_t id) const {
        return id < slots.size() && slots[id].is_done();
    }

    bool has_live_tasks(uint32_t skip_id) const {
        for (uint32_t id = 0; id < slots.size(); ++id) {
            if (id == skip_id) continue;
            if (!slots[id].is_done()) return true;
        }
        return false;
    }

    // Wait for the next timer event. Returns true if at least one timer
    // fired; false if there's nothing to wait on (caller panics).
    bool wait_for_event() {
        if (timers.empty()) return false;
        TimePoint next = timers.top().deadline;
        std::this_thread::sleep_until(next);
        auto now = std::chrono::steady_clock::now();
        while (!timers.empty() && timers.top().deadline <= now) {
            Timer timer = timers.top();
            timers.pop();
            timer.waker.wake();
        }
        return true;
    }

    void run_until(uint32_t main_id) {
        while (true) {
            if (slot_done(main_id)) return;
            if (drain_runnable()) continue;
            if (slot_done(main_id)) return;
            if (!wait_for_event()) {
                tpy_panic("asyncio.run: no progress possible (coroutine "
                          "returned Pending with no pending timers; v1 has "
                          "no I/O reactor)");
            }
        }
    }

    // Cancel all live spawned tasks and drive them to completion so
    // their finally blocks run. We re-schedule live tasks directly
    // instead of waiting on timers; a task parked at an await point
    // observes cancellation when it is polled again.
    void drain_spawned_with_cancel(uint32_t skip_id, int max_polls = 8) {
        for (uint32_t id = 0; id < slots.size(); ++id) {
            if (id == skip_id) continue;
            auto& slot = slots[id];
            if (!slot.is_done()) {
                slot.task->cancel_any();
            }
        }
        for (int i = 0; i < max_polls && has_live_tasks(skip_id); ++i) {
            for (uint32_t id = 0; id < slots.size(); ++id) {
                if (id == skip_id) continue;
                auto& slot = slots[id];
                if (!slot.is_done()) {
                    mark_runnable(id, slot.generation);
                }
            }
            if (!drain_runnable()) break;
        }
    }
};

inline void Waker::wake() const noexcept {
    if (exec != nullptr) {
        exec->mark_runnable(task_id, generation);
    }
}

// Thread-local current executor pointer. Awaitables that need timer
// registration consult this.
inline thread_local Executor* current_executor = nullptr;


/// Bridge for TPy-side awaitables: register a timer with the current
/// executor at a deadline expressed in steady_clock seconds (matching
/// `time.monotonic()`'s domain). No-op if no executor is running, so
/// hand-rolled awaitables polled from a test harness without
/// `asyncio.run` don't crash.
inline void executor_register_timer_seconds(double deadline_seconds,
                                            Waker waker) {
    if (current_executor == nullptr) return;
    using DurDouble = std::chrono::duration<double>;
    auto deadline = Executor::TimePoint(
        std::chrono::duration_cast<Executor::TimePoint::duration>(
            DurDouble(deadline_seconds)));
    current_executor->register_timer(deadline, waker);
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
    auto state = std::make_shared<TaskStateImpl<T, CoroT>>(
        std::forward<CoroT>(coro));
    state->executor_owned = true;
    current_executor->spawn(state);
    return Task<T>::from_state(std::move(state));
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


/**
 * async_run -- v1 `asyncio.run` driver.
 *
 * Sets up a thread-local Executor, polls the top-level coro to
 * completion, and waits on the executor's timer heap when the coro
 * returns Pending. Returns the awaited value (Poll<T>::value() result)
 * for non-void coros; returns void otherwise.
 *
 * v1 has no I/O reactor -- only timer-driven sleep. A coro that returns
 * Pending with no pending timers panics ("no progress possible"); this
 * is the v1 equivalent of asyncio's "no current event loop" error.
 */
template <typename CoroT>
inline auto async_run(CoroT&& coro)
    -> decltype(std::declval<CoroT&>().__poll__(std::declval<Waker>()).value()) {
    using ResultT = decltype(std::declval<CoroT&>().__poll__(std::declval<Waker>()).value());
    using CoroValT = std::remove_cvref_t<CoroT>;
    // Per docs/ASYNC_DESIGN.md ("Context propagation"): asyncio.run
    // cannot be re-entered. Matches CPython's "asyncio.run() cannot be
    // called from a running event loop" RuntimeError.
    if (current_executor != nullptr) {
        tpy_panic("asyncio.run() cannot be called from a running event loop");
    }
    Executor exec;
    Executor* prev = current_executor;
    current_executor = &exec;
    constexpr uint32_t no_skip = static_cast<uint32_t>(-1);
    uint32_t main_id = no_skip;
    // Drain spawned tasks (cancel + poll until done) before returning,
    // so finally blocks run for any in-flight fire-and-forget tasks.
    // current_executor must still be active during drain because
    // user finally blocks may register timers / poll futures.
    auto drain_and_restore_executor = [&](uint32_t skip_id) noexcept {
        try {
            exec.drain_spawned_with_cancel(skip_id);
        } catch (...) {
            // Swallow exceptions thrown out of finally blocks during
            // drain; v1 has no place to surface them and dropping the
            // executor with a live exception would terminate.
        }
        current_executor = prev;
    };
    try {
        auto state = std::make_shared<TaskStateImpl<ResultT, CoroValT>>(
            std::forward<CoroT>(coro));
        main_id = exec.spawn(state);
        exec.run_until(main_id);
        drain_and_restore_executor(main_id);
        if (state->has_exc) {
            std::rethrow_exception(state->exc);
        }
        if constexpr (std::is_void_v<ResultT>) {
            return;
        } else {
            if (!state->has_result) {
                tpy_panic("asyncio.run: main coroutine completed without result");
            }
            ResultT value(std::move(*state->result_));
            state->result_.reset();
            state->has_result = false;
            return value;
        }
    } catch (...) {
        drain_and_restore_executor(main_id);
        throw;
    }
}

// Task<void> uses the primary template; Poll<void>'s `pending()` /
// `ready()` shape works through the same code path because all the
// generic accessors are conditional on the value type.

}  // namespace tpy
