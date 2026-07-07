# A Set-Cookie with Max-Age<=0 deletes a previously-set cookie from the Session
# jar (the clock-free expiry case). A positive Max-Age is not honored (the cookie
# is kept as a session cookie -- a declared divergence). Cookie state is observed
# across three requests in one Session.
import socket
from http.client import HTTPConnection
from tplib import Box
import tplib.requests as requests


def _hop(s: requests.Session, response: bytes) -> None:
    a, b = socket.socketpair()
    b.sendall(response)
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    s._connection = Box(conn)
    s.get("http://api.test/x")
    b.close()


def main() -> None:
    s = requests.Session()
    s.headers = {"User-Agent": "t"}

    # 1. Set two cookies (one with a positive Max-Age -> kept as a session cookie).
    _hop(s, b"HTTP/1.1 200 OK\r\nSet-Cookie: sid=abc\r\n"
            b"Set-Cookie: keep=1; Max-Age=3600\r\nContent-Length: 0\r\n\r\n")
    print("sid" in s.cookies)
    print("keep" in s.cookies)

    # 2. A Max-Age=0 Set-Cookie deletes sid; keep is untouched.
    _hop(s, b"HTTP/1.1 200 OK\r\nSet-Cookie: sid=x; Max-Age=0\r\n"
            b"Content-Length: 0\r\n\r\n")
    print("sid" in s.cookies)
    print("keep" in s.cookies)
    print(len(s.cookies))

    # 3. A response that only deletes a cookie leaves it invisible to indexing.
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 200 OK\r\nSet-Cookie: tmp=y; Max-Age=0\r\n"
              b"Content-Length: 0\r\n\r\n")
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    s._connection = Box(conn)
    r = s.get("http://api.test/x")
    b.close()
    try:
        print(r.cookies["tmp"])
    except KeyError:
        print("KeyError")


main()
