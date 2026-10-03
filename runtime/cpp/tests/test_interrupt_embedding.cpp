/**
 * Ctrl-C embedding API self-check (linked with signal_impl.cpp).
 *
 * A --no-main host never runs process_startup(), so no generated case reaches
 * tpy::install_interrupt_handler() / tpy::request_interrupt(). Pins that:
 *   - a host that keeps its own SIGINT handler and forwards through
 *     request_interrupt() gets a KeyboardInterrupt out of a TPy sleep, and its
 *     handler stays installed (installing TPy's own handler is the path every
 *     standalone program takes, pinned by tests/test_cli_signals.py);
 *   - an interrupt requested from another thread is delivered only on the
 *     thread that armed the layer;
 *   - a stdout / file write and a print chain deliver a pending interrupt
 *     after writing;
 *   - install_interrupt_handler(true) on a layer armed without a handler
 *     installs it then;
 *   - the disarm / re-arm path (a forked child, so it starts unarmed): the
 *     wake fds survive a disarm, a re-arm drops a request left pending while
 *     disarmed, and install_interrupt_handler() keeps a layer that an
 *     asyncio.run arm would otherwise remove when the run ends;
 *   - a DeferSignals scope (nested too) holds a pending interrupt across check
 *     points, a sleep and an asyncio.run's begin (armed or not), and the first
 *     check point after it delivers it once.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <chrono>
#include <csignal>
#include <cstdio>
#include <thread>

#include <poll.h>
#include <sys/wait.h>
#include <unistd.h>

#include "tpy/tpy.hpp"
#include "tpy/stdlib/signal_h.hpp"

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
    host_handler_ran = 1;
    tpy::request_interrupt();
}

// Sleeps up to 10s; true when a KeyboardInterrupt ended it early.
bool sleep_interrupted() {
    auto start = std::chrono::steady_clock::now();
    try {
        tpy::time_sleep(10.0);
    } catch (const tpy::KeyboardInterrupt&) {
        return std::chrono::steady_clock::now() - start < std::chrono::seconds(5);
    }
    return false;
}

// Readable right now, without waiting.
bool fd_readable(int fd) {
    pollfd pfd{};
    pfd.fd = fd;
    pfd.events = POLLIN;
    return ::poll(&pfd, 1, 0) > 0;
}

// True when a check point raised KeyboardInterrupt.
bool delivered() {
    try {
        tpy::check_signals();
    } catch (const tpy::KeyboardInterrupt&) {
        return true;
    }
    return false;
}

bool interrupt_pending() {
    return tpy::interrupt_detail::pending.load() != 0;
}

// Starts from an unarmed layer, as a --no-main host does before its first
// asyncio.run.
void check_disarm_rearm() {
    {
        tpy::DeferSignals defer;
        int fd = tpy_interrupt_async_begin();
        check(fd == -1 && !tpy::interrupt_armed(),
              "asyncio.run inside a scope does not arm an unarmed layer");
        if (fd >= 0) {
            tpy_interrupt_async_end();
        }
    }

    int fd = tpy_interrupt_async_begin();  // arms for the run only
    check(fd >= 0 && tpy::interrupt_armed(), "asyncio.run arms an unarmed layer");
    tpy_interrupt_async_end();
    check(!tpy::interrupt_armed(), "the run's end disarms the layer it armed");

    // Nothing consumes a request made while disarmed.
    tpy::request_interrupt();
    int again = tpy_interrupt_async_begin();
    check(again == fd, "the wake fds survive a disarm");
    check(!fd_readable(again), "a re-arm drains the stale wake byte");
    check(tpy_interrupt_async_consume() == 0, "a re-arm drops the stale request");

    check(tpy::install_interrupt_handler(false), "install during the run's arm");
    tpy_interrupt_async_end();
    check(tpy::interrupt_armed(), "the installed layer outlives the run");
}

}  // namespace

int main() {
    // Before any thread exists, so the child is a plain copy of this process.
    pid_t child = ::fork();
    if (child == 0) {
        check_disarm_rearm();
        std::fflush(stdout);
        ::_exit(failures == 0 ? 0 : 1);
    }
    int status = 0;
    check(child > 0 && ::waitpid(child, &status, 0) == child && WIFEXITED(status)
              && WEXITSTATUS(status) == 0,
          "disarm / re-arm checks");

    // Host keeps its handler; the layer is armed without one.
    std::signal(SIGINT, host_handler);
    check(!tpy::signals_deliverable(), "nothing is deliverable before the layer is armed");
    check(tpy::install_interrupt_handler(false), "arm without a handler");
    // Where a Ctrl-C would be raised: the armed thread only, outside a
    // deferral scope and outside an asyncio.run.
    check(tpy::signals_deliverable(), "deliverable on the armed thread");
    {
        tpy::DeferSignals defer;
        check(!tpy::signals_deliverable(), "not deliverable inside a deferral scope");
    }
    bool worker_deliverable = true;
    std::thread([&] { worker_deliverable = tpy::signals_deliverable(); }).join();
    check(!worker_deliverable, "not deliverable off the armed thread");
    check(tpy_interrupt_async_begin() >= 0, "asyncio.run takes delivery");
    check(!tpy::signals_deliverable(), "not deliverable while asyncio.run owns delivery");
    tpy_interrupt_async_end();
    check(tpy::signals_deliverable(), "deliverable again after the run");
    std::thread([] {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
        std::raise(SIGINT);  // runs the host handler on this worker thread
    }).detach();
    check(sleep_interrupted(), "request_interrupt() ends a sleep on the armed thread");
    check(host_handler_ran == 1, "the host's own handler stayed installed");

    // A request on a worker thread is not delivered there.
    bool worker_saw = false;
    std::thread worker([&] {
        tpy::request_interrupt();
        try {
            tpy::check_signals();
        } catch (const tpy::KeyboardInterrupt&) {
            worker_saw = true;
        }
    });
    worker.join();
    check(!worker_saw, "no KeyboardInterrupt off the target thread");
    bool main_saw = false;
    try {
        tpy::check_signals();
    } catch (const tpy::KeyboardInterrupt&) {
        main_saw = true;
    }
    check(main_saw, "the worker's request reaches the target thread");

    // Writes are check points: the write happens, then the pending interrupt.
    tpy::request_interrupt();
    bool stdout_saw = false;
    try {
        tpy::get_sys_stdout().write("");
    } catch (const tpy::KeyboardInterrupt&) {
        stdout_saw = true;
    }
    check(stdout_saw, "a pending interrupt is raised by a stdout write");
    tpy::request_interrupt();
    bool file_saw = false;
    try {
        tpy::builtin_open_mode("/dev/null", "w").write("x");
    } catch (const tpy::KeyboardInterrupt&) {
        file_saw = true;
    }
    check(file_saw, "a pending interrupt is raised by a file write");
    // A generated print chain ends in the check_signals manipulator: the
    // line is written, then the pending interrupt raised; nothing pending is
    // a no-op.
    tpy::request_interrupt();
    bool print_saw = false;
    try {
        std::cout << "" << tpy::check_signals;
    } catch (const tpy::KeyboardInterrupt&) {
        print_saw = true;
    }
    check(print_saw, "a pending interrupt is raised by a print chain");
    bool print_quiet = true;
    try {
        std::cout << "" << tpy::check_signals;
    } catch (const tpy::KeyboardInterrupt&) {
        print_quiet = false;
    }
    check(print_quiet, "a print chain with nothing pending is a no-op");

    // A deferral scope holds the interrupt; the first check point after it
    // raises it once.
    tpy::request_interrupt();
    {
        tpy::DeferSignals defer;
        check(!delivered() && interrupt_pending(), "a scope defers a check point");
    }
    check(delivered(), "the check point after the scope delivers");
    check(!delivered(), "a deferred interrupt is delivered once");

    tpy::request_interrupt();
    {
        tpy::DeferSignals outer;
        {
            tpy::DeferSignals inner;
            check(!delivered(), "nested scopes defer");
        }
        check(!delivered() && interrupt_pending(), "the outer scope still defers");
    }
    check(delivered(), "closing both scopes delivers");

    // asyncio.run inside a scope must not take ownership: its consume would
    // raise out of the noexcept body the scope guards.
    tpy::request_interrupt();
    {
        tpy::DeferSignals defer;
        int fd = tpy_interrupt_async_begin();
        check(fd == -1, "asyncio.run inside a scope declines");
        check(interrupt_pending(), "a declined asyncio.run leaves the interrupt pending");
        if (fd >= 0) {
            tpy_interrupt_async_end();  // hand delivery back so later checks still run
        }
    }
    check(delivered(), "the interrupt survives a declined asyncio.run");

    // A wait inside a scope runs its full length and returns normally.
    tpy::request_interrupt();
    {
        tpy::DeferSignals defer;
        auto start = std::chrono::steady_clock::now();
        bool raised = false;
        try {
            tpy::time_sleep(0.02);
        } catch (const tpy::KeyboardInterrupt&) {
            raised = true;
        }
        check(!raised && std::chrono::steady_clock::now() - start
                             >= std::chrono::milliseconds(20),
              "a sleep inside a scope runs to completion");
    }
    check(delivered(), "the interrupt held across a sleep is delivered after it");

    // A later call asking for the handler installs it over the host's.
    host_handler_ran = 0;
    check(tpy::install_interrupt_handler(true), "add the handler to an armed layer");
    std::thread([] {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
        std::raise(SIGINT);
    }).detach();
    check(sleep_interrupted(), "the added handler ends a sleep");
    check(host_handler_ran == 0, "the added handler replaced the host's");

    return failures == 0 ? 0 : 1;
}
