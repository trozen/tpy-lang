"""Async TCP client (high-level streams) for async_echo_server.py.

The streams counterpart of async_echo_client.py: instead of driving
loop.sock_sendall / sock_recv directly, it uses open_connection's
StreamWriter (write + drain) and StreamReader (readline) -- the reader
buffers the bytes and frames the reply at '\\n' for you.

    # Terminal 1:
    uv run tpy examples/net/async_echo_server.py
    # Terminal 2:
    uv run tpy examples/net/stream_client.py
    uv run tpy examples/net/stream_client.py -- --host 127.0.0.1 --port 9000 --message ping

Plain Python types only, so it also runs under stock CPython:

    python3 examples/net/stream_client.py
"""
import asyncio
from argparse import ArgumentParser


async def echo_once(host: str, port: int, message: str) -> None:
    reader, writer = await asyncio.open_connection(host, port)
    print(f"connected to {host}:{port}")

    writer.write(message.encode() + b"\n")
    await writer.drain()

    reply = await reader.readline()      # framed at '\n' by the reader's buffer
    print(f"got {len(reply)} bytes from server:")
    print(reply.decode().rstrip())

    writer.close()
    await writer.wait_closed()


def main() -> None:
    parser = ArgumentParser(description="Send a message over TCP via asyncio streams, print the echoed reply.")
    parser.add_argument("--host", default="127.0.0.1", help="server address")
    parser.add_argument("--port", type=int, default=8765, help="server port")
    parser.add_argument("--message", default="hello from tpy streams client",
                        help="payload to send")
    args = parser.parse_args()
    asyncio.run(echo_once(args.host, args.port, args.message))


main()
