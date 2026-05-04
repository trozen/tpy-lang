# tpy: cpp_namespace("tpystd::socket")
"""POSIX-sockets module, CPython-compatible surface.

Backed by `_bindings.posix_socket` (raw @native bindings) + three out-of-line
helpers in runtime/cpp/src/stdlib/socket_impl.cpp for DNS resolution and
errno access. All Python semantics (error wrapping, address-tuple packing,
RAII of fd lifetimes) live in this file, not in C++.

Phase 1 scope:
  * IPv4 TCP client/server (blocking I/O, single connection at a time).
  * Hostname resolution via getaddrinfo (behind tpy_resolve_ipv4).
  * with-statement support (context manager).
  * Errors surface as SocketError (wraps errno + strerror).

TODO -- v2 feature follow-ups. New scope, not compiler-blocked:

  * **IPv6.** AF_INET6 = 10 is declared below but there's no SockaddrIn6
    binding yet. Needs a second @native(binding="C") struct mirroring
    sockaddr_in6 (28 bytes, POSIX-stable).

  * **getaddrinfo with multiple results.** tpy_resolve_ipv4 returns only
    the first A record; real DNS often returns several, and Happy-
    Eyeballs-style connect wants to try each. Needs a helper variant
    that returns a list of addresses + types. Also want getnameinfo for
    reverse lookup.

  * **Windows (Winsock2).** `SOCKET` is unsigned with `INVALID_SOCKET`
    sentinel (not -1), `closesocket` instead of `close`, errors via
    `WSAGetLastError` not errno, WSAStartup/WSACleanup init required,
    links `-lws2_32`. All goes in socket_impl.cpp behind `#ifdef _WIN32`;
    TPy-side API stays the same. Blocker: no Windows CI yet.

  * **macOS portability.** AF_INET6, SOL_SOCKET, SO_REUSEADDR etc.
    values differ on macOS/BSD (e.g. SOL_SOCKET is 0xffff on BSD vs 1
    on Linux). Constants below hardcode Linux values. Fix by routing
    through `tpy_socket_constants()` helpers in socket_impl.cpp, or via
    per-platform Final declarations once TPy supports those.

  * **Non-blocking I/O.** setblocking(False), settimeout(sec), EAGAIN /
    EWOULDBLOCK handling. Belongs with the Phase 2 `selectors`
    (epoll/kqueue) module -- non-blocking alone without a selector
    isn't useful.

  * **setsockopt with struct values.** SO_RCVTIMEO / SO_SNDTIMEO take
    struct timeval; SO_LINGER takes struct linger. Only int-valued opts
    supported today (via setsockopt_int). Add one @native(binding="C")
    struct + another setsockopt overload per shape.

  * **sendto / recvfrom / recv_into.** Phase 1 supports `send` / `recv`
    on an already-connected socket only. Datagram-style sendto/recvfrom
    needs an out-addr sockaddr_in parameter; recv_into needs a mutable
    buffer parameter (writing into a caller-provided bytearray rather
    than allocating a new bytes).

  * **SOCK_DGRAM.** Declared but effectively untested -- without
    sendto/recvfrom the only usable pattern is `connect` + `send`/`recv`
    on a datagram socket, rare in practice. Land with sendto/recvfrom.

  * **AF_UNIX (Unix-domain sockets).** A third @native(binding="C")
    struct (sockaddr_un, 110-byte path field). Useful for IPC.

  * **TLS / ssl module.** Phase 3 work, needs a TLS library (mbedTLS
    vendored is the current lean -- see the project roadmap).

  * **makefile().** CPython exposes a BufferedReader/Writer over a
    socket, integrating with the io module. Needs our io module to
    grow a "adopt this fd" constructor.

  * **SocketError vs OSError hierarchy.** CPython distinguishes
    ConnectionRefusedError, ConnectionResetError, BlockingIOError etc.
    as OSError subclasses keyed on errno. We raise plain SocketError
    everywhere; users can branch on the message text but not on
    isinstance of a specific subclass.

  * **gethostbyname_ex, gethostbyaddr, getservbyname.** CPython legacy
    DNS APIs; low priority.

  * **Typed address record.** A typed record (`InetAddress(host: str,
    port: Int32)`) would read better than a bare tuple. Tabled until we
    decide on the module's record shapes overall.

  * **struct hostent / addrinfo accessors.** We only expose the flat
    tpy_resolve_ipv4 helper today. A proper getaddrinfo wrapper that
    surfaces the full result set wants typed TPy records; blocked on
    the same portability concern that drove the helper approach.
"""

