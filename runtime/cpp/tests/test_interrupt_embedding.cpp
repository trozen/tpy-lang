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
 *     disarmed, install_interrupt_handler() keeps a layer that an
 *     asyncio.run arm would otherwise remove when the run ends, and the run's
 *     own SIGINT handler may be set on a layer it armed itself;
 *   - inside an asyncio.run a forwarded Ctrl-C is an ordinary signal: a check
 *     point raises it at default_int, and under the run's SIGINT handler (a
 *     user kind, what asyncio.run installs) a check point, a sleep, an
 *     input() and an fd wait run that handler and go on; the loop's
 *     delivery runs what arrived while it waited and drains a stray wake
 *     byte;
 *   - a DeferSignals scope (nested too) holds a pending interrupt across check
 *     points, a sleep and an asyncio.run's begin (armed or not), and the first
 *     check point after it delivers it once;
 *   - signal.signal's runtime half: refused with no layer, under an
 *     asyncio.run's own arming and off the armed thread; a host that keeps
 *     its SIGINT handler keeps it under a user kind, and its forwarded
 *     request then runs the dispatcher instead of raising KeyboardInterrupt.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <chrono>
#include <csignal>
#include <cstdio>
#include <ctime>
#include <string>
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

// Stands in for signal.py's dispatcher, which this self-check does not link;
// tests/test_cli_signals.py's --no-main host drives the real one.
int dispatched = 0;

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

// A forwarded Ctrl-C 50 ms from now, from another thread.
std::thread request_interrupt_later() {
    return std::thread([] {
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        tpy::request_interrupt();
    });
}

// A pipe whose write end gets `data` after 200 ms, from another thread; the
// read end is returned. (main() ignores SIGPIPE, so a check that fails before
// the read leaves the writer an EPIPE rather than killing the process.)
int pipe_fed_later(const char* data) {
    int fds[2];
    if (::pipe(fds) != 0) {
        return -1;
    }
    int wr = fds[1];
    std::thread([wr, data] {
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
        ssize_t r = ::write(wr, data, std::char_traits<char>::length(data));
        (void)r;
        ::close(wr);
    }).detach();
    return fds[0];
}

// CPU time since `cpu0` stayed small: a wait did not spin on a wake byte.
bool no_spin(std::clock_t cpu0) {
    return std::clock() - cpu0 < CLOCKS_PER_SEC / 20;
}

template <class F>
bool raises_value_error(F f) {
    try {
        f();
    } catch (const tpy::ValueError&) {
        return true;
    }
    return false;
}

// Starts from an unarmed layer, as a --no-main host does before its first
// asyncio.run.
void check_disarm_rearm() {
    check(raises_value_error([] { tpy_signal_set(SIGUSR1, kSignalKindUser); }),
          "signal.signal needs an armed layer");
    if (tpy_interrupt_async_begin() >= 0) {
        check(raises_value_error([] { tpy_signal_set(SIGUSR1, kSignalKindUser); }),
              "signal.signal refuses a layer armed only for an asyncio.run");
        check(tpy_signal_kind(SIGINT) == kSignalKindDefaultInt,
              "the run's own arming starts SIGINT at default_int");
        tpy_signal_enable_dispatch();
        tpy_signal_set_for_run(kSignalKindUser);
        check(tpy_signal_kind(SIGINT) == kSignalKindUser,
              "the run sets its SIGINT handler on a layer it armed");
        tpy::request_interrupt();
        check(!delivered() && dispatched == SIGINT, "the run's SIGINT handler runs");
        dispatched = 0;
        tpy_interrupt_async_end();
    }

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
    check(!interrupt_pending() && !delivered(), "a re-arm drops the stale request");
    check(tpy_signal_kind(SIGINT) == kSignalKindDefaultInt,
          "a re-arm starts SIGINT at default_int again");

    check(tpy::install_interrupt_handler(false), "install during the run's arm");
    tpy_interrupt_async_end();
    check(tpy::interrupt_armed(), "the installed layer outlives the run");
}

}  // namespace

extern "C" void tpy_signal_dispatch(std::int32_t sig) {
    dispatched = sig;
}

