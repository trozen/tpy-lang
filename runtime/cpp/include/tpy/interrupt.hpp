#pragma once
// Process-wide signal state shared by the signal layer
// (runtime/cpp/src/stdlib/signal_impl.cpp) and the header-only check points
// (core.hpp's check_signals(), time.sleep, input()).
//
// Model: the layer's C-level handler only marks its signal tripped, sets
// `pending` and writes a wake fd; what the signal does runs later, on the
// thread that armed the layer, at the next check point (every generated print
// chain ends in the check_signals manipulator): a Ctrl-C under
// default_int_handler raises KeyboardInterrupt, a `signal.signal` handler is
// called. An operation that can block on an fd waits on the wake fd too, so a
// signal wakes it without EINTR (the handler is installed with SA_RESTART, so
// no other syscall ever sees one) and is delivered inside the wait; a handler
// that returns lets the wait go on to its deadline (PEP 475). The stdlib's
// TPy code composes its blocking operations from check_signals() and
// tpy_interrupt_wait() (signal_h.hpp) through lib/tpy/_interrupt.py: whoever
// owns a blocking fd waits for it there first (socket; subprocess.Popen for
// its pipes, and for its child's exit as a pidfd where the OS has one). A wait
// that is not on an fd re-checks in slices: tpy.thread's JoinHandle.join()
// every 100 ms, Popen.wait() without a pidfd with a backoff.
//
// A body that must not throw (a destructor, a noexcept move, a host's C
// callback) opens a tpy::DeferSignals scope (below): inside it no check point
// or wait delivers, and pending signals are delivered at the first check point
// after it.
//
// A build that defines TPY_NO_SIGNALS compiles the layer out: check_signals()
// and DeferSignals are empty, nothing installs a signal handler and no
// KeyboardInterrupt comes from a signal -- for generated code embedded in a
// host that owns its signals. The define must be set on every TU of the target,
// the runtime sources included (the link guard at the end of this header
// rejects a runtime built without it).
//
// Free of system headers so it can sit under every generated TU.

#include <atomic>
#include <cstdint>
#include <string>

namespace tpy::interrupt_detail {

// Result codes of tpy_interrupt_wait(); lib/tpy/_interrupt.py mirrors the
// values.
inline constexpr int kWaitReady = 1;
inline constexpr int kWaitError = -1;  // errno set
inline constexpr int kTimedOut = -2;

#ifndef TPY_NO_SIGNALS

// What the signal layer publishes while armed. Header check points reach the
// layer through this table rather than by calling signal_impl.cpp directly, so
// a build that never arms it and links no runtime impls (the clang-repl JIT)
// still resolves every symbol the headers reference.
struct Ops {
    // Delivers the signals pending for the calling thread, if it is
    // deliverable: raises KeyboardInterrupt for a Ctrl-C, calls the
    // `signal.signal` handler of the others. An exception from either
    // propagates, and the signals after it stay pending.
    void (*deliver)();
    // Sleeps `seconds`, delivering the signals that arrive meanwhile; a
    // handler that returns lets the sleep go on to its deadline.
    void (*sleep)(double seconds);
    // 1 with the next stdin line in `out` (newline stripped), 0 at EOF.
    // Delivers the signals that arrive while it waits; the part of the line
    // read so far is kept when the handler returns and dropped when it
    // raises (a Ctrl-C), as CPython's input() does.
    int (*read_line)(std::string& out);
    // 1 when a signal can be delivered to the calling thread right now (the
    // interrupt target, outside a DeferSignals scope; inside asyncio.run
    // too): the threads on which a wait is worth routing through the wake
    // fd.
    int (*deliverable)();
};

// Some signal is pending: set by the layer's C-level handler (or
// request_interrupt()) after it marks its signal tripped, and cleared by the
// delivery that takes the tripped signals. The one flag the check points
// load. A lock-free atomic is both signal-safe and visible across threads --
// the handler may run on any thread that leaves the signal unblocked.
inline std::atomic<int> pending{0};

// Non-null while the layer is armed.
inline std::atomic<const Ops*> ops{nullptr};

// signal.py's dispatcher, called for a signal with a `signal.signal`
// handler; published by tpy_signal_enable_dispatch() (stdlib/signal_h.hpp)
// the first time signal.signal installs one. A pointer for the same reason as
// `ops`: the layer never names the symbol, so a binary that does not arm it
// resolves without it.
inline std::atomic<void (*)(std::int32_t)> dispatch{nullptr};

// Open DeferSignals scopes on this thread. A plain constant-initialized int so
// no TLS guard or destructor is involved; the C-level handler never reads it
// (thread-local access is not signal-safe), only the layer's check points and
// waits do.
inline constinit thread_local int defer_depth = 0;

#endif  // TPY_NO_SIGNALS

} // namespace tpy::interrupt_detail

namespace tpy {

#ifndef TPY_NO_SIGNALS

// Defers signal delivery on this thread for the scope's lifetime. While one is
// open the signal layer treats the thread as not deliverable: a check point
// returns instead of throwing KeyboardInterrupt or running a `signal.signal`
// handler, a wait (time.sleep, input(), blocking socket I/O, a subprocess
// wait or pipe) runs to completion without watching the wake fd, and an
// asyncio.run started inside declines signal handling. The signals stay
// pending and are delivered at the first check point after the scope closes.
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

#else  // TPY_NO_SIGNALS

// Nothing to defer. The constructor and destructor stay user-provided so the
// scope local generated code declares is not an unused variable.
struct DeferSignals {
    DeferSignals() noexcept {}
    ~DeferSignals() noexcept {}
    DeferSignals(const DeferSignals&) = delete;
    DeferSignals(DeferSignals&&) = delete;
    DeferSignals& operator=(const DeferSignals&) = delete;
    DeferSignals& operator=(DeferSignals&&) = delete;
};

#endif  // TPY_NO_SIGNALS

} // namespace tpy

extern "C" {
// Defined in signal_impl.cpp; referenced only from code that runs when the
// layer is linked (generated main(), the terminate handler it installs, and
// the embedding API below it in core.hpp).
#ifndef TPY_NO_SIGNALS
int tpy_interrupt_process_startup();
int tpy_interrupt_install(int with_handler);
void tpy_request_interrupt();
#else
// The exception: the link guard below refers to it from every TU.
extern const int tpy_signals_compiled_out;
#endif
[[noreturn]] void tpy_interrupt_exit_by_sigint();
}

#ifdef TPY_NO_SIGNALS
namespace tpy::interrupt_detail {
// Link guard: every TU compiled with TPY_NO_SIGNALS refers to a symbol that
// only signal_impl.cpp compiled the same way defines. Without it, a runtime
// built with the layer could still be armed (a host's own TU, asyncio.run)
// and raise KeyboardInterrupt through cleanup bodies whose DeferSignals scope
// was compiled empty here. `used` keeps the compiler from dropping it; on ELF
// only `retain` keeps the linker's --gc-sections from doing the same.
#if defined(__ELF__)
[[gnu::used, gnu::retain]]
#else
[[gnu::used]]
#endif
inline constexpr const int* no_signals_link_guard = &tpy_signals_compiled_out;
} // namespace tpy::interrupt_detail
#endif
