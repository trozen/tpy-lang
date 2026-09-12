# urllib.parse -- split/join URLs and percent-encode query components.
#
# CPython-faithful behaviour for the algorithms, with one deliberate API-shape
# divergence: urlsplit/urlparse return a *record* (SplitResult / ParseResult)
# with named attributes + .geturl()/.hostname/.port/.username/.password, NOT a
# namedtuple. Integer indexing (r[0]) and unpacking (a, b, ... = urlsplit(u))
# are therefore unavailable -- both are compile errors here, never a silent
# divergence. Attribute access (the modern idiom) matches CPython exactly.
#
# Not yet implemented (see STDLIB_ROADMAP.md / TODO.md): parse_qs (dict-of-lists
# form), bytes variants (quote_from_bytes / unquote_to_bytes / SplitResultBytes),
# urldefrag, and the urlencode(doseq=)/quote(encoding=) extra parameters.
#
# Known divergence: unquote of an invalid UTF-8 percent-sequence returns the raw
# bytes, where CPython substitutes U+FFFD (errors='replace'); matching that needs
# a lossy UTF-8 decode the runtime does not yet expose.
# tpy: cpp_namespace("tpystd::urllib::parse")
from tpy import int32, uint8, char, Own

_HEX: bytes = b"0123456789ABCDEF"


# CPython's uses_netloc / uses_relative / uses_params scheme lists, verbatim.
# Module-level sets so each is built once at init (a set literal in the function
# body would reconstruct the set on every call); membership is then an O(1)
# lookup. (TPy tuples aren't iterable, so a set is the natural constant form.)
_USES_NETLOC: set[str] = {"", "ftp", "http", "gopher", "nntp", "telnet", "imap",
                          "wais", "file", "mms", "https", "shttp", "snews",
                          "prospero", "rtsp", "rtsps", "rtspu", "rsync", "svn",
                          "svn+ssh", "sftp", "nfs", "git", "git+ssh", "ws",
                          "wss", "itms-services"}
_USES_RELATIVE: set[str] = {"", "ftp", "http", "gopher", "nntp", "imap", "wais",
                            "file", "https", "shttp", "mms", "prospero", "rtsp",
                            "rtsps", "rtspu", "sftp", "svn", "svn+ssh", "ws",
                            "wss"}
_USES_PARAMS: set[str] = {"", "ftp", "hdl", "prospero", "http", "imap", "https",
                          "shttp", "rtsp", "rtsps", "rtspu", "sip", "sips",
                          "mms", "sftp", "tel"}


def _scheme_uses_netloc(scheme: str) -> bool:
    return scheme in _USES_NETLOC


def _scheme_uses_relative(scheme: str) -> bool:
    return scheme in _USES_RELATIVE


def _scheme_uses_params(scheme: str) -> bool:
    return scheme in _USES_PARAMS


# ---------- byte / char classifiers ----------

def _is_alpha(c: char) -> bool:
    o = ord(c)
    return (o >= 65 and o <= 90) or (o >= 97 and o <= 122)


def _is_alnum(c: char) -> bool:
    o = ord(c)
    return _is_alpha(c) or (o >= 48 and o <= 57)


def _byte_unreserved(c: int32) -> bool:
    # RFC 3986 unreserved set -- always safe, never percent-encoded.
    # ALPHA / DIGIT / '-' / '.' / '_' / '~'.
    if c >= 65 and c <= 90:
        return True
    if c >= 97 and c <= 122:
        return True
    if c >= 48 and c <= 57:
        return True
    return c == 45 or c == 46 or c == 95 or c == 126


def _byte_in(c: int32, chars: str) -> bool:
    for ch in chars:
        if ord(ch) == c:
            return True
    return False


def _hex_val(c: int32) -> int32:
    if c >= 48 and c <= 57:
        return c - 48
    if c >= 65 and c <= 70:
        return c - 55
    if c >= 97 and c <= 102:
        return c - 87
    return -1


# ---------- percent-encoding ----------

def _quote_impl(s: str, safe: str, plus: bool) -> str:
    data = s.encode()
    out = bytearray()
    n = len(data)
    i = 0
    while i < n:
        c = int32(data[i])
        if _byte_unreserved(c) or _byte_in(c, safe):
            out.append(uint8(c))
        elif plus and c == 32:
            out.append(uint8(43))
        else:
            out.append(uint8(37))
            out.append(_HEX[c >> 4])
            out.append(_HEX[c & 0xF])
        i += 1
    return out.decode()


