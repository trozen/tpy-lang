# tplib.requests GET: params appended to the query, status/ok/reason/text,
# headers collected into a dict, and the untyped .json() result narrowed (deep
# narrow needs `v: JsonValue = d[k]` first -- the recursive-union storage form).
# Driven over a socketpair: the response is pre-buffered, then the injected
# socket is moved into the connection (socket is @nocopy, so the move is real
# -- a silent copy would be a compile error). The request bytes are inspected
# to confirm the query string and auto headers.
import socket
from http.client import HTTPConnection
from tplib import Box
import tplib.requests as requests
from json import JsonValue


def main() -> None:
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
              b"Content-Length: 24\r\n\r\n"
              b'{"name": "t", "rows": 3}')
    s = requests.Session()
    # Pin the User-Agent so the sent-bytes snapshot doesn't churn on a compiler
    # version bump (the auto default is version-derived; covered version-
    # agnostically in requests_default_user_agent).
    s.headers = {"User-Agent": "test-agent"}
    # Inject the pre-bound socket via the public sock field, then box the
    # fresh-constructed local (the offline test seam).
    conn = HTTPConnection("api.test", 8002)
    conn.sock = a
    s._connection = Box(conn)
    r = s.get("http://api.test:8002/v1/tables", {"db": "das"})
    print(r.status_code, r.ok, r.reason)
    print(r.text)
    print(r.headers["Content-Type"])
    r.raise_for_status()            # 200 -> no raise
    d = r.json()
    if isinstance(d, dict):
        v: JsonValue = d["rows"]
        if isinstance(v, int):
            print("rows =", v)
    print(b.recv(65536))
    b.close()


main()
