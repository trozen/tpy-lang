# tplib.requests streaming: Response.iter_lines() yields body lines with the
# trailing line terminator stripped, buffering a partial line across chunk
# boundaries. Splits on "\n" and strips a preceding "\r" (so CRLF bodies do not
# leak a trailing \r, matching requests' splitlines). Bytes (not decoded). A
# body with no trailing newline still yields its last (unterminated) line.
# no_cpython: tplib.requests has no CPython module.
import socket
from http.client import HTTPConnection
from tplib import Box
import tplib.requests as requests


def lines_chunked() -> None:
    a, b = socket.socketpair()
    s = requests.Session()
    s.headers = {"User-Agent": "test-agent"}
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    key = requests._pool_key("http://api.test/log", True)
    s._pool[key] = Box(conn)

    # Three newline-delimited records split awkwardly across two chunks so a
    # line spans the boundary; no trailing newline after the last record.
    b.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
              b"8\r\nalpha\nbe\r\n8\r\nta\ngamma\r\n0\r\n\r\n")
    r = s.get("http://api.test/log", stream=True)
    for line in r.iter_lines():
        print("line:", line.decode())
    b.close()


def lines_trailing_newline() -> None:
    a, b = socket.socketpair()
    s = requests.Session()
    s.headers = {"User-Agent": "test-agent"}
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    key = requests._pool_key("http://api.test/log2", True)
    s._pool[key] = Box(conn)

    # A trailing newline must NOT yield a spurious empty final line.
    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\n\r\none\ntwo\n")
    r = s.get("http://api.test/log2", stream=True)
    lines: list[str] = []
    for line in r.iter_lines():
        lines.append(line.decode())
    # A trailing newline yields no spurious empty final line.
    print("count:", len(lines))
    for ln in lines:
        print("line:", ln)
    b.close()


def lines_crlf() -> None:
    a, b = socket.socketpair()
    s = requests.Session()
    s.headers = {"User-Agent": "test-agent"}
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    key = requests._pool_key("http://api.test/log3", True)
    s._pool[key] = Box(conn)

    # CRLF-terminated body: the preceding \r must be stripped (real requests via
    # splitlines), so lines carry no trailing \r. The final CRLF yields no
    # spurious empty line.
    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\n"
              b"alpha\r\nbeta\r\ngamma\r\n")
    r = s.get("http://api.test/log3", stream=True)
    for line in r.iter_lines():
        print("crlf:", line.decode(), "len", len(line))
    b.close()


def main() -> None:
    lines_chunked()
    lines_trailing_newline()
    lines_crlf()


main()