from __future__ import annotations
from typing import Final
from tpy import (
    Int32, Int64, UInt8, UInt16, UInt32, UInt64, Ptr, readonly, Own,
    String, take_ptr, nocopy,
)
from tpy.mem import UninitArrayStorage, UninitHeapStorage
from tpy.unsafe import (
    unsafe_cast, unsafe_ptr, unsafe_ptr_add, unsafe_load,
    unsafe_str_from_cstr, unsafe_bytes_from_buf,
)

from _bindings import posix_socket
from _bindings.posix_socket import SockaddrIn


# ---------- Wire constants ----------
# Linux glibc values (hardcoded -- see module TODO for macOS/BSD story).
# Sourced from `<sys/socket.h>`, `<netinet/in.h>`, `<netinet/tcp.h>`.

AF_INET:     Final[Int32] = 2
AF_INET6:    Final[Int32] = 10   # Linux-specific (macOS: 30). Not yet usable.
AF_UNIX:     Final[Int32] = 1    # Not yet usable (no sockaddr_un binding).

SOCK_STREAM: Final[Int32] = 1
SOCK_DGRAM:  Final[Int32] = 2

SOL_SOCKET:   Final[Int32] = 1    # Linux. BSD = 0xffff.
SO_REUSEADDR: Final[Int32] = 2
SO_KEEPALIVE: Final[Int32] = 9
SO_ERROR:     Final[Int32] = 4

IPPROTO_TCP: Final[Int32] = 6
IPPROTO_UDP: Final[Int32] = 17

TCP_NODELAY: Final[Int32] = 1

SHUT_RD:   Final[Int32] = 0
SHUT_WR:   Final[Int32] = 1
SHUT_RDWR: Final[Int32] = 2


# ---------- SocketError ----------

class SocketError(Exception):
    """Raised on any libc socket-call failure. Carries errno + strerror."""
    # Explicit __init__ + String param are compiler-gap workarounds mirroring
    # re.error; see module TODO above and BUGS.md.
    def __init__(self, message: String = "") -> None:
        super().__init__(message)


def _raise_errno(op: str) -> None:
    """Raise SocketError("<op>: <strerror(errno)>")."""
    msg = unsafe_str_from_cstr(posix_socket.strerror(posix_socket.tpy_errno()))
    raise SocketError(op + ": " + msg)


def _raise_resolve_error(host: str) -> None:
    """Raise SocketError from the last getaddrinfo gai_strerror message."""
    msg = unsafe_str_from_cstr(posix_socket.tpy_last_resolve_error())
    raise SocketError("resolve " + host + ": " + msg)


# ---------- Address helpers ----------

def gethostbyname(hostname: str) -> str:
    """Resolve a hostname to the first IPv4 dotted-quad string."""
    host_ptr: Ptr[readonly[UInt8]] = unsafe_cast(unsafe_ptr(hostname))
    out = UninitArrayStorage[UInt8, 4]()
    rc = posix_socket.tpy_resolve_ipv4(host_ptr, UInt64(len(hostname)), out.ptr())
    if rc != 0:
        _raise_resolve_error(hostname)
    return _ipv4_to_str(out.ptr())


def _ipv4_to_str(addr_bytes: Ptr[UInt8]) -> str:
    # INET_ADDRSTRLEN = 16 ("255.255.255.255\0").
    buf = UninitArrayStorage[UInt8, 16]()
    if posix_socket.inet_ntop(AF_INET, addr_bytes, buf.ptr(), 16) is None:
        _raise_errno("inet_ntop")
    return unsafe_str_from_cstr(unsafe_cast(buf.ptr()))


