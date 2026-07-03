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

from tpy import Int32, UInt8, UInt16, UInt32, UInt64, Int64, Ptr, readonly
from tpy.extern import native


# ---------- sockaddr_in (POSIX-stable 16-byte layout) ----------
# Mirror of the `struct sockaddr_in` declaration in socket_h.hpp. TPy-side
# field access codegens to normal C struct field access; no unsafe_load
# is needed. Constants for sin_family / sin_port / sin_addr are built by
# the caller using htons() + tpy_resolve_ipv4() + the AF_* values in the
# public `socket` facade.
@native("sockaddr_in", binding="C")
class SockaddrIn:
    sin_family: UInt16
    sin_port: UInt16
    sin_addr: UInt32


# ---------- Socket lifecycle ----------

# All @native names below use a leading `::` to bind to libc's global
# symbols (`::socket`, `::bind`, ...). Without the `::` prefix, the rename
# would be treated as relative to this module's `cpp_namespace` and emit
# `::tpystd::_bindings::posix_socket::socket` -- no such symbol. Absolute
# renames (containing `::` anywhere in the string) opt out of the
# namespace prefix, which is what we want for libc bindings.

@native("::socket")
def socket(domain: Int32, type_: Int32, protocol: Int32) -> Int32: ...

@native("::close")
def close(fd: Int32) -> Int32: ...

@native("::shutdown")
def shutdown(fd: Int32, how: Int32) -> Int32: ...


# ---------- Address binding + connection setup ----------

@native("::bind")
def bind(fd: Int32, addr: Ptr[SockaddrIn], addrlen: UInt32) -> Int32: ...

@native("::connect")
def connect(fd: Int32, addr: Ptr[SockaddrIn], addrlen: UInt32) -> Int32: ...

@native("::listen")
def listen(fd: Int32, backlog: Int32) -> Int32: ...

@native("::accept")
def accept(fd: Int32, addr: Ptr[SockaddrIn], addrlen: Ptr[UInt32]) -> Int32: ...


# ---------- Data transfer ----------
# send/recv return ssize_t (= long on 64-bit *nix). Negative means error;
# 0 from recv means peer closed (orderly shutdown).

@native("::send")
def send(fd: Int32, buf: Ptr[readonly[UInt8]], len_: UInt64, flags: Int32) -> Int64: ...

@native("::recv")
def recv(fd: Int32, buf: Ptr[UInt8], len_: UInt64, flags: Int32) -> Int64: ...


# ---------- Options + introspection ----------
# setsockopt's `optval` is polymorphic (int* for boolean-style opts,
# struct timeval* for timeouts, etc.). Phase 1 only exposes int-valued
# options; broader shapes arrive with SO_RCVTIMEO / SO_LINGER support.

@native("::setsockopt")
def setsockopt(fd: Int32, level: Int32, optname: Int32,
               optval: Ptr[readonly[Int32]], optlen: UInt32) -> Int32: ...

# optlen is in/out (socklen_t*): caller seeds it with the buffer size, libc
# writes back the bytes filled. Int-valued options only, like setsockopt.
@native("::getsockopt")
def getsockopt(fd: Int32, level: Int32, optname: Int32,
               optval: Ptr[Int32], optlen: Ptr[UInt32]) -> Int32: ...

@native("::getsockname")
def getsockname(fd: Int32, addr: Ptr[SockaddrIn], addrlen: Ptr[UInt32]) -> Int32: ...

@native("::getpeername")
def getpeername(fd: Int32, addr: Ptr[SockaddrIn], addrlen: Ptr[UInt32]) -> Int32: ...

@native("::socketpair")
def socketpair(domain: Int32, type_: Int32, protocol: Int32,
               sv: Ptr[Int32]) -> Int32: ...


# ---------- Byte-order + address conversion ----------

@native("::htons")
def htons(hostshort: UInt16) -> UInt16: ...

@native("::ntohs")
def ntohs(netshort: UInt16) -> UInt16: ...

# inet_pton writes the binary form (4 bytes for AF_INET) into `dst`.
# Returns 1 on success, 0 for bad format, -1 for unsupported family.
@native("::inet_pton")
def inet_pton(af: Int32, src: Ptr[readonly[UInt8]], dst: Ptr[UInt8]) -> Int32: ...

# inet_ntop writes the text form into `dst` and returns a pointer into it
# on success, NULL on error. We accept the non-null-terminated return
# semantics implicitly by copying via dst's span.
@native("::inet_ntop")
def inet_ntop(af: Int32, src: Ptr[readonly[UInt8]],
              dst: Ptr[UInt8], size: UInt32) -> Ptr[UInt8]: ...


# ---------- TPy runtime helpers (socket_impl.cpp) ----------

# Resolve `host` (first `host_len` bytes, not required null-terminated) to
# a single IPv4 address via getaddrinfo. Writes 4 network-byte-order bytes
# to `out`. Returns 0 on success, -1 on failure; caller reads
# tpy_last_resolve_error for a human message on failure.
@native("::tpy_resolve_ipv4")
def tpy_resolve_ipv4(host: Ptr[readonly[UInt8]], host_len: UInt64,
                     out: Ptr[UInt8]) -> Int32: ...

# errno at the point of call. Indirection for platform portability
# (glibc uses __errno_location, macOS uses __error).
@native("::tpy_errno")
def tpy_errno() -> Int32: ...

# Last DNS-resolution error message (thread-local, C string, valid until
# the next tpy_resolve_ipv4 call on this thread). readonly because the
# C signature is `const char*` -- callers must not mutate the buffer.
@native("::tpy_last_resolve_error")
def tpy_last_resolve_error() -> Ptr[readonly[UInt8]]: ...

# The matching EAI_* code (same thread-local lifetime as the message);
# becomes socket.gaierror's `.errno`, like CPython.
@native("::tpy_last_resolve_code")
def tpy_last_resolve_code() -> Int32: ...

# Toggle O_NONBLOCK on `fd` (nonblocking != 0 sets it). Backs
# socket.socket.setblocking; required before using a socket with the
# asyncio reactor. Returns 0 on success, -1 on error (read tpy_errno).
@native("::tpy_set_nonblocking")
def tpy_set_nonblocking(fd: Int32, nonblocking: Int32) -> Int32: ...


# Set SO_RCVTIMEO + SO_SNDTIMEO from `seconds` (<= 0 disables). Backs
# socket.socket.settimeout for recv/send. Returns 0 on success, -1 on error.
@native("::tpy_set_timeout")
def tpy_set_timeout(fd: Int32, seconds: float) -> Int32: ...


# connect() with a wall-clock timeout (SO_*TIMEO does not cover connect).
# Returns 0 on success, -2 on timeout, -1 on any other error (read tpy_errno).
@native("::tpy_connect_timeout")
def tpy_connect_timeout(fd: Int32, addr: Ptr[SockaddrIn], addrlen: UInt32,
                        seconds: float) -> Int32: ...


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
def strerror(errnum: Int32) -> Ptr[readonly[UInt8]]: ...
