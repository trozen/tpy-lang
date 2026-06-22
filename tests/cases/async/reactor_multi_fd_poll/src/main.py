# asyncio epoll reactor (v2): two coroutines park on two different fds, then
# both peers send while both are parked, so a single epoll_wait returns both
# ready fds at once -- the n>1 batch branch of EpollReactor.poll. Results are
# awaited in task order so the printed output is deterministic regardless of
# the order the batch wakes the two wakers.
import asyncio
from socket import socketpair, socket


async def reader(sock: socket) -> bytes:
    loop = asyncio.get_running_loop()
    return await loop.sock_recv(sock, 16)


async def main_coro() -> None:
    a1, b1 = socketpair()
    a2, b2 = socketpair()
    b1.setblocking(False)
    b2.setblocking(False)
    t1 = asyncio.create_task(reader(b1))
    t2 = asyncio.create_task(reader(b2))
    await asyncio.sleep(0.01)  # let both readers park on the reactor
    a1.sendall(b"one")
    a2.sendall(b"two")
    r1 = await t1
    r2 = await t2
    print(r1)
    print(r2)


def main() -> None:
    asyncio.run(main_coro())


main()
