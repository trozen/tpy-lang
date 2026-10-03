// The process-wide SIGINT layer: Ctrl-C -> KeyboardInterrupt for synchronous
// code, and the wake source for asyncio.run's graceful shutdown.
//
// Out of line for the same reason as epoll_impl.cpp / socket_impl.cpp: the
// system headers below define macros (EFD_*, POLL*, ...) and non-portable
// struct layouts that must stay out of TPy-generated TUs. The generated side
// sees only interrupt.hpp (shared state, no system headers) and the flat
// `tpy_interrupt_*` / `tpy_signal_*` entry points.
//
// Model (see also interrupt.hpp):
//   * Arming installs an async-signal-safe SIGINT handler WITH SA_RESTART, so
//     no syscall anywhere (iostreams, TLS, DNS, fsync, ...) sees EINTR from it.
//     The handler sets `pending` and writes a wake fd; nothing else.
//   * The armed thread (the main thread for a standalone program) is the
//     interrupt target. Its blocking waits (time.sleep, input(), blocking
//     socket I/O) also wait on the wake fd, so a Ctrl-C wakes them race-free:
//     a signal landing just before the wait has already written its byte.
//   * While asyncio.run is active on the target thread, asyncio owns delivery:
//     synchronous check points never consume the interrupt, the executor's
//     reactor watches the same wake fd instead.
//   * While a tpy::DeferSignals scope is open on the target thread, nothing
//     consumes the interrupt and waits ignore the wake fd; asyncio.run there
//     declines ownership.
//   * A second Ctrl-C while the first is still undelivered (a CPU loop with no
//     check point) terminates the process the way an unhandled SIGINT does.
//
// The wake fd is an eventfd on Linux; macOS / *BSD have neither eventfd nor
// pipe2, so there the self-pipe trick stands in (a plain pipe with CLOEXEC +
// NONBLOCK set by hand).
//
// With TPY_NO_SIGNALS the layer is compiled out (see interrupt.hpp): what is
// left is the plain fd wait, asyncio hooks that decline, raise_signal and the
// exit-by-SIGINT of an uncaught KeyboardInterrupt.
//
// We intentionally do NOT #include signal_h.hpp here (mirrors the sibling
// impls): its short signatures are kept in sync by hand.

#if defined(__linux__) && !defined(_GNU_SOURCE)
#define _GNU_SOURCE 1  // ppoll
#endif

#include <atomic>
#include <cerrno>
#include <climits>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <string>

#include <csignal>
#include <poll.h>
#include <pthread.h>
#include <unistd.h>

#if defined(__linux__)
#include <sys/eventfd.h>
#else
#include <fcntl.h>
#endif

#include "tpy/interrupt.hpp"

// Header check points and the SIGINT handler both touch `pending`; a lock-free
// atomic is what makes that signal-safe.
static_assert(std::atomic<int>::is_always_lock_free);

#if !defined(__GLIBC__) && !defined(__APPLE__) && !defined(__FreeBSD__)
// musl (and gnulib-style libcs) export the buffered-byte count directly. Weak,
// so a libc without it links and input() falls back to a plain blocking read.
extern "C" std::size_t __freadahead(FILE*) __attribute__((weak));
#endif

