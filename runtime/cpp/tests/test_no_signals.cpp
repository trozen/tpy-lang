/**
 * TPY_NO_SIGNALS self-check (built with the define, linked with
 * signal_impl.cpp built the same way).
 *
 * The generated code is the same with and without the define, so no snapshot
 * case reaches what the runtime compiles to under it. Pins that:
 *   - a SIGINT the host's own handler takes never surfaces as a
 *     KeyboardInterrupt: check points, a print chain, time.sleep (which runs
 *     its full time) and raise_signal are the plain operations;
 *   - DeferSignals is an empty scope that still declares cleanly under
 *     -Wall -Wextra -Werror, the flags the harness builds this file with;
 *   - asyncio.run's hooks decline SIGINT handling, and signal.signal's
 *     runtime half raises ValueError;
 *   - the fd wait still reports readiness and timeouts.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <chrono>
#include <csignal>
#include <cstdio>
#include <iostream>
#include <type_traits>

#include <unistd.h>

#include "tpy/tpy.hpp"
#include "tpy/stdlib/signal_h.hpp"

#ifndef TPY_NO_SIGNALS
#error "this self-check is built with -DTPY_NO_SIGNALS"
#endif

static_assert(std::is_empty_v<tpy::DeferSignals>);

namespace {

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

volatile std::sig_atomic_t host_handler_ran = 0;

void host_handler(int) {
    host_handler_ran = host_handler_ran + 1;
}

// The shape generated cleanup bodies have: a scope local nothing reads.
void cleanup_body() noexcept {
    ::tpy::DeferSignals __tpy_defer_signals;
    ::tpy::check_signals();
}

}  // namespace

int main() {
    std::signal(SIGINT, host_handler);
    check(!tpy::interrupt_armed(), "the layer is never armed");
    check(!tpy::signals_deliverable(), "no thread is ever a delivery target");

    try {
        check(tpy_signal_raise(SIGINT) == 0, "raise_signal sends the signal");
        check(host_handler_ran == 1, "the host's handler takes the SIGINT");
        tpy::check_signals();
        std::cout << "line" << "\n" << ::tpy::check_signals;
        cleanup_body();
        tpy::time_sleep(0.0);
        auto start = std::chrono::steady_clock::now();
        std::raise(SIGINT);
        tpy::time_sleep(0.05);
        auto slept = std::chrono::steady_clock::now() - start;
        check(slept >= std::chrono::milliseconds(50), "time.sleep runs its full time");
        check(host_handler_ran == 2, "the second SIGINT reached the host too");
    } catch (const tpy::KeyboardInterrupt&) {
        check(false, "no check point raises KeyboardInterrupt");
    }

    check(tpy_interrupt_async_begin() == -1, "asyncio.run handles no SIGINT");
    tpy_interrupt_async_deliver(1);
    check(tpy_signal_kind(SIGINT) == kSignalKindNone, "no signal has a kind");
    bool refused = false;
    try {
        tpy_signal_set(SIGTERM, kSignalKindUser);
    } catch (const tpy::ValueError&) {
        refused = true;
    }
    check(refused, "signal.signal is refused");
    struct sigaction current{};
    ::sigaction(SIGINT, nullptr, &current);
    check(current.sa_handler == host_handler, "the host's handler stays installed");

    int fds[2];
    check(::pipe(fds) == 0, "pipe");
    namespace idet = tpy::interrupt_detail;
    check(tpy_interrupt_wait(fds[0], 0, 0.01) == idet::kTimedOut,
          "the fd wait times out on an idle fd");
    check(::write(fds[1], "x", 1) == 1, "write");
    check(tpy_interrupt_wait(fds[0], 0, -1.0) == idet::kWaitReady,
          "the fd wait reports a readable fd");
    check(tpy_interrupt_wait(fds[1], 1, 0.0) == idet::kWaitReady,
          "the fd wait reports a writable fd");

    return failures == 0 ? 0 : 1;
}
