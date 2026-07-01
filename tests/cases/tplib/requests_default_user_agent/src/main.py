# tplib.requests default User-Agent: auto-injected when the caller sends none,
# and overridable by a caller header (case-insensitively). Assertions are
# version-agnostic (the UA
# carries the compiler's major.minor, so baking the exact string would churn
# this snapshot on every version bump) -- the other requests_* cases pin an
# explicit UA to stay stable, so this is the one case exercising the real
# default. Driven over a socketpair with a pre-buffered response.
import socket
from http.client import HTTPConnection
from tplib import Box
import tplib.requests as requests


def main() -> None:
    # No UA header -> default auto-injected, prefixed with the client name.
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    s = requests.Session()
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    s._connection = Box(conn)
    s.get("http://api.test/x")
    print(b"User-Agent: tpy-requests/" in b.recv(65536))
    b.close()

    # A caller-supplied UA wins verbatim; the default is not also added.
    a2, b2 = socket.socketpair()
    b2.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    s2 = requests.Session()
    conn2 = HTTPConnection("api.test", 80)
    conn2.sock = a2
    s2._connection = Box(conn2)
    s2.get("http://api.test/x", None, {"User-Agent": "my-app/9"})
    sent = b2.recv(65536)
    print(b"User-Agent: my-app/9" in sent)
    print(b"tpy-requests" in sent)
    b2.close()

    # The presence check is case-insensitive: a lowercase "user-agent" also
    # suppresses the default (no second, auto UA is appended).
    a3, b3 = socket.socketpair()
    b3.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    s3 = requests.Session()
    conn3 = HTTPConnection("api.test", 80)
    conn3.sock = a3
    s3._connection = Box(conn3)
    s3.get("http://api.test/x", None, {"user-agent": "low/1"})
    sent3 = b3.recv(65536)
    print(b"user-agent: low/1" in sent3)
    print(b"tpy-requests" in sent3)
    b3.close()


main()
