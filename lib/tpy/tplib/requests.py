# tplib.requests -- a requests-style HTTP client (pure TPy) over http.client.
#
# Mirrors the third-party `requests` API as closely as a static type system
# allows. Declarable divergences from CPython `requests` (all compile-visible,
# none silent):
#   - params / headers are dict[str, str] (requests accepts many shapes); the
#     request kwargs are a fixed typed set, not arbitrary **kwargs.
#   - data is bytes (no form-dict / file-like / iterable body).
#   - json= takes a JsonValue; an inline dict literal must be bound to a
#     JsonValue local first (`payload: JsonValue = {...}`) -- the compiler
#     rejects an implicit concrete-container -> recursive-union conversion.
#   - Response.json() returns an untyped JsonValue (narrow with isinstance);
#     for a known shape use the typed path `Model.from_json(r.text)`
#     (tplib.json @model).
#   - Response.text decodes as UTF-8 (no charset sniffing; .encoding is fixed).
#   - Response.headers is a plain dict[str, str] (last value wins), not a
#     case-insensitive multi-dict.
#   - Session keeps default headers/params but cannot truly pool connections:
#     http.client sends `Connection: close`, so each request uses a fresh
#     connection. The assignable `connection` field overrides that for one
#     request (used to inject a socket in tests).
#   - A URL with no scheme (e.g. a protocol-relative "//host/path") is not
#     rejected -- it connects via the parsed host, where CPython requests
#     raises MissingSchema. A scheme-less host-only string ("host/path")
#     resolves no host and raises ConnectionError.
#   - timeout= is a single float (seconds) applied to connect/recv/send, or
#     None for no timeout; the requests (connect, read) tuple form is not
#     supported, and a timeout raises requests.Timeout (not a bare OSError).
#   - the exception tree (RequestException -> HTTPError/ConnectionError/
#     Timeout/TooManyRedirects) subclasses Exception only, NOT OSError; CPython
#     requests roots it at IOError/OSError, so `except OSError` catches a
#     requests error there but not here -- catch RequestException (or a
#     subclass) instead.
#   - redirects: allow_redirects= (default True; head() defaults False) follows
#     301/302/303/307/308 via the Location header, resolved against the current
#     URL with urljoin. Response.history holds the intermediate responses and
#     Response.url is the final URL. As in requests, 301/302/303 rewrite the
#     method to GET (POST->GET; 303 always except HEAD) and drop the body;
#     307/308 keep method and body; a cross-host redirect drops Authorization.
#     Exceeding Session.max_redirects (default 30) raises TooManyRedirects. The
#     one divergence from requests: this client is HTTP-only, so a redirect to a
#     non-http scheme (e.g. https) raises ConnectionError rather than being
#     silently followed.
# Not supported (yet): cookies, multipart files, streaming
# (stream=/iter_content), proxies, TLS/HTTPS, auth schemes beyond Basic.
# tpy: cpp_namespace("tpystd::tplib::requests")
from __future__ import annotations
from typing import Final
from tpy import Int32, Own, String
from http.client import HTTPConnection
from urllib.parse import urlsplit, urlencode, urljoin
from json import loads, dumps, JsonValue
import base64


DEFAULT_HTTP_PORT: Final[Int32] = 80


class RequestException(Exception):
    def __init__(self, message: String = "") -> None:
        super().__init__(message)


class HTTPError(RequestException):
    def __init__(self, message: String = "") -> None:
        super().__init__(message)


class ConnectionError(RequestException):
    def __init__(self, message: String = "") -> None:
        super().__init__(message)


class Timeout(RequestException):
    """Raised when a request times out (the socket-level TimeoutError is
    re-raised as this, matching the `requests` exception surface)."""
    def __init__(self, message: String = "") -> None:
        super().__init__(message)


class TooManyRedirects(RequestException):
    """Raised when a request exceeds Session.max_redirects redirect hops."""
    def __init__(self, message: String = "") -> None:
        super().__init__(message)


