# tpy: cpp_namespace("tpystd::socket")
"""POSIX-sockets module, CPython-compatible surface.

Backed by `_bindings.posix_socket` (raw @native bindings) + a few out-of-line
helpers in runtime/cpp/src/stdlib/socket_impl.cpp for DNS resolution, errno
access and fcntl / timeval options. All Python semantics (error wrapping,
address-tuple packing, RAII of fd lifetimes, the blocking / timeout-mode
retry loops and where a Ctrl-C is delivered) are TPy code here; the wait on
an fd plus the Ctrl-C wake fd is `_interrupt`'s one runtime primitive.

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
    `settimeout(sec)` is also done: recv/send/accept/connect wait in poll()
    on one deadline per operation (`_interrupt`'s waits), and a timed-out
    op raises TimeoutError ("timed out"), matching CPython's socket.timeout.
    Blocking mode takes the same path with no deadline, so a Ctrl-C ends a
    blocked call with KeyboardInterrupt. Not reproduced: the
    process-wide `setdefaulttimeout()` / `_GLOBAL_DEFAULT_TIMEOUT` sentinel
    (default is plain blocking).

  * **setsockopt with struct values.** SO_RCVTIMEO / SO_SNDTIMEO are set by
    settimeout() through the tpy_set_timeout helper (timeval built C-side),
    for makefile()'s reader only. SO_LINGER
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
    port: int32)`) would read better than a bare tuple. Tabled until we
    decide on the module's record shapes overall.

  * **struct hostent / addrinfo accessors.** We only expose the flat
    tpy_resolve_ipv4 helper today. A proper getaddrinfo wrapper that
    surfaces the full result set wants typed TPy records; blocked on
    the same portability concern that drove the helper approach.
"""