namespace {

namespace idet = tpy::interrupt_detail;

constexpr long kNsPerSec = 1000000000L;
// Longer waits are clamped (~31 years) so the timespec arithmetic below
// cannot overflow.
constexpr double kMaxWaitSeconds = 1e9;

void set_sigint_default() noexcept {
    struct sigaction dfl{};
    dfl.sa_handler = SIG_DFL;
    // Unqualified: sigemptyset is a function-like macro on macOS/BSD, so `::`
    // would be a syntax error; plain lookup finds the libc function on Linux.
    sigemptyset(&dfl.sa_mask);
    ::sigaction(SIGINT, &dfl, nullptr);
}

// --- monotonic deadlines -------------------------------------------------

timespec monotonic_now() {
    timespec ts{};
    ::clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts;
}

timespec deadline_after(double seconds) {
    if (seconds > kMaxWaitSeconds) {
        seconds = kMaxWaitSeconds;
    }
    timespec now = monotonic_now();
    auto whole = static_cast<time_t>(seconds);
    auto frac = static_cast<long>((seconds - static_cast<double>(whole))
                                  * static_cast<double>(kNsPerSec));
    timespec d{};
    d.tv_sec = now.tv_sec + whole;
    d.tv_nsec = now.tv_nsec + frac;
    if (d.tv_nsec >= kNsPerSec) {
        d.tv_sec += 1;
        d.tv_nsec -= kNsPerSec;
    }
    return d;
}

// Time left until `deadline`; false once it has passed.
bool remaining(const timespec& deadline, timespec& out) {
    timespec now = monotonic_now();
    out.tv_sec = deadline.tv_sec - now.tv_sec;
    out.tv_nsec = deadline.tv_nsec - now.tv_nsec;
    if (out.tv_nsec < 0) {
        out.tv_sec -= 1;
        out.tv_nsec += kNsPerSec;
    }
    return out.tv_sec > 0 || (out.tv_sec == 0 && out.tv_nsec > 0);
}

#if !defined(__linux__)
// poll()'s millisecond timeout for `rem`, rounded up so poll() never returns
// before the deadline has passed (the callers' loops re-check it).
int ceil_ms(const timespec& rem) {
    if (rem.tv_sec >= INT_MAX / 1000 - 1) {
        return INT_MAX;
    }
    return static_cast<int>(rem.tv_sec * 1000 + (rem.tv_nsec + 999999) / 1000000);
}
#endif

// poll() `fds` until one is ready or `deadline` (nullptr: none) passes.
// Returns poll()'s count, 0 on timeout, -1 on error (errno set).
// Polls at least once, also past the deadline, so an fd that is already
// ready (a queued connection under a tiny timeout) is not reported as a
// timeout -- as CPython does.
int poll_until(pollfd* fds, nfds_t n, const timespec* deadline) {
    for (bool first = true;; first = false) {
        timespec rem{};
        const timespec* tp = nullptr;
        if (deadline != nullptr) {
            if (!remaining(*deadline, rem)) {
                if (!first) {
                    return 0;
                }
                rem = timespec{};
            }
            tp = &rem;
        }
#if defined(__linux__)
        int r = ::ppoll(fds, n, tp, nullptr);
#else
        int r = ::poll(fds, n, tp != nullptr ? ceil_ms(rem) : -1);
#endif
        if (r < 0 && errno == EINTR) {
            // Some handler ran (poll is never restarted); a Ctrl-C shows up
            // on the wake fd at the next poll.
            continue;
        }
        if (r == 0) {
            // The deadline check above, not poll()'s rounding, decides.
            continue;
        }
        return r;
    }
}

#ifndef TPY_NO_SIGNALS

// The handler's write target and the fd waits / the reactor watch. One eventfd
// on Linux; the two ends of a pipe elsewhere. Created on the first arm and
// never closed: a handler running on another thread (or a host's handler
// calling tpy_request_interrupt()) may still hold the old number, and writing
// into a closed fd that open() has since reused would corrupt a stranger's
// file. A disarm keeps them for the next arm.
std::atomic<int> g_wake_rd{-1};
std::atomic<int> g_wake_wr{-1};

// Installation state, touched only by arm / disarm (never by the handler).
bool g_installed = false;
// False when a host keeps its own SIGINT handler and forwards through
// tpy_request_interrupt(); then there is no disposition to restore.
bool g_handler_installed = false;
struct sigaction g_old_int{};
// process_startup() ran, so this standalone program decided SIGINT ownership
// once; asyncio.run then never installs a handler of its own.
bool g_process_owned = false;
// Armed by asyncio.run's fallback for the duration of the run (--no-main /
// extension builds, where no process-level layer exists).
bool g_async_installed = false;
// Written before `idet::ops` is published (release) and read after loading it
// (acquire), so every reader sees the armed value.
pthread_t g_target{};
// Non-zero while asyncio.run on the target thread owns delivery.
std::atomic<int> g_async_owner{0};

void post_interrupt() noexcept {
    idet::pending.store(1, std::memory_order_release);
    int fd = g_wake_wr.load(std::memory_order_relaxed);
    if (fd >= 0) {
        // write() is async-signal-safe. The 8-byte payload is the eventfd
        // counter increment and harmless on a pipe; a full pipe (EAGAIN)
        // already holds a pending wakeup.
        std::uint64_t one = 1;
        ssize_t r = ::write(fd, &one, sizeof(one));
        (void)r;
    }
}

void on_sigint(int /*sig*/) {
    int saved_errno = errno;
    if (idet::pending.load(std::memory_order_acquire) != 0) {
        // The previous Ctrl-C was never delivered (no interruptible operation
        // ran since): terminate like an unhandled SIGINT. SIGINT is blocked
        // while this handler runs, so the re-raise lands as it returns.
        set_sigint_default();
        ::raise(SIGINT);
    } else {
        post_interrupt();
    }
    errno = saved_errno;
}

// Create the wake fd(s) unless an earlier arm did; false on failure.
bool ensure_wake_fds() {
    if (g_wake_rd.load() >= 0) {
        return true;
    }
#if defined(__linux__)
    int fd = ::eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
    if (fd < 0) {
        return false;
    }
    g_wake_rd.store(fd);
    g_wake_wr.store(fd);
    return true;
#else
    int fds[2];
    if (::pipe(fds) != 0) {
        return false;
    }
    for (int i = 0; i < 2; ++i) {
        int fl = ::fcntl(fds[i], F_GETFL, 0);
        int fdfl = ::fcntl(fds[i], F_GETFD, 0);
        // A blocking end would let the handler's write (or a drain) hang.
        if (fl < 0 || fdfl < 0 || ::fcntl(fds[i], F_SETFL, fl | O_NONBLOCK) < 0
                || ::fcntl(fds[i], F_SETFD, fdfl | FD_CLOEXEC) < 0) {
            ::close(fds[0]);
            ::close(fds[1]);
            return false;
        }
    }
    g_wake_rd.store(fds[0]);
    g_wake_wr.store(fds[1]);
    return true;
#endif
}

// Take the pending interrupt, if any. The fd is drained BEFORE the flag is
// cleared: a Ctrl-C landing in between leaves the flag set and its byte
// readable, so the next wait still wakes. Clearing first could strand a set
// flag with no byte -- a wait would then sleep through that Ctrl-C.
int consume() {
    int fd = g_wake_rd.load();
    if (fd >= 0) {
        // Drain fully: a self-pipe can hold several bytes and a level-
        // triggered reactor would re-fire on the leftover. EAGAIN / 0 ends it.
        std::uint64_t buf;
        ssize_t r;
        do {
            r = ::read(fd, &buf, sizeof(buf));
        } while (r > 0 || (r < 0 && errno == EINTR));
    }
    return idet::pending.exchange(0, std::memory_order_acq_rel) != 0 ? 1 : 0;
}

bool on_target_thread() {
    return idet::ops.load(std::memory_order_acquire) != nullptr
        && ::pthread_equal(::pthread_self(), g_target) != 0;
}

// Synchronous code on this thread may be interrupted right now. Every check
// point and every wait's choice to watch the wake fd goes through this, so an
// open tpy::DeferSignals scope leaves `pending` untouched until it closes.
bool deliverable_here() {
    return on_target_thread() && g_async_owner.load() == 0
        && idet::defer_depth == 0;
}

// Sleep until `deadline` without waking early and without restarting the
// whole interval when a signal interrupts the sleep.
void sleep_until(const timespec& deadline) {
#if defined(__linux__)
    while (::clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &deadline, nullptr)
           == EINTR) {
    }
#else
    timespec rem{};
    while (remaining(deadline, rem)) {
        ::nanosleep(&rem, nullptr);
    }
#endif
}

