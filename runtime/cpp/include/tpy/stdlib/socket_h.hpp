#pragma once
// Hand-written POSIX-sockets facade for TPy bindings.
//
// We deliberately do NOT `#include <sys/socket.h>`, `<netinet/in.h>`,
// `<netdb.h>`, etc. here. Those headers `#define` a large set of macros
// (`AF_INET`, `SOCK_STREAM`, `SO_REUSEADDR`, ...) that would collide with
// TPy module-level constants of the same name: the preprocessor would
// expand `AF_INET` inside a generated `inline constexpr int32_t AF_INET
// = 2;` declaration, producing a syntax error. By manually mirroring the
// libc symbols + the one struct we need, the system headers never enter
// any TPy-generated translation unit.
//
// All names match upstream POSIX exactly and live at global scope (same
// as the real headers), so generated TPy code that references
// `sockaddr_in` or calls `::socket` resolves directly.
//
// sockaddr_in's layout is POSIX-stable (16 bytes, defined field order)
// across Linux, *BSD, macOS; safe to mirror. struct addrinfo is NOT
// stable (ai_addr and ai_canonname are swapped on BSD/macOS), which is
// why we provide `tpy_resolve_ipv4` below -- the helper hides addrinfo
// entirely behind a flat C signature.
//
// Linker connects our extern "C" declarations to the actual libc
// implementations; no vendoring, no third-party lib.

#include <cstddef>
#include <cstdint>

// On macOS, libc's `<sys/_endian.h>` is pulled in transitively by libc++'s
// base headers (`<cstdint>`, `<string>`, ...) and `#define`s the byte-order
// helpers as preprocessor macros expanding to inline asm. The macros then
// fire inside our `extern "C"` decls below and inside any generated code
// that names them (`::htons(x)` becomes `::((uint16_t)(...))`, a syntax
// error). Real linker symbols for these helpers exist on every supported
// platform (Linux glibc/musl, macOS libSystem), so undef'ing the macros
// here lets us keep the upstream names end-to-end and bind directly.
#undef htons
#undef ntohs
#undef htonl
#undef ntohl

extern "C" {

// ---- sockaddr_in (POSIX-stable 16-byte layout) ----
// Field widths chosen to match struct sockaddr_in on any POSIX system:
//   sa_family_t is 16-bit unsigned;
//   in_port_t is 16-bit unsigned (network order);
//   in_addr.s_addr is 32-bit unsigned (network order);
//   sin_zero is 8 bytes of padding (required for cast-compat with sockaddr).
struct sockaddr_in {
    std::uint16_t sin_family;
    std::uint16_t sin_port;
    std::uint32_t sin_addr;
    std::uint8_t  sin_zero[8];
};

}  // extern "C" (close for static_asserts, reopen below)

// We `reinterpret_cast<sockaddr*>(&our_sockaddr_in)` at every kernel-boundary
// call, so the layout must match the system's struct exactly. Any toolchain
// that inserts padding or reorders fields must fail at compile time, not at
// runtime where it would silently corrupt addresses.
static_assert(sizeof(sockaddr_in) == 16,
              "sockaddr_in must be exactly 16 bytes to match POSIX layout");
static_assert(offsetof(sockaddr_in, sin_family) == 0,
              "sin_family must be at offset 0");
static_assert(offsetof(sockaddr_in, sin_port) == 2,
              "sin_port must be at offset 2");
static_assert(offsetof(sockaddr_in, sin_addr) == 4,
              "sin_addr must be at offset 4");
static_assert(offsetof(sockaddr_in, sin_zero) == 8,
              "sin_zero must be at offset 8");

