# Regression guard: a negative recv size is a ValueError (CPython parity),
# not a zero-length read. Covers both entry points -- the sync
# socket.recv(-1) and the async loop.sock_recv(sock, -1) path, which polls
# through the same socket.recv check before it ever parks on the reactor.
import asyncio
from socket import socketpair


async def main_coro() -> None:
    a, b = socketpair()
    b.setblocking(False)
    try:
        b.recv(-1)
        print("sync: no error")
    except ValueError:
        print("sync: ValueError")

    loop = asyncio.get_running_loop()
    try:
        await loop.sock_recv(b, -1)
        print("async: no error")
    except ValueError:
        print("async: ValueError")


def main() -> None:
    asyncio.run(main_coro())


main()