// --- the Ops table -------------------------------------------------------

int take_op() {
    return deliverable_here() ? consume() : 0;
}

int sleep_op(double seconds) {
    if (!(seconds > 0.0)) {
        return 0;
    }
    timespec deadline = deadline_after(seconds);
    if (!deliverable_here()) {
        sleep_until(deadline);
        return 0;
    }
    pollfd pfd{};
    pfd.fd = g_wake_rd.load();
    pfd.events = POLLIN;
    for (;;) {
        timespec rem{};
        if (!remaining(deadline, rem)) {
            return 0;
        }
#if defined(__linux__)
        int r = ::ppoll(&pfd, 1, &rem, nullptr);
#else
        // poll() only counts milliseconds: sleep the whole-ms part
        // interruptibly, then the sub-ms tail precisely.
        int ms = rem.tv_sec >= INT_MAX / 1000 - 1
            ? INT_MAX : static_cast<int>(rem.tv_sec * 1000 + rem.tv_nsec / 1000000);
        if (ms == 0) {
            sleep_until(deadline);
            return 0;
        }
        int r = ::poll(&pfd, 1, ms);
#endif
        if (r > 0 && consume() != 0) {
            return idet::kInterrupted;
        }
    }
}

// Bytes stdio already buffered for `f`, or -1 when this libc offers no way to
// tell. The same per-libc knowledge gnulib's freadahead() encodes.
long stdio_buffered(FILE* f) {
#if defined(__GLIBC__)
    // _IO_IN_BACKUP lives in glibc's private libio.h: set while ungetc()'s
    // backup area is active, with the main buffer's rest parked in _IO_save_*.
    constexpr int kIoInBackup = 0x100;
    long n = static_cast<long>(f->_IO_read_end - f->_IO_read_ptr);
    if ((f->_flags & kIoInBackup) != 0) {
        n += static_cast<long>(f->_IO_save_end - f->_IO_save_base);
    }
    return n;
#elif defined(__APPLE__) || defined(__FreeBSD__)
    // _r counts the bytes left in the current buffer; while ungetc() bytes are
    // pending it counts those and _ur holds the main buffer's count.
    long n = f->_r > 0 ? f->_r : 0;
    if (f->_ub._base != nullptr && f->_ur > 0) {
        n += f->_ur;
    }
    return n;
#else
    if (__freadahead != nullptr) {
        return static_cast<long>(__freadahead(f));
    }
    return -1;
#endif
}

