# tplib.requests Response.headers is a CaseInsensitiveDict: lookups ignore case
# while items()/keys() keep the server's casing. no_cpython (requests has no
# CPython module; the socket-injection seam is not real-requests API).
import socket
from http.client import HTTPConnection
import tplib.requests as requests
from tplib.requests import CaseInsensitiveDict


def main() -> None:
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 200 OK\r\n"
              b"Content-Type: application/json\r\n"
              b"X-Custom-Header: Yes\r\n"
              b"Content-Length: 2\r\n\r\nok")
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    s = requests.Session()
    s._connection = conn
    r = s.get("http://api.test/")
    b.recv(65536)
    b.close()

    print(r.headers["content-type"], r.headers["Content-Type"],
          r.headers["CONTENT-TYPE"])
    print("x-custom-header" in r.headers, "X-CUSTOM-HEADER" in r.headers,
          "missing" in r.headers)
    cl = r.headers.get("content-length")
    if cl is not None:
        print("len:", cl)
    print(r.headers.get("nope", "dflt"))
    print(len(r.headers))
    names: list[str] = []
    for kv in r.headers.items():
        names.append(kv[0])
    print(sorted(names))
    print(sorted(r.headers.keys()))
    print(sorted(r.headers.values()))

    other = CaseInsensitiveDict()
    other["CONTENT-TYPE"] = "application/json"
    other["x-custom-header"] = "Yes"
    other["Content-Length"] = "2"
    print(r.headers == other)               # equal despite key casing
    other["content-length"] = "3"           # value differs
    print(r.headers == other)
    del other["CONTENT-length"]             # case-insensitive delete
    print("content-length" in other, len(other))

    # re-setting a key under different casing overwrites in place (last casing
    # wins, len unchanged) -- the invariant that makes this not a plain dict
    cid = CaseInsensitiveDict()
    cid["Accept"] = "text/html"
    cid["ACCEPT"] = "application/json"
    print(len(cid), cid["accept"], sorted(cid.keys()))
    # __eq__ false on a same-length, different-key pair
    lhs = CaseInsensitiveDict()
    lhs["A"] = "1"
    rhs = CaseInsensitiveDict()
    rhs["B"] = "1"
    print(lhs == rhs)


main()
