// Out-of-line helpers for TPy's `socket` stdlib module.
//
// Exists because:
//   * struct addrinfo's field order is not portable across Linux and
//     BSD/macOS (ai_addr and ai_canonname are swapped). Declaring it at
//     @native(binding="C") level would only work on one platform family.
//     tpy_resolve_ipv4 walks the list here and hands back a flat 4-byte
//     IPv4 address.
//   * `errno` and gai_strerror need `<errno.h>` / `<netdb.h>`, whose
//     macros we keep out of every TPy-generated TU by hiding them
//     behind these helpers.
//
// Everything else in the socket module binds libc directly via @native
// against the forward decls in runtime/cpp/include/tpy/stdlib/socket_h.hpp.
//
// We intentionally do NOT `#include` socket_h.hpp here: its libc forward
// decls (using `void*` sockaddr pointers, no `noexcept`) conflict with
// the real libc decls the system headers bring in. ABI consistency for
// the three `tpy_*` symbols is maintained manually -- trivial with three
// short signatures, and the only alternative is duplicating two parallel
// header variants.

#include <cerrno>
#include <cstdint>
#include <cstring>
#include <string>

#include <fcntl.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netdb.h>

namespace {

// Thread-local so two threads running concurrent resolves don't stomp
// each other's error messages. The buffer is a fixed size because
// gai_strerror / strerror messages are short; we copy to decouple the
// returned pointer from libc's internal buffer reuse.
thread_local char g_resolve_err[256] = {0};

void set_resolve_error(const char* msg) {
    if (msg == nullptr) {
        g_resolve_err[0] = '\0';
        return;
    }
    std::strncpy(g_resolve_err, msg, sizeof(g_resolve_err) - 1);
    g_resolve_err[sizeof(g_resolve_err) - 1] = '\0';
}

}  // namespace

extern "C" {

int tpy_resolve_ipv4(const std::uint8_t* host, std::uint64_t host_len,
                     std::uint8_t out[4]) {
    // getaddrinfo requires a null-terminated C string. TPy `str` is
    // std::string_view -- not guaranteed terminated -- so copy into a
    // local std::string. reinterpret_cast<const char*> because TPy
    // binding types string bytes as uint8_t* but std::string takes char*;
    // the underlying bytes are identical.
    std::string host_s(reinterpret_cast<const char*>(host), host_len);

    addrinfo hints{};
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;

    addrinfo* res = nullptr;
    int rc = getaddrinfo(host_s.c_str(), nullptr, &hints, &res);
    if (rc != 0) {
        set_resolve_error(gai_strerror(rc));
        return -1;
    }
    if (res == nullptr) {
        // getaddrinfo returned 0 but no results -- shouldn't happen,
        // but guard anyway.
        set_resolve_error("no addresses returned");
        return -1;
    }
    if (res->ai_addr == nullptr) {
        // Defensive: with AF_INET + SOCK_STREAM hints every returned
        // entry should carry a sockaddr_in in ai_addr. A broken or
        // custom resolver library could in theory break this
        // invariant; fail cleanly rather than dereference null.
        freeaddrinfo(res);
        set_resolve_error("getaddrinfo returned entry with null ai_addr");
        return -1;
    }
    // Copy the first A-record's 4 bytes. `sin_addr.s_addr` is already in
    // network byte order, which is what the caller wants for sockaddr_in.
    auto* sin = reinterpret_cast<sockaddr_in*>(res->ai_addr);
    std::memcpy(out, &sin->sin_addr, 4);
    freeaddrinfo(res);
    set_resolve_error(nullptr);
    return 0;
}

int tpy_errno() {
    return errno;
}

const char* tpy_last_resolve_error() {
    return g_resolve_err;
}

int tpy_set_nonblocking(int fd, int nonblocking) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0) {
        return -1;
    }
    int updated = nonblocking ? (flags | O_NONBLOCK) : (flags & ~O_NONBLOCK);
    if (fcntl(fd, F_SETFL, updated) < 0) {
        return -1;
    }
    return 0;
}

// Platform-correct socket / errno constant values, sourced from the system
// headers here so the public `socket` facade need not hardcode Linux values:
// SOL_SOCKET (1 vs BSD 0xffff), SO_* and AF_INET6, and EAGAIN / EINPROGRESS
// all differ on macOS/BSD. Exposed as extern globals (read facade-side via
// `native_global`) for the same reason as the helpers above -- keep
// <sys/socket.h> / <errno.h> out of every TPy-generated TU. Values that are
// identical across Linux and macOS/BSD (AF_INET, SOCK_*, IPPROTO_*,
// TCP_NODELAY, SHUT_*) stay as literals facade-side. Non-const so the type
// matches the `extern int32_t` the native_global decl emits.
std::int32_t tpy_const_sol_socket = SOL_SOCKET;
std::int32_t tpy_const_so_reuseaddr = SO_REUSEADDR;
std::int32_t tpy_const_so_error = SO_ERROR;
std::int32_t tpy_const_so_keepalive = SO_KEEPALIVE;
std::int32_t tpy_const_af_inet6 = AF_INET6;
std::int32_t tpy_const_eagain = EAGAIN;
std::int32_t tpy_const_einprogress = EINPROGRESS;

}  // extern "C"
