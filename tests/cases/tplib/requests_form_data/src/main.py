# tplib.requests: a dict data= body is urlencoded (application/x-www-form-
# urlencoded, with a matching Content-Length); an empty dict is falsy so it is
# treated as no body at all (no Content-Type, no body -- matching requests'
# `elif data:` truthiness, not an empty form); a bytes data= body is still sent
# verbatim with no implied Content-Type. Sent bytes inspected via a socketpair
# seam.
import socket
from http.client import HTTPConnection
from tplib import Box
import tplib.requests as requests


def send(url: str, data: bytes | dict[str, str] | None) -> None:
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    s = requests.Session()
    s.headers = {"User-Agent": "test-agent"}
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    s._connection = Box(conn)
    r = s.post(url, data)
    print(r.status_code)
    print(b.recv(65536))
    b.close()


def main() -> None:
    send("http://api.test/login", {"user": "ann", "pw": "s3cret"})
    empty: dict[str, str] = {}   # empty literal can't be inferred against the union
    send("http://api.test/empty", empty)
    send("http://api.test/raw", b"raw-bytes")


main()
