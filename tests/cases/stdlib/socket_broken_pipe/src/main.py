# A write to a hung-up peer raises BrokenPipeError (an OSError/ConnectionError
# subclass, PEP 3151), not a process-killing SIGPIPE. The except-clause order
# proves the concrete subclass is raised, not the generic SocketError/OSError.
import socket


def main() -> None:
    # Inverse guard: a write to a live peer still succeeds -- the errno->subclass
    # mapping must not perturb a normal send.
    a, b = socket.socketpair()
    a.sendall(b"hi")
    print("live send:", b.recv(2))
    a.close()
    b.close()

    # The peer hangs up; the next write must raise BrokenPipeError specifically.
    c, d = socket.socketpair()
    d.close()
    try:
        c.sendall(b"x")
        print("NO ERROR")
    except BrokenPipeError:
        print("caught BrokenPipeError")
    except OSError:
        print("caught generic OSError")
    print("survived")
    c.close()


main()
