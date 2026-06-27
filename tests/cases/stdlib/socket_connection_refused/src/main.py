# A connect to a loopback port with no listener raises ConnectionRefusedError
# (PEP 3151), not the generic SocketError. Port 1 on 127.0.0.1 is privileged
# and never has a listener, so the connect is refused immediately (no network,
# deterministic). The except-clause order proves the concrete subclass fires.
import socket


def main() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # A bound timeout guards against a host that DROPs (rather than REJECTs)
    # loopback; the refused connect returns ECONNREFUSED well before it.
    s.settimeout(5.0)
    try:
        s.connect(("127.0.0.1", 1))
        print("NO ERROR")
    except ConnectionRefusedError:
        print("caught ConnectionRefusedError")
    except ConnectionError:
        print("caught generic ConnectionError")
    except OSError:
        print("caught generic OSError")
    s.close()


main()
