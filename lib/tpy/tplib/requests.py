# tplib.requests -- a requests-style HTTP client (pure TPy) over http.client.
#
# Mirrors the third-party `requests` API as closely as a static type system
# allows. Declarable divergences from CPython `requests` (all compile-visible,
# none silent):
#   - params / headers are dict[str, str] (requests accepts many shapes); the
#     request kwargs are a fixed typed set, not arbitrary **kwargs.
#   - data is bytes | dict[str, str]: raw bytes are sent verbatim; a dict is
#     urlencoded (application/x-www-form-urlencoded). No str body / file-like /
#     iterable / list-valued form field. files= (dict[str, FileField]) adds
#     multipart/form-data: each value is a FileField(filename, content,
#     content_type="application/octet-stream") -- so a file part always carries
#     a Content-Type (octet-stream by default, or the one given), unlike a
#     requests bare 2-tuple which omits it. Any dict `data` folds in as plain
#     form parts. The boundary is a fixed token (not randomized), so the wire
#     bytes are deterministic. An empty files={} is falsy (like requests) -- it
#     does not force a multipart body, so data= handling applies. When files= is
#     non-empty a data=bytes body is ignored (only a dict data folds in). A
#     field name / filename with a `"` or CR/LF is percent-escaped, as in
#     requests/urllib3.
#   - json= takes a JsonValue; an inline dict literal must be bound to a
#     JsonValue local first (`payload: JsonValue = {...}`) -- the compiler
#     rejects an implicit concrete-container -> recursive-union conversion.
#   - Response.json() returns an untyped JsonValue (narrow with isinstance);
#     for a known shape use the typed path `Model.from_json(r.text)`
#     (tplib.json @model).
#   - Response.text decodes as UTF-8 (no charset sniffing; .encoding is fixed).
#   - Response.headers is a CaseInsensitiveDict (case-insensitive lookup,
#     original casing preserved for items/keys, repeated headers joined with
#     ", " like CPython requests, and the full mutable-mapping surface incl.
#     `for k in headers` iteration). Narrowings vs CPython's CID, all
#     unavoidable under static typing: == only compares against another CID
#     (a plain-dict compare is a compile error, not False); pop() of an absent
#     key returns the default (None) instead of raising KeyError (no sentinel
#     to mark "no default given"); update()/setdefault() take a CID / a
#     required default only (a non-CID arg / omitted default is a compile error).
#   - Session pools connections per (scheme, host, port, verify) and reuses
#     them across requests (HTTP/1.1 keep-alive); a pooled connection keeps
#     the timeout it was created with (a later per-call timeout= does not
#     re-apply). The assignable `_connection` field injects a single-use
#     connection for one request (offline test seam; never pooled).
#   - A URL with no scheme (e.g. a protocol-relative "//host/path") is not
#     rejected -- it connects via the parsed host, where CPython requests
#     raises MissingSchema. A scheme-less host-only string ("host/path")
#     resolves no host and raises ConnectionError.
#   - timeout= is a single float (seconds) applied to connect/recv/send, or
#     None for no timeout; the requests (connect, read) tuple form is not
#     supported, and a timeout raises requests.Timeout (not a bare OSError).
#   - the exception tree (RequestException -> HTTPError/ConnectionError/
#     Timeout/TooManyRedirects) roots at OSError like CPython requests
#     (IOError/OSError), so `except OSError` catches a requests error.
#   - redirects: allow_redirects= (default True; head() defaults False) follows
#     301/302/303/307/308 via the Location header, resolved against the current
#     URL with urljoin. Response.history holds the intermediate responses and
#     Response.url is the final URL. As in requests, 301/302/303 rewrite the
#     method to GET (POST->GET; 303 always except HEAD) and drop the body;
#     307/308 keep method and body; a cross-host redirect drops Authorization.
#     Exceeding Session.max_redirects (default 30) raises TooManyRedirects.
#     Authorization is dropped across a host/scheme/port change
#     (should_strip_auth). A redirect to a scheme other than http/https (e.g.
#     ftp) raises ConnectionError.
#   - https: an https URL (or redirect target) routes to HTTPSConnection on port
#     443; verify (bool | str) selects the TLS trust (True = verified default,
#     "<path>" = custom CA file, False = no verification). ssl.SSLError is
#     wrapped as requests.SSLError (a ConnectionError). verify=True trusts the
#     vendored Mozilla root bundle, so a public https server verifies with no
#     explicit CA path.
#   - cookies: a `cookies=` dict is sent on the request (unscoped -- sent to
#     every hop of that call), and a Session persists Set-Cookie responses in
#     `Session.cookies` (a CookieJar), resending them domain/path/secure-matched
#     on later requests; `Response.cookies` holds the cookies that response set.
#     Expiry is honored: Max-Age (taking precedence over Expires, per RFC 6265)
#     and an Expires date set the cookie's lifetime; an expired cookie is not
#     sent, and a past expiry / Max-Age<=0 deletes the cookie. Narrowings, all
#     declared: the jar is keyed by name only (a same-name/different-domain
#     collision is last-wins, not both kept); the default cookie path is "/"
#     (not the request-URI directory); the RFC 1123 and RFC 850 Expires forms
#     are parsed (matching CPython's http.cookiejar) but not the asctime form
#     (an asctime Expires is ignored, so the cookie is then session-lifetime);
#     `cookies=` accepts a dict[str, str] only (not a whole CookieJar).
# Not supported (yet): streaming (stream=/iter_content), proxies, auth schemes
# beyond Basic.
# tpy: cpp_namespace("tpystd::tplib::requests")
from __future__ import annotations
from typing import Final, Iterator
from tpy import Int32, Own, String
from tpy.version import version_info as _tpy_version_info
from tplib import Box
from http.client import HTTPConnection, HTTPSConnection, HTTPResponse, _Connection
import ssl
from urllib.parse import urlsplit, urlencode, urljoin
from json import loads, dumps, JsonValue
from datetime import datetime, UTC
import base64
import time


DEFAULT_HTTP_PORT: Final[Int32] = 80
DEFAULT_HTTPS_PORT: Final[Int32] = 443

# Chunk size iter_lines pulls from the raw stream between newline scans. requests
# uses 512 for iter_lines; iter_content has no default (see Response.iter_content).
_ITER_LINES_CHUNK: Final[Int32] = 512

