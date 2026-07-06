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

  * **macOS portability.** The divergent wire constants (SOL_SOCKET,
    SO_*, AF_INET6) and errno values (EAGAIN, EINPROGRESS) are read from
    the system headers via `native_global` bindings to the `tpy_const_*`
    globals in socket_impl.cpp, so they are platform-correct. Remaining
    macOS gaps are the same feature follow-ups as on Linux (IPv6
    sockaddr binding, struct-valued setsockopt, etc.), not portability.

  * **Non-blocking I/O.** `setblocking(False)` is done (toggles O_NONBLOCK
    via fcntl); the EAGAIN/EWOULDBLOCK + selector handling lives in the
    asyncio epoll reactor (`get_running_loop().sock_recv`/`sock_sendall`).
    `settimeout(sec)` is also done: recv/send use SO_RCVTIMEO/SO_SNDTIMEO
    (the timeval is built in tpy_set_timeout, not a @native struct), and
    connect() uses a poll-based wait (tpy_connect_timeout). A timed-out op
    raises TimeoutError ("timed out"), matching CPython's socket.timeout.
    Not reproduced: the process-wide `setdefaulttimeout()` /
    `_GLOBAL_DEFAULT_TIMEOUT` sentinel (default is plain blocking), and
    accept() under a timeout (server-side, not needed for the client).

  * **setsockopt with struct values.** SO_RCVTIMEO / SO_SNDTIMEO are handled
    via the dedicated tpy_set_timeout helper (timeval built C-side). SO_LINGER
    (struct linger) is still int-only -- add an @native(binding="C") struct +
    another setsockopt overload when needed.

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

  * **SocketError vs OSError hierarchy.** SocketError subclasses OSError,
    so `except OSError` catches socket failures (CPython-faithful).
    `_raise_errno`/`_raise_io` raise `BlockingIOError` on
    EAGAIN/EWOULDBLOCK/EINPROGRESS (the asyncio reactor parks on it) and the
    PEP 3151 `ConnectionError` subclasses on the connection errno
    (EPIPE -> BrokenPipeError, ECONNRESET -> ConnectionResetError,
    ECONNREFUSED -> ConnectionRefusedError, ECONNABORTED ->
    ConnectionAbortedError); every other errno falls through to SocketError.
    All carry the structured `.errno` / `.strerror` OSError attributes
    (compare `.errno` against the `errno` module's constants) with the
    CPython-exact "[Errno N] strerror" message, and name-resolution
    failures raise a distinct `gaierror` whose `.errno` is the EAI_* code.

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
    take_ptr, nocopy,
)
from tpy.extern import native_global
from tpy.mem import UninitArrayStorage, UninitHeapStorage
from tpy.unsafe import (
    unsafe_cast, unsafe_ptr, unsafe_ptr_add, unsafe_load,
    unsafe_str_from_cstr, unsafe_bytes_from_buf,
)

from _bindings import posix_socket
from _bindings.posix_socket import SockaddrIn

import os
from io import FileIO, BufferedReader, DEFAULT_BUFFER_SIZE


# ---------- Wire constants ----------
# Values identical across Linux and macOS/BSD are literals; the ones that
# diverge (SOL_SOCKET, SO_*, AF_INET6) are sourced from the system headers
# via posix_socket.tpy_const_* getters so the same source builds correctly on
# either platform. Sourced from `<sys/socket.h>`, `<netinet/in.h>`,
# `<netinet/tcp.h>`.

AF_INET:     Final[Int32] = 2
AF_UNIX:     Final[Int32] = 1    # Not yet usable (no sockaddr_un binding).
# Linux 10, macOS/BSD 30. Not yet usable (no sockaddr_in6 binding).
AF_INET6:    Final[Int32] = native_global("tpy_const_af_inet6", binding="C")

SOCK_STREAM: Final[Int32] = 1
SOCK_DGRAM:  Final[Int32] = 2

# Linux 1, BSD/macOS 0xffff.
SOL_SOCKET:   Final[Int32] = native_global("tpy_const_sol_socket", binding="C")
SO_REUSEADDR: Final[Int32] = native_global("tpy_const_so_reuseaddr", binding="C")
SO_KEEPALIVE: Final[Int32] = native_global("tpy_const_so_keepalive", binding="C")
SO_ERROR:     Final[Int32] = native_global("tpy_const_so_error", binding="C")