class Response:
    """The result of an HTTP request -- the body is fully read into `content`."""

    status_code: Int32
    reason: str
    url: str
    headers: dict[str, str]
    content: bytes
    # The chain of responses that led here (oldest first); empty when the
    # request was not redirected. The final response carries the whole chain,
    # mirroring requests.Response.history. Recursive (list of Self).
    history: list[Response]

    def __init__(self, status_code: Int32, reason: str, url: str,
                 headers: Own[dict[str, str]], content: bytes) -> None:
        self.status_code = status_code
        self.reason = reason
        self.url = url
        self.headers = headers
        self.content = content
        self.history = []

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    @property
    def text(self) -> str:
        return self.content.decode()

    def json(self) -> Own[JsonValue]:
        # `from json import JsonValue` (not qualified json.JsonValue): the
        # qualified alias in a return annotation fails to match loads's own
        # return form across the module boundary.
        return loads(self.content.decode())

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPError(str(self.status_code) + " " + self.reason
                            + " for url: " + self.url)


def _basic_auth_header(user: str, password: str) -> str:
    creds: str = user + ":" + password
    return "Basic " + base64.b64encode(creds.encode()).decode()


def _merge_query(path: str, query: str, params: dict[str, str] | None) -> str:
    target: str = path
    if target == "":
        target = "/"
    combined: str = query
    if params is not None and len(params) > 0:
        extra = urlencode(params)
        if combined == "":
            combined = extra
        else:
            combined = combined + "&" + extra
    if combined != "":
        target = target + "?" + combined
    return target


def _prepare_headers(headers: dict[str, str] | None,
                     auth: tuple[str, str] | None,
                     has_json_body: bool) -> Own[dict[str, str]]:
    out: dict[str, str] = {}
    if headers is not None:
        for kv in headers.items():
            out[kv[0]] = kv[1]
    if auth is not None:
        out["Authorization"] = _basic_auth_header(auth[0], auth[1])
    if has_json_body and "Content-Type" not in out:
        out["Content-Type"] = "application/json"
    return out


def _is_redirect(status: Int32) -> bool:
    return (status == 301 or status == 302 or status == 303
            or status == 307 or status == 308)


def _header_ci(headers: dict[str, str], lower_name: str) -> str | None:
    # Response.headers is a plain (case-preserving) dict, but HTTP header names
    # are case-insensitive -- a server may send "location". Match lowercased.
    for kv in headers.items():
        if kv[0].lower() == lower_name:
            return kv[1]
    return None


def _host_of(url: str) -> str:
    h = urlsplit(url).hostname
    if h is None:
        return ""
    return h


def _drop_body_headers(headers: dict[str, str]) -> None:
    # When a redirect coerces the method to GET the body is dropped, so its
    # content headers must go too (requests purges Content-Type/Length/Transfer-
    # Encoding/Content-Encoding). Case-insensitive: the caller may use any case.
    to_drop: list[str] = []
    for kv in headers.items():
        low = kv[0].lower()
        if (low == "content-type" or low == "content-length"
                or low == "transfer-encoding" or low == "content-encoding"):
            to_drop.append(kv[0])
    for k in to_drop:
        del headers[k]


def _rebuild_method(method: str, status: Int32) -> str:
    # Mirrors requests.Session.rebuild_method: 303 and 302 coerce any non-HEAD
    # method to GET; 301 coerces only POST. 307/308 preserve the method.
    if status == 303 and method != "HEAD":
        return "GET"
    if status == 302 and method != "HEAD":
        return "GET"
    if status == 301 and method == "POST":
        return "GET"
    return method


def _request_on(conn: HTTPConnection, method: str, url: str,
                params: dict[str, str] | None, data: bytes | None,
                json: JsonValue | None, headers: dict[str, str] | None,
                auth: tuple[str, str] | None) -> Own[Response]:
    # Closes the connection after reading: http.client framing is
    # Connection: close, so the connection serves exactly one response.
    parts = urlsplit(url)
    target = _merge_query(parts.path, parts.query, params)

    body: bytes | None = data
    has_json = False
    if data is None and json is not None:
        body = dumps(json).encode()
        has_json = True
    hdrs = _prepare_headers(headers, auth, has_json)

    # Re-wrap socket-level OSerrors into the requests exception surface. The
    # TimeoutError arm must precede the OSError arm: TimeoutError is itself an
    # OSError and the first matching handler wins (CPython's Timeout-vs-
    # ConnectionError split).
    try:
        conn.request(method, target, body, hdrs)
        resp = conn.getresponse()
        content = resp.read()
    except TimeoutError:
        conn.close()
        raise Timeout("request timed out: " + url)
    except OSError:
        conn.close()
        raise ConnectionError("connection failed: " + url)
    status = resp.status
    reason = resp.reason
    out_headers: dict[str, str] = {}
    for kv in resp.getheaders():
        out_headers[kv[0]] = kv[1]
    conn.close()
    return Response(status, reason, url, out_headers, content)


