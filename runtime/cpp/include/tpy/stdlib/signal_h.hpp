#pragma once
// Hand-written signal facade over the process-wide SIGINT layer
// (runtime/cpp/src/stdlib/signal_impl.cpp; model in tpy/interrupt.hpp).
//
// Mirrors the epoll_h.hpp / socket_h.hpp strategy: <signal.h>'s sigaction
// machinery and the wake-fd headers never cross into a TPy-generated TU; here
// we expose a handful of flat helpers taking plain scalars.
//
// asyncio.run owns Ctrl-C delivery while it runs: async_begin() hands it the
// layer's wake fd (an eventfd on Linux, a self-pipe on macOS / *BSD) to watch
// in its reactor, so a signal wakes a blocked `epoll_wait` / `kevent`
// race-free -- the byte is already pending even if the signal lands just
// before the wait. The run loop calls async_consume() after each wait to count
// Ctrl-Cs: the first cancels the root task, a second raises KeyboardInterrupt
// out of the run. SIGINT only, matching CPython; SIGTERM keeps its default.

extern "C" {

// Take over Ctrl-C delivery for an asyncio.run on the calling thread. Returns
// the readable wake fd (>= 0) to register in the reactor, or -1 when this run
// handles no SIGINT (not the interrupt target thread, or SIGINT was inherited
// as ignored). With no process-level layer armed (--no-main / extension
// builds) it installs the handler for the run.
int tpy_interrupt_async_begin();

// Hand delivery back to synchronous code; removes a handler async_begin()
// installed. Only called after async_begin() returned a fd.
void tpy_interrupt_async_end();

// 1 if a SIGINT arrived since the last call (consuming it and draining the
// wake fd), else 0. Always 0 outside an async_begin() / async_end() pair.
int tpy_interrupt_async_consume();

// kill(getpid(), sig) -- signal.raise_signal. Returns 0 on success.
int tpy_signal_raise(int sig);

// The blocking-wait primitive the stdlib's interruptible operations compose
// (lib/tpy/_interrupt.py). Waits until `fd` is readable (writable when
// `want_write`), `timeout` seconds pass (< 0: no timeout) or, on the
// interrupt target thread, a Ctrl-C is taken. Polls at least once, even
// with an expired deadline. Returns interrupt.hpp's kWaitReady (1),
// kWaitError (-1, errno set), kTimedOut (-2) or kInterrupted (-3).
int tpy_interrupt_wait(int fd, int want_write, double timeout);

}  // extern "C"
