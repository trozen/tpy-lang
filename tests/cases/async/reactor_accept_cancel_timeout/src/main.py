# asyncio epoll reactor (v2): cancelling a parked _SockAccept. A listening
# socket with no incoming connection parks sock_accept on EPOLLIN; wait_for's
# timeout cancels it (cancel -> _reactor_unregister_fd -> CancelledError ->
# TimeoutError). The accept-side analog of the recv/sendall cancel coverage.
import asyncio
from socket import create_server


async def main_coro() -> None:
    srv = create_server(("127.0.0.1", 0))
    srv.setblocking(False)
    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(loop.sock_accept(srv), 0.01)
        print("not reached")
    except TimeoutError:
        print("accept timed out")


def main() -> None:
    asyncio.run(main_coro())


main()
