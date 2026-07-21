# http.client -- a minimal HTTP/1.1 client (pure TPy).
#
# Scope (v1): HTTP/1.1 over plaintext (HTTPConnection) and TLS
# (HTTPSConnection, via `ssl`); the high-level request()/getresponse() flow
# (no incremental putrequest/putheader/endheaders), bytes request bodies (no
# str/file/iterable), and response bodies framed by Content-Length, chunked
# transfer-encoding, or connection-close. Reads go through io.BufferedReader
# over the connection's makefile(): the request is written via the socket,
# the response read via the reader. `getheader`/`getheaders` replace CPython's
# email.message-backed `.headers` object (which is not reproduced here).
#
# Connections are persistent (HTTP/1.1 keep-alive): the socket stays open
# across request()/getresponse() cycles, and `HTTPResponse.will_close` reports
# whether the server ended reuse (Connection: close, HTTP/1.0 without
# keep-alive, or a read-to-EOF-framed body). The caller must fully drain each
# response before the next request() and close() the connection when
# `will_close` is set -- there is no CPython-style CannotSendRequest/
# ResponseNotReady state machine guarding misuse, and unlike CPython,
# getresponse() does not auto-close the connection on will_close (TPy's
# SSLSocket.close() sends close_notify immediately, which would race a
# still-unread TLS body; CPython's close is deferred by fp refcounts).
# request() on a closed connection transparently reconnects. No pipelining.
#
# Both connection classes nominally inherit the @dynamic `_Connection` protocol
# so a caller (requests/urllib) can hold either behind one `Box[_Connection]`
# and dispatch request/getresponse/close virtually -- TPy method overrides are
# static, so a plain subclass would not dispatch through a base reference.
# Nominal inheritance (vs structural conformance) stores the conformer directly
# as the protocol base in the Box (no Adapter).
# HTTPSConnection imports `ssl`, so any program importing http.client links
# the TLS backend (mbedTLS) -- discovery + managed-linking are import-driven
# at module granularity, with no per-symbol use-driven scoping.
# tpy: cpp_namespace("tpystd::http::client")
from __future__ import annotations
from typing import Final, Protocol
from tpy import Int32, Own, String, dynamic, copy
import socket
import ssl
from io import BufferedReader


HTTP_PORT: Final[Int32] = 80
HTTPS_PORT: Final[Int32] = 443


class HTTPException(Exception):
    # Explicit __init__ + String param: compiler-gap workaround for
    # exception subclasses (StrView default-arg on a user ctor).
    def __init__(self, message: String = "") -> None:
        super().__init__(message)


class BadStatusLine(HTTPException):
    def __init__(self, line: String = "") -> None:
        super().__init__(line)


class UnknownProtocol(HTTPException):
    def __init__(self, version: String = "") -> None:
        super().__init__(version)


def _hex_val(c: Int32) -> Int32:
    if c >= 48 and c <= 57:
        return c - 48
    if c >= 65 and c <= 70:
        return c - 55
    if c >= 97 and c <= 102:
        return c - 87
    return -1


def _digits_to_int(s: str) -> int:
    # Non-negative base-10 parse; -1 on any non-digit (so a malformed
    # Content-Length / status falls back to a caller-handled sentinel rather
    # than panicking the way int(str) would).
    data = s.encode()
    n = len(data)
    if n == 0:
        return -1
    val: int = 0
    i = 0
    while i < n:
        c = Int32(data[i])
        if c < 48 or c > 57:
            return -1
        val = val * 10 + int(c - 48)
        i += 1
    return val


def _parse_chunk_size(line: bytes) -> int:
    # Hex chunk size; stops at the first non-hex byte, which transparently
    # handles chunk extensions (";ext"), the trailing CR/LF, and any spaces.
    n = len(line)
    size: int = 0
    i = 0
    while i < n:
        v = _hex_val(Int32(line[i]))
        if v < 0:
            break
        size = size * 16 + int(v)
        i += 1
    return size


