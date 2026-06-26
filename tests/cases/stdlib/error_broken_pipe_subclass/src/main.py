# `except BrokenPipeError` does not compile: the ConnectionError/BrokenPipeError
# errno-keyed OSError subclass family does not exist in TPy yet. Guards the
# documented divergence -- a write to a hung-up peer raises the generic
# SocketError/OSError, catchable as OSError but not as BrokenPipeError. When the
# taxonomy lands (TODO.md socket follow-up), this case flips and must be updated.
import socket


def main() -> None:
    a, b = socket.socketpair()
    b.close()
    try:  # tpyc: error(/Unknown exception type 'BrokenPipeError'/)
        a.sendall(b"x")
    except BrokenPipeError:  # the unknown-type error attaches to the try stmt
        print("never")
    a.close()


main()
