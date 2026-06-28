# Method/body rewrite on redirect, mirroring requests.Session.rebuild_method:
# 301 and 303 coerce POST to GET and drop the body plus its Content-Type header;
# 307 and 308 preserve method, body, and headers. (no_cpython -- the socket-
# injection seam isn't real-requests API; parity is against requests' documented
# rules, not a live run.) Each flow inspects the bytes the second hop sends.
import socket
from http.client import HTTPConnection
import tplib.requests as requests


def run_redirect(status_line: bytes) -> tuple[bytes, bool, bool]:
    a, b = socket.socketpair()
    c, d = socket.socketpair()
    b.sendall(status_line + b"\r\nLocation: /next\r\nContent-Length: 0\r\n\r\n")
    d.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    h0 = HTTPConnection("api.test", 80)
    h0.sock = a
    h1 = HTTPConnection("api.test", 80)
    h1.sock = c
    s = requests.Session()
    s.connection = h0
    s.redirect_connections = [h1]
    r = s.post("http://api.test/submit", b'{"x":1}', None, None,
               {"Content-Type": "text/plain"})
    print(r.status_code, r.url)
    b.recv(65536)                         # drain hop-0 request
    second = d.recv(65536)
    b.close()
    d.close()
    request_line = second.split(b"\r\n")[0]
    has_body = b'{"x":1}' in second
    has_content_type = b"text/plain" in second
    return (request_line, has_body, has_content_type)


def report(status_line: bytes) -> None:
    line, has_body, has_ct = run_redirect(status_line)
    print(line, has_body, has_ct)


def main() -> None:
    report(b"HTTP/1.1 301 Moved Permanently")   # POST -> GET, body + CT dropped
    report(b"HTTP/1.1 303 See Other")           # POST -> GET, body + CT dropped
    report(b"HTTP/1.1 307 Temporary Redirect")  # POST kept, body + CT preserved
    report(b"HTTP/1.1 308 Permanent Redirect")  # POST kept, body + CT preserved


main()
