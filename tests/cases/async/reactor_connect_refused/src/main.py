# asyncio reactor: sock_connect to a refused port surfaces as OSError (TPy
# raises socket.SocketError, CPython ConnectionRefusedError -- both OSError).
# The bound-but-not-listen()ed socket stays open to reserve the port.
import asyncio
from socket import socket, AF_INET, SOCK_STREAM


async def main_coro() -> None:
    loop = asyncio.get_running_loop()
    blocker = socket(AF_INET, SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    host, port = blocker.getsockname()

    s = socket(AF_INET, SOCK_STREAM)
    s.setblocking(False)
    try:
        await loop.sock_connect(s, ("127.0.0.1", port))
        print("connected unexpectedly")
    except OSError:
        print("connect refused")
    s.close()
    blocker.close()


def main() -> None:
    asyncio.run(main_coro())


main()