IPPROTO_TCP: Final[Int32] = 6
IPPROTO_UDP: Final[Int32] = 17

TCP_NODELAY: Final[Int32] = 1

SHUT_RD:   Final[Int32] = 0
SHUT_WR:   Final[Int32] = 1
SHUT_RDWR: Final[Int32] = 2


# ---------- SocketError ----------

class SocketError(OSError):
    """Raised on any libc socket-call failure. The base's errno-taking ctor
    formats the CPython-exact "[Errno N] strerror" message and fills the
    structured `.errno` / `.strerror` attributes.

    Subclasses `OSError` (not plain `Exception`) to match CPython, whose
    socket module raises `OSError`: code written `except OSError` catches
    these and ports to CPython unchanged. The distinct name is kept for
    readable tracebacks and existing `except SocketError` users.
    """
    # Single (errno, strerror) __init__: user classes cannot mirror the
    # base's message-only overload (no user-class ctor overloads; BUGS.md).
    def __init__(self, err: Int32, strerror: str) -> None:
        super().__init__(err, strerror)


class gaierror(OSError):
    """Raised when name resolution (getaddrinfo) fails. `.errno` carries the
    EAI_* code (not a POSIX errno) and `.strerror` the gai_strerror message,
    like CPython's socket.gaierror. Subclasses `OSError` directly, matching
    CPython's hierarchy."""
    def __init__(self, err: Int32, strerror: str) -> None:
        super().__init__(err, strerror)


# errno values diverge across platforms (Linux EAGAIN 11 / EINPROGRESS 115;
# macOS 35 / 36), so source them from <errno.h> via native globals. EAGAIN ==
# EWOULDBLOCK on both Linux and macOS; EINPROGRESS is a non-blocking connect's
# "in progress" result.
_EAGAIN: Final[Int32] = native_global("tpy_const_eagain", binding="C")
_EINPROGRESS: Final[Int32] = native_global("tpy_const_einprogress", binding="C")
# Connection-error errno values, also platform-divergent (see socket_impl.cpp).
_EPIPE: Final[Int32] = native_global("tpy_const_epipe", binding="C")
_ECONNRESET: Final[Int32] = native_global("tpy_const_econnreset", binding="C")
_ECONNREFUSED: Final[Int32] = native_global("tpy_const_econnrefused", binding="C")
_ECONNABORTED: Final[Int32] = native_global("tpy_const_econnaborted", binding="C")


def _strerror(err: Int32) -> str:
    return unsafe_str_from_cstr(posix_socket.strerror(err))


def _maybe_raise_connection_error(err: Int32, strerr: str) -> None:
    """Raise the PEP 3151 ConnectionError subclass for a connection-related
    errno; return if `err` is none of them, so the caller falls back to the
    generic SocketError. Mirrors CPython, which raises these subclasses (all
    OSError) for the same errno on socket I/O. The errno-taking ctors fill
    `.errno` / `.strerror` and the CPython-exact "[Errno N] ..." message."""
    if err == _EPIPE:
        raise BrokenPipeError(err, strerr)
    if err == _ECONNRESET:
        raise ConnectionResetError(err, strerr)
    if err == _ECONNREFUSED:
        raise ConnectionRefusedError(err, strerr)
    if err == _ECONNABORTED:
        raise ConnectionAbortedError(err, strerr)


def _raise_errno() -> None:
    """Raise the errno-keyed OSError subclass: BlockingIOError on
    EAGAIN/EWOULDBLOCK/EINPROGRESS (so the asyncio reactor can park on fd
    readiness), a ConnectionError subclass on a connection errno, else
    SocketError."""
    err = posix_socket.tpy_errno()
    msg = _strerror(err)
    if err == _EAGAIN or err == _EINPROGRESS:
        raise BlockingIOError(err, msg)
    _maybe_raise_connection_error(err, msg)
    raise SocketError(err, msg)