# The multipart/form-data boundary. Fixed (not randomized like urllib3) so the
# emitted wire bytes are deterministic and snapshot-testable; as with urllib3
# there is no scan for the token appearing inside a part's content, which for a
# 30-char marker is not a practical collision risk. Plain `str` (not Final):
# Final[str] lowers to a StrView, which has no .encode() for the body bytes.
_MULTIPART_BOUNDARY: str = "----TPyFormBoundary7MA4YWxkTrZu0gW"


class FileField:
    """One multipart file part: a filename, its content bytes, and a MIME
    content type (defaulting to application/octet-stream). Used as a files=
    value: files={"avatar": FileField("me.png", data, "image/png")}."""

    filename: str
    content: bytes
    content_type: str

    def __init__(self, filename: str, content: Own[bytes],
                 content_type: str = "application/octet-stream") -> None:
        self.filename = filename
        self.content = content
        self.content_type = content_type


class RequestException(OSError):
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


class SSLError(ConnectionError):
    """Raised when the TLS layer fails (cert verification, handshake). Wraps
    `ssl.SSLError`, mirroring how Timeout wraps TimeoutError -- a ConnectionError
    subclass so existing `except ConnectionError` handlers still catch it."""
    def __init__(self, message: String = "") -> None:
        super().__init__(message)


class TooManyRedirects(RequestException):
    """Raised when a request exceeds Session.max_redirects redirect hops."""
    def __init__(self, message: String = "") -> None:
        super().__init__(message)


class CaseInsensitiveDict:
    """Case-insensitive str->str mapping for HTTP headers, mirroring
    requests.structures.CaseInsensitiveDict. Header names are case-insensitive
    per RFC 7230, so `h["Content-Type"]` and `h["content-type"]` are the same
    entry; the original casing is preserved for items()/keys()/iteration
    (last-set wins). pop() of an absent key returns the default (None) rather
    than raising KeyError -- TPy has no sentinel to distinguish "no default"."""

    # lowercased name -> (original-cased name, value): the lowercased key drives
    # case-insensitive lookup while the tuple keeps the caller's casing.
    _store: dict[str, tuple[str, str]]

    def __init__(self) -> None:
        self._store = {}

    def __setitem__(self, key: str, value: str) -> None:
        self._store[key.lower()] = (key, value)

    def __getitem__(self, key: str) -> str:
        return self._store[key.lower()][1]

    def __delitem__(self, key: str) -> None:
        del self._store[key.lower()]

    def __contains__(self, key: str) -> bool:
        return key.lower() in self._store

    def __len__(self) -> Int32:
        return len(self._store)

    def get(self, key: str, default: str | None = None) -> str | None:
        lk = key.lower()
        if lk in self._store:
            return self._store[lk][1]
        return default

    def keys(self) -> Own[list[str]]:
        out: list[str] = []
        for lk in self._store:
            out.append(self._store[lk][0])
        return out

    def values(self) -> Own[list[str]]:
        out: list[str] = []
        for lk in self._store:
            out.append(self._store[lk][1])
        return out

    def items(self) -> Own[list[tuple[str, str]]]:
        out: list[tuple[str, str]] = []
        for lk in self._store:
            pair = self._store[lk]
            out.append((pair[0], pair[1]))
        return out

    def __iter__(self) -> Iterator[str]:
        # Yields the original-cased header names (last-set casing wins). A
        # generator method, so `for k in headers` needs no separate iterator.
        for lk in self._store:
            yield self._store[lk][0]

    def update(self, other: CaseInsensitiveDict) -> None:
        for lk in other._store:
            pair = other._store[lk]
            self[pair[0]] = pair[1]

    def copy(self) -> Own[CaseInsensitiveDict]:
        out = CaseInsensitiveDict()
        for lk in self._store:
            pair = self._store[lk]
            out[pair[0]] = pair[1]
        return out

    def pop(self, key: str, default: str | None = None) -> str | None:
        lk = key.lower()
        if lk in self._store:
            # Own the value before deleting -- a view into _store would dangle
            # once the entry is removed.
            v = String(self._store[lk][1])
            del self._store[lk]
            return v
        return default

    def setdefault(self, key: str, default: str) -> str:
        lk = key.lower()
        if lk in self._store:
            return self._store[lk][1]
        self._store[lk] = (key, default)
        return default

    def popitem(self) -> Own[tuple[str, str]]:
        # Pops the first key (CPython MutableMapping.popitem order). Capture the
        # key under a read-only scan, then delete outside the loop -- deleting
        # mid-iteration would invalidate the iterator.
        lk = ""
        found = False
        for k in self._store:
            lk = k
            found = True
            break
        if not found:
            raise KeyError("popitem(): CaseInsensitiveDict is empty")
        pair = (String(self._store[lk][0]), String(self._store[lk][1]))
        del self._store[lk]
        return pair

    def clear(self) -> None:
        self._store.clear()

    def __eq__(self, other: CaseInsensitiveDict) -> bool:
        # Compare by lowercased key, ignoring original casing (matches
        # requests' CaseInsensitiveDict).
        if len(self._store) != len(other._store):
            return False
        for lk in self._store:
            if lk not in other._store:
                return False
            if self._store[lk][1] != other._store[lk][1]:
                return False
        return True


def _parse_maxage(raw: str) -> tuple[bool, int]:
    # (ok, seconds) for a Set-Cookie Max-Age. RFC 6265: a non-integer value is
    # ignored (ok=False); a <= 0 value means expire immediately.
    try:
        return (True, int(raw.strip()))
    except ValueError:
        return (False, 0)


def _parse_http_date(raw: str) -> tuple[bool, float]:
    # (ok, unix_ts) for a Set-Cookie Expires, matching what CPython's
    # http.cookiejar parses: the RFC 1123 form ("Wdy, DD Mon YYYY HH:MM:SS GMT")
    # and the legacy RFC 850 form ("Weekday, DD-Mon-YY HH:MM:SS GMT",
    # 2-digit year). The asctime form is not parsed (ok=False -> the attribute
    # is ignored and the cookie is session-lifetime), matching CPython. Expires
    # is always GMT, so the naive parse is stamped UTC before the timestamp
    # (a naive datetime.timestamp() would assume system-local time).
    for fmt in ["%a, %d %b %Y %H:%M:%S GMT", "%A, %d-%b-%y %H:%M:%S GMT"]:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return (True, dt.replace(tzinfo=UTC).timestamp())
    return (False, 0.0)


