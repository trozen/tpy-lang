# asyncio epoll reactor (v2): two concurrent coroutines on one executor
# echo bytes over a non-blocking socketpair. The server task parks in the
# reactor (real epoll would-block) until the client sends; the round-tripped
# bytes confirm data actually moved through the fd-backed awaitables.
import asyncio
from socket import socketpair, socket


async def server(sock: socket) -> None:
    loop = asyncio.get_running_loop()
    while True:
        data = await loop.sock_recv(sock, 1024)
        if len(data) == 0:
            break
        await loop.sock_sendall(sock, data)


async def client(sock: socket) -> None:
    loop = asyncio.get_running_loop()
    await loop.sock_sendall(sock, b"ping")
    reply = await loop.sock_recv(sock, 1024)
    print(reply)
    sock.shutdown(1)  # SHUT_WR -> server's next recv sees EOF


async def main_coro() -> None:
    a, b = socketpair()
    a.setblocking(False)
    b.setblocking(False)
    srv = asyncio.create_task(server(a))
    await client(b)
    await srv
    print("done")


def main() -> None:
    asyncio.run(main_coro())


main()
