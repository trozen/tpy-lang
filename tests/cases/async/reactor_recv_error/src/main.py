# asyncio epoll reactor (v2): a non-EAGAIN recv error surfaces as an
# OSError through the fd awaitable (recv on a closed fd -> EBADF), not
# silently swallowed or treated as would-block. TPy raises socket.SocketError,
# which subclasses OSError, so `except OSError` catches it -- matching
# CPython (whose socket raises OSError), which is why this runs under the
# cpy phase too.
import asyncio
from socket import socketpair


async def main_coro() -> None:
    a, b = socketpair()
    b.setblocking(False)
    b.close()  # fd is now -1; recv -> EBADF
    loop = asyncio.get_running_loop()
    try:
        await loop.sock_recv(b, 16)
        print("no error")
    except OSError:
        print("OSError caught")


def main() -> None:
    asyncio.run(main_coro())


main()
