# asyncio.start_server: two clients concurrently get their line echoed back
# upper-cased (the round-trip proves the per-connection handler ran). The
# @nocopy reader/writer move through the synthesized handler-factory wrapper,
# so a silent copy would be a compile error.
import asyncio
from tpy import Int32, Own


async def handle(reader: Own[asyncio.StreamReader],
                 writer: Own[asyncio.StreamWriter]) -> None:
    line = await reader.readline()
    writer.write(line.decode().strip().upper().encode() + b"\n")
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

    a = asyncio.create_task(client(port, "hello"))
    b = asyncio.create_task(client(port, "world"))
    print(await a)
    print(await b)

    server.close()
    await server.wait_closed()


def main() -> None:
    asyncio.run(main_coro())


main()