// input() on the armed layer. Reads stdin through stdio -- the buffer std::cin
// also reads through under sync_with_stdio, so neither side loses bytes -- and
// blocks only in a poll() that also watches the wake fd, entered only once
// stdio's buffer is empty since poll() cannot see bytes stdio read ahead.
// A Ctrl-C discards the part of the line read so far, as CPython's input()
// does; the next input() starts with what is typed after it.
int read_line_op(std::string& out) {
    FILE* in = stdin;
    const bool interruptible = deliverable_here();
    const int wake = g_wake_rd.load();
    // A Ctrl-C already pending wins over a line stdio has buffered, which
    // then stays for the next input(), as in CPython.
    if (interruptible && idet::pending.load(std::memory_order_relaxed) != 0
        && consume() != 0) {
        return idet::kInterrupted;
    }
    flockfile(in);
    std::string line;
    int result;
    for (;;) {
        // feof: stdio's EOF is sticky (a Ctrl-D on a tty), so the next getc
        // answers without reading while a poll() would wait for more input.
        if (interruptible && std::feof(in) == 0 && stdio_buffered(in) == 0) {
            pollfd fds[2]{};
            fds[0].fd = fileno(in);
            fds[0].events = POLLIN;
            fds[1].fd = wake;
            fds[1].events = POLLIN;
            int r = poll_until(fds, 2, nullptr);
            if (r > 0 && fds[1].revents != 0 && consume() != 0) {
                result = idet::kInterrupted;
                break;
            }
            if (r > 0 && fds[0].revents == 0) {
                continue;  // a stale wake byte
            }
            // Readable, hung up, or an error: the read below reports which.
        }
        int c = getc_unlocked(in);
        if (c == EOF) {
            // A partial last line is returned; the next call raises EOFError.
            result = line.empty() ? 0 : 1;
            break;
        }
        if (c == '\n') {
            result = 1;
            break;
        }
        line.push_back(static_cast<char>(c));
    }
    funlockfile(in);
    if (result == 1) {
        out = std::move(line);
    }
    return result;
}