def quote(s: str, safe: str = "/") -> str:
    """Percent-encode `s`, leaving unreserved chars and `safe` chars intact."""
    return _quote_impl(s, safe, False)


def quote_plus(s: str, safe: str = "") -> str:
    """Like quote, but encode spaces as '+' (form-encoding); '/' is not safe."""
    return _quote_impl(s, safe, True)


def _unquote_impl(s: str, plus: bool) -> str:
    data = s.encode()
    out = bytearray()
    n = len(data)
    i = 0
    while i < n:
        c = int32(data[i])
        if c == 37 and i + 2 < n:
            hi = _hex_val(int32(data[i + 1]))
            lo = _hex_val(int32(data[i + 2]))
            if hi >= 0 and lo >= 0:
                out.append(uint8((hi << 4) | lo))
                i += 3
                continue
            out.append(uint8(c))
            i += 1
        elif plus and c == 43:
            out.append(uint8(32))
            i += 1
        else:
            out.append(uint8(c))
            i += 1
    return out.decode()


def unquote(s: str) -> str:
    """Replace %XX escapes with their byte, decoding the result as UTF-8."""
    return _unquote_impl(s, False)


def unquote_plus(s: str) -> str:
    """Like unquote, but also turn '+' into a space (form-decoding)."""
    return _unquote_impl(s, True)


def urlencode(query: dict[str, str]) -> str:
    """Build an application/x-www-form-urlencoded query string.

    Keys and values are quote_plus-encoded; pairs are joined with '&' in the
    dict's insertion order (TPy dicts are ordered, matching CPython)."""
    parts: list[str] = []
    for k in query:
        parts.append(quote_plus(k) + "=" + quote_plus(query[k]))
    return "&".join(parts)


def parse_qsl(qs: str, keep_blank_values: bool = False) -> Own[list[tuple[str, str]]]:
    """Parse a query string into an ordered list of (name, value) pairs.

    Both name and value are unquote_plus-decoded. With keep_blank_values
    False (the default) pairs with an empty value are dropped, matching
    CPython."""
    result: list[tuple[str, str]] = []
    for pair in qs.split("&"):
        if len(pair) == 0:
            continue
        eq = pair.find("=")
        if eq < 0:
            if keep_blank_values:
                result.append((unquote_plus(pair), ""))
            continue
        value = pair[eq + 1:]
        if len(value) == 0 and not keep_blank_values:
            continue
        result.append((unquote_plus(pair[:eq]), unquote_plus(value)))
    return result


# ---------- netloc decomposition (shared by SplitResult / ParseResult) ----------

def _netloc_hostinfo(netloc: str) -> str:
    at = netloc.rfind("@")
    if at >= 0:
        return netloc[at + 1:]
    return netloc


def _hostinfo_host(hostinfo: str) -> str:
    if hostinfo.startswith("["):
        end = hostinfo.find("]")
        if end >= 0:
            return hostinfo[1:end]
    colon = hostinfo.find(":")
    if colon >= 0:
        return hostinfo[:colon]
    return hostinfo


def _hostinfo_port(hostinfo: str) -> str:
    if hostinfo.startswith("["):
        end = hostinfo.find("]")
        if end >= 0:
            after = hostinfo[end + 1:]
            if after.startswith(":"):
                return after[1:]
            return ""
    colon = hostinfo.find(":")
    if colon >= 0:
        return hostinfo[colon + 1:]
    return ""


def _netloc_hostname(netloc: str) -> str | None:
    h = _hostinfo_host(_netloc_hostinfo(netloc))
    if len(h) == 0:
        return None
    return h.lower()


def _all_ascii_digits(s: str) -> bool:
    if len(s) == 0:
        return False
    for c in s:
        o = ord(c)
        if o < 48 or o > 57:
            return False
    return True


def _netloc_port(netloc: str) -> int | None:
    ps = _hostinfo_port(_netloc_hostinfo(netloc))
    if len(ps) == 0:
        return None
    # CPython requires ASCII digits; str.isdigit() also accepts Unicode digits.
    if not _all_ascii_digits(ps):
        raise ValueError("Port could not be cast to integer value")
    p = int(ps)
    if p < 0 or p > 65535:
        raise ValueError("Port out of range 0-65535")
    return p


