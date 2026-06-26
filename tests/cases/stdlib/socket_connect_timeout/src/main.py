# create_connection(timeout=) applies the timeout before connect, so the whole
# connect/recv/send sequence honors it. Connecting to a live local listener with
# a generous timeout succeeds (no spurious timeout), the connected socket carries
# the timeout, and data round-trips under it. Loopback + an ephemeral port keep
# this deterministic (no external network); a socketpair can't exercise connect.
import socket


def main() -> None:
    srv = socket.create_server(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    c = socket.create_connection(("127.0.0.1", port), 2.0)
    conn, _ = srv.accept()
    print("connected:", c.gettimeout() == 2.0)

    c.sendall(b"ping")
    got = conn.recv(4)
    print("server got:", got)

    conn.close()
    c.close()
    srv.close()


main()