class HTTPResponse:
    """Reads and parses one HTTP response off an owned BufferedReader.

    `getresponse()` builds this; it is not part of the public construction
    surface (CPython builds HTTPResponse from a socket, not a reader).
    """

    _fp: BufferedReader
    _method: str
    status: Int32
    reason: str
    version: Int32
    _headers: list[tuple[str, str]]
    _length: int      # remaining Content-Length bytes; -1 if unknown (read to close)
    _chunked: bool
    _chunk_left: int  # remaining bytes in the current chunk; <= 0 => read next size
    _eof: bool
    will_close: bool  # server ended keep-alive; caller should close the connection

    def __init__(self, fp: Own[BufferedReader], method: str) -> None:
        self._fp = fp
        self._method = method
        self.status = 0
        self.reason = ""
        self.version = 0
        self._headers = []
        self._length = -1
        self._chunked = False
        self._chunk_left = -1
        self._eof = False
        self.will_close = True

    def begin(self) -> None:
        self._read_status()
        # Only 100 Continue is an interim response to skip; CPython returns
        # 101/102/103 as the final status rather than skipping them.
        while self.status == 100:
            self._skip_headers()
            self._read_status()
        self._read_headers()
        self._init_framing()
        self.will_close = self._check_close()

    def _read_status(self) -> None:
        line: str = self._fp.readline().decode()
        line = line.rstrip()
        if line == "":
            raise BadStatusLine("")
        parts = line.split(" ", 2)
        if len(parts) < 2:
            raise BadStatusLine(line)
        ver = parts[0]
        if not ver.startswith("HTTP/"):
            raise BadStatusLine(line)
        code = _digits_to_int(parts[1])
        if code < 100 or code > 999:
            raise BadStatusLine(line)
        self.status = Int32(code)
        if len(parts) >= 3:
            self.reason = parts[2].strip()
        else:
            self.reason = ""
        if ver == "HTTP/1.0" or ver == "HTTP/0.9":
            self.version = 10
        elif ver.startswith("HTTP/1."):
            self.version = 11
        else:
            # String(): ver is a view into `parts`; the exception ctor wants
            # an owned string (view->owned wrap isn't applied to ctor args).
            raise UnknownProtocol(String(ver))

    def _skip_headers(self) -> None:
        while True:
            h: str = self._fp.readline().decode().rstrip()
            if h == "":
                break

    def _read_headers(self) -> None:
        while True:
            line: str = self._fp.readline().decode().rstrip()
            if line == "":
                break
            idx = line.find(":")
            if idx < 0:
                continue
            name = line[:idx].strip()
            value = line[idx + 1:].strip()
            self._headers.append((name, value))

    def _init_framing(self) -> None:
        no_body = (self._method == "HEAD" or self.status == 204
                   or self.status == 304
                   or (self.status >= 100 and self.status < 200))
        if no_body:
            self._length = 0
            self._eof = True
            return
        te = self.getheader("transfer-encoding")
        if te is not None and te.lower() == "chunked":
            self._chunked = True
            return
        cl = self.getheader("content-length")
        if cl is not None:
            self._length = _digits_to_int(cl)

    def _check_close(self) -> bool:
        # CPython HTTPResponse._check_close (substring match on the Connection
        # header, like CPython), minus the HTTP/1.0 Proxy-Connection case, plus
        # CPython's begin() fallback: a body with no framing (no Content-Length,
        # not chunked) is delimited by connection close.
        conn_hdr = self.getheader("connection")
        if self.version == 11:
            if conn_hdr is not None and "close" in conn_hdr.lower():
                return True
        else:
            # HTTP/1.0 stays open only on an explicit keep-alive: a non-empty
            # standalone Keep-Alive header (CPython truthiness -- an empty
            # value does not count) or a Connection: keep-alive token. Either
            # way the unframed-body fallback below still applies.
            ka = self.getheader("keep-alive")
            if ka is None or ka == "":
                if conn_hdr is None or "keep-alive" not in conn_hdr.lower():
                    return True
        if not self._chunked and self._length < 0 and not self._eof:
            return True
        return False

    def read(self, amt: Int32 = -1) -> bytes:
        if self._eof:
            return b""
        if self._chunked:
            return self._read_chunked(amt)
        if amt < 0:
            data = self._fp.read() if self._length < 0 else self._fp.read(Int32(self._length))
            self._eof = True
            return data
        want = int(amt)
        if self._length >= 0 and self._length < want:
            want = self._length
        data = self._fp.read(Int32(want))
        if self._length >= 0:
            self._length = self._length - len(data)
            if self._length <= 0:
                self._eof = True
        elif want > 0 and len(data) == 0:
            # Short read on connection-close framing (no Content-Length) means
            # EOF; a legitimate read(0) must not be misread as end-of-body.
            self._eof = True
        return data

    def _read_chunked(self, amt: Int32) -> bytes:
        # bytearray accumulator: `result = result + piece` would be
        # O(total*chunks), and chunked framing is normal streamed-response
        # behavior, not an edge (CPython collects pieces and joins once).
        result = bytearray()
        while not self._eof:
            if self._chunk_left <= 0:
                if not self._next_chunk():
                    break
            if amt >= 0:
                got = len(result)
                if got >= amt:
                    break
                remaining = int(amt) - got
                want = self._chunk_left if self._chunk_left < remaining else remaining
            else:
                want = self._chunk_left
            piece = self._fp.read(Int32(want))
            if len(piece) == 0:
                self._eof = True
                break
            result.extend(piece)
            self._chunk_left = self._chunk_left - len(piece)
            if self._chunk_left <= 0:
                # Chunk data is CRLF-terminated; drop it before the next size line.
                self._fp.read(2)
        return bytes(result)

    def _next_chunk(self) -> bool:
        size = _parse_chunk_size(self._fp.readline())
        if size <= 0:
            self._read_trailer()
            self._eof = True
            return False
        self._chunk_left = size
        return True

    def _read_trailer(self) -> None:
        while True:
            t: str = self._fp.readline().decode().rstrip()
            if t == "":
                break

    def getheader(self, name: str, default: str | None = None) -> str | None:
        low = name.lower()
        result: str = ""
        found = False
        for kv in self._headers:
            if kv[0].lower() == low:
                if not found:
                    result = kv[1]
                else:
                    result = result + ", " + kv[1]
                found = True
        if not found:
            return default
        return result

    def getheaders(self) -> Own[list[tuple[str, str]]]:
        out: list[tuple[str, str]] = []
        for kv in self._headers:
            out.append((kv[0], kv[1]))
        return out

    def close(self) -> None:
        self._fp.close()
        self._eof = True

    def __enter__(self) -> "HTTPResponse":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def _content_length(method: str, body: bytes | None) -> int:
    if body is not None:
        return len(body)
    m = method.upper()
    if m == "POST" or m == "PUT" or m == "PATCH":
        return 0
    return -1


