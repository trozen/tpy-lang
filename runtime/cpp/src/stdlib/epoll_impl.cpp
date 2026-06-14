// Out-of-line helpers for TPy's asyncio epoll reactor.
//
// Exists for the same reason as socket_impl.cpp: `<sys/epoll.h>` defines
// the `EPOLL*` macros (which would collide with TPy module-level constants
// of the same name) and `struct epoll_event` (a packed union whose ABI is
// arch-specific). Hiding epoll behind these three flat helpers keeps the
// system header out of every TPy-generated TU and pins the kernel ABI to
// the one place that includes the real header.
//
// We intentionally do NOT `#include` epoll_h.hpp here (mirrors
// socket_impl.cpp): the facade's bare `extern "C"` decls would only risk
// drift against the bodies below, and there are just three short
// signatures to keep in sync by hand.

#include <cerrno>
#include <cstdint>

#include <sys/epoll.h>

extern "C" {

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

}  // extern "C"
