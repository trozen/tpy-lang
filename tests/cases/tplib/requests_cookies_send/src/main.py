# A `cookies=` dict is serialized into a Cookie: request header (name=value
# pairs joined with "; ", in insertion order). A Cookie header the caller sets
# explicitly via headers= wins over the cookies= dict (no double Cookie header).
import socket
from http.client import HTTPConnection
from tplib import Box
import tplib.requests as requests


def _send(headers: dict[str, str] | None,
          cookies: dict[str, str] | None) -> bytes:
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    s = requests.Session()
    s.headers = {"User-Agent": "t"}
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    s._connection = Box(conn)
    r = s.get("http://api.test/x", None, headers, None, True, True, cookies)
    print(r.status_code)
    sent = b.recv(65536)
    b.close()
    return sent


def _cookie_line(sent: bytes) -> None:
    for line in sent.split(b"\r\n"):
        if line.startswith(b"Cookie:"):
            print(line)
            return
    print(b"<no Cookie header>")


def main() -> None:
    _cookie_line(_send(None, {"sid": "abc", "k": "v"}))
    # No cookies= -> no Cookie header at all.
    _cookie_line(_send(None, None))
    # An explicit Cookie header wins over cookies=.
    _cookie_line(_send({"Cookie": "manual=1"}, {"sid": "abc"}))


main()
