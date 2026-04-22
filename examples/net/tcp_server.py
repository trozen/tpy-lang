"""Minimal single-connection TCP echo server. Binds to localhost:8765,
accepts one client, echoes everything it sends back, closes, exits.

Phase 1 has no threading / async / selectors, so this genuinely handles
one connection then exits -- intentionally. Phase 2 adds the selectors
module which lets a single thread juggle many clients.

Usage:
    uv run tpyc -x examples/net/tcp_server.py
"""

from socket import (
    Socket, AF_INET, SOCK_STREAM, SOL_SOCKET, SO_REUSEADDR,
    create_server,
)
from tpy import Int32


def main() -> None:
    # create_server bundles socket + SO_REUSEADDR + bind + listen.
    server = create_server(("127.0.0.1", Int32(8765)))
    print(f"listening on {server.getsockname()}")

    conn = server.accept()
    print(f"accepted connection from {conn.getpeername()}")

    # Drain the client (one chunk at a time) and echo back. Loop exits
    # when recv returns empty bytes (peer's half-close -> EOF).
    total_bytes = Int32(0)
    while True:
        chunk = conn.recv(Int32(4096))
        if len(chunk) == 0:
            break
        total_bytes = total_bytes + Int32.trunc(len(chunk))
        conn.sendall(chunk)

    print(f"echoed {total_bytes} bytes, closing")
    conn.close()
    server.close()


main()
