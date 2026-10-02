#pragma once
// Process-wide Ctrl-C state shared by the SIGINT layer
// (runtime/cpp/src/stdlib/signal_impl.cpp) and the header-only interrupt
// check points (core.hpp's check_signals(), time.sleep, input()).
//
// Model: the SIGINT handler only sets `pending` and writes a wake fd; the
// KeyboardInterrupt is raised later, on the thread that armed the layer, at the
// next interruptible operation (every generated print chain ends in the
// check_signals manipulator). An operation that can block on an fd waits on
// the wake fd too, so a Ctrl-C wakes it without EINTR (the handler is installed
// with SA_RESTART, so no other syscall ever sees one). The stdlib's TPy code
// composes its blocking operations from check_signals() and
// tpy_interrupt_wait() (signal_h.hpp): socket through lib/tpy/_interrupt.py,
// and tpy.thread's JoinHandle.join(), whose wait is not on an fd (a thread's
// end is not one), by re-checking in 100 ms slices.
//
// A body that must not throw (a destructor, a noexcept move, a host's C
// callback) opens a tpy::DeferSignals scope (below): inside it no check point
// or wait raises, and the Ctrl-C is raised at the first check point after it.
//
// Free of system headers so it can sit under every generated TU.

#include <atomic>
#include <string>

namespace tpy::interrupt_detail {

// Result codes of the layer's waits: tpy_interrupt_wait() returns all four,
// the Ops table's sleep and read_line kInterrupted. lib/tpy/_interrupt.py
// mirrors the values.
inline constexpr int kWaitReady = 1;
inline constexpr int kWaitError = -1;  // errno set
inline constexpr int kTimedOut = -2;
inline constexpr int kInterrupted = -3;  // a Ctrl-C was consumed

// What the SIGINT layer publishes while armed. Header check points reach the
// layer through this table rather than by calling signal_impl.cpp directly, so
// a build that never arms it and links no runtime impls (the clang-repl JIT)
// still resolves every symbol the headers reference.
struct Ops {
    // 1 when an interrupt was pending for the calling thread and is now
    // consumed, else 0.
    int (*take)();
    // 0 once `seconds` elapsed, kInterrupted when a Ctrl-C cut it short.
    int (*sleep)(double seconds);
    // 1 with the next stdin line in `out` (newline stripped), 0 at EOF,
    // kInterrupted when a Ctrl-C arrived while waiting for input.
    int (*read_line)(std::string& out);
};

// Set by the SIGINT handler (or request_interrupt()) and cleared by whoever
// consumes it. A lock-free atomic is both signal-safe and visible across
// threads -- the handler may run on any thread that leaves SIGINT unblocked.
inline std::atomic<int> pending{0};

// Non-null while the layer is armed.
inline std::atomic<const Ops*> ops{nullptr};

// Open DeferSignals scopes on this thread. A plain constant-initialized int so
// no TLS guard or destructor is involved; the SIGINT handler never reads it
// (thread-local access is not signal-safe), only the layer's check points and
// waits do.
inline constinit thread_local int defer_depth = 0;

} // namespace tpy::interrupt_detail

namespace tpy {

// Defers Ctrl-C delivery on this thread for the scope's lifetime. While one is
// open the SIGINT layer treats the thread as not deliverable: a check point
// returns instead of throwing KeyboardInterrupt, a wait (time.sleep, input(),
// blocking socket I/O) runs to completion without watching the wake fd, and an
// asyncio.run started inside declines SIGINT handling. The Ctrl-C stays
// pending and is raised at the first check point after the scope closes.
// Generated code opens one in a body that runs under noexcept or a catch-all
// (a __del__ destructor, a __move__ constructor, the std::hash wrapper, an
// abandoned generator frame's cleanup), where a throw would terminate the
// process, unless the compiler proved the body cannot reach a check point. A host may open one around its own non-throwing
// contexts (a C callback, a destructor) for the same reason.
//
// Scopes nest -- a __del__ that drops a suspended generator opens two -- and
// stay balanced because only the constructor / destructor pair touches the
// count. The destructor never delivers: it may itself run in a noexcept body.
// A second Ctrl-C while the first is still pending terminates the process as
// it does anywhere else, so a long-running deferred body stays killable.
struct DeferSignals {
    DeferSignals() noexcept { ++interrupt_detail::defer_depth; }
    ~DeferSignals() noexcept { --interrupt_detail::defer_depth; }
    DeferSignals(const DeferSignals&) = delete;
    DeferSignals(DeferSignals&&) = delete;
    DeferSignals& operator=(const DeferSignals&) = delete;
    DeferSignals& operator=(DeferSignals&&) = delete;
};

} // namespace tpy

extern "C" {
// Defined in signal_impl.cpp; referenced only from code that runs when the
// layer is linked (generated main(), the terminate handler it installs, and
// the embedding API below it in core.hpp).
int tpy_interrupt_process_startup();
int tpy_interrupt_install(int with_handler);
void tpy_request_interrupt();
[[noreturn]] void tpy_interrupt_exit_by_sigint();
}
