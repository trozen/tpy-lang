# tplib.requests follows a 302 to its Location: the final Response carries the
# 200 (status/url/text) while .history holds the intermediate 302. Two
# socketpairs feed the two hops (Session._connection for hop 0, the
# _redirect_connections queue for hop 1 -- the offline test seam); peers stay
# open while the client writes so the request sends don't hit a closed peer.
import socket
from http.client import HTTPConnection
import tplib.requests as requests


def main() -> None:
    a, b = socket.socketpair()
    c, d = socket.socketpair()
    b.sendall(b"HTTP/1.1 302 Found\r\nLocation: /v2/tables\r\n"
              b"Content-Length: 0\r\n\r\n")
    d.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
              b"Content-Length: 5\r\n\r\nhello")
    h0 = HTTPConnection("api.test", 80)
    h0.sock = a
    h1 = HTTPConnection("api.test", 80)
    h1.sock = c
    s = requests.Session()
    s._connection = h0
    s._redirect_connections = [h1]
    r = s.get("http://api.test/v1/tables")
    print(r.status_code, r.ok, r.text)
    print(r.url)
    print(len(r.history), r.history[0].status_code, r.history[0].url)
    # The first hop's request targets /v1/tables; the second targets the
    # redirect Location /v2/tables.
    print(b.recv(65536).split(b"\r\n")[0])
    print(d.recv(65536).split(b"\r\n")[0])
    b.close()
    d.close()


main()