def _netloc_username(netloc: str) -> str | None:
    at = netloc.rfind("@")
    if at < 0:
        return None
    userinfo = netloc[:at]
    colon = userinfo.find(":")
    if colon >= 0:
        return userinfo[:colon]
    return userinfo


def _netloc_password(netloc: str) -> str | None:
    at = netloc.rfind("@")
    if at < 0:
        return None
    userinfo = netloc[:at]
    colon = userinfo.find(":")
    if colon >= 0:
        return userinfo[colon + 1:]
    return None


# ---------- result records ----------

class SplitResult:
    """Result of urlsplit: scheme://netloc/path?query#fragment.

    Records hold owned copies of each component, so the result safely
    outlives the parsed URL string."""
    scheme: str
    netloc: str
    path: str
    query: str
    fragment: str

    def __init__(self, scheme: str, netloc: str, path: str, query: str,
                 fragment: str) -> None:
        self.scheme = scheme
        self.netloc = netloc
        self.path = path
        self.query = query
        self.fragment = fragment

    @property
    def hostname(self) -> str | None:
        return _netloc_hostname(self.netloc)

    @property
    def port(self) -> int | None:
        return _netloc_port(self.netloc)

    @property
    def username(self) -> str | None:
        return _netloc_username(self.netloc)

    @property
    def password(self) -> str | None:
        return _netloc_password(self.netloc)

    def geturl(self) -> str:
        return urlunsplit((self.scheme, self.netloc, self.path, self.query,
                           self.fragment))


class ParseResult:
    """Result of urlparse: like SplitResult plus the legacy ;params segment."""
    scheme: str
    netloc: str
    path: str
    params: str
    query: str
    fragment: str

    def __init__(self, scheme: str, netloc: str, path: str, params: str,
                 query: str, fragment: str) -> None:
        self.scheme = scheme
        self.netloc = netloc
        self.path = path
        self.params = params
        self.query = query
        self.fragment = fragment

    @property
    def hostname(self) -> str | None:
        return _netloc_hostname(self.netloc)

    @property
    def port(self) -> int | None:
        return _netloc_port(self.netloc)

    @property
    def username(self) -> str | None:
        return _netloc_username(self.netloc)

    @property
    def password(self) -> str | None:
        return _netloc_password(self.netloc)

    def geturl(self) -> str:
        return urlunparse((self.scheme, self.netloc, self.path, self.params,
                           self.query, self.fragment))


# ---------- splitting ----------

def _is_scheme(s: str) -> bool:
    n = len(s)
    if n == 0:
        return False
    if not _is_alpha(s[0]):
        return False
    i = 1
    while i < n:
        c = s[i]
        if not (_is_alnum(c) or c == "+" or c == "-" or c == "."):
            return False
        i += 1
    return True


def _clean(url: str) -> str:
    # CPython removes tab/CR/LF anywhere and lstrips leading control-or-space,
    # but keeps trailing whitespace (significant in path/query/fragment).
    cleaned = url.lstrip().replace("\t", "").replace("\r", "").replace("\n", "")
    return cleaned


def _netloc_end(s: str) -> int32:
    n = len(s)
    i = 0
    while i < n:
        c = s[i]
        if c == "/" or c == "?" or c == "#":
            return i
        i += 1
    return n


def urlsplit(url: str) -> Own[SplitResult]:
    """Split a URL into (scheme, netloc, path, query, fragment)."""
    scheme = ""
    netloc = ""
    query = ""
    fragment = ""
    rest = _clean(url)

    i = rest.find(":")
    if i > 0 and _is_scheme(rest[:i]):
        scheme = rest[:i].lower()
        rest = rest[i + 1:]

    if rest.startswith("//"):
        body = rest[2:]
        end = _netloc_end(body)
        netloc = body[:end]
        rest = body[end:]
        if (("[" in netloc and "]" not in netloc)
                or ("]" in netloc and "[" not in netloc)):
            raise ValueError("Invalid IPv6 URL")

    h = rest.find("#")
    if h >= 0:
        fragment = rest[h + 1:]
        rest = rest[:h]

    q = rest.find("?")
    if q >= 0:
        query = rest[q + 1:]
        rest = rest[:q]

    return SplitResult(scheme, netloc, rest, query, fragment)


