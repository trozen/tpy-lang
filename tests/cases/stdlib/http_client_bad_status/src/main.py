# A malformed status line, a non-numeric status code, and an empty response
# (peer closed before sending) all surface as a catchable BadStatusLine --
# matching CPython, whose RemoteDisconnected is a BadStatusLine subclass. A
# non-HTTP/1.x protocol version raises UnknownProtocol (a separate
# HTTPException subclass), also matching CPython.
import socket
import http.client


def attempt(response: bytes) -> None:
    a, b = socket.socketpair()
    conn = http.client.HTTPConnection("h", 80)
    conn.sock = a
    conn.request("GET", "/")
    b.recv(65536)
    b.sendall(response)
    b.close()
    try:
        conn.getresponse()
        print("no-raise")
    except http.client.UnknownProtocol:
        print("UnknownProtocol")
    except http.client.BadStatusLine:
        print("BadStatusLine")
    conn.close()


def main() -> None:
    attempt(b"GARBAGE LINE HERE\r\n\r\n")     # not an HTTP status line
    attempt(b"HTTP/1.1 spam OK\r\n\r\n")      # non-numeric status code
    attempt(b"")                              # peer closed, nothing sent
    attempt(b"HTTP/2.0 200 OK\r\n\r\n")       # unsupported protocol version


main()
