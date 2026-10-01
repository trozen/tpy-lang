# socket.settimeout timeout mode: a recv that outlasts the timeout raises
# TimeoutError("timed out") -- CPython's socket.timeout -- and because
# TimeoutError is an OSError subclass, `except OSError` catches it too. The
# makefile-backed read path (what http.client/requests use) honors the timeout
# as well. A send to a live peer under a timeout still succeeds. accept() and a
# send into a full buffer time out too, and a blocking send sends everything.
# A timeout too short to wait still accepts a connection that is already queued.
import socket
from tpy import Own, int32
from tpy.thread import spawn


class Drain:
    sock: socket.socket

    def __init__(self, sock: Own[socket.socket]) -> None:
        self.sock = sock

    def run(self) -> int32:
        total = 0
        while True:
            chunk = self.sock.recv(65536)
            if len(chunk) == 0:
                break
            total += len(chunk)
        return total


def accept_timeout() -> None:
    # accept: a timeout-mode listener nobody connects to
    srv = socket.create_server(("127.0.0.1", 0))
    srv.settimeout(0.05)
    try:
        conn, peer = srv.accept()  # tpyc: ok -- the subject: times out
        print("accept: NO TIMEOUT")
    except TimeoutError as e:
        print("accept: timeout:", str(e))
    srv.close()


def accept_queued() -> None:
    # accept: the deadline has passed before the wait starts, but the
    # connection is already queued, so the one poll still sees it
    srv = socket.create_server(("127.0.0.1", 0))
    c = socket.create_connection(srv.getsockname())
    srv.settimeout(1e-9)
    conn, peer = srv.accept()  # tpyc: ok -- the subject: accepted, no timeout
    print("accept queued: peer matches:", peer == c.getsockname())
    conn.close()
    c.close()
    srv.close()


def send_timeout() -> None:
    # send: the peer never reads, so the buffer fills and a send times out
    a, b = socket.socketpair()
    a.settimeout(0.05)
    chunk = b"x" * 65536
    try:
        while True:
            a.send(chunk)  # tpyc: ok -- the subject: raises once nothing fits
    except TimeoutError as e:
        print("send: timeout:", str(e))
    a.close()
    b.close()


def blocking_send() -> None:
    # blocking send: one send() waits for the reader and sends everything
    a, b = socket.socketpair()
    h = spawn(Drain(b))
    data = b"x" * 2000000
    n = a.send(data)  # tpyc: ok -- the subject: larger than any socket buffer
    print("blocking send: all sent:", n == len(data))
    a.close()
    print("blocking send: peer got:", h.join())


def main() -> None:
    a, b = socket.socketpair()

    # Send under a timeout to a live peer: no spurious timeout.
    a.settimeout(0.5)
    a.sendall(b"hi")
    got = b.recv(2)
    print("recv ok:", got)

    # A recv that never receives data times out -> TimeoutError("timed out").
    a.settimeout(0.05)
    try:
        a.recv(100)
        print("NO TIMEOUT")
    except TimeoutError as e:
        print("recv timeout:", str(e))

    # TimeoutError is an OSError, so `except OSError` also catches the timeout.
    try:
        a.recv(100)
        print("NO TIMEOUT")
    except OSError as e:
        print("as oserror:", str(e))

    # The makefile/BufferedReader read path honors the timeout too.
    r = a.makefile("rb")
    try:
        r.read(10)
        print("NO TIMEOUT")
    except TimeoutError as e:
        print("makefile timeout:", str(e))

    a.close()
    b.close()

    accept_timeout()
    accept_queued()
    send_timeout()
    blocking_send()


main()
