# urllib.request.urlopen over http.client: GET reads the body (read happens AFTER
# the connection is dropped -- the response reader is a dup of the socket fd, so
# it stays valid); data= switches to POST; a non-http scheme / no-host raises
# URLError. Drives the internal `_urlopen` with a pre-connected Box[_Connection]
# (the offline seam -- @nocopy socket, so a silent copy would be a compile error);
# public urlopen() dials and takes no such param.
import socket
from tplib import Box
from urllib.request import urlopen, _urlopen, URLError
from http.client import HTTPConnection, _Connection


def main() -> None:
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello")
    hc = HTTPConnection("api.test", 8002)
    hc.sock = a
    conn: Box[_Connection] = Box(hc)
    resp = _urlopen("http://api.test:8002/health", None, None, None, conn)
    print(resp.status, resp.reason)
    print(resp.read())
    print(b.recv(65536))
    b.close()

    c, d = socket.socketpair()
    d.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    hc2 = HTTPConnection("api.test", 80)
    hc2.sock = c
    conn2: Box[_Connection] = Box(hc2)
    resp2 = _urlopen("http://api.test/v1", b'{"x":1}', None, None, conn2)
    print(resp2.status)
    print(d.recv(65536))
    d.close()

    try:
        urlopen("ftp://api.test/x")     # non-http scheme
        print("no-raise")
    except URLError:
        print("scheme URLError")

    try:
        urlopen("http:///path")         # http but no host
        print("no-raise")
    except URLError:
        print("no-host URLError")

    # URLError roots at OSError (CPython parity), so a handler written for
    # the builtin hierarchy catches it.
    try:
        urlopen("http:///path")
        print("no-raise")
    except OSError:
        print("no-host OSError")


main()
