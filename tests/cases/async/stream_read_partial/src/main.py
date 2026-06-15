# StreamReader.read(n) returns as soon as data is available (up to n), like
# CPython -- it must NOT wait for the full n. The server sends 3 bytes and
# stays open; a read(10) that blocked for 10 would deadlock (server then
# waits on the client's reply that never comes).
import asyncio
from tpy import Int32
from socket import socket, AF_INET, SOCK_STREAM


async def client_role(port: Int32) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    data = await reader.read(10)        # only 3 available, peer open -> returns 3
    print("got: " + data.decode())
    writer.write(b"x")                  # signal the server it may close
    await writer.drain()
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
    await loop.sock_sendall(conn, b"abc")   # send 3, keep open
    sig = await loop.sock_recv(conn, 16)     # wait for the client's reply
    conn.close()

    await client
    listener.close()


def main() -> None:
    asyncio.run(main_coro())


main()
