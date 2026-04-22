"""Minimal TCP client. Connects to a peer, sends a GET-like request,
prints the response.

Run against the companion `tcp_server.py` in a second terminal, or
against any TCP service that echoes / speaks HTTP on localhost.

Usage:
    # Terminal 1:
    uv run tpyc -x examples/net/tcp_server.py
    # Terminal 2:
    uv run tpyc -x examples/net/tcp_client.py
"""

from socket import Socket, AF_INET, SOCK_STREAM, SHUT_WR
from tpy import Int32


def main() -> None:
    sock = Socket(AF_INET, SOCK_STREAM)
    sock.connect(("127.0.0.1", Int32(8765)))
    print(f"connected to {sock.getpeername()}")

    sock.sendall(b"hello from tpy client\n")
    # Half-close write side so the server sees EOF and stops recv'ing.
    sock.shutdown(SHUT_WR)

    # Read the echoed reply (up to 4 KiB in one go -- fine for this demo).
    reply = sock.recv(Int32(4096))
    print(f"got {len(reply)} bytes from server:")
    print(reply.decode())

    sock.close()


main()