def _build_request(method: str, url: str, body: bytes | None,
                   headers: dict[str, str] | None,
                   host: str, port: Int32, default_port: Int32) -> bytes:
    """Serialize a request line + headers + body. A free function shared by
    HTTPConnection and HTTPSConnection (a @dynamic protocol declares no shared
    implementation, so common logic lives in module functions, not a base);
    `default_port` is the scheme's default (80 / 443), omitted from the Host
    header like CPython."""
    lines: list[str] = []
    lines.append(method + " " + url + " HTTP/1.1")
    has_host = False
    has_ae = False
    has_cl = False
    has_te = False
    if headers is not None:
        for k in headers:
            kl = k.lower()
            if kl == "host":
                has_host = True
            elif kl == "accept-encoding":
                has_ae = True
            elif kl == "content-length":
                has_cl = True
            elif kl == "transfer-encoding":
                has_te = True
    if not has_host:
        if port == default_port:
            lines.append("Host: " + host)
        else:
            lines.append("Host: " + host + ":" + str(port))
    if not has_ae:
        lines.append("Accept-Encoding: identity")
    if not has_cl and not has_te:
        cl = _content_length(method, body)
        if cl >= 0:
            lines.append("Content-Length: " + str(cl))
    if headers is not None:
        for k, v in headers.items():
            lines.append(k + ": " + v)
    data = b""
    for ln in lines:
        data = data + ln.encode() + b"\r\n"
    data = data + b"\r\n"
    if body is not None:
        data = data + body
    return data


