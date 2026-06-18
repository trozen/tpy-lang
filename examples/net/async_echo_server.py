"""Async TCP echo server: one thread, many concurrent clients.

The async analog of tcp_server.py: it accepts in a loop and spawns a handler
coroutine per client on the asyncio epoll reactor, so a slow client never
blocks the others -- all on one thread, no selectors boilerplate.

Pair it with async_echo_client.py, or any TCP client:

    uv run tpy examples/net/async_echo_server.py
    uv run tpy examples/net/async_echo_server.py -- --host 0.0.0.0 --port 9000

Uses only plain Python types (the int port narrows to the socket layer's
fixed-width port at the call boundary), so it also runs under stock CPython:

    python3 examples/net/async_echo_server.py
"""
import asyncio
from argparse import ArgumentParser
from socket import socket, create_server


async def handle(conn: socket, peer: tuple[str, int]) -> None:
    print(f"connection from {peer}")
    loop = asyncio.get_running_loop()
    while True:
        chunk = await loop.sock_recv(conn, 4096)
        if len(chunk) == 0:  # peer half-closed -> EOF
            break
        await loop.sock_sendall(conn, chunk)
    print(f"closing {peer}")
    conn.close()


async def serve(host: str, port: int) -> None:
    loop = asyncio.get_running_loop()
    # create_server bundles socket + SO_REUSEADDR + bind + listen; the reactor
    # just needs it non-blocking on top.
    listener = create_server((host, port))
    listener.setblocking(False)
    print(f"listening on {listener.getsockname()}")

    # Each accepted connection runs in its own task, so the accept loop
    # keeps taking new clients while existing ones are still talking.
    while True:
        conn, peer = await loop.sock_accept(listener)
        asyncio.create_task(handle(conn, peer))


def main() -> None:
    parser = ArgumentParser(description="Async TCP echo server.")
    parser.add_argument("--host", default="127.0.0.1", help="bind address")
    parser.add_argument("--port", type=int, default=8765, help="bind port")
    args = parser.parse_args()
    # asyncio.run raises KeyboardInterrupt on Ctrl-C after cancelling the
    # serve loop and running its cleanup; catch it for a clean exit.
    try:
        asyncio.run(serve(args.host, args.port))
    except KeyboardInterrupt:
        print("shutting down")


main()
