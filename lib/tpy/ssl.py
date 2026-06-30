# tpy: cpp_namespace("tpystd::ssl")
"""TLS for sockets -- a CPython-compatible `ssl` surface backed by mbedTLS.

v1 is an HTTPS *client*: `create_default_context()` -> `SSLContext` ->
`wrap_socket(sock, server_hostname=...)` -> `SSLSocket` (recv/send/sendall/
do_handshake/close). Secure by default: certificate verification REQUIRED
and the hostname checked against the peer cert's CN/SAN (which also drives
SNI). Pass a trust store via `load_verify_locations(cafile=...)`.

Architecture (see docs/SSL_DESIGN.md):
  * `_bindings.mbedtls` -- raw @native bindings to the cohesive
    `tpy_tls_session` C shim; the only place mbedTLS is touched.
  * this module -- backend-agnostic facade; classes hold a single opaque
    session handle and map mbedTLS return codes to the exception tree.

Known v1 gaps (filed in docs/SSL_DESIGN.md / TODO): no bundled default CA
store yet, so a verifying context needs an explicit `load_verify_locations`
until the Mozilla bundle is vendored; server-side TLS is internal-only (the
test peer). `makefile()` returns a binary `BufferedReader` (the http.client
read path); text mode and the http.client/requests https wiring follow.

Deliberate divergences from CPython's `ssl` (so they are declared, not
silent -- see docs/LANGUAGE_FEATURES.md):
  * `SSLSocket.do_handshake()` returns a bool (True done / False needs I/O)
    rather than returning None and raising `SSLWantReadError`/`Write` on a
    non-blocking socket; the blocking `wrap_socket(do_handshake_on_connect=
    True)` path is unaffected and matches CPython.
  * `recv()` returns `b""` on a clean `close_notify` (the socket EOF
    convention the read path expects) rather than raising `SSLZeroReturnError`.
  * `SSLCertVerificationError` derives only from `SSLError`, not from
    `(SSLError, ValueError)` -- TPy enforces single inheritance, so the
    `ValueError` base cannot be added; code catching `ValueError` for a cert
    failure will not fire.
  * `close()` sends `close_notify` best-effort (return ignored) and defers
    the fd close to the last shared holder of the session, rather than
    eager-closing it: CPython's `SSLSocket.close()` does not send
    `close_notify` at all (that is `unwrap()`'s job) and closes the fd once
    `_io_refs` reaches zero. A still-open `makefile()` reader keeps the
    connection alive either way; `close()` here is idempotent.
  * `makefile()` takes no arguments and returns a binary `BufferedReader`,
    where CPython's `socket.makefile()` defaults to text mode and accepts
    `mode`/`buffering`/encoding arguments (text/write modes are deferred).
  * `version()` returns `"unknown"` before the handshake, where CPython
    returns `None`.
  * `wrap_socket` / `load_verify_locations` take a tighter v1 signature
    (`server_hostname` positional-with-default; `cafile` only, no `capath`/
    `cadata`). Non-blocking `recv` raises `SSLError`, not `SSLWantReadError`
    (the `SSLWant*` subclasses are deferred).
"""

from __future__ import annotations
from typing import Final
from tpy import Ptr, UInt8, UInt32, Int32, Int64, UInt64, readonly, Own, nocopy
from tpy.mem import UninitHeapStorage
from tpy.unsafe import (
    unsafe_ptr, unsafe_ptr_add, unsafe_cast,
    unsafe_str_from_cstr, unsafe_bytes_from_buf,
)
from _bindings import mbedtls
from socket import socket
from tplib import Rc
from io import BufferedReader

# CPython ssl.CERT_* values.
CERT_NONE: Final[Int32] = 0
CERT_REQUIRED: Final[Int32] = 2


class SSLError(OSError):
    """A TLS-layer error (handshake, protocol, or I/O over the session)."""
    pass


class SSLCertVerificationError(SSLError):
    """The peer certificate failed verification (chain or hostname)."""
    pass


def _errstr(rc: Int32) -> str:
    buf = UninitHeapStorage[UInt8](UInt32(160))
    mbedtls.tls_strerror(rc, buf.ptr(), UInt64(160))
    return unsafe_str_from_cstr(unsafe_cast(buf.ptr()))


