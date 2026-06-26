# A write to a hung-up peer is a catchable OSError, not a process-killing SIGPIPE.
# Subclass and message differ across runtimes (CPython BrokenPipeError vs TPy
# SocketError), so we print a stable token, not str(e), for a portable snapshot.
import socket


def main() -> None:
    # Inverse guard: a write to a live peer still succeeds -- ignoring SIGPIPE
    # must not perturb a normal send.
    a, b = socket.socketpair()
    a.sendall(b"hi")
    print("live send:", b.recv(2))
    a.close()
    b.close()

    # The peer hangs up; the next write must raise rather than kill the process.
    c, d = socket.socketpair()
    d.close()
    try:
        c.sendall(b"x")
        print("NO ERROR")
    except OSError:
        print("caught OSError")
    print("survived")
    c.close()


main()