def _raise_resolve_error() -> None:
    """Raise gaierror from the last getaddrinfo failure: the EAI_* code in
    `.errno`, the gai_strerror message in `.strerror` (CPython-shaped)."""
    code = posix_socket.tpy_last_resolve_code()
    msg = unsafe_str_from_cstr(posix_socket.tpy_last_resolve_error())
    raise gaierror(code, msg)


# ---------- Address helpers ----------

def gethostbyname(hostname: str) -> str:
    """Resolve a hostname to the first IPv4 dotted-quad string."""
    host_ptr: Ptr[readonly[UInt8]] = unsafe_cast(unsafe_ptr(hostname))
    out = UninitArrayStorage[UInt8, 4]()
    rc = posix_socket.tpy_resolve_ipv4(host_ptr, UInt64(len(hostname)), out.ptr())
    if rc != 0:
        _raise_resolve_error()
    return _ipv4_to_str(out.ptr())


def _ipv4_to_str(addr_bytes: Ptr[UInt8]) -> str:
    # INET_ADDRSTRLEN = 16 ("255.255.255.255\0").
    buf = UninitArrayStorage[UInt8, 16]()
    if posix_socket.inet_ntop(AF_INET, addr_bytes, buf.ptr(), 16) is None:
        _raise_errno()
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
            _raise_resolve_error()

    addr_u32_ptr: Ptr[UInt32] = unsafe_cast(addr_bytes.ptr())
    return SockaddrIn(UInt16.trunc(AF_INET), port_no,
                      unsafe_load(addr_u32_ptr, 0))


# ---------- socket class ----------
# Class name is lowercase `socket` to match CPython's `socket.socket`
# exactly, so user code (and the asyncio reactor's sock_* helpers) ports to
# CPython unchanged and the test cpy phase can run on CPython's real socket.

_SOCKADDR_IN_LEN: Final[UInt32] = 16