@nocopy
class _SslSession:
    """Owning handle over the C `tpy_tls_session` AND the underlying socket.

    Shared via `Rc` so a `makefile()` reader and the `SSLSocket` keep the
    connection's fd alive until the last holder drops -- the session reads
    through that fd, so it must not be closed out from under a live reader.
    `__del__` frees the session; the contained socket's own `__del__` then
    closes the fd.
    """
    _s: Ptr[mbedtls.Session]
    _sock: socket

    def __init__(self, s: Ptr[mbedtls.Session], sock: Own[socket]) -> None:
        self._s = s
        self._sock = sock

    def __del__(self) -> None:
        # tls_free tolerates a null pointer; this runs once (single owner).
        mbedtls.tls_free(self._s)

    def raw(self) -> Ptr[mbedtls.Session]:
        return self._s

    def fileno(self) -> Int32:
        return self._sock.fileno()

    def setblocking(self, flag: bool) -> None:
        self._sock.setblocking(flag)

    def read_into(self, size: Int32) -> bytes:
        """Decrypt up to `size` bytes; b"" on a clean close_notify (EOF)."""
        if size <= Int32(0):
            return b""
        buf = UninitHeapStorage[UInt8](UInt32.trunc(size))
        rc = mbedtls.tls_read(self._s, buf.ptr(), UInt64(size))
        c = mbedtls.tls_classify(rc)
        if c == 3:  # peer close_notify -> EOF
            return b""
        if rc < Int32(0):
            raise SSLError(_errstr(rc))
        return unsafe_bytes_from_buf(buf.ptr(), UInt64(rc))


class SSLContext:
    """Client TLS configuration. Secure by default (verify + hostname on)."""
    verify_mode: Int32
    check_hostname: bool
    _cafile: str

    def __init__(self) -> None:
        self.verify_mode = CERT_REQUIRED
        self.check_hostname = True
        self._cafile = ""

    def load_verify_locations(self, cafile: str) -> None:
        """Trust the CA certificates in `cafile` (PEM or DER)."""
        self._cafile = cafile

    def wrap_socket(self, sock: Own[socket], server_hostname: str = "",
                    do_handshake_on_connect: bool = True) -> Own[SSLSocket]:
        # CPython rejects this combination too: you cannot verify the hostname
        # without one. (CPython raises ValueError; TPy has no ValueError base.)
        if self.check_hostname and len(server_hostname) == 0:
            raise SSLError("check_hostname requires server_hostname")
        s = mbedtls.tls_new()
        if s is None:
            raise SSLError("could not allocate TLS session")
        verify = Int32(1) if self.verify_mode == CERT_REQUIRED else Int32(0)
        ca = self._cafile  # "" -> no trust store loaded (len 0; shim skips it)
        rc = mbedtls.tls_config_client(
            s, unsafe_cast(unsafe_ptr(ca)), UInt64(len(ca)), verify)
        if rc != 0:
            mbedtls.tls_free(s)
            raise SSLError(_errstr(rc))
        if mbedtls.tls_setup(s) != 0:
            mbedtls.tls_free(s)
            raise SSLError("TLS setup failed")
        mbedtls.tls_set_fd(s, sock.fileno())
        # set_hostname drives both SNI and the CN/SAN match -- a failure here
        # would silently leave verification with neither, so it must raise.
        host = server_hostname
        if len(host) > 0:
            if mbedtls.tls_set_hostname(s, unsafe_cast(unsafe_ptr(host)),
                                        UInt64(len(host))) != 0:
                mbedtls.tls_free(s)
                raise SSLError("could not set TLS hostname")
        wrapped = SSLSocket(Rc.new(_SslSession(s, sock)))
        if do_handshake_on_connect:
            wrapped.do_handshake_blocking()
        return wrapped


def create_default_context() -> Own[SSLContext]:
    """A secure-by-default client context (verification + hostname check on)."""
    return SSLContext()


