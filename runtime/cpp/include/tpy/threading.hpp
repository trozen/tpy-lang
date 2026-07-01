/**
 * TurboPython Runtime - OS-thread spawn/join (tpy.thread V1)
 *
 * Backs `tpy.thread.spawn` (the Runnable-struct form). A task is moved into a
 * fresh std::thread, its `run()` is executed there, and the result-or-exception
 * is delivered back through a std::future -- future::get() already does the
 * cross-thread result-or-rethrow that join() needs, and stores the result for
 * free, so we don't hand-roll an exception_ptr stash.
 *
 * This is the RAW handle. The consume bookkeeping (double-join / loud
 * abort-on-unconsumed-drop) lives in the TPy `JoinHandle` wrapper (thread.py):
 * its __del__ panics on the unconsumed path. This raw handle's destructor only
 * DETACHES an un-consumed thread -- never std::terminate, never panic -- so it
 * is safe to run during the stack unwinding that the wrapper's __del__ panic
 * triggers (a member teardown that terminated would mask that diagnostic).
 *
 * Pulled in only by modules importing `tpy.thread` (via `# tpy: include`), and
 * -lpthread is likewise import-gated, so non-threaded programs pay nothing.
 */

#pragma once

#include <future>
#include <thread>
#include <utility>

#include "core.hpp"

namespace tpy {

// Abort-on-unconsumed-drop. Called from the TPy JoinHandle's __del__ on the
// drop-without-join/detach path: a hard panic rather than a thrown exception,
// because a TPy __del__ lowers to a noexcept C++ destructor where `throw`
// would -Werror=terminate. The loud diagnostic still surfaces.
[[noreturn]] inline void join_handle_dropped_unconsumed() {
    tpy_panic("JoinHandle dropped without join() or detach() -- "
              "the spawned thread's result was discarded");
}

template <typename R>
class JoinHandle {
public:
    JoinHandle(std::thread thread, std::future<R> future)
        : thread_(std::move(thread)), future_(std::move(future)) {}

    JoinHandle(JoinHandle&&) noexcept = default;
    // Detach *this's own thread before overwriting: std::thread's move-assign
    // std::terminate()s if the target still holds a joinable thread, which
    // would violate this type's never-terminate contract. (The defaulted move
    // ctor is safe -- it leaves the source non-joinable, never terminates.)
    JoinHandle& operator=(JoinHandle&& other) noexcept {
        if (this != &other) {
            if (thread_.joinable()) thread_.detach();
            thread_ = std::move(other.thread_);
            future_ = std::move(other.future_);
        }
        return *this;
    }
    JoinHandle(const JoinHandle&) = delete;
    JoinHandle& operator=(const JoinHandle&) = delete;

    ~JoinHandle() {
        // Safe teardown only. The wrapper's __del__ owns the loud diagnostic;
        // detaching (not terminating) keeps that panic from being masked by a
        // std::terminate during unwinding.
        if (thread_.joinable()) thread_.detach();
    }

    // Block until the task finishes; return its result or rethrow its
    // exception in the joining thread.
    R join() {
        if (thread_.joinable()) thread_.join();
        return future_.get();
    }

    void detach() {
        if (thread_.joinable()) thread_.detach();
    }

private:
    std::thread thread_;
    std::future<R> future_;
};

// Move the task onto a new OS thread and run task.run() there. R is deduced
// from the task's run() so the TPy-side explicit R and the C++ result type
// cannot drift apart.
template <typename T>
auto spawn_thread(T task) -> JoinHandle<decltype(task.run())> {
    using R = decltype(task.run());
    std::packaged_task<R()> job(
        [t = std::move(task)]() mutable -> R { return t.run(); });
    std::future<R> future = job.get_future();
    std::thread thread(std::move(job));
    return JoinHandle<R>(std::move(thread), std::move(future));
}

} // namespace tpy
