# urllib.request -- a simplified urlopen() over http.client (pure TPy).
#
# CPython's urllib.request is built on a dynamic opener/handler stack
# (build_opener, HTTPHandler, HTTPRedirectHandler, ...) that does not fit a
# static compiler. This is a deliberately simplified, signature-compatible
# urlopen that calls http.client directly. Divergences (declarable):
#   - No opener/handler machinery, no install_opener/build_opener.
#   - No redirect following, no proxies, no auth handlers.
#   - http and https both work (https routes to HTTPSConnection on 443; `context`
#     is the TLS context, like CPython -- but keyword-only there, positional
#     here). The default context trusts the vendored Mozilla roots plus the
#     system CA bundle; `context` with load_verify_locations adds custom CAs.
#   - `timeout` (seconds) is honored for connect/recv/send (None = blocking);
#     CPython's `_GLOBAL_DEFAULT_TIMEOUT` sentinel / setdefaulttimeout() is not
#     reproduced (default is plain blocking).
#   - Returns an http.client.HTTPResponse; .geturl()/.info()/.getcode() are not
#     provided (use .status / .reason / .read() / .getheader()).
# For a higher-level API see tplib.requests.
# tpy: cpp_namespace("tpystd::urllib::request")
from __future__ import annotations
from typing import Final
from tpy import int32, Own, String
from tplib import Box
import ssl
from http.client import HTTPConnection, HTTPSConnection, HTTPResponse, _Connection
from urllib.parse import urlsplit


HTTP_PORT: Final[int32] = 80
HTTPS_PORT: Final[int32] = 443


# Subclasses OSError like CPython's URLError, so `except OSError` catches it.
class URLError(OSError):
    def __init__(self, reason: String = "") -> None:
        super().__init__(reason)


def urlopen(url: str, data: bytes | None = None,
            timeout: float | None = None,
            context: ssl.SSLContext | None = None) -> Own[HTTPResponse]:
    # `context` mirrors CPython's urlopen(context=...) for https (default
    # verification when None, against the vendored Mozilla roots + the system
    # CA bundle); pass a context with load_verify_locations for custom CAs.
    return _urlopen(url, data, timeout, context, None)


def _urlopen(url: str, data: bytes | None, timeout: float | None,
             context: ssl.SSLContext | None,
             injected: Own[Box[_Connection]] | None) -> Own[HTTPResponse]:
    # `injected` is an offline test seam: a pre-connected `Box[_Connection]` used
    # instead of dialing from the URL (the suite can't do real connects). It
    # stays off the public `urlopen` signature -- only this internal impl takes
    # it. The response reader outlives the dropped connection (a dup'd fd for
    # http, the Rc-shared session for https), so the returned response is valid.
    parts = urlsplit(url)
    scheme = parts.scheme
    # Require an explicit http/https scheme: an empty scheme (e.g. a protocol-
    # relative "//host/path") would otherwise connect silently, where CPython
    # raises.
    if scheme != "http" and scheme != "https":
        raise URLError("unsupported URL scheme (expected http or https): '"
                       + scheme + "'")
    target: str = parts.path
    if target == "":
        target = "/"
    if parts.query != "":
        target = target + "?" + parts.query

    method: str = "GET"
    if data is not None:
        method = "POST"

    # Headers passed explicitly (None): the call dispatches through the
    # _Connection pure-virtual, which -- unlike the concrete methods -- carries no
    # default args, so a box-routed call must supply every parameter.
    if injected is not None:
        # Test seam: drive the pre-connected box directly (avoids moving it out
        # of the narrowed Optional into a shared local).
        injected.request(method, target, data, None)
        return injected.getresponse()

    host = parts.hostname
    if host is None:
        raise URLError("no host in URL: " + url)
    pnum = parts.port
    # Box[_Connection] so http and https share request()/getresponse().
    if scheme == "https":
        hport: int32 = HTTPS_PORT
        if pnum is not None:
            hport = int32(pnum)
        conn: Box[_Connection] = Box(HTTPSConnection(host, hport, timeout, context))
    else:
        port: int32 = HTTP_PORT
        if pnum is not None:
            port = int32(pnum)
        conn = Box(HTTPConnection(host, port, timeout))
    conn.request(method, target, data, None)
    return conn.getresponse()
