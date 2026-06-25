# Response body framing + status-line parsing: chunked (with extension and
# trailer), connection-close (read to EOF), no-body cases (HEAD/204/304),
# HTTP version mapping (1.0/0.9 -> 10, 1.x -> 11), and the 100-Continue skip
# (only 100 is skipped; 101/103 are returned as the status). Driven over a
# socketpair with conn.sock injection; the peer drains the request then sends
# a canned response so read-to-close sees a graceful FIN.
import socket
import http.client


def run(method: str, response: bytes) -> None:
    a, b = socket.socketpair()
    conn = http.client.HTTPConnection("h", 80)
    conn.sock = a
    conn.request(method, "/")
    b.recv(65536)
    b.sendall(response)
    b.close()
    resp = conn.getresponse()
    print(resp.status, resp.reason, resp.version)
    print(resp.read())
    print(resp.read())          # exhausted -> b''
    conn.close()


def main() -> None:
    # Two chunks; the second carries a ";ext" extension; a trailer follows 0.
    run("GET", b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
              b"5\r\nhello\r\n7;ext=1\r\n, world\r\n0\r\nX-Trailer: v\r\n\r\n")
    # No Content-Length, no chunked: body runs to connection close.
    run("GET", b"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\n\r\nnope")
    # HEAD: never a body, even with a Content-Length header.
    run("HEAD", b"HTTP/1.1 200 OK\r\nContent-Length: 999\r\n\r\n")
    # 204 / 304: never a body.
    run("GET", b"HTTP/1.1 204 No Content\r\n\r\n")
    run("GET", b"HTTP/1.1 304 Not Modified\r\nContent-Length: 5\r\n\r\n")
    # HTTP/1.0 and HTTP/0.9 map to version 10; HTTP/1.x (x>=1) to 11.
    run("GET", b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nhi")
    run("GET", b"HTTP/1.5 200 OK\r\nContent-Length: 2\r\n\r\nhi")
    # 100 Continue is skipped; the real 200 is returned.
    run("GET", b"HTTP/1.1 100 Continue\r\n\r\n"
              b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi")
    # 101/103 are NOT skipped -- returned as the status (matching CPython).
    run("GET", b"HTTP/1.1 101 Switching Protocols\r\n\r\n")
    run("GET", b"HTTP/1.1 103 Early Hints\r\n\r\n"
              b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi")

    # Bounded read(amt) on a chunked body: pull it out in pieces across the
    # chunk boundary.
    a, b = socket.socketpair()
    conn = http.client.HTTPConnection("h", 80)
    conn.sock = a
    conn.request("GET", "/")
    b.recv(65536)
    b.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
              b"5\r\nhello\r\n5\r\nworld\r\n0\r\n\r\n")
    b.close()
    resp = conn.getresponse()
    print(resp.read(3))     # b'hel'
    print(resp.read(4))     # b'lowo' (spans the chunk boundary)
    print(resp.read())      # b'rld'
    conn.close()


main()