@nocopy
class SSLSocket:
    """A socket whose I/O is encrypted through an mbedTLS session.

    Built by `SSLContext.wrap_socket`. The session + socket are held behind
    an `Rc` so `makefile()` can share them with a buffered reader. Reads and
    writes go through `mbedtls_ssl_read`/`write`, never the raw fd.
    """
    _session: Rc[_SslSession]
    _handshaked: bool
    _closed: bool

    def __init__(self, session: Own[Rc[_SslSession]]) -> None:
        self._session = session
        self._handshaked = False
        self._closed = False

    def do_handshake(self) -> bool:
        """Advance the handshake one step. True when complete; False when it
        needs more socket I/O (non-blocking socket). Raises on failure."""
        c = mbedtls.tls_classify(
            mbedtls.tls_handshake(self._session.get().raw()))
        if c == 0:
            self._handshaked = True
            return True
        if c == 1 or c == 2:  # WANT_READ / WANT_WRITE
            return False
        if c == 4:
            raise SSLCertVerificationError("certificate verify failed")
        raise SSLError("handshake failed")

    def do_handshake_blocking(self) -> None:
        """Drive the handshake to completion (expects a blocking socket)."""
        while not self.do_handshake():
            pass

    def recv(self, bufsize: Int32) -> bytes:
        """Receive up to `bufsize` decrypted bytes; b"" means the peer sent
        a clean close_notify."""
        return self._session.get().read_into(bufsize)

    def send(self, data: bytes) -> Int32:
        """Encrypt + send some of `data`; returns bytes sent."""
        rc = mbedtls.tls_write(self._session.get().raw(), unsafe_ptr(data),
                               UInt64(len(data)))
        if rc < Int32(0):
            raise SSLError(_errstr(rc))
        return rc

    def sendall(self, data: bytes) -> None:
        """Encrypt + send every byte in `data`."""
        total: UInt64 = UInt64(len(data))
        sent: UInt64 = 0
        data_ptr: Ptr[readonly[UInt8]] = unsafe_ptr(data)
        while sent < total:
            rc = mbedtls.tls_write(self._session.get().raw(),
                                   unsafe_ptr_add(data_ptr, Int64.trunc(sent)),
                                   total - sent)
            if rc < Int32(0):
                raise SSLError(_errstr(rc))
            sent = sent + UInt64(rc)

    def makefile(self) -> Own[BufferedReader]:
        """A buffered binary reader over this TLS session (CPython's
        `socket.makefile("rb")`). Shares the session via `Rc`, so the reader
        keeps the connection alive independently of this `SSLSocket`."""
        return BufferedReader(SSLRawIO(self._session.clone()))

    def version(self) -> str:
        """The negotiated protocol, e.g. "TLSv1.3"."""
        return unsafe_str_from_cstr(unsafe_cast(
            mbedtls.tls_version(self._session.get().raw())))

    def fileno(self) -> Int32:
        return self._session.get().fileno()

    def setblocking(self, flag: bool) -> None:
        self._session.get().setblocking(flag)

    def close(self) -> None:
        """Send close_notify (best-effort). The underlying fd is closed when
        the last shared holder of the session drops -- so a still-open
        `makefile()` reader keeps the connection alive, matching CPython's
        refcounted `socket.makefile`. Idempotent: close_notify is sent once."""
        if self._closed:
            return
        self._closed = True
        mbedtls.tls_close_notify(self._session.get().raw())


@nocopy
class SSLRawIO:
    """A `RawBinaryIO` byte source over a shared TLS session -- the raw read
    path under a `makefile()` BufferedReader. `read()` returns b"" on a clean
    close_notify (the EOF convention the buffered reader expects)."""
    _session: Rc[_SslSession]

    def __init__(self, session: Own[Rc[_SslSession]]) -> None:
        self._session = session

    def read(self, size: Int32 = -1) -> bytes:
        n = size if size > Int32(0) else 8192
        return self._session.get().read_into(n)

    def close(self) -> None:
        # The session/fd close when the last Rc holder drops; nothing here.
        pass


def _wrap_server(sock: Own[socket], certfile: str, keyfile: str,
                 do_handshake_on_connect: bool = False) -> Own[SSLSocket]:
    """Internal: server-side TLS, for the in-process test peer only -- NOT a
    public API (v1 is client-only). Mirrors wrap_socket with a server config."""
    s = mbedtls.tls_new()
    if s is None:
        raise SSLError("could not allocate TLS session")
    rc = mbedtls.tls_config_server(
        s, unsafe_cast(unsafe_ptr(certfile)), UInt64(len(certfile)),
        unsafe_cast(unsafe_ptr(keyfile)), UInt64(len(keyfile)))
    if rc != 0:
        mbedtls.tls_free(s)
        raise SSLError(_errstr(rc))
    if mbedtls.tls_setup(s) != 0:
        mbedtls.tls_free(s)
        raise SSLError("TLS setup failed")
    mbedtls.tls_set_fd(s, sock.fileno())
    wrapped = SSLSocket(Rc.new(_SslSession(s, sock)))
    if do_handshake_on_connect:
        wrapped.do_handshake_blocking()
    return wrapped