def _path_match(req_path: str, cookie_path: str) -> bool:
    # RFC 6265 5.1.4 path-match: equal, or the cookie path is a prefix ending in
    # "/", or a prefix whose next request-path char is "/". A bare `startswith`
    # would wrongly match `/foobar` against a `/foo` cookie.
    if req_path == cookie_path:
        return True
    if not req_path.startswith(cookie_path):
        return False
    if cookie_path.endswith("/"):
        return True
    return req_path[len(cookie_path)] == '/'


class Cookie:
    """One stored cookie. `domain` encodes the match scope: "" = unscoped (an
    explicit per-call `cookies=` entry, sent to every host), a leading "." =
    domain cookie (suffix match, from `Set-Cookie: Domain=`), otherwise
    host-only (exact host, a `Set-Cookie` with no `Domain=`). A `deleted` marker
    (from `Max-Age<=0`) is carried so a merge into a persisted jar can drop the
    entry; it is never sent and is invisible to the mapping accessors."""

    name: str
    value: str
    domain: str
    path: str
    secure: bool
    deleted: bool
    # Absolute Unix expiry (seconds); 0.0 means a session cookie (no expiry).
    expires_at: float

    def __init__(self, name: str, value: str, domain: str, path: str,
                 secure: bool, deleted: bool, expires_at: float) -> None:
        self.name = name
        self.value = value
        self.domain = domain
        self.path = path
        self.secure = secure
        self.deleted = deleted
        self.expires_at = expires_at

    def copy(self) -> Own[Cookie]:
        return Cookie(self.name, self.value, self.domain, self.path,
                      self.secure, self.deleted, self.expires_at)

    def matches(self, host: str, path: str, is_https: bool, now: float) -> bool:
        if self.deleted:
            return False
        if self.expires_at != 0.0 and now >= self.expires_at:
            return False
        if self.secure and not is_https:
            return False
        if not _path_match(path, self.path):
            return False
        d = self.domain
        if d == "":
            return True
        if d.startswith("."):
            return host == d[1:] or host.endswith(d)
        return host == d

    def pair(self) -> str:
        return self.name + "=" + self.value

    def __eq__(self, other: Cookie) -> bool:
        # Cookie must be Equatable so a `name in jar._store` membership test on a
        # const/readonly jar compiles -- an `in` over a const dict whose value
        # type is not Equatable is rejected (BUGS.md, the const-dict `in` entry).
        return (self.name == other.name and self.value == other.value
                and self.domain == other.domain and self.path == other.path
                and self.secure == other.secure and self.deleted == other.deleted
                and self.expires_at == other.expires_at)


class CookieJar:
    """A cookie store keyed by name (requests.RequestsCookieJar-style dict
    access). Narrowings vs CPython requests, all under static typing and
    declared: keyed by name only (a same-name/different-domain collision is
    last-wins, not both kept); no expiry beyond Max-Age<=0 delete (positive
    Max-Age / Expires are ignored -- live cookies are session-lifetime);
    default cookie path is "/" (not the request-URI directory)."""

    # name -> Cookie; the Cookie carries its own domain/path/secure scope.
    _store: dict[str, Cookie]

    def __init__(self) -> None:
        self._store = {}

    def set(self, name: str, value: str, domain: str = "", path: str = "/",
            secure: bool = False) -> None:
        self._store[name] = Cookie(name, value, domain, path, secure, False, 0.0)

    def __getitem__(self, name: str) -> str:
        # A deleted marker is invisible to the accessors, so treat it as absent.
        if name in self._store and not self._store[name].deleted:
            return self._store[name].value
        raise KeyError(name)

    def __contains__(self, name: str) -> bool:
        return name in self._store and not self._store[name].deleted

    def __len__(self) -> Int32:
        n: Int32 = 0
        for name in self._store:
            if not self._store[name].deleted:
                n += 1
        return n

    def get(self, name: str, default: str | None = None) -> str | None:
        if name in self._store and not self._store[name].deleted:
            return self._store[name].value
        return default

    def keys(self) -> Own[list[str]]:
        out: list[str] = []
        for name in self._store:
            if not self._store[name].deleted:
                out.append(name)
        return out

    def items(self) -> Own[list[tuple[str, str]]]:
        out: list[tuple[str, str]] = []
        for name in self._store:
            if not self._store[name].deleted:
                out.append((name, self._store[name].value))
        return out

    def __iter__(self) -> Iterator[str]:
        # Deleted markers are tombstones, invisible through every accessor
        # (get / in / keys / items) -- iteration must hide them too.
        for name in self._store:
            if not self._store[name].deleted:
                yield name

    def update(self, other: CookieJar) -> None:
        # Merge another jar in. A deleted marker drops the entry here (a server
        # deleting a cookie); otherwise the cookie is copied (rebuilt, so no
        # move out of `other` is needed). Field/method access is on the subscript
        # directly -- binding a named `Cookie` local off a const jar drops const
        # (BUGS.md, the reference-type container-borrow entry).
        for name in other._store:
            if other._store[name].deleted:
                if name in self._store:
                    del self._store[name]
            else:
                self._store[name] = other._store[name].copy()

    def clear(self) -> None:
        self._store.clear()

    def header_for(self, host: str, path: str, is_https: bool,
                   now: float) -> str:
        # The `Cookie:` request-header value for a target: every matching,
        # unexpired cookie's `name=value`, joined with "; ". `now` is the
        # current Unix time (threaded in so the request path reads the clock
        # once, and tests can pin it).
        parts: list[str] = []
        for name in self._store:
            if self._store[name].matches(host, path, is_https, now):
                parts.append(self._store[name].pair())
        return "; ".join(parts)

    def _ingest(self, raw: str, req_host: str, req_path: str,
                now: float) -> None:
        # Parse one Set-Cookie header value into the jar. The first ";"-segment
        # is name=value; the rest are attributes (Domain/Path/Secure/Max-Age/
        # Expires honored; HttpOnly parsed-and-ignored). Defaults: host-only
        # domain (the request host), path "/".
        segs = raw.split(";")
        if len(segs) == 0:
            return
        first = segs[0].strip()
        eq = first.find("=")
        if eq < 0:
            return
        name = first[:eq].strip()
        if name == "":
            return
        value = first[eq + 1:].strip()
        domain_attr: str = ""   # bare Domain= value; "" -> host-only cookie
        path: str = "/"
        secure = False
        # Expiry: Max-Age takes precedence over Expires (RFC 6265). Track each
        # separately and resolve after the loop.
        max_age_set = False
        max_age_secs = 0
        bad_max_age = False
        expires_ts = 0.0
        expires_set = False
        i = 1
        while i < len(segs):
            attr = segs[i].strip()
            i += 1
            aeq = attr.find("=")
            if aeq < 0:
                if attr.lower() == "secure":
                    secure = True
                continue
            an = attr[:aeq].strip().lower()
            av = attr[aeq + 1:].strip()
            if an == "domain":
                d = av
                if d.startswith("."):
                    d = d[1:]
                domain_attr = d
            elif an == "path":
                if av != "":
                    path = av
            elif an == "max-age":
                ok, secs = _parse_maxage(av)
                if ok:
                    max_age_set = True
                    max_age_secs = secs
                else:
                    bad_max_age = True
            elif an == "expires":
                ok2, ts = _parse_http_date(av)
                if ok2:
                    expires_set = True
                    expires_ts = ts
        # A malformed Max-Age discards the whole Set-Cookie (CPython's
        # http.cookiejar marks it a bad cookie), leaving any prior same-name
        # cookie untouched -- not just the attribute ignored.
        if bad_max_age:
            return
        domain: str = req_host   # host-only (exact-host match) by default
        if domain_attr != "":
            # RFC 6265: a Domain attribute must domain-match the responding host,
            # else the cookie is rejected outright -- otherwise a host could
            # plant a cookie scoped to an unrelated domain that the jar would
            # then resend there.
            if req_host == domain_attr or req_host.endswith("." + domain_attr):
                domain = "." + domain_attr
            else:
                return
        # Resolve expiry -> an absolute Unix time (0.0 = session cookie). An
        # already-past expiry becomes a delete marker (drops the cookie and
        # propagates the removal into a persisted jar on merge).
        expires_at = 0.0
        delete = False
        if max_age_set:
            if max_age_secs <= 0:
                delete = True
            else:
                expires_at = now + float(max_age_secs)
        elif expires_set:
            if expires_ts <= now:
                delete = True
            else:
                expires_at = expires_ts
        self._store[name] = Cookie(name, value, domain, path, secure, delete,
                                   expires_at)


