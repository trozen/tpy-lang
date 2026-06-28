# tplib.requests error surface: a 4xx makes .ok False and .raise_for_status()
# raise HTTPError; a URL with no host raises ConnectionError before any socket
# work; a socket-level failure mid-request (here, a hung-up peer) is re-wrapped
# from the OSError family into requests.ConnectionError. All are caught by the
# documented exception tree (the RequestException base).
import socket
from http.client import HTTPConnection
import tplib.requests as requests
from tplib.requests import HTTPError, ConnectionError


def main() -> None:
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 3\r\n\r\nno!")
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    s = requests.Session()
    s._connection = conn
    r = s.get("http://api.test/missing")
    print(r.status_code, r.ok, r.text)
    try:
        r.raise_for_status()
        print("no-raise")
    except HTTPError:
        print("HTTPError")
    b.close()

    try:
        requests.get("http:///no-host")
        print("no-raise")
    except ConnectionError:
        print("ConnectionError")

    # A hung-up peer mid-request: the socket BrokenPipeError is re-wrapped as
    # requests.ConnectionError. Catch the specific subclass to pin the wrap
    # target, not just the RequestException base.
    p, q = socket.socketpair()
    q.close()
    conn2 = HTTPConnection("api.test", 80)
    conn2.sock = p
    s2 = requests.Session()
    s2._connection = conn2
    try:
        s2.get("http://api.test/x")
        print("no-raise")
    except ConnectionError:
        print("ConnectionError")


main()
