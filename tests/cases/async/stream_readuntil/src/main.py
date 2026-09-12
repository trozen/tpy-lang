# asyncio StreamReader.readuntil: reads through a bytes separator (included in
# the result); IncompleteReadError on EOF first; empty separator is a ValueError.
import asyncio
from tpy import int32
from socket import socket, AF_INET, SOCK_STREAM


async def client_role(port: int32) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await reader.readuntil(b"")
        print("no value error")
    except ValueError:
        print("empty sep rejected")
    first = await reader.readuntil(b"|")
    print("first=" + first.decode())
    second = await reader.readuntil(b"|")
    print("second=" + second.decode())
    try:
        await reader.readuntil(b"|")
        print("no incomplete")
    except asyncio.IncompleteReadError as e:
        print("incomplete partial=" + e.partial.decode())
    writer.close()
    await writer.wait_closed()


async def main_coro() -> None:
    loop = asyncio.get_running_loop()
    listener = socket(AF_INET, SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.setblocking(False)
    port = listener.getsockname()[1]

    client = asyncio.create_task(client_role(port))

    conn, addr = await loop.sock_accept(listener)
    await loop.sock_sendall(conn, b"foo|bar|baz")
    conn.close()

    await client
    listener.close()


def main() -> None:
    asyncio.run(main_coro())


main()
