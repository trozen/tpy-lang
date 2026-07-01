# A redirect to an unsupported scheme (here ftp://) raises ConnectionError --
# only http/https are followed. Guards the redirect scheme-rejection branch.
import socket
from http.client import HTTPConnection
from tplib import Box
import tplib.requests as requests
from tplib.requests import ConnectionError


def main() -> None:
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 302 Found\r\n"
              b"Location: ftp://files.test/data\r\n"
              b"Content-Length: 0\r\n\r\n")
    s = requests.Session()
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    s._connection = Box(conn)
    try:
        s.get("http://api.test/start")
        print("NO RAISE")
    except ConnectionError:
        print("caught ConnectionError for ftp redirect")
    b.close()


main()
