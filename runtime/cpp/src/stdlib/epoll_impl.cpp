// Out-of-line helpers for TPy's asyncio I/O reactor.
//
// Exists for the same reason as socket_impl.cpp: the system headers define
// macros (the `EPOLL*` / `EV*` families) that would collide with TPy
// module-level constants of the same name, and packed structs whose ABI is
// arch-specific. Hiding the backend behind three flat helpers keeps the
// system header out of every TPy-generated TU and pins the kernel ABI to
// the one place that includes the real header.
//
// Two backends sit behind the same flat ABI: epoll on Linux, kqueue on
// macOS / *BSD. The asyncio reactor (lib/tpy/asyncio/) only ever sees the
// `tpy_epoll_*` functions plus the EPOLLIN/EPOLLOUT interest bits, so it
// stays backend-agnostic. The kqueue branch maps those bits onto kqueue
// filters and emulates epoll's level-triggered, caller-disarmed semantics.
//
// We intentionally do NOT `#include` epoll_h.hpp here (mirrors
// socket_impl.cpp): the facade's bare `extern "C"` decls would only risk
// drift against the bodies below, and there are just three short
// signatures to keep in sync by hand.

#include <cerrno>
#include <cstdint>

#if defined(__linux__)
#include <sys/epoll.h>
#else
#include <fcntl.h>
#include <sys/event.h>
#endif

extern "C" {

#if defined(__linux__)

int tpy_epoll_create() {
    // EPOLL_CLOEXEC so the epoll fd is not inherited across exec (matches
    // CPython's selectors and avoids fd leaks into spawned subprocesses).
    return ::epoll_create1(EPOLL_CLOEXEC);
}

int tpy_epoll_ctl(int epfd, int op, int fd, std::uint32_t events) {
    struct epoll_event ev{};
    ev.events = events;
    ev.data.fd = fd;
    return ::epoll_ctl(epfd, op, fd, &ev);
}

int tpy_epoll_wait(int epfd, int* out_fds, std::uint32_t* out_events,
                   int maxevents, int timeout_ms) {
    if (maxevents <= 0) {
        return 0;
    }
    // Bounded on the caller side (the reactor's drain batch); a small
    // fixed cap keeps this off the heap on the hot path.
    constexpr int kMaxBatch = 64;
    if (maxevents > kMaxBatch) {
        maxevents = kMaxBatch;
    }
    struct epoll_event evs[kMaxBatch];
    int n;
    do {
        n = ::epoll_wait(epfd, evs, maxevents, timeout_ms);
    } while (n < 0 && errno == EINTR);
    for (int i = 0; i < n; ++i) {
        out_fds[i] = evs[i].data.fd;
        out_events[i] = evs[i].events;
    }
    return n;
}

#else  // kqueue backend (macOS / *BSD)

namespace {
// Mirror of the EPOLLIN / EPOLLOUT interest bits the reactor passes
// (asyncio/__init__.py); kept in sync by hand like the EPOLL_CTL_* ops.
constexpr std::uint32_t kEpollIn = 0x001;
constexpr std::uint32_t kEpollOut = 0x004;
// Mirror of _EPOLL_CTL_DEL in asyncio/_executor.py; ADD and MOD both arm.
constexpr int kCtlDel = 2;
}  // namespace

int tpy_epoll_create() {
    int kq = ::kqueue();
    if (kq < 0) {
        return -1;
    }
    // kqueue() has no CLOEXEC variant, so set it by hand (parity with
    // epoll_create1(EPOLL_CLOEXEC): don't leak the fd across exec).
    int flags = ::fcntl(kq, F_GETFD, 0);
    if (flags >= 0) {
        ::fcntl(kq, F_SETFD, flags | FD_CLOEXEC);
    }
    return kq;
}

int tpy_epoll_ctl(int epfd, int op, int fd, std::uint32_t events) {
    // epoll multiplexes both directions on one fd via a combined mask;
    // kqueue uses one filter per direction. ADD/MOD arm exactly the
    // directions named in `events` and disarm the others, so a MOD that
    // switches read<->write matches epoll's replace semantics; DEL disarms
    // both. EV_RECEIPT applies the changes and reports per-change status
    // without blocking; ENOENT on a disarm (filter not armed) is expected.
    bool want_read = (op != kCtlDel) && (events & kEpollIn) != 0;
    bool want_write = (op != kCtlDel) && (events & kEpollOut) != 0;
    struct kevent changes[2];
    EV_SET(&changes[0], fd, EVFILT_READ,
           (want_read ? (EV_ADD | EV_ENABLE) : EV_DELETE) | EV_RECEIPT,
           0, 0, nullptr);
    EV_SET(&changes[1], fd, EVFILT_WRITE,
           (want_write ? (EV_ADD | EV_ENABLE) : EV_DELETE) | EV_RECEIPT,
           0, 0, nullptr);
    struct kevent results[2];
    int r;
    do {
        r = ::kevent(epfd, changes, 2, results, 2, nullptr);
    } while (r < 0 && errno == EINTR);
    if (r < 0) {
        return -1;
    }
    // With EV_RECEIPT every change yields a result whose `data` is its errno
    // (0 == applied). A disarm of an unarmed filter reports ENOENT, benign.
    for (int i = 0; i < r; ++i) {
        if ((results[i].flags & EV_ERROR) && results[i].data != 0 &&
            results[i].data != ENOENT) {
            errno = static_cast<int>(results[i].data);
            return -1;
        }
    }
    return 0;
}

int tpy_epoll_wait(int epfd, int* out_fds, std::uint32_t* out_events,
                   int maxevents, int timeout_ms) {
    if (maxevents <= 0) {
        return 0;
    }
    constexpr int kMaxBatch = 64;
    if (maxevents > kMaxBatch) {
        maxevents = kMaxBatch;
    }
    struct timespec ts;
    struct timespec* tsp;
    if (timeout_ms < 0) {
        tsp = nullptr;  // block indefinitely (epoll timeout -1)
    } else {
        ts.tv_sec = timeout_ms / 1000;
        ts.tv_nsec = static_cast<long>(timeout_ms % 1000) * 1000000L;
        tsp = &ts;
    }
    struct kevent evs[kMaxBatch];
    int n;
    do {
        n = ::kevent(epfd, nullptr, 0, evs, maxevents, tsp);
    } while (n < 0 && errno == EINTR);
    if (n < 0) {
        return -1;
    }
    for (int i = 0; i < n; ++i) {
        out_fds[i] = static_cast<int>(evs[i].ident);
        std::uint32_t ev = 0;
        if (evs[i].filter == EVFILT_READ) {
            ev |= kEpollIn;
        } else if (evs[i].filter == EVFILT_WRITE) {
            ev |= kEpollOut;
        }
        // Surface error/EOF as both directions ready so the parked
        // awaitable's retry observes the error (mirrors epoll's
        // EPOLLERR/EPOLLHUP, which the reactor treats as "wake and let the
        // syscall report the failure").
        if (evs[i].flags & (EV_ERROR | EV_EOF)) {
            ev |= (kEpollIn | kEpollOut);
        }
        out_events[i] = ev;
    }
    return n;
}

#endif

}  // extern "C"