def _connect(url: str, timeout: float | None = None) -> Own[HTTPConnection]:
    parts = urlsplit(url)
    host = parts.hostname
    if host is None:
        raise ConnectionError("No host in URL: " + url)
    port: Int32 = DEFAULT_HTTP_PORT
    pnum = parts.port
    if pnum is not None:
        port = Int32(pnum)
    return HTTPConnection(host, port, timeout)


def request(method: str, url: str, params: dict[str, str] | None = None,
            data: bytes | None = None, json: JsonValue | None = None,
            headers: dict[str, str] | None = None,
            auth: tuple[str, str] | None = None,
            timeout: float | None = None,
            allow_redirects: bool = True) -> Own[Response]:
    # A fresh Session per call (http.client is Connection: close, so there is
    # no pool to lose); routing through it keeps the redirect engine in one
    # place. The empty-default header/param merge is an identity here.
    s = Session()
    return s.request(method, url, params, data, json, headers, auth, timeout,
                     allow_redirects)


def get(url: str, params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True) -> Own[Response]:
    return request("GET", url, params, None, None, headers, auth, timeout,
                   allow_redirects)


def head(url: str, params: dict[str, str] | None = None,
         headers: dict[str, str] | None = None,
         auth: tuple[str, str] | None = None,
         timeout: float | None = None,
         allow_redirects: bool = False) -> Own[Response]:
    return request("HEAD", url, params, None, None, headers, auth, timeout,
                   allow_redirects)


def post(url: str, data: bytes | None = None, json: JsonValue | None = None,
         params: dict[str, str] | None = None,
         headers: dict[str, str] | None = None,
         auth: tuple[str, str] | None = None,
         timeout: float | None = None,
         allow_redirects: bool = True) -> Own[Response]:
    return request("POST", url, params, data, json, headers, auth, timeout,
                   allow_redirects)


def put(url: str, data: bytes | None = None, json: JsonValue | None = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True) -> Own[Response]:
    return request("PUT", url, params, data, json, headers, auth, timeout,
                   allow_redirects)


def patch(url: str, data: bytes | None = None, json: JsonValue | None = None,
          params: dict[str, str] | None = None,
          headers: dict[str, str] | None = None,
          auth: tuple[str, str] | None = None,
          timeout: float | None = None,
          allow_redirects: bool = True) -> Own[Response]:
    return request("PATCH", url, params, data, json, headers, auth, timeout,
                   allow_redirects)


def delete(url: str, params: dict[str, str] | None = None,
           headers: dict[str, str] | None = None,
           auth: tuple[str, str] | None = None,
           timeout: float | None = None,
           allow_redirects: bool = True) -> Own[Response]:
    return request("DELETE", url, params, None, None, headers, auth, timeout,
                   allow_redirects)