int deliverable_op() {
    return deliverable_here() ? 1 : 0;
}

const idet::Ops kOps{&take_op, &sleep_op, &read_line_op, &deliverable_op};

enum class Handler {
    kIfDefault,  // leave a non-default disposition (an inherited SIG_IGN) alone
    kAlways,
    kNone,       // the host keeps its handler and calls tpy_request_interrupt()
};

int install_handler() {
    struct sigaction sa{};
    sa.sa_handler = on_sigint;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_RESTART;
    if (::sigaction(SIGINT, &sa, &g_old_int) != 0) {
        return -1;
    }
    g_handler_installed = true;
    return 0;
}

// Arm the layer with the calling thread as the interrupt target. Returns 0 on
// success, 1 when kIfDefault found SIGINT not at SIG_DFL, -1 on error. On an
// armed layer the target stays and a handler can only be added: kAlways
// installs it when an earlier arm left it out, anything else changes nothing.
int arm(Handler handler) {
    if (g_installed) {
        if (handler == Handler::kAlways && !g_handler_installed) {
            return install_handler();
        }
        return 0;
    }
    if (handler == Handler::kIfDefault) {
        struct sigaction cur{};
        if (::sigaction(SIGINT, nullptr, &cur) != 0) {
            return -1;
        }
        if ((cur.sa_flags & SA_SIGINFO) != 0 || cur.sa_handler != SIG_DFL) {
            return 1;
        }
    }
    if (!ensure_wake_fds()) {
        return -1;
    }
    g_target = ::pthread_self();
    // Drops a stale request (and its wake byte) left from an earlier arming.
    consume();
    if (handler != Handler::kNone && install_handler() != 0) {
        return -1;
    }
    g_installed = true;
    idet::ops.store(&kOps, std::memory_order_release);
    return 0;
}

void disarm() {
    if (!g_installed) {
        return;
    }
    idet::ops.store(nullptr, std::memory_order_release);
    if (g_handler_installed) {
        ::sigaction(SIGINT, &g_old_int, nullptr);
        g_handler_installed = false;
    }
    idet::pending.store(0);
    g_installed = false;
}

// The wake fd a wait on this thread also watches, or -1 (poll() skips it).
int wait_wake_fd() {
    return deliverable_here() ? g_wake_rd.load() : -1;
}

#else  // TPY_NO_SIGNALS

// The layer is compiled out: no handler, no wake fd, nothing ever pending. A
// wait watches its own fd only.
int wait_wake_fd() {
    return -1;
}

int consume() {
    return 0;
}

#endif  // TPY_NO_SIGNALS

}  // namespace

