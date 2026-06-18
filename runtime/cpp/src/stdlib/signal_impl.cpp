// Out-of-line signal helpers for TPy's asyncio graceful-shutdown support.
//
// Exists for the same reason as epoll_impl.cpp / socket_impl.cpp: the system
// headers below define macros (SIGINT, EFD_*, ...) that would collide with
// TPy module-level constants and expose non-portable struct layouts. Hiding
// them behind flat `tpy_signal_*` helpers keeps the headers out of every
// TPy-generated TU.
//
// We intentionally do NOT #include signal_h.hpp here (mirrors the sibling
// impls): four short signatures kept in sync by hand.

#include <cerrno>
#include <cstdint>

#include <csignal>
#include <sys/eventfd.h>
#include <unistd.h>

namespace {

// Async-signal-safe shutdown state. `g_flag` and `g_evfd` are touched from the
// signal handler, so both are `volatile sig_atomic_t` (the only type the C and
// POSIX standards permit a handler to read/write on a shared object).
volatile sig_atomic_t g_flag = 0;
volatile sig_atomic_t g_evfd = -1;
bool g_installed = false;
struct sigaction g_old_int{};

// The handler does only async-signal-safe work: set the flag and write to the
// eventfd. write() is on the POSIX async-signal-safe list; eventfd's 8-byte
// counter semantics make the write atomic and non-blocking (EFD_NONBLOCK). A
// full counter (EAGAIN) is harmless -- a wakeup is already pending.
void on_shutdown_signal(int /*sig*/) {
    g_flag = 1;
    int fd = g_evfd;
    if (fd >= 0) {
        std::uint64_t one = 1;
        ssize_t r = ::write(fd, &one, sizeof(one));
        (void)r;
    }
}

}  // namespace

extern "C" {

int tpy_signal_install_shutdown() {
    if (g_installed) {
        return g_evfd;
    }
    int fd = ::eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
    if (fd < 0) {
        return -1;
    }
    g_evfd = fd;
    g_flag = 0;
    struct sigaction sa{};
    sa.sa_handler = on_shutdown_signal;
    ::sigemptyset(&sa.sa_mask);
    // No SA_RESTART: a signal should interrupt a blocking syscall. The reactor's
    // epoll_wait retries EINTR internally and then sees the eventfd ready.
    sa.sa_flags = 0;
    // Only SIGINT, matching CPython's asyncio.run (which turns Ctrl-C into a
    // KeyboardInterrupt). SIGTERM keeps its default disposition (terminate),
    // also matching CPython; graceful SIGTERM shutdown is a deliberate future
    // enhancement, not parity behavior.
    if (::sigaction(SIGINT, &sa, &g_old_int) != 0) {
        ::close(fd);
        g_evfd = -1;
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
    int fd = g_evfd;
    if (fd >= 0) {
        ::close(fd);
        g_evfd = -1;
    }
    g_flag = 0;
    g_installed = false;
}

int tpy_signal_consume() {
    if (g_flag == 0) {
        return 0;
    }
    g_flag = 0;
    int fd = g_evfd;
    if (fd >= 0) {
        // Drain the counter (one read resets it). Only EINTR is retried;
        // EAGAIN (counter already 0) just means nothing to drain and must NOT
        // be retried -- the fd is non-blocking, so EAGAIN would busy-loop. A
        // signal landing between the flag clear above and this read leaves its
        // byte for the next reactor wakeup, which finds g_flag clear and re-
        // polls -- one harmless spurious wakeup, never a lost interrupt.
        std::uint64_t buf;
        ssize_t r;
        do {
            r = ::read(fd, &buf, sizeof(buf));
        } while (r < 0 && errno == EINTR);
    }
    return 1;
}

int tpy_signal_raise(int sig) {
    return ::raise(sig);
}

}  // extern "C"
