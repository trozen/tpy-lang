# allow_redirects=False returns the 3xx response as-is (no following): status is
# the 302, .history is empty, .url is the requested URL. One socketpair is
# enough since no second hop is made.
import socket
from http.client import HTTPConnection
import tplib.requests as requests


def main() -> None:
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 302 Found\r\nLocation: /elsewhere\r\n"
              b"Content-Length: 0\r\n\r\n")
    h0 = HTTPConnection("api.test", 80)
    h0.sock = a
    s = requests.Session()
    s._connection = h0
    r = s.get("http://api.test/start", None, None, None, False)
    print(r.status_code, r.ok)
    print(r.url)
    print(len(r.history))
    print(r.headers["Location"])
    b.close()


main()
