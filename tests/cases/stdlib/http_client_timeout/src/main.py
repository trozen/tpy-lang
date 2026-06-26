# HTTPConnection(timeout=) threads the timeout through connect() ->
# socket.create_connection -> settimeout, so the connected socket carries it
# (the timeout= plumbing requests/urlopen rely on). Loopback + an ephemeral
# port keep it deterministic; http.client is a real CPython module, so this is
# a parity test. connect() completes the handshake into the listen backlog
# without an accept(), so no server-side accept is needed.
import socket
from http.client import HTTPConnection


def main() -> None:
    srv = socket.create_server(("127.0.0.1", 0))
    port = srv.getsockname()[1]

    conn = HTTPConnection("127.0.0.1", port, 0.05)
    conn.connect()
    s = conn.sock
    if s is not None:
        print("timeout threaded:", s.gettimeout() == 0.05)
        print("blocking in timeout mode:", s.getblocking())
    conn.close()

    # No timeout argument -> the connected socket is plain blocking (None).
    conn2 = HTTPConnection("127.0.0.1", port)
    conn2.connect()
    s2 = conn2.sock
    if s2 is not None:
        print("default timeout None:", s2.gettimeout() is None)
    conn2.close()

    srv.close()


main()
