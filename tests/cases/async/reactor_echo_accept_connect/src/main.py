# asyncio reactor: concurrent sock_accept + sock_connect echo round-trip on
# one executor. The accepted @nocopy conn moves out of the await tuple (a
# silent copy would be a compile error), forcing the value-vs-reference test.
import asyncio
from tpy import int32
from socket import socket, AF_INET, SOCK_STREAM


async def echo_client(port: int32) -> None:
    loop = asyncio.get_running_loop()
    s = socket(AF_INET, SOCK_STREAM)
    s.setblocking(False)
    await loop.sock_connect(s, ("127.0.0.1", port))
    await loop.sock_sendall(s, b"hello")
    reply = await loop.sock_recv(s, 1024)
    print("client got: " + reply.decode())
    s.close()


async def main_coro() -> None:
    loop = asyncio.get_running_loop()
    listener = socket(AF_INET, SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.setblocking(False)
    host, port = listener.getsockname()

    client = asyncio.create_task(echo_client(port))

    conn, addr = await loop.sock_accept(listener)
    data = await loop.sock_recv(conn, 1024)
    await loop.sock_sendall(conn, data)
    conn.close()

    await client
    print("server done")
    listener.close()


def main() -> None:
    asyncio.run(main_coro())


main()
