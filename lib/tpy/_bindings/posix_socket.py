# tpy: native_module
# tpy: cpp_namespace("tpystd::_bindings::posix_socket")
# tpy: include("<tpy/stdlib/socket_h.hpp>")
"""Raw POSIX-sockets C bindings.

Thin @native layer over libc's `socket`, `bind`, `connect`, ..., plus
three out-of-line helpers from runtime/cpp/src/stdlib/socket_impl.cpp
(DNS resolution, errno access, last-resolve-error). One declaration per
primitive, matching the C ABI 1:1 -- no Python semantics.

Consumers (the public `socket` facade) build Socket / SocketError classes
on top of these raw handles, using __del__ for RAII over file descriptors.

Naming:
  * `SockaddrIn` -- @native(binding="C") struct aliased to `sockaddr_in`.
    Fields match POSIX layout (16 bytes total); safe across Linux, *BSD,
    macOS. IPv6 (`sockaddr_in6`) is a separate struct, deferred.
  * All function names mirror their libc counterparts.
  * Wire values (AF_INET etc.) live in the public `socket` facade, not
    here -- posix_socket.py is declaration-only.

The `# tpy: native_module` directive means no .cpp/.hpp is generated for
this module itself; its @native bindings resolve at link time to symbols
in libc (for the socket/bind/... set) and in socket_impl.cpp (for the
tpy_* set).
"""

from tpy import int32, uint8, uint16, uint32, uint64, int64, Ptr, readonly
from tpy.extern import native


# ---------- sockaddr_in (POSIX-stable 16-byte layout) ----------
# Mirror of the `struct sockaddr_in` declaration in socket_h.hpp. TPy-side
# field access codegens to normal C struct field access; no unsafe_load
# is needed. Constants for sin_family / sin_port / sin_addr are built by
# the caller using htons() + tpy_resolve_ipv4() + the AF_* values in the
# public `socket` facade.
@native("sockaddr_in", binding="C")
class SockaddrIn:
    sin_family: uint16
    sin_port: uint16
    sin_addr: uint32


# ---------- Socket lifecycle ----------

# All @native names below use a leading `::` to bind to libc's global
# symbols (`::socket`, `::bind`, ...). Without the `::` prefix, the rename
# would be treated as relative to this module's `cpp_namespace` and emit
# `::tpystd::_bindings::posix_socket::socket` -- no such symbol. Absolute
# renames (containing `::` anywhere in the string) opt out of the
# namespace prefix, which is what we want for libc bindings.

@native("::socket")
def socket(domain: int32, type_: int32, protocol: int32) -> int32: ...

@native("::close")
def close(fd: int32) -> int32: ...

@native("::shutdown")
def shutdown(fd: int32, how: int32) -> int32: ...


# ---------- Address binding + connection setup ----------

@native("::bind")
def bind(fd: int32, addr: Ptr[SockaddrIn], addrlen: uint32) -> int32: ...

@native("::connect")
def connect(fd: int32, addr: Ptr[SockaddrIn], addrlen: uint32) -> int32: ...

@native("::listen")
def listen(fd: int32, backlog: int32) -> int32: ...

@native("::accept")
def accept(fd: int32, addr: Ptr[SockaddrIn], addrlen: Ptr[uint32]) -> int32: ...


# ---------- Data transfer ----------
# send/recv return ssize_t (= long on 64-bit *nix). Negative means error;
# 0 from recv means peer closed (orderly shutdown).

@native("::send")
def send(fd: int32, buf: Ptr[readonly[uint8]], len_: uint64, flags: int32) -> int64: ...

@native("::recv")
def recv(fd: int32, buf: Ptr[uint8], len_: uint64, flags: int32) -> int64: ...


# ---------- Options + introspection ----------
# setsockopt's `optval` is polymorphic (int* for boolean-style opts,
# struct timeval* for timeouts, etc.). Phase 1 only exposes int-valued
# options; broader shapes arrive with SO_RCVTIMEO / SO_LINGER support.

@native("::setsockopt")
def setsockopt(fd: int32, level: int32, optname: int32,
               optval: Ptr[readonly[int32]], optlen: uint32) -> int32: ...

# optlen is in/out (socklen_t*): caller seeds it with the buffer size, libc
# writes back the bytes filled. Int-valued options only, like setsockopt.
@native("::getsockopt")
def getsockopt(fd: int32, level: int32, optname: int32,
               optval: Ptr[int32], optlen: Ptr[uint32]) -> int32: ...