class Response:
    """The result of an HTTP request.

    By default the body is fully read into `content` at request time. With
    `stream=True` the body is left unread: `_raw` holds the live HTTPResponse
    and the body is pulled on demand via `iter_content` / `iter_lines` / `raw`.
    The reader outlives the source connection being dropped -- for a plaintext
    connection because `socket.makefile` gives it its own dup'd fd, for TLS
    because the reader shares the session via an `Rc[_SslSession]` refcount.

    Divergences from real requests on a `stream=True` response (all declared):
    - `.content` / `.text` / `.json()` do NOT auto-drain the stream; `.content`
      stays `b""` until the caller consumes it via `iter_content` /
      `iter_lines` / `r.raw.read(-1)` (requests lazily fills `.content` on
      access -- blocked here on a mutable-property-getter compiler feature, see
      TODO.md; reading `.content` before draining silently yields empty).
    - A mid-stream socket failure surfaces as the raw socket exception
      (`ConnectionResetError`, etc.), not a wrapped `requests.ConnectionError`
      (requests re-wraps `raw.stream()` errors; see TODO.md).
    """

    status_code: Int32
    reason: str
    url: str
    headers: CaseInsensitiveDict
    content: bytes
    # Cookies this response set (parsed from its Set-Cookie headers), mirroring
    # requests.Response.cookies. On a redirect chain each hop's response carries
    # its own; the final returned response has the last hop's, and Session
    # accumulates all of them.
    cookies: CookieJar
    # The chain of responses that led here (oldest first); empty when the
    # request was not redirected. The final response carries the whole chain,
    # mirroring requests.Response.history. Recursive (list of Self).
    history: list[Response]
    # The live body reader for a stream=True response; None for a fully-read
    # one. Holding it makes Response non-copyable, which the redirect engine
    # already respects (it only ever moves responses).
    _raw: HTTPResponse | None

    def __init__(self, status_code: Int32, reason: str, url: str,
                 headers: Own[CaseInsensitiveDict], content: bytes,
                 cookies: Own[CookieJar],
                 raw: Own[HTTPResponse] | None = None) -> None:
        self.status_code = status_code
        self.reason = reason
        self.url = url
        self.headers = headers
        self.content = content
        self.cookies = cookies
        self.history = []
        self._raw = raw

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

    @property
    def raw(self) -> HTTPResponse | None:
        # The underlying HTTPResponse for a stream=True response (None otherwise);
        # its `.read(amt)` is the file-like access requests exposes as `.raw`.
        return self._raw

    def iter_content(self, chunk_size: Int32) -> Iterator[bytes]:
        # chunk_size is required (no default): a generator method with a default
        # parameter that narrows an Optional reference-type self field drops the
        # default in codegen (BUGS.md). requests' chunk_size=1 default is
        # pathological anyway -- real callers always pass a size.
        r = self._raw
        if r is not None:
            while True:
                chunk = r.read(chunk_size)
                if len(chunk) == 0:
                    break
                yield chunk
        else:
            # Non-streamed response: chunk over the already-read body, so
            # iter_content works regardless of stream= (matching requests).
            data = self.content
            n = len(data)
            pos: Int32 = 0
            while pos < n:
                end = pos + chunk_size
                if end > n:
                    end = n
                yield data[pos:end]
                pos = end

    def iter_lines(self) -> Iterator[bytes]:
        # Yields body lines with the trailing line terminator stripped, buffering
        # a partial line across chunk boundaries. Splits on "\n" and strips a
        # preceding "\r" (so "\r\n" and "\n" both terminate), matching requests'
        # splitlines-based default. Bytes, not decoded (decode_unicode=False).
        # A lone "\r" (old-Mac) is NOT a terminator here -- declared divergence
        # from requests' full splitlines.
        pending = bytearray()
        for chunk in self.iter_content(_ITER_LINES_CHUNK):
            pending += chunk
            buf = bytes(pending)
            nl = buf.find(b"\n")
            while nl >= 0:
                end = nl
                if end > 0 and buf[end - 1] == 13:  # strip a preceding CR ("\r\n")
                    end = end - 1
                yield buf[:end]
                buf = bytes(buf[nl + 1:])
                nl = buf.find(b"\n")
            pending = bytearray(buf)
        if len(pending) > 0:
            tail = bytes(pending)
            tend = len(tail)
            if tend > 0 and tail[tend - 1] == 13:
                tend = tend - 1
            yield tail[:tend]

    def close(self) -> None:
        # Release the live reader (closing its dup'd fd). Idempotent; a no-op on
        # a non-streamed response.
        self._raw = None

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


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


