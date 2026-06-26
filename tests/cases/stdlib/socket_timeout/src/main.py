# socket.settimeout timeout mode: a recv that outlasts the timeout raises
# TimeoutError("timed out") -- CPython's socket.timeout -- and because
# TimeoutError is an OSError subclass, `except OSError` catches it too. The
# makefile-backed read path (what http.client/requests use) honors the timeout
# as well. A send to a live peer under a timeout still succeeds.
import socket


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


main()
