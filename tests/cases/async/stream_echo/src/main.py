# asyncio streams: open_connection -> StreamReader/StreamWriter over the
# epoll reactor. The reader/writer are @nocopy, moved out of the await tuple
# (a silent copy would be a compile error), forcing the value-vs-reference test.
import asyncio
from tpy import Int32
from socket import socket, AF_INET, SOCK_STREAM


async def client_role(port: Int32) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"ping\n")
    writer.write(b"more")
    await writer.drain()
    line = await reader.readline()
    print("line: " + line.decode())
    rest = await reader.readexactly(4)
    print("rest: " + rest.decode())
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
    data = await loop.sock_recv(conn, 1024)
    await loop.sock_sendall(conn, data)
    conn.close()

    await client
    listener.close()


def main() -> None:
    asyncio.run(main_coro())


main()
