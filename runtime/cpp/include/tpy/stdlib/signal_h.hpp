#pragma once
// Hand-written signal facade for TPy's asyncio graceful-shutdown support.
//
// Mirrors the epoll_h.hpp / socket_h.hpp strategy: <signal.h> (sigaction,
// the SIG* macros) and <sys/eventfd.h> never cross into a TPy-generated TU.
// The real headers are included only by signal_impl.cpp; here we expose a
// handful of flat `tpy_signal_*` helpers taking plain scalars.
//
// Shutdown model: install_shutdown() arms SIGINT (only -- matching CPython's
// asyncio.run, which turns Ctrl-C into KeyboardInterrupt and leaves SIGTERM at
// its default terminate disposition) with an async-signal-safe handler that
// sets a flag and writes to an eventfd. The
// asyncio executor registers that eventfd in its epoll set so a signal wakes
// a blocked `epoll_wait` (race-free -- the byte is already pending even if the
// signal lands just before the wait). The run loop calls consume() after each
// wait to learn a signal fired, cancels the root task, and run() raises
// KeyboardInterrupt once cleanup has drained. Linux-only, like the reactor.

extern "C" {

// Create the wakeup eventfd and install the SIGINT handler (saving the
// previous disposition for restore()). Returns the eventfd (>= 0) to register
// in the reactor, or -1 on error. Idempotent: a second call while already
// installed returns the existing eventfd.
int tpy_signal_install_shutdown();

// Restore the saved SIGINT disposition and close the wakeup eventfd.
// No-op if not installed.
void tpy_signal_restore();

// 1 if a SIGINT has fired since the last consume (clears the flag and drains
// the eventfd counter), else 0. Safe to call when not installed (returns 0).
int tpy_signal_consume();

// raise(sig) on the current process -- used by tests to drive a deterministic,
// CPython-parity shutdown. Returns 0 on success.
int tpy_signal_raise(int sig);

}  // extern "C"