from __future__ import annotations
from typing import Final
from tpy import (
    int32, int64, uint8, uint16, uint32, uint64, Ptr, readonly, Own,
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
import _interrupt

import os
from io import FileIO, BufferedReader, DEFAULT_BUFFER_SIZE


# ---------- Wire constants ----------
# Values identical across Linux and macOS/BSD are literals; the ones that
# diverge (SOL_SOCKET, SO_*, AF_INET6) are sourced from the system headers
# via posix_socket.tpy_const_* getters so the same source builds correctly on
# either platform. Sourced from `<sys/socket.h>`, `<netinet/in.h>`,
# `<netinet/tcp.h>`.

AF_INET:     Final[int32] = 2
AF_UNIX:     Final[int32] = 1    # Not yet usable (no sockaddr_un binding).
# Linux 10, macOS/BSD 30. Not yet usable (no sockaddr_in6 binding).
AF_INET6:    Final[int32] = native_global("tpy_const_af_inet6", binding="C")

SOCK_STREAM: Final[int32] = 1
SOCK_DGRAM:  Final[int32] = 2

# Linux 1, BSD/macOS 0xffff.
SOL_SOCKET:   Final[int32] = native_global("tpy_const_sol_socket", binding="C")
SO_REUSEADDR: Final[int32] = native_global("tpy_const_so_reuseaddr", binding="C")
SO_KEEPALIVE: Final[int32] = native_global("tpy_const_so_keepalive", binding="C")
SO_ERROR:     Final[int32] = native_global("tpy_const_so_error", binding="C")

IPPROTO_TCP: Final[int32] = 6
IPPROTO_UDP: Final[int32] = 17

TCP_NODELAY: Final[int32] = 1

SHUT_RD:   Final[int32] = 0
SHUT_WR:   Final[int32] = 1
SHUT_RDWR: Final[int32] = 2

# recv/send flag: this call only, don't block (Linux 0x40, macOS/BSD 0x80).
_MSG_DONTWAIT: Final[int32] = native_global("tpy_const_msg_dontwait", binding="C")


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
    def __init__(self, err: int32, strerror: str) -> None:
        super().__init__(err, strerror)


class gaierror(OSError):
    """Raised when name resolution (getaddrinfo) fails. `.errno` carries the
    EAI_* code (not a POSIX errno) and `.strerror` the gai_strerror message,
    like CPython's socket.gaierror. Subclasses `OSError` directly, matching
    CPython's hierarchy."""
    def __init__(self, err: int32, strerror: str) -> None:
        super().__init__(err, strerror)


# errno values diverge across platforms (Linux EAGAIN 11 / EINPROGRESS 115;
# macOS 35 / 36), so source them from <errno.h> via native globals. EAGAIN ==
# EWOULDBLOCK on both Linux and macOS; EINPROGRESS is a non-blocking connect's
# "in progress" result.
_EAGAIN: Final[int32] = native_global("tpy_const_eagain", binding="C")
_EINTR: Final[int32] = native_global("tpy_const_eintr", binding="C")
_EINPROGRESS: Final[int32] = native_global("tpy_const_einprogress", binding="C")
# Connection-error errno values, also platform-divergent (see socket_impl.cpp).
_EPIPE: Final[int32] = native_global("tpy_const_epipe", binding="C")
_ECONNRESET: Final[int32] = native_global("tpy_const_econnreset", binding="C")
_ECONNREFUSED: Final[int32] = native_global("tpy_const_econnrefused", binding="C")
_ECONNABORTED: Final[int32] = native_global("tpy_const_econnaborted", binding="C")


def _strerror(err: int32) -> str:
    return unsafe_str_from_cstr(posix_socket.strerror(err))


def _maybe_raise_connection_error(err: int32, strerr: str) -> None:
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
    host_ptr: Ptr[readonly[uint8]] = unsafe_cast(unsafe_ptr(hostname))
    out = UninitArrayStorage[uint8, 4]()
    rc = posix_socket.tpy_resolve_ipv4(host_ptr, uint64(len(hostname)), out.ptr())
    if rc != 0:
        _raise_resolve_error()
    return _ipv4_to_str(out.ptr())


def _ipv4_to_str(addr_bytes: Ptr[uint8]) -> str:
    # INET_ADDRSTRLEN = 16 ("255.255.255.255\0").
    buf = UninitArrayStorage[uint8, 16]()
    if posix_socket.inet_ntop(AF_INET, addr_bytes, buf.ptr(), 16) is None:
        _raise_errno()
    return unsafe_str_from_cstr(unsafe_cast(buf.ptr()))


def _build_sockaddr_in(host: str, port: int32) -> Own[SockaddrIn]:
    """Pack (host, port) into a sockaddr_in for bind/connect.

    Empty `host` means INADDR_ANY; otherwise tried as a dotted-quad
    (inet_pton) first, then resolved via getaddrinfo. sin_port and
    sin_addr are in network byte order; sin_family is host order.

    The 3-arg SockaddrIn(...) relies on C++ aggregate init zero-filling
    sin_zero[8] -- POSIX requires those bytes zero on kernel entry, and
    un-zeroed padding can make bind return EINVAL. Do not switch to an
    init style that skips zero-fill.
    """
    port_no = posix_socket.htons(uint16.trunc(port))
    if len(hostname := host) == 0:
        return SockaddrIn(uint16.trunc(AF_INET), port_no, 0)

    addr_bytes = UninitArrayStorage[uint8, 4]()
    host_ptr: Ptr[readonly[uint8]] = unsafe_cast(unsafe_ptr(hostname))
    rc = posix_socket.inet_pton(AF_INET, host_ptr, addr_bytes.ptr())
    if rc != 1:
        rc2 = posix_socket.tpy_resolve_ipv4(host_ptr, uint64(len(hostname)),
                                addr_bytes.ptr())
        if rc2 != 0:
            _raise_resolve_error()

    addr_u32_ptr: Ptr[uint32] = unsafe_cast(addr_bytes.ptr())
    return SockaddrIn(uint16.trunc(AF_INET), port_no,
                      unsafe_load(addr_u32_ptr, 0))


# ---------- socket class ----------
# Class name is lowercase `socket` to match CPython's `socket.socket`
# exactly, so user code (and the asyncio reactor's sock_* helpers) ports to
# CPython unchanged and the test cpy phase can run on CPython's real socket.

_SOCKADDR_IN_LEN: Final[uint32] = 16


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
    fd: int32 = int32(-1)

    # Socket mode, mirroring CPython's three states: -1.0 = blocking (None
    # timeout), 0.0 = non-blocking, > 0 = timeout mode. Blocking and timeout
    # mode try each call without blocking and on a would-block wait in poll()
    # (on a deadline this many seconds out when > 0), a wait a Ctrl-C ends;
    # non-blocking mode calls libc directly and reports a would-block as
    # BlockingIOError, which the asyncio reactor parks on.
    _timeout: float = -1.0

    def __init__(self, family: int32, type_: int32, proto: int32 = int32(0),
                 fileno: int32 = int32(-1)) -> None:
        """`fileno >= 0` wraps an existing fd (from accept); family/type/
        proto are ignored in that case. Otherwise a new socket is created
        and SocketError is raised on libc failure -- the constructor then
        throws and __del__ is not called."""
        if fileno >= int32(0):
            self.fd = fileno
        else:
            new_fd = posix_socket.socket(family, type_, proto)
            if new_fd < int32(0):
                _raise_errno()
            self.fd = new_fd

    def __del__(self) -> None:
        if self.fd >= int32(0):
            posix_socket.close(self.fd)
            self.fd = int32(-1)

    def fileno(self) -> int32:
        return self.fd

    def close(self) -> None:
        if self.fd >= int32(0):
            posix_socket.close(self.fd)
            self.fd = int32(-1)

    def shutdown(self, how: int32) -> None:
        if posix_socket.shutdown(self.fd, how) < int32(0):
            _raise_errno()

    def _check_wait(self, rc: int32) -> None:
        """Raise unless an `_interrupt` wait reported the fd READY: TimeoutError
        once the timeout-mode deadline passed, else the poll()'s errno."""
        if rc == _interrupt.TIMED_OUT:
            raise TimeoutError("timed out")
        if rc != _interrupt.READY:
            self._raise_io()

    def _raise_io(self) -> None:
        """Like the module-level `_raise_errno`, but mode-aware: in non-blocking
        mode an EAGAIN/EWOULDBLOCK is a genuine would-block -> BlockingIOError
        (the asyncio reactor parks on it); in timeout mode it can only be a
        wait that ran out -> TimeoutError("timed out"), CPython's
        socket.timeout. Other errno -> SocketError."""
        self._raise_err(posix_socket.tpy_errno())

    def _raise_err(self, err: int32) -> None:
        """`_raise_io` for an error code the caller already has."""
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
          * > 0   -> recv/send/accept/connect raise TimeoutError after
                     `value` secs.
        The operations wait in poll() on one deadline per call;
        SO_RCVTIMEO/SO_SNDTIMEO are set too, for makefile()'s reader over a
        dup of the fd. A negative value is a ValueError."""
        if value is None:
            self._timeout = -1.0
            if posix_socket.tpy_set_nonblocking(self.fd, int32(0)) < int32(0):
                _raise_errno()
            if posix_socket.tpy_set_timeout(self.fd, 0.0) < int32(0):
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
            if posix_socket.tpy_set_nonblocking(self.fd, int32(1)) < int32(0):
                _raise_errno()
            if posix_socket.tpy_set_timeout(self.fd, 0.0) < int32(0):
                _raise_errno()
            return
        self._timeout = value
        # Timeout mode stays blocking at the OS level; getblocking() therefore
        # reports True, as in CPython.
        if posix_socket.tpy_set_nonblocking(self.fd, int32(0)) < int32(0):
            _raise_errno()
        if posix_socket.tpy_set_timeout(self.fd, value) < int32(0):
            _raise_errno()

    def gettimeout(self) -> float | None:
        """The current timeout in seconds, or None if blocking (CPython
        returns 0.0 for non-blocking, a positive float for timeout mode)."""
        if self._timeout < 0.0:
            return None
        return self._timeout

    def bind(self, address: tuple[str, int32]) -> None:
        host, port = address
        addr = _build_sockaddr_in(host, port)
        if posix_socket.bind(self.fd, take_ptr(addr), _SOCKADDR_IN_LEN) < int32(0):
            _raise_errno()

    def connect(self, address: tuple[str, int32]) -> None:
        host, port = address
        addr = _build_sockaddr_in(host, port)
        # Non-blocking mode (asyncio) connects directly and reports
        # EINPROGRESS as BlockingIOError.
        if self._timeout != 0.0:
            self._connect_waiting(take_ptr(addr))
        elif posix_socket.connect(self.fd, take_ptr(addr), _SOCKADDR_IN_LEN) < int32(0):
            self._raise_io()

    # Blocking and timeout mode: a non-blocking connect, a wait for
    # writability and SO_ERROR for the outcome. connect() is never re-issued:
    # after EINPROGRESS the kernel completes it on its own, and a second call
    # would only report EALREADY / EISCONN. O_NONBLOCK is set for the call
    # only (connect has no per-call don't-wait flag); flipping it is safe on a
    # socket that is not yet connected, as no other thread does I/O on it.
    def _connect_waiting(self, addr: Ptr[SockaddrIn]) -> None:
        was_nonblocking = posix_socket.tpy_set_nonblocking(self.fd, 1)
        if was_nonblocking < 0:
            self._raise_io()
        try:
            if posix_socket.connect(self.fd, addr, _SOCKADDR_IN_LEN) < 0:
                err = posix_socket.tpy_errno()
                # EINTR: the connect goes on in the background, as after
                # EINPROGRESS.
                if err != _EINPROGRESS and err != _EINTR:
                    self._raise_err(err)
                self._check_wait(_interrupt.wait_writable(
                    self.fd, _interrupt.deadline_after(self._timeout)))
                so_err: int32 = 0
                optlen: uint32 = 4
                if posix_socket.getsockopt(self.fd, SOL_SOCKET, SO_ERROR,
                                           take_ptr(so_err),
                                           take_ptr(optlen)) < 0:
                    self._raise_io()
                if so_err != 0:
                    self._raise_err(so_err)
        finally:
            if was_nonblocking == 0:
                posix_socket.tpy_set_nonblocking(self.fd, 0)
        # Connected: a Ctrl-C that arrived meanwhile is delivered now.
        _interrupt.check()

    # Literal 128 = SOMAXCONN; named-Final-as-default rejected by sema.
    def listen(self, backlog: int32 = int32(128)) -> None:
        if posix_socket.listen(self.fd, backlog) < int32(0):
            _raise_errno()

    # Returns the raw accepted fd + peer address as value types (no Own
    # element), so callers can wrap the fd in a fresh-constructor `socket`
    # local -- the move-analyzer tracks that as owned, whereas unpacking an
    # `Own[socket]` out of a tuple and repacking hits a move gap (BUGS.md).
    # Peer resolution (`_ipv4_to_str` -> `inet_ntop`) can raise while `new_fd`
    # is still naked (not yet owned by a `socket`), so close it on failure to
    # avoid leaking the accepted descriptor.
    #
    # Blocking and timeout mode wait for a pending connection, then call a
    # plain accept(): the listener's O_NONBLOCK is shared with every thread
    # accepting on it and with setblocking(), so it is never flipped for the
    # call (the multi-acceptor race this leaves is TODO.md's settimeout item).
    def _accept_fd(self) -> tuple[int32, tuple[str, int32]]:
        addr = SockaddrIn(0, 0, 0)
        addrlen: uint32 = _SOCKADDR_IN_LEN
        new_fd: int32 = -1
        if self._timeout != 0.0:
            deadline = _interrupt.deadline_after(self._timeout)
            while new_fd < 0:
                self._check_wait(_interrupt.wait_readable(self.fd, deadline))
                addrlen = _SOCKADDR_IN_LEN
                new_fd = posix_socket.accept(self.fd, take_ptr(addr), take_ptr(addrlen))
                if new_fd < 0:
                    err = posix_socket.tpy_errno()
                    # EAGAIN (a listener that is O_NONBLOCK after all): another
                    # acceptor took the connection first; wait for the next.
                    if err != _EINTR and err != _EAGAIN:
                        self._raise_err(err)
        else:
            new_fd = posix_socket.accept(self.fd, take_ptr(addr), take_ptr(addrlen))
            if new_fd < int32(0):
                _raise_errno()
        try:
            peer = (_ipv4_to_str(unsafe_cast(take_ptr(addr.sin_addr))),
                    int32.trunc(posix_socket.ntohs(addr.sin_port)))
        except OSError:
            posix_socket.close(new_fd)
            raise
        return (new_fd, peer)

    def accept(self) -> tuple[Own[socket], tuple[str, int32]]:
        """Block until a client connects, return `(conn, (host, port))`
        matching CPython's `socket.accept()` shape."""
        fd, peer = self._accept_fd()
        return (socket(int32(0), int32(0), int32(0), fileno=fd), peer)

    def _accept_nonblocking(self) -> tuple[Own[socket], tuple[str, int32]]:
        """`accept()` with the returned connection set non-blocking, for the
        asyncio reactor (accepted sockets do NOT inherit O_NONBLOCK on Linux).
        Underscore-private: not part of CPython's socket surface (the facade is
        otherwise CPython-faithful), so the reactor's `_SockAccept` calls it
        instead of `accept()`."""
        fd, peer = self._accept_fd()
        conn = socket(int32(0), int32(0), int32(0), fileno=fd)
        conn.setblocking(False)
        return (conn, peer)

    def send(self, data: bytes) -> int32:
        """Send (some of) `data`; returns bytes actually sent. Use
        `sendall` for full-buffer delivery. Return truncated to int32
        from libc's ssize_t -- see module TODO."""
        return self._send_from(data, 0)

    # Send the suffix `data[offset:]` without materializing it -- the async
    # `_SockSendAll` advances `offset` across parks, so slicing a fresh
    # `bytes` per park would be O(n^2) (CPython tracks a memoryview offset).
    # Underscore-private: not part of CPython's socket surface.
    def _send_from(self, data: bytes, offset: uint64) -> int32:
        data_ptr: Ptr[readonly[uint8]] = unsafe_ptr(data)
        start = unsafe_ptr_add(data_ptr, int64.trunc(offset))
        if self._timeout != 0.0:
            return int32.trunc(self._send_waiting(start,
                                                  uint64(len(data)) - offset))
        n = posix_socket.send(self.fd, start, uint64(len(data)) - offset,
                              int32(0))
        if n < int64(0):
            self._raise_io()
        return int32.trunc(n)

    def sendall(self, data: bytes) -> None:
        """Send every byte in `data` (loops over send)."""
        total: uint64 = uint64(len(data))
        sent: uint64 = 0
        data_ptr: Ptr[readonly[uint8]] = unsafe_ptr(data)
        while sent < total:
            start = unsafe_ptr_add(data_ptr, int64.trunc(sent))
            chunk: int64 = 0
            if self._timeout != 0.0:
                chunk = self._send_waiting(start, total - sent)
            else:
                chunk = posix_socket.send(self.fd, start, total - sent, int32(0))
                if chunk < 0:
                    self._raise_io()
            if chunk == int64(0):
                # A zero-byte send means the peer went away; CPython's next
                # send() would fail with EPIPE, so surface the same class.
                raise BrokenPipeError(_EPIPE, _strerror(_EPIPE))
            sent = sent + uint64(chunk)

    # Blocking and timeout mode: sends everything, as a blocking send() on a
    # stream socket does, unless the timeout expires after part of the data
    # went out (then the count sent so far, like SO_SNDTIMEO) or a Ctrl-C
    # arrives (then the count is lost: BUGS.md#interrupted-send-loses-sent-count).
    def _send_waiting(self, data: Ptr[readonly[uint8]], size: uint64) -> int64:
        deadline = _interrupt.deadline_after(self._timeout)
        sent: uint64 = 0
        while True:
            n = posix_socket.send(self.fd, unsafe_ptr_add(data, int64.trunc(sent)),
                                  size - sent, _MSG_DONTWAIT)
            if n >= 0:
                sent += uint64.trunc(n)
                if sent == size or n == 0:
                    # The data is committed; a Ctrl-C that arrived meanwhile
                    # is delivered now.
                    _interrupt.check()
                    return int64.trunc(sent)
                continue
            err = posix_socket.tpy_errno()
            if err == _EINTR:
                continue
            if err != _EAGAIN:
                if sent > 0:
                    return int64.trunc(sent)
                self._raise_err(err)
            rc = _interrupt.wait_writable(self.fd, deadline)
            if rc == _interrupt.TIMED_OUT and sent > 0:
                return int64.trunc(sent)
            self._check_wait(rc)

    # Blocking and timeout mode: a Ctrl-C already pending is delivered before
    # the call, so no received data is dropped for it.
    def _recv_waiting(self, buf: Ptr[uint8], size: uint64) -> int64:
        _interrupt.check()
        deadline = _interrupt.deadline_after(self._timeout)
        while True:
            n = posix_socket.recv(self.fd, buf, size, _MSG_DONTWAIT)
            if n >= 0:
                return n
            err = posix_socket.tpy_errno()
            if err == _EINTR:
                continue
            if err != _EAGAIN:
                self._raise_err(err)
            self._check_wait(_interrupt.wait_readable(self.fd, deadline))

    def recv(self, bufsize: int32) -> bytes:
        """Receive up to `bufsize` bytes. Empty bytes means peer closed."""
        if bufsize < int32(0):
            # Matches CPython's sock.recv(n): negative size is an error, not
            # a zero-length read. The asyncio sock_recv path relies on this.
            raise ValueError("negative buffersize in recv")
        if bufsize == int32(0):
            return bytes()
        buf = UninitHeapStorage[uint8](uint32.trunc(bufsize))
        n: int64 = 0
        if self._timeout != 0.0:
            n = self._recv_waiting(buf.ptr(), uint64(bufsize))
        else:
            n = posix_socket.recv(self.fd, buf.ptr(), uint64(bufsize), int32(0))
            if n < 0:
                self._raise_io()
        return unsafe_bytes_from_buf(buf.ptr(), uint64(n))

    def setsockopt_int(self, level: int32, optname: int32, value: int32) -> None:
        """Set an int-valued socket option. Struct options deferred."""
        v = value
        if posix_socket.setsockopt(self.fd, level, optname, take_ptr(v), 4) < int32(0):
            _raise_errno()

    def getsockopt_int(self, level: int32, optname: int32) -> int32:
        """Read an int-valued socket option (e.g. SO_ERROR after a
        non-blocking connect). Struct options deferred."""
        out: int32 = 0
        optlen: uint32 = 4
        if posix_socket.getsockopt(self.fd, level, optname,
                                   take_ptr(out), take_ptr(optlen)) < int32(0):
            _raise_errno()
        return out

    def getsockname(self) -> tuple[str, int32]:
        addr = SockaddrIn(0, 0, 0)
        addrlen: uint32 = _SOCKADDR_IN_LEN
        if posix_socket.getsockname(self.fd, take_ptr(addr), take_ptr(addrlen)) < int32(0):
            _raise_errno()
        return (_ipv4_to_str(unsafe_cast(take_ptr(addr.sin_addr))),
                int32.trunc(posix_socket.ntohs(addr.sin_port)))

    def getpeername(self) -> tuple[str, int32]:
        addr = SockaddrIn(0, 0, 0)
        addrlen: uint32 = _SOCKADDR_IN_LEN
        if posix_socket.getpeername(self.fd, take_ptr(addr), take_ptr(addrlen)) < int32(0):
            _raise_errno()
        return (_ipv4_to_str(unsafe_cast(take_ptr(addr.sin_addr))),
                int32.trunc(posix_socket.ntohs(addr.sin_port)))

    def makefile(self, mode: str = "r",
                 buffering: int32 = -1) -> Own[BufferedReader]:
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
        return BufferedReader(FileIO(os.dup(int64(self.fd)),
                                     timeout_mode=self._timeout > 0.0), size)

    def __enter__(self) -> socket:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# ---------- Module-level factories ----------


def socketpair(family: int32 = AF_UNIX, type_: int32 = SOCK_STREAM,
               proto: int32 = int32(0)) -> tuple[Own[socket], Own[socket]]:
    """Create a pair of connected sockets via `::socketpair`.

    Defaults match CPython: AF_UNIX + SOCK_STREAM. Useful for in-process
    full-duplex pipes; AF_UNIX is POSIX-only (no Windows support yet)."""
    sv = UninitArrayStorage[int32, 2]()
    if posix_socket.socketpair(family, type_, proto, sv.ptr()) < int32(0):
        _raise_errno()
    a = socket(int32(0), int32(0), int32(0), fileno=unsafe_load(sv.ptr(), 0))
    b = socket(int32(0), int32(0), int32(0), fileno=unsafe_load(sv.ptr(), 1))
    return (a, b)


def create_connection(address: tuple[str, int32],
                      timeout: float | None = None) -> Own[socket]:
    """TCP client convenience: socket + connect. A `timeout` (seconds) is
    applied before connect so connect/recv/send all honor it (CPython parity);
    None leaves the socket blocking."""
    s = socket(AF_INET, SOCK_STREAM, int32(0))
    if timeout is not None:
        s.settimeout(timeout)
    s.connect(address)
    return s


def create_server(address: tuple[str, int32],
                  backlog: int32 = int32(128),
                  reuse_addr: bool = True) -> Own[socket]:
    """TCP server convenience: socket + SO_REUSEADDR + bind + listen.
    Caller loops on accept() to serve connections."""
    s = socket(AF_INET, SOCK_STREAM, int32(0))
    if reuse_addr:
        s.setsockopt_int(SOL_SOCKET, SO_REUSEADDR, int32(1))
    s.bind(address)
    s.listen(backlog)
    return s