def _escape_header_param(value: str) -> str:
    # A field name / filename goes into a Content-Disposition quoted param, so a
    # `"` or CR/LF would break the header (or inject into the wire body). Percent-
    # escape those three, as requests/urllib3 do; other bytes pass through.
    return value.replace('"', "%22").replace("\r", "%0D").replace("\n", "%0A")


def _multipart_body(data: dict[str, str] | None,
                    files: dict[str, FileField]) -> Own[bytes]:
    # RFC 7578 multipart/form-data. Any dict `data` entries become plain form
    # parts (no filename); each `files` entry is a file part carrying its
    # filename and content type (application/octet-stream unless the FileField
    # set one).
    bnd = _MULTIPART_BOUNDARY.encode()
    body = bytearray()
    if data is not None:
        for kv in data.items():
            body += b"--" + bnd + b"\r\n"
            body += (b'Content-Disposition: form-data; name="'
                     + _escape_header_param(kv[0]).encode() + b'"\r\n\r\n')
            body += kv[1].encode() + b"\r\n"
    for k in files:
        f = files[k]
        body += b"--" + bnd + b"\r\n"
        body += (b'Content-Disposition: form-data; name="'
                 + _escape_header_param(k).encode() + b'"; filename="'
                 + _escape_header_param(f.filename).encode() + b'"\r\n')
        body += b"Content-Type: " + f.content_type.encode() + b"\r\n\r\n"
        body += f.content + b"\r\n"
    body += b"--" + bnd + b"--\r\n"
    return bytes(body)


def _encode_body(data: bytes | dict[str, str] | None,
                 files: dict[str, FileField] | None,
                 json: JsonValue | None) -> Own[tuple[bytes | None, str | None]]:
    # Resolve (data, files, json) into the wire body and the Content-Type it
    # implies (None = set no default, leaving it to the caller's headers). A raw
    # bytes `data` carries no implied type; a dict `data` is urlencoded; `files`
    # (folding any dict `data` in) is multipart; `json` is JSON. A falsy `data`
    # (None / empty dict / empty bytes) is treated as no body -- matching
    # requests' `elif data:` truthiness, so an empty dict is not an empty form
    # and a `json=` body still fires alongside one. An empty `files` dict is
    # likewise falsy (requests gates on `if files:`), so it does not force a
    # multipart body -- data= handling applies instead.
    if files is not None and len(files) > 0:
        form: dict[str, str] | None = None
        if data is not None and isinstance(data, dict) and len(data) > 0:
            form = data
        return (_multipart_body(form, files),
                "multipart/form-data; boundary=" + _MULTIPART_BOUNDARY)
    if data is not None:
        if isinstance(data, dict):
            if len(data) > 0:
                return (urlencode(data).encode(),
                        "application/x-www-form-urlencoded")
        else:
            # `else` (not `elif len...`) so `data` narrows to bytes here -- an
            # isinstance-false narrows the else arm but not an elif condition.
            if len(data) > 0:
                return (data, None)
    if json is not None:
        return (dumps(json).encode(), "application/json")
    return (None, None)


def _prepare_headers(headers: dict[str, str] | None,
                     auth: tuple[str, str] | None,
                     content_type: str | None) -> Own[dict[str, str]]:
    out: dict[str, str] = {}
    if headers is not None:
        for kv in headers.items():
            out[kv[0]] = kv[1]
    # A default User-Agent (overridable by the caller), as CPython requests
    # sends one -- some servers (e.g. the GitHub API) reject UA-less requests.
    has_ua = False
    for k in out:
        if k.lower() == "user-agent":
            has_ua = True
            break
    if not has_ua:
        # major.minor (not full version) so a patch/dev bump doesn't churn it.
        # TODO: hoist to a module constant computed once, when tpyc can
        # const-fold a str-concat Final initializer (today it rejects it -- see
        # BUGS.md); until then this rebuilds the tiny string per request.
        out["User-Agent"] = ("tpy-requests/" + str(_tpy_version_info[0])
                             + "." + str(_tpy_version_info[1]))
    if auth is not None:
        out["Authorization"] = _basic_auth_header(auth[0], auth[1])
    if content_type is not None and "Content-Type" not in out:
        out["Content-Type"] = content_type
    return out


def _is_redirect(status: Int32) -> bool:
    return (status == 301 or status == 302 or status == 303
            or status == 307 or status == 308)


def _host_of(url: str) -> str:
    h = urlsplit(url).hostname
    if h is None:
        return ""
    return h


def _should_strip_auth(old_url: str, new_url: str) -> bool:
    # Mirrors requests.Session.should_strip_auth: drop Authorization across a
    # redirect unless host, scheme, and port all match -- so a scheme change or
    # port change (even same host) strips, preventing a credential leak on an
    # https->http downgrade or a same-host non-default-port hop. The one
    # exception (requests back-compat): a same-host http->https upgrade on the
    # standard ports keeps auth. Ports compared raw (None vs int), as CPython does.
    o = urlsplit(old_url)
    n = urlsplit(new_url)
    if o.hostname != n.hostname:
        return True
    op = o.port
    np = n.port
    if (o.scheme == "http" and (op is None or op == 80)
            and n.scheme == "https" and (np is None or np == 443)):
        return False
    if o.scheme != n.scheme:
        return True
    if op is None and np is None:
        return False
    if op is None or np is None:
        return True
    return op != np


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


def _send_recv(conn: Box[_Connection], method: str, target: str,
               body: bytes | None, hdrs: dict[str, str],
               url: str) -> Own[HTTPResponse]:
    # Send the request and read the response head, re-wrapping socket errors
    # into the requests surface. Order matters -- each arm's exception is an
    # OSError subclass and the first matching handler wins: ssl.SSLError
    # (TLS/cert) before TimeoutError (a timed-out connect) before the generic
    # OSError (CPython's SSLError/Timeout/ConnectionError split).
    #
    # Kept a separate function (not an inline try) so its result binds to a
    # normal local in _request_on: a try-block-scoped Own local is stored in
    # optional form and cannot be MOVED into the streamed Response's raw field
    # afterwards -- it would silently copy, which the non-copyable HTTPResponse
    # forbids. See BUGS.md on try-scoped-local moves.
    try:
        conn.request(method, target, body, hdrs)
        return conn.getresponse()
    except ssl.SSLError as e:
        conn.close()
        raise SSLError("TLS error for " + url + ": " + str(e))
    except TimeoutError:
        conn.close()
        raise Timeout("request timed out: " + url)
    except OSError:
        conn.close()
        raise ConnectionError("connection failed: " + url)


