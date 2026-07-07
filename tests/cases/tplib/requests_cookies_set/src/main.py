# A Set-Cookie response header is parsed into Response.cookies (and, on a
# Session, accumulated into Session.cookies). name=value plus the honored
# attributes (Domain/Path/Secure) are covered; the mapping accessors
# (indexing, `in`, .get, .items, len) reflect the parsed cookies. A Domain=
# that does not domain-match the responding host is rejected (RFC 6265), and a
# deleted marker is invisible to __getitem__ (raises KeyError).
import socket
from http.client import HTTPConnection
from tplib import Box
import tplib.requests as requests


def main() -> None:
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 200 OK\r\n"
              b"Set-Cookie: sid=xyz; Path=/; HttpOnly\r\n"
              b"Set-Cookie: pref=dark; Domain=api.test\r\n"
              b"Set-Cookie: evil=1; Domain=other.test\r\n"
              b"Content-Length: 2\r\n\r\nok")
    s = requests.Session()
    s.headers = {"User-Agent": "t"}
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    s._connection = Box(conn)
    r = s.get("http://api.test/login")
    b.close()

    print(r.status_code)
    # Response.cookies holds this response's Set-Cookie values.
    print(r.cookies["sid"])
    print(r.cookies["pref"])
    print("sid" in r.cookies)
    print("missing" in r.cookies)
    print(r.cookies.get("missing", "fallback"))
    # A Domain= not matching the responding host is dropped (no cross-domain plant).
    print("evil" in r.cookies)
    print(len(r.cookies))
    for kv in r.cookies.items():
        print(kv[0] + "=" + kv[1])
    # And the Session accumulated the accepted cookies for later requests.
    print("sid" in s.cookies)
    print("evil" in s.cookies)


main()
