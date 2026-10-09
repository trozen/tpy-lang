# asyncio epoll reactor (v2): two concurrent coroutines on one executor
# echo bytes over a non-blocking socketpair. The server task parks in the
# reactor (real epoll would-block) until the client sends; the round-tripped
# bytes confirm data actually moved through the fd-backed awaitables. A task
# looping on sleep(0) does not keep a reader woken by fd readiness from
# running, which resumes one batch after the readiness is seen (CPython's
# reader callback, then the task's wakeup).
import asyncio
from socket import socketpair, socket
from tpy import int32


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


async def io_reader(sock: socket) -> None:
    loop = asyncio.get_running_loop()
    data = await loop.sock_recv(sock, 16)
    print("io-hop: reader got", data)


async def io_spins(n: int32) -> None:
    for i in range(n):
        print("io-hop: spin", i)
        await asyncio.sleep(0)


async def io_hop() -> None:
    a, b = socketpair()
    a.setblocking(False)
    r = asyncio.create_task(io_reader(a))
    await asyncio.sleep(0)
    s = asyncio.create_task(io_spins(4))
    b.send(b"x")
    for i in range(3):
        print("io-hop: main", i)
        await asyncio.sleep(0)  # tpyc: ok -- the reader resumes one batch after the readiness
    await r
    await s


def main() -> None:
    asyncio.run(main_coro())
    asyncio.run(io_hop())


main()