def _request_on(conn: Box[_Connection], method: str, url: str,
                params: dict[str, str] | None,
                data: bytes | dict[str, str] | None,
                files: dict[str, FileField] | None,
                json: JsonValue | None, headers: dict[str, str] | None,
                auth: tuple[str, str] | None,
                send_cookies: CookieJar,
                stream: bool = False, follow: bool = False) -> Own[Response]:
    # By default reads the full body, then closes the connection only when the
    # server ended keep-alive (will_close) or the request failed -- a still-open
    # connection is reusable and the caller may pool it. With stream=True the
    # body is left unread and the live HTTPResponse is moved into the Response;
    # the caller then drops (never pools) the connection, since its socket is
    # mid-body -- the response reader holds its own dup'd fd and survives.
    # stream is honored only on the terminal response: a followed redirect
    # (follow and a 3xx carrying Location) is always drained + poolable, so only
    # its status/headers matter, matching requests (which streams only the last).
    parts = urlsplit(url)
    target = _merge_query(parts.path, parts.query, params)
    host = parts.hostname
    if host is None:
        host = ""
    req_path = parts.path
    if req_path == "":
        req_path = "/"
    is_https = parts.scheme == "https"

    body, body_ctype = _encode_body(data, files, json)
    hdrs = _prepare_headers(headers, auth, body_ctype)
    # One clock read per request drives both the send-side expiry filter and
    # the Set-Cookie expiry resolution below.
    now = time.time()
    # Attach matching cookies unless the caller set a Cookie header explicitly.
    if "Cookie" not in hdrs:
        cookie_header = send_cookies.header_for(host, req_path, is_https, now)
        if cookie_header != "":
            hdrs["Cookie"] = cookie_header

    # resp binds to a normal (non-try-scoped) local so it can be moved into the
    # streamed Response below; _send_recv owns the send/receive try + rewrapping.
    resp = _send_recv(conn, method, target, body, hdrs, url)
    # A followable redirect is never streamed (drained + poolable below); only a
    # terminal response honors stream. Status + Location are eager (read by
    # getresponse), so this decision needs no body read.
    followable = (follow and _is_redirect(resp.status)
                  and resp.getheader("location") is not None)
    do_stream = stream and not followable
    status = resp.status
    reason = resp.reason
    will_close = resp.will_close
    # Materialize the header list before the loop so resp is not borrowed across
    # it (the loop would otherwise extend resp's live range past the move below).
    all_headers = resp.getheaders()
    out_headers = CaseInsensitiveDict()
    resp_cookies = CookieJar()
    for kv in all_headers:
        # Parse Set-Cookie from the raw header (before the ", "-join below --
        # a cookie's Expires value contains a comma, so a joined Set-Cookie is
        # unparseable). Each Set-Cookie is scoped to the request host/path.
        if kv[0].lower() == "set-cookie":
            resp_cookies._ingest(kv[1], host, req_path, now)
        # Join repeated header names with ", " rather than last-wins, matching
        # CPython requests (urllib3's HTTPHeaderDict).
        existing = out_headers.get(kv[0])
        if existing is not None:
            out_headers[kv[0]] = existing + ", " + kv[1]
        else:
            out_headers[kv[0]] = kv[1]
    if do_stream:
        # Keep the reader alive; the connection is dropped (not pooled) by the
        # caller since its socket is mid-body. Move resp into the Response.
        return Response(status, reason, url, out_headers, b"", resp_cookies,
                        resp)
    # Non-stream: read the full body, re-wrapping read errors the same way the
    # send phase does.
    try:
        content = resp.read()
    except ssl.SSLError as e:
        conn.close()
        raise SSLError("TLS error for " + url + ": " + str(e))
    except TimeoutError:
        conn.close()
        raise Timeout("request timed out: " + url)
    except OSError:
        conn.close()
        raise ConnectionError("connection failed: " + url)
    if will_close:
        conn.close()
    return Response(status, reason, url, out_headers, content, resp_cookies)


def _ssl_context_for(verify: bool | str) -> Own[ssl.SSLContext]:
    # verify=True -> verified default context; verify="<path>" -> trust that CA
    # file; verify=False -> disable verification (check_hostname must be cleared
    # before CERT_NONE, or the context rejects the combination, as in CPython).
    ctx = ssl.create_default_context()
    if isinstance(verify, str):
        ctx.load_verify_locations(verify)
        return ctx
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _connect(url: str, timeout: float | None = None,
             verify: bool | str = True) -> Own[Box[_Connection]]:
    parts = urlsplit(url)
    host = parts.hostname
    if host is None:
        raise ConnectionError("No host in URL: " + url)
    pnum = parts.port
    # Box sites are rvalues: a nominal @dynamic conformer can't be moved into
    # Box from a named local (slicing guard), so build the connection inline.
    if parts.scheme == "https":
        hport: Int32 = DEFAULT_HTTPS_PORT
        if pnum is not None:
            hport = Int32(pnum)
        return Box(HTTPSConnection(host, hport, timeout, _ssl_context_for(verify)))
    port: Int32 = DEFAULT_HTTP_PORT
    if pnum is not None:
        port = Int32(pnum)
    return Box(HTTPConnection(host, port, timeout))


def _pool_key(url: str, verify: bool | str) -> str:
    # One pooled connection per (scheme, host, port) -- plus the TLS trust
    # selection, so reusing a socket never silently changes what a request
    # trusts (a different `verify` gets its own connection).
    parts = urlsplit(url)
    scheme = parts.scheme
    host = parts.hostname
    if host is None:
        host = ""
    port: Int32 = DEFAULT_HTTPS_PORT if scheme == "https" else DEFAULT_HTTP_PORT
    pnum = parts.port
    if pnum is not None:
        port = Int32(pnum)
    # isinstance + early return (not elif): the elif arm's `not verify` on the
    # un-narrowed bool|str union miscompiles (BUGS.md); _ssl_context_for uses
    # the same return-based shape.
    if isinstance(verify, str):
        return scheme + "|" + host + "|" + str(port) + "|path:" + verify
    vtok: str = "on"
    if not verify:
        vtok = "off"
    return scheme + "|" + host + "|" + str(port) + "|" + vtok


