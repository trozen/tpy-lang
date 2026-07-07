# tpy: cpp_namespace("tpystd::ssl")
"""TLS for sockets -- a CPython-compatible `ssl` surface backed by mbedTLS.

The HTTPS *client* path: `create_default_context()` -> `SSLContext` ->
`wrap_socket(sock, server_hostname=...)` -> `SSLSocket` (recv/send/sendall/
do_handshake/close). Secure by default: certificate verification REQUIRED
and the hostname checked against the peer cert's CN/SAN (which also drives
SNI). `create_default_context()` trusts a vendored Mozilla root bundle PLUS
the platform's CA bundle when one exists (`SSL_CERT_FILE` overrides the
probed location; see `load_default_certs`), so system-installed corporate
CAs verify with no flags, like curl. Add per-context CAs with
`load_verify_locations(cafile=...)`.

Architecture (see docs/SSL_DESIGN.md):
  * `_bindings.mbedtls` -- raw @native bindings to the cohesive
    `tpy_tls_session` C shim; the only place mbedTLS is touched.
  * this module -- backend-agnostic facade; classes hold a single opaque
    session handle and map mbedTLS return codes to the exception tree.

The server path: `SSLContext()` -> `load_cert_chain(certfile, keyfile)` ->
`wrap_socket(sock, server_side=True)` -> `SSLSocket`. The context's
client-only verify/hostname config is ignored on this path (no mutual-TLS
client-cert verification yet -- see docs/SSL_DESIGN.md).

Known gaps (see docs/SSL_DESIGN.md deferred surface): the system trust store
is read as a bundle FILE (env override + well-known paths) -- macOS
Keychain-only corporate CAs and `SSL_CERT_DIR` directory stores are not
read. `makefile()` returns a binary `BufferedReader` (the http.client read
path); text mode follows.

Documented limitations (match CPython or a harmless teardown gap; not
tracked as bugs):
  * After `makefile()`, `recv()` and the returned reader both drive
    `tls_read` on the same shared session, so interleaving reads across
    the two handles splits the TLS byte-stream -- the same hazard as
    CPython's `socket.recv` + `makefile`. TPy's `Rc` share additionally
    keeps the reader (and the fd) alive past `close()`, so a stale reader
    holds the connection open longer than CPython would.
  * `SSLSocket` has no `__del__`, so dropping one without `close()` skips
    the best-effort `close_notify` -- no leak (the fd + session free via
    their field `__del__`s), just an incomplete TLS teardown.

Deliberate divergences from CPython's `ssl` (so they are declared, not
silent -- see docs/LANGUAGE_FEATURES.md):
  * `SSLContext()` takes no protocol argument and is role-agnostic: the
    client/server role is chosen at `wrap_socket(server_side=)`, where
    CPython selects it via `PROTOCOL_TLS_CLIENT`/`PROTOCOL_TLS_SERVER`
    (or `create_default_context(purpose=)`). `load_cert_chain` takes no
    `password=` (tighter v1 signature).
  * `load_cert_chain` supplies the SERVER identity only: it is consulted
    solely on the `wrap_socket(server_side=True)` path. On the client path
    it is a no-op -- TPy has no client-certificate / mutual-TLS support yet
    (deferred; see docs/SSL_DESIGN.md), where CPython would present the
    loaded cert to an mTLS server.
  * `wrap_socket(server_hostname=..., server_side=True)` raises rather than
    silently ignoring the hostname -- `server_hostname` is client-only (SNI
    + CN/SAN match). CPython raises `ValueError` here; TPy raises `SSLError`
    (no `ValueError` base).
  * `SSLSocket.do_handshake()` returns a bool (True done / False needs I/O)
    rather than returning None and raising `SSLWantReadError`/`Write` on a
    non-blocking socket; the blocking `wrap_socket(do_handshake_on_connect=
    True)` path is unaffected and matches CPython. (recv/send DO raise the
    `SSLWant*` subclasses on a non-blocking socket, and the write path maps
    a `close_notify` return to `SSLZeroReturnError` defensively; `recv()`
    returns `b""` on a clean `close_notify`, which matches CPython's
    `SSLSocket.recv`.)
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
    `cadata`).
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
import os
from tplib import Rc
from io import BufferedReader

# CPython ssl.CERT_* values.
CERT_NONE: Final[Int32] = 0
CERT_REQUIRED: Final[Int32] = 2

# Well-known platform CA-bundle locations (the curl/Go probe conventions),
# tried in order by load_default_certs(); SSL_CERT_FILE overrides the probe.
# A module global (not Final) so tests can inject a fixture bundle -- the
# offline seam for the system-trust path. macOS gap: Keychain-only corporate
# CAs live in a database, not a PEM file; the shipped /etc/ssl/cert.pem and
# the Homebrew export cover the common cases, SSL_CERT_FILE the rest.
_ca_probe_paths: list[str] = [
    "/etc/ssl/certs/ca-certificates.crt",                 # Debian/Ubuntu/Arch
    "/etc/pki/tls/certs/ca-bundle.crt",                   # Fedora/RHEL
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # RHEL 7+
    "/etc/ssl/ca-bundle.pem",                             # openSUSE
    "/etc/ssl/cert.pem",                                  # Alpine, macOS
    "/usr/local/share/certs/ca-root-nss.crt",             # FreeBSD
    "/opt/homebrew/etc/ca-certificates/cert.pem",         # Homebrew (arm64)
    "/usr/local/etc/ca-certificates/cert.pem",            # Homebrew (x86_64)
]


def _resolve_system_ca_file() -> str:
    """The platform CA bundle: SSL_CERT_FILE if set, else the first existing
    well-known bundle, else "" (no system store; the vendored roots still
    apply). Loading is best-effort, matching CPython/OpenSSL: a missing or
    unparseable bundle (even an explicit SSL_CERT_FILE) is skipped at wrap
    time, never raises -- load_verify_locations() is the loud explicit
    spelling."""
    env = os.getenv("SSL_CERT_FILE")
    if env is not None and len(env) > 0:
        return env
    for p in _ca_probe_paths:
        if os.path.isfile(p):
            return p
    return ""


class SSLError(OSError):
    """A TLS-layer error (handshake, protocol, or I/O over the session)."""
    pass


class SSLCertVerificationError(SSLError):
    """The peer certificate failed verification (chain or hostname)."""
    pass


class SSLWantReadError(SSLError):
    """A non-blocking operation needs more data from the socket; retry when
    it is readable."""
    pass


class SSLWantWriteError(SSLError):
    """A non-blocking operation needs to flush to the socket; retry when it
    is writable."""
    pass


class SSLZeroReturnError(SSLError):
    """The TLS connection was closed cleanly (close_notify) during the
    operation."""
    pass


def _errstr(rc: Int32) -> str:
    buf = UninitHeapStorage[UInt8](UInt32(160))
    mbedtls.tls_strerror(rc, buf.ptr(), UInt64(160))
    return unsafe_str_from_cstr(unsafe_cast(buf.ptr()))


def _fail(s: Ptr[mbedtls.Session], msg: str) -> None:
    """Free a half-configured session, then raise -- the single home for the
    free+raise pairing so no config error branch can leak `s`."""
    mbedtls.tls_free(s)
    raise SSLError(msg)


def _raise_io_error(rc: Int32) -> None:
    """Map a negative mbedTLS I/O return to the CPython ssl exception:
    WANT_READ/WANT_WRITE -> SSLWantReadError/SSLWantWriteError (non-blocking
    socket needs I/O), close_notify -> SSLZeroReturnError, else SSLError.
    The close_notify arm is defensive on the write path: mbedTLS surfaces
    PEER_CLOSE_NOTIFY from record reads, and whether CPython raises on a
    write after a received close_notify is unverified."""
    c = mbedtls.tls_classify(rc)
    if c == 1:
        raise SSLWantReadError(_errstr(rc))
    if c == 2:
        raise SSLWantWriteError(_errstr(rc))
    if c == 3:
        raise SSLZeroReturnError(_errstr(rc))
    raise SSLError(_errstr(rc))


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
        if mbedtls.tls_classify(rc) == 3:  # peer close_notify -> EOF
            return b""
        if rc < Int32(0):
            _raise_io_error(rc)
        return unsafe_bytes_from_buf(buf.ptr(), UInt64(rc))


class SSLContext:
    """TLS configuration, role-agnostic. Secure-by-default client config
    (verify + hostname on); the server role is opted into per-wrap via
    `wrap_socket(server_side=True)` over a `load_cert_chain` cert."""
    verify_mode: Int32
    check_hostname: bool
    _cafile: str
    _use_bundled_ca: bool
    _system_cafile: str
    _certfile: str
    _keyfile: str

    def __init__(self) -> None:
        self.verify_mode = CERT_REQUIRED
        self.check_hostname = True
        self._cafile = ""
        # A bare SSLContext() trusts nothing until told to (like CPython, where
        # only create_default_context / load_default_certs load the roots).
        self._use_bundled_ca = False
        self._system_cafile = ""
        # The server-role cert chain is empty until load_cert_chain; the
        # client path never consults it.
        self._certfile = ""
        self._keyfile = ""

    def load_verify_locations(self, cafile: str) -> None:
        """Trust the CA certificates in `cafile` (PEM or DER). Additive to the
        bundled roots when those are also enabled (matches CPython)."""
        self._cafile = cafile

    def load_cert_chain(self, certfile: str, keyfile: str) -> None:
        """Load the server's certificate chain and private key (PEM files),
        used when wrapping a socket with `server_side=True`. CPython's
        `password=` parameter is not supported (tighter v1 signature)."""
        self._certfile = certfile
        self._keyfile = keyfile

    def load_default_certs(self) -> None:
        """Trust the default CA sets: the vendored Mozilla bundle plus the
        platform's own bundle when one exists (SSL_CERT_FILE overrides the
        probed location, like OpenSSL) -- so a corporate CA installed
        system-wide verifies with no flags, matching curl. Additive with
        load_verify_locations. CPython's `purpose=` parameter is not
        supported (tighter v1 signature)."""
        self._use_bundled_ca = True
        self._system_cafile = _resolve_system_ca_file()

    def wrap_socket(self, sock: Own[socket], server_hostname: str = "",
                    do_handshake_on_connect: bool = True,
                    server_side: bool = False) -> Own[SSLSocket]:
        """Wrap `sock` in a TLS session. `server_side=False` (default) is the
        verifying HTTPS-client path; `server_side=True` is the server path,
        which requires a prior `load_cert_chain` and ignores the client-only
        verify/hostname configuration."""
        if server_side:
            # server_hostname is client-only (SNI + CN/SAN match); CPython
            # raises ValueError for this combination. (TPy has no ValueError
            # base, so SSLError -- same as the check_hostname guard below.)
            if len(server_hostname) > 0:
                raise SSLError("server_hostname can only be specified in "
                               "client mode")
        elif self.check_hostname and len(server_hostname) == 0:
            # CPython rejects this too: you cannot verify the hostname without
            # one. (CPython raises ValueError; TPy has no ValueError base.)
            raise SSLError("check_hostname requires server_hostname")
        s = mbedtls.tls_new()
        if s is None:
            raise SSLError("could not allocate TLS session")
        if server_side:
            self._config_server(s)
        else:
            self._config_client(s)
        if mbedtls.tls_setup(s) != 0:
            _fail(s, "TLS setup failed")
        mbedtls.tls_set_fd(s, sock.fileno())
        if not server_side and len(server_hostname) > 0:
            # set_hostname drives both SNI and the CN/SAN match -- a failure
            # here would silently leave verification with neither, so it must
            # raise.
            host = server_hostname
            if mbedtls.tls_set_hostname(s, unsafe_cast(unsafe_ptr(host)),
                                        UInt64(len(host))) != 0:
                _fail(s, "could not set TLS hostname")
        wrapped = SSLSocket(Rc.new(_SslSession(s, sock)))
        if do_handshake_on_connect:
            wrapped.do_handshake_blocking()
        return wrapped

    def _config_client(self, s: Ptr[mbedtls.Session]) -> None:
        """Apply the verifying-client config (trust store + verify mode) to a
        fresh session. Frees `s` and raises on failure."""
        verify = Int32(1) if self.verify_mode == CERT_REQUIRED else Int32(0)
        ca = self._cafile  # "" -> no trust store loaded (len 0; shim skips it)
        rc = mbedtls.tls_config_client(
            s, unsafe_cast(unsafe_ptr(ca)), UInt64(len(ca)), verify)
        if rc != 0:
            _fail(s, _errstr(rc))
        if self._use_bundled_ca:
            if mbedtls.tls_add_bundled_ca(s) != 0:
                _fail(s, "could not load bundled CA store")
        if len(self._system_cafile) > 0:
            # Best-effort, matching CPython/OpenSSL: an unreadable or
            # unparseable system bundle (even an explicit SSL_CERT_FILE) is
            # skipped -- the vendored roots and load_verify_locations still
            # apply. load_verify_locations() is the loud explicit tool.
            sp = self._system_cafile
            mbedtls.tls_add_ca_file(s, unsafe_cast(unsafe_ptr(sp)),
                                    UInt64(len(sp)))

    def _config_server(self, s: Ptr[mbedtls.Session]) -> None:
        """Apply the server config (own cert chain + key) to a fresh session.
        Frees `s` and raises on failure."""
        if len(self._certfile) == 0:
            _fail(s, "server_side wrap_socket requires load_cert_chain")
        cf = self._certfile
        kf = self._keyfile
        rc = mbedtls.tls_config_server(
            s, unsafe_cast(unsafe_ptr(cf)), UInt64(len(cf)),
            unsafe_cast(unsafe_ptr(kf)), UInt64(len(kf)))
        if rc != 0:
            _fail(s, _errstr(rc))


def create_default_context() -> Own[SSLContext]:
    """A secure-by-default client context: verification + hostname check on,
    trusting the vendored Mozilla root bundle plus the platform's CA bundle
    (see load_default_certs). `requests.get("https://...")` and `urlopen`
    verify out of the box, and hosts signed by a system-installed corporate
    CA verify with no flags, like curl."""
    ctx = SSLContext()
    ctx.load_default_certs()
    return ctx


def _bundled_ca_count() -> Int32:
    """Number of roots in the compiled-in Mozilla bundle (-1 on parse error).
    Test hook: a real public-root handshake can't run offline, so this is how
    a test proves the default trust store is embedded and non-empty."""
    return mbedtls.tls_bundled_ca_count()


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
        rc = mbedtls.tls_handshake(self._session.get().raw())
        c = mbedtls.tls_classify(rc)
        if c == 0:
            self._handshaked = True
            return True
        if c == 1 or c == 2:  # WANT_READ / WANT_WRITE
            return False
        if c == 4:
            raise SSLCertVerificationError("certificate verify failed")
        # Keep the mbedTLS reason: a bare "handshake failed" is undebuggable
        # (protocol/cipher mismatch vs alert vs parse error all look alike).
        raise SSLError("handshake failed: " + _errstr(rc))

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
            _raise_io_error(rc)
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
                _raise_io_error(rc)
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
