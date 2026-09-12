# start_server: `async with server as srv` binds srv as an ALIAS of the
# server (stdlib __aenter__ returns self as a borrow, CPython parity) --
# reading through srv and closing through srv act on the real server, so
# serve_forever observes the close.
import asyncio
from tpy import int32, Own


async def handle(reader: Own[asyncio.StreamReader],
                 writer: Own[asyncio.StreamWriter]) -> None:
    line = await reader.readline()
    writer.write(line)
    await writer.drain()
    writer.close()


async def client(port: int32, msg: str) -> str:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(msg.encode() + b"\n")
    await writer.drain()
    reply = await reader.readline()
    writer.close()
    await writer.wait_closed()
    return reply.decode().strip()


async def main_coro() -> None:
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    sf = asyncio.create_task(server.serve_forever())
    async with server as srv:
        port = srv.sockets[0].getsockname()[1]
        print(await client(port, "ping"))
        srv.close()
    # srv.close() acted on the real server, so serve_forever raises
    # CancelledError (the __aexit__ close is then a no-op).
    try:
        await sf
    except asyncio.CancelledError:
        pass
    print("served")


def main() -> None:
    asyncio.run(main_coro())


main()
