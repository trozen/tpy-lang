# Persisted cookies are domain/path/secure-scoped, so a Set-Cookie from one host
# is not leaked to another across a redirect (the security invariant). A
# host-only cookie (no Domain=) is sent only to the exact host; a Domain= cookie
# matches subdomains; a Secure cookie is withheld over http. hop0 sets cookies
# and redirects; the Cookie header hop1 sends is inspected.
import socket
from http.client import HTTPConnection
from tplib import Box
import tplib.requests as requests


def _cookie_line(sent: bytes) -> None:
    for line in sent.split(b"\r\n"):
        if line.startswith(b"Cookie:"):
            print(line)
            return
    print(b"<no Cookie header>")


def redirect_cookie(set_cookies: bytes, location: bytes) -> None:
    a, b = socket.socketpair()
    c, d = socket.socketpair()
    b.sendall(b"HTTP/1.1 302 Found\r\nLocation: " + location + b"\r\n"
              + set_cookies + b"Content-Length: 0\r\n\r\n")
    d.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    s = requests.Session()
    s.headers = {"User-Agent": "t"}
    h0 = HTTPConnection("api.test", 80)
    h0.sock = a
    s._connection = Box(h0)
    h1 = HTTPConnection("api.test", 80)
    h1.sock = c
    s._redirect_connections = [Box(h1)]
    s.get("http://api.test/start")
    b.recv(65536)
    _cookie_line(d.recv(65536))
    b.close()
    d.close()


def cookies_arg_crosses_host() -> None:
    # A per-call cookies= dict is unscoped (domain "") and IS resent across a
    # cross-host redirect within the same call -- the documented exception to
    # host-scoping (the caller explicitly attached them to this call).
    a, b = socket.socketpair()
    c, d = socket.socketpair()
    b.sendall(b"HTTP/1.1 302 Found\r\nLocation: http://other.test/next\r\n"
              b"Content-Length: 0\r\n\r\n")
    d.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    s = requests.Session()
    s.headers = {"User-Agent": "t"}
    h0 = HTTPConnection("api.test", 80)
    h0.sock = a
    s._connection = Box(h0)
    h1 = HTTPConnection("api.test", 80)
    h1.sock = c
    s._redirect_connections = [Box(h1)]
    s.get("http://api.test/start", None, None, None, True, True, {"tok": "1"})
    b.recv(65536)
    _cookie_line(d.recv(65536))
    b.close()
    d.close()


def path_scoping() -> None:
    # A Path=/foo cookie matches only path-segment boundaries (RFC 6265):
    # withheld from /foobar, sent to /foo/bar and to the exact /foo.
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 200 OK\r\nSet-Cookie: sid=abc; Path=/foo\r\n"
              b"Content-Length: 0\r\n\r\n")
    s = requests.Session()
    s.headers = {"User-Agent": "t"}
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    s._connection = Box(conn)
    jar = s.get("http://api.test/login").cookies
    b.close()
    print("/foobar: '" + jar.header_for("api.test", "/foobar", False, 0.0) + "'")
    print("/foo/bar: '" + jar.header_for("api.test", "/foo/bar", False, 0.0) + "'")
    print("/foo: '" + jar.header_for("api.test", "/foo", False, 0.0) + "'")


def main() -> None:
    # Host-only cookie: NOT sent to a different host.
    redirect_cookie(b"Set-Cookie: sid=abc\r\n", b"http://other.test/next")
    # Host-only cookie: sent on a same-host redirect.
    redirect_cookie(b"Set-Cookie: sid=abc\r\n", b"http://api.test/next")
    # Domain cookie matches a subdomain; the sibling host-only cookie does not.
    redirect_cookie(b"Set-Cookie: sid=abc\r\nSet-Cookie: pref=x; Domain=api.test\r\n",
                    b"http://sub.api.test/next")
    # Secure cookie is withheld over http even on the same host.
    redirect_cookie(b"Set-Cookie: tok=1; Secure\r\n", b"http://api.test/next")
    cookies_arg_crosses_host()
    path_scoping()


main()
