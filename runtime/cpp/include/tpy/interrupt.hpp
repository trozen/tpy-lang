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

} // namespace tpy::interrupt_detail

extern "C" {
// Defined in signal_impl.cpp; referenced only from code that runs when the
// layer is linked (generated main(), the terminate handler it installs, and
// the embedding API below it in core.hpp).
int tpy_interrupt_process_startup();
int tpy_interrupt_install(int with_handler);
void tpy_request_interrupt();
[[noreturn]] void tpy_interrupt_exit_by_sigint();
}