extern "C" {

#ifndef TPY_NO_SIGNALS

int tpy_interrupt_process_startup() {
    g_process_owned = true;
    return arm(Handler::kIfDefault);
}

int tpy_interrupt_install(int with_handler) {
    if (arm(with_handler != 0 ? Handler::kAlways : Handler::kNone) != 0) {
        return -1;
    }
    // The host asked for the layer to stay: an asyncio.run that armed it for
    // its own duration must not remove it on the way out.
    g_async_installed = false;
    return 0;
}

void tpy_request_interrupt() {
    // Called from a host's signal handler, which must not clobber the errno
    // of the code it interrupted.
    int saved_errno = errno;
    post_interrupt();
    errno = saved_errno;
}

// asyncio.run takes over delivery for its duration. Returns the wake fd to
// watch in the reactor, or -1 when this run handles no SIGINT: off the
// interrupt target thread, or in a standalone program that left an inherited
// SIG_IGN alone, or inside a tpy::DeferSignals scope. With no process-level
// layer (--no-main / extension builds) the run arms one itself and
// tpy_interrupt_async_end() removes it again.
int tpy_interrupt_async_begin() {
    // tpy_interrupt_async_consume() is gated on ownership, not on
    // deliverable_here(): a run that took ownership inside a deferred __del__
    // would consume the Ctrl-C and raise KeyboardInterrupt out of the
    // noexcept destructor. Declined, the run leaves it pending for the first
    // check point after the scope.
    if (idet::defer_depth > 0) {
        return -1;
    }
    if (!g_installed) {
        if (g_process_owned || arm(Handler::kAlways) != 0) {
            return -1;
        }
        g_async_installed = true;
    } else if (!on_target_thread()) {
        return -1;
    }
    g_async_owner.store(1);
    return g_wake_rd.load();
}

void tpy_interrupt_async_end() {
    g_async_owner.store(0);
    if (g_async_installed) {
        g_async_installed = false;
        disarm();
    }
}

// 1 if a Ctrl-C arrived since the last call (consuming it), else 0.
int tpy_interrupt_async_consume() {
    return g_async_owner.load() != 0 ? consume() : 0;
}

#else  // TPY_NO_SIGNALS

// What interrupt.hpp's link guard refers to. Only this build of the file
// defines it, so a TU compiled with TPY_NO_SIGNALS (its DeferSignals scopes
// are empty) cannot link against a layer that could still be armed.
extern const int tpy_signals_compiled_out = 1;

// asyncio.run handles no SIGINT.
int tpy_interrupt_async_begin() {
    return -1;
}

void tpy_interrupt_async_end() {}

int tpy_interrupt_async_consume() {
    return 0;
}

#endif  // TPY_NO_SIGNALS

[[noreturn]] void tpy_interrupt_exit_by_sigint() {
    set_sigint_default();
    sigset_t set;
    sigemptyset(&set);
    sigaddset(&set, SIGINT);
    ::pthread_sigmask(SIG_UNBLOCK, &set, nullptr);
    ::raise(SIGINT);
    // Only reached if SIGINT could not terminate the process.
    std::_Exit(128 + SIGINT);
}

int tpy_interrupt_wait(int fd, int want_write, double timeout) {
    const int wake = wait_wake_fd();
    timespec deadline{};
    const bool has_deadline = timeout >= 0.0;
    if (has_deadline) {
        deadline = deadline_after(timeout);
    }
    for (;;) {
        pollfd fds[2]{};
        fds[0].fd = fd;
        fds[0].events = static_cast<short>(want_write != 0 ? POLLOUT : POLLIN);
        fds[1].fd = wake;
        fds[1].events = POLLIN;
        int r = poll_until(fds, 2, has_deadline ? &deadline : nullptr);
        if (r < 0) {
            return idet::kWaitError;
        }
        if (r == 0) {
            return idet::kTimedOut;
        }
        if (fds[1].revents != 0 && consume() != 0) {
            return idet::kInterrupted;
        }
        if (fds[0].revents != 0) {
            return idet::kWaitReady;
        }
    }
}

// Sent to the whole process rather than raise()d on the calling thread: a
// worker thread keeps SIGINT blocked, so raise() there would never reach the
// interrupt target.
int tpy_signal_raise(int sig) {
    return ::kill(::getpid(), sig);
}

// Signal numbers read from <signal.h> rather than hardcoded facade-side: the
// SIGINT/SIGTERM/SIGKILL macros are in scope in every TPy-generated TU on
// macOS, so a plain `int32_t SIGINT` constant there would be mangled by the
// macro. Exposed
// as extern globals (read via `native_global`), same as socket_impl's tpy_const_*.
// Non-const so the type matches the `extern int32_t` decl native_global emits.
std::int32_t tpy_const_sigint = SIGINT;
std::int32_t tpy_const_sigterm = SIGTERM;
std::int32_t tpy_const_sigkill = SIGKILL;

}  // extern "C"
