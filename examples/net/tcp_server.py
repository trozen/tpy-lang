"""Minimal single-connection TCP echo server. Binds to a host:port,
accepts one client, echoes everything it sends back, closes, exits.

Phase 1 has no threading / async / selectors, so this genuinely handles
one connection then exits -- intentionally. Phase 2 adds the selectors
module which lets a single thread juggle many clients.

Usage:
    uv run tpyc -x examples/net/tcp_server.py
    uv run tpyc -x examples/net/tcp_server.py -- --host 0.0.0.0 --port 9000
"""

from argparse import ArgumentParser
from socket import (
    Socket, AF_INET, SOCK_STREAM, SOL_SOCKET, SO_REUSEADDR,
    create_server,
)
from tpy import Int32


def main() -> None:
    parser = ArgumentParser(description="Single-connection TCP echo server.")
    parser.add_argument(
        "--host", default="127.0.0.1", help="bind address",
    )
    # TODO: changing this to int breaks c++ compilation
    parser.add_argument(
        "--port", type=Int32, default=8765, help="bind port",
    )
    args = parser.parse_args()

    # create_server bundles socket + SO_REUSEADDR + bind + listen.
    server = create_server((args.host, args.port))
    print(f"listening on {server.getsockname()}")

    conn = server.accept()
    print(f"accepted connection from {conn.getpeername()}")

    # Drain the client (one chunk at a time) and echo back. Loop exits
    # when recv returns empty bytes (peer's half-close -> EOF).
    total_bytes = 0
    while True:
        chunk = conn.recv(4096)
        if len(chunk) == 0:
            break
        total_bytes = total_bytes + len(chunk)
        conn.sendall(chunk)

    print(f"echoed {total_bytes} bytes, closing")
    conn.close()
    server.close()


main()
