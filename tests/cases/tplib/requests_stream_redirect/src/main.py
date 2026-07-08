# tplib.requests streaming through a redirect: stream=True streams only the
# TERMINAL response. An intermediate 302 is a followable redirect, so it is
# fully drained (its status/Location drive the hop) and never streamed -- only
# the final 200 hands back a lazy body. Two socketpairs feed the two hops
# (Session._connection for hop 0, _redirect_connections for hop 1).
# no_cpython: tplib.requests has no CPython module.
import socket
from http.client import HTTPConnection
from tplib import Box
import tplib.requests as requests


def main() -> None:
    a, b = socket.socketpair()
    c, d = socket.socketpair()
    b.sendall(b"HTTP/1.1 302 Found\r\nLocation: /v2/data\r\n"
              b"Content-Length: 0\r\n\r\n")
    d.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\n\r\nstreamed-ok!")
    s = requests.Session()
    s.headers = {"User-Agent": "test-agent"}
    h0 = HTTPConnection("api.test", 80)
    h0.sock = a
    s._connection = Box(h0)
    h1 = HTTPConnection("api.test", 80)
    h1.sock = c
    s._redirect_connections = [Box(h1)]

    r = s.get("http://api.test/v1/data", stream=True)
    print("final status:", r.status_code)
    print("final url:", r.url)
    print("history:", len(r.history), r.history[0].status_code)
    # The intermediate 302 was drained, not streamed: its body reader is absent.
    print("intermediate not streamed:", r.history[0].raw is None)
    # The terminal 200 streams its body lazily.
    got = bytearray()
    for chunk in r.iter_content(4):
        got += chunk
    print("streamed body:", bytes(got).decode())
    b.close()
    d.close()


main()
