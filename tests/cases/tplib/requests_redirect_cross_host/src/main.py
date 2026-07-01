# A redirect drops the Authorization header unless host, scheme, and port all
# match (requests.should_strip_auth), so credentials don't leak across a host,
# scheme, or port change; a fully same-origin redirect keeps it. Each flow
# inspects the Authorization line the second hop sends.
import socket
from http.client import HTTPConnection
from tplib import Box
import tplib.requests as requests


def second_has_auth(location: bytes) -> bool:
    a, b = socket.socketpair()
    c, d = socket.socketpair()
    b.sendall(b"HTTP/1.1 302 Found\r\nLocation: " + location
              + b"\r\nContent-Length: 0\r\n\r\n")
    d.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    s = requests.Session()
    s._connection = Box(HTTPConnection("api.test", 80, None, a))
    s._redirect_connections = [Box(HTTPConnection("api.test", 80, None, c))]
    s.auth = ("user", "pw")
    r = s.get("http://api.test/start")
    print(r.status_code)
    b.recv(65536)
    second = d.recv(65536)
    b.close()
    d.close()
    return b"Authorization:" in second


def main() -> None:
    # Same-origin redirect keeps the credentials.
    print(second_has_auth(b"http://api.test/next"))
    # Cross-host redirect strips them.
    print(second_has_auth(b"http://other.test/next"))
    # Same host but a different port also strips (should_strip_auth).
    print(second_has_auth(b"http://api.test:8080/next"))


main()
