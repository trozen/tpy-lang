# start_server lifecycle: serve_forever runs as its own task; `async with
# server:` closes on block exit (the __aexit__ path), which makes serve_forever
# raise CancelledError (CPython parity), caught at the await.
import asyncio
from tpy import Int32, Own


async def handle(reader: Own[asyncio.StreamReader],
                 writer: Own[asyncio.StreamWriter]) -> None:
    line = await reader.readline()
    writer.write(line)
    await writer.drain()
    writer.close()


async def client(port: Int32, msg: str) -> str:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(msg.encode() + b"\n")
    await writer.drain()
    reply = await reader.readline()
    writer.close()
    await writer.wait_closed()
    return reply.decode().strip()


async def main_coro() -> None:
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    sf = asyncio.create_task(server.serve_forever())
    async with server:
        print(await client(port, "ping"))
    # __aexit__ ran close(); serve_forever then raises CancelledError.
    try:
        await sf
    except asyncio.CancelledError:
        pass
    print("served")


def main() -> None:
    asyncio.run(main_coro())


main()
