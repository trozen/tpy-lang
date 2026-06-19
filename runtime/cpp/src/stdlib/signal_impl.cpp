// Out-of-line signal helpers for TPy's asyncio graceful-shutdown support.
//
// Exists for the same reason as epoll_impl.cpp / socket_impl.cpp: the system
// headers below define macros (SIGINT, EFD_*, ...) that would collide with
// TPy module-level constants and expose non-portable struct layouts. Hiding
// them behind flat `tpy_signal_*` helpers keeps the headers out of every
// TPy-generated TU.
//
// The wake fd is an eventfd on Linux; macOS / *BSD have neither eventfd nor
// pipe2, so there the self-pipe trick stands in (a plain pipe with CLOEXEC +
// NONBLOCK set by hand). Either way the SIGINT handler writes a byte and the
// run loop drains the read end -- the asyncio reactor only sees the fd.
//
// We intentionally do NOT #include signal_h.hpp here (mirrors the sibling
// impls): four short signatures kept in sync by hand.

#include <cerrno>
#include <cstdint>

#include <csignal>
#include <unistd.h>

#if defined(__linux__)
#include <sys/eventfd.h>
#else
#include <fcntl.h>
#endif

namespace {

// Async-signal-safe shutdown state. `g_flag` and the wake fds are touched
// from the signal handler, so they are `volatile sig_atomic_t` (the only
// type the C and POSIX standards permit a handler to read/write on a shared
// object). `g_wake_rd` is the fd the reactor registers/drains; `g_wake_wr`
// is the handler's write target. On Linux both are the one eventfd; on macOS
// they are the two ends of a pipe.
volatile sig_atomic_t g_flag = 0;
volatile sig_atomic_t g_wake_rd = -1;
volatile sig_atomic_t g_wake_wr = -1;
bool g_installed = false;
struct sigaction g_old_int{};

// The handler does only async-signal-safe work: set the flag and write to the
// wake fd. write() is on the POSIX async-signal-safe list; the 8-byte payload
// is the eventfd counter increment and is harmless on a pipe (one readable
// byte is all the drain needs). A full eventfd / pipe (EAGAIN) is fine -- a
// wakeup is already pending.
void on_shutdown_signal(int /*sig*/) {
    g_flag = 1;
    int fd = g_wake_wr;
    if (fd >= 0) {
        std::uint64_t one = 1;
        ssize_t r = ::write(fd, &one, sizeof(one));
        (void)r;
    }
}

// Create the wake fd(s); store them in the globals and return the read end
// (the fd the reactor registers), or -1 on failure.
int create_wake_fds() {
#if defined(__linux__)
    int fd = ::eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
    if (fd < 0) {
        return -1;
    }
    g_wake_rd = fd;
    g_wake_wr = fd;
    return fd;
#else
    int fds[2];
    if (::pipe(fds) != 0) {
        return -1;
    }
    for (int i = 0; i < 2; ++i) {
        int fl = ::fcntl(fds[i], F_GETFL, 0);
        if (fl >= 0) {
            ::fcntl(fds[i], F_SETFL, fl | O_NONBLOCK);
        }
        int fdfl = ::fcntl(fds[i], F_GETFD, 0);
        if (fdfl >= 0) {
            ::fcntl(fds[i], F_SETFD, fdfl | FD_CLOEXEC);
        }
    }
    g_wake_rd = fds[0];
    g_wake_wr = fds[1];
    return fds[0];
#endif
}

void close_wake_fds() {
    int rd = g_wake_rd;
    int wr = g_wake_wr;
    if (rd >= 0) {
        ::close(rd);
    }
    if (wr >= 0 && wr != rd) {
        ::close(wr);
    }
    g_wake_rd = -1;
    g_wake_wr = -1;
}

}  // namespace

extern "C" {

int tpy_signal_install_shutdown() {
    if (g_installed) {
        return g_wake_rd;
    }
    int fd = create_wake_fds();
    if (fd < 0) {
        return -1;
    }
    g_flag = 0;
    struct sigaction sa{};
    sa.sa_handler = on_shutdown_signal;
    // Unqualified: sigemptyset is a function-like macro on macOS/BSD, so `::`
    // would be a syntax error; plain lookup finds the libc function on Linux.
    sigemptyset(&sa.sa_mask);
    // No SA_RESTART: a signal should interrupt a blocking syscall. The reactor's
    // epoll_wait / kevent retries EINTR internally and then sees the fd ready.
    sa.sa_flags = 0;
    // Only SIGINT, matching CPython's asyncio.run (which turns Ctrl-C into a
    // KeyboardInterrupt). SIGTERM keeps its default disposition (terminate),
    // also matching CPython; graceful SIGTERM shutdown is a deliberate future
    // enhancement, not parity behavior.
    if (::sigaction(SIGINT, &sa, &g_old_int) != 0) {
        close_wake_fds();
        return -1;
    }
    g_installed = true;
    return fd;
}

void tpy_signal_restore() {
    if (!g_installed) {
        return;
    }
    ::sigaction(SIGINT, &g_old_int, nullptr);
    close_wake_fds();
    g_flag = 0;
    g_installed = false;
}

int tpy_signal_consume() {
    if (g_flag == 0) {
        return 0;
    }
    g_flag = 0;
    int fd = g_wake_rd;
    if (fd >= 0) {
        // Drain fully: one read resets an eventfd counter, but a self-pipe
        // can hold several bytes, and a level-triggered reactor (kqueue
        // EVFILT_READ) would re-fire and busy-loop on the leftover. Loop
        // until the fd is empty -- EINTR is retried, EAGAIN / 0 ends the
        // drain (never retried, or a non-blocking fd would spin). A signal
        // landing between the flag clear above and this read leaves its byte
        // for the next reactor wakeup, which finds g_flag clear and re-polls
        // -- one harmless spurious wakeup, never a lost interrupt.
        std::uint64_t buf;
        ssize_t r;
        do {
            r = ::read(fd, &buf, sizeof(buf));
        } while (r > 0 || (r < 0 && errno == EINTR));
    }
    return 1;
}

int tpy_signal_raise(int sig) {
    return ::raise(sig);
}

// Signal numbers read from <signal.h> rather than hardcoded facade-side: the
// SIGINT/SIGTERM macros are in scope in every TPy-generated TU on macOS, so a
// plain `int32_t SIGINT` constant there would be mangled by the macro. Exposed
// as extern globals (read via `native_global`), same as socket_impl's tpy_const_*.
// Non-const so the type matches the `extern int32_t` decl native_global emits.
std::int32_t tpy_const_sigint = SIGINT;
std::int32_t tpy_const_sigterm = SIGTERM;

}  // extern "C"