@dynamic
class _Connection(Protocol):
    """The connection surface a caller (requests/urllib) drives. Both
    HTTPConnection and HTTPSConnection satisfy it, so a caller can hold either
    behind one `Box[_Connection]` and dispatch virtually -- the workaround for
    TPy's static method dispatch (a base-typed reference to a subclass would
    call the base method)."""
    def connect(self) -> None: ...
    def request(self, method: str, url: str, body: bytes | None = None,
                headers: dict[str, str] | None = None) -> None: ...
    def getresponse(self) -> Own[HTTPResponse]: ...
    def close(self) -> None: ...


class HTTPConnection(_Connection):
    """A persistent plaintext HTTP/1.1 connection to (host, port).

    The socket stays open across request()/getresponse() cycles; the caller
    drains each response, checks its `will_close`, and calls close() when the
    server ended reuse. request() after close() reconnects."""

    host: str
    port: Int32
    timeout: float | None
    sock: socket.socket | None
    _method: str

    def __init__(self, host: str, port: Int32 = HTTP_PORT,
                 timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None
        self._method = ""

    def connect(self) -> None:
        if self.sock is None:
            self.sock = socket.create_connection((self.host, self.port),
                                                 self.timeout)

    def request(self, method: str, url: str, body: bytes | None = None,
                headers: dict[str, str] | None = None) -> None:
        self.connect()
        self._method = method
        data = _build_request(method, url, body, headers, self.host,
                              self.port, HTTP_PORT)
        if self.sock is None:
            raise HTTPException("Connection not established")
        self.sock.sendall(data)

    def getresponse(self) -> Own[HTTPResponse]:
        if self.sock is None:
            raise HTTPException("Connection not established")
        resp = HTTPResponse(self.sock.makefile("rb"), self._method)
        resp.begin()
        return resp

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None


class HTTPSConnection(_Connection):
    """A persistent HTTP/1.1 connection over TLS -- HTTPConnection's flow run
    through an `ssl.SSLSocket` instead of a bare socket (same keep-alive and
    reconnect-after-close contract).

    Secure by default: with no `context`, an `ssl.create_default_context()`
    verifies the chain and checks the hostname (so it needs a trust store --
    `ssl` has no bundled CA bundle yet, so a real server requires a context
    with `load_verify_locations`). A sibling of HTTPConnection, not a subclass
    (TPy's static dispatch would not route a base reference here); both nominally
    inherit `_Connection` for virtual dispatch through a `Box[_Connection]`.
    """
    host: str
    port: Int32
    timeout: float | None
    _context: ssl.SSLContext
    _tls: ssl.SSLSocket | None
    _method: str

    def __init__(self, host: str, port: Int32 = HTTPS_PORT,
                 timeout: float | None = None,
                 context: ssl.SSLContext | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        # A caller-supplied context is captured by value (copied) -- it is a
        # small config record (verify mode + hostname flag + CA path), so the
        # connection snapshots its settings rather than aliasing the caller's.
        self._context = (copy(context) if context is not None
                         else ssl.create_default_context())
        self._tls = None
        self._method = ""

    def connect(self) -> None:
        if self._tls is None:
            sock = socket.create_connection((self.host, self.port),
                                            self.timeout)
            self._tls = self._context.wrap_socket(sock, self.host)

    def request(self, method: str, url: str, body: bytes | None = None,
                headers: dict[str, str] | None = None) -> None:
        self.connect()
        self._method = method
        data = _build_request(method, url, body, headers, self.host,
                              self.port, HTTPS_PORT)
        if self._tls is None:
            raise HTTPException("Connection not established")
        self._tls.sendall(data)

    def getresponse(self) -> Own[HTTPResponse]:
        if self._tls is None:
            raise HTTPException("Connection not established")
        resp = HTTPResponse(self._tls.makefile(), self._method)
        resp.begin()
        return resp

    def close(self) -> None:
        if self._tls is not None:
            self._tls.close()
            self._tls = None
