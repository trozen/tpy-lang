#pragma once
// Hand-written epoll facade for TPy bindings.
//
// Mirrors the socket_h.hpp strategy: we deliberately do NOT expose
// `struct epoll_event` (a packed union whose ABI differs by arch) or the
// `EPOLL*` macros to any TPy-generated translation unit. Instead the three
// `tpy_epoll_*` helpers below take flat scalar / pointer arguments and the
// real system header is included only by epoll_impl.cpp, where the kernel
// ABI is guaranteed correct. The EPOLL* / EPOLL_CTL_* wire values are
// hardcoded on the TPy side (the small subset the reactor uses is stable
// across the backends), the same way socket.py hardcodes AF_INET et al.
//
// One flat ABI, two backends inside epoll_impl.cpp: epoll on Linux, kqueue
// on macOS / *BSD (the kqueue branch maps the EPOLLIN/EPOLLOUT bits onto
// EVFILT_READ/WRITE and emulates epoll's level-triggered, caller-disarmed
// semantics). An io_uring backend would slot in the same way.

#include <cstdint>

extern "C" {

// Create an epoll instance via epoll_create1(0). Returns the epoll fd
// (>= 0) on success, or -1 on error (caller reads tpy_errno()).
int tpy_epoll_create();

// epoll_ctl wrapper. `op` is an EPOLL_CTL_* value (1 = ADD, 2 = DEL,
// 3 = MOD); `events` is an EPOLL* mask (ignored for DEL). The fd is stored
// in the kernel event's data so tpy_epoll_wait can hand it back. Returns 0
// on success, -1 on error.
int tpy_epoll_ctl(int epfd, int op, int fd, std::uint32_t events);

// epoll_wait wrapper. Blocks up to `timeout_ms` (-1 = forever, 0 = poll)
// for readiness on up to `maxevents` fds, writing each ready fd and its
// event mask into the caller-provided parallel arrays out_fds[i] /
// out_events[i]. Returns the number of ready fds (>= 0), or -1 on error.
// EINTR is retried internally so a signal does not surface as a spurious
// error to the run loop.
int tpy_epoll_wait(int epfd, int* out_fds, std::uint32_t* out_events,
                   int maxevents, int timeout_ms);

}  // extern "C"