def request(method: str, url: str, params: dict[str, str] | None = None,
            data: bytes | dict[str, str] | None = None,
            json: JsonValue | None = None,
            headers: dict[str, str] | None = None,
            auth: tuple[str, str] | None = None,
            timeout: float | None = None,
            allow_redirects: bool = True,
            verify: bool | str = True,
            cookies: dict[str, str] | None = None,
            files: dict[str, FileField] | None = None,
            stream: bool = False
            ) -> Own[Response]:
    # A fresh Session per call, like CPython requests' module-level API (its
    # pool dies with the call too; same-host redirect hops still reuse the
    # pooled connection within the call). Routing through Session keeps the
    # redirect engine in one place. files= is last so the existing positional
    # param order (params/data/json/headers/...) is preserved for callers.
    s = Session()
    return s.request(method, url, params, data, json, headers, auth, timeout,
                     allow_redirects, verify, cookies, files, stream)


def get(url: str, params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
        verify: bool | str = True,
        cookies: dict[str, str] | None = None,
        stream: bool = False) -> Own[Response]:
    return request("GET", url, params, None, None, headers, auth, timeout,
                   allow_redirects, verify, cookies, None, stream)


def head(url: str, params: dict[str, str] | None = None,
         headers: dict[str, str] | None = None,
         auth: tuple[str, str] | None = None,
         timeout: float | None = None,
         allow_redirects: bool = False,
         verify: bool | str = True,
         cookies: dict[str, str] | None = None,
         stream: bool = False) -> Own[Response]:
    return request("HEAD", url, params, None, None, headers, auth, timeout,
                   allow_redirects, verify, cookies, None, stream)


def post(url: str, data: bytes | dict[str, str] | None = None,
         json: JsonValue | None = None,
         params: dict[str, str] | None = None,
         headers: dict[str, str] | None = None,
         auth: tuple[str, str] | None = None,
         timeout: float | None = None,
         allow_redirects: bool = True,
         verify: bool | str = True,
         cookies: dict[str, str] | None = None,
         files: dict[str, FileField] | None = None,
         stream: bool = False
         ) -> Own[Response]:
    return request("POST", url, params, data, json, headers, auth, timeout,
                   allow_redirects, verify, cookies, files, stream)


def put(url: str, data: bytes | dict[str, str] | None = None,
        json: JsonValue | None = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
        verify: bool | str = True,
        cookies: dict[str, str] | None = None,
        files: dict[str, FileField] | None = None,
        stream: bool = False
        ) -> Own[Response]:
    return request("PUT", url, params, data, json, headers, auth, timeout,
                   allow_redirects, verify, cookies, files, stream)


def patch(url: str, data: bytes | dict[str, str] | None = None,
          json: JsonValue | None = None,
          params: dict[str, str] | None = None,
          headers: dict[str, str] | None = None,
          auth: tuple[str, str] | None = None,
          timeout: float | None = None,
          allow_redirects: bool = True,
          verify: bool | str = True,
          cookies: dict[str, str] | None = None,
          files: dict[str, FileField] | None = None,
          stream: bool = False
          ) -> Own[Response]:
    return request("PATCH", url, params, data, json, headers, auth, timeout,
                   allow_redirects, verify, cookies, files, stream)


def delete(url: str, params: dict[str, str] | None = None,
           headers: dict[str, str] | None = None,
           auth: tuple[str, str] | None = None,
           timeout: float | None = None,
           allow_redirects: bool = True,
           verify: bool | str = True,
           cookies: dict[str, str] | None = None,
           stream: bool = False) -> Own[Response]:
    return request("DELETE", url, params, None, None, headers, auth, timeout,
                   allow_redirects, verify, cookies, None, stream)


