# asyncio epoll reactor (v2): cancelling a parked fd awaitable. wait_for's
# timeout cancels the inner _SockRecv / _SockSendAll, which unregisters the
# fd from the reactor and raises CancelledError; wait_for surfaces that as
# TimeoutError. Covers the cancel -> _reactor_unregister_fd -> CancelledError
# path for both the read and write awaitables.
import asyncio
from socket import socketpair

# 4 MiB overflows the socket send buffer, so sendall must park at least once
# (the peer never reads) -- giving the timeout a parked write to cancel.
_BIG = b"x" * (4 * 1024 * 1024)


async def main_coro() -> None:
    a, b = socketpair()
    a.setblocking(False)
    b.setblocking(False)
    loop = asyncio.get_running_loop()

    try:
        await asyncio.wait_for(loop.sock_recv(b, 16), 0.01)
        print("recv: not reached")
    except TimeoutError:
        print("recv: timed out")

    try:
        await asyncio.wait_for(loop.sock_sendall(a, _BIG), 0.01)
        print("sendall: not reached")
    except TimeoutError:
        print("sendall: timed out")


def main() -> None:
    asyncio.run(main_coro())


main()
