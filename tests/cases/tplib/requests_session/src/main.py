# tplib.requests Session: default headers/params merge into each request (with
# per-call override), a default auth is applied, the injected connection is
# cleared after the request (so a regression that drops the clear is caught),
# Session.post sends a body, and `with Session()` closes a still-set connection
# on exit (observed as EOF on the socketpair peer).
import socket
from http.client import HTTPConnection
from tplib import Box
import tplib.requests as requests


def merge_and_clear() -> None:
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    s = requests.Session()
    s.headers = {"X-App": "atlas", "Accept": "application/json"}
    s.params = {"db": "das"}
    s.auth = ("user", "pw")
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    s._connection = Box(conn)
    r = s.get("http://api.test/v1/tables", {"limit": "10"}, {"X-App": "override"})
    print(r.status_code)
    print(b.recv(65536))
    if s._connection is None:
        print("connection cleared")
    b.close()


def session_post() -> None:
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 201 Created\r\nContent-Length: 2\r\n\r\nok")
    s = requests.Session()
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    s._connection = Box(conn)
    r = s.post("http://api.test/v1/items", b"payload")
    print(r.status_code)
    print(b.recv(65536))
    b.close()


def context_manager_closes() -> None:
    a, b = socket.socketpair()
    with requests.Session() as s:
        conn = HTTPConnection("api.test", 80)
        conn.sock = a
        s._connection = Box(conn)
    # __exit__ closed the still-set connection; the peer now sees EOF.
    print(b.recv(10))
    b.close()


def main() -> None:
    merge_and_clear()
    session_post()
    context_manager_closes()


main()
