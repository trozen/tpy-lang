# asyncio epoll reactor (v2): timer + I/O interleaved on one executor.
# The main coroutine parks in the reactor on recv while a sleeping task
# holds a timer; wait_for_event must block in epoll_wait bounded by the
# timer deadline, fire the timer, let the sender run, then wake the recv.
import asyncio
from socket import socketpair, socket


async def delayed_send(sock: socket) -> None:
    await asyncio.sleep(0.02)
    loop = asyncio.get_running_loop()
    await loop.sock_sendall(sock, b"late")


async def main_coro() -> None:
    a, b = socketpair()
    a.setblocking(False)
    b.setblocking(False)
    sender = asyncio.create_task(delayed_send(a))
    loop = asyncio.get_running_loop()
    data = await loop.sock_recv(b, 1024)
    print(data)
    await sender


def main() -> None:
    asyncio.run(main_coro())


main()
