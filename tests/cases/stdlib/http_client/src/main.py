# http.client request/response over a socketpair (conn.sock injection -- no
# real server, no DNS, no ports). Covers the full GET flow (status/reason/
# version, case-insensitive getheader incl. duplicate-join, default, and
# absent->None; read(amt)/read(0)/read-to-exhausted; getheaders) and the exact
# request bytes various method/header combinations emit (auto Host /
# Accept-Encoding / Content-Length in CPython's order, and their suppression
# when the caller supplies the header). socket and BufferedReader are @nocopy,
# so moving the owned socket into conn.sock and the makefile'd reader into the
# response are real moves -- a silent copy would be a compile error.
import socket
import http.client


def show_request(method: str, url: str, body: bytes | None,
                 headers: dict[str, str]) -> None:
    # Capture exactly what HTTPConnection writes for this method/headers combo.
    # (headers is always a dict -- CPython's request() rejects a None headers.)
    a, b = socket.socketpair()
    conn = http.client.HTTPConnection("api.test", 8002)
    conn.sock = a
    conn.request(method, url, body, headers)
    print(b.recv(65536))
    conn.close()
    b.close()


def main() -> None:
    # --- GET: parse status line + headers, read the Content-Length body. ---
    a, b = socket.socketpair()
    conn = http.client.HTTPConnection("api.test", 8002)
    conn.sock = a
    conn.request("GET", "/v1/resource")
    b.recv(65536)                       # drain request -> graceful FIN on close
    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
              b"X-Multi: a\r\nContent-Length: 10\r\nX-Multi: b\r\n\r\nabcdefghij")
    b.close()
    resp = conn.getresponse()
    print(resp.status, resp.reason, resp.version)
    print(resp.getheader("content-type"))      # case-insensitive
    print(resp.getheader("x-multi"))           # duplicate values joined: a, b
    print(resp.getheader("missing", "DEF"))    # default when absent
    print(resp.getheader("absent"))            # no default -> None
    print(resp.read(3))                        # b'abc'
    print(resp.read(0))                        # b'' (must not end the body)
    print(resp.read())                         # b'defghij'
    print(resp.read())                         # b'' (exhausted)
    for kv in resp.getheaders():
        print(kv[0], "=", kv[1])
    conn.close()

    # --- Request bytes for various method/header combinations. ---
    # POST with body: auto Host/Accept-Encoding/Content-Length precede caller's.
    show_request("POST", "/v1", b'{"x":1}', {"Content-Type": "application/json"})
    # Bodyless POST/PUT/PATCH still send Content-Length: 0.
    show_request("POST", "/x", None, {})
    show_request("PUT", "/x", None, {})
    show_request("PATCH", "/x", None, {})
    # Bodyless GET sends no Content-Length.
    show_request("GET", "/x", None, {})
    # Caller-supplied Host suppresses the auto Host (auto Accept-Encoding stays).
    show_request("GET", "/x", None, {"Host": "override:9000"})
    # Caller-supplied Transfer-Encoding suppresses the auto Content-Length.
    show_request("POST", "/x", b"data", {"Transfer-Encoding": "chunked"})


main()
