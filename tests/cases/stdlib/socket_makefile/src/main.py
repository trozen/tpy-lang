# socket.makefile("rb") -> io.BufferedReader over a socketpair: the server
# end sends a canned HTTP-shaped response and closes; the client end reads it
# line-by-line then drains the body. The reader owns a dup of the fd, so it
# and the socket close independently.
import socket


def main() -> None:
    a, b = socket.socketpair()
    a.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello")
    a.close()
    f = b.makefile("rb")
    print(f.readline())       # b'HTTP/1.1 200 OK\r\n'
    print(f.readline())       # b'Content-Length: 5\r\n'
    print(f.readline())       # b'\r\n'
    print(f.read())           # b'hello'
    f.close()
    b.close()

    # "b" is an accepted binary-read alias of "rb".
    c, d = socket.socketpair()
    c.sendall(b"ping\n")
    c.close()
    g = d.makefile("b")
    print(g.readline())       # b'ping\n'
    g.close()
    d.close()


main()
