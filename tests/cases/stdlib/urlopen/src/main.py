# urllib.request.urlopen over http.client: GET reads the body (read happens
# AFTER the connection built inside urlopen has been dropped -- the response
# reader is a dup of the socket fd, so it stays valid); data= switches to POST
# with a Content-Length; a non-http scheme raises URLError. The _sock seam
# injects a socketpair end (moved in -- @nocopy, so a silent copy would be a
# compile error) with a pre-buffered response.
import socket
from urllib.request import urlopen, URLError


def main() -> None:
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello")
    resp = urlopen("http://api.test:8002/health", None, a)
    print(resp.status, resp.reason)
    print(resp.read())
    print(b.recv(65536))
    b.close()

    c, d = socket.socketpair()
    d.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    resp2 = urlopen("http://api.test/v1", b'{"x":1}', c)
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


main()
