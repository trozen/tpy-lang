/**
 * TurboPython Runtime - OS-thread spawn/join (tpy.thread V1)
 *
 * Backs `tpy.thread.spawn` (the Runnable-struct form). A task is moved into a
 * fresh std::thread, its `run()` is executed there, and the result-or-exception
 * is delivered back through a std::future -- future::get() already does the
 * cross-thread result-or-rethrow that join() needs, and stores the result for
 * free, so we don't hand-roll an exception_ptr stash.
 *
 * Task lifetime contract: the task is destroyed when its `run()` completes
 * (moved into a body-scoped local of the worker lambda), not when the handle
 * is joined/dropped -- matching Rust's drop-at-thread-completion, so a task's
 * RAII cleanup runs as soon as the thread finishes. `Send` task authors may
 * rely on this (e.g. a channel `Sender.__del__` that closes + notifies).
 *
 * This is the RAW handle. The consume bookkeeping (double-join raises) and the
 * interruptible join (a Ctrl-C raises KeyboardInterrupt while it waits) live
 * in the TPy `JoinHandle` wrapper (thread.py). Dropping an un-consumed handle
 * DETACHES the thread, here and in the wrapper -- never std::terminate -- so a
 * handle torn down by stack unwinding never masks the exception in flight.
 *
 * Pulled in only by modules importing `tpy.thread` (via `# tpy: include`), and
 * -lpthread is likewise import-gated, so non-threaded programs pay nothing.
 */

#pragma once

#include <chrono>
#include <csignal>
#include <exception>
#include <future>
#include <thread>
#include <type_traits>
#include <utility>
#include <variant>

#include "core.hpp"

namespace tpy {

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

    // True once the task has finished; waits at most `seconds` for it.
    bool wait_for(double seconds) {
        return future_.wait_for(std::chrono::duration<double>(seconds))
            == std::future_status::ready;
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

// Keeps the asynchronous signals blocked on the calling thread while a worker
// is created, so the worker inherits the blocked mask and the kernel delivers
// each of them to the interrupt target thread: a Ctrl-C, a `signal.signal`
// handler's signal, and raise_signal / os.kill(os.getpid(), ...) stay
// synchronous on the target. The faults and SIGPIPE stay unblocked: they are
// sent to the thread that caused them. SIGPROF and SIGVTALRM stay unblocked
// too: a profiler's process-directed ticks sample whichever thread is
// running. Only while the signal layer is armed; the creator's mask is
// restored on scope exit, also when thread creation throws.
class SpawnSignalBlock {
public:
    SpawnSignalBlock() : active_(interrupt_armed()) {
        if (active_) {
            sigset_t block;
            // Unqualified: function-like macros on macOS/BSD.
            sigfillset(&block);
            for (int sig : {SIGSEGV, SIGBUS, SIGFPE, SIGILL, SIGTRAP, SIGSYS,
                            SIGABRT, SIGPIPE, SIGPROF, SIGVTALRM}) {
                sigdelset(&block, sig);
            }
            pthread_sigmask(SIG_BLOCK, &block, &saved_);
        }
    }
    ~SpawnSignalBlock() {
        if (active_) {
            pthread_sigmask(SIG_SETMASK, &saved_, nullptr);
        }
    }
    SpawnSignalBlock(const SpawnSignalBlock&) = delete;
    SpawnSignalBlock& operator=(const SpawnSignalBlock&) = delete;

private:
    bool active_;
    sigset_t saved_{};
};

// Move the task onto a new OS thread and run task.run() there. R is deduced
// from the task's run() so the TPy-side explicit R and the C++ result type
// cannot drift apart. A `-> None` run() deduces void, but the TPy side maps
// None to std::monostate (JoinHandle[None] = JoinHandle<monostate>) -- adapt
// so a fire-and-forget task deduces the same type the wrapper expects.
template <typename T>
auto spawn_thread(T task) {
    using Raw = decltype(task.run());
    using R = std::conditional_t<std::is_void_v<Raw>, std::monostate, Raw>;
    std::packaged_task<R()> job(
        [t = std::move(task)]() mutable -> R {
            // Move the task into a body-scoped local so it is destroyed when
            // run() completes -- not when the packaged_task is torn down at
            // join. Matches Rust's drop-at-thread-completion, so a task's RAII
            // cleanup (e.g. a channel Sender.__del__ that closes + notifies)
            // fires as soon as the thread finishes rather than deadlocking a
            // peer that is blocked waiting for it before it joins.
            T active = std::move(t);
            if constexpr (std::is_void_v<Raw>) {
                active.run();
                return std::monostate{};
            } else {
                return active.run();
            }
        });
    std::future<R> future = job.get_future();
    SpawnSignalBlock signal_block;
    std::thread thread(std::move(job));
    return JoinHandle<R>(std::move(thread), std::move(future));
}

} // namespace tpy
