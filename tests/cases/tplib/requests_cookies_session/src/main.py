# A Session persists Set-Cookie across requests: the first response sets a
# cookie, and the second request to the same host resends it in a Cookie header
# automatically. The second connection is injected fresh (the _connection seam
# is single-use, so it is reassigned before the second call).
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


def cookies_arg_not_persisted() -> None:
    # A per-call cookies= dict is sent on that call but NOT stored in the
    # Session jar (matches requests), so a later call without cookies= sends none.
    s = requests.Session()
    s.headers = {"User-Agent": "t"}

    a1, b1 = socket.socketpair()
    b1.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
    c1 = HTTPConnection("api.test", 80)
    c1.sock = a1
    s._connection = Box(c1)
    s.get("http://api.test/one", None, None, None, True, True, {"tok": "1"})
    _cookie_line(b1.recv(65536))
    b1.close()

    a2, b2 = socket.socketpair()
    b2.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
    c2 = HTTPConnection("api.test", 80)
    c2.sock = a2
    s._connection = Box(c2)
    s.get("http://api.test/two")
    _cookie_line(b2.recv(65536))
    b2.close()
    print("tok" in s.cookies)


def main() -> None:
    s = requests.Session()
    s.headers = {"User-Agent": "t"}

    a1, b1 = socket.socketpair()
    b1.sendall(b"HTTP/1.1 200 OK\r\nSet-Cookie: sid=xyz; Path=/\r\n"
               b"Content-Length: 2\r\n\r\nok")
    c1 = HTTPConnection("api.test", 80)
    c1.sock = a1
    s._connection = Box(c1)
    r1 = s.get("http://api.test/login")
    print(r1.status_code)
    # The login request itself sends no Cookie (jar was empty).
    _cookie_line(b1.recv(65536))
    b1.close()

    a2, b2 = socket.socketpair()
    b2.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    c2 = HTTPConnection("api.test", 80)
    c2.sock = a2
    s._connection = Box(c2)
    r2 = s.get("http://api.test/profile")
    print(r2.status_code)
    # The persisted cookie is resent automatically.
    _cookie_line(b2.recv(65536))
    b2.close()

    cookies_arg_not_persisted()


main()
