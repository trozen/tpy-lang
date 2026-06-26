# urllib.request -- a simplified urlopen() over http.client (pure TPy).
#
# CPython's urllib.request is built on a dynamic opener/handler stack
# (build_opener, HTTPHandler, HTTPRedirectHandler, ...) that does not fit a
# static compiler. This is a deliberately simplified, signature-compatible
# urlopen that calls http.client directly. Divergences (declarable):
#   - No opener/handler machinery, no install_opener/build_opener.
#   - No redirect following, no proxies, no auth handlers, no timeout (blocked
#     on socket.settimeout), no HTTPS/TLS.
#   - Returns an http.client.HTTPResponse; .geturl()/.info()/.getcode() are not
#     provided (use .status / .reason / .read() / .getheader()).
# For a higher-level API see tplib.requests.
# tpy: cpp_namespace("tpystd::urllib::request")
from __future__ import annotations
from typing import Final
from tpy import Int32, Own, String
from socket import socket
from http.client import HTTPConnection, HTTPResponse
from urllib.parse import urlsplit


HTTP_PORT: Final[Int32] = 80


class URLError(Exception):
    def __init__(self, reason: String = "") -> None:
        super().__init__(reason)


def urlopen(url: str, data: bytes | None = None,
            _sock: Own[socket] | None = None) -> Own[HTTPResponse]:
    # `_sock` is a test seam: an injected socket bound to the built connection
    # (skipping the real TCP connect). The connection is still built from the
    # URL and dropped on return exactly as in normal use, so the returned
    # response's reader (a dup of the socket fd) is exercised the same way.
    parts = urlsplit(url)
    # Require an explicit http scheme: an empty scheme (e.g. a protocol-
    # relative "//host/path") would otherwise connect silently, where CPython
    # raises. No TLS, so https is rejected too.
    if parts.scheme != "http":
        raise URLError("unsupported URL scheme (expected http): '"
                       + parts.scheme + "'")
    target: str = parts.path
    if target == "":
        target = "/"
    if parts.query != "":
        target = target + "?" + parts.query

    method: str = "GET"
    if data is not None:
        method = "POST"

    host = parts.hostname
    if host is None:
        raise URLError("no host in URL: " + url)
    port: Int32 = HTTP_PORT
    pnum = parts.port
    if pnum is not None:
        port = Int32(pnum)

    conn = HTTPConnection(host, port)
    if _sock is not None:
        conn.sock = _sock
    conn.request(method, target, data)
    return conn.getresponse()
