# asyncio epoll reactor (v2): a non-EAGAIN sendall error surfaces as an
# OSError through the fd awaitable (sendall on a closed fd -> EBADF), not
# silently swallowed or treated as would-block. The write-path mirror of
# reactor_recv_error; runs under the cpy phase since TPy's SocketError (and
# CPython's socket) both raise OSError here.
import asyncio
from socket import socketpair


async def main_coro() -> None:
    a, b = socketpair()
    a.setblocking(False)
    a.close()  # fd is now -1; send -> EBADF
    loop = asyncio.get_running_loop()
    try:
        await loop.sock_sendall(a, b"data")
        print("no error")
    except OSError:
        print("OSError caught")


def main() -> None:
    asyncio.run(main_coro())


main()
