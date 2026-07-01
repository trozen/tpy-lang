# A redirect chain longer than Session.max_redirects raises TooManyRedirects.
# max_redirects=1 allows one hop into .history; the second 302 trips the limit.
# Both peers serve a 302, so the chain never terminates on its own.
import socket
from http.client import HTTPConnection
from tplib import Box
import tplib.requests as requests
from tplib.requests import TooManyRedirects


def main() -> None:
    a, b = socket.socketpair()
    c, d = socket.socketpair()
    b.sendall(b"HTTP/1.1 302 Found\r\nLocation: /two\r\nContent-Length: 0\r\n\r\n")
    d.sendall(b"HTTP/1.1 302 Found\r\nLocation: /three\r\nContent-Length: 0\r\n\r\n")
    s = requests.Session()
    h0 = HTTPConnection("api.test", 80)
    h0.sock = a
    s._connection = Box(h0)
    h1 = HTTPConnection("api.test", 80)
    h1.sock = c
    s._redirect_connections = [Box(h1)]
    s.max_redirects = 1
    try:
        s.get("http://api.test/one")
        print("NO RAISE")
    except TooManyRedirects:
        print("caught TooManyRedirects")
    b.close()
    d.close()


main()