def _build_sockaddr_in(host: str, port: Int32) -> Own[SockaddrIn]:
    """Pack (host, port) into a sockaddr_in for bind/connect.

    Empty `host` means INADDR_ANY; otherwise tried as a dotted-quad
    (inet_pton) first, then resolved via getaddrinfo. sin_port and
    sin_addr are in network byte order; sin_family is host order.

    The 3-arg SockaddrIn(...) relies on C++ aggregate init zero-filling
    sin_zero[8] -- POSIX requires those bytes zero on kernel entry, and
    un-zeroed padding can make bind return EINVAL. Do not switch to an
    init style that skips zero-fill.
    """
    port_no = posix_socket.htons(UInt16.trunc(port))
    if len(hostname := host) == 0:
        return SockaddrIn(UInt16.trunc(AF_INET), port_no, 0)

    addr_bytes = UninitArrayStorage[UInt8, 4]()
    host_ptr: Ptr[readonly[UInt8]] = unsafe_cast(unsafe_ptr(hostname))
    rc = posix_socket.inet_pton(AF_INET, host_ptr, addr_bytes.ptr())
    if rc != 1:
        rc2 = posix_socket.tpy_resolve_ipv4(host_ptr, UInt64(len(hostname)),
                                addr_bytes.ptr())
        if rc2 != 0:
            _raise_resolve_error(hostname)

    addr_u32_ptr: Ptr[UInt32] = unsafe_cast(addr_bytes.ptr())
    return SockaddrIn(UInt16.trunc(AF_INET), port_no,
                      unsafe_load(addr_u32_ptr, 0))


# ---------- Socket class ----------

_SOCKADDR_IN_LEN: Final[UInt32] = 16


@nocopy
class Socket:
    """A POSIX socket file descriptor with RAII cleanup.

    `fd` is the underlying OS descriptor; -1 means closed. @nocopy keeps
    ownership unique so close() runs exactly once in __del__. Obtain
    Sockets via Socket(family, type) / create_connection / create_server
    / accept; move with Own[Socket].
    """

    # Field default silences a sema "not initialized before ctor body"
    # warning (the if/else below sets fd on every path, sema can't prove it).
    fd: Int32 = Int32(-1)

    def __init__(self, family: Int32, type_: Int32, proto: Int32 = Int32(0),
                 fileno: Int32 = Int32(-1)) -> None:
        """`fileno >= 0` wraps an existing fd (from accept); family/type/
        proto are ignored in that case. Otherwise a new socket is created
        and SocketError is raised on libc failure -- the constructor then
        throws and __del__ is not called."""
        if fileno >= Int32(0):
            self.fd = fileno
        else:
            new_fd = posix_socket.socket(family, type_, proto)
            if new_fd < Int32(0):
                _raise_errno("socket")
            self.fd = new_fd

    def __del__(self) -> None:
        if self.fd >= Int32(0):
            posix_socket.close(self.fd)
            self.fd = Int32(-1)

    def fileno(self) -> Int32:
        return self.fd

    def close(self) -> None:
        if self.fd >= Int32(0):
            posix_socket.close(self.fd)
            self.fd = Int32(-1)

    def shutdown(self, how: Int32) -> None:
        if posix_socket.shutdown(self.fd, how) < Int32(0):
            _raise_errno("shutdown")

    def bind(self, address: tuple[str, Int32]) -> None:
        host, port = address
        addr = _build_sockaddr_in(host, port)
        if posix_socket.bind(self.fd, take_ptr(addr), _SOCKADDR_IN_LEN) < Int32(0):
            _raise_errno("bind")

    def connect(self, address: tuple[str, Int32]) -> None:
        host, port = address
        addr = _build_sockaddr_in(host, port)
        if posix_socket.connect(self.fd, take_ptr(addr), _SOCKADDR_IN_LEN) < Int32(0):
            _raise_errno("connect")

    # Literal 128 = SOMAXCONN; named-Final-as-default rejected by sema.
    def listen(self, backlog: Int32 = Int32(128)) -> None:
        if posix_socket.listen(self.fd, backlog) < Int32(0):
            _raise_errno("listen")

    def accept(self) -> tuple[Own[Socket], tuple[str, Int32]]:
        """Block until a client connects, return `(conn, (host, port))`
        matching CPython's `socket.accept()` shape."""
        addr = SockaddrIn(0, 0, 0)
        addrlen: UInt32 = _SOCKADDR_IN_LEN
        new_fd = posix_socket.accept(self.fd, take_ptr(addr), take_ptr(addrlen))
        if new_fd < Int32(0):
            _raise_errno("accept")
        conn = Socket(Int32(0), Int32(0), Int32(0), fileno=new_fd)
        peer = (_ipv4_to_str(unsafe_cast(take_ptr(addr.sin_addr))),
                Int32.trunc(posix_socket.ntohs(addr.sin_port)))
        return (conn, peer)

    def send(self, data: bytes) -> Int32:
        """Send (some of) `data`; returns bytes actually sent. Use
        `sendall` for full-buffer delivery. Return truncated to Int32
        from libc's ssize_t -- see module TODO."""
        n = posix_socket.send(self.fd, unsafe_ptr(data), UInt64(len(data)), Int32(0))
        if n < Int64(0):
            _raise_errno("send")
        return Int32.trunc(n)

    def sendall(self, data: bytes) -> None:
        """Send every byte in `data` (loops over send)."""
        total: UInt64 = UInt64(len(data))
        sent: UInt64 = 0
        data_ptr: Ptr[readonly[UInt8]] = unsafe_ptr(data)
        while sent < total:
            chunk = posix_socket.send(self.fd,
                              unsafe_ptr_add(data_ptr, Int64.trunc(sent)),
                              total - sent, Int32(0))
            if chunk < Int64(0):
                _raise_errno("send")
            if chunk == Int64(0):
                raise SocketError("send: peer closed early")
            sent = sent + UInt64(chunk)

    def recv(self, bufsize: Int32) -> bytes:
        """Receive up to `bufsize` bytes. Empty bytes means peer closed."""
        if bufsize <= Int32(0):
            return bytes()
        buf = UninitHeapStorage[UInt8](UInt32.trunc(bufsize))
        n = posix_socket.recv(self.fd, buf.ptr(), UInt64(bufsize), Int32(0))
        if n < Int64(0):
            _raise_errno("recv")
        return unsafe_bytes_from_buf(buf.ptr(), UInt64(n))

    def setsockopt_int(self, level: Int32, optname: Int32, value: Int32) -> None:
        """Set an int-valued socket option. Struct options deferred."""
        v = value
        if posix_socket.setsockopt(self.fd, level, optname, take_ptr(v), 4) < Int32(0):
            _raise_errno("setsockopt")

    def getsockname(self) -> tuple[str, Int32]:
        addr = SockaddrIn(0, 0, 0)
        addrlen: UInt32 = _SOCKADDR_IN_LEN
        if posix_socket.getsockname(self.fd, take_ptr(addr), take_ptr(addrlen)) < Int32(0):
            _raise_errno("getsockname")
        return (_ipv4_to_str(unsafe_cast(take_ptr(addr.sin_addr))),
                Int32.trunc(posix_socket.ntohs(addr.sin_port)))

    def getpeername(self) -> tuple[str, Int32]:
        addr = SockaddrIn(0, 0, 0)
        addrlen: UInt32 = _SOCKADDR_IN_LEN
        if posix_socket.getpeername(self.fd, take_ptr(addr), take_ptr(addrlen)) < Int32(0):
            _raise_errno("getpeername")
        return (_ipv4_to_str(unsafe_cast(take_ptr(addr.sin_addr))),
                Int32.trunc(posix_socket.ntohs(addr.sin_port)))

    def __enter__(self) -> Socket:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# ---------- Module-level factories ----------


