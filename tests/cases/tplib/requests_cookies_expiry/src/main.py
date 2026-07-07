# Cookie expiry is honored (RFC 6265, matching CPython's http.cookiejar). A
# Set-Cookie with a past Expires (or Max-Age<=0) drops the cookie; a future
# Expires / positive Max-Age keeps it and it is sent until it expires; Max-Age
# takes precedence over Expires; a malformed Max-Age discards the whole cookie;
# the RFC 1123 and RFC 850 Expires forms parse, the asctime form does not. The
# send path is probed at a chosen `now` (header_for's threaded clock) so the
# expired-vs-live boundary is deterministic without waiting on the wall clock.
import socket
from http.client import HTTPConnection
from tplib import Box
from tpy import Own
import tplib.requests as requests

PAST = b"Thu, 01 Jan 1970 00:00:00 GMT"        # RFC 1123, in the past
FUTURE = b"Fri, 31 Dec 2099 23:59:59 GMT"      # RFC 1123, far future
RFC850_PAST = b"Sunday, 06-Nov-94 08:49:37 GMT"  # RFC 850 (2-digit year), past
ASCTIME = b"Sun Nov  6 08:49:37 1994"          # asctime form -- not parsed
# A `now` past the 2099 Expires above, to force that expiry to fire.
AFTER_2099 = 4200000000.0


def one(set_cookie: bytes) -> Own[requests.Response]:
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 200 OK\r\n" + set_cookie + b"Content-Length: 0\r\n\r\n")
    s = requests.Session()
    s.headers = {"User-Agent": "t"}
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    s._connection = Box(conn)
    r = s.get("http://api.test/x")
    b.close()
    return r


def _cookie_line(sent: bytes) -> None:
    for line in sent.split(b"\r\n"):
        if line.startswith(b"Cookie:"):
            print(line)
            return
    print(b"<no Cookie header>")


def live_cookie_resent() -> None:
    # End-to-end: a live (future-expiry) cookie set on one real request is
    # resent in the Cookie header on a later real request in the same session
    # (exercises the time.time() send path + the Session-jar merge).
    s = requests.Session()
    s.headers = {"User-Agent": "t"}
    a1, b1 = socket.socketpair()
    b1.sendall(b"HTTP/1.1 200 OK\r\nSet-Cookie: k=v; Max-Age=3600\r\n"
               b"Content-Length: 0\r\n\r\n")
    c1 = HTTPConnection("api.test", 80)
    c1.sock = a1
    s._connection = Box(c1)
    s.get("http://api.test/one")
    b1.close()

    a2, b2 = socket.socketpair()
    b2.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
    c2 = HTTPConnection("api.test", 80)
    c2.sock = a2
    s._connection = Box(c2)
    s.get("http://api.test/two")
    _cookie_line(b2.recv(65536))
    b2.close()


def main() -> None:
    # A past Expires drops the cookie outright.
    print("past expires kept:", "a" in one(b"Set-Cookie: a=1; Expires=" + PAST
                                           + b"\r\n").cookies)

    # A future Expires cookie is stored and sent now, withheld once expired.
    fut = one(b"Set-Cookie: b=2; Expires=" + FUTURE + b"\r\n").cookies
    print("future kept:", "b" in fut)
    print("future now:", "'" + fut.header_for("api.test", "/", False, 0.0) + "'")
    print("future after:", "'" + fut.header_for("api.test", "/", False, AFTER_2099) + "'")

    # A positive Max-Age is live now and expires at a far-future clock.
    ma = one(b"Set-Cookie: c=3; Max-Age=3600\r\n").cookies
    print("maxage now:", "'" + ma.header_for("api.test", "/", False, 0.0) + "'")
    print("maxage later:", "'" + ma.header_for("api.test", "/", False, 1e18) + "'")

    # Max-Age takes precedence: Max-Age=0 deletes despite a future Expires...
    print("maxage0 over expires:", "d" in one(b"Set-Cookie: d=4; Max-Age=0; Expires="
                                              + FUTURE + b"\r\n").cookies)
    # ...and a positive Max-Age keeps the cookie despite a PAST Expires (if the
    # past Expires governed, it would have been deleted).
    print("maxage over past expires:", "e" in one(b"Set-Cookie: e=5; Max-Age=3600; Expires="
                                                  + PAST + b"\r\n").cookies)

    # A malformed Max-Age discards the whole cookie (CPython bad-cookie).
    print("bad maxage kept:", "f" in one(b"Set-Cookie: f=6; Max-Age=abc\r\n").cookies)

    # RFC 850 Expires parses; this one is in the past, so the cookie is dropped.
    print("rfc850 past kept:", "g" in one(b"Set-Cookie: g=7; Expires=" + RFC850_PAST
                                          + b"\r\n").cookies)

    # The asctime Expires form is not parsed -> ignored -> session cookie (kept).
    asc = one(b"Set-Cookie: h=8; Expires=" + ASCTIME + b"\r\n").cookies
    print("asctime kept:", "h" in asc)
    print("asctime now:", "'" + asc.header_for("api.test", "/", False, 0.0) + "'")

    live_cookie_resent()


main()
