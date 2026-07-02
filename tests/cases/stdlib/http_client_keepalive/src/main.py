# http.client keep-alive: one HTTPConnection serves multiple request/response
# cycles over the same socket (Content-Length and chunked framing), and
# HTTPResponse.will_close reports when the server ended reuse (Connection:
# close, HTTP/1.0 without a keep-alive -- Connection token or standalone
# Keep-Alive header -- or an unframed read-to-EOF body; a bodyless 204 stays
# reusable).
# Responses are written only after the previous one is fully drained -- the
# per-response reader must not slurp a pre-written next response into its
# buffer (no pipelining).
import socket
import http.client


def keepalive_cycles() -> None:
    a, b = socket.socketpair()
    conn = http.client.HTTPConnection("api.test", 8002)
    conn.sock = a

    conn.request("GET", "/one")
    b.recv(65536)
    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nres1")
    r1 = conn.getresponse()
    print(r1.read().decode(), r1.will_close)

    # Same connection, chunked framing this time.
    conn.request("GET", "/two")
    b.recv(65536)
    b.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
              b"4\r\nres2\r\n3\r\n!!!\r\n0\r\n\r\n")
    r2 = conn.getresponse()
    print(r2.read().decode(), r2.will_close)

    # Server ends reuse: Connection: close -> will_close.
    conn.request("GET", "/three")
    b.recv(65536)
    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n"
              b"\r\nres3")
    r3 = conn.getresponse()
    print(r3.read().decode(), r3.will_close)
    conn.close()
    b.close()


def will_close_response(response: bytes) -> bool:
    # One connection per variant: after a will_close response CPython's
    # getresponse() auto-closes the connection (TPy defers that to the caller
    # -- a declared divergence), so a follow-up request on the same injected
    # socket would diverge.
    a, b = socket.socketpair()
    conn = http.client.HTTPConnection("api.test", 8002)
    conn.sock = a
    conn.request("GET", "/x")
    b.recv(65536)
    b.sendall(response)
    b.close()
    resp = conn.getresponse()
    resp.read()
    conn.close()
    return resp.will_close


def will_close_variants() -> None:
    # HTTP/1.0 without keep-alive -> will_close.
    print(will_close_response(
        b"HTTP/1.0 200 OK\r\nContent-Length: 3\r\n\r\nold"))
    # HTTP/1.0 with keep-alive -> reusable.
    print(will_close_response(
        b"HTTP/1.0 200 OK\r\nConnection: keep-alive\r\nContent-Length: 3\r\n"
        b"\r\nold"))
    # HTTP/1.0 with a standalone Keep-Alive header (no Connection header) ->
    # reusable; needs Content-Length so the unframed-body fallback stays out
    # of the way.
    print(will_close_response(
        b"HTTP/1.0 200 OK\r\nKeep-Alive: timeout=15, max=100\r\n"
        b"Content-Length: 3\r\n\r\nold"))
    # Unframed 1.1 body (no Content-Length, not chunked) -> read to EOF,
    # will_close even without a Connection header.
    print(will_close_response(b"HTTP/1.1 200 OK\r\n\r\nuntil-close"))
    # Bodyless 204 without a Connection header: the no-body framing (_eof)
    # keeps the unframed fallback from firing -> reusable.
    print(will_close_response(b"HTTP/1.1 204 No Content\r\n\r\n"))


def main() -> None:
    keepalive_cycles()
    will_close_variants()


main()
