#pragma once
// Hand-written signal facade over the process-wide signal layer
// (runtime/cpp/src/stdlib/signal_impl.cpp; model in tpy/interrupt.hpp).
//
// Mirrors the epoll_h.hpp / socket_h.hpp strategy: <signal.h>'s sigaction
// machinery and the wake-fd headers never cross into a TPy-generated TU; here
// we expose a handful of flat helpers taking plain scalars.
//
// asyncio.run joins the delivery while it runs: async_begin() hands it the
// layer's wake fd (an eventfd on Linux, a self-pipe on macOS / *BSD) to watch
// in its reactor, so a signal wakes a blocked `epoll_wait` / `kevent`
// race-free -- the byte is already pending even if the signal lands just
// before the wait -- and after each wait and each drain the run loop calls
// async_deliver() to run the handlers of the signals that arrived meanwhile.
// A coroutine's check points deliver every signal as synchronous code does.
// The run's graceful Ctrl-C is asyncio's own SIGINT handler, set with
// tpy_signal_set_for_run() over default_int_handler (CPython's
// asyncio.Runner).

#include <cstdint>
#include <functional>
#include <type_traits>

#include <tpy/interrupt.hpp>

extern "C" {

// Joins an asyncio.run on the calling thread to the delivery. Returns the
// readable wake fd (>= 0) to register in the reactor, or -1 when this run
// delivers nothing (not the interrupt target thread, inside a DeferSignals
// scope, no layer). With no process-level layer armed (--no-main /
// extension builds) it arms one, with the SIGINT handler, for the run.
int tpy_interrupt_async_begin();

// Ends the run's part; removes a layer async_begin() armed. Only called
// after async_begin() returned a fd.
void tpy_interrupt_async_end();

// Delivers the signals pending since the last call, in ascending signal
// order; a handler's exception propagates and leaves the signals after it
// pending. `after_wait` (the wake fd fired in the loop's reactor wait) also
// drains a wake byte nothing is pending for. Nothing outside an async_begin() /
// async_end() pair.
void tpy_interrupt_async_deliver(int after_wait);

// kill(getpid(), sig) -- signal.raise_signal. Returns 0 on success.
int tpy_signal_raise(int sig);

// The blocking-wait primitive the stdlib's interruptible operations compose
// (lib/tpy/_interrupt.py). Waits until `fd` is readable (writable when
// `want_write`) or `timeout` seconds pass (< 0: no timeout). On the
// interrupt target thread a signal arriving meanwhile is delivered inside
// the wait (an exception from it propagates: KeyboardInterrupt for a
// Ctrl-C); a handler that returns lets the wait go on to the same deadline.
// Polls at least once, even with an expired deadline. Returns interrupt.hpp's
// kWaitReady (1), kWaitError (-1, errno set) or kTimedOut (-2).
int tpy_interrupt_wait(int fd, int want_write, double timeout);

// What a delivered signal does (signal.py mirrors the values). Every
// signal starts at kSignalKindNone, which leaves its disposition alone;
// SIGINT starts at kSignalKindDefaultInt where the layer handles Ctrl-C.
// kSignalKindDefaultInt raises KeyboardInterrupt; kSignalKindUser calls
// signal.py's dispatcher.
enum : int {
    kSignalKindNone = 0,
    kSignalKindDefaultInt = 1,
    kSignalKindUser = 2,
};

// signal.signal's runtime half: installs the layer's C-level handler for
// `sig` and records `kind`. Raises CPython's errors: ValueError off the
// interrupt target thread or for a number outside 1..NSIG-1, OSError from
// sigaction (EINVAL for SIGKILL / SIGSTOP), and ValueError with no layer to
// run handlers (a --no-signals build, a --no-main host that never armed it,
// an asyncio.run that armed it only for its own duration).
void tpy_signal_set(int sig, int kind);

// asyncio.run's own SIGINT handler: tpy_signal_set(SIGINT, kind) also on a
// layer the run armed itself, which takes the handler with it when the run
// ends (SIGINT only: the only disposition that disarm restores). Called only
// after async_begin() returned a fd.
void tpy_signal_set_for_run(int kind);

// The kind `sig` has on the interrupt target thread (kSignalKindNone off it,
// or for a number out of range).
int tpy_signal_kind(int sig);

#ifndef TPY_NO_SIGNALS
// Marks `sig` pending as the layer's C-level handler does, without sending
// it (tests pend several signals before one check point with it).
void tpy_signal_request(int sig);
#endif

// Defined by signal.py's `@export` dispatcher: runs the handler of `sig`.
void tpy_signal_dispatch(std::int32_t sig);

}  // extern "C"

// Publishes the dispatcher. A function pointer rather than a call the layer
// makes by name, so the layer stays link-free like the Ops table
// (interrupt.hpp): a binary that never arms it or never imports signal (the
// clang-repl JIT, most programs) still resolves every symbol. Inline, so only
// a TU that calls it (signal.py's) references tpy_signal_dispatch.
inline void tpy_signal_enable_dispatch() {
#ifndef TPY_NO_SIGNALS
    ::tpy::interrupt_detail::dispatch.store(&tpy_signal_dispatch,
                                            std::memory_order_release);
#endif
}

// Whether `f` holds the function `g` -- how signal.signal tells
// default_int_handler from a handler of the user's (CPython compares the
// object). Codegen passes a function name as the function itself; anything
// else compares unequal and so runs as a user handler, which for
// default_int_handler raises the same KeyboardInterrupt.
// Two std::functions never compare equal: BUGS.md#signal-same-function-through-callable-param
template <class Sig, class G>
bool tpy_signal_same_function(const std::function<Sig>& f, G& g) {
    if constexpr (std::is_function_v<G>) {
        G* const* held = f.template target<G*>();
        return held != nullptr && *held == &g;
    } else {
        return false;
    }
}