class Session:
    """Default headers/params reused across requests.

    `headers` and `params` are merged into every request (per-call values win).
    `connection`, when set, is used for the next request instead of opening one
    from the URL (then cleared) -- the seam tests use to inject a socket. Real
    connection pooling is not provided (http.client is Connection: close).
    """

    headers: dict[str, str]
    params: dict[str, str]
    auth: tuple[str, str] | None
    connection: HTTPConnection | None
    # Connections for redirect hops 1..N (hop 0 uses `connection`): the offline
    # test seam, since each hop needs a fresh connection and there is no real
    # pool. Empty in normal use -- a hop with no queued connection opens one.
    redirect_connections: list[HTTPConnection]
    max_redirects: Int32

    def __init__(self) -> None:
        self.headers = {}
        self.params = {}
        self.auth = None
        self.connection = None
        self.redirect_connections = []
        self.max_redirects = 30

    def _merge_headers(self, headers: dict[str, str] | None) -> Own[dict[str, str]]:
        out: dict[str, str] = {}
        for kv in self.headers.items():
            out[kv[0]] = kv[1]
        if headers is not None:
            for kv in headers.items():
                out[kv[0]] = kv[1]
        return out

    def _merge_params(self, params: dict[str, str] | None) -> Own[dict[str, str]]:
        out: dict[str, str] = {}
        for kv in self.params.items():
            out[kv[0]] = kv[1]
        if params is not None:
            for kv in params.items():
                out[kv[0]] = kv[1]
        return out

    def _send_for_hop(self, method: str, url: str,
                      params: dict[str, str] | None, data: bytes | None,
                      json: JsonValue | None, headers: dict[str, str],
                      auth: tuple[str, str] | None,
                      timeout: float | None, hop: Int32) -> Own[Response]:
        # Each connection is single-use (Connection: close) and is closed inside
        # _request_on, so every hop needs its own.
        if hop == 0 and self.connection is not None:
            # Clear even if the request raises -- the connection is single-use
            # and must not be reused after a failure.
            try:
                return _request_on(self.connection, method, url, params, data,
                                   json, headers, auth)
            finally:
                self.connection = None
        if len(self.redirect_connections) > 0:
            conn = self.redirect_connections.pop(0)
            return _request_on(conn, method, url, params, data, json, headers,
                               auth)
        fresh = _connect(url, timeout)
        return _request_on(fresh, method, url, params, data, json, headers,
                           auth)

    def _hop(self, method: str, url: str, params: dict[str, str] | None,
             data: bytes | None, json: JsonValue | None,
             headers: dict[str, str], auth: tuple[str, str] | None,
             timeout: float | None, history: Own[list[Response]],
             hop: Int32, follow: bool) -> Own[Response]:
        # One request, then (when following) recurse on a 3xx Location. Recursion
        # rather than a loop so each `return resp` is a straight-line last use --
        # a loop-carried Own local trips the borrow checker's return guard.
        resp = self._send_for_hop(method, url, params, data, json, headers, auth,
                                  timeout, hop)
        if not follow or not _is_redirect(resp.status_code):
            resp.history = history
            return resp
        location = _header_ci(resp.headers, "location")
        if location is None:
            resp.history = history
            return resp
        if len(history) >= self.max_redirects:
            raise TooManyRedirects("Exceeded " + str(self.max_redirects)
                                   + " redirects for url: " + url)
        next_url = urljoin(url, location)
        next_scheme = urlsplit(next_url).scheme
        if next_scheme != "" and next_scheme != "http":
            raise ConnectionError("redirect to unsupported scheme '"
                                  + next_scheme + "': " + next_url)
        # Read everything needed off `resp` before appending it -- the append is
        # resp's last use so it moves into history (no copy).
        new_method = _rebuild_method(method, resp.status_code)
        next_auth = auth
        if _host_of(next_url) != _host_of(url):
            # Don't leak credentials to a different host (requests.rebuild_auth).
            if "Authorization" in headers:
                del headers["Authorization"]
            next_auth = None
        history.append(resp)
        # Redirect targets carry their own query in the Location, so the
        # caller's params apply only to the first hop. The data/json body is
        # forwarded directly (not via a reassignable local) so the recursive-
        # union `json` param stays read-only (const) up the call chain.
        if new_method != method:
            _drop_body_headers(headers)
            return self._hop(new_method, next_url, None, None, None, headers,
                             next_auth, timeout, history, hop + 1, True)
        return self._hop(new_method, next_url, None, data, json, headers,
                         next_auth, timeout, history, hop + 1, True)

    def request(self, method: str, url: str,
                params: dict[str, str] | None = None,
                data: bytes | None = None, json: JsonValue | None = None,
                headers: dict[str, str] | None = None,
                auth: tuple[str, str] | None = None,
                timeout: float | None = None,
                allow_redirects: bool = True) -> Own[Response]:
        merged_headers = self._merge_headers(headers)
        merged_params = self._merge_params(params)
        use_auth = auth
        if use_auth is None:
            use_auth = self.auth
        history: list[Response] = []
        return self._hop(method, url, merged_params, data, json, merged_headers,
                         use_auth, timeout, history, 0, allow_redirects)

    def get(self, url: str, params: dict[str, str] | None = None,
            headers: dict[str, str] | None = None,
            timeout: float | None = None,
            allow_redirects: bool = True) -> Own[Response]:
        return self.request("GET", url, params, None, None, headers, None,
                            timeout, allow_redirects)

    def post(self, url: str, data: bytes | None = None,
             json: JsonValue | None = None,
             params: dict[str, str] | None = None,
             headers: dict[str, str] | None = None,
             timeout: float | None = None,
             allow_redirects: bool = True) -> Own[Response]:
        return self.request("POST", url, params, data, json, headers, None,
                            timeout, allow_redirects)

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        for conn in self.redirect_connections:
            conn.close()
