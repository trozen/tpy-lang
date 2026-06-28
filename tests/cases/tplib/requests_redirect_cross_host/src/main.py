# Cross-host redirect drops the Authorization header (requests.rebuild_auth), so
# credentials don't leak to a different host; a same-host redirect keeps it. Each
# flow inspects the Authorization line the second hop sends.
import socket
from http.client import HTTPConnection
import tplib.requests as requests


def second_has_auth(location: bytes) -> bool:
    a, b = socket.socketpair()
    c, d = socket.socketpair()
    b.sendall(b"HTTP/1.1 302 Found\r\nLocation: " + location
              + b"\r\nContent-Length: 0\r\n\r\n")
    d.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    h0 = HTTPConnection("api.test", 80)
    h0.sock = a
    h1 = HTTPConnection("api.test", 80)
    h1.sock = c
    s = requests.Session()
    s._connection = h0
    s._redirect_connections = [h1]
    s.auth = ("user", "pw")
    r = s.get("http://api.test/start")
    print(r.status_code)
    b.recv(65536)
    second = d.recv(65536)
    b.close()
    d.close()
    return b"Authorization:" in second


def main() -> None:
    # Same-host redirect keeps the credentials.
    print(second_has_auth(b"http://api.test/next"))
    # Cross-host redirect strips them.
    print(second_has_auth(b"http://other.test/next"))


main()
