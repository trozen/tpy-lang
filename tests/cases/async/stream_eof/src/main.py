# asyncio streams EOF edges: readexactly past EOF raises IncompleteReadError
# (carrying the partial bytes); read(-1) drains to EOF; at_eof reports closed.
import asyncio
from tpy import Int32
from socket import socket, AF_INET, SOCK_STREAM


async def client_role(port: Int32) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"abcdef")
    await writer.drain()
    try:
        await reader.readexactly(10)
        print("no error")
    except asyncio.IncompleteReadError as e:
        print("incomplete, partial=" + e.partial.decode())
    tail = await reader.read(-1)
    print("tail_len: " + str(len(tail)))
    print("at_eof: " + str(reader.at_eof()))
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
