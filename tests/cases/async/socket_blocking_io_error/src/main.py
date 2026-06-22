# A non-blocking recv with no data pending raises BlockingIOError on EAGAIN.
# Assert it is catchable, that `except OSError` catches it (BlockingIOError
# subclasses OSError), and that `except ValueError` does NOT -- it propagates
# to the outer handler.
from socket import socketpair


def trigger() -> None:
    a, b = socketpair()
    b.setblocking(False)
    b.recv(16)  # no data pending -> EAGAIN -> BlockingIOError


def main() -> None:
    try:
        trigger()
        print("no error")
    except OSError:
        print("caught as OSError")

    try:
        try:
            trigger()
        except ValueError:
            print("WRONG: caught as ValueError")
    except BlockingIOError:
        print("not ValueError; caught as BlockingIOError")


main()
