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
# Not supported (yet): timeout=, redirect following, cookies, multipart files,
# streaming (stream=/iter_content), proxies, TLS/HTTPS, auth schemes beyond
# Basic.
# tpy: cpp_namespace("tpystd::tplib::requests")
from __future__ import annotations
from typing import Final
from tpy import Int32, Own, String
from http.client import HTTPConnection
from urllib.parse import urlsplit, urlencode
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


class Response:
    """The result of an HTTP request -- the body is fully read into `content`."""

    status_code: Int32
    reason: str
    url: str
    headers: dict[str, str]
    content: bytes

    def __init__(self, status_code: Int32, reason: str, url: str,
                 headers: Own[dict[str, str]], content: bytes) -> None:
        self.status_code = status_code
        self.reason = reason
        self.url = url
        self.headers = headers
        self.content = content

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

    conn.request(method, target, body, hdrs)
    resp = conn.getresponse()
    content = resp.read()
    status = resp.status
    reason = resp.reason
    out_headers: dict[str, str] = {}
    for kv in resp.getheaders():
        out_headers[kv[0]] = kv[1]
    conn.close()
    return Response(status, reason, url, out_headers, content)


def _connect(url: str) -> Own[HTTPConnection]:
    parts = urlsplit(url)
    host = parts.hostname
    if host is None:
        raise ConnectionError("No host in URL: " + url)
    port: Int32 = DEFAULT_HTTP_PORT
    pnum = parts.port
    if pnum is not None:
        port = Int32(pnum)
    return HTTPConnection(host, port)


def request(method: str, url: str, params: dict[str, str] | None = None,
            data: bytes | None = None, json: JsonValue | None = None,
            headers: dict[str, str] | None = None,
            auth: tuple[str, str] | None = None) -> Own[Response]:
    conn = _connect(url)
    return _request_on(conn, method, url, params, data, json, headers, auth)


def get(url: str, params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None) -> Own[Response]:
    return request("GET", url, params, None, None, headers, auth)


def head(url: str, params: dict[str, str] | None = None,
         headers: dict[str, str] | None = None,
         auth: tuple[str, str] | None = None) -> Own[Response]:
    return request("HEAD", url, params, None, None, headers, auth)


def post(url: str, data: bytes | None = None, json: JsonValue | None = None,
         params: dict[str, str] | None = None,
         headers: dict[str, str] | None = None,
         auth: tuple[str, str] | None = None) -> Own[Response]:
    return request("POST", url, params, data, json, headers, auth)


def put(url: str, data: bytes | None = None, json: JsonValue | None = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None) -> Own[Response]:
    return request("PUT", url, params, data, json, headers, auth)


def patch(url: str, data: bytes | None = None, json: JsonValue | None = None,
          params: dict[str, str] | None = None,
          headers: dict[str, str] | None = None,
          auth: tuple[str, str] | None = None) -> Own[Response]:
    return request("PATCH", url, params, data, json, headers, auth)


def delete(url: str, params: dict[str, str] | None = None,
           headers: dict[str, str] | None = None,
           auth: tuple[str, str] | None = None) -> Own[Response]:
    return request("DELETE", url, params, None, None, headers, auth)


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

    def __init__(self) -> None:
        self.headers = {}
        self.params = {}
        self.auth = None
        self.connection = None

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

    def request(self, method: str, url: str,
                params: dict[str, str] | None = None,
                data: bytes | None = None, json: JsonValue | None = None,
                headers: dict[str, str] | None = None,
                auth: tuple[str, str] | None = None) -> Own[Response]:
        merged_headers = self._merge_headers(headers)
        merged_params = self._merge_params(params)
        use_auth = auth
        if use_auth is None:
            use_auth = self.auth
        if self.connection is not None:
            # Clear even if the request raises: the connection is single-use
            # (Connection: close) and must not be reused after a failure.
            try:
                return _request_on(self.connection, method, url, merged_params,
                                   data, json, merged_headers, use_auth)
            finally:
                self.connection = None
        conn = _connect(url)
        return _request_on(conn, method, url, merged_params, data, json,
                           merged_headers, use_auth)

    def get(self, url: str, params: dict[str, str] | None = None,
            headers: dict[str, str] | None = None) -> Own[Response]:
        return self.request("GET", url, params, None, None, headers, None)

    def post(self, url: str, data: bytes | None = None,
             json: JsonValue | None = None,
             params: dict[str, str] | None = None,
             headers: dict[str, str] | None = None) -> Own[Response]:
        return self.request("POST", url, params, data, json, headers, None)

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