@native("::getsockname")
def getsockname(fd: int32, addr: Ptr[SockaddrIn], addrlen: Ptr[uint32]) -> int32: ...

@native("::getpeername")
def getpeername(fd: int32, addr: Ptr[SockaddrIn], addrlen: Ptr[uint32]) -> int32: ...

@native("::socketpair")
def socketpair(domain: int32, type_: int32, protocol: int32,
               sv: Ptr[int32]) -> int32: ...


# ---------- Byte-order + address conversion ----------

@native("::htons")
def htons(hostshort: uint16) -> uint16: ...

@native("::ntohs")
def ntohs(netshort: uint16) -> uint16: ...

# inet_pton writes the binary form (4 bytes for AF_INET) into `dst`.
# Returns 1 on success, 0 for bad format, -1 for unsupported family.
@native("::inet_pton")
def inet_pton(af: int32, src: Ptr[readonly[uint8]], dst: Ptr[uint8]) -> int32: ...

# inet_ntop writes the text form into `dst` and returns a pointer into it
# on success, NULL on error. We accept the non-null-terminated return
# semantics implicitly by copying via dst's span.
@native("::inet_ntop")
def inet_ntop(af: int32, src: Ptr[readonly[uint8]],
              dst: Ptr[uint8], size: uint32) -> Ptr[uint8]: ...


# ---------- TPy runtime helpers (socket_impl.cpp) ----------

# Resolve `host` (first `host_len` bytes, not required null-terminated) to
# a single IPv4 address via getaddrinfo. Writes 4 network-byte-order bytes
# to `out`. Returns 0 on success, -1 on failure; caller reads
# tpy_last_resolve_error for a human message on failure.
@native("::tpy_resolve_ipv4")
def tpy_resolve_ipv4(host: Ptr[readonly[uint8]], host_len: uint64,
                     out: Ptr[uint8]) -> int32: ...

# errno at the point of call. Indirection for platform portability
# (glibc uses __errno_location, macOS uses __error).
@native("::tpy_errno")
def tpy_errno() -> int32: ...

# Last DNS-resolution error message (thread-local, C string, valid until
# the next tpy_resolve_ipv4 call on this thread). readonly because the
# C signature is `const char*` -- callers must not mutate the buffer.
@native("::tpy_last_resolve_error")
def tpy_last_resolve_error() -> Ptr[readonly[uint8]]: ...

# The matching EAI_* code (same thread-local lifetime as the message);
# becomes socket.gaierror's `.errno`, like CPython.
@native("::tpy_last_resolve_code")
def tpy_last_resolve_code() -> int32: ...

# Toggle O_NONBLOCK on `fd` (nonblocking != 0 sets it). Backs
# socket.socket.setblocking; required before using a socket with the
# asyncio reactor. Returns 0 on success, -1 on error (read tpy_errno).
@native("::tpy_set_nonblocking")
def tpy_set_nonblocking(fd: int32, nonblocking: int32) -> int32: ...


# Set SO_RCVTIMEO + SO_SNDTIMEO from `seconds` (<= 0 disables). Backs
# socket.socket.settimeout for recv/send. Returns 0 on success, -1 on error.
@native("::tpy_set_timeout")
def tpy_set_timeout(fd: int32, seconds: float) -> int32: ...


# connect() with a wall-clock timeout (SO_*TIMEO does not cover connect).
# Returns 0 on success, -2 on timeout, -1 on any other error (read tpy_errno).
@native("::tpy_connect_timeout")
def tpy_connect_timeout(fd: int32, addr: Ptr[SockaddrIn], addrlen: uint32,
                        seconds: float) -> int32: ...


# Platform-correct constant values (SOL_SOCKET / SO_* / AF_INET6 / EAGAIN /
# EINPROGRESS differ on macOS/BSD) live as `extern int32_t tpy_const_*`
# globals in socket_impl.cpp; the public `socket` facade reads them directly
# via `native_global`, so no binding declaration is needed here.


# ---------- Raw libc strerror ----------
# Returns a human-readable description of `errnum`. Null-terminated C
# string; caller wraps it in a TPy `str` via the usual ptr+length walk.
# No `# tpy: include` needed for this -- <cstring> is transitively pulled
# in via tpy.hpp, which declares `char* strerror(int)`. uint8_t* return
# matches TPy's byte-buffer convention; ABI is identical. readonly because
# libc's strerror buffer is thread-local / shared and must not be written.
@native("::strerror")
def strerror(errnum: int32) -> Ptr[readonly[uint8]]: ...
