# Declared divergence from requests: this client is HTTP-only, so a redirect to
# a non-http scheme (here https) raises ConnectionError rather than being
# silently followed over plaintext. Only the first hop runs (one socketpair).
import socket
from http.client import HTTPConnection
import tplib.requests as requests
from tplib.requests import ConnectionError


def main() -> None:
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 302 Found\r\n"
              b"Location: https://secure.test/login\r\n"
              b"Content-Length: 0\r\n\r\n")
    h0 = HTTPConnection("api.test", 80)
    h0.sock = a
    s = requests.Session()
    s._connection = h0
    try:
        s.get("http://api.test/start")
        print("NO RAISE")
    except ConnectionError:
        print("caught ConnectionError for https redirect")
    b.close()


main()