class Session:
    """Default headers/params reused across requests, plus connection pooling.

    `headers` and `params` are merged into every request (per-call values win).
    Connections are pooled per `_pool_key` (scheme|host|port|verify): a request
    pops the pooled connection for its target, and puts it back afterwards --
    keep-alive reuse when the socket survived, a lazy reconnect (inside
    `HTTPConnection.connect`) when it did not. A pooled connection keeps the
    timeout it was created with; a later per-call `timeout=` does not re-apply
    to it.
    """

    headers: dict[str, str]
    params: dict[str, str]
    auth: tuple[str, str] | None
    # Cookies persisted across requests: Set-Cookie responses accumulate here and
    # are sent (domain/path/secure-matched) on later requests, like requests'
    # Session.cookies.
    cookies: CookieJar
    # Offline test seam: the tests can't run a threaded loopback server, so they
    # inject a pre-bound connection here instead of letting hop 0 do a real TCP
    # connect. Cleared after use and never pooled (single-use). Held as
    # Box[_Connection] so an injected HTTPConnection or HTTPSConnection both fit.
    # (Pooling tests seed `_pool` directly instead -- a Box cannot be moved out
    # of this Optional field into the pool.)
    _connection: Box[_Connection] | None
    # Offline test seam for redirect hops 1..N (hop 0 uses `_connection`): each
    # hop pops the next queued connection, bypassing the pool.
    _redirect_connections: list[Box[_Connection]]
    _pool: dict[str, Box[_Connection]]
    max_redirects: Int32

    def __init__(self) -> None:
        self.headers = {}
        self.params = {}
        self.auth = None
        self.cookies = CookieJar()
        self._connection = None
        self._redirect_connections = []
        self._pool = {}
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
                      params: dict[str, str] | None,
                      data: bytes | dict[str, str] | None,
                      files: dict[str, FileField] | None,
                      json: JsonValue | None, headers: dict[str, str],
                      auth: tuple[str, str] | None,
                      timeout: float | None, hop: Int32,
                      send_cookies: CookieJar,
                      verify: bool | str = True,
                      stream: bool = False,
                      follow: bool = False) -> Own[Response]:
        # A streamed response (resp._raw is not None) is never pooled: its socket
        # is mid-body, so reuse would interleave a new request into the unread
        # stream. The connection Box is dropped instead (RAII closes its fd; the
        # response reader's own dup keeps the socket alive).
        if hop == 0 and self._connection is not None:
            # Clear even if the request raises -- the injected connection is
            # single-use and must not be reused after a failure.
            try:
                return _request_on(self._connection, method, url, params, data,
                                   files, json, headers, auth, send_cookies,
                                   stream, follow)
            finally:
                self._connection = None
        if len(self._redirect_connections) > 0:
            conn = self._redirect_connections.pop(0)
            return _request_on(conn, method, url, params, data, files, json,
                               headers, auth, send_cookies, stream, follow)
        # Pool pop -> use -> put back. A raise inside _request_on drops the
        # popped/fresh connection (RAII closes the socket); on success a
        # fully-read response goes back in even if will_close closed it -- the
        # pooled entry then acts as a lazy-reconnect handle for the next request
        # to the same target.
        key = _pool_key(url, verify)
        if key in self._pool:
            pooled = self._pool.pop(key)
            resp = _request_on(pooled, method, url, params, data, files, json,
                               headers, auth, send_cookies, stream, follow)
            if resp._raw is None:
                self._pool[key] = pooled
            return resp
        fresh = _connect(url, timeout, verify)
        resp = _request_on(fresh, method, url, params, data, files, json,
                           headers, auth, send_cookies, stream, follow)
        if resp._raw is None:
            self._pool[key] = fresh
        return resp

    def _hop(self, method: str, url: str, params: dict[str, str] | None,
             data: bytes | dict[str, str] | None,
             files: dict[str, FileField] | None,
             json: JsonValue | None,
             headers: dict[str, str], auth: tuple[str, str] | None,
             timeout: float | None, history: Own[list[Response]],
             hop: Int32, follow: bool, send_cookies: CookieJar,
             verify: bool | str = True,
             stream: bool = False) -> Own[Response]:
        # One request, then (when following) recurse on a 3xx Location. Recursion
        # rather than a loop so each `return resp` is a straight-line last use --
        # a loop-carried Own local trips the borrow checker's return guard.
        # `follow` gates whether _request_on may stream this hop: only a terminal
        # (non-followed) response streams, so an intermediate redirect is always
        # drained + poolable and never lands in history holding an open reader.
        resp = self._send_for_hop(method, url, params, data, files, json,
                                  headers, auth, timeout, hop, send_cookies,
                                  verify, stream, follow)
        # Persist cookies this response set (into the Session jar) and feed them
        # to the send jar so a following redirect hop sends the ones that match.
        self.cookies.update(resp.cookies)
        send_cookies.update(resp.cookies)
        if not follow or not _is_redirect(resp.status_code):
            resp.history = history
            return resp
        location = resp.headers.get("location")
        if location is None:
            resp.history = history
            return resp
        if len(history) >= self.max_redirects:
            raise TooManyRedirects("Exceeded " + str(self.max_redirects)
                                   + " redirects for url: " + url)
        next_url = urljoin(url, location)
        next_scheme = urlsplit(next_url).scheme
        if next_scheme != "" and next_scheme != "http" and next_scheme != "https":
            raise ConnectionError("redirect to unsupported scheme '"
                                  + next_scheme + "': " + next_url)
        # Read everything needed off `resp` before appending it -- the append is
        # resp's last use so it moves into history (no copy).
        new_method = _rebuild_method(method, resp.status_code)
        next_auth = auth
        if _should_strip_auth(url, next_url):
            # Don't leak credentials across a host/scheme/port change
            # (requests.rebuild_auth / should_strip_auth).
            if "Authorization" in headers:
                del headers["Authorization"]
            next_auth = None
        history.append(resp)
        # Redirect targets carry their own query in the Location, so the
        # caller's params apply only to the first hop. The data/files/json body
        # is forwarded directly (not via a reassignable local) so the recursive-
        # union `json` param stays read-only (const) up the call chain.
        if new_method != method:
            _drop_body_headers(headers)
            return self._hop(new_method, next_url, None, None, None, None,
                             headers, next_auth, timeout, history, hop + 1, True,
                             send_cookies, verify, stream)
        return self._hop(new_method, next_url, None, data, files, json, headers,
                         next_auth, timeout, history, hop + 1, True,
                         send_cookies, verify, stream)

    def request(self, method: str, url: str,
                params: dict[str, str] | None = None,
                data: bytes | dict[str, str] | None = None,
                json: JsonValue | None = None,
                headers: dict[str, str] | None = None,
                auth: tuple[str, str] | None = None,
                timeout: float | None = None,
                allow_redirects: bool = True,
                verify: bool | str = True,
                cookies: dict[str, str] | None = None,
                files: dict[str, FileField] | None = None,
                stream: bool = False
                ) -> Own[Response]:
        merged_headers = self._merge_headers(headers)
        merged_params = self._merge_params(params)
        use_auth = auth
        if use_auth is None:
            use_auth = self.auth
        # The send jar for this call: the persisted Session cookies plus any
        # per-call cookies= (unscoped -- domain "" sends them to every hop of
        # this call). The per-call ones are NOT persisted into self.cookies.
        send_cookies = CookieJar()
        send_cookies.update(self.cookies)
        if cookies is not None:
            for kv in cookies.items():
                send_cookies.set(kv[0], kv[1])
        history: list[Response] = []
        return self._hop(method, url, merged_params, data, files, json,
                         merged_headers, use_auth, timeout, history, 0,
                         allow_redirects, send_cookies, verify, stream)

    def get(self, url: str, params: dict[str, str] | None = None,
            headers: dict[str, str] | None = None,
            timeout: float | None = None,
            allow_redirects: bool = True,
            verify: bool | str = True,
            cookies: dict[str, str] | None = None,
            stream: bool = False) -> Own[Response]:
        return self.request("GET", url, params, None, None, headers, None,
                            timeout, allow_redirects, verify, cookies, None,
                            stream)

    def post(self, url: str, data: bytes | dict[str, str] | None = None,
             json: JsonValue | None = None,
             params: dict[str, str] | None = None,
             headers: dict[str, str] | None = None,
             timeout: float | None = None,
             allow_redirects: bool = True,
             verify: bool | str = True,
             cookies: dict[str, str] | None = None,
             files: dict[str, FileField] | None = None,
             stream: bool = False
             ) -> Own[Response]:
        return self.request("POST", url, params, data, json, headers, None,
                            timeout, allow_redirects, verify, cookies, files,
                            stream)

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        for conn in self._redirect_connections:
            conn.close()
        for k in self._pool:
            self._pool[k].close()
        self._pool.clear()