@nocopy
class socket:
    """A POSIX socket file descriptor with RAII cleanup.

    `fd` is the underlying OS descriptor; -1 means closed. @nocopy keeps
    ownership unique so close() runs exactly once in __del__. Obtain
    sockets via socket(family, type) / create_connection / create_server
    / accept; move with Own[socket].
    """

    # Field default silences a sema "not initialized before ctor body"
    # warning (the if/else below sets fd on every path, sema can't prove it).
    fd: Int32 = Int32(-1)

    # Socket mode, mirroring CPython's three states: -1.0 = blocking (None
    # timeout), 0.0 = non-blocking, > 0 = timeout mode. recv/send read this to
    # decide whether an EAGAIN is a timeout (TimeoutError) or a non-blocking
    # "would block" (BlockingIOError, which the asyncio reactor parks on).
    _timeout: float = -1.0

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
                _raise_errno()
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
            _raise_errno()

    def _raise_io(self) -> None:
        """Like the module-level `_raise_errno`, but timeout-aware: in timeout
        mode an EAGAIN/EWOULDBLOCK means the SO_*TIMEO window elapsed, so raise
        TimeoutError("timed out") to match CPython's socket.timeout. In
        non-blocking mode the same errno is a genuine would-block ->
        BlockingIOError (the asyncio reactor parks on it). Other errno ->
        SocketError."""
        err = posix_socket.tpy_errno()
        msg = _strerror(err)
        if err == _EAGAIN or err == _EINPROGRESS:
            if self._timeout > 0.0:
                # CPython's socket.timeout carries no errno (it is None
                # there); leave the unset 0 / "" defaults.
                raise TimeoutError("timed out")
            raise BlockingIOError(err, msg)
        _maybe_raise_connection_error(err, msg)
        raise SocketError(err, msg)

    def setblocking(self, flag: bool) -> None:
        """Set blocking (True) or non-blocking (False) mode, like CPython.
        Equivalent to settimeout(None) / settimeout(0.0) respectively.
        Non-blocking is the prerequisite for using a socket with the
        asyncio reactor (asyncio.get_running_loop().sock_recv/sendall)."""
        if flag:
            self.settimeout(None)
        else:
            self.settimeout(0.0)

    def getblocking(self) -> bool:
        """True in blocking or timeout mode, False only in non-blocking mode
        -- matching CPython (a positive timeout still reports blocking=True)."""
        return self._timeout != 0.0

    def settimeout(self, value: float | None) -> None:
        """Set the socket's timeout mode (CPython parity):
          * None  -> blocking forever (clears any timeout).
          * 0.0   -> non-blocking (same as setblocking(False)).
          * > 0   -> recv/send/connect raise TimeoutError after `value` secs.
        recv/send use SO_RCVTIMEO/SO_SNDTIMEO; connect uses a poll-based wait
        (see connect()). A negative value is a ValueError."""
        if value is None:
            self._timeout = -1.0
            if posix_socket.tpy_set_nonblocking(self.fd, Int32(0)) < Int32(0):
                _raise_errno()
            if posix_socket.tpy_set_timeout(self.fd, 0.0) < Int32(0):
                _raise_errno()
            return
        # Reject non-finite before the C helper casts to time_t / int (a NaN
        # or inf cast is undefined behavior). CPython raises these exact types.
        # NaN must be tested before the inf test (NaN also fails value-value).
        if value != value:
            raise ValueError("Invalid value NaN (not a number)")
        if value - value != 0.0:
            raise OverflowError("timestamp out of range for platform time_t")
        if value < 0.0:
            raise ValueError("Timeout value out of range")
        if value == 0.0:
            self._timeout = 0.0
            if posix_socket.tpy_set_nonblocking(self.fd, Int32(1)) < Int32(0):
                _raise_errno()
            if posix_socket.tpy_set_timeout(self.fd, 0.0) < Int32(0):
                _raise_errno()
            return
        self._timeout = value
        # Timeout mode stays blocking at the OS level (SO_*TIMEO enforce the
        # window); getblocking() therefore reports True, as in CPython.
        if posix_socket.tpy_set_nonblocking(self.fd, Int32(0)) < Int32(0):
            _raise_errno()
        if posix_socket.tpy_set_timeout(self.fd, value) < Int32(0):
            _raise_errno()

    def gettimeout(self) -> float | None:
        """The current timeout in seconds, or None if blocking (CPython
        returns 0.0 for non-blocking, a positive float for timeout mode)."""
        if self._timeout < 0.0:
            return None
        return self._timeout

    def bind(self, address: tuple[str, Int32]) -> None:
        host, port = address
        addr = _build_sockaddr_in(host, port)
        if posix_socket.bind(self.fd, take_ptr(addr), _SOCKADDR_IN_LEN) < Int32(0):
            _raise_errno()

    def connect(self, address: tuple[str, Int32]) -> None:
        host, port = address
        addr = _build_sockaddr_in(host, port)
        # SO_*TIMEO does not cover connect(), so timeout mode routes through the
        # poll-based helper (non-blocking connect + poll + SO_ERROR); -2 means
        # the wait elapsed. Blocking / non-blocking modes use the plain connect.
        if self._timeout > 0.0:
            rc = posix_socket.tpy_connect_timeout(self.fd, take_ptr(addr),
                                                  _SOCKADDR_IN_LEN, self._timeout)
            if rc == Int32(-2):
                raise TimeoutError("timed out")
            if rc != Int32(0):
                self._raise_io()
        elif posix_socket.connect(self.fd, take_ptr(addr), _SOCKADDR_IN_LEN) < Int32(0):
            self._raise_io()

    # Literal 128 = SOMAXCONN; named-Final-as-default rejected by sema.
    def listen(self, backlog: Int32 = Int32(128)) -> None:
        if posix_socket.listen(self.fd, backlog) < Int32(0):
            _raise_errno()

    # Returns the raw accepted fd + peer address as value types (no Own
    # element), so callers can wrap the fd in a fresh-constructor `socket`
    # local -- the move-analyzer tracks that as owned, whereas unpacking an
    # `Own[socket]` out of a tuple and repacking hits a move gap (BUGS.md).
    # Peer resolution (`_ipv4_to_str` -> `inet_ntop`) can raise while `new_fd`
    # is still naked (not yet owned by a `socket`), so close it on failure to
    # avoid leaking the accepted descriptor.
    def _accept_fd(self) -> tuple[Int32, tuple[str, Int32]]:
        addr = SockaddrIn(0, 0, 0)
        addrlen: UInt32 = _SOCKADDR_IN_LEN
        new_fd = posix_socket.accept(self.fd, take_ptr(addr), take_ptr(addrlen))
        if new_fd < Int32(0):
            _raise_errno()
        try:
            peer = (_ipv4_to_str(unsafe_cast(take_ptr(addr.sin_addr))),
                    Int32.trunc(posix_socket.ntohs(addr.sin_port)))
        except OSError:
            posix_socket.close(new_fd)
            raise
        return (new_fd, peer)

    def accept(self) -> tuple[Own[socket], tuple[str, Int32]]:
        """Block until a client connects, return `(conn, (host, port))`
        matching CPython's `socket.accept()` shape."""
        fd, peer = self._accept_fd()
        return (socket(Int32(0), Int32(0), Int32(0), fileno=fd), peer)

    def _accept_nonblocking(self) -> tuple[Own[socket], tuple[str, Int32]]:
        """`accept()` with the returned connection set non-blocking, for the
        asyncio reactor (accepted sockets do NOT inherit O_NONBLOCK on Linux).
        Underscore-private: not part of CPython's socket surface (the facade is
        otherwise CPython-faithful), so the reactor's `_SockAccept` calls it
        instead of `accept()`."""
        fd, peer = self._accept_fd()
        conn = socket(Int32(0), Int32(0), Int32(0), fileno=fd)
        conn.setblocking(False)
        return (conn, peer)

    def send(self, data: bytes) -> Int32:
        """Send (some of) `data`; returns bytes actually sent. Use
        `sendall` for full-buffer delivery. Return truncated to Int32
        from libc's ssize_t -- see module TODO."""
        return self._send_from(data, 0)

    # Send the suffix `data[offset:]` without materializing it -- the async
    # `_SockSendAll` advances `offset` across parks, so slicing a fresh
    # `bytes` per park would be O(n^2) (CPython tracks a memoryview offset).
    # Underscore-private: not part of CPython's socket surface.
    def _send_from(self, data: bytes, offset: UInt64) -> Int32:
        data_ptr: Ptr[readonly[UInt8]] = unsafe_ptr(data)
        n = posix_socket.send(self.fd,
                              unsafe_ptr_add(data_ptr, Int64.trunc(offset)),
                              UInt64(len(data)) - offset, Int32(0))
        if n < Int64(0):
            self._raise_io()
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
                self._raise_io()
            if chunk == Int64(0):
                # A zero-byte send means the peer went away; CPython's next
                # send() would fail with EPIPE, so surface the same class.
                raise BrokenPipeError(_EPIPE, _strerror(_EPIPE))
            sent = sent + UInt64(chunk)

    def recv(self, bufsize: Int32) -> bytes:
        """Receive up to `bufsize` bytes. Empty bytes means peer closed."""
        if bufsize < Int32(0):
            # Matches CPython's sock.recv(n): negative size is an error, not
            # a zero-length read. The asyncio sock_recv path relies on this.
            raise ValueError("negative buffersize in recv")
        if bufsize == Int32(0):
            return bytes()
        buf = UninitHeapStorage[UInt8](UInt32.trunc(bufsize))
        n = posix_socket.recv(self.fd, buf.ptr(), UInt64(bufsize), Int32(0))
        if n < Int64(0):
            self._raise_io()
        return unsafe_bytes_from_buf(buf.ptr(), UInt64(n))

    def setsockopt_int(self, level: Int32, optname: Int32, value: Int32) -> None:
        """Set an int-valued socket option. Struct options deferred."""
        v = value
        if posix_socket.setsockopt(self.fd, level, optname, take_ptr(v), 4) < Int32(0):
            _raise_errno()

    def getsockopt_int(self, level: Int32, optname: Int32) -> Int32:
        """Read an int-valued socket option (e.g. SO_ERROR after a
        non-blocking connect). Struct options deferred."""
        out: Int32 = 0
        optlen: UInt32 = 4
        if posix_socket.getsockopt(self.fd, level, optname,
                                   take_ptr(out), take_ptr(optlen)) < Int32(0):
            _raise_errno()
        return out

    def getsockname(self) -> tuple[str, Int32]:
        addr = SockaddrIn(0, 0, 0)
        addrlen: UInt32 = _SOCKADDR_IN_LEN
        if posix_socket.getsockname(self.fd, take_ptr(addr), take_ptr(addrlen)) < Int32(0):
            _raise_errno()
        return (_ipv4_to_str(unsafe_cast(take_ptr(addr.sin_addr))),
                Int32.trunc(posix_socket.ntohs(addr.sin_port)))

    def getpeername(self) -> tuple[str, Int32]:
        addr = SockaddrIn(0, 0, 0)
        addrlen: UInt32 = _SOCKADDR_IN_LEN
        if posix_socket.getpeername(self.fd, take_ptr(addr), take_ptr(addrlen)) < Int32(0):
            _raise_errno()
        return (_ipv4_to_str(unsafe_cast(take_ptr(addr.sin_addr))),
                Int32.trunc(posix_socket.ntohs(addr.sin_port)))

    def makefile(self, mode: str = "r",
                 buffering: Int32 = -1) -> Own[BufferedReader]:
        """Return a buffered binary reader over a *dup* of this socket's fd.

        Signature mirrors CPython (default mode "r"), but v1 implements only
        the binary-read modes ("rb"/"br"/"b") -> io.BufferedReader. Text modes
        (including the bare-makefile() default), write modes, and the
        unbuffered buffering=0 form need io's TextIOWrapper / BufferedWriter /
        raw-SocketIO layers (not built) and raise ValueError -- loud, not a
        silent binary-for-text substitution. The reader owns its own dup of
        the fd, so it and the socket close independently (differs from
        CPython's shared-fd refcount). The dup shares the same kernel
        byte-stream, so do not interleave reads on the socket and the reader
        -- each steals bytes from the other; read via one only."""
        if mode != "rb" and mode != "br" and mode != "b":
            raise ValueError("makefile: only binary read mode ('rb'/'br'/'b')"
                             " supported in v1; got '" + mode + "'")
        if buffering == 0:
            raise ValueError("makefile: unbuffered (buffering=0) not supported")
        size = DEFAULT_BUFFER_SIZE if buffering < 0 else buffering
        # Propagate timeout mode so a recv-timeout on the dup'd fd (SO_RCVTIMEO
        # is shared across the dup) surfaces as TimeoutError, not a raw EAGAIN.
        return BufferedReader(FileIO(os.dup(Int64(self.fd)),
                                     timeout_mode=self._timeout > 0.0), size)

    def __enter__(self) -> socket:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# ---------- Module-level factories ----------


