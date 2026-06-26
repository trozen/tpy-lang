# tplib.requests error surface: a 4xx makes .ok False and .raise_for_status()
# raise HTTPError; a URL with no host raises ConnectionError before any socket
# work. Both are caught by the documented exception tree.
import socket
from http.client import HTTPConnection
import tplib.requests as requests
from tplib.requests import HTTPError, ConnectionError


def main() -> None:
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 3\r\n\r\nno!")
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    s = requests.Session()
    s.connection = conn
    r = s.get("http://api.test/missing")
    print(r.status_code, r.ok, r.text)
    try:
        r.raise_for_status()
        print("no-raise")
    except HTTPError:
        print("HTTPError")
    b.close()

    try:
        requests.get("http:///no-host")
        print("no-raise")
    except ConnectionError:
        print("ConnectionError")


main()
