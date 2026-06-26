# tplib.requests POST: a json= body (bound to a JsonValue local first, as the
# compiler requires) sets Content-Type and Content-Length; a raw data= body is
# sent verbatim; auth=(user, pass) emits an Authorization: Basic header. The
# request bytes are inspected to confirm each. socketpair injection with a
# pre-buffered response (the @nocopy socket move makes a silent copy a compile
# error).
import socket
from http.client import HTTPConnection
import tplib.requests as requests
from json import JsonValue


def send(method: str, url: str, data: bytes | None, body_json: JsonValue | None,
         auth: tuple[str, str] | None) -> None:
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 201 Created\r\nContent-Length: 2\r\n\r\nok")
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    s = requests.Session()
    s.connection = conn
    r = s.request(method, url, None, data, body_json, None, auth)
    print(r.status_code, r.ok)
    print(b.recv(65536))
    b.close()


def main() -> None:
    payload: JsonValue = {"name": "x", "count": 5}
    send("POST", "http://api.test/v1/items", None, payload, None)
    send("PUT", "http://api.test/v1/items/1", b"raw-bytes", None, None)
    send("POST", "http://api.test/secure", None, None, ("user", "pw"))


main()