def socketpair(family: Int32 = AF_UNIX, type_: Int32 = SOCK_STREAM,
               proto: Int32 = Int32(0)) -> tuple[Own[socket], Own[socket]]:
    """Create a pair of connected sockets via `::socketpair`.

    Defaults match CPython: AF_UNIX + SOCK_STREAM. Useful for in-process
    full-duplex pipes; AF_UNIX is POSIX-only (no Windows support yet)."""
    sv = UninitArrayStorage[Int32, 2]()
    if posix_socket.socketpair(family, type_, proto, sv.ptr()) < Int32(0):
        _raise_errno()
    a = socket(Int32(0), Int32(0), Int32(0), fileno=unsafe_load(sv.ptr(), 0))
    b = socket(Int32(0), Int32(0), Int32(0), fileno=unsafe_load(sv.ptr(), 1))
    return (a, b)


def create_connection(address: tuple[str, Int32],
                      timeout: float | None = None) -> Own[socket]:
    """TCP client convenience: socket + connect. A `timeout` (seconds) is
    applied before connect so connect/recv/send all honor it (CPython parity);
    None leaves the socket blocking."""
    s = socket(AF_INET, SOCK_STREAM, Int32(0))
    if timeout is not None:
        s.settimeout(timeout)
    s.connect(address)
    return s


def create_server(address: tuple[str, Int32],
                  backlog: Int32 = Int32(128),
                  reuse_addr: bool = True) -> Own[socket]:
    """TCP server convenience: socket + SO_REUSEADDR + bind + listen.
    Caller loops on accept() to serve connections."""
    s = socket(AF_INET, SOCK_STREAM, Int32(0))
    if reuse_addr:
        s.setsockopt_int(SOL_SOCKET, SO_REUSEADDR, Int32(1))
    s.bind(address)
    s.listen(backlog)
    return s
