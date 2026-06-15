"""Async TCP client for async_echo_server.py: connect, send, print the reply.

The async analog of tcp_client.py -- the connect and the recv park on the
asyncio epoll reactor instead of blocking the thread.

    # Terminal 1:
    uv run tpy examples/net/async_echo_server.py
    # Terminal 2:
    uv run tpy examples/net/async_echo_client.py
    uv run tpy examples/net/async_echo_client.py -- --host 127.0.0.1 --port 9000 --message ping

Uses only plain Python types (the int port narrows to the socket layer's
fixed-width port at the call boundary), so it also runs under stock CPython:

    python3 examples/net/async_echo_client.py
"""
import asyncio
from argparse import ArgumentParser
from socket import socket, AF_INET, SOCK_STREAM, SHUT_WR


async def echo_once(host: str, port: int, message: str) -> None:
    loop = asyncio.get_running_loop()
    s = socket(AF_INET, SOCK_STREAM)
    s.setblocking(False)
    await loop.sock_connect(s, (host, port))
    print(f"connected to {s.getpeername()}")

    await loop.sock_sendall(s, message.encode() + b"\n")
    s.shutdown(SHUT_WR)  # half-close write side so the server sees EOF

    reply = await loop.sock_recv(s, 4096)
    print(f"got {len(reply)} bytes from server:")
    print(reply.decode())
    s.close()


def main() -> None:
    parser = ArgumentParser(description="Send a message over TCP, print the echoed reply.")
    parser.add_argument("--host", default="127.0.0.1", help="server address")
    parser.add_argument("--port", type=int, default=8765, help="server port")
    parser.add_argument("--message", default="hello from tpy async client",
                        help="payload to send")
    args = parser.parse_args()
    asyncio.run(echo_once(args.host, args.port, args.message))


main()
