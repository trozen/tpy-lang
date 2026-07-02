# tplib.requests Session connection pooling: a pooled connection (seeded via
# s._pool -- the offline pooling seam; _connection stays the single-use seam)
# serves multiple requests over one socket, goes back in the pool after each,
# and a Connection: close response closes the socket while the pool keeps the
# entry as a lazy-reconnect handle -- but a request that FAILS mid-flight
# drops the entry instead of restoring it. Also pins the _pool_key shape
# (scheme | host | port | verify-token). User-Agent pinned so sent-bytes
# don't churn.
# no_cpython: tplib.requests has no CPython module.
import socket
from http.client import HTTPConnection, BadStatusLine
from tplib import Box
import tplib.requests as requests


def pooled_reuse() -> None:
    a, b = socket.socketpair()
    s = requests.Session()
    s.headers = {"User-Agent": "test-agent"}
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    key = requests._pool_key("http://api.test/v1/a", True)
    print("key:", key)
    s._pool[key] = Box(conn)

    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nr1")
    r1 = s.get("http://api.test/v1/a")
    print(r1.status_code, r1.text)
    print("req1:", b.recv(65536).decode().split("\r\n")[0])

    # Second request to the same target: pool pops the same connection --
    # the same socketpair peer sees the second request (real reuse, not a
    # reconnect, which would fail here: api.test does not resolve).
    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nr2")
    r2 = s.get("http://api.test/v1/b")
    print(r2.status_code, r2.text)
    print("req2:", b.recv(65536).decode().split("\r\n")[0])

    # Server ends reuse: the pooled socket is closed (peer sees EOF), but the
    # pool keeps the connection as a reconnect handle.
    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n"
              b"\r\nr3")
    r3 = s.get("http://api.test/v1/c")
    print(r3.status_code, r3.text)
    b.recv(65536)
    print("peer EOF after close:", b.recv(10) == b"")
    print("pool keeps handle:", key in s._pool)
    b.close()


def pool_key_shapes() -> None:
    # https default port + verify variants get distinct keys.
    print(requests._pool_key("https://api.test/x", True))
    print(requests._pool_key("https://api.test:8443/x", False))
    print(requests._pool_key("https://api.test/x", "/etc/ca.pem"))


def failed_request_drops_entry() -> None:
    # A request that fails mid-flight on a pooled connection must DROP the
    # entry (RAII-close), not restore it -- restoring would hand the next
    # request a mid-stream-corrupted connection. shutdown(SHUT_WR) (not
    # close()) so the request send still succeeds and the failure is
    # deterministically the EOF status line, not a send/RST race.
    a, b = socket.socketpair()
    s = requests.Session()
    s.headers = {"User-Agent": "test-agent"}
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    key = requests._pool_key("http://api.test/x", True)
    s._pool[key] = Box(conn)
    b.shutdown(socket.SHUT_WR)
    try:
        s.get("http://api.test/x")
        print("unexpected success")
    except BadStatusLine:
        print("bad status; entry dropped:", key not in s._pool)
    b.close()


def exit_closes_pool() -> None:
    a, b = socket.socketpair()
    with requests.Session() as s:
        conn = HTTPConnection("api.test", 80)
        conn.sock = a
        s._pool[requests._pool_key("http://api.test/", True)] = Box(conn)
    # __exit__ closed the pooled connection; the peer now sees EOF.
    print("closed on exit:", b.recv(10) == b"")
    b.close()


def main() -> None:
    pooled_reuse()
    pool_key_shapes()
    failed_request_drops_entry()
    exit_closes_pool()


main()