int main() {
    std::signal(SIGPIPE, SIG_IGN);
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
    // Where a signal would be delivered: the armed thread only, outside a
    // deferral scope.
    check(tpy::signals_deliverable(), "deliverable on the armed thread");
    {
        tpy::DeferSignals defer;
        check(!tpy::signals_deliverable(), "not deliverable inside a deferral scope");
    }
    bool worker_deliverable = true;
    std::thread([&] { worker_deliverable = tpy::signals_deliverable(); }).join();
    check(!worker_deliverable, "not deliverable off the armed thread");
    // Inside an asyncio.run a forwarded Ctrl-C is an ordinary signal: at
    // default_int a check point raises it.
    int run_fd = tpy_interrupt_async_begin();
    check(run_fd >= 0, "asyncio.run joins the delivery");
    check(tpy::signals_deliverable(), "deliverable inside asyncio.run (a coroutine's check points)");
    tpy::request_interrupt();
    check(delivered(), "a check point in the run raises a default_int Ctrl-C");
    check(!fd_readable(run_fd), "the delivery drained the wake byte");
    // Under the run's SIGINT handler (the user kind asyncio.run installs over
    // default_int) a sleep, an input() and an fd wait run it when the Ctrl-C
    // arrives and go on, without spinning on a wake byte.
    tpy_signal_enable_dispatch();
    tpy_signal_set_for_run(kSignalKindUser);
    dispatched = 0;
    std::thread req = request_interrupt_later();
    auto start = std::chrono::steady_clock::now();
    std::clock_t cpu0 = std::clock();
    bool sleep_raised = false;
    try {
        tpy::time_sleep(0.2);
    } catch (const tpy::KeyboardInterrupt&) {
        sleep_raised = true;
    }
    req.join();
    check(!sleep_raised && dispatched == SIGINT
              && std::chrono::steady_clock::now() - start >= std::chrono::milliseconds(200),
          "a sleep runs the run's SIGINT handler and goes on");
    check(no_spin(cpu0), "a sleep does not spin after the handler ran");
    // The same for input() (stdin swapped for a pipe fed later) ...
    int saved_stdin = ::dup(0);
    int in_rd = pipe_fed_later("abc\n");
    check(saved_stdin >= 0 && in_rd >= 0 && ::dup2(in_rd, 0) == 0, "stdin swapped for a pipe");
    ::close(in_rd);
    dispatched = 0;
    req = request_interrupt_later();
    cpu0 = std::clock();
    bool input_raised = false;
    std::string line;
    try {
        line = tpy::input_line();
    } catch (const tpy::KeyboardInterrupt&) {
        input_raised = true;
    }
    req.join();
    check(!input_raised && line == "abc" && dispatched == SIGINT,
          "input() runs the run's SIGINT handler and goes on");
    check(no_spin(cpu0), "input() does not spin after the handler ran");
    ::dup2(saved_stdin, 0);
    ::close(saved_stdin);
    // ... and for an fd wait (a socket or a subprocess pipe).
    int wait_rd = pipe_fed_later("x");
    dispatched = 0;
    req = request_interrupt_later();
    cpu0 = std::clock();
    bool wait_raised = false;
    int wait_rc = 0;
    try {
        wait_rc = tpy_interrupt_wait(wait_rd, 0, 5.0);
    } catch (const tpy::KeyboardInterrupt&) {
        wait_raised = true;
    }
    req.join();
    ::close(wait_rd);
    check(!wait_raised && wait_rc == tpy::interrupt_detail::kWaitReady && dispatched == SIGINT,
          "an fd wait runs the run's SIGINT handler and goes on");
    check(no_spin(cpu0), "an fd wait does not spin after the handler ran");
    // The loop's own delivery: nothing pending runs nothing, a Ctrl-C that
    // arrived while it waited runs the handler, and after a wait the wake fd
    // fired on a wake byte nothing is pending for is drained, so the one-shot
    // reactor does not fire on it at every re-arm.
    dispatched = 0;
    tpy_interrupt_async_deliver(0);
    check(dispatched == 0, "the loop's delivery with nothing pending runs nothing");
    tpy::request_interrupt();
    tpy_interrupt_async_deliver(0);
    check(dispatched == SIGINT && !fd_readable(run_fd), "the loop's delivery runs the handler");
#if defined(__linux__)
    std::uint64_t one = 1;
    check(::write(run_fd, &one, sizeof(one)) == static_cast<ssize_t>(sizeof(one)),
          "a stray wake byte");
    tpy_interrupt_async_deliver(1);
    check(!fd_readable(run_fd), "the loop's delivery after a wait drains a stray wake byte");
#endif
    tpy_signal_set_for_run(kSignalKindDefaultInt);
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

    // asyncio.run inside a scope must decline: its loop's delivery would
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

    // signal.signal on the host's layer: refused off the armed thread; a user
    // SIGINT kind leaves the host's handler in place and turns its forwarded
    // request into a dispatcher call.
    bool worker_refused = false;
    std::thread([&] {
        worker_refused = raises_value_error(
            [] { tpy_signal_set(SIGUSR1, kSignalKindUser); });
    }).join();
    check(worker_refused, "signal.signal is refused off the armed thread");
    tpy_signal_enable_dispatch();
    tpy_signal_set(SIGINT, kSignalKindUser);
    struct sigaction cur{};
    ::sigaction(SIGINT, nullptr, &cur);
    check(cur.sa_handler == host_handler, "a user SIGINT kind keeps the host's handler");
    tpy::request_interrupt();
    check(!delivered() && dispatched == SIGINT,
          "a forwarded request runs the SIGINT user handler");
    tpy_signal_set(SIGINT, kSignalKindDefaultInt);
    tpy::request_interrupt();
    check(delivered(), "default_int_handler restores KeyboardInterrupt");

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