extern "C" {

// ---- Raw libc bindings ----
// sockaddr arguments are declared `void*` to avoid forward-declaring
// `struct sockaddr` (unused name in TPy land). Pointer ABI is identical.
// `socklen_t` is `unsigned int` on Linux (glibc + musl), FreeBSD, and
// macOS -- stable enough to hardcode. `ssize_t` is `long` on every 64-bit
// *nix target TPy runs on.

int socket(int domain, int type, int protocol);
int bind(int sockfd, const void* addr, unsigned int addrlen);
int connect(int sockfd, const void* addr, unsigned int addrlen);
int listen(int sockfd, int backlog);
int accept(int sockfd, void* addr, unsigned int* addrlen);
int close(int fd);
int shutdown(int sockfd, int how);

long send(int sockfd, const void* buf, unsigned long len, int flags);
long recv(int sockfd, void* buf, unsigned long len, int flags);

int setsockopt(int sockfd, int level, int optname,
               const void* optval, unsigned int optlen);
int getsockopt(int sockfd, int level, int optname,
               void* optval, unsigned int* optlen);
int getsockname(int sockfd, void* addr, unsigned int* addrlen);
int getpeername(int sockfd, void* addr, unsigned int* addrlen);
int socketpair(int domain, int type, int protocol, int sv[2]);

std::uint16_t htons(std::uint16_t hostshort);
std::uint16_t ntohs(std::uint16_t netshort);
// `inet_pton` and `inet_ntop` -- we use uint8_t* instead of char* for the
// IP-text buffers so TPy bindings can use Ptr[uint8] naturally (matches
// the re module's convention for string-like byte buffers). uint8_t* and
// char* have identical ABI; libc's real decls use char*, linker resolves.
int            inet_pton(int af, const std::uint8_t* src, void* dst);
std::uint8_t* inet_ntop(int af, const void* src, std::uint8_t* dst,
                         unsigned int size);

// strerror is intentionally NOT declared here: libc's `char* strerror(int)`
// is transitively pulled in via `<cstring>` (from tpy.hpp's PCH chain),
// and declaring a different const-qualification here conflicts. TPy
// binding references `strerror` directly against the libc decl.

// ---- Runtime helpers (implemented in runtime/cpp/src/stdlib/socket_impl.cpp) ----

// Resolve a hostname to a single IPv4 address via getaddrinfo().
// `host` + `host_len` are a non-null-terminated byte span (matches TPy's
// str = std::string_view convention); the helper copies into a local
// std::string before calling getaddrinfo. Writes 4 bytes of the first
// A-record address to `out` in network byte order on success. Returns
// 0 on success, -1 on failure (caller checks tpy_last_resolve_error()
// for a human-readable message).
//
// Rationale for not exposing getaddrinfo directly: struct addrinfo's
// field order differs between Linux (ai_addr before ai_canonname) and
// BSD/macOS (swapped), so we cannot declare it portably at @native level.
// This helper keeps the struct walking on the C++ side where <netdb.h>
// is available.
int tpy_resolve_ipv4(const std::uint8_t* host, std::uint64_t host_len,
                     std::uint8_t out[4]);

// Returns `errno` at the point of call. Indirection needed because the
// errno symbol resolves differently per platform (__errno_location on
// Linux glibc, __error on macOS); the TPy binding shouldn't care.
int tpy_errno();

// Returns a human-readable description of the most recent failure from
// tpy_resolve_ipv4. Pointer is valid until the next tpy_resolve_ipv4
// call on the same thread. Used by TPy-side error reporting to turn
// getaddrinfo errors (EAI_*) into messages via gai_strerror without
// exposing the error-code namespace.
const char* tpy_last_resolve_error();

// The matching EAI_* code for the most recent tpy_resolve_ipv4 failure
// (same thread-local lifetime as the message). Surfaced as
// socket.gaierror's `.errno`, mirroring CPython.
int tpy_last_resolve_code();

// Toggle O_NONBLOCK on `fd` via fcntl (nonblocking != 0 sets it, 0 clears
// it). Hidden behind a helper because <fcntl.h> defines the F_* / O_*
// macros we keep out of TPy-generated TUs. Returns 0 on success, -1 on
// error (caller reads tpy_errno()). Backs socket.socket.setblocking, the
// prerequisite for using a socket with the asyncio reactor.
int tpy_set_nonblocking(int fd, int nonblocking);

// Set SO_RCVTIMEO + SO_SNDTIMEO on a blocking socket from `seconds`
// (<= 0 disables). Hidden behind a helper because struct timeval's member
// types are not portable enough to mirror as a TPy @native struct and
// <sys/time.h> would drag macros into TPy TUs. Backs socket.socket.settimeout
// for recv/send; a timed-out op returns EAGAIN, mapped to TimeoutError facade
// side. Returns 0 on success, -1 on error (caller reads tpy_errno()).
int tpy_set_timeout(int fd, double seconds);

// connect() with a wall-clock timeout (SO_*TIMEO does not cover connect):
// non-blocking connect + poll(POLLOUT, seconds) + SO_ERROR, restoring the
// fd's prior O_NONBLOCK state before returning. Returns 0 on success, -2 on
// timeout, -1 on any other error (errno set; caller reads tpy_errno()).
int tpy_connect_timeout(int fd, const void* addr, unsigned int addrlen,
                        double seconds);

}  // extern "C"
