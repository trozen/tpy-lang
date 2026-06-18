"""Async TCP echo server (high-level streams) via asyncio.start_server.

The streams counterpart of async_echo_server.py: instead of writing the
accept loop by hand, start_server runs it for you and hands each connection
an async handler with a (StreamReader, StreamWriter) pair. Pair it with
stream_client.py (or async_echo_client.py, or any TCP client):

    # Terminal 1:
    uv run tpy examples/net/stream_server.py
    # Terminal 2:
    uv run tpy examples/net/stream_client.py

The handler takes ownership of its streams (`Own[...]` -- it runs as its own
task, so a borrow of the accept loop's temporaries would dangle), so this
example imports `tpy.Own`; under CPython it needs the stub package on the path:

    PYTHONPATH=lib/cpy python3 examples/net/stream_server.py
"""
import asyncio
from argparse import ArgumentParser
from tpy import Own


async def handle(reader: Own[asyncio.StreamReader],
                 writer: Own[asyncio.StreamWriter]) -> None:
    # Echo every line back until the client half-closes (readline -> b"").
    # The prints show the per-connection lifecycle; with several clients at
    # once they interleave, which is the point -- one handler task per client.
    print("client connected")
    while True:
        line = await reader.readline()
        if len(line) == 0:
            break
        print("echoing: " + line.decode().rstrip())
        writer.write(line)
        await writer.drain()
    print("client disconnected")
    writer.close()
    await writer.wait_closed()


async def serve(host: str, port: int) -> None:
    server = await asyncio.start_server(handle, host, port)
    print(f"listening on {host}:{port} (Ctrl-C to stop)")
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = ArgumentParser(description="Async TCP echo server over asyncio streams.")
    parser.add_argument("--host", default="127.0.0.1", help="bind address")
    parser.add_argument("--port", type=int, default=8765, help="bind port")
    args = parser.parse_args()
    # asyncio.run raises KeyboardInterrupt on Ctrl-C after cancelling serve()
    # and draining the `async with server` cleanup; catch it for a clean exit.
    try:
        asyncio.run(serve(args.host, args.port))
    except KeyboardInterrupt:
        print("shutting down")


main()