def _split_params(path: str) -> tuple[str, str]:
    # ;params attaches to the final path segment only.
    slash = path.rfind("/")
    start = 0 if slash < 0 else slash
    rel = path[start:].find(";")
    if rel < 0:
        return (path, "")
    i = start + rel
    return (path[:i], path[i + 1:])


def urlparse(url: str) -> Own[ParseResult]:
    """Split a URL into (scheme, netloc, path, params, query, fragment)."""
    sr = urlsplit(url)
    params = ""
    path = sr.path
    if _scheme_uses_params(sr.scheme) and ";" in sr.path:
        path, params = _split_params(sr.path)
    return ParseResult(sr.scheme, sr.netloc, path, params, sr.query,
                       sr.fragment)


# ---------- unsplitting ----------

def urlunsplit(components: tuple[str, str, str, str, str]) -> str:
    scheme, netloc, path, query, fragment = components
    url = path
    if len(netloc) > 0 or (len(scheme) > 0 and _scheme_uses_netloc(scheme)
                           and not path.startswith("//")):
        if len(url) > 0 and not url.startswith("/"):
            url = "/" + url
        url = "//" + netloc + url
    if len(scheme) > 0:
        url = scheme + ":" + url
    if len(query) > 0:
        url = url + "?" + query
    if len(fragment) > 0:
        url = url + "#" + fragment
    return url


def urlunparse(components: tuple[str, str, str, str, str, str]) -> str:
    scheme, netloc, path, params, query, fragment = components
    if len(params) > 0:
        return urlunsplit((scheme, netloc, path + ";" + params, query,
                           fragment))
    return urlunsplit((scheme, netloc, path, query, fragment))


# ---------- relative-reference resolution ----------

def urljoin(base: str, url: str) -> str:
    """Resolve a possibly-relative `url` against `base` (RFC 3986)."""
    if len(base) == 0:
        return url
    if len(url) == 0:
        return base

    b = urlsplit(base)
    r = urlsplit(url)

    if r.scheme != b.scheme and len(r.scheme) > 0:
        return url
    scheme = b.scheme

    if not _scheme_uses_relative(scheme):
        return url

    netloc = r.netloc
    if _scheme_uses_netloc(scheme):
        if len(netloc) > 0:
            return urlunsplit((scheme, netloc, r.path, r.query, r.fragment))
        netloc = b.netloc

    if len(r.path) == 0:
        path = b.path
        query = r.query if len(r.query) > 0 else b.query
        return urlunsplit((scheme, netloc, path, query, r.fragment))

    if r.path.startswith("/"):
        segments = r.path.split("/")
    else:
        base_parts = b.path.split("/")
        # Drop the base's last (file) segment unless the base path is a dir.
        if len(base_parts) > 0 and len(base_parts[len(base_parts) - 1]) > 0:
            base_parts.pop()
        rel_parts = url_split_path_only(r.path)
        merged: list[str] = []
        for p in base_parts:
            merged.append(p)
        for p in rel_parts:
            merged.append(p)
        segments = _drop_inner_empties(merged)

    resolved: list[str] = []
    for seg in segments:
        if seg == "..":
            if len(resolved) > 0:
                resolved.pop()
        elif seg == ".":
            continue
        else:
            resolved.append(seg)

    last = segments[len(segments) - 1]
    if last == "." or last == "..":
        resolved.append("")

    joined = "/".join(resolved)
    if len(joined) == 0:
        joined = "/"
    return urlunsplit((scheme, netloc, joined, r.query, r.fragment))


def url_split_path_only(path: str) -> Own[list[str]]:
    return path.split("/")


def _drop_inner_empties(segments: list[str]) -> Own[list[str]]:
    # Mirror CPython's `segments[1:-1] = filter(None, segments[1:-1])`: keep the
    # first and last segments, drop empty ones in between (collapses // runs).
    n = len(segments)
    if n <= 2:
        out: list[str] = []
        for s in segments:
            out.append(s)
        return out
    result: list[str] = []
    result.append(segments[0])
    k = 1
    while k < n - 1:
        if len(segments[k]) > 0:
            result.append(segments[k])
        k += 1
    result.append(segments[n - 1])
    return result