def socketpair(family: Int32 = AF_UNIX, type_: Int32 = SOCK_STREAM,
               proto: Int32 = Int32(0)) -> tuple[Own[Socket], Own[Socket]]:
    """Create a pair of connected sockets via `::socketpair`.

    Defaults match CPython: AF_UNIX + SOCK_STREAM. Useful for in-process
    full-duplex pipes; AF_UNIX is POSIX-only (no Windows support yet)."""
    sv = UninitArrayStorage[Int32, 2]()
    if posix_socket.socketpair(family, type_, proto, sv.ptr()) < Int32(0):
        _raise_errno("socketpair")
    a = Socket(Int32(0), Int32(0), Int32(0), fileno=unsafe_load(sv.ptr(), 0))
    b = Socket(Int32(0), Int32(0), Int32(0), fileno=unsafe_load(sv.ptr(), 1))
    return (a, b)


def create_connection(address: tuple[str, Int32]) -> Own[Socket]:
    """TCP client convenience: socket + connect."""
    s = Socket(AF_INET, SOCK_STREAM, Int32(0))
    s.connect(address)
    return s


def create_server(address: tuple[str, Int32],
                  backlog: Int32 = Int32(128),
                  reuse_addr: bool = True) -> Own[Socket]:
    """TCP server convenience: socket + SO_REUSEADDR + bind + listen.
    Caller loops on accept() to serve connections."""
    s = Socket(AF_INET, SOCK_STREAM, Int32(0))
    if reuse_addr:
        s.setsockopt_int(SOL_SOCKET, SO_REUSEADDR, Int32(1))
    s.bind(address)
    s.listen(backlog)
    return s
